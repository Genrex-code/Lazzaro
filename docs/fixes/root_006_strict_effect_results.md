# ROOT-006 — Resultados de efecto y approval estrictos

- Fecha: 2026-09-05
- Rama: `codex/stabilization/pre-capa2`
- Commit de entrada: `6885b6a8cb54e6ec72adef99ec986ec33d32fec5`
- Tickets absorbidos: `MEM-CONTRACT-002`, `TOOL-CONTRACT-001`,
  `TOOL-CONTRACT-003`, `TOOL-CONTRACT-004`
- Estado: `CLOSED`

## 1. Causa

Cuatro fronteras confiaban en annotations sin enforcement o en truthiness:

1. `ToolResult` era una dataclass mutable cuyo `exito: bool = True` aceptaba
   strings, números, `None` y contenedores sin validación runtime;
2. `ActionExecutor` convertía cualquier retorno que no fuera `ToolResult` a
   `ToolResult(exito=True, mensaje=str(resultado))`;
3. `EventoEntrante.detalles` es intencionalmente `dict[str, Any]` y
   `BehaviorCore` proyectaba `exito` mediante `bool(exito)` al `TaskContext`;
4. los readers de alias y afecto aprobaban metadata mediante `is False` o
   `bool(...)`, y el writer afectivo hacía `bool(confirmed)`.

Por ello `"false"` podía significar éxito, aprobación o memoria confirmada,
mientras que `False`, `None`, `{}` y `[]` crudos podían convertirse en éxito de
herramienta. La causa común era la ausencia de un contrato boolean/result
estricto en los adapters de efectos.

## 2. Tickets absorbidos

| Ticket | Manifestación | Resolución local |
|---|---|---|
| `TOOL-CONTRACT-001` | `ToolResult(exito="false")` era truthy y Orchestrator devolvía `ok` | constructor exige bool exacto; consumer usa `is True` |
| `TOOL-CONTRACT-003` | retornos `False/None/{}/[]` se promovían a éxito | allowlist cerrada de retornos y fallo contractual observable |
| `TOOL-CONTRACT-004` | handlers mezclaban `ToolResult` y payloads crudos | downstream siempre recibe `ToolResult`; `str` legacy exige annotation explícita |
| `MEM-CONTRACT-002` | strings/missing podían activar alias o afecto | solo `True` booleano activa; writer exige bool explícito |

`TOOL-CONTRACT-002` no fue absorbido: separar requisito, autorización,
pending y dispatch pertenece a `ROOT-004`.

## 3. Mapa producers/consumers

| Producer | Campo/result | Tipo declarado | Valores observables antes | Consumer | Condición anterior | Truthiness | Significado anterior |
|---|---|---|---|---|---|---|---|
| handlers mouse/teclado | `ToolResult.exito` | `bool` | bool propios; cualquier tipo posible por constructor | `ActionExecutor` | `isinstance(ToolResult)` | no en adapter; sí después | se reenviaba sin validar |
| handlers movimiento/interacción | retorno | `str` | mensaje no vacío | `ActionExecutor` | cualquier no-`ToolResult` | implícita al afirmar éxito por retorno | mensaje se convertía en success |
| handler custom/fake | retorno | libre | `False`, `None`, `{}`, `[]`, `""`, otros | `ActionExecutor` | `else: str(resultado)` | sí, por presencia de retorno | todos success |
| `ToolRegistry.register` | `requires_confirmation` | `bool` | cualquier tipo Python | `ActionExecutor` | `if value` | sí | string podía gobernar el gate |
| Pydantic `AccionSaliente` | `requiere_confirmacion` | `bool` | bool, strings y 0/1 coercibles | Orchestrator/Executor | `if flag`/`not flag` | sí | tipo se normalizaba silenciosamente |
| Unity `LazzaroAgent` | `detalles.exito` | C# `bool` | bool en producer oficial | `EventoEntrante` | payload `Any` | no en DTO | otros callers podían enviar cualquier tipo |
| `EventoEntrante` | `detalles.exito` | `Any` | todos los tipos/missing | `BehaviorCore` | `exito is not None`, luego `bool(exito)` | sí | strings truthy; 0/empty falsey |
| `BehaviorCore` | `exito` | dinámico | valor raw | `TaskContext` | `bool(exito)` | sí | mutaba intentos/frustración con coerción |
| `Orchestrator` | `ToolResult.exito` | annotation bool | valor raw conservado | estado ejecución/AuditTrace | ternario por truthiness | sí | `"false"` producía `ok` |
| alias writer | `approved` | metadata | `True` bool | alias reader | `approved is False` | aceptación por exclusión | cualquier valor salvo `False` aprobaba |
| affect writer | `confirmed` | `bool` annotation | `bool(confirmed)` | affect reader | `bool(meta)` | sí | `"false"` se volvía `True` |
| affect reader/provider | `confirmed` | metadata `Any` | bool/string/number/missing | Fuzzy resolver | `is False` | aceptación por exclusión | no-`False` podía aportar bonus |
| candidate deserializer | `approved` | dataclass bool | `bool(raw)` | serialización/aprendizaje | `bool(...)` | sí | `"false"` quedaba aprobado |
| alias learning builder | `action_success` | bool annotation | cualquier tipo directo | candidato | `if not action_success` | sí | string `"false"` habilitaba aprendizaje |
| IdentityState | `success` | bool interno | bool producido por expresión lógica | biología | `if not success` | sí | sin input ambiguo en callers productivos localizados |

`IdentityState` no cambió: el único caller productivo le entrega el resultado
de una expresión booleana interna. `ActionResponse.valido` es una decisión, no
un receipt de efecto; su tipado/provenance pertenece a `ROOT-008/020`.

## 4. Contrato ToolResult

`ToolResult` sigue siendo una dataclass pequeña, no un framework nuevo. El
contrato final es:

```text
ToolResult(exito=True)  → SUCCESS
ToolResult(exito=False) → FAILURE
ToolResult()            → TypeError (ABSENT no se convierte a success)
ToolResult(exito=<no bool exacto>) → TypeError
```

La comprobación usa `type(exito) is bool`; por tanto `0/1`, aunque sean
comparables con bool en Python, no sobreviven. El `TypeError` que ocurra dentro
de un handler queda capturado por `ActionExecutor` y se transforma en el fallo
existente, sin escapar ni reejecutar el handler.

## 5. Policy de retornos crudos

La allowlist central queda así:

1. `ToolResult`: conservar exactamente success/failure;
2. `str` no vacío y distinto de los sentinels case-insensitive `true/false`:
   aceptar solo si el handler declara explícitamente `-> str`; se trata como
   mensaje legacy exitoso, nunca se normaliza a booleano;
3. cualquier otro retorno, un string vacío/boolean-like o un string sin
   contrato declarado: devolver
   `ToolResult(exito=False, error="Contrato de retorno invalido.")`;
4. una excepción del handler: conservar la captura histórica como failure;
5. en todos los casos el handler se invoca una sola vez.

No se añadió metadata al registry porque los 10 payload handlers productivos ya
declaran `-> str`; las otras 7 tools producen `ToolResult`. Los dict/list no se
declaran falsos por su truthiness: quedan `INVALID` porque ningún handler
productivo tiene ese contrato. Si aparece uno futuro, deberá declarar y adaptar
su success de forma explícita.

### Inventario de handlers productivos

| Tool | Función | Retorno declarado/real | `ToolResult` | bool | `None` | Adapter final |
|---|---|---|---:|---:|---:|---|
| `navegar_a_objeto` | `navegar_a_objeto` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `navegar_a_posicion` | `navegar_a_posicion` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `alejarse_de_objeto` | `alejarse_de_objeto` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `girar_avatar` | `girar_avatar` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `cancelar_tarea` | `cancelar_tarea` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `interactuar_objeto_3d` | `interactuar_objeto_3d` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `agarrar_objeto` | `agarrar_objeto` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `soltar_objeto` | `soltar_objeto` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `aplicar_fuerza` | `aplicar_fuerza` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `hablar` | `hablar` | `str` no vacío | no | no | no | mensaje legacy declarado → success |
| `mover_mouse` | `mover_mouse` | `ToolResult(True)` | sí | no | no | conservar |
| `click_mouse` | `click_mouse` | `ToolResult(True)` | sí | no | no | conservar |
| `escribir_texto` | `escribir_texto` | `ToolResult(True)` | sí | no | no | conservar |
| `presionar_tecla_especial` | `presionar_tecla_especial` | `ToolResult(True)` | sí | no | no | conservar |
| `mouse_real_move` | `mouse_real_move` | `ToolResult(True/False)` | sí | no | no | conservar; no ejecutado |
| `mouse_real_click` | `mouse_real_click` | `ToolResult(True/False)` | sí | no | no | conservar; no ejecutado |
| `keyboard_real` | `keyboard_real` | `ToolResult(True/False)` | sí | no | no | conservar; no ejecutado |

## 6. Policy Memory approval

La policy de lectura es fail-closed y no migra Chroma:

```text
raw is True  → approved/confirmed
cualquier otro valor, incluido missing → invalid / no aprobado
```

El writer de alias continúa escribiendo `approved=True` booleano porque
`guardar_alias_aprendido()` es la operación explícita que aprueba un candidato.
El writer afectivo ahora exige que `confirmed` sea provisto y que sea bool
exacto: `False` puede persistirse como no confirmado; un tipo inválido devuelve
`None`, emite warning y no llama al store. El resolver fuzzy vuelve a comprobar
`confirmed is True`, de modo que un provider alterno tampoco puede inyectar
`"false"` como bonus.

No se tocó ni migró metadata histórica. Los valores legacy ambiguos quedan
inertes hasta una migración conscientemente diseñada.

## 7. Compatibilidad legacy

Se conserva la única compatibilidad inequívoca observada: los 10 handlers que
declaran `-> str` y devuelven un mensaje no vacío/no boolean-like. No se
interpreta el contenido del mensaje como bool. Los producers oficiales Unity ya
envían bool real y no requieren adaptación.

No se conserva compatibilidad con sentinels Memory string/number/missing ni con
returns de handler sin contrato. Esos casos eran el defecto. No existe evidencia
de un formato legacy oficial que justifique normalizar `"true"`, `"false"`, 1 o
0. Los probes históricos permanecen como evidencia inmutable del antes.

## 8. Matriz de booleanos

En la tabla, `TR` es `ToolResult.exito`, `RA` es
`resultado_accion.detalles.exito`, y `MEM` cubre readers de
`approved/confirmed`. “Efecto” significa mutación de Task/aprendizaje/bonus por
ese flag; no incluye construir el objeto de prueba.

| Input | Parse TR / RA | Normalized | Valid | MEM | Resultado semántico | Efecto posible |
|---|---|---|---|---|---|---|
| `True` | acepta / acepta | `True` bool | sí | aprobado | SUCCESS | sí, por success/approval explícito |
| `False` | acepta / acepta | `False` bool | sí | no aprobado | FAILURE | Task registra fallo; no aprendizaje/bonus |
| `"true"` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `"false"` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `"TRUE"` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `"FALSE"` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `1` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `0` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `1.0` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `0.0` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `None` | rechaza / rechaza si presente | ninguno | no | no aprobado | INVALID | no |
| `""` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `[]` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| `{}` | rechaza / rechaza | ninguno | no | no aprobado | INVALID | no |
| missing | TR: falta argumento; RA: acepta ausencia | ninguno | RA: ABSENT | no aprobado | ABSENT | no |

`AccionSaliente.requiere_confirmacion` usa `StrictBool`; el registry y executor
también rechazan metadata de confirmación no booleana. Esto solo endurece el
tipo: no redefine qué significa pending/autorizado.

## 9. Matriz de retornos de handler

| Retorno | Contrato declarado | Resultado ActionExecutor | Mensaje/payload | Calls | Clasificación |
|---|---|---|---|---:|---|
| `ToolResult(True)` | `ToolResult` | `exito=True` | conserva | 1 | SUCCESS |
| `ToolResult(False)` | `ToolResult` | `exito=False` | conserva | 1 | FAILURE |
| `True` | ninguno soportado | `exito=False` | error contractual | 1 | INVALID |
| `False` | ninguno soportado | `exito=False` | error contractual | 1 | INVALID |
| `None` | ninguno soportado | `exito=False` | error contractual | 1 | ABSENT/INVALID |
| `""` | `-> str` | `exito=False` | error contractual | 1 | INVALID |
| `"true"`/`"false"` | `-> str` | `exito=False` | error contractual | 1 | INVALID |
| `"ok"` | `-> str` | `exito=True` | `"ok"` | 1 | SUCCESS legacy explícito |
| `"ok"` | sin annotation | `exito=False` | error contractual | 1 | INVALID |
| `{}` | ninguno soportado | `exito=False` | error contractual | 1 | INVALID |
| `{"value": 1}` | ninguno soportado | `exito=False` | error contractual | 1 | INVALID |
| `[]` | ninguno soportado | `exito=False` | error contractual | 1 | INVALID |
| `raise` | excepción | `exito=False` | error capturado | 1 | EXCEPTION/FAILURE |

## 10. Before/after

| Caso | Antes | Después | Razón de cambio |
|---|---|---|---|
| `ToolResult(exito="false")` | objeto válido y truthy | `TypeError` predecible | string no representa bool |
| `ToolResult()` | success implícito | falta argumento | ABSENT no es SUCCESS |
| handler `False` | success, mensaje `"False"` | failure contractual | bool crudo no tiene contrato registrado |
| handler `None` | success, mensaje `"None"` | failure contractual | ausencia no prueba efecto |
| handler `{}`/`[]` | success por retorno | failure contractual | payload no decide success |
| handler `-> str: "ok"` | success | success | compatibilidad productiva inequívoca |
| `resultado_accion.exito="false"` | DTO acepta; Task registra success | DTO rechaza; caller directo no muta Task | resultado externo exige bool |
| alias `approved="false"` | aprobado | no aprobado | solo `True` autoriza |
| alias missing | aprobado | no aprobado | legacy ambiguo falla cerrado |
| afecto `confirmed="false"` | activo | inactivo | se elimina `bool(raw)` |
| writer `confirmed="false"` | persistía `True` | warning + `None`, cero writes | no coerción en escritura |
| hint alterno `confirmed="false"` | podía aportar bonus | bonus cero | defensa en consumer final |

## 11. Archivos modificados y tests permanentes

Producción:

- `tools/base.py`
- `tools/executor.py`
- `tools/registry.py`
- `api/schemas.py`
- `core/behavior_core.py`
- `core/task_context.py`
- `core/orchestrator.py`
- `memory/memory_manager.py`
- `ai_engine/learned_alias_candidates.py`
- `ai_engine/fuzzy_object_resolver.py`

Prueba y documentación:

- `core/test_strict_effect_results.py`
- `docs/fixes/root_006_strict_effect_results.md`

No se modificaron handlers productivos, C#, Chroma, AuditTrace ni probes de
auditoría histórica.

### Tests permanentes

`core/test_strict_effect_results.py` contiene 21 unidades focales que cubren:

- constructor `ToolResult`, incluidos bool reales, invalids y missing;
- matriz completa de retornos fake y una sola invocación;
- contrato explícito `-> str` y string no declarado;
- excepción controlada;
- validation de `resultado_accion` y confirmation flags;
- defensa directa de Behavior/Task;
- readers/writers sintéticos de alias y afecto;
- candidato/aprendizaje de alias;
- consumer fuzzy;
- propagación `ActionExecutor → Orchestrator → AuditTrace failed`.

No usan red, Unity, LLM, hardware ni Chroma activa.

## 12. AuditTrace

AuditTrace v1 permanece congelado: no cambió schema, stage ni sanitización.
Para una tool local válida se conserva el mapeo histórico:

```text
ToolResult(True) → resultado_memoria=ok → execution.status=ok
ToolResult(False) → resultado_memoria=error → execution.status=failed
```

Un retorno inválido ahora sigue la segunda ruta. No se vuelve a invocar el
handler para auditar y no se añade raw metadata del resultado. Los estados
`pending/despachada` no se reinterpretaron.

## 13. Roots vecinos

| ROOT | Estado tras ROOT-006 | Límite preservado |
|---|---|---|
| `ROOT-004` | `OPEN`, parcialmente habilitado | no se creó state machine ni se separó autorización/dispatch |
| `ROOT-015` | `OPEN`, parcialmente habilitado | no hay receipts durables, atomicidad, dedupe ni migración |
| `ROOT-003` | `OPEN/BLOCKED` hasta ROOT-006 + ROOT-012 | no se creó action ID/ledger/correlación |
| `ROOT-008/020` | `OPEN` | no se cambió provenance/validez de decisiones AI |

## 14. Riesgos y compatibilidad

- Plugins/tools custom que devolvían bool, `None`, dict/list o string sin
  annotation ahora fallan cerrado; deberán devolver `ToolResult` o declarar el
  contrato legacy `-> str`.
- Metadata histórica con approval missing/string deja de influir. No se inventó
  normalización y no se tocó la base activa.
- `confirmed=False` aún puede persistirse como dato no confirmado; dedupe y
  receipt de esa escritura permanecen para ROOT-015.
- El bit `requiere_confirmacion` conserva su ambigüedad de lifecycle; solo su
  tipo es estricto.
- Los errores contractuales usan el mecanismo existente; ROOT-010 definirá el
  envelope público estable.

## 15. Criterio de cierre y evidencia

| Evidencia | Resultado |
|---|---|
| focal ROOT-006 | PASS: 21/21 |
| ROOT-001 | PASS: 14/14 |
| ROOT-002 | PASS: 13/13 |
| alias + afecto existentes | PASS: 10/10 |
| regression completa | PASS: 208 PASS + 1 KNOWN_XFAIL, 0 inesperados |
| compileall | PASS |
| AuditTrace v1 | intacto |
| Chroma activa | 3/3 hashes idénticos a entrada |

Los probes históricos no se reescribieron. `contracts_motor_tools_probe.py`
se detiene primero en la expectativa ya invalidada por ROOT-002 de aceptar bool
como coordenada. `contracts_memory_probe.py` y `memory_deep_probe.py` fallan
exactamente en sus assertions históricas de que `"false"` se acepta como
approval; esa inversión es evidencia esperada del fix, no una regresión. La
matriz de aceptación vigente vive en la prueba permanente ROOT-006.

| Criterio | Estado |
|---|---|
| `"false"` no decide success/approval por truthiness | PASS |
| `"true"` no se acepta como booleano | PASS |
| `ToolResult.exito` es bool exacto y obligatorio | PASS |
| `False`/`None` crudos no se promueven a success | PASS |
| dict/list no deciden por truthiness | PASS |
| handlers productivos válidos conservan contrato | PASS |
| readers/writers Memory son fail-closed | PASS |
| `resultado_accion` distingue bool/absent/invalid | PASS |
| consumers Task/Orchestrator usan bool explícito | PASS |
| invalids no escapan ni reejecutan el handler | PASS |
| ROOT-004/015 no implementados | PASS |
| ROOT-001/002 y regresión global verdes | PASS |
| AuditTrace y Chroma intactos | PASS |

## 16. Estado final

Estado final: **CLOSED**.
