# ROOT-038 B9 — propiedades ambiguas duplicadas en respuesta Unity

Estado máximo de este packet:
`BLOCKER_B9_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Riesgo corregido

Newtonsoft materializa por defecto el último valor de una propiedad JSON
duplicada. Antes de B9, una respuesta podía declarar primero `status=error` y
después `status=ok`, o primero `nombre=hablar` y después
`nombre=girar_avatar`. B7, B5 y B6 podían aceptar el resultado ya colapsado y
Unity alcanzar un handler físico distinto.

## Frontera y orden

```text
JSON crudo
→ ResponseDuplicateWireBoundary (B9)
→ ActionEnvelopeWireBoundary (B7)
→ AuthorityWireBoundary (B5)
→ RespuestaAPI tipada
→ ActionBindingVerifierV1 (B6)
→ onSuccess
```

B9 usa `JsonTextReader` antes de la deserialización y conserva un
`HashSet<string>(StringComparer.OrdinalIgnoreCase)` independiente para:

- todas las propiedades directas del objeto raíz `RespuestaAPI`;
- todas las propiedades directas del objeto `accion`.

La política es genérica e incluye campos desconocidos. Una colisión se rechaza
aunque ambos valores sean iguales y aunque las variantes difieran sólo en
casing. Por tanto `status + Status` y `nombre + Nombre` son duplicados
semánticos.

## Responsabilidades preservadas

B9 no decide si un único `Status`, `Nombre` o `Parametros` es válido: esa
política permanece en B8. Tampoco inspecciona duplicados dentro de:

- `parametros`, propiedad de B7;
- `perception_authority` o `target`, propiedades de B5;
- `decision`, aún fuera del alcance de B9.

B6 no cambia. La ausencia de autoridad no permite omitir B9 porque el boundary
se ejecuta antes de cualquier deserialización o binding.

## Fallo cerrado

Una colisión produce el camino existente `INVALID_RESPONSE`; no existe
fallback legacy:

```text
duplicate root/action property
→ no RespuestaAPI tipada
→ no onSuccess
→ no ProcesarRespuestaDelCerebro
→ no EncolarInicioEjecucion
→ no handler físico
→ no efecto material
```

## Verificación

La matriz permanente se compila con Mono/Newtonsoft instalado por Unity, sin
abrir Unity. Cubre todos los campos actuales de raíz y acción, propiedades
desconocidas, mismos/distintos valores, colisiones de casing, el exploit
`error→ok` más `hablar→girar_avatar`, positivos canónicos y los límites de
propiedad B7/B5/B8.
