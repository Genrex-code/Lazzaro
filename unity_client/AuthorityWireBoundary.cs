using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using Newtonsoft.Json;

/// <summary>
/// Preserva la semantica de los tokens JSON que forman la autoridad fisica.
/// Debe ejecutarse sobre el texto crudo antes de materializar RespuestaAPI,
/// porque Json.NET convierte strings, floats y campos ausentes a int/long.
/// </summary>
public static class AuthorityWireBoundary
{
    private enum ObjectScope
    {
        Other,
        Root,
        Action,
        Authority,
        Target,
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
            Properties = isObject
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
        bool actionMemberSeen = false;
        bool authorityMemberSeen = false;
        bool authorityObjectSeen = false;
        bool authorityVersionSeen = false;
        bool authorityGenerationSeen = false;
        bool targetObjectSeen = false;
        bool targetVersionSeen = false;
        bool targetGenerationSeen = false;
        long authorityGeneration = 0;
        long targetGeneration = 0;

        try
        {
            using (var text = new StringReader(rawJson))
            using (var reader = new JsonTextReader(text))
            {
                while (reader.Read())
                {
                    if (reader.TokenType == JsonToken.StartObject)
                    {
                        string property = TakePendingProperty(containers);
                        ObjectScope scope = ChildScope(containers, property);

                        if (scope == ObjectScope.Authority)
                        {
                            authorityObjectSeen = true;
                        }
                        else if (scope == ObjectScope.Target)
                        {
                            targetObjectSeen = true;
                        }

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
                        string property = TakePendingProperty(containers);
                        if (IsAuthorityContainerProperty(containers, property))
                        {
                            return false;
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

                        if (
                            (frame.Scope == ObjectScope.Authority
                                || frame.Scope == ObjectScope.Target)
                            && !frame.Properties.Add(property)
                        )
                        {
                            return false;
                        }

                        if (
                            frame.Scope == ObjectScope.Root
                            && EqualsWireName(property, "accion")
                        )
                        {
                            if (actionMemberSeen)
                            {
                                return false;
                            }
                            actionMemberSeen = true;
                        }
                        if (
                            frame.Scope == ObjectScope.Action
                            && EqualsWireName(property, "perception_authority")
                        )
                        {
                            if (authorityMemberSeen)
                            {
                                return false;
                            }
                            authorityMemberSeen = true;
                        }

                        frame.PendingProperty = property;
                        continue;
                    }

                    string scalarProperty = TakePendingProperty(containers);
                    if (scalarProperty == null)
                    {
                        continue;
                    }
                    if (
                        !ValidateScalar(
                            containers,
                            scalarProperty,
                            reader,
                            ref authorityVersionSeen,
                            ref authorityGenerationSeen,
                            ref authorityGeneration,
                            ref targetVersionSeen,
                            ref targetGenerationSeen,
                            ref targetGeneration
                        )
                    )
                    {
                        return false;
                    }
                }
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

        if (containers.Count != 0)
        {
            return false;
        }
        if (!authorityObjectSeen)
        {
            // Ausencia o null conservan la compatibilidad legacy actual.
            return true;
        }
        return authorityVersionSeen
            && authorityGenerationSeen
            && targetObjectSeen
            && targetVersionSeen
            && targetGenerationSeen
            && authorityGeneration == targetGeneration;
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
        if (
            parent == ObjectScope.Action
            && EqualsWireName(property, "perception_authority")
        )
        {
            return ObjectScope.Authority;
        }
        if (parent == ObjectScope.Authority && EqualsWireName(property, "target"))
        {
            return ObjectScope.Target;
        }
        return ObjectScope.Other;
    }

    private static bool IsAuthorityContainerProperty(
        Stack<ContainerFrame> containers,
        string property
    )
    {
        if (containers.Count == 0 || property == null)
        {
            return false;
        }
        ObjectScope scope = containers.Peek().Scope;
        return (
                scope == ObjectScope.Action
                && EqualsWireName(property, "perception_authority")
            )
            || (scope == ObjectScope.Authority && EqualsWireName(property, "target"))
            || (
                scope == ObjectScope.Authority
                && (
                    EqualsWireName(property, "version")
                    || EqualsWireName(property, "generation")
                )
            )
            || (
                scope == ObjectScope.Target
                && (
                    EqualsWireName(property, "contract_version")
                    || EqualsWireName(property, "generation")
                )
            );
    }

    private static bool ValidateScalar(
        Stack<ContainerFrame> containers,
        string property,
        JsonTextReader reader,
        ref bool authorityVersionSeen,
        ref bool authorityGenerationSeen,
        ref long authorityGeneration,
        ref bool targetVersionSeen,
        ref bool targetGenerationSeen,
        ref long targetGeneration
    )
    {
        if (containers.Count == 0)
        {
            return true;
        }
        ObjectScope scope = containers.Peek().Scope;
        if (
            scope == ObjectScope.Action
            && EqualsWireName(property, "perception_authority")
        )
        {
            return reader.TokenType == JsonToken.Null;
        }
        if (scope == ObjectScope.Authority && EqualsWireName(property, "target"))
        {
            return false;
        }
        if (scope == ObjectScope.Authority && EqualsWireName(property, "version"))
        {
            if (property != "version" || !TryReadStrictInt64(reader, out long value) || value != 1)
            {
                return false;
            }
            authorityVersionSeen = true;
            return true;
        }
        if (scope == ObjectScope.Authority && EqualsWireName(property, "generation"))
        {
            if (
                property != "generation"
                || !TryReadStrictInt64(reader, out long value)
                || value < 0
            )
            {
                return false;
            }
            authorityGeneration = value;
            authorityGenerationSeen = true;
            return true;
        }
        if (scope == ObjectScope.Target && EqualsWireName(property, "contract_version"))
        {
            if (
                property != "contract_version"
                || !TryReadStrictInt64(reader, out long value)
                || value != 1
            )
            {
                return false;
            }
            targetVersionSeen = true;
            return true;
        }
        if (scope == ObjectScope.Target && EqualsWireName(property, "generation"))
        {
            if (
                property != "generation"
                || !TryReadStrictInt64(reader, out long value)
                || value < 0
            )
            {
                return false;
            }
            targetGeneration = value;
            targetGenerationSeen = true;
            return true;
        }
        return true;
    }

    private static bool TryReadStrictInt64(JsonTextReader reader, out long value)
    {
        value = 0;
        if (reader.TokenType != JsonToken.Integer || reader.Value == null)
        {
            return false;
        }
        try
        {
            value = Convert.ToInt64(reader.Value, CultureInfo.InvariantCulture);
            return true;
        }
        catch (Exception error) when (
            error is OverflowException
            || error is FormatException
            || error is InvalidCastException
        )
        {
            return false;
        }
    }

    private static bool EqualsWireName(string actual, string expected)
    {
        return string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase);
    }
}
