# Checklist de rotación de credenciales — incidente `.env`

Estado: **contención local preparada; rotación externa pendiente.**

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

`ORACLE_API_KEY` no apareció en esas versiones. El runtime actual puede usar `GROQ_API_KEY` como credencial de Oracle.

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
| Google Gemini | `GEMINI_API_KEY` | [ ] | [ ] | [ ] |
| Groq | `GROQ_API_KEY` | [ ] | [ ] | [ ] |
| xAI | `XAI_API_KEY` | [ ] | [ ] | [ ] |
| Moonshot/Kimi | `KIMI_API_KEY` | [ ] | [ ] | [ ] |
| DeepSeek | `DEEPSEEK_API_KEY` | [ ] | [ ] | [ ] |

Para Groq también se debe elegir un `GROQ_MODEL` actualmente habilitado en la cuenta; el baseline obtuvo `404 model_not_found` con `llama-3.1-8b-instant`.

## Validación posterior

- [ ] La carga de `Settings()` termina sin exponer valores.
- [ ] El backend arranca y el health check del primario pasa.
- [ ] `/api/v1/estado` devuelve HTTP 200.
- [ ] La consulta Oracle usa el proveedor esperado o un fallback documentado.
- [ ] `git status --ignored --short .env .env.example` muestra `.env` ignorado y `.env.example` versionado.
- [ ] Un escaneo de secretos del diff y del árbol actual no encuentra credenciales.

## Historial remoto

Rotar/revocar las claves neutraliza los secretos históricos. Si el repositorio fue público o la política exige eliminar también los blobs, hará falta una operación separada con `git filter-repo`, coordinación con todos los clones y `force push` de ramas/tags. Esa reescritura es destructiva y no está autorizada por esta remediación local.

El tag local `pre-audit-2026-08` conserva el baseline comprometido y referencia un commit que contiene `.env`; no debe publicarse. Cualquier decisión de eliminarlo o recrearlo se toma después de la rotación.
