# Auditoria integral de Lazzaro

Fecha del escaneo: 2026-07-12  
Commit revisado: `1711f8b`  
Workspace: `C:\Users\JAIRO\Entidades\Lazzaro`

## Actualización Del 2026-07-29

Este informe conserva la evidencia y los conteos del escaneo realizado el
2026-07-12. No se reescriben retrospectivamente sus hallazgos. Para el estado
vigente consultar
[`estado_actual_proyecto_2026-07-29.md`](estado_actual_proyecto_2026-07-29.md).

Cambios relevantes posteriores al escaneo:

- se agregó `terminal_client/` como frontend conversacional activo;
- `api/schemas.py` reconoce `origen="terminal"`;
- el Core conserva Persona/Oracle y reconduce las demás rutas a Persona para
  ese origen;
- `ToolPolicy` limita terminal a `hablar`;
- cancelación, descanso y consolidación quedan bloqueados desde terminal;
- Persona conoce que ese canal no tiene cuerpo disponible;
- las dos frases base de guardado manual producen `CONVERSATION_SAVE`;
- el Core persiste una fotografía pública e idempotente de la RAM como memoria
  semántica, sin LLM, tools, descanso ni consolidador;
- los retries del mismo ID se serializan y conservan el snapshot inicial dentro
  del proceso;
- existe una suite aislada en `terminal_client/test_chat.py`;
- existe `ai_engine/test_manual_memory_save.py`, incluida reapertura de una base
  Chroma real temporal;
- el inventario actual contiene 101 archivos Python, 18 pruebas
  `ai_engine/test_*.py` y una suite adicional en `terminal_client/test_chat.py`,
  para un total de 19 pruebas; `terminal_client/` contiene 4 archivos Python.

El veredicto sobre `gemini_client.py` se mantiene: **no está obsoleto**. En el
estado observado el 2026-07-29, `config/settings.py` conserva `gemini` como
default y `client_factory.py` mantiene su ruta de construcción. El proveedor
efectivo todavía puede ser reemplazado mediante `.env`.

El hallazgo C-02 también sigue vigente. Aunque el nuevo cliente apunta a
`127.0.0.1`, `main.py` escucha en `0.0.0.0:8000` y la API continúa sin
autenticación. El chat debe usarse únicamente en una red confiable.

El riesgo de concurrencia general también sigue vigente. La actualización
serializa reintentos del mismo `id_transaccion`, pero no protege de forma global
IDs distintos, procesos o workers. El alcance aprobado continúa siendo un solo
cliente secuencial con Unity cerrado.

La incorporación del terminal no cierra automáticamente los demás hallazgos de
esta auditoría. Sus estados deberán reevaluarse en una futura ronda integral.

## Alcance y criterio

Revision estatica y checks no destructivos de todo el repositorio: punto de
entrada, API, Core, capas de IA, memoria, herramientas, Triage/entrenamiento,
Telescopio, scripts operativos, ESP32 y los siete archivos del cliente Unity.

No se corrigio codigo. El unico archivo creado es este informe.

La palabra **obsoleto** se reserva para piezas sin ruta de ejecucion o ya
reemplazadas. Los modulos independientes, experimentales o activables bajo
configuracion aparecen como **dormantes**, no como obsoletos.

## Veredicto ejecutivo

El backend Python conserva una base funcional: los 96 archivos Python tienen
AST valido, `compileall` termina correctamente, el import de `main` funciona,
se registran 14 herramientas simuladas y `pip check` no detecta dependencias
rotas. Sin embargo, hay fallos funcionales que las pruebas actuales no cubren.

Prioridades inmediatas:

1. Rotar los secretos versionados. Eliminar el texto del ultimo commit no basta,
   porque ya existe en el historial Git.
2. No exponer el puerto del backend a una red hasta agregar autenticacion y
   limitar los endpoints administrativos.
3. Serializar o proteger el Orchestrator: hoy dos peticiones concurrentes pueden
   procesar dos veces la misma transaccion y mezclar estado.
4. Reparar la correlacion de tareas/eventos Unity. Respuestas atrasadas pueden
   afectar la subtarea siguiente y una cancelacion no invalida la peticion vieja.
5. Alinear dataset, extractor y modelo de Triage antes de volver a entrenar.

### Veredicto sobre `gemini_client.py`

`ai_engine/gemini_client.py` **no esta obsoleto**. Es el cliente primario actual:

- `config/settings.py:12,21-24` usa `AI_PROVIDER="gemini"` y define su modelo.
- `ai_engine/client_factory.py:10,55-58` lo importa y construye.
- El `.env` actual no sobrescribe `AI_PROVIDER`, por lo que aplica el default
  Gemini.
- El entorno construye la cadena `gemini -> groq -> xai -> kimi -> deepseek_api`.
- Una consulta no generativa de metadatos resolvio correctamente el modelo
  configurado durante este escaneo.

Si se borra, el factory deja de importar y el proveedor primario deja de
arrancar. Lo que necesita es endurecimiento y pruebas directas, no retiro.

## Hallazgos criticos

### C-01 - Secretos reales versionados

Evidencia:

- `.env:1-6` contiene cinco claves API no vacias y esta rastreado por Git, aunque
  `.gitignore:2` intenta ignorarlo.
- `tools/Realidad/esp_32.ino:6-7` contiene SSID y contrasena Wi-Fi en claro.
- Ambos archivos ya aparecen en el historial Git.

Impacto: cualquier copia o publicacion del repositorio expone acceso a
proveedores pagados y a la red Wi-Fi. Deben rotarse las claves de Gemini, Groq,
xAI, Kimi y DeepSeek, ademas de la contrasena Wi-Fi.

### C-02 - API administrativa expuesta sin autenticacion

Evidencia:

- `main.py:129-134` escucha en `0.0.0.0:8000` al ejecutar el archivo.
- `api/routes.py:135-147` declara `/api/v1/comando` como administrativo, pero no
  aplica autenticacion ni autorizacion.
- `/estado`, `/notificaciones` y `/constelacion` tambien carecen de control de
  acceso.
- `api/schemas.py:19` acepta origen `sandbox`; `core/orchestrator.py:1518-1560`
  usa ese valor, controlado por el cliente, para decidir ejecucion local.

Impacto: en una red accesible, terceros pueden consumir cuota LLM, alterar estado
y memoria, leer telemetria y enviar acciones. Hoy el startup solo registra
herramientas simuladas; el impacto sube a control del SO si se reactivan las
herramientas reales.

### C-03 - Stored XSS en el HTML de Telescopio

Evidencia:

- `telescopio/visualizar_constelacion.py:364-365,521-523` incrusta `json.dumps()`
  directamente dentro de `<script>` sin neutralizar `</script>`.
- `telescopio/visualizar_constelacion.py:623-630` inserta grupos mediante
  `innerHTML`.
- Un payload sintetico con cierre de `script` sobrevivio literalmente en el HTML
  generado.

Impacto: texto controlado almacenado en memoria puede ejecutar JavaScript cuando
se abre la visualizacion. El archivo es local, pero puede leer o manipular el
contexto del navegador y vuelve peligroso compartir el HTML.

## Hallazgos altos

### A-01 - Orchestrator y BehaviorCore no son seguros bajo concurrencia

`api/routes.py:48-50` despacha cada evento al threadpool, pero comparte una sola
instancia mutable (`core/orchestrator.py:40-63` y
`core/behavior_core.py:39-51`) sin lock global por evento.

La idempotencia es check-then-act: consulta el cache en
`core/orchestrator.py:79-82` y no escribe hasta `:221`. Dos peticiones simultaneas
con el mismo `id_transaccion` pueden pasar el check, modificar estado, escribir
memoria y generar acciones diferentes. `TriageRouter` agrega otra carrera al
guardar `_last_confidence` y `_last_reason` en la instancia
(`ai_engine/triage_router.py:183-184,242-300`).

### A-02 - Excepciones internas se convierten en HTTP 200 y congelan Unity

`core/orchestrator.py:84-91` captura una excepcion de BehaviorCore y devuelve
`None`. `api/routes.py:53-59` traduce ese `None` a `status="ok"` con accion nula.
Unity retorna sin liberar la tarea (`unity_client/LazzaroAgent.cs:490-495`).

El cliente no puede distinguir telemetria pasiva de un fallo real, y una
subtarea activa puede quedar bloqueada indefinidamente.

### A-03 - La cola Unity no correlaciona respuesta, evento y subtarea

Hay varios fallos del mismo origen:

- Un resultado fisico se encola y la siguiente subtarea inicia al siguiente
  frame (`LazzaroAgent.cs:452-468,220-229`). Cuando llega el `none` del resultado
  anterior, `:519-522` puede marcar como fallida la subtarea nueva.
- Un fallo HTTP solo registra warning (`:479-484`); no libera la tarea, no avanza
  la secuencia y no reintenta.
- Una respuesta o accion nula simplemente retorna (`:490-495`).
- `id_accion` se recibe en `DTOs.cs:18`, pero no se reenvia en
  `ReportarResultadoAccion()` (`LazzaroAgent.cs:452-467`).

Impacto: secuencias multiaccion pueden cancelarse por la respuesta de otro paso,
quedar congeladas o aprobar aliases con el resultado equivocado.

### A-04 - Cancelar la fila no cancela la accion vieja

`LazzaroAgent.cs:146-149,232-243` limpia la cola logica, pero no elimina un evento
ya en vuelo, no invalida su respuesta y no llama a `FrenarMovimiento()`. La cola
HTTP es serial (`:471-487`), de modo que la respuesta vieja todavia puede ejecutar
movimiento antes de procesar el comando de cancelacion.

El evento de dolor tiene el mismo limite: `:412-415,427-449` solo lo inserta al
frente de la cola; no interrumpe una peticion activa, cuyo timeout llega a 90 s
(`ApiClient.cs:9-11,27-29`).

### A-05 - Cuatro handlers Unity reportan exito sin actuar

- Interactuar: `LazzaroAgent.cs:835-855`.
- Agarrar: `:857-867`.
- Soltar: `:869-879`.
- Aplicar fuerza: `:881-901`.

Los metodos validan parcialmente o escriben logs, pero no activan componentes,
no crean un agarre, no sueltan ningun objeto y no aplican `Rigidbody.AddForce`.
Aun asi reportan `exito=true`, contaminando tarea, identidad, memoria y
aprendizaje de aliases.

### A-06 - Affordances de interaccion se validan como una accion generica

`AffordanceResolver` soporta `interaction_type`
(`ai_engine/affordance_resolver.py:173-178`), pero
`fuzzy_object_resolver.py:276-281` y `motor_engine.py:370-442` solo pasan
`interactuar_objeto_3d`.

Reproduccion: `activar` fue aprobado para un objeto que solo tenia `mirar`; a la
inversa, `golpear` puede bloquearse aunque el objeto sea `empujable`. El parametro
`tipo_accion` se agrega despues de haber tomado la decision de affordance.

### A-07 - El nombre resuelto por backend puede no existir en Unity

Unity envia `id`, `display_name`, aliases y `nombre` del collider
(`PerceptionSystem.cs:184-210`). El backend reconstruye el objeto usando
preferentemente `display_name` (`perception_models.py:180-207`) y lo devuelve como
nombre legacy (`fuzzy_object_resolver.py:318`; `motor_engine.py:300-307`). Unity,
sin embargo, busca solo contra `Collider.name` (`PerceptionSystem.cs:253-290`).

Si `InteractableMetadata.displayName` difiere del nombre real del GameObject, la
resolucion backend puede ser correcta y la ejecucion Unity fallar.

### A-08 - Coordenadas y vectores aceptan `NaN`/`Infinity`

`ai_engine/motor_schemas.py:26-29,67-72` usa floats sin exigir finitud para
coordenadas y componentes de vector. Pydantic acepto `x=inf` y `vector_x=nan` en
el smoke check; la serializacion JSON estricta falla o el cliente recibe un valor
fisicamente invalido.

### A-09 - El flujo de entrenamiento predeterminado esta roto

- `train_triage_model.py:11,63-67` busca `dataset_shingon.csv` en la raiz, donde
  no existe.
- El dataset real esta en
  `cvs_entrenamiento_final_finaloso_finalisimo_v2_v1/dataset_shingon.csv`.
- El extractor (`Scripts_Aux/...py:8-11,27-30`) elige por defecto
  `logs antiguos/lazzaro_4.log` e ignora el activo `logs/lazzaro.log`, aunque su
  help en `:90` afirma lo contrario.
- El log viejo produjo 25 pares; el activo contiene 302 pares extraibles.

Ejecutar el trainer con defaults termina en `FileNotFoundError`.

### A-10 - Taxonomia del modelo Triage esta desalineada

El modelo activo contiene `ORACLE` y `ORACLE_QUERY` como clases separadas y
carece de `MEMORY_UPDATE`, `MOTOR_REFERENCE` y `CORE_SYSTEM`, presentes en
`ai_engine/intent_types.py:11-21`. El trainer valida columnas, pero no labels
(`train_triage_model.py:23-40,70-76`).

`triage_router.py:249-265` aplica el umbral de 0.60 antes de normalizar el alias.
En una fila reproducida, `ORACLE=0.58` y `ORACLE_QUERY=0.0267` sumaban 0.6067,
pero el modelo cayo al fallback por usar solo el maximo 0.58. En ese caso el
fallback recupero la ruta correcta por casualidad.

### A-11 - El modelo activo no se puede reconstruir desde un clon limpio

Los `.joblib` estan ignorados y el runtime degrada silenciosamente a reglas si
faltan. El unico trainer incluido falla con su ruta predeterminada y ademas
produce un segundo modelo de ruta que el runtime ya no usa.

### A-12 - `segurity.sh` es valido sintacticamente, pero esta roto

- `segurity.sh:2` contiene el comando literal `segurity.sh` antes del shebang.
- El bloque `3-88` esta duplicado exactamente en `89-174`.
- La ejecucion de `--help` confirma `segurity.sh: command not found`.
- En flujo normal intenta aplicar toda la configuracion UFW dos veces.
- Ambos `.sh` tienen modo Git `100644`, aunque documentan `sudo ./script.sh`.
- `install-segurity-autostart.sh:90` instala la copia defectuosa.

`bash -n` no detecta el problema porque no es un error de sintaxis.

### A-13 - La decision inicial documentada nunca ocurre

`LazzaroAgent.cs:68-74` envia `inicio_simulacion` sin
`solicita_decision=true`. El Core solo decide para comandos, flags explicitos o
interrupciones (`core/orchestrator.py:1485-1490`) y Triage fuerza este evento a
`noop` (`ai_engine/triage_router.py:110-116`). Esto contradice
`docs/cognitive_contracts.md:1155-1159`, que promete una decision inicial una
sola vez.

### A-14 - La confirmacion de herramientas no tiene camino de finalizacion

El schema dice que `requiere_confirmacion=true` debe pedir confirmacion
(`api/schemas.py:108-111`). El Orchestrator entonces no ejecuta
(`core/orchestrator.py:1505-1516`), mientras el Executor rechaza una herramienta
marcada como sensible si el flag es `false` (`tools/executor.py:41-44`). No existe
endpoint o evento para representar "ya confirmada". Unity tambien omite y reporta
fallida toda accion marcada (`LazzaroAgent.cs:525-533`).

### A-15 - Fallos ejecutados se contabilizan como exito biologico

`core/orchestrator.py:202-213` obtiene el resultado real, pero
`_registrar_estado_identidad()` (`:1166-1173`) deriva exito solo de que el
`ActionResponse` haya sido valido, antes de recibir el resultado fisico. Un fallo
de Unity o de una herramienta puede reforzar confianza en vez de aumentar estres
y ajustar foco.

## Hallazgos medios y deuda funcional

### M-01 - Gemini y el health check pueden bloquear el arranque/fallback

`gemini_client.py:55-80` duerme incluso despues del cuarto y ultimo error. El
smoke con cliente falso registro cuatro sleeps para cuatro fallos; el ultimo no
sirve para ningun reintento. Una respuesta vacia, en cambio, se solicita cuatro
veces sin pausa. `main.py:64` hace un health check generativo en cada arranque y
`FallbackAIClient.health_check()` puede probar proveedores sucesivos.

Resultado: arranque lento, consumo de cuota y posible timeout Unity antes de que
el fallback responda. No hay prueba dedicada del cliente Gemini real.

### M-02 - El filtro `requires_memory` de Triage se ignora

`core/orchestrator.py:548-558` decide leer memoria para toda ruta activa y no
consulta `triage_decision["requires_memory"]`. Triage declara que el motor normal
no la necesita (`ai_engine/triage_router.py:318-332`). Cada orden motora puede
sumar busquedas vectoriales, memoria relacional y ruido contextual innecesario.

### M-03 - `mensaje`, `texto_usuario` y `texto` no son compatibles extremo a extremo

Triage y un helper del Orchestrator aceptan las tres claves, pero Persona,
Oracle, Motor y partes de BehaviorCore leen solo `detalles["mensaje"]`
(`core/orchestrator.py:568,647,750,819`; `core/behavior_core.py:314-333`). Un
evento puede clasificarse bien y llegar vacio a la capa que debe resolverlo.

### M-04 - Estado temporal y escala biologica inconsistentes

- `StateEngine.tick()` dice que debe ejecutarse antes de evaluar
  (`core/state_engine.py:56-60`), pero las lecturas publicas no lo llaman. Energia,
  estres, foco y expiracion de dolor pueden quedar congelados hasta otro evento.
- `core/orchestrator.py:945-955` divide valores por 100 solo si son mayores que
  1. Energia `1.0` se interpreta como 100% y `0.5` como 50%, aunque el estado
  fuente usa escala 0-100.
- `api/schemas.py:195` exige `dopamina`, pero `orchestrator.py:1212-1217` la
  rellena duplicando `confianza`.

### M-05 - Alias fallido puede aprenderse tras otro exito

Un resultado fallido no elimina el candidato pendiente
(`core/orchestrator.py:427-435`). Si falta `id_accion`, un exito posterior busca
solo accion/target (`:446-475`). Unity actualmente no reenvia `id_accion`, por lo
que un alias asociado a un intento fallido puede persistirse por otro resultado.

### M-06 - Descanso y `exito` dependen de payloads ambiguos

- El descanso se detecta por subcadenas en texto
  (`core/orchestrator.py:1178-1189`), no por el tipo de evento. Un evento descanso
  sin mensaje puede no aplicar; una frase como "define dormir" puede activar
  consolidacion.
- `detalles` es libre. BehaviorCore exige `exito is True/False` para emocion, pero
  usa `bool(exito)` al registrar la tarea. El string `"false"` termina siendo
  verdadero para TaskContext.

### M-07 - El timestamp de entrada no participa en el flujo

`api/schemas.py:33-36` acepta `EventoEntrante.timestamp`, pero no tiene lectores.
Eventos atrasados o fuera de orden se procesan y memorizan como actuales. La API
tambien devuelve `str(e)` al cliente (`api/routes.py:69-77`) y publica
`pensamiento_privado`, elevando la fuga de detalles si el bind es publico.

### M-08 - Estado emocional episodico se pierde al persistir

El Orchestrator entrega el snapshot (`core/orchestrator.py:291-302`) y el encoder
lo coloca en el dataclass (`memory/memory_encoder.py:74-79`), pero
`a_documento()` no lo guarda (`:131-147`). Al recuperar se fuerza
`estado_emocional=None` (`memory/episodic_memory.py:108-113`). El parametro es
funcionalmente muerto tras el round-trip de Chroma.

### M-09 - El decay por uso no existe en la practica

`memory/memory_decay.py:55-78` recibe `ultimo_acceso` pero no lo utiliza.
`memory_retriever.py:87-91` no entrega contador ni ultimo acceso, y no existe un
contador de recuperaciones. El decay activo usa edad e impacto, no frecuencia ni
recencia de uso como afirma su contrato.

### M-10 - Telescopio confunde muestra con total y muestra memoria antigua

`visualizar_constelacion.py:90,105-108` pide `listar(limite=80)` sin orden por
fecha y guarda `total_memorias=len(ids)`. El HTML dice 80 memorias, pero la
coleccion actual contiene 1,507 experiencias; el lote visible abarca del 29-may
al 7-jun, mientras la DB llega al 11-jul. `constelacion.html` es del 28-jun y esta
desactualizado, aunque no obsoleto.

### M-11 - Datos runtime y secretos siguen rastreados pese a `.gitignore`

`lazzaro_data/chroma.sqlite3` y los indices HNSW estan en Git. Esto expone
memoria, aumenta el repositorio, provoca diffs binarios y facilita conflictos o
corrupcion. Los dos directorios HNSW actuales si corresponden a colecciones
activas; no son archivos huerfanos.

### M-12 - Dependencias incompletas y sin versiones

`Heat_live.py:18-21` requiere `matplotlib`, `numpy` y `pyserial`; `tools/real/*`
requiere `pyautogui`. No estan en `requirements.txt`. En esta maquina existen,
pero un clon que instale solo el archivo declarado no es reproducible. Ninguna
dependencia esta fijada a una version.

### M-13 - Configuracion relativa al directorio de arranque

`config/settings.py:58,61-65` usa `.env` y `./lazzaro_data` relativos al CWD. Un
servicio lanzado desde otra carpeta puede no encontrar las claves y crear o abrir
otra base Chroma.

### M-14 - Politica de herramientas calcula un filtro que se ignora

`llm_policies.py:82-89` devuelve `herramientas_filtradas`, pero
`reasoning_engine.py:75-100` solo consume `generation` y valida contra el allowlist
anterior. Con politica social, una herramienta de riesgo puede seguir en prompt y
ser aceptada. Hoy la politica real queda casi siempre en `exploradora`, porque
`BehaviorCore.cambiar_policy()` no tiene call sites; aun asi el contrato de
seguridad queda roto si se activa.

### M-15 - El fallback replica memoria a proveedores externos

`fallback_ai_client.py:40-54` envia el mismo prompt al siguiente proveedor si el
anterior falla. El prompt contiene percepcion y memoria
(`prompt_builder.py:51-62,87-97`). Esto es esperado tecnicamente, pero debe
considerarse una frontera de privacidad; la configuracion actual habilita cinco
proveedores con credenciales.

Ademas, `FallbackAIClient.provider_name` queda fijado al primario
(`fallback_ai_client.py:34-37`). Si Ollama es fallback, recibe parametros de
politica pensados para el proveedor remoto.

### M-16 - Oracle presupone soporte de `response_format`

`oracle_client.py:48,82-85` envia formato JSON a cualquier URL configurada. Un
endpoint compatible con Chat Completions pero sin esa extension fallara siempre
y caera al fallback.

### M-17 - Percepcion Unity puede quedar obsoleta

- `CambioSignificativo()` (`PerceptionSystem.cs:216-250`) no detecta movimiento
  de colliders ya conocidos. Un objeto nuevo que aparezca a mas de 2 m se agrega
  al snapshot local sin reportarse y sus acercamientos posteriores tampoco
  disparan evento.
- `ObtenerObjetosInteractuables()` usa esfera y angulo, pero no raycast de
  oclusion (`:86-126,293-303`), por lo que puede ver a traves de paredes.
- Busca `InteractableMetadata` solo en el collider (`:54,184`), no en su padre.
- Las distancias de interaccion usan el centro del objeto; colliders grandes
  pueden rechazarse aunque su superficie este cerca.

### M-18 - Riesgos de movimiento y somatosensores Unity

- El detector de atasco asigna velocidad y comprueba esa misma velocidad en el
  mismo ciclo (`MovementController.cs:201-215,266-270`); contra un obstaculo puede
  no acumular el tiempo de atasco real.
- `UltimaAccionMovimiento` inicia como `navegar_a_objeto`
  (`LazzaroAgent.cs:30`), creando un fallo ficticio si la primera colision sucede
  antes de cualquier orden.
- `SomatoSensorySystem` obtiene `LazzaroAgent` pero no lo declara como componente
  requerido (`SomatoSensorySystem.cs:5-7,45-53`).
- El callback de exito de `ApiClient` se ejecuta dentro del `try` de parseo
  (`ApiClient.cs:33-52`); una excepcion del consumidor se informa como si fuera
  error JSON.

### M-19 - El runner de pruebas no cubre la prueba mas reciente

Existen 17 archivos `ai_engine/test_*.py`. Dieciseis son scripts autoejecutables
y pasaron. `test_task_cancellation.py` es estilo pytest, no agrega el root al
`sys.path`, no tiene `main` y `pytest` no esta instalado ni declarado:

- `python ai_engine/test_task_cancellation.py` falla con
  `ModuleNotFoundError: api`.
- `python -m ai_engine.test_task_cancellation` sale 0 sin ejecutar tests.
- Sus cinco funciones pasan al importarlas y ejecutarlas manualmente.

El feature funciona en esos cinco casos, pero el corredor habitual puede dar un
falso verde.

### M-20 - Documentacion desactualizada o contradictoria

- `docs/estado_actual_proyecto_2026-07-06.md:20-28` registra 95 Python, 16 tests
  y seis C#; hoy hay 96, 17 y siete, incluyendo `SomatoSensorySystem.cs`.
- `docs/arquitectura_completa_lazzaro.md` esta fechado 2026-06-11 y no incluye
  Somato ni `cancelar_tarea`.
- `docs/cognitive_contracts.md:854-867` omite `cancelar_tarea`.
- El contrato documenta formas distintas a las reales para `estado_fisico`,
  `objeto_mas_cercano`, `direccion_escape`, `timestamp_servidor` y
  `parametros_descripcion`.
- La arquitectura describe agarrar/soltar/interactuar como ejecucion fisica,
  aunque sus handlers actuales son stubs.

### M-21 - Defectos menores confirmados

- `memory/memory_manager.py:466-468`: `obtener_ultimas_acciones(n)` ignora `n` y
  siempre resume cinco.
- `tools/Realidad/esp_32.ino:17-23,104-107`: conecta Wi-Fi solo en `setup()`; una
  desconexion posterior requiere reiniciar el dispositivo.
- `api/schemas.py:112-115`: ningun productor rellena
  `parametros_descripcion`.
- Los endpoints `/estado`, `/notificaciones`, `/constelacion` y `/comando` no
  tienen consumidores internos. Pueden ser APIs externas legitimas; no se
  consideran obsoletos sin confirmar esos consumidores.

## Archivos y piezas obsoletas

### Obsoletos con confianza alta

| Archivo o pieza | Confianza | Motivo |
| --- | ---: | --- |
| `lazzaro_data/triage_model_route.joblib` | 100% | El trainer lo genera en `train_triage_model.py:74-83`; el runtime solo carga `triage_model_intent.joblib` y deriva la ruta desde la intencion. |
| Rama de entrenamiento `pipeline_route` | 100% | Produce exclusivamente el artefacto anterior, sin lector. |
| `to-do` | 100% | Archivo rastreado, vacio y sin referencias. |
| `memory/memory_filters.py` | 98% | Solo se importa e instancia; ningun metodo tiene call site. `recientes()` produce `_recencia_horas`, marcador que nadie interpreta y que Chroma no acepta directamente. |
| `ToolRegistry.get_all_tools_info()` | 98% | Sin call sites; el flujo activo usa `describir_herramientas()`. |
| `ToolRegistry.get_tools_for_prompt()` | 98% | Sin call sites y duplica la responsabilidad anterior. |
| `MemoryDecay.es_candidato_descarte()` | 95% | Sin call sites; consolidacion usa los calculos de importancia directamente. |
| `MotorEngine.resolver_comando_usuario()` | 95% | Sin call sites; el flujo usa diagnostico/resolucion por las rutas nuevas. |
| `INTENT_TO_ROUTE_MAP` en `triage_router.py` | 95% | Se construye, pero ningun consumidor lo lee. |
| Singletons globales `behavior_core`, `personalidad`, `estado_actual` | 95% | `main.py` construye instancias propias y no hay consumidores de los globales. |

Otros miembros sin consumidores: helpers legacy de `SpatialReasoner`,
`required_provider` en `LLMGenerationAdapter`, `EVENTOS_CON_RESULTADO`, algunas
propiedades derivadas de `StateEngine`, `FallbackAIClient.last_provider_used` y
`fallback_chain_name`.

### Candidatos para archivar o confirmar antes de retirar

| Archivo | Confianza | Dictamen |
| --- | ---: | --- |
| `teses.py` | 90% | Smoke manual huerfano; usa herramientas ficticias (`escanear_red`, `buscar_archivo`, `ninguna`) y hace una llamada LLM real. |
| `tools/real/keyboard_real.py`, `mouse_real.py` | 80% condicional | No se importan en startup, no se registran y su confirmacion no puede completarse. Conservar solo si se planea reactivar control real. |
| `cvs_.../dataset_triage_crudo_*` | 60% | Ningun codigo los consume. Parecen procedencia/intermedios; archivar, no borrar hasta confirmar lineage del modelo. |
| `docs/estado_actual_proyecto_2026-07-06.md` | Historico | Ya no describe el estado actual, pero sirve como snapshot fechado. No usarlo como informe vigente. |
| `constelacion.html` | Salida stale | No es obsoleto; debe regenerarse tras corregir seleccion/XSS y etiquetar muestra vs total. |

### Confirmados como activos o validamente independientes

- `ai_engine/gemini_client.py`, `client_factory.py`, `fallback_ai_client.py`,
  `openai_compatible_client.py`, `ollama_client.py`, `oracle_client.py` y
  `persona_prompt_buider.py`.
- `memory/chroma_client.py`, stores, retriever y manager.
- `telescopio/constelacion_cognitiva.py`: actualmente lo usa `BehaviorCore`.
- `telescopio/visualizar_constelacion.py`: utilidad autonoma activa, aunque tiene
  XSS y selecciona una muestra antigua.
- Los dos directorios HNSW: pertenecen a `experiencias` y `oracle_cache`.
- `tools/Realidad/esp_32.ino` y `Heat_live.py`: pareja autonoma coherente; no esta
  integrada al agente principal.
- Ninguno de los siete archivos completos de `unity_client/**` es obsoleto. Los
  cuatro handlers fisicos son stubs activos, no archivos muertos.
- Los `__init__.py` vacios son marcadores de paquete, no basura.

## Estado funcional por subsistema

| Subsistema | Estado del escaneo |
| --- | --- |
| Entrada FastAPI | Importa y expone 9 rutas; riesgo alto de autenticacion y concurrencia. |
| Gemini/fallbacks | Activo; modelo resuelve. Retry/health check y privacidad requieren ajuste. |
| Triage | Activo con modelo intent; dataset, labels, extractor y reproducibilidad estan desalineados. |
| Motor/fuzzy | Activo y probado en casos basicos; falla affordance especifica y finitud numerica. |
| Persona/Oracle/Reasoning | Compilan y tests pasan; quedan inconsistencias de politica, payload y fallback. |
| Memoria/Chroma | Activa; decay por uso y snapshot emocional no cumplen su contrato. Estado runtime esta versionado. |
| Tools simuladas | 14 registradas. Confirmacion y tools reales no tienen flujo operativo. |
| Unity | Contrato mayormente compatible; cola/correlacion, stubs y movimiento requieren prueba de campo. |
| Telescopio | Modelo cognitivo activo; HTML stale, muestra antigua y permite XSS. |
| Entrenamiento/logs | No funciona correctamente con defaults. |
| Scripts UFW | Sintaxis valida, ejecucion defectuosa y sin bit ejecutable. |
| ESP32/Heatmap | Coherentes como modulo aparte; secretos, reconexion y dependencias pendientes. |
| Documentacion | Util como historia, no sincronizada con el commit actual. |

## Verificaciones ejecutadas

| Check | Resultado |
| --- | --- |
| Inventario | 96 Python, 17 tests Python, 7 C#, 14 herramientas registradas. |
| `python -m compileall -q ...` | OK. |
| AST de 96 archivos Python | OK. |
| Import de `main` y 32 modulos productivos de `ai_engine` | OK. |
| `python -m pip check` | OK. |
| 16 tests autoejecutables | Pasan. |
| Cinco funciones de cancelacion invocadas manualmente | Pasan. |
| Ejecucion directa de `test_task_cancellation.py` | Falla por import; no esta integrada al runner. |
| Carga del modelo intent | OK; taxonomia desalineada confirmada. |
| Trainer con ruta por defecto | Falla por dataset inexistente. |
| Resolucion default del extractor | Selecciona log antiguo. |
| `bash -n` en ambos `.sh` | OK sintactico; smoke `--help` confirma comando espurio. |
| `git diff --check` antes del informe | OK. |

## Limites de la revision

- No se hicieron generaciones pagadas ni se probaron cuotas/rate limits reales.
- No se retrenaron ni sobrescribieron modelos.
- No se modifico ni regenero ChromaDB o `constelacion.html`.
- No se puede compilar Unity aqui: faltan `.sln`, `.csproj`, `.asmdef`,
  `ProjectSettings.asset` y `Packages/manifest.json`. Los hallazgos C# son
  revision estatica y de contrato; `MovementController`, fisica, colisiones y
  componentes de escena necesitan Play Mode.

## Orden recomendado para una futura ronda de correcciones

1. Seguridad: rotar secretos, limpiar historial y cerrar/autenticar API.
2. Consistencia de ejecucion: lock/serializacion backend e identidad basada en
   resultados reales.
3. Unity: correlacion por `id_accion`, invalidacion de respuestas viejas,
   cancelacion real y handlers fisicos.
4. Motor: affordance por `tipo_accion`, nombres estables y floats finitos.
5. Triage: fuente de logs, dataset canonico, labels normalizadas y un solo modelo
   reproducible.
6. Memoria/Telescopio: persistencia emocional, contrato de decay, XSS y orden de
   muestreo.
7. Higiene: scripts, dependencias fijadas, artefactos runtime y documentacion.
