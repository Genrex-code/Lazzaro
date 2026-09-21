"""Pruebas de proyección route/effective_route para ROOT-024."""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.triage_router import TriageRouter
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class ContradictoryMotorModel:
    def predict(self, textos):
        return ["MOTOR_SIMPLE"]

    def predict_proba(self, textos):
        return [[0.99, 0.01]]


def _event(origin: str, text: str, suffix: str) -> EventoEntrante:
    return EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion=f"root-024-{suffix}",
        detalles={"mensaje": text},
    )


def _triage(router: TriageRouter, event: EventoEntrante) -> dict:
    return router.route_event(event.model_dump(), estado_agente={})


def test_unity_and_sandbox_preserve_direct_motor_route() -> None:
    router = TriageRouter()
    router.model_intent = None
    for origin in ("unity", "sandbox"):
        event = _event(origin, "mueve la caja", f"direct-{origin}")
        original = _triage(router, event)
        effective = Orchestrator._adaptar_triage_para_terminal(event, original)
        assert_true(original["intent"] == "MOTOR_SIMPLE", "Intent Motor inesperado.")
        assert_true(original["route"] == "motor", "La ruta original debe ser motor.")
        assert_true(effective["route"] == "motor", "Unity/sandbox no deben reconducir Motor.")


def test_terminal_preserves_original_motor_and_projects_persona() -> None:
    router = TriageRouter()
    router.model_intent = None
    event = _event("terminal", "mueve la caja", "terminal-direct")
    original = _triage(router, event)
    effective = Orchestrator._adaptar_triage_para_terminal(event, original)

    assert_true(original["route"] == "motor", "Triage debe conservar la ruta original.")
    assert_true(effective["route"] == "persona", "Terminal debe usar Persona efectivamente.")
    assert_true(
        effective["terminal_original_route"] == "motor",
        "La proyección debe conservar el origen de la adaptación.",
    )
    assert_true(original.get("terminal_original_route") is None, "No debe mutar el original.")


def test_protected_and_url_keywords_never_project_from_motor() -> None:
    router = TriageRouter()
    router.model_intent = ContradictoryMotorModel()
    cases = (
        ('"mueve la caja"', "quoted"),
        ("https://x.test/mover/abre", "url"),
        ('"guarda lo que hemos hablado"', "quoted-save"),
    )
    for text, suffix in cases:
        event = _event("terminal", text, suffix)
        original = _triage(router, event)
        effective = Orchestrator._adaptar_triage_para_terminal(event, original)
        if suffix != "quoted-save":
            assert_true(original["route"] != "motor", f"Frontera léxica violada: {text}")
        assert_true(effective["route"] == "persona", "Terminal debe seguir conversacional.")
        if suffix == "quoted-save":
            assert_true(
                original["intent"] != "CONVERSATION_SAVE",
                "El control citado no debe saltarse protected_save.",
            )


def main() -> None:
    test_unity_and_sandbox_preserve_direct_motor_route()
    test_terminal_preserves_original_motor_and_projects_persona()
    test_protected_and_url_keywords_never_project_from_motor()
    print("[OK] ROOT-024 projection tests passed.")


if __name__ == "__main__":
    main()
