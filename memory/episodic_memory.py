"""
episodic_memory.py - Gestion de memoria episodica.
Proporciona metodos para almacenar y recuperar recuerdos episodicos
utilizando ChromaClient y MemoryEncoder.
"""

import datetime
from typing import List

from chromadb.api.types import Metadata, Where

from core.logger import get_logger
from memory.chroma_client import ChromaClient
from memory.memory_encoder import MemoryEncoder
from memory.memory_types import EpisodicMemory, MemoryMetadata, MemoryResult

logger = get_logger(__name__)


def _valor_a_str(valor: object, default: str) -> str:
    return valor if isinstance(valor, str) else default


def _valor_a_float(valor: object, default: float) -> float:
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    return default


class EpisodicMemoryStore:
    """
    Repositorio de recuerdos episodicos.
    Utiliza ChromaClient como backend y MemoryEncoder para transformar objetos.
    """

    def __init__(self, client: ChromaClient):
        """
        Args:
            client: Instancia de ChromaClient ya inicializada.
        """
        self.client = client
        self.encoder = MemoryEncoder()

    def almacenar(self, recuerdo: EpisodicMemory) -> bool:
        """
        Guarda un recuerdo episodico en ChromaDB.
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
        Busca recuerdos episodicos por similitud semantica.
        Retorna una lista de MemoryResult con EpisodicMemory reconstruidos.
        """
        where_real = self.client.combinar_where(where, {"tipo": "episodico"})
        resultados = self.client.buscar(query, n, where_real)
        if not resultados:
            return []

        ids = resultados["ids"][0] if resultados["ids"] else []
        document_batches = resultados.get("documents")
        metadata_batches = resultados.get("metadatas")
        distance_batches = resultados.get("distances")

        documents: list[str] = document_batches[0] if document_batches else []
        metadatas: list[Metadata] = metadata_batches[0] if metadata_batches else []
        distances: list[float] = distance_batches[0] if distance_batches else []

        output: list[MemoryResult] = []
        for i, id_ in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            doc_text = documents[i] if i < len(documents) else ""

            tipo_evento = _valor_a_str(meta.get("tipo_evento"), "desconocido")
            valencia = _valor_a_float(meta.get("valencia"), 0.0)
            impacto = _valor_a_float(meta.get("impacto_emocional"), 0.5)
            ts_str = _valor_a_str(meta.get("timestamp"), "")
            try:
                ts = (
                    datetime.datetime.fromisoformat(ts_str)
                    if ts_str
                    else datetime.datetime.utcnow()
                )
            except Exception:
                ts = datetime.datetime.utcnow()

            reserved = {"tipo_evento", "valencia", "impacto_emocional", "timestamp", "tipo"}
            extra = {k: v for k, v in meta.items() if k not in reserved}

            metadata = MemoryMetadata(
                tipo_evento=tipo_evento,
                valencia=valencia,
                impacto_emocional=impacto,
                timestamp=ts,
                extra=extra,
            )
            recuerdo = EpisodicMemory(
                id=id_,
                metadata=metadata,
                narrativa=doc_text,
                estado_emocional=None,
            )
            distancia = distances[i] if i < len(distances) else 1.0
            output.append(MemoryResult(recuerdo=recuerdo, distancia=distancia))
        return output
