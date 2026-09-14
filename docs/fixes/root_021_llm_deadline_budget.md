# ROOT-021 — Deadline y retry budget compartido para LLM

Estado: `IMPLEMENTED / REVIEW_FIX_READY_FOR_HUMAN_REVIEW`.

## Problema histórico

Las llamadas LLM no compartían un límite total. Gemini poseía cuatro intentos internos, con backoff y un `sleep` incluso después del último intento; `FallbackAIClient` tenía otro bucle de proveedores; Oracle hacía primero su llamada externa y después reiniciaba el flujo con el proveedor activo. Los timeouts HTTP eran límites de fase/transporte, no un deadline total: una respuesta lenta por fragmentos, varios retries o varios stages podían superar el sobre de tiempo del turno.

## Decisión HARD-A

La aplicación continúa síncrona en Orchestrator y FastAPI conserva `run_in_threadpool`. Sólo transporte y control LLM forman una isla async. `LLMCallExecutorV1` expone `execute_async()` y un bridge `execute_sync()` que usa `asyncio.run()` únicamente cuando el hilo no tiene loop activo. Desde un loop activo el bridge falla de forma estable e indica usar la API async; no hay `nest_asyncio`, helper thread ni task desprendido. El lifespan principal espera `health_check_async()` directamente.

## Contrato de budget

`LLMCallBudgetV1` es frozen/slotted y contiene sólo `deadline_ns`, un deadline absoluto creado con `time.perf_counter_ns()`. No existe contador mutable. `remaining_ns()` y `remaining_seconds()` vuelven a leer el reloj. `derive_cap()` produce un hijo con:

```text
min(parent.deadline_ns, now_ns + provider_cap_ns)
```

El padre nunca cambia. Cada sibling deriva desde el padre original usando el `now` actual: si A consume 15 s de una ruta de 45 s, B ve aproximadamente 30 s; si A consume 40 s, B ve aproximadamente 5 s, nunca cero heredado de A ni 45 s nuevos.

El budget total configurado es `LLM_TOTAL_BUDGET_SECONDS=45.0`. Caps actuales:

- Gemini/OpenAI-compatible: 45 s como máximo;
- Oracle externo: 15 s;
- Ollama diagnóstico/health: 120 s, reducido siempre por la ruta.

Settings y el boundary de control rechazan `bool`, NaN, infinito, cero y negativos. La construcción directa de Oracle aplica la misma defensa aunque no use Settings.

## Un solo scheduler

`LLMCallExecutorV1` es la única autoridad para orden de proveedores, retry del mismo proveedor, attempt cap, Retry-After, backoff, jitter, sleep, fallback, child cap, deadline y terminal final. `FallbackAIClient` es una fachada: conserva providers/configuración, compatibilidad textual, `last_provider_used`, captura de audit y close lógico, pero no contiene bucles de intentos o proveedores. La factory devuelve esta fachada incluso con un único proveedor.

El contrato productivo es estrictamente async: todo provider debe implementar
`attempt_async(...)`. `BaseAIClient` falla cerrado con `ProviderConfigurationError`
si una subclase no lo implementa, y el executor nunca llama como alternativa a
`generate`, `generate_plan`, `health_check` o `consultar_plan`. Tampoco traslada
I/O síncrono a threads. Los caminos de clientes síncronos usados por dobles
históricos de Gemini/OpenAI-compatible fueron retirados; los tests usan transports
async inyectados.

Cada proveedor concreto proyecta la entrada y ejecuta exactamente un I/O. No reintenta, no duerme, no recorre fallbacks y no crea un budget de ruta. Gemini declara cuatro intentos totales —incluido el inicial— para que el executor reproduzca la política 1/2/4 s; los otros perfiles actuales conservan un intento.

## Deadline y cancelación

El executor instala un `asyncio.timeout(remaining)` exterior para la ruta y otro límite con el remanente del child de proveedor. Un vencimiento del límite total produce `DEADLINE_EXCEEDED`, detiene retries/fallback y nunca acepta texto tardío. Un timeout de transporte/cap de proveedor antes del deadline se clasifica como `TRANSPORT_TIMEOUT` y puede permitir retry o sibling. Además de la cancelación, una respuesta sólo se acepta si `finished_ns < route.deadline_ns`.

Sólo errores derivados de `Exception` se convierten en fallos de provider.
`asyncio.CancelledError`, `KeyboardInterrupt`, `SystemExit` y otros controles de
proceso no se convierten en receipts, no se reintentan y no activan siblings.

La garantía es local: al vencer el deadline, Lazzaro cancela su operación async, propaga la cancelación al transporte, cierra el transporte que posee, no deja worker/task desprendido y no acepta un resultado posterior. Esto no obliga al servidor remoto a detener inmediatamente el cómputo ni promete preempción exacta del sistema operativo en el nanosegundo 45.000000.

Los `AsyncClient` se crean dentro del loop que ejecuta cada tentativa y se cierran antes de salir. Ninguno se conserva en el objeto provider ni se reutiliza entre dos `asyncio.run()`. `close()` sigue siendo idempotente y no abre ROOT-022.

## Gemini

Gemini usa `client.aio.models.generate_content` con un `httpx.AsyncClient` caller-owned por tentativa. `HttpOptions.httpx_async_client` fuerza el path HTTPX, evitando el retry de conexión oculto del path aiohttp instalado. `HttpRetryOptions(attempts=1)` deja al SDK en exactamente una llamada de transporte por tentativa del executor. Tanto el SDK async como el cliente HTTPX explícito se cierran en `finally`.

El remanente se convierte con floor a milisegundos. Con menos de 1 ms no se despacha; desde 1 ms, el timeout es positivo y nunca supera el remanente expresado en unidades del SDK.

## Taxonomía y policy

`LLMFailureClassV1` contiene:

```text
TRANSPORT_TIMEOUT, CONNECTION_FAILURE, RATE_LIMITED, TRANSIENT_HTTP,
NON_RETRYABLE_CLIENT, AUTH_FAILURE, MALFORMED_PROVIDER_RESPONSE,
EMPTY_RESPONSE, PROJECTION_UNSUPPORTED, CONFIGURATION_ERROR,
UNKNOWN_FAILURE, DEADLINE_EXCEEDED
```

Retry en el mismo proveedor: timeout de transporte, conexión transitoria, HTTP 429, 425, 500, 502, 503 y 504, siempre sujeto a attempts y remanente. No hay retry local para 400/401/403/404/409, respuesta vacía, envelope malformado, configuración, fallo desconocido o proyección incompatible. Cualquier fallo específico del proveedor puede permitir el siguiente proveedor mientras quede ruta.

`EMPTY_RESPONSE` representa transporte exitoso sin texto. `MALFORMED_PROVIDER_RESPONSE` representa un envelope HTTP/SDK inválido. Ninguno se confunde con JSON cognitivo inválido, que permanece bajo ROOT-020/ROOT-008.

Una proyección incompatible se resuelve antes de HTTP: no produce receipt/audit de intento, no altera salud y no cuenta como proveedor compatible. Ollama cognitivo continúa bloqueado por ROOT-019.

## Retry-After y backoff

Retry-After sólo se obtiene de headers tipados o campos estructurados. Se aceptan delta-seconds finitos no negativos y HTTP-date; el reloj de pared inyectable sólo convierte la fecha a un retraso relativo. El deadline siempre sigue siendo monotónico. Nunca se extrae un retraso del texto libre de una excepción.

El backoff determinista de Gemini es 1, 2 y 4 s antes de intentos 2, 3 y 4. Sólo se duerme si habrá otro intento; nunca después del último. Si el delay es mayor o igual al remanente del provider/ruta, no se duerme hasta el vencimiento: se intenta un sibling si todavía corresponde o se terminaliza.

## Composición de rutas

- Persona crea un budget inmediatamente antes del primary; el repair semántico usa el mismo objeto y sólo su remanente. Identidad, memoria, percepción y grounding no se recapturan.
- Reasoning y Motor→Reasoning entran por la fachada y reciben un único budget finito.
- Oracle cache miss usa una única cadena controlada `(oracle_external, *active.providers)`; external, fallback técnico, verbalización Persona y repair comparten el deadline. Un cache hit crea el budget inmediatamente antes de verbalizar.
- Health usa 45 s totales compartidos por sus providers. ROOT-023 no cambia sus estados de readiness.
- El `generate(str)` diagnóstico legacy sigue permitido sólo como ruta no cognitiva y ahora siempre recibe el mismo límite total.

Solicitar una reparación no equivale a haberla ejecutado. Persona y la
verbalización Oracle consumen el resultado tipado de la reparación y sólo marcan
`DecisionAttempt.REPAIR`/`retry_count=1` cuando existe al menos un receipt de
provider. Si el deadline compartido expira antes del dispatch, hay cero llamadas,
cero receipts y `DecisionOutcome` permanece en `FIRST`/`retry_count=0`.

## Resultado y precedencia

`LLMCallResultV1` contiene terminal, texto final aceptado, proveedor/modelo final, failure final y receipts. Precedencia:

1. texto aceptado antes del deadline → `SUCCESS`;
2. deadline antes de aceptar texto → `DEADLINE_EXCEEDED`;
3. ningún proveedor compatible → `NO_COMPATIBLE_PROVIDER`;
4. un compatible: failure retryable agotado → `RETRY_EXHAUSTED`; de otro modo → `NON_RETRYABLE_FAILURE`;
5. dos o más compatibles intentados sin éxito → `ALL_PROVIDERS_FAILED`.

`ProviderAttemptReceiptV1` sólo conserva provider, model id, failure class, error type estable, status HTTP, Retry-After relativo y timestamps monotónicos. No contiene prompt, contexto, request, respuesta, body, URL, API key ni mensaje libre de excepción. El texto exitoso sólo aparece en `LLMCallResultV1.final_text`.

## Observabilidad y fronteras congeladas

Se conserva un `LLMCallAudit` por tentativa real, sin duplicados. Su duración es monotónica, no negativa y excluye backoff. El schema `LLMCallAudit` y AuditTrace v1 no cambian. Tampoco cambian PromptPlan, DTOs API/Unity ni metadata pública terminal.

ROOT-018 podrá consumir el resultado terminal tipado, pero no se inicia aquí: no se añade KnowledgeReceipt ni se rediseña cache. ROOT-023 también permanece separado. No se inicia ROOT-053 singleflight, ROOT-017 renderer ni ROOT-022 lifecycle.

## Verificación

`ai_engine/test_llm_call_control.py` contiene 107 casos sin red ni LLM real: validación, sibling canaries, taxonomy, retry/fallback, Retry-After, privacidad, terminales, dos loops sync, rechazo de loop activo, cancelación/finalización, cero tasks pendientes, path Gemini HTTPX, SDK attempts=1, conversión a ms, factory/fachada, contrato async fail-closed, propagación de controles de proceso y verdad del repair Persona/Oracle. Las regresiones ROOT-019/016/008/020/012/009/010 y la suite global siguen siendo gates de cierre.
