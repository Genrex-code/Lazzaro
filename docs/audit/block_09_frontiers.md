# Fase 2 — Bloque 9: fronteras API / terminal / Unity

**Fecha:** 2026-09-05

**Base:** `bcda3859d3d68932261dbefc58a77977da3fb829`

**Estado:** **CERRADO — AUDITORÍA COMPLETA / 0 P0 NUEVOS / 2 P1 Y 2 P2 NUEVOS OPEN.**

`PASS_REPRODUCED` significa que se caracterizó el comportamiento actual; no
certifica que sea seguro ni correcto. Todo el trabajo fue offline, con datos
sintéticos, TestClient ASGI en memoria, `httpx.MockTransport` y lectura estática
de C#. No se inició servidor, Unity ni Play Mode; no hubo red, LLM, Chroma activa,
ActionExecutor, movimiento, hardware ni fixes.

## 1. Resumen ejecutivo

La frontera no conserva un significado único de extremo a extremo. HTTP 200
significa que Python produjo un envelope, no que Unity haya empezado ni terminado
un efecto. Para clientes externos, Orchestrator clasifica `despachada` como
`execution.status=pending`; el resultado físico llega, si llega, en otro evento y
otro `id_transaccion`. Unity recibe `id_accion`, pero no lo conserva en
`resultado_accion`; tampoco retorna la transacción original. Por tanto el outcome
no puede reconciliarse inequívocamente con el comando.

La terminal sí mantiene un firewall efectivo: Orchestrator reconduce Motor a
Persona y el cliente acepta exclusivamente `status=ok`, `accion.nombre=hablar` y
texto no vacío. No ejecuta herramientas y no imprime razonamiento ni pensamiento
privado. Unity, en cambio, recibe ambos campos públicos y muestra/registra
`pensamiento_privado`.

Se reprodujeron las causas heredadas: pérdida `ActionResponse→AccionSaliente`,
`pensamiento_privado` público, estados pending ambiguos, correlación permisiva,
frescura perceptiva insuficiente y `NaN/±Inf→null` en HTTP 200. No se duplicaron
sus tickets. Se abren cuatro causas propias:

| ID | P | Causa raíz |
|---|---:|---|
| `UNITY-BOUNDARY-001` | P1 | cuatro handlers informan éxito sin materializar el efecto declarado |
| `UNITY-BOUNDARY-002` | P1 | `accion:null` se descarta sin cerrar la subtarea local |
| `TRANSPORT-AUDIT-001` | P2 | fallo de envío elimina el evento de cola, sin retry/backoff ni recibo |
| `API-BOUNDARY-001` | P2 | fallo interno de notificaciones se publica como HTTP 200 `[]` |

Siguen OPEN los P0 heredados `MOTOR-SAFETY-001` y
`MOTOR-CONTRACT-002`; el segundo amplía su evidencia con serialización HTTP real
en memoria: `NaN/±Inf` salió como `null` bajo `status=ok`.

## 2. Arquitectura de fronteras

```text
Terminal input ──HTTP JSON──┐
                            ├─> EventoEntrante ─> Orchestrator
Unity event ────HTTP JSON───┘                         │
                                                     ├─ AccionSaliente
                          RespuestaAPI/HTTP JSON <────┘
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                            ▼
          Terminal: solo hablar        Unity: switch de 10 acciones
                                                   │
                              percepción / dolor / resultado_accion
                                                   │
                              nuevo EventoEntrante y nueva transacción
```

| Flecha | Origen/serializer | Wire | Destino | Pérdida/coerción principal |
|---|---|---|---|---|
| terminal→API | dict + httpx | UTF-8 JSON | Pydantic `EventoEntrante` | timestamp omitido; detalles flexibles |
| Unity→API | Newtonsoft | UTF-8 JSON | Pydantic `EventoEntrante` | timestamp de captura inexistente; Any conserva tipos inesperados |
| Core→API | `ActionResponse`→Pydantic | objeto Python | `AccionSaliente` | valid/error/metadata/status/proveedor/capa se pierden |
| API→terminal | FastAPI/Pydantic | `RespuestaAPI` | dict httpx | terminal conserva solo texto de hablar |
| API→Unity | FastAPI/Pydantic | `RespuestaAPI` | Newtonsoft/C# fields | `parametros_descripcion` ignorado; varios campos no consumidos |
| Unity→Core outcome | dict C# | nuevo evento | detalles `Any` | no action ID ni transacción original; duración/frescura ausentes |

Fuentes: `api/schemas.py:13-157`, `api/routes.py:35-146`,
`terminal_client/chat.py:84-195`, `unity_client/ApiClient.cs:13-61`,
`unity_client/LazzaroAgent.cs:394-572,632-856`.

## 3. API input

### Endpoints productivos

| Método | Ruta | Entrada | Salida observada |
|---|---|---|---|
| POST | `/api/v1/evento` | `EventoEntrante` | `RespuestaAPI`; 200 o 500; validación 422 |
| POST | `/api/v1/comando` | `EventoEntrante` | fuerza `tipo_evento=comando_usuario` |
| GET | `/api/v1/estado` | — | `EstadoAgente` |
| GET | `/api/v1/notificaciones` | — | lista; excepción interna degrada a 200 `[]` |
| GET | `/api/v1/constelacion` | — | dict público |

### Matriz de campos de `EventoEntrante`

| Campo | Requerido/default | Null | Validación/uso real |
|---|---|---|---|
| `origen` | requerido | no | literal: roblox/unity/sandbox/terminal/otro; decide canal/ejecución |
| `tipo_evento` | requerido, min 1 | no | whitespace/desconocido pasan; consumidores deciden significado |
| `id_transaccion` | requerido, min 1 | no | trim y no-blank; no UUID ni máximo; cache/lock usan el string |
| `timestamp` | UTC naive al omitir | sí | parse datetime; no lector operativo del instante del cliente |
| `objeto_involucrado` | `"ninguno"` | no | contexto/memoria; sin vocabulario cerrado |
| `detalles` | `{}` | no | `Dict[str, Any]`; nested extras/tipos se conservan |

Pydantic ignora extras top-level. El probe observó: body vacío, JSON inválido,
origen desconocido, ID ausente/blanco y timestamp inválido → 422; tipo de evento
desconocido → 200; ID de 4096 caracteres → 200; `mensaje:null`, strings bool y
numéricos y nested desconocido cruzan sin coerción. `text/plain` con cuerpo JSON
dio 422. El parser JSON aceptó tokens `NaN/Infinity` dentro de `detalles`, que
llegaron como floats no finitos.

El mismo `id_transaccion` queda protegido solo dentro de Orchestrator: tras una
primera llamada completa, el segundo intento devuelve la acción cacheada. API por
sí sola no aporta ledger durable; los fallos pre-cache heredados siguen repetibles.

## 4. API output

`RespuestaAPI` tiene `status`, `mensaje`, `id_transaccion`, `accion` y
`timestamp_servidor`. `accion`, cuando existe, serializa exactamente los siete
campos Pydantic: `id_accion`, `nombre`, `parametros`, `razonamiento`,
`pensamiento_privado`, `requiere_confirmacion`, `parametros_descripcion`.

| Situación | HTTP | `status` | `accion` | Significado real |
|---|---:|---|---|---|
| Orchestrator devuelve acción | 200 | ok | objeto | decisión disponible; no outcome |
| Orchestrator devuelve `None` | 200 | ok | null | sin acción o fallo Behavior capturado; indistinguibles |
| fallback produce acción | 200 | ok | objeto | acción final sanitizada; causa interna no cruza |
| excepción escapa | 500 | error | null | mensaje incluye `str(exception)` |
| no Orchestrator | 503 | detail FastAPI | — | dependencia no lista |

La frontera pierde `ActionResponse.valido`, `error`, `metadata`, status Motor,
capa, provider/model y estado de ejecución. `requiere_confirmacion` sí cruza como
bool. `None` cruza como JSON `null`. En el TestClient real, parámetros
`NaN/+Inf/-Inf` produjeron HTTP 200 con `x/y/z:null`; no hubo error de
serialización. `status=pendiente` existe en schema, pero las rutas no lo emiten.

Un fallo Motor/Persona/Oracle solo se ve fuera si termina en `None`, acción final
o excepción HTTP; el envelope no preserva su procedencia. `none` es un objeto de
acción distinto de `accion:null`. Esta pérdida amplía `MOTOR-CONTRACT-001` y
`ORCH-AUDIT-003/004`.

## 5. Terminal

La terminal real usa HTTP; no invoca Orchestrator directamente. Cada mensaje
genera UUID4 salvo ID explícito y envía origen terminal, comando_usuario,
objeto `ninguno`, mensaje, `solicita_decision=true` y
`modo_conversacion=true`; omite timestamp. Mantiene solo la sesión HTTP y el REPL,
no estado cognitivo local.

| Respuesta | Conducta terminal |
|---|---|
| `ok + hablar + texto` | muestra únicamente texto sanitizado |
| acción Motor/desconocida/none | `TerminalChatError`; no ejecuta |
| `accion:null` | error “no devolvió respuesta hablada” |
| `pendiente`/`error` | error con mensaje de dominio |
| JSON malformed/no-dict | error de contrato |
| 4xx | detalle sanitizado y truncado |
| 5xx | cuerpo oculto; “error interno” o 503 genérico |
| timeout/conexión | mensaje genérico; sin retry automático |

El doble firewall es real: `motor→persona` en
`_adaptar_triage_para_terminal` conserva `terminal_original_route`, y el cliente
solo acepta `hablar`. `razonamiento` y `pensamiento_privado` no se imprimen. Una
frase como “hecho” podría existir en la narrativa del modelo, pero no constituye
certificación de outcome; la terminal no materializa acciones físicas.
`test_chat.py` usa fakes/TestClient y prueba Orchestrator aislado; no es el
transporte HTTP productivo del REPL.

## 6. Python → Unity

### Matriz DTO exacta

| DTO/campo | Python | C# Unity | Paridad/uso |
|---|---|---|---|
| Evento.`origen` | Literal requerido | string=`"unity"` | wire coincide; C# no valida enum |
| Evento.`tipo_evento` | string requerido | string null por default | wire coincide |
| Evento.`id_transaccion` | string requerido | string null por default | Unity genera GUID |
| Evento.`timestamp` | datetime|null/default | **ausente** | pérdida de hora de captura |
| Evento.`objeto_involucrado` | string=`ninguno` | string=`ninguno` | coincide |
| Evento.`detalles` | dict=`{}` | Dictionary null | nullability/default difiere |
| Acción.`id_accion` | string requerido | string null | recibido, no usado para outcome |
| Acción.`nombre` | string requerido | string null | switch exacto |
| Acción.`parametros` | dict=`{}` | `JObject` null | flexible, validación ad hoc |
| Acción.`razonamiento` | string=`""` | string null | recibido, no usado |
| Acción.`pensamiento_privado` | string=`""` | string null | UI/log público Unity |
| Acción.`requiere_confirmacion` | bool=false | bool=false | true omite y reporta fallo |
| Acción.`parametros_descripcion` | lista=`[]` | **ausente** | Newtonsoft lo ignora |
| Respuesta.`status` | ok/error/pendiente | string null | solo `error` se distingue |
| Respuesta.`mensaje` | string | string null | usado en error lógico |
| Respuesta.`id_transaccion` | string|null | string null | deserializado, no consumido |
| Respuesta.`timestamp_servidor` | datetime | string null | deserializado, no consumido |
| Respuesta.`accion` | Acción|null | referencia null | null retorna sin transición |

## 7. Unity serialization

Unity usa Newtonsoft `JsonConvert.SerializeObject/DeserializeObject`, nombres
snake_case explícitos, UTF-8 y `Content-Type: application/json`. No hay naming
strategy declarada. Por defaults de Newtonsoft, la lectura de miembros es
case-insensitive y campos desconocidos se ignoran; esto es **INFERIDO por contrato
de librería**, no compilado en este bloque. Los campos de referencia ausentes
quedan null y bool ausente queda false.

`parametros` permanece como `JObject`: null/missing/lista/tipo erróneo no tiene una
política común, sino que cada handler usa `ToString`, `Value<T>` o
`TryLeerFloat`. Navegación a posición exige que x/y/z existan y se puedan parsear
antes de construir `Vector3`, por lo que el `null` HTTP reproducido no se convierte
silenciosamente en origen para esa acción. Otros handlers sí aplican defaults 0,
2 o true. `TryLeerFloat` no verifica `IsFinite`; la conducta exacta de tokens C#
NaN/Infinity quedó **INFERIDA/no compilada**, mientras la transformación Python
no-finito→JSON null sí quedó OBSERVADA.

## 8. Unity consumers

| Acción | Selección/validación Unity | Efecto observado por lectura |
|---|---|---|
| `navegar_a_objeto` | target no blanco | `MovementController.NavegarAObjeto`→NavMesh |
| `navegar_a_posicion` | x/y/z parseables | `Vector3`→MovementController→NavMesh |
| `alejarse_de_objeto` | target; distancia default 2 | MovementController |
| `girar_avatar` | grados parseable, clamp ±360, abs≥1 | MovementController |
| `cancelar_tarea` | bools/defaults | frena movimiento/cancela cola local |
| `interactuar_objeto_3d` | objeto cercano; tipo default mirar | log + outcome success; sin interacción material |
| `agarrar_objeto` | objeto cercano | log + outcome success; sin agarre material |
| `soltar_objeto` | fuerza default 0 | log + outcome success; sin objeto/materialización |
| `aplicar_fuerza` | defaults 0 | log + outcome success; sin `AddForce` |
| `hablar` | texto default `...` | log/UI y completa tarea; no envía outcome |
| desconocida | default switch | outcome failure |
| `none`/nombre vacío | guard previo | completa tarea como fallo |

Unity revalida algunos targets/proximidad y formatos, pero no conoce
`valido=False`, status Motor, procedencia del target ni affordance/riesgo que
motivó la decisión. Su allowlist evita ejecutar nombres arbitrarios, pero no
garantiza equivalencia semántica de parámetros ni veracidad del outcome.

## 9. Unity → Python

`EnviarEvento` siempre crea un GUID nuevo, conserva origen default `unity`, no
incluye timestamp y encola el dict. `resultado_accion` toma una percepción nueva y
añade `accion`, `exito` bool y `mensaje_resultado`; opcionalmente parámetros del
movimiento y `movimiento_finalizado`. `objeto_involucrado` suele ser el nombre
local. No añade `id_accion`, transacción original, duración, error tipado ni hora.

Pydantic conserva esos campos dentro de `detalles: Any`; success/failure bool
llegaron intactos en el probe. La pérdida ocurre en el productor Unity y después
en consumidores informales, no en deserialización Pydantic.

## 10. `resultado_accion`

| Campo | Enviado | Calidad correlacional |
|---|---|---|
| nueva `id_transaccion` | sí | identifica solo el evento de resultado |
| `accion` | sí | nombre, no instancia |
| `exito` | sí, bool | tipo correcto en productor actual |
| `mensaje_resultado` | sí | texto libre |
| target | a veces como objeto/param extra | nombre mutable/no obligatorio |
| `id_accion` original | no | pérdida decisiva |
| transacción del comando | no | pérdida decisiva |
| duración/timestamp | no | no detecta lateness/reorder |
| posición final/percepción | snapshot actual | sin timestamp/frescura verificable |
| collision/pain | evento separado | no outcome canónico de la acción |

El fallback de alias busca primero action ID si algún cliente lo aporta y luego
nombre+target. El probe reprodujo que, tras consumir `action-old` por ID, un
duplicado del mismo resultado puede caer por fallback sobre `action-current`.
Esto amplía `EXEC-AUDIT-001`, no crea otro ticket.

## 11. `success` / `exito`

| Señal | Quién la emite | Lo que significa | No significa |
|---|---|---|---|
| HTTP 200 | API | envelope serializado | dispatch ni ejecución |
| `RespuestaAPI.status=ok` | API | Orchestrator retornó acción o None | acción válida/outcome |
| Audit `execution=pending` | Orchestrator | confirmación pendiente o despacho lógico | efecto iniciado/terminado |
| Unity `onSuccess` | ApiClient | HTTP success + parse + status distinto de error | status conocido ni efecto |
| `detalles.exito` | handler Unity | creencia local del handler | correlación inequívoca |
| terminal display | Terminal | hablar válido recibido | hecho externo cumplido |

Para navegar, el bool llega del callback de MovementController. Para
interactuar/agarrar/soltar/aplicar fuerza, hoy significa que el handler pasó sus
checks y emitió un log, no que el efecto declarado ocurrió. Para hablar no existe
resultado de vuelta; el backend queda con pending.

## 12. Perception

`PerceptionSystem` captura colliders interactuables en radio/FOV, los ordena solo
por distancia y limita a ocho. El wire incluye `snapshot_id`, source unity,
resumen/lista textual, objetos normalizados, conteo, affordances desconocidas,
posición de Lazzaro y objeto más cercano.

Cada objeto contiene id, display_name, nombre runtime, runtime_instance_id,
aliases, posición, distance/distancia, affordances, source y confidence. Metadata
ausente cae a nombre del collider, aliases mínimos, affordances vacías y confidence
1. Objetos null se omiten. Empates de distancia no tienen desempate estable.

`snapshot_id` concatena posición redondeada, nombres y distancias: identifica
contenido aproximado, no una captura/generación. No hay timestamp, frame, edad ni
secuencia. Dos capturas iguales comparten ID; estados distintos que colisionen por
redondeo pueden compartirlo. Python conserva source y normaliza estructuras
parciales, pero no puede probar frescura. Esto amplía `MOTOR-CONTRACT-006/007` y
`DTO-CONTRACT-003`.

## 13. Dolor / sensorial

`SomatoSensorySystem.OnCollisionEnter` filtra ruido ambiental, umbral 0.5 y
cooldown 5 s, frena movimiento y emite `alerta_dolor_fisico`. El evento contiene
percepción, mensaje crítico, `estado_fisico=bloqueado`, dirección/distancia y
velocidad de escape, acción fallida y `exito=false`. `impacto` contiene tipo
contundente, objeto, velocidad relativa redondeada, dolor normalizado 0..1,
riesgo bajo/medio/alto, dirección y número de contactos.

No cruza timestamp de captura, frame, punto exacto ni parte corporal; source vive
en la percepción. Python conserva el nested dict, pero StateEngine consume
principalmente el tipo de evento como flag temporal y sus deltas; Orchestrator usa
estado/dirección/distancia/acción para interrupción, memoria y escape. La física
detallada no se convierte en un contrato tipado ni en correlación de outcome.

## 14. Correlation IDs

| ID | Creación | Ida/vuelta | Uso real |
|---|---|---|---|
| `id_transaccion` comando | terminal UUID/caller o Unity GUID | API lo devuelve | lock/cache RAM; Unity no lo conserva |
| `id_accion` | Orchestrator UUID | llega en AccionSaliente | pending alias interno; Unity no lo retorna |
| ID transacción outcome | Unity GUID nuevo | identifica nuevo evento | no enlaza comando |
| `trace_id` | Orchestrator por invocación | no cruza API | correlaciona stages del único procesamiento |
| `snapshot_id` | Unity por contenido redondeado | dentro de detalles | identidad aproximada, no generación |
| object ID | metadata o nombre fallback | percepción | no siempre retorna como target estable |

Un cache hit del mismo ID crea otro `trace_id` y solo registra
`event_start→event_end`; el probe lo reprodujo. El outcome posterior no actualiza
el trace original. Sin ledger, abrir un único ID no permite reconstruir toda la
historia decisión→efecto.

## 15. Timestamps

| Campo/reloj | Creador | UTC/zona/unidad | Uso |
|---|---|---|---|
| `EventoEntrante.timestamp` | Pydantic si omitido | datetime UTC naive, ISO JSON | declarado; no orden/freshness |
| captura Unity | — | ausente | no existe |
| `timestamp_servidor` | RespuestaAPI | UTC naive, ISO string en Unity | deserializado, no leído |
| AuditTrace timestamp | writer/Core | UTC serializado | orden interno por trace |
| `Time.time` dolor | Unity | segundos desde inicio local | cooldown local; no cruza |
| duración movimiento | — | ausente | no cruza |

Fechas inválidas dan 422, offset-aware se parsean, y null explícito se conserva.
Ninguno de estos campos permite detectar outcome/percepción stale ni ordenar de
forma fiable eventos entre procesos.

## 16. HTTP / retries

Unity hace request/response por coroutine, timeout clamp 15–180 s (default 90),
sin retry, backoff ni cancelación explícita. Un 2xx se deserializa; solo
`status==error` falla. `pendiente`, null o status desconocido entran a `onSuccess`.
En 4xx/5xx/timeout/conexión, callback failure recibe error/código y el body entero;
LazzaroAgent solo registra warning y el evento ya fue retirado de la cola.

Terminal usa timeout 300 s, sin retry automático. Sanitiza 4xx y oculta cuerpo
5xx. Cada mensaje normal crea un ID nuevo; un caller puede reenviar manualmente el
mismo ID. Si un timeout ocurre después de procesar el servidor, Unity no reintenta
por sí sola; un retry externo con el mismo ID puede obtener cache tras completion,
pero no está protegido de causas pre-cache/reinicio y Unity no implementa
preservación de ID para retry.

## 17. State machines

### A. Comando

```text
CREATED [sí: cliente]
  → SENT [sí: request iniciado]
  → ACCEPTED [parcial: validación/API; no recibo durable]
  → DISPATCHED [sí lógico: respuesta + Audit pending]
  → EXECUTING [solo estado local de algunos handlers; no cruza]
  → SUCCEEDED/FAILED [evento nuevo y no correlacionado inequívocamente]
```

No hay máquina canónica. `accion:null` ni siquiera cierra siempre la subtarea
Unity; hablar la cierra localmente sin outcome; cuatro handlers generan success
sin efecto material; fallo de transporte puede terminar solo en log.

### B. Percepción

```text
CAPTURED [sí, Unity]
  → SERIALIZED [sí, Newtonsoft]
  → RECEIVED [sí, API/Pydantic]
  → NORMALIZED [sí, Context/Motor]
  → CONSUMED [sí, decisión/memoria]
```

La identidad temporal se pierde en CAPTURED: no existen timestamp/frame/generation.
Serialización y normalización conservan un snapshot ID derivado, pero no pueden
demostrar que el dato sea nuevo.

## 18. Roundtrips sintéticos

| Caso | Resultado offline |
|---|---|
| A terminal social | input→Evento→API→hablar→solo texto público |
| B terminal Motor | adaptación motor→persona; motor recibido se bloquea en cliente |
| C Unity hablar | DTO selecciona hablar; UI/log; no outcome al backend |
| D Unity navegación | selección llega a MovementController; no se invocó |
| E clarification | llega como hablar final; metadata/estado de aclaración se pierde |
| F invalid | Unity solo sabe acción final/null; `valido` no cruza |
| G/H result success/failure | bool y detalles llegan a Pydantic bajo nueva transacción |
| I late | sin tiempo/action ID, no detectable de forma fiable |
| J duplicate | duplicado viejo consumió candidato actual por fallback heredado |
| K unknown | API acepta; no existe ledger que lo clasifique canónicamente |
| L perception | campos/ID por contenido cruzan; no frescura temporal |
| M private thought | REST y Unity públicos; terminal lo descarta |
| N NaN/Inf | AccionSaliente→HTTP 200→JSON null; sin consumer físico |

## 19. Matriz de pérdida semántica

| Concepto interno | Python | JSON | Cliente | Vuelve a Python | Pérdida |
|---|---|---|---|---|---|
| valid | `ActionResponse.valido` | ausente | desconocido | — | total |
| error | `ActionResponse.error` | ausente salvo excepción envelope | genérico/body | texto libre outcome | tipo/procedencia |
| Motor status | metadata | ausente | acción final | — | total |
| confirmation | bool salida | bool | Unity omite; terminal bloquea | fallo nuevo sin ID | concesión/estado |
| pending | execution interno | no en ruta actual | Unity trataría como success | — | significado |
| execution outcome | estado futuro | evento separado | creencia handler | bool flexible | vínculo canónico |
| target provenance | resolution metadata | solo target final | nombre | a veces nombre | original/sustitución |
| source/provider | metadata/LLM audit | ausente | desconocido | source evento nuevo | provider/capa |
| private thought | string | público | Unity UI/log; terminal oculta | no vuelve | privacidad |
| reason | string | público | Unity no usa; terminal oculta | no vuelve | consumo |
| correlation | transaction+action+trace | transaction/action | Unity ignora ambos | nueva transaction | cadena |
| timestamp | datetime | ISO | string no usado | nuevo default servidor | captura/orden |
| perception freshness | sin campo | sin campo | snapshot derivado | sin edad | total |
| NaN/nonfinite | float | null | JObject null | puede fallar/default | significado numérico |
| memory health | interno | ausente | desconocido | — | total |

## 20. Failure matrix

| Boundary | Failure | Captura | Sender cree | Receiver cree | Retry/duplicado | AuditTrace | Usuario |
|---|---|---|---|---|---|---|---|
| JSON→API | malformed/empty/enum | FastAPI 422 | rechazado | no Evento | manual; no side effect | no trace | 422 |
| JSON→API | nested unknown/NaN | no rechazo | aceptado | valor Any | posible | trace parcial semántico | 200 posible |
| API→Core | Behavior retorna None | Core captura | procesado | acción null | retry pre-cache | error/fin | 200 ok |
| Core→API | excepción | ruta captura | fallo | 500 | retry puede repetir | error | detalle interno REST |
| Core→JSON | nonfinite params | serializer a null | acción ok | null param | no automático | decisión/pending | 200 ok |
| API→terminal | motor/null/pending | cliente | backend ok/pending | contrato inválido | no automático | no error cliente | error legible |
| API→Unity | status pending/unknown | no | backend estado distinto | success callback | no automático | no error cliente | normalmente log/UI |
| API→Unity | malformed/null | parse/null guard | respuesta enviada | failure o retorno | no retry | no enlace | warning o silencio |
| Unity consumer | acción unknown | default | comando válido | outcome failure | evento nuevo | trace nuevo si llega | warning |
| Unity consumer | consumer exception | no catch global | comando entregado | coroutine puede abortar | ninguno | no outcome | log Unity |
| Unity→API | timeout/connect/4xx/5xx | callback | evento intentado | nunca recibido o incierto | sin retry; drop | no trace o trace desligado | warning |
| Unity→Core | duplicate/out-of-order | API acepta | outcome enviado | correlación permisiva | puede aplicar a actual | trace nuevo | no señal fiable |
| notifications | excepción interna | endpoint captura | fallo | lista vacía sana | polling repite | log, no trace evento | 200 `[]` |

## 21. Privacidad

| Dato | Internal only | REST public | Terminal public | Unity public | Log only/not exposed |
|---|---:|---:|---:|---:|---|
| `pensamiento_privado` | no | **sí** | no | **sí, UI+log** | también log Unity |
| `razonamiento` | no | **sí** | no | recibido, no mostrado | no uso actual |
| Action metadata | sí | no | no | no | Audit resumido parcial |
| memoria/contexto completos | sí | no | no | no | no deben cruzar |
| provider/model | Audit interno | no | no | no | Audit/log según ruta |
| prompt | interno | no nominalmente | no | no | logs dependen de subsistema |
| exception `str(e)` | debería ser interno | **sí en 500** | oculto por cliente | **body completo en failure log** | logger servidor |
| stack | logger servidor | no en JSON nominal | no | no salvo texto en exception | sí, servidor |

Los marcadores sintéticos confirmaron `pensamiento_privado` y razón en REST;
terminal mostró solo texto público. Un marcador de excepción cruzó REST y el body
completo sería incluido en error Unity. Amplía `API-CONTRACT-001/002` y
`DTO-CONTRACT-001`.

## 22. AuditTrace

AuditTrace conoce el procesamiento del Core, no el transporte posterior. Registra
`execution.pending` tanto para confirmación como para `despachada`; esto significa
decisión no finalizada, no execution success. No contiene HTTP status, parse C#,
cola Unity ni outcome del handler. Un resultado posterior crea su propio trace y
no actualiza el original. Unity no recibe `trace_id`.

`id_transaccion` agrupa stages de una invocación y el cache, pero Unity rompe la
cadena al crear otro para resultado. Cache hit/retry produce un trace nuevo
event_start/end sin triage. HTTP/client errors posteriores al 200 no quedan ligados
al trace original. AuditTrace v1 permaneció congelado.

## 23. Candidatos históricos

| Candidato | Resultado Bloque 9 | Dueño |
|---|---|---|
| timestamp declarado/no usado | confirmado | `DTO-CONTRACT-003` |
| paridad DTO Python/Unity | parcial; dos campos faltantes y defaults distintos | `DTO-CONTRACT-002/003` |
| bool/string success | productor Unity bool; API Any conserva strings de otros | `TOOL-CONTRACT-001`, `MEM-CONTRACT-002` |
| confirmation | true bloquea Core y Unity; Executor directo difiere | `TOOL-CONTRACT-002` |
| pensamiento privado | REST/Unity público; terminal oculto | `API-CONTRACT-001` |
| NaN→null | confirmado en HTTP 200 real in-memory | `MOTOR-CONTRACT-002` |
| despachada≠success | confirmado: Audit pending | `EXEC-AUDIT-002` |
| outcome Unity correlation | no action/original transaction ID | `DTO-CONTRACT-003`, `EXEC-AUDIT-001/002`, `TASK-AUDIT-001` |
| perception freshness | no timestamp/frame/generation | `MOTOR-CONTRACT-006/007`, `DTO-CONTRACT-003` |
| route terminal | original motor; effective persona | OBS-004/contrato ya cerrado |

Descartados como ticket nuevo: “null→(0,0,0) en navegación” (x/y/z se verifican
antes de Vector3); “Unity ejecuta nombre arbitrario” (switch allowlist); “retry
automático duplica” (Unity/terminal no tienen retry automático); “pending hoy sale
por API” (schema lo admite, rutas actuales no lo producen); y “NaN/null crea un P0
distinto” (misma raíz heredada y no se ejecutó cliente).

## 24. Tickets

Todos **OPEN**; solo recomendaciones, sin fixes.

### `UNITY-BOUNDARY-001` — P1 — outcome físico falso

- **Procedencia/clase:** nuevo Bloque 9; success cambia significado.
- **Evidencia:** `EjecutarInteraccion`, `EjecutarAgarrar`, `EjecutarSoltar` y
  `EjecutarAplicarFuerza` llaman `ReportarResultadoAccion(..., true, ...)` tras
  checks/log; sus cuerpos no materializan interacción, agarre, liberación ni fuerza.
- **Ubicación:** `unity_client/LazzaroAgent.cs:782-846`; contraste
  `MovementController.cs` para movimiento real.
- **Reproducción:** grupo estático
  `unity_four_nonmovement_handlers_report_success_without_material_effect`.
- **Impacto:** Core/tarea/memoria/aprendizaje pueden aceptar una afirmación de
  éxito que el mundo no realizó. No se ejecutó Unity ni se demuestra daño físico.
- **Recomendación preliminar:** success solo desde confirmación observable del
  componente que materializa el efecto; mientras no exista, resultado explícito
  unsupported/failed.

### `UNITY-BOUNDARY-002` — P1 — subtarea sin transición

- **Procedencia/clase:** nuevo Bloque 9; liveness/estado divergente.
- **Evidencia:** `ProcesarRespuestaDelCerebro` retorna inmediatamente para
  `respuesta==null || respuesta.accion==null`; no llama
  `CompletarTareaUsuarioActual`, no outcome y no timeout de subtarea.
- **Ubicación:** `unity_client/LazzaroAgent.cs:487-492`.
- **Reproducción:** lectura verificada por
  `unity_null_action_returns_without_task_completion`; API produce 200/action null.
- **Impacto:** backend cree turno finalizado sin acción y Unity puede conservar la
  tarea actual indefinidamente; futuros resultados/comandos se interpretan sobre
  estado local divergente.
- **Recomendación preliminar:** definir transición terminal explícita para no-op,
  fallo y acción ausente, diferenciando fallo interno de no-action deliberado.

### `TRANSPORT-AUDIT-001` — P2 — evento perdido tras fallo HTTP

- **Procedencia/clase:** nuevo Bloque 9; retry/receipt frágil.
- **Evidencia:** `ProcesarColaEventos` elimina primero el evento y el callback de
  fallo solo hace warning; ApiClient tiene timeout pero no retry/backoff.
- **Ubicación:** `unity_client/LazzaroAgent.cs:468-485`,
  `unity_client/ApiClient.cs:13-61`.
- **Reproducción:** lectura estática verificada por
  `unity_has_timeout_but_no_retry_or_backoff`; no se hizo red.
- **Impacto:** percepción, comando u outcome pueden perderse; si el servidor sí
  procesó antes del timeout, el estado queda incierto. No existe duplicación por
  retry automático actual.
- **Recomendación preliminar:** recibo/estado explícito, retry acotado con el mismo
  ID y reconciliación durable antes de retirar definitivamente de cola.

### `API-BOUNDARY-001` — P2 — error de notificaciones oculto

- **Procedencia/clase:** nuevo Bloque 9; error/no-hit indistinguibles.
- **Evidencia:** excepción sintética de `obtener_notificaciones` produjo HTTP 200
  con `[]`.
- **Ubicación:** `api/routes.py:99-115`.
- **Reproducción:** `notification_failure_degrades_to_200_empty`.
- **Impacto:** un cliente no distingue cola sana vacía de fallo interno; puede
  perder alertas sin señal de transporte.
- **Recomendación preliminar:** contrato de error observable separado de lista
  vacía, sin divulgar detalle interno.

### Causas raíz de frontera para consolidación

1. Outcome no prueba materialización (`UNITY-BOUNDARY-001`).
2. Estados terminales no son compartidos (`UNITY-BOUNDARY-002`, pending/status).
3. Identidad/correlación se corta entre comando, acción y resultado.
4. Transporte carece de recibo/retry reconciliable (`TRANSPORT-AUDIT-001`).
5. Errores y ausencia se colapsan (`API-BOUNDARY-001`, action null/None).
6. DTOs duplicados eliminan metadata operativa y privacidad.
7. Percepción no porta identidad temporal verificable.

### Heredados ampliados

`MOTOR-CONTRACT-002`, `MOTOR-CONTRACT-001`, `API-CONTRACT-001/002`,
`DTO-CONTRACT-001/002/003/004`, `TOOL-CONTRACT-001/002`, `ORCH-AUDIT-001/003/004`,
`EXEC-AUDIT-001/002`, `TASK-AUDIT-001`, `MOTOR-CONTRACT-006/007`.

### Duplicados administrativos a agrupar en la próxima fase

- correlación/lifecycle: `DTO-CONTRACT-003` + `EXEC-AUDIT-001/002` +
  `TASK-AUDIT-001`;
- no-finito/serialización: `MOTOR-CONTRACT-002` y sus manifestaciones de frontera;
- error público: `API-CONTRACT-002` + `DTO-CONTRACT-001`;
- private thought: `API-CONTRACT-001` + hallazgos Capa 2;
- pending/confirmation: `DTO-CONTRACT-004` + `TOOL-CONTRACT-002` +
  `EXEC-AUDIT-002`;
- freshness/ownership: `DTO-CONTRACT-003` + `MOTOR-CONTRACT-006/007`.

### Invariantes antes de prueba de campo

1. Una acción bloqueada/inválida nunca llega a un consumer ejecutable.
2. Todo parámetro físico es finito, acotado y conserva unidad/significado en wire.
3. `id_transaccion`→`id_accion`→outcome forma una cadena única y durable.
4. Duplicado, tardío, desconocido y reordered se clasifican sin tocar tarea actual.
5. `pending`, dispatch, executing, success y failure son estados distintos.
6. Success solo se emite después de observar el efecto material correspondiente.
7. No-op, fallo interno y action null son resultados distinguibles y terminales.
8. Retry conserva ID y no duplica side effects.
9. Percepción porta generation/timestamp/frescura suficiente y object IDs estables.
10. `pensamiento_privado`, memoria, prompt y errores internos no cruzan público.
11. Terminal sigue siendo solo-conversación incluso ante DTO malformed.
12. Audit/ledger permiten reconstruir una acción con un único identificador.

## 25. Pruebas y regresión

Probe principal: `python -B docs/audit/probes/frontiers_deep_probe.py`.
Resultado final: **PASS_REPRODUCED, 65/65 controles**. Incluye API in-memory,
terminal MockTransport, cache/trace aislado, correlación sintética, DTO C# por
lectura, dispatcher, percepción, dolor, hashes y barreras.

Gate final: regression probe **160 PASS + 1 KNOWN_XFAIL histórico + 0
inesperados** (25 módulos/161 unidades; suite e isolation PASS); `compileall`
PASS; los 6 JSON de evidence válidos; `git diff --check` PASS; hashes protegidos
y Chroma iguales a los capturados por el probe.

## 26. Git / gate

Archivos autorizados del bloque: este informe, el probe y
`docs/audit/evidence/block_09_frontiers.json`. No se modificó código funcional ni
AuditTrace v1. Los archivos runtime Chroma y `constelacion.html` ya estaban dirty
antes del bloque y quedan excluidos del commit.

El commit incluye únicamente estos tres archivos. Permanecen fuera tres archivos
runtime Chroma modificados y `constelacion.html` untracked. Commit final: **se
completa al cierre administrativo del bloque**.

## Anexo A — Respuestas a las 204 preguntas

Las respuestas `OBS` fueron ejecutadas en el probe; `L` son lectura de código;
`INF` es conducta de librería/cliente no compilada. Las secciones 3–22 contienen
las matrices y el contexto completo.

### API: entrada, salida y privacidad (1–53)

| Nº | Respuesta |
|---:|---|
| 1 | Cinco: POST evento/comando y GET estado/notificaciones/constelación. OBS |
| 2 | `/api/v1/evento`; `/comando` acepta el mismo DTO y cambia el tipo. L/OBS |
| 3 | POST. OBS |
| 4 | OpenAPI declara `application/json`; `text/plain` dio 422. OBS |
| 5 | Extras top-level se ignoran; `detalles` conserva campos desconocidos. OBS |
| 6 | origen, tipo_evento e id_transaccion. L |
| 7 | timestamp UTC naive ahora, objeto `ninguno`, detalles `{}`. OBS |
| 8 | 422. OBS |
| 9 | 422. OBS |
| 10 | Pasa schema/API; consumidores deciden y pueden no reconocerlo. OBS |
| 11 | 422 salvo los cinco literals. OBS |
| 12 | 422. OBS |
| 13 | API lo entrega; Orchestrator cachea tras completion y serializa same-ID concurrente. OBS/heredado |
| 14 | vacío/whitespace da 422; se aplica trim. OBS |
| 15 | 4096 caracteres fueron aceptados; no hay máximo. OBS |
| 16 | se crea datetime UTC naive al validar. OBS |
| 17 | 422. OBS |
| 18 | no ordena ni valida freshness; nominal/telemetría. L |
| 19 | `detalles.mensaje=null` cruza porque detalles es Any. OBS |
| 20 | mensaje vacío cruza; terminal evita enviarlo. OBS/L |
| 21 | dentro de detalles quedan strings; no coerción Pydantic. OBS |
| 22 | dentro de detalles quedan strings. OBS |
| 23 | nested dict desconocido se conserva. OBS |
| 24 | Sí: parser aceptó NaN/±Infinity dentro de detalles. OBS |
| 25 | 422. OBS |
| 26 | 500 con envelope error y `str(e)`. OBS |
| 27 | 200/status ok con la acción final o null. OBS |
| 28 | `api.schemas.AccionSaliente`, dentro de `RespuestaAPI`. L |
| 29 | Sus siete campos: ID, nombre, parámetros, razón, pensamiento, confirmación y descripciones. OBS |
| 30 | valid, error, metadata, Motor status, capa, provider/model y execution state. L |
| 31 | Sí, JSON null. OBS |
| 32 | Pydantic/FastAPI emitió null en parámetros bajo HTTP 200. OBS |
| 33 | En el caso no-finito ensayado hubo sanitización a null, no 500. OBS |
| 34 | No. L |
| 35 | No como campo de acción; solo excepción puede aparecer en mensaje 500. L/OBS |
| 36 | No. L |
| 37 | No. L |
| 38 | No provider/source de decisión; origen queda solo en request. L |
| 39 | No. `execution.pending` solo queda en AuditTrace/WM. L |
| 40 | Schema permite status pendiente, pero rutas no lo emiten; confirmación va en acción. L |
| 41 | `accion:null` o objeto `nombre=none`; no son equivalentes. OBS/L |
| 42 | bool `requiere_confirmacion` dentro de acción. OBS |
| 43 | como none/null/fallback/500 según dónde falle; status Motor no cruza. L |
| 44 | como hablar final/fallback/500; causa Persona no cruza. L |
| 45 | como acción final/fallback/500; source/cache Oracle no cruza. L |
| 46 | Sí, figura en schema/OpenAPI de AccionSaliente. L |
| 47 | Cuando `accion` existe, se serializa siempre, aunque sea string vacío; con acción null no. OBS |
| 48 | El string no tiene filtro de contenido; podría incorporarlo, no se usó dato real. L |
| 49 | `razonamiento` cruza REST. OBS |
| 50 | Metadata interna puede contener datos privados, pero el campo no cruza; derivados sí pueden entrar en prosa/params. L |
| 51 | Sí, `str(exception)` llega al mensaje REST 500. OBS |
| 52 | Mensaje sí; stack no se incluye nominalmente en JSON. OBS/L |
| 53 | Servidor registra excepción; Unity incorpora body completo de no-2xx en failure log. Terminal lo oculta. L |

### Terminal (54–79)

| Nº | Respuesta |
|---:|---|
| 54 | Construye dict con origen terminal, comando, ID, objeto ninguno y tres detalles. OBS |
| 55 | UUID4 por turno, salvo ID explícito. L/OBS |
| 56 | `terminal`. OBS |
| 57 | `comando_usuario`. OBS |
| 58 | timestamp y cualquier estado/metadata cognitiva. OBS |
| 59 | Solo cliente HTTP/REPL; el estado cognitivo permanece backend. L |
| 60 | No automático. L |
| 61 | HTTP con httpx. L |
| 62 | test_chat usa TestClient/fakes y helpers directos; REPL usa transporte HTTP real. L |
| 63 | Puede recibirla por drift/backend, pero la bloquea y lanza error. OBS |
| 64 | triage original motor se copia y effective route pasa a persona. OBS |
| 65 | No ejecuta herramientas. L/OBS |
| 66 | Como salida, solo `hablar` con texto no vacío. OBS |
| 67 | adaptación en Orchestrator + allowlist exacta en cliente. OBS |
| 68 | Error “acción no conversacional”; cero ejecución. OBS |
| 69 | acción null o nombre none da error, no display. OBS |
| 70 | muestra texto_a_comunicar sanitizado. OBS |
| 71 | Solo texto público; `/estado` formatea campos públicos aparte. L/OBS |
| 72 | Sí. OBS |
| 73 | Sí. OBS |
| 74 | Sí, mensajes legibles; 5xx se generaliza. OBS |
| 75 | Lo rechaza porque status no es ok. OBS |
| 76 | No ejecuta; sí puede recibir una narrativa que lo afirme, sin certificarla. L |
| 77 | El modelo puede decirlo; el canal no posee prueba de outcome. L |
| 78 | JSON/no-dict/estructura inválida produce TerminalChatError. OBS |
| 79 | timeout/conexión/HTTP producen error sanitizado; no retry automático. OBS/L |

### Unity: serialización, consumo y confirmación (80–125)

| Nº | Respuesta |
|---:|---|
| 80 | FastAPI/Pydantic v2 response serialization. L/OBS |
| 81 | Newtonsoft `JsonConvert`. L |
| 82 | Newtonsoft.Json; no System.Text.Json ni JsonUtility. L |
| 83 | Coincide por nombres públicos snake_case; no strategy declarada. L |
| 84 | No conversión: ambos DTO declaran snake_case. L |
| 85 | Newtonsoft resuelve miembros sin distinguir case por default; INF, no compilado. |
| 86 | Pydantic y Newtonsoft default ignoran unknown top-level. OBS/INF |
| 87 | Sí: Pydantic usa defaults; C# deja referencias null y bool false. OBS/L |
| 88 | Campo C# string null permanece null; handlers suelen aplicar `??`. L/INF |
| 89 | En JObject depende de `Value<float>`/TryParse/default; no regla global. L |
| 90 | No hay parámetros int consumidos en esta ruta; conducta genérica no ensayada. L |
| 91 | DTO missing→false; parámetros bool usan `Value<bool>()` con defaults ad hoc. L/INF |
| 92 | No se compiló serializer C#; Python→wire no-finitos sí quedó resuelto como null. INF/OBS |
| 93 | Puede activar defaults 0 en handlers ad hoc; navegación xyz no lo hace. L |
| 94 | No para navegar_a_posicion: falta/null hace fallar TryLeerFloat. L |
| 95 | No; Vector3 se crea después de validar los tres ejes. L |
| 96 | Depende del handler: parse/Value puede fallar o defaults degradan; no contrato uniforme. L |
| 97 | `LazzaroAgent.ProcesarRespuestaDelCerebro`. L |
| 98 | No se ignoran. L |
| 99 | Se registra warning y se envía outcome false. L |
| 100 | Navegar objeto/posición, alejarse, girar y cancelar tienen efecto local; cuatro interacciones son stubs con success. L |
| 101 | `EjecutarNavegarAObjeto`→`MovementController.NavegarAObjeto`→NavMesh. L |
| 102 | `EjecutarNavegarAPosicion`→MovementController→NavMesh. L |
| 103 | `EjecutarGirarAvatar`→MovementController. L |
| 104 | Interactuar/agarrar/soltar/fuerza solo validan/loguean/reportan; no efecto material. L |
| 105 | UI/log + completa tarea local; no resultado_accion. L |
| 106 | completa subtarea como fallo; acción null, en cambio, retorna sin completarla. L |
| 107 | Sí, switch exacto de diez nombres. OBS/L |
| 108 | Parcialmente, por handler. L |
| 109 | No: TryLeerFloat no comprueba finitud. L |
| 110 | Sí para navegación a objeto y proximidad; algunos handlers usan default desconocido. L |
| 111 | Solo proximidad/objeto; no revalida riesgo ni affordance de Motor. L |
| 112 | Solo conoce el bool final requiere_confirmacion. L |
| 113 | No; valido se pierde. L |
| 114 | No; metadata/status Motor se pierde. L |
| 115 | No distingue ready/clarification/unsafe/not_allowed/not_found como estados. L |
| 116 | Recibe únicamente acción final y prosa/campo de confirmación. L |
| 117 | ActionResponse bool→AccionSaliente bool→JSON bool→C# bool→guard Unity. L/OBS |
| 118 | Orchestrator, Unity y terminal; Executor directo tiene semántica histórica distinta. L |
| 119 | En Orchestrator/Unity significa no ejecutar aún/omitir; no es token de autorización. L |
| 120 | Sí: omite y envía failure nuevo. L |
| 121 | No ejecuta nada y solo acepta hablar; acción no conversacional queda bloqueada. OBS |
| 122 | Transporta el bool; la decisión pending ocurre antes y el consumer vuelve a interpretar. L |
| 123 | Sí: missing en C# queda false y desaparece la distinción “ausente”. L |
| 124 | Sí, default C# bool=false. L |
| 125 | Pydantic puede coaccionar `"false"` a false al construir DTO; JSON contractual esperado es bool. Heredado/OBS Bloque 2 |

### Unity→Python, outcome, percepción y dolor (126–174)

| Nº | Respuesta |
|---:|---|
| 126 | percepción + accion + exito + mensaje_resultado + extras opcionales. L/OBS |
| 127 | No. L |
| 128 | Sí, pero uno nuevo del evento outcome; no el original. L |
| 129 | Solo heurística nombre/target; no vínculo obligatorio. L/OBS |
| 130 | Siempre nombre; puede acompañarse de target/parámetros. L |
| 131 | A veces objeto_involucrado/nombre/parámetro; no estable ni obligatorio. L |
| 132 | No timestamp de captura. L |
| 133 | Productor Unity envía bool real. OBS/L |
| 134 | Solo mensaje libre/failure bool; no error tipado. L |
| 135 | No. L |
| 136 | Percepción actual y params del movimiento; no campo canónico final_position. L |
| 137 | No dentro de resultado normal. L |
| 138 | Evento separado alerta_dolor_fisico. L |
| 139 | Extras/percepción flexible, no metadata de contrato. L |
| 140 | Pydantic no pierde nested; ya faltan action/original transaction/time/duration al salir de Unity. OBS |
| 141 | No de forma fiable. L |
| 142 | No de forma fiable; fallback puede consumir otro pendiente. OBS |
| 143 | Solo algunos helpers devuelven no-match; no hay clasificación/ledger canónico. L |
| 144 | No, faltan secuencia/tiempo/action ID. L |
| 145 | Sí; heredado TASK-AUDIT-001 y fallback reproducido. OBS |
| 146 | Tipos se conservan en detalles: bool/bool/str/str/int/int/None/ausente. TaskContext aplica bool: true,false,true,true,true,false,None,None; lectores `is True/False` difieren. OBS |
| 147 | `PerceptionSystem.EscanearEntorno`. L |
| 148 | Colliders tag Interactuable, dentro de radio/FOV, hasta ocho, excluyendo self/Plane. L |
| 149 | Object ID depende de metadata; fallback nombre. Snapshot ID no es generación estable. L |
| 150 | nombre runtime y display_name. L |
| 151 | No campo type canónico de objeto. L |
| 152 | x/y/z redondeados. L |
| 153 | nombre/display/aliases metadata, lista deduplicada. L |
| 154 | allowlist canónica; desconocidas van separadas. L |
| 155 | clamp metadata 0..1; default 1. L |
| 156 | Ausente. L |
| 157 | runtime_instance_id por objeto; no frame/generation del snapshot. L |
| 158 | No edad/freshness verificable. L |
| 159 | Distancia ascendente; empate sin desempate estable. L |
| 160 | objeto más cercano puede null; colliders null se omiten; API permite detalles parciales. L |
| 161 | Solo por contenido del snapshot_id; dos capturas iguales no se distinguen. L |
| 162 | Sí, `source=unity` en snapshot/objetos. OBS/L |
| 163 | Sí; detalles es flexible y normalizador degrada parciales. L |
| 164 | Builder omite collider null; campo/objeto faltante cruza y puede omitirse en normalización. L |
| 165 | `alerta_dolor_fisico`, IMPLEMENTADO. L |
| 166 | percepción + bloqueo/escape/acción/exito + dict impacto. L |
| 167 | velocidad relativa (magnitud Unity), dolor normalizado 0..1; unidades físicas no versionadas. L |
| 168 | No body part ni punto de contacto en wire. L |
| 169 | No timestamp de captura. L |
| 170 | source unity dentro de percepción; no source propio del impacto. L |
| 171 | Impacto tiene tipo y conteo de contactos, no lista/posición de contactos. L |
| 172 | Pydantic conserva dict; Behavior/Orchestrator proyectan campos útiles. L |
| 173 | El tipo activa flag dolor reciente y deltas; intensidad detallada nested no parametriza esos deltas. L |
| 174 | Se pierde tiempo, body/contact point, action ID y vínculo al movimiento; detalle nested no queda tipado. L |

#### Detalle de 146: matriz `exito`

| Wire | Pydantic | TaskContext `bool()` | Lectores `is True/False` |
|---|---|---:|---|
| true | bool True | éxito | True |
| false | bool False | fallo | False |
| `"true"` | str | éxito | ninguno |
| `"false"` | str | **éxito** | ninguno |
| 1 | int | éxito | ninguno |
| 0 | int | fallo | ninguno |
| null | None | no registra | ninguno |
| missing | ausente | no registra | ninguno |

### IDs, tiempo y transporte (175–204)

| Nº | Respuesta |
|---:|---|
| 175 | El comando sí vuelve en RespuestaAPI; la cadena se corta en el resultado. OBS/L |
| 176 | Lo deserializa pero no lo conserva/usa para outcome. L |
| 177 | Sí, GUID nuevo por cada evento de retorno. L |
| 178 | Sí, id_accion UUID de Orchestrator. L |
| 179 | No con el productor Unity actual. L/OBS |
| 180 | No; es interno de AuditTrace. L |
| 181 | El contrato actual no lo exige; falta un correlator público/ledger, sea o no el trace interno. Documentado, no diseñado. |
| 182 | Evento.timestamp, Respuesta.timestamp_servidor, Audit timestamp y Time.time local de dolor. L |
| 183 | Pydantic crea los dos primeros si faltan; Audit Core/writer; Unity crea Time.time. L |
| 184 | Los de API usan utcnow; Unity Time.time no es UTC. L |
| 185 | API defaults son naive; input puede traer offset; Unity response lo guarda string. OBS/L |
| 186 | API ISO datetime; Time.time segundos; duraciones Audit ms. L |
| 187 | Evento/servidor se transportan casi sin lectores; Time.time solo cooldown; Audit sí observa. L |
| 188 | Pydantic valida datetime; no valida coherencia/edad/zona. OBS |
| 189 | No para outcome/perception Unity. L |
| 190 | No entre procesos de forma confiable. L |
| 191 | Una coroutine request/response con callbacks. L |
| 192 | Sí, Unity 15–180 s/default 90; terminal default 300 s. L |
| 193 | No automático en ambos clientes. L |
| 194 | No. L |
| 195 | No cancelación explícita del request; movimiento/tarea tienen cancelación local distinta. L |
| 196 | Unity failure con error/código/body; terminal error sanitizado. L/OBS |
| 197 | Unity failure con body completo; terminal mensaje genérico. L/OBS |
| 198 | Cliente queda incierto y descarta evento; servidor puede haber mutado/cacheado. L |
| 199 | Un retry externo/manual puede duplicar en fallos pre-cache/reinicio. Heredado |
| 200 | Solo tras completion y mientras exista cache RAM; no durable ni universal. OBS/heredado |
| 201 | No implementa retry; por tanto no existe preservación automática. L |
| 202 | Tampoco retry; caller puede pasar manualmente el mismo ID. L |
| 203 | Sí. Para Unity, respuesta se envía tras decisión/dispatch lógico y antes de outcome físico. L |
| 204 | HTTP 200 significa **decisión/envelope aceptado por API**; puede incluir dispatch lógico. No prueba executing ni outcome. OBS/L |
