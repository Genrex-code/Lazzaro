"""Typed, redacted result contract for internal Chroma similarity searches."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from chromadb.api.types import QueryResult


class ChromaSearchStatusV1(str, Enum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class ChromaSearchFailureCodeV1(str, Enum):
    COLLECTION_UNAVAILABLE = "COLLECTION_UNAVAILABLE"
    BACKEND_FAILURE = "BACKEND_FAILURE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"


@dataclass(frozen=True, slots=True)
class ChromaSearchResultV1:
    contract_version: int
    status: ChromaSearchStatusV1
    payload: QueryResult | None
    failure_code: ChromaSearchFailureCodeV1 | None

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("contract_version must be the exact integer 1")
        if not isinstance(self.status, ChromaSearchStatusV1):
            raise ValueError("status must be ChromaSearchStatusV1")

        row_count: int | None = None
        if isinstance(self.payload, dict):
            ids = self.payload.get("ids")
            if isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], list):
                row_count = len(ids[0])

        if self.status is ChromaSearchStatusV1.SUCCESS:
            if (
                not isinstance(self.payload, dict)
                or self.failure_code is not None
                or row_count is None
                or row_count == 0
            ):
                raise ValueError("SUCCESS requires payload and forbids failure_code")
            return
        if self.status is ChromaSearchStatusV1.EMPTY:
            if (
                not isinstance(self.payload, dict)
                or self.failure_code is not None
                or row_count != 0
            ):
                raise ValueError("EMPTY requires payload and forbids failure_code")
            return
        if self.status is ChromaSearchStatusV1.UNAVAILABLE:
            if (
                self.payload is not None
                or self.failure_code
                is not ChromaSearchFailureCodeV1.COLLECTION_UNAVAILABLE
            ):
                raise ValueError(
                    "UNAVAILABLE requires COLLECTION_UNAVAILABLE without payload"
                )
            return
        if self.payload is not None or self.failure_code not in {
            ChromaSearchFailureCodeV1.BACKEND_FAILURE,
            ChromaSearchFailureCodeV1.MALFORMED_RESPONSE,
        }:
            raise ValueError("ERROR requires a bounded error failure_code")


__all__ = [
    "ChromaSearchFailureCodeV1",
    "ChromaSearchResultV1",
    "ChromaSearchStatusV1",
]
