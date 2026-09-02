# Fase 2 · Bloque 3 — Auditoría Core / Orchestrator

Fecha: 2026-09-02. Código examinado desde `e396ec9`, rama
`codex/stabilization/pre-capa2`. Estado: auditoría completada; hallazgos OPEN,
sin fixes funcionales. AuditTrace continúa en v1. No se abrió el Bloque 4 ni
Capa 2/DevelopmentalCore.

## 1. Resumen ejecutivo

La respuesta corta a la pregunta del bloque es:

- **BehaviorCore posee el estado conductual productivo**: su `StateEngine`, su
  `PersonalityTraits`, su `TaskContext`, la última percepción, la constelación y
  la policy activa.
- **Orchestrator posee el flujo operativo**: idempotencia en RAM, locks por
  transacción, Triage y adaptación de canal, selección de capa, guardarraíles,
  conversión pública, ejecución/dispatch, candidatos de alias, notificaciones y
  orden de side effects.
- **MemoryManager posee tres memorias diferentes**: persistencia Chroma,
  WorkingMemory y RAM conversacional. Su thread-safety es parcial, no global.
- **IdentityState no posee otra reserva en producción**: Orchestrator lo crea con
  el mismo `StateEngine` de BehaviorCore y actúa como adaptador/mutador tardío.
- **ToolPolicy solo filtra nombres**. No decide intención, no valida parámetros,
  no confirma y no ejecuta.
- **El cliente posee la ejecución física externa**. Para Unity/Roblox,
  Orchestrator solo etiqueta la acción como `despachada` y la devuelve por API.

La secuencia nominal real es:

```text
event_start (antes del lock)
  → lock por id_transaccion
  → cache / retry manual pendiente
  → despertar terminal pendiente
  → BehaviorCore (estado/tarea/personalidad/percepción)
  → Triage original
  → adaptación de canal
  → recuperación de memoria condicional
  → escritura episódica incondicional
  → resultado_accion puede aprobar alias anterior
  → selección de tools
  → resolver de capa
  → firewall terminal
  → sanitización
  → firewall terminal
  → esfuerzo subjetivo
  → AccionSaliente
  → candidato de alias
  → ejecutar local / dejar pendiente / despachar
  → WorkingMemory
  → RAM conversacional y posibles hechos relacionales
  → cache
  → notificaciones
  → IdentityState
  → descanso terminal diferido
  → retorno
  → liberar lock
  → event_end
```

El lock garantiza una sola ejecución para retries **solapados del mismo ID** y
el cache evita repetir side effects cuando el primer turno llegó completamente
al punto de cache. No es una garantía durable ni global: el cache conserva solo
`AccionSaliente`, vive en proceso, tiene máximo 2000 entradas y se escribe
después de ejecutar/despachar y de WorkingMemory. Un fallo antes de ese punto
deja el ID sin recibo; el probe reprodujo que un retry repite BehaviorCore,
escritura episódica y ejecución.

Transacciones con IDs distintos sí se ejecutan en paralelo. El probe hizo que
ambas coincidieran dentro del mismo BehaviorCore. `StateEngine` protege cada
método con `RLock`, pero `BehaviorCore`, `TaskContext`, `PersonalityTraits`,
percepción, constelación y varias colecciones de Orchestrator no comparten una
frontera transaccional. Por tanto, el estado es canónico en identidad pero no en
atomicidad de turno.

Se confirmó `CORE-AUDIT-001`: todo `comando_usuario` no vacío se convierte en
objetivo. `hola`, `como estas?`, `ve al cubo` y `recuerda esto` reemplazan la
tarea sucesivamente. Un outcome tardío se aplica a la tarea que exista cuando
llega, no a la que originó la acción. Incluso `detente` reemplaza primero el
objetivo y después Orchestrator cancela esa tarea recién renombrada.

`CORE-AUDIT-002` no es una segunda fuente productiva: los tres globales legacy
se instancian al importar, son objetos distintos, pero `main.py` construye un
BehaviorCore nuevo y no hay consumidores productivos de esos globales. Queda
como deuda P3, no como divergencia activa.

No se demostró P0. Se registran **19 tickets OPEN: 11 P1, 7 P2 y 1 P3**. Los P1
que más condicionan DevelopmentalCore son la identidad de tarea/outcome, el
commit tardío de idempotencia, la concurrencia entre IDs, la ausencia de un
ledger de acciones y la correlación permisiva de aliases.

## 2. Alcance, método y etiquetas

Se recorrieron `procesar_evento()` y `_procesar_evento_serializado()` de
principio a fin antes de crear archivos. Después se siguieron únicamente los
helpers y componentes necesarios para ownership, mutaciones, locks, retries,
ejecución y errores. No se auditó calidad del RF, retrieval, affordances, Oracle
o proveedores más allá de sus fronteras con Orchestrator.

Etiquetas usadas:

- **IMPLEMENTADO**: consecuencia directa del código.
- **OBSERVADO**: reproducido por probe determinista.
- **INFERIDO**: consecuencia estructural con ruta clara, sin incidente real.
- **HEREDADO**: ya demostrado en Bloque 2; aquí solo se siguió operativamente.

`PASS` significa “baseline reproducido”, no “comportamiento correcto”. El probe
nuevo instala barreras de red y de escritura/SQLite contra `lazzaro_data`, usa
fakes en memoria y no construye servidor, Chroma, proveedor ni Unity.

## 3. Flujo real completo

### 3.1 `procesar_evento()`

1. Convierte `id_transaccion` a string, crea un `trace_id` UUID4 y empieza el
   reloj/captura LLM.
2. Emite `event_start` y captura `pre` **antes de adquirir el lock de la
   transacción**.
3. Bajo `_transacciones_lock_guard`, obtiene o crea un estado por ID con un
   `RLock` y contador `users`; el contador incluye al propietario y a waiters.
4. Mantiene ese `RLock` durante toda `_procesar_evento_serializado()`.
5. En `finally`, decrementa `users` y elimina la entrada cuando llega a cero.
6. Una excepción no absorbida emite `error(component=orchestrator)` y se
   propaga.
7. Otro `finally` emite `event_end`, captura `post` y cierra el reloj aunque el
   flujo haya retornado temprano o lanzado.

Implicaciones:

- dos llamadas same-ID pueden emitir ambos `event_start` antes de que la segunda
  espere;
- el trabajo funcional same-ID sí queda serializado;
- IDs distintos solo comparten el guard durante microsecciones de bookkeeping;
- el writer AuditTrace falla abierto;
- todos los locks transaccionales se liberan ante return o excepción.

### 3.2 `_procesar_evento_serializado()`

| Orden | Operación | Mutación/side effect |
|---:|---|---|
| 1 | `_obtener_transaccion_cacheada` | LRU: `move_to_end`; return inmediato en hit |
| 2 | `_reintentar_guardado_conversacional_pendiente` | puede reescribir fotografía manual y retornar |
| 3 | consumir descanso terminal | puede llamar `StateEngine.finalizar_descanso()` |
| 4 | `BehaviorCore.procesar_evento` | estado, personalidad, tarea, percepción y constelación |
| 5 | Triage original | lectura del evento y estado ya mutado |
| 6 | adaptación terminal | puede cambiar solo la ruta operativa a Persona |
| 7 | lectura de memoria condicional | larga + conversacional + relacional + WorkingMemory |
| 8 | `guardar_experiencia` | episodio para todo flujo que alcanzó este punto |
| 9 | alias de outcome anterior | un `resultado_accion` exitoso puede persistir alias |
| 10 | tools permitidas | ToolPolicy filtra; Registry describe |
| 11 | resolver decisión | crítica, cancelación, save, corte, pasiva o capa |
| 12 | guardarraíles | terminal → sanitize → terminal |
| 13 | esfuerzo + DTO | crea nuevo `id_accion` y `AccionSaliente` |
| 14 | candidato alias | se guarda en RAM antes de ejecución |
| 15 | ejecución | skipped/pending/ok/error/despachada |
| 16 | WorkingMemory | guarda la decisión y estado de dispatch, no outcome futuro |
| 17 | turno conversacional | RAM y posibles hechos relacionales persistentes |
| 18 | cache/pending-save | punto de recibo de idempotencia normal |
| 19 | notificaciones | pensamiento de `none` y/o error |
| 20 | IdentityState | segundo cambio sobre StateEngine compartido |
| 21 | descanso terminal | puede armar recuperación diferida |

### 3.3 Lo que ocurre antes de Triage

Antes de que Capa 0 vea el evento ya pudieron ocurrir:

- snapshot `pre` y escritura AuditTrace;
- espera/adquisición del lock;
- lookup/mutación LRU del cache;
- retry manual aislado;
- finalización de descanso terminal;
- sincronización o reemplazo de tarea;
- registro de outcome en la tarea vigente;
- `StateEngine.tick()` y estímulo emocional;
- cambio de personalidad;
- eventos recursivos de loop/estancamiento/compulsión;
- actualización de modificadores, percepción y constelación.

Por tanto, Triage clasifica usando un `estado_actual` **posterior** a todas esas
mutaciones. BehaviorCore modifica estado sin conocer intent ni ruta.

## 4. Ownership y orden de mutaciones

| Componente | Posee | Lee | Muta | Thread-safety efectiva |
|---|---|---|---|---|
| `BehaviorCore` productivo | StateEngine, PersonalityTraits, TaskContext, policy, percepción, constelación | detalles del evento | todos sus objetos | sin lock de turno |
| `StateEngine` | energía, estrés, foco, carga y variables conductuales | clock/modificadores | tick, eventos, descanso, IdentityState | `RLock` por método |
| `PersonalityTraits` | cinco rasgos | evento/intensidad | read-modify-write | sin lock |
| `TaskContext` | objetivo, contadores, frustración, umbral, historial | acción/outcome | reset/intentos | sin lock |
| `PsychologyEngine` | no estado propio relevante | State/Personality/Task | proyección; detectores producen logs | sin snapshot conjunto |
| `IdentityState` productivo | no reserva separada | StateEngine compartido | costes tardíos sobre StateEngine | delega al lock de StateEngine |
| `Orchestrator` | caches, locks, pending saves/aliases, notifications | todos los subsistemas | routing y side effects | lock solo por ID; mapas sin lock común |
| `MemoryManager` | Chroma, WorkingMemory, ConversationMemory, dedupe/counters | contexto | episodios, reglas, RAM | locks parciales por backend/feature |
| `ToolRegistry` | mapa class-level de tools | selección/ejecución | decoradores de registro | sin lock; normalmente startup |
| `ActionExecutor` | nada durable | Registry global | ejecuta tool local | captura errores de tool |

Mutadores por dato:

- **energía/estrés/foco**: `StateEngine.tick/aplicar_evento`, descanso terminal,
  `IdentityState.registrar_evento` y descanso explícito;
- **tarea/frustración**: exclusivamente BehaviorCore/TaskContext, pero el comando
  se aplica antes de que Orchestrator reconozca cancelación;
- **personalidad**: `BehaviorCore.personality.update_from_event`, incluyendo
  eventos recursivos;
- **percepción**: caches `_ultima_percepcion*` de BehaviorCore;
- **memoria conversacional**: MemoryManager después de una salida `hablar`; el
  registro también puede persistir hechos relacionales;
- **WorkingMemory**: Orchestrator después de ejecución/dispatch;
- **aliases**: Orchestrator mantiene candidatos; MemoryManager persiste el
  aprendido.

Un evento nominal puede cambiar la misma reserva dos veces: BehaviorCore aplica
el estímulo antes de Triage y, al final, IdentityState aplica coste de ruta/fallo.
Un `resultado_accion` que activa detectores puede aplicar todavía más estímulos
recursivos.

### Globales legacy

Se instancian al importar:

- `core.behavior_core.behavior_core`;
- `core.state_engine.estado_actual`;
- `core.personality.personalidad`.

Son tres ramas de objetos diferentes. `main.py:51` crea otro `BehaviorCore` y lo
inyecta a Orchestrator; no se hallaron imports productivos de esos objetos
globales. Producen allocation/initialization al importar, pero no compiten por el
estado de `app.state.orchestrator`.

## 5. Máquina efectiva de TaskContext

```text
SIN TAREA
  └─ comando_usuario con mensaje no vacío
       └─ CREA TaskContext(objetivo = mensaje)

TAREA ACTIVA
  ├─ comando_usuario con mensaje distinto
  │    └─ RESET del mismo TaskContext
  │         objetivo = mensaje nuevo
  │         intentos/fallos/frustración/historial = 0
  │         umbral_abandono = SE CONSERVA
  ├─ evento con accion != None y exito != None
  │    ├─ registrar_intento(accion, bool(exito))
  │    ├─ recalcular umbral desde paciencia
  │    └─ detectores pueden disparar eventos emocionales recursivos
  ├─ detalles.objetivo_completado is True
  │    ├─ evento recursivo tarea_completada
  │    └─ task_context = None
  └─ cancelación explícita no terminal
       ├─ el comando ya pudo resetear objetivo a "detente"
       └─ task_context = None
```

No existe transición automática `frustración >= umbral → abandonar`. Psychology
solo genera `debe_abandonar`/narrativa para el prompt. Tampoco existe correlación
entre TaskContext y `id_accion`, `id_transaccion` u objetivo histórico.

Evidencia observada:

- objetivos consecutivos: `hola → como estas? → ve al cubo → recuerda esto`;
- después de `ve al cubo → hola → resultado_accion(navegar, false)`, la tarea
  `hola` recibe el intento fallido;
- una cancelación iniciada con tarea `ve al cubo` reporta como tarea cancelada
  `detente`;
- `reset()` conserva un umbral inyectado de `47.0`;
- el quinto fallo no-motor `hablar` produjo
  `fracaso → loop_detectado → fracaso → sobrecarga` y la tarea siguió activa;
- `obtener_resumen()` invoca loop una vez, estancamiento dos y compulsión dos;
  son lecturas lógicas, pero pueden duplicar warnings.

## 6. Mapa de early returns

Hay **tres early returns significativos** y un return nominal.

| Return | Qué ya ocurrió | Qué omite | Cache | Memoria | Ejecución |
|---|---|---|---|---|---|
| cache hit | `event_start`, lock, lookup+LRU | descanso, Behavior, Triage, toda memoria, capa, WM, alias, notif., Identity | ya existía | no nueva | no nueva |
| retry manual pendiente | límites, lock, cache miss, solo reintento save + guards + DTO | descanso, Behavior, Triage, episodio, decisión auditada, ejecución, WM, conversación, notif., Identity | solo si retry resuelve | fotografía manual únicamente | no |
| fallo Behavior | límites, lock, posible despertar y mutaciones parciales internas | Triage en adelante | no | no | no |
| retorno nominal | flujo completo | nada funcional posterior salvo liberación/event_end | normalmente sí | según rama | clasificada |

Una excepción no absorbida no es early return: sube a `procesar_evento`, emite
error/event_end y deja sin cache cualquier side effect previo al punto 18.

## 7. Matriz de side effects

Leyenda: `R` retrieval; `E` episodio; `C` turno/RAM conversacional (puede crear
hechos semánticos); `A` alias; `W` WorkingMemory; `I` IdentityState; `—` no.

| Rama | Estado/tarea antes | Mem. read | Mem. persistente | Capa/acción | Tool/dispatch | W | Cache | A | Notif. | I | Audit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| social | sí | R | E + C si habla | Persona/`hablar` | local o despachada | sí | sí | — | si error | sí | completo |
| motor | sí | R | E | Motor, a veces Reasoning | física/pending/dispatch | sí | sí | candidato | si error | sí | completo |
| oracle | sí | R | E + OracleMemory + C | Oracle→Persona | `hablar` | sí | sí | — | si error | sí | completo |
| reasoning | sí | R | E + C si habla | Reasoning | según tool | sí | sí | posible metadata motor no usual | si error | sí | completo |
| NOOP | sí | — | E | pasiva `none` | skipped | no en MemoryManager real | sí | — | normalmente — | sí | completo |
| INTERNAL | sí | — | E | pasiva `none` | skipped | no | sí | — | normalmente — | sí | completo |
| cancelación | sí; primero puede resetear tarea | R según Triage | E | `cancelar_tarea` | cliente/local | sí | sí | — | si error | sí | completo |
| save conversación | sí | — | E + fotografía manual | Persona determinista/`hablar` | dispatch | sí | sí o pending especial | — | si error | sí | completo |
| resultado_accion | sí + intento/outcome | normalmente — | E; alias si éxito | pasiva salvo solicita_decision | normalmente skipped | no para pasiva | sí | puede aprobar | normalmente — | sí | completo |
| cache hit | — | — | — | acción cacheada | — | — | hit | — | — | — | límites |
| error Behavior | posible parcial | — | — | — | — | — | no | — | — | — | límites+error |
| error Memory | sí | intento R | E puede fallar | capa continúa | normal | sí | sí | según ruta | según acción | sí | completo, sin distinguir no-hit |
| error de capa capturado | sí | según ruta | E | fallback/invalid | normal/skipped | según acción | sí | según metadata | error | sí | error+decision+execution |
| error Motor no capturado | sí | R | E | excepción | — | — | no | — | — | — | error+límites |

Side effects importantes **antes de validar la decisión**: estado, tarea,
personalidad, percepción, lectura de memoria, episodio, posible consolidación
observacional y aprobación de alias de un outcome anterior. Después de crear
`AccionSaliente`: candidato de alias, ejecución/dispatch, WorkingMemory,
conversación, cache, notificaciones, IdentityState y descanso.

## 8. Idempotencia y cache

### Garantía que sí existe

Cuando el primer intento completa hasta `_cachear_transaccion`:

- same-ID secuencial devuelve **el mismo objeto** `AccionSaliente`;
- same-ID concurrente espera el lock, observa el cache y no repite Behavior,
  Triage, memoria, capa, ejecución, alias, notificación ni IdentityState;
- cada intento conserva límites AuditTrace propios, pero el hit no fabrica stages
  que no ejecutó.

### Lo que guarda

El valor es solo un `AccionSaliente` mutable: ID/nombre/parámetros/razonamiento,
pensamiento privado, confirmación y descripciones. No guarda:

- `ActionResponse.valido`, error o metadata;
- resultado de ejecución/dispatch;
- snapshot de estado/tarea;
- recibos de memoria/alias/notificación;
- outcome físico;
- trace_id.

### Límites

- RAM del proceso; reinicio pierde todo;
- máximo 2000; hit actualiza LRU y eviction elimina el más antiguo;
- mismo contenido con ID distinto ejecuta todo otra vez;
- ID expulsado ejecuta todo otra vez;
- fallo Behavior o excepción pre-cache no deja recibo;
- el save manual fallido es la única excepción: un pending especial hace que el
  retry repita solo la fotografía manual;
- cache se crea después de tool/dispatch y WorkingMemory, pero antes de
  notificaciones, IdentityState y descanso terminal.

El probe con fault injection hizo fallar WorkingMemory después de una ejecución
local simulada: el retry same-ID ejecutó dos veces, llamó Behavior dos veces y
escribió dos episodios. Esto demuestra la frontera, aunque no afirma que la deque
real falle rutinariamente.

## 9. Locks y concurrencia

| Lock/recurso | Granularidad | Duración | Observación |
|---|---|---|---|
| `_transacciones_lock_guard` | global bookkeeping | crear/retirar lock | corto |
| per-ID `RLock` | una transacción | flujo funcional completo | incluye LLM, Chroma y tools |
| `StateEngine._state_lock` | instancia | cada método | no hace atómico Behavior completo |
| `_descanso_terminal_lock` | Orchestrator | wake/arm | anida StateEngine |
| Memory manual-save lock | MemoryManager | fotografía + recibos | global para saves manuales |
| ConversationMemory lock | conversación | operación RAM | propio |
| Chroma locks por path/colección | backend | llamadas de DB | propio |

El per-ID lock mantiene llamadas largas, pero solo bloquea retries del mismo ID;
no detiene transacciones ajenas. No se encontró un camino inverso que adquiera
el lock transaccional desde StateEngine, Memory o tool, así que **no se demostró
deadlock**. Los `finally` liberan el lock ante excepción.

Races confirmadas/estructurales:

- dos IDs distintos pueden entrar simultáneamente a BehaviorCore;
- TaskContext, PersonalityTraits, percepción, policy y constelación carecen de
  lock conjunto;
- `_transacciones_procesadas.get()` + `move_to_end()` no es atómico: el probe
  intercaló eviction y obtuvo `KeyError`;
- pending saves, aliases y notificaciones son colecciones compartidas sin el
  guard transaccional global;
- `obtener_notificaciones()` hace `list()` y luego `clear()`: el probe insertó
  una notificación entre ambos y quedó perdida;
- WorkingMemory usa una deque sin lock de snapshot; Chroma y conversación tienen
  protecciones propias, pero dedupe sets y contador de consolidación de
  MemoryManager no comparten lock general;
- `/evento` corre en threadpool, mientras `/estado` y `/notificaciones` leen
  objetos compartidos desde el event loop.

## 10. Decisión → ejecución → resultado

```text
resolver de capa
  → ActionResponse
  → firewall terminal
  → sanitizar inválido/tool inexistente
  → firewall terminal otra vez
  → esfuerzo subjetivo
  → AccionSaliente (pierde valido/error/metadata)
  → _gestionar_ejecucion
       none                   → sin_accion / audit skipped
       requiere_confirmacion  → pendiente_confirmacion / audit pending
       sandbox                → ActionExecutor / ok|error
       otro cliente           → despachada / audit pending
  → resultado_accion futuro es OTRO evento, si llega
```

“Decidir” crea ActionResponse; “despachar” solo devuelve una orden a cliente;
“ejecutar” ocurre sincrónicamente únicamente para `sandbox`; “confirmar outcome”
depende de un `resultado_accion` posterior. No hay ledger central que conecte
esas cuatro fases.

Orchestrator no marca `despachada` como ejecución exitosa en AuditTrace, pero
`IdentityState.success` sí se calcula desde `action_response.valido and not
error`, no desde el outcome físico. WorkingMemory conserva `despachada` y no se
actualiza cuando llega el resultado. El cache también conserva la decisión, no
su outcome.

Correlación actual de `resultado_accion`:

- **TaskContext/estado**: ignoran `id_accion`; usan `accion` y `exito` sobre la
  tarea vigente;
- **alias**: intenta ID exacto; si falla, busca de más reciente a antiguo por
  acción/target opcionales; si ambos faltan acepta el más reciente;
- **WorkingMemory**: no actualiza el item original;
- **cache**: usa el ID de transacción del evento resultado, no el ID de acción.

Si el resultado llega tarde, cambia la tarea vigente. Si llega duplicado, vuelve
a aplicar estado/tarea bajo un nuevo `id_transaccion` y puede consumir otro
candidato de alias. Si nunca llega, WorkingMemory queda `despachada`, la tarea no
recibe intento y el candidato de alias permanece hasta eviction. Un resultado
desconocido puede afectar estado/tarea y, si es exitoso, el alias más reciente.

Para aliases, un fallo explícito no elimina el candidato. Un éxito local
`sandbox` lo aprueba inmediatamente; un dispatch externo espera outcome. El
probe reprodujo `unknown-action → action-b` y el duplicado siguiente
`unknown-action → action-a`.

## 11. Guardarraíles, invalidación y confirmación

El orden terminal es deliberadamente doble:

1. un ActionResponse físico/`none` se transforma a `hablar` y a `valido=True`;
2. sanitización valida esa nueva acción;
3. el segundo firewall garantiza que sanitización no vuelva a exponer física.

Por eso la acción auditada coincide con la acción que `_gestionar_ejecucion`
recibe. Ninguna rama nominal después de decisión salta estos guardarraíles. Los
early returns de cache/manual retry son excepciones controladas: cache ya guarda
la acción guardada; retry manual aplica los guardarraíles explícitamente.

Fuera de terminal, `valido=False` se vuelve `none` pero sigue siendo inválido.
Antes ya dejó estado/tarea/episodio; después puede dejar cache, notificaciones e
IdentityState. En terminal, ese mismo inválido termina públicamente como
`hablar`, registra WorkingMemory y turno conversacional, conserva `error` solo
internamente y marca `IdentityState.success=False`. El DTO público no permite
distinguir el fallback inválido; este es el impacto operativo del hallazgo
heredado del Bloque 2.

Confirmación mantiene las dos semánticas heredadas:

- Orchestrator interpreta `requiere_confirmacion=True` como “detener antes del
  executor”;
- ActionExecutor interpreta `requiere_confirmacion=True` como dato suficiente
  para pasar su guard interno.

No hay endpoint de reanudación/confirmación en `api/routes.py`. El probe confirmó
que `True` produce `pendiente_confirmacion` sin llamar executor y `False` en
sandbox sí entra al executor. No se demostró ejecución física indebida; la
contradicción tiende a bloqueo o flujo incompleto.

## 12. Errores, fallbacks y rollback

No existe rollback lógico de StateEngine, Personality, TaskContext, Chroma,
aliases, tool o WorkingMemory. Cada helper intenta fallar abierto cuando fue
diseñado así; una excepción no capturada conserva todo lo anterior.

| Falla | Manejo real | Retorno/API | Side effects y retry |
|---|---|---|---|
| AuditTrace | warning y continuar | normal | no cambia funcionalidad |
| BehaviorCore | Orchestrator captura y retorna `None` | API `status=ok`, sin acción | puede dejar mutación parcial; no cache; retry repite |
| Triage | captura y usa `{}` | cae a Reasoning | estado/memoria siguen; cache normal |
| retrieval Memory | captura y texto vacío | capa continúa | no-hit y fallo indistinguibles, heredado |
| episodio Memory | captura y `None` | capa continúa | turno puede cachearse sin episodio |
| WorkingMemory | no captura en Orchestrator | 500 | ocurre después de execution; no cache; retry repite |
| Persona | helper captura | fallback `hablar` válido con error | cache, notif., Identity fail |
| Oracle | captura cache/client/engine/verbalización | fallback hablado con error | OracleMemory pudo tener side effects previos |
| Reasoning general | captura | `none`, inválido | se sanitiza y cachea |
| Reasoning dentro Motor | captura | `none`, inválido | se sanitiza y cachea |
| diagnóstico/proceso Motor determinista | fuera del `try` local | excepción/500 | Behavior+memory ya ocurrieron; no cache; retry repite |
| tool local | ActionExecutor captura | estado `error` | WorkingMemory+cache; no retry automático |
| conversación/alias/Identity/rest | helpers capturan en general | normal | side effect puede faltar sin cambiar respuesta |

Persona, Oracle y Reasoning convierten la mayoría de errores en ActionResponse;
Motor no tiene una frontera equivalente alrededor de
`diagnosticar_comando_usuario()` ni de todas las llamadas deterministas a
`procesar_comando_motor()`. El probe lanzó ahí dos veces con el mismo ID: ambos
intentos propagaron y cada uno repitió Behavior y episodio.

Los fallbacks no son solo respuesta. Dado que aparecen después de Behavior y del
episodio, el estado/memoria ya cambiaron; luego el fallback puede añadir
WorkingMemory, conversación, cache, notificación e IdentityState. Memory fallida
no impide Persona/Motor; una decisión fallida no revierte Memory.

## 13. Side effects ocultos y callbacks

- `StateEngine.aplicar_evento()` llama `tick()` antes del estímulo; una acción
  aparente de evento también aplica tiempo.
- `TaskContext.obtener_resumen()` evalúa detectores y puede emitir warnings;
  Psychology y Behavior lo llaman varias veces por turno.
- `BehaviorCore.obtener_resumen()` puede materializar la constelación si estaba
  vacía mediante sus getters.
- `MemoryManager.guardar_experiencia()` puede programar consolidación; al umbral,
  una escritura episódica también puede crear regla y eliminar episodios
  observacionales. Su auditoría profunda pertenece al Bloque 4.
- `registrar_turno_conversacional()` no solo guarda RAM: extrae y persiste hechos
  relacionales.
- los proveedores de aliases/afecto conectados por Orchestrator son callbacks de
  **lectura** desde Motor hacia Memory. Capturan sus excepciones.

No se encontró callback `Memory → Behavior/Orchestrator` ni ciclo de locks que
reingrese el flujo. El grafo observado es Orchestrator→Memory y
Motor→proveedor Memory; no vuelve a Core.

AuditTrace es observación pasiva respecto al estado: usa
`BehaviorCore.obtener_snapshot_audit()`, que no llama detectores ni Triage ni
Memory, y copia el snapshot. El probe comparó estado/tarea antes y después. Su
captura no es, sin embargo, un snapshot atómico entre StateEngine y TaskContext
cuando otro ID muta en paralelo.

## 14. Tickets OPEN

### `CORE-AUDIT-001` — P1 — estado/tarea incorrecta

- **Evidencia:** `_extraer_objetivo_desde_detalles()` retorna todo mensaje de
  `comando_usuario`; probe: cuatro comandos, cuatro objetivos. `detente` pisa la
  tarea antes de cancelarla.
- **Ubicación:** `core/behavior_core.py:318-349`; orden en
  `core/orchestrator.py:587-696`.
- **Reproducción:** `task_commands_and_late_outcome`.
- **Impacto:** conversación social, controles y órdenes no relacionadas
  reinician objetivo, contadores, frustración e historial.
- **Recomendación preliminar:** separar “turno de usuario” de “nuevo objetivo” y
  hacer explícito el productor de lifecycle de tarea.
- **Estado:** OPEN.

### `CORE-AUDIT-002` — P3 — legacy/ownership

- **Evidencia:** existen tres instancias globales separadas, pero `main.py` crea
  e inyecta otra y no hay consumidores productivos de los globales.
- **Ubicación:** `core/behavior_core.py:606`, `core/state_engine.py:422`,
  `core/personality.py:140`, `main.py:51-86`.
- **Reproducción:** `legacy_instances_and_audit_snapshot` + búsqueda de imports.
- **Impacto:** allocations y confusión de lectura; no divergencia productiva
  demostrada.
- **Recomendación preliminar:** retirar o marcar explícitamente los singletons en
  fase de fixes, después de confirmar compatibilidad de imports.
- **Estado:** OPEN.

### `ORCH-AUDIT-001` — P1 — idempotencia / side effect duplicable

- **Evidencia:** cache se escribe después de `_gestionar_ejecucion()` y
  `memory.agregar_accion()`; fault injection pre-cache repitió ejecución,
  Behavior y dos episodios con same-ID.
- **Ubicación:** `core/orchestrator.py:867-915`.
- **Reproducción:** `retry_after_pre_cache_failure`.
- **Impacto:** una acción local/física puede repetirse tras 500 aunque el primer
  intento ya la materializó.
- **Recomendación preliminar:** diseñar ledger/estado de transacción durable con
  fases y recibo de ejecución antes de habilitar retries seguros.
- **Estado:** OPEN.

### `ORCH-AUDIT-002` — P1 — idempotencia limitada

- **Evidencia:** `OrderedDict` local máximo 2000; eviction y reinicio eliminan el
  único recibo. El probe expulsó un ID y su replay reejecutó Behavior.
- **Ubicación:** `core/orchestrator.py:45, 2679-2725`.
- **Reproducción:** `sequential_idempotency_and_cache_window`.
- **Impacto:** el contrato API dice “garantiza idempotencia”, pero no cubre
  retries tardíos/reinicio; side effects pueden duplicarse.
- **Recomendación preliminar:** versionar alcance de idempotencia y persistir
  recibos de acciones con política de retención.
- **Estado:** OPEN.

### `ORCH-AUDIT-003` — P1 — error parcial oculto

- **Evidencia:** Behavior puede mutar y lanzar; Orchestrator captura y retorna
  `None`; API lo publica como `status=ok`. No cachea y retry repite.
- **Ubicación:** `core/orchestrator.py:585-602`, `api/routes.py:43-54`.
- **Reproducción:** `behavior_partial_failure_retry` dejó dos mutaciones tras
  fallo+retry.
- **Impacto:** cliente no distingue fallo central de no-op legítimo; estado
  parcial queda sin recibo.
- **Recomendación preliminar:** definir outcome explícito de Core y punto de
  commit/rollback lógico.
- **Estado:** OPEN.

### `ORCH-AUDIT-004` — P1 — frontera de error Motor asimétrica

- **Evidencia:** diagnóstico y parte del procesamiento Motor quedan fuera del
  `try` que solo cubre Reasoning; mismo ID falló dos veces después de estado y
  episodio.
- **Ubicación:** `core/orchestrator.py:1802-1888`.
- **Reproducción:** `motor_exception_repeats_predecision_effects`.
- **Impacto:** 500/retry y duplicación predecisión; conducta distinta a
  Persona/Oracle/Reasoning.
- **Recomendación preliminar:** establecer una frontera uniforme de outcome por
  capa, sin esconder estados Motor.
- **Estado:** OPEN.

### `ORCH-AUDIT-005` — P2 — cache de objeto mutable

- **Evidencia:** cache guarda y devuelve la misma instancia `AccionSaliente`, sin
  copia ni congelación.
- **Ubicación:** `core/orchestrator.py:2679-2683, 2721-2725`.
- **Reproducción:** `same_id_returns_same_object=true`.
- **Impacto:** un caller Python que muta el DTO altera respuestas de retries;
  HTTP normal solo serializa y reduce el riesgo.
- **Recomendación preliminar:** almacenar snapshot inmutable/serializado o
  devolver copia.
- **Estado:** OPEN.

### `TASK-AUDIT-001` — P1 — outcome tardío en tarea equivocada

- **Evidencia:** TaskContext no guarda `id_accion`; Behavior aplica cualquier
  `accion/exito` a la tarea vigente. Probe: outcome de navegación terminó en
  objetivo `hola`.
- **Ubicación:** `core/behavior_core.py:103-119`.
- **Reproducción:** `task_commands_and_late_outcome`.
- **Impacto:** intentos, fallos, frustración y eventos emocionales se atribuyen a
  un objetivo no causante.
- **Recomendación preliminar:** correlacionar intento con action/task generation
  y decidir política explícita para outcomes tardíos.
- **Estado:** OPEN.

### `TASK-AUDIT-002` — P2 — reset incompleto

- **Evidencia:** `reset()` limpia objetivo/contadores/frustración/historial pero
  no `umbral_abandono`; probe conserva 47.0.
- **Ubicación:** `core/task_context.py:38-45`.
- **Reproducción:** `task_reset_and_detector_amplification`.
- **Impacto:** una tarea nueva hereda temporalmente tolerancia de la anterior
  hasta su primer outcome.
- **Recomendación preliminar:** declarar si el umbral pertenece a Task o a
  Personality y resetear/recalcular en una sola frontera.
- **Estado:** OPEN.

### `TASK-AUDIT-003` — P2 — estado “abandonar” sin transición

- **Evidencia:** Psychology produce banderas/narrativa; ningún consumidor llama
  cancel/finalize al superar umbral. Tarea permanece activa.
- **Ubicación:** `core/psychology.py:150-160`, `core/task_context.py`.
- **Reproducción:** `task_still_active=true`; búsqueda completa de consumidores.
- **Impacto:** el resumen puede afirmar que debe abandonar sin que la máquina de
  estados lo haga; DevelopmentalCore no puede inferir lifecycle solo por flags.
- **Recomendación preliminar:** decidir si es recomendación cognitiva o transición
  y nombrar/implementar el contrato elegido.
- **Estado:** OPEN.

### `STATE-AUDIT-001` — P2 — amplificación recursiva de un outcome

- **Evidencia:** después de registrar intento se evalúan tres detectores
  independientes; todos pueden disparar eventos recursivos en el mismo turno.
- **Ubicación:** `core/behavior_core.py:103-119`.
- **Reproducción:** quinto fallo `fracaso, loop_detectado, fracaso, sobrecarga`.
- **Impacto:** un resultado amplifica StateEngine/Personality sin envelope único;
  warnings se repiten por proyecciones posteriores.
- **Recomendación preliminar:** definir composición/prioridad de detectores y una
  sola transición observable por outcome.
- **Estado:** OPEN.

### `STATE-AUDIT-002` — P2 — éxito biológico no es outcome físico

- **Evidencia:** IdentityState recibe `success=valido and not error`; para acción
  externa despachada no espera `resultado_accion`. El evento resultado tampoco
  reconcilia la acción original en IdentityState.
- **Ubicación:** `core/orchestrator.py:2259-2288, 2613-2659`.
- **Reproducción:** mapa de flujo + probes de dispatch/invalid.
- **Impacto:** estado biológico mezcla calidad de decisión con éxito de ejecución;
  futuras capas pueden aprender señal equivocada.
- **Recomendación preliminar:** separar `decision_valid`, `dispatch_status` y
  `execution_outcome`.
- **Estado:** OPEN.

### `CONC-AUDIT-001` — P1 — Behavior compartido sin serialización global

- **Evidencia:** dos IDs distintos cruzaron una barrera dentro del mismo
  BehaviorCore; solo StateEngine tiene lock por método.
- **Ubicación:** `core/orchestrator.py:99-132`, `core/behavior_core.py:37-151`,
  `api/routes.py:43`.
- **Reproducción:** `concurrent_same_and_different_ids`.
- **Impacto:** tareas, personalidad, percepción y constelación pueden intercalar;
  un turno puede razonar con fragmentos de otro.
- **Recomendación preliminar:** definir actor/lock transaccional del estado
  conductual o separar estado por sesión antes de DevelopmentalCore.
- **Estado:** OPEN.

### `CONC-AUDIT-002` — P1 — race de cache

- **Evidencia:** `get` seguido de `move_to_end` sin lock; eviction intercalada
  produjo `KeyError` determinista.
- **Ubicación:** `core/orchestrator.py:2679-2683, 2721-2725`.
- **Reproducción:** `unprotected_cache_and_notifications`.
- **Impacto:** 500 en cache hit y pérdida/repetición posterior del recibo de
  idempotencia.
- **Recomendación preliminar:** proteger todas las operaciones compuestas del
  cache bajo un lock dedicado o estructura concurrente.
- **Estado:** OPEN.

### `CONC-AUDIT-003` — P2 — pérdida de notificación

- **Evidencia:** snapshot `list()` y `clear()` separados; append intercalado fue
  eliminado sin aparecer en el retorno.
- **Ubicación:** `core/orchestrator.py:2347-2351`.
- **Reproducción:** `late_notification_lost=true`.
- **Impacto:** avisos espontáneos/error pueden desaparecer bajo concurrencia.
- **Recomendación preliminar:** drenar atómicamente o usar cola con consumo
  protegido.
- **Estado:** OPEN.

### `CONC-AUDIT-004` — P2 — thread-safety parcial de colecciones Memory/Core

- **Evidencia:** pending saves/aliases, dedupe sets, contador de consolidación y
  WorkingMemory no comparten lock de turno; Chroma/conversación/manual save sí
  tienen locks locales.
- **Ubicación:** `core/orchestrator.py:78-85`; `memory/memory_manager.py:118-126,
  202-249, 1156-1181, 1549-1565`; `memory/working_memory.py`.
- **Reproducción:** análisis de ownership; overlap entre IDs confirmado.
- **Impacto:** dedupe/consolidación/orden de RAM pueden intercalar. No se afirma
  corrupción Chroma en este bloque.
- **Recomendación preliminar:** llevar estos recursos al Bloque 4 y definir
  granularidad de locks/atomicidad.
- **Estado:** OPEN.

### `EXEC-AUDIT-001` — P1 — correlación permisiva de alias

- **Evidencia:** ID desconocido cae a búsqueda por campos opcionales; sin acción
  ni target consume el candidato más reciente. Duplicado consumió el siguiente.
  Un outcome fallido no limpia candidato.
- **Ubicación:** `core/orchestrator.py:1162-1211`.
- **Reproducción:** `alias_correlation_fallback`.
- **Impacto:** una confirmación desconocida/tardía/duplicada puede persistir
  asociación objeto-palabra incorrecta.
- **Recomendación preliminar:** exigir ID exacto para aprobación; tratar fallo,
  expiración y duplicado como estados terminales.
- **Estado:** OPEN.

### `EXEC-AUDIT-002` — P1 — no existe ledger/reconciliación de acción

- **Evidencia:** WorkingMemory registra `despachada`; resultado futuro no actualiza
  el item, cache ni IdentityState original. Task y alias mantienen correlaciones
  independientes.
- **Ubicación:** `core/orchestrator.py:867-915, 2613-2659`; WorkingMemory.
- **Reproducción:** mapa productor/consumidor de `id_accion` y probe de
  idempotencia.
- **Impacto:** no se puede contestar canónicamente qué decisión fue ejecutada,
  falló o quedó pendiente; bloquea aprendizaje fiable.
- **Recomendación preliminar:** ledger de lifecycle con acción, transacción,
  tarea, dispatch y outcome idempotente.
- **Estado:** OPEN.

### `EXEC-AUDIT-003` — P1 — acción inválida deja side effects importantes

- **Evidencia:** inválido Unity dejó estado, episodio, cache, notificaciones e
  IdentityState; terminal lo convirtió a `hablar`, añadió WorkingMemory/turno y
  ocultó invalidez en DTO público.
- **Ubicación:** `core/orchestrator.py:587-675, 771-919, 1318-1342, 2450-2525`.
- **Reproducción:** `invalid_action_and_memory_fail_open`.
- **Impacto:** una decisión inválida parece acción normal y altera memoria/estado;
  futuros learners no pueden discriminarla.
- **Recomendación preliminar:** conservar outcome estructurado y decidir qué side
  effects pertenecen a evento vs decisión válida.
- **Estado:** OPEN.

## 15. Candidatos históricos

| Candidato | Resultado | Ticket |
|---|---|---|
| TaskContext toma cualquier comando como objetivo | **CONFIRMADO y reproducido** | `CORE-AUDIT-001` / `TASK-AUDIT-001` |
| ownership duplicado de Personality/State | **DESCARTADO como competencia productiva**; globales legacy sí existen | `CORE-AUDIT-002` P3 |

Seguimiento de Bloque 2, sin fixes:

- pérdida `ActionResponse → AccionSaliente`: confirmada operativamente;
- `resultado_accion.exito`: consumidores y orden documentados; truthiness
  contradictorio permanece HEREDADO;
- confirmación: no materializó ejecución indebida; sí frontera incompleta;
- MotorResolution: metadata interna alimenta candidatos alias antes de perderse
  públicamente;
- Memory error/no-hit: Orchestrator los consume como contexto vacío y continúa.

## 16. Respuestas indexadas a las 60 preguntas

1. **Secuencia:** límites→lock→cache/retry→descanso→Behavior→Triage→Memory→
   decisión→guards→DTO→ejecución→WM→conversación→cache→notif.→Identity→rest.
2. **Early returns:** tres: hit, retry manual y fallo Behavior; sus omisiones
   están en §6.
3. **Antes de Triage:** ya cambió estado/tarea/personalidad/percepción y pudo
   finalizar descanso.
4. **Behavior antes de intención:** sí.
5. **Estado canónico:** el `StateEngine` del BehaviorCore productivo; Task y
   Personality también pertenecen a ese Behavior.
6. **Varias instancias:** sí al importar/tests, pero una rama productiva; dentro
   de ella hay un solo TaskContext vigente.
7. **Globales legacy productivos:** no se hallaron consumidores.
8. **Mutadores:** tabla §4.
9. **Orden:** Behavior primero; Memory/decisión; Identity al final.
10. **Lecturas alrededor de mutaciones:** Triage y capas leen post-Behavior y
    pre-Identity; `event_start` lee pre-lock; `event_end` post-lock.
11. **Múltiples mutaciones por evento:** sí, Behavior + Identity + recursiones.
12. **Retries/hits:** hit no repite; retry sin cache sí; retry manual repite solo
    save.
13. **Cobertura idempotente:** respuesta y side effects solo tras commit de cache;
    no es durable ni reconcilia outcome.
14. **Contenido cache:** solo AccionSaliente.
15. **Creación/reutilización:** al final normal; lookup al inicio bajo lock per-ID.
16. **Cache antes de side effects finales:** sí, antes de notif./Identity/rest.
17. **Acción física repetible:** sí ante fallo post-ejecución/pre-cache,
    eviction/restart o ID diferente.
18. **Protección lock:** cubre todo el flujo same-ID; no recursos compartidos de
    IDs distintos.
19. **Ciclo lock:** se crea al primer usuario, contador incluye waiters, se elimina
    al último `finally`.
20. **Races:** sí en Behavior/Task/Personality/caches/aliases/notif.; Chroma y
    Conversation tienen locks parciales.
21. **Serialización:** por transacción, no global.
22. **Threadpool:** `/evento` ejecuta Orchestrator en worker threads; estado y
    notifications se leen desde event loop.
23. **Locks durante llamadas largas:** per-ID cubre LLM/red/Chroma/tools, pero no
    bloquea IDs distintos.
24. **Deadlock:** no se demostró ciclo de adquisición.
25. **Liberación ante excepción:** sí para lock transaccional y locks con context
    manager inspeccionados.
26. **Side effects pre-validación:** estado/tarea/personality/percepción,
    retrieval, episodio, potencial consolidación y alias de outcome.
27. **Post-AccionSaliente:** candidato alias, ejecución, WM, conversación, cache,
    notif., Identity/rest.
28. **Inválida deja efectos:** sí; probe §11.
29. **Decidir/despachar/ejecutar/confirmar:** son fases distintas; §10.
30. **Asume éxito antes de outcome:** Audit dice pending para dispatch, pero
    Identity usa validez de decisión como success.
31. **Correlación resultado:** Task ninguna; alias exacta o fallback permisivo;
    no ledger.
32. **Resultado atrasado:** muta tarea/estado vigentes y puede aprobar alias.
33. **Dos resultados:** con IDs de evento distintos duplican estímulo; alias puede
    consumir otro candidato.
34. **Nunca llega:** acción queda despachada/pending, alias pendiente y Task sin
    outcome.
35. **Desconocida:** Behavior la procesa; alias puede emparejar candidato reciente.
36. **Ramas WorkingMemory:** todas tras ejecución salvo early returns; MemoryManager
    ignora solo `none/sin_accion`.
37. **WM antes de outcome:** sí, registra dispatch/pending inmediatamente.
38. **Persistencia:** episodio en todo flujo post-Triage; conversación/hechos al
    hablar; save manual; aliases; OracleMemory; consolidación eventual.
39. **Retry duplica persistencia:** sí si no hubo cache; manual save tiene receipt
    especial.
40. **Antes/después decisión:** episodio antes; conversación/alias/Oracle y WM
    según sus puntos descritos.
41. **Helpers con efectos ocultos:** tick, detector logs, constellation lazy,
    consolidación y hechos relacionales.
42. **AuditTrace pasivo:** funcionalmente sí; snapshot no es atómico entre objetos
    bajo concurrencia.
43. **Callback circular:** no encontrado.
44. **ToolPolicy:** filtra únicamente.
45. **Guard terminal cambia acción:** sí, a `hablar`, incluso inválidos.
46. **Audit vs ejecución:** decision audit usa acción post-guard; execution audit
    usa retorno real de `_gestionar_ejecucion`.
47. **Salto de guards:** ninguna rama nominal; retry manual los aplica; cache usa
    resultado ya guardado.
48. **`valido=False`:** sanitiza a none, o terminal lo convierte a hablar válido
    conservando error.
49. **Inválido parece normal:** sí en terminal y siempre pierde outcome en DTO
    público.
50. **Errores a API:** Behavior→200/no acción; fallbacks→200/acción; excepción no
    capturada→500 con detalle heredado.
51. **Excepción deja estado parcial:** sí, sin rollback.
52. **Rollback:** no existe.
53. **Memory falla:** Persona/Motor continúan con contexto vacío; episodio puede
    faltar y aun cachearse.
54. **Persona falla:** fallback hablar válido con error.
55. **Motor falla:** Reasoning interno se captura; diagnóstico/proceso puede
    propagarse.
56. **Oracle falla:** varias capturas y fallback activo/social; puede terminar
    hablado con error.
57. **Reasoning falla:** ActionResponse none inválido y flujo continúa.
58. **Fallback y side effects:** cambia respuesta y también condiciona WM,
    conversación, cache, notif. e Identity; preeffects ya ocurrieron.
59. **Notificaciones:** cache hit evita duplicado, pero retry sin cache puede
    duplicar y snapshot/clear puede perder nuevas.
60. **IdentityState:** Orchestrator lo crea con StateEngine de Behavior; Persona,
    Motor, esfuerzo y Orchestrator lo leen/usan; Orchestrator lo muta al final.

## 17. Límites para bloques posteriores

- **Bloque 4 Memory:** locks/dedupe/consolidación, eliminación, calidad de
  retrieval, error/no-hit y callbacks de proveedores. No abrir DB activa.
- **Fase de fixes:** ledger/idempotencia, lifecycle de Task, correlación outcome,
  atomicidad del Core, envelopes de capa, confirmación y DTO público.
- **Capa 0:** calidad del RF queda fuera; esta auditoría solo demuestra que su
  input ya contiene estado post-Behavior y que terminal puede adaptar ruta.
- **Capa 1/Unity:** transporte y materialización física quedan fuera; aquí solo se
  mapeó dispatch/resultado.
- **DevelopmentalCore:** no debe aprender de `ActionResponse` perdido,
  `despachada`, TaskContext vigente ni IdentityState.success como si fueran un
  outcome único.
- **AuditTrace:** permanece v1; no convertirlo en event sourcing ni ledger.

## 18. Pruebas y regresión

| Comando/check | Resultado |
|---|---|
| `python docs/audit/probes/core_orchestrator_probe.py` | PASS: 12/12 grupos |
| safety barriers del probe | red y mutaciones/SQLite de Chroma activa bloqueadas |
| `python -m compileall -q core ai_engine memory tools api terminal_client docs/audit/probes` | PASS |
| `python docs/audit/probes/regression_probe.py` | 160 PASS, 1 KNOWN_XFAIL, 0 nuevos |
| hash de Chroma activa en regression harness | 3/3 archivos sin cambio |
| `git diff --check` | PASS |
| escaneo de cambios funcionales | solo este informe y un probe nuevo |

El único fallo conocido continúa siendo
`memory.test_chromadb_flow.test_eliminacion`, ya clasificado en Bloque 2. La
regresión ejecutó 161 unidades en 25 módulos, sin import failures ni intentos de
red inexplicados.

Evidencia estructurada del probe nuevo:

- cualquier comando reemplaza task; outcome tardío cae en `hola`;
- reset conserva umbral; quinto fallo amplifica cuatro eventos;
- globales legacy son distintos; Audit snapshot pasivo;
- same-ID secuencial/concurrente procesa una vez;
- IDs distintos se solapan en Behavior;
- fallo pre-cache repite execution/Behavior/episodio;
- fallo Behavior deja mutación parcial y retry;
- inválido deja efectos; terminal lo vuelve `hablar`;
- Memory falla abierto;
- Motor no capturado repite preeffects;
- alias desconocido/duplicado consume candidatos recientes;
- ToolPolicy filtra; confirmación detiene antes del executor;
- cache puede correr contra eviction; notificación puede perderse.

## 19. Archivos, Git y gate de salida

Archivos nuevos del bloque:

- `docs/audit/block_03_core_orchestrator.md`
- `docs/audit/probes/core_orchestrator_probe.py`

No se modificó ningún archivo funcional. Los cambios runtime preexistentes y
excluidos del commit permanecen:

```text
 M lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin
 M lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin
 M lazzaro_data/chroma.sqlite3
```

Gate:

- [x] ownership y orden real reconstruidos;
- [x] TaskContext efectivo y outcomes tardíos reproducidos;
- [x] early returns y side effects por rama documentados;
- [x] same/different ID y concurrencia deterministas;
- [x] frontera de cache/retry/ejecución reproducida;
- [x] errores/fallbacks/rollback mapeados;
- [x] candidatos históricos resueltos;
- [x] 0 P0; P1/P2/P3 clasificados;
- [x] no fixes ni cambios funcionales;
- [x] AuditTrace v1 intacto;
- [x] Chroma activa intacta durante probes;
- [x] Bloque 4 no iniciado;
- [x] no push.
