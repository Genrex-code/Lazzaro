using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

/// <summary>
/// Boundary estricto del receipt publico ROOT-054. Una accion recuperada se
/// valida nuevamente por la fachada ROOT-038 antes de llegar a LazzaroAgent.
/// </summary>
public static class UnityDeliveryReconciliationWireV1
{
    private static readonly HashSet<string> RootFields = new HashSet<string>(
        new[] {
            "version", "id_transaccion", "resolution", "directive",
            "receipt_kind", "receipt_status", "accion"
        },
        StringComparer.Ordinal
    );
    private static readonly HashSet<string> Resolutions = Set(
        "NOT_FOUND", "UNKNOWN", "UNVERIFIABLE", "IN_PROGRESS",
        "COMPLETED_NO_ACTION", "COMPLETED_WITH_ACTION", "FAILED_TERMINAL",
        "REQUIRES_RECONCILIATION"
    );
    private static readonly HashSet<string> Directives = Set(
        "RETRY_SAME_TX", "WAIT", "RESOLVED", "TERMINAL",
        "REQUIRES_RECONCILIATION"
    );
    private static readonly HashSet<string> ReceiptKinds = Set(
        "NONE", "TRANSACTION", "ACTION_LIFECYCLE", "OUTCOME"
    );
    private static readonly HashSet<string> ReceiptStatuses = Set(
        "NONE", "COMMITTED", "APPLIED", "ALREADY_APPLIED", "ACCEPTED",
        "DUPLICATE", "REJECTED", "MISSING"
    );

    public static bool TryParse(
        string rawJson,
        out ReconciliacionTransaccionRespuestaV1 response
    )
    {
        response = null;
        if (
            string.IsNullOrWhiteSpace(rawJson)
            || !ResponseDuplicateWireBoundary.TryValidateResponse(rawJson)
        )
        {
            return false;
        }
        try
        {
            JToken document = JToken.Parse(rawJson);
            if (document.Type != JTokenType.Object) return false;
            JObject root = (JObject)document;
            if (!HasExactFields(root, RootFields)) return false;
            if (!IsExactInteger(root["version"], 1)) return false;
            if (!IsNonEmptyString(root["id_transaccion"])) return false;
            if (!IsOneOf(root["resolution"], Resolutions)) return false;
            if (!IsOneOf(root["directive"], Directives)) return false;
            if (!IsOneOf(root["receipt_kind"], ReceiptKinds)) return false;
            if (!IsOneOf(root["receipt_status"], ReceiptStatuses)) return false;

            string resolution = root["resolution"].Value<string>();
            string directive = root["directive"].Value<string>();
            string receiptKind = root["receipt_kind"].Value<string>();
            string receiptStatus = root["receipt_status"].Value<string>();
            bool hasAction = root["accion"].Type != JTokenType.Null;
            if ((resolution == "COMPLETED_WITH_ACTION") != hasAction) return false;
            if (!IsAllowedCombination(
                resolution,
                directive,
                receiptKind,
                receiptStatus
            ))
                return false;

            AccionSaliente action = null;
            if (hasAction)
            {
                var facadeRoot = new JObject
                {
                    ["wire_version"] = 1,
                    ["status"] = "ok",
                    ["mensaje"] = "receipt",
                    ["id_transaccion"] = root["id_transaccion"].DeepClone(),
                    ["accion"] = root["accion"].DeepClone(),
                    ["decision"] = JValue.CreateNull(),
                    ["timestamp_servidor"] = "1970-01-01T00:00:00Z",
                };
                if (
                    !UnityResponseWireContractV1.TryParse(
                        facadeRoot.ToString(Formatting.None),
                        out UnityResponseWireContractResultV1 parsed
                    )
                    || parsed.typed_response == null
                    || parsed.typed_response.accion == null
                )
                {
                    return false;
                }
                action = parsed.typed_response.accion;
            }

            response = JsonConvert.DeserializeObject<ReconciliacionTransaccionRespuestaV1>(
                rawJson
            );
            if (response == null) return false;
            response.accion = action;
            return true;
        }
        catch (Exception error) when (
            error is JsonException
            || error is InvalidOperationException
            || error is FormatException
            || error is OverflowException
        )
        {
            return false;
        }
    }

    private static HashSet<string> Set(params string[] values)
    {
        return new HashSet<string>(values, StringComparer.Ordinal);
    }

    private static bool IsAllowedCombination(
        string resolution,
        string directive,
        string receiptKind,
        string receiptStatus
    )
    {
        switch (resolution)
        {
            case "NOT_FOUND":
                return directive == "RETRY_SAME_TX"
                    && receiptKind == "NONE"
                    && receiptStatus == "NONE";
            case "UNKNOWN":
            case "UNVERIFIABLE":
                return directive == "REQUIRES_RECONCILIATION"
                    && receiptKind == "NONE"
                    && receiptStatus == "NONE";
            case "IN_PROGRESS":
                return directive == "WAIT"
                    && receiptKind == "NONE"
                    && receiptStatus == "NONE";
            case "COMPLETED_NO_ACTION":
                return directive == "RESOLVED"
                    && IsSuccessfulReceipt(receiptKind, receiptStatus, false);
            case "COMPLETED_WITH_ACTION":
                return directive == "RESOLVED"
                    && IsSuccessfulReceipt(receiptKind, receiptStatus, true);
            case "FAILED_TERMINAL":
                return directive == "TERMINAL"
                    && (
                        (receiptKind == "NONE" && receiptStatus == "NONE")
                        || (receiptKind == "ACTION_LIFECYCLE" && receiptStatus == "REJECTED")
                        || (receiptKind == "OUTCOME" && receiptStatus == "REJECTED")
                    );
            case "REQUIRES_RECONCILIATION":
                return directive == "REQUIRES_RECONCILIATION"
                    && (
                        (receiptKind == "NONE" && receiptStatus == "NONE")
                        || (receiptKind == "ACTION_LIFECYCLE" && receiptStatus == "MISSING")
                        || (receiptKind == "OUTCOME" && receiptStatus == "MISSING")
                    );
            default:
                return false;
        }
    }

    private static bool IsSuccessfulReceipt(
        string receiptKind,
        string receiptStatus,
        bool withAction
    )
    {
        if (receiptKind == "TRANSACTION") return receiptStatus == "COMMITTED";
        if (receiptKind == "ACTION_LIFECYCLE")
        {
            return receiptStatus == "APPLIED" || receiptStatus == "ALREADY_APPLIED";
        }
        if (!withAction && receiptKind == "OUTCOME")
        {
            return receiptStatus == "ACCEPTED" || receiptStatus == "DUPLICATE";
        }
        return false;
    }

    private static bool HasExactFields(JObject value, HashSet<string> expected)
    {
        var actual = new HashSet<string>(StringComparer.Ordinal);
        foreach (JProperty property in value.Properties())
        {
            if (!actual.Add(property.Name)) return false;
        }
        return actual.SetEquals(expected);
    }

    private static bool IsExactInteger(JToken token, long expected)
    {
        return token != null
            && token.Type == JTokenType.Integer
            && token.Value<long>() == expected;
    }

    private static bool IsNonEmptyString(JToken token)
    {
        return token != null
            && token.Type == JTokenType.String
            && !string.IsNullOrWhiteSpace(token.Value<string>());
    }

    private static bool IsOneOf(JToken token, HashSet<string> values)
    {
        return token != null
            && token.Type == JTokenType.String
            && values.Contains(token.Value<string>());
    }
}
