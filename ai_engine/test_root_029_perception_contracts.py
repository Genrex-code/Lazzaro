from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from ai_engine.perception_contracts import (
    IdentityStatusV1,
    PerceivedObjectDataV1,
    PerceptionParseStatusV1,
    RecordMultiplicityRelationV1,
    Vector2V1,
    Vector3V1,
    evaluate_perception_identity,
    parse_perception_payload,
)


def _payload(*, capture: str = "capture-0", generation: int = 0) -> dict:
    return {
        "contract_version": 1,
        "source_kind": "unity",
        "producer_instance_id": "unity-main",
        "stream_id": "scene-perception",
        "producer_epoch_id": "epoch-a",
        "logical_world_id": "world-a",
        "producer_capture_id": capture,
        "generation": generation,
        "captured_at_utc_ms": 1_000,
        "objects": [
            {
                "producer_object_id": "crate-persistent",
                "runtime_object_id": "42",
                "display_name": "Caja",
                "aliases": ["caja"],
                "position": {"x": 0, "y": 0.0, "z": 0},
                "screen_position": {"x": 0, "y": 0.0},
                "distance": 0,
                "affordances": ["tocar"],
                "confidence": 0,
            }
        ],
    }


def test_canonical_snapshot_preserves_all_meaningful_zeroes() -> None:
    receipt = parse_perception_payload(_payload(), local_ingest_id="ingest-a")

    assert receipt.status is PerceptionParseStatusV1.CANONICAL
    obj = receipt.data.objects[0]  # type: ignore[union-attr]
    assert obj.position == Vector3V1(x=0, y=0, z=0)
    assert obj.screen_position == Vector2V1(x=0, y=0)
    assert obj.distance == 0.0
    assert obj.confidence == 0.0


def test_missing_values_remain_distinct_from_zero() -> None:
    payload = _payload()
    payload["objects"][0].pop("distance")
    payload["objects"][0].pop("confidence")
    payload["objects"][0].pop("position")

    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    obj = receipt.data.objects[0]  # type: ignore[union-attr]
    assert obj.distance is None
    assert obj.confidence is None
    assert obj.position is None


@pytest.mark.parametrize("bad", [True, "0", math.nan, math.inf, -math.inf])
def test_numeric_contract_rejects_bool_strings_and_nonfinite_distance(bad: object) -> None:
    payload = _payload()
    payload["objects"][0]["distance"] = bad

    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    assert receipt.status is PerceptionParseStatusV1.INVALID
    assert receipt.data is None


@pytest.mark.parametrize("bad", [True, "0", math.nan, math.inf, -math.inf])
def test_numeric_contract_rejects_bool_strings_and_nonfinite_vector_values(
    bad: object,
) -> None:
    payload = _payload()
    payload["objects"][0]["position"]["x"] = bad

    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    assert receipt.status is PerceptionParseStatusV1.INVALID


def test_partial_vectors_are_invalid_instead_of_becoming_zero_filled() -> None:
    payload = _payload()
    payload["objects"][0]["position"] = {"x": 0, "y": 0}

    assert (
        parse_perception_payload(payload, local_ingest_id="ingest-a").status
        is PerceptionParseStatusV1.INVALID
    )


def test_canonical_models_and_serialized_copies_are_immutable() -> None:
    receipt = parse_perception_payload(_payload(), local_ingest_id="ingest-a")
    data = receipt.data
    assert data is not None

    with pytest.raises(ValidationError):
        data.generation = 7  # type: ignore[misc]
    with pytest.raises(ValidationError):
        data.objects[0].distance = 9  # type: ignore[misc]

    dumped = data.model_dump(mode="json")
    dumped["objects"][0]["distance"] = 99
    assert data.objects[0].distance == 0.0


def test_parser_does_not_retain_mutable_raw_payload() -> None:
    payload = _payload()
    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")
    payload["objects"][0]["display_name"] = "Mutada"
    payload["objects"][0]["affordances"].append("golpear")

    assert receipt.data is not None
    assert receipt.data.objects[0].display_name == "Caja"
    assert receipt.data.objects[0].affordances == ("tocar",)


def test_legacy_or_incomplete_payload_is_explicitly_degraded() -> None:
    receipt = parse_perception_payload(
        {"objetos_normalizados": [{"nombre": "Caja"}]},
        local_ingest_id="ingest-a",
    )

    assert receipt.status is PerceptionParseStatusV1.LEGACY_DEGRADED
    assert receipt.compatibility_used is True
    assert receipt.data is None


def test_strict_contract_version_rejects_coercions() -> None:
    for invalid in (True, 1.0, "1", False, 2):
        payload = _payload()
        payload["contract_version"] = invalid
        assert (
            parse_perception_payload(payload, local_ingest_id="ingest-a").status
            is PerceptionParseStatusV1.INVALID
        )


def test_explicit_identity_preserves_producer_runtime_and_presentation_dimensions() -> None:
    receipt = parse_perception_payload(_payload(), local_ingest_id="ingest-a")
    identity = evaluate_perception_identity(
        receipt,
        runtime_object_id="42",
        producer_object_id="crate-persistent",
        lifecycle_resolved=True,
    )

    assert identity.status is IdentityStatusV1.RESOLVED
    assert identity.target_runtime_object_id == "42"
    assert identity.target_producer_object_id == "crate-persistent"
    assert receipt.data.objects[0].display_name == "Caja"  # type: ignore[union-attr]


def test_display_name_never_becomes_runtime_identity() -> None:
    payload = _payload()
    payload["objects"][0]["runtime_object_id"] = None
    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    identity = evaluate_perception_identity(
        receipt,
        runtime_object_id="Caja",
        lifecycle_resolved=True,
    )

    assert identity.status is IdentityStatusV1.ABSENT


def test_same_display_name_with_different_ids_remains_unambiguous_by_exact_id() -> None:
    payload = _payload()
    second = dict(payload["objects"][0])
    second.update(producer_object_id="other", runtime_object_id="84")
    payload["objects"].append(second)
    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    identity = evaluate_perception_identity(
        receipt,
        runtime_object_id="42",
        producer_object_id="crate-persistent",
        lifecycle_resolved=True,
    )

    assert identity.status is IdentityStatusV1.RESOLVED
    assert identity.multiplicity is RecordMultiplicityRelationV1.UNIQUE


def test_consistent_duplicate_record_is_visible_and_resolved() -> None:
    payload = _payload()
    payload["objects"].append(dict(payload["objects"][0]))
    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    identity = evaluate_perception_identity(
        receipt,
        runtime_object_id="42",
        producer_object_id="crate-persistent",
        lifecycle_resolved=True,
    )

    assert identity.status is IdentityStatusV1.RESOLVED
    assert identity.multiplicity is RecordMultiplicityRelationV1.DUPLICATE_RECORD
    assert identity.action_relevant_records_agree is True


def test_conflicting_duplicate_record_is_ambiguous_and_never_picks_first() -> None:
    payload = _payload()
    conflicting = dict(payload["objects"][0])
    conflicting["affordances"] = ["golpear"]
    payload["objects"].append(conflicting)
    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    identity = evaluate_perception_identity(
        receipt,
        runtime_object_id="42",
        producer_object_id="crate-persistent",
        lifecycle_resolved=True,
    )

    assert identity.status is IdentityStatusV1.AMBIGUOUS
    assert identity.multiplicity is RecordMultiplicityRelationV1.AMBIGUOUS_DUPLICATE
    assert identity.action_relevant_records_agree is False


def test_duplicate_runtime_id_with_different_producer_ids_is_contradictory() -> None:
    payload = _payload()
    conflicting = dict(payload["objects"][0])
    conflicting["producer_object_id"] = "different-persistent-id"
    payload["objects"].append(conflicting)
    receipt = parse_perception_payload(payload, local_ingest_id="ingest-a")

    identity = evaluate_perception_identity(
        receipt,
        runtime_object_id="42",
        lifecycle_resolved=True,
    )

    assert identity.status is IdentityStatusV1.CONTRADICTORY
    assert identity.multiplicity is RecordMultiplicityRelationV1.CONTRADICTORY


def test_direct_object_contract_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PerceivedObjectDataV1(
            display_name="Caja",
            unexpected="not allowed",  # type: ignore[call-arg]
        )
