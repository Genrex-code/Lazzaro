"""Fachada controlada para acceso productivo a uno o mas proveedores LLM."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import logging
import threading
from typing import Any

from ai_engine.ai_client import BaseAIClient
from ai_engine.llm_call_control import (
    LLMCallBudgetV1,
    LLMCallExecutorV1,
    LLMCallResultV1,
    LLMCallTerminalV1,
)
from ai_engine.prompt_plan import PromptPlanV1, PromptProjectionUnsupported
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure


logger = get_logger(__name__)


class FallbackAIClient(BaseAIClient):
    """Fachada delgada; toda calendarizacion pertenece a LLMCallExecutorV1."""

    provider_name = "fallback"

    def __init__(
        self,
        clients: Iterable[BaseAIClient],
        settings: Any | None = None,
        *,
        executor: LLMCallExecutorV1 | None = None,
    ) -> None:
        self.clients = [client for client in clients if client is not None]
        if not self.clients:
            raise ValueError("FallbackAIClient requiere al menos un cliente LLM.")
        self.providers = tuple(self.clients)
        self.settings = settings or getattr(self.clients[0], "settings", None)
        self.executor = executor or LLMCallExecutorV1()
        self.provider_order = [
            str(getattr(client, "provider_name", client.__class__.__name__)).strip()
            for client in self.clients
        ]
        self.provider_name = self.provider_order[0]
        self.fallback_chain_name = "fallback:" + "->".join(self.provider_order)
        self._provider_local = threading.local()
        self.last_provider_used = None
        self._close_lock = threading.Lock()
        self._close_attempted = False

    @property
    def last_provider_used(self) -> str | None:
        return getattr(self._provider_local, "value", None)

    @last_provider_used.setter
    def last_provider_used(self, value: str | None) -> None:
        self._provider_local.value = value

    def new_budget(self) -> LLMCallBudgetV1:
        total = getattr(self.settings, "LLM_TOTAL_BUDGET_SECONDS", 45.0)
        return LLMCallBudgetV1.from_seconds(total)

    def generate(self, prompt: str, **kwargs: Any) -> str:
        result = self.execute_text_result(prompt, generation=kwargs)
        return result.final_text or ""

    def generate_plan(self, plan: PromptPlanV1, **kwargs: Any) -> str:
        result = self.execute_plan_result(plan, generation=kwargs)
        if result.terminal is LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER:
            raise PromptProjectionUnsupported(
                "Ningun proveedor configurado soporta PromptPlanV1"
            )
        return result.final_text or ""

    def execute_text_result(
        self,
        prompt: str,
        *,
        budget: LLMCallBudgetV1 | None = None,
        generation: dict[str, Any] | None = None,
        providers: Sequence[object] | None = None,
    ) -> LLMCallResultV1:
        return self._execute_sync(
            "text",
            prompt,
            budget=budget,
            generation=generation,
            providers=providers,
        )

    def execute_plan_result(
        self,
        plan: PromptPlanV1,
        *,
        budget: LLMCallBudgetV1 | None = None,
        generation: dict[str, Any] | None = None,
        providers: Sequence[object] | None = None,
    ) -> LLMCallResultV1:
        if not isinstance(plan, PromptPlanV1):
            raise TypeError("execute_plan_result requiere PromptPlanV1")
        return self._execute_sync(
            "plan",
            plan,
            budget=budget,
            generation=generation,
            providers=providers,
        )

    async def execute_plan_result_async(
        self,
        plan: PromptPlanV1,
        *,
        budget: LLMCallBudgetV1 | None = None,
        generation: dict[str, Any] | None = None,
        providers: Sequence[object] | None = None,
    ) -> LLMCallResultV1:
        if not isinstance(plan, PromptPlanV1):
            raise TypeError("execute_plan_result_async requiere PromptPlanV1")
        return await self._execute_async(
            "plan",
            plan,
            budget=budget,
            generation=generation,
            providers=providers,
        )

    async def execute_text_result_async(
        self,
        prompt: str,
        *,
        budget: LLMCallBudgetV1 | None = None,
        generation: dict[str, Any] | None = None,
        providers: Sequence[object] | None = None,
    ) -> LLMCallResultV1:
        return await self._execute_async(
            "text",
            prompt,
            budget=budget,
            generation=generation,
            providers=providers,
        )

    def health_check(self) -> bool:
        return self._execute_sync("health", "").succeeded

    async def health_check_async(self) -> bool:
        return (await self._execute_async("health", "")).succeeded

    def _execute_sync(
        self,
        operation: str,
        value: object,
        *,
        budget: LLMCallBudgetV1 | None = None,
        generation: dict[str, Any] | None = None,
        providers: Sequence[object] | None = None,
    ) -> LLMCallResultV1:
        result = self.executor.execute_sync(
            tuple(providers) if providers is not None else self.providers,
            operation=operation,
            value=value,
            budget=budget or self.new_budget(),
            generation=generation,
            audit_sink=self._record_llm_audit,
        )
        self._project_result(result)
        return result

    async def _execute_async(
        self,
        operation: str,
        value: object,
        *,
        budget: LLMCallBudgetV1 | None = None,
        generation: dict[str, Any] | None = None,
        providers: Sequence[object] | None = None,
    ) -> LLMCallResultV1:
        result = await self.executor.execute_async(
            tuple(providers) if providers is not None else self.providers,
            operation=operation,
            value=value,
            budget=budget or self.new_budget(),
            generation=generation,
            audit_sink=self._record_llm_audit,
        )
        self._project_result(result)
        return result

    def _project_result(self, result: LLMCallResultV1) -> None:
        self.last_provider_used = result.final_provider if result.succeeded else None
        if result.succeeded and result.final_provider != self.provider_order[0]:
            logger.warning(
                "Fallback LLM activado. Respuesta obtenida desde %s.",
                result.final_provider,
            )
        elif not result.succeeded:
            logger.warning(
                "Ruta LLM termino sin texto terminal=%s failure=%s.",
                result.terminal.value,
                result.final_failure.value if result.final_failure else None,
            )

    def close(self) -> None:
        with self._close_lock:
            if self._close_attempted:
                return
            self._close_attempted = True
        first_error: BaseException | None = None
        for client in reversed(self.clients):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                    try:
                        log_safe_boundary_failure(
                            logger,
                            component="llm_fallback.close",
                            code="UPSTREAM_PROVIDER_FAILED",
                            error=error,
                            level=logging.WARNING,
                        )
                    except Exception:
                        pass
        if first_error is not None:
            raise first_error


__all__ = ["FallbackAIClient"]
