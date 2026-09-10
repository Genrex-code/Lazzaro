# ROOT-015 — Memory writes idempotentes y verificables

## 1. ROOT y tickets

ROOT-015 cierra `MEM-PERSIST-001`, `MEM-PERSIST-002`, `MEM-CONC-001` y
`MEM-CONSOLIDATE-001`. El alcance es la persistencia de Memory: identidad durable,
target estable, verificacion exacta, receipts y consolidacion recuperable. No incluye
retry de transporte, 2PC, migracion masiva, Capa 2 ni cambios de routing.

## 2. Dependencias y limites

Las dependencias ROOT-006 y ROOT-012 estaban cerradas. ROOT-039, ROOT-013 y
ROOT-014 deben conservar sus contratos. `ActionLedger`, `AuditTrace v1`, API,
Unity y terminal no cambian de autoridad ni de schema. ROOT-020 permanece sin
iniciar.

## 3. Baseline

Base confirmada antes de editar: `2e29069a645910ddcbcf1b91d8dc81a69cbc1fe5`,
commit `fix(memory): expose retrieval health receipts`, rama
`codex/stabilization/pre-capa2`, working tree limpio. La regresion de entrada fue
`544/544 PASS`, cero XFAIL y cero resultados inesperados. No hubo red, LLM real,
Unity, field test ni push.

## 4. Grafo de escritura anterior

```text
Orchestrator / caller
  -> MemoryManager
       -> encoder (UUID y timestamp nuevos)
       -> Store / Chroma bool
       -> cache o scan de dedupe
       -> mantenimiento o delete posterior
       -> str | None
```

No habia una identidad durable que uniera intentos, target y resultado. El check,
la mutacion, la verificacion y el receipt eran pasos independientes.

## 5. Falso negativo anterior

Una experiencia podia quedar persistida y luego fallar la programacion de
consolidacion. El wrapper devolvia `None`, aunque el efecto primario ya existia. El
caller recibia un falso fallo y un retry podia crear otra memoria.

## 6. Ambiguedad de bool CRUD

El bool de Chroma solo informa lo que devolvio el adapter, no el estado final. Un
`add` de ID repetido puede ser no-op; una excepcion puede ocurrir despues del
efecto; un delete/update puede contestar sin probar cuantas filas cambiaron. Por
eso ROOT-015 obtiene evidencia exacta antes y despues.

## 7. Carrera anterior

Los flujos alias, afectivo y relacional hacian `cache/scan -> no existe -> write`.
Dos threads podian observar simultaneamente ausencia y escribir dos targets. El
`RLock` de proceso no daba arbitraje durable entre reinicios o conexiones.

## 8. Estado parcial de consolidacion anterior

Consolidar era `crear regla -> borrar episodios`. Un crash o delete parcial dejaba
regla y sources coexistiendo sin registro durable del paso alcanzado. Un retry
podia repetir la regla o seleccionar arbitrariamente el estado legacy.

## 9. Comparacion de opciones

| Opcion | Resultado |
|---|---|
| A. Reutilizar `ActionLedger` | Rechazada: mezcla lifecycle de acciones y memoria. |
| B. Journal dentro de Chroma | Rechazada: usa como arbitro al mismo backend incierto. |
| C. Locks/checks y content hash | Rechazada: no sobrevive restart y confunde contenido con evento. |
| D. Ledger SQLite dedicado + coordinator | Elegida: claim durable, target estable y saga verificable. |

No se afirma 2PC ni ACID entre bases.

## 10. Arquitectura elegida

```text
MemoryManager (owner)
  -> MemoryWriteCoordinator
       -> MemoryWriteLedger (claim/transiciones cortas)
       -> ChromaClient.obtener_por_ids (evidencia exacta)
       -> Store/Chroma mutation (efecto)
       -> MemoryWriteReceipt
```

El coordinator decide identidad, secuencia y verdad del outcome. El ledger
persiste control; Chroma persiste recuerdos; `MemoryManager.close()` cierra el
owner local.

## 11. Por que no ActionLedger

`ActionLedger` conserva correlacion y outcomes materiales de acciones. Hacerlo
autoridad de Memory acoplaria dos ciclos de vida, permitiria IDs con significado
doble y alteraria ROOT-003/ROOT-007. ROOT-015 mantiene un journal separado.

## 12. Por que no journal en Chroma

Un journal en Chroma no puede arbitrar de forma independiente una excepcion o
respuesta ambigua del propio Chroma. Tampoco ofrece la constraint relacional corta
necesaria para reclamar keys. SQLite aporta esa autoridad sin guardar narrativa.

## 13. Atomicidad logica

La garantia es **crash-safe logical atomicity / saga idempotente**: todo estado
parcial queda representado; el mismo intento conserva operacion, target y timestamp;
retry/restart converge al mismo efecto logico; consolidacion no borra sources antes
de verificar su replacement.

## 14. Descargo no-ACID

No hay transaccion distribuida SQLite+Chroma, atomic visibility ni promesa de cero
estados intermedios. Puede existir temporalmente doble representacion fisica. El
journal la vuelve rastreable y reanudable; no la oculta ni la llama ACID.

## 15. Identidad de operacion

Las keys son SHA-256 sobre JSON canonico y versionado:
`mwk:v1:<64-hex>`. El envelope incluye version, namespace, `operation_kind` y
`source_identity`. Solo se guarda el digest, nunca la fuente raw. Una misma key y
otro intent produce `IDEMPOTENCY_CONFLICT` antes de Chroma.

## 16. Identidad logica por dominio

| Dominio | Retry identity | Logical identity |
|---|---|---|
| Episodic | transaccion + clase de evento | ninguna global; eventos distintos siguen distintos |
| Generic semantic | key explicita del caller cuando se requiere | ninguna por texto/regla |
| Manual | transaccion + fingerprint canonico del snapshot | la misma key durable |
| Alias | action identity cuando existe | tupla alias normalizada y versionada |
| Affective | evento del caller cuando existe | `(object_id, object_class, affect, source)` versionado |
| Relational | transaccion + fingerprint del hecho | identidad canonica/versionada del hecho |
| Consolidation | source IDs exactos ordenados + policy/schema version | el mismo batch canonico |

La constraint parcial `UNIQUE(operation_kind, logical_key)` solo actua cuando hay
identidad logica declarada.

## 17. Contenido no es identidad de operacion

No se usa `hash(texto)` como dedupe universal. Dos episodios con igual narrativa y
eventos diferentes son memorias legitimas. Una semantic generica sin correlacion
explicita conserva comportamiento de nueva operacion.

## 18. Intent fingerprint

`mwi:v1:<sha256>` representa la intencion estable del dominio antes de tocar
Chroma. Excluye UUID y timestamp aleatorios del target. Se calcula sobre JSON
canonico (`sort_keys`, UTF-8, sin NaN), no sobre `repr()`.

## 19. Effect fingerprint

`mwe:v1:<sha256>` cubre ID, documento canonico, metadata persistida canonica de
ROOT-013 y version. Se calcula despues de recuperar el target/timestamp durables.
Es la postcondicion contra la que se compara `collection.get(ids=...)`.

## 20. Asignacion del target

El claim corto asigna una sola vez `operation_id`, UUID de target y timestamp UTC,
y los hace durables antes de mutar Chroma. Retry y restart leen exactamente esos
valores; nunca asignan un segundo target para la misma operacion.

## 21. Contrato del receipt

`MemoryWriteReceipt` es top-level y frozen. Sus campos exactos son:
`operation_id`, `idempotency_key`, `operation_kind`, `status`, `target_id`,
`matched_count`, `changed_count`, `reason_codes`, `durable`,
`reconciliation_required` y `replayed`. No contiene memoria, documento, metadata
ni exception.

## 22. Outcome durable y replay

`status` describe el outcome durable; `replayed` solo describe la invocacion. Una
creacion comprometida conserva `CREATED`; el retry devuelve `CREATED,
replayed=True`, sin reescribir el historial como `ALREADY_EXISTS`.
`ALREADY_EXISTS` queda reservado para adopcion logica o legacy compatible.

## 23. Lifecycle

Los estados son `INTENT_RECORDED`, `WRITE_ATTEMPTED`, `TARGET_VERIFIED`,
`CLEANUP_PENDING`, `COMMITTED`, `FAILED` y `RECONCILIATION_REQUIRED`. Cada
transicion SQLite confirma antes de cualquier llamada posterior a Chroma.

## 24. Estados terminales y no terminales

Terminales: `COMMITTED`, `FAILED`. No terminales: `INTENT_RECORDED`,
`WRITE_ATTEMPTED`, `TARGET_VERIFIED`, `CLEANUP_PENDING` y
`RECONCILIATION_REQUIRED`. Un receipt `AMBIGUOUS` no vuelve terminal a la
operacion.

## 25. R1 reanudable

`INTENT_RECORDED + target ausente + payload no disponible en recovery` no escribe,
no inventa payload, no cambia UUID/timestamp y no marca `FAILED`. El recovery
acotado reporta pendiente/`AMBIGUOUS`; un caller posterior con misma key, mismo
intent y payload reanuda el target original.

## 26. Verificacion exacta

`ChromaClient.obtener_por_ids(ids)` llama `collection.get(ids=..., include=...)`.
El receipt tipado separa `PRESENT`, `ABSENT` y `FAILED`. Esta API no usa consulta
vectorial y no modifica los receipts de retrieval ROOT-014.

## 27. May-have-written

Despues de un bool falso o excepcion de mutacion, el coordinator siempre intenta
leer el target exacto. Match exacto confirma el efecto; ausencia probada conserva
una operacion reanudable/fallo observable sin afirmar creacion; fallo de la propia
verificacion produce `AMBIGUOUS` y reconciliacion.

## 28. Verificacion de UPDATE

UPDATE hace exact read anterior, reclama la operacion, aplica el patch de metadata,
hace exact read posterior y compara la postcondicion fusionada. `matched_count=1`
solo si el target existia; `changed_count=1` solo si el fingerprint cambió y quedo
verificado. Target desconocido no inventa cambio.

## 29. Verificacion de DELETE

DELETE mide como matched los IDs probados presentes antes. Tras borrar, vuelve a
leer esos IDs; changed son solo los que quedaron probadamente ausentes. Targets ya
ausentes o replay comprometido no cuentan como cambios nuevos. Restantes se
persisten para reconciliacion.

## 30. Adopcion legacy

No hay migracion masiva. En primer encuentro, cada dominio puede proporcionar un
lookup compatible. Un unico match exacto adopta target/timestamp y termina
`ALREADY_EXISTS/LEGACY_EFFECT_ADOPTED`; multiples matches no se seleccionan al
azar y producen estado ambiguo. Episodic y semantic generica no adoptan sin
correlacion estable.

## 31. Integracion episodica

Orchestrator pasa `transaction_id` y clase de evento a
`guardar_experiencia_con_recibo`. La misma identidad reintenta la misma operacion;
otra transaccion permite otro episodio aunque el contenido sea identico. El wrapper
legacy sigue devolviendo `str | None`.

## 32. Integracion manual

El guardado conversacional usa transaccion y fingerprint canonico del snapshot.
Un nuevo efecto comprometido traduce a `saved`; replay o adopcion equivalente a
`already_saved`; `FAILED/AMBIGUOUS` a `error`. El ledger reemplaza como autoridad
al update de metadata no verificado y el restart deduplica aun con RAM vacia.

## 33. Alias

El alias usa correlacion de accion cuando existe y una identidad logica de tupla
normalizada/versionada. La constraint SQLite decide first-writer; el set RAM solo
acelera. Dos threads y un restart convergen a una operacion/target.

## 34. Afectiva

La API existente trata la misma tupla `(object_id, object_class, affect, source)`
como el mismo recuerdo logico; la version forma parte de la identidad para permitir
evolucion consciente. La deduplicacion ya no depende del scan/cache no atomico.

## 35. Relacional

Retry usa transaccion y fingerprint del hecho, y la logical key usa la identidad
canonica/versionada del hecho. Un lookup incierto no equivale a ausencia. La cache
de fingerprints queda como optimizacion, nunca autoridad.

## 36. Semantic generica

No existe dedupe global por regla o texto. Un caller que necesita idempotencia debe
proporcionar identidad estable; si no la tiene, cada invocacion recibe correlacion
propia. No se fabrica `hash(rule_text)`.

## 37. Maquina de consolidacion

```text
sorted exact source IDs + version
  -> durable claim + stable replacement target
  -> replacement write
  -> exact fingerprint verification
  -> CLEANUP_PENDING
  -> delete remaining known sources
  -> exact absence verification
  -> COMMITTED
```

El replacement no se recrea durante cleanup y el mismo batch conserva operacion,
target y timestamp.

## 38. Delete parcial

Si tras borrar `[A,B,C]` permanece `C`, el journal conserva solo `C` en
`remaining_source_ids_json`, outcome de invocacion `AMBIGUOUS`, reasons
`PARTIAL_DELETE` y `RECONCILIATION_REQUIRED`. El retry borra el restante; nunca
recrea el replacement.

## 39. Matrices W0-W7 y R1-R7

| Caso write | Comportamiento final |
|---|---|
| W0 intent invalido | `FAILED/INVALID_INTENT`, cero Chroma |
| W1 claim no durable | `FAILED/JOURNAL_FAILURE`, durable false, cero Chroma |
| W2 misma key/otro intent | `FAILED/IDEMPOTENCY_CONFLICT`, cero Chroma |
| W3 target previo exacto | adopta/reconcilia y no repite mutacion |
| W4 claim valido/target ausente | marca `WRITE_ATTEMPTED` y usa target durable |
| W5 mutacion responde o lanza | bool/exception es advisory; siempre verifica |
| W6 target exacto coincide | `TARGET_VERIFIED`; nunca falso `FAILED` |
| W7 final commit | `COMMITTED`, outcome durable; fallo final = `AMBIGUOUS` |

| Caso restart | Comportamiento final |
|---|---|
| R1 intent, target ausente, sin payload | permanece reanudable; no escribe ni adivina |
| R2 intent/attempt, target exacto | verifica y compromete el mismo target |
| R3 intent/attempt, target ausente | espera mismo caller/payload y conserva identidad |
| R4 lookup falla o target no coincide | reconciliacion requerida; no sobrescribe |
| R5 replacement exacto, sources presentes | continua cleanup de esos sources |
| R6 replacement exacto, algunos sources | borra/verifica solo los restantes |
| R7 replacement exacto, sources ausentes | compromete sin repetir replacement/delete |

`reconcile_pending(limit=100)` es explicito y acotado: no daemon, no loop, no
sleep. El retry de misma key tambien reanuda. Consolidacion puede reconstruirse de
sources exactos; un write ordinario nunca reconstruye payload desde un hash.

## 40. Ventana de doble representacion

Entre la verificacion del replacement y el fin del cleanup pueden coexistir la
regla y sus episodios. Es una ventana fisica esperada, **no atomic visibility**.
ROOT-014 no la esconde; `CLEANUP_PENDING`/`RECONCILIATION_REQUIRED` la hacen
observable y convergente.

## 41. Invariante de no perdida

Si el replacement no fue exact-verified, el numero de llamadas delete sobre
sources es cero. Fallo de write, verificacion no disponible o fingerprint distinto
detienen cleanup. Las pruebas inyectan las tres fronteras.

## 42. Ownership y concurrencia SQLite

El schema contiene `memory_write_meta`, `memory_write_operations` y
`memory_write_keys`; operations añade el conjunto operativo restante para cleanup.
`MemoryManager` posee coordinator/ledger y realiza `open/close` explicitos. El
`RLock` protege la conexion, pero las PK/UNIQUE de SQLite son el arbitro durable.
`BEGIN IMMEDIATE` solo envuelve claim/transicion y nunca permanece abierto durante
Chroma.

## 43. Privacidad del journal

Solo se guardan IDs operativos, digests, timestamps, states, status, reason codes y
conteos. No se guardan narrativa, regla, conversacion, prompt, documento ni metadata
cognitiva. `source_ids_json` y `remaining_source_ids_json` solo contienen IDs
canonicos de consolidacion.

## 44. Logging seguro

Los boundaries tocados registran `operation_kind`, etapa/state, code, conteos y
`error_type`. No interpolan `str(exception)`, `exc_info`, documento, metadata,
conversacion, estado cognitivo ni prompt. El delete de Chroma quedo redactado en el
mismo boundary.

## 45. AuditTrace

AuditTrace v1 permanece congelado. No serializa el receipt. `created_ids` recibe
solo un target `CREATED`, durable y con `changed_count > 0` en esa invocacion;
replay, `ALREADY_EXISTS`, `AMBIGUOUS`, `FAILED` e intent journal-only no aparecen
como creacion nueva. El mismo UoW/correlation de ROOT-007 se conserva.

## 46. Aislamiento de Chroma

Los tests usan fakes, SQLite temporal y, donde aplica, Chroma temporal. La coleccion
activa `./lazzaro_data` no se abre. Sus hashes se comparan antes/despues. El journal
productivo configurable es `./lazzaro_data/memory_write_ledger.sqlite3`, creado solo
al inicializar runtime explicitamente; import/constructor sin `open()` no crea DB.
`.gitignore` cubre DB, WAL y SHM de ese path.

## 47. Pruebas permanentes

`memory/test_memory_write_transactions.py` contiene exactamente 55 casos: writes,
replay/restart/conflict, may-have-written, concurrencia por dominio, CRUD, fallo de
mantenimiento, consolidacion/recovery, R1, privacidad, aislamiento, no DB on import
y prohibicion de vector query. Los probes `memory_deep_probe` y
`contracts_memory_probe` inspeccionan ruta runtime y contrato/schema.

## 48. Riesgos residuales

- La saga no evita que lectores externos vean la ventana de doble representacion.
- Un R1 ordinario necesita que el caller repita el payload; el journal no puede
  reconstruir cognicion deliberadamente.
- Un target mismatch requiere intervencion/reconciliacion; no se sobrescribe.
- Coordinacion multi-proceso depende de SQLite para identidad; el lock por operacion
  solo evita trabajo duplicado intra-proceso. Chroma puede recibir intentos
  fisicamente repetidos en una ventana inter-proceso, siempre con el mismo target;
  la exact verification converge el efecto logico.
- No hay expiracion, cancelacion, daemon, retry infinito ni bulk legacy migration.

## 49. Estado de cierre

ROOT-015 queda `CLOSED` solo tras gates finales. Evidencia ya confirmada: matriz
focal `55/55 PASS`, exact-ID Chroma y probes contractuales/runtime. Resultado global
real: `600/600 PASS`, cero XFAIL y cero resultados inesperados; isolation gate
`PASS`. `compileall` y `git diff --check` quedaron verdes, los hashes Chroma fueron
identicos y no quedo ningun DB de test residual. El working tree debe quedar limpio
tras el commit unico
`fix(memory): make writes idempotent and verifiable`, y sin push. ROOT-020 sigue
`NOT STARTED`.
