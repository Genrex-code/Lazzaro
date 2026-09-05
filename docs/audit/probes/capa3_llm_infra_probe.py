"""Offline deep-audit probe for Phase 2 / Block 7.

PASS means current behavior was reproduced, not that providers or Oracle are
correct. All prompts, responses and errors are synthetic. No network, active
Chroma, external LLM, tool or physical client is allowed.
"""
from __future__ import annotations

from collections import deque
from contextlib import ExitStack
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
logging.getLogger().handlers = [logging.NullHandler()]

PROTECTED = [
    "ai_engine/ai_client.py", "ai_engine/client_factory.py",
    "ai_engine/gemini_client.py", "ai_engine/openai_compatible_client.py",
    "ai_engine/ollama_client.py", "ai_engine/fallback_ai_client.py",
    "ai_engine/llm_generation_adapter.py", "ai_engine/reasoning_engine.py",
    "ai_engine/oracle_client.py", "ai_engine/oracle_engine.py",
    "ai_engine/oracle_memory.py", "ai_engine/persona_engine.py",
    "ai_engine/schemas.py", "config/settings.py", "core/orchestrator.py",
    "core/audit_trace.py", "core/audit_writer.py", "main.py",
    "lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin",
    "lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin",
    "lazzaro_data/chroma.sqlite3",
]
BLOCKED: list[str] = []
BLOCKED_DETAILS: list[dict[str, Any]] = []
EXPECTED_BLOCKED_PLATFORM: list[str] = []
EXPECTED_BLOCKED_SYSTEM: list[str] = []
WRITE_ATTEMPTS: list[str] = []


def hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest().upper()
            for name in PROTECTED}


def deny(*_args, **_kwargs):
    raise PermissionError("Block 7 external operation forbidden")


def audit_barrier(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
        frame = sys._getframe()
        stack = []
        while frame is not None and len(stack) < 12:
            stack.append(f"{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}")
            frame = frame.f_back
        if event == "socket.bind" and "connection.py:_has_ipv6" in stack:
            EXPECTED_BLOCKED_SYSTEM.append(event)
        else:
            BLOCKED.append(event)
            BLOCKED_DETAILS.append({"event": event, "stack": stack})
        deny()
    if event == "subprocess.Popen":
        frame = sys._getframe()
        platform_probe = False
        while frame is not None:
            code = frame.f_code
            if code.co_filename.endswith("platform.py") and code.co_name == "_syscmd_ver":
                platform_probe = True
                break
            frame = frame.f_back
        (EXPECTED_BLOCKED_PLATFORM if platform_probe else BLOCKED).append(event)
        deny()
    if event in {"os.system", "os.startfile", "sqlite3.connect"}:
        BLOCKED.append(event)
        deny()
    if event == "open":
        _path, mode, flags = args
        if os.path.normcase(str(_path)) == os.path.normcase(os.devnull):
            return
        write_mode = isinstance(mode, str) and any(char in mode for char in "wax+")
        write_flags = isinstance(flags, int) and bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if write_mode or write_flags:
            WRITE_ATTEMPTS.append(str(_path))
            deny()
    if event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir"}:
        WRITE_ATTEMPTS.append(event)
        deny()


def emit(group: str, data: Any) -> None:
    print(json.dumps({"group": group, "data": data}, ensure_ascii=True,
                     allow_nan=False, separators=(",", ":")), flush=True)


class FakeOracleMemory:
    def __init__(self, cached=None, *, search_error=None, write_error=None):
        self.cached = cached
        self.search_error = search_error
        self.write_error = write_error
        self.searches: list[str] = []
        self.writes: list[dict[str, Any]] = []

    def buscar_resultado_cacheado(self, pregunta):
        self.searches.append(pregunta)
        if self.search_error:
            raise self.search_error
        return self.cached

    def buscar_respuesta_cacheada(self, pregunta):
        return None

    def guardar_en_cache(self, pregunta, contenido_tecnico,
                         resumen_para_persona=None, fuente="api"):
        if self.write_error:
            raise self.write_error
        self.writes.append({"pregunta": pregunta, "contenido": contenido_tecnico,
                            "resumen": resumen_para_persona, "fuente": fuente})
        return True


class FakeHTTPResponse:
    def __init__(self, data=None, *, status=200, text=""):
        self._data, self.status_code, self.text = data, status, text
        self.request = None

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", "https://synthetic.invalid")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise __import__("httpx").HTTPStatusError("SYNTHETIC", request=request, response=response)

    def json(self):
        if isinstance(self._data, BaseException):
            raise self._data
        return self._data


class FakeHTTPClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0
        self.closed = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)

    def close(self):
        self.closed += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def audit_dict(event):
    return {"provider": event.provider, "model_id": event.model_id,
            "fallback_used": event.fallback_used, "status": event.status,
            "error_type": event.error_type,
            "duration_nonnegative": event.duration_ms >= 0,
            "has_content_fields": any(hasattr(event, name) for name in ("prompt", "response", "message"))}


def run() -> int:
    initial = hashes()
    sys.addaudithook(audit_barrier)
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", deny))
        stack.enter_context(patch.object(socket.socket, "connect_ex", deny))
        stack.enter_context(patch.object(socket, "create_connection", deny))

        from pydantic import ValidationError
        import httpx
        import chromadb
        from ai_engine.ai_client import BaseAIClient, LLMCallAudit
        from ai_engine.fallback_ai_client import FallbackAIClient
        from ai_engine.gemini_client import GeminiClient
        from ai_engine.ollama_client import OllamaClient
        from ai_engine.openai_compatible_client import OpenAICompatibleClient
        from ai_engine.oracle_client import OracleClient
        from ai_engine.oracle_engine import OracleEngine
        from ai_engine.oracle_memory import OracleMemory
        from ai_engine.persona_engine import PersonaEngine
        from ai_engine.schemas import OracleResult, ActionResponse
        from api.schemas import EventoEntrante
        from config.settings import Settings
        from core.orchestrator import Orchestrator
        from memory.chroma_client import ChromaClient
        from tools.executor import ActionExecutor

        stack.enter_context(patch.object(ChromaClient, "__init__", deny))
        stack.enter_context(patch.object(chromadb, "PersistentClient", deny))
        stack.enter_context(patch.object(chromadb, "Client", deny))
        stack.enter_context(patch.object(ActionExecutor, "ejecutar", deny))

        # OracleResult and parse/repair behavior.
        engine = OracleEngine()
        parser_rows = []
        parser_cases = [
            ("empty", ""),
            ("malformed_object", '{"contenido_tecnico":'),
            ("valid_non_object", '["x"]'),
            ("plain_text", "Conocimiento plano sintetico."),
            ("unknown_fields", '{"contenido_tecnico":"T","desconocido":"X"}'),
            ("legacy_response", '{"respuesta":"Legacy T"}'),
            ("legacy_action_payload", '{"parametros":{"texto_a_comunicar":"Legacy P"}}'),
            ("wrapped_json", 'prefijo {"contenido_tecnico":"Inner"} sufijo'),
            ("wrong_content_type", '{"contenido_tecnico":{"nested":1}}'),
        ]
        for name, raw in parser_cases:
            memory = FakeOracleMemory()
            result = engine.keep_y_save(raw, "Q synthetic", memory)
            parser_rows.append({"case": name, "result": result.model_dump(),
                                "cache_writes": len(memory.writes)})
        schema_rows = []
        for name, kwargs in [
            ("blank_required_strings", {"pregunta_original": "", "contenido_tecnico": ""}),
            ("unknown_extra", {"pregunta_original": "q", "contenido_tecnico": "t", "extra": "ignored"}),
            ("bad_source", {"pregunta_original": "q", "contenido_tecnico": "t", "fuente": "other"}),
            ("missing_content", {"pregunta_original": "q"}),
        ]:
            try:
                value = OracleResult(**kwargs)
                schema_rows.append({"case": name, "accepted": True, "result": value.model_dump()})
            except ValidationError as exc:
                schema_rows.append({"case": name, "accepted": False,
                                    "error_types": [row["type"] for row in exc.errors()]})
        emit("oracle_contract", {"parser": parser_rows, "schema": schema_rows,
                                 "prompt_roles": [x["role"] for x in engine.preparar_consulta("Q")],
                                 "prompt_forbids_actions": "No incluyas accion" in engine.preparar_consulta("Q")[0]["content"]})

        # Actual OracleMemory interpretation over an in-memory fake Chroma facade.
        class CacheClient:
            def __init__(self, search_result=None, search_error=None, add_error=None):
                self.disponible = True
                self.search_result, self.search_error, self.add_error = search_result, search_error, add_error
                self.queries, self.adds, self.closed = [], [], 0

            def buscar(self, **kwargs):
                self.queries.append(kwargs)
                if self.search_error:
                    raise self.search_error
                return self.search_result

            def agregar(self, **kwargs):
                self.adds.append(kwargs)
                if self.add_error:
                    raise self.add_error
                return True

            def close(self): self.closed += 1

        def memory_with(client):
            memory = object.__new__(OracleMemory)
            memory.db_path, memory.client = "synthetic", client
            return memory

        cache_rows = []
        cache_payloads = [
            ("hit", {"documents": [["q"]], "metadatas": [[{"contenido_tecnico": "T", "resumen_para_persona": "R", "fuente": "api", "timestamp": "1900"}]], "distances": [[0.1]]}),
            ("distance_miss", {"documents": [["q"]], "metadatas": [[{"contenido_tecnico": "T"}]], "distances": [[0.36]]}),
            ("missing_metadata", {"documents": [["q"]], "metadatas": [[]], "distances": [[0.1]]}),
            ("malformed_metadata", {"documents": [["q"]], "metadatas": [["bad"]], "distances": [[0.1]]}),
            ("empty_content", {"documents": [["q"]], "metadatas": [[{"contenido_tecnico": ""}]], "distances": [[0.1]]}),
            ("bad_distance", {"documents": [["q"]], "metadatas": [[{"contenido_tecnico": "T"}]], "distances": [["bad"]]}),
        ]
        for name, payload in cache_payloads:
            client = CacheClient(payload)
            memory = memory_with(client)
            result = memory.buscar_resultado_cacheado("  Q Raw  ")
            cache_rows.append({"case": name, "result": result.model_dump() if result else None,
                               "query_sent": client.queries})
        for name, error in [("search_exception", RuntimeError("SYNTHETIC_SEARCH_SECRET"))]:
            client = CacheClient(search_error=error)
            try:
                memory_with(client).buscar_resultado_cacheado("Q")
                escaped = None
            except Exception as exc:
                escaped = type(exc).__name__
            cache_rows.append({"case": name, "escaped": escaped})
        client = CacheClient()
        saved = memory_with(client).guardar_en_cache("Q exact", "T", "R", fuente="fallback")
        stored_meta = client.adds[0]["metadatos"][0]
        cache_rows.append({"case": "write_contract", "saved": saved,
                           "document": client.adds[0]["documentos"],
                           "metadata_keys": sorted(stored_meta),
                           "model_or_provider_stored": any(k in stored_meta for k in ("model", "model_id", "provider", "config_version")),
                           "ttl_stored": any(k in stored_meta for k in ("ttl", "expires_at"))})
        write_fail_memory = FakeOracleMemory(write_error=RuntimeError("SYNTHETIC_CACHE_WRITE"))
        write_fail_result = engine.keep_y_save('{"contenido_tecnico":"T"}', "Q", write_fail_memory)
        cache_rows.append({"case": "provider_success_cache_write_failure",
                           "result": write_fail_result.model_dump(), "escaped": None})
        emit("cache", cache_rows)

        # Deterministic exact fake cache illustrates what changes across ten calls.
        class ExactCache:
            def __init__(self): self.data, self.searches, self.writes = {}, [], 0
            def buscar_resultado_cacheado(self, query):
                self.searches.append(query)
                value = self.data.get(query)
                return value.model_copy(update={"pregunta_original": query, "fuente": "cache", "cache_hit": True}) if value else None
            def buscar_respuesta_cacheada(self, query): return None
            def guardar_en_cache(self, query, content, resumen_para_persona=None, fuente="api"):
                self.writes += 1
                self.data[query] = OracleResult(pregunta_original=query, contenido_tecnico=content,
                    resumen_para_persona=resumen_para_persona, fuente=fuente, cache_hit=False)
                return True
        exact = ExactCache()
        provider_calls = 0
        result_variants, persona_inputs, action_variants = [], [], []
        persona = PersonaEngine()
        identity = SimpleNamespace(inyectar_en_prompt=lambda: "STATE")
        for _ in range(10):
            result = engine.evaluar_cache("Q exact", exact)
            if result is None:
                provider_calls += 1
                result = engine.keep_y_save('{"contenido_tecnico":"2","resumen_para_persona":"dos"}', "Q exact", exact)
            result_variants.append(json.dumps(result.model_dump(), sort_keys=True))
            messages = persona.preparar_verbalizacion_oraculo("Q exact", identity, result, recuerdos="MEM")
            persona_inputs.append(messages[0]["content"])
            parsed = persona.parsear_respuesta('{"accion":"hablar","parametros":{"texto_a_comunicar":"dos"}}')
            action_variants.append(json.dumps(parsed, sort_keys=True))
        # These dimensions change outside OracleMemory; its real API still receives only the question.
        sensitivity = []
        for dimension in ("context", "memory", "model_id", "provider", "config"):
            before = len(exact.searches)
            observed = engine.evaluar_cache("Q exact", exact)
            sensitivity.append({"changed_only": dimension, "query": exact.searches[before],
                                "cache_hit": bool(observed and observed.cache_hit)})
        whitespace_miss = engine.evaluar_cache(" Q exact ", exact) is None
        emit("determinism", {"runs": 10, "provider_calls": provider_calls,
            "cache_writes": exact.writes, "unique_oracle_results": len(set(result_variants)),
            "unique_persona_prompts": len(set(persona_inputs)), "unique_actions": len(set(action_variants)),
            "queries": exact.searches, "cache_inputs": ["pregunta only"],
            "context_memory_model_provider_in_key": False,
            "sensitivity": sensitivity, "whitespace_control_miss": whitespace_miss})

        # Deterministic miss barrier proves duplicate provider/cache work for identical concurrent misses.
        miss_barrier = threading.Barrier(2)
        provider_barrier = threading.Barrier(2)
        shared_lock = threading.Lock()
        shared_entries: dict[str, OracleResult] = {}
        concurrency = {"searches": 0, "provider_calls": 0, "writes": 0}

        class ConcurrentCache:
            def buscar_resultado_cacheado(self, query):
                with shared_lock:
                    concurrency["searches"] += 1
                    value = shared_entries.get(query)
                miss_barrier.wait(timeout=3)
                return value
            def buscar_respuesta_cacheada(self, query): return None
            def guardar_en_cache(self, query, content, resumen_para_persona=None, fuente="api"):
                with shared_lock:
                    concurrency["writes"] += 1
                    shared_entries[query] = OracleResult(pregunta_original=query,
                        contenido_tecnico=content, resumen_para_persona=resumen_para_persona,
                        fuente=fuente, cache_hit=False)
                return True

        def concurrent_worker():
            memory = ConcurrentCache()
            result = engine.evaluar_cache("same", memory)
            if result is None:
                with shared_lock: concurrency["provider_calls"] += 1
                provider_barrier.wait(timeout=3)
                result = engine.keep_y_save('{"contenido_tecnico":"T"}', "same", memory)
            return result.contenido_tecnico

        outputs = [None, None]
        threads = [threading.Thread(target=lambda i=i: outputs.__setitem__(i, concurrent_worker())) for i in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        distinct_lock = threading.Lock()
        distinct_barrier = threading.Barrier(2)
        distinct = {"searches": [], "provider_calls": [], "writes": [], "outputs": {}}

        class DistinctCache:
            disponible = True
            def buscar_resultado_cacheado(self, query):
                with distinct_lock: distinct["searches"].append(query)
                return None
            def buscar_respuesta_cacheada(self, query): return None
            def guardar_en_cache(self, query, content, resumen_para_persona=None, fuente="api"):
                with distinct_lock: distinct["writes"].append(query)
                return True

        def distinct_worker(query):
            memory = DistinctCache()
            assert engine.evaluar_cache(query, memory) is None
            distinct_barrier.wait(timeout=3)
            with distinct_lock: distinct["provider_calls"].append(query)
            result = engine.keep_y_save(
                json.dumps({"contenido_tecnico": "T:" + query}), query, memory)
            with distinct_lock: distinct["outputs"][query] = result.contenido_tecnico

        distinct_threads = [threading.Thread(target=distinct_worker, args=(query,))
                            for query in ("Q-one", "Q-two")]
        for thread in distinct_threads: thread.start()
        for thread in distinct_threads: thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in distinct_threads)
        emit("cache_concurrency", {**concurrency, "outputs": outputs, "same_result": outputs == ["T", "T"],
                                   "duplicate_miss_work": concurrency["provider_calls"] == concurrency["writes"] == 2,
                                   "distinct_queries": distinct})

        # Provider behavior without real client construction or transport.
        def openai_case(name, outcome):
            client = object.__new__(OpenAICompatibleClient)
            client.provider_name, client.model_name = "compatible", "model-c"
            client.api_key, client.api_url = "SYNTHETIC_KEY", "https://synthetic.invalid"
            client.supports_json_format, client.timeout, client.client = True, 45.0, FakeHTTPClient(outcome)
            client.begin_audit_capture()
            text = client.generate("SYNTHETIC_PROMPT")
            events = [audit_dict(x) for x in client.consume_audit_events()]
            calls = client.client.calls
            client.close()
            return {"case": name, "text": text, "calls": calls, "events": events}

        def ollama_case(name, outcome):
            client = object.__new__(OllamaClient)
            client.provider_name, client.model_name = "ollama", "model-o"
            client.client = FakeHTTPClient(outcome)
            client.begin_audit_capture()
            text = client.generate("SYNTHETIC_PROMPT")
            events = [audit_dict(x) for x in client.consume_audit_events()]
            calls = client.client.calls
            client.close()
            return {"case": name, "text": text, "calls": calls, "events": events}

        def oracle_http_case(name, outcome):
            client = object.__new__(OracleClient)
            client.api_key, client.api_url, client.model_name = "SYNTHETIC_KEY", "https://synthetic.invalid", "model-x"
            client.timeout, client._supports_json_format = 15.0, True
            fake = FakeHTTPClient(outcome)
            client.begin_audit_capture()
            with patch("ai_engine.oracle_client.httpx.Client", return_value=fake):
                text = client.consultar_oraculo([{"role": "user", "content": "SYNTHETIC"}])
            return {"case": name, "text": text, "calls": fake.calls,
                    "context_closed": fake.closed, "events": [audit_dict(x) for x in client.consume_audit_events()]}

        timeout = httpx.ReadTimeout("SYNTHETIC_TIMEOUT", request=httpx.Request("POST", "https://synthetic.invalid"))
        connection = httpx.ConnectError("SYNTHETIC_CONNECTION", request=httpx.Request("POST", "https://synthetic.invalid"))
        auth = FakeHTTPResponse({}, status=401, text="SYNTHETIC_AUTH_BODY")
        rate = FakeHTTPResponse({}, status=429, text="SYNTHETIC_RATE_BODY")
        good_chat = FakeHTTPResponse({"choices": [{"message": {"content": "OK"}}]})
        empty_chat = FakeHTTPResponse({"choices": [{"message": {"content": ""}}]})
        malformed = FakeHTTPResponse(ValueError("SYNTHETIC_JSON"))
        good_ollama = FakeHTTPResponse({"response": "OK"})
        empty_ollama = FakeHTTPResponse({"response": ""})
        provider_rows = []
        for name, outcome in [("success", good_chat), ("empty", empty_chat), ("timeout", timeout),
                              ("connection", connection), ("auth", auth), ("rate_limit", rate), ("malformed", malformed)]:
            provider_rows.append({"provider": "compatible", **openai_case(name, outcome)})
            provider_rows.append({"provider": "oracle_external", **oracle_http_case(name, outcome)})
        for name, outcome in [("success", good_ollama), ("empty", empty_ollama), ("timeout", timeout),
                              ("connection", connection), ("auth", auth), ("rate_limit", rate), ("malformed", malformed)]:
            provider_rows.append({"provider": "ollama", **ollama_case(name, outcome)})

        class GeminiModels:
            def __init__(self, outcomes): self.outcomes, self.calls = deque(outcomes), 0
            def generate_content(self, **kwargs):
                self.calls += 1
                outcome = self.outcomes.popleft() if self.outcomes else ""
                if isinstance(outcome, BaseException): raise outcome
                return SimpleNamespace(text=outcome, candidates=[])

        def gemini_case(name, outcomes):
            client = object.__new__(GeminiClient)
            client.provider_name, client.model_name = "gemini", "model-g"
            models = GeminiModels(outcomes)
            client.client = SimpleNamespace(models=models)
            client.begin_audit_capture()
            with patch.object(client, "_build_generation_config", return_value=None), \
                 patch("ai_engine.gemini_client.time.sleep") as sleeper, \
                 patch("ai_engine.gemini_client.random.uniform", return_value=0.0):
                text = client.generate("SYNTHETIC_PROMPT")
            return {"provider": "gemini", "case": name, "text": text, "calls": models.calls,
                    "sleeps": [call.args[0] for call in sleeper.call_args_list],
                    "events": [audit_dict(x) for x in client.consume_audit_events()]}

        class SyntheticAuthError(Exception): pass
        class SyntheticRateLimitError(Exception): pass
        provider_rows.extend([
            gemini_case("success", ["OK"]), gemini_case("empty", ["", "", "", ""]),
            gemini_case("timeout", [TimeoutError("SYNTHETIC")]*4),
            gemini_case("connection", [ConnectionError("SYNTHETIC")]*4),
            gemini_case("auth", [SyntheticAuthError("SYNTHETIC")]*4),
            gemini_case("rate_limit", [SyntheticRateLimitError("SYNTHETIC")]*4),
            gemini_case("recovery_same_provider", [TimeoutError("SYNTHETIC"), "OK"]),
        ])
        health_client = object.__new__(GeminiClient)
        with patch.object(health_client, "generate", return_value="not ok") as health_generate:
            not_ok_healthy = health_client.health_check()
        emit("providers", {"rows": provider_rows,
            "health_substring_not_ok_is_healthy": not_ok_healthy,
            "health_generate_calls": health_generate.call_count,
            "timeouts": {"gemini": None, "compatible": 45.0, "ollama": 120.0, "oracle_external": 15.0},
            "retry_counts": {"gemini_max_attempts": 4, "compatible": 1, "ollama": 1, "oracle_external": 1},
            "cancellation_contract": False})

        class Child(BaseAIClient):
            def __init__(self, name, outcomes, health=True, close_error=False):
                self.provider_name, self.model_name = name, "model-" + name
                self.outcomes, self.calls, self.health_calls = deque(outcomes), 0, 0
                self.healthy, self.closed, self.close_error = health, 0, close_error
            def generate(self, prompt, **kwargs):
                self.calls += 1
                value = self.outcomes.popleft() if self.outcomes else ""
                if isinstance(value, BaseException): raise value
                return value
            def health_check(self): self.health_calls += 1; return self.healthy
            def close(self):
                self.closed += 1
                if self.close_error: raise RuntimeError("SYNTHETIC_CLOSE")

        def fallback_case(name, a_values, b_values=None):
            a, b = Child("A", a_values), Child("B", b_values or [])
            children = [a] + ([b] if b_values is not None else [])
            chain = FallbackAIClient(children)
            chain.begin_audit_capture()
            text = chain.generate("SYNTHETIC_PROMPT")
            events = chain.consume_audit_events()
            return {"case": name, "text": text, "calls": {"A": a.calls, "B": b.calls},
                    "order": [e.provider for e in events],
                    "events": [audit_dict(e) for e in events], "selected": chain.last_provider_used}

        fallback_rows = [
            fallback_case("A_ok", ["AOK"]), fallback_case("A_timeout", [TimeoutError("S")]),
            fallback_case("A_error", [RuntimeError("S")]), fallback_case("A_empty", [""]),
            fallback_case("A_fail_B_ok", [""], ["BOK"]),
            fallback_case("A_timeout_B_ok", [TimeoutError("S")], ["BOK"]),
            fallback_case("A_fail_B_fail", [""], [""]),
        ]
        # Capture event details in a dedicated chain (the compact rows above preserve order).
        a, b = Child("A", ["", "A_SECOND"]), Child("B", ["B_FIRST"])
        sticky_chain = FallbackAIClient([a, b])
        selected_sequence = [sticky_chain.generate("one"), sticky_chain.last_provider_used,
                             sticky_chain.generate("two"), sticky_chain.last_provider_used]
        health_a, health_b = Child("A", [""], health=False), Child("B", [""], health=True)
        health_chain = FallbackAIClient([health_a, health_b])
        health_result = health_chain.health_check()
        close_a, close_b = Child("A", [""], close_error=True), Child("B", [""])
        FallbackAIClient([close_a, close_b]).close()

        class RetryChild(Child):
            def generate(self, prompt, **kwargs):
                self.calls += 1
                self._record_llm_audit(LLMCallAudit("A", self.model_name, False, "timeout", "TimeoutError", 1, 1))
                self._record_llm_audit(LLMCallAudit("A", self.model_name, False, "ok", None, 1, 2))
                return "RECOVERED"
        retry = RetryChild("A", [])
        retry_chain = FallbackAIClient([retry])
        retry_chain.begin_audit_capture()
        retry_text = retry_chain.generate("S")
        retry_events = [audit_dict(e) for e in retry_chain.consume_audit_events()]

        # Thread-local selected provider and captures with deterministic per-thread outcomes.
        barrier = threading.Barrier(2)
        class ThreadChild(Child):
            def __init__(self, name): super().__init__(name, [])
            def generate(self, prompt, **kwargs):
                self.calls += 1
                if self.provider_name == "A":
                    barrier.wait(timeout=3)
                    if prompt == "fallback": return ""
                return self.provider_name + ":" + prompt
        shared = FallbackAIClient([ThreadChild("A"), ThreadChild("B")])
        thread_results: dict[str, Any] = {}
        def thread_call(prompt):
            shared.begin_audit_capture(); text = shared.generate(prompt)
            thread_results[prompt] = {"text": text, "events": [audit_dict(e) for e in shared.consume_audit_events()],
                                      "selected": shared.last_provider_used}
        ts = [threading.Thread(target=thread_call, args=(value,)) for value in ("primary", "fallback")]
        for t in ts: t.start()
        for t in ts: t.join(timeout=5)
        assert all(not t.is_alive() for t in ts)
        emit("fallback", {"rows": fallback_rows, "selection_not_sticky": selected_sequence,
            "health": {"result": health_result, "calls": [health_a.health_calls, health_b.health_calls]},
            "close_continues_after_error": [close_a.closed, close_b.closed],
            "same_provider_retry": {"text": retry_text, "events": retry_events},
            "thread_local": thread_results})

        # Oracle -> Persona fact preservation is instructional only, not validated.
        oracle_result = OracleResult(pregunta_original="Cuanto?", contenido_tecnico="La respuesta es 2.",
                                     resumen_para_persona="Responde dos.", fuente="api")
        persona_messages = persona.preparar_verbalizacion_oraculo("Cuanto?", identity, oracle_result)
        contradicted = persona.parsear_respuesta(
            '{"accion":"navegar_a_objeto","parametros":{"texto_a_comunicar":"La respuesta es 3."},'
            '"pensamiento_privado":"P","razon":"R","extra":"kept"}')
        emit("oracle_persona", {"oracle": oracle_result.model_dump(),
            "prompt_contains_content": "La respuesta es 2." in persona_messages[0]["content"],
            "prompt_contains_source_and_cache": "Fuente: api" in persona_messages[0]["content"] and "Cache hit: False" in persona_messages[0]["content"],
            "prompt_instructs_no_invention": "No inventes datos" in persona_messages[0]["content"],
            "contradictory_persona_output": contradicted,
            "facts_validated_against_oracle": False,
            "physical_action_blocked_to": contradicted["accion"]})

        # Full Orchestrator/AuditTrace cases use existing in-memory harness and actual Oracle route.
        from api.schemas import EventoEntrante
        from terminal_client.test_chat import _orquestador_aislado

        class AuditedExternal(BaseAIClient):
            provider_name, model_name, configurado = "oracle_external", "oracle-model", True
            def __init__(self, outcome): self.outcome, self.calls = outcome, 0
            def consultar_oraculo(self, messages):
                self.calls += 1
                started_ns = time.perf_counter_ns()
                if isinstance(self.outcome, BaseException):
                    error = self.outcome
                    self._record_llm_audit(LLMCallAudit(self.provider_name, self.model_name, False,
                        "timeout" if self.stable_error_type(error) == "TimeoutError" else "error",
                        self.stable_error_type(error), 1, started_ns))
                    return None
                status = "ok" if self.outcome else "empty"
                self._record_llm_audit(LLMCallAudit(self.provider_name, self.model_name, False,
                    status, None if status == "ok" else "EmptyResponse", 1, started_ns))
                return self.outcome
            def generate(self, prompt, **kwargs): raise AssertionError("wrong interface")
            def health_check(self): return True

        class QueueProvider(BaseAIClient):
            def __init__(self, name, outcomes): self.provider_name, self.model_name = name, "model-"+name; self.outcomes=deque(outcomes); self.calls=0
            def generate(self, prompt, **kwargs):
                self.calls += 1
                started_ns = time.perf_counter_ns()
                value = self.outcomes.popleft() if self.outcomes else ""
                if isinstance(value, BaseException):
                    error_type=self.stable_error_type(value)
                    self._record_llm_audit(LLMCallAudit(self.provider_name,self.model_name,False,"timeout" if error_type=="TimeoutError" else "error",error_type,1,started_ns))
                    raise value
                status="ok" if value else "empty"
                self._record_llm_audit(LLMCallAudit(self.provider_name,self.model_name,False,status,None if value else "EmptyResponse",1,started_ns))
                return value
            def health_check(self): return True

        class Adapter:
            def __init__(self, client): self.client,self.prompts=client,[]
            def ejecutar_texto(self, messages, **kwargs):
                prompt="\n".join(str(x.get("content", "")) for x in messages)
                self.prompts.append({"contains_oracle_content": "CONOCIMIENTO INTERNO DEL ORACULO" in prompt,
                                     "error_context": kwargs.get("error_context")})
                return self.client.generate("SYNTHETIC_INTERNAL_PROMPT", **kwargs)

        class Writer:
            def __init__(self, fail=False): self.fail,self.rows=fail,[]
            def write(self, trace):
                self.rows.append(json.loads(trace.to_json()))
                if self.fail: raise OSError("SYNTHETIC_WRITER")
                return True

        def full_case(name, cache_result, external_outcome, active_clients, fail_writer=False,
                      cache_search_error=None, cache_write_error=None):
            orch, calls = _orquestador_aislado("oracle", intent="ORACLE_QUERY")
            del orch.__dict__["_resolver_ruta_oracle"]
            orch.oracle_engine = OracleEngine()
            orch.oracle_memory = FakeOracleMemory(
                cache_result, search_error=cache_search_error, write_error=cache_write_error)
            orch.oracle_client = AuditedExternal(external_outcome)
            active = active_clients[0] if len(active_clients) == 1 else FallbackAIClient(active_clients)
            orch.reasoning = SimpleNamespace(ai_client=active)
            orch.persona_engine, orch.llm_adapter, orch.audit_writer = PersonaEngine(), Adapter(active), Writer(fail_writer)
            response = orch.procesar_evento(EventoEntrante(
                origen="unity", tipo_evento="comando_usuario",
                id_transaccion="block7-" + name,
                detalles={"mensaje": "Procesa este turno.", "solicita_decision": True},
            ))
            rows=orch.audit_writer.rows
            public=json.dumps(rows, sort_keys=True)
            forbidden=[token for token in ("SYNTHETIC_INTERNAL_PROMPT","PRIVATE_RESPONSE_TOKEN","PRIVATE_ERROR_TOKEN") if token in public]
            return {"case":name,"action":response.nombre,"text":response.parametros.get("texto_a_comunicar"),
                "external_calls":orch.oracle_client.calls,"active_calls":{c.provider_name:c.calls for c in active_clients},
                "adapter_calls":orch.llm_adapter.prompts,"cache_searches":orch.oracle_memory.searches,
                "cache_writes":len(orch.oracle_memory.writes),"stages":[x["stage"] for x in rows],
                "errors":[{"error":x.get("error"),"llm":x.get("llm")} for x in rows if x["stage"]=="error"],
                "decision":next(x for x in rows if x["stage"]=="decision")["decision"],
                "decision_llm":next(x for x in rows if x["stage"]=="decision")["llm"],
                "same_trace_id":len({x["trace_id"] for x in rows})==1,"forbidden_in_trace":forbidden,
                "writer_failed":fail_writer}

        persona_json = json.dumps({"accion":"hablar","parametros":{"texto_a_comunicar":"PUBLIC"},"pensamiento_privado":"PRIVATE_RESPONSE_TOKEN","razon":"R"})
        cache_hit = OracleResult(pregunta_original="Q", contenido_tecnico="CACHE_TECH", fuente="cache", cache_hit=True)
        audit_rows = [
            full_case("cache_hit", cache_hit, '{"contenido_tecnico":"unused"}', [QueueProvider("persona",[persona_json])]),
            full_case("external_success", None, '{"contenido_tecnico":"EXT_TECH"}', [QueueProvider("persona",[persona_json])]),
            full_case("cache_search_failure", None, '{"contenido_tecnico":"EXT_TECH"}',
                      [QueueProvider("persona",[persona_json])],
                      cache_search_error=RuntimeError("PRIVATE_ERROR_TOKEN")),
            full_case("external_timeout_active_success", None, TimeoutError("PRIVATE_ERROR_TOKEN"),
                      [QueueProvider("active",['{"contenido_tecnico":"FALLBACK_TECH"}',persona_json])]),
            full_case("provider_fallback", None, None,
                      [QueueProvider("A",[RuntimeError("PRIVATE_ERROR_TOKEN"),RuntimeError("PRIVATE_ERROR_TOKEN")]),
                       QueueProvider("B",['{"contenido_tecnico":"B_TECH"}',persona_json])]),
            full_case("all_active_empty", None, None, [QueueProvider("A",["","",""]),QueueProvider("B",["","",""])]),
            full_case("writer_failure", cache_hit, '{"contenido_tecnico":"unused"}', [QueueProvider("persona",[persona_json])], True),
        ]
        writer_control = audit_rows[0]
        writer_fail = audit_rows[-1]
        assert {k:v for k,v in writer_control.items() if k not in {"case","writer_failed"}} == {k:v for k,v in writer_fail.items() if k not in {"case","writer_failed"}}
        emit("audittrace", audit_rows)

        # Factory selection is observed with constructors patched; no SDK/HTTP allocated.
        from ai_engine import client_factory
        class Built(Child): pass
        built=[]
        def construct(name):
            client=Built(name,["ok"]);built.append(client);return client
        base = Settings(_env_file=None, AI_PROVIDER="gemini", GEMINI_API_KEY="SYNTHETIC",
                        LLM_FALLBACKS_ENABLED=False)
        with patch.object(client_factory, "_build_single_client", side_effect=lambda s,p: construct(p)):
            selected=client_factory.build_ai_client(base)
        explicit = SimpleNamespace(AI_PROVIDER="gemini", AI_FALLBACK_ORDER="unknown,groq,kimi", LLM_FALLBACKS_ENABLED=True)
        with patch.object(client_factory, "_build_single_client", side_effect=lambda s,p: construct(p)):
            selected_explicit=client_factory.build_ai_client(explicit)
        missing = SimpleNamespace(AI_PROVIDER="gemini", AI_FALLBACK_ORDER="gemini", LLM_FALLBACKS_ENABLED=True, GEMINI_API_KEY=None)
        try: client_factory.build_ai_client(missing); missing_error=None
        except Exception as exc: missing_error=type(exc).__name__
        emit("factory", {"settings_default_provider":Settings.model_fields["AI_PROVIDER"].default,
            "configured_profile_inherited_from_block1":"gemini with fallbacks disabled; not re-read from .env",
            "disabled_selected":selected.provider_name,
            "explicit_unknown_filtered_order":selected_explicit.provider_order if isinstance(selected_explicit,FallbackAIClient) else [selected_explicit.provider_name],
            "missing_key_error":missing_error,
            "supported":list(client_factory.PROVEEDORES_SOPORTADOS),
            "models":{"gemini":Settings.model_fields["GEMINI_MODEL_NAME"].default,"groq":Settings.model_fields["GROQ_MODEL"].default,
                      "xai":Settings.model_fields["XAI_MODEL"].default,"kimi":Settings.model_fields["KIMI_MODEL"].default,
                      "deepseek_api":Settings.model_fields["DEEPSEEK_API_MODEL"].default,
                      "ollama":Settings.model_fields["DEEPSEEK_MODEL"].default,
                      "oracle_external_fallback":Settings.model_fields["GROQ_MODEL"].default}})

    final = hashes()
    assert final == initial
    assert not BLOCKED and not WRITE_ATTEMPTS, {
        "blocked": BLOCKED, "write_attempts": WRITE_ATTEMPTS,
        "blocked_details": BLOCKED_DETAILS,
        "expected_platform": EXPECTED_BLOCKED_PLATFORM,
        "expected_system": EXPECTED_BLOCKED_SYSTEM,
    }
    emit("integrity", {"status":"PASS_REPRODUCED_NOT_PROVIDER_APPROVAL",
        "protected_hashes":final,"protected_unchanged":True,"blocked_operations":BLOCKED,
        "expected_platform_probes_blocked":EXPECTED_BLOCKED_PLATFORM,
        "expected_system_probes_blocked":EXPECTED_BLOCKED_SYSTEM,
        "write_attempts":WRITE_ATTEMPTS,"network":False,"active_chroma":False,
        "real_llm":False,"physical_execution":False,"synthetic_secrets_only":True})
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
