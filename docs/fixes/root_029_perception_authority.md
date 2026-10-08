# ROOT-029 — Autoridad perceptiva para acciones físicas

Estado máximo de este packet: `IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING / FIELD_PENDING`.

ROOT-029 convierte la percepción que fundamenta una acción física en una cadena comprobable de hechos y recibos. No cambia la política de seguridad de ROOT-027, la semántica de interacción de ROOT-028 ni el fail-closed de ROOT-001.

## A — Datos canónicos

`PerceptionDataV1` y `PerceivedObjectDataV1` son modelos estrictos, congelados y con campos extra prohibidos. Los vectores deben estar completos; `0` y `0.0` se conservan y no se confunden con ausencia. Se rechazan booleanos usados como números, strings numéricos, `NaN` e infinitos. El parser copia sólo los campos allowlisted, de modo que una mutación posterior del payload original no modifica el snapshot aceptado.

Un payload sin el contrato moderno completo recibe `LEGACY_DEGRADED`; uno que pretende ser moderno pero no valida recibe `INVALID`. Ninguno puede autorizar una acción física dirigida a objetos.

## B — Identidad

El contrato separa:

- `producer_instance_id`: identidad estable y configurada del productor real;
- `producer_object_id`: identidad persistente opcional dentro de `logical_world_id`;
- `runtime_object_id`: handle exacto válido dentro de un epoch;
- `display_name` y aliases: presentación, nunca autoridad física;
- `producer_capture_id`: identidad opaca de una captura concreta.

Registros duplicados sólo pueden seguir adelante si resuelven al mismo target y coinciden en todos los hechos relevantes para la acción. Un conflicto produce `AMBIGUOUS`/`CONTRADICTORY` y denegación; nunca se elige el primero, el más cercano ni el nombre más parecido.

## C — Lifecycle y scope

`PerceptionLifecycleTracker` es inyectado por el composition root. Mantiene estado acotado por `producer_instance_id + stream_id`, con lock independiente por stream. El productor Unity inicia cada epoch en generación `0`; el receptor, en cambio, acepta como `FIRST_OBSERVED` cualquier generación no negativa porque puede arrancar tarde o perder entregas. Cada captura posterior avanza una vez y una retransmisión conserva capture ID y generación.

El recibo distingue advance, gap, repeat, out-of-order y colisiones, además de cambios de epoch y mundo lógico. Conserva historia acotada de epochs retirados: un replay de un epoch conocido es `OLD_EPOCH_REPLAY` y jamás reemplaza al actual; si la historia necesaria ya fue expulsada, el resultado es `HISTORY_UNKNOWN`. Nunca se fabrica `NEW_CAPTURE`, repeat ni collision sin evidencia.

`logical_world_id` es un namespace lógico persistente explícito. No se deriva del nombre de escena.

## D — Frescura

Los tres tiempos tienen autoridades diferentes:

- `captured_at_utc_ms`: productor;
- `received_at_utc_ms`: primer boundary receptor, acuñado una sola vez;
- `evaluated_at_utc_ms`: reloj inyectado en la evaluación.

`FreshnessPolicyV1` suministra un ID, edad máxima, skew futuro y regla de supersesión. No hay umbrales productivos por defecto. Replay y reutilización de cache conservan la captura original y por tanto no rejuvenecen evidencia.

Un payload sin tiempo de captura canónico sólo proporciona `RECEIVE_TIME_PROXY/PARTIAL`. En V1 ese proxy siempre se deniega para acciones físicas dirigidas por percepción, aunque el mensaje acabe de llegar.

## E — Autoridad

`PerceptionAuthorityEvaluator` es el único compositor semántico. Recibe evidencia A–D correlacionada, `MotorResolution`, affordance y `SafetyAssessmentV1`; Orchestrator sólo impone el resultado y proyecta la acción autorizada.

La procedencia de parámetros es code-owned y nace de `TrustedParameterPathV1`, no del nombre de la acción ni de metadata de usuario/modelo. El parser Motor determinista puede acreditar coordenadas explícitas; coordenadas producidas por Reasoning/LLM quedan `UNKNOWN`. Una resolución Motor ligada a percepción produce `PERCEPTION_TARGET`. `PERCEPTION_TARGET` requiere autoridad; `UNKNOWN` queda `INDETERMINATE` y sin dispatch; `NOT_REQUIRED` sólo significa que ROOT-029 no gobierna ese camino, no que exista autorización física general.

La matriz semántica distingue evidencia incompleta de contradicción. Identidad `PARTIAL`, `ABSENT` o `AMBIGUOUS` sin conflicto queda `INDETERMINATE`; identidad `CONTRADICTORY` o duplicate-state conflict queda `DENIED`. Lifecycle `ABSENT`/`HISTORY_UNKNOWN` queda `INDETERMINATE`; lifecycle `CONTRADICTORY` queda `DENIED`. Ambos estados siguen siendo fail-closed.

Para autorizar se exige:

- datos canónicos;
- identidad exacta resuelta;
- lifecycle resuelto;
- política explícita y frescura satisfecha;
- correlación de ingest/capture/generation/producer/stream/epoch;
- grounding `ready`;
- affordance aplicable;
- ROOT-027 `SAFE` o `CAUTION` con reason code;
- subtipo ROOT-028 cuando la interacción lo requiere;
- schema físico válido;
- `runtime_object_id` exacto; si existe ID persistente, también `logical_world_id`.

Cualquier `DENY` produce `DENIED`; sin denegaciones, cualquier evidencia incompleta produce `INDETERMINATE`; sólo todos los gates en PASS producen `AUTHORIZED`.

## Binding de acción

`id_accion` se preasigna antes de evaluar. Un `authority_decision_id` identifica esa evaluación y se liga uno-a-uno a la acción. El SHA-256 canónico incluye versión, action ID/nombre, parámetros validados, target/scope/capture/generation, subtipo, disposición y reason de seguridad, policy IDs y generation mode.

El hash es evidencia backend/ActionLedger de integridad y correlación; no es firma, autenticación de red, frescura, seguridad ni autoridad. Python comprueba que el `AuthorizedPhysicalActionV1` congelado coincide con su referencia. Unity no valida ni recomputa este SHA en V1: verifica estructuralmente action ID, nombre, parámetros canónicos, coherencia capture/generation, generation mode, subtipo, scope y target exacto. Si en el futuro C# verifica el SHA, deberán añadirse vectores compartidos antes de habilitarlo.

## Unity

`PerceptionLifecycleContext` conserva un `producer_instance_id` serializado, crea un epoch opaco por namespace runtime y acuña capture ID + generation bajo el mismo lock. Una identidad de productor vacía hace fallar el contrato en backend.

`ExactTargetResolver` compara `runtime_object_id` por igualdad exacta, rechaza ausencia y conflicto entre identidades físicas, valida productor/stream/epoch/mundo y comprueba que el ID persistente opcional pertenece al mismo `GameObject`. Varios colliders del mismo `GameObject` se agrupan como una sola identidad runtime; no se altera ni deduplica el snapshot Block-A. La ruta con autoridad no usa substrings, aliases, nombres, nearest ni retarget. MovementController recibe un collider representativo del objeto ya resuelto una sola vez.

La validación C# de este packet es estática/sintética. No afirma Unity runtime, Play Mode ni campo.

## ActionLedger, cache y observabilidad

La migración de ActionLedger V3→V4 es aditiva: agrega `authority_decision_id`, `authority_reference_json` y `action_binding_sha256`. Esos campos participan en idempotencia, de modo que el mismo action ID con otro binding es conflicto. El state machine existente no cambia.

BehaviorCore conserva capture ID, epoch, generation, captured time e ingest original al reutilizar `_ultima_percepcion`. AuditTrace v1 permanece congelado; la evidencia ROOT-029 viaja en metadata interna, acción autorizada, referencia mínima, ActionLedger y contexto de ejecución Unity.

## Configuración productiva pendiente

Antes de cualquier prueba de campo deben configurarse conscientemente los cinco valores siguientes:

```text
PERCEPTION_FRESHNESS_POLICY_ID
PERCEPTION_MAX_CAPTURE_AGE_MS
PERCEPTION_MAX_FUTURE_SKEW_MS
PERCEPTION_AUTHORITY_POLICY_ID
PERCEPTION_GENERATION_BINDING_MODE
```

Si falta uno, no se construye política productiva y las acciones físicas dirigidas por percepción terminan `INDETERMINATE`, con cero dispatch.

## Límites y FIELD_PENDING

ROOT-029 autoriza el inicio, no reautoriza continuamente durante el movimiento. La remoción de `FIELD_PENDING` requiere, en un entorno controlado y con aprobación separada:

1. política productiva humana configurada;
2. Unity Play Mode con productor estable no vacío;
3. matriz combinada ROOT-027/028/029 para SAFE, CAUTION, UNSAFE e INDETERMINATE;
4. target desaparecido, duplicado, epoch/world/productor incorrectos y no-retarget verificados en runtime;
5. los seis subtipos de interacción comprobados extremo a extremo;
6. outcome correlacionado con el mismo `id_accion` y authority decision;
7. confirmación de cero movimiento/contacto ante cualquier gate no satisfecho.

Hasta entonces no se afirma `FIELD_COMPLETE` ni ejecución física verificada.
