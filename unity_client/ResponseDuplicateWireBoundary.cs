using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;

/// <summary>
/// Rechaza propiedades ambiguas en RespuestaAPI y AccionSaliente antes de que
/// Json.NET aplique su semantica last-value-wins.
///
/// B9 no decide el casing de un nombre unico ni reemplaza la validacion de
/// parametros B7, autoridad B5 o binding B6.
/// </summary>
public static class ResponseDuplicateWireBoundary
{
    private enum ObjectScope
    {
        Other,
        Root,
        Action,
    }

    private sealed class ContainerFrame
    {
        public bool IsObject;
        public ObjectScope Scope;
        public string PendingProperty;
        public HashSet<string> Properties;

        public ContainerFrame(bool isObject, ObjectScope scope)
        {
            IsObject = isObject;
            Scope = scope;
            PendingProperty = null;
            Properties = scope == ObjectScope.Root || scope == ObjectScope.Action
                ? new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                : null;
        }
    }

    public static bool TryValidateResponse(string rawJson)
    {
        if (string.IsNullOrWhiteSpace(rawJson))
        {
            return false;
        }

        var containers = new Stack<ContainerFrame>();
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
                        TakePendingProperty(containers);
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
                        if (property == null || frame.PendingProperty != null)
                        {
                            return false;
                        }
                        if (
                            frame.Properties != null
                            && !frame.Properties.Add(property)
                        )
                        {
                            return false;
                        }
                        frame.PendingProperty = property;
                        continue;
                    }

                    TakePendingProperty(containers);
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

        return containers.Count == 0;
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
        return containers.Peek().Scope == ObjectScope.Root
            && string.Equals(property, "accion", StringComparison.OrdinalIgnoreCase)
                ? ObjectScope.Action
                : ObjectScope.Other;
    }
}
