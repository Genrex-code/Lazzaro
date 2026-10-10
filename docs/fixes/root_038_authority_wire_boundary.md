# ROOT-038 B5 — Boundary estricto de autoridad antes de coerción Unity

Estado máximo de este packet: `BLOCKER_B5_IMPLEMENTATION_COMPLETE / HUMAN_REVIEW_PENDING`.

## Problema

`JsonConvert.DeserializeObject<RespuestaAPI>` materializaba primero los campos
`int`/`long`. Newtonsoft podía convertir strings numéricos, booleanos y floats,
y los campos ausentes quedaban en cero. Esas conversiones destruían la
semántica del token JSON antes de evaluar la autoridad ROOT-029.

## Boundary

`AuthorityWireBoundary` recorre el texto crudo mediante `JsonTextReader` antes
de construir `RespuestaAPI`. Cuando existe `accion.perception_authority`, exige:

- `version` presente, token `Integer` y valor exacto `1`;
- `target.contract_version` presente, token `Integer` y valor exacto `1`;
- ambas generaciones presentes, token `Integer`, no negativas y representables
  por `Int64`;
- igualdad entre la generación de la autoridad y la del target;
- `target` como objeto;
- ausencia de propiedades duplicadas dentro de autoridad y target.

También rechaza `accion` o `perception_authority` duplicados: sin esa guardia,
un segundo objeto action podría reemplazar el packet ya inspeccionado antes de
la deserialización tipada. La detección no se amplió globalmente; duplicados no
relacionados con este camino permanecen fuera de B5 para la caracterización
general de ROOT-038.

Una autoridad ausente o `null` conserva las reglas legacy actuales. Una
autoridad presente pero inválida produce `INVALID_RESPONSE` en `ApiClient`, no
llama `onSuccess` y por tanto no alcanza `ProcesarRespuestaDelCerebro`, inicio
de ejecución, handlers físicos, resolución exacta ni materialización.

## Matriz

Los tests C# puros usan la copia instalada de Newtonsoft sin abrir Unity.
Cubren 0, 1 e `Int64.MaxValue`; strings numéricos, floats integrales y
fraccionales, booleanos, ausencia, null, negativos, overflow, generaciones
distintas y duplicados de las cuatro propiedades críticas. También fijan que
una generación inicial ausente nunca se convierta en autoridad para generación
cero.

## Límite adyacente: action binding SHA-256

B5 no cambia el contrato ROOT-029. Unity sólo exige que
`action_binding_sha256` sea no vacío. No recomputa el hash contra acción,
target, políticas, parámetros y seguridad antes de ejecutar. Los chequeos
estructurales existentes de acción, parámetros, captura/generación, subtipo,
scope y target exacto siguen activos.

La verificación cross-language del hash necesita decisión humana y vectores de
canonicalización compartidos; no se absorbió en este correctivo.
