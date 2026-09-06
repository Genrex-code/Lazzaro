# ROOT-005 — Material action attestation

## 1. ROOT

`ROOT-005 — Consumer success lacks material attestation` queda `CLOSED_CODE / FIELD_PENDING`.
El cierre de codigo elimina las rutas conocidas de éxito físico por check/log. El gate de campo
permanece pendiente porque este packet no abrió Unity ni ejecutó Play Mode.

## 2. Causa

`LazzaroAgent` trataba la llegada al final de cuatro handlers como prueba de efecto. Encontrar un
`Collider`, leer parámetros o emitir `Debug.Log` terminaba directamente en
`ReportarResultadoAccion(..., true, ...)`, aunque el mundo no hubiera cambiado.

## 3. Evidencia histórica

En el commit de entrada `8071e307b0e6d8cc605e4ee7d0c5c703f7f1201e`,
`EjecutarInteraccion`, `EjecutarAgarrar`, `EjecutarSoltar` y `EjecutarAplicarFuerza` contenían ese
patrón. Interacción y agarre sólo buscaban/logueaban; release ni mantenía estado de objeto
sostenido; force ni resolvía un `Rigidbody`.

## 4. Materialization map

| action | owner/materializer final | efecto | postcondición/source of success | clasificación final |
|---|---|---|---|---|
| `navegar_a_objeto` | `MovementController` | navegación física | distancia horizontal dentro de `stoppingDistance`, callback con `actionId` | materialized + attested (control) |
| `navegar_a_posicion` | `MovementController` | navegación física | llegada al destino, callback con `actionId` | materialized + attested (control) |
| `alejarse_de_objeto` | `MovementController` | navegación de escape | llegada al destino calculado, callback con `actionId` | materialized + attested (control) |
| `girar_avatar` | `MovementController` | `Rigidbody.MoveRotation` | rutina física completa, callback con `actionId` | materialized + attested (control) |
| `interactuar_objeto_3d` | target `IMaterialInteractable` | definido por la capability | receipt del componente con `effect_observed=true` | attested si existe capability; de otro modo unsupported |
| `agarrar_objeto` | `ObjectMaterializationController` | ownership + parent al hold point | referencia exacta sostenida cambió y parent es hold point | materialized + attested |
| `soltar_objeto` | `ObjectMaterializationController` | pierde ownership/parent de agarre | referencia exacta pasa a null y deja hold point | materialized + attested |
| `aplicar_fuerza` | `ObjectMaterializationController` + `Rigidbody` | `AddForce` | después de `FixedUpdate`, delta direccional compatible positivo | materialized + attested |
| `hablar` | `LazzaroAgent`/UI | salida visual | texto publicado; no es efecto físico | non-material |

## 5. Clasificación A/B/C/D

- A — materialized + attested: movimiento; grab/release/force cuando cumplen capability y
  postcondición; interact sólo cuando el target implementa `IMaterialInteractable`.
- B — materialized but unattested: no queda ruta aceptada en los cuatro handlers objetivo.
- C — not materialized: un target sin capability no se simula; retorna `UNSUPPORTED`.
- D — non-material/special: `hablar` conserva su contrato anterior.

## 6. Receipt contract

`MaterialEffectReceipt` es discriminado y transporta únicamente:

```text
action_id    string exacto
effect_kind  interact | grab | release | force
status       applied | no_effect | unsupported | failed
object_id    identidad pequeña del objeto
evidence     mapa plano de evidencia física mínima
```

Unity usa enums `MaterialEffectKind`/`MaterialEffectStatus`; Python usa los mismos discriminantes y la dataclass
`MaterialEffectReceipt`. No hay booleano desnudo desde el materializer. `action_id` sigue siendo
la correlación primaria y nunca se sustituye por nombre, FIFO o último objeto.

## 7. Attestation statuses

- `APPLIED`: existe postcondición observable compatible; permite `exito=True`.
- `NO_EFFECT`: se intentó o evaluó, pero la postcondición no apareció; exige `exito=False`.
- `UNSUPPORTED`: falta capability/Rigidbody utilizable; exige `exito=False`.
- `FAILED`: precondición o materializer falló; exige `exito=False`.

Mayúsculas inesperadas, truthiness, status/boolean contradictorios y receipts malformados se
rechazan como protocolo inválido.

## 8. Server-side validation

`Orchestrator._registrar_outcome_entrante()` obtiene primero la acción canónica del ledger y llama
a `validate_material_attestation()` antes de `ActionLedger.record_outcome()`. Para las cuatro
acciones materiales valida forma exacta, status, `action_id`, `effect_kind`, evidencia específica
y coherencia con `exito`.

Un fallo produce `OutcomeRecordStatus.INVALID_ATTESTATION`; no se escribe outcome y la acción
permanece `executing`. Por tanto no existe transición a `reported_success` ni llega el evento a
Behavior/Task, Identity o alias learning. No se amplió `ActionState` ni se creó otra máquina.

## 9. Interaction

Antes: target cercano → log → success. Después: target cercano → búsqueda de
`IMaterialInteractable` en el objeto → el propio componente materializa y observa → receipt.
`APPLIED` exige `effect_observed=true`, `interaction_type` y `observer`. HEAD no posee una
implementación concreta de esa capability, así que los objetos actuales responden
`UNSUPPORTED`. Mirar/inspeccionar no se redefinieron como física y no se inventó gaze system.

## 10. Grab

Antes: `Collider` cercano → log → success. Después: exige hold point configurado, affordance `agarrable` y `Rigidbody`;
el controlador conserva configuración previa, cambia ownership, vuelve el body cinemático, lo
parenta al hold point y observa:

```text
held_before != target
held_after == target exacto
held_reference_changed == true
parent_is_hold_point == true
```

Target inexistente, capability ausente, otro objeto sostenido, excepción o postcondición ausente
nunca producen success.

## 11. Release

Antes: leer `fuerza_lanzamiento` → log → success incluso sin objeto. Después sólo puede ser
`APPLIED` si había un objeto exacto sostenido y luego `held_after=null` y ya no está parentado al
hold point. Release sin objeto es `NO_EFFECT`. Una fuerza opcional se registra sólo como impulso
solicitado/aplicado; el success afirma liberación, no una trayectoria no observada.

## 12. Force

Antes: leer target/vector/fuerza → log → success; no había `Rigidbody`. Después exige affordance
`empujable`, `Rigidbody` dinámico, vector y magnitud finitos. Captura velocidad/posición, llama
`AddForce`, espera `WaitForFixedUpdate` y vuelve a observar. `APPLIED` requiere
`fixed_update_observed=true`, vectores before/after finitos y `directional_delta > Mathf.Epsilon`.
Se usa el epsilon del motor, no igualdad float exacta ni un umbral de gameplay inventado.

Sin body o body bloqueado es `UNSUPPORTED`; cero delta es `NO_EFFECT`; excepción/observación
inválida es `FAILED`. `AddForce` llamado no es evidencia suficiente.

## 13. Movement control

No se reescribió `MovementController`. Las cuatro acciones de movimiento siguen transportando el
ID exacto, ejecutando una coroutine física y reportando mediante `OnMovimientoCompletado` tras el
proceso. Las pruebas estáticas confirman que el callback sigue correlacionado por ID.

## 14. Hablar

`hablar` se clasifica `NON_MATERIAL`: no requiere `material_attestation` y conserva el outcome que
ROOT-004 dejó después de publicar la salida visual/log. No se usa como prueba del contrato físico.

## 15. Material failure vs protocol failure

`NO_EFFECT`, `UNSUPPORTED` y `FAILED` son outcomes materiales tipados y terminalizan como el
`reported_failure` ya definido por ROOT-004; su status permanece dentro del payload para no
confundirlos con success. Un receipt ausente, forjado o incompatible es `protocol_invalid`: no
terminaliza ni enseña éxito o fracaso del mundo.

## 16. Zero fake success evidence

Los cuatro handlers ya no contienen llamadas directas a
`ReportarResultadoAccion(contexto, true, ...)`, ni producen outcome desde `Debug.Log` o un parseo.
Todos llaman `ReportarResultadoMaterial`, que sólo reporta un receipt internamente válido. Core
repite la validación de forma independiente para no confiar en el cliente.

## 17. Tests

`core/test_material_action_attestation.py` cubre 31 pruebas permanentes: APPLIED válido, success
sin receipt, NO_EFFECT, UNSUPPORTED, FAILED, IDs/efectos cruzados, enum case-sensitive, bool/string
ambiguo, evidencia insuficiente, interaction/grab/release/force, duplicate, unknown, payload
mínimo y gates de Task/Identity/alias.

Resultado focal: `31/31 PASS`.

## 18. Unity static/harness validation

No hay proyecto Unity/Test Framework o `.csproj` en el repo; no se descargó tooling. El harness
offline analiza cuerpos completos, no cuenta solamente palabras: prueba que los cuatro handlers
usan el camino del receipt, que no contienen success literal y que grab/release/force observan sus
postcondiciones. Llaves balanceadas y contrato estático C# quedan como code gate; no equivalen a
Play Mode.

El host posee `dotnet`, pero no `csc` independiente y el repo no contiene `Packages/manifest.json`;
por eso no se fingió compilación Unity. Los probes históricos de DTO/Frontiers/Capa 1 también se
ejecutaron y conservan fallos por aserciones anteriores a ROOT-003/004 (campos DTO viejos y barrera
que prohíbe incluso SQLite `:memory:`). No fueron reescritos ni usados para ocultar resultados; el
probe de regresión vigente y el harness ROOT-005 son los gates del packet.

## 19. Field-required status

`FIELD_REQUIRED = PENDING`. El field gate posterior debe comprobar en Unity real: cambio de held
al grab, pérdida de held al release, respuesta física de force, una interacción con capability y
que no-op no reporte success. Este packet no abrió Unity, no usó hardware y no afirma validación
física real.

## 20. Crash windows

Permanecen deliberadamente fuera de ROOT-005:

- efecto material observado → proceso cae antes de enviar outcome: ledger queda `executing`;
- outcome enviado → cliente no sabe si llegó: ROOT-054 sigue dueño del transporte/retry;
- outcome durable → consumidores → `consumed_at`: ROOT-007 sigue dueño del Unit of Work;
- no existe rollback físico ni exactly-once distribuido.

## 21. ROOT-003 regression

La identidad exacta action/outcome, unknown, duplicate, late/reordered, restart y alias por
`id_accion` permanecen bajo el ledger existente. Resultado: `29/29 PASS`.

## 22. ROOT-004 regression

No cambió la semántica de `created`, authorization, dispatch, executing ni estados terminales.
Sólo se añadió una clasificación de outcome previa al ledger. Resultado: `48/48 PASS`.

También permanecen verdes ROOT-012 `15/15`, ROOT-006 `21/21`, ROOT-001 `14/14` y ROOT-002
`13/13`.

## 23. Neighboring roots

No se iniciaron ni cerraron `ROOT-007`, `ROOT-011`, `ROOT-015`, `ROOT-054`, `ROOT-029` o
`ROOT-038`. Capa 2, TCM, Persona y cualquier sistema nuevo quedaron intactos.

## 24. Criterion

El code gate satisface la regla:

```text
intent
→ materializer owner
→ observed postcondition
→ MaterialEffectReceipt APPLIED con action_id exacto
→ validación server-side
→ reported_success
```

La ruta `intent → check/log → true` ya no existe para los cuatro handlers. AuditTrace v1 no fue
ampliado; el receipt pequeño vive en el outcome del ledger.

## 25. Final state

`ROOT-005 = CLOSED_CODE / FIELD_PENDING`.

Regression global tras añadir ROOT-005: 332 unidades, 331 PASS, 1 KNOWN_XFAIL histórico, 0 fallos
inesperados; suite gate e isolation gate PASS. El cierre definitivo de campo se reserva para el
controlled field test global posterior.
