# ROOT-038 — contrato Python ↔ Unity V1

## Estado

`FINAL_V1_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`

Este cambio no declara `HUMAN_APPROVED` ni `CLOSED_CODE`.

## Propiedad y versión

Los modelos Pydantic de Python son la fuente canónica. `RespuestaAPI` es el
único writer productivo y emite obligatoriamente `wire_version: 1`. Esta versión
es independiente de `decision.version`, `perception_authority.version` y
`target.contract_version`.

Unity clasifica el documento antes de deserializar:

- `wire_version == 1` como token Integer exacto: sólo lector V1;
- campo ausente: lector legacy V0 explícito;
- versión desconocida, mal formada o con casing distinto: rechazo;
- un documento que declara V1 y falla nunca se reintenta como V0.

## Fachadas y orden

`UnityResponseWireContractV1` coordina, sin duplicar la propiedad semántica de
los boundaries congelados:

1. clasificación de versión y B9 para duplicados raíz/acción;
2. en V1, duplicados/case-collisions en cualquier objeto;
3. forma V1 exacta, casing y extras;
4. B14 envelope raíz;
5. B7/B13 envelope de acción y lifecycle;
6. B11 parámetros físicos;
7. B5 autoridad/target;
8. deserialización tipada;
9. B6 binding SHA-256.

B15 permanece fuera de la fachada porque compara la respuesta con estado local
de confirmación y lifecycle. `ApiClient` sólo recibe un DTO completamente
validado; un fallo nunca expone un objeto ejecutable parcial.

Los errores HTTP usan `UnityPublicErrorWireContractV1`, una fachada separada.
Valida casing, duplicados, extras, tokens, código, status y UUID. El texto
`error.message` remoto continúa sin autoridad de UX: `ApiClient` selecciona la
plantilla local por `code`.

## Forma V1

Todos los campos siguientes están presentes. Sólo los indicados pueden ser
`null`.

### RespuestaAPI

- `wire_version`: Integer exacto `1`;
- `status`: `ok | error | pendiente`;
- `mensaje`: String;
- `id_transaccion`: String o null;
- `accion`: objeto `AccionSaliente` o null;
- `decision`: objeto `DecisionPublica` o null;
- `timestamp_servidor`: String ISO producido por Python.

### AccionSaliente

- `id_accion`, `nombre`: String;
- `parametros`: Object, nunca null;
- `requiere_confirmacion`: Boolean;
- `estado_ejecucion`: enum lifecycle exacto;
- `id_transaccion_origen`: String o null;
- `parametros_descripcion`: Array;
- `perception_authority`: objeto o null según la acción.

`DecisionPublica` requiere exactamente `version`, `disposition` y
`reason_code`, con enums actuales de Python. Continúa siendo una proyección
informativa y no adquiere autoridad de ejecución.

V1 rechaza nombres con casing distinto, campos desconocidos, duplicados exactos
o por casing, coerción escalar y enums desconocidos. La extensibilidad futura
requiere contenedor explícito o nueva versión.

## V0

El lector V0 existe únicamente para wire sin `wire_version`. Conserva la
compatibilidad de casing individual B8 y autoridad omitida donde ya era legacy,
pero mantiene B9, B14, B7/B13, B11, B5/B6 cuando hay autoridad y B15. Ninguna
corrección B1–B15 fue revertida.

## Dominio numérico V1

Los parámetros físicos se canonicalizan en Python al valor float32 finito que
Unity puede representar antes de ledger, JSON canónico, SHA y wire. Se rechazan
overflow y cualquier no-cero que colapse a cero. El cero físico se representa
únicamente como `+0.0`.

La dirección de `aplicar_fuerza` se normaliza de forma robusta y después se
redondea a float32. Su revalidación es un punto fijo exacto. Unity verifica una
tolerancia acotada de `2e-6` para la norma y mantiene su normalización local como
defensa en profundidad. V0 conserva direcciones no unitarias documentadas.

Los campos cubiertos son distancia de navegación, coordenadas, distancia de
retirada, grados, fuerza de lanzamiento, dirección y magnitud de fuerza.

`generation` y `captured_at_utc_ms` usan tokens Integer estrictos en
`0..Int64.MaxValue`; strings, floats, bools, negativos y overflow son inválidos.

## Autoridad ROOT-029

V1 exige `perception_authority` válida para:

- `navegar_a_objeto`;
- `alejarse_de_objeto`;
- `interactuar_objeto_3d`;
- `agarrar_objeto`;
- `aplicar_fuerza`.

Ausencia o null rechaza toda la respuesta V1. V0 mantiene su compatibilidad
legacy, sin posibilidad de downgrade desde un V1 inválido.

## Roles de campos

| Campo | Rol V1 |
|---|---|
| `estado_ejecucion`, `id_accion`, `nombre`, `parametros`, autoridad | OPERACIONAL |
| `id_transaccion_origen`, autoridad y binding | PROVENANCE/AUDIT |
| `decision`, `timestamp_servidor`, `parametros_descripcion` | INFORMACIONAL |
| `requiere_confirmacion` | INFORMACIONAL en wire; el lifecycle/B15 es la autoridad operacional |

Ningún campo informativo se promueve a autoridad.

## Golden fixtures y migración

`api/fixtures/root_038_wire_v1.jsonl` congela respuestas sin acción, pending,
acción despachada, hablar, cuatro disposiciones públicas y errores. El fixture
físico existente `core/fixtures/root_038_action_binding_v1.jsonl` cubre los ocho
tipos físicos, UTF-8, JSON canónico, SHA-256, generaciones límite y dirección de
fuerza float32.

Las pruebas negativas cubren missing/null/tipo/casing/extras/duplicados/enums,
versión desconocida o mal formada, autoridad ausente, overflow, underflow,
signed zero e int64. Existe una prueba explícita de que V1 inválido no cae a V0.

La migración usa dual reader y un único writer V1. Versión desconocida falla
cerrada. Cambios de tipo, requiredness, enum o semántica física exigen una nueva
versión; no se introdujo un framework general de generación Pydantic→C#.
