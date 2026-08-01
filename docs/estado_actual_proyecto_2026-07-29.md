# Estado Actual Del Proyecto

Fecha de actualización: 2026-07-29  
Commit base observado: `1711f8b`  
Workspace: `C:\Users\JAIRO\Entidades\Lazzaro`

> Nota de vigencia (2026-08-01): este archivo conserva el snapshot del
> 2026-07-29. Las afirmaciones posteriores que bloquean toda regeneración desde
> terminal quedaron reemplazadas por el contrato de descanso diferido aprobado:
> `0.10` puntos de energía por segundo hasta el siguiente `comando_usuario`
> nuevo, proyección mediante `/estado` sin despertar, sin consolidación ni
> acciones físicas, no acumulable y válido solo mientras corre el backend. La
> especificación vigente está en [`chat_terminal.md`](chat_terminal.md).

Este documento actualiza el snapshot histórico del 2026-07-06. Describe el
commit base junto con los cambios todavía no confirmados del chat terminal y
del guardado manual de RAM conversacional.

## Veredicto Ejecutivo

Los dos entregables aprobados están implementados:

1. cliente de terminal exclusivamente conversacional;
2. guardado natural de una fotografía pública de la RAM en memoria semántica.

Ambos pasaron validación automatizada. La persistencia fue comprobada con una
base Chroma real temporal, cierre y reapertura. La prueba de campo con el
backend, proveedor y `lazzaro_data/` del usuario continúa pendiente.

Estado:

- implementación del chat: completada;
- implementación del guardado manual: completada;
- regresiones automatizadas: aprobadas;
- prueba de campo terminal: resultado pendiente de documentar;
- prueba de recuerdo real después de reiniciar: pendiente de campo.

## Cliente Terminal

Entrada:

```powershell
python -m terminal_client
```

Requiere iniciar primero el backend:

```powershell
python main.py
```

Decisiones vigentes:

- se usa con Unity completamente cerrado;
- reutiliza `POST /api/v1/comando`;
- envía `origen="terminal"` y `tipo_evento="comando_usuario"`;
- conserva las rutas Persona y Oracle;
- reconduce Motor, NOOP, INTERNAL y rutas desconocidas a Persona;
- solo acepta como acción exitosa `hablar`;
- nunca imprime pensamiento privado ni razonamiento;
- no ejecuta cancelación, descanso, consolidación ni herramientas físicas;
- `/ayuda`, `/estado` y `/salir` son los únicos comandos locales;
- no existe `/guardar`;
- no se separó `TaskContext`, por decisión explícita de esta fase.

La guía operativa y los límites completos están en
[`chat_terminal.md`](chat_terminal.md).

## Guardado Manual De Conversación

Frases base:

```text
guarda lo que hemos hablado
guarda lo que tengas en ram en tu mente
```

La comparación tolera mayúsculas, acentos, puntuación y espacios. Triage asigna
`CONVERSATION_SAVE` a Persona, pero el Orchestrator lo resuelve por una rama
determinista: no consulta recuerdos vectoriales, no expone herramientas y no
llama al LLM.

El Random Forest desplegado todavía no contiene la clase
`CONVERSATION_SAVE`. Por eso hoy solo funcionan las dos frases base. La
expansión mediante modelo continúa como trabajo futuro de entrenamiento y
validación.

`MemoryManager`:

- fija un snapshot público, atómico e inmutable de hasta 12 turnos;
- limita Usuario y Lazzaro a 700 caracteres por lado;
- guarda la transcripción, un índice compacto, cantidad e intervalo UTC;
- excluye pensamiento, emoción, ruta, intent y metadata privada;
- persiste una regla semántica
  `tipo_evento="memoria_conversacional_manual"`;
- deduplica por transacción y huella SHA-256 del snapshot;
- falla cerrado si no puede comprobar idempotencia;
- conserva el snapshot original durante retries del mismo ID dentro del
  proceso;
- mantiene intacta la RAM y excluye el turno de control;
- no toca descanso, biología ni el consolidador observacional.
- emite un evento `conversation_memory_save` por intento con estado, conteo,
  prefijo de huella y confirmación durable, sin contenido conversacional.

Persona confirma `Está bien, lo recordaré para después.` únicamente después de
una escritura confirmada. Los estados duplicado, vacío y error tienen mensajes
propios y nunca anuncian un éxito falso.

El diseño y sus límites están en
[`plan_guardado_ram_conversacional.md`](plan_guardado_ram_conversacional.md).

## Memoria Y Continuidad

El chat mantiene el flujo de memoria existente:

- guarda el comando como evento por el mecanismo episódico normal;
- consulta memoria cuando una ruta cognitiva normal lo requiere;
- registra pares usuario/Lazzaro en la memoria conversacional RAM;
- puede extraer hechos relacionales selectivos mediante el flujo ya existente;
- puede promover explícitamente la fotografía pública actual mediante el
  guardado manual.

La RAM conserva hasta 12 intercambios, normalmente inyecta los últimos 6,
recorta cada lado a 700 caracteres y limita su resumen a 1600. El buffer se
pierde al reiniciar.

La fotografía manual sí queda en Chroma como memoria semántica recuperable, pero
no repuebla la cola RAM al arrancar. La recuperación que llega a Persona sigue
sujeta a ranking semántico y al límite de 600 caracteres del contexto largo;
la transcripción completa persiste, aunque no se promete recordar literalmente
cualquier fragmento interior de un turno largo.

El consolidador observacional conserva sin cambios su scheduler y batch. El
guardado manual no incrementa su contador, no borra episodios, no simula
descanso y no crea otro checkpoint.

## Barreras Del Canal

La restricción de solo conversación se aplica en varias capas:

1. `ToolPolicy` expone únicamente `hablar`.
2. El Core adapta la decisión de Triage antes de elegir la capa.
3. El atajo de cancelación no se ejecuta para terminal.
4. Las frases de descanso no regeneran biología ni consolidan memoria.
5. El firewall de salida se aplica antes y después de sanitizar la respuesta.
6. Persona recibe contexto explícito de que el canal no tiene cuerpo.
7. El cliente rechaza contratos no conversacionales y limpia secuencias ANSI
   basadas en ESC y controles C0/DEL.
8. El control de guardado siempre termina en `hablar` y nunca se despacha como
   herramienta.

Unity conserva sus rutas motoras, cancelación y descanso. Si recibe una frase de
guardado válida, la rama de memoria tampoco se confunde con descanso.

## Verificaciones Aprobadas

| Verificación | Resultado |
|---|---|
| 17 scripts autoejecutables de `ai_engine/test_*.py` | OK |
| Cinco funciones de `ai_engine/test_task_cancellation.py` invocadas directamente | OK |
| `python terminal_client\test_chat.py` | OK |
| `python ai_engine\test_manual_memory_save.py` | OK |
| Chroma real temporal: escritura, segundo recibo, cierre, reapertura y consulta | OK |
| Retry con snapshot estable y turnos posteriores | OK |
| Fallos `False`/excepción al asociar metadata, seguidos de retry | OK |
| Dos solicitudes simultáneas con el mismo ID | OK |
| Logging de `saved`, `already_saved`, `empty` y `error` | OK |
| Logging sin transcripción/canarios privados y fail-open | OK |
| `python -m compileall -q terminal_client ai_engine core memory api tools telescopio config main.py` | OK |
| `git diff --check` | OK |

Inventario actual:

- 101 archivos Python;
- 18 archivos `ai_engine/test_*.py`;
- 1 suite adicional en `terminal_client/test_chat.py`;
- 19 archivos de prueba Python en total.

`ai_engine/test_task_cancellation.py` conserva una limitación histórica de
entrada: ejecutarlo directamente no configura la raíz del proyecto. Sus cinco
funciones sí pasan cuando se importan e invocan desde la raíz.

Las pruebas de Chroma usan `TemporaryDirectory`; no modifican `lazzaro_data/`.
No se llamó a un proveedor LLM real.

## Proveedores LLM

`gemini_client.py` sigue activo y no es obsoleto:

- `client_factory.py` todavía puede construirlo;
- `config/settings.py` conserva actualmente `gemini` como proveedor
  predeterminado;
- `.env` puede cambiar el proveedor efectivo;
- Ollama y los clientes compatibles con OpenAI siguen disponibles;
- Oracle y los fallbacks pueden enviar conversación a servicios remotos cuando
  estén configurados.

No debe inferirse el proveedor efectivo únicamente desde el valor por defecto
sin revisar la configuración de ejecución.

## Riesgos Y Límites Vigentes

- `main.py` escucha en `0.0.0.0:8000` y la API no tiene autenticación.
- El cliente apunta a `127.0.0.1`, pero el backend puede quedar visible en la
  red local según el firewall.
- El Orchestrator mantiene estado compartido y no está diseñado para usar Unity
  y terminal al mismo tiempo.
- Los locks nuevos serializan el mismo `id_transaccion`; no resuelven
  concurrencia general entre IDs, procesos o workers.
- Cada mensaje terminal todavía puede aparecer como tarea actual en `/estado`.
- La cola RAM completa sigue siendo volátil.
- Un snapshot pendiente por fallo solo puede mantenerse inmutable mientras vive
  el proceso; tras un reinicio sin escritura confirmada no existe un recibo
  durable del intento fallido.
- La deduplicación tras reinicio garantiza una sola fotografía semántica; el
  cache del Core y el resultado `empty` son process-local, por lo que reutilizar
  el ID puede volver a registrar el comando episódico.
- Cada fotografía conserva hasta 128 hashes de transacción; la búsqueda de esos
  recibos es lineal sobre las memorias manuales.
- El contexto compacto representa fragmentos de todos los turnos, no cada
  detalle de una transcripción larga.
- El resultado real de las pruebas de campo todavía debe registrarse.
- El filtro visible no elimina explícitamente todos los controles Unicode C1.

## Próxima Prueba De Campo

Registrar:

1. arranque real del backend y tiempo de primera respuesta;
2. conversación social de varios turnos;
3. las dos frases base de guardado;
4. respuesta pública observada y ausencia de excepciones;
5. contenido de RAM todavía disponible después de guardar;
6. reinicio del backend;
7. consulta natural sobre un detalle canario de la fotografía;
8. verificación de que no apareció una segunda memoria al repetir el guardado;
9. consulta Oracle y una frase originalmente motora;
10. `detente` y `descansa` sin efectos físicos desde terminal;
11. funcionamiento de `/estado` y `/salir`;
12. cualquier `[error]` visible en terminal o excepción del backend.

No hace falta abrir Unity para esta prueba.
