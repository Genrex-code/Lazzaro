# Checklist de rotación de credenciales — incidente `.env`

Estado: **contención y revocación externas completadas; gate de seguridad de Fase 0 cerrado.**

No escribir claves, fragmentos, últimos caracteres ni hashes de valores secretos en este documento, commits, tickets o capturas.

## Alcance confirmado

El archivo `.env` aparece en el snapshot local de `origin/main` y tuvo cambios en cuatro commits:

- `1ccf805` — 2026-05-23
- `c6f7311` — 2026-05-23
- `3505270` — 2026-05-26
- `842414a` — 2026-06-09

Nombres de credenciales presentes en alguna versión histórica, inspeccionados sin mostrar valores:

- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `XAI_API_KEY`
- `KIMI_API_KEY`
- `DEEPSEEEK_API_KEY` — alias histórico con typo; al reemplazar usar `DEEPSEEK_API_KEY`

`ORACLE_API_KEY` no apareció en esas versiones. El baseline podía usar `GROQ_API_KEY` como credencial de Oracle; el runtime actual opera solo con Gemini.

## Contención local

- [x] No hacer `push` de la rama ni del tag `pre-audit-2026-08`.
- [x] Retirar `.env` del índice sin borrar la copia local.
- [x] Ignorar `.env` y variantes `.env.*`, conservando `.env.example` versionable.
- [x] Crear `.env.example` sin valores secretos.
- [x] Auditar los nuevos archivos contra los valores locales y patrones comunes de claves.

La eliminación del tracking evita exposiciones nuevas, pero no borra las versiones históricas.

## Rotación externa — ejecutar proveedor por proveedor

Para cada proveedor afectado:

1. Crear una credencial nueva en el portal oficial de la cuenta.
2. Reemplazar únicamente la variable correspondiente en el `.env` local.
3. Reiniciar LAZZARO y validar el health check del proveedor sin imprimir la clave.
4. Confirmar un smoke mínimo exitoso.
5. Revocar la credencial anterior.
6. Registrar solo proveedor, fecha, responsable y resultado; nunca el valor.

| Proveedor | Variable nueva | Crear | Validar | Revocar anterior |
|---|---|:---:|:---:|:---:|
| Google Gemini | `GEMINI_API_KEY` | [x] | [x] | [x] |
| Groq | retirada localmente | N/A | N/A | [x] |
| xAI | retirada localmente | N/A | N/A | [x] |
| Moonshot/Kimi | retirada localmente | N/A | N/A | [x] |
| DeepSeek | retirada localmente | N/A | N/A | [x] |

Registro seguro:

- Google Gemini — 2026-08-29 — responsable: Jairo/Codex — clave nueva validada antes y después de la revocación; health check HTTP 200; clave anterior revocada.
- Decisión operativa — 2026-08-29 — LAZZARO queda en modo solo Gemini. Se retiraron del `.env` las credenciales de Groq, xAI, Moonshot/Kimi y DeepSeek, sin crear reemplazos; los fallbacks remotos quedaron deshabilitados.
- Groq — 2026-08-29 — responsable: Jairo — cuenta desactivada por inactividad y clave eliminada, según verificación del usuario.
- xAI — 2026-08-29 — responsable: Jairo — portal verificado sin claves API presentes.
- Moonshot/Kimi — 2026-08-29 — responsable: Jairo — portal verificado sin claves API presentes.
- DeepSeek — 2026-08-29 — responsable: Jairo — revocación confirmada y portal verificado sin claves API presentes.

Las capturas usadas para verificar xAI, Moonshot/Kimi y DeepSeek fueron revisadas, pero no se incorporaron al repositorio porque contienen detalles personales del navegador. La necesidad de elegir otro `GROQ_MODEL` queda anulada al haberse retirado ese proveedor.

## Validación posterior

- [x] La carga de `Settings()` termina sin exponer valores.
- [x] El backend arranca y el health check del primario pasa.
- [x] `/api/v1/estado` devuelve HTTP 200.
- [x] La consulta Oracle usa el proveedor esperado o un fallback documentado.
- [x] `git status --ignored --short .env .env.example` muestra `.env` ignorado y `.env.example` versionado.
- [x] Un escaneo de secretos del diff y del árbol actual no encuentra credenciales.

## Historial remoto

Rotar/revocar las claves neutraliza los secretos históricos. Si el repositorio fue público o la política exige eliminar también los blobs, hará falta una operación separada con `git filter-repo`, coordinación con todos los clones y `force push` de ramas/tags. Esa reescritura es destructiva y no está autorizada por esta remediación local.

El tag local `pre-audit-2026-08` conserva el baseline comprometido y referencia un commit que contiene `.env`; no debe publicarse. Cualquier decisión de eliminarlo o recrearlo se toma después de la rotación.
