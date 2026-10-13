from __future__ import annotations

import sqlite3

import pytest

from core.action_ledger import ActionLedger, ActionRecordStatus


def _record(
    ledger: ActionLedger,
    *,
    decision: str = "authority-a",
    binding: str = "a" * 64,
    reference: dict | None = None,
):
    reference = reference or {
        "version": 1,
        "authority_decision_id": decision,
        "id_accion": "action-a",
        "action_binding_sha256": binding,
    }
    return ledger.record_action(
        action_id="action-a",
        transaction_id="tx-a",
        action_name="navegar_a_objeto",
        parameters={"nombre_objeto": "Caja"},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
        authority_decision_id=decision,
        authority_reference=reference,
        action_binding_sha256=binding,
    )


def test_authority_binding_roundtrips_as_additive_action_evidence() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("tx-a", origin="unity", event_type="comando_usuario")

    result = _record(ledger)
    stored = ledger.get_action("action-a")

    assert result.status is ActionRecordStatus.CREATED
    assert stored is not None
    assert stored.authority_decision_id == "authority-a"
    assert stored.action_binding_sha256 == "a" * 64
    assert stored.authority_reference["id_accion"] == "action-a"
    ledger.close()


def test_same_action_and_same_authority_binding_is_idempotent() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("tx-a", origin="unity", event_type="comando_usuario")

    first = _record(ledger)
    second = _record(ledger)

    assert first.status is ActionRecordStatus.CREATED
    assert second.status is ActionRecordStatus.EXISTING
    ledger.close()


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "authority-b"},
        {"binding": "b" * 64},
        {
            "reference": {
                "version": 1,
                "authority_decision_id": "authority-a",
                "id_accion": "action-a",
                "action_binding_sha256": "a" * 64,
                "target": "different",
            }
        },
    ],
)
def test_same_action_with_different_authority_binding_is_conflict(change: dict) -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("tx-a", origin="unity", event_type="comando_usuario")
    _record(ledger)

    result = _record(ledger, **change)

    assert result.status is ActionRecordStatus.CONFLICT
    assert ledger.get_action("action-a").action_binding_sha256 == "a" * 64
    ledger.close()


@pytest.mark.parametrize(
    "authority_kwargs",
    [
        {"authority_decision_id": "authority-a"},
        {"authority_reference": {"version": 1}},
        {"action_binding_sha256": "a" * 64},
        {
            "authority_decision_id": "authority-a",
            "authority_reference": {"version": 1},
        },
    ],
)
def test_partial_authority_binding_is_rejected(authority_kwargs: dict) -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("tx-a", origin="unity", event_type="comando_usuario")

    with pytest.raises(ValueError, match="supplied together"):
        ledger.record_action(
            action_id="action-a",
            transaction_id="tx-a",
            action_name="navegar_a_objeto",
            parameters={},
            requires_confirmation=False,
            task_instance_id=None,
            intended_client="unity",
            outcome_expected=True,
            decision_valid=True,
            **authority_kwargs,
        )
    ledger.close()


def test_v3_database_is_migrated_additively_without_rebuilding_actions(tmp_path) -> None:
    path = tmp_path / "ledger-v3.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO ledger_meta(key, value) VALUES('schema_version', '3');
        CREATE TABLE actions (
            action_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL UNIQUE,
            action_name TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            requires_confirmation INTEGER NOT NULL,
            task_instance_id TEXT,
            intended_client TEXT NOT NULL,
            outcome_expected INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            decision_valid INTEGER,
            action_state TEXT NOT NULL DEFAULT 'legacy_unknown',
            state_updated_at TEXT
        );
        INSERT INTO actions(
            action_id, transaction_id, action_name, parameters_json,
            requires_confirmation, intended_client, outcome_expected,
            created_at, action_state
        ) VALUES(
            'legacy-action', 'legacy-tx', 'none', '{}', 0, 'unity', 0,
            '2026-01-01T00:00:00+00:00', 'legacy_unknown'
        );
        """
    )
    connection.commit()
    connection.close()

    ledger = ActionLedger(path)
    migrated = ledger.get_action("legacy-action")
    columns = {
        row["name"]
        for row in ledger._connection.execute("PRAGMA table_info(actions)").fetchall()
    }
    version = ledger._connection.execute(
        "SELECT value FROM ledger_meta WHERE key='schema_version'"
    ).fetchone()["value"]

    assert migrated is not None
    assert migrated.authority_decision_id is None
    assert migrated.authority_reference is None
    assert migrated.action_binding_sha256 is None
    assert {
        "authority_decision_id",
        "authority_reference_json",
        "action_binding_sha256",
    }.issubset(columns)
    assert version == "5"
    ledger.close()
