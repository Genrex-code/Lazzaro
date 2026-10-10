# ROOT-038 B3 — dirección canónica de `aplicar_fuerza`

## Decisión semántica

`vector_x/vector_y/vector_z` representan sólo dirección. `fuerza` representa
la magnitud física. Por ello `(0,0,0)` y `(-0.0,0.0,0.0)` son inválidos y no
son no-ops exitosos.

Todo vector finito no nulo se almacena canónicamente como vector unitario. La
normalización divide primero por la mayor magnitud de componente y calcula
después `hypot` sobre valores escalados. Así evita overflow y underflow incluso
para doubles finitos extremos, sin umbrales físicos inventados. Un ajuste
determinista de unidades en último lugar deja `hypot == 1.0`; una dirección ya
canónica se conserva byte por byte al revalidarse. Esto evita que replay o
ActionLedger alteren por redondeo los parámetros ligados por ROOT-029.

## Orden de autoridad

La normalización pertenece a `ApplyForceParams`; ocurre durante
`MotorActionAdapterV1`, antes de finalizar `MotorActionV1`. En consecuencia,
ROOT-029 calcula `canonical_parameters_json` y `action_binding_sha256` sobre la
dirección unitaria exacta. Esa misma representación llega a `ActionResponse`,
`AccionSaliente` y ActionLedger.

El vector cero produce `FIELD_VALUE_INVALID`, se proyecta como
`DecisionDisposition.INVALID`/`INVALID_PARAMETERS` y nunca alcanza evaluación
ROOT-029, acción ejecutable ni transición de dispatch.

## Consumidor Unity y ROOT-005

Unity conserva su defensa contra direcciones no finitas o nulas y normaliza
defensivamente antes de `Rigidbody.AddForce`. Una dirección canónica recibida
no se reinterpreta mediante umbrales de gameplay.

ROOT-005 no cambia: `APPLIED` continúa requiriendo respuesta física compatible
observada después de `FixedUpdate`. El vector cero no se convierte en
`APPLIED`, `NO_EFFECT` exitoso ni no-op.

Las pruebas son Python y estáticas; no ejecutan Unity Play Mode, red ni
movimiento físico.
