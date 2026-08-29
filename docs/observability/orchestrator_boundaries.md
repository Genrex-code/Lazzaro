# Límites AuditTrace en Orchestrator — OBS-003

Estado: **implementado y verificado.**

OBS-003 conecta únicamente los límites `event_start` y `event_end` alrededor de cada llamada a `Orchestrator.procesar_evento`. Ambos registros comparten `trace_id` e `id_transaccion`.

Implementación: [`core/orchestrator.py`](../../core/orchestrator.py).

## Comportamiento

1. `procesar_evento` genera un UUID4 para la invocación.
2. Emite `event_start` antes de adquirir el lock de la transacción.
3. Ejecuta el flujo existente, incluidas salidas tempranas y respuestas cacheadas.
4. Libera el lock transaccional.
5. Emite `event_end` desde un `finally`, incluso si el procesamiento propaga una excepción.

`event_start` registra solo:

- `trace_id`;
- `id_transaccion`;
- `stage=event_start`;
- origen;
- tipo de evento;
- preview seguro del mensaje.

`event_end` registra únicamente los identificadores correlacionados y `stage=event_end`. El writer conserva el contrato no bloqueante de OBS-002; un fallo de auditoría no cambia el resultado funcional del evento.

## Fuera de alcance

OBS-003 no registra ni infiere:

- estado o tarea pre/post;
- decisión o ruta de Triage;
- recuperación o creación de memoria;
- capa cognitiva, acción o ejecución;
- error funcional, fallback, timeout o duración.

Esos datos pertenecen respectivamente a OBS-004–OBS-007. No se modificaron las funciones internas de Triage, memoria, resolución de capas ni ejecución.

## Compatibilidad

- El constructor acepta inyección opcional de un writer para pruebas.
- El runtime normal crea `AuditTraceJSONLWriter` por defecto.
- Los fakes históricos construidos con `object.__new__` y sin writer continúan funcionando.
- Cada invocación obtiene su propio `trace_id`; los dos stages de esa invocación reutilizan el mismo valor.

## Verificación

El runner [`core/test_orchestrator_audit.py`](../../core/test_orchestrator_audit.py) comprueba:

1. inicio/cierre y correlación correcta;
2. cierre desde `finally` ante una excepción funcional;
3. fallo no bloqueante del writer;
4. compatibilidad con orquestadores aislados antiguos;
5. persistencia real de dos líneas JSON correlacionadas;
6. correlación de una salida temprana desde cache;
7. ausencia explícita de metadata reservada para OBS-004–OBS-007.

Resultado de OBS-003: **PASS**.

- Pruebas específicas: 6 PASS.
- Regresión directa del repositorio: 127 PASS, 0 fallos nuevos y 1 fallo conocido del baseline (`memory.test_chromadb_flow.test_eliminacion`).

La integración posterior de la decisión original y la ruta efectiva está
documentada en [`orchestrator_triage.md`](orchestrator_triage.md).
