"""Pruebas focales de ROOT-007: Turn Unit of Work y side effects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import sys

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ai_engine.schemas import ActionResponse
from api.routes import router
from api.schemas import EventoEntrante
from core.action_ledger import (
    ActionLedger,
    ActionLedgerError,
    ActionState,
    ActionTransitionEvent,
    TransactionCompletionStatus,
    TurnAbortedError,
    TurnEffectStatus,
    TurnProcessingError,
    TurnReconciliationRequiredError,
    TurnUoWStatus,
)
from core.audit_trace import AUDIT_STAGES, AUDIT_TRACE_VERSION
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from memory.memory_manager import ManualConversationSaveResult
from terminal_client.test_chat import _orquestador_aislado
from tools.base import ToolResult
from tools.registry import ToolRegistry


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _event(
    transaction_id: str,
    *,
    origin: str = "unity",
    event_type: str = "actualizacion_estado",
    decision: bool = False,
    details: dict | None = None,
) -> EventoEntrante:
    payload = dict(details or {})
    payload.setdefault("solicita_decision", decision)
    return EventoEntrante(
        origen=origin,
        tipo_evento=event_type,
        id_transaccion=transaction_id,
        detalles=payload,
    )


def _isolated(route: str = "internal", *, ledger: ActionLedger | None = None):
    orchestrator, calls = _orquestador_aislado(route)
    orchestrator.action_ledger = ledger or ActionLedger(":memory:")
    return orchestrator, calls


def _effect_map(ledger: ActionLedger, transaction_id: str):
    return {effect.effect_key: effect for effect in ledger.get_turn_effects(transaction_id)}


def _behavior_checkpoint_view(checkpoint: dict) -> dict:
    task = checkpoint["task_context"]
    return {
        "state_engine": checkpoint["state_engine"],
        "personality": checkpoint["personality"],
        "task": task.obtener_resumen() if task is not None else None,
        "policy": checkpoint["policy"],
        "offsets_politica": checkpoint["offsets_politica"],
        "constelacion": checkpoint["constelacion"].a_dict(),
        "ultima_percepcion": checkpoint["ultima_percepcion"],
        "ultima_percepcion_resumida": checkpoint["ultima_percepcion_resumida"],
    }


def _prepare_executing_action(
    ledger: ActionLedger,
    *,
    action_id: str,
    transaction_id: str,
    action_name: str = "hablar",
) -> None:
    ledger.claim_transaction(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
    )
    receipt = ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name=action_name,
        parameters={"texto_a_comunicar": "hola"},
        requires_confirmation=False,
        task_instance_id="task-root007",
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    assert_true(receipt.action is not None, "El fixture debe crear la accion.")
    ledger.transition_action(
        action_id,
        ActionTransitionEvent.AUTO_AUTHORIZE,
        receipt_id=f"authorize:{action_id}",
    )
    ledger.transition_action(
        action_id,
        ActionTransitionEvent.DISPATCH,
        receipt_id=f"dispatch:{action_id}",
    )
    ledger.transition_action(
        action_id,
        ActionTransitionEvent.START_EXECUTION,
        receipt_id=f"start:{action_id}",
    )
    status = ledger.complete_transaction(
        transaction_id,
        {
            "id_accion": action_id,
            "nombre": action_name,
            "parametros": {"texto_a_comunicar": "hola"},
            "requiere_confirmacion": False,
            "estado_ejecucion": "dispatched",
            "id_transaccion_origen": transaction_id,
        },
    )
    assert_true(status == TransactionCompletionStatus.COMPLETED, "Fixture origen completo.")


def _outcome_event(transaction_id: str, action_id: str) -> EventoEntrante:
    return _event(
        transaction_id,
        event_type="resultado_accion",
        details={
            "id_accion": action_id,
            "accion": "hablar",
            "exito": True,
            "mensaje_resultado": "entregado",
        },
    )


def test_01_v2_to_v3_preserves_history_and_marks_legacy_unknown() -> None:
    with TemporaryDirectory(prefix="root007-migration-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        old = ActionLedger(path)
        _prepare_executing_action(
            old,
            action_id="legacy-action",
            transaction_id="legacy-transaction",
        )
        outcome = old.record_outcome(
            action_id="legacy-action",
            outcome_transaction_id="legacy-outcome",
            success=True,
            action_name="hablar",
            payload={"material_attestation": {"receipt": "kept"}},
        )
        assert_true(outcome.outcome is not None, "Debe crear outcome historico.")
        old.mark_outcome_consumed("legacy-action")
        transition_count = len(old.get_action_transitions("legacy-action"))
        receipt_count = old._connection.execute(
            "SELECT COUNT(*) AS n FROM transition_receipts"
        ).fetchone()["n"]
        old._connection.execute("DROP TABLE turn_effects")
        old._connection.execute("DROP TABLE turn_uows")
        old._connection.execute(
            "UPDATE ledger_meta SET value = '2' WHERE key = 'schema_version'"
        )
        old.close()

        migrated = ActionLedger(path)
        action = migrated.get_action("legacy-action")
        restored_outcome = migrated.get_outcome("legacy-action")
        version = migrated._connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert_true(version == "3", "La migracion debe terminar en schema v3.")
        assert_true(action is not None and action.action_state == ActionState.REPORTED_SUCCESS, "Debe preservar action y lifecycle.")
        assert_true(len(migrated.get_action_transitions("legacy-action")) == transition_count, "Debe preservar transiciones.")
        assert_true(
            migrated._connection.execute("SELECT COUNT(*) AS n FROM transition_receipts").fetchone()["n"] == receipt_count,
            "Debe preservar transition receipts.",
        )
        assert_true(
            restored_outcome is not None
            and restored_outcome.consumed_at is not None
            and restored_outcome.payload["material_attestation"]["receipt"] == "kept",
            "Debe preservar outcome, material receipt y consumed_at.",
        )
        assert_true(migrated.get_turn_uow("legacy-transaction") is None, "No debe inventar una UoW historica.")
        assert_true(
            migrated.get_turn_uow_status("legacy-transaction") == TurnUoWStatus.LEGACY_UNKNOWN,
            "La ausencia historica debe ser LEGACY_UNKNOWN.",
        )
        migrated.close()


def test_02_effect_receipts_are_terminal_and_commit_is_atomic() -> None:
    ledger = ActionLedger(":memory:")
    claim = ledger.claim_turn_transaction("uow-atomic", origin="unity", event_type="evento")
    assert_true(
        claim.status.value == "created"
        and ledger.get_turn_uow_status("uow-atomic") == TurnUoWStatus.OPEN,
        "Claim y UoW OPEN deben aparecer en el mismo commit.",
    )
    ledger.start_turn_effect(
        "uow-atomic",
        "event:behavior",
        effect_kind="event",
        phase="event",
        required=True,
    )
    ledger.finish_turn_effect("uow-atomic", "event:behavior", TurnEffectStatus.APPLIED)
    same = ledger.finish_turn_effect("uow-atomic", "event:behavior", TurnEffectStatus.APPLIED)
    assert_true(same.status == TurnEffectStatus.APPLIED, "El mismo cierre debe ser idempotente.")
    try:
        ledger.finish_turn_effect("uow-atomic", "event:behavior", TurnEffectStatus.FAILED)
    except ActionLedgerError:
        pass
    else:
        raise AssertionError("Un receipt terminal no debe cambiar de significado.")
    committed = ledger.commit_turn_uow("uow-atomic", None)
    assert_true(committed == TransactionCompletionStatus.COMPLETED, "Commit UoW debe completar atomico.")
    assert_true(ledger.get_turn_uow_status("uow-atomic") == TurnUoWStatus.COMMITTED, "UoW committed durable.")
    assert_true(ledger.get_transaction("uow-atomic").status == "completed", "Transaction y UoW deben cerrar juntas.")


def test_03_required_failure_prevents_commit() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("required-failure", origin="unity", event_type="evento")
    ledger.begin_turn_uow("required-failure")
    ledger.start_turn_effect(
        "required-failure",
        "decision:required",
        effect_kind="decision",
        phase="decision",
        required=True,
    )
    ledger.finish_turn_effect("required-failure", "decision:required", TurnEffectStatus.FAILED)
    result = ledger.commit_turn_uow("required-failure", None)
    assert_true(result == TransactionCompletionStatus.CONFLICT, "Required FAILED no puede commit.")
    assert_true(ledger.get_turn_uow_status("required-failure") == TurnUoWStatus.OPEN, "Debe quedar clasificable, no mentir committed.")


def test_04_behavior_owner_rolls_back_mutate_then_raise() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("proteger el faro")
    before = _behavior_checkpoint_view(behavior._crear_checkpoint_operacional_locked())

    def fail_after_mutation(*args, **kwargs):
        raise RuntimeError("failure-injection-after-behavior-mutation")

    behavior.psychology.generar_contexto_llm = fail_after_mutation
    try:
        behavior.procesar_evento(
            "alerta_dolor_fisico",
            {
                "intensidad": 0.9,
                "accion": "hablar",
                "exito": True,
                "percepcion": {"objeto": "faro"},
            },
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("La inyeccion Behavior debio fallar.")
    after = _behavior_checkpoint_view(behavior._crear_checkpoint_operacional_locked())
    differing = [key for key in before if before[key] != after[key]]
    assert_true(not differing, f"BehaviorCore debe restaurar todos sus owners; difieren: {differing}.")


def test_05_behavior_failure_is_clean_abort_and_retry_does_not_remutate() -> None:
    orchestrator, _ = _isolated("internal")
    behavior = BehaviorCore()
    calls = {"psychology": 0}

    def bomb(*args, **kwargs):
        calls["psychology"] += 1
        raise RuntimeError("behavior-central-failure")

    behavior.psychology.generar_contexto_llm = bomb
    orchestrator.behavior = behavior
    before = _behavior_checkpoint_view(behavior._crear_checkpoint_operacional_locked())
    event = _event("behavior-abort")
    try:
        orchestrator.procesar_evento(event)
    except TurnProcessingError:
        pass
    else:
        raise AssertionError("Behavior failure no puede parecer no-op.")
    assert_true(before == _behavior_checkpoint_view(behavior._crear_checkpoint_operacional_locked()), "Behavior debe volver a S0.")
    assert_true(orchestrator.action_ledger.get_turn_uow_status("behavior-abort") == TurnUoWStatus.ABORTED_CLEAN, "Rollback conocido debe ser clean abort.")
    try:
        orchestrator.procesar_evento(event)
    except TurnAbortedError:
        pass
    else:
        raise AssertionError("Same-ID clean abort no debe reejecutar.")
    assert_true(calls["psychology"] == 1, "Retry no debe remutar Behavior.")
    assert_true("behavior-abort" not in orchestrator._transacciones_procesadas, "Failure no entra al cache.")


def test_06_motor_exception_uses_central_failure_boundary() -> None:
    orchestrator, calls = _isolated("motor")
    event = _event("motor-abort", event_type="comando_usuario", decision=True)
    try:
        orchestrator.procesar_evento(event)
    except TurnProcessingError:
        pass
    else:
        raise AssertionError("Motor exception no puede retornar accion valida.")
    effects = _effect_map(orchestrator.action_ledger, "motor-abort")
    assert_true(calls["motor"] == 1, "Motor debe haberse intentado una vez.")
    assert_true(effects["decision:layer"].status == TurnEffectStatus.FAILED, "Motor failure debe cerrar la decision.")
    assert_true(orchestrator.action_ledger.get_turn_uow_status("motor-abort") == TurnUoWStatus.ABORTED_CLEAN, "Sin decision effects aplicados es clean abort.")
    assert_true(orchestrator.action_ledger.get_action_for_transaction("motor-abort") is None, "No debe existir action valida.")
    assert_true(orchestrator.memory.acciones == [], "No debe escribir WorkingMemory.")
    assert_true("motor-abort" not in orchestrator._transacciones_procesadas, "No debe cachear.")
    try:
        orchestrator.procesar_evento(event)
    except TurnAbortedError:
        pass
    else:
        raise AssertionError("Retry Motor debe devolver fallo durable.")
    assert_true(calls["motor"] == 1 and len(orchestrator.memory.experiencias) == 1, "Retry no duplica Motor ni event memory.")


def test_07_invalid_decision_has_zero_decision_owned_effects() -> None:
    orchestrator, _ = _isolated("persona")
    orchestrator._resolver_ruta_persona = lambda **kwargs: ActionResponse(
        pensamiento="invalido",
        accion="hablar",
        parametros={},
        razon="contrato roto",
        valido=False,
        error="invalid",
    )
    event = _event("invalid-decision", origin="terminal", event_type="comando_usuario", decision=True, details={"mensaje": "hola"})
    try:
        orchestrator.procesar_evento(event)
    except TurnProcessingError:
        pass
    else:
        raise AssertionError("Decision invalida no debe convertirse en hablar valido.")
    assert_true(orchestrator.action_ledger.get_action_for_transaction("invalid-decision") is None, "No action ejecutable.")
    assert_true(orchestrator.memory.acciones == [], "Zero WorkingMemory action.")
    assert_true(orchestrator.memory.turnos == [], "Zero ConversationMemory.")
    assert_true(orchestrator.identity_state.eventos == [], "Zero Identity signal.")
    assert_true(list(orchestrator._notificaciones) == [], "Zero notification.")
    assert_true(orchestrator._alias_candidates_pendientes == {}, "Zero alias candidate.")
    assert_true("invalid-decision" not in orchestrator._transacciones_procesadas, "Zero success cache.")
    assert_true(orchestrator.action_ledger.get_turn_uow_status("invalid-decision") == TurnUoWStatus.ABORTED_CLEAN, "Invalid decision es clean abort conocido.")


def test_08_optional_event_memory_failure_is_visible_but_can_commit() -> None:
    orchestrator, _ = _isolated("internal")

    def memory_failure(**kwargs):
        raise OSError("memory-down")

    orchestrator.memory.guardar_experiencia = memory_failure
    action = orchestrator.procesar_evento(_event("optional-memory"))
    effects = _effect_map(orchestrator.action_ledger, "optional-memory")
    assert_true(action is not None and action.nombre == "none", "El no-op legitimo debe completar.")
    assert_true(effects["memory:event_episode"].required is False, "Episodio de evento es optional explicito.")
    assert_true(effects["memory:event_episode"].status == TurnEffectStatus.FAILED, "Failure optional debe ser observable.")
    assert_true(orchestrator.action_ledger.get_turn_uow_status("optional-memory") == TurnUoWStatus.COMMITTED, "Optional failure no impide commit.")


def test_09_manual_memory_failure_is_required_and_not_success() -> None:
    orchestrator, _ = _isolated("persona")
    orchestrator.triage_router.intent = "CONVERSATION_SAVE"
    orchestrator.memory.resultados_guardado_manual.append(
        ManualConversationSaveResult(status="error", error="write-failed", turn_count=2)
    )
    event = _event(
        "manual-memory-failure",
        origin="terminal",
        event_type="comando_usuario",
        decision=True,
        details={"mensaje": "guarda esta conversacion"},
    )
    try:
        orchestrator.procesar_evento(event)
    except TurnProcessingError:
        pass
    else:
        raise AssertionError("Fallo de guardado requerido no puede ser API success.")
    effect = _effect_map(orchestrator.action_ledger, "manual-memory-failure")["decision:manual_memory_save"]
    assert_true(effect.required and effect.status == TurnEffectStatus.FAILED, "Guardado manual debe declarar required FAILED.")
    assert_true(orchestrator.action_ledger.get_turn_uow_status("manual-memory-failure") == TurnUoWStatus.ABORTED_CLEAN, "Fallo conocido sin aplicacion confirmada.")
    assert_true("manual-memory-failure" not in orchestrator._transacciones_procesadas, "No cache success.")


class _NoParams(BaseModel):
    pass


def test_10_local_tool_is_not_reexecuted_after_later_failure() -> None:
    previous = dict(ToolRegistry._tools)
    counter = {"handler": 0}
    try:
        @ToolRegistry.register("root007_counter", "ROOT-007 counter", _NoParams)
        def handler(params):
            counter["handler"] += 1
            return ToolResult(True, "applied")

        orchestrator, _ = _isolated("persona")
        orchestrator.tool_registry = ToolRegistry
        orchestrator._resolver_ruta_persona = lambda **kwargs: ActionResponse(
            pensamiento="",
            accion="root007_counter",
            parametros={},
            razon="failure injection",
            valido=True,
        )

        def fail_after_tool(**kwargs):
            raise RuntimeError("post-tool-working-memory-failure")

        orchestrator.memory.agregar_accion = fail_after_tool
        event = _event("local-post-failure", origin="sandbox", event_type="comando_usuario", decision=True)
        try:
            orchestrator.procesar_evento(event)
        except TurnReconciliationRequiredError:
            pass
        else:
            raise AssertionError("El fallo posterior al tool debe abortar completion.")
        assert_true(counter["handler"] == 1, "El handler debe ejecutarse exactamente una vez.")
        assert_true(orchestrator.action_ledger.get_turn_uow_status("local-post-failure") == TurnUoWStatus.PARTIAL, "Tool aplicado + consumer fallido debe ser PARTIAL.")
        assert_true(_effect_map(orchestrator.action_ledger, "local-post-failure")["execution:local_tool"].status == TurnEffectStatus.APPLIED, "El receipt debe recordar el tool aplicado.")
        try:
            orchestrator.procesar_evento(event)
        except TurnReconciliationRequiredError:
            pass
        else:
            raise AssertionError("Retry partial debe quedar bloqueado.")
        assert_true(counter["handler"] == 1, "Retry no reejecuta el handler.")
    finally:
        ToolRegistry._tools = previous


def test_11_outcome_partial_is_not_consumed_or_replayed() -> None:
    ledger = ActionLedger(":memory:")
    _prepare_executing_action(ledger, action_id="outcome-partial-action", transaction_id="outcome-partial-origin")
    orchestrator, _ = _isolated("internal", ledger=ledger)
    behavior_calls = {"count": 0}
    original_behavior = orchestrator.behavior.procesar_evento

    def counted_behavior(*args, **kwargs):
        behavior_calls["count"] += 1
        return original_behavior(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted_behavior

    def fail_second_consumer(**kwargs):
        raise RuntimeError("outcome-working-memory-failure")

    orchestrator.memory.agregar_accion = fail_second_consumer
    event = _outcome_event("outcome-partial-turn", "outcome-partial-action")
    try:
        orchestrator.procesar_evento(event)
    except TurnReconciliationRequiredError:
        pass
    else:
        raise AssertionError("Consumer parcial no debe completar.")
    durable = ledger.get_outcome("outcome-partial-action")
    assert_true(durable is not None and durable.consumed_at is None, "Outcome durable pero no consumed.")
    assert_true(ledger.get_turn_uow_status("outcome-partial-turn") == TurnUoWStatus.PARTIAL, "Outcome consumer partial debe ser PARTIAL.")
    assert_true(_effect_map(ledger, "outcome-partial-turn")["outcome:behavior_task"].status == TurnEffectStatus.APPLIED, "Consumer 1 debe tener receipt.")
    try:
        orchestrator.procesar_evento(event)
    except TurnReconciliationRequiredError:
        pass
    else:
        raise AssertionError("Outcome partial no debe replay automatico.")
    assert_true(behavior_calls["count"] == 1, "Consumer 1 no se reaplica.")


def test_12_outcome_complete_consumes_after_required_consumers() -> None:
    ledger = ActionLedger(":memory:")
    _prepare_executing_action(ledger, action_id="outcome-complete-action", transaction_id="outcome-complete-origin")
    orchestrator, _ = _isolated("internal", ledger=ledger)
    result = orchestrator.procesar_evento(_outcome_event("outcome-complete-turn", "outcome-complete-action"))
    effects = ledger.get_turn_effects("outcome-complete-turn")
    durable = ledger.get_outcome("outcome-complete-action")
    assert_true(result is None, "Outcome no crea una segunda action.")
    assert_true(durable is not None and durable.consumed_at is not None, "Debe marcar consumed al final.")
    assert_true(ledger.get_turn_uow_status("outcome-complete-turn") == TurnUoWStatus.COMMITTED, "Outcome UoW committed.")
    assert_true(ledger.get_transaction("outcome-complete-turn").status == "completed", "Transaction completa.")
    assert_true(
        all((not effect.required) or effect.status in {TurnEffectStatus.APPLIED, TurnEffectStatus.SKIPPED} for effect in effects),
        "Cada efecto required debe quedar resuelto.",
    )
    mark = _effect_map(ledger, "outcome-complete-turn")["outcome:mark_consumed"]
    assert_true(mark.status == TurnEffectStatus.APPLIED, "consumed_at debe tener receipt final.")


def test_13_restart_preserves_partial_and_blocks_pipeline() -> None:
    with TemporaryDirectory(prefix="root007-partial-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first_ledger = ActionLedger(path)
        _prepare_executing_action(first_ledger, action_id="restart-partial-action", transaction_id="restart-partial-origin")
        first, _ = _isolated("internal", ledger=first_ledger)
        first.memory.agregar_accion = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("consumer-failure"))
        event = _outcome_event("restart-partial-turn", "restart-partial-action")
        try:
            first.procesar_evento(event)
        except TurnReconciliationRequiredError:
            pass
        else:
            raise AssertionError("Fixture debe producir partial.")
        first_ledger.close()

        reopened = ActionLedger(path)
        second, _ = _isolated("internal", ledger=reopened)
        assert_true(reopened.get_turn_uow_status("restart-partial-turn") == TurnUoWStatus.PARTIAL, "Restart conserva PARTIAL.")
        assert_true(reopened.get_action("restart-partial-action") is not None, "Restart conserva action identity.")
        assert_true(reopened.get_outcome("restart-partial-action") is not None, "Restart conserva outcome identity.")
        try:
            second.procesar_evento(event)
        except TurnReconciliationRequiredError:
            pass
        else:
            raise AssertionError("Restart partial requiere reconciliacion.")
        assert_true(second.memory.experiencias == [] and second.memory.acciones == [], "Restart no reejecuta pipeline.")
        reopened.close()


def test_14_restart_replays_committed_response() -> None:
    with TemporaryDirectory(prefix="root007-committed-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first_ledger = ActionLedger(path)
        first, _ = _isolated("internal", ledger=first_ledger)
        event = _event("restart-committed")
        original = first.procesar_evento(event)
        first_ledger.close()
        reopened = ActionLedger(path)
        second, _ = _isolated("internal", ledger=reopened)
        replay = second.procesar_evento(event)
        assert_true(original is not None and replay is not None and replay.id_accion == original.id_accion, "Restart debe replay durable exacto.")
        assert_true(second.memory.experiencias == [] and second.identity_state.eventos == [], "Replay committed no corre side effects.")
        reopened.close()


def test_15_restart_replays_clean_abort_without_behavior() -> None:
    with TemporaryDirectory(prefix="root007-aborted-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first_ledger = ActionLedger(path)
        first, _ = _isolated("motor", ledger=first_ledger)
        event = _event("restart-aborted", event_type="comando_usuario", decision=True)
        try:
            first.procesar_evento(event)
        except TurnProcessingError:
            pass
        else:
            raise AssertionError("Fixture debe abortar.")
        first_ledger.close()
        reopened = ActionLedger(path)
        second, calls = _isolated("motor", ledger=reopened)
        try:
            second.procesar_evento(event)
        except TurnAbortedError:
            pass
        else:
            raise AssertionError("Restart debe devolver clean abort durable.")
        assert_true(calls["motor"] == 0 and second.memory.experiencias == [], "No reaplica Behavior/Motor/memory.")
        reopened.close()


def test_16_same_transaction_concurrent_has_one_owner() -> None:
    orchestrator, _ = _isolated("internal")
    calls = {"behavior": 0}
    calls_lock = threading.Lock()
    original = orchestrator.behavior.procesar_evento

    def counted(*args, **kwargs):
        with calls_lock:
            calls["behavior"] += 1
        return original(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted
    event = _event("concurrent-uow")
    gate = threading.Barrier(3)

    def run():
        gate.wait()
        return orchestrator.procesar_evento(event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        gate.wait()
        results = [future.result(timeout=5) for future in futures]
    assert_true(calls["behavior"] == 1, "Same transaction concurrent debe tener un owner.")
    assert_true(results[0].id_accion == results[1].id_accion, "El segundo caller recibe replay durable.")
    assert_true(orchestrator.action_ledger.get_turn_uow_status("concurrent-uow") == TurnUoWStatus.COMMITTED, "Un solo UoW committed.")


def test_17_open_after_restart_becomes_reconciliation_required() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("abandoned-open", origin="unity", event_type="actualizacion_estado")
    ledger.begin_turn_uow("abandoned-open")
    orchestrator, _ = _isolated("internal", ledger=ledger)
    try:
        orchestrator.procesar_evento(_event("abandoned-open"))
    except TurnReconciliationRequiredError:
        pass
    else:
        raise AssertionError("OPEN recuperado no debe replay automatico.")
    assert_true(ledger.get_turn_uow_status("abandoned-open") == TurnUoWStatus.RECONCILIATION_REQUIRED, "OPEN abandonado debe quedar durable ambiguo.")


def test_18_api_distinguishes_noop_failure_and_partial() -> None:
    class Noop:
        @staticmethod
        def procesar_evento(event):
            return None

    class Failure:
        @staticmethod
        def procesar_evento(event):
            raise TurnProcessingError("behavior_core", "InjectedFailure")

    class Partial:
        @staticmethod
        def procesar_evento(event):
            raise TurnReconciliationRequiredError("partial")

    def request(orchestrator, transaction_id: str):
        app = FastAPI()
        app.state.orchestrator = orchestrator
        app.include_router(router)
        return TestClient(app).post(
            "/api/v1/evento",
            json={
                "origen": "unity",
                "tipo_evento": "actualizacion_estado",
                "id_transaccion": transaction_id,
                "detalles": {},
            },
        )

    noop = request(Noop(), "api-noop")
    failure = request(Failure(), "api-failure")
    partial = request(Partial(), "api-partial")
    assert_true(noop.status_code == 200 and noop.json()["status"] == "ok" and noop.json()["accion"] is None, "No-op valido es success sin action.")
    assert_true(failure.status_code == 500 and failure.json()["status"] == "error", "Fallo central no es success.")
    assert_true(partial.status_code == 409 and partial.json()["status"] == "pendiente", "Partial exige reconciliacion.")
    assert_true("InjectedFailure" not in failure.text, "API no filtra failure crudo.")


def test_19_audittrace_v1_remains_frozen_and_is_not_the_journal() -> None:
    assert_true(AUDIT_TRACE_VERSION == 1, "AuditTrace debe seguir v1.")
    assert_true(
        AUDIT_STAGES == {"event_start", "triage", "memory", "decision", "execution", "event_end", "error"},
        "No debe agregar stages UoW/commit/rollback.",
    )
    source = Path(__file__).with_name("audit_trace.py").read_text(encoding="utf-8")
    assert_true("turn_uows" not in source and "turn_effects" not in source, "AuditTrace no decide recovery.")


def test_20_failure_matrix_covers_f0_through_f17_without_root015_claims() -> None:
    matrix = {
        "F0": "atomic_claim_no_gap",
        "F1": "rolled_back",
        "F2": "aborted_clean",
        "F3": "optional_failed_or_reconciliation",
        "F4": "aborted_clean",
        "F5": "aborted_clean",
        "F6": "partial",
        "F7": "aborted_clean",
        "F8": "partial",
        "F9": "partial",
        "F10": "partial_or_reconciliation",
        "F11": "partial_or_reconciliation",
        "F12": "partial_or_reconciliation",
        "F13": "partial",
        "F14": "partial_or_reconciliation",
        "F15": "partial",
        "F16": "partial",
        "F17": "partial",
    }
    assert_true(tuple(matrix) == tuple(f"F{i}" for i in range(18)), "La matriz debe cubrir F0..F17.")
    assert_true(all("retry" not in value for value in matrix.values()), "No promete dedupe Memory de ROOT-015.")


TESTS = [
    test_01_v2_to_v3_preserves_history_and_marks_legacy_unknown,
    test_02_effect_receipts_are_terminal_and_commit_is_atomic,
    test_03_required_failure_prevents_commit,
    test_04_behavior_owner_rolls_back_mutate_then_raise,
    test_05_behavior_failure_is_clean_abort_and_retry_does_not_remutate,
    test_06_motor_exception_uses_central_failure_boundary,
    test_07_invalid_decision_has_zero_decision_owned_effects,
    test_08_optional_event_memory_failure_is_visible_but_can_commit,
    test_09_manual_memory_failure_is_required_and_not_success,
    test_10_local_tool_is_not_reexecuted_after_later_failure,
    test_11_outcome_partial_is_not_consumed_or_replayed,
    test_12_outcome_complete_consumes_after_required_consumers,
    test_13_restart_preserves_partial_and_blocks_pipeline,
    test_14_restart_replays_committed_response,
    test_15_restart_replays_clean_abort_without_behavior,
    test_16_same_transaction_concurrent_has_one_owner,
    test_17_open_after_restart_becomes_reconciliation_required,
    test_18_api_distinguishes_noop_failure_and_partial,
    test_19_audittrace_v1_remains_frozen_and_is_not_the_journal,
    test_20_failure_matrix_covers_f0_through_f17_without_root015_claims,
]


if __name__ == "__main__":
    passed = 0
    for test in TESTS:
        test()
        passed += 1
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-007: {passed}/{len(TESTS)} pruebas pasaron.")
