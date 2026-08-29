# Baseline pre-Capa 2 — 2026-08-29

Estado: **Fase 0 completada; hallazgos registrados, sin fixes aplicados.**

Este baseline captura el estado reproducible de LAZZARO antes de la auditoría y la estabilización. La evidencia cruda está en [`evidence/`](evidence/).

## Punto de retorno

- Rama local: `codex/stabilization/pre-capa2`
- Commit de partida: `c2ff114ab655c196a519ef9c5d436035f3bbcf0b`
- Tag local: `pre-audit-2026-08`
- El tag apunta al commit de partida, antes de agregar este reporte.
- No se hizo `push`.

Al iniciar ya existían cambios locales sin commit en:

- `lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin`
- `lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin`
- `lazzaro_data/chroma.sqlite3`

Se preservaron fuera del commit. Los smokes reales abrieron la Chroma activa y una consulta Oracle agregó una entrada a `oracle_cache`, por lo que esos archivos siguen siendo estado de runtime, no parte de la evidencia versionada.

## Entorno real

- Python: `3.11.9`
- Pip: `24.0`
- Proveedor primario: Gemini, modelo `gemini-3.1-flash-lite`
- Fallbacks configurados: Groq, xAI, Kimi y DeepSeek API
- Chroma: `./lazzaro_data`
- `pip freeze` completo: [`evidence/pip-freeze.txt`](evidence/pip-freeze.txt)
- Configuración activa redactada: [`evidence/config-active-redacted.txt`](evidence/config-active-redacted.txt)
- Variables de `.env`: solo nombres y estado `set/empty`, sin valores, en [`evidence/env-keys-redacted.txt`](evidence/env-keys-redacted.txt)

`requirements.txt` declara paquetes sin versiones. El `pip freeze` es, por ahora, la fotografía reproducible del entorno realmente instalado.

## Resultados

| Control | Resultado | Evidencia |
|---|---:|---|
| `compileall` | PASS | [`compileall.txt`](evidence/compileall.txt) |
| `unittest discover` | 0 pruebas, exit 0 | [`unittest-discover.txt`](evidence/unittest-discover.txt) |
| Runner directo de baseline | 109 PASS, 1 FAIL | [`baseline-test-run.txt`](evidence/baseline-test-run.txt) |
| Arranque + `/estado` + apagado limpio | PASS | [`backend-smoke-graceful.txt`](evidence/backend-smoke-graceful.txt) |
| Memoria temporal: abrir/escribir/buscar/reabrir/eliminar/cerrar | PASS | [`memory-smoke.txt`](evidence/memory-smoke.txt) |
| Terminal social | PASS, acción `hablar` | [`e2e-smoke-sanitized.txt`](evidence/e2e-smoke-sanitized.txt) |
| Terminal Oracle | PASS mediante fallback, acción `hablar` | [`e2e-smoke-sanitized.txt`](evidence/e2e-smoke-sanitized.txt) |
| Unity controlado | PASS de contrato, acción `navegar_a_objeto` a `Cube` | [`e2e-smoke-sanitized.txt`](evidence/e2e-smoke-sanitized.txt) |

El smoke de Unity validó el contrato backend y la acción devuelta. No se abrió el editor/runtime de Unity ni se materializó movimiento físico.

## Hallazgos iniciales — no corregidos

### BASE-FIND-001 — P0 confirmado — credenciales en archivo versionado

`IMPLEMENTADO/OBSERVADO`: `.env` está trackeado por Git aunque `.gitignore` lo excluye, contiene campos de credenciales configurados y aparece en el snapshot local de `origin/main`. No se copiaron sus valores a la evidencia. Requiere retirar el archivo del tracking, sustituirlo por un ejemplo seguro y rotar las credenciales que hayan salido del equipo. No se debe reescribir historia ni revocar claves sin autorización explícita. El tag `pre-audit-2026-08` apunta al commit que contiene ese archivo y no debe publicarse ni empujarse antes de resolver el riesgo.

### MEM-AUDIT-001 — P2 provisional — fallo en flujo con metadatos vacíos

`OBSERVADO`: `memory.test_chromadb_flow::test_eliminacion` falla porque la inserción previa con metadatos `{}` devuelve `False` tras un `ValueError`; el conteo queda en cero y la eliminación no llega a validarse. El smoke con metadatos no vacíos sí pasó completo. Antes de corregir hay que aislar si el contrato válido permite `{}` o si la prueba quedó desactualizada.

### LLM-AUDIT-001 — P2 provisional — fallback Groq no utilizable

`OBSERVADO`: la consulta Oracle recibió `404 model_not_found` para `llama-3.1-8b-instant`. El Orchestrator degradó correctamente a Gemini y produjo respuesta pública, pero Groq no funciona actualmente como respaldo real.

### TEST-AUDIT-001 — P2 provisional — no existe runner estándar operativo

`IMPLEMENTADO/OBSERVADO`: `pytest` no está instalado ni declarado y `unittest discover` encuentra cero pruebas. Para fotografiar el baseline se invocaron directamente las funciones `test_*` y los `main()` de módulos de prueba. Antes del gate conviene tener un único comando reproducible de suite.

## Criterio de salida de Fase 0

- [x] Existe un commit/tag al que se puede volver.
- [x] Se conoce qué pruebas pasan y cuál falla antes del saneamiento.
- [x] Se capturó el entorno real instalado; `requirements.txt` ya no es la única referencia.
- [x] Los fallos existentes están registrados sin intentar corregirlos.

Siguiente paso obligatorio: resolver `BASE-FIND-001` antes de publicar la rama o continuar con trabajo ordinario. Después se inicia OBS-001/OBS-002 para definir `AuditTrace v1` y un writer JSONL tolerante a fallos, manteniendo el logger humano existente.
