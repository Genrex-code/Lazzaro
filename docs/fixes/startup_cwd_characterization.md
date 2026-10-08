# Caracterizacion del fallo de arranque por directorio de trabajo

Fecha: 2026-10-08. Sin cambios de comportamiento en produccion.

## Correccion posterior: solo resolucion del .env

Se corrigio `Settings.model_config.env_file` para resolver `.env` desde la raiz
del proyecto mediante `Path(__file__).resolve().parents[1]`. El diagnostico que
sigue documenta el estado anterior al fix.

La prueba de solo lectura con el .env existente confirma que `Settings()` carga
la misma configuracion desde Entidades y desde Lazzaro, con clave Gemini presente
en ambos casos. No se hicieron peticiones remotas ni se arranco Chroma.

Las pruebas de caracterizacion se actualizaron a regresion: **7 passed**.
Cubren ambos directorios, seleccion de Gemini con clientes simulados, rechazo de
un .env ajeno en el cwd, prioridad de variables del proceso y compatibilidad con
`_env_file=otra_ruta` y `_env_file=None`. `git diff --check` paso.

## Correccion posterior: rutas de datos

Por solicitud del usuario, las rutas relativas de `CHROMA_DB_PATH`,
`ACTION_LEDGER_PATH` y `MEMORY_WRITE_LEDGER_PATH` ahora se resuelven desde
`Entidades/Lazzaro`. Con el perfil actual, Chroma apunta a
`Entidades/Lazzaro/lazzaro_data` desde ambos directorios de lanzamiento.
El cache Oracle recibe esta misma ruta desde el cliente de memoria.

Se conservan los overrides absolutos y `:memory:` para los ledgers.
No se trasladaron, fusionaron ni borraron datos. Verificacion de configuracion
sin abrir las bases, **12 pruebas passed** y `git diff --check` sin errores.

## Causa observada

El comando de la captura ejecuta `Lazzaro/main.py` con el directorio de trabajo
`C:\Users\JAIRO\Entidades`. Ejecutar un script por ruta absoluta no cambia ese
directorio. `Settings.model_config` declara `env_file=".env"`, por lo que busca
`Entidades/.env`, no `Entidades/Lazzaro/.env`.

Un probe de solo lectura con el interprete Python 3.11 de la captura confirmo:

| Directorio de trabajo | .env encontrado | Clave Gemini presente | Orden de fallback |
| --- | --- | --- | --- |
| Entidades | No | No | Defaults: gemini, groq, xai, kimi, deepseek_api |
| Entidades/Lazzaro | Si | Si | gemini |

No se imprimieron credenciales ni se hicieron peticiones a proveedores.

## Punto de fallo

`main.lifespan` carga Settings, inicializa BehaviorCore y memoria, y llama a
`build_ai_client(settings)`. La fabrica omite los cinco proveedores remotos por
falta de claves y lanza `ValueError: No hay proveedores LLM configurados`.
El fallo ocurre antes del health check y de la construccion de ReasoningEngine
y Orchestrator. No es evidencia de credenciales rechazadas, cuota ni fallo de red.

Tambien `CHROMA_DB_PATH=./lazzaro_data` se resuelve contra el directorio de trabajo:
la captura inicializa `Entidades/lazzaro_data`, mientras que el lanzamiento desde
el proyecto apunta a `Entidades/Lazzaro/lazzaro_data`. Los paths por defecto de
los ledgers tambien son relativos. No se movieron ni modificaron esos datos.

## Pruebas

`config/test_startup_configuration_characterization.py` contiene tres pruebas
con .env temporal y credencial sintetica: carga desde el padre, carga desde el
proyecto y excepcion real de la fabrica sin construir ningun cliente.

Validacion: `python -m pytest config/test_startup_configuration_characterization.py -q`
con el interprete de la captura: **3 passed**.

Estas pruebas congelan el comportamiento defectuoso actual para esta fase.
Al corregir la carga del .env, las expectativas correspondientes deben cambiar
al contrato deseado. La politica de rutas de datos requiere una decision separada
para evitar usar accidentalmente una base diferente. Tener la clave presente no
demuestra que el proveedor remoto ni el resto del arranque funcionen.
