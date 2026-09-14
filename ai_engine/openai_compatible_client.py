"""Proveedor OpenAI-compatible async de un solo intento."""

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
from ai_engine.prompt_plan import PromptPlanV1
from ai_engine.provider_prompt_projection import project_openai_messages
from core.logger import get_logger


logger = get_logger(__name__)


class OpenAICompatibleClient(BaseAIClient):
    """Proyecta una solicitud y realiza exactamente un POST por invocación."""

    llm_max_attempts = 1

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str,
        api_url: str,
        model_name: str,
        timeout: float = 45.0,
        supports_json_format: bool = True,
        settings: Optional[Any] = None,
    ) -> None:
        self.provider_name = provider_name.strip().lower()
        self.api_key = api_key.strip()
        self.api_url = api_url.strip()
        self.model_name = model_name.strip()
        self.provider_timeout_seconds = require_finite_positive(
            timeout,
            field_name=f"{self.provider_name} timeout",
        )
        # Alias de lectura para compatibilidad; no representa un cliente persistente.
        self.timeout = self.provider_timeout_seconds
        self.supports_json_format = bool(supports_json_format)
        self.settings = settings
        if not self.api_key:
            raise ValueError(f"{self.provider_name} requiere API key.")
        if not self.api_url:
            raise ValueError(f"{self.provider_name} requiere api_url.")
        if not self.model_name:
            raise ValueError(f"{self.provider_name} requiere model_name.")
        logger.info(
            "OpenAICompatibleClient listo (proveedor: %s, modelo: %s).",
            self.provider_name,
            self.model_name,
        )

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
                raise TypeError("OpenAI-compatible plan requiere PromptPlanV1")
            messages = project_openai_messages(value)
        elif operation in {"text", "health"}:
            prompt = "Responde solo con: ok" if operation == "health" else str(value)
            messages = [{"role": "user", "content": prompt}]
        else:
            raise ProviderConfigurationError("Operacion OpenAI-compatible no soportada")

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.0 if operation == "health" else generation.get("temperature", 0.7),
            "max_tokens": 8 if operation == "health" else generation.get(
                "max_output_tokens", generation.get("max_tokens", 1024)
            ),
        }
        if generation.get("top_p") is not None:
            payload["top_p"] = generation["top_p"]
        if generation.get("stop_sequences") is not None:
            payload["stop"] = generation["stop_sequences"]
        if generation.get("response_mime_type") == "application/json" and self.supports_json_format:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = child_budget.remaining_seconds()
        if timeout <= 0:
            raise TimeoutError("Provider child budget exhausted")
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            try:
                data = response.json()
            except (ValueError, TypeError) as error:
                raise ProviderMalformedResponse("Invalid provider JSON envelope") from error
        text = self._extraer_texto_estricto(data)
        if operation == "health":
            return "ok" if "ok" in text.lower() else ""
        return text

    def close(self) -> None:
        return None

    @staticmethod
    def _extraer_texto_estricto(data: object) -> str:
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("Provider envelope must be an object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderMalformedResponse("Provider envelope has no choices")
        first = choices[0]
        message = first.get("message")
        if isinstance(message, dict) and "content" in message:
            content = message.get("content")
            if not isinstance(content, str):
                raise ProviderMalformedResponse("Provider content is not text")
            return content.strip()
        if "text" in first:
            text = first.get("text")
            if not isinstance(text, str):
                raise ProviderMalformedResponse("Provider text is invalid")
            return text.strip()
        raise ProviderMalformedResponse("Provider choice has no text field")

    @staticmethod
    def _extraer_texto(data: dict[str, Any]) -> str:
        """Compatibilidad de proyección local sin relajar el intento productivo."""
        try:
            return OpenAICompatibleClient._extraer_texto_estricto(data)
        except ProviderMalformedResponse:
            return ""


__all__ = ["OpenAICompatibleClient"]
