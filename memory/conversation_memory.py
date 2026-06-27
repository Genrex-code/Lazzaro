"""
conversation_memory.py - Memoria conversacional volatil de LAZZARO.

Mantiene un buffer pequeno de turnos usuario/Lazzaro para dar continuidad
inmediata sin consultar ChromaDB ni persistir ruido conversacional.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ConversationTurn:
    """Un intercambio conversacional reciente, mantenido solo en RAM."""

    usuario: str
    lazzaro: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    pensamiento: str = ""
    emocion: str = "Fcl_ALL_Neutral"
    route: str = ""
    intent: str = ""
    metadata: Optional[dict[str, Any]] = None


class ConversationMemory:
    """Cola circular para continuidad de dialogo de corto plazo."""

    def __init__(
        self,
        max_turns: int = 12,
        max_text_chars: int = 700,
        max_summary_chars: int = 1600,
    ):
        self._turns: deque[ConversationTurn] = deque(maxlen=max_turns)
        self.max_text_chars = max(120, max_text_chars)
        self.max_summary_chars = max(300, max_summary_chars)

    def registrar_turno(
        self,
        usuario: str,
        lazzaro: str,
        pensamiento: str = "",
        emocion: str = "Fcl_ALL_Neutral",
        route: str = "",
        intent: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Guarda un turno usuario/Lazzaro si contiene texto util."""
        usuario_limpio = self._limpiar_texto(usuario)
        lazzaro_limpio = self._limpiar_texto(lazzaro)
        if not usuario_limpio and not lazzaro_limpio:
            logger.debug("Memoria conversacional ignora turno vacio.")
            return

        turno = ConversationTurn(
            usuario=usuario_limpio,
            lazzaro=lazzaro_limpio,
            pensamiento=self._limpiar_texto(pensamiento, limite=240),
            emocion=str(emocion or "Fcl_ALL_Neutral").strip() or "Fcl_ALL_Neutral",
            route=str(route or "").strip(),
            intent=str(intent or "").strip(),
            metadata=metadata or None,
        )
        self._turns.append(turno)
        logger.debug("Memoria conversacional: +turno route=%s intent=%s", turno.route, turno.intent)

    def obtener_ultimos(self, n: int = 6) -> list[ConversationTurn]:
        """Devuelve los ultimos n turnos, del mas antiguo al mas reciente."""
        limite = max(1, n)
        return list(self._turns)[-limite:]

    def resumen(self, n: int = 6) -> str:
        """Devuelve continuidad conversacional compacta para prompts."""
        turnos = self.obtener_ultimos(n)
        if not turnos:
            return ""

        lineas = ["CONTINUIDAD CONVERSACIONAL RECIENTE:"]
        for turno in turnos:
            ts = turno.timestamp.strftime("%H:%M")
            if turno.usuario:
                lineas.append(f"[{ts}] Usuario: {turno.usuario}")
            if turno.lazzaro:
                lineas.append(f"[{ts}] Lazzaro: {turno.lazzaro}")

        resumen = "\n".join(lineas)
        return self._recortar_resumen(resumen)

    def limpiar(self) -> None:
        """Vacia el buffer conversacional."""
        self._turns.clear()
        logger.debug("Memoria conversacional limpiada.")

    def _limpiar_texto(self, texto: str, limite: Optional[int] = None) -> str:
        texto_limpio = " ".join(str(texto or "").split())
        maximo = limite or self.max_text_chars
        if len(texto_limpio) <= maximo:
            return texto_limpio
        return texto_limpio[: maximo - 3].rstrip() + "..."

    def _recortar_resumen(self, resumen: str) -> str:
        if len(resumen) <= self.max_summary_chars:
            return resumen
        return resumen[-self.max_summary_chars :].lstrip()
