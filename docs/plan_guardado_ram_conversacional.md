# Segundo Entregable: Guardado Manual De RAM Conversacional

Estado: implementado y validado automáticamente.  
Prueba de campo: pendiente.  
Última actualización: 2026-07-29.

## Objetivo Cumplido

El usuario puede pedirle a Lazzaro, mediante una frase natural, que guarde lo
conversado hasta ese momento. El sistema toma una fotografía pública de la RAM,
la persiste como una sola memoria semántica recuperable y responde mediante
`hablar`.

No se agregó un comando local `/guardar`, otro endpoint, otro Orchestrator ni
otro MemoryManager.

## Frases Base

La base determinista aprobada contiene exactamente:

```text
guarda lo que hemos hablado
guarda lo que tengas en ram en tu mente
```

La comparación ignora mayúsculas, acentos, puntuación y espacios repetidos. No
convierte solicitudes genéricas como `recuerda que...`, `memoriza...` o
`guarda el archivo...` en un guardado completo de RAM.

El intent interno es `CONVERSATION_SAVE` y su ruta es Persona. Un modelo Random
Forest reentrenado podrá proponer el mismo intent para variantes adicionales;
ese reentrenamiento no forma parte de este entregable y deberá validarse contra
falsos positivos antes de adoptarse.

## Qué Se Guarda

`ConversationMemory.snapshot_publico()` genera una tupla inmutable y atómica
con un máximo de 12 intercambios. Cada lado se limita a 700 caracteres.

La memoria persistida contiene:

- texto público del usuario;
- texto público de Lazzaro;
- orden cronológico;
- timestamp UTC de cada intercambio;
- rango temporal y cantidad de turnos;
- un índice compacto que representa todos los turnos;
- la transcripción pública disponible en RAM.

El documento completo tiene un límite independiente de 20 000 caracteres. El
índice compacto se coloca al inicio para que los turnos finales sigan
representados cuando el recuperador reduzca el contexto a 600 caracteres.

Nunca se copia a esta memoria:

- `pensamiento_privado`;
- emoción interna;
- ruta o intent de diagnóstico;
- metadata del turno o del transporte;
- prompts de sistema;
- razonamiento interno crudo.

La solicitud de control y la confirmación de Lazzaro tampoco se agregan a la
RAM conversacional. El buffer original permanece intacto.

## Persistencia E Idempotencia

`MemoryManager.guardar_fotografia_conversacional()` escribe una sola regla
semántica con:

- `tipo_evento="memoria_conversacional_manual"`;
- `origen="guardado_manual_conversacion"`;
- huella SHA-256 del snapshot público;
- recibo de la transacción;
- cantidad de turnos e intervalo UTC.

Antes de escribir se consulta Chroma por transacción y por huella. La consulta
usa tres estados: encontrado, ausente o error. Si Chroma no puede confirmar la
lectura, el sistema falla de forma reintentable y no escribe a ciegas.

Esto cubre:

- retry HTTP con el mismo `id_transaccion`;
- dos solicitudes sobre una fotografía idéntica;
- respuesta perdida después de que Chroma sí escribió;
- reconstrucción de `MemoryManager` y reapertura real de Chroma.

Cuando otra transacción llega a la misma huella, su recibo se agrega a la
metadata de la unidad existente, sin crear otro recuerdo. Se conservan hasta
128 hashes de transacción por fotografía. Si Chroma no confirma esa
actualización, el sistema devuelve error reintentable en vez de afirmar una
idempotencia durable que no quedó registrada.

Un fallo transitorio queda pendiente dentro del proceso. Repetir la misma
transacción reintenta únicamente la persistencia manual: no vuelve a ejecutar
BehaviorCore ni duplica la experiencia, la memoria operativa o el impacto en
IdentityState. El snapshot público del primer intento también queda fijado:
turnos posteriores no cambian el contenido asociado a esa transacción.

Ese snapshot pendiente vive en el proceso. Si el backend reinicia antes de que
Chroma confirme cualquier escritura, no existe un recibo durable desde el cual
reconstruir el intento fallido.

La garantía durable después de reiniciar cubre la unidad semántica manual. El
cache del Orchestrator, el estado `empty` y los efectos generales del comando
son process-local; reutilizar un ID tras reiniciar puede volver a registrar el
evento episódico, aunque no duplique la fotografía ya guardada.

## Separación Del Consolidador

El guardado manual es memoria semántica explícita. No usa
`consolidar_memorias_si_hace_falta()` y no:

- adelanta ni cambia el batch observacional de 30;
- incrementa su contador;
- borra episodios observacionales;
- simula descanso;
- regenera estado biológico;
- crea un checkpoint paralelo;
- vacía la RAM.

El control se bloquea como descanso tanto desde terminal como desde Unity,
incluso si una futura variante clasificada contiene palabras como `duerme`.

## Respuestas Públicas

Persona verbaliza de forma determinista el resultado ya confirmado:

| Estado | Respuesta |
|---|---|
| Guardado nuevo | `Está bien, lo recordaré para después.` |
| Ya existía | `Eso ya estaba guardado en mi mente; no hace falta duplicarlo.` |
| RAM vacía | `Todavía no tengo una conversación reciente en RAM que pueda guardar.` |
| Error | Informa que no pudo guardarlo y no finge éxito. |

La confirmación de éxito ocurre únicamente después de una escritura confirmada
o de encontrar la misma fotografía ya persistida.

## Validación Automatizada

`ai_engine/test_manual_memory_save.py` comprueba:

- RAM pública y privada intacta;
- orden, cantidad e intervalo temporal;
- ausencia de canarios privados en documento y metadata;
- snapshot independiente de turnos posteriores;
- RAM vacía sin escritura;
- lectura de idempotencia fallida sin escritura a ciegas;
- timeout o `False` después de una escritura efectiva;
- deduplicación por transacción y por huella;
- asociación durable de una segunda transacción;
- visibilidad del último turno dentro del contexto recuperado;
- separación total del consolidador y del descanso;
- cierre, reapertura y consulta de una base Chroma real en un directorio
  temporal.

`terminal_client/test_chat.py` comprueba además:

- confirmación de éxito por el flujo completo del Orchestrator;
- estados vacío, duplicado y error;
- retry que no duplica efectos generales;
- dos retries simultáneos de la misma transacción;
- control manual desde Unity sin descanso;
- ausencia del turno de control en la RAM.

Las 18 suites de `ai_engine`, las cinco funciones de cancelación y la suite del
terminal pasan junto con `compileall`.

## Criterios De Aceptación

- [x] Una frase base guarda exactamente la RAM pública disponible.
- [x] El resultado se recupera después de reabrir Chroma.
- [x] Lazzaro confirma éxito solo después de persistir.
- [x] Un error es visible y no se anuncia como éxito.
- [x] Los retries no duplican la unidad semántica ni los efectos generales.
- [x] La RAM se conserva y el turno de control queda fuera.
- [x] No se modifica el consolidador observacional.
- [x] No se crea otro checkpoint, cerebro, endpoint o canal.
- [x] Las regresiones del primer entregable siguen pasando.
- [ ] Prueba de campo con backend y proveedor reales.

## Fuera De Alcance

- uso concurrente de Unity y terminal;
- separación de `TaskContext`;
- sincronización multiusuario;
- streaming;
- editar o borrar recuerdos desde terminal;
- guardado automático de cada turno;
- repoblar la cola RAM o recrear automáticamente la sesión después de
  reiniciar;
- persistir cadena de pensamiento;
- reentrenar el Random Forest.
