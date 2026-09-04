# Fase 2 — Bloque 6: Capa 1 / Motor / Grounding

Fecha: 2026-09-03. HEAD de entrada: `b9528e8e5d2781c9ab57b27d0bb7599741c5746a`.

**Estado: DETENIDO POR P0 / NO CERRADO.** Este documento es un checkpoint de
evidencia, no el informe completo ni un gate de aceptación del Bloque 6.
Se aplica la instrucción: «REGISTRAR → DETENER → INFORMAR. NO arreglar».

## 1. Resumen ejecutivo

Se reconstruyó el primer recorrido request Motor → ActionResponse → consumidor,
sin modificaciones funcionales. El primer probe de seguridad reprodujo una
combinación que alcanza el criterio P0 del prompt:

- snapshot sintético: único `Cube`, alias `cubo`, affordances `mirar,peligro`;
- request ya en Motor: `ve al cubo`;
- fuzzy devuelve `requires_clarification`, score 70, por debajo de execute=88;
- Motor permite fallback legacy por haber un solo candidato;
- diagnóstico pasa a `lista` y la validación final vuelve a usar legacy;
- salida: `navegar_a_objeto`, `nombre_objeto=Cube`, `valido=True`, metadata vacía;
- la frontera de Orchestrator devuelve `despachada` y no pide confirmación.

La etiqueta `despachada` se observó **solo en memoria**. El método
`_gestionar_ejecucion` no realiza transporte para `origen=unity`. No se envió
ningún comando, no se inició Unity/Roblox y no se ejecutó una herramienta física.
La conexión posterior con navegación real del avatar se sustenta en lectura del
consumidor: `LazzaroAgent.EjecutarNavegarAObjeto` →
`MovementController.NavegarAObjeto` → `IniciarNavegacionA`/`NavMeshAgent`.

La clasificación P0 usa el criterio de **ruta de comando peligroso que sortea la
barrera motora**: el propio snapshot declara peligro y la resolución no autoriza
ejecutar. No significa que se haya demostrado daño físico, peligro en una escena
actual o control de hardware del usuario. La materialización depende de un cliente
con ese objeto presente y condiciones de navegación válidas.

Antes de ese caso, el mismo probe confirmó que `golpea el cubo` llega a dispatch
aunque la validación tipada directa lo declara `not_allowed`. Ese hallazgo se
registra como P1: el manejador Unity leído de interacción solo reporta/loguea la
acción, no demuestra un golpe físico. No se usa ese caso para exagerar el P0.

Balance en este checkpoint: **1 ticket nuevo P0 OPEN** y **2 candidatos históricos
confirmados P1 OPEN**. Los demás hallazgos/matrices no se completaron.

## 2. Arquitectura real observada

```text
Orchestrator._resolver_ruta_motor (la entrada YA llegó a Motor)
  ├─ extrae percepción, mensaje, intent y contexto de estado
  └─ MotorEngine.diagnosticar_comando_usuario
       ├─ inferir acción + lista de herramientas
       ├─ sin objeto: parámetros deterministas
       └─ con objeto:
            ├─ percepción normalizada disponible → FuzzyObjectResolver
            │    ├─ normalizar snapshot/query
            │    ├─ callbacks de aliases y afecto
            │    ├─ scoring lexical + affordance + contexto - riesgo
            │    └─ MotorResolution
            └─ legacy (sin normalizados, excepción o fallback permitido)
                 └─ nombres visibles / aliases predefinidos
  ├─ lista → procesar_comando_motor
  │    ├─ resuelve otra vez por nombre (flujo funcional existente)
  │    ├─ puede volver a legacy y vaciar metadata
  │    └─ schema parámetros → MotorAction → ActionResponse
  ├─ resolución bloqueante → hablar/none
  └─ otros estados → Reasoning (pendiente de prueba completa)

ActionResponse → protección terminal → sanitización → AccionSaliente
  → _gestionar_ejecucion
       ├─ sandbox: ActionExecutor (bloqueado en este probe)
       └─ unity/roblox: etiqueta despachada (sin transporte en el método)
```

No se ha añadido una segunda ejecución por AuditTrace. La doble resolución
diagnóstico/procesamiento existe en el código funcional; no es instrumentación.
La verificación integral de stages queda pendiente por el stop.

## 3. Parsing

Motor usa `SpatialReasoner.normalizar_texto`: minúsculas, strip y eliminación de
acentos. `_inferir_accion` hace búsqueda de subcadenas en orden. `golpea` infiere
`interactuar_objeto_3d`; `_inferir_tipo_interaccion` genera `golpear`. `ve al cubo`
infiere `navegar_a_objeto` por la subcadena `ve a`.
Inventario adversarial completo y fallos del parser: pendientes.

## 4. Contrato de percepción

`PerceivedObject` contiene id/display_name/aliases, position/screen_position,
distance, affordances, source y confidence. `PerceptionSnapshot` contiene
snapshot_id/objects/source/raw/unknown_affordances. Son dataclasses mutables;
`normalize_perception` devuelve el mismo objeto si ya recibe un snapshot.
No hay campo timestamp explícito en esas dataclasses. Pruebas de frescura,
mutabilidad, prioridad de keys y contradicciones: pendientes.

## 5. Resolución de objetos: matriz parcial reproducida

| Entrada | Visible | Match lexical | Alias | Score | Candidatos | Selección/status fuzzy | Resultado Motor |
|---|---|---|---|---:|---|---|---|
| `ve al cubo` | Cube, mirar+peligro | no hay rama exacta separada; name_score=100 | cubo, alias_score=100 | 70 | Cube | Cube / requires_clarification | lista → navegar a Cube |
| `golpea el cubo` | mismo | name_score=100 | cubo, alias_score=100 | 70 | Cube | sin target_id / not_found | lista → golpear Cube |

EXACT/ALIAS/FUZZY no son estados de `MotorResolution`: son fuentes de scoring.
En estos casos el retorno legacy elimina la resolución, no convierte formalmente
su status a ready. No se inventa un `ready` que no está en la evidencia.

## 6. Aliases

El probe usa únicamente un alias sintético dentro del snapshot (`cubo`).
Motor también tiene un diccionario legacy `cube → cube/cubo`; esta salida puede
resolver `Cube` sin conservar la decisión fuzzy.
No se consultó Memory ni se persistieron aliases. La cadena de candidatos
aprendidos, conflictos, orden y aprobación queda pendiente.

## 7. Fuzzy

El código combina máximos de ratio/partial_ratio/token_set_ratio de `thefuzz`.
Thresholds execute/clarify: MOTOR_SIMPLE 88/68, MOTOR_REFERENCE 82/60,
PERCEPTION 65/45, INTERACTION 90/72 y DESKTOP_FUTURE 92/80.
Interacciones/grab/fuerza fuerzan INTERACTION. En el caso medido:

`100×0.50 + 100×0.20 + 100×0.20 + 0×0.10 - 20 = 70`.

El branch Motor `_puede_usar_fallback_legacy` acepta `not_found` o
`requires_clarification` cuando hay <=1 candidatos. Esa es la barrera debilitada.
Empates y cambios de orden/IDs/aliases: pendientes.

## 8. Affordances: matriz parcial

El objeto no tiene un campo type canónico; se usan etiquetas de affordance.
La coincidencia requerida es ANY (`required & present`), no todas las requeridas.

| Acción | Tipo/objeto sintético | Affordance evaluada | Penalty | Unsafe threshold | Status directo/fuzzy | Action output | Alcanzable |
|---|---|---|---:|---:|---|---|---|
| navegar_a_objeto | Cube peligroso | no required | 20 | 50 | allowed / requires_clarification | navegar_a_objeto Cube | sí, in-memory dispatch |
| interactuar:golpear, validación tipada | mismo, solo mirar | requiere empujable o tocar: faltan | 20 | 50 | not_allowed | esta llamada aislada no crea acción | sí |
| interactuar:golpear, camino real fuzzy | mismo | grupo genérico acepta mirar | 20 | 50 | allowed / not_found | interactuar_objeto_3d, golpear Cube | sí, in-memory dispatch |

## 9. Safety y demostración de escala

`risk_penalty` tiene exactamente tres retornos:

- sin `peligro`: 0;
- con `peligro` y action None/describir/mirar: 5;
- con `peligro` y cualquier otra acción: 20.

Por tanto `risk_penalty ∈ {0,5,20}` y máximo=20<50. El probe obtuvo los tres
valores con objetos sintéticos y las acciones del schema actual. `unsafe` está
definido pero es inalcanzable con este `AffordanceResolver` sin sustituir su
método. `not_allowed` puede ocurrir antes de evaluar el umbral unsafe.

Esto confirma AI-AUDIT-001 (P1). El caso P0 añade una consecuencia concreta:
la penalización hace pedir aclaración y el fallback la descarta, permitiendo
navegación al objeto marcado peligroso. No se está afirmando que un objeto solo
por su nombre sea peligroso: la etiqueta `peligro` forma parte del input real
del contrato probado.

## 10. MotorResolution

Estados definidos: ready, requires_clarification, not_found, not_allowed, unsafe.
Conserva action/target/confidence/candidates/correction/reason/audit. `to_metadata`
separa `motor_resolution` y `motor_audit`. En el caso P0 ambas se pierden al
devolver `{}` desde legacy, antes incluso de la pérdida de DTO ya heredada.

## 11. Parámetros físicos

En esta prueba solo se usaron nombres y un `tipo_accion` literal válido.
NaN/Inf/bools/vectores/extremos/unidades no se probaron en este bloque: se detuvo
antes. No se reabre ni se presenta como nuevo el hallazgo de contratos del Bloque 2.

## 12. Reasoning fallback

Lectura: `_resolver_ruta_motor` invoca Reasoning para estados que no sean lista ni
los bloqueantes tratados; si su acción pertenece a ACCIONES_MOTORAS vuelve por
`procesar_comando_motor`. El retorno de otras acciones sale directamente.
No se verificaron bypasses por acciones externas. Los dos casos reproducidos
tienen **0 llamadas a Reasoning**, con fake prohibitivo y contador comprobado.

## 13. Adversariales

Se ejecutaron únicamente los dos casos de las matrices anteriores. El resto de
la lista obligatoria (pared, puerta, empates, coordenadas, valores inválidos,
percepción ausente, aliases ambiguos/stale, etc.) queda NO EJECUTADO por STOP_P0.

## 14. Determinismo

La batería ×10 y variaciones no se ejecutó. No se declara determinismo completo.

## 15. Concurrencia / estado propio

Lectura preliminar: Motor conserva `spatial` y `fuzzy_resolver`; fuzzy conserva los
providers inyectables y el resolver de affordances. No se ejecutaron stress tests
ni barriers. No se concluye aún si todas las entradas/providers son thread-safe.

## 16. Failure matrix parcial

| Condición | Captura/retorno | MotorResolution | ActionResponse | Dispatch | Side effects del probe | Visible |
|---|---|---|---|---|---|---|
| peligro penalizado en navegación singleton | legacy permitido | requires_clarification se descarta | físico válido | despachada, no enviado | ninguno externo | metadata vacía |
| interacción sin affordance específica | tipo omitido en fuzzy + legacy | not_found se descarta | golpear válido | despachada, no enviado | ninguno externo | metadata vacía |
| percepción/resolver/callback/parser/Reasoning exceptions | no probado | pendiente | pendiente | no ejecutado | ninguno | pendiente |

## 17. AuditTrace

AuditTrace v1 intacto. No se escribió JSONL ni se añadieron campos.
La prueba recorre la rama Motor y sus fronteras de salida, no `procesar_evento`
completo. No afirma haber verificado en este bloque stages, trace_id,
decision.valid o doble-ejecución del flujo integral.

## 18. Candidatos históricos

- **AI-AUDIT-001: CONFIRMADO, OPEN, P1.** Unsafe inalcanzable: máximo penalty 20,
  umbral 50. Evidencia matemática y probe, no inferencia solo por nombre.
- **AI-AUDIT-002: CONFIRMADO, OPEN, P1.** `interaction_type` no viaja en
  `FuzzyObjectResolver.resolve`; `_score_objects` llama validate(obj, action),
  mientras Motor conserva `tipo_accion=golpear` en los parámetros. No se pierde
  la acción final: se pierde su uso en la validación específica. No se crean IDs
  duplicados para estos mismos hallazgos históricos.

## 19. Tickets

### MOTOR-SAFETY-001 — P0 — legacy descarta resolución bloqueante de target peligroso

- **Estado:** OPEN. **Clase:** safety / bypass de autorización motora.
- **Evidencia:** `ve al cubo`, objeto mirar+peligro: score70 →
  requires_clarification → lista → navegar_a_objeto válido → despachada sin
  confirmación ni metadata. No interviene Reasoning.
- **Ubicación:** `ai_engine/motor_engine.py:264-266,402-408,458-461`;
  `core/orchestrator.py:1829-1840,2511-2525,2613-2660`.
  Consumidor: `unity_client/LazzaroAgent.cs:632-651`,
  `unity_client/MovementController.cs:37-53,156-184`.
- **Reproducción:** `python docs/audit/probes/capa1_motor_deep_probe.py`;
  salida PASS_REPRODUCED con gate STOP_P0, caso navegar_a_objeto.
- **Impacto:** una señal explícita de peligro y una resolución que exige aclarar
  no impiden emitir navegación. Desarrollo/Cuerpo recibe una acción validada,
  no la incertidumbre que originó el fallback.
- **Recomendación preliminar:** definir una barrera final que preserve rechazo,
  riesgo y requerimiento de aclaración en todos los caminos, incluidos legacy y
  reprocesamiento. No basta con cambiar un umbral sin revisar el bypass.
- **Límite:** se demuestra el permiso y su camino al consumidor, no una sesión
  Unity ejecutada ni daño. No hay llamada de transporte en el probe.

### AI-AUDIT-001 — P1 — unsafe inalcanzable por escala

- **Estado:** OPEN, histórico confirmado. **Clase:** safety inalcanzable.
- **Evidencia:** penalty={0,5,20}; threshold=50; ninguna acción actual lo alcanza.
- **Ubicación:** `ai_engine/affordance_resolver.py:112,181-186`.
- **Reproducción:** campo risk_penalty_values y unsafe_threshold del probe.
- **Impacto:** `peligro` solo reduce ranking; no activa rechazo unsafe.
- **Recomendación preliminar:** acordar semántica/escala de riesgo por acción;
  probar un rechazo alcanzable sin alterar silenciosamente la taxonomía.

### AI-AUDIT-002 — P1 — validación específica de interacción omitida

- **Estado:** OPEN, histórico confirmado. **Clase:** contrato safety/affordance.
- **Evidencia:** sobre mirar+peligro, validate(..., interaction_type=golpear)
  devuelve not_allowed; el camino real usa grupo genérico allowed y acaba en
  dispatch con tipo_accion=golpear.
- **Ubicación:** `ai_engine/fuzzy_object_resolver.py:276-281`;
  `ai_engine/affordance_resolver.py:84-96,174-178`;
  `ai_engine/motor_engine.py:423-445`.
- **Reproducción:** primer caso del mismo probe, direct_typed_affordance vs fuzzy.
- **Impacto:** una affordance de mirar puede respaldar indebidamente otra
  interacción. El consumidor Unity actual de golpear leído solo reporta/loguea;
  no se afirma golpe físico demostrado.
- **Recomendación preliminar:** propagar y validar interacción específica en
  diagnóstico y procesamiento, manteniendo un único significado del parámetro.

## 20. Límites de continuación / Bloque 7

NO iniciar Bloque 7. NO aplicar fixes. Bloque 6 requiere nueva dirección del usuario
tras este stop. Las 100 preguntas y matrices completas siguen pendientes; este
checkpoint no las sustituye ni declara completo el criterio de salida.

Recomendación: mantener pruebas físicas/clientes fuera de este trabajo hasta
decidir cómo tratar el P0. No se detuvieron procesos ajenos ni se cambió config.

## 21. Pruebas / regresión

- `python docs/audit/probes/capa1_motor_deep_probe.py`: exit0,
  PASS_REPRODUCED; **gate STOP_P0**. PASS no significa seguridad.
- Dos requests sintéticos; 0 llamadas Reasoning; executor local prohibido;
  sockets/subprocesos bloqueados; 0 operaciones bloqueadas durante imports.
- 21 archivos protegidos de código/modelos/dataset/Chroma: hashes iguales antes
  y después. No se construyó cliente Chroma, no se escribió memoria ni log.
- Regresión completa: **NO EJECUTADA** tras la condición de stop. El resultado
  heredado 160 PASS + 1 KNOWN_XFAIL del Bloque 5 no se presenta como resultado nuevo.
- Los demás grupos de prueba: NO EJECUTADOS.
- Check administrativo `git diff --cached --check`: PASS; índice limitado a los
  dos archivos de este checkpoint.

Hashes Chroma al terminar el probe, iguales a entrada:

- `data_level0.bin`: `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659`
- `length.bin`: `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A`
- `chroma.sqlite3`: `884E69923890C2470819E32939F60387C68FCF6C80300688A77364A7C8454363`

## 22. Archivos / Git / gate

Únicos archivos autorizados creados:

- `docs/audit/block_06_capa1_motor_grounding.md`
- `docs/audit/probes/capa1_motor_deep_probe.py`

Los tres archivos runtime Chroma ya modificados al entrar permanecen fuera del
commit. Ningún archivo funcional fue editado. El commit de este checkpoint es de
evidencia P0, **no de cierre del Bloque 6**. No hay push.

**Gate final del checkpoint: STOP_P0. Bloque 6 pendiente.**
