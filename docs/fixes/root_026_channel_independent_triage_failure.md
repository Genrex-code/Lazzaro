# ROOT-026 — Denegación independiente del canal ante fallos de Triage

Estado de implementación: `IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Problema histórico

La proyección legacy de ROOT-025 puede representar tanto `UNCERTAIN` como un
`FAILED` contenido mediante `UNKNOWN/persona`. Antes de ROOT-026, Core podía
continuar desde esa proyección y autorizar Persona o Reasoning según el canal.
Eso hacía posible que un fallo técnico de Triage alcanzara incluso rutas con
acciones físicas. El canal terminaba influyendo accidentalmente en la
autorización cognitiva.

## Frontera tipada

`core.triage_boundary.TriageBoundaryFailureV1` representa solamente el fallo
de la frontera entre Triage y Core:

- `contract_version`: entero estricto y exactamente `1`;
- `code`: `TRIAGE_EVALUATION_ERROR` o `TRIAGE_CONTRACT_ERROR`;
- `error_type`: identificador seguro de 1 a 96 caracteres que cumple
  `^[A-Za-z_][A-Za-z0-9_.]*$`.

El modelo es inmutable y rechaza campos adicionales. Una excepción de
`route_event()` produce `TRIAGE_EVALUATION_ERROR`. Un resultado que no sea un
diccionario, no tenga `classification`, no valide como `TriageDecisionV1` o
contradiga su proyección legacy estructural produce
`TRIAGE_CONTRACT_ERROR`. El `error_type` procede sólo del nombre seguro del
tipo; si éste no es válido se usa `UnknownError`. No se copian mensajes de
excepción, texto de evento, traceback ni etiquetas de proveedor/modelo.

## Validación y autoridad

Core invoca `route_event()` exactamente una vez. Valida la clasificación
anidada y reutiliza el único adaptador canónico de ROOT-025 con
`confidence=0.0`. Sólo compara los campos estructurales cuya autoridad no
depende de la confianza:

```text
intent
route
requires_memory
requires_oracle
```

`confidence`, `reason` y `human_reason` permanecen diagnósticos legacy, nunca
autoridad. Cualquier contradicción estructural se contiene antes del adaptador
de terminal, Memory, ToolPolicy, Persona, Reasoning, Motor u Oracle.

## Política independiente del canal

Core deniega autorización para:

```text
TriageDecisionV1.outcome == FAILED
TRIAGE_EVALUATION_ERROR
TRIAGE_CONTRACT_ERROR
```

La misma regla se aplica a `terminal`, `unity`, `sandbox`, `roblox` y cualquier
otro origen. Un `FAILED` sigue siendo una decisión válida de Triage: Core no lo
reclasifica como fallo de contrato y usa su `reason_code` finito como
`failure_class`. La proyección `UNKNOWN/persona` puede seguir existiendo por
compatibilidad, pero no autoriza trabajo posterior.

`UNCERTAIN` está expresamente fuera del alcance. También se conservan sin nueva
política `DECIDED`, `DEGRADED` y el `UNKNOWN` semántico aceptado. Los eventos
duros válidos, incluido `alerta_dolor_fisico → CORE_SYSTEM/motor`, siguen el
flujo normal.

## UoW y frontera pública

ROOT-026 eleva un `TurnProcessingError(component="triage", ...)` y deja que el
finalizador existente de UoW sea la única autoridad durable:

```text
efecto STARTED                              → RECONCILIATION_REQUIRED
efecto decision/execution/outcome APPLIED  → PARTIAL
sin lo anterior                            → ABORTED_CLEAN
```

Por ello no existe un HTTP universal para ROOT-026. `ABORTED_CLEAN` conserva
`500 / TURN_PROCESSING_FAILED`; `PARTIAL` y reconciliación conservan
`409 / RECONCILIATION_REQUIRED / pendiente`. Un efecto previo de Behavior no
se anuncia falsamente como revertido. Presentar el error al cliente sigue
siendo responsabilidad de API/terminal; autorizar subsistemas pertenece a
Core.

## AuditTrace y privacidad

Al alcanzar la frontera se escribe exactamente un stage `triage`. Para fallo
contenido sus campos autoritativos `intent`, `route` y `effective_route` son
`null`, aunque la proyección legacy dijera `UNKNOWN/persona`. La frontera
exterior conserva la propiedad exclusiva del stage `error`; `finally` añade
`event_end`. La secuencia esperada es:

```text
event_start → triage → error → event_end
```

No se fabrican stages `memory`, `decision` ni `execution`. Todos reutilizan el
mismo `trace_id`. El writer continúa siendo fail-open. La regresión con el
canario `ROOT026_CANARY_SECRET` prueba que el texto de excepción no aparece en
logs normales, AuditTrace, envelope público ni error visible del cliente.

## Pruebas y preservación

Las pruebas permanentes cubren el contrato estricto, todas las formas de fallo
de evaluación/contrato, independencia de `confidence`, exactamente una llamada
a Triage, las cinco procedencias, contadores cero para todos los subsistemas
prohibidos, los tres desenlaces UoW, secuencia de auditoría, writer fail-open,
privacidad, continuidad del REPL y mappings públicos condicionales.

ROOT-024 y ROOT-025 permanecen autoridades de límites lexicales y del contrato
de clasificación, respectivamente. No se modifican TriageRouter,
TriageDecisionV1, ActionLedger, AuditTrace, DTOs API/Unity, motores cognitivos,
Memory, entrenamiento ni artefactos de modelo.

## Riesgos residuales

- `UNCERTAIN` conserva intencionalmente su política compatibility debt hasta
  que un ROOT separado la redefina.
- AuditTrace v1 no persiste `outcome` ni `source`; el stage nulo distingue la
  denegación, pero no reemplaza una futura versión explícita del esquema.
- Trazas habilitadas deliberadamente por un desarrollador durante depuración
  local quedan fuera de la garantía; la garantía cubre la ruta productiva
  normal y sus payloads estructurados.
