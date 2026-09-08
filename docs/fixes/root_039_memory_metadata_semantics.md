# ROOT-039 — Stable memory metadata semantics

## 1. ROOT

ROOT-039 estabiliza la semantica de metadata ausente, vacia y presente en la memoria legacy de LAZZARO. Es un prerequisito de ROOT-013 y ROOT-014 y no requiere field test.

## 2. Causa

LAZZARO declaraba `MemoryDocument.metadata` como `dict`, permitia construirlo vacio y pasaba metadata sin canonicalizacion a Chroma. ChromaDB 1.5.9 acepta `None`, rechaza `{}` en ADD y acepta `None` como no-op exitoso en UPDATE. Type, adapter, backend y tests expresaban contratos distintos.

## 3. Ticket MEM-CONTRACT-004

El ticket absorbido es exclusivamente `MEM-CONTRACT-004`: `None` y `{}` no tenian semantica estable. No se absorben fidelidad de schema, health receipts, idempotencia, scoring ni deep immutability.

## 4. MEM-AUDIT-001 historico

`memory.test_chromadb_flow.test_eliminacion` intentaba insertar tres documentos con `metadata={}`. El backend rechazaba la insercion, `ChromaClient.agregar()` devolvia `False` y el test fallaba en `count == 3`; DELETE nunca se ejecutaba. El hallazgo queda cerrado como precondicion de insercion, no como defecto de DELETE.

## 5. Chroma version

La version efectiva caracterizada es ChromaDB `1.5.9`. La prueba usa `TemporaryDirectory`, colecciones sinteticas y embeddings explicitos; nunca abre `./lazzaro_data`.

## 6. Backend characterization

| Operacion | Input | Chroma 1.5.9 |
|---|---|---|
| ADD | `None` | accepted; read `None` |
| ADD | `[]` | `ValueError` |
| ADD | `[None]` | accepted; read `None` |
| ADD | `[{}]` | `ValueError` |
| ADD | `[non-empty]` | accepted |
| UPDATE | `None` | accepted no-op |
| UPDATE | `[]` | `ValueError` |
| UPDATE | `[None]` | accepted no-op |
| UPDATE | `[{}]` | `ValueError` |
| UPDATE | `[non-empty]` | accepted merge |

La conducta del backend es evidencia, no authority del contrato LAZZARO.

## 7. Metadata contract map

| Nivel | Ausencia | Vacio | Presente |
|---|---|---|---|
| `MemoryMetadata.extra` | no necesario | valido: cero extensiones | extensiones opcionales |
| `MemoryDocument.metadata` | invalido | invalido | dict no vacio requerido |
| Chroma ADD transport | `None` | alias legacy de ausencia | dict no vacio |
| Chroma UPDATE transport | invalido | invalido | dict no vacio requerido |
| Chroma READ transport | `None` | canonicalizado a `None` | dict no vacio |

## 8. Callsite map

| Caller | Coleccion | Producer | Forma contractual |
|---|---|---|---|
| `EpisodicMemoryStore.almacenar()` | `experiencias` | `MemoryEncoder` | metadata cognitiva no vacia |
| `SemanticMemoryStore.almacenar()` | `experiencias` | `MemoryEncoder` | metadata cognitiva no vacia |
| `OracleMemory.guardar_en_cache()` | `oracle_cache` | dict del dominio Oracle | metadata de transporte no vacia |
| `MemoryManager._asociar_transaccion_a_guardado()` | `experiencias` | copia de metadata leida + hash | UPDATE no vacio |
| tests/probes | temporales | fixtures sinteticos | matriz explicita ROOT-039 |

## 9. Read consumer map

`EpisodicMemoryStore` y `SemanticMemoryStore` reconstruyen `MemoryMetadata` y requieren metadata cognitiva. Oracle interpreta ausencia como cache miss. Los lectores directos de `MemoryManager` y Telescope ya verifican que cada fila sea `dict` o aplican defaults locales. ROOT-039 entrega `None` o dict no vacio sin rediseñar esos consumidores.

## 10. Policy A

Normalizar `None` y `{}` como ausencia en todos los niveles simplifica transporte, pero permitiria memorias cognitivas sin metadata central. Se rechazo como politica global.

## 11. Policy B

Exigir metadata no vacia en todos los niveles protege memorias cognitivas, pero impone una restriccion cognitiva al wrapper generico usado tambien por `oracle_cache`. Se adopta solo para `MemoryDocument` y UPDATE.

## 12. Policy C

El contrato de memoria cognitiva y el contrato de transporte Chroma son distintos. Esta separacion representa ausencia en el adapter sin autorizar un `MemoryDocument` desnudo.

## 13. Politica elegida

Se adopta Policy C refinada: ADD acepta ausencia; UPDATE exige un cambio real; READ usa una sola forma de ausencia; `MemoryDocument` exige metadata central no vacia.

## 14. Canonicalization owner

`MemoryDocument` es owner de validacion cognitiva. `ChromaClient` es la unica canonicalization authority de transporte. Encoder y stores no añaden otra representacion de ausencia.

## 15. None semantics

`None` representa metadata ausente en ADD y READ. En UPDATE es invalido porque Chroma lo convierte en un no-op reportado como exitoso.

## 16. Empty dict semantics

`{}` es invalido en `MemoryDocument` y UPDATE. ADD lo acepta como alias legacy y crea una copia canonicalizada a `None`. READ convierte cualquier `{}` del backend a `None`.

## 17. Non-empty semantics

Un diccionario no vacio representa metadata presente. ADD y UPDATE envian una copia equivalente para no mutar ni compartir el contenedor del caller.

## 18. MemoryMetadata.extra distinction

`MemoryMetadata.extra={}` sigue siendo valido y significa que no existen extensiones opcionales. `MemoryEncoder` agrega `tipo`, `tipo_evento`, `valencia`, `impacto_emocional`, `timestamp` y, en semantica, `origen`; por eso la metadata total del documento no queda vacia.

## 19. MemoryDocument contract

`MemoryDocument.__post_init__()` exige que `metadata` sea un `dict` no vacio. `None` produce `TypeError` y `{}` produce `ValueError` antes de cualquier store o backend. No se validan reserved keys, schema completo, origen, estado emocional ni extras.

## 20. ChromaClient ADD

ADD acepta `None`, dict singular o una lista alineada con IDs. Por fila, `None` permanece `None`, `{}` se vuelve `None` y un dict no vacio se copia. `[]`, contenedores invalidos y batches de metadata desalineados retornan `False` antes del backend.

## 21. ChromaClient UPDATE

UPDATE acepta un dict no vacio para un ID o un batch alineado de dicts no vacios. `None`, `[]`, `[None]`, `{}` y `[{}]` retornan `False` sin invocar el backend. El retorno publico sigue siendo `bool`; no se crea receipt.

## 22. Read normalization

`listar()` conserva el shape plano `[None, {...}]`. `buscar()` conserva el shape nativo anidado `[[None, {...}]]`. Ambas rutas copian listas de metadata y convierten `{}` a `None`; no envuelven ni rediseñan `QueryResult`.

Metadata ausente en una memoria cognitiva significa registro invalido. Su aislamiento y health status pertenecen a ROOT-014.

## 23. Mutation policy

Canonicalization crea listas y diccionarios nuevos. Las pruebas conservan igualdad de `source_metadata`, `source_list` y sus copias previas despues de ADD/UPDATE.

## 24. Error policy

Input de metadata invalido falla deterministicamente con `False` antes del backend. El warning contiene `operation`, `reason_code` e `input_type`, nunca metadata, documentos, contenido, prompts ni texto de memoria.

## 25. Historical delete resolution

El fixture de eliminacion inserta metadata sintetica no vacia, exige `agregar() == True`, verifica count, llama al DELETE real y comprueba count y IDs posteriores. `ChromaClient.eliminar()` no cambia.

## 26. Known XFAIL before/after

Antes: `memory.test_chromadb_flow.test_eliminacion` era el unico `KNOWN_XFAIL`. Despues: su precondicion es valida, DELETE se ejecuta y la expectativa se retira del regression probe. El gate espera cero known failures mientras no exista otra entrada explicita.

## 27. ROOT-013 boundary

ROOT-013 permanece `OPEN / NOT STARTED`. No se persiste `estado_emocional`, no se resuelven colisiones reservadas, extras no escalares, divergencia de origen, schema version ni migracion.

## 28. ROOT-014 boundary

ROOT-014 permanece `OPEN / NOT STARTED`. No se crean `RetrievalReceipt`, `OK_EMPTY`, `PARTIAL`, `FAILED`, normalizacion UTC, aislamiento temporal ni recuperacion de poisoned records.

## 29. ROOT-015 boundary

ROOT-015 permanece `OPEN / NOT STARTED`. No se crean write receipts, idempotency keys, journals, transacciones Chroma, CAS, 2PC ni consolidacion atomica.

## 30. Tests

`memory.test_metadata_contract` cubre validacion cognitiva, ADD/UPDATE/READ, no mutacion, logs acotados, Oracle, version/backend temporal, matrices y ejecucion real del delete historico: `41/41 PASS`. `memory.test_chromadb_flow` completa sus siete casos y `memory_deep_probe` completa nueve grupos con datos sinteticos.

Los dos grupos de `contracts_memory_probe` afectados por ROOT-039 pasan. El entry point integral conserva una expectativa preexistente de ROOT-006 sobre sentinels string/bool que ya no coincide con el codigo base; no se altero porque el packet autoriza cambiar en ese probe unicamente expectativas de ROOT-039. El regression gate autoritativo ejecuta `423/423 PASS`, `0 KNOWN_XFAIL` y `0 unexpected`.

## 31. Risks

Los principales riesgos son filtros legacy sobre metadata ausente, cambio futuro de Chroma y records cognitivos antiguos con metadata nula. ROOT-039 no inspecciona ni migra la base activa; deja health/isolation para ROOT-014 y schema fidelity para ROOT-013.

## 32. Final criterion

Contrato oficial: "En LAZZARO, None representa metadata ausente en ADD/read, un diccionario no vacio representa metadata presente, y {} es invalido como MemoryDocument o UPDATE, aunque ADD lo acepta como alias legacy y lo canonicaliza a None."

El cierre exige focales y regresion global verdes, cero XFAIL ficticio, hashes activos identicos, cero SQLite runtime accidental, compileall y diff checks limpios.

## 33. Final state

ROOT-039 queda `CLOSED`: focales, memoria profunda, regresiones historicas y gate global estan verdes; el XFAIL ficticio fue retirado. El trabajo parte de `21e1a0debf229fa7851f9f4ea3950bc43f9ebce7` sin amend, revert ni cambios a sus artefactos. No hubo field test, Unity, red, LLM real ni push.
