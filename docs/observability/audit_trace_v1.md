# AuditTrace v1 — OBS-001

Estado: **implementado y verificado; writer OBS-002 disponible.**

AuditTrace es el contrato estructurado de auditoría de LAZZARO. Complementa el logger humano existente; no lo reemplaza. La persistencia JSONL, la concurrencia y la tolerancia a fallos de escritura pertenecen a OBS-002.

## API del contrato

- `AuditTrace`: dataclass con una forma superior fija.
- `AuditTrace.from_mapping(...)`: completa un dict parcial e ignora campos superiores ajenos a v1.
- `AuditTrace.to_dict()`: devuelve únicamente tipos compatibles con JSON.
- `AuditTrace.to_json()`: genera JSON determinista, UTF-8 y sin `NaN`/`Infinity`.
- `serialize_audit_trace(...)`: acepta `AuditTrace`, un mapping parcial o `None`.
- `safe_preview(...)`: resume texto en una línea, lo limita a 240 caracteres y redacta secretos.

Implementación: [`core/audit_trace.py`](../../core/audit_trace.py).

## Forma superior estable

| Campo | Valor por defecto o regla |
|---|---|
| `trace_version` | Siempre `1` |
| `trace_id` | UUID4 nuevo cuando no se proporciona |
| `id_transaccion` | `null` si aún no existe correlación externa |
| `timestamp` | ISO-8601 UTC con milisegundos |
| `stage` | `event_start`; solo admite las etapas de v1 |
| `build` | `commit`, `branch`, `config_hash` |
| `llm` | `provider`, `model_id`, `fallback_used` |
| `input` | `origin`, `event_type`, `message_preview` |
| `pre` | `state`, `task`, `perception_snapshot_id` |
| `triage` | `intent`, `route`, `effective_route`, `confidence`, `reason` |
| `memory` | `retrieved_ids`, `scores`, `created_ids`, `working_memory_count` |
| `decision` | `layer`, `action`, `valid` |
| `execution` | `status`, `error_type` |
| `post` | `state`, `task` |
| `durations_ms` | Dict vacío |
| `error` | `null` |

Etapas válidas: `event_start`, `triage`, `memory`, `decision`, `execution`, `event_end` y `error`.

## Garantías de serialización

- Los campos y secciones ausentes reciben defaults nuevos e independientes.
- Los campos superiores desconocidos no amplían accidentalmente el contrato v1.
- `datetime`, fechas, UUID y rutas se convierten a texto estable.
- Sets se convierten en listas ordenadas; bytes se sustituyen por su longitud.
- Valores flotantes no finitos se convierten en `null`.
- Excepciones conservan solo su tipo, nunca el mensaje potencialmente sensible.
- Estructuras cíclicas y profundidad excesiva se detienen con marcadores seguros.
- La entrada original no se modifica durante la serialización.

## Reglas de privacidad

- Claves sensibles como `api_key`, `authorization`, `password`, `secret`, credenciales, cookies y contenido `env` se sustituyen por `[REDACTED]` en cualquier profundidad.
- Patrones comunes de API keys, bearer tokens y asignaciones sensibles se redactan también dentro de strings.
- `max_tokens` y métricas equivalentes no se confunden con tokens de autenticación.
- Las vistas previas de mensajes son de una sola línea y están acotadas.
- AuditTrace no debe recibir prompts completos, contenido de `.env` ni configuración extensa; para configuración se usa `config_hash`.

## Ejemplo mínimo

```python
from core.audit_trace import AuditTrace

trace = AuditTrace(
    id_transaccion="tx-123",
    stage="triage",
    triage={"intent": "SOCIAL", "route": "persona", "confidence": 0.91},
)
linea_json = trace.to_json()
```

## Verificación

El runner [`core/test_audit_trace.py`](../../core/test_audit_trace.py) comprueba:

1. forma superior estable y JSON válido;
2. tolerancia a campos ausentes y defaults independientes;
3. redacción por nombre de campo y por patrón dentro de strings;
4. normalización de valores no JSON sin mutar la entrada;
5. rechazo de etapas fuera del contrato.

Resultado de OBS-001: **PASS**.

- Pruebas específicas: 5 PASS.
- Regresión directa del repositorio: 114 PASS, 0 fallos nuevos y 1 fallo conocido del baseline (`memory.test_chromadb_flow.test_eliminacion`).

El writer de OBS-002 está documentado en [`audit_trace_writer.md`](audit_trace_writer.md),
los límites de OBS-003 en [`orchestrator_boundaries.md`](orchestrator_boundaries.md)
y la decisión original/efectiva de OBS-004 en
[`orchestrator_triage.md`](orchestrator_triage.md). La metadata de memoria de
OBS-005 está en [`orchestrator_memory.md`](orchestrator_memory.md). El registro
de capa, acción final y ejecución de OBS-006 está en
[`orchestrator_decision_execution.md`](orchestrator_decision_execution.md). El
siguiente ticket es OBS-007, observabilidad de errores, fallbacks y duración.
