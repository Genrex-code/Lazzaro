"""
ai_engine/llm_generation_adapter.py - Adaptador comun para llamadas LLM textuales.
"""

from __future__ import annotations

from typing import Literal

from ai_engine.prompt_plan import PromptPlanV1


TokenStrategy = Literal["cap", "floor", "exact"]


class LLMGenerationAdapter:
    """Centraliza la adaptacion de mensajes tipo chat al cliente LLM activo."""

    def __init__(self, ai_client, policy_manager):
        self.ai_client = ai_client
        self.policy_manager = policy_manager

    def ejecutar_texto_diagnostico(
        self,
        mensajes: list[dict[str, str]],
        *,
        politica: str,
        temperature: float,
        max_output_tokens: int,
        token_strategy: TokenStrategy = "exact",
        required_provider: str | None = None,
        error_context: str = "LLM local",
    ) -> str:
        if self.ai_client is None:
            raise RuntimeError(f"No hay cliente LLM disponible para {error_context}.")
        if self.policy_manager is None:
            raise RuntimeError(f"No hay policy manager disponible para {error_context}.")

        proveedor = getattr(
            self.ai_client,
            "provider_name",
            self.ai_client.__class__.__name__.lower(),
        )
        if required_provider and str(proveedor).strip().lower() != required_provider.lower():
            raise RuntimeError(
                f"El cliente LLM activo no es {required_provider}; {error_context} no disponible."
            )

        generation = self.policy_manager.ajustar_parametros(
            politica,
            herramientas=["hablar"],
            herramientas_riesgosas=[],
            proveedor=proveedor,
        )["generation"]
        generation["temperature"] = min(float(generation.get("temperature", temperature)), temperature)
        generation["max_output_tokens"] = self._resolver_max_tokens(
            int(generation.get("max_output_tokens", max_output_tokens)),
            int(max_output_tokens),
            token_strategy,
        )
        generation["response_mime_type"] = "text/plain"

        prompt = self.mensajes_chat_a_prompt_diagnostico(mensajes)
        return self.ai_client.generate(prompt, **generation)

    def ejecutar_plan(
        self,
        plan: PromptPlanV1,
        *,
        politica: str,
        temperature: float,
        max_output_tokens: int,
        token_strategy: TokenStrategy = "exact",
        required_provider: str | None = None,
        error_context: str = "LLM cognitivo",
        response_mime_type: str = "text/plain",
    ) -> str:
        """Execute a typed cognitive plan without a legacy text flattening path."""
        if not isinstance(plan, PromptPlanV1):
            raise TypeError("ejecutar_plan requiere PromptPlanV1")
        if self.ai_client is None:
            raise RuntimeError(f"No hay cliente LLM disponible para {error_context}.")
        if self.policy_manager is None:
            raise RuntimeError(f"No hay policy manager disponible para {error_context}.")

        proveedor = getattr(
            self.ai_client,
            "provider_name",
            self.ai_client.__class__.__name__.lower(),
        )
        if required_provider and str(proveedor).strip().lower() != required_provider.lower():
            raise RuntimeError(
                f"El cliente LLM activo no es {required_provider}; {error_context} no disponible."
            )
        generation = self.policy_manager.ajustar_parametros(
            politica,
            herramientas=["hablar"],
            herramientas_riesgosas=[],
            proveedor=proveedor,
        )["generation"]
        generation["temperature"] = min(
            float(generation.get("temperature", temperature)), temperature
        )
        generation["max_output_tokens"] = self._resolver_max_tokens(
            int(generation.get("max_output_tokens", max_output_tokens)),
            int(max_output_tokens),
            token_strategy,
        )
        generation["response_mime_type"] = response_mime_type
        return self.ai_client.generate_plan(plan, **generation)

    @staticmethod
    def mensajes_chat_a_prompt_diagnostico(mensajes: list[dict[str, str]]) -> str:
        partes: list[str] = []
        for mensaje in mensajes:
            if not isinstance(mensaje, dict):
                continue
            rol = str(mensaje.get("role", "user")).upper()
            contenido = str(mensaje.get("content", "")).strip()
            if contenido:
                partes.append(f"[{rol}]\n{contenido}")
        return "\n\n".join(partes)

    @staticmethod
    def _resolver_max_tokens(actual: int, limite: int, strategy: TokenStrategy) -> int:
        if strategy == "cap":
            return min(actual, limite)
        if strategy == "floor":
            return max(actual, limite)
        return limite
