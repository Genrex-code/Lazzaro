# ROOT-038 B11 — contrato raw-wire de parámetros físicos

Estado máximo de este packet:
`BLOCKER_B11_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Defecto

`AccionSaliente.parametros` es deliberadamente flexible en el DTO público.
Antes de B11, varios handlers Unity convertían tokens con `ToString()` más
`float.TryParse` o con `Value<float>()`. Un string numérico o un `null` podía
por ello materializar una acción física que el contrato Motor Python no habría
emitido con esa representación.

## Frontera y orden

```text
JSON crudo
→ B9 ResponseDuplicateWireBoundary
→ B7 ActionEnvelopeWireBoundary
→ B11 PhysicalActionWireBoundaryV1
→ B5 AuthorityWireBoundary
→ RespuestaAPI tipada
→ B6 ActionBindingVerifierV1
→ onSuccess
```

B11 valida exclusivamente el objeto `parametros` de las ocho acciones físicas.
No valida autoridad, target, binding, attestation, cancelación, `hablar` ni la
arquitectura general de DTOs.

## Fuente de verdad

La matriz refleja `ai_engine/motor_schemas.py` y
`ai_engine/motor_resolution.py`:

| Acción | Campos requeridos | Campo opcional/default | Dominio |
|---|---|---|---|
| `navegar_a_objeto` | `nombre_objeto` string | `distancia` | ausente o número `0.1..10` |
| `navegar_a_posicion` | `x`, `y`, `z` | ninguno | números finitos |
| `alejarse_de_objeto` | `nombre_objeto` string | `distancia=2.0` | ausente o número `0.5..5` |
| `girar_avatar` | `grados` | ninguno | número finito `-360..360` |
| `interactuar_objeto_3d` | `nombre_objeto`, `tipo_accion` | ninguno | strings; enum canónico exacto |
| `agarrar_objeto` | `nombre_objeto` | ninguno | string |
| `soltar_objeto` | ninguno | `fuerza_lanzamiento=0.0` | ausente o número `0..20` |
| `aplicar_fuerza` | nombre, tres componentes, fuerza | ninguno | dirección finita no cero; fuerza `(0,100]` |

No se permiten campos extra ni nombres de parámetro con casing distinto. Los
números sólo admiten tokens JSON `Integer`/`Float`, siempre finitos. Strings,
booleanos, `null`, objetos y arrays no se convierten a números. Los strings de
objeto y enum sólo admiten tokens JSON `String`; el vocabulario de interacción
es `mirar`, `tocar`, `inspeccionar`, `golpear`, `usar` y `activar`.

## Ausencia, null y defaults

Los probes ejecutables Python produjeron:

| Campo | Ausente | Entrada Python `null` | Wire canónico / B11 |
|---|---|---|---|
| navegación `distancia` | omitido | aceptado y canonicalizado como omitido | ausente o `null` válidos; ambos conservan el umbral histórico |
| soltar `fuerza_lanzamiento` | `0.0` | inválido | ausente válido; `null` inválido |
| alejarse `distancia` | `2.0` | inválido | ausente válido; `null` inválido |

La distinción conserva los dominios B1–B4. Sólo navegación permite `null`,
porque su tipo Python es realmente opcional y lo canonicaliza como ausencia;
el handler conserva entonces el umbral histórico sin inventar una distancia.
Los otros dos `null` siguen fallando y sus defaults sólo aplican a ausencia.

## Defensa en profundidad

`TryLeerFloat` ahora exige un token numérico, lee primero `double`, rechaza no
finitos y también rechaza overflow al convertir a `float`. El handler de
`alejarse_de_objeto` dejó de usar `Value<float>()`; sólo aplica `2.0f` cuando el
campo está ausente y usa el mismo parser estricto cuando está presente.

La autoridad de esquema sigue siendo B11; estas comprobaciones cubren callers
directos futuros sin duplicar en cada handler todas las reglas y rangos.

## Drift float64 → float32 que permanece fuera de B11

Se conserva para el sweep general ROOT-038:

- coordenadas finitas mayores que `float.MaxValue` son válidas para Python y
  son rechazadas fail-closed por el consumidor Unity;
- magnitudes positivas menores que el mínimo representable `float` pueden
  quedar en cero y son rechazadas por `aplicar_fuerza`;
- coordenadas o componentes subnormales pueden redondearse a cero; la dirección
  de fuerza conserva al menos su componente dominante y el materializer vuelve
  a normalizarla.

Son diferencias Type B/C P2: no convierten input inválido en ejecución física.
B11 no introduce clamp, tolerancia ni una política global float64→float32.

## Verificación permanente

La matriz C# se compila y ejecuta con el Mono/Newtonsoft instalado por Unity,
sin abrir Unity. Cubre los ocho esquemas, defaults, ausencia/null, campos
requeridos, extras, casing, vocabulario enum, límites, no finitos, vector cero y
los exploits obligatorios con strings, booleanos, objetos y arrays. El fallo
ocurre como `INVALID_RESPONSE` antes de DTO tipado, `onSuccess` o cualquier
handler físico.
