from __future__ import annotations

from dataclasses import FrozenInstanceError
from itertools import product

import pytest
from pydantic import ValidationError

from ai_engine.motor_resolution import (
    InteractionType,
    MotorActionKindV1,
    MotorDiagnosis,
    MotorResolution,
)
from ai_engine.motor_schemas import (
    ApplyForceActionV1,
    ApplyForceParams,
    CancelTaskActionV1,
    CancelTaskParams,
    GrabObjectActionV1,
    GrabObjectParams,
    InteractObjectActionV1,
    InteractObjectParams,
    MotorActionAdapterV1,
    MotorActionContractError,
    MotorActionContractFailureV1,
    MotorActionFailureCodeV1,
    MotorActionV1,
    MoveAwayActionV1,
    MoveAwayParams,
    NavigateToObjectActionV1,
    NavigateToObjectParams,
    NavigateToPositionActionV1,
    NavigateToPositionParams,
    NoActionParams,
    NoActionV1,
    ReleaseObjectActionV1,
    ReleaseObjectParams,
    RotateAvatarActionV1,
    RotateAvatarParams,
)


VALID_ROWS = (
    (MotorActionKindV1.NONE, {}, NoActionV1, NoActionParams),
    (
        MotorActionKindV1.NAVIGATE_TO_OBJECT,
        {"nombre_objeto": "Panel", "distancia": 0.1},
        NavigateToObjectActionV1,
        NavigateToObjectParams,
    ),
    (
        MotorActionKindV1.NAVIGATE_TO_POSITION,
        {"x": 0.0, "y": -1.0, "z": 3.5},
        NavigateToPositionActionV1,
        NavigateToPositionParams,
    ),
    (
        MotorActionKindV1.INTERACT_OBJECT_3D,
        {"nombre_objeto": "Panel", "tipo_accion": "tocar"},
        InteractObjectActionV1,
        InteractObjectParams,
    ),
    (
        MotorActionKindV1.GRAB_OBJECT,
        {"nombre_objeto": "Caja"},
        GrabObjectActionV1,
        GrabObjectParams,
    ),
    (
        MotorActionKindV1.RELEASE_OBJECT,
        {"fuerza_lanzamiento": 0.0},
        ReleaseObjectActionV1,
        ReleaseObjectParams,
    ),
    (
        MotorActionKindV1.MOVE_AWAY_FROM_OBJECT,
        {"nombre_objeto": "Pared", "distancia": 5.0},
        MoveAwayActionV1,
        MoveAwayParams,
    ),
    (
        MotorActionKindV1.ROTATE_AVATAR,
        {"grados": 0.0},
        RotateAvatarActionV1,
        RotateAvatarParams,
    ),
    (
        MotorActionKindV1.APPLY_FORCE,
        {
            "nombre_objeto": "Caja",
            "vector_x": 0.0,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 100.0,
        },
        ApplyForceActionV1,
        ApplyForceParams,
    ),
    (
        MotorActionKindV1.CANCEL_TASK,
        {
            "motivo": "usuario",
            "cancelar_movimiento": True,
            "cancelar_cola": False,
            "texto_confirmacion": "cancelada",
        },
        CancelTaskActionV1,
        CancelTaskParams,
    ),
)


@pytest.mark.parametrize("action,raw,variant_type,params_type", VALID_ROWS)
def test_valid_action_matrix_is_normalized_and_discriminated(
    action,
    raw,
    variant_type,
    params_type,
) -> None:
    canonical = MotorActionAdapterV1.require(action, raw)

    assert isinstance(canonical, MotorActionV1)
    assert isinstance(canonical.root, variant_type)
    assert type(canonical.params) is params_type
    assert canonical.action is action
    assert canonical.model_dump(mode="json") == {
        "contract_version": 1,
        "action": action.value,
        "params": canonical.parameters_dict(),
    }


@pytest.mark.parametrize("interaction", list(InteractionType))
def test_all_six_root_028_interactions_survive_canonical_serialization(
    interaction: InteractionType,
) -> None:
    canonical = MotorActionAdapterV1.require(
        MotorActionKindV1.INTERACT_OBJECT_3D,
        {"nombre_objeto": "Panel", "tipo_accion": interaction.value},
    )

    assert canonical.params.tipo_accion is interaction
    assert canonical.parameters_dict()["tipo_accion"] == interaction.value


def test_defaults_zero_and_existing_root_002_bounds_are_preserved() -> None:
    assert MotorActionAdapterV1.require("soltar_objeto", {}).parameters_dict() == {
        "fuerza_lanzamiento": 0.0
    }
    assert MotorActionAdapterV1.require(
        "alejarse_de_objeto", {"nombre_objeto": "Caja"}
    ).parameters_dict()["distancia"] == 2.0
    assert MotorActionAdapterV1.require(
        "navegar_a_objeto", {"nombre_objeto": "Caja", "distancia": 10.0}
    ).parameters_dict()["distancia"] == 10.0
    assert MotorActionAdapterV1.require(
        "girar_avatar", {"grados": -360.0}
    ).parameters_dict()["grados"] == -360.0
    assert MotorActionAdapterV1.require(
        "soltar_objeto", {"fuerza_lanzamiento": 20.0}
    ).parameters_dict()["fuerza_lanzamiento"] == 20.0


def test_direct_typed_cross_product_accepts_only_exact_parameter_class() -> None:
    typed_rows = []
    for action, raw, variant_type, params_type in VALID_ROWS[1:]:
        del action
        typed_rows.append((variant_type, params_type.model_validate(raw)))

    for (variant_type, _), (_, params) in product(typed_rows, typed_rows):
        if type(params) is variant_type.PARAMS_TYPE:
            assert type(variant_type(params=params).params) is type(params)
        else:
            with pytest.raises(ValidationError):
                variant_type(params=params)


def test_discriminator_not_shape_selects_semantic_action() -> None:
    navigate = MotorActionAdapterV1.require(
        "navegar_a_objeto", {"nombre_objeto": "Caja"}
    )
    grab = MotorActionAdapterV1.require(
        "agarrar_objeto", {"nombre_objeto": "Caja"}
    )

    assert isinstance(navigate.root, NavigateToObjectActionV1)
    assert isinstance(grab.root, GrabObjectActionV1)


@pytest.mark.parametrize("invalid_version", (True, False, 1.0, "1", 0, 2))
def test_contract_version_is_exact_integer_one(invalid_version) -> None:
    with pytest.raises(ValidationError):
        NoActionV1(
            contract_version=invalid_version,
            params=NoActionParams(),
        )


def test_canonical_variants_and_params_forbid_extras() -> None:
    with pytest.raises(ValidationError):
        NoActionParams.model_validate({"x": 1})
    with pytest.raises(ValidationError):
        NoActionV1.model_validate(
            {"params": NoActionParams(), "unexpected": True}
        )


@pytest.mark.parametrize(
    "action,params,reason",
    (
        ("teleportar", {}, MotorActionFailureCodeV1.UNSUPPORTED_ACTION),
        ("navegar_a_posicion", [], MotorActionFailureCodeV1.PARAMETERS_NOT_OBJECT),
        ("navegar_a_posicion", {"x": 0, "y": 0}, MotorActionFailureCodeV1.REQUIRED_FIELD_MISSING),
        ("navegar_a_posicion", {"x": 0, "y": 0, "z": 0, "foo": 1}, MotorActionFailureCodeV1.EXTRA_FIELD_FORBIDDEN),
        ("interactuar_objeto_3d", {"nombre_objeto": "Panel"}, MotorActionFailureCodeV1.REQUIRED_FIELD_MISSING),
        ("interactuar_objeto_3d", {"nombre_objeto": "Panel", "tipo_accion": None}, MotorActionFailureCodeV1.FIELD_TYPE_INVALID),
        ("interactuar_objeto_3d", {"nombre_objeto": "Panel", "tipo_accion": "romper"}, MotorActionFailureCodeV1.FIELD_TYPE_INVALID),
        ("agarrar_objeto", {"nombre_objeto": "Caja", "tipo_accion": "tocar"}, MotorActionFailureCodeV1.EXTRA_FIELD_FORBIDDEN),
        ("none", {"x": 1}, MotorActionFailureCodeV1.EXTRA_FIELD_FORBIDDEN),
        ("cancelar_tarea", {"arbitrario": True}, MotorActionFailureCodeV1.EXTRA_FIELD_FORBIDDEN),
        ("navegar_a_objeto", {"x": 1, "y": 2, "z": 3}, MotorActionFailureCodeV1.REQUIRED_FIELD_MISSING),
        ("navegar_a_posicion", {"nombre_objeto": "Caja"}, MotorActionFailureCodeV1.REQUIRED_FIELD_MISSING),
        ("navegar_a_posicion", {"x": True, "y": 0, "z": 0}, MotorActionFailureCodeV1.FIELD_VALUE_INVALID),
        ("navegar_a_posicion", {"x": float("nan"), "y": 0, "z": 0}, MotorActionFailureCodeV1.FIELD_VALUE_INVALID),
        ("navegar_a_posicion", {"x": float("inf"), "y": 0, "z": 0}, MotorActionFailureCodeV1.FIELD_VALUE_INVALID),
        ("navegar_a_posicion", {"x": float("-inf"), "y": 0, "z": 0}, MotorActionFailureCodeV1.FIELD_VALUE_INVALID),
        ("cancelar_tarea", {"cancelar_movimiento": "false"}, MotorActionFailureCodeV1.FIELD_TYPE_INVALID),
        ("cancelar_tarea", {"cancelar_cola": 1}, MotorActionFailureCodeV1.FIELD_TYPE_INVALID),
    ),
)
def test_invalid_legacy_matrix_returns_stable_reason(action, params, reason) -> None:
    result = MotorActionAdapterV1.parse(action, params)

    assert isinstance(result, MotorActionContractFailureV1)
    assert result.reason_code is reason


def test_wrong_typed_parameter_model_has_explicit_mismatch_reason() -> None:
    result = MotorActionAdapterV1.parse(
        MotorActionKindV1.GRAB_OBJECT,
        NavigateToObjectParams(nombre_objeto="Caja"),
    )

    assert isinstance(result, MotorActionContractFailureV1)
    assert result.reason_code is MotorActionFailureCodeV1.ACTION_PARAMETER_MISMATCH


def test_root_002_numeric_strings_are_normalized_by_legacy_adapter() -> None:
    canonical = MotorActionAdapterV1.require(
        "navegar_a_posicion", {"x": "1", "y": "-1", "z": "3.5"}
    )

    assert canonical.parameters_dict() == {"x": 1.0, "y": -1.0, "z": 3.5}
    assert all(type(value) is float for value in canonical.parameters_dict().values())


@pytest.mark.parametrize(
    "factory",
    (
        lambda: NavigateToPositionParams(x="1", y=0.0, z=0.0),
        lambda: RotateAvatarParams(grados="90"),
        lambda: ApplyForceParams(
            nombre_objeto="Caja",
            vector_x="1",
            vector_y=0.0,
            vector_z=0.0,
            fuerza=1.0,
        ),
    ),
)
def test_direct_canonical_numeric_string_construction_is_rejected(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_canonical_action_and_params_are_practically_immutable() -> None:
    raw = {"nombre_objeto": "Caja", "distancia": 2.0}
    canonical = MotorActionAdapterV1.require("alejarse_de_objeto", raw)
    raw["distancia"] = 4.0

    assert canonical.parameters_dict()["distancia"] == 2.0
    with pytest.raises(ValidationError):
        canonical.params.distancia = 4.0
    with pytest.raises((AttributeError, ValidationError)):
        canonical.root.action = MotorActionKindV1.NONE
    with pytest.raises(MotorActionContractError) as action_update:
        canonical.model_copy(update={"root": NoActionV1(params=NoActionParams())})
    with pytest.raises(MotorActionContractError) as params_update:
        canonical.params.model_copy(update={"distancia": 4.0})
    assert action_update.value.reason_code is MotorActionFailureCodeV1.MUTATION_FORBIDDEN
    assert params_update.value.reason_code is MotorActionFailureCodeV1.MUTATION_FORBIDDEN


@pytest.mark.parametrize(
    "factory",
    (
        lambda: MotorDiagnosis(estado="bogus"),
        lambda: MotorDiagnosis(estado="lista", accion="teleportar", parametros={}),
        lambda: MotorResolution(status="bogus"),
        lambda: MotorResolution(status="ready", action="teleportar"),
        lambda: MotorResolution(
            status="ready", action="interactuar_objeto_3d", interaction_type=None
        ),
        lambda: MotorResolution(
            status="ready", action="interactuar_objeto_3d", interaction_type="romper"
        ),
    ),
)
def test_motor_dataclasses_enforce_runtime_vocabulary(factory) -> None:
    with pytest.raises((TypeError, ValueError, FrozenInstanceError)):
        factory()


def test_motor_resolution_control_fields_cannot_mutate_after_validation() -> None:
    resolution = MotorResolution(
        status="ready",
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )

    with pytest.raises(FrozenInstanceError):
        resolution.status = "bogus"
    with pytest.raises(FrozenInstanceError):
        resolution.action = "teleportar"
    with pytest.raises(FrozenInstanceError):
        resolution.interaction_type = InteractionType.USAR


def test_motor_diagnosis_exposes_only_immutable_snapshots_and_detached_serialization() -> None:
    resolution = MotorResolution(
        status="ready",
        action="alejarse_de_objeto",
        target_id="pared",
        target_display_name="Pared",
        legacy_nombre_objeto="Pared",
    )
    original_params = {"nombre_objeto": "Pared", "distancia": 2.0}
    original_metadata = resolution.to_metadata()
    diagnosis = MotorDiagnosis(
        estado="lista",
        accion="alejarse_de_objeto",
        parametros=original_params,
        metadata=original_metadata,
        resolucion_motor=resolution,
    )

    original_params["distancia"] = 5.0
    original_metadata["motor_resolution"]["status"] = "unsafe"
    assert diagnosis.parametros["distancia"] == 2.0
    assert diagnosis.metadata["motor_resolution"]["status"] == "ready"

    with pytest.raises(TypeError):
        diagnosis.parametros["distancia"] = 4.0
    with pytest.raises(TypeError):
        diagnosis.metadata["motor_resolution"]["status"] = "bogus"

    detached = diagnosis.to_dict()
    detached["parametros"]["distancia"] = 4.0
    detached["metadata"]["motor_resolution"]["status"] = "bogus"
    assert diagnosis.parametros["distancia"] == 2.0
    assert diagnosis.metadata["motor_resolution"]["status"] == "ready"
