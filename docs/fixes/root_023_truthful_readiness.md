# ROOT-023 — Truthful runtime readiness

Estado de código: `FIXES_COMPLETE / HUMAN_REVIEW_PENDING`.

ROOT-023 separa liveness, readiness operacional y estado cognitivo. La API
puede estar viva aunque alguna capacidad de LAZZARO esté reducida; a su vez,
`/api/v1/estado` continúa siendo exclusivamente una proyección del dominio.

## Autoridad y lifecycle

`RuntimeCapabilityHolder` es la única autoridad de readiness. La aplicación lo
posee durante toda su vida y cada entrada al lifespan abre una generación con
UUID nuevo. El holder reemplaza snapshots completos bajo un lock corto; los
snapshots y sus entradas son dataclasses congeladas y las capacidades se
almacenan como tupla. Los lectores reciben una referencia inmutable coherente.

AI y Memory reciben un `RuntimeCapabilityEvidenceSink` ligado al UUID de la
generación. Todo update exige que ese UUID siga abierto y vigente. Al comenzar
shutdown se cierra la generación antes de liberar recursos; resultados tardíos,
resultados de una generación anterior y updates posteriores al cierre se
rechazan. Si startup no llegó a completarse, el cierre conserva
`STARTUP_INCOMPLETE`; sólo una generación que alcanzó startup completo termina
como `SHUTDOWN`. ROOT-022 conserva su ownership y cierre LIFO original.

No hay polling, TTL, thread de monitorización, probe adicional ni segunda
operación de memoria. El snapshot sólo expresa la última evidencia producida
por el flujo normal.

## Contrato interno

Estados globales:

```text
READY
DEGRADED
UNAVAILABLE
```

Completitud de evidencia:

```text
PARTIAL
COMPLETE
```

Capacidades observadas:

```text
orchestration
llm_generation
llm_plan
memory_read
memory_write
```

Cada una conserva `UNKNOWN`, `AVAILABLE`, `DEGRADED` o `UNAVAILABLE`, un
`reason_code` finito y un timestamp interno. `UNKNOWN` nunca degrada el global:
una composición válida sin reducción conocida puede publicar `READY/PARTIAL`.
`COMPLETE` sólo significa que todas las capacidades recibieron evidencia
concluyente en la generación; no promete vigencia eterna.

`UNAVAILABLE` global corresponde a orchestration no publicada, startup
incompleto o shutdown. Con orchestration disponible, una reducción conocida
produce `DEGRADED`; en ausencia de reducciones conocidas el estado es `READY`.

## Evidencia LLM

`FallbackAIClient` publica el mismo `LLMCallResultV1` que devuelve al llamador.
El health de startup expone ese recibo tipado sin ejecutar un segundo probe.

- `SUCCESS` en health/text habilita `llm_generation`.
- `SUCCESS` en plan habilita `llm_generation` y `llm_plan`.
- Un primario fallido seguido de fallback compatible exitoso conserva la
  capacidad efectiva como `AVAILABLE`.
- `NO_COMPATIBLE_PROVIDER` sólo vuelve `llm_plan` indisponible.
- Sólo `NON_RETRYABLE_FAILURE` o `ALL_PROVIDERS_FAILED`, cuyos receipts sean
  exclusivamente `AUTH_FAILURE`, `CONFIGURATION_ERROR` o
  `PROJECTION_UNSUPPORTED`, demuestran resolución exhaustiva e indisponibilidad
  de la capacidad correspondiente.
- `DEADLINE_EXCEEDED` nunca prueba cobertura exhaustiva, aunque todos los
  receipts alcanzados antes del deadline sean estructurales.
- Deadline, rate limit, timeout, conexión y fallos HTTP transitorios son
  outcomes de request: no alteran una capacidad previamente disponible ni
  convierten un `UNKNOWN` en diagnóstico inventado.
- Un health inicial fallido sin evidencia estructural deja generación
  `DEGRADED`; un éxito posterior recupera la capacidad en la misma generación.

Los nombres/orden de candidatos, modelos y fallos privados no entran al DTO.
ROOT-021 conserva budgets, retries, deadlines y terminal taxonomy; ROOT-033
conserva toda futura política de calidad o preferencia entre providers.

## Evidencia de memoria

`MemoryManager` publica exactamente los receipts ya obtenidos:

| Receipt | Capacidad efectiva |
| --- | --- |
| read `OK` / `OK_EMPTY` | `memory_read=AVAILABLE` |
| read `PARTIAL` | `memory_read=DEGRADED` |
| read agregado `FAILED` | `memory_read=UNAVAILABLE` (ambas fuentes fallaron) |
| write durable `CREATED` / `ALREADY_EXISTS` | `memory_write=AVAILABLE` |
| write `FAILED` / `AMBIGUOUS` / no durable | `memory_write=DEGRADED` |
| `reconciliation_required` | `memory_write=DEGRADED` |
| rechazo `INVALID_INTENT` / `IDEMPOTENCY_CONFLICT` | sin cambio de health |

Una colección Chroma ausente constituye indisponibilidad estructural de read y
write. Un ledger cerrado constituye indisponibilidad estructural de write.
Construcción exitosa de objetos no se promociona por sí sola a disponibilidad.
Receipts posteriores sanos recuperan la capacidad dentro de la generación.

## HTTP público

```text
GET /api/v1/health/live
  200 mientras la API puede responder

GET /api/v1/health/ready
  READY       -> 200
  DEGRADED    -> 200
  UNAVAILABLE -> 503
```

El DTO v1 de readiness contiene sólo:

```text
version
overall
verification
capabilities.<name>.status
capabilities.<name>.reason_code
```

No expone cadenas de providers, modelos, URLs, configuración, prompts,
contenido de memoria, exception messages ni tracebacks. Liveness no afirma
salud cognitiva. `/api/v1/estado` no se reutiliza ni cambia de semántica.

## Startup y fail-soft

El mensaje histórico `Sistemas nominales` fue retirado. Después de publicar
Orchestrator se registra el `overall` y `verification` reales; un estado
degradado usa warning. Los sinks de observación son fail-soft y nunca sustituyen
el resultado LLM o Memory. Un fallo exclusivamente de logging no altera ninguna
capacidad, preservando ROOT-031.

## Pruebas permanentes

`core/test_runtime_capabilities.py` cubre estados globales y completitud,
transiciones UNKNOWN/AVAILABLE/DEGRADED/UNAVAILABLE, recuperación, fallos
transitorios, fallo estructural, fallback compatible, all-providers-failed,
PromptPlan incompatible, health distinto de plan, todos los receipts de
memoria requeridos, rechazos semánticos, composición estructural, publicación
sin segunda búsqueda, startup incompleto, shutdown, dos generaciones y rechazo
de resultados tardíos.

`api/test_runtime_readiness.py` cubre status HTTP, forma allowlisted, redacción,
liveness independiente y separación de `/estado`.

Las regresiones ROOT-021, ROOT-022, ROOT-031, AI, Memory, API y lifecycle, la
suite completa, compileall, diff-check e integridad de las DB persistentes se
registran en el packet final de HUMAN REVIEW.

## Alcance preservado

ROOT-023 no cambia provider ordering, retries, deadlines, routing, schemas de
memoria, ownership de recursos ni inicialización de logging. No implementa ni
absorbe ROOT-030 (cache Chroma cross-path), ROOT-032 (canonical config paths) o
ROOT-033 (provider policy).

Riesgo residual: sin polling ni TTL, readiness expresa únicamente la última
evidencia observada. Un proveedor o backend puede cambiar después de esa
observación; el siguiente receipt normal actualiza el snapshot sin trabajo
adicional de monitorización.
