using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

public enum MaterialEffectStatus
{
    Applied,
    NoEffect,
    Unsupported,
    Failed
}

public enum MaterialEffectKind
{
    Unknown,
    Interact,
    Grab,
    Release,
    Force
}

public sealed class MaterialEffectReceipt
{
    public string ActionId { get; private set; }
    public MaterialEffectKind EffectKind { get; private set; }
    public MaterialEffectStatus Status { get; private set; }
    public string ObjectId { get; private set; }
    public Dictionary<string, object> Evidence { get; private set; }
    public string Message { get; private set; }

    private MaterialEffectReceipt(
        string actionId,
        MaterialEffectKind effectKind,
        MaterialEffectStatus status,
        string objectId,
        Dictionary<string, object> evidence,
        string message
    )
    {
        ActionId = actionId ?? "";
        EffectKind = effectKind;
        Status = status;
        ObjectId = string.IsNullOrWhiteSpace(objectId) ? "none" : objectId;
        Evidence = evidence ?? new Dictionary<string, object>();
        Message = message ?? "";
    }

    public bool IsApplied
    {
        get { return Status == MaterialEffectStatus.Applied; }
    }

    public bool IsValidFor(string expectedActionId, MaterialEffectKind expectedEffectKind)
    {
        if (
            string.IsNullOrWhiteSpace(expectedActionId)
            || ActionId != expectedActionId
            || EffectKind == MaterialEffectKind.Unknown
            || EffectKind != expectedEffectKind
            || string.IsNullOrWhiteSpace(ObjectId)
            || Evidence == null
        )
        {
            return false;
        }
        if (!IsApplied)
        {
            return Evidence.TryGetValue("reason_code", out object reason)
                && reason is string
                && !string.IsNullOrWhiteSpace((string)reason);
        }

        switch (EffectKind)
        {
            case MaterialEffectKind.Interact:
                return Evidence.TryGetValue("effect_observed", out object observed)
                    && observed is bool
                    && (bool)observed
                    && HasNonEmptyString(Evidence, "interaction_type")
                    && HasNonEmptyString(Evidence, "observer");
            case MaterialEffectKind.Grab:
                return HasTrue(Evidence, "held_reference_changed")
                    && HasTrue(Evidence, "parent_is_hold_point")
                    && Evidence.TryGetValue("held_after", out object heldAfter)
                    && heldAfter is string
                    && (string)heldAfter == ObjectId;
            case MaterialEffectKind.Release:
                return HasTrue(Evidence, "held_reference_changed")
                    && HasFalse(Evidence, "parent_is_hold_point")
                    && Evidence.TryGetValue("held_before", out object heldBefore)
                    && heldBefore is string
                    && (string)heldBefore == ObjectId
                    && Evidence.TryGetValue("held_after", out object releasedAfter)
                    && releasedAfter == null;
            case MaterialEffectKind.Force:
                return HasTrue(Evidence, "fixed_update_observed")
                    && HasVector3(Evidence, "velocity_before")
                    && HasVector3(Evidence, "velocity_after")
                    && HasPositiveFiniteNumber(Evidence, "directional_delta");
            default:
                return false;
        }
    }

    public static MaterialEffectReceipt Applied(
        string actionId,
        MaterialEffectKind effectKind,
        string objectId,
        Dictionary<string, object> evidence,
        string message
    )
    {
        return new MaterialEffectReceipt(
            actionId, effectKind, MaterialEffectStatus.Applied, objectId, evidence, message
        );
    }

    public static MaterialEffectReceipt NoEffect(
        string actionId,
        MaterialEffectKind effectKind,
        string objectId,
        string reasonCode,
        string message,
        Dictionary<string, object> evidence = null
    )
    {
        return Negative(
            actionId, effectKind, MaterialEffectStatus.NoEffect, objectId, reasonCode, message, evidence
        );
    }

    public static MaterialEffectReceipt Unsupported(
        string actionId,
        MaterialEffectKind effectKind,
        string objectId,
        string reasonCode,
        string message,
        Dictionary<string, object> evidence = null
    )
    {
        return Negative(
            actionId, effectKind, MaterialEffectStatus.Unsupported, objectId, reasonCode, message, evidence
        );
    }

    public static MaterialEffectReceipt Failed(
        string actionId,
        MaterialEffectKind effectKind,
        string objectId,
        string reasonCode,
        string message,
        Dictionary<string, object> evidence = null
    )
    {
        return Negative(
            actionId, effectKind, MaterialEffectStatus.Failed, objectId, reasonCode, message, evidence
        );
    }

    private static MaterialEffectReceipt Negative(
        string actionId,
        MaterialEffectKind effectKind,
        MaterialEffectStatus status,
        string objectId,
        string reasonCode,
        string message,
        Dictionary<string, object> evidence
    )
    {
        Dictionary<string, object> payload = evidence ?? new Dictionary<string, object>();
        payload["reason_code"] = string.IsNullOrWhiteSpace(reasonCode) ? "unspecified" : reasonCode;
        return new MaterialEffectReceipt(actionId, effectKind, status, objectId, payload, message);
    }

    public Dictionary<string, object> ToWirePayload()
    {
        return new Dictionary<string, object>
        {
            { "action_id", ActionId },
            { "effect_kind", EffectKindToWire(EffectKind) },
            { "status", StatusToWire(Status) },
            { "object_id", ObjectId },
            { "evidence", new Dictionary<string, object>(Evidence) }
        };
    }

    private static string StatusToWire(MaterialEffectStatus status)
    {
        switch (status)
        {
            case MaterialEffectStatus.Applied:
                return "applied";
            case MaterialEffectStatus.NoEffect:
                return "no_effect";
            case MaterialEffectStatus.Unsupported:
                return "unsupported";
            default:
                return "failed";
        }
    }

    private static string EffectKindToWire(MaterialEffectKind effectKind)
    {
        switch (effectKind)
        {
            case MaterialEffectKind.Interact:
                return "interact";
            case MaterialEffectKind.Grab:
                return "grab";
            case MaterialEffectKind.Release:
                return "release";
            case MaterialEffectKind.Force:
                return "force";
            default:
                return "unknown";
        }
    }

    private static bool HasTrue(Dictionary<string, object> evidence, string key)
    {
        return evidence.TryGetValue(key, out object value) && value is bool && (bool)value;
    }

    private static bool HasFalse(Dictionary<string, object> evidence, string key)
    {
        return evidence.TryGetValue(key, out object value) && value is bool && !(bool)value;
    }

    private static bool HasNonEmptyString(Dictionary<string, object> evidence, string key)
    {
        return evidence.TryGetValue(key, out object value)
            && value is string
            && !string.IsNullOrWhiteSpace((string)value);
    }

    private static bool HasVector3(Dictionary<string, object> evidence, string key)
    {
        if (!evidence.TryGetValue(key, out object value) || !(value is IList values) || values.Count != 3)
        {
            return false;
        }
        foreach (object component in values)
        {
            if (!IsFiniteNumber(component))
            {
                return false;
            }
        }
        return true;
    }

    private static bool HasPositiveFiniteNumber(Dictionary<string, object> evidence, string key)
    {
        return evidence.TryGetValue(key, out object value)
            && IsFiniteNumber(value)
            && Convert.ToSingle(value) > 0f;
    }

    private static bool IsFiniteNumber(object value)
    {
        if (value is bool || !(value is byte || value is short || value is int || value is long || value is float || value is double || value is decimal))
        {
            return false;
        }
        float parsed = Convert.ToSingle(value);
        return !float.IsNaN(parsed) && !float.IsInfinity(parsed);
    }
}

public interface IMaterialInteractable
{
    MaterialEffectReceipt TryInteract(string actionId, string interactionType, GameObject actor);
}

[DisallowMultipleComponent]
public sealed class ObjectMaterializationController : MonoBehaviour
{
    [Tooltip("Punto de ownership para objetos sostenidos. Si queda vacio, usa el transform del avatar.")]
    [SerializeField] private Transform holdPoint;

    public GameObject HeldObject { get; private set; }
    public string HeldObjectId { get; private set; }

    private Rigidbody heldBody;
    private Transform originalParent;
    private bool originalIsKinematic;
    private bool originalUseGravity;

    private Transform EffectiveHoldPoint
    {
        get { return holdPoint; }
    }

    public MaterialEffectReceipt TryInteract(
        string actionId,
        Collider target,
        string interactionType
    )
    {
        string objectId = GetObjectId(target);
        if (target == null)
        {
            return MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Interact, objectId, "target_missing", "No existe un target para interactuar."
            );
        }

        IMaterialInteractable capability = FindInteractionCapability(target);
        if (capability == null)
        {
            return MaterialEffectReceipt.Unsupported(
                actionId,
                MaterialEffectKind.Interact,
                objectId,
                "capability_missing",
                $"'{objectId}' no posee una capability de interaccion material."
            );
        }

        try
        {
            MaterialEffectReceipt receipt = capability.TryInteract(
                actionId, interactionType, gameObject
            );
            if (
                receipt == null
                || receipt.ActionId != actionId
                || receipt.EffectKind != MaterialEffectKind.Interact
                || receipt.ObjectId != objectId
            )
            {
                return MaterialEffectReceipt.Failed(
                    actionId,
                    MaterialEffectKind.Interact,
                    objectId,
                    "invalid_component_receipt",
                    "El componente interactuable devolvio un receipt incompatible."
                );
            }
            return receipt;
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            return MaterialEffectReceipt.Failed(
                actionId,
                MaterialEffectKind.Interact,
                objectId,
                "materializer_exception",
                "El componente interactuable fallo durante la materializacion."
            );
        }
    }

    public MaterialEffectReceipt TryGrab(string actionId, Collider target)
    {
        string objectId = GetObjectId(target);
        if (target == null)
        {
            return MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Grab, objectId, "target_missing", "No existe un target para agarrar."
            );
        }
        if (EffectiveHoldPoint == null)
        {
            return MaterialEffectReceipt.Unsupported(
                actionId,
                MaterialEffectKind.Grab,
                objectId,
                "hold_point_missing",
                "El avatar no posee un hold point configurado."
            );
        }
        if (!HasAffordance(target, "agarrable"))
        {
            return MaterialEffectReceipt.Unsupported(
                actionId, MaterialEffectKind.Grab, objectId, "affordance_missing", $"'{objectId}' no es agarrable."
            );
        }
        Rigidbody body = target.attachedRigidbody;
        if (body == null || body.gameObject == gameObject)
        {
            return MaterialEffectReceipt.Unsupported(
                actionId, MaterialEffectKind.Grab, objectId, "rigidbody_missing", $"'{objectId}' no posee Rigidbody manipulable."
            );
        }
        if (HeldObject != null)
        {
            return MaterialEffectReceipt.NoEffect(
                actionId,
                MaterialEffectKind.Grab,
                objectId,
                "already_holding_object",
                $"Ya se sostiene '{HeldObjectId}'.",
                new Dictionary<string, object>
                {
                    { "held_before", HeldObjectId },
                    { "held_after", HeldObjectId }
                }
            );
        }

        string heldBefore = null;
        try
        {
            originalParent = body.transform.parent;
            originalIsKinematic = body.isKinematic;
            originalUseGravity = body.useGravity;
            heldBody = body;
            HeldObject = body.gameObject;
            HeldObjectId = objectId;

            body.velocity = Vector3.zero;
            body.angularVelocity = Vector3.zero;
            body.isKinematic = true;
            body.useGravity = false;
            body.transform.SetParent(EffectiveHoldPoint, false);
            body.transform.localPosition = Vector3.zero;
            body.transform.localRotation = Quaternion.identity;

            bool referenceChanged = HeldObject == body.gameObject;
            bool parentObserved = body.transform.parent == EffectiveHoldPoint;
            Dictionary<string, object> evidence = new Dictionary<string, object>
            {
                { "held_before", heldBefore },
                { "held_after", HeldObjectId },
                { "held_reference_changed", referenceChanged },
                { "parent_is_hold_point", parentObserved }
            };
            if (referenceChanged && parentObserved)
            {
                return MaterialEffectReceipt.Applied(
                    actionId, MaterialEffectKind.Grab, objectId, evidence, $"'{objectId}' quedo bajo ownership de agarre."
                );
            }
            RestoreFailedGrab();
            return MaterialEffectReceipt.NoEffect(
                actionId, MaterialEffectKind.Grab, objectId, "grab_postcondition_missing", "No aparecio la postcondicion de agarre."
            );
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            RestoreFailedGrab();
            return MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Grab, objectId, "materializer_exception", "Fallo el materializer de agarre."
            );
        }
    }

    public MaterialEffectReceipt TryRelease(string actionId, float throwForce)
    {
        if (HeldObject == null || heldBody == null)
        {
            return MaterialEffectReceipt.NoEffect(
                actionId, MaterialEffectKind.Release, "none", "nothing_held", "No habia un objeto sostenido para soltar."
            );
        }

        Rigidbody body = heldBody;
        string objectId = HeldObjectId;
        string heldBefore = objectId;
        try
        {
            body.transform.SetParent(originalParent, true);
            body.isKinematic = originalIsKinematic;
            body.useGravity = originalUseGravity;

            HeldObject = null;
            HeldObjectId = null;
            heldBody = null;
            originalParent = null;

            bool impulseApplied = false;
            if (throwForce > 0f && !body.isKinematic)
            {
                body.AddForce(transform.forward * throwForce, ForceMode.Impulse);
                impulseApplied = true;
            }

            bool referenceChanged = HeldObject == null;
            bool noLongerAtHoldPoint = body.transform.parent != EffectiveHoldPoint;
            Dictionary<string, object> evidence = new Dictionary<string, object>
            {
                { "held_before", heldBefore },
                { "held_after", null },
                { "held_reference_changed", referenceChanged },
                { "parent_is_hold_point", !noLongerAtHoldPoint },
                { "throw_impulse_requested", throwForce > 0f },
                { "throw_impulse_applied", impulseApplied }
            };
            if (referenceChanged && noLongerAtHoldPoint)
            {
                return MaterialEffectReceipt.Applied(
                    actionId, MaterialEffectKind.Release, objectId, evidence, $"Se libero '{objectId}' del ownership de agarre."
                );
            }
            return MaterialEffectReceipt.NoEffect(
                actionId, MaterialEffectKind.Release, objectId, "release_postcondition_missing", "No aparecio la postcondicion de release."
            );
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            return MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Release, objectId, "materializer_exception", "Fallo el materializer de release."
            );
        }
    }

    public void ApplyForce(
        string actionId,
        Collider target,
        Vector3 direction,
        float force,
        Action<MaterialEffectReceipt> completed
    )
    {
        StartCoroutine(ObserveForce(actionId, target, direction, force, completed));
    }

    private IEnumerator ObserveForce(
        string actionId,
        Collider target,
        Vector3 direction,
        float force,
        Action<MaterialEffectReceipt> completed
    )
    {
        string objectId = GetObjectId(target);
        if (target == null)
        {
            completed?.Invoke(MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Force, objectId, "target_missing", "No existe un target para aplicar fuerza."
            ));
            yield break;
        }
        if (!HasAffordance(target, "empujable"))
        {
            completed?.Invoke(MaterialEffectReceipt.Unsupported(
                actionId, MaterialEffectKind.Force, objectId, "affordance_missing", $"'{objectId}' no es empujable."
            ));
            yield break;
        }
        Rigidbody body = target.attachedRigidbody;
        if (body == null)
        {
            completed?.Invoke(MaterialEffectReceipt.Unsupported(
                actionId, MaterialEffectKind.Force, objectId, "rigidbody_missing", $"'{objectId}' no posee Rigidbody."
            ));
            yield break;
        }
        if (body.isKinematic || body.constraints == RigidbodyConstraints.FreezeAll)
        {
            completed?.Invoke(MaterialEffectReceipt.Unsupported(
                actionId, MaterialEffectKind.Force, objectId, "rigidbody_blocked", $"El Rigidbody de '{objectId}' no admite movimiento."
            ));
            yield break;
        }
        if (!IsFiniteVector(direction) || direction.sqrMagnitude <= Mathf.Epsilon || !IsFinite(force) || force <= 0f)
        {
            completed?.Invoke(MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Force, objectId, "invalid_physical_parameters", "La fuerza no posee direccion y magnitud validas."
            ));
            yield break;
        }

        Vector3 normalizedDirection = direction.normalized;
        Vector3 velocityBefore = body.velocity;
        Vector3 positionBefore = body.position;
        try
        {
            body.AddForce(normalizedDirection * force, ForceMode.Force);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            completed?.Invoke(MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Force, objectId, "materializer_exception", "Fallo el materializer de fuerza."
            ));
            yield break;
        }

        yield return new WaitForFixedUpdate();

        Vector3 velocityAfter;
        Vector3 positionAfter;
        try
        {
            velocityAfter = body.velocity;
            positionAfter = body.position;
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            completed?.Invoke(MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Force, objectId, "observation_failed", "No fue posible observar el Rigidbody despues de FixedUpdate."
            ));
            yield break;
        }
        if (!IsFiniteVector(velocityBefore) || !IsFiniteVector(velocityAfter) || !IsFiniteVector(positionBefore) || !IsFiniteVector(positionAfter))
        {
            completed?.Invoke(MaterialEffectReceipt.Failed(
                actionId, MaterialEffectKind.Force, objectId, "non_finite_observation", "La observacion fisica produjo valores no finitos."
            ));
            yield break;
        }
        float velocityProjection = Vector3.Dot(velocityAfter - velocityBefore, normalizedDirection);
        float positionProjection = Vector3.Dot(positionAfter - positionBefore, normalizedDirection);
        float directionalDelta = Mathf.Max(velocityProjection, positionProjection);
        Dictionary<string, object> evidence = new Dictionary<string, object>
        {
            { "fixed_update_observed", true },
            { "velocity_before", VectorPayload(velocityBefore) },
            { "velocity_after", VectorPayload(velocityAfter) },
            { "directional_delta", directionalDelta },
            { "position_delta", Vector3.Distance(positionBefore, positionAfter) }
        };
        if (directionalDelta > Mathf.Epsilon)
        {
            completed?.Invoke(MaterialEffectReceipt.Applied(
                actionId, MaterialEffectKind.Force, objectId, evidence, $"Se observo respuesta fisica compatible en '{objectId}'."
            ));
            yield break;
        }

        evidence["reason_code"] = "force_postcondition_missing";
        completed?.Invoke(MaterialEffectReceipt.NoEffect(
            actionId,
            MaterialEffectKind.Force,
            objectId,
            "force_postcondition_missing",
            "AddForce fue invocado, pero no se observo un cambio fisico compatible.",
            evidence
        ));
    }

    private void RestoreFailedGrab()
    {
        if (heldBody != null)
        {
            heldBody.transform.SetParent(originalParent, true);
            heldBody.isKinematic = originalIsKinematic;
            heldBody.useGravity = originalUseGravity;
        }
        heldBody = null;
        HeldObject = null;
        HeldObjectId = null;
        originalParent = null;
    }

    private static IMaterialInteractable FindInteractionCapability(Collider target)
    {
        MonoBehaviour[] components = target.GetComponents<MonoBehaviour>();
        foreach (MonoBehaviour component in components)
        {
            IMaterialInteractable capability = component as IMaterialInteractable;
            if (capability != null)
            {
                return capability;
            }
        }
        return null;
    }

    private static bool HasAffordance(Collider target, string expected)
    {
        InteractableMetadata metadata = target.GetComponent<InteractableMetadata>();
        if (metadata == null)
        {
            metadata = target.GetComponentInParent<InteractableMetadata>();
        }
        if (metadata == null)
        {
            return false;
        }
        foreach (string affordance in metadata.GetAffordances())
        {
            if (string.Equals(affordance, expected, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    private static string GetObjectId(Collider target)
    {
        if (target == null)
        {
            return "none";
        }
        InteractableMetadata metadata = target.GetComponent<InteractableMetadata>();
        if (metadata == null)
        {
            metadata = target.GetComponentInParent<InteractableMetadata>();
        }
        return metadata != null ? metadata.GetObjectId(target.gameObject) : target.name;
    }

    private static List<object> VectorPayload(Vector3 vector)
    {
        return new List<object> { vector.x, vector.y, vector.z };
    }

    private static bool IsFiniteVector(Vector3 vector)
    {
        return IsFinite(vector.x) && IsFinite(vector.y) && IsFinite(vector.z);
    }

    private static bool IsFinite(float value)
    {
        return !float.IsNaN(value) && !float.IsInfinity(value);
    }
}
