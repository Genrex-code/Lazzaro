# Fase 2 — Bloque 4: Memory

Fecha de cierre: 2026-09-02

Alcance: auditoría profunda, sin fixes funcionales

Estado: **CERRADO**

## 1. Resumen ejecutivo

LAZZARO tiene cuatro familias reales de memoria:

1. una colección Chroma persistente compartida por recuerdos episódicos y
   semánticos, diferenciados por metadata `tipo`;
2. `WorkingMemory`, una `deque` RAM de dispatches recientes;
3. `ConversationMemory`, una `deque` RAM de turnos con lock;
4. caches/recibos RAM en `MemoryManager` para dedupe de hechos, aliases, afecto y
   snapshots manuales pendientes.

El camino persistente normal es `MemoryEncoder → MemoryDocument → ChromaClient`.
El episodio conserva ID, narrativa, campos centrales y metadata escalar, pero
**pierde siempre `estado_emocional`** y todo `extra` no escalar. Los extras escalares
pueden sobrescribir campos reservados. La memoria semántica conserva regla y origen,
con las mismas pérdidas/colisiones de metadata.

La recuperación híbrida ejecuta una consulta episódica y una semántica, calcula
`metadata_score`, mezcla y ordena descendente. `OBS-005` sigue haciendo una sola
recuperación híbrida y usa exactamente esa lista para texto, IDs y scores; no existe
una segunda recuperación de auditoría. Sin embargo, “no hay recuerdos”, fallo total
y varios fallos parciales quedan indistinguibles en las fronteras públicas.

La consolidación es intencionalmente lossy y sigue el orden seguro relativo
**guardar regla → borrar episodios**. No se demostró una ruta actual que borre las
fuentes antes de confirmar la regla. Sí se reprodujeron estados no atómicos: regla y
episodios pueden coexistir tras fallo de delete, y dos consolidaciones concurrentes
pueden crear dos reglas para el mismo lote. El decay solo puntúa/selecciona; no borra.

El hallazgo nuevo más peligroso es de recibo: `guardar_experiencia()` puede guardar
el episodio, fallar después al programar consolidación y devolver `None`. Un retry
legítimo puede entonces duplicar el episodio. También se reprodujo que un único
timestamp aware en un lote hace fallar el reranking completo y que el consumidor
termina viendo una recuperación vacía.

Resultado del bloque:

- **7 tickets nuevos OPEN**: 3 P1, 3 P2 y 1 P3;
- 9 tickets `MEM-CONTRACT-*` heredados confirmados/acotados;
- 0 P0;
- ningún fix funcional;
- AuditTrace v1 intacto;
- Chroma activa no abierta por el probe y hashes intactos al cierre.

## 2. Arquitectura real

```text
Evento autorizado por Orchestrator
       │
       ├─ _guardar_evento_en_memoria()
       │        │
       │        ▼
       │   MemoryManager.guardar_experiencia()
       │        │
       ▼        ▼
  MemoryEncoder.codificar_episodio()
       │
       ▼
 EpisodicMemory (RAM, previo a persistencia)
       │
       ▼
 MemoryEncoder.a_documento()
       │
       ▼
 MemoryDocument
       │
       ▼
 ChromaClient, colección "experiencias"
       │
       ├─ metadata tipo=episodico
       └─ metadata tipo=semantico

Recuperación autorizada
       │
       ▼
 MemoryManager.obtener_contexto_memoria_con_metadata()
       │ una llamada
       ▼
 MemoryRetriever.buscar()
       ├─ EpisodicMemoryStore.buscar() ── consulta Chroma #1
       └─ SemanticMemoryStore.buscar() ── consulta Chroma #2
                 │
                 ▼
          MemoryResult[]
      distancia raw + metadata_score
                 │
          decay + reranking
                 │
                 ▼
       MemorySummarizer.resumir()
                 │
                 ▼
       MemoryContextResult
       texto + IDs + scores alineados
                 │
                 ▼
            Orchestrator
```

Ramas paralelas:

```text
dispatch → WorkingMemory (RAM, max 20 por default, sin reconciliar outcome)

hablar → ConversationMemory (RAM, max 12 por default)
             ├─ resumen de continuidad
             ├─ extracción selectiva → regla relacional → Chroma
             └─ save manual → snapshot público → regla semántica → Chroma

resultado de acción exitoso → candidato alias → regla semántica → Chroma

API afectiva (sin productor productivo localizado) → regla semántica → Chroma

episodios observacionales viejos
    → regla consolidada semántica
    → delete de episodios fuente
```

## 3. Ownership

| Recurso | Dueño real | Escritores | Lectores | Cierre/eviction |
|---|---|---|---|---|
| `ChromaClient("experiencias")` | creado en `main`, operado/cerrado por `MemoryManager` | stores, save manual, relaciones, aliases, consolidación | stores, retriever, callbacks Motor | `MemoryManager.close()` |
| episodios y semántica | una misma colección; `tipo` los separa | `EpisodicMemoryStore`, `SemanticMemoryStore` | ambos stores | persistentes hasta delete |
| `WorkingMemory` | `MemoryManager` | Orchestrator tras gestionar ejecución | contexto cognitivo y conteo AuditTrace | `deque(maxlen=20)`; proceso |
| `ConversationMemory` | `MemoryManager` | Orchestrator tras `comando_usuario → hablar` | contexto, snapshot manual | `deque(maxlen=12)`; proceso |
| sets de dedupe | `MemoryManager` | reads y writes de aliases/afecto/relaciones | sus propios check-then-write | proceso; sin lock común |
| pending snapshots manuales | `MemoryManager` | save manual | retry del mismo ID | máximo 2000; se olvida en éxito/vacío |

Los stores no poseen clientes separados: ambos reciben el mismo wrapper y la misma
colección. Los wrappers Chroma del mismo path, incluso de colecciones distintas,
comparten un `RLock` de clase por path. Ese lock serializa cada llamada CRUD, no una
transacción de varios pasos de `MemoryManager`.

## 4. Mapa de persistencia

### Episódico

Orchestrator llama `guardar_experiencia()` para eventos que atravesaron la frontera
de early-return/cache y llegaron al flujo principal. La escritura ocurre antes de la
decisión de capa. No se escribe en cache hit ni en fallos anteriores a esa frontera.
Un retry pre-cache vuelve a escribir.

Campos productivos entregados por Orchestrator:

- `tipo_evento` derivado;
- narrativa construida desde evento/percepción;
- snapshot `estado_actual` como `estado_emocional`;
- valencia `0.0`;
- impacto calculado;
- extras escalares `origen`, `objeto`, `estado_fisico`, `accion`, `exito`.

Persistido finalmente:

- ID UUID;
- documento = narrativa;
- `tipo=episodico`, `tipo_evento`, valencia, impacto y timestamp ISO;
- extras solamente si son `str/int/float/bool`.

No persistido:

- `estado_emocional`, en todas las rutas episódicas;
- `None`, dicts, listas y otros extras no escalares;
- estructura original completa del evento/estado.

### Semántico

`guardar_regla()` crea un UUID y persiste documento = regla, metadata central,
`origen` y extras escalares. La usan consolidación, save manual, hechos relacionales,
aliases y la API afectiva. No hay dedupe genérico en `guardar_regla()`.

### Colisiones

`a_documento()` construye metadata reservada y después hace `update(extra_plano)`.
Por ello un extra escalar puede cambiar `tipo`, `tipo_evento`, `valencia`,
`impacto_emocional`, `timestamp` u `origen`. Esto confirma `MEM-CONTRACT-005`.

## 5. Mapa de retrieval

`MemoryRetriever.buscar()` siempre intenta:

1. episódica con `limite * 2`;
2. semántica con `limite`;
3. elimina episodios cuyo ID también apareció como semántico;
4. calcula scores in-place;
5. ordena `metadata_score` descendente;
6. recorta a `limite`.

Por recuperación híbrida normal hay **dos consultas Chroma**. `OBS-005` llama una
sola vez a esta recuperación híbrida; “una búsqueda” allí significa una operación
funcional, no una única query física. El mismo `MemoryResult[]` alimenta el resumen y
los pares `id/metadata_score`, conservando ranking y alignment.

Además del contexto largo, Orchestrator lee en el mismo bloque autorizado:

- resumen de `ConversationMemory`;
- búsqueda semántica relacional propia (una tercera query Chroma);
- resumen de `WorkingMemory`.

## 6. Roundtrip episódico

| Campo | Antes | Después de Chroma/store | Resultado |
|---|---|---|---|
| `id` | string | mismo | conserva |
| `narrativa` | string | documento | conserva |
| `tipo_evento` | string | metadata | conserva salvo colisión |
| `valencia` | float | metadata | conserva salvo colisión/tipo inválido |
| `impacto_emocional` | float | metadata | conserva salvo colisión/tipo inválido |
| `timestamp` | datetime naive | ISO → datetime | conserva normalmente |
| `extra` escalar | dict | metadata desconocida → `extra` | conserva |
| `extra` no escalar | dict/list/None | no se codifica | pierde |
| `estado_emocional` | dict/None | nunca se serializa | siempre `None` al leer |
| `distancia` | no existe | cosine raw de Chroma | se añade |
| `metadata_score` | no existe | default `1.0`, luego rerank | derivado mutable |

El probe temporal ejercitó el adaptador real y el store con embeddings sintéticos.
El alcance de `MEM-CONTRACT-001` es total: toda escritura episódica usa el mismo
encoder; consolidación tampoco recibe `estado_emocional`; retrieval no puede
reconstruirlo.

## 7. Roundtrip semántico

ID, regla, origen, timestamp, campos centrales y extras escalares sobreviven en el
caso válido. Extras no escalares se pierden. `origen` puede ser sobrescrito por
`extra`. El default directo `consolidacion` y los defaults del encoder/lector
`determinista` siguen divergentes (`MEM-CONTRACT-009`).

## 8. Error vs no-hit

La diferencia se conserva solo abajo:

```text
ChromaClient.buscar()
    error/unavailable → None
    no-hit válido     → QueryResult con ids=[[]]

Store.buscar()
    ambos → []

MemoryRetriever
    excepción por store → [] local
    un store falla + otro acierta → lista parcial sin health
    ambos fallan/no-hit → []

MemoryManager
    [] → resumen "No hay recuerdos relevantes..."
    excepción de rerank → [] → el mismo resumen
```

Por tanto, sí puede reportarse “no recuerdo” cuando Chroma falló. Un store puede
fallar mientras el otro retorna datos y esa recuperación parcial parece completa.
La excepción está en save manual: sus helpers usan `found/missing/error` y mantienen
la distinción para idempotencia.

## 9. Scoring y reranking

Para cada resultado:

```text
similitud = max(0, 1 - distancia)
importancia_temporal = clamp(impacto, 0..1) * recencia * uso

metadata_score =
    0.45 * similitud
  + 0.25 * impacto_original
  + 0.20 * importancia_temporal
  + 0.10 * recencia
```

Semántica usa vida media de 30 días y piso 0.35; episodios, 72 horas y piso 0.1.
El impacto original del término `0.25` y la similitud no se acotan arriba. Tipos
runtime tampoco se validan. El probe reprodujo `metadata_score > 1.0`.

`metadata_score` se inicia en `1.0` en `MemoryResult`, se muta durante reranking y
después es el score OBS-005. El orden final es mayor score primero; sort de Python es
estable para empates.

## 10. Filtros y decay

Los constructores de filtros son puros y no mutan sus argumentos. Los filtros por
tipo, impacto y valencia se pasan a Chroma. `por_valencia()` no valida el operador.

`MemoryFilters.recientes()` devuelve `{"_recencia_horas": horas}` y su docstring
dice que el retriever debe interpretarlo, pero no existe tal interpretación. Si se
pasa al store, llega a Chroma como campo real, puede fallar y el fallo termina como
`[]`.

Decay ocurre:

- en lectura, como parte del score;
- en selección de candidatos de consolidación;
- no en escritura;
- no elimina por sí mismo.

Fechas futuras reciben peso 1.0. Timestamp inválido al reconstruir se sustituye por
`datetime.utcnow()`, rejuveneciendo silenciosamente el recuerdo. Timestamp aware se
acepta/reconstruye pero luego falla al restarse de `utcnow()` naive. El probe mostró
que un solo resultado aware aborta el rerank completo y MemoryManager lo presenta
como recuperación vacía.

## 11. Consolidación

Disparadores:

- cada 5–10 escrituras observacionales, según `revision_consolidacion_cada`;
- explícitamente durante un evento de descanso.

No cuentan tipos críticos ni memorias que no parezcan observacionales. El contador
vive en RAM y no tiene lock. Un retry vuelve a contar/escribir; un interleaving
forzado reprodujo dos incrementos observados como uno.

Selección:

- lista todos los episodios;
- excluye críticos, impacto `>= 0.6`, recientes y relevancia mayor al umbral;
- ordena por importancia/fecha ascendente;
- toma exactamente `consolidacion_umbral` candidatos.

Regla resultante:

- conserva número de observaciones;
- tipo más frecuente y frecuencia;
- hasta tres muestras truncadas a 90 caracteres;
- hash del lote y tipo fuente en metadata;
- **no conserva IDs fuente**, estado emocional ni narrativa completa.

Orden de commit lógico:

```text
consultar candidatos
 → comprobar lote existente
 → guardar regla semántica
 → si y solo si retornó ID: eliminar episodios
```

Fallo de regla no borra. Fallo de delete deja regla y fuentes coexistiendo. Retry
puede encontrar el hash de lote y borrar las fuentes. No hay transacción común;
cada llamada Chroma está bloqueada por separado. Dos consolidaciones concurrentes
pueden observar ausencia y crear dos reglas antes de borrar los mismos episodios.

**No se demostró pérdida destructiva de fuentes en el orden actual.** El riesgo
confirmado es duplicación/representación dual y recibos imprecisos, no delete previo
a regla.

## 12. Eliminación

Con Chroma 1.5.9 temporal y embedding fake:

- inserción con metadata válida + delete funciona y reduce conteo;
- delete de ID inexistente retorna `True` y no cambia conteo;
- delete con lista vacía retorna `False`;
- delete en una colección no toca otra colección del mismo path;
- update de ID inexistente también retorna `True` sin cambiar filas.

El fallo histórico `memory.test_chromadb_flow.test_eliminacion` ocurre antes del
delete: usa `metadata={}`, la inserción es rechazada y el conteo nunca llega a 3.
Con precondición válida, el delete probado funciona. No se modificó el test.

`True` en delete/update significa “la llamada al backend no lanzó”, no “la fila
existía/cambió”.

## 13. WorkingMemory

- `deque(maxlen=20)` por default;
- guarda `accion`, estado inmediato (`despachada`, `ok`, etc.), timestamp y detalles;
- eviction FIFO automática;
- omite únicamente `none/sin_accion` desde `MemoryManager`;
- no actualiza el item cuando llega `resultado_accion`;
- no contiene `id_accion` canónico salvo que aparezca accidentalmente en detalles;
- no tiene lock explícito;
- devuelve snapshots de lista, pero los dicts internos siguen mutables;
- `MemoryManager.obtener_ultimas_acciones(n)` ignora `n`: el resumen usa 5 fijo.

Representa dispatch/resultado local inmediato, no lifecycle físico reconciliado.
Esto confirma el ticket heredado `EXEC-AUDIT-002`.

## 14. ConversationMemory

- `deque(maxlen=12)` por default;
- lock `RLock` para append/snapshot/lectura/clear;
- guarda texto usuario/Lazzaro, pensamiento, route, intent y metadata;
- limpia espacios y trunca texto;
- resumen público usa solo usuario/Lazzaro y señales derivadas;
- `snapshot_publico()` copia solo usuario/Lazzaro/timestamp;
- `obtener_ultimos()` sí expone el turno interno completo a callers Python;
- metadata sigue siendo dict por referencia: frozen superficial.

Los turnos ordinarios se registran solo para `comando_usuario` cuya salida final fue
`hablar`, excepto el control de save manual. RAM desaparece al reiniciar.

## 15. Save manual de conversación

El save manual fija un snapshot público de hasta 12 turnos, calcula SHA256 de JSON
canónico que incluye timestamps/textos y crea una regla semántica con:

- índice compacto;
- periodo y cantidad;
- transcripción pública completa;
- fingerprint;
- ID de transacción inicial y hashes de recibos;
- timestamps de inicio/fin.

Garantías observadas:

- mismo ID en el mismo proceso: `already_saved`;
- mismo fingerprint con ID nuevo: reutiliza el registro y asocia recibo;
- mismo ID tras reiniciar Manager: consulta durable y devuelve `already_saved`;
- fallo mantiene snapshot pending; retry no captura turnos nuevos;
- lookup fallido se distingue de snapshot vacío;
- si el write pareció fallar, hace lookup post-write para recuperar timeout ambiguo.

Límites:

- la idempotencia cross-process depende de check/update Chroma, sin transacción CAS;
- hashes de recibo se guardan en un string, máximo 128;
- el snapshot contiene contenido público potencialmente privado del usuario; es una
  persistencia explícita solicitada, no un almacén anónimo;
- pensamiento, route, intent y metadata interna del turno no aparecen en el
  documento manual. El probe lo verificó con canarios sintéticos.

El pending que Orchestrator reintenta significa: el evento original ya pasó por
Behavior/memoria; el retry con mismo ID ejecuta solo el save manual, conserva el
snapshot inicial y se cachea cuando deja de estar en `error`.

## 16. Hechos relacionales

Una conversación ordinaria puede persistir reglas sin save manual. Se extraen de
texto explícito del usuario:

- apodo de Lazzaro y revocación;
- nombre de Lazzaro;
- nombre del usuario y revocación;
- afirmación de autoridad/creador;
- preferencias y rechazos simples.

Dedupe = SHA1 truncado a 16 chars de categoría, valor, estado y regla normalizados,
primero en set RAM y luego mediante lookup Chroma. Si lookup falla, se interpreta
como ausente y se intenta escribir. Check y write no son atómicos.

Las contradicciones por la misma categoría se conservan como registros distintos.
En lectura se elige el timestamp más reciente por categoría; una revocación reciente
puede reemplazar al hecho activo anterior y aparece como texto de revocación. La
selección sucede después de que la búsqueda vectorial ya recortó a `limite * 3`, por
lo que “más reciente” solo significa más reciente dentro de los candidatos devueltos.

## 17. Aliases y afecto

Aliases:

- productor productivo actual escribe `approved=True` booleano;
- dedupe por `object_id + human_concept`, RAM y scan Chroma hasta 200;
- lectura con `approved="false"` lo acepta porque solo excluye `is False`;
- la lectura agrega aliases al set de dedupe: es READ WITH MUTATION;
- dos check-then-write concurrentes produjeron dos reglas UUID.

Afecto:

- el método de escritura normaliza/clampa y convierte `confirmed` con `bool()`;
- no se localizó caller productivo de escritura fuera de pruebas;
- el callback de lectura sí está conectado a Motor/Fuzzy Resolver;
- `confirmed="false"` se vuelve `True` al leer;
- la lectura llena el set de dedupe;
- el scan durable también está limitado a 200.

Así, `MEM-CONTRACT-002` afecta lectura legacy/externa. Los productores propios
localizados no escriben el string, pero el contrato de almacenamiento no lo impide.

## 18. Dedupe

| Ruta | Identidad | RAM | Durable | Atómico | Tras restart |
|---|---|---:|---:|---:|---|
| episodio genérico | ninguna; UUID nuevo | no | no | no aplica | idéntico evento duplica |
| regla genérica | ninguna; UUID nuevo | no | no | no aplica | regla idéntica duplica |
| alias | objeto+concepto | sí | scan max 200 | no | depende del scan |
| afecto | objeto/clase+afecto+fuente | sí | scan max 200 | no | depende del scan |
| relacional | SHA1 16 | sí | lookup exacto | no | lookup preserva dedupe si funciona |
| consolidación | SHA1 16 de IDs ordenados | no | lookup lote | no | retry puede reconocer lote |
| save manual | ID transacción + SHA256 snapshot | pending | metadata Chroma | lock solo intra-Manager | sí, si lookup funciona |

## 19. Concurrencia

- `ChromaClient`: `RLock` compartido por path; serializa cada add/query/get/update/
  delete/count/close en el proceso.
- `ConversationMemory`: lock propio alrededor de deque.
- save manual: lock propio cubre snapshot, lookups, write y recibo.
- `WorkingMemory`: sin lock explícito.
- sets de alias/afecto/relación: sin lock.
- contador y ciclo de consolidación: sin lock.
- no existe lock que abarque query-check-write o regla-delete.

Probes con barreras reprodujeron:

- dos episodios UUID para dos eventos idénticos concurrentes;
- dos reglas alias para el mismo alias;
- dos reglas consolidadas para el mismo lote;
- dos incrementos del contador colapsados a uno bajo interleaving forzado;
- bloqueo correcto de delete mientras add sostiene el lock Chroma compartido.

No se ejecutó stress infinito ni se tocó la DB activa.

## 20. Failure matrix

| Etapa | Error capturado | Retorno | Datos ya escritos | Rollback | Retry/duplicación | Visibilidad |
|---|---|---|---|---|---|---|
| encoder | por Manager | `None` | no | n/a | retry nuevo UUID | solo fallo genérico |
| episodio store False/error | sí | `None` | normalmente no/ambiguo backend | no | posible | colapsado |
| post-write consolidación | sí | `None` | **episodio sí** | no | duplica | invisible |
| regla semántica | sí | ID/`None` | según backend | no | UUID nuevo | ambiguo |
| query Chroma | sí | `None` | n/a | n/a | caller sigue | visible solo aquí |
| episodic/semantic store | sí/falsey | `[]` | n/a | n/a | parcial posible | no-hit/error iguales |
| retriever | por store; rerank propaga | lista/`[]` | n/a | n/a | dos queries | health invisible |
| summarizer | Manager captura | texto vacío | n/a | n/a | no requery | IDs pueden conservarse |
| decay | rerank puede propagar | recuperación vacía arriba | n/a | n/a | repetible | invisible arriba |
| consolidación regla falla | caller captura según ruta | sin delete | no regla confirmada | no | retry | logs |
| consolidación delete falla | adaptador `False` | regla queda | regla+fuentes | no | retry limpia | warning |
| delete unknown | no error | `True` | ninguna fila | n/a | parece éxito | rowcount invisible |
| WorkingMemory | Orchestrator no captura localmente | excepción/500 | side effects previos | no | retry repite | visible como 500 |
| ConversationMemory | Orchestrator captura | continúa | quizá RAM previa | no | siguiente turno | log |
| save manual lookup | sí | status `error` | no | pending conserva | retry especializado | explícito |
| save manual write ambiguo | sí + post-lookup | saved/already/error | quizá sí | no | pending | mejor que memoria general |
| relacional/alias | helpers capturan | ID/`None` | quizá sí | no | check race duplica | ambiguo |

## 21. Side-effect matrix

| Método público relevante | Clase | Efecto |
|---|---|---|
| `guardar_experiencia` | WRITE CHROMA + DERIVED WRITE | episodio; puede disparar consolidación/delete |
| `guardar_regla` | WRITE CHROMA | regla UUID |
| `buscar_recuerdos` | READ PURE funcional | dos queries; muta solo resultados efímeros al score |
| `obtener_contexto_memoria*` | READ PURE funcional | query+resumen; sin segunda query de auditoría |
| `obtener_contexto_relacional` | READ PURE funcional | query y selección |
| `agregar_accion` | WRITE RAM | append/eviction WorkingMemory |
| `obtener_ultimas_acciones` | READ PURE | resumen; ignora `n` |
| `obtener_cantidad_memoria_trabajo` | READ PURE | snapshot y len |
| `registrar_turno_conversacional` | WRITE RAM + DERIVED WRITE | turno y posibles hechos Chroma |
| `obtener_contexto_conversacional` | READ PURE | copia/resumen |
| `guardar_fotografia_conversacional` | DEDUPE/RECEIPT + WRITE CHROMA | snapshot/fingerprint/recibos |
| `guardar_alias_aprendido` | DEDUPE/RECEIPT + WRITE CHROMA | lookup y regla |
| `obtener_aliases_aprendidos` | READ WITH MUTATION | lista Chroma y llena set RAM |
| `guardar_memoria_afectiva_espacial` | DEDUPE + WRITE CHROMA | lookup y regla |
| `obtener_memorias_afectivas_espaciales` | READ WITH MUTATION | lista Chroma y llena set RAM |
| `consolidar_memorias_si_hace_falta` | DERIVED WRITE + DELETE CHROMA | regla agregada y episodios eliminados |
| `close` | LIFECYCLE MUTATION | cierra wrapper inyectado |

No se encontró método con nombre `obtener/buscar` que dispare consolidación/delete,
pero las lecturas de alias/afecto sí mutan sus caches RAM.

## 22. Hallazgos heredados

| Ticket heredado | Resultado Bloque 4 |
|---|---|
| `MEM-CONTRACT-001` P1 | confirmado en roundtrip Chroma; afecta todo episodio, retrieval y consolidación |
| `MEM-CONTRACT-002` P1 | confirmado en reads; writers propios localizados usan bool, afecto sin productor productivo |
| `MEM-CONTRACT-003` P1 | confirmado y ampliado a matriz de fallos parciales |
| `MEM-CONTRACT-004` P2 | confirmado: `None` aceptado, `{}` rechazado, válido aceptado; test histórico es fixture |
| `MEM-CONTRACT-005` P2 | confirmado: pérdidas no escalares y overwrite reservado |
| `MEM-CONTRACT-006` P2 | confirmado; aware puede envenenar lote y inválido se rejuvenece |
| `MEM-CONTRACT-007` P2 | confirmado; score mutable y >1 reproducido |
| `MEM-CONTRACT-008` P2 | confirmado en estado/extra/detalles/metadata RAM |
| `MEM-CONTRACT-009` P3 | confirmado; defaults de origen divergen |
| `CONC-AUDIT-004` P2 | acotado con races deterministas; origina `MEM-CONC-001` P1 |
| `EXEC-AUDIT-002` P1 | confirmado: WorkingMemory no reconcilia outcome |

## 23. Tickets nuevos

### `MEM-PERSIST-001` — P1 — recibo falso-negativo después de persistir episodio

- **Clase:** bug de receipt/idempotencia.
- **Evidencia:** store aceptó el episodio; el hook posterior de consolidación lanzó;
  `guardar_experiencia()` devolvió `None` aunque el episodio permaneció.
- **Ubicación:** `memory/memory_manager.py:129-160,1549-1565`.
- **Reproducción:** grupo `write_receipts_and_concurrent_duplicates`.
- **Impacto:** Core/auditoría creen que no se creó memoria; retry genera otro UUID y
  duplica experiencia/aprendizaje.
- **Recomendación preliminar:** separar receipt de escritura primaria del outcome de
  mantenimiento derivado; consolidación no debe invalidar un write ya confirmado.
- **Estado:** OPEN.

### `MEM-RETRIEVE-001` — P1 — un recuerdo temporalmente válido puede vaciar todo el lote

- **Clase:** fallo de aislamiento de registro/reranking.
- **Evidencia:** un timestamp aware reconstruido correctamente llegó a decay; la
  resta con UTC naive lanzó `TypeError`; MemoryManager devolvió contexto vacío y
  descartó también el recuerdo naive válido del mismo lote.
- **Ubicación:** `memory/episodic_memory.py:88-110`,
  `memory/memory_decay.py:25-49`, `memory/memory_retriever.py:73-104`,
  `memory/memory_manager.py:444-518`.
- **Reproducción:** grupo `scoring_filters_and_poison_record`.
- **Impacto:** un registro envenena recuperación completa y parece “no recuerdo”;
  DevelopmentalCore podría aprender una ausencia falsa.
- **Recomendación preliminar:** normalización UTC única y aislamiento explícito por
  registro con health/partial status antes de aprendizaje.
- **Estado:** OPEN.

### `MEM-CONC-001` — P1 — dedupe check-then-write duplica aprendizaje

- **Clase:** race de dedupe semántico.
- **Evidencia:** dos threads atravesaron cache+lookup antes de write y crearon dos
  reglas UUID para el mismo alias. Hechos/afecto comparten el patrón sin lock/CAS.
- **Ubicación:** `memory/memory_manager.py:202-249,294-360,1128-1181`.
- **Reproducción:** grupo `alias_affect_relational_side_effects_and_race`.
- **Impacto:** evidencia duplicada puede sesgar retrieval/scoring y consolidar una
  asociación varias veces; el scan límite 200 agrava el restart.
- **Recomendación preliminar:** identidad durable única/upsert o sección crítica
  check-write; definir receipt `created/already_exists/error`.
- **Estado:** OPEN.

### `MEM-CONSOLIDATE-001` — P2 — ciclo no atómico duplica reglas y representación

- **Clase:** atomicidad/lifecycle.
- **Evidencia:** fallo de delete dejó regla+episodios; dos consolidaciones que vieron
  lote ausente crearon dos reglas. El lock Chroma solo cubre llamadas individuales.
- **Ubicación:** `memory/memory_manager.py:1567-1620`,
  `memory/chroma_client.py:138-261`.
- **Reproducción:** grupo `consolidation_partial_failure_and_concurrency`.
- **Impacto:** doble representación, reglas duplicadas y conteo/recall sesgado. No se
  demostró pérdida destructiva de fuentes.
- **Recomendación preliminar:** journal/estado de lote o unicidad durable, ciclo
  idempotente con outcomes explícitos; preservar orden write-before-delete.
- **Estado:** OPEN.

### `MEM-RETRIEVE-002` — P2 — filtro `recientes` no tiene consumidor

- **Clase:** contrato filtro/backend.
- **Evidencia:** produce `_recencia_horas`; retriever no hace post-filter; al pasar al
  store se trata como metadata Chroma y el fallo se colapsa a `[]`.
- **Ubicación:** `memory/memory_filters.py:42-51`,
  `memory/memory_retriever.py:32-71`, stores.
- **Reproducción:** grupo `scoring_filters_and_poison_record`.
- **Impacto:** un caller que confía en el docstring puede recibir falsa ausencia.
- **Recomendación preliminar:** convertir a filtro soportado o post-filtrar de forma
  explícita; no enviar marcadores internos al backend.
- **Estado:** OPEN.

### `MEM-PERSIST-002` — P2 — booleanos CRUD no prueban efecto durable

- **Clase:** receipt ambiguo.
- **Evidencia:** delete/update de ID desconocido retornaron `True` sin cambiar filas;
  delete vacío retornó `False`.
- **Ubicación:** `memory/chroma_client.py:168-191,248-261`.
- **Reproducción:** Chroma temporal, grupo
  `real_roundtrip_delete_and_wrapper_isolation`.
- **Impacto:** consolidación puede afirmar limpieza de N; save manual puede creer
  asociado un recibo sin rowcount/lectura posterior.
- **Recomendación preliminar:** receipt con `matched/changed` o verificación dirigida
  cuando la semántica exige existencia.
- **Estado:** OPEN.

### `MEM-RAM-001` — P3 — `obtener_ultimas_acciones(n)` ignora `n`

- **Clase:** API/documentación.
- **Evidencia:** con `n=1` el resumen incluyó dos acciones; Manager no pasa `n` y
  `WorkingMemory.resumen()` fija 5.
- **Ubicación:** `memory/memory_manager.py:569-571`,
  `memory/working_memory.py:56-67`.
- **Reproducción:** grupo `ram_memories`.
- **Impacto:** callers no controlan el tamaño prometido; bajo riesgo actual.
- **Recomendación preliminar:** honrar/eliminar el parámetro al corregir contratos.
- **Estado:** OPEN.

## 24. Límites para Bloque 5

Este bloque no auditó calidad de LLM/Capa 0/Capa 1, providers/Oracle salvo contratos
directos, ni DevelopmentalCore. Para el siguiente bloque deben tratarse como datos de
entrada, no reabrirse:

- error/no-hit/partial de memoria no tiene health público;
- WorkingMemory no es ledger;
- `estado_emocional` no sobrevive;
- score y tiempo no tienen contrato seguro;
- dedupe y consolidación no son transacciones;
- save manual sí tiene un receipt más rico, pero no sustituye health general.

Un futuro DevelopmentalCore debe distinguir al menos:

- `retrieval_ok_empty` vs `retrieval_failed` vs `retrieval_partial`;
- ID/score/tipo y versión de representación;
- created vs already-existed vs write-ambiguous;
- episodio original vs regla derivada/consolidada;
- dispatch vs outcome físico;
- dato vigente/revocado;
- fuente validada vs legacy con flags mal tipados;
- timestamp válido/normalizado;
- mantenimiento fallido posterior a un write exitoso.

No se propone ampliar AuditTrace v1 en este bloque.

## 25. Respuestas a las 88 preguntas obligatorias

1. Episódica, semántica, WorkingMemory, ConversationMemory y caches/recibos RAM.
2. Episódica/semántica persisten; las demás son RAM, salvo derivados selectivos.
3. `MemoryManager` posee/coordina todas; recibe el Chroma creado en boot.
4. Orchestrator y helpers derivados del Manager.
5. Orchestrator, retriever, Persona context y callbacks Motor.
6. Evento que alcanza `_guardar_evento_en_memoria`.
7. Sí tras esa frontera; no cache hits/fallos previos.
8. Mapa §4.
9. Estado emocional y extras no escalares.
10. Timestamp inválido, defaults/tipos, campos colisionados y estado perdido.
11. Todo reservado escalar, incluido `tipo` y `origen`.
12. Dict/list/None/datetime y no escalares.
13. ID pretende éxito; `None` agrupa varias cosas y puede mentir post-write.
14. No los distingue de modo completo.
15. Solo rutas especializadas; no episodio/regla genéricos.
16. Tabla §18.
17. Algunos scans/fingerprints son durables; caches son RAM.
18. RAM desaparece; lookups pueden reconstruir dedupe; episodios genéricos duplican.
19. Sí, check-then-write.
20. Dos UUID/documentos.
21. Query válida vacía abajo; `[]` arriba.
22. `None` abajo; `[]` arriba.
23. Store.
24. Sí.
25. Sí.
26. Chroma/store/both fallan → `[]` total o parcial; no-hit → el mismo `[]`.
27. Dos por retrieval híbrido; más relacional aparte.
28. Sí, una recuperación híbrida, sin auditoría duplicada.
29. Cosine raw devuelta por Chroma; menor es mejor.
30. Fórmula §9.
31. Durante `_calcular_scores`.
32. `metadata_score` descendente.
33. Where Chroma y recorte final; `recientes` no funciona como promete.
34. No; el score sí muta `MemoryResult` efímero.
35. Lectura/scoring y candidatos de consolidación.
36. No.
37. Sí, se vuelve “ahora”.
38. Futuro = recencia 1; extremos viejos llegan al piso; aware puede fallar.
39. Reducir ranking o reemplazar episodios por regla consolidada.
40. No.
41. Solo `ChromaClient.eliminar`, hoy desde consolidación/tests.
42. Sí, después de guardar regla.
43. Contador observacional o descanso.
44. No.
45. Sí.
46. Observaciones viejas, no críticas, bajo impacto/importancia.
47. Conteo, tipo frecuente y tres muestras truncadas.
48. Agregados, hash lote y muestras; no IDs/estado completo.
49. Sí, por diseño lossy.
50. Sí: estado dual.
51. No se demostró; orden actual lo evita.
52. No.
53. §11/§20.
54. Sí.
55. No, solo cada CRUD.
56. Deque RAM de acciones.
57. Default 20, FIFO.
58. Dispatch/local inmediato, no outcome futuro.
59. No.
60. Sin lock explícito; orden intercalable.
61. Deque RAM locked de turnos.
62. Default 12; manual máximo 12.
63. Usuario, Lazzaro, pensamiento, route, intent, metadata, timestamp.
64. Persistencia automática de cada turno; resumen/manual excluyen privados.
65. §15.
66. Canonicaliza snapshot y deduplica por fingerprint.
67. Sí dentro del Manager.
68. Sí para transacción/registro durable si lookup funciona.
69. Post-lookup recupera write ambiguo; receipt update ambiguo queda limitado.
70. Snapshot inicial fijado y retry solo del save.
71. Sí, conversación pública que puede contener datos personales.
72. Sí para snapshot/documento manual; no en `obtener_ultimos()` RAM.
73. Apodos/nombres/autoridad/preferencias/revocaciones.
74. Sí.
75. SHA1 16 + set RAM + lookup.
76. Persisten; selección más reciente por categoría dentro del top-k.
77. Regla semántica con metadata y dedupe especializado.
78. Escritura propia bool; lectura permisiva incorrecta.
79. Registro legacy/externo con string false sí; productor propio fallido no debería.
80. Aliases y memoria afectiva.
81. Sí, llenan caches de dedupe.
82. No desde lectura; solo guardar experiencia/descanso.
83. `obtener_aliases*` y `obtener_memorias_afectivas*`.
84. CRUD degrada a False/None/-1; capas superiores continúan.
85. Sí: RAM/conversación siguen; persistencia/retrieval fallan abierto.
86. WorkingMemory, ConversationMemory, pending y caches.
87. Sí.
88. Estados enumerados en §24.

## 26. Pruebas, archivos y gate

Archivos creados:

- `docs/audit/block_04_memory.md`
- `docs/audit/probes/memory_deep_probe.py`

Probe profundo:

| Grupo | Evidencia principal |
|---|---|
| real roundtrip/delete | roundtrips, delete válido, no-ops unknown, aislamiento colección |
| retrieval/OBS-005 | matriz failure/no-hit/partial; una recuperación/alignment |
| scoring/filtros | ranking, >1, future, poison aware, `recientes` |
| writes | receipts y duplicado concurrente |
| consolidación | fallos parciales, retry, regla duplicada, contador |
| RAM | eviction, privacidad, locks, ausencia de reconciliación |
| save manual | idempotencia, restart, pending, exclusión de privados |
| aliases/afecto/relación | flags, side effects, race y regla ordinaria |
| lock Chroma | serialización de llamadas individuales |

Comandos de cierre:

```text
python docs/audit/probes/memory_deep_probe.py
python docs/audit/probes/regression_probe.py
python -m compileall -q core ai_engine memory tools api terminal_client docs/audit/probes
git diff --check
```

Los tres archivos runtime preexistentes bajo `lazzaro_data/` permanecen modificados
y fuera del commit.
No se hizo push, fix, migración, vacuum, compact ni delete sobre la Chroma activa.

Resultados de cierre:

| Check | Resultado |
|---|---|
| `memory_deep_probe.py` | PASS, 9/9 grupos, solo fixtures sintéticas |
| regresión protegida | 160 PASS, 1 KNOWN_XFAIL, 0 fallos nuevos |
| suite/isolation gate | PASS / PASS |
| `compileall` | PASS |
| `git diff --check` | PASS |
| AuditTrace v1 | sin cambios |

SHA-256 inicial = final:

| Archivo activo preexistente | SHA-256 |
|---|---|
| `data_level0.bin` | `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659` |
| `length.bin` | `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A` |
| `chroma.sqlite3` | `884E69923890C2470819E32939F60387C68FCF6C80300688A77364A7C8454363` |
