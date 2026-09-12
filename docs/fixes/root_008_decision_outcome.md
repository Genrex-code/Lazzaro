# ROOT-008 — Estado tipado y provenance de decisión

## 1. Estado

`CLOSED_CODE / FIELD_PENDING`. La implementacion, tests offline, documentacion y
regresion de codigo quedan cerrados. No se ejecuto Unity Play Mode ni prueba de
campo real.

## 2. Tickets y dependencia

ROOT-008 cierra `AI-CONTRACT-002`, `MOTOR-CONTRACT-001` y
`PERSONA-AUDIT-002`. Consume el resultado estricto de ROOT-020. No implementa
ROOT-016, ROOT-017, ROOT-018, ROOT-019, ROOT-021, ROOT-029, ROOT-038 ni ROOT-042.

## 3. Problema anterior

`ActionResponse.valido` mezclaba payload utilizable, parse valido, decision
primaria, fallback y capacidad de ejecucion. El adaptador Persona descartaba
error/retry/fallback, Motor reducia `ready`, `requires_clarification`,
`not_found`, `not_allowed` y `unsafe` a acciones indistinguibles, y la API
convertia cualquier `none` en `accion=null` sin explicar la causa.

El retry Persona reconstruia un prompt minimo. Conservaba mensaje y restriccion
de canal, pero perdia identity/state, memoria, percepcion y contenido Oracle del
primer intento.

## 4. Autoridad interna

`ai_engine/decision_outcome.py` introduce `DecisionOutcome v1`, frozen y con
runtime invariants. Sus dimensiones ortogonales son:

- `disposition`: `action`, `no_action`, `clarification`, `blocked`, `invalid`,
  `error`;
- `quality`: `validated`, `salvaged`, `deterministic_fallback`, `rejected`,
  `failed`, `legacy_unknown`;
- `attempt`: `first`, `repair`, `legacy`;
- `layer`: `core`, `reasoning`, `persona`, `motor`, `oracle_persona`;
- razones finitas, `retry_count`, parse primario/final opcional y estado Motor
  opcional.

`ActionResponse.outcome` es la unica autoridad. Constructores anteriores reciben
explicitamente `legacy_unknown/legacy_untyped`; el boundary central clasifica
los productores legacy activos sin promover records durables a decisiones
primarias.

## 5. Significado de valido

`ActionResponse.valido` significa exclusivamente que su payload operacional es
seguro y utilizable. Una frase rescatada, un fallback verbal o un bloqueo Motor
pueden ser validos sin ser decisiones primarias. Parse invalido, parametros
fisicos invalidos y herramientas desconocidas siguen fallando el boundary
funcional conforme ROOT-007.

## 6. Invariantes

La version y el retry count usan enteros estrictos. Repair coincide exactamente
con un retry y exige parse primario `ERROR`; sin retry ambos parse status son
iguales. `VALIDATED`, `SALVAGED`, `REJECTED` y `FAILED` exigen combinaciones
coherentes. `motor_status` solo pertenece a Motor y fija la disposition esperada.
Enums, reason codes y cardinalidad de retry son finitos.

## 7. Persona y repair grounded

Persona construye el grounding una sola vez. El retry reutiliza una copia de los
mismos mensajes y agrega solamente:

1. la salida fallida, acotada a 1200 caracteres, como mensaje `assistant`;
2. una instruccion breve `user` para reparar el formato.

No vuelve a leer Identity, memoria, percepcion ni Oracle. El raw fallido no entra
en `DecisionOutcome`, API, ledger, AuditTrace ni logs nuevos. Se conserva el
maximo historico de un solo retry.

## 8. Quality de Persona

| Caso | Quality | Attempt | Parse primario/final |
| --- | --- | --- | --- |
| JSON canonico | `validated` | `first` | `VALID/VALID` |
| texto social rescatable | `salvaged` | `first` | `DEGRADED/DEGRADED` |
| repair valido | `validated` | `repair` | `ERROR/VALID` |
| repair rescatable | `salvaged` | `repair` | `ERROR/DEGRADED` |
| repair irrecuperable | `deterministic_fallback` | `repair` | `ERROR/ERROR` |

Oracle-Persona usa el mismo contrato y conserva el bloque Oracle ya incluido en
el primer grounding. Provenance de provider/model/cache sigue perteneciendo a
ROOT-018.

## 9. Estados Motor

| Estado observado | Disposition | Reason principal | Payload |
| --- | --- | --- | --- |
| `ready` | `action` | `motor_ready` | accion fisica |
| `requires_clarification` | `clarification` | `motor_clarification` | `hablar` |
| `not_found` | `blocked` | `target_not_found` | `hablar` |
| `not_allowed` | `blocked` | `action_not_allowed` | `hablar` |
| `unsafe` | `blocked` | `unsafe_block` | `none` |
| excepcion fuzzy traducida | `error` | `motor_resolution_failed` | `hablar` seguro |

Un `none` pasivo conserva `no_action/passive_no_action`; ya no se confunde con
un bloqueo inseguro. `estado_ejecucion` permanece como state machine independiente.

## 10. Proyeccion publica aprobada

`RespuestaAPI` agrega `decision`, independiente de `accion`, con exactamente:

```json
{
  "version": 1,
  "disposition": "blocked",
  "reason_code": "unsafe_block"
}
```

No expone quality, attempt, layer, retry, parse status, Motor status ni
provenance detallada. Para `no_action`, `blocked`, `invalid` y `error`,
`accion` puede seguir siendo `null`; `decision` conserva la diferencia. El
schema anidado de `AccionSaliente` no contiene `decision_outcome`.

## 11. Replay y compatibilidad

El snapshot durable guarda el `DecisionOutcome` finito junto al payload de
accion, sin cognicion ni texto libre. Cache RAM y replay de transaccion conservan
el mismo outcome. Records anteriores sin outcome se restauran como
`legacy_unknown/legacy_untyped`; solo se deriva `action` frente a `no_action`,
nunca calidad primaria. No hay migracion masiva.

## 12. Privacidad y errores

Pensamiento, razon interna, prompt, memoria, estado biologico, contenido Oracle,
raw LLM y excepciones no cruzan `DecisionPublica`. `PublicErrorEnvelope v1`
permanece exactamente `{version,status,error}` y la semantica HTTP de ROOT-007 y
ROOT-010 no cambia. AuditTrace v1 permanece congelado.

## 13. Clientes

Unity incorpora el DTO estatico `DecisionPublica` como campo top-level de
`RespuestaAPI`; no lo anida en `AccionSaliente`. Terminal conserva su contrato de
voz y no interpreta provenance privada. No se cambio dispatch, authorization ni
materializacion.

## 14. Pruebas de codigo

`ai_engine.test_decision_outcome` aporta 60 pruebas deterministas que cubren los
60 casos de diseño: Persona, grounding, Oracle verbalization, AI contract, cinco
estados Motor, parser general, invariantes, privacidad, wire y autoridad unica.
Usa fakes; no abre red, no consulta modelos reales y no toca Chroma activo.

Resultado de cierre:

- ROOT-008: `60/60 PASS`;
- ROOT-020: `55/55 PASS`;
- ROOT-013: `66/66 PASS`;
- ROOT-039: `41/41 PASS`;
- ROOT-009: API `15/15 PASS` y Unity static `10/10 PASS`;
- ROOT-010: API `18/18 PASS` y Unity static `8/8 PASS`;
- regresion global, que incluye ROOT-015, ROOT-014, ROOT-001, ROOT-004 y
  ROOT-007: `715/715 PASS`, `0 XFAIL`, `0 unexpected`, `0 import failures`;
- `compileall` y `git diff --check`: `PASS`;
- aislamiento: `PASS`; los hashes de los tres archivos Chroma activos
  permanecieron intactos.

Todas las pruebas fueron offline, con fakes o almacenamiento temporal. No hubo
red real, modelos reales, Unity Play Mode ni acceso de escritura a Chroma activo.

## 15. Field acceptance pendiente

En una sesion futura de Unity Play Mode se debe confirmar que el DTO deserializa
los seis valores de disposition, que `decision` sigue disponible con
`accion=null`, y que acciones fisicas/voz conservan dispatch y lifecycle. Hasta
entonces el estado de campo es `FIELD_PENDING`; no se afirma verificacion real.
