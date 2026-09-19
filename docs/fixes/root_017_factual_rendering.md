# ROOT-017 — puente factual temporal de Persona

Estado de código: `CLOSED_CODE / HUMAN_REVIEW_PENDING` tras la validación y el commit local de este packet. La definición canónica sigue en `docs/audit/consolidated_backlog_pre_capa2.md`: ROOT-017, P1; absorbe `ORACLE-AUDIT-001` y `PERSONA-AUDIT-001`; depende de ROOT-008/016/018/019. El criterio es ligar afirmaciones a fuentes y restricciones, con fallback honesto y corpus de contradicciones.

## Alcance y reemplazo

Este cambio cierra la brecha actual por la que JSON válido de Persona podía transportar `texto_a_comunicar` factual sin respaldo. Es un **puente temporal de estabilización**, no la arquitectura final de Persona ni de Capa 2. El TCM Cognitive Freedom Contract v0.1 (nombre provisional) se desarrolla por separado; aquí no se implementa. La costura de reemplazo es `PersonaFactualBoundaryV1`: Orchestrator llama `prepare(...)` una vez y `finish(raw, turn)` para la salida primaria y, si hubo error sintáctico, el único repair existente. Un futuro adaptador C ABI/CFC puede sustituir ese boundary sin convertir a Orchestrator en validador de fuentes o renderer.

El puente puede disminuir temporalmente la naturalidad y la variación expresiva/HEAT. No se cambió `PERSONA_STYLE`, ni se codificó en los templates una personalidad obligatoria. La rigidez factual no se propone como rigidez permanente de tono, desacuerdo, humor o pensamiento. TEARS futuro sólo aporta relevancia asociativa y no autoridad factual; CÉRBERO futuro sólo selecciona runtime/modelo y no verdad, identidad ni personalidad. Ninguno se implementa ni se enlaza aquí.

## Flujo vigente

1. `compile_persona_evidence` copia valores ya disponibles del turno a snapshots inmutables privados. El índice enviado al modelo contiene IDs, clase, alcance y capacidades, **sin valores**. El valor original no se vuelve a consultar al renderizar.
2. `PersonaPromptBuilder.build_factual_plan` conserva `PromptPlanV1` y agrega `persona_factual_index` como `DATA` de autoridad `NONE`. El usuario sigue siendo `USER`; `SYSTEM` sólo describe el esquema/política propiedad del código.
3. El modelo elige `PersonaFactualPlanV1` v1: de uno a ocho actos cerrados `EVIDENCE_CLAIM`, `ATTRIBUTED_TEXT`, `SOCIAL` o `CLARIFICATION`, más enums de estilo y verbosidad. `extra=forbid`, tipos/versiones estrictos y sin campo libre para texto público, pensamiento, razón, paráfrasis, opinión o formato.
4. El boundary valida ID, capacidad, familia de acto, fuente, alcance temporal, kind, tipo de valor y correlación material. Conflictos estructurales entre átomos comparables bloquean el claim directo; no se hace NLI sobre prosa. Un plan parcialmente válido conserva actos válidos con calificación fija. Ningún acto válido produce incertidumbre fija. Los receipts guardan sólo estados/conteos/códigos.
5. El renderer usa únicamente snapshots y templates propiedad del código. Texto de usuario, memoria agregada y Oracle sólo puede salir citado/atribuido mediante copia exacta o fragmento de 240/480 caracteres, escapado y marcado si se trunca. El resultado público máximo es 700 caracteres; un exceso produce fallback, no excepción pública.

El repair sintáctico de ROOT-008/021 sigue siendo uno, dentro del mismo budget. Reutiliza el mismo `PromptPlanV1`, bundle y registro privado; no repite Memory, Oracle, percepción ni ledger. Un plan sintácticamente válido pero factual inválido **no** invoca juez LLM ni otro repair. `StructuredParseResult` de ROOT-020 mantiene su semántica: un acto factual descartado no convierte `VALID` en `DEGRADED`. Dado que ROOT-008 reserva `SALVAGED` para parse `DEGRADED`, la proyección operativa parcial usa `DETERMINISTIC_FALLBACK` con razón `SAFE_SALVAGE`; el receipt factual interno conserva `PARTIAL`. Una salida `DEGRADED` con texto libre nunca alcanza el renderer.

## Autoridad por fuente

| Fuente | Permitido | No permitido |
| --- | --- | --- |
| Self canónico | nombre/tipo configurados exactos | identidad metafísica o identidad inventada |
| Estado operacional | energía/estrés/foco exactos | emoción o estado del mundo inferido |
| Percepción estructurada del evento actual | objeto acotado como observación del turno | convertir contexto previo en visión actual |
| Percepción previa/cacheada | mención histórica con incertidumbre | afirmar presencia o ausencia actual |
| Usuario actual | “me dijiste” con texto citado | elevar el enunciado a hecho independiente |
| Memoria agregada | “mi memoria agregada registra” con texto citado | procedencia por recuerdo, frescura o verdad del mundo |
| Oracle | resultado técnico atribuido según provenance | verdad confirmada por proveedor/modelo |
| Efecto material | resultado exacto sólo con attestation `VALID` y `action_id`/`effect_kind`/`object_id` correlacionados | solicitud, autorización o despacho como ejecución; `FAILED`/`NO_EFFECT`/`UNSUPPORTED` como éxito |

La semántica ROOT-018 permanece intacta. Resultado generado válido, recuperado de cache, parse degradado, legacy de productor desconocido y fallback local reciben atribuciones distintas. Un cache hit es previo, no llamada nueva; Q1 almacenada no se identifica con Q2 consultada. `provider` y `model_id` son descriptivos, jamás jerarquía de confianza.

## Límites deliberados

La percepción anterior de BehaviorCore no gana freshness: ROOT-029 sigue pendiente. El material sólo entra al compilador si el llamador proporciona simultáneamente attestation validada y correlación tipada; las rutas Persona actuales no inventan esa evidencia. No se añadieron DB, colección Chroma, creencias persistentes, segundo juez, cambio público API/Unity, ni AuditTrace v2. El parser legacy de `PersonaEngine` permanece para compatibilidad de tests/llamadores antiguos, pero las dos rutas productivas de Persona en Orchestrator ya no publican su texto libre. Una integración externa que invoque directamente ese parser fuera de Orchestrator debe migrar al boundary antes de exponer salida pública.

El único cambio de prompt es la instrucción de salida/schema para el nuevo `factual_bridge=True` y el bloque `persona_factual_index` como DATA. La instrucción de estilo, fuentes, jerarquía de confianza y `PERSONA_STYLE` no cambian. Se enumeran tipos de acto, templates sociales/cierre, display, style cues y verbosity; no hay tuning de obediencia, calidez, sarcasmo, rebeldía ni brevedad.

## Riesgo residual y revisión humana

Los templates v1 estrechan el repertorio expresivo. La exactitud semántica de texto libre de Memory/Oracle no se afirma: se atribuye literalmente. La fuente de percepción del evento sigue confiando en el contrato estructurado actual del canal y no resuelve la identidad/freshness general de ROOT-029. La revisión humana debe verificar especialmente que no exista otra ruta productiva que publique el parser legacy y que la merma de naturalidad sea aceptable hasta el reemplazo CFC.
