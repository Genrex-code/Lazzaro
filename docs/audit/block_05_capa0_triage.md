# Fase 2 — Bloque 5: Capa 0 / Triage

Fecha de cierre: 2026-09-02

Alcance: auditoría profunda de clasificación y routing; sin fixes funcionales

Estado: **CERRADO**

## 1. Resumen ejecutivo

Capa 0 es un clasificador híbrido, single-label y determinista en inferencia:

1. reglas directas por `tipo_evento`;
2. dos guardrails de comando que evitan ML;
3. un pipeline TF-IDF + Random Forest para intención;
4. heurísticas por keywords solo cuando ML falta, falla o queda bajo umbral;
5. ruta canónica derivada de la intención;
6. overrides de evento/movimiento;
7. adaptación posterior exclusiva del canal terminal.

El runtime carga únicamente `triage_model_intent.joblib`. El trainer también crea
`triage_model_route.joblib`, pero ningún camino productivo lo carga: la ruta no se
predice independientemente. `INTENT_TO_ROUTE` es la autoridad canónica; el mapping
duplicado `INTENT_TO_ROUTE_MAP` tampoco tiene consumidor.

El modelo activo quedó vinculado de forma exacta al dataset/trainer actuales: al
recrear ambos pipelines en RAM coincidieron vocabulario, IDF y los 150 árboles. El
CSV final tiene 863 ejemplos únicos, cero conflictos exactos/normalizados y rutas
canónicas consistentes. No hay desincronización actual. Sí hay una deuda de artefacto:
los joblib están ignorados por Git, no hay manifiesto de hash/métricas y la ruta
default del trainer apunta a un CSV raíz inexistente.

La coherencia semántica fuera del corpus es más frágil. El modelo activo más
fallback reprodujo:

- `ve al cubo` → `UNKNOWN/persona`;
- `por favor ve al cubo` → `SOCIAL/persona`;
- `habla de cómo girar` → `MOTOR_SIMPLE/motor`;
- `ve al cubo y dime qué ves` → una sola intención `PERCEPTION/motor`;
- `no recuerdes esto` → `UNKNOWN/persona` con confidence `0.81`.

La evaluación estratificada recreada —no histórica ni accuracy productiva— obtuvo
69.36% para el RF solo y 57.80% para el router operativo con threshold+fallback. El
fallback tomó 113 de 173 casos; la heurística puede empeorar una predicción ML baja
pero correcta. El trainer actual no calcula ni persiste ninguna métrica holdout.

Resultado:

- **8 tickets nuevos OPEN**: 3 P1, 4 P2 y 1 P3;
- 0 P0;
- modelo/dataset/rules/thresholds intactos;
- AuditTrace v1 intacto;
- Chroma activa no abierta por el probe;
- ningún fix, push ni inicio del Bloque 6.

## 2. Arquitectura real

```text
EventoEntrante
     │
     │ Orchestrator: model_dump(mode="python")
     │ + estado post-BehaviorCore
     ▼
TriageRouter.route_event(evento, estado_agente)
     │
     ├─ hard rules por tipo_evento
     │    ├─ actualizacion_estado → NOOP/noop
     │    ├─ resultado_accion    → INTERNAL/internal
     │    ├─ inicio_simulacion   → CORE_SYSTEM/noop
     │    └─ alerta_dolor_fisico → CORE_SYSTEM/motor
     │
     ├─ comando vacío → NOOP/noop
     ├─ allowlist save manual → CONVERSATION_SAVE/persona
     ├─ guardrail autoridad/regañó → SOCIAL/persona
     │
     ├─ TF-IDF word (1,2) + RandomForest intent
     │      ├─ confidence redondeada >= 0.60 → aceptar intent
     │      └─ falta/error/baja → fallback keywords
     │
     ├─ normalize_intent
     ├─ route_for_intent
     └─ si ya se mueve + intent motor → NOOP/noop
              │
              ▼
 intent + route + confidence + reason
              │
              ▼
 Orchestrator
              │
              ├─ Unity/sandbox: route sin cambio
              └─ terminal: no persona/oracle → effective_route=persona
```

BehaviorCore ya mutó estado/tarea/personalidad antes de esta llamada, como quedó
cerrado en Bloque 3. Capa 0 no lee Memory ni health de Chroma.

## 3. Inputs reales

Orchestrator entrega el `model_dump(mode="python")` completo y `estado_actual` del
BehaviorCore. Triage solo consume:

| Input disponible | Influye | Cómo |
|---|---:|---|
| `tipo_evento` | sí | selecciona hard rule o rama comando |
| `detalles.mensaje` | sí | primera fuente de texto |
| `detalles.texto_usuario` | sí | fallback si mensaje es falsey |
| `detalles.texto` | sí | tercer fallback |
| estado del agente | parcialmente | solo señales de movimiento |
| movimiento en detalles/`estado_fisico` | sí | bloquea intents motor |
| `origen` | no en Capa 0 original | terminal adapta después |
| `solicita_decision` | no | Orchestrator lo usa después |
| tarea | no | no es feature |
| percepción | no | no es feature |
| objeto involucrado | no | no es feature |
| estado emocional/energía/estrés | no | salvo que coincida con clave de movimiento |

El probe cambió origen, tarea, percepción, objeto, `solicita_decision`, energía y
estrés manteniendo texto/tipo: la decisión original no cambió. Añadir
`is_moving=True` convirtió un motor seguro en `NOOP/noop`.

## 4. Preprocesamiento

Pre-ML:

- extrae campos por `or`;
- convierte a `str`;
- elimina whitespace exterior;
- no limita longitud;
- no normaliza acentos/puntuación/case explícitamente.

TF-IDF:

- `lowercase=True`;
- analizador word;
- unigramas+bigramas;
- token regex `(?u)\b\w\w+\b`;
- `strip_accents=None`;
- 4522 features en modelo activo.

Las reglas heurísticas sí minúsculizan y eliminan acentos con NFD. Por ello ML y
fallback no comparten exactamente el mismo espacio textual.

Casos frontera:

- `""`, whitespace, `None` o ausente → hard `NOOP/noop`, confidence 1.0;
- detalles no-dict → se vuelven `{}` y producen comando vacío;
- evento no-dict → `AttributeError`, capturado solo por Orchestrator;
- emoji puro → vector TF-IDF vacío; el RF activo da prior SOCIAL 0.59, bajo umbral,
  y fallback termina UNKNOWN 0.4;
- texto de 25,000 caracteres se procesa sin límite;
- texto mixto español/inglés no tiene manejo especial y cayó UNKNOWN;
- typos pueden funcionar por vocabulario aprendido, no por fuzzy explícito.

## 5. Reglas y precedencia

### Hard rules que evitan ML

Orden exacto:

1. `actualizacion_estado`;
2. `resultado_accion`;
3. `inicio_simulacion`;
4. `alerta_dolor_fisico`;
5. para comandos: vacío;
6. allowlist exacta-normalizada de save manual;
7. guardrail relacional de autoridad/reprimenda.

Estas reglas pueden cambiar intent y route a la vez y nunca consultan RF.

### `REGLAS_BASE`

No son hard rules previas al modelo: son **fallback**. Solo se ejecutan si el RF no
está disponible, lanza o su confidence redondeada es menor a 0.60. Su prioridad es:

1. ORACLE_QUERY;
2. PERCEPTION;
3. MOTOR_SIMPLE;
4. MEMORY_UPDATE;
5. MEMORY_QUERY;
6. SOCIAL.

Gana la primera keyword que haga match. No existe análisis gramatical, negación ni
composición. Los solapamientos dependen de la confianza del RF: el mismo vocabulario
puede decidirse por modelo o por una prioridad heurística diferente.

Ejemplos confirmados:

- `por favor ve al cubo`: el fallback no reconoce `ve al` como la keyword `ve a` y
  SOCIAL gana por `por favor`;
- `habla de cómo girar`: MOTOR gana por `girar` antes que cualquier intención de
  discutir/hablar;
- `ve al cubo y dime qué ves`: PERCEPTION gana por prioridad sobre motor;
- `recuerda que este código...` puede ser ORACLE en fallback porque Oracle precede
  MEMORY_UPDATE.

### Movimiento

Después de ML/fallback, solo `MOTOR_SIMPLE` y `MOTOR_REFERENCE` se reemplazan por
`NOOP/noop` si estado/detalles indican movimiento. Se pierde el intent motor previo;
`reason` informa el bloqueo, pero no conserva path/confidence originales.

## 6. Modelo/pipeline activo

| Propiedad | Intent model | Route model legacy |
|---|---:|---:|
| archivo | `triage_model_intent.joblib` | `triage_model_route.joblib` |
| tamaño | 16,378,725 bytes | 6,209,085 bytes |
| runtime lo carga | sí | no |
| pipeline | TF-IDF + RF | TF-IDF + RF |
| árboles | 150 | 150 |
| `random_state` | 42 | 42 |
| `class_weight` | balanced | balanced |
| muestras | 863 | 863 |
| features | 4522 | 4522 |
| clases | 10 intents | motor/oracle/persona |

El constructor captura modelo ausente, joblib corrupto o incompatibilidad de carga y
deja `model_intent=None`; boot continúa en modo heurístico.

El candidato histórico queda resuelto definitivamente:

- trainer: entrena y guarda **dos** clasificadores independientes;
- runtime: carga solo intención;
- runtime: siempre deriva route desde intent, salvo overrides hard;
- route joblib: artefacto legacy, cargable y coherente, pero sin consumidor.

## 7. Linaje modelo/dataset

Hashes de entrada/final del probe:

| Artefacto | SHA-256 |
|---|---|
| dataset final | `01A07A541E9D86F672AD63976803EBFF370D1C8D0CD1A1C229EAD82F289CD03E` |
| intent joblib | `EF5712BF60B5AF6DCBBC6067511442EA8F6497DE4544BA73CA96C1350BCD267C` |
| route joblib | `9A543B4E3823C8A2AAB4E6581EB8AAF1DEEC694EAAD6519939635CA907B80BB9` |
| trainer | `E1ED023B19A83DF848ABBD34C89A2F8BAE1503EF4A2D17A7C11AA61BACA08DC8` |

Para ambos modelos se recreó el pipeline exacto en RAM con el CSV actual:

- vocabulario exacto;
- IDF exacta;
- los 150 árboles exactos, incluidos splits/thresholds/values;
- 863 muestras.

Esto demuestra que los joblib actuales fueron producidos por el dataset, receta y
orden actuales. No depende solo de timestamp o suposición.

Límites de reproducibilidad:

- mismo dataset+orden+script produce probabilidades idénticas;
- invertir el orden conserva datos pero cambió probabilidades hasta 0.06 y una de
  cinco labels del set de comparación;
- los joblib están bajo `lazzaro_data/`, ignorado por Git;
- no contienen manifiesto de commit/hash/métricas;
- `train_triage_model.py` busca por default `dataset_shingon.csv` en raíz, archivo
  que no existe; el CSV real está en el subdirectorio histórico.

## 8. Dataset

Dataset final: 863 filas, tres columnas, sin nulos/vacíos.

| Intent | Ejemplos | Route |
|---|---:|---|
| CONVERSATION_SAVE | 88 | persona |
| CORE_SYSTEM | 57 | persona |
| MEMORY_QUERY | 73 | persona |
| MEMORY_UPDATE | 65 | persona |
| MOTOR_REFERENCE | 119 | motor |
| MOTOR_SIMPLE | 100 | motor |
| ORACLE_QUERY | 113 | oracle |
| PERCEPTION | 61 | motor |
| SOCIAL | 129 | persona |
| UNKNOWN | 58 | persona |

Route total: persona 470, motor 280, oracle 113. Ratio clase mayor/menor: 2.263;
el RF usa `class_weight=balanced`.

Calidad estructural:

- 863 textos exactos únicos;
- 0 duplicados exactos;
- 0 duplicados después de lowercase+whitespace normalizado;
- 0 conflictos texto/intent o texto/route;
- 0 rutas fuera del mapping canónico;
- longitud min 3, mediana 34, p95 77.9, max 234;
- señal de near-duplicate en split recreado: 0 vecinos >=0.90 y solo 2 >=0.80.

No se detectó leakage obvio por duplicados exactos/casi exactos en el split
recreado. El workbook de curación documenta 714 registros crudos y 863 finales,
con fuentes/linaje por fila; no contiene métricas del modelo entrenado.

### Métricas correctamente separadas

A. **Históricas originales:** no existen en trainer, joblib ni workbook.

B. **Evaluación offline recreada, estratificada 80/20, random_state 42:**

- RF puro: accuracy 0.693642, macro-F1 0.694155;
- router operativo con threshold+fallback: accuracy 0.578035;
- en 173 casos operativos: 58 path=model, 113 path=fallback y 2 path=rule;
- F1 más bajo RF puro: UNKNOWN 0.48, PERCEPTION 0.5455, ORACLE 0.5946;
- el resultado no es una métrica histórica ni del joblib entrenado con 100% del CSV.

C. **Modelo activo sobre su propio corpus de entrenamiento:** 1.0 / 0 errores;
resubstitution, no generalización.

La caída del router operativo confirma que el threshold no garantiza mejora: remite
la mayoría de casos a reglas más simples y puede cambiar una predicción baja pero
correcta.

## 9. Taxonomía y matriz Intent → Route

| IntentType | Route canónica | Puede emitir RF | Originales observadas | Terminal effective |
|---|---|---:|---|---|
| SOCIAL | persona | sí | persona | persona |
| MOTOR_SIMPLE | motor | sí | motor | persona |
| MOTOR_REFERENCE | motor | sí | motor | persona |
| PERCEPTION | motor | sí | motor | persona |
| MEMORY_QUERY | persona | sí | persona | persona |
| MEMORY_UPDATE | persona | sí | persona | persona |
| CONVERSATION_SAVE | persona | sí | persona | persona |
| CORE_SYSTEM | persona | sí | persona, noop, motor | persona salvo oracle no aplica |
| ORACLE_QUERY | oracle | sí | oracle | oracle |
| INTERNAL | internal | no | internal | persona |
| NOOP | noop | no | noop | persona |
| UNKNOWN | persona | sí | persona, noop | persona |

Todas las labels del intent model normalizan. `INTERNAL`/`NOOP` son inaccesibles al
RF porque representan eventos, no comandos del dataset. El route model legacy solo
conoce rutas cognitivas; tampoco modela internal/noop.

Overrides intencionales:

- inicio: CORE_SYSTEM/noop;
- dolor: CORE_SYSTEM/motor;
- unsupported: UNKNOWN/noop;
- movimiento activo: intent final NOOP/noop;
- terminal: conserva intent/route original en AuditTrace y usa persona operativa.

Las combinaciones no canónicas internas son explícitas. Las accidentales vienen de
decisiones semánticas equivocadas (por ejemplo SOCIAL/persona para un comando motor),
no de un route model contradictorio.

## 10. `normalize_intent`

- `IntentType` pasa sin cambio;
- string se strippea y upper-case;
- aliases: ORACLE→ORACLE_QUERY, MOTOR→MOTOR_SIMPLE,
  MEMORY→MEMORY_QUERY, SYSTEM→CORE_SYSTEM;
- vacío/None/label desconocida → UNKNOWN;
- `route_for_intent(UNKNOWN)` → persona.

Una clase nueva emitida por un modelo incompatible se convierte a UNKNOWN/persona,
pero conserva confidence del modelo y un reason de ML.

## 11. Confidence

| Fuente | Cálculo | Rango real | Semántica | Comparable |
|---|---|---|---|---:|
| hard evento/manual/vacío | constante | 1.0 | certeza de regla | no |
| guardrail/movimiento | constante | 0.95 | prioridad de regla | no |
| unsupported | constante | 0.0 | tipo no soportado | no |
| ML aceptado | `round(max(predict_proba),2)` | nominal 0..1 | probabilidad RF de clase ganadora | solo dentro de ML |
| fallback conocido sin ML/error | max(0.5,0.55) | 0.55 | match keyword | no |
| fallback conocido tras ML bajo | max(conf ML,0.55) | 0.55..0.59 | mezcla de dos fuentes | no |
| fallback UNKNOWN | min(conf previa,0.4) | normalmente <=0.4 | ausencia de match | no |
| excepción fallback | constante | 0.5 | doble fallo | no |

El threshold se compara **después de redondear**; un valor bruto cercano a 0.595
puede aceptarse como 0.60. No hay clamp ni validación de finitud: fakes devolvieron
confidence 1.5 y NaN. El sklearn activo normal no produce esos valores.

UNKNOWN tampoco tiene una semántica única:

- ML puede estar muy seguro de la clase UNKNOWN (`no recuerdes esto`: 0.81);
- fallback UNKNOWN suele ser <=0.4;
- evento no soportado usa 0.0;
- route puede ser persona o noop.

`reason` describe normalmente rule/model/fallback, pero no existe campo `path`; hay
que inferirlo del texto. El guard de movimiento reemplaza el reason anterior y no
expone la predicción ML/fallback que bloqueó.

## 12. Fallback

Modelo ausente, corrupto, incompatible, `predict` fallido, extracción fallida o
`predict_proba` fallido se degradan al mismo `REGLAS_BASE`. Boot no falla.

El fallback es determinista, pero no equivalente al modelo:

- `toma la esfera`: activo → MOTOR_SIMPLE/motor 0.67; sin modelo → UNKNOWN/persona;
- typo social: activo → SOCIAL 0.93; sin modelo → UNKNOWN/persona;
- `descansa por favor`: activo → CORE_SYSTEM; sin modelo → SOCIAL por `por favor`.

Una excepción dentro de manual/guardrail/hard rule no la captura Triage. Orchestrator
convierte la decisión a `{}`. Para comando no-terminal, route vacía cae al
`ReasoningEngine` legacy; para terminal, el adaptador transforma `{}` a persona. Este
fallback es por canal y no está expresado en el contrato de Triage.

## 13. Adversariales observados

El probe versiona 35 casos sintéticos. Selección representativa:

| Input/tipo | Intent | Route | Effective | Conf. | Path |
|---|---|---|---|---:|---|
| hola | SOCIAL | persona | persona | 0.81 | model |
| cómo estás | SOCIAL | persona | persona | 0.55 | fallback |
| ve al cubo | UNKNOWN | persona | persona | 0.40 | fallback |
| por favor ve al cubo | SOCIAL | persona | persona | 0.55 | fallback |
| gira a la izquierda | MOTOR_SIMPLE | motor | motor | 0.95 | model |
| toma la esfera | MOTOR_SIMPLE | motor | motor | 0.67 | model |
| no vayas al cubo | UNKNOWN | persona | persona | 0.40 | fallback |
| explica qué es una mutex | ORACLE_QUERY | oracle | oracle | 0.63 | model |
| recuerda que mi color favorito es azul | MEMORY_UPDATE | persona | persona | 0.88 | model |
| qué recuerdas de mí | MEMORY_QUERY | persona | persona | 0.81 | model |
| no recuerdes esto | UNKNOWN | persona | persona | 0.81 | model |
| me gusta cuando vas al cubo | SOCIAL | persona | persona | 0.55 | fallback |
| habla de cómo girar | MOTOR_SIMPLE | motor | motor | 0.55 | fallback |
| ve al cubo y dime qué ves | PERCEPTION | motor | motor | 0.55 | fallback |
| emoji puro | UNKNOWN | persona | persona | 0.40 | fallback |
| texto mixto | UNKNOWN | persona | persona | 0.40 | fallback |
| resultado_accion | INTERNAL | internal | internal | 1.0 | rule |
| actualizacion_estado | NOOP | noop | noop | 1.0 | rule |
| percepción no soportada | UNKNOWN | noop | noop | 0.0 | rule |
| inicio_simulacion | CORE_SYSTEM | noop | noop | 1.0 | rule |
| alerta_dolor_fisico | CORE_SYSTEM | motor | motor | 1.0 | rule |
| motor mientras moving | NOOP | noop | noop | 0.95 | rule |
| motor desde terminal | MOTOR_SIMPLE | motor | persona | 0.95 | model+adapter |

Negaciones del set final suelen quedar UNKNOWN, lo cual falla seguro. No hay soporte
multi-intent: una sola label gana; no se preserva lista, orden ni segunda tarea.

## 14. Reproducibilidad

- mismo input+estado, 10 ejecuciones: dict completo idéntico;
- no hay randomización en inferencia;
- RF usa random_state 42;
- mismo dataset y orden: entrenamiento RAM exacto;
- cambiar orden altera bootstrap/probabilidades;
- mismo mensaje Unity vs sandbox: decisión original idéntica;
- terminal cambia solo la ruta efectiva después;
- estado solo cambia Triage si activa movimiento.

La reproducibilidad depende de poseer el joblib exacto o de reconstruir con el mismo
orden; hoy el joblib no está versionado por Git.

## 15. Eventos no comando

Todos los eventos que superan cache/retry/BehaviorCore llegan a `route_event`, aunque
no pidan decisión. Bypass del RF no significa bypass de Triage.

| Tipo | Intent/route |
|---|---|
| actualizacion_estado | NOOP/noop |
| resultado_accion | INTERNAL/internal, aunque traiga mensaje/solicita_decision |
| inicio_simulacion | CORE_SYSTEM/noop |
| alerta_dolor_fisico | CORE_SYSTEM/motor; Orchestrator además usa interrupción crítica |
| cualquier otro | UNKNOWN/noop |

Cache hit no llama Triage. Retry especializado de save manual tampoco. Un retry
general pre-cache después de fallo posterior sí vuelve a pasar Behavior y Triage.

## 16. Failure matrix

| Falla | Captura | Intent/route | Conf. | Reason/fallback | Side effects/visibilidad |
|---|---|---|---:|---|---|
| joblib ausente | constructor Triage | heurística | 0.55/0.4 | ML no disponible + fallback | boot continúa; AuditTrace ve final |
| joblib corrupto/incompatible | constructor Triage | heurística | 0.55/0.4 | mismo | boot continúa |
| feature/predict error | `_predict_intent_with_ml` | heurística | 0.55/0.4 | ML falló + fallback | log + final visible |
| predict_proba error | mismo | heurística | 0.55/0.4 | mismo | predict ya ocurrió, sin write |
| clase desconocida | normalize | UNKNOWN/persona | conserva proba | reason ML | raw label se pierde |
| proba >1/NaN fake | no captura | intent normalizado | >1/NaN | reason ML | puede llegar a AuditTrace |
| fallback exception | `_predict_intent_with_rules` | UNKNOWN/persona | 0.5 | doble fallo | visible en reason |
| manual/guardrail exception | Orchestrator | `{}` | None | sin reason Triage | nonterminal→Reasoning; terminal→Persona |
| evento no-dict | Orchestrator | `{}` | None | igual | igual |
| detalles no-dict | Triage normaliza | NOOP/noop | 1.0 | comando vacío | estable |
| estado parcial | helpers | normal | normal | no falla | solo movimiento relevante |
| route_event retorna no-dict | Orchestrator | `{}` | None | warning | mismo fallback por canal |

Una decisión `{}` no corta flujo, no es motor/persona/oracle y en canal no-terminal
cae a Reasoning. Es el fallback real que consume Orchestrator; no está documentado en
el contrato cognitivo como ruta de Triage.

## 17. AuditTrace

OBS-004 sigue correcto y congelado:

- Triage se ejecuta una vez por turno nominal;
- snapshot copia intent, route, confidence y reason **antes** de adaptación;
- effective_route se toma después;
- terminal conserva route motor original y effective persona;
- AuditTrace no reejecuta Triage;
- cache hit no fabrica stage;
- fallo previo en BehaviorCore no fabrica stage;
- fallo del writer no cambia decisión funcional.

Ante excepción de Triage, el stage sí existe porque la frontera fue alcanzada, pero
los originales son `None`; en no-terminal effective también es `None`, y en terminal
effective puede ser persona por adaptación de `{}`. Esto refleja el flujo real.

## 18. Tickets nuevos

### `TRIAGE-AUDIT-001` — P1 — fronteras léxicas enrutan acciones y conversación al revés

- **Clase:** incoherencia central intención/ruta.
- **Evidencia:** joblib activo+fallback: `ve al cubo`→UNKNOWN/persona;
  `por favor ve al cubo`→SOCIAL/persona; `habla de cómo girar`→MOTOR/motor.
- **Ubicación:** `ai_engine/triage_router.py:136-203,246-314`,
  `ai_engine/routing_rules.py:34-239`.
- **Reproducción:** `adversarial_and_reproducibility`.
- **Impacto:** una orden común no llega a Capa 1 y una petición discursiva puede
  llegar a Motor. Motor mantiene sus guardrails, por lo que no se clasifica P0.
- **Recomendación preliminar:** suite de fronteras/negaciones y reglas sintácticas
  explícitas antes de retocar corpus/threshold; medir routing operativo.
- **Estado:** OPEN.

### `TRIAGE-CONTRACT-001` — P1 — UNKNOWN no separa incertidumbre de clase segura

- **Clase:** contrato bloqueante para learner.
- **Evidencia:** UNKNOWN puede ser ML 0.81, fallback <=0.4 o evento 0.0; route puede
  ser persona/noop; no existe `path` ni raw predicted intent.
- **Ubicación:** `ai_engine/triage_router.py:192-203,246-314`,
  `ai_engine/intent_types.py:52-81`.
- **Reproducción:** `no recuerdes esto`, emoji, evento unsupported y fake class.
- **Impacto:** un futuro DevelopmentalCore no distingue “modelo seguro de que es
  ambiguo” de “modelo caído/no sabe”; puede aprender confianza falsa.
- **Recomendación preliminar:** outcome de clasificación explícito con source,
  raw label, uncertainty/failure y final normalized intent; no ampliar v1 ahora.
- **Estado:** OPEN.

### `TRIAGE-AUDIT-002` — P1 — excepción Triage activa fallback cognitivo por canal

- **Clase:** fallback central no contractual.
- **Evidencia:** hard-rule/evento mal formado escapa; `_evaluar_triage` devuelve
  `{}`; Unity/sandbox cae a Reasoning legacy, terminal adapta vacío a Persona.
- **Ubicación:** `ai_engine/triage_router.py:75-221`,
  `core/orchestrator.py:1241-1315,1345-1359,741-769`.
- **Reproducción:** `failures_and_confidence`.
- **Impacto:** el fallo de Capa 0 no significa safe NOOP/UNKNOWN estable y cambia por
  canal; puede habilitar una capa que Triage nunca autorizó.
- **Recomendación preliminar:** decidir un fallback tipado único y fail-safe,
  separado del legacy Reasoning; conservar error visible.
- **Estado:** OPEN.

### `TRIAGE-DATA-001` — P2 — no existe evaluación histórica y fallback degrada split recreado

- **Clase:** cobertura/evaluación.
- **Evidencia:** trainer ajusta 100% sin split/métricas; recreated RF=69.36%, router
  operativo=57.80%, 113/173 casos fueron fallback. Resubstitution activo=100%.
- **Ubicación:** `train_triage_model.py:20-87`, dataset/workbook.
- **Reproducción:** `dataset_profile_and_offline_evaluation`.
- **Impacto:** no hay gate que detecte degradación en clases/fronteras y accuracy de
  entrenamiento puede parecer salud productiva.
- **Recomendación preliminar:** evaluación versionada estratificada+adversarial,
  métricas por clase/path y comparación antes de reemplazar joblib.
- **Estado:** OPEN.

### `TRIAGE-CONTRACT-002` — P2 — confidence mezcla escalas y acepta no-finitos

- **Clase:** score ambiguo.
- **Evidencia:** constantes, max-proba y score de keyword comparten campo; fallback
  mezcla confidence ML previa; fakes 1.5/NaN pasaron; threshold usa valor redondeado.
- **Ubicación:** `ai_engine/triage_router.py:115-203,246-336`.
- **Reproducción:** `failures_and_confidence`.
- **Impacto:** consumers no pueden comparar 0.55 heuristic con 0.55 ML ni asumir
  [0,1]/finitud.
- **Recomendación preliminar:** source-specific score y confidence final validada;
  threshold sobre proba sin redondear.
- **Estado:** OPEN.

### `TRIAGE-MODEL-001` — P2 — artefacto reproducible localmente pero no desplegable por contrato

- **Clase:** provenance/lifecycle.
- **Evidencia:** joblibs exactos al CSV/script actual, pero ignorados por Git, sin
  manifiesto; dataset default del trainer no existe; orden cambia el forest.
- **Ubicación:** `.gitignore:15`, `train_triage_model.py:8-14,60-83`,
  `ai_engine/triage_router.py:52-72`.
- **Reproducción:** `model_dataset_lineage` y `adversarial_and_reproducibility`.
- **Impacto:** otra instalación puede arrancar solo con heurísticas o generar un
  modelo distinto sin evidencia de versión.
- **Recomendación preliminar:** manifest versionado con hashes/entorno/métricas y
  ruta dataset válida; política explícita de distribución del artefacto.
- **Estado:** OPEN.

### `TRIAGE-DATA-002` — P2 — taxonomía single-label descarta multi-intent

- **Clase:** cobertura funcional.
- **Evidencia:** `ve al cubo y dime qué ves` conserva solo PERCEPTION; variante con
  “qué viste” cayó SOCIAL. Dataset/contrato exigen intención dominante.
- **Ubicación:** dataset `Contrato`, `train_triage_model.py`, router.
- **Reproducción:** casos `amb_multi` y variante adversarial.
- **Impacto:** órdenes compuestas pierden acción o respuesta secundaria; orden de
  palabras/keywords cambia la capa ganadora.
- **Recomendación preliminar:** declarar rechazo/clarificación o descomposición
  posterior; no fingir multi-intent con una label.
- **Estado:** OPEN.

### `TRIAGE-MODEL-002` — P3 — clasificador de route y mapping duplicado son legacy

- **Clase:** deuda/nombres.
- **Evidencia:** trainer genera route joblib exacto pero runtime no lo referencia;
  `INTENT_TO_ROUTE_MAP` se construye y tampoco se usa.
- **Ubicación:** `train_triage_model.py:64-84`,
  `ai_engine/triage_router.py:35-42,52-72`.
- **Reproducción:** mapa de referencias y `model_dataset_lineage`.
- **Impacto:** confunde ownership y candidato histórico; no altera routing actual.
- **Recomendación preliminar:** retirar/documentar como artefacto de análisis cuando
  exista migración de modelo, sin tocarlo durante auditoría.
- **Estado:** OPEN.

## 19. Respuestas a las 100 preguntas obligatorias

1. Dict completo + estado post-Behavior.
2. `model_dump(mode="python")` completo.
3. Tipo, texto y movimiento.
4. Mensaje/tipo/movimiento sí; origen/tarea/percepción/solicita/objeto no directos.
5. Behavior/estado/tarea/personalidad ya mutaron.
6. Strip, luego TF-IDF o normalizador heurístico.
7. Case en TF-IDF; acentos solo reglas; whitespace exterior; puntuación tokenizada.
8. NOOP/noop 1.0.
9. Igual.
10. Igual.
11. Sin límite; se vectoriza.
12. Unicode palabras sí; emoji puro vector vacío.
13. Sin soporte especial; ejemplo UNKNOWN.
14. Reglas de evento, vacío, save y guardrail.
15. §5.
16. Sí.
17. Sí.
18. Primeras cuatro por tipo; demás por mensaje/movimiento.
19. Sí en `REGLAS_BASE`.
20. Menor prioridad numérica, pero solo en fallback.
21. Pipeline sklearn TF-IDF+RF.
22. `lazzaro_data/triage_model_intent.joblib`.
23. Captura y degrada a heurísticas.
24. Sí.
25. Solo degrada; boot sigue.
26. Dos entrenados, uno usado.
27. Trainer sí; runtime no.
28. `route_for_intent`.
29. Sí, route joblib legacy.
30. Sí para sus 10 clases.
31. Sí.
32. INTERNAL y NOOP.
33. No actualmente.
34. Strip+upper+aliases+Enum; desconocido→UNKNOWN.
35. None/vacío/nuevo label.
36. Persona canónica.
37. §9/§15.
38. Sí, por overrides intencionales.
39. CORE/noop, CORE/motor, UNKNOWN/noop.
40. Semántica equivocada, no mapping ML-route independiente.
41. En comandos, sí; hard rules escriben route directa.
42. Sí.
43. Depende de source; no una sola semántica.
44. En ML, max predict_proba.
45. En fallback, mezcla de proba previa y constantes.
46. No.
47. Sí con modelo/fake mal formado; no clamp.
48. 1.0 o 0.95; unsupported 0.
49. 0.55 conocido, <=0.4 UNKNOWN, 0.5 doble fallo.
50. Normalmente sí por texto.
51. Clase desconocida sigue diciendo ML, correctamente respecto al path, pero oculta raw.
52. Sí con mismo modelo/input/estado.
53. No en inferencia.
54. Sí, 42.
55. Sí con mismo orden/entorno; no frente a reordenamiento.
56. Sí.
57. 863 filas; modelo exacto al CSV final.
58. Sí, igualdad completa de vectorizador/árboles; no manifest embebido.
59. Sí si cambia orden/versión.
60. 57–129, ratio 2.263.
61. §8.
62. No exactos.
63. No.
64. No señal fuerte: 0 >=0.90, 2 >=0.80 en split recreado.
65. Trainer no hace split; evaluación audit usó 80/20.
66. Solo la evaluación audit, sí.
67. Ninguna histórica.
68. No aplica.
69. Menor CORE_SYSTEM 57, UNKNOWN 58.
70. Sí, dataset curado y fallback son léxicos.
71. Predomina vocabulario/ngramas; no comprensión composicional.
72. Ejemplos negados fueron UNKNOWN; no parser de negación.
73. Puede separar algunos, pero `puedes ir` quedó UNKNOWN; `qué es` Oracle.
74. Ejemplo “me gusta” quedó Social.
75. Discusión sobre girar activó Motor.
76. UNKNOWN/persona o primera keyword.
77. Algunos typos memorizados funcionan; no fuzzy.
78. Una sola label.
79. No.
80. RF si >=.60; si no, primera regla prioritaria.
81. Sí en OOD/ambigüedad; ejemplos centrales funcionaron.
82. Sí: `explica el cubo` quedó UNKNOWN/persona.
83. Sí: `habla de cómo girar`.
84. No: resultado_accion hard INTERNAL aunque solicite.
85. Sí, Triage corre; Orchestrator decide después si actuar.
86. Bypass del RF, no de Triage.
87. actualizacion, comando vacío, movimiento motor bloqueado.
88. resultado_accion.
89. dolor físico; comandos clasificados Motor/Perception.
90. Original igual; post-adapter persona salvo persona/oracle.
91. Original igual; sandbox ejecuta localmente después, Unity despacha.
92. Solo effective_route después.
93. Sí, salvo fallo Triage con valores None coherentes.
94. Sí.
95. Sí.
96. Sí para retry general pre-cache; no retry manual especializado.
97. ML error sí, heurística estable; excepción externa no.
98. Reasoning legacy no-terminal; Persona terminal.
99. No como contrato Triage explícito.
100. Source, raw label, normalized/final, model health, uncertainty, override y error.

## 20. Límites para Bloque 6

No reabrir en el siguiente bloque:

- Behavior ocurre antes de Triage;
- route es autoridad operativa;
- modelo de route no se usa;
- intent model está sincronizado hoy con CSV/script;
- UNKNOWN/confidence no tienen semántica única;
- terminal solo cambia effective route después;
- no hay multi-intent;
- fallo Triage `{}` activa fallback externo por canal.

Un futuro DevelopmentalCore necesita distinguir al menos:

- classification source: rule/model/fallback/failure;
- model loaded/version/hash;
- raw label/probabilities vs intent normalizado/final;
- route canónica, override de Capa 0 y adaptación de canal;
- “UNKNOWN como clase” vs “no pudo clasificar”;
- motor bloqueado por estado vs intención no motora;
- single-intent truncado/ambigüedad;
- confidence finita y específica de su fuente.

AuditTrace v1 permanece congelado; estas necesidades se documentan, no se
implementan aquí.

## 21. Pruebas, archivos y gate

Archivos del bloque:

- `docs/audit/block_05_capa0_triage.md`
- `docs/audit/probes/triage_deep_probe.py`

El probe tiene cinco grupos:

1. linaje exacto modelo/dataset;
2. perfil/deduplicación/evaluación offline;
3. mapping/input influence;
4. adversariales/reproducibilidad;
5. failure/confidence matrix.

Barreras:

- sockets bloqueados;
- construcción Chroma bloqueada;
- sin LLM/Unity/acción física;
- hashes de dataset/modelos/trainer antes/después;
- modelos recreados solo en RAM;
- ningún dump/joblib/CSV/XLSX escrito.

Checks de cierre ejecutados el 2026-09-02:

- `python docs/audit/probes/triage_deep_probe.py`: PASS, 5/5 grupos;
- `python -m compileall -q docs/audit/probes/triage_deep_probe.py`: PASS;
- `python docs/audit/probes/regression_probe.py`: gate PASS, isolation gate PASS,
  160 PASS, 1 `KNOWN_XFAIL`, 0 fallos inesperados, 161/161 unidades ejecutadas;
- `git diff --check`: PASS.

Hashes SHA-256 de los tres archivos Chroma activos, iguales antes y después de los
probes:

- `data_level0.bin`:
  `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659`;
- `length.bin`:
  `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A`;
- `chroma.sqlite3`:
  `884E69923890C2470819E32939F60387C68FCF6C80300688A77364A7C8454363`.

Los tres archivos runtime preexistentes bajo `lazzaro_data/` permanecen modificados
y deliberadamente fuera del commit. No se aplicaron fixes ni se inició el Bloque 6.
