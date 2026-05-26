"""
reasoning_engine.py - Orquestador central del módulo ai_engine.
Coordina el pipeline completo: contexto → prompt → LLM → parseo → acción.
Actúa como "director de orquesta", delegando cada paso a los módulos especializados.
"""

from typing import Optional, List
from core.logger import get_logger
from ai_engine.ai_client import BaseAIClient
from ai_engine.context_builder import ContextBuider
from ai_engine.prompt_buider import PromptBuilder
from ai_engine.llm_policies import LLMPolicyManager
from ai_engine.tool_router import ToolRouter
from ai_engine.response_parser import ResponseParser
from ai_engine.schemas import ActionResponse

logger = get_logger(__name__)


class ReasoningEngine:
    """
    Coordina el flujo de razonamiento sin contener lógica de dominio.
    Recibe un cliente de IA y orquesta los pasos necesarios para obtener
    una acción validada a partir del estado actual del agente.
    """

    def __init__(self, ai_client: BaseAIClient):
        """
        Args:
            ai_client: implementación concreta de BaseAIClient (ej. GeminiClient).
        """
        self.ai_client = ai_client
        self.context_builder = ContextBuider()
        self.prompt_builder = PromptBuilder()
        self.tool_router = ToolRouter()
        self.policy_manager = LLMPolicyManager()
        self.response_parser = ResponseParser()

    def process(
        self,
        contexto_llm: dict,
        memoria_texto: str = "",
        herramientas: Optional[List[str]] = None,
        politica: str = "exploradora",
        tarea: Optional[dict] = None,
    ) -> ActionResponse:
        """
        Ejecuta el pipeline cognitivo completo.

        Args:
            contexto_llm: diccionario generado por PsychologyEngine (narrativa, valores, banderas...).
            memoria_texto: resumen de recuerdos relevantes desde MemoryManager.
            herramientas: lista de nombres de herramientas disponibles.
            politica: nombre de la política activa (cautelosa, exploradora, etc.).
            tarea: información opcional sobre el objetivo actual.

        Returns:
            ActionResponse con la acción a ejecutar (o fallback si algo falla).
        """
        try:
            # 1. Filtrar herramientas según política y tipo de tarea
            if herramientas is None:
                herramientas = []
            herramientas_info = self.tool_router.filtrar(
                herramientas,
                objetivo=tarea.get("objetivo") if tarea else None,
                politica=politica,
                tipo_tarea="general",  # puedes ajustarlo según la tarea real
            )
            herramientas_filtradas = herramientas_info["permitidas"]

            # 2. Construir el contexto del agente
            agent_context = self.context_builder.build(
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto,
                herramientas=herramientas_filtradas,
                politica=politica,
                tarea=tarea,
            )

            # 3. Ajustar parámetros de generación según política del LLM
            policy_config = self.policy_manager.ajustar_parametros(
                politica,
                herramientas=herramientas_filtradas,
                herramientas_riesgosas=herramientas_info.get("riesgosas"),
            )
            gen_params = policy_config["generation"]

            # 4. Construir prompt final
            prompt = self.prompt_builder.build(agent_context)
            logger.debug("Prompt generado (%d caracteres).", len(prompt))

            # 5. Llamar al LLM
            raw_response = self.ai_client.generate(prompt, **gen_params)
            if not raw_response:
                logger.warning("El LLM devolvió una respuesta vacía.")
                return ActionResponse(
                    pensamiento="",
                    accion="none",
                    parametros={},
                    razon="",
                    valido=False,
                    error="Respuesta vacía del modelo."
                )

            # 6. Parsear y validar respuesta
            action = self.response_parser.parse(raw_response, set(herramientas_filtradas))
            logger.info("Acción decidida: %s (válida=%s)", action.accion, action.valido)
            return action

        except Exception as e:
            logger.error("Error en el pipeline de razonamiento: %s", e, exc_info=True)
            return ActionResponse(
                pensamiento="",
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error=f"Error interno del motor de razonamiento: {e}"
            )