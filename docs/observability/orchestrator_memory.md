# Auditoría de memoria en Orchestrator — OBS-005

Estado: **implementado y verificado.**

OBS-005 registra metadata de la recuperación y escritura de memoria que ya
ejecuta el turno. No realiza una segunda búsqueda, no vuelve a consultar Chroma
y no incorpora el contenido de recuerdos a `AuditTrace`.

Implementación:

- [`memory/memory_manager.py`](../../memory/memory_manager.py)
- [`core/orchestrator.py`](../../core/orchestrator.py)

## Una sola recuperación

`MemoryManager.obtener_contexto_memoria_con_metadata()` ejecuta exactamente esta
secuencia:

```text
buscar_recuerdos()
    ↓
list[MemoryResult] ordenada por MemoryRetriever
    ├──→ MemorySummarizer.resumir() → texto funcional
    └──→ IDs + metadata_score       → metadata de auditoría
```

El resumidor y la metadata consumen la misma lista local de `MemoryResult`. No
se almacena un `last_results` compartido y no existe una consulta de auditoría.
La API histórica `obtener_contexto_memoria()` continúa devolviendo `str` y
delega en esa misma operación.

Los pares se construyen conjuntamente y después se separan en dos listas. Por
ello `retrieved_ids[i]` y `scores[i]` siempre describen el mismo resultado y
conservan el ranking recibido.

## Contrato v1 conservado

OBS-005 utiliza únicamente los cuatro campos que ya existían en `AuditTrace v1`:

| Campo | Fuente |
|---|---|
| `memory.retrieved_ids` | `resultado.recuerdo.id` |
| `memory.scores` | `resultado.metadata_score` |
| `memory.created_ids` | retorno existente de `guardar_experiencia()` |
| `memory.working_memory_count` | tamaño del snapshot de `WorkingMemory.obtener_todas()` |

`scores` no contiene la distancia cruda de Chroma. No se agregó silenciosamente
un campo `types` ni se cambió el significado del contrato v1.

El conteo de working memory se toma antes de resolver y agregar la acción del
turno actual. Representa la cola que estaba disponible para informar esa
decisión.

## Privacidad

El registro `stage=memory` no recibe:

- narrativas episódicas;
- reglas semánticas;
- texto del contexto resumido;
- continuidad conversacional;
- prompts ni contenido de working memory.

`MemoryContextResult.texto` sigue alimentando el flujo funcional, pero solo sus
IDs y scores pasan al snapshot de auditoría.

## Semántica de presencia

- Si la recuperación devuelve cero resultados, `retrieved_ids=[]` y `scores=[]`.
- Si `guardar_experiencia()` devuelve `None`, `created_ids=[]`.
- Si el turno alcanza la fase estándar de memoria, el stage se emite aunque una
  de esas listas esté vacía.
- Un cache hit, un retry resuelto antes de Triage o un fallo previo de
  `BehaviorCore` no fabrican `stage=memory`.

La escritura capturada es exclusivamente la escritura episódica ya realizada
por `_guardar_evento_en_memoria`. Las búsquedas relacionales, alias aprendidos y
fotografías conversacionales usan contratos distintos y permanecen fuera de
OBS-005.

## Tolerancia a fallos

Un fallo al extraer metadata conserva el texto funcional y deja las listas de
auditoría vacías y alineadas. Un fallo del writer genera un warning en el logger
humano y no altera la recuperación, escritura, ruta ni acción del turno.

## Verificación

El runner [`core/test_orchestrator_audit.py`](../../core/test_orchestrator_audit.py)
comprueba:

1. una sola llamada a `buscar_recuerdos()`;
2. identidad de la lista usada por el resumidor y la extracción de metadata;
3. orden alineado de IDs y scores;
4. uso explícito de `metadata_score`, no `distancia`;
5. captura del ID ya devuelto por `guardar_experiencia()`;
6. `None → created_ids=[]`;
7. conteo de una `WorkingMemory` real antes de agregar la acción actual;
8. cuatro claves exactas de v1, sin `types`;
9. ausencia de narrativa, regla y resumen en el JSON;
10. el mismo `trace_id` en `event_start`, `triage`, `memory` y `event_end`;
11. cache y fallo previo de `BehaviorCore` sin `memory`;
12. fallo del writer sin modificar la decisión funcional.

Resultado de OBS-005: **PASS**.

- Pruebas específicas OBS-003–OBS-005: 13 PASS.
- Regresión directa del repositorio: 134 PASS, 0 fallos nuevos y 1 fallo conocido
  del baseline (`memory.test_chromadb_flow.test_eliminacion`).
- Compatibilidad directa de memoria conversacional, guardado manual y terminal:
  PASS.
- Compilación de los módulos del runtime: PASS.

La integración posterior de capa, acción final y ejecución está documentada en
[`orchestrator_decision_execution.md`](orchestrator_decision_execution.md). El
registro posterior de errores seguros, fallbacks y duración está documentado en
[`orchestrator_errors_durations.md`](orchestrator_errors_durations.md). El
siguiente ticket es OBS-008, sidequest de no regresión.
