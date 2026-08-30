# AuditTrace JSONL Writer — OBS-002

Estado: **implementado y verificado.**

El writer agrega registros `AuditTrace` a un archivo JSONL sin integrarse todavía con `Orchestrator`. El logger humano permanece separado y solo se usa como canal de aviso cuando el append falla.

Implementación: [`core/audit_writer.py`](../../core/audit_writer.py).

## API

```python
from core.audit_trace import AuditTrace
from core.audit_writer import AuditTraceJSONLWriter

writer = AuditTraceJSONLWriter()
result = writer.write(AuditTrace(stage="event_start"))

if not result.success:
    print(result.error_type)
```

`AuditWriteResult` expone:

- `success`: `True` cuando la línea fue agregada completa;
- `path`: destino del intento;
- `error_type`: tipo estable del fallo, sin mensaje potencialmente sensible;
- conversión booleana equivalente a `success`.

## Garantías

- Destino por defecto: `logs/audit_trace.jsonl`.
- Crea el directorio padre cuando no existe.
- Codificación UTF-8 sin BOM y terminación LF.
- Un `AuditTrace` por línea.
- Apertura con `O_APPEND`; si `os.write` escribe parcialmente, continúa desde el offset pendiente bajo el mismo lock hasta completar la línea.
- Un retorno de cero bytes desde `os.write` se trata como `OSError` para evitar un bucle infinito.
- Lock compartido entre instancias para impedir mezcla de líneas entre threads del proceso.
- Usa exclusivamente `AuditTrace.to_json()`; no duplica sanitización ni contrato.
- Conserva el `trace_id` recibido, por lo que puede reutilizarse entre distintos stages.
- Devuelve éxito o fallo en todos los errores operativos y de serialización.
- Ante un fallo emite `warning` mediante el logger humano y continúa.
- Si el warning provoca una reentrada al writer, devuelve `ReentrantAuditWrite` sin emitir otro warning.
- Si el propio logger humano falla, su excepción también se contiene.

El lock garantiza seguridad entre threads del mismo proceso. Coordinación entre procesos distintos no forma parte de OBS-002.

## Límites intencionales

- No modifica `core/logger.py`.
- No registra automáticamente mensajes del logger humano.
- No integra stages ni hooks en `Orchestrator`.
- No implementa rotación, compresión, lectura ni envío de trazas.

## Verificación

El runner [`core/test_audit_writer.py`](../../core/test_audit_writer.py) comprueba:

1. UTF-8, append-only, una línea por JSON y reutilización de `trace_id`;
2. 240 escrituras concurrentes válidas y completas;
3. creación del directorio padre;
4. warning humano y resultado fallido sin excepción;
5. corte de recursión logger → AuditTrace → logger;
6. delegación directa a `AuditTrace.to_json()`;
7. reintento del segmento restante ante escrituras parciales simuladas;
8. tratamiento de un retorno de cero bytes como `OSError`.

Resultado de OBS-002: **PASS**.

- Pruebas específicas: 7 PASS.
- Regresión directa del repositorio: 121 PASS, 0 fallos nuevos y 1 fallo conocido del baseline (`memory.test_chromadb_flow.test_eliminacion`).

La integración acotada de OBS-003 está documentada en
[`orchestrator_boundaries.md`](orchestrator_boundaries.md) y la captura de Triage
de OBS-004 en [`orchestrator_triage.md`](orchestrator_triage.md). La metadata de
memoria de OBS-005 está en [`orchestrator_memory.md`](orchestrator_memory.md). El
siguiente ticket es OBS-006, observabilidad de decisión y capa cognitiva.
