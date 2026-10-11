using System;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

/// <summary>
/// Valida la forma y los tipos escalares del objeto raiz RespuestaAPI antes
/// de que Json.NET pueda convertirlos al DTO tipado.
///
/// B14 no valida el interior de accion/decision, no decide defaults y no
/// reemplaza la deteccion de duplicados B9. Los nombres se comparan sin
/// distinguir mayusculas para conservar temporalmente la compatibilidad B8.
/// </summary>
public static class ResponseEnvelopeWireBoundaryV1
{
    public static bool TryValidateResponse(string rawJson)
    {
        if (string.IsNullOrWhiteSpace(rawJson))
        {
            return false;
        }

        try
        {
            using (var text = new StringReader(rawJson))
            using (var reader = new JsonTextReader(text))
            {
                reader.DateParseHandling = DateParseHandling.None;
                JToken document = JToken.ReadFrom(reader);
                if (reader.Read() || document.Type != JTokenType.Object)
                {
                    return false;
                }

                foreach (JProperty property in ((JObject)document).Properties())
                {
                    if (!ValidateRootProperty(property.Name, property.Value))
                    {
                        return false;
                    }
                }
            }
        }
        catch (Exception error) when (
            error is JsonException
            || error is IOException
            || error is InvalidCastException
        )
        {
            return false;
        }

        return true;
    }

    private static bool ValidateRootProperty(string name, JToken value)
    {
        if (EqualsWireName(name, "status"))
        {
            if (value == null || value.Type != JTokenType.String)
            {
                return false;
            }
            string status = value.Value<string>();
            return status == "ok" || status == "error" || status == "pendiente";
        }
        if (EqualsWireName(name, "mensaje"))
        {
            return value != null && value.Type == JTokenType.String;
        }
        if (EqualsWireName(name, "id_transaccion"))
        {
            return value != null
                && (value.Type == JTokenType.String || value.Type == JTokenType.Null);
        }
        if (EqualsWireName(name, "accion") || EqualsWireName(name, "decision"))
        {
            return value != null
                && (value.Type == JTokenType.Object || value.Type == JTokenType.Null);
        }
        if (EqualsWireName(name, "timestamp_servidor"))
        {
            return value != null && value.Type == JTokenType.String;
        }

        // RespuestaAPI no usa extra="forbid" actualmente. La politica general
        // de extras pertenece al contrato versionado posterior de ROOT-038.
        return true;
    }

    private static bool EqualsWireName(string actual, string expected)
    {
        return string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase);
    }
}
