# Registro De Pruebas Unity-Python

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
