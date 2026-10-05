# ROOT-027 — Reachable Motor safety authority

## Estado

`IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`

ROOT-027 corrige el defecto historico en el que la seguridad fisica dependia de
una comparacion imposible: `risk_penalty` solo producia `0`, `5` o `20`, pero el
bloqueo legacy exigia `>= 50`. El ranking y la autorizacion ya no comparten
autoridad.

## Invariante

La autorizacion fisica procede exclusivamente de una evaluacion categorica de
la evidencia disponible. El ranking solo expresa preferencia entre candidatos:

```text
SAFETY AUTHORIZATION != RANKING PREFERENCE
```

`SafetyAssessmentV1` es un contrato interno estricto, inmutable y serializable:

```text
contract_version = 1
disposition       = SAFE | CAUTION | UNSAFE | INDETERMINATE
reason_code       = enum finito
ranking_penalty   = puntos finitos en [0, 20]
```

La autorizacion se deriva, no se almacena como otro boolean mutable:

```text
SAFE / CAUTION          -> autorizada por seguridad
UNSAFE / INDETERMINATE  -> denegada por seguridad
```

`INDETERMINATE` significa que la evidencia de accion disponible no basta para
establecer seguridad. No afirma que el peligro este probado, ni representa el
`UNKNOWN` semantico de Triage; aun asi, falla cerrado.

## Politica accion × peligro

| Evidencia | Disposicion | Reason code | Penalizacion |
|---|---|---|---:|
| sin `peligro` | `SAFE` | `NO_HAZARD` | 0 |
| retirada | `SAFE` | `HAZARD_AVOIDANCE` | 0 |
| observacion tipada | `CAUTION` | `HAZARD_OBSERVATION` | 5 |
| aproximacion | `UNSAFE` | `HAZARD_APPROACH` | 20 |
| contacto tipado | `UNSAFE` | `HAZARD_CONTACT` | 20 |
| manipulacion | `UNSAFE` | `HAZARD_MANIPULATION` | 20 |
| fuerza | `UNSAFE` | `HAZARD_FORCE` | 20 |
| interaccion generica | `INDETERMINATE` | `HAZARD_INTERACTION_UNSPECIFIED` | 20 |
| accion desconocida | `INDETERMINATE` | `HAZARD_ACTION_UNSUPPORTED` | 20 |
| fallo local de datos evaluables | `INDETERMINATE` | `SAFETY_ASSESSMENT_FAILED` | 20 |

La penalizacion son puntos restados al score fuzzy. No es probabilidad,
confidence, severidad ni permiso fisico. Un candidato con score 100 sigue sin
ser ejecutable si la evaluacion es `UNSAFE` o `INDETERMINATE`.

## Affordance y seguridad

Las dos dimensiones permanecen separadas:

```text
applicable = no hay affordance requerida OR existe affordance compatible
safety_allowed = disposition in {SAFE, CAUTION}
allowed = applicable AND safety_allowed
```

Una affordance ausente conserva `status=not_allowed`, incluso si la evaluacion
de seguridad simultanea es `UNSAFE` o `INDETERMINATE`. Si la affordance aplica,
ambas disposiciones denegadas se proyectan al `status=unsafe` legacy. Esta
proyeccion no convierte `INDETERMINATE` en `UNSAFE`; el assessment anidado es la
autoridad causal.

`AffordanceDecision.risk_penalty` es ahora una propiedad derivada de
`safety_assessment.ranking_penalty`. Fuzzy no vuelve a calcularla.

## Retirada y observacion

La retirada de un objeto peligroso es `SAFE / HAZARD_AVOIDANCE / 0`, por lo que
ya no sufre la degradacion artificial de 20 puntos. Si los demas gates permiten
la accion, sigue siendo elegible.

Una observacion con semantica tipada es
`CAUTION / HAZARD_OBSERVATION / 5`. `CAUTION` requiere semantica conocida; no es
un alias para una interaccion desconocida.

## ROOT-028 y bloqueo temporal

ROOT-027 no propaga `interaction_type` por el resolver fuzzy. Por eso una
`interactuar_objeto_3d` sobre un peligro sin tipo confiable permanece:

```text
INDETERMINATE -> denied
```

Esto es un falso bloqueo temporal deliberado hasta ROOT-028. La ruta directa de
`AffordanceResolver` ya soporta observacion tipada, sin fingir que Fuzzy posee
esa evidencia.

## Fallos y compatibilidad ROOT-001

Solo un fallo local esperado al leer datos de affordances se normaliza como
`SAFETY_ASSESSMENT_FAILED`. No se captura `Exception` alrededor del subsistema.
Los defectos inesperados propagan hasta el boundary fail-closed existente de
Motor, que produce una resolucion no ejecutable.

Tanto `UNSAFE` como `INDETERMINATE` atraviesan
`FuzzyObjectResolver -> MotorResolution -> MotorDiagnosis -> MotorEngine ->
Orchestrator` sin crear `MotorAction` fisica, sin recuperacion de Reasoning y sin
dispatch. No se modifico ROOT-001.

## Pruebas

Las pruebas permanentes cubren:

- version estricta, modelo frozen, `extra=forbid` y dominio numerico;
- mapping inmutable reason/disposition/penalty;
- alcance natural de las cuatro disposiciones;
- matriz completa safety × affordance;
- aproximacion, retirada e interaccion generica en Fuzzy/Motor;
- score 100 incapaz de elevar una denegacion;
- invariancia ante nombre visible, origen, orden, contexto y texto humano;
- propagacion de excepciones inesperadas al boundary Motor existente.

No hubo Play Mode, movimiento real, contacto ni pruebas de actuadores.

## Fuera de alcance y riesgos residuales

- ROOT-028 conserva la responsabilidad de propagar semantica de interaccion.
- ROOT-029 conserva freshness, generation, timestamp y ownership perceptivos.
- ROOT-050/051/052 conservan descriptor ranking, duplicados y thresholds.
- RAIN, TEARS y CÉRBERO no se integran. Un estado subjetivo futuro puede variar
  ranking, preferencia, atencion o esfuerzo, pero nunca elevar `UNSAFE` o
  `INDETERMINATE` sin evidencia factual/de accion nueva.
- La validacion fisica en Unity sigue pendiente; ROOT-027 permanece
  `FIELD_PENDING` aun despues de una futura aprobacion humana del codigo.
