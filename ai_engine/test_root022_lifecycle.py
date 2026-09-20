"""ROOT-022: ownership temporal de providers y transporte Gemini por llamada."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import ai_engine.client_factory as factory
import ai_engine.gemini_client as gemini_module
from ai_engine.fallback_ai_client import FallbackAIClient
from ai_engine.llm_call_control import LLMCallBudgetV1


def test_factory_rolls_back_providers_in_reverse_on_later_failure(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(factory, "_resolver_orden_proveedores", lambda _: ["one", "two", "three"])
    def build(_, name):
        if name == "three":
            raise RuntimeError("PROVIDER_CANARY")
        return SimpleNamespace(provider_name=name, close=lambda: events.append(name))
    monkeypatch.setattr(factory, "_build_single_client", build)
    with pytest.raises(RuntimeError, match="PROVIDER_CANARY"):
        factory.build_ai_client(SimpleNamespace(LLM_FALLBACKS_ENABLED=True))
    assert events == ["two", "one"]


@pytest.mark.parametrize("failed_provider,closed", [
    ("one", []), ("two", ["one"]),
])
def test_factory_failure_at_early_provider(monkeypatch, failed_provider, closed):
    events: list[str] = []
    monkeypatch.setattr(factory, "_resolver_orden_proveedores", lambda _: ["one", "two"])
    def build(_, name):
        if name == failed_provider:
            raise RuntimeError("PROVIDER_CANARY")
        return SimpleNamespace(provider_name=name, close=lambda: events.append(name))
    monkeypatch.setattr(factory, "_build_single_client", build)
    with pytest.raises(RuntimeError, match="PROVIDER_CANARY"):
        factory.build_ai_client(SimpleNamespace(LLM_FALLBACKS_ENABLED=True))
    assert events == closed


def test_factory_rollback_when_fallback_composition_fails(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(factory, "_resolver_orden_proveedores", lambda _: ["one"])
    monkeypatch.setattr(factory, "_build_single_client", lambda *_: SimpleNamespace(
        provider_name="one", close=lambda: events.append("one")
    ))
    monkeypatch.setattr(factory, "FallbackAIClient", lambda *_a, **_kw:
                        (_ for _ in ()).throw(RuntimeError("COMPOSITION_CANARY")))
    with pytest.raises(RuntimeError, match="COMPOSITION_CANARY"):
        factory.build_ai_client(SimpleNamespace(LLM_FALLBACKS_ENABLED=True))
    assert events == ["one"]


def test_fallback_close_all_reverse_once_and_propagate_first():
    events: list[str] = []
    def provider(name, fails=False):
        def close():
            events.append(name)
            if fails:
                raise RuntimeError("CLOSE_CANARY")
        return SimpleNamespace(provider_name=name, close=close)
    client = FallbackAIClient([provider("one"), provider("two", True), provider("three")])
    with pytest.raises(RuntimeError, match="CLOSE_CANARY"):
        client.close()
    client.close()
    assert events == ["three", "two", "one"]


@pytest.mark.parametrize("failure", ["http", "options", "sdk", "aio", "request", "success"])
def test_gemini_transport_closed_at_every_acquisition_stage(monkeypatch, failure):
    events: list[str] = []
    client = gemini_module.GeminiClient(SimpleNamespace(
        GEMINI_API_KEY="test-only", GEMINI_MODEL_NAME="gemini-test",
        OPENAI_COMPAT_TIMEOUT=3.0, GEMINI_TEMPERATURE=0.0, GEMINI_MAX_TOKENS=8,
    ))

    class HTTP:
        def __init__(self, **_):
            events.append("http_open")
            if failure == "http":
                raise RuntimeError("HTTP_CANARY")

        async def aclose(self):
            events.append("http_close")

    class AIO:
        def __init__(self):
            self.models = self

        async def generate_content(self, **_):
            events.append("request")
            if failure == "request":
                raise RuntimeError("REQUEST_CANARY")
            return SimpleNamespace(text="ok")

        async def aclose(self):
            events.append("aio_close")

    class SDK:
        def __init__(self, **_):
            events.append("sdk_open")
            if failure == "sdk":
                raise RuntimeError("SDK_CANARY")
            self._aio = AIO()

        @property
        def aio(self):
            if failure == "aio":
                raise RuntimeError("AIO_CANARY")
            return self._aio

        def close(self):
            events.append("sdk_close")

    monkeypatch.setattr(gemini_module.httpx, "AsyncClient", HTTP)
    original_options = gemini_module.types.HttpOptions
    def options(**kwargs):
        events.append("options")
        if failure == "options":
            raise RuntimeError("OPTIONS_CANARY")
        return original_options(**{key: value for key, value in kwargs.items()
                                   if key != "httpx_async_client"})
    monkeypatch.setattr(gemini_module.types, "HttpOptions", options)
    monkeypatch.setattr(gemini_module.genai, "Client", SDK)

    async def attempt():
        return await client.attempt_async(
            operation="health", value="", child_budget=LLMCallBudgetV1.from_seconds(2),
            generation={},
        )
    if failure == "success":
        assert asyncio.run(attempt()) == "ok"
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(attempt())
    assert events.count("http_close") == (0 if failure == "http" else 1)
    assert events.count("sdk_close") == (1 if failure in {"aio", "request", "success"} else 0)
    assert events.count("aio_close") == (1 if failure in {"request", "success"} else 0)
