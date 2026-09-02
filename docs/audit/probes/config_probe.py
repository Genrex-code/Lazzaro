"""Read-only BOOT/CONFIG audit; no provider requests or active Chroma access.

Run as a standalone process from any directory with the repository interpreter;
do not import into a server or shared runner (logger/env/network mocks are global).
The profile is loaded deliberately from the repository cwd using this probe's
environment; it does not inspect the cwd/environment of a running server.
All credentials used by
fault injections are synthetic. Output is JSON with booleans/counts/allowlisted
configuration only, never Settings repr, .env contents, exception messages, HTTP
bodies or captured human logs. PASS means a behavior was reproduced, not that
the observed behavior is necessarily desirable. No production code is patched
on disk. Temporary .env files are isolated test fixtures.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Prevent importing the application logger from opening its runtime log file.
logging.getLogger().handlers = [logging.NullHandler()]

import httpx
from dotenv import dotenv_values
from pydantic import ValidationError

from ai_engine import client_factory, gemini_client, ollama_client
from ai_engine import openai_compatible_client, oracle_client
from ai_engine.fallback_ai_client import FallbackAIClient
from config.settings import Settings


SYNTHETIC = "AUDIT_SYNTHETIC_CREDENTIAL_NOT_REAL"
PROVIDERS = set(client_factory.PROVEEDORES_SOPORTADOS)
KEY_FIELDS = (
    "GEMINI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY", "KIMI_API_KEY",
    "DEEPSEEK_API_KEY", "ORACLE_API_KEY",
)
SDK_ENV_FIELDS = (
    "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_VERTEX_BASE_URL", "GOOGLE_GENAI_CLIENT_MODE", "GOOGLE_GENAI_REPLAYS_DIRECTORY",
    "GOOGLE_GENAI_REPLAY_ID", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)


@contextmanager
def in_directory(path):
    original = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(original)


def fake_http():
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "ok"}}], "response": "ok"},
        request=httpx.Request("POST", "https://example.invalid/chat/completions"),
    )
    client.get.return_value = httpx.Response(
        200,
        json={"models": [{"name": "deepseek-r1:8b"}]},
        request=httpx.Request("GET", "https://example.invalid/api/tags"),
    )
    return client


def safe_provider(value):
    return value if value in PROVIDERS else "unrecognized"


def live_profile():
    """Inspect repo-cwd profile in this process, not a running server profile."""
    with in_directory(ROOT):
        settings = Settings()
        explicit = Settings(_env_file=ROOT / ".env")
        env_values = dotenv_values(ROOT / ".env")
        chroma_path_matches_repo_default = (
            Path(settings.CHROMA_DB_PATH).resolve() == (ROOT / "lazzaro_data").resolve()
        )
        with patch.dict(os.environ, {}, clear=True):
            file_only = Settings(_env_file=ROOT / ".env")
    tokens = (settings.AI_FALLBACK_ORDER or "").split(",")
    return {
        "settings_loaded": True,
        "profile_source": "repo_cwd_and_probe_process_environment",
        "server_process_inspected": False,
        "from_repo_cwd_matches_explicit_repo_env": settings == explicit,
        "active_matches_dotenv_and_defaults": settings == file_only,
        "gemini_model_matches_settings_default": (
            settings.GEMINI_MODEL_NAME == Settings.model_fields["GEMINI_MODEL_NAME"].default
        ),
        "chroma_path_matches_repo_default": chroma_path_matches_repo_default,
        "fields_differing_from_dotenv_and_defaults": [
            field for field in Settings.model_fields
            if getattr(settings, field) != getattr(file_only, field)
        ],
        "provider": safe_provider(settings.AI_PROVIDER),
        "fallback_order": [safe_provider(token.strip().lower()) for token in tokens if token.strip()],
        "fallbacks_enabled": settings.LLM_FALLBACKS_ENABLED,
        "credential_presence": {field: bool(getattr(settings, field)) for field in KEY_FIELDS},
        "process_overrides_present": [
            field for field in (*Settings.model_fields, "DEEPSEEEK_API_KEY")
            if any(key.lower() == field.lower() for key in os.environ)
        ],
        "ambient_sdk_env_presence": {
            field: any(key.lower() == field.lower() for key in os.environ)
            for field in SDK_ENV_FIELDS
        },
        "dotenv_credential_presence": {
            field: bool(env_values.get(field) or (
                env_values.get("DEEPSEEEK_API_KEY") if field == "DEEPSEEK_API_KEY" else None
            )) for field in KEY_FIELDS
        },
    }


def defaults_and_aliases():
    settings = Settings(_env_file=None)
    assert settings.AI_PROVIDER == "gemini" and settings.LLM_FALLBACKS_ENABLED
    alias = Settings(_env_file=None, DEEPSEEEK_API_KEY=SYNTHETIC)
    both = Settings(_env_file=None, DEEPSEEK_API_KEY="canonical", DEEPSEEEK_API_KEY="legacy")
    blank = Settings(_env_file=None, GEMINI_API_KEY="  ", AI_PROVIDER=" GEMINI ")
    assert alias.DEEPSEEK_API_KEY == SYNTHETIC
    assert both.DEEPSEEK_API_KEY == "canonical"
    assert blank.GEMINI_API_KEY is None and blank.AI_PROVIDER == "gemini"
    return {"default_provider": settings.AI_PROVIDER, "default_fallbacks_enabled": True,
            "legacy_alias_accepted": True, "canonical_alias_wins": True,
            "whitespace_normalized": True}


def precedence_and_cwd():
    with TemporaryDirectory(prefix="lazzaro-config-audit-") as temporary:
        base = Path(temporary)
        populated = base / "with-env"
        empty = base / "without-env"
        populated.mkdir()
        empty.mkdir()
        (populated / ".env").write_text(
            "AI_PROVIDER=ollama\nLLM_FALLBACKS_ENABLED=false\nGEMINI_API_KEY=" + SYNTHETIC + "\n",
            encoding="utf-8",
        )
        with in_directory(populated):
            file_settings = Settings()
            with patch.dict(os.environ, {"AI_PROVIDER": "groq"}):
                process_settings = Settings()
                argument_settings = Settings(AI_PROVIDER="kimi")
            assert file_settings.AI_PROVIDER == "ollama"
            assert process_settings.AI_PROVIDER == "groq"
            assert argument_settings.AI_PROVIDER == "kimi"
        with in_directory(empty):
            absent = Settings()
        assert absent.AI_PROVIDER == "gemini" and absent.GEMINI_API_KEY is None
    return {"precedence": ["init", "process_env", "cwd_dotenv", "defaults"],
            "changing_cwd_changes_settings": True, "dotenv_not_exported_to_process": "GEMINI_API_KEY" not in os.environ}


def invalid_validation():
    rejected = []
    for kwargs in (
        {"AI_PROVIDER": "unsupported"}, {"OLLAMA_TIMEOUT_SECONDS": 0},
        {"ORACLE_TIMEOUT": -1}, {"OPENAI_COMPAT_TIMEOUT": 0},
        {"OLLAMA_PORT": "not-an-int"}, {"LLM_FALLBACKS_ENABLED": "not-a-bool"},
    ):
        try:
            Settings(_env_file=None, **kwargs)
        except ValidationError:
            rejected.extend(kwargs)
        else:
            raise AssertionError("known invalid configuration unexpectedly accepted")
    return {"known_invalid_rejected_fields": rejected, "error_type": "ValidationError"}


def invalid_values_survive():
    nonfinite = []
    for field in ("OLLAMA_TIMEOUT_SECONDS", "ORACLE_TIMEOUT", "OPENAI_COMPAT_TIMEOUT"):
        for value in (float("nan"), float("inf")):
            settings = Settings(_env_file=None, **{field: value})
            assert not math.isfinite(getattr(settings, field))
            nonfinite.append(field)
    permissive = {
        "OLLAMA_PORT": -1, "GEMINI_MAX_TOKENS": -1,
        "GEMINI_TEMPERATURE": float("nan"), "GEMINI_MODEL_NAME": " ",
        "GROQ_API_URL": "not-an-http-url", "GROQ_MODEL": " ",
        "AI_FALLBACK_ORDER": "unsupported", "CHROMA_DB_PATH": " ",
    }
    settings = Settings(_env_file=None, **permissive)
    assert settings.GEMINI_MODEL_NAME == "" and settings.CHROMA_DB_PATH == ""
    return {"accepted_nonfinite_timeout_cases": len(nonfinite),
            "accepted_invalid_fields": list(permissive)}


def disabled_fallback_selection():
    """Intentional: architecture docs define false as first valid client only."""
    settings = Settings(
        _env_file=None, AI_PROVIDER="gemini", AI_FALLBACK_ORDER="gemini,groq",
        LLM_FALLBACKS_ENABLED=False, GEMINI_API_KEY=None, GROQ_API_KEY=SYNTHETIC,
    )
    transport = fake_http()
    with patch.object(openai_compatible_client.httpx, "Client", return_value=transport):
        client = client_factory.build_ai_client(settings)
        assert client.provider_name == "groq" and not isinstance(client, FallbackAIClient)
        description = client_factory.describe_ai_backend(settings)
        client.close()
    return {"fallbacks_enabled": False, "configured_primary": "gemini",
            "constructed_provider": "groq", "description_includes_skipped_primary": "gemini:" in description,
            "provider_requests": transport.post.call_count,
            "matches_documented_first_valid_contract": True}


def explicit_order_overrides_primary():
    """Intentional: explicit order replaces the default/AI_PROVIDER order."""
    settings = Settings(
        _env_file=None, AI_PROVIDER="gemini", AI_FALLBACK_ORDER="groq",
        LLM_FALLBACKS_ENABLED=False, GEMINI_API_KEY=SYNTHETIC, GROQ_API_KEY=SYNTHETIC,
    )
    with patch.object(openai_compatible_client.httpx, "Client", return_value=fake_http()):
        client = client_factory.build_ai_client(settings)
        assert client.provider_name == "groq"
        client.close()
    return {"configured_primary": "gemini", "explicit_order": ["groq"],
            "constructed_provider": "groq", "matches_documented_explicit_order_contract": True}


def no_credentials_failure():
    settings = Settings(_env_file=None, AI_PROVIDER="gemini", AI_FALLBACK_ORDER="gemini")
    try:
        client_factory.build_ai_client(settings)
    except ValueError:
        return {"factory_error_type": "ValueError", "failure_before_health_check": True}
    raise AssertionError("factory unexpectedly constructed unconfigured client")


def direct_gemini_none_key():
    try:
        gemini_client.GeminiClient(Settings(_env_file=None))
    except AttributeError:
        return {"direct_constructor_error_type": "AttributeError", "factory_guards_this_path": True}
    raise AssertionError("direct Gemini missing-key behavior changed")


def oracle_settings_split():
    settings = Settings(
        _env_file=None, AI_PROVIDER="gemini", AI_FALLBACK_ORDER="gemini",
        LLM_FALLBACKS_ENABLED=False, GROQ_API_KEY=SYNTHETIC,
        GROQ_API_URL="https://example.invalid/custom", ORACLE_API_KEY=None,
    )
    via_settings = oracle_client.OracleClient(settings)
    direct = oracle_client.OracleClient()
    assert via_settings.configurado and not direct.configurado
    assert via_settings.api_key == settings.GROQ_API_KEY
    assert via_settings.api_url != settings.GROQ_API_URL
    with patch.dict(os.environ, {"ORACLE_TIMEOUT": "0"}):
        zero = oracle_client.OracleClient()
    assert zero.timeout == 0
    return {"groq_key_activates_oracle_without_oracle_key": True,
            "independent_of_disabled_llm_fallbacks": True,
            "groq_url_override_ignored_by_settings_default": True,
            "direct_constructor_does_not_load_settings": True,
            "direct_constructor_accepts_zero_timeout": True}


def compatible_allocation_before_validation():
    transport = fake_http()
    with patch.object(openai_compatible_client.httpx, "Client", return_value=transport) as constructor:
        try:
            openai_compatible_client.OpenAICompatibleClient(
                provider_name="groq", api_key=SYNTHETIC,
                api_url="https://example.invalid/chat/completions", model_name="",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("blank model unexpectedly accepted")
        assert constructor.call_count == 1 and transport.close.call_count == 0
    return {"http_client_constructed": 1, "validation_error_type": "ValueError", "close_calls": 0}


def factory_partial_construction_failure():
    first = MagicMock(provider_name="gemini")
    settings = Settings(_env_file=None, AI_FALLBACK_ORDER="gemini,groq", LLM_FALLBACKS_ENABLED=True)
    with patch.object(client_factory, "_build_single_client", side_effect=[first, RuntimeError("synthetic construction failure")]):
        try:
            client_factory.build_ai_client(settings)
        except RuntimeError:
            pass
        else:
            raise AssertionError("construction failure unexpectedly swallowed")
    assert first.close.call_count == 0
    return {"successful_client_before_failure": 1, "close_calls_on_partial_failure": 0,
            "propagated_error_type": "RuntimeError"}


def health_contracts():
    gemini = object.__new__(gemini_client.GeminiClient)
    gemini.generate = MagicMock(return_value="not ok")
    compatible = object.__new__(openai_compatible_client.OpenAICompatibleClient)
    compatible.provider_name = "groq"
    compatible.generate = MagicMock(return_value="not ok")
    ollama = object.__new__(ollama_client.OllamaClient)
    ollama.client = fake_http()
    ollama.model_name = "deepseek-r1:8b"
    assert gemini.health_check() and compatible.health_check() and ollama.health_check()
    assert gemini.generate.call_count == compatible.generate.call_count == 1
    assert ollama.client.post.call_count == 0 and ollama.client.get.call_count == 1
    failed = MagicMock(provider_name="gemini")
    failed.health_check.return_value = False
    healthy = MagicMock(provider_name="groq")
    healthy.health_check.return_value = True
    chain = FallbackAIClient([failed, healthy])
    assert chain.health_check()
    assert chain.provider_name == "gemini" and chain.last_provider_used is None
    return {"remote_checks_call_generation": True, "substring_not_ok_accepted": True,
            "ollama_check_lists_models_without_generation": True,
            "fallback_health_true_on_secondary": True,
            "fallback_declared_provider_stays_primary": True}


def gemini_health_retry_contract():
    sdk = MagicMock()
    sdk.models.generate_content.side_effect = RuntimeError("synthetic health failure")
    settings = Settings(_env_file=None, GEMINI_API_KEY=SYNTHETIC)
    with patch.object(gemini_client.genai, "Client", return_value=sdk) as constructor:
        with patch.object(gemini_client.time, "sleep") as sleep:
            client = gemini_client.GeminiClient(settings)
            result = client.health_check()
            client.close()
    assert not result and sdk.models.generate_content.call_count == 4 and sleep.call_count == 4
    return {"health_result": False, "generation_attempts": 4,
            "backoff_sleep_calls_including_final_failure": 4,
            "sdk_http_options_provided": "http_options" in constructor.call_args.kwargs}


def gemini_installed_sdk_timeout():
    """Exercise installed SDK only against an in-process MockTransport."""
    observed_timeouts = []

    def respond(request):
        observed_timeouts.append(request.extensions.get("timeout"))
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"},
                            "finishReason": "STOP"}],
        })

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    async_transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    actual_constructor = gemini_client.genai.Client

    def injected_transport(**kwargs):
        # SDK creates async state even for a sync client. Own both injected
        # transports; SDK intentionally leaves custom clients open on close().
        return actual_constructor(**kwargs, http_options={
            "httpx_client": transport, "httpx_async_client": async_transport,
        })

    settings = Settings(_env_file=None, GEMINI_API_KEY=SYNTHETIC)
    try:
        with patch.object(gemini_client.genai, "Client", side_effect=injected_transport):
            client = gemini_client.GeminiClient(settings)
            try:
                configured_timeout = client.client._api_client._http_options.timeout
                result = client.health_check()
            finally:
                try:
                    asyncio.get_event_loop().run_until_complete(client.client.aio.aclose())
                finally:
                    client.close()
    finally:
        try:
            transport.close()
        finally:
            asyncio.get_event_loop().run_until_complete(async_transport.aclose())
    assert result and configured_timeout is None and len(observed_timeouts) == 1
    assert observed_timeouts[0] and all(value is None for value in observed_timeouts[0].values())
    assert transport.is_closed and async_transport.is_closed
    return {"mock_transport_requests": 1, "health_result": True,
            "sdk_default_timeout_is_none": True,
            "httpx_request_all_timeout_dimensions_none": True,
            "owned_sync_transport_closed": True, "owned_async_transport_closed": True}


def gemini_sdk_ambient_endpoint():
    observed = []

    def respond(request):
        observed.append(request.url.host == "example.invalid")
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"},
                            "finishReason": "STOP"}],
        })

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    async_transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    actual_constructor = gemini_client.genai.Client

    def injected_transport(**kwargs):
        return actual_constructor(**kwargs, http_options={
            "httpx_client": transport, "httpx_async_client": async_transport,
        })

    settings = Settings(_env_file=None, GEMINI_API_KEY=SYNTHETIC)
    try:
        with patch.dict(os.environ, {"GOOGLE_GEMINI_BASE_URL": "https://example.invalid"}):
            with patch.object(gemini_client.genai, "Client", side_effect=injected_transport):
                client = gemini_client.GeminiClient(settings)
                try:
                    result = client.health_check()
                finally:
                    try:
                        asyncio.get_event_loop().run_until_complete(client.client.aio.aclose())
                    finally:
                        client.close()
    finally:
        try:
            transport.close()
        finally:
            asyncio.get_event_loop().run_until_complete(async_transport.aclose())
    assert result and observed == [True]
    assert "GOOGLE_GEMINI_BASE_URL" not in Settings.model_fields
    assert transport.is_closed and async_transport.is_closed
    return {"mock_transport_requests": 1, "health_result": True,
            "undeclared_process_env_changes_sdk_endpoint": True,
            "settings_object_unchanged": settings.AI_PROVIDER == "gemini",
            "owned_sync_transport_closed": True, "owned_async_transport_closed": True}


def secret_log_sinks():
    """Synthetic canary only; captured messages never leave memory."""
    sdk = MagicMock()
    sdk.models.generate_content.side_effect = RuntimeError(SYNTHETIC)
    settings = Settings(_env_file=None, GEMINI_API_KEY=SYNTHETIC)
    with patch.object(gemini_client.genai, "Client", return_value=sdk):
        with patch.object(gemini_client.time, "sleep"), patch.object(gemini_client.logger, "warning") as warning:
            gemini = gemini_client.GeminiClient(settings)
            assert not gemini.health_check()
            gemini_echo = any(SYNTHETIC in repr(call.args) for call in warning.call_args_list)
            gemini.close()
    transport = fake_http()
    transport.post.return_value = httpx.Response(
        401, text=SYNTHETIC,
        request=httpx.Request("POST", "https://example.invalid/chat/completions"),
    )
    with patch.object(openai_compatible_client.httpx, "Client", return_value=transport):
        with patch.object(openai_compatible_client.logger, "warning") as warning:
            client = openai_compatible_client.OpenAICompatibleClient(
                provider_name="groq", api_key=SYNTHETIC,
                api_url="https://example.invalid/chat/completions", model_name="audit-model",
            )
            assert not client.health_check()
            body_echo = any(SYNTHETIC in repr(call.args) for call in warning.call_args_list)
            client.close()
    url_settings = Settings(_env_file=None, OLLAMA_BASE_URL="http://user:" + SYNTHETIC + "@example.invalid")
    with patch.object(ollama_client.httpx, "Client", return_value=fake_http()):
        with patch.object(ollama_client.logger, "info") as info:
            client = ollama_client.OllamaClient(url_settings)
            url_echo = any(SYNTHETIC in repr(call.args) for call in info.call_args_list)
            client.close()
    assert gemini_echo and body_echo and url_echo
    return {"synthetic_only": True, "gemini_exception_message_reaches_logger": gemini_echo,
            "compatible_http_body_reaches_logger": body_echo,
            "ollama_url_userinfo_reaches_logger": url_echo,
            "real_credential_disclosure_tested": False}


CHECKS = (
    defaults_and_aliases, precedence_and_cwd, invalid_validation,
    invalid_values_survive, disabled_fallback_selection, explicit_order_overrides_primary,
    no_credentials_failure, direct_gemini_none_key, oracle_settings_split,
    compatible_allocation_before_validation, factory_partial_construction_failure,
    health_contracts, gemini_health_retry_contract, gemini_installed_sdk_timeout,
    gemini_sdk_ambient_endpoint, secret_log_sinks,
)


def main():
    report = {"scope": "BOOT/CONFIG", "network_allowed": False, "checks": []}
    failed = 0
    # Initialize Windows' event-loop self-pipe before blocking socket connects.
    # This loop only closes owned async transports; it makes no provider calls.
    # Last-resort guard: even a missed HTTP mock cannot connect to a provider.
    with asyncio.Runner(), patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
        try:
            report["live_profile"] = live_profile()
        except Exception as error:
            report["live_profile"] = {"settings_loaded": False, "error_type": type(error).__name__}
            failed += 1
        for check in CHECKS:
            try:
                with patch.dict(os.environ, {}, clear=True):
                    evidence = check()
                report["checks"].append({"name": check.__name__, "status": "PASS", "evidence": evidence})
            except Exception as error:
                failed += 1
                report["checks"].append({"name": check.__name__, "status": "FAIL", "error_type": type(error).__name__})
    report["passed"] = len(CHECKS) - sum(row["status"] == "FAIL" for row in report["checks"])
    report["failed"] = failed
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
