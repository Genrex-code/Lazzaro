# ROOT-042 — Motor Action Runtime Contract

Estado máximo de este paquete:

```text
IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING / FIELD_PENDING
```

## Problema

La frontera Motor transportaba históricamente `accion: str` y
`parametros: dict`. Los schemas individuales validaban varias herramientas,
pero el runtime no tenía una autoridad única que demostrara que el
discriminador y el tipo exacto de parámetros pertenecían a la misma acción.
Una anotación `Literal` en los dataclasses de diagnóstico tampoco constituía
validación runtime.

## Contrato canónico V1

`MotorActionKindV1` cierra el vocabulario en:

```text
none
navegar_a_objeto
navegar_a_posicion
interactuar_objeto_3d
agarrar_objeto
soltar_objeto
alejarse_de_objeto
girar_avatar
aplicar_fuerza
cancelar_tarea
```

`MotorActionV1` envuelve una unión discriminada por `action` formada por
`NoActionV1`, `NavigateToObjectActionV1`, `NavigateToPositionActionV1`,
`InteractObjectActionV1`, `GrabObjectActionV1`, `ReleaseObjectActionV1`,
`MoveAwayActionV1`, `RotateAvatarActionV1`, `ApplyForceActionV1` y
`CancelTaskActionV1`. Cada variante exige versión entera exacta `1`, el
modelo de parámetros exacto, prohíbe extras y es inmutable.

La matriz es uno-a-uno:

| Acción | Parámetros |
|---|---|
| `none` | `NoActionParams` exactamente vacío |
| `navegar_a_objeto` | `NavigateToObjectParams` |
| `navegar_a_posicion` | `NavigateToPositionParams` |
| `interactuar_objeto_3d` | `InteractObjectParams` |
| `agarrar_objeto` | `GrabObjectParams` |
| `soltar_objeto` | `ReleaseObjectParams` |
| `alejarse_de_objeto` | `MoveAwayParams` |
| `girar_avatar` | `RotateAvatarParams` |
| `aplicar_fuerza` | `ApplyForceParams` |
| `cancelar_tarea` | `CancelTaskParams` |

`none` es sólo un sentinel no ejecutable. Cualquier parámetro adicional lo
invalida. `cancelar_tarea` usa booleanos estrictos; strings y enteros no se
interpretan como bool.

## Adapter legacy y compatibilidad ROOT-002

`MotorActionAdapterV1` es la entrada única desde el par legacy
`action string + raw dict`. Selecciona el schema por el discriminador,
normaliza, valida campos/cross-fields y devuelve `MotorActionV1` o
`MotorActionContractFailureV1` con un reason code finito. No se usa texto de
excepciones como autoridad.

Los strings numéricos ya admitidos por ROOT-002 se convierten exclusivamente
dentro de este boundary y el objeto canónico conserva floats. La construcción
directa de parameter models canónicos rechaza strings numéricos. El adapter
detecta los campos `PhysicalFloat` mediante la metadata del mismo schema antes
de validarlo, por lo que no duplica nombres, rangos ni defaults. Permanecen
intactos la finitud, el rechazo de bool y la validez de cero. Después del
adapter los consumidores reciben sólo la proyección normalizada.

## ROOT-028

`InteractObjectParams.tipo_accion` es obligatorio y conserva el enum
`InteractionType`: `mirar`, `tocar`, `inspeccionar`, `golpear`, `usar` o
`activar`. Ausencia, `None` y valores ajenos se rechazan; en cualquier otra
acción el campo es un extra inválido. Las pruebas demuestran la igualdad:

```text
MotorResolution.interaction_type
== MotorActionV1.params.tipo_accion
== ActionResponse.parametros["tipo_accion"]
== AccionSaliente.parametros["tipo_accion"]
```

## Diagnóstico, resolución e inmutabilidad

`MotorDiagnosis` y `MotorResolution` convierten y validan en `__post_init__`
sus estados, acciones e `InteractionType`. Acciones o estados desconocidos no
pueden construirse. Una resolución `ready` para interacción exige subtipo.
`MotorResolution` es un dataclass frozen: después de validar no pueden
reasignarse `status`, `action` ni `interaction_type`.

Los snapshots `parametros` y `metadata` de `MotorDiagnosis` se copian y se
congelan recursivamente. La API `Mapping` continúa entregando serializaciones
legacy `dict/list` mediante copias defensivas, pero el estado interno no puede
mutarse a través de referencias originales, propiedades públicas ni copias
serializadas.

Los modelos canónicos y sus parámetros son frozen. Además bloquean
explícitamente `model_copy(update=...)` con `MUTATION_FORBIDDEN`; mutar el dict
de entrada tras el parseo tampoco modifica el objeto canónico. Los modelos no
contienen colecciones anidadas mutables.

## Productores, herramientas y ledger

- `MotorEngine` materializa a través del adapter y produce `none` tipado ante
  fallo estructural.
- `ActionResponse` LLM/Reasoning sigue siendo amplio y no confiable; el guard
  común lo convierte antes de cualquier semántica física.
- cancelación e interrupción corporal construyen o intentan construir la
  variante canónica correspondiente;
- las herramientas simuladas reutilizan los mismos parameter models, sin
  schemas físicos alternos;
- `ActionExecutor` vuelve a exigir el contrato antes de buscar o invocar un
  handler;
- el replay del ledger trata action/params almacenados como serialización
  legacy y los pasa por el adapter; historia inválida falla cerrada.

## Gate de salida y orden ROOT-029

El orden productivo es:

```text
ActionResponse no confiable
→ MotorActionAdapterV1
→ MotorActionV1
→ ROOT-029, si aplica
→ nueva validación/proyección canónica
→ AccionSaliente
→ validación canónica inmediata antes de record_action
```

Una estructura inválida queda `DecisionDisposition.INVALID` con
`DecisionReasonCode.INVALID_PARAMETERS`, sin `AccionSaliente` ejecutable, sin
evaluación semántica ROOT-029 y sin transición de ledger. Las acciones no
perceptivas también pasan el gate: `NOT_APPLICABLE` nunca es bypass.
ROOT-029 conserva intactas sus reglas de freshness, identidad, seguridad y
binding.

## Failure contract

Los reason codes estructurales son:

```text
UNSUPPORTED_ACTION
PARAMETERS_NOT_OBJECT
REQUIRED_FIELD_MISSING
EXTRA_FIELD_FORBIDDEN
FIELD_TYPE_INVALID
FIELD_VALUE_INVALID
ACTION_PARAMETER_MISMATCH
CROSS_FIELD_INVARIANT_FAILED
MUTATION_FORBIDDEN
```

No se confunden con `unsafe`, `not_found`, clarificación, freshness ni
autoridad indeterminada.

## Límites y handoff

ROOT-042 no cambia política numérica ROOT-002, seguridad ROOT-027, vocabulario
ROOT-028, autoridad ROOT-029, state machine de ActionLedger, AuditTrace ni
wire DTO C#. ROOT-038 sigue siendo dueño de versionado y fuente de schema
cross-language.

La implementación queda `FIELD_PENDING`. El futuro gate de campo debe probar
que una estructura inválida/tampered produce cero mensaje ejecutable en Unity,
que las acciones canónicas válidas conservan el comportamiento actual y que
un subtipo de interacción alterado después de validar produce cero contacto o
efecto físico.
