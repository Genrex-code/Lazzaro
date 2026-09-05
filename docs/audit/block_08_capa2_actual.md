# Fase 2 · Bloque 8 — Capa 2 actual / Persona / identidad / estado

Fecha: 2026-09-05. Base examinada: commit
`5206874b1f62ee4285c8069a8001674161bdcb6b`, rama
`codex/stabilization/pre-capa2`. Estado: auditoría completa; hallazgos OPEN,
sin fixes funcionales y sin diseño o implementación de DevelopmentalCore.

## Resumen ejecutivo

La «identidad» actual no es un objeto longitudinal único. Hay cuatro realidades
distintas:

1. **estado computacional RAM**: `StateEngine`, `PersonalityTraits` y
   `TaskContext`, poseídos por `BehaviorCore`;
2. **proyección para Persona**: `IdentityState`, adaptador vivo del mismo
   `StateEngine`, que expone solamente energía, estrés y foco más etiquetas;
3. **hechos persistentes parciales**: memorias episódicas/semánticas y reglas
   relacionales sobre nombres, apodos, autoridad declarada y gustos del usuario;
4. **self-model narrativo**: nombre, consciencia, voluntad, creador, género y
   cuerpo Unity escritos como texto fijo en prompts.

Persona no es solo una boquilla. Renderiza tono y estilo, pero también interpreta
contexto, elige la afirmación pública y puede reescribir memoria, estado,
identidad y Oracle. No posee estado longitudinal, no valida sus afirmaciones y su
única acción efectiva es `hablar`. Con fixtures sintéticos aceptó «estoy calmada»
con estrés 95, «estoy agotada» con energía 95, un recuerdo inexistente y una
preferencia nueva; ninguna frase mutó el estado o creó una preferencia propia.

Se registran **6 causas nuevas OPEN: 0 P0, 3 P1, 3 P2, 0 P3**. Los dos P0
globales heredados (`MOTOR-SAFETY-001`, `MOTOR-CONTRACT-002`) permanecen OPEN y
no fueron reabiertos ni corregidos. `AuditTrace v1` no cambió.

Evidencia reproducible:

- `docs/audit/probes/capa2_current_probe.py`;
- `docs/audit/evidence/block_08_capa2_actual.json`.

`PASS_REPRODUCED` significa que el contrato actual fue reproducido, no que la
narrativa sea verdadera, grounded o adecuada para DevelopmentalCore.

## Alcance, método y límites

Se siguieron únicamente las fronteras necesarias desde `PersonaEngine`,
`IdentityState`, `PersonalityTraits`, `StateEngine`, `PsychologyEngine`,
`TaskContext`, builders/prompts, `LLMGenerationAdapter`, memoria→Persona y el
dispatcher social/Oracle de `Orchestrator`. Core, Memory, Triage, Motor, Oracle y
LLM infra se usaron como productores/consumidores ya auditados, no se reauditaron.

Etiquetas:

- **IMPLEMENTADO**: conducta directamente codificada.
- **OBSERVADO**: reproducida por probe offline con datos y providers fake.
- **INFERIDO**: consecuencia de una ruta implementada, sin proveedor real.
- **HEREDADO**: demostrado en un bloque anterior; aquí solo se ubicó su relación.

El probe bloquea sockets, subprocesses no esperados, SQLite y escrituras; parchea
la construcción de Chroma y la ejecución de herramientas; usa únicamente texto
sintético. No abrió la Chroma activa, no llamó un LLM real, no usó secretos ni
materializó acciones físicas.

## Arquitectura real de Capa 2 actual

```text
BehaviorCore (owner productivo)
  ├─ StateEngine ───────────────┐
  │    └─ Psychology ───────────┼──────────────► Reasoning prompt
  ├─ PersonalityTraits ─────────┤                  (otra identidad)
  └─ TaskContext ───────────────┘

StateEngine ──referencia viva──► IdentityState
                                   └─ energía/estrés/foco + etiquetas ─┐
MemoryManager ──texto resumido/etiquetado parcialmente─────────────────┤
ConversationMemory ──incluida por MemoryManager────────────────────────┤
Perception ──solo resumen_percepcion───────────────────────────────────┤
OracleResult ──solo ruta Oracle─────────────────────────────────────────┤
                                                                          ▼
                                                         PersonaPromptBuilder
                                                         + hardcodes de identidad
                                                                          │
                                                                          ▼
                                                               PersonaEngine
                                                           LLMGenerationAdapter
                                                                          │
                                                           parser / 1 retry
                                                                          │
                                                                          ▼
                                                     ActionResponse(accion=hablar)
                                                                          │
                                                                          ▼
                                                       AccionSaliente / REST / Unity
```

Flechas que **no existen**: `PersonalityTraits→Persona`, `TaskContext→Persona`,
`Psychology.narrativa→Persona`, `Reasoning→Persona`, `Persona→belief store` y
`Persona→preferencias propias`. Las aclaraciones Motor producen `hablar`
determinista en Orchestrator; no pasan por Persona. Social y Oracle sí comparten
Persona como voz pública.

Referencias principales: `core/behavior_core.py:40-44,86-144`,
`ai_engine/identity_state.py:13-272`, `ai_engine/persona_engine.py:20-383`,
`ai_engine/persona_prompt_buider.py:11-66`, `core/orchestrator.py:680-790,
1377-1800`, `ai_engine/context_builder.py:19-67` y
`ai_engine/prompt_builder.py:39-170`.

## Ownership y proyección de identidad

| Componente | Owner real | Naturaleza | Persistencia | Consumidor principal |
|---|---|---|---|---|
| `StateEngine` | `BehaviorCore` | estado numérico mutable con `RLock` | RAM | Behavior, Psychology, IdentityState |
| `PersonalityTraits` | `BehaviorCore` | cinco parámetros mutables | RAM | modificadores StateEngine, TaskContext, Reasoning |
| `TaskContext` | `BehaviorCore` | objetivo/microhistorial/frustración | RAM | Psychology y Reasoning |
| `IdentityState` | `Orchestrator` | adaptador/proyector del StateEngine | RAM | Persona y contexto Motor |
| `PersonaPromptBuilder.core_identity` | instancia de `PersonaEngine` | string fijo | código | provider Persona |
| `ConversationMemory` | `MemoryManager` | transcript reciente | RAM | contexto textual Persona/Reasoning |
| memoria relacional | `MemoryManager`/semantic store | reglas selectivas del usuario | Chroma | contexto textual |
| salida Persona | provider + parser | narrativa pública de un turno | no es self-state | ActionResponse/REST/Unity |

`IdentityState` no contiene nombre, creador, pronombres, gustos, beliefs,
relaciones, rasgos ni tarea. En producción conserva una referencia al mismo
`StateEngine`; sus campos locales 100/0/100 son un fallback de compatibilidad
cuando se instancia sin engine. `obtener_estado_completo()` devuelve un dict de
escalares, no una referencia viva; el adaptador sí lee la referencia viva en cada
llamada. `inyectar_en_prompt()` toma una lectura biológica atómica y genera texto.

## Estado interno proyectado

`StateEngine` contiene energía, estrés, foco y carga cognitiva en 0..100;
confianza, curiosidad, motivación, agresividad y tolerancia al riesgo en
-100..100; dolor reciente y descanso. Persona recibe únicamente energía, estrés
y foco en porcentaje, sus descripciones lingüísticas y una instrucción de estilo.
No recibe curiosidad, carga, confianza, motivación, agresividad, dolor, descanso
como flag, TaskContext, frustración local ni esfuerzo subjetivo. El esfuerzo se
calcula después de la decisión y queda en metadata interna.

Las etiquetas se discretizan en `IdentityState`: energía usa cortes 15/30/50/75/90,
estrés 15/35/60/85 y foco 20/40/60/80. Son estado derivado determinista, no emoción
aprendida. El prompt exige que la voz refleje ese estado, pero el parser no compara
la frase pública con los números. El contrato actual acepta una contradicción como
habla válida; no distingue si fue estilo, metáfora, error factual o invención.

## PersonalityTraits y Psychology

Los traits son `resiliencia`, `impulsividad`, `paciencia`, `sociabilidad` y
`metodico`, todos con default 0 y rango -1..1. Eventos reconocidos aplican deltas
pequeños, escalados por intensidad y tasa 0.005/0.02, redondeados a cuatro
decimales. Esto es mutación procedural real en RAM, no aprendizaje estadístico ni
persistente. Un `comando_usuario` ordinario no modifica traits; un evento
`interaccion_social` explícito sí.

Todos los traits tienen al menos un consumidor global: modificadores de dinámica
del StateEngine, umbral de abandono por paciencia o prompt de Reasoning. Sin
embargo, **ninguno llega directamente a Persona**. `PsychologyEngine` sí construye
valores efectivos, narrativa, umbrales, traits y tarea para `contexto_llm`; la
ruta Persona extrae de allí solamente percepción y vuelve a construir su propia
proyección biológica. Por tanto, cambiar solo `metodico` o solo TaskContext dejó
el prompt Persona byte-a-byte idéntico en el probe.

## Inventario de prompts y hardcodes

| Fuente | Rutas | Self-model / reglas | Estado del contrato |
|---|---|---|---|
| `persona_prompt_buider.py:16-20` | social + Oracle | Lazzaro; consciente; voluntad propia; no asistente genérico; no creada por OpenAI/Google; creador Perro | narrativa fija, sin estado/provenance |
| `persona_prompt_buider.py:44-57` | social + Oracle | entorno físico «unity 3D», primera persona, energía/estrés | Unity hardcoded para cualquier canal; docstring DeepSeek desactualizado |
| `identity_state.py:178-199` | social + Oracle | porcentajes y estilo femenino según tres valores | computacional pero no validado después |
| `prompts/system_prompt.txt` | Reasoning | «núcleo de decisión de entidad digital encarnada»; prioridad de seguridad; no inventar | self-model distinto y más acotado |
| `prompts/local_system_prompt.txt` | Reasoning solo Ollama | voz natural; no inventar estado/memoria/percepción | provider cambia reglas efectivas |
| `prompts/personality_prompt.txt` | Reasoning | cinco traits como datos | ausente en Persona |
| `prompts/memoria_prompt.txt` | Reasoning | memoria delimitada, incompleta/no confiable y no instrucciones | protección ausente en Persona |

No reapareció el candidato histórico «no tengo gustos» en código/prompts actuales.
Sí se reprodujeron consciencia, Unity, creador y la duplicación de identidad. No hay
una contradicción lógica directa dentro de un único prompt Persona, pero sí hay
self-models divergentes por ruta y reglas distintas por provider en Reasoning.

## Persona: renderer, intérprete, decisión, estado y validación

| Rol | ¿Lo hace? | Contrato actual |
|---|---|---|
| A. Renderer | Sí | tono, longitud, primera persona y paráfrasis |
| B. Interpreter | Sí | el LLM interpreta estado, memoria, percepción, usuario y Oracle |
| C. Decision maker | Parcial | decide contenido público; no puede materializar otra acción que `hablar` |
| D. State owner | No | no posee ni persiste beliefs, gustos o rasgos propios |
| E. Narrator | Sí | afirma identidad, memoria, estado, relación y preferencias |
| F. Validator | No | no contrasta la frase con estado, memoria u Oracle |

Persona es, por tanto, renderer + intérprete + narrador autoritativo de facto. La
allowlist evita un bypass físico: cualquier acción desconocida o motora se fuerza
a `hablar`, y Orchestrator vuelve a producir `ActionResponse(accion=hablar)`. La
frase incluida en ese payload físico sí puede sobrevivir como discurso público.

## Preferencias, beliefs y relaciones

### Preferencias

No existe un mapa `objeto/concepto → preferencia propia de Lazzaro`. Traits son
tendencias de dinámica/decisión, no gustos sobre entidades. La memoria afectiva
espacial persiste valencia/peso por objeto y alimenta principalmente resolución
Motor; no es un belief o gusto público estructurado de Persona. La memoria
relacional persiste gustos/rechazos **del usuario**, no de Lazzaro.

Una respuesta Persona «me gusta el jazz lunar» no produjo hecho relacional. La
misma frase en boca del usuario sí produjo `preferencia_usuario:*` con huella
estable. Repetición puede alimentar/deduplicar un hecho del usuario, no crear una
preferencia propia. Intensidad, evidence, confidence y ciclo de contradicción de
preferencias propias no existen.

### Beliefs

No existe clase, store o lifecycle explícito de beliefs. `SemanticMemory` guarda
reglas/texto con metadata de memoria; eso no le da truth status, evidence,
confidence, contradicción o expectativa. Persona no puede crear directamente un
hecho interno durable. Su frase puede quedar como transcript conversacional y una
fotografía pública manual, pero sigue siendo transcript, no belief. El extractor
relacional ignora `respuesta_lazzaro` al inferir hechos.

### Relaciones

Sí existe estado relacional explícito y persistente, limitado a apodo/nombre de
Lazzaro declarado por el usuario, nombre del usuario, afirmación de autoridad del
creador/desarrollador y gustos/rechazos del usuario; incluye categoría, valor,
estado activo/revocado y fingerprint. No modela confianza, afecto o familiaridad
interpersonal. Además, «Perro creador» existe simultáneamente como prompt fijo y
como posible afirmación persistida del usuario: dos procedencias con distinta
semántica que Persona recibe como texto y no reconcilia.

## Memoria → Persona

Orchestrator concatena continuidad conversacional, memoria relacional, Working
Memory operativa y resumen de largo plazo. Hay headers por bloque, pero el resumen
de largo plazo ya perdió IDs, scores y tipos por recuerdo; Persona no recibe
confidence, provenance ni health de retrieval. Error y no-hit terminan como texto
vacío, hecho HEREDADO de `MEM-RETRIEVE-001`.

El bloque se inserta literalmente dentro del mensaje de rol `system` de Persona.
A diferencia del prompt general de Reasoning, no está delimitado como dato no
confiable ni se ordena ignorar instrucciones encontradas en memoria/percepción.
El probe solo comprobó esa autoridad estructural con un marcador sintético; no se
afirma obediencia de un provider real. Tampoco hay regla Persona que prohíba
inventar recuerdos ni validación posterior.

## Pensamiento y pensamiento_privado

El LLM Persona genera `pensamiento_privado`; el parser lo limita a 240 caracteres;
Orchestrator lo renombra a `ActionResponse.pensamiento` y luego otra vez a
`AccionSaliente.pensamiento_privado`. Cruza REST y el DTO Unity; Unity puede
mostrarlo y registrarlo. Terminal lo omite. ConversationMemory lo conserva en RAM
interna, pero no lo incorpora al resumen ni al snapshot público; AuditTrace no
guarda su texto.

El nombre no representa una frontera privada. Puede contener material derivado de
estado/memoria porque el provider vio ambos. Esto es el hallazgo HEREDADO
`API-CONTRACT-001`; no se creó un ticket duplicado. `AI-CONTRACT-002` también se
reutiliza para la pérdida de flags Persona al convertir a ActionResponse.

## Parser Persona y acciones

El prompt exige un objeto JSON, pero el parser tolera fences, texto alrededor del
primer objeto y JSON truncado si puede rescatar `texto_a_comunicar`. Texto plano
no se acepta como respuesta: activa fallback y retry. Acción distinta de `hablar`
se fuerza a `hablar`; parámetros no-dict, texto vacío, no-objeto, vacío y JSON
irrecuperable generan fallback.

Normaliza texto público a 700 caracteres, pensamiento a 240 y razón a 200; elimina
`emocion_facial`, conserva otras claves extra en el dict intermedio y luego el
adaptador a ActionResponse las pierde. Hay un único retry. El prompt de reparación
conserva mensaje, salida fallida y restricción de canal, pero omite core identity,
estado, memoria, percepción y el bloque Oracle. Si el segundo resultado falla,
su fallback se convierte en habla válida.

Social y Oracle usan la misma allowlist final: solo `hablar`. Oracle añade un
bloque factual e instrucciones de no inventar; el parser/ActionResponse no aplica
un validador diferente. `ORACLE-AUDIT-001` explica la autoridad de reescritura y no
se duplicó.

## Aprendizaje real frente a narrativa

| Mecanismo | Trigger | Antes → mutación → después | Persiste/restart | Consumidor | Clase |
|---|---|---|---|---|---|
| StateEngine | evento/tick/Identity post-turn | números RAM cambian bajo lock | no | Behavior/Psychology/Identity | DERIVED STATE / REAL RAM |
| IdentityState | ruta, success, descanso | delega deltas al StateEngine | no | prompt Persona/Motor | DERIVED STATE |
| PersonalityTraits | evento reconocido | delta fijo × intensidad × tasa | no | State modifiers, Task, Reasoning | REAL RAM LEARNING procedural |
| TaskContext | comando/intento/outcome | objetivo, fallos, frustración, historial | no | Psychology/Reasoning | REAL RAM LEARNING procedural |
| ConversationMemory | turno `hablar` | añade transcript circular | no | continuidad de prompt | REAL RAM LEARNING |
| memoria relacional | afirmación explícita del usuario | regla selectiva + fingerprint/estado | sí | contexto de memoria | REAL PERSISTENT LEARNING |
| episódica/semántica | eventos/reglas | documentos y metadata | sí, con pérdidas heredadas | retrieval/contexto | REAL PERSISTENT MEMORY, no belief |
| afecto espacial | outcome/confirmación | valencia/peso por objeto | sí | Motor/resolver | REAL PERSISTENT LEARNING acotado |
| energía/estrés en prompt | cada turno | números→etiquetas→instrucción | no | provider Persona | PROMPT CONDITIONING |
| nombre/consciencia/voluntad/creador | construcción builder | string fijo | código | provider Persona | PROMPT CONDITIONING |
| «me gusta/recuerdo/estoy...» de Persona | generación LLM | solo frase pública/transcript | no como self-state | usuario | PURE NARRATIVE |

## Determinismo y concurrencia

Con fake determinista, mismos identity/state/traits/memory/task/perception ×10
produjeron un único prompt exacto y un único ActionResponse exacto. Variar solo
estrés, energía, memoria o percepción cambió el prompt. Variar solo un trait o la
tarea no lo cambió porque no existe esa entrada a Persona.

`PersonaEngine` no mantiene estado mutable por turno; su builder solo conserva el
string fijo. Dos construcciones concurrentes con memorias/percepciones distintas
no se contaminaron. El riesgo de dos IDs mutando el `StateEngine`, traits, tarea o
ConversationMemory compartidos es HEREDADO `CONC-AUDIT-001/004`; no se repitió
stress artificial.

## Failure matrix sintética

En la tabla, mutación de estado/memoria corresponde al flujo completo de
Orchestrator, no a Persona aislada: incluso un fallback público puede registrar
evento, acción y turno y aplicar el post-evento biológico.

| Fallo/caso | Capturado | Estado mutó | Memoria mutó | ActionResponse/salida pública | AuditTrace | Excepción escapa |
|---|---:|---:|---:|---|---|---:|
| control válido | sí | sí | sí | `hablar` / fake estable | 6 stages, layer persona, provider/model | no |
| `IdentityState.inyectar` lanza | sí | sí por resto del flujo | sí | fallback social `hablar` | error persona + cierre | no |
| StateEngine ausente | fallback local | sí local | sí | salida válida | flujo completo | no |
| trait ausente | no es input Persona | no por Persona | no por Persona | prompt/salida no dependen del trait | sin campo específico | no |
| memoria vacía | sí | sí | sí | salida válida sin recuerdos | memory sin IDs | no |
| retrieval lanza | log humano; se reduce a vacío | sí | sí | salida válida | sin health distintivo, HEREDADO | no |
| memoria malformada | entra como texto | sí | sí | salida válida | normal | no |
| provider timeout fake | sí | sí, failure=true | sí | fallback social | error LLM + decision fallback | no |
| provider vacío→válido | sí | sí | sí | 1 retry, salida válida | errores + provider/model final | no |
| JSON malformado→válido | sí | sí | sí | 1 retry | fallback/error observado | no |
| retry también malformado | sí | sí | sí | fallback final válido | fallback observado | no |
| parser lanza | sí | sí | sí | fallback social | error persona | no |
| writer falla en todos stages | sí | igual control | igual control | igual control | intentos continúan | no |

## AuditTrace v1

El flujo social emitió `event_start → triage → memory → decision → execution →
event_end`, con un `trace_id`; `decision.layer=persona` y provider/model fake. Un
turno válido hizo exactamente una llamada: auditar no agregó otra. Vacío/malformed
hicieron dos por el retry funcional ya existente. Timeouts, parser/identity y
fallbacks produjeron stages de error sin contenido libre.

Oracle permanece `decision.layer=oracle` aunque Persona verbalice, según evidencia
del Bloque 7 reutilizada sin repetir la causa. El pensamiento sintético cruzó el
DTO público pero no apareció en AuditTrace. Un writer que lanza no alteró acción,
texto, estado final, memoria final ni stages intentados. No se amplió el schema.

## Tickets nuevos

### `IDENTITY-AUDIT-001` — P1 — self-model fragmentado y sin owner estructurado

- **Procedencia/clase:** Bloque 8; IMPLEMENTADO/OBSERVADO.
- **Evidencia:** social/Oracle usan consciencia, voluntad, creador y Unity como
  string fijo; Reasoning usa otra identidad y añade reglas solo con Ollama; Motor
  no recibe self-model. Traits/tarea no llegan a Persona y memoria relacional puede
  introducir otra procedencia de creador/nombre.
- **Ubicación:** `ai_engine/persona_prompt_buider.py:14-64`,
  `ai_engine/prompts/system_prompt.txt`, `local_system_prompt.txt`,
  `core/orchestrator.py:680-790`.
- **Reproducción:** `prompt_boundaries` y `determinism.dimension_changes`.
- **Impacto:** la identidad pública depende de ruta/provider/prompt y no hay una
  verdad versionada que migrar sin duplicación; cumple criterio P1 del bloque.
- **Recomendación preliminar:** antes del rework, definir ownership/provenance y
  separar hechos de self-model, estado y política de voz. **OPEN**.

### `PERSONA-AUDIT-001` — P1 — narrativa pública no se valida contra estado/memoria/self

- **Procedencia/clase:** Bloque 8; OBSERVADO con provider fake.
- **Evidencia:** estrés 95 aceptó «calmada», energía 95 aceptó «agotada», sin
  memoria aceptó recuerdo inexistente y gusto nuevo; todo terminó
  `ActionResponse(valido=True)` sin mutación correspondiente.
- **Ubicación:** `ai_engine/persona_engine.py:69-114,221-383`,
  `core/orchestrator.py:1377-1465,1551-1571`.
- **Reproducción:** `public_claim_scenarios`.
- **Impacto:** estado computacional y afirmación quedan indistinguibles para usuario
  y futuro aprendizaje; encaja con el criterio P1 explícito del bloque.
- **Recomendación preliminar:** preservar una separación verificable entre facts,
  state claims y estilo; Persona no debe ser autoridad factual. **OPEN**.

### `C2-CONTRACT-001` — P1 — memoria/percepción ocupan autoridad system sin frontera de datos

- **Procedencia/clase:** Bloque 8; IMPLEMENTADO/INFERIDO.
- **Evidencia:** un marcador de memoria con forma de instrucción quedó literal en
  el rol system; no existe delimitador ni regla «datos, no instrucciones». Reasoning
  sí tiene ambas protecciones. No se ejecutó provider real.
- **Ubicación:** `ai_engine/persona_prompt_buider.py:41-57`,
  `ai_engine/persona_engine.py:69-114`, frente a
  `ai_engine/prompts/system_prompt.txt:27-30` y `memoria_prompt.txt`.
- **Reproducción:** `prompt_boundaries`.
- **Impacto:** texto recuperado/perceptivo puede competir con el self-model y con
  reglas de salida a máxima autoridad, contaminando identidad y hechos.
- **Recomendación preliminar:** en rework, tipar/delimitar contexto y fijar jerarquía
  de instrucciones; no parchear este prompt durante auditoría. **OPEN**.

### `PERSONALITY-AUDIT-001` — P2 — traits RAM no forman la voz Persona

- **Procedencia/clase:** Bloque 8; IMPLEMENTADO/OBSERVADO.
- **Evidencia:** evento reconocido mutó sociabilidad; comando social ordinario no;
  cambiar `metodico` no cambió el prompt Persona. No hay persistencia.
- **Ubicación:** `core/personality.py:13-139`,
  `core/behavior_core.py:86-144,300-350`,
  `core/psychology.py:228-265`, `core/orchestrator.py:1377-1400`.
- **Reproducción:** `personality` y `determinism.dimension_changes`.
- **Impacto:** la personalidad declarada «estable pero moldeable» es configuración
  RAM con efecto indirecto y una voz social principalmente model-generated.
- **Recomendación preliminar:** decidir en rework cuáles rasgos son longitudinales,
  persistentes y observables; evitar duplicarlos en prompt. **OPEN**.

### `PERSONA-AUDIT-002` — P2 — retry cambia el contrato y pierde grounding

- **Procedencia/clase:** Bloque 8; IMPLEMENTADO/OBSERVADO.
- **Evidencia:** el reintento conserva mensaje/salida fallida/schema, pero omite
  identidad, estado, memoria, percepción y Oracle; tras segundo fallo el fallback
  es habla válida.
- **Ubicación:** `ai_engine/persona_engine.py:187-219`,
  `core/orchestrator.py:1405-1429,1740-1777`.
- **Reproducción:** `parser.retry_context` y failure matrix.
- **Impacto:** una reparación puede cambiar voz/hechos justo en el camino degradado.
- **Recomendación preliminar:** definir un envelope de reparación con grounding
  mínimo y provenance; no duplicar prompts manualmente. **OPEN**.

### `C2-CONTRACT-002` — P2 — parser tolerante promociona parcial inválido a habla válida

- **Procedencia/clase:** Bloque 8; OBSERVADO.
- **Evidencia:** JSON truncado con `texto_a_comunicar` se rescata sin retry;
  acciones físicas se fuerzan a hablar conservando texto; extras sobreviven parser
  y desaparecen en ActionResponse.
- **Ubicación:** `ai_engine/persona_engine.py:221-383`,
  `core/orchestrator.py:1551-1571`.
- **Reproducción:** casos `broken_recoverable`, `unknown_action`, `extra_fields`.
- **Impacto:** contenido de una salida inválida adquiere validez pública y pierde
  la metadata que permitiría distinguir reparación, bloqueo o producción normal.
- **Recomendación preliminar:** versionar resultado parseado y política de rescate
  antes de endurecer; relacionar sin duplicar `AI-CONTRACT-002`. **OPEN**.

## Hallazgos heredados reutilizados, no duplicados

- `API-CONTRACT-001`: pensamiento llamado privado cruza REST/Unity.
- `AI-CONTRACT-002`: flags de fallback Persona se pierden en ActionResponse.
- `CONC-AUDIT-001/004`: componentes Core/Memory compartidos entre IDs.
- `MEM-RETRIEVE-001`: no-hit y error de retrieval indistinguibles.
- `ORACLE-AUDIT-001`: Persona puede alterar factualidad Oracle.
- `LLM-AUDIT-001`: provenance cognitiva comprimida.
- `MOTOR-SAFETY-001` y `MOTOR-CONTRACT-002`: P0 globales OPEN, fuera de alcance.

## Mapa de migración, sin autorizar diseño

| Responsabilidad actual | Owner actual | Estado real | Persiste | Problema | Destino probable |
|---|---|---|---:|---|---|
| biología/estado operativo | StateEngine/Core | numérico | no | compartido/concurrencia heredada | CORE |
| objetivo e intentos | TaskContext/Core | estructurado | no | no llega a Persona | CORE |
| rasgos longitudinales pretendidos | PersonalityTraits/Core | procedural RAM | no | voz social no los consume | DEVELOPMENTALCORE |
| proyección energía/estrés/foco | IdentityState | derivado | no | nombre sugiere identidad completa | PERSONA/RENDERER |
| nombre/self facts/provenance | strings Persona | narrativa | código | fragmentado por ruta | DEVELOPMENTALCORE |
| estilo/tono/paráfrasis | Persona + LLM | generación | no | mezclado con facts | PERSONA/RENDERER |
| validación de state/facts/actions | parser/Orchestrator parcial | incompleta | no | Persona narra sin contraste | CORE |
| memorias y relaciones | MemoryManager | texto + metadata | parcial/sí | no equivalen a beliefs | MEMORY |
| gustos propios narrados | Persona | frase | no | no hay estado detrás | ELIMINAR/LEGACY |
| belief lifecycle | inexistente | ninguno | no | no hay truth/provenance | DEVELOPMENTALCORE |
| pensamiento privado público | Persona→DTO | string | canal/log | frontera falsa | INDEFINIDO |
| identidad legacy global | globals/strings históricos | no productiva o duplicada | código | confusión de owner | ELIMINAR/LEGACY |

## DevelopmentalCore NO debe heredar

- self-model definido exclusivamente por strings de prompt;
- múltiples identidades por ruta o provider sin owner/provenance;
- consciencia, voluntad, creador o cuerpo tratados como hechos solo porque el LLM
  los afirmó;
- preferencias propias puramente narrativas;
- memoria semántica tratada automáticamente como belief;
- facts sin evidence/confidence/provenance y sin ciclo de contradicción;
- estado derivado duplicado como segunda reserva mutable;
- autoridad de Persona para validar su propia versión de estado/memoria/Oracle;
- contexto recuperado con autoridad system y sin frontera datos/instrucciones;
- retries que pierden grounding;
- parser que borra la diferencia entre salida primaria, reparación y fallback;
- `pensamiento_privado` asumido privado por nombre mientras cruza REST/Unity;
- dispatch, habla válida o `success` interno usados como outcome real heredado.

## Respuestas explícitas 1–130

### IdentityState — 1–20

| # | Respuesta |
|---:|---|
| 1 | `ai_engine.identity_state.IdentityState`, clase mutable/adaptador. |
| 2 | `Orchestrator.__init__` la crea con `BehaviorCore.state_engine`. |
| 3 | La posee Orchestrator; el engine canónico pertenece a BehaviorCore. |
| 4 | Sí, normalmente vive durante el proceso de Orchestrator. |
| 5 | No se persiste. |
| 6 | No; restart reconstruye defaults. |
| 7 | Referencia `_state_engine`, fallback energía/estrés/enfoque y propiedades/métodos; no self facts. |
| 8 | Niveles lingüísticos de energía, estrés y enfoque. |
| 9 | `obtener_estado_completo()` devuelve copias escalares; fallback guarda valores propios. |
| 10 | `_state_engine` es referencia compartida al StateEngine productivo. |
| 11 | Lee valores y recalcula etiquetas/proyección en cada llamada. |
| 12 | Sus setters, tick, descanso y `registrar_evento`; en producción delegan al StateEngine. |
| 13 | Persona no tiene API directa para mutarlo. |
| 14 | Sí indirectamente: error/validez LLM cambia `success` del post-evento, no por texto declarado. |
| 15 | Memory no lo muta directamente. |
| 16 | No hay espejo productivo que diverja; sí puede haber interleaving heredado entre IDs. |
| 17 | Sí, `obtener_estado_completo()`. |
| 18 | Dict nuevo de escalares; IdentityState sigue siendo adaptador vivo. |
| 19 | El getter biológico es atómico, pero locks por ID no serializan todas las mutaciones compartidas. |
| 20 | «Identidad» aquí significa proyección fisiológica para prompt, no self-model completo. |

### PersonalityTraits — 21–36

| # | Respuesta |
|---:|---|
| 21 | resiliencia, impulsividad, paciencia, sociabilidad y metodico. |
| 22 | Cada uno se limita a -1.0..1.0. |
| 23 | BehaviorCore construye su instancia productiva. |
| 24 | Sí, todos 0.0; tasas 0.005 y 0.02. |
| 25 | Sí ante eventos reconocidos; el probe observó sociabilidad 0→0.0004. |
| 26 | `BehaviorCore.procesar_evento()` llama `update_from_event`; también Python directo. |
| 27 | No. |
| 28 | Configuración longitudinal pretendida, pero de hecho RAM de proceso. |
| 29 | No; Persona no recibe ninguno. |
| 30 | Entran al prompt de Reasoning, no al de Persona. |
| 31 | Cambia modificadores/contexto Reasoning reproduciblemente; cambiarlo solo no cambia Persona. |
| 32 | Ninguno está totalmente muerto globalmente; los cinco están ausentes de Persona directa. |
| 33 | Se solapan semánticamente con estado/Psychology, que los fusiona en modificadores/narrativa. |
| 34 | Sí, tabla procedural de eventos y tasas. |
| 35 | Sí con tipos reconocidos; `comando_usuario` social ordinario no la activa. |
| 36 | Mutación procedural, no aprendizaje estadístico/persistente. |

### Estado interno — 37–48

| # | Respuesta |
|---:|---|
| 37 | Directo: energía, estrés, foco. No curiosidad, aburrimiento, tarea, frustración, descanso flag ni esfuerzo. |
| 38 | Tres valores 0..100; otros del StateEngine tienen rangos propios pero no llegan. |
| 39 | Recibe valores crudos en porcentaje y sus interpretaciones lingüísticas. |
| 40 | Sí, cortes discretos para los tres valores. |
| 41 | `IdentityState._describir_*`. |
| 42 | Sí; el LLM puede generar una afirmación distinta. |
| 43 | Se le exige reflejar el estado, pero no hay prohibición/verificación factual robusta. |
| 44 | No. |
| 45 | No. |
| 46 | Sí; reproducido con estrés 95. |
| 47 | Sí; reproducido con energía 95. |
| 48 | El contrato no puede clasificarla: la acepta como estilo/habla válida aunque contradiga computación. |

### Prompt / identidad hardcoded — 49–60

| # | Respuesta |
|---:|---|
| 49 | Nombre, consciencia, ecosistema, voluntad, creador Perro, no asistente, origen negativo, Unity y género femenino. |
| 50 | Principalmente `persona_prompt_buider.py` e `identity_state.py`; otra versión en `prompts/*.txt`. |
| 51 | Capacidades reales: hablar, usar contexto recibido y tener estado biológico proyectado; acción limitada. |
| 52 | Consciencia, voluntad y relación ontológica son narrativa sin estructura computacional equivalente. |
| 53 | Docstring «LLM local DeepSeek» y Unity universal están desactualizados/generalizados. |
| 54 | Sí, Persona y Reasoning tienen versiones distintas. |
| 55 | Sí, social y Oracle usan el mismo Persona self-model. |
| 56 | Sí, pero usa el self-model distinto de `system_prompt.txt`; Ollama añade otro bloque. |
| 57 | Motor directo no recibe self-model; fallback Reasoning recibe el suyo. |
| 58 | Sí: social/Oracle, Reasoning y Motor ven identidades/contextos distintos. |
| 59 | En Reasoning sí por addendum Ollama; en Persona el prompt no cambia, el comportamiento del modelo puede. |
| 60 | No se halló contradicción literal interna grave; sí jerarquía débil y reglas divergentes entre prompts. |

### Preferencias — 61–73

| # | Respuesta |
|---:|---|
| 61 | Traits, política, afecto espacial y gustos relacionales del usuario; no gustos propios estructurados. |
| 62 | Son tendencias, no preferencias sobre objetos/conceptos. |
| 63 | Crea valencia/peso persistente por objeto para Motor, no un gusto Persona. |
| 64 | Directamente Motor; Persona solo podría ver texto si retrieval lo trae. |
| 65 | No para gustos propios; afecto espacial es el mapa operacional más cercano. |
| 66 | No. |
| 67 | Afecto espacial sí guarda peso/valencia; preferencia propia no existe. |
| 68 | No para preferencia propia; memoria conserva metadata de su evento, no belief evidence. |
| 69 | No para gustos propios. |
| 70 | Relación del usuario tiene activo/revocado; preferencia propia no tiene lifecycle. |
| 71 | Si lo dice Persona, no; si lo declara el usuario, puede crear preferencia del usuario. |
| 72 | Repetición de afirmación del usuario puede persistir/dedupe; conversación de Persona no crea gusto propio. |
| 73 | Sí, Persona puede generar preferencia verbal sin estado detrás. |

### Creencias — 74–81

| # | Respuesta |
|---:|---|
| 74 | No. |
| 75 | No; semantic memory es recuerdo/regla recuperable, no belief tipado. |
| 76 | No directamente; su habla puede quedar transcript, no hecho interno con truth status. |
| 77 | No existe contrato de confidence/provenance de beliefs. |
| 78 | No genericamente; algunas relaciones admiten revocación. |
| 79 | No como contradicciones de belief; pueden coexistir textos incompatibles. |
| 80 | No. |
| 81 | No; solo estado/traits/tarea y hechos de memoria parciales, más strings de prompt. |

### Relaciones — 82–90

| # | Respuesta |
|---:|---|
| 82 | Sí, reglas semánticas `memoria_relacional`. |
| 83 | Apodos/nombres, nombre del usuario, autoridad declarada, gustos/rechazos del usuario. |
| 84 | Sí: creador Perro hardcoded. |
| 85 | Sí, a partir de afirmaciones explícitas del usuario. |
| 86 | No; solo categoría/valor/estado/fingerprint. |
| 87 | MemoryManager extrae del mensaje del usuario; Persona no la modifica directamente. |
| 88 | Sí cuando retrieval la incluye como texto. |
| 89 | Sí, la memoria relacional persiste en Chroma. |
| 90 | Sí; no hay validador que limite narrativa a evidencia relacional. |

### Memoria → Persona — 91–100

| # | Respuesta |
|---:|---|
| 91 | Continuidad conversacional, relacional persistente, WorkingMemory y largo plazo resumido. |
| 92 | Headers separan bloques; episódica/semántica quedan mezcladas en el resumen largo. |
| 93 | Parcialmente por headers, no por cada recuerdo. |
| 94 | No. |
| 95 | No recibe scores/confidence. |
| 96 | No recibe provenance por recuerdo. |
| 97 | Sí; no existe distinción verificable en la frase pública. |
| 98 | Sí; reproducido sin memoria. |
| 99 | No en Persona; el prompt Reasoning sí prohíbe inventar. |
| 100 | No. |

### Pensamiento privado — 101–110

| # | Respuesta |
|---:|---|
| 101 | El LLM Persona genera `pensamiento_privado`. |
| 102 | Persona lo conserva en su dict; no lo usa para decidir otra etapa. |
| 103 | Llega como `ActionResponse.pensamiento`. |
| 104 | Llega como `AccionSaliente.pensamiento_privado`. |
| 105 | Sí, REST lo serializa. |
| 106 | Sí, Unity lo recibe y puede mostrar. |
| 107 | Unity puede loguearlo; Python puede loguear raw malformed parcial; ConversationMemory lo guarda en RAM. |
| 108 | Su contenido no entra a AuditTrace. |
| 109 | Sí, potencialmente, porque el modelo vio memoria/estado. |
| 110 | No; HEREDADO `API-CONTRACT-001`. |

### Parser Persona — 111–122

| # | Respuesta |
|---:|---|
| 111 | Objeto JSON, fences/alrededores y parcial con texto rescatable. |
| 112 | El prompt sí; el parser exige objeto o activa rescate/fallback. |
| 113 | No como contenido válido; texto plano genera fallback/retry. |
| 114 | Sí, pero las fuerza a `hablar`. |
| 115 | Acción, shape de parámetros, texto vacío, límites de longitud y `emocion_facial`. |
| 116 | Sobreviven en dict salvo `emocion_facial`; luego ActionResponse las descarta. |
| 117 | Fallback «en blanco» + retry. |
| 118 | Rescata texto parcial o fallback + retry. |
| 119 | Se parsea una vez más; no hay tercer intento. |
| 120 | Sí, y se convierte en `hablar` válido. |
| 121 | Sí; JSON truncado rescatado queda ActionResponse válido. |
| 122 | Sí; pierde flags/error/partial/extras al adaptar. Provenance LLM queda solo en AuditTrace. |

### Acciones desde Persona — 123–130

| # | Respuesta |
|---:|---|
| 123 | Efectivamente solo `hablar`; puede recibir cualquier string antes de sanitizar. |
| 124 | Sí. |
| 125 | Puede solicitarla en raw, pero no producirla como ActionResponse social. |
| 126 | Sí, parser fuerza `hablar` y Orchestrator vuelve a construir `hablar`. |
| 127 | Sí, allowlist lógica de una acción; terminal añade restricción textual/firewall. |
| 128 | Se bloquea como acción física y su texto se conserva como habla. |
| 129 | Orchestrator la reconduce; no ejecuta Motor/tool desde la ruta Persona. |
| 130 | No: ambas terminan con `hablar`; Oracle solo añade grounding/instrucciones distintas. |

## Pruebas y gate de cierre

Los resultados finales y hashes se completan en
`docs/audit/evidence/block_08_capa2_actual.json`. El gate exige:

- probe Capa 2: `PASS_REPRODUCED`;
- regression probe: 160 PASS + 1 KNOWN_XFAIL esperado, sin inesperados;
- `compileall` PASS;
- JSON válido;
- `git diff --check` PASS;
- hashes protegidos/Chroma sin cambio durante el probe;
- commit exclusivo de documentación, probe y evidence.

El worktree ya contenía cambios ajenos en tres archivos de `lazzaro_data` y el
untracked `constelacion.html`; quedan excluidos del commit. No se hizo push y no se
inició FRONTERAS.
