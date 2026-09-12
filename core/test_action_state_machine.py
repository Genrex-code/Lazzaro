"""Pruebas permanentes de ROOT-004: authorization y execution lifecycle."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from api.routes import router
from api.schemas import AccionSaliente, EventoEntrante
from core.action_ledger import (
    ActionLedger,
    ActionState,
    ActionTransitionEvent,
    OutcomeRecordStatus,
    TransitionClassification,
)
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from ai_engine.identity_state import IdentityState
from terminal_client.test_chat import _orquestador_aislado
from tools.base import ToolResult
from tools.executor import ActionExecutor
from tools.registry import ToolRegistry


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _new_action(
    ledger: ActionLedger,
    *,
    action_id: str = "action-a",
    transaction_id: str = "tx-a",
    requires_confirmation: bool = False,
    intended_client: str = "unity",
    action_name: str = "navegar_a_objeto",
    outcome_expected: bool = True,
):
    ledger.claim_transaction(
        transaction_id,
        origin=intended_client,
        event_type="comando_usuario",
    )
    receipt = ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name=action_name,
        parameters={"nombre_objeto": "Cube"},
        requires_confirmation=requires_confirmation,
        task_instance_id=None,
        intended_client=intended_client,
        outcome_expected=outcome_expected,
        decision_valid=True,
    )
    assert_true(receipt.action is not None, "El fixture debe crear la action.")
    return receipt.action


def _waiting(ledger: ActionLedger, **kwargs):
    action = _new_action(ledger, requires_confirmation=True, **kwargs)
    ledger.transition_action(
        action.action_id,
        ActionTransitionEvent.REQUIRE_AUTHORIZATION,
        receipt_id=f"wait:{action.action_id}",
    )
    return ledger.get_action(action.action_id)


def _dispatched(ledger: ActionLedger, **kwargs):
    action = _new_action(ledger, **kwargs)
    ledger.transition_action(
        action.action_id,
        ActionTransitionEvent.AUTO_AUTHORIZE,
        receipt_id=f"auto:{action.action_id}",
    )
    ledger.transition_action(
        action.action_id,
        ActionTransitionEvent.DISPATCH,
        receipt_id=f"dispatch:{action.action_id}",
    )
    return ledger.get_action(action.action_id)


def _executing(ledger: ActionLedger, **kwargs):
    action = _dispatched(ledger, **kwargs)
    ledger.transition_action(
        action.action_id,
        ActionTransitionEvent.START_EXECUTION,
        receipt_id=f"start:{action.action_id}",
    )
    return ledger.get_action(action.action_id)


def _confirmation_event(transaction_id: str, action_id: str, approved: bool):
    return EventoEntrante(
        origen="unity",
        tipo_evento="confirmacion_accion",
        id_transaccion=transaction_id,
        detalles={"id_accion": action_id, "approved": approved},
    )


def _app(orchestrator) -> FastAPI:
    app = FastAPI()
    app.state.orchestrator = orchestrator
    app.include_router(router)
    return app


def _orchestrator_with_waiting(action_id: str = "waiting-a"):
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    _waiting(
        ledger,
        action_id=action_id,
        transaction_id=f"origin-{action_id}",
    )
    ledger.complete_transaction(f"origin-{action_id}", None)
    return orchestrator, ledger


def test_01_new_action_starts_created_then_auto_authorizes() -> None:
    ledger = ActionLedger(":memory:")
    action = _new_action(ledger)
    assert_true(action.action_state == ActionState.CREATED, "Debe iniciar CREATED.")
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.AUTO_AUTHORIZE)
    assert_true(result.state_after == ActionState.AUTHORIZED, "Debe autorizarse explícitamente.")


def test_02_external_release_is_dispatched() -> None:
    ledger = ActionLedger(":memory:")
    action = _dispatched(ledger)
    assert_true(action.action_state == ActionState.DISPATCHED, "Release externo debe ser DISPATCHED.")


def test_03_confirmation_requirement_enters_waiting() -> None:
    ledger = ActionLedger(":memory:")
    assert_true(_waiting(ledger).action_state == ActionState.AWAITING_AUTHORIZATION, "Debe esperar permiso.")


def test_04_waiting_has_no_execution_permit() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger)
    assert_true(ledger.issue_execution_permit(action.action_id) is None, "Waiting no ejecuta.")


def test_05_approve_moves_waiting_to_authorized() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger)
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.AUTHORIZE)
    assert_true(result.state_after == ActionState.AUTHORIZED, "Approve debe autorizar.")


def test_06_approve_preserves_exact_action_id() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger, action_id="stable-id")
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.AUTHORIZE)
    assert_true(result.action_id == "stable-id" and ledger.get_action("stable-id") is not None, "No debe crear action B.")


def test_07_confirmation_bypasses_ai_and_behavior() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("no-ai")
    calls = {"behavior": 0}
    original = orchestrator.behavior.procesar_evento

    def counted(*args, **kwargs):
        calls["behavior"] += 1
        return original(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted
    action = orchestrator.procesar_evento(_confirmation_event("confirm-no-ai", "no-ai", True))
    assert_true(calls["behavior"] == 0 and action.id_accion == "no-ai", "Confirmar no debe repetir cognición.")
    ledger.close()


def test_08_duplicate_approval_has_zero_second_dispatch() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("duplicate-approval")
    first = orchestrator.procesar_evento(_confirmation_event("approve-1", "duplicate-approval", True))
    second = orchestrator.procesar_evento(_confirmation_event("approve-2", "duplicate-approval", True))
    dispatches = [t for t in ledger.get_action_transitions("duplicate-approval") if t.event == ActionTransitionEvent.DISPATCH]
    assert_true(first is not None and second is None and len(dispatches) == 1, "Sólo el primer approve despacha.")


def test_09_deny_moves_waiting_to_rejected() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger)
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.REJECT)
    assert_true(result.state_after == ActionState.REJECTED, "Deny debe ser terminal neutral.")


def test_10_deny_cannot_dispatch() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger)
    ledger.transition_action(action.action_id, ActionTransitionEvent.REJECT)
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.DISPATCH)
    assert_true(result.classification == TransitionClassification.INVALID_TRANSITION, "Rejected no despacha.")


def test_11_deny_does_not_increment_task_failure() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("deny-task")
    orchestrator.behavior = BehaviorCore()
    orchestrator.behavior.iniciar_tarea("objetivo")
    before = orchestrator.behavior.task_context.obtener_resumen()
    orchestrator.procesar_evento(_confirmation_event("deny-task-receipt", "deny-task", False))
    after = orchestrator.behavior.task_context.obtener_resumen()
    assert_true(before["intentos_totales"] == after["intentos_totales"] == 0, "Deny no es intento.")
    ledger.close()


def test_12_deny_does_not_signal_identity_failure() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("deny-identity")
    calls = []
    orchestrator.identity_state.registrar_evento = lambda *args, **kwargs: calls.append(kwargs)
    orchestrator.procesar_evento(_confirmation_event("deny-identity-receipt", "deny-identity", False))
    assert_true(calls == [], "Deny no debe mutar identidad.")
    ledger.close()


def test_13_unknown_confirmation_is_side_effect_free() -> None:
    ledger = ActionLedger(":memory:")
    result = ledger.transition_action("missing", ActionTransitionEvent.AUTHORIZE, receipt_id="unknown-confirm")
    assert_true(result.classification == TransitionClassification.UNKNOWN_ACTION, "Debe fallar cerrado.")


def test_14_confirmation_requires_strict_bool() -> None:
    try:
        EventoEntrante(
            origen="unity",
            tipo_evento="confirmacion_accion",
            id_transaccion="ambiguous",
            detalles={"id_accion": "a", "approved": 1},
        )
    except ValidationError:
        return
    raise AssertionError("approved=1 debió rechazarse.")


def test_15_waiting_outcome_is_illegal() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger)
    result = ledger.record_outcome(
        action_id=action.action_id,
        outcome_transaction_id="waiting-outcome",
        success=False,
        action_name=action.action_name,
        payload={},
    )
    assert_true(result.status == OutcomeRecordStatus.INVALID_STATE and ledger.get_outcome(action.action_id) is None, "Waiting no puede fallar.")


def test_16_dispatched_start_is_observable() -> None:
    ledger = ActionLedger(":memory:")
    action = _dispatched(ledger)
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.START_EXECUTION)
    assert_true(result.state_after == ActionState.EXECUTING, "Start del cliente debe ser durable.")


def test_17_duplicate_start_is_idempotent() -> None:
    ledger = ActionLedger(":memory:")
    action = _dispatched(ledger)
    first = ledger.transition_action(action.action_id, ActionTransitionEvent.START_EXECUTION, receipt_id="start-once")
    second = ledger.transition_action(action.action_id, ActionTransitionEvent.START_EXECUTION, receipt_id="start-twice")
    assert_true(first.classification == TransitionClassification.APPLIED and second.classification == TransitionClassification.ALREADY_APPLIED, "Dos starts no mutan dos veces.")


def test_18_executing_can_report_success() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    result = ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="success", success=True, action_name=action.action_name, payload={})
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED and ledger.get_action_state(action.action_id) == ActionState.REPORTED_SUCCESS, "Éxito reportado debe ser terminal.")


def test_19_executing_can_report_failure() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    result = ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="failure", success=False, action_name=action.action_name, payload={})
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED and ledger.get_action_state(action.action_id) == ActionState.REPORTED_FAILURE, "Fallo reportado debe ser terminal.")


def test_20_success_cannot_restart_execution() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="done", success=True, action_name=action.action_name, payload={})
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.START_EXECUTION)
    assert_true(result.classification == TransitionClassification.INVALID_TRANSITION, "Success no admite un start nuevo.")
    assert_true(ledger.get_action_state(action.action_id) == ActionState.REPORTED_SUCCESS, "Success no se reabre.")


def test_21_failure_cannot_dispatch() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="failed", success=False, action_name=action.action_name, payload={})
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.DISPATCH)
    assert_true(result.classification == TransitionClassification.INVALID_TRANSITION and ledger.get_action_state(action.action_id) == ActionState.REPORTED_FAILURE, "Failure no vuelve a dispatch.")


def test_22_duplicate_success_outcome_is_not_accepted_twice() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    first = ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="success-1", success=True, action_name=action.action_name, payload={})
    second = ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="success-2", success=True, action_name=action.action_name, payload={})
    assert_true(first.status == OutcomeRecordStatus.ACCEPTED and second.status == OutcomeRecordStatus.DUPLICATE, "Sólo un outcome se acepta.")


def test_23_conflicting_terminal_does_not_overwrite() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="winner", success=True, action_name=action.action_name, payload={})
    loser = ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="loser", success=False, action_name=action.action_name, payload={})
    assert_true(loser.status == OutcomeRecordStatus.CONFLICT and ledger.get_action_state(action.action_id) == ActionState.REPORTED_SUCCESS, "Terminal ganador no se sobrescribe.")


def test_24_valid_decision_is_not_execution_success() -> None:
    ledger = ActionLedger(":memory:")
    action = _new_action(ledger)
    assert_true(action.decision_valid is True and action.action_state == ActionState.CREATED and ledger.get_outcome(action.action_id) is None, "Validez sólo pertenece al eje decision.")


def test_25_dispatch_is_not_execution_success() -> None:
    ledger = ActionLedger(":memory:")
    action = _dispatched(ledger)
    assert_true(action.action_state == ActionState.DISPATCHED and ledger.get_outcome(action.action_id) is None, "Dispatch no es success.")


class _EmptyParams(BaseModel):
    pass


def test_26_sandbox_runs_authorized_executing_terminal() -> None:
    previous = dict(ToolRegistry._tools)
    calls = {"handler": 0}
    try:
        @ToolRegistry.register("root004_sandbox", "fake", _EmptyParams)
        def handler(params):
            calls["handler"] += 1
            return ToolResult(True, "ok")

        ledger = ActionLedger(":memory:")
        _new_action(ledger, action_name="root004_sandbox", intended_client="sandbox")
        orchestrator = object.__new__(Orchestrator)
        orchestrator.action_ledger = ledger
        event = EventoEntrante(origen="sandbox", tipo_evento="comando_usuario", id_transaccion="tx-a", detalles={})
        action = AccionSaliente(id_accion="action-a", nombre="root004_sandbox")
        status, details = orchestrator._gestionar_ejecucion(event, action)
        outcome = ledger.record_outcome(action_id="action-a", outcome_transaction_id="sandbox-result", success=status == "ok", action_name="root004_sandbox", payload=details or {})
        states = [item.state_after for item in ledger.get_action_transitions("action-a")]
        assert_true(calls["handler"] == 1 and outcome.status == OutcomeRecordStatus.ACCEPTED, "Sandbox debe ejecutar una vez.")
        assert_true(states[-3:] == [ActionState.AUTHORIZED, ActionState.EXECUTING, ActionState.REPORTED_SUCCESS], "Lifecycle sandbox completo.")
    finally:
        ToolRegistry._tools = previous


def test_27_none_is_skipped_not_failed() -> None:
    ledger = ActionLedger(":memory:")
    action = _new_action(ledger, action_name="none", outcome_expected=False)
    result = ledger.transition_action(action.action_id, ActionTransitionEvent.SKIP)
    assert_true(result.state_after == ActionState.SKIPPED and ledger.get_outcome(action.action_id) is None, "None debe ser neutral.")


def test_28_hablar_external_has_reported_lifecycle() -> None:
    ledger = ActionLedger(":memory:")
    action = _dispatched(ledger, action_name="hablar", outcome_expected=True)
    ledger.transition_action(action.action_id, ActionTransitionEvent.START_EXECUTION)
    outcome = ledger.record_outcome(action_id=action.action_id, outcome_transaction_id="speech", success=True, action_name="hablar", payload={})
    assert_true(outcome.status == OutcomeRecordStatus.ACCEPTED and ledger.get_action_state(action.action_id) == ActionState.REPORTED_SUCCESS, "Unity reporta la entrega de hablar.")


def test_29_restart_preserves_current_state() -> None:
    with TemporaryDirectory(prefix="root004-state-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first = ActionLedger(path)
        _waiting(first, action_id="restart", transaction_id="restart-tx")
        first.close()
        second = ActionLedger(path)
        assert_true(second.get_action_state("restart") == ActionState.AWAITING_AUTHORIZATION, "Restart conserva waiting.")
        second.close()


def test_30_restart_preserves_transition_history() -> None:
    with TemporaryDirectory(prefix="root004-history-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first = ActionLedger(path)
        _executing(first, action_id="history", transaction_id="history-tx")
        count = len(first.get_action_transitions("history"))
        first.close()
        second = ActionLedger(path)
        assert_true(second.get_action_state("history") == ActionState.EXECUTING and len(second.get_action_transitions("history")) == count, "Restart conserva snapshot e historia.")
        second.close()


def test_31_concurrent_authorize_has_one_effective_transition() -> None:
    ledger = ActionLedger(":memory:")
    action = _waiting(ledger)
    barrier = threading.Barrier(2)
    results = []

    def worker(index: int):
        barrier.wait(timeout=2)
        results.append(ledger.transition_action(action.action_id, ActionTransitionEvent.AUTHORIZE, receipt_id=f"approve-{index}").classification)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert_true(results.count(TransitionClassification.APPLIED) == 1 and results.count(TransitionClassification.ALREADY_APPLIED) == 1, "Una sola autorización muta.")


def test_32_concurrent_terminals_have_one_winner() -> None:
    ledger = ActionLedger(":memory:")
    action = _executing(ledger)
    barrier = threading.Barrier(2)
    results = []

    def worker(success: bool):
        barrier.wait(timeout=2)
        results.append(ledger.record_outcome(action_id=action.action_id, outcome_transaction_id=f"terminal-{success}", success=success, action_name=action.action_name, payload={}).status)

    threads = [threading.Thread(target=worker, args=(value,)) for value in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert_true(results.count(OutcomeRecordStatus.ACCEPTED) == 1 and results.count(OutcomeRecordStatus.CONFLICT) == 1, "Sólo un terminal gana.")


def test_33_schema_v1_migrates_conservatively() -> None:
    with TemporaryDirectory(prefix="root004-migration-") as directory:
        path = Path(directory) / "v1.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE ledger_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO ledger_meta VALUES('schema_version', '1');
            CREATE TABLE transactions(transaction_id TEXT PRIMARY KEY, origin TEXT NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL, action_id TEXT, response_json TEXT, created_at TEXT NOT NULL, completed_at TEXT);
            CREATE TABLE actions(action_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL UNIQUE, action_name TEXT NOT NULL, parameters_json TEXT NOT NULL, requires_confirmation INTEGER NOT NULL, task_instance_id TEXT, intended_client TEXT NOT NULL, outcome_expected INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE outcomes(action_id TEXT PRIMARY KEY, outcome_transaction_id TEXT NOT NULL UNIQUE, success INTEGER NOT NULL, action_name TEXT NOT NULL, payload_json TEXT NOT NULL, payload_fingerprint TEXT NOT NULL, received_at TEXT NOT NULL, consumed_at TEXT);
            CREATE TABLE outcome_attempts(attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, outcome_transaction_id TEXT NOT NULL UNIQUE, action_id TEXT NOT NULL, classification TEXT NOT NULL, payload_fingerprint TEXT NOT NULL, received_at TEXT NOT NULL);
            INSERT INTO transactions VALUES('legacy-tx','unity','comando_usuario','completed','legacy-action','{}','2026-01-01Z','2026-01-01Z');
            INSERT INTO actions VALUES('legacy-action','legacy-tx','hablar','{}',0,NULL,'unity',1,'2026-01-01Z');
            INSERT INTO outcomes VALUES('legacy-action','legacy-outcome',1,'hablar','{}','fp','2026-01-01Z','2026-01-02Z');
            """
        )
        connection.commit()
        connection.close()
        ledger = ActionLedger(path)
        action = ledger.get_action("legacy-action")
        outcome = ledger.get_outcome("legacy-action")
        assert_true(action.action_state == ActionState.LEGACY_UNKNOWN and action.decision_valid is None, "Migración no inventa lifecycle.")
        assert_true(action.transaction_id == "legacy-tx" and outcome.success is True and outcome.consumed_at == "2026-01-02Z", "Debe conservar v1.")
        ledger.close()


def test_34_transition_receipt_reuse_conflicts() -> None:
    ledger = ActionLedger(":memory:")
    a = _waiting(ledger, action_id="receipt-a", transaction_id="receipt-tx-a")
    b = _waiting(ledger, action_id="receipt-b", transaction_id="receipt-tx-b")
    ledger.transition_action(a.action_id, ActionTransitionEvent.AUTHORIZE, receipt_id="shared-receipt")
    result = ledger.transition_action(b.action_id, ActionTransitionEvent.AUTHORIZE, receipt_id="shared-receipt")
    assert_true(result.classification == TransitionClassification.CONFLICT and ledger.get_action_state(b.action_id) == ActionState.AWAITING_AUTHORIZATION, "Receipt no migra de action.")


def test_35_requirement_flag_is_not_executor_permission() -> None:
    previous = dict(ToolRegistry._tools)
    calls = {"handler": 0}
    try:
        @ToolRegistry.register("root004_guarded", "fake", _EmptyParams, requires_confirmation=True)
        def handler(params):
            calls["handler"] += 1
            return ToolResult(True, "ok")

        action = AccionSaliente(id_accion="guarded", nombre="root004_guarded", requiere_confirmacion=True)
        result = ActionExecutor.ejecutar(action)
        assert_true(result.exito is False and calls["handler"] == 0, "El flag de requisito no autoriza.")
    finally:
        ToolRegistry._tools = previous


def test_36_executor_accepts_matching_execution_permit() -> None:
    previous = dict(ToolRegistry._tools)
    calls = {"handler": 0}
    try:
        @ToolRegistry.register("root004_permitted", "fake", _EmptyParams, requires_confirmation=True)
        def handler(params):
            calls["handler"] += 1
            return ToolResult(True, "ok")

        ledger = ActionLedger(":memory:")
        action = _waiting(ledger, action_name="root004_permitted", intended_client="sandbox")
        ledger.transition_action(action.action_id, ActionTransitionEvent.AUTHORIZE)
        ledger.transition_action(action.action_id, ActionTransitionEvent.START_EXECUTION)
        permit = ledger.issue_execution_permit(action.action_id)
        outgoing = AccionSaliente(id_accion=action.action_id, nombre="root004_permitted", requiere_confirmacion=True)
        result = ActionExecutor.ejecutar(outgoing, permit=permit)
        assert_true(result.exito is True and calls["handler"] == 1, "Permit matching habilita una ejecución.")
    finally:
        ToolRegistry._tools = previous


def test_37_api_pending_is_explicit_and_not_success() -> None:
    class Fake:
        @staticmethod
        def procesar_evento(event):
            return AccionSaliente(id_accion="pending", nombre="root004", requiere_confirmacion=True, estado_ejecucion="awaiting_authorization")

    with TestClient(_app(Fake())) as client:
        response = client.post("/api/v1/evento", json={"origen": "unity", "tipo_evento": "comando_usuario", "id_transaccion": "api-pending", "detalles": {}})
    body = response.json()
    assert_true(response.status_code == 200 and body["status"] == "pendiente" and body["accion"]["estado_ejecucion"] == "awaiting_authorization", "Pending debe ser inequívoco.")


def test_38_api_confirmation_approve_and_duplicate() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("api-approve")
    payload = {"origen": "unity", "tipo_evento": "confirmacion_accion", "id_transaccion": "api-confirm", "detalles": {"id_accion": "api-approve", "approved": True}}
    with TestClient(_app(orchestrator)) as client:
        first = client.post("/api/v1/evento", json=payload)
        second = client.post("/api/v1/evento", json=payload)
    assert_true(first.json()["accion"]["id_accion"] == "api-approve" and first.json()["accion"]["estado_ejecucion"] == "dispatched", "Approve devuelve la misma action liberada.")
    assert_true(second.json()["accion"] is None, "Retry exacto no redespacha.")
    ledger.close()


def test_39_api_confirmation_unknown_and_invalid_bool() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("api-known")
    with TestClient(_app(orchestrator)) as client:
        unknown = client.post("/api/v1/evento", json={"origen": "unity", "tipo_evento": "confirmacion_accion", "id_transaccion": "api-unknown", "detalles": {"id_accion": "missing", "approved": True}})
        invalid = client.post("/api/v1/evento", json={"origen": "unity", "tipo_evento": "confirmacion_accion", "id_transaccion": "api-invalid", "detalles": {"id_accion": "api-known", "approved": "true"}})
    assert_true(unknown.status_code == 200 and unknown.json()["accion"] is None, "Unknown no produce acción.")
    assert_true(invalid.status_code == 422, "Bool ambiguo debe rechazarse en API.")
    ledger.close()


def test_40_api_confirmation_receipt_conflict_is_409() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("api-conflict")
    base = {"origen": "unity", "tipo_evento": "confirmacion_accion", "id_transaccion": "same-confirmation"}
    with TestClient(_app(orchestrator)) as client:
        first = client.post("/api/v1/evento", json={**base, "detalles": {"id_accion": "api-conflict", "approved": True}})
        second = client.post("/api/v1/evento", json={**base, "detalles": {"id_accion": "api-conflict", "approved": False}})
    assert_true(first.status_code == 200 and second.status_code == 409, "Receipt incompatible debe ser conflicto.")
    ledger.close()


def test_41_unity_lifecycle_contract_is_fail_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    agent = (root / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
    api = (root / "unity_client" / "ApiClient.cs").read_text(encoding="utf-8-sig")
    dto = (root / "unity_client" / "DTOs.cs").read_text(encoding="utf-8-sig")
    assert_true('estadoEjecucion != "awaiting_authorization"' in agent, "Waiting debe tener branch exacto.")
    assert_true('estadoEjecucion != "dispatched"' in agent, "Sólo dispatched ejecuta.")
    assert_true('"inicio_ejecucion_accion"' in agent and '{ "id_accion", contexto.IdAccion }' in agent, "Start conserva action ID.")
    assert_true("requiere confirmacion. Se omite en Unity" not in agent, "Requirement ya no genera false failure.")
    assert_true(
        'respuesta.status == "ok" || respuesta.status == "pendiente"' in api
        and '"INVALID_RESPONSE"' in api
        and "respuesta.mensaje" not in api,
        "Status desconocido falla cerrado sin convertir texto remoto en UX.",
    )
    assert_true("public string estado_ejecucion;" in dto and "public string id_transaccion_origen;" in dto, "DTO C# expone lifecycle.")


def test_42_behavior_detectors_do_not_recurse() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("objetivo")
    calls = {"events": 0}
    original = behavior.procesar_evento

    def counted(*args, **kwargs):
        calls["events"] += 1
        return original(*args, **kwargs)

    behavior.procesar_evento = counted
    for _ in range(5):
        behavior.procesar_evento("resultado_accion", {"accion": "navegar_a_objeto", "exito": False})
    summary = behavior.task_context.obtener_resumen()
    assert_true(calls["events"] == 5 and summary["estancamiento_detectado"] is True, "Detectores observan sin amplificar outcomes.")


def test_43_identity_none_is_neutral_relative_to_success() -> None:
    neutral = IdentityState(BehaviorCore())
    successful = IdentityState(BehaviorCore())
    neutral.registrar_evento(route="motor", success=None)
    successful.registrar_evento(route="motor", success=True)
    assert_true(neutral.obtener_estado_completo() == successful.obtener_estado_completo(), "None no se transforma en failure biológico.")


def test_44_sandbox_confirmation_executes_same_action_once() -> None:
    previous = dict(ToolRegistry._tools)
    calls = {"handler": 0}
    try:
        @ToolRegistry.register("root004_confirm_flow", "fake", _EmptyParams, requires_confirmation=True)
        def handler(params):
            calls["handler"] += 1
            return ToolResult(True, "ok")

        orchestrator, _ = _orquestador_aislado("internal")
        ledger = ActionLedger(":memory:")
        orchestrator.action_ledger = ledger
        _waiting(
            ledger,
            action_id="sandbox-confirmed",
            transaction_id="sandbox-origin",
            intended_client="sandbox",
            action_name="root004_confirm_flow",
        )
        ledger.complete_transaction("sandbox-origin", None)
        first = orchestrator.procesar_evento(
            _confirmation_event("sandbox-approval", "sandbox-confirmed", True)
        )
        second = orchestrator.procesar_evento(
            _confirmation_event("sandbox-approval", "sandbox-confirmed", True)
        )
        assert_true(first.id_accion == "sandbox-confirmed" and first.estado_ejecucion == "reported_success", "Approve conserva ID y termina el tool local.")
        assert_true(second is None and calls["handler"] == 1, "Retry no reejecuta handler.")
        ledger.close()
    finally:
        ToolRegistry._tools = previous


def test_45_api_execution_started_is_exact_and_idempotent() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    _dispatched(
        ledger,
        action_id="api-start",
        transaction_id="api-start-origin",
    )
    ledger.complete_transaction("api-start-origin", None)
    payload = {
        "origen": "unity",
        "tipo_evento": "inicio_ejecucion_accion",
        "id_transaccion": "api-start-receipt",
        "detalles": {"id_accion": "api-start"},
    }
    with TestClient(_app(orchestrator)) as client:
        first = client.post("/api/v1/evento", json=payload)
        second = client.post("/api/v1/evento", json=payload)
    starts = [
        item
        for item in ledger.get_action_transitions("api-start")
        if item.event == ActionTransitionEvent.START_EXECUTION
    ]
    assert_true(first.status_code == second.status_code == 200, "Start y retry son receipts válidos.")
    assert_true(ledger.get_action_state("api-start") == ActionState.EXECUTING and len(starts) == 1, "Sólo un start muta.")
    ledger.close()


def test_46_identity_changes_only_on_accepted_terminal_outcome() -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    calls = []
    orchestrator.behavior.registrar_impacto_identidad = (
        lambda *args, **kwargs: calls.append(kwargs.get("success"))
    )
    action = orchestrator.procesar_evento(
        EventoEntrante(
            origen="unity",
            tipo_evento="comando_usuario",
            id_transaccion="identity-command",
            detalles={"mensaje": "hola"},
        )
    )
    orchestrator.procesar_evento(
        EventoEntrante(
            origen="unity",
            tipo_evento="inicio_ejecucion_accion",
            id_transaccion="identity-start",
            detalles={"id_accion": action.id_accion},
        )
    )
    outcome = EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion="identity-outcome",
        detalles={
            "id_accion": action.id_accion,
            "id_transaccion_origen": "identity-command",
            "accion": action.nombre,
            "exito": True,
        },
    )
    orchestrator.procesar_evento(outcome)
    duplicate = outcome.model_copy(update={"id_transaccion": "identity-outcome-duplicate"})
    orchestrator.procesar_evento(duplicate)
    assert_true(calls == [None, True], "Decision/dispatch es neutral; sólo terminal aceptado señala success.")
    ledger.close()


def test_47_api_confirmation_deny_is_terminal_neutral() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("api-deny")
    with TestClient(_app(orchestrator)) as client:
        response = client.post(
            "/api/v1/evento",
            json={
                "origen": "unity",
                "tipo_evento": "confirmacion_accion",
                "id_transaccion": "api-deny-receipt",
                "detalles": {"id_accion": "api-deny", "approved": False},
            },
        )
    assert_true(response.status_code == 200 and response.json()["accion"]["estado_ejecucion"] == "rejected", "Deny debe exponer rechazo neutral.")
    assert_true(ledger.get_outcome("api-deny") is None, "Deny no fabrica outcome.")
    ledger.close()


def test_48_api_cannot_resurrect_terminal_rejection() -> None:
    orchestrator, ledger = _orchestrator_with_waiting("api-terminal")
    with TestClient(_app(orchestrator)) as client:
        client.post(
            "/api/v1/evento",
            json={
                "origen": "unity",
                "tipo_evento": "confirmacion_accion",
                "id_transaccion": "terminal-deny",
                "detalles": {"id_accion": "api-terminal", "approved": False},
            },
        )
        resurrect = client.post(
            "/api/v1/evento",
            json={
                "origen": "unity",
                "tipo_evento": "confirmacion_accion",
                "id_transaccion": "terminal-approve",
                "detalles": {"id_accion": "api-terminal", "approved": True},
            },
        )
    assert_true(resurrect.status_code == 200 and resurrect.json()["accion"] is None, "Terminal no produce nuevo dispatch.")
    assert_true(ledger.get_action_state("api-terminal") == ActionState.REJECTED, "Rejected no resucita.")
    ledger.close()


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-004: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
