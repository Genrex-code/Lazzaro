using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

[Serializable]
public class EventoEntrante
{
    public string origen = "unity";
    public string tipo_evento;
    public string id_transaccion;
    public string objeto_involucrado = "ninguno";
    public Dictionary<string, object> detalles;
    public int? request_identity_version;
    public string request_fingerprint_sha256;
}

[Serializable]
public class ReconciliacionTransaccionV1
{
    public int version = 1;
    public string id_transaccion;
    public string origen;
    public string tipo_evento;
    public int? request_identity_version;
    public string request_fingerprint_sha256;
}

[Serializable]
public class ReconciliacionTransaccionRespuestaV1
{
    public int version;
    public string id_transaccion;
    public string resolution;
    public string directive;
    public string receipt_kind;
    public string receipt_status;
    public AccionSaliente accion;
}

[Serializable]
public class AccionSaliente
{
    public string id_accion;
    public string nombre;
    public JObject parametros;
    public bool requiere_confirmacion;
    public string estado_ejecucion;
    public string id_transaccion_origen;
    public PerceptionAuthorityReferenceV1 perception_authority;

    public AccionSaliente()
    {
        estado_ejecucion = "legacy_unknown";
    }
}

[Serializable]
public class TargetReferenceV1
{
    public int contract_version;
    public string producer_instance_id;
    public string stream_id;
    public string producer_epoch_id;
    public string logical_world_id;
    public string producer_capture_id;
    public long generation;
    public string producer_object_id;
    public string runtime_object_id;
}

[Serializable]
public class PerceptionAuthorityReferenceV1
{
    public int version;
    public string authority_decision_id;
    public string id_accion;
    public string authority_policy_id;
    public string freshness_policy_id;
    public string generation_binding_mode;
    public string producer_capture_id;
    public long generation;
    public TargetReferenceV1 target;
    public string action_name;
    public string canonical_parameters_json;
    public string interaction_type;
    public string safety_disposition;
    public string safety_reason_code;
    public string action_binding_sha256;
}

[Serializable]
public class RespuestaAPI
{
    public int wire_version;
    public string status;
    public string mensaje;
    public string id_transaccion;
    public string timestamp_servidor;
    public AccionSaliente accion;
    public DecisionPublica decision;
}

[Serializable]
public class DecisionPublica
{
    public int version;
    public string disposition;
    public string reason_code;
}

[Serializable]
public class PublicErrorDetail
{
    [JsonProperty("code")]
    public string code;

    // Se deserializa por compatibilidad de wire, pero nunca es authority de UX.
    [JsonProperty("message")]
    public string message;

    [JsonProperty("correlation_id")]
    public string correlation_id;
}

[Serializable]
public class PublicErrorEnvelope
{
    [JsonProperty("version")]
    public int version;

    [JsonProperty("status")]
    public string status;

    [JsonProperty("error")]
    public PublicErrorDetail error;
}

[Serializable]
public class ApiFailure
{
    public string Code;
    public string Message;
    public string CorrelationId;
    public long HttpStatus;
    public bool TransportFailure;

    public string ToSafeString()
    {
        string correlation = string.IsNullOrWhiteSpace(CorrelationId)
            ? ""
            : $" correlation_id={CorrelationId}";
        return $"{Code}: {Message} http_status={HttpStatus}{correlation}";
    }
}
