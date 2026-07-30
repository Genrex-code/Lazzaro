"""
conversation_memory.py - Memoria conversacional volatil de LAZZARO.

Mantiene un buffer pequeno de turnos usuario/Lazzaro para dar continuidad
inmediata sin consultar ChromaDB ni persistir ruido conversacional.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import re
import threading
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


@dataclass(frozen=True)
class PublicConversationTurn:
    """Proyección inmutable de un turno, sin pensamiento ni metadata interna."""

    usuario: str
    lazzaro: str
    timestamp: datetime


class ConversationMemory:
    """Cola circular para continuidad de dialogo de corto plazo."""

    def __init__(
        self,
        max_turns: int = 12,
        max_text_chars: int = 700,
        max_summary_chars: int = 1600,
    ):
        self._turns: deque[ConversationTurn] = deque(maxlen=max_turns)
        self._lock = threading.RLock()
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
        with self._lock:
            self._turns.append(turno)
        logger.debug("Memoria conversacional: +turno route=%s intent=%s", turno.route, turno.intent)

    def obtener_ultimos(self, n: int = 6) -> list[ConversationTurn]:
        """Devuelve los ultimos n turnos, del mas antiguo al mas reciente."""
        limite = max(1, n)
        with self._lock:
            return list(self._turns)[-limite:]

    def snapshot_publico(
        self,
        max_turns: Optional[int] = None,
        max_text_chars: Optional[int] = None,
    ) -> tuple[PublicConversationTurn, ...]:
        """Copia atómicamente la RAM usando únicamente el diálogo público."""
        with self._lock:
            turnos = list(self._turns)
            if max_turns is not None:
                turnos = turnos[-max(1, max_turns) :]
            limite_texto = max_text_chars or self.max_text_chars
            return tuple(
                PublicConversationTurn(
                    usuario=self._limpiar_texto(turno.usuario, limite=limite_texto),
                    lazzaro=self._limpiar_texto(turno.lazzaro, limite=limite_texto),
                    timestamp=turno.timestamp,
                )
                for turno in turnos
                if turno.usuario or turno.lazzaro
            )

    def resumen(self, n: int = 6) -> str:
        """Devuelve continuidad conversacional compacta para prompts."""
        turnos = self.obtener_ultimos(n)
        if not turnos:
            return ""

        lineas = ["CONTINUIDAD CONVERSACIONAL RECIENTE:"]
        lineas.extend(self._construir_bloque_enfoque(turnos))
        lineas.append("DIALOGO RECIENTE:")
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
        with self._lock:
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

    def _construir_bloque_enfoque(self, turnos: list[ConversationTurn]) -> list[str]:
        """Extrae senales simples para que Persona mantenga continuidad inmediata."""
        if not turnos:
            return []

        bloque: list[str] = []
        ultimo = turnos[-1]
        if ultimo.usuario or ultimo.lazzaro:
            bloque.append("ULTIMO INTERCAMBIO:")
            if ultimo.usuario:
                bloque.append(f"- Usuario venia de decir: {ultimo.usuario}")
            if ultimo.lazzaro:
                bloque.append(f"- Lazzaro venia de responder: {ultimo.lazzaro}")

        temas = self._extraer_temas_activos(turnos)
        if temas:
            bloque.append("TEMAS ACTIVOS:")
            bloque.append("- " + ", ".join(temas))

        if self._requiere_continuidad(ultimo.usuario):
            bloque.append("SENAL DE CONTINUIDAD:")
            bloque.append(
                "- El usuario probablemente se refiere al intercambio anterior; "
                "usa el contexto reciente antes de responder."
            )

        return bloque

    def _extraer_temas_activos(self, turnos: list[ConversationTurn], limite: int = 6) -> list[str]:
        texto = " ".join(f"{turno.usuario} {turno.lazzaro}" for turno in turnos).lower()
        candidatos = [
            ("apodo/nombre", ("apodo", "nombre", "llamas", "lazz", "ent-0")),
            ("identidad/creador", ("creador", "desarrollador", "codigo", "perro")),
            ("memoria", ("recuerdas", "recordar", "memoria", "base de datos")),
            ("estado interno", ("estres", "energia", "cansada", "agotada", "sientes")),
            ("percepcion", ("ves", "miras", "entorno", "objeto", "capsule", "cube", "sphere")),
            ("movimiento", ("camina", "gira", "alejate", "acercate", "interactua")),
            ("preferencias", ("me gusta", "prefiero", "te gusta", "opinas")),
        ]

        temas: list[str] = []
        for nombre, pistas in candidatos:
            if any(pista in texto for pista in pistas):
                temas.append(nombre)
            if len(temas) >= limite:
                break
        return temas

    @staticmethod
    def _requiere_continuidad(texto_usuario: str) -> bool:
        texto = f" {str(texto_usuario or '').lower()} "
        patrones = (
            r"\beso\b",
            r"\bese\b",
            r"\besa\b",
            r"\beste\b",
            r"\besta\b",
            r"\blo anterior\b",
            r"\blo que te dije\b",
            r"\bcomo te dije\b",
            r"\brecuerdas\b",
            r"\brecuerdaste\b",
            r"\bque opinas\b",
            r"\bpor que\b",
            r"\bporque\b",
            r"\by eso\b",
        )
        return any(re.search(patron, texto) for patron in patrones)
