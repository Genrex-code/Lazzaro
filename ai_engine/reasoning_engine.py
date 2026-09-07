"""
reasoning_engine.py - Orquestador central del modulo ai_engine.
"""

import time
from typing import List, Optional

from ai_engine.ai_client import BaseAIClient
from ai_engine.context_builder import ContextBuilder
from ai_engine.llm_policies import LLMPolicyManager
from ai_engine.prompt_builder import PromptBuilder
from ai_engine.response_parser import ResponseParser
from ai_engine.schemas import ActionResponse
from ai_engine.tool_router import ToolRouter
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure

logger = get_logger(__name__)


class ReasoningEngine:
    """
    Coordina el flujo de razonamiento sin contener logica de dominio.
    """

    def __init__(self, ai_client: BaseAIClient):
        self.ai_client = ai_client
        self.context_builder = ContextBuilder()
        self.prompt_builder = PromptBuilder()
        self.tool_router = ToolRouter()
        self.policy_manager = LLMPolicyManager()
        self.response_parser = ResponseParser()

    def process(
        self,
        contexto_llm: dict,
        memoria_texto: str = "",
        herramientas: Optional[List[str]] = None,
        herramientas_detalle: Optional[dict[str, str]] = None,
        politica: str = "exploradora",
        tarea: Optional[dict] = None,
        audit_observation: dict[str, object] | None = None,
    ) -> ActionResponse:
        try:
            if herramientas is None:
                herramientas = []
            if herramientas_detalle is None:
                herramientas_detalle = {}

            herramientas_info = self.tool_router.filtrar(
                herramientas,
                objetivo=tarea.get("objetivo") if tarea else None,
                politica=politica,
                tipo_tarea="general",
            )
            herramientas_filtradas = herramientas_info["permitidas"]
            herramientas_detalle_filtradas = {
                nombre: detalle
                for nombre, detalle in herramientas_detalle.items()
                if nombre in herramientas_filtradas
            }

            proveedor = getattr(
                self.ai_client,
                "provider_name",
                self.ai_client.__class__.__name__.lower(),
            )
            agent_context = self.context_builder.build(
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto,
                herramientas=herramientas_filtradas,
                herramientas_detalle=herramientas_detalle_filtradas,
                politica=politica,
                tarea=tarea,
                proveedor=proveedor,
            )

            policy_config = self.policy_manager.ajustar_parametros(
                politica,
                herramientas=herramientas_filtradas,
                herramientas_riesgosas=herramientas_info.get("riesgosas"),
                proveedor=proveedor,
            )
            gen_params = policy_config["generation"]

            prompt = self.prompt_builder.build(agent_context)
            logger.debug("Prompt generado (%d caracteres).", len(prompt))

            raw_response = self.ai_client.generate(prompt, **gen_params)
            if not raw_response:
                logger.warning("El LLM devolvio una respuesta vacia.")
                self._observe_failure(
                    audit_observation,
                    error_type="EmptyResponse",
                    component="llm",
                )
                return ActionResponse(
                    pensamiento="",
                    accion="none",
                    parametros={},
                    razon="",
                    valido=False,
                    error="Respuesta vacia del modelo.",
                )

            acciones_permitidas = set(herramientas_filtradas)
            acciones_permitidas.add("none")
            action = self.response_parser.parse(raw_response, acciones_permitidas)
            if action.error or not action.valido:
                self._observe_failure(
                    audit_observation,
                    error_type="InvalidLLMResponse",
                    component="llm",
                )
            logger.info("Accion decidida: %s (valida=%s)", action.accion, action.valido)
            return action

        except Exception as e:
            log_safe_boundary_failure(
                logger,
                component="reasoning.pipeline",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
            )
            self._observe_failure(
                audit_observation,
                error_type=type(e).__name__,
                component="reasoning",
            )
            return ActionResponse(
                pensamiento="",
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error=f"Error interno del motor de razonamiento: {e}",
            )

    @staticmethod
    def _observe_failure(
        audit_observation: dict[str, object] | None,
        *,
        error_type: str,
        component: str,
    ) -> None:
        if not isinstance(audit_observation, dict):
            return
        audit_observation["fallback_used"] = True
        audit_observation.setdefault("decision_error_type", error_type)
        audit_observation.setdefault("decision_error_component", component)
        audit_observation.setdefault(
            "decision_error_observed_ns",
            time.perf_counter_ns(),
        )
