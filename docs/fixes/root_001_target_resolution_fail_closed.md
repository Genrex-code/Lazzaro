# ROOT-001 — Resolución de target fail-closed

- Fecha: 2026-09-05
- Rama: `codex/stabilization/pre-capa2`
- Commit de entrada: `1c0aecb1765b218c33e90fce9b9e73d15f610342`
- Ticket absorbido: `MOTOR-SAFETY-001`
- Estado: `CLOSED`

## 1. Causa

`MotorResolution` no era autoritativa. Dos ramas de `MotorEngine` permitían que
`requires_clarification` o `not_found`, cuando tenían cero o un candidato,
volvieran al extractor legacy:

1. `_diagnosticar_accion_con_objeto()` construía parámetros legacy, devolvía
   `estado="lista"` y reemplazaba la metadata por `{}`;
2. `_enriquecer_parametros()` podía sustituir `nombre_objeto` por un nombre
   visible y también devolver metadata vacía.

Luego Orchestrator llamaba `procesar_comando_motor()` y una segunda resolución,
ya sobre el nombre sustituido, podía resultar `ready`. Además, una excepción del
resolver se convertía en `None`, indistinguible de “resolver moderno no
aplicable”, y habilitaba el mismo fallback. Un array moderno vacío tampoco se
consideraba percepción normalizada.

## 2. Evidencia histórica: peligro

Con un único `Cube`, aliases `cubo` y affordances `mirar,peligro`:

```text
"ve al cubo"
→ fuzzy requires_clarification (score 70)
→ singleton legacy permitido
→ diagnóstico lista, metadata={}
→ navegar_a_objeto(Cube), valido=True
→ AccionSaliente física
→ despachada
```

El probe histórico `capa1_motor_deep_probe.py` reprodujo el defecto sin enviar
nada a Unity. No se modificó ese probe.

## 3. Evidencia histórica: rojo → azul

Con request `ve al cubo rojo` y únicamente `Cube_azul` visible:

```text
resolución original = requires_clarification
candidate_correction = Cube_azul
→ legacy adopta Cube_azul
→ metadata original borrada
→ segunda resolución("Cube_azul") = ready
→ navegar_a_objeto(Cube_azul)
→ despachada
```

La reproducción previa al fix observó exactamente `diagnosis=lista`,
`final_metadata=ready` y `dispatch=despachada`.

## 4. Mapa de rutas

| Entrada | Resolución | Legacy antes | Segunda resolución antes | Resultado antes | Resultado después |
|---|---|---|---|---|---|
| target exacto | `ready` | no | sí, target resuelto | física válida | física válida |
| ambiguo, varios candidatos | `requires_clarification` | no por cardinalidad | no | aclaración | aclaración |
| singleton/corrección | `requires_clarification` | sí | sí | posible física incorrecta | aclaración |
| candidato bajo | `not_found` | sí con ≤1 | sí | posible física incorrecta | no física |
| affordance bloqueada | `not_allowed` | no | no | no física | no física |
| riesgo bloqueado | `unsafe` | no | no | no física | no física |
| excepción + snapshot moderno | `None` | sí | sí | posible física | aclaración auditable |
| sin snapshot moderno | `None` | sí | no hay autoridad moderna | legacy válido | legacy válido |

El mismo defecto existía tanto en el diagnóstico determinista como en el
enrichment de acciones Motor propuestas por otra ruta.

## 5. Autoridad de MotorResolution

El contrato es monotónico dentro de una transacción:

```text
status == ready
→ puede materializar el target y continuar a schema/acción

status != ready
→ conserva status y metadata
→ no construye ni sustituye target mediante legacy
→ no inicia una segunda resolución sobre un sustituto
```

Solo una nueva entrada explícita —otra transacción, aclaración del usuario o
nuevo snapshot— puede intentar resolver nuevamente un target bloqueado.

## 6. Policy legacy

Se distinguen dos casos:

- **MotorResolution existente:** es autoritativa. Legacy no puede intervenir.
- **Resolver moderno no aplicable:** cuando no existe ninguna colección
  normalizada (`objetos_normalizados`, `objects`, `perceived_objects`), se
  conserva el extractor legacy y sus nombres visibles históricos.

Para no romper aliases exactos, el diccionario histórico (`cubo→Cube`, etc.) se
incorpora como alias del objeto **antes** de ejecutar fuzzy. Así el resultado
moderno puede ser `ready` legítimamente. No se usa el alias después de un
resultado no-ready. Descriptores adicionales como `rojo` permanecen visibles al
resolver y no se eliminan.

## 7. Policy ante excepción del resolver

Si hay percepción moderna y normalización/fuzzy lanza:

```text
MotorResolution(
    status="requires_clarification",
    audit.status_reason="resolver_exception",
    audit.error_type=<clase segura>
)
```

La excepción no escapa, no activa Reasoning y no se convierte en permiso
legacy. Un snapshot moderno vacío es también autoritativo y produce `not_found`;
no revive nombres de `objetos_visibles`.

## 8. Before / after

| Caso | Before | After |
|---|---|---|
| singleton `requires_clarification` | legacy podía elevar a `lista` | aclaración no física |
| `not_found` con nombre legacy | podía elevar a `lista` | no físico, metadata preservada |
| `not_allowed` | bloqueado | bloqueado, sin cambio |
| `unsafe` sintético | bloqueado | bloqueado, sin cambio |
| resolver exception moderno | `None` → legacy → física | aclaración no física |
| snapshot moderno vacío + nombre legacy | legacy podía resucitar nombre | `not_found`, no físico |
| alias exacto `cubo→Cube` | física por fallback posterior | `ready` moderno por alias previo |
| legacy sin snapshot moderno | física válida | física válida |

## 9. Matrices permanentes

`ai_engine/test_target_resolution_fail_closed.py` añade 14 tests:

1. target rojo exacto `ready`;
2. rojo pedido / solo azul visible;
3. dos targets ambiguos;
4. singleton `requires_clarification` sintético;
5. `not_found` sintético en diagnóstico y enrichment;
6. `not_allowed` y `unsafe` sintéticos;
7. `not_allowed` natural por affordance;
8. singleton peligroso natural;
9. alias exacto, learned alias y typo fuerte `ready`;
10. legacy legítimo sin resolución moderna;
11. snapshot moderno vacío con nombre legacy obsoleto;
12. excepción del resolver con percepción moderna;
13. AuditTrace válido/bloqueado;
14. determinismo ×10 para ready, sustitución y peligro.

Resultado focal: `ROOT001_FOCAL_PASS=14`.

## 10. Zero wrong-target dispatch

Para rojo→azul, el test exige simultáneamente:

- resolución original `requires_clarification`;
- diagnóstico no `lista` y sin parámetros físicos;
- una sola llamada fuzzy en la ruta completa;
- `ActionResponse.accion == hablar`, nunca `navegar_a_objeto`;
- ningún `nombre_objeto` en la salida;
- metadata conservada como `requires_clarification`;
- cero llamadas a Reasoning.

Para `unsafe`, la salida es `none` y ejecución `sin_accion`. Ningún test realiza
ejecución física ni contacta Unity.

## 11. Metadata

Las rutas no-ready conservan `motor_resolution.status`, target/candidates,
candidate correction, reason y `motor_audit`. Ya no retornan `metadata={}`
después de una resolución real. No se amplió el schema ni se intentó cerrar
ROOT-008 completo.

## 12. Compatibilidad del camino válido

Se conservaron:

- target exacto;
- objeto único con nombre exacto;
- alias exacto histórico;
- alias aprendido;
- typo fuzzy suficientemente fuerte;
- schema numérico de ROOT-002;
- clasificación histórica `despachada/pending` para acción física ready.

La ruta ready continúa realizando su segunda validación fuzzy histórica; el fix
solo impide una segunda resolución cuando la primera fue no-ready.

## 13. AuditTrace

AuditTrace v1 no cambió.

Ready:

```text
event_start → triage → memory → decision(navegar_a_objeto)
→ execution(pending) → event_end
```

Rojo→azul bloqueado:

```text
event_start → triage → memory → decision(hablar)
→ execution(pending) → event_end
```

`pending` en el segundo caso corresponde exclusivamente a la aclaración hablada;
no existe acción ni target físico. Cambiar ese lifecycle pertenece a ROOT-004.
Un bloqueo `unsafe` termina `none/sin_accion` y AuditTrace `skipped`.

## 14. Roots vecinos

| ROOT | Estado tras ROOT-001 |
|---|---|
| ROOT-027 unsafe threshold | `OPEN — impact mitigated`: si se emite `unsafe`, ya no puede promoverse |
| ROOT-028 interaction_type | `OPEN — impact mitigated`: estados no-ready no se promueven; la pérdida de tipo persiste |
| ROOT-029 perception freshness | `OPEN — partially mitigated`: snapshot vacío ya es autoritativo; freshness sigue sin contrato |
| ROOT-050 descriptor ranking | `OPEN — impact mitigated`: no-ready no se sustituye; ranking no cambió |
| ROOT-051 clarification labels | `OPEN`: texto/labels no se rediseñaron |
| ROOT-042 runtime invariants | `OPEN — partially improved`, sin cambios en este packet |

## 15. Riesgos

- Un sistema que enviaba colección normalizada vacía y nombres legacy
  contradictorios ahora confía en la colección moderna y bloquea; es el cambio
  fail-closed intencional.
- Los aliases históricos se incorporan solo cuando el id/display name contiene
  su canonical previo; no se amplió el ranking fuzzy general.
- Las aclaraciones `hablar` aún aparecen como `pending` en AuditTrace porque el
  lifecycle no distingue ejecución física de speech; ROOT-004 permanece abierto.
- ROOT-027 puede impedir que `unsafe` aparezca naturalmente; el contrato se
  verificó inyectando una `MotorResolution` sintética.

## 16. Criterio de cierre

| Criterio | Estado |
|---|---|
| `requires_clarification` no produce target físico | PASS |
| `not_found` no se eleva por legacy | PASS |
| `not_allowed` y `unsafe` no producen acción física | PASS |
| rojo→azul produce cero dispatch físico | PASS |
| peligro singleton no se promueve | PASS |
| MotorResolution conserva autoridad/metadata | PASS |
| no hay segunda resolución del sustituto | PASS |
| resolver exception moderna fail-closed | PASS |
| targets exactos/ready siguen funcionando | PASS |
| legacy legítimo sin MotorResolution sigue funcionando | PASS |
| tests focales permanentes | PASS: 14/14 |
| ROOT-002 | PASS: 13/13 dentro de regresión |
| regression completa | PASS: 187 PASS + 1 KNOWN_XFAIL, 0 inesperados |
| AuditTrace v1 | PASS: intacto |
| Chroma | PASS: 3/3 hashes finales idénticos a la entrada |

El probe Motor de AuditTrace (`capa1_motor_completion_probe.py --group audit`)
pasó. `contracts_motor_tools_probe.py` conserva una expectativa histórica de
aceptar bool como coordenada y por eso ya no es un acceptance test válido desde
ROOT-002; no se reescribió.

## 17. Estado final

**CLOSED**.
