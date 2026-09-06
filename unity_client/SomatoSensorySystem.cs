using System;
using System.Collections.Generic;
using UnityEngine;

[RequireComponent(typeof(Collider))]
[RequireComponent(typeof(Rigidbody))]
public class SomatoSensorySystem : MonoBehaviour
{
    [Header("Sistema Nervioso (BOLOGIA)")]
    [Tooltip("Fuerza minima de impacto para considerar que hubo dolor fisico.")]
    public float umbralDeDolor = 0.5f;

    [Tooltip("Segundos minimos entre alertas de dolor para no saturar el backend.")]
    public float cooldownDolorSegundos = 5.0f;

    [Tooltip("Distancia sugerida para escapar tras un impacto fuerte.")]
    public float distanciaEscapeDolor = 2.0f;

    private float tiempoUltimoDolor = -10f;
    private LazzaroAgent lazzaroAgent;
    private MovementController movimiento;

    void Awake()
    {
        lazzaroAgent = GetComponent<LazzaroAgent>();
        movimiento = GetComponent<MovementController>();
    }

    void OnCollisionEnter(Collision choque)
    {
        if (!DebeProcesarChoque(choque, out string nombreObjeto))
        {
            return;
        }

        float fuerzaDelGolpe = choque.relativeVelocity.magnitude;
        if (fuerzaDelGolpe <= umbralDeDolor)
        {
            return;
        }

        tiempoUltimoDolor = Time.time;
        Debug.LogWarning($"[DOLOR] Lazzaro se pego con '{nombreObjeto}' con fuerza de {fuerzaDelGolpe:0.##}");

        if (movimiento != null)
        {
            movimiento.FrenarMovimiento();
        }

        if (lazzaroAgent == null)
        {
            return;
        }

        string direccionEscape = CalcularDireccionEscape(choque);
        lazzaroAgent.ReportarDolor(
            causaDelDolor: ConstruirMensajeDolor(nombreObjeto, fuerzaDelGolpe),
            objetoGolpeado: nombreObjeto,
            direccionEscape: direccionEscape,
            distanciaEscape: distanciaEscapeDolor,
            accionFallida: lazzaroAgent.AccionMovimientoActiva,
            detallesExtra: CrearDetallesImpacto(nombreObjeto, fuerzaDelGolpe, choque, direccionEscape)
        );
    }

    private bool DebeProcesarChoque(Collision choque, out string nombreObjeto)
    {
        nombreObjeto = "";
        if (choque == null || choque.gameObject == null)
        {
            return false;
        }

        nombreObjeto = choque.gameObject.name;
        if (EsRuidoAmbiental(nombreObjeto))
        {
            return false;
        }

        return Time.time - tiempoUltimoDolor >= cooldownDolorSegundos;
    }

    private static bool EsRuidoAmbiental(string nombreObjeto)
    {
        return Contiene(nombreObjeto, "Plane")
            || Contiene(nombreObjeto, "Ground")
            || Contiene(nombreObjeto, "Terrain")
            || Contiene(nombreObjeto, "Flower")
            || Contiene(nombreObjeto, "Grass")
            || Contiene(nombreObjeto, "Bush");
    }

    private static bool Contiene(string texto, string fragmento)
    {
        return !string.IsNullOrEmpty(texto)
            && texto.IndexOf(fragmento, StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private string CalcularDireccionEscape(Collision choque)
    {
        Vector3 puntoContacto = choque.contactCount > 0
            ? choque.contacts[0].point
            : choque.transform.position;
        Vector3 direccionGolpe = transform.position - puntoContacto;
        direccionGolpe.y = 0f;

        if (direccionGolpe.sqrMagnitude < 0.0001f)
        {
            direccionGolpe = -transform.forward;
        }

        return Mathf.Abs(direccionGolpe.x) > Mathf.Abs(direccionGolpe.z)
            ? (direccionGolpe.x > 0f ? "derecha" : "izquierda")
            : (direccionGolpe.z > 0f ? "adelante" : "atras");
    }

    private string ConstruirMensajeDolor(string nombreObjeto, float fuerzaDelGolpe)
    {
        return $"Ay. Acabo de chocar contra {nombreObjeto}. El impacto fue de magnitud {fuerzaDelGolpe:0.##} y se sintio duro.";
    }

    private Dictionary<string, object> CrearDetallesImpacto(
        string nombreObjeto,
        float fuerzaDelGolpe,
        Collision choque,
        string direccionEscape
    )
    {
        float dolor = CalcularDolorNormalizado(fuerzaDelGolpe);
        return new Dictionary<string, object>
        {
            {
                "impacto",
                new Dictionary<string, object>
                {
                    { "tipo", "contundente" },
                    { "objeto", nombreObjeto },
                    { "velocidad_relativa", Math.Round(fuerzaDelGolpe, 2) },
                    { "dolor", Math.Round(dolor, 2) },
                    { "riesgo", ClasificarRiesgo(dolor) },
                    { "direccion_escape", direccionEscape },
                    { "contactos", choque.contactCount },
                }
            }
        };
    }

    private float CalcularDolorNormalizado(float fuerzaDelGolpe)
    {
        float umbralSuperior = Mathf.Max(umbralDeDolor * 4f, umbralDeDolor + 0.01f);
        return Mathf.Clamp01(Mathf.InverseLerp(umbralDeDolor, umbralSuperior, fuerzaDelGolpe));
    }

    private static string ClasificarRiesgo(float dolor)
    {
        if (dolor >= 0.75f)
        {
            return "alto";
        }

        if (dolor >= 0.35f)
        {
            return "medio";
        }

        return "bajo";
    }
}
