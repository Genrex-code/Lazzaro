# ROOT-003 — Identidad canónica y action/outcome ledger

## 1. Causa

El flujo anterior generaba `EventoEntrante.id_transaccion` y
`AccionSaliente.id_accion`, pero la relación solo sobrevivía en caches y
colecciones RAM. Unity emitía el resultado con una transacción nueva y con el
nombre de la acción, sin devolver de forma fiable el `id_accion` original. El
Orchestrator podía entonces asociar por nombre, target o candidato reciente;
un duplicate, un resultado tardío o un reorder podía afectar otra acción o la
task vigente. Un restart perdía tanto la idempotencia como la evidencia de que
un outcome ya había producido efectos internos.

## 2. Tickets absorbidos

Este cierre absorbe `API-CONTRACT-003`, `DTO-CONTRACT-003`,
`ORCH-AUDIT-001`, `ORCH-AUDIT-002`, `TASK-AUDIT-001`, `EXEC-AUDIT-001`,
`EXEC-AUDIT-002` y `UNITY-BOUNDARY-002`. No amplía la solución hacia la state
machine de ROOT-004, la atestación material de ROOT-005, Unit of Work de
ROOT-007, lifecycle completo de ROOT-011, receipts Memory de ROOT-015,
versionado wire global de ROOT-038, ownership profundo de cache de ROOT-043 ni
retries de transporte de ROOT-054.

## 3. Mapa de IDs before

| Entidad | ID anterior | Creador | Scope | RAM/durable | Wire | Regresaba | Consumidor | Unicidad | Problema |
|---|---|---|---|---|---|---|---|---|---|
| Evento entrante | `id_transaccion` | client | request | cache RAM | sí | en `RespuestaAPI` | Orchestrator/cache | declarada por client | se perdía al reiniciar; descripción prometía UUID sin exigirlo |
| AuditTrace | `trace_id` | wrapper Orchestrator | turno observado | JSONL | no contractual | no | observabilidad | UUID por turno | no puede decidir identidad operacional |
| Acción saliente | `id_accion` | adapter Python | decisión | RAM | sí | no en outcome | client/caches/alias | UUID por adapter | no había relación durable con request/outcome |
| Response cache | `id_transaccion` key | Orchestrator | proceso | RAM LRU | no | n/a | retry local | key única en dict | eviction/restart permitían replay |
| Task | `objetivo_id` | BehaviorCore | tarea lógica | RAM | indirecto | no | Behavior/LLM | no garantiza instancia | reset podía reutilizar objetivo sin distinguir generación |
| Alias candidate | `id_accion` | Orchestrator | pendiente RAM | RAM | no | esperaba resultado | alias learning | key de dict | fallback podía consumir otro candidato |
| Unity response | `accion.id_accion` | server | dispatch | wire | sí | no | handlers Unity | recibido | handlers descartaban identidad |
| Unity result event | nueva `id_transaccion` | Unity | outcome request | wire | sí | sí | API/Orchestrator | GUID nuevo | no refería inequívocamente a la acción original |
| Snapshot perceptivo | `snapshot_id` | Unity | percepción | payload | sí | opcional | motores/memoria | derivada de estado | no es action identity |
| WorkingMemory | sin ID canónico | Orchestrator | acción inmediata | RAM | no | no | contexto | ninguna | dispatch y resultado no se reconciliaban |

Cadena anterior real:

```text
REQUEST id_transaccion
  → DECISION sin ID durable
  → ACTION id_accion generado por adapter
  → DISPATCH con id_accion
  → OUTCOME nueva id_transaccion + nombre/exito
  → INTERNAL APPLICATION por nombre/task/candidato RAM
```

## 4. Consumers de outcome before

| Consumer | Usaba `id_accion` | Usaba nombre | Usaba task actual | Mutación | Duplicate safe | Late safe | Unknown safe |
|---|---:|---:|---:|---|---:|---:|---:|
| `BehaviorCore` | no | sí | sí | state/personality/task | no | no | no |
| `TaskContext` | no | sí | era la activa | intentos/umbrales | no | no | no |
| `Orchestrator` | parcial | sí | indirectamente | flujo, memoria, identity | no | no | no |
| alias learning | primero, con fallback | sí | no | aprende/consume candidate | no | no | no |
| Memory episódica/asociativa | no | sí | narrativa vigente | escrituras | no | no | no |
| `IdentityState` | no | sí | contexto del turno | estado interno | no | no | no |
| `WorkingMemory` | no | sí | no | append de acción/resultado | no | no | no |

La frontera nueva resuelve y clasifica el outcome antes de todos estos
consumers. Solo `ACCEPTED` entra al flujo; `UNKNOWN`, `DUPLICATE`, `CONFLICT` y
`MISMATCH` terminan como no-op durable.

## 5. Storage elegido y por qué

Se creó `core/action_ledger.py` sobre SQLite de la stdlib. Es un storage
operacional separado de Chroma y AuditTrace, inspeccionable, transaccional,
durable tras restart y con constraints `PRIMARY KEY`/`UNIQUE`. Los tests usan
`:memory:` o un archivo dentro de `TemporaryDirectory`; no crean el ledger de
producción. No se añadió ninguna dependencia externa.

## 6. Schema ledger

Schema version `1`, guardada en `ledger_meta`:

| Tabla | Identidad y datos mínimos | Constraints relevantes |
|---|---|---|
| `transactions` | transaction, origin, event type, status, action, response JSON, UTC timestamps | PK `transaction_id`; status `incomplete/completed` |
| `actions` | action, originating transaction, name, operational params, confirmation, task instance, intended client, outcome expected, UTC created | PK `action_id`; UNIQUE `transaction_id`; FK transaction |
| `outcomes` | action, outcome transaction, strict success, canonical name, small payload, fingerprint, received/consumed UTC | PK `action_id`; UNIQUE `outcome_transaction_id`; FK action |
| `outcome_attempts` | outcome transaction, exact action, classification, fingerprint, received UTC | UNIQUE `outcome_transaction_id` |

El JSON durable es canónico y versionado en la response mínima. No usa pickle.

## 7. Ownership/lifecycle

`Orchestrator` posee una sola instancia de `ActionLedger`. `main.lifespan`
inyecta `settings.ACTION_LEDGER_PATH`; al apagar, `Orchestrator.close()` cierra
el ledger y luego Oracle Memory. `ActionLedger.close()` es idempotente. Los
fakes legacy construidos sin `__init__` reciben un ledger `:memory:` lazy. El
directorio padre se crea al abrir el path productivo, nunca durante imports.

## 8. Transaction claim

El primer caller ejecuta `claim_transaction()` antes de Behavior y recibe
`CREATED`. Un retry encuentra `COMPLETED` y reconstruye la misma response desde
SQLite, usando RAM solo como fast cache. Un claim existente sin completion
devuelve `INCOMPLETE` y bloquea replay; origin/event type incompatibles devuelven
`CONFLICT`. La API traduce incomplete/conflict a HTTP 409 y un fallo del ledger
a HTTP 503 sin acción.

## 9. Action identity

El adapter sigue generando una única UUID `id_accion`. Antes de ejecución local
o entrega externa, `record_action()` persiste la relación única
`transaction_id → action_id`, nombre, parámetros operacionales, task instance y
client previsto. Un retry completed restaura ese mismo ID; nunca vuelve a
ejecutar Triage ni regenera la acción.

Mapa final:

```text
REQUEST transaction_id (opaque idempotency key)
  → ACTION action_id (UUID estable y durable)
  → OUTCOME outcome_transaction_id + action_id exacto
  → OUTCOME CONSUMPTION consumed_at
```

## 10. Task correlation

Cada creación/reset de `TaskContext` recibe un `task_instance_id` nuevo. La
acción persiste el token de la task existente al decidir. Tras resolver el
ledger, Behavior recibe la correlación canónica y solo registra el intento si
el token coincide con la instancia activa. Un objetivo textual igual no basta.
Las llamadas internas legacy no correlacionadas conservan su contrato previo;
el enforcement se activa en la frontera productiva del outcome.

## 11. Outcome correlation

`resultado_accion` exige `id_accion` exacto y `exito` bool estricto. El ledger
resuelve exclusivamente esa PK, valida el nombre si fue enviado y valida
`id_transaccion_origen` cuando está disponible. Solo `OUTCOME_ACCEPTED` pasa a
Behavior, Memory, alias e IdentityState; después de esos consumers se escribe
`consumed_at`. La clasificación también queda durable y consultable por
`outcome_transaction_id`.

## 12. Duplicate policy

El primer outcome por `action_id` es autoridad. Otro receipt con el mismo
fingerprint se clasifica `DUPLICATE`; si cambia success/nombre/payload se
clasifica `CONFLICT`. Reutilizar una misma transacción de outcome para otra
acción también es conflicto. Nunca se sobrescribe el original y ninguna de
estas clasificaciones reaplica task, alias, memoria ni estado interno.

## 13. Unknown policy

Un `id_accion` inexistente crea únicamente un attempt `UNKNOWN`. No crea acción
fantasma, no busca por nombre/target/recency, no llega a Behavior y produce cero
mutaciones funcionales. El evento outcome queda completed con respuesta nula,
por lo que repetir su misma transacción tampoco abre una vía de replay.

## 14. Late policy

Un outcome tardío válido se conserva ligado a su action. Si su
`task_instance_id` ya no es el activo, Task Y no recibe intentos, éxito ni
fracaso de Task X. No se reactiva la task anterior. Un alias solo puede
consumirse si continúa pendiente bajo el mismo `id_accion`.

## 15. Reorder policy

Para acciones A/B y outcomes recibidos B/A, cada lookup usa su propia PK
`action_id`. No existe supuesto FIFO ni variable global de última acción. Las
dos acciones pueden registrar su receipt válido independientemente del orden.

## 16. Crash/retry policy

El orden es: claim durable, trabajo cognitivo, identidad action durable,
ejecución/dispatch, mutations internas, consumption/completion durable. Si un
crash deja el claim `incomplete`, el retry falla cerrado y no repite
automáticamente side effects. Si ya existe completion, el retry reconstruye la
misma action o el mismo no-action. No se afirma exactly-once distribuido ni se
inventa success para estados ambiguos.

## 17. Unity propagation

`LazzaroAgent` crea un `ContextoAccion` por response con `id_accion`, transacción
de origen, nombre y parámetros. Todos los handlers y closures pasan ese
contexto; `MovementController` devuelve `actionId` en cada callback. El evento
`resultado_accion` incluye `id_accion` e `id_transaccion_origen`. Se eliminaron
`UltimaAccionMovimiento`, el target/params globales y toda correlación por última
acción. Una response nula o no correlacionada solo puede cerrar la subtarea de
su request exacta.

## 18. API contract

`id_transaccion` queda documentado como idempotency key opaca, estable y no
vacía para conservar clientes no UUID. Para `resultado_accion`, Pydantic rechaza
ID ausente/blanco y success ausente o no booleano. El action ID se normaliza con
`strip`. Los errores del ledger tienen envelopes mínimos 409/503 y nunca
despachan una acción sin identidad durable.

## 19. Alias learning

La búsqueda de candidato acepta únicamente
`detalles["id_accion"] → pending_store[id_accion]`. Se eliminaron aliases de
campo (`accion_id`, `action_id`) y los fallbacks por nombre, target, similitud,
recencia o único candidato. Unknown, duplicate y mismatch se detienen antes de
alias learning.

## 20. Sandbox synchronous outcomes

Las acciones sandbox también obtienen action identity antes de llamar a
`ActionExecutor`. Su `ToolResult` se registra inmediatamente como outcome del
mismo `action_id`, con transacción sintética estable
`sandbox:<action_id>`, y se marca consumido tras los consumers internos. No se
añadió la state machine de ROOT-004.

## 21. Privacy

El ledger persiste solo origin/type, identidad, nombre, parámetros
operacionales, flags, token de task, client, response mínima, payload pequeño y
timestamps. El sanitizador elimina claves relacionadas con API key,
authorization, token, password, secret, prompt, pensamiento privado y contexto
de memoria; limita profundidad, colecciones, strings y payload total. No guarda
prompt completo, chain-of-thought, memoria completa, raw LLM context ni objetos
pickled.

## 22. Concurrency

Cada operación de escritura usa un `RLock` de conexión y `BEGIN IMMEDIATE`;
SQLite y constraints deciden atómicamente. Las pruebas con barrera demuestran:
un solo `CREATED` para la misma transaction, action created/existing sin doble
fila, y un solo outcome accepted ante dos threads; el otro queda
duplicate/conflict. No hay secuencia insegura check/unlock/insert.

## 23. Restart

Con un SQLite tempfile, la instancia B reabre transaction y action creadas por
la instancia A y recupera el mismo `id_accion`. Una tercera apertura conserva
el outcome original, su `consumed_at` y detecta el duplicate. Los attempts
UNKNOWN también siguen consultables después del cierre/reopen.

## 24. AuditTrace

AuditTrace v1 permanece congelado: no cambió schema, sanitización ni stages. No
genera action IDs, no se consulta para dedupe y su JSONL no es source of truth.
Un retry durable resuelto antes de Triage conserva `event_start → event_end` sin
stages inventados. Ledger y AuditTrace se verifican como sistemas separados.

## 25. Roots vecinos

| ROOT | Estado tras ROOT-003 |
|---|---|
| ROOT-004 | `OPEN / READY FOR FIX` |
| ROOT-005 | `OPEN`; aún depende de ROOT-004 |
| ROOT-007 | `OPEN`; prerequisito ROOT-003 satisfecho |
| ROOT-011 | `OPEN`; puede apoyarse en action identity |
| ROOT-015 | `OPEN`; no se añadieron receipts Memory |
| ROOT-043 | `OPEN`; RAM cache sigue siendo optimización |
| ROOT-054 | `OPEN`; puede apoyarse en transaction/action ID estable |

## 26. Crash windows residuales para ROOT-007/015

- Crash tras claim y tras una mutación Behavior/Memory, pero antes de completion:
  queda `incomplete`; no hay rollback ni replay automático.
- Crash tras persistir action y antes de que el client reciba la response: la
  identidad existe, pero su entrega requiere reconciliación futura.
- Crash durante una tool sandbox tras registrar action y antes de outcome: no se
  puede inferir si el side effect ocurrió.
- Crash después de aplicar consumers y antes de `consumed_at`: el receipt es
  durable pero la aplicación interna queda ambigua.
- SQLite y Chroma/Memory no comparten transacción; atomicidad cross-storage,
  dedupe durable de Memory y compensaciones pertenecen a ROOT-007/015.
- Ejecución del client, pérdida de response HTTP y transporte no constituyen
  exactly-once distribuido; ROOT-054 tratará retries de transporte.

## 27. Tests

- ROOT-003 permanente: **29/29 PASS**.
- ROOT-012: **15/15 PASS**.
- ROOT-006: **21/21 PASS**.
- ROOT-001: **14/14 PASS**.
- ROOT-002: **13/13 PASS**.
- Core probes estables seleccionados: **4/4 PASS**.
- Regresión global: **253 unidades**, **252 PASS**, **1 KNOWN_XFAIL** histórico
  (`memory.test_chromadb_flow.test_eliminacion`), **0 inesperados**.
- Validación C# offline: 3 archivos balanceados, 16 calls de outcome con contexto
  exacto y 4 rutas movement con `actionId`: PASS.
- El probe histórico `capa1_motor_completion_probe.py --group audit` no es una
  aceptación compatible: su barrera anterior prohíbe todo `sqlite3.connect`,
  incluido el ledger `:memory:`, y se detiene antes del motor. No fue modificado
  ni usado para ocultar resultados.
- Sin red, Unity, Play Mode, LLM real, hardware, Chroma activa ni ledger runtime.

## 28. Criterio de cierre

Los 28 gates solicitados quedan satisfechos por diseño y pruebas: identity antes
de dispatch; relaciones durable request/action/outcome; contrato Unity/API;
unknown/duplicate/reorder/late fail-safe; alias exacto; retry sin action nueva ni
replay pre-cache; reopen y concurrencia; ledger authority separado de RAM,
Chroma y AuditTrace; privacidad; write failure con cero dispatch; regresiones
ROOT-012/006/001/002 y global verdes; AuditTrace v1 y hashes Chroma intactos.

## 29. Estado final

**ROOT-003 CLOSED.** La respuesta a “¿cómo sé después de reiniciar que este
outcome pertenece a esta acción y ya fue consumido?” es: consultar
`actions[action_id]` para la transaction de origen y
`outcomes[action_id]` para el `outcome_transaction_id` y `consumed_at`, sin usar
nombre, orden ni RAM. ROOT-004 no fue iniciado y no hubo push.
