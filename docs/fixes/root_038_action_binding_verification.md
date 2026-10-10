# ROOT-038 B6 — verificación del binding físico en Unity

Estado máximo de este packet: `BLOCKER_B6_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Boundary y responsabilidad

El flujo de una respuesta queda ordenado así:

```text
JSON crudo
→ AuthorityWireBoundary (B5: tipos/tokens/versiones/generación)
→ RespuestaAPI tipada
→ ActionBindingVerifierV1 (B6: integridad ROOT-029)
→ ApiClient.onSuccess
```

B5 y B6 permanecen separados. Si `perception_authority` está ausente o es
`null`, se conserva el camino legacy. Si está presente, B6 es obligatorio:
cualquier incoherencia produce `INVALID_RESPONSE` y no llega a `onSuccess`, al
agente, a la cola de ejecución, al resolvedor exacto ni a handlers físicos.

## Binding canónico V1

`ActionBindingVerifierV1` reconstruye la misma secuencia UTF-8 sin BOM que:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

El orden fijo V1 es:

```text
action_name
authority_policy_id
binding_version
freshness_policy_id
generation_binding_mode
id_accion
interaction_type
parameters
safety_disposition
safety_reason_code
target
```

Dentro de `target`:

```text
contract_version
generation
logical_world_id
producer_capture_id
producer_epoch_id
producer_instance_id
producer_object_id
runtime_object_id
stream_id
```

`canonical_parameters_json` debe ser exactamente un objeto JSON, sin
documentos adicionales, comentarios ni propiedades duplicadas. Se compara
estructuralmente con `accion.parametros` y luego se inserta literalmente en el
binding. En particular, B6 no parsea y reserializa sus números.

El codificador de strings conserva Unicode sin escape ASCII artificial,
incluidos NFC/NFD, U+007F, U+0085, U+2028/U+2029 y escalares astrales. Escapa
comillas, barra inversa y controles U+0000–U+001F como Python; `/` permanece
literal. Los pares sustitutos válidos llegan a UTF-8 como un escalar y un
sustituto UTF-16 aislado falla cerrado.

## Correlación y rechazo

Antes de comparar la huella se exige:

- identidad y nombre iguales entre acción y autoridad;
- captura y generación iguales entre autoridad y target;
- campos V1 requeridos presentes y valores enumerados conocidos;
- hash recibido de exactamente 64 caracteres hexadecimales minúsculos;
- igualdad ordinal entre SHA-256 reconstruido y recibido.

La matriz conserva la huella original mientras muta individualmente acción,
parámetros, target, generación, políticas, modo, subtipo y evidencia de
seguridad. También cubre el bypass conocido que cambia simultáneamente
`fuerza` de `1` a `100` en ambas representaciones: la huella reconstruida ya no
coincide y la respuesta se rechaza antes de cualquier efecto.

## Vectores cross-language

`core/fixtures/root_038_action_binding_v1.jsonl` contiene los casos generados
desde la implementación Python. Cada línea conserva acción, autoridad, JSON
canónico, bytes UTF-8 en hexadecimal y SHA-256. El harness se compila y ejecuta
con Mono/Newtonsoft instalados por Unity, sin abrir Unity, y compara los tres
artefactos exactamente, no sólo el hash.

Los vectores cubren las ocho acciones físicas, generaciones 0, 1 e
`Int64.MaxValue`, interacción nula/no nula/vacía legal, enteros, floats
ordinarios, subnormal mínimo y double finito máximo, dirección B3 normalizada,
ceros canónicos, opcionales nulos y todos los casos de strings exigidos.

## Límite de seguridad

SHA-256 es una huella de integridad contractual. B6 no añade autenticación de
red, MAC, firma, claves ni cambios TLS. Modificar el conjunto de campos o la
canonicalización requiere una nueva versión del binding; V1 no puede cambiar
silenciosamente.
