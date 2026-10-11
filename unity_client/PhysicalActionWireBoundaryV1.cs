using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

/// <summary>
/// Valida el objeto parametros de las acciones fisicas contra el contrato
/// canonico Python antes de que Json.NET pueda convertir tipos escalares.
///
/// No valida autoridad, identidad, binding, cancelacion ni acciones no fisicas.
/// B9 y B7 se ejecutan antes para resolver duplicados y forma del envelope.
/// </summary>
public static class PhysicalActionWireBoundaryV1
{
    private static readonly HashSet<string> InteractionTypes =
        new HashSet<string>(StringComparer.Ordinal)
        {
            "mirar",
            "tocar",
            "inspeccionar",
            "golpear",
            "usar",
            "activar",
        };

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
                reader.FloatParseHandling = FloatParseHandling.Double;
                JToken document = JToken.ReadFrom(reader);
                if (reader.Read())
                {
                    return false;
                }
                if (document.Type != JTokenType.Object)
                {
                    // La forma general pertenece al envelope/DTO posterior.
                    return true;
                }

                JObject root = (JObject)document;
                JToken actionToken = root.GetValue(
                    "accion",
                    StringComparison.OrdinalIgnoreCase
                );
                if (actionToken == null || actionToken.Type == JTokenType.Null)
                {
                    return true;
                }
                if (actionToken.Type != JTokenType.Object)
                {
                    return false;
                }

                JObject action = (JObject)actionToken;
                JToken nameToken = action.GetValue(
                    "nombre",
                    StringComparison.OrdinalIgnoreCase
                );
                if (nameToken == null || nameToken.Type != JTokenType.String)
                {
                    return true;
                }

                string actionName = nameToken.Value<string>();
                if (!IsPhysicalAction(actionName))
                {
                    return true;
                }

                JToken parametersToken = action.GetValue(
                    "parametros",
                    StringComparison.OrdinalIgnoreCase
                );
                if (parametersToken == null || parametersToken.Type != JTokenType.Object)
                {
                    return false;
                }

                return ValidatePhysicalParameters(actionName, (JObject)parametersToken);
            }
        }
        catch (Exception error) when (
            error is JsonException
            || error is IOException
            || error is OverflowException
            || error is FormatException
            || error is InvalidCastException
        )
        {
            return false;
        }
    }

    private static bool IsPhysicalAction(string actionName)
    {
        switch (actionName)
        {
            case "navegar_a_objeto":
            case "navegar_a_posicion":
            case "alejarse_de_objeto":
            case "girar_avatar":
            case "interactuar_objeto_3d":
            case "agarrar_objeto":
            case "soltar_objeto":
            case "aplicar_fuerza":
                return true;
            default:
                return false;
        }
    }

    private static bool ValidatePhysicalParameters(
        string actionName,
        JObject parameters
    )
    {
        switch (actionName)
        {
            case "navegar_a_objeto":
                return HasOnlyFields(
                        parameters,
                        new[] { "nombre_objeto" },
                        new[] { "distancia" }
                    )
                    && IsString(parameters, "nombre_objeto")
                    && (
                        !HasField(parameters, "distancia")
                        || IsNull(parameters, "distancia")
                        || IsNumberInRange(parameters, "distancia", 0.1d, 10.0d)
                    );

            case "navegar_a_posicion":
                return HasOnlyFields(
                        parameters,
                        new[] { "x", "y", "z" },
                        Array.Empty<string>()
                    )
                    && IsFiniteNumber(parameters, "x")
                    && IsFiniteNumber(parameters, "y")
                    && IsFiniteNumber(parameters, "z");

            case "alejarse_de_objeto":
                return HasOnlyFields(
                        parameters,
                        new[] { "nombre_objeto" },
                        new[] { "distancia" }
                    )
                    && IsString(parameters, "nombre_objeto")
                    && (
                        !HasField(parameters, "distancia")
                        || IsNumberInRange(parameters, "distancia", 0.5d, 5.0d)
                    );

            case "girar_avatar":
                return HasOnlyFields(
                        parameters,
                        new[] { "grados" },
                        Array.Empty<string>()
                    )
                    && IsNumberInRange(parameters, "grados", -360.0d, 360.0d);

            case "interactuar_objeto_3d":
                return HasOnlyFields(
                        parameters,
                        new[] { "nombre_objeto", "tipo_accion" },
                        Array.Empty<string>()
                    )
                    && IsString(parameters, "nombre_objeto")
                    && IsInteractionType(parameters, "tipo_accion");

            case "agarrar_objeto":
                return HasOnlyFields(
                        parameters,
                        new[] { "nombre_objeto" },
                        Array.Empty<string>()
                    )
                    && IsString(parameters, "nombre_objeto");

            case "soltar_objeto":
                return HasOnlyFields(
                        parameters,
                        Array.Empty<string>(),
                        new[] { "fuerza_lanzamiento" }
                    )
                    && (
                        !HasField(parameters, "fuerza_lanzamiento")
                        || IsNumberInRange(
                            parameters,
                            "fuerza_lanzamiento",
                            0.0d,
                            20.0d
                        )
                    );

            case "aplicar_fuerza":
                return HasOnlyFields(
                        parameters,
                        new[] {
                            "nombre_objeto",
                            "vector_x",
                            "vector_y",
                            "vector_z",
                            "fuerza",
                        },
                        Array.Empty<string>()
                    )
                    && IsString(parameters, "nombre_objeto")
                    && IsFiniteNumber(parameters, "vector_x")
                    && IsFiniteNumber(parameters, "vector_y")
                    && IsFiniteNumber(parameters, "vector_z")
                    && HasNonZeroDirection(parameters)
                    && IsNumberInRange(
                        parameters,
                        "fuerza",
                        0.0d,
                        100.0d,
                        minimumExclusive: true
                    );

            default:
                return true;
        }
    }

    private static bool HasOnlyFields(
        JObject parameters,
        IEnumerable<string> required,
        IEnumerable<string> optional
    )
    {
        var allowed = new HashSet<string>(StringComparer.Ordinal);
        foreach (string field in required)
        {
            allowed.Add(field);
            if (!HasField(parameters, field))
            {
                return false;
            }
        }
        foreach (string field in optional)
        {
            allowed.Add(field);
        }
        foreach (JProperty property in parameters.Properties())
        {
            if (!allowed.Contains(property.Name))
            {
                return false;
            }
        }
        return true;
    }

    private static bool HasField(JObject parameters, string field)
    {
        return parameters.Property(field, StringComparison.Ordinal) != null;
    }

    private static bool IsString(JObject parameters, string field)
    {
        JToken token = parameters.GetValue(field, StringComparison.Ordinal);
        return token != null && token.Type == JTokenType.String;
    }

    private static bool IsNull(JObject parameters, string field)
    {
        JToken token = parameters.GetValue(field, StringComparison.Ordinal);
        return token != null && token.Type == JTokenType.Null;
    }

    private static bool IsInteractionType(JObject parameters, string field)
    {
        JToken token = parameters.GetValue(field, StringComparison.Ordinal);
        return token != null
            && token.Type == JTokenType.String
            && InteractionTypes.Contains(token.Value<string>());
    }

    private static bool IsFiniteNumber(JObject parameters, string field)
    {
        return TryReadFiniteNumber(parameters, field, out _);
    }

    private static bool IsNumberInRange(
        JObject parameters,
        string field,
        double minimum,
        double maximum,
        bool minimumExclusive = false
    )
    {
        if (!TryReadFiniteNumber(parameters, field, out double value))
        {
            return false;
        }
        bool minimumValid = minimumExclusive ? value > minimum : value >= minimum;
        return minimumValid && value <= maximum;
    }

    private static bool TryReadFiniteNumber(
        JObject parameters,
        string field,
        out double value
    )
    {
        value = 0.0d;
        JToken token = parameters.GetValue(field, StringComparison.Ordinal);
        if (
            token == null
            || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
        )
        {
            return false;
        }
        try
        {
            value = token.Value<double>();
        }
        catch (Exception error) when (
            error is OverflowException
            || error is FormatException
            || error is InvalidCastException
        )
        {
            return false;
        }
        return !double.IsNaN(value) && !double.IsInfinity(value);
    }

    private static bool HasNonZeroDirection(JObject parameters)
    {
        return TryReadFiniteNumber(parameters, "vector_x", out double x)
            && TryReadFiniteNumber(parameters, "vector_y", out double y)
            && TryReadFiniteNumber(parameters, "vector_z", out double z)
            && (x != 0.0d || y != 0.0d || z != 0.0d);
    }
}
