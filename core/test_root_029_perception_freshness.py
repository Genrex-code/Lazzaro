from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_engine.perception_contracts import parse_perception_payload
from api.schemas import EventoEntrante
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from core.perception_freshness import (
    FreshnessPolicyV1,
    FreshnessReasonCodeV1,
    FreshnessStatusV1,
    PerceptionFreshnessEvaluator,
    PerceptionIngestionMetadataV1,
    SupersessionStatusV1,
    TemporalEvidenceKindV1,
)
from core.perception_lifecycle import PerceptionLifecycleTracker


def _parse(
    *,
    captured: object = 1_000,
    generation: int = 0,
    capture: str | None = None,
    ingest: str = "ingest-a",
):
    return parse_perception_payload(
        {
            "source_kind": "UNITY",
            "producer_instance_id": "unity-main",
            "stream_id": "world",
            "producer_epoch_id": "epoch-a",
            "logical_world_id": "world-a",
            "producer_capture_id": capture or f"capture-{generation}",
            "generation": generation,
            "captured_at_utc_ms": captured,
            "objects": [],
        },
        local_ingest_id=ingest,
    )


def _policy(*, age: int = 500, skew: int = 50) -> FreshnessPolicyV1:
    return FreshnessPolicyV1(
        policy_id="test-policy",
        max_capture_age_ms=age,
        max_future_skew_ms=skew,
        require_no_known_superseding_capture=True,
    )


def _evaluate(
    receipt,
    lifecycle,
    *,
    received: int,
    evaluated: int,
    policy: FreshnessPolicyV1 | None = None,
):
    return PerceptionFreshnessEvaluator().evaluate(
        receipt,
        PerceptionIngestionMetadataV1(
            local_ingest_id=receipt.local_ingest_id,
            received_at_utc_ms=received,
        ),
        lifecycle,
        policy=policy,
        evaluated_at_utc_ms=evaluated,
    )


def test_capture_age_boundary_is_inclusive_and_deterministic() -> None:
    tracker = PerceptionLifecycleTracker()
    parsed = _parse(captured=1_000)
    lifecycle = tracker.observe_and_classify(parsed)

    exact = _evaluate(parsed, lifecycle, received=1_100, evaluated=1_500, policy=_policy())
    old = _evaluate(parsed, lifecycle, received=1_100, evaluated=1_501, policy=_policy())

    assert exact.status is FreshnessStatusV1.SATISFIED
    assert exact.capture_age_ms == 500
    assert old.status is FreshnessStatusV1.NOT_SATISFIED
    assert old.reason_code is FreshnessReasonCodeV1.TOO_OLD


def test_unix_zero_is_a_valid_capture_time_not_missing() -> None:
    tracker = PerceptionLifecycleTracker()
    parsed = _parse(captured=0)
    lifecycle = tracker.observe_and_classify(parsed)

    result = _evaluate(parsed, lifecycle, received=0, evaluated=0, policy=_policy())

    assert result.evidence_kind is TemporalEvidenceKindV1.UTC_CAPTURE_TIME
    assert result.captured_at_utc_ms == 0
    assert result.capture_age_ms == 0
    assert result.status is FreshnessStatusV1.SATISFIED


@pytest.mark.parametrize("invalid", [None, True, "1000", 1.5])
def test_missing_or_invalid_capture_time_never_becomes_physical_freshness(
    invalid: object,
) -> None:
    parsed = _parse(captured=invalid)
    lifecycle = PerceptionLifecycleTracker().observe_and_classify(parsed)

    result = _evaluate(parsed, lifecycle, received=1_100, evaluated=1_200, policy=_policy())

    assert result.evidence_kind is TemporalEvidenceKindV1.RECEIVE_TIME_PROXY
    assert result.status is FreshnessStatusV1.PARTIAL
    assert result.reason_code is FreshnessReasonCodeV1.RECEIVE_TIME_PROXY_ONLY


def test_future_time_honors_exact_skew_boundary() -> None:
    tracker = PerceptionLifecycleTracker()
    at_limit = _parse(captured=1_050)
    life_at_limit = tracker.observe_and_classify(at_limit)
    too_future = _parse(captured=1_051, generation=1, capture="capture-1")
    life_too_future = tracker.observe_and_classify(too_future)

    accepted = _evaluate(at_limit, life_at_limit, received=1_000, evaluated=1_000, policy=_policy())
    rejected = _evaluate(too_future, life_too_future, received=1_000, evaluated=1_000, policy=_policy())

    assert accepted.status is FreshnessStatusV1.SATISFIED
    assert rejected.status is FreshnessStatusV1.NOT_SATISFIED
    assert rejected.reason_code is FreshnessReasonCodeV1.TOO_FAR_IN_FUTURE


def test_replay_and_cache_reuse_do_not_rejuvenate_capture() -> None:
    tracker = PerceptionLifecycleTracker()
    parsed = _parse(captured=1_000)
    first_lifecycle = tracker.observe_and_classify(parsed)
    replay_lifecycle = tracker.observe_and_classify(parsed)

    first = _evaluate(parsed, first_lifecycle, received=1_010, evaluated=1_100, policy=_policy())
    replayed = _evaluate(parsed, replay_lifecycle, received=1_010, evaluated=1_600, policy=_policy())

    assert first.status is FreshnessStatusV1.SATISFIED
    assert replayed.status is FreshnessStatusV1.NOT_SATISFIED
    assert replayed.capture_age_ms == 600


def test_supersession_is_distinct_from_repeat_and_unknown_history() -> None:
    tracker = PerceptionLifecycleTracker(history_limit=2)
    capture0 = _parse(captured=1_000, generation=0, capture="capture-0", ingest="i0")
    life0 = tracker.observe_and_classify(capture0)
    capture1 = _parse(captured=1_010, generation=1, capture="capture-1", ingest="i1")
    tracker.observe_and_classify(capture1)
    repeated0 = tracker.observe_and_classify(capture0)

    superseded = _evaluate(capture0, repeated0, received=1_020, evaluated=1_030, policy=_policy())

    capture2 = _parse(captured=1_020, generation=2, capture="capture-2", ingest="i2")
    tracker.observe_and_classify(capture2)
    capture3 = _parse(captured=1_030, generation=3, capture="capture-3", ingest="i3")
    tracker.observe_and_classify(capture3)
    forgotten0 = tracker.observe_and_classify(capture0)
    unknown = _evaluate(capture0, forgotten0, received=1_020, evaluated=1_030, policy=_policy())

    assert life0.highest_observed_generation == 0
    assert superseded.supersession is SupersessionStatusV1.SUPERSEDED_BY_KNOWN_CAPTURE
    assert superseded.reason_code is FreshnessReasonCodeV1.SUPERSEDED
    assert unknown.supersession is SupersessionStatusV1.HISTORY_UNKNOWN
    assert unknown.status is FreshnessStatusV1.INDETERMINATE


def test_missing_policy_is_indeterminate_and_no_magic_threshold_is_used() -> None:
    tracker = PerceptionLifecycleTracker()
    parsed = _parse(captured=1_000)
    lifecycle = tracker.observe_and_classify(parsed)

    result = _evaluate(parsed, lifecycle, received=1_001, evaluated=1_001, policy=None)

    assert result.status is FreshnessStatusV1.INDETERMINATE
    assert result.reason_code is FreshnessReasonCodeV1.POLICY_MISSING
    assert result.policy_id is None


def test_policy_rejects_bool_float_string_and_negative_thresholds() -> None:
    for invalid in (True, 1.0, "1", -1):
        with pytest.raises(ValidationError):
            FreshnessPolicyV1(
                policy_id="policy",
                max_capture_age_ms=invalid,  # type: ignore[arg-type]
                max_future_skew_ms=0,
            )


def test_receiver_ingress_metadata_is_minted_once_and_preserved_on_copy() -> None:
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-1",
        detalles={
            "producer_capture_id": "capture-0",
            "producer_epoch_id": "epoch-a",
            "captured_at_utc_ms": 1_000,
            "objects": [],
        },
    )

    stamped_once = Orchestrator._adjuntar_ingreso_perceptivo(event)
    stamped_twice = Orchestrator._adjuntar_ingreso_perceptivo(stamped_once)

    first = stamped_once.detalles["perception_ingestion"]
    second = stamped_twice.detalles["perception_ingestion"]
    assert first == second
    assert first["local_ingest_id"] == event.local_ingest_id
    assert first["received_at_utc_ms"] == event.received_at_utc_ms


def test_behavior_cache_preserves_original_capture_epoch_generation_and_times() -> None:
    behavior = BehaviorCore()
    perception = {
        "source_kind": "UNITY",
        "producer_instance_id": "unity-main",
        "stream_id": "world",
        "producer_epoch_id": "epoch-a",
        "logical_world_id": "world-a",
        "producer_capture_id": "capture-0",
        "generation": 0,
        "captured_at_utc_ms": 1_000,
        "objects": [],
        "perception_ingestion": {
            "contract_version": 1,
            "local_ingest_id": "ingest-a",
            "received_at_utc_ms": 1_010,
        },
    }
    behavior.procesar_evento("actualizacion_estado", perception)
    cached = dict(behavior._ultima_percepcion)

    behavior.procesar_evento("comando_usuario", {"mensaje": "usa lo que ves"})

    assert behavior._ultima_percepcion == cached
    assert behavior._ultima_percepcion["producer_capture_id"] == "capture-0"
    assert behavior._ultima_percepcion["producer_epoch_id"] == "epoch-a"
    assert behavior._ultima_percepcion["generation"] == 0
    assert behavior._ultima_percepcion["captured_at_utc_ms"] == 1_000
    assert behavior._ultima_percepcion["perception_ingestion"]["received_at_utc_ms"] == 1_010
