"""
episodic_memory.py - Gestión de memoria episódica.
Proporciona métodos para almacenar y recuperar recuerdos episódicos
utilizando ChromaClient y MemoryEncoder.
"""

from typing import List, Optional
import datetime

from memory.memory_types import (
    EpisodicMemory,
    MemoryMetadata,
    MemoryResult,
)
from memory.memory_encoder import MemoryEncoder
from memory.chroma_client import ChromaClient
from core.logger import get_logger

logger = get_logger(__name__)


class EpisodicMemoryStore:
    """
    Repositorio de recuerdos episódicos.
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
        Guarda un recuerdo episódico en ChromaDB.
        Retorna True si se guardó correctamente, False en caso de error.
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
        where: Optional[dict] = None,
    ) -> List[MemoryResult]:
        """
        Busca recuerdos episódicos por similitud semántica.
        Retorna una lista de MemoryResult con EpisodicMemory reconstruidos.
        """
        resultados = self.client.buscar(query, n, where)
        if not resultados:
            return []

        ids = resultados.get("ids", [[]])[0]
        documents = resultados.get("documents", [[]])[0]
        metadatas = resultados.get("metadatas", [[]])[0]
        distances = resultados.get("distances", [[]])[0]

        output = []
        for i, id_ in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            doc_text = documents[i] if i < len(documents) else ""

            # Reconstruir MemoryMetadata
            tipo_evento = meta.get("tipo_evento", "desconocido")
            valencia = meta.get("valencia", 0.0)
            impacto = meta.get("impacto_emocional", 0.5)
            ts_str = meta.get("timestamp", "")
            try:
                ts = datetime.datetime.fromisoformat(ts_str) if ts_str else datetime.datetime.utcnow()
            except Exception:
                ts = datetime.datetime.utcnow()

            # Extraer campos adicionales que no son reservados
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
                estado_emocional=None,  # no almacenamos el estado completo aquí
            )
            output.append(MemoryResult(recuerdo=recuerdo, distancia=distances[i]))
        return output