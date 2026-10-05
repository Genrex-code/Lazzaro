from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.affordance_resolver import (
    AffordanceResolver,
    SafetyAssessmentV1,
    SafetyDisposition,
    SafetyReasonCode,
)
from ai_engine.perception_models import PerceivedObject


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _object(*, hazard: bool, affordances: list[str] | None = None, name: str = "Target") -> PerceivedObject:
    values = list(affordances or [])
    if hazard:
        values.append("peligro")
    return PerceivedObject(id=name, display_name=name, aliases=[name.lower()], affordances=values)


def _assessment(
    reason_code: SafetyReasonCode,
    *,
    disposition: SafetyDisposition | None = None,
    ranking_penalty: float | int | None = None,
    contract_version: object = 1,
) -> SafetyAssessmentV1:
    canonical = {
        SafetyReasonCode.NO_HAZARD: (SafetyDisposition.SAFE, 0.0),
        SafetyReasonCode.HAZARD_AVOIDANCE: (SafetyDisposition.SAFE, 0.0),
        SafetyReasonCode.HAZARD_OBSERVATION: (SafetyDisposition.CAUTION, 5.0),
        SafetyReasonCode.HAZARD_APPROACH: (SafetyDisposition.UNSAFE, 20.0),
        SafetyReasonCode.HAZARD_CONTACT: (SafetyDisposition.UNSAFE, 20.0),
        SafetyReasonCode.HAZARD_MANIPULATION: (SafetyDisposition.UNSAFE, 20.0),
        SafetyReasonCode.HAZARD_FORCE: (SafetyDisposition.UNSAFE, 20.0),
        SafetyReasonCode.HAZARD_INTERACTION_UNSPECIFIED: (SafetyDisposition.INDETERMINATE, 20.0),
        SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED: (SafetyDisposition.INDETERMINATE, 20.0),
        SafetyReasonCode.SAFETY_ASSESSMENT_FAILED: (SafetyDisposition.INDETERMINATE, 20.0),
    }
    expected_disposition, expected_penalty = canonical[reason_code]
    return SafetyAssessmentV1(
        contract_version=contract_version,
        disposition=disposition or expected_disposition,
        reason_code=reason_code,
        ranking_penalty=expected_penalty if ranking_penalty is None else ranking_penalty,
    )


@pytest.mark.parametrize("value", [True, False, 1.0, "1", 2])
def test_safety_contract_version_is_exact_integer_one(value: object) -> None:
    with pytest.raises((ValidationError, ValueError, TypeError)):
        _assessment(SafetyReasonCode.NO_HAZARD, contract_version=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -0.1, True, "5", 20.1])
def test_safety_ranking_penalty_rejects_invalid_numeric_values(value: object) -> None:
    with pytest.raises((ValidationError, ValueError, TypeError)):
        _assessment(SafetyReasonCode.HAZARD_OBSERVATION, ranking_penalty=value)  # type: ignore[arg-type]


def test_safety_contract_is_frozen_forbids_extra_and_serializes_stably() -> None:
    assessment = _assessment(SafetyReasonCode.HAZARD_OBSERVATION)
    assert assessment.to_dict() == {
        "contract_version": 1,
        "disposition": "CAUTION",
        "reason_code": "HAZARD_OBSERVATION",
        "ranking_penalty": 5.0,
    }
    with pytest.raises(ValidationError):
        assessment.ranking_penalty = 0.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SafetyAssessmentV1(
            disposition=SafetyDisposition.SAFE,
            reason_code=SafetyReasonCode.NO_HAZARD,
            ranking_penalty=0,
            extra_field=True,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    ("reason", "wrong_disposition", "wrong_penalty"),
    [
        (SafetyReasonCode.NO_HAZARD, SafetyDisposition.UNSAFE, 20),
        (SafetyReasonCode.HAZARD_AVOIDANCE, SafetyDisposition.UNSAFE, 20),
        (SafetyReasonCode.HAZARD_APPROACH, SafetyDisposition.SAFE, 0),
        (SafetyReasonCode.HAZARD_OBSERVATION, SafetyDisposition.SAFE, 0),
        (SafetyReasonCode.HAZARD_INTERACTION_UNSPECIFIED, SafetyDisposition.UNSAFE, 0),
    ],
)
def test_reason_disposition_and_penalty_contradictions_are_rejected(
    reason: SafetyReasonCode,
    wrong_disposition: SafetyDisposition,
    wrong_penalty: float,
) -> None:
    with pytest.raises(ValidationError):
        _assessment(reason, disposition=wrong_disposition)
    with pytest.raises(ValidationError):
        _assessment(reason, ranking_penalty=wrong_penalty)


def test_authorization_is_derived_from_disposition() -> None:
    assert _assessment(SafetyReasonCode.NO_HAZARD).allowed_by_safety is True
    assert _assessment(SafetyReasonCode.HAZARD_OBSERVATION).allowed_by_safety is True
    assert _assessment(SafetyReasonCode.HAZARD_APPROACH).allowed_by_safety is False
    assert _assessment(SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED).allowed_by_safety is False


def test_grab_requires_grabbable() -> None:
    resolver = AffordanceResolver()
    tree = PerceivedObject(
        id="Tree_01",
        display_name="Tree_01",
        aliases=["arbol"],
        affordances=["mirar", "da_sombra"],
    )

    decision = resolver.validate(tree, "agarrar_objeto")

    assert_true(not decision.allowed, "Agarrar debe fallar sin affordance agarrable.")
    assert_true(decision.status == "not_allowed", "Debe devolver not_allowed.")
    assert_true("agarrable" in decision.missing, "Debe auditar affordance faltante.")


def test_grab_allowed_when_grabbable() -> None:
    resolver = AffordanceResolver()
    flower = PerceivedObject(
        id="Flower_01",
        display_name="Flower_01",
        aliases=["flor"],
        affordances=["agarrable", "mirar"],
    )

    decision = resolver.validate(flower, "agarrar_objeto")

    assert_true(decision.allowed, "Objeto agarrable debe permitir agarrar.")
    assert_true(decision.status == "allowed", "Debe devolver allowed.")
    assert_true("agarrable" in decision.matched, "Debe auditar affordance usada.")


def test_interaction_type_uses_specific_rules() -> None:
    resolver = AffordanceResolver()
    tablet = PerceivedObject(
        id="Tablet_01",
        display_name="Tablet_01",
        aliases=["tablet"],
        affordances=["inspeccionable"],
    )

    mirar = resolver.validate(tablet, "interactuar_objeto_3d", interaction_type="mirar")
    activar = resolver.validate(tablet, "interactuar_objeto_3d", interaction_type="activar")

    assert_true(mirar.allowed, "Mirar debe permitir inspeccionable.")
    assert_true(not activar.allowed, "Activar debe requerir activable.")
    assert_true("activable" in activar.missing, "Debe auditar activable faltante.")


def test_query_affordances_and_score() -> None:
    resolver = AffordanceResolver()
    tree = PerceivedObject(
        id="Tree_01",
        display_name="Tree_01",
        aliases=["arbol"],
        affordances=["da_sombra", "cubre"],
    )

    requested = resolver.requested_from_query("busca algo para cubrirte")
    score = resolver.score(tree, action="navegar_a_objeto", requested_affordances=requested)

    assert_true({"cubre", "da_sombra"} & requested, "Debe detectar affordances de cobertura.")
    assert_true(score == 100.0, "Objeto con affordance solicitada debe puntuar 100.")


def test_risk_penalty_for_danger() -> None:
    resolver = AffordanceResolver()
    fire = PerceivedObject(
        id="Fire_01",
        display_name="Fire_01",
        aliases=["fuego"],
        affordances=["peligro", "mirar"],
    )

    decision = resolver.validate(fire, "interactuar_objeto_3d", interaction_type="mirar")

    assert_true(decision.allowed, "Mirar un peligro puede seguir permitido.")
    assert_true(decision.risk_penalty == 5.0, "Observacion conocida debe producir penalizacion 5.")
    assert_true(decision.safety_assessment.disposition == SafetyDisposition.CAUTION, "Debe quedar CAUTION.")
    assert_true(
        decision.to_dict()["risk_penalty"] == decision.to_dict()["safety_assessment"]["ranking_penalty"],
        "risk_penalty legacy debe proyectarse desde la autoridad tipada.",
    )


@pytest.mark.parametrize(
    ("hazard", "action", "interaction_type", "expected_disposition", "expected_reason", "expected_penalty"),
    [
        (False, "navegar_a_objeto", None, SafetyDisposition.SAFE, SafetyReasonCode.NO_HAZARD, 0.0),
        (True, "alejarse_de_objeto", None, SafetyDisposition.SAFE, SafetyReasonCode.HAZARD_AVOIDANCE, 0.0),
        (True, "interactuar_objeto_3d", "mirar", SafetyDisposition.CAUTION, SafetyReasonCode.HAZARD_OBSERVATION, 5.0),
        (True, "navegar_a_objeto", None, SafetyDisposition.UNSAFE, SafetyReasonCode.HAZARD_APPROACH, 20.0),
        (True, "agarrar_objeto", None, SafetyDisposition.UNSAFE, SafetyReasonCode.HAZARD_MANIPULATION, 20.0),
        (True, "aplicar_fuerza", None, SafetyDisposition.UNSAFE, SafetyReasonCode.HAZARD_FORCE, 20.0),
        (True, "interactuar_objeto_3d", "tocar", SafetyDisposition.UNSAFE, SafetyReasonCode.HAZARD_CONTACT, 20.0),
        (True, "interactuar_objeto_3d", "activar", SafetyDisposition.UNSAFE, SafetyReasonCode.HAZARD_MANIPULATION, 20.0),
        (True, "interactuar_objeto_3d", None, SafetyDisposition.INDETERMINATE, SafetyReasonCode.HAZARD_INTERACTION_UNSPECIFIED, 20.0),
        (True, "teletransportar", None, SafetyDisposition.INDETERMINATE, SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED, 20.0),
    ],
)
def test_action_hazard_policy_is_naturally_reachable(
    hazard: bool,
    action: str,
    interaction_type: str | None,
    expected_disposition: SafetyDisposition,
    expected_reason: SafetyReasonCode,
    expected_penalty: float,
) -> None:
    assessment = AffordanceResolver.assess_safety(
        _object(hazard=hazard, affordances=["mirar", "inspeccionable", "tocar", "activable", "agarrable", "empujable"]),
        action,
        interaction_type=interaction_type,
    )
    assert assessment.disposition == expected_disposition
    assert assessment.reason_code == expected_reason
    assert assessment.ranking_penalty == expected_penalty


@pytest.mark.parametrize(
    ("label", "action", "interaction_type", "compatible_affordances", "disposition", "compatible_status"),
    [
        ("SAFE", "agarrar_objeto", None, ["agarrable"], SafetyDisposition.SAFE, "allowed"),
        ("CAUTION", "interactuar_objeto_3d", "mirar", ["mirar", "peligro"], SafetyDisposition.CAUTION, "allowed"),
        ("UNSAFE", "agarrar_objeto", None, ["agarrable", "peligro"], SafetyDisposition.UNSAFE, "unsafe"),
        ("INDETERMINATE", "interactuar_objeto_3d", None, ["mirar", "peligro"], SafetyDisposition.INDETERMINATE, "unsafe"),
    ],
)
def test_affordance_and_safety_dimensions_are_preserved(
    label: str,
    action: str,
    interaction_type: str | None,
    compatible_affordances: list[str],
    disposition: SafetyDisposition,
    compatible_status: str,
) -> None:
    resolver = AffordanceResolver()
    compatible = resolver.validate(
        _object(hazard=False, affordances=compatible_affordances, name=f"{label}-compatible"),
        action,
        interaction_type=interaction_type,
    )
    incompatible_affordances = [value for value in compatible_affordances if value == "peligro"]
    incompatible = resolver.validate(
        _object(hazard=False, affordances=incompatible_affordances, name=f"{label}-incompatible"),
        action,
        interaction_type=interaction_type,
    )

    assert compatible.safety_assessment.disposition == disposition
    assert compatible.status == compatible_status
    assert compatible.allowed is (disposition in {SafetyDisposition.SAFE, SafetyDisposition.CAUTION})
    assert incompatible.safety_assessment.disposition == disposition
    assert incompatible.status == "not_allowed"
    assert incompatible.allowed is False


def test_safety_metamorphic_inputs_do_not_change_policy() -> None:
    resolver = AffordanceResolver()
    baseline = _object(hazard=True, affordances=["mirar"], name="Hazard-A")
    renamed = _object(hazard=True, affordances=["mirar"], name="Completely Different Display")
    for obj in (baseline, renamed):
        decision = resolver.validate(obj, "interactuar_objeto_3d")
        assert decision.safety_assessment.reason_code == SafetyReasonCode.HAZARD_INTERACTION_UNSPECIFIED
        assert decision.allowed is False

    approach = resolver.assess_safety(baseline, "navegar_a_objeto")
    retreat = resolver.assess_safety(baseline, "alejarse_de_objeto")
    observation = resolver.assess_safety(baseline, "interactuar_objeto_3d", interaction_type="mirar")
    unspecified = resolver.assess_safety(baseline, "interactuar_objeto_3d")
    assert (approach.disposition, retreat.disposition) == (SafetyDisposition.UNSAFE, SafetyDisposition.SAFE)
    assert (observation.disposition, unspecified.disposition) == (
        SafetyDisposition.CAUTION,
        SafetyDisposition.INDETERMINATE,
    )


def test_expected_malformed_affordance_data_fails_closed_but_unexpected_defects_propagate() -> None:
    resolver = AffordanceResolver()
    malformed = _object(hazard=False)
    malformed.affordances = None  # type: ignore[assignment]

    decision = resolver.validate(malformed, "navegar_a_objeto")

    assert decision.allowed is False
    assert decision.status == "unsafe"
    assert decision.safety_assessment.reason_code == SafetyReasonCode.SAFETY_ASSESSMENT_FAILED
    assert decision.safety_assessment.disposition == SafetyDisposition.INDETERMINATE

    class ExplodingAffordances:
        def __iter__(self):
            raise RuntimeError("synthetic programming defect")

    unexpected = _object(hazard=False)
    unexpected.affordances = ExplodingAffordances()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="synthetic programming defect"):
        resolver.validate(unexpected, "navegar_a_objeto")


def main() -> None:
    print("=== INICIANDO PRUEBA DE AFFORDANCE RESOLVER ===\n")
    test_grab_requires_grabbable()
    test_grab_allowed_when_grabbable()
    test_interaction_type_uses_specific_rules()
    test_query_affordances_and_score()
    test_risk_penalty_for_danger()
    print("[OK] test_affordance_resolver paso: affordances validadas.")


if __name__ == "__main__":
    main()
