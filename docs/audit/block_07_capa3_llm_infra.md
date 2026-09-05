# Fase 2 — Bloque 7: Capa 3 + infraestructura LLM

Fecha de cierre: 2026-09-05. Rama: `codex/stabilization/pre-capa2`.
Entrada: `b5cf83eb4030a459c358733190bb237740e48564` (cierre real de Bloque 6).

**Estado: CERRADO — AUDITORÍA COMPLETA / SIN FIXES / 0 P0 NUEVOS.**

Cerrar la auditoría no aprueba los providers ni corrige sus defectos. Los dos P0
OPEN heredados de Bloque 6 permanecen fuera de alcance. Este bloque registra
**6 causas nuevas OPEN: 0 P0, 3 P1, 3 P2, 0 P3**. Cinco tickets anteriores se
citan sin duplicarlos. No se inició Capa 2 actual.

La prueba offline terminó `PASS_REPRODUCED`: red real, LLM real, Chroma activa y
ejecución física fueron cero. AuditTrace v1 y el código funcional permanecieron
intactos. Evidencia: [block_07_capa3_llm_infra.json](evidence/block_07_capa3_llm_infra.json)
y [capa3_llm_infra_probe.py](probes/capa3_llm_infra_probe.py).

## 1. Resumen ejecutivo

Capa 3 es una capa de conocimiento frío: recibe exclusivamente el mensaje de la
consulta, intenta recuperar un `OracleResult` del cache semántico y, en miss,
obtiene texto de un Oracle externo o del cliente LLM activo. `OracleEngine`
normaliza ese texto a `OracleResult`. Persona recibe después el conocimiento,
estado, percepción y recuerdos, y es quien construye la única acción pública:
`hablar`. No se encontró una ruta Oracle capaz de despachar una acción física.

El aislamiento de acción está bien delimitado, pero la semántica longitudinal no
lo está. El cache no liga una respuesta a provider, modelo, versión de prompt,
schema o TTL; además, al leer reemplaza la procedencia original por `cache`.
Persona recibe una instrucción de no inventar, pero no existe validación factual:
el probe aceptó «2» desde Oracle y «3» desde Persona. AuditTrace v1 registra los
fallos por llamada y resume el último éxito, pero no conserva todos los éxitos ni
sus roles cognitivos; por ello una llamada Oracle externa seguida por Persona se
ve finalmente como llamada de Persona, igual que un cache hit.

Gemini continúa sin deadline finito efectivo, hecho heredado. Además, reintenta
cualquier excepción —incluidas auth/rate-limit sintéticas— cuatro veces y duerme
incluso tras el cuarto fallo. Los clientes compatibles, Ollama y Oracle externo
tienen timeout escalar finito y un intento propio. La cadena fallback es
alcanzable si se configura, empieza desde el primario en cada `generate`, cambia
de provider ante excepción o vacío, y no tiene deadline ni cancelación global.

## 2. Arquitectura y ownership reales

```text
Triage route=oracle
  → Orchestrator._resolver_ruta_oracle
      → OracleEngine.evaluar_cache(pregunta, OracleMemory)
          ├─ HIT semántico → OracleResult(source=cache, cache_hit=true)
          └─ MISS/error recuperable
              → OracleEngine.preparar_consulta(pregunta)
              → OracleClient externo, si tiene key
                  └─ vacío/error → LLMGenerationAdapter(cliente activo)
                      └─ FallbackAIClient, si factory creó cadena
              → OracleEngine.keep_y_save(raw)
              → parse/normalización → OracleResult
              → OracleMemory.guardar_en_cache (best effort)
      → PersonaEngine.preparar_verbalizacion_oraculo
      → LLMGenerationAdapter(cliente activo)
      → PersonaEngine.parsear_respuesta
          ├─ inválida → un reintento social
          └─ válida/fallback → ActionResponse(action=hablar)
      → AccionSaliente pública
```

`Orchestrator` es dueño de `OracleEngine`, `OracleMemory`, `OracleClient`,
`PersonaEngine` y del adaptador sobre `reasoning.ai_client`
([orchestrator.py](../../core/orchestrator.py), constructor y líneas 1573–1795).
`client_factory` solo construye el cliente activo o su cadena; `OracleClient` es
una vía separada, no un hijo de `FallbackAIClient`.

Una transacción Oracle puede usar el cliente activo dos veces: conocimiento
técnico de fallback y verbalización. Si Persona pide reparación, puede usarlo una
tercera vez. Provider fallback es infraestructura; la capa de decisión continúa
siendo `oracle`.

## 3. Input, output y contrato Oracle

`OracleEngine.preparar_consulta` recibe el mensaje ya recortado y crea roles
`system`/`user`. El prompt pide JSON de conocimiento y prohíbe acciones. No recibe
memoria, estado biológico, tarea, herramientas, percepción ni contexto del turno.
Esos datos entran después a Persona; por tanto pueden cambiar la voz pública,
pero no la consulta técnica ni su cache key.

La salida pública de Capa 3 es siempre `OracleResult`. En capas inferiores los
providers devuelven `str`, `""` o `None`, y el parser usa un `dict` transitorio,
pero el camino de Orchestrator no expone esos tipos al usuario. Un payload legacy
con acción se reduce a texto técnico; no ejecuta la acción.

`OracleResult` exige `pregunta_original` y `contenido_tecnico`; ambos aceptan
string vacío. `resumen_para_persona` y `error` son opcionales; `fuente` limita
`api|cache|fallback|error`; `cache_hit` default false. Pydantic ignora extras.
Respuesta vacía, objeto JSON truncado o JSON válido no-object generan un
`OracleResult` frío de rescate `fuente=error` sin escritura. Texto plano se
acepta como conocimiento `api` y se cachea. Aliases legacy y fences se toleran;
un valor estructurado en `contenido_tecnico` se convierte con `str(...)`.

La tolerancia evita excepciones públicas, pero también puede reclasificar una
salida estructuralmente incorrecta como conocimiento válido: `LLM-CONTRACT-001`.

## 4. Oracle cache / OracleMemory

La colección real es `oracle_cache`. Se envía la pregunta cruda a Chroma con
`n_resultados=1`; hit significa distancia cosine `<=0.35`. No existe key exacta,
hash, lower/case folding, normalización de whitespace/puntuación, TTL,
invalidación, versioning ni namespace por modelo/config. Case, espacio o
puntuación cambian la consulta vectorial, pero el resultado final depende de la
distancia semántica: no son ni igualdad exacta ni miss garantizado.

Persistencia por entrada: UUID nuevo, documento=pregunta y metadata
`contenido_tecnico`, `resumen_para_persona`, `fuente`, `timestamp` y alias legacy
`respuesta`. No almacena provider, model id, schema/prompt version ni configuración.
En hit se ignora `metadata.fuente` y se reconstruye `fuente=cache`; se pierde la
procedencia original.

Contexto, memoria, estado y tarea no entran al Oracle actual y tampoco a la
consulta del cache. Modelo, provider y configuración sí pueden cambiar la
respuesta técnica de un miss, pero no invalidan una entrada previa. El probe
modificó cada dimensión de forma aislada y el mismo texto siguió siendo hit.

Metadata ausente/malformada, contenido vacío, distancia inválida o demasiado
alta se normalizan a miss. La llamada `client.buscar` queda fuera del `try` de
interpretación: su excepción escapa de `OracleMemory`, pero Orchestrator la
captura, registra error genérico y continúa al provider. Una excepción de
escritura es atrapada por `OracleEngine`; el `OracleResult` exitoso se devuelve.
Un fallo de provider no inicia escritura parcial.

No hay singleflight ni dedupe. Dos misses simultáneos idénticos produjeron dos
búsquedas, dos llamadas fake y dos UUID/escrituras. Dos consultas distintas
permanecieron separadas en el control concurrente.

## 5. Frontera Oracle → Persona

Persona recibe pregunta, contenido técnico, resumen, `fuente`, `cache_hit`, error,
estado/identidad, percepción, recuerdos y restricción de canal. El prompt ordena
no inventar y limita la acción a `hablar`. El parser fuerza cualquier acción
distinta a `hablar`; por eso el probe transformó `navegar_a_objeto` en `hablar`.
No hay ejecución física desde este borde.

La fidelidad factual solo es una instrucción. No se compara la respuesta pública
contra `contenido_tecnico` o `resumen_para_persona`: un OracleResult «la respuesta
es 2» fue verbalizado como «la respuesta es 3» y aceptado sin reintento. Persona
puede igualmente afirmar que se consultó un modelo aunque `cache_hit=true`.
`pensamiento_privado` no se copia al texto público ni a AuditTrace; en una
reparación, la respuesta cruda fallida sí vuelve a un prompt interno de Persona.

Oracle no tiene voz final: aporta contenido y resumen, Persona puede resumir,
reescribir u omitir source/error/cache. En el flujo normal y en los fallbacks
Oracle se pasa por Persona. Si Persona lanza, Orchestrator produce todavía un
`ActionResponse(hablar)` determinista. No se encontró Oracle→usuario que salte
Persona.

## 6. AIClient, factory y selección

La interfaz común mínima es `generate(prompt, **kwargs) -> str`,
`health_check() -> bool` y `close() -> None`; los dos primeros son abstractos y
`close` tiene no-op base. `LLMAuditBuffer` agrega captura, consumo y registro.
`OracleClient` no implementa `BaseAIClient`: solo comparte el buffer y expone
`configurado`/`consultar_oraculo`; crea/cierra un `httpx.Client` por llamada y no
tiene health/close.

`build_ai_client` construye proveedores en orden, omite los que lanzan
`ValueError` por configuración, retorna el único válido o envuelve varios en
`FallbackAIClient`. Si ninguno es válido, lanza `ValueError`. Con fallback
deshabilitado se detiene tras el primer cliente construible. Un modelo inválido
no se valida localmente; el backend lo rechazará en generate/health.

Settings default es Gemini y fallback=true; el profile ya auditado de ejecución
es Gemini con fallback global deshabilitado. Se conserva ese hecho de Bloque 1
sin abrir `.env`. Orden explícito viene de `AI_FALLBACK_ORDER` y puede omitir al
primario; sin orden explícito la factory combina primario con el hard-code
`gemini,groq,xai,kimi,deepseek_api`, deduplica y excluye desconocidos. Ollama solo
entra si es primario o se enumera explícitamente.

`Settings` normaliza key vacía a `None`. Sin Settings, solo `OracleClient` lee
directamente `ORACLE_API_URL`, `ORACLE_API_KEY|GROQ_API_KEY`,
`ORACLE_MODEL|GROQ_MODEL` y `ORACLE_TIMEOUT` de `os.environ`. Los demás clientes
usan el objeto Settings. Construcción parcial sin rollback se mantiene como
`BOOT-AUDIT-004`; no se duplicó.

## 7. Provider matrix

| provider | constructor/model/auth | timeout | intentos/retry | health | vacío / close | audit / fallback |
|---|---|---:|---|---|---|---|
| Gemini | `GeminiClient`; `GEMINI_MODEL_NAME`; `GEMINI_API_KEY` | **ninguno finito visible** | 4; todo error y empty; backoff solo excepción | generate «ok», substring | `""`; close aislado | `gemini`; elegible |
| Groq | compatible; `GROQ_MODEL/API_KEY` | 45 s escalar httpx | 1 | generate, substring | `""`; close aislado | `groq`; elegible |
| xAI | compatible; `XAI_MODEL/API_KEY` | 45 s | 1 | generate, substring | `""`; close aislado | `xai`; elegible |
| Kimi | compatible; `KIMI_MODEL/API_KEY` | 45 s | 1 | generate, substring | `""`; close aislado | `kimi`; elegible |
| DeepSeek API | compatible; `DEEPSEEK_API_MODEL/API_KEY` | 45 s | 1 | generate, substring | `""`; close aislado | `deepseek_api`; elegible |
| Ollama | `OllamaClient`; `OLLAMA_MODEL_NAME` o `DEEPSEEK_MODEL`; local | 120 s | 1 | `/api/tags`, modelo exacto | `""`; close aislado | `ollama`; elegible si ordenado |
| Oracle externo | `OracleClient`; Oracle→Groq fallbacks de config | 15 s | 1 | ninguno | `None`; client por llamada | `oracle_external`; antes del cliente activo |

Defaults observados: Gemini `gemini-3.1-flash-lite`; Groq y Oracle heredado
`llama-3.1-8b-instant`; xAI `grok-3-mini`; Kimi `moonshot-v1-8k`; DeepSeek API
`deepseek-chat`; Ollama `deepseek-r1:8b`. Las fuentes duplicadas/divergentes se
mantienen bajo `CONFIG-AUDIT-005`.

El timeout escalar de httpx limita fases connect/read/write/pool, no constituye
un deadline duro de toda la transacción ni de la cadena. Gemini no pasa timeout
al SDK. En consecuencia Gemini y una cadena que lo incluya pueden quedar sin
límite total según el código actual (`CONFIG-AUDIT-001`).

## 8. Retries, fallback y health

Gemini hace hasta cuatro intentos. Empty se repite inmediatamente; cualquier
excepción se repite con base 1/2/4/8 segundos más jitter. El cuarto error también
duerme antes de retornar vacío. Timeout, auth, rate-limit, conexión y error no se
clasifican para decidir retry. JSON Oracle inválido ya ocurre fuera del provider
y no reintenta conocimiento; JSON Persona inválido permite un único reintento
social. No existe señal de cancelación ni presupuesto global.

Los retries de texto no ejecutan herramientas, pero duplican llamadas, cuota,
latencia y eventos Audit; el cache se escribe una sola vez tras un resultado
aceptado. Los misses concurrentes son la vía reproducida que sí duplica llamada y
escritura.

`FallbackAIClient` es runtime-reachable cuando la factory construye dos o más
clientes. En cada `generate` empieza en A; no es sticky y no hace health previo.
Excepción, timeout normalizado a vacío o empty avanzan a B. El orden es el de la
factory. Los controles A ok, A timeout, A error, A empty, A→B ok,
A timeout→B ok y A→B fail reprodujeron conteos/orden esperados.

`fallback_used=false` significa primer provider de la cadena, incluso durante
retries internos; `true` significa índice posterior. Orchestrator también fuerza
el resumen a true para fallback/error Oracle o fallo total. Así, recovery dentro
de Gemini no es sustitución; A→B sí. La secuencia completa de fallos es visible
en stages `error` y el último éxito en `decision.llm`, pero los éxitos anteriores
y su rol se pierden: una cadena arbitraria A fail→B fail→C success puede
reconstruir fallos y C, no todas las operaciones cognitivas exitosas.

Gemini y compatibles consideran healthy cualquier texto que contenga `ok`; el
control `not ok` dio true y la prueba consume una generación/cuota. Ollama exige
que el modelo figure en tags. Fallback health prueba hijos hasta el primero true,
sin cambiar selección sticky. Oracle externo no tiene health, así que key no
vacía puede parecer configurada y fallar recién en la primera consulta. Main
permite startup con health false y luego registra nominal: `BOOT-AUDIT-006`.

## 9. Error contract

| frontera | comportamiento actual |
|---|---|
| compatible/Ollama | timeout, HTTP, parse y excepción → `""`; no escapan |
| Oracle externo | los mismos casos → `None`; no escapan normalmente |
| Gemini | todo error/empty hasta cuatro intentos → `""` |
| FallbackAI | excepción de hijo → empty y siguiente provider |
| Oracle parser | vacío/malformed/no-object → OracleResult `fuente=error` |
| cache malformed | miss |
| `client.buscar` cache | escapa de OracleMemory; Orchestrator lo captura |
| cache write | atrapado por OracleEngine; respuesta funcional sobrevive |
| Persona parse | marca retry; tras fallo total produce hablar de rescate |
| child close en fallback | se registra y continúa cerrando otros hijos |
| OracleMemory close | no tiene aislamiento local en `Orchestrator.close` |
| Audit writer | fallo aislado; misma acción/texto que control |

`stable_error_type` usa el lineage del tipo: cualquier clase con timeout en la
jerarquía queda `TimeoutError`; no inspecciona el mensaje. LLMCallAudit conserva
el tipo estable concreto para otros errores (`ConnectError`, `HTTPStatusError`,
`ValueError`, etc.). Provider error y parsing error son distinguibles en la
captura interna, pero el resumen público de trazas es incompleto; el fallo de
búsqueda de cache terminó como `DecisionError/component=decision`, no como error
de cache.

AuditTrace nunca guardó los tokens sintéticos de prompt, respuesta privada o
mensaje de excepción. Los loggers humanos sí imprimen mensajes/body HTTP en
varios clientes; es el `CONFIG-AUDIT-006` heredado. El usuario termina recibiendo
Persona o un texto de rescate `hablar`, no una excepción HTTP. La caída del
writer no alteró el resultado funcional.

## 10. LLMCallAudit / AuditTrace v1

`LLMCallAudit` contiene solo provider, model id, fallback, status, error type,
duration y timestamp monotónico. `LLMAuditBuffer` se activa/consume por llamada y
usa `threading.local`; dos hilos sobre la misma cadena mantuvieron eventos y
`last_provider_used` separados. Duraciones fueron no negativas. No contiene
prompt, respuesta, key ni mensaje libre.

Una llamada de compatible/Ollama/Oracle produjo un evento; un intento Gemini
produjo uno y cuatro retries produjeron cuatro. Fallback consume eventos del hijo
y no los duplica; si el hijo no instrumenta, sintetiza uno. Fallos generan stages
`error`. `decision.llm` elige el último evento exitoso cronológico y resume si
hubo fallback.

Casos completos:

| caso | resultado observable |
|---|---|
| cache hit | 0 Oracle externo, 1 Persona, hablar; `decision.llm=persona` |
| Oracle externo ok | 1 externo + 1 Persona; solo Persona queda en decision |
| cache exception | error genérico, provider continúa, hablar |
| externo timeout → activo ok | error Timeout externo; activo técnico + Persona; fallback=true |
| A fail → B ok | fallos A visibles; decision B/fallback=true |
| todos empty | fallos por intento; Persona de rescate; provider null |
| writer falla | mismas etapas intentadas y misma respuesta que control |

Todos los stages de cada turno compartieron trace id. No se añadieron campos ni
stages. V1 cumple su semántica congelada, pero no puede contestar si el
conocimiento vino de cache o de qué Oracle/modelo cuando Persona también llama a
LLM. Esto es `LLM-AUDIT-001`, no autorización para modificar v1.

## 11. Determinismo, idempotencia y concurrencia

Con provider fake determinista y misma pregunta ×10 hubo una llamada y una
escritura. La primera serialización de OracleResult fue `api/cache_hit=false` y
las nueve posteriores `cache/cache_hit=true`: dos serializaciones y dos prompts
Persona, aunque la acción pública determinista fue una. UUID/timestamp de la
persistencia tampoco son deterministas.

Cambiar solo contexto, memoria, modelo, provider o configuración no cambió la
consulta del cache y conservó hit. Cambiar whitespace sí cambió la consulta
cruda y fue miss en el fake exacto; en Chroma real la similitud podría aún dar
hit. El cache semántico hace que igualdad dependa del embedding/distancia, no de
una identidad reproducible versionada.

Dos misses idénticos simultáneos duplicaron trabajo. Dos consultas distintas no
mezclaron contenido. `FallbackAIClient.last_provider_used` y el buffer Audit son
thread-local. Los `httpx.Client` persistentes no tienen coordinación repo-level
entre `generate` y `close`; no se probó close concurrente porque el contrato no
lo promete. No hubo stress ni acceso a Chroma real.

## 12. Privacidad

Fixtures, keys, URLs, respuestas y errores fueron sintéticos. No se abrió `.env`,
no se persistieron prompts reales ni narrativas de memoria, y no se usaron
headers reales. El probe verifica ausencia de los tokens sintéticos privados en
AuditTrace. La superficie pendiente de logs humanos se referencia como
`CONFIG-AUDIT-006`; no se cambió logger ni sanitización.

## 13. Tickets nuevos OPEN

### ORACLE-AUDIT-001 — P1 — Persona no preserva hechos de Oracle

- **Procedencia/clase:** Bloque 7; integridad semántica Capa3→Persona.
- **Evidencia:** Oracle «2», Persona «3»; parse válido, acción física solicitada
  reconducida a hablar.
- **Ubicación:** `ai_engine/persona_engine.py:116–282`,
  `core/orchestrator.py:1726–1795`.
- **Reproducción:** grupo `oracle_persona` del probe.
- **Impacto:** la voz pública y un futuro DevelopmentalCore pueden aprender una
  afirmación distinta del conocimiento técnico validado.
- **Recomendación preliminar:** definir invariantes/estructura de hechos y una
  validación de fidelidad o degradación explícita antes de publicar.
- **Estado:** OPEN; sin fix.

### ORACLE-CACHE-001 — P1 — cache sin identidad/procedencia/versionado

- **Procedencia/clase:** Bloque 7; corrección semántica longitudinal.
- **Evidencia:** provider/model/config no participan; no TTL/version; hit borra
  fuente original; sensibilidad fake mantiene el hit.
- **Ubicación:** `ai_engine/oracle_memory.py:18–139`.
- **Reproducción:** grupos `cache` y `determinism`.
- **Impacto:** una respuesta de modelo/prompt/config anterior puede reaparecer
  como conocimiento actual sin poder atribuirla ni invalidarla.
- **Recomendación preliminar:** namespace/version de key y metadata con provider,
  modelo, schema/prompt/config digest, origen, TTL/política de invalidación.
- **Estado:** OPEN; sin fix.

### LLM-AUDIT-001 — P1 — provenance LLM/Oracle se comprime por debajo del rol cognitivo

- **Procedencia/clase:** Bloque 7; observabilidad para aprendizaje.
- **Evidencia:** cache hit y Oracle externo success terminan ambos con
  `decision.llm=persona`; el éxito Oracle previo no queda; cache exception se
  etiqueta `DecisionError/decision`.
- **Ubicación:** `core/orchestrator.py:435–565`, `1573–1795`.
- **Reproducción:** grupo `audittrace`.
- **Impacto:** no se puede atribuir conocimiento, verbalización y cache a sus
  fuentes; fallback/error puede ser aprendido con causalidad equivocada.
- **Recomendación preliminar:** diseñar fuera de v1 spans/roles y source/cache
  explícitos para la observabilidad siguiente; conservar todos los éxitos.
- **Estado:** OPEN; AuditTrace v1 no se tocó.

### ORACLE-CACHE-002 — P2 — misses concurrentes duplican llamada/escritura

- **Procedencia/clase:** Bloque 7; concurrencia/coste.
- **Evidencia:** barrera determinista: 2 búsquedas, 2 provider calls, 2 writes.
- **Ubicación:** `ai_engine/oracle_engine.py:40–217`,
  `ai_engine/oracle_memory.py:104–139`.
- **Reproducción:** grupo `cache_concurrency`.
- **Impacto:** cuota/latencia duplicadas y entradas semánticas duplicadas.
- **Recomendación preliminar:** singleflight por key versionada y dedupe atómico.
- **Estado:** OPEN; sin fix.

### LLM-PROVIDER-001 — P2 — política Gemini reintenta indiscriminadamente

- **Procedencia/clase:** Bloque 7; resiliencia/latencia.
- **Evidencia:** timeout, conexión, auth-like y rate-limit-like hicieron cuatro
  intentos; backoff 1/2/4/8; hay sleep después del último error.
- **Ubicación:** `ai_engine/gemini_client.py:49–121`.
- **Reproducción:** grupo `providers`.
- **Impacto:** espera/cuota innecesaria, amplificada por fallback y sin cancelación.
- **Recomendación preliminar:** clasificar retryable, respetar Retry-After,
  eliminar sleep final y aplicar presupuesto/cancelación global.
- **Estado:** OPEN; no duplica el timeout ausente de `CONFIG-AUDIT-001`.

### LLM-CONTRACT-001 — P2 — parser Oracle acepta estructura degradada como conocimiento

- **Procedencia/clase:** Bloque 7; contrato/parsing.
- **Evidencia:** texto plano se cachea; dict anidado se stringifica; extras se
  ignoran; campos obligatorios admiten vacío.
- **Ubicación:** `ai_engine/oracle_engine.py:90–228`,
  `ai_engine/schemas.py:93–101`.
- **Reproducción:** grupo `oracle_contract`.
- **Impacto:** salida fuera del esquema puede convertirse silenciosamente en
  conocimiento válido y persistente.
- **Recomendación preliminar:** separar compatibilidad legacy de validación
  estricta, versionar el contrato y no cachear degradación ambigua.
- **Estado:** OPEN; sin fix.

## 14. Candidatos heredados y documentación

| ID | estado en Bloque 7 |
|---|---|
| `CONFIG-AUDIT-001` | confirmado: Gemini no muestra deadline finito; no duplicado |
| `BOOT-AUDIT-006` | confirmado: health puede fallar, startup continúa y luego dice nominal |
| `CONFIG-AUDIT-005` | confirmado: defaults/model/config Oracle duplicados; no duplicado |
| `CONFIG-AUDIT-006` | confirmado: logs humanos pueden incluir error/body; AuditTrace no |
| `BOOT-AUDIT-004` | confirmado por lectura: factory no revierte clientes parciales; no duplicado |

`DOC-AUDIT-001` no se abre. `docs/registro_pruebas_unity.md:124` conserva como
historia que el fallback requería Ollama, pero el mismo registro documenta en
línea 359 la corrección posterior. Código actual no pasa `required_provider` en
Oracle. No es una afirmación vigente contradictoria, sino historial cronológico.

## 15. Respuestas 1–100

1. Oracle recibe el `mensaje` recortado del evento como pregunta.
2. Recibe pregunta; no memoria, estado, herramientas, percepción ni tarea.
3. Solo pregunta, prompt fijo y salida/provider/cache influyen en Capa 3; el resto influye después en Persona.
4. No produce acciones físicas ejecutables.
5. No selecciona herramientas; el prompt las prohíbe.
6. El límite de Capa 3 entrega siempre `OracleResult`.
7. Providers internos devuelven string/None y el parser usa dict; Orchestrator no los expone directamente.
8. Pydantic valida OracleResult y OracleEngine valida/normaliza el raw.
9. Obligatorios: pregunta y contenido; opcionales: resumen, fuente default, cache flag y error.
10. Vacío produce OracleResult de error sin cache.
11. JSON objeto truncado produce fallback de error.
12. Campos desconocidos son ignorados.
13. Texto no estructurado se acepta como contenido técnico y se cachea.
14. Sí: extracción de fences/objeto, aliases legacy y fallback frío.
15. Sí: puede stringificar estructura o promover texto plano a conocimiento.
16. No; Oracle no tiene voz pública propia.
17. Sí en el flujo runtime normal, incluidos fallbacks Oracle.
18. No se encontró ninguna.
19. Crea OracleResult de error y usa Persona; si Persona falla, hablar determinista.
20. No cambia `decision.layer`; puede cambiar infraestructura al cliente activo.
21. Conserva source en miss; un hit reemplaza la fuente original por `cache`.
22. En OracleResult sí; en AuditTrace v1 no explícitamente.
23. Sí; Persona ve cache=true pero la instrucción no se valida.
24. Sí, porque no hay namespace/version/provider/model/TTL.
25. Ninguna metadata de modelo; solo source genérica y timestamp.
26. Distancia semántica cosine del top-1 <=0.35.
27. Cambian la consulta cruda; el hit depende del embedding, no de igualdad exacta.
28. No.
29. No.
30. Sí; el mismo texto consulta la misma dimensión técnica aunque Persona tenga contexto distinto.
31. No.
32. No.
33. No.
34. Se trata como miss salvo excepción de búsqueda.
35. La excepción de búsqueda escapa del cache, pero Orchestrator la captura y cae al provider.
36. Sí.
37. No por el flujo observado; solo se escribe tras parse aceptado.
38. Sí; dos misses duplicaron llamada y escritura.
39. BaseAIClient + buffer Audit.
40. `generate`, `health_check` y `close`; los dos primeros obligatorios.
41. `client_factory.build_ai_client`; Oracle externo lo crea Orchestrator aparte.
42. Profile heredado: Gemini con fallback global deshabilitado.
43. Los modelos están enumerados en la matriz de sección 7.
44. Sí; heredado `CONFIG-AUDIT-005`.
45. Solo OracleClient sin Settings lee directamente variables ORACLE/GROQ; Settings carga env para el resto.
46. Factory omite ese provider; si no queda otro, ValueError.
47. Settings la normaliza a None y ocurre lo mismo.
48. Settings rechaza; la lista explícita factory filtra desconocidos y falla si no queda válido.
49. No hay validación local; fallo diferido al backend/health.
50. Degrada saltando providers no configurados; falla si ninguno es construible.
51. Sí, riesgo heredado `BOOT-AUDIT-004`.
52. No: Gemini carece de timeout finito visible.
53. Settings/httpx para compatible, Ollama y Oracle; no en Gemini.
54. Son escalares httpx por fases de I/O, no deadline global; Gemini usa SDK sin deadline explícito.
55. Sí, `CONFIG-AUDIT-001`.
56. Gemini y cualquier pipeline/cadena que dependa de esa llamada.
57. Gemini 4; compatible/Ollama/Oracle externo 1; Persona puede agregar un reintento de parsing.
58. Gemini reintenta todo error; los demás delegan a fallback tras devolver vacío.
59. Sí en Gemini y sí cambia de provider si existe cadena.
60. Sí en Gemini; compatible no internamente, pero puede avanzar fallback.
61. Igual que auth.
62. Gemini sí sin backoff; los demás avanzan fallback si existe.
63. JSON técnico inválido no reintenta provider; Persona inválida sí una vez.
64. Gemini solo en excepciones: exponencial.
65. Sí, Gemini.
66. No hay contrato de cancelación.
67. Duplica llamadas/cuota/audit; cache write queda posterior al éxito.
68. Sí, si la factory construye más de un cliente.
69. Config de fallback y al menos dos providers construibles; Oracle externo tiene fallback aparte.
70. No selecciona sticky; startup health solo informa.
71. Sí.
72. Sí.
73. Sí, porque el cliente lo normaliza a vacío o wrapper captura excepción.
74. Orden explícito o primario + defaults hard-coded deduplicados.
75. No; también existe orden default en factory.
76. Sí: cada llamada vuelve a A.
77. No.
78. No.
79. Sí para Gemini/compatibles; usan generate.
80. Sí, y además Oracle técnico y Persona pueden usar modelos diferentes.
81. Índice posterior en cadena, más resumen forzado por fallback/error Oracle.
82. No.
83. Los eventos internos sí; el resumen v1 no conserva toda la secuencia exitosa.
84. Fallos A/B y último éxito C sí; roles/éxitos anteriores no completamente.
85. Gemini/compatibles: texto con substring ok; Ollama: modelo listado; fallback: algún hijo true.
86. Sí.
87. Sí; reproducido.
88. Sí para health basado en generate.
89. Según provider: Gemini no finito; compatible/Ollama sí escalares.
90. No, heredado.
91. No.
92. Sí; `configurado` solo comprueba key no vacía.
93. Sí, heredado `BOOT-AUDIT-006`.
94. Escapan búsqueda cache directa y close OracleMemory; el flujo principal atrapa la primera. Providers normalizan.
95. Clases cuyo lineage contiene timeout.
96. `TimeoutError` estable o el nombre concreto del tipo.
97. No en LLMCallAudit/AuditTrace; logs humanos siguen ticket heredado.
98. No; writer failure conservó decisión funcional.
99. En eventos internos sí; el cache error quedó genérico y el resumen no preserva todos los roles.
100. Persona o hablar de rescate; no HTTP exception ni `None` en el flujo normal.

## 16. Límites impuestos antes de Capa 2 actual

- No permitir que DevelopmentalCore trate `decision.llm` como procedencia completa
  del conocimiento; falta role/source/cache y éxitos previos.
- No aprender una afirmación pública como si fuera el OracleResult sin comprobar
  fidelidad (`ORACLE-AUDIT-001`).
- No considerar un hit reusable entre modelos/config/versiones sin resolver
  `ORACLE-CACHE-001`.
- No interpretar fallback como cambio de capa: sigue siendo infraestructura del
  turno Oracle.
- No usar health como certificación semántica ni de deadline.
- Mantener los dos P0 Motor heredados como gate global; este bloque no los tocó.

## 17. Pruebas, regresión y gate Git

Comandos del cierre (resultados finales se fijan tras ejecutar el gate):

| check | resultado |
|---|---|
| `python -B docs/audit/probes/capa3_llm_infra_probe.py` | PASS_REPRODUCED, exit 0 |
| `python docs/audit/probes/regression_probe.py` | 160 PASS + 1 KNOWN_XFAIL, 0 inesperados; 161 unidades/25 módulos; gates PASS |
| `python -m compileall -q main.py ai_engine api config core memory terminal_client tools docs/audit/probes` | PASS |
| `python -m json.tool docs/audit/evidence/block_07_capa3_llm_infra.json` | PASS |
| `git diff --check` | PASS antes de staging; el diff staged se vuelve a verificar al cerrar |

El probe protegió 18 archivos funcionales más los tres archivos de Chroma activa
por SHA-256 antes/después. Bloqueó tres intentos de inspección `platform` vía
subprocess y un probe IPv6 `socket.bind` de la librería estándar; ninguno llegó a
ejecutarse. No hubo operación inesperada bloqueada ni intento de escritura del
probe. Los hashes completos están en la evidencia JSON.

Hashes Chroma del probe final:

- `data_level0.bin`: `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659`
- `length.bin`: `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A`
- `chroma.sqlite3`: `C003D87617B5156E358E4DD56DAB9B8765401AF239F809750627E42A77D23F6D`

Los tres aparecían modificados antes de Bloque 7 y deben permanecer fuera del
commit. SQLite tenía hash
`884E69923890C2470819E32939F60387C68FCF6C80300688A77364A7C8454363` en una
ejecución previa y cambió entre invocaciones, junto con la aparición externa de
`constelacion.html`; dentro de cada probe protegido el hash inicial/final fue
idéntico. No se atribuye ese cambio al probe y ambos artefactos quedan fuera del
commit. El hash del commit final se entrega externamente: un commit no puede
contener su propio hash.
