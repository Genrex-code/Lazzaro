"""ROOT-022: SQLite, memoria y Chroma liberan adquisiciones parciales."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

import memory.chroma_client as chroma_module
import memory.memory_manager as manager_module
import memory.memory_write_ledger as write_module


class _Connection:
    def __init__(self, fail_at: str):
        self.fail_at = fail_at
        self.closed = 0
        self.row_factory = None

    def execute(self, sql):
        if self.fail_at == "pragma" and sql.startswith("PRAGMA"):
            raise sqlite3.OperationalError("PRAGMA_CANARY")

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("failure", ["pragma", "schema"])
def test_memory_write_ledger_closes_partial_connection(monkeypatch, failure):
    connection = _Connection(failure)
    monkeypatch.setattr(write_module.sqlite3, "connect", lambda *_a, **_kw: connection)
    if failure == "schema":
        monkeypatch.setattr(write_module.MemoryWriteLedger, "_initialize_schema",
                            lambda _self: (_ for _ in ()).throw(sqlite3.OperationalError("SCHEMA_CANARY")))
    ledger = write_module.MemoryWriteLedger(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        ledger.open()
    assert connection.closed == 1
    assert not ledger.is_open


@pytest.mark.parametrize("borrowed", [True, False])
def test_memory_manager_constructor_rolls_back_ledger(monkeypatch, borrowed):
    events: list[str] = []
    chroma = SimpleNamespace(close=lambda: events.append("chroma"))

    class Ledger:
        def __init__(self, *_):
            pass

        def open(self):
            events.append("ledger_open")

        def close(self):
            events.append("ledger_close")

    monkeypatch.setattr(manager_module, "MemoryWriteLedger", Ledger)
    monkeypatch.setattr(manager_module, "MemoryWriteCoordinator", lambda *_:
                        (_ for _ in ()).throw(RuntimeError("COORDINATOR_CANARY")))
    with pytest.raises(RuntimeError, match="COORDINATOR_CANARY"):
        manager_module.MemoryManager(chroma, owns_chroma_client=not borrowed)
    assert events == (["ledger_open", "ledger_close"] if borrowed else
                      ["ledger_open", "ledger_close", "chroma"])


def test_memory_manager_close_attempts_both_once_even_if_first_fails():
    manager = manager_module.MemoryManager.__new__(manager_module.MemoryManager)
    events: list[str] = []
    def fail():
        events.append("ledger")
        raise RuntimeError("CLOSE_CANARY")
    manager.memory_write_coordinator = SimpleNamespace(close=fail)
    manager.client = SimpleNamespace(close=lambda: events.append("chroma"))
    manager._owns_chroma_client = True
    with pytest.raises(RuntimeError, match="CLOSE_CANARY"):
        manager.close()
    manager.close()
    assert events == ["ledger", "chroma"]


def test_memory_manager_borrowed_chroma_is_not_closed():
    manager = manager_module.MemoryManager.__new__(manager_module.MemoryManager)
    events: list[str] = []
    manager.memory_write_coordinator = SimpleNamespace(close=lambda: events.append("ledger"))
    manager.client = SimpleNamespace(close=lambda: events.append("chroma"))
    manager._owns_chroma_client = False
    manager.close()
    manager.close()
    assert events == ["ledger"]


def test_chroma_partial_acquisition_uses_refcount_release(monkeypatch, tmp_path):
    events: list[str] = []
    class Native:
        def __init__(self):
            self._system = SimpleNamespace(stop=lambda: events.append("stop"))

        def get_or_create_collection(self, **_):
            raise RuntimeError("COLLECTION_CANARY")

    native = Native()
    monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", lambda **_: native)
    monkeypatch.setattr(chroma_module.ChromaClient, "_limpiar_cache_sistema_chroma",
                        staticmethod(lambda _: events.append("cache_clear")))
    wrapper = chroma_module.ChromaClient(str(tmp_path))
    assert not wrapper.disponible
    assert wrapper._client is None
    assert events == ["stop", "cache_clear"]
    assert wrapper.db_path not in chroma_module.ChromaClient._refcounts_by_db_path
    wrapper.close()
    assert events == ["stop", "cache_clear"]


def test_chroma_failure_before_handle_releases_registration(monkeypatch, tmp_path):
    monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", lambda **_:
                        (_ for _ in ()).throw(RuntimeError("BEFORE_HANDLE_CANARY")))
    wrapper = chroma_module.ChromaClient(str(tmp_path))
    assert not wrapper.disponible
    assert wrapper.db_path not in chroma_module.ChromaClient._refcounts_by_db_path


def test_memory_write_ledger_connect_failure_has_no_acquired_handle(monkeypatch):
    monkeypatch.setattr(write_module.sqlite3, "connect", lambda *_a, **_kw:
                        (_ for _ in ()).throw(sqlite3.OperationalError("CONNECT_CANARY")))
    ledger = write_module.MemoryWriteLedger(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        ledger.open()
    assert not ledger.is_open


def test_chroma_same_path_last_wrapper_stops_shared_system(monkeypatch, tmp_path):
    events: list[str] = []
    class Native:
        def __init__(self):
            self._system = SimpleNamespace(stop=lambda: events.append("stop"))

        def get_or_create_collection(self, **_):
            return SimpleNamespace(count=lambda: 1)

    native = Native()
    monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", lambda **_: native)
    monkeypatch.setattr(chroma_module.ChromaClient, "_limpiar_cache_sistema_chroma",
                        staticmethod(lambda _: events.append("cache_clear")))
    first = chroma_module.ChromaClient(str(tmp_path))
    second = chroma_module.ChromaClient(str(tmp_path))
    first.close()
    assert events == []
    assert second.contar() == 1
    second.close()
    second.close()
    assert events == ["stop", "cache_clear"]
    assert first.db_path not in chroma_module.ChromaClient._refcounts_by_db_path


def test_chroma_second_wrapper_partial_failure_keeps_first_alive(monkeypatch, tmp_path):
    events: list[str] = []
    collection_calls = 0
    class Native:
        def __init__(self):
            self._system = SimpleNamespace(stop=lambda: events.append("stop"))

        def get_or_create_collection(self, **_):
            nonlocal collection_calls
            collection_calls += 1
            if collection_calls == 2:
                raise RuntimeError("SECOND_COLLECTION_CANARY")
            return SimpleNamespace(count=lambda: 2)

    native = Native()
    monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", lambda **_: native)
    monkeypatch.setattr(chroma_module.ChromaClient, "_limpiar_cache_sistema_chroma",
                        staticmethod(lambda _: events.append("cache_clear")))
    first = chroma_module.ChromaClient(str(tmp_path))
    second = chroma_module.ChromaClient(str(tmp_path))
    assert not second.disponible
    assert first.contar() == 2
    assert events == []
    first.close()
    assert events == ["stop", "cache_clear"]
