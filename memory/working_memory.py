"""
working_memory.py - Memoria de trabajo volátil (RAM) de LAZZARO.
Almacena las últimas acciones y eventos inmediatos, sin persistencia.
"""

from collections import deque
from datetime import datetime
import threading

from core.logger import get_logger
from memory.memory_types import WorkingMemoryItem

logger = get_logger(__name__)


class WorkingMemory:
    """
    Cola circular de eventos recientes.
    No se persiste ni se embeberá directamente; proporciona contexto inmediato
    al agente (últimas acciones, errores recientes, etc.).
    """

    def __init__(self, max_size: int = 20):
        """
        Args:
            max_size: Número máximo de elementos en la cola.
        """
        self._buffer = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def agregar(
        self, accion: str, resultado: str = "pendiente", detalles: dict | None = None
    ):
        """Añade una acción o evento a la memoria de trabajo."""
        item = WorkingMemoryItem(
            accion=accion,
            resultado=resultado,
            timestamp=datetime.utcnow(),
            detalles=detalles,
        )
        with self._lock:
            self._buffer.append(item)
        logger.debug("Working memory: +%s (%s)", accion, resultado)

    def obtener_ultimas(self, n: int = 5) -> list[WorkingMemoryItem]:
        """Devuelve los últimos n eventos (los más recientes primero)."""
        with self._lock:
            return list(self._buffer)[-n:][::-1]

    def obtener_todas(self) -> list[WorkingMemoryItem]:
        """Devuelve todos los eventos actualmente en la memoria de trabajo."""
        with self._lock:
            return list(self._buffer)

    def ultimo_resultado(self) -> str | None:
        """Resultado de la acción más reciente, o None si no hay."""
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer[-1].resultado

    def resumen(self) -> str:
        """Devuelve un texto breve con las últimas acciones para incluir en el prompt."""
        ultimas = self.obtener_ultimas(5)
        if not ultimas:
            return "No hay acciones recientes."
        lineas = ["Últimas acciones:"]
        for item in reversed(ultimas):  # más antiguo primero
            ts = item.timestamp.strftime("%H:%M:%S")
            esfuerzo = self._resumen_esfuerzo(item.detalles)
            sufijo = f" ({esfuerzo})" if esfuerzo else ""
            lineas.append(f"[{ts}] {item.accion} → {item.resultado}{sufijo}")
        return "\n".join(lineas)

    def limpiar(self):
        """Vacía la memoria de trabajo."""
        with self._lock:
            self._buffer.clear()
        logger.debug("Working memory limpiada.")

    @staticmethod
    def _resumen_esfuerzo(detalles: dict | None) -> str:
        if not isinstance(detalles, dict):
            return ""
        esfuerzo = detalles.get("subjective_effort")
        if not isinstance(esfuerzo, dict):
            return ""
        etiqueta = str(esfuerzo.get("label") or "").strip()
        score = esfuerzo.get("score")
        if not etiqueta:
            return ""
        try:
            return f"esfuerzo percibido: {etiqueta}, costo {float(score):0.2f}"
        except (TypeError, ValueError):
            return f"esfuerzo percibido: {etiqueta}"
