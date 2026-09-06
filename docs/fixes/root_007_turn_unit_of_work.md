# ROOT-007 — Turn Unit of Work

## 1. ROOT

ROOT-007 absorbe `ORCH-AUDIT-003`, `ORCH-AUDIT-004` y `EXEC-AUDIT-003`. El alcance es una unidad de trabajo durable por turno, rollback local donde es seguro y cierre fail-closed cuando un efecto no puede deshacerse o queda ambiguo. No promete ACID entre SQLite, Chroma, Unity ni otros procesos.

## 2. Causa

El flujo histórico mezclaba mutaciones de evento, decisión, ejecución y bookkeeping sin punto de commit común. Una excepción podía dejar efectos aplicados, devolver `None`, publicarse como éxito y bloquear o duplicar un retry sin explicar qué había ocurrido. ROOT-003 aportó identidad e idempotencia de transacción; faltaba modelar el estado operacional del primer intento y sus efectos.

## 3. Tickets absorbidos

`ORCH-AUDIT-003` se cierra con checkpoint/restore owner-local en `BehaviorCore`; `ORCH-AUDIT-004`, con una frontera tipada uniforme para fallos centrales, incluido Motor; `EXEC-AUDIT-003`, impidiendo que una decisión inválida produzca efectos normales o se convierta en una salida terminal válida.

## 4. Side-effect map before

| Orden | Operación | Owner | R/W | Medio | Reversible | Idempotente antes | Puede fallar | Fallo histórico | Clase | Policy UoW final |
|---:|---|---|---|---|---|---|---|---|---|---|
| 1 | claim de transacción | ActionLedger | W | SQLite | no | sí | sí | gap claim/UoW | bookkeeping | claim + `OPEN` atómicos |
| 2 | confirmación/inicio lifecycle | ActionLedger | W | SQLite | no | sí | sí | transición parcial | execution | receipt requerido |
| 3 | recepción de outcome | ActionLedger | W | SQLite | no | sí | sí | outcome durable sin consumers | outcome | receipt requerido |
| 4 | despertar descanso terminal | Orchestrator | W | RAM | sí | no | sí | estado local opaco | event | receipt opcional |
| 5 | State/Personality/Task/percepción/constelación | BehaviorCore y owners | W | RAM | sí dentro del call | no | sí | mutación sobrevivía excepción | event/outcome | checkpoint owner-local + receipt requerido |
| 6 | Triage y adaptación de canal | Triage/Orchestrator | R | RAM/modelo local | n/a | n/a | sí | fallo confundido con no-op | decisión pura | fallo tipado; sin efecto inventado |
| 7 | recuperación de memoria | MemoryManager | R | Chroma/RAM | n/a | n/a | sí | degradación best-effort | event read | sin segundo query ni receipt de write |
| 8 | episodio del evento | MemoryManager | W | Chroma | no | no durable | sí | fallo invisible | event | opcional explícito; receipt visible |
| 9 | Persona/Motor/Oracle/Reasoning | capa correspondiente | R/decide | RAM/LLM falso en pruebas | n/a | n/a | sí | excepción podía ser fallback válido | decision | `decision:layer` requerido |
| 10 | guardado manual | MemoryManager | W | Chroma | no | no durable | sí | retry especial ambiguo | decision | requerido; fallo impide commit |
| 11 | persistencia de identidad de acción | ActionLedger | W | SQLite | no | sí | sí | acción separada del turno | decision | receipt requerido |
| 12 | alias candidato | Orchestrator | W | RAM | sí | no | sí | aprendizaje desde decisión inválida | decision | opcional; sólo decisión válida |
| 13 | ejecución local | ToolRegistry | W | proceso/entorno | normalmente no | depende del tool | sí | tool podía repetirse tras fallo posterior | execution | `STARTED/APPLIED`; no replay automático |
| 14 | preparación de dispatch | Orchestrator | W | boundary externo | no garantizado | no garantizado | sí | entrega ambigua | execution | receipt requerido; ambigüedad bloquea |
| 15 | Behavior/Task de outcome sandbox | BehaviorCore | W | RAM | sí dentro del call | no | sí | consumer parcial | outcome | receipt requerido |
| 16 | WorkingMemory | MemoryManager | W | RAM | parcialmente | no | sí | acción inválida registrada | decision/outcome | requerido; sólo fase válida |
| 17 | ConversationMemory | MemoryManager | W | RAM/store configurado | no garantizado | no | sí | conversación ficticia | decision | requerido; sólo respuesta válida |
| 18 | notificación | Orchestrator | W | cola RAM | sí localmente | no | sí | notificación de decisión inválida | decision | opcional; sólo respuesta válida |
| 19 | IdentityState | IdentityState | W | RAM | no general | no | sí | señal duplicable | decision/outcome | receipt requerido |
| 20 | armar descanso terminal | Orchestrator | W | RAM | sí | no | sí | descanso tras aborto | decision | opcional; después de decisión válida |
| 21 | aprendizaje de alias por outcome | Orchestrator/Memory | W | RAM/Memory | no garantizado | no durable | sí | duplicación o crash window | outcome | opcional; residual ROOT-015 |
| 22 | consumidores de outcome | Behavior/Task/Working/Identity | W | RAM | depende del owner | no | sí | algunos aplicados y otros no | outcome | receipts requeridos por consumidor |
| 23 | `mark_outcome_consumed` | ActionLedger | W | SQLite | no | sí | sí | consumo prematuro | outcome/bookkeeping | último consumer requerido |
| 24 | completion de transacción/UoW | ActionLedger | W | SQLite | no | sí | sí | estados divergentes | bookkeeping | commit atómico |
| 25 | cache de respuesta | Orchestrator | W | RAM | sí | sí | sí | partial cacheado como success | bookkeeping | sólo después de commit durable |

## 5. Event/decision/outcome/bookkeeping classification

- Event-owned: despertar descanso, `event:behavior` y `memory:event_episode`. El evento físico puede haber ocurrido aunque la decisión posterior falle.
- Decision-owned: capa cognitiva, guardado manual solicitado, identidad de acción, candidato de alias, WorkingMemory de decisión, ConversationMemory, notificación, Identity sin outcome y armado de descanso.
- Execution/outcome-owned: lifecycle, ejecución local/dispatch, registro de outcome, Behavior/Task de outcome, WorkingMemory de outcome, Identity de outcome, alias de outcome y consumo.
- Bookkeeping: claim, UoW, receipts, estado de transacción y cache RAM. Triage y retrieval son observaciones/cómputo sin side effect persistente propio.

## 6. Failure map

| Punto | Delta conocido | UoW/API | Retry/restart |
|---|---|---|---|
| F0 después de claim | claim y `OPEN` nacen juntos o no nace ninguno | `OPEN`; un abandono se reclasifica | bloqueado como reconciliation |
| F1 Behavior tras mutar | checkpoint restaura S0; effect `ROLLED_BACK` | `ABORTED_CLEAN`, failure | no remuta |
| F2 después de Behavior | efectos event-owned confirmados pueden permanecer | clean abort si no hubo efecto decision/outcome | no repite el turno |
| F3 durante memory write | fallo retornado: opcional `FAILED`; write→crash→receipt: `STARTED` | puede commit si fallo conocido; crash exige reconciliation | no replay de `STARTED` |
| F4 antes de Triage | sólo event-owned confirmado | clean abort | no repite event effects |
| F5 dentro de Motor | capa `FAILED`, sin acción válida | `ABORTED_CLEAN`, failure | no repite Motor |
| F6 después de decisión | `decision:layer` aplicado | `PARTIAL` | bloqueado |
| F7 antes de action persistence | decisión aplicada, identidad aún no | `PARTIAL` | bloqueado |
| F8 después de action persistence | identidad de acción durable | `PARTIAL` | bloqueado; identidad preservada |
| F9 después de local tool | tool aplicado y receipt durable | `PARTIAL`/reconciliation | handler no se repite |
| F10 después de WorkingMemory | consumer aplicado | `PARTIAL` | bloqueado |
| F11 después de ConversationMemory | turno aplicado | `PARTIAL` | bloqueado |
| F12 después de notification | notificación aplicada | `PARTIAL`; su propio fallo conocido es opcional | bloqueado si falla luego |
| F13 después de Identity | señal aplicada | `PARTIAL` | bloqueado |
| F14 durante outcome consumer | anteriores aplicados, actual `AMBIGUOUS` o `FAILED` | `PARTIAL`/reconciliation | no replay automático |
| F15 antes de `consumed_at` | consumers requeridos resueltos, outcome no consumido | `PARTIAL` | bloqueado |
| F16 después de `consumed_at` | outcome consumido, commit pendiente | `PARTIAL` | bloqueado, no reconsume |
| F17 antes de completion | receipts aplicados, UoW aún abierto | `PARTIAL` o reconciliation tras restart | no replay automático |

`ABORTED_CLEAN` significa que no quedan efectos de decisión/ejecución pendientes; no significa que un evento real ni sus efectos event-owned sean deshechos.

## 7. UoW model

Estados durables: `OPEN`, `COMMITTED`, `ABORTED_CLEAN`, `PARTIAL` y `RECONCILIATION_REQUIRED`. `LEGACY_UNKNOWN` es una clasificación de lectura para transacciones históricas sin fila UoW, no un estado insertable. La columna histórica `transactions.status` conserva la semántica ROOT-003 de identidad/completion; `turn_uows.status` expresa el resultado operacional.

## 8. Durable journal schema

`turn_uows(transaction_id PK/FK, status, phase, failure_class, reconciliation_required, created_at, updated_at)` guarda el cierre del turno. `turn_effects(transaction_id, effect_key, effect_kind, phase, status, required, started_at, finished_at)` usa clave primaria compuesta. No guarda prompts, pensamientos, contexto de memoria, scene dumps, excepciones crudas ni secretos.

## 9. Effect keys/states

Las keys son estables y acotadas: `event:terminal_rest_wake`, `event:behavior`, `memory:event_episode`, `decision:layer`, `decision:manual_memory_save`, `decision:action_identity`, `decision:alias_candidate`, `execution:local_tool`, `execution:dispatch`, `execution:lifecycle_confirmation`, `execution:lifecycle_start`, `outcome:record`, `outcome:behavior_task`, `outcome:sandbox_behavior_task`, `decision:working_memory`, `outcome:working_memory`, `decision:conversation`, `decision:notification`, `decision:identity`, `outcome:identity`, `decision:terminal_rest_arm`, `outcome:alias`, `outcome:mark_consumed` y `outcome:sandbox_mark_consumed`.

Los estados son `PLANNED`, `STARTED`, `APPLIED`, `FAILED`, `AMBIGUOUS`, `ROLLED_BACK` y `SKIPPED`. Un estado terminal sólo admite repetición idempotente del mismo valor; cambiarlo se rechaza. Un required effect no resuelto impide `COMMITTED`.

## 10. Behavior checkpoint

`BehaviorCore` captura bajo su lock un snapshot operacional de `StateEngine`, `PersonalityTraits`, `TaskContext`, offsets de policy, constelación y última percepción. `StateEngine` posee sus helpers de checkpoint/restore. Si cualquier subcomponente lanza, `BehaviorCore` restaura a S0 antes de propagar; Orchestrator registra `ROLLED_BACK` y emite un fallo tipado. No se persisten modelos ni contexto LLM.

## 11. Motor failure boundary

Motor, Persona, Oracle y Reasoning atraviesan la misma frontera central: excepción implica `decision:layer=FAILED`, ninguna acción válida, cero efectos downstream de decisión, cero cache y cero completion. Orchestrator propaga `TurnProcessingError`; no convierte la excepción en fallback cognitivo autorizado ni en `None` legítimo.

## 12. Invalid decision policy

Antes, el blindaje terminal podía convertir `valido=False` en `hablar`. Ahora se valida antes de adaptar el canal. Una respuesta inválida cierra la capa como fallo y termina `ABORTED_CLEAN`: no action identity ejecutable, WorkingMemory, ConversationMemory, notificación normal, señal Identity, alias, cache ni hablar coercionado. Los efectos event-owned previos conservan su semántica.

## 13. Memory required/optional policy

| Operación | Policy | Motivo |
|---|---|---|
| retrieval | lectura, sin receipt de write | observa la consulta real, sin duplicarla |
| episodio del evento | opcional | el evento sigue siendo procesable; el fallo queda `FAILED` visible |
| guardado manual solicitado | requerido | el éxito funcional es precisamente persistir la conversación |
| WorkingMemory de acción/outcome | requerido | refleja una decisión o transición que sí ocurrió |
| ConversationMemory | requerido | evita declarar éxito con un turno conversacional esperado ausente |
| alias candidato/outcome | opcional | aprendizaje auxiliar; ventana durable queda en ROOT-015 |

No se agrega una segunda búsqueda de Chroma ni dedupe de writes. Un crash entre write externo y `APPLIED` permanece ambiguo.

## 14. WorkingMemory

Se escribe sólo después de una decisión válida o de un outcome aceptado, con keys distintas. Es un consumer, no el ledger. Una decisión inválida genera cero acciones en WorkingMemory. Si falla o queda ambiguo después de un efecto previo, el UoW no puede commit.

## 15. ConversationMemory

Se registra después de una respuesta válida y se journaliza como requerido. Excepción central o decisión inválida no produce conversación ficticia. ROOT-007 conserva la semántica temporal existente; no afirma delivery externo.

## 16. Notifications

La notificación normal sólo se evalúa tras una decisión válida. Es opcional y su fallo propio queda `AMBIGUOUS` sin exponer texto interno; si una notificación fue aplicada y luego falla otra etapa, el turno queda parcial. Los errores diagnósticos permanecen en logger/AuditTrace.

## 17. Identity

`decision:identity` y `outcome:identity` distinguen la fuente. La señal es requerida cuando aplica y se emite una vez antes del receipt. Un receipt confirmado impide replay automático de un turno parcial; una decisión inválida no emite señal de execution success/failure.

## 18. Task

Las mutaciones de Task dentro de Behavior comparten su checkpoint owner-local. En outcomes, Behavior/Task usa un receipt requerido propio. ROOT-007 garantiza aplicación 0/1 o ambigüedad explícita; no crea la state machine de ROOT-011 ni redefine creación/lifecycle.

## 19. Alias

Los candidatos de decisión y el aprendizaje por outcome sólo ocurren en flujos válidos y son opcionales. Un fallo se registra sin transformar el turno en éxito falso. La idempotencia durable del write de Memory y el crash antes del receipt siguen en ROOT-015.

## 20. Local tool

Antes de invocar un handler se persiste `execution:local_tool=STARTED`; al retornar, `APPLIED`. La prueba usa un contador, inyecta un fallo posterior y demuestra `counter == 1` antes y después del retry. Si no existe receipt terminal seguro, el sistema bloquea replay y exige reconciliación; no afirma exactly-once distribuido.

## 21. External action

La preparación de dispatch usa `execution:dispatch`. `STARTED` sin terminal es ambigua y no se reintenta automáticamente. Lifecycle y material attestation continúan bajo ROOT-004/005; delivery, timeout y ack externo pertenecen a ROOT-054.

## 22. Outcome consumption

Un outcome se registra durablemente antes de ejecutar consumers. Behavior/Task, WorkingMemory, alias cuando aplica e Identity tienen receipts separados. `mark_outcome_consumed` ocurre sólo después de resolver todos los consumers requeridos. Si uno falla después de otro aplicado, el UoW queda parcial y no se vuelve a ejecutar desde cero.

## 23. Retry

`COMMITTED` devuelve la respuesta durable; `ABORTED_CLEAN` devuelve el mismo fallo durable sin reejecutar; `PARTIAL` y `RECONCILIATION_REQUIRED` bloquean replay; un `OPEN` encontrado por otro intento/restart se marca abandonado y pasa a reconciliation. El retry no se usa como rollback.

## 24. Restart

Con SQLite temporal se prueba que un UoW parcial, la identidad de acción y el outcome sobreviven al cierre/reapertura y no disparan el pipeline. También se prueba replay exacto de una respuesta committed y recuperación de clean abort sin volver a invocar Behavior/Motor.

## 25. Partial/reconciliation

Un fallo con `STARTED` pendiente produce `RECONCILIATION_REQUIRED`; un fallo después de cualquier efecto `decision`, `execution` u `outcome` confirmado produce `PARTIAL`; sin esos deltas pendientes produce `ABORTED_CLEAN`. La primera llamada ya recibe la clasificación de reconciliation cuando corresponde, no sólo los retries.

## 26. API no-op vs failure

Una acción válida `none` conserva `HTTP 200`, `status=ok`, `accion=null`. `TurnProcessingError` y `TurnAbortedError` producen `HTTP 500`; partial/reconciliation produce `HTTP 409`, `status=pendiente`. Los mensajes son mínimos y redactados; ROOT-010 conserva la propiedad del envelope público definitivo.

## 27. AuditTrace

AuditTrace v1 permanece congelado y no se consulta para recovery. Sigue observando sus stages existentes, incluido `error` y `event_end`; no se añadieron stages UoW/commit/rollback. El journal operacional vive exclusivamente en ActionLedger v3.

## 28. ROOT-015 boundary

Permanecen abiertos exactamente: (a) Memory write seguido de crash antes de su receipt UoW, (b) dedupe durable de Memory, (c) receipt durable del write de alias en Memory y (d) transaccionalidad de consolidation. ROOT-007 los representa como ambigüedad y no los declara resueltos.

## 29. ROOT-054 boundary

Permanecen la ambigüedad de efecto físico/transporte de outcome, los timeouts y retries HTTP, la pérdida de respuesta al cliente y el delivery ack. No se agregó worker de reconciliación, 2PC, exactly-once distribuido ni cambio de transport retry.

## 30. Field-required status

El code gate se ejecuta aislado: sin red real, LLM real, hardware, Unity Play Mode ni Chroma activa. ROOT-007 mantiene `field_required=true`; el field gate queda pendiente para la integración posterior.

## 31. Tests

`core/test_turn_unit_of_work.py` contiene 20 pruebas deterministas: migración, contrato de receipts y commit, required failure, rollback exacto de Behavior, clean abort, Motor, invalid decision, Memory opcional, guardado manual requerido, tool post-failure, outcome parcial/completo, tres escenarios de restart, concurrencia, `OPEN` abandonado, API, congelamiento AuditTrace y cobertura nominal F0–F17. Se complementa con los gates ROOT-005/004/003/012/006/001/002, regresiones de guardado manual/outcomes/Core y `regression_probe` global.

## 32. Migration

ActionLedger evoluciona explícitamente de schema v2 a v3. La migración crea sólo `turn_uows` y `turn_effects`, conserva transactions, actions, outcomes, lifecycle, transition history, material receipts y `consumed_at`, y actualiza meta dentro de transacción SQLite. Una fixture v2 demuestra preservación; una transacción histórica sin UoW se lee como `LEGACY_UNKNOWN`, nunca como committed inventado.

## 33. Risks/crash windows

Persisten ventanas inevitables entre un efecto no SQLite y su receipt, especialmente Chroma, alias, herramientas y dispatch externo. `STARTED` evita repetición optimista y fuerza reconciliación. Los checkpoints Behavior sólo cubren el mismo proceso y call; no compensan efectos físicos. Un optional `AMBIGUOUS` puede coexistir con commit cuando el contrato declara que no es requisito, pero su incertidumbre queda durable.

## 34. Criterion

Una transacción sólo es `COMMITTED` cuando su UoW está `OPEN`, cada efecto requerido está terminalmente `APPLIED` o `SKIPPED`, no queda required `PLANNED/STARTED/FAILED/AMBIGUOUS`, los marcadores de consumo requeridos están resueltos y ActionLedger actualiza atómicamente `turn_uows.status=COMMITTED` y `transactions.status=completed` con su respuesta durable. La cache RAM se escribe únicamente después de ese commit.

## 35. Final state

ROOT-007 queda `CLOSED_CODE / FIELD_PENDING` cuando los gates documentados cierren sin regresiones, con un único commit conceptual. ROOT-011, ROOT-010, ROOT-015, ROOT-054, Capa 2, Persona factual, TCM y el sistema nuevo quedan intactos; no se ejecuta field test ni push.
