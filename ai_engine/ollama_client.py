"""Proveedor Ollama async para diagnostico/health; PromptPlan sigue bloqueado."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ai_engine.ai_client import BaseAIClient
from ai_engine.llm_call_control import (
    LLMCallBudgetV1,
    ProviderConfigurationError,
    ProviderMalformedResponse,
    require_finite_positive,
)
from ai_engine.prompt_plan import PromptPlanV1, PromptProjectionUnsupported
from ai_engine.schemas import LLMResponse
from core.logger import get_logger


logger = get_logger(__name__)


class OllamaClient(BaseAIClient):
    """Un intento por invocación, con transporte propiedad del loop actual."""

    provider_name = "ollama"
    llm_max_attempts = 1

    def __init__(self, settings: Optional[Any] = None):
        from config.settings import Settings

        self.settings = settings or Settings()
        self.base_url = self._resolver_base_url(self.settings)
        self.model_name = self._resolver_modelo(self.settings)
        self.provider_timeout_seconds = require_finite_positive(
            getattr(self.settings, "OLLAMA_TIMEOUT_SECONDS", 120.0),
            field_name="OLLAMA_TIMEOUT_SECONDS",
        )
        self.timeout_seconds = self.provider_timeout_seconds
        logger.info("OllamaClient listo (modelo: %s).", self.model_name)

    async def attempt_async(
        self,
        *,
        operation: str,
        value: object,
        child_budget: LLMCallBudgetV1,
        generation: dict[str, Any],
    ) -> str:
        if operation == "plan":
            if not isinstance(value, PromptPlanV1):
                raise PromptProjectionUnsupported("Ollama requires PromptPlanV1")
            raise PromptProjectionUnsupported(
                "Ollama /api/generate cannot preserve PromptPlanV1 instruction authority"
            )
        timeout = child_budget.remaining_seconds()
        if timeout <= 0:
            raise TimeoutError("Provider child budget exhausted")
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
            if operation == "health":
                response = await client.get("/api/tags")
                response.raise_for_status()
                try:
                    data = response.json()
                except (ValueError, TypeError) as error:
                    raise ProviderMalformedResponse("Invalid Ollama tags envelope") from error
                if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                    raise ProviderMalformedResponse("Invalid Ollama tags envelope")
                names = {
                    str(model.get("name", "")).strip()
                    for model in data["models"]
                    if isinstance(model, dict)
                }
                return "ok" if self.model_name in names else ""
            if operation != "text":
                raise ProviderConfigurationError("Operacion Ollama no soportada")
            payload = {
                "model": self.model_name,
                "prompt": str(value),
                "stream": False,
                "options": self._build_options(generation),
            }
            if generation.get("response_mime_type") == "application/json":
                payload["format"] = self._response_format_schema()
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            try:
                data = response.json()
            except (ValueError, TypeError) as error:
                raise ProviderMalformedResponse("Invalid Ollama response envelope") from error
        if not isinstance(data, dict) or "response" not in data:
            raise ProviderMalformedResponse("Invalid Ollama response envelope")
        text = data.get("response")
        if not isinstance(text, str):
            raise ProviderMalformedResponse("Ollama response is not text")
        return text.strip()

    def close(self) -> None:
        return None

    @staticmethod
    def _resolver_base_url(settings: Any) -> str:
        base_url = getattr(settings, "OLLAMA_BASE_URL", None)
        if isinstance(base_url, str) and base_url.strip():
            return base_url.strip().rstrip("/")
        host = str(getattr(settings, "OLLAMA_HOST", "127.0.0.1")).strip()
        port = int(getattr(settings, "OLLAMA_PORT", 11434))
        return f"http://{host}:{port}"

    @staticmethod
    def _resolver_modelo(settings: Any) -> str:
        modelo = getattr(settings, "OLLAMA_MODEL_NAME", None) or getattr(
            settings,
            "DEEPSEEK_MODEL",
            "deepseek-r1:8b",
        )
        return str(modelo).strip()

    @staticmethod
    def _build_options(overrides: dict[str, Any]) -> dict[str, Any]:
        options = {
            "temperature": overrides.get("temperature", 0.7),
            "num_predict": overrides.get("max_output_tokens", 1024),
            "top_p": overrides.get("top_p"),
            "top_k": overrides.get("top_k"),
            "stop": overrides.get("stop_sequences"),
        }
        return {key: value for key, value in options.items() if value is not None}

    @staticmethod
    def _response_format_schema() -> dict[str, Any]:
        schema = LLMResponse.model_json_schema()
        schema["additionalProperties"] = False
        return schema


__all__ = ["OllamaClient"]
