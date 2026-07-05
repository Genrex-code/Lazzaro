"""
memory_retriever.py - Recuperacion hibrida de recuerdos.

Combina memoria episodica y semantica, aplica reranking por similitud,
impacto y decaimiento temporal.
"""

from typing import List, Optional

from core.logger import get_logger
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_decay import MemoryDecay
from memory.memory_types import MemoryResult, SemanticMemory
from memory.semantic_memory import SemanticMemoryStore

logger = get_logger(__name__)


class MemoryRetriever:
    """
    Recuperador hibrido que consulta memorias episodicas y semanticas.
    """

    def __init__(
        self,
        episodic_store: EpisodicMemoryStore,
        semantic_store: SemanticMemoryStore,
    ):
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
        """
        episodicos = self._buscar_episodica(query, limite * 2, where_episodica)
        semanticos = self._buscar_semantica(query, limite, where_semantica)

        ids_semanticos = {r.recuerdo.id for r in semanticos}
        episodicos = [r for r in episodicos if r.recuerdo.id not in ids_semanticos]
        todos = episodicos + semanticos

        if not todos:
            logger.debug("Sin recuerdos recuperados para '%s'.", query[:50])
            return []

        self._calcular_scores(todos)
        todos.sort(key=lambda r: r.metadata_score, reverse=True)
        return todos[:limite]

    def _buscar_episodica(self, query: str, n: int, where: Optional[dict]) -> List[MemoryResult]:
        """Recupera recuerdos episodicos con manejo de errores."""
        try:
            return self.episodic.buscar(query, n, where)
        except Exception as e:
            logger.error("Error al buscar en memoria episodica: %s", e)
            return []

    def _buscar_semantica(self, query: str, n: int, where: Optional[dict]) -> List[MemoryResult]:
        """Recupera reglas semanticas con manejo de errores."""
        try:
            return self.semantic.buscar(query, n, where)
        except Exception as e:
            logger.error("Error al buscar en memoria semantica: %s", e)
            return []

    def _calcular_scores(self, resultados: List[MemoryResult]) -> None:
        """
        Asigna metadata_score usando similitud, impacto y MemoryDecay.

        Las reglas semanticas envejecen mas lento que los episodios crudos para
        no perder aprendizajes consolidados por el simple paso del tiempo.
        """
        for resultado in resultados:
            similitud = max(0.0, 1.0 - resultado.distancia)
            metadata = getattr(resultado.recuerdo, "metadata", None)

            if metadata:
                impacto = metadata.impacto_emocional
                es_semantica = isinstance(resultado.recuerdo, SemanticMemory)
                decaimiento = MemoryDecay.calcular_importancia_efectiva(
                    resultado.recuerdo,
                    vida_media_horas=24.0 * 30.0 if es_semantica else 72.0,
                    minimo_recencia=0.35 if es_semantica else 0.1,
                )
                recencia = float(decaimiento["peso_recencia"])
                importancia_temporal = float(decaimiento["importancia_total"])
            else:
                impacto = 0.5
                recencia = 0.5
                importancia_temporal = impacto * recencia

            resultado.metadata_score = (
                0.45 * similitud
                + 0.25 * impacto
                + 0.20 * importancia_temporal
                + 0.10 * recencia
            )
