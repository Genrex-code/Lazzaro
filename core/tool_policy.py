"""
core/tool_policy.py - Politica declarativa de herramientas por ruta y origen.
"""

from __future__ import annotations

from collections.abc import Iterable


class ToolPolicy:
    """Selecciona herramientas sin volver a clasificar el texto del usuario."""

    CLIENT_SAFE_TOOLS = {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "alejarse_de_objeto",
        "moverse_entorno_3d",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "aplicar_fuerza",
        "hablar",
    }
    SPEECH_TOOLS = {"hablar"}
    WORLD_CLIENTS = {"unity", "roblox"}

    def seleccionar(
        self,
        herramientas: Iterable[str],
        *,
        origen: str,
        route: str,
    ) -> list[str]:
        """Devuelve el subconjunto permitido para el frontend y la ruta cognitiva."""
        disponibles = list(herramientas or [])
        route_norm = str(route or "").strip().lower()
        origen_norm = str(origen or "").strip().lower()

        if route_norm in {"persona", "oracle"}:
            return self._filtrar(disponibles, self.SPEECH_TOOLS)

        if origen_norm in self.WORLD_CLIENTS:
            return self._filtrar(disponibles, self.CLIENT_SAFE_TOOLS)

        return disponibles

    @staticmethod
    def _filtrar(herramientas: list[str], permitidas: set[str]) -> list[str]:
        return [herramienta for herramienta in herramientas if herramienta in permitidas]
