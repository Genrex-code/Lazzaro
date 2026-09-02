# Fase 2 · Bloque 2 — Auditoría de contratos

Fecha: 2026-09-02. Código examinado desde `c512152`, rama
`codex/stabilization/pre-capa2`. Estado: auditoría contractual completada;
hallazgos OPEN, sin fixes funcionales. No se modificaron schemas, tipos, enums,
validadores, payloads, MemoryManager, Unity, dependencias ni AuditTrace v1.

## Resumen ejecutivo

La pregunta del bloque —qué entra/sale y qué significan `None`, `False`,
`"none"` y vacío— no tiene una respuesta única en LAZZARO. Esos valores son
sentinelas distintos y solo se vuelven equivalentes en adaptadores concretos.
Los modelos Pydantic tipados convierten correctamente strings booleanos comunes,
pero los payloads `Dict[str, Any]`, las dataclasses sin validación runtime y la
metadata persistida conservan tipos arbitrarios. Ahí se confirmaron los cambios
de semántica de `bool("false")`.

No se demostró ningún P0 ni un incidente real de secreto, corrupción o ejecución
física. Se registran **30 tickets OPEN: 10 P1, 18 P2 y 2 P3**. Los P1 que más
condicionan un futuro DevelopmentalCore son:

- `ActionResponse → AccionSaliente → RespuestaAPI` elimina `valido`, `error`,
  `metadata` y el resultado Motor estructurado; un `none` pasivo válido y un
  bloqueo inseguro inválido terminan ambos bajo `status="ok"`.
- `ToolResult.exito="false"` y `resultado_accion.exito="false"` pueden contarse
  como éxito por truthiness, aunque otras ramas exigen identidad booleana.
- `requiere_confirmacion` significa “detener y esperar” para Orchestrator, pero
  “confirmación concedida” para `ActionExecutor`.
- coordenadas Motor no finitas se aceptan como acción válida y al serializar se
  convierten en JSON `null`.
- memoria episódica pierde siempre `estado_emocional` al persistir; no-hit y fallo
  de recuperación se vuelven indistinguibles; flags persistidos como string
  `"false"` pueden aceptarse como verdaderos.
- un campo llamado `pensamiento_privado` cruza el REST público y Unity lo muestra
  y registra; además el detalle textual de excepciones se publica en HTTP 500.

El hallazgo histórico `MEM-AUDIT-001` queda resuelto como **C: existen contratos
contradictorios**. En ChromaDB 1.5.9, el backend real acepta metadata `None`,
rechaza `{}` y acepta un diccionario no vacío; sin embargo, los tipos de LAZZARO
declaran `dict` sin prohibir vacío y la prueba histórica espera que `{}` funcione.
Para el backend instalado prevalece B (la prueba está desactualizada), pero el
contrato total sigue siendo C. El flujo normal del encoder siempre crea metadata
no vacía y no está bloqueado por este caso.

Los cuatro probes nuevos pasan y no usan red, proveedor remoto ni la Chroma de
uso diario. La regresión conserva **160 PASS, 1 fallo histórico conocido y 0
fallos nuevos**. Los tres archivos runtime preexistentes permanecen fuera del
commit.

## Alcance, método y evidencia

Se empezó por `api/schemas.py` y `ai_engine/schemas.py`; después se siguieron
productores y consumidores solo para verificar el contrato. Orchestrator se leyó
como adaptador, no como arquitectura completa. No se auditó calidad del Random
Forest, transporte/colas de Unity, internals completos de Capa 1/Memory ni Bloque
3. Capa 2/DevelopmentalCore no fue iniciada.

Etiquetas:

- **IMPLEMENTADO**: conducta directamente codificada.
- **OBSERVADO**: conducta reproducida con probe determinista.
- **INFERIDO**: consecuencia sustentada por código, sin incidente end-to-end.
- **HIPÓTESIS**: pendiente de un bloque posterior; no se presenta como bug.

`PASS` en un probe significa “se reprodujo el contrato actual”, no “el contrato es
deseable”. Los probes deshabilitan logs humanos antes de importar producción,
bloquean sockets y construcciones de la Chroma activa, usan fakes/directorios
temporales y no inician FastAPI, Unity, AuditTrace ni LLM. La única Chroma real se
creó en un directorio temporal, con embedding fake y sin consulta semántica, para
reproducir `None / {} / metadata válida` en la versión instalada.

## 1. Mapa general de contratos

```text
Unity / terminal / sandbox
  │ JSON
  ▼
EventoEntrante (Pydantic; detalles = Dict[str, Any])
  ├─ BehaviorCore recibe tipo_evento + detalles
  ├─ Triage recibe model_dump completo
  └─ Orchestrator adapta contexto, memoria y ruta
         │
         ├─ Route.NOOP/INTERNAL → corte operativo
         ├─ Motor → MotorAction / MotorResolution
         ├─ Persona → dict social informal
         └─ Reasoning → LLMResponse → ActionResponse
                                      │
                                      ▼
                     _sanitizar_action_response
                                      │
                                      ▼
            ActionResponse --adaptador con pérdida--> AccionSaliente
                                      │
                   ┌──────────────────┴──────────────────┐
                   ▼                                     ▼
        sandbox: ActionExecutor                   REST: RespuestaAPI
                   │                                     │
              ToolResult                         Unity / terminal
                   │                                     │
          status + detalles                   resultado_accion
                   └──────────────────┬──────────────────┘
                                      ▼
                          memoria / tarea / emoción
```

```text
evento + estado
  → MemoryEncoder
  → EpisodicMemory / SemanticMemory
  → MemoryDocument (texto + metadata escalar)
  → ChromaClient
  → stores reconstruyen MemoryMetadata + recuerdo
  → MemoryResult(distancia cruda, metadata_score de reranking)
  → MemoryRetriever ordena por metadata_score
  → MemoryContextResult(texto, IDs, scores)
```

```text
label externo
  → normalize_intent
  → IntentType
  → route_for_intent
  → Triage dict {intent, route, confidence, reason, ...}
  → adaptación de canal produce effective_route

percepción legacy/dict
  → PerceptionSnapshot / PerceivedObject
  → FuzzyObjectResolver
  → MotorResolution (5 estados)
  → MotorEngine
  → ActionResponse
  → AccionSaliente (sin estado Motor estructurado)
```

## 2. Productor → contrato → consumidor

| Productor | Contrato efectivo | Consumidor | Adaptación/pérdida principal |
|---|---|---|---|
| Unity/terminal | JSON `EventoEntrante` | FastAPI/Pydantic | Unity omite timestamp; extras top-level se ignoran; `detalles` conserva tipos arbitrarios |
| FastAPI | `EventoEntrante` | Orchestrator/Triage/BehaviorCore | Triage ve dump completo; BehaviorCore solo tipo + detalles; `/comando` muta tipo |
| Behavior/Psychology | dict `contexto_llm` | `ContextBuilder` | Renombra claves, proyecta 7 campos de tarea y descarta el resto |
| LLM general | JSON | `LLMResponse`/`ResponseParser` | Extra prohibido; varios sentinelas se normalizan a acción `none` |
| PersonaEngine | dict social | Orchestrator | Fuerza `hablar`; descarta error/retry/partial flags; marca la respuesta ejecutable como válida |
| TriageRouter | dict informal | Orchestrator | `route` es autoridad; no se recalcula desde `intent`; terminal puede cambiar solo ruta efectiva |
| MotorEngine | `MotorAction` dict | Orchestrator | Se convierte a `ActionResponse`; un `none` Motor queda inválido con error/metadata |
| Resolver Motor | `MotorResolution` | Motor/Orchestrator | Cinco estados preservados internamente en metadata, no en DTO público |
| Reasoning/Motor/Persona | `ActionResponse` | Orchestrator | Sanitización conserva estado interno; conversión pública pierde `valido/error/metadata` |
| Orchestrator | `AccionSaliente` | API/Unity/Executor | Genera ID nuevo y confirmación derivada; pensamiento pasa al campo público “privado” |
| API | `RespuestaAPI` | Unity/terminal | `None` y objeto `nombre=none` son dos formas; HTTP 500 evita el DTO tipado de error en Unity |
| Herramienta | `str` o `ToolResult` de hecho | `ActionExecutor` | Todo retorno que no sea `ToolResult`, incluso falsey, se promueve a éxito |
| `ActionExecutor` | `ToolResult` | Orchestrator | `exito` se evalúa por truthiness y se reduce a status `ok/error` |
| Unity | dict `resultado_accion` | Evento/Behavior/Memory | `exito` real es bool; API también acepta strings/números y consumidores discrepan |
| MemoryEncoder | dataclasses + `MemoryDocument` | stores/Chroma | Estado emocional no se serializa; extra no escalar se descarta y puede colisionar |
| ChromaClient | `QueryResult | None` | stores | Aquí aún distingue fallo (`None`) de no-hit (resultado vacío) |
| Stores/Retriever | `list[MemoryResult]` | MemoryManager | Reducen fallo y no-hit a `[]`; el retriever permite éxito parcial silencioso |
| MemoryManager | `MemoryContextResult` | Orchestrator | IDs/scores alineados de una sola búsqueda; score es `metadata_score`, no distancia |

## 3. Inventario y fichas de contrato

### 3.1 `EventoEntrante`

- **Responsabilidad/productor/consumidor:** evento público de Unity, terminal o
  sandbox; FastAPI valida y Orchestrator lo distribuye.
- **Campos declarados:** `origen: Literal[5]`, `tipo_evento: str`,
  `id_transaccion: str`, `timestamp: datetime | None`,
  `objeto_involucrado: str`, `detalles: Dict[str, Any]`.
- **Aceptación real/defaults:** los tres primeros son obligatorios; timestamp
  omitido genera UTC naive, `null` explícito permanece `None` y una fecha con zona
  conserva zona; objeto=`"ninguno"`; detalles=`{}` aislado por instancia.
- **Normalización:** solo el ID se recorta y rechaza whitespace. `tipo_evento=" "`
  pasa. Cualquier string no blanco sirve como ID aunque la descripción prometa UUID.
- **Unknown/serialización/errores:** origen fuera del literal, tipo vacío, ID blanco
  o detalles `None` fallan; extras top-level se ignoran; extras dentro de detalles
  se preservan, incluidos `"false"`, `0`, `{}` y `None`.
- **Side effects/pérdida/compatibilidad:** `/comando` reemplaza `tipo_evento` por
  `comando_usuario`. `timestamp` no tiene lector operativo; Unity no lo envía.
  BehaviorCore no recibe origen/ID/timestamp/objeto directamente. Triage sí recibe
  el dump completo. Los payloads legacy flexibles siguen aceptados.
- **Cobertura:** probe API/DTO y tests de terminal/orquestación; no había matriz
  explícita de whitespace, extras y tipos de `detalles`.

### 3.2 `AccionSaliente`, `ParametroHerramienta` y `RespuestaAPI`

- **Responsabilidad:** DTO público de acción y envelope REST.
- **Campos:** acción: ID/nombre obligatorios pero strings vacíos válidos; parámetros
  dict, razonamiento y pensamiento “privado”, bool de confirmación y descripción de
  parámetros. Respuesta: status `ok|error|pendiente`, mensaje, ID/acción opcionales
  y timestamp de servidor.
- **Aceptación/defaults:** Pydantic convierte `"false"→False` y `"yes"→True` en el
  bool tipado; dict/list defaults están aislados; extras se ignoran. Respuesta puede
  tener cualquier combinación de status y acción porque no hay validación cruzada.
- **Sentinelas:** `accion=None` significa que no existe objeto; un objeto con
  `nombre="none"` es una acción serializada de no-op; `nombre=""` también es válido.
  Las rutas actuales solo producen `ok/error`, no `pendiente`.
- **Conversión:** Orchestrator genera `id_accion`, renombra `razon→razonamiento` y
  `pensamiento→pensamiento_privado`, calcula confirmación y conserva parámetros.
  Elimina `valido`, `error`, `metadata` y status Motor.
- **Error/pérdida:** API envuelve cualquier `AccionSaliente`, incluso `none`
  inválido, como `status=ok`. La excepción se convierte a HTTP 500 con `str(e)`.
  `parametros_descripcion` nunca es poblado por el adaptador actual.
- **Cobertura:** probes API/AI, DTO y Motor; tests existentes comprueban formas
  nominales, no equivalencia semántica de no-op válido/fallido.

### 3.3 `AgentContext` y `PromptSections`

- **Responsabilidad:** proyección tipada del estado para construir prompt.
- **Campos/defaults:** strings vacíos, dict/list factories, tarea opcional,
  política=`exploradora`, proveedor=`ollama` y timestamp UTC aware.
- **Aceptación real:** Pydantic aplica coerción ordinaria e ignora extras. Dicts y
  listas mutables están aislados.
- **Normalización/pérdida:** `ContextBuilder` renombra `valores→valores_estado`,
  `umbrales→banderas`, colapsa herramientas `None/{vacío}` a colecciones vacías y
  tarea `{}` a `None`. De una tarea de nueve campos reproducida solo conserva siete;
  descarta timestamp y campos custom. `PromptSections` omite `""`, pero una cadena
  de whitespace sí crea una sección.
- **Errores/compatibilidad:** consumidores duck-typed pueden enviar dicts parciales;
  claves no proyectadas desaparecen silenciosamente. Es una contracción intencional
  del prompt, no se clasificó por sí sola como bug.
- **Cobertura:** probe API/AI; tests de prompts cubren el contrato nominal.

### 3.4 `LLMResponse`, `ActionResponse`, `OracleResult` y dict Persona

- **Responsabilidad:** `LLMResponse` valida el JSON general; `ActionResponse` es la
  decisión interna unificada; `OracleResult` es conocimiento frío antes de Persona;
  Persona usa un dict informal con acción/parámetros/pensamiento/razón y flags.
- **Campos/Optional:** `LLMResponse.pensamiento` es requerido y extras prohibidos;
  acción default `none`. `ActionResponse` requiere pensamiento/acción, usa dicts
  aislados, `valido=True`, `error=None`. Oracle tiene resumen/error opcionales,
  fuente literal y cache bool.
- **Normalización:** parser convierte acción ausente, `""`, whitespace,
  `"none"` en cualquier case y texto `"null"` a `"none"`. JSON `null` o bool en
  acción también se vuelve `none`, pero con `valido=False`. Texto `"false"` sigue
  siendo el nombre de acción `false` si no se limita el conjunto permitido.
- **Errores:** ausencia/malformación JSON devuelve `ActionResponse` inválido; un
  JSON sintácticamente válido con tipos inválidos puede hacer que el fallback del
  propio parser lance `ValidationError`. `ReasoningEngine` lo captura una frontera
  arriba y devuelve no-op inválido.
- **Persona:** tras su propio reintento, el adaptador fuerza `hablar`, reemplaza
  texto blanco, marca `valido=True` y pierde `error`, `requiere_reintento` y
  `recuperado_de_json_parcial`. AuditTrace observa el fallback antes de esa pérdida;
  la respuesta pública segura se considera ejecutable de forma deliberada.
- **Pérdida pública:** `ActionResponse` y `AccionSaliente` no representan la misma
  información. Los parámetros conservan referencias anidadas compartidas durante
  la conversión Pydantic; no se demostró mutación dañina en consumidores actuales.
- **Cobertura:** probes API/AI y Motor; tests nominales del parser, Persona, Oracle
  y prompt. Faltaba el roundtrip con estado inválido/metadata.

### 3.5 `IntentType`, `Route` y respuesta Triage

- **Responsabilidad:** taxonomía de intención y destino operativo.
- **Valores:** Intent incluye `NOOP`, `UNKNOWN`, `INTERNAL` y categorías sociales,
  Motor, memoria, sistema y Oracle. Route solo incluye `motor/persona/oracle/internal/noop`.
- **Normalización:** `None`, `False`, `0`, vacío, string `"none"` o desconocido se
  vuelven `IntentType.UNKNOWN`; aliases como `MOTOR` se vuelven intención canónica.
  `NOOP→noop`, `INTERNAL→internal`, `UNKNOWN→persona`.
- **Contrato real:** Orchestrator usa `route` como autoridad y no recalcula desde
  `intent`. Un dict contradictorio es técnicamente aceptado. El productor Triage
  puede emitir overrides deliberados (`CORE_SYSTEM/noop`, `CORE_SYSTEM/motor`,
  `UNKNOWN/noop`), y el terminal conserva la intención original pero cambia la ruta
  efectiva a persona. Esto es comportamiento intencional, no bug del clasificador.
- **Campos informales:** consumidores asumen claves `intent`, `route`, `confidence`,
  `reason` y flags derivados; no existe un modelo runtime cerrado.
- **Cobertura:** probe Motor, tests Triage y auditoría OBS-004.

### 3.6 Contratos Motor y percepción

- **StrictParams:** nueve familias de parámetros. Prohíben extras y aplican rangos,
  pero coercionan strings numéricos y bools a floats. Navegación/posición y fuerza
  aceptan NaN/Inf; al serializar el DTO se vuelven `null`.
- **MotorAction:** literal de nueve acciones físicas más `none`, parámetros dict,
  razón y metadata. Extras prohibidos y defaults aislados. El modelo no liga acción
  con su schema: acepta `none` con parámetros y acción física sin parámetros; el
  flujo normal de `MotorEngine` sí valida después.
- **Percepción:** `PerceivedObject` y `PerceptionSnapshot` son dataclasses mutables
  por contenido. Dict/legacy se normaliza; source inválido cae a Unity, confidence
  se acota y aliases/affordances se limpian. Una instancia ya canónica evita toda
  normalización, puede tener confidence 3.5/source inválido; `raw` se conserva por
  referencia. Distancia cero se pierde por selección con `or`.
- **MotorResolution:** dataclass con `ready`, `requires_clarification`, `not_found`,
  `not_allowed`, `unsafe`, candidatos, score y audit. `to_metadata` separa resultado
  y auditoría. El `Literal` no se valida runtime; un status inventado puede caer a
  Reasoning. `CandidateCorrection.to_dict()` usa `bool(...)`.
- **Scores:** confidence/score Motor nominalmente “mayor es mejor” y se redondean,
  pero las dataclasses no acotan. No equivalen a distancia de memoria.
- **Pérdida:** los cinco estados siguen distintos en metadata de `ActionResponse`;
  el DTO público conserva solo acción/prosa y pierde el status.
- **Cobertura:** probe Motor y seis módulos focalizados existentes.

### 3.7 `ToolResult`, `ActionExecutor` y `resultado_accion`

- **Responsabilidad:** frontera de ejecución local y retorno físico externo.
- **ToolResult:** dataclass `exito: bool=True`, mensaje, error opcional y dict extra
  aislado; sus anotaciones no validan runtime, por lo que acepta `"false"`.
- **Executor:** faltante/schema/confirmación/excepción producen bool False. Un
  `ToolResult` se devuelve intacto. Cualquier otro retorno, incluidos `False`,
  `None`, `{}` y `[]`, se convierte a `ToolResult(exito=True, mensaje=str(valor))`.
  Los built-ins actuales devuelven string de éxito o `ToolResult` con bool; no se
  halló productor incluido que emita el caso mal tipado.
- **Confirmación:** Orchestrator detiene `requiere_confirmacion=True`; Executor
  directo exige precisamente True para ejecutar. No existe un campo independiente
  que pruebe que la confirmación fue concedida.
- **Resultado externo:** Unity produce `detalles.exito` como bool. El schema API
  también permite cualquier tipo. BehaviorCore registra tarea con `bool(exito)`
  pero selecciona emoción con `is True/is False`; memoria y alias usan todavía
  otras condiciones de identidad.
- **Pérdida:** `ToolResult` se reduce a status `ok/error` y detalles sin el valor
  original de `exito`; un tipo incorrecto queda oculto tras truthiness.
- **Cobertura:** probe Motor/tools; tests de tools cubren built-ins válidos, no
  retornos falsey ni tipos arbitrarios.

### 3.8 Contratos de memoria

- **MemoryMetadata:** frozen superficial con tipo, valencia, impacto, timestamp
  naive y `extra` mutable. Rangos descritos no se validan.
- **EpisodicMemory/SemanticMemory:** frozen superficial; episodio contiene estado
  emocional opcional, semántica contiene origen. Defaults directos y del encoder
  discrepan para origen (`consolidacion` vs `determinista`).
- **MemoryDocument:** ID/contenido/metadata dict sin validación runtime; permite
  vacíos y tipos que el backend no necesariamente acepta.
- **WorkingMemoryItem:** acción, resultado string (`exito/fallo/pendiente` solo por
  convención), timestamp y detalles opcionales. La cola devuelve `None` si no hay
  último resultado y texto fijo si está vacía.
- **MemoryResult:** recuerdo + distancia raw (cosine, menor mejor) +
  `metadata_score` (reranking, mayor mejor). Score empieza en 1.0, el retriever lo
  muta y ordena; no está garantizado en `[0,1]`.
- **MemoryContextResult:** texto + tuplas alineadas de IDs/scores de la misma única
  recuperación. Usa `metadata_score`, no repite consulta y no expone distancia.
- **Serialización:** encoder persiste narrativa/regla y metadata escalar. Descarta
  `estado_emocional`; descarta `extra` no escalar; al hacer `update(extra)` permite
  sobrescribir campos reservados. Lectura reconstruye timestamps inválidos como
  “ahora”, metadata central por defaults y extras desconocidos.
- **Sentinelas/error:** Chroma distingue error `None` de no-hit; stores/retriever/
  manager reducen ambos a lista vacía. El resumen final de ambos es el mismo texto
  “No hay recuerdos relevantes para esta situación.” con IDs/scores vacíos.
  Escritura `False` y excepción se reducen a ID `None`.
- **Inmutabilidad:** factories aíslan defaults entre instancias, pero dicts anidados
  siguen mutables y entradas no vacías pueden conservar identidad.
- **Cobertura:** probe memoria, tests CRUD/reranking/OBS-005/conversación. Faltaban
  estado emocional, colisiones, timestamps aware, metadata vacía y flags string.

### 3.9 DTO Python ↔ Unity ↔ terminal

- **Evento:** nombres coinciden salvo `timestamp`, ausente en C#. Unity genera GUID
  y bools reales, pero Python no valida UUID.
- **Acción:** nombres coinciden salvo `parametros_descripcion`, ausente en C#.
  Unity mantiene un switch y claves ad hoc para parámetros; Python admite cualquier
  nombre/dict. Unity no lee `id_accion` ni `razonamiento`; sí muestra/registra
  `pensamiento_privado`.
- **Respuesta:** nombres coinciden. C# usa referencias potencialmente null. Unity no
  lee ID de transacción ni timestamp. Trata cualquier status distinto de `error`
  como éxito, por lo que `pendiente` sería éxito si se emitiera.
- **Error:** Python crea un `RespuestaAPI(status=error)` pero lo envía con HTTP 500;
  Unity solo deserializa DTO en éxito de transporte y en 500 entrega body crudo.
- **Terminal:** tercer contrato independiente basado en dict; exige `status=ok`,
  acción `hablar` y extrae solo `texto_a_comunicar`. Deliberadamente ignora campos
  privados.
- **Cobertura:** probe DTO de 55 checks, tests del terminal; Unity no se compiló ni
  ejecutó, por lo que tolerancia Json.NET a extras queda INFERIDA.

## 4. Diccionario de sentinelas

| Valor | Contexto/productor | Significado real en consumidor | Riesgo |
|---|---|---|---|
| Python `None` | retorno Orchestrator aceptado por API | no existe `AccionSaliente`; REST `accion:null`, status ok | distinto de objeto `nombre=none` |
| `None` | `EventoEntrante.timestamp` explícito | conserva JSON null; omisión genera hora | omisión y null no equivalen |
| `None` | `detalles.exito` | Behavior no registra intento; emoción genérica | ausencia, fallo y desconocido divergen |
| `None` | `ChromaClient.buscar` | error/no conexión | se pierde al pasar a stores |
| `None` | metadata entregada directamente a Chroma 1.5.9 | backend la acepta/persiste null | el tipo LAZZARO declara dict, no None |
| `None` | `guardar_experiencia/regla` | escritura no confirmada, tanto False como excepción | causa solo visible en log |
| bool `False` | campo Pydantic tipado | false real; string `"false"` también se convierte a False | comportamiento correcto en schemas tipados |
| bool `False` | `detalles.exito` | fallo en tarea/emoción/memoria; no aprueba alias | contrato nominal esperado |
| bool `False` | retorno de tool no `ToolResult` | se promueve a ToolResult exitoso con mensaje `False` | contradice intuición de retorno falsey |
| bool `True` | `detalles.solicita_decision` | solo identidad exacta activa decisión | `1` y `"true"` no equivalen |
| bool `True` | `requiere_confirmacion` en Orchestrator | pendiente, no ejecutar | en Executor directo autoriza ejecución |
| entero `0` | `detalles.exito` | tarea lo cuenta fallo; emoción no lo reconoce como False | mismo payload tiene dos lecturas |
| `0` | intent externo | normaliza a UNKNOWN→persona | no equivale a NOOP |
| `0` | distancia perceptiva | se trata como ausente y puede reemplazarse por fallback | pierde contacto válido |
| `""` | acción del parser | normaliza a `"none"` válido | raw response vacía produce `none` inválido |
| `""` | `AccionSaliente.nombre` | schema lo acepta; Unity lo trata como no ejecutable | Orchestrator no lo define como no-op |
| `""` | tarea/contexto | tarea dict vacío se vuelve `None`; sección prompt vacía se omite | whitespace no se omite |
| `{}` | `EventoEntrante.detalles`/defaults Pydantic | payload vacío válido y aislado | no equivale a None |
| `{}` | metadata Chroma 1.5.9 | ValueError→False | contradice tipos y test histórico |
| `{}` | retorno de tool no `ToolResult` | éxito con mensaje `{}` | ausencia promovida a éxito |
| `[]` | herramientas/contexto | colección válida vacía; `None` colapsa al mismo valor | pérdida de “no suministrado” |
| `[]` | store/retriever memoria | no-hit o error | degrada sin indicador |
| `"none"` | `ActionResponse.accion` | no ejecutar; puede ser válido o inválido | validez desaparece en DTO público |
| `"none"` | normalizador de intent | UNKNOWN→persona | no es IntentType.NOOP |
| `"false"` | `detalles.exito` | truthy en TaskContext; no bool para emoción | bug confirmado |
| `"false"` | `ToolResult.exito` | truthy→status `ok` | bug confirmado |
| `"false"` | metadata `approved/confirmed` | se acepta como verdadero | bug confirmado |
| `"false"` | bool Pydantic | se convierte correctamente a False | no generalizar el bug a schemas tipados |
| `"true"` | metadata/dict flexible | string truthy, no identidad True | lectura depende del consumidor |
| `"ok"` | `RespuestaAPI.status` | request procesado, aunque acción sea `none` inválida | no expresa outcome de decisión |
| `"error"` | `RespuestaAPI.status` | fallo; actual ruta lo acompaña con HTTP 500 | Unity no consume el DTO tipado |
| `"pendiente"` | schema RespuestaAPI | valor válido no producido hoy | Unity lo trataría como success |
| `"pendiente_confirmacion"` | resultado interno ejecución | no se ejecutó | distinto de status REST `pendiente` |
| Intent `NOOP` | clasificación | intención de no trabajo | normalmente route noop, pero route manda |
| Route `noop` | Orchestrator | corta flujo cognitivo | no es acción `none` ni retorno None |
| Intent `UNKNOWN` | normalización | fallback a persona por mapa canónico | Triage puede override a route noop |
| Intent/Route `INTERNAL/internal` | resultado interno | corta decisión externa, procesa estado | distinto de no-op pasivo |

## 5. Matriz Optional / default / empty

| Campo/contrato | Omitido | `None` explícito | Vacío | Efecto observable |
|---|---|---|---|---|
| `EventoEntrante.timestamp` | UTC naive ahora | aceptado, queda null | string vacío falla parse | contrato temporal triestado |
| `EventoEntrante.detalles` | `{}` | rechazado | `{}` válido | contenido no tipado |
| `EventoEntrante.objeto_involucrado` | `ninguno` | rechazado como str | `""` válido | escape trata varios strings como ausente |
| `RespuestaAPI.accion` | null | null | objeto `nombre=""` válido | tres formas públicas |
| `AgentContext.tarea` | None | None | `{}` se colapsa a None | no conserva “tarea vacía” |
| herramientas ContextBuilder | `[]/{}` | `[]/{}` | iguales | ausencia y vacío confluyen |
| `LLMResponse.accion` | `none` | inválido→fallback none inválido | `""`→none válido | validez depende de origen |
| `ActionResponse.error` | None | None | `""` válido | conversión pública siempre lo elimina |
| `ActionResponse.parametros/metadata` | `{}` | rechazado | `{}` válido | defaults aislados |
| `MotorResolution.action/target` | None | None | `""` permitido runtime | dataclass sin validación |
| percepción input | snapshot vacío generado | comparte ruta de no-dict | `""`, `[]`, False y 0 comparten ID | identidad ambigua |
| `ToolResult.error` | None | None | `""` distinto pero truthiness no usado | error no define por sí solo éxito |
| `resultado_accion.exito` | None | None | `""` cuenta intento fallido y emoción neutra | varios consumidores discrepan |
| `MemoryMetadata.extra` | dict aislado | constructor acepta None aunque métodos fallen después | `{}` válido en dataclass | inmutabilidad superficial |
| `estado_emocional` episodio | None | None | `{}` distinto en RAM | ambos se pierden tras persistencia |
| metadata batch de lectura | batch ausente→defaults | elemento None lanza | `{}`→defaults | ausencia y null no equivalen |
| búsqueda memoria | no aplica | error en Chroma | `[]` no-hit | capas públicas los confluyen |
| texto MemoryContext no-hit | frase fija | misma frase tras error | no es string vacío | IDs/scores sí vacíos |

Defaults mutables Pydantic/dataclass creados con factories quedaron aislados entre
instancias en Evento, ActionResponse, MotorAction, MotorResolution y ToolResult.
Eso descarta el candidato histórico de defaults compartidos. “Frozen” en memoria
solo impide reasignar atributos; no congela los dicts referenciados.

## 6. Roundtrips reproducidos

### 6.1 Evento público

```text
payload Unity sin timestamp
  → EventoEntrante
  → timestamp de recepción naive generado por Python
  → model_dump
  → Orchestrator
```

Se conservan origen, tipo, ID recortado, objeto y detalles. Se descartan extras
top-level. Dentro de detalles se preserva exactamente el tipo, por ejemplo string
`"false"`; no se convierte a bool. `/comando` cambia el tipo sobre la misma instancia.

### 6.2 Respuesta cognitiva

```text
LLM JSON
  → LLMResponse
  → ActionResponse
  → sanitización
  → AccionSaliente
  → RespuestaAPI
  → JSON
```

Pensamiento y razón se renombran; parámetros se preservan; se crea otro ID y se
deriva confirmación. Se pierden `valido`, `error`, `metadata`, status Motor y origen
del fallo. Tras normalizar el ID generado, un no-op válido y uno fallido con la
misma prosa resultan idénticos. El envelope API marca ambos `ok`.

### 6.3 Persona informal

```text
dict {accion, parametros, pensamiento_privado, razon,
      error, requiere_reintento, recuperado_de_json_parcial}
  → ActionResponse {accion=hablar, valido=True,
                    pensamiento, parametros, razon}
```

La ejecución de una frase fallback es deliberadamente válida; los indicadores del
fallo dejan de pertenecer al contrato ActionResponse. AuditTrace OBS-007 los observa
antes de la conversión, pero consumidores futuros de la decisión no los ven.

### 6.4 Herramienta local

```text
AccionSaliente
  → schema específico
  → función tool
  → ToolResult o retorno arbitrario
  → ToolResult normalizado
  → status ok/error + {mensaje,error,datos_extra}
```

Un `ToolResult(exito="false")` conserva el string y termina `ok`. Un retorno crudo
`False/None/{}/[]` termina también como éxito. El valor original de `exito` no forma
parte de detalles posteriores.

### 6.5 Memoria episódica

```text
EpisodicMemory(id, metadata, narrativa, estado_emocional)
  → MemoryDocument(id, narrativa, metadata escalar)
  → Chroma temporal
  → EpisodicMemory(id, metadata, narrativa, estado_emocional=None)
```

ID, narrativa, metadata central, distancia y extras escalares se conservan. Estado
emocional y extras dict/list/None se pierden. Valores `extra` con nombres reservados
pueden cambiar el tipo persistido u otros campos. SemanticMemory conserva regla y
metadata nominales; su default de origen depende del constructor usado.

### 6.6 Recuperación OBS-005

Los `retrieved_ids[i]` y `scores[i]` provienen del mismo `MemoryResult` y mantienen
orden de ranking. `scores` es `metadata_score`; `distancia` cruda no se disfraza ni
se exporta. No hay segunda búsqueda. Un no-hit y un error producen exactamente el
mismo `MemoryContextResult` público.

### 6.7 Python ↔ Unity

El payload nominal deserializa por nombres coincidentes. Se pierden timestamp de
evento y descripción de parámetros porque no existen en C#. Unity ignora de hecho
varios campos de correlación, y un HTTP 500 evita por completo el roundtrip del DTO
`RespuestaAPI` de error. No se compiló Unity; la equivalencia se verificó por
inventario estático y JSON conocido.

## 7. Respuestas a las 30 preguntas obligatorias

| # | Respuesta respaldada por evidencia |
|---:|---|
| 1 | `EventoEntrante` exige origen literal, tipo/ID strings no vacíos y detalles dict; solo ID se recorta. Timestamp es optional triestado, extras top-level se ignoran y detalles conserva Any. |
| 2 | `AccionSaliente` exige presencia de ID/nombre pero acepta vacíos; params/prosa/descripciones tienen defaults, confirmación es bool coercible y extras se ignoran. No contiene outcome/valid/error. |
| 3 | Evento→Behavior pierde campos fuera de tipo/detalles; ContextBuilder proyecta y descarta; ActionResponse→Accion elimina valid/error/metadata; ToolResult→resultado reduce exito a status truthy y elimina el valor original. |
| 4 | “No acción” puede ser retorno `None`, acción `none` válida, acción `none` inválida, Route noop, Intent NOOP, nombre vacío en Unity o status interno `sin_accion`; no son equivalentes. |
| 5 | `None` representa ausencia/fallo según frontera; `none` es nombre de acción; NOOP es intención/ruta; vacío puede normalizarse, rechazarse o conservarse según tipo. Ver diccionario. |
| 6 | Sí. Reproducido en ToolResult→Orchestrator, resultado_accion→TaskContext y flags de memoria. No ocurre en bools Pydantic bien tipados. |
| 7 | No. Timestamp Optional no es leído; `accion=None` sí lo toleran clientes; metadata explícitamente None falla en stores; campos opcionales de dataclass pueden romper consumidores. |
| 8 | Sí: detalles de evento, Triage dict, contexto, Persona dict, metadata Motor, params Unity y Chroma QueryResult tienen claves asumidas informalmente. |
| 9 | Sí: dict Persona tiene flags sin ActionResponse equivalente; resultado_accion no tiene schema; Triage no tiene modelo; Unity carece de timestamp/descripciones. |
| 10 | Sí, factories aíslan defaults probados. Las estructuras frozen de memoria siguen compartiendo mutables suministrados. |
| 11 | Pydantic API/internal ignora extras salvo LLM/Motor strict, que los rechazan; detalles preserva; normalizadores perceptivos descartan claves de objetos pero guardan raw; metadata Chroma desconocida se preserva en extra. |
| 12 | Depende: ID blanco falla, tipo whitespace pasa, parser acción vacía→none, prompt whitespace se conserva, Persona texto blanco usa fallback, AccionSaliente vacío pasa. |
| 13 | Sí: Pydantic convierte strings bool/número; Motor convierte bool a float; parsers usan str(); timestamps inválidos se reemplazan; truthiness cambia flags. |
| 14 | No. Distancia memoria es cosine raw/menor mejor; metadata_score es reranking/mayor mejor; confidence Triage/percepción/Motor nominalmente mayor mejor y con validaciones diferentes. |
| 15 | Sí. Consumidor acepta pares contradictorios y route manda. Triage y terminal generan overrides deliberados; no se clasificó como error de RF. |
| 16 | No. ActionResponse tiene valid/error/metadata y no tiene ID/confirmación/descripciones; AccionSaliente hace lo inverso. |
| 17 | pensamiento→pensamiento_privado; razón→razonamiento; metadata se pierde; Persona ya llama privado a su campo antes del adaptador. |
| 18 | Todo `pensamiento` interno convertido cruza REST como `pensamiento_privado`; Unity lo muestra/loguea. Terminal lo excluye. |
| 19 | No en runtime: ToolResult es dataclass sin validación. Built-ins observados sí producen bool. |
| 20 | API acepta Any; Unity produce bool; Behavior usa truthiness para tarea e identidad para emoción; Orchestrator/memoria/alias usan otras identidades. |
| 21 | Contrato global C. Backend 1.5.9: None válido, `{}` inválido, dict no vacío válido. Test histórico y anotaciones contradicen backend. |
| 22 | No: metadata central y extra escalar sí; extra no escalar se pierde, reservados pueden ser sobreescritos, timestamps cambian y estructuras son mutables internamente. |
| 23 | ID/texto/core metadata se conservan; estado emocional episódico no; origen semántico tiene defaults distintos; Optional no está validado runtime. |
| 24 | Sí en productores actuales: distancia raw queda intacta, metadata_score se calcula/muta y ordena; OBS-005 exporta solo score. No hay acotación runtime. |
| 25 | Sí dentro de MotorResolution/ActionResponse metadata. El DTO público elimina la distinción y convierte estados en habla/no-op con prosa. |
| 26 | Paridad parcial: nombres principales coinciden; Unity carece de timestamp/descripciones, no usa IDs/razón/tiempos, pending sería success y error 500 no se deserializa. |
| 27 | ID de transacción sí impulsa cache/idempotencia; id_accion se usa internamente para alias pero Unity no lo devuelve; timestamps API/Unity están inertes. |
| 28 | `timestamp_servidor` y `evento.timestamp` no tienen lectores; Unity no lee razón/IDs; parametros_descripcion no se llena; varios campos sí son telemetría/prosa. |
| 29 | Sí: detalles exige de hecho mensaje/exito/accion/percepción por evento; Unity espera nombres/params ad hoc; Triage/Persona/Motor metadata tienen claves no formalizadas. |
| 30 | Sí: eventos/acciones se duplican en Pydantic, C# y terminal; acciones/params en Motor schemas, Registry y switch Unity; estados de éxito/error en API, ToolResult, execution y working memory. |

## 8. Tickets

Todos permanecen **OPEN**. Las recomendaciones son preliminares para la fase de
fixes; no autorizan cambios dentro de este bloque.

| Namespace | P1 | P2 | P3 | Total |
|---|---:|---:|---:|---:|
| API | 2 | 2 | 0 | 4 |
| DTO | 0 | 4 | 0 | 4 |
| AI | 0 | 2 | 0 | 2 |
| MEM | 3 | 5 | 1 | 9 |
| TOOL | 3 | 1 | 0 | 4 |
| MOTOR | 2 | 4 | 1 | 7 |
| **Total** | **10** | **18** | **2** | **30** |

### API-CONTRACT-001 — P1 — OPEN

- **Clase/evidencia:** conflicto de frontera privada/pública;
  IMPLEMENTADO y OBSERVADO.
- **Ubicación:** `ai_engine/schemas.py:63-89`, `core/orchestrator.py:2518-2525`,
  `api/schemas.py:100-107`, `unity_client/DTOs.cs:20`,
  `unity_client/LazzaroAgent.cs:495-497,531`.
- **Reproducción:** un marcador sintético en `ActionResponse.pensamiento` queda en
  JSON `pensamiento_privado`; Unity lo muestra y registra. Terminal no lo imprime.
- **Impacto:** el nombre promete privacidad, pero el contrato REST es público. Un
  DevelopmentalCore más expresivo podría ampliar accidentalmente lo expuesto.
- **Recomendación preliminar:** definir política por canal y separar contenido
  público de telemetría protegida; nunca asumir privacidad por el nombre.

### API-CONTRACT-002 — P1 — OPEN

- **Clase/evidencia:** bug confirmado de divulgación de detalle interno;
  IMPLEMENTADO/OBSERVADO con excepción sintética.
- **Ubicación:** `api/routes.py:69-77`.
- **Reproducción:** `RuntimeError` sintético aparece literalmente dentro del
  `mensaje` público del HTTP 500.
- **Impacto:** una excepción puede contener rutas, datos de proveedor o contenido
  sensible. No se demostró credencial real, por eso no es P0.
- **Recomendación preliminar:** mensaje externo genérico + ID de correlación; detalle
  solo en log interno seguro.

### API-CONTRACT-003 — P2 — OPEN

- **Clase/evidencia:** contrato de ID contradictorio; OBSERVADO.
- **Ubicación:** `api/schemas.py:28-31,62-67`.
- **Reproducción:** `id_transaccion="not-a-uuid"` es válido; solo se exige no blanco.
- **Impacto:** descripción promete UUID único, mientras cache/idempotencia aceptan
  una clave opaca y no verifican unicidad.
- **Recomendación preliminar:** decidir UUID vs clave opaca antes de endurecer, con
  estrategia explícita de compatibilidad.

### API-CONTRACT-004 — P2 — OPEN

- **Clase/evidencia:** normalización y política de extras inconsistentes;
  OBSERVADO.
- **Ubicación:** `api/schemas.py:13-128`.
- **Reproducción:** ID whitespace se recorta, tipo whitespace pasa, ID/nombre de
  acción vacíos pasan y extras top-level se ignoran.
- **Impacto:** typos desaparecen y valores semánticamente vacíos cruzan fronteras.
- **Recomendación preliminar:** documentar por campo política de whitespace/extras
  antes de agregar validadores.

### DTO-CONTRACT-001 — P2 — OPEN

- **Clase/evidencia:** incompatibilidad del canal de error; IMPLEMENTADO/INFERIDO.
- **Ubicación:** `api/routes.py:71-77`, `unity_client/ApiClient.cs:31-59`.
- **Reproducción:** Python envía envelope tipado con HTTP 500; Unity solo
  deserializa en `Result.Success` y en fallo propaga el body crudo.
- **Impacto:** consumidor no puede manejar el mismo contrato de error de forma
  estable y mezcla transporte con dominio.
- **Recomendación preliminar:** un envelope versionado parseable también en no-2xx,
  después de eliminar detalle interno.

### DTO-CONTRACT-002 — P2 — OPEN

- **Clase/evidencia:** contrato duplicado y parcialmente informal; IMPLEMENTADO.
- **Ubicación:** `api/schemas.py:82-115`, `ai_engine/motor_schemas.py:85-126`,
  `unity_client/DTOs.cs:16-24`, `unity_client/LazzaroAgent.cs:533-572`.
- **Reproducción:** Python acepta nombre/params genéricos; Unity mantiene su switch
  y claves como `nombre_objeto`, `x/y/z`, `texto_a_comunicar`.
- **Impacto:** ambos lados pueden derivar sin detección automática.
- **Recomendación preliminar:** formalizar/versionar el lenguaje o generar pruebas
  de compatibilidad compartidas.

### DTO-CONTRACT-003 — P2 — OPEN

- **Clase/evidencia:** metadata temporal/correlacional inerte; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `api/schemas.py:33-35,88-90,145-155`,
  `unity_client/DTOs.cs`, `unity_client/LazzaroAgent.cs:401-407,449-465`.
- **Reproducción:** Unity no envía timestamp y no lee IDs/timestamp de respuesta;
  no hay lectura de `evento.timestamp`; resultados Unity no retornan id_accion.
- **Impacto:** hora de captura y correlación de acción se pierden o quedan solo como
  transporte nominal.
- **Recomendación preliminar:** declarar cada campo operativo o retirar su promesa
  en una migración compatible.

### DTO-CONTRACT-004 — P2 — OPEN

- **Clase/evidencia:** estado público indefinido; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `api/schemas.py:137-150`, `unity_client/ApiClient.cs:40-47`.
- **Reproducción:** Python acepta `status="pendiente"`; Unity solo distingue
  `error` y trataría pendiente como éxito. Rutas actuales no lo producen.
- **Impacto:** una futura emisión podría finalizar prematuramente el flujo cliente.
- **Recomendación preliminar:** definir semántica y rama cliente antes de usarlo.

### AI-CONTRACT-001 — P2 — OPEN

- **Clase/evidencia:** contrato de parser incompleto; OBSERVADO.
- **Ubicación:** `ai_engine/response_parser.py:48-62`.
- **Reproducción:** JSON válido con `pensamiento={}` falla `LLMResponse`; el fallback
  vuelve a construir `ActionResponse` con el dict y deja escapar `ValidationError`.
- **Impacto:** `parse()` no siempre devuelve el `ActionResponse` prometido. La
  frontera superior de Reasoning sí lo captura, limitando el daño actual.
- **Recomendación preliminar:** en fixes, validar/sanitizar campos del fallback y
  definir formalmente si el parser puede lanzar.

### AI-CONTRACT-002 — P2 — OPEN

- **Clase/evidencia:** adaptación intencional con pérdida de status; OBSERVADO.
- **Ubicación:** `ai_engine/persona_engine.py:221-283,328-382`,
  `core/orchestrator.py:1405-1425,1445-1464,1551-1571`.
- **Reproducción:** dict fallback con error/retry/partial se convierte en
  `ActionResponse(accion=hablar,valido=True,error=None)`.
- **Impacto:** la frase segura sigue siendo ejecutable, pero consumidores de
  ActionResponse no distinguen respuesta primaria de fallback. AuditTrace sí lo
  observa antes de la conversión.
- **Recomendación preliminar:** conservar outcome/fallback en un contrato interno
  explícito sin convertir la frase segura en inválida por accidente.

### MEM-CONTRACT-001 — P1 — OPEN

- **Clase/evidencia:** pérdida central confirmada; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/memory_types.py:27-38`,
  `memory/memory_encoder.py:121-147`, `memory/episodic_memory.py:108-113`,
  `core/orchestrator.py:1024-1029`.
- **Reproducción:** episodio con `estado_emocional` lo conserva antes de encoder,
  pero el documento no lo persiste y toda lectura reconstruye `None`.
- **Impacto:** el snapshot de estado que Orchestrator entrega se pierde siempre;
  memoria futura/DevelopmentalCore no puede recuperar ese contexto.
- **Recomendación preliminar:** decidir representación persistente y migración;
  no añadirla silenciosamente a metadata existente.

### MEM-CONTRACT-002 — P1 — OPEN

- **Clase/evidencia:** bug de sentinela confirmado; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/memory_manager.py:274-275,348,387-389`.
- **Reproducción:** alias con `approved="false"` pasa porque solo se excluye
  `is False`; memoria afectiva con `confirmed="false"` pasa por `bool(...)`.
- **Impacto:** hechos no aprobados pueden tratarse como aprobados y afectar grounding.
- **Recomendación preliminar:** contrato booleano estricto en escritura/lectura,
  con estado ausente separado y migración de metadata legacy.

### MEM-CONTRACT-003 — P1 — OPEN

- **Clase/evidencia:** degradación central indistinguible; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/chroma_client.py:193-217`, stores `:56-69`,
  `memory/memory_retriever.py:57-71`, `memory/memory_manager.py:444-518`,
  `memory/memory_summarizer.py:35`.
- **Reproducción:** no-hit y excepción producen el mismo texto, IDs y scores; un
  store fallido y otro válido puede parecer éxito parcial normal.
- **Impacto:** un consumidor no sabe si LAZZARO no recuerda o si la memoria falló,
  condición clave antes de confiar en aprendizaje/adaptación.
- **Recomendación preliminar:** resultado triestado/estado de degradación interno;
  mantener fallback de texto sin ocultar health.

### MEM-CONTRACT-004 — P2 — OPEN

- **Clase/evidencia:** contratos contradictorios, resolución de `MEM-AUDIT-001`;
  OBSERVADO.
- **Ubicación:** `memory/memory_types.py:56-66`,
  `memory/chroma_client.py:138-166`, `memory/test_chromadb_flow.py:225-237`.
- **Reproducción:** Chroma 1.5.9 temporal: None=true/null, `{}`=ValueError/False,
  dict no vacío=true. Tipos no admiten None y no prohíben `{}`; test exige `{}`.
- **Impacto:** callers no tienen una precondición única; el flujo normal no falla
  porque encoder agrega claves reservadas.
- **Recomendación preliminar:** fijar contrato soportado por versión y alinear tipos,
  adaptador y prueba en fase de fixes.

### MEM-CONTRACT-005 — P2 — OPEN

- **Clase/evidencia:** pérdida/colisión de metadata; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/memory_types.py:24`,
  `memory/memory_encoder.py:28-34,134-159`.
- **Reproducción:** dict/list/None extra se descartan; extras escalares con nombres
  `tipo`, `tipo_evento`, `valencia`, `timestamp` u `origen` sobrescriben reservados.
- **Impacto:** información desaparece o un recuerdo puede ocultarse/cambiar clase.
- **Recomendación preliminar:** namespace y política de colisiones/pérdida explícita,
  con compatibilidad de registros existentes.

### MEM-CONTRACT-006 — P2 — OPEN

- **Clase/evidencia:** contrato temporal incompatible; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/memory_types.py:22`, stores `:88-96`,
  `memory/memory_decay.py:44-45`.
- **Reproducción:** timestamp inválido se reemplaza por ahora; datetime aware es
  aceptado por dataclass pero la resta contra `datetime.utcnow()` naive falla.
- **Impacto:** historia cambia silenciosamente o una recuperación se degrada a vacío.
- **Recomendación preliminar:** definir una sola normalización UTC y error explícito.

### MEM-CONTRACT-007 — P2 — OPEN

- **Clase/evidencia:** score mutable/no acotado; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/memory_types.py:83-92`,
  `memory/memory_retriever.py:73-104`, `memory/memory_manager.py:499-518`.
- **Reproducción:** `metadata_score` inicia 1.0, se muta al rerank y puede salir de
  `[0,1]`; distancia raw queda separada y puede tener tipo inválido hasta rerank.
- **Impacto:** un consumidor puede asumir incorrectamente unidad/rango o interpretar
  el valor previo al rerank.
- **Recomendación preliminar:** documentar estado pre/post y rangos; nombres/unidades
  separados ya existen y deben preservarse.

### MEM-CONTRACT-008 — P2 — OPEN

- **Clase/evidencia:** inmutabilidad superficial; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `memory/memory_types.py:13-78`, `memory/memory_encoder.py:59-78`.
- **Reproducción:** dataclasses frozen impiden asignar atributos, pero `extra` y
  `estado_emocional` suministrados siguen siendo dicts mutables/referenciados.
- **Impacto:** un recuerdo aparentemente inmutable puede cambiar después de crearse.
- **Recomendación preliminar:** definir ownership/copia profunda o quitar la promesa
  de inmutabilidad, según costo.

### MEM-CONTRACT-009 — P3 — OPEN

- **Clase/evidencia:** default duplicado; IMPLEMENTADO.
- **Ubicación:** `memory/memory_types.py:43-53`,
  `memory/memory_encoder.py:87-113`, `memory/semantic_memory.py:98-123`.
- **Reproducción:** constructor directo usa origen `consolidacion`; encoder y
  reconstrucción usan `determinista`.
- **Impacto:** provenance depende de la ruta de construcción, sin fallo central
  demostrado.
- **Recomendación preliminar:** elegir una semántica al versionar el contrato.

### TOOL-CONTRACT-001 — P1 — OPEN

- **Clase/evidencia:** bug confirmado de truthiness; OBSERVADO.
- **Ubicación:** `tools/base.py:9-15`, `tools/executor.py:48-56`,
  `core/orchestrator.py:892-898,2635-2644`.
- **Reproducción:** tool fake devuelve `ToolResult(exito="false")`; Executor lo
  conserva y Orchestrator produce `ok`.
- **Impacto:** un fallo puede persistirse/contarse como éxito y aprobar alias. Los
  built-ins actuales no producen el tipo incorrecto, pero el contrato lo acepta.
- **Recomendación preliminar:** validar bool exacto y fallar cerrado ante resultados
  no conformes.

### TOOL-CONTRACT-002 — P1 — OPEN

- **Clase/evidencia:** contratos de seguridad incompatibles; OBSERVADO.
- **Ubicación:** `api/schemas.py:108-110`,
  `core/orchestrator.py:2513-2525,2619-2633`, `tools/executor.py:40-48`.
- **Reproducción:** el mismo `requiere_confirmacion=True` queda pendiente/no ejecuta
  en Orchestrator, pero ejecuta al llamar directamente Executor; False es rechazado.
- **Impacto:** requisito y concesión comparten un bit; futuros callers o tools
  riesgosas no tienen prueba inequívoca de consentimiento.
- **Recomendación preliminar:** separar requisito, estado pendiente y concesión/token
  verificable.

### TOOL-CONTRACT-003 — P2 — OPEN

- **Clase/evidencia:** adaptador promociona retornos indefinidos; OBSERVADO.
- **Ubicación:** `tools/executor.py:48-56`.
- **Reproducción:** `False`, `None`, `{}` y `[]` crudos se vuelven éxito con texto.
- **Impacto:** una tool custom/futura puede expresar fallo o ausencia y quedar éxito.
- **Recomendación preliminar:** lista cerrada de retornos válidos; falsey no
  declarado debe ser error contractual.

### TOOL-CONTRACT-004 — P1 — OPEN

- **Clase/evidencia:** bug confirmado de sentinela inter-módulo; OBSERVADO.
- **Ubicación:** `api/schemas.py:41-44`, `core/behavior_core.py:94-112,302-316`,
  `core/task_context.py:47-73`, `core/orchestrator.py:1162-1166,2761-2764`.
- **Reproducción:** `exito="false"` registra éxito de tarea pero emoción neutra;
  `0` registra fallo de tarea pero emoción neutra; `None` no registra intento.
- **Impacto:** tarea, frustración, emoción, memoria y alias discrepan sobre un mismo
  resultado.
- **Recomendación preliminar:** bool estricto al entrar `resultado_accion`, con
  ausencia/desconocido separado y compatibilidad de clientes.

### MOTOR-CONTRACT-001 — P1 — OPEN

- **Clase/evidencia:** pérdida central confirmada; IMPLEMENTADO/OBSERVADO.
- **Ubicación:** `ai_engine/motor_resolution.py:67-105`,
  `core/orchestrator.py:1964-1985,2007-2094,2511-2525`,
  `api/schemas.py:82-128`, `api/routes.py:52-67`.
- **Reproducción:** los cinco estados existen en metadata interna; conversión elimina
  metadata/valid/error. Passive none válido y unsafe none inválido quedan en API ok.
- **Impacto:** cliente/DevelopmentalCore no puede distinguir programáticamente
  no-acción, bloqueo, invalidez o error; solo queda prosa.
- **Recomendación preliminar:** outcome público/interno estructurado y versionado;
  no ampliar AuditTrace v1 para resolverlo.

### MOTOR-CONTRACT-002 — P1 — OPEN

- **Clase/evidencia:** bug numérico de frontera física; OBSERVADO.
- **Ubicación:** `ai_engine/motor_schemas.py:26-29,67-72`,
  `ai_engine/motor_engine.py:149-162`, `core/orchestrator.py:1964-1985`.
- **Reproducción:** navegar con `nan/inf` y aplicar fuerza con vector no finito pasa
  validación, queda `valido=True` y JSON lo convierte en null. Bools se vuelven 1/0.
- **Impacto:** comando físico inválido cambia significado al cruzar JSON y puede
  fallar o producir valor indefinido en cliente.
- **Recomendación preliminar:** finitud y tipos numéricos explícitos en boundary
  Motor, con pruebas Python/Unity.

### MOTOR-CONTRACT-003 — P2 — OPEN

- **Clase/evidencia:** schema llamado estricto no cruza acción/parámetros; OBSERVADO.
- **Ubicación:** `ai_engine/motor_schemas.py:98-126`.
- **Reproducción:** `none` con params y acción física con `{}` son `MotorAction`
  válidos. MotorEngine normal hace una validación posterior.
- **Impacto:** productores alternos pueden asumir más garantía de la que existe.
- **Recomendación preliminar:** documentar factory canónica o validación cruzada en
  futura versión.

### MOTOR-CONTRACT-004 — P2 — OPEN

- **Clase/evidencia:** Literals/dataclass sin enforcement runtime; OBSERVADO.
- **Ubicación:** `ai_engine/motor_resolution.py:11,44-105`,
  `ai_engine/motor_engine.py:252-274`, `core/orchestrator.py:1829-1847`.
- **Reproducción:** status inventado se construye y cae a Reasoning;
  `requires_confirmation="false"` se serializa True en CandidateCorrection.
- **Impacto:** typo de productor puede cambiar bloqueo por razonamiento.
- **Recomendación preliminar:** validación cerrada en frontera, sin confundir typing
  estático con runtime.

### MOTOR-CONTRACT-005 — P2 — OPEN

- **Clase/evidencia:** pérdida perceptiva confirmada; OBSERVADO.
- **Ubicación:** `ai_engine/perception_models.py:180-205`, especialmente `:204`.
- **Reproducción:** `distance=0` se convierte en None; si existe fallback legacy 9,
  el cero es reemplazado por 9.0.
- **Impacto:** contacto/distancia legítima puede afectar ranking y descripción.
- **Recomendación preliminar:** comprobar presencia de clave, no truthiness.

### MOTOR-CONTRACT-006 — P3 — OPEN

- **Clase/evidencia:** identidad de snapshot inestable; OBSERVADO.
- **Ubicación:** `ai_engine/perception_models.py:116-150,299-302`.
- **Reproducción:** None/False/0/[]/vacío comparten ID; dicts equivalentes con orden
  distinto producen IDs distintos.
- **Impacto:** correlación inestable; no se halló uso actual como clave persistente.
- **Recomendación preliminar:** canonicalizar solo si el ID adquiere semántica fuerte.

### MOTOR-CONTRACT-007 — P2 — OPEN

- **Clase/evidencia:** contrato canónico/ownership indefinido; OBSERVADO.
- **Ubicación:** `ai_engine/perception_models.py:58-87,103-104,144-149`.
- **Reproducción:** snapshot ya construido con source inválido/confidence 3.5 pasa
  intacto; mutar el raw original cambia el snapshot.
- **Impacto:** “canónico” no garantiza rangos ni estabilidad temporal.
- **Recomendación preliminar:** decidir si instancias se confían, copian o revalidan.

## 9. Candidatos históricos verificados

### Confirmados

| Candidato | Resultado |
|---|---|
| `bool(exito)` con string no vacío | Confirmado en ToolResult→Orchestrator, resultado_accion→TaskContext y flags de memoria |
| ActionResponse vs dict | Confirmado: Triage, Persona y Motor metadata usan dicts informales con campos que no caben en ActionResponse |
| ActionResponse→AccionSaliente | Confirmada pérdida de valid/error/metadata/status; rename de pensamiento/razón y nuevo ID |
| pensamiento vs pensamiento_privado | Confirmado: rename y cruce REST/Unity público |
| metadata Chroma `{}` | Confirmado como contrato C; backend 1.5.9 rechaza vacío |
| estado emocional episódico perdido | Confirmado en roundtrip temporal/fake store |
| timestamps declarados pero no usados | Confirmado en API/Unity; además incompatibilidad naive/aware en MemoryDecay |
| intent/route independientes | Confirmado; route es autoridad y hay overrides deliberados |
| payloads resultado de acción | Confirmado: Unity dict flexible, ToolResult interno y status de ejecución son contratos distintos |
| diferencias Python↔Unity | Confirmadas y acotadas en sección DTO |

### Descartados o acotados

- No se encontró un productor built-in actual que devuelva `ToolResult.exito` como
  string o un retorno crudo falsey. El contrato lo permite; la explotación actual
  no se afirma.
- `"false"` en campos bool Pydantic (`valido`, `requiere_confirmacion`, parámetros
  CancelTask) se convierte correctamente a False. El bug no es universal.
- Los defaults mutables creados con `default_factory` no se comparten entre
  instancias. La mutabilidad superficial de objetos ya suministrados es otro tema.
- `distancia` y `metadata_score` no son alias: tienen dirección, momento y propósito
  distintos. El riesgo es rango/estado no declarado, no confusión del productor.
- Los cinco estados Motor se preservan correctamente dentro de metadata interna;
  la pérdida ocurre al publicar la acción.
- El encoder normal genera metadata Chroma no vacía; `MEM-AUDIT-001` no bloquea la
  escritura episódica nominal.
- Los nombres de `RespuestaAPI` coinciden en Python y Unity. La incompatibilidad
  está en campos faltantes, uso, nulabilidad y rama HTTP de error.
- Una combinación intent/route no canónica no demuestra routing aleatorio ni un bug
  del RF: existen overrides intencionales de Triage/canal.

## 10. Límites para bloques posteriores

- **Bloque 3 / Orchestrator:** ownership completo del flujo, concurrencia, caches,
  retries, ordering y arquitectura. Aquí solo se observaron adaptadores.
- **Auditoría profunda Memory:** calidad de retrieval, filtros, consolidación,
  duplicados, migraciones y corrupción de datos reales. No se abrió la DB activa.
- **Capa 0:** calidad RF/heurísticas, dataset, drift y accuracy. Este bloque solo
  verificó el contrato intención/ruta.
- **Capa 1/fronteras:** transporte Unity, colas, movimiento, ejecución física y
  comportamiento Json.NET compilado.
- **Fase de fixes:** elegir/versionar sentinelas, envelopes, schema de
  resultado_accion, confirmación, salud de memoria y persistencia emocional.
- **Capa 2/DevelopmentalCore:** no iniciar hasta decidir al menos los P1 centrales;
  no debe inferir aprendizaje a partir de status actualmente colapsados.
- **AuditTrace:** permanece v1, sin stages/campos/rework. Los probes llenan la
  evidencia que v1 deliberadamente no almacena.

## 11. Pruebas y cierre técnico

| Comando/check | Resultado |
|---|---|
| `python docs/audit/probes/contracts_api_ai_probe.py` | PASS: 8/8 checks |
| `python docs/audit/probes/contracts_dto_probe.py` | PASS: 55 checks |
| `python docs/audit/probes/contracts_memory_probe.py` | PASS: 7 grupos de evidencia; Chroma 1.5.9 temporal |
| `python docs/audit/probes/contracts_motor_tools_probe.py` | PASS: 27/27 checks |
| regresión focalizada Motor/percepción | PASS: 6 módulos existentes |
| `python -m compileall ...` sobre producción y probes | PASS |
| `python docs/audit/probes/regression_probe.py` | 160 PASS, 1 fallo histórico conocido, 0 nuevos |
| `git diff --check` | PASS |
| escaneo de cambios funcionales | solo este informe y cuatro probes nuevos |
| red / proveedor / servidor / Unity | no usados |
| Chroma diaria / eventos diarios | no abiertos ni modificados por los probes |

El único fallo de regresión es el ya fotografiado
`memory.test_chromadb_flow::test_eliminacion`: la inserción con metadata `{}` falla
antes de probar eliminación. Este bloque lo explica como `MEM-CONTRACT-004`; no lo
corrige ni lo cuenta como regresión nueva.

## 12. Archivos del bloque y estado Git

Archivos nuevos del bloque:

- `docs/audit/block_02_contracts.md`
- `docs/audit/probes/contracts_api_ai_probe.py`
- `docs/audit/probes/contracts_dto_probe.py`
- `docs/audit/probes/contracts_memory_probe.py`
- `docs/audit/probes/contracts_motor_tools_probe.py`

No se modificó ningún archivo funcional. Los cambios runtime preexistentes y
excluidos del commit siguen siendo:

```text
 M lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin
 M lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin
 M lazzaro_data/chroma.sqlite3
```

## 13. Gate de salida

- [x] Se reconstruyeron entradas/salidas y productores/consumidores centrales.
- [x] Se distinguieron None/False/`none`/vacíos por frontera.
- [x] Se reprodujeron normalizaciones, coerciones y pérdida de información.
- [x] Se resolvió `MEM-AUDIT-001` sin tocar memoria real.
- [x] Se verificaron roundtrips Memory, Motor, tools y Python/Unity.
- [x] Se clasificaron hechos, comportamientos intencionales y deuda.
- [x] No se modificó comportamiento funcional ni AuditTrace.
- [x] No hay regresiones nuevas ni P0 demostrado.
- [x] No se inició Bloque 3 ni Capa 2; no hubo push.

Conclusión: Bloque 2 puede cerrarse administrativamente. La próxima acción no es
“arreglar todos los tipos”, sino priorizar decisiones de contrato para los diez P1,
con especial atención a outcome público, booleanos de resultado, confirmación,
finitud Motor y salud/roundtrip de memoria.
