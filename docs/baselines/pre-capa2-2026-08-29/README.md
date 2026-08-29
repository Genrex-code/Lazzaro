# Baseline pre-Capa 2 — 2026-08-29

Estado: **Fase 0 y OBS-001/OBS-002 completados; listo para OBS-003.**

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
- Proveedor único: Gemini, modelo `gemini-3.1-flash-lite`
- Fallbacks LLM remotos: deshabilitados
- Oracle externo: no configurado; la ruta Oracle usa el cliente Gemini activo
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

## Gate final de Fase 0

| Control | Resultado |
|---|---:|
| Compilación de `main.py`, `api`, `ai_engine`, `config`, `core`, `memory`, `terminal_client` y `tools` | PASS |
| Carga de `Settings()` con perfil solo Gemini | PASS |
| Retiro local de credenciales de proveedores deshabilitados | PASS |
| Arranque + health check Gemini + `/api/v1/estado` + apagado limpio | PASS |
| Ruta Oracle real mediante Gemini, HTTP 200 y acción `hablar` | PASS |
| Pruebas dirigidas de fallbacks y Oracle | PASS |
| `.env` ignorado, `.env.example` versionado y escaneo del árbol actual | PASS |

Resumen reproducible y sanitizado: [`evidence/phase0-final-gate.txt`](evidence/phase0-final-gate.txt).

## Estado de los hallazgos iniciales

### BASE-FIND-001 — P0 remediado — credenciales en archivo versionado

`REMEDIADO`: `.env` estaba trackeado por Git aunque `.gitignore` lo excluía y aparece en el snapshot local de `origin/main`. Se retiró del índice sin borrar la copia local, se endureció `.gitignore` y se agregó `.env.example`. Gemini fue rotado y la clave anterior revocada; Groq, xAI, Moonshot/Kimi y DeepSeek fueron retirados localmente y sus claves antiguas quedaron eliminadas, ausentes o revocadas según la verificación externa registrada en el [checklist de credenciales](credential-rotation-checklist.md). El árbol Git actual no contiene la credencial Gemini activa.

El historial conserva valores antiguos ya neutralizados. Reescribirlo requeriría una operación destructiva coordinada y no está autorizado. Por ello no deben publicarse el tag local `pre-audit-2026-08` ni commits históricos sin decidir antes una política explícita de saneamiento remoto.

### MEM-AUDIT-001 — P2 provisional — fallo en flujo con metadatos vacíos

`OBSERVADO`: `memory.test_chromadb_flow::test_eliminacion` falla porque la inserción previa con metadatos `{}` devuelve `False` tras un `ValueError`; el conteo queda en cero y la eliminación no llega a validarse. El smoke con metadatos no vacíos sí pasó completo. Antes de corregir hay que aislar si el contrato válido permite `{}` o si la prueba quedó desactualizada.

### LLM-AUDIT-001 — retirado — fallback Groq no utilizable

`RETIRADO POR DECISIÓN OPERATIVA`: el baseline recibió `404 model_not_found` para `llama-3.1-8b-instant`. LAZZARO ahora usa exclusivamente Gemini y los fallbacks remotos están deshabilitados, por lo que Groq dejó de formar parte del contrato operativo.

### TEST-AUDIT-001 — P2 provisional — no existe runner estándar operativo

`IMPLEMENTADO/OBSERVADO`: `pytest` no está instalado ni declarado y `unittest discover` encuentra cero pruebas. Para fotografiar el baseline se invocaron directamente las funciones `test_*` y los `main()` de módulos de prueba. Antes del gate conviene tener un único comando reproducible de suite.

## Criterio de salida de Fase 0

- [x] Existe un commit/tag al que se puede volver.
- [x] Se conoce qué pruebas pasan y cuál falla antes del saneamiento.
- [x] Se capturó el entorno real instalado; `requirements.txt` ya no es la única referencia.
- [x] Los fallos existentes están registrados sin intentar corregirlos.

La rotación y revocación externas de `BASE-FIND-001` quedaron completadas y el gate posterior pasó. OBS-001 definió el contrato y la [serialización segura de AuditTrace v1](../../observability/audit_trace_v1.md); OBS-002 agregó el [writer JSONL thread-safe y tolerante a fallos](../../observability/audit_trace_writer.md), sin integrar todavía `Orchestrator`. Siguiente paso: OBS-003, correlacionar `event_start`/`event_end` manteniendo el logger humano existente.
