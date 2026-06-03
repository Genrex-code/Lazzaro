"""
oracle_memory.py - Memoria Semantinca y Cache del Oraculo (Capa 3).
Almacena respuestas tecnicas o filosoficas ya procesadas para no repetir llamadas
al LLM.
"""

import uuid
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from core.logger import get_logger

logger = get_logger(__name__)


class OracleMemory:
    """
    Caché de conocimiento basado en vectores.
    Utiliza una colección independiente de los recuerdos episódicos de Lazzaro.
    """

    def __init__(self, db_path: str = "./lazzaro_data"):
        self.db_path = db_path
        self._client = None
        self._collection = None
        self._inicializar()

    @property
    def disponible(self) -> bool:
        """Indica si la colección está operativa."""
        return self._collection is not None

    def _inicializar(self):
        """Conecta a ChromaDB y crea/obtiene la colección del oráculo."""
        try:
            self._client = chromadb.PersistentClient(
                path=self.db_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name="oracle_cache",
                metadata={
                    "description": "Caché de respuestas técnicas y filosóficas",
                    "hnsw:space": "cosine",
                },
            )
            logger.info("OracleMemory listo (colección 'oracle_cache').")
        except Exception as e:
            logger.error("Error al inicializar OracleMemory: %s", e)
            self._collection = None
    #======================================================================================
    #Busqueda semantica
    #======================================================================================

    def buscar_respuesta_cacheada(
        self, pregunta: str, umbral_similitud: float = 0.8
    ) -> Optional[str]:
        """
        Busca una respuesta previamente guardada para una pregunta similar.
        Con métrica cosine, la distancia varía entre 0 (idéntico) y 2 (opuesto).
        A menor distancia, mayor similitud. es decir mi niña se volvera inteligente :3 

        Args:
            pregunta: texto de la pregunta.
            umbral_similitud: distancia máxima para considerar un acierto (default 0.8).

        Returns:
            La respuesta cacheada o None si no hay coincidencia.
        """
        if not self.disponible or not pregunta.strip():
            return None

        try:
            resultados = self._collection.query(
                query_texts=[pregunta],
                n_results=1,
            )
            # Verificar que haya resultados
            if not resultados.get("documents") or not resultados["documents"][0]:
                return None

            distancia = resultados["distances"][0][0]
            if distancia <= umbral_similitud:
                metadata = resultados["metadatas"][0][0]
                respuesta = metadata.get("respuesta", "")
                logger.info("Oracle Cache HIT (distancia %.3f).", distancia)
                return respuesta
            else:
                logger.debug("Oracle Cache MISS (distancia %.3f > %.2f).", distancia, umbral_similitud)
                return None
        except Exception as e:
            logger.error("Error al consultar Oracle Cache: %s", e)
            return None

    # ------------------------------------------------------------------
    # Almacenamiento
    # ------------------------------------------------------------------
    def guardar_en_cache(self, pregunta: str, respuesta: str) -> bool:
        """
        Persiste un nuevo par pregunta‑respuesta en la caché.
        Retorna True si se guardó correctamente.
        """
        if not self.disponible or not pregunta.strip() or not respuesta.strip():
            return False

        try:
            doc_id = str(uuid.uuid4())
            self._collection.add(
                ids=[doc_id],
                documents=[pregunta],
                metadatas=[{"respuesta": respuesta}],
            )
            logger.info("Oracle Cache: nueva respuesta almacenada (id=%s).", doc_id)
            return True
        except Exception as e:
            logger.error("Error al guardar en Oracle Cache: %s", e)
            return False
