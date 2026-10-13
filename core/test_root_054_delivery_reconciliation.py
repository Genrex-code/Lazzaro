"""Regresiones del contrato ROOT-054 de delivery y reconciliacion."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.routes import router
from api.schemas import EventoEntrante
from core.action_ledger import (
    ActionLedger,
    ActionTransitionEvent,
    OutcomeRecordStatus,
    TransactionClaimStatus,
    TransactionCompletionStatus,
    TurnReconciliationRequiredError,
)
from core.transport_delivery import (
    DeliveryDirectiveV1,
    DeliveryReconciliationResultV1,
    DeliveryResolutionV1,
    DurableReceiptKindV1,
    DurableReceiptStatusV1,
    request_fingerprint_sha256_v1,
)
from terminal_client.test_chat import _orquestador_aislado


def _identity(tx: str, event_type: str, details: dict | None = None) -> tuple[int, str]:
    return 1, request_fingerprint_sha256_v1(
        transaction_id=tx,
        origin="unity",
        event_type=event_type,
        involved_object="ninguno",
        details=details or {},
    )


def _claim(
    ledger: ActionLedger,
    tx: str,
    event_type: str,
    details: dict | None = None,
):
    version, fingerprint = _identity(tx, event_type, details)
    return ledger.claim_turn_transaction(
        tx,
        origin="unity",
        event_type=event_type,
        request_identity_version=version,
        request_fingerprint_sha256=fingerprint,
    )


def _reconcile(
    ledger: ActionLedger,
    tx: str,
    event_type: str,
    details: dict | None = None,
):
    version, fingerprint = _identity(tx, event_type, details)
    return ledger.reconcile_delivery(
        tx,
        origin="unity",
        event_type=event_type,
        request_identity_version=version,
        request_fingerprint_sha256=fingerprint,
    )


def _record_dispatched_action(ledger: ActionLedger) -> None:
    assert _claim(ledger, "origin-tx", "comando_usuario").status is TransactionClaimStatus.CREATED
    ledger.record_action(
        action_id="action-1",
        transaction_id="origin-tx",
        action_name="girar_avatar",
        parameters={"grados": 90.0},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    ledger.transition_action("action-1", ActionTransitionEvent.AUTO_AUTHORIZE)
    ledger.transition_action("action-1", ActionTransitionEvent.DISPATCH)
    ledger.commit_turn_uow(
        "origin-tx",
        {
            "id_accion": "action-1",
            "nombre": "girar_avatar",
            "parametros": {"grados": 90.0},
            "requiere_confirmacion": False,
        },
    )


def _complete_legacy_action(ledger: ActionLedger, transaction_id: str) -> None:
    claim = ledger.claim_transaction(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
    )
    assert claim.status is TransactionClaimStatus.CREATED
    action_id = f"action-{transaction_id}"
    ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name="girar_avatar",
        parameters={"grados": 45.0},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    completed = ledger.complete_transaction(
        transaction_id,
        {
            "id_accion": action_id,
            "nombre": "girar_avatar",
            "parametros": {"grados": 45.0},
            "requiere_confirmacion": False,
            "estado_ejecucion": "dispatched",
            "id_transaccion_origen": transaction_id,
        },
    )
    assert completed is TransactionCompletionStatus.COMPLETED


def _complete_modern_no_action(
    ledger: ActionLedger,
    transaction_id: str,
    details: dict,
) -> tuple[int, str]:
    version, fingerprint = _identity(transaction_id, "comando_usuario", details)
    claim = ledger.claim_transaction(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
        request_identity_version=version,
        request_fingerprint_sha256=fingerprint,
    )
    assert claim.status is TransactionClaimStatus.CREATED
    assert (
        ledger.complete_transaction(transaction_id, None)
        is TransactionCompletionStatus.COMPLETED
    )
    return version, fingerprint


def test_request_identity_is_strict_and_verified() -> None:
    details = {"mensaje": "hola"}
    version, fingerprint = _identity("tx-1", "comando_usuario", details)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-1",
        detalles=details,
        request_identity_version=version,
        request_fingerprint_sha256=fingerprint,
    )
    assert event.request_fingerprint_sha256 == fingerprint

    with pytest.raises(ValueError):
        EventoEntrante(
            origen="unity",
            tipo_evento="comando_usuario",
            id_transaccion="tx-1",
            detalles=details,
            request_identity_version=True,
            request_fingerprint_sha256=fingerprint,
        )
    with pytest.raises(ValueError):
        EventoEntrante(
            origen="unity",
            tipo_evento="comando_usuario",
            id_transaccion="tx-1",
            detalles={"mensaje": "distinto"},
            request_identity_version=1,
            request_fingerprint_sha256=fingerprint,
        )


def test_claim_identity_relationship_matrix_preserves_only_proven_replay() -> None:
    ledger = ActionLedger(":memory:")
    _complete_legacy_action(ledger, "legacy-matrix")
    for claim in (ledger.claim_transaction, ledger.claim_turn_transaction):
        result = claim(
            "legacy-matrix",
            origin="unity",
            event_type="comando_usuario",
        )
        assert result.status is TransactionClaimStatus.COMPLETED

    original = {"mensaje": "original"}
    version, fingerprint = _complete_modern_no_action(
        ledger,
        "modern-matrix",
        original,
    )
    for claim in (ledger.claim_transaction, ledger.claim_turn_transaction):
        exact = claim(
            "modern-matrix",
            origin="unity",
            event_type="comando_usuario",
            request_identity_version=version,
            request_fingerprint_sha256=fingerprint,
        )
        assert exact.status is TransactionClaimStatus.COMPLETED

        _, changed_fingerprint = _identity(
            "modern-matrix",
            "comando_usuario",
            {"mensaje": "distinto"},
        )
        conflict = claim(
            "modern-matrix",
            origin="unity",
            event_type="comando_usuario",
            request_identity_version=version,
            request_fingerprint_sha256=changed_fingerprint,
        )
        assert conflict.status is TransactionClaimStatus.CONFLICT


def test_completed_legacy_transaction_rejects_v1_replay_without_side_effects() -> None:
    ledger = ActionLedger(":memory:")
    transaction_id = "legacy-to-v1"
    _complete_legacy_action(ledger, transaction_id)
    before = ledger.get_transaction(transaction_id)
    action_count_before = ledger._connection.execute(
        "SELECT COUNT(*) AS count FROM actions"
    ).fetchone()["count"]

    details = {"mensaje": "payload V1 diferente", "solicita_decision": True}
    version, fingerprint = _identity(transaction_id, "comando_usuario", details)
    for claim in (ledger.claim_transaction, ledger.claim_turn_transaction):
        result = claim(
            transaction_id,
            origin="unity",
            event_type="comando_usuario",
            request_identity_version=version,
            request_fingerprint_sha256=fingerprint,
        )
        assert result.status is TransactionClaimStatus.UNVERIFIABLE

    orchestrator, route_calls = _orquestador_aislado("internal")
    orchestrator.action_ledger = ledger
    behavior_calls = 0
    original_behavior = orchestrator.behavior.procesar_evento

    def counted_behavior(*args, **kwargs):
        nonlocal behavior_calls
        behavior_calls += 1
        return original_behavior(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted_behavior
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles=details,
        request_identity_version=version,
        request_fingerprint_sha256=fingerprint,
    )
    with pytest.raises(TurnReconciliationRequiredError):
        orchestrator.procesar_evento(event)

    app = FastAPI()
    app.state.orchestrator = orchestrator
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/evento",
            json={
                "origen": "unity",
                "tipo_evento": "comando_usuario",
                "id_transaccion": transaction_id,
                "objeto_involucrado": "ninguno",
                "detalles": details,
                "request_identity_version": version,
                "request_fingerprint_sha256": fingerprint,
            },
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECONCILIATION_REQUIRED"

    reconciliation = ledger.reconcile_delivery(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
        request_identity_version=version,
        request_fingerprint_sha256=fingerprint,
    )
    assert reconciliation.resolution is DeliveryResolutionV1.UNVERIFIABLE
    assert reconciliation.directive is DeliveryDirectiveV1.REQUIRES_RECONCILIATION
    assert ledger.get_transaction(transaction_id) == before
    assert (
        ledger._connection.execute("SELECT COUNT(*) AS count FROM actions").fetchone()[
            "count"
        ]
        == action_count_before
    )
    assert behavior_calls == 0
    assert all(count == 0 for count in route_calls.values())
    assert orchestrator.memory.experiencias == []
    assert orchestrator.memory.acciones == []
    assert orchestrator.memory.turnos == []


def test_completed_v1_transaction_rejects_legacy_replay_as_unverifiable() -> None:
    ledger = ActionLedger(":memory:")
    transaction_id = "v1-to-legacy"
    details = {"mensaje": "original V1"}
    _complete_modern_no_action(ledger, transaction_id, details)
    before = ledger.get_transaction(transaction_id)

    for claim in (ledger.claim_transaction, ledger.claim_turn_transaction):
        result = claim(
            transaction_id,
            origin="unity",
            event_type="comando_usuario",
        )
        assert result.status is TransactionClaimStatus.UNVERIFIABLE

    orchestrator, _ = _orquestador_aislado("internal")
    orchestrator.action_ledger = ledger
    with pytest.raises(TurnReconciliationRequiredError):
        orchestrator.procesar_evento(
            EventoEntrante(
                origen="unity",
                tipo_evento="comando_usuario",
                id_transaccion=transaction_id,
                detalles=details,
            )
        )
    reconciliation = ledger.reconcile_delivery(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
        request_identity_version=None,
        request_fingerprint_sha256=None,
    )
    assert reconciliation.resolution is DeliveryResolutionV1.UNVERIFIABLE
    assert reconciliation.directive is DeliveryDirectiveV1.REQUIRES_RECONCILIATION
    assert ledger.get_transaction(transaction_id) == before


def test_only_proven_not_found_allows_same_transaction_retry() -> None:
    ledger = ActionLedger(":memory:")
    result = _reconcile(ledger, "missing", "comando_usuario")
    assert result.resolution is DeliveryResolutionV1.NOT_FOUND
    assert result.directive is DeliveryDirectiveV1.RETRY_SAME_TX

    legacy = ledger.reconcile_delivery(
        "also-missing",
        origin="unity",
        event_type="comando_usuario",
        request_identity_version=None,
        request_fingerprint_sha256=None,
    )
    assert legacy.resolution is DeliveryResolutionV1.UNVERIFIABLE
    assert legacy.directive is DeliveryDirectiveV1.REQUIRES_RECONCILIATION


def test_causally_inconsistent_ack_is_rejected_before_consumption() -> None:
    with pytest.raises(ValueError, match="resolution/directive"):
        DeliveryReconciliationResultV1(
            "tx-malformed",
            DeliveryResolutionV1.UNKNOWN,
            DeliveryDirectiveV1.RESOLVED,
            DurableReceiptKindV1.ACTION_LIFECYCLE,
            DurableReceiptStatusV1.APPLIED,
        )


def test_legacy_row_and_identity_mismatch_are_never_not_found() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_turn_transaction("legacy", origin="unity", event_type="comando_usuario")
    result = _reconcile(ledger, "legacy", "comando_usuario")
    assert result.resolution is DeliveryResolutionV1.UNVERIFIABLE

    _claim(ledger, "modern", "comando_usuario")
    mismatch = ledger.reconcile_delivery(
        "modern",
        origin="unity",
        event_type="comando_usuario",
        request_identity_version=1,
        request_fingerprint_sha256="f" * 64,
    )
    assert mismatch.resolution is DeliveryResolutionV1.REQUIRES_RECONCILIATION
    assert mismatch.directive is DeliveryDirectiveV1.REQUIRES_RECONCILIATION


def test_open_and_committed_transaction_use_durable_state_not_http_status() -> None:
    ledger = ActionLedger(":memory:")
    _claim(ledger, "open", "comando_usuario")
    pending = _reconcile(ledger, "open", "comando_usuario")
    assert pending.resolution is DeliveryResolutionV1.IN_PROGRESS
    assert pending.directive is DeliveryDirectiveV1.WAIT

    _claim(ledger, "done", "actualizacion_estado")
    ledger.commit_turn_uow("done", None)
    done = _reconcile(ledger, "done", "actualizacion_estado")
    assert done.resolution is DeliveryResolutionV1.COMPLETED_NO_ACTION
    assert done.receipt_kind is DurableReceiptKindV1.TRANSACTION
    assert done.receipt_status is DurableReceiptStatusV1.COMMITTED


def test_start_execution_requires_durable_lifecycle_receipt() -> None:
    ledger = ActionLedger(":memory:")
    _record_dispatched_action(ledger)
    details = {"id_accion": "action-1", "id_transaccion_origen": "origin-tx"}
    _claim(ledger, "start-tx", "inicio_ejecucion_accion", details)
    ledger.transition_action(
        "action-1",
        ActionTransitionEvent.START_EXECUTION,
        receipt_id="lifecycle:start-tx",
    )
    ledger.commit_turn_uow("start-tx", None)

    result = _reconcile(ledger, "start-tx", "inicio_ejecucion_accion", details)
    assert result.resolution is DeliveryResolutionV1.COMPLETED_NO_ACTION
    assert result.receipt_kind is DurableReceiptKindV1.ACTION_LIFECYCLE
    assert result.receipt_status is DurableReceiptStatusV1.APPLIED
    assert result.directive is DeliveryDirectiveV1.RESOLVED


@pytest.mark.parametrize(
    ("approved", "expected_state"),
    [(True, "dispatched"), (False, "rejected")],
)
def test_confirmation_reconciliation_recovers_durable_action(
    approved: bool,
    expected_state: str,
) -> None:
    ledger = ActionLedger(":memory:")
    assert _claim(ledger, "origin-confirm", "comando_usuario").status is TransactionClaimStatus.CREATED
    ledger.record_action(
        action_id="action-confirm",
        transaction_id="origin-confirm",
        action_name="girar_avatar",
        parameters={"grados": 45.0},
        requires_confirmation=True,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    ledger.transition_action("action-confirm", ActionTransitionEvent.REQUIRE_AUTHORIZATION)
    ledger.commit_turn_uow(
        "origin-confirm",
        {
            "id_accion": "action-confirm",
            "nombre": "girar_avatar",
            "parametros": {"grados": 45.0},
            "requiere_confirmacion": True,
        },
    )

    details = {"id_accion": "action-confirm", "approved": approved}
    _claim(ledger, "confirmation-tx", "confirmacion_accion", details)
    event = (
        ActionTransitionEvent.AUTHORIZE
        if approved
        else ActionTransitionEvent.REJECT
    )
    receipt = ledger.transition_action(
        "action-confirm",
        event,
        receipt_id="lifecycle:confirmation-tx",
    )
    assert receipt.classification.value == "applied"
    if approved:
        ledger.transition_action("action-confirm", ActionTransitionEvent.DISPATCH)
    ledger.commit_turn_uow("confirmation-tx", None)

    result = _reconcile(
        ledger,
        "confirmation-tx",
        "confirmacion_accion",
        details,
    )
    assert result.resolution is DeliveryResolutionV1.COMPLETED_WITH_ACTION
    assert result.directive is DeliveryDirectiveV1.RESOLVED
    assert result.receipt_kind is DurableReceiptKindV1.ACTION_LIFECYCLE
    assert result.receipt_status is DurableReceiptStatusV1.APPLIED
    assert result.action_id == "action-confirm"
    assert ledger.get_action("action-confirm").action_state.value == expected_state


def test_completed_start_without_receipt_requires_reconciliation() -> None:
    ledger = ActionLedger(":memory:")
    details = {"id_accion": "action-1", "id_transaccion_origen": "origin-tx"}
    _claim(ledger, "bad-start", "inicio_ejecucion_accion", details)
    ledger.commit_turn_uow("bad-start", None)
    result = _reconcile(ledger, "bad-start", "inicio_ejecucion_accion", details)
    assert result.resolution is DeliveryResolutionV1.REQUIRES_RECONCILIATION
    assert result.receipt_status is DurableReceiptStatusV1.MISSING


def test_outcome_is_confirmed_by_durable_outcome_receipt() -> None:
    ledger = ActionLedger(":memory:")
    _record_dispatched_action(ledger)
    ledger.transition_action("action-1", ActionTransitionEvent.START_EXECUTION)
    details = {
        "id_accion": "action-1",
        "id_transaccion_origen": "origin-tx",
        "accion": "girar_avatar",
        "exito": True,
    }
    _claim(ledger, "outcome-tx", "resultado_accion", details)
    receipt = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-tx",
        success=True,
        action_name="girar_avatar",
        originating_transaction_id="origin-tx",
        payload={},
    )
    assert receipt.status is OutcomeRecordStatus.ACCEPTED
    ledger.commit_turn_uow("outcome-tx", None)

    result = _reconcile(ledger, "outcome-tx", "resultado_accion", details)
    assert result.resolution is DeliveryResolutionV1.COMPLETED_NO_ACTION
    assert result.receipt_kind is DurableReceiptKindV1.OUTCOME
    assert result.receipt_status is DurableReceiptStatusV1.ACCEPTED


def test_completed_action_reuses_canonical_response_identity() -> None:
    ledger = ActionLedger(":memory:")
    _record_dispatched_action(ledger)
    result = _reconcile(ledger, "origin-tx", "comando_usuario")
    assert result.resolution is DeliveryResolutionV1.COMPLETED_WITH_ACTION
    assert result.directive is DeliveryDirectiveV1.RESOLVED
    assert result.response_action is not None
    assert result.response_action["id_accion"] == "action-1"
