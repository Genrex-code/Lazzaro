"""Pruebas permanentes ROOT-014 para salud y aislamiento de retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from memory.chroma_client import ChromaClient
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_decay import MemoryDecay, MemoryTemporalError
from memory.memory_encoder import MemoryEncoder
from memory.memory_manager import MemoryContextResult, MemoryManager
from memory.memory_retriever import MemoryRetriever
from memory.memory_schema import (
    EPISODIC_KIND,
    MEMORY_SCHEMA_VERSION_KEY,
    SEMANTIC_KIND,
    MemorySchemaCodec,
    MemorySchemaDecodeError,
)
from memory.memory_summarizer import MemorySummarizer
from memory.memory_types import EpisodicMemory, MemoryMetadata, MemoryResult, SemanticMemory
from memory.retrieval_types import (
    MemoryRetrievalReceipt,
    RetrievalHealth,
    RetrievalIssueCode,
    StoreRetrievalReceipt,
    merge_reason_codes,
)
from memory.semantic_memory import SemanticMemoryStore


NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


class RawClient:
    def __init__(self, result: Any = None, error: BaseException | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    @staticmethod
    def combinar_where(first: dict | None, second: dict) -> dict:
        return second if not first else {"$and": [first, second]}

    def buscar(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class ReceiptSource:
    def __init__(self, receipt: StoreRetrievalReceipt):
        self.receipt = receipt
        self.calls = 0

    def buscar_con_recibo(self, *_args: Any, **_kwargs: Any) -> StoreRetrievalReceipt:
        self.calls += 1
        return self.receipt


class LegacySource:
    def __init__(self, results: list[MemoryResult]):
        self.results = results
        self.calls = 0

    def buscar(self, *_args: Any, **_kwargs: Any) -> list[MemoryResult]:
        self.calls += 1
        return list(self.results)


class ReceiptRetriever:
    def __init__(self, receipt: MemoryRetrievalReceipt):
        self.receipt = receipt
        self.calls = 0

    def buscar_con_recibo(self, *_args: Any, **_kwargs: Any) -> MemoryRetrievalReceipt:
        self.calls += 1
        return self.receipt

    def buscar(self, *_args: Any, **_kwargs: Any) -> list[MemoryResult]:
        raise AssertionError("El path productivo no debe usar el adapter legacy")


def _metadata(timestamp: datetime, *, extra: dict[str, Any] | None = None) -> MemoryMetadata:
    return MemoryMetadata(
        tipo_evento="root014",
        valencia=0.0,
        impacto_emocional=0.5,
        timestamp=timestamp,
        extra=extra or {},
    )


def _result(
    memory_id: str,
    *,
    semantic: bool = False,
    timestamp: datetime = NOW,
    distance: float = 0.2,
    impact: float = 0.5,
) -> MemoryResult:
    metadata = MemoryMetadata(
        tipo_evento="root014",
        valencia=0.0,
        impacto_emocional=impact,
        timestamp=timestamp,
    )
    memory = (
        SemanticMemory(memory_id, metadata, f"rule {memory_id}", "probe")
        if semantic
        else EpisodicMemory(memory_id, metadata, f"episode {memory_id}")
    )
    return MemoryResult(memory, distance)


def _v1_metadata(kind: str, timestamp: datetime = NOW) -> dict[str, Any]:
    if kind == EPISODIC_KIND:
        memory = EpisodicMemory("raw", _metadata(timestamp), "document")
    else:
        memory = SemanticMemory("raw", _metadata(timestamp), "document", "probe")
    return MemoryEncoder.a_documento(memory).metadata


def _raw_rows(
    ids: list[Any],
    documents: list[Any],
    metadatas: list[Any],
    distances: list[Any],
) -> dict[str, list[list[Any]]]:
    return {
        "ids": [ids],
        "documents": [documents],
        "metadatas": [metadatas],
        "distances": [distances],
    }


def _store_receipt(
    health: RetrievalHealth,
    results: tuple[MemoryResult, ...] = (),
    *,
    dropped: int = 0,
    reasons: tuple[RetrievalIssueCode, ...] = (),
) -> StoreRetrievalReceipt:
    return StoreRetrievalReceipt(
        health=health,
        results=results,
        candidate_count=len(results) + dropped,
        dropped_count=dropped,
        reason_codes=reasons,
    )


def _hybrid(
    episodic: StoreRetrievalReceipt,
    semantic: StoreRetrievalReceipt,
    *,
    limit: int = 5,
    clock: Any = None,
) -> tuple[MemoryRetrievalReceipt, ReceiptSource, ReceiptSource]:
    episodic_source = ReceiptSource(episodic)
    semantic_source = ReceiptSource(semantic)
    retriever = MemoryRetriever(
        episodic_source,  # type: ignore[arg-type]
        semantic_source,  # type: ignore[arg-type]
        clock=clock or (lambda: NOW),
    )
    return (
        retriever.buscar_con_recibo("synthetic", limite=limit),
        episodic_source,
        semantic_source,
    )


def _memory_receipt(
    health: RetrievalHealth,
    results: tuple[MemoryResult, ...] = (),
    *,
    episodic: StoreRetrievalReceipt | None = None,
    semantic: StoreRetrievalReceipt | None = None,
) -> MemoryRetrievalReceipt:
    episodic = episodic or _store_receipt(RetrievalHealth.OK_EMPTY)
    semantic = semantic or _store_receipt(RetrievalHealth.OK_EMPTY)
    return MemoryRetrievalReceipt(health, results, episodic, semantic)


def _manager_context(
    receipt: MemoryRetrievalReceipt,
    summarizer: object | None = None,
) -> tuple[MemoryContextResult, ReceiptRetriever]:
    manager = object.__new__(MemoryManager)
    retriever = ReceiptRetriever(receipt)
    manager.retriever = retriever
    manager.summarizer = summarizer or MemorySummarizer()
    return manager.obtener_contexto_memoria_con_metadata("synthetic"), retriever


def test_01_healthy_empty_store() -> None:
    receipt = EpisodicMemoryStore(RawClient(_raw_rows([], [], [], []))).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.OK_EMPTY and receipt.results == ()


def test_02_backend_failed_store() -> None:
    receipt = EpisodicMemoryStore(RawClient(None)).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.FAILED
    assert receipt.reason_codes == (RetrievalIssueCode.BACKEND_FAILURE,)


def test_03_healthy_one_hit() -> None:
    raw = _raw_rows(["one"], ["doc"], [_v1_metadata(EPISODIC_KIND)], [0.1])
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.OK
    assert [item.recuerdo.id for item in receipt.results] == ["one"]


def test_04_healthy_multiple_hits() -> None:
    raw = _raw_rows(
        ["one", "two"],
        ["doc1", "doc2"],
        [_v1_metadata(SEMANTIC_KIND), _v1_metadata(SEMANTIC_KIND)],
        [0.1, 0.2],
    )
    receipt = SemanticMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.OK and receipt.valid_count == 2


def test_05_hybrid_empty_empty_is_ok_empty() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK_EMPTY),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.OK_EMPTY and not receipt.results


def test_06_hybrid_hit_empty_is_ok() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("e"),)),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.OK and receipt.returned_count == 1


def test_07_hybrid_empty_hit_is_ok() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK_EMPTY),
        _store_receipt(RetrievalHealth.OK, (_result("s", semantic=True),)),
    )
    assert receipt.health == RetrievalHealth.OK and receipt.returned_count == 1


def test_08_hybrid_both_hit_is_ok() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("e"),)),
        _store_receipt(RetrievalHealth.OK, (_result("s", semantic=True),)),
    )
    assert receipt.health == RetrievalHealth.OK and receipt.returned_count == 2


def test_09_failed_empty_is_partial() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.FAILED, reasons=(RetrievalIssueCode.BACKEND_FAILURE,)),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.PARTIAL and not receipt.results


def test_10_empty_failed_is_partial() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK_EMPTY),
        _store_receipt(RetrievalHealth.FAILED, reasons=(RetrievalIssueCode.BACKEND_FAILURE,)),
    )
    assert receipt.health == RetrievalHealth.PARTIAL and not receipt.results


def test_11_failed_hit_is_partial_with_results() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.FAILED, reasons=(RetrievalIssueCode.BACKEND_FAILURE,)),
        _store_receipt(RetrievalHealth.OK, (_result("s", semantic=True),)),
    )
    assert receipt.health == RetrievalHealth.PARTIAL
    assert [item.recuerdo.id for item in receipt.results] == ["s"]


def test_12_hit_failed_is_partial_with_results() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("e"),)),
        _store_receipt(RetrievalHealth.FAILED, reasons=(RetrievalIssueCode.BACKEND_FAILURE,)),
    )
    assert receipt.health == RetrievalHealth.PARTIAL
    assert [item.recuerdo.id for item in receipt.results] == ["e"]


def test_13_both_failed_is_failed() -> None:
    failed = _store_receipt(
        RetrievalHealth.FAILED,
        reasons=(RetrievalIssueCode.BACKEND_FAILURE,),
    )
    receipt, _, _ = _hybrid(failed, failed)
    assert receipt.health == RetrievalHealth.FAILED and not receipt.results


def test_14_malformed_v1_among_valid_is_isolated() -> None:
    invalid = _v1_metadata(EPISODIC_KIND)
    invalid["timestamp"] = "not-iso"
    raw = _raw_rows(
        ["good", "bad"],
        ["good doc", "bad doc"],
        [_v1_metadata(EPISODIC_KIND), invalid],
        [0.1, 0.2],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert [item.recuerdo.id for item in receipt.results] == ["good"]
    assert receipt.health == RetrievalHealth.PARTIAL
    assert RetrievalIssueCode.INVALID_TIMESTAMP in receipt.reason_codes


def test_15_unknown_schema_among_valid_is_isolated() -> None:
    unknown = _v1_metadata(EPISODIC_KIND)
    unknown[MEMORY_SCHEMA_VERSION_KEY] = 999
    raw = _raw_rows(
        ["good", "unknown"],
        ["good doc", "unknown doc"],
        [_v1_metadata(EPISODIC_KIND), unknown],
        [0.1, 0.2],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert [item.recuerdo.id for item in receipt.results] == ["good"]
    assert receipt.reason_codes == (RetrievalIssueCode.UNKNOWN_SCHEMA,)


def test_16_metadata_none_among_valid_is_isolated() -> None:
    raw = _raw_rows(
        ["good", "none"],
        ["good doc", "none doc"],
        [_v1_metadata(EPISODIC_KIND), None],
        [0.1, 0.2],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.valid_count == 1 and receipt.dropped_count == 1
    assert receipt.reason_codes == (RetrievalIssueCode.MISSING_METADATA,)


def test_17_missing_document_row_is_isolated() -> None:
    raw = _raw_rows(
        ["good", "bad"],
        ["good doc", None],
        [_v1_metadata(EPISODIC_KIND), _v1_metadata(EPISODIC_KIND)],
        [0.1, 0.2],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert [item.recuerdo.id for item in receipt.results] == ["good"]
    assert receipt.reason_codes == (RetrievalIssueCode.INVALID_DOCUMENT,)


def test_18_missing_distance_row_is_isolated() -> None:
    raw = _raw_rows(
        ["good", "bad"],
        ["good doc", "bad doc"],
        [_v1_metadata(EPISODIC_KIND), _v1_metadata(EPISODIC_KIND)],
        [0.1, None],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert [item.recuerdo.id for item in receipt.results] == ["good"]
    assert receipt.reason_codes == (RetrievalIssueCode.INVALID_DISTANCE,)


def test_19_misaligned_arrays_do_not_cross_contaminate() -> None:
    raw = _raw_rows(
        ["one", "two"],
        ["only one document"],
        [_v1_metadata(EPISODIC_KIND), _v1_metadata(EPISODIC_KIND)],
        [0.1, 0.2],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.PARTIAL and not receipt.results
    assert receipt.candidate_count == receipt.dropped_count == 2
    assert receipt.reason_codes == (RetrievalIssueCode.MALFORMED_BATCH,)


def test_20_healthy_v0_row_reads() -> None:
    raw = _raw_rows(
        ["legacy"],
        ["legacy doc"],
        [{"timestamp": "2026-09-08T11:00:00", "tipo_evento": "legacy"}],
        [0.1],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.OK
    assert receipt.results[0].recuerdo.metadata.tipo_evento == "legacy"


def test_21_invalid_v0_timestamp_isolated() -> None:
    raw = _raw_rows(
        ["legacy"],
        ["legacy doc"],
        [{"timestamp": "invalid"}],
        [0.1],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.health == RetrievalHealth.PARTIAL and not receipt.results
    assert receipt.reason_codes == (RetrievalIssueCode.INVALID_TIMESTAMP,)


def test_22_naive_timestamp_scores() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.OK,
            (_result("naive", timestamp=NOW.replace(tzinfo=None) - timedelta(hours=1)),),
        ),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.OK and receipt.returned_count == 1


def test_23_aware_utc_timestamp_scores() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("utc", timestamp=NOW),)),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.OK and receipt.returned_count == 1


def test_24_aware_non_utc_timestamp_scores() -> None:
    offset = timezone(timedelta(hours=-5))
    same_instant = datetime(2026, 9, 8, 6, 0, 0, tzinfo=offset)
    weight = MemoryDecay.calcular_peso_recencia(same_instant, now_utc=NOW)
    naive_weight = MemoryDecay.calcular_peso_recencia(
        datetime(2026, 9, 8, 11, 0, 0),
        now_utc=NOW,
    )
    assert weight == naive_weight


def test_25_mixed_naive_aware_batch_scores() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.OK,
            (
                _result("naive", timestamp=datetime(2026, 9, 8, 11, 0, 0)),
                _result("aware", timestamp=datetime(2026, 9, 8, 11, 0, 0, tzinfo=timezone.utc)),
            ),
        ),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.OK and receipt.returned_count == 2


def test_26_mixed_batch_preserves_all_valid_records() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.OK,
            (
                _result("one", timestamp=NOW - timedelta(hours=1)),
                _result("two", timestamp=(NOW - timedelta(hours=2)).replace(tzinfo=None)),
            ),
        ),
        _store_receipt(RetrievalHealth.OK, (_result("three", semantic=True),)),
    )
    assert {item.recuerdo.id for item in receipt.results} == {"one", "two", "three"}


def test_27_future_timestamp_is_partial() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.OK,
            (_result("future", timestamp=NOW + timedelta(seconds=1)),),
        ),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.PARTIAL and not receipt.results
    assert RetrievalIssueCode.FUTURE_TIMESTAMP in receipt.reason_codes


def test_28_future_does_not_drop_valid_current() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.OK,
            (
                _result("current", timestamp=NOW),
                _result("future", timestamp=NOW + timedelta(hours=1)),
            ),
        ),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert [item.recuerdo.id for item in receipt.results] == ["current"]
    assert receipt.health == RetrievalHealth.PARTIAL


def test_29_malformed_timestamp_does_not_drop_valid() -> None:
    bad = _v1_metadata(EPISODIC_KIND)
    bad["timestamp"] = "bad"
    raw = _raw_rows(
        ["valid", "bad"],
        ["valid doc", "bad doc"],
        [_v1_metadata(EPISODIC_KIND), bad],
        [0.1, 0.2],
    )
    store_receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    receipt, _, _ = _hybrid(store_receipt, _store_receipt(RetrievalHealth.OK_EMPTY))
    assert [item.recuerdo.id for item in receipt.results] == ["valid"]
    assert receipt.health == RetrievalHealth.PARTIAL


def test_30_one_retrieval_uses_one_now() -> None:
    calls = {"clock": 0}

    def clock() -> datetime:
        calls["clock"] += 1
        return NOW

    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("e"),)),
        _store_receipt(RetrievalHealth.OK, (_result("s", semantic=True),)),
        clock=clock,
    )
    assert receipt.returned_count == 2 and calls["clock"] == 1


def test_31_healthy_ranking_and_unbounded_score_preserved() -> None:
    best = _result("best", distance=-1.0, impact=4.0)
    other = _result("other", distance=0.8)
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (other, best)),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert [item.recuerdo.id for item in receipt.results] == ["best", "other"]
    assert receipt.results[0].metadata_score > 1.0


def test_32_ids_scores_alignment_preserved() -> None:
    items = (_result("one", distance=0.1), _result("two", distance=0.7))
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, items),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    context, _ = _manager_context(receipt)
    pairs = tuple((item.recuerdo.id, item.metadata_score) for item in receipt.results)
    assert tuple(zip(context.retrieved_ids, context.scores)) == pairs


def test_33_partial_health_preserves_valid_ranking() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.PARTIAL,
            (_result("lower", distance=0.8), _result("higher", distance=0.1)),
            dropped=1,
            reasons=(RetrievalIssueCode.INVALID_SCHEMA,),
        ),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert receipt.health == RetrievalHealth.PARTIAL
    assert [item.recuerdo.id for item in receipt.results] == ["higher", "lower"]


def test_34_health_performs_no_second_query() -> None:
    _, episodic, semantic = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("e"),)),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    assert episodic.calls == semantic.calls == 1


def test_35_obs005_context_performs_one_hybrid_retrieval() -> None:
    receipt, episodic, semantic = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("e"),)),
        _store_receipt(RetrievalHealth.OK, (_result("s", semantic=True),)),
    )
    context, retriever = _manager_context(receipt)
    assert retriever.calls == 1 and episodic.calls == semantic.calls == 1
    assert len(context.retrieved_ids) == len(context.scores) == 2


def test_36_same_receipt_drives_text_ids_scores_health() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("only"),)),
        _store_receipt(RetrievalHealth.OK_EMPTY),
    )
    context, retriever = _manager_context(receipt)
    assert retriever.calls == 1
    assert context.texto and context.retrieved_ids == ("only",)
    assert context.scores == (receipt.results[0].metadata_score,)
    assert context.health == receipt.health


def test_37_raw_exception_not_in_receipt_or_safe_log() -> None:
    secret = "private-provider-body"
    client = RawClient(error=RuntimeError(secret))
    logger = logging.getLogger("memory.episodic_memory")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger.addHandler(handler)
    try:
        receipt = EpisodicMemoryStore(client).buscar_con_recibo("private query")
    finally:
        logger.removeHandler(handler)
    rendered = " ".join(record.getMessage() for record in records)
    assert secret not in repr(receipt) and secret not in rendered
    assert "private query" not in rendered


def test_38_receipt_has_no_raw_payload_fields() -> None:
    store_fields = set(StoreRetrievalReceipt.__dataclass_fields__)
    hybrid_fields = set(MemoryRetrievalReceipt.__dataclass_fields__)
    forbidden = {"raw_exception", "raw_metadata", "query", "documents", "payload"}
    assert not store_fields.intersection(forbidden)
    assert not hybrid_fields.intersection(forbidden)


def test_39_root013_strict_v1_decode_intact() -> None:
    metadata = _v1_metadata(EPISODIC_KIND)
    metadata[MEMORY_SCHEMA_VERSION_KEY] = 999
    try:
        MemorySchemaCodec.decode_metadata(metadata, EPISODIC_KIND)
    except MemorySchemaDecodeError as exc:
        assert exc.reason_code == "unknown_schema_version"
    else:
        raise AssertionError("La version desconocida no fue rechazada")


def test_40_root039_none_empty_read_semantics_intact() -> None:
    normalized = ChromaClient._normalizar_resultado_query(
        {"ids": [["none", "empty"]], "metadatas": [[None, {}]]}  # type: ignore[arg-type]
    )
    assert normalized["metadatas"] == [[None, None]]


def test_41_reason_codes_are_deduped_in_first_occurrence_order() -> None:
    merged = merge_reason_codes(
        (RetrievalIssueCode.INVALID_SCHEMA, RetrievalIssueCode.INVALID_TIMESTAMP),
        (RetrievalIssueCode.INVALID_SCHEMA, RetrievalIssueCode.BACKEND_FAILURE),
    )
    assert merged == (
        RetrievalIssueCode.INVALID_SCHEMA,
        RetrievalIssueCode.INVALID_TIMESTAMP,
        RetrievalIssueCode.BACKEND_FAILURE,
    )


def test_42_aligned_store_count_invariant() -> None:
    raw = _raw_rows(
        ["good", "bad"],
        ["good", None],
        [_v1_metadata(EPISODIC_KIND), _v1_metadata(EPISODIC_KIND)],
        [0.1, 0.2],
    )
    receipt = EpisodicMemoryStore(RawClient(raw)).buscar_con_recibo("q")
    assert receipt.candidate_count == receipt.valid_count + receipt.dropped_count


def test_43_hybrid_dedupe_does_not_increment_dropped() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(RetrievalHealth.OK, (_result("same"),)),
        _store_receipt(RetrievalHealth.OK, (_result("same", semantic=True),)),
    )
    assert receipt.source_valid_count == 2 and receipt.returned_count == 1
    assert receipt.dropped_count == 0


def test_44_final_limit_does_not_increment_dropped() -> None:
    receipt, _, _ = _hybrid(
        _store_receipt(
            RetrievalHealth.OK,
            (_result("one"), _result("two"), _result("three")),
        ),
        _store_receipt(RetrievalHealth.OK_EMPTY),
        limit=1,
    )
    assert receipt.source_valid_count == 3 and receipt.returned_count == 1
    assert receipt.dropped_count == 0


def test_45_unexpected_store_failure_is_not_invalid_schema() -> None:
    class Exploding:
        def buscar_con_recibo(self, *_args: Any, **_kwargs: Any) -> StoreRetrievalReceipt:
            raise RuntimeError("implementation bug")

    retriever = MemoryRetriever(
        Exploding(),  # type: ignore[arg-type]
        ReceiptSource(_store_receipt(RetrievalHealth.OK_EMPTY)),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    receipt = retriever.buscar_con_recibo("q")
    assert RetrievalIssueCode.INVALID_SCHEMA not in receipt.reason_codes
    assert RetrievalIssueCode.INTERNAL_FAILURE in receipt.reason_codes


def test_46_main_context_path_uses_receipt() -> None:
    receipt = _memory_receipt(
        RetrievalHealth.OK,
        (_result("main"),),
        episodic=_store_receipt(RetrievalHealth.OK, (_result("main"),)),
    )
    context, retriever = _manager_context(receipt)
    assert retriever.calls == 1 and context.health == RetrievalHealth.OK


def test_47_relational_productive_path_uses_receipt() -> None:
    class SemanticReceiptOnly:
        def __init__(self) -> None:
            self.calls = 0

        def buscar_con_recibo(self, *_args: Any, **_kwargs: Any) -> StoreRetrievalReceipt:
            self.calls += 1
            return _store_receipt(
                RetrievalHealth.OK,
                (
                    _result(
                        "rel",
                        semantic=True,
                        timestamp=datetime.now(timezone.utc) - timedelta(minutes=1),
                    ),
                    _result(
                        "rel-future",
                        semantic=True,
                        timestamp=datetime.now(timezone.utc) + timedelta(days=1),
                    ),
                ),
            )

        def buscar(self, *_args: Any, **_kwargs: Any) -> list[MemoryResult]:
            raise AssertionError("No debe usar adapter legacy")

    manager = object.__new__(MemoryManager)
    source = SemanticReceiptOnly()
    manager.semantic_store = source
    text = manager.obtener_contexto_relacional("q")
    assert source.calls == 1 and text.startswith("MEMORIA RELACIONAL PERSISTENTE")
    assert "rel-future" not in text


def test_48_legacy_adapters_do_not_add_queries() -> None:
    raw_client = RawClient(_raw_rows([], [], [], []))
    assert EpisodicMemoryStore(raw_client).buscar("q") == []
    assert raw_client.calls == 1

    episodic = LegacySource([_result("e")])
    semantic = LegacySource([])
    retriever = MemoryRetriever(episodic, semantic, clock=lambda: NOW)  # type: ignore[arg-type]
    assert len(retriever.buscar("q")) == 1
    assert episodic.calls == semantic.calls == 1


def test_49_memory_context_default_health_is_failed() -> None:
    context = MemoryContextResult()
    assert context.health == RetrievalHealth.FAILED
    assert context.episodic_health == context.semantic_health == RetrievalHealth.FAILED


def test_50_partial_zero_hits_has_no_empty_sentinel() -> None:
    receipt = _memory_receipt(
        RetrievalHealth.PARTIAL,
        episodic=_store_receipt(
            RetrievalHealth.PARTIAL,
            dropped=1,
            reasons=(RetrievalIssueCode.INVALID_SCHEMA,),
        ),
    )
    context, _ = _manager_context(receipt)
    assert context.texto == "" and context.health == RetrievalHealth.PARTIAL


def test_51_failed_has_no_empty_sentinel() -> None:
    failed = _store_receipt(
        RetrievalHealth.FAILED,
        reasons=(RetrievalIssueCode.BACKEND_FAILURE,),
    )
    context, _ = _manager_context(_memory_receipt(RetrievalHealth.FAILED, episodic=failed, semantic=failed))
    assert context.texto == "" and context.health == RetrievalHealth.FAILED


def test_52_ok_empty_keeps_current_sentinel() -> None:
    context, _ = _manager_context(_memory_receipt(RetrievalHealth.OK_EMPTY))
    assert context.texto == "No hay recuerdos relevantes para esta situación."


def test_53_summarizer_failure_does_not_rewrite_health() -> None:
    class ExplodingSummarizer:
        @staticmethod
        def resumir(*_args: Any, **_kwargs: Any) -> str:
            raise RuntimeError("private summary body")

    item = _result("healthy")
    receipt = _memory_receipt(
        RetrievalHealth.OK,
        (item,),
        episodic=_store_receipt(RetrievalHealth.OK, (item,)),
    )
    context, _ = _manager_context(receipt, ExplodingSummarizer())
    assert context.texto == "" and context.health == RetrievalHealth.OK


def test_54_v0_invalid_timestamp_never_becomes_now() -> None:
    for metadata in ({}, {"timestamp": "invalid"}):
        try:
            MemorySchemaCodec.decode_metadata(metadata, EPISODIC_KIND)
        except MemorySchemaDecodeError as exc:
            assert exc.reason_code == "invalid_timestamp"
        else:
            raise AssertionError("V0 invalido fue aceptado")


def test_55_future_never_receives_trusted_max_recency() -> None:
    try:
        MemoryDecay.calcular_peso_recencia(
            NOW + timedelta(seconds=1),
            now_utc=NOW,
        )
    except MemoryTemporalError as exc:
        assert exc.reason_code == RetrievalIssueCode.FUTURE_TIMESTAMP
    else:
        raise AssertionError("El timestamp futuro recibio recencia silenciosa")


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-014 retrieval health: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
