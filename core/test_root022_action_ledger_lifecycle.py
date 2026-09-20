"""ROOT-022: ActionLedger cierra la conexión incluso si falla un paso post-connect."""

from __future__ import annotations

import sqlite3

import pytest

import core.action_ledger as module


@pytest.mark.parametrize("failure", ["pragma", "schema"])
def test_action_ledger_partial_connection_is_closed(monkeypatch, failure):
    class Connection:
        row_factory = None
        closed = 0

        def execute(self, sql):
            if failure == "pragma" and sql.startswith("PRAGMA"):
                raise sqlite3.OperationalError("PRAGMA_CANARY")

        def close(self):
            self.closed += 1

    connection = Connection()
    monkeypatch.setattr(module.sqlite3, "connect", lambda *_a, **_kw: connection)
    if failure == "schema":
        monkeypatch.setattr(module.ActionLedger, "_initialize_schema", lambda _self:
                            (_ for _ in ()).throw(sqlite3.OperationalError("SCHEMA_CANARY")))
    with pytest.raises(module.ActionLedgerError):
        module.ActionLedger(":memory:")
    assert connection.closed == 1


def test_action_ledger_close_idempotent():
    ledger = module.ActionLedger(":memory:")
    ledger.close()
    ledger.close()


def test_action_ledger_connect_failure_has_no_acquired_handle(monkeypatch):
    monkeypatch.setattr(module.sqlite3, "connect", lambda *_a, **_kw:
                        (_ for _ in ()).throw(sqlite3.OperationalError("CONNECT_CANARY")))
    with pytest.raises(module.ActionLedgerError):
        module.ActionLedger(":memory:")
