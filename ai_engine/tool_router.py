"""
tool_router.py - Filtra y selecciona herramientas disponibles segun el contexto actual.
Reduce la sobrecarga de opciones que recibe el LLM, mejorando sus decisiones.
"""

from typing import Optional

from core.logger import get_logger

logger = get_logger(__name__)


class ToolRouter:
    """
    Enruta las herramientas disponibles al subconjunto adecuado para la tarea,
    politica y estado actual del agente.
    """

    HERRAMIENTAS_RIESGOSAS_BASE = {
        "keyboard_real",
        "shell",
        "system",
        "ejecutar_script",
    }
    HERRAMIENTAS_RIESGOSAS_PREFIJOS = (
        "mouse_real",
        "keyboard_real",
        "shell",
        "system",
        "ejecutar_script",
    )

    @staticmethod
    def filtrar(
        herramientas: list[str],
        objetivo: Optional[str] = None,
        politica: str = "exploradora",
        tipo_tarea: str = "general",
    ) -> dict:
        """
        Filtra la lista completa de herramientas y devuelve las permitidas y las riesgosas.
        """
        if not herramientas:
            logger.debug("Sin herramientas disponibles.")
            return {"permitidas": [], "riesgosas": []}

        tipo_tarea = ToolRouter._inferir_tipo_tarea(objetivo, tipo_tarea)
        riesgosas = [h for h in herramientas if ToolRouter._es_herramienta_riesgosa(h)]

        if tipo_tarea == "documentacion":
            permitidas = ToolRouter._para_documentacion(herramientas)
        elif tipo_tarea == "quiz":
            permitidas = ToolRouter._para_quiz(herramientas)
        elif tipo_tarea == "red":
            permitidas = ToolRouter._para_red(herramientas)
        else:
            permitidas = list(herramientas)

        if politica in ("cautelosa", "eficiente"):
            permitidas = [h for h in permitidas if h not in riesgosas]
            riesgosas = []

        logger.debug(
            "ToolRouter: tarea=%s, politica=%s -> %d permitidas, %d riesgosas.",
            tipo_tarea,
            politica,
            len(permitidas),
            len(riesgosas),
        )
        return {"permitidas": permitidas, "riesgosas": riesgosas}

    @staticmethod
    def _es_herramienta_riesgosa(nombre: str) -> bool:
        if nombre in ToolRouter.HERRAMIENTAS_RIESGOSAS_BASE:
            return True
        return any(nombre.startswith(prefijo) for prefijo in ToolRouter.HERRAMIENTAS_RIESGOSAS_PREFIJOS)

    @staticmethod
    def _inferir_tipo_tarea(objetivo: Optional[str], tipo_tarea: str) -> str:
        """Infere un tipo de tarea a partir del objetivo cuando llega como 'general'."""
        if tipo_tarea != "general" or not objetivo:
            return tipo_tarea

        objetivo_norm = objetivo.lower()
        if any(token in objetivo_norm for token in ("document", "doc", "texto", "escribir", "redact")):
            return "documentacion"
        if any(token in objetivo_norm for token in ("quiz", "pregunta", "examen", "respuesta")):
            return "quiz"
        if any(token in objetivo_norm for token in ("red", "network", "msdr")):
            return "red"
        return tipo_tarea

    @staticmethod
    def _para_documentacion(herramientas: list[str]) -> list[str]:
        """Prioriza escritura y teclado, evita herramientas graficas si no aportan."""
        palabras_clave = ("teclado", "keyboard", "escribir", "texto", "tecla", "clipboard")
        filtradas = [h for h in herramientas if any(token in h for token in palabras_clave)]
        return filtradas or list(herramientas)

    @staticmethod
    def _para_quiz(herramientas: list[str]) -> list[str]:
        """Solo respuestas de texto, sin mouse ni control real."""
        return [h for h in herramientas if "mouse" not in h and "real" not in h]

    @staticmethod
    def _para_red(herramientas: list[str]) -> list[str]:
        """Herramientas de monitoreo de red, excluye ofimatica si hay opciones especificas."""
        permitidas = [h for h in herramientas if "red" in h or "network" in h or "msdr" in h]
        return permitidas or list(herramientas)
