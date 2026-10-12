using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

/// <summary>
/// Boundary raw dedicado al PublicErrorEnvelope de ROOT-010. El mensaje remoto
/// se valida como dato, pero nunca se convierte en autoridad de UX.
/// </summary>
public static class UnityPublicErrorWireContractV1
{
    private static readonly HashSet<string> RootFields = Set("version", "status", "error");
    private static readonly HashSet<string> ErrorFields = Set("code", "message", "correlation_id");
    private static readonly HashSet<string> Codes = Set(
        "RECONCILIATION_REQUIRED", "TURN_ABORTED", "TURN_PROCESSING_FAILED",
        "TRANSACTION_INCOMPLETE", "TRANSACTION_CONFLICT",
        "OPERATIONAL_STORAGE_UNAVAILABLE", "AGENT_UNAVAILABLE",
        "STATE_READ_FAILED", "CONSTELLATION_READ_FAILED", "VALIDATION_ERROR",
        "INTERNAL_ERROR"
    );

    public static bool TryParse(string rawJson, out PublicErrorEnvelope envelope)
    {
        envelope = null;
        JObject root;
        if (!TryReadUniqueObject(rawJson, out root) || !HasExactFields(root, RootFields))
        {
            return false;
        }
        if (!IsExactInteger(root["version"], 1) || !IsString(root["status"]))
        {
            return false;
        }
        JToken errorToken = root["error"];
        if (errorToken == null || errorToken.Type != JTokenType.Object)
        {
            return false;
        }
        JObject detail = (JObject)errorToken;
        if (
            !HasExactFields(detail, ErrorFields)
            || !IsString(detail["code"])
            || !IsString(detail["message"])
            || !IsString(detail["correlation_id"])
        )
        {
            return false;
        }
        string code = detail["code"].Value<string>();
        string status = root["status"].Value<string>();
        if (!Codes.Contains(code) || !StatusCompatible(code, status))
        {
            return false;
        }
        if (!Guid.TryParse(detail["correlation_id"].Value<string>(), out _))
        {
            return false;
        }
        try
        {
            envelope = root.ToObject<PublicErrorEnvelope>();
            return envelope != null && envelope.version == 1 && envelope.error != null;
        }
        catch (Exception error) when (
            error is JsonException || error is InvalidCastException || error is OverflowException
        )
        {
            envelope = null;
            return false;
        }
    }

    private static bool StatusCompatible(string code, string status)
    {
        bool pending = code == "RECONCILIATION_REQUIRED"
            || code == "TRANSACTION_INCOMPLETE";
        return pending ? status == "pendiente" : status == "error";
    }

    private static bool TryReadUniqueObject(string rawJson, out JObject root)
    {
        root = null;
        if (string.IsNullOrWhiteSpace(rawJson)) return false;
        var properties = new Stack<HashSet<string>>();
        var containers = new Stack<bool>();
        try
        {
            using (var text = new StringReader(rawJson))
            using (var reader = new JsonTextReader(text))
            {
                reader.DateParseHandling = DateParseHandling.None;
                while (reader.Read())
                {
                    if (reader.TokenType == JsonToken.StartObject)
                    {
                        containers.Push(true);
                        properties.Push(new HashSet<string>(StringComparer.OrdinalIgnoreCase));
                    }
                    else if (reader.TokenType == JsonToken.EndObject)
                    {
                        if (containers.Count == 0 || !containers.Pop()) return false;
                        properties.Pop();
                    }
                    else if (reader.TokenType == JsonToken.StartArray)
                    {
                        containers.Push(false);
                    }
                    else if (reader.TokenType == JsonToken.EndArray)
                    {
                        if (containers.Count == 0 || containers.Pop()) return false;
                    }
                    else if (reader.TokenType == JsonToken.PropertyName)
                    {
                        if (
                            containers.Count == 0
                            || !containers.Peek()
                            || !properties.Peek().Add((string)reader.Value)
                        ) return false;
                    }
                }
            }
            if (containers.Count != 0 || properties.Count != 0) return false;
            JToken document = JToken.Parse(rawJson);
            if (document.Type != JTokenType.Object) return false;
            root = (JObject)document;
            return true;
        }
        catch (Exception error) when (error is JsonException || error is IOException)
        {
            return false;
        }
    }

    private static bool HasExactFields(JObject value, HashSet<string> expected)
    {
        var actual = new HashSet<string>(StringComparer.Ordinal);
        foreach (JProperty property in value.Properties())
        {
            if (!expected.Contains(property.Name) || !actual.Add(property.Name)) return false;
        }
        return actual.SetEquals(expected);
    }

    private static bool IsExactInteger(JToken token, long expected)
    {
        if (token == null || token.Type != JTokenType.Integer) return false;
        try
        {
            return Convert.ToInt64(((JValue)token).Value, CultureInfo.InvariantCulture) == expected;
        }
        catch (Exception error) when (
            error is OverflowException || error is FormatException || error is InvalidCastException
        )
        {
            return false;
        }
    }

    private static bool IsString(JToken token)
    {
        return token != null && token.Type == JTokenType.String;
    }

    private static HashSet<string> Set(params string[] values)
    {
        return new HashSet<string>(values, StringComparer.Ordinal);
    }
}
