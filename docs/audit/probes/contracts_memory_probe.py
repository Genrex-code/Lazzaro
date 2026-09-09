"""Standalone, read-only probe for Block 2 memory contracts.

This probe never opens ``lazzaro_data`` and never performs semantic queries.
The one real Chroma exercise uses a temporary directory and an in-process fake
embedding function so no model can be downloaded.  Socket connection attempts
and any attempt to construct Chroma on the active database are hard failures.

PASS means that the current behavior was reproduced; several PASS results are
evidence of ambiguous or lossy contracts, not an assertion that they are good.
No production object is patched on disk.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import importlib.metadata
import json
import logging
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Importing LAZZARO's logger must not create or append to its runtime log.
logging.getLogger().handlers = [logging.NullHandler()]

import memory.chroma_client as chroma_client_module
from memory.chroma_client import ChromaClient
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_decay import MemoryDecay, MemoryTemporalError
from memory.memory_encoder import MemoryEncoder
from memory.memory_manager import MemoryContextResult, MemoryManager
from memory.memory_retriever import MemoryRetriever
from memory.memory_summarizer import MemorySummarizer
from memory.memory_types import (
    EpisodicMemory,
    MemoryDocument,
    MemoryMetadata,
    MemoryResult,
    SemanticMemory,
)
from memory.semantic_memory import SemanticMemoryStore
from memory.retrieval_types import (
    MemoryRetrievalReceipt,
    RetrievalHealth,
    RetrievalIssueCode,
    StoreRetrievalReceipt,
)


ACTIVE_CHROMA = (ROOT / "lazzaro_data").resolve()


def _deny_network(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("network access is forbidden in contracts_memory_probe")


@contextmanager
def offline_and_temp_chroma_only():
    """Deny sockets and reject construction on the active Chroma path."""

    original_persistent_client = chroma_client_module.chromadb.PersistentClient

    def guarded_persistent_client(*args: Any, **kwargs: Any):
        raw_path = kwargs.get("path", args[0] if args else None)
        if raw_path is None:
            raise AssertionError("Chroma path must be explicit in this probe")
        requested = Path(raw_path).expanduser().resolve()
        if requested == ACTIVE_CHROMA or ACTIVE_CHROMA in requested.parents:
            raise AssertionError("active lazzaro_data access is forbidden")
        return original_persistent_client(*args, **kwargs)

    with (
        patch.object(socket.socket, "connect", _deny_network),
        patch.object(socket.socket, "connect_ex", _deny_network),
        patch.object(socket, "create_connection", _deny_network),
        patch.object(
            chroma_client_module.chromadb,
            "PersistentClient",
            guarded_persistent_client,
        ),
    ):
        yield


class OfflineEmbeddingFunction:
    """Minimal deterministic Chroma embedding function; no files or network."""

    @staticmethod
    def name() -> str:
        return "lazzaro-contract-audit-offline"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), 1.0, 0.0] for text in input]

    def get_config(self) -> dict[str, Any]:
        return {}


class ReplayClient:
    """Exact store-facing fake: captures a write and replays raw Chroma output."""

    def __init__(self) -> None:
        self.write: dict[str, Any] | None = None
        self.query_result: dict[str, Any] | None = None
        self.where_calls: list[tuple[Any, Any]] = []

    def agregar(self, documentos, metadatos, ids) -> bool:
        self.write = {
            "documents": list(documentos),
            "metadatas": list(metadatos),
            "ids": list(ids),
        }
        return True

    def combinar_where(self, base, extra):
        self.where_calls.append((base, extra))
        if base is None:
            return extra
        return {"$and": [base, extra]}

    def buscar(self, _query, _n, _where):
        return self.query_result


class SearchStore:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = list(result or [])
        self.error = error

    def buscar(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return list(self.result)


class StoreOutcome:
    def __init__(self, outcome: bool | Exception) -> None:
        self.outcome = outcome

    def almacenar(self, _memory) -> bool:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class ListingClient:
    def __init__(self, metadatas: list[dict[str, Any]]) -> None:
        self.metadatas = metadatas

    @staticmethod
    def combinar_where(base, extra):
        return {"$and": [base, extra]}

    def listar(self, **_kwargs):
        return {"metadatas": list(self.metadatas)}


def chroma_metadata_matrix() -> dict[str, Any]:
    """Reproduce MEM-AUDIT-001 on real, temporary Chroma without embeddings."""

    with TemporaryDirectory(prefix="lazzaro-contract-memory-") as temporary:
        temporary_path = Path(temporary).resolve()
        assert temporary_path != ACTIVE_CHROMA and ACTIVE_CHROMA not in temporary_path.parents
        with offline_and_temp_chroma_only():
            client = ChromaClient(temporary, collection_name="contract_metadata")
            assert client.disponible
            try:
                # ChromaClient has no embedding injection seam. Replacing the
                # temporary collection's function keeps the adapter call exact.
                client._collection._embedding_function = OfflineEmbeddingFunction()
                outcomes = {
                    "none": client.agregar(["none"], [None], ["none"]),
                    "empty": client.agregar(["empty"], [{}], ["empty"]),
                    "valid": client.agregar(["valid"], [{"kind": "valid"}], ["valid"]),
                }
                rows = client.listar(limite=10)
                assert outcomes == {"none": True, "empty": True, "valid": True}
                assert rows is not None
                assert set(rows["ids"]) == {"none", "empty", "valid"}
                metadata_by_id = dict(zip(rows["ids"], rows["metadatas"]))
                assert metadata_by_id["none"] is None
                assert metadata_by_id["empty"] is None
                assert metadata_by_id["valid"] == {"kind": "valid"}
                assert client.contar() == 3
            finally:
                client.close()

    return {
        "chromadb_version": importlib.metadata.version("chromadb"),
        "adapter_runtime_none": "accepted_and_persisted_as_null",
        "adapter_runtime_empty_dict": "canonicalized_to_null_and_accepted",
        "adapter_runtime_valid_dict": "accepted",
        "historical_empty_metadata_bug_resolved": True,
        "active_chroma_constructed": False,
        "semantic_query_executed": False,
        "network_allowed": False,
    }


def valid_roundtrips() -> dict[str, Any]:
    fixed = datetime(2026, 1, 2, 3, 4, 5, 6789)
    scalar_extra = {"source": "probe", "attempt": 2, "ratio": 0.25, "flag": False}
    lossy_extra = {"nested": {"x": 1}, "items": [1, 2], "null_value": None}
    emotional_state = {"energia": 0.4, "estres": 0.7}
    episodic = EpisodicMemory(
        id="episode-1",
        metadata=MemoryMetadata(
            tipo_evento="probe_event",
            valencia=-0.25,
            impacto_emocional=0.8,
            timestamp=fixed,
            extra={**scalar_extra, **lossy_extra},
        ),
        narrativa="narrativa estable",
        estado_emocional=emotional_state,
    )
    episode_client = ReplayClient()
    episode_store = EpisodicMemoryStore(episode_client)  # type: ignore[arg-type]
    assert episode_store.almacenar(episodic)
    assert episode_client.write is not None
    episode_client.query_result = {
        "ids": [[episodic.id]],
        "documents": [episode_client.write["documents"]],
        "metadatas": [episode_client.write["metadatas"]],
        "distances": [[0.125]],
    }
    recovered_episode = episode_store.buscar("probe", n=1)[0]

    semantic = SemanticMemory(
        id="semantic-1",
        metadata=MemoryMetadata(
            tipo_evento="rule",
            valencia=0.2,
            impacto_emocional=0.6,
            timestamp=fixed,
            extra={**scalar_extra, **lossy_extra},
        ),
        regla="una regla estable",
        origen="manual",
    )
    semantic_client = ReplayClient()
    semantic_store = SemanticMemoryStore(semantic_client)  # type: ignore[arg-type]
    assert semantic_store.almacenar(semantic)
    assert semantic_client.write is not None
    semantic_client.query_result = {
        "ids": [[semantic.id]],
        "documents": [semantic_client.write["documents"]],
        "metadatas": [semantic_client.write["metadatas"]],
        "distances": [[0.375]],
    }
    recovered_semantic = semantic_store.buscar("probe", n=1)[0]

    episode_core_preserved = (
        recovered_episode.recuerdo.id == episodic.id
        and recovered_episode.recuerdo.narrativa == episodic.narrativa
        and recovered_episode.recuerdo.metadata.tipo_evento == episodic.metadata.tipo_evento
        and recovered_episode.recuerdo.metadata.valencia == episodic.metadata.valencia
        and recovered_episode.recuerdo.metadata.impacto_emocional
        == episodic.metadata.impacto_emocional
        and recovered_episode.recuerdo.metadata.timestamp == fixed
    )
    semantic_core_preserved = (
        recovered_semantic.recuerdo.id == semantic.id
        and recovered_semantic.recuerdo.regla == semantic.regla
        and recovered_semantic.recuerdo.origen == semantic.origen
        and recovered_semantic.recuerdo.metadata.timestamp == fixed
    )
    assert episode_core_preserved and semantic_core_preserved
    assert recovered_episode.recuerdo.metadata.extra == {**scalar_extra, **lossy_extra}
    assert recovered_semantic.recuerdo.metadata.extra == {**scalar_extra, **lossy_extra}
    assert recovered_episode.recuerdo.estado_emocional == emotional_state
    assert recovered_episode.distancia == 0.125
    assert recovered_semantic.distancia == 0.375
    assert recovered_episode.metadata_score == recovered_semantic.metadata_score == 1.0

    return {
        "episodic_core_fields_preserved": episode_core_preserved,
        "semantic_core_fields_preserved": semantic_core_preserved,
        "scalar_extra_preserved": True,
        "non_scalar_extra_preserved": sorted(lossy_extra),
        "episodic_emotional_state_preserved": True,
        "raw_distance_preserved": True,
        "store_result_metadata_score_before_rerank": 1.0,
        "ranking_order_is_id_document_metadata_distance_index_order": True,
    }


def optional_defaults_and_mutability() -> dict[str, Any]:
    kwargs = {
        "tipo_evento": "event",
        "valencia": 0.0,
        "impacto_emocional": 0.5,
    }
    first = MemoryMetadata(**kwargs)
    second = MemoryMetadata(**kwargs)
    assert first.extra is not second.extra
    first.extra["mutable"] = True
    assert "mutable" not in second.extra

    top_level_frozen = False
    try:
        first.valencia = 1.0  # type: ignore[misc]
    except FrozenInstanceError:
        top_level_frozen = True

    supplied_extra = {"before": 1}
    supplied_state = {"energia": 0.5}
    shallow = EpisodicMemory(
        id="shallow",
        metadata=MemoryMetadata(**kwargs, extra=supplied_extra),
        narrativa="",
        estado_emocional=supplied_state,
    )
    supplied_extra["after"] = 2
    supplied_state["energia"] = 0.1
    nested_mutation_visible = (
        shallow.metadata.extra["after"] == 2
        and shallow.estado_emocional is not None
        and shallow.estado_emocional["energia"] == 0.1
    )

    none_state = EpisodicMemory("none", MemoryMetadata(**kwargs), "", None)
    empty_state = EpisodicMemory("empty", MemoryMetadata(**kwargs), "", {})
    assert none_state.estado_emocional is None and empty_state.estado_emocional == {}
    none_doc = MemoryEncoder.a_documento(none_state)
    empty_doc = MemoryEncoder.a_documento(empty_state)
    assert none_doc.metadata != empty_doc.metadata
    assert none_doc.metadata["__lazzaro_memory_state_json"] == "null"
    assert empty_doc.metadata["__lazzaro_memory_state_json"] == "{}"

    empty_input: dict[str, Any] = {}
    encoded_empty = MemoryEncoder.codificar_episodio("event", "", extra=empty_input)
    nonempty_input = {"kept_by_reference": True}
    encoded_nonempty = MemoryEncoder.codificar_episodio("event", "", extra=nonempty_input)
    assert encoded_empty.metadata.extra is not empty_input
    assert encoded_nonempty.metadata.extra is nonempty_input

    unknown_field_rejected = False
    try:
        MemoryMetadata(**kwargs, unknown=1)  # type: ignore[call-arg]
    except TypeError:
        unknown_field_rejected = True

    runtime_wrong_type = MemoryMetadata(
        tipo_evento=None,  # type: ignore[arg-type]
        valencia="not-a-float",  # type: ignore[arg-type]
        impacto_emocional=99,
    )
    empty_document_rejected = False
    try:
        MemoryDocument(id="", contenido="", metadata={})
    except ValueError:
        empty_document_rejected = True
    assert runtime_wrong_type.valencia == "not-a-float"
    assert empty_document_rejected

    direct_semantic_default = SemanticMemory(
        "direct-default",
        MemoryMetadata(**kwargs),
        "rule",
    ).origen
    encoder_semantic_default = MemoryEncoder.codificar_semantica("rule").origen
    assert direct_semantic_default == "desconocido"
    assert encoder_semantic_default == "desconocido"

    return {
        "default_extra_isolated": True,
        "dataclass_top_level_frozen": top_level_frozen,
        "nested_dicts_deeply_immutable": not nested_mutation_visible,
        "none_vs_empty_emotional_state_distinct_in_memory": True,
        "none_vs_empty_emotional_state_distinct_after_encoding": True,
        "empty_extra_input_identity_preserved": False,
        "nonempty_extra_input_identity_preserved": True,
        "unknown_dataclass_field_rejected": unknown_field_rejected,
        "declared_types_runtime_validated": False,
        "range_constraints_runtime_validated": False,
        "empty_memory_document_runtime_rejected": empty_document_rejected,
        "semantic_origin_direct_default": direct_semantic_default,
        "semantic_origin_encoder_default": encoder_semantic_default,
        "semantic_origin_defaults_are_equal": True,
    }


def metadata_collisions_and_missing_fields() -> dict[str, Any]:
    fixed = datetime(2026, 2, 3, 4, 5, 6)
    colliding = EpisodicMemory(
        id="collision",
        metadata=MemoryMetadata(
            tipo_evento="original_event",
            valencia=0.4,
            impacto_emocional=0.7,
            timestamp=fixed,
            extra={
                "tipo": "semantico",
                "tipo_evento": "overridden_event",
                "valencia": "invalid-overwrite",
                "timestamp": "invalid-overwrite",
            },
        ),
        narrativa="collision",
    )
    collision_doc = MemoryEncoder.a_documento(colliding)
    assert collision_doc.metadata["tipo"] == "episodico"
    assert collision_doc.metadata["tipo_evento"] == "original_event"
    assert collision_doc.metadata["valencia"] == 0.4
    assert collision_doc.metadata["timestamp"] == fixed.isoformat()
    collision_extra = json.loads(collision_doc.metadata["__lazzaro_memory_extra_json"])
    assert collision_extra == colliding.metadata.extra

    semantic_collision = SemanticMemory(
        id="semantic-collision",
        metadata=MemoryMetadata(
            tipo_evento="rule",
            valencia=0.0,
            impacto_emocional=0.5,
            timestamp=fixed,
            extra={"origen": "extra-overwrite"},
        ),
        regla="collision",
        origen="canonical-origin",
    )
    semantic_collision_doc = MemoryEncoder.a_documento(semantic_collision)
    assert semantic_collision_doc.metadata["origen"] == "canonical-origin"
    semantic_collision_extra = json.loads(
        semantic_collision_doc.metadata["__lazzaro_memory_extra_json"]
    )
    assert semantic_collision_extra["origen"] == "extra-overwrite"

    missing = ReplayClient()
    missing.query_result = {"ids": [["missing"]]}
    missing_receipt = EpisodicMemoryStore(missing).buscar_con_recibo("probe", n=1)  # type: ignore[arg-type]
    assert missing_receipt.health == RetrievalHealth.PARTIAL
    assert missing_receipt.reason_codes == (RetrievalIssueCode.MALFORMED_BATCH,)
    assert missing_receipt.candidate_count == missing_receipt.dropped_count == 1

    unknown = ReplayClient()
    unknown.query_result = {
        "ids": [["unknown"]],
        "documents": [["doc"]],
        "metadatas": [[{
            "tipo": "episodico",
            "tipo_evento": "event",
            "valencia": True,
            "impacto_emocional": False,
            "timestamp": "not-an-iso-date",
            "future_field": "preserved",
        }]],
        "distances": [[0.2]],
    }
    unknown_receipt = EpisodicMemoryStore(unknown).buscar_con_recibo("probe", n=1)  # type: ignore[arg-type]
    assert unknown_receipt.health == RetrievalHealth.PARTIAL
    assert unknown_receipt.reason_codes == (RetrievalIssueCode.INVALID_TIMESTAMP,)
    assert not unknown_receipt.results

    explicit_null = ReplayClient()
    explicit_null.query_result = {
        "ids": [["null"]],
        "documents": [["doc"]],
        "metadatas": [[None]],
        "distances": [[0.2]],
    }
    explicit_null_receipt = EpisodicMemoryStore(explicit_null).buscar_con_recibo("probe", n=1)  # type: ignore[arg-type]
    assert explicit_null_receipt.health == RetrievalHealth.PARTIAL
    assert explicit_null_receipt.reason_codes == (RetrievalIssueCode.MISSING_METADATA,)

    semantic_missing = ReplayClient()
    semantic_missing.query_result = {"ids": [["semantic-missing"]]}
    semantic_missing_receipt = SemanticMemoryStore(semantic_missing).buscar_con_recibo("probe", n=1)  # type: ignore[arg-type]
    assert semantic_missing_receipt.health == RetrievalHealth.PARTIAL
    assert semantic_missing_receipt.reason_codes == (RetrievalIssueCode.MALFORMED_BATCH,)

    return {
        "extra_can_override_reserved_fields": False,
        "extra_can_change_persisted_memory_kind": False,
        "extra_can_override_semantic_origin": False,
        "colliding_extra_preserved_as_data": True,
        "omitted_metadata_batch_uses_defaults": False,
        "omitted_metadata_batch_health": missing_receipt.health.value,
        "explicit_null_metadata_uses_defaults": False,
        "explicit_null_metadata_raises": False,
        "explicit_null_metadata_health": explicit_null_receipt.health.value,
        "unknown_raw_metadata_preserved_in_extra": False,
        "invalid_timestamp_replaced_with_current_time": False,
        "bool_numeric_metadata_accepted_as_number": False,
        "missing_document_default": None,
        "missing_distance_default": None,
        "semantic_missing_origin_default": None,
    }


def score_and_result_contract() -> dict[str, Any]:
    now = datetime.utcnow()
    low_distance = MemoryResult(
        EpisodicMemory(
            "low-distance",
            MemoryMetadata("event", 0.0, 0.6, timestamp=now),
            "low",
        ),
        distancia=0.2,
    )
    high_distance = MemoryResult(
        SemanticMemory(
            "high-distance",
            MemoryMetadata("rule", 0.0, 0.6, timestamp=now),
            "high",
        ),
        distancia=0.8,
    )
    retriever = MemoryRetriever(SearchStore(), SearchStore())  # type: ignore[arg-type]
    fixed_decay = {
        "peso_recencia": 0.5,
        "importancia_total": 0.4,
    }
    with patch.object(MemoryDecay, "calcular_importancia_efectiva", return_value=fixed_decay):
        retriever._calcular_scores([low_distance, high_distance])
    expected_low = 0.45 * 0.8 + 0.25 * 0.6 + 0.20 * 0.4 + 0.10 * 0.5
    expected_high = 0.45 * 0.2 + 0.25 * 0.6 + 0.20 * 0.4 + 0.10 * 0.5
    assert abs(low_distance.metadata_score - expected_low) < 1e-12
    assert abs(high_distance.metadata_score - expected_high) < 1e-12
    assert low_distance.distancia == 0.2 and high_distance.distancia == 0.8
    assert low_distance.metadata_score > high_distance.metadata_score

    out_of_range = MemoryResult(
        EpisodicMemory(
            "out-of-range",
            MemoryMetadata("event", 0.0, 4.0, timestamp=now),
            "range",
        ),
        distancia=-1.0,
    )
    with patch.object(MemoryDecay, "calcular_importancia_efectiva", return_value={
        "peso_recencia": 1.0,
        "importancia_total": 1.0,
    }):
        retriever._calcular_scores([out_of_range])
    assert out_of_range.metadata_score > 1.0

    bad_distance = MemoryResult(
        EpisodicMemory(
            "bad-distance",
            MemoryMetadata("event", 0.0, 0.5, timestamp=now),
            "bad",
        ),
        distancia="0.2",  # type: ignore[arg-type]
    )
    wrong_distance_raises = False
    try:
        retriever._calcular_scores([bad_distance])
    except TypeError:
        wrong_distance_raises = True
    assert wrong_distance_raises

    scoring_now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    aware_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    aware_weight = MemoryDecay.calcular_peso_recencia(
        aware_timestamp,
        now_utc=scoring_now,
    )
    naive_weight = MemoryDecay.calcular_peso_recencia(
        aware_timestamp.replace(tzinfo=None),
        now_utc=scoring_now,
    )
    assert aware_weight == naive_weight

    future_rejected = False
    try:
        MemoryDecay.calcular_peso_recencia(
            scoring_now + timedelta(seconds=1),
            now_utc=scoring_now,
        )
    except MemoryTemporalError as exc:
        future_rejected = exc.reason_code == RetrievalIssueCode.FUTURE_TIMESTAMP
    assert future_rejected

    return {
        "distance_semantics": "cosine_distance_lower_is_better",
        "metadata_score_semantics": "weighted_rerank_score_higher_is_better",
        "distance_mutated_by_rerank": False,
        "metadata_score_mutated_by_rerank": True,
        "metadata_score_default_before_rerank": 1.0,
        "score_is_bounded_to_zero_one": False,
        "wrong_distance_type_rejected_at_construction": False,
        "wrong_distance_type_fails_during_rerank": wrong_distance_raises,
        "timezone_aware_datetime_accepted_by_model": True,
        "timezone_aware_datetime_fails_decay": False,
        "naive_aware_same_instant_same_weight": True,
        "future_timestamp_rejected": future_rejected,
    }


def metadata_bool_sentinels() -> dict[str, Any]:
    """Exercise the two public readers that turn persisted flags into policy."""

    def alias_is_accepted(value: Any, *, include: bool = True) -> bool:
        metadata = {
            "object_id": "Tree_01",
            "human_concept": "arbol",
        }
        if include:
            metadata["approved"] = value
        manager = object.__new__(MemoryManager)
        manager.client = ListingClient([metadata])
        manager._alias_aprendidos_guardados = set()
        return bool(manager.obtener_aliases_aprendidos())

    def affect_is_accepted(value: Any, *, include: bool = True) -> bool:
        metadata = {
            "object_id": "Tree_01",
            "object_class": "tree",
            "affect": "alerta",
            "weight": 0.5,
            "source": "probe",
        }
        if include:
            metadata["confirmed"] = value
        manager = object.__new__(MemoryManager)
        manager.client = ListingClient([metadata])
        manager._memorias_afectivas_guardadas = set()
        return bool(manager.obtener_memorias_afectivas_espaciales())

    alias_matrix = {
        "missing": alias_is_accepted(None, include=False),
        "true_bool": alias_is_accepted(True),
        "false_bool": alias_is_accepted(False),
        "false_string": alias_is_accepted("false"),
        "zero_int": alias_is_accepted(0),
        "none": alias_is_accepted(None),
        "empty_string": alias_is_accepted(""),
    }
    affect_matrix = {
        "missing": affect_is_accepted(None, include=False),
        "true_bool": affect_is_accepted(True),
        "false_bool": affect_is_accepted(False),
        "false_string": affect_is_accepted("false"),
        "zero_int": affect_is_accepted(0),
        "none": affect_is_accepted(None),
        "empty_string": affect_is_accepted(""),
    }
    assert alias_matrix == {
        "missing": True,
        "true_bool": True,
        "false_bool": False,
        "false_string": True,
        "zero_int": True,
        "none": True,
        "empty_string": True,
    }
    assert affect_matrix == {
        "missing": True,
        "true_bool": True,
        "false_bool": False,
        "false_string": True,
        "zero_int": False,
        "none": False,
        "empty_string": False,
    }
    return {
        "alias_approved_acceptance": alias_matrix,
        "affective_confirmed_acceptance": affect_matrix,
        "bool_false_string_is_truthy": True,
        "alias_uses_identity_check_against_false": True,
        "affective_uses_bool_coercion": True,
    }


def facade_sentinel_contract() -> dict[str, Any]:
    def context_for(episodic: SearchStore, semantic: SearchStore) -> MemoryContextResult:
        manager = object.__new__(MemoryManager)
        manager.retriever = MemoryRetriever(episodic, semantic)  # type: ignore[arg-type]
        manager.summarizer = MemorySummarizer()
        return manager.obtener_contexto_memoria_con_metadata("probe", limite=3)

    no_hit = context_for(SearchStore(), SearchStore())
    read_error = context_for(SearchStore(error=RuntimeError("synthetic")), SearchStore())
    assert no_hit != read_error
    assert no_hit.texto == "No hay recuerdos relevantes para esta situación."
    assert no_hit.health == RetrievalHealth.OK_EMPTY
    assert read_error.texto == "" and read_error.health == RetrievalHealth.PARTIAL
    assert no_hit.retrieved_ids == () and no_hit.scores == ()

    manager = object.__new__(MemoryManager)
    manager.encoder = MemoryEncoder()
    manager._programar_consolidacion_si_aplica = lambda *_args, **_kwargs: None
    manager.episodic_store = StoreOutcome(False)
    false_write = manager.guardar_experiencia("event", "narrative")
    manager.episodic_store = StoreOutcome(RuntimeError("synthetic"))
    exception_write = manager.guardar_experiencia("event", "narrative")
    manager.episodic_store = StoreOutcome(True)
    successful_write = manager.guardar_experiencia("event", "narrative")
    assert false_write is None and exception_write is None and successful_write is not None

    manager.semantic_store = StoreOutcome(False)
    false_rule = manager.guardar_regla("rule")
    manager.semantic_store = StoreOutcome(RuntimeError("synthetic"))
    exception_rule = manager.guardar_regla("rule")
    assert false_rule is None and exception_rule is None

    ranked = MemoryResult(
        EpisodicMemory(
            "ranked",
            MemoryMetadata("event", 0.0, 0.5),
            "narrative",
        ),
        distancia=0.9,
        metadata_score=0.33,
    )
    manager_with_rank = object.__new__(MemoryManager)
    episodic_ranked = StoreRetrievalReceipt(
        RetrievalHealth.OK,
        (ranked,),
        candidate_count=1,
    )
    semantic_empty = StoreRetrievalReceipt(RetrievalHealth.OK_EMPTY)
    ranked_receipt = MemoryRetrievalReceipt(
        RetrievalHealth.OK,
        (ranked,),
        episodic_ranked,
        semantic_empty,
    )
    manager_with_rank.retriever = type(
        "ReceiptRetriever",
        (),
        {"buscar_con_recibo": lambda self, **_kwargs: ranked_receipt},
    )()
    manager_with_rank.summarizer = MemorySummarizer()
    context = manager_with_rank.obtener_contexto_memoria_con_metadata("probe")
    assert context.retrieved_ids == ("ranked",)
    assert context.scores == (0.33,) and context.scores != (ranked.distancia,)

    return {
        "no_hit_and_retrieval_error_same_public_result": False,
        "no_hit_health": no_hit.health.value,
        "retrieval_error_health": read_error.health.value,
        "no_hit_text_is_empty": False,
        "no_hit_text_sentinel": no_hit.texto,
        "failed_write_false_and_exception_both_return_none": True,
        "successful_episode_write_returns_id": True,
        "failed_rule_write_false_and_exception_both_return_none": True,
        "context_scores_use_metadata_score_not_distance": True,
        "context_ids_scores_keep_same_result_order": True,
    }


def main() -> None:
    report = {
        "probe": "contracts_memory_probe",
        "pass_semantics": "behavior_reproduced_not_behavior_approved",
        "chroma_metadata_matrix": chroma_metadata_matrix(),
        "valid_roundtrips": valid_roundtrips(),
        "optional_defaults_and_mutability": optional_defaults_and_mutability(),
        "metadata_collisions_and_missing_fields": metadata_collisions_and_missing_fields(),
        "score_and_result_contract": score_and_result_contract(),
        "metadata_bool_sentinels": metadata_bool_sentinels(),
        "facade_sentinel_contract": facade_sentinel_contract(),
        "result": "PASS",
    }
    # ASCII escapes make the evidence stable under Windows' varying console code pages.
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
