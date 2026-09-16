"""ROOT-018: provenance, cache identity and typed Oracle bridge matrix."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from ai_engine.knowledge_provenance import (
    CacheEntryIdentityV1,
    CacheLookupStatusV1,
    CacheReceiptV1,
    CacheStoreStatusV1,
    KnowledgeProducerKindV1,
    KnowledgeProducerRouteV1,
    KnowledgeProvenanceV1,
    ProducerReceiptV1,
    RetrievalPathV1,
    RetrievalReceiptV1,
    cache_receipt_for_lookup,
    cache_receipt_not_checked,
    canonical_producer_json,
    legacy_unknown_provenance,
    local_deterministic_producer,
    normalize_oracle_query,
    oracle_cache_logical_key,
    oracle_query_sha256,
    provenance_for,
    provider_receipt_from_llm,
)
from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    LLMFailureClassV1,
    ProviderAttemptReceiptV1,
)
from ai_engine.oracle_engine import OracleEngine
from ai_engine.oracle_memory import OracleCacheLookupResultV1, OracleMemory
from ai_engine.schemas import OracleResult
from ai_engine.structured_parse_types import StructuredParseStatus
from core.orchestrator import Orchestrator
from memory.chroma_search_types import ChromaSearchResultV1, ChromaSearchStatusV1


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _attempt(
    provider: str,
    failure: LLMFailureClassV1 | None,
    *,
    model: str | None = None,
    started: int = 1,
) -> ProviderAttemptReceiptV1:
    return ProviderAttemptReceiptV1(
        provider=provider,
        model_id=model or f"{provider}-model",
        failure_class=failure,
        error_type=None if failure is None else "StableFailure",
        http_status=None,
        retry_after_seconds=None,
        started_ns=started,
        finished_ns=started + 1,
    )


def _technical(
    final_provider: str,
    receipts: tuple[ProviderAttemptReceiptV1, ...],
) -> LLMCallResultV1:
    return LLMCallResultV1(
        terminal=LLMCallTerminalV1.SUCCESS,
        final_text='{"contenido_tecnico":"dato"}',
        final_provider=final_provider,
        final_model_id=f"{final_provider}-model",
        final_failure=None,
        receipts=receipts,
    )


def _producer(
    *,
    provider: str = "gemini",
    route: KnowledgeProducerRouteV1 = KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN,
    fallback: bool = False,
    attempts: int = 1,
    failures: tuple[LLMFailureClassV1, ...] = (),
    status: StructuredParseStatus = StructuredParseStatus.VALID,
) -> ProducerReceiptV1:
    return ProducerReceiptV1(
        kind=KnowledgeProducerKindV1.PROVIDER_GENERATED,
        route=route,
        provider=provider,
        model_id=f"{provider}-model",
        final_parse_status=status,
        parse_contract_version=1,
        provider_fallback_used=fallback,
        scheduler_attempt_count=attempts,
        failure_classes=failures,
        accepted_at_utc=NOW,
    )


def _miss(query: str = "Q2") -> CacheReceiptV1:
    return cache_receipt_for_lookup(
        query,
        status=CacheLookupStatusV1.MISS,
        threshold=0.35,
    )


def _generated_result(
    *,
    query: str = "Q2",
    producer: ProducerReceiptV1 | None = None,
) -> OracleResult:
    return OracleResult(
        pregunta_original=query,
        contenido_tecnico="conocimiento",
        resumen_para_persona="resumen",
        provenance=provenance_for(producer or _producer(), _miss(query)),
    )


def _entry(entry_id: str = "entry-q1", query: str = "Q1") -> CacheEntryIdentityV1:
    return CacheEntryIdentityV1(
        entry_id=entry_id,
        logical_key=oracle_cache_logical_key(query),
        stored_query_sha256=oracle_query_sha256(query),
        cache_schema_version=1,
        knowledge_contract_version=1,
        provenance_version=1,
        entry_created_at_utc=NOW,
    )


def _metadata(query: str = "Q1", producer: ProducerReceiptV1 | None = None) -> dict:
    return {
        "contenido_tecnico": "historical-knowledge",
        "resumen_para_persona": "historical-summary",
        "oracle_cache_schema_version": 1,
        "oracle_knowledge_contract_version": 1,
        "knowledge_provenance_version": 1,
        "entry_logical_key": oracle_cache_logical_key(query),
        "stored_query_sha256": oracle_query_sha256(query),
        "entry_created_at_utc": NOW.isoformat(),
        "producer_receipt_json": canonical_producer_json(producer or _producer()),
    }


def _payload(query: str, metadata: object, *, distance: float = 0.1, entry_id: str = "entry-q1"):
    return {
        "ids": [[entry_id]],
        "documents": [[query]],
        "metadatas": [[metadata]],
        "distances": [[distance]],
    }


class _Client:
    def __init__(
        self,
        search: ChromaSearchResultV1,
        *,
        add_result: bool = True,
    ) -> None:
        self.search = search
        self.add_result = add_result
        self.disponible = True
        self.search_calls = 0
        self.writes: list[dict] = []

    def buscar_con_estado(self, **kwargs):
        self.search_calls += 1
        return self.search

    def agregar(self, **kwargs):
        self.writes.append(kwargs)
        return self.add_result


def _memory(search: ChromaSearchResultV1, *, add_result: bool = True) -> OracleMemory:
    memory = object.__new__(OracleMemory)
    memory.db_path = "fixture"
    memory.client = _Client(search, add_result=add_result)
    return memory


def _search(status: ChromaSearchStatusV1, payload=None) -> ChromaSearchResultV1:
    if status in {ChromaSearchStatusV1.SUCCESS, ChromaSearchStatusV1.EMPTY}:
        return ChromaSearchResultV1(1, status, payload, None)
    from memory.chroma_search_types import ChromaSearchFailureCodeV1

    failure = (
        ChromaSearchFailureCodeV1.COLLECTION_UNAVAILABLE
        if status is ChromaSearchStatusV1.UNAVAILABLE
        else ChromaSearchFailureCodeV1.BACKEND_FAILURE
    )
    return ChromaSearchResultV1(1, status, None, failure)


def test_01_oracle_result_requires_provenance() -> None:
    try:
        OracleResult(pregunta_original="q", contenido_tecnico="dato")
    except ValidationError:
        return
    raise AssertionError("OracleResult without provenance was accepted")


def test_02_explicit_legacy_helper_is_uncertain() -> None:
    matched = CacheEntryIdentityV1(
        entry_id="legacy-id",
        logical_key=None,
        stored_query_sha256=None,
        cache_schema_version=None,
        knowledge_contract_version=None,
        provenance_version=None,
        entry_created_at_utc=None,
    )
    receipt = cache_receipt_for_lookup(
        "Q2",
        status=CacheLookupStatusV1.LEGACY_ENTRY,
        threshold=0.35,
        matched_entry=matched,
        distance=0.1,
    )
    result = OracleResult(
        pregunta_original="Q2",
        contenido_tecnico="legacy",
        provenance=legacy_unknown_provenance(
            retrieval=RetrievalReceiptV1(cache=receipt)
        ),
    )
    assert result.provenance.producer.kind is KnowledgeProducerKindV1.LEGACY_UNKNOWN
    assert result.provenance.producer.provider is None
    assert result.provenance.producer.model_id is None
    assert result.provenance.producer.accepted_at_utc is None
    assert result.cache_hit and result.fuente == "cache"


def test_03_provenance_extra_fields_and_bad_versions_are_rejected() -> None:
    provenance = provenance_for(_producer(), _miss())
    assert KnowledgeProvenanceV1.model_validate(
        provenance.model_dump(mode="python")
    ) == provenance
    payload = provenance.model_dump(mode="python")
    payload["surprise"] = "forbidden"
    try:
        KnowledgeProvenanceV1.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown provenance field accepted")
    for invalid in (True, 1.0, "1", 0, 2):
        try:
            KnowledgeProvenanceV1(
                version=invalid,
                producer=_producer(),
                retrieval=RetrievalReceiptV1(cache=_miss()),
            )
        except ValidationError:
            continue
        raise AssertionError("invalid provenance version accepted")


def test_04_query_identity_is_nfc_strip_only_and_deterministic() -> None:
    composed = "  café  "
    decomposed = "cafe\u0301"
    assert normalize_oracle_query(composed) == "café"
    assert oracle_query_sha256(composed) == oracle_query_sha256(decomposed)
    assert oracle_cache_logical_key(composed) == oracle_cache_logical_key(decomposed)
    assert oracle_query_sha256("A  B") != oracle_query_sha256("A B")
    assert oracle_query_sha256("Case") != oracle_query_sha256("case")
    assert oracle_cache_logical_key("x") == oracle_cache_logical_key("x")


def test_05_provider_route_mapping_is_slot_based_and_fail_closed() -> None:
    class Provider:
        def __init__(self, name):
            self.provider_name = name

    external = Provider("external-slot")
    active = Provider("active-slot")
    mapping = Orchestrator._construir_mapa_rutas_oracle(
        oracle_provider=external,
        active_providers=(active,),
    )
    assert mapping == {
        "external-slot": KnowledgeProducerRouteV1.ORACLE_EXTERNAL,
        "active-slot": KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN,
    }
    for oracle, active_slots in (
        (Provider(""), (active,)),
        (Provider("same"), (Provider("same"),)),
    ):
        try:
            Orchestrator._construir_mapa_rutas_oracle(
                oracle_provider=oracle,
                active_providers=active_slots,
            )
        except ValueError:
            continue
        raise AssertionError("ambiguous provider slots accepted")


def test_06_same_provider_retry_does_not_count_as_fallback() -> None:
    result = _technical(
        "gemini",
        (
            _attempt("gemini", LLMFailureClassV1.TRANSPORT_TIMEOUT, started=1),
            _attempt("gemini", None, started=3),
        ),
    )
    producer = provider_receipt_from_llm(
        result,
        route_by_provider={
            "gemini": KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN
        },
        final_parse_status=StructuredParseStatus.VALID,
        accepted_at_utc=NOW,
    )
    assert producer.provider_fallback_used is False
    assert producer.scheduler_attempt_count == len(result.receipts) == 2


def test_07_different_provider_before_success_counts_as_fallback() -> None:
    for first, final in (("oracle", "gemini"), ("gemini", "groq")):
        result = _technical(
            final,
            (
                _attempt(first, LLMFailureClassV1.CONNECTION_FAILURE, started=1),
                _attempt(final, None, started=3),
            ),
        )
        producer = provider_receipt_from_llm(
            result,
            route_by_provider={
                final: KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN
            },
            final_parse_status=StructuredParseStatus.VALID,
            accepted_at_utc=NOW,
        )
        assert producer.provider_fallback_used is True


def test_08_receipt_count_and_failure_classes_follow_root021_receipts() -> None:
    result = _technical(
        "groq",
        (
            _attempt("gemini", LLMFailureClassV1.TRANSPORT_TIMEOUT, started=1),
            _attempt("gemini", LLMFailureClassV1.TRANSPORT_TIMEOUT, started=3),
            _attempt("groq", LLMFailureClassV1.RATE_LIMITED, started=5),
            _attempt("groq", None, started=7),
        ),
    )
    producer = provider_receipt_from_llm(
        result,
        route_by_provider={"groq": KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN},
        final_parse_status=StructuredParseStatus.DEGRADED,
        accepted_at_utc=NOW,
    )
    assert producer.scheduler_attempt_count == 4
    assert producer.failure_classes == (
        LLMFailureClassV1.TRANSPORT_TIMEOUT,
        LLMFailureClassV1.RATE_LIMITED,
    )
    # Projection-unsupported providers create no receipt in ROOT-021, so the
    # one accepted receipt remains one attempt and no fallback history.
    one = provider_receipt_from_llm(
        _technical("gemini", (_attempt("gemini", None),)),
        route_by_provider={
            "gemini": KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN
        },
        final_parse_status=StructuredParseStatus.VALID,
        accepted_at_utc=NOW,
    )
    assert one.scheduler_attempt_count == 1 and not one.provider_fallback_used


def test_09_final_provider_outside_mapping_is_rejected() -> None:
    try:
        provider_receipt_from_llm(
            _technical("unknown", (_attempt("unknown", None),)),
            route_by_provider={},
            final_parse_status=StructuredParseStatus.VALID,
        )
    except ValueError:
        return
    raise AssertionError("unmapped final provider accepted")


def test_10_cache_health_statuses_survive_oracle_memory_boundary() -> None:
    empty_payload = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    cases = (
        (_search(ChromaSearchStatusV1.EMPTY, empty_payload), CacheLookupStatusV1.MISS),
        (_search(ChromaSearchStatusV1.UNAVAILABLE), CacheLookupStatusV1.UNAVAILABLE),
        (_search(ChromaSearchStatusV1.ERROR), CacheLookupStatusV1.ERROR),
    )
    for search, expected in cases:
        bridge = _memory(search).buscar_resultado_cacheado_con_recibo("Q2")
        assert isinstance(bridge, OracleCacheLookupResultV1)
        assert bridge.cached_result is None
        assert bridge.cache_receipt.lookup_status is expected


def test_11_distance_miss_survives_with_q2_identity() -> None:
    search = _search(
        ChromaSearchStatusV1.SUCCESS,
        _payload("Q1", _metadata(), distance=0.8),
    )
    bridge = _memory(search).buscar_resultado_cacheado_con_recibo("Q2")
    assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.MISS
    assert bridge.cache_receipt.distance == 0.8
    assert bridge.cache_receipt.lookup_query_sha256 == oracle_query_sha256("Q2")


def test_12_partial_and_invalid_versions_are_malformed() -> None:
    base = _metadata()
    for keys in (
        ("oracle_cache_schema_version",),
        ("oracle_cache_schema_version", "oracle_knowledge_contract_version"),
    ):
        metadata = {key: base[key] for key in keys}
        metadata["contenido_tecnico"] = "x"
        bridge = _memory(
            _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", metadata))
        ).buscar_resultado_cacheado_con_recibo("Q2")
        assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.MALFORMED_ENTRY
    for invalid in (True, 1.0, "1"):
        metadata = _metadata()
        metadata["oracle_cache_schema_version"] = invalid
        bridge = _memory(
            _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", metadata))
        ).buscar_resultado_cacheado_con_recibo("Q2")
        assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.MALFORMED_ENTRY


def test_13_unsupported_versions_are_incompatible() -> None:
    metadata = _metadata()
    metadata["knowledge_provenance_version"] = 2
    bridge = _memory(
        _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", metadata))
    ).buscar_resultado_cacheado_con_recibo("Q2")
    assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.INCOMPATIBLE_VERSION


def test_14_corrupt_v1_receipt_key_or_content_is_malformed() -> None:
    mutations = (
        ("producer_receipt_json", "not-json"),
        ("entry_logical_key", oracle_cache_logical_key("another")),
        ("stored_query_sha256", oracle_query_sha256("another")),
        ("contenido_tecnico", ""),
    )
    for key, value in mutations:
        metadata = _metadata()
        metadata[key] = value
        bridge = _memory(
            _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", metadata))
        ).buscar_resultado_cacheado_con_recibo("Q2")
        assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.MALFORMED_ENTRY


def test_15_semantic_hit_preserves_q1_q2_and_historical_producer() -> None:
    historical = _producer(
        provider="old-provider",
        route=KnowledgeProducerRouteV1.ORACLE_EXTERNAL,
        fallback=True,
        attempts=3,
        failures=(LLMFailureClassV1.CONNECTION_FAILURE,),
    )
    bridge = _memory(
        _search(
            ChromaSearchStatusV1.SUCCESS,
            _payload("Q1 historical", _metadata("Q1 historical", historical)),
        )
    ).buscar_resultado_cacheado_con_recibo("Q2 current")
    result = bridge.cached_result
    assert result is not None
    assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.HIT
    assert bridge.cache_receipt.lookup_query_sha256 == oracle_query_sha256("Q2 current")
    assert bridge.cache_receipt.lookup_logical_key == oracle_cache_logical_key("Q2 current")
    assert bridge.cache_receipt.matched_entry is not None
    assert bridge.cache_receipt.matched_entry.stored_query_sha256 == oracle_query_sha256("Q1 historical")
    assert bridge.cache_receipt.matched_entry.logical_key == oracle_cache_logical_key("Q1 historical")
    assert bridge.cache_receipt.matched_entry.entry_id == "entry-q1"
    assert result.pregunta_original == "Q2 current"
    assert result.provenance.producer == historical
    assert result.fuente == "cache" and result.cache_hit


def test_16_all_version_fields_absent_can_serve_explicit_legacy() -> None:
    metadata = {
        "contenido_tecnico": "legacy",
        "resumen_para_persona": "legacy-summary",
        "fuente": "api",
    }
    bridge = _memory(
        _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", metadata))
    ).buscar_resultado_cacheado_con_recibo("Q2")
    assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.LEGACY_ENTRY
    assert bridge.cached_result is not None
    producer = bridge.cached_result.provenance.producer
    assert producer.kind is KnowledgeProducerKindV1.LEGACY_UNKNOWN
    assert producer.provider is producer.model_id is producer.accepted_at_utc is None
    assert bridge.cache_receipt.store_status is CacheStoreStatusV1.NOT_ATTEMPTED


def test_17_invalid_legacy_shape_is_malformed() -> None:
    for metadata in (None, {}, {"contenido_tecnico": ""}):
        bridge = _memory(
            _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", metadata))
        ).buscar_resultado_cacheado_con_recibo("Q2")
        assert bridge.cache_receipt.lookup_status is CacheLookupStatusV1.MALFORMED_ENTRY


def test_18_generated_valid_store_success_and_failure_are_observable() -> None:
    parse = OracleEngine().parse_result(
        json.dumps({"contenido_tecnico": "new", "resumen_para_persona": "short"})
    )
    empty = _search(
        ChromaSearchStatusV1.EMPTY,
        {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]},
    )
    for add_result, expected in (
        (True, CacheStoreStatusV1.STORED),
        (False, CacheStoreStatusV1.FAILED),
    ):
        memory = _memory(empty, add_result=add_result)
        result = OracleEngine().proyectar_resultado(
            parse,
            "Q2",
            memory,
            producer=_producer(),
            cache_receipt=_miss("Q2"),
        )
        assert result.contenido_tecnico == "new"
        assert result.provenance.retrieval.cache.store_status is expected
        assert len(memory.client.writes) == 1
        if expected is CacheStoreStatusV1.STORED:
            stored_entry = result.provenance.retrieval.cache.stored_entry
            assert stored_entry is not None
            assert str(uuid.UUID(stored_entry.entry_id)) == stored_entry.entry_id
            assert stored_entry.entry_id != stored_entry.logical_key
            metadata = memory.client.writes[0]["metadatos"][0]
            assert not {"fuente", "timestamp", "respuesta"}.intersection(metadata)


def test_19_degraded_and_local_fallback_are_skipped_policy() -> None:
    memory = _memory(
        _search(
            ChromaSearchStatusV1.EMPTY,
            {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]},
        )
    )
    degraded = OracleEngine().proyectar_resultado(
        OracleEngine().parse_result("plain candidate"),
        "Q",
        memory,
        producer=_producer(status=StructuredParseStatus.DEGRADED),
        cache_receipt=_miss("Q"),
    )
    error = OracleEngine().proyectar_resultado(
        OracleEngine().parse_result(""),
        "Q",
        memory,
        producer=local_deterministic_producer(
            final_parse_status=StructuredParseStatus.ERROR,
            accepted_at_utc=NOW,
        ),
        cache_receipt=_miss("Q"),
    )
    assert degraded.provenance.retrieval.cache.store_status is CacheStoreStatusV1.SKIPPED_POLICY
    assert error.provenance.retrieval.cache.store_status is CacheStoreStatusV1.SKIPPED_POLICY
    assert error.fuente == "error"
    assert memory.client.writes == []
    try:
        OracleEngine().proyectar_resultado(
            OracleEngine().parse_result("plain candidate"),
            "Q",
            memory,
            producer=_producer(status=StructuredParseStatus.VALID),
            cache_receipt=_miss("Q"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("projection accepted contradictory parse provenance")


def test_20_generated_after_error_or_miss_retains_lookup_status() -> None:
    for status in (CacheLookupStatusV1.ERROR, CacheLookupStatusV1.MISS):
        receipt = cache_receipt_for_lookup("Q", status=status, threshold=0.35)
        result = OracleEngine().proyectar_resultado(
            OracleEngine().parse_result(
                json.dumps({"contenido_tecnico": "new", "resumen_para_persona": None})
            ),
            "Q",
            _memory(
                _search(
                    ChromaSearchStatusV1.EMPTY,
                    {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]},
                )
            ),
            producer=_producer(),
            cache_receipt=receipt,
        )
        assert result.provenance.retrieval.cache.lookup_status is status


def test_21_cache_hits_are_never_recache_laundered() -> None:
    bridge = _memory(
        _search(ChromaSearchStatusV1.SUCCESS, _payload("Q1", _metadata()))
    ).buscar_resultado_cacheado_con_recibo("Q2")
    result = bridge.cached_result
    assert result is not None
    memory = _memory(_search(ChromaSearchStatusV1.EMPTY, {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}))
    receipt = memory.guardar_resultado_en_cache(result)
    assert receipt.store_status is CacheStoreStatusV1.NOT_ATTEMPTED
    assert memory.client.writes == []


def test_22_compatibility_fields_are_derived_and_contradictions_fail() -> None:
    external = _generated_result(
        producer=_producer(route=KnowledgeProducerRouteV1.ORACLE_EXTERNAL)
    )
    active = _generated_result()
    local = OracleResult(
        pregunta_original="Q",
        contenido_tecnico="local",
        provenance=provenance_for(
            local_deterministic_producer(accepted_at_utc=NOW),
            _miss("Q"),
        ),
    )
    assert (external.fuente, external.cache_hit) == ("api", False)
    assert (active.fuente, active.cache_hit) == ("fallback", False)
    assert (local.fuente, local.cache_hit) == ("error", False)
    for field, value in (("fuente", "api"), ("cache_hit", True)):
        payload = active.model_dump(mode="python", exclude={"fuente", "cache_hit"})
        payload[field] = value
        try:
            OracleResult.model_validate(payload)
        except ValidationError:
            continue
        raise AssertionError("contradictory compatibility field accepted")


def test_23_cache_receipt_lookup_path_and_store_invariants() -> None:
    generated = RetrievalReceiptV1(cache=_miss())
    assert generated.lookup_path is RetrievalPathV1.GENERATED
    assert RetrievalReceiptV1.model_validate(
        generated.model_dump(mode="python")
    ) == generated
    hit = cache_receipt_for_lookup(
        "Q2",
        status=CacheLookupStatusV1.HIT,
        threshold=0.35,
        matched_entry=_entry(),
        distance=0.1,
    )
    assert RetrievalReceiptV1(cache=hit).lookup_path is RetrievalPathV1.CACHE_HIT
    try:
        OracleCacheLookupResultV1(1, _generated_result(), hit)
    except ValueError:
        pass
    else:
        raise AssertionError("cache bridge accepted two contradictory receipts")
    try:
        CacheReceiptV1(
            lookup_status=CacheLookupStatusV1.HIT,
            store_status=CacheStoreStatusV1.STORED,
            lookup_query_sha256=oracle_query_sha256("Q"),
            lookup_logical_key=oracle_cache_logical_key("Q"),
            matched_entry=_entry(),
            stored_entry=_entry("new", "Q"),
            distance=0.1,
            threshold=0.35,
        )
    except ValidationError:
        return
    raise AssertionError("cache hit laundering was accepted")


def test_24_receipts_do_not_store_sensitive_payloads() -> None:
    producer = _producer()
    names = set(ProducerReceiptV1.model_fields)
    forbidden = {"prompt", "response", "http_body", "exception", "url", "api_key", "credentials"}
    assert not names.intersection(forbidden)
    serialized = canonical_producer_json(producer)
    for canary in ("PRIVATE_PROMPT", "PRIVATE_RESPONSE", "https://secret", "api-key"):
        assert canary not in serialized


def test_25_physical_entry_id_is_not_logical_identity() -> None:
    identity = _entry(entry_id="550e8400-e29b-41d4-a716-446655440000", query="Q1")
    assert identity.entry_id != identity.logical_key
    assert identity.entry_id != identity.stored_query_sha256


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-018 provenance: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
