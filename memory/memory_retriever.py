"""
memory_retriever.py - Recuperacion hibrida de recuerdos.

Combina memoria episodica y semantica, aplica reranking por similitud,
impacto y decaimiento temporal.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Callable, List, Optional

from core.logger import get_logger
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_decay import MemoryDecay, MemoryTemporalError
from memory.memory_types import MemoryResult, SemanticMemory
from memory.retrieval_types import (
    MemoryRetrievalReceipt,
    RetrievalHealth,
    RetrievalIssueCode,
    StoreRetrievalReceipt,
    merge_reason_codes,
)
from memory.semantic_memory import SemanticMemoryStore

logger = get_logger(__name__)


class MemoryRetriever:
    """Recuperador hibrido que conserva resultados y salud por fuente."""

    def __init__(
        self,
        episodic_store: EpisodicMemoryStore,
        semantic_store: SemanticMemoryStore,
        clock: Callable[[], datetime] | None = None,
    ):
        self.episodic = episodic_store
        self.semantic = semantic_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def buscar(
        self,
        query: str,
        limite: int = 5,
        where_episodica: Optional[dict] = None,
        where_semantica: Optional[dict] = None,
    ) -> List[MemoryResult]:
        """Adaptador legacy: proyecta la lista del mismo receipt hibrido."""

        return list(
            self.buscar_con_recibo(
                query=query,
                limite=limite,
                where_episodica=where_episodica,
                where_semantica=where_semantica,
            ).results
        )

    def buscar_con_recibo(
        self,
        query: str,
        limite: int = 5,
        where_episodica: Optional[dict] = None,
        where_semantica: Optional[dict] = None,
    ) -> MemoryRetrievalReceipt:
        """Ejecuta una consulta por fuente y agrega salud, scoring y ranking."""

        episodic = self._buscar_fuente(
            self.episodic,
            source="episodic",
            query=query,
            n=limite * 2,
            where=where_episodica,
        )
        semantic = self._buscar_fuente(
            self.semantic,
            source="semantic",
            query=query,
            n=limite,
            where=where_semantica,
        )

        try:
            now_utc = self._clock()
        except Exception as exc:
            logger.error(
                "Fallo de retrieval. component=memory_retriever operation=clock "
                "health=PARTIAL reason_code=SCORING_FAILURE error_type=%s",
                type(exc).__name__,
            )
            episodic = self._descartar_por_scoring(
                episodic,
                RetrievalIssueCode.SCORING_FAILURE,
            )
            semantic = self._descartar_por_scoring(
                semantic,
                RetrievalIssueCode.SCORING_FAILURE,
            )
        else:
            episodic = self._calcular_scores_recibo(episodic, now_utc, "episodic")
            semantic = self._calcular_scores_recibo(semantic, now_utc, "semantic")

        ids_semanticos = {result.recuerdo.id for result in semantic.results}
        episodicos = [
            result
            for result in episodic.results
            if result.recuerdo.id not in ids_semanticos
        ]
        todos = episodicos + list(semantic.results)
        todos.sort(key=lambda result: result.metadata_score, reverse=True)
        ranked = tuple(todos[:limite])

        health = self._aggregate_health(episodic, semantic)
        if health == RetrievalHealth.OK_EMPTY:
            logger.debug(
                "Retrieval completado. component=memory_retriever operation=hybrid_search "
                "health=OK_EMPTY returned_count=0"
            )
        return MemoryRetrievalReceipt(
            health=health,
            results=ranked,
            episodic=episodic,
            semantic=semantic,
            reason_codes=merge_reason_codes(
                episodic.reason_codes,
                semantic.reason_codes,
            ),
        )

    def _buscar_fuente(
        self,
        store: object,
        *,
        source: str,
        query: str,
        n: int,
        where: Optional[dict],
    ) -> StoreRetrievalReceipt:
        """Obtiene un receipt; el fallback legacy existe sólo para compatibilidad."""

        try:
            buscar_con_recibo = getattr(store, "buscar_con_recibo", None)
            if callable(buscar_con_recibo):
                receipt = buscar_con_recibo(query, n, where)
                if not isinstance(receipt, StoreRetrievalReceipt):
                    raise TypeError("invalid store receipt")
                return receipt

            buscar_legacy = getattr(store, "buscar")
            results = tuple(buscar_legacy(query, n, where))
            return StoreRetrievalReceipt(
                health=(RetrievalHealth.OK if results else RetrievalHealth.OK_EMPTY),
                results=results,
                candidate_count=len(results),
            )
        except Exception as exc:
            logger.error(
                "Fallo de retrieval. component=memory_retriever operation=source_search "
                "source=%s health=FAILED reason_code=INTERNAL_FAILURE error_type=%s",
                source,
                type(exc).__name__,
            )
            return StoreRetrievalReceipt(
                health=RetrievalHealth.FAILED,
                reason_codes=(RetrievalIssueCode.INTERNAL_FAILURE,),
            )

    def _calcular_scores_recibo(
        self,
        receipt: StoreRetrievalReceipt,
        now_utc: datetime,
        source: str,
    ) -> StoreRetrievalReceipt:
        """Aisla resultados que no pueden completar scoring temporal."""

        if not receipt.results:
            return receipt

        valid: list[MemoryResult] = []
        reasons = list(receipt.reason_codes)
        scoring_dropped = 0
        for result in receipt.results:
            try:
                self._calcular_score(result, now_utc)
            except MemoryTemporalError as exc:
                reason = exc.reason_code
            except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
                reason = RetrievalIssueCode.SCORING_FAILURE
                logger.warning(
                    "Resultado aislado. component=memory_retriever operation=scoring "
                    "source=%s health=PARTIAL reason_code=SCORING_FAILURE error_type=%s",
                    source,
                    type(exc).__name__,
                )
            else:
                valid.append(result)
                continue

            reasons.append(reason)
            scoring_dropped += 1

        if scoring_dropped == 0:
            return receipt
        return StoreRetrievalReceipt(
            health=RetrievalHealth.PARTIAL,
            results=tuple(valid),
            candidate_count=receipt.candidate_count,
            dropped_count=receipt.dropped_count + scoring_dropped,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def _descartar_por_scoring(
        receipt: StoreRetrievalReceipt,
        reason: RetrievalIssueCode,
    ) -> StoreRetrievalReceipt:
        if not receipt.results:
            return receipt
        return StoreRetrievalReceipt(
            health=RetrievalHealth.PARTIAL,
            candidate_count=receipt.candidate_count,
            dropped_count=receipt.dropped_count + receipt.valid_count,
            reason_codes=merge_reason_codes(receipt.reason_codes, (reason,)),
        )

    @staticmethod
    def _aggregate_health(
        episodic: StoreRetrievalReceipt,
        semantic: StoreRetrievalReceipt,
    ) -> RetrievalHealth:
        source_health = (episodic.health, semantic.health)
        if source_health == (RetrievalHealth.FAILED, RetrievalHealth.FAILED):
            return RetrievalHealth.FAILED
        if any(
            health in {RetrievalHealth.FAILED, RetrievalHealth.PARTIAL}
            for health in source_health
        ):
            return RetrievalHealth.PARTIAL
        if source_health == (RetrievalHealth.OK_EMPTY, RetrievalHealth.OK_EMPTY):
            return RetrievalHealth.OK_EMPTY
        return RetrievalHealth.OK

    @staticmethod
    def _calcular_score(resultado: MemoryResult, now_utc: datetime) -> None:
        """Calcula el score de un resultado y sólo lo asigna al finalizar."""

        if isinstance(resultado.distancia, bool) or not isinstance(
            resultado.distancia,
            (int, float),
        ):
            raise TypeError("invalid scoring distance")
        distancia = float(resultado.distancia)
        if not math.isfinite(distancia):
            raise ValueError("invalid scoring distance")

        similitud = max(0.0, 1.0 - distancia)
        metadata = getattr(resultado.recuerdo, "metadata", None)
        if metadata:
            impacto = metadata.impacto_emocional
            es_semantica = isinstance(resultado.recuerdo, SemanticMemory)
            decaimiento = MemoryDecay.calcular_importancia_efectiva(
                resultado.recuerdo,
                vida_media_horas=24.0 * 30.0 if es_semantica else 72.0,
                minimo_recencia=0.35 if es_semantica else 0.1,
                now_utc=now_utc,
            )
            recencia = float(decaimiento["peso_recencia"])
            importancia_temporal = float(decaimiento["importancia_total"])
        else:
            impacto = 0.5
            recencia = 0.5
            importancia_temporal = impacto * recencia

        score = (
            0.45 * similitud
            + 0.25 * impacto
            + 0.20 * importancia_temporal
            + 0.10 * recencia
        )
        if not math.isfinite(float(score)):
            raise ArithmeticError("non-finite scoring result")
        resultado.metadata_score = score

    def _calcular_scores(
        self,
        resultados: List[MemoryResult],
        now_utc: datetime | None = None,
    ) -> None:
        """Helper compatible para callers de prueba; production usa receipts."""

        if now_utc is None:
            clock = getattr(self, "_clock", None)
            now_utc = clock() if callable(clock) else datetime.now(timezone.utc)
        for resultado in resultados:
            self._calcular_score(resultado, now_utc)
