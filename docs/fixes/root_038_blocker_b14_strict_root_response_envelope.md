# ROOT-038 B14 — Strict root response envelope

## Estado

`BLOCKER_B14_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`

B14 cierra la coercion de escalares del objeto raiz `RespuestaAPI` antes de la
deserializacion tipada. No cambia los contratos de las acciones, los parametros
fisicos, la autoridad perceptiva ni el binding ROOT-029.

## Evidencia original

Antes de B14, un response podia enviar `id_transaccion` como entero. Newtonsoft
lo convertia a `string`; para una solicitud cuyo id era `"7"`, el entero `7`
superaba la correlacion exacta y una accion fisica `dispatched` podia ejecutarse.

El probe que motivo el bloqueo produjo:

```text
B9=true B7=true B11=true B5=true B6=true
wire token=Integer(7)
DTO id_transaccion="7"
request id_transaccion="7"
exact_match=true
action=navegar_a_posicion
lifecycle=dispatched
```

## Caracterizacion Python

`RespuestaAPI` acepta estos defaults de entrada y los emite en su serializacion
normal:

```text
status="ok"
mensaje=""
id_transaccion=null
accion=null
decision=null
timestamp_servidor=<ISO-8601 string>
```

La entrada Pydantic acepta `status` omitido, `mensaje` omitido,
`id_transaccion` omitido/null, `accion` omitida/null, `decision` omitida/null y
`timestamp_servidor` omitido. Aunque Pydantic tambien puede convertir ciertos
numeros de entrada en `datetime`, la salida productiva siempre serializa el
timestamp como string. `id_transaccion` rechaza booleanos y numeros. Los extras
raiz se ignoran actualmente porque `RespuestaAPI` no declara `extra="forbid"`.

## Frontera B14

`ResponseEnvelopeWireBoundaryV1` se ejecuta despues de B9 y antes de B7:

```text
raw JSON
→ B9 ResponseDuplicateWireBoundary
→ B14 ResponseEnvelopeWireBoundaryV1
→ B7 ActionEnvelopeWireBoundary
→ B11 PhysicalActionWireBoundaryV1
→ B5 AuthorityWireBoundary
→ typed RespuestaAPI
→ B6 ActionBindingVerifierV1
```

La politica raw es:

| Campo presente | Tokens permitidos |
|---|---|
| `status` | string exacto `ok`, `error` o `pendiente` |
| `mensaje` | string |
| `id_transaccion` | string o null |
| `accion` | object o null |
| `decision` | object o null |
| `timestamp_servidor` | string |

Los campos omitidos no reciben defaults en Unity. Las capas y consumidores
posteriores conservan su comportamiento fail-closed. B14 no valida el formato
del timestamp ni el interior de `accion` o `decision`.

## Ownership preservado

- B9 conserva la unica autoridad sobre duplicados/case collisions.
- B7/B13 conservan la forma y escalares de `AccionSaliente`.
- B11 conserva los parametros de acciones fisicas.
- B5 conserva la autoridad perceptiva raw.
- B6 conserva la reconstruccion y comprobacion del binding.
- B8 sigue abierto: un unico nombre raiz con casing distinto se reconoce de
  forma case-insensitive, pero B9 rechaza dos variantes simultaneas.
- Los extras raiz continuan admitidos hasta que el contrato general ROOT-038
  decida su politica versionada.

## Failure semantics

Todo rechazo B14 termina en el `INVALID_RESPONSE` local ya existente, antes de
crear un `RespuestaAPI`. Por tanto no existe callback `onSuccess`, correlacion
operativa, dispatch de accion ni efecto material.

## Pruebas

`unity_client/test_root_038_response_envelope_boundary.py` cubre la matriz de
tipos de los seis campos raiz, los tres valores canonicos de status, valores
desconocidos, campos omitidos, B8, extras, ownership B9 y el exploit original.
La matriz C# se compila y ejecuta con el Mono/Newtonsoft instalado por Unity sin
abrir el editor ni usar Play Mode.
