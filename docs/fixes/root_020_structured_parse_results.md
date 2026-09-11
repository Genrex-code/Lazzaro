# ROOT-020 — Resultados explícitos de parsing estructurado

## 1. ROOT y tickets

ROOT-020 (P1, developmental gate) cierra AI-CONTRACT-001,
LLM-CONTRACT-001 y C2-CONTRACT-002. Su alcance es parsing estructurado y la
policy mínima necesaria para no promover resultados degradados como autoridad.

## 2. Baseline

La implementación parte de
`85669b7e35a40c55b26f7952adf6d265dc414a78`, con árbol limpio y baseline
global de 600/600 PASS, cero XFAIL y cero resultados inesperados.

## 3. Inventario de parsers

- `ResponseParser`: respuesta general `LLMResponse -> ActionResponse`.
- `PersonaEngine`: salida social JSON y texto comunicable.
- `OracleEngine`: conocimiento técnico interno y cache Oracle.
- `OllamaClient`: boundary productivo que antes retiraba fences/`think`
  antes de entregar el raw al parser.

## 4. Matriz de outcomes anterior

Los parsers no compartían definición de validez. General aceptaba extracción
desde primer/último brace; Persona tomaba el primer objeto y convertía tipos con
`str()`; Oracle promovía texto plano y aliases a conocimiento cacheable.
Duplicados usaban silenciosamente last-key-wins.

## 5. Escape de excepciones anterior

El reproducer `pensamiento={}` fallaba primero al validar
`LLMResponse` y volvía a fallar al reconstruir `ActionResponse` con el
campo inválido. Esa segunda `ValidationError` escapaba del parser.

## 6. Promoción de conocimiento anterior

Oracle convertía texto plano, valores dict/list y aliases mediante `str()`,
construía `OracleResult` como si fuera estructurado y lo guardaba en cache.

## 7. Promoción de acciones anterior

Persona convertía cualquier acción desconocida o física a `hablar` dentro del
parser. El caller recibía un dict aparentemente válido y perdía el hecho de que
la salida primaria no había validado.

## 8. Opción elegida

Se implementa Option B con estados finitos `VALID`, `DEGRADED` y
`ERROR`. Parsing describe; policy proyecta.

## 9. Contrato de resultado

`StructuredParseResult[T]` v1 contiene exclusivamente:

- `contract_version`
- `status`
- `value`
- `salvage`
- `reason_codes`
- `normalizations`
- `raw_shape`
- `raw_length`

No contiene raw response, prompt ni contexto de memoria.

## 10. Invariantes

- `VALID`: `value` obligatorio; sin salvage ni reason codes.
- `DEGRADED`: sin value; al menos un reason code; salvage tipado opcional.
- `ERROR`: sin value ni salvage; al menos un reason code.
- La versión acepta únicamente `type(x) is int and x == 1`.
- Status, enums, tuplas, longitud acotada y duplicados de códigos se validan en
  runtime mediante `dataclass(frozen=True)`.

## 11. Issue codes

El dominio v1 queda en:
`EMPTY_OUTPUT`, `MALFORMED_JSON`, `WRONG_ROOT_TYPE`,
`SCHEMA_VALIDATION_FAILED`, `UNKNOWN_ACTION`,
`INVALID_PARAMETERS`, `PARTIAL_RECOVERY`, `PLAIN_TEXT_ONLY`,
`NONCANONICAL_WRAPPER`, `MULTIPLE_OBJECTS` y `DUPLICATE_KEYS`.

## 12. Normalizaciones

`FENCE_STRIPPED` y `THINK_BLOCK_STRIPPED` son cambios sintácticos
deterministas. No representan repair, extracción desde prosa ni downgrade de
acciones.

## 13. Raw shapes

El enum finito es `EMPTY`, `OBJECT`, `ARRAY`, `STRING`,
`NUMBER`, `BOOLEAN`, `NULL` y `UNKNOWN`. `raw_length` informa
la longitud del string sin almacenar su contenido y está limitado al entero
signed de 32 bits.

## 14. Tipos de salvage

`PERSONA_SAFE_TEXT` contiene exclusivamente texto social no vacío y acotado a
700 caracteres por la policy actual. `ORACLE_CANDIDATE_TEXT` contiene texto
técnico candidato no vacío, acotado por contrato a 12.000 caracteres. Salvage
nunca ocupa `value`.

## 15. JSON canónico

Sólo un objeto JSON exacto, sin contenido externo y validado con schema estricto
puede ser `VALID`.

## 16. Fences

Un fence exacto `json` o neutro, con sólo whitespace fuera, se retira y
registra `FENCE_STRIPPED`. Si el payload es un objeto único y cumple schema,
el resultado sigue siendo `VALID`.

## 17. Prosa

Prosa alrededor de un único objeto produce `DEGRADED /
NONCANONICAL_WRAPPER`. Persona u Oracle pueden exponer un salvage textual
tipado; nunca un value autoritativo.

## 18. Trailing

Contenido no-whitespace después de un objeto produce `DEGRADED /
NONCANONICAL_WRAPPER`, salvo que demuestre un segundo objeto.

## 19. Duplicate keys

`JSONDecoder(object_pairs_hook=...)` rechaza claves repetidas en cualquier
nivel. Los tres parsers devuelven `ERROR / DUPLICATE_KEYS`; no existe
last-key-wins.

## 20. Multiple objects

Dos o más objetos separados producen `ERROR / MULTIPLE_OBJECTS`. Persona ya
no toma el primero y General ya no corta del primer al último brace.

## 21. ResponseParser

`parse_result()` es la API autoritativa. `parse()` permanece como wrapper
compatible y proyecta exactamente ese resultado una vez. Un resultado no válido
produce no-op inválido y nunca reconstruye campos crudos.

## 22. Persona

`parsear_resultado()` valida tipos estrictos y `extra=forbid` sobre acción,
parámetros, texto, pensamiento breve y razón. Tipos erróneos no se convierten
con `str()`. La única acción primaria válida es `hablar`.

## 23. Safe salvage

JSON truncado con un string `texto_a_comunicar` recuperable produce
`DEGRADED / PARTIAL_RECOVERY`, `value=None` y
`salvage=PERSONA_SAFE_TEXT`. Duplicados y múltiples objetos nunca se
rescatan.

## 24. Proyección de policy Persona

`proyectar_resultado()` consume un resultado ya calculado. Un salvage social
puede proyectarse a `hablar`; unknown/physical action pierde toda autoridad.
El dict legacy conserva `primary_parse_valid=false`, status, reasons y
normalizations internos hasta que Orchestrator toma la decisión de policy.
Fallback determinista es contenido nuevo de policy, no parse válido.

## 25. Oracle

`parse_result()` es puro respecto de cache y provider. El schema canónico
acepta `contenido_tecnico: str` y resumen opcional string, con extras
prohibidos. Texto plano y aliases son `DEGRADED`; dict/list no se convierten
en conocimiento.

## 26. Regla de cache

`proyectar_resultado()` llama a `guardar_en_cache` sólo para
`status == VALID`. Un exacto válido escribe una vez. `DEGRADED` y
`ERROR` escriben cero veces. Un fallo de cache no muta el resultado inmutable
ya calculado.

## 27. Boundary de cache legacy

Los hits existentes conservan lectura compatible, pero su calidad de parse es
desconocida y ROOT-020 no los eleva a `VALID`. No hubo scan, migración,
borrado ni cambio de identidad/provenance del cache; ese dominio sigue en
ROOT-018.

## 28. Single parse

Cada raw productivo llama una vez a la API autoritativa. Wrappers y Orchestrator
proyectan desde el mismo objeto; no hay segunda regex/parse ni segunda llamada
LLM para telemetría.

## 29. Excepciones esperadas e inesperadas

`JSONDecodeError`, duplicate-key y `ValidationError` se convierten a
estados tipados. Un `RuntimeError` inyectado dentro del decoder se propaga y
no se etiqueta falsamente como `MALFORMED_JSON`; la frontera operacional
existente sigue siendo responsable de bugs internos.

## 30. Privacidad y logging

Los logs tocados contienen sólo component, status, issue codes,
normalizations implícitas por contrato, `raw_length` y `error_type`.
No registran raw model output, prompt, memoria, candidate Oracle ni exception
message. El corpus usa canaries para verificarlo.

## 31. AuditTrace

AuditTrace v1 permanece congelado. No recibe status, reasons ni
normalizations. Sus campos/fases y serialización no cambian; Orchestrator sólo
reutiliza `fallback_used` y clasificación de error existentes.

## 32. API y Unity

`AccionSaliente`, `RespuestaAPI` y los DTO públicos no reciben campos de
parsing. Unity no se modifica y no se expone pensamiento privado.

## 33. Boundary ROOT-008

ROOT-020 no cambia número de retries ni payload de reparación. Persona mantiene
como máximo el único retry previo en los casos sin salvage. Grounding de
identity/state/memory/perception/Oracle queda para ROOT-008, que sigue
`NOT STARTED`.

## 34. Otros boundaries

No se implementó validación factual (ROOT-017), provenance/cache identity
(ROOT-018), trust/prompt injection (ROOT-019), deadlines/budget de retry
(ROOT-021) ni Triage UNKNOWN (ROOT-025). Motor, Memory/UoW, ActionLedger y
prompts de modelo permanecen fuera.

## 35. Corpus y pruebas

`ai_engine.test_structured_parse_contract` aporta 55 unidades y un corpus
local determinista de 30 formas: canonical, fence, think, prosa, trailing,
truncados, raíces escalares, tipos erróneos, extras, aliases, Unicode, texto
acotado, duplicados y múltiples objetos. Usa fakes de cache/LLM; no abre red,
Chroma activo ni modelo real.

## 36. Estado de cierre

ROOT-020 queda `CLOSED`. La suite focal completa `55/55 PASS`; Persona,
Oracle, LLM fallback, prompt y Orchestrator Audit permanecen verdes. Las suites
protegidas ROOT-015, ROOT-014, ROOT-013 y ROOT-039 completan respectivamente
`55/55`, `55/55`, `66/66` y `41/41 PASS`.

El gate aislado global descubre y ejecuta `655/655 PASS`, con cero XFAIL, cero
unexpected, cero import failures, `suite_gate=PASS`, `isolation_gate=PASS` y
`gate=PASS`. Los entry points históricos de caracterización de Bloques 2/7/8
se ejecutaron como evidencia: todavía contienen assertions stale de contratos
pre-ROOT ya reemplazados (incluido aceptar extras Persona/cachear texto Oracle)
y no se falsearon para hacerlos pasar; las unidades vigentes equivalentes están
cubiertas por la suite ROOT-020 y el gate global.

`compileall` y `git diff --check` terminan con exit 0. Los diez hashes de la
colección Chroma activa coinciden antes/después, no apareció ningún DB runtime
fuera de `lazzaro_data`, y los hashes de `core/audit_trace.py` y
`api/schemas.py` permanecen idénticos al baseline. No hubo red, modelo real,
Unity, field test ni push. ROOT-008, ROOT-018 y ROOT-021 siguen `NOT STARTED`.
