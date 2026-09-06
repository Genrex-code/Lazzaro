# ROOT-011 — Explicit Task Lifecycle

## 1. Causa

`BehaviorCore` trataba cualquier `comando_usuario.detalles.mensaje` como objetivo. Sin task creaba una; con task activa comparaba el texto y ejecutaba `reset()`. Conversación, consultas y cancelaciones se convertían accidentalmente en gestión de tareas. Además, `reset()` heredaba el threshold anterior y `Psychology` podía narrar abandono sin una transición real.

## 2. Tickets absorbidos

ROOT-011 absorbe `CORE-AUDIT-001`, `TASK-AUDIT-002` y `TASK-AUDIT-003`. `TASK-AUDIT-001` permanece cerrado por ROOT-003: los outcomes se correlacionan mediante `action_id` y el `task_instance_id` capturado al crear la acción.

## 3. Lifecycle map before

| Entrada | Efecto anterior | Defecto |
|---|---|---|
| primer comando con mensaje | `TaskContext(mensaje)` | texto libre creaba task |
| siguiente mensaje distinto | `reset(mensaje)` | conversación reemplazaba task |
| `inicio_simulacion` con `objetivo:` | regex extraía objetivo | texto seguía siendo authority |
| `objetivo_completado=True` | llamada recursiva a `finalizar_tarea(True)` | completion mezclada con procesamiento anidado |
| `finalizar_tarea(False)` | evento `fracaso` + liberar | failure y lifecycle colapsados |
| “detente” | Behavior reemplazaba objetivo y Orchestrator cancelaba después | receipt nombraba “detente”, no la task original |
| outcome correlacionado | intento sobre ID de origen | correcto desde ROOT-003 |
| threshold cruzado | sólo `debe_abandonar=True` | narrativa terminal con task activa |
| `reset()` | nuevo ID y limpieza parcial | heredaba `umbral_abandono` |
| `obtener_resumen()` | detectores repetidos para aliases | lectura redundante |

## 4. Intent map

| Input real | Task intent |
|---|---|
| social, query, Oracle, memoria, percepción y sensores | `NONE` |
| acción física inmediata sin continuidad declarada | one-shot, `NONE` |
| objetivo estructurado nuevo sin task | `START` |
| paso estructurado de la instancia activa | `CONTINUE` |
| objetivo estructurado distinto con task activa | `REPLACE` |
| orden externa estructurada de cancelación | `CANCEL` |
| evento/flag explícito de completion | `COMPLETE` |
| outcome correlacionado | intento de la instancia de origen; no transición por sí solo |
| threshold canónico alcanzado | `ABANDON` interno |

## 5. Authority de task intent

La autoridad externa mínima vive en `EventoEntrante.detalles`: `task_intent` (`start`, `continue`, `replace`, `cancel`, `complete`), `task_objective` para start/replace y `task_instance_id` opcional para precondición de identidad. También se conservan `tipo_evento=tarea_completada` y el booleano existente `objetivo_completado=True` como completion explícita. Triage decide ruta cognitiva, no lifecycle; Persona/LLM y el mensaje libre nunca son authority.

## 6. State machine final

La ausencia se representa con `None`. Un `TaskContext` existente siempre representa una instancia `ACTIVE` con objetivo no vacío. `COMPLETED`, `CANCELLED` y `ABANDONED` son receipts terminales tipados producidos antes de liberar el contexto; no se conservan objetos terminales ni se añade persistencia.

Los intents son `TaskIntent.NONE/START/CONTINUE/REPLACE/CANCEL/COMPLETE/ABANDON`. Los resultados son `NO_TASK`, `UNCHANGED`, `CREATED`, `CONTINUED`, `REPLACED`, `COMPLETED`, `CANCELLED`, `ABANDONED` e `INVALID_TRANSITION`.

## 7. Transition table

| Estado | Evento | Estado siguiente | Nueva instancia | Efecto |
|---|---|---|---|---|
| None | START + objetivo | Active X | sí | crea contexto limpio |
| Active X | CONTINUE + identidad compatible | Active X | no | conserva todos los campos |
| Active X | REPLACE + objetivo distinto | Active Y | sí | receipt X→Y y reset completo |
| Active X | CANCEL | None | no | receipt de X y liberación |
| Active X | COMPLETE | None | no | receipt de X y una recompensa de completion |
| Active X | threshold canónico | None | no | receipt ABANDONED, sin outcome inventado |
| None | CANCEL/COMPLETE | None | no | `NO_TASK` idempotente |
| None | CONTINUE/REPLACE | None | no | `INVALID_TRANSITION` |
| Active X | START o identidad/objetivo incompatible | Active X | no | `INVALID_TRANSITION`, sin mutación |

## 8. `task_instance_id` semantics

START y REPLACE crean UUID nuevo. CONTINUE conserva UUID, objetivo, intentos, fallos, frustración, historial, threshold y configuración. Los receipts terminales conservan el UUID/objetivo original. ActionLedger sólo recibe un `task_instance_id` para acciones cuyo evento autorizó START, CONTINUE o REPLACE; una microacción one-shot no hereda accidentalmente la task activa.

## 9. Create

Sólo `TaskIntent.START` con ausencia de task y `task_objective` no vacío crea contexto. `TaskContext("")` está prohibido. Invocar `BehaviorCore.iniciar_tarea()` sigue siendo una entrada programática explícita, no una inferencia textual.

## 10. Continue

CONTINUE requiere una task activa. Si se suministra `task_instance_id`, debe coincidir; si se suministra objetivo, debe ser idéntico. No resetea ningún campo. El mensaje puede cambiar porque no participa en la precondición.

## 11. Replace/reset

REPLACE requiere task activa, objetivo nuevo y, si se aporta, identidad coincidente. El receipt conserva la instancia anterior y crea otra. `reset()` restaura objetivo, UUID, contadores, frustración, historial y `umbral_abandono`; conserva `max_historial` como configuración.

## 12. Completion

Completion sólo nace de `TaskIntent.COMPLETE`, `tarea_completada` o `objetivo_completado is True`. Una action success aislada mantiene la task activa. La primera completion libera y aplica una vez el evento emocional; una completion duplicada devuelve `NO_TASK` y no reaplica recompensa.

## 13. Cancellation

La detección por regex fue eliminada. `task_intent=cancel` actúa sobre la instancia que existía antes del mensaje, conserva ID/objetivo en el receipt y luego libera. Sin task devuelve no-op explícito. En Unity puede producir `cancelar_tarea`; terminal verbaliza el receipt sin fingir una acción física.

## 14. Abandonment

El owner es `BehaviorCore`, usando la condición determinista `frustracion_local >= umbral_abandono` de `TaskContext`. La evaluación ocurre después del único outcome correlacionado y antes de construir Psychology, por lo que no queda una task activa junto a narrativa de abandono. ABANDON externo es ilegal: no puede decidirlo Persona, LLM ni texto.

ABANDONED no cambia ActionState, no fabrica `reported_failure`, no crea outcome y no equivale a CANCELLED ni COMPLETED.

## 15. Attempt semantics

Sólo un outcome terminal válido y aceptado por ActionLedger llega a `TaskContext.registrar_intento`. Se exige bool estricto. Authorization, dispatch, executing, denial, unknown y duplicate no incrementan contadores. Un intento fallido mantiene ACTIVE salvo que su frustración alcance el threshold canónico.

## 16. Late outcome

La acción conserva en ActionLedger el `task_instance_id` autorizado al decidirse. Orchestrator reinyecta ese ID como correlación; Behavior registra el intento únicamente si coincide con la instancia activa. Un outcome de X recibido después de crear Y sí se consume según ROOT-003/007, pero Y conserva ID, objetivo, contadores, frustración y lifecycle.

## 17. Conversation behavior

“hola”, “cómo estás”, “gracias” y otras conversaciones sin señal lifecycle mantienen `task_context=None`. Si X está activa, la conversación se procesa con X disponible como contexto, pero no cambia objetivo, UUID, contadores ni threshold.

## 18. Query behavior

Una consulta normal, incluida una ruta Oracle, no crea task. Route/intent cognitivo no se reinterpreta como lifecycle. Convertir una consulta en objetivo requiere un START o REPLACE estructurado independiente.

## 19. Physical commands

Una acción física inmediata sin `task_intent` es one-shot y no se vincula a TaskContext. Un objetivo físico multi-turn usa START; los pasos posteriores usan CONTINUE y el ID de la instancia. REPLACE es obligatorio para un objetivo distinto. No se infiere ninguna de estas decisiones por longitud, verbo o regex.

## 20. Threshold reset

`TaskContext.DEFAULT_ABANDONMENT_THRESHOLD` conserva el valor contractual existente `80.0`. Toda nueva instancia o reset comienza ahí; luego Behavior puede proyectarlo con la paciencia mediante la policy ya existente. Ninguna task hereda el threshold contextual de la anterior.

## 21. Detector behavior

Loop, estancamiento y compulsión siguen siendo flags diagnósticos y no producen outcomes derivados. `obtener_resumen()` calcula cada detector una sola vez y reutiliza el resultado para sus aliases compatibles. Leer resumen/snapshot no ejecuta una transición; el abandono se aplica únicamente en el path owner del evento.

## 22. UoW integration

No existe un segundo journal de tareas. Las transiciones ocurren bajo el lock y checkpoint de BehaviorCore, dentro de `event:behavior` o `outcome:behavior_task` de ROOT-007. Una excepción dentro de Behavior restaura la transición; un efecto confirmado y un fallo posterior conserva la clasificación event/outcome del UoW existente.

## 23. AuditTrace

AuditTrace v1 permanece congelado. `state_before/state_after.task` sigue usando los campos existentes y safe preview del objetivo. No se añaden stages, historial, receipt completo ni secretos. La ausencia o cambio de ID/objetivo explica la transición sin modificar el schema.

## 24. Roots vecinos

ROOT-010 queda `OPEN / NEXT`. ROOT-009, ROOT-039, ROOT-013, ROOT-014, ROOT-015 y ROOT-016 permanecen `OPEN`. ROOT-011 no añade planner, persistencia de task, nueva base cognitiva, migración Chroma, TCM, DevelopmentalCore ni componentes del rework de Capa 2.

## 25. Tests

`core/test_task_lifecycle.py` contiene 22 pruebas permanentes: conversación con/sin task, query, start, continue, replace, reset total, threshold, cancel, completion, action success, attempts, duplicate, unknown, late outcome, abandono, ausencia de failure inventado, detectores, transiciones ilegales, UoW y binding ActionLedger. También permanecen obligatorios los gates ROOT-007/005/004/003/012/006/001/002, cancelación, guardado manual, regresión global, compileall, C# estático, hashes y búsqueda de ledgers runtime.

## 26. Criterion

El code gate exige que ninguna prosa cree o reemplace tasks; toda transición tenga productor, precondición y receipt; los IDs sean por instancia; reset limpie todo estado local; cancel/completion/abandonment sean distintos; attempts procedan sólo de outcomes válidos; late/duplicate/unknown sean inocuos; las lecturas no muten lifecycle; ROOT-003/004/007 sigan verdes; AuditTrace y Chroma permanezcan intactos.

## 27. Final state

ROOT-011 queda `CLOSED` al completar los gates offline. No requiere persistencia nueva ni field test en este packet. ROOT-005 y ROOT-007 conservan `CLOSED_CODE / FIELD_PENDING`; no se abre Unity, no se usa red/LLM/hardware real y no se realiza push.
