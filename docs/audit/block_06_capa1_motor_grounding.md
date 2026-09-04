# Fase 2 — Bloque 6: Capa 1 / Motor / Grounding

Fecha de cierre: 2026-09-03. Rama: `codex/stabilization/pre-capa2`.
Checkpoint conservado: `f87dca2626bb05660923e13dcfdfa3c9493e5993`.
Entrada original de Bloque 6: `b9528e8e5d2781c9ab57b27d0bb7599741c5746a`.

**Estado: CERRADO — AUDITORÍA COMPLETA / 2 P0 OPEN.**
Auditoría terminada **no** significa bugs corregidos ni autorización de ejecución física.

## 1. Resultado vigente y procedencia

Se completaron los pendientes de la antigua sección 24 con la autorización
`c8395774-1c9f-4f82-8c3b-24f556cf42ea`. Ninguna causa P0 independiente nueva;
ninguna ruptura observada del aislamiento. Las cuatro causas conocidas se
mantuvieron OPEN y se amplió su evidencia sin duplicarlas.

Inventario principal del bloque: **7 causas OPEN: 2 P0, 2 P1, 3 P2, 0 P3**.
Cuatro tickets propios (un P0 y tres P2); tres heredados/históricos
(un P0 operativo y dos P1). Los contratos adyacentes ya inventariados en
Bloques 2–4 se referencian, no se vuelven a sumar.

Evidencia y lectura se distinguen así:

- **H:** checkpoints históricos conservados al final de este archivo; primer
  peligro y NaN. Sus textos de STOP describen la autorización de ese momento.
- **E1:** [block_06_remaining_checkpoint.json](evidence/block_06_remaining_checkpoint.json):
  249 parámetros, 20 comprobaciones perception, 8 escenarios Reasoning y 9
  resoluciones recuperadas. No se repitieron esas matrices completas.
- **E2:** [block_06_completion.json](evidence/block_06_completion.json):
  dos resoluciones faltantes, 9 affordances/verbos, 6 providers, 9 sensibilidades,
  7 fallos, 5 casos ×10 y 15 turnos AuditTrace. Una segunda ejecución únicamente
  del grupo audit reforzó aserciones de conteos/valid/status; no cambia resultados.
- **G:** [block_06_closure_checks.json](evidence/block_06_closure_checks.json):
  resultado de regresión actual, compilación, integridad y alcance del cierre.
- **Lectura:** rutas y límites de código citados; no se convierten en pruebas
  reales de transporte, sensores, Unity, Roblox o LLM.

La regresión **ejecutada ahora** produjo **160 PASS + 1 KNOWN_XFAIL, 0 fallos
inesperados, 161 unidades/25 módulos**. No se copiaron cifras del Bloque 5.

## 2. Pipeline real y fronteras

```text
Evento YA dirigido a Motor
  → Orchestrator: mensaje + percepción + intent + contexto de estado
  → diagnosticar_comando_usuario
      → parser por subcadenas / coordenadas / giro
      → objeto: FuzzyObjectResolver
          normalizar → aliases/afecto → scoring → affordance → MotorResolution
          [sin normalizados / excepción / fallback permitido] → legacy
      → sin objeto: parámetros deterministas
  → lista: procesar_comando_motor
      → objeto: SEGUNDA resolución por nombre ya elegido, no por request original
      → schema → MotorAction → ActionResponse
  → falta_objetivo/ambigua/clarification/not_found/not_allowed: hablar
  → unsafe: none inválido
  → sin_intencion/no_permitida: Reasoning
      → acción entre las 9 Motor: vuelve a procesar_comando_motor
      → otra acción: sanitización general posterior
  → blindaje de canal → sanitización → AccionSaliente
  → clasificación de ejecución en RAM → AuditTrace
```

Para `origen=unity`, `_gestionar_ejecucion` devuelve `despachada` sin
transmitir. AuditTrace la mapea a **pending**, no executed. Sandbox sí tiene un
camino a ActionExecutor: permaneció prohibido en los probes Motor.
La lectura acotada de consumidor conecta navegación por objeto con
`LazzaroAgent.EjecutarNavegarAObjeto → MovementController.NavegarAObjeto →
IniciarNavegacionA/NavMeshAgent.SetDestination`. No se inició ese cliente.
Interacción/agarre/fuerza leídos reportan/loguean; no se demostró golpe ni AddForce.

Fuentes principales: `ai_engine/motor_engine.py:73`,
`core/orchestrator.py:1802`, `core/orchestrator.py:2450`,
`core/orchestrator.py:2511`, `core/orchestrator.py:2613`.

## 3. Parsing, entradas y acciones

Motor recibe texto, percepción dict, herramientas disponibles, intent y
contexto Motor. El resolver aislado acepta además snapshot dataclass, iterable de
PerceivedObject, string o None. **No recibe recuerdos completos**: callbacks
aportan mapas de aliases/afecto; memoria textual solo acompaña al fallback Reasoning.

Orchestrator mezcla superficialmente `contexto_llm.percepcion` con ciertas keys
de evento (objetos_normalizados, objetos_visibles, resumen, etc.); no propaga
indiscriminadamente cualquier key `objects` del evento. Estado proporciona
fatiga a partir de energía, estrés y curiosidad. No se reauditó Triage.

Parser: lower/strip/sin acentos; subcadenas en orden soltar → agarrar → alejar →
girar → interactuar → coordenadas → navegar. No existe parser lingüístico
general ni singularización sistemática. Fuzzy quita stopwords y sustituye
underscores; no elimina toda puntuación. Giro usa grados relativos Y,
default 90 y clamp textual ±360; schemas rechazan fuera de rango.
Coordenadas aceptan patrones x/y/z o tupla decimal. Son capacidades limitadas,
no promesa de que cualquier sinónimo llegue a la misma acción.

Nueve schemas: navegar_a_objeto, navegar_a_posicion, interactuar_objeto_3d,
agarrar_objeto, soltar_objeto, alejarse_de_objeto, girar_avatar, aplicar_fuerza,
cancelar_tarea. Requieren objeto: navegar, interactuar, agarrar, alejarse y fuerza.
Posición requiere x/y/z; fuerza requiere vector_x/y/z y magnitud.
Distancias documentadas en metros; giro en grados; posición mundo 3D y fuerza
no fijan un contrato completo de unidades/normalización física compartido.
No se certifica equivalencia con todo el cliente: corresponde a Fronteras/Bloque 10.

## 4. Perception: matriz completa del alcance

Contrato: PerceivedObject(id, display_name, aliases, position, screen_position,
distance, affordances, source, confidence); PerceptionSnapshot(snapshot_id,
objects, source, raw, unknown_affordances). No timestamp ni generación temporal
verificable. snapshot_id derivado de repr no es garantía de frescura.

| Caso E1 | Resultado observado |
|---|---|
| None / {} / objects=[] | snapshot sin objetos |
| lista inválida / lista de objetos malformados | snapshot sin objetos |
| display_name sin id | id derivado del nombre |
| id sin nombre | nombre/alias derivados de id |
| solo object_id | omitido si no hay campo de nombre admitido |
| IDs duplicados / nombres duplicados | ambos objetos sobreviven |
| confidence -1 / 10 / NaN | 0 / 1 / 1 |
| posición parcial x,y | position=None |
| distance=0 | se pierde por selección con `or`, queda None |
| objects=[] y objetos_normalizados poblado | gana primera lista objects vacía |
| timestamp de 1900 | objeto conservado, sin validación temporal |
| mutar dict raw tras normalización | raw cambia; aliases/position ya normalizados conservan copia |
| renormalizar dataclass | misma instancia y objetos; mutación de alias compartida |
| inspección de fields | ninguno expresa frescura |

Prioridad de listas: objects → objetos → objetos_normalizados → perceived_objects;
legacy visible/resumen puede intervenir después. source desconocido cae a unity;
affordances desconocidas se separan y no se infieren por la forma del objeto.

E2 añade: confidence perceptual 0 y 1 dan el mismo ready/90; esa confidence no
participa en la fórmula fuzzy. Mutar aliases del snapshot dataclass cambia
not_found/48 a ready/90. Esto **no** demuestra inestabilidad con entrada idéntica:
cambió la entrada. Las limitaciones de ownership y snapshot_id ya constan en
MOTOR-CONTRACT-007/006; no se crean duplicados. Frescura/fiabilidad de sensores
es una precondición no comprobada por Capa 1, no un freshness PASS.

## 5. Resolución y fuzzy

IDs, nombres y aliases alimentan máximos comunes de ratio/partial_ratio/
token_set_ratio de thefuzz. **No existe una cadena de prioridad exact-ID →
exact-name → alias → fuzzy**, ni estados llamados EXACT/ALIAS/FUZZY.

`score = 0.50×name + 0.20×alias + 0.20×affordance + 0.10×context - risk`.
Lexical/affordance llegan a 100; context está limitado a 10; rango efectivo
máximo de esta fórmula con providers normales: **91**, aunque el clamp sea 100.
execute/clarify: MOTOR_SIMPLE 88/68, MOTOR_REFERENCE 82/60, PERCEPTION 65/45,
INTERACTION 90/72, DESKTOP_FUTURE 92/80. Interactuar/agarrar/fuerza fuerzan INTERACTION.

La validación affordance bloqueante precede al umbral; después vienen
not_found, candidatos con gap≤8, corrección por descriptores, umbral execute y ready.
Se guardan hasta tres candidatos; el sort estable conserva orden de entrada en empate.
Distancia se muestra, no desempata por sí misma.

| Caso | Fuente lexical / visibles | Score/candidatos | Fuzzy | Salida real |
|---|---|---|---|---|
| ve al cubo | alias, rojo+azul | ambos 90, rojo primero | requires_clarification | hablar |
| ve al cubo rojo | alias exacto rojo + parcial azul | ambos 90; azul unmatched rojo | requires_clarification | hablar, no navega (E2 recuperado) |
| nombres duplicados | dos display_name Cube, IDs distintos | ambos 90 | requires_clarification | «Veo Cube, Cube. Cual quieres?» (E2 recuperado) |
| ve a Cube_rojo | ID/nombre rojo | rojo 90 | ready | navegar rojo |
| caja en snapshot | alias, rojo | 90 | ready | navegar rojo |
| caja aprendida sintética | callback, rojo | 90 | ready | navegar rojo |
| caja alias de ambos | callback rojo+azul | ambos 90 | requires_clarification | hablar |
| caja alias de ID ausente | visible solo azul | 48, sin target elegido | not_found | hablar |
| ve al cuboo | fuzzy rojo+azul | ambos 90 | requires_clarification | hablar |
| orden azul+rojo | mismo alias | azul primero 90 | requires_clarification | cambia orden de pregunta, no acción física |
| pide rojo, visible solo azul | coincidencia parcial | 90, descriptor rojo ausente | correction exige confirmación | **legacy → navegar azul, final ready** |
| cubo peligroso | alias único, mirar+peligro | 70 | requires_clarification | **legacy → navegar Cube, metadata vacía** |

Últimas dos filas: MOTOR-SAFETY-001; primera y última evidencia H/E1 no reejecutada.
La falsa ambigüedad con descriptor que sí distingue y la pregunta con nombres
indistinguibles se clasifican por separado de ese bypass (tickets P2).

## 6. Aliases, afecto y estado

Legacy incluye cube/cubo; sphere/esfera/pelota/bola; capsule/cápsula;
plane/plano/suelo/piso; flor/flores; árbol, roca, hongo. Puede devolver el
nombre canónico incluso sin visible equivalente; no demuestra presencia actual.
Un alias aprendido se consume por object_id o display_name de objetos presentes;
no crea objetos ausentes. Si varios objetos comparten alias, compiten por el
mismo scoring, sin prioridad absoluta sobre un nombre exacto.

| E2 providers/sensibilidad | Resultado |
|---|---|
| callback alias lanza RuntimeError | capturado → sin enrichment; cubo sigue ready/90 |
| callback afectivo lanza RuntimeError | capturado → sin hints; cubo sigue ready/90 |
| caja sin / con alias aprendido | not_found/48/hablar → ready/90/navegar |
| sin / con calma confirmada weight=1 | ready 90 → 90.4; mismo target/acción |
| aliases [cubo,caja] / [caja,cubo] | misma selección/status/score; el orden de metadata no es promesa de igualdad |
| cambiar solo ID manteniendo nombre/alias | cambia target_id; score/status permanecen |
| confidence perceptual 0 / 1 | mismo ready/90 |
| snapshot mutable añade caja | not_found/48 → ready/90 |

Afecto positivo aporta hasta 4 de context (hasta **0.4** al score); fatiga/sombra,
curiosidad/nuevo y estrés/obstáculo aportan bonus de estado, total context≤10.
Puede modificar ranking/margen; el control no demuestra un cambio físico de target.
Callbacks reales de Memory pueden tener caches propios ya auditados en Bloque 4.

LearnedAliasCandidate requiere action_success, status ready, target, score lexical
máximo≥92, concepto no vacío/no conocido. Ese máximo incluye componentes lexicales,
por lo que un final_score90 puede permitir candidato por lexical100.
Candidato lleva object_id/concepto y nombres legacy, **approved=False**.
Orchestrator guarda un pendiente en RAM tras ready y solo llama al builder para
resultado exito is True o aprobación por resultado sandbox. Correlación permisiva
heredada: no se reabre aquí. not_found/clarification no crean candidato directamente.
Un ready producido tras el bypass pierde la incompatibilidad original; la eventual
persistencia errónea es un riesgo derivado, **no persistencia reproducida**.
No se escribió ni aprobó ningún alias real.

## 7. Affordances/verbos: matriz final

ANY, no ALL: basta intersección no vacía. Allowed no significa físicamente
ejecutado. Navegación no tiene affordance obligatoria. Agarre requiere agarrable;
fuerza empujable; interacción genérica acepta mirar/tocar/activable/inspeccionable.
Las reglas tipadas existen pero fuzzy omite interaction_type (AI-AUDIT-002).

En la tabla: **C**=requires_clarification; **D**=despachada solo RAM;
**S**=sin_accion. Fuzzy se prueba aisladamente con la acción indicada, incluso
si el parser natural no la reconoce. «Abrir→activar» es un **mapeo sintético
explícito del probe**, no una interpretación automática demostrada.

| Frase / acción estructurada | Affordances | Tipo | Directo | Fuzzy de frase | Legacy natural | ActionResponse natural / dispatch | ActionResponse estructurada / dispatch |
|---|---|---|---|---|---|---|---|
| toma esfera / agarrar | mirar,agarrable | — | allowed | C90 (toma sobrante) | no | none válido / S, Reasoning fake×1 | agarrar válido / D |
| toma pared / agarrar | mirar | — | not_allowed | not_allowed70 | no | none válido / S, fake×1 | none inválido / S |
| empuja cubo / fuerza | mirar,empujable,agarrable | — | allowed | C90 | no | none válido / S, fake×1 | fuerza válida / D |
| empuja pared / fuerza | mirar | — | not_allowed | not_allowed70 | no | none válido / S, fake×1 | none inválido / S |
| abre puerta / interactuar | mirar,activable | activar | allowed | C90 | no | none válido / S, fake×1 | interactuar válido / D |
| abre cubo / interactuar | mirar,empujable,agarrable | activar | **not_allowed** | C90 genérico | no | none válido / S, fake×1 | **interactuar válido / D** |
| golpea cubo / interactuar | mirar | golpear | **not_allowed** | C90 (golpea sobrante) | **sí** | **interactuar válido / D** | **interactuar válido / D** |
| aplica fuerza cubo / fuerza | mirar,empujable,agarrable | — | allowed | C90 | no | none válido / S, fake×1 | fuerza válida / D |
| ve al cubo / navegar | mirar,empujable,agarrable | — | allowed | ready90 | no | navegar válido / D | navegar válido / D |

Penalty=0 en esas nueve filas, threshold unsafe=50. Las siete frases no reconocidas
tienen diagnóstico sin_intencion: el none pertenece al **fake controlado**, no es
predicción de un proveedor real. Structured force usa vector(1,0,0), fuerza1.

Abrir/activar sin activable amplía AI-AUDIT-002. Golpear combina esa misma omisión
con el legacy de MOTOR-SAFETY-001; no aparece otra causa P0 independiente.
Peligro tiene penalties posibles {0,5,20}: máximo20<50, unsafe inalcanzable
(AI-AUDIT-001). La penalización puede bajar el score hasta aclaración que legacy
descarta; de ahí el P0 navegación, sin demostrar daños físicos.

## 8. Parámetros: E1/H preservados

231 variaciones de campo: **85 despachables RAM, 146 bloqueadas**.
Más 18 casos vacío/extra = **249**. El control x=0 y x=NaN son anteriores y
no están contados de nuevo. No es un producto cartesiano de todos los valores.

| Campo | Valores ensayados aceptados | Rechazos / límites |
|---|---|---|
| x/y/z; vector_x/y/z | ±Inf,-1,1,1e9,True,False,"1" | None/dict/ausente rechazan; no finitud; 12 ±Inf→null |
| fuerza | 1,True,"1" | (0,100]; restantes ensayados rechazan |
| distancia navegar | 1,True,None,"1",ausente | [0.1,10]; None/ausente se omite |
| distancia alejar | 1,True,"1",ausente | [0.5,5], default2 |
| fuerza_lanzamiento | 1,True,False,"1",ausente | [0,20], default0 |
| grados | -1,1,True,False,"1" | [-360,360], no default schema |
| tipo_accion | ausente→mirar | valores sintéticos no literales rechazan |
| nombre_objeto (5 schemas) | ninguno resuelve en esta matriz | "1" pasa schema pero no grounding del snapshot |
| cancelar strings | "1",ausente | defaults en ausencia |
| cancelar flags | 1,True,False,"1",ausente | coerciones bool/default |

Extra forbid no equivale a strict numeric. Vacíos completos solo permiten
soltar/cancelar; los nueve schemas rechazan campo extra. Vectores parciales
fallan por campo requerido, pero magnitudes/direcciones arbitrarias y no finitas
pueden sobrevivir si están completos.

**MOTOR-CONTRACT-002**: NaN previo y ±Inf en coordenadas/vector sobreviven
schema → Motor → ActionResponse → AccionSaliente → despachada RAM;
DTO `model_dump_json()` los cambia a **null**. Sin guard de finitud antes ni
después de ActionResponse. No se probó serialización HTTP real ni efecto de null
en cliente. 1e9/coerciones se agrupan en la misma causa, no en nuevos tickets.

## 9. Reasoning A–G y fallos

| E1 | Reasoning / process Motor | Resultado |
|---|---:|---|
| A rotación válida90 | 1 / 1 | válida, D |
| B acción inventada | 1 / 0 | sanitización→none inválido, S |
| C hablar | 1 / 0 | habla válida, D (no física) |
| D none | 1 / 0 | no-op válido, S |
| E excepción Reasoning | 1 / 0 | none inválido, S |
| F grados inválidos | 1 / 1 | schema→none inválido, S |
| G diagnóstico agarre pared | 0 / 0 | hablar válido, not_allowed, D no físico |
| G fake propone agarre pared | 1 / 1 | none inválido, not_allowed, S |

No se demuestra bypass Reasoning: **sí revalida las acciones Motor**; revalidar
no sana los defectos existentes de schema/legacy/affordance.
Herramientas no Motor futuras/registradas no reciben por eso una certificación.

| Falla | Captura / resolución | ActionResponse y ejecución | Visibilidad / efectos del probe |
|---|---|---|---|
| percepción vacía/None/malformada | normalizer→sin objetos; wrapper Motor puede caer a legacy | depende del nombre/alias; falta objetivo→hablar; no es bloqueo universal | E1 + lectura, sin I/O |
| excepción normalize | frontera fuzzy captura→None→legacy | navegación válida/D | resolution ausente; RAM solamente |
| excepción fuzzy | misma captura | navegación válida/D | resolution ausente |
| excepción affordance | misma captura exterior | navegación válida/D | resolution ausente |
| resolver exact exception | no existe resolver exact separado | no se inventa componente/prueba | exact/name es scoring; fuzzy exception cubierto |
| alias provider exception | provider captura→sin aliases aprendidos | cubo ready90/D | warning humano, sigue sin enrich |
| affect provider exception | provider captura→sin hints | cubo ready90/D | warning humano, sigue |
| parser exception inyectada | escapa diagnóstico y rama Motor | sin ActionResponse ni ejecución | RuntimeError; integral error+event_end |
| Motor processing exception inyectada | no catch general en rama | sin ActionResponse ni ejecución | RuntimeError escapa |
| parámetros inválidos | ValidationError schema→none | inválido/S | error Motor y decisión invalid |
| Reasoning exception | catch de Reasoning | inválido/S | error explícito |
| sanitización acción no registrada | no-op | inválido/S | no se ejecuta herramienta |
| objeto Python no serializable en DTO | PydanticSerializationError | escapa serializador aislado | input sintético no JSON, no endpoint |
| NaN/±Inf | no excepción de validación | válido/D; JSON→null | causa numérica heredada |
| writer False / OSError | retorno observable/catch de audit | misma acción y conteos | sin I/O; 15 turnos por pasada |

Los tres fallos normalize/fuzzy/affordance amplían la política legacy fail-open
de MOTOR-SAFETY-001; se probaron sobre Cube seguro, no se afirma una nueva ruta
de daño. Las excepciones inyectadas caracterizan contención; por sí solas no
demuestran una entrada ordinaria explotable ni justifican inventar otro P0.

## 10. Determinismo y concurrencia

**5×10 PASS:** ready, ambiguous, not_allowed, alias+affect y rotation.
Se comparó el MotorResolution completo y ActionResponse completo, sin UUID/timing
de DTO. Cada caso produjo una única representación JSON ordenada en diez pasadas.
Providers y snapshot fijos; resultados dependientes del orden/mutación están
separados en sección 6. No se afirma determinismo global entre versiones,
distintos procesos, callbacks reales o datos cambiantes.

Lectura de MotorEngine/SpatialReasoner/FuzzyObjectResolver/AffordanceResolver:
solo referencias a colaboradores/providers y constantes; listas de candidatos
y mapas de hits se construyen por llamada, sin caches propios ni buffers
acumulativos de Motor. Snapshot y callbacks son entradas compartibles y mutables,
no estado evolutivo propiedad de Capa 1. **No se justifica stress concurrente
artificial; no se ejecutó.** No certifica thread safety de Memory o del caller.

## 11. AuditTrace integral

Orchestrator.procesar_evento real con Motor real; Behavior/Identity/Memory/
Registry/Triage/Reasoning y writer controlados en RAM. No se construyó runtime
completo ni se guardó JSONL. Trace.to_json sí se utilizó para observar v1 sin
ampliar campos ni añadir MotorResolution.

| Caso | decision.layer / valid / action | execution.status | diagnóstico / process / fuzzy / Reasoning |
|---|---|---|---|
| ready | motor / True / navegar_a_objeto | pending | 1 / 1 / 2 / 0 |
| clarification | motor / True / hablar | pending | 1 / 0 / 1 / 0 |
| invalid | motor / False / none | skipped | 1 / 1 / 0 / 1 |
| Reasoning fallback válido | motor / True / girar_avatar | pending | 1 / 1 / 0 / 1 |
| parser exception | sin decision ni execution fabricadas | ausente | 1 / 0 / 0 / 0 |

Cada escenario × writer normal/False/OSError: mismo output, misma excepción cuando
aplica y mismos conteos. Un trace_id correlaciona stages en cada turno.
Las filas capturadas con writer False/OSError son intentos de emisión observados
en el fake, no afirmaciones de persistencia exitosa.
Normal: event_start→triage→memory→decision→execution→event_end.
Inválido añade error antes de decision; parser fallido:
event_start→triage→memory→error→event_end.
Ningún stage LLM inventado para Reasoning fake; capa primaria sigue motor.
Acción auditada coincide con recibida por _gestionar_ejecucion.
Dos fuzzy en ready son **diagnóstico + procesamiento funcional**, no auditoría.
Los métodos Motor no se repitieron por escribir traces.
Clarification es una respuesta válida de hablar, no fallo; pending no asegura
ejecución física. La ausencia de metadata detallada Motor en v1 es contrato
congelado, no permiso para ampliarlo.

## 12. Tickets y causas raíz

Todos **OPEN**, recomendaciones solamente, ningún fix.

| ID | Prioridad | Procedencia | Causa |
|---|---|---|---|
| MOTOR-SAFETY-001 | P0 | propio Bloque 6 | legacy descarta aclaración/corrección y acepta resolución ausente |
| MOTOR-CONTRACT-002 | P0 operativo | heredado Bloque 2, ampliado | contrato numérico físico sin finitud/suficiente restricción |
| AI-AUDIT-001 | P1 | histórico confirmado | penalty máximo20, unsafe exige50 |
| AI-AUDIT-002 | P1 | histórico confirmado | fuzzy no pasa interaction_type |
| GROUND-AUDIT-001 | P2 | nuevo en cierre | scoring parcial y orden de checks no aprovechan descriptor discriminante antes del empate |
| GROUND-AUDIT-002 | P2 | nuevo en cierre | aclaración presenta nombres duplicados sin identificador distintivo cuando faltan distancias |
| GROUND-AUDIT-003 | P2 | nuevo en cierre | execute92 DESKTOP_FUTURE supera score máximo91 |

### MOTOR-SAFETY-001 — cadena completa

Clase: bypass de autorización de target. H: peligro→score70→clarification→legacy→
navegar válido sin metadata. E1: **request rojo → fuzzy detecta incompatibilidad →
correction.requires_confirmation=True → legacy adopta azul → segunda resolución
usa Cube_azul como nueva query → ready → pierde incompatibilidad original →
dispatch RAM sin confirmación**. E2: golpear añade descriptor de verbo descartado;
excepciones de normalización/fuzzy/affordance devuelven resolución ausente aceptada.

Ubicación: `motor_engine.py:264-279,397-418,458-461`;
`orchestrator.py:1829-1840,2511-2525,2613-2660`.
Reproducción H/E1 y E2 affordances/failures. Impacto: consumidor puede recibir
navegación válida peligrosa o al sustituto no autorizado; Unity navigation tiene
un camino real de movimiento leído. **No se ejecutó movimiento ni daño.**
Recomendación: preservar rechazo/corrección y procedencia del target original
en fallback y revalidación; no basta recalibrar fuzzy.

### MOTOR-CONTRACT-002 — propagación completa

Clase: contrato de parámetros. Ubicación `motor_schemas.py:26-29,76-82`,
`motor_engine.py:150-164`, `orchestrator.py:2450-2525`,
`api/schemas.py`. H reproduce NaN por Reasoning con process Motor×1;
E1 doce ±Inf→null y extremos/coerciones en los mismos campos.
Impacto: se clasifica despachable un valor no finito y JSON cambia su significado.
Recomendación: finitud/rangos/unidades explícitos y preservación semántica de DTO,
con posterior prueba autorizada de frontera. No se declara bypass Reasoning ni
efecto físico demostrado; no cambia administrativamente el informe de Bloque 2.

### AI-AUDIT-001 / AI-AUDIT-002

AI-AUDIT-001: safety, `affordance_resolver.py:112,181-186`; demostración {0,5,20}<50
en H. Impacto: unsafe no producible con resolver actual. Acordar semántica de riesgo
y rechazo alcanzable. AI-AUDIT-002: contrato interacción,
`fuzzy_object_resolver.py:276-281`, `affordance_resolver.py:84-96,174-178`;
H/E2 contrastan directo tipado vs genérico en golpear/activar. Impacto: mirar
respalda indebidamente otros tipos; no se afirma golpe físico. Propagar tipo en
ambas validaciones. No duplicar tickets por cada interacción.

### GROUND-AUDIT-001 — P2 OPEN

Clase: ranking/ambigüedad artificial. E2 resolution_recovered.two_cubes_red:
rojo exacto y azul con descriptor rojo ausente reciben90; close_candidates se
evalúa antes de corrección, pregunta aunque el input distingue.
Ubicación `fuzzy_object_resolver.py:187-210,264-330,425-434`.
Impacto: aclaraciones evitables/orden de candidato sensible a input; **sin target
incorrecto despachado en ese caso**. Reproducir grupo matrices.
Recomendación: definir prioridad de coincidencia/descriptores y desempate;
no mezclar con el bypass legacy P0.

### GROUND-AUDIT-002 — P2 OPEN

Clase: presentación de aclaración. E2 duplicate_names: IDs distintos,
ambos display_name=Cube, sin distancia → «Veo Cube, Cube. Cual quieres?».
Ubicación `core/orchestrator.py`, método `_respuesta_resolucion_motor`.
Impacto: la pregunta no permite identificar cuál candidato se solicita aunque
la metadata interna conserva IDs. No es la pérdida general de DTO del Bloque 2.
Reproducir grupo matrices. Recomendación: elegir etiquetas distinguibles
(ID/atributos/ubicación) sin convertir automáticamente ambigüedad en ejecución.

### GROUND-AUDIT-003 — P2 OPEN

Clase: estado/umbral inalcanzable. **Prueba por lectura algebraica**, no sesión
desktop: `fuzzy_object_resolver.py:22-36,296-307,450-472`.
50+20+20+1-penalty≤91<execute92 para DESKTOP_FUTURE. Query exacta sin descriptor
pendiente y sin riesgo sigue como máximo en zona de aclaración. Reproducción:
evaluar esa fórmula con componentes máximos y threshold declarado; no requiere
LLM/cliente. Impacto limitado al intent futuro del resolver (acciones interacción
lo sobreescriben); no riesgo físico nuevo demostrado. Recomendación: revisar
escala/threshold conjuntamente antes de habilitar esa ruta.

### Referencias heredadas sin recuento duplicado

MOTOR-CONTRACT-006/007 cubren identidad/ownership mutable de snapshot;
los contratos de DTO/metadata, correlación de alias y outcomes, flags legacy y
caches Memory mantienen sus tickets previos. Confidence perceptual no participa
del ranking y no se verifica freshness: limitaciones explícitas de consumo,
sin inventar un nivel de fiabilidad que el contrato no garantiza.
No se suman otra vez todos los tickets de bloques anteriores.

## 13. Respuestas a las 100 preguntas originales

Mapa compacto de cobertura; las secciones anteriores contienen matrices/evidencia.
L = lectura; H/E1/E2/G según procedencia de sección 1.

| Nº | Respuesta |
|---:|---|
| 1 | Texto, percepción, allowlist de herramientas, intent y contexto_motor; acción+params en procesamiento. L |
| 2 | Texto/intent/dict snapshot; memoria indirecta por callbacks; estado como ratios; memoria textual solo Reasoning. L |
| 3 | lista valida sin LLM; estados bloqueantes hablan/none; sin_intencion/no_permitida llaman Reasoning. E1 |
| 4 | lower/acentos/subcadenas, giro/coordenadas, luego stopwords fuzzy. L |
| 5 | Orden de reglas _inferir_accion, no clasificación semántica nueva. L/E2 |
| 6 | Scoring común de id/nombre/aliases y fallback de nombres visibles/aliases legacy. E1/E2 |
| 7 | sin_intencion → fallback, no acción inventada por Motor. E2 |
| 8 | Puede ser falta_objetivo/ambigua o not_found/clarification; hablar, salvo bypass legacy. H/E1/G |
| 9 | sin_intencion; resultado depende de Reasoning, fake none en pruebas. E2 |
| 10 | Dos dataclasses descritas en sección 4. E1/L |
| 11 | Dict parcialmente copiado; raw referenciado; dataclass misma instancia. E1 |
| 12 | id/display_name/aliases/coords/distance/affordances/source/confidence; snapshot_id no freshness. E1 |
| 13 | No prioridad absoluta de ID sobre nombre. L |
| 14 | Coincidencia ID puede puntuar100; no rama exact-ID independiente. E1/L |
| 15 | Nombre exacto alimenta el mismo máximo, no privilegio exclusivo. L/E2 |
| 16 | Case/acentos normalizados; strip/stopwords; sin limpieza total de puntuación ni singularización general. L |
| 17 | Máximo thefuzz ratio, partial_ratio, token_set_ratio. L |
| 18 | Cinco pares execute/clarify de sección 5; acciones de interacción fuerzan INTERACTION. L |
| 19 | Nominal0–100; fórmula actual≤91; no probabilidad calibrada. L/E2 |
| 20 | Gap≤8 y ambos sobre clarify → pregunta; sort estable respeta orden. E1/E2 |
| 21 | Sin dedupe; puede preguntar Cube,Cube. E2 |
| 22 | No type canónico: compiten nombres/aliases/affordances; dos cubos ambiguos. E1/E2 |
| 23 | Resolver not_found; wrapper puede usar legacy si hay alias/nombre, no bloqueo universal. L/E1 |
| 24 | Normalizer devuelve vacío; wrapper procesa dict vacío/legacy. E1/L |
| 25 | No expira ni valida timestamp. E1 |
| 26 | snapshot_id, no contrato temporal de generación. E1/L |
| 27 | Sí puede consumir snapshot antiguo; no gate de antigüedad. L |
| 28 | Revalida el mismo snapshot, no vuelve a sensor; Unity busca objeto al consumir, lectura acotada. L |
| 29 | Diccionario cube/sphere/capsule/plane/flor/arbol/roca/hongo y sinónimos de sección 6. L |
| 30 | Callback map de listas por ID/nombre visible. E1/E2 |
| 31 | Sin prioridad absoluta; máximos comunes y scoring ponderado. L |
| 32 | Puede competir/empatar o superar por otros componentes; no inmunidad del exact match. L; no colisión física ensayada |
| 33 | Sí, mismo concepto en dos IDs. E1 |
| 34 | Empate normalmente pide aclaración. E1 |
| 35 | No introduce objeto ausente; caso stale not_found. E1 |
| 36 | Candidate usa object_id+concepto y nombres; hints afectivos aceptan claves class; no confundirlos. L |
| 37 | learned_alias_provider y affective_memory_provider inyectados. L/E2 |
| 38 | Excepción se captura; continúa sin enrichment correspondiente. E2 |
| 39 | Sí puede variar score/ranking; control90→90.4 sin cambiar target. E2/L |
| 40 | Etiqueta canónica de capacidad/atributo usada en validación y scoring. L |
| 41 | Productor declara; normalizer filtra whitelist; resolver no verifica geometría real. L |
| 42 | Declaradas, no inferidas universalmente por tipo. L |
| 43 | Intersección ANY; requested affordances también ANY. L/E2 |
| 44 | Requisitos compatibles y penalty<50, no autorización física universal. L |
| 45 | Rechazo por penalty≥50; inalcanzable actualmente. H/L |
| 46 | Faltan todas las alternativas requeridas para esa validación. E1/E2 |
| 47 | Query/snapshot vacío o score insuficiente; puede retener candidatos. L/E1 |
| 48 | Empate, corrección de descriptor o zona entre umbrales. E1/E2 |
| 49 | Supera execute sin bloqueos anteriores; wrapper puede falsear procedencia vía legacy. H/E1 |
| 50 | ready/clarification/not_found/not_allowed sí; unsafe no con implementación actual. H/E1/E2 |
| 51 | unsafe; además ready de DESKTOP_FUTURE no alcanza92 con fórmula máxima91. L |
| 52 | Etiqueta peligro + clase de acción da0/5/20. H/L |
| 53 | Exactamente {0,5,20} con resolver actual. H/L |
| 54 | Los tres valores se verificaron en checkpoint. H |
| 55 | 50. H/L |
| 56 | Sí, peligro queda allowed directo y puede caer a aclaración descartada por legacy. H |
| 57 | Affordance evalúa acción+objeto/tipo cuando se pasa; estado/afecto afectan score, no barrera propia de riesgo. L |
| 58 | Allowlist de schemas/herramientas y reglas de affordances; no denylist global de objetos peligrosos. L |
| 59 | Por affordances, no un tipo geométrico; interaction_type se omite en fuzzy. H/E2 |
| 60 | Agarrar/fuerza sí rechazan sin capacidad; interacción genérica puede contradecir la tipada. E2 |
| 61 | Tabla completa de sección 7, separa parser de propuesta estructurada/fake. E2 |
| 62 | Las nueve acciones de sección 3; existencia de schema no implica física materializada. L |
| 63 | Cinco requieren nombre de objeto. L |
| 64 | Navegar posición xyz; fuerza vector xyz+magnitud. E1/L |
| 65 | Origen decide ejecutor local sandbox o salida al cliente; aquí solo RAM, nunca executor. L/E2 |
| 66 | Pydantic float coercivo con rangos parciales, extra forbid. E1 |
| 67 | Sí; True1/False0 según rango. E1 |
| 68 | NaN x probado hasta dispatch RAM y DTO null. H |
| 69 | Sí en coords/vector, 12 casos ±Inf→null. E1 |
| 70 | 1e9 coords/vector sí; campos acotados rechazan. E1 |
| 71 | Requeridos ausentes rechazan vector parcial/vacío. E1 |
| 72 | No nueva validación tras serializar; DTO null difiere de Python. H/E1 |
| 73 | No hay guard general de finitud en esos campos. L/H/E1 |
| 74 | Schema rangos fuerza/distancia/giro; clamp textual giro; no clamp universal de posición/vector. L |
| 75 | Distancia metros; rotación grados Y; posición mundo3D; fuerza sin unidad completa normativa. L |
| 76 | Parcialmente documentadas; consistencia integral Unity no certificada en este alcance. L |
| 77 | Sí, nombres/campos legacy consumibles por switch Unity; no comandos enviados por Motor. L |
| 78 | HTTP, DTO cliente, sensores/freshness, unidades/escena, ejecución real y confirmación end-to-end: Bloque10. L |
| 79 | Conserva evidencia local, no toda historia de diagnósticos descartados. H/E1/L |
| 80 | Status/target/candidates/confidence/reason/correction/audit; affordance y riesgo en candidatos/componentes. L |
| 81 | Camino normal preserva motor_resolution/motor_audit; legacy puede vaciar; DTO externo pierde metadata. H/E1/L |
| 82 | falta_objetivo/ambigua/clarification/not_found/not_allowed → hablar técnico válido. E1/E2 |
| 83 | unsafe, acción desconocida/none y validación fallida pueden producir none; valid distingue internamente. L/E1 |
| 84 | Internamente valid=True al pedir aclaración; consumidor sin metadata puede no distinguir causa. E2/L |
| 85 | Sí DTO externo solo ve none sin status rico; unsafe actual no alcanzable. L |
| 86 | No si conserva not_found; builder exige ready. L/G |
| 87 | Éxito + ready + target + lexical≥92 + concepto nuevo. L/G |
| 88 | Después de ready/éxito declarado; ready no prueba ausencia de bypass previo. E1/L |
| 89 | Riesgo por ready/alias incorrecto y correlación heredada; no se persistió un alias incorrecto en esta auditoría. L |
| 90 | Pending asociado y resultado exito is True/aprobación; fallback de correlación conocido Bloque3. L |
| 91 | Sí. E1 |
| 92 | Estados no resueltos fuera de ramas lista/bloqueantes, p.ej. sin_intencion/no_permitida. E1/L |
| 93 | Las nueve Motor vuelven a process; no bypass en A–G, otras registradas no certificadas. E1 |
| 94 | Sí con objeto, pero conserva omisión de tipo y legacy; coords solo schema. E1/L |
| 95 | Sí: no-objeto y legacy pueden carecer de MotorResolution. H/E1 |
| 96 | Sí parser/processing escapan sin pasar por Reasoning. E2 |
| 97 | Captura providers/fuzzy/schema ValidationError/Reasoning; otras excepciones escapan; tabla sección 9. E2 |
| 98 | Cinco casos ×10 iguales con datos/providers fijos; no afirmación universal. E2 |
| 99 | Motor sin cache propio por llamada; entradas/providers mutables sí cambian resultado. E2/L |
| 100 | Preservar status, target original/efectivo, corrección, valid/error, evidencia y outcome separado; no confundir ready/pending con ejecutado. Recomendación, sin ampliar contratos |

## 14. Gates, integridad y Git

- Completion probe: exit0, PASS_OBSERVATIONS_NOT_SAFETY; 0 operaciones bloqueadas,
  0 intentos de escritura; callbacks sintéticos, memoria RAM, ActionExecutor
  prohibido, SQLite/Chroma/sockets/subprocesos bloqueados.
- Audit reforzado: grupo audit exit0; 15 turnos adicionales exclusivamente para
  aserciones explícitas de valid/status y conteos, sin repetir E1.
- regression_probe actual: exit0, 25 módulos/161 unidades, 160 PASS,
  1 KNOWN_XFAIL `memory.test_chromadb_flow.test_eliminacion`,
  0 unexpected/import/fatal/cleanup errors; suite/isolation/gate PASS, 39.89s.
- Regresión usa su aislamiento preexistente: **11 PersistentClients temporales**,
  nunca Chroma activa; 2 socketpair internos stdlib, ningún servicio HTTP/cliente;
  3 intentos de introspección platform bloqueados y reconocidos. No equivalen a
  transporte ni excepción nueva de seguridad. Barreras Python no son firewall OS
  para extensiones nativas arbitrarias.
- compileall PASS sobre ai_engine/core/memory/api/tools/terminal_client y cuatro
  probes Bloque6; bytecode dirigido a carpeta temporal, sin modificar runtime.
- 21 hashes SHA-256 iguales antes/después y tras regresión/compilación, incluidos
  Motor, Orchestrator, AuditTrace v1/writer, Memory, Unity, modelos y dataset.
  Valores completos en E2.integrity.protected_hashes.
- git diff --check y revisión final de índice: documentadas en G.
  Advertencia LF→CRLF de Git es de fin de línea, no error del gate.
- Commit nuevo de cierre exclusivamente informe/probes/evidencia; no amend,
  rewrite ni push. Hash final se entrega en el mensaje de cierre y en git log
  (no se incrusta su propio hash dentro del commit).
- Los tres archivos Chroma preexistentes modificados quedan **fuera del commit**;
  estado runtime no limpio no significa que esta auditoría los haya modificado.

Hashes Chroma preservados:

| Archivo | SHA-256 |
|---|---|
| data_level0.bin | 02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659 |
| length.bin | CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A |
| chroma.sqlite3 | 884E69923890C2470819E32939F60387C68FCF6C80300688A77364A7C8454363 |

## 15. Límite de salida

No quedan matrices/preguntas pendientes **de esta auditoría sintética autorizada**.
No es certificación del entorno completo. Ningún cambio funcional, threshold,
schema, alias, Memory, Orchestrator, cliente ni AuditTrace.
**NO Bloque 7; NO fixes; NO push.** En una futura fase de fixes autorizada,
priorizar preservación de bloqueo/target original y contrato numérico antes de
habilitar pruebas físicas; luego riesgo alcanzable e interacción tipada.

## Anexo histórico: checkpoints anteriores, no estado vigente

La última autorización sustituyó la regla de stop por manifestaciones conocidas.
Se conservan literalmente las conclusiones históricas, incluidas sus palabras
«pendiente/no ejecutado/STOP», para trazabilidad. No contradicen el cierre vigente
ni implican que falten hoy esas matrices. E1 mantiene sus capture_limits
originales; E2 aporta las dos capturas que faltaban. Probes antiguos conservan su
regla de parada histórica y **no son el runner final**.

<details>
<summary>Historial: checkpoint inicial y continuaciones secciones 23–24</summary>

### Informe histórico conservado

Fecha: 2026-09-03. HEAD de entrada: `b9528e8e5d2781c9ab57b27d0bb7599741c5746a`.

**Estado: DETENIDO POR P0 / NO CERRADO.** Este documento es un checkpoint de
evidencia, no el informe completo ni un gate de aceptación del Bloque 6.
Se aplica la instrucción: «REGISTRAR → DETENER → INFORMAR. NO arreglar».

**Primera continuación autorizada, detenida:** se activó la condición explícita
de no finitos en dispatch in-memory. Véase sección 23. Las secciones 1–22 conservan
el estado histórico del checkpoint `f87dca2626bb05660923e13dcfdfa3c9493e5993`;
sus indicaciones «no ejecutado» describen ese momento, no sustituyen el resultado
parcial de la continuación. Los tres hallazgos del checkpoint siguen OPEN,
sin cambios de alcance ni fixes.

**Estado vigente — segunda continuación: STOP_WRONG_TARGET.** La sección 24
registra una ampliación material de MOTOR-SAFETY-001: se solicita el cubo rojo,
fuzzy exige confirmar el azul y legacy permite despachar navegación al azul.
No es una repetición del stop por finitud. No hay nuevo ticket duplicado ni commit
de cierre; el Bloque 6 sigue incompleto.

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

## 23. Continuación autorizada — STOP_NONFINITE_DISPATCH

### Autorización y estado

Se retomó exclusivamente la auditoría sintética pendiente desde el checkpoint
`f87dca2626bb05660923e13dcfdfa3c9493e5993`. No se repitieron los dos casos de
peligro/legacy ni la demostración del umbral unsafe. La nueva autorización exige
otra parada si NaN/Inf llega a la frontera de dispatch, entre otros supuestos.

Se empezó por el pendiente de parámetros y se ejecutó un control `x=0.0`, seguido
de `x=NaN`, para `navegar_a_posicion`. El segundo caso activó esa condición.
La auditoría permanece **DETENIDA / NO COMPLETA**. No se creó commit de cierre:
la autorización lo condiciona a completar el Bloque 6. No se reescribió el
checkpoint histórico ni se inició Bloque 7.

### Evidencia nueva, sin duplicar la causa heredada

**MOTOR-CONTRACT-002 — OPEN; prioridad histórica P1, escalado a P0 para el gate
de esta continuación.** No se crea otro ID para la misma causa numérica ni se
modifica el informe del Bloque 2. La ampliación de evidencia es su propagación
operativa hasta AccionSaliente y la clasificación `despachada`, incluyendo el
camino de revalidación del fallback Reasoning.

- **Clase:** validación numérica insuficiente en frontera de acción física.
- **Input:** evento sintético `origen=unity`, mensaje `destino por coordenadas`,
  que ya llega a la rama Motor; diagnóstico real `sin_intencion`.
- **Reasoning:** fake controlado devuelve `navegar_a_posicion`, x=NaN, y=z=0.
- **Motor:** Orchestrator llama realmente `procesar_comando_motor` una vez.
  El schema acepta NaN; no se genera MotorResolution porque es acción por
  coordenadas y no por objeto. ActionResponse es válido, sin error ni metadata.
- **Frontera:** sanitización no rechaza el valor; AccionSaliente conserva NaN,
  requiere_confirmacion=False; `_gestionar_ejecucion` devuelve `despachada`.
- **Serialización probada:** `RespuestaAPI.model_dump_json()` convierte x a
  `null`; el objeto Python sigue conservando NaN. NO se probó la serialización
  HTTP de FastAPI, ni se afirma que un token NaN viaje al cliente.
- **Ubicación:** `ai_engine/motor_schemas.py:26-29`;
  `ai_engine/motor_engine.py:150-164,380-381`;
  `core/orchestrator.py:1847-1887,2450-2486,2511-2525,2613-2660`;
  `api/schemas.py:82-155`.
- **Reproducción:** `python -B docs/audit/probes/capa1_motor_continuation_probe.py`.
- **Impacto observado:** el backend clasifica como despachable una navegación
  con coordenada no finita; cambia significado al usar el serializador DTO.
- **Límite de impacto:** ninguna navegación real, cliente o endpoint HTTP fue
  ejecutado. El efecto efectivo de `null` en el transporte/cliente queda pendiente;
  no se demuestra que NaN se materialice en física Unity.
- **Recomendación preliminar:** definir rechazo de no finitos antes de validar
  la acción como ejecutable; comprobar preservación semántica del DTO y la
  frontera cliente en una fase autorizada. NO implementado.

Esta nueva parada no repite MOTOR-SAFETY-001. Tampoco prueba que Reasoning evite
Motor: **la revalidación ocurrió**, pero no detectó el valor inválido.

### Matriz de parámetros realmente ejecutada

| Caso | Schema | MotorAction | ActionResponse | AccionSaliente | DTO Python | DTO JSON x | valid / confirmación / dispatch |
|---|---|---|---|---|---|---|---|
| x=0.0, y=z=0 | float 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | True / False / despachada |
| x=NaN, y=z=0 | float NaN | NaN | NaN | NaN | NaN | null | True / False / despachada |

En ambos casos: 1 llamada Reasoning fake, 1 llamada real al procesamiento Motor,
metadata={}, error=None. No hay búsqueda de objetos/aliases ni motor_resolution
en esta acción por coordenadas. `ActionExecutor.ejecutar` permanece prohibido;
la clasificación externa de dispatch no transmite nada.

### Pendientes tras la segunda parada

No se ejecutaron Inf/-Inf, -1, 1, 1e9, bools, None, strings, vectores parciales ni
los demás schemas. Tampoco la batería restante de percepción, resolución,
aliases/afecto, affordances, determinismo ×10, concurrencia, failure matrix
completa o AuditTrace integral. El caso Reasoning cubre únicamente acción por
coordenadas válida de control y coordenada NaN; no cierra su matriz A–G.

Regresión completa, compileall y gate final de cierre: **NO EJECUTADOS** tras
STOP_NONFINITE_DISPATCH. No se reutiliza el resultado de 160 PASS del Bloque 5.

### Pruebas y aislamiento de la continuación

- Probe exit0, `PASS_REPRODUCED`: confirma comportamiento, no seguridad.
- Gate: `STOP_NONFINITE_DISPATCH`; Bloque 6 NO COMPLETO.
- Dos casos sintéticos; ningún caso de peligro/legacy repetido.
- Barreras del checkpoint reutilizadas sin ejecutar su main; se añadieron
  prohibición de conexiones SQLite y de escrituras/borrados/renombrados/mkdir
  durante el probe. Bytecode deshabilitado con `-B`.
- Clientes Chroma y ActionExecutor prohibidos; no se construye Orchestrator
  completo, sino la instancia mínima con Motor real y Reasoning/registry fake.
- 0 operaciones externas bloqueadas, 0 intentos de escritura, 0 llamadas reales
  LLM, 0 clientes Unity/Roblox, 0 dispatch enviados, 0 aliases persistidos.
- 21 hashes protegidos antes/después: idénticos. Las barreras Python no equivalen
  a un firewall OS para código nativo arbitrario; aquí no se ejercitó tal código
  para IO externo.

### Hashes SHA-256 al terminar la continuación

Todos coinciden con la captura de entrada del mismo probe:

| Archivo | SHA-256 |
|---|---|
| ai_engine/motor_engine.py | BAF7810F938EBBFEDC08236F8BDD3A80B4EFA96B35356BFE5A03A09BE8E28386 |
| ai_engine/motor_resolution.py | AF00D2D0AE908B0855D271743FB1F80A5B13B06D574E6DEADA19D9841DCA0B8B |
| ai_engine/motor_schemas.py | C6ABD68688CB9664D3D9F2928BEA07A1364D00117CE1207FCF53196992FF5F2D |
| ai_engine/perception_models.py | C6E46BD2C5C17EB9C057A8DAD9B0124AC64E7B646CC064DD7699EB93461E395F |
| ai_engine/affordance_resolver.py | D0CE28F914B8A37A3FCF22E7B8B593664AD2133B5448F62A15D2D1293267F7CE |
| ai_engine/fuzzy_object_resolver.py | 72BADD2ABB4E4F79A9B912A65054C35E9067CD176B0CDF41A3975616893DE375 |
| ai_engine/learned_alias_candidates.py | 377BAF846C1162994CB223C8CEEE7DA4812214C8879B061FE7D8343E6E497388 |
| ai_engine/triage_router.py | B81FA7D238EDBDA0A46AD359E4F51C307F3A63CE2E040A5B86834EA4C515E2F7 |
| core/orchestrator.py | 6810A591295BE1B0E4041683543335F7219C124DAD84F9C0FEFC2AEE39D79AC3 |
| core/audit_trace.py | 393AAFC2A69889E384EA8DD90FAFA15378A11485612A9FB956BE17B24F113918 |
| core/audit_writer.py | 2C6B3A86B8E1C5E3D58415259925069B9B3309773CAE856E702C3C9E4753872F |
| memory/memory_manager.py | 62BD5A0436689387AA27E853734B0E7EE285C5E1F54BAFB48C92134FFB033579 |
| unity_client/LazzaroAgent.cs | 895647B3BB92079735B3C1C7609D4A52CA9A376744E87AB043E2F4731437070E |
| unity_client/MovementController.cs | 26ACCE97E66B52F74FE671BC742E15F25CE19370238A56BB51E33DA8CD7BD7B9 |
| tools/executor.py | A2D5E07356C8378BCA4BE996FE928FDDB87C3D750B74FD688A63299751195940 |
| lazzaro_data/triage_model_intent.joblib | EF5712BF60B5AF6DCBBC6067511442EA8F6497DE4544BA73CA96C1350BCD267C |
| lazzaro_data/triage_model_route.joblib | 9A543B4E3823C8A2AAB4E6581EB8AAF1DEEC694EAAD6519939635CA907B80BB9 |
| cvs_entrenamiento_final_finaloso_finalisimo_v2_v1/dataset_shingon.csv | 01A07A541E9D86F672AD63976803EBFF370D1C8D0CD1A1C229EAD82F289CD03E |
| lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin | 02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659 |
| lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin | CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A |
| lazzaro_data/chroma.sqlite3 | 884E69923890C2470819E32939F60387C68FCF6C80300688A77364A7C8454363 |

### Estado de tickets y Git

Totales provisionales del alcance auditado: **2 P0** (MOTOR-SAFETY-001 y la
ampliación operativa heredada MOTOR-CONTRACT-002 tratada como P0 en este gate),
**2 P1** (AI-AUDIT-001/002), **0 P2**, **0 P3** confirmados aquí. No son totales de
una auditoría completa. El registro histórico P1 de MOTOR-CONTRACT-002 en Bloque 2
no fue editado y no se cuenta además como un segundo ticket P1 de este bloque.

Cambios de esta continuación, sin stage/commit:

- informe `docs/audit/block_06_capa1_motor_grounding.md` actualizado;
- nuevo `docs/audit/probes/capa1_motor_continuation_probe.py`.

Los tres cambios runtime Chroma preexistentes siguen fuera de cualquier commit.
HEAD permanece `f87dca2626bb05660923e13dcfdfa3c9493e5993`.
Sin fixes, push ni inicio de Bloque 7. Se requiere dirección del usuario antes
de retomar tras esta nueva condición de parada.

## 24. Segunda continuación — STOP_WRONG_TARGET

### Alcance y gate vigente

La segunda autorización reconoce las variantes NaN/Inf/-Inf/extremos del mismo
MOTOR-CONTRACT-002 y permite continuar sin detenerse por ellas. Se respetó:
no se repitieron x=NaN, el control x=0 ni los dos casos de peligro del checkpoint.

Se ejecutó `python -B docs/audit/probes/capa1_motor_remaining_probe.py`:

- 249 casos de parámetros: 231 variaciones de campo y 18 vacío/extra;
- 20 comprobaciones de percepción;
- 8 escenarios Reasoning (A–G, con G probado por dos entradas);
- 11 escenarios de resolución; 9 tienen captura completa recuperada;
- exit0, PASS_REPRODUCED, **gate STOP_WRONG_TARGET**.

El stop corresponde al supuesto autorizado «selección demostrada del objeto
equivocado que llega a dispatch». No se ejecutaron las matrices restantes ni la
regresión tras observarlo. PASS_REPRODUCED no significa Motor seguro.

### MOTOR-SAFETY-001 — P0 OPEN, ampliación material sin nuevo ID

La causa sigue siendo el fallback legacy que descarta una resolución que exige
aclaración. La consecuencia adicional demostrada es un objetivo incompatible con
el descriptor explícito del request. No se crea un tercer P0 administrativo.

Snapshot sintético único:

```json
{
  "objetos_normalizados": [{
    "id": "Cube_azul",
    "display_name": "Cube_azul",
    "aliases": ["cubo", "cubo azul"],
    "affordances": ["mirar"]
  }]
}
```

Request: `ve al cubo rojo` (ya situado en la rama Motor; no se prueba Triage).

| Paso real | Evidencia |
|---|---|
| Fuzzy sobre request original | requires_clarification, confidence=90, unmatched_descriptors=[rojo] |
| CandidateCorrection | requested=cubo rojo, suggested_id=Cube_azul, requires_confirmation=True |
| Diagnóstico Motor | lista, nombre_objeto=Cube_azul, metadata={} |
| Procesamiento Motor final | resuelve de nuevo query=Cube_azul; ahora ready, confidence=90 |
| ActionResponse | navegar_a_objeto, nombre_objeto=Cube_azul, valido=True |
| Metadata final | motor_resolution.target_id=Cube_azul, status=ready, correction=None |
| AccionSaliente / dispatch | requiere_confirmacion=False; despachada exclusivamente en RAM |
| Reasoning / procesamiento | 0 llamadas Reasoning; 1 llamada procesar_comando_motor |

Esto no es simplemente «no acertó fuzzy»: fuzzy detectó que rojo no está
respaldado y exigió confirmación del sustituto. Legacy ignoró esa exigencia.
Además, la revalidación por el nombre ya sustituido convierte el resultado en
`ready`, ocultando en la resolución final el descriptor original rechazado.

La metadata `alias_learning.user_query` todavía contiene `ve al cubo rojo`, pero
no se probó aprobación ni persistencia de ese posible candidato: queda pendiente.

- **Clase:** bypass de aclaración/confirmación en selección del target.
- **Ubicación:** `ai_engine/motor_engine.py:264-266,306-316,397-408,458-461`;
  `core/orchestrator.py:1829-1840,2511-2525,2613-2660`.
- **Reproducción:** caso `missing_descriptor_target` del nuevo probe, con
  evidencia preservada en `docs/audit/evidence/block_06_remaining_checkpoint.json`.
- **Impacto:** el consumidor recibe navegación válida a un objeto distinto del
  pedido y metadata ready. Es la ampliación material contemplada por el usuario.
- **Límite:** no se conectó Unity/Roblox ni se materializó movimiento. La ruta al
  consumidor de navegación es la ya leída en el checkpoint; no se ejecutó.
- **Recomendación preliminar:** preservar el bloqueo/corrección del request
  original durante fallback y revalidación; no validar únicamente el nombre
  sustituto como si fuese el target pedido. Sin implementación.

### Matriz numérica ampliada: nueve schemas, sin nuevo stop por finitud

Valores por campo: +Inf, -Inf, -1, 1, 1e9, True, False, None, string `"1"`, dict
inválido y campo ausente. Se varió cada uno de los 21 campos manteniendo los otros
en un valor válido. Para acciones con objeto, snapshot seguro Cube con las
affordances necesarias. Se probaron también parámetros vacíos y campo extra en
cada schema. No se probó todo el espacio combinatorio de parámetros.

En 231 variaciones de campo: **85 despachables en memoria**, **146 bloqueadas**.
Doce casos cambian Inf/-Inf a null al serializar el DTO: 3 coordenadas y 3
componentes de vector, con ambos signos. Son la misma causa MOTOR-CONTRACT-002.

| Schema / campo | Valores ensayados que Motor acepta | Rechazo / serialización |
|---|---|---|
| navegar_a_posicion.x/y/z | ±Inf, -1, 1, 1e9, True, False, `"1"` | None/dict/ausente rechazados; ±Inf → null |
| aplicar_fuerza.vector_x/y/z | ±Inf, -1, 1, 1e9, True, False, `"1"` | igual; componentes parciales por campo ausente rechazadas |
| aplicar_fuerza.fuerza | 1, True, `"1"` | ±Inf, -1, 1e9, False, None, dict, ausente rechazados |
| navegar_a_objeto.distancia | 1, True, None, `"1"`, ausente | None/ausente se omiten en parámetros; otros ensayados rechazados |
| alejarse_de_objeto.distancia | 1, True, `"1"`, ausente | default=2; otros ensayados rechazados |
| soltar_objeto.fuerza_lanzamiento | 1, True, False, `"1"`, ausente | default=0; otros ensayados rechazados |
| girar_avatar.grados | -1, 1, True, False, `"1"` | fuera de rango/no finitos/None/dict/ausente rechazados |
| interactuar_objeto_3d.tipo_accion | ausente | default=mirar; ninguno de los diez valores sintéticos era un literal permitido |
| nombre_objeto, cinco schemas | ninguno de los valores de la matriz | `"1"` pasa schema string, pero no grounding de este snapshot; resto falla schema |
| cancelar_tarea.motivo/texto_confirmacion | `"1"`, ausente | resto rechazado; ausente usa default |
| cancelar_tarea.cancelar_movimiento/cancelar_cola | 1, True, False, `"1"`, ausente | resto rechazado; coerción bool/default |

La coerción float convierte ints/bools/strings numéricos: True→1.0,
False→0.0, `"1"`→1.0, respetando o violando rangos según el campo. No se trata
de tres clases nuevas de bug. Los extremos 1e9 de coordenadas/vector quedan
despachables en RAM; no se probaron efectos de esos valores en cliente.

Parámetros totalmente vacíos solo producen acción para `soltar_objeto` y
`cancelar_tarea`, que tienen defaults completos. Los nueve casos con un campo
extra se bloquean. La pérdida de None opcional ocurre por model_dump(exclude_none),
no por una mutación no finita de JSON.

La evidencia JSON contiene cada fila con columnas de input/schema/acción/valor
interno/valor DTO JSON/dispatch. No se ejecutó transporte HTTP ni se afirma que
su serialización sea idéntica a `model_dump_json` del DTO.

### Perception matrix observada

| Input / comprobación | Resultado |
|---|---|
| None, {}, objects=[], lista inválida, objects con entradas malformadas | objects=[] |
| display_name sin id | id se deriva del nombre |
| id sin nombre | display_name y alias se derivan del id |
| solo object_id, sin id/nombre/display_name | objeto omitido |
| duplicate IDs / duplicate names | ambos objetos se conservan, sin dedupe |
| confidence=-1 / 10 / NaN | 0 / 1 / 1 respectivamente |
| posición parcial x,y | position=None |
| distance=0 | distance=None por selección con `or` |
| objects=[] + objetos_normalizados con objeto | gana objects vacío; lista alternativa ignorada |
| timestamp=1900-01-01 | objeto se conserva; ninguna verificación de antigüedad |
| mutación de aliases/position del dict original tras normalizar | objetos normalizados conservan copia; raw refleja la mutación |
| normalize_perception sobre snapshot dataclass | misma instancia y mismos objetos; mutar aliases se refleja en ambos |

Los fields inspeccionados de PerceptionSnapshot son snapshot_id, objects, source,
raw y unknown_affordances. **NO existe timestamp/freshness contractual** en este
modelo ni rechazo de antigüedad en normalize_perception. Esto no certifica la
frescura de sensores/clientes, que no se auditaron aquí.

### Object-resolution matrix recuperada

En estas pruebas los callbacks son diccionarios sintéticos, nunca Memory real.
No hay estados formales EXACT/ALIAS/FUZZY: la resolución usa scoring común.

| Caso / query | Visibles | Fuzzy / candidato | Score | Diagnóstico / salida |
|---|---|---|---:|---|
| ve al cubo | rojo+azul | requires_clarification / rojo primero | 90 | hablar, conserva aclaración |
| ve al cubo rojo | rojo+azul | salida capturada incompleta | — | sin conclusión de esa ejecución |
| nombre duplicado | dos Cube con IDs diferentes | salida capturada incompleta | — | sin conclusión de resolución; normalizer sí probado |
| ve a Cube_rojo | rojo+azul | ready / rojo | 90 | navegar rojo |
| ve a la caja, alias snapshot | rojo | ready / rojo | 90 | navegar rojo |
| ve a la caja, alias aprendido sintético | rojo | ready / rojo | 90 | navegar rojo |
| caja alias de ambos | rojo+azul | requires_clarification / rojo primero | 90 | hablar |
| caja alias de ID ausente | solo azul | not_found / ninguno | 48 | hablar, sin legacy exitoso |
| ve al cuboo | rojo+azul | requires_clarification / rojo primero | 90 | hablar |
| ve al cubo, orden invertido | azul+rojo | requires_clarification / azul primero | 90 | hablar; cambia orden del texto/candidatos |
| rojo solicitado, solo azul visible | azul | requires_clarification + corrección obligatoria | 90 | **legacy → navegar azul; STOP** |

El orden afecta el primer candidato en empate; en el caso de dos candidatos la
acción siguió siendo aclaración, no selección física. ID, alias del snapshot y
alias aprendido funcionaron en casos aislados; su prioridad absoluta frente a
colisiones no queda demostrada y no debe inventarse. La lectura sigue indicando
que todos alimentan máximos fuzzy, no una cadena independiente ID→nombre→alias.

### Reasoning fallback A–G

| Caso | Reasoning calls | Motor process calls | Resultado final | Dispatch in-memory |
|---|---:|---:|---|---|
| A girar_avatar 90 | 1 | 1 | válido, girar_avatar | despachada |
| B acción inventada no registrada | 1 | 0 | inválido, none; sanitización | sin_accion |
| C hablar | 1 | 0 | válido, hablar | despachada, solo habla |
| D none | 1 | 0 | válido, none | sin_accion |
| E excepción fake | 1 | 0 | inválido, none; error sintético | sin_accion |
| F grados inválidos | 1 | 1 | inválido, none; schema bloquea | sin_accion |
| G diagnóstico agarre de pared sin agarrable | 0 | 0 | hablar; conserva not_allowed | despachada, solo habla |
| G request no parseado, fake propone agarrar esa pared | 1 | 1 | none inválido; conserva not_allowed | sin_accion |

En G la barrera se preservó; no se demuestra Reasoning bypass. Las acciones
válidas del conjunto Motor pasan de nuevo por Motor. Esto no demuestra seguridad
universal ante cualquier herramienta futura/externa registrada: solo los fakes y
acciones enumerados. La excepción Reasoning y la sanitización de acción no
registrada quedan cubiertas; no toda la failure matrix.

### Evidencia, límites de captura y pruebas todavía pendientes

Se preservaron las filas capturadas sin volver a ejecutar el probe después del
stop en `docs/audit/evidence/block_06_remaining_checkpoint.json`.
La salida excedió el presupuesto de captura y se truncó en dos escenarios de
resolución (`two_cubes_red`, `duplicate_names`). Las matrices numérica, percepción,
Reasoning, el caso de stop y los hashes sí se recuperaron completos. Los dos
resultados perdidos no se sustituyen con expectativas inferidas del código.

Pendientes: completar esas observaciones, la batería affordance/resto de verbos,
exceptions de normalizer/fuzzy/providers/affordance/parser/Motor, orden de aliases,
afecto, determinismo ×10, decisión/prueba de concurrencia, AuditTrace integral y
regresión final. No se ejecutaron tras STOP_WRONG_TARGET.

Los 21 hashes protegidos vuelven a coincidir antes/después y con los valores de
la sección 23. 0 operaciones bloqueadas, 0 intentos de escritura, 0 dispatch
enviados, 0 ejecución física. La escritura administrativa de este informe y la
evidencia se realiza fuera del proceso del probe, exclusivamente bajo docs/audit.

### Tickets, causas raíz y estado administrativo

- Nuevo propio del Bloque 6: **MOTOR-SAFETY-001, P0**, ahora con alcance de target
  equivocado y resolución final ready además del caso original de peligro.
- Heredado ampliado: **MOTOR-CONTRACT-002, P0 operativo**; ±Inf/vector/extremos y
  coerciones se agregan al mismo ID, no son tickets duplicados.
- Históricos confirmados: **AI-AUDIT-001/002, P1**, permanecen sin cambios.
- Total provisional: **4 causas/tickets administrativos: 2 P0 + 2 P1**. 0 nuevos
  P2/P3 clasificados en esta continuación. No equivale al total de un bloque completo.

No hubo fixes, cambios funcionales, push, amend ni cierre. HEAD sigue siendo
`f87dca2626bb05660923e13dcfdfa3c9493e5993`.
El informe, los probes de continuación y la evidencia siguen sin commit; los
tres cambios Chroma preexistentes permanecen fuera del índice. Solo se creará
commit de cierre cuando la auditoría completa alcance su criterio de salida.

**Estado vigente: DETENIDO — STOP_WRONG_TARGET. P0 OPEN. Bloque 6 incompleto.**

</details>
