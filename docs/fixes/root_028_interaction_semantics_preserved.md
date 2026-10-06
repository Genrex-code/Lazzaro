# ROOT-028 — Semantica de interaccion preservada durante grounding

## Estado

`IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`

ROOT-028 corrige la perdida historica de `tipo_accion` antes de autorizar una
interaccion. El resolver fuzzy evaluaba affordance y seguridad sin el subtipo y
Motor lo volvia a inferir despues, por lo que la accion autorizada podia no ser
la misma que finalmente se enviaba a Unity.

Tambien existian tres fuentes de semantica fabricada:

- el parser textual convertia todo caso no reconocido en `inspeccionar`;
- los schemas Python convertian la omision en `mirar`;
- Unity convertia la omision en `mirar`.

Esos defaults fueron eliminados.

## Invariante canonico

```text
evidencia valida
→ InteractionType | None
→ FuzzyObjectResolver
→ AffordanceResolver + SafetyAssessmentV1
→ MotorResolution
→ parametros finales
→ schema Motor
→ Unity
```

Para toda accion despachable:

```text
interaction_type validado
== MotorResolution.interaction_type
== parametros.tipo_accion
== tipo_accion recibido por Unity
```

`MotorResolution` es un portador canonico local al turno, no almacenamiento
durable ni mecanismo de continuacion entre turnos.

## Vocabulario

`InteractionType`, definido una sola vez en `ai_engine/motor_resolution.py`,
contiene:

```text
mirar
tocar
inspeccionar
golpear
usar
activar
```

Los schemas Motor y Tool reutilizan ese enum. Unity conserva exactamente el
mismo allowlist y recibe el string canonico sin traduccion.

## Parser textual y fronteras

El parser normaliza mediante `SpatialReasoner.normalizar_texto()`, tokeniza y
compara tokens completos. No busca substrings.

Mapea:

| Evidencia textual | Tipo canonico |
|---|---|
| `mira`, `mirar`, `observa`, `observar` | `mirar` |
| `inspecciona`, `inspeccionar` | `inspeccionar` |
| `toca`, `tocar` | `tocar` |
| `golpea`, `golpear` | `golpear` |
| `usa`, `usar` | `usar` |
| `activa`, `activar` | `activar` |

`interactua/interactuar` identifica la accion general, pero no inventa un
subtipo. Multiples sinonimos del mismo tipo son aceptables; dos tipos distintos
son ambiguos y producen `None`.

Casos como `admirar`, `observador`, `retocar`, `golpeador`, `causa`,
`desactivar` y `activamente` no coinciden accidentalmente. `mira alrededor`
conserva su semantica previa de giro.

## Estructurado frente a texto

La precedencia es:

```text
estructurado valido
> evidencia textual explicita no ambigua
> None
```

La presencia del campo se distingue de su ausencia:

- ausente: el texto puede suministrar evidencia;
- presente y canonico: es autoridad y el texto no lo reemplaza;
- presente pero invalido: `INVALID_PARAMETERS` antes de Fuzzy, sin rescate
  lexico.

Los valores estructurados no se normalizan. `MIRAR`, ` mirar `, `observa`,
booleanos, numeros, `None`, vacio y strings arbitrarios son invalidos.

## Grounding, affordance y seguridad

`FuzzyObjectResolver.resolve()` y `_score_objects()` reciben el
`InteractionType | None`. Para cada candidato, `validate()` y `score()` reciben
el mismo valor canonico. El `SafetyAssessmentV1` anidado se deriva de esa misma
evaluacion, sin parser secundario.

ROOT-027 permanece congelado:

| Interaccion peligrosa | Disposicion | Reason code | Penalizacion |
|---|---|---|---:|
| mirar / inspeccionar | `CAUTION` | `HAZARD_OBSERVATION` | 5 |
| tocar / golpear | `UNSAFE` | `HAZARD_CONTACT` | 20 |
| usar / activar | `UNSAFE` | `HAZARD_MANIPULATION` | 20 |
| sin subtipo | `INDETERMINATE` | `HAZARD_INTERACTION_UNSPECIFIED` | 20 |

Una interaccion generica no peligrosa puede recibir `SAFE / NO_HAZARD`, pero
queda `requires_clarification`: seguridad permitida no completa la intencion.
Una generica peligrosa conserva el bloqueo mas fuerte `unsafe` derivado de
`INDETERMINATE`.

La decision de completitud precede al bloqueo por affordance generica. Si falta
el subtipo, una affordance `empujable`, `agarrable` o incluso la ausencia de una
affordance generica no puede convertir la resolucion en `not_allowed`: el
resultado sigue siendo `requires_clarification / interaction_type_missing`.
La unica precedencia mas fuerte es un `SafetyAssessmentV1` anidado con
`UNSAFE` o `INDETERMINATE`, que conserva `unsafe` y permanece no fisico. Las
interacciones con subtipo explicito siguen aplicando el bloqueo normal de
affordance.

La completitud no sustituye el grounding del objetivo. Tras un bloqueo de
seguridad, las decisiones `below_clarify_threshold`, `close_candidates` y
`candidate_correction` conservan su causalidad original; solo un candidato ya
plausible, no ambiguo y sin correccion pendiente puede terminar en
`interaction_type_missing`.

Score, aliases y orden de candidatos no pueden crear ni cambiar el subtipo.

## Construccion y schemas

`_parametros_desde_resolucion()` ya no vuelve a interpretar el mensaje. Solo
proyecta `MotorResolution.interaction_type`. Si falta el subtipo, no construye
parametros de interaccion despachables.

Como defensa estructural, `MotorResolution` rechaza toda construccion `ready`
de `interactuar_objeto_3d` cuyo `interaction_type` sea `None`. Las resoluciones
no listas pueden conservar `None` para representar aclaracion o bloqueo, y las
acciones no interactivas siguen rechazando cualquier subtipo.

`InteractObjectParams.tipo_accion` y el schema de
`tools/simulated/interaccion_sim.py` son requeridos, cerrados y no tienen
default. Esta es una incompatibilidad conductual intencional para productores
que anteriormente omitian el campo.

La ruta legacy conserva tipos explicitamente demostrados, pero una interaccion
generica queda no fisica y solicita aclaracion. No restaura el antiguo fallback
`generic → inspeccionar`.

## Unity fail-closed

`LazzaroAgent.EjecutarInteraccion()` valida `tipo_accion` antes de buscar el
objeto o llamar `TryInteract`:

- ausente/vacio: receipt `Unsupported` con `interaction_type_missing`;
- no-string/no canonico: receipt `Unsupported` con
  `interaction_type_unsupported`.

Ambos usan el `resultado_accion` y la attestation material existentes con
`exito=false`. Nunca sustituyen el valor por `mirar` ni ejecutan interaccion.

La verificacion automatizada de C# es estatica; no equivale a Play Mode ni a
evidencia fisica.

## Pruebas y exclusiones

Las pruebas permanentes cubren vocabulario/sinonimos, fronteras negativas,
ambiguedad, precedencia, invalidos antes de Fuzzy, propagacion por resoluciones
ready y no-ready, matrices hazard/no-hazard, score 100, aliases, orden de
candidatos, equivalencia directa/estructurada, schema Tool/Motor y contrato
estatico Unity.

No se modifican AuditTrace, Orchestrator, API, perception models, Triage,
Memory, ROOT-029 ni ROOT-050/051/052. ROOT-027 conserva sus contratos y sigue
`FIELD_PENDING`.

## Riesgos residuales y campo

- No hay implementaciones concretas de `IMaterialInteractable` en el arbol
  inspeccionado; la semantica material debe verificarse en Unity.
- No existe reanudacion automatica de candidate correction entre turnos;
  ROOT-028 no crea persistencia para ella.
- La ruta legacy conserva menor evidencia perceptiva que el snapshot moderno.

Una sesion controlada futura debe comprobar observacion sin contacto, bloqueo
de contacto/manipulacion peligrosa, aclaracion generica y paridad exacta entre
el subtipo validado por Python y el recibido por Unity. No se ejecuto Play Mode,
movimiento, contacto ni actuadores durante ROOT-028.
