"""
memory_filters.py - Construcción de filtros avanzados para consultas a ChromaDB.
Proporciona filtros predefinidos y generación dinámica de condiciones 'where'
basadas en metadatos (tipo_evento, valencia, impacto, recencia, etc.).
"""

from datetime import datetime, timedelta
from typing import Any, Optional

from core.logger import get_logger

logger = get_logger(__name__)


class MemoryFilters:
    """
    Factoría de filtros para usar en las búsquedas de ChromaClient.
    Todos los métodos devuelven diccionarios compatibles con el parámetro 'where'.
    """

    @staticmethod
    def por_tipo_evento(tipo: str) -> dict:
        """Filtra recuerdos exactamente por el campo 'tipo_evento'."""
        return {"tipo_evento": tipo}

    @staticmethod
    def por_impacto_minimo(umbral: float = 0.7) -> dict:
        """Recuerdos con impacto_emocional >= umbral (0.0 a 1.0)."""
        return {"impacto_emocional": {"$gte": umbral}}

    @staticmethod
    def por_valencia(valencia: float, operador: str = "$eq") -> dict:
        """
        Filtra por valencia exacta o comparativa.
        Ejemplos:
            operador='$gte' y valencia=0 → recuerdos positivos o neutros.
            operador='$lt' y valencia=-0.5 → recuerdos muy negativos.
        """
        return {"valencia": {operador: valencia}}

    @staticmethod
    def recientes(horas: float = 24.0) -> dict:
        """
        Recuerdos de las últimas N horas.
        Nota: ChromaDB no soporta operadores de fecha directamente en 'where';
        este método devuelve una estructura que debe ser interpretada por el
        llamante (p.ej., memory_retriever podría filtrar post-consulta).
        Por ahora, devolvemos un marcador para filtrado manual.
        """
        # Devolvemos un dict con un campo personalizado que el retriever pueda entender.
        return {"_recencia_horas": horas}

    @staticmethod
    def combinado(*filtros: dict) -> dict:
        """
        Combina múltiples filtros usando $and (todos deben cumplirse).
        Si algún filtro contiene claves que no son operadores Chroma,
        se devuelve un dict con clave '$and' y la lista de condiciones.
        """
        condiciones = []
        for f in filtros:
            if f:
                condiciones.append(f)
        if not condiciones:
            return {}
        if len(condiciones) == 1:
            return condiciones[0]
        return {"$and": condiciones}

    # ------------------------------------------------------------------
    # Filtros predefinidos (conveniencia)
    # ------------------------------------------------------------------

    @staticmethod
    def solo_criticos() -> dict:
        """Recuerdos con impacto_emocional >= 0.8."""
        return MemoryFilters.por_impacto_minimo(0.8)

    @staticmethod
    def solo_positivos() -> dict:
        """Recuerdos con valencia > 0 (positivos)."""
        return MemoryFilters.por_valencia(0.0, "$gt")

    @staticmethod
    def solo_negativos() -> dict:
        """Recuerdos con valencia < 0 (negativos)."""
        return MemoryFilters.por_valencia(0.0, "$lt")

    @staticmethod
    def errores_relevantes() -> dict:
        """Errores críticos o fracasos con alto impacto."""
        return MemoryFilters.combinado(
            MemoryFilters.por_tipo_evento("error_critico"),
            MemoryFilters.por_impacto_minimo(0.6),
        )

    @staticmethod
    def exitos_importantes() -> dict:
        """Éxitos con impacto alto."""
        return MemoryFilters.combinado(
            MemoryFilters.por_tipo_evento("tarea_completada"),
            MemoryFilters.por_impacto_minimo(0.7),
        )