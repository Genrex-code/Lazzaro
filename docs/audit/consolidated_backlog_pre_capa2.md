# Backlog consolidado pre-Capa 2

- Fecha de consolidación: 2026-09-05
- Rama: `codex/stabilization/pre-capa2`
- Commit documental de entrada: `f6ef27c5d9256431522d23b91b79945c4e17e066` (`docs(audit): close block 9 frontier audit`)
- Alcance: baseline/Fase 0, AuditTrace/Fase 1 y auditorías cerradas de los bloques 01–09.
Semántica: `PASS_REPRODUCED` confirma que el comportamiento observado pudo reproducirse; no lo aprueba como seguro ni correcto.

### Estado de remediación posterior

El inventario y los totales de este documento siguen siendo la fotografía
histórica del cierre de auditoría; no se reescriben al implementar cada ROOT.
A 2026-09-16, `ROOT-018` queda `IMPLEMENTED / HUMAN_REVIEW_PENDING`: provenance
de productor y retrieval versionada, identidad Q1/Q2 separada, cache health
tipado y política de escritura sin laundering. `ORACLE-CACHE-001` y
`LLM-AUDIT-001` quedan cubiertos por código y pruebas. ROOT-017 (factualidad) y
ROOT-053 (single-flight) permanecen abiertos y no fueron iniciados.

## 1. Resumen ejecutivo

La auditoría produjo **105 tickets administrativos OPEN**: 2 P0, 40 P1, 56 P2 y 7 P3. La deduplicación por corrección arquitectónica o contractual común los reduce a **55 causas raíz**: 2 P0, 27 P1, 22 P2 y 4 P3.

La reducción no borra tickets: 73 tickets se agrupan en 23 ROOT de múltiples manifestaciones y 32 quedan en relación uno-a-uno con una ROOT. Por tanto, se eliminan **50 conteos administrativos redundantes** (`105 - 55`), no 50 hallazgos históricos.

Las primeras correcciones de Fase 3 deben ser:

1. `ROOT-002`, contrato numérico físico finito/acotado, porque corta valores inválidos antes del wire/consumer.
2. `ROOT-001`, resolución de targets fail-closed, porque hoy una aclaración/corrección/rechazo puede terminar materializando un objetivo sustituido o inseguro.
3. `ROOT-003`, identidad y ledger canónicos de acción/outcome, como prerequisito del lifecycle, correlación y aprendizaje.

No hace falta anteponer un P1 contractual a los dos P0: ambos pueden cerrarse con validación local fail-closed y pruebas de no-dispatch. Sí debe cerrarse `ROOT-003` antes de confiar en outcomes, memoria de resultados o DevelopmentalCore.

AuditTrace v1 permanece congelado: OBS-001–OBS-008 están implementados y verificados. Esta fase no amplía su esquema, no toca producción, no ejecuta Unity/LLM/red/físico y no diseña DevelopmentalCore.

## 2. Inventario global de tickets OPEN

Leyenda de origen: `P` propio del bloque donde nació; `H` heredado/reconfirmado; `A` ampliado por evidencia posterior. `Vigente` se evalúa al cierre del Bloque 9. La evidencia primaria es el informe indicado; el ROOT de la columna relación es el resultado de esta consolidación. La recomendación histórica se resume sin sustituir el texto original.

| Ticket | Bloque | P original | Clase / owner | Causa y contrato afectado | Evidencia | Relación | Origen | Vigente | Recomendación histórica |
|---|---:|---:|---|---|---|---|:---:|:---:|---|
| BOOT-AUDIT-001 | 01 | P1 | lifecycle / `main` | cleanup de lifespan no garantizado | B01 § ticket | ROOT-022 | P | sí | cierre total e idempotente |
| BOOT-AUDIT-002 | 01 | P1 | ownership / `main` | ownership parcial de recursos | B01 § ticket | ROOT-022 | P | sí | owner único y rollback |
| BOOT-AUDIT-003 | 01 | P1 | storage / Chroma | handle nativo descartado si falla colección | B01 § ticket | ROOT-022 | P | sí | conservar/cerrar cada recurso |
| BOOT-AUDIT-004 | 01 | P2 | lifecycle / LLM factory | construcción parcial sin rollback | B01 § ticket | ROOT-022 | P | sí | rollback por etapas |
| BOOT-AUDIT-005 | 01 | P2 | cache / Chroma | cache global cruza paths y lifecycles | B01 § ticket | ROOT-030 | P | sí | identidad/close de cache |
| BOOT-AUDIT-006 | 01 | P2 | readiness / API | modo degradado se anuncia nominal | B01 § ticket | ROOT-023 | P | sí | health explícito |
| BOOT-AUDIT-007 | 01 | P2 | lifecycle / FastAPI | referencias residuales en `app.state` | B01 § ticket | ROOT-022 | P | sí | limpiar referencias |
| BOOT-AUDIT-008 | 01 | P2 | logging / `core.logger` | error de logfile puede abortar import | B01 § ticket | ROOT-031 | P | sí | logging fail-soft |
| CONFIG-AUDIT-001 | 01 | P1 | timeout / Gemini | llamada sin deadline finito | B01 § ticket | ROOT-021 | P | sí | presupuesto finito |
| CONFIG-AUDIT-002 | 01 | P2 | paths / config | env y DB dependen del CWD | B01 § ticket | ROOT-032 | P | sí | paths canónicos |
| CONFIG-AUDIT-003 | 01 | P3 | policy / providers | selección intencional no registrada | B01 § ticket | ROOT-033 | P | sí | documentar policy |
| CONFIG-AUDIT-004 | 01 | P2 | validation / Settings | valores inválidos sobreviven carga | B01 § ticket | ROOT-034 | P | sí | validar al construir |
| CONFIG-AUDIT-005 | 01 | P2 | config / runtime | defaults duplicados divergen | B01 § ticket | ROOT-032 | P | sí | una fuente canónica |
| CONFIG-AUDIT-006 | 01 | P2 | privacy / logger | logs humanos sin redacción de frontera | B01 § ticket | ROOT-010 | P | sí | sanitizar error externo |
| CONFIG-AUDIT-007 | 01 | P2 | readiness / factory | candidatos descritos como backend configurado | B01 § ticket | ROOT-023 | P | sí | estado efectivo |
| CONFIG-AUDIT-008 | 01 | P3 | legacy / Gemini | key `None` causa `AttributeError` directo | B01 § ticket | ROOT-035 | P | sí | retirar/validar ruta legacy |
| CONFIG-AUDIT-009 | 01 | P2 | config / SDK | SDK lee configuración ambiental fuera de Settings | B01 § ticket | ROOT-032 | P | sí | inyectar configuración |
| DEPS-AUDIT-001 | 01 | P2 | packaging / repo | manifest sin pins ni contrato Python | B01 § ticket | ROOT-036 | P | sí | soporte reproducible |
| DEPS-AUDIT-002 | 01 | P2 | packaging / repo | dependencias hardware/desktop no declaradas | B01 § ticket | ROOT-036 | P | sí | extras explícitos |
| API-CONTRACT-001 | 02 | P1 | privacy / API | pensamiento privado expuesto a REST/Unity | B02 § ticket | ROOT-009 | P | sí | DTO público separado |
| API-CONTRACT-002 | 02 | P1 | error / API | mensaje interno de excepción se hace público | B02 § ticket | ROOT-010 | P | sí | error estable y redacted |
| API-CONTRACT-003 | 02 | P2 | identity / API | UUID descrito pero se acepta ID opaco | B02 § ticket | ROOT-003 | P | sí | identidad canónica |
| API-CONTRACT-004 | 02 | P2 | validation / API | whitespace, extras y vacíos se normalizan distinto | B02 § ticket | ROOT-037 | P | sí | normalización única |
| DTO-CONTRACT-001 | 02 | P2 | error / API-Unity | envelope de error no coincide con transporte Unity | B02 § ticket | ROOT-010 | P | sí | envelope público versionado |
| DTO-CONTRACT-002 | 02 | P2 | schema / Python-C# | acción duplicada e informal entre lenguajes | B02 § ticket | ROOT-038 | P | sí | contrato generado/versionado |
| DTO-CONTRACT-003 | 02 | P2 | correlation / action | campos temporales/correlación inertes | B02 § ticket | ROOT-003; relacionado ROOT-029 | P | sí | cadena de identidad viva |
| DTO-CONTRACT-004 | 02 | P2 | lifecycle / Unity | `pending` se acepta como éxito | B02 § ticket | ROOT-004 | P | sí | estados disjuntos |
| AI-CONTRACT-001 | 02 | P2 | parser / AI | fallback del parser puede lanzar | B02 § ticket | ROOT-020 | P | sí | resultado tipado total |
| AI-CONTRACT-002 | 02 | P2 | fallback / Persona | flags de fallback se pierden en `ActionResponse` | B02 § ticket | ROOT-008 | P | sí | propagar provenance |
| MEM-CONTRACT-001 | 02 | P1 | schema / Memory | estado emocional se pierde al persistir/leer | B02 § ticket | ROOT-013 | P | sí | round-trip fiel |
| MEM-CONTRACT-002 | 02 | P1 | approval / Memory | sentinels bool/string ambiguos | B02 § ticket | ROOT-006 | P | sí | booleano estricto |
| MEM-CONTRACT-003 | 02 | P1 | health / Memory | no-hit, fail y partial indistinguibles | B02 § ticket | ROOT-014 | P | sí | receipt tipado |
| MEM-CONTRACT-004 | 02 | P2 | metadata / Memory | `None` y `{}` no comparten contrato | B02 § ticket | ROOT-039 | P | sí | normalizar semántica |
| MEM-CONTRACT-005 | 02 | P2 | schema / Memory | pérdida de metadata/colisión reservada | B02 § ticket | ROOT-013 | P | sí | namespace estable |
| MEM-CONTRACT-006 | 02 | P2 | time / Memory | timestamps inválidos y aware/naive incompatibles | B02 § ticket | ROOT-014 | P | sí | tiempo UTC validado |
| MEM-CONTRACT-007 | 02 | P2 | score / Memory | score mutable y sin cota | B02 § ticket | ROOT-040 | P | sí | dominio documentado |
| MEM-CONTRACT-008 | 02 | P2 | ownership / Memory | inmutabilidad solo superficial | B02 § ticket | ROOT-041 | P | sí | snapshot defensivo |
| MEM-CONTRACT-009 | 02 | P3 | schema / Memory | defaults de origen divergen | B02 § ticket | ROOT-013 | P | sí | origen canónico |
| TOOL-CONTRACT-001 | 02 | P1 | result / Tools | `exito="false"` es truthy y se vuelve ok | B02 § ticket | ROOT-006 | P | sí | bool estricto |
| TOOL-CONTRACT-002 | 02 | P1 | approval / Tools | confirmación mezcla requisito y autorización | B02 § ticket | ROOT-004 | P | sí | estados separados |
| TOOL-CONTRACT-003 | 02 | P2 | result / Tools | `False/None/{}/[]` crudos se promueven a éxito | B02 § ticket | ROOT-006 | P | sí | adaptador explícito |
| TOOL-CONTRACT-004 | 02 | P1 | result / Tools | `exito` inconsistente entre handlers | B02 § ticket | ROOT-006 | P | sí | un `ToolResult` |
| MOTOR-CONTRACT-001 | 02 | P1 | decision / Motor | estado válido/error interno se pierde públicamente | B02 § ticket | ROOT-008 | P | sí | decision receipt tipado |
| MOTOR-CONTRACT-002 | 02/06 | P1→P0 | safety / Motor-wire | no-finitos/coerción llegan como `null` | B02+B06 probes | ROOT-002 | A | sí | rechazar antes de dispatch |
| MOTOR-CONTRACT-003 | 02 | P2 | schema / Motor | `MotorAction` permite combinaciones inválidas | B02 § ticket | ROOT-042 | P | sí | invariantes cross-field |
| MOTOR-CONTRACT-004 | 02 | P2 | schema / Motor | Literal/dataclass no se fuerzan en runtime | B02 § ticket | ROOT-042 | P | sí | validación runtime |
| MOTOR-CONTRACT-005 | 02 | P2 | perception / Motor | distancia cero se pierde | B02 § ticket | ROOT-029 | P | sí | snapshot fiel |
| MOTOR-CONTRACT-006 | 02 | P3 | perception / Motor | identidad de snapshot inestable | B02 § ticket | ROOT-029 | P | sí | generation/ID |
| MOTOR-CONTRACT-007 | 02 | P2 | perception / Motor | ownership/rangos canónicos indefinidos | B02 § ticket | ROOT-029 | P | sí | contrato de percepción |
| CORE-AUDIT-001 | 03 | P1 | task / Core | todo comando se vuelve objetivo | B03 § ticket | ROOT-011 | P | sí | creación explícita |
| CORE-AUDIT-002 | 03 | P3 | ownership / Core | instancias globales legacy compiten con owner | B03 § ticket | ROOT-016 | P | sí | retirar globals |
| ORCH-AUDIT-001 | 03 | P1 | idempotence / Orchestrator | side effects antes de cache se duplican en retry | B03 § ticket | ROOT-003; relacionado ROOT-007 | P | sí | UoW + ledger |
| ORCH-AUDIT-002 | 03 | P1 | idempotence / Orchestrator | idempotencia RAM, acotada al proceso | B03 § ticket | ROOT-003 | P | sí | recibo durable |
| ORCH-AUDIT-003 | 03 | P1 | failure / Orchestrator | fallo parcial de Behavior aparece API ok/null | B03 § ticket | ROOT-007; relacionado ROOT-010 | P | sí | resultado transaccional |
| ORCH-AUDIT-004 | 03 | P1 | failure / Orchestrator | frontera Motor asimétrica repite efectos previos | B03 § ticket | ROOT-007; relacionado ROOT-010 | P | sí | rollback/commit explícito |
| ORCH-AUDIT-005 | 03 | P2 | cache / Orchestrator | cache devuelve el mismo objeto mutable | B03 § ticket | ROOT-043 | P | sí | copia/snapshot |
| TASK-AUDIT-001 | 03 | P1 | correlation / Task | outcome tardío se atribuye a tarea actual | B03 § ticket | ROOT-003 | P | sí | lookup por action ID |
| TASK-AUDIT-002 | 03 | P2 | lifecycle / Task | reset conserva umbral de abandono | B03 § ticket | ROOT-011 | P | sí | reset completo |
| TASK-AUDIT-003 | 03 | P2 | lifecycle / Task | narrativa “abandono” no cambia estado | B03 § ticket | ROOT-011 | P | sí | transición real |
| STATE-AUDIT-001 | 03 | P2 | lifecycle / State | un outcome dispara eventos derivados recursivos | B03 § ticket | ROOT-004 | P | sí | transición acotada |
| STATE-AUDIT-002 | 03 | P2 | semantics / State | “éxito” biológico significa decisión válida | B03 § ticket | ROOT-004 | P | sí | outcome material |
| CONC-AUDIT-001 | 03 | P1 | concurrency / Core | Behavior compartido sin lock transaccional | B03 § ticket | ROOT-012 | P | sí | ownership/serialización |
| CONC-AUDIT-002 | 03 | P1 | concurrency / cache | operaciones compuestas de cache compiten | B03 § ticket | ROOT-012 | P | sí | sección atómica |
| CONC-AUDIT-003 | 03 | P2 | concurrency / notifications | snapshot/clear pierde appends | B03 § ticket | ROOT-012 | P | sí | drain atómico |
| CONC-AUDIT-004 | 03 | P2 | concurrency / Core-Memory | colecciones parcialmente thread-safe | B03 § ticket | ROOT-012 | P | sí | política consistente |
| EXEC-AUDIT-001 | 03 | P1 | correlation / execution | alias permisivo correlaciona resultado | B03 § ticket | ROOT-003 | P | sí | identidad exacta |
| EXEC-AUDIT-002 | 03 | P1 | ledger / execution | no existe reconciliación/ledger de acción | B03 § ticket | ROOT-003; relacionado ROOT-004 | P | sí | ledger canónico |
| EXEC-AUDIT-003 | 03 | P1 | UoW / execution | acción inválida deja efectos previos | B03 § ticket | ROOT-007 | P | sí | validate-before-effects |
| MEM-PERSIST-001 | 04 | P1 | receipt / Memory | false negative tras episodio persistido | B04 § ticket | ROOT-015 | P | sí | receipt idempotente |
| MEM-RETRIEVE-001 | 04 | P1 | isolation / Memory | un timestamp aware envenena el lote | B04 § ticket | ROOT-014 | P | sí | aislar registro malo |
| MEM-CONC-001 | 04 | P1 | concurrency / Memory | dedupe check-write crea duplicados | B04 § ticket | ROOT-015 | P | sí | escritura atómica |
| MEM-CONSOLIDATE-001 | 04 | P2 | atomicity / Memory | regla+delete no atómicos dejan doble representación | B04 § ticket | ROOT-015 | P | sí | transacción/receipt |
| MEM-RETRIEVE-002 | 04 | P2 | dead contract / Memory | filtro “recent” no tiene consumidor | B04 § ticket | ROOT-044 | P | sí | conectar o retirar |
| MEM-PERSIST-002 | 04 | P2 | durability / Memory | bool CRUD no prueba efecto durable | B04 § ticket | ROOT-015 | P | sí | receipt verificable |
| MEM-RAM-001 | 04 | P3 | API / WorkingMemory | `obtener_ultimas_acciones(n)` ignora `n` | B04 § ticket | ROOT-045 | P | sí | respetar argumento |
| TRIAGE-AUDIT-001 | 05 | P1 | routing / Triage | límites léxicos desvían comandos/discusión | B05 § ticket | ROOT-024 | P | sí | reglas evaluadas |
| TRIAGE-CONTRACT-001 | 05 | P1 | semantics / Triage | UNKNOWN mezcla clase, fallo e incertidumbre | B05 § ticket | ROOT-025 | P | sí | outcomes disjuntos |
| TRIAGE-AUDIT-002 | 05 | P1 | failure / Triage | excepción cae por canal a Reasoning/Persona | B05 § ticket | ROOT-026 | P | sí | policy fail-safe |
| TRIAGE-DATA-001 | 05 | P2 | evaluation / Triage | sin split/histórico; fallback degrada | B05 § ticket | ROOT-046 | P | sí | corpus y métricas |
| TRIAGE-CONTRACT-002 | 05 | P2 | confidence / Triage | confidence mezcla escalas y no-finitos | B05 § ticket | ROOT-047 | P | sí | dominio finito |
| TRIAGE-MODEL-001 | 05 | P2 | deployment / Triage | artifact local no reproducible/versionado | B05 § ticket | ROOT-046 | P | sí | manifest de modelo |
| TRIAGE-DATA-002 | 05 | P2 | taxonomy / Triage | single-label pierde multi-intent | B05 § ticket | ROOT-048 | P | sí | política multi-intent |
| TRIAGE-MODEL-002 | 05 | P3 | legacy / Triage | clasificador/mapping de ruta sin uso | B05 § ticket | ROOT-049 | P | sí | retirar/documentar |
| MOTOR-SAFETY-001 | 06 | P0 | safety / Motor resolution | clarification/correction/unsafe puede despachar target sustituido | B06 probes | ROOT-001 | P | sí | fail-closed antes de Motor |
| AI-AUDIT-001 | 06 | P1 | safety score / Reasoning | penalty máximo 20, umbral unsafe 50 inalcanzable | B06 probes | ROOT-027 | P | sí | recalibrar contrato |
| AI-AUDIT-002 | 06 | P1 | decision / Reasoning | fuzzy omite `interaction_type` | B06 probes | ROOT-028 | P | sí | transportar intención |
| GROUND-AUDIT-001 | 06 | P2 | ranking / Grounding | score/orden ignora descriptor distintivo | B06 probes | ROOT-050 | P | sí | incluir atributo |
| GROUND-AUDIT-002 | 06 | P2 | UX / Grounding | aclaración de nombres duplicados no distingue | B06 probes | ROOT-051 | P | sí | labels únicos |
| GROUND-AUDIT-003 | 06 | P2 | reachability / Desktop | threshold 92 supera máximo 91 | B06 probes | ROOT-052 | P | sí | alinear dominio |
| ORACLE-AUDIT-001 | 07 | P1 | factuality / Persona | Persona reescribe o pierde facts de Oracle | B07 probe | ROOT-017 | P | sí | renderer grounded |
| ORACLE-CACHE-001 | 07 | P1 | provenance / Oracle | cache sin identidad/provenance/version | B07 probe | ROOT-018 | P | sí | key/receipt versionado |
| LLM-AUDIT-001 | 07 | P1 | provenance / LLM | causa/proveedor de Oracle se comprime bajo rol | B07 probe | ROOT-018 | P | sí | provenance end-to-end |
| ORACLE-CACHE-002 | 07 | P2 | concurrency / Oracle | misses concurrentes duplican llamada/escritura | B07 probe | ROOT-053 | P | sí | single-flight |
| LLM-PROVIDER-001 | 07 | P2 | retry / Gemini | retry indiscriminado | B07 probe | ROOT-021 | P | sí | clasificación retryable |
| LLM-CONTRACT-001 | 07 | P2 | parser / Oracle | estructura degradada se acepta como conocimiento | B07 probe | ROOT-020 | P | sí | schema estricto |
| IDENTITY-AUDIT-001 | 08 | P1 | ownership / Identity | self model fragmentado y sin owner estructurado | B08 probe | ROOT-016 | P | sí | owner y snapshot canónicos |
| PERSONA-AUDIT-001 | 08 | P1 | factuality / Persona | narrativa no se valida contra state/memory/self | B08 probe | ROOT-017 | P | sí | render-only + assertions |
| C2-CONTRACT-001 | 08 | P1 | prompt security / Capa 2 | memoria/percepción suben a autoridad system sin frontera | B08 probe | ROOT-019 | P | sí | separar data/instructions |
| PERSONALITY-AUDIT-001 | 08 | P2 | identity / Persona | traits RAM no forman voz de Persona | B08 probe | ROOT-016 | P | sí | proyección explícita |
| PERSONA-AUDIT-002 | 08 | P2 | repair / Persona | retry pierde grounding de identity/state/memory/perception/Oracle | B08 probe | ROOT-008 | P | sí | conservar contexto/provenance |
| C2-CONTRACT-002 | 08 | P2 | parser / Capa 2 | parser tolerante promueve parcial a habla válida | B08 probe | ROOT-020 | P | sí | resultado degradado explícito |
| UNITY-BOUNDARY-001 | 09 | P1 | materialization / Unity | cuatro handlers dicen success sin efecto material | B09 65/65 | ROOT-005 | P | sí | receipt del consumer |
| UNITY-BOUNDARY-002 | 09 | P1 | lifecycle / Unity | `accion=null` no termina subtarea local | B09 65/65 | ROOT-003; relacionado ROOT-004 | P | sí | outcome terminal correlacionado |
| TRANSPORT-AUDIT-001 | 09 | P2 | retry / Unity | evento se elimina al fallar transporte, sin retry | B09 65/65 | ROOT-054 | P | sí | cola/ack idempotente |
| API-BOUNDARY-001 | 09 | P2 | error / API | excepción de notificaciones se convierte en HTTP 200 `[]` | B09 65/65 | ROOT-055 | P | sí | diferenciar fallo/vacío |

### Comprobación del inventario

| Fuente de nacimiento | OPEN únicos | P0 | P1 | P2 | P3 |
|---|---:|---:|---:|---:|---:|
| Bloque 01 | 19 | 0 | 4 | 13 | 2 |
| Bloque 02 | 30 | 1 | 9 | 18 | 2 |
| Bloque 03 | 19 | 0 | 11 | 7 | 1 |
| Bloque 04 | 7 | 0 | 3 | 3 | 1 |
| Bloque 05 | 8 | 0 | 3 | 4 | 1 |
| Bloque 06, IDs nuevos | 6 | 1 | 2 | 3 | 0 |
| Bloque 07 | 6 | 0 | 3 | 3 | 0 |
| Bloque 08 | 6 | 0 | 3 | 3 | 0 |
| Bloque 09 | 4 | 0 | 2 | 2 | 0 |
| **Total reconciliado** | **105** | **2** | **40** | **56** | **7** |

`MOTOR-CONTRACT-002` nació P1 en Bloque 02 y la evidencia de Bloque 06 lo escaló a P0; se cuenta una sola vez y en la prioridad vigente. Los siete hallazgos discutidos en Bloque 06 incluyen ese heredado, pero solo seis IDs nuevos, evitando doble conteo.

## 3. Documentation reconciliation

| ID / asunto | Versión antigua | Evidencia más reciente | Decisión consolidada |
|---|---|---|---|
| `BASE-FIND-001` | P0 por secretos expuestos | Fase 0 documenta retiro/rotación; usuario confirmó revocación de proveedores obsoletos | `REMEDIATED`; no OPEN, no ROOT |
| `MEM-AUDIT-001` | P2 provisional sobre `None`/`{}` | Bloque 02 lo precisó como `MEM-CONTRACT-004` | candidato retirado; solo cuenta `MEM-CONTRACT-004` |
| `TEST-AUDIT-001` | P2 provisional por runner | `docs/audit/probes/regression_probe.py` establece gate reproducible | resuelto como infraestructura de auditoría; no OPEN |
| `LLM-AUDIT-001` | ID histórico retirado: modelo Groq fallback no utilizable | Bloque 07 reutiliza el ID para pérdida de provenance/causa de Oracle | colisión documental. La ROOT absorbe solo el significado OPEN de Bloque 07; el anterior queda histórico y retirado |
| `MOTOR-CONTRACT-002` | P1 contractual en Bloque 02 | Bloque 06 demuestra no-finitos/coerción capaces de cruzar hacia serialización/dispatch | prioridad vigente P0; una sola instancia |
| OBS-001–OBS-008 | sidequest en progreso en documentos intermedios | documentos de aceptación y no-regresión: PASS | cerrados; no generan tickets OPEN |
| Hash Bloque 9 | prompt conservaba placeholder | Git contiene `f6ef27c5d9256431522d23b91b79945c4e17e066` | hash documental de entrada de esta consolidación |

## 4. Mapa de duplicados administrativos

La absorción se exige solo cuando una misma corrección esencial resuelve todas las manifestaciones. Los grupos más importantes son:

| ROOT | Tickets absorbidos | Justificación de una causa común |
|---|---|---|
| ROOT-003 | API-CONTRACT-003, DTO-CONTRACT-003, ORCH-AUDIT-001/002, TASK-AUDIT-001, EXEC-AUDIT-001/002, UNITY-BOUNDARY-002 | un ID/lifecycle/ledger durable debe correlacionar request, action y outcome; no son cuatro fixes independientes |
| ROOT-006 | MEM-CONTRACT-002, TOOL-CONTRACT-001/003/004 | falta un contrato boolean/result estricto en adaptadores de efectos |
| ROOT-007 | ORCH-AUDIT-003/004, EXEC-AUDIT-003 | falta unidad de trabajo: validar/decidir antes de comprometer side effects y reportar fallo parcial |
| ROOT-010 | CONFIG-AUDIT-006, API-CONTRACT-002, DTO-CONTRACT-001 | una frontera pública de error estable y redacted resuelve las tres fugas/inconsistencias |
| ROOT-012 | CONC-AUDIT-001/002/003/004 | falta una política de ownership y atomicidad para estado compartido del turno |
| ROOT-014 | MEM-CONTRACT-003/006, MEM-RETRIEVE-001 | retrieval carece de receipt de salud y aislamiento temporal por elemento |
| ROOT-015 | MEM-PERSIST-001/002, MEM-CONC-001, MEM-CONSOLIDATE-001 | persistencia necesita operación idempotente/atómica con receipt durable |
| ROOT-016 | CORE-AUDIT-002, IDENTITY-AUDIT-001, PERSONALITY-AUDIT-001 | identidad/traits carecen de owner y snapshot canónicos; no incluye factualidad del renderer |
| ROOT-018 | ORACLE-CACHE-001, LLM-AUDIT-001 | knowledge/cache pierden la misma provenance/version desde obtención hasta consumo |
| ROOT-020 | AI-CONTRACT-001, LLM-CONTRACT-001, C2-CONTRACT-002 | parsers tolerantes no representan degradación como estado tipado |
| ROOT-021 | CONFIG-AUDIT-001, LLM-PROVIDER-001 | un único presupuesto de llamada clasificado por retry gobierna timeout y reintento |
| ROOT-022 | BOOT-AUDIT-001/002/003/004/007 | adquisición parcial no está gobernada por ownership/rollback/close simétricos |
| ROOT-029 | MOTOR-CONTRACT-005/006/007 | percepción carece de snapshot con identidad, fidelidad y freshness canónicas |

No se sobre-consolidaron: `ROOT-001` (elección fail-closed), `ROOT-027` (escala de riesgo) y `ROOT-028` (tipo de interacción) son tres fixes Motor distintos; los tres se relacionan, pero ninguno corrige automáticamente a los demás. Triage también conserva tres P1 separados (`ROOT-024` reglas, `ROOT-025` semántica UNKNOWN, `ROOT-026` política de fallo). Identidad (`ROOT-016`), factualidad de Persona (`ROOT-017`), prompt trust (`ROOT-019`) y provenance Oracle (`ROOT-018`) permanecen separados porque requieren contratos y pruebas diferentes.

## 5. Catálogo de causas raíz

En cada fila, “fix/prueba” es el criterio de cierre, no una implementación prescrita. “Riesgo” identifica el vecino cuya regresión debe vigilarse.

| ROOT / prioridad / título | Owner principal; secundarios | Tickets absorbidos; relacionados no absorbidos | Invariante, causa, manifestación e impacto | Dependencias | Criterio de fix / prueba / riesgo |
|---|---|---|---|---|---|
| **ROOT-001 · P0 — Resolución de target no es fail-closed** | `ai_engine.motor_resolution`; Motor/Grounding | MOTOR-SAFETY-001; rel. ROOT-027/028/050/051 | Una aclaración, corrección o rechazo jamás produce un target ejecutable. El adapter legacy pierde estados y sustituye objetivo; puede mover/interactuar con el objeto equivocado. | ROOT-029 recomendado, no requerido | Conservar estado no-ejecutable hasta boundary; matriz correction/clarification/unsafe prueba cero dispatch. Riesgo: comandos válidos. |
| **ROOT-002 · P0 — Contrato numérico físico no finito/acotado** | `ai_engine.motor_schemas`; API/Unity | MOTOR-CONTRACT-002; rel. ROOT-038/042 | Todo parámetro físico es finito, acotado y con unidad estable antes de serializar. Coerción/no-finitos se vuelven `null` o cruzan capas; riesgo físico/corrupción semántica. | ninguna | Rechazo antes de action/dispatch; pruebas NaN/±Inf/bordes por schema y wire. Riesgo: compatibilidad de inputs. |
| **ROOT-003 · P1 — No existe identidad y ledger canónicos de acción/outcome** | Orchestrator; API, Task, Unity, Memory | API-CONTRACT-003, DTO-CONTRACT-003, ORCH-AUDIT-001/002, TASK-AUDIT-001, EXEC-AUDIT-001/002, UNITY-BOUNDARY-002; rel. ROOT-004/007/054 | Una transacción origina una acción estable y cada outcome pertenece a ella exactamente una vez. Hoy IDs inertes/alias/RAM y tareas actuales permiten duplicación o atribución tardía errónea. | ROOT-006; ROOT-012 para concurrencia | Ledger durable con transiciones idempotentes; duplicate/late/unknown/reordered/restart. Riesgo: cache, TaskContext, clientes. |
| **ROOT-004 · P1 — Estados de confirmación/ejecución están colapsados** | Orchestrator/Tools; State/Unity | DTO-CONTRACT-004, TOOL-CONTRACT-002, STATE-AUDIT-001/002; rel. ROOT-003/005 | `pending`, autorización, dispatch, executing, success y failure son distintos. Hoy decisión válida puede contarse como éxito y disparar derivados antes del efecto. | ROOT-003, ROOT-006 | Máquina de estados explícita; transición y no-transición por receipt. Riesgo: UI/task/biología. |
| **ROOT-005 · P1 — Consumer declara success sin materialización** | `unity_client`; Orchestrator | UNITY-BOUNDARY-001; rel. ROOT-003/004 | Success requiere efecto material observable. Cuatro handlers Unity devuelven éxito sin hacerlo; aprendizaje/estado reciben verdad falsa. | ROOT-003, ROOT-004 | Receipt por handler y no-op/failure explícitos; prueba Unity simulada de efecto. Riesgo: compatibilidad de DTO. |
| **ROOT-006 · P1 — Resultado/approval booleano no es estricto** | Tools/Memory; Orchestrator | MEM-CONTRACT-002, TOOL-CONTRACT-001/003/004; rel. ROOT-004/015 | Solo un resultado tipado decide éxito/autorización. Truthiness y sentinels heterogéneos promueven fallos a ok. | ninguna | Normalizar en adapters y rechazar tipos ambiguos; tabla de falsy/truthy. Riesgo: handlers legacy. |
| **ROOT-007 · P1 — El turno no tiene unidad de trabajo** | Orchestrator; Behavior/Memory/State | ORCH-AUDIT-003/004, EXEC-AUDIT-003; rel. ROOT-003/010/012 | Un fallo no deja side effects no reconciliados. Efectos preceden validación/commit y los parciales se reportan mal. | ROOT-003, ROOT-006 | Fases validate/prepare/commit y receipt de parcial; inyección de fallo en cada frontera. Riesgo: orden de AuditTrace y memoria. |
| **ROOT-008 · P1 — Decisión/fallback pierde estado tipado y provenance** | AI/Motor/Persona; API | AI-CONTRACT-002, MOTOR-CONTRACT-001, PERSONA-AUDIT-002; rel. ROOT-017/020 | Toda salida conserva valid/error/fallback/proveedor y grounding. Adaptadores la reducen a payload. | ROOT-020 | Resultado discriminado end-to-end; pruebas fallback/repair/error. Riesgo: DTO público. |
| **ROOT-009 · P1 — Pensamiento privado cruza la frontera pública** | API schemas; Orchestrator/Unity | API-CONTRACT-001; rel. ROOT-010/019 | Thought/memoria/prompt interno nunca es público. El DTO REST/Unity lo expone. | ninguna | DTO público por allowlist; snapshot niega campos privados. Riesgo: clientes que lo consumen. |
| **ROOT-010 · P1 — No hay error envelope público estable y redacted** | API; logger/Unity/Orchestrator | CONFIG-AUDIT-006, API-CONTRACT-002, DTO-CONTRACT-001; rel. ROOT-007/055 | Error externo contiene código estable, no internals, y coincide con wire. Hoy excepciones/log/Unity divergen o filtran. | ROOT-006 | Mapper central y redacción; fixtures por frontera. Riesgo: diagnóstico humano y compatibilidad. |
| **ROOT-011 · P1 — Task lifecycle no expresa intención/transición real** | `core.task_context`; Orchestrator | CORE-AUDIT-001, TASK-AUDIT-002/003; rel. ROOT-003 | Solo intención de tarea crea objetivo y reset/abandono cambian estado coherente. Hoy texto gobierna vida de tarea. | ROOT-003 | Eventos/estados explícitos; comandos conversacionales, reset y abandono. Riesgo: continuidad conversacional. |
| **ROOT-012 · P1 — Ownership/atomicidad del estado compartido es incompleto** | Core/Orchestrator; Memory/cache | CONC-AUDIT-001/002/003/004; rel. ROOT-007/015/053 | Un turno observa y muta snapshots consistentes. Locks parciales permiten carreras y pérdida. | ninguna | Política por owner + operaciones compuestas atómicas; pruebas deterministas de interleaving. Riesgo: deadlocks/latencia. |
| **ROOT-013 · P1 — Persistencia de memoria no es schema-fiel** | Memory types/encoder; State | MEM-CONTRACT-001/005/009; rel. ROOT-015/016 | Persistir/leer conserva estado emocional, metadata y origen sin colisión. Hoy el round-trip pierde significado. | ROOT-039 | Schema/version/namespace canónicos y migración compatible; round-trip. Riesgo: recuerdos existentes. |
| **ROOT-014 · P1 — Retrieval no expresa salud ni aísla datos temporales malos** | Memory retriever/manager; Chroma | MEM-CONTRACT-003/006, MEM-RETRIEVE-001; rel. ROOT-040 | `ok-empty`, partial y failed son distinguibles; un registro malo no invalida el lote. Hoy se colapsan/propagan. | ROOT-039 | Receipt tipado + parse UTC por elemento; lote mixto/no-hit/backend fail. Riesgo: ranking/contexto. |
| **ROOT-015 · P1 — Escrituras de memoria no son atómicas/idempotentes ni verificables** | Memory manager/stores; Orchestrator | MEM-PERSIST-001/002, MEM-CONC-001, MEM-CONSOLIDATE-001; rel. ROOT-003/006/012 | Created/already/failed/ambiguous son receipts durables; dedupe+write y consolidate+delete son indivisibles. | ROOT-006, ROOT-012 | Idempotency key/operación atómica/receipt; retry y fallo entre pasos. Riesgo: Chroma legacy. |
| **ROOT-016 · P1 — Identidad/self no tiene owner ni snapshot canónicos** | Orchestrator/identity state; Personality/State | CORE-AUDIT-002, IDENTITY-AUDIT-001, PERSONALITY-AUDIT-001; rel. ROOT-017/019 | Una fuente versionada define self/traits/state proyectable. Globals y fragmentos RAM compiten; Persona no recibe identidad completa. | ROOT-012 | Owner único y snapshot immutable/versionado; restart y referencias. Riesgo: voz/Behavior. |
| **ROOT-017 · P1 — Persona no es renderer factual verificable** | Persona engine; Oracle/Identity/Memory | ORACLE-AUDIT-001, PERSONA-AUDIT-001; rel. ROOT-008/016/018/019 | Narrativa no contradice hechos ni certifica lo no sustentado. Reescritura libre pierde Oracle/state/self. | ROOT-008, ROOT-016, ROOT-018, ROOT-019 | Claims ligados a fuentes/constraints y fallback honesto; corpus de contradicciones. Riesgo: naturalidad. |
| **ROOT-018 · P1 — Conocimiento pierde provenance/version y cache identity** | Oracle engine/cache; LLM adapter | ORACLE-CACHE-001, LLM-AUDIT-001; rel. ROOT-017/053 | Cada belief/fact conserva proveedor, causa, versión y cache key. Hoy todo se comprime bajo rol/contenido. | ROOT-021 para deadline confiable | Receipt de conocimiento versionado end-to-end; cache hit/miss/proveedor/fallback. Riesgo: invalidación de cache. |
| **ROOT-019 · P1 — Datos no confiables reciben autoridad de system prompt** | Context/prompt builder; Persona/Memory/Perception | C2-CONTRACT-001; rel. ROOT-009/016/017 | Memoria/percepción son datos delimitados, nunca instrucciones. Hoy comparten autoridad system. | ROOT-016 | Frontera estructural de trust y pruebas prompt injection. Riesgo: calidad de contexto. |
| **ROOT-020 · P1 — Parsing estructurado no tiene resultado degradado estricto** | AI/Oracle/Persona parsers | AI-CONTRACT-001, LLM-CONTRACT-001, C2-CONTRACT-002; rel. ROOT-008/018 | Malformed/partial/plain no se promueve silenciosamente a decisión o conocimiento válido. | ninguna | Union valid/degraded/error, schema versionado; fuzz/malformed. Riesgo: disponibilidad/fallback. |
| **ROOT-021 · P1 — Llamadas LLM no comparten deadline/retry budget** | Gemini/client factory; Oracle/Persona | CONFIG-AUDIT-001, LLM-PROVIDER-001; rel. ROOT-018 | Toda llamada termina en presupuesto finito y solo reintenta errores elegibles. Hoy puede colgar o repetir indiscriminadamente. | ROOT-023 recomendado | Deadline total + clasificación; timeout/429/4xx/malformed sin red real. Riesgo: latencia y fallback. |
| **ROOT-022 · P1 — Startup no posee/rollback/cierra recursos simétricamente** | `main`; Chroma/LLM/FastAPI | BOOT-AUDIT-001/002/003/004/007; rel. ROOT-023/030/031 | Cada adquisición tiene owner y cierre idempotente aun en fallo parcial. Hoy hay handles/referencias residuales. | ROOT-031 | Resource stack explícito; fallo inyectado en cada paso, startup/shutdown repetido. Riesgo: orden de cierre. |
| **ROOT-023 · P1 — Readiness no representa capacidad efectiva** | `main`/API health; client factory | BOOT-AUDIT-006, CONFIG-AUDIT-007; rel. ROOT-021/022 | Ready/degraded/unavailable reflejan backend realmente utilizable. Se anuncian candidatos o degradación como nominal. | ROOT-022 | Snapshot de capabilities efectivo; matrix de init parcial. Riesgo: orquestación externa. |
| **ROOT-024 · P1 — Routing léxico básico no es reproducible** | Triage router | TRIAGE-AUDIT-001; rel. ROOT-025/026/046 | Comandos y conversación comunes respetan fronteras conocidas. Reglas substring desvían casos. | ninguna | Dataset de límites/negaciones y decisión estable. Riesgo: recall. |
| **ROOT-025 · P1 — UNKNOWN mezcla tres semánticas** | Triage contracts; Orchestrator | TRIAGE-CONTRACT-001; rel. ROOT-026 | Clase segura, incertidumbre y fallo son estados distintos. La mezcla selecciona rutas equivocadas. | ROOT-020 recomendado | Resultado discriminado y pruebas de cada caso. Riesgo: adapters terminales. |
| **ROOT-026 · P1 — Excepción de Triage tiene fallback dependiente del canal** | Orchestrator/Triage | TRIAGE-AUDIT-002; rel. ROOT-025 | Un fallo de clasificador sigue policy explícita y observable, no Persona por accidente. | ROOT-025 | Inyección de excepción por canal y route esperado. Riesgo: disponibilidad conversacional. |
| **ROOT-027 · P1 — Escala/umbral unsafe es inalcanzable** | Reasoning risk policy | AI-AUDIT-001; rel. ROOT-001 | Todo umbral de seguridad es alcanzable en el dominio del score. Máximo 20 no llega a 50. | ninguna | Unidades/rango únicos; casos seguros/límite/unsafe. Riesgo: falsos bloqueos. |
| **ROOT-028 · P1 — Resolución fuzzy pierde tipo de interacción** | Reasoning/Grounding | AI-AUDIT-002; rel. ROOT-001/050 | Target resuelto conserva la acción pretendida. La omisión cambia semántica del comando. | ninguna | Propagar `interaction_type`; matriz objeto×acción. Riesgo: aliases legacy. |
| **ROOT-029 · P1 — Perception snapshot carece de identidad/freshness/ownership** | perception models; Motor/Unity | MOTOR-CONTRACT-005/006/007; rel. DTO-CONTRACT-003/ROOT-001 | Toda decisión física refiere generation/timestamp/freshness/IDs y preserva cero. Hoy snapshots son ambiguos/inestables. | ROOT-038 recomendado | Snapshot versionado y reject-stale; cero/duplicate/reordered/generation. Riesgo: wire Unity. |
| **ROOT-030 · P2 — Cache global Chroma cruza lifecycle/path** | Chroma client | BOOT-AUDIT-005; rel. ROOT-022 | Un cache entry pertenece a una identidad de DB y lifecycle. | ROOT-022 | key/close explícitos; dos paths/reopen. Riesgo: conexiones. |
| **ROOT-031 · P2 — Logging puede impedir import/startup** | `core.logger` | BOOT-AUDIT-008; rel. ROOT-010/022 | Fallar logfile degrada logging, no runtime. | ninguna | fallback stderr/no recursion; path denegado. Riesgo: doble handler. |
| **ROOT-032 · P2 — Configuración no tiene fuente/path canónicos** | Settings/main/SDK | CONFIG-AUDIT-002/005/009; rel. ROOT-034 | Mismo config efectivo sin depender de CWD ni defaults paralelos. | ROOT-034 | inyección y path absoluto; matrices CWD. Riesgo: despliegues actuales. |
| **ROOT-033 · P3 — Policy de proveedor no está registrada** | docs/client factory | CONFIG-AUDIT-003; rel. ROOT-018/021 | Selección intencional es auditable. | ROOT-032 recomendado | documento/test de elección. Riesgo bajo. |
| **ROOT-034 · P2 — Settings acepta valores inválidos** | Settings | CONFIG-AUDIT-004; rel. ROOT-032 | Config inválida falla temprano con secreto redacted. | ninguna | validators/boundaries. Riesgo: configs toleradas. |
| **ROOT-035 · P3 — Ruta Gemini legacy falla con key ausente** | Gemini legacy client | CONFIG-AUDIT-008; rel. ROOT-021 | Ruta retirada o validada explícitamente. | ROOT-032 | unit key None. Riesgo bajo. |
| **ROOT-036 · P2 — Packaging/dependencias no es reproducible** | repo packaging | DEPS-AUDIT-001/002 | Python y extras tienen contrato instalable. | ninguna | clean-env/import matrix. Riesgo: plataformas. |
| **ROOT-037 · P2 — Normalización API no es uniforme** | API schemas | API-CONTRACT-004; rel. ROOT-038 | Vacíos/whitespace/extras siguen una policy. | ninguna | matrix request validation. Riesgo: clientes. |
| **ROOT-038 · P2 — DTO Python/C# duplicado sin versión** | API/Unity DTOs | DTO-CONTRACT-002; rel. ROOT-002/003/029 | Wire schema tiene una fuente/version. | ROOT-002, ROOT-003, ROOT-029 antes de congelar | contract snapshots ambos lados. Riesgo: compatibilidad. |
| **ROOT-039 · P2 — Metadata `None`/vacía carece de semántica** | Memory types/encoder | MEM-CONTRACT-004; rel. ROOT-013/014 | Ausente y vacío tienen definición estable. | ninguna | round-trip cases. Riesgo: filtros legacy. |
| **ROOT-040 · P2 — Score de memoria carece de dominio estable** | Memory retriever | MEM-CONTRACT-007; rel. ROOT-014 | Ranking usa score finito/acotado/immutable. | ROOT-014 recomendado | bounds/order/nonfinite. Riesgo: ranking. |
| **ROOT-041 · P2 — Objetos Memory no son profundamente inmutables** | Memory types | MEM-CONTRACT-008; rel. ROOT-043 | Snapshot no cambia por alias. | ninguna | mutation isolation. Riesgo: copias. |
| **ROOT-042 · P2 — MotorAction no fuerza invariantes runtime** | motor schemas | MOTOR-CONTRACT-003/004; rel. ROOT-002 | Tipo/acción/campos forman combinación válida. | ROOT-002 | schema cross-field matrix. Riesgo: producers legacy. |
| **ROOT-043 · P2 — Cache expone instancia mutable compartida** | Orchestrator cache | ORCH-AUDIT-005; rel. ROOT-003/041 | Cache hit no comparte mutación con original. | ROOT-003 | identity/mutation test. Riesgo: performance. |
| **ROOT-044 · P2 — Filtro recent es contrato muerto** | Memory filters/retriever | MEM-RETRIEVE-002; rel. ROOT-014 | O se consume o se retira. | ROOT-014 | behavior filter test. Riesgo bajo. |
| **ROOT-045 · P3 — WorkingMemory ignora `n`** | working memory | MEM-RAM-001 | API respeta argumento. | ninguna | n=0/1/N. Riesgo bajo. |
| **ROOT-046 · P2 — Triage model no tiene evaluación/deploy provenance** | training/router | TRIAGE-DATA-001, TRIAGE-MODEL-001; rel. ROOT-024/048 | Artifact identifica corpus/split/métricas/versión. | ROOT-024 | reproducibilidad y holdout. Riesgo: artifact drift. |
| **ROOT-047 · P2 — Confidence de Triage mezcla escalas/no-finitos** | Triage contracts | TRIAGE-CONTRACT-002 | Confidence finita en dominio único. | ninguna | bounds/nonfinite/providers. Riesgo: thresholds. |
| **ROOT-048 · P2 — Taxonomía single-label pierde multi-intent** | Triage data/policy | TRIAGE-DATA-002; rel. ROOT-024 | Política compuesta explícita. | ROOT-024 | corpus multi-intent. Riesgo: rutas. |
| **ROOT-049 · P3 — Clasificador/mapping Triage legacy sin uso** | Triage repo | TRIAGE-MODEL-002 | Código/artifact tiene owner o se retira. | ROOT-046 recomendado | reachability. Riesgo bajo. |
| **ROOT-050 · P2 — Ranking Grounding ignora descriptor distintivo** | Grounding resolver | GROUND-AUDIT-001; rel. ROOT-001/028 | Descriptor discriminante afecta score antes del tie. | ROOT-028 recomendado | pares similares. Riesgo: ranking. |
| **ROOT-051 · P2 — Clarification no identifica candidatos** | Grounding UX | GROUND-AUDIT-002; rel. ROOT-001/050 | Usuario puede elegir inequívocamente. | ROOT-050 | duplicate names/IDs. Riesgo: copy. |
| **ROOT-052 · P2 — Desktop Future tiene umbral imposible** | Desktop resolver | GROUND-AUDIT-003 | Umbral está dentro del dominio alcanzable. | ninguna | boundary 91/92. Riesgo: falsos positivos. |
| **ROOT-053 · P2 — Oracle cache no coalescea misses** | Oracle cache | ORACLE-CACHE-002; rel. ROOT-018 | Una key concurrente hace una llamada/escritura. | ROOT-018 | concurrent single-flight. Riesgo: locks. |
| **ROOT-054 · P2 — Transporte Unity pierde eventos sin ack/retry** | Unity ApiClient | TRANSPORT-AUDIT-001; rel. ROOT-003 | Retry conserva ID y no elimina hasta ack. | ROOT-003 | fail/retry/duplicate/restart. Riesgo: cola infinita. |
| **ROOT-055 · P2 — Notificaciones confunden fallo con vacío** | API notifications | API-BOUNDARY-001; rel. ROOT-010 | Empty y backend failure son distintos. | ROOT-010 | fault injection/status envelope. Riesgo: cliente terminal. |

## 6. Prioridades consolidadas

| Prioridad | ROOT | Cantidad | Razón de gate |
|---|---|---:|---|
| P0 | ROOT-001, ROOT-002 | 2 | target equivocado/unsafe y valores físicos inválidos pueden materializarse |
| P1 | ROOT-003–ROOT-029 | 27 | seguridad lógica, lifecycle/outcome, factualidad, memoria/identidad, readiness o contratos centrales previos al gate |
| P2 | ROOT-030, 031, 032, 034, 036–044, 046–048, 050–055 | 22 | robustez/deuda relevante o gates parciales, sin impacto P0 directo |
| P3 | ROOT-033, 035, 045, 049 | 4 | policy/legacy/API menor sin bloqueo central |

Escalaciones consolidadas:

- `ROOT-002` es P0: Bloque 06 elevó `MOTOR-CONTRACT-002` al demostrar cruce real de no-finitos hacia wire/dispatch.
- `ROOT-020` es P1 aunque sus tickets nacieron P2: aceptar estructura degradada como decisión/conocimiento impide confiar en factualidad y DevelopmentalCore.
- `ROOT-023` es P1 aunque sus manifestaciones nacieron P2: el gate necesita que readiness sea una verdad operacional.
- `ROOT-029` es P1 aunque sus tickets nacieron P2/P3: un field test físico no puede atribuir una acción a percepción sin identity/freshness.

## 7. Grafo de dependencias

```text
ROOT-002 numeric safety ─┬─> ROOT-042 MotorAction runtime ─> field test
                         └─> ROOT-038 wire schema
ROOT-001 target fail-closed ───────────────────────> field test

ROOT-006 typed results ─┬─> ROOT-003 action ledger ─┬─> ROOT-004 execution states ─> ROOT-005 material success
ROOT-012 atomic state ──┘                            ├─> ROOT-007 unit of work
                                                    ├─> ROOT-011 task lifecycle
                                                    └─> ROOT-054 transport retry

ROOT-039 metadata semantics ─┬─> ROOT-013 memory fidelity
                             └─> ROOT-014 retrieval health
ROOT-006 + ROOT-012 ─────────────> ROOT-015 durable memory receipts

ROOT-016 identity owner ─┬──────────────────────────> ROOT-017 factual Persona
ROOT-018 provenance ─────┤
ROOT-019 prompt trust ───┤
ROOT-020 strict parse ─> ROOT-008 typed fallback ───┘
ROOT-021 LLM budget ────────────────────────────────> ROOT-018

ROOT-031 logging fail-soft ─> ROOT-022 resource lifecycle ─> ROOT-023 truthful readiness
ROOT-025 UNKNOWN semantics ─> ROOT-026 Triage failure policy
ROOT-029 perception identity ─> ROOT-038 wire schema ───────> field test
```

| ROOT | Depende de | Bloquea a | Razón estricta |
|---|---|---|---|
| ROOT-003 | ROOT-006, ROOT-012 | ROOT-004, 005, 007, 011, 043, 054; DevelopmentalCore | un ledger no puede persistir resultados ambiguos ni competir sobre estado no atómico |
| ROOT-004 | ROOT-003, ROOT-006 | ROOT-005; outcome learning | las transiciones necesitan identidad y receipt tipado |
| ROOT-005 | ROOT-003, ROOT-004 | field test; DevelopmentalCore | materialización debe confirmar una transición concreta |
| ROOT-007 | ROOT-003, ROOT-006 | estado/memoria confiables | rollback/reconciliación necesitan acción y resultado identificables |
| ROOT-008 | ROOT-020 | ROOT-017 | no se puede preservar fallback si el parser no distingue degradación |
| ROOT-011 | ROOT-003 | aprendizaje por tarea | outcome debe localizar la tarea por identidad, no por actualidad |
| ROOT-013 | ROOT-039 | DevelopmentalCore | el schema debe definir metadata vacía antes del round-trip canónico |
| ROOT-014 | ROOT-039 | DevelopmentalCore | receipt de retrieval necesita semántica estable de metadata |
| ROOT-015 | ROOT-006, ROOT-012 | DevelopmentalCore | receipt e idempotencia exigen tipos estrictos y atomicidad |
| ROOT-017 | ROOT-008, ROOT-016, ROOT-018, ROOT-019 | DevelopmentalCore factual | renderer requiere identidad, fuentes, trust y fallback explícitos |
| ROOT-018 | ROOT-021 | ROOT-017 | provenance debe registrar también timeout/retry/fallback final |
| ROOT-021 | ninguna | ROOT-018 | proveedor final no es confiable sin presupuesto finito |
| ROOT-022 | ROOT-031 | ROOT-023 | cleanup no debe quedar inutilizado por inicialización de logging |
| ROOT-023 | ROOT-022 | pre-Capa2 gate | readiness depende del estado final de recursos adquiridos |
| ROOT-026 | ROOT-025 | rutas centrales | la policy de excepción necesita distinguir failure de UNKNOWN |
| ROOT-038 | ROOT-002, ROOT-003, ROOT-029 | field test | congelar wire antes fijaría un contrato físico/correlacional incompleto |
| ROOT-042 | ROOT-002 | field test | invariantes cross-field se apoyan en valores válidos |
| ROOT-051 | ROOT-050 | UX posterior | labels útiles dependen de candidatos bien rankeados |
| ROOT-053 | ROOT-018 | eficiencia Oracle | single-flight debe usar la key canónica versionada |
| ROOT-054 | ROOT-003 | field test | retry seguro exige ID estable e idempotencia |

Las ROOT no listadas en la tabla no tienen prerequisito estricto; sus relaciones son de vecindad/regresión. En particular, `ROOT-001` y `ROOT-002` pueden corregirse primero sin esperar un rediseño de lifecycle.

## 8. Invariantes de gate

| # | Invariante | Estado actual | ROOT bloqueantes | Prueba de cierre | Field test |
|---:|---|---|---|---|:---:|
| 1 | acción bloqueada/inválida nunca llega a consumer ejecutable | **FAIL** | ROOT-001, 002, 007 | correction/clarification/unsafe/nonfinite con cero dispatch | sí |
| 2 | parámetros físicos finitos, acotados y con unidades estables | **FAIL** | ROOT-002, 042 | matriz NaN/±Inf/bordes/unidades en schema y wire | sí |
| 3 | `id_transaccion → id_accion → outcome` es cadena única | **FAIL** | ROOT-003 | duplicate/restart/late/unknown conserva unicidad | sí |
| 4 | duplicate/late/unknown/reordered no mutan tarea equivocada | **FAIL** | ROOT-003, 011 | secuencias reordenadas contra dos tareas | sí |
| 5 | pending/dispatch/executing/success/failure son distintos | **FAIL** | ROOT-003, 004, 006 | tabla completa de transiciones y rechazos | sí |
| 6 | success solo existe tras efecto material | **FAIL** | ROOT-004, 005 | handlers con/no efecto y receipt comprobable | sí |
| 7 | no-op/fallo/action-null son distinguibles y terminales | **FAIL** | ROOT-004, 005, 008 | cada resultado cierra exactamente una action | sí |
| 8 | retry conserva ID y no duplica side effects | **FAIL** | ROOT-003, 054 | fallo transporte, retry y replay tras restart | sí |
| 9 | perception tiene generation/timestamp/freshness/IDs | **FAIL** | ROOT-029, 038 | snapshot stale/zero/duplicate/reordered | sí |
| 10 | private thought/memory/prompt/internal errors no son públicos | **FAIL** | ROOT-009, 010, 019 | snapshots REST/terminal/Unity y prompt-injection | sí |
| 11 | terminal continúa solo-conversación | **PASS** | ninguno | conservar probes de adaptación `motor→persona` sin materialización | sí |
| 12 | Audit/ledger reconstruye la acción completa | **PARTIAL** | ROOT-003 | un ID reconstruye request/action/states/outcome/retry | sí |
| 13 | retrieval distingue ok-empty/failed/partial | **FAIL** | ROOT-014 | no-hit, backend fail y lote mixto | no; sí para DevelopmentalCore |
| 14 | writes distinguen created/already/failed/ambiguous | **FAIL** | ROOT-015 | dedupe concurrente, retry y fallo entre pasos | no; sí para DevelopmentalCore |
| 15 | Persona no certifica hechos sin sustento | **FAIL** | ROOT-017, 018, 020 | contradicciones Oracle/state/memory y malformed | no; sí para DevelopmentalCore |
| 16 | knowledge conserva provenance/version/cache identity | **FAIL** | ROOT-018, 021 | hit/miss/fallback/version/provider | no; sí para DevelopmentalCore |
| 17 | rutas básicas de Triage son reproducibles y failure explícito | **FAIL** | ROOT-024, 025, 026 | corpus de límites + UNKNOWN/failure por canal | sí |
| 18 | startup/shutdown parcial es limpio y readiness veraz | **FAIL** | ROOT-022, 023, prereq ROOT-031 | fallo por etapa + doble shutdown + health | sí |
| 19 | self/identity tiene owner y snapshot versionado | **FAIL** | ROOT-016 | identidad tras turnos/restart/concurrencia | no; sí para DevelopmentalCore |
| 20 | backlog de gate no contiene P0/P1 OPEN | **FAIL** | ROOT-001–029 | evidencia de cierre individual + regresión final | sí |

AuditTrace ya satisface su contrato OBS-001–008 y debe usarse como comparador de no interferencia. No sustituye el ledger durable ausente de `ROOT-003`.

## 9. Gate de DevelopmentalCore

No se diseña ni migra DevelopmentalCore en este paso. Antes de hacerlo debe poder asumirse que las señales que aprendería tienen dueño, provenance y significado.

### BLOCKER

- Seguridad y outcome: `ROOT-001`, `ROOT-002`, `ROOT-003`, `ROOT-004`, `ROOT-005`, `ROOT-006`, `ROOT-007`, `ROOT-008`.
- Task/state/concurrencia: `ROOT-011`, `ROOT-012`.
- Memoria: `ROOT-013`, `ROOT-014`, `ROOT-015` (con prerequisito técnico `ROOT-039`).
- Identidad y factualidad: `ROOT-016`, `ROOT-017`, `ROOT-018`, `ROOT-019`, `ROOT-020`.
- Si DevelopmentalCore consumirá experiencia física: `ROOT-027`, `ROOT-028`, `ROOT-029` también son blockers de datos de aprendizaje, no del diseño abstracto.

### RECOMMENDED antes del gate

- `ROOT-009`, `ROOT-010`: privacidad y error envelope estable.
- `ROOT-021`: deadline/retry para que provenance incluya terminación confiable.
- `ROOT-022`, `ROOT-023`: ownership de proceso y readiness verdadera.
- `ROOT-024`, `ROOT-025`, `ROOT-026`: routing reproducible para no entrenar sobre rutas accidentales.
- `ROOT-038`, `ROOT-042`, `ROOT-054`: wire, schema y retry requeridos antes del field test que produzca experiencia real.

### CAN WAIT

Las ROOT de la lista `DEFER POST-CAPA2` pueden esperar mientras no se amplíe su impacto con evidencia nueva. Ningún P2/P3 fue promovido a blocker solo por prioridad administrativa; `ROOT-039` es prerequisito técnico porque dos blockers P1 dependen de su semántica.

## 10. DEFER POST-CAPA2

Estas 21 causas no bloquean seguridad, contratos centrales, DevelopmentalCore ni el field test controlado actual:

| ROOT | Motivo de diferimiento |
|---|---|
| ROOT-030 | cache Chroma multi-path; robustez de lifecycle secundaria |
| ROOT-032 | source/path config; entorno del gate puede fijarse explícitamente |
| ROOT-033 | documentación de policy de proveedor |
| ROOT-034 | validación general de Settings; gate usa configuración conocida |
| ROOT-035 | ruta Gemini legacy |
| ROOT-036 | packaging/extras; no cambia contrato runtime auditado |
| ROOT-037 | normalización periférica de requests |
| ROOT-040 | dominio fino de score, tras receipt de retrieval |
| ROOT-041 | inmutabilidad profunda de Memory |
| ROOT-043 | copia defensiva de cache de respuesta |
| ROOT-044 | filtro recent sin consumidor |
| ROOT-045 | parámetro `n` de WorkingMemory |
| ROOT-046 | provenance de entrenamiento/deploy de Triage posterior al routing básico |
| ROOT-047 | escala de confidence de Triage |
| ROOT-048 | multi-intent taxonomy |
| ROOT-049 | classifier/mapping legacy |
| ROOT-050 | descriptor adicional de ranking; el fail-closed P0 impide target dudoso |
| ROOT-051 | labels de aclaración; puede conservarse bloqueo seguro |
| ROOT-052 | Desktop Future no está en el field test actual |
| ROOT-053 | single-flight Oracle es eficiencia, no verdad semántica |
| ROOT-055 | error/vacío de notificaciones no gobierna ejecución |

No se difieren `ROOT-031`, `ROOT-038`, `ROOT-039`, `ROOT-042` ni `ROOT-054`: son P2, pero respectivamente habilitan lifecycle P1, wire del field test, contratos Memory P1, seguridad de schema y retry idempotente.

## 11. Orden exacto recomendado de fixes

Cada número significa un ciclo independiente: reproducir → fix acotado → tests focales → comparar AuditTrace → regresión proporcional → commit → STOP. No se agrupan números en un megapatch.

1. ROOT-002
2. ROOT-001
3. ROOT-006
4. ROOT-012
5. ROOT-003
6. ROOT-004
7. ROOT-005
8. ROOT-007
9. ROOT-011
10. ROOT-010
11. ROOT-009
12. ROOT-039 (prerequisito P2)
13. ROOT-013
14. ROOT-014
15. ROOT-015
16. ROOT-020
17. ROOT-008
18. ROOT-016
19. ROOT-019
20. ROOT-021
21. ROOT-018
22. ROOT-017
23. ROOT-031 (prerequisito P2)
24. ROOT-022
25. ROOT-023
26. ROOT-024
27. ROOT-025
28. ROOT-026
29. ROOT-027
30. ROOT-028
31. ROOT-029
32. ROOT-042 (field-test P2)
33. ROOT-038 (field-test P2)
34. ROOT-054 (field-test P2)
35. Definition of Ready y field test controlado; no automático
36. ROOT-030
37. ROOT-034
38. ROOT-032
39. ROOT-036
40. ROOT-037
41. ROOT-040
42. ROOT-041
43. ROOT-043
44. ROOT-044
45. ROOT-046
46. ROOT-047
47. ROOT-048
48. ROOT-050
49. ROOT-051
50. ROOT-052
51. ROOT-053
52. ROOT-055
53. ROOT-033
54. ROOT-035
55. ROOT-045
56. ROOT-049

El hito del punto 35 no es una causa y no cambia el total de 55 ROOT. El orden posterior puede detenerse al completar el gate pre-Capa2; las entradas 36–56 son el orden interno recomendado si se decide atender el diferido.

## 12. Fix packets P0/P1

“Archivos probables” orienta la inspección de Fase 3; no autoriza ampliar el patch. Cada packet se detiene al satisfacer su invariante y preservar vecinos.

| ROOT | Objetivo y archivos probables | Reproducción; actual → esperado | Contratos vecinos / AuditTrace | STOP / commit sugerido |
|---|---|---|---|---|
| ROOT-002 | Rechazar parámetros físicos no finitos/acotados antes de action/wire. `ai_engine/motor_schemas.py`, `ai_engine/schemas.py`, `api/schemas.py`, quizá `unity_client/DTOs.cs`. | Extender `capa1_motor_remaining_probe.py` con NaN/±Inf/bordes. Actual: aceptación/coerción/`null`; esperado: invalid, cero dispatch. | Proteger JSON, MotorAction y comandos válidos. Comparar `triage/decision/execution/event_end`: sin execution en inválidos. | STOP cuando todo valor inválido muere antes del consumer y válidos no cambian. `fix(motor): reject invalid physical parameters`. |
| ROOT-001 | Mantener clarification/correction/unsafe como no ejecutable. `ai_engine/motor_resolution.py`, `motor_engine.py`, `core/orchestrator.py`. | `capa1_motor_completion_probe.py`: targets ambiguos/corregidos/unsafe. Actual: target sustituido puede despacharse; esperado: bloqueo/clarificación sin action física. | Proteger fuzzy/affordance y ruta válida. Trace debe conservar razón y `execution` ausente/bloqueada. | STOP al probar cero wrong-target dispatch. `fix(motor): fail closed on unresolved targets`. |
| ROOT-006 | Unificar bool/result/approval estricto. `tools/base.py`, `tools/executor.py`, `tools/registry.py`, `memory/memory_manager.py`. | Tabla `False`, `"false"`, `None`, `{}`, `[]`, receipts. Actual: falsos success; esperado: resultado discriminado/rechazo. | Proteger handlers válidos y ROOT-004/015. Audit `execution.success` coincide con receipt. | STOP sin truthiness implícita en boundary. `fix(contracts): make effect results explicit`. |
| ROOT-012 | Definir ownership y operaciones atómicas del turno. `core/behavior_core.py`, `core/orchestrator.py`, `core/task_context.py`, `memory/working_memory.py`. | Interleavings barrera para dos transacciones y drain de notifications. Actual: carreras/pérdida; esperado: snapshots/commits serializables. | Proteger latencia, no deadlock y trace IDs separados. | STOP con pruebas deterministas, no sleeps frágiles. `fix(core): serialize shared turn state`. |
| ROOT-003 | Crear identidad/lifecycle/ledger durable único. `api/schemas.py`, `core/orchestrator.py`, `core/task_context.py`, storage dedicado, `unity_client/DTOs.cs`, `ApiClient.cs`, `LazzaroAgent.cs`. | `frontiers_deep_probe.py`: duplicate/late/unknown/reordered/restart. Actual: alias/RAM/tarea actual; esperado: una cadena y una aplicación. | Proteger cache, AuditTrace ID, terminal. Trace y ledger deben coincidir sin usar AuditTrace como storage operacional. | STOP cuando todo outcome se aplica 0/1 vez a su action. `fix(execution): add canonical action ledger`. |
| ROOT-004 | Separar pending/authorization/dispatch/executing/success/failure. `core/orchestrator.py`, `core/state_engine.py`, `tools/base.py`, Unity DTO/agent. | Tabla de transiciones. Actual: pending/decision-valid pueden ser success; esperado: solo transitions permitidas. | Proteger ROOT-003 IDs y ROOT-005 receipts. Trace registra transición real. | STOP sin estados implícitos ni doble derived event. `fix(execution): enforce action state machine`. |
| ROOT-005 | Hacer success dependiente de efecto material Unity. `unity_client/LazzaroAgent.cs`, `MovementController.cs`, `SomatoSensorySystem.cs`, DTOs. | Harness de cuatro handlers con efecto/no-op/fallo. Actual: success vacío; esperado: receipt observado o failure/no-op. | Proteger protocolo ROOT-003/004 y simulación sin hardware. Trace final coincide con receipt devuelto. | STOP al eliminar success sin evidencia. `fix(unity): attest material action outcomes`. |
| ROOT-007 | Introducir unidad de trabajo del turno. `core/orchestrator.py`, `behavior_core.py`, `task_context.py`, `memory/memory_manager.py`. | Inyectar fallo antes/después de cada side effect. Actual: estado/memoria parciales con ok/null; esperado: commit o partial explícito/reconciliable. | Proteger orden de stages y compatibilidad de cache. Audit registra error real, nunca genera trabajo extra. | STOP cuando cada fallo deja estado coherente. `fix(core): make turn effects transactional`. |
| ROOT-011 | Convertir TaskContext en state machine explícita. `core/task_context.py`, `behavior_core.py`, `orchestrator.py`. | Conversación, comando, reset, abandono y outcome tardío. Actual: todo crea task y “abandono” narrativo; esperado: transiciones tipadas. | Proteger ROOT-003 correlation y charla. Trace `state_before/after` explica cambio. | STOP sin inferir lifecycle solo de texto. `fix(tasks): enforce explicit task lifecycle`. |
| ROOT-010 | Centralizar error público redacted/versionado. `api/routes.py`, `api/schemas.py`, `core/logger.py`, `unity_client/ApiClient.cs`. | Excepciones sintéticas con secretos/path. Actual: internals/envelopes divergentes; esperado: código estable público + detalle humano redacted. | Proteger status HTTP y ROOT-009 privacidad. Audit conserva clase segura, no secreto. | STOP con snapshots por cliente y redaction. `fix(api): normalize public error envelopes`. |
| ROOT-009 | Retirar thought interno de DTO público. `api/schemas.py`, `api/routes.py`, `unity_client/DTOs.cs`. | Snapshot respuesta normal/error/fallback. Actual: private thought visible; esperado: allowlist pública. | Proteger texto final, action y diagnostics internos en AuditTrace seguro. | STOP si ningún schema/wire público declara el campo. `fix(api): seal private cognition boundary`. |
| ROOT-013 | Preservar schema de memoria en round-trip. `memory/memory_types.py`, `memory_encoder.py`, `episodic_memory.py`, `semantic_memory.py`. | Estado emocional, metadata reservada y origen por create/read. Actual: pérdida/colisión; esperado: igualdad semántica versionada. | Proteger recuerdos existentes, ROOT-039 y sanitización AuditTrace. | STOP con compatibilidad explícita o migración probada. `fix(memory): preserve canonical memory schema`. |
| ROOT-014 | Devolver retrieval receipt saludable y aislar items malos. `memory/memory_retriever.py`, `memory_manager.py`, `memory_filters.py`. | No-hit/backend fail/lote timestamps mixtos. Actual: indistinguibles o lote abortado; esperado: ok-empty/partial/failed con resultados sanos ordenados. | Proteger score/order y OBS-005 sin segunda query. Trace usa metadata de la operación real. | STOP con una sola búsqueda y estados distinguibles. `fix(memory): expose retrieval health receipts`. |
| ROOT-015 | Hacer persist/dedupe/consolidation atómicos e idempotentes. `memory/memory_manager.py`, `episodic_memory.py`, `semantic_memory.py`, `chroma_client.py`. | Dos writers, retry y fallo entre regla/delete. Actual: duplicado/false negative/doble representación; esperado: created/already/failed/ambiguous verificable. | Proteger ROOT-006/012, IDs OBS-005 y Chroma activo. | STOP sin segunda escritura de auditoría y con retry seguro. `fix(memory): make writes idempotent and receipted`. |
| ROOT-020 | Tipar valid/degraded/error en parsers. `ai_engine/response_parser.py`, `oracle_engine.py`, `schemas.py`, `persona_engine.py`. | Fuzz malformed/plain/nested/partial/unknown. Actual: promoción o excepción; esperado: unión discriminada sin conocimiento falso. | Proteger fallbacks útiles, ROOT-008/018 y voz. Audit muestra fallback/error sin payload sensible. | STOP sin aceptación silenciosa de inválido. `fix(ai): make structured parsing total and strict`. |
| ROOT-008 | Propagar estado/provenance de decisión y repair. `ai_engine/motor_engine.py`, `response_parser.py`, `persona_engine.py`, `api/schemas.py`. | Error/fallback/repair válido. Actual: flags desaparecen; esperado: final payload conserva origen y validez. | Proteger DTO público sin private thought y ROOT-020. Trace provider/fallback coincide. | STOP si adapters no reducen estados distintos al mismo objeto. `fix(ai): preserve decision provenance`. |
| ROOT-016 | Establecer owner/snapshot versionado de self. `core/orchestrator.py`, `state_engine.py`, `personality.py`, `ai_engine/identity_state.py`, `persona_prompt_buider.py`. | Dos turnos, restart y referencias mutables. Actual: fragmentos/globals; esperado: owner único y snapshot coherente. | Proteger Behavior, prompts y concurrencia ROOT-012. Trace state before/after no se amplía. | STOP cuando consumers leen snapshot, no globals rivales. `fix(identity): establish canonical self state`. |
| ROOT-019 | Rebajar memoria/percepción a datos no confiables. `ai_engine/context_builder.py`, `prompt_builder.py`, `persona_prompt_buider.py`, prompts. | Memoria/percepción con instrucciones hostiles. Actual: autoridad system; esperado: delimitación/encoding y policy superior intacta. | Proteger factual Persona, token budget y contenido normal. Audit no almacena prompts completos. | STOP con corpus injection y sin fuga. `fix(prompts): separate context data from instructions`. |
| ROOT-021 | Aplicar deadline total y retry clasificado. `ai_engine/gemini_client.py`, `llm_policies.py`, `client_factory.py`, `fallback_ai_client.py`. | Clientes fake: timeout, 429, 4xx, malformed, success tardío. Actual: espera/retry indiscriminado; esperado: presupuesto finito y causa final. | Proteger fallback Gemini-only y ROOT-018 provenance. Trace duration/provider/fallback. | STOP sin red real y con límite monotónico. `fix(llm): enforce bounded retry budgets`. |
| ROOT-018 | Versionar provenance/cache identity de Oracle. `ai_engine/oracle_memory.py`, `oracle_engine.py`, `oracle_client.py`, `llm_generation_adapter.py`. | Hit/miss/provider/fallback/version distinta. Actual: cache/rol borra causa; esperado: receipt de conocimiento end-to-end e invalidación correcta. | Proteger no cachear error y ROOT-017. Trace registra metadatos, no narrativa completa. | STOP cuando una respuesta identifica su origen exacto. `fix(oracle): preserve knowledge provenance`. |
| ROOT-017 | Restringir Persona a renderer factual. `ai_engine/persona_engine.py`, `prompt_builder.py`, `oracle_engine.py`, `identity_state.py`. | Contradicciones state/self/memory/Oracle y ausencia de fuente. Actual: reescribe/certifica; esperado: conserva facts o declara incertidumbre. | Proteger naturalidad, ROOT-008/016/018/019/020. Trace decision/fallback, no pensamiento. | STOP con assertions de facts y fallback honesto. `fix(persona): enforce factual rendering`. |
| ROOT-022 | Asegurar resource ownership/rollback/close. `main.py`, `memory/chroma_client.py`, `ai_engine/client_factory.py`. | Fallo en cada etapa y doble startup/shutdown. Actual: handles/app.state residuales; esperado: cierre idempotente completo. | Proteger ROOT-031 logging y datos Chroma. Audit no participa en cleanup. | STOP con hash de DB intacto y sin recurso vivo. `fix(boot): own and close startup resources`. |
| ROOT-023 | Publicar readiness efectivo. `main.py`, `api/routes.py`, `client_factory.py`. | Matrix proveedor/Chroma parcial. Actual: nominal/candidatos; esperado: ready/degraded/unavailable por capability real. | Proteger contrato health y ROOT-022. Trace no sustituye health. | STOP cuando estado anunciado coincide con operaciones posibles. `fix(boot): report effective readiness`. |
| ROOT-024 | Corregir fronteras de routing léxico. `ai_engine/routing_rules.py`, `triage_router.py`, `test_triage_routing.py`. | Dataset comandos/discusión/negación/substrings. Actual: desvíos; esperado: rutas esperadas deterministas. | Proteger modelo/fallback y Audit route vs effective_route. | STOP en corpus acordado sin retraining incidental. `fix(triage): harden lexical routing boundaries`. |
| ROOT-025 | Separar UNKNOWN, uncertain y failure. `ai_engine/intent_types.py`, `triage_router.py`, `core/orchestrator.py`. | Casos de clase segura, baja confianza y excepción. Actual: mismo valor; esperado: resultado discriminado. | Proteger adaptación terminal y ROOT-026. Trace conserva original/effective. | STOP cuando cada estado tiene policy explícita. `fix(triage): split unknown from classifier failure`. |
| ROOT-026 | Definir fallback de fallo Triage por policy. `ai_engine/triage_router.py`, `core/orchestrator.py`. | Excepción por terminal/API/canal físico. Actual: caída accidental a Reasoning/Persona; esperado: ruta segura/observable definida. | Proteger ROOT-025 y conversación. Trace error y ausencia/presencia de stage correctas. | STOP sin fallback por accidente. `fix(triage): make failure routing explicit`. |
| ROOT-027 | Alinear dominio y umbral unsafe. `ai_engine/reasoning_engine.py`, `subjective_effort.py`, policies Motor. | Casos score mínimo/límite/máximo. Actual: 20 nunca alcanza 50; esperado: unsafe alcanzable y safe no bloqueado. | Proteger ROOT-001 fail-closed y calibration. Trace reason/confidence seguro. | STOP con unidades documentadas y boundary tests. `fix(safety): align motor risk thresholds`. |
| ROOT-028 | Preservar `interaction_type` durante fuzzy. `ai_engine/fuzzy_object_resolver.py`, `motor_resolution.py`, `reasoning_engine.py`. | Matriz mirar/ir/interactuar × alias. Actual: target sin acción; esperado: intención intacta. | Proteger ROOT-001, affordances y ranking. Trace action type final. | STOP sin default silencioso. `fix(grounding): preserve interaction intent`. |
| ROOT-029 | Definir snapshot perceptual con identity/freshness. `ai_engine/perception_models.py`, `motor_schemas.py`, `motor_engine.py`, `unity_client/PerceptionSystem.cs`, `DTOs.cs`. | generation, timestamp, stale, distance=0, duplicate ID, reorder. Actual: identidad ambigua/pérdida; esperado: snapshot validado y stale rechazado. | Proteger ROOT-001/038, no ejecutar Unity real. Trace referencia IDs/metadatos mínimos. | STOP cuando toda action física prueba qué snapshot usó. `fix(perception): version and validate snapshots`. |

### Primer packet autorizado para Fase 3

El primer packet recomendado es **ROOT-002**. Es localmente cerrable, no depende del ledger futuro y reduce exposición física antes de tocar routing. Su criterio STOP es más fuerte que “serializa”: NaN, `+Inf`, `-Inf`, fuera de rango o unidad inválida no deben crear una acción ejecutable ni alcanzar `execution`; los casos válidos deben conservar el contrato actual. Al terminar ese packet se hace commit y se detiene antes de ROOT-001.

## 13. Riesgos de regresión transversales

- Endurecer contratos puede convertir inputs legacy tolerados en errores: snapshots de API/terminal/Unity deben fijarse antes de cada cambio.
- Ledger/state machine puede duplicar la autoridad si convive con cache o TaskContext legacy; un solo owner debe decidir la transición.
- Atomicidad mal aplicada puede introducir deadlocks; los tests de concurrencia deben usar barreras y timeouts finitos.
- Migración de Memory puede alterar recuerdos existentes; nunca probar contra Chroma activa y siempre comparar hashes.
- Parser estricto puede bajar disponibilidad; degradación debe ser explícita, no desaparecer.
- Persona grounded puede perder naturalidad; probar facts y estilo por separado.
- Readiness más honesta puede hacer fallar despliegues antes “verdes”; eso es cambio contractual esperado y debe versionarse.
- AuditTrace debe permanecer fail-soft, sin segunda query/acción ni narrativa sensible; comparar secuencias, trace IDs y resultados funcionales.
- Cambios Python/C# pueden desincronizar wire; `ROOT-038` debe cerrar snapshots a ambos lados antes del field test.

## 14. Definition of Ready — field test

El field test físico puede solicitarse, nunca iniciarse automáticamente, solo cuando:

- `ROOT-001`, `ROOT-002`, `ROOT-003`, `ROOT-004`, `ROOT-005`, `ROOT-006`, `ROOT-007`, `ROOT-008`, `ROOT-009`, `ROOT-010`, `ROOT-011`, `ROOT-017`, `ROOT-022`, `ROOT-023`, `ROOT-024`, `ROOT-025`, `ROOT-026`, `ROOT-027`, `ROOT-028`, `ROOT-029`, `ROOT-038`, `ROOT-042` y `ROOT-054` estén cerradas con evidencia.
- Los invariantes 1–12 y 17–18 estén PASS; el 20 no tenga P0/P1 aplicable al field path.
- Existe kill/stop físico, ambiente despejado, límites de movimiento y operador presente.
- El mismo `id_transaccion/id_accion` une request, percepción, dispatch y outcome material.
- Retry/reconnect conserva ID y no duplica side effects.
- El plan de casos comienza por no-op/bloqueados/simulación, luego movimiento mínimo; nunca salta directo a caso amplio.
- Regression probe y pruebas focales están verdes salvo el `KNOWN_XFAIL` histórico documentado.
- Chroma activa queda fuera de probes destructivos y sus hashes se capturan antes/después.

## 15. Definition of Ready — gate pre-Capa2 / DevelopmentalCore

Antes de diseñar o migrar DevelopmentalCore:

- todas las ROOT P0/P1 están cerradas o existe una decisión explícita que reduzca alcance sin ocultarlas;
- invariantes 1–20 están PASS en el alcance que DevelopmentalCore consumirá;
- outcome/task/action tienen identidad, lifecycle y materialización inequívocos;
- Memory ofrece retrieval/write receipts, round-trip fiel e idempotencia;
- self/state/belief tienen owners y snapshots versionados;
- Oracle/LLM/Persona conservan provenance, trust boundary y factualidad;
- Triage distingue ruta, incertidumbre y fallo de forma reproducible;
- startup/shutdown/readiness son confiables;
- AuditTrace conserva no interferencia y permite comparar cada fix;
- existe evidencia de field test controlado si el diseño usará experiencia física;
- el backlog `DEFER POST-CAPA2` está explícitamente aceptado y no contiene dependencias ocultas del diseño.

## 16. Totales reconciliados

| Métrica | Resultado |
|---|---:|
| Tickets administrativos OPEN únicos | 105 |
| Tickets antes: P0 / P1 / P2 / P3 | 2 / 40 / 56 / 7 |
| Causas raíz | 55 |
| ROOT después: P0 / P1 / P2 / P3 | 2 / 27 / 22 / 4 |
| ROOT multi-ticket | 23 |
| Tickets contenidos en ROOT multi-ticket | 73 |
| Tickets que permanecen uno-a-uno | 32 |
| Conteos administrativos redundantes absorbidos | 50 |
| Tickets históricos cerrados/provisionales excluidos | 3 (`BASE-FIND-001`, `MEM-AUDIT-001`, `TEST-AUDIT-001`) |

Comprobaciones: `2+40+56+7=105`; `2+27+22+4=55`; `73` tickets en 23 grupos producen `50` manifestaciones administrativas por encima de una causa por grupo; `23+32=55` ROOT y `73+32=105` tickets.

## 17. Pruebas de cierre documental

| Control | Resultado |
|---|---|
| `python -B docs/audit/probes/regression_probe.py` (una sola vez al cierre) | **PASS**: 160 PASS, 1 `KNOWN_XFAIL` (`memory.test_chromadb_flow.test_eliminacion`), 0 inesperados; suite/isolation gate PASS |
| `compileall` | no requerido: no se añadió script/probe |
| JSON evidence parseable | **PASS**; 55 ROOT/105 tickets únicos, prioridades y orden reconciliados |
| `git diff --check` | **PASS** |
| hashes Chroma comparados con entrada | **PASS**; 3/3 idénticos antes/después |
| producción modificada | no |
| red / LLM real / Unity / ejecución física | no |

Hashes SHA-256 preservados:

- `data_level0.bin`: `02DD64FFD2DFFBDBFE5F9600798E3C96AA392B3F1D306510CFA4F254A77BF659`
- `length.bin`: `CA9905E27DA718E079150D7B929D337B85D7E6B3D0021C8A0E8B41C7362D6E0A`
- `chroma.sqlite3`: `C003D87617B5156E358E4DD56DAB9B8765401AF239F809750627E42A77D23F6D`

## 18. Git

- Commit de entrada: `f6ef27c5d9256431522d23b91b79945c4e17e066`.
- Archivos autorizados: este informe y `docs/audit/evidence/consolidated_backlog_pre_capa2.json`.
- Archivos preexistentes fuera de alcance y excluidos del commit: tres archivos de `lazzaro_data/` modificados y `constelacion.html` no seguido.
- Commit final: se registra en el cierre externo porque el hash no existe hasta crear el commit.
- Push: no.
