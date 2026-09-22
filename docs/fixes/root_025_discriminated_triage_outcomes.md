# ROOT-025 — Discriminated Triage outcomes

## Estado

ROOT-025 reemplaza la ambigüedad interna de `UNKNOWN` con un contrato tipado.
El cambio no define todavía la política de routing para una excepción total de
Triage; ese boundary continúa perteneciendo a ROOT-026.

## TRIAGE-CONTRACT-001

Históricamente, el mismo shape legacy podía significar condiciones distintas:

- el modelo decidió legítimamente la clase entrenada `UNKNOWN`;
- el modelo tuvo baja confianza y el fallback no encontró una regla;
- el modelo no estaba disponible o falló y tampoco hubo fallback utilizable;
- el modelo emitió un label fuera del contrato;
- un constraint léxico eliminó todos los candidatos.

Todas podían terminar como `UNKNOWN/persona`. Confidence y `reason` no eran una
autoridad segura para reconstruir la causa.

## Autoridad interna

`TriageDecisionV1` separa dimensiones ortogonales:

```text
semantic_intent      qué intención fue clasificada
outcome              DECIDED / DEGRADED / UNCERTAIN / FAILED
source               autoridad que produjo semantic_intent
operational_override modificación operacional posterior
model_status         evidencia causal del intento ML
fallback_status      evidencia causal del fallback
reason_code          causa finita del resultado terminal
```

`classification`, la serialización canónica de `TriageDecisionV1`, es la
autoridad. Un `UNKNOWN` del dict legacy no siempre es semantic `UNKNOWN`.

El contrato es inmutable, rechaza campos extra y exige `contract_version` como
el entero exacto `1`. No contiene confidence.

## Outcomes

### DECIDED

Existe una decisión semántica utilizable. Incluye:

- event rules y reglas léxicas deterministas;
- labels compatibles aceptados por el modelo;
- fallback aceptado después de baja confianza o veto normal;
- la clase entrenada literal `UNKNOWN` cuando el modelo la acepta.

### DEGRADED

Existe una decisión fallback utilizable, pero el modelo estaba `UNAVAILABLE`,
produjo `ERROR` o emitió un `INCOMPATIBLE_LABEL`.

### UNCERTAIN

El pipeline funcionó, pero no sobrevivió ningún candidato aceptable. No tiene
`semantic_intent`, `source` ni `route`. Un `NO_MATCH` normal o un veto léxico no
son excepciones.

### FAILED

No existe decisión utilizable y hubo un fallo técnico contenido. Tampoco afirma
`semantic_intent`, `source` o `route`. Las excepciones internas no contenidas no
se convierten artificialmente en FAILED; siguen propagándose para ROOT-026.

## Sources y reglas

`source` significa exclusivamente la autoridad de la intención semántica:

```text
EVENT_RULE
LEXICAL
MODEL
FALLBACK
```

Los sources de regla conservan un `accepted_rule_id` estable. Los hard events
usan IDs `event.*`; los resultados léxicos reutilizan los IDs congelados por
ROOT-024.

Sin operational override, `route` debe coincidir exactamente con
`route_for_intent(semantic_intent)`.

## Operational overrides

### MOVEMENT_BLOCKED

El movement gate no reinterpreta lo que pidió el usuario:

```text
semantic_intent=MOTOR_SIMPLE
source=MODEL | FALLBACK | LEXICAL
route=noop
operational_override=MOVEMENT_BLOCKED
```

El dict legacy continúa proyectando `NOOP/noop`, pero el nested contract conserva
la intención Motor y su source. Un fallback degradado sigue siendo DEGRADED tras
el gate.

### EVENT_ROUTE_OVERRIDE

Sólo admite las combinaciones revisadas:

```text
EVENT_RULE + CORE_SYSTEM + noop
EVENT_RULE + CORE_SYSTEM + motor
```

Se usa para `inicio_simulacion` y `alerta_dolor_fisico`; no debilita la
invariante global de rutas.

## UNKNOWN entrenado y raw labels

La clase literal `UNKNOWN` continúa siendo semántica válida cuando:

```text
outcome=DECIDED
source=MODEL
model_status=ACCEPTED
raw_label=UNKNOWN
route=persona
```

Un label como `CLASS_NOT_IN_CONTRACT` se registra internamente como
`INCOMPATIBLE_LABEL`, activa fallback y nunca se presenta como semantic
`UNKNOWN`. `raw_label` está acotado a 128 caracteres, no contiene texto del
usuario y no cruza los DTO públicos.

## Estados causales

El modelo distingue `NOT_ATTEMPTED`, `UNAVAILABLE`, `ACCEPTED`,
`LOW_CONFIDENCE`, `ERROR`, `INCOMPATIBLE_LABEL` y `VETOED`.

El fallback distingue `NOT_ATTEMPTED`, `ACCEPTED`, `NO_MATCH`, `ERROR` y
`VETOED`.

La precedencia de fallo es:

```text
fallback ERROR                       → FALLBACK_ERROR
fallback NO_MATCH/VETOED + unavailable → MODEL_UNAVAILABLE
fallback NO_MATCH/VETOED + model error → MODEL_ERROR
fallback NO_MATCH/VETOED + bad label   → MODEL_LABEL_INCOMPATIBLE
low confidence/veto sin candidato      → NO_ACCEPTABLE_CANDIDATE
```

No se reconstruye ninguna de estas condiciones desde `human_reason`.

## Adapter legacy único

`TriageRouter.classify_event()` es la autoridad tipada.
`TriageRouter.route_event()` la invoca exactamente una vez y llama al adapter
canónico `TriageDecisionV1.to_legacy_dict()`.

La superficie compatible permanece:

```text
intent
route
confidence
requires_memory
requires_oracle
reason
classification
```

Confidence continúa siendo exclusivamente legacy y queda pendiente de ROOT-047.
No fue recalibrada, normalizada ni incorporada al contrato nuevo.

Mientras ROOT-026 no cierre la política, UNCERTAIN y FAILED conservan la
proyección superior `UNKNOWN/persona`. Esa proyección es deuda compatible, no
una política nueva. El outcome nested permanece inequívoco.

## Compatibilidad de eventos no soportados

La autoridad interna registra:

```text
DECIDED / EVENT_RULE / NOOP / noop / UNSUPPORTED_EVENT
```

La superficie legacy permanece deliberadamente:

```text
UNKNOWN / noop / 0.0
```

Ambas vistas se prueban simultáneamente para evitar cambios downstream dentro
de ROOT-025.

## Terminal, AuditTrace y wire

El adapter de terminal sólo cambia la route superior efectiva y conserva la
clasificación nested original. Unity y sandbox mantienen el resultado directo.

AuditTrace v1 no fue modificado y todavía no puede persistir outcome/source;
continúa observando `intent`, `route`, `effective_route`, `confidence` y `reason`.
Una ampliación requerirá contrato versionado separado.

No se modificaron DTOs API/Unity. `classification` permanece en el dict interno
Triage/Orchestrator y no se publica como receipt wire.

## Fault matrix cubierta

Las pruebas usan fakes locales para:

- clases conocidas y `UNKNOWN` literal con alta confianza;
- baja confianza con fallback aceptado o no-match;
- modelo unavailable;
- excepciones de `predict` y `predict_proba`;
- raw label incompatible;
- veto léxico con y sin alternativa;
- excepción real de fallback;
- hard events, empty command y unsupported event;
- movement gate normal y tras degradación técnica;
- excepción interna no contenida que sigue propagándose.

No hay red, reentrenamiento ni mutación del joblib.

## Exclusiones y handoff

- ROOT-026: boundary total y política de fallo independiente del canal.
- ROOT-046: provenance, evaluación y calidad del modelo.
- ROOT-047: dominio, finitud, escalas, threshold y calibración de confidence.
- ROOT-048: multi-intent.
- ROOT-024: reglas, fixture y autoridad léxica permanecen congelados.
- DevelopmentalCore, AuditTrace v2 y DTOs públicos no forman parte del cambio.

## Riesgos residuales

- La proyección temporal `UNKNOWN/persona` de UNCERTAIN/FAILED puede ser
  malinterpretada por consumidores que ignoren `classification`.
- AuditTrace v1 pierde todavía outcome/source.
- Los human logs históricos de algunos boundaries siguen incluyendo texto de
  excepciones; ROOT-025 no se amplió a una limpieza global de logging.
- Confidence sigue mezclando escalas hasta ROOT-047.
