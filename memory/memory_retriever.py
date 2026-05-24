"""
memory_retriever.py - Sistema especializado de búsqueda y recuperación de recuerdos.
Combina resultados de memoria episódica y semántica, aplica filtros y reranking
basado en relevancia emocional, recencia y similitud semántica.
"""

from typing import List, Optional

from memory.memory_types import MemoryResult
from memory.episodic_memory import EpisodicMemoryStore
from memory.semantic_memory import SemanticMemoryStore
from core.logger import get_logger

logger = get_logger(__name__)


class MemoryRetriever:
    """
    Recuperador híbrido que consulta las memorias episódica y semántica,
    combina los resultados y los ordena según múltiples criterios.
    """

    def __init__(
        self,
        episodic_store: EpisodicMemoryStore,
        semantic_store: SemanticMemoryStore,
    ):
        """
        Args:
            episodic_store: Repositorio de memoria episódica.
            semantic_store: Repositorio de memoria semántica.
        """
        self.episodic = episodic_store
        self.semantic = semantic_store

    def buscar(
        self,
        query: str,
        limite: int = 5,
        where_episodica: Optional[dict] = None,
        where_semantica: Optional[dict] = None,
    ) -> List[MemoryResult]:
        """
        Busca en ambas memorias y devuelve los mejores resultados combinados.

        Args:
            query: Texto de consulta (normalmente la narrativa actual del agente).
            limite: Número máximo de resultados a retornar.
            where_episodica: Filtro de metadatos para ChromaDB (memoria episódica).
            where_semantica: Filtro de metadatos para ChromaDB (memoria semántica).

        Returns:
            Lista de MemoryResult ordenados por relevancia (el más relevante primero).
        """
        # Obtener resultados de ambas memorias (con más candidatos de los necesarios para rerankear)
        episodicos = self._buscar_episodica(query, limite * 2, where_episodica)
        semanticos = self._buscar_semantica(query, limite, where_semantica)

        # Combinar todos los resultados
        todos = episodicos + semanticos

        if not todos:
            logger.debug("Sin recuerdos recuperados para '%s'.", query[:50])
            return []

        # Calcular puntuación combinada (score) para cada resultado
        self._calcular_scores(todos)

        # Ordenar por score descendente (mayor = mejor)
        todos.sort(key=lambda r: r.metadata_score, reverse=True)

        # Devolver los mejores hasta el límite
        return todos[:limite]

    def _buscar_episodica(self, query: str, n: int, where: Optional[dict]) -> List[MemoryResult]:
        """Recupera recuerdos episódicos con manejo de errores."""
        try:
            return self.episodic.buscar(query, n, where)
        except Exception as e:
            logger.error("Error al buscar en memoria episódica: %s", e)
            return []

    def _buscar_semantica(self, query: str, n: int, where: Optional[dict]) -> List[MemoryResult]:
        """Recupera reglas semánticas con manejo de errores."""
        try:
            return self.semantic.buscar(query, n, where)
        except Exception as e:
            logger.error("Error al buscar en memoria semántica: %s", e)
            return []

    def _calcular_scores(self, resultados: List[MemoryResult]):
        """
        Asigna a cada MemoryResult un puntaje combinado (metadata_score)
        basado en similitud, impacto emocional y recencia.
        """
        now = None  # se calculará bajo demanda
        for r in resultados:
            # Similitud: distancia coseno (menor = más similar). Convertimos a 0-1.
            similitud = max(0.0, 1.0 - r.distancia)  # asumiendo distancia en [0,1] para cosine

            # Impacto emocional: valor directo de metadata
            if hasattr(r.recuerdo, 'metadata') and r.recuerdo.metadata:
                impacto = r.recuerdo.metadata.impacto_emocional
            else:
                impacto = 0.5

            # Recencia: antigüedad en horas (más reciente = mayor peso)
            if hasattr(r.recuerdo, 'metadata') and r.recuerdo.metadata:
                timestamp = r.recuerdo.metadata.timestamp
                if now is None:
                    from datetime import datetime
                    now = datetime.utcnow()
                horas = (now - timestamp).total_seconds() / 3600.0
                # Peso decreciente: 1.0 para 0h, 0.1 para 24h+
                recencia = max(0.1, 1.0 - horas / 24.0)
            else:
                recencia = 0.5

            # Score combinado (ponderaciones ajustables)
            r.metadata_score = 0.4 * similitud + 0.35 * impacto + 0.25 * recencia