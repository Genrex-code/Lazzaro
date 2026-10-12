using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

public sealed class UnityResponseWireContractResultV1
{
    public bool success;
    public RespuestaAPI typed_response;
    public int wire_version;
    public bool legacy_mode;
    public string failure_reason;
}

/// <summary>
/// Clasifica V0/V1 y coordina los boundaries ROOT-038 sin apropiarse de sus
/// reglas internas. Un documento que declara V1 nunca vuelve a V0.
/// </summary>
public static class UnityResponseWireContractV1
{
    private const string InvalidResponse = "INVALID_RESPONSE";

    private static readonly HashSet<string> RootFields = Set(
        "wire_version", "status", "mensaje", "id_transaccion", "accion",
        "decision", "timestamp_servidor"
    );
    private static readonly HashSet<string> ActionFields = Set(
        "id_accion", "nombre", "parametros", "requiere_confirmacion",
        "estado_ejecucion", "id_transaccion_origen", "parametros_descripcion",
        "perception_authority"
    );
    private static readonly HashSet<string> DecisionFields = Set(
        "version", "disposition", "reason_code"
    );
    private static readonly HashSet<string> ParameterDescriptionFields = Set(
        "nombre", "tipo", "descripcion", "obligatorio"
    );
    private static readonly HashSet<string> AuthorityFields = Set(
        "version", "authority_decision_id", "id_accion", "authority_policy_id",
        "freshness_policy_id", "generation_binding_mode", "producer_capture_id",
        "generation", "target", "action_name", "canonical_parameters_json",
        "interaction_type", "safety_disposition", "safety_reason_code",
        "action_binding_sha256"
    );
    private static readonly HashSet<string> TargetFields = Set(
        "contract_version", "producer_instance_id", "stream_id",
        "producer_epoch_id", "logical_world_id", "producer_capture_id",
        "generation", "producer_object_id", "runtime_object_id"
    );
    private static readonly HashSet<string> LifecycleStates = Set(
        "legacy_unknown", "created", "awaiting_authorization", "authorized",
        "dispatched", "executing", "reported_success", "reported_failure",
        "rejected", "skipped"
    );
    private static readonly HashSet<string> DecisionDispositions = Set(
        "action", "no_action", "clarification", "blocked", "invalid", "error"
    );
    private static readonly HashSet<string> DecisionReasons = Set(
        "primary_valid", "passive_no_action", "parse_degraded", "parse_error",
        "safe_salvage", "retry_repaired", "deterministic_fallback", "motor_ready",
        "motor_clarification", "missing_target", "ambiguous_target",
        "target_not_found", "action_not_allowed", "unsafe_block",
        "motor_resolution_failed", "unknown_action", "invalid_parameters",
        "oracle_result_unavailable", "channel_blocked", "pipeline_error",
        "legacy_untyped"
    );
    private static readonly HashSet<string> ParameterKinds = Set(
        "float", "int", "str", "bool", "coordenadas", "array", "objeto"
    );
    private static readonly HashSet<string> PerceptionTargetActions = Set(
        "navegar_a_objeto", "alejarse_de_objeto", "interactuar_objeto_3d",
        "agarrar_objeto", "aplicar_fuerza"
    );

    public static bool TryParse(
        string rawJson,
        out UnityResponseWireContractResultV1 result
    )
    {
        result = Failure();
        if (!ResponseDuplicateWireBoundary.TryValidateResponse(rawJson))
        {
            return false;
        }

        JObject root;
        if (!TryReadRoot(rawJson, out root))
        {
            return false;
        }

        JProperty versionProperty = null;
        foreach (JProperty property in root.Properties())
        {
            if (string.Equals(property.Name, "wire_version", StringComparison.OrdinalIgnoreCase))
            {
                versionProperty = property;
                break;
            }
        }

        if (versionProperty == null)
        {
            return TryParseV0(rawJson, out result);
        }
        if (
            versionProperty.Name != "wire_version"
            || versionProperty.Value.Type != JTokenType.Integer
            || !TryStrictInt64(versionProperty.Value, out long wireVersion)
            || wireVersion != 1
        )
        {
            return false;
        }

        return TryParseV1(rawJson, root, out result);
    }

    private static bool TryParseV0(
        string rawJson,
        out UnityResponseWireContractResultV1 result
    )
    {
        result = Failure();
        try
        {
            if (
                !ResponseEnvelopeWireBoundaryV1.TryValidateResponse(rawJson)
                || !ActionEnvelopeWireBoundary.TryValidateResponse(rawJson)
                || !PhysicalActionWireBoundaryV1.TryValidateResponse(rawJson)
                || !AuthorityWireBoundary.TryValidateResponse(rawJson)
            )
            {
                return false;
            }
            RespuestaAPI response = JsonConvert.DeserializeObject<RespuestaAPI>(rawJson);
            if (response == null || !ActionBindingVerifierV1.TryVerifyResponse(response))
            {
                return false;
            }
            result = Success(response, 0, true);
            return true;
        }
        catch (Exception error) when (
            error is JsonException
            || error is InvalidCastException
            || error is OverflowException
            || error is FormatException
        )
        {
            return false;
        }
    }

    private static bool TryParseV1(
        string rawJson,
        JObject root,
        out UnityResponseWireContractResultV1 result
    )
    {
        result = Failure();
        try
        {
            if (
                !HasNoDuplicateProperties(rawJson)
                || !ValidateV1Shape(root)
                || !HasNoNegativeZeroPhysicalParameters(rawJson, root)
                || !ResponseEnvelopeWireBoundaryV1.TryValidateResponse(rawJson)
                || !ActionEnvelopeWireBoundary.TryValidateResponse(rawJson)
                || !PhysicalActionWireBoundaryV1.TryValidateResponse(rawJson)
                || !AuthorityWireBoundary.TryValidateResponse(rawJson)
                || !ValidateV1PhysicalDomain(root)
            )
            {
                return false;
            }

            RespuestaAPI response = JsonConvert.DeserializeObject<RespuestaAPI>(rawJson);
            if (
                response == null
                || response.wire_version != 1
                || !ActionBindingVerifierV1.TryVerifyResponse(response)
            )
            {
                return false;
            }
            result = Success(response, 1, false);
            return true;
        }
        catch (Exception error) when (
            error is JsonException
            || error is InvalidCastException
            || error is OverflowException
            || error is FormatException
        )
        {
            return false;
        }
    }

    private static bool ValidateV1Shape(JObject root)
    {
        if (!HasExactFields(root, RootFields))
        {
            return false;
        }
        if (!IsInteger(root["wire_version"], 1))
        {
            return false;
        }
        if (!IsOneOfString(root["status"], Set("ok", "error", "pendiente")))
        {
            return false;
        }
        if (!IsString(root["mensaje"]) || !IsStringOrNull(root["id_transaccion"]))
        {
            return false;
        }
        if (!IsString(root["timestamp_servidor"]))
        {
            return false;
        }
        if (!ValidateDecision(root["decision"]))
        {
            return false;
        }
        return ValidateAction(root["accion"]);
    }

    private static bool ValidateDecision(JToken token)
    {
        if (token == null || token.Type == JTokenType.Null)
        {
            return true;
        }
        if (token.Type != JTokenType.Object)
        {
            return false;
        }
        JObject decision = (JObject)token;
        return HasExactFields(decision, DecisionFields)
            && IsInteger(decision["version"], 1)
            && IsOneOfString(decision["disposition"], DecisionDispositions)
            && IsOneOfString(decision["reason_code"], DecisionReasons);
    }

    private static bool ValidateAction(JToken token)
    {
        if (token == null || token.Type == JTokenType.Null)
        {
            return true;
        }
        if (token.Type != JTokenType.Object)
        {
            return false;
        }
        JObject action = (JObject)token;
        if (
            !HasExactFields(action, ActionFields)
            || !IsString(action["id_accion"])
            || !IsString(action["nombre"])
            || action["parametros"] == null
            || action["parametros"].Type != JTokenType.Object
            || action["requiere_confirmacion"] == null
            || action["requiere_confirmacion"].Type != JTokenType.Boolean
            || !IsOneOfString(action["estado_ejecucion"], LifecycleStates)
            || !IsStringOrNull(action["id_transaccion_origen"])
            || !ValidateParameterDescriptions(action["parametros_descripcion"])
            || !ValidateAuthorityShape(action["perception_authority"])
        )
        {
            return false;
        }

        string actionName = action["nombre"].Value<string>();
        return !PerceptionTargetActions.Contains(actionName)
            || (
                action["perception_authority"] != null
                && action["perception_authority"].Type == JTokenType.Object
            );
    }

    private static bool ValidateParameterDescriptions(JToken token)
    {
        if (token == null || token.Type != JTokenType.Array)
        {
            return false;
        }
        foreach (JToken item in (JArray)token)
        {
            if (item.Type != JTokenType.Object)
            {
                return false;
            }
            JObject value = (JObject)item;
            if (
                !HasExactFields(value, ParameterDescriptionFields)
                || !IsString(value["nombre"])
                || !IsOneOfString(value["tipo"], ParameterKinds)
                || !IsString(value["descripcion"])
                || value["obligatorio"] == null
                || value["obligatorio"].Type != JTokenType.Boolean
            )
            {
                return false;
            }
        }
        return true;
    }

    private static bool ValidateAuthorityShape(JToken token)
    {
        if (token == null || token.Type == JTokenType.Null)
        {
            return true;
        }
        if (token.Type != JTokenType.Object)
        {
            return false;
        }
        JObject authority = (JObject)token;
        if (!HasExactFields(authority, AuthorityFields))
        {
            return false;
        }
        JToken targetToken = authority["target"];
        return targetToken != null
            && targetToken.Type == JTokenType.Object
            && HasExactFields((JObject)targetToken, TargetFields);
    }

    private static bool ValidateV1PhysicalDomain(JObject root)
    {
        JToken actionToken = root["accion"];
        if (actionToken == null || actionToken.Type == JTokenType.Null)
        {
            return true;
        }
        JObject action = (JObject)actionToken;
        string name = action["nombre"].Value<string>();
        JObject parameters = (JObject)action["parametros"];
        switch (name)
        {
            case "navegar_a_objeto":
                return OptionalCanonicalFloat32(parameters, "distancia");
            case "navegar_a_posicion":
                return CanonicalFloat32(parameters, "x")
                    && CanonicalFloat32(parameters, "y")
                    && CanonicalFloat32(parameters, "z");
            case "alejarse_de_objeto":
                return OptionalCanonicalFloat32(parameters, "distancia");
            case "girar_avatar":
                return CanonicalFloat32(parameters, "grados");
            case "soltar_objeto":
                return OptionalCanonicalFloat32(parameters, "fuerza_lanzamiento");
            case "aplicar_fuerza":
                if (
                    !CanonicalFloat32(parameters, "vector_x")
                    || !CanonicalFloat32(parameters, "vector_y")
                    || !CanonicalFloat32(parameters, "vector_z")
                    || !CanonicalFloat32(parameters, "fuerza")
                )
                {
                    return false;
                }
                float x = parameters["vector_x"].Value<float>();
                float y = parameters["vector_y"].Value<float>();
                float z = parameters["vector_z"].Value<float>();
                double norm = Math.Sqrt((double)x * x + (double)y * y + (double)z * z);
                return Math.Abs(norm - 1.0d) <= 0.000002d;
            default:
                return true;
        }
    }

    private static bool OptionalCanonicalFloat32(JObject value, string field)
    {
        JToken token = value[field];
        return token == null || token.Type == JTokenType.Null || CanonicalFloat32(value, field);
    }

    private static bool CanonicalFloat32(JObject value, string field)
    {
        JToken token = value[field];
        if (
            token == null
            || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
        )
        {
            return false;
        }
        double raw;
        try
        {
            raw = token.Value<double>();
        }
        catch (Exception error) when (
            error is OverflowException || error is FormatException || error is InvalidCastException
        )
        {
            return false;
        }
        if (double.IsNaN(raw) || double.IsInfinity(raw))
        {
            return false;
        }
        if (raw == 0.0d && BitConverter.DoubleToInt64Bits(raw) < 0)
        {
            return false;
        }
        float narrowed = (float)raw;
        if (float.IsNaN(narrowed) || float.IsInfinity(narrowed))
        {
            return false;
        }
        if (raw != 0.0d && narrowed == 0.0f)
        {
            return false;
        }
        return (double)narrowed == raw;
    }

    private static bool HasNoDuplicateProperties(string rawJson)
    {
        var objectProperties = new Stack<HashSet<string>>();
        var containers = new Stack<bool>();
        try
        {
            using (var text = new StringReader(rawJson))
            using (var reader = new JsonTextReader(text))
            {
                while (reader.Read())
                {
                    if (reader.TokenType == JsonToken.StartObject)
                    {
                        containers.Push(true);
                        objectProperties.Push(new HashSet<string>(StringComparer.OrdinalIgnoreCase));
                    }
                    else if (reader.TokenType == JsonToken.EndObject)
                    {
                        if (containers.Count == 0 || !containers.Pop()) return false;
                        objectProperties.Pop();
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
                            || !objectProperties.Peek().Add((string)reader.Value)
                        )
                        {
                            return false;
                        }
                    }
                }
            }
        }
        catch (Exception error) when (error is JsonException || error is IOException)
        {
            return false;
        }
        return containers.Count == 0 && objectProperties.Count == 0;
    }

    private static bool HasNoNegativeZeroPhysicalParameters(
        string rawJson,
        JObject root
    )
    {
        JToken actionToken = root["accion"];
        if (actionToken == null || actionToken.Type == JTokenType.Null)
        {
            return true;
        }

        JObject action = (JObject)actionToken;
        HashSet<string> physicalFields = PhysicalNumericFields(
            action["nombre"].Value<string>()
        );
        if (physicalFields == null)
        {
            return true;
        }

        for (int index = 0; index < rawJson.Length; index++)
        {
            if (rawJson[index] != '"')
            {
                continue;
            }

            int stringEnd = FindJsonStringEnd(rawJson, index);
            if (stringEnd < 0)
            {
                return false;
            }

            string propertyName;
            try
            {
                propertyName = JsonConvert.DeserializeObject<string>(
                    rawJson.Substring(index, stringEnd - index + 1)
                );
            }
            catch (JsonException)
            {
                return false;
            }

            int cursor = SkipWhitespace(rawJson, stringEnd + 1);
            if (
                physicalFields.Contains(propertyName)
                && cursor < rawJson.Length
                && rawJson[cursor] == ':'
            )
            {
                cursor = SkipWhitespace(rawJson, cursor + 1);
                if (IsNegativeZeroNumberAt(rawJson, cursor))
                {
                    return false;
                }
            }
            index = stringEnd;
        }
        return true;
    }

    private static HashSet<string> PhysicalNumericFields(string actionName)
    {
        switch (actionName)
        {
            case "navegar_a_objeto":
            case "alejarse_de_objeto":
                return Set("distancia");
            case "navegar_a_posicion":
                return Set("x", "y", "z");
            case "girar_avatar":
                return Set("grados");
            case "soltar_objeto":
                return Set("fuerza_lanzamiento");
            case "aplicar_fuerza":
                return Set("vector_x", "vector_y", "vector_z", "fuerza");
            default:
                return null;
        }
    }

    private static int FindJsonStringEnd(string rawJson, int start)
    {
        bool escaped = false;
        for (int index = start + 1; index < rawJson.Length; index++)
        {
            char current = rawJson[index];
            if (escaped)
            {
                escaped = false;
            }
            else if (current == '\\')
            {
                escaped = true;
            }
            else if (current == '"')
            {
                return index;
            }
        }
        return -1;
    }

    private static int SkipWhitespace(string rawJson, int start)
    {
        int cursor = start;
        while (cursor < rawJson.Length && char.IsWhiteSpace(rawJson[cursor]))
        {
            cursor++;
        }
        return cursor;
    }

    private static bool IsNegativeZeroNumberAt(string rawJson, int start)
    {
        if (
            start < 0
            || start + 1 >= rawJson.Length
            || rawJson[start] != '-'
            || rawJson[start + 1] != '0'
        )
        {
            return false;
        }
        int end = start + 2;
        while (end < rawJson.Length && IsNumberCharacter(rawJson[end]))
        {
            end++;
        }
        string token = rawJson.Substring(start, end - start);
        return (
                double.TryParse(
                    token,
                    NumberStyles.Float,
                    CultureInfo.InvariantCulture,
                    out double value
                )
                && value == 0.0d
        );
    }

    private static bool IsNumberCharacter(char value)
    {
        return (value >= '0' && value <= '9')
            || value == '.'
            || value == 'e'
            || value == 'E'
            || value == '+'
            || value == '-';
    }

    private static bool TryReadRoot(string rawJson, out JObject root)
    {
        root = null;
        if (string.IsNullOrWhiteSpace(rawJson)) return false;
        try
        {
            using (var text = new StringReader(rawJson))
            using (var reader = new JsonTextReader(text))
            {
                reader.DateParseHandling = DateParseHandling.None;
                reader.FloatParseHandling = FloatParseHandling.Double;
                JToken document = JToken.ReadFrom(reader);
                if (reader.Read() || document.Type != JTokenType.Object) return false;
                root = (JObject)document;
                return true;
            }
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
            if (!expected.Contains(property.Name) || !actual.Add(property.Name))
            {
                return false;
            }
        }
        return actual.SetEquals(expected);
    }

    private static bool IsInteger(JToken token, long expected)
    {
        return token != null
            && token.Type == JTokenType.Integer
            && TryStrictInt64(token, out long value)
            && value == expected;
    }

    private static bool TryStrictInt64(JToken token, out long value)
    {
        value = 0;
        try
        {
            value = Convert.ToInt64(((JValue)token).Value, CultureInfo.InvariantCulture);
            return true;
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

    private static bool IsStringOrNull(JToken token)
    {
        return token != null && (token.Type == JTokenType.String || token.Type == JTokenType.Null);
    }

    private static bool IsOneOfString(JToken token, HashSet<string> values)
    {
        return IsString(token) && values.Contains(token.Value<string>());
    }

    private static HashSet<string> Set(params string[] values)
    {
        return new HashSet<string>(values, StringComparer.Ordinal);
    }

    private static UnityResponseWireContractResultV1 Success(
        RespuestaAPI response,
        int version,
        bool legacy
    )
    {
        return new UnityResponseWireContractResultV1
        {
            success = true,
            typed_response = response,
            wire_version = version,
            legacy_mode = legacy,
            failure_reason = null,
        };
    }

    private static UnityResponseWireContractResultV1 Failure()
    {
        return new UnityResponseWireContractResultV1
        {
            success = false,
            typed_response = null,
            wire_version = -1,
            legacy_mode = false,
            failure_reason = InvalidResponse,
        };
    }
}
