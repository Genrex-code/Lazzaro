"""
chroma_client.py - Encapsula toda interaccion directa con ChromaDB.
Proporciona una interfaz limpia y tolerante a fallos para el resto del sistema.
"""

from pathlib import Path
from typing import Any, ClassVar, TypeAlias, cast
import gc
import logging
import math
import threading
import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.api.types import Metadata, QueryResult, Where
from chromadb.config import Settings as ChromaSettings
try:
    from chromadb.api.shared_system_client import SharedSystemClient
except Exception:  # pragma: no cover - compatibilidad con versiones antiguas
    SharedSystemClient = None  # type: ignore[assignment]

from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure
from memory.memory_write_types import (
    ExactLookupReceipt,
    ExactLookupStatus,
    ExactMemoryRow,
)
from memory.chroma_search_types import (
    ChromaSearchFailureCodeV1,
    ChromaSearchResultV1,
    ChromaSearchStatusV1,
)

logger = get_logger(__name__)

TransportMetadata: TypeAlias = dict[str, Any] | None
AddMetadataInput: TypeAlias = (
    dict[str, Any] | list[TransportMetadata] | None
)
UpdateMetadataInput: TypeAlias = dict[str, Any] | list[dict[str, Any]] | None


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
            try:
                log_safe_boundary_failure(
                    logger, component="chroma.cache_cleanup", code="CHROMA_CLEANUP_FAILED",
                    error=e, level=logging.DEBUG,
                )
            except Exception:
                pass

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
            except BaseException as e:
                try:
                    log_safe_boundary_failure(
                        logger, component="chroma.initialize", code="CHROMA_UNAVAILABLE",
                        error=e,
                    )
                except Exception:
                    pass
                # Release por la misma ruta de refcount; no abandonar el handle parcial.
                try:
                    self.close()
                except BaseException as cleanup_error:
                    try:
                        log_safe_boundary_failure(
                            logger, component="chroma.initialize.rollback",
                            code="CHROMA_CLEANUP_FAILED", error=cleanup_error,
                            level=logging.WARNING,
                        )
                    except Exception:
                        pass
                if not isinstance(e, Exception):
                    raise

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

    @staticmethod
    def _normalizar_metadatos_add(
        metadatos: AddMetadataInput,
        cantidad_ids: int,
    ) -> tuple[bool, list[TransportMetadata] | None, str]:
        """Canonicaliza ausencia a None y devuelve copias sin mutar al caller."""
        if metadatos is None:
            return True, None, ""

        filas: list[TransportMetadata]
        if isinstance(metadatos, dict):
            if cantidad_ids != 1:
                return False, None, "single_metadata_batch_mismatch"
            filas = [metadatos]
        elif isinstance(metadatos, list):
            if not metadatos:
                return False, None, "empty_metadata_batch"
            if len(metadatos) != cantidad_ids:
                return False, None, "metadata_batch_mismatch"
            filas = metadatos
        else:
            return False, None, "invalid_metadata_container"

        normalizadas: list[TransportMetadata] = []
        for metadata in filas:
            if metadata is None:
                normalizadas.append(None)
                continue
            if not isinstance(metadata, dict):
                return False, None, "invalid_metadata_row"
            normalizadas.append(dict(metadata) if metadata else None)
        return True, normalizadas, ""

    @staticmethod
    def _normalizar_metadatos_update(
        metadatos: UpdateMetadataInput,
        cantidad_ids: int,
    ) -> tuple[bool, list[dict[str, Any]] | None, str]:
        """Exige cambios no vacios; None seria un no-op exitoso en Chroma."""
        if metadatos is None:
            return False, None, "absent_metadata"

        filas: list[dict[str, Any]]
        if isinstance(metadatos, dict):
            if cantidad_ids != 1:
                return False, None, "single_metadata_batch_mismatch"
            filas = [metadatos]
        elif isinstance(metadatos, list):
            if not metadatos:
                return False, None, "empty_metadata_batch"
            if len(metadatos) != cantidad_ids:
                return False, None, "metadata_batch_mismatch"
            filas = metadatos
        else:
            return False, None, "invalid_metadata_container"

        normalizadas: list[dict[str, Any]] = []
        for metadata in filas:
            if not isinstance(metadata, dict):
                return False, None, "invalid_metadata_row"
            if not metadata:
                return False, None, "empty_metadata"
            normalizadas.append(dict(metadata))
        return True, normalizadas, ""

    @staticmethod
    def _normalizar_metadata_leida(metadata: Metadata | None) -> Metadata | None:
        """Usa None como unica forma de metadata ausente en lecturas."""
        if metadata is None or not metadata:
            return None
        return dict(metadata)

    @classmethod
    def _normalizar_resultado_query(cls, resultado: QueryResult) -> QueryResult:
        copia = dict(resultado)
        metadatas = resultado.get("metadatas")
        if metadatas is not None:
            copia["metadatas"] = [
                None
                if batch is None
                else [cls._normalizar_metadata_leida(metadata) for metadata in batch]
                for batch in metadatas
            ]
        return cast(QueryResult, copia)

    @classmethod
    def _normalizar_resultado_listado(
        cls,
        resultado: dict[str, list[Any]],
    ) -> dict[str, list[Any]]:
        copia = dict(resultado)
        metadatas = resultado.get("metadatas")
        if metadatas is not None:
            copia["metadatas"] = [
                cls._normalizar_metadata_leida(metadata)
                for metadata in metadatas
            ]
        return copia

    def agregar(
        self,
        documentos: list[str],
        metadatos: AddMetadataInput,
        ids: list[str],
    ) -> bool:
        """
        Anade una o varias entradas a la coleccion.
        Retorna True si la operacion fue exitosa, False en caso de error.
        """
        valido, metadatos_backend, reason_code = self._normalizar_metadatos_add(
            metadatos,
            len(ids),
        )
        if not valido:
            logger.warning(
                "Metadata rechazada. operation=add_metadata reason_code=%s input_type=%s",
                reason_code,
                type(metadatos).__name__,
            )
            return False

        collection = self._collection
        if collection is None:
            logger.warning("Intento de agregar a ChromaDB sin conexion.")
            return False
        with self._lock:  
            try:
                collection.add(
                    documents=documentos,
                    metadatas=metadatos_backend,
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
        metadatos: UpdateMetadataInput,
    ) -> bool:
        """Actualiza metadata escalar sin reescribir documentos ni embeddings."""
        valido, metadatos_backend, reason_code = self._normalizar_metadatos_update(
            metadatos,
            len(ids),
        )
        if not valido or metadatos_backend is None:
            logger.warning(
                "Metadata rechazada. operation=update_metadata reason_code=%s input_type=%s",
                reason_code,
                type(metadatos).__name__,
            )
            return False

        collection = self._collection
        if collection is None:
            logger.warning("Intento de actualizar ChromaDB sin conexion.")
            return False
        with self._lock:
            try:
                collection.update(ids=ids, metadatas=metadatos_backend)
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
                resultado = collection.query(
                    query_texts=[consulta],
                    n_results=n_resultados,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                return self._normalizar_resultado_query(resultado)
            except Exception as e:
                logger.error(
                    "Fallo de retrieval. component=chroma_client operation=search "
                    "reason_code=BACKEND_FAILURE error_type=%s",
                    type(e).__name__,
                )
                return None

    @classmethod
    def _validar_resultado_query_tipado(
        cls,
        resultado: object,
    ) -> QueryResult | None:
        """Valida el envelope incluido por ``buscar_con_estado`` sin coerciones."""
        if not isinstance(resultado, dict):
            return None

        required = ("ids", "documents", "metadatas", "distances")
        batches: dict[str, list[Any]] = {}
        for field in required:
            value = resultado.get(field)
            if not isinstance(value, list) or not value:
                return None
            if any(not isinstance(batch, list) for batch in value):
                return None
            batches[field] = value

        batch_count = len(batches["ids"])
        if batch_count != 1:
            return None
        if any(len(value) != batch_count for value in batches.values()):
            return None

        for batch_index in range(batch_count):
            row_count = len(batches["ids"][batch_index])
            if any(
                len(batches[field][batch_index]) != row_count
                for field in required[1:]
            ):
                return None
            for row_index in range(row_count):
                memory_id = batches["ids"][batch_index][row_index]
                document = batches["documents"][batch_index][row_index]
                metadata = batches["metadatas"][batch_index][row_index]
                distance = batches["distances"][batch_index][row_index]
                if not isinstance(memory_id, str) or not memory_id:
                    return None
                if not isinstance(document, str):
                    return None
                if metadata is not None and not isinstance(metadata, dict):
                    return None
                if (
                    isinstance(distance, bool)
                    or not isinstance(distance, (int, float))
                    or not math.isfinite(float(distance))
                ):
                    return None

        return cls._normalizar_resultado_query(cast(QueryResult, resultado))

    def buscar_con_estado(
        self,
        consulta: str,
        n_resultados: int = 5,
        where: Where | None = None,
    ) -> ChromaSearchResultV1:
        """Busca con status tipado; no cambia la semántica legacy de ``buscar``."""
        collection = self._collection
        if collection is None:
            logger.warning(
                "Typed retrieval unavailable. component=chroma_client "
                "operation=search reason_code=COLLECTION_UNAVAILABLE"
            )
            return ChromaSearchResultV1(
                contract_version=1,
                status=ChromaSearchStatusV1.UNAVAILABLE,
                payload=None,
                failure_code=ChromaSearchFailureCodeV1.COLLECTION_UNAVAILABLE,
            )

        with self._lock:
            try:
                raw = collection.query(
                    query_texts=[consulta],
                    n_results=n_resultados,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as error:
                logger.error(
                    "Typed retrieval failed. component=chroma_client "
                    "operation=search reason_code=BACKEND_FAILURE error_type=%s",
                    type(error).__name__,
                )
                return ChromaSearchResultV1(
                    contract_version=1,
                    status=ChromaSearchStatusV1.ERROR,
                    payload=None,
                    failure_code=ChromaSearchFailureCodeV1.BACKEND_FAILURE,
                )

        payload = self._validar_resultado_query_tipado(raw)
        if payload is None:
            logger.error(
                "Typed retrieval rejected malformed response. "
                "component=chroma_client operation=search "
                "reason_code=MALFORMED_RESPONSE"
            )
            return ChromaSearchResultV1(
                contract_version=1,
                status=ChromaSearchStatusV1.ERROR,
                payload=None,
                failure_code=ChromaSearchFailureCodeV1.MALFORMED_RESPONSE,
            )

        row_count = sum(len(batch) for batch in payload["ids"])
        return ChromaSearchResultV1(
            contract_version=1,
            status=(
                ChromaSearchStatusV1.SUCCESS
                if row_count > 0
                else ChromaSearchStatusV1.EMPTY
            ),
            payload=payload,
            failure_code=None,
        )

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
                resultado = cast(dict[str, list[Any]], collection.get(**kwargs))
                return self._normalizar_resultado_listado(resultado)
            except Exception as e:
                logger.error(
                    "Error al listar documentos en ChromaDB: error_type=%s",
                    type(e).__name__,
                )
                return None

    def obtener_por_ids(self, ids: list[str]) -> ExactLookupReceipt:
        """Lee IDs exactos con ``collection.get``; nunca usa similitud vectorial."""
        requested = tuple(dict.fromkeys(str(item) for item in ids if str(item)))
        if not requested:
            return ExactLookupReceipt(
                status=ExactLookupStatus.ABSENT,
                requested_ids=(),
            )

        collection = self._collection
        if collection is None:
            logger.warning(
                "Exact lookup unavailable. operation=get_by_ids reason_code=BACKEND_FAILURE"
            )
            return ExactLookupReceipt(
                status=ExactLookupStatus.FAILED,
                requested_ids=requested,
            )
        with self._lock:
            try:
                raw = cast(
                    dict[str, list[Any]],
                    collection.get(
                        ids=list(requested),
                        include=["documents", "metadatas"],
                    ),
                )
            except Exception as e:
                logger.error(
                    "Exact lookup failed. operation=get_by_ids "
                    "reason_code=BACKEND_FAILURE error_type=%s",
                    type(e).__name__,
                )
                return ExactLookupReceipt(
                    status=ExactLookupStatus.FAILED,
                    requested_ids=requested,
                )

        raw_ids = raw.get("ids") or []
        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        rows_by_id: dict[str, ExactMemoryRow] = {}
        for index, memory_id in enumerate(raw_ids):
            memory_id = str(memory_id)
            document = str(documents[index]) if index < len(documents) else ""
            metadata = metadatas[index] if index < len(metadatas) else None
            rows_by_id[memory_id] = ExactMemoryRow(
                id=memory_id,
                document=document,
                metadata=self._normalizar_metadata_leida(metadata),
            )
        rows = tuple(rows_by_id[memory_id] for memory_id in requested if memory_id in rows_by_id)
        return ExactLookupReceipt(
            status=ExactLookupStatus.PRESENT if rows else ExactLookupStatus.ABSENT,
            requested_ids=requested,
            rows=rows,
        )

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
                logger.error(
                    "Error al eliminar documentos: error_type=%s",
                    type(e).__name__,
                )
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

            es_ultimo_cliente = self._liberar_cliente(self.db_path)
            close_error: BaseException | None = None
            try:
                if es_ultimo_cliente:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
                    else:
                        system = getattr(client, "_system", None)
                        stop = getattr(system, "stop", None)
                        if callable(stop):
                            stop()
            except BaseException as error:
                close_error = error
                try:
                    log_safe_boundary_failure(
                        logger, component="chroma.close", code="CHROMA_CLEANUP_FAILED",
                        error=error, level=logging.WARNING,
                    )
                except Exception:
                    pass
            finally:
                if es_ultimo_cliente:
                    self._limpiar_cache_sistema_chroma(client)
                del collection
                del client
                gc.collect()
            if close_error is not None:
                raise close_error
