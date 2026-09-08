"""
memory_encoder.py - Convierte experiencias estructuradas en documentos semánticos limpios.
Separa el contenido a embeber de la metadata, generando MemoryDocument para ChromaDB.
"""

from datetime import datetime
from typing import Any, Optional
import uuid

from core.logger import get_logger
from memory.memory_schema import CANONICAL_DEFAULT_ORIGIN, MemorySchemaCodec
from memory.memory_types import (
    EpisodicMemory,
    SemanticMemory,
    MemoryMetadata,
    MemoryDocument,
)

logger = get_logger(__name__)


class MemoryEncoder:
    """
    Encargado de transformar eventos del sistema en representaciones
    aptas para almacenamiento vectorial y recuperación semántica.
    """

    @staticmethod
    def codificar_episodio(
        tipo_evento: str,
        narrativa: str,
        estado_emocional: Optional[dict[str, bool | int | float]] = None,
        valencia: float = 0.0,
        impacto_emocional: float = 0.5,
        extra: Optional[dict[str, Any]] = None,
    ) -> EpisodicMemory:
        """
        Construye un EpisodicMemory inmutable a partir de los datos crudos de un evento.

        Args:
            tipo_evento: categoría del evento (ej. "error_critico").
            narrativa: descripción textual limpia.
            estado_emocional: snapshot del StateEngine (opcional).
            valencia: cuán positivo/negativo fue el evento (-1.0 a 1.0).
            impacto_emocional: intensidad del evento (0.0 a 1.0).
            extra: metadatos adicionales (ej. herramienta usada).

        Returns:
            EpisodicMemory listo para almacenar.
        """
        metadata = MemoryMetadata(
            tipo_evento=tipo_evento,
            valencia=valencia,
            impacto_emocional=impacto_emocional,
            timestamp=datetime.utcnow(),
            extra=(
                {}
                if extra is None or (isinstance(extra, dict) and not extra)
                else extra
            ),
        )
        recuerdo_id = str(uuid.uuid4())
        logger.debug(
            "Codificado episodio '%s' (id=%s, valencia=%.2f, impacto=%.2f)",
            tipo_evento,
            recuerdo_id,
            valencia,
            impacto_emocional,
        )
        return EpisodicMemory(
            id=recuerdo_id,
            metadata=metadata,
            narrativa=narrativa,
            estado_emocional=estado_emocional,
        )

    @staticmethod
    def codificar_semantica(
        regla: str,
        tipo_evento: str = "semantica",
        valencia: float = 0.0,
        impacto_emocional: float = 0.5,
        origen: str = CANONICAL_DEFAULT_ORIGIN,
        extra: Optional[dict[str, Any]] = None,
    ) -> SemanticMemory:
        """
        Construye una abstracción semántica (regla/patrón).

        Args:
            regla: texto que resume la regla o patrón.
            tipo_evento: usualmente "semantica" o "regla".
            valencia: tono general de la regla.
            impacto_emocional: relevancia del aprendizaje.
            origen: cómo se generó ("manual", "gemini", "determinista").
            extra: metadatos adicionales.

        Returns:
            SemanticMemory.
        """
        metadata = MemoryMetadata(
            tipo_evento=tipo_evento,
            valencia=valencia,
            impacto_emocional=impacto_emocional,
            timestamp=datetime.utcnow(),
            extra=(
                {}
                if extra is None or (isinstance(extra, dict) and not extra)
                else extra
            ),
        )
        recuerdo_id = str(uuid.uuid4())
        logger.debug("Codificada semántica '%s' (id=%s)", regla[:60], recuerdo_id)
        return SemanticMemory(
            id=recuerdo_id,
            metadata=metadata,
            regla=regla,
            origen=origen,
        )

    @staticmethod
    def a_documento(recuerdo: EpisodicMemory | SemanticMemory) -> MemoryDocument:
        """
        Transforma un recuerdo (episódico o semántico) en un MemoryDocument
        listo para ser insertado en ChromaDB.

        El contenido a embeber será limpio y específico:
        - Para episódico: la narrativa.
        - Para semántico: la regla.
        La metadata se prepara como diccionario simple para ChromaDB.
        """
        if isinstance(recuerdo, EpisodicMemory):
            contenido = recuerdo.narrativa
            metadata_plana = MemorySchemaCodec.encode_episodic_metadata(
                tipo_evento=recuerdo.metadata.tipo_evento,
                valencia=recuerdo.metadata.valencia,
                impacto_emocional=recuerdo.metadata.impacto_emocional,
                timestamp=recuerdo.metadata.timestamp,
                extra=recuerdo.metadata.extra,
                estado_emocional=recuerdo.estado_emocional,
            )
            return MemoryDocument(
                id=recuerdo.id,
                contenido=contenido,
                metadata=metadata_plana,
            )
        else:  # SemanticMemory
            contenido = recuerdo.regla
            metadata_plana = MemorySchemaCodec.encode_semantic_metadata(
                tipo_evento=recuerdo.metadata.tipo_evento,
                valencia=recuerdo.metadata.valencia,
                impacto_emocional=recuerdo.metadata.impacto_emocional,
                timestamp=recuerdo.metadata.timestamp,
                extra=recuerdo.metadata.extra,
                origen=recuerdo.origen,
            )
            return MemoryDocument(
                id=recuerdo.id,
                contenido=contenido,
                metadata=metadata_plana,
            )
