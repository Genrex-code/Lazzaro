# ROOT-038 B1 — distancia de navegación consumida por Unity

## Defecto

`NavigateToObjectParams` ya aceptaba `distancia` opcional y ROOT-029 la incluía
en los parámetros canónicos ligados a la acción. El consumidor Unity recibía
el campo dentro de `AccionSaliente.parametros`, pero `LazzaroAgent` no lo leía
y `MovementController` siempre usaba su `NavMeshAgent.stoppingDistance`
configurado. Una acción válida podía por tanto materializar otra separación.

## Semántica V1 exacta

El destino geométrico no cambia: Unity toma
`Collider.ClosestPoint(transform.position)` y lo proyecta al NavMesh mediante
`NavMesh.SamplePosition`. Cuando `distancia` está presente, su valor controla
el `NavMeshAgent.stoppingDistance` y el umbral horizontal del bucle de esa
acción respecto del destino proyectado.

Esto **no** promete una distancia euclidiana literal a la superficie final del
collider. La proyección al NavMesh, el tamaño del agente y la navegación física
siguen formando parte de la materialización.

Cuando `distancia` no está presente, Unity conserva exactamente el umbral
histórico configurado en el `NavMeshAgent` (normalizado a un mínimo de `0.1` en
`Awake`).

## Defensa del consumidor

`LazzaroAgent` distingue campo ausente de campo presente. Un valor presente
sólo se admite si el token JSON es numérico, finito y está dentro de
`0.1 <= distancia <= 10.0`. No se aceptan booleanos, strings numéricos, `null`,
NaN, infinitos ni valores fuera de rango. Un valor inválido genera el outcome
de ejecución fallido antes de llamar a `MovementController`; no hay movimiento
ni sustitución por el default histórico.

`MovementController` repite la validación finita y de rango para cubrir callers
directos. No clampa el valor válido.

## Propiedad por acción y restauración

El override guarda el `stoppingDistance` previo en un contexto ligado al
`actionId`. Se restaura desde el cierre común de movimiento, que cubre éxito,
ruta inválida, atasco y otras terminaciones. `FrenarMovimiento`, `OnDisable` y
la sustitución defensiva de una coroutine también atraviesan ese cierre.

Después de restaurar, el contexto se elimina. Por ello una acción posterior
sin `distancia` vuelve al valor histórico y nunca hereda el override anterior.

## Contratos preservados

- ROOT-002 conserva límites, finitud y rechazo de `bool`/strings en el wire.
- ROOT-042, `MotorActionV1`, `NavigateToObjectParams` y su adapter no cambian.
- ROOT-029 conserva hash, referencia de autoridad y resolución exacta. El mismo
  valor ya ligado por Python es el que ahora consume Unity.
- No se introdujo búsqueda por display name ni nearest fallback en la ruta de
  autoridad.

## Validación

Las pruebas estáticas verifican ausencia, bordes `0.1`/`10.0`, valor
intermedio, rechazo fail-closed, propagación por rutas legacy y ROOT-029,
restauración en finalización/cancelación/interrupción y conservación del target
exacto. La comprobación material en Unity Play Mode sigue pendiente.
