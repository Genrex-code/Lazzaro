"""
context_builder.py - Construye el contexto estructurado que alimenta el prompt del LLM.
Recibe la salida de PsychologyEngine, la memoria resumida, las herramientas
disponibles y la politica activa, y las empaqueta en un AgentContext.
"""

from typing import Optional

from ai_engine.schemas import AgentContext
from core.logger import get_logger

logger = get_logger(__name__)


class ContextBuilder:
    """
    Consolida toda la informacion relevante en un unico AgentContext
    listo para que el PromptBuilder lo transforme en texto.
    """

    @staticmethod
    def build(
        contexto_llm: dict,
        memoria_texto: str = "",
        herramientas: Optional[list[str]] = None,
        politica: str = "exploradora",
        tarea: Optional[dict] = None,
    ) -> AgentContext:
        """
        Construye un AgentContext a partir de las piezas proporcionadas.

        Soporta tanto el resumen crudo de TaskContext como una tarea ya
        normalizada con claves como 'objetivo', 'intentos' y 'fallos'.
        """
        estado_narrativo = contexto_llm.get("estado_narrativo", "")
        valores = contexto_llm.get("valores", {})
        personalidad = contexto_llm.get("personalidad", {})
        umbrales = contexto_llm.get("umbrales", {})
        percepcion_texto = contexto_llm.get("percepcion_resumida", "")
        tarea_data = tarea if tarea else contexto_llm.get("tarea")

        tarea_contexto = None
        if tarea_data:
            tarea_contexto = {
                "objetivo": tarea_data.get("objetivo", tarea_data.get("objetivo_id", "")),
                "intentos": tarea_data.get("intentos", tarea_data.get("intentos_totales", 0)),
                "fallos": tarea_data.get("fallos", tarea_data.get("intentos_fallidos", 0)),
                "frustracion": tarea_data.get("frustracion", tarea_data.get("frustracion_local", 0.0)),
                "umbral_abandono": tarea_data.get("umbral_abandono", 80.0),
                "loop_detectado": tarea_data.get("loop_detectado", False),
                "estancamiento": tarea_data.get(
                    "estancamiento",
                    tarea_data.get("estancamiento_detectado", False),
                ),
            }

        agent_context = AgentContext(
            estado_narrativo=estado_narrativo,
            valores_estado=valores,
            personalidad=personalidad,
            tarea=tarea_contexto,
            memoria_texto=memoria_texto,
            percepcion_texto=percepcion_texto,
            herramientas=herramientas or [],
            politica=politica,
            banderas=umbrales,
        )
        logger.debug(
            "AgentContext construido con politica '%s' y %d herramientas.",
            politica,
            len(herramientas or []),
        )
        return agent_context
