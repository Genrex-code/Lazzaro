using System;
using System.Collections;
using UnityEngine;
using UnityEngine.AI;

[RequireComponent(typeof(NavMeshAgent))]
[RequireComponent(typeof(Animator))]
[RequireComponent(typeof(PerceptionSystem))]
[RequireComponent(typeof(Rigidbody))]
public class MovementController : MonoBehaviour
{
    public bool IsMoving { get; private set; } = false;
    public Action<bool, string> OnMovimientoCompletado;
    public float velocidadDeseada = 3.5f;

    private NavMeshAgent navAgent;
    private Animator animator;
    private PerceptionSystem percepcion;
    private Coroutine rutinaMovimiento;
    private Rigidbody rb;

    void Awake()
    {
        navAgent = GetComponent<NavMeshAgent>();
        animator = GetComponent<Animator>();
        percepcion = GetComponent<PerceptionSystem>();
        rb = GetComponent<Rigidbody>();

        rb.freezeRotation = true;
        rb.collisionDetectionMode = CollisionDetectionMode.Continuous;

        navAgent.updatePosition = false;
        navAgent.updateRotation = false;
        navAgent.stoppingDistance = Mathf.Max(0.1f, navAgent.stoppingDistance);
    }

    public void NavegarAObjeto(string nombreObjeto)
    {
        if (!PuedeNavegar(out string mensajeError))
        {
            RechazarMovimiento(mensajeError);
            return;
        }

        Collider objetivo = percepcion.BuscarObjeto(nombreObjeto);
        if (objetivo == null)
        {
            RechazarMovimiento($"No pude encontrar el objeto '{nombreObjeto}'.");
            return;
        }

        Vector3 destino = objetivo.ClosestPoint(transform.position);
        IniciarNavegacionA(destino, objetivo.name);
    }

    public void NavegarAPosicion(Vector3 destino, string etiquetaDestino = "destino")
    {
        if (!PuedeNavegar(out string mensajeError))
        {
            RechazarMovimiento(mensajeError);
            return;
        }

        IniciarNavegacionA(destino, etiquetaDestino);
    }

    public void AlejarseDeObjeto(string nombreObjeto, float distancia)
    {
        if (!PuedeNavegar(out string mensajeError))
        {
            RechazarMovimiento(mensajeError);
            return;
        }

        Collider objetivo = percepcion.BuscarObjeto(nombreObjeto);
        if (objetivo == null)
        {
            RechazarMovimiento($"No pude encontrar el objeto '{nombreObjeto}'.");
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
        IniciarNavegacionA(destino, $"alejarme de {objetivo.name}");
    }

    public void GirarAvatar(float grados)
    {
        if (!PuedeEjecutarOrden("rotacion", out string mensajeError))
        {
            RechazarMovimiento(mensajeError);
            return;
        }

        float gradosClamped = Mathf.Clamp(grados, -360f, 360f);
        if (Mathf.Abs(gradosClamped) < 1f)
        {
            RechazarMovimiento("La rotacion solicitada es demasiado pequena.");
            return;
        }

        IniciarRutinaMovimiento(GirarHastaAngulo(gradosClamped));
    }

    public void FrenarMovimiento()
    {
        if (rutinaMovimiento != null)
        {
            StopCoroutine(rutinaMovimiento);
        }

        DetenerMovimientoActual();
    }

    private bool PuedeNavegar(out string mensajeError)
    {
        if (!PuedeEjecutarOrden("navegacion", out mensajeError))
        {
            return false;
        }

        if (!navAgent.isOnNavMesh)
        {
            mensajeError = "Mi cuerpo no esta colocado sobre un NavMesh valido.";
            return false;
        }

        return true;
    }

    private bool PuedeEjecutarOrden(string tipoOrden, out string mensajeError)
    {
        if (IsMoving)
        {
            mensajeError = $"Se recibio una nueva orden de {tipoOrden} mientras otra seguia en curso.";
            return false;
        }

        mensajeError = "";
        return true;
    }

    private void RechazarMovimiento(string mensaje)
    {
        OnMovimientoCompletado?.Invoke(false, mensaje);
    }

    private void IniciarNavegacionA(Vector3 destino, string etiquetaDestino)
    {
        if (!NavMesh.SamplePosition(destino, out NavMeshHit navHit, 2f, NavMesh.AllAreas))
        {
            RechazarMovimiento($"No encontre una ruta navegable hacia '{etiquetaDestino}'.");
            return;
        }

        IniciarRutinaMovimiento(NavegarHastaDestino(etiquetaDestino, navHit.position));
    }

    private void IniciarRutinaMovimiento(IEnumerator rutina)
    {
        if (rutinaMovimiento != null)
        {
            StopCoroutine(rutinaMovimiento);
        }

        rutinaMovimiento = StartCoroutine(rutina);
    }

    private IEnumerator NavegarHastaDestino(string nombreObjeto, Vector3 destino)
    {
        IsMoving = true;
        animator.SetBool("isWalking", true);

        navAgent.nextPosition = transform.position;
        navAgent.isStopped = false;
        navAgent.SetDestination(destino);

        yield return new WaitForFixedUpdate();

        float tiempoAtascado = 0f;

        while (DistanciaHorizontal(transform.position, destino) > navAgent.stoppingDistance)
        {
            navAgent.nextPosition = transform.position;

            if (!navAgent.pathPending && navAgent.pathStatus == NavMeshPathStatus.PathInvalid)
            {
                DetenerMovimientoActual();
                OnMovimientoCompletado?.Invoke(false, $"La ruta hacia '{nombreObjeto}' no es valida.");
                yield break;
            }

            Vector3 direccion = navAgent.steeringTarget - transform.position;
            direccion.y = 0f;

            if (direccion.sqrMagnitude > 0.01f)
            {
                AplicarMovimientoFisico(direccion.normalized);
            }
            else
            {
                DetenerVelocidadHorizontal();
            }

            if (!navAgent.pathPending && VelocidadHorizontal().magnitude < 0.2f)
            {
                tiempoAtascado += Time.fixedDeltaTime;
                if (tiempoAtascado > 2.0f)
                {
                    DetenerMovimientoActual();
                    OnMovimientoCompletado?.Invoke(
                        false,
                        $"Me atasque fisicamente intentando llegar a '{nombreObjeto}'."
                    );
                    yield break;
                }
            }
            else
            {
                tiempoAtascado = 0f;
            }

            yield return new WaitForFixedUpdate();
        }

        DetenerMovimientoActual();
        OnMovimientoCompletado?.Invoke(true, $"Llegue cerca de '{nombreObjeto}'.");
    }

    private IEnumerator GirarHastaAngulo(float grados)
    {
        IsMoving = true;
        animator.SetBool("isWalking", false);
        DetenerRutaNavMesh();

        Quaternion rotacionInicial = rb.rotation;
        Quaternion rotacionDestino = Quaternion.Euler(
            0f,
            transform.eulerAngles.y + grados,
            0f
        );
        float duracion = Mathf.Clamp(Mathf.Abs(grados) / 180f, 0.2f, 1.2f);
        float tiempo = 0f;

        while (tiempo < duracion)
        {
            tiempo += Time.fixedDeltaTime;
            float progreso = Mathf.Clamp01(tiempo / duracion);
            rb.MoveRotation(Quaternion.Slerp(rotacionInicial, rotacionDestino, progreso));
            yield return new WaitForFixedUpdate();
        }

        rb.MoveRotation(rotacionDestino);
        DetenerMovimientoActual();
        OnMovimientoCompletado?.Invoke(true, $"Gire {grados:0.#} grados.");
    }

    private void AplicarMovimientoFisico(Vector3 direccion)
    {
        Vector3 nuevaVelocidad = direccion * velocidadDeseada;
        rb.velocity = new Vector3(nuevaVelocidad.x, rb.velocity.y, nuevaVelocidad.z);

        Quaternion rotacionDestino = Quaternion.LookRotation(direccion);
        rb.MoveRotation(Quaternion.Slerp(rb.rotation, rotacionDestino, Time.fixedDeltaTime * 10f));
    }

    private void DetenerMovimientoActual()
    {
        DetenerRutaNavMesh();

        if (animator != null)
        {
            animator.SetBool("isWalking", false);
        }

        DetenerVelocidadHorizontal();
        IsMoving = false;
        rutinaMovimiento = null;
    }

    private void DetenerRutaNavMesh()
    {
        if (navAgent != null && navAgent.isOnNavMesh)
        {
            navAgent.ResetPath();
            navAgent.isStopped = true;
            navAgent.nextPosition = transform.position;
        }
    }

    private void DetenerVelocidadHorizontal()
    {
        if (rb != null)
        {
            rb.velocity = new Vector3(0f, rb.velocity.y, 0f);
        }
    }

    private Vector3 VelocidadHorizontal()
    {
        return rb == null ? Vector3.zero : new Vector3(rb.velocity.x, 0f, rb.velocity.z);
    }

    private static float DistanciaHorizontal(Vector3 a, Vector3 b)
    {
        a.y = 0f;
        b.y = 0f;
        return Vector3.Distance(a, b);
    }
}
