"""
chroma_client.py - Encapsula toda interacción directa con ChromaDB.
Proporciona una interfaz limpia y tolerante a fallos para el resto del sistema.
"""

from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from core.logger import get_logger

logger = get_logger(__name__)


class ChromaClient:
    """
    Cliente ligero sobre una única colección de ChromaDB.
    Abstrae detalles de inicialización, añadido, consulta y eliminación,
    y captura errores para que un fallo en la base de datos no derribe al agente.
    """

    def __init__(
        self,
        db_path: str = "./lazzaro_data",
        collection_name: str = "experiencias",
    ):
        """
        Args:
            db_path: Ruta del almacenamiento persistente de ChromaDB.
            collection_name: Nombre de la colección a utilizar/crear.
        """
        self.db_path = db_path
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._inicializar()

    # ------------------------------------------------------------------
    # Inicialización y estado
    # ------------------------------------------------------------------

    def _inicializar(self):
        """Crea o conecta al cliente y a la colección, con manejo de errores."""
        try:
            self._client = chromadb.PersistentClient(
                path=self.db_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "ChromaDB inicializado en '%s', colección '%s' lista.",
                self.db_path,
                self.collection_name,
            )
        except Exception as e:
            logger.error("Error al inicializar ChromaDB: %s", e)
            # El cliente queda a None; los métodos de consulta devolverán vacío.
            self._client = None
            self._collection = None

    @property
    def disponible(self) -> bool:
        """Indica si la conexión con la colección está activa."""
        return self._collection is not None

    # ------------------------------------------------------------------
    # Operaciones CRUD
    # ------------------------------------------------------------------

    def agregar(
        self,
        documentos: list[str],
        metadatos: list[dict],
        ids: list[str],
    ) -> bool:
        """
        Añade una o varias entradas a la colección.
        Retorna True si la operación fue exitosa, False en caso de error.
        """
        if not self.disponible:
            logger.warning("Intento de agregar a ChromaDB sin conexión.")
            return False
        try:
            self._collection.add(
                documents=documentos,
                metadatas=metadatos,
                ids=ids,
            )
            logger.debug("Agregados %d documentos a '%s'.", len(ids), self.collection_name)
            return True
        except Exception as e:
            logger.error("Error al agregar documentos a ChromaDB: %s", e)
            return False

    def buscar(
        self,
        consulta: str,
        n_resultados: int = 5,
        where: Optional[dict] = None,
    ) -> dict:
        """
        Realiza una búsqueda por similitud semántica.
        Retorna un diccionario con la respuesta cruda de ChromaDB,
        o un diccionario vacío si ocurre un error.
        """
        if not self.disponible:
            logger.warning("Búsqueda en ChromaDB sin conexión.")
            return {}
        try:
            resultado = self._collection.query(
                query_texts=[consulta],
                n_results=n_resultados,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            return resultado
        except Exception as e:
            logger.error("Error al buscar en ChromaDB: %s", e)
            return {}

    def eliminar(self, ids: list[str]) -> bool:
        """Elimina documentos por sus IDs. Retorna True si fue exitoso."""
        if not self.disponible:
            logger.warning("Intento de eliminar en ChromaDB sin conexión.")
            return False
        try:
            self._collection.delete(ids=ids)
            logger.debug("Eliminados %d documentos de '%s'.", len(ids), self.collection_name)
            return True
        except Exception as e:
            logger.error("Error al eliminar documentos: %s", e)
            return False

    def contar(self) -> int:
        """Devuelve el número de documentos en la colección, o -1 si hay error."""
        if not self.disponible:
            return -1
        try:
            return self._collection.count()
        except Exception as e:
            logger.error("Error al contar documentos: %s", e)
            return -1