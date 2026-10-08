from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from ai_engine.perception_contracts import parse_perception_payload
from core.perception_lifecycle import (
    CaptureRelationV1,
    GenerationRelationV1,
    LifecycleStatusV1,
    PerceptionLifecycleTracker,
    ScopeRelationV1,
)


def _receipt(
    generation: int,
    *,
    capture: str | None = None,
    producer: str = "unity-main",
    stream: str = "world",
    epoch: str = "epoch-a",
    world: str | None = "logical-world-a",
    distance: float = 1.0,
):
    payload = {
        "source_kind": "UNITY",
        "producer_instance_id": producer,
        "stream_id": stream,
        "producer_epoch_id": epoch,
        "logical_world_id": world,
        "producer_capture_id": capture or f"capture-{generation}",
        "generation": generation,
        "captured_at_utc_ms": 1_000 + generation,
        "objects": [
            {
                "runtime_object_id": "42",
                "producer_object_id": "crate",
                "display_name": "Caja",
                "distance": distance,
            }
        ],
    }
    return parse_perception_payload(payload, local_ingest_id=f"ingest-{producer}-{stream}-{capture or generation}")


def _seed_to(tracker: PerceptionLifecycleTracker, generation: int) -> None:
    for current in range(generation + 1):
        assert tracker.observe_and_classify(_receipt(current)).status is LifecycleStatusV1.RESOLVED


def test_receiver_first_observation_accepts_nonzero_generation() -> None:
    tracker = PerceptionLifecycleTracker()

    first = tracker.observe_and_classify(_receipt(10))

    assert first.status is LifecycleStatusV1.RESOLVED
    assert first.scope_relation is ScopeRelationV1.FIRST_OBSERVED
    assert first.generation_relation is GenerationRelationV1.FIRST_OBSERVED
    assert first.capture_relation is CaptureRelationV1.NEW_CAPTURE
    assert first.highest_observed_generation == 10

    advanced = tracker.observe_and_classify(_receipt(11))
    older_unknown = tracker.observe_and_classify(_receipt(9))

    assert advanced.status is LifecycleStatusV1.RESOLVED
    assert advanced.generation_relation is GenerationRelationV1.ADVANCE
    assert older_unknown.status is LifecycleStatusV1.HISTORY_UNKNOWN
    assert older_unknown.generation_relation is GenerationRelationV1.OUT_OF_ORDER
    assert older_unknown.capture_relation is CaptureRelationV1.HISTORY_UNKNOWN


def test_generation_10_to_11_is_advance() -> None:
    tracker = PerceptionLifecycleTracker(history_limit=16)
    _seed_to(tracker, 10)

    result = tracker.observe_and_classify(_receipt(11))

    assert result.status is LifecycleStatusV1.RESOLVED
    assert result.generation_relation is GenerationRelationV1.ADVANCE


def test_generation_10_to_15_is_visible_gap() -> None:
    tracker = PerceptionLifecycleTracker(history_limit=16)
    _seed_to(tracker, 10)

    result = tracker.observe_and_classify(_receipt(15))

    assert result.status is LifecycleStatusV1.RESOLVED
    assert result.generation_relation is GenerationRelationV1.ADVANCE_WITH_GAP


def test_out_of_order_known_capture_repeats_without_becoming_new() -> None:
    tracker = PerceptionLifecycleTracker(history_limit=16)
    _seed_to(tracker, 12)

    result = tracker.observe_and_classify(_receipt(11))

    assert result.status is LifecycleStatusV1.RESOLVED
    assert result.generation_relation is GenerationRelationV1.REPEAT
    assert result.capture_relation is CaptureRelationV1.REPEATED_CAPTURE


def test_same_generation_same_capture_is_repeat() -> None:
    tracker = PerceptionLifecycleTracker()
    first = _receipt(0, capture="same")
    tracker.observe_and_classify(first)

    repeated = tracker.observe_and_classify(_receipt(0, capture="same"))

    assert repeated.status is LifecycleStatusV1.RESOLVED
    assert repeated.generation_relation is GenerationRelationV1.REPEAT
    assert repeated.capture_relation is CaptureRelationV1.REPEATED_CAPTURE


def test_same_generation_different_capture_is_collision() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, capture="first"))

    collision = tracker.observe_and_classify(_receipt(0, capture="second"))

    assert collision.status is LifecycleStatusV1.CONTRADICTORY
    assert collision.generation_relation is GenerationRelationV1.COLLISION
    assert collision.capture_relation is CaptureRelationV1.GENERATION_COLLISION


def test_same_capture_different_generation_is_reuse_conflict() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, capture="same"))

    conflict = tracker.observe_and_classify(_receipt(1, capture="same"))

    assert conflict.status is LifecycleStatusV1.CONTRADICTORY
    assert conflict.capture_relation is CaptureRelationV1.CAPTURE_ID_REUSE_CONFLICT


def test_same_capture_and_generation_with_changed_content_is_content_conflict() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, capture="same", distance=1.0))

    conflict = tracker.observe_and_classify(
        _receipt(0, capture="same", distance=2.0)
    )

    assert conflict.status is LifecycleStatusV1.CONTRADICTORY
    assert conflict.capture_relation is CaptureRelationV1.CAPTURE_CONTENT_CONFLICT


def test_new_epoch_resets_generation_and_runtime_namespace() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))
    tracker.observe_and_classify(_receipt(1, epoch="epoch-a"))

    reset = tracker.observe_and_classify(_receipt(0, epoch="epoch-b"))

    assert reset.status is LifecycleStatusV1.RESOLVED
    assert reset.scope_relation is ScopeRelationV1.NEW_EPOCH
    assert reset.generation_relation is GenerationRelationV1.FIRST_OBSERVED


def test_new_epoch_without_generation_reset_is_contradictory() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))

    result = tracker.observe_and_classify(_receipt(1, epoch="epoch-b"))

    assert result.status is LifecycleStatusV1.CONTRADICTORY
    assert result.scope_relation is ScopeRelationV1.NEW_EPOCH


def test_old_epoch_replay_never_replaces_current_epoch() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))
    tracker.observe_and_classify(_receipt(1, epoch="epoch-a"))
    tracker.observe_and_classify(_receipt(0, epoch="epoch-b"))

    replay = tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))

    assert replay.status is LifecycleStatusV1.CONTRADICTORY
    assert replay.scope_relation is ScopeRelationV1.CONTRADICTORY_SCOPE
    assert replay.generation_relation is GenerationRelationV1.OUT_OF_ORDER
    assert replay.capture_relation is CaptureRelationV1.OLD_EPOCH_REPLAY
    assert tracker._states[("unity-main", "world")].epoch_id == "epoch-b"


def test_new_epoch_continues_normally_after_reset() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))

    first_b = tracker.observe_and_classify(_receipt(0, epoch="epoch-b"))
    second_b = tracker.observe_and_classify(_receipt(1, epoch="epoch-b"))

    assert first_b.status is LifecycleStatusV1.RESOLVED
    assert first_b.scope_relation is ScopeRelationV1.NEW_EPOCH
    assert second_b.status is LifecycleStatusV1.RESOLVED
    assert second_b.scope_relation is ScopeRelationV1.SAME_SCOPE
    assert second_b.generation_relation is GenerationRelationV1.ADVANCE


def test_evicted_epoch_history_fails_closed_as_unknown() -> None:
    tracker = PerceptionLifecycleTracker(epoch_history_limit=1)
    tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))
    tracker.observe_and_classify(_receipt(0, epoch="epoch-b"))
    tracker.observe_and_classify(_receipt(0, epoch="epoch-c"))

    old_unknown = tracker.observe_and_classify(_receipt(0, epoch="epoch-a"))

    assert old_unknown.status is LifecycleStatusV1.HISTORY_UNKNOWN
    assert old_unknown.scope_relation is ScopeRelationV1.UNKNOWN_SCOPE
    assert old_unknown.capture_relation is CaptureRelationV1.HISTORY_UNKNOWN
    assert tracker._states[("unity-main", "world")].epoch_id == "epoch-c"


def test_producers_and_streams_have_independent_state_and_locks() -> None:
    tracker = PerceptionLifecycleTracker()

    producer_a = tracker.observe_and_classify(_receipt(0, producer="producer-a"))
    producer_b = tracker.observe_and_classify(_receipt(0, producer="producer-b"))
    stream_b = tracker.observe_and_classify(_receipt(0, stream="other-stream"))

    assert producer_a.scope_relation is ScopeRelationV1.FIRST_OBSERVED
    assert producer_b.scope_relation is ScopeRelationV1.FIRST_OBSERVED
    assert stream_b.scope_relation is ScopeRelationV1.FIRST_OBSERVED
    assert len(tracker._locks) == 3


def test_world_change_inside_epoch_is_contradictory() -> None:
    tracker = PerceptionLifecycleTracker()
    tracker.observe_and_classify(_receipt(0, world="world-a"))

    result = tracker.observe_and_classify(_receipt(1, world="world-b"))

    assert result.status is LifecycleStatusV1.CONTRADICTORY
    assert result.scope_relation is ScopeRelationV1.NEW_WORLD


def test_evicted_history_returns_unknown_never_fabricated_new_capture() -> None:
    tracker = PerceptionLifecycleTracker(history_limit=2)
    _seed_to(tracker, 3)

    evicted = tracker.observe_and_classify(_receipt(0))

    assert evicted.status is LifecycleStatusV1.HISTORY_UNKNOWN
    assert evicted.generation_relation is GenerationRelationV1.OUT_OF_ORDER
    assert evicted.capture_relation is CaptureRelationV1.HISTORY_UNKNOWN


def test_observe_and_classify_is_atomic_per_stream_using_barrier() -> None:
    tracker = PerceptionLifecycleTracker()
    barrier = threading.Barrier(3)

    def observe() -> tuple[GenerationRelationV1, CaptureRelationV1]:
        barrier.wait()
        receipt = tracker.observe_and_classify(_receipt(0, capture="shared"))
        return receipt.generation_relation, receipt.capture_relation

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(observe)
        second = pool.submit(observe)
        barrier.wait()
        results = {first.result(), second.result()}

    assert results == {
        (GenerationRelationV1.FIRST_OBSERVED, CaptureRelationV1.NEW_CAPTURE),
        (GenerationRelationV1.REPEAT, CaptureRelationV1.REPEATED_CAPTURE),
    }
