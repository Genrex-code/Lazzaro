"""Proyeccion del contrato ROOT-025 a traves del adapter de terminal."""

from __future__ import annotations

from copy import deepcopy

from ai_engine.triage_router import TriageRouter
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


class MotorModel:
    def predict(self, textos):
        return ["MOTOR_SIMPLE"]

    def predict_proba(self, textos):
        return [[0.99, 0.01]]


def _event(origin: str, suffix: str) -> EventoEntrante:
    return EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion=f"root-025-{suffix}",
        detalles={"mensaje": "mueve la caja"},
    )


def test_terminal_adapter_copies_without_mutating_classification() -> None:
    router = TriageRouter()
    router.model_intent = MotorModel()
    event = _event("terminal", "terminal")
    original = router.route_event(event.model_dump(mode="python"), {})
    before = deepcopy(original["classification"])

    effective = Orchestrator._adaptar_triage_para_terminal(event, original)

    assert original["classification"] == before
    assert effective["classification"] == before
    assert effective["route"] == "persona"
    assert effective["terminal_original_route"] == "motor"
    assert before["semantic_intent"] == "MOTOR_SIMPLE"
    assert before["source"] == "MODEL"
    assert before["route"] == "motor"
    assert before["operational_override"] is None


def test_unity_and_sandbox_preserve_legacy_and_classification() -> None:
    router = TriageRouter()
    router.model_intent = MotorModel()
    for origin in ("unity", "sandbox"):
        event = _event(origin, origin)
        original = router.route_event(event.model_dump(mode="python"), {})
        effective = Orchestrator._adaptar_triage_para_terminal(event, original)
        assert effective is original
        assert effective["route"] == "motor"
        assert effective["classification"]["route"] == "motor"


def test_terminal_changes_only_top_level_route_after_movement_override() -> None:
    router = TriageRouter()
    router.model_intent = MotorModel()
    event = _event("terminal", "movement")
    original = router.route_event(
        event.model_dump(mode="python"),
        {"is_moving": True},
    )
    effective = Orchestrator._adaptar_triage_para_terminal(event, original)

    assert original["intent"] == "NOOP"
    assert original["route"] == "noop"
    assert original["classification"]["semantic_intent"] == "MOTOR_SIMPLE"
    assert original["classification"]["route"] == "noop"
    assert original["classification"]["operational_override"] == "MOVEMENT_BLOCKED"
    assert effective["route"] == "persona"
    assert effective["terminal_original_route"] == "noop"
    assert effective["classification"] == original["classification"]
