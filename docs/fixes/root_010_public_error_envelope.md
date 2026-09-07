# ROOT-010 — Stable Redacted Public Error Envelope

## 1. ROOT

ROOT-010 corrige el boundary publico de errores sin modificar respuestas normales. Su estado de code gate es `CLOSED_CODE / FIELD_PENDING`; el field posterior debe validar el contrato con Unity real.

## 2. Causa

Los fallos usaban canales incompatibles: `/evento` reutilizaba `RespuestaAPI` y el genérico publicaba `str(exception)`; `/estado`, `/constelacion` y dependencias usaban `{"detail": ...}`; FastAPI 422 podía reflejar input; Unity concatenaba body y `request.error`; varios sinks humanos registraban excepción, body, URL o traceback crudos.

## 3. Tickets absorbidos

Se absorben `CONFIG-AUDIT-006`, `API-CONTRACT-002` y `DTO-CONTRACT-001`. No se absorben ROOT-009, ROOT-031, ROOT-038, ROOT-054 ni ROOT-055.

## 4. Public error map before

| Condición | HTTP anterior | Body anterior |
|---|---:|---|
| reconciliation/incomplete | 409 | `RespuestaAPI(status=pendiente)` |
| aborted/processing | 500 | `RespuestaAPI(status=error)` |
| transaction conflict | 409 | `RespuestaAPI(status=error)` |
| ActionLedger | 503 | `RespuestaAPI(status=error)` |
| generic `/evento` | 500 | mensaje con `str(exception)` |
| unavailable/state/constellation | 503/500 | `detail` de FastAPI |
| validation | 422 | lista `detail`, potencialmente con input |
| notifications internal failure | 200 | `[]` |

No existían version, stable code ni correlation ID comunes.

## 5. Human log sink map

| Sink auditado | Antes | Después |
|---|---|---|
| API | excepción/traceback/transacción dinámica | component/code/correlation/error_type/HTTP |
| startup | excepción + traceback | campos seguros; Settings también queda dentro del boundary |
| Gemini | exception message truncado | type, correlation e información numérica de retry |
| OpenAI-compatible | provider body/exception | status HTTP y type |
| Ollama | base URL y exception | URL omitida, type estructurado |
| Oracle | provider body/exception | status HTTP y type |
| fallback/reasoning | child exception | component/type |
| Orchestrator central | excepción/traceback propagados | component/type; API conserva la correlación pública |

La corrección está limitada a estos boundaries y manifestaciones directamente conectadas; no es una limpieza global de logs.

## 6. Unity error map

HTTP 2xx con `ok` o `pendiente` conserva `onSuccess`. Un 2xx legacy con `error`, status desconocido o JSON inválido produce un `ApiFailure` local fijo. Todo non-2xx intenta primero parsear el envelope. Network/timeout, body malformado y schema incompatible terminan en errores locales seguros, sin body ni exception text.

## 7. Public envelope final

```json
{
  "version": 1,
  "status": "error",
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "Ocurrio un error interno.",
    "correlation_id": "00000000-0000-4000-8000-000000000000"
  }
}
```

No contiene action, transaction payload, exception, traceback, provider body, URL, prompt ni cognition.

## 8. Error version

`version` es el literal entero `1`. Este versionado pertenece exclusivamente a `PublicErrorEnvelope`; los demás DTO continúan sin versionado global y permanecen bajo ROOT-038.

## 9. Public code taxonomy

La allowlist es: `RECONCILIATION_REQUIRED`, `TURN_ABORTED`, `TURN_PROCESSING_FAILED`, `TRANSACTION_INCOMPLETE`, `TRANSACTION_CONFLICT`, `OPERATIONAL_STORAGE_UNAVAILABLE`, `AGENT_UNAVAILABLE`, `STATE_READ_FAILED`, `CONSTELLATION_READ_FAILED`, `VALIDATION_ERROR` e `INTERNAL_ERROR`.

Unity añade sólo códigos locales: `TRANSPORT_FAILURE`, `TRANSPORT_TIMEOUT`, `INVALID_RESPONSE` y `BACKEND_ERROR`.

## 10. Message policy

Cada public code tiene una template fija en Python. Dos excepciones de la misma categoría producen el mismo mensaje. Unity no usa `error.message`: para codes conocidos selecciona su propia template allowlisted; code desconocido o versión inválida produce mensaje local genérico.

## 11. Correlation policy

Cada error recibe un UUID propio del boundary. El mismo valor se escribe en el envelope y en el log seguro del API. No se reutilizan `id_transaccion`, `id_accion` ni `AuditTrace.trace_id`; sus identidades conservan sus contratos independientes.

## 12. HTTP mapping

| Code | HTTP | Status |
|---|---:|---|
| RECONCILIATION_REQUIRED | 409 | pendiente |
| TRANSACTION_INCOMPLETE | 409 | pendiente |
| TRANSACTION_CONFLICT | 409 | error |
| TURN_ABORTED | 500 | error |
| TURN_PROCESSING_FAILED | 500 | error |
| INTERNAL_ERROR | 500 | error |
| STATE_READ_FAILED | 500 | error |
| CONSTELLATION_READ_FAILED | 500 | error |
| OPERATIONAL_STORAGE_UNAVAILABLE | 503 | error |
| AGENT_UNAVAILABLE | 503 | error |
| VALIDATION_ERROR | 422 | error |

`TURN_ABORTED → 500` se preserva del runtime y documentación de ROOT-007; ROOT-010 no redefine esa semántica.

## 13. Pending vs error

Reconciliation e incomplete conservan HTTP 409 con `status=pendiente`. Awaiting authorization continúa siendo una respuesta normal HTTP 200 `pendiente`. Valid no-op continúa HTTP 200 `ok` con `accion=null`.

## 14. Validation errors

`RequestValidationError` se mapea a 422 `VALIDATION_ERROR`. No se serializan `exc.errors()`, payload, input, locaciones ni valores. 404/405 conservan el comportamiento FastAPI y permanecen fuera del alcance.

## 15. Public allowlist

El DTO Pydantic prohíbe extras tanto arriba como dentro de `error`. La construcción sólo acepta code, template y correlation procedentes de tablas internas; nunca serializa `__dict__`, `vars`, repr ni metadata de la excepción.

## 16. Logger policy

`log_safe_boundary_failure()` acepta component, code, correlation, error type y HTTP numérico. Valida identifiers y UUID, no lee el mensaje de la excepción y no configura handlers. Los boundaries auditados no usan `exc_info=True`.

## 17. Redaction policy

La defensa principal es no pasar texto no confiable al sink. Provider body, traceback, URL completa, bearer, key, password, path y exception text se descartan. No se pretende convertir cualquier string arbitrario en seguro mediante regex.

## 18. Provider boundaries

Gemini conserva la lectura privada del mensaje únicamente para calcular backoff, pero no lo registra. OpenAI-compatible y Oracle conservan HTTP status sin body. Ollama deja de registrar su URL. Fallback y Reasoning registran type/component, preservando retries, fallback, selección y resultados funcionales.

## 19. Startup boundary

La carga de Settings y la construcción quedan dentro del mismo `try`. El fallo se registra sin exception message ni traceback y se propaga como antes. No se cambian cleanup, readiness, ownership ni configuración de handlers.

## 20. Unity parser

`ApiClient` usa `Action<ApiFailure>`. Para non-2xx deserializa `PublicErrorEnvelope`, exige version 1, status conocido, error presente y correlation UUID. Los codes conocidos usan mensajes locales; un code desconocido se degrada a `BACKEND_ERROR` y conserva una correlation válida.

## 21. Malformed body behavior

JSON inválido, HTML/proxy, versión incompatible, correlation inválida o combinación code/status incompatible se descartan sin mostrar body. Con HTTP recibido se devuelve `BACKEND_ERROR`; sin HTTP se usa `TRANSPORT_FAILURE` o `TRANSPORT_TIMEOUT`. Las excepciones locales de parse se convierten en `INVALID_RESPONSE`.

## 22. AuditTrace

AuditTrace v1 permanece congelado. Una excepción con canario se serializa solamente como `{"type": "RuntimeError"}` dentro del stage existente; no se añaden public code, correlation ni body.

## 23. ROOT-009 boundary

ROOT-009 permanece `OPEN / NEXT`. Campos de cognition en respuestas normales no se modifican. `PublicErrorEnvelope` no contiene ninguno de ellos.

## 24. ROOT-055 boundary

ROOT-055 permanece `OPEN`. `/notificaciones` conserva deliberadamente exception → HTTP 200 `[]`; sólo su log humano se vuelve seguro. No se afirma distinguir todavía empty de failure.

## 25. ROOT-038 boundary

ROOT-038 permanece `OPEN`. Python y C# comparten únicamente el error envelope v1; no se versiona ni normaliza el resto de DTOs.

## 26. Field gate

ROOT-010 requiere field test, pero este packet no abre Unity. El estado queda `CLOSED_CODE / FIELD_PENDING`. El field posterior comprobará non-2xx real, code/correlation visible y ausencia de body crudo.

## 27. Tests

Los 34 tests focales permanentes cubren matriz HTTP, mensajes estables, correlation, 422, no-op/pending, ROOT-055, DTO estricto, provider/startup canaries, AuditTrace y contrato C# estático. Se suman las regresiones ROOT-011/007/005/004/003/012/006/001/002 y el probe global aislado.

## 28. Risks

Unity se valida estáticamente hasta el field gate. Un proxy sin envelope produce mensaje genérico, por diseño. Los logs internos no auditados permanecen fuera de este packet; ROOT-031 conserva ownership del sistema de logging. El mensaje remoto permanece en el DTO C# sólo para compatibilidad de wire y nunca es leído para UX.

## 29. Criterion

El code gate exige una forma única, versionada, parseable, redactada y correlacionable; status HTTP heredado; canarios ausentes de HTTP/logger/AuditTrace/Unity; no-op y pending intactos; logs mínimos; Unity fail-closed; Chroma y runtime stores intactos; 0 fallos inesperados; no field y no push.

## 30. Final state

ROOT-010 queda `CLOSED_CODE / FIELD_PENDING`. ROOT-009, ROOT-031, ROOT-038, ROOT-054 y ROOT-055 permanecen abiertos. No se inició Capa 2, TCM, DevelopmentalCore, Memory redesign, DB cognitiva nueva ni migración Chroma.
