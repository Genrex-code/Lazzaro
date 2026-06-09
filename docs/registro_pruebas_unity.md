# Registro De Pruebas Unity-Python

## 2026-06-09 - Verificacion Post-Limpieza De Mojibake

### Objetivo

- Confirmar que la limpieza manual de comentarios/simbolos no rompio el flujo Persona/Motor.
- Corregir bloqueos de import detectados antes de nuevas pruebas Unity.

### Cambios Aplicados

- `memory/chroma_client.py` tenia una linea incompleta `from chromadb.api.shared_system_client`; se elimino porque el bloque `try` de compatibilidad ya importa `SharedSystemClient`.

### Pruebas Ejecutadas

- Compilacion dirigida de `memory/chroma_client.py`, Persona, Motor, Orchestrator y herramientas simuladas.
- `python ai_engine/test_persona.py`.
- `python ai_engine/test_motor_guardrails.py`.
- `python -m compileall -q ai_engine core memory api tools telescopio config main.py`.
- Prueba de import: `core.orchestrator` carga correctamente y `ToolRegistry` lista herramientas Unity activas.
- `git diff --check` sin errores; solo warning CRLF esperado por Windows.

### Riesgos Pendientes

- Validar en Unity Play Mode que las respuestas de aclaracion motor se muestren como `hablar` sin disparar cascadas.
- La limpieza de mojibake visual en comentarios/documentos puede continuar como higiene, pero no bloqueo las pruebas Python.

## 2026-06-08 - Estabilizacion Persona/Motor

### Objetivo

- Reducir fallos de Persona cuando Ollama/DeepSeek devuelve texto vacio, JSON sucio o JSON truncado.
- Evitar que comandos motores incompletos caigan al razonador viejo e inventen objetivos fisicos.
- Mantener intacto el contrato Unity-Python y dejar el rediseño de Triage para una fase futura.

### Cambios Aplicados

- `PersonaEngine.parsear_respuesta(...)` ahora intenta rescatar `texto_a_comunicar` desde JSON parcial antes de activar fallback.
- Las respuestas sociales invalidas pueden marcar `requiere_reintento` para activar un unico reintento corto.
- `Orchestrator._resolver_ruta_persona(...)` ejecuta ese reintento con temperatura baja y limite de tokens reducido.
- `MotorEngine` agrega diagnostico local para distinguir comandos listos, sin intencion, no permitidos o con objetivo faltante.
- `Orchestrator._resolver_ruta_motor(...)` responde con `hablar` pidiendo aclaracion cuando falta el objeto objetivo.

### Pruebas Ejecutadas

- Prueba social con JSON envuelto en Markdown: conserva texto y fuerza `hablar`.
- Prueba social con respuesta vacia: marca reintento.
- Prueba social con JSON truncado: rescata `texto_a_comunicar` sin gastar reintento.
- Prueba social con fake LLM: un fallo inicial dispara exactamente un reintento y usa la respuesta reparada.
- Prueba motor: `camina al cubo` sigue resolviendo `navegar_a_objeto`.
- Prueba motor: `camina` y `camina hacia el` piden aclaracion sin llamar al `ReasoningEngine`.

### Riesgos Pendientes

- Las variantes motoras amplias como `caminar` siguen fuera de esta tanda.
- El rediseño futuro de Triage debe absorber frases mixtas social/motor sin depender de parches locales.
- Validar en Unity Play Mode que la aclaracion `hablar` no se reporte como resultado de accion.

## 2026-06-03 - Fase 4: Capa Oraculo

### Objetivo

- Separar consultas tecnicas, logicas y filosoficas del razonamiento general.
- Reutilizar respuestas complejas mediante cache semantico.
- Usar API externa cuando este disponible y Ollama local como fallback.

### Cambios Aplicados

- `Orchestrator` instancia `OracleEngine`, `OracleMemory` y `OracleClient`.
- La ruta `oracle` consulta primero `OracleMemory` y evita llamadas LLM en cache HIT.
- En cache MISS, la consulta intenta el cliente externo y usa el cliente Ollama activo si no hay respuesta.
- La salida cruda siempre pasa por `OracleEngine.keep_y_save(...)` antes de convertirse en una accion `hablar`.
- Los errores finales de Capa 3 quedan registrados en `ActionResponse` para que `IdentityState` aumente el estres.
- Se corrigio la inicializacion del logger de `OracleMemory`.
- `OracleEngine` ahora rechaza JSON con forma invalida, parametros incorrectos o texto vacio.
- `Settings` declara `ORACLE_API_URL`, `ORACLE_API_KEY`, `ORACLE_MODEL` y `ORACLE_TIMEOUT` para cargar la API externa desde `.env`.

### Pruebas Ejecutadas

- Compilacion completa de `ai_engine`, `core`, `api` y `tools`.
- Prueba de ruta `oracle` con cache HIT: no llama al cliente externo ni a Ollama.
- Prueba de ruta `oracle` con API externa exitosa: no llama a Ollama y guarda conocimiento.
- Prueba de ruta `oracle` sin API key: usa Ollama local con los mismos mensajes.
- Prueba de fallo final: conserva una respuesta `hablar` segura y aumenta el estres de `IdentityState`.
- Prueba de API externa fallida con Ollama exitoso: responde localmente y aun registra el estres del fallo.
- Prueba real de ChromaDB: memoria episodica y `oracle_cache` pueden compartir la misma ruta.
- Prueba de respuestas invalidas: JSON no-objeto, parametros incorrectos y texto vacio activan fallback.
- Prueba de configuracion: `Settings` entrega `ORACLE_API_KEY`, modelo y timeout al cliente externo.

### Riesgos Pendientes

- El fallback local requiere que el cliente LLM activo sea Ollama.
- Validar con el proveedor externo real que soporte `response_format={"type": "json_object"}`.
- Ajustar el umbral de similitud del cache con preguntas reales para evitar respuestas demasiado generales.

## 2026-06-02 - Fase 3: Capa Social / Identidad

### Objetivo

- Separar conversacion social de razonamiento general y herramientas fisicas.
- Inyectar estado biologico/emocional en respuestas sociales.
- Registrar gasto de energia/enfoque segun la ruta cognitiva usada.

### Cambios Aplicados

- `Orchestrator` instancia `IdentityState` y `PersonaEngine`.
- Ruta `persona` usa `PersonaEngine.preparar_interaccion(...)`.
- El LLM activo se llama con el prompt social y la respuesta pasa por `PersonaEngine.parsear_respuesta(...)`.
- La accion social final se fuerza a `hablar`.
- `IdentityState.registrar_evento(...)` se ejecuta al cierre del procesamiento no cacheado.
- Se evito forzar el schema global `LLMResponse` en Capa Social para no chocar con `pensamiento_privado`.

### Pruebas Ejecutadas

- Compilacion Python de `identity_state.py`, `persona_engine.py`, `persona_prompt_buider.py` y `orchestrator.py`.
- Prueba con fakes: ruta `persona` llama al cliente LLM y no a `ReasoningEngine.process`.
- Prueba con fakes: ruta `motor` sigue usando Capa 1 sin LLM cuando puede resolver localmente.
- Prueba con fakes: `actualizacion_estado` sigue devolviendo `none` sin LLM.
- Prueba con fakes: `IdentityState` reduce energia en ruta social y motor.

### Riesgos Pendientes

- El archivo `persona_prompt_buider.py` conserva el nombre escrito como `buider`; funciona porque `persona_engine.py` lo importa asi, pero conviene renombrarlo en una limpieza futura.
- Validar con DeepSeek real que el JSON social sea estable sin schema forzado.

## 2026-06-01 - Fila De Tareas En Unity

### Objetivo

- Encadenar comandos del usuario con conectores como `luego`, `despues`, `después`, `y luego` y `finalmente`.
- Ejecutar una subtarea a la vez y esperar a que termine antes de enviar la siguiente al backend.
- Cancelar la secuencia si una subtarea falla o si ocurre dolor fisico.

### Cambios Aplicados

- `LazzaroAgent` agrega `colaTareasUsuario` separada de `colaEventos`.
- `EnviarComandoUsuario()` divide comandos largos en subtareas.
- Subtareas sin verbo fisico heredan el contexto anterior, por ejemplo `camina hacia la esfera luego hacia el cubo`.
- `ReportarResultadoAccion()` completa subtareas fisicas.
- `hablar` completa subtareas conversacionales sin reportar resultado al backend.
- `alerta_dolor_fisico` cancela cualquier fila pendiente.

### Riesgos Pendientes

- Validar en Unity Play Mode que el orden de eventos quede como `resultado_accion` antes del siguiente `comando_usuario`.
- Si el usuario quiere interrumpir un movimiento ya iniciado, todavia falta una accion explicita de cancelar movimiento.

## 2026-06-01 - Percepcion Con Campo Visual Frontal

### Objetivo

- Hacer que Lazzaro perciba solo objetos dentro de un cono de vision frontal.
- Evitar que el `Plane` aparezca como objeto relevante en la percepcion.
- Mantener compatible el resumen textual con el motor local de Fase 2.

### Cambios Aplicados

- `PerceptionSystem` agrega `anguloVision` configurable desde Inspector.
- `ObtenerObjetosInteractuables()` filtra objetos fuera del cono frontal.
- `BuscarObjeto()` ahora respeta el campo visual filtrado.
- El resumen perceptivo usa `Frente a mi veo: ...`.
- `SpatialReasoner` reconoce el nuevo prefijo para seguir extrayendo nombres de objetos.

### Pruebas Ejecutadas

- Compilacion Python de los modulos afectados.
- Prueba del parser espacial con `Frente a mi veo: Capsule..., Sphere..., Cube...`.
- Prueba de `MotorEngine`: `camina hacia el cubo` sigue resolviendo `Cube`.
- Prueba de `MotorEngine`: `inspecciona lo que tengas enfrente` sigue usando el objeto mas cercano.

### Riesgos Pendientes

- Validar en Unity Play Mode que el cono use el frente correcto del avatar.
- Ajustar `anguloVision` desde Inspector si Lazzaro queda demasiado ciega o demasiado amplia.

## 2026-06-01 - Fase 2: Cerebro Motor Local

### Objetivo

- Separar la validacion de acciones fisicas en una Capa 1 sin personalidad ni conversacion.
- Resolver comandos motores simples sin llamar al LLM cuando el objetivo sea claro.
- Validar las acciones motoras propuestas por el LLM antes de enviarlas a Unity.

### Cambios Aplicados

- Se agregaron `motor_engine.py`, `motor_schemas.py` y `spatial_reasoner.py`.
- `Orchestrator` instancia `MotorEngine` y lo usa solo cuando el Triage devuelve ruta `motor`.
- Comandos como `camina hacia el cubo`, `moverte hacia la sphere`, `inspecciona lo que tengas enfrente` y `alejate de la esfera` pueden resolverse por reglas locales.
- Si la ruta motor aun necesita LLM, la accion resultante pasa por validacion estricta de Capa 1.
- Se evito que nombres de objetos por si solos (`cubo`, `esfera`, `capsule`) disparen ruta motora.

### Pruebas Ejecutadas

- Prueba directa de `MotorEngine` con percepcion textual comprimida de Unity.
- Prueba de `Orchestrator` con fakes: motor determinista no llama al LLM.
- Prueba de `Orchestrator` con fakes: motor ambiguo llama al LLM y luego valida la accion.
- Prueba de regresion: `reconoces el cubo?` queda en ruta persona, no motor.
- Prueba de regresion: `actualizacion_estado` sigue devolviendo `none` sin LLM.
- Compilacion Python con `compileall` en `ai_engine`, `core`, `api` y `tools`.
- Revision `git diff --check` sin errores; solo warnings CRLF esperados en Windows.

### Riesgos Pendientes

- Unity debe validar en Play Mode que parametros extra opcionales no afecten handlers C#.
- Los comandos motores multi-paso siguen ejecutando solo el primer objetivo claro; la cola de tareas queda para una fase posterior.
- El motor local usa reglas heuristicas; conviene ampliar con ejemplos reales cuando haya mas logs.

## 2026-05-30 - Estabilizacion De Bucle Cognitivo

### Sintomas Observados

- `actualizacion_estado` reiniciaba la tarea a `Acabas de despertar quedate quieta y espera instrucciones`.
- La accion `hablar` generaba `resultado_accion`, lo que provocaba cascadas de nuevas respuestas del LLM.
- Mensajes conversacionales o narrativos podian terminar en acciones fisicas como `navegar_a_objeto` o `agarrar_objeto`.
- Unity reportaba exito al interactuar o agarrar aunque el objeto no estuviera validado como visible/cercano.

### Causa Raiz

- El manual espacial se reenviaba como `mensaje` en latidos pasivos.
- El orquestador trataba `resultado_accion` y `actualizacion_estado` como eventos que requerian decision.
- No habia separacion clara entre intencion conversacional e intencion fisica.
- Las acciones fisicas en Unity no validaban distancia antes de reportar exito.

### Cambios Aplicados

- `actualizacion_estado` queda como telemetria pasiva y no reinicia tareas.
- `hablar` es salida pasiva en Unity: actualiza UI/log, pero no reporta resultado al backend.
- `ProcesarRespuestaDelCerebro` valida respuesta nula antes de leer campos.
- `resultado_accion` y `actualizacion_estado` no llaman al LLM salvo `solicita_decision=true`.
- Resultados legacy de `hablar` se degradan a `none` sin razonamiento.
- Se agrego filtrado de herramientas por intencion: conversacion normal usa solo `hablar`.
- Se agrego `alejarse_de_objeto(nombre_objeto, distancia)` en backend y Unity.
- `agarrar_objeto` e `interactuar_objeto_3d` validan objeto visible y distancia.
- `interactuar_objeto_3d` quedo registrado en backend para coincidir con el handler de Unity.

### Pruebas Esperadas

- Un latido tras movimiento no debe cambiar la tarea actual a la instruccion de despertar.
- Una respuesta `hablar` no debe producir un nuevo `resultado_accion`.
- Una historia o pregunta personal debe responder con `hablar` o `none`, no con navegacion.
- Una orden como `alejate de la sphere` debe usar `alejarse_de_objeto`.
- Intentar agarrar/interactuar con un objeto lejano debe reportar fallo.

### Pruebas Ejecutadas

- Compilacion Python con `py_compile`: 50 archivos compilados.
- Prueba de flujo directo: `actualizacion_estado` pasivo no llama al LLM ni reinicia tarea.
- Prueba de compatibilidad legacy: `resultado_accion` de `hablar` responde `none` sin llamar al LLM.
- Prueba de intencion conversacional: historia de `pelotita` solo expone `hablar`.
- Prueba de intencion fisica: `sin interactuar` filtra herramientas de interaccion.
- Prueba FastAPI con Chroma temporal: `/estado` y `/evento` funcionan con telemetria pasiva y comando activo.

### Riesgos Pendientes

- El compilado C# debe validarse dentro de Unity.
- La clasificacion de intencion usa heuristicas simples; puede necesitar ajuste con mas logs reales.

## 2026-06-07 - Bug Detectado Durante Bugfix Memoria

### Bug Registrado

- `unity_client/ApiClient.cs` mantenia `request.timeout = 10`.
- El backend local con Ollama/DeepSeek puede tardar mas de 10 segundos en Persona u Oraculo.
- Si Unity corta antes que Python termine, pueden aparecer respuestas tardias, tareas desincronizadas o falsos fallos.

### Decision

- No se corrigio en la tanda de memoria para mantener ese bugfix enfocado.
- Se atendio en la ronda de estabilidad Unity/API registrada abajo.

## 2026-06-07 - Bugfix Unity Timeout Y Triage Motor

### Cambios Aplicados

- `unity_client/ApiClient.cs` reemplazo el timeout fijo de 10 segundos por `TimeoutSegundos = 90`, configurable en Inspector.
- El timeout queda limitado entre 15 y 180 segundos para evitar esperas infinitas o cortes demasiado agresivos.
- `ai_engine/routing_rules.py` normaliza acentos antes de comparar keywords.
- El Triage reconoce variantes motoras como `alejate`, `alejarme`, `interactua`, `mirar la capsula` y `observa la capsule`.
- `ai_engine/motor_engine.py` resuelve `alejarme de la capsula` y `observa la capsule` sin depender del LLM.
- `ai_engine/triage_router.py` elimino una doble evaluacion redundante de intencion.

### Pruebas Ejecutadas

- `alejate de la capsula`, `alejarme de la capsula`, `interactua con la capsula`, `puedes mirar la capsula` y `observa la capsule` enrutan a `motor`.
- `mira creo que esto esta mejor`, `hola` y `reconoces el cubo?` no se convierten en motor accidentalmente.
- La compuerta `is_moving` sigue devolviendo `noop` para comandos motores mientras Lazzaro se mueve.
- `MotorEngine` genera acciones deterministas para alejarse, interactuar y mirar objetos visibles.

## 2026-06-07 - Retiro De `moverse_entorno_3d` E Higiene

### Cambios Aplicados

- `moverse_entorno_3d` se retiro de las listas internas de acciones de movimiento/exploracion.
- El fallback de dolor fisico ya no intenta usar una herramienta no registrada.
- Si hay posicion de Lazzaro, la interrupcion critica sigue usando `navegar_a_posicion`.
- Si no hay posicion pero hay objeto involucrado, la interrupcion critica usa `alejarse_de_objeto`.
- `.gitignore` se amplio para caches Python, caches de pruebas y datos runtime futuros.
- `telescopio/visualizar_constelacion.py` queda documentado como exportador standalone de `constelacion.html`.

### Pruebas Esperadas

- El registro de herramientas no debe contener `moverse_entorno_3d`.
- Una alerta de dolor con posicion debe devolver `navegar_a_posicion`.
- Una alerta de dolor sin posicion pero con objeto debe devolver `alejarse_de_objeto`.
- `ToolPolicy` para Unity no debe exponer herramientas obsoletas.
- `telescopio/visualizar_constelacion.py` debe compilar/importar sin depender de integracion extra.

### Pruebas Ejecutadas

- Escaneo `rg`: no quedan referencias runtime a `moverse_entorno_3d`; solo esta bitacora lo menciona historicamente.
- `ToolRegistry.listar_nombres()` no contiene `moverse_entorno_3d`.
- `ToolPolicy` para `unity/motor` filtra cualquier `moverse_entorno_3d` accidental.
- `_resolver_interrupcion_critica` devuelve `navegar_a_posicion` cuando hay posicion.
- `_resolver_interrupcion_critica` devuelve `alejarse_de_objeto` cuando no hay posicion pero si objeto involucrado.
- `python -m py_compile` paso para `telescopio`, `core/orchestrator.py`, `core/behavior_core.py`, `core/task_context.py` y `core/tool_policy.py`.
