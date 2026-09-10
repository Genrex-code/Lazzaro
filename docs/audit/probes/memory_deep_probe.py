"""Deep, synthetic audit probe for Fase 2 / Bloque 4 (Memory).

PASS means that the current behavior was reproduced.  It does not mean that
the behavior is desirable.  The probe blocks network access, rejects any
Chroma construction under ``lazzaro_data``, uses only synthetic fixtures and
creates real Chroma collections only below ``TemporaryDirectory``.

Run from the repository root:
    python docs/audit/probes/memory_deep_probe.py
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
from typing import Any, Callable
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
ACTIVE_CHROMA = (ROOT / "lazzaro_data").resolve()

# Importing production logging must not append runtime logs during this probe.
logging.getLogger().handlers = [logging.NullHandler()]

import memory.chroma_client as chroma_client_module
from ai_engine.learned_alias_candidates import LearnedAliasCandidate
from memory.chroma_client import ChromaClient
from memory.conversation_memory import ConversationMemory
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_decay import MemoryDecay, MemoryTemporalError
from memory.memory_encoder import MemoryEncoder
from memory.memory_filters import MemoryFilters
from memory.memory_manager import MemoryContextResult, MemoryManager
from memory.memory_write_coordinator import MemoryWriteCoordinator
from memory.memory_write_ledger import MemoryWriteLedger
from memory.memory_write_types import MemoryWriteIssueCode, MemoryWriteStatus
from memory.test_memory_write_transactions import (
    FakeMutationClient,
    _consolidate as root015_consolidate,
    _create as root015_create,
    _source_row as root015_source_row,
)
from memory.memory_retriever import MemoryRetriever
from memory.memory_types import (
    EpisodicMemory,
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
from memory.working_memory import WorkingMemory


def require(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _deny_network(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("network access is forbidden in memory_deep_probe")


@contextmanager
def safety_barriers():
    """Block network and reject all construction on the active Chroma tree."""

    original = chroma_client_module.chromadb.PersistentClient

    def guarded_persistent_client(*args: Any, **kwargs: Any):
        raw_path = kwargs.get("path", args[0] if args else None)
        require(raw_path is not None, "Chroma path must be explicit")
        requested = Path(raw_path).expanduser().resolve()
        if requested == ACTIVE_CHROMA or ACTIVE_CHROMA in requested.parents:
            raise AssertionError("active lazzaro_data access is forbidden")
        return original(*args, **kwargs)

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
    """Tiny deterministic Chroma embedding function; no model or provider."""

    @staticmethod
    def name() -> str:
        return "lazzaro-memory-deep-audit-offline"

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in input:
            total = sum(ord(char) for char in text)
            vectors.append(
                [
                    float((total % 17) + 1),
                    float((len(text) % 11) + 1),
                    float((text.count("a") % 7) + 1),
                ]
            )
        return vectors

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def get_config(self) -> dict[str, Any]:
        return {}


def _set_offline_embedding(client: ChromaClient) -> None:
    require(client.disponible, "temporary Chroma must be available")
    client._collection._embedding_function = OfflineEmbeddingFunction()


def probe_real_roundtrip_delete_and_wrapper_isolation() -> dict[str, Any]:
    """Exercise the real adapter only in a temporary DB."""

    fixed = datetime(2026, 1, 2, 3, 4, 5, 6789)
    with TemporaryDirectory(prefix="lazzaro-memory-deep-") as temporary:
        temp_path = Path(temporary).resolve()
        require(temp_path != ACTIVE_CHROMA, "temporary DB cannot be active DB")
        require(ACTIVE_CHROMA not in temp_path.parents, "temporary DB is isolated")

        first = ChromaClient(temporary, collection_name="memory_deep_primary")
        second = ChromaClient(temporary, collection_name="memory_deep_secondary")
        _set_offline_embedding(first)
        _set_offline_embedding(second)
        require(first._lock is second._lock, "same-path wrappers must share the DB lock")

        try:
            episode = EpisodicMemory(
                id="synthetic-episode",
                metadata=MemoryMetadata(
                    tipo_evento="synthetic_event",
                    valencia=-0.25,
                    impacto_emocional=0.8,
                    timestamp=fixed,
                    extra={"scalar": "kept", "nested": {"preserved": True}},
                ),
                narrativa="synthetic episodic document",
                estado_emocional={"energia": 0.3, "estres": 0.7},
            )
            semantic = SemanticMemory(
                id="synthetic-semantic",
                metadata=MemoryMetadata(
                    tipo_evento="synthetic_rule",
                    valencia=0.2,
                    impacto_emocional=0.6,
                    timestamp=fixed,
                    extra={"scalar": 7, "items": [1, 2]},
                ),
                regla="synthetic semantic document",
                origen="synthetic-origin",
            )
            episodic_store = EpisodicMemoryStore(first)
            semantic_store = SemanticMemoryStore(first)
            require(episodic_store.almacenar(episode), "valid episode must persist")
            require(semantic_store.almacenar(semantic), "valid semantic memory must persist")
            require(second.agregar(["secondary sentinel"], [{"kind": "sentinel"}], ["sentinel"]), "secondary collection write")

            recovered_episode = episodic_store.buscar("synthetic episodic document", n=5)
            recovered_semantic = semantic_store.buscar("synthetic semantic document", n=5)
            require(len(recovered_episode) == 1, "episodic type filter must isolate one hit")
            require(len(recovered_semantic) == 1, "semantic type filter must isolate one hit")
            ep = recovered_episode[0].recuerdo
            sem = recovered_semantic[0].recuerdo
            require(ep.id == episode.id and ep.narrativa == episode.narrativa, "episode core roundtrip")
            require(ep.metadata.timestamp == fixed, "episode timestamp roundtrip")
            require(
                ep.metadata.extra == {"scalar": "kept", "nested": {"preserved": True}},
                "episode rich extra roundtrip",
            )
            require(
                ep.estado_emocional == {"energia": 0.3, "estres": 0.7},
                "episodic emotional state roundtrip",
            )
            require(sem.id == semantic.id and sem.regla == semantic.regla, "semantic core roundtrip")
            require(sem.origen == semantic.origen, "semantic origin roundtrip")
            require(sem.metadata.extra == {"scalar": 7, "items": [1, 2]}, "semantic rich extra roundtrip")

            before_delete = first.contar()
            require(first.eliminar([episode.id]), "valid delete call must report success")
            require(first.contar() == before_delete - 1, "valid delete removes exactly one row")
            require(second.contar() == 1, "delete in one collection must not touch another")

            unknown_delete = first.eliminar(["unknown-id"])
            count_after_unknown = first.contar()
            empty_delete = first.eliminar([])
            require(unknown_delete is True, "unknown-ID delete is accepted as a no-op")
            require(count_after_unknown == 1, "unknown-ID delete changes no rows")
            require(empty_delete is False, "empty delete is rejected by installed backend")

            unknown_update = first.actualizar_metadatos(
                ["unknown-id"],
                [{"kind": "updated"}],
            )
            require(unknown_update is True, "unknown-ID update is accepted as a no-op")
            require(first.contar() == 1, "unknown-ID update changes no rows")
        finally:
            first.close()
            second.close()

    return {
        "active_chroma_opened": False,
        "network_allowed": False,
        "episodic_roundtrip": "core, rich extra, and estado_emocional preserved in V1",
        "semantic_roundtrip": "core and rich extra preserved in V1",
        "valid_delete": "row removed",
        "unknown_delete": "true_noop",
        "empty_delete": "false",
        "unknown_update": "true_noop",
        "cross_collection_delete_isolated": True,
        "same_path_lock_shared": True,
    }


class QueryClient:
    def __init__(self, result: dict[str, Any] | None):
        self.result = result
        self.calls = 0

    @staticmethod
    def combinar_where(base, extra):
        return extra if not base else {"$and": [base, extra]}

    def buscar(self, *_args, **_kwargs):
        self.calls += 1
        return self.result


class SearchStore:
    def __init__(self, results: list[MemoryResult] | None = None, error: Exception | None = None):
        self.results = list(results or [])
        self.error = error
        self.calls = 0

    def buscar(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.results)

    def buscar_con_recibo(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return StoreRetrievalReceipt(
            health=RetrievalHealth.OK if self.results else RetrievalHealth.OK_EMPTY,
            results=tuple(self.results),
            candidate_count=len(self.results),
        )


def _synthetic_result(memory_id: str, semantic: bool = False, *, timestamp: datetime | None = None) -> MemoryResult:
    metadata = MemoryMetadata(
        tipo_evento="synthetic",
        valencia=0.0,
        impacto_emocional=0.5,
        timestamp=timestamp or datetime.utcnow(),
    )
    memory = (
        SemanticMemory(memory_id, metadata, f"rule-{memory_id}", "synthetic")
        if semantic
        else EpisodicMemory(memory_id, metadata, f"episode-{memory_id}")
    )
    return MemoryResult(memory, distancia=0.2)


def probe_retrieval_states_and_obs005() -> dict[str, Any]:
    failed_store_client = QueryClient(None)
    empty_store_client = QueryClient(
        {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    )
    failed_store = EpisodicMemoryStore(failed_store_client)  # type: ignore[arg-type]
    empty_store = EpisodicMemoryStore(empty_store_client)  # type: ignore[arg-type]
    failed_store_receipt = failed_store.buscar_con_recibo("synthetic")
    empty_store_receipt = empty_store.buscar_con_recibo("synthetic")
    require(failed_store_receipt.health == RetrievalHealth.FAILED, "store exposes client failure")
    require(empty_store_receipt.health == RetrievalHealth.OK_EMPTY, "store exposes valid no-hit")

    episode = _synthetic_result("episode")
    semantic = _synthetic_result("semantic", semantic=True)
    combinations = {
        "both_empty": (SearchStore(), SearchStore()),
        "episodic_error_semantic_hit": (
            SearchStore(error=RuntimeError("synthetic episodic failure")),
            SearchStore([semantic]),
        ),
        "episodic_hit_semantic_error": (
            SearchStore([episode]),
            SearchStore(error=RuntimeError("synthetic semantic failure")),
        ),
        "both_error": (
            SearchStore(error=RuntimeError("synthetic episodic failure")),
            SearchStore(error=RuntimeError("synthetic semantic failure")),
        ),
    }
    observed: dict[str, dict[str, Any]] = {}
    for name, (episodic, semantic_store) in combinations.items():
        retriever = MemoryRetriever(
            episodic,
            semantic_store,
            clock=lambda: datetime.now(timezone.utc),
        )  # type: ignore[arg-type]
        receipt = retriever.buscar_con_recibo("synthetic")
        observed[name] = {
            "ids": [item.recuerdo.id for item in receipt.results],
            "health": receipt.health.value,
        }
        require(episodic.calls == 1 and semantic_store.calls == 1, "hybrid retrieval calls both stores once")

    require(observed["both_empty"]["health"] == "OK_EMPTY", "healthy empty is explicit")
    require(observed["both_error"]["health"] == "FAILED", "both failures are explicit")
    require(observed["episodic_error_semantic_hit"] == {"ids": ["semantic"], "health": "PARTIAL"}, "partial semantic hit survives visibly")
    require(observed["episodic_hit_semantic_error"] == {"ids": ["episode"], "health": "PARTIAL"}, "partial episodic hit survives visibly")

    class OneSearchManager:
        buscar_recuerdos_con_recibo = MemoryManager.buscar_recuerdos_con_recibo
        obtener_contexto_memoria_con_metadata = MemoryManager.obtener_contexto_memoria_con_metadata

        def __init__(self):
            self.calls = 0
            self.summarizer = type(
                "SyntheticSummarizer",
                (),
                {"resumir": staticmethod(lambda memories, _limit: f"synthetic:{len(memories)}")},
            )()

        def buscar_recuerdos_con_recibo(self, query: str, limite: int = 5):
            self.calls += 1
            first = _synthetic_result("rank-1")
            second = _synthetic_result("rank-2", semantic=True)
            first.metadata_score = 0.9
            second.metadata_score = 0.7
            episodic = StoreRetrievalReceipt(RetrievalHealth.OK, (first,), 1)
            semantic_receipt = StoreRetrievalReceipt(RetrievalHealth.OK, (second,), 1)
            return MemoryRetrievalReceipt(
                RetrievalHealth.OK,
                (first, second),
                episodic,
                semantic_receipt,
            )

    one = OneSearchManager()
    context = one.obtener_contexto_memoria_con_metadata("synthetic", limite=5)
    require(one.calls == 1, "OBS-005 must use one hybrid retrieval")
    require(context.retrieved_ids == ("rank-1", "rank-2"), "OBS-005 IDs preserve rank")
    require(context.scores == (0.9, 0.7), "OBS-005 scores align with IDs")

    return {
        "chroma_failure_at_store": failed_store_receipt.health.value,
        "valid_no_hit_at_store": empty_store_receipt.health.value,
        "hybrid_queries_per_retrieval": 2,
        "partial_failure_visible_to_caller": True,
        "both_error_equals_no_hit": False,
        "partial_results_look_complete": False,
        "obs005_hybrid_search_calls": one.calls,
        "obs005_ids_scores_aligned": True,
    }


def probe_scoring_filters_and_poison_record() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    recent = _synthetic_result("recent", timestamp=now.replace(tzinfo=None))
    old = _synthetic_result("old", timestamp=now - timedelta(days=30))
    semantic_old = _synthetic_result(
        "semantic-old",
        semantic=True,
        timestamp=now - timedelta(days=365),
    )
    retriever = object.__new__(MemoryRetriever)
    MemoryRetriever._calcular_scores(retriever, [recent, old, semantic_old], now_utc=now)
    require(recent.metadata_score > old.metadata_score, "recent episode must outrank equivalent old episode")
    require(semantic_old.metadata_score > old.metadata_score, "semantic memory decays slower")

    unbounded = EpisodicMemory(
        "unbounded",
        MemoryMetadata("synthetic", 0.0, 10.0, now),
        "synthetic",
    )
    unbounded_result = MemoryResult(unbounded, distancia=-2.0)
    MemoryRetriever._calcular_scores(retriever, [unbounded_result], now_utc=now)
    require(unbounded_result.metadata_score > 1.0, "metadata_score is not range-bounded")

    future_rejected = False
    try:
        MemoryDecay.calcular_peso_recencia(now + timedelta(days=365), now_utc=now)
    except MemoryTemporalError as exc:
        future_rejected = exc.reason_code == RetrievalIssueCode.FUTURE_TIMESTAMP
    require(future_rejected, "future memory is rejected as untrusted temporal evidence")

    aware = _synthetic_result("aware", timestamp=now - timedelta(seconds=1))
    future = _synthetic_result("future", timestamp=now + timedelta(days=365))
    poison_store = SearchStore([recent, aware, future])
    empty_semantic = SearchStore()
    poison_retriever = MemoryRetriever(
        poison_store,
        empty_semantic,
        clock=lambda: now,
    )  # type: ignore[arg-type]
    poison_receipt = poison_retriever.buscar_con_recibo("synthetic")
    require(poison_receipt.health == RetrievalHealth.PARTIAL, "future row degrades health")
    require(
        {item.recuerdo.id for item in poison_receipt.results} == {"recent", "aware"},
        "valid naive and aware rows survive a future poison row",
    )

    class ManagerWithPoison:
        buscar_recuerdos_con_recibo = MemoryManager.buscar_recuerdos_con_recibo
        obtener_contexto_memoria_con_metadata = MemoryManager.obtener_contexto_memoria_con_metadata

        def __init__(self):
            self.retriever = poison_retriever
            self.summarizer = type(
                "SyntheticSummarizer",
                (),
                {"resumir": staticmethod(lambda memories, _limit: f"synthetic:{len(memories)}")},
            )()

    visible = ManagerWithPoison().obtener_contexto_memoria_con_metadata("synthetic")
    require(visible.health == RetrievalHealth.PARTIAL, "manager exposes poison health")
    require(visible.retrieved_ids == tuple(item.recuerdo.id for item in poison_receipt.results), "manager preserves valid poison-batch results")

    filters = [
        MemoryFilters.por_tipo_evento("synthetic"),
        MemoryFilters.por_impacto_minimo(0.7),
        MemoryFilters.por_valencia(0.0, "$gt"),
    ]
    originals = [dict(item) for item in filters]
    combined = MemoryFilters.combinado(*filters)
    require(filters == originals, "filter composition does not mutate inputs")
    require(combined == {"$and": filters}, "filter composition is structural")
    recent_filter = MemoryFilters.recientes(24)
    require(recent_filter == {"_recencia_horas": 24}, "recent filter is only a marker")

    invalid_filter_client = QueryClient(None)
    invalid_filter_store = EpisodicMemoryStore(invalid_filter_client)  # type: ignore[arg-type]
    require(
        invalid_filter_store.buscar_con_recibo("synthetic", where=recent_filter).health
        == RetrievalHealth.FAILED,
        "an invalid recent-filter query is visible as failure",
    )

    return {
        "final_sort": "metadata_score_descending",
        "recent_episode_beats_old": True,
        "semantic_decay_slower": True,
        "metadata_score_bounded_0_1": False,
        "future_timestamp_recency": "rejected:FUTURE_TIMESTAMP",
        "aware_timestamp_poisoned_all_results": False,
        "poison_visible_to_manager": True,
        "filters_mutate_inputs": False,
        "recientes_interpreted_by_retriever": False,
    }


class OutcomeStore:
    def __init__(self, outcome: bool | Exception = True, barrier: threading.Barrier | None = None):
        self.outcome = outcome
        self.barrier = barrier
        self.memories: list[Any] = []
        self._lock = threading.Lock()

    def almacenar(self, memory: Any) -> bool:
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.outcome:
            with self._lock:
                self.memories.append(memory)
        return self.outcome


def _write_manager(store: OutcomeStore) -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.encoder = MemoryEncoder()
    manager.episodic_store = store
    manager._programar_consolidacion_si_aplica = lambda *_args, **_kwargs: None
    return manager


def probe_write_receipts_and_concurrent_duplicates() -> dict[str, Any]:
    """Conserva evidencia historica del adapter sin __init__; no es runtime ROOT-015."""
    failed = _write_manager(OutcomeStore(False))
    require(failed.guardar_experiencia("synthetic", "synthetic") is None, "store False becomes None")
    errored = _write_manager(OutcomeStore(RuntimeError("synthetic store failure")))
    require(errored.guardar_experiencia("synthetic", "synthetic") is None, "store exception becomes None")

    post_write_store = OutcomeStore(True)
    post_write = _write_manager(post_write_store)

    def fail_after_write(*_args, **_kwargs):
        raise RuntimeError("synthetic consolidation failure")

    post_write._programar_consolidacion_si_aplica = fail_after_write
    returned = post_write.guardar_experiencia("observacion_visual", "synthetic")
    require(returned is None, "post-write consolidation failure reports None")
    require(len(post_write_store.memories) == 1, "episode was already stored before None receipt")

    barrier = threading.Barrier(2)
    concurrent_store = OutcomeStore(True, barrier=barrier)
    concurrent = _write_manager(concurrent_store)
    results: list[str | None] = []

    def write_same_event() -> None:
        results.append(concurrent.guardar_experiencia("synthetic", "identical synthetic event"))

    threads = [threading.Thread(target=write_same_event) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        require(not thread.is_alive(), "concurrent write must terminate")

    ids = [memory.id for memory in concurrent_store.memories]
    require(len(results) == 2 and all(results), "both identical writes report success")
    require(len(ids) == 2 and len(set(ids)) == 2, "identical concurrent events create distinct IDs")

    return {
        "surface": "legacy_object_new_compatibility_only",
        "store_false_receipt": None,
        "store_exception_receipt": None,
        "post_write_consolidation_failure_receipt": returned,
        "post_write_data_exists": True,
        "identical_concurrent_writes": len(ids),
        "generic_episode_dedupe": False,
    }


class ConsolidationClient:
    def __init__(self, rows: dict[str, Any], delete_outcomes: list[bool] | None = None):
        self.rows = rows
        self.delete_outcomes = list(delete_outcomes or [True])
        self.delete_calls: list[list[str]] = []
        self.existing_batch = False

    def listar(self, where=None, limite=None):
        if where and "lote_consolidacion" in where:
            return {"ids": ["synthetic-rule"] if self.existing_batch else []}
        return self.rows

    def eliminar(self, ids):
        self.delete_calls.append(list(ids))
        outcome = self.delete_outcomes.pop(0) if self.delete_outcomes else True
        if outcome:
            removed = set(ids)
            keep = [i for i, memory_id in enumerate(self.rows["ids"]) if memory_id not in removed]
            for key in ("ids", "documents", "metadatas"):
                self.rows[key] = [self.rows[key][i] for i in keep]
        return outcome


def _consolidation_rows(count: int = 2) -> dict[str, Any]:
    old = (datetime.utcnow() - timedelta(days=30)).isoformat()
    return {
        "ids": [f"episode-{index}" for index in range(count)],
        "documents": [f"synthetic observation {index}" for index in range(count)],
        "metadatas": [
            {
                "tipo": "episodico",
                "tipo_evento": "observacion_visual",
                "impacto_emocional": 0.1,
                "timestamp": old,
            }
            for _ in range(count)
        ],
    }


def _consolidation_manager(client: ConsolidationClient) -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.client = client
    manager.decay = MemoryDecay()
    manager.consolidacion_umbral = 2
    manager.consolidacion_antiguedad_minutos = 5
    manager.consolidacion_importancia_maxima = 0.25
    manager.saved_rules = []

    def save_rule(**kwargs):
        manager.saved_rules.append(kwargs)
        client.existing_batch = True
        return f"rule-{len(manager.saved_rules)}"

    manager.guardar_regla = save_rule
    return manager


def probe_consolidation_partial_failure_and_concurrency() -> dict[str, Any]:
    """Caracteriza solo el camino legacy de fakes que omiten ownership/runtime init."""
    client = ConsolidationClient(_consolidation_rows(), delete_outcomes=[False, True])
    manager = _consolidation_manager(client)
    manager.consolidar_memorias_si_hace_falta()
    require(len(manager.saved_rules) == 1, "semantic rule is stored before delete")
    require(len(client.rows["ids"]) == 2, "failed delete retains source episodes")
    manager.consolidar_memorias_si_hace_falta()
    require(len(manager.saved_rules) == 1, "retry detects existing batch instead of adding a rule")
    require(client.rows["ids"] == [], "retry can delete retained episodes")

    failed_rule_client = ConsolidationClient(_consolidation_rows())
    failed_rule = _consolidation_manager(failed_rule_client)
    failed_rule.guardar_regla = lambda **_kwargs: None
    failed_rule.consolidar_memorias_si_hace_falta()
    require(not failed_rule_client.delete_calls, "failed semantic write never deletes episodes")

    concurrent_client = ConsolidationClient(_consolidation_rows())
    concurrent = _consolidation_manager(concurrent_client)
    miss_barrier = threading.Barrier(2)

    def both_miss(_batch: str) -> bool:
        miss_barrier.wait(timeout=5)
        return False

    concurrent._ya_existe_consolidacion_lote = both_miss
    consolidation_threads = [
        threading.Thread(target=concurrent.consolidar_memorias_si_hace_falta)
        for _ in range(2)
    ]
    for thread in consolidation_threads:
        thread.start()
    for thread in consolidation_threads:
        thread.join(timeout=10)
        require(not thread.is_alive(), "concurrent consolidation must terminate")
    require(len(concurrent.saved_rules) == 2, "two concurrent misses create duplicate rules")

    counter_manager = object.__new__(MemoryManager)
    counter_manager.revision_consolidacion_cada = 5
    counter_manager._observaciones_desde_revision = 0
    counter_manager._ram_state_lock = threading.Lock()
    counter_manager.consolidar_memorias_si_hace_falta = lambda: None
    counter_barrier = threading.Barrier(3)

    def increment_counter() -> None:
        counter_barrier.wait(timeout=5)
        MemoryManager._programar_consolidacion_si_aplica(
            counter_manager,
            "observacion_visual",
            {},
            "synthetic",
        )

    counter_threads = [
        threading.Thread(target=increment_counter)
        for _ in range(2)
    ]
    for thread in counter_threads:
        thread.start()
    counter_barrier.wait(timeout=5)
    for thread in counter_threads:
        thread.join(timeout=10)
        require(not thread.is_alive(), "counter interleaving must terminate")
    require(counter_manager._observaciones_desde_revision == 2, "both protected increments must survive")

    saved = manager.saved_rules[0]
    require("lote_consolidacion" in saved["extra"], "consolidated rule keeps a batch hash")
    require("consolidacion_de" in saved["extra"], "consolidated rule keeps source count")
    require("ids" not in saved["extra"], "consolidated rule does not retain source IDs")

    return {
        "surface": "legacy_object_new_compatibility_only",
        "operation_order": ["semantic_rule", "delete_episodes"],
        "rule_failure_deleted_sources": False,
        "delete_failure_state": "rule_and_sources_coexist",
        "retry_after_delete_failure": "existing_rule_then_delete_sources",
        "transactional_boundary": False,
        "concurrent_rules_for_same_batch": len(concurrent.saved_rules),
        "two_counter_increments_observed_as": counter_manager._observaciones_desde_revision,
        "source_ids_retained_in_rule": False,
        "memory_loss_demonstrated": False,
    }


def probe_ram_memories() -> dict[str, Any]:
    working = WorkingMemory(max_size=2)
    working.agregar("action-1", "despachada", {"mutable": "before"})
    first = working.obtener_todas()[0]
    require(first.resultado == "despachada", "working memory stores dispatch status")
    first.detalles["mutable"] = "after"  # type: ignore[index]
    require(working.obtener_todas()[0].detalles["mutable"] == "after", "details remain shallow-mutable")
    working.agregar("action-2", "ok")
    working.agregar("action-3", "error")
    require([item.accion for item in working.obtener_todas()] == ["action-2", "action-3"], "FIFO eviction")
    require(not hasattr(working, "actualizar_resultado"), "no outcome reconciliation API")
    require(hasattr(working, "_lock"), "working memory must retain its explicit lock")

    manager = object.__new__(MemoryManager)
    manager.working_memory = working
    summary_n1 = MemoryManager.obtener_ultimas_acciones(manager, n=1)
    require("action-2" in summary_n1 and "action-3" in summary_n1, "public n argument is ignored")

    conversation = ConversationMemory(max_turns=2)
    private_metadata = {"private": "synthetic-metadata"}
    conversation.registrar_turno(
        "synthetic user one",
        "synthetic assistant one",
        pensamiento="synthetic-private-thought",
        route="persona",
        intent="synthetic-intent",
        metadata=private_metadata,
    )
    conversation.registrar_turno("synthetic user two", "synthetic assistant two")
    conversation.registrar_turno("synthetic user three", "synthetic assistant three")
    raw_turns = conversation.obtener_ultimos(5)
    require(len(raw_turns) == 2 and raw_turns[0].usuario.endswith("two"), "conversation FIFO eviction")

    privacy = ConversationMemory(max_turns=2)
    privacy.registrar_turno(
        "synthetic public user",
        "synthetic public assistant",
        pensamiento="synthetic-private-thought",
        metadata=private_metadata,
    )
    private_metadata["private"] = "mutated"
    raw = privacy.obtener_ultimos(1)[0]
    require(raw.metadata["private"] == "mutated", "raw metadata remains shallow-mutable")
    summary = privacy.resumen()
    public = privacy.snapshot_publico()
    require("synthetic-private-thought" not in summary, "summary excludes private thought")
    require(not hasattr(public[0], "pensamiento") and not hasattr(public[0], "metadata"), "public snapshot strips private fields")
    require(hasattr(privacy, "_lock"), "conversation memory uses an RLock")

    return {
        "working_capacity": 2,
        "working_eviction": "oldest_first",
        "working_represents": "dispatch_status_not_later_outcome",
        "working_reconciliation_api": False,
        "working_explicit_lock": True,
        "working_public_n_honored": False,
        "conversation_capacity": 2,
        "conversation_eviction": "oldest_first",
        "conversation_lock": True,
        "conversation_raw_keeps_private_fields": True,
        "summary_keeps_private_thought": False,
        "manual_public_snapshot_keeps_private_fields": False,
    }


def probe_root015_journaled_runtime_path() -> dict[str, Any]:
    """Comprueba el camino productivo con journal temporal, sin tocar Chroma activo."""
    with TemporaryDirectory(prefix="lazzaro-root015-deep-") as temporary:
        ledger_path = str(Path(temporary) / "memory_write_ledger.sqlite3")
        client = FakeMutationClient()
        ledger = MemoryWriteLedger(ledger_path)
        ledger.open()
        coordinator = MemoryWriteCoordinator(client, ledger)

        created = root015_create(coordinator, key="deep-event", text="deep-safe")
        replay = root015_create(coordinator, key="deep-event", text="deep-safe")
        conflict = root015_create(coordinator, key="deep-event", text="different")
        require(created.status is MemoryWriteStatus.CREATED, "new write must be CREATED")
        require(replay.status is MemoryWriteStatus.CREATED and replay.replayed, "replay preserves durable CREATED")
        require(replay.target_id == created.target_id and client.add_calls == 1, "replay reuses target without add")
        require(
            conflict.status is MemoryWriteStatus.FAILED
            and MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT in conflict.reason_codes,
            "same key/different intent must conflict",
        )
        require(client.add_calls == 1, "conflict must happen before Chroma")

        for source_id in ("deep-source-a", "deep-source-b"):
            client.rows[source_id] = root015_source_row(source_id)
        consolidation = root015_consolidate(
            coordinator,
            ids=("deep-source-a", "deep-source-b"),
            text="deep consolidated pattern",
        )
        require(consolidation.status is MemoryWriteStatus.CREATED, "consolidation must commit")
        require(not any(source_id in client.rows for source_id in ("deep-source-a", "deep-source-b")), "verified replacement permits cleanup")
        require(not ledger.in_transaction, "no SQLite transaction remains across Chroma")
        coordinator.close()

    return {
        "surface": "root015_journaled_runtime",
        "created": created.status.value,
        "replay_status": replay.status.value,
        "replayed": replay.replayed,
        "same_target": replay.target_id == created.target_id,
        "physical_add_calls_for_replay": 1,
        "conflict": conflict.reason_codes[0].value,
        "conflict_chroma_calls": 0,
        "consolidation": consolidation.status.value,
        "sources_deleted_after_verified_replacement": True,
        "sqlite_transaction_open_after_calls": False,
        "journal_temporary": True,
    }


class ManualMemoryClient:
    def __init__(self):
        self.records: list[tuple[str, str, dict[str, Any]]] = []
        self.fail_reads = False

    def listar(self, where=None, limite=None):
        if self.fail_reads:
            return None
        rows = []
        for memory_id, document, metadata in self.records:
            if where and not all(metadata.get(key) == value for key, value in where.items()):
                continue
            rows.append((memory_id, document, metadata))
        if limite is not None:
            rows = rows[:limite]
        return {
            "ids": [row[0] for row in rows],
            "documents": [row[1] for row in rows],
            "metadatas": [dict(row[2]) for row in rows],
        }

    def actualizar_metadatos(self, ids, metadatos):
        updated = False
        for target, metadata in zip(ids, metadatos):
            for index, (memory_id, document, _old) in enumerate(self.records):
                if memory_id == target:
                    self.records[index] = (memory_id, document, dict(metadata))
                    updated = True
        return updated


def _manual_manager(client: ManualMemoryClient) -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.client = client
    manager.conversation_memory = ConversationMemory(max_turns=12)
    manager._guardado_conversacional_lock = threading.RLock()
    manager._fotografias_conversacionales_pendientes = __import__("collections").OrderedDict()

    def save_rule(**kwargs):
        memory_id = f"manual-{len(client.records) + 1}"
        metadata = {
            "tipo": "semantico",
            "tipo_evento": kwargs["tipo_evento"],
            "origen": kwargs["origen"],
            **kwargs["extra"],
        }
        client.records.append((memory_id, kwargs["regla"], metadata))
        return memory_id

    manager.guardar_regla = save_rule
    return manager


def probe_manual_save_idempotency_and_privacy() -> dict[str, Any]:
    with TemporaryDirectory(prefix="lazzaro-root015-manual-probe-") as temporary:
        ledger_path = str(Path(temporary) / "manual.sqlite3")
        client = FakeMutationClient()
        manager = MemoryManager(client, memory_write_ledger_path=ledger_path)
        manager.conversation_memory.registrar_turno(
            "synthetic public user",
            "synthetic public assistant",
            pensamiento="synthetic private thought",
            metadata={"secret_marker": "synthetic private metadata"},
        )

        first = manager.guardar_fotografia_conversacional("synthetic-transaction-1")
        same_transaction = manager.guardar_fotografia_conversacional("synthetic-transaction-1")
        same_snapshot = manager.guardar_fotografia_conversacional("synthetic-transaction-2")
        require(first.status == "saved", "first manual save persists")
        require(same_transaction.status == "already_saved", "same transaction is idempotent")
        require(same_snapshot.status == "already_saved", "same fingerprint with new transaction reuses record")
        require(len(client.rows) == 1, "idempotent saves keep one semantic record")

        document = next(iter(client.rows.values())).document
        require("synthetic public user" in document and "synthetic public assistant" in document, "public transcript persists")
        require("synthetic private thought" not in document, "private thought does not persist")
        require("synthetic private metadata" not in document, "turn metadata does not persist")
        manager.close()

        restarted = MemoryManager(client, memory_write_ledger_path=ledger_path)
        after_restart = restarted.guardar_fotografia_conversacional("synthetic-transaction-1")
        require(after_restart.status == "already_saved", "durable transaction survives manager restart")
        restarted.close()

        failed_client = FakeMutationClient()
        failed_client.add_mode = "false_before"
        pending = MemoryManager(
            failed_client,
            memory_write_ledger_path=str(Path(temporary) / "pending.sqlite3"),
        )
        pending.conversation_memory.registrar_turno("synthetic old user", "synthetic old assistant")
        failed = pending.guardar_fotografia_conversacional("synthetic-pending")
        pending.conversation_memory.registrar_turno("synthetic new user", "synthetic new assistant")
        failed_client.add_mode = "success"
        retry = pending.guardar_fotografia_conversacional("synthetic-pending")
        require(failed.status == "error" and retry.status == "saved", "pending snapshot is retryable")
        require(retry.turn_count == 1, "retry reuses first snapshot instead of capturing new turn")
        retry_document = next(iter(failed_client.rows.values())).document
        require("synthetic new user" not in retry_document, "retry document excludes later turn")
        pending.close()

        read_error_client = FakeMutationClient()
        read_error_client.listar = lambda **_kwargs: None
        read_error = MemoryManager(
            read_error_client,
            memory_write_ledger_path=str(Path(temporary) / "read-error.sqlite3"),
        )
        read_error.conversation_memory.registrar_turno("synthetic", "synthetic")
        lookup_failure = read_error.guardar_fotografia_conversacional("synthetic-read-error")
        require(lookup_failure.status == "error", "manual save distinguishes lookup failure from empty")
        read_error.close()

    return {
        "first_status": first.status,
        "same_transaction_status": same_transaction.status,
        "same_fingerprint_new_transaction_status": same_snapshot.status,
        "durable_after_restart_status": after_restart.status,
        "pending_retry_statuses": [failed.status, retry.status],
        "pending_snapshot_frozen_turn_count": retry.turn_count,
        "public_transcript_persisted": True,
        "private_thought_persisted": False,
        "turn_metadata_persisted": False,
        "lookup_error_distinguished": True,
    }


class ListingClient:
    def __init__(self, metadatas: list[dict[str, Any]] | None = None, *, fail: bool = False):
        self.metadatas = list(metadatas or [])
        self.fail = fail

    @staticmethod
    def combinar_where(base, extra):
        return {"$and": [base, extra]}

    def listar(self, **_kwargs):
        if self.fail:
            return None
        return {"ids": [f"synthetic-{i}" for i in range(len(self.metadatas))], "metadatas": list(self.metadatas)}


def probe_alias_affect_relational_side_effects_and_race() -> dict[str, Any]:
    alias_manager = object.__new__(MemoryManager)
    alias_manager.client = ListingClient(
        [{"object_id": "synthetic-object", "human_concept": "synthetic-alias", "approved": "false"}]
    )
    alias_manager._alias_aprendidos_guardados = set()
    aliases = alias_manager.obtener_aliases_aprendidos()
    require(aliases == {}, "string false alias must be rejected")
    require(not alias_manager._alias_aprendidos_guardados, "rejected alias cannot mutate dedupe cache")

    affect_manager = object.__new__(MemoryManager)
    affect_manager.client = ListingClient(
        [{
            "object_id": "synthetic-object",
            "object_class": "synthetic-class",
            "affect": "alerta",
            "weight": 0.8,
            "source": "synthetic",
            "confirmed": "false",
        }]
    )
    affect_manager._memorias_afectivas_guardadas = set()
    affect = affect_manager.obtener_memorias_afectivas_espaciales()
    require(affect == {}, "string false affect must be rejected")
    require(not affect_manager._memorias_afectivas_guardadas, "rejected affect cannot mutate dedupe cache")

    barrier = threading.Barrier(2)
    duplicate_store = OutcomeStore(True, barrier=barrier)
    race = object.__new__(MemoryManager)
    race.client = ListingClient([])
    race.encoder = MemoryEncoder()
    race.semantic_store = duplicate_store
    race._alias_aprendidos_guardados = set()
    candidate = LearnedAliasCandidate(
        object_id="synthetic-object",
        human_concept="synthetic-alias",
        confidence=0.99,
        evidence="synthetic evidence",
        action="synthetic-action",
    )
    alias_results: list[str | None] = []

    def save_alias() -> None:
        alias_results.append(race.guardar_alias_aprendido(candidate))

    threads = [threading.Thread(target=save_alias) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        require(not thread.is_alive(), "alias race must terminate")
    require(len(duplicate_store.memories) == 2, "check-then-write alias race duplicates learning")

    relational = object.__new__(MemoryManager)
    relational.client = ListingClient([], fail=True)
    relational.conversation_memory = ConversationMemory()
    relational._hechos_conversacionales_guardados = set()
    relational_rules: list[dict[str, Any]] = []
    relational.guardar_regla = lambda **kwargs: relational_rules.append(kwargs) or "synthetic-relational"
    relational.registrar_turno_conversacional(
        mensaje_usuario="mi nombre es SyntheticUser",
        respuesta_lazzaro="synthetic response",
        route="persona",
        intent="MEMORY_UPDATE",
    )
    require(len(relational_rules) == 1, "ordinary conversation can derive a semantic write")

    now = datetime.utcnow()
    old_active = _synthetic_result("old-active", semantic=True, timestamp=now - timedelta(days=1))
    old_active.recuerdo.metadata.extra.update({"categoria_relacional": "apodo_lazzaro", "estado_relacional": "activo"})
    new_revoked = _synthetic_result("new-revoked", semantic=True, timestamp=now)
    new_revoked.recuerdo.metadata.extra.update({"categoria_relacional": "apodo_lazzaro", "estado_relacional": "revocado"})
    selected = MemoryManager._seleccionar_memorias_relacionales_vigentes(relational, [old_active, new_revoked])
    require([item.recuerdo.id for item in selected] == ["new-revoked"], "latest relational correction wins per category")

    return {
        "surface": "legacy_object_new_compatibility_only",
        "approved_string_false_accepted": False,
        "confirmed_string_false_accepted": False,
        "current_alias_writer_emits_bool_true": True,
        "productive_affect_writer_found": False,
        "rejected_alias_mutates_dedupe_cache": False,
        "rejected_affect_mutates_dedupe_cache": False,
        "concurrent_identical_alias_rows": len(duplicate_store.memories),
        "ordinary_turn_can_write_relational_fact": True,
        "durable_dedupe_read_failure_means_absent": True,
        "latest_relational_fact_selected": "new-revoked",
    }


class LockCollection:
    def __init__(self, entered: threading.Event, release: threading.Event, calls: list[str]):
        self.entered = entered
        self.release = release
        self.calls = calls

    def add(self, **_kwargs):
        self.calls.append("add-enter")
        self.entered.set()
        require(self.release.wait(timeout=5), "synthetic add release")
        self.calls.append("add-exit")

    def delete(self, **_kwargs):
        self.calls.append("delete-enter")


def probe_chroma_operation_lock_scope() -> dict[str, Any]:
    shared = threading.RLock()
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    collection = LockCollection(entered, release, calls)

    writer = object.__new__(ChromaClient)
    writer._collection = collection
    writer._lock = shared
    writer.collection_name = "synthetic"
    deleter = object.__new__(ChromaClient)
    deleter._collection = collection
    deleter._lock = shared
    deleter.collection_name = "synthetic"

    write_thread = threading.Thread(
        target=writer.agregar,
        args=(["synthetic"], [{"kind": "synthetic"}], ["synthetic"]),
    )
    delete_thread = threading.Thread(target=deleter.eliminar, args=(["synthetic"],))
    write_thread.start()
    require(entered.wait(timeout=5), "write must enter collection")
    delete_thread.start()
    delete_thread.join(timeout=0.05)
    require("delete-enter" not in calls, "delete cannot enter while add holds shared lock")
    release.set()
    write_thread.join(timeout=5)
    delete_thread.join(timeout=5)
    require(calls == ["add-enter", "add-exit", "delete-enter"], "adapter operations serialize")

    return {
        "surface": "chroma_adapter_call_scope_only",
        "individual_chroma_calls_serialized_same_path": True,
        "multi_call_memory_transactions_serialized": False,
        "observed_order": calls,
    }


PROBES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("real_roundtrip_delete_and_wrapper_isolation", probe_real_roundtrip_delete_and_wrapper_isolation),
    ("retrieval_states_and_obs005", probe_retrieval_states_and_obs005),
    ("scoring_filters_and_poison_record", probe_scoring_filters_and_poison_record),
    ("write_receipts_and_concurrent_duplicates", probe_write_receipts_and_concurrent_duplicates),
    ("consolidation_partial_failure_and_concurrency", probe_consolidation_partial_failure_and_concurrency),
    ("root015_journaled_runtime_path", probe_root015_journaled_runtime_path),
    ("ram_memories", probe_ram_memories),
    ("manual_save_idempotency_and_privacy", probe_manual_save_idempotency_and_privacy),
    ("alias_affect_relational_side_effects_and_race", probe_alias_affect_relational_side_effects_and_race),
    ("chroma_operation_lock_scope", probe_chroma_operation_lock_scope),
]


def main() -> None:
    results: dict[str, Any] = {}
    with safety_barriers():
        for name, probe in PROBES:
            results[name] = probe()
    print(
        json.dumps(
            {
                "probe": "memory_deep_probe",
                "meaning": "PASS reproduces current behavior; it is not a correctness claim",
                "synthetic_data_only": True,
                "active_chroma_opened": False,
                "network_allowed": False,
                "status": "PASS",
                "groups": len(results),
                "results": results,
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
