"""
chroma_client.py - Encapsula toda interaccion directa con ChromaDB.
Proporciona una interfaz limpia y tolerante a fallos para el resto del sistema.
"""

from pathlib import Path
from typing import Any, ClassVar, cast
import gc
import threading
import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.api.types import Metadata, QueryResult, Where
from chromadb.config import Settings as ChromaSettings
try:
    from chromadb.api.client import SharedSystemClient
except Exception:  # pragma: no cover - compatibilidad con versiones antiguas
    SharedSystemClient = None  # type: ignore[assignment]

from core.logger import get_logger

logger = get_logger(__name__)


class ChromaClient:
    """
    Cliente ligero sobre una unica coleccion de ChromaDB.
    Abstrae detalles de inicializacion, anadido, consulta y eliminacion,
    y captura errores para que un fallo en la base de datos no derribe al agente.
    """

    _locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _locks_by_db_path: ClassVar[dict[str, Any]] = {}
    _refcounts_by_db_path: ClassVar[dict[str, int]] = {}

    def __init__(
        self,
        db_path: str = "./lazzaro_data",
        collection_name: str = "experiencias",
    ) -> None:
        """
        Args:
            db_path: Ruta del almacenamiento persistente de ChromaDB.
            collection_name: Nombre de la coleccion a utilizar o crear.
        """
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.collection_name = collection_name
        self._client: ClientAPI | None = None
        #esta es una forma de decir que esto puede ser cualquiercosa o puede ser nada vacia
        self._collection: Any | None = None
        self._lock = self._obtener_lock(self.db_path)
        self._closed = False
        self._registrar_cliente(self.db_path)
        self._inicializar()

    @classmethod
    def _obtener_lock(cls, db_path: str) -> Any:
        """Devuelve una compuerta compartida por base Chroma dentro del proceso."""
        with cls._locks_guard:
            lock = cls._locks_by_db_path.get(db_path)
            if lock is None:
                lock = threading.RLock()
                cls._locks_by_db_path[db_path] = lock
            return lock

    @classmethod
    def _registrar_cliente(cls, db_path: str) -> None:
        with cls._locks_guard:
            cls._refcounts_by_db_path[db_path] = cls._refcounts_by_db_path.get(db_path, 0) + 1

    @classmethod
    def _liberar_cliente(cls, db_path: str) -> bool:
        """Devuelve True si ya no quedan clientes vivos para esa base."""
        with cls._locks_guard:
            restantes = cls._refcounts_by_db_path.get(db_path, 0) - 1
            if restantes <= 0:
                cls._refcounts_by_db_path.pop(db_path, None)
                return True
            cls._refcounts_by_db_path[db_path] = restantes
            return False

    @staticmethod
    def _limpiar_cache_sistema_chroma(client: ClientAPI | None) -> None:
        """Libera referencias globales que Chroma mantiene en algunas versiones."""
        try:
            clear = getattr(client, "clear_system_cache", None) if client is not None else None
            if callable(clear):
                clear()
                return
            if SharedSystemClient is not None:
                SharedSystemClient.clear_system_cache()
        except Exception as e:
            logger.debug("No se pudo limpiar cache global de ChromaDB: %s", e)

    # Inicializacion y estado

    def _inicializar(self) -> None:
        """Crea o conecta al cliente y a la coleccion, con manejo de errores."""
        with self._lock:
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
                    "ChromaDB inicializado en '%s', coleccion '%s' lista.",
                    self.db_path,
                    self.collection_name,
                )
            except Exception as e:
                logger.error("Error al inicializar ChromaDB: %s", e)
                # El cliente queda a None; los metodos devolveran un valor seguro.
                self._client = None
                self._collection = None

    @property
    def disponible(self) -> bool:
        """Indica si la conexion con la coleccion esta activa."""
        return self._collection is not None

    @staticmethod
    def combinar_where(base: Where | None, extra: Where | None) -> Where | None:
        """Combina dos filtros Chroma usando $and cuando ambos existen."""
        if not base:
            return extra
        if not extra:
            return base
        if isinstance(base, dict) and "$and" in base and isinstance(base["$and"], list):
            return {"$and": [*base["$and"], extra]}
        return {"$and": [base, extra]}

    # Operaciones CRUD

    def agregar(
        self,
        documentos: list[str],
        metadatos: list[dict[str, Any]],
        ids: list[str],
    ) -> bool:
        """
        Anade una o varias entradas a la coleccion.
        Retorna True si la operacion fue exitosa, False en caso de error.
        """
        collection = self._collection
        if collection is None:
            logger.warning("Intento de agregar a ChromaDB sin conexion.")
            return False
        with self._lock:  
            try:
                collection.add(
                    documents=documentos,
                    metadatas=metadatos,
                    ids=ids,
                )
                logger.debug("Agregados %d documentos a '%s'.", len(ids), self.collection_name)
                return True
            except Exception as e:
                logger.error(
                    "Error al agregar documentos: error_type=%s",
                    type(e).__name__,
                )
                return False

    def actualizar_metadatos(
        self,
        ids: list[str],
        metadatos: list[dict[str, Any]],
    ) -> bool:
        """Actualiza metadata escalar sin reescribir documentos ni embeddings."""
        collection = self._collection
        if collection is None:
            logger.warning("Intento de actualizar ChromaDB sin conexion.")
            return False
        if len(ids) != len(metadatos):
            logger.error("IDs y metadatos deben tener la misma longitud.")
            return False
        with self._lock:
            try:
                collection.update(ids=ids, metadatas=metadatos)
                logger.debug("Actualizada metadata de %d documentos.", len(ids))
                return True
            except Exception as e:
                logger.error(
                    "Error al actualizar metadata: error_type=%s",
                    type(e).__name__,
                )
                return False

    def buscar(
        self,
        consulta: str,
        n_resultados: int = 5,
        where: Where | None = None,
    ) -> QueryResult | None:
        """
        Realiza una busqueda por similitud semantica.
        Retorna la respuesta cruda de ChromaDB, o None si ocurre un error.
        """
        collection = self._collection
        if collection is None:
            logger.warning("Busqueda en ChromaDB sin conexion.")
            return None
        with self._lock:
            try:
                return collection.query(
                    query_texts=[consulta],
                    n_results=n_resultados,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as e:
                logger.error("Error al buscar en ChromaDB: %s", e)
                return None

    def listar(
        self,
        where: Where | None = None,
        limite: int | None = None,
    ) -> dict[str, list[Any]] | None:
        """
        Recupera documentos sin consulta semantica.
        Sirve para tareas de mantenimiento como consolidacion o limpieza.
        """
        collection = self._collection
        if collection is None:
            logger.warning("Lectura en ChromaDB sin conexion.")
            return None
        with self._lock:
            try:
                kwargs: dict[str, Any] = {
                    "where": where,
                    "include": ["documents", "metadatas"],
                }
                if limite is not None:
                    kwargs["limit"] = limite
                return cast(dict[str, list[Any]], collection.get(**kwargs))
            except Exception as e:
                logger.error(
                    "Error al listar documentos en ChromaDB: error_type=%s",
                    type(e).__name__,
                )
                return None

    def eliminar(self, ids: list[str]) -> bool:
        """Elimina documentos por sus IDs. Retorna True si fue exitoso."""
        collection = self._collection
        if collection is None:
            logger.warning("Intento de eliminar en ChromaDB sin conexion.")
            return False
        with self._lock:
            try:
                collection.delete(ids=ids)
                logger.debug("Eliminados %d documentos de '%s'.", len(ids), self.collection_name)
                return True
            except Exception as e:
                logger.error("Error al eliminar documentos: %s", e)
                return False

    def contar(self) -> int:
        """Devuelve el numero de documentos en la coleccion, o -1 si hay error."""
        collection = self._collection
        if collection is None:
            return -1
        with self._lock:
            try:
                return collection.count()
            except Exception as e:
                logger.error("Error al contar documentos: %s", e)
                return -1

    def close(self) -> None:
        """Libera recursos de ChromaDB para evitar locks de archivos en Windows."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            collection = self._collection
            self._collection = None
            self._client = None

            try:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
                else:
                    system = getattr(client, "_system", None)
                    stop = getattr(system, "stop", None)
                    if callable(stop):
                        stop()
            except Exception as e:
                logger.warning("No se pudo cerrar ChromaDB limpiamente: %s", e)
            finally:
                es_ultimo_cliente = self._liberar_cliente(self.db_path)
                if es_ultimo_cliente:
                    self._limpiar_cache_sistema_chroma(client)
                del collection
                del client
                gc.collect()
