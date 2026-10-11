using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;

/// <summary>
/// Valida la estructura minima de una accion antes de que Json.NET pueda
/// colapsar missing/null/tipos escalares en defaults del DTO de Unity.
///
/// B7 es independiente de la autoridad perceptual B5 y del binding B6.
/// </summary>
public static class ActionEnvelopeWireBoundary
{
    private static readonly HashSet<string> ActionLifecycleStates =
        new HashSet<string>(StringComparer.Ordinal)
        {
            "legacy_unknown",
            "created",
            "awaiting_authorization",
            "authorized",
            "dispatched",
            "executing",
            "reported_success",
            "reported_failure",
            "rejected",
            "skipped",
        };

    private enum ObjectScope
    {
        Other,
        Root,
        Action,
        Parameters,
    }

    private sealed class ContainerFrame
    {
        public bool IsObject;
        public ObjectScope Scope;
        public string PendingProperty;

        public ContainerFrame(bool isObject, ObjectScope scope)
        {
            IsObject = isObject;
            Scope = scope;
            PendingProperty = null;
        }
    }

    public static bool TryValidateResponse(string rawJson)
    {
        if (string.IsNullOrWhiteSpace(rawJson))
        {
            return false;
        }

        var containers = new Stack<ContainerFrame>();
        bool actionObjectSeen = false;
        bool actionIdSeen = false;
        bool actionNameSeen = false;
        bool parametersMemberSeen = false;
        bool parametersObjectSeen = false;
        string actionName = null;
        var parameterTokenTypes = new Dictionary<string, JsonToken>(
            StringComparer.Ordinal
        );

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
                        string property = TakePendingProperty(containers);
                        ObjectScope scope = ChildScope(containers, property);

                        if (containers.Count == 0)
                        {
                            scope = ObjectScope.Root;
                        }
                        else if (
                            containers.Peek().Scope == ObjectScope.Root
                            && EqualsWireName(property, "accion")
                        )
                        {
                            actionObjectSeen = true;
                        }
                        else if (
                            containers.Peek().Scope == ObjectScope.Action
                            && EqualsWireName(property, "parametros")
                        )
                        {
                            if (parametersMemberSeen)
                            {
                                return false;
                            }
                            parametersMemberSeen = true;
                            parametersObjectSeen = true;
                        }
                        else if (IsDirectActionScalarProperty(containers, property))
                        {
                            return false;
                        }
                        else if (containers.Peek().Scope == ObjectScope.Parameters)
                        {
                            if (!RecordParameterToken(parameterTokenTypes, property, reader.TokenType))
                            {
                                return false;
                            }
                        }

                        containers.Push(new ContainerFrame(true, scope));
                        continue;
                    }

                    if (reader.TokenType == JsonToken.EndObject)
                    {
                        if (containers.Count == 0 || !containers.Peek().IsObject)
                        {
                            return false;
                        }
                        containers.Pop();
                        continue;
                    }

                    if (reader.TokenType == JsonToken.StartArray)
                    {
                        string property = TakePendingProperty(containers);
                        if (
                            IsRootActionProperty(containers, property)
                            || IsActionParametersProperty(containers, property)
                            || IsDirectActionScalarProperty(containers, property)
                        )
                        {
                            return false;
                        }
                        if (containers.Count > 0 && containers.Peek().Scope == ObjectScope.Parameters)
                        {
                            if (!RecordParameterToken(parameterTokenTypes, property, reader.TokenType))
                            {
                                return false;
                            }
                        }
                        containers.Push(new ContainerFrame(false, ObjectScope.Other));
                        continue;
                    }

                    if (reader.TokenType == JsonToken.EndArray)
                    {
                        if (containers.Count == 0 || containers.Peek().IsObject)
                        {
                            return false;
                        }
                        containers.Pop();
                        continue;
                    }

                    if (reader.TokenType == JsonToken.PropertyName)
                    {
                        if (containers.Count == 0 || !containers.Peek().IsObject)
                        {
                            return false;
                        }
                        ContainerFrame frame = containers.Peek();
                        string property = reader.Value as string;
                        if (string.IsNullOrEmpty(property) || frame.PendingProperty != null)
                        {
                            return false;
                        }
                        frame.PendingProperty = property;
                        continue;
                    }

                    string scalarProperty = TakePendingProperty(containers);
                    if (scalarProperty == null)
                    {
                        continue;
                    }

                    if (IsRootActionProperty(containers, scalarProperty))
                    {
                        if (reader.TokenType != JsonToken.Null)
                        {
                            return false;
                        }
                        continue;
                    }
                    if (IsActionParametersProperty(containers, scalarProperty))
                    {
                        return false;
                    }
                    if (containers.Count > 0 && containers.Peek().Scope == ObjectScope.Action)
                    {
                        if (
                            !ValidateActionScalar(
                                scalarProperty,
                                reader,
                                ref actionIdSeen,
                                ref actionNameSeen,
                                ref actionName
                            )
                        )
                        {
                            return false;
                        }
                        continue;
                    }
                    if (containers.Count > 0 && containers.Peek().Scope == ObjectScope.Parameters)
                    {
                        if (!RecordParameterToken(
                            parameterTokenTypes,
                            scalarProperty,
                            reader.TokenType
                        ))
                        {
                            return false;
                        }
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

        if (containers.Count != 0)
        {
            return false;
        }
        if (!actionObjectSeen)
        {
            return true;
        }
        if (
            !actionIdSeen
            || !actionNameSeen
            || !parametersMemberSeen
            || !parametersObjectSeen
        )
        {
            return false;
        }
        if (!string.Equals(actionName, "cancelar_tarea", StringComparison.Ordinal))
        {
            return true;
        }
        return ValidateCancelParameters(parameterTokenTypes);
    }

    private static bool ValidateCancelParameters(
        Dictionary<string, JsonToken> parameterTokenTypes
    )
    {
        foreach (KeyValuePair<string, JsonToken> entry in parameterTokenTypes)
        {
            switch (entry.Key)
            {
                case "cancelar_movimiento":
                case "cancelar_cola":
                    if (entry.Value != JsonToken.Boolean)
                    {
                        return false;
                    }
                    break;
                case "motivo":
                case "texto_confirmacion":
                    if (entry.Value != JsonToken.String)
                    {
                        return false;
                    }
                    break;
                default:
                    return false;
            }
        }
        return true;
    }

    private static bool RecordParameterToken(
        Dictionary<string, JsonToken> parameterTokenTypes,
        string property,
        JsonToken tokenType
    )
    {
        if (property == null || parameterTokenTypes.ContainsKey(property))
        {
            return false;
        }
        parameterTokenTypes.Add(property, tokenType);
        return true;
    }

    private static string TakePendingProperty(Stack<ContainerFrame> containers)
    {
        if (containers.Count == 0 || !containers.Peek().IsObject)
        {
            return null;
        }
        ContainerFrame parent = containers.Peek();
        string property = parent.PendingProperty;
        parent.PendingProperty = null;
        return property;
    }

    private static ObjectScope ChildScope(
        Stack<ContainerFrame> containers,
        string property
    )
    {
        if (containers.Count == 0 || property == null)
        {
            return ObjectScope.Other;
        }
        ObjectScope parent = containers.Peek().Scope;
        if (parent == ObjectScope.Root && EqualsWireName(property, "accion"))
        {
            return ObjectScope.Action;
        }
        if (parent == ObjectScope.Action && EqualsWireName(property, "parametros"))
        {
            return ObjectScope.Parameters;
        }
        return ObjectScope.Other;
    }

    private static bool IsRootActionProperty(
        Stack<ContainerFrame> containers,
        string property
    )
    {
        return containers.Count > 0
            && containers.Peek().Scope == ObjectScope.Root
            && EqualsWireName(property, "accion");
    }

    private static bool IsActionParametersProperty(
        Stack<ContainerFrame> containers,
        string property
    )
    {
        return containers.Count > 0
            && containers.Peek().Scope == ObjectScope.Action
            && EqualsWireName(property, "parametros");
    }

    private static bool IsDirectActionScalarProperty(
        Stack<ContainerFrame> containers,
        string property
    )
    {
        return containers.Count > 0
            && containers.Peek().Scope == ObjectScope.Action
            && (
                EqualsWireName(property, "id_accion")
                || EqualsWireName(property, "nombre")
                || EqualsWireName(property, "requiere_confirmacion")
                || EqualsWireName(property, "estado_ejecucion")
                || EqualsWireName(property, "id_transaccion_origen")
            );
    }

    private static bool ValidateActionScalar(
        string property,
        JsonTextReader reader,
        ref bool actionIdSeen,
        ref bool actionNameSeen,
        ref string actionName
    )
    {
        if (EqualsWireName(property, "id_accion"))
        {
            if (reader.TokenType != JsonToken.String)
            {
                return false;
            }
            actionIdSeen = true;
            return true;
        }
        if (EqualsWireName(property, "nombre"))
        {
            if (reader.TokenType != JsonToken.String)
            {
                return false;
            }
            actionName = reader.Value as string;
            actionNameSeen = true;
            return true;
        }
        if (EqualsWireName(property, "requiere_confirmacion"))
        {
            return reader.TokenType == JsonToken.Boolean;
        }
        if (EqualsWireName(property, "estado_ejecucion"))
        {
            return reader.TokenType == JsonToken.String
                && ActionLifecycleStates.Contains(reader.Value as string);
        }
        if (EqualsWireName(property, "id_transaccion_origen"))
        {
            return reader.TokenType == JsonToken.String
                || reader.TokenType == JsonToken.Null;
        }
        return true;
    }

    private static bool EqualsWireName(string actual, string expected)
    {
        return string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase);
    }
}
