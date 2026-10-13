using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEngine;
using TMPro;    // Corregido para UI

[RequireComponent(typeof(ApiClient))]
[RequireComponent(typeof(PerceptionSystem))]
[RequireComponent(typeof(MovementController))]
[RequireComponent(typeof(SomatoSensorySystem))]
[RequireComponent(typeof(ObjectMaterializationController))]
[RequireComponent(typeof(ExactTargetResolver))]
public class LazzaroAgent : MonoBehaviour
{
    private ApiClient api;
    private PerceptionSystem percepcion;
    private MovementController movimiento;
    private ObjectMaterializationController materializacion;
    private ExactTargetResolver exactTargetResolver;
    private readonly LinkedList<EventoEntrante> colaEventos = new LinkedList<EventoEntrante>();
    private readonly Queue<string> colaTareasUsuario = new Queue<string>();
    private bool procesandoCola = false;
    private bool tareaUsuarioEnCurso = false;
    private List<Collider> ultimosObjetosPercibidos = new List<Collider>();
    private bool movimientoTerminadoPendiente = false;
    private bool ultimoMovimientoExitoso = false;
    private string ultimoResultadoMovimiento = "";
    private string tareaUsuarioActual = "";
    private string tareaUsuarioActualIdTransaccion = "";
    private readonly Dictionary<string, ContextoAccion> accionesMovimientoPendientes =
        new Dictionary<string, ContextoAccion>();
    private readonly Dictionary<string, ContextoAccion> accionesEsperandoAutorizacion =
        new Dictionary<string, ContextoAccion>();
    private readonly Dictionary<string, EventoEntrante> eventosEnVuelo =
        new Dictionary<string, EventoEntrante>();
    private readonly Dictionary<string, ContextoAccion> accionesEsperandoInicio =
        new Dictionary<string, ContextoAccion>();
    private readonly HashSet<string> accionesEntregadasAEjecucion =
        new HashSet<string>(StringComparer.Ordinal);
    private UnityDeliveryOutboxV1 deliveryOutbox;
    private const int MaxIntentosDelivery = 3;
    private const int MaxIntentosReconciliacion = 3;
    private const float DistanciaMaximaInteraccion = 2.0f;
    private const float DistanciaMaximaAgarrar = 1.5f;
    private const int MaxTareasUsuarioEnCola = 8;
    private static readonly Regex SeparadorTareasRegex = new Regex(
        @"\s+(?:y\s+luego|luego|despues|después|finalmente)\s+",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant
    );

    private sealed class ContextoAccion
    {
        public readonly string IdAccion;
        public readonly string IdTransaccionOrigen;
        public readonly string Nombre;
        public readonly JObject Parametros;
        public readonly Dictionary<string, object> DetallesResultado;
        public readonly string ObjetoInvolucrado;
        public readonly PerceptionAuthorityReferenceV1 PerceptionAuthority;

        public ContextoAccion(
            string idAccion,
            string idTransaccionOrigen,
            string nombre,
            JObject parametros,
            PerceptionAuthorityReferenceV1 perceptionAuthority = null
        )
        {
            IdAccion = idAccion;
            IdTransaccionOrigen = idTransaccionOrigen;
            Nombre = nombre;
            Parametros = parametros;
            DetallesResultado = new Dictionary<string, object>();
            ObjetoInvolucrado = "ninguno";
            PerceptionAuthority = perceptionAuthority;
        }

        private ContextoAccion(
            string idAccion,
            string idTransaccionOrigen,
            string nombre,
            JObject parametros,
            Dictionary<string, object> detallesResultado,
            string objetoInvolucrado,
            PerceptionAuthorityReferenceV1 perceptionAuthority
        )
        {
            IdAccion = idAccion;
            IdTransaccionOrigen = idTransaccionOrigen;
            Nombre = nombre;
            Parametros = parametros;
            DetallesResultado = detallesResultado;
            ObjetoInvolucrado = objetoInvolucrado;
            PerceptionAuthority = perceptionAuthority;
        }

        public ContextoAccion ConDetalles(
            Dictionary<string, object> detalles,
            string objetoInvolucrado = "ninguno"
        )
        {
            return new ContextoAccion(
                IdAccion,
                IdTransaccionOrigen,
                Nombre,
                Parametros,
                detalles ?? new Dictionary<string, object>(),
                string.IsNullOrWhiteSpace(objetoInvolucrado) ? "ninguno" : objetoInvolucrado,
                PerceptionAuthority
            );
        }
    }

    public string AccionMovimientoActiva
    {
        get
        {
            foreach (ContextoAccion contexto in accionesMovimientoPendientes.Values)
            {
                return contexto.Nombre;
            }
            return "accion_fisica_desconocida";
        }
    }

    [Header("Interfaz Visual (Nubes)")]
    public TextMeshProUGUI textoPensamientoUI;
    public TextMeshProUGUI textoVozUI;

    [Header("Consola de Dios")]
    public TMP_InputField inputComandoDios;

    [Header("Debug y Consola")]
    public bool mostrarLogsCognitivos = false;

    void Awake()
    {
        api = GetComponent<ApiClient>();
        percepcion = GetComponent<PerceptionSystem>();
        movimiento = GetComponent<MovementController>();
        materializacion = GetComponent<ObjectMaterializationController>();
        exactTargetResolver = GetComponent<ExactTargetResolver>();
        movimiento.OnMovimientoCompletado += ManejarMovimientoCompletado;
        try
        {
            deliveryOutbox = new UnityDeliveryOutboxV1(
                Path.Combine(Application.persistentDataPath, "lazzaro_delivery_outbox_v1.json")
            );
        }
        catch (Exception)
        {
            deliveryOutbox = null;
            Debug.LogError("No se pudo abrir el outbox durable; delivery queda bloqueado.");
        }
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
        RestaurarDeliveryPendiente();
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
                if (mostrarLogsCognitivos) Debug.Log("Lazzaro esta concentrado moviendose. Se omite escaneo pasivo.");
                continue;
            }

            if (procesandoCola && colaEventos.Count >= 3)
            {
                if (mostrarLogsCognitivos) Debug.Log("Backpressure activo. Se omite un escaneo pasivo para no saturar la cola.");
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

            if (movimientoTerminadoPendiente)
            {
                detallesVision["movimiento_finalizado"] = true;
                detallesVision["movimiento_exito"] = ultimoMovimientoExitoso;
                detallesVision["mensaje_movimiento"] = ultimoResultadoMovimiento;
                movimientoTerminadoPendiente = false;
            }

            // Según el plan de estabilización, esto es telemetría pasiva.
            EnviarEvento("actualizacion_estado", detallesVision);
            ultimosObjetosPercibidos = objetosActuales;
        }
    }

    public void EnviarComandoUsuario()
    {
        if (inputComandoDios == null || string.IsNullOrWhiteSpace(inputComandoDios.text))
        {
            return;
        }

        string comandoTexto = inputComandoDios.text;
        inputComandoDios.text = "";

        Debug.Log($"[DIOS]: {comandoTexto}");

        EncolarComandoUsuario(comandoTexto);
    }

    private void EncolarComandoUsuario(string comandoTexto)
    {
        List<string> tareas = DividirComandoEnTareas(comandoTexto);
        if (tareas.Count == 0)
        {
            return;
        }

        if (tareaUsuarioEnCurso || colaTareasUsuario.Count > 0)
        {
            CancelarFilaTareasUsuario("Nuevo comando del usuario recibido.");
        }

        int agregadas = 0;
        foreach (string tarea in tareas)
        {
            if (agregadas >= MaxTareasUsuarioEnCola)
            {
                Debug.LogWarning($"Se omitieron subtareas extra; limite actual: {MaxTareasUsuarioEnCola}.");
                break;
            }

            colaTareasUsuario.Enqueue(tarea);
            agregadas++;
        }

        if (mostrarLogsCognitivos && colaTareasUsuario.Count > 1)
        {
            Debug.Log($"Fila de tareas creada con {colaTareasUsuario.Count} pasos.");
        }

        IniciarSiguienteTareaUsuario();
    }

    private void IniciarSiguienteTareaUsuario()
    {
        if (tareaUsuarioEnCurso || colaTareasUsuario.Count == 0)
        {
            return;
        }

        tareaUsuarioActual = colaTareasUsuario.Dequeue();
        tareaUsuarioEnCurso = true;

        if (mostrarLogsCognitivos)
        {
            Debug.Log($"Ejecutando subtarea: {tareaUsuarioActual}");
        }

        var detalles = new Dictionary<string, object>
        {
            { "mensaje", tareaUsuarioActual },
            { "secuencia_usuario", true },
            { "tareas_restantes", colaTareasUsuario.Count }
        };

        tareaUsuarioActualIdTransaccion = EnviarEvento("comando_usuario", detalles, "usuario");
    }

    private void CompletarTareaUsuarioActual(
        string idTransaccionOrigen,
        bool exito,
        string mensaje
    )
    {
        if (
            !tareaUsuarioEnCurso
            || string.IsNullOrWhiteSpace(idTransaccionOrigen)
            || idTransaccionOrigen != tareaUsuarioActualIdTransaccion
        )
        {
            return;
        }

        string tareaCompletada = tareaUsuarioActual;
        tareaUsuarioActual = "";
        tareaUsuarioActualIdTransaccion = "";
        tareaUsuarioEnCurso = false;

        if (!exito)
        {
            Debug.LogWarning($"Subtarea fallida: {tareaCompletada}. Motivo: {mensaje}");
            CancelarFilaTareasUsuario("Se detuvo la secuencia por fallo en una subtarea.");
            return;
        }

        if (mostrarLogsCognitivos)
        {
            Debug.Log($"Subtarea completada: {tareaCompletada}");
        }

        if (colaTareasUsuario.Count > 0)
        {
            StartCoroutine(IniciarSiguienteTareaEnSiguienteFrame());
        }
    }

    private IEnumerator IniciarSiguienteTareaEnSiguienteFrame()
    {
        yield return null;
        IniciarSiguienteTareaUsuario();
    }

    private void CancelarFilaTareasUsuario(string motivo)
    {
        int pendientes = colaTareasUsuario.Count;
        colaTareasUsuario.Clear();
        tareaUsuarioEnCurso = false;
        tareaUsuarioActual = "";
        tareaUsuarioActualIdTransaccion = "";

        if (pendientes > 0 || mostrarLogsCognitivos)
        {
            Debug.Log($"Fila de tareas cancelada. {motivo}");
        }
    }

    private static List<string> DividirComandoEnTareas(string comandoTexto)
    {
        List<string> tareas = new List<string>();
        if (string.IsNullOrWhiteSpace(comandoTexto))
        {
            return tareas;
        }

        string[] partes = SeparadorTareasRegex.Split(comandoTexto.Trim());
        string verboContexto = "";

        foreach (string parte in partes)
        {
            string tarea = LimpiarSubtarea(parte);
            if (string.IsNullOrWhiteSpace(tarea))
            {
                continue;
            }

            if (TieneVerboFisico(tarea))
            {
                verboContexto = InferirVerboContexto(tarea);
                tareas.Add(tarea);
                continue;
            }

            if (!string.IsNullOrEmpty(verboContexto))
            {
                tareas.Add($"{verboContexto} {tarea}");
                continue;
            }

            tareas.Add(tarea);
        }

        return tareas;
    }

    private static string LimpiarSubtarea(string texto)
    {
        if (string.IsNullOrWhiteSpace(texto))
        {
            return "";
        }

        string limpio = texto.Trim();
        limpio = Regex.Replace(limpio, @"^(primero|despues|después|luego|finalmente)\s+", "", RegexOptions.IgnoreCase);
        limpio = Regex.Replace(limpio, @"\s+(por\s+favor|porfavor|porfa)\s*[\.\?!]*$", "", RegexOptions.IgnoreCase);
        return limpio.Trim(' ', '.', ',', ';');
    }

    private static bool TieneVerboFisico(string texto)
    {
        string normalizado = NormalizarTextoSimple(texto);
        string[] marcadores =
        {
            "camina",
            "mueve",
            "mover",
            "moverte",
            "muevete",
            "ve a",
            "acercate",
            "alejate",
            "dirigete",
            "navega",
            "inspecciona",
            "interactua",
            "toca",
            "agarra",
            "suelta",
            "usa",
            "activa",
            "golpea",
            "gira",
            "girar",
            "rota",
            "rotar",
            "voltea",
            "reorienta",
            "reorientarte",
            "mira alrededor"
        };

        foreach (string marcador in marcadores)
        {
            if (normalizado.Contains(marcador))
            {
                return true;
            }
        }

        return false;
    }

    private static string InferirVerboContexto(string texto)
    {
        string normalizado = NormalizarTextoSimple(texto);
        if (
            normalizado.Contains("gira")
            || normalizado.Contains("rotar")
            || normalizado.Contains("rota")
            || normalizado.Contains("voltea")
            || normalizado.Contains("reorient")
            || normalizado.Contains("mira alrededor")
        )
        {
            return "gira";
        }

        if (
            normalizado.Contains("inspecciona")
            || normalizado.Contains("interactua")
            || normalizado.Contains("toca")
            || normalizado.Contains("usa")
            || normalizado.Contains("activa")
            || normalizado.Contains("golpea")
        )
        {
            return "inspecciona";
        }

        if (normalizado.Contains("alejate"))
        {
            return "alejate de";
        }

        if (normalizado.Contains("agarra"))
        {
            return "agarra";
        }

        if (normalizado.Contains("suelta"))
        {
            return "suelta";
        }

        return "camina";
    }

    private static string NormalizarTextoSimple(string texto)
    {
        return (texto ?? "")
            .ToLowerInvariant()
            .Replace("á", "a")
            .Replace("é", "e")
            .Replace("í", "i")
            .Replace("ó", "o")
            .Replace("ú", "u")
            .Replace("ü", "u");
    }

    public string EnviarEvento(
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

        if (
            deliveryOutbox == null
            || !UnityDeliveryIdentityV1.TryPrepare(evento)
            || !deliveryOutbox.Enqueue(evento)
        )
        {
            Debug.LogError("El evento no pudo quedar durable; no se enviara.");
            return "";
        }

        EncolarEventoExistente(evento, prioridadMaxima);
        return evento.id_transaccion;
    }

    private void EncolarEventoExistente(EventoEntrante evento, bool prioridadMaxima)
    {
        if (evento == null || string.IsNullOrWhiteSpace(evento.id_transaccion)) return;
        foreach (EventoEntrante pendiente in colaEventos)
        {
            if (pendiente.id_transaccion == evento.id_transaccion) return;
        }
        if (eventosEnVuelo.ContainsKey(evento.id_transaccion)) return;

        if (prioridadMaxima) colaEventos.AddFirst(evento);
        else colaEventos.AddLast(evento);
        if (!procesandoCola) StartCoroutine(ProcesarColaEventos());
    }

    private void RestaurarDeliveryPendiente()
    {
        if (deliveryOutbox == null) return;
        foreach (DeliveryOutboxRecordV1 record in deliveryOutbox.SnapshotPending())
        {
            if (record.request == null) continue;
            if (
                record.state == UnityDeliveryOutboxV1.ExecutionHandoff
                || record.state == UnityDeliveryOutboxV1.Paused
                || record.state == UnityDeliveryOutboxV1.Terminal
            )
            {
                continue;
            }
            // Tras un reinicio incluso QUEUED es ambiguo: reconciliar primero.
            eventosEnVuelo[record.request.id_transaccion] = record.request;
            StartCoroutine(ReconciliarEvento(record.request));
        }
    }

    public void ConfirmarAccion(string idAccion, bool approved)
    {
        if (
            string.IsNullOrWhiteSpace(idAccion)
            || !accionesEsperandoAutorizacion.ContainsKey(idAccion)
        )
        {
            Debug.LogWarning($"No existe una acción pendiente exacta para confirmar: {idAccion}");
            return;
        }
        EnviarEvento(
            "confirmacion_accion",
            new Dictionary<string, object>
            {
                { "id_accion", idAccion },
                { "approved", approved }
            }
        );
    }

    private void EncolarInicioEjecucion(ContextoAccion contexto)
    {
        string transactionId = EnviarEvento(
            "inicio_ejecucion_accion",
            new Dictionary<string, object>
            {
                { "id_accion", contexto.IdAccion },
                { "id_transaccion_origen", contexto.IdTransaccionOrigen }
            },
            prioridadMaxima: true
        );
        if (!string.IsNullOrWhiteSpace(transactionId))
        {
            accionesEsperandoInicio[transactionId] = contexto;
        }
    }

    public void ReportarDolor(
        string causaDelDolor,
        string objetoGolpeado,
        string direccionEscape,
        float distanciaEscape,
        string accionFallida,
        Dictionary<string, object> detallesExtra = null
    )
    {
        CancelarFilaTareasUsuario("Interrupcion por dolor fisico.");

        var detalles = percepcion.EscanearEntorno();
        detalles["mensaje_critico"] = causaDelDolor;
        detalles["estado_fisico"] = "bloqueado";
        detalles["direccion_escape"] = direccionEscape;
        detalles["distancia_escape"] = Math.Round(distanciaEscape, 2);
        detalles["velocidad_escape"] = "caminar";
        detalles["accion"] = accionFallida;
        detalles["exito"] = false;

        AgregarDetallesExtra(detalles, detallesExtra);

        EnviarEvento("alerta_dolor_fisico", detalles, objetoGolpeado, true);
    }

    private void CerrarTareaUsuarioActualNeutral(
        string idTransaccionOrigen,
        string mensaje
    )
    {
        if (
            !tareaUsuarioEnCurso
            || string.IsNullOrWhiteSpace(idTransaccionOrigen)
            || idTransaccionOrigen != tareaUsuarioActualIdTransaccion
        )
        {
            return;
        }
        tareaUsuarioActual = "";
        tareaUsuarioActualIdTransaccion = "";
        tareaUsuarioEnCurso = false;
        Debug.Log($"Subtarea cerrada sin resultado de ejecución: {mensaje}");
        if (colaTareasUsuario.Count > 0)
        {
            StartCoroutine(IniciarSiguienteTareaEnSiguienteFrame());
        }
    }

    private void ReportarResultadoAccion(
        ContextoAccion contexto,
        bool exito,
        string mensaje,
        Dictionary<string, object> detallesExtra = null,
        string objetoInvolucrado = "ninguno"
    )
    {
        var detalles = percepcion.EscanearEntorno();
        detalles["id_accion"] = contexto.IdAccion;
        detalles["id_transaccion_origen"] = contexto.IdTransaccionOrigen;
        detalles["accion"] = contexto.Nombre;
        detalles["exito"] = exito;
        detalles["mensaje_resultado"] = mensaje;

        AgregarDetallesExtra(detalles, contexto.DetallesResultado);
        AgregarDetallesExtra(detalles, detallesExtra);

        string outcomeTransaction = EnviarEvento(
            "resultado_accion",
            detalles,
            objetoInvolucrado
        );
        if (!string.IsNullOrWhiteSpace(outcomeTransaction))
        {
            // El outcome ya esta durable en el outbox antes de retirar el handoff.
            deliveryOutbox?.CompleteExecutionHandoffForAction(contexto.IdAccion);
        }
        CompletarTareaUsuarioActual(contexto.IdTransaccionOrigen, exito, mensaje);
    }

    private IEnumerator ProcesarColaEventos()
    {
        procesandoCola = true;
        while (colaEventos.Count > 0)
        {
            EventoEntrante evento = colaEventos.First.Value;
            colaEventos.RemoveFirst();
            if (deliveryOutbox == null || !deliveryOutbox.MarkSending(evento.id_transaccion))
            {
                Debug.LogError("Delivery sin registro durable; request bloqueado.");
                continue;
            }
            eventosEnVuelo[evento.id_transaccion] = evento;

            yield return StartCoroutine(
                api.EnviarEvento(
                    evento,
                    respuesta => ProcesarRespuestaTransporte(evento, respuesta),
                    failure => ProcesarFalloTransporte(evento, failure)
                )
            );
        }
        procesandoCola = false;
    }

    private static bool RequiereReceiptDurable(string tipoEvento)
    {
        return tipoEvento == "confirmacion_accion"
            || tipoEvento == "inicio_ejecucion_accion"
            || tipoEvento == "resultado_accion";
    }

    private void ProcesarRespuestaTransporte(EventoEntrante evento, RespuestaAPI respuesta)
    {
        if (RequiereReceiptDurable(evento.tipo_evento))
        {
            // Un 2xx no es ACK semantico para lifecycle/outcome.
            StartCoroutine(ReconciliarEvento(evento));
            return;
        }
        ProcesarRespuestaDelCerebro(evento.id_transaccion, respuesta);
        deliveryOutbox?.Complete(evento.id_transaccion);
    }

    private void ProcesarFalloTransporte(EventoEntrante evento, ApiFailure failure)
    {
        string code = failure != null ? failure.Code : "TRANSPORT_FAILURE";
        Debug.LogWarning(
            $"No se pudo enviar el evento {evento.tipo_evento}: "
            + (failure != null ? failure.ToSafeString() : code)
        );
        if (
            code == "TRANSACTION_CONFLICT"
            || code == "TURN_ABORTED"
            || code == "TURN_PROCESSING_FAILED"
            || code == "VALIDATION_ERROR"
        )
        {
            deliveryOutbox?.MarkTerminal(evento.id_transaccion, code);
            eventosEnVuelo.Remove(evento.id_transaccion);
            return;
        }
        deliveryOutbox?.MarkUnknown(evento.id_transaccion, code);
        StartCoroutine(ReconciliarEvento(evento));
    }

    private IEnumerator ReconciliarEvento(EventoEntrante evento)
    {
        if (
            deliveryOutbox == null
            || evento == null
            || !deliveryOutbox.MarkReconciling(evento.id_transaccion)
        )
        {
            yield break;
        }

        ReconciliacionTransaccionRespuestaV1 receipt = null;
        ApiFailure failure = null;
        yield return StartCoroutine(
            api.ReconciliarTransaccion(
                evento,
                value => receipt = value,
                value => failure = value
            )
        );
        if (receipt != null)
        {
            ProcesarReconciliacion(evento, receipt);
            yield break;
        }

        string code = failure != null ? failure.Code : "RECONCILIATION_TRANSPORT_FAILURE";
        deliveryOutbox.MarkUnknown(evento.id_transaccion, code);
        if (
            deliveryOutbox.ReconciliationAttemptCount(evento.id_transaccion)
            < MaxIntentosReconciliacion
        )
        {
            yield return new WaitForSeconds(1.0f);
            yield return StartCoroutine(ReconciliarEvento(evento));
        }
        else
        {
            deliveryOutbox.MarkPaused(evento.id_transaccion, code);
        }
    }

    private void ProcesarReconciliacion(
        EventoEntrante evento,
        ReconciliacionTransaccionRespuestaV1 receipt
    )
    {
        if (
            receipt == null
            || !string.Equals(
                receipt.id_transaccion,
                evento.id_transaccion,
                StringComparison.Ordinal
            )
        )
        {
            deliveryOutbox?.MarkPaused(evento.id_transaccion, "CORRELATION_MISMATCH");
            return;
        }

        if (receipt.directive == "RETRY_SAME_TX")
        {
            if (
                receipt.resolution != "NOT_FOUND"
                || evento.request_identity_version != UnityDeliveryIdentityV1.Version
                || string.IsNullOrWhiteSpace(evento.request_fingerprint_sha256)
                || deliveryOutbox.SendAttemptCount(evento.id_transaccion) >= MaxIntentosDelivery
            )
            {
                deliveryOutbox.MarkPaused(evento.id_transaccion, "RETRY_NOT_PROVEN");
                return;
            }
            if (!deliveryOutbox.MarkQueuedAfterNotFound(evento.id_transaccion))
            {
                deliveryOutbox.MarkPaused(evento.id_transaccion, "OUTBOX_WRITE_FAILED");
                return;
            }
            eventosEnVuelo.Remove(evento.id_transaccion);
            EncolarEventoExistente(evento, RequiereReceiptDurable(evento.tipo_evento));
            return;
        }
        if (receipt.directive == "WAIT")
        {
            deliveryOutbox.MarkUnknown(evento.id_transaccion, "IN_PROGRESS");
            if (
                deliveryOutbox.ReconciliationAttemptCount(evento.id_transaccion)
                < MaxIntentosReconciliacion
            )
            {
                StartCoroutine(ReconciliarEventoDespues(evento, 1.0f));
            }
            else
            {
                deliveryOutbox.MarkPaused(evento.id_transaccion, "IN_PROGRESS");
            }
            return;
        }
        if (receipt.directive == "REQUIRES_RECONCILIATION")
        {
            deliveryOutbox.MarkPaused(evento.id_transaccion, receipt.resolution);
            return;
        }
        if (receipt.directive == "TERMINAL")
        {
            deliveryOutbox.MarkTerminal(evento.id_transaccion, receipt.resolution);
            eventosEnVuelo.Remove(evento.id_transaccion);
            return;
        }
        if (receipt.directive != "RESOLVED")
        {
            deliveryOutbox.MarkPaused(evento.id_transaccion, "UNKNOWN_DIRECTIVE");
            return;
        }

        if (evento.tipo_evento == "inicio_ejecucion_accion")
        {
            ProcesarStartConfirmado(evento, receipt);
            return;
        }
        if (evento.tipo_evento == "resultado_accion")
        {
            if (
                receipt.receipt_kind != "OUTCOME"
                || (receipt.receipt_status != "ACCEPTED" && receipt.receipt_status != "DUPLICATE")
            )
            {
                deliveryOutbox.MarkPaused(evento.id_transaccion, "OUTCOME_RECEIPT_INVALID");
                return;
            }
            eventosEnVuelo.Remove(evento.id_transaccion);
            deliveryOutbox.Complete(evento.id_transaccion);
            return;
        }

        RespuestaAPI response = new RespuestaAPI
        {
            wire_version = 1,
            status = receipt.accion != null
                && receipt.accion.estado_ejecucion == "awaiting_authorization"
                    ? "pendiente"
                    : "ok",
            mensaje = "Resultado durable reconciliado.",
            id_transaccion = evento.id_transaccion,
            timestamp_servidor = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
            accion = receipt.accion,
        };
        ProcesarRespuestaDelCerebro(evento, response);
        eventosEnVuelo.Remove(evento.id_transaccion);
        deliveryOutbox.Complete(evento.id_transaccion);
    }

    private IEnumerator ReconciliarEventoDespues(EventoEntrante evento, float seconds)
    {
        yield return new WaitForSeconds(seconds);
        yield return StartCoroutine(ReconciliarEvento(evento));
    }

    private void ProcesarStartConfirmado(
        EventoEntrante evento,
        ReconciliacionTransaccionRespuestaV1 receipt
    )
    {
        if (
            receipt.receipt_kind != "ACTION_LIFECYCLE"
            || (receipt.receipt_status != "APPLIED" && receipt.receipt_status != "ALREADY_APPLIED")
        )
        {
            deliveryOutbox.MarkPaused(evento.id_transaccion, "START_RECEIPT_INVALID");
            return;
        }
        if (!accionesEsperandoInicio.TryGetValue(
            evento.id_transaccion,
            out ContextoAccion contexto
        ))
        {
            // Tras reinicio no se puede distinguir handoff previo: no reejecutar.
            deliveryOutbox.MarkPaused(evento.id_transaccion, "EXECUTION_CONTEXT_UNAVAILABLE");
            return;
        }
        if (!accionesEntregadasAEjecucion.Add(contexto.IdAccion))
        {
            deliveryOutbox.Complete(evento.id_transaccion);
            eventosEnVuelo.Remove(evento.id_transaccion);
            accionesEsperandoInicio.Remove(evento.id_transaccion);
            return;
        }

        if (!deliveryOutbox.MarkExecutionHandoff(evento.id_transaccion))
        {
            deliveryOutbox.MarkPaused(evento.id_transaccion, "OUTBOX_WRITE_FAILED");
            accionesEntregadasAEjecucion.Remove(contexto.IdAccion);
            return;
        }
        eventosEnVuelo.Remove(evento.id_transaccion);
        accionesEsperandoInicio.Remove(evento.id_transaccion);
        EjecutarAccionConfirmada(contexto);
    }

    private void ProcesarRespuestaDelCerebro(
        string idTransaccionSolicitud,
        RespuestaAPI respuesta
    )
    {
        if (!eventosEnVuelo.TryGetValue(idTransaccionSolicitud, out EventoEntrante solicitud))
        {
            Debug.LogWarning("Respuesta para un request que ya no está en vuelo.");
            return;
        }
        eventosEnVuelo.Remove(idTransaccionSolicitud);
        ProcesarRespuestaDelCerebro(solicitud, respuesta);
    }

    private void ProcesarRespuestaDelCerebro(
        EventoEntrante solicitud,
        RespuestaAPI respuesta
    )
    {
        string idTransaccionSolicitud = solicitud.id_transaccion;
        if (
            respuesta == null
            || string.IsNullOrWhiteSpace(respuesta.id_transaccion)
            || respuesta.id_transaccion != idTransaccionSolicitud
        )
        {
            Debug.LogWarning("Respuesta sin correlacion exacta; no se ejecutara ni cerrara otra subtarea.");
            return;
        }
        if (respuesta.accion == null)
        {
            if (
                solicitud.tipo_evento != "confirmacion_accion"
                && solicitud.tipo_evento != "inicio_ejecucion_accion"
                && solicitud.tipo_evento != "resultado_accion"
            )
            {
                CerrarTareaUsuarioActualNeutral(
                    idTransaccionSolicitud,
                    "El backend no produjo una acción ejecutable."
                );
            }
            return;
        }
        if (string.IsNullOrWhiteSpace(respuesta.accion.id_accion))
        {
            CompletarTareaUsuarioActual(
                idTransaccionSolicitud,
                false,
                "El backend produjo una accion sin identidad canonica."
            );
            return;
        }

        if (!TryResolveCanonicalActionContext(solicitud, respuesta, out ContextoAccion contexto))
        {
            Debug.LogWarning("La respuesta no conserva la continuidad canonica de la accion.");
            return;
        }

        string estadoEjecucion = respuesta.accion.estado_ejecucion ?? "";
        if (respuesta.status == "pendiente")
        {
            if (estadoEjecucion != "awaiting_authorization")
            {
                Debug.LogWarning("Respuesta pending sin estado awaiting_authorization; se bloquea.");
                return;
            }
            accionesEsperandoAutorizacion[contexto.IdAccion] = contexto;
            Debug.Log($"Acción pendiente de autorización: {contexto.IdAccion}");
            return;
        }
        if (respuesta.status != "ok")
        {
            Debug.LogWarning($"Status no ejecutable: {respuesta.status}");
            return;
        }
        if (estadoEjecucion == "rejected" || estadoEjecucion == "skipped")
        {
            accionesEsperandoAutorizacion.Remove(contexto.IdAccion);
            CerrarTareaUsuarioActualNeutral(
                contexto.IdTransaccionOrigen,
                $"Estado terminal neutral: {estadoEjecucion}."
            );
            return;
        }
        if (estadoEjecucion != "dispatched")
        {
            Debug.LogWarning($"Estado de ejecución desconocido o no ejecutable: {estadoEjecucion}");
            return;
        }
        accionesEsperandoAutorizacion.Remove(contexto.IdAccion);

        // ROOT-054: dispatched no autoriza efecto fisico. El handler se invoca
        // unicamente desde ProcesarStartConfirmado tras receipt durable.
        EncolarInicioEjecucion(contexto);
    }

    private void EjecutarAccionConfirmada(ContextoAccion contexto)
    {

        // La voz publica viaja solamente como parametro contractual de `hablar`.
        if (contexto.Nombre == "hablar")
        {
            string textoVoz = contexto.Parametros?["texto_a_comunicar"]?.ToString() ?? "";
            if (textoVozUI != null)
            {
                textoVozUI.text = $"[Dice:]\n{textoVoz}";
            }
        }
        else if (textoVozUI != null)
        {
            textoVozUI.text = ""; // Limpia la burbuja de voz si hace otra cosa
        }

        switch (contexto.Nombre)
        {
            case "navegar_a_objeto":
                EjecutarNavegarAObjeto(contexto);
                break;
            case "navegar_a_posicion":
                EjecutarNavegarAPosicion(contexto);
                break;
            case "alejarse_de_objeto":
                EjecutarAlejarseDeObjeto(contexto);
                break;
            case "girar_avatar":
                EjecutarGirarAvatar(contexto);
                break;
            case "cancelar_tarea":
                EjecutarCancelarTarea(contexto);
                break;
            case "interactuar_objeto_3d":
                EjecutarInteraccion(contexto);
                break;
            case "agarrar_objeto":
                EjecutarAgarrar(contexto);
                break;
            case "soltar_objeto":
                EjecutarSoltar(contexto);
                break;
            case "aplicar_fuerza":
                EjecutarAplicarFuerza(contexto);
                break;
            case "hablar":
                EjecutarHablar(contexto);
                break;
            default:
                Debug.LogWarning($"No hay manejador Unity para la accion: {contexto.Nombre}");
                ReportarResultadoAccion(
                    contexto,
                    false,
                    "Unity no tenia implementado un manejador para esta accion."
                );
                break;
        }
    }

    private bool TryResolveCanonicalActionContext(
        EventoEntrante solicitud,
        RespuestaAPI respuesta,
        out ContextoAccion contexto
    )
    {
        contexto = null;
        if (solicitud == null || respuesta == null || respuesta.accion == null)
        {
            return false;
        }
        AccionSaliente responseAction = respuesta.accion;

        if (solicitud.tipo_evento == "confirmacion_accion")
        {
            if (
                !ActionLifecycleContinuityV1.TryGetRequestedConfirmation(
                    solicitud,
                    out string requestedActionId,
                    out bool approved
                )
                || !accionesEsperandoAutorizacion.TryGetValue(
                    requestedActionId,
                    out ContextoAccion pendiente
                )
                || !ActionLifecycleContinuityV1.IsValidConfirmationContinuation(
                    requestedActionId,
                    approved,
                    respuesta.status,
                    responseAction,
                    pendiente.IdAccion,
                    pendiente.IdTransaccionOrigen,
                    pendiente.Nombre,
                    pendiente.Parametros,
                    pendiente.PerceptionAuthority
                )
            )
            {
                return false;
            }

            // La respuesta confirma una transicion. La identidad ejecutable
            // permanece en el contexto que el usuario aprobo originalmente.
            contexto = pendiente;
            return true;
        }

        if (
            !ActionLifecycleContinuityV1.TryResolveNewActionOrigin(
                solicitud,
                responseAction,
                out string canonicalOrigin
            )
        )
        {
            return false;
        }

        contexto = new ContextoAccion(
            responseAction.id_accion,
            canonicalOrigin,
            responseAction.nombre,
            responseAction.parametros,
            responseAction.perception_authority
        );
        return true;
    }

    private void EjecutarCancelarTarea(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string motivo = parametros?["motivo"]?.ToString() ?? "Solicitud explicita del usuario.";
        bool cancelarMovimiento = parametros?["cancelar_movimiento"]?.Value<bool>() ?? true;
        bool cancelarCola = parametros?["cancelar_cola"]?.Value<bool>() ?? true;
        string textoConfirmacion = parametros?["texto_confirmacion"]?.ToString() ?? "De acuerdo, cancelo la tarea actual.";

        if (cancelarMovimiento && movimiento != null)
        {
            movimiento.FrenarMovimiento();
            movimientoTerminadoPendiente = false;
            ultimoMovimientoExitoso = false;
            ultimoResultadoMovimiento = "Movimiento cancelado por solicitud del usuario.";
            accionesMovimientoPendientes.Clear();
        }

        if (cancelarCola)
        {
            CancelarFilaTareasUsuario(motivo);
        }

        if (textoVozUI != null && !string.IsNullOrWhiteSpace(textoConfirmacion))
        {
            textoVozUI.text = $"[Dice:]\n{textoConfirmacion}";
        }

        if (!string.IsNullOrWhiteSpace(textoConfirmacion))
        {
            Debug.Log($"[VOZ LAZZARO]: {textoConfirmacion}");
        }
        Debug.Log($"Tarea cancelada por usuario. {motivo}");
        ReportarResultadoAccion(
            contexto,
            true,
            "La tarea y actividad fisica pendientes fueron canceladas."
        );
    }

    private static string ConstruirManualEspacial()
    {
        // Usamos @ para crear un string multilinea limpio y facil de editar.
        return @"OBJETIVO INICIAL: Acabas de despertar en un entorno virtual. Cuando estes lista, habla y di 'esperando tarea', luego quedate quieta y espera.

REGLAS DE IDENTIDAD Y COMPORTAMIENTO:
- No pidas permiso para hacer acciones que ya te fueron ordenadas. Actua directamente.
- Si acabas de confirmar que vas a ejecutar una accion (ej. caminar), NO vuelvas a hablar hasta que llegues al destino o el usuario te hable de nuevo.
- Cuando necesites comunicarte, usa obligatoriamente la accion 'hablar'.

REGLAS FISICAS Y DE ENTORNO:
- NAVEGACION: Para desplazarte hacia un objeto visible usa 'navegar_a_objeto' con el parametro 'nombre_objeto' exacto o aproximado.
- ROTACION: Para girar, voltear, reorientarte o mirar alrededor sin avanzar usa 'girar_avatar' con el parametro numerico 'grados'.
- VERIFICACION: Antes de ejecutar una accion fisica (navegar/agarrar), revisa los objetos visibles en tu resumen de percepcion. Si no estas segura de que objeto es cual, pregunta una sola vez. Si estas segura, actua.
- INTERACCION: Si ya estas cerca del objeto objetivo, usa 'interactuar_objeto_3d'.";
    }

    private void EjecutarNavegarAObjeto(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "";
        if (string.IsNullOrWhiteSpace(objeto))
        {
            ReportarResultadoAccion(
                contexto,
                false,
                "La accion navegar_a_objeto no incluyo el parametro nombre_objeto."
            );
            return;
        }
        if (!TryLeerDistanciaNavegacionObjeto(parametros, out float? distanciaParada))
        {
            ReportarResultadoAccion(
                contexto,
                false,
                "La distancia de navegar_a_objeto no cumple el contrato fisico."
            );
            return;
        }

        ContextoAccion movimientoContexto = contexto.ConDetalles(
            new Dictionary<string, object>
        {
            { "nombre_objeto", objeto },
        }, objeto);
        accionesMovimientoPendientes[contexto.IdAccion] = movimientoContexto;
        if (contexto.PerceptionAuthority != null)
        {
            if (!TryResolverObjetivoAutorizado(contexto, out Collider exacto, out string error))
            {
                accionesMovimientoPendientes.Remove(contexto.IdAccion);
                ReportarResultadoAccion(contexto, false, error);
                return;
            }
            movimiento.NavegarAObjetivoExacto(
                contexto.IdAccion,
                exacto,
                distanciaParada
            );
        }
        else
        {
            movimiento.NavegarAObjeto(
                contexto.IdAccion,
                objeto,
                distanciaParada
            );
        }
    }

    private void EjecutarNavegarAPosicion(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        if (
            !TryLeerFloat(parametros, "x", out float x)
            || !TryLeerFloat(parametros, "y", out float y)
            || !TryLeerFloat(parametros, "z", out float z)
        )
        {
            ReportarResultadoAccion(
                contexto,
                false,
                "La accion navegar_a_posicion requiere parametros numericos x, y, z."
            );
            return;
        }

        Vector3 destino = new Vector3(x, y, z);
        string etiqueta = $"posicion({x:0.##}, {y:0.##}, {z:0.##})";
        ContextoAccion movimientoContexto = contexto.ConDetalles(
            new Dictionary<string, object>
        {
            { "x", Math.Round(x, 2) },
            { "y", Math.Round(y, 2) },
            { "z", Math.Round(z, 2) },
        }, etiqueta);

        accionesMovimientoPendientes[contexto.IdAccion] = movimientoContexto;
        movimiento.NavegarAPosicion(contexto.IdAccion, destino, etiqueta);
    }

    private void EjecutarAlejarseDeObjeto(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "";
        float distancia = 2.0f;

        if (
            parametros == null
            || (
                parametros.TryGetValue("distancia", out JToken _)
                && !TryLeerFloat(parametros, "distancia", out distancia)
            )
        )
        {
            ReportarResultadoAccion(
                contexto,
                false,
                "La distancia de alejarse_de_objeto no cumple el contrato fisico."
            );
            return;
        }

        if (string.IsNullOrWhiteSpace(objeto))
        {
            ReportarResultadoAccion(
                contexto,
                false,
                "La accion alejarse_de_objeto no incluyo el parametro nombre_objeto."
            );
            return;
        }

        ContextoAccion movimientoContexto = contexto.ConDetalles(
            new Dictionary<string, object>
        {
            { "nombre_objeto", objeto },
            { "distancia", Math.Round(distancia, 2) },
        }, objeto);

        accionesMovimientoPendientes[contexto.IdAccion] = movimientoContexto;
        if (contexto.PerceptionAuthority != null)
        {
            if (!TryResolverObjetivoAutorizado(contexto, out Collider exacto, out string error))
            {
                accionesMovimientoPendientes.Remove(contexto.IdAccion);
                ReportarResultadoAccion(contexto, false, error);
                return;
            }
            movimiento.AlejarseDeObjetivoExacto(contexto.IdAccion, exacto, distancia);
        }
        else
        {
            movimiento.AlejarseDeObjeto(contexto.IdAccion, objeto, distancia);
        }
    }

    private void EjecutarGirarAvatar(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        if (!TryLeerGradosRotacion(parametros, out float grados))
        {
            ReportarResultadoAccion(
                contexto,
                false,
                "La accion girar_avatar requiere grados numericos finitos entre -360 y 360."
            );
            return;
        }

        ContextoAccion movimientoContexto = contexto.ConDetalles(
            new Dictionary<string, object>
        {
            { "grados", grados },
        }, "rotacion");

        accionesMovimientoPendientes[contexto.IdAccion] = movimientoContexto;
        movimiento.GirarAvatar(contexto.IdAccion, grados);
    }

    private static bool TryLeerGradosRotacion(JObject parametros, out float grados)
    {
        grados = 0f;
        if (
            parametros == null
            || !parametros.TryGetValue("grados", out JToken token)
            || token == null
            || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
        )
        {
            return false;
        }

        double valor;
        try
        {
            valor = token.Value<double>();
        }
        catch (Exception)
        {
            return false;
        }

        if (
            double.IsNaN(valor)
            || double.IsInfinity(valor)
            || valor < -360.0d
            || valor > 360.0d
        )
        {
            return false;
        }

        grados = (float)valor;
        return true;
    }

    private void ManejarMovimientoCompletado(string idAccion, bool exito, string mensaje)
    {
        movimientoTerminadoPendiente = true;
        ultimoMovimientoExitoso = exito;
        ultimoResultadoMovimiento = mensaje;

        if (!accionesMovimientoPendientes.TryGetValue(idAccion, out ContextoAccion contexto))
        {
            Debug.LogWarning($"Resultado de movimiento desconocido: id_accion={idAccion}");
            return;
        }
        accionesMovimientoPendientes.Remove(idAccion);

        ReportarResultadoAccion(
            contexto,
            exito,
            mensaje,
            new Dictionary<string, object> { { "movimiento_finalizado", true } },
            contexto.ObjetoInvolucrado
        );
    }

    private static bool TryLeerFloat(JObject parametros, string clave, out float valor)
    {
        valor = 0f;
        if (
            parametros == null
            || !parametros.TryGetValue(clave, out JToken token)
            || token == null
            || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
        )
        {
            return false;
        }

        double valorRaw;
        try
        {
            valorRaw = token.Value<double>();
        }
        catch (Exception)
        {
            return false;
        }
        if (double.IsNaN(valorRaw) || double.IsInfinity(valorRaw))
        {
            return false;
        }

        float valorUnity = (float)valorRaw;
        if (float.IsNaN(valorUnity) || float.IsInfinity(valorUnity))
        {
            return false;
        }
        valor = valorUnity;
        return true;
    }

    private static bool TryLeerFuerzaLanzamiento(JObject parametros, out float fuerza)
    {
        fuerza = 0f;
        if (parametros == null)
        {
            return false;
        }
        if (!parametros.TryGetValue("fuerza_lanzamiento", out JToken token))
        {
            return true;
        }
        if (
            token == null
            || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
        )
        {
            return false;
        }

        double valorRaw;
        try
        {
            valorRaw = token.Value<double>();
        }
        catch (Exception)
        {
            return false;
        }
        if (
            double.IsNaN(valorRaw)
            || double.IsInfinity(valorRaw)
            || valorRaw < 0.0d
            || valorRaw > 20.0d
        )
        {
            return false;
        }

        float valorUnity = (float)valorRaw;
        if (
            float.IsNaN(valorUnity)
            || float.IsInfinity(valorUnity)
            || valorUnity < 0.0f
            || valorUnity > 20.0f
            || (valorRaw > 0.0d && valorUnity == 0.0f)
        )
        {
            return false;
        }

        fuerza = valorUnity;
        return true;
    }

    private static bool TryLeerDistanciaNavegacionObjeto(
        JObject parametros,
        out float? distancia
    )
    {
        distancia = null;
        if (parametros == null || !parametros.TryGetValue("distancia", out JToken token))
        {
            return true;
        }
        if (token == null || token.Type == JTokenType.Null)
        {
            return true;
        }
        if (
            token.Type != JTokenType.Integer
            && token.Type != JTokenType.Float
        )
        {
            return false;
        }

        double valor;
        try
        {
            valor = token.Value<double>();
        }
        catch (Exception)
        {
            return false;
        }
        if (
            double.IsNaN(valor)
            || double.IsInfinity(valor)
            || valor < 0.1d
            || valor > 10.0d
        )
        {
            return false;
        }

        float valorUnity = (float)valor;
        if (float.IsNaN(valorUnity) || float.IsInfinity(valorUnity))
        {
            return false;
        }
        distancia = valorUnity;
        return true;
    }

    private void EjecutarInteraccion(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        JToken tipoToken = parametros?["tipo_accion"];
        if (
            tipoToken == null
            || tipoToken.Type == JTokenType.Null
            || (tipoToken.Type == JTokenType.String && string.IsNullOrWhiteSpace(tipoToken.Value<string>()))
        )
        {
            ReportarResultadoMaterial(
                contexto,
                MaterialEffectReceipt.Unsupported(
                    contexto.IdAccion,
                    MaterialEffectKind.Interact,
                    objeto,
                    "interaction_type_missing",
                    "La accion interactuar_objeto_3d no incluyo un tipo de interaccion explicito."
                )
            );
            return;
        }

        if (tipoToken.Type != JTokenType.String)
        {
            ReportarResultadoMaterial(
                contexto,
                MaterialEffectReceipt.Unsupported(
                    contexto.IdAccion,
                    MaterialEffectKind.Interact,
                    objeto,
                    "interaction_type_unsupported",
                    "El tipo de interaccion no pertenece al contrato canonico."
                )
            );
            return;
        }

        string tipo = tipoToken.Value<string>();
        if (!EsTipoInteraccionCanonico(tipo))
        {
            ReportarResultadoMaterial(
                contexto,
                MaterialEffectReceipt.Unsupported(
                    contexto.IdAccion,
                    MaterialEffectKind.Interact,
                    objeto,
                    "interaction_type_unsupported",
                    $"El tipo de interaccion '{tipo}' no pertenece al contrato canonico."
                )
            );
            return;
        }
        if (!TryBuscarObjetoCercano(
            contexto,
            objeto,
            DistanciaMaximaInteraccion,
            MaterialEffectKind.Interact,
            out Collider objetivo,
            out MaterialEffectReceipt falloBusqueda
        ))
        {
            ReportarResultadoMaterial(contexto, falloBusqueda);
            return;
        }

        MaterialEffectReceipt receipt = materializacion.TryInteract(
            contexto.IdAccion, objetivo, tipo
        );
        ReportarResultadoMaterial(contexto, receipt);
    }

    private void EjecutarAgarrar(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        if (!TryBuscarObjetoCercano(
            contexto,
            objeto,
            DistanciaMaximaAgarrar,
            MaterialEffectKind.Grab,
            out Collider objetivo,
            out MaterialEffectReceipt falloBusqueda
        ))
        {
            ReportarResultadoMaterial(contexto, falloBusqueda);
            return;
        }

        MaterialEffectReceipt receipt = materializacion.TryGrab(contexto.IdAccion, objetivo);
        ReportarResultadoMaterial(contexto, receipt);
    }

    private void EjecutarSoltar(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        if (!TryLeerFuerzaLanzamiento(parametros, out float fuerza))
        {
            ReportarResultadoMaterial(
                contexto,
                MaterialEffectReceipt.Failed(
                    contexto.IdAccion,
                    MaterialEffectKind.Release,
                    materializacion.HeldObjectId ?? "none",
                    "invalid_physical_parameters",
                    "fuerza_lanzamiento no cumple el contrato fisico."
                )
            );
            return;
        }

        MaterialEffectReceipt receipt = materializacion.TryRelease(contexto.IdAccion, fuerza);
        ReportarResultadoMaterial(contexto, receipt);
    }

    private void EjecutarAplicarFuerza(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        if (
            !TryLeerFloat(parametros, "vector_x", out float vx)
            || !TryLeerFloat(parametros, "vector_y", out float vy)
            || !TryLeerFloat(parametros, "vector_z", out float vz)
            || !TryLeerFloat(parametros, "fuerza", out float fuerza)
        )
        {
            ReportarResultadoMaterial(
                contexto,
                MaterialEffectReceipt.Failed(
                    contexto.IdAccion,
                    MaterialEffectKind.Force,
                    objeto,
                    "invalid_physical_parameters",
                    "aplicar_fuerza recibio parametros fuera del contrato fisico."
                )
            );
            return;
        }
        if (fuerza <= 0f || fuerza > 100f)
        {
            ReportarResultadoMaterial(
                contexto,
                MaterialEffectReceipt.Failed(
                    contexto.IdAccion,
                    MaterialEffectKind.Force,
                    objeto,
                    "invalid_physical_parameters",
                    "fuerza quedo fuera del rango canonico (0, 100]."
                )
            );
            return;
        }
        if (!TryBuscarObjetoCercano(
            contexto,
            objeto,
            DistanciaMaximaInteraccion,
            MaterialEffectKind.Force,
            out Collider objetivo,
            out MaterialEffectReceipt falloBusqueda
        ))
        {
            ReportarResultadoMaterial(contexto, falloBusqueda);
            return;
        }

        materializacion.ApplyForce(
            contexto.IdAccion,
            objetivo,
            new Vector3(vx, vy, vz),
            fuerza,
            receipt => ReportarResultadoMaterial(contexto, receipt)
        );
    }

    private void EjecutarHablar(ContextoAccion contexto)
    {
        JObject parametros = contexto.Parametros;
        string texto = parametros?["texto_a_comunicar"]?.ToString() ?? "...";
        Debug.Log($"[VOZ LAZZARO]: {texto}");
        ReportarResultadoAccion(contexto, true, "Salida hablar reportada por Unity.");
    }

    private void ReportarResultadoMaterial(
        ContextoAccion contexto,
        MaterialEffectReceipt receipt
    )
    {
        MaterialEffectKind expectedEffect = EffectKindForAction(contexto.Nombre);
        if (receipt == null || !receipt.IsValidFor(contexto.IdAccion, expectedEffect))
        {
            Debug.LogWarning(
                $"Receipt material invalido; no se reporta outcome. id_accion={contexto.IdAccion} effect={expectedEffect}"
            );
            return;
        }
        ReportarResultadoAccion(
            contexto,
            receipt.IsApplied,
            receipt.Message,
            new Dictionary<string, object>
            {
                { "material_attestation", receipt.ToWirePayload() }
            },
            receipt.ObjectId
        );
    }

    private static MaterialEffectKind EffectKindForAction(string actionName)
    {
        switch (actionName)
        {
            case "interactuar_objeto_3d":
                return MaterialEffectKind.Interact;
            case "agarrar_objeto":
                return MaterialEffectKind.Grab;
            case "soltar_objeto":
                return MaterialEffectKind.Release;
            case "aplicar_fuerza":
                return MaterialEffectKind.Force;
            default:
                return MaterialEffectKind.Unknown;
        }
    }

    private static bool EsTipoInteraccionCanonico(string tipo)
    {
        return tipo == "mirar"
            || tipo == "tocar"
            || tipo == "inspeccionar"
            || tipo == "golpear"
            || tipo == "usar"
            || tipo == "activar";
    }

    private bool TryBuscarObjetoCercano(
        ContextoAccion contexto,
        string nombreObjeto,
        float distanciaMaxima,
        MaterialEffectKind effectKind,
        out Collider objetivo,
        out MaterialEffectReceipt failureReceipt
    )
    {
        objetivo = null;
        failureReceipt = null;
        if (string.IsNullOrWhiteSpace(nombreObjeto) || nombreObjeto == "objeto_desconocido")
        {
            failureReceipt = MaterialEffectReceipt.Failed(
                contexto.IdAccion,
                effectKind,
                "none",
                "target_missing",
                $"La accion {contexto.Nombre} no incluyo un objeto valido."
            );
            return false;
        }

        if (contexto.PerceptionAuthority != null)
        {
            if (!TryResolverObjetivoAutorizado(contexto, out objetivo, out string authorityError))
            {
                failureReceipt = MaterialEffectReceipt.Failed(
                    contexto.IdAccion,
                    effectKind,
                    nombreObjeto,
                    "authority_target_binding_failed",
                    authorityError
                );
                return false;
            }
        }
        else
        {
            objetivo = percepcion.BuscarObjeto(nombreObjeto);
        }
        if (objetivo == null)
        {
            failureReceipt = MaterialEffectReceipt.Failed(
                contexto.IdAccion,
                effectKind,
                nombreObjeto,
                "target_not_found",
                $"No pude encontrar el objeto '{nombreObjeto}'."
            );
            return false;
        }

        float distancia = Vector3.Distance(transform.position, objetivo.transform.position);
        if (distancia > distanciaMaxima)
        {
            failureReceipt = MaterialEffectReceipt.NoEffect(
                contexto.IdAccion,
                effectKind,
                objetivo.name,
                "target_too_far",
                $"El objeto '{objetivo.name}' esta demasiado lejos ({distancia:0.##}m). Primero debo acercarme.",
                new Dictionary<string, object> { { "distance", Math.Round(distancia, 2) } }
            );
            return false;
        }

        return true;
    }

    private bool TryResolverObjetivoAutorizado(
        ContextoAccion contexto,
        out Collider objetivo,
        out string mensajeError
    )
    {
        objetivo = null;
        mensajeError = "La referencia de autoridad perceptiva es invalida.";
        PerceptionAuthorityReferenceV1 authority = contexto.PerceptionAuthority;
        if (
            authority == null
            || authority.version != 1
            || authority.id_accion != contexto.IdAccion
            || authority.action_name != contexto.Nombre
            || authority.target == null
            || string.IsNullOrWhiteSpace(authority.authority_decision_id)
            || string.IsNullOrWhiteSpace(authority.action_binding_sha256)
            || string.IsNullOrWhiteSpace(authority.generation_binding_mode)
        )
        {
            return false;
        }
        JToken boundParameters;
        try
        {
            boundParameters = JToken.Parse(authority.canonical_parameters_json);
        }
        catch (Exception)
        {
            mensajeError = "Los parametros ligados a la autoridad no son JSON valido.";
            return false;
        }
        if (!JToken.DeepEquals(boundParameters, contexto.Parametros))
        {
            mensajeError = "Los parametros cambiaron despues de la autorizacion.";
            return false;
        }
        if (
            authority.producer_capture_id != authority.target.producer_capture_id
            || authority.generation != authority.target.generation
        )
        {
            mensajeError = "La captura ligada a la accion no coincide con el objetivo.";
            return false;
        }
        if (!exactTargetResolver.IsGenerationBindingValid(
            authority.target,
            authority.generation_binding_mode
        ))
        {
            mensajeError = "La generacion perceptiva ya no satisface el modo autorizado.";
            return false;
        }
        string interactionType = contexto.Parametros?["tipo_accion"]?.ToString();
        if (
            authority.action_name == "interactuar_objeto_3d"
            && authority.interaction_type != interactionType
        )
        {
            mensajeError = "El subtipo de interaccion cambio despues de la autorizacion.";
            return false;
        }
        if (
            string.IsNullOrWhiteSpace(authority.safety_disposition)
            || string.IsNullOrWhiteSpace(authority.safety_reason_code)
        )
        {
            mensajeError = "La evidencia de seguridad ligada esta incompleta.";
            return false;
        }
        ExactTargetResolution resolution = exactTargetResolver.Resolve(authority.target);
        if (!resolution.IsResolved)
        {
            mensajeError = $"Fallo de objetivo exacto: {resolution.ReasonCode}.";
            return false;
        }
        objetivo = resolution.Target;
        mensajeError = "";
        return true;
    }

    private static void AgregarDetallesExtra(
        Dictionary<string, object> detalles,
        Dictionary<string, object> detallesExtra
    )
    {
        if (detalles == null || detallesExtra == null)
        {
            return;
        }

        foreach (KeyValuePair<string, object> par in detallesExtra)
        {
            detalles[par.Key] = par.Value;
        }
    }
}
