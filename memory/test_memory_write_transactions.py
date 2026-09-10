"""Matriz focal ROOT-015: saga idempotente, verificacion y concurrencia."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import logging
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import threading
from typing import Any, Iterator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_engine.learned_alias_candidates import LearnedAliasCandidate
from memory.chroma_client import ChromaClient
from memory.memory_encoder import MemoryEncoder
from memory.memory_manager import MemoryManager
from memory.memory_schema import EPISODIC_KIND, MemorySchemaCodec
from memory.memory_types import MemoryDocument
from memory.memory_write_coordinator import MemoryWriteCoordinator
from memory.memory_write_ledger import MemoryWriteLedger
from memory.memory_write_types import (
    ExactLookupReceipt,
    ExactLookupStatus,
    ExactMemoryRow,
    MemoryWriteIssueCode,
    MemoryWriteKind,
    MemoryWriteState,
    MemoryWriteStatus,
    NON_TERMINAL_MEMORY_WRITE_STATES,
    merge_write_issue_codes,
)
from memory.retrieval_types import RetrievalHealth


ROOT = Path(__file__).resolve().parents[1]


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeMutationClient:
    def __init__(self) -> None:
        self.rows: dict[str, ExactMemoryRow] = {}
        self.add_calls = 0
        self.update_calls = 0
        self.delete_calls: list[list[str]] = []
        self.search_calls = 0
        self.lookup_calls = 0
        self.add_mode = "success"
        self.update_mode = "success"
        self.delete_mode = "success"
        self.fail_lookup_calls: set[int] = set()
        self.events: list[str] = []
        self.add_entered: threading.Event | None = None
        self.release_add: threading.Event | None = None
        self.transaction_probe: Any = None
        self._lock = threading.RLock()

    @staticmethod
    def combinar_where(base, extra):
        if not base:
            return extra
        if not extra:
            return base
        return {"$and": [base, extra]}

    def agregar(self, documentos, metadatos, ids) -> bool:
        with self._lock:
            self.add_calls += 1
            self.events.append("add")
            if self.transaction_probe is not None:
                assert_true(not self.transaction_probe.in_transaction, "SQLite no debe estar en transaccion durante add")
            entered = self.add_entered
            release = self.release_add
            mode = self.add_mode
        if entered is not None:
            entered.set()
        if release is not None:
            assert_true(release.wait(timeout=5), "La compuerta del fake debe liberarse")
        if mode in {"false_before", "raise_before"}:
            if mode == "raise_before":
                raise RuntimeError("CANARY_COGNITIVE_PAYLOAD")
            return False
        with self._lock:
            for memory_id, document, metadata in zip(ids, documentos, metadatos):
                if str(memory_id) not in self.rows:
                    self.rows[str(memory_id)] = ExactMemoryRow(
                        id=str(memory_id),
                        document=str(document),
                        metadata=dict(metadata) if isinstance(metadata, dict) else None,
                    )
        if mode == "write_then_raise":
            raise RuntimeError("CANARY_COGNITIVE_PAYLOAD")
        return mode != "write_then_false"

    def obtener_por_ids(self, ids: list[str]) -> ExactLookupReceipt:
        with self._lock:
            self.lookup_calls += 1
            call = self.lookup_calls
            self.events.append("exact")
            if self.transaction_probe is not None:
                assert_true(not self.transaction_probe.in_transaction, "SQLite no debe estar en transaccion durante exact get")
            if call in self.fail_lookup_calls:
                return ExactLookupReceipt(
                    status=ExactLookupStatus.FAILED,
                    requested_ids=tuple(ids),
                )
            rows = tuple(self.rows[item] for item in ids if item in self.rows)
        return ExactLookupReceipt(
            status=ExactLookupStatus.PRESENT if rows else ExactLookupStatus.ABSENT,
            requested_ids=tuple(ids),
            rows=rows,
        )

    def actualizar_metadatos(self, ids, metadatos) -> bool:
        with self._lock:
            self.update_calls += 1
            if self.transaction_probe is not None:
                assert_true(not self.transaction_probe.in_transaction, "SQLite no debe estar en transaccion durante update")
            if self.update_mode == "raise_before":
                raise RuntimeError("CANARY_COGNITIVE_PAYLOAD")
            if self.update_mode == "false_before":
                return False
            for memory_id, metadata in zip(ids, metadatos):
                row = self.rows.get(str(memory_id))
                if row is not None:
                    self.rows[str(memory_id)] = ExactMemoryRow(
                        id=row.id,
                        document=row.document,
                        metadata=dict(metadata),
                    )
            return True

    def eliminar(self, ids) -> bool:
        with self._lock:
            self.delete_calls.append([str(item) for item in ids])
            self.events.append("delete")
            if self.transaction_probe is not None:
                assert_true(not self.transaction_probe.in_transaction, "SQLite no debe estar en transaccion durante delete")
            if self.delete_mode == "raise_before":
                raise RuntimeError("CANARY_COGNITIVE_PAYLOAD")
            if self.delete_mode == "false_before":
                return False
            selected = list(ids)
            if self.delete_mode == "partial_one":
                selected = selected[:1]
            for memory_id in selected:
                self.rows.pop(str(memory_id), None)
            return self.delete_mode != "write_then_false"

    def listar(self, where=None, limite=None):
        with self._lock:
            selected = [row for row in self.rows.values() if self._matches(row.metadata or {}, where)]
        if limite is not None:
            selected = selected[:limite]
        return {
            "ids": [row.id for row in selected],
            "documents": [row.document for row in selected],
            "metadatas": [dict(row.metadata) if row.metadata else None for row in selected],
        }

    def buscar(self, *_args, **_kwargs):
        self.search_calls += 1
        raise AssertionError("La verificacion ROOT-015 nunca debe usar similitud")

    def contar(self) -> int:
        return len(self.rows)

    def close(self) -> None:
        return None

    @classmethod
    def _matches(cls, metadata: dict[str, Any], where: dict | None) -> bool:
        if not where:
            return True
        if "$and" in where:
            return all(cls._matches(metadata, item) for item in where["$and"])
        return all(metadata.get(key) == value for key, value in where.items())


@contextmanager
def coordinator_context(
    client: FakeMutationClient | None = None,
) -> Iterator[tuple[MemoryWriteCoordinator, MemoryWriteLedger, FakeMutationClient, Path]]:
    with TemporaryDirectory(prefix="lazzaro-root015-") as temporary:
        path = Path(temporary) / "memory_write_ledger.sqlite3"
        ledger = MemoryWriteLedger(str(path))
        ledger.open()
        backend = client or FakeMutationClient()
        coordinator = MemoryWriteCoordinator(backend, ledger)
        try:
            yield coordinator, ledger, backend, path
        finally:
            coordinator.close()


def _factory(text: str = "regla estable", extra: dict[str, Any] | None = None):
    encoder = MemoryEncoder()

    def build(target_id: str, timestamp_utc: str) -> MemoryDocument:
        memory = encoder.codificar_semantica(
            regla=text,
            tipo_evento="root015",
            origen="determinista",
            extra=extra or {"fixture": "root015"},
            recuerdo_id=target_id,
            timestamp=datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00")),
        )
        return encoder.a_documento(memory)

    return build


def _create(
    coordinator: MemoryWriteCoordinator,
    key: str = "event-1",
    text: str = "regla estable",
    *,
    kind: MemoryWriteKind = MemoryWriteKind.SEMANTIC,
    logical: Any | None = None,
    legacy=None,
):
    return coordinator.create_memory(
        operation_kind=kind,
        source_identity={"event": key},
        intent={"text": text} if logical is None else logical,
        build_document=_factory(text),
        logical_identity=logical,
        legacy_candidates=legacy,
    )


def _source_row(memory_id: str) -> ExactMemoryRow:
    return ExactMemoryRow(
        id=memory_id,
        document=f"observacion {memory_id}",
        metadata={
            "tipo": "episodico",
            "tipo_evento": "observacion_visual",
            "timestamp": "2025-01-01T00:00:00+00:00",
            "impacto_emocional": 0.1,
        },
    )


def _consolidate(coordinator: MemoryWriteCoordinator, ids=("s1", "s2"), text="patron"):
    return coordinator.consolidate_memory(
        source_ids=tuple(ids),
        intent={"schema_policy_version": 1, "source_ids": sorted(ids)},
        build_document=_factory(text, {"lote_consolidacion": "root015"}),
    )


def test_01_new_write_is_created() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        receipt = _create(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.CREATED and not receipt.replayed, "La escritura nueva debe ser CREATED")
        assert_true(receipt.durable and receipt.changed_count == 1 and client.add_calls == 1, "CREATED exige efecto exacto y outcome durable")


def test_02_same_key_retry_reuses_target_without_effect() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        first = _create(coordinator)
        replay = _create(coordinator)
        assert_true(replay.target_id == first.target_id and replay.replayed, "El replay conserva target")
        assert_true(replay.status is MemoryWriteStatus.CREATED and replay.changed_count == 0 and client.add_calls == 1, "Replay no reescribe outcome ni efecto")


def test_03_restart_preserves_durable_target_and_outcome() -> None:
    client = FakeMutationClient()
    with TemporaryDirectory(prefix="lazzaro-root015-restart-") as temporary:
        path = str(Path(temporary) / "journal.sqlite3")
        first_ledger = MemoryWriteLedger(path); first_ledger.open()
        first = _create(MemoryWriteCoordinator(client, first_ledger)); first_ledger.close()
        second_ledger = MemoryWriteLedger(path); second_ledger.open()
        replay = _create(MemoryWriteCoordinator(client, second_ledger)); second_ledger.close()
        assert_true(replay.target_id == first.target_id and replay.status is MemoryWriteStatus.CREATED, "Restart conserva target/outcome")
        assert_true(client.add_calls == 1, "Restart committed no repite Chroma")


def test_04_same_key_different_intent_conflicts_before_chroma() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        _create(coordinator, text="uno")
        conflict = _create(coordinator, text="dos")
        assert_true(conflict.status is MemoryWriteStatus.FAILED and MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT in conflict.reason_codes, "Debe detectar conflicto")
        assert_true(client.add_calls == 1, "El conflicto no toca Chroma")


def test_05_distinct_episode_events_allow_identical_payload() -> None:
    client = FakeMutationClient()
    with TemporaryDirectory(prefix="lazzaro-root015-episodes-") as temporary:
        manager = MemoryManager(client, memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"))
        first = manager.guardar_experiencia_con_recibo("evento", "igual", idempotency_key={"transaction": "a"})
        second = manager.guardar_experiencia_con_recibo("evento", "igual", idempotency_key={"transaction": "b"})
        manager.close()
        assert_true(first.target_id != second.target_id and len(client.rows) == 2, "Contenido igual no es identidad episodica")


def test_06_backend_failure_with_absence_is_safe_and_retryable() -> None:
    client = FakeMutationClient(); client.add_mode = "false_before"
    with coordinator_context(client) as (coordinator, ledger, _client, _path):
        receipt = _create(coordinator)
        operation = ledger.get_operation(receipt.operation_id)
        assert_true(receipt.status is MemoryWriteStatus.FAILED and not receipt.durable, "Ausencia probada no finge CREATED")
        assert_true(operation is not None and operation.state is MemoryWriteState.INTENT_RECORDED, "Fallo transitorio conserva intento reanudable")


def test_07_may_have_written_exact_match_confirms_created() -> None:
    client = FakeMutationClient(); client.add_mode = "write_then_raise"
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _create(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.CREATED and receipt.durable, "Exact match manda sobre excepcion")


def test_08_may_have_written_unverifiable_is_ambiguous() -> None:
    client = FakeMutationClient(); client.add_mode = "write_then_raise"; client.fail_lookup_calls = {2}
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _create(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and receipt.reconciliation_required, "May-have sin verificacion es ambiguo")


def test_09_ambiguous_retry_reuses_same_target() -> None:
    client = FakeMutationClient(); client.add_mode = "write_then_raise"; client.fail_lookup_calls = {2}
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        first = _create(coordinator)
        client.fail_lookup_calls.clear(); client.add_mode = "success"
        replay = _create(coordinator)
        assert_true(first.target_id == replay.target_id and replay.status is MemoryWriteStatus.CREATED, "Retry ambiguo reconcilia mismo target")


def test_10_ambiguous_retry_allocates_no_second_operation() -> None:
    client = FakeMutationClient(); client.fail_lookup_calls = {2}
    with coordinator_context(client) as (coordinator, ledger, _client, _path):
        first = _create(coordinator); client.fail_lookup_calls.clear(); second = _create(coordinator)
        count = ledger._require_connection().execute("SELECT COUNT(*) FROM memory_write_operations").fetchone()[0]
        assert_true(first.operation_id == second.operation_id and count == 1, "Retry no asigna segundo UUID")


def test_11_same_key_two_threads_has_one_effect() -> None:
    client = FakeMutationClient(); client.add_entered = threading.Event(); client.release_add = threading.Event()
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        start = threading.Barrier(3); results = []
        def run():
            start.wait(timeout=5); results.append(_create(coordinator))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        start.wait(timeout=5); assert_true(client.add_entered.wait(timeout=5), "Primer add debe entrar"); client.release_add.set()
        for thread in threads: thread.join(timeout=5)
        assert_true(all(not thread.is_alive() for thread in threads), "Threads deben terminar")
        assert_true(client.add_calls == 1 and len({item.target_id for item in results}) == 1, "Una key produce un efecto")


def test_12_same_alias_two_threads_has_one_logical_effect() -> None:
    client = FakeMutationClient()
    with TemporaryDirectory(prefix="lazzaro-root015-alias-") as temporary:
        manager = MemoryManager(client, memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"))
        candidate = LearnedAliasCandidate("Tree_01", "arbolito", 0.98, approved=True)
        start = threading.Barrier(3); results = []
        def run(): start.wait(timeout=5); results.append(manager.guardar_alias_aprendido(candidate))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        start.wait(timeout=5)
        for thread in threads: thread.join(timeout=5)
        manager.close()
        assert_true(client.add_calls == 1 and len(client.rows) == 1, "Alias logical key arbitra durablemente")


def test_13_same_relational_fact_two_threads_has_one_effect() -> None:
    client = FakeMutationClient()
    with TemporaryDirectory(prefix="lazzaro-root015-rel-") as temporary:
        manager = MemoryManager(client, memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"))
        start = threading.Barrier(3)
        def run():
            start.wait(timeout=5)
            manager._registrar_hechos_conversacionales("mi nombre es Perro", metadata={"id_transaccion": "tx-rel"})
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        start.wait(timeout=5)
        for thread in threads: thread.join(timeout=5)
        manager.close()
        assert_true(client.add_calls == 1 and len(client.rows) == 1, "Hecho relacional concurrente no se duplica")


def test_14_same_affective_tuple_two_threads_has_one_effect() -> None:
    client = FakeMutationClient()
    with TemporaryDirectory(prefix="lazzaro-root015-affect-") as temporary:
        manager = MemoryManager(client, memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"))
        start = threading.Barrier(3)
        def run():
            start.wait(timeout=5)
            manager.guardar_memoria_afectiva_espacial(object_id="Tree", object_class="tree", affect="calma", weight=.8, source="event", confirmed=True)
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        start.wait(timeout=5)
        for thread in threads: thread.join(timeout=5)
        manager.close()
        assert_true(client.add_calls == 1 and len(client.rows) == 1, "Tupla afectiva usa identidad atomica")


def test_15_restart_preserves_target_timestamp() -> None:
    with TemporaryDirectory(prefix="lazzaro-root015-time-") as temporary:
        path = str(Path(temporary) / "j.sqlite3"); ledger = MemoryWriteLedger(path); ledger.open()
        key = MemoryWriteCoordinator.derive_idempotency_key(MemoryWriteKind.SEMANTIC, {"event": "r1"})
        fp = MemoryWriteCoordinator.intent_fingerprint(MemoryWriteKind.SEMANTIC, {"text": "x"})
        claim = ledger.claim(operation_kind=MemoryWriteKind.SEMANTIC, idempotency_key=key, intent_fingerprint=fp)
        before = (claim.operation.target_id, claim.operation.target_timestamp_utc); ledger.close()
        reopened = MemoryWriteLedger(path); reopened.open(); after = reopened.get_by_key(key); reopened.close()
        assert_true(after is not None and before == (after.target_id, after.target_timestamp_utc), "Target y timestamp son durables")


def test_16_sqlite_unique_constraint_owns_logical_identity() -> None:
    with coordinator_context() as (_coordinator, ledger, _client, _path):
        first = ledger.claim(operation_kind=MemoryWriteKind.ALIAS, idempotency_key="k1", intent_fingerprint="i", logical_key="logical")
        second = ledger.claim(operation_kind=MemoryWriteKind.ALIAS, idempotency_key="k2", intent_fingerprint="i", logical_key="logical")
        assert_true(first.operation.operation_id == second.operation.operation_id and second.logical_adoption, "UNIQUE converge identidades")


def test_17_journal_claim_failure_has_zero_chroma_calls() -> None:
    class FailLedger:
        def get_by_key(self, _key): return None
        def claim(self, **_kwargs): raise sqlite3.OperationalError("no journal")
    client = FakeMutationClient(); coordinator = MemoryWriteCoordinator(client, FailLedger())
    receipt = _create(coordinator)
    assert_true(receipt.status is MemoryWriteStatus.FAILED and MemoryWriteIssueCode.JOURNAL_FAILURE in receipt.reason_codes, "Claim failure es tipado")
    assert_true(client.add_calls == 0, "Sin claim durable no hay Chroma")


def test_18_verified_effect_finalization_failure_is_ambiguous() -> None:
    class FinalFailLedger(MemoryWriteLedger):
        def transition(self, operation_id, **kwargs):
            if kwargs.get("state") is MemoryWriteState.COMMITTED:
                raise sqlite3.OperationalError("finalization failed")
            return super().transition(operation_id, **kwargs)
    with TemporaryDirectory(prefix="lazzaro-root015-final-") as temporary:
        ledger = FinalFailLedger(str(Path(temporary) / "j.sqlite3")); ledger.open(); client = FakeMutationClient()
        receipt = _create(MemoryWriteCoordinator(client, ledger)); ledger.close()
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and receipt.reconciliation_required, "Efecto verificado no puede ser FAILED")


def test_19_same_payload_exact_verification_commits() -> None:
    with coordinator_context() as (coordinator, ledger, client, _path):
        key = coordinator.derive_idempotency_key(MemoryWriteKind.SEMANTIC, {"event": "exact"})
        fp = coordinator.intent_fingerprint(MemoryWriteKind.SEMANTIC, {"text": "same"})
        claim = ledger.claim(operation_kind=MemoryWriteKind.SEMANTIC, idempotency_key=key, intent_fingerprint=fp)
        document = _factory("same")(claim.operation.target_id, claim.operation.target_timestamp_utc)
        ledger.set_expected_fingerprint(claim.operation.operation_id, coordinator.effect_fingerprint(document))
        client.rows[document.id] = ExactMemoryRow(document.id, document.contenido, document.metadata)
        receipt = _create(coordinator, key="exact", text="same")
        assert_true(receipt.status is MemoryWriteStatus.CREATED and client.add_calls == 0, "Target exacto se reconcilia sin add")


def test_20_different_target_payload_is_mismatch() -> None:
    with coordinator_context() as (coordinator, ledger, client, _path):
        key = coordinator.derive_idempotency_key(MemoryWriteKind.SEMANTIC, {"event": "mismatch"})
        fp = coordinator.intent_fingerprint(MemoryWriteKind.SEMANTIC, {"text": "wanted"})
        claim = ledger.claim(operation_kind=MemoryWriteKind.SEMANTIC, idempotency_key=key, intent_fingerprint=fp)
        wanted = _factory("wanted")(claim.operation.target_id, claim.operation.target_timestamp_utc)
        ledger.set_expected_fingerprint(claim.operation.operation_id, coordinator.effect_fingerprint(wanted))
        client.rows[wanted.id] = ExactMemoryRow(wanted.id, "different", wanted.metadata)
        receipt = _create(coordinator, key="mismatch", text="wanted")
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and MemoryWriteIssueCode.TARGET_MISMATCH in receipt.reason_codes, "No sobrescribe target distinto")


def test_21_delete_existing_counts_are_verified() -> None:
    client = FakeMutationClient(); client.rows["d"] = _source_row("d")
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = coordinator.delete_memories(source_identity="delete-1", ids=("d",))
        assert_true((receipt.matched_count, receipt.changed_count) == (1, 1), "DELETE cuenta reads exactos")


def test_22_delete_unknown_has_zero_counts() -> None:
    with coordinator_context() as (coordinator, _ledger, _client, _path):
        receipt = coordinator.delete_memories(source_identity="delete-unknown", ids=("missing",))
        assert_true(receipt.status is MemoryWriteStatus.ALREADY_EXISTS and (receipt.matched_count, receipt.changed_count) == (0, 0), "Unknown no finge cambio")


def test_23_repeat_delete_is_idempotently_satisfied() -> None:
    client = FakeMutationClient(); client.rows["d"] = _source_row("d")
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        coordinator.delete_memories(source_identity="delete-repeat", ids=("d",))
        replay = coordinator.delete_memories(source_identity="delete-repeat", ids=("d",))
        assert_true(replay.replayed and replay.changed_count == 0 and len(client.delete_calls) == 1, "Replay delete no cambia filas")


def test_24_update_existing_changed_is_verified() -> None:
    client = FakeMutationClient(); client.rows["u"] = ExactMemoryRow("u", "doc", {"a": 1})
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = coordinator.update_memory_metadata(source_identity="update-1", target_id="u", metadata={"a": 2})
        assert_true((receipt.matched_count, receipt.changed_count) == (1, 1) and receipt.status is MemoryWriteStatus.CREATED, "UPDATE verifica postcondicion")


def test_25_update_unknown_has_no_fake_change() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        receipt = coordinator.update_memory_metadata(source_identity="update-missing", target_id="missing", metadata={"a": 2})
        assert_true(receipt.status is MemoryWriteStatus.FAILED and receipt.changed_count == 0 and client.update_calls == 0, "UPDATE unknown no muta")


def test_26_primary_success_survives_maintenance_failure() -> None:
    client = FakeMutationClient()
    with TemporaryDirectory(prefix="lazzaro-root015-maint-") as temporary:
        manager = MemoryManager(client, memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"))
        manager._programar_consolidacion_si_aplica = lambda *_args: (_ for _ in ()).throw(RuntimeError("CANARY_COGNITIVE_PAYLOAD"))
        receipt = manager.guardar_experiencia_con_recibo("observacion_visual", "narrativa", idempotency_key="maintenance")
        manager.close()
        assert_true(receipt.status is MemoryWriteStatus.CREATED and receipt.durable, "Mantenimiento no revierte primario")


def test_27_primary_failure_does_not_schedule_maintenance() -> None:
    client = FakeMutationClient(); client.add_mode = "false_before"; calls = []
    with TemporaryDirectory(prefix="lazzaro-root015-no-maint-") as temporary:
        manager = MemoryManager(client, memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"))
        manager._programar_consolidacion_si_aplica = lambda *_args: calls.append(True)
        receipt = manager.guardar_experiencia_con_recibo("observacion_visual", "narrativa", idempotency_key="no-maint")
        manager.close()
        assert_true(receipt.status is MemoryWriteStatus.FAILED and not calls, "Primario fallido no agenda mantenimiento")


def test_28_replacement_is_verified_before_delete() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")})
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _consolidate(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.CREATED and client.events.index("delete") > client.events.index("add"), "Replacement precede cleanup")


def test_29_replacement_failure_deletes_zero_sources() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")}); client.add_mode = "false_before"
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _consolidate(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.FAILED and not client.delete_calls, "Sin replacement verificado no hay delete")


def test_30_cleanup_failure_requires_reconciliation() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")}); client.delete_mode = "raise_before"
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _consolidate(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and receipt.reconciliation_required, "Cleanup fallido queda representado")


def test_31_partial_delete_tracks_remaining_sources() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2", "s3")}); client.delete_mode = "partial_one"
    with coordinator_context(client) as (coordinator, ledger, _client, _path):
        receipt = _consolidate(coordinator, ids=("s1", "s2", "s3")); operation = ledger.get_operation(receipt.operation_id)
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and operation is not None and len(operation.remaining_source_ids) == 2, "Journal conserva remaining exactos")


def test_32_same_consolidation_batch_reuses_target() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")})
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        first = _consolidate(coordinator); second = _consolidate(coordinator)
        assert_true(first.target_id == second.target_id and client.add_calls == 1, "Mismo source set conserva replacement")


def test_33_concurrent_consolidation_produces_one_rule() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")})
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        start = threading.Barrier(3); results = []
        def run(): start.wait(timeout=5); results.append(_consolidate(coordinator))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        start.wait(timeout=5)
        for thread in threads: thread.join(timeout=5)
        assert_true(client.add_calls == 1 and len({item.target_id for item in results}) == 1, "Consolidacion concurrente converge")


def test_34_restart_consolidation_uses_same_operation() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")}); client.delete_mode = "raise_before"
    with TemporaryDirectory(prefix="lazzaro-root015-cons-restart-") as temporary:
        path = str(Path(temporary) / "j.sqlite3"); one = MemoryWriteLedger(path); one.open(); first = _consolidate(MemoryWriteCoordinator(client, one)); one.close()
        client.delete_mode = "success"; two = MemoryWriteLedger(path); two.open(); second = _consolidate(MemoryWriteCoordinator(client, two)); two.close()
        assert_true(first.operation_id == second.operation_id and first.target_id == second.target_id and second.status is MemoryWriteStatus.CREATED, "Restart reanuda misma consolidacion")


def test_35_no_source_loss_before_replacement_verification() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")}); client.fail_lookup_calls = {2}
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _consolidate(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and not client.delete_calls and all(item in client.rows for item in ("s1", "s2")), "Unverified replacement preserva sources")


def test_36_committed_consolidation_has_no_duplicate_rule() -> None:
    client = FakeMutationClient(); client.rows.update({item: _source_row(item) for item in ("s1", "s2")})
    with TemporaryDirectory(prefix="lazzaro-root015-manager-cons-") as temporary:
        manager = MemoryManager(
            client,
            consolidacion_umbral=2,
            consolidacion_antiguedad_minutos=5,
            consolidacion_importancia_maxima=1.0,
            memory_write_ledger_path=str(Path(temporary) / "j.sqlite3"),
        )
        first = manager.consolidar_memorias_si_hace_falta()
        manager.consolidar_memorias_si_hace_falta()
        manager.close()
        assert_true(first is not None and first.status is MemoryWriteStatus.CREATED, "Manager conecta saga de consolidacion")
        assert_true(client.add_calls == 1 and len(client.rows) == 1, "Solo existe una regla semantica comprometida")


def test_37_created_ids_predicate_excludes_replay_and_ambiguous() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        first = _create(coordinator); replay = _create(coordinator)
        client.fail_lookup_calls = {client.lookup_calls + 2}; ambiguous = _create(coordinator, key="other")
        created = [item.target_id for item in (first, replay, ambiguous) if item.status is MemoryWriteStatus.CREATED and item.durable and item.changed_count > 0]
        assert_true(created == [first.target_id], "AuditTrace solo proyecta efecto nuevo confirmado")


def test_38_root014_retrieval_contract_remains_unchanged() -> None:
    assert_true({item.value for item in RetrievalHealth} == {"OK", "OK_EMPTY", "PARTIAL", "FAILED"}, "ROOT-014 conserva RetrievalHealth")


def test_39_root013_effect_fingerprint_is_canonical() -> None:
    first = MemoryDocument("id", "doc", {"b": 2, "a": 1, "tipo": "semantico"})
    second = MemoryDocument("id", "doc", {"tipo": "semantico", "a": 1, "b": 2})
    assert_true(MemoryWriteCoordinator.effect_fingerprint(first) == MemoryWriteCoordinator.effect_fingerprint(second), "Fingerprint reutiliza representacion canonica")


def test_40_root039_none_empty_semantics_stay_distinct_at_contract_boundary() -> None:
    valid_none, normalized_none, _ = ChromaClient._normalizar_metadatos_add(None, 1)
    valid_empty, normalized_empty, _ = ChromaClient._normalizar_metadatos_add({}, 1)
    assert_true(valid_none and valid_empty and normalized_none is None and normalized_empty == [None], "ROOT-039 permanece intacto")


def test_41_r1_intent_survives_restart_without_payload() -> None:
    with TemporaryDirectory(prefix="lazzaro-root015-r1-") as temporary:
        path = str(Path(temporary) / "j.sqlite3"); ledger = MemoryWriteLedger(path); ledger.open()
        key = MemoryWriteCoordinator.derive_idempotency_key(MemoryWriteKind.SEMANTIC, "r1")
        claim = ledger.claim(operation_kind=MemoryWriteKind.SEMANTIC, idempotency_key=key, intent_fingerprint=MemoryWriteCoordinator.intent_fingerprint(MemoryWriteKind.SEMANTIC, "intent")); target = claim.operation.target_id; ledger.close()
        reopened = MemoryWriteLedger(path); reopened.open(); coordinator = MemoryWriteCoordinator(FakeMutationClient(), reopened); receipts = coordinator.reconcile_pending(); operation = reopened.get_by_key(key); reopened.close()
        assert_true(operation is not None and operation.target_id == target and operation.state is MemoryWriteState.INTENT_RECORDED, "R1 sigue reanudable")
        assert_true(receipts and receipts[0].status is MemoryWriteStatus.AMBIGUOUS, "Recovery reporta pendiente sin envenenar")
        assert_true(receipts[0].idempotency_key == key, "Recovery conserva la key publica de la operacion")


def test_42_r1_later_caller_resumes_same_target() -> None:
    client = FakeMutationClient()
    with coordinator_context(client) as (coordinator, ledger, _client, _path):
        key = coordinator.derive_idempotency_key(MemoryWriteKind.SEMANTIC, {"event": "later"})
        claim = ledger.claim(operation_kind=MemoryWriteKind.SEMANTIC, idempotency_key=key, intent_fingerprint=coordinator.intent_fingerprint(MemoryWriteKind.SEMANTIC, {"text": "payload"}))
        receipt = _create(coordinator, key="later", text="payload")
        assert_true(receipt.target_id == claim.operation.target_id and receipt.status is MemoryWriteStatus.CREATED, "Caller reanuda target R1")


def test_43_recovery_without_payload_never_writes() -> None:
    client = FakeMutationClient()
    with coordinator_context(client) as (coordinator, ledger, _client, _path):
        ledger.claim(operation_kind=MemoryWriteKind.EPISODIC, idempotency_key="raw-key", intent_fingerprint="intent")
        coordinator.reconcile_pending()
        assert_true(client.add_calls == 0 and not client.rows, "Un hash no reconstruye payload")


def test_44_replay_does_not_mutate_created_outcome() -> None:
    with coordinator_context() as (coordinator, ledger, _client, _path):
        first = _create(coordinator); _create(coordinator); operation = ledger.get_operation(first.operation_id)
        assert_true(operation is not None and operation.final_status is MemoryWriteStatus.CREATED, "Replay no cambia CREATED a ALREADY_EXISTS")


def test_45_legacy_adoption_is_distinct_from_created() -> None:
    client = FakeMutationClient(); row = ExactMemoryRow("legacy", "legacy doc", {"tipo": "semantico", "timestamp": "2025-01-01T00:00:00+00:00"}); client.rows[row.id] = row
    legacy = lambda: ExactLookupReceipt(status=ExactLookupStatus.PRESENT, requested_ids=(row.id,), rows=(row,))
    with coordinator_context(client) as (coordinator, _ledger, _client, _path):
        receipt = _create(coordinator, key="legacy", logical={"business": "legacy"}, legacy=legacy)
        assert_true(receipt.status is MemoryWriteStatus.ALREADY_EXISTS and MemoryWriteIssueCode.LEGACY_EFFECT_ADOPTED in receipt.reason_codes and client.add_calls == 0, "Legacy adoption no finge CREATED")


def test_46_logical_uniqueness_is_not_applied_to_episodes() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        _create(coordinator, key="e1", kind=MemoryWriteKind.EPISODIC); _create(coordinator, key="e2", kind=MemoryWriteKind.EPISODIC)
        assert_true(len(client.rows) == 2, "Episodios no usan content hash global")


def test_47_domain_version_can_define_distinct_logical_effects() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        _create(coordinator, key="v1", logical={"object": "x", "policy_version": 1}); _create(coordinator, key="v2", logical={"object": "x", "policy_version": 2})
        assert_true(len(client.rows) == 2, "Version de dominio evita first-writer-wins accidental")


def test_48_no_sqlite_transaction_is_open_during_chroma_calls() -> None:
    client = FakeMutationClient()
    with coordinator_context(client) as (coordinator, ledger, _client, _path):
        client.transaction_probe = ledger; _create(coordinator)
        assert_true(not ledger.in_transaction, "Cada boundary Chroma queda fuera de BEGIN")


def test_49_import_or_constructor_without_open_creates_no_db() -> None:
    with TemporaryDirectory(prefix="lazzaro-root015-import-") as temporary:
        path = Path(temporary) / "not_created.sqlite3"; MemoryWriteLedger(str(path))
        assert_true(not path.exists(), "El ledger requiere open explicito")


def test_50_temporary_ledger_artifacts_disappear() -> None:
    temporary = TemporaryDirectory(prefix="lazzaro-root015-clean-"); root = Path(temporary.name); path = root / "j.sqlite3"
    ledger = MemoryWriteLedger(str(path)); ledger.open(); ledger.close(); assert_true(path.exists(), "Fixture crea journal temporal"); temporary.cleanup()
    assert_true(not root.exists(), "TemporaryDirectory elimina DB y sidecars")


def test_51_runtime_journal_sidecars_are_ignored() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert_true(all(name in text for name in ("memory_write_ledger.sqlite3", "memory_write_ledger.sqlite3-wal", "memory_write_ledger.sqlite3-shm")), "Gitignore cubre journal acotado")


def test_52_finalization_failure_never_reports_failed() -> None:
    class FinalFailLedger(MemoryWriteLedger):
        def transition(self, operation_id, **kwargs):
            if kwargs.get("state") is MemoryWriteState.COMMITTED: raise sqlite3.OperationalError("final")
            return super().transition(operation_id, **kwargs)
    with TemporaryDirectory(prefix="lazzaro-root015-not-failed-") as temporary:
        ledger = FinalFailLedger(str(Path(temporary) / "j.sqlite3")); ledger.open(); receipt = _create(MemoryWriteCoordinator(FakeMutationClient(), ledger)); ledger.close()
        assert_true(receipt.status is MemoryWriteStatus.AMBIGUOUS and receipt.status is not MemoryWriteStatus.FAILED, "Verified effect con journal failure es ambiguo")


def test_53_reason_codes_are_stable_and_deduplicated() -> None:
    merged = merge_write_issue_codes((MemoryWriteIssueCode.BACKEND_FAILURE, MemoryWriteIssueCode.JOURNAL_FAILURE), (MemoryWriteIssueCode.BACKEND_FAILURE,))
    assert_true(merged == (MemoryWriteIssueCode.BACKEND_FAILURE, MemoryWriteIssueCode.JOURNAL_FAILURE), "Reason codes conservan primera aparicion")


def test_54_logs_do_not_contain_cognitive_payload() -> None:
    client = FakeMutationClient(); client.add_mode = "raise_before"; records: list[str] = []
    class Capture(logging.Handler):
        def emit(self, record): records.append(record.getMessage())
    handler = Capture(); logging.getLogger("memory.memory_write_coordinator").addHandler(handler)
    try:
        with coordinator_context(client) as (coordinator, _ledger, _client, _path): _create(coordinator, text="SECRET_NARRATIVE")
    finally:
        logging.getLogger("memory.memory_write_coordinator").removeHandler(handler)
    joined = "\n".join(records)
    assert_true("SECRET_NARRATIVE" not in joined and "CANARY_COGNITIVE_PAYLOAD" not in joined, "Logs solo exponen tipos/codigos")


def test_55_exact_verification_never_uses_vector_search() -> None:
    with coordinator_context() as (coordinator, _ledger, client, _path):
        receipt = _create(coordinator)
        assert_true(receipt.status is MemoryWriteStatus.CREATED and client.search_calls == 0 and client.lookup_calls >= 2, "Verificacion usa get exacto")


def main() -> None:
    tests = sorted(
        (name, value)
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    )
    for name, test in tests:
        test()
        print(f"  PASS {name}")
    print(f"[OK] ROOT-015: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
