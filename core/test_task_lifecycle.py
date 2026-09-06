"""Pruebas permanentes de ROOT-011: lifecycle explícito de TaskContext."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.schemas import EventoEntrante
from core.action_ledger import ActionLedger, TurnEffectStatus, TurnUoWStatus
from core.behavior_core import BehaviorCore
from core.task_context import (
    TaskContext,
    TaskIntent,
    TaskTransitionStatus,
)
from core.test_action_ledger import _attach_ledger, _claim_action, _outcome_event
from terminal_client.test_chat import _orquestador_aislado


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _command(transaction_id: str, message: str, **details) -> EventoEntrante:
    payload = {"mensaje": message, "solicita_decision": False, **details}
    return EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles=payload,
    )


def _task_state(task: TaskContext) -> dict:
    return {
        "task_instance_id": task.task_instance_id,
        "objetivo_id": task.objetivo_id,
        "intentos_totales": task.intentos_totales,
        "intentos_fallidos": task.intentos_fallidos,
        "frustracion_local": task.frustracion_local,
        "umbral_abandono": task.umbral_abandono,
        "historial": list(task.historial),
        "max_historial": task.max_historial,
    }


def _correlated_outcome(behavior: BehaviorCore, *, success: bool = True) -> dict:
    task_id = behavior.task_context.task_instance_id
    return behavior.procesar_evento(
        "resultado_accion",
        {
            "accion": "navegar_a_objeto",
            "exito": success,
            "_task_correlation_enforced": True,
            "_task_instance_id_correlacion": task_id,
        },
    )


def test_01_conversation_without_task_stays_taskless() -> None:
    behavior = BehaviorCore()
    for message in ("hola", "como estas?", "gracias"):
        result = behavior.procesar_evento("comando_usuario", {"mensaje": message})
        assert_true(result["task_transition"]["status"] == "no_task", "Conversación no crea task.")
    assert_true(behavior.task_context is None, "La conversación social debe seguir sin TaskContext.")


def test_02_conversation_does_not_replace_active_task() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("ve al cubo")
    task = behavior.task_context
    task.registrar_intento("navegar_a_objeto", False)
    before = _task_state(task)
    for message in ("por cierto, cómo estás?", "gracias"):
        result = behavior.procesar_evento("comando_usuario", {"mensaje": message})
        assert_true(result["task_transition"]["status"] == "unchanged", "La charla sólo observa.")
    assert_true(_task_state(behavior.task_context) == before, "La task activa debe quedar intacta.")


def test_03_query_oracle_does_not_create_task() -> None:
    behavior = BehaviorCore()
    result = behavior.procesar_evento("comando_usuario", {"mensaje": "qué es la fotosíntesis?"})
    assert_true(behavior.task_context is None, "Una consulta no es una task.")
    assert_true(result["task_transition"]["intent"] == "none", "No se infiere lifecycle desde la pregunta.")


def test_04_explicit_task_start_creates_one_instance() -> None:
    behavior = BehaviorCore()
    result = behavior.procesar_evento(
        "comando_usuario",
        {"mensaje": "ve al cubo", "task_intent": "start", "task_objective": "ve al cubo"},
    )
    receipt = result["task_transition"]
    assert_true(receipt["status"] == "created", "START debe crear la task.")
    assert_true(receipt["task_instance_id"] == behavior.task_context.task_instance_id, "Receipt e instancia coinciden.")


def test_05_same_task_continuation_preserves_instance_and_state() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("ve al cubo rojo")
    task = behavior.task_context
    task.registrar_intento("navegar_a_objeto", False)
    before = _task_state(task)
    result = behavior.procesar_evento(
        "comando_usuario",
        {
            "mensaje": "ahora acércate más",
            "task_intent": "continue",
            "task_instance_id": task.task_instance_id,
        },
    )
    assert_true(result["task_transition"]["status"] == "continued", "CONTINUE debe ser explícito.")
    assert_true(_task_state(behavior.task_context) == before, "CONTINUE conserva todo el estado task-local.")


def test_06_explicit_replacement_creates_new_instance() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("objetivo X")
    old_id = behavior.task_context.task_instance_id
    receipt = behavior.aplicar_transicion_tarea(
        TaskIntent.REPLACE,
        objetivo_id="objetivo Y",
        expected_task_instance_id=old_id,
    )
    assert_true(receipt.status == TaskTransitionStatus.REPLACED, "REPLACE debe ser una transición real.")
    assert_true(receipt.previous_task_instance_id == old_id, "Debe conservar identidad anterior en receipt.")
    assert_true(behavior.task_context.task_instance_id != old_id, "Y necesita una instancia nueva.")


def test_07_replacement_resets_all_task_local_state() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("objetivo X")
    task = behavior.task_context
    task.registrar_intento("acción", False)
    task.frustracion_local = 67.0
    task.umbral_abandono = 47.0
    old_max = task.max_historial
    behavior.aplicar_transicion_tarea(TaskIntent.REPLACE, objetivo_id="objetivo Y")
    new = behavior.task_context
    assert_true(new.intentos_totales == new.intentos_fallidos == 0, "Replacement limpia contadores.")
    assert_true(new.frustracion_local == 0.0 and new.historial == [], "Replacement limpia frustración/historial.")
    assert_true(new.max_historial == old_max, "La configuración max_historial se conserva.")


def test_08_threshold_reset_uses_canonical_base() -> None:
    task = TaskContext("X")
    task.umbral_abandono = 41.0
    task.reset("Y")
    assert_true(
        task.umbral_abandono == TaskContext.DEFAULT_ABANDONMENT_THRESHOLD == 80.0,
        "Una task nueva no hereda el threshold contextual anterior.",
    )


def test_09_cancellation_preserves_original_objective_and_id() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("ve al cubo")
    old_id = behavior.task_context.task_instance_id
    result = behavior.procesar_evento(
        "comando_usuario",
        {"mensaje": "detente", "task_intent": "cancel", "task_instance_id": old_id},
    )
    receipt = result["task_transition"]
    assert_true(receipt["status"] == "cancelled", "CANCEL debe terminalizar X.")
    assert_true(receipt["objetivo_id"] == "ve al cubo" and receipt["task_instance_id"] == old_id, "Receipt conserva X.")


def test_10_cancellation_creates_no_new_task_and_text_is_not_authority() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("ve al cubo")
    behavior.procesar_evento("comando_usuario", {"mensaje": "detente"})
    assert_true(behavior.task_context.objetivo_id == "ve al cubo", "Texto solo no cancela ni reemplaza.")
    behavior.procesar_evento("comando_usuario", {"mensaje": "detente", "task_intent": "cancel"})
    assert_true(behavior.task_context is None, "Cancel estructurado libera sin crear 'detente'.")


def test_11_completion_is_explicit_and_idempotent() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("objetivo")
    task_id = behavior.task_context.task_instance_id
    first = behavior.procesar_evento(
        "tarea_completada",
        {"task_instance_id": task_id, "objetivo_completado": True},
    )
    personality_after_first = behavior.personality.obtener_rasgos()
    second = behavior.procesar_evento("tarea_completada", {"objetivo_completado": True})
    assert_true(first["task_transition"]["status"] == "completed", "Completion explícita cierra una vez.")
    assert_true(second["task_transition"]["status"] == "no_task", "Completion duplicada es idempotente.")
    assert_true(behavior.personality.obtener_rasgos() == personality_after_first, "Duplicate no reaplica reward de completion.")


def test_12_action_success_alone_does_not_complete_task() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("objetivo de varios pasos")
    task_id = behavior.task_context.task_instance_id
    _correlated_outcome(behavior, success=True)
    assert_true(behavior.task_context is not None, "Una acción exitosa no completa el objetivo.")
    assert_true(behavior.task_context.task_instance_id == task_id, "La misma task continúa ACTIVE.")


def test_13_attempt_only_from_valid_terminal_outcome() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    task_id = behavior.task_context.task_instance_id
    orchestrator.behavior = behavior
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    _claim_action(ledger, task_instance_id=task_id)
    orchestrator.procesar_evento(_outcome_event("task-attempt", "action-1"))
    assert_true(behavior.task_context.intentos_totales == 1, "Outcome terminal aceptado registra un intento.")
    ledger.close()


def test_14_duplicate_outcome_does_not_add_attempt() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    task_id = behavior.task_context.task_instance_id
    orchestrator.behavior = behavior
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    _claim_action(ledger, task_instance_id=task_id)
    orchestrator.procesar_evento(_outcome_event("task-first", "action-1"))
    orchestrator.procesar_evento(_outcome_event("task-duplicate", "action-1"))
    assert_true(behavior.task_context.intentos_totales == 1, "Duplicate no reaplica el consumer Task.")
    ledger.close()


def test_15_unknown_outcome_does_not_add_attempt() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    orchestrator.behavior = behavior
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    orchestrator.procesar_evento(_outcome_event("task-unknown", "missing-action"))
    assert_true(behavior.task_context.intentos_totales == 0, "Unknown outcome no llega a Task.")
    ledger.close()


def test_16_late_outcome_does_not_affect_replacement_task() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    task_x = behavior.task_context.task_instance_id
    orchestrator.behavior = behavior
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    _claim_action(ledger, task_instance_id=task_x)
    behavior.aplicar_transicion_tarea(TaskIntent.REPLACE, objetivo_id="Task Y")
    task_y = behavior.task_context.task_instance_id
    orchestrator.procesar_evento(_outcome_event("task-late", "action-1"))
    assert_true(behavior.task_context.task_instance_id == task_y, "Late outcome conserva Y.")
    assert_true(behavior.task_context.intentos_totales == 0, "Late outcome no registra intento en Y.")
    ledger.close()


def test_17_abandonment_signal_produces_real_transition() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    old_id = behavior.task_context.task_instance_id
    behavior.task_context.frustracion_local = 79.0
    result = _correlated_outcome(behavior, success=False)
    receipt = result["task_transition"]
    assert_true(receipt["status"] == "abandoned", "Threshold debe producir ABANDONED real.")
    assert_true(receipt["task_instance_id"] == old_id and behavior.task_context is None, "Receipt identifica y libera X.")


def test_18_abandonment_does_not_invent_execution_failure() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    behavior.task_context.frustracion_local = 79.0
    calls: list[str] = []
    original = behavior.state_engine.aplicar_evento

    def observed(event_type, *args, **kwargs):
        calls.append(event_type)
        return original(event_type, *args, **kwargs)

    behavior.state_engine.aplicar_evento = observed
    _correlated_outcome(behavior, success=False)
    assert_true(calls == ["exploracion_fallida"], "Abandonment no fabrica fracaso/outcome adicional.")


def test_19_detector_reads_do_not_mutate_lifecycle_and_run_once() -> None:
    class CountingTask(TaskContext):
        def __init__(self):
            super().__init__("Task X")
            self.calls = {"loop": 0, "stagnation": 0, "compulsion": 0}

        def detectar_loop(self, ventana=5):
            self.calls["loop"] += 1
            return False

        def detectar_estancamiento(self, umbral_fallos_consecutivos=4):
            self.calls["stagnation"] += 1
            return False

        def detectar_compulsion(self, ventana_tiempo=30.0, repeticiones=5):
            self.calls["compulsion"] += 1
            return False

    task = CountingTask()
    task_id = task.task_instance_id
    task.obtener_resumen()
    assert_true(task.calls == {"loop": 1, "stagnation": 1, "compulsion": 1}, "Cada detector se evalúa una vez.")
    assert_true(task.task_instance_id == task_id, "La lectura no cambia lifecycle.")


def test_20_illegal_transitions_fail_closed() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    before = _task_state(behavior.task_context)
    receipts = (
        behavior.aplicar_transicion_tarea(TaskIntent.START, objetivo_id="Task Y"),
        behavior.aplicar_transicion_tarea(TaskIntent.CONTINUE, expected_task_instance_id="other"),
        behavior.aplicar_transicion_tarea(TaskIntent.REPLACE, objetivo_id="Task X"),
        behavior.aplicar_transicion_tarea("not-a-task-intent"),
    )
    assert_true(all(item.status == TaskTransitionStatus.INVALID_TRANSITION for item in receipts), "Todo ilegal debe ser explícito.")
    assert_true(_task_state(behavior.task_context) == before, "Transición ilegal no muta ningún campo.")
    empty = BehaviorCore().aplicar_transicion_tarea(TaskIntent.CONTINUE)
    assert_true(empty.status == TaskTransitionStatus.INVALID_TRANSITION, "CONTINUE sin task no crea una instancia.")


def test_21_task_transition_integrates_with_root007_uow() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    orchestrator.behavior = BehaviorCore()
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    event = _command(
        "task-uow",
        "ve al cubo",
        task_intent="start",
        task_objective="ve al cubo",
    )
    orchestrator.procesar_evento(event)
    effects = {effect.effect_key: effect for effect in ledger.get_turn_effects("task-uow")}
    assert_true(ledger.get_turn_uow_status("task-uow") == TurnUoWStatus.COMMITTED, "Task event comparte el UoW.")
    assert_true(effects["event:behavior"].status == TurnEffectStatus.APPLIED, "Task transition vive en Behavior receipt.")
    assert_true(orchestrator.behavior.task_context.objetivo_id == "ve al cubo", "La transición funcional fue aplicada.")
    ledger.close()


def test_22_action_task_binding_requires_explicit_lifecycle_intent() -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    orchestrator.behavior = BehaviorCore()
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    first = _command(
        "task-bind-start",
        "ve al cubo",
        task_intent="start",
        task_objective="ve al cubo",
        solicita_decision=True,
    )
    orchestrator.procesar_evento(first)
    created = ledger.get_action_for_transaction("task-bind-start")
    task_id = orchestrator.behavior.task_context.task_instance_id
    assert_true(created.task_instance_id == task_id, "START vincula la acción a la instancia creada.")
    orchestrator.procesar_evento(_command("task-bind-one-shot", "gira 90 grados", solicita_decision=True))
    one_shot = ledger.get_action_for_transaction("task-bind-one-shot")
    assert_true(one_shot.task_instance_id is None, "Una microacción sin CONTINUE no hereda la task activa.")
    ledger.close()


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-011: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
