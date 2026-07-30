# Chat Conversacional En Terminal

Estado: cliente y guardado manual implementados y validados automáticamente.  
Prueba de campo iniciada el 2026-07-28; resultado todavía no documentado.

Este cliente permite hablar con Lazzaro sin abrir Unity. Es un frontend delgado
del backend existente: no crea otro orquestador, otra memoria ni otra instancia
cognitiva.

## Alcance

- Envía cada turno a `POST /api/v1/comando` con `origen="terminal"`.
- Conserva las rutas Persona y Oracle.
- Reconduce cualquier ruta motora, pasiva o desconocida a Persona.
- Expone únicamente la herramienta `hablar`.
- Muestra solo `accion.parametros.texto_a_comunicar`.
- Nunca imprime `pensamiento_privado`, `razonamiento` ni el JSON completo.
- Mantiene la lectura de recuerdos y registra los turnos hablados en la RAM
  conversacional compartida.
- Permite solicitar mediante habla natural una fotografía persistente de esa
  RAM.

No emite ni ejecuta `cancelar_tarea`, movimiento, herramientas de escritorio o
descanso biológico. El guardado manual es una operación interna de memoria, no
un comando físico ni una herramienta expuesta al cliente.

Un mensaje nuevo sí puede sustituir el objetivo conversacional activo en
`TaskContext`, como en el flujo normal del Core.

## Inicio

Usar el chat con Unity completamente cerrado, según la decisión de diseño
actual.

En una terminal, iniciar el backend:

```powershell
python main.py
```

Cuando el backend indique que está listo, abrir otra terminal en la raíz del
proyecto:

```powershell
python -m terminal_client
```

La URL predeterminada es `http://127.0.0.1:8000` y el timeout HTTP
predeterminado es de 300 segundos, pensado para tolerar una ruta Oracle seguida
de verbalización local. Ambos pueden cambiarse:

```powershell
python -m terminal_client --url http://127.0.0.1:8000 --timeout 420
```

## Comandos Locales

| Comando | Efecto |
|---|---|
| `/ayuda` | Muestra la ayuda del cliente. |
| `/estado` | Consulta `GET /api/v1/estado` sin crear un turno. |
| `/salir` | Cierra el chat limpiamente. |

Los comandos se reconocen solo cuando ocupan toda la entrada, ignorando
mayúsculas y espacios exteriores. Una entrada vacía no genera solicitudes. Un
texto que empieza con otra `/` se rechaza localmente como comando desconocido.

No existe `/guardar`.

## Guardar Lo Hablado En La Mente

Después de conversar, escribir una de las dos frases base:

```text
guarda lo que hemos hablado
guarda lo que tengas en ram en tu mente
```

Se toleran diferencias de mayúsculas, acentos, puntuación y espacios. Hoy el
modelo Random Forest desplegado no contiene la clase `CONVERSATION_SAVE`; por
eso únicamente esta base determinista activa el efecto. Admitir variantes por
ML requiere reentrenar, desplegar y volver a probar el clasificador.

La frase viaja como un mensaje normal. Triage produce
`intent="CONVERSATION_SAVE"` y `route="persona"`, pero el Orchestrator corta la
ruta antes de consultar memoria vectorial, exponer herramientas o llamar al
LLM. `MemoryManager` realiza el guardado y Persona solo construye una respuesta
determinista a partir del resultado confirmado.

Respuestas esperadas:

| Resultado | Texto público |
|---|---|
| Escritura nueva | `Está bien, lo recordaré para después.` |
| Fotografía ya existente | Informa que ya estaba guardada y no la duplica. |
| RAM vacía | Informa que todavía no hay conversación reciente para guardar. |
| Fallo de Chroma | Informa que no pudo guardarla y no finge éxito. |

La fotografía:

- conserva hasta 12 pares Usuario/Lazzaro, en orden;
- limita cada lado a 700 caracteres;
- guarda la transcripción pública, un índice compacto, cantidad e intervalo
  UTC;
- excluye pensamiento, emoción, ruta, intent y metadata privada;
- no agrega a la RAM ni la solicitud ni su confirmación;
- no vacía ni modifica la RAM;
- no activa descanso, biología ni consolidación observacional;
- deduplica por transacción y por huella del contenido.

Si una escritura falla y se reintenta el mismo ID dentro del proceso, se
conserva el snapshot del primer intento aunque lleguen turnos posteriores. Un
retry exitoso no vuelve a ejecutar BehaviorCore ni duplica la experiencia o la
memoria operativa.

## Barreras De Solo Conversación

La garantía no depende de una sola validación:

1. `ToolPolicy` entrega únicamente `hablar` al origen terminal.
2. `Orchestrator` transforma rutas no conversacionales en Persona, omite la
   cancelación y bloquea efectos de descanso.
3. Un firewall final del servidor convierte cualquier acción inesperada en
   `hablar`.
4. El cliente rechaza defensivamente cualquier respuesta cuyo nombre de acción
   no sea `hablar`.
5. Persona recibe una restricción de canal para no fingir que ejecutó o
   ejecutará acciones físicas sin Unity.
6. El texto visible elimina secuencias ANSI basadas en ESC y controles C0/DEL
   antes de escribirlos en la terminal.

## RAM Conversacional Y Persistencia

El buffer inmediato es deliberadamente pequeño y volátil:

- conserva como máximo 12 intercambios usuario/Lazzaro;
- normalmente inyecta solo los últimos 6 intercambios al siguiente turno;
- recorta cada lado de un intercambio a 700 caracteres;
- recorta el resumen total enviado al prompt a 1600 caracteres;
- se pierde al reiniciar el backend.

El guardado manual no convierte la RAM en almacenamiento permanente ni la
repuebla al arrancar. Crea una memoria semántica separada y recuperable que
contiene la fotografía pública. La transcripción persiste en Chroma, pero el
contexto que llega a Persona sigue sujeto a búsqueda semántica, ranking y
límites de resumen; no se garantiza recuperar literalmente cualquier detalle
intermedio de un turno largo.

Los hechos relacionales selectivos siguen usando su mecanismo propio. El
guardado manual tampoco sustituye ni modifica el consolidador observacional.

## Red, Proveedores Y Errores

`python main.py` escucha en `0.0.0.0:8000`, aunque este cliente se conecte a
`127.0.0.1`. Los endpoints no tienen autenticación, así que el backend puede
quedar accesible desde la red local según el firewall del equipo. Usarlo solo
en una red confiable y no exponer el puerto a Internet.

El chat hereda `AI_PROVIDER`, Oracle y la cadena de fallbacks del backend. Si
hay proveedores remotos configurados, el contenido de la conversación puede
enviarse a esos servicios; el cliente terminal no añade aislamiento de datos.
La rama determinista de guardado no llama al LLM.

Ante desconexión, timeout, error HTTP, JSON inválido, acción ausente o una
acción no conversacional, el cliente muestra `[error]` y conserva el bucle para
el siguiente turno. `Ctrl+C` o fin de entrada cierran de forma limpia.

El filtro actual no cubre explícitamente todos los controles Unicode C1
(`U+0080`–`U+009F`); queda como endurecimiento posterior.

## Decisión Postergada

`BehaviorCore` sigue tratando cada `comando_usuario` como el objetivo activo.
Por eso `/estado` puede mostrar el último mensaje del chat como tarea actual.
Separar el contexto de tareas entre Unity y terminal fue postergado
deliberadamente: este cliente se usará con Unity cerrado.

No se soporta el uso simultáneo de Unity y terminal en esta fase. Los locks
añadidos serializan retries con el mismo ID; no convierten al Orchestrator
completo en multiusuario o multiworker.

## Validación

La suite del cliente usa transportes y capas falsas; cubre el contrato HTTP, las
barreras conversacionales, retries y concurrencia del mismo ID:

```powershell
python terminal_client\test_chat.py
```

La persistencia se valida por separado, incluyendo una base Chroma real en un
directorio temporal, cierre y reapertura:

```powershell
python ai_engine\test_manual_memory_save.py
```

También puede verificarse la sintaxis sin iniciar el backend:

```powershell
python -m compileall -q terminal_client ai_engine core memory api tools config main.py
```

Ninguna de estas pruebas modifica `lazzaro_data/`. La conversación con el
backend, el proveedor y la base real del usuario continúa pendiente de prueba
de campo.
