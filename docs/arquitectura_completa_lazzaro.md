# Arquitectura Completa De Lazzaro

Actualizado: 2026-06-11

Documento para explicar el proyecto a un colaborador tecnico. Describe carpetas,
archivos, contratos de entrada/salida y flujo actual entre Unity y Python.

> Nota de seguridad: no compartir el contenido real de `.env`. Este documento
> solo describe que variables espera el sistema, sin exponer claves.

## Resumen Ejecutivo

Lazzaro es un agente cognitivo encarnado. Unity funciona como cuerpo y jardin
de pruebas: percibe objetos, ejecuta movimiento NavMesh, muestra habla, aplica
expresiones VRM y reporta resultados fisicos. Python funciona como cerebro:
recibe eventos REST, actualiza estado, escribe memoria, clasifica intencion,
elige una capa cognitiva y devuelve una accion estructurada.

La prioridad actual del sistema es estabilidad con bajo consumo:

- La telemetria pasiva se guarda en memoria, pero no consulta memoria vectorial
  ni llama al LLM si Triage la corta.
- La conversacion social no puede emitir acciones fisicas.
- Los comandos motores incompletos piden aclaracion antes de inventar objetos.
- Las rutas costosas usan caches y fallbacks para evitar bloqueos por un solo
  proveedor LLM.
- Unity evita bucles: `hablar` no genera `resultado_accion`.

## Capas Cognitivas

| Capa | Modulo principal | Responsabilidad | LLM |
|---|---|---|---|
| Capa 0 | `ai_engine/triage_router.py` | Clasifica cada evento como `noop`, `internal`, `motor`, `persona` u `oracle`. | No |
| Capa 1 | `ai_engine/motor_engine.py` | Resuelve y valida comandos fisicos. | Solo fallback si el motor determinista no basta |
| Capa 2 | `ai_engine/persona_engine.py` | Conversacion, identidad, estado biologico y voz segura. | Si |
| Capa 3 | `ai_engine/oracle_engine.py` | Preguntas tecnicas/complejas con cache semantico. | Si, con cache y fallback |
| Fallback | `ai_engine/reasoning_engine.py` | Pipeline historico de prompt + LLM + parser. | Si |

## Diagrama General

```mermaid
flowchart TD
    U["Unity Client<br/>LazzaroAgent.cs"] -->|"POST /api/v1/evento<br/>EventoEntrante"| API["FastAPI<br/>api/routes.py"]
    API --> ORCH["Orchestrator<br/>core/orchestrator.py"]

    ORCH --> BCORE["BehaviorCore<br/>estado, tarea, psicologia, constelacion"]
    ORCH --> TRIAGE["TriageRouter<br/>Capa 0"]
    ORCH --> MEMW["MemoryManager.guardar_experiencia<br/>escritura siempre"]

    TRIAGE -->|"noop/internal"| NONE["Accion none<br/>sin lectura vectorial<br/>sin LLM"]
    TRIAGE -->|"motor"| MOTOR["MotorEngine<br/>Capa 1"]
    TRIAGE -->|"persona"| PERSONA["PersonaEngine<br/>Capa 2"]
    TRIAGE -->|"oracle"| ORACLE["OracleEngine<br/>Capa 3"]
    TRIAGE -->|"fallback"| REASON["ReasoningEngine<br/>pipeline clasico"]

    MOTOR --> OUT["ActionResponse"]
    PERSONA --> ADAPTER["LLMGenerationAdapter<br/>text/plain"]
    ORACLE --> OCACHE["OracleMemory<br/>cache semantico"]
    ORACLE --> OCLIENT["OracleClient<br/>API externa opcional"]
    ORACLE --> ADAPTER
    REASON --> LLM["AI client activo<br/>FallbackAIClient o cliente unico"]
    ADAPTER --> LLM
    LLM --> PARSE["Parser / keep_y_save"]
    PARSE --> OUT
    NONE --> OUT

    OUT --> SAN["Sanitizacion + AccionSaliente"]
    SAN --> API
    API -->|"RespuestaAPI"| U
    U -->|"ejecuta accion / reporta resultado"| API
```

## Flujo Real De Un Evento

```mermaid
sequenceDiagram
    participant Unity as Unity/LazzaroAgent
    participant API as FastAPI
    participant Orch as Orchestrator
    participant Core as BehaviorCore
    participant Triage as TriageRouter
    participant Mem as MemoryManager
    participant Layer as Motor/Persona/Oracle/Reasoning

    Unity->>API: EventoEntrante
    API->>Orch: procesar_evento(evento)
    Orch->>Orch: cache por id_transaccion
    Orch->>Core: procesar_evento(tipo, detalles)
    Core-->>Orch: estado, contexto_llm, tarea, politica, narrativa
    Orch->>Triage: route_event(evento, estado)
    Triage-->>Orch: intent, route, confidence, reason
    alt evento sobrevivio al triage
        Orch->>Mem: obtener_contexto_memoria + obtener_ultimas_acciones
        Mem-->>Orch: memoria de trabajo + recuerdos
    else noop/internal
        Orch->>Orch: saltar lectura vectorial
    end
    Orch->>Mem: guardar_experiencia siempre
    alt alerta_dolor_fisico
        Orch-->>API: accion de escape segura
    else noop/internal
        Orch-->>API: none
    else resultado_accion hablar o sin solicita_decision
        Orch-->>API: none
    else motor/persona/oracle/fallback
        Orch->>Layer: resolver ruta
        Layer-->>Orch: ActionResponse
    end
    Orch->>Orch: sanitizar, cachear, notificar, registrar IdentityState
    API-->>Unity: RespuestaAPI
```

Punto clave: el sistema separa lectura y escritura de memoria. Todos los eventos
se escriben para alimentar consolidacion y "sueno"; solo los eventos relevantes
leen memoria vectorial para ahorrar latencia y tokens.

## Contrato Principal De Datos

### EventoEntrante

Llega desde Unity, Roblox, sandbox u otro cliente.

```json
{
  "origen": "unity",
  "tipo_evento": "comando_usuario",
  "id_transaccion": "uuid",
  "objeto_involucrado": "ninguno",
  "detalles": {}
}
```

Campos frecuentes en `detalles`:

| Campo | Uso |
|---|---|
| `mensaje` | Texto del usuario, manual inicial o texto de subtarea. |
| `solicita_decision` | Si `true`, una telemetria puede pedir decision. Default practico: `false`. |
| `objetos_visibles` | Resumen textual o lista comprimida de percepcion Unity. |
| `resumen_percepcion` | Texto legible de percepcion frontal. |
| `cantidad_objetos_visibles` | Conteo de objetos detectados. |
| `posicion_lazzaro` | Coordenadas `{x, y, z}`. |
| `objeto_mas_cercano` | Objeto destacado para razonamiento espacial. |
| `accion`, `exito`, `mensaje_resultado` | Resultado de accion ejecutada por Unity. |
| `mensaje_critico` | Descripcion de dolor, choque o interrupcion. |
| `direccion_escape`, `distancia_escape` | Parametros para escape fisico inmediato. |

Tipos de evento principales:

| Tipo | Intencion |
|---|---|
| `inicio_simulacion` | Despertar inicial. Unity puede mandar manual espacial una sola vez. |
| `actualizacion_estado` | Telemetria pasiva. Normalmente Triage la corta. |
| `comando_usuario` | Instruccion directa. Pasa por Triage. |
| `resultado_accion` | Reporte interno de Unity. Normalmente no llama al LLM. |
| `alerta_dolor_fisico` | Interrupcion critica. Prioridad maxima, sin LLM. |

### AccionSaliente

Sale desde Python hacia Unity.

```json
{
  "id_accion": "uuid",
  "nombre": "hablar",
  "parametros": {"texto_a_comunicar": "texto"},
  "razonamiento": "motivo breve",
  "pensamiento_privado": "debug interno",
  "requiere_confirmacion": false,
  "emocion_facial": "Fcl_ALL_Neutral"
}
```

Acciones actuales:

| Accion | Parametros principales | Ejecuta |
|---|---|---|
| `none` | `{}` | No hace nada. |
| `hablar` | `texto_a_comunicar` | Unity muestra voz/log. No reporta resultado. |
| `navegar_a_objeto` | `nombre_objeto` | NavMesh hacia objeto visible. |
| `navegar_a_posicion` | `x`, `y`, `z` | NavMesh hacia coordenadas. |
| `alejarse_de_objeto` | `nombre_objeto`, `distancia` | Punto opuesto al objeto. |
| `girar_avatar` | `grados` | Rotacion relativa en Y. |
| `interactuar_objeto_3d` | `nombre_objeto`, `tipo_accion` | Mirar/tocar/inspeccionar/golpear/usar/activar. |
| `agarrar_objeto` | `nombre_objeto` | Agarrar objeto cercano. |
| `soltar_objeto` | `fuerza_lanzamiento` | Soltar objeto. |
| `aplicar_fuerza` | `nombre_objeto`, `vector_x`, `vector_y`, `vector_z`, `fuerza` | Aplicar fuerza simulada. |

## Carpetas Principales

| Carpeta | Responsabilidad | Recibe | Entrega |
|---|---|---|---|
| `ai_engine/` | Clientes LLM, prompts, triage, motor, persona, oracle y parser. | Contexto, memoria, herramientas, mensajes. | `ActionResponse`, prompts, rutas, texto LLM. |
| `api/` | API REST FastAPI y modelos HTTP. | JSON de clientes externos. | `RespuestaAPI`, estado, notificaciones, constelacion. |
| `config/` | Configuracion por `.env`/Pydantic. | Variables de entorno. | `Settings`. |
| `core/` | Orquestador, estado, tarea, personalidad, psicologia, politica de herramientas. | `EventoEntrante`. | Estado interno y `AccionSaliente`. |
| `memory/` | Memoria episodica, semantica, trabajo, consolidacion y ChromaDB. | Narrativas, reglas, consultas. | Recuerdos, resumenes, persistencia. |
| `telescopio/` | Modelo y visualizador de constelacion cognitiva. | Estado bruto o ChromaDB. | Grafo y HTML visual. |
| `tools/` | Registro y ejecucion de herramientas simuladas/reales. | Acciones y parametros. | `ToolResult` o confirmaciones. |
| `unity_client/` | Cuerpo Unity: percepcion, movimiento, voz, VRM, cola de tareas. | Respuestas API y eventos de usuario/fisica. | Eventos HTTP y acciones en escena. |
| `docs/` | Bitacoras y documentacion tecnica. | Cambios del proyecto. | Markdown compartible. |
| `lazzaro_data/` | Persistencia ChromaDB. | Memoria y cache. | Base local. No editar manualmente. |

## Inicializacion Del Sistema

```mermaid
flowchart TD
    START["uvicorn main:app"] --> SETTINGS["Settings desde .env"]
    SETTINGS --> CORE["BehaviorCore"]
    SETTINGS --> CHROMA["ChromaClient(CHROMA_DB_PATH)"]
    CHROMA --> MEMORY["MemoryManager"]
    SETTINGS --> CLIENT["build_ai_client<br/>cliente unico o FallbackAIClient"]
    CLIENT --> REASON["ReasoningEngine"]
    REASON --> ORCH["Orchestrator"]
    CORE --> ORCH
    MEMORY --> ORCH
    ORCH --> APISTATE["app.state.orchestrator"]
    APISTATE --> READY["FastAPI listo"]
```

`main.py` importa por defecto las herramientas simuladas:

- `tools.simulated.interaccion_sim`
- `tools.simulated.keyboard_sim`
- `tools.simulated.mouse_sim`
- `tools.simulated.movimiento_sim`

Las herramientas reales de escritorio no se importan por defecto.

## Backend LLM Y Fallbacks

El punto unico para construir clientes LLM es `ai_engine/client_factory.py`.

Proveedores soportados:

- `gemini`
- `groq`
- `xai`
- `kimi`
- `deepseek_api`
- `ollama`

Variables principales:

| Variable | Uso |
|---|---|
| `AI_PROVIDER` | Proveedor principal si no se define `AI_FALLBACK_ORDER`. |
| `AI_FALLBACK_ORDER` | Orden explicito de proveedores, separado por comas. Si existe, reemplaza el orden default. |
| `LLM_FALLBACKS_ENABLED` | Si es falso, se usa solo el primer cliente valido. |
| `GEMINI_API_KEY`, `GEMINI_MODEL_NAME` | Gemini via `google.genai`. |
| `GROQ_API_KEY`, `GROQ_MODEL` | Cliente compatible OpenAI para Groq. |
| `XAI_API_KEY`, `XAI_MODEL` | Cliente compatible OpenAI para xAI. |
| `KIMI_API_KEY`, `KIMI_MODEL` | Cliente compatible OpenAI para Moonshot/Kimi. |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_API_MODEL` | DeepSeek remoto. Tambien acepta alias `DEEPSEEEK_API_KEY`. |
| `OLLAMA_BASE_URL`, `OLLAMA_HOST`, `OLLAMA_PORT`, `OLLAMA_MODEL_NAME`, `DEEPSEEK_MODEL` | Ollama local. |
| `OPENAI_COMPAT_TIMEOUT` | Timeout comun para clientes compatibles OpenAI. |

Si hay mas de un cliente disponible, `build_ai_client()` devuelve
`FallbackAIClient`. Ese wrapper prueba proveedores hasta encontrar texto no
vacio. Importante: hoy el fallback opera a nivel de texto crudo, no valida JSON
semanticamente antes de aceptar una respuesta.

`LLMGenerationAdapter` centraliza llamadas textuales tipo chat para Persona y
Oracle. Convierte mensajes `{role, content}` a prompt plano y usa
`response_mime_type="text/plain"`.

## Flujo Por Ruta

### NOOP / INTERNAL

Entrada tipica:

- `actualizacion_estado`
- `resultado_accion`
- eventos pasivos o internos

Proceso:

- `BehaviorCore` actualiza estado.
- `TriageRouter` corta.
- `MemoryManager.guardar_experiencia(...)` se ejecuta igual.
- No se llama a `obtener_contexto_memoria`.
- No se llama al LLM.

Salida:

- `ActionResponse(accion="none")`

### MOTOR

Entrada tipica:

- "camina hacia el cubo"
- "alejate de la sphere"
- "gira 180 grados"
- "ve a x=1 y=0 z=3"

Proceso:

- `MotorEngine.diagnosticar_comando_usuario(...)` intenta resolver sin LLM.
- Si el comando esta listo, valida con `motor_schemas.py`.
- Si falta objetivo, responde con `hablar` pidiendo aclaracion.
- Si el motor determinista no puede resolver, usa `ReasoningEngine`.
- Cualquier accion motora generada por LLM vuelve a pasar por `MotorEngine`.

Salida:

- Accion fisica validada, `hablar` de aclaracion o `none` seguro.

### PERSONA

Entrada tipica:

- "hola"
- "como te sientes?"
- "te gusta tu nombre?"

Proceso:

- `PersonaEngine.preparar_interaccion(...)` arma mensajes tipo chat.
- Inyecta `IdentityState`, percepcion y recuerdos.
- `LLMGenerationAdapter` llama al cliente LLM.
- `PersonaEngine.parsear_respuesta(...)` tolera JSON sucio/parcial.
- Si la salida es irrecuperable, el orquestador hace un reintento breve.
- Cualquier accion distinta de `hablar` se fuerza a `hablar`.

Salida:

- Siempre una respuesta social segura con `accion="hablar"`.

### ORACLE

Entrada tipica:

- preguntas tecnicas
- codigo
- debugging
- arquitectura
- matematicas
- filosofia compleja

Proceso:

- `OracleEngine.evaluar_cache(...)` consulta `OracleMemory`.
- Si hay cache hit, responde sin LLM.
- Si no hay cache, `OracleClient` intenta API externa opcional.
- Si falla o no hay key, usa el cliente LLM activo via `LLMGenerationAdapter`.
- `OracleEngine.keep_y_save(...)` limpia la respuesta, fuerza `hablar` y guarda cache.

Salida:

- `hablar` con respuesta tecnica o fallback de rescate.

### Interrupcion Critica

`alerta_dolor_fisico` no depende del LLM. El orquestador intenta:

1. `navegar_a_posicion` usando posicion actual y vector de escape.
2. `alejarse_de_objeto` si hay objeto de escape.
3. `none` seguro si no hay datos suficientes.

## Memoria

`MemoryManager` es la fachada principal.

Componentes:

- `ChromaClient`: wrapper de ChromaDB con lock por ruta dentro del proceso.
- `EpisodicMemoryStore`: experiencias concretas.
- `SemanticMemoryStore`: reglas y abstracciones.
- `MemoryRetriever`: busqueda hibrida episodica + semantica.
- `MemorySummarizer`: resumen compacto para prompt.
- `WorkingMemory`: acciones recientes en RAM.
- Consolidacion observacional: compacta telemetria vieja en reglas semanticas.

Reglas actuales:

- Todos los eventos se guardan como experiencia si es posible.
- La lectura vectorial solo ocurre cuando el evento sobrevive al triage.
- `resultado_accion` y `actualizacion_estado` no entran a memoria de trabajo si terminan en `none` pasivo.
- Al detectar descanso, el orquestador llama a `consolidar_memorias_si_hace_falta()`.
- `ChromaClient` tiene compuerta de hilos process-local; no protege contra otro proceso externo tocando la misma DB.

## Unity

Scripts principales:

- `DTOs.cs`: contrato C# equivalente a la API.
- `ApiClient.cs`: cliente HTTP con timeout configurable y parseo JSON.
- `PerceptionSystem.cs`: percepcion comprimida, resumen textual, busqueda de objetos.
- `MovementController.cs`: NavMesh para navegar, alejarse y girar.
- `LazzaroAgent.cs`: coordinador de eventos, cola, UI, VRM y reportes.

Flujo Unity:

```mermaid
flowchart TD
    INPUT["Input Dios / evento Unity"] --> QUEUE["colaEventos / colaTareasUsuario"]
    QUEUE --> API["ApiClient.EnviarEvento"]
    API --> RESP["RespuestaAPI"]
    RESP --> AGENT["LazzaroAgent.ProcesarRespuestaDelCerebro"]
    AGENT --> FACE["Aplicar emocion_facial"]
    AGENT --> THINK["Mostrar pensamiento_privado"]
    AGENT --> SWITCH["switch accion.nombre"]
    SWITCH -->|"hablar"| VOICE["UI voz + Debug.Log<br/>sin ReportarResultadoAccion"]
    SWITCH -->|"navegar_a_objeto"| NAVOBJ["MovementController.NavegarAObjeto"]
    SWITCH -->|"navegar_a_posicion"| NAVPOS["MovementController.NavegarAPosicion"]
    SWITCH -->|"alejarse_de_objeto"| AWAY["MovementController.AlejarseDeObjeto"]
    SWITCH -->|"girar_avatar"| ROT["MovementController.GirarAvatar"]
    SWITCH -->|"interactuar/agarrar"| VALID["Validar objeto visible y cercano"]
    NAVOBJ --> DONE["OnMovimientoCompletado"]
    NAVPOS --> DONE
    AWAY --> DONE
    ROT --> DONE
    DONE --> REPORT["ReportarResultadoAccion"]
    VALID --> REPORT
    REPORT --> API
```

Reglas importantes de estabilidad:

- `inicio_simulacion` envia manual espacial.
- `actualizacion_estado` no reinyecta el manual.
- El escaneo pasivo se omite mientras `MovementController.IsMoving` es true.
- `hablar` completa la subtarea localmente, pero no manda `resultado_accion`.
- `agarrar_objeto` e `interactuar_objeto_3d` validan objeto visible y distancia.
- La cola de tareas permite secuencias con separadores tipo `luego`, `despues`,
  `despues` con acento y `finalmente`.
- Riesgo pendiente: comandos unidos solo por coma pueden quedar como una sola
  subtarea.

## Inventario De Archivos

### Raiz

| Archivo | Hace | Recibe | Sale |
|---|---|---|---|
| `main.py` | Punto de entrada FastAPI. Carga settings, memoria, cliente LLM, herramientas y orquestador. | `.env`, imports, ciclo FastAPI. | App `FastAPI` y cierre ordenado. |
| `README.md` | Resumen y enlaces de documentacion. | Lectura humana. | Guia breve. |
| `requirements.txt` | Dependencias Python. | `pip`. | Entorno reproducible. |
| `.env` | Configuracion local secreta. | Claves/modelos/puertos. | Valores para `Settings`. |
| `.editorconfig` | Convenciones de edicion. | Editores compatibles. | Encoding/estilo consistente. |
| `.gitignore` | Reglas Git. | Patrones. | Evita versionar secretos/cache. |
| `lazzaro.log` | Log runtime. | Mensajes logger. | Diagnostico. |
| `esp32_scan_log.txt` | Log auxiliar de sensores/ESP32. | Runtime externo. | Diagnostico. |
| `teses.py` | Script manual antiguo. | Ejecucion directa. | Salida por consola. |
| `to-do` | Pendientes libres. | Texto humano. | Texto humano. |

### `api/`

| Archivo | Hace |
|---|---|
| `api/__init__.py` | Marca paquete. |
| `api/schemas.py` | Modelos HTTP: `EventoEntrante`, `AccionSaliente`, `RespuestaAPI`, `EstadoAgente`, `NotificacionSaliente`. |
| `api/routes.py` | Endpoints `/evento`, `/estado`, `/notificaciones`, `/constelacion`, `/comando`. Usa `run_in_threadpool`. |

### `config/`

| Archivo | Hace |
|---|---|
| `config/settings.py` | Define proveedores LLM, fallbacks, Chroma, Ollama, Gemini, Groq, xAI, Kimi, DeepSeek y Oracle. Ignora extras de `.env`. |

### `core/`

| Archivo | Hace |
|---|---|
| `core/__init__.py` | Marca paquete. |
| `core/logger.py` | Logging a consola y `lazzaro.log`. |
| `core/orchestrator.py` | Cerebro central: idempotencia, behavior, triage, memoria, capas, herramientas, notificaciones e identidad. |
| `core/tool_policy.py` | Politica declarativa de herramientas por ruta/origen. Evita que Unity reciba herramientas de escritorio. |
| `core/behavior_core.py` | Integra estado, personalidad, tarea, psicologia y constelacion. |
| `core/state_engine.py` | Variables emocionales/cognitivas y decaimiento temporal. |
| `core/task_context.py` | Objetivo activo, intentos, fallos, compulsiones y loops. |
| `core/personality.py` | Rasgos moldeables. |
| `core/psychology.py` | Traduce estado numerico a narrativa y contexto LLM. |

### `ai_engine/`

| Archivo | Hace |
|---|---|
| `ai_engine/__init__.py` | Marca paquete. |
| `ai_engine/ai_client.py` | Interfaz base para clientes LLM. |
| `ai_engine/client_factory.py` | Construye cliente unico o `FallbackAIClient`. |
| `ai_engine/fallback_ai_client.py` | Cadena de proveedores LLM por texto no vacio. |
| `ai_engine/openai_compatible_client.py` | Cliente `/chat/completions` para Groq/xAI/Kimi/DeepSeek. |
| `ai_engine/gemini_client.py` | Cliente Gemini con `google.genai`, reintentos y rate limits. |
| `ai_engine/ollama_client.py` | Cliente local Ollama/DeepSeek R1. |
| `ai_engine/llm_generation_adapter.py` | Adaptador comun para Persona/Oracle en modo texto. |
| `ai_engine/reasoning_engine.py` | Pipeline LLM general historico. |
| `ai_engine/context_builder.py` | Convierte dicts a `AgentContext`. |
| `ai_engine/prompt_builder.py` | Ensambla prompts desde plantillas. |
| `ai_engine/response_parser.py` | Extrae JSON y valida acciones del LLM general. |
| `ai_engine/schemas.py` | Modelos internos de prompt/respuesta. |
| `ai_engine/llm_policies.py` | Temperatura, tokens y riesgo por politica/proveedor. |
| `ai_engine/tool_router.py` | Filtro adicional de herramientas en `ReasoningEngine`. |
| `ai_engine/intent_types.py` | Enum de intenciones y rutas. |
| `ai_engine/routing_rules.py` | Reglas keyword para clasificar mensajes. |
| `ai_engine/triage_router.py` | Capa 0 de enrutamiento. |
| `ai_engine/spatial_reasoner.py` | Utilidades espaciales deterministas. |
| `ai_engine/motor_schemas.py` | Esquemas estrictos de acciones fisicas. |
| `ai_engine/motor_engine.py` | Capa 1 motora. |
| `ai_engine/identity_state.py` | Energia, estres y enfoque para Persona. |
| `ai_engine/persona_prompt_buider.py` | Prompt social. Mantiene typo historico `buider`. |
| `ai_engine/persona_engine.py` | Capa 2 social y parser robusto. |
| `ai_engine/oracle_client.py` | Cliente API externa para Oraculo. |
| `ai_engine/oracle_memory.py` | Cache semantico del Oraculo en Chroma. |
| `ai_engine/oracle_engine.py` | Capa 3 Oraculo, normalizacion y cache. |
| `ai_engine/test_llm_fallbacks.py` | Prueba de settings/fallbacks. |
| `ai_engine/test_oracle.py` | Prueba de Oraculo. |
| `ai_engine/test_persona.py` | Prueba de Persona. |
| `ai_engine/test_motor_guardrails.py` | Prueba de guardarrailes motores. |

### `ai_engine/prompts/`

| Archivo | Hace |
|---|---|
| `system_prompt.txt` | Reglas base del sistema cognitivo general. |
| `local_system_prompt.txt` | Reglas para modelos locales, identidad y expresiones. |
| `personality_prompt.txt` | Bloque de rasgos. |
| `estado_prompt.txt` | Bloque emocional. |
| `memoria_prompt.txt` | Bloque de recuerdos. |
| `tool_prompt.txt` | Bloque de herramientas. |
| `format_prompt.txt` | Formato JSON obligatorio del pipeline general. |

### `memory/`

| Archivo | Hace |
|---|---|
| `memory/__init__.py` | Marca paquete. |
| `memory/chroma_client.py` | Wrapper de ChromaDB con RLock process-local, add/query/list/delete/count/close. |
| `memory/memory_types.py` | Tipos de memoria y resultados. |
| `memory/memory_encoder.py` | Convierte eventos/reglas a documentos Chroma. |
| `memory/episodic_memory.py` | Store episodico. |
| `memory/semantic_memory.py` | Store semantico. |
| `memory/memory_retriever.py` | Recuperador hibrido y reranking. |
| `memory/memory_summarizer.py` | Resume recuerdos. |
| `memory/memory_filters.py` | Fabrica filtros Chroma. |
| `memory/memory_decay.py` | Peso temporal/uso. |
| `memory/working_memory.py` | Memoria de trabajo de acciones recientes. |
| `memory/memory_manager.py` | Fachada, escritura, lectura, consolidacion y sueno. |

### `tools/`

| Archivo | Hace |
|---|---|
| `tools/__init__.py` | Marca paquete. |
| `tools/base.py` | `ToolResult`. |
| `tools/registry.py` | Registro global por decoradores. |
| `tools/executor.py` | Ejecuta herramientas localmente cuando aplica. |

#### `tools/simulated/`

| Archivo | Hace |
|---|---|
| `tools/simulated/__init__.py` | Marca paquete. |
| `tools/simulated/movimiento_sim.py` | Registra `navegar_a_objeto`, `navegar_a_posicion`, `alejarse_de_objeto`, `girar_avatar`. |
| `tools/simulated/interaccion_sim.py` | Registra `interactuar_objeto_3d`, `agarrar_objeto`, `soltar_objeto`, `aplicar_fuerza`, `hablar`. |
| `tools/simulated/mouse_sim.py` | Simula mouse. |
| `tools/simulated/keyboard_sim.py` | Simula teclado. |

#### `tools/real/`

| Archivo | Hace |
|---|---|
| `tools/real/__init__.py` | Marca paquete. |
| `tools/real/mouse_real.py` | Control real de mouse con confirmacion. No se importa en arranque normal. |
| `tools/real/keyboard_real.py` | Control real de teclado con confirmacion. No se importa en arranque normal. |

#### `tools/Realidad/`

| Archivo | Hace |
|---|---|
| `tools/Realidad/Heat_live.py` | Herramienta/visualizador auxiliar para datos seriales/ESP32 y mapa vivo. |
| `tools/Realidad/esp_32.ino` | Sketch Arduino/ESP32 asociado. |

### `telescopio/`

| Archivo | Hace |
|---|---|
| `telescopio/__init__.py` | Marca paquete. |
| `telescopio/constelacion_cognitiva.py` | Grafo cognitivo standalone con nodos/enlaces/contexto. |
| `telescopio/visualizar_constelacion.py` | Exporta constelacion y memorias ChromaDB a HTML interactivo. |

### `unity_client/`

| Archivo | Hace |
|---|---|
| `unity_client/DTOs.cs` | Contrato C# compatible con `api/schemas.py`. |
| `unity_client/ApiClient.cs` | Cliente HTTP Unity, timeout configurable, parseo con Newtonsoft. |
| `unity_client/PerceptionSystem.cs` | Escaneo frontal, resumen textual, cambios significativos y busqueda de objetos. |
| `unity_client/MovementController.cs` | NavMesh, alejarse, navegar a posicion y rotar. |
| `unity_client/LazzaroAgent.cs` | Coordinador Unity: cola, latido, eventos, habla, VRM, movimiento y reportes. |

### `docs/`

| Archivo | Hace |
|---|---|
| `docs/registro_pruebas_unity.md` | Bitacora historica de fases, pruebas, bugs y riesgos. |
| `docs/arquitectura_completa_lazzaro.md` | Este documento. |

### Datos Generados

| Ruta | Hace |
|---|---|
| `lazzaro_data/` | Persistencia ChromaDB de experiencias y cache del Oraculo. |
| `constelacion.html` | Salida HTML generada por `telescopio/visualizar_constelacion.py` si se usa esa ruta. |
| `__pycache__/` | Cache Python. |
| `.pytest_cache/` | Cache de pruebas si existe. |
| `.vscode/` | Configuracion local del editor si existe. |

## Herramientas Registradas En Arranque Normal

El arranque actual registra herramientas simuladas:

- `interactuar_objeto_3d`
- `agarrar_objeto`
- `soltar_objeto`
- `aplicar_fuerza`
- `hablar`
- `escribir_texto`
- `presionar_tecla_especial`
- `mover_mouse`
- `click_mouse`
- `navegar_a_objeto`
- `navegar_a_posicion`
- `alejarse_de_objeto`
- `girar_avatar`

`ToolPolicy` filtra por origen/ruta:

- `persona` y `oracle`: solo `hablar`.
- `unity`/`roblox`: herramientas seguras de mundo 3D y voz.
- otros origenes: puede exponer mas herramientas, sujeto al registry y al executor.

## Puntos De Extension

- Agregar proveedor LLM: extender `client_factory.py`, `settings.py` y si aplica `openai_compatible_client.py`.
- Agregar herramienta: crear schema Pydantic y registrar con `ToolRegistry.register(...)`.
- Agregar accion fisica: actualizar `motor_schemas.py`, `motor_engine.py`, `tools/simulated/*` y handlers Unity.
- Mejorar Triage: editar `routing_rules.py` o reemplazar `TriageRouter.route_event(...)`.
- Mejorar Persona: editar `persona_prompt_buider.py` y pruebas en `test_persona.py`.
- Mejorar Oraculo: ajustar `oracle_engine.py`, `oracle_client.py` y `oracle_memory.py`.
- Depurar memoria: revisar `memory_manager.py`, `lazzaro_data/` y `/api/v1/constelacion`.
- Visualizar memoria: usar `telescopio/visualizar_constelacion.py`.

## Pruebas Y Validaciones Utiles

Comandos ligeros:

```powershell
python -m compileall -q ai_engine core memory api tools telescopio config main.py
python ai_engine\test_llm_fallbacks.py
python ai_engine\test_oracle.py
python ai_engine\test_persona.py
python ai_engine\test_motor_guardrails.py
```

Checks manuales en Unity:

- `hola` responde por Persona sin accion fisica.
- `camina hacia la capsule` navega.
- `alejate de la capsule` se aleja.
- `gira 180` rota sin intentar navegar.
- `camina` pide objetivo.
- `hablar` no produce cascada de `resultado_accion`.
- Movimiento terminado no reinicia a "Acabas de despertar".
- Objeto inexistente/lejos reporta fallo, no exito falso.

## Riesgos Y Notas Actuales

- `.env` contiene secretos locales; nunca copiarlo a documentacion compartida.
- `FallbackAIClient` acepta el primer texto no vacio; si ese texto no es JSON
  valido, puede no intentar el siguiente proveedor.
- `orchestrator.procesar_evento()` corre desde threadpool y el orquestador tiene
  estado compartido. Chroma esta protegido por lock process-local, pero el
  orquestador completo no tiene una compuerta global.
- `AI_FALLBACK_ORDER` reemplaza el orden default; si se configura, incluir
  explicitamente el proveedor que se quiera probar primero.
- `persona_prompt_buider.py` conserva typo en el nombre. Renombrarlo requiere
  actualizar imports y pruebas.
- `routing_rules.py` usa keywords y puede clasificar mal frases ambiguas.
- `OracleClient` asume endpoint compatible con `/chat/completions` y puede usar
  `response_format` segun proveedor.
- `tools/real/*` controla mouse/teclado real si se importa. Mantener fuera del
  arranque normal salvo sandbox controlado.
- Unity necesita objetos con tag `Interactuable`, colliders y NavMesh valido.
- La cola Unity aun puede no separar subtareas si el usuario usa solo coma como
  separador.
- `lazzaro_data/` es estado persistente. Borrarlo cambia memoria y cache.

## Como Explicarlo A Un Companero

1. Unity no decide inteligencia; percibe, ejecuta y reporta.
2. Python no mueve directamente el avatar; devuelve acciones estructuradas.
3. `core/orchestrator.py` es el mapa real del flujo.
4. `ai_engine/triage_router.py` decide la ruta.
5. `ai_engine/motor_engine.py` evita alucinaciones fisicas.
6. `ai_engine/persona_engine.py` protege la conversacion.
7. `ai_engine/oracle_engine.py` protege consultas complejas con cache.
8. `memory/memory_manager.py` conserva eventos y compacta observaciones viejas.
9. `unity_client/LazzaroAgent.cs` muestra como Unity ejecuta la respuesta.
10. `docs/registro_pruebas_unity.md` explica por que existen los guardarrailes.
