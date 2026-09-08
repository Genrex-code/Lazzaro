# ROOT-013 — Canonical memory schema fidelity

## 1. ROOT-013 state

ROOT-013 cierra `MEM-CONTRACT-001`, `MEM-CONTRACT-005` y `MEM-CONTRACT-009`. La causa era una persistencia lossy: `MemoryEncoder` descartaba extras no escalares y `estado_emocional`, permitía que `extra` sobrescribiera core fields y mantenía defaults de `origen` divergentes. El estado final es `CLOSED` con schema V1 fiel, lectura V0 compatible y sin migración activa.

## 2. Base HEAD

La implementación parte de `ab17107d820cc8e352caa584f9d6838c1c05332f` (`fix(memory): define empty metadata semantics`) en `codex/stabilization/pre-capa2`. ROOT-039 estaba cerrado y el baseline autoritativo era `423/423 PASS`, cero XFAIL y cero inesperados.

## 3. Final commit

El cierre se entrega en un único commit conceptual `fix(memory): preserve canonical memory schema`. El hash final se registra en la entrega del agente después de crear el commit; no se hace amend, squash, rewrite ni push.

## 4. Exact V1 schema

Episódica:

```text
__lazzaro_memory_schema_version: int exacto 1
tipo: "episodico"
tipo_evento: str
valencia: int/float finito, persistido como float
impacto_emocional: int/float finito, persistido como float
timestamp: datetime.isoformat()
__lazzaro_memory_extra_json: JSON canónico cuyo root es object
__lazzaro_memory_state_json: JSON canónico null u object
...proyecciones escalares seguras de extra
```

Semántica:

```text
__lazzaro_memory_schema_version: int exacto 1
tipo: "semantico"
tipo_evento: str
valencia: int/float finito, persistido como float
impacto_emocional: int/float finito, persistido como float
timestamp: datetime.isoformat()
origen: str
__lazzaro_memory_extra_json: JSON canónico cuyo root es object
...proyecciones escalares seguras de extra
```

## 5. Final authority map

| Dato | Authority |
|---|---|
| ID y narrativa/regla | Chroma `ids` / `documents` |
| Core de memoria | top-level canonical fields |
| `extra` V1 | `__lazzaro_memory_extra_json` exclusivamente |
| `estado_emocional` V1 | `__lazzaro_memory_state_json` exclusivamente |
| Filtros y raw readers | proyección escalar top-level derivada |

Las proyecciones nunca participan en la reconstrucción V1. V0, que no posee payload, continúa reconstruyendo extra desde campos top-level desconocidos.

## 6. Reserved namespace

El prefijo `__lazzaro_memory_` queda reservado para authority interna. Las keys internas V1 son `__lazzaro_memory_schema_version`, `__lazzaro_memory_extra_json` y `__lazzaro_memory_state_json`. Una key homónima dentro de `extra` se conserva como data en el payload y nunca se proyecta top-level.

## 7. JSON domain

`extra` admite solamente `None`, `bool`, `int`, `float` finito, `str`, listas recursivas de esos valores y `dict[str, JSONValue]`. Rechaza keys no string, NaN, infinitos, `datetime`, tuple, set, bytes, objetos arbitrarios, ciclos y cualquier coerción/stringification. El encoder usa `ensure_ascii=False`, `sort_keys=True`, separadores compactos y `allow_nan=False` sin mutar al caller.

## 8. State domain

`estado_emocional` admite `None` o `dict[str, bool | int | float finito]`. No admite estructuras anidadas ni otros tipos. `None` se codifica como `null`; `{}` como `{}`; ambos se reconstruyen sin colapsarse.

## 9. Schema version strictness

El dispatch acepta V1 sólo cuando `type(marker) is int` y `marker == 1`. `True`, `False`, `1.0`, `"1"`, tipos no hashables y versiones desconocidas fallan cerradas con `MemorySchemaDecodeError`; nunca degradan a V0.

## 10. Duplicate-key policy

El decoder data-only usa `json.loads` con un `object_pairs_hook` que rechaza keys duplicadas en cualquier object del payload. `{"x":1,"x":2}` produce un reason code determinista y no se evalúa ni interpreta con `eval`, `literal_eval` o pickle.

## 11. Episodic roundtrip evidence

Un episodio rico fue codificado, escrito en ChromaDB 1.5.9 temporal y leído por `EpisodicMemoryStore`. Se preservaron ID, narrativa, core, timestamp, Unicode, bool/int, null, listas, objetos anidados, colisiones reservadas y el snapshot emocional completo. La equality semántica del objeto before/after pasó.

## 12. Semantic roundtrip evidence

Una memoria semántica rica siguió el mismo ciclo real temporal. Se preservaron ID, regla, core, timestamp aware, `origen`, extras escalares filtrables y extras ricos no escalares. El `extra["origen"]` de colisión sobrevivió como data sin alterar el core `origen`.

## 13. None versus empty state evidence

Pruebas permanentes validan `None -> "null" -> None` y `{}` -> `"{}"` -> `{}`. Un estado no vacío conserva bool, int y float sin convertir `False` en `0`.

## 14. Collision evidence

`extra["tipo"]`, `extra["timestamp"]`, `extra["origen"]` semántico y keys del namespace interno sobreviven dentro del payload. Ninguna sobrescribe tipo, timestamp, origen o schema version canónicos, y ninguna key reservada se proyecta. La reconstrucción V1 ignora incluso proyecciones top-level conflictivas o forjadas.

## 15. Origin decision

El default canónico para nuevas memorias semánticas es `CANONICAL_DEFAULT_ORIGIN = "desconocido"` tanto en `SemanticMemory`, `MemoryEncoder` como `MemoryManager`. El fallback de lectura V0 permanece `LEGACY_DEFAULT_ORIGIN = "determinista"`. `origen` representa el workflow productor conocido, no provenance completo.

## 16. Productive omitted-origin callsites

La búsqueda final encontró `productive omitted-origin callsites: 0`. Alias aprendido, memoria afectiva, guardado manual conversacional, hecho relacional y consolidación pasan un origen explícito porque conocen su workflow. Las omisiones restantes son defaults deliberados o fixtures/probes, no producers productivos que oculten conocimiento.

## 17. V0 compatibility

La ausencia total del marker selecciona el decoder V0. Episodic conserva core recuperable, extra escalar top-level y `estado_emocional=None`. Semantic preserva un origen presente o usa `"determinista"` si falta. No se inventan extras no escalares ya perdidos ni se reparan colisiones históricas.

## 18. Unknown version behavior

Un marker entero distinto de 1 produce `MemorySchemaDecodeError(reason_code="unknown_schema_version")`. El error sólo expone version segura y tipo seguro cuando existen; no contiene metadata, documento, JSON, estado ni extra.

## 19. Malformed V1 behavior

Marker de tipo incorrecto, tipo ausente/equivocado/no hashable, core ausente o inválido, bool/no-finito en numerics, timestamp inválido, origen semántico ausente, payload ausente/malformado, roots incorrectos, duplicate keys y estado fuera de dominio fallan determinísticamente. No hay fallback V0 para un record que declaró V1.

## 20. Filter regression

La prueba real preserva filtros por `tipo=episodico`, `tipo=semantico`, `tipo_evento`, `impacto_emocional`, `valencia`, huellas de guardado manual, transacción, relación, `lote_consolidacion`, alias, afecto, `fuente`, `canal`, `accion`, `objeto` y `origen`: 16/16 filtros encontraron el record esperado.

## 21. Raw reader regression

Alias, affective, manual, relationship, consolidation y Telescope continúan consumiendo proyecciones escalares. La prueba raw confirma `origen`, `objeto`, `accion` y marker V1 top-level, mientras stores reconstruyen exclusivamente desde el payload.

## 22. Manual-update synchronization

`MemoryManager._asociar_transaccion_a_guardado()` usa `MemorySchemaCodec.update_scalar_extra()`. En V1 decodifica el extra canónico, modifica una copia, regenera JSON y reemplaza la proyección escalar completa; payload y `transacciones_guardado_hashes` top-level quedan iguales. V0 mantiene el update plano histórico. No se añade CAS, retry, journal ni transacción.

## 23. No-mutation evidence

Las pruebas comparan snapshots antes/después del encode y update. `extra`, listas/objetos anidados, `estado_emocional` y metadata entregada al helper permanecen sin mutación. La regeneración devuelve un diccionario nuevo.

## 24. Temporary Chroma evidence

Los roundtrips usan `TemporaryDirectory`, una colección sintética y embedding determinista offline. ChromaDB observado: 1.5.9. La ruta temporal se verifica distinta y fuera de `./lazzaro_data`; no hubo red ni LLM real.

## 25. Active Chroma hashes

Hashes SHA-256 antes y después:

| Archivo | SHA-256 |
|---|---|
| `lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin` | `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659` |
| `lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin` | `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A` |
| `lazzaro_data/chroma.sqlite3` | `C003D87617B5156E358E4DD56DAB9B8765401AF239F809750627E42A77D23F6D` |

Los tres son idénticos al baseline. El runner global también reportó `active_chroma_sha256_unchanged=true` y bloqueo de cualquier acceso a esa ruta.

## 26. ROOT-039 regression

`memory.test_metadata_contract` pasa `41/41`. ADD/UPDATE/read, ausencia/vacío/presente, no mutación, Oracle y caracterización real temporal de Chroma 1.5.9 permanecen intactos. `memory.test_chromadb_flow` pasa sus 7 unidades.

## 27. ROOT-014 state

ROOT-014 permanece `OPEN / NOT STARTED`. Un record V1 malformado puede propagar `MemorySchemaDecodeError` y afectar la consulta. No se implementan per-record isolation, health receipts, `OK_EMPTY`, `PARTIAL`, `FAILED`, normalización UTC ni corrección temporal.

## 28. ROOT-015 state

ROOT-015 permanece `OPEN / NOT STARTED`. El update manual sólo sincroniza schema; no mejora atomicidad, idempotency keys, transacciones Chroma, consolidación, CAS, 2PC, journaling o receipts de escritura.

## 29. ROOT-018 state

ROOT-018 permanece `OPEN / NOT STARTED`. `origen` se limita al workflow productor ya conocido y no se convierte en un sistema nuevo de provenance.

## 30. Focal tests

`memory.test_schema_faithful_persistence` pasa `66/66`. Los tres grupos ROOT-013 de `contracts_memory_probe` pasan; `memory_deep_probe` pasa 9 grupos; `ai_engine.test_manual_memory_save` y las regresiones de manager pasan. Las suites ROOT-001 a ROOT-012 solicitadas, incluidos ROOT-009/010 API y Unity estático, permanecen verdes. No se modificó la expectativa stale ROOT-006 del entry point integral de contracts.

## 31. Global regression real

`docs/audit/probes/regression_probe.py` descubre y ejecuta `489/489 PASS`, `0 KNOWN_XFAIL`, `0 unexpected`, cero import failures, cero fatal/cleanup errors y `suite_gate = isolation_gate = gate = PASS`. El incremento real frente a 423 corresponde a las 66 pruebas permanentes ROOT-013.

## 32. Compileall

`python -m compileall -q memory ai_engine docs/audit/probes` termina con exit code 0.

## 33. Diff check

`git diff --check` termina sin whitespace errors. Los avisos locales de futura conversión LF/CRLF no son errores del diff ni alteran contenido.

## 34. Runtime DB search

La búsqueda repo-wide de `*.sqlite`, `*.sqlite3` y `*.db`, excluyendo `lazzaro_data/**` y `.git/**`, devuelve cero archivos. Las bases creadas por tests estuvieron en directorios temporales ya limpiados.

## 35. Git status

Antes del commit sólo aparecen los archivos aprobados de ROOT-013. Después del commit, `git status --short` debe quedar vacío. No se toca `memory/chroma_client.py`.

## 36. AuditTrace v1

AuditTrace v1 permanece congelado. La prueba permanente conserva exactamente `retrieved_ids`, `scores`, `created_ids` y `working_memory_count` en la sección memory y confirma que keys/payloads internos ROOT-013 ni JSON rico entran en la traza.

## 37. Network and push state

No hubo llamadas de red, providers, LLM real ni field test. El runner bloqueó red y subprocesses no autorizados. El commit queda únicamente local: `NO push`.

## 38. Out-of-scope systems

Capa 2, TCM, TEARS, DevelopmentalCore, Canonical Cognitive DB y cualquier DB cognitiva nueva permanecen intactos. No se inició migración, no se abrió/rewriteó la Chroma activa y ROOT-040/041 no se tocaron.
