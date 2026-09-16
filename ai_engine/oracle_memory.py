"""Semantic Oracle cache with versioned identity and typed retrieval receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import uuid

from pydantic import ValidationError

from ai_engine.knowledge_provenance import (
    CacheEntryIdentityV1,
    CacheLookupStatusV1,
    CacheReceiptV1,
    CacheStoreStatusV1,
    KNOWLEDGE_PROVENANCE_VERSION,
    KnowledgeProducerKindV1,
    ORACLE_CACHE_SCHEMA_VERSION,
    ORACLE_KNOWLEDGE_CONTRACT_VERSION,
    ProducerReceiptV1,
    RetrievalReceiptV1,
    cache_receipt_for_lookup,
    cache_receipt_not_checked,
    canonical_producer_json,
    legacy_unknown_provenance,
    oracle_cache_logical_key,
    oracle_query_sha256,
    provenance_for,
    with_cache_store_status,
)
from ai_engine.schemas import OracleResult
from ai_engine.structured_parse_types import StructuredParseStatus
from core.logger import get_logger
from memory.chroma_client import ChromaClient
from memory.chroma_search_types import ChromaSearchStatusV1


logger = get_logger(__name__)

_VERSION_FIELDS = (
    "oracle_cache_schema_version",
    "oracle_knowledge_contract_version",
    "knowledge_provenance_version",
)


@dataclass(frozen=True, slots=True)
class OracleCacheLookupResultV1:
    """Typed bridge: a miss/failure remains observable without cached content."""

    contract_version: int
    cached_result: OracleResult | None
    cache_receipt: CacheReceiptV1

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("contract_version must be the exact integer 1")
        if not isinstance(self.cache_receipt, CacheReceiptV1):
            raise ValueError("cache_receipt must be CacheReceiptV1")
        hit = self.cache_receipt.lookup_status in {
            CacheLookupStatusV1.HIT,
            CacheLookupStatusV1.LEGACY_ENTRY,
        }
        if hit != isinstance(self.cached_result, OracleResult):
            raise ValueError("cached_result must exist exactly for cache hits")
        if (
            self.cached_result is not None
            and self.cached_result.provenance.retrieval.cache != self.cache_receipt
        ):
            raise ValueError("cached_result and cache_receipt must share one receipt")


class OracleMemory:
    """Cache de conocimiento; la similitud no sustituye provenance histórica."""

    DEFAULT_CACHE_DISTANCE = 0.35

    def __init__(self, db_path: str = "./lazzaro_data"):
        self.db_path = db_path
        self.client = ChromaClient(db_path=db_path, collection_name="oracle_cache")

    @property
    def disponible(self) -> bool:
        return self.client.disponible

    def buscar_respuesta_cacheada(
        self,
        pregunta: str,
        umbral_similitud: float = DEFAULT_CACHE_DISTANCE,
    ) -> str | None:
        """Compatibilidad legacy: devuelve solo contenido si hubo hit tipado."""
        result = self.buscar_resultado_cacheado_con_recibo(
            pregunta,
            umbral_similitud,
        ).cached_result
        return result.contenido_tecnico if result is not None else None

    def buscar_resultado_cacheado(
        self,
        pregunta: str,
        umbral_similitud: float = DEFAULT_CACHE_DISTANCE,
    ) -> OracleResult | None:
        """Compatibilidad legacy: el nuevo flujo usa el bridge con recibo."""
        return self.buscar_resultado_cacheado_con_recibo(
            pregunta,
            umbral_similitud,
        ).cached_result

    def buscar_resultado_cacheado_con_recibo(
        self,
        pregunta: str,
        umbral_similitud: float = DEFAULT_CACHE_DISTANCE,
    ) -> OracleCacheLookupResultV1:
        """Consulta una vez y conserva MISS/health/version hasta el consumidor."""
        threshold = self._validar_umbral(umbral_similitud)
        if not isinstance(pregunta, str) or not pregunta.strip():
            return OracleCacheLookupResultV1(1, None, cache_receipt_not_checked())

        search = self.client.buscar_con_estado(consulta=pregunta, n_resultados=1)
        if search.status is ChromaSearchStatusV1.UNAVAILABLE:
            return self._without_result(
                pregunta, CacheLookupStatusV1.UNAVAILABLE, threshold
            )
        if search.status is ChromaSearchStatusV1.ERROR:
            return self._without_result(pregunta, CacheLookupStatusV1.ERROR, threshold)
        if search.status is ChromaSearchStatusV1.EMPTY:
            return self._without_result(pregunta, CacheLookupStatusV1.MISS, threshold)

        payload = search.payload
        if payload is None:
            return self._without_result(pregunta, CacheLookupStatusV1.ERROR, threshold)
        ids = payload["ids"][0]
        documents = payload["documents"][0]  # type: ignore[index]
        metadatas = payload["metadatas"][0]  # type: ignore[index]
        distances = payload["distances"][0]  # type: ignore[index]
        if not ids:
            return self._without_result(pregunta, CacheLookupStatusV1.MISS, threshold)

        entry_id = ids[0]
        stored_query = documents[0]
        metadata = metadatas[0]
        distance = float(distances[0])
        if distance > threshold:
            logger.debug(
                "Oracle cache MISS reason=distance threshold=%.3f distance=%.3f",
                threshold,
                distance,
            )
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MISS,
                threshold,
                distance=distance,
            )

        version_presence = (
            [field in metadata for field in _VERSION_FIELDS]
            if isinstance(metadata, dict)
            else [False, False, False]
        )
        if not any(version_presence):
            return self._read_legacy(
                pregunta=pregunta,
                threshold=threshold,
                distance=distance,
                entry_id=entry_id,
                stored_query=stored_query,
                metadata=metadata,
            )
        if not all(version_presence) or not isinstance(metadata, dict):
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MALFORMED_ENTRY,
                threshold,
                distance=distance,
            )

        versions = tuple(metadata[field] for field in _VERSION_FIELDS)
        if any(type(value) is not int for value in versions):
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MALFORMED_ENTRY,
                threshold,
                distance=distance,
            )
        if versions != (
            ORACLE_CACHE_SCHEMA_VERSION,
            ORACLE_KNOWLEDGE_CONTRACT_VERSION,
            KNOWLEDGE_PROVENANCE_VERSION,
        ):
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.INCOMPATIBLE_VERSION,
                threshold,
                distance=distance,
            )
        return self._read_v1(
            pregunta=pregunta,
            threshold=threshold,
            distance=distance,
            entry_id=entry_id,
            stored_query=stored_query,
            metadata=metadata,
        )

    def _read_legacy(
        self,
        *,
        pregunta: str,
        threshold: float,
        distance: float,
        entry_id: str,
        stored_query: str,
        metadata: object,
    ) -> OracleCacheLookupResultV1:
        if (
            not isinstance(entry_id, str)
            or not entry_id.strip()
            or not isinstance(stored_query, str)
            or not stored_query.strip()
            or not isinstance(metadata, dict)
        ):
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MALFORMED_ENTRY,
                threshold,
                distance=distance,
            )
        content = metadata.get("contenido_tecnico") or metadata.get("respuesta")
        if not isinstance(content, str) or not content.strip():
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MALFORMED_ENTRY,
                threshold,
                distance=distance,
            )
        summary_raw = metadata.get("resumen_para_persona")
        if summary_raw is not None and not isinstance(summary_raw, str):
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MALFORMED_ENTRY,
                threshold,
                distance=distance,
            )
        matched = CacheEntryIdentityV1(
            entry_id=entry_id,
            logical_key=None,
            stored_query_sha256=None,
            cache_schema_version=None,
            knowledge_contract_version=None,
            provenance_version=None,
            entry_created_at_utc=None,
        )
        receipt = cache_receipt_for_lookup(
            pregunta,
            status=CacheLookupStatusV1.LEGACY_ENTRY,
            threshold=threshold,
            matched_entry=matched,
            distance=distance,
        )
        result = OracleResult(
            pregunta_original=pregunta,
            contenido_tecnico=content.strip(),
            resumen_para_persona=(summary_raw.strip() or None) if summary_raw else None,
            provenance=legacy_unknown_provenance(
                retrieval=RetrievalReceiptV1(cache=receipt)
            ),
        )
        logger.info("Oracle cache LEGACY_ENTRY distance=%.3f", distance)
        return OracleCacheLookupResultV1(1, result, receipt)

    def _read_v1(
        self,
        *,
        pregunta: str,
        threshold: float,
        distance: float,
        entry_id: str,
        stored_query: str,
        metadata: dict,
    ) -> OracleCacheLookupResultV1:
        try:
            content = metadata.get("contenido_tecnico")
            summary = metadata.get("resumen_para_persona")
            stored_hash = metadata.get("stored_query_sha256")
            logical_key = metadata.get("entry_logical_key")
            created_at = self._parse_utc(metadata.get("entry_created_at_utc"))
            producer_json = metadata.get("producer_receipt_json")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("invalid content")
            if summary is not None and not isinstance(summary, str):
                raise ValueError("invalid summary")
            if not isinstance(stored_query, str) or not stored_query.strip():
                raise ValueError("invalid stored query")
            if stored_hash != oracle_query_sha256(stored_query):
                raise ValueError("stored query hash mismatch")
            if logical_key != oracle_cache_logical_key(stored_query):
                raise ValueError("stored logical key mismatch")
            if not isinstance(producer_json, str):
                raise ValueError("producer receipt is absent")
            producer_payload = json.loads(producer_json)
            if not isinstance(producer_payload, dict):
                raise ValueError("producer receipt must be an object")
            producer = ProducerReceiptV1.model_validate(producer_payload)
            matched = CacheEntryIdentityV1(
                entry_id=entry_id,
                logical_key=logical_key,
                stored_query_sha256=stored_hash,
                cache_schema_version=metadata["oracle_cache_schema_version"],
                knowledge_contract_version=metadata[
                    "oracle_knowledge_contract_version"
                ],
                provenance_version=metadata["knowledge_provenance_version"],
                entry_created_at_utc=created_at,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
            return self._without_result(
                pregunta,
                CacheLookupStatusV1.MALFORMED_ENTRY,
                threshold,
                distance=distance,
            )

        receipt = cache_receipt_for_lookup(
            pregunta,
            status=CacheLookupStatusV1.HIT,
            threshold=threshold,
            matched_entry=matched,
            distance=distance,
        )
        result = OracleResult(
            pregunta_original=pregunta,
            contenido_tecnico=content.strip(),
            resumen_para_persona=(summary.strip() or None) if summary else None,
            provenance=provenance_for(producer, receipt),
        )
        logger.info("Oracle cache HIT distance=%.3f", distance)
        return OracleCacheLookupResultV1(1, result, receipt)

    def guardar_resultado_en_cache(self, resultado: OracleResult) -> CacheReceiptV1:
        """Stores one eligible generated VALID result and reports the outcome."""
        receipt = resultado.provenance.retrieval.cache
        producer = resultado.provenance.producer
        if resultado.cache_hit:
            return receipt
        eligible = (
            producer.kind is KnowledgeProducerKindV1.PROVIDER_GENERATED
            and producer.final_parse_status is StructuredParseStatus.VALID
        )
        if not eligible:
            return with_cache_store_status(
                receipt, CacheStoreStatusV1.SKIPPED_POLICY
            )

        question = resultado.pregunta_original
        content = resultado.contenido_tecnico
        if not self.disponible or not question.strip() or not content.strip():
            return with_cache_store_status(receipt, CacheStoreStatusV1.FAILED)

        entry_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)
        identity = CacheEntryIdentityV1(
            entry_id=entry_id,
            logical_key=oracle_cache_logical_key(question),
            stored_query_sha256=oracle_query_sha256(question),
            cache_schema_version=ORACLE_CACHE_SCHEMA_VERSION,
            knowledge_contract_version=ORACLE_KNOWLEDGE_CONTRACT_VERSION,
            provenance_version=KNOWLEDGE_PROVENANCE_VERSION,
            entry_created_at_utc=created_at,
        )
        metadata = {
            "contenido_tecnico": content,
            "resumen_para_persona": resultado.resumen_para_persona or "",
            "oracle_cache_schema_version": ORACLE_CACHE_SCHEMA_VERSION,
            "oracle_knowledge_contract_version": ORACLE_KNOWLEDGE_CONTRACT_VERSION,
            "knowledge_provenance_version": KNOWLEDGE_PROVENANCE_VERSION,
            "entry_logical_key": identity.logical_key,
            "stored_query_sha256": identity.stored_query_sha256,
            "entry_created_at_utc": created_at.isoformat(),
            "producer_receipt_json": canonical_producer_json(producer),
        }
        stored = self.client.agregar(
            documentos=[question], metadatos=[metadata], ids=[entry_id]
        )
        if not stored:
            return with_cache_store_status(receipt, CacheStoreStatusV1.FAILED)
        logger.info("Oracle cache stored entry_id=%s", entry_id)
        return with_cache_store_status(
            receipt,
            CacheStoreStatusV1.STORED,
            stored_entry=identity,
        )

    def guardar_en_cache(
        self,
        pregunta: str,
        contenido_tecnico: str,
        resumen_para_persona: str | None = None,
        fuente: str = "api",
    ) -> bool:
        """Legacy writer kept for old callers; productive v1 uses a result."""
        if not self.disponible or not pregunta.strip() or not contenido_tecnico.strip():
            return False
        doc_id = str(uuid.uuid4())
        stored = self.client.agregar(
            documentos=[pregunta],
            metadatos=[
                {
                    "contenido_tecnico": contenido_tecnico,
                    "resumen_para_persona": resumen_para_persona or "",
                    "fuente": fuente,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "respuesta": contenido_tecnico,
                }
            ],
            ids=[doc_id],
        )
        if stored:
            logger.info("Oracle legacy cache stored entry_id=%s", doc_id)
        return stored

    @staticmethod
    def _without_result(
        pregunta: str,
        status: CacheLookupStatusV1,
        threshold: float,
        *,
        distance: float | None = None,
    ) -> OracleCacheLookupResultV1:
        return OracleCacheLookupResultV1(
            1,
            None,
            cache_receipt_for_lookup(
                pregunta,
                status=status,
                threshold=threshold,
                distance=distance,
            ),
        )

    @staticmethod
    def _validar_umbral(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("umbral_similitud must be finite and non-negative")
        threshold = float(value)
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("umbral_similitud must be finite and non-negative")
        return threshold

    @staticmethod
    def _parse_utc(value: object) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("entry_created_at_utc is absent")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("entry_created_at_utc must be timezone-aware")
        return parsed.astimezone(timezone.utc)

    def close(self) -> None:
        self.client.close()


__all__ = ["OracleCacheLookupResultV1", "OracleMemory"]
