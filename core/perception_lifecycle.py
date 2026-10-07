"""Receiver-owned lifecycle classification for ROOT-029 perception streams."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, field_validator

from ai_engine.perception_contracts import (
    PerceptionDataV1,
    PerceptionParseReceiptV1,
)


class LifecycleStatusV1(str, Enum):
    RESOLVED = "RESOLVED"
    ABSENT = "ABSENT"
    CONTRADICTORY = "CONTRADICTORY"
    HISTORY_UNKNOWN = "HISTORY_UNKNOWN"


class ScopeRelationV1(str, Enum):
    FIRST_OBSERVED = "FIRST_OBSERVED"
    SAME_SCOPE = "SAME_SCOPE"
    NEW_EPOCH = "NEW_EPOCH"
    NEW_WORLD = "NEW_WORLD"
    PRODUCER_CHANGED = "PRODUCER_CHANGED"
    STREAM_CHANGED = "STREAM_CHANGED"
    UNKNOWN_SCOPE = "UNKNOWN_SCOPE"
    CONTRADICTORY_SCOPE = "CONTRADICTORY_SCOPE"


class GenerationRelationV1(str, Enum):
    FIRST_OBSERVED = "FIRST_OBSERVED"
    ADVANCE = "ADVANCE"
    ADVANCE_WITH_GAP = "ADVANCE_WITH_GAP"
    REPEAT = "REPEAT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    COLLISION = "COLLISION"


class CaptureRelationV1(str, Enum):
    NEW_CAPTURE = "NEW_CAPTURE"
    REPEATED_CAPTURE = "REPEATED_CAPTURE"
    GENERATION_COLLISION = "GENERATION_COLLISION"
    CAPTURE_ID_REUSE_CONFLICT = "CAPTURE_ID_REUSE_CONFLICT"
    CAPTURE_CONTENT_CONFLICT = "CAPTURE_CONTENT_CONFLICT"
    HISTORY_UNKNOWN = "HISTORY_UNKNOWN"


class PerceptionLifecycleReceiptV1(BaseModel):
    contract_version: StrictInt = 1
    local_ingest_id: StrictStr
    producer_instance_id: StrictStr | None
    stream_id: StrictStr | None
    producer_epoch_id: StrictStr | None
    logical_world_id: StrictStr | None
    producer_capture_id: StrictStr | None
    generation: StrictInt | None
    status: LifecycleStatusV1
    scope_relation: ScopeRelationV1
    generation_relation: GenerationRelationV1
    capture_relation: CaptureRelationV1
    highest_observed_generation: StrictInt | None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value


@dataclass(frozen=True)
class _CaptureState:
    capture_id: str
    content_sha256: str


@dataclass
class _EpochState:
    epoch_id: str
    logical_world_id: str | None
    highest_generation: int
    history: OrderedDict[int, _CaptureState] = field(default_factory=OrderedDict)
    captures: OrderedDict[str, int] = field(default_factory=OrderedDict)


class PerceptionLifecycleTracker:
    """Bounded tracker with one lock per producer/stream owner scope."""

    def __init__(self, *, history_limit: int = 64):
        if type(history_limit) is not int or history_limit < 2:
            raise ValueError("history_limit must be an integer greater than one")
        self.history_limit = history_limit
        self._registry_lock = threading.Lock()
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._states: dict[tuple[str, str], _EpochState] = {}

    def observe_and_classify(
        self,
        receipt: PerceptionParseReceiptV1,
    ) -> PerceptionLifecycleReceiptV1:
        data = receipt.data
        if data is None:
            return self._absent(receipt.local_ingest_id)
        key = (data.producer_instance_id, data.stream_id)
        with self._registry_lock:
            lock = self._locks.setdefault(key, threading.RLock())
        with lock:
            return self._observe_locked(receipt.local_ingest_id, data, key)

    def _observe_locked(
        self,
        local_ingest_id: str,
        data: PerceptionDataV1,
        key: tuple[str, str],
    ) -> PerceptionLifecycleReceiptV1:
        state = self._states.get(key)
        scope_relation = ScopeRelationV1.FIRST_OBSERVED
        new_scope = False
        if state is None:
            if data.generation != 0:
                return self._receipt(
                    local_ingest_id,
                    data,
                    status=LifecycleStatusV1.CONTRADICTORY,
                    scope=scope_relation,
                    generation=GenerationRelationV1.FIRST_OBSERVED,
                    capture=CaptureRelationV1.GENERATION_COLLISION,
                    highest=None,
                )
            state = _EpochState(
                epoch_id=data.producer_epoch_id,
                logical_world_id=data.logical_world_id,
                highest_generation=data.generation,
            )
            self._states[key] = state
            new_scope = True
        elif state.epoch_id != data.producer_epoch_id:
            scope_relation = ScopeRelationV1.NEW_EPOCH
            if data.generation != 0:
                return self._receipt(
                    local_ingest_id,
                    data,
                    status=LifecycleStatusV1.CONTRADICTORY,
                    scope=scope_relation,
                    generation=GenerationRelationV1.FIRST_OBSERVED,
                    capture=CaptureRelationV1.GENERATION_COLLISION,
                    highest=state.highest_generation,
                )
            state = _EpochState(
                epoch_id=data.producer_epoch_id,
                logical_world_id=data.logical_world_id,
                highest_generation=0,
            )
            self._states[key] = state
            new_scope = True
        elif state.logical_world_id != data.logical_world_id:
            scope_relation = ScopeRelationV1.NEW_WORLD
            return self._receipt(
                local_ingest_id,
                data,
                status=LifecycleStatusV1.CONTRADICTORY,
                scope=scope_relation,
                generation=GenerationRelationV1.COLLISION,
                capture=CaptureRelationV1.CAPTURE_ID_REUSE_CONFLICT,
                highest=state.highest_generation,
            )
        else:
            scope_relation = ScopeRelationV1.SAME_SCOPE

        fingerprint = self._fingerprint(data)
        if new_scope:
            state.history[data.generation] = _CaptureState(
                data.producer_capture_id,
                fingerprint,
            )
            state.captures[data.producer_capture_id] = data.generation
            return self._receipt(
                local_ingest_id,
                data,
                status=LifecycleStatusV1.RESOLVED,
                scope=scope_relation,
                generation=GenerationRelationV1.FIRST_OBSERVED,
                capture=CaptureRelationV1.NEW_CAPTURE,
                highest=state.highest_generation,
            )
        known_at_generation = state.history.get(data.generation)
        known_generation_for_capture = state.captures.get(data.producer_capture_id)

        if known_at_generation is not None:
            generation_relation = GenerationRelationV1.REPEAT
            if known_at_generation.capture_id != data.producer_capture_id:
                return self._receipt(
                    local_ingest_id,
                    data,
                    status=LifecycleStatusV1.CONTRADICTORY,
                    scope=scope_relation,
                    generation=GenerationRelationV1.COLLISION,
                    capture=CaptureRelationV1.GENERATION_COLLISION,
                    highest=state.highest_generation,
                )
            if known_at_generation.content_sha256 != fingerprint:
                return self._receipt(
                    local_ingest_id,
                    data,
                    status=LifecycleStatusV1.CONTRADICTORY,
                    scope=scope_relation,
                    generation=generation_relation,
                    capture=CaptureRelationV1.CAPTURE_CONTENT_CONFLICT,
                    highest=state.highest_generation,
                )
            state.history.move_to_end(data.generation)
            state.captures.move_to_end(data.producer_capture_id)
            return self._receipt(
                local_ingest_id,
                data,
                status=LifecycleStatusV1.RESOLVED,
                scope=scope_relation,
                generation=generation_relation,
                capture=CaptureRelationV1.REPEATED_CAPTURE,
                highest=state.highest_generation,
            )

        if known_generation_for_capture is not None:
            return self._receipt(
                local_ingest_id,
                data,
                status=LifecycleStatusV1.CONTRADICTORY,
                scope=scope_relation,
                generation=GenerationRelationV1.COLLISION,
                capture=CaptureRelationV1.CAPTURE_ID_REUSE_CONFLICT,
                highest=state.highest_generation,
            )

        if data.generation < state.highest_generation:
            return self._receipt(
                local_ingest_id,
                data,
                status=LifecycleStatusV1.HISTORY_UNKNOWN,
                scope=scope_relation,
                generation=GenerationRelationV1.OUT_OF_ORDER,
                capture=CaptureRelationV1.HISTORY_UNKNOWN,
                highest=state.highest_generation,
            )

        if data.generation == state.highest_generation:
            # A same-generation row absent from bounded history is unknowable.
            return self._receipt(
                local_ingest_id,
                data,
                status=LifecycleStatusV1.HISTORY_UNKNOWN,
                scope=scope_relation,
                generation=GenerationRelationV1.COLLISION,
                capture=CaptureRelationV1.HISTORY_UNKNOWN,
                highest=state.highest_generation,
            )

        relation = (
            GenerationRelationV1.ADVANCE
            if data.generation == state.highest_generation + 1
            else GenerationRelationV1.ADVANCE_WITH_GAP
        )
        state.highest_generation = data.generation
        state.history[data.generation] = _CaptureState(
            data.producer_capture_id,
            fingerprint,
        )
        state.captures[data.producer_capture_id] = data.generation
        self._evict(state)
        return self._receipt(
            local_ingest_id,
            data,
            status=LifecycleStatusV1.RESOLVED,
            scope=scope_relation,
            generation=relation,
            capture=CaptureRelationV1.NEW_CAPTURE,
            highest=state.highest_generation,
        )

    def _evict(self, state: _EpochState) -> None:
        while len(state.history) > self.history_limit:
            _generation, capture = state.history.popitem(last=False)
            state.captures.pop(capture.capture_id, None)

    @staticmethod
    def _fingerprint(data: PerceptionDataV1) -> str:
        payload = json.dumps(
            data.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()

    @staticmethod
    def _absent(local_ingest_id: str) -> PerceptionLifecycleReceiptV1:
        return PerceptionLifecycleReceiptV1(
            local_ingest_id=local_ingest_id,
            producer_instance_id=None,
            stream_id=None,
            producer_epoch_id=None,
            logical_world_id=None,
            producer_capture_id=None,
            generation=None,
            status=LifecycleStatusV1.ABSENT,
            scope_relation=ScopeRelationV1.UNKNOWN_SCOPE,
            generation_relation=GenerationRelationV1.FIRST_OBSERVED,
            capture_relation=CaptureRelationV1.HISTORY_UNKNOWN,
            highest_observed_generation=None,
        )

    @staticmethod
    def _receipt(
        local_ingest_id: str,
        data: PerceptionDataV1,
        *,
        status: LifecycleStatusV1,
        scope: ScopeRelationV1,
        generation: GenerationRelationV1,
        capture: CaptureRelationV1,
        highest: int | None,
    ) -> PerceptionLifecycleReceiptV1:
        return PerceptionLifecycleReceiptV1(
            local_ingest_id=local_ingest_id,
            producer_instance_id=data.producer_instance_id,
            stream_id=data.stream_id,
            producer_epoch_id=data.producer_epoch_id,
            logical_world_id=data.logical_world_id,
            producer_capture_id=data.producer_capture_id,
            generation=data.generation,
            status=status,
            scope_relation=scope,
            generation_relation=generation,
            capture_relation=capture,
            highest_observed_generation=highest,
        )
