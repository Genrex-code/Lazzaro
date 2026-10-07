using System;
using UnityEngine;

public class InteractableMetadata : MonoBehaviour
{
    [Tooltip("Id persistente explicito del productor. Vacio significa identidad persistente ausente.")]
    public string objectId = "";

    [Tooltip("Nombre humano para reportar al backend. Si queda vacio, se usa el nombre del GameObject.")]
    public string displayName = "";

    [Tooltip("Aliases humanos, por ejemplo arbol, pino, roca.")]
    public string[] aliases = new string[0];

    [Tooltip("Affordances canonicas definidas por el contrato de Capa 1.")]
    public string[] affordances = new string[0];

    [Range(0f, 1f)]
    public float confidence = 1f;

    public string GetObjectId(GameObject fallbackObject)
    {
        if (!string.IsNullOrWhiteSpace(objectId))
        {
            return objectId.Trim();
        }

        return fallbackObject != null ? fallbackObject.name : "unknown_object";
    }

    public string GetExplicitObjectId()
    {
        return string.IsNullOrWhiteSpace(objectId) ? null : objectId.Trim();
    }

    public string GetDisplayName(GameObject fallbackObject)
    {
        if (!string.IsNullOrWhiteSpace(displayName))
        {
            return displayName.Trim();
        }

        return fallbackObject != null ? fallbackObject.name : "unknown_object";
    }

    public string[] GetAliases()
    {
        return aliases ?? new string[0];
    }

    public string[] GetAffordances()
    {
        return affordances ?? new string[0];
    }
}
