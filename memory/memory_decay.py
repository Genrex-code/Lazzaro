"""
memory_decay.py - Sistema de relevancia dinámica para recuerdos.
Simula olvido gradual reduciendo la prioridad de los recuerdos poco accedidos o antiguos,
sin eliminarlos. Proporciona funciones para ajustar la importancia y para determinar
qué recuerdos son candidatos a ser descartados o comprimidos por el garbage collector.
"""

import math
from datetime import datetime, timezone
from typing import Optional

from memory.memory_types import EpisodicMemory, SemanticMemory
from memory.retrieval_types import RetrievalIssueCode
from core.logger import get_logger

logger = get_logger(__name__)


class MemoryTemporalError(ValueError):
    """Error temporal acotado y machine-readable para retrieval/scoring."""

    def __init__(self, reason_code: RetrievalIssueCode):
        self.reason_code = reason_code
        super().__init__(f"memory_temporal_error reason_code={reason_code.value}")


class MemoryDecay:
    """
    Gestiona la relevancia temporal de los recuerdos.
    No elimina nada directamente, solo calcula pesos y sugiere acciones.
    """

    @staticmethod
    def normalizar_instante_utc(timestamp: datetime) -> datetime:
        """Interpreta naive legacy como UTC y convierte aware al mismo huso."""

        if not isinstance(timestamp, datetime):
            raise MemoryTemporalError(RetrievalIssueCode.INVALID_TIMESTAMP)
        try:
            offset = timestamp.utcoffset()
        except (OverflowError, ValueError) as exc:
            raise MemoryTemporalError(RetrievalIssueCode.INVALID_TIMESTAMP) from exc
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        if offset is None:
            raise MemoryTemporalError(RetrievalIssueCode.INVALID_TIMESTAMP)
        try:
            return timestamp.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise MemoryTemporalError(RetrievalIssueCode.INVALID_TIMESTAMP) from exc

    @staticmethod
    def normalizar_instante_scoring(
        timestamp: datetime,
        now_utc: datetime,
    ) -> datetime:
        """Normaliza un timestamp y rechaza evidencia temporal futura."""

        instant = MemoryDecay.normalizar_instante_utc(timestamp)
        reference = MemoryDecay.normalizar_instante_utc(now_utc)
        if instant > reference:
            raise MemoryTemporalError(RetrievalIssueCode.FUTURE_TIMESTAMP)
        return instant

    @staticmethod
    def calcular_peso_recencia(
        timestamp: datetime,
        vida_media_horas: float = 48.0,
        minimo: float = 0.1,
        now_utc: datetime | None = None,
    ) -> float:
        """
        Calcula un peso basado en la antigüedad usando decaimiento exponencial.
        - 0 horas → peso 1.0
        - vida_media_horas → peso 0.5
        - después de mucho tiempo → peso tiende a 'minimo'

        Args:
            timestamp: momento en que se creó el recuerdo.
            vida_media_horas: tiempo en horas tras el cual el peso es 0.5.
            minimo: peso mínimo para evitar que llegue a 0.

        Returns:
            float entre minimo y 1.0.
        """
        reference = MemoryDecay.normalizar_instante_utc(
            now_utc if now_utc is not None else datetime.now(timezone.utc)
        )
        instant = MemoryDecay.normalizar_instante_scoring(timestamp, reference)
        antiguedad_horas = (reference - instant).total_seconds() / 3600.0
        if antiguedad_horas == 0:
            return 1.0
        # Decaimiento exponencial: peso = e^(-lambda * t)
        # donde lambda = ln(2) / vida_media
        lam = math.log(2) / vida_media_horas
        peso = math.exp(-lam * antiguedad_horas)
        return max(minimo, peso)

    @staticmethod
    def calcular_peso_uso(
        accesos: int,
        ultimo_acceso: Optional[datetime] = None,
        factor_acceso: float = 0.1,
        max_peso: float = 1.5,
    ) -> float:
        """
        Peso adicional según la frecuencia y recencia de uso.
        Cada acceso suma 'factor_acceso' hasta un máximo.

        Args:
            accesos: número total de veces que se ha recuperado.
            ultimo_acceso: fecha del último acceso (si no hay, vale ahora).
            factor_acceso: incremento por acceso.
            max_peso: límite superior.

        Returns:
            float entre 1.0 y max_peso.
        """
        if accesos <= 0:
            return 1.0
        # Bonificación lineal con saturación
        bonificacion = min(accesos * factor_acceso, max_peso - 1.0)
        return 1.0 + bonificacion

    @staticmethod
    def calcular_importancia_efectiva(
        recuerdo: EpisodicMemory | SemanticMemory,
        accesos: int = 0,
        ultimo_acceso: Optional[datetime] = None,
        vida_media_horas: float = 48.0,
        minimo_recencia: float = 0.1,
        now_utc: datetime | None = None,
    ) -> dict:
        """
        Calcula un diccionario con los pesos y la importancia combinada del recuerdo.
        No modifica el objeto original.

        Returns:
            dict con claves:
                - peso_recencia: float
                - peso_uso: float
                - impacto_emocional: float (original)
                - importancia_total: float (combinación de los anteriores)
        """
        metadata = recuerdo.metadata
        return MemoryDecay.calcular_importancia_desde_metadata(
            timestamp=metadata.timestamp,
            impacto_emocional=metadata.impacto_emocional,
            accesos=accesos,
            ultimo_acceso=ultimo_acceso,
            vida_media_horas=vida_media_horas,
            minimo_recencia=minimo_recencia,
            now_utc=now_utc,
        )

    @staticmethod
    def calcular_importancia_desde_metadata(
        timestamp: datetime,
        impacto_emocional: float,
        accesos: int = 0,
        ultimo_acceso: Optional[datetime] = None,
        vida_media_horas: float = 48.0,
        minimo_recencia: float = 0.1,
        now_utc: datetime | None = None,
    ) -> dict:
        """
        Variante liviana para datos ya extraidos de Chroma sin reconstruir recuerdos.
        """
        peso_recencia = MemoryDecay.calcular_peso_recencia(
            timestamp,
            vida_media_horas=vida_media_horas,
            minimo=minimo_recencia,
            now_utc=now_utc,
        )
        peso_uso = MemoryDecay.calcular_peso_uso(accesos, ultimo_acceso)
        impacto_limpio = max(0.0, min(1.0, float(impacto_emocional)))
        importancia_total = min(1.0, impacto_limpio * peso_recencia * peso_uso)
        return {
            "peso_recencia": round(peso_recencia, 3),
            "peso_uso": round(peso_uso, 3),
            "impacto_emocional": impacto_limpio,
            "importancia_total": round(importancia_total, 3),
        }

    @staticmethod
    def es_candidato_descarte(
        recuerdo: EpisodicMemory | SemanticMemory,
        accesos: int = 0,
        ultimo_acceso: Optional[datetime] = None,
        umbral_importancia: float = 0.1,
        vida_media_horas: float = 48.0,
        minimo_recencia: float = 0.1,
        now_utc: datetime | None = None,
    ) -> bool:
        """
        Determina si un recuerdo puede ser considerado para eliminación/compresión.

        Un recuerdo es candidato si:
        - Su importancia total es menor que el umbral.
        - No ha sido accedido en mucho tiempo.
        - Su impacto emocional es bajo.

        Returns:
            True si es candidato a descarte.
        """
        importancia = MemoryDecay.calcular_importancia_efectiva(
            recuerdo,
            accesos,
            ultimo_acceso,
            vida_media_horas=vida_media_horas,
            minimo_recencia=minimo_recencia,
            now_utc=now_utc,
        )
        return importancia["importancia_total"] < umbral_importancia
