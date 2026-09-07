"""
fallback_ai_client.py - Cadena de respaldo para clientes LLM.

El resto del sistema llama generate() una sola vez. Este wrapper intenta el
cliente primario y, si no produce texto util, pasa al siguiente proveedor.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
import threading
import time
from typing import Any

from ai_engine.ai_client import BaseAIClient, LLMCallAudit
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure

logger = get_logger(__name__)


class FallbackAIClient(BaseAIClient):
    """Cliente compuesto que prueba varios proveedores en orden."""

    provider_name = "fallback"

    def __init__(self, clients: Iterable[BaseAIClient], settings: Any | None = None):
        self.clients = [client for client in clients if client is not None]
        if not self.clients:
            raise ValueError("FallbackAIClient requiere al menos un cliente LLM.")

        self.settings = settings or getattr(self.clients[0], "settings", None)
        self.provider_order = [
            str(getattr(client, "provider_name", client.__class__.__name__)).strip()
            for client in self.clients
        ]
        # Mantener el nombre del proveedor primario evita activar reglas de politica
        # de otros fallbacks (por ejemplo Ollama) antes de saber si se usaran.
        self.provider_name = self.provider_order[0]
        self.fallback_chain_name = "fallback:" + "->".join(self.provider_order)
        self._provider_local = threading.local()
        self.last_provider_used: str | None = None

    @property
    def last_provider_used(self) -> str | None:
        return getattr(self._provider_local, "value", None)

    @last_provider_used.setter
    def last_provider_used(self, value: str | None) -> None:
        self._provider_local.value = value

    def generate(self, prompt: str, **kwargs) -> str:
        """Intenta generar texto con cada proveedor hasta obtener una respuesta no vacia."""
        for index, client in enumerate(self.clients):
            proveedor = str(getattr(client, "provider_name", client.__class__.__name__))
            model_id = str(getattr(client, "model_name", "")).strip() or None
            started_ns = time.perf_counter_ns()
            begin_capture = getattr(client, "begin_audit_capture", None)
            consume_events = getattr(client, "consume_audit_events", None)
            if callable(begin_capture):
                try:
                    begin_capture()
                except Exception as e:
                    logger.warning(
                        "No se pudo iniciar auditoria interna de %s: %s",
                        proveedor,
                        type(e).__name__,
                    )
            raised_error: Exception | None = None
            try:
                texto = client.generate(prompt, **kwargs)
            except Exception as e:
                log_safe_boundary_failure(
                    logger,
                    component="llm_fallback.generate",
                    code="UPSTREAM_PROVIDER_FAILED",
                    error=e,
                    level=logging.WARNING,
                )
                raised_error = e
                texto = ""

            finished_ns = time.perf_counter_ns()
            child_events = []
            if callable(consume_events):
                try:
                    child_events = consume_events()
                except Exception as e:
                    logger.warning(
                        "No se pudo consumir auditoria interna de %s: %s",
                        proveedor,
                        type(e).__name__,
                    )
            child_events = [
                event
                for event in child_events
                if isinstance(event, LLMCallAudit)
            ] if isinstance(child_events, list) else []
            fallback_used = index > 0
            if child_events:
                for event in child_events:
                    self._record_llm_audit(event.as_fallback(fallback_used))
            elif raised_error is not None:
                error_type = self.stable_error_type(raised_error)
                self._record_llm_audit(
                    LLMCallAudit(
                        provider=proveedor,
                        model_id=model_id,
                        fallback_used=fallback_used,
                        status="timeout" if error_type == "TimeoutError" else "error",
                        error_type=error_type,
                        duration_ms=self.elapsed_ms(started_ns, finished_ns),
                        started_ns=started_ns,
                    )
                )
            else:
                has_text = isinstance(texto, str) and bool(texto.strip())
                self._record_llm_audit(
                    LLMCallAudit(
                        provider=proveedor,
                        model_id=model_id,
                        fallback_used=fallback_used,
                        status="ok" if has_text else "empty",
                        error_type=None if has_text else "EmptyResponse",
                        duration_ms=self.elapsed_ms(started_ns, finished_ns),
                        started_ns=started_ns,
                    )
                )

            if isinstance(texto, str) and texto.strip():
                self.last_provider_used = proveedor
                if proveedor != self.provider_order[0]:
                    logger.warning("Fallback LLM activado. Respuesta obtenida desde %s.", proveedor)
                return texto

            logger.warning("Proveedor LLM %s no produjo texto util; probando fallback.", proveedor)

        self.last_provider_used = None
        logger.error("Todos los proveedores LLM fallaron o devolvieron texto vacio.")
        return ""

    def health_check(self) -> bool:
        """El sistema se considera operativo si al menos un proveedor responde."""
        for client in self.clients:
            proveedor = str(getattr(client, "provider_name", client.__class__.__name__))
            try:
                if client.health_check():
                    logger.info("Health check LLM OK usando %s.", proveedor)
                    return True
            except Exception as e:
                log_safe_boundary_failure(
                    logger,
                    component="llm_fallback.health_check",
                    code="UPSTREAM_PROVIDER_FAILED",
                    error=e,
                    level=logging.WARNING,
                )
        return False

    def close(self) -> None:
        for client in self.clients:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    log_safe_boundary_failure(
                        logger,
                        component="llm_fallback.close",
                        code="UPSTREAM_PROVIDER_FAILED",
                        error=e,
                        level=logging.WARNING,
                    )
