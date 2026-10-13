using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;

[Serializable]
public sealed class DeliveryOutboxRecordV1
{
    public int version = 1;
    public EventoEntrante request;
    public string state;
    public int send_attempt_count;
    public int reconciliation_attempt_count;
    public string last_failure_code;
    public long updated_at_utc_ms;
}

/// <summary>
/// Outbox local persistente y transport-only. Conserva requests e intentos;
/// nunca almacena ni decide authority, parametros de accion o exito material.
/// </summary>
public sealed class UnityDeliveryOutboxV1
{
    public const string Queued = "QUEUED";
    public const string Sending = "SENDING";
    public const string Unknown = "UNKNOWN";
    public const string Reconciling = "RECONCILING";
    public const string ExecutionHandoff = "EXECUTION_HANDOFF";
    public const string Paused = "PAUSED";
    public const string Terminal = "TERMINAL";

    private readonly object gate = new object();
    private readonly string path;
    private readonly Dictionary<string, DeliveryOutboxRecordV1> records =
        new Dictionary<string, DeliveryOutboxRecordV1>(StringComparer.Ordinal);

    public UnityDeliveryOutboxV1(string storagePath)
    {
        if (string.IsNullOrWhiteSpace(storagePath))
            throw new ArgumentException("storagePath es requerido", nameof(storagePath));
        path = storagePath;
        Load();
    }

    public bool Enqueue(EventoEntrante request)
    {
        if (!UnityDeliveryIdentityV1.TryPrepare(request)) return false;
        lock (gate)
        {
            if (records.TryGetValue(request.id_transaccion, out DeliveryOutboxRecordV1 existing))
            {
                return SameIdentity(existing.request, request);
            }
            records.Add(
                request.id_transaccion,
                new DeliveryOutboxRecordV1
                {
                    request = CloneRequest(request),
                    state = Queued,
                    updated_at_utc_ms = NowMs(),
                }
            );
            try
            {
                Persist();
                return true;
            }
            catch (Exception error) when (IsStorageFailure(error))
            {
                records.Remove(request.id_transaccion);
                return false;
            }
        }
    }

    public IReadOnlyList<DeliveryOutboxRecordV1> SnapshotPending()
    {
        lock (gate)
        {
            return records.Values
                .OrderBy(value => value.updated_at_utc_ms)
                .Select(CloneRecord)
                .ToList();
        }
    }

    public bool MarkSending(string transactionId)
    {
        return Mutate(transactionId, record => {
            record.state = Sending;
            record.send_attempt_count++;
        });
    }

    public bool MarkUnknown(string transactionId, string failureCode)
    {
        return Update(transactionId, Unknown, failureCode, false);
    }

    public bool MarkReconciling(string transactionId)
    {
        return Mutate(transactionId, record => {
            record.state = Reconciling;
            record.reconciliation_attempt_count++;
        });
    }

    public bool MarkQueuedAfterNotFound(string transactionId)
    {
        return Update(transactionId, Queued, null, false);
    }

    public bool MarkExecutionHandoff(string transactionId)
    {
        return Update(transactionId, ExecutionHandoff, null, false);
    }

    public bool MarkPaused(string transactionId, string reason)
    {
        return Update(transactionId, Paused, reason, false);
    }

    public bool MarkTerminal(string transactionId, string reason)
    {
        return Update(transactionId, Terminal, reason, false);
    }

    public int SendAttemptCount(string transactionId)
    {
        lock (gate)
        {
            return TryGet(transactionId, out DeliveryOutboxRecordV1 record)
                ? record.send_attempt_count
                : 0;
        }
    }

    public int ReconciliationAttemptCount(string transactionId)
    {
        lock (gate)
        {
            return TryGet(transactionId, out DeliveryOutboxRecordV1 record)
                ? record.reconciliation_attempt_count
                : 0;
        }
    }

    public void Complete(string transactionId)
    {
        lock (gate)
        {
            if (!TryGet(transactionId, out DeliveryOutboxRecordV1 record)) return;
            records.Remove(transactionId);
            try
            {
                Persist();
            }
            catch (Exception error) when (IsStorageFailure(error))
            {
                records[transactionId] = record;
            }
        }
    }

    public void CompleteExecutionHandoffForAction(string actionId)
    {
        if (string.IsNullOrWhiteSpace(actionId)) return;
        lock (gate)
        {
            string match = records.Values
                .Where(value => value.state == ExecutionHandoff)
                .Select(value => value.request)
                .Where(request => request != null && request.detalles != null)
                .Where(request =>
                    request.detalles.TryGetValue("id_accion", out object raw)
                    && string.Equals(raw as string, actionId, StringComparison.Ordinal)
                )
                .Select(request => request.id_transaccion)
                .FirstOrDefault();
            if (match == null || !TryGet(match, out DeliveryOutboxRecordV1 record)) return;
            records.Remove(match);
            try
            {
                Persist();
            }
            catch (Exception error) when (IsStorageFailure(error))
            {
                records[match] = record;
            }
        }
    }

    private bool Update(
        string transactionId,
        string state,
        string failureCode,
        bool incrementReconciliation
    )
    {
        return Mutate(transactionId, record => {
            record.state = state;
            record.last_failure_code = failureCode;
            if (incrementReconciliation) record.reconciliation_attempt_count++;
        });
    }

    private bool Mutate(string transactionId, Action<DeliveryOutboxRecordV1> mutation)
    {
        lock (gate)
        {
            if (!TryGet(transactionId, out DeliveryOutboxRecordV1 record)) return false;
            DeliveryOutboxRecordV1 previous = CloneRecord(record);
            mutation(record);
            Touch(record);
            try
            {
                Persist();
                return true;
            }
            catch (Exception error) when (IsStorageFailure(error))
            {
                records[transactionId] = previous;
                return false;
            }
        }
    }

    private bool TryGet(string transactionId, out DeliveryOutboxRecordV1 record)
    {
        record = null;
        return !string.IsNullOrWhiteSpace(transactionId)
            && records.TryGetValue(transactionId, out record);
    }

    private void Load()
    {
        if (!File.Exists(path)) return;
        string raw = File.ReadAllText(path, new UTF8Encoding(false, true));
        List<DeliveryOutboxRecordV1> loaded =
            JsonConvert.DeserializeObject<List<DeliveryOutboxRecordV1>>(raw);
        if (loaded == null) throw new InvalidDataException("Outbox V1 invalida");
        foreach (DeliveryOutboxRecordV1 record in loaded)
        {
            if (
                record == null
                || record.version != 1
                || record.request == null
                || !UnityDeliveryIdentityV1.TryPrepare(record.request)
                || string.IsNullOrWhiteSpace(record.state)
                || records.ContainsKey(record.request.id_transaccion)
            )
            {
                throw new InvalidDataException("Outbox V1 contiene un registro invalido");
            }
            records.Add(record.request.id_transaccion, record);
        }
    }

    private void Persist()
    {
        string directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
        string temporary = path + ".tmp";
        string backup = path + ".bak";
        string json = JsonConvert.SerializeObject(
            records.Values.OrderBy(value => value.updated_at_utc_ms).ToList(),
            Formatting.None
        );
        File.WriteAllText(temporary, json, new UTF8Encoding(false, true));
        if (File.Exists(path))
        {
            File.Replace(temporary, path, backup, true);
            if (File.Exists(backup)) File.Delete(backup);
        }
        else
        {
            File.Move(temporary, path);
        }
    }

    private static bool SameIdentity(EventoEntrante left, EventoEntrante right)
    {
        return left != null
            && right != null
            && left.request_identity_version == right.request_identity_version
            && string.Equals(
                left.request_fingerprint_sha256,
                right.request_fingerprint_sha256,
                StringComparison.Ordinal
            );
    }

    private static EventoEntrante CloneRequest(EventoEntrante request)
    {
        return JsonConvert.DeserializeObject<EventoEntrante>(
            JsonConvert.SerializeObject(request, Formatting.None)
        );
    }

    private static DeliveryOutboxRecordV1 CloneRecord(DeliveryOutboxRecordV1 record)
    {
        return JsonConvert.DeserializeObject<DeliveryOutboxRecordV1>(
            JsonConvert.SerializeObject(record, Formatting.None)
        );
    }

    private static void Touch(DeliveryOutboxRecordV1 record)
    {
        record.updated_at_utc_ms = NowMs();
    }

    private static long NowMs()
    {
        return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    }

    private static bool IsStorageFailure(Exception error)
    {
        return error is IOException
            || error is UnauthorizedAccessException
            || error is JsonException;
    }
}
