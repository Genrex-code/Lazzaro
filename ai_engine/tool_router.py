"""
tool_router.py - Filtra y selecciona herramientas disponibles según el contexto actual.
Reduce la sobrecarga de opciones que recibe el LLM, mejorando sus decisiones.
"""

from typing import List, Optional
from core.logger import get_logger

logger = get_logger(__name__)


class ToolRouter:
    """
    Enruta las herramientas disponibles al subconjunto adecuado para la tarea,
    política y estado actual del agente.
    """

    # Herramientas consideradas intrínsecamente riesgosas (acceso real al sistema)
    HERRAMIENTAS_RIESGOSAS_BASE = {
        "mouse_real", "keyboard_real", "shell", "system", "ejecutar_script",
    }

    @staticmethod
    def filtrar(
        herramientas: List[str],
        objetivo: Optional[str] = None,
        politica: str = "exploradora",
        tipo_tarea: str = "general",
    ) -> dict:
        """
        Filtra la lista completa de herramientas y devuelve las permitidas y las riesgosas.

        Args:
            herramientas: todas las herramientas registradas en el sistema.
            objetivo: descripción del objetivo actual (opcional).
            politica: política activa del LLM.
            tipo_tarea: categoría de la tarea ("documentacion", "quiz", "red", "general").

        Returns:
            dict con:
                - "permitidas": lista de herramientas seguras para el contexto.
                - "riesgosas": subconjunto de herramientas que requieren precaución extra.
        """
        if not herramientas:
            logger.debug("Sin herramientas disponibles.")
            return {"permitidas": [], "riesgosas": []}

        # Clasificar herramientas riesgosas según nuestra base
        riesgosas = [h for h in herramientas if h in ToolRouter.HERRAMIENTAS_RIESGOSAS_BASE]

        # Aplicar filtros según tipo de tarea
        if tipo_tarea == "documentacion":
            permitidas = ToolRouter._para_documentacion(herramientas)
        elif tipo_tarea == "quiz":
            permitidas = ToolRouter._para_quiz(herramientas)
        elif tipo_tarea == "red":
            permitidas = ToolRouter._para_red(herramientas)
        else:
            # Tarea general: entregamos todas, salvo las extremadamente peligrosas si la política no lo permite
            permitidas = list(herramientas)

        # La política también puede restringir herramientas riesgosas (llm_policies ya lo hace,
        # pero aquí podemos hacer una primera pasada para no enviarlas al LLM si no es necesario)
        if politica in ("cautelosa", "eficiente"):
            permitidas = [h for h in permitidas if h not in riesgosas]
            riesgosas = []  # No se ofrecen en absoluto

        logger.debug(
            "ToolRouter: tarea=%s, política=%s → %d permitidas, %d riesgosas.",
            tipo_tarea, politica, len(permitidas), len(riesgosas)
        )
        return {"permitidas": permitidas, "riesgosas": riesgosas}

    # ------------------------------------------------------------------
    # Filtros específicos por tipo de tarea
    # ------------------------------------------------------------------

    @staticmethod
    def _para_documentacion(herramientas: List[str]) -> List[str]:
        """Prioriza teclado, portapapeles y evita herramientas gráficas."""
        prioridad = {"teclado", "keyboard", "keyboard_sim", "clipboard"}
        return [h for h in herramientas if h in prioridad or "teclado" in h or "keyboard" in h]

    @staticmethod
    def _para_quiz(herramientas: List[str]) -> List[str]:
        """Solo respuestas de texto, sin mouse ni control real."""
        return [h for h in herramientas if "mouse" not in h and "real" not in h]

    @staticmethod
    def _para_red(herramientas: List[str]) -> List[str]:
        """Herramientas de monitoreo de red (MSDR), excluye ofimática."""
        permitidas = [h for h in herramientas if "red" in h or "network" in h or "msdr" in h]
        return permitidas or herramientas  # si no hay específicas, pasar todas