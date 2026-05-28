using System;
using System.Collections.Generic;
using UnityEngine;

public class PerceptionSystem : MonoBehaviour
{
    public float radioVision = 15f;
    public int maxObjetosVisibles = 8;

    public Dictionary<string, object> EscanearEntorno()
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

        List<object> objetosVistos = new List<object>();
        Dictionary<string, object> objetoMasCercano = null;

        int limite = Mathf.Min(maxObjetosVisibles, interactuables.Count);
        for (int i = 0; i < limite; i++)
        {
            Collider col = interactuables[i];
            float distancia = Vector3.Distance(transform.position, col.transform.position);
            var objeto = new Dictionary<string, object>
            {
                { "nombre", col.name },
                { "distancia", Math.Round(distancia, 2) },
                {
                    "posicion",
                    new Dictionary<string, object>
                    {
                        { "x", Math.Round(col.transform.position.x, 2) },
                        { "y", Math.Round(col.transform.position.y, 2) },
                        { "z", Math.Round(col.transform.position.z, 2) },
                    }
                },
            };
            objetosVistos.Add(objeto);

            if (i == 0)
            {
                objetoMasCercano = objeto;
            }
        }

        return new Dictionary<string, object>
        {
            { "objetos_visibles", objetosVistos },
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
}
