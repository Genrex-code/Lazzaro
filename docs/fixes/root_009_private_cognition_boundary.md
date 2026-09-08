# ROOT-009 — Private cognition boundary

## 1. ROOT

ROOT-009 corrige `API-CONTRACT-001`: pensamiento y justificacion internos cruzaban el contrato REST de acciones. El cierre offline esperado es `CLOSED_CODE / FIELD_PENDING` porque la validacion final requiere Unity real.

## 2. Causa

`ActionResponse` es deliberadamente rico y conserva `pensamiento` y `razon`. La proyeccion publica `_a_accion_saliente()` copiaba esos datos a `AccionSaliente.pensamiento_privado` y `AccionSaliente.razonamiento`. Unity recibia ambos, mostraba el pensamiento y podia escribirlo en su log. Ademas, el Orchestrator convertia `none + pensamiento` y `ActionResponse.error` en notificaciones publicas.

## 3. Ticket absorbido

Se absorbe exclusivamente `API-CONTRACT-001`. No se inicia versionado global, renderer publico, cambio de prompts, trust boundary, rediseño de memoria ni semantica de entrega de notificaciones.

## 4. Private cognition flow map before

| Producer | Dato | Adaptador publico anterior | Sink publico anterior |
|---|---|---|---|
| `LLMResponse` / `ResponseParser` | `pensamiento`, `razon` | `ActionResponse` -> `_a_accion_saliente()` | REST, Unity DTO; thought tambien UI/log |
| `PersonaEngine` | `pensamiento_privado`, `razon` | `_action_response_desde_persona()` -> `_a_accion_saliente()` | REST y Unity |
| `MotorAction` | `razon` | `_action_response_desde_motor()` -> `_a_accion_saliente()` | REST y Unity |
| Orchestrator fallbacks | `pensamiento`, `razon`, `error` | `_a_accion_saliente()` / `_agregar_notificacion_si_corresponde()` | REST, Unity y notifications |

Terminal recibia los campos dentro del JSON compartido, pero su consumer solo extraia `parametros.texto_a_comunicar`. Ledger y AuditTrace ya excluian cognition cruda.

## 5. Public surface map

| Superficie | Antes | Despues |
|---|---|---|
| `POST /evento` y `POST /comando` | Action thought/reason | Proyeccion operacional minima |
| `GET /estado` | Sin thought/reason | Sin cambio |
| `GET /constelacion` | Estado/tarea/percepcion, sin ActionResponse cognition | Sin cambio |
| `GET /notificaciones` | Podia entregar thought o raw internal error | Esos productores quedan suprimidos |
| Terminal | Ignoraba thought/reason recibidos | Ya no los recibe |
| Unity success/pending | DTO recibia ambos | DTO no los modela |
| Unity error | `PublicErrorEnvelope v1` seguro | Sin cambio |
| OpenAPI | Campos y ejemplos privados | Ausencia estructural |

## 6. Data classification policy

| Clase | Internal Python | REST/Terminal/Unity | AuditTrace | Human logs | Memory | ActionLedger |
|---|---|---|---|---|---|---|
| Private thought | Permitido | Prohibido | Prohibido crudo | Prohibido | Solo storage interno ya contractual | Prohibido |
| Internal reason | Permitido | Prohibido | Prohibido crudo | Prohibido | No se añade | Prohibido |
| Public speech | Permitido | Permitido | No necesario | Evitar contenido completo | Dialogo publico permitido | Permitido como parametro |
| Action identity/lifecycle | Permitido | Permitido | Permitido | Permitido acotado | Metadata interna | Permitido |
| Prompt/memory context | Permitido | Prohibido | Prohibido crudo | Solo metadata acotada | Permitido internamente | Prohibido |
| Public state | Permitido | Permitido por endpoints dedicados | Permitido | Acotado | Segun owner | No |

## 7. Thought semantics

`ActionResponse.pensamiento` es narrativa cognitiva interna. Puede proceder del modelo, Persona o un fallback determinista. No constituye texto dirigido al usuario, aunque parezca legible. ROOT-009 lo conserva en el objeto interno y corta todos sus adaptadores publicos automaticos.

## 8. Reason semantics

`ActionResponse.razon`, `LLMResponse.razon` y `MotorAction.razon` son justificacion o diagnostico interno. No son una explicacion publica y no autorizan exposicion por REST. Los productores y consumidores internos permanecen intactos.

## 9. Public speech semantics

La voz publica requiere `accion == "hablar"` y un `parametros["texto_a_comunicar"]` explicito. El texto sigue cruzando REST y siendo consumido por Terminal y Unity. Thought o reason nunca se usan como fallback de voz.

## 10. Python DTO before/after

Antes, `AccionSaliente` declaraba nueve campos e incluia `pensamiento_privado` y `razonamiento`. Despues conserva exclusivamente:

- `id_accion`
- `nombre`
- `parametros`
- `requiere_confirmacion`
- `estado_ejecucion`
- `id_transaccion_origen`
- `parametros_descripcion`

Se adoptó `extra="forbid"` solo en `AccionSaliente`. Los constructores legitimos y la regresion permanecen compatibles; payloads legacy con aliases privados se rechazan en vez de ignorarse. No se versionan otros DTO.

## 11. Orchestrator projection

`_a_accion_saliente()` sigue creando identidad y copiando nombre, parametros y confirmacion, pero ya no copia `ActionResponse.pensamiento` ni `ActionResponse.razon`. El objeto interno no se muta. Esta es una proyeccion publica minima, no una redaccion cosmetica.

## 12. Durable replay

`_serializar_accion_durable()` ya almacenaba solo campos operacionales. `_restaurar_accion_desde_ledger()` y `_accion_desde_record()` dejaron de reconstruir campos cognitivos vacios. La respuesta inicial, el cache same-ID y el replay tras restart tienen las mismas claves publicas, identidad, parametros y lifecycle. No hay cambio de schema ni migracion.

## 13. Unity DTO

`AccionSaliente` C# elimina estructuralmente `pensamiento_privado` y `razonamiento`. Conserva ID, nombre, parametros, confirmacion, estado e ID de transaccion de origen. No se usa `JsonIgnore` ni alias legacy.

## 14. Unity UI

`textoPensamientoUI` puede permanecer como componente visual general, pero ningun dato de la respuesta API lo alimenta. Se elimina el renderer `[Pensando...]`. La voz continua usando exclusivamente `texto_a_comunicar`.

## 15. Unity logs

Se elimina el log `Pensamiento de Lazzaro` derivado de la respuesta. `mostrarLogsCognitivos` permanece para diagnosticos locales no cognitivos, pero no concede autoridad para transportar cognition remota.

## 16. Terminal

No requiere cambios de produccion. `TerminalChatClient` valida `status`, exige `accion.nombre == "hablar"` y devuelve solo `parametros.texto_a_comunicar`. Las pruebas conservan un payload legacy hostil como control positivo y demuestran que thought/reason no se renderizan.

## 17. Notifications

Se eliminan los dos productores inseguros:

- `none + ActionResponse.pensamiento`
- `ActionResponse.error`

`_agregar_notificacion_si_corresponde()` no fabrica reemplazo y retorna `False`; el efecto UoW queda `SKIPPED`. La cola y su drain thread-safe permanecen disponibles para un futuro productor con public intent explicito. ROOT-055 sigue abierto: no se resuelve `empty` frente a `failure`.

## 18. Public state

`EstadoAgente` y `obtener_estado_publico()` no contienen thought, reason, prompt, memory context ni salida LLM. Continuan exponiendo el snapshot de estado, personalidad y tarea que ya era publico. No se modifica Identity owner.

## 19. Constellation

La implementacion actual expone nodos estructurados de estado, tarea, percepcion y policy. No transporta campos de `ActionResponse`, prompt, contexto de memoria ni raw LLM output. No se modifica Telescope ni la constelacion.

## 20. OpenAPI

El schema de `AccionSaliente` y los ejemplos anidados de `RespuestaAPI` ya no declaran los dos campos privados. `additionalProperties: false` refleja el rechazo de extras en el DTO de accion. `PublicErrorEnvelope v1` no cambia.

## 21. AuditTrace

AuditTrace v1 permanece congelado. Decision registra capa, nombre de accion y validez; errores registran tipo/componente; memoria registra IDs/scores/count. No se añade thought/reason, y los canarios privados permanecen ausentes.

## 22. ConversationMemory

`ConversationMemory` puede conservar `ConversationTurn.pensamiento` en RAM interna. Su `snapshot_publico()` proyecta solo usuario, respuesta publica y timestamp. ROOT-009 valida simultaneamente que el thought siga internamente y no aparezca en el snapshot.

## 23. Error boundary ROOT-010

`PublicErrorEnvelope v1`, su taxonomia, mensajes, correlation UUID y semantica HTTP permanecen sin cambios. Las pruebas ROOT-010 se ejecutan completas como regresion.

## 24. Future PublicExplanation boundary

Una futura explicacion publica debera ser un dato nuevo producido deliberadamente para usuario. `PublicExplanation` no sera alias ni copia de `InternalReason`. ROOT-009 no implementa renderer, chain-of-thought ni ROOT-017.

## 25. Field gate

`field_required=true`. No se ejecuta Unity Play Mode en este packet. La validacion posterior debe comprobar habla visible, accion fisica, pending authorization y ausencia de thought/reason en UI/log, ademas de `none + private thought -> no notification`.

## 26. Neighboring roots

Permanecen abiertos y no iniciados ROOT-039, ROOT-013, ROOT-014, ROOT-015, ROOT-016, ROOT-017, ROOT-019, ROOT-020, ROOT-038 y ROOT-055. Tampoco se toca el issue de objetos mutables ROOT-043.

## 27. Tests

Las pruebas focales cubren DTO/serialization, REST hablar/motor/pending, notifications, OpenAPI, Terminal, ConversationMemory, AuditTrace, public state/constellation, cache, restart replay, ActionLedger y compatibilidad ROOT-010. Un segundo modulo inspecciona estructuralmente DTO, consumer, UI, logs, speech, dispatch y authorization de Unity.

## 28. Canary matrix

| Sink | Thought | Reason | Prompt | Memory context |
|---|---:|---:|---:|---:|
| Internal `ActionResponse` | 1 | 1 | 1 metadata | 1 metadata |
| REST / Pydantic | 0 | 0 | 0 | 0 |
| OpenAPI | 0 fields/examples | 0 fields/examples | 0 | 0 |
| Unity DTO/UI/log | 0 | 0 | 0 | 0 |
| Terminal output | 0 | 0 | 0 | 0 |
| Public notifications | 0 | 0 | 0 | 0 |
| AuditTrace | 0 | 0 | 0 | 0 |
| ActionLedger / replay / cache | 0 | 0 | 0 | 0 |

Canarios: `PRIVATE_THOUGHT_CANARY_9A11`, `INTERNAL_REASON_CANARY_77BC`, `PROMPT_CANARY_40DD` y `MEMORY_CONTEXT_CANARY_31EF`.

## 29. Criterion

El code gate exige ausencia estructural, no valores vacios; conservación del objeto interno; voz publica funcional; identidad, parametros, authorization y lifecycle intactos; no notification privada; OpenAPI y Unity limpios; replay/cache equivalentes; regresiones historicas verdes; Chroma y runtime stores intactos.

## 30. Final state

Con focales y regresion completa verdes, ROOT-009 queda `CLOSED_CODE / FIELD_PENDING`. No hay field test, red real, LLM real, Unity Play Mode ni push. Memory rework, Capa 2, DevelopmentalCore, TCM, TEARS y Canonical Cognitive DB permanecen intactos.
