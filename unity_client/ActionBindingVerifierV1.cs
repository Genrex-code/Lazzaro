using System;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

/// <summary>
/// Reconstruye el binding ROOT-029 V1 con los mismos bytes que Python y
/// rechaza cualquier autoridad fisica cuya huella no coincida.
///
/// Este contrato es una huella de integridad, no autenticacion de red.
/// </summary>
public static class ActionBindingVerifierV1
{
    private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

    public static bool TryVerifyResponse(RespuestaAPI response)
    {
        if (response == null)
        {
            return false;
        }
        if (response.accion == null || response.accion.perception_authority == null)
        {
            return true;
        }

        string canonicalBinding;
        string utf8Hex;
        string reconstructedHash;
        if (!TryReconstruct(
            response.accion,
            out canonicalBinding,
            out utf8Hex,
            out reconstructedHash
        ))
        {
            return false;
        }

        return string.Equals(
            reconstructedHash,
            response.accion.perception_authority.action_binding_sha256,
            StringComparison.Ordinal
        );
    }

    public static bool TryReconstruct(
        AccionSaliente action,
        out string canonicalBindingJson,
        out string canonicalBindingUtf8Hex,
        out string reconstructedSha256
    )
    {
        canonicalBindingJson = null;
        canonicalBindingUtf8Hex = null;
        reconstructedSha256 = null;

        if (action == null || action.perception_authority == null)
        {
            return false;
        }

        PerceptionAuthorityReferenceV1 authority = action.perception_authority;
        TargetReferenceV1 target = authority.target;
        if (
            authority.version != 1
            || target == null
            || target.contract_version != 1
            || authority.generation < 0
            || target.generation < 0
            || authority.generation != target.generation
            || !Required(action.id_accion)
            || !Required(action.nombre)
            || !Required(authority.authority_decision_id)
            || !Required(authority.id_accion)
            || !Required(authority.action_name)
            || !Required(authority.authority_policy_id)
            || !Required(authority.freshness_policy_id)
            || !Required(authority.generation_binding_mode)
            || !Required(authority.producer_capture_id)
            || !Required(authority.canonical_parameters_json)
            || !Required(authority.safety_disposition)
            || !Required(authority.safety_reason_code)
            || !Required(target.producer_instance_id)
            || !Required(target.stream_id)
            || !Required(target.producer_epoch_id)
            || !Required(target.producer_capture_id)
            || !Required(target.runtime_object_id)
            || !OptionalIdentifier(target.logical_world_id)
            || !OptionalIdentifier(target.producer_object_id)
            || (target.producer_object_id != null && target.logical_world_id == null)
            || !string.Equals(action.id_accion, authority.id_accion, StringComparison.Ordinal)
            || !string.Equals(action.nombre, authority.action_name, StringComparison.Ordinal)
            || !string.Equals(
                authority.producer_capture_id,
                target.producer_capture_id,
                StringComparison.Ordinal
            )
            || !IsGenerationBindingMode(authority.generation_binding_mode)
            || !IsSafetyDisposition(authority.safety_disposition)
            || !IsSafetyReasonCode(authority.safety_reason_code)
            || !IsLowercaseSha256(authority.action_binding_sha256)
        )
        {
            return false;
        }

        JObject canonicalParameters;
        if (!TryParseSingleObject(authority.canonical_parameters_json, out canonicalParameters))
        {
            return false;
        }
        if (action.parametros == null || !JToken.DeepEquals(canonicalParameters, action.parametros))
        {
            return false;
        }

        StringBuilder output = new StringBuilder(1024);
        output.Append('{');
        AppendPropertyName(output, "action_name");
        if (!TryAppendJsonString(output, authority.action_name))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "authority_policy_id");
        if (!TryAppendJsonString(output, authority.authority_policy_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "binding_version");
        output.Append('1');
        output.Append(',');
        AppendPropertyName(output, "freshness_policy_id");
        if (!TryAppendJsonString(output, authority.freshness_policy_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "generation_binding_mode");
        if (!TryAppendJsonString(output, authority.generation_binding_mode))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "id_accion");
        if (!TryAppendJsonString(output, authority.id_accion))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "interaction_type");
        if (!TryAppendNullableJsonString(output, authority.interaction_type))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "parameters");
        output.Append(authority.canonical_parameters_json);
        output.Append(',');
        AppendPropertyName(output, "safety_disposition");
        if (!TryAppendJsonString(output, authority.safety_disposition))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "safety_reason_code");
        if (!TryAppendJsonString(output, authority.safety_reason_code))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "target");
        if (!TryAppendTarget(output, target))
        {
            return false;
        }
        output.Append('}');

        byte[] utf8;
        try
        {
            canonicalBindingJson = output.ToString();
            utf8 = StrictUtf8.GetBytes(canonicalBindingJson);
        }
        catch (EncoderFallbackException)
        {
            canonicalBindingJson = null;
            return false;
        }

        canonicalBindingUtf8Hex = ToLowerHex(utf8);
        using (SHA256 sha256 = SHA256.Create())
        {
            reconstructedSha256 = ToLowerHex(sha256.ComputeHash(utf8));
        }
        return true;
    }

    private static bool TryAppendTarget(StringBuilder output, TargetReferenceV1 target)
    {
        output.Append('{');
        AppendPropertyName(output, "contract_version");
        output.Append('1');
        output.Append(',');
        AppendPropertyName(output, "generation");
        output.Append(target.generation.ToString(CultureInfo.InvariantCulture));
        output.Append(',');
        AppendPropertyName(output, "logical_world_id");
        if (!TryAppendNullableJsonString(output, target.logical_world_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "producer_capture_id");
        if (!TryAppendJsonString(output, target.producer_capture_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "producer_epoch_id");
        if (!TryAppendJsonString(output, target.producer_epoch_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "producer_instance_id");
        if (!TryAppendJsonString(output, target.producer_instance_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "producer_object_id");
        if (!TryAppendNullableJsonString(output, target.producer_object_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "runtime_object_id");
        if (!TryAppendJsonString(output, target.runtime_object_id))
        {
            return false;
        }
        output.Append(',');
        AppendPropertyName(output, "stream_id");
        if (!TryAppendJsonString(output, target.stream_id))
        {
            return false;
        }
        output.Append('}');
        return true;
    }

    private static bool TryParseSingleObject(string rawJson, out JObject parsed)
    {
        parsed = null;
        try
        {
            // Json.NET accepts JavaScript-style comments by default. Python's
            // json.loads does not, so reject them before loading the object.
            using (var commentText = new System.IO.StringReader(rawJson))
            using (var commentReader = new JsonTextReader(commentText))
            {
                commentReader.DateParseHandling = DateParseHandling.None;
                while (commentReader.Read())
                {
                    if (commentReader.TokenType == JsonToken.Comment)
                    {
                        return false;
                    }
                }
            }
            using (var text = new System.IO.StringReader(rawJson))
            using (var reader = new JsonTextReader(text))
            {
                reader.DateParseHandling = DateParseHandling.None;
                JToken token = JToken.ReadFrom(
                    reader,
                    new JsonLoadSettings
                    {
                        CommentHandling = CommentHandling.Load,
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                    }
                );
                if (token.Type != JTokenType.Object || reader.Read())
                {
                    return false;
                }
                parsed = (JObject)token;
                return true;
            }
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

    private static void AppendPropertyName(StringBuilder output, string name)
    {
        output.Append('"');
        output.Append(name);
        output.Append("\":");
    }

    private static bool TryAppendNullableJsonString(StringBuilder output, string value)
    {
        if (value == null)
        {
            output.Append("null");
            return true;
        }
        return TryAppendJsonString(output, value);
    }

    private static bool TryAppendJsonString(StringBuilder output, string value)
    {
        if (value == null)
        {
            return false;
        }
        output.Append('"');
        for (int index = 0; index < value.Length; index++)
        {
            char character = value[index];
            switch (character)
            {
                case '"':
                    output.Append("\\\"");
                    break;
                case '\\':
                    output.Append("\\\\");
                    break;
                case '\b':
                    output.Append("\\b");
                    break;
                case '\f':
                    output.Append("\\f");
                    break;
                case '\n':
                    output.Append("\\n");
                    break;
                case '\r':
                    output.Append("\\r");
                    break;
                case '\t':
                    output.Append("\\t");
                    break;
                default:
                    if (character <= 0x001f)
                    {
                        output.Append("\\u");
                        output.Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                    }
                    else if (char.IsHighSurrogate(character))
                    {
                        if (
                            index + 1 >= value.Length
                            || !char.IsLowSurrogate(value[index + 1])
                        )
                        {
                            return false;
                        }
                        output.Append(character);
                        output.Append(value[index + 1]);
                        index++;
                    }
                    else if (char.IsLowSurrogate(character))
                    {
                        return false;
                    }
                    else
                    {
                        output.Append(character);
                    }
                    break;
            }
        }
        output.Append('"');
        return true;
    }

    private static bool Required(string value)
    {
        return !string.IsNullOrWhiteSpace(value);
    }

    private static bool OptionalIdentifier(string value)
    {
        return value == null || !string.IsNullOrWhiteSpace(value);
    }

    private static bool IsLowercaseSha256(string value)
    {
        if (value == null || value.Length != 64)
        {
            return false;
        }
        for (int index = 0; index < value.Length; index++)
        {
            char character = value[index];
            if (
                !((character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f'))
            )
            {
                return false;
            }
        }
        return true;
    }

    private static bool IsGenerationBindingMode(string value)
    {
        return value == "REQUIRE_AUTHORIZED_GENERATION"
            || value == "REJECT_IF_KNOWN_SUPERSEDED"
            || value == "ALLOW_ADVANCE_WITH_EXACT_TARGET";
    }

    private static bool IsSafetyDisposition(string value)
    {
        return value == "SAFE"
            || value == "CAUTION"
            || value == "UNSAFE"
            || value == "INDETERMINATE";
    }

    private static bool IsSafetyReasonCode(string value)
    {
        return value == "NO_HAZARD"
            || value == "HAZARD_AVOIDANCE"
            || value == "HAZARD_OBSERVATION"
            || value == "HAZARD_APPROACH"
            || value == "HAZARD_CONTACT"
            || value == "HAZARD_MANIPULATION"
            || value == "HAZARD_FORCE"
            || value == "HAZARD_INTERACTION_UNSPECIFIED"
            || value == "HAZARD_ACTION_UNSUPPORTED"
            || value == "SAFETY_ASSESSMENT_FAILED";
    }

    private static string ToLowerHex(byte[] value)
    {
        StringBuilder output = new StringBuilder(value.Length * 2);
        for (int index = 0; index < value.Length; index++)
        {
            output.Append(value[index].ToString("x2", CultureInfo.InvariantCulture));
        }
        return output.ToString();
    }
}
