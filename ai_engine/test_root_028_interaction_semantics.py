from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_engine.affordance_resolver import AffordanceResolver
from ai_engine.fuzzy_object_resolver import FuzzyObjectResolver
from ai_engine.motor_engine import MotorEngine
from ai_engine.motor_resolution import CandidateMatch, InteractionType, MotorResolution
from ai_engine.motor_schemas import InteractObjectParams
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot
from ai_engine.decision_outcome import DecisionDisposition, DecisionReasonCode
from core.orchestrator import Orchestrator
from tools.simulated.interaccion_sim import InteractuarObjeto3DParams


CANONICAL_VALUES = {
    "mirar",
    "tocar",
    "inspeccionar",
    "golpear",
    "usar",
    "activar",
}


def _snapshot(*objects: PerceivedObject) -> PerceptionSnapshot:
    return PerceptionSnapshot(snapshot_id="root-028", source="unity", objects=list(objects))


def _perception(
    *,
    object_id: str = "Panel",
    aliases: list[str] | None = None,
    affordances: list[str] | None = None,
) -> dict:
    return {
        "snapshot_id": "root-028",
        "source": "unity",
        "objetos_normalizados": [
            {
                "id": object_id,
                "display_name": object_id,
                "nombre": object_id,
                "aliases": aliases or ["panel"],
                "affordances": affordances or ["mirar", "tocar", "inspeccionable", "activable"],
            }
        ],
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mira el panel", InteractionType.MIRAR),
        ("mirar el panel", InteractionType.MIRAR),
        ("observa el panel", InteractionType.MIRAR),
        ("observar el panel", InteractionType.MIRAR),
        ("inspecciona el panel", InteractionType.INSPECCIONAR),
        ("inspeccionar el panel", InteractionType.INSPECCIONAR),
        ("toca el panel", InteractionType.TOCAR),
        ("tocar el panel", InteractionType.TOCAR),
        ("golpea el panel", InteractionType.GOLPEAR),
        ("golpear el panel", InteractionType.GOLPEAR),
        ("usa el panel", InteractionType.USAR),
        ("usar el panel", InteractionType.USAR),
        ("activa el panel", InteractionType.ACTIVAR),
        ("activar el panel", InteractionType.ACTIVAR),
        ("¡MIRA, EL PANEL!", InteractionType.MIRAR),
        ("observá el panel", InteractionType.MIRAR),
        ("mira y observa el panel", InteractionType.MIRAR),
        ("mira, mira el panel", InteractionType.MIRAR),
    ],
)
def test_text_parser_maps_only_explicit_boundary_evidence(
    text: str,
    expected: InteractionType,
) -> None:
    assert MotorEngine._inferir_tipo_interaccion(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        "interactúa con el panel",
        "haz algo con el panel",
        "admirar el panel",
        "observador del panel",
        "inspeccionador",
        "retocar la pintura",
        "golpeador",
        "causa problemas",
        "desactivar la alarma",
        "activamente",
        "mira y toca el panel",
    ],
)
def test_text_parser_never_guesses_generic_substrings_or_conflicts(text: str) -> None:
    assert MotorEngine._inferir_tipo_interaccion(text) is None


def test_generic_interaction_is_an_action_without_a_fabricated_subtype() -> None:
    requested, interaction_type = MotorEngine._analizar_interaccion_textual(
        "interactúa con el panel"
    )
    assert requested is True
    assert interaction_type is None


def test_look_around_remains_rotation() -> None:
    diagnosis = MotorEngine().diagnosticar_comando_usuario(
        "mira alrededor",
        percepcion={},
    )
    assert diagnosis.estado == "lista"
    assert diagnosis.accion == "girar_avatar"


@pytest.mark.parametrize("interaction_type", list(InteractionType))
def test_motor_resolution_serializes_canonical_values(
    interaction_type: InteractionType,
) -> None:
    resolution = MotorResolution(
        status="not_found",
        action="interactuar_objeto_3d",
        interaction_type=interaction_type,
        reason="probe",
    )
    assert resolution.to_dict()["interaction_type"] == interaction_type.value
    assert "InteractionType." not in str(resolution.to_dict())


def test_motor_resolution_serializes_absence_as_none() -> None:
    resolution = MotorResolution(
        status="requires_clarification",
        action="interactuar_objeto_3d",
        interaction_type=None,
        reason="probe",
    )
    assert resolution.to_dict()["interaction_type"] is None


def test_ready_interaction_resolution_rejects_missing_type() -> None:
    with pytest.raises(
        ValueError,
        match="ready interactuar_objeto_3d resolution requires interaction_type",
    ):
        MotorResolution(
            status="ready",
            action="interactuar_objeto_3d",
            interaction_type=None,
            reason="invalid fixture",
        )


def test_nonready_interaction_resolution_may_omit_type() -> None:
    resolution = MotorResolution(
        status="requires_clarification",
        action="interactuar_objeto_3d",
        interaction_type=None,
        reason="incomplete semantics",
    )
    assert resolution.interaction_type is None


def test_noninteraction_resolution_still_rejects_interaction_type() -> None:
    with pytest.raises(
        ValueError,
        match="interaction_type is only valid for interactuar_objeto_3d",
    ):
        MotorResolution(
            status="ready",
            action="navegar_a_objeto",
            interaction_type=InteractionType.TOCAR,
            reason="invalid fixture",
        )


def test_motor_and_tool_schemas_require_the_same_closed_vocabulary() -> None:
    assert InteractObjectParams.model_fields["tipo_accion"].is_required()
    assert InteractuarObjeto3DParams.model_fields["tipo_accion"].is_required()

    for value in CANONICAL_VALUES:
        motor = InteractObjectParams.model_validate(
            {"nombre_objeto": "Panel", "tipo_accion": value}
        )
        tool = InteractuarObjeto3DParams.model_validate(
            {"nombre_objeto": "Panel", "tipo_accion": value}
        )
        assert motor.model_dump()["tipo_accion"] == value
        assert tool.tipo_accion.value == value

    for invalid in (None, "", "xyz", True, 123, "MIRAR", " mirar ", "observa"):
        with pytest.raises(ValidationError):
            InteractObjectParams.model_validate(
                {"nombre_objeto": "Panel", "tipo_accion": invalid}
            )
        with pytest.raises(ValidationError):
            InteractuarObjeto3DParams.model_validate(
                {"nombre_objeto": "Panel", "tipo_accion": invalid}
            )


class CountingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("invalid structured input reached Fuzzy")


@pytest.mark.parametrize("invalid", [None, "", "xyz", True, 123, "MIRAR", " mirar ", "observa"])
def test_present_invalid_structured_type_fails_before_fuzzy_without_lexical_rescue(
    invalid: object,
) -> None:
    engine = MotorEngine()
    counter = CountingResolver()
    engine.fuzzy_resolver = counter  # type: ignore[assignment]

    result = engine.procesar_comando_motor(
        "interactuar_objeto_3d",
        {"nombre_objeto": "Panel", "tipo_accion": invalid},
        _perception(),
        contexto_motor={"user_query": "toca el panel"},
    )

    assert result["accion"] == "none"
    assert result["metadata"] == {}
    assert counter.calls == 0
    projected = Orchestrator._action_response_desde_motor(result, pensamiento="")
    assert projected.outcome.disposition is DecisionDisposition.INVALID
    assert projected.outcome.reason_codes == (DecisionReasonCode.INVALID_PARAMETERS,)


def test_absent_structured_type_may_use_explicit_text_once() -> None:
    result = MotorEngine().procesar_comando_motor(
        "interactuar_objeto_3d",
        {"nombre_objeto": "Panel"},
        _perception(affordances=["tocar"]),
        contexto_motor={"user_query": "toca el panel"},
    )
    assert result["accion"] == "interactuar_objeto_3d"
    assert result["parametros"]["tipo_accion"] == "tocar"
    assert result["metadata"]["motor_resolution"]["interaction_type"] == "tocar"


def test_structured_type_overrides_conflicting_human_context() -> None:
    result = MotorEngine().procesar_comando_motor(
        "interactuar_objeto_3d",
        {"nombre_objeto": "Panel", "tipo_accion": "golpear"},
        _perception(affordances=["tocar"]),
        contexto_motor={"user_query": "mira el panel"},
    )
    assert result["accion"] == "interactuar_objeto_3d"
    assert result["parametros"]["tipo_accion"] == "golpear"
    assert result["metadata"]["motor_resolution"]["interaction_type"] == "golpear"


def test_direct_and_structured_touch_converge_after_grounding() -> None:
    perception = _perception(affordances=["tocar"])
    engine = MotorEngine()

    direct = engine.diagnosticar_comando_usuario(
        "toca el panel",
        perception,
        intent="INTERACTION",
    )
    structured = engine.procesar_comando_motor(
        "interactuar_objeto_3d",
        {"nombre_objeto": "Panel", "tipo_accion": "tocar"},
        perception,
        intent="INTERACTION",
        contexto_motor={"user_query": "toca el panel"},
    )

    assert direct.estado == "lista"
    assert direct.parametros == structured["parametros"]
    assert direct.metadata["motor_resolution"]["target_id"] == structured["metadata"]["motor_resolution"]["target_id"]
    assert direct.metadata["motor_resolution"]["interaction_type"] == "tocar"
    direct_decision = direct.metadata["motor_resolution"]["candidates"][0]["affordance_decision"]
    structured_decision = structured["metadata"]["motor_resolution"]["candidates"][0]["affordance_decision"]
    assert direct_decision == structured_decision


def test_generic_nonhazard_is_safe_but_incomplete_and_nonphysical() -> None:
    diagnosis = MotorEngine().diagnosticar_comando_usuario(
        "interactúa con el panel",
        _perception(),
        intent="INTERACTION",
    )
    assert diagnosis.estado == "requires_clarification"
    resolution = diagnosis.metadata["motor_resolution"]
    assert resolution["interaction_type"] is None
    assert diagnosis.metadata["motor_audit"]["status_reason"] == "interaction_type_missing"
    decision = resolution["candidates"][0]["affordance_decision"]
    assert decision["safety_assessment"]["disposition"] == "SAFE"
    assert diagnosis.parametros is None


@pytest.mark.parametrize("affordances", [["empujable"], ["agarrable"], []])
def test_generic_nonhazard_affordance_mismatch_still_requires_interaction_type(
    affordances: list[str],
) -> None:
    result = FuzzyObjectResolver().resolve(
        "panel",
        _snapshot(
            PerceivedObject(
                id="Panel",
                display_name="Panel",
                aliases=["panel"],
                affordances=affordances,
            )
        ),
        action="interactuar_objeto_3d",
        interaction_type=None,
    )

    assert result.status == "requires_clarification"
    assert result.interaction_type is None
    assert result.audit["status_reason"] == "interaction_type_missing"


def test_generic_hazard_without_interaction_affordance_preserves_safety_block() -> None:
    result = FuzzyObjectResolver().resolve(
        "fuego",
        _snapshot(
            PerceivedObject(
                id="Fire",
                display_name="Fire",
                aliases=["fuego"],
                affordances=["peligro"],
            )
        ),
        action="interactuar_objeto_3d",
        interaction_type=None,
    )

    assert result.status == "unsafe"
    assert result.interaction_type is None
    assert result.audit["status_reason"] == "affordance_block"
    assessment = result.candidates[0].affordance_decision["safety_assessment"]
    assert assessment["disposition"] == "INDETERMINATE"
    assert assessment["reason_code"] == "HAZARD_INTERACTION_UNSPECIFIED"


def test_absent_structured_type_without_lexical_evidence_is_clarification() -> None:
    result = MotorEngine().procesar_comando_motor(
        "interactuar_objeto_3d",
        {"nombre_objeto": "Panel"},
        _perception(),
        contexto_motor={"user_query": "interactúa con el panel"},
    )
    assert result["accion"] == "none"
    assert result["metadata"]["motor_resolution"]["status"] == "requires_clarification"
    assert result["metadata"]["motor_resolution"]["interaction_type"] is None


def test_legacy_path_preserves_explicit_type_and_never_fabricates_generic_type() -> None:
    perception = {"objetos_visibles": [{"nombre": "Panel"}]}
    engine = MotorEngine()

    explicit = engine.diagnosticar_comando_usuario(
        "toca el panel",
        perception,
        intent="INTERACTION",
    )
    generic = engine.diagnosticar_comando_usuario(
        "interactúa con el panel",
        perception,
        intent="INTERACTION",
    )

    assert explicit.estado == "lista"
    assert explicit.parametros == {"nombre_objeto": "Panel", "tipo_accion": "tocar"}
    assert explicit.metadata["motor_resolution"]["interaction_type"] == "tocar"
    assert generic.estado == "requires_clarification"
    assert generic.parametros is None
    assert generic.metadata["motor_resolution"]["interaction_type"] is None


def test_generic_hazard_is_indeterminate_unsafe_and_nonphysical() -> None:
    diagnosis = MotorEngine().diagnosticar_comando_usuario(
        "interactúa con el fuego",
        _perception(object_id="Fire", aliases=["fuego"], affordances=["mirar", "peligro"]),
        intent="INTERACTION",
    )
    assert diagnosis.estado == "unsafe"
    resolution = diagnosis.metadata["motor_resolution"]
    assert resolution["interaction_type"] is None
    assessment = resolution["candidates"][0]["affordance_decision"]["safety_assessment"]
    assert assessment == {
        "contract_version": 1,
        "disposition": "INDETERMINATE",
        "reason_code": "HAZARD_INTERACTION_UNSPECIFIED",
        "ranking_penalty": 20.0,
    }


@pytest.mark.parametrize(
    ("interaction_type", "affordances", "disposition", "reason", "penalty"),
    [
        (InteractionType.MIRAR, ["mirar", "peligro"], "CAUTION", "HAZARD_OBSERVATION", 5.0),
        (InteractionType.INSPECCIONAR, ["inspeccionable", "peligro"], "CAUTION", "HAZARD_OBSERVATION", 5.0),
        (InteractionType.TOCAR, ["tocar", "peligro"], "UNSAFE", "HAZARD_CONTACT", 20.0),
        (InteractionType.GOLPEAR, ["tocar", "peligro"], "UNSAFE", "HAZARD_CONTACT", 20.0),
        (InteractionType.USAR, ["activable", "peligro"], "UNSAFE", "HAZARD_MANIPULATION", 20.0),
        (InteractionType.ACTIVAR, ["activable", "peligro"], "UNSAFE", "HAZARD_MANIPULATION", 20.0),
    ],
)
def test_root_027_hazard_matrix_receives_explicit_root_028_evidence(
    interaction_type: InteractionType,
    affordances: list[str],
    disposition: str,
    reason: str,
    penalty: float,
) -> None:
    result = FuzzyObjectResolver().resolve(
        "fuego",
        _snapshot(
            PerceivedObject(
                id="Fire",
                display_name="Fire",
                aliases=["fuego"],
                affordances=affordances,
            )
        ),
        action="interactuar_objeto_3d",
        interaction_type=interaction_type,
    )
    assessment = result.candidates[0].affordance_decision["safety_assessment"]
    assert assessment == {
        "contract_version": 1,
        "disposition": disposition,
        "reason_code": reason,
        "ranking_penalty": penalty,
    }


def test_explicit_compatible_and_incompatible_nonhazard_affordances() -> None:
    resolver = FuzzyObjectResolver()
    compatible = resolver.resolve(
        "panel",
        _snapshot(PerceivedObject(id="Panel", display_name="Panel", aliases=["panel"], affordances=["tocar"])),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    incompatible = resolver.resolve(
        "panel",
        _snapshot(PerceivedObject(id="Panel", display_name="Panel", aliases=["panel"], affordances=["mirar"])),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    assert compatible.status == "ready"
    assert incompatible.status == "not_allowed"
    assert compatible.interaction_type is InteractionType.TOCAR
    assert incompatible.interaction_type is InteractionType.TOCAR


@pytest.mark.parametrize(
    ("text", "interaction_type", "affordances"),
    [
        ("mira el panel", InteractionType.MIRAR, ["mirar"]),
        ("toca el panel", InteractionType.TOCAR, ["tocar"]),
        ("inspecciona el panel", InteractionType.INSPECCIONAR, ["inspeccionable"]),
        ("golpea el panel", InteractionType.GOLPEAR, ["tocar"]),
        ("usa el panel", InteractionType.USAR, ["activable"]),
        ("activa el panel", InteractionType.ACTIVAR, ["activable"]),
    ],
)
def test_interaction_type_survives_python_end_to_end_projection(
    text: str,
    interaction_type: InteractionType,
    affordances: list[str],
) -> None:
    engine = MotorEngine()
    diagnosis = engine.diagnosticar_comando_usuario(
        text,
        _perception(affordances=affordances),
        intent="INTERACTION",
    )
    assert diagnosis.estado == "lista"
    assert diagnosis.resolucion_motor is not None

    motor_action = engine.procesar_diagnostico_motor(diagnosis)
    action_response = Orchestrator._action_response_desde_motor(
        motor_action,
        pensamiento="",
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    outgoing = orchestrator._a_accion_saliente(action_response, "autonoma")

    expected = interaction_type.value
    assert diagnosis.resolucion_motor.interaction_type is interaction_type
    assert diagnosis.metadata["motor_resolution"]["interaction_type"] == expected
    assert motor_action["parametros"]["tipo_accion"] == expected
    assert action_response.parametros["tipo_accion"] == expected
    assert outgoing.parametros["tipo_accion"] == expected


class SpyAffordances(AffordanceResolver):
    def __init__(self) -> None:
        self.validate_types: list[str | None] = []
        self.score_types: list[str | None] = []

    def validate(self, obj, action, *, interaction_type=None):
        self.validate_types.append(interaction_type)
        return super().validate(obj, action, interaction_type=interaction_type)

    def score(self, obj, *, action=None, requested_affordances=None, interaction_type=None):
        self.score_types.append(interaction_type)
        return super().score(
            obj,
            action=action,
            requested_affordances=requested_affordances,
            interaction_type=interaction_type,
        )


def test_validate_score_and_safety_receive_one_identical_subtype_for_every_candidate() -> None:
    spy = SpyAffordances()
    resolver = FuzzyObjectResolver(affordance_resolver=spy)
    result = resolver.resolve(
        "panel",
        _snapshot(
            PerceivedObject(id="PanelA", display_name="PanelA", aliases=["panel"], affordances=["tocar"]),
            PerceivedObject(id="PanelB", display_name="PanelB", aliases=["panel"], affordances=["tocar"]),
        ),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    assert spy.validate_types == ["tocar", "tocar"]
    assert spy.score_types == spy.validate_types
    assert result.interaction_type is InteractionType.TOCAR
    for candidate in result.candidates:
        assert candidate.affordance_decision["safety_assessment"]["reason_code"] == "NO_HAZARD"


def test_nonready_resolutions_preserve_valid_interaction_type() -> None:
    resolver = FuzzyObjectResolver()
    not_found = resolver.resolve(
        "panel",
        _snapshot(),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    clarification = resolver.resolve(
        "panel",
        _snapshot(
            PerceivedObject(id="PanelA", display_name="PanelA", aliases=["panel"], affordances=["tocar"]),
            PerceivedObject(id="PanelB", display_name="PanelB", aliases=["panel"], affordances=["tocar"]),
        ),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    correction = resolver.resolve(
        "panel rojo",
        _snapshot(PerceivedObject(id="Panel", display_name="Panel", aliases=["panel"], affordances=["tocar"])),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    unsafe = resolver.resolve(
        "fuego",
        _snapshot(PerceivedObject(id="Fire", display_name="Fire", aliases=["fuego"], affordances=["tocar", "peligro"])),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    for result in (not_found, clarification, correction, unsafe):
        assert result.interaction_type is InteractionType.TOCAR
    assert not_found.status == "not_found"
    assert clarification.status == "requires_clarification"
    assert correction.candidate_correction is not None
    assert unsafe.status == "unsafe"


def test_score_100_cannot_override_missing_unsafe_or_indeterminate() -> None:
    resolver = FuzzyObjectResolver()
    panel = PerceivedObject(id="Panel", display_name="Panel", aliases=["panel"], affordances=["mirar"])
    safe_decision = resolver.affordances.validate(panel, "interactuar_objeto_3d").to_dict()
    score_100 = CandidateMatch(
        target_id="Panel",
        display_name="Panel",
        confidence=100.0,
        affordance_decision=safe_decision,
        score_components={"name_score": 100.0, "alias_score": 100.0},
    )
    resolver._score_objects = lambda *args, **kwargs: [score_100]  # type: ignore[method-assign]
    missing = resolver.resolve(
        "panel",
        _snapshot(panel),
        action="interactuar_objeto_3d",
        interaction_type=None,
    )
    assert missing.status == "requires_clarification"

    fire = PerceivedObject(id="Fire", display_name="Fire", aliases=["fuego"], affordances=["peligro", "tocar"])
    for subtype in (InteractionType.TOCAR, None):
        decision = resolver.affordances.validate(
            fire,
            "interactuar_objeto_3d",
            interaction_type=subtype.value if subtype else None,
        ).to_dict()
        candidate = CandidateMatch(
            target_id="Fire",
            display_name="Fire",
            confidence=100.0,
            affordance_decision=decision,
            score_components={"name_score": 100.0, "alias_score": 100.0},
        )
        assert resolver._blocking_affordance_status(candidate) == "unsafe"


def test_alias_and_candidate_order_never_change_interaction_type() -> None:
    resolver = FuzzyObjectResolver()
    panel = PerceivedObject(
        id="Panel",
        display_name="Panel",
        aliases=["panel", "tablero"],
        affordances=["tocar"],
    )
    other = PerceivedObject(
        id="Other",
        display_name="Other",
        aliases=["otro"],
        affordances=["tocar"],
    )
    first = resolver.resolve(
        "panel",
        _snapshot(panel, other),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    aliased = resolver.resolve(
        "tablero",
        _snapshot(other, panel),
        action="interactuar_objeto_3d",
        interaction_type=InteractionType.TOCAR,
    )
    assert first.target_id == aliased.target_id == "Panel"
    assert first.interaction_type is aliased.interaction_type is InteractionType.TOCAR


def test_unity_interaction_boundary_is_static_fail_closed_and_vocabulary_matches() -> None:
    source = (Path(__file__).resolve().parents[1] / "unity_client" / "LazzaroAgent.cs").read_text(
        encoding="utf-8"
    )
    start = source.index("private void EjecutarInteraccion")
    end = source.index("private void EjecutarAgarrar", start)
    method = source[start:end]
    assert '?? "mirar"' not in method
    assert '"interaction_type_missing"' in method
    assert '"interaction_type_unsupported"' in method
    assert method.index('"interaction_type_missing"') < method.index("TryBuscarObjetoCercano")
    assert method.index('"interaction_type_unsupported"') < method.index("TryBuscarObjetoCercano")
    assert method.count("return;") >= 3
    assert method.index("TryBuscarObjetoCercano") < method.index("TryInteract")

    vocabulary_start = source.index("private static bool EsTipoInteraccionCanonico")
    vocabulary_end = source.index("private bool TryBuscarObjetoCercano", vocabulary_start)
    unity_values = set(re.findall(r'tipo == "([a-z]+)"', source[vocabulary_start:vocabulary_end]))
    assert unity_values == CANONICAL_VALUES == {item.value for item in InteractionType}
