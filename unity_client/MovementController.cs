using System;
using System.Collections;
using UnityEngine;
using UnityEngine.AI;

[RequireComponent(typeof(NavMeshAgent))]
[RequireComponent(typeof(Animator))]
[RequireComponent(typeof(PerceptionSystem))]
public class MovementController : MonoBehaviour
{
    public bool IsMoving { get; private set; } = false;
    public Action<bool, string> OnMovimientoCompletado;

    private NavMeshAgent navAgent;
    private Animator animator;
    private PerceptionSystem percepcion;
    private Coroutine rutinaMovimiento;

    void Awake()
    {
        navAgent = GetComponent<NavMeshAgent>();
        animator = GetComponent<Animator>();
        percepcion = GetComponent<PerceptionSystem>();

        navAgent.updateRotation = true;
        navAgent.stoppingDistance = Mathf.Max(0.1f, navAgent.stoppingDistance);
    }

    public void NavegarAObjeto(string nombreObjeto)
    {
        if (IsMoving)
        {
            OnMovimientoCompletado?.Invoke(
                false,
                "Se recibio una nueva orden de navegacion mientras otra seguia en curso."
            );
            return;
        }

        Collider objetivo = percepcion.BuscarObjeto(nombreObjeto);
        if (objetivo == null)
        {
            OnMovimientoCompletado?.Invoke(false, $"No pude encontrar el objeto '{nombreObjeto}'.");
            return;
        }

        if (!navAgent.isOnNavMesh)
        {
            OnMovimientoCompletado?.Invoke(false, "Mi cuerpo no esta colocado sobre un NavMesh valido.");
            return;
        }

        Vector3 destino = objetivo.ClosestPoint(transform.position);
        if (!NavMesh.SamplePosition(destino, out NavMeshHit navHit, 2f, NavMesh.AllAreas))
        {
            OnMovimientoCompletado?.Invoke(false, $"No encontre una ruta navegable hacia '{objetivo.name}'.");
            return;
        }

        if (rutinaMovimiento != null)
        {
            StopCoroutine(rutinaMovimiento);
        }

        rutinaMovimiento = StartCoroutine(NavegarHastaDestino(objetivo.name, navHit.position));
    }

    public void NavegarAPosicion(Vector3 destino, string etiquetaDestino = "destino")
    {
        if (IsMoving)
        {
            OnMovimientoCompletado?.Invoke(
                false,
                "Se recibio una nueva orden de navegacion mientras otra seguia en curso."
            );
            return;
        }

        if (!navAgent.isOnNavMesh)
        {
            OnMovimientoCompletado?.Invoke(false, "Mi cuerpo no esta colocado sobre un NavMesh valido.");
            return;
        }

        if (!NavMesh.SamplePosition(destino, out NavMeshHit navHit, 2f, NavMesh.AllAreas))
        {
            OnMovimientoCompletado?.Invoke(false, $"No encontre una ruta navegable hacia '{etiquetaDestino}'.");
            return;
        }

        if (rutinaMovimiento != null)
        {
            StopCoroutine(rutinaMovimiento);
        }

        rutinaMovimiento = StartCoroutine(NavegarHastaDestino(etiquetaDestino, navHit.position));
    }

    public void AlejarseDeObjeto(string nombreObjeto, float distancia)
    {
        if (IsMoving)
        {
            OnMovimientoCompletado?.Invoke(
                false,
                "Se recibio una nueva orden de navegacion mientras otra seguia en curso."
            );
            return;
        }

        Collider objetivo = percepcion.BuscarObjeto(nombreObjeto);
        if (objetivo == null)
        {
            OnMovimientoCompletado?.Invoke(false, $"No pude encontrar el objeto '{nombreObjeto}'.");
            return;
        }

        Vector3 puntoCercano = objetivo.ClosestPoint(transform.position);
        Vector3 direccionEscape = transform.position - puntoCercano;
        direccionEscape.y = 0f;

        if (direccionEscape.sqrMagnitude < 0.01f)
        {
            direccionEscape = -transform.forward;
        }

        Vector3 destino = transform.position + direccionEscape.normalized * Mathf.Clamp(distancia, 0.5f, 5f);
        NavegarAPosicion(destino, $"alejarme de {objetivo.name}");
    }

    private IEnumerator NavegarHastaDestino(string nombreObjeto, Vector3 destino)
    {
        IsMoving = true;
        animator.SetBool("isWalking", true);

        navAgent.isStopped = false;
        navAgent.SetDestination(destino);

        yield return null;

        float tiempoAtascado = 0f;

        while (navAgent.pathPending || navAgent.remainingDistance > navAgent.stoppingDistance)
        {
            if (!navAgent.pathPending && navAgent.pathStatus == NavMeshPathStatus.PathInvalid)
            {
                FrenarMovimiento();
                OnMovimientoCompletado?.Invoke(false, $"La ruta hacia '{nombreObjeto}' no es valida.");
                yield break;
            }

            if (!navAgent.pathPending && navAgent.velocity.magnitude < 0.1f)
            {
                tiempoAtascado += Time.deltaTime;
                if (tiempoAtascado > 2.0f)
                {
                    FrenarMovimiento();
                    OnMovimientoCompletado?.Invoke(
                        false,
                        $"Me atasque intentando llegar a '{nombreObjeto}'."
                    );
                    yield break;
                }
            }
            else
            {
                tiempoAtascado = 0f;
            }

            yield return null;
        }

        FrenarMovimiento();
        OnMovimientoCompletado?.Invoke(true, $"Llegue cerca de '{nombreObjeto}'.");
    }

    private void FrenarMovimiento()
    {
        if (navAgent != null && navAgent.isOnNavMesh)
        {
            navAgent.ResetPath();
            navAgent.isStopped = true;
        }

        if (animator != null)
        {
            animator.SetBool("isWalking", false);
        }

        IsMoving = false;
        rutinaMovimiento = null;
    }
}
