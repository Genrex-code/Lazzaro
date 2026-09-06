# ROOT-004 — Action state machine

## 1. Causa

El flujo confundía validez de decisión, requisito de autorización, permiso, dispatch, ejecución y resultado. En particular, `requiere_confirmacion=True` significaba “esperar” para Orchestrator y “autorizado” para ActionExecutor; Unity convertía la espera en `exito=False`; Identity convertía una decisión válida en éxito biológico; y Behavior podía amplificar un outcome mediante eventos recursivos.

## 2. Tickets absorbidos

ROOT-004 cierra DTO-CONTRACT-004, TOOL-CONTRACT-002, STATE-AUDIT-001 y STATE-AUDIT-002. No absorbe ROOT-005, ROOT-007, ROOT-011, ROOT-015, ROOT-016, ROOT-038, ROOT-043 ni ROOT-054.

## 3. Semantics map before

| Concepto | Productor anterior | Representación | Consumidor/side effect | Durable | Problema |
|---|---|---|---|---|---|
| `ActionResponse.valido` | Motor/Persona/Oracle | bool | Audit e Identity | no | Validez de contrato parecía éxito |
| `ActionResponse.error` | capa decisora | texto opcional | logger, Audit, Identity | no | Ausencia de error parecía éxito |
| `AccionSaliente.requiere_confirmacion` | adapter | bool | Orchestrator, executor, Unity | sí en action v1 | requisito y permiso colapsados |
| `ToolRegistry.requires_confirmation` | registro de tool | bool | adapter/executor | no | executor buscaba permiso en otro flag de requisito |
| guard de `ActionExecutor` | executor | `accion.requiere_confirmacion is True` | handler | no | permitía ejecutar sin aprobación separada |
| `_gestionar_ejecucion` | Orchestrator | cadenas | memoria/Audit | no | lifecycle implícito |
| `sin_accion` | Orchestrator | cadena | Audit skipped | no | no state durable |
| `pendiente_confirmacion` | Orchestrator | cadena | Audit pending | no | Unity lo convertía en failure |
| `despachada` | Orchestrator | cadena | Audit pending | no | indistinguible de espera en Audit |
| `ok` / `error` | ToolResult adapter | cadena | memoria/Audit/alias | no | terminal sin transición durable |
| `AuditTrace.execution.status` | observer | enum v1 | diagnóstico | JSONL | pending agrupa espera y dispatch deliberadamente |
| `RespuestaAPI.status` | route | ok/error/pendiente | clientes | no | `status != error` era ejecutable en Unity |
| `ActionLedger.requires_confirmation` | Orchestrator | bool | ledger/query | sí | sólo conservaba requisito |
| `ActionLedger.outcome_expected` | Orchestrator | bool | diagnóstico | sí | no era estado actual |
| `outcome.exito` | cliente/tool | bool estricto | Behavior/Task/Identity | sí | no había guard de estado |
| `outcome.consumed_at` | Orchestrator | timestamp | deduplicación de consumidores | sí | separaba consumo, no lifecycle |
| Unity response | API client | DTO | agent | no | pending/unknown podían pasar |
| Unity execution | agent | switch por nombre | materializer | no | no emitía start |
| Identity success | Orchestrator | `valido && !error` | estado biológico | no | decision validity era execution success |
| Behavior result | API outcome | evento + bool | State/Personality/Task | parcialmente | detectores podían generar recursión |

## 4. Transition map before

- Externa sin confirmación: decisión → action v1 → `despachada` → API ok → Unity → outcome.
- Externa con confirmación: decisión → `pendiente_confirmacion` → API ok → Unity omite → outcome false.
- Sandbox: decisión → executor directo → `ok/error` → outcome local.
- `hablar`: dispatch → UI/log Unity, sin outcome.
- `none`: action registrada → `sin_accion`, pero Identity aún podía interpretar validez como éxito.
- Decisión inválida: degradación a `none`, con fallo cognitivo confundido con fallo operacional.
- Outcome: correlación ROOT-003 → consumidores → `consumed_at`, sin legalidad de estado.
- Cancelación: `cancelar_tarea` era una action normal; no existía cancelación genérica del lifecycle.

## 5. Separación decision/auth/execution

`decision_valid` sólo afirma que la salida de la capa decisora cumple contrato. `requires_confirmation` sólo expresa requisito de policy. `authorized` es el permiso durable. `dispatched` es release de Core. `executing` exige receipt observable. `reported_success/failure` sólo aparece desde un resultado estricto legal.

## 6. Enum final

`legacy_unknown`, `created`, `awaiting_authorization`, `authorized`, `dispatched`, `executing`, `reported_success`, `reported_failure`, `rejected`, `skipped`. No se añadió `cancelled`: no existe productor/consumidor real; `cancelar_tarea` sigue siendo una action.

## 7. Transition table final

| From | Event | To | Side effect permitido |
|---|---|---|---|
| inexistente | `action_recorded` | `created` | persistir identidad |
| `created` | `require_authorization` | `awaiting_authorization` | ninguno |
| `created` | `auto_authorize` | `authorized` | permiso durable |
| `created` | `skip` | `skipped` | terminal neutral |
| `awaiting_authorization` | `authorize` | `authorized` | permiso explícito |
| `awaiting_authorization` | `reject` | `rejected` | terminal neutral |
| `authorized` | `dispatch` | `dispatched` | release externo |
| `authorized` | `start_execution` | `executing` | entrar al handler local |
| `dispatched` | `start_execution` | `executing` | receipt del cliente |
| `executing` | `report_success/failure` | terminal reportado | habilitar consumidores |
| `dispatched` | `report_success/failure` | terminal reportado | compatibilidad ROOT-003, sin inventar start |

Todo arco no listado es ilegal. Un terminal nunca reabre.

## 8. ActionLedger schema evolution

Schema v2 añade a `actions`: `decision_valid`, `action_state`, `state_updated_at`. Añade `action_transitions` para historia aplicada y `transition_receipts` para idempotencia/clasificación. SQLite sigue siendo el único owner durable.

## 9. Migration

La migración v1→v2 usa `ALTER TABLE` no destructivo dentro de transacción. Todas las actions históricas quedan `legacy_unknown` y `decision_valid=NULL`; conserva transaction, action, outcome y `consumed_at`. No deriva success aunque exista un outcome v1.

## 10. Transition receipts

`ActionTransitionResult` contiene `action_id`, `state_before`, `state_after`, `classification`, timestamp y receipt ID opcional. Las clasificaciones son `APPLIED`, `ALREADY_APPLIED`, `INVALID_TRANSITION`, `UNKNOWN_ACTION` y `CONFLICT`. Misma key+mismo contenido es idempotente; misma key+otro contenido es conflicto.

## 11. Authorization protocol

Se usa el endpoint existente con `EventoEntrante(tipo_evento="confirmacion_accion")`, `detalles.id_accion` exacto y `detalles.approved` bool estricto. La transaction de confirmación sólo es receipt: nunca reemplaza `action.transaction_id` ni vuelve a ejecutar Triage, IA, Behavior del comando, resolución de target o alias candidate.

## 12. Approval

Un approve legal aplica `awaiting_authorization→authorized` sobre el mismo ID. Para cliente externo aplica después `authorized→dispatched`; para sandbox aplica `authorized→executing`, ejecuta con permit y registra terminal reportado.

## 13. Denial

Deny aplica `awaiting_authorization→rejected`. Es terminal neutral: cero executor, cero dispatch, cero attempt de Task, cero failure de Identity y cero alias learning.

## 14. Executor contract

`ActionExecutor.ejecutar` acepta un `ExecutionPermit` tipado. Un tool cuyo registry exige confirmación no corre sin permit matching para el mismo action ID en `executing`. `AccionSaliente.requiere_confirmacion` nunca prueba aprobación.

## 15. External dispatch

`dispatched` significa únicamente “Core liberó la action hacia la frontera de transporte”. No afirma que HTTP llegó, que Unity recibió ni exactly-once delivery. Esas garantías permanecen en ROOT-054.

## 16. Execution started

Unity encola `inicio_ejecucion_accion` con el ID exacto justo antes del switch/materializador. Sólo ese receipt mueve una action externa `dispatched→executing`; HTTP 200 no lo hace.

## 17. Success/failure semantics

Sólo `ToolResult.exito` estricto después de start local o `resultado_accion.detalles.exito` estricto desde un estado legal produce `reported_success/failure`. Espera, deny, decisión inválida, ausencia de action, missing authorization y dispatch no son failures.

## 18. Boundary con ROOT-005

`reported_success` significa “el consumidor informó success”. ROOT-004 no demuestra que el mundo materializó el efecto. La attestation material de interactuar/agarrar/soltar/aplicar fuerza sigue íntegramente en ROOT-005.

## 19. Sandbox

El camino síncrono es `created→authorized→executing→reported_success/failure`. El ledger emite permit antes del handler; ToolResult se guarda como outcome local y sólo entonces se proyecta una vez a Behavior/Task/Identity.

## 20. Hablar/none

`none` termina en `skipped` y nunca es failure. En Unity, `hablar` usa `dispatched→executing→reported_success` después de UI/log; el evento outcome se vuelve pasivo para evitar el bucle. En terminal puede permanecer `dispatched` porque no existe receipt físico del cliente.

## 21. API status vs action state

`RespuestaAPI.status` describe procesamiento. Una action waiting devuelve `status=pendiente` y `estado_ejecucion=awaiting_authorization`; el resto de respuestas válidas usa `ok`. Ningún status implica success operacional.

## 22. Unity

El DTO incluye `estado_ejecucion` e `id_transaccion_origen`. ApiClient acepta sólo `ok`, `pendiente` o `error`; desconocidos fallan cerrado. Agent guarda waiting por ID, no ejecuta ni reporta false, confirma por ID, ejecuta sólo `dispatched`, encola start y conserva `ContextoAccion` exacto.

## 23. Task

En producción sólo un outcome terminal aceptado y correlacionado registra intento. Waiting, autorización, deny, dispatch, executing, duplicate, unknown e invalid transition no incrementan intentos, fallos ni frustración.

## 24. IdentityState

Antes recibía `success=valido&&!error`. Ahora acepta `bool|None`: `None` mantiene únicamente el coste cognitivo de ruta sin penalización de fallo. Orchestrator envía bool sólo desde outcome terminal legal o ToolResult local terminal.

## 25. Recursive outcome fix

Behavior sigue calculando y exponiendo loop, estancamiento y compulsión en `TaskContext.obtener_resumen()`, pero ya no llama recursivamente `procesar_evento`. Un outcome produce una sola aplicación canónica de State/Personality.

## 26. Concurrency

Cada transición abre `BEGIN IMMEDIATE` bajo `RLock`; check, update, historia y receipt están dentro de la misma transacción. Dos authorize/start producen un aplicado y un idempotente. Success/failure concurrentes dejan un solo terminal; el perdedor no sobrescribe.

## 27. Restart

Snapshot e historia viven en SQLite. Waiting y executing sobreviven close/reopen sin inferir completion. Receipt IDs conservan idempotencia después de restart.

## 28. Crash windows

Siguen representables sin convertir incertidumbre en success: authorized antes de dispatch; dispatched antes del receipt del cliente; cliente iniciado antes de que server vea start; executing antes de outcome; outcome durable antes de consumidores/`consumed_at`. Reconciliación corresponde a ROOT-007/054.

## 29. AuditTrace

AuditTrace v1 permanece congelado. Cada invocación conserva su trace independiente; el ledger reconstruye el lifecycle multi-evento. `pending` puede seguir observando espera o dispatch sin convertirse en autoridad.

## 30. Privacy

Las tablas nuevas sólo guardan IDs, estados, eventos, clasificación y timestamps. No guardan prompt, pensamiento, contexto de memoria, secretos, payload completo ni excepción cruda.

## 31. Neighboring roots

ROOT-005 queda OPEN/READY. ROOT-007, ROOT-011, ROOT-015, ROOT-016, ROOT-043 y ROOT-054 permanecen OPEN. ROOT-038 no fue iniciado. No se añadieron DevelopmentalCore, Persona rework, TCM, LoRA ni transporte completo.

## 32. Tests

`core/test_action_state_machine.py` contiene 48 pruebas focales: tabla legal/ilegal, confirmación, executor, API ASGI, Unity estático, Behavior/Identity, restart, concurrencia y fixture v1. Resultado final: ROOT-004 48/48, ROOT-003 29/29, ROOT-012 15/15, ROOT-006 21/21, ROOT-001 14/14 y ROOT-002 13/13. `regression_probe.py` ejecutó 301 unidades: 300 PASS, un KNOWN_XFAIL histórico y cero fallos inesperados; suite/isolation gates PASS. `compileall`, diff check y validación C# estática pasaron. Los tres SHA-256 de Chroma coinciden con la entrada y no se creó ledger runtime.

## 33. Criterion

El cierre exige que sólo ActionLedger sea autoridad, que requisito no sea permiso, que waiting sea neutral, que executing sea observable, que outcomes ilegales no lleguen a consumidores, que Task/Identity sólo respondan a terminal legal, y que las regresiones, hashes y aislamiento permanezcan verdes.

## 34. Final state

ROOT-004 queda CLOSED. El field test no se ejecutó porque la cláusula de aislamiento de este packet lo prohíbe; no se abrió Unity real ni un ledger productivo. ROOT-005 queda OPEN/READY FOR FIX y no fue iniciado.
