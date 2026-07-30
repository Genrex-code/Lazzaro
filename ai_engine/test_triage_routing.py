from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.triage_router import TriageRouter
from ai_engine.routing_rules import MANUAL_CONVERSATION_SAVE_PHRASES


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeOracleModel:
    def predict(self, textos):
        return ["ORACLE_QUERY"]

    def predict_proba(self, textos):
        return [[0.99, 0.01]]


class FakeConversationSaveModel:
    def predict(self, textos):
        return ["CONVERSATION_SAVE"]

    def predict_proba(self, textos):
        return [[0.99, 0.01]]


def _route(router: TriageRouter, mensaje: str) -> dict:
    return router.route_event(
        {
            "tipo_evento": "comando_usuario",
            "detalles": {"mensaje": mensaje},
        },
        estado_agente={},
    )


def test_authority_rebuke_stays_in_persona_even_if_ml_says_oracle() -> None:
    router = TriageRouter()
    router.model_intent = FakeOracleModel()

    decision = _route(
        router,
        "me estas diciendo a mi el que escribio todo tu codigo que no toque lo que no entienda? nina boba",
    )

    assert_true(decision["intent"] == "SOCIAL", "El regano relacional debe ser SOCIAL.")
    assert_true(decision["route"] == "persona", "El regano relacional debe ir a Persona.")
    assert_true(decision["confidence"] == 0.95, "El guardrail debe tener confianza alta.")


def test_creator_authority_stays_in_persona() -> None:
    router = TriageRouter()
    router.model_intent = None

    decision = _route(router, "nina el desarrollador soy yo boba")
    assert_true(decision["intent"] == "SOCIAL", "La autoridad del desarrollador debe ser SOCIAL.")
    assert_true(decision["route"] == "persona", "La autoridad del desarrollador debe ir a Persona.")

    decision = _route(router, "soy tu creador y quiero hablar contigo")
    assert_true(decision["intent"] == "SOCIAL", "La identidad del creador debe ser SOCIAL.")
    assert_true(decision["route"] == "persona", "La identidad del creador debe ir a Persona.")


def test_explicit_technical_queries_still_go_to_oracle() -> None:
    router = TriageRouter()
    router.model_intent = None

    decision = _route(router, "explicame este codigo")
    assert_true(decision["intent"] == "ORACLE_QUERY", "Una consulta de codigo debe ir al Oraculo.")
    assert_true(decision["route"] == "oracle", "Una consulta de codigo debe usar ruta oracle.")

    decision = _route(router, "debuggea este error")
    assert_true(decision["intent"] == "ORACLE_QUERY", "Debuggear un error debe ir al Oraculo.")
    assert_true(decision["route"] == "oracle", "Debuggear un error debe usar ruta oracle.")


def test_manual_conversation_save_uses_a_dedicated_intent() -> None:
    router = TriageRouter()
    router.model_intent = FakeOracleModel()
    assert_true(
        MANUAL_CONVERSATION_SAVE_PHRASES
        == {
            "guarda lo que hemos hablado",
            "guarda lo que tengas en ram en tu mente",
        },
        "La allowlist base debe conservar exactamente las dos frases aprobadas.",
    )

    for frase in MANUAL_CONVERSATION_SAVE_PHRASES:
        decision = _route(router, f"  ¡{frase.upper()}!  ")
        assert_true(
            decision["intent"] == "CONVERSATION_SAVE",
            f"La frase aprobada debe activar guardado manual: {frase}",
        )
        assert_true(decision["route"] == "persona", "El guardado debe permanecer en Capa 2.")
        assert_true(
            decision["confidence"] == 1.0,
            "La allowlist determinista debe ganar incluso si el ML dice Oraculo.",
        )


def test_heuristic_memory_updates_do_not_save_the_whole_ram() -> None:
    router = TriageRouter()
    router.model_intent = None

    no_autorizadas = (
        "recuerda que mi cita es mañana",
        "¿recuerdas lo que hablamos?",
        "no guardes lo que hemos hablado",
        "guarda el archivo",
        "memoriza",
        "/guardar",
    )
    for frase in no_autorizadas:
        decision = _route(router, frase)
        assert_true(
            decision["intent"] != "CONVERSATION_SAVE",
            f"No debe guardar toda la RAM para: {frase}",
        )


def test_retrained_random_forest_can_expand_conversation_save() -> None:
    router = TriageRouter()
    router.model_intent = FakeConversationSaveModel()

    decision = _route(router, "archiva nuestra charla reciente")
    assert_true(
        decision["intent"] == "CONVERSATION_SAVE",
        "El modelo reentrenado puede admitir variantes fuera de la allowlist base.",
    )
    assert_true(decision["route"] == "persona", "La ampliación ML permanece en Capa 2.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE ROUTING TICKET 8 ===\n")
    test_authority_rebuke_stays_in_persona_even_if_ml_says_oracle()
    test_creator_authority_stays_in_persona()
    test_explicit_technical_queries_still_go_to_oracle()
    test_manual_conversation_save_uses_a_dedicated_intent()
    test_heuristic_memory_updates_do_not_save_the_whole_ram()
    test_retrained_random_forest_can_expand_conversation_save()
    print("[OK] test_triage_routing paso: autoridad social no salta al Oraculo.")


if __name__ == "__main__":
    main()
