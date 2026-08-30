# Aceptación del sidequest: un ID, un JSONL

El gate reconstruye las nueve respuestas a partir de las líneas del mismo
`id_transaccion` en un solo archivo JSONL. No consulta el logger humano, Chroma,
el objeto Orchestrator ni otro archivo para contestarlas.

La inspección final encontró un hueco real: v1 ya definía `pre` y `post`, pero
OBS-003–008 todavía no los poblaban. Este patch completa esos campos existentes;
no añade una versión, un stage ni un OBS-009.

## Mapa de aceptación

| Pregunta | Línea / campo leído | Resultado del fixture `sidequest-acceptance-001` |
|---|---|---|
| ¿Qué recibió Lazzaro? | `event_start.input` | Terminal, comando de usuario: «Ve al cubo y recuerda lo que hablamos.» |
| ¿Qué estado/tarea tenía antes? | `event_start.pre.state/task` | Energía 80; «Objetivo anterior», 2 intentos |
| ¿Qué decidió Triage y cuál fue la ruta efectiva? | `triage.triage` | `MOTOR_SIMPLE`, `motor → persona`, confianza 0.99 y razón original |
| ¿Qué memoria se recuperó? | `memory.memory` | `mem-01`, `mem-72`; scores 0.88, 0.74; creado `mem-new-acceptance`; RAM previa 1 |
| ¿Qué capa procesó? | `decision.decision.layer` | `persona` |
| ¿Qué acción salió? | `decision.decision.action` | `hablar` |
| ¿Qué se ejecutó o bloqueó? | `execution.execution` | `pending`: no consta ejecución confirmada; no afirmar movimiento físico |
| ¿Hubo fallback/error/timeout? | `decision.llm.fallback_used` + líneas `error` | Sí, fallback y `TimeoutError` del componente `llm` |
| ¿Cómo terminó el estado? | `event_end.post.state/task` | Energía 61, foco 77; objetivo actual del mensaje, 1 intento |

Los valores son de una prueba determinista, no de una conversación real ni de
un movimiento observado en Unity. Orchestrator, BehaviorCore, el writer JSONL y
la cadena de fallback son reales; las respuestas de proveedores y el almacén
de memoria están aislados. No se llama a una API de pago.

## Captura previa y final

`BehaviorCore.obtener_snapshot_audit()` lee el estado mediante
`StateEngine.obtener_estado()` y cinco atributos existentes de la tarea:
`objetivo_id`, `intentos_totales`, `intentos_fallidos`, `frustracion_local` y
`umbral_abandono`. No usa el resumen general, narrativa, constelación,
microhistorial ni vuelve a calcular loop/estancamiento/compulsión.

Orchestrator hace una copia profunda al emitir cada límite. El objetivo usa el
preview seguro central de AuditTrace, limitado a 240 caracteres, para que un
mensaje largo no entre al log a través de TaskContext. El resto de la
serialización y redacción sigue perteneciendo a `AuditTrace.to_json()`.

- `pre` se lee en `event_start`, antes del lock transaccional y antes de mutar
  el estado por el evento o liquidar el descanso terminal.
- `post` se lee en el `finally` de `event_end`, tras liberar el lock. Incluye
  las mutaciones tardías del flujo, no reutiliza el estado devuelto antes por
  `BehaviorCore.procesar_evento()`.
- El getter puede proyectar descanso, como la lectura pública del estado;
  nunca aplica un tick, despierta ni modifica las reservas almacenadas.
- Una lectura válida sin tarea produce `task={}`. Si la captura falla,
  `state=null` y `task=null` significan desconocido, y se intenta un warning
  humano seguro. Un componente legacy sin getter conserva los defaults v1.
- Un fallo de captura o incluso de su warning no cambia la respuesta ni
  sustituye una excepción funcional. Su coste se excluye de la duración total
  funcional de OBS-007.

Los snapshots son observaciones de esos límites, no una transacción atómica
del estado global. IDs distintos aún pueden intercalarse, como antes del patch.
No se añadió un lock global ni se atribuyen todos los cambios exclusivamente
al evento observado. Si hay retries del mismo ID, se agrupan sus líneas por
`trace_id`: no se mezclan snapshots de distintas invocaciones.

## Límites que v1 conserva deliberadamente

- La entrada y el objetivo son previews, no transcripciones completas.
- La memoria se identifica por IDs y `metadata_score`; no se vuelcan recuerdos.
- La acción es el nombre final, no sus parámetros ni pensamiento privado.
- `pending` conserva el mapeo aprobado de OBS-006: puede ser espera de
  confirmación o despacho a un cliente. No permite distinguir ambos motivos
  ni demostrar una ejecución física. `skipped`, `ok` y `failed` conservan su
  semántica existente. No se inventa un motivo de bloqueo ausente del contrato.
- Un stage perdido por fallo del writer no se puede reconstruir. Una etapa
  que no ocurrió (por ejemplo, Triage en un cache hit) tampoco se fabrica.

Esta aceptación valida el nivel de detalle aprobado en v1; no afirma contener
información que se decidió omitir. Distinguir motivos de `pending`, bloqueos
concretos o parámetros requeriría una ampliación explícita del contrato.

## Pruebas

Runner: [`core/test_audit_acceptance.py`](../../core/test_audit_acceptance.py).

```powershell
python core/test_audit_acceptance.py
```

El runner imprime `ACCEPTANCE_FROM_JSONL` con las nueve respuestas derivadas del
archivo. Contiene además pruebas de lectura sin detectores/tick, descanso no
mutado, objetivo acotado y saneado, copias independientes en éxito/error,
fallo de snapshot/logger no bloqueante, descuento temporal y writer caído con
snapshots del Core real. La prueba completa cuenta una sola búsqueda/resumen,
escritura, Triage, resolución y ejecución, y una llamada por proveedor simulado.

Resultado: **PASS dentro del contrato v1 y de los límites anteriores**.

- Pruebas específicas de aceptación/snapshots: 7 PASS.
- Integración Orchestrator OBS-003–008: 29 PASS.
- Writer JSONL OBS-002: 7 PASS.
- Regresión directa del repositorio: 160 PASS, 0 fallos nuevos y 1 fallo
  histórico conocido (`memory.test_chromadb_flow.test_eliminacion`).
- Compilación y `git diff --check`: PASS.
- Revisión independiente: sin hallazgos P0–P2 pendientes.
