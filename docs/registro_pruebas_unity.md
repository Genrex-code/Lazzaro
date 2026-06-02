# Registro De Pruebas Unity-Python

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
