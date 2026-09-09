# ROOT-014 — Retrieval health receipts and bad-record isolation

## 1. ROOT y estado

ROOT-014 absorbe `MEM-CONTRACT-003`, `MEM-CONTRACT-006` y `MEM-RETRIEVE-001`. Su dependencia ROOT-039 y el schema ROOT-013 estaban cerrados antes de iniciar. El objetivo es distinguir ausencia sana, degradacion parcial y fallo, y evitar que un registro corrupto elimine resultados sanos.

Estado final esperado: `CLOSED` tras gate focal/global y commit unico `fix(memory): expose retrieval health receipts`. No se hace push.

## 2. Baseline

Base real: `424b1e752479e316a338e868c533501e9e47d025`, commit `fix(memory): preserve canonical memory schema`, rama `codex/stabilization/pre-capa2`, working tree limpio. Baseline de entrada: `489/489 PASS`, cero XFAIL y cero inesperados.

## 3. Collapse anterior

El contrato raw ya distinguia `dict` para una operacion Chroma completada y `None` para fallo/unavailability. Una consulta sana sin hits producia `ids=[[]]`. Los stores ejecutaban `if not resultados: return []`, por lo que `None` y no-hit terminaban en la misma lista. `MemoryRetriever` ademas convertia excepciones de cada fuente en `[]`; un fallo episodico con hits semanticos parecia completamente sano y dos fallos parecian una ausencia real.

`MemoryManager` resumía cualquier lista vacia como `No hay recuerdos relevantes para esta situación.`, incluso cuando ninguna fuente habia podido contestar.

## 4. Call graph antes

```text
ChromaClient.buscar
  -> EpisodicMemoryStore.buscar
  -> SemanticMemoryStore.buscar
       -> MemoryRetriever.buscar
            -> MemoryManager.buscar_recuerdos
                 -> MemoryManager.obtener_contexto_memoria_con_metadata
                      -> Orchestrator / cognition / OBS-005
```

`MemoryManager.obtener_contexto_relacional` realizaba una consulta semantica funcional separada. `OracleMemory` usa otra coleccion y otro contrato; permanece fuera de ROOT-014.

## 5. Contrato de health

`RetrievalHealth` es un enum finito de strings:

| Estado | Significado |
|---|---|
| `OK` | Backend sano y al menos un resultado valido, sin degradacion. |
| `OK_EMPTY` | Todas las fuentes consultadas sanamente y no existieron candidatos. |
| `PARTIAL` | Falta evidencia de una fuente o se aislaron batches/filas/resultados. Puede contener cero o mas hits. |
| `FAILED` | Ninguna fuente pudo producir evidencia de retrieval. |

No se usa bool ni truthiness para health.

## 6. Issue codes

El dominio estable es: `BACKEND_FAILURE`, `MALFORMED_BATCH`, `INVALID_ID`, `INVALID_DOCUMENT`, `MISSING_METADATA`, `INVALID_DISTANCE`, `UNKNOWN_SCHEMA`, `INVALID_SCHEMA`, `INVALID_TIMESTAMP`, `FUTURE_TIMESTAMP`, `SCORING_FAILURE` e `INTERNAL_FAILURE`.

Los codes no incluyen query, documento, ID de memoria, metadata, JSON, exception message, traceback ni payload cognitivo. `merge_reason_codes()` deduplica conservando la primera aparicion: episodic, semantic y luego cualquier code agregado por el nivel actual. El set interno se usa solo para membership; nunca se itera para decidir el orden.

## 7. StoreRetrievalReceipt

Contrato exacto, top-level frozen:

```text
health: RetrievalHealth
results: tuple[MemoryResult, ...]
candidate_count: int
dropped_count: int
reason_codes: tuple[RetrievalIssueCode, ...]
valid_count: property == len(results)
```

Invariante permanente:

```text
candidate_count == valid_count + dropped_count
```

Aplica tambien a batch desalineado cuando la cardinalidad de IDs es conocida. Un outer batch sin cardinalidad confiable usa cero candidatos, cero validos y cero descartados, pero sigue siendo `PARTIAL/MALFORMED_BATCH`.

## 8. MemoryRetrievalReceipt

Contrato exacto, top-level frozen:

```text
health: RetrievalHealth
results: tuple[MemoryResult, ...]
episodic: StoreRetrievalReceipt
semantic: StoreRetrievalReceipt
reason_codes: tuple[RetrievalIssueCode, ...]
candidate_count: property
source_valid_count: property
returned_count: property
dropped_count: property
```

`source_valid_count` se calcula antes del dedupe inter-source, sort y limite. `returned_count` se calcula despues. Dedupe y limit no son corrupcion y nunca incrementan `dropped_count`.

## 9. Matriz de store

| Evidencia | Health | Results |
|---|---|---|
| `None` o excepcion de backend contenida por el store | `FAILED` | vacio |
| Cuatro arrays alineados y vacios | `OK_EMPTY` | vacio |
| Una o mas filas, todas validas | `OK` | todas |
| Filas validas e invalidas | `PARTIAL` | solo validas |
| Backend sano, todas invalidas | `PARTIAL` | vacio |
| Outer batch malformed | `PARTIAL/MALFORMED_BATCH` | vacio |
| Arrays con longitudes distintas | `PARTIAL/MALFORMED_BATCH` | vacio |

Un backend que respondio con contenido no confiable no se etiqueta `FAILED`: la operacion fue observable, pero degradada.

## 10. Matriz hibrida

| Episodic | Semantic | Final |
|---|---|---|
| `OK_EMPTY` | `OK_EMPTY` | `OK_EMPTY` |
| `OK` | `OK_EMPTY` | `OK` |
| `OK_EMPTY` | `OK` | `OK` |
| `OK` | `OK` | `OK` |
| `FAILED` | `FAILED` | `FAILED` |
| `FAILED` | `OK_EMPTY` | `PARTIAL` |
| `OK_EMPTY` | `FAILED` | `PARTIAL` |
| `FAILED` | `OK` | `PARTIAL` con hits validos |
| `OK` | `FAILED` | `PARTIAL` con hits validos |
| Cualquier `PARTIAL` | Cualquier fuente | `PARTIAL` |

Una fuente fallida y la otra vacia nunca puede ser `OK_EMPTY`, porque no sabemos qué contenia la fuente fallida.

## 11. Alineacion de transporte

Se exige exactamente un batch para `ids`, `documents`, `metadatas` y `distances`. Antes de crear filas se comparan las cuatro longitudes. Si difieren se rechaza el batch completo: no existe evidencia suficiente para saber qué posicion desaparecio y conservar un prefijo podria asociar ID A con metadata B.

Con longitudes iguales, cada unidad se construye exclusivamente por el indice original. No se usa `zip`, compactacion ni filtrado previo.

## 12. Aislamiento por fila

Cada fila alineada valida, en orden: ID string no vacio, documento string, metadata dict presente y distancia numerica finita que no sea bool. Despues se invoca el decoder estricto ROOT-013.

Una fila invalida incrementa `dropped_count`, añade un reason code seguro y no interrumpe las siguientes. Nunca se fabrican `document=""`, `metadata={}` ni `distance=1.0` para reparar ausencia de transporte.

## 13. Strict schema interaction

`MemorySchemaCodec` sigue siendo la autoridad V1. `MemorySchemaDecodeError` no relaja V1 ni provoca fallback a V0. `unknown_schema_version` se mapea a `UNKNOWN_SCHEMA`, `invalid_timestamp` a `INVALID_TIMESTAMP` y los demas errores estrictos a `INVALID_SCHEMA`. Metadata `None` se detecta antes del decoder como `MISSING_METADATA`.

## 14. V0

V0 sano sigue decodificando core, extra escalar historica y origen legacy. El unico cambio de schema es temporal: timestamp ausente o ISO invalido ya no se reemplaza por `datetime.utcnow()`. Produce `MemorySchemaDecodeError(reason_code="invalid_timestamp")` y el store aisla la fila.

Los tests 29/30 de ROOT-013 ahora incluyen timestamp V0 valido porque prueban origen e internal extra, no el fallback temporal eliminado. Sus assertions y el total `66/66` permanecen.

## 15. Contrato temporal

El timestamp persistido y `MemoryMetadata.timestamp` no se reescriben. Para scoring:

- naive se interpreta como UTC por compatibilidad con writers historicos `datetime.utcnow()`;
- aware UTC se conserva;
- aware con offset se convierte con `astimezone(UTC)`;
- timestamp invalido produce `INVALID_TIMESTAMP`;
- `timestamp_utc > now_utc` produce `FUTURE_TIMESTAMP` con tolerancia cero.

Un timestamp futuro nunca recibe recencia maxima silenciosa.

## 16. Clock y MemoryDecay

`MemoryRetriever` captura un solo `now_utc` despues de ambas consultas y antes del scoring. El mismo valor se pasa a todos los resultados. `MemoryDecay` es la unica autoridad de conversion UTC, edad y peso de recencia; sus APIs aceptan `now_utc` opcional para compatibilidad y tests deterministas.

No se introdujo framework de clocks. La inyeccion opcional del retriever permite comprobar que el reloj se consulta exactamente una vez.

## 17. Scoring isolation

El retriever calcula cada resultado por separado. Los componentes permanecen en variables locales y `metadata_score` sólo se asigna después de completar el calculo. `MemoryTemporalError` conserva su reason code; errores aritmeticos/de dominio esperados se convierten en `SCORING_FAILURE`. No existe `except Exception -> INVALID_SCHEMA` por fila.

Un fallo inesperado de un store que ya deberia devolver receipt se contiene en el boundary del retriever como `INTERNAL_FAILURE`, nunca como schema invalido. Los stores contienen sus propias excepciones de backend como `BACKEND_FAILURE`.

## 18. Ranking

Se conserva la formula:

```text
0.45 * similitud
+ 0.25 * impacto
+ 0.20 * importancia_temporal
+ 0.10 * recencia
```

Se conserva dedupe donde semantic gana ante mismo ID, orden base `episodic remaining + semantic`, sort estable descendente por `metadata_score` y limite final. No hay tie-breaker nuevo ni clamp. La regresion demuestra que un `metadata_score > 1` sigue siendo posible; ROOT-040 permanece abierto.

## 19. Authoritative APIs y compatibilidad

Las APIs autoritativas son:

```text
EpisodicMemoryStore.buscar_con_recibo
SemanticMemoryStore.buscar_con_recibo
MemoryRetriever.buscar_con_recibo
MemoryManager.buscar_recuerdos_con_recibo
```

Los metodos `buscar()`/`buscar_recuerdos()` existentes proyectan `list(receipt.results)` de esa misma llamada. No ejecutan una busqueda adicional. El fallback a stores legacy en `MemoryRetriever` existe sólo para dobles/compatibilidad; `MemoryManager` productivo construye los stores nuevos.

## 20. Productive caller audit

El path cognitivo principal es:

```text
obtener_contexto_memoria_con_metadata
  -> buscar_recuerdos_con_recibo
     -> MemoryRetriever.buscar_con_recibo
        -> episodic.buscar_con_recibo (una vez)
        -> semantic.buscar_con_recibo (una vez)
```

No queda caller productivo del contexto principal interpretando `[]` como evidencia suficiente. Los usos legacy restantes son tests/probes de compatibilidad y reconstruccion post-write. `OracleMemory` usa su contrato de cache separado.

## 21. Relational retrieval

`obtener_contexto_relacional()` consume `SemanticMemoryStore.buscar_con_recibo()` una vez y despues proyecta texto. Si el store esta `FAILED/PARTIAL`, el boundary conserva la degradacion en un log seguro; no afirma internamente un no-hit sano. La API textual no cambia y no añade policy de Persona.

La seleccion relacional normaliza timestamps mediante la misma autoridad, captura un solo now para esa operacion y aisla invalidos/futuros antes de comparar u ordenar.

## 22. Consolidation temporal-only

La seleccion de candidatos de consolidacion captura un now UTC, rechaza timestamp ausente/invalido/futuro y pasa el mismo now a `MemoryDecay`. Ya no fabrica `datetime.utcnow()` para historia corrupta.

No se modifica qué se escribe cuando un candidato es valido, el orden write/delete, dedupe, retries, atomicidad, journal, CAS, 2PC ni recibos de escritura. Esos problemas permanecen ROOT-015.

## 23. MemoryContextResult

Conserva `texto`, `retrieved_ids` y `scores`, y añade defaults fail-closed:

```text
health
episodic_health
episodic_valid_count
episodic_dropped_count
semantic_health
semantic_valid_count
semantic_dropped_count
dropped_count
reason_codes
```

Un `MemoryContextResult()` sin evidencia usa `FAILED`, no `OK_EMPTY`.

## 24. Text semantics

- `OK_EMPTY`: conserva `No hay recuerdos relevantes para esta situación.`
- `OK` con hits: resumen actual.
- `PARTIAL` con hits: resumen sólo de resultados validos.
- `PARTIAL` sin hits: string vacio.
- `FAILED`: string vacio.

Un fallo del summarizer vacia sólo `texto`; no reescribe retrieval health.

## 25. OBS-005 y single-search

Un unico `MemoryRetrievalReceipt.results` alimenta resumen y un unico recorrido de pares `(id, metadata_score)`. `retrieved_ids[i]` y `scores[i]` siguen perteneciendo al mismo `MemoryResult` y ranking.

Cada retrieval hibrido realiza exactamente una consulta episodica y una semantica. Health se deriva de esas respuestas. No hay retry, audit query ni health query.

La consulta relacional preexistente es funcionalmente distinta y no se introdujo para observabilidad.

## 26. AuditTrace v1

AuditTrace permanece congelado. Orchestrator sigue leyendo `texto`, `retrieved_ids` y `scores`; su schema memory conserva exactamente `retrieved_ids`, `scores`, `created_ids` y `working_memory_count`. No se modificó `core/orchestrator.py`.

Dos fixtures de observabilidad se actualizaron para devolver el nuevo receipt desde la misma busqueda simulada; esto conserva la prueba OBS-005 y no altera producción ni el JSONL.

## 27. ChromaClient

La semantica no cambió: dict significa operacion completada, `None` fallo/unavailable y `ids=[[]]` no-hit sano. Sólo el log de `buscar()` fue redactado para emitir operation, safe reason y `error_type`; nunca exception string, query, metadata o traceback.

La prueba temporal real exige ahora que buscar sobre una coleccion sana vacia devuelva un dict, no `None`.

## 28. Logging

Los boundaries modificados registran sólo componente, operacion, source, health, reason code, counts y/o `error_type`. No registran query, documento, metadata, JSON, cognition, exception message ni `exc_info`. ROOT-014 no hizo una limpieza global de logging.

## 29. Pruebas ROOT-014

`memory.test_retrieval_health` contiene 55 unidades permanentes:

- store empty/failure/hits;
- matriz hibrida completa;
- aislamiento V1, unknown schema, metadata None, documento y distancia;
- rechazo de batches desalineados;
- V0 sano y timestamp V0 invalido;
- naive/aware/offset/mixed/future;
- un solo now;
- ranking, score sin clamp y OBS-005;
- no segunda consulta;
- receipts sin payload raw;
- ordering/dedupe de reason codes;
- invariantes de counts;
- adapters de una sola llamada;
- callers productivos principal/relacional;
- defaults/text semantics y fallo del summarizer.

`ai_engine.test_memory_decay` protege ademas que consolidacion aisle timestamps futuros/corruptos sin alterar la seleccion sana.

## 30. Evidencia de probes

`memory_deep_probe` muestra:

```text
chroma_failure_at_store = FAILED
valid_no_hit_at_store = OK_EMPTY
partial_failure_visible_to_caller = true
both_error_equals_no_hit = false
obs005_hybrid_search_calls = 1
obs005_ids_scores_aligned = true
aware_timestamp_poisoned_all_results = false
future_timestamp_recency = rejected:FUTURE_TIMESTAMP
poison_visible_to_manager = true
metadata_score_bounded_0_1 = false
```

Los grupos ROOT-014 de `contracts_memory_probe` confirman batches malformed como `PARTIAL`, metadata None aislada, no timestamp→now, naive/aware equivalentes, futuro rechazado y facade `OK_EMPTY != PARTIAL`.

## 31. ROOT-013 regression

`memory.test_schema_faithful_persistence`: `66/66 PASS`. V1 strict, duplicate JSON keys, state, origin, collisions, V0 valido, filtros y proyecciones permanecen.

## 32. ROOT-039 regression

`memory.test_metadata_contract`: `41/41 PASS`. None, `{}` y metadata presente conservan el contrato de transporte. ROOT-014 no reabre ADD/UPDATE/read semantics.

## 33. Boundaries pendientes

- ROOT-015: `OPEN / NOT STARTED`; sin receipts de escritura, atomicidad, dedupe, journal, CAS, 2PC ni retry.
- ROOT-040: `OPEN / NOT STARTED`; sin redefinir formula/rango de `metadata_score`.
- ROOT-041: `OPEN / NOT STARTED`; receipts frozen top-level, sin deep immutability.
- MEM-RETRIEVE-002: `_recencia_horas` no fue implementado; un fallo del filtro ahora queda visible en health.

## 34. Sistemas no tocados

No se modificaron API, Unity, terminal, Orchestrator productivo, AuditTrace, TEARS, DevelopmentalCore, Canonical Cognitive DB, Rust, C ni Swap Area. No hubo red, LLM real ni field test.

## 35. Gate de cierre

La regresion global final post-implementacion y documentacion descubrió y ejecutó `544/544 PASS`, `0 XFAIL`, `0 unexpected`, cero import failures, cero fatal/cleanup errors y `suite_gate = isolation_gate = gate = PASS`. El hash del commit unico se informa en la entrega final.

## 36. Active Chroma y runtime DB

Las pruebas reales usan `TemporaryDirectory` y embedding offline. El runner bloquea acceso a `lazzaro_data`, red y subprocesses no autorizados, y compara los tres hashes activos. La busqueda final de SQLite fuera de `lazzaro_data/.git` debe ser vacia.

## 37. Riesgos residuales

Registros V0 sin timestamp dejan de ser recuperables; es deliberado para no inventar historia. Tolerancia futura es cero porque no existe evidencia de clock skew aceptable. Los adapters legacy pueden descartar health para callers que explicitamente sólo requieren una lista; el pipeline cognitivo principal no los usa. La inmutabilidad profunda de los `MemoryResult` queda para ROOT-041.

## 38. Estado final

ROOT-014 queda `CLOSED`: focales, regresiones heredadas, probes, gate global, compileall, diff check, hashes y busqueda de runtime DB quedaron verdes. El cierre usa un unico commit conceptual y no se hace push.
