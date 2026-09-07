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
}

[Serializable]
public class AccionSaliente
{
    public string id_accion;
    public string nombre;
    public string pensamiento_privado;
    public string razonamiento;
    public JObject parametros;
    public bool requiere_confirmacion;
    public string estado_ejecucion;
    public string id_transaccion_origen;
}

[Serializable]
public class RespuestaAPI
{
    public string status;
    public string mensaje;
    public string id_transaccion;
    public string timestamp_servidor;
    public AccionSaliente accion;
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
