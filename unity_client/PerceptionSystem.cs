using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

[RequireComponent(typeof(PerceptionLifecycleContext))]
public class PerceptionSystem : MonoBehaviour
{
    private static readonly HashSet<string> AffordancesPermitidas = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        "obstaculo",
        "transitable",
        "bloquea_paso",
        "punto_refugio",
        "mirar",
        "tocar",
        "agarrable",
        "empujable",
        "activable",
        "inspeccionable",
        "da_sombra",
        "cubre",
        "ilumina",
        "descanso",
        "peligro",
        "recurso",
        "clicable",
        "escribible",
        "arrastrable",
        "ventana",
        "boton",
        "campo_texto",
    };

    public float radioVision = 15f;
    [Tooltip("Angulo de vision frontal en grados. 120 equivale a 60 grados por lado.")]
    public float anguloVision = 120f;
    public int maxObjetosVisibles = 8;
    private PerceptionLifecycleContext lifecycle;

    void Awake()
    {
        lifecycle = GetComponent<PerceptionLifecycleContext>();
    }

    public Dictionary<string, object> EscanearEntorno()
    {
        PerceptionCaptureContext capture = lifecycle.BeginCapture();
        List<Collider> interactuables = ObtenerObjetosInteractuables();
        string resumenTextual = GenerarResumenTextual(interactuables);
        List<string> unknownAffordances;
        List<Dictionary<string, object>> objetosNormalizados = GenerarObjetosNormalizados(
            interactuables,
            out unknownAffordances
        );

        Dictionary<string, object> objetoMasCercano = null;
        if (interactuables.Count > 0)
        {
            Collider cercano = interactuables[0];
            float distancia = Vector3.Distance(transform.position, cercano.transform.position);
            InteractableMetadata metadata = cercano.GetComponent<InteractableMetadata>();
            objetoMasCercano = new Dictionary<string, object>
            {
                { "nombre", cercano.name },
                { "id", ObtenerObjectId(cercano, metadata) },
                { "display_name", ObtenerDisplayName(cercano, metadata) },
                { "distancia", Math.Round(distancia, 2) },
            };
        }

        return new Dictionary<string, object>
        {
            { "contract_version", 1 },
            { "source_kind", "UNITY" },
            { "producer_instance_id", capture.producer_instance_id },
            { "stream_id", capture.stream_id },
            { "producer_epoch_id", capture.producer_epoch_id },
            { "logical_world_id", capture.logical_world_id },
            { "producer_capture_id", capture.producer_capture_id },
            { "generation", capture.generation },
            { "captured_at_utc_ms", capture.captured_at_utc_ms },
            { "objects", objetosNormalizados },
            { "snapshot_id", CrearSnapshotId(interactuables) },
            { "source", "unity" },
            { "objetos_visibles", resumenTextual },
            { "objetos_normalizados", objetosNormalizados },
            { "resumen_percepcion", resumenTextual },
            { "cantidad_objetos_visibles", interactuables.Count },
            { "unknown_affordances", unknownAffordances },
            {
                "posicion_lazzaro",
                new Dictionary<string, object>
                {
                    { "x", Math.Round(transform.position.x, 2) },
                    { "y", Math.Round(transform.position.y, 2) },
                    { "z", Math.Round(transform.position.z, 2) },
                }
            },
            { "objeto_mas_cercano", objetoMasCercano },
        };
    }

    public List<Collider> ObtenerObjetosInteractuables()
    {
        // OverlapSphere es la fase amplia barata; despues filtramos por campo visual.
        Collider[] hitColliders = Physics.OverlapSphere(
            transform.position,
            radioVision,
            ~0,
            QueryTriggerInteraction.Ignore
        );

        List<Collider> interactuables = new List<Collider>();
        foreach (Collider col in hitColliders)
        {
            if (col == null || col.gameObject == gameObject)
            {
                continue;
            }

            if (col.name.Equals("Plane", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            if (col.CompareTag("Interactuable"))
            {
                Vector3 direccionAlObjeto = col.transform.position - transform.position;
                if (direccionAlObjeto.sqrMagnitude <= 0.0001f || EstaEnCampoVisual(direccionAlObjeto))
                {
                    interactuables.Add(col);
                }
            }
        }

        interactuables.Sort((a, b) =>
        {
            float distA = (a.transform.position - transform.position).sqrMagnitude;
            float distB = (b.transform.position - transform.position).sqrMagnitude;
            return distA.CompareTo(distB);
        });

        return interactuables;
    }

    public string GenerarResumenTextual(List<Collider> objetos)
    {
        if (objetos == null || objetos.Count == 0)
        {
            return "No veo objetos interactuables en mi campo visual directo.";
        }

        List<string> fragmentos = new List<string>();
        int limite = Mathf.Min(maxObjetosVisibles, objetos.Count);

        for (int i = 0; i < limite; i++)
        {
            Collider col = objetos[i];
            if (col == null)
            {
                continue;
            }

            Vector3 delta = col.transform.position - transform.position;
            float distancia = delta.magnitude;
            string distanciaTexto = distancia.ToString("0.#", CultureInfo.InvariantCulture);
            string direccion = DescribirDireccion(delta);
            fragmentos.Add($"{col.name} a {distanciaTexto}m al {direccion}");
        }

        if (fragmentos.Count == 0)
        {
            return "No veo objetos interactuables en mi campo visual directo.";
        }

        return "Frente a mi veo: " + string.Join(", ", fragmentos) + ".";
    }

    public List<Dictionary<string, object>> GenerarObjetosNormalizados(
        List<Collider> objetos,
        out List<string> unknownAffordances
    )
    {
        unknownAffordances = new List<string>();
        List<Dictionary<string, object>> normalizados = new List<Dictionary<string, object>>();

        if (objetos == null)
        {
            return normalizados;
        }

        int limite = Mathf.Min(maxObjetosVisibles, objetos.Count);
        for (int i = 0; i < limite; i++)
        {
            Collider col = objetos[i];
            if (col == null)
            {
                continue;
            }

            InteractableMetadata metadata = col.GetComponent<InteractableMetadata>();
            float distancia = Vector3.Distance(transform.position, col.transform.position);
            List<string> aliases = ObtenerAliases(col, metadata);
            List<string> affordances = ObtenerAffordancesCanonicas(metadata, unknownAffordances);

            normalizados.Add(new Dictionary<string, object>
            {
                { "id", ObtenerObjectId(col, metadata) },
                {
                    "producer_object_id",
                    metadata != null ? metadata.GetExplicitObjectId() : null
                },
                {
                    "runtime_object_id",
                    col.gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture)
                },
                { "display_name", ObtenerDisplayName(col, metadata) },
                { "nombre", col.name },
                { "runtime_instance_id", col.gameObject.GetInstanceID() },
                { "aliases", aliases },
                {
                    "position",
                    new Dictionary<string, object>
                    {
                        { "x", Math.Round(col.transform.position.x, 2) },
                        { "y", Math.Round(col.transform.position.y, 2) },
                        { "z", Math.Round(col.transform.position.z, 2) },
                    }
                },
                { "distance", Math.Round(distancia, 2) },
                { "distancia", Math.Round(distancia, 2) },
                { "affordances", affordances },
                { "source", "unity" },
                { "confidence", metadata != null ? Mathf.Clamp01(metadata.confidence) : 1f },
            });
        }

        return normalizados;
    }

    public bool CambioSignificativo(List<Collider> nuevos, List<Collider> viejos)
    {
        if (nuevos == null)
        {
            nuevos = new List<Collider>();
        }

        if (viejos == null)
        {
            viejos = new List<Collider>();
        }

        foreach (Collider viejo in viejos)
        {
            if (viejo == null || !nuevos.Contains(viejo))
            {
                return true;
            }
        }

        foreach (Collider nuevo in nuevos)
        {
            if (nuevo == null || viejos.Contains(nuevo))
            {
                continue;
            }

            float distancia = Vector3.Distance(transform.position, nuevo.transform.position);
            if (distancia <= 2f)
            {
                return true;
            }
        }

        return false;
    }

    public Collider BuscarObjeto(string nombreObjeto)
    {
        if (string.IsNullOrWhiteSpace(nombreObjeto))
        {
            return null;
        }

        string objetivoNormalizado = nombreObjeto.Trim().ToLowerInvariant();
        List<Collider> interactuables = ObtenerObjetosInteractuables();
        Collider mejorCoincidencia = null;
        float mejorDistancia = float.PositiveInfinity;

        foreach (Collider col in interactuables)
        {
            if (col == null)
            {
                continue;
            }

            string nombreNormalizado = col.name.ToLowerInvariant();
            bool coincide = nombreNormalizado == objetivoNormalizado
                || nombreNormalizado.Contains(objetivoNormalizado)
                || objetivoNormalizado.Contains(nombreNormalizado);

            if (!coincide)
            {
                continue;
            }

            float distancia = (col.transform.position - transform.position).sqrMagnitude;
            if (distancia < mejorDistancia)
            {
                mejorDistancia = distancia;
                mejorCoincidencia = col;
            }
        }

        return mejorCoincidencia;
    }

    private bool EstaEnCampoVisual(Vector3 direccionAlObjeto)
{
    // Evitar errores si el objeto está literalmente adentro de ella
    if (direccionAlObjeto.sqrMagnitude <= 0.001f) return true;

    // Calcular el ángulo 3D real desde donde mira Lazzaro
    float anguloAlObjeto = Vector3.Angle(transform.forward, direccionAlObjeto.normalized);
    
    // Si el ángulo es menor o igual a la mitad de su FOV, lo está viendo
    return anguloAlObjeto <= (Mathf.Clamp(anguloVision, 1f, 360f) / 2f);
}

    private static string DescribirDireccion(Vector3 delta)
    {
        if (Mathf.Abs(delta.x) > Mathf.Abs(delta.z))
        {
            return delta.x >= 0f ? "este (derecha)" : "oeste (izquierda)";
        }

        return delta.z >= 0f ? "norte (al frente)" : "sur (detras)";
    }

    private static string ObtenerObjectId(Collider col, InteractableMetadata metadata)
    {
        if (metadata != null)
        {
            return metadata.GetObjectId(col.gameObject);
        }

        return col != null ? col.name : "unknown_object";
    }

    private static string ObtenerDisplayName(Collider col, InteractableMetadata metadata)
    {
        if (metadata != null)
        {
            return metadata.GetDisplayName(col.gameObject);
        }

        return col != null ? col.name : "unknown_object";
    }

    private static List<string> ObtenerAliases(Collider col, InteractableMetadata metadata)
    {
        List<string> aliases = new List<string>();
        AgregarUnico(aliases, col != null ? col.name : "");

        if (metadata != null)
        {
            AgregarUnico(aliases, metadata.GetDisplayName(col.gameObject));
            foreach (string alias in metadata.GetAliases())
            {
                AgregarUnico(aliases, alias);
            }
        }

        return aliases;
    }

    private static List<string> ObtenerAffordancesCanonicas(
        InteractableMetadata metadata,
        List<string> unknownAffordances
    )
    {
        List<string> affordances = new List<string>();
        if (metadata == null)
        {
            return affordances;
        }

        foreach (string raw in metadata.GetAffordances())
        {
            string normalizada = NormalizarAffordance(raw);
            if (string.IsNullOrWhiteSpace(normalizada))
            {
                continue;
            }

            if (AffordancesPermitidas.Contains(normalizada))
            {
                AgregarUnico(affordances, normalizada);
            }
            else
            {
                AgregarUnico(unknownAffordances, normalizada);
            }
        }

        return affordances;
    }

    private static string NormalizarAffordance(string valor)
    {
        if (string.IsNullOrWhiteSpace(valor))
        {
            return "";
        }

        return valor.Trim().ToLowerInvariant().Replace(" ", "_").Replace("-", "_");
    }

    private static void AgregarUnico(List<string> valores, string valor)
    {
        if (valores == null || string.IsNullOrWhiteSpace(valor))
        {
            return;
        }

        string limpio = valor.Trim();
        if (!valores.Contains(limpio))
        {
            valores.Add(limpio);
        }
    }

    private string CrearSnapshotId(List<Collider> objetos)
    {
        List<string> partes = new List<string>
        {
            "unity",
            transform.position.x.ToString("0.00", CultureInfo.InvariantCulture),
            transform.position.y.ToString("0.00", CultureInfo.InvariantCulture),
            transform.position.z.ToString("0.00", CultureInfo.InvariantCulture),
        };

        if (objetos != null)
        {
            int limite = Mathf.Min(maxObjetosVisibles, objetos.Count);
            for (int i = 0; i < limite; i++)
            {
                Collider col = objetos[i];
                if (col != null)
                {
                    partes.Add(col.name);
                    float distancia = Vector3.Distance(transform.position, col.transform.position);
                    partes.Add(distancia.ToString("0.00", CultureInfo.InvariantCulture));
                }
            }
        }

        return string.Join("_", partes);
    }
}
