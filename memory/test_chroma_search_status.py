"""ROOT-018: typed Chroma search status without touching a real collection."""

from __future__ import annotations

import sys
import threading
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.chroma_client import ChromaClient
from memory.chroma_search_types import (
    ChromaSearchFailureCodeV1,
    ChromaSearchResultV1,
    ChromaSearchStatusV1,
)


class _Collection:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def query(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _client(collection) -> ChromaClient:
    client = object.__new__(ChromaClient)
    client._collection = collection
    client._lock = threading.RLock()
    client.collection_name = "fixture"
    return client


def _payload(*, ids=None, documents=None, metadatas=None, distances=None):
    return {
        "ids": [[] if ids is None else ids],
        "documents": [[] if documents is None else documents],
        "metadatas": [[] if metadatas is None else metadatas],
        "distances": [[] if distances is None else distances],
    }


def test_01_collection_none_is_unavailable() -> None:
    result = _client(None).buscar_con_estado("q")
    assert result.status is ChromaSearchStatusV1.UNAVAILABLE
    assert result.failure_code is ChromaSearchFailureCodeV1.COLLECTION_UNAVAILABLE
    assert result.payload is None


def test_02_backend_exception_is_redacted_error() -> None:
    result = _client(_Collection(error=RuntimeError("PRIVATE_BACKEND_BODY"))).buscar_con_estado("q")
    assert result.status is ChromaSearchStatusV1.ERROR
    assert result.failure_code is ChromaSearchFailureCodeV1.BACKEND_FAILURE
    assert "PRIVATE" not in repr(result)


def test_03_malformed_envelope_is_error() -> None:
    result = _client(_Collection(result={"ids": [[]]})).buscar_con_estado("q")
    assert result.status is ChromaSearchStatusV1.ERROR
    assert result.failure_code is ChromaSearchFailureCodeV1.MALFORMED_RESPONSE


def test_04_valid_empty_is_empty() -> None:
    result = _client(_Collection(result=_payload())).buscar_con_estado("q")
    assert result.status is ChromaSearchStatusV1.EMPTY
    assert result.payload is not None


def test_05_valid_one_row_is_success() -> None:
    result = _client(
        _Collection(
            result=_payload(
                ids=["id-1"],
                documents=["q1"],
                metadatas=[{"x": 1}],
                distances=[0.2],
            )
        )
    ).buscar_con_estado("q")
    assert result.status is ChromaSearchStatusV1.SUCCESS
    assert result.payload is not None and result.payload["ids"] == [["id-1"]]


def test_06_misaligned_batches_are_rejected() -> None:
    malformed = _payload(
        ids=["a", "b"], documents=["a"], metadatas=[{}, {}], distances=[0.1, 0.2]
    )
    result = _client(_Collection(result=malformed)).buscar_con_estado("q")
    assert result.failure_code is ChromaSearchFailureCodeV1.MALFORMED_RESPONSE


def test_07_non_numeric_and_bool_distances_are_rejected() -> None:
    for distance in ("0.1", True):
        result = _client(
            _Collection(
                result=_payload(
                    ids=["a"], documents=["q"], metadatas=[{}], distances=[distance]
                )
            )
        ).buscar_con_estado("q")
        assert result.failure_code is ChromaSearchFailureCodeV1.MALFORMED_RESPONSE


def test_08_non_finite_distances_are_rejected() -> None:
    for distance in (float("nan"), float("inf"), float("-inf")):
        result = _client(
            _Collection(
                result=_payload(
                    ids=["a"], documents=["q"], metadatas=[{}], distances=[distance]
                )
            )
        ).buscar_con_estado("q")
        assert result.failure_code is ChromaSearchFailureCodeV1.MALFORMED_RESPONSE


def test_09_primitive_row_types_are_required() -> None:
    invalid_rows = (
        _payload(ids=[1], documents=["q"], metadatas=[{}], distances=[0.1]),
        _payload(ids=["a"], documents=[None], metadatas=[{}], distances=[0.1]),
        _payload(ids=["a"], documents=["q"], metadatas=[[]], distances=[0.1]),
    )
    for payload in invalid_rows:
        result = _client(_Collection(result=payload)).buscar_con_estado("q")
        assert result.failure_code is ChromaSearchFailureCodeV1.MALFORMED_RESPONSE


def test_10_contract_version_is_exact_integer_one() -> None:
    for value in (True, 1.0, "1", 0, 2):
        try:
            ChromaSearchResultV1(
                contract_version=value,
                status=ChromaSearchStatusV1.EMPTY,
                payload=_payload(),
                failure_code=None,
            )
        except ValueError:
            continue
        raise AssertionError("invalid Chroma contract version accepted")


def test_11_impossible_status_combinations_are_rejected() -> None:
    bad = (
        (ChromaSearchStatusV1.SUCCESS, None, None),
        (ChromaSearchStatusV1.SUCCESS, _payload(), None),
        (
            ChromaSearchStatusV1.EMPTY,
            _payload(ids=["a"], documents=["q"], metadatas=[{}], distances=[0.1]),
            None,
        ),
        (ChromaSearchStatusV1.EMPTY, _payload(), ChromaSearchFailureCodeV1.BACKEND_FAILURE),
        (ChromaSearchStatusV1.UNAVAILABLE, None, ChromaSearchFailureCodeV1.BACKEND_FAILURE),
        (ChromaSearchStatusV1.ERROR, _payload(), ChromaSearchFailureCodeV1.BACKEND_FAILURE),
    )
    for status, payload, failure in bad:
        try:
            ChromaSearchResultV1(1, status, payload, failure)
        except ValueError:
            continue
        raise AssertionError("impossible Chroma search result accepted")


def test_12_legacy_buscar_semantics_are_unchanged() -> None:
    success_collection = _Collection(
        result=_payload(ids=["a"], documents=["q"], metadatas=[{}], distances=[0.1])
    )
    assert _client(success_collection).buscar("q") == {
        **success_collection.result,
        "metadatas": [[None]],
    }
    assert _client(None).buscar("q") is None
    assert _client(_Collection(error=RuntimeError("x"))).buscar("q") is None


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-018 Chroma status: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
