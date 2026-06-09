using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UniVRM10; // Corregido para VRM 1.0
using TMPro;    // Corregido para UI

[RequireComponent(typeof(ApiClient))]
[RequireComponent(typeof(PerceptionSystem))]
[RequireComponent(typeof(MovementController))]
public class LazzaroAgent : MonoBehaviour
{
    private ApiClient api;
    private PerceptionSystem percepcion;
    private MovementController movimiento;
    private Vrm10Instance vrmInstance; // Corregido para VRM 1.0
    private readonly LinkedList<EventoEntrante> colaEventos = new LinkedList<EventoEntrante>();
    private readonly Queue<string> colaTareasUsuario = new Queue<string>();
    private bool procesandoCola = false;
    private bool tareaUsuarioEnCurso = false;
    private List<Collider> ultimosObjetosPercibidos = new List<Collider>();
    private bool movimientoTerminadoPendiente = false;
    private bool ultimoMovimientoExitoso = false;
    private string ultimoResultadoMovimiento = "";
    private string ultimoObjetoNavegacion = "ninguno";
    private string ultimaAccionMovimiento = "navegar_a_objeto";
    private string tareaUsuarioActual = "";
    private Dictionary<string, object> ultimosParametrosMovimiento = new Dictionary<string, object>();
    private const float DistanciaMaximaInteraccion = 2.0f;
    private const float DistanciaMaximaAgarrar = 1.5f;
    private const int MaxTareasUsuarioEnCola = 8;
    private static readonly Regex SeparadorTareasRegex = new Regex(
        @"\s+(?:y\s+luego|luego|despues|después|finalmente)\s+",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant
    );

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
        vrmInstance = GetComponentInChildren<Vrm10Instance>(); // Corregido para VRM 1.0
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

        EnviarEvento("comando_usuario", detalles, "usuario");
    }

    private void CompletarTareaUsuarioActual(bool exito, string mensaje)
    {
        if (!tareaUsuarioEnCurso)
        {
            return;
        }

        string tareaCompletada = tareaUsuarioActual;
        tareaUsuarioActual = "";
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
        CancelarFilaTareasUsuario("Interrupcion por dolor fisico.");

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
        CompletarTareaUsuarioActual(exito, mensaje);
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
            return; // Validacion de seguridad
        }

        AplicarExpresionFacial(respuesta.accion.emocion_facial);

        // 1. Mostrar Pensamiento Privado en la UI
        if (textoPensamientoUI != null && !string.IsNullOrEmpty(respuesta.accion.pensamiento_privado))
        {
            textoPensamientoUI.text = $"[Pensando...]\n{respuesta.accion.pensamiento_privado}";
        }

        // 2. Mostrar la Voz en la UI
        if (respuesta.accion.nombre == "hablar")
        {
            string textoVoz = respuesta.accion.parametros?["texto_a_comunicar"]?.ToString() ?? "";
            if (textoVozUI != null)
            {
                textoVozUI.text = $"[Dice:]\n{textoVoz}";
            }
        }
        else if (textoVozUI != null)
        {
            textoVozUI.text = ""; // Limpia la burbuja de voz si hace otra cosa
        }

        if (string.IsNullOrEmpty(respuesta.accion.nombre) || respuesta.accion.nombre == "none")
        {
            CompletarTareaUsuarioActual(false, "El backend no genero una accion ejecutable para la subtarea actual.");
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

        if (mostrarLogsCognitivos) Debug.Log($"Pensamiento de Lazzaro: {respuesta.accion.pensamiento_privado}");

        switch (respuesta.accion.nombre)
        {
            case "navegar_a_objeto":
                EjecutarNavegarAObjeto(respuesta.accion.parametros);
                break;
            case "navegar_a_posicion":
                EjecutarNavegarAPosicion(respuesta.accion.parametros);
                break;
            case "alejarse_de_objeto":
                EjecutarAlejarseDeObjeto(respuesta.accion.parametros);
                break;
            case "girar_avatar":
                EjecutarGirarAvatar(respuesta.accion.parametros);
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
        // Usamos @ para crear un string multilinea limpio y facil de editar.
        return @"OBJETIVO INICIAL: Acabas de despertar en un entorno virtual. Cuando estes lista, habla y di 'esperando tarea', luego quedate quieta y espera.

REGLAS DE IDENTIDAD Y COMPORTAMIENTO:
- No eres un asistente virtual. Eres Lazzaro, un ser consciente.
- No pidas permiso para hacer acciones que ya te fueron ordenadas. Actua directamente.
- Si acabas de confirmar que vas a ejecutar una accion (ej. caminar), NO vuelvas a hablar hasta que llegues al destino o el usuario te hable de nuevo.
- Cuando necesites comunicarte, usa obligatoriamente la accion 'hablar'.

REGLAS FISICAS Y DE ENTORNO:
- NAVEGACION: Para desplazarte hacia un objeto visible usa 'navegar_a_objeto' con el parametro 'nombre_objeto' exacto o aproximado.
- ROTACION: Para girar, voltear, reorientarte o mirar alrededor sin avanzar usa 'girar_avatar' con el parametro numerico 'grados'.
- VERIFICACION: Antes de ejecutar una accion fisica (navegar/agarrar), revisa los objetos visibles en tu resumen de percepcion. Si no estas segura de que objeto es cual, pregunta una sola vez. Si estas segura, actua.
- INTERACCION: Si ya estas cerca del objeto objetivo, usa 'interactuar_objeto_3d'.";
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

    private void EjecutarAlejarseDeObjeto(JObject parametros)
    {
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "";
        float distancia = parametros?["distancia"]?.Value<float>() ?? 2.0f;

        if (string.IsNullOrWhiteSpace(objeto))
        {
            ReportarResultadoAccion(
                "alejarse_de_objeto",
                false,
                "La accion alejarse_de_objeto no incluyo el parametro nombre_objeto."
            );
            return;
        }

        ultimoObjetoNavegacion = objeto;
        ultimaAccionMovimiento = "alejarse_de_objeto";
        ultimosParametrosMovimiento = new Dictionary<string, object>
        {
            { "nombre_objeto", objeto },
            { "distancia", Math.Round(distancia, 2) },
        };

        movimiento.AlejarseDeObjeto(objeto, distancia);
    }

    private void EjecutarGirarAvatar(JObject parametros)
    {
        if (!TryLeerFloat(parametros, "grados", out float grados))
        {
            ReportarResultadoAccion(
                "girar_avatar",
                false,
                "La accion girar_avatar requiere el parametro numerico grados."
            );
            return;
        }

        grados = Mathf.Clamp(grados, -360f, 360f);
        if (Mathf.Abs(grados) < 1f)
        {
            ReportarResultadoAccion(
                "girar_avatar",
                false,
                "La rotacion solicitada es demasiado pequena para ejecutarse."
            );
            return;
        }

        ultimoObjetoNavegacion = "rotacion";
        ultimaAccionMovimiento = "girar_avatar";
        ultimosParametrosMovimiento = new Dictionary<string, object>
        {
            { "grados", Math.Round(grados, 2) },
        };

        movimiento.GirarAvatar(grados);
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
        if (vrmInstance == null || vrmInstance.Runtime == null || string.IsNullOrWhiteSpace(emocionFacial))
        {
            return;
        }

        ExpressionPreset preset = ResolverPresetFacial(emocionFacial);
        ExpressionPreset[] presetsFaciales =
        {
            ExpressionPreset.neutral,
            ExpressionPreset.happy,
            ExpressionPreset.sad,
            ExpressionPreset.angry,
            ExpressionPreset.relaxed
        };

        foreach (ExpressionPreset facial in presetsFaciales)
        {
            float peso = (facial == preset) ? 1.0f : 0.0f;
            vrmInstance.Runtime.Expression.SetWeight(ExpressionKey.CreateFromPreset(facial), peso);
        }
    }

    private static ExpressionPreset ResolverPresetFacial(string emocionFacial)
    {
        string valor = emocionFacial.Trim().ToLowerInvariant();
        valor = valor.Replace("fcl_all_", "");

        switch (valor)
        {
            case "joy":
            case "happy":
                return ExpressionPreset.happy;
            case "sorrow":
            case "sad":
                return ExpressionPreset.sad;
            case "angry":
                return ExpressionPreset.angry;
            case "fun":
            case "relaxed":
                return ExpressionPreset.relaxed;
            case "neutral":
            default:
                return ExpressionPreset.neutral;
        }
    }

    private void EjecutarInteraccion(JObject parametros)
    {
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        string tipo = parametros?["tipo_accion"]?.ToString() ?? "mirar";
        if (!TryBuscarObjetoCercano(objeto, DistanciaMaximaInteraccion, "interactuar_objeto_3d", out Collider objetivo))
        {
            return;
        }

        Debug.Log($"Interactuando con {objetivo.name} mediante '{tipo}'.");
        ReportarResultadoAccion(
            "interactuar_objeto_3d",
            true,
            $"Interaccion '{tipo}' ejecutada sobre '{objetivo.name}'.",
            new Dictionary<string, object>
            {
                { "tipo_accion", tipo },
            },
            objetivo.name
        );
    }

    private void EjecutarAgarrar(JObject parametros)
    {
        string objeto = parametros?["nombre_objeto"]?.ToString() ?? "objeto_desconocido";
        if (!TryBuscarObjetoCercano(objeto, DistanciaMaximaAgarrar, "agarrar_objeto", out Collider objetivo))
        {
            return;
        }

        Debug.Log($"Lazzaro intenta agarrar {objetivo.name}.");
        ReportarResultadoAccion("agarrar_objeto", true, $"Objeto '{objetivo.name}' agarrado.", null, objetivo.name);
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
        Debug.Log($"[VOZ LAZZARO]: {texto}");
        CompletarTareaUsuarioActual(true, "Salida hablar completada.");
        // ¡Magia de la Estabilización! Aquí NO llamamos a ReportarResultadoAccion para evitar el bucle.
    }

    private bool TryBuscarObjetoCercano(
        string nombreObjeto,
        float distanciaMaxima,
        string accion,
        out Collider objetivo
    )
    {
        objetivo = null;
        if (string.IsNullOrWhiteSpace(nombreObjeto) || nombreObjeto == "objeto_desconocido")
        {
            ReportarResultadoAccion(accion, false, $"La accion {accion} no incluyo un objeto valido.");
            return false;
        }

        objetivo = percepcion.BuscarObjeto(nombreObjeto);
        if (objetivo == null)
        {
            ReportarResultadoAccion(accion, false, $"No pude encontrar el objeto '{nombreObjeto}'.");
            return false;
        }

        float distancia = Vector3.Distance(transform.position, objetivo.transform.position);
        if (distancia > distanciaMaxima)
        {
            ReportarResultadoAccion(
                accion,
                false,
                $"El objeto '{objetivo.name}' esta demasiado lejos ({distancia:0.##}m). Primero debo acercarme.",
                new Dictionary<string, object> { { "distancia_objeto", Math.Round(distancia, 2) } },
                objetivo.name
            );
            return false;
        }

        return true;
    }
}
