"""
oracle_memory.py - Memoria semantica y cache del Oraculo (Capa 3).
Almacena respuestas tecnicas o filosoficas ya procesadas para no repetir llamadas
al LLM.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from ai_engine.schemas import OracleResult
from core.logger import get_logger
from memory.chroma_client import ChromaClient

logger = get_logger(__name__)


class OracleMemory:
    """
    Cache de conocimiento basado en vectores.
    Usa ChromaClient para compartir compuertas y ciclo de cierre con memoria.
    """

    DEFAULT_CACHE_DISTANCE = 0.35

    def __init__(self, db_path: str = "./lazzaro_data"):
        self.db_path = db_path
        self.client = ChromaClient(db_path=db_path, collection_name="oracle_cache")

    @property
    def disponible(self) -> bool:
        """Indica si la coleccion esta operativa."""
        return self.client.disponible

    def buscar_respuesta_cacheada(
        self,
        pregunta: str,
        umbral_similitud: float = DEFAULT_CACHE_DISTANCE,
    ) -> Optional[str]:
        """Compatibilidad legacy: devuelve solo el contenido tecnico cacheado."""
        resultado = self.buscar_resultado_cacheado(pregunta, umbral_similitud)
        return resultado.contenido_tecnico if resultado else None

    def buscar_resultado_cacheado(
        self,
        pregunta: str,
        umbral_similitud: float = DEFAULT_CACHE_DISTANCE,
    ) -> Optional[OracleResult]:
        """
        Busca conocimiento tecnico previamente guardado para una pregunta similar.
        Con metrica cosine, menor distancia significa mayor similitud.
        """
        if not self.disponible or not pregunta.strip():
            return None

        resultados = self.client.buscar(
            consulta=pregunta,
            n_resultados=1,
        )
        if not resultados:
            return None

        try:
            docs = resultados.get("documents") or []
            metadatas = resultados.get("metadatas") or []
            distances = resultados.get("distances") or []
            if not docs or not docs[0] or not distances or not distances[0]:
                return None

            distancia = float(distances[0][0])
            if distancia > umbral_similitud:
                logger.debug(
                    "Oracle Cache MISS (distancia %.3f > %.2f).",
                    distancia,
                    umbral_similitud,
                )
                return None

            metadata = metadatas[0][0] if metadatas and metadatas[0] else {}
            if not isinstance(metadata, dict):
                return None

            contenido = str(
                metadata.get("contenido_tecnico")
                or metadata.get("respuesta")
                or ""
            ).strip()
            if not contenido:
                return None

            logger.info("Oracle Cache HIT (distancia %.3f).", distancia)
            resumen = str(metadata.get("resumen_para_persona", "")).strip() or None
            return OracleResult(
                pregunta_original=pregunta,
                contenido_tecnico=contenido,
                resumen_para_persona=resumen,
                fuente="cache",
                cache_hit=True,
            )
        except Exception as e:
            logger.error("Error al interpretar Oracle Cache: %s", e)
            return None

    def guardar_en_cache(
        self,
        pregunta: str,
        contenido_tecnico: str,
        resumen_para_persona: str | None = None,
        fuente: str = "api",
    ) -> bool:
        """
        Persiste un par pregunta-conocimiento frio en la cache.
        Retorna True si se guardo correctamente.
        """
        if not self.disponible or not pregunta.strip() or not contenido_tecnico.strip():
            return False

        doc_id = str(uuid.uuid4())
        guardado = self.client.agregar(
            documentos=[pregunta],
            metadatos=[
                {
                    "contenido_tecnico": contenido_tecnico,
                    "resumen_para_persona": resumen_para_persona or "",
                    "fuente": fuente,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    # Campo legacy para no romper lectores antiguos durante la migracion.
                    "respuesta": contenido_tecnico,
                }
            ],
            ids=[doc_id],
        )
        if guardado:
            logger.info("Oracle Cache: nueva respuesta almacenada (id=%s).", doc_id)
        return guardado

    def close(self) -> None:
        """Cierra el cliente Chroma usado por el cache del Oraculo."""
        self.client.close()
