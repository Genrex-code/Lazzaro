using System;
using Newtonsoft.Json.Linq;

/// <summary>
/// Comparaciones puras para preservar la identidad de una accion y ligar la
/// decision local de confirmacion con la transicion posterior de su lifecycle.
///
/// El estado pendiente y la decision de ejecutar siguen perteneciendo a
/// LazzaroAgent. Esta clase no deserializa wire ni crea identidad nueva.
/// </summary>
public static class ActionLifecycleContinuityV1
{
    public static bool TryResolveNewActionOrigin(
        EventoEntrante request,
        AccionSaliente responseAction,
        out string canonicalOrigin
    )
    {
        canonicalOrigin = null;
        if (
            request == null
            || responseAction == null
            || string.IsNullOrWhiteSpace(request.id_transaccion)
        )
        {
            return false;
        }

        string expectedOrigin = request.id_transaccion;
        if (!OriginMatchesOrIsOmitted(responseAction.id_transaccion_origen, expectedOrigin))
        {
            return false;
        }

        canonicalOrigin = expectedOrigin;
        return true;
    }

    public static bool TryGetRequestedConfirmation(
        EventoEntrante request,
        out string requestedActionId,
        out bool approved
    )
    {
        requestedActionId = null;
        approved = false;
        if (
            request == null
            || request.tipo_evento != "confirmacion_accion"
            || request.detalles == null
            || !request.detalles.TryGetValue("id_accion", out object rawActionId)
            || !(rawActionId is string actionId)
            || string.IsNullOrWhiteSpace(actionId)
            || !request.detalles.TryGetValue("approved", out object rawApproved)
            || !(rawApproved is bool approvedDecision)
        )
        {
            return false;
        }

        requestedActionId = actionId;
        approved = approvedDecision;
        return true;
    }

    public static bool IsValidConfirmationContinuation(
        string requestedActionId,
        bool approved,
        string responseStatus,
        AccionSaliente responseAction,
        string pendingActionId,
        string pendingOrigin,
        string pendingName,
        JObject pendingParameters,
        PerceptionAuthorityReferenceV1 pendingAuthority
    )
    {
        if (
            string.IsNullOrWhiteSpace(requestedActionId)
            || responseAction == null
            || string.IsNullOrWhiteSpace(pendingActionId)
            || string.IsNullOrWhiteSpace(pendingOrigin)
            || string.IsNullOrWhiteSpace(pendingName)
            || pendingParameters == null
            || !IsExpectedConfirmationLifecycle(
                approved,
                responseStatus,
                responseAction.estado_ejecucion
            )
            || !string.Equals(requestedActionId, pendingActionId, StringComparison.Ordinal)
            || !string.Equals(responseAction.id_accion, pendingActionId, StringComparison.Ordinal)
            || !string.Equals(responseAction.nombre, pendingName, StringComparison.Ordinal)
            || !OriginMatchesOrIsOmitted(responseAction.id_transaccion_origen, pendingOrigin)
            || responseAction.parametros == null
            || !JToken.DeepEquals(responseAction.parametros, pendingParameters)
            || !SameAuthority(responseAction.perception_authority, pendingAuthority)
        )
        {
            return false;
        }

        return true;
    }

    private static bool IsExpectedConfirmationLifecycle(
        bool approved,
        string responseStatus,
        string executionState
    )
    {
        return string.Equals(responseStatus, "ok", StringComparison.Ordinal)
            && string.Equals(
                executionState,
                approved ? "dispatched" : "rejected",
                StringComparison.Ordinal
            );
    }

    private static bool OriginMatchesOrIsOmitted(string receivedOrigin, string expectedOrigin)
    {
        return string.IsNullOrWhiteSpace(receivedOrigin)
            || string.Equals(receivedOrigin, expectedOrigin, StringComparison.Ordinal);
    }

    private static bool SameAuthority(
        PerceptionAuthorityReferenceV1 received,
        PerceptionAuthorityReferenceV1 pending
    )
    {
        if (received == null || pending == null)
        {
            return received == null && pending == null;
        }

        try
        {
            return JToken.DeepEquals(
                JToken.FromObject(received),
                JToken.FromObject(pending)
            );
        }
        catch (Exception)
        {
            return false;
        }
    }
}
