# ROOT-054 — Transport ACK, retry and reconciliation

## Estado

`IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`

ROOT-054 corrige la ventana en la que Unity podía perder una respuesta y no
saber si el backend había procesado el request. El contrato no crea acciones,
no decide autoridad física y no interpreta éxito material. Su única autoridad
es el estado de delivery, retry y reconciliación.

## Separación de estados

`DeliveryResolutionV1` mantiene explícitamente:

- `NOT_FOUND`: ausencia demostrada de la transacción identificada;
- `UNKNOWN`: evidencia durable insuficiente;
- `UNVERIFIABLE`: request sin identidad V1 o fila histórica sin fingerprint;
- `IN_PROGRESS`;
- `COMPLETED_NO_ACTION` / `COMPLETED_WITH_ACTION`;
- `FAILED_TERMINAL`;
- `REQUIRES_RECONCILIATION`.

La única pareja que habilita reenvío es:

```text
NOT_FOUND + RETRY_SAME_TX
```

Los validadores Python, el DTO público y el boundary Unity rechazan cualquier
otra combinación. La matriz causal completa también rechaza receipts cuya
`resolution`, `directive`, clase o status sean incompatibles; un
`UNKNOWN + RESOLVED` jamás puede aparentar un ACK de inicio. Un request legacy
sin `request_identity_version` y
`request_fingerprint_sha256` produce `UNVERIFIABLE /
REQUIRES_RECONCILIATION`; nunca se transforma en `NOT_FOUND`.

## Identidad del request

La identidad V1 se calcula con SHA-256 sobre JSON UTF-8 canónico de:

```text
detalles
id_transaccion
objeto_involucrado
origen
tipo_evento
```

El timestamp, número de intento, backoff y estado local de delivery no forman
parte de esa identidad. Python valida el fingerprint al ingresar el evento y
Unity lo calcula antes de persistir el outbox. Un probe C# real compara ambos
resultados byte por byte.

Las filas históricas de ActionLedger conservan `NULL` en los campos de
identidad. La migración no les atribuye retrospectivamente un fingerprint.

La relación entre identidad durable y entrante se clasifica una sola vez como
`LEGACY_COMPATIBLE`, `VERIFIED_MATCH`, `VERIFIED_CONFLICT` o `UNVERIFIABLE`.
Sólo legacy/legacy conserva el replay histórico y sólo V1/V1 exacto acredita
equivalencia moderna. Mezclar una fila legacy con un request V1 —o una fila V1
con un request legacy— produce `UNVERIFIABLE / REQUIRES_RECONCILIATION`; nunca
reutiliza una respuesta histórica ni altera la fila durable.

## Reconciliación durable

`POST /api/v1/transacciones/reconciliar` es read-only. Consulta:

- transaction y Turn UoW;
- transition receipt para `confirmacion_accion` e
  `inicio_ejecucion_accion`;
- outcome attempt para `resultado_accion`;
- la acción durable original cuando corresponde.

No reclama una transacción, no repite Behavior/Triage/Memory y no produce una
nueva acción. Una confirmación perdida recupera la misma acción desde
ActionLedger; una acción normal completada reutiliza la respuesta canónica ya
guardada.

Un HTTP 2xx sólo confirma transporte. Para confirmación, inicio y outcome,
Unity consulta después el receipt durable. La respuesta de reconciliación pasa
por un boundary raw estricto y una acción recuperada vuelve a atravesar la
fachada ROOT-038.

## Handoff físico ACK-first

El orden Unity queda congelado así:

```text
acción dispatched
→ persistir/enviar inicio_ejecucion_accion
→ reconciliar ACTION_LIFECYCLE receipt
→ APPLIED o ALREADY_APPLIED
→ persistir EXECUTION_HANDOFF
→ invocar handler físico
→ persistir resultado_accion en outbox
→ retirar EXECUTION_HANDOFF
```

El `switch` de handlers no es alcanzable desde la respuesta `dispatched`. Si
Unity reinicia con un `EXECUTION_HANDOFF`, no reconstruye ni repite el efecto:
queda pausado como ejecución ambigua. Esta regla prioriza at-most-once sobre
una reejecución potencialmente peligrosa.

## Outbox local

`UnityDeliveryOutboxV1` persiste atómicamente, antes del primer envío:

- request original e identidad;
- estado de delivery;
- contadores de envío/reconciliación;
- última clase estable de fallo;
- timestamp local de actualización.

No almacena `authority_decision_id`, `canonical_parameters_json`,
`action_binding_sha256` ni otra autoridad de acción. Tras un reinicio todos los
requests no terminales se reconcilian primero, incluso los que quedaron
`QUEUED`; nunca se asume que no llegaron.

Los fallos ambiguos pasan a `UNKNOWN`. Las consultas read-only de
reconciliación pueden repetirse con límite acotado. El evento original sólo se
reencola cuando el servidor devuelve `NOT_FOUND / RETRY_SAME_TX`, conservando
el mismo ID, payload y fingerprint.

## Receipts

| Evento | Evidencia requerida |
| --- | --- |
| request ordinario | transaction `COMMITTED` y respuesta durable |
| `confirmacion_accion` | transition receipt `APPLIED/ALREADY_APPLIED` |
| `inicio_ejecucion_accion` | transition receipt `APPLIED/ALREADY_APPLIED` |
| `resultado_accion` | outcome receipt `ACCEPTED/DUPLICATE` |

Receipt faltante, identidad incompatible o contexto de ejecución perdido no
habilitan retry físico: requieren reconciliación o intervención posterior.

## Ownership preservado

- ROOT-003/ActionLedger: transaction, UoW, action identity y receipts.
- ROOT-029: target, freshness, authority y binding físico.
- ROOT-038: contrato wire Python↔Unity.
- ROOT-042: contrato runtime de acciones Motor.
- ROOT-054: delivery, retry, reconciliación y outbox.

ROOT-054 no declara material success, no cambia lifecycle y no sustituye una
acción o target.

## Pruebas permanentes

Las regresiones cubren:

- separación `NOT_FOUND/UNKNOWN/UNVERIFIABLE`;
- retry exclusivamente tras `NOT_FOUND` demostrado;
- identidad legacy fail-closed;
- receipts de confirmación, inicio y outcome;
- recuperación de acción confirmada;
- 2xx no universal;
- orden ACK-first antes de handlers;
- reinicio sin reejecución de handoff;
- persistencia real del outbox con Mono/Newtonsoft de Unity;
- fingerprint Python/C# idéntico;
- boundary estricto de reconciliación y reutilización de ROOT-038;
- errores públicos redactados.

No requieren red, LLM real, Unity Play Mode ni ejecución física.

## Riesgo residual de campo

La validación de código no sustituye la inyección de fallos en dispositivo.
ROOT-054 continúa requiriendo pruebas de campo posteriores para desconexión,
scene reload y process restart durante cada ventana del handoff. Esas pruebas
deben demostrar `material effects por action_id <= 1` sin cambiar las
autoridades descritas arriba.
