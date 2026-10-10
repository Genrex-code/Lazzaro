"""Golden vectors for the ROOT-029 V1 physical-action binding.

The checked-in JSONL is generated from the Python authority implementation.
It is consumed independently by the Unity/C# verifier tests.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from ai_engine.affordance_resolver import SafetyDisposition, SafetyReasonCode
from ai_engine.motor_schemas import ApplyForceParams
from core.perception_authority import (
    GenerationBindingModeV1,
    PerceptionAuthorityReferenceV1,
    TargetReferenceV1,
    action_binding_sha256,
    canonical_json,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "core" / "fixtures" / "root_038_action_binding_v1.jsonl"


def _target(
    *,
    generation: int,
    suffix: str,
    logical_world_id: str | None = "world:lazzaro-lab",
    producer_object_id: str | None = "object:caja-01",
    runtime_object_id: str = "runtime:caja-01",
) -> TargetReferenceV1:
    return TargetReferenceV1(
        producer_instance_id=f"unity-producer:{suffix}",
        stream_id=f"perception-stream:{suffix}",
        producer_epoch_id=f"epoch:{suffix}",
        logical_world_id=logical_world_id,
        producer_capture_id=f"capture:{suffix}",
        generation=generation,
        producer_object_id=producer_object_id,
        runtime_object_id=runtime_object_id,
    )


def _vector(
    case_id: str,
    *,
    action_name: str,
    parameters: dict[str, Any],
    target: TargetReferenceV1,
    interaction_type: str | None = None,
    mode: GenerationBindingModeV1 = GenerationBindingModeV1.REQUIRE_AUTHORIZED_GENERATION,
    safety: SafetyDisposition = SafetyDisposition.SAFE,
    safety_reason: SafetyReasonCode = SafetyReasonCode.NO_HAZARD,
    id_accion: str | None = None,
    policy_id: str = "root-029-authority-v1",
    freshness_id: str = "root-029-freshness-v1",
) -> dict[str, Any]:
    action_id = id_accion or f"action:{case_id}"
    canonical_parameters = canonical_json(parameters)
    binding_payload = {
        "binding_version": 1,
        "id_accion": action_id,
        "action_name": action_name,
        "parameters": json.loads(canonical_parameters),
        "target": target.model_dump(mode="json"),
        "authority_policy_id": policy_id,
        "freshness_policy_id": freshness_id,
        "generation_binding_mode": mode.value,
        "interaction_type": interaction_type,
        "safety_disposition": safety.value,
        "safety_reason_code": safety_reason.value,
    }
    canonical_binding = canonical_json(binding_payload)
    binding_bytes = canonical_binding.encode("utf-8")
    expected_hash = action_binding_sha256(
        id_accion=action_id,
        action_name=action_name,
        canonical_parameters_json=canonical_parameters,
        target=target,
        authority_policy_id=policy_id,
        freshness_policy_id=freshness_id,
        generation_binding_mode=mode,
        interaction_type=interaction_type,
        safety_disposition=safety,
        safety_reason_code=safety_reason,
    )
    assert sha256(binding_bytes).hexdigest() == expected_hash

    reference = PerceptionAuthorityReferenceV1(
        authority_decision_id=f"authority:{case_id}",
        id_accion=action_id,
        authority_policy_id=policy_id,
        freshness_policy_id=freshness_id,
        generation_binding_mode=mode,
        producer_capture_id=target.producer_capture_id,
        generation=target.generation,
        target=target,
        action_name=action_name,
        canonical_parameters_json=canonical_parameters,
        interaction_type=interaction_type,
        safety_disposition=safety,
        safety_reason_code=safety_reason,
        action_binding_sha256=expected_hash,
    )
    return {
        "case_id": case_id,
        "action": {
            "id_accion": action_id,
            "nombre": action_name,
            "parametros": parameters,
        },
        "authority_reference": reference.model_dump(mode="json"),
        "canonical_binding_json": canonical_binding,
        "canonical_binding_utf8_hex": binding_bytes.hex(),
        "expected_sha256": expected_hash,
    }


def build_vectors() -> list[dict[str, Any]]:
    all_controls = "control:" + "".join(chr(value) for value in range(0x20)) + ":end"
    nfc = "caja-caf\u00e9-ni\u00f1o"
    nfd = "caja-cafe\u0301-nin\u0303o"
    maximum_generation = (2**63) - 1
    signed_zero_force = ApplyForceParams(
        nombre_objeto="Caja fuerza",
        vector_x=1.0,
        vector_y=-0.0,
        vector_z=0.0,
        fuerza=1.0,
    ).model_dump(mode="json")
    diagonal_force = ApplyForceParams(
        nombre_objeto="Caja diagonal",
        vector_x=1.0,
        vector_y=1.0,
        vector_z=1.0,
        fuerza=37.25,
    ).model_dump(mode="json")

    return [
        _vector(
            "navigate_object_ascii_gen0",
            action_name="navegar_a_objeto",
            parameters={"distancia": 1.25, "nombre_objeto": "Caja"},
            target=_target(generation=0, suffix="ascii"),
        ),
        _vector(
            "navigate_position_integer_gen1",
            action_name="navegar_a_posicion",
            parameters={"x": 1, "y": -2, "z": 3},
            target=_target(
                generation=1,
                suffix="integer",
                logical_world_id=None,
                producer_object_id=None,
                runtime_object_id="runtime:position-anchor",
            ),
            interaction_type="",
            mode=GenerationBindingModeV1.REJECT_IF_KNOWN_SUPERSEDED,
        ),
        _vector(
            "interact_nfc_quote_slash_backslash",
            action_name="interactuar_objeto_3d",
            parameters={
                "nombre_objeto": f'{nfc} / ruta\\objeto "principal"',
                "tipo_accion": "tocar",
            },
            target=_target(generation=7, suffix=f'nfc/{nfc}\\"'),
            interaction_type="tocar",
            safety=SafetyDisposition.CAUTION,
            safety_reason=SafetyReasonCode.HAZARD_OBSERVATION,
        ),
        _vector(
            "grab_nfd_all_controls",
            action_name="agarrar_objeto",
            parameters={"nombre_objeto": nfd + all_controls},
            target=_target(generation=8, suffix="nfd-" + nfd + all_controls),
            safety=SafetyDisposition.CAUTION,
            safety_reason=SafetyReasonCode.HAZARD_MANIPULATION,
        ),
        _vector(
            "release_del_nel",
            action_name="soltar_objeto",
            parameters={"fuerza_lanzamiento": 0.0},
            target=_target(generation=9, suffix="DEL:\u007f:NEL:\u0085"),
        ),
        _vector(
            "move_away_line_separators",
            action_name="alejarse_de_objeto",
            parameters={"distancia": 2.5, "nombre_objeto": "zona\u2028linea\u2029parrafo"},
            target=_target(generation=10, suffix="separators-\u2028-\u2029"),
            mode=GenerationBindingModeV1.ALLOW_ADVANCE_WITH_EXACT_TARGET,
            safety=SafetyDisposition.SAFE,
            safety_reason=SafetyReasonCode.HAZARD_AVOIDANCE,
        ),
        _vector(
            "rotate_astral_emoji",
            action_name="girar_avatar",
            parameters={"grados": -45.5},
            target=_target(generation=11, suffix="emoji-\U0001f916-\U0001f680"),
        ),
        _vector(
            "apply_force_max_generation_signed_zero",
            action_name="aplicar_fuerza",
            parameters=signed_zero_force,
            target=_target(generation=maximum_generation, suffix="max-generation"),
            safety_reason=SafetyReasonCode.NO_HAZARD,
        ),
        _vector(
            "apply_force_normalized_diagonal",
            action_name="aplicar_fuerza",
            parameters=diagonal_force,
            target=_target(generation=12, suffix="diagonal"),
            safety=SafetyDisposition.CAUTION,
            safety_reason=SafetyReasonCode.HAZARD_FORCE,
        ),
        _vector(
            "position_double_min_subnormal",
            action_name="navegar_a_posicion",
            parameters={"x": 5e-324, "y": -5e-324, "z": 0.0},
            target=_target(generation=13, suffix="subnormal"),
        ),
        _vector(
            "position_double_max_finite",
            action_name="navegar_a_posicion",
            parameters={"x": float.fromhex("0x1.fffffffffffffp+1023"), "y": 0.0, "z": 1.0},
            target=_target(generation=14, suffix="max-finite"),
        ),
        _vector(
            "navigate_null_target_optionals",
            action_name="navegar_a_objeto",
            parameters={"distancia": None, "nombre_objeto": "Marcador"},
            target=_target(
                generation=15,
                suffix="null-optionals",
                logical_world_id=None,
                producer_object_id=None,
            ),
        ),
        _vector(
            "interact_inspect_unicode",
            action_name="interactuar_objeto_3d",
            parameters={"nombre_objeto": "panel el\u00e9ctrico", "tipo_accion": "inspeccionar"},
            target=_target(generation=16, suffix="inspect"),
            interaction_type="inspeccionar",
            safety=SafetyDisposition.CAUTION,
            safety_reason=SafetyReasonCode.HAZARD_OBSERVATION,
        ),
        _vector(
            "grab_ordinary_float",
            action_name="agarrar_objeto",
            parameters={"nombre_objeto": "Esfera"},
            target=_target(generation=17, suffix="ordinary"),
        ),
        _vector(
            "apply_force_zero_components",
            action_name="aplicar_fuerza",
            parameters={
                "fuerza": 1.0,
                "nombre_objeto": "Caja exploit",
                "vector_x": 1.0,
                "vector_y": 0.0,
                "vector_z": 0.0,
            },
            target=_target(generation=18, suffix="exploit"),
        ),
    ]


def _load_fixture() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        # JSON permits U+0085 literally when ensure_ascii=False. str.splitlines()
        # treats it as a separator, while JSONL is delimited only by LF here.
        for line in FIXTURE.read_text(encoding="utf-8").split("\n")
        if line
    ]


def write_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    serialized = "\n".join(
        json.dumps(vector, ensure_ascii=False, separators=(",", ":"))
        for vector in build_vectors()
    )
    FIXTURE.write_text(serialized + "\n", encoding="utf-8", newline="\n")


def test_fixture_is_exact_python_source_of_truth() -> None:
    assert _load_fixture() == build_vectors()


def test_fixture_covers_every_physical_action_and_numeric_boundaries() -> None:
    vectors = _load_fixture()
    assert {vector["action"]["nombre"] for vector in vectors} == {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "alejarse_de_objeto",
        "girar_avatar",
        "aplicar_fuerza",
    }
    generations = {
        vector["authority_reference"]["generation"] for vector in vectors
    }
    assert {0, 1, (2**63) - 1} <= generations
    signed = next(
        vector
        for vector in vectors
        if vector["case_id"] == "apply_force_max_generation_signed_zero"
    )
    assert math.copysign(1.0, signed["action"]["parametros"]["vector_y"]) == 1.0


def test_fixture_bytes_and_hash_are_self_verifying() -> None:
    for vector in _load_fixture():
        encoded = vector["canonical_binding_json"].encode("utf-8")
        assert encoded.hex() == vector["canonical_binding_utf8_hex"]
        assert sha256(encoded).hexdigest() == vector["expected_sha256"]
        assert (
            vector["authority_reference"]["action_binding_sha256"]
            == vector["expected_sha256"]
        )


if __name__ == "__main__":
    write_fixture()
