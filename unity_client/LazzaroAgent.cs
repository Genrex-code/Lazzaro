using System;
using System.Collections;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

[RequireComponent(typeof(ApiClient))]
[RequireComponent(typeof(PerceptionSystem))]
[RequireComponent(typeof(MovementController))]
public class LazzaroAgent : MonoBehaviour
{
    private ApiClient api;
    private PerceptionSystem percepcion;
    private MovementController movimiento;
    private readonly LinkedList<EventoEntrante> colaEventos = new LinkedList<EventoEntrante>();
    private bool procesandoCola = false;

    void Awake()
    {
        api = GetComponent<ApiClient>();
        percepcion = GetComponent<PerceptionSystem>();
        movimiento = GetComponent<MovementController>();
    }

    void Start()
    {
        var detalles = percepcion.EscanearEntorno();
        detalles["mensaje"] = ConstruirManualEspacial();
        EnviarEvento("inicio_simulacion", detalles);
        StartCoroutine(LatidoCognitivo());
    }

    private IEnumerator LatidoCognitivo()
    {
        while (true)
        {
            yield return new WaitForSeconds(3.0f);

            if (movimiento.IsMoving)
            {
                Debug.Log("Lazzaro esta concentrado moviendose. Se omite latido cognitivo.");
                continue;
            }

            if (procesandoCola && colaEventos.Count >= 3)
            {
                Debug.Log("Backpressure activo. Se omite un latido para no saturar la cola.");
                continue;
            }

            var detallesVision = percepcion.EscanearEntorno();
            detallesVision["mensaje"] = ConstruirManualEspacial();
            EnviarEvento("actualizacion_estado", detallesVision);
        }
    }

    public void EnviarEvento(
        string tipoEvento,
        Dictionary<string, object> detalles,
        string objetoInvolucrado = "ninguno",
        bool prioridadMaxima = false
    )
    {
        EventoEntrante evento = new EventoEntrante
        {
            tipo_evento = tipoEvento,
            id_transaccion = Guid.NewGuid().ToString(),
            objeto_involucrado = objetoInvolucrado,
            detalles = detalles ?? new Dictionary<string, object>()
        };

        if (prioridadMaxima)
        {
            colaEventos.AddFirst(evento);
        }
        else
        {
            colaEventos.AddLast(evento);
        }

        if (!procesandoCola)
        {
            StartCoroutine(ProcesarColaEventos());
        }
    }

    public void ReportarDolor(
        string causaDelDolor,
        string objetoGolpeado,
        string direccionEscape,
        float distanciaEscape,
        string accionFallida
    )
    {
        var detalles = percepcion.EscanearEntorno();
        detalles["mensaje_critico"] = causaDelDolor;
        detalles["estado_fisico"] = "bloqueado";
        detalles["direccion_escape"] = direccionEscape;
        detalles["distancia_escape"] = Math.Round(distanciaEscape, 2);
        detalles["velocidad_escape"] = "caminar";
        detalles["accion"] = accionFallida;
        detalles["exito"] = false;

        EnviarEvento("alerta_dolor_fisico", detalles, objetoGolpeado, true);
    }

    public void ReportarResultadoAccion(
        string nombreAccion,
        bool exito,
        string mensaje,
        Dictionary<string, object> detallesExtra = null,
        string objetoInvolucrado = "ninguno"
    )
    {
        var detalles = percepcion.EscanearEntorno();
        detalles["accion"] = nombreAccion;
        detalles["exito"] = exito;
        detalles["mensaje_resultado"] = mensaje;

        if (detallesExtra != null)
        {
            foreach (KeyValuePair<string, object> par in detallesExtra)
            {
                detalles[par.Key] = par.Value;
            }
        }

        EnviarEvento("resultado_accion", detalles, objetoInvolucrado);
    }

    private IEnumerator ProcesarColaEventos()
    {
        procesandoCola = true;
        while (colaEventos.Count > 0)
        {
            EventoEntrante evento = colaEventos.First.Value;
            colaEventos.RemoveFirst();

            yield return StartCoroutine(
                api.EnviarEvento(
                    evento,
                    ProcesarRespuestaDelCerebro,
                    mensajeError => Debug.LogWarning($"No se pudo enviar el evento {evento.tipo_evento}: {mensajeError}")
                )
            );
        }
        procesandoCola = false;
    }

    private void ProcesarRespuestaDelCerebro(RespuestaAPI respuesta)
    {
        if (respuesta == null)
        {
            return;
        }

        if (respuesta.accion == null || string.IsNullOrEmpty(respuesta.accion.nombre) || respuesta.accion.nombre == "none")
        {
            return;
        }

        if (respuesta.accion.requiere_confirmacion)
        {
            Debug.LogWarning($"Accion '{respuesta.accion.nombre}' requiere confirmacion. Se omite en Unity.");
            ReportarResultadoAccion(
                respuesta.accion.nombre,
                false,
                "La accion fue omitida porque requiere confirmacion manual."
            );
            return;
        }

        Debug.Log($"Pensamiento de Lazzaro: {respuesta.accion.pensamiento_privado}");

        switch (respuesta.accion.nombre)
        {
            case "moverse_entorno_3d":
                movimiento.EjecutarMovimiento(respuesta.accion.parametros);
                break;
            case "interactuar_objeto_3d":
                EjecutarInteraccion(respuesta.accion.parametros);
                break;
            case "agarrar_objeto":
                EjecutarAgarrar(respuesta.accion.parametros);
                break;
            case "soltar_objeto":
                EjecutarSoltar(respuesta.accion.parametros);
                break;
            case "aplicar_fuerza":
                EjecutarAplicarFuerza(respuesta.accion.parametros);
                break;
            case "hablar":
                EjecutarHablar(respuesta.accion.parametros);
                break;
            default:
                Debug.LogWarning($"No hay manejador Unity para la accion: {respuesta.accion.nombre}");
                ReportarResultadoAccion(
                    respuesta.accion.nombre,
                    false,
                    "Unity no tenia implementado un manejador para esta accion."
                );
                break;
        }
    }

    private static string ConstruirManualEspacial()
    {
        return "Objetivo: Explora el bosque y golpea el arbol mas cercano. "
            + "REGLAS DE NAVEGACION: 'adelante' aumenta Z (+Z), 'atras' disminuye Z (-Z), "
            + "'derecha' aumenta X (+X), 'izquierda' disminuye X (-X). "
            + "REGLA INTERACCION: Si tu posicion_lazzaro coincide con un objeto_visible "
            + "(margen de 2 unidades), detente y usa interactuar_objeto_3d.";
    }

    private void EjecutarInteraccion(JObject parametros)
    {
        string objeto = parametros["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        string tipo = parametros["tipo_accion"]?.ToString() ?? "mirar";
        Debug.Log($"Interactuando con {objeto} mediante '{tipo}'.");
        ReportarResultadoAccion(
            "interactuar_objeto_3d",
            true,
            $"Interaccion '{tipo}' ejecutada sobre '{objeto}'.",
            new Dictionary<string, object>
            {
                { "tipo_accion", tipo },
            },
            objeto
        );
    }

    private void EjecutarAgarrar(JObject parametros)
    {
        string objeto = parametros["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        Debug.Log($"Lazzaro intenta agarrar {objeto}.");
        ReportarResultadoAccion("agarrar_objeto", true, $"Objeto '{objeto}' agarrado.", null, objeto);
    }

    private void EjecutarSoltar(JObject parametros)
    {
        float fuerza = parametros["fuerza_lanzamiento"]?.Value<float>() ?? 0f;
        Debug.Log($"Lazzaro suelta el objeto con fuerza {fuerza}.");
        ReportarResultadoAccion(
            "soltar_objeto",
            true,
            $"Objeto soltado con fuerza {fuerza}.",
            new Dictionary<string, object> { { "fuerza_lanzamiento", Math.Round(fuerza, 2) } }
        );
    }

    private void EjecutarAplicarFuerza(JObject parametros)
    {
        string objeto = parametros["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        float vx = parametros["vector_x"]?.Value<float>() ?? 0f;
        float vy = parametros["vector_y"]?.Value<float>() ?? 0f;
        float vz = parametros["vector_z"]?.Value<float>() ?? 0f;
        float fuerza = parametros["fuerza"]?.Value<float>() ?? 0f;
        Debug.Log($"Aplicando fuerza {fuerza} a '{objeto}' en direccion ({vx}, {vy}, {vz}).");
        ReportarResultadoAccion(
            "aplicar_fuerza",
            true,
            $"Fuerza aplicada a '{objeto}'.",
            new Dictionary<string, object>
            {
                { "vector_x", Math.Round(vx, 2) },
                { "vector_y", Math.Round(vy, 2) },
                { "vector_z", Math.Round(vz, 2) },
                { "fuerza", Math.Round(fuerza, 2) },
            },
            objeto
        );
    }

    private void EjecutarHablar(JObject parametros)
    {
        string texto = parametros["texto_a_comunicar"]?.ToString() ?? "...";
        Debug.Log($"Lazzaro dice: {texto}");
        ReportarResultadoAccion(
            "hablar",
            true,
            "Mensaje emitido correctamente.",
            new Dictionary<string, object> { { "texto", texto } }
        );
    }
}
