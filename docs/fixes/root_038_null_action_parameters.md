# ROOT-038 B7 — parámetros de acción nulos y defaults de cancelación

Estado máximo de este packet:
`BLOCKER_B7_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Riesgo corregido

El DTO Python exige que `accion.parametros` sea un diccionario, pero el DTO
Unity lo recibe como `JObject`. Antes de B7, una respuesta con
`cancelar_tarea` y `parametros: null` llegaba al handler y los operadores
`??` fabricaban `cancelar_movimiento=true` y `cancelar_cola=true`. Una entrada
inválida podía así frenar movimiento y limpiar la cola.

## Frontera

El orden de recepción queda:

```text
JSON crudo
→ ActionEnvelopeWireBoundary (B7)
→ AuthorityWireBoundary (B5)
→ RespuestaAPI tipada
→ ActionBindingVerifierV1 (B6)
→ onSuccess
```

B7 inspecciona los tokens JSON originales. Para cualquier acción no nula,
`parametros` debe existir y ser un objeto. `parametros` ausente, nulo, array o
escalar produce `INVALID_RESPONSE` antes de `onSuccess`. `accion: null` y la
ausencia de acción conservan las semánticas existentes de no-acción.

B7 no incorpora autoridad perceptual a `cancelar_tarea`, no modifica B5/B6 y
no reemplaza la futura arquitectura general de ROOT-038.

## Contrato de `cancelar_tarea`

Un objeto vacío es válido. Sólo después de validar el contenedor, los campos
ausentes reciben los defaults canónicos ya existentes:

```text
motivo = "Solicitud explicita del usuario."
cancelar_movimiento = true
cancelar_cola = true
texto_confirmacion = "De acuerdo, cancelo la tarea actual."
```

Los únicos campos permitidos son esos cuatro. Si aparecen,
`cancelar_movimiento` y `cancelar_cola` deben ser tokens JSON Boolean, y
`motivo` y `texto_confirmacion` deben ser tokens JSON String. `null`, strings
booleanos, números, objetos, arrays, campos desconocidos y duplicados fallan
cerrado.

Por tanto:

```text
parametros missing/null/no-objeto
→ INVALID_RESPONSE
→ no onSuccess
→ no ProcesarRespuestaDelCerebro
→ no EjecutarCancelarTarea
→ no FrenarMovimiento
→ no CancelarFilaTareasUsuario
→ no receipt de éxito
```

## Verificación

La prueba focal compila la frontera C# pura con el Mono/Newtonsoft instalado
por Unity, sin abrir Unity. La matriz cubre el contenedor de acción, ambos
booleanos, ambos strings, campos ausentes, extras y duplicados. Las pruebas
estáticas fijan además el orden B7 → B5 → DTO → B6 y confirman que la frontera
no conoce ni puede invocar efectos de `LazzaroAgent`.
