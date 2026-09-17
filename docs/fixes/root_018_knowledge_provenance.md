# ROOT-018 — Provenance de conocimiento e identidad de cache Oracle

Estado: `IMPLEMENTED / HUMAN_REVIEW_PENDING`.

## Problema histórico

La ruta Oracle reducía el origen del conocimiento a `fuente` y `cache_hit`.
Después de un hit semántico no era posible demostrar qué proveedor produjo el
contenido, qué fallos/fallbacks precedieron al productor aceptado, qué versión
de contrato se almacenó ni distinguir la consulta actual de la consulta que
originó la fila. Además, el boundary legacy de Chroma devolvía `None` tanto para
un miss limpio como para un backend indisponible o fallido.

ROOT-018 conserva esas realidades sin cambiar la narrativa pública, los prompts,
AuditTrace v1 ni los contratos API/Unity.

## Objeto de provenance

Todo `OracleResult` exige un `KnowledgeProvenanceV1`; no existe default
silencioso. Un caller que omite provenance falla validación. La entrada legacy
requiere invocar explícitamente `legacy_unknown_provenance(...)`.

La estructura v1 separa dos autoridades:

- `ProducerReceiptV1`: origen histórico e inmutable del conocimiento;
- `RetrievalReceiptV1`: cómo el turno actual lo obtuvo y qué ocurrió con cache.

El productor conserva `kind`, `route`, provider, model, parse status/version,
fallback entre proveedores, cantidad de receipts del scheduler, clases de fallo
estables, `generation_terminal`, `final_failure` y `accepted_at_utc`. Ese
timestamp es el momento local en que Lazzaro aceptó la salida, no una afirmación
sobre cuándo computó el modelo remoto.

`scheduler_attempt_count` es exactamente `len(LLMCallResultV1.receipts)`. No
pretende contar requests HTTP, porque ROOT-021 puede resolver incompatibilidad
de proyección antes del transporte. Las failure classes son únicas, estables y
en orden de primera aparición; un receipt exitoso no agrega una clase.
Ese resumen no reconstruye causalidad: `generation_terminal` y `final_failure`
se copian literalmente de `LLMCallResultV1.terminal/final_failure`. Así,
`DEADLINE_EXCEEDED`, `RETRY_EXHAUSTED`, `NON_RETRYABLE_FAILURE`,
`ALL_PROVIDERS_FAILED` y `NO_COMPATIBLE_PROVIDER` permanecen distinguibles aun
cuando dos agregados de receipts se parezcan.

`provider_fallback_used` es `true` sólo si, antes del receipt exitoso aceptado,
existe al menos un receipt con una identidad de proveedor diferente. Reintentos
del mismo proveedor no cuentan. En ROOT-021 una proyección incompatible no crea
receipt y por eso no altera este campo ni el attempt count.

Las combinaciones del productor son estrictas:

- `PROVIDER_GENERATED` exige terminal `SUCCESS`, `final_failure=None`, provider,
  timestamp de aceptación, parse `VALID|DEGRADED` y al menos un receipt;
- `LOCAL_DETERMINISTIC` con tentativa técnica conserva exactamente terminal,
  fallo final, cantidad y clases de esa tentativa; sin tentativa usa terminal y
  fallo `None`, cero receipts y cero failure classes;
- `LEGACY_UNKNOWN` usa terminal/fallo `None` porque no puede reconstruir estado
  histórico ROOT-021.

## Ruta del productor

La ruta se deriva de los slots configurados antes del dispatch:

- slot `OracleClient` externo → `ORACLE_EXTERNAL`;
- slots de la cadena activa → `ACTIVE_PROVIDER_CHAIN`;
- resultado local determinista → `LOCAL_DETERMINISTIC`;
- entrada histórica sin metadata v1 → `LEGACY_UNKNOWN`.

No se infiere la ruta con heurísticas sobre strings. Los `provider_name` vacíos
o duplicados entre slots se rechazan antes de ejecutar; el proveedor final debe
estar en el mapa precalculado.

## Boundary tipado de Chroma

`ChromaClient.buscar_con_estado(...)` es una API interna aditiva. La API legacy
`buscar(...)` conserva su semántica para callers anteriores.

El resultado tipado distingue:

- `SUCCESS`: un único batch bien formado con al menos una fila;
- `EMPTY`: un único batch válido sin filas;
- `UNAVAILABLE`: colección no disponible;
- `ERROR`: excepción de backend o respuesta malformada, con failure code
  acotado y sin exception text.

Se validan alineación, tipos primitivos y distancias finitas. No se consulta
Chroma una segunda vez para construir telemetría.

## Receipt de cache

`CacheReceiptV1` contiene una única autoridad para:

- `lookup_status`: `NOT_CHECKED`, `MISS`, `HIT`, `UNAVAILABLE`, `ERROR`,
  `MALFORMED_ENTRY`, `INCOMPATIBLE_VERSION` o `LEGACY_ENTRY`;
- `store_status`: `NOT_ATTEMPTED`, `STORED`, `FAILED` o `SKIPPED_POLICY`;
- identidad SHA-256/key de la consulta actual;
- identidad física y lógica de la entrada encontrada o creada;
- distancia, threshold y timestamp UTC de la entrada.

Un hit nunca puede declarar escritura y sólo `STORED` puede portar una nueva
identidad almacenada. `fuente` y `cache_hit` siguen disponibles como computed
fields derivados de provenance; si un constructor intenta suministrarlos con
valores contradictorios, la validación falla.

Además, `HIT` exige una `matched_entry` v1 completa (tres versiones, key lógica,
hash de Q1 y timestamp); `LEGACY_ENTRY` exige que todos esos campos sean `None`.
No se puede etiquetar una identidad legacy como v1 ni una identidad v1 como
legacy mediante construcción directa.

## Coherencia productor/retrieval

`KnowledgeProvenanceV1` rechaza estados imposibles:

- `LEGACY_ENTRY` existe si y sólo si el productor es `LEGACY_UNKNOWN`;
- un `HIT` v1 exige productor histórico `PROVIDER_GENERATED`;
- `LOCAL_DETERMINISTIC` siempre usa path `GENERATED` y nunca cache hit;
- un productor provider puede ser generación tras lookup o hit v1, pero no
  `LEGACY_ENTRY` ni `NOT_CHECKED`.

La caracterización del flujo confirmó que `NOT_CHECKED` productivo sólo aparece
en el resultado local de una consulta vacía antes de cache/LLM. Toda generación
por provider atraviesa primero un lookup tipado; por eso provider +
`NOT_CHECKED` se rechaza sin eliminar un caso productivo.

## Identidad Q1/Q2

Para un hit semántico:

- Q2 es la consulta del turno actual;
- Q1 es la consulta histórica que originó la fila;
- `lookup_query_sha256` y `lookup_logical_key` pertenecen a Q2;
- `matched_entry.stored_query_sha256` y `matched_entry.logical_key` pertenecen
  a Q1;
- `matched_entry.entry_id` es el UUID físico de la fila Q1;
- `OracleResult.pregunta_original` permanece Q2;
- el `ProducerReceiptV1` restaurado permanece el productor histórico de Q1.

La key lógica es SHA-256 de JSON canónico versionado. La única normalización de
la consulta es NFC + `strip`; no hay `casefold`, colapso de whitespace interno
ni `hash()` de Python. El UUID físico no se reutiliza como key lógica.

## Árbol de lectura

La lectura productiva devuelve siempre `OracleCacheLookupResultV1`:

1. consulta vacía → `NOT_CHECKED`;
2. colección no disponible → `UNAVAILABLE`;
3. excepción/respuesta malformada del backend → `ERROR`;
4. batch vacío o candidato sobre threshold → `MISS`;
5. ausencia de los tres campos de versión → candidato `LEGACY_ENTRY`;
6. presencia parcial, tipos corruptos o receipt/identidad v1 inválidos →
   `MALFORMED_ENTRY`;
7. versiones enteras válidas pero no soportadas → `INCOMPATIBLE_VERSION`;
8. metadata v1 compatible bajo threshold → `HIT`.

Todos los estados no-hit continúan a generación bajo la política existente.
Un legacy bien formado puede servirse únicamente como `LEGACY_UNKNOWN`, sin
provider/model/timestamp reconstruidos desde la configuración actual. Un legacy
malformado no se sirve.

## Política de escritura

- generado por provider + parse `VALID` → una tentativa, `STORED` o `FAILED`;
- `DEGRADED` → `SKIPPED_POLICY`;
- fallback local determinista/`ERROR` → `SKIPPED_POLICY`;
- `HIT` o `LEGACY_ENTRY` → `NOT_ATTEMPTED`.

Un write fail no invalida ni regenera el conocimiento ya aceptado. Los hits no
se re-cachean, por lo que no existe laundering de provenance histórica.
El JSON canónico del productor incluye terminal y fallo final; un hit restaura
el receipt histórico byte/semánticamente equivalente aunque cambie la
configuración actual de providers.

## Fronteras preservadas

ROOT-018 no afirma factualidad. Un hit por asociación semántica conserva la
evidencia de recuperación, pero no certifica que el contenido sea verdadero ni
adecuado para Q2; esa verificación sigue en ROOT-017.

Tampoco coalescea misses concurrentes ni agrega locks/single-flight; ROOT-053
permanece sin iniciar. No se modificaron Persona, PromptPlan, los prompts de
Reasoning/Persona/Oracle, API, Unity, AuditTrace v1, ActionLedger, Memory general
ni DevelopmentalCore.

ROOT-020 sigue siendo la autoridad de `reason_codes` detallados. ROOT-018
persiste el status/version de parse necesarios, pero no duplica esos códigos:
ROOT-017 aún no define una necesidad durable que justifique ampliar este
contrato y no se inició en este packet.

## Pruebas

Las matrices permanentes son:

- `memory.test_chroma_search_status`: 12 casos del boundary tipado;
- `ai_engine.test_knowledge_provenance`: 44 casos de provenance, causalidad
  ROOT-021, rutas, coherencia, identidad, versionado, legacy, Q1/Q2, lectura y
  escritura;
- `ai_engine.test_oracle`: integración Oracle→Persona;
- regresiones ROOT-021/019/020/008/016 y suite aislada global.

Todas usan fakes sintéticos; no abren red, LLM real, Unity ni Chroma activo.
