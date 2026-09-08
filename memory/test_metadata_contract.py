"""Contrato permanente de metadata cognitiva y transporte Chroma (ROOT-039)."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import gc
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from ai_engine.oracle_memory import OracleMemory
from memory.chroma_client import ChromaClient
from memory.memory_encoder import MemoryEncoder
from memory.memory_types import MemoryDocument, MemoryMetadata
from memory.test_chromadb_flow import test_eliminacion as _historical_delete_test

try:
    from chromadb.api.shared_system_client import SharedSystemClient
except Exception:  # pragma: no cover - compatibilidad con versiones antiguas
    SharedSystemClient = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_CHROMA = (ROOT / "lazzaro_data").resolve()
_TEMP_PATHS: list[Path] = []


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeCollection:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.get_result: dict[str, Any] = {
            "ids": [],
            "documents": [],
            "metadatas": [],
        }
        self.query_result: dict[str, Any] = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

    def add(self, **kwargs: Any) -> None:
        self.add_calls.append(deepcopy(kwargs))

    def update(self, **kwargs: Any) -> None:
        self.update_calls.append(deepcopy(kwargs))

    def get(self, **_kwargs: Any) -> dict[str, Any]:
        return self.get_result

    def query(self, **_kwargs: Any) -> dict[str, Any]:
        return self.query_result


def _fake_client(collection: FakeCollection | None = None) -> tuple[ChromaClient, FakeCollection]:
    fake = collection or FakeCollection()
    client = object.__new__(ChromaClient)
    client._collection = fake
    client._lock = threading.RLock()
    client.collection_name = "root039_fake"
    return client, fake


class OracleRecordingClient:
    disponible = True

    def __init__(self) -> None:
        self.write: dict[str, Any] | None = None

    def agregar(self, **kwargs: Any) -> bool:
        self.write = deepcopy(kwargs)
        return True


@lru_cache(maxsize=1)
def _backend_characterization() -> dict[str, Any]:
    cases = (
        ("none", None),
        ("empty_list", []),
        ("list_none", [None]),
        ("list_empty", [{}]),
        ("list_present", [{"kind": "synthetic"}]),
    )
    report: dict[str, Any] = {
        "version": version("chromadb"),
        "add": {},
        "update": {},
    }
    with TemporaryDirectory(prefix="root039-backend-") as directory:
        temporary_path = Path(directory).resolve()
        _TEMP_PATHS.append(temporary_path)
        assert_true(temporary_path != ACTIVE_CHROMA, "El fixture no puede usar la Chroma activa.")
        assert_true(ACTIVE_CHROMA not in temporary_path.parents, "El fixture debe quedar aislado.")
        client = chromadb.PersistentClient(
            path=directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        try:
            for index, (name, value) in enumerate(cases):
                collection = client.get_or_create_collection(name=f"root039_add_{index}")
                before = deepcopy(value)
                row: dict[str, Any] = {}
                try:
                    collection.add(
                        ids=[f"add-{index}"],
                        embeddings=[[0.1, 0.2, 0.3]],
                        documents=["synthetic"],
                        metadatas=value,
                    )
                    row["accepted"] = True
                    row["get"] = collection.get(include=["metadatas"]).get("metadatas")
                    row["query"] = collection.query(
                        query_embeddings=[[0.1, 0.2, 0.3]],
                        n_results=1,
                        include=["metadatas"],
                    ).get("metadatas")
                except Exception as exc:
                    row["accepted"] = False
                    row["error_type"] = type(exc).__name__
                row["caller_unchanged"] = value == before
                report["add"][name] = row

            for index, (name, value) in enumerate(cases):
                collection = client.get_or_create_collection(name=f"root039_update_{index}")
                record_id = f"update-{index}"
                collection.add(
                    ids=[record_id],
                    embeddings=[[0.1, 0.2, 0.3]],
                    documents=["synthetic"],
                    metadatas=[{"seed": "base"}],
                )
                before = deepcopy(value)
                row = {}
                try:
                    collection.update(ids=[record_id], metadatas=value)
                    row["accepted"] = True
                    row["get"] = collection.get(
                        ids=[record_id],
                        include=["metadatas"],
                    ).get("metadatas")
                except Exception as exc:
                    row["accepted"] = False
                    row["error_type"] = type(exc).__name__
                    row["get"] = collection.get(
                        ids=[record_id],
                        include=["metadatas"],
                    ).get("metadatas")
                row["caller_unchanged"] = value == before
                report["update"][name] = row
        finally:
            try:
                client._system.stop()
            except Exception:
                pass
            if SharedSystemClient is not None:
                try:
                    SharedSystemClient.clear_system_cache()
                except Exception:
                    pass
            del client
            gc.collect()
    return report


def test_01_memory_metadata_empty_extra_is_valid() -> None:
    metadata = MemoryMetadata("synthetic", 0.0, 0.5, extra={})
    assert_true(metadata.extra == {}, "Empty extra debe representar cero extensiones.")


def test_02_episodic_encoder_empty_extra_has_total_metadata() -> None:
    memory = MemoryEncoder.codificar_episodio("synthetic", "episode", extra={})
    document = MemoryEncoder.a_documento(memory)
    assert_true(bool(document.metadata), "El documento episodico debe incluir metadata central.")
    assert_true(document.metadata["tipo"] == "episodico", "Debe conservar el tipo central.")


def test_03_semantic_encoder_empty_extra_has_total_metadata() -> None:
    memory = MemoryEncoder.codificar_semantica("rule", extra={})
    document = MemoryEncoder.a_documento(memory)
    assert_true(bool(document.metadata), "El documento semantico debe incluir metadata central.")
    assert_true(document.metadata["tipo"] == "semantico", "Debe conservar el tipo central.")


def test_04_memory_document_rejects_empty_metadata() -> None:
    try:
        MemoryDocument("empty", "synthetic", {})
    except ValueError:
        return
    raise AssertionError("MemoryDocument debe rechazar metadata vacia.")


def test_05_memory_document_rejects_none_metadata() -> None:
    try:
        MemoryDocument("none", "synthetic", None)  # type: ignore[arg-type]
    except TypeError:
        return
    raise AssertionError("MemoryDocument debe rechazar metadata None.")


def test_06_memory_document_accepts_present_metadata() -> None:
    source = {"kind": "synthetic"}
    document = MemoryDocument("valid", "synthetic", source)
    assert_true(document.metadata == source, "Metadata presente debe conservarse.")


def test_07_add_none_preserves_global_absence() -> None:
    client, fake = _fake_client()
    assert_true(client.agregar(["doc"], None, ["id"]), "ADD None debe ser valido.")
    assert_true(fake.add_calls[0]["metadatas"] is None, "Backend debe recibir None.")


def test_08_add_list_none_preserves_row_absence() -> None:
    client, fake = _fake_client()
    assert_true(client.agregar(["doc"], [None], ["id"]), "ADD [None] debe ser valido.")
    assert_true(fake.add_calls[0]["metadatas"] == [None], "Backend debe recibir [None].")


def test_09_add_list_empty_canonicalizes_to_none() -> None:
    client, fake = _fake_client()
    assert_true(client.agregar(["doc"], [{}], ["id"]), "ADD [{}] debe ser alias valido.")
    assert_true(fake.add_calls[0]["metadatas"] == [None], "{} debe canonicalizarse a None.")


def test_10_add_present_metadata_preserves_equivalent_copy() -> None:
    source = {"kind": "synthetic"}
    client, fake = _fake_client()
    assert_true(client.agregar(["doc"], [source], ["id"]), "Metadata presente debe ser valida.")
    captured = fake.add_calls[0]["metadatas"][0]
    assert_true(captured == source and captured is not source, "Backend debe recibir una copia equivalente.")


def test_11_add_does_not_mutate_single_dict() -> None:
    source = {"kind": "synthetic"}
    before = deepcopy(source)
    client, _ = _fake_client()
    assert_true(client.agregar(["doc"], source, ["id"]), "El dict singular debe ser valido.")
    assert_true(source == before, "ADD no debe mutar el dict del caller.")


def test_12_add_does_not_mutate_list_or_empty_dict() -> None:
    source: list[dict[str, Any]] = [{}]
    before = deepcopy(source)
    client, _ = _fake_client()
    assert_true(client.agregar(["doc"], source, ["id"]), "El alias vacio debe ser valido.")
    assert_true(source == before, "ADD no debe mutar lista ni dict vacio.")


def test_13_update_none_is_rejected_before_backend() -> None:
    client, fake = _fake_client()
    assert_true(not client.actualizar_metadatos(["id"], None), "UPDATE None debe fallar.")
    assert_true(fake.update_calls == [], "El backend no debe recibir UPDATE None.")


def test_14_update_empty_list_is_rejected_before_backend() -> None:
    client, fake = _fake_client()
    assert_true(not client.actualizar_metadatos(["id"], []), "UPDATE [] debe fallar.")
    assert_true(fake.update_calls == [], "El backend no debe recibir UPDATE [].")


def test_15_update_list_none_is_rejected_before_backend() -> None:
    client, fake = _fake_client()
    assert_true(
        not client.actualizar_metadatos(["id"], [None]),  # type: ignore[list-item]
        "UPDATE [None] debe fallar.",
    )
    assert_true(fake.update_calls == [], "El backend no debe recibir UPDATE [None].")


def test_16_update_list_empty_is_rejected_before_backend() -> None:
    client, fake = _fake_client()
    assert_true(not client.actualizar_metadatos(["id"], [{}]), "UPDATE [{}] debe fallar.")
    assert_true(fake.update_calls == [], "El backend no debe recibir UPDATE [{}].")


def test_17_update_present_metadata_calls_backend() -> None:
    client, fake = _fake_client()
    assert_true(
        client.actualizar_metadatos(["id"], [{"kind": "synthetic"}]),
        "UPDATE presente debe ser valido.",
    )
    assert_true(len(fake.update_calls) == 1, "El backend debe recibir exactamente un UPDATE.")


def test_18_update_present_metadata_does_not_mutate_caller() -> None:
    source = [{"kind": "synthetic"}]
    before = deepcopy(source)
    client, fake = _fake_client()
    assert_true(client.actualizar_metadatos(["id"], source), "UPDATE debe ser valido.")
    assert_true(source == before, "UPDATE no debe mutar al caller.")
    assert_true(fake.update_calls[0]["metadatas"] is not source, "Backend debe recibir otra lista.")


def test_19_list_read_preserves_none_absence() -> None:
    fake = FakeCollection()
    fake.get_result = {"ids": ["a"], "documents": ["doc"], "metadatas": [None]}
    client, _ = _fake_client(fake)
    result = client.listar()
    assert_true(result is not None and result["metadatas"] == [None], "LIST debe preservar None.")


def test_20_list_read_preserves_present_dict() -> None:
    fake = FakeCollection()
    source = {"kind": "synthetic"}
    fake.get_result = {"ids": ["a"], "documents": ["doc"], "metadatas": [source]}
    client, _ = _fake_client(fake)
    result = client.listar()
    assert_true(result is not None and result["metadatas"] == [source], "LIST debe preservar dict.")
    assert_true(result["metadatas"][0] is not source, "LIST debe devolver una copia de metadata.")


def test_21_query_read_preserves_nested_none_shape() -> None:
    fake = FakeCollection()
    fake.query_result = {
        "ids": [["a"]],
        "documents": [["doc"]],
        "metadatas": [[None]],
        "distances": [[0.0]],
    }
    client, _ = _fake_client(fake)
    result = client.buscar("synthetic")
    assert_true(result is not None and result["metadatas"] == [[None]], "QUERY conserva shape.")


def test_22_query_read_preserves_nested_present_dict() -> None:
    fake = FakeCollection()
    source = {"kind": "synthetic"}
    fake.query_result = {
        "ids": [["a"]],
        "documents": [["doc"]],
        "metadatas": [[source]],
        "distances": [[0.0]],
    }
    client, _ = _fake_client(fake)
    result = client.buscar("synthetic")
    assert_true(result is not None and result["metadatas"] == [[source]], "QUERY conserva dict.")
    assert_true(result["metadatas"][0][0] is not source, "QUERY debe copiar metadata.")


def test_23_read_never_emits_empty_dict_as_absence() -> None:
    fake = FakeCollection()
    fake.get_result = {"ids": ["a"], "documents": ["doc"], "metadatas": [{}]}
    fake.query_result = {
        "ids": [["a"]],
        "documents": [["doc"]],
        "metadatas": [[{}]],
        "distances": [[0.0]],
    }
    client, _ = _fake_client(fake)
    listed = client.listar()
    queried = client.buscar("synthetic")
    assert_true(listed is not None and listed["metadatas"] == [None], "LIST {} debe ser None.")
    assert_true(queried is not None and queried["metadatas"] == [[None]], "QUERY {} debe ser None.")


def test_24_oracle_cache_keeps_nonempty_transport_metadata() -> None:
    client = OracleRecordingClient()
    oracle = object.__new__(OracleMemory)
    oracle.client = client
    assert_true(oracle.guardar_en_cache("question", "answer"), "Oracle cache debe seguir escribiendo.")
    assert_true(client.write is not None, "Oracle debe invocar al transporte.")
    metadata = client.write["metadatos"][0]
    assert_true(isinstance(metadata, dict) and bool(metadata), "Oracle debe emitir metadata no vacia.")


def test_25_historical_delete_fixture_reaches_delete() -> None:
    calls: list[list[str]] = []
    original = ChromaClient.eliminar

    def tracking_delete(self: ChromaClient, ids: list[str]) -> bool:
        calls.append(list(ids))
        return original(self, ids)

    ChromaClient.eliminar = tracking_delete
    try:
        _historical_delete_test()
    finally:
        ChromaClient.eliminar = original
    assert_true(calls == [["x", "y"]], "El test historico debe ejecutar DELETE real.")


def test_26_backend_characterization_never_uses_active_chroma() -> None:
    _backend_characterization()
    assert_true(bool(_TEMP_PATHS), "Debe existir evidencia del fixture temporal.")
    assert_true(
        all(path != ACTIVE_CHROMA and ACTIVE_CHROMA not in path.parents for path in _TEMP_PATHS),
        "Ningun fixture puede usar la Chroma activa.",
    )


def test_27_observed_chroma_version_is_1_5_9() -> None:
    assert_true(_backend_characterization()["version"] == "1.5.9", "Cambio la version caracterizada.")


def test_28_backend_add_none_is_accepted() -> None:
    row = _backend_characterization()["add"]["none"]
    assert_true(row["accepted"] and row["get"] == [None], "Backend ADD None cambio.")


def test_29_backend_add_empty_list_is_rejected() -> None:
    row = _backend_characterization()["add"]["empty_list"]
    assert_true(not row["accepted"] and row["error_type"] == "ValueError", "Backend ADD [] cambio.")


def test_30_backend_add_list_none_is_accepted() -> None:
    row = _backend_characterization()["add"]["list_none"]
    assert_true(row["accepted"] and row["query"] == [[None]], "Backend ADD [None] cambio.")


def test_31_backend_add_list_empty_is_rejected() -> None:
    row = _backend_characterization()["add"]["list_empty"]
    assert_true(not row["accepted"] and row["error_type"] == "ValueError", "Backend ADD [{}] cambio.")


def test_32_backend_add_present_is_accepted() -> None:
    row = _backend_characterization()["add"]["list_present"]
    assert_true(row["accepted"] and row["get"] == [{"kind": "synthetic"}], "Backend ADD dict cambio.")


def test_33_backend_update_none_is_successful_noop() -> None:
    row = _backend_characterization()["update"]["none"]
    assert_true(row["accepted"] and row["get"] == [{"seed": "base"}], "Backend UPDATE None cambio.")


def test_34_backend_update_empty_list_is_rejected() -> None:
    row = _backend_characterization()["update"]["empty_list"]
    assert_true(not row["accepted"] and row["error_type"] == "ValueError", "Backend UPDATE [] cambio.")


def test_35_backend_update_list_none_is_successful_noop() -> None:
    row = _backend_characterization()["update"]["list_none"]
    assert_true(row["accepted"] and row["get"] == [{"seed": "base"}], "Backend UPDATE [None] cambio.")


def test_36_backend_update_list_empty_is_rejected() -> None:
    row = _backend_characterization()["update"]["list_empty"]
    assert_true(not row["accepted"] and row["error_type"] == "ValueError", "Backend UPDATE [{}] cambio.")


def test_37_backend_update_present_merges() -> None:
    row = _backend_characterization()["update"]["list_present"]
    assert_true(
        row["accepted"] and row["get"] == [{"seed": "base", "kind": "synthetic"}],
        "Backend UPDATE dict debe conservar semantica merge.",
    )


def test_38_update_accepts_single_nonempty_dict() -> None:
    source = {"kind": "synthetic"}
    client, fake = _fake_client()
    assert_true(client.actualizar_metadatos(["id"], source), "UPDATE dict singular debe ser valido.")
    assert_true(fake.update_calls[0]["metadatas"] == [source], "Debe convertirse a batch de una fila.")


def test_39_add_accepts_single_empty_dict_as_absence() -> None:
    source: dict[str, Any] = {}
    client, fake = _fake_client()
    assert_true(client.agregar(["doc"], source, ["id"]), "ADD {} singular debe ser valido.")
    assert_true(fake.add_calls[0]["metadatas"] == [None], "ADD {} debe canonicalizarse.")


def test_40_add_accepts_single_nonempty_dict() -> None:
    source = {"kind": "synthetic"}
    client, fake = _fake_client()
    assert_true(client.agregar(["doc"], source, ["id"]), "ADD dict singular debe ser valido.")
    assert_true(fake.add_calls[0]["metadatas"] == [source], "ADD dict debe formar un batch.")


def test_41_invalid_update_log_does_not_include_raw_input() -> None:
    import logging

    client, _ = _fake_client()
    logger = logging.getLogger("memory.chroma_client")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger.addHandler(handler)
    try:
        assert_true(
            not client.actualizar_metadatos(["id"], ["PRIVATE_METADATA_CANARY"]),  # type: ignore[list-item]
            "Input invalido debe fallar.",
        )
    finally:
        logger.removeHandler(handler)
    rendered = "\n".join(record.getMessage() for record in records)
    assert_true("PRIVATE_METADATA_CANARY" not in rendered, "El log no debe volcar metadata cruda.")
    assert_true("operation=update_metadata" in rendered, "El log debe identificar la operacion.")


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-039 metadata contract: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
