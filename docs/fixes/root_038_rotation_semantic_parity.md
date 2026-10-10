# ROOT-038 B2 — paridad semántica de rotación en Unity

## Defecto

El contrato canónico Python de `girar_avatar.grados` admite todo número finito
en `[-360, 360]`, incluidos cero y los subgrados. Unity reescribía ese dominio:
clampaba el valor y rechazaba cualquier magnitud menor que un grado. Una acción
válida podía por tanto terminar como fallo sin materializar la semántica pedida.

## Semántica V1 exacta

- `grados == 0`: acción válida que completa con éxito como no-op. Conserva el
  `id_accion`, no crea coroutine y no ejecuta `MoveRotation`.
- `0 < abs(grados) < 1`: rotación física válida. La maquinaria existente recibe
  el valor solicitado sin redondeo, clamp ni umbral mínimo.
- `abs(grados) >= 1`: conserva el comportamiento de rotación previo dentro del
  dominio canónico.

## Validación en el consumidor

`LazzaroAgent` exige que `grados` sea un token JSON numérico, finito y dentro de
`[-360, 360]`. Rechaza `null`, booleanos, strings numéricos, NaN, infinitos y
valores fuera de rango antes de despachar movimiento. No hay default, coerción
ni clamp.

`MovementController` repite la comprobación de finitud y rango para proteger
callers directos. Un valor inválido produce callback fallido con el mismo
`actionId` y cero rotación.

## Contratos preservados

- ROOT-002 mantiene sin cambios su dominio Python canónico.
- ROOT-029 no cambia autoridad, target binding ni provenance.
- ROOT-042 no cambia schemas, adapters ni resolución canónica.
- ActionLedger, API y versionado wire general quedan fuera de este bloqueo.

Las pruebas son estáticas y de contrato; no ejecutan Unity Play Mode ni
movimiento físico. La comprobación material sigue pendiente de field testing.
