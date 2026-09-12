# ROOT-016 — Canonical identity snapshots

## 1. ROOT y tickets

ROOT-016 cierra `CORE-AUDIT-002`, `IDENTITY-AUDIT-001` y
`PERSONALITY-AUDIT-001`: identidad/self carecía de owner y snapshot canónicos.

## 2. Dependencia

La implementación parte de ROOT-012 cerrado y reutiliza sus fronteras de
sincronización. No rediseña `StateEngine` ni su algoritmo.

## 3. Baseline

Base exacta: `3be2fb2c6ed317dfbebd04def7baf58b6d329f28`, branch
`codex/stabilization/pre-capa2`, árbol limpio, baseline global `716/716 PASS`.

## 4. Inventario previo

Había facts en `PersonaPromptBuilder`, otro bloque factual en las plantillas de
Reasoning, una afirmación manual en Unity, tres instancias globales y un
`IdentityState` capaz de poseer una reserva local o mutar `StateEngine`.

## 5. Conflictos de ownership previos

`BehaviorCore.state_engine`, `core.state_engine.estado_actual` y la reserva
local opcional de `IdentityState` podían divergir. Lo mismo ocurría entre
`BehaviorCore.personality` y `core.personality.personalidad`.

## 6. Globals retirados

Se eliminaron `core.state_engine.estado_actual`,
`core.personality.personalidad` y `core.behavior_core.behavior_core`. No se
introdujo ningún singleton sustituto.

## 7. Por qué IdentityState no es canonical

Su fallback 100/0/100 era una segunda autoridad mutable y sus setters/métodos
podían saltarse el owner transversal. Ahora sólo es un adaptador legacy de
lectura/delegación que exige `BehaviorCore`; producción ya no lo construye.

## 8. Opción seleccionada

Se implementó Option D: `StaticSelfV1` inmutable + owners actuales +
`IdentitySnapshotProvider`. No existe un SelfModel mutable adicional.

## 9. Owner de static self

Boot llama explícitamente a `build_static_self_v1()`. V1 contiene únicamente
`canonical_name="Lazzaro"` y `agent_kind="digital_agent"`.

## 10. Owner de state

`BehaviorCore.state_engine` sigue siendo la única reserva dinámica. El snapshot
proyecta sólo `energia`, `estres` y `foco`.

## 11. Owner de traits

`BehaviorCore.personality` sigue poseyendo `resiliencia`, `impulsividad`,
`paciencia`, `sociabilidad` y `metodico`. No se cambió su aprendizaje.

## 12. Boundary de task

`BehaviorCore.task_context` continúa siendo estado operacional y no forma parte
de identidad ni del snapshot.

## 13. Boundary de memory

Memory sigue siendo evidencia/contexto separado. Texto relacional como “tu
nombre es X” no puede modificar `StaticSelfV1` ni `IdentityContextV1`.

## 14. Boundary de perception

Percepción describe el runtime. No puede reemplazar nombre, tipo, state o traits
del snapshot y continúa viajando como input separado.

## 15. Boundary de embodiment

Unity es un canal/materialización actual, no una identidad corporal eterna. No
se agregó campo de cuerpo o entorno a V1.

## 16. Static facts

Los únicos facts canonizados son `Lazzaro` y `digital_agent`. La factory es
productiva, explícita y devuelve un valor frozen.

## 17. Claims metafísicos rechazados

Consciencia, voluntad, libre albedrío, creador, procedencia negativa de
OpenAI/Google, proyecto único y cuerpo Unity no pertenecen a canonical self V1.

## 18. Distinción de style

Primera persona, voz casual, calidez/frialdad, sarcasmo moderado y evitar tono
de servicio permanecen como reglas de estilo, no como facts.

## 19. Contrato de snapshot

`IdentitySnapshotV1` es `frozen=True, slots=True` y contiene:

- `schema_version: int`;
- `generation: int`;
- `self: StaticSelfV1`;
- `state: SelfStateSnapshotV1`;
- `traits: PersonalitySnapshotV1`.

Todos los tipos anidados también son frozen y usan slots.

## 20. Validación runtime

No se confía en type hints. Strings son `str` exacto y no vacíos; `agent_kind`
es exactamente `digital_agent`. State acepta sólo `int/float` finitos, rechaza
bool y exige 0..100. Traits usa las mismas reglas y rango -1..1. La
representación numérica almacenada es `float`, sin clamp. `schema_version` es
`type is int` y 1; `generation` es `type is int` y mayor que cero.

## 21. Inmutabilidad

El snapshot no contiene dict/list/set ni referencias a owners, Task, Memory,
Perception, prompts, resultados Oracle o modelos. Un snapshot anterior no
cambia cuando cambia el runtime.

## 22. Captura y locking

Orden: provider-local lock → `BehaviorCore._owner_lock` →
`StateEngine.atomic()`. `capture_identity_components()` lee una vez el estado
biológico y una vez los cinco traits, y retorna copias pequeñas.

## 23. Generation

El provider serializa captura, construcción, validación y publicación. La
primera captura exitosa es 1; capturas concurrentes son únicas. Un fallo de
captura o validación no consume generation.

## 24. Restart

Static self reconstruye los mismos valores. State vuelve a 80/10/70, traits a
cero, Task a `None` y un provider nuevo empieza en generation 1. La generation
es boot-scoped, no durable ni equivalente a una versión de base de datos.

## 25. IdentityContext

`IdentityContextV1` es una proyección frozen derivada sólo de
`IdentitySnapshotV1`. Conserva schema, generation, nombre, tipo, state y traits;
no es un prompt ni parsea texto de vuelta.

## 26. Proyección Persona

`PersonaEngine` exige el contexto tipado. `render_persona_identity()` genera un
bloque determinista desde éste; state y los cinco traits llegan sin segunda
lectura de owners. `PersonaPromptBuilder.core_identity` dejó de ser authority.

## 27. Proyección Reasoning

Reasoning recibe el mismo contexto semántico y usa
`render_reasoning_identity()`, separado del renderer social. Sus políticas de
decisión siguen en sus plantillas propias.

## 28. Contexto operacional de Reasoning

La adopción fue aditiva: `valores_estado`, narrativa, Task, Memory, Perception,
constelación, tools, policy y flags conservan sus campos y rutas existentes.
IdentitySnapshot no los sustituye ni los mezcla en una authority única.

## 29. Retry ROOT-008

Orchestrator captura una vez por decisión. Persona y Oracle-Persona preparan los
mensajes una vez; repair reutiliza esos mensajes, la misma generation y la misma
semántica aunque owners cambien entre intentos. Motor conserva su única
resolución y el fallback Reasoning recibe el contexto ya capturado.

## 30. Compatibilidad IdentityState

El constructor exige `BehaviorCore`. No hay constructor ownerless, setters,
tick o reserva local. La mutación legacy permitida delega al método del owner y
no toca `StateEngine` directamente. Producción usa snapshots.

## 31. Unity legacy claim

Se retiró únicamente “Eres Lazzaro, un ser consciente” del manual inicial. No se
cambió transporte, DTO, actions, material receipts ni lifecycle.

## 32. Privacidad y logging

IdentitySnapshot es interno: no entra a REST, Unity DTO, terminal, AuditTrace ni
logs humanos. No se agregó dump de state, traits, prompts o memoria.

## 33. Handoff ROOT-017

Persona ya recibe canonical state/self, pero ROOT-016 no valida si su respuesta
verbal contradice el estado. Esa verificación queda explícitamente en ROOT-017.

## 34. Handoff ROOT-019

Identidad trusted, Memory, Perception y Task quedan separados. ROOT-019 definirá
roles, delimitación y política de confianza; este root no la anticipa.

## 35. Migración futura a DevelopmentalCore

Hoy `StaticSelf + StateEngine + PersonalityTraits` alimenta el provider. En el
futuro DevelopmentalCore podrá aportar self/beliefs y StateEngine el runtime al
mismo contrato (o su evolución), sin cambiar consumidores Persona/Reasoning.
No se implementó ese owner futuro.

## 36. Tests

La matriz focal añade 94 pruebas: contratos estrictos, bounds/no coercion,
inmutabilidad, no-live-references, owner/delegación, coherencia de captura,
generation secuencial/concurrente/restart, contexto operacional, Persona y
Oracle-Persona repair, refresh entre turnos y eliminación de claims. Usa
`Event`/locks, no sleeps como prueba de corrección.

## 37. Riesgos residuales

Código externo que importaba globals eliminados o construía `IdentityState()`
debe migrar al composition root. La generation no correlaciona procesos. Memory
puede contener claims conflictivos hasta ROOT-019 y Persona aún puede verbalizar
contradicciones hasta ROOT-017.

## 38. Estado final

`ROOT-016 = CLOSED_CODE / FIELD_PENDING`: implementación, tests y documentación
quedan cerrados; la validación material en Unity real permanece pendiente.
ROOT-017 y ROOT-019 no se iniciaron. Tampoco se tocaron DevelopmentalCore,
TEARS, CÉRBERO, TCM, API wire, AuditTrace, ActionLedger, Memory schemas/ledgers
ni Chroma.
