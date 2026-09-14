"""
ai_client.py - Interfaz abstracta para clientes de modelos de lenguaje.
Define el contrato que deben cumplir todas las implementaciones (Gemini, OpenAI, etc.).
"""

from abc import ABC
from dataclasses import dataclass, replace
import threading
from typing import Literal

from core.logger import get_logger


logger = get_logger(__name__)


LLMCallStatus = Literal["ok", "empty", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class LLMCallAudit:
    """Metadata interna y acotada de una llamada LLM; nunca contiene prompts."""

    provider: str
    model_id: str | None
    fallback_used: bool
    status: LLMCallStatus
    error_type: str | None
    duration_ms: float
    started_ns: int

    def as_fallback(self, fallback_used: bool) -> "LLMCallAudit":
        return replace(self, fallback_used=bool(fallback_used))


class LLMAuditBuffer:
    """Buffer por hilo activado explícitamente por el dueño de una transacción."""

    _AUDIT_STORAGE_INIT_LOCK = threading.Lock()

    def _audit_local(self) -> threading.local:
        storage = getattr(self, "_llm_audit_local", None)
        if storage is None:
            with self._AUDIT_STORAGE_INIT_LOCK:
                storage = getattr(self, "_llm_audit_local", None)
                if storage is None:
                    storage = threading.local()
                    self._llm_audit_local = storage
        return storage

    def begin_audit_capture(self) -> None:
        storage = self._audit_local()
        storage.active = True
        storage.events = []

    def consume_audit_events(self) -> list[LLMCallAudit]:
        storage = self._audit_local()
        events = list(getattr(storage, "events", []))
        storage.events = []
        storage.active = False
        return events

    def _record_llm_audit(self, event: LLMCallAudit) -> bool:
        """Registra metadata sin permitir que la telemetría altere la llamada."""
        try:
            storage = self._audit_local()
            if not bool(getattr(storage, "active", False)):
                return False
            events = getattr(storage, "events", None)
            if not isinstance(events, list):
                events = []
                storage.events = events
            events.append(event)
            return True
        except Exception as exc:
            try:
                logger.warning(
                    "No se pudo registrar auditoria interna LLM; la llamada continuara. error_type=%s",
                    type(exc).__name__,
                )
            except Exception:
                pass
            return False

    @staticmethod
    def stable_error_type(error: BaseException) -> str:
        """Normaliza timeouts por tipo, sin inspeccionar ni conservar mensajes."""
        error_type = type(error).__name__ or "LLMError"
        lineage = " ".join(cls.__name__ for cls in type(error).__mro__).lower()
        if "timeout" in lineage:
            return "TimeoutError"
        return error_type

    @staticmethod
    def elapsed_ms(started_ns: int, finished_ns: int) -> float:
        return round(max(0, finished_ns - started_ns) / 1_000_000, 3)


class BaseAIClient(LLMAuditBuffer, ABC):
    """
    Cliente abstracto para interactuar con un modelo de lenguaje.
    Las implementaciones concretas se encargarán de la autenticación,
    la construcción de la petición HTTP y el parseo de la respuesta cruda.
    """

    def generate(self, prompt: str, **kwargs) -> str:
        """
        API textual legacy reservada para health checks y diagnosticos no cognitivos.

        Args:
            prompt: texto diagnostico sin contexto cognitivo.
            **kwargs: parámetros adicionales específicos del modelo
                      (temperatura, max_tokens, stop_sequences, etc.).

        Returns:
            Respuesta textual del modelo (sin procesar).
        """
        result = self._execute_self("text", prompt, kwargs)
        return result.final_text or ""

    def health_check(self) -> bool:
        """
        Verifica rápidamente si el cliente puede comunicarse con el servicio.

        Returns:
            True si el servicio está accesible, False en caso contrario.
        """
        return self._execute_self("health", "", {}).succeeded

    async def health_check_async(self) -> bool:
        """Ruta de health para lifespans que ya poseen event loop."""
        result = await self._execute_self_async("health", "", {})
        return result.succeeded

    def generate_plan(self, plan: "object", **kwargs) -> str:
        """Genera cognitivamente pasando incluso el acceso directo por el executor."""
        from ai_engine.llm_call_control import LLMCallTerminalV1
        from ai_engine.prompt_plan import PromptProjectionUnsupported

        result = self._execute_self("plan", plan, kwargs)
        if result.terminal is LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER:
            raise PromptProjectionUnsupported(
                f"{self.__class__.__name__} no soporta PromptPlanV1"
            )
        return result.final_text or ""

    def _execute_self(self, operation: str, value: object, generation: dict):
        from ai_engine.llm_call_control import LLMCallBudgetV1, LLMCallExecutorV1

        total = getattr(getattr(self, "settings", None), "LLM_TOTAL_BUDGET_SECONDS", 45.0)
        budget = LLMCallBudgetV1.from_seconds(total)
        return LLMCallExecutorV1().execute_sync(
            (self,),
            operation=operation,
            value=value,
            budget=budget,
            generation=generation,
            audit_sink=self._record_llm_audit,
        )

    async def _execute_self_async(self, operation: str, value: object, generation: dict):
        from ai_engine.llm_call_control import LLMCallBudgetV1, LLMCallExecutorV1

        total = getattr(getattr(self, "settings", None), "LLM_TOTAL_BUDGET_SECONDS", 45.0)
        budget = LLMCallBudgetV1.from_seconds(total)
        return await LLMCallExecutorV1().execute_async(
            (self,),
            operation=operation,
            value=value,
            budget=budget,
            generation=generation,
            audit_sink=self._record_llm_audit,
        )

    def close(self) -> None:
        """Libera recursos del cliente si la implementacion concreta lo requiere."""
        return None
