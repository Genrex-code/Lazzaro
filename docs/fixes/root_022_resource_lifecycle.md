# ROOT-022 — ownership simétrico de recursos de arranque

Estado: `IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`. P1; absorbe
`BOOT-AUDIT-001/002/003/004/007`. ROOT-031 es prerrequisito. ROOT-023
(readiness) y ROOT-030 (cache global Chroma entre paths/lifecycles) permanecen
separados.

## Problema y ownership

Antes, el `lifespan` registraba recursos sólo en variables locales y un fallo
parcial podía omitir cierres, interrumpir la cascada o dejar `app.state` con
referencias a objetos cerrados. Los constructores de MemoryManager,
Orchestrator y los ledgers podían adquirir hijos antes de lanzar una excepción.
La fábrica LLM podía perder providers ya construidos; Gemini tenía una ventana
entre `AsyncClient` y el `finally`.

Ahora `main` adquiere y registra inmediatamente en `AsyncExitStack`:

```text
main (owner)
  Chroma experiencias
  MemoryManager (Chroma borrowed en esta ruta; owner de MemoryWriteLedger)
  FallbackAIClient (owner de providers)
  Orchestrator (owner de OracleMemory/Chroma Oracle y ActionLedger)
```

El cierre LIFO es `Orchestrator → AI → MemoryManager → Chroma experiencias`.
Dentro de Orchestrator es `ActionLedger → OracleMemory`; dentro de AI,
providers en orden inverso. El contrato legacy de MemoryManager conserva
`owns_chroma_client=True` por defecto para otros callers. El constructor de
cada owner revierte localmente los recursos que adquirió pero aún no pudo
entregar a su caller.

## Arbitraje y estado

Todos los cierres ordinarios se intentan aun si uno falla. Si ya existe un
fallo de startup o body, éste se propaga y los errores de limpieza quedan como
diagnósticos secundarios seguros (componente/stage y tipo, nunca mensaje,
traceback ni datos del recurso). Si el body terminó bien, se propaga el primer
fallo de limpieza después de intentar los demás. `KeyboardInterrupt`,
`SystemExit` y cancelación no se convierten en éxito ni se reemplazan por
fallos ordinarios de cierre. Esta garantía cubre cancelación cooperativa de
startup/health/body, no SIGKILL, pérdida de energía o crash irrecuperable.

`app.state.ai_client` y `app.state.orchestrator` empiezan limpios, se publican
tras composición completa y se invalidan antes del teardown y al salir de
cualquier ruta. Son referencias de validez, **no** un estado de readiness.
Owners con hijos tienen cierre idempotente por intento: un segundo `close()`
no repite la cascada. Si algún hijo lanza, se registra el fallo y se propaga
según el arbitraje; no se afirma que el recurso haya quedado liberado.

## Límites internos

- SQLite: MemoryWriteLedger y ActionLedger cierran la conexión local ante
  cualquier fallo posterior a `connect` (incluidos PRAGMA/schema). Un fallo
  de `connect` no entrega conexión. MemoryManager revierte su ledger si falla
  la composición posterior.
- Chroma: un fallo tras `PersistentClient` libera el wrapper mediante su ruta
  local de refcount. Con dos wrappers del mismo path, el primero no detiene el
  sistema compartido y el segundo sigue funcional; sólo el último detiene el
  handle/limpia cache según el mecanismo existente. No se rediseñan claves de
  cache global ni semántica cross-path: eso sigue en ROOT-030.
- Gemini: cada llamada posee su `httpx.AsyncClient` desde su creación;
  `HttpOptions`, SDK, fachada async y request están bajo el mismo `finally`.
  Se intenta cerrar fachada async, cliente SDK sync y transporte HTTP
  caller-owned. No cambia retry, deadline, scheduler ni política ROOT-021.
- AuditTrace, API, Unity y semántica de acciones no participan del cleanup.

## Validación permanente

`core/test_root022_lifecycle.py` inyecta fallos en Settings, BehaviorCore,
identidad, Chroma, MemoryManager, descripción de backend, AI, health,
ReasoningEngine, ToolRegistry y Orchestrator; verifica LIFO, dos generaciones,
body failure, arbitraje, limpieza fallida, cancelación y `app.state`.
`memory/test_root022_lifecycle.py` cubre ledger PRAGMA/schema/connect,
rollback de MemoryManager, Chroma antes/después de handle y sharing same-path.
`core/test_root022_action_ledger_lifecycle.py` cubre ActionLedger
connect/PRAGMA/schema/idempotencia. `ai_engine/test_root022_lifecycle.py`
cubre providers tempranos/tardíos, composición Fallback, cierre inverso único
y Gemini en HTTP/opciones/SDK/request/éxito. `core/test_logger_failsoft.py`
compone fallo de logfile con fallo de health/cierre, stderr seguro y causa
primaria preservada. La prueba source-level de ROOT-010 se actualizó para
exigir el nuevo boundary de `BaseException` y `AsyncExitStack`, conservando
sus aserciones de redacción.

Regresión dirigida ROOT-003/007/010/012/017/021/031, Oracle, Chroma,
Memory, AI y lifecycle: **390 passed, 1 warning**. Suite global sobre
`api`, `ai_engine`, `core`, `memory`, `terminal_client`: **1215 passed,
1 warning, 0 XFAIL**. `compileall -q .` y `git diff --check` terminaron con
exit code 0. `PRAGMA quick_check` read-only dio `ok` para Chroma,
MemoryWriteLedger y ActionLedger. Sus SHA-256 quedaron iguales al baseline:

```text
chroma.sqlite3              C003D87617B5156E358E4DD56DAB9B8765401AF239F809750627E42A77D23F6D
memory_write_ledger.sqlite3 118074BA7F2B1BB9AB2E916BAD4B6F267D04FFEBC44E85E0475F186F26C852DA
action_ledger.sqlite3       18DB24084BFFF0F28FEC54D519C97651C6CA0B8B6726B31EFC02575E153642B1
```

Los tests usan fakes o almacenamiento temporal; no abren ni mutan Chroma
real. El único warning de la suite es el de dependencias ya presente en el
baseline; no hay XFAIL. Riesgo residual: si un cierre nativo falla de modo
ambiguo, el owner no reintenta en cascada y el recurso puede continuar vivo;
queda diagnosticado de forma segura para intervención. Sharing cross-path y
semántica de readiness siguen en ROOT-030 y ROOT-023 respectivamente.
