"""Proveedor Gemini async de un solo intento bajo control ROOT-021."""

from __future__ import annotations

from typing import Any, Optional

import httpx
from google import genai
from google.genai import types

from ai_engine.ai_client import BaseAIClient
from ai_engine.llm_call_control import (
    LLMCallBudgetV1,
    ProviderConfigurationError,
    RouteDeadlineExceeded,
    require_finite_positive,
)
from ai_engine.prompt_plan import PromptPlanV1
from ai_engine.provider_prompt_projection import project_gemini_parts
from core.logger import get_logger


logger = get_logger(__name__)


class GeminiClient(BaseAIClient):
    """Proyecta y ejecuta exactamente una operación Gemini por invocación."""

    provider_name = "gemini"
    llm_max_attempts = 4

    def __init__(self, settings: Optional[Any] = None):
        from config.settings import Settings

        self.settings = settings or Settings()
        api_key = str(getattr(self.settings, "GEMINI_API_KEY", "") or "").strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY no esta definida en la configuracion.")
        self.api_key = api_key
        self.model_name = self._normalizar_modelo(
            getattr(self.settings, "GEMINI_MODEL_NAME", "gemini-2.5-flash")
        )
        self.provider_timeout_seconds = require_finite_positive(
            getattr(self.settings, "OPENAI_COMPAT_TIMEOUT", 45.0),
            field_name="Gemini provider cap",
        )
        self.generation_config = types.GenerateContentConfig(
            temperature=getattr(self.settings, "GEMINI_TEMPERATURE", 0.7),
            max_output_tokens=getattr(self.settings, "GEMINI_MAX_TOKENS", 1024),
            top_p=0.95,
            top_k=40,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
                maximum_remote_calls=None,
            ),
        )
        logger.info("GeminiClient listo (modelo: %s).", self.model_name)

    async def attempt_async(
        self,
        *,
        operation: str,
        value: object,
        child_budget: LLMCallBudgetV1,
        generation: dict[str, Any],
    ) -> str:
        if not isinstance(child_budget, LLMCallBudgetV1):
            raise TypeError("Gemini requiere LLMCallBudgetV1")
        if operation == "plan":
            if not isinstance(value, PromptPlanV1):
                raise TypeError("Gemini plan requiere PromptPlanV1")
            system_instruction, projected_contents = project_gemini_parts(value)
            contents: Any = [
                types.Content(role=role, parts=[types.Part(text=content)])
                for role, content in projected_contents
            ]
            base = self._build_generation_config(generation).model_dump(exclude_none=True)
            base["system_instruction"] = system_instruction
            config = types.GenerateContentConfig(**base)
        elif operation in {"text", "health"}:
            contents = "Responde solo con: ok" if operation == "health" else str(value)
            overrides = dict(generation)
            if operation == "health":
                overrides.update(
                    temperature=0.0,
                    max_output_tokens=8,
                    response_mime_type="text/plain",
                )
            config = self._build_generation_config(overrides)
        else:
            raise ProviderConfigurationError("Operacion Gemini no soportada")

        remaining_ns = child_budget.remaining_ns()
        if remaining_ns < 1_000_000:
            raise RouteDeadlineExceeded("Insuficiente presupuesto para timeout Gemini positivo")
        timeout_ms = remaining_ns // 1_000_000
        if timeout_ms < 1:
            raise RouteDeadlineExceeded("Timeout Gemini agotado")

        legacy_client = getattr(self, "client", None)
        if legacy_client is not None:
            # Dobles de proyección pre-ROOT-021; sigue siendo una sola tentativa.
            response = legacy_client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
            text = self._extraer_texto(response)
            return "ok" if operation == "health" and "ok" in text.lower() else (
                "" if operation == "health" else text
            )

        # El AsyncClient pertenece al loop actual. Forzar HTTPX evita el retry
        # oculto del path aiohttp instalado. El SDK queda explícitamente en 1 intento.
        http_client = httpx.AsyncClient(timeout=timeout_ms / 1000.0)
        sdk_client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_ms),
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_async_client=http_client,
            ),
        )
        aio_client = sdk_client.aio
        try:
            response = await aio_client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
            text = self._extraer_texto(response)
            return "ok" if operation == "health" and "ok" in text.lower() else (
                "" if operation == "health" else text
            )
        finally:
            try:
                await aio_client.aclose()
            finally:
                # google-genai considera caller-owned al cliente custom.
                await http_client.aclose()

    def close(self) -> None:
        """Idempotente: no hay transporte persistente que cerrar."""
        return None

    def _build_generation_config(self, overrides: dict[str, Any]) -> types.GenerateContentConfig:
        base = self.generation_config.model_dump(exclude_none=True)
        new_kwargs = {
            "temperature": overrides.get("temperature", base.get("temperature")),
            "max_output_tokens": overrides.get("max_output_tokens", base.get("max_output_tokens")),
            "top_p": overrides.get("top_p", base.get("top_p")),
            "top_k": overrides.get("top_k", base.get("top_k")),
            "stop_sequences": overrides.get("stop_sequences"),
            "response_mime_type": overrides.get("response_mime_type"),
            "automatic_function_calling": overrides.get(
                "automatic_function_calling",
                base.get("automatic_function_calling"),
            ),
        }
        return types.GenerateContentConfig(
            **{key: value for key, value in new_kwargs.items() if value is not None}
        )

    @staticmethod
    def _normalizar_modelo(model_name: str) -> str:
        return str(model_name).removeprefix("models/").strip()

    @staticmethod
    def _extraer_texto(response: Any) -> str:
        texto = getattr(response, "text", None)
        if isinstance(texto, str) and texto.strip():
            return texto.strip()
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            fragmentos = [
                str(getattr(part, "text")).strip()
                for part in parts
                if isinstance(getattr(part, "text", None), str)
                and str(getattr(part, "text")).strip()
            ]
            if fragmentos:
                return "\n".join(fragmentos)
        return ""


__all__ = ["GeminiClient"]
