"""Regresiones ROOT-054 B1 para replay semántico de resultado_accion."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import router
from core.action_ledger import (
    ActionLedger,
    ActionRecordStatus,
    ActionTransitionEvent,
    OutcomeRecordStatus,
    OutcomeReplayStatus,
    TransactionClaimStatus,
    TransactionCompletionStatus,
)
from terminal_client.test_chat import _orquestador_aislado


def _client(orchestrator) -> TestClient:
    app = FastAPI()
    app.state.orchestrator = orchestrator
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _runtime():
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    return orchestrator, ledger, _client(orchestrator)


def _record_action(
    ledger: ActionLedger,
    *,
    action_id: str = "action-a",
    transaction_id: str = "command-a",
    action_name: str = "navegar_a_objeto",
) -> None:
    claim = ledger.claim_transaction(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
    )
    assert claim.status is TransactionClaimStatus.CREATED
    recorded = ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name=action_name,
        parameters={"nombre_objeto": "Cube"},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
    )
    assert recorded.status is ActionRecordStatus.CREATED
    completed = ledger.complete_transaction(
        transaction_id,
        {
            "id_accion": action_id,
            "nombre": action_name,
            "parametros": {"nombre_objeto": "Cube"},
            "requiere_confirmacion": False,
        },
    )
    assert completed is TransactionCompletionStatus.COMPLETED


def _record_executing_material_action(ledger: ActionLedger) -> None:
    claim = ledger.claim_transaction(
        "material-command",
        origin="unity",
        event_type="comando_usuario",
    )
    assert claim.status is TransactionClaimStatus.CREATED
    recorded = ledger.record_action(
        action_id="material-action",
        transaction_id="material-command",
        action_name="agarrar_objeto",
        parameters={"nombre_objeto": "Cube"},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    assert recorded.status is ActionRecordStatus.CREATED
    ledger.transition_action("material-action", ActionTransitionEvent.AUTO_AUTHORIZE)
    ledger.transition_action("material-action", ActionTransitionEvent.DISPATCH)
    ledger.transition_action("material-action", ActionTransitionEvent.START_EXECUTION)
    assert (
        ledger.complete_transaction("material-command", None)
        is TransactionCompletionStatus.COMPLETED
    )


def _outcome_payload(
    transaction_id: str,
    action_id: str,
    *,
    success: bool,
    action_name: str | None = "navegar_a_objeto",
    originating_transaction_id: str | None = "command-a",
    message: str = "resultado original",
    extra: str | None = None,
    timestamp: str = "2026-10-12T10:00:00Z",
) -> dict:
    details = {
        "id_accion": action_id,
        "exito": success,
        "mensaje_resultado": message,
    }
    if action_name is not None:
        details["accion"] = action_name
    if originating_transaction_id is not None:
        details["id_transaccion_origen"] = originating_transaction_id
    if extra is not None:
        details["detalle_no_canonico"] = extra
    return {
        "origen": "unity",
        "tipo_evento": "resultado_accion",
        "id_transaccion": transaction_id,
        "timestamp": timestamp,
        "detalles": details,
    }


def _error_code(response) -> str | None:
    body = response.json()
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code") if isinstance(error, dict) else None


def _table_counts(ledger: ActionLedger) -> dict[str, int]:
    with ledger._lock:
        return {
            table: int(
                ledger._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in (
                "transactions",
                "turn_uows",
                "turn_effects",
                "actions",
                "outcomes",
                "outcome_attempts",
                "action_transitions",
                "transition_receipts",
            )
        }


def test_validator_is_read_only_and_reuses_record_outcome_fingerprint() -> None:
    ledger = ActionLedger(":memory:")
    _record_action(ledger)
    accepted = ledger.record_outcome(
        action_id="action-a",
        outcome_transaction_id="outcome-a",
        success=True,
        action_name="navegar_a_objeto",
        originating_transaction_id="command-a",
        payload={"mensaje_resultado": "ok", "target": "Cube"},
    )
    assert accepted.status is OutcomeRecordStatus.ACCEPTED
    before = _table_counts(ledger)
    total_changes = ledger._connection.total_changes

    match = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="outcome-a",
        success=True,
        action_name=None,
        originating_transaction_id=None,
        payload={"target": "Cube", "mensaje_resultado": "ok"},
    )
    changed_success = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="outcome-a",
        success=False,
        action_name="navegar_a_objeto",
        originating_transaction_id="command-a",
        payload={"mensaje_resultado": "ok", "target": "Cube"},
    )
    changed_origin = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="outcome-a",
        success=True,
        action_name="navegar_a_objeto",
        originating_transaction_id="command-b",
        payload={"mensaje_resultado": "ok", "target": "Cube"},
    )
    missing = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="missing-outcome",
        success=True,
        action_name="navegar_a_objeto",
        payload={},
    )

    assert match.status is OutcomeReplayStatus.MATCH
    assert match.prior_classification is OutcomeRecordStatus.ACCEPTED
    assert changed_success.status is OutcomeReplayStatus.CONFLICT
    assert changed_origin.status is OutcomeReplayStatus.CONFLICT
    assert missing.status is OutcomeReplayStatus.MISSING_RECEIPT
    assert _table_counts(ledger) == before
    assert ledger._connection.total_changes == total_changes
    ledger.close()


def test_legacy_accepted_fingerprint_remains_replay_compatible() -> None:
    ledger = ActionLedger(":memory:")
    _record_action(ledger)
    accepted = ledger.record_outcome(
        action_id="action-a",
        outcome_transaction_id="legacy-receipt",
        success=True,
        action_name="navegar_a_objeto",
        originating_transaction_id="command-a",
        payload={"mensaje_resultado": "ok"},
    )
    assert accepted.status is OutcomeRecordStatus.ACCEPTED
    legacy_fingerprint = ledger._outcome_fingerprint(
        True,
        "navegar_a_objeto",
        '{"mensaje_resultado":"ok"}',
    )
    with ledger._lock:
        ledger._connection.execute(
            "UPDATE outcome_attempts SET payload_fingerprint = ? "
            "WHERE outcome_transaction_id = ?",
            (legacy_fingerprint, "legacy-receipt"),
        )
        ledger._connection.execute(
            "UPDATE outcomes SET payload_fingerprint = ? WHERE action_id = ?",
            (legacy_fingerprint, "action-a"),
        )

    omitted_origin = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="legacy-receipt",
        success=True,
        action_name=None,
        originating_transaction_id=None,
        payload={"mensaje_resultado": "ok"},
    )
    canonical_origin = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="legacy-receipt",
        success=True,
        action_name="navegar_a_objeto",
        originating_transaction_id="command-a",
        payload={"mensaje_resultado": "ok"},
    )
    changed_origin = ledger.validate_outcome_replay(
        action_id="action-a",
        outcome_transaction_id="legacy-receipt",
        success=True,
        action_name="navegar_a_objeto",
        originating_transaction_id="wrong-origin",
        payload={"mensaje_resultado": "ok"},
    )

    assert omitted_origin.status is OutcomeReplayStatus.MATCH
    assert canonical_origin.status is OutcomeReplayStatus.MATCH
    assert changed_origin.status is OutcomeReplayStatus.CONFLICT
    ledger.close()


def test_api_exact_replay_is_success_and_conflicting_content_is_409() -> None:
    orchestrator, ledger, client = _runtime()
    _record_action(ledger, action_id="action-a", transaction_id="command-a")
    _record_action(ledger, action_id="action-b", transaction_id="command-b")
    first_payload = _outcome_payload("receipt-a", "action-a", success=True)

    first = client.post("/api/v1/evento", json=first_payload)
    exact = client.post("/api/v1/evento", json=first_payload)
    changed_success = client.post(
        "/api/v1/evento",
        json=_outcome_payload("receipt-a", "action-a", success=False),
    )
    changed_action = client.post(
        "/api/v1/evento",
        json=_outcome_payload(
            "receipt-a",
            "action-b",
            success=False,
            originating_transaction_id="command-b",
        ),
    )

    assert first.status_code == exact.status_code == 200
    assert first.json()["status"] == exact.json()["status"] == "ok"
    assert changed_success.status_code == changed_action.status_code == 409
    assert _error_code(changed_success) == "TRANSACTION_CONFLICT"
    assert _error_code(changed_action) == "TRANSACTION_CONFLICT"
    canonical_a = ledger.get_outcome("action-a")
    assert canonical_a is not None and canonical_a.success is True
    assert ledger.get_outcome("action-b") is None
    assert ledger.get_outcome_attempt("receipt-a").action_id == "action-a"
    client.close()
    ledger.close()


def test_replay_uses_canonical_payload_not_raw_json_or_timestamp() -> None:
    _, ledger, client = _runtime()
    _record_action(ledger)
    original = _outcome_payload(
        "canonical-receipt",
        "action-a",
        success=True,
        extra="primero",
    )
    assert client.post("/api/v1/evento", json=original).status_code == 200

    reordered = deepcopy(original)
    reordered["detalles"] = dict(reversed(list(reordered["detalles"].items())))
    reordered["timestamp"] = "2026-10-12T12:34:56Z"
    reordered["detalles"]["detalle_no_canonico"] = "cambio permitido"
    assert client.post("/api/v1/evento", json=reordered).status_code == 200

    omitted_name = deepcopy(original)
    omitted_name["detalles"].pop("accion")
    assert client.post("/api/v1/evento", json=omitted_name).status_code == 200
    omitted_origin = deepcopy(original)
    omitted_origin["detalles"].pop("id_transaccion_origen")
    assert client.post("/api/v1/evento", json=omitted_origin).status_code == 200

    changed_payload = deepcopy(original)
    changed_payload["detalles"]["mensaje_resultado"] = "resultado distinto"
    changed_name = deepcopy(original)
    changed_name["detalles"]["accion"] = "girar_avatar"
    changed_origin = deepcopy(original)
    changed_origin["detalles"]["id_transaccion_origen"] = "command-other"
    for payload in (changed_payload, changed_name, changed_origin):
        response = client.post("/api/v1/evento", json=payload)
        assert response.status_code == 409
        assert _error_code(response) == "TRANSACTION_CONFLICT"

    stored = ledger.get_outcome("action-a")
    assert stored is not None
    assert stored.payload == {"mensaje_resultado": "resultado original"}
    client.close()
    ledger.close()


def test_unknown_action_receipt_replays_exactly_and_never_creates_phantom() -> None:
    _, ledger, client = _runtime()
    original = _outcome_payload(
        "unknown-receipt",
        "unknown-action",
        success=False,
        action_name=None,
        originating_transaction_id=None,
    )
    exact = deepcopy(original)
    changed_action = deepcopy(original)
    changed_action["detalles"]["id_accion"] = "other-action"
    changed_success = deepcopy(original)
    changed_success["detalles"]["exito"] = True

    first = client.post("/api/v1/evento", json=original)
    retry = client.post("/api/v1/evento", json=exact)
    action_conflict = client.post("/api/v1/evento", json=changed_action)
    success_conflict = client.post("/api/v1/evento", json=changed_success)

    assert first.status_code == retry.status_code == 200
    assert action_conflict.status_code == success_conflict.status_code == 409
    assert _error_code(action_conflict) == _error_code(success_conflict) == "TRANSACTION_CONFLICT"
    attempt = ledger.get_outcome_attempt("unknown-receipt")
    assert attempt is not None and attempt.classification is OutcomeRecordStatus.UNKNOWN
    assert ledger.get_action("unknown-action") is None
    assert ledger.get_action("other-action") is None
    assert ledger.get_outcome("unknown-action") is None
    assert ledger.get_outcome("other-action") is None
    client.close()
    ledger.close()


def test_rejected_origin_mismatch_replays_only_the_same_rejected_receipt() -> None:
    _, ledger, client = _runtime()
    _record_action(ledger)
    original = _outcome_payload(
        "origin-mismatch-receipt",
        "action-a",
        success=True,
        originating_transaction_id="wrong-origin-a",
    )
    changed_origin = deepcopy(original)
    changed_origin["detalles"]["id_transaccion_origen"] = "wrong-origin-b"

    first = client.post("/api/v1/evento", json=original)
    exact = client.post("/api/v1/evento", json=original)
    changed = client.post("/api/v1/evento", json=changed_origin)

    assert first.status_code == exact.status_code == 200
    assert changed.status_code == 409
    assert _error_code(changed) == "TRANSACTION_CONFLICT"
    attempt = ledger.get_outcome_attempt("origin-mismatch-receipt")
    assert attempt is not None and attempt.classification is OutcomeRecordStatus.MISMATCH
    assert ledger.get_outcome("action-a") is None
    client.close()
    ledger.close()


def test_completed_outcome_without_receipt_requires_reconciliation() -> None:
    _, ledger, client = _runtime()
    claim = ledger.claim_turn_transaction(
        "missing-receipt",
        origin="unity",
        event_type="resultado_accion",
    )
    assert claim.status is TransactionClaimStatus.CREATED
    assert ledger.commit_turn_uow("missing-receipt", None) is TransactionCompletionStatus.COMPLETED

    response = client.post(
        "/api/v1/evento",
        json=_outcome_payload(
            "missing-receipt",
            "unknown-action",
            success=False,
            action_name=None,
            originating_transaction_id=None,
        ),
    )
    assert response.status_code == 409
    assert response.json()["status"] == "pendiente"
    assert _error_code(response) == "RECONCILIATION_REQUIRED"
    assert ledger.get_outcome_attempt("missing-receipt") is None
    client.close()
    ledger.close()


def test_material_outcome_replay_preserves_the_canonical_projection() -> None:
    _, ledger, client = _runtime()
    _record_executing_material_action(ledger)
    receipt = {
        "action_id": "material-action",
        "effect_kind": "grab",
        "status": "applied",
        "object_id": "Cube",
        "evidence": {
            "held_before": None,
            "held_after": "Cube",
            "held_reference_changed": True,
            "parent_is_hold_point": True,
        },
    }
    payload = _outcome_payload(
        "material-receipt",
        "material-action",
        success=True,
        action_name="agarrar_objeto",
        originating_transaction_id="material-command",
    )
    payload["detalles"]["material_attestation"] = receipt

    first = client.post("/api/v1/evento", json=payload)
    stored_first = ledger.get_outcome("material-action")
    exact = client.post("/api/v1/evento", json=payload)
    stored_retry = ledger.get_outcome("material-action")
    changed = deepcopy(payload)
    changed["detalles"]["material_attestation"]["object_id"] = "Other"
    conflict = client.post("/api/v1/evento", json=changed)

    assert first.status_code == exact.status_code == 200
    assert conflict.status_code == 409
    assert _error_code(conflict) == "TRANSACTION_CONFLICT"
    assert stored_first is not None and stored_retry == stored_first
    assert stored_retry.payload["material_attestation"] == receipt
    client.close()
    ledger.close()


def test_exact_replay_has_zero_duplicate_consumers(monkeypatch) -> None:
    orchestrator, ledger, client = _runtime()
    _record_action(ledger)
    counters = {
        "behavior": 0,
        "memory": 0,
        "identity": 0,
        "alias": 0,
        "consume": 0,
    }

    original_behavior = orchestrator.behavior.procesar_evento
    original_memory = orchestrator.memory.agregar_accion
    original_identity = orchestrator.behavior.registrar_impacto_identidad
    original_alias = orchestrator._registrar_alias_aprendido_si_corresponde
    original_consume = ledger.mark_outcome_consumed

    def counted_behavior(*args, **kwargs):
        counters["behavior"] += 1
        return original_behavior(*args, **kwargs)

    def counted_memory(*args, **kwargs):
        counters["memory"] += 1
        return original_memory(*args, **kwargs)

    def counted_identity(*args, **kwargs):
        counters["identity"] += 1
        return original_identity(*args, **kwargs)

    def counted_alias(*args, **kwargs):
        counters["alias"] += 1
        return original_alias(*args, **kwargs)

    def counted_consume(*args, **kwargs):
        counters["consume"] += 1
        return original_consume(*args, **kwargs)

    monkeypatch.setattr(orchestrator.behavior, "procesar_evento", counted_behavior)
    monkeypatch.setattr(orchestrator.memory, "agregar_accion", counted_memory)
    monkeypatch.setattr(
        orchestrator.behavior,
        "registrar_impacto_identidad",
        counted_identity,
    )
    monkeypatch.setattr(
        orchestrator,
        "_registrar_alias_aprendido_si_corresponde",
        counted_alias,
    )
    monkeypatch.setattr(ledger, "mark_outcome_consumed", counted_consume)

    payload = _outcome_payload("consumer-receipt", "action-a", success=True)
    assert client.post("/api/v1/evento", json=payload).status_code == 200
    after_first = dict(counters)
    stored_first = ledger.get_outcome("action-a")
    assert stored_first is not None and stored_first.consumed_at is not None
    assert client.post("/api/v1/evento", json=payload).status_code == 200
    stored_retry = ledger.get_outcome("action-a")

    assert counters == after_first
    assert after_first["behavior"] == 1
    assert after_first["memory"] == 1
    assert after_first["identity"] == 1
    assert after_first["alias"] == 1
    assert after_first["consume"] == 1
    assert stored_retry == stored_first
    client.close()
    ledger.close()


def test_concurrent_identical_and_conflicting_reuse_are_serialized() -> None:
    orchestrator, ledger, client = _runtime()
    _record_action(ledger)
    behavior_calls = 0
    behavior_lock = __import__("threading").Lock()
    original_behavior = orchestrator.behavior.procesar_evento

    def counted_behavior(*args, **kwargs):
        nonlocal behavior_calls
        with behavior_lock:
            behavior_calls += 1
        return original_behavior(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted_behavior
    identical = _outcome_payload("concurrent-identical", "action-a", success=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: client.post("/api/v1/evento", json=identical),
                range(2),
            )
        )
    assert [response.status_code for response in responses] == [200, 200]
    assert behavior_calls == 1
    assert ledger.get_outcome_attempt("concurrent-identical") is not None

    second_orchestrator, second_ledger, second_client = _runtime()
    _record_action(second_ledger)
    true_payload = _outcome_payload("concurrent-conflict", "action-a", success=True)
    false_payload = _outcome_payload("concurrent-conflict", "action-a", success=False)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(second_client.post, "/api/v1/evento", json=payload)
            for payload in (true_payload, false_payload)
        ]
        conflicting = [future.result() for future in futures]
    statuses = sorted(response.status_code for response in conflicting)
    assert statuses == [200, 409]
    assert sum(_error_code(response) == "TRANSACTION_CONFLICT" for response in conflicting) == 1
    assert second_ledger.get_outcome_attempt("concurrent-conflict") is not None
    assert second_ledger.get_outcome("action-a") is not None

    client.close()
    ledger.close()
    second_client.close()
    second_ledger.close()
