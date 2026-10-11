# ROOT-038 B15 — Action lifecycle continuity / origin binding

## Estado

`BLOCKER_B15_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`

## Hallazgo

La correlación de la respuesta raíz ya exigía que `respuesta.id_transaccion`
coincidiera con la solicitud en vuelo. Sin embargo, el contexto ejecutable se
construía desde `accion.id_transaccion_origen`, que era un segundo valor
controlado por el wire. Una respuesta con raíz `tx-current` y origen de acción
`tx-other` podía ejecutar y después reportar o cerrar contra `tx-other`.

El servidor mantiene una identidad estable en `ActionRecord`: `action_id`,
`transaction_id`, `action_name`, `parameters` y la referencia de autoridad. El
probe sintético de confirmación verificó que aprobación y rechazo proyectan de
nuevo esos mismos valores; únicamente cambia el estado del lifecycle.

## Contrato congelado

La continuidad pertenece a `LazzaroAgent`, que posee la solicitud actual y el
estado pendiente. No se añadió otro validador raw a `ApiClient`.

Para una acción nueva o producida por un evento que no sea una continuación de
confirmación:

- el origen esperado es `solicitud.id_transaccion`;
- origen ausente, nulo, vacío o whitespace conserva compatibilidad legacy;
- un origen presente debe coincidir exactamente;
- el `ContextoAccion` deriva siempre el origen de la solicitud, nunca vuelve a
  copiar el valor opcional del wire.

Para `confirmacion_accion`:

- `solicitud.detalles["id_accion"]` debe ser un string no vacío;
- `solicitud.detalles["approved"]` debe ser un `bool` real, sin coerción;
- debe existir el contexto pendiente de ese ID exacto;
- ID, nombre, parámetros y autoridad recibidos deben ser idénticos al pendiente;
- el origen puede omitirse, pero si está presente debe ser el origen original;
- `approved=true` sólo admite `status=ok` y `estado_ejecucion=dispatched`;
- `approved=false` sólo admite `status=ok` y `estado_ejecucion=rejected`;
- la ejecución o cierre usa el `ContextoAccion` almacenado, no los campos
  ejecutables de la respuesta de confirmación.

El productor Python vigente no genera `skipped` desde
`_procesar_confirmacion_accion`: autorizar una acción Unity la despacha y
rechazarla la deja en `rejected`. Por ello `skipped`,
`awaiting_authorization`, un status distinto de `ok` o cualquier inversión
entre la decisión local y el lifecycle recibido fallan cerrados.

La autoridad se preserva de forma completa: nulo sólo coincide con nulo y una
referencia presente requiere igualdad estructural. Esto conserva el binding ya
emitido por ROOT-029/B6 sin crear una política de autoridad nueva.

## Orden fail-closed

La continuidad se valida antes de almacenar o eliminar pendientes, encolar
`inicio_ejecucion_accion`, seleccionar el handler físico, reportar el resultado
o cerrar la tarea. Una continuación inválida no reemplaza ni elimina el
pendiente y no se transforma en el fallo de otra acción.

`inicio_ejecucion_accion` conserva su comportamiento habitual sin acción. Si
`resultado_accion` (u otro evento no confirmación) produce una acción nueva, se
aplica la regla de origen de la solicitud actual.

## Pruebas permanentes

`unity_client/test_root_038_action_lifecycle_continuity.py` compila la frontera
pura con el Mono/Newtonsoft instalado por Unity, sin abrir Unity. Cubre:

- el exploit `tx-current` / `tx-other`;
- acción inmediata, pendiente inicial y orígenes legacy omitidos;
- aprobación y rechazo válidos con su lifecycle exacto;
- la matriz `approved=true/dispatched` y `approved=false/rejected`;
- inversión de decisión, `skipped`, re-pending y valores `approved` no bool;
- mutaciones de origen, ID, nombre, parámetros y autoridad añadida, retirada o
  cambiada;
- orden de validación anterior a mutaciones/ejecución;
- uso del contexto almacenado para ejecución, reporte y cierre.

No se modificaron B1–B14, ROOT-029, el esquema de ActionLedger, la semántica
Python del lifecycle ni la atestación material.
