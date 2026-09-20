"""ROOT-022: ownership, rollback y arbitraje de errores de startup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import main


def _lifespan_fakes(monkeypatch, *, fail_at=None, close_fail=()):
    events: list[str] = []

    def acquire(stage, value=None):
        events.append(f"acquire:{stage}")
        if fail_at == stage:
            raise RuntimeError("PRIMARY_CANARY")
        return value

    def resource(stage, **attrs):
        def close():
            events.append(f"close:{stage}")
            if stage in close_fail:
                if isinstance(close_fail, dict):
                    raise close_fail[stage]
                raise RuntimeError("CLEANUP_CANARY")

        return SimpleNamespace(close=close, **attrs)

    monkeypatch.setattr(main, "Settings", lambda: acquire("settings", SimpleNamespace(
        CHROMA_DB_PATH="unused", MEMORY_WRITE_LEDGER_PATH=":memory:",
        ACTION_LEDGER_PATH=":memory:",
    )))
    monkeypatch.setattr(main, "BehaviorCore", lambda: acquire("behavior", object()))
    monkeypatch.setattr(main, "build_static_self_v1", lambda: acquire("static_identity", object()))
    monkeypatch.setattr(main, "IdentitySnapshotProvider", lambda *_: acquire("identity", object()))
    monkeypatch.setattr(main, "ChromaClient", lambda **_: acquire("chroma", resource("chroma")))

    def memory(**kw):
        assert kw["owns_chroma_client"] is False
        return acquire("memory", resource("memory"))

    monkeypatch.setattr(main, "MemoryManager", memory)
    monkeypatch.setattr(main, "describe_ai_backend", lambda _: acquire("backend_description", "fake"))

    async def health():
        events.append("health")
        if fail_at == "health":
            raise RuntimeError("PRIMARY_CANARY")
        if fail_at == "health_cancel":
            raise asyncio.CancelledError()
        return True

    monkeypatch.setattr(main, "build_ai_client", lambda _: acquire(
        "ai", resource("ai", health_check_async=health)
    ))
    monkeypatch.setattr(main, "ReasoningEngine", lambda **_: acquire("reasoning", object()))
    monkeypatch.setattr(main.ToolRegistry, "listar_nombres", lambda: acquire("tool_registry", []))
    monkeypatch.setattr(main, "Orchestrator", lambda **_: acquire("orchestrator", resource("orchestrator")))
    return events


def _run_lifespan(*, body_error=None):
    async def run():
        async with main.lifespan(main.app):
            assert main.app.state.ai_client is not None
            assert main.app.state.orchestrator is not None
            if body_error is not None:
                raise body_error

    return asyncio.run(run())


@pytest.mark.parametrize("stage,closed", [
    ("settings", []), ("behavior", []), ("static_identity", []),
    ("identity", []), ("chroma", []), ("memory", ["chroma"]),
    ("backend_description", ["memory", "chroma"]),
    ("ai", ["memory", "chroma"]),
    ("health", ["ai", "memory", "chroma"]),
    ("reasoning", ["ai", "memory", "chroma"]),
    ("tool_registry", ["ai", "memory", "chroma"]),
    ("orchestrator", ["ai", "memory", "chroma"]),
])
def test_lifespan_startup_failure_rolls_back(monkeypatch, stage, closed):
    events = _lifespan_fakes(monkeypatch, fail_at=stage)
    with pytest.raises(RuntimeError, match="PRIMARY_CANARY"):
        _run_lifespan()
    assert [e.removeprefix("close:") for e in events if e.startswith("close:")] == closed
    assert main.app.state.ai_client is None
    assert main.app.state.orchestrator is None


def test_lifespan_lifo_and_two_generations(monkeypatch):
    events = _lifespan_fakes(monkeypatch)
    for _ in range(2):
        _run_lifespan()
        assert main.app.state.ai_client is None
        assert main.app.state.orchestrator is None
    assert [e for e in events if e.startswith("close:")] == [
        "close:orchestrator", "close:ai", "close:memory", "close:chroma",
    ] * 2


def test_primary_wins_all_cleanup_failures(monkeypatch):
    events = _lifespan_fakes(monkeypatch, close_fail={"orchestrator", "ai", "memory", "chroma"})
    with pytest.raises(ValueError, match="BODY_CANARY"):
        _run_lifespan(body_error=ValueError("BODY_CANARY"))
    assert [e for e in events if e.startswith("close:")] == [
        "close:orchestrator", "close:ai", "close:memory", "close:chroma",
    ]
    assert main.app.state.ai_client is None
    assert main.app.state.orchestrator is None


def test_clean_body_propagates_first_cleanup_failure(monkeypatch):
    events = _lifespan_fakes(monkeypatch, close_fail={"ai", "memory"})
    with pytest.raises(RuntimeError, match="CLEANUP_CANARY"):
        _run_lifespan()
    assert [e for e in events if e.startswith("close:")] == [
        "close:orchestrator", "close:ai", "close:memory", "close:chroma",
    ]
    assert main.app.state.ai_client is None
    assert main.app.state.orchestrator is None


@pytest.mark.parametrize("stage", ["health_cancel", "body"])
def test_cancellation_rolls_back(monkeypatch, stage):
    events = _lifespan_fakes(monkeypatch, fail_at=stage)
    with pytest.raises(asyncio.CancelledError):
        _run_lifespan(body_error=asyncio.CancelledError() if stage == "body" else None)
    expected = ["ai", "memory", "chroma"] if stage == "health_cancel" else [
        "orchestrator", "ai", "memory", "chroma",
    ]
    assert [e.removeprefix("close:") for e in events if e.startswith("close:")] == expected
    assert main.app.state.ai_client is None
    assert main.app.state.orchestrator is None


@pytest.mark.parametrize("control_error", [KeyboardInterrupt, SystemExit])
def test_base_exception_preserved_over_cleanup(monkeypatch, control_error):
    events = _lifespan_fakes(
        monkeypatch, close_fail={"orchestrator": RuntimeError("CLEANUP_CANARY")}
    )
    with pytest.raises(control_error):
        _run_lifespan(body_error=control_error())
    assert [e for e in events if e.startswith("close:")] == [
        "close:orchestrator", "close:ai", "close:memory", "close:chroma",
    ]


def test_control_flow_cleanup_is_not_hidden_and_all_closes_run(monkeypatch):
    events = _lifespan_fakes(
        monkeypatch, close_fail={"orchestrator": KeyboardInterrupt()}
    )
    with pytest.raises(KeyboardInterrupt):
        _run_lifespan()
    assert [e for e in events if e.startswith("close:")] == [
        "close:orchestrator", "close:ai", "close:memory", "close:chroma",
    ]


def test_orchestrator_partial_rollback_and_idempotent_close(monkeypatch):
    import core.orchestrator as module

    events: list[str] = []
    class Identity:
        pass

    monkeypatch.setattr(module, "IdentitySnapshotProvider", Identity)
    def failing_oracle_memory(**_):
        raise RuntimeError("ORACLE_MEMORY_CANARY")
    monkeypatch.setattr(module, "OracleMemory", failing_oracle_memory)
    args = dict(behavior_core=object(), memory_manager=SimpleNamespace(client=SimpleNamespace(db_path="unused")),
                reasoning_engine=SimpleNamespace(ai_client=SimpleNamespace(settings=None)),
                tool_registry=object(), identity_provider=Identity())
    with pytest.raises(RuntimeError, match="ORACLE_MEMORY_CANARY"):
        module.Orchestrator(**args)
    assert events == []

    monkeypatch.setattr(module, "OracleMemory", lambda **_: SimpleNamespace(
        close=lambda: events.append("oracle_close")
    ))
    def failing_client(**_):
        raise RuntimeError("ORACLE_CLIENT_CANARY")
    monkeypatch.setattr(module, "OracleClient", failing_client)
    with pytest.raises(RuntimeError, match="ORACLE_CLIENT_CANARY"):
        module.Orchestrator(**args)
    assert events == ["oracle_close"]

    monkeypatch.setattr(module, "OracleClient", lambda **_: object())
    def failing_ledger(*_):
        raise RuntimeError("ACTION_LEDGER_CANARY")
    monkeypatch.setattr(module, "ActionLedger", failing_ledger)
    with pytest.raises(RuntimeError, match="ACTION_LEDGER_CANARY"):
        module.Orchestrator(**args)
    assert events == ["oracle_close", "oracle_close"]

    monkeypatch.setattr(module, "ActionLedger", lambda *_: SimpleNamespace(
        close=lambda: events.append("ledger_close")
    ))
    orchestrator = module.Orchestrator(**args)
    orchestrator.close()
    orchestrator.close()
    assert events[-2:] == ["ledger_close", "oracle_close"]

    def failing_close():
        events.append("ledger_failed_close")
        raise RuntimeError("LEDGER_CLOSE_CANARY")
    monkeypatch.setattr(module, "ActionLedger", lambda *_: SimpleNamespace(close=failing_close))
    orchestrator = module.Orchestrator(**args)
    with pytest.raises(RuntimeError, match="LEDGER_CLOSE_CANARY"):
        orchestrator.close()
    orchestrator.close()
    assert events[-2:] == ["ledger_failed_close", "oracle_close"]
