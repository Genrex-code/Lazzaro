from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ai_engine.affordance_resolver import SafetyDisposition, SafetyReasonCode
from ai_engine.perception_contracts import (
    IdentityStatusV1,
    PerceptionIdentityReceiptV1,
    PerceptionParseStatusV1,
    evaluate_perception_identity,
    parse_perception_payload,
)
from core.perception_authority import (
    AuthorityApplicabilityV1,
    AuthorityReasonCodeV1,
    AuthorityStatusV1,
    AuthorizedPhysicalActionV1,
    GenerationBindingModeV1,
    ParameterProvenanceKindV1,
    ParameterProvenanceV1,
    PerceptionAuthorityEvaluator,
    PerceptionAuthorityPolicyV1,
    PhysicalActionCandidateV1,
    PhysicalActionClassV1,
    TargetReferenceV1,
    TrustedParameterPathV1,
    canonical_json,
    derive_parameter_provenance,
)
from core.perception_freshness import (
    FreshnessPolicyV1,
    FreshnessReasonCodeV1,
    FreshnessStatusV1,
    PerceptionFreshnessEvaluator,
    PerceptionFreshnessReceiptV1,
    PerceptionIngestionMetadataV1,
    SupersessionStatusV1,
    TemporalEvidenceKindV1,
)
from core.perception_lifecycle import (
    LifecycleStatusV1,
    PerceptionLifecycleReceiptV1,
    PerceptionLifecycleTracker,
)


def _payload(*, duplicate: str | None = None) -> dict:
    obj = {
        "producer_object_id": "crate",
        "runtime_object_id": "42",
        "display_name": "Caja",
        "position": {"x": 1, "y": 0, "z": 2},
        "distance": 2.0,
        "affordances": ["tocar", "inspeccionable"],
        "confidence": 1.0,
    }
    objects = [obj]
    if duplicate is not None:
        second = deepcopy(obj)
        if duplicate == "conflict":
            second["affordances"] = ["golpear"]
        objects.append(second)
    return {
        "source_kind": "UNITY",
        "producer_instance_id": "unity-main",
        "stream_id": "world",
        "producer_epoch_id": "epoch-a",
        "logical_world_id": "world-a",
        "producer_capture_id": "capture-0",
        "generation": 0,
        "captured_at_utc_ms": 1_000,
        "objects": objects,
    }


def _bundle(*, duplicate: str | None = None):
    parse = parse_perception_payload(_payload(duplicate=duplicate), local_ingest_id="ingest-a")
    lifecycle = PerceptionLifecycleTracker().observe_and_classify(parse)
    identity = evaluate_perception_identity(
        parse,
        runtime_object_id="42",
        producer_object_id="crate",
        lifecycle_resolved=lifecycle.status is LifecycleStatusV1.RESOLVED,
    )
    freshness_policy = FreshnessPolicyV1(
        policy_id="freshness-test",
        max_capture_age_ms=500,
        max_future_skew_ms=50,
        require_no_known_superseding_capture=True,
    )
    freshness = PerceptionFreshnessEvaluator().evaluate(
        parse,
        PerceptionIngestionMetadataV1(
            local_ingest_id="ingest-a",
            received_at_utc_ms=1_010,
        ),
        lifecycle,
        policy=freshness_policy,
        evaluated_at_utc_ms=1_020,
    )
    policy = PerceptionAuthorityPolicyV1(
        policy_id="authority-test",
        freshness_policy_id="freshness-test",
        generation_binding_mode=GenerationBindingModeV1.REQUIRE_AUTHORIZED_GENERATION,
    )
    target = TargetReferenceV1(
        producer_instance_id="unity-main",
        stream_id="world",
        producer_epoch_id="epoch-a",
        logical_world_id="world-a",
        producer_capture_id="capture-0",
        generation=0,
        producer_object_id="crate",
        runtime_object_id="42",
    )
    candidate = PhysicalActionCandidateV1(
        id_accion="action-a",
        action_name="navegar_a_objeto",
        canonical_parameters_json=canonical_json({"nombre_objeto": "Caja"}),
        parameter_provenance=derive_parameter_provenance(
            "navegar_a_objeto",
            trusted_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
        ),
        action_class=PhysicalActionClassV1.TARGET_NAVIGATION,
        target_reference=target,
        interaction_type=None,
        grounding_status="ready",
        affordance_allowed=True,
        safety_disposition=SafetyDisposition.SAFE,
        safety_reason_code=SafetyReasonCode.NO_HAZARD,
        schema_valid=True,
    )
    return parse, identity, lifecycle, freshness, policy, candidate


def _evaluate(
    *,
    parse=None,
    identity=None,
    lifecycle=None,
    freshness=None,
    policy=None,
    candidate=None,
):
    base = _bundle()
    values = [parse, identity, lifecycle, freshness, policy, candidate]
    resolved = [base[index] if value is None else value for index, value in enumerate(values)]
    return PerceptionAuthorityEvaluator().evaluate(
        resolved[5],
        parse_receipt=resolved[0],
        identity_receipt=resolved[1],
        lifecycle_receipt=resolved[2],
        freshness_receipt=resolved[3],
        policy=resolved[4],
        authority_decision_id="authority-a",
    )


@pytest.mark.parametrize("safety", [SafetyDisposition.SAFE, SafetyDisposition.CAUTION])
def test_complete_modern_exact_target_can_authorize(safety: SafetyDisposition) -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    reason = (
        SafetyReasonCode.NO_HAZARD
        if safety is SafetyDisposition.SAFE
        else SafetyReasonCode.HAZARD_OBSERVATION
    )
    candidate = PhysicalActionCandidateV1.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "safety_disposition": safety.value,
            "safety_reason_code": reason.value,
        }
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
        authority_decision_id="authority-a",
    )

    assert receipt.status is AuthorityStatusV1.AUTHORIZED
    assert receipt.reason_code is AuthorityReasonCodeV1.ALL_GATES_PASSED
    assert authorized is not None
    assert authorized.target_reference.runtime_object_id == "42"
    assert authorized.parameters_copy() == {"nombre_objeto": "Caja"}
    assert authorized.binding_is_intact() is True


def test_consistent_duplicate_record_may_authorize_exact_target() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle(duplicate="same")

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert identity.action_relevant_records_agree is True
    assert receipt.status is AuthorityStatusV1.AUTHORIZED
    assert authorized is not None


def test_conflicting_duplicate_record_is_denied_never_first_wins() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle(duplicate="conflict")

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert identity.status is IdentityStatusV1.AMBIGUOUS
    assert receipt.status is AuthorityStatusV1.DENIED
    assert receipt.reason_code is AuthorityReasonCodeV1.DUPLICATE_RECORD_STATE_CONFLICT
    assert authorized is None


def test_invalid_block_a_data_denies() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    invalid = parse_perception_payload(
        {**_payload(), "captured_at_utc_ms": "not-an-int"},
        local_ingest_id="ingest-a",
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=invalid,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert invalid.status is PerceptionParseStatusV1.INVALID
    assert receipt.status is AuthorityStatusV1.DENIED
    assert receipt.reason_code is AuthorityReasonCodeV1.CANONICAL_DATA_INVALID
    assert authorized is None


@pytest.mark.parametrize(
    ("identity_status", "expected_status", "expected_reason"),
    [
        (
            IdentityStatusV1.AMBIGUOUS,
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.IDENTITY_AMBIGUOUS,
        ),
        (
            IdentityStatusV1.CONTRADICTORY,
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.IDENTITY_CONTRADICTORY,
        ),
        (
            IdentityStatusV1.ABSENT,
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.IDENTITY_ABSENT,
        ),
        (
            IdentityStatusV1.PARTIAL,
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.IDENTITY_PARTIAL,
        ),
    ],
)
def test_identity_failures_never_authorize_with_truthful_status(
    identity_status: IdentityStatusV1,
    expected_status: AuthorityStatusV1,
    expected_reason: AuthorityReasonCodeV1,
) -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    changed = PerceptionIdentityReceiptV1.model_validate(
        {
            **identity.model_dump(mode="json"),
            "status": identity_status.value,
            "action_relevant_records_agree": identity_status is IdentityStatusV1.AMBIGUOUS,
        }
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=changed,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert receipt.status is expected_status
    assert receipt.reason_code is expected_reason
    assert authorized is None


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_reason"),
    [
        (
            LifecycleStatusV1.ABSENT,
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.LIFECYCLE_ABSENT,
        ),
        (
            LifecycleStatusV1.CONTRADICTORY,
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.LIFECYCLE_CONTRADICTORY,
        ),
        (
            LifecycleStatusV1.HISTORY_UNKNOWN,
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.LIFECYCLE_HISTORY_UNKNOWN,
        ),
    ],
)
def test_lifecycle_failures_never_authorize(
    status: LifecycleStatusV1,
    expected_status: AuthorityStatusV1,
    expected_reason: AuthorityReasonCodeV1,
) -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    changed = PerceptionLifecycleReceiptV1.model_validate(
        {**lifecycle.model_dump(mode="json"), "status": status.value}
    )
    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=changed,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert receipt.status is expected_status
    assert receipt.reason_code is expected_reason
    assert authorized is None


def test_missing_policy_is_indeterminate() -> None:
    parse, identity, lifecycle, freshness, _policy, candidate = _bundle()

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=None,
    )

    assert receipt.status is AuthorityStatusV1.INDETERMINATE
    assert receipt.reason_code is AuthorityReasonCodeV1.FRESHNESS_POLICY_MISSING
    assert authorized is None


@pytest.mark.parametrize(
    ("fresh_status", "reason", "expected_status", "expected_reason"),
    [
        (
            FreshnessStatusV1.NOT_SATISFIED,
            FreshnessReasonCodeV1.TOO_OLD,
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.FRESHNESS_NOT_SATISFIED,
        ),
        (
            FreshnessStatusV1.INDETERMINATE,
            FreshnessReasonCodeV1.POLICY_MISSING,
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.FRESHNESS_INDETERMINATE,
        ),
    ],
)
def test_unsatisfied_or_indeterminate_freshness_never_authorizes(
    fresh_status: FreshnessStatusV1,
    reason: FreshnessReasonCodeV1,
    expected_status: AuthorityStatusV1,
    expected_reason: AuthorityReasonCodeV1,
) -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    changed = PerceptionFreshnessReceiptV1.model_validate(
        {
            **freshness.model_dump(mode="json"),
            "status": fresh_status.value,
            "reason_code": reason.value,
        }
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=changed,
        policy=policy,
    )

    assert receipt.status is expected_status
    assert receipt.reason_code is expected_reason
    assert authorized is None


def test_receive_time_proxy_is_always_denied_for_physical_target() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    proxy = PerceptionFreshnessReceiptV1.model_validate(
        {
            **freshness.model_dump(mode="json"),
            "evidence_kind": TemporalEvidenceKindV1.RECEIVE_TIME_PROXY.value,
            "status": FreshnessStatusV1.PARTIAL.value,
            "reason_code": FreshnessReasonCodeV1.RECEIVE_TIME_PROXY_ONLY.value,
        }
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=proxy,
        policy=policy,
    )

    assert receipt.status is AuthorityStatusV1.DENIED
    assert receipt.reason_code is AuthorityReasonCodeV1.RECEIVE_TIME_PROXY_DENIED
    assert authorized is None


@pytest.mark.parametrize(
    ("updates", "expected_status", "expected_reason"),
    [
        (
            {"grounding_status": "not_found"},
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.GROUNDING_NOT_READY,
        ),
        (
            {"affordance_allowed": False},
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.AFFORDANCE_DENIED,
        ),
        (
            {"affordance_allowed": None},
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.AFFORDANCE_INDETERMINATE,
        ),
        (
            {
                "safety_disposition": SafetyDisposition.UNSAFE.value,
                "safety_reason_code": SafetyReasonCode.HAZARD_APPROACH.value,
            },
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.SAFETY_DENIED,
        ),
        (
            {
                "safety_disposition": SafetyDisposition.INDETERMINATE.value,
                "safety_reason_code": SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED.value,
            },
            AuthorityStatusV1.INDETERMINATE,
            AuthorityReasonCodeV1.SAFETY_INDETERMINATE,
        ),
        (
            {"schema_valid": False},
            AuthorityStatusV1.DENIED,
            AuthorityReasonCodeV1.SCHEMA_INVALID,
        ),
    ],
)
def test_downstream_gate_failures_never_authorize(
    updates: dict,
    expected_status: AuthorityStatusV1,
    expected_reason: AuthorityReasonCodeV1,
) -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    changed = PhysicalActionCandidateV1.model_validate(
        {**candidate.model_dump(mode="json"), **updates}
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        changed,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert receipt.status is expected_status
    assert receipt.reason_code is expected_reason
    assert authorized is None


def test_interaction_without_canonical_subtype_is_indeterminate() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    changed = PhysicalActionCandidateV1.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "action_name": "interactuar_objeto_3d",
            "action_class": PhysicalActionClassV1.UNKNOWN.value,
            "canonical_parameters_json": canonical_json({"nombre_objeto": "Caja"}),
            "interaction_type": None,
        }
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        changed,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert receipt.status is AuthorityStatusV1.INDETERMINATE
    assert receipt.reason_code is AuthorityReasonCodeV1.INTERACTION_TYPE_MISSING
    assert authorized is None


def test_mixed_receipts_are_denied_by_correlation_gate() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    mixed = PerceptionFreshnessReceiptV1.model_validate(
        {
            **freshness.model_dump(mode="json"),
            "local_ingest_id": "different-ingest",
            "producer_capture_id": "different-capture",
        }
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=mixed,
        policy=policy,
    )

    assert receipt.status is AuthorityStatusV1.DENIED
    assert receipt.reason_code is AuthorityReasonCodeV1.RECEIPT_CORRELATION_MISMATCH
    assert authorized is None


def test_freshness_policy_id_mismatch_is_correlation_denial() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    mismatched_policy = PerceptionAuthorityPolicyV1(
        policy_id=policy.policy_id,
        freshness_policy_id="other-freshness-policy",
        generation_binding_mode=policy.generation_binding_mode,
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=mismatched_policy,
    )

    assert receipt.status is AuthorityStatusV1.DENIED
    assert receipt.reason_code is AuthorityReasonCodeV1.RECEIPT_CORRELATION_MISMATCH
    assert authorized is None


def test_legacy_label_only_can_never_authorize_target_action() -> None:
    _parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    legacy = parse_perception_payload(
        {"objetos_normalizados": [{"nombre": "Caja"}]},
        local_ingest_id="ingest-a",
    )

    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=legacy,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert receipt.status is AuthorityStatusV1.DENIED
    assert receipt.reason_code is AuthorityReasonCodeV1.LEGACY_PERCEPTION_DENIED
    assert authorized is None


def test_code_owned_applicability_distinguishes_not_required_and_unknown() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    body = PhysicalActionCandidateV1.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "action_name": "alejarse_de_objeto",
            "parameter_provenance": derive_parameter_provenance(
                "alejarse_de_objeto",
                trusted_path=TrustedParameterPathV1.CRITICAL_BODY_EVENT,
            ).model_dump(mode="json"),
        }
    )
    unknown = PhysicalActionCandidateV1.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "parameter_provenance": ParameterProvenanceV1(
                kind=ParameterProvenanceKindV1.UNKNOWN,
                producer="orchestrator_unknown",
            ).model_dump(mode="json"),
        }
    )

    body_receipt, _ = PerceptionAuthorityEvaluator().evaluate(
        body,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )
    unknown_receipt, _ = PerceptionAuthorityEvaluator().evaluate(
        unknown,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )

    assert body_receipt.applicability is AuthorityApplicabilityV1.NOT_REQUIRED
    assert body_receipt.status is AuthorityStatusV1.NOT_APPLICABLE
    assert unknown_receipt.applicability is AuthorityApplicabilityV1.UNKNOWN
    assert unknown_receipt.status is AuthorityStatusV1.INDETERMINATE


def test_parameter_provenance_cannot_be_marked_user_owned() -> None:
    with pytest.raises(ValidationError):
        ParameterProvenanceV1(
            kind=ParameterProvenanceKindV1.PERCEPTION_TARGET,
            producer="user metadata",
            code_owned=False,
        )


def test_binding_detects_target_action_subtype_safety_and_parameter_mutation() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )
    assert receipt.status is AuthorityStatusV1.AUTHORIZED
    assert authorized is not None

    mutations = (
        {"id_accion": "action-b"},
        {"action_name": "aplicar_fuerza"},
        {"canonical_parameters_json": canonical_json({"nombre_objeto": "Otra"})},
        {
            "target_reference": TargetReferenceV1.model_validate(
                {**authorized.target_reference.model_dump(mode="json"), "runtime_object_id": "84"}
            )
        },
        {"interaction_type": "golpear"},
        {"safety_disposition": SafetyDisposition.CAUTION},
        {"safety_reason_code": SafetyReasonCode.HAZARD_OBSERVATION},
    )
    for update in mutations:
        mutated = authorized.model_copy(update=update)
        assert mutated.binding_is_intact() is False


def test_authorized_action_constructor_rejects_reference_mismatch() -> None:
    parse, identity, lifecycle, freshness, policy, candidate = _bundle()
    _receipt, authorized = PerceptionAuthorityEvaluator().evaluate(
        candidate,
        parse_receipt=parse,
        identity_receipt=identity,
        lifecycle_receipt=lifecycle,
        freshness_receipt=freshness,
        policy=policy,
    )
    assert authorized is not None

    with pytest.raises(ValidationError):
        AuthorizedPhysicalActionV1(
            id_accion="different-action",
            action_name=authorized.action_name,
            canonical_parameters_json=authorized.canonical_parameters_json,
            target_reference=authorized.target_reference,
            interaction_type=authorized.interaction_type,
            safety_disposition=authorized.safety_disposition,
            safety_reason_code=authorized.safety_reason_code,
            authority_reference=authorized.authority_reference,
        )
