# ROOT-012 — Ownership y atomicidad del estado compartido

## 1. Causa

El lock por `id_transaccion` de `Orchestrator` solo serializaba reintentos del
mismo ID. Dos IDs distintos podían ejecutar simultáneamente la secuencia
compuesta de `BehaviorCore`: tarea, estado, personalidad, policy, percepción y
constelación. Aunque `StateEngine` protegía varios métodos aislados, no existía
una frontera que hiciera serializable el snapshot conductual completo.

Además, operaciones compuestas de RAM carecían de owner lock: `get` + touch y
`put` + eviction del cache, snapshot + clear de notificaciones, pending stores,
sets/counter de `MemoryManager` y snapshots de `WorkingMemory`.

## 2. Tickets absorbidos

| Ticket | Evidencia cerrada aquí |
|---|---|
| `CONC-AUDIT-001` | `BehaviorCore` compartido ahora serializa su mutación y snapshot. |
| `CONC-AUDIT-002` | cache LRU tiene `get+touch` y `put+evict` atómicos. |
| `CONC-AUDIT-003` | notificaciones tienen append y drain bajo el mismo owner. |
| `CONC-AUDIT-004` | pending RAM, WorkingMemory, dedupe sets y contador tienen locks locales. |

No se absorbe la atomicidad durable de Memory ni el ownership de valores
mutables cacheados.

## 3. Ownership map before

| Recurso | Owner real | Readers | Writers | Threads posibles | Lock anterior | Operación compuesta | Carrera demostrada | Lock apropiado | Cambia en ROOT-012 |
|---|---|---|---|---|---|---|---|---|---|
| `BehaviorCore` compuesto | `BehaviorCore` | Orchestrator, `/estado`, `/constelacion`, AuditTrace | eventos, task lifecycle, policy | threadpool + event loop/lectores | ninguno | mutar State/Traits/Task/percepción/constelación y proyectar contexto | sí, dos IDs dentro de Behavior | `RLock` owner + `StateEngine.atomic()` | sí |
| `StateEngine` numérico | `StateEngine` | Behavior, Identity, terminal rest | los mismos | varios threads | `RLock` por método, dos properties sin lock | varias lecturas/mutaciones forman el snapshot Behavior | parcial | conservar `RLock`; exponer sección compuesta al owner | sí, acotado |
| `PersonalityTraits` | `BehaviorCore` | Psychology/Behavior | Behavior | varios IDs | ninguno | update + lectura de rasgos/modifiers | sí como parte de Behavior | lock externo del owner Behavior | sí, indirecto |
| `TaskContext` | `BehaviorCore` | Behavior/API snapshot | Behavior/cancelación | varios IDs | ninguno | objetivo + intento + detectores + resumen | sí | lock externo del owner Behavior | sí, indirecto |
| policy/offsets | `BehaviorCore` | Psychology/Orchestrator snapshot | `cambiar_policy` y recomputación | varios threads | ninguno | policy + offsets + modifiers State | sí | lock owner Behavior | sí |
| última percepción/constelación | `BehaviorCore` | contexto, API, audit | cada turno | varios threads | ninguno | reemplazo + resumen + serialización | sí | lock owner + copia defensiva | sí |
| `_transacciones_procesadas` | `Orchestrator` | inicio de turno/tests | final de turno/eviction | IDs distintos | ninguno | `get+move_to_end`, `put+move+pop` | sí, `KeyError` | `Lock` dedicado | sí |
| pending conversation saves | `Orchestrator` | retry | fallo/éxito manual | IDs distintos | solo lock per-ID, distinto por clave | get/put/touch/evict/pop | riesgo demostrado por ownership | `Lock` dedicado | sí |
| pending alias candidates | `Orchestrator` | outcome/approval | decisión Motor | IDs distintos | solo lock per-ID | put/evict/search/pop | riesgo demostrado por ownership | `Lock` dedicado; persistir fuera | sí |
| `_notificaciones` | `Orchestrator` | `/notificaciones` | turns threadpool | event loop + threads | ninguno | snapshot + clear frente a append | sí, elemento perdido | `Lock` dedicado | sí |
| `_transacciones_locks` | `Orchestrator` | entrada/salida de turn | entrada/salida de turn | todos | `_transacciones_lock_guard` | lookup/create/refcount/remove | no después de guard | ya protegido | no |
| descanso terminal pending | `Orchestrator` | comando terminal | arm/wake | IDs distintos | `_descanso_terminal_lock` `RLock` | pending + transición StateEngine | no residual aquí | ya protegido | no |
| `WorkingMemory._buffer` | `WorkingMemory` | prompts/AuditTrace | final de turn | IDs distintos | ninguno | append/evict, snapshots, latest, clear | riesgo estructural | `Lock` local | sí |
| `ConversationMemory._turns` | `ConversationMemory` | prompts/save manual | turns | IDs distintos | `RLock` | append/snapshot/clear | no residual estructural | ya protegido | no |
| dedupe sets RAM | `MemoryManager` | alias/afecto/hechos | reads y writes de Memory | IDs distintos | ninguno | contains/add alrededor de I/O | RAM sin owner; check-write durable aparte | `Lock` RAM solo para set op | sí, parcial |
| contador consolidación | `MemoryManager` | scheduler interno | episodios observacionales | IDs distintos | ninguno | increment/threshold/reset | sí, lost update | `Lock` RAM; consolidar fuera | sí |
| pending manual snapshots | `MemoryManager` | retry manual | save manual | varios IDs | `_guardado_conversacional_lock` `RLock` | snapshot/lookups/write/receipt | protegido intra-proceso | ya protegido | no |
| Chroma | `ChromaClient`/stores | Memory readers | Memory writers | varios threads/procesos | `RLock` compartido por path, por operación | check→write, write→delete, receipt | sí a nivel durable | receipt/UoW durable | no; `ROOT-015` |

Clasificación final: **proteger ahora** Behavior, cache, notificaciones, pending
Orchestrator, WorkingMemory y RAM simple de MemoryManager; **ya protegido**
bookkeeping per-ID, descanso terminal, ConversationMemory y save manual
intra-proceso; **ROOT-015** check/write/receipt/delete persistentes; **otro ROOT**
valores mutables del response cache (`ROOT-043`) y ledger/UoW (`ROOT-003/007`).

## 4. Lock map before

| Lock | Owner | Puede adquirir mientras lo sostiene | Duración máxima anterior |
|---|---|---|---|
| per-transaction `RLock` | Orchestrator, por ID | toda la ruta del turno | turno completo, incluso LLM/Memory/tools; no bloquea IDs distintos |
| `_transacciones_lock_guard` `Lock` | Orchestrator | ninguno | create/refcount/remove del mapa |
| `StateEngine._state_lock` `RLock` | StateEngine | reentrada por `tick`/biología | un método numérico |
| Behavior owner | inexistente | n/a | n/a |
| `_descanso_terminal_lock` `RLock` | Orchestrator | `StateEngine._state_lock` | arm/wake terminal |
| `_guardado_conversacional_lock` `RLock` | MemoryManager | ConversationMemory y lock Chroma | save manual completo |
| `ConversationMemory._lock` `RLock` | ConversationMemory | ninguno | append/snapshot/clear |
| lock Chroma por path `RLock` | ChromaClient | backend local | una operación del backend |
| WorkingMemory/cache/notificaciones/pending/RAM | inexistentes | n/a | n/a |
| `_WRITE_LOCK` | AuditTrace writer | syscall append | una línea JSONL |

No se encontró un ciclo A→B y B→A en el mapa anterior; sí faltaban owners.

## 5. Ownership policy final

- `BehaviorCore` sincroniza todos sus entrypoints productivos y no delega el
  conocimiento de Task/Traits/percepción a Orchestrator.
- `StateEngine` conserva la propiedad de su reserva numérica y ofrece
  `atomic()` para que el owner conductual componga una secuencia estable.
- Cada colección de Orchestrator tiene un lock dedicado. Ningún caller externo
  manipula `WorkingMemory` bajo un lock de Orchestrator.
- `WorkingMemory` y `ConversationMemory` son owners de sus propias deques.
- `MemoryManager._ram_state_lock` protege exclusivamente sets y contador RAM.
- Chroma conserva su lock por operación; el protocolo durable sigue separado.

## 6. Lock order final

| Lock | Owner | Puede adquirir | Mientras sostiene | Duración máxima esperada |
|---|---|---|---|---|
| per-ID `RLock` | Orchestrator | terminal→State, Behavior→State, Memory locales, cache/notificaciones/pending | solo el turno de ese ID | turno completo; no global |
| `_transacciones_lock_guard` | Orchestrator | ninguno | nunca espera el per-ID lock | lookup/refcount breve |
| `BehaviorCore._owner_lock` `RLock` | BehaviorCore | `StateEngine._state_lock` | nunca LLM, Chroma, Persona, Oracle ni tools | aplicación + snapshot determinista |
| `StateEngine._state_lock` `RLock` | StateEngine | solo reentrada propia | nunca adquiere Behavior/terminal | operación/snapshot numérico |
| terminal-rest `RLock` | Orchestrator | StateEngine | no adquiere Behavior | arm/wake breve |
| cache `Lock` | Orchestrator | ninguno | solo OrderedDict | get/touch o put/evict |
| notifications `Lock` | Orchestrator | ninguno | solo deque | extend o drain |
| pending save/alias `Lock` | Orchestrator | ninguno | solo OrderedDict; no Memory I/O | get/put/search/pop/evict |
| WorkingMemory `Lock` | WorkingMemory | ninguno | solo deque | append/read/clear |
| Memory RAM `Lock` | MemoryManager | ninguno | jamás Chroma/consolidación | contains/add o counter token |
| manual-save `RLock` | MemoryManager | ConversationMemory y Chroma | protocolo manual existente | save manual completo |
| ConversationMemory `RLock` | ConversationMemory | ninguno | no llama manual-save | append/snapshot/clear |
| Chroma path `RLock` | ChromaClient | backend | no llama a owners Core | una operación |
| AuditTrace `_WRITE_LOCK` | writer | syscall | no llama logger desde writer | una línea |

Órdenes relevantes: `Behavior → StateEngine`; `terminal-rest → StateEngine`;
`manual-save → ConversationMemory → Chroma`. No existe la inversa. Cache,
notifications, pending, WorkingMemory y RAM Memory no se anidan con I/O.

## 7. Behavior atomicity

`BehaviorCore` incorporó un `threading.RLock` porque sus métodos públicos se
reentran legítimamente: `finalizar_tarea()` llama `procesar_evento()`, la
sincronización de tarea puede iniciar/finalizar, y los getters de constelación
pueden generar contexto. Dentro de `procesar_evento`, el owner adquiere además
`StateEngine.atomic()`; así Personality, Task, State, offsets, percepción,
Psychology y constelación pertenecen a una sola sección serializable.

Los entrypoints cubiertos son `procesar_evento`, `iniciar_tarea`,
`finalizar_tarea`, `cancelar_tarea`, `cambiar_policy`, getters de contexto,
constelación, audit y resumen. `TaskContext` y `PersonalityTraits` no recibieron
locks duplicados: en producción pertenecen al owner Behavior.

## 8. Snapshot policy

El resultado de `procesar_evento`, el contexto LLM, la constelación y el resumen
son copias defensivas. La última percepción también se retiene mediante copia
profunda. Un turno posterior que cambie tarea, rasgos, estado o percepción no
puede reescribir retrospectivamente el snapshot del turno anterior.

Esto no inicia `ROOT-016`: solo congela el estado operativo que Behavior ya
proyectaba. Tampoco convierte los dataclasses de todo el dominio en inmutables.

## 9. Cache atomicity

`_transacciones_cache_lock` (`threading.Lock`) cubre:

- hit + `move_to_end`;
- put + touch + todas las evictions del límite.

La carrera histórica fue reproducida con un `OrderedDict` instrumentado: el
writer esperó el lock mientras el reader estaba entre get/touch; el hit se
devolvió, no hubo `KeyError`, luego la eviction dejó el tamaño en uno.

El valor cacheado sigue siendo la misma instancia. Por eso `ROOT-043` permanece
`OPEN`; este packet protege atomicidad estructural, no ownership/deep-copy del
valor.

## 10. Notifications drain

El producer construye localmente las notificaciones y hace un único `extend`
bajo `_notificaciones_lock`. El consumer realiza `list + clear` bajo el mismo
lock. Una notificación concurrente queda exactamente en el batch actual o en el
siguiente según quién adquiere primero, nunca desaparece y no se duplica.

`ROOT-055` no cambia: la API aún conserva su semántica histórica de fallo frente
a lista vacía.

## 11. WorkingMemory

`WorkingMemory` posee un `threading.Lock` no reentrante. Protege append/eviction,
`obtener_ultimas`, `obtener_todas`, `ultimo_resultado` y `limpiar`. `resumen`
trabaja sobre el snapshot devuelto por `obtener_ultimas`, ya fuera del lock.

No se cambió el orden FIFO, el tamaño máximo, los resultados ni el bug
`MEM-RAM-001`/`ROOT-045` donde `MemoryManager.obtener_ultimas_acciones(n)` ignora
`n`. Tampoco se resolvió la mutabilidad interna de `detalles`.

## 12. MemoryManager RAM state

`_ram_state_lock` (`threading.Lock`) protege operaciones individuales sobre:

- `_hechos_conversacionales_guardados`;
- `_alias_aprendidos_guardados`;
- `_memorias_afectivas_guardadas`;
- `_observaciones_desde_revision`.

El incremento + comparación + reset del contador es una sola sección crítica.
Solo se obtiene un token booleano bajo lock; `consolidar_memorias_si_hace_falta`
se ejecuta después de liberarlo. Los contains/add de sets son seguros, pero el
lock también se libera antes de lookup o write persistente.

Las fotografías conversacionales pendientes ya estaban bajo el `RLock` del save
manual y no recibieron otro lock. ConversationMemory también estaba protegida.

## 13. Límites con ROOT-015

Continúan fuera de ROOT-012:

- miss RAM → lookup Chroma → write → add RAM de alias/afecto/hechos;
- dedupe entre procesos o tras crash;
- confirmación durable de episodios/reglas;
- check→write de lote de consolidación;
- write de regla → delete de episodios;
- receipts/idempotency keys y recuperación transaccional.

Un lock RAM no convierte esas secuencias en transacciones durables. No se
mantuvo `_ram_state_lock` durante Chroma y no se declara `ROOT-015` cerrado.

## 14. Concurrency matrices

| Caso | Interleaving forzado | Resultado |
|---|---|---|
| IDs distintos / Behavior | primer writer retenido; segundo contendió | `max_active=1` |
| IDs distintos / post-Behavior | barrera de dos parties después del snapshot | ambos llegaron; no giant lock |
| mismo ID | primer trabajo retenido; retry contendió per-ID | una ejecución y mismo cache hit |
| snapshot A → turno B | B cambió Task/State/Traits/percepción | A quedó idéntico |
| cache reader/writer | pause entre get/touch + put/evict | sin `KeyError`, LRU coherente |
| notification drain/append | pause en clear + producer concurrente | initial actual, late siguiente |
| dos drains | drain seguido de drain | segundo vacío |
| WorkingMemory read/write | pause durante snapshot + append concurrente | snapshot anterior y final completo |
| contador | dos threads desde Barrier | valor final 2, sin lost update |
| pending stores | writer contra lock retenido | espera owner y conserva entrada |
| AuditTrace | dos IDs en barrera concurrente | dos pares start/end, trace IDs separados |

Las sincronizaciones primarias usan `Event`, `Barrier` y locks instrumentados.
Los timeouts existen solo como guard anti-hang; no se usa `sleep`.

## 15. Deadlock analysis

Se probaron la reentrada `Behavior → Behavior → StateEngine`, la combinación
per-ID + Behavior y la independencia cache/notificaciones. Todos los threads
terminaron. El `RLock` de Behavior es necesario por llamadas públicas anidadas;
el de StateEngine ya era necesario por `aplicar_evento → tick` y descanso.

No hay ruta `StateEngine → Behavior`, `StateEngine → terminal-rest`,
`ConversationMemory → manual-save` ni lock RAM → Chroma. Los locks de cache,
notificaciones y pending no adquieren otros locks. No se detectó ciclo A→B→A.

## 16. Latency/concurrency sanity

Una medición offline local de 250 000 pares uncontended
`BehaviorCore RLock → StateEngine RLock` observó aproximadamente **505 ns por
par**. Es una observación del host, no un SLA ni un presupuesto fijo.

La prueba de IDs distintos retuvo el primer writer Behavior y confirmó
contención del segundo; tras liberar el snapshot, ambos alcanzaron una barrera
post-Behavior. Esto descarta serialización accidental de toda la pipeline. En el
flujo real, Behavior libera antes de Triage (y por tanto antes de Memory/LLM,
Persona, Oracle, Reasoning y tools).

## 17. AuditTrace

`AuditTrace v1` y su writer no cambiaron. El test concurrente produjo para cada
ID exactamente `event_start` y `event_end` con un único `trace_id`, y dos IDs
distintos obtuvieron trace IDs distintos. El `_WRITE_LOCK` existente continúa
protegiendo cada línea JSONL; no se añadieron stages ni un lock global.

El probe `capa1_motor_completion_probe.py --group audit` pasó sus matrices
ready/clarification/invalid/fallback/parser bajo writers ok/false/raise, con
invariancia funcional y correlación conservada.

También pasaron 10/10 probes estables de `core_orchestrator_probe.py`. No se
ejecutaron como aceptación los dos probes históricos que exigen la condición ya
corregida (`Behavior` solapado y pérdida de notificación); se conservaron sin
reescribir como evidencia forense anterior al fix.

## 18. Roots vecinos

| Root | Estado tras ROOT-012 |
|---|---|
| `ROOT-003` action/outcome ledger | `OPEN / READY FOR FIX`; no iniciado |
| `ROOT-004` state machine | `OPEN`; no tocado |
| `ROOT-007` Unit of Work | `OPEN`; no tocado |
| `ROOT-011` task lifecycle | `OPEN`; no tocado |
| `ROOT-015` durable receipts/atomic writes | `OPEN / READY FOR FUTURE FIX` |
| `ROOT-016` identity owner | `OPEN`; no tocado |
| `ROOT-043` mutable cache values | `OPEN`; atomicidad estructural mejorada |
| `ROOT-053` Oracle single-flight | `OPEN`; no tocado |
| `ROOT-055` notifications API envelope | `OPEN`; no tocado |

## 19. Riesgos

- El per-ID lock sigue abarcando el turno completo; es deliberado para
  idempotencia del mismo ID, pero un caller que reutilice IDs bloquea su retry.
- El lock Behavior serializa un único estado conductual global. Si en el futuro
  existen sesiones independientes, el ownership deberá particionarse.
- La copia profunda añade coste proporcional al snapshot, hoy pequeño.
- Save manual conserva su lock intra-proceso durante Chroma; cambiar ese
  protocolo pertenece a ROOT-015/007.
- Los sets RAM evitan carreras estructurales, no duplicados durables concurrentes.
- WorkingMemory sigue exponiendo items con `detalles` mutables; no se amplió el
  contrato de ownership de valores.

## 20. Criterio de cierre

| Gate | Evidencia |
|---|---|
| Behavior de dos IDs serializable | PASS |
| snapshot coherente/no retroactivo | PASS |
| ningún lock global durante LLM/Chroma/tools | PASS por frontera y barrera post-Behavior |
| same-ID serializado/idempotente | PASS |
| cache get/touch y put/evict | PASS |
| race cache sin `KeyError` | PASS |
| notifications sin pérdida/duplicación | PASS |
| WorkingMemory owner/snapshot | PASS |
| RAM Memory sin lost update | PASS |
| durable Memory separado | PASS, documentado para ROOT-015 |
| deadlock | no demostrado en rutas cubiertas |
| tests sin sleeps | PASS |
| AuditTrace v1/traces concurrentes | PASS |
| ROOT-006 | 21/21 PASS |
| ROOT-001 | 14/14 PASS |
| ROOT-002 | 13/13 PASS |
| regresión global | 224 ejecutadas: 223 PASS + 1 KNOWN_XFAIL, 0 inesperados |
| compileall | PASS |
| Chroma activa | 3/3 SHA-256 iguales a entrada |

## 21. Estado final

`ROOT-012 CLOSED`.

Prueba focal permanente: `core/test_shared_state_atomicity.py`, 15/15 PASS.
Regresión global real: 224 unidades ejecutadas, 223 PASS, 1 `KNOWN_XFAIL`
histórico (`memory.test_chromadb_flow.test_eliminacion`), 0 fallos inesperados.

Hashes de entrada y cierre esperados:

| Archivo | SHA-256 |
|---|---|
| `lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin` | `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659` |
| `lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin` | `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A` |
| `lazzaro_data/chroma.sqlite3` | `C003D87617B5156E358E4DD56DAB9B8765401AF239F809750627E42A77D23F6D` |

`ROOT-003` queda listo por prerequisitos (`ROOT-006 CLOSED` +
`ROOT-012 CLOSED`), pero no fue iniciado. No hubo red, Unity, LLM real,
hardware, Chroma activa, field test ni push.
