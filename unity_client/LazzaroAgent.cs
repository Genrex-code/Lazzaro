using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using Newtonsoft.Json.Linq;
using UnityEngine;
using VRM;

[RequireComponent(typeof(ApiClient))]
[RequireComponent(typeof(PerceptionSystem))]
[RequireComponent(typeof(MovementController))]
public class LazzaroAgent : MonoBehaviour
{
    private ApiClient api;
    private PerceptionSystem percepcion;
    private MovementController movimiento;
    private VRMBlendShapeProxy blendShapeProxy;
    private readonly LinkedList<EventoEntrante> colaEventos = new LinkedList<EventoEntrante>();
    private bool procesandoCola = false;
    private List<Collider> ultimosObjetosPercibidos = new List<Collider>();
    private bool movimientoTerminadoPendiente = false;
    private bool ultimoMovimientoExitoso = false;
    private string ultimoResultadoMovimiento = "";
    private string ultimoObjetoNavegacion = "ninguno";
    private string ultimaAccionMovimiento = "navegar_a_objeto";
    private Dictionary<string, object> ultimosParametrosMovimiento = new Dictionary<string, object>();

    void Awake()
    {
        api = GetComponent<ApiClient>();
        percepcion = GetComponent<PerceptionSystem>();
        movimiento = GetComponent<MovementController>();
        blendShapeProxy = GetComponentInChildren<VRMBlendShapeProxy>();
        movimiento.OnMovimientoCompletado += ManejarMovimientoCompletado;
    }

    void OnDestroy()
    {
        if (movimiento != null)
        {
            movimiento.OnMovimientoCompletado -= ManejarMovimientoCompletado;
        }
    }

    void Start()
    {
        ultimosObjetosPercibidos = percepcion.ObtenerObjetosInteractuables();
        var detalles = percepcion.EscanearEntorno();
        detalles["mensaje"] = ConstruirManualEspacial();
        EnviarEvento("inicio_simulacion", detalles);
        StartCoroutine(LatidoCognitivo());
    }

    private IEnumerator LatidoCognitivo()
    {
        while (true)
        {
            yield return new WaitForSeconds(1.0f);

            if (movimiento.IsMoving)
            {
                continue;
            }

            if (procesandoCola && colaEventos.Count >= 3)
            {
                Debug.Log("Backpressure activo. Se omite un escaneo pasivo para no saturar la cola.");
                continue;
            }

            List<Collider> objetosActuales = percepcion.ObtenerObjetosInteractuables();
            bool cambioSignificativo = percepcion.CambioSignificativo(
                objetosActuales,
                ultimosObjetosPercibidos
            );

            if (!cambioSignificativo && !movimientoTerminadoPendiente)
            {
                ultimosObjetosPercibidos = objetosActuales;
                continue;
            }

            var detallesVision = percepcion.EscanearEntorno();
            detallesVision["mensaje"] = ConstruirManualEspacial();

            if (movimientoTerminadoPendiente)
            {
                detallesVision["movimiento_finalizado"] = true;
                detallesVision["movimiento_exito"] = ultimoMovimientoExitoso;
                detallesVision["mensaje_movimiento"] = ultimoResultadoMovimiento;
                movimientoTerminadoPendiente = false;
            }

            EnviarEvento("actualizacion_estado", detallesVision);
            ultimosObjetosPercibidos = objetosActuales;
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
        if (respuesta == null || respuesta.accion == null)
        {
            return;
        }

        AplicarExpresionFacial(respuesta.accion.emocion_facial);

        if (string.IsNullOrEmpty(respuesta.accion.nombre) || respuesta.accion.nombre == "none")
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
            case "navegar_a_objeto":
                EjecutarNavegarAObjeto(respuesta.accion.parametros);
                break;
            case "navegar_a_posicion":
                EjecutarNavegarAPosicion(respuesta.accion.parametros);
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
            + "REGLA DE NAVEGACION: Para desplazarte hacia un objeto visible usa navegar_a_objeto "
            + "con el parametro nombre_objeto exacto o aproximado. "
            + "REGLA INTERACCION: Si ya estas cerca del objeto objetivo, usa interactuar_objeto_3d.";
    }

    private void EjecutarNavegarAObjeto(JObject parametros)
    {
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "";
        if (string.IsNullOrWhiteSpace(objeto))
        {
            ReportarResultadoAccion(
                "navegar_a_objeto",
                false,
                "La accion navegar_a_objeto no incluyo el parametro nombre_objeto."
            );
            return;
        }

        ultimoObjetoNavegacion = objeto;
        ultimaAccionMovimiento = "navegar_a_objeto";
        ultimosParametrosMovimiento = new Dictionary<string, object>
        {
            { "nombre_objeto", objeto },
        };
        movimiento.NavegarAObjeto(objeto);
    }

    private void EjecutarNavegarAPosicion(JObject parametros)
    {
        if (
            !TryLeerFloat(parametros, "x", out float x)
            || !TryLeerFloat(parametros, "y", out float y)
            || !TryLeerFloat(parametros, "z", out float z)
        )
        {
            ReportarResultadoAccion(
                "navegar_a_posicion",
                false,
                "La accion navegar_a_posicion requiere parametros numericos x, y, z."
            );
            return;
        }

        Vector3 destino = new Vector3(x, y, z);
        ultimoObjetoNavegacion = $"posicion({x:0.##}, {y:0.##}, {z:0.##})";
        ultimaAccionMovimiento = "navegar_a_posicion";
        ultimosParametrosMovimiento = new Dictionary<string, object>
        {
            { "x", Math.Round(x, 2) },
            { "y", Math.Round(y, 2) },
            { "z", Math.Round(z, 2) },
        };

        movimiento.NavegarAPosicion(destino, ultimoObjetoNavegacion);
    }

    private void ManejarMovimientoCompletado(bool exito, string mensaje)
    {
        movimientoTerminadoPendiente = true;
        ultimoMovimientoExitoso = exito;
        ultimoResultadoMovimiento = mensaje;

        Dictionary<string, object> detallesMovimiento = new Dictionary<string, object>
        {
            { "movimiento_finalizado", true },
        };
        foreach (KeyValuePair<string, object> par in ultimosParametrosMovimiento)
        {
            detallesMovimiento[par.Key] = par.Value;
        }

        ReportarResultadoAccion(
            ultimaAccionMovimiento,
            exito,
            mensaje,
            detallesMovimiento,
            ultimoObjetoNavegacion
        );
    }

    private static bool TryLeerFloat(JObject parametros, string clave, out float valor)
    {
        valor = 0f;
        if (parametros == null || parametros[clave] == null)
        {
            return false;
        }

        return float.TryParse(
            parametros[clave].ToString(),
            NumberStyles.Float,
            CultureInfo.InvariantCulture,
            out valor
        );
    }

    private void AplicarExpresionFacial(string emocionFacial)
    {
        if (blendShapeProxy == null || string.IsNullOrWhiteSpace(emocionFacial))
        {
            return;
        }

        BlendShapePreset preset = ResolverPresetFacial(emocionFacial);
        BlendShapePreset[] presetsFaciales =
        {
            BlendShapePreset.Neutral,
            BlendShapePreset.Joy,
            BlendShapePreset.Sorrow,
            BlendShapePreset.Angry,
            BlendShapePreset.Fun,
        };

        foreach (BlendShapePreset facial in presetsFaciales)
        {
            float peso = facial == preset ? 1.0f : 0.0f;
            blendShapeProxy.ImmediatelySetValue(BlendShapeKey.CreateFromPreset(facial), peso);
        }
    }

    private static BlendShapePreset ResolverPresetFacial(string emocionFacial)
    {
        string valor = emocionFacial.Trim().ToLowerInvariant();
        valor = valor.Replace("fcl_all_", "");

        switch (valor)
        {
            case "joy":
                return BlendShapePreset.Joy;
            case "sorrow":
                return BlendShapePreset.Sorrow;
            case "angry":
                return BlendShapePreset.Angry;
            case "fun":
                return BlendShapePreset.Fun;
            case "neutral":
            default:
                return BlendShapePreset.Neutral;
        }
    }

    private void EjecutarInteraccion(JObject parametros)
    {
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        string tipo = parametros?["tipo_accion"]?.ToString() ?? "mirar";
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
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        Debug.Log($"Lazzaro intenta agarrar {objeto}.");
        ReportarResultadoAccion("agarrar_objeto", true, $"Objeto '{objeto}' agarrado.", null, objeto);
    }

    private void EjecutarSoltar(JObject parametros)
    {
        float fuerza = parametros?["fuerza_lanzamiento"]?.Value<float>() ?? 0f;
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
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        float vx = parametros?["vector_x"]?.Value<float>() ?? 0f;
        float vy = parametros?["vector_y"]?.Value<float>() ?? 0f;
        float vz = parametros?["vector_z"]?.Value<float>() ?? 0f;
        float fuerza = parametros?["fuerza"]?.Value<float>() ?? 0f;
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
        string texto = parametros?["texto_a_comunicar"]?.ToString() ?? "...";
        Debug.Log($"Lazzaro dice: {texto}");
        ReportarResultadoAccion(
            "hablar",
            true,
            "Mensaje emitido correctamente.",
            new Dictionary<string, object> { { "texto_a_comunicar", texto } }
        );
    }
}
