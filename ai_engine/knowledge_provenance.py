"""Versioned provenance and cache-identity contracts for Oracle knowledge."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    computed_field,
    field_validator,
    model_validator,
)

from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    LLMFailureClassV1,
)
from ai_engine.structured_parse_types import StructuredParseStatus


ORACLE_CACHE_SCHEMA_VERSION = 1
ORACLE_KNOWLEDGE_CONTRACT_VERSION = 1
KNOWLEDGE_PROVENANCE_VERSION = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeProducerKindV1(str, Enum):
    PROVIDER_GENERATED = "PROVIDER_GENERATED"
    LOCAL_DETERMINISTIC = "LOCAL_DETERMINISTIC"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class KnowledgeProducerRouteV1(str, Enum):
    ORACLE_EXTERNAL = "ORACLE_EXTERNAL"
    ACTIVE_PROVIDER_CHAIN = "ACTIVE_PROVIDER_CHAIN"
    LOCAL_DETERMINISTIC = "LOCAL_DETERMINISTIC"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class CacheLookupStatusV1(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    MISS = "MISS"
    HIT = "HIT"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    MALFORMED_ENTRY = "MALFORMED_ENTRY"
    INCOMPATIBLE_VERSION = "INCOMPATIBLE_VERSION"
    LEGACY_ENTRY = "LEGACY_ENTRY"


class CacheStoreStatusV1(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    STORED = "STORED"
    FAILED = "FAILED"
    SKIPPED_POLICY = "SKIPPED_POLICY"


class RetrievalPathV1(str, Enum):
    GENERATED = "GENERATED"
    CACHE_HIT = "CACHE_HIT"


def _exact_version(value: object, *, name: str, expected: int = 1) -> int:
    if type(value) is not int or value != expected:
        raise ValueError(f"{name} must be the exact integer {expected}")
    return value


def _utc_or_none(value: datetime | None, *, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    try:
        offset = value.utcoffset()
    except Exception as error:
        raise ValueError(f"{name} must be timezone-aware") from error
    if offset is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite_nonnegative(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return normalized


class ProducerReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provenance_version: StrictInt = KNOWLEDGE_PROVENANCE_VERSION
    knowledge_contract_version: StrictInt = ORACLE_KNOWLEDGE_CONTRACT_VERSION
    kind: KnowledgeProducerKindV1
    route: KnowledgeProducerRouteV1
    provider: StrictStr | None
    model_id: StrictStr | None
    final_parse_status: StructuredParseStatus | None
    parse_contract_version: StrictInt | None
    provider_fallback_used: StrictBool
    scheduler_attempt_count: StrictInt
    failure_classes: tuple[LLMFailureClassV1, ...]
    generation_terminal: LLMCallTerminalV1 | None
    final_failure: LLMFailureClassV1 | None
    accepted_at_utc: datetime | None

    @field_validator("provenance_version", mode="before")
    @classmethod
    def _validate_provenance_version(cls, value: object) -> int:
        return _exact_version(value, name="provenance_version")

    @field_validator("knowledge_contract_version", mode="before")
    @classmethod
    def _validate_knowledge_version(cls, value: object) -> int:
        return _exact_version(value, name="knowledge_contract_version")

    @field_validator("parse_contract_version", mode="before")
    @classmethod
    def _validate_parse_version(cls, value: object) -> object:
        if value is None:
            return None
        return _exact_version(value, name="parse_contract_version")

    @field_validator("scheduler_attempt_count", mode="before")
    @classmethod
    def _validate_attempt_count(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("scheduler_attempt_count must be a non-negative integer")
        return value

    @field_validator("provider", "model_id")
    @classmethod
    def _strip_optional_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("failure_classes", mode="before")
    @classmethod
    def _validate_failure_tuple(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        if not isinstance(value, tuple):
            raise ValueError("failure_classes must be a tuple")
        return value

    @field_validator("accepted_at_utc")
    @classmethod
    def _validate_accepted_at(cls, value: datetime | None) -> datetime | None:
        return _utc_or_none(value, name="accepted_at_utc")

    @model_validator(mode="after")
    def _validate_kind(self) -> "ProducerReceiptV1":
        if len(set(self.failure_classes)) != len(self.failure_classes):
            raise ValueError("failure_classes must be unique")

        if self.kind is KnowledgeProducerKindV1.PROVIDER_GENERATED:
            if self.route not in {
                KnowledgeProducerRouteV1.ORACLE_EXTERNAL,
                KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN,
            }:
                raise ValueError("provider-generated knowledge requires a provider route")
            if not self.provider or self.accepted_at_utc is None:
                raise ValueError("provider-generated knowledge requires provider and acceptance time")
            if self.final_parse_status not in {
                StructuredParseStatus.VALID,
                StructuredParseStatus.DEGRADED,
            } or self.parse_contract_version != 1:
                raise ValueError("provider-generated knowledge requires usable typed parse evidence")
            if self.scheduler_attempt_count <= 0:
                raise ValueError("provider-generated knowledge requires scheduler receipts")
            if self.generation_terminal is not LLMCallTerminalV1.SUCCESS:
                raise ValueError("provider-generated knowledge requires SUCCESS terminal")
            if self.final_failure is not None:
                raise ValueError("provider-generated knowledge cannot claim final failure")
            return self

        if self.kind is KnowledgeProducerKindV1.LOCAL_DETERMINISTIC:
            if self.route is not KnowledgeProducerRouteV1.LOCAL_DETERMINISTIC:
                raise ValueError("local deterministic producer requires its matching route")
            if self.provider is not None or self.model_id is not None:
                raise ValueError("local deterministic producer cannot claim provider identity")
            if self.accepted_at_utc is None:
                raise ValueError("local deterministic producer requires acceptance time")
            if (self.final_parse_status is None) != (self.parse_contract_version is None):
                raise ValueError("parse status and version must be present together")
            if self.generation_terminal is None and (
                self.final_failure is not None
                or self.scheduler_attempt_count != 0
                or self.failure_classes
            ):
                raise ValueError(
                    "local producer without generation terminal cannot claim scheduler state"
                )
            return self

        if self.route is not KnowledgeProducerRouteV1.LEGACY_UNKNOWN:
            raise ValueError("legacy producer requires LEGACY_UNKNOWN route")
        if any(
            value is not None
            for value in (
                self.provider,
                self.model_id,
                self.final_parse_status,
                self.parse_contract_version,
                self.generation_terminal,
                self.final_failure,
                self.accepted_at_utc,
            )
        ):
            raise ValueError("legacy producer cannot claim reconstructed provenance")
        if (
            self.provider_fallback_used
            or self.scheduler_attempt_count != 0
            or self.failure_classes
        ):
            raise ValueError("legacy producer cannot claim scheduler history")
        return self


class CacheEntryIdentityV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: StrictStr
    logical_key: StrictStr | None
    stored_query_sha256: StrictStr | None
    cache_schema_version: StrictInt | None
    knowledge_contract_version: StrictInt | None
    provenance_version: StrictInt | None
    entry_created_at_utc: datetime | None

    @field_validator("entry_id")
    @classmethod
    def _entry_id_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entry_id must be non-empty")
        return value.strip()

    @field_validator("stored_query_sha256")
    @classmethod
    def _stored_hash(cls, value: str | None) -> str | None:
        if value is not None and _SHA256_RE.fullmatch(value) is None:
            raise ValueError("stored_query_sha256 must be lowercase SHA-256")
        return value

    @field_validator("logical_key")
    @classmethod
    def _logical_key(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("oracle-cache:")
            or _SHA256_RE.fullmatch(value.removeprefix("oracle-cache:")) is None
        ):
            raise ValueError("logical_key must be an Oracle cache logical key")
        return value

    @field_validator(
        "cache_schema_version",
        "knowledge_contract_version",
        "provenance_version",
        mode="before",
    )
    @classmethod
    def _versions(cls, value: object) -> object:
        if value is None:
            return None
        return _exact_version(value, name="cache entry version")

    @field_validator("entry_created_at_utc")
    @classmethod
    def _created_at(cls, value: datetime | None) -> datetime | None:
        return _utc_or_none(value, name="entry_created_at_utc")

    @model_validator(mode="after")
    def _version_shape(self) -> "CacheEntryIdentityV1":
        version_values = (
            self.cache_schema_version,
            self.knowledge_contract_version,
            self.provenance_version,
        )
        if any(value is None for value in version_values) and any(
            value is not None for value in version_values
        ):
            raise ValueError("cache entry versions must be all present or all absent")
        if all(value is None for value in version_values):
            if any(
                value is not None
                for value in (
                    self.logical_key,
                    self.stored_query_sha256,
                    self.entry_created_at_utc,
                )
            ):
                raise ValueError("legacy identity cannot claim v1 cache identity")
        else:
            if any(
                value is None
                for value in (
                    self.logical_key,
                    self.stored_query_sha256,
                    self.entry_created_at_utc,
                )
            ):
                raise ValueError("v1 cache identity requires key, query hash and timestamp")
        return self


class CacheReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lookup_status: CacheLookupStatusV1
    store_status: CacheStoreStatusV1
    lookup_query_sha256: StrictStr | None
    lookup_logical_key: StrictStr | None
    matched_entry: CacheEntryIdentityV1 | None
    stored_entry: CacheEntryIdentityV1 | None
    distance: float | None
    threshold: float | None

    @field_validator("lookup_query_sha256")
    @classmethod
    def _lookup_hash(cls, value: str | None) -> str | None:
        if value is not None and _SHA256_RE.fullmatch(value) is None:
            raise ValueError("lookup_query_sha256 must be lowercase SHA-256")
        return value

    @field_validator("lookup_logical_key")
    @classmethod
    def _lookup_key(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("oracle-cache:")
            or _SHA256_RE.fullmatch(value.removeprefix("oracle-cache:")) is None
        ):
            raise ValueError("lookup_logical_key must be an Oracle cache logical key")
        return value

    @field_validator("distance", "threshold", mode="before")
    @classmethod
    def _finite_numbers(cls, value: object, info) -> object:
        if value is None:
            return None
        return _finite_nonnegative(value, name=info.field_name)

    @model_validator(mode="after")
    def _validate_receipt(self) -> "CacheReceiptV1":
        checked = self.lookup_status is not CacheLookupStatusV1.NOT_CHECKED
        if checked:
            if (
                self.lookup_query_sha256 is None
                or self.lookup_logical_key is None
                or self.threshold is None
            ):
                raise ValueError("consulted cache requires lookup identity and threshold")
        elif any(
            value is not None
            for value in (
                self.lookup_query_sha256,
                self.lookup_logical_key,
                self.matched_entry,
                self.distance,
                self.threshold,
            )
        ):
            raise ValueError("NOT_CHECKED cannot claim lookup evidence")

        is_hit = self.lookup_status in {
            CacheLookupStatusV1.HIT,
            CacheLookupStatusV1.LEGACY_ENTRY,
        }
        if is_hit:
            if self.matched_entry is None or self.distance is None:
                raise ValueError("cache hit requires matched entry and distance")
            if self.store_status is not CacheStoreStatusV1.NOT_ATTEMPTED:
                raise ValueError("cache hit cannot be stored again")
            is_versioned = self.matched_entry.cache_schema_version is not None
            if self.lookup_status is CacheLookupStatusV1.HIT and not is_versioned:
                raise ValueError("HIT requires a versioned v1 matched identity")
            if (
                self.lookup_status is CacheLookupStatusV1.LEGACY_ENTRY
                and is_versioned
            ):
                raise ValueError("LEGACY_ENTRY requires a legacy matched identity")
        elif self.matched_entry is not None:
            raise ValueError("non-hit cannot claim matched entry")

        if self.store_status is CacheStoreStatusV1.STORED:
            if self.stored_entry is None:
                raise ValueError("STORED requires stored_entry")
        elif self.stored_entry is not None:
            raise ValueError("only STORED can claim stored_entry")
        return self


class RetrievalReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: StrictInt = 1
    cache: CacheReceiptV1

    @model_validator(mode="before")
    @classmethod
    def _computed_path_cannot_contradict_cache(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "lookup_path" not in value:
            return value
        enriched = dict(value)
        cache = CacheReceiptV1.model_validate(enriched.get("cache"))
        expected = (
            RetrievalPathV1.CACHE_HIT
            if cache.lookup_status
            in {CacheLookupStatusV1.HIT, CacheLookupStatusV1.LEGACY_ENTRY}
            else RetrievalPathV1.GENERATED
        )
        try:
            supplied = RetrievalPathV1(enriched["lookup_path"])
        except (TypeError, ValueError) as error:
            raise ValueError("lookup_path contradicts cache receipt") from error
        if supplied is not expected:
            raise ValueError("lookup_path contradicts cache receipt")
        enriched.pop("lookup_path")
        return enriched

    @field_validator("contract_version", mode="before")
    @classmethod
    def _contract_version(cls, value: object) -> int:
        return _exact_version(value, name="retrieval contract_version")

    @computed_field(return_type=RetrievalPathV1)
    @property
    def lookup_path(self) -> RetrievalPathV1:
        if self.cache.lookup_status in {
            CacheLookupStatusV1.HIT,
            CacheLookupStatusV1.LEGACY_ENTRY,
        }:
            return RetrievalPathV1.CACHE_HIT
        return RetrievalPathV1.GENERATED


class KnowledgeProvenanceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: StrictInt = KNOWLEDGE_PROVENANCE_VERSION
    producer: ProducerReceiptV1
    retrieval: RetrievalReceiptV1

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: object) -> int:
        return _exact_version(value, name="knowledge provenance version")

    @model_validator(mode="after")
    def _producer_retrieval_coherence(self) -> "KnowledgeProvenanceV1":
        producer_kind = self.producer.kind
        lookup_status = self.retrieval.cache.lookup_status
        is_legacy_entry = lookup_status is CacheLookupStatusV1.LEGACY_ENTRY
        is_legacy_producer = producer_kind is KnowledgeProducerKindV1.LEGACY_UNKNOWN

        if is_legacy_entry != is_legacy_producer:
            raise ValueError(
                "LEGACY_ENTRY and LEGACY_UNKNOWN must describe the same provenance"
            )
        if (
            producer_kind is KnowledgeProducerKindV1.LOCAL_DETERMINISTIC
            and self.retrieval.lookup_path is not RetrievalPathV1.GENERATED
        ):
            raise ValueError("local deterministic knowledge cannot be a cache hit")
        if (
            lookup_status is CacheLookupStatusV1.HIT
            and producer_kind is not KnowledgeProducerKindV1.PROVIDER_GENERATED
        ):
            raise ValueError("v1 HIT requires provider-generated historical knowledge")
        if (
            producer_kind is KnowledgeProducerKindV1.PROVIDER_GENERATED
            and lookup_status is CacheLookupStatusV1.NOT_CHECKED
        ):
            raise ValueError("provider-generated knowledge requires a cache lookup receipt")
        return self


def normalize_oracle_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("query must be str")
    return unicodedata.normalize("NFC", query).strip()


def oracle_query_sha256(query: str) -> str:
    return hashlib.sha256(normalize_oracle_query(query).encode("utf-8")).hexdigest()


def oracle_cache_logical_key(query: str) -> str:
    material = {
        "namespace": "oracle_cache",
        "cache_schema_version": ORACLE_CACHE_SCHEMA_VERSION,
        "knowledge_contract_version": ORACLE_KNOWLEDGE_CONTRACT_VERSION,
        "knowledge_provenance_version": KNOWLEDGE_PROVENANCE_VERSION,
        "query_sha256": oracle_query_sha256(query),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "oracle-cache:" + hashlib.sha256(encoded).hexdigest()


def cache_receipt_not_checked() -> CacheReceiptV1:
    return CacheReceiptV1(
        lookup_status=CacheLookupStatusV1.NOT_CHECKED,
        store_status=CacheStoreStatusV1.NOT_ATTEMPTED,
        lookup_query_sha256=None,
        lookup_logical_key=None,
        matched_entry=None,
        stored_entry=None,
        distance=None,
        threshold=None,
    )


def cache_receipt_for_lookup(
    query: str,
    *,
    status: CacheLookupStatusV1,
    threshold: float,
    matched_entry: CacheEntryIdentityV1 | None = None,
    distance: float | None = None,
) -> CacheReceiptV1:
    if status is CacheLookupStatusV1.NOT_CHECKED:
        raise ValueError("use cache_receipt_not_checked for NOT_CHECKED")
    return CacheReceiptV1(
        lookup_status=status,
        store_status=CacheStoreStatusV1.NOT_ATTEMPTED,
        lookup_query_sha256=oracle_query_sha256(query),
        lookup_logical_key=oracle_cache_logical_key(query),
        matched_entry=matched_entry,
        stored_entry=None,
        distance=distance,
        threshold=threshold,
    )


def with_cache_store_status(
    receipt: CacheReceiptV1,
    status: CacheStoreStatusV1,
    *,
    stored_entry: CacheEntryIdentityV1 | None = None,
) -> CacheReceiptV1:
    payload = receipt.model_dump(mode="python")
    payload.update({"store_status": status, "stored_entry": stored_entry})
    return CacheReceiptV1.model_validate(
        payload
    )


def retrieval_from_cache(receipt: CacheReceiptV1) -> RetrievalReceiptV1:
    return RetrievalReceiptV1(cache=receipt)


def _failure_classes(result: LLMCallResultV1) -> tuple[LLMFailureClassV1, ...]:
    ordered: list[LLMFailureClassV1] = []
    for receipt in result.receipts:
        failure = receipt.failure_class
        if failure is not None and failure not in ordered:
            ordered.append(failure)
    return tuple(ordered)


def provider_receipt_from_llm(
    result: LLMCallResultV1,
    *,
    route_by_provider: Mapping[str, KnowledgeProducerRouteV1],
    final_parse_status: StructuredParseStatus,
    accepted_at_utc: datetime | None = None,
) -> ProducerReceiptV1:
    final_provider = str(result.final_provider or "").strip()
    if (
        result.terminal is not LLMCallTerminalV1.SUCCESS
        or result.final_failure is not None
        or not final_provider
    ):
        raise ValueError("provider producer requires a successful final provider")
    route = route_by_provider.get(final_provider)
    if route not in {
        KnowledgeProducerRouteV1.ORACLE_EXTERNAL,
        KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN,
    }:
        raise ValueError("final provider is absent from the precomputed route mapping")

    accepted_index = next(
        (
            index
            for index in range(len(result.receipts) - 1, -1, -1)
            if result.receipts[index].failure_class is None
            and str(result.receipts[index].provider).strip() == final_provider
        ),
        None,
    )
    if accepted_index is None:
        raise ValueError("successful result lacks an accepted provider receipt")
    fallback_used = any(
        str(receipt.provider).strip() != final_provider
        for receipt in result.receipts[:accepted_index]
    )
    return ProducerReceiptV1(
        kind=KnowledgeProducerKindV1.PROVIDER_GENERATED,
        route=route,
        provider=final_provider,
        model_id=(str(result.final_model_id).strip() if result.final_model_id else None),
        final_parse_status=final_parse_status,
        parse_contract_version=1,
        provider_fallback_used=fallback_used,
        scheduler_attempt_count=len(result.receipts),
        failure_classes=_failure_classes(result),
        generation_terminal=result.terminal,
        final_failure=result.final_failure,
        accepted_at_utc=accepted_at_utc or datetime.now(timezone.utc),
    )


def local_deterministic_producer(
    *,
    technical_result: LLMCallResultV1 | None = None,
    final_parse_status: StructuredParseStatus | None = None,
    accepted_at_utc: datetime | None = None,
) -> ProducerReceiptV1:
    return ProducerReceiptV1(
        kind=KnowledgeProducerKindV1.LOCAL_DETERMINISTIC,
        route=KnowledgeProducerRouteV1.LOCAL_DETERMINISTIC,
        provider=None,
        model_id=None,
        final_parse_status=final_parse_status,
        parse_contract_version=1 if final_parse_status is not None else None,
        provider_fallback_used=False,
        scheduler_attempt_count=(
            len(technical_result.receipts) if technical_result is not None else 0
        ),
        failure_classes=(
            _failure_classes(technical_result)
            if technical_result is not None
            else ()
        ),
        generation_terminal=(
            technical_result.terminal if technical_result is not None else None
        ),
        final_failure=(
            technical_result.final_failure if technical_result is not None else None
        ),
        accepted_at_utc=accepted_at_utc or datetime.now(timezone.utc),
    )


def legacy_unknown_provenance(
    *,
    retrieval: RetrievalReceiptV1,
) -> KnowledgeProvenanceV1:
    producer = ProducerReceiptV1(
        kind=KnowledgeProducerKindV1.LEGACY_UNKNOWN,
        route=KnowledgeProducerRouteV1.LEGACY_UNKNOWN,
        provider=None,
        model_id=None,
        final_parse_status=None,
        parse_contract_version=None,
        provider_fallback_used=False,
        scheduler_attempt_count=0,
        failure_classes=(),
        generation_terminal=None,
        final_failure=None,
        accepted_at_utc=None,
    )
    return KnowledgeProvenanceV1(
        producer=producer,
        retrieval=retrieval,
    )


def provenance_for(
    producer: ProducerReceiptV1,
    cache_receipt: CacheReceiptV1,
) -> KnowledgeProvenanceV1:
    return KnowledgeProvenanceV1(
        producer=producer,
        retrieval=retrieval_from_cache(cache_receipt),
    )


def canonical_producer_json(producer: ProducerReceiptV1) -> str:
    return json.dumps(
        producer.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "CacheEntryIdentityV1",
    "CacheLookupStatusV1",
    "CacheReceiptV1",
    "CacheStoreStatusV1",
    "KNOWLEDGE_PROVENANCE_VERSION",
    "KnowledgeProducerKindV1",
    "KnowledgeProducerRouteV1",
    "KnowledgeProvenanceV1",
    "ORACLE_CACHE_SCHEMA_VERSION",
    "ORACLE_KNOWLEDGE_CONTRACT_VERSION",
    "ProducerReceiptV1",
    "RetrievalPathV1",
    "RetrievalReceiptV1",
    "cache_receipt_for_lookup",
    "cache_receipt_not_checked",
    "canonical_producer_json",
    "legacy_unknown_provenance",
    "local_deterministic_producer",
    "normalize_oracle_query",
    "oracle_cache_logical_key",
    "oracle_query_sha256",
    "provenance_for",
    "provider_receipt_from_llm",
    "retrieval_from_cache",
    "with_cache_store_status",
]
