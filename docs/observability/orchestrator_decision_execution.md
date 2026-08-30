# Auditoría de decisión y ejecución en Orchestrator — OBS-006

Estado: **implementado y verificado.**

OBS-006 registra qué capa primaria resolvió el turno, cuál fue la acción final
después de los guardarraíles y qué ocurrió con la única gestión de ejecución ya
existente. No vuelve a resolver una capa, no repite la ejecución y no cambia el
contrato funcional de `Orchestrator`.

Implementación: [`core/orchestrator.py`](../../core/orchestrator.py).

## Capa realmente ejecutada

`decision.layer` se asigna dentro de la rama que produjo la decisión. No se
deduce desde `triage.route`, `triage.effective_route`, el nombre de la acción ni
la última capa auxiliar que haya tocado la respuesta.

| Rama ejecutada | `decision.layer` |
|---|---|
| `_resolver_ruta_motor()` | `motor` |
| `_resolver_ruta_persona()` | `persona` |
| `_resolver_ruta_oracle()` | `oracle` |
| `reasoning.process()` legacy | `reasoning` |
| interrupción, cancelación, guardado, noop/internal, resultado de hablar o pasivo | `none` |

Esta semántica conserva dos casos importantes:

- Motor puede pedir aclaración con una acción `hablar` y continúa siendo
  `layer=motor`.
- Oracle continúa siendo `layer=oracle` aunque Persona verbalice después su
  respuesta pública.

La adaptación de canal ocurre antes de seleccionar la rama. Por tanto, si el
terminal reconduce una ruta Motor hacia Persona, `triage.route=motor`,
`triage.effective_route=persona` y `decision.layer=persona` describen
correctamente las tres decisiones causales.

## Acción final observada

El stage `decision` se emite después de esta secuencia funcional existente:

```text
_blindar_salida_terminal()
    ↓
_sanitizar_action_response()
    ↓
_blindar_salida_terminal()
    ↓
_adjuntar_esfuerzo_subjetivo()
    ↓
_a_accion_saliente()
    ↓
stage=decision
```

El contrato registrado es exactamente:

```json
{
  "decision": {
    "layer": "persona",
    "action": "hablar",
    "valid": true
  }
}
```

`action` es el nombre de la `AccionSaliente` final y `valid` procede de
`action_response.valido` después de los guardarraíles. No se guardan
pensamiento, razón, parámetros ni contenido verbal.

## Ejecución sin repetir trabajo

`_gestionar_ejecucion()` conserva su única llamada. OBS-006 observa su retorno
y lo traduce al vocabulario estable de `AuditTrace v1`:

| Retorno interno existente | `execution.status` |
|---|---|
| `sin_accion` | `skipped` |
| `pendiente_confirmacion` | `pending` |
| `ok` | `ok` |
| `error` | `failed` |
| `despachada` | `pending` |

`despachada` no significa éxito: solo confirma que la acción fue enviada al
cliente externo. Un estado interno desconocido no fabrica semántica y no genera
un stage de ejecución.

`execution.error_type` permanece explícitamente en `null`. Errores
estructurados, fallbacks, timeouts y duraciones pertenecen a OBS-007.

## Secuencia y ausencias

Un turno normal queda correlacionado con un único `trace_id`:

```text
event_start → triage → memory → decision → execution → event_end
```

Un cache hit o un fallo previo de `BehaviorCore` conserva únicamente
`event_start → event_end`. Una rama pasiva que sí alcanzó el flujo estándar
registra `decision(layer=none, action=none)` y
`execution(status=skipped)`.

## Tolerancia a fallos

Los writers de `decision` y `execution` siguen el contrato no bloqueante: una
excepción produce un warning en el logger humano y devuelve fallo observable,
pero no modifica la acción ni repite la capa o la ejecución. Un resultado
`False` del writer también se limita al canal de auditoría.

## Verificación

El runner [`core/test_orchestrator_audit.py`](../../core/test_orchestrator_audit.py)
comprueba:

1. Persona con `layer=persona`;
2. Motor físico con `layer=motor`;
3. Motor que aclara mediante `hablar` sin convertirse en Persona;
4. Oracle verbalizado que conserva `layer=oracle`;
5. Reasoning legacy y sanitización final;
6. rama Core/pasiva con `layer=none` y ejecución `skipped`;
7. acción posterior al firewall terminal;
8. los cinco mapeos de ejecución;
9. `error_type=null` y ausencia de detalles internos;
10. un solo resolver y una sola llamada a `_gestionar_ejecucion()`;
11. el mismo `trace_id` en los seis stages normales;
12. cache y fallo de `BehaviorCore` sin stages artificiales;
13. fallos del writer de decisión o ejecución sin impacto funcional.

Resultado de OBS-006: **PASS**.

- Pruebas específicas OBS-003–OBS-006: 17 PASS.
- Regresión directa del repositorio: 138 PASS, 0 fallos nuevos y 1 fallo conocido
  del baseline (`memory.test_chromadb_flow.test_eliminacion`).
- Compilación de los módulos del runtime: PASS.

El siguiente ticket es OBS-007, observabilidad de errores, fallbacks y duración.
