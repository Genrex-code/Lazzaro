"""Proveedor externo de Capa 3 bajo el control de deadline ROOT-021."""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from ai_engine.ai_client import LLMAuditBuffer
from ai_engine.llm_call_control import (
    LLMCallBudgetV1,
    LLMCallExecutorV1,
    ProviderConfigurationError,
    ProviderMalformedResponse,
    require_finite_positive,
)
from ai_engine.prompt_plan import PromptPlanV1
from ai_engine.provider_prompt_projection import project_openai_messages


class OracleClient(LLMAuditBuffer):
    """Oracle compatible con OpenAI; cada invocación es exactamente un POST."""

    provider_name = "oracle_external"
    llm_max_attempts = 1

    def __init__(self, settings: Optional[Any] = None):
        self.settings = settings
        if settings is not None:
            self.api_url = getattr(
                settings,
                "ORACLE_API_URL",
                getattr(settings, "GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"),
            )
            self.api_key = getattr(settings, "ORACLE_API_KEY", None) or getattr(
                settings, "GROQ_API_KEY", ""
            )
            self.model_name = getattr(settings, "ORACLE_MODEL", None) or getattr(
                settings, "GROQ_MODEL", "llama-3.1-8b-instant"
            )
            timeout_raw: object = getattr(settings, "ORACLE_TIMEOUT", 15.0)
        else:
            self.api_url = os.getenv(
                "ORACLE_API_URL",
                "https://api.groq.com/openai/v1/chat/completions",
            )
            self.api_key = os.getenv("ORACLE_API_KEY") or os.getenv("GROQ_API_KEY", "")
            self.model_name = os.getenv("ORACLE_MODEL") or os.getenv(
                "GROQ_MODEL", "llama-3.1-8b-instant"
            )
            timeout_raw = os.getenv("ORACLE_TIMEOUT", "15.0")
        if isinstance(timeout_raw, bool):
            raise ValueError("ORACLE_TIMEOUT debe ser finito y positivo.")
        try:
            timeout_numeric = float(timeout_raw)
        except (TypeError, ValueError) as error:
            raise ValueError("ORACLE_TIMEOUT debe ser finito y positivo.") from error
        self.provider_timeout_seconds = require_finite_positive(
            timeout_numeric,
            field_name="ORACLE_TIMEOUT",
        )
        self.timeout = self.provider_timeout_seconds
        self._supports_json_format = True

    @property
    def configurado(self) -> bool:
        return bool(self.api_key and str(self.api_key).strip())

    async def attempt_async(
        self,
        *,
        operation: str,
        value: object,
        child_budget: LLMCallBudgetV1,
        generation: dict[str, Any],
    ) -> str:
        if operation != "plan" or not isinstance(value, PromptPlanV1):
            raise ProviderConfigurationError("Oracle externo requiere PromptPlanV1")
        if not self.configurado:
            raise ProviderConfigurationError("Oracle externo no configurado")
        messages = project_openai_messages(value)
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": generation.get("temperature", 0.1),
        }
        if self._supports_json_format:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = child_budget.remaining_seconds()
        if timeout <= 0:
            raise TimeoutError("Oracle child budget exhausted")
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            try:
                data = response.json()
            except (ValueError, TypeError) as error:
                raise ProviderMalformedResponse("Invalid Oracle provider envelope") from error
        return self._extract_text(data)

    def consultar_plan(self, plan: PromptPlanV1) -> Optional[str]:
        """Compatibilidad sync; aun así usa el executor y un budget finito."""
        if not isinstance(plan, PromptPlanV1):
            raise TypeError("consultar_plan requiere PromptPlanV1")
        total = getattr(self.settings, "LLM_TOTAL_BUDGET_SECONDS", 45.0)
        result = LLMCallExecutorV1().execute_sync(
            (self,),
            operation="plan",
            value=plan,
            budget=LLMCallBudgetV1.from_seconds(total),
            generation={"temperature": 0.1},
            audit_sink=self._record_llm_audit,
        )
        return result.final_text

    def close(self) -> None:
        return None

    @staticmethod
    def _extract_text(data: object) -> str:
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("Oracle envelope must be an object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderMalformedResponse("Oracle envelope has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise ProviderMalformedResponse("Oracle envelope has no message content")
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderMalformedResponse("Oracle content is not text")
        return content.strip()


__all__ = ["OracleClient"]
