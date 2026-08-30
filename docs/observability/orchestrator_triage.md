# Auditoría de Triage en Orchestrator — OBS-004

Estado: **implementado y verificado.**

OBS-004 registra la decisión original de Capa 0 y la ruta que realmente usará
`Orchestrator` después de las adaptaciones del canal. La observabilidad reutiliza
las variables del flujo existente: no vuelve a ejecutar `TriageRouter`.

Implementación: [`core/orchestrator.py`](../../core/orchestrator.py).

## Secuencia

```text
event_start
    ↓
BehaviorCore
    ↓
TriageRouter (una sola evaluación)
    ↓
snapshot de intent/route/confidence/reason originales
    ↓
adaptación para el canal terminal
    ↓
triage (route original + effective_route operativa)
    ↓
flujo funcional existente
    ↓
event_end
```

El `trace_id` creado al entrar en `procesar_evento` se pasa explícitamente al
procesamiento serializado. De ese modo los tres stages de un turno quedan
correlacionados sin almacenar contexto compartido en el objeto y sin riesgo de
mezclar invocaciones concurrentes.

## Contrato registrado

| Campo | Fuente |
|---|---|
| `triage.intent` | intención original de Triage |
| `triage.route` | ruta original de Triage |
| `triage.effective_route` | ruta post-adaptación que consume Orchestrator |
| `triage.confidence` | confianza original, conservando su tipo numérico |
| `triage.reason` | explicación original de Triage |

La fotografía de los cuatro campos originales se toma antes de llamar a
`_adaptar_triage_para_terminal`. Por eso una reconducción terminal puede quedar
registrada así, sin confundir la decisión del clasificador con la del canal:

```json
{
  "stage": "triage",
  "triage": {
    "intent": "MOTOR_SIMPLE",
    "route": "motor",
    "effective_route": "persona",
    "confidence": 0.99,
    "reason": "Decision de prueba."
  }
}
```

El `reason` de auditoría también es el original. El texto adicional que el
adaptador terminal pueda agregar a la decisión operativa no lo reemplaza.

## Ausencias deliberadas

- Un cache hit emite `event_start → event_end`: no alcanzó Triage.
- Un retry de guardado resuelto antes de Triage conserva la misma ausencia.
- Un fallo de `BehaviorCore` no fabrica `triage`: desde OBS-007 queda como
  `event_start → error(component=behavior_core) → event_end`.

No se inventan registros para completar visualmente la secuencia. La ausencia
de `stage=triage` indica que esa etapa no se ejecutó.

## Tolerancia a fallos

El stage usa el writer de OBS-002. Un resultado `False` o una excepción de un
writer inyectado no cambia la ruta ni la acción funcional; ante una excepción se
emite un warning en el logger humano y el flujo continúa. No se agregó otro lock
ni otra ruta de logging recursiva.

## Fuera de alcance

OBS-004 no registra ni modifica:

- recuperación o escritura de memoria (OBS-005);
- capa cognitiva, acción o ejecución (OBS-006);
- errores estructurados, fallbacks, timeouts o duraciones (incorporados después
  por OBS-007);
- lógica del clasificador o del adaptador de terminal.

## Verificación

El runner [`core/test_orchestrator_audit.py`](../../core/test_orchestrator_audit.py)
comprueba:

1. ruta normal con `route == effective_route`;
2. reconducción terminal `motor → persona`;
3. conservación de `intent`, `confidence` y `reason` originales;
4. una sola llamada a Triage;
5. el mismo `trace_id` en `event_start`, `triage` y `event_end`;
6. cache hit sin `triage`;
7. fallo previo de `BehaviorCore` sin `triage`;
8. fallo del writer de `triage` sin alterar la decisión funcional.

Resultado de OBS-004: **PASS**.

- Pruebas específicas OBS-003/004: 10 PASS.
- Regresión directa del repositorio: 131 PASS, 0 fallos nuevos y 1 fallo conocido
  del baseline (`memory.test_chromadb_flow.test_eliminacion`).
- Compilación de los módulos del runtime: PASS.

La integración posterior de recuperación y escritura de memoria está
documentada en [`orchestrator_memory.md`](orchestrator_memory.md), y la de capa,
acción final y ejecución en
[`orchestrator_decision_execution.md`](orchestrator_decision_execution.md). El
registro posterior de errores seguros, fallbacks y duración está documentado en
[`orchestrator_errors_durations.md`](orchestrator_errors_durations.md). El
siguiente ticket es OBS-008, sidequest de no regresión.
