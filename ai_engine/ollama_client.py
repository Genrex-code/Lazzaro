"""
ollama_client.py - Cliente BaseAIClient para modelos locales servidos por Ollama.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import httpx

from ai_engine.ai_client import BaseAIClient, LLMCallAudit
from ai_engine.schemas import LLMResponse
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure

logger = get_logger(__name__)


class OllamaClient(BaseAIClient):
    """Cliente sincronico para usar DeepSeek R1 u otros modelos locales via Ollama."""
    provider_name = "ollama"

    def __init__(self, settings: Optional[Any] = None):
        from config.settings import Settings

        self.settings = settings or Settings()
        self.base_url = self._resolver_base_url(self.settings)
        self.model_name = self._resolver_modelo(self.settings)
        self.timeout_seconds = float(getattr(self.settings, "OLLAMA_TIMEOUT_SECONDS", 120.0))
        self.client = httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds)

        logger.info(
            "OllamaClient listo (modelo: %s).",
            self.model_name,
        )

    def generate(self, prompt: str, **kwargs) -> str:
        """Envia un prompt a Ollama y devuelve el texto generado."""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": self._build_options(kwargs),
        }

        if kwargs.get("response_mime_type") == "application/json":
            payload["format"] = self._response_format_schema()

        started_ns = time.perf_counter_ns()
        try:
            response = self.client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            texto = self._limpiar_respuesta(str(data.get("response", "")))
            if texto:
                logger.debug("Ollama -> %d caracteres.", len(texto))
            else:
                logger.warning("Ollama devolvio texto vacio.")
            finished_ns = time.perf_counter_ns()
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="ok" if texto else "empty",
                    error_type=None if texto else "EmptyResponse",
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            return texto
        except Exception as e:
            finished_ns = time.perf_counter_ns()
            error_type = self.stable_error_type(e)
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="timeout" if error_type == "TimeoutError" else "error",
                    error_type=error_type,
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            log_safe_boundary_failure(
                logger,
                component="ollama.generate",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
            )
            return ""

    def health_check(self) -> bool:
        """Verifica que Ollama este accesible y que el modelo configurado exista."""
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            modelos = data.get("models", [])
            nombres = {str(modelo.get("name", "")).strip() for modelo in modelos}
            return bool(nombres) and self.model_name in nombres
        except Exception as e:
            log_safe_boundary_failure(
                logger,
                component="ollama.health_check",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
            )
            return False

    def close(self) -> None:
        """Cierra la sesion HTTP persistente."""
        client = getattr(self, "client", None)
        if client is None:
            return

        try:
            client.close()
        except Exception as e:
            log_safe_boundary_failure(
                logger,
                component="ollama.close",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
                level=logging.WARNING,
            )
        finally:
            self.client = None

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
    def _limpiar_respuesta(texto: str) -> str:
        texto_limpio = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL | re.IGNORECASE)
        texto_limpio = texto_limpio.replace("```json", "").replace("```", "").strip()
        return texto_limpio

    @staticmethod
    def _response_format_schema() -> dict[str, Any]:
        schema = LLMResponse.model_json_schema()
        schema["additionalProperties"] = False
        return schema
