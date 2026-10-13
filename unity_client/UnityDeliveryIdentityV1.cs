using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

/// <summary>
/// Identidad semantica versionada de un request ROOT-054. El timestamp y el
/// estado local de delivery no participan en el fingerprint.
/// </summary>
public static class UnityDeliveryIdentityV1
{
    public const int Version = 1;

    public static bool TryPrepare(EventoEntrante request)
    {
        if (!TryCompute(request, out string fingerprint))
        {
            return false;
        }

        if (request.request_identity_version.HasValue)
        {
            return request.request_identity_version.Value == Version
                && string.Equals(
                    request.request_fingerprint_sha256,
                    fingerprint,
                    StringComparison.Ordinal
                );
        }

        if (!string.IsNullOrEmpty(request.request_fingerprint_sha256))
        {
            return false;
        }
        request.request_identity_version = Version;
        request.request_fingerprint_sha256 = fingerprint;
        return true;
    }

    public static bool TryCompute(EventoEntrante request, out string fingerprint)
    {
        fingerprint = null;
        if (
            request == null
            || string.IsNullOrWhiteSpace(request.id_transaccion)
            || string.IsNullOrWhiteSpace(request.origen)
            || string.IsNullOrWhiteSpace(request.tipo_evento)
            || request.objeto_involucrado == null
        )
        {
            return false;
        }

        try
        {
            var payload = new JObject
            {
                ["detalles"] = JToken.FromObject(
                    request.detalles ?? new Dictionary<string, object>()
                ),
                ["id_transaccion"] = request.id_transaccion,
                ["objeto_involucrado"] = request.objeto_involucrado,
                ["origen"] = request.origen,
                ["tipo_evento"] = request.tipo_evento,
            };
            JToken canonical = Canonicalize(payload);
            if (!HasOnlyFiniteNumbers(canonical))
            {
                return false;
            }
            string json = canonical.ToString(Formatting.None);
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(json));
                var builder = new StringBuilder(digest.Length * 2);
                foreach (byte value in digest)
                {
                    builder.Append(value.ToString("x2", CultureInfo.InvariantCulture));
                }
                fingerprint = builder.ToString();
            }
            return true;
        }
        catch (Exception error) when (
            error is JsonException
            || error is InvalidOperationException
            || error is ArgumentException
        )
        {
            return false;
        }
    }

    private static JToken Canonicalize(JToken token)
    {
        if (token.Type == JTokenType.Object)
        {
            var result = new JObject();
            var properties = new List<JProperty>(((JObject)token).Properties());
            properties.Sort((left, right) =>
                string.CompareOrdinal(left.Name, right.Name)
            );
            foreach (JProperty property in properties)
            {
                result.Add(property.Name, Canonicalize(property.Value));
            }
            return result;
        }
        if (token.Type == JTokenType.Array)
        {
            var result = new JArray();
            foreach (JToken item in (JArray)token)
            {
                result.Add(Canonicalize(item));
            }
            return result;
        }
        return token.DeepClone();
    }

    private static bool HasOnlyFiniteNumbers(JToken token)
    {
        if (token.Type == JTokenType.Float)
        {
            double value = token.Value<double>();
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }
        if (token is JContainer container)
        {
            foreach (JToken child in container.Children())
            {
                if (!HasOnlyFiniteNumbers(child)) return false;
            }
        }
        return true;
    }
}
