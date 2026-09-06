using System;
using System.Collections.Generic;
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
