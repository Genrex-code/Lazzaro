"""
gemini_client.py - Cliente robusto para Google Gemini usando google.genai.
"""

import random
import re
import time
import logging
from typing import Any, Optional

from google import genai
from google.genai import types

from ai_engine.ai_client import BaseAIClient, LLMCallAudit
from ai_engine.prompt_plan import PromptPlanV1
from ai_engine.provider_prompt_projection import project_gemini_parts
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure

logger = get_logger(__name__)


class GeminiClient(BaseAIClient):
    """Cliente para Google Gemini con reintentos y manejo basico de rate limits."""
    provider_name = "gemini"

    def __init__(self, settings: Optional[Any] = None):
        from config.settings import Settings

        self.settings = settings or Settings()

        api_key = getattr(self.settings, "GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY no esta definida en la configuracion.")

        self.model_name = self._normalizar_modelo(
            getattr(self.settings, "GEMINI_MODEL_NAME", "gemini-2.5-flash")
        )
        self.client = genai.Client(api_key=api_key)
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

    def generate(self, prompt: str, **kwargs) -> str:
        """API textual legacy para health checks/diagnosticos no cognitivos."""
        gen_config = self._build_generation_config(kwargs)
        return self._generate_content(prompt, gen_config)

    def generate_plan(self, plan: PromptPlanV1, **kwargs) -> str:
        """Project PromptPlanV1 using Gemini's native system instruction channel."""
        system_instruction, projected_contents = project_gemini_parts(plan)
        contents = [
            types.Content(role=role, parts=[types.Part(text=content)])
            for role, content in projected_contents
        ]
        base = self._build_generation_config(kwargs).model_dump(exclude_none=True)
        base["system_instruction"] = system_instruction
        gen_config = types.GenerateContentConfig(**base)
        return self._generate_content(contents, gen_config)

    def _generate_content(self, contents: Any, gen_config: types.GenerateContentConfig) -> str:
        max_retries = 4
        backoff = 1.0

        for intento in range(1, max_retries + 1):
            started_ns = time.perf_counter_ns()
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=gen_config,
                )
                texto = self._extraer_texto(response)
                if texto:
                    finished_ns = time.perf_counter_ns()
                    self._record_llm_audit(
                        LLMCallAudit(
                            provider=self.provider_name,
                            model_id=self.model_name,
                            fallback_used=False,
                            status="ok",
                            error_type=None,
                            duration_ms=self.elapsed_ms(started_ns, finished_ns),
                            started_ns=started_ns,
                        )
                    )
                    logger.debug("Gemini -> %d caracteres (intento %d).", len(texto), intento)
                    return texto

                finished_ns = time.perf_counter_ns()
                self._record_llm_audit(
                    LLMCallAudit(
                        provider=self.provider_name,
                        model_id=self.model_name,
                        fallback_used=False,
                        status="empty",
                        error_type="EmptyResponse",
                        duration_ms=self.elapsed_ms(started_ns, finished_ns),
                        started_ns=started_ns,
                    )
                )
                logger.warning("Gemini devolvio texto vacio (intento %d).", intento)

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
                msg = str(e)
                wait = self._calcular_espera(msg, backoff)
                log_safe_boundary_failure(
                    logger,
                    component="gemini.generate",
                    code="UPSTREAM_PROVIDER_FAILED",
                    error=e,
                    level=logging.WARNING,
                )
                logger.warning(
                    "Reintento Gemini intento=%d/%d espera_segundos=%.1f",
                    intento,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
                backoff *= 2

        logger.error("Gemini no respondio tras %d intentos.", max_retries)
        return ""

    def health_check(self) -> bool:
        """Verifica accesibilidad al servicio con una generacion minima."""
        try:
            respuesta = self.generate(
                "Responde solo con: ok",
                temperature=0.0,
                max_output_tokens=8,
                response_mime_type="text/plain",
            )
            return bool(respuesta and "ok" in respuesta.lower())
        except Exception as e:
            log_safe_boundary_failure(
                logger,
                component="gemini.health_check",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
            )
            return False

    def close(self) -> None:
        """Cierra las conexiones HTTP mantenidas por el SDK."""
        client = getattr(self, "client", None)
        if client is None:
            return

        try:
            client.close()
        except Exception as e:
            log_safe_boundary_failure(
                logger,
                component="gemini.close",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
                level=logging.WARNING,
            )
        finally:
            self.client = None

    def _build_generation_config(self, overrides: dict[str, Any]) -> types.GenerateContentConfig:
        """Construye un GenerateContentConfig a partir de la base y los kwargs recibidos."""
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
        """Acepta nombres con o sin prefijo 'models/'."""
        return model_name.removeprefix("models/").strip()

    @staticmethod
    def _extraer_texto(response: Any) -> str:
        """Extrae texto del response de google.genai de forma tolerante."""
        texto = getattr(response, "text", None)
        if isinstance(texto, str) and texto.strip():
            return texto.strip()

        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            fragmentos = []
            for part in parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    fragmentos.append(part_text.strip())
            if fragmentos:
                return "\n".join(fragmentos)

        return ""

    @staticmethod
    def _calcular_espera(msg: str, backoff: float) -> float:
        """Calcula el tiempo de espera a partir del mensaje de error y un backoff base."""
        wait = backoff + random.uniform(0, 1)
        match = re.search(r"retry_delay\s*\{\s*seconds\s*:\s*(\d+)", msg, re.IGNORECASE)
        if match:
            return int(match.group(1)) + random.uniform(0, 0.5)

        match = re.search(r"retry in (\d+)s", msg, re.IGNORECASE)
        if match:
            return int(match.group(1)) + random.uniform(0, 1)

        return wait
