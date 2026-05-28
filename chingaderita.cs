using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

public class LazzaroClient : MonoBehaviour
{
    [SerializeField] private string apiBaseUrl = "http://localhost:8000/api/v1";
    private string EventoUrl => $"{apiBaseUrl}/evento";

    private Queue<EventoEntrante> eventQueue = new Queue<EventoEntrante>();
    private bool isProcessing = false;

    // Diccionario de acciones -> delegados
    private Dictionary<string, Action<JObject>> actionHandlers;

    [Serializable]
    public class EventoEntrante
    {
        public string origen = "unity";
        public string tipo_evento;
        public string id_transaccion;
        public string objeto_involucrado = "ninguno"; // campo obligatorio en el schema actual
        public Dictionary<string, object> detalles; // cambiar a JObject si se complica
    }

    [Serializable]
    public class AccionSaliente
    {
        public string id_accion;
        public string nombre;
        public string pensamiento_privado;
        public JObject parametros; // Usamos JObject para flexibilidad
        public bool requiere_confirmacion;
    }

    [Serializable]
    public class RespuestaAPI
    {
        public string status;
        public string mensaje;
        public string id_transaccion;
        public AccionSaliente accion;
    }

    void Awake()
    {
        // Registrar manejadores de acciones
        actionHandlers = new Dictionary<string, Action<JObject>>()
        {
            { "moverse_entorno_3d", HandleMoverse },
            { "interactuar_objeto_3d", HandleInteractuar },
            { "mover_mouse", HandleMoverMouse },
            { "escribir_texto", HandleEscribirTexto },
            { "agarrar_objeto", HandleAgarrarObjeto },
            { "soltar_objeto", HandleSoltarObjeto },
            { "aplicar_fuerza", HandleAplicarFuerza },
            { "hablar", HandleHablar },
            // Agregar más según herramientas simuladas
        };
    }

    void Start()
    {
        // Usamos nuestro nuevo escáner
        Dictionary<string, object> detallesVision = EscanearEntorno();
        // ¡Aquí está el truco! Le damos un objetivo claro en su mensaje de despertar.
        detallesVision.Add("mensaje", "Lazzaro ha despertado. Tu objetivo prioritario es explorar el entorno. Muévete hacia el cubo más cercano para investigarlo.");
        detallesVision.Add("salud_avatar", 100);
        ReportarEvento("inicio_simulacion", detallesVision);
        // nueva linea  --- latidos de agente --- 
        StartCoroutine(LatidoCognitivo());
    }
    private IEnumerator LatidoCognitivo()
    {
        while (true) // Bucle infinito de consciencia
        {
            // Espera 3 segundos antes de volver a pensar
            yield return new WaitForSeconds(3.0f);

            // Abre los ojos y escanea su nueva posición
            Dictionary<string, object> detallesVision = EscanearEntorno();
            
            // Le inyectamos su objetivo constantemente para que no se quede "preservando la estabilidad"
            detallesVision.Add("mensaje", "Objetivo activo: explora el entorno y acércate a los cubos. Continúa moviéndote.");

            // Manda la telemetría a su cerebro en Python
            ReportarEvento("actualizacion_estado", detallesVision);
        }
    }

    public void ReportarEvento(string tipoEvento, Dictionary<string, object> detalles = null, string objeto = "ninguno")
    {
        EventoEntrante evento = new EventoEntrante
        {
            tipo_evento = tipoEvento,
            id_transaccion = System.Guid.NewGuid().ToString(),
            objeto_involucrado = objeto,
            detalles = detalles ?? new Dictionary<string, object>()
        };

        eventQueue.Enqueue(evento);
        if (!isProcessing)
            StartCoroutine(ProcessQueue());
    }

    IEnumerator ProcessQueue()
    {
        isProcessing = true;
        while (eventQueue.Count > 0)
        {
            var evento = eventQueue.Dequeue();
            yield return StartCoroutine(EnviarPost(evento));
        }
        isProcessing = false;
    }
    IEnumerator EnviarPost(EventoEntrante evento)
    {
        string json = JsonConvert.SerializeObject(evento);
        
        using (UnityWebRequest request = new UnityWebRequest(EventoUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.timeout = 10; // Redujimos el timeout para que no se empalme con el latido

            // Enviamos la petición una sola vez
            yield return request.SendWebRequest();

            // Si hay un error de red, solo avisamos y cancelamos. El próximo latido lo intentará de nuevo.
            if (request.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"Fallo de conexión ({request.error}). Se reintentará en el próximo latido.");
                yield break; 
            }

            // Parsear respuesta si fue exitosa
            string respuestaJson = request.downloadHandler.text;
            try
            {
                RespuestaAPI respuesta = JsonConvert.DeserializeObject<RespuestaAPI>(respuestaJson);
                if (respuesta != null && respuesta.status == "ok" && respuesta.accion != null)
                {
                    if (respuesta.id_transaccion == evento.id_transaccion)
                    {
                        EjecutarAccionEnUnity(respuesta.accion);
                    }
                    else
                    {
                        Debug.LogWarning("ID de transacción no coincide, ignorando acción.");
                    }
                }
            }
            catch (Exception e)
            {
                Debug.LogError($"Error parseando respuesta JSON: {e.Message}");
            }
        }
    }
    void EjecutarAccionEnUnity(AccionSaliente accion)
    {
        if (accion.nombre == "none" || string.IsNullOrEmpty(accion.nombre))
        {
            Debug.Log($"👀 Lazzaro está observando pacíficamente. Pensamiento: {accion.pensamiento_privado}");
            return; // Cortamos la función aquí
        }
        Debug.Log($"Acción: {accion.nombre}, Params: {accion.parametros?.ToString()}");
        if (actionHandlers.TryGetValue(accion.nombre, out var handler))
        {
            handler.Invoke(accion.parametros);
        }
        else
        {
            Debug.LogWarning($"No hay manejador para la acción: {accion.nombre}");
        }
    }

    // Manejadores concretos de acciones
    void HandleMoverse(JObject parametros)
    {
        string direccion = parametros["direccion"]?.ToString() ?? "adelante";
        float distancia = parametros["distancia"]?.Value<float>() ?? 1.0f;
        string velocidad = parametros["velocidad"]?.ToString() ?? "caminar";
        // Aquí moverías al personaje: Transform.Translate o CharacterController.Move
        Debug.Log($"Moviendo {distancia} unidades hacia {direccion} ({velocidad})");

        // convertimos la direcion en un vector matematico de unity
        Vector3 movimiento = Vector3.zero;

        switch (direccion.ToLower())
        {
            case "adelante":
                movimiento = Vector3.forward; //eje z positivo
                break;
            case "atras":
                movimiento = Vector3.back;
                break;
            case "izquierda":
                movimiento = Vector3.left;
                break;
            case "derecha":
                movimiento = Vector3.right;
                break;
            default:
                movimiento = Vector3.forward;
                break;
        }
        // En lugar de teletransportarnos, iniciamos una rutina de movimiento suave
        StartCoroutine(MoverSuavemente(movimiento * distancia));
    }
    private IEnumerator MoverSuavemente(Vector3 desplazamiento)
    {
        Vector3 posicionInicial = transform.position;
        Vector3 posicionFinal = posicionInicial + desplazamiento;
        
        float tiempoDeViaje = 1.0f; // Tardará exactamente 1 segundo en dar el paso
        float tiempoTranscurrido = 0;

        // Mientras no haya pasado 1 segundo, moveremos el objeto poco a poco
        while (tiempoTranscurrido < tiempoDeViaje)
        {
            // Vector3.Lerp calcula la posición exacta entre el inicio y el fin basándose en el tiempo
            transform.position = Vector3.Lerp(posicionInicial, posicionFinal, tiempoTranscurrido / tiempoDeViaje);
            tiempoTranscurrido += Time.deltaTime; // Sumamos el tiempo de este frame
            yield return null; // Esperamos al siguiente frame de Unity
        }

        // Al terminar, nos aseguramos de que quede exactamente en la posición final
        transform.position = posicionFinal; 
    }

    void HandleInteractuar(JObject parametros)
    {
        string objeto = parametros["nombre_objeto"]?.ToString();
        string tipo = parametros["tipo_accion"]?.ToString() ?? "mirar";
        Debug.Log($"Interactuando con {objeto} (acción: {tipo})");
    }

    void HandleMoverMouse(JObject parametros)
    {
        float x = parametros["x"]?.Value<float>() ?? 0;
        float y = parametros["y"]?.Value<float>() ?? 0;
        Debug.Log($"Mover cursor a ({x},{y})");
        // Simular en UI
    }
    void HandleAgarrarObjeto(JObject parametros)
{
    string nombreObjeto = parametros["nombre_objeto"]?.ToString();
    Debug.Log($"Lazzaro intenta agarrar: {nombreObjeto}");
    // Aquí iría la lógica de agarre (Raycast, Joints, etc.)
}

void HandleSoltarObjeto(JObject parametros)
{
    float fuerza = parametros["fuerza_lanzamiento"]?.Value<float>() ?? 0f;
    Debug.Log($"Lazzaro suelta el objeto con fuerza de lanzamiento: {fuerza}");
    // Lógica de soltar (destruir FixedJoint, aplicar impulso al objeto agarrado, etc.)
}

void HandleAplicarFuerza(JObject parametros)
{
    string nombreObjeto = parametros["nombre_objeto"]?.ToString();
    float vx = parametros["vector_x"]?.Value<float>() ?? 0f;
    float vy = parametros["vector_y"]?.Value<float>() ?? 0f;
    float vz = parametros["vector_z"]?.Value<float>() ?? 0f;
    float fuerza = parametros["fuerza"]?.Value<float>() ?? 0f;
    Vector3 direccion = new Vector3(vx, vy, vz);
    Debug.Log($"Aplicando fuerza {fuerza} a '{nombreObjeto}' en dirección {direccion}");
    // Buscar el GameObject y usar Rigidbody.AddForce(direccion.normalized * fuerza)
}

void HandleHablar(JObject parametros)
{
    string texto = parametros["texto_a_comunicar"]?.ToString();
    Debug.Log($"Lazzaro dice: \"{texto}\"");
    // Mostrar texto en UI o activar burbuja de diálogo
}
    void HandleEscribirTexto(JObject parametros)
    {
        string texto = parametros["texto"]?.ToString();
        Debug.Log($"Escribir texto: {texto}");
        // Simular en un campo de texto
    }
    private Dictionary<string, object> EscanearEntorno()
    {
        List<object> objetosVistos = new List<object>();
        
        // Buscamos todos los objetos que tengan la etiqueta que creamos
        GameObject[] objetosEnEscena = GameObject.FindGameObjectsWithTag("Interactuable");

        foreach (GameObject obj in objetosEnEscena)
        {
            objetosVistos.Add(new 
            {
                nombre = obj.name,
                // Redondeamos a 2 decimales para no saturar los tokens del LLM
                posicion = new { 
                    x = Math.Round(obj.transform.position.x, 2), 
                    y = Math.Round(obj.transform.position.y, 2), 
                    z = Math.Round(obj.transform.position.z, 2) 
                }
            });
        }

        return new Dictionary<string, object>
        {
            { "objetos_visibles", objetosVistos },
            { "posicion_lazzaro", new { 
                x = Math.Round(transform.position.x, 2), 
                y = Math.Round(transform.position.y, 2), 
                z = Math.Round(transform.position.z, 2)
        }}
    };
}}