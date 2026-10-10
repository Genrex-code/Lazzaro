# ROOT-038 B4 — underflow de fuerza de lanzamiento en Unity

## Defecto

El contrato Python permite `fuerza_lanzamiento == 0` como liberación sin
impulso y todo valor positivo como liberación con impulso. Unity consumía el
campo mediante una conversión directa a `float`. Un `double` JSON positivo
extremadamente pequeño —por ejemplo `5e-324`— podía convertirse en `0f`; la
acción liberaba el objeto sin aplicar el impulso solicitado.

## Guardia en el límite material

`LazzaroAgent` usa un parser dedicado para `fuerza_lanzamiento`. Un campo
ausente conserva el valor canónico `0f`. Si el campo está presente, su token
debe ser `Integer` o `Float`; `null`, booleanos y strings numéricos se rechazan.

El parser observa primero el valor original como `double`, exige que sea
finito y que pertenezca a `0..20`, y sólo entonces lo convierte a `float`.
Vuelve a validar finitud y rango tras la conversión. La condición crítica

```text
valorRaw > 0.0d && valorUnity == 0.0f
```

falla de forma cerrada con `invalid_physical_parameters`.

## Orden y ausencia de efecto físico

La validación termina antes de `ObjectMaterializationController.TryRelease`.
Por ello un underflow no cambia el objeto sostenido, su parent, su Rigidbody,
el impulso ni genera una recepción material de éxito. El campo ausente y el
cero explícito siguen llegando a `TryRelease` como liberación sin impulso;
los positivos representables conservan la ruta de liberación con impulso.

Las recepciones ROOT-005 permanecen como autoridad del resultado material:
el rechazo y la materialización continúan notificándose mediante
`ReportarResultadoMaterial`, nunca mediante un booleano desnudo.

## Alcance y riesgo residual

Este correctivo congela únicamente la distinción categórica entre **cero** y
**solicitud positiva de lanzamiento**. No define todavía una política global
ROOT-038 para el redondeo ordinario `float64 -> float32`; un valor positivo que
sigue siendo positivo puede cambiar ligeramente de magnitud al representarse
como `float` en Unity. El dominio canónico Python, ROOT-002 y ROOT-042 no se
modifican.

La validación permanente cubre ausencia, cero, valores positivos y límites,
tipos JSON inválidos, no finitos, underflow de `5e-324`, orden previo a
`TryRelease` y conservación de las recepciones ROOT-005. La prueba material en
Unity Play Mode permanece fuera de este bloque.
