# Seguimiento separado — persistencia conversacional híbrida

Estado: **diseño/backlog; no implementado en ROOT-017**. Ownership propuesto: Memory/Core. No asignar un ROOT nuevo ni mezclar este trabajo con el puente factual de Persona sin revisión del backlog canónico.

- Cuando la memoria conversacional esté sucia, hacer un checkpoint automático y silencioso cada `N` turnos conversacionales **completados**, con `N` configurable. Un turno recibido pero abortado no cuenta como completado.
- Un autosave exitoso no genera acuse público ni respuesta Persona; el guardado manual de sesión sigue siendo un flush final explícito con acuse operativo tipado.
- La política de consolidación de Memory decide qué contenido merece persistencia e indexación. Chroma sigue siendo infraestructura de retrieval/índice, no la autoridad que decide qué recordar.
- El diseño futuro debe definir dirty/clean, checkpoint idempotente, concurrencia, crash/retry, confirmación durable y qué ocurre si falla un autosave sin inventar éxito. Reutilizar los contratos de receipt y ownership de Memory aplicables; no añadir una segunda escritura sólo para telemetría.

Esto no introduce scheduler, escritura, consulta Chroma ni cambio de API/Unity en ROOT-017.
