# ROOT-031 — inicialización de logging fail-soft

ROOT-031 · P2 — Logging puede impedir import/startup. Dueño: `core.logger`.
Absorbe BOOT-AUDIT-008 y se relaciona con ROOT-010/022. Invariante del
backlog: **“Fallar logfile degrada logging, no runtime.”** No tiene
dependencias; habilita ROOT-022, que a su vez precede ROOT-023.

## Reproducción previa

En una carga aislada de `core/logger.py`, la ruta normal instalaba un
`FileHandler` y un `StreamHandler(stdout)`. Al inyectar `PermissionError`
en `Path.mkdir` o `logging.FileHandler`, o `OSError` en el segundo,
la excepción salía de la configuración a nivel de módulo: no se completaba
el import ni podía consumirse `get_logger`. El reproducer usó mocks y
archivos temporales; no dependió de permisos reales.

## Contrato implementado

- Se conserva el path actual `<repo>/logs/lazzaro.log`, el nivel INFO,
  el formato, el stream stdout y el respeto a handlers root preexistentes
  cuando el archivo funciona. No hay rotación.
- `OSError` (incluido `PermissionError`) en creación de directorio,
  apertura/configuración/cierre de handler de archivo degrada a un único
  `StreamHandler(sys.stderr)`. La advertencia de bootstrap es constante,
  no contiene ruta, excepción ni datos de usuario, y se escribe directamente
  en stderr a lo sumo una vez por carga del módulo. No llama a
  `get_logger`, AuditTrace, Memory, Persona ni API.
- El lock local serializa la inicialización; el guard sobre handlers root
  preserva la semántica histórica de `basicConfig`. Un handler parcialmente
  adjunto se retira y cierra. Si otro configurador gana la carrera del root
  global, se cierran los handlers construidos pero no adjuntos.
- Errores de programación ajenos a I/O no se silencian. `SystemExit` y
  `KeyboardInterrupt` se propagan. Si incluso el mecanismo stderr falla,
  se omite el diagnóstico y se devuelve el logger sin recursión; no se
  promete que logging sea infalible.

La importación de un consumidor que obtiene logger ya no falla **por
errores esperados de configuración del logfile**. La degradación no define
readiness: ROOT-023 decide esa semántica. ROOT-022 sigue siendo dueño del
rollback/cierre de recursos de startup y puede asumir que este fallo de
logfile no le impide ejecutarse. ROOT-032 conserva la responsabilidad de
configuración y paths canónicos; este cambio no altera la selección de path.
No se aborda logging multiproceso/fork ni se crea un lifecycle de handlers.
ROOT-010 y AuditTrace v1 no cambian.

## Pruebas

`core/test_logger_failsoft.py` cubre ruta normal, errores de directorio y
archivo, stderr utilizable, advertencia sin datos sensibles y sin recursión,
idempotencia por nombre y entre nombres, concurrencia, handler parcial/cierre
fallido, import de consumidor, fallo del propio fallback y propagación de
`SystemExit`/`KeyboardInterrupt`. El fixture restaura handlers y nivel
globales y usa `tmp_path`; no requiere red, Chroma ni base de datos.
