# Gate de no regresión de AuditTrace — OBS-008

Estado: **gate final de observabilidad definido y verificado.**

OBS-008 cierra el sidequest de observabilidad con una regla funcional simple:
AuditTrace puede perder una traza, pero nunca puede cambiar la respuesta ni los
efectos reales del turno que intenta observar. Este gate no amplía AuditTrace
v1, no añade stages y no modifica el flujo cognitivo.

## Contrato no negociable

Ante un fallo devuelto o una excepción durante la escritura de AuditTrace:

- la respuesta principal conserva el mismo valor funcional;
- el logger humano emite un warning seguro cuando existe una frontera capaz de
  hacerlo;
- el fallo no genera otro `stage=error` y no abre la recursión
  `logger → AuditTrace → logger → AuditTrace`;
- no se repiten Triage, recuperación o escritura de memoria, el resolver ni
  `_gestionar_ejecucion()`;
- no se altera el cache de transacciones ni se fabrican entradas nuevas;
- no se duplican las escrituras persistentes o conversacionales observadas;
- los locks por transacción se liberan también en salidas tempranas y errores;
- el error de auditoría nunca sustituye ni enmascara un error funcional real.

El resultado observable de `AuditTraceJSONLWriter.write()` continúa siendo
éxito o fallo. El runtime no usa ese resultado para cambiar la decisión.

## Stages protegidos por el gate

La tolerancia aplica a todos los stages de AuditTrace v1, incluidos los tres
límites más sensibles:

- `event_start`: su fallo no impide procesar el evento;
- `event_end`: su fallo no cambia el valor que el turno ya produjo ni oculta la
  liberación del lock;
- `error`: su fallo solo deja el warning humano; jamás intenta escribir otro
  error de auditoría.

Lo mismo rige para `triage`, `memory`, `decision` y `execution`. Si una línea no
puede persistirse, la ausencia de esa línea indica pérdida de telemetría, no una
decisión funcional distinta.

## Fallo físico real del writer

El gate no depende únicamente de un fake que lance una excepción. Incluye el
caso real en que la ruta padre esperada como directorio ya existe como archivo.
El sistema operativo produce `FileExistsError`; el writer:

1. devuelve `success=false` y solo el tipo estable del error;
2. emite un único warning por el logger humano;
3. no propaga la excepción al runtime;
4. rechaza una reentrada del logger con `ReentrantAuditWrite`;
5. no crea ni sobreescribe un archivo alternativo.

Las escrituras parciales continúan bajo el mismo lock hasta completar la línea;
un retorno de cero bytes sigue tratándose como `OSError`. Esos controles evitan
que la tolerancia a fallos convierta el JSONL en un archivo silenciosamente
corrupto.

## Equivalencia funcional

La aceptación comprueba un resultado funcional conocido durante el fallo de
auditoría y verifica que no cambien sus invariantes:

- acción y parámetros públicos finales;
- cantidad de llamadas a la capa que resolvió;
- una única llamada a `_gestionar_ejecucion()`;
- escrituras de memoria y registro de la acción;
- entrada de cache, cuando el turno sea cacheable;
- estado final de `_transacciones_locks`.

En un cache hit, AuditTrace conserva el contrato `event_start → event_end` si
ambas líneas pueden escribirse. Si el writer falla, la acción cacheada sigue
siendo exactamente la misma y no se reejecuta BehaviorCore, memoria, decisión o
ejecución.

## Error funcional frente a error de auditoría

Un fallo funcional real conserva su semántica propia. Por ejemplo:

```text
event_start → error(component=behavior_core) → event_end
```

si el writer funciona. Si falla al persistir cualquiera de esas líneas, el
resultado funcional —incluida la propagación o recuperación que ya definía el
runtime— permanece intacto. El fallo del writer no se registra como un segundo
`error`, porque ese registro volvería a depender del mismo writer defectuoso.

## Evidencia del gate

Los controles existentes cubren conjuntamente:

- fallo físico `FileExistsError`, warning único y corte de recursión en
  [`core/test_audit_writer.py`](../../core/test_audit_writer.py);
- escrituras UTF-8 completas, append-only, concurrencia, partial write y retorno
  de cero bytes en el mismo runner;
- respuesta funcional, cache hit, cierre desde `finally`, liberación de locks y
  fallos por stage en
  [`core/test_orchestrator_audit.py`](../../core/test_orchestrator_audit.py);
- no repetición del resolver Persona ni de la gestión de ejecución bajo el
  fallo inyectado;
- privacidad de errores y continuidad hasta `event_end` cuando los demás
  registros sí pueden persistirse.

Resultado del gate de observabilidad: **PASS**.

- Regresión directa del repositorio: 153 PASS, 0 fallos inesperados y 1 fallo
  conocido del baseline (`memory.test_chromadb_flow.test_eliminacion`).
- Compilación de los módulos del runtime: PASS.
- `git diff --check`: PASS.

OBS-008 es el cierre de la serie OBS-001–OBS-008. No existe un OBS-009
implícito. El siguiente paso del plan es la **auditoría profunda por bloques**,
comenzando por **Bloque 1 — BOOT/CONFIG**.
