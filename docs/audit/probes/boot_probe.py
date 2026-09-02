"""BOOT/lifecycle evidence without changing application behavior on disk.

Run with the repository's interpreter. Faults and providers are fakes; the HTTP
smoke uses the real FastAPI composition and Chroma only in a temporary directory.
No cognitive events, provider requests, active database writes or AuditTrace
events are generated. PASS means the stated current behavior was reproduced,
including known defects; it does NOT mean that every lifecycle is correct.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import gc
import importlib.util
import io
import json
import logging
from pathlib import Path
import socket
import sys
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
# Avoid the production logger's import-time FileHandler in an audit process.
logging.getLogger().handlers = [logging.NullHandler()]


class BootFailure(RuntimeError):
    pass


class CleanupFailure(RuntimeError):
    pass


class BodyFailure(RuntimeError):
    pass


class Resource:
    def __init__(self, name, harness, dependency=None):
        self.name = name
        self.harness = harness
        self.dependency = dependency
        self.close_calls = 0
        self.closed = False

    def close(self):
        self.close_calls += 1
        self.harness.sequence.append("close:" + self.name)
        if self.harness.close_failure == self.name:
            raise CleanupFailure("synthetic close failure")
        if self.dependency is not None:
            self.dependency.close()
        self.closed = True


class Harness:
    """Patch composition boundaries, never the body of main.lifespan."""

    def __init__(self, entry, fail_at=None, health=True, close_failure=None,
                 chroma_available=True, empty_tools=False):
        self.entry = entry
        self.fail_at = fail_at
        self.health = health
        self.close_failure = close_failure
        self.chroma_available = chroma_available
        self.empty_tools = empty_tools
        self.sequence = []
        self.resources = {}
        self.error = BootFailure("AUDIT_SYNTHETIC_CANARY_NOT_A_REAL_SECRET")
        self.logger = MagicMock()
        self.settings = SimpleNamespace(CHROMA_DB_PATH="audit-fake-only")
        self.ready = False
        self.caught = None
        self.state = None

    def step(self, name):
        self.sequence.append(name)
        if self.fail_at == name:
            raise self.error

    def resource(self, name, dependency=None):
        item = Resource(name, self, dependency)
        self.resources[name] = item
        return item

    def make_settings(self):
        self.step("Settings")
        return self.settings

    def make_behavior(self):
        self.step("BehaviorCore")
        return SimpleNamespace(state_engine=object())

    def make_chroma(self, db_path):
        self.step("ChromaClient")
        client = self.resource("chroma")
        client.db_path = db_path
        client.disponible = self.chroma_available
        return client

    def make_memory(self, chroma_client):
        self.step("MemoryManager")
        memory = self.resource("memory", chroma_client)
        memory.client = chroma_client
        return memory

    def describe(self, settings):
        assert settings is self.settings
        self.step("describe_ai_backend")
        return "audit-fake"

    def make_ai(self, settings):
        assert settings is self.settings
        self.step("build_ai_client")
        ai = self.resource("ai")
        ai.settings = settings

        def health_check():
            self.step("health_check")
            return self.health

        ai.health_check = health_check
        return ai

    def make_reasoning(self, ai_client):
        self.step("ReasoningEngine")
        assert ai_client is self.resources["ai"]
        return SimpleNamespace(ai_client=ai_client, policy_manager=object())

    def list_tools(self):
        self.step("ToolRegistry.listar_nombres")
        return [] if self.empty_tools else ["audit_tool"]

    def make_orchestrator(self, **kwargs):
        self.step("Orchestrator")
        assert kwargs["memory_manager"] is self.resources["memory"]
        assert kwargs["reasoning_engine"].ai_client is self.resources["ai"]
        return self.resource("orchestrator")

    def patches(self, stack, orchestrator=None):
        replacements = {
            "logger": self.logger, "Settings": self.make_settings,
            "BehaviorCore": self.make_behavior, "ChromaClient": self.make_chroma,
            "MemoryManager": self.make_memory, "describe_ai_backend": self.describe,
            "build_ai_client": self.make_ai, "ReasoningEngine": self.make_reasoning,
            "ToolRegistry": SimpleNamespace(listar_nombres=self.list_tools),
            "Orchestrator": orchestrator or self.make_orchestrator,
        }
        for name, value in replacements.items():
            stack.enter_context(patch.object(self.entry, name, value))

    async def execute(self, body_error=None):
        app = self.entry.create_app()
        try:
            async with self.entry.lifespan(app):
                self.ready = True
                if body_error is not None:
                    raise body_error
        except BaseException as error:
            if not isinstance(error, (Exception, asyncio.CancelledError)):
                raise
            self.caught = error
        self.state = app.state

    def run(self, body_error=None):
        with ExitStack() as stack:
            self.patches(stack)
            asyncio.run(self.execute(body_error))
        return self.evidence()

    def evidence(self):
        return {
            "sequence": self.sequence,
            "ready_reached": self.ready,
            "exception_type": type(self.caught).__name__ if self.caught else None,
            "original_startup_error_preserved": self.caught is self.error,
            "close_calls": {name: item.close_calls for name, item in self.resources.items()},
            "state_has_ai": self.state.ai_client is not None,
            "state_has_orchestrator": self.state.orchestrator is not None,
            "nominal_log": any("Sistemas nominales" in str(call.args[0])
                               for call in self.logger.info.call_args_list),
            "startup_error_logged": self.logger.error.called,
            "warning_logged": self.logger.warning.called,
        }


def clean_fake_lifecycle(entry):
    harness = Harness(entry)
    evidence = harness.run()
    assert harness.ready and harness.caught is None
    assert evidence["sequence"] == [
        "Settings", "BehaviorCore", "ChromaClient", "MemoryManager", "describe_ai_backend",
        "build_ai_client", "health_check", "ReasoningEngine", "ToolRegistry.listar_nombres",
        "Orchestrator", "close:orchestrator", "close:memory", "close:chroma", "close:ai",
    ]
    assert all(item.close_calls == 1 for item in harness.resources.values())
    assert harness.state.ai_client.closed and harness.state.orchestrator.closed
    evidence["state_still_contains_closed_resources"] = True
    return evidence


def startup_failure_matrix(entry):
    rows = []
    stages = (
        "Settings", "BehaviorCore", "ChromaClient", "MemoryManager", "describe_ai_backend",
        "build_ai_client", "health_check", "ReasoningEngine", "ToolRegistry.listar_nombres", "Orchestrator",
    )
    for stage in stages:
        harness = Harness(entry, fail_at=stage)
        evidence = harness.run()
        assert not harness.ready and harness.caught is harness.error
        assert not evidence["nominal_log"] and not evidence["state_has_orchestrator"]
        assert evidence["startup_error_logged"] == (stage != "Settings")
        if stage == "MemoryManager":
            assert harness.resources["chroma"].close_calls == 0
        elif "memory" in harness.resources:
            assert harness.resources["memory"].close_calls == 1
            assert harness.resources["chroma"].close_calls == 1
        if "ai" in harness.resources:
            assert harness.resources["ai"].close_calls == 1
            assert harness.state.ai_client.closed
        evidence["injected_stage"] = stage
        rows.append(evidence)
    return {"cases": rows}


def degraded_but_nominal(entry):
    rows = []
    for name, arguments in (
        ("llm_health_false", {"health": False}),
        ("chroma_unavailable", {"chroma_available": False}),
        ("empty_registry", {"empty_tools": True}),
    ):
        harness = Harness(entry, **arguments)
        evidence = harness.run()
        assert harness.ready and harness.caught is None and evidence["nominal_log"]
        assert evidence["warning_logged"] == (name == "llm_health_false")
        evidence["condition"] = name
        rows.append(evidence)
    return {"cases": rows}


def cleanup_failure_matrix(entry):
    rows = []
    for close_failure in ("memory", "ai"):
        harness = Harness(entry, fail_at="ReasoningEngine", close_failure=close_failure)
        evidence = harness.run()
        assert isinstance(harness.caught, CleanupFailure) and harness.caught is not harness.error
        if close_failure == "memory":
            assert harness.resources["ai"].close_calls == 0
        evidence["context"] = "startup_rollback"
        evidence["injected_close"] = close_failure
        rows.append(evidence)
    for close_failure in ("orchestrator", "memory", "ai"):
        harness = Harness(entry, close_failure=close_failure)
        evidence = harness.run()
        assert harness.ready and isinstance(harness.caught, CleanupFailure)
        if close_failure == "orchestrator":
            assert harness.resources["memory"].close_calls == 0
            assert harness.resources["ai"].close_calls == 0
        if close_failure == "memory":
            assert harness.resources["ai"].close_calls == 0
        evidence["context"] = "shutdown"
        evidence["injected_close"] = close_failure
        rows.append(evidence)
    return {"cases": rows}


def exceptional_lifespan_exit(entry):
    rows = []
    for error in (BodyFailure("synthetic body failure"), asyncio.CancelledError()):
        harness = Harness(entry)
        evidence = harness.run(body_error=error)
        assert harness.ready and harness.caught is error
        assert all(item.close_calls == 0 for item in harness.resources.values())
        rows.append(evidence)
    return {"cases": rows, "scope": "exception/cancellation injected through lifespan context, not a request 500"}


def partially_constructed_orchestrator(entry):
    from core import orchestrator as module

    harness = Harness(entry)
    cache = harness.resource("oracle_cache")
    with ExitStack() as stack:
        harness.patches(stack, orchestrator=module.Orchestrator)
        # Execute the real constructor, replacing only its child constructors.
        for name in (
            "TriageRouter", "ToolPolicy", "MotorEngine", "IdentityState",
            "SubjectiveEffortCalculator", "PersonaEngine", "LLMGenerationAdapter", "OracleEngine",
        ):
            stack.enter_context(patch.object(module, name, return_value=SimpleNamespace()))
        cache_constructor = stack.enter_context(patch.object(module, "OracleMemory", return_value=cache))
        stack.enter_context(patch.object(module, "OracleClient", side_effect=harness.error))
        asyncio.run(harness.execute())
    assert cache_constructor.call_count == 1 and cache.close_calls == 0
    assert harness.caught is harness.error and not harness.ready
    assert harness.resources["memory"].close_calls == harness.resources["ai"].close_calls == 1
    return harness.evidence()


def chroma_partial_construction(entry):
    from memory import chroma_client as module

    native = MagicMock()
    native.get_or_create_collection.side_effect = RuntimeError("synthetic collection failure")
    with TemporaryDirectory(prefix="lazzaro-boot-chroma-fault-") as temporary:
        with patch.object(module.chromadb, "PersistentClient", return_value=native):
            with patch.object(module.ChromaClient, "_limpiar_cache_sistema_chroma"):
                wrapper = module.ChromaClient(db_path=temporary)
                assert not wrapper.disponible and wrapper._client is None
                wrapper.close()
                wrapper.close()
    assert native.get_or_create_collection.call_count == 1 and native.close.call_count == 0
    return {"native_constructed": True, "collection_failed": True, "wrapper_available": False,
            "native_close_calls_after_two_wrapper_closes": 0}


def logger_import_and_existing_handler(entry):
    import core.logger as module

    spec = importlib.util.spec_from_file_location("audit_isolated_logger", ROOT / "core" / "logger.py")
    isolated = importlib.util.module_from_spec(spec)
    with patch.object(logging, "getLogger", return_value=SimpleNamespace(handlers=[])):
        with patch.object(Path, "mkdir"), patch.object(logging, "FileHandler", side_effect=PermissionError):
            try:
                spec.loader.exec_module(isolated)
            except PermissionError:
                import_failed = True
            else:
                import_failed = False
    with patch.object(logging, "getLogger", return_value=SimpleNamespace(handlers=[object()])):
        with patch.object(logging, "FileHandler") as file_handler:
            module.configurar_logger_root()
    assert import_failed and file_handler.call_count == 0
    return {"unwritable_human_log_aborts_module_import": True,
            "existing_root_handler_skips_application_file_handler": True,
            "actual_logfile_touched": False}


def startup_log_canary(entry):
    harness = Harness(entry, fail_at="build_ai_client")
    harness.run()
    assert any(harness.error in call.args for call in harness.logger.error.call_args_list)
    assert harness.logger.error.call_args.kwargs.get("exc_info") is True
    return {"synthetic_only": True, "unredacted_exception_passed_to_human_logger": True,
            "traceback_requested": True, "real_credential_disclosure_tested": False}


class OfflineAI:
    provider_name = "audit-fake"
    model_name = "audit-no-generation"

    def __init__(self, settings, health=True):
        self.settings = settings
        self.health = health
        self.close_calls = 0
        self.health_calls = 0
        self.generate_calls = 0

    def health_check(self):
        self.health_calls += 1
        return self.health

    def generate(self, *args, **kwargs):
        self.generate_calls += 1
        raise AssertionError("generation forbidden in BOOT probe")

    def close(self):
        self.close_calls += 1


def real_app_smoke(entry):
    """Real composition and temporary Chroma; only the provider is simulated."""
    import os
    from fastapi.testclient import TestClient
    from config.settings import Settings

    rows = []
    for healthy in (True, False):
        with TemporaryDirectory(prefix="lazzaro-boot-smoke-") as temporary:
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings(_env_file=None, CHROMA_DB_PATH=temporary, AI_FALLBACK_ORDER="gemini",
                                    LLM_FALLBACKS_ENABLED=False)
            ai = OfflineAI(settings, healthy)
            with patch.object(entry, "Settings", return_value=settings), patch.object(entry, "build_ai_client", return_value=ai):
                app = entry.create_app()
                # Without the context, TestClient intentionally has not run startup.
                before = TestClient(app)
                try:
                    before_startup = before.get("/api/v1/estado").status_code
                finally:
                    before.close()
                assert before_startup == 503
                with TestClient(app) as client:
                    orchestrator = app.state.orchestrator
                    memory = orchestrator.memory
                    oracle = orchestrator.oracle_memory
                    assert memory.client.disponible and oracle.disponible
                    assert orchestrator.reasoning.ai_client is app.state.ai_client is ai
                    assert orchestrator.llm_adapter.ai_client is ai
                    assert orchestrator.identity_state.state_engine is orchestrator.behavior.state_engine
                    assert memory.episodic_store.client is memory.client
                    assert memory.semantic_store.client is memory.client
                    assert oracle.client is not memory.client
                    assert oracle.client.db_path == memory.client.db_path
                    assert not orchestrator.oracle_client.configurado
                    response = client.get("/api/v1/estado")
                    assert response.status_code == 200
                    assert "health" not in " ".join(route.path for route in app.routes).lower()
                assert memory.client._closed and oracle.client._closed and ai.close_calls == 1
                assert not memory.client.disponible and not oracle.disponible
                assert ai.health_calls == 1 and ai.generate_calls == 0
                # No lifespan rerun: the stale state is still visible to a local ASGI request.
                client = TestClient(app)
                try:
                    after_shutdown = client.get("/api/v1/estado").status_code
                finally:
                    client.close()
                assert after_shutdown == 200
                rows.append({"health_result": healthy, "estado_before_startup": before_startup,
                             "estado_while_ready": response.status_code,
                             "estado_after_shutdown_without_lifespan_rerun": after_shutdown,
                             "shared_ai_identity": True, "shared_state_identity": True,
                             "two_chroma_wrappers_same_db": True, "all_owned_resources_closed": True,
                             "health_calls": ai.health_calls, "generation_calls": ai.generate_calls,
                             "oracle_unconfigured": True, "registry_tool_count": len(entry.ToolRegistry.listar_nombres())})
            del orchestrator, memory, oracle, app, client
            gc.collect()
    return {"cases": rows, "active_database_opened": False, "temporary_database_only": True}


def real_chroma_degraded_boot(entry):
    import os
    from fastapi.testclient import TestClient
    from config.settings import Settings
    from memory import chroma_client as module

    logger = MagicMock()
    with TemporaryDirectory(prefix="lazzaro-boot-unavailable-") as temporary:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None, CHROMA_DB_PATH=temporary,
                                AI_FALLBACK_ORDER="gemini", LLM_FALLBACKS_ENABLED=False)
        ai = OfflineAI(settings)
        with ExitStack() as stack:
            stack.enter_context(patch.object(entry, "Settings", return_value=settings))
            stack.enter_context(patch.object(entry, "build_ai_client", return_value=ai))
            stack.enter_context(patch.object(entry, "logger", logger))
            constructor = stack.enter_context(patch.object(module.chromadb, "PersistentClient",
                                                         side_effect=RuntimeError("synthetic unavailable DB")))
            with TestClient(entry.create_app()) as client:
                orchestrator = client.app.state.orchestrator
                assert not orchestrator.memory.client.disponible
                assert not orchestrator.oracle_memory.disponible
                status = client.get("/api/v1/estado").status_code
                assert status == 200
            assert constructor.call_count == 2 and ai.close_calls == 1
    assert any("Sistemas nominales" in str(call.args[0]) for call in logger.info.call_args_list)
    return {"real_composition_with_native_chroma_failures": True, "failed_native_constructors": 2,
            "memory_available": False, "oracle_cache_available": False,
            "estado_while_ready": status, "nominal_log": True, "provider_requests": 0}


def chroma_shared_path_close(entry):
    from memory.chroma_client import ChromaClient

    with TemporaryDirectory(prefix="lazzaro-boot-shared-") as temporary:
        with ExitStack() as cleanup:
            first = ChromaClient(temporary, collection_name="audit_first")
            cleanup.callback(first.close)
            second = ChromaClient(temporary, collection_name="audit_second")
            cleanup.callback(second.close)
            assert first.disponible and second.disponible
            shared_system = first._client._system
            assert shared_system is second._client._system
            with patch.object(shared_system, "stop", wraps=shared_system.stop) as stop:
                first.close()
                assert stop.call_count == 0 and second.contar() == 0
                first.close()
                assert stop.call_count == 0
                second.close()
                assert stop.call_count == 1
                second.close()
                assert stop.call_count == 1
        del first, second, shared_system
        gc.collect()
    return {"same_path_system_shared": True, "first_close_does_not_stop_other_owner": True,
            "native_stop_calls_after_both_owners_and_repeated_closes": 1}


def chroma_cross_path_cache(entry):
    """Separate temporary paths; manually release the handle lost by production."""
    from memory.chroma_client import ChromaClient
    from chromadb.api.shared_system_client import SharedSystemClient

    with TemporaryDirectory(prefix="lazzaro-boot-multipath-") as temporary:
        first = ChromaClient(str(Path(temporary) / "a"), collection_name="audit_a")
        second = None
        system_b = None
        stop = None
        try:
            second = ChromaClient(str(Path(temporary) / "b"), collection_name="audit_b")
            assert first.disponible and second.disponible
            system_b = second._client._system
            identifier_b = second._client._identifier
            assert identifier_b in SharedSystemClient._identifier_to_system
            with patch.object(system_b, "stop", wraps=system_b.stop) as stop:
                first.close()
                assert identifier_b not in SharedSystemClient._identifier_to_system
                second.close()
                assert stop.call_count == 0
                missed_stop = stop.call_count
        finally:
            first.close()
            if second is not None:
                second.close()
            # Audit-process cleanup, NOT a production fix: DB B's native handle
            # was saved before the global cache was emptied by the real code.
            if system_b is not None and getattr(system_b, "_running", False):
                system_b.stop()
        del first, second, system_b
        gc.collect()
    return {"closing_path_a_removes_still_open_path_b_from_global_registry": True,
            "native_b_stop_calls_at_its_own_close": missed_stop,
            "audit_process_manually_cleaned_lost_temporary_handle": True,
            "data_corruption_tested": False}


CHECKS = (
    clean_fake_lifecycle, startup_failure_matrix, degraded_but_nominal,
    cleanup_failure_matrix, exceptional_lifespan_exit, partially_constructed_orchestrator,
    chroma_partial_construction, logger_import_and_existing_handler, startup_log_canary,
    real_app_smoke, real_chroma_degraded_boot, chroma_shared_path_close, chroma_cross_path_cache,
)


def main():
    from contextlib import redirect_stderr, redirect_stdout

    report = {"scope": "BOOT/CONFIG", "network_allowed": False,
              "local_socketpair_for_asyncio_only": True, "checks": []}
    unexpected = 0
    actual_connect = socket.socket.connect
    actual_socketpair = socket.socketpair
    pair_scope = threading.local()

    def local_socketpair(*args, **kwargs):
        # Windows implements the asyncio self-pipe with one loopback socketpair.
        previous = getattr(pair_scope, "active", False)
        pair_scope.active = True
        try:
            return actual_socketpair(*args, **kwargs)
        finally:
            pair_scope.active = previous

    def guarded_connect(sock, address):
        if getattr(pair_scope, "active", False):
            return actual_connect(sock, address)
        raise AssertionError("network forbidden")

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", new=guarded_connect))
        stack.enter_context(patch.object(socket, "socketpair", new=local_socketpair))
        stack.enter_context(patch.object(socket.socket, "connect_ex", side_effect=AssertionError("network forbidden")))
        stack.enter_context(patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")))
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            import main as entry
            import chromadb
        actual_persistent_client = chromadb.PersistentClient
        active_data = (ROOT / "lazzaro_data").resolve()
        blocked_active_opens = []

        def guarded_persistent_client(*args, **kwargs):
            path = Path(kwargs.get("path", args[0] if args else ".")).resolve()
            if path == active_data or active_data in path.parents:
                blocked_active_opens.append(True)
                raise AssertionError("active database forbidden")
            return actual_persistent_client(*args, **kwargs)

        stack.enter_context(patch.object(chromadb, "PersistentClient", new=guarded_persistent_client))
        for check in CHECKS:
            try:
                with redirect_stdout(captured), redirect_stderr(captured):
                    evidence = check(entry)
                report["checks"].append({"name": check.__name__, "status": "PASS", "evidence": evidence})
            except Exception as error:
                unexpected += 1
                report["checks"].append({"name": check.__name__, "status": "FAIL", "error_type": type(error).__name__})
        report["blocked_active_database_opens"] = len(blocked_active_opens)
        if blocked_active_opens:
            unexpected += 1
    report["passed"] = len(CHECKS) - unexpected
    report["failed"] = unexpected
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(unexpected)


if __name__ == "__main__":
    raise SystemExit(main())
