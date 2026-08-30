# Auditoría de errores, fallbacks y duración en Orchestrator — OBS-007

Estado: **implementado y verificado.**

OBS-007 vincula errores, fallbacks y tiempos con la misma transacción y el mismo
`trace_id` de los stages anteriores. La telemetría observa las llamadas que el
flujo funcional ya ejecuta: no repite `generate()`, consultas Oracle, decisiones
ni ejecución para reconstruir lo ocurrido.

Implementación:

- [`ai_engine/ai_client.py`](../../ai_engine/ai_client.py)
- [`ai_engine/fallback_ai_client.py`](../../ai_engine/fallback_ai_client.py)
- [`ai_engine/gemini_client.py`](../../ai_engine/gemini_client.py)
- [`ai_engine/openai_compatible_client.py`](../../ai_engine/openai_compatible_client.py)
- [`ai_engine/ollama_client.py`](../../ai_engine/ollama_client.py)
- [`ai_engine/oracle_client.py`](../../ai_engine/oracle_client.py)
- [`core/orchestrator.py`](../../core/orchestrator.py)

## Captura sobre las mismas llamadas

Los clientes LLM exponen un buffer acotado al turno y aislado por thread. Al
comenzar `procesar_evento`, `Orchestrator` abre la captura en el cliente activo y
en `OracleClient`; al terminar la decisión consume los eventos producidos por
esas mismas llamadas.

```text
begin_audit_capture()
    ↓
llamada funcional existente
    ├──→ intento primario
    ├──→ intento de fallback, solo si el flujo ya lo requería
    └──→ resultado funcional sin cambios
    ↓
consume_audit_events()
    ↓
stages error + resumen decision.llm
```

Cada intento conserva internamente `provider`, `model_id`, `fallback_used`,
`status`, `error_type`, `duration_ms` y un orden monotónico de inicio. `status`
puede ser `ok`, `empty`, `timeout` o `error`. Estos objetos internos no guardan
prompts, mensajes, respuestas ni detalles de excepciones.

Un intento con estado distinto de `ok` produce su propio `stage=error`. Por ello
dos proveedores fallidos pueden originar dos registros correlacionados, sin
colapsar hechos distintos ni volver a invocar ninguno. Dentro de
`FallbackAIClient`, los clientes inyectados que todavía no implementan el buffer
se observan alrededor de su misma llamada; esa compatibilidad tampoco ejecuta
trabajo adicional.

La captura y su consumo son tolerantes a fallos. Si un cliente no puede aportar
telemetría, se emite un warning humano y el turno continúa con su semántica
funcional original.

## Contrato seguro de error

El objeto persistido en `error` tiene exactamente dos campos estables:

```json
{
  "stage": "error",
  "llm": {
    "provider": "gemini",
    "model_id": "modelo-configurado",
    "fallback_used": false
  },
  "durations_ms": {
    "llm": 125.4
  },
  "error": {
    "type": "TimeoutError",
    "component": "llm"
  }
}
```

`error.type` procede de un nombre de clase estable; nunca se deriva del mensaje
de la excepción. `error.component` identifica la frontera que observó el fallo,
por ejemplo `llm`, `behavior_core`, `decision`, `execution` u `orchestrator`.

AuditTrace no recibe mensajes de excepción, traceback, prompt, URL, respuesta
HTTP o LLM, parámetros ni secretos. Los errores funcionales libres que pueda
contener un `ActionResponse` tampoco se copian al JSONL.

Un resultado interno de ejecución `error` se representa en el stage funcional
como `execution.status=failed` y
`execution.error_type=ExecutionFailed`. A continuación se emite un
`stage=error` con `component=execution`; no se persisten los detalles libres de
la ejecución.

El fallo del propio writer queda exclusivamente en el logger humano. Nunca
genera otro `stage=error`, porque hacerlo abriría la recursión
writer → error trace → writer.

## Proveedor final y semántica de fallback

`decision.llm` resume el conjunto observado de intentos:

- `provider` y `model_id` pertenecen a la última llamada exitosa más cercana a
  la respuesta final;
- `fallback_used=true` si hubo sustitución de proveedor, fallback activo de
  Oracle, retry/rescate de Persona o rescate determinista después de que todos
  los intentos observados fallaran;
- si no hubo una llamada exitosa, `provider` y `model_id` permanecen en `null`;
- cada intento fallido conserva su propio proveedor, modelo, flag de fallback y
  duración en su `stage=error`.

Oracle distingue política de fallo:

| Situación | Auditoría |
|---|---|
| Oracle externo sin credenciales | fallback configurado; la ausencia de credencial no crea un error |
| Oracle externo configurado pero fallido | `stage=error` del intento y fallback activo |
| resultado servido por cache semántico | fuente cache; el hit no crea un error |
| fallback activo completado por el cliente principal | `decision.llm.fallback_used=true` |

El proveedor externo se identifica de forma estable como `oracle_external`; no
se infiere una marca concreta desde la URL o la configuración.

Una salida de Persona que exige reintento, contiene error de parseo o se rescata
desde JSON parcial registra `InvalidLLMResponse` y marca fallback. La respuesta
cruda que originó la recuperación nunca entra a AuditTrace.

Reasoning aplica la misma distinción: una respuesta transportada con éxito pero
rechazada por el parser queda como `InvalidLLMResponse`, vinculada al proveedor,
y no como un `DecisionError` genérico. Las excepciones internas de Reasoning o
Persona conservan el nombre de clase y su componente real. Si Oracle recibe una
respuesta externa sin conocimiento utilizable, registra también
`InvalidLLMResponse`, conserva el proveedor que la produjo y marca el rescate
frío como fallback.

AuditTrace v1 posee un solo objeto `llm`. En una ruta Oracle pueden intervenir
el proveedor de conocimiento y la verbalización posterior de Persona; por eso
`decision.llm` representa únicamente la última llamada exitosa. La cadena de
fallos se conserva mediante los stages `error`, pero un historial completo de
múltiples éxitos requeriría una ampliación explícita del contrato en otra
versión.

Para no atribuir un error temprano al modelo que intervino después, cada error
de contrato conserva un límite monotónico interno: su `llm` se resume solo con
los intentos que ya habían ocurrido cuando se detectó. Así, una respuesta Oracle
inválida puede quedar vinculada a `oracle_external`, mientras `decision.llm`
atribuye correctamente la voz final al proveedor posterior de Persona.

## Duraciones monotónicas

Los relojes usan `time.perf_counter_ns()` y se convierten a milisegundos. Cada
reloj se detiene antes de invocar el writer del stage que lo informa, de modo que
el append de ese registro no infla su propia medición.

`event_end.total` descuenta además el tiempo invertido en construir/escribir
traces y en preparar/consumir la captura LLM. El acumulador es thread-local y
usa una pila por turno, por lo que tampoco mezcla eventos concurrentes o
reentrantes.

| Stage | Claves de `durations_ms` |
|---|---|
| `triage` | `triage` |
| `memory` | `memory` |
| `decision` | `decision` y, si hubo intentos observados, suma `llm` |
| `execution` | `execution` |
| `event_end` | `total` |
| `error` de un intento LLM | `llm` del intento fallido |

Un error temprano de `BehaviorCore` puede incluir además su tiempo de frontera
como `behavior_core`. Todas las duraciones son metadata diagnóstica: no cambian
timeouts, retries ni decisiones del runtime.

## Secuencias observables

Todos los registros de un turno reutilizan el mismo `trace_id`.

```text
Turno normal
event_start → triage → memory → decision → execution → event_end

Proveedor primario fallido con fallback exitoso
event_start → triage → memory → error(llm primario)
            → decision(fallback_used=true) → execution → event_end

Salida Persona inválida recuperada
event_start → triage → memory → error(InvalidLLMResponse)
            → decision(fallback_used=true) → execution → event_end

Fallo de ejecución
event_start → triage → memory → decision → execution(failed)
            → error(ExecutionFailed) → event_end

Fallo previo de BehaviorCore
event_start → error(component=behavior_core) → event_end

Cache hit de transacción
event_start → event_end
```

Un hit del cache semántico de Oracle continúa por la decisión y verbalización
normales, pero no fabrica un error del proveedor externo. La ausencia de
`triage`, `memory`, `decision` y `execution` en un cache hit de transacción sigue
significando que esas etapas no ocurrieron.

## No interferencia

- No se agregaron consultas ni generaciones de auditoría.
- `_gestionar_ejecucion()` conserva una sola llamada.
- Los buffers son call-scoped y thread-local; turnos concurrentes no comparten
  sus intentos ni el último proveedor usado.
- Un fallo de captura, consumo o escritura no cambia la acción funcional.
- El writer no se convierte en fuente de nuevos traces de error.

## Verificación

El runner [`core/test_orchestrator_audit.py`](../../core/test_orchestrator_audit.py)
comprueba duraciones inyectadas, timeout con fallback, agotamiento de
proveedores, fuentes Oracle, rescate/retry de Persona, privacidad, correlación y
fallo no bloqueante del writer.

Resultado de OBS-007: **PASS**.

- Pruebas focales de `core`: 26 PASS.
- Pruebas focales del fallback LLM: 4 PASS.
- Pruebas focales de Oracle: PASS.
- Regresión directa del repositorio: 150 PASS, 0 fallos inesperados y 1 fallo
  conocido del baseline (`memory.test_chromadb_flow.test_eliminacion`).
- Compilación de los módulos del runtime: PASS.

El siguiente ticket es OBS-008, sidequest de no regresión.
