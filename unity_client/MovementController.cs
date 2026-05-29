using System;
using System.Collections;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

public class MovementController : MonoBehaviour
{
    public bool IsMoving { get; private set; } = false;

    private LazzaroAgent agente;
    private Rigidbody cuerpoRigido;

    void Awake()
    {
        agente = GetComponent<LazzaroAgent>();
        cuerpoRigido = GetComponent<Rigidbody>();
    }

    public void EjecutarMovimiento(JObject parametros)
    {
        if (IsMoving)
        {
            agente.ReportarResultadoAccion(
                "moverse_entorno_3d",
                false,
                "Se recibio una nueva orden de movimiento mientras otra seguia en curso."
            );
            return;
        }

        string direccion = parametros["direccion"]?.ToString() ?? "adelante";
        float distancia = parametros["distancia"]?.Value<float>() ?? 1.0f;
        string velocidad = parametros["velocidad"]?.ToString() ?? "caminar";
        float rotacionY = parametros["rotacion_y"]?.Value<float>() ?? transform.eulerAngles.y;

        if (!TryResolverDireccion(direccion, out Vector3 vectorMovimiento))
        {
            agente.ReportarResultadoAccion(
                "moverse_entorno_3d",
                false,
                $"Direccion no valida recibida: '{direccion}'."
            );
            return;
        }

        StartCoroutine(MoverSuavemente(direccion, vectorMovimiento * distancia, velocidad, rotacionY));
    }

    private IEnumerator MoverSuavemente(string direccion, Vector3 desplazamiento, string velocidad, float rotacionY)
    {
        IsMoving = true;
        Quaternion rotacionObjetivo = Quaternion.Euler(0f, rotacionY, 0f);
        if (cuerpoRigido != null)
        {
            cuerpoRigido.transform.rotation = rotacionObjetivo;
        }
        else
        {
            transform.rotation = rotacionObjetivo;
        }

        Vector3 posicionInicial = transform.position;
        Vector3 posicionFinal = posicionInicial + desplazamiento;
        float radioEsfera = Mathf.Max(0.1f, transform.localScale.y / 2f);
        float tiempoDeViaje = CalcularTiempoViaje(velocidad, desplazamiento.magnitude);
        float tiempoTranscurrido = 0f;

        if (TryDetectarObstaculo(desplazamiento, radioEsfera, out RaycastHit obstaculo))
        {
            string nombreObstaculo = obstaculo.collider != null ? obstaculo.collider.name : "obstaculo";
            Debug.LogWarning($"Choque detectado contra {nombreObstaculo}.");
            agente.ReportarDolor(
                $"Choque frontal contra '{nombreObstaculo}'. Movimiento cancelado.",
                nombreObstaculo,
                ObtenerDireccionOpuesta(direccion),
                Mathf.Max(1f, desplazamiento.magnitude * 0.5f),
                "moverse_entorno_3d"
            );
            IsMoving = false;
            yield break;
        }

        while (tiempoTranscurrido < tiempoDeViaje)
        {
            float progreso = tiempoDeViaje > 0f ? tiempoTranscurrido / tiempoDeViaje : 1f;
            Vector3 posicionHorizontalIdeal = Vector3.Lerp(posicionInicial, posicionFinal, progreso);
            posicionHorizontalIdeal.y = ObtenerAlturaSuelo(posicionHorizontalIdeal, radioEsfera, transform.position.y);

            transform.position = posicionHorizontalIdeal;
            tiempoTranscurrido += Time.deltaTime;
            yield return null;
        }

        posicionFinal.y = ObtenerAlturaSuelo(posicionFinal, radioEsfera, transform.position.y);
        transform.position = posicionFinal;
        IsMoving = false;

        agente.ReportarResultadoAccion(
            "moverse_entorno_3d",
            true,
            "Movimiento completado correctamente.",
            new Dictionary<string, object>
            {
                { "direccion", direccion },
                { "distancia_recorrida", Math.Round(desplazamiento.magnitude, 2) },
                { "velocidad", velocidad },
                { "rotacion_y", Math.Round(rotacionY, 2) },
            }
        );
    }

    private static bool TryResolverDireccion(string direccion, out Vector3 movimiento)
    {
        switch (direccion.ToLowerInvariant())
        {
            case "adelante":
                movimiento = Vector3.forward;
                return true;
            case "atras":
                movimiento = Vector3.back;
                return true;
            case "izquierda":
                movimiento = Vector3.left;
                return true;
            case "derecha":
                movimiento = Vector3.right;
                return true;
            default:
                movimiento = Vector3.zero;
                return false;
        }
    }

    private static string ObtenerDireccionOpuesta(string direccion)
    {
        switch (direccion.ToLowerInvariant())
        {
            case "adelante":
                return "atras";
            case "atras":
                return "adelante";
            case "izquierda":
                return "derecha";
            case "derecha":
                return "izquierda";
            default:
                return "atras";
        }
    }

    private static float CalcularTiempoViaje(string velocidad, float distancia)
    {
        float multiplicador = 1f;
        switch ((velocidad ?? "caminar").ToLowerInvariant())
        {
            case "correr":
                multiplicador = 0.6f;
                break;
            case "sigilo":
                multiplicador = 1.5f;
                break;
            default:
                multiplicador = 1f;
                break;
        }

        return Mathf.Max(0.35f, distancia * 0.4f * multiplicador);
    }

    private bool TryDetectarObstaculo(Vector3 desplazamiento, float radioEsfera, out RaycastHit obstaculo)
    {
        obstaculo = default;
        if (desplazamiento == Vector3.zero)
        {
            return false;
        }

        Vector3 origen = transform.position + Vector3.up * radioEsfera;
        RaycastHit[] hits = Physics.SphereCastAll(
            origen,
            radioEsfera * 0.8f,
            desplazamiento.normalized,
            desplazamiento.magnitude,
            ~0,
            QueryTriggerInteraction.Ignore
        );

        foreach (RaycastHit hit in hits)
        {
            if (EsObstaculoIgnorable(hit.collider))
            {
                continue;
            }

            obstaculo = hit;
            return true;
        }

        return false;
    }

    private bool EsObstaculoIgnorable(Collider collider)
    {
        if (collider == null || collider.gameObject == gameObject || collider.isTrigger)
        {
            return true;
        }

        string nombre = collider.name.ToLowerInvariant();
        return nombre.Contains("ground") || nombre.Contains("terrain");
    }

    private bool EsSueloIgnorable(Collider collider)
    {
        return collider == null || collider.gameObject == gameObject || collider.isTrigger;
    }

    private float ObtenerAlturaSuelo(Vector3 posicionHorizontalIdeal, float radioEsfera, float alturaFallback)
    {
        Vector3 origenRayo = new Vector3(posicionHorizontalIdeal.x, 20f, posicionHorizontalIdeal.z);
        RaycastHit[] hitsSuelo = Physics.RaycastAll(
            origenRayo,
            Vector3.down,
            40f,
            ~0,
            QueryTriggerInteraction.Ignore
        );

        float alturaSuelo = float.NegativeInfinity;
        foreach (RaycastHit hit in hitsSuelo)
        {
            if (EsSueloIgnorable(hit.collider))
            {
                continue;
            }

            if (hit.point.y > alturaSuelo)
            {
                alturaSuelo = hit.point.y;
            }
        }

        if (float.IsNegativeInfinity(alturaSuelo))
        {
            return alturaFallback;
        }

        return alturaSuelo + radioEsfera;
    }
}
