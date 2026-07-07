# Estado actual del proyecto - Escaneo integral

Fecha del escaneo: 2026-07-06
Workspace: `C:\Users\JAIRO\Entidades\Lazzaro`

## Veredicto ejecutivo

El backend Python y la integracion de Capa 1 quedan en estado OK desde Codex:
compilan, las pruebas existentes pasan, las dependencias declaradas no presentan
conflictos y el smoke import principal carga correctamente FastAPI, Motor,
Resolver fuzzy y Memory.

La parte Unity queda validada por contrato y revision estatica dentro de este
workspace. No se encontro un `.sln`, `.csproj`, `.asmdef` ni `ProjectSettings.asset`
en esta carpeta, por lo que la compilacion real de C# debe confirmarse desde el
proyecto Unity completo y con prueba de campo.

## Inventario revisado

- Archivos Python detectados: 95.
- Pruebas detectadas: 16 archivos `ai_engine/test_*.py`.
- Cliente Unity presente en `unity_client/`:
  - `ApiClient.cs`
  - `DTOs.cs`
  - `InteractableMetadata.cs`
  - `LazzaroAgent.cs`
  - `MovementController.cs`
  - `PerceptionSystem.cs`

## Verificaciones ejecutadas

| Verificacion | Resultado |
| --- | --- |
| `python -m compileall ai_engine core memory api tools telescopio config main.py train_triage_model.py teses.py` | OK |
| Loop completo sobre 16 pruebas `ai_engine/test_*.py` | OK, `ALL TESTS PASSED: 16` |
| `python -m pip check` | OK, `No broken requirements found.` |
| Smoke import de `main.app`, `MotorEngine`, `FuzzyObjectResolver`, `MemoryManager` | OK, `SMOKE_OK routes= 9 motor_actions= 8` |
| `git diff --check` antes del reporte | OK |
| `git status --short` antes del reporte | Limpio |

Pruebas ejecutadas y aprobadas:

- `ai_engine/test_affordance_resolver.py`
- `ai_engine/test_context_biased_fuzzing.py`
- `ai_engine/test_conversation_memory.py`
- `ai_engine/test_fuzzy_object_resolver.py`
- `ai_engine/test_hallucination_correction.py`
- `ai_engine/test_learned_alias_candidates.py`
- `ai_engine/test_llm_fallbacks.py`
- `ai_engine/test_memory_decay.py`
- `ai_engine/test_motor_fuzzy_integration.py`
- `ai_engine/test_motor_guardrails.py`
- `ai_engine/test_oracle.py`
- `ai_engine/test_perception_models.py`
- `ai_engine/test_persona.py`
- `ai_engine/test_spatial_affective_memory.py`
- `ai_engine/test_subjective_effort.py`
- `ai_engine/test_triage_routing.py`

## Integracion de Capa 1

### Contrato y documentacion

`docs/cognitive_contracts.md` contiene las reglas esperadas para:

- compatibilidad legacy con `nombre_objeto`;
- normalizacion a `PerceivedObject` y `PerceptionSnapshot`;
- estados `ready`, `requires_clarification`, `not_found`, `not_allowed` y `unsafe`;
- metadata obligatoria `metadata.motor_resolution` y `metadata.motor_audit`;
- salida tecnica para aclaraciones;
- lista cerrada de affordances;
- uso autorizado de `thefuzz`;
- transporte de `subjective_effort`;
- candidatos de alias aprendido;
- memoria afectiva espacial.

### Percepcion y compatibilidad Unity

La entrada perceptiva queda normalizada en `ai_engine/perception_models.py`.
El adaptador acepta formatos nuevos y legacy:

- `objects`
- `objetos`
- `objetos_normalizados`
- `perceived_objects`
- texto legacy

Unity mantiene compatibilidad con `nombre_objeto` como parametro saliente, pero
el backend resuelve internamente hacia identificadores de objeto cuando existe
snapshot perceptivo.

`unity_client/PerceptionSystem.cs` ya emite:

- `snapshot_id`
- `objetos_normalizados`
- `unknown_affordances`
- `aliases`
- `affordances`
- `id`
- `display_name`
- `confidence`

### Resolver fuzzy y auditoria

`ai_engine/fuzzy_object_resolver.py` usa `thefuzz` y entrega una resolucion con
auditoria. La revision encontro metadata de:

- candidatos evaluados;
- scores de nombre, alias, affordance y contexto;
- penalizaciones de riesgo;
- candidatos rechazados;
- affordances desconocidas;
- aliases aprendidos usados;
- memoria afectiva consultada.

`ai_engine/motor_engine.py` conserva compatibilidad con `nombre_objeto`, pero
adjunta `metadata.motor_resolution` y `metadata.motor_audit` cuando una decision
pasa por `MotorResolution`.

### Affordances

La ontologia cerrada esta definida en `ai_engine/perception_models.py` mediante
`ALLOWED_AFFORDANCES`. La validacion de accion vive en
`ai_engine/affordance_resolver.py`.

El flujo actual:

- conserva affordances canonicas;
- marca tags libres como `unknown_affordances`;
- bloquea acciones con `not_allowed` cuando falta la affordance requerida;
- no permite que un tag libre participe en scoring.

Unity tambien filtra affordances canonicas desde `PerceptionSystem.cs`, con
registro de valores rechazados en `unknown_affordances`.

### Aclaraciones y correccion de alucinaciones

El Motor devuelve estados tecnicos cuando no hay seguridad:

- `requires_clarification` para ambiguedad;
- `not_found` para objeto inexistente;
- `not_allowed` para affordance faltante;
- `unsafe` para bloqueo de seguridad.

`CandidateCorrection` queda documentado y conectado para sugerir candidatos
plausibles sin ejecutar fisicamente una correccion no confirmada.

### Alias aprendido

El Core conecta el proveedor de aliases aprendidos al resolver fuzzy. La
persistencia queda en `memory/memory_manager.py` con `tipo_evento="alias_aprendido"`.

El registro se aprueba cuando hay evidencia de accion exitosa y se evita duplicar
aliases ya conocidos para el mismo objeto.

### Esfuerzo subjetivo

`SubjectiveEffortCalculator` esta conectado en `core/orchestrator.py`.
El resultado viaja en:

- `ActionResponse.metadata.subjective_effort`;
- detalles enriquecidos de memoria de trabajo cuando corresponde.

La prueba dedicada confirma que es estable y no invasivo.

### Sesgo contextual y memoria afectiva espacial

El resolver incluye sesgo contextual y memoria afectiva como senales acotadas:

- `context_bonus` esta limitado y auditado;
- la memoria afectiva puede influir en scoring, pero no otorga autonomia fisica;
- la persistencia usa `tipo_evento="memoria_afectiva_espacial"`;
- el proveedor se conecta desde `core/orchestrator.py` hacia el resolver fuzzy.

## Integracion Core, API y estado

`core/orchestrator.py` preserva `id_accion`, metadata motora y eventos
`resultado_accion`. Tambien maneja aprobacion de alias pendiente por `id_accion`
o por detalles del resultado cuando aplica.

`api/schemas.py` mantiene el contrato externo con:

- `id_accion`;
- `accion`;
- `parametros`;
- `nombre_objeto` dentro de parametros;
- metadata interna opcional.

`core/state_engine.py` y `core/behavior_core.py` siguen reconociendo
`resultado_accion` como evento interno de estado/comportamiento.

## Observaciones no bloqueantes

- `core/logger.py` contiene un `pass` esperado para el caso en que no se puede
  escribir log; no bloquea ejecucion.
- `ai_engine/subjective_effort.py` contiene un `pass` dentro de manejo tolerante
  de excepciones; no bloquea ejecucion.
- `ai_engine/triage_router.py` contiene texto con `TODO` dentro de una nota de
  comportamiento, no una tarea tecnica abierta.
- `ai_engine/test_llm_fallbacks.py` puede mostrar warnings esperados cuando no
  hay API keys externas; la prueba aun pasa.
- `ai_engine/test_persona.py` puede imprimir logs de fallback/reparacion; son
  parte de los casos cubiertos.

## Pendientes para prueba de campo

- Confirmar en Unity que `PerceptionSystem.cs` esta asignado en escena y que los
  objetos relevantes tienen `InteractableMetadata` cuando sea necesario.
- Confirmar que Unity envia `objetos_normalizados` y `unknown_affordances` en el
  evento perceptivo real.
- Confirmar que las acciones fisicas devuelven `resultado_accion` con
  `id_accion`, `accion`, `exito` y `nombre_objeto`.
- Confirmar que `hablar` no emite `resultado_accion`, para evitar loops.
- Confirmar que un caso ambiguo en campo produce aclaracion y no movimiento.
- Confirmar que un objeto sin affordance requerida produce bloqueo tecnico y no
  ejecucion fisica.
- Confirmar disponibilidad real de proveedores LLM segun `.env` y llaves locales.

## Estado final

Desde el lado Python/backend, el proyecto queda integrado correctamente para el
rework actual de Capa 1. No se detectaron fallos de compilacion, pruebas,
dependencias, contrato fuzzy, metadata motora, affordances, alias aprendido,
esfuerzo subjetivo ni memoria afectiva espacial.

El unico punto que no puede cerrarse desde Codex en este workspace es la
compilacion/runtime completo de Unity, porque aqui solo esta el cliente C# suelto
y no el proyecto Unity compilable. Esa validacion queda correctamente delegada a
la prueba de campo.
