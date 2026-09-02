# Fase 2 · Bloque 1 — Auditoría BOOT / CONFIG

Fecha: 2026-08-30. Código examinado: `cc01701`, rama `codex/stabilization/pre-capa2`.
Estado del bloque: auditoría completada; recomendaciones pendientes de aprobación.
No se aplicaron fixes funcionales, cambios de dependencias, credenciales ni AuditTrace.

## Resumen ejecutivo

El arranque normal y el cierre normal funcionan en una composición real de FastAPI,
BehaviorCore, MemoryManager, ReasoningEngine y Orchestrator, con proveedor simulado
y Chroma temporal. La suite conserva **160 PASS, 1 fallo histórico y 0 fallos nuevos**.

No se demostró un P0 que hiciera inseguro continuar esta auditoría. Hay cuatro P1
que conviene resolver antes de confiar en Capa 2: salida excepcional del lifecycle,
ownership incompleto durante construcción parcial, pérdida del cliente nativo de
Chroma si falla la colección y ausencia de timeout finito en Gemini durante boot.
Las condiciones se reprodujeron de forma aislada; no se afirma un incidente real,
una corrupción de datos ni un bloqueo real del proveedor.

El perfil cargado desde el cwd del repositorio sigue siendo **solo Gemini**, orden
`gemini`, fallbacks deshabilitados y ninguna otra credencial de proveedor presente.
La inspección no representa el entorno de un servidor que pudiera estar ejecutándose
en otro proceso. No se hizo una llamada real para comprobar cuota o modelo remoto.

Hay 19 registros OPEN: **4 P1, 13 P2 y 2 P3**. Uno de los P3 conserva explícitamente
un comportamiento intencional ya documentado, no un bug: la selección del primer
proveedor válido cuando se deshabilita la cadena de fallbacks.

## Alcance, evidencia y protección de datos

Se siguieron dependencias únicamente para entender construcción, configuración,
disponibilidad y cierre. No se auditó la lógica interna de Memory, Triage, Persona,
Motor u Oracle. Fases 0/1 permanecen cerradas; no existe OBS-009 en este trabajo.

Etiquetas utilizadas:

- **IMPLEMENTADO**: contrato visible en el código o metadata instalada.
- **OBSERVADO**: conducta reproducida por un probe; se indica si hubo fault injection.
- **INFERIDO**: consecuencia razonada, todavía no observada como incidente real.
- **HIPÓTESIS**: requiere otra prueba o bloque; no se presenta como bug confirmado.

Los probes son herramientas standalone para un proceso nuevo. No deben importarse
en el runtime: instalan barreras y handlers de logging solo para el proceso de prueba.
Los probes BOOT/CONFIG usan fakes, transporte HTTPX en memoria y directorios temporales;
no crean eventos cognitivos ni necesitan ampliar AuditTrace. La regresión sí ejecuta
los tests cognitivos/AuditTrace sintéticos existentes, siempre en aislamiento. Se
capturan o descartan logs/errores sin publicar sus textos. Las credenciales sintéticas
solo producen booleanos en evidencia.

La regresión bloquea red Python, subprocess y acceso/escritura a la Chroma activa;
verifica hashes antes/después de los tres archivos runtime previamente modificados.
Los probes BOOT/CONFIG no hacen peticiones a proveedores. La excepción de conexión
permitida es el `socketpair` interno de asyncio en Windows, no un endpoint HTTP local.
Estas barreras Python no son un firewall de sistema operativo para código nativo.

## 1. Mapa real del arranque

Fuentes principales: [main.py](C:/Users/JAIRO/Entidades/Lazzaro/main.py:10),
[Settings](C:/Users/JAIRO/Entidades/Lazzaro/config/settings.py:8) y
[constructor de Orchestrator](C:/Users/JAIRO/Entidades/Lazzaro/core/orchestrator.py:49).

```text
Importar main
  ├─ importar módulos / SDKs de todos los clientes, aunque algunos estén inactivos
  ├─ core.logger configura logging global y abre logs/lazzaro.log si no hay handlers
  ├─ instancias globales legacy de StateEngine / PersonalityTraits / BehaviorCore
  ├─ imports de 4 módulos simulated → decoradores registran herramientas
  └─ create_app() → FastAPI + rutas; app.state.{orchestrator, ai_client} = None

Entrar al lifespan
  ├─ resetear app.state y referencias locales de AI / MemoryManager
  ├─ Settings()                                  [fuera del try de startup]
  └─ try
       BehaviorCore()
         └─ estado, personalidad, psicología, constelación y tarea RAM
       ChromaClient(settings.CHROMA_DB_PATH, "experiencias")
       MemoryManager(chroma_client)
         └─ stores/retriever/encoder/summarizer/colas y locks
       describe_ai_backend(settings) → log de candidatos solicitados
       build_ai_client(settings)
         ├─ resolver orden; omitir ValueError de candidatos no utilizables
         ├─ primer válido si LLM_FALLBACKS_ENABLED=false
         └─ cliente único o FallbackAIClient propietario de varios
       app.state.ai_client = ai_client
       ai_client.health_check()                   [síncrono; False NO aborta]
       ReasoningEngine(ai_client)
         └─ builders, plantillas, policy manager, router y parser
       ToolRegistry.listar_nombres()              [NO crea el registro aquí]
       Orchestrator(behavior, memory, reasoning, ToolRegistry)
         ├─ TriageRouter: carga joblib del repositorio o degrada a heurística
         ├─ ToolPolicy, MotorEngine; conecta callbacks de la memoria prestada
         ├─ IdentityState comparte BehaviorCore.state_engine
         ├─ SubjectiveEffortCalculator y PersonaEngine
         ├─ LLMGenerationAdapter comparte AI y policy manager de ReasoningEngine
         ├─ OracleEngine
         ├─ OracleMemory: NUEVO ChromaClient("oracle_cache"), mismo db_path
         ├─ OracleClient: settings del AI; no abre HTTP en el constructor
         └─ caches/locks/deques y AuditTraceJSONLWriter (sin abrir archivo aún)
       app.state.orchestrator = orchestrator
       log "Sistemas nominales"
       yield                                      [FastAPI ready]

Salida normal
  Orchestrator.close() → OracleMemory.close() → Chroma oracle_cache.close()
  MemoryManager.close() → Chroma experiencias.close()
  AI.close() → cliente concreto o hijos de FallbackAIClient
  app.state conserva las referencias, ya cerradas
```

El `except` de startup cierra MemoryManager y luego AI si sus referencias locales
ya fueron asignadas; después relanza. No dispone de todos los objetos parcialmente
construidos. El bloque posterior a `yield` no está protegido por `finally`.
El comando directo inicia Uvicorn en `0.0.0.0:8000`; no se abrió un servidor real
ni se modificó esa configuración durante la auditoría.

## 2. Ownership y lifecycle

| Componente | Creado por | Propietario / dependencias compartidas | Cierre | Comportamiento ante fallo |
|---|---|---|---|---|
| Settings | `main.lifespan` | Local de main; se pasa a factory y cliente | Sin recurso persistente | Validación fatal antes del try; sin rollback necesario de componentes de startup |
| BehaviorCore | main | Orchestrator lo usa prestado; crea sus helpers RAM | Sin close propio | Constructor que lanza aborta startup |
| Chroma `experiencias` | main | Transferido de hecho a MemoryManager; stores comparten el mismo wrapper | `MemoryManager.close()` | Inicialización interna degrada a `disponible=False`; construcción parcial puede perder handle nativo |
| MemoryManager | main | Propietario operativo del Chroma recibido; prestado a Orchestrator | main lo cierra | Si falla su constructor, main no cierra el Chroma ya creado |
| AI único / cadena | factory, invocada por main | main es propietario; Reasoning y adapter lo comparten | main → `AI.close()` | Sin candidatos válidos: fatal; factory puede perder clientes anteriores al fallar otro constructor |
| Gemini / compatible / Ollama | factory | Cada concreto posee su SDK/HTTP persistente | `close()` captura errores y pone `client=None` | Segundo close tolerado; si cerrar falla se pierde la referencia para reintentar |
| FallbackAIClient | factory si hay varios | Posee lista de concretos; comparte Settings | Cierra cada hijo; contiene fallos individuales | Health termina en primer éxito, no valida todos los hijos |
| ReasoningEngine | main | AI prestado; helpers y plantillas propios | Sin close persistente | Plantilla ausente → cadena vacía y warning; otros errores de lectura/constructor pueden abortar |
| ToolRegistry | import de clase/módulos | Diccionario global al proceso; apps comparten registro | No necesita close | Import fallido aborta antes de lifespan; lista vacía no bloquea startup |
| Orchestrator | main | Behavior/Memory/Reasoning/registro prestados; posee hijos nuevos | Cierra solo OracleMemory, no dependencias prestadas | Si falla tras crear OracleMemory, main no recibe un Orchestrator para cerrarlo |
| IdentityState / LLM adapter | Orchestrator | Comparten el estado canónico y el AI/policy manager, respectivamente | Sin recurso externo propio | Fallo de constructor abortaría Orchestrator |
| OracleMemory | Orchestrator | Posee segundo wrapper Chroma sobre la misma DB, colección distinta | `Orchestrator.close()` | DB no disponible degrada; wrapper idempotente |
| OracleClient | Orchestrator | Guarda configuración; no posee cliente HTTP duradero | Cada consulta usa `with httpx.Client(...)` | `configurado` significa presencia de key, no conectividad; no se consulta en este bloque |
| AuditTraceJSONLWriter | Orchestrator | Writer propio; contrato v1 sin cambios | Abre/cierra FD en cada append | Fail-open ya cubierto por Fase 1; boot no fuerza stages cognitivos |
| Logger | import de `core.logger` | Handlers globales al proceso | Shutdown de `logging`, no de main | FileHandler/mkdir pueden abortar import; handler raíz previo evita configurar logfile de la app |

Evidencia de identidad en smoke real: `reasoning.ai_client is app.state.ai_client`,
`llm_adapter.ai_client` es el mismo objeto, `identity_state.state_engine` es el del
BehaviorCore y ambos stores comparten `memory.client`. Oracle usa otro wrapper con
el mismo path. Se observaron 14 herramientas registradas.

No hay doble cierre incorrecto demostrado entre las dos colecciones del boot normal.
Con Chroma **1.5.9**, cerrar el primer wrapper del mismo path no detiene al segundo;
cerrar ambos y repetir close produce una sola llamada nativa a `System.stop()`.
No se extrapola esto a versiones antiguas que usan `_system.stop()` como fallback.

## 3. Configuración efectiva y límites de disponibilidad

La fotografía de `config_probe.live_profile` carga Settings desde el repositorio,
con el entorno de ese proceso de auditoría. No imprime `.env`, claves, hashes de
claves ni valores arbitrarios del entorno.

| Comprobación | Resultado observado |
|---|---|
| `Settings()` carga y coincide con `.env` explícito del repo | Sí |
| Perfil con entorno del probe coincide con `.env` + defaults | Sí; cero campos diferentes |
| Provider / orden / fallbacks | `gemini` / solo `gemini` / `false` |
| Credenciales presentes | Gemini: sí; Groq, xAI, Kimi, DeepSeek remoto, Oracle: no |
| Modelo Gemini coincide con default de Settings | Sí: default del código `gemini-3.1-flash-lite` |
| Chroma resuelve al default del repositorio | Sí, cuando cwd es el repo |
| Overrides de campos Settings en entorno del probe | Ninguno |
| 16 variables adicionales inspeccionadas de SDK/proxy/certs | Todas ausentes |
| Servidor vivo / disponibilidad real de modelo, key o cuota | No inspeccionados / no probados |

Precedencia reproducida: **argumentos de Settings → variables del proceso → `.env`
del cwd → defaults**. `.env` no se exporta automáticamente al entorno del proceso.
El alias histórico `DEEPSEEEK_API_KEY` se acepta; gana el nombre canónico si ambos
están presentes. Las claves vacías se normalizan a `None`.

### Fuentes y defaults que no deben confundirse

| Tema | Contratos encontrados | Clasificación |
|---|---|---|
| Proveedor primario | Settings: `gemini`; factory ante objeto sin atributo: `ollama` | Default duplicado para consumidores duck-typed; main sí pasa Settings completo |
| Modelo Gemini | Settings: `gemini-3.1-flash-lite`; constructor sin atributo: `gemini-2.5-flash`; descripción sin atributo: `gemini` | Inconsistencia legacy, no cambio del perfil actual; CONFIG-005 |
| Fallbacks | Settings default `true`; `.env.example` explícitamente `false` y orden Gemini | Override intencional del perfil, no contradicción funcional |
| Orden explícito | Reemplaza al primario; flag false elige primer cliente válido | Intencional y documentado; CONFIG-003 |
| Modelos DeepSeek | `DEEPSEEK_MODEL` es modelo local Ollama; `DEEPSEEK_API_MODEL` es remoto | Dos significados explícitos, no confundirlos ni reactivar remoto |
| Oracle | Con Settings hereda key/model de Groq si no hay Oracle; sin Settings lee `os.environ` directamente | Contrato duplicado; fallback de key intencional, CONFIG-005 |
| URL Oracle | Settings ya tiene URL Oracle default, por lo que una URL Groq personalizada no la reemplaza | Diferencia reproducida, CONFIG-005 |
| HTTP compatible / Ollama | Timeouts 45 s / 120 s; Oracle 15 s; override por Settings | Valores por proveedor, no un único presupuesto global de boot |
| Gemini | No se suministra timeout al SDK; SDK instalado termina pasando `None` a HTTPX | CONFIG-001 |
| Paths | `.env` y Chroma default dependen del cwd; logs, prompts y joblib se anclan al repo | CONFIG-002 |
| Entorno del SDK | `GOOGLE_GEMINI_BASE_URL` puede alterar endpoint sin cambiar Settings | Capacidad intencional del SDK no descrita por Settings; CONFIG-009 |

Las credenciales ausentes impiden construir los proveedores remotos retirados;
tener defaults de modelo no los hace utilizables. Ollama no forma parte del orden
activo ni fue contactado. Presencia de key Gemini no prueba que siga autorizada,
tenga cuota o que su modelo esté disponible. Ningún probe infiere eso.

### Qué comprueba realmente cada health check

| Cliente | Operación implementada / reproducida | Qué NO demuestra |
|---|---|---|
| Gemini | Generación breve; busca substring `ok`; cuatro intentos y sleeps si hay excepciones | No es ping gratuito ni valida capacidad futura; sin deadline finito actual |
| Compatible OpenAI | Generación breve; busca substring `ok` | No es solo construcción; puede consumir cuota; no valida todas las tareas |
| Ollama | GET de tags y presencia del nombre del modelo | No ejecuta generación ni prueba que el modelo cargue correctamente |
| FallbackAIClient | Primer hijo cuyo health sea verdadero | No valida todos; `provider_name` permanece primario y `last_provider_used` no registra ese health |
| OracleClient | No health de boot; `configurado` consulta presencia de key | No comprueba endpoint, autenticación ni cuota |
| Chroma | `disponible` refleja colección asignada | main no consulta esa propiedad como gate de readiness |

El substring acepta también una respuesta sintética `not ok`: es un contrato débil
de respuesta, pero sí confirma que hubo texto del backend en ese intercambio. No se
infiere indisponibilidad de un proveedor solo por esa frase. El coste de health es
**posible** por diseño; esta auditoría utilizó fakes y no consumió generación remota.

## 4. Matriz de fallo y estado residual

Los diez puntos principales se inyectaron en la función `main.lifespan` real. Los
componentes frontera eran fakes salvo en los smokes de composición y de Chroma.

| Etapa / condición | Fatal o degradada | Cleanup observado / estado resultante |
|---|---|---|
| Settings lanza | Fatal, fuera del try | Sin recursos centrales creados; ambos app.state None; sin log crítico de ese except |
| BehaviorCore lanza | Fatal | Log crítico; ambos state None |
| Chroma constructor lanza hacia main | Fatal | Aún no hay MemoryManager; state None; distinto del error interno capturado por Chroma |
| PersistentClient o colección fallan dentro del wrapper | Degradada | `disponible=False`; composición real continúa y `/estado` devuelve 200; BOOT-003 si ya había handle |
| MemoryManager constructor lanza | Fatal | Chroma ya creado recibe cero cierres; BOOT-002 |
| Descripción de backend lanza | Fatal | Se cierra memoria/Chroma; AI todavía None |
| Factory lanza | Fatal si no entrega cliente | Memoria se cierra; main no puede cerrar recursos parciales ocultos en factory |
| Health lanza fuera del cliente | Fatal | Memoria y AI se cierran; state retiene referencia AI cerrada |
| Health devuelve False | Degradada intencional | Sigue Reasoning/Orchestrator; warning seguido de log nominal |
| Reasoning, listado de herramientas u Orchestrator lanzan | Fatal | Se cierran memoria y AI asignados; state.orchestrator None, state.ai_client residual |
| Registro vacío | No aborta | Flujo llega a ready y al log nominal |
| OracleClient falla después de crear OracleMemory | Fatal | Main limpia memoria principal/AI, no OracleMemory parcial; BOOT-002 |
| Un close lanza durante rollback | Fatal | Sustituye la excepción propagada y puede omitir cierres posteriores; original queda como contexto Python/log previo |
| Salida normal del lifespan | Correcta en smoke | Oracle cache → memoria principal → AI; referencias de state permanecen |
| Excepción/cancelación inyectada por el contexto lifespan | Omite shutdown | Ninguno de los cuatro close del harness se ejecuta; BOOT-001 |
| Un close lanza durante shutdown | Interrumpe shutdown | No ejecuta los cierres posteriores; BOOT-001 |

Los clientes concretos actuales contienen muchas excepciones ordinarias de close;
la matriz que fuerza un close a lanzar verifica robustez del **contrato del lifecycle**,
no afirma que cualquier fallo de disco/red salte esas defensas hoy.

Una excepción HTTP 500 normal no se inyecta automáticamente en el lifespan. Asimismo,
el 200 obtenido forzando un request ASGI local después de shutdown solo demuestra
state residual; **no** demuestra que Uvicorn siga sirviendo tras apagarse.

## 5. Respuestas trazables a las veinte preguntas

| # | Respuesta / evidencia principal |
|---|---|
| 1 | Secuencia completa en §1; `clean_fake_lifecycle` y `real_app_smoke` la verifican. |
| 2 | Orden y propietarios en §1–2; ToolRegistry se puebla por imports, no se construye en ese paso de lifespan. |
| 3 | AI y estado canónico compartidos; stores comparten Chroma; OracleMemory crea segundo wrapper. Identidades comprobadas en smoke. |
| 4 | Matriz §4: diez etapas, construcción parcial de Orchestrator/Chroma y cinco fallos de cleanup. |
| 5 | Validación/constructores que propagan son fatales; health False, Chroma interno, modelo Triage ausente y plantilla inexistente permiten degradación. |
| 6 | Sí: health False y dos colecciones Chroma no disponibles llegaron a ready/log nominal; BOOT-006. |
| 7 | Sí para la carga desde cwd repo del probe: igualdad completa `.env` + defaults. No se inspeccionó entorno de un servidor vivo; §3. |
| 8 | Tabla de defaults/fuentes §3; diferencias legacy y Oracle en CONFIG-005, políticas intencionales en CONFIG-003. |
| 9 | Los retirados no tienen keys ni pertenecen al orden activo; defaults de modelo no habilitan uso. Disponibilidad remota real no probada. |
| 10 | Sí: NaN/Infinity, URL/modelo/path vacíos e inválidos sobreviven a Settings; CONFIG-004. Factory/cliente o petición pueden fallar después. |
| 11 | Normal same-path e idempotencia Chroma pasan; recursos parciales sin close y cache global cross-path fallan; BOOT-002/003/004/005. |
| 12 | No en todos los casos: rollback de los objetos asignados funciona; ownership parcial y close que lanza no quedan cubiertos; BOOT-001/002/003/004. |
| 13 | Orchestrator queda None; AI puede seguir referenciado, cerrado o sin cerrar si cleanup falló; BOOT-007 y matriz §4. |
| 14 | Las 11 dependencias declaradas cubren los imports externos estáticos del grafo BOOT inspeccionado; no fijan entorno reproducible ni extras de herramientas; DEPS-001/002. |
| 15 | 159/159 paquetes del freeze coinciden; ninguno falta/cambió. Solo pip/setuptools adicionales. No hay regresión de dependencias observada. |
| 16 | Canarios alcanzan logs humanos por excepción/cuerpo HTTP/URL; no filtración real demostrada. AuditTrace no recibe boot ni se amplió; CONFIG-006. |
| 17 | Health remoto ejecuta generación; Ollama solo tags; cadena prueba al menos un hijo; Oracle solo configurado. Ninguno equivale a readiness global. |
| 18 | Logger, instancias globales legacy, decoradores de tools y creación de FastAPI ocurren al importar; §6 y BOOT-008. |
| 19 | Sí: `.env` y Chroma dependen del cwd; modelos/prompts/logs no. CONFIG-002. |
| 20 | Sí: nominal después de degradación, descripción de candidatos frente a efectivos y state residual; BOOT-006/007 y CONFIG-007. |

## 6. Intencional, deuda e hipótesis: límites de clasificación

Comportamientos intencionales/positivos que no deben convertirse automáticamente en fixes:

- Tolerar health False y Chroma no disponible está implementado deliberadamente;
  el problema separado es cómo se comunica disponibilidad (BOOT-006).
- La selección del primer cliente válido y prioridad del orden explícito ya están
  documentadas en [arquitectura](C:/Users/JAIRO/Entidades/Lazzaro/docs/arquitectura_completa_lazzaro.md:286).
  CONFIG-003 no autoriza reinterpretar esa política.
- La herencia Oracle → key Groq tiene pruebas existentes. No se reactivó ningún
  proveedor; la deuda de fuentes y documentación está en CONFIG-005.
- El segundo wrapper Chroma es para otra colección del mismo path, no una segunda
  DB accidental en el boot normal. El cierre same-path funciona en la versión instalada.
- Las instancias legacy de [StateEngine](C:/Users/JAIRO/Entidades/Lazzaro/core/state_engine.py:422),
  [personalidad](C:/Users/JAIRO/Entidades/Lazzaro/core/personality.py:140) y
  [BehaviorCore](C:/Users/JAIRO/Entidades/Lazzaro/core/behavior_core.py:606) se crean al
  importar; main crea otro BehaviorCore. Son objetos RAM distintos, no evidencia
  de divergencia del estado canónico utilizado por la composición observada.
- Registro global de herramientas y locks persistentes por path son propiedades
  del proceso. No se demostró impacto funcional de su tamaño; no se propone limpieza
  arquitectónica por motivos de estilo.

Hipótesis o cuestiones fuera de este bloque:

- **HIPÓTESIS**, ligada a BOOT-005: reaperturas/concurrencia entre DB distintas
  podrían empeorar el efecto del cache global. Se observó pérdida de registro y
  ausencia de stop, **no** corrupción ni pérdida de recuerdos; no se ensayó sobre datos activos.
- **HIPÓTESIS**, ligada a CONFIG-006: un proveedor/error real podría reflejar una
  credencial en su mensaje. Los sinks existen, pero no se buscó ni demostró una
  filtración real en logs históricos.
- **HIPÓTESIS**, ligada a DEPS-001: otra resolución de dependencias o Python podría
  romper APIs/pickle. No se hizo reinstall limpio ni prueba con versiones diferentes.
- Calidad de embeddings, heurísticas Triage, reglas/acciones, fallback cognitivo
  y DevelopmentalCore requieren otros bloques. No se extraen conclusiones de sus
  algoritmos a partir de estos smokes de construcción.

## 7. Tickets estables

Todos permanecen **OPEN**. Las recomendaciones son preliminares: no son cambios
aplicados ni autorización para ejecutarlos. P1 sigue el criterio del usuario
(lifecycle/ownership/contrato central no confiable), no una afirmación de incidente.

| ID | Prioridad | Clase | Resumen |
|---|---|---|---|
| BOOT-AUDIT-001 | P1 | Bug confirmado por inyección | Cleanup del lifespan no garantizado |
| BOOT-AUDIT-002 | P1 | Bug confirmado por inyección | Recursos parciales sin propietario que los cierre |
| BOOT-AUDIT-003 | P1 | Bug confirmado por inyección | Chroma pierde cliente nativo al fallar colección |
| BOOT-AUDIT-004 | P2 | Bug confirmado por inyección | Construcción LLM parcial sin rollback |
| BOOT-AUDIT-005 | P2 | Bug confirmado, DBs temporales | Cache global Chroma borra otra DB abierta |
| BOOT-AUDIT-006 | P2 | Mensaje incorrecto / deuda | Nominal/readiness no expresan degradación |
| BOOT-AUDIT-007 | P2 | Deuda de lifecycle | Referencias app.state residuales |
| BOOT-AUDIT-008 | P2 | Fragilidad confirmada por inyección | Error de logfile aborta import |
| CONFIG-AUDIT-001 | P1 | Contrato defectuoso observado | Gemini sin timeout finito |
| CONFIG-AUDIT-002 | P2 | Deuda de configuración | `.env` y DB dependen del cwd |
| CONFIG-AUDIT-003 | P3 | Intencional documentado | Primer proveedor válido; no cambiar política por su nombre |
| CONFIG-AUDIT-004 | P2 | Validación insuficiente confirmada | Valores inválidos sobreviven a Settings |
| CONFIG-AUDIT-005 | P2 | Deuda de fuentes/defaults | Defaults duplicados y configuración Oracle divergente |
| CONFIG-AUDIT-006 | P2 | Hardening, canarios sintéticos | Logs humanos sin redacción en varios límites |
| CONFIG-AUDIT-007 | P2 | Mensaje incorrecto | Candidatos descritos como backend configurado |
| CONFIG-AUDIT-008 | P3 | Bug legacy confirmado | Gemini directo: AttributeError por key None |
| CONFIG-AUDIT-009 | P2 | Deuda de contrato | Configuración ambiental del SDK fuera de Settings |
| DEPS-AUDIT-001 | P2 | Deuda de reproducibilidad | Manifest sin versiones/Python soportado |
| DEPS-AUDIT-002 | P2 | Deuda opcional, no boot | Dependencias hardware/desktop no declaradas |

### BOOT-AUDIT-001 — P1 — OPEN

**Cleanup no garantizado.** **IMPLEMENTADO:** [main.py:92](C:/Users/JAIRO/Entidades/Lazzaro/main.py:92)
y [main.py:100](C:/Users/JAIRO/Entidades/Lazzaro/main.py:100) encadenan cierres sin
contención individual y no envuelven yield en finally. **OBSERVADO:** `exceptional_lifespan_exit`
deja todos los close en cero; `cleanup_failure_matrix` reproduce cinco combinaciones
en las que el error de cierre sustituye al error propagado o salta cierres posteriores.
Impacto **INFERIDO**: salida excepcional/reinicio no confiable. No es una prueba de
cualquier HTTP 500 ni de crash no recuperable del sistema operativo.
Reproducción: `boot_probe.py`, esos dos checks. Recomendación: acordar cleanup garantizado,
independiente por recurso, preservando la causa original; probar cancelación y close fallido.

### BOOT-AUDIT-002 — P1 — OPEN

**Ventanas de ownership durante construcción.** **IMPLEMENTADO:**
[main.py:54](C:/Users/JAIRO/Entidades/Lazzaro/main.py:54),
[rollback](C:/Users/JAIRO/Entidades/Lazzaro/main.py:94) y
[OracleMemory antes de OracleClient](C:/Users/JAIRO/Entidades/Lazzaro/core/orchestrator.py:75).
**OBSERVADO:** fallar MemoryManager deja el Chroma creado con cero close; ejecutar
el constructor real de Orchestrator con fallo en OracleClient deja OracleMemory
sin cierre, aunque main sí cierre memoria principal y AI.
Impacto **INFERIDO**: handles/recursos sin dueño en startup parcial de la ruta activa.
Reproducción: `startup_failure_matrix` / `partially_constructed_orchestrator`.
Recomendación: registrar responsabilidad de cleanup inmediatamente al adquirir cada
recurso y transferirla solo al completar la composición; sin cerrar objetos prestados.

### BOOT-AUDIT-003 — P1 — OPEN

**Handle Chroma descartado en inicialización parcial.** **IMPLEMENTADO:**
[chroma_client.py:101](C:/Users/JAIRO/Entidades/Lazzaro/memory/chroma_client.py:101)
crea PersistentClient; si `get_or_create_collection` falla, el except sustituye
`_client` por None sin cerrar ese handle. **OBSERVADO:** `chroma_partial_construction`
crea cliente nativo fake, falla la colección y obtiene cero close incluso después
de dos cierres del wrapper. Impacto **INFERIDO**: recursos/refcounts nativos vivos
sin referencia recuperable, aunque se acepte operar sin memoria. No se afirma pérdida
de datos. Recomendación: rollback explícito del handle antes de entrar en modo degradado;
gate con colección fallida y exactamente un cierre del recurso adquirido.

### BOOT-AUDIT-004 — P2 — OPEN

**Rollback incompleto de construcción LLM.** **IMPLEMENTADO:**
[factory:26](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/client_factory.py:26) solo captura
ValueError; [compatible:42](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/openai_compatible_client.py:42)
crea HTTP antes de validar URL/modelo/key. **OBSERVADO:** segundo constructor con
RuntimeError deja el primer cliente sin close; modelo vacío del compatible deja
HTTP construido una vez y cerrado cero veces. Impacto: recursos parciales sin cleanup;
los casos reproducidos de múltiples/otros proveedores no son el perfil Gemini-only actual.
Reproducción: `config_probe.factory_partial_construction_failure` y
`compatible_allocation_before_validation`. Recomendación: validar antes de adquirir
y revertir clientes ya adquiridos si la factory no entrega un propietario final.

### BOOT-AUDIT-005 — P2 — OPEN

**Cache global limpiado por refcount de una sola ruta.** **IMPLEMENTADO:**
[helper global](C:/Users/JAIRO/Entidades/Lazzaro/memory/chroma_client.py:83) y
[close](C:/Users/JAIRO/Entidades/Lazzaro/memory/chroma_client.py:298). **OBSERVADO** con
Chroma 1.5.9 y dos DB temporales: cerrar A elimina B del registro global mientras
B sigue abierta; cerrar B después produce cero `System.stop()` para B. El probe
conserva y cierra manualmente ese handle temporal para no dejarlo vivo.
Impacto **INFERIDO**: lifecycle inconsistente con múltiples paths. No afecta al caso
same-path verificado ni demuestra corrupción. Reproducción: `chroma_cross_path_cache`;
control positivo `chroma_shared_path_close`. Recomendación: respetar ownership/refcount
del SDK y probar varias rutas; no limpiar registro global ajeno por cierre local.

### BOOT-AUDIT-006 — P2 — OPEN

**Disponibilidad degradada comunicada como nominal.** **IMPLEMENTADO:**
[main.py:64](C:/Users/JAIRO/Entidades/Lazzaro/main.py:64),
[log nominal](C:/Users/JAIRO/Entidades/Lazzaro/main.py:89) y
[/estado](C:/Users/JAIRO/Entidades/Lazzaro/api/routes.py:82). **OBSERVADO:** health False
permite boot y 200; fallar ambos PersistentClient en la composición real también
produce 200 y log nominal; registro vacío pasa en el harness.
Impacto: log/estado emocional no prueban readiness de todos los componentes.
Degradar es intencional; el defecto es mensaje y contrato de disponibilidad, no
que necesariamente deba abortar todo. Reproducción: `degraded_but_nominal`,
`real_app_smoke`, `real_chroma_degraded_boot`. Recomendación: decidir componentes
obligatorios/opcionales y diferenciar listo/degradado/saludable sin nuevos stages AuditTrace.

### BOOT-AUDIT-007 — P2 — OPEN

**State retiene objetos cerrados o parciales.** **IMPLEMENTADO:** main asigna AI en
[línea 62](C:/Users/JAIRO/Entidades/Lazzaro/main.py:62) y no limpia state en rollback/shutdown.
**OBSERVADO:** tras fallar Reasoning/Orchestrator queda AI referenciado; tras cierre
normal quedan AI y Orchestrator cerrados en app.state. Un request ASGI forzado sin
reiniciar lifespan todavía obtiene 200 de `/estado`.
Impacto **INFERIDO**: estado ambiguo para reutilización de app/pruebas; no evidencia
de Uvicorn sirviendo después de shutdown. Reproducción: `clean_fake_lifecycle`,
`startup_failure_matrix`, `real_app_smoke`. Recomendación: explicitar estado de lifecycle
y limpiar referencias al completar rollback/shutdown, según política aprobada.

### BOOT-AUDIT-008 — P2 — OPEN

**Logging puede abortar import antes del lifecycle.** **IMPLEMENTADO:**
[logger.py:29](C:/Users/JAIRO/Entidades/Lazzaro/core/logger.py:29) abre directorio/archivo
sin manejo de error; se ejecuta al [importar](C:/Users/JAIRO/Entidades/Lazzaro/core/logger.py:47).
**OBSERVADO:** FileHandler con PermissionError impide ejecutar ese módulo aislado;
handler raíz existente evita configurar el logfile de la app.
Impacto **INFERIDO**: permiso/disco/host de logging puede impedir el boot o cambiar
dónde se registran mensajes. No se modificaron permisos ni logs reales.
Reproducción: `logger_import_and_existing_handler`. Recomendación: acordar fail-fast
o fallback de logging y su ownership con el host; probarlo explícitamente.

### CONFIG-AUDIT-001 — P1 — OPEN

**Gemini sin deadline finito.** **IMPLEMENTADO:**
[Gemini constructor](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/gemini_client.py:35) no
envía `http_options`; [health](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/gemini_client.py:123)
se invoca síncronamente desde main. **OBSERVADO:** SDK google-genai 2.6.0 real con
MockTransport recibe timeout None y construye request con connect/read/write/pool
todos None. El transporte custom no altera el argumento de timeout que pasa el SDK.
Impacto **INFERIDO**: proveedor que no responde puede retener boot indefinidamente,
sin alcanzar el warning por False. No se provocó una espera infinita real.
Reproducción: `gemini_installed_sdk_timeout`; `gemini_health_retry_contract` confirma
cuatro intentos/sleeps cuando sí hay excepciones. Recomendación: presupuesto finito
de boot/health y generación, timeouts explícitos y gate de finalización acotada.

### CONFIG-AUDIT-002 — P2 — OPEN

**Config/path ligados al cwd.** **IMPLEMENTADO:**
[env_file](C:/Users/JAIRO/Entidades/Lazzaro/config/settings.py:62),
[DB default](C:/Users/JAIRO/Entidades/Lazzaro/config/settings.py:58) y
[resolve](C:/Users/JAIRO/Entidades/Lazzaro/memory/chroma_client.py:46).
**OBSERVADO:** dos cwd temporales cargan perfiles distintos o pierden la key sintética
del archivo; el perfil actual desde repo es consistente.
Impacto **INFERIDO**: otro launcher podría usar defaults o una DB distinta mientras
assets siguen anclados al repo. No se probó un fallo de una invocación soportada
distinta de `python main.py` desde repo, por eso P2.
Reproducción: `precedence_and_cwd`. Recomendación: explicitar contrato de directorio
base y configuración/persistencia; probar launchers admitidos antes de cambiar paths.

### CONFIG-AUDIT-003 — P3 — OPEN

**Registro de política intencional, no bug.** **IMPLEMENTADO/OBSERVADO:**
[factory](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/client_factory.py:34) usa el primer
cliente válido con fallbacks false; orden explícito reemplaza primario.
La [documentación existente](C:/Users/JAIRO/Entidades/Lazzaro/docs/arquitectura_completa_lazzaro.md:286)
lo define así. Reproducción: `disabled_fallback_selection` / `explicit_order_overrides_primary`
con credenciales sintéticas. Impacto: quien interprete el nombre como prohibición
absoluta de selección alternativa podría confundirse; el perfil actual solo permite Gemini.
Recomendación: preservar política; cualquier semántica más restrictiva necesita
decisión expresa. OPEN es registro para aceptación de esa decisión, no deuda funcional
que obligue a reescribir el selector.

### CONFIG-AUDIT-004 — P2 — OPEN

**Validación insuficiente.** **IMPLEMENTADO:**
[Settings](C:/Users/JAIRO/Entidades/Lazzaro/config/settings.py:22) y validador
[timeout](C:/Users/JAIRO/Entidades/Lazzaro/config/settings.py:99) solo rechazan `<=0`.
**OBSERVADO:** los tres timeouts aceptan NaN/+Infinity (seis casos); se admiten puerto
-1, tokens -1, temperatura NaN, modelo/path vacíos, URL no HTTP y orden desconocido.
Otros inválidos sí dan ValidationError: provider desconocido, timeout cero/negativo,
puerto no numérico y boolean no válido.
Impacto **INFERIDO**: error tardío/cliente dependiente; Settings válido no garantiza
uso válido. Reproducción: `invalid_values_survive` y control `invalid_validation`.
Recomendación: definir rangos, finitud, cadenas/URLs y validación cruzada; no cambiar
defaults para ocultar avisos estáticos.

### CONFIG-AUDIT-005 — P2 — OPEN

**Fuentes y defaults duplicados, especialmente Oracle.** **IMPLEMENTADO:**
[Settings](C:/Users/JAIRO/Entidades/Lazzaro/config/settings.py:12),
[factory](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/client_factory.py:107),
[Gemini](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/gemini_client.py:32),
[Oracle](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/oracle_client.py:32) y
[.env.example](C:/Users/JAIRO/Entidades/Lazzaro/.env.example:12).
**OBSERVADO:** `oracle_settings_split` reproduce herencia de key Groq, URL Groq
personalizada no heredada por Settings, y constructor directo sin carga de `.env`
que admite timeout cero. Defaults legacy distintos constan en tabla §3; main pasa
Settings completo, por lo que no activan esas diferencias en el perfil actual.
Impacto: fuentes/validación no uniformes y documentación parcial; heredar la key
Groq es intencional y ya testeado, no un hallazgo de reactivación actual.
Recomendación: definir precedencia común y excepciones Oracle; alinear descripción
y defaults tras aprobación, sin modificar automáticamente políticas existentes.

### CONFIG-AUDIT-006 — P2 — OPEN

**Sinks humanos sin redacción.** **IMPLEMENTADO:**
[Gemini:110](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/gemini_client.py:110),
[compatible:133](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/openai_compatible_client.py:133),
[Ollama:33](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/ollama_client.py:33) y
[main:93](C:/Users/JAIRO/Entidades/Lazzaro/main.py:93).
**OBSERVADO:** canario sintético llega al logger mediante excepción, cuerpo HTTP 401,
userinfo de URL y error crítico con traceback. Los textos quedan solo en memoria.
Impacto **INFERIDO**: un dato sensible reflejado en esas entradas podría acabar en log
humano. **No** se observó credencial real filtrada ni se probó que el SDK real incluya
una key en errores; no es un P0 demostrado. AuditTrace ya sanitiza su propio contrato,
pero no protege estos sinks humanos.
Reproducción: `secret_log_sinks` / `startup_log_canary`. Recomendación: hardening y
pruebas sintéticas de redacción en esos límites, sin rotaciones ni revisar/exportar
logs privados durante este bloque.

### CONFIG-AUDIT-007 — P2 — OPEN

**Descripción de candidatos no equivale a backend efectivo.** **IMPLEMENTADO:**
[describe_ai_backend](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/client_factory.py:96)
se ejecuta [antes de construir](C:/Users/JAIRO/Entidades/Lazzaro/main.py:60).
**OBSERVADO:** describe Gemini aunque se omita por falta de key y solo se construya
Groq sintético; tampoco representa el corte tras primer válido.
Impacto: mensajes pueden confundir configuración solicitada con disponibilidad
construida. No afecta a la selección efectiva Gemini-only verificada aquí.
Reproducción: `disabled_fallback_selection`. Recomendación: distinguir candidatos,
omisiones y cliente/cadena resultante en el contrato de mensajes de boot.

### CONFIG-AUDIT-008 — P3 — OPEN

**Error legacy en constructor directo Gemini.** **IMPLEMENTADO/OBSERVADO:**
[Gemini:28](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/gemini_client.py:28) llama strip
sobre None y lanza AttributeError antes del ValueError previsto cuando se construye
directamente con Settings sin key. Factory protege esta ruta; no falla así el boot
principal actual. Reproducción: `direct_gemini_none_key`.
Impacto limitado al contrato de construcción directa. Recomendación: normalizar
validación/tipo de error cuando se aprueben fixes; no reactivar proveedores.

### CONFIG-AUDIT-009 — P2 — OPEN

**Entorno del SDK no representado por Settings.** **IMPLEMENTADO:**
[genai.Client](C:/Users/JAIRO/Entidades/Lazzaro/ai_engine/gemini_client.py:35) conserva
configuración ambiental del SDK; fuente instalada `google/genai/_base_url.py:50`
y `_api_client.py:612`. **OBSERVADO:** mismo Settings + `GOOGLE_GEMINI_BASE_URL`
sintético cambia el endpoint utilizado, exclusivamente frente a MockTransport.
Las 16 variables inspeccionadas están ausentes en el entorno de este probe.
Impacto **INFERIDO**: igualdad de Settings no describe todos los parámetros de
transporte de otras sesiones. Es capacidad intencional del SDK, no bug del SDK.
Reproducción: `gemini_sdk_ambient_endpoint`. Recomendación: documentar/controlar
estas entradas explícitas y registrar solo metadata segura de configuración efectiva.

### DEPS-AUDIT-001 — P2 — OPEN

**Reproducibilidad de instalación no contratada.** **IMPLEMENTADO:**
[requirements.txt](C:/Users/JAIRO/Entidades/Lazzaro/requirements.txt:1) no fija
versiones/rangos ni existe manifest de Python soportado. **OBSERVADO:** 11/11 directas
instaladas con versiones iguales al baseline; 159/159 entradas del freeze coinciden;
pip check pasa. Python 3.11.9; metadata instalada de pandas 3.0.1 y scikit-learn 1.8.0
requiere Python >=3.11.
Impacto **INFERIDO**: reinstalar sin restricciones no garantiza el entorno validado;
no se observó regresión ni se probó instalación limpia.
Reproducción: `dependencies_probe.py` (`declared`, diferencias baseline, `requires_python`,
`pip_check`). Recomendación: acordar Python y constraints/lock, validarlos aislados;
no convertir automáticamente los 159 paquetes globales en requirements de producción.

### DEPS-AUDIT-002 — P2 — OPEN

**Perfil opcional hardware/desktop no declarado.** **IMPLEMENTADO:**
[Heat_live](C:/Users/JAIRO/Entidades/Lazzaro/tools/Realidad/Heat_live.py:18),
[keyboard_real](C:/Users/JAIRO/Entidades/Lazzaro/tools/real/keyboard_real.py:24) y
[mouse_real](C:/Users/JAIRO/Entidades/Lazzaro/tools/real/mouse_real.py:24).
**OBSERVADO:** matplotlib, pyserial y PyAutoGUI existen en el freeze pero no en
requirements ni en su cierre transitivo activo. NumPy sí llega transitivamente.
Impacto **INFERIDO**: instalar solo el cierre de runtime no habilita esas utilidades.
main importa herramientas **simuladas**, no estas; no se demuestra fallo de BOOT
ni bloqueo de Capa 2. No se exploraron algoritmos de hardware.
Reproducción: `dependencies_probe.py`, secciones de imports/dependency closure.
Recomendación: documentar extras/perfil opcional cuando se decida soportarlo;
no añadir automáticamente estas dependencias al núcleo.

## 8. Pruebas y regresión

Cada PASS de los probes de auditoría significa **comportamiento esperado reproducido**,
incluso cuando ese comportamiento es un defecto. No es un gate que declare los tickets
arreglados. Los probes se conservan fuera de `test_*.py` productivos para no confundir
estas caracterizaciones con el conteo de regresión de la suite existente.

| Ejecución | Resultado |
|---|---|
| `python docs/audit/probes/boot_probe.py` | 13 grupos PASS, 0 FAIL; diez puntos de startup y cinco fallos de cleanup; dos smokes health y otro Chroma degradada; sin aperturas activas |
| `python docs/audit/probes/config_probe.py` | 16 checks PASS, 0 FAIL, más perfil sanitizado; SDK real con transporte en memoria; HTTPX propios cerrados |
| `python docs/audit/probes/dependencies_probe.py` | Inventario offline y pip check PASS; cero drift de versiones |
| `python docs/audit/probes/regression_probe.py` | 25 módulos, 161/161 unidades: 160 PASS + 1 KNOWN_XFAIL, 0 nuevas; suite_gate e isolation_gate PASS |
| `python -m compileall -q main.py ai_engine api config core memory terminal_client tools docs/audit/probes` | PASS |
| `git diff --check` | PASS |

La unidad histórica es
[memory.test_chromadb_flow.test_eliminacion](C:/Users/JAIRO/Entidades/Lazzaro/memory/test_chromadb_flow.py:233):
AssertionError en `assert client.contar() == 3`. El runner exige esa ubicación/condición,
no acepta cualquier fallo del mismo test como histórico. No se modificó.

Resultados complementarios:

- 11 PersistentClient de la regresión estuvieron aislados; los smokes BOOT utilizaron
  DB temporales propias. Los tres archivos runtime activos conservaron sus SHA256.
- Dependencias: freeze de 159 paquetes; metadata del entorno tiene 161 (extras pip y
  setuptools). El cierre transitivo activo de requirements contiene 98; 61 del freeze
  quedan fuera. No hay import externo del grafo BOOT inspeccionado sin declarar.
- `google-genai` es correcto para `google.genai`; presencia del SDK legacy en freeze
  no demuestra que boot lo use. `python-dotenv` llega mediante pydantic-settings.
- Los imports estáticos incluyen ramas condicionales/funciones: no son prueba de
  ejecución ni de dependencias internas de un modelo serializado.
- La suite usa el mismo descubrimiento de módulos/funciones directas y main-only
  del baseline. No usa un `unittest discover` vacío para reclamar cobertura.

Límites explícitos de comparación:

- Se cambió solo el entorno de prueba: cwd temporal, credenciales heredadas retiradas,
  logging aislado y barreras. No se sustituyeron funciones productivas de LAZZARO;
  el harness envuelve `chromadb.PersistentClient` y `socketpair` únicamente como barreras.
- Tres intentos estándar de `platform._syscmd_ver` (`ver`, `command /c ver`,
  `cmd /c ver`) permanecieron bloqueados durante import; la stdlib usó su fallback.
  Se reportan como introspección de plataforma bloqueada, no como regresión. Cualquier
  subprocess no explicado por ese caso hace fallar el gate de aislamiento.
- Dos conexiones internas socketpair se permitieron para asyncio; no se permitió
  tráfico de proveedor ni descargas. Las pruebas semánticas dependen de la caché
  ONNX local que ya existía; no se evaluó instalación sin esa caché.
- No se probó señal/crash real de Uvicorn, crash nativo, reinstalación limpia,
  disponibilidad remota ni entorno de un servidor en ejecución.

## 9. Cambios, Git y siguiente decisión

Únicamente se añadieron artefactos de auditoría:

- Este documento.
- [boot_probe.py](C:/Users/JAIRO/Entidades/Lazzaro/docs/audit/probes/boot_probe.py).
- [config_probe.py](C:/Users/JAIRO/Entidades/Lazzaro/docs/audit/probes/config_probe.py).
- [dependencies_probe.py](C:/Users/JAIRO/Entidades/Lazzaro/docs/audit/probes/dependencies_probe.py).
- [regression_probe.py](C:/Users/JAIRO/Entidades/Lazzaro/docs/audit/probes/regression_probe.py).

Los archivos runtime que ya estaban modificados permanecen fuera del commit:

```text
 M lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin
 M lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin
 M lazzaro_data/chroma.sqlite3
```

Commit previsto exclusivamente para este documento y los cuatro probes; el hash final
se entrega con el cierre del bloque, sin autorreferencia dentro de este documento.
Sin push ni reescritura de historia; no hubo cambios funcionales ni regeneración
de la base activa.

Recomendación para la fase de fixes, pendiente de autorización:

1. **CONFIG-AUDIT-001:** acotar health/startup de Gemini; presupuesto y resultado ante timeout.
2. **BOOT-AUDIT-001:** garantizar cleanup en salida excepcional y contener cierres fallidos.
3. **BOOT-AUDIT-002 y BOOT-AUDIT-003:** cerrar recursos de construcción parcial sin
   alterar ownership de dependencias prestadas ni esconder la causa original.

No hay P0 demostrado. Antes de Capa 2, resolver esos cuatro P1 o aceptar explícitamente
su riesgo con gates definidos. BOOT-005 requiere atención antes de usar múltiples DB
simultáneas; los demás P2/P3 no autorizan una refactorización general. Este cierre
**no inicia el Bloque 2 ni aplica fixes**.
