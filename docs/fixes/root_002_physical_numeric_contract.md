# ROOT-002 — Contrato numérico físico finito

- Fecha: 2026-09-05
- Rama: `codex/stabilization/pre-capa2`
- Commit de entrada: `1803fd588f4f4a1c8c491acbf8ed6fdebfd1edc0`
- Ticket absorbido: `MOTOR-CONTRACT-002`
- Estado: `CLOSED`

## 1. ROOT

`ROOT-002` es P0 porque un parámetro físico no finito podía conservar una acción `valido=True`, clasificarse como `despachada` y cambiar a `null` únicamente durante serialización. El invariante de cierre es: todo parámetro numérico físico que pueda alcanzar una acción ejecutable se valida por tipo, finitud y rango contractual antes de `AccionSaliente` y antes de ejecución local.

Este packet no resuelve target selection (`ROOT-001`), lifecycle/ledger ni todas las invariantes cross-field de `MotorAction` (`ROOT-042`).

## 2. Causa

Los schemas usaban `float`. Pydantic convertía `bool` a `1.0/0.0`, aceptaba strings numéricos y también aceptaba NaN/±Inf en campos sin un constraint que los rechazara. Los constraints `ge/le/gt` rechazaban no-finitos en varios campos acotados como efecto lateral de la comparación, pero coordenadas y vectores no tenían rango y los dejaban pasar.

`MotorEngine` confiaba en esos schemas; `ActionResponse` y `AccionSaliente` almacenaban un `dict` sin validación numérica propia; el serializer transformaba los no-finitos a `null`. Además, producers directos como la interrupción crítica construyen `ActionResponse` sin pasar por `MotorEngine`, y `ActionExecutor` validaba con schemas duplicados más permisivos.

## 3. Evidencia histórica

Los informes de Bloques 02 y 06 y sus probes conservaron el comportamiento original:

```text
NaN / +Inf / -Inf en x,y,z o vector_x,y,z
→ schema acepta
→ MotorAction física
→ ActionResponse valido=True
→ AccionSaliente
→ estado despachada en RAM
→ model_dump_json convierte el valor a null
```

La reproducción focal previa a editar confirmó para `navegar_a_posicion.x`:

| Input | Schema previo | Normalización | ActionResponse | JSON | Ejecución posible |
|---|---|---|---|---|---|
| `NaN` | acepta | `nan` | válida | `null` | `despachada` |
| `+Inf` | acepta | `inf` | válida | `null` | `despachada` |
| `-Inf` | acepta | `-inf` | válida | `null` | `despachada` |
| `True` | acepta | `1.0` | válida | `1.0` | `despachada` |
| `False` | acepta | `0.0` | válida | `0.0` | `despachada` |
| `"1"`, `"-1"`, `"3.5"` | acepta | float correspondiente | válida | número | `despachada` |
| `"nan"`, `"inf"`, `"-inf"` | acepta | no finito | válida | `null` | `despachada` |
| `None` | rechaza | — | inválida | `{}` | `sin_accion` |

Los campos acotados rechazaban NaN/±Inf por sus constraints, pero todavía aceptaban `bool`. Los vectores, como las coordenadas, aceptaban ambos defectos.

## 4. Mapa de campos físicos

| Acción | Parámetro | Archivo/schema owner | Tipo antes → después | Coerción final | Validación/rango existente | Unidad conocida | Consumer final |
|---|---|---|---|---|---|---|---|
| `navegar_a_objeto` | `distancia` opcional | `motor_schemas.NavigateToObjectParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | `[0.1,10]` | metros | wire Unity; handler actual navega por objeto |
| `navegar_a_posicion` | `x/y/z` | `motor_schemas.NavigateToPositionParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | sin rango contractual | mundo 3D, unidad no formalizada | `LazzaroAgent` → Unity `Vector3` |
| `soltar_objeto` | `fuerza_lanzamiento` | `motor_schemas.ReleaseObjectParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | `[0,20]`, default `0` | fuerza, unidad no formalizada | `LazzaroAgent.EjecutarSoltar` |
| `alejarse_de_objeto` | `distancia` | `motor_schemas.MoveAwayParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | `[0.5,5]`, default `2` | metros | `MovementController.AlejarseDeObjeto` |
| `girar_avatar` | `grados` | `motor_schemas.RotateAvatarParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | `[-360,360]`; Unity rechaza `abs<1` | grados relativos en Y | `MovementController.GirarAvatar` |
| `aplicar_fuerza` | `vector_x/y/z` | `motor_schemas.ApplyForceParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | sin rango contractual | dirección, normalización no formalizada | `LazzaroAgent.EjecutarAplicarFuerza` |
| `aplicar_fuerza` | `fuerza` | `motor_schemas.ApplyForceParams` | `float` → `PhysicalFloat` | string finito→float; bool/no-finito reject | `(0,100]` | magnitud, unidad no formalizada | `LazzaroAgent.EjecutarAplicarFuerza` |

`MovementController.velocidadDeseada=3.5f` no es parámetro del wire ni de una acción Python; queda fuera de ROOT-002. Los flags de `cancelar_tarea` son booleanos reales y tampoco forman parte del contrato numérico físico.

## 5. Contrato adoptado

`PhysicalFloat` es un `float` Pydantic con un `BeforeValidator` común. Antes de la coerción:

1. rechaza `bool` explícitamente;
2. comprueba que `float(value)` sea posible;
3. exige `math.isfinite(...)`;
4. devuelve el valor a Pydantic para la normalización y aplicación de constraints existentes.

`validar_parametros_fisicos(accion, parametros)` usa el schema canónico de `ACTION_SCHEMA_MAP` y devuelve un dict normalizado. Se reutiliza en:

- `MotorEngine`, como validación temprana normal;
- `Orchestrator._sanitizar_action_response`, como barrera común para los seis tipos de acción con números físicos, incluidos producers directos/fallback;
- `ActionExecutor`, antes del schema de herramienta, como defensa del camino sandbox/local.

No se copió `math.isfinite` por handlers, no se modificó C# y la serialización no participa en la decisión de validez.

## 6. Bool policy

`True` y `False` siempre se rechazan en los once campos numéricos físicos. Los booleanos explícitos de acciones de control conservan su contrato previo.

## 7. Numeric-string policy

Se conserva compatibilidad con strings que el contrato `float` histórico normalizaba limpiamente y cuyo valor es finito, incluidos `"1"`, `"-1"`, `"3.5"` y notación exponencial finita. Se rechazan todos los spellings comprobados de no-finitos: `nan`, `NaN`, `inf`, `+inf`, `-inf`, `infinity`, `+Infinity`, `-Infinity`.

Whitespace/formatos adicionales siguen la aceptación de Pydantic después de superar el guard finito; este packet no redefine el grammar numérico general.

## 8. Rangos existentes

| Owner | Rango | Unidad | Dónde se valida |
|---|---|---|---|
| `NavigateToObjectParams.distancia` | `[0.1,10]` | m | Pydantic `ge/le` |
| `MoveAwayParams.distancia` | `[0.5,5]` | m | Pydantic `ge/le`; Unity vuelve a clamp |
| `RotateAvatarParams.grados` | `[-360,360]` | grados Y | Pydantic `ge/le`; Unity clamp y mínimo de ejecución `abs>=1` |
| `ReleaseObjectParams.fuerza_lanzamiento` | `[0,20]` | no formalizada | Pydantic `ge/le` |
| `ApplyForceParams.fuerza` | `(0,100]` | no formalizada | Pydantic `gt/le` |
| coordenadas y vector | sin rango real declarado | mundo 3D / dirección | solo tipo y finitud |

No se inventó un límite para `1e9`: coordenadas y vectores finitos grandes siguen aceptados. Centralizar unidades o imponer invariantes de vector pertenece a ROOT-042 u otro contrato posterior.

## 9. Archivos modificados

Producción:

- `ai_engine/motor_schemas.py`
- `ai_engine/motor_engine.py`
- `core/orchestrator.py`
- `tools/executor.py`

Pruebas/documentación:

- `ai_engine/test_physical_numeric_contract.py`
- `docs/audit/probes/regression_probe.py`
- `docs/fixes/root_002_physical_numeric_contract.md`

El probe de regresión dejó de comparar contra el total histórico fijo de 160
PASS y ahora exige que `PASS + KNOWN_XFAIL` coincida con las unidades realmente
descubiertas. Esto permite que el gate siga siendo estricto al añadir tests:
mantiene un único xfail conocido, cero fallos inesperados y cobertura completa
del inventario. No se modificó API schema, Unity/C#, AuditTrace ni informes
históricos.

## 10. Before / after

| Clase | Before | After |
|---|---|---|
| `NaN`, `+Inf`, `-Inf` float | coordenadas/vectores válidos, dispatch, JSON `null` | schema reject → `none`, `valido=False`, `{}`, `sin_accion` |
| strings no finitos | convertidos a no-finito, dispatch, JSON `null` | schema reject → cero dispatch |
| `True`, `False` | `1.0`, `0.0`; campos físicos válidos según rango | schema reject → cero dispatch |
| `"1"`, `"-1"`, `"3.5"` | aceptados y convertidos | continúan aceptados y normalizados |
| `0`, `-0.0`, `±1`, `±1e9` en coordenada/vector | aceptados | continúan aceptados; no hay rango inventado |
| límites contractuales existentes | aceptados/rechazados por `Field` | resultado idéntico |
| acción sin números físicos | no afectada | no afectada |

## 11. Tests

Se añadieron trece funciones permanentes que cubren:

1. NaN/±Inf en los once campos;
2. ocho spellings string no finitos;
3. bool en los once campos;
4. strings numéricos válidos;
5. finitos `0`, `-0.0`, `±1`, `±1e9`;
6. límites/defaults existentes;
7. bloqueo temprano de MotorEngine sin sustitución;
8. revalidación del fallback de Reasoning;
9. producer directo de interrupción crítica;
10. barrera común Orchestrator y cero dispatch;
11. defensa de ActionExecutor antes de invocar tool;
12. camino válido/AuditTrace equivalente;
13. acción no física inalterada.

Resultados previos al cierre global:

- `ROOT002_FOCAL_PASS=13`.
- `ai_engine/test_motor_guardrails.py`: PASS.
- `ai_engine/test_motor_fuzzy_integration.py`: PASS.
- `docs/audit/probes/capa1_motor_completion_probe.py`: PASS, todos los grupos, aislamiento intacto.
- Los probes `capa1_motor_continuation_probe.py` y `capa1_motor_remaining_probe.py` no se ejecutaron: son evidencia histórica diseñada para registrar/STOP ante el comportamiento antiguo, no tests de aceptación del fix.

Resultado global final:

- `regression_probe.py`: `gate=PASS`, `suite_gate=PASS`,
  `isolation_gate=PASS`.
- 26 módulos y 174 unidades ejecutadas: 173 PASS, 1 KNOWN_XFAIL histórico
  (`memory.test_chromadb_flow.test_eliminacion`) y 0 fallos inesperados.
- Sin fallos de import, fatal ni cleanup; los tres archivos activos de Chroma
  permanecieron sin cambios.
- `python -m compileall -q ai_engine api core tools memory terminal_client docs/audit/probes`:
  PASS (exit 0).

## 12. AuditTrace

AuditTrace v1 no cambió.

Caso válido antes/después:

```text
event_start → triage → memory → decision(action=navegar_a_posicion, valid=true)
→ execution(status=pending) → event_end
```

`pending` es el mapeo v1 histórico de la clasificación funcional `despachada`; cambiarlo pertenece a lifecycle, no a ROOT-002.

Caso inválido después:

```text
event_start → triage → memory → error(type=DecisionError, component=decision)
→ decision(action=none, valid=false) → execution(status=skipped) → event_end
```

Todos los stages comparten `trace_id`. No se añadió stage/campo, no se repitió trabajo y no hubo physical execution.

## 13. Zero-dispatch evidence

- MotorEngine devuelve `MotorAction(accion="none", parametros={})` para cada clase inválida.
- La barrera común convierte un `ActionResponse` físico directo no finito a `none`, `valido=False`, parámetros vacíos.
- `_gestionar_ejecucion` devuelve `sin_accion`; AuditTrace registra `skipped`.
- ActionExecutor devuelve `ToolResult.exito=False` y la función registrada conserva cero llamadas.
- Ningún JSON posterior contiene `null` bajo una acción física válida, porque la acción inválida ya no existe.

## 14. Fallbacks

- Reasoning puede proponer una acción física, pero `_resolver_ruta_motor` la revalida con MotorEngine y produce `none`.
- El producer directo de interrupción crítica puede omitir MotorEngine, pero la barrera común anterior a `AccionSaliente` lo bloquea.
- La invalidación nunca elimina el campo para usar un default, ni sustituye por 0/`None`, ni crea una acción física alternativa.
- Parser/legacy que terminen en cualquiera de los seis tipos de acción numérica atraviesan la misma barrera común antes de dispatch.

## 15. Compatibilidad

Se preservan nombres de acciones, estructura de parámetros, strings numéricos finitos, defaults y rangos existentes. No se versionó ni congeló el DTO Python/C#. Unity solo fue leído como consumer y no se ejecutó.

## 16. ROOT-042

Estado: **OPEN — partially improved**.

La barrera común ahora aplica el schema action-specific antes de despacho en los seis tipos con números físicos. Sin embargo, `MotorAction` sigue declarando `parametros: dict` y no impone por sí mismo todas las combinaciones acción/campos, Literals/runtime ni invariantes cross-field. Su criterio independiente no está satisfecho.

## 17. Riesgos

- Inputs que dependían accidentalmente de `True→1` o `False→0` ahora se bloquean; es el cambio de seguridad intencional.
- Strings numéricos finitos permanecen coercibles, por compatibilidad; un futuro contrato strict-string sería otro packet.
- Coordenadas/vectores finitos extremos continúan admitidos porque no existe rango real; ROOT-002 no afirma que sean físicamente razonables.
- La doble defensa Motor/response/executor revalida solo schemas en memoria y no ejecuta trabajo externo.
- AuditTrace sigue mostrando `pending` para dispatch válido; no se confundió este ROOT con ROOT-004.

## 18. Criterio de cierre

| Criterio | Estado |
|---|---|
| NaN/±Inf y strings no finitos no producen acción ejecutable | PASS |
| bool no se promociona a número físico | PASS |
| valores válidos y rangos actuales se conservan | PASS |
| fallback/direct producer no reconstruyen la acción | PASS |
| cero dispatch para no-finitos | PASS |
| no existe `nonfinite → JSON null → status ok` en runtime normal | PASS |
| tests focales/permanentes | PASS |
| regression completa | PASS: 173 PASS + 1 KNOWN_XFAIL, 0 inesperados |
| compileall | PASS |
| AuditTrace v1 intacto | PASS |
| Chroma intacta | PASS: 3/3 hashes idénticos |
| estado final ROOT-002 | CLOSED |

Estado final: **CLOSED**.
