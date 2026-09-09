"""
semantic_memory.py - Gestion de memoria semantica.
Proporciona metodos para almacenar y recuperar reglas, patrones y abstracciones
utilizando ChromaClient y MemoryEncoder.
"""

from typing import List

from chromadb.api.types import Where

from core.logger import get_logger
from memory.chroma_client import ChromaClient
from memory.memory_encoder import MemoryEncoder
from memory.memory_schema import SEMANTIC_KIND, MemorySchemaCodec, MemorySchemaDecodeError
from memory.memory_types import MemoryMetadata, MemoryResult, SemanticMemory
from memory.retrieval_types import (
    RetrievalHealth,
    RetrievalIssueCode,
    StoreRetrievalReceipt,
    extract_aligned_query_rows,
    schema_issue_code,
    validate_transport_row,
)

logger = get_logger(__name__)


class SemanticMemoryStore:
    """
    Repositorio de recuerdos semanticos (abstracciones, reglas, patrones).
    Utiliza ChromaClient como backend y MemoryEncoder para transformar objetos.
    """

    def __init__(self, client: ChromaClient):
        """
        Args:
            client: Instancia de ChromaClient ya inicializada.
        """
        self.client = client
        self.encoder = MemoryEncoder()

    def almacenar(self, recuerdo: SemanticMemory) -> bool:
        """
        Guarda una regla o abstraccion en ChromaDB.
        Retorna True si se guardo correctamente, False en caso de error.
        """
        doc = self.encoder.a_documento(recuerdo)
        return self.client.agregar(
            documentos=[doc.contenido],
            metadatos=[doc.metadata],
            ids=[doc.id],
        )

    def buscar(
        self,
        query: str,
        n: int = 5,
        where: Where | None = None,
    ) -> List[MemoryResult]:
        """
        Busca recuerdos semanticos por similitud semantica.
        Retorna una lista de MemoryResult con SemanticMemory reconstruidos.
        """
        return list(self.buscar_con_recibo(query, n, where).results)

    def buscar_con_recibo(
        self,
        query: str,
        n: int = 5,
        where: Where | None = None,
    ) -> StoreRetrievalReceipt:
        """Busca una vez y conserva salud, resultados validos y descartes."""

        try:
            where_real = self.client.combinar_where(where, {"tipo": SEMANTIC_KIND})
            resultados = self.client.buscar(query, n, where_real)
        except Exception as exc:
            logger.error(
                "Fallo de retrieval. component=memory_store operation=search "
                "source=semantic health=FAILED reason_code=BACKEND_FAILURE error_type=%s",
                type(exc).__name__,
            )
            return StoreRetrievalReceipt(
                health=RetrievalHealth.FAILED,
                reason_codes=(RetrievalIssueCode.BACKEND_FAILURE,),
            )

        if resultados is None:
            return StoreRetrievalReceipt(
                health=RetrievalHealth.FAILED,
                reason_codes=(RetrievalIssueCode.BACKEND_FAILURE,),
            )

        rows, candidate_count, malformed = extract_aligned_query_rows(resultados)
        if malformed:
            return StoreRetrievalReceipt(
                health=RetrievalHealth.PARTIAL,
                candidate_count=candidate_count,
                dropped_count=candidate_count,
                reason_codes=(RetrievalIssueCode.MALFORMED_BATCH,),
            )
        if candidate_count == 0:
            return StoreRetrievalReceipt(health=RetrievalHealth.OK_EMPTY)

        output: list[MemoryResult] = []
        reasons: list[RetrievalIssueCode] = []
        dropped_count = 0
        for row in rows:
            clean_row, transport_issue = validate_transport_row(row)
            if transport_issue is not None:
                reasons.append(transport_issue)
                dropped_count += 1
                continue

            assert clean_row is not None
            memory_id, document, metadata_raw, distance = clean_row
            try:
                decoded = MemorySchemaCodec.decode_metadata(metadata_raw, SEMANTIC_KIND)
            except MemorySchemaDecodeError as exc:
                reasons.append(schema_issue_code(exc))
                dropped_count += 1
                continue

            metadata = MemoryMetadata(
                tipo_evento=decoded.tipo_evento,
                valencia=decoded.valencia,
                impacto_emocional=decoded.impacto_emocional,
                timestamp=decoded.timestamp,
                extra=decoded.extra,
            )
            output.append(
                MemoryResult(
                    recuerdo=SemanticMemory(
                        id=memory_id,
                        metadata=metadata,
                        regla=document,
                        origen=decoded.origen,
                    ),
                    distancia=distance,
                )
            )

        health = RetrievalHealth.PARTIAL if dropped_count else RetrievalHealth.OK
        return StoreRetrievalReceipt(
            health=health,
            results=tuple(output),
            candidate_count=candidate_count,
            dropped_count=dropped_count,
            reason_codes=tuple(reasons),
        )
