using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

public class PerceptionSystem : MonoBehaviour
{
    public float radioVision = 15f;
    public int maxObjetosVisibles = 8;

    public Dictionary<string, object> EscanearEntorno()
    {
        List<Collider> interactuables = ObtenerObjetosInteractuables();
        string resumenTextual = GenerarResumenTextual(interactuables);

        Dictionary<string, object> objetoMasCercano = null;
        if (interactuables.Count > 0)
        {
            Collider cercano = interactuables[0];
            float distancia = Vector3.Distance(transform.position, cercano.transform.position);
            objetoMasCercano = new Dictionary<string, object>
            {
                { "nombre", cercano.name },
                { "distancia", Math.Round(distancia, 2) },
            };
        }

        return new Dictionary<string, object>
        {
            { "objetos_visibles", resumenTextual },
            { "resumen_percepcion", resumenTextual },
            { "cantidad_objetos_visibles", interactuables.Count },
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

            if (col.CompareTag("Interactuable"))
            {
                interactuables.Add(col);
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
            return "No veo objetos interactuables cercanos.";
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
            return "No veo objetos interactuables cercanos.";
        }

        return "Veo " + string.Join(", ", fragmentos) + ".";
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

    private static string DescribirDireccion(Vector3 delta)
    {
        if (Mathf.Abs(delta.x) > Mathf.Abs(delta.z))
        {
            return delta.x >= 0f ? "este" : "oeste";
        }

        return delta.z >= 0f ? "norte" : "sur";
    }
}
