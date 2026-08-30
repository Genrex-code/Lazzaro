"""
openai_compatible_client.py - Cliente generico para APIs tipo OpenAI Chat Completions.

Sirve para proveedores externos como Groq, xAI, Kimi/Moonshot y DeepSeek API,
siempre que expongan un endpoint compatible con /chat/completions.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from ai_engine.ai_client import BaseAIClient, LLMCallAudit
from core.logger import get_logger

logger = get_logger(__name__)


class OpenAICompatibleClient(BaseAIClient):
    """Cliente sincronico para proveedores con contrato OpenAI-compatible."""

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
    ):
        self.provider_name = provider_name.strip().lower()
        self.api_key = api_key.strip()
        self.api_url = api_url.strip()
        self.model_name = model_name.strip()
        self.timeout = float(timeout)
        self.supports_json_format = bool(supports_json_format)
        self.settings = settings
        self.client = httpx.Client(timeout=self.timeout)

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

    def generate(self, prompt: str, **kwargs) -> str:
        """Envia un prompt al endpoint /chat/completions y devuelve contenido textual."""
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_output_tokens", kwargs.get("max_tokens", 1024)),
        }

        if kwargs.get("top_p") is not None:
            payload["top_p"] = kwargs["top_p"]
        if kwargs.get("stop_sequences") is not None:
            payload["stop"] = kwargs["stop_sequences"]
        if kwargs.get("response_mime_type") == "application/json" and self.supports_json_format:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        started_ns = time.perf_counter_ns()
        try:
            response = self.client.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            texto = self._extraer_texto(data)
            if texto:
                logger.debug(
                    "%s -> %d caracteres.",
                    self.provider_name,
                    len(texto),
                )
            else:
                logger.warning("%s devolvio texto vacio.", self.provider_name)
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
        except httpx.TimeoutException:
            finished_ns = time.perf_counter_ns()
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="timeout",
                    error_type="TimeoutError",
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            logger.warning("Timeout llamando a %s tras %.1fs.", self.provider_name, self.timeout)
            return ""
        except httpx.HTTPStatusError as e:
            finished_ns = time.perf_counter_ns()
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="error",
                    error_type=type(e).__name__,
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            logger.warning(
                "Error HTTP %s en %s: %s",
                e.response.status_code,
                self.provider_name,
                e.response.text[:240],
            )
            return ""
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
            logger.warning("Error llamando a %s: %s", self.provider_name, e)
            return ""

    def health_check(self) -> bool:
        try:
            respuesta = self.generate(
                "Responde solo con: ok",
                temperature=0.0,
                max_output_tokens=8,
                response_mime_type="text/plain",
            )
            return bool(respuesta and "ok" in respuesta.lower())
        except Exception as e:
            logger.warning("Health check fallido para %s: %s", self.provider_name, e)
            return False

    def close(self) -> None:
        client = getattr(self, "client", None)
        if client is None:
            return
        try:
            client.close()
        except Exception as e:
            logger.warning("No se pudo cerrar %s limpiamente: %s", self.provider_name, e)
        finally:
            self.client = None

    @staticmethod
    def _extraer_texto(data: dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if not choices:
            return ""

        first = choices[0] or {}
        message = first.get("message", {})
        contenido = message.get("content") if isinstance(message, dict) else None
        if isinstance(contenido, str) and contenido.strip():
            return contenido.strip()

        texto = first.get("text")
        if isinstance(texto, str) and texto.strip():
            return texto.strip()

        return ""
