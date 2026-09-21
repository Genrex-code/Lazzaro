# ROOT-024 — Deterministic lexical routing

Estado de este packet: `IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Defecto reproducido

TRIAGE-AUDIT-001 mostró que una decisión podía depender de coincidencias
léxicas accidentales: una keyword dentro de otra palabra, una orden negada o
una keyword citada podía terminar en una ruta física. También existía una
colisión relacional concreta: la secuencia histórica `te cree` podía aparecer
dentro de `gente cree` cuando se buscaba como substring.

La caracterización previa a la revisión del contrato tenía 57 casos, 54 de
ellos gating: 35 pasaban y 19 fallaban. Dentro de esos fallos se contaron 11
falsos positivos Motor y 4 falsos negativos Motor. El contrato final cambió la
autoridad de varios casos, añadió casos observe-only y agregó el control citado
`SAV-003`; por ello el denominador final no se presenta como si fuera el mismo
dataset histórico.

## Arquitectura implementada

El flujo de comandos de usuario queda así:

```text
raw text
→ LexicalView
→ tabla ordenada de LexicalRule
→ LexicalMatch (positivo opcional + constraints acumulados)
→ guardrail relacional con tokens
→ modelo existente
→ fallback existente
→ veto de candidato por forbidden_intents/forbidden_routes
→ normalize_intent + route_for_intent existentes
```

Las reglas no llaman al modelo ni producen efectos. Cada una tiene `rule_id`,
`tier` y `priority`; la construcción valida IDs/prioridades duplicados y orden
declarativo. Un constraint no inventa un intent alternativo: únicamente veta
un candidato incompatible y deja continuar el pipeline existente. El mismo
validador se aplica a candidatos `CONTROL`, `POSITIVE`, ML y fallback. En
particular, el control `manual_conversation_save` ya no puede saltarse
`constraint.protected_save`.

No se modificó el texto enviado al TF-IDF/RandomForest. `LexicalView` es una
vista paralela exclusiva de las reglas ROOT-024.

## LexicalView y normalización

La vista conserva `raw_text`, `normalized_text`, tokens con offsets y spans
protegidos. Su normalización es exactamente:

1. `str(input)`;
2. Unicode NFC;
3. `casefold`;
4. trim;
5. colapso de runs de whitespace;
6. conservación de diacríticos y puntuación.

La tokenización usa tokens Unicode `\w+` con offsets sobre el texto normalizado.
No hay stemming, lematización, fuzzy matching ni eliminación global de acentos.
Los alias acentuados/no acentuados aceptados son explícitos.

## Spans protegidos

Se detectan comillas simples/dobles/curvas, backticks, URLs HTTP/WWW y paths
absolutos Windows/POSIX. Un delimitador sin cierre protege
conservadoramente hasta el final del texto. Los tokens totalmente contenidos
en esos spans no activan reglas positivas físicas o de memoria. Esto es una
protección léxica pequeña, no un parser general de código o lenguaje natural.

## Fronteras, contexto y negación

Los guardrails relacionales usan secuencias de tokens completos; `te cree` ya
no coincide con `gente cree`, ni `creador` con `recreador`.

La frontera de lookalikes es una propiedad general y acotada: detecta lexemas
Motor sensibles (`mueve`, `mover`, `abre`, `usa`) embebidos dentro de un token
distinto. Así, `promueve`, `remueve`, `abre2` y `causa` no autorizan Motor. No se
enumeran las cuatro frases del fixture. Si el texto contiene además un token
Motor completo e independiente —por ejemplo, `promueve el arte y mueve la
caja`— el comando real no queda bloqueado por el lookalike.

La negación local reconoce `no`, `no quiero`, `no hagas`, `nunca`, `evita` y
`sin` dentro de una ventana corta sin cruzar puntuación fuerte. También existen
constraints para narración pasada/futura, hipótesis, metalingüística, citas,
código, URLs, guardado conversacional y actualización de memoria. Una cláusula
Oracle positiva independiente puede sobrevivir al veto Motor, como en `sin
mover la caja, explícame el plan`.

`deja de mover la caja` permanece observe-only. El consumidor Motor actual lo
materializa como `navegar_a_objeto`, por lo que ROOT-024 no puede declarar una
semántica de cese correcta sin ampliar scope.

## Positivos léxicos y precedencia

Los positivos exactos del contrato son pequeños y explícitos:

- consulta técnica `por qué ... gente cree ... código/falla`;
- `explícame el plan`;
- `qué significa ...`;
- literal protegido seguido por `da error`;
- `cómo se abre ...`;
- control exacto de guardado conversacional.

La precedencia efectiva es: reglas duras no textuales, comando vacío, análisis
léxico agregado, validación de control, guardrail relacional, validación de
positivo, modelo, fallback con los mismos vetos, mapping canónico y gate de
movimiento. La adaptación terminal sigue después, en Orchestrator, sin cambios.

## Fixture contractual

`ai_engine/fixtures/root_024_triage_contract_v1.jsonl` es UTF-8, está ordenado
por `case_id` y contiene 58 casos:

| Autoridad | Casos | Semántica |
|---|---:|---|
| `lexical` | 31 | Sus predicados sobreviven al modelo normal, ausente y contradictorio. |
| `model_fallback` | 22 | Se evalúan con el pipeline local configurado. |
| `observe_only` | 5 | Se ejecutan, pero no imponen resultado semántico. |

Hay 53 casos gating. Resultado final: 53 pass, 0 fail. Entre los casos que
prohíben Motor quedan 0 falsos positivos Motor; entre los casos que exigen
Motor quedan 0 falsos negativos Motor. `TYP-001` conserva explícitamente el
resultado actual `UNKNOWN`: fuzzy typo correction quedó fuera de ROOT-024.

El dataset contractual no es el corpus de entrenamiento. No se copió al CSV,
no reentrenó el modelo y no garantiza calidad ML ni reproducibilidad entre
deployments; ROOT-046 sigue siendo su propietario.

## Pruebas permanentes

El runner valida schema, IDs, enums, autoridad y ausencia de contradicciones;
ejecuta los 31 casos léxicos con modelo normal, ausente y contradictorio; y
evalúa los 22 casos dependientes con el artifact local actual. Incluye pruebas
directas de:

- token boundaries generales y comando real coexistente;
- comillas/código/URL/path y delimitador sin cerrar;
- guardado directo frente al mismo control completamente citado;
- negación y actualización de memoria;
- NFC/NFD, case, whitespace y puntuación;
- input original del modelo sin reescritura;
- estabilidad de tabla y 20 repeticiones de casos representativos;
- proyección `route`/`effective_route` en Unity, sandbox y terminal.

## Exclusiones y riesgos residuales

No se absorbieron ROOT-025 (UNKNOWN), ROOT-026 (excepciones), ROOT-046
(provenance/evaluación ML), ROOT-047 (confidence) ni ROOT-048 (multi-intent).
Tampoco se añadió inglés general, fuzzy correction, parser completo o semántica
física de stop/cessation.

Persisten riesgos deliberados: la ventana de negación es superficial; las
frases subordinadas o doble negación pueden excederla; spans de código
complejos no forman un AST; los lexemas embebidos protegidos son una lista
acotada; y los casos model/fallback siguen dependiendo del artifact desplegado.
Estas limitaciones están visibles en el fixture o asignadas a los ROOTs
separados, en vez de ocultarse con intents inventados.
