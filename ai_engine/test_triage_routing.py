from __future__ import annotations

from collections import Counter
import json
import sys
from pathlib import Path
import unicodedata


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.triage_router import TriageRouter
from ai_engine.intent_types import IntentType, Route
from ai_engine.routing_rules import (
    MANUAL_CONVERSATION_SAVE_PHRASES,
    ROOT_024_LEXICAL_RULES,
    analyze_lexical_routing,
    build_lexical_view,
    match_guardrail_intent,
)


ROOT_024_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "root_024_triage_contract_v1.jsonl"
)
ROOT_024_CATEGORIES = frozenset(
    {
        "ambiguous_multi_intent", "case_variation", "cessation_command",
        "code_literal", "future_narrative", "guardrail_substring_collision",
        "hypothetical", "informational_question", "manual_save",
        "memory_update", "metalinguistic", "metalinguistic_question",
        "minor_typo", "mixed_language", "negated_memory", "negated_oracle",
        "negated_perception", "negated_save", "negation",
        "negation_with_positive_oracle", "oracle", "ordinary_conversation",
        "past_narrative", "perception", "positive_command",
        "precedence_collision", "punctuation", "question_command",
        "quoted_example", "quoted_keyword", "quoted_memory_keyword",
        "quoted_save", "request_frame", "token_number_collision",
        "unicode_nfc", "unicode_nfd", "unsupported_language", "url_path",
        "whitespace_variation", "word_boundary_collision",
    }
)


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


class ContradictoryMotorModel:
    def predict(self, textos):
        return ["MOTOR_SIMPLE"]

    def predict_proba(self, textos):
        return [[0.99, 0.01]]


class RecordingModel:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def predict(self, textos):
        self.seen.extend(textos)
        return ["MOTOR_SIMPLE"]

    def predict_proba(self, textos):
        self.seen.extend(textos)
        return [[0.99, 0.01]]


def _route(router: TriageRouter, mensaje: str) -> dict:
    return router.route_event(
        {
            "tipo_evento": "comando_usuario",
            "detalles": {"mensaje": mensaje},
        },
        estado_agente={},
    )


def _load_root_024_fixture() -> list[dict]:
    with ROOT_024_FIXTURE.open("r", encoding="utf-8") as fixture_file:
        return [json.loads(line) for line in fixture_file if line.strip()]


def _assert_contract_predicates(case: dict, decision: dict) -> None:
    prefix = case["case_id"]
    expected_intent = case["expected_intent"]
    expected_route = case["expected_route"]
    if expected_intent is not None:
        assert_true(
            decision["intent"] == expected_intent,
            f"{prefix}: intent={decision['intent']}, esperado={expected_intent}",
        )
    if expected_route is not None:
        assert_true(
            decision["route"] == expected_route,
            f"{prefix}: route={decision['route']}, esperado={expected_route}",
        )
    assert_true(
        decision["intent"] not in case["forbidden_intents"],
        f"{prefix}: intent prohibido={decision['intent']}",
    )
    assert_true(
        decision["route"] not in case["forbidden_routes"],
        f"{prefix}: route prohibida={decision['route']}",
    )


def test_root_024_fixture_schema_and_authority_split() -> None:
    cases = _load_root_024_fixture()
    case_ids = [case["case_id"] for case in cases]
    assert_true(len(cases) == 58, "El contrato ROOT-024 v1 debe contener 58 casos.")
    assert_true(case_ids == sorted(case_ids), "El fixture debe estar ordenado por case_id.")
    assert_true(len(case_ids) == len(set(case_ids)), "Los case_id deben ser únicos.")
    counts = Counter(case["authority"] for case in cases)
    assert_true(
        counts == {"lexical": 31, "model_fallback": 22, "observe_only": 5},
        f"Split de autoridad inesperado: {dict(counts)}",
    )

    expected_fields = {
        "case_id",
        "text",
        "category",
        "language",
        "authority",
        "expected_intent",
        "expected_route",
        "forbidden_intents",
        "forbidden_routes",
        "notes",
    }
    valid_intents = {intent.value for intent in IntentType}
    valid_routes = {route.value for route in Route}
    for case in cases:
        assert_true(set(case) == expected_fields, f"Schema inválido: {case['case_id']}")
        assert_true(case["category"] in ROOT_024_CATEGORIES, "Categoría inválida.")
        assert_true(case["language"] in {"es", "en", "mixed"}, "Idioma inválido.")
        if case["expected_intent"] is not None:
            assert_true(case["expected_intent"] in valid_intents, "Intent esperado inválido.")
            assert_true(
                case["expected_intent"] not in case["forbidden_intents"],
                "El intent esperado no puede estar prohibido.",
            )
        if case["expected_route"] is not None:
            assert_true(case["expected_route"] in valid_routes, "Ruta esperada inválida.")
            assert_true(
                case["expected_route"] not in case["forbidden_routes"],
                "La ruta esperada no puede estar prohibida.",
            )
        assert_true(
            set(case["forbidden_intents"]).issubset(valid_intents),
            "Intent prohibido inválido.",
        )
        assert_true(
            set(case["forbidden_routes"]).issubset(valid_routes),
            "Ruta prohibida inválida.",
        )
        declared_predicates = (
            case["expected_intent"] is not None
            or case["expected_route"] is not None
            or bool(case["forbidden_intents"])
            or bool(case["forbidden_routes"])
        )
        if case["authority"] == "observe_only":
            assert_true(
                not declared_predicates,
                f"Observe-only no debe imponer predicados: {case['case_id']}",
            )
        else:
            assert_true(
                declared_predicates,
                f"Caso gating sin predicados: {case['case_id']}",
            )


def test_root_024_fixture_runner() -> None:
    cases = _load_root_024_fixture()
    deployed_router = TriageRouter()
    absent_model_router = TriageRouter()
    absent_model_router.model_intent = None
    contradictory_router = TriageRouter()
    contradictory_router.model_intent = ContradictoryMotorModel()

    for case in cases:
        authority = case["authority"]
        if authority == "observe_only":
            _route(deployed_router, case["text"])
            continue
        if authority == "model_fallback":
            _assert_contract_predicates(case, _route(deployed_router, case["text"]))
            continue
        for router in (deployed_router, absent_model_router, contradictory_router):
            _assert_contract_predicates(case, _route(router, case["text"]))


def test_root_024_rule_identity_and_priority_are_stable() -> None:
    rule_ids = [rule.rule_id for rule in ROOT_024_LEXICAL_RULES]
    priorities = [rule.priority for rule in ROOT_024_LEXICAL_RULES]
    assert_true(len(rule_ids) == len(set(rule_ids)), "rule_id duplicado.")
    assert_true(len(priorities) == len(set(priorities)), "priority duplicada.")
    assert_true(priorities == sorted(priorities), "La prioridad debe ser estable.")


def test_root_024_lexical_view_normalization_and_protection() -> None:
    nfc = build_lexical_view("  MUÉVETE   hacia el cubo  ")
    nfd = build_lexical_view(
        unicodedata.normalize("NFD", "  MUÉVETE   hacia el cubo  ")
    )
    assert_true(nfc.normalized_text == nfd.normalized_text, "NFC/NFD deben converger.")
    assert_true(nfc.normalized_text == "muévete hacia el cubo", "Normalización inesperada.")

    protected = build_lexical_view('texto "abre la puerta" `mover()` https://x/mueve')
    protected_values = {token.value for token in protected.tokens if token.protected}
    assert_true(
        {"abre", "mover", "mueve"}.issubset(protected_values),
        "Citas, código y URL deben quedar protegidos.",
    )
    unmatched = build_lexical_view('ella dijo "abre la puerta')
    assert_true(
        all(token.protected for token in unmatched.tokens[2:]),
        "Una cita sin cerrar debe proteger conservadoramente hasta el final.",
    )


def test_root_024_general_token_boundaries() -> None:
    lookalikes = (
        "promueve una conversación",
        "el detergente remueve manchas",
        "esta causa problemas",
        "abre2 no es un comando",
        "supermueve aparece como etiqueta",
    )
    for text in lookalikes:
        _, lexical_match = analyze_lexical_routing(text)
        assert_true(
            "constraint.boundary_lookalike_not_motor"
            in lexical_match.matched_rule_ids,
            f"Debe detectar el lexema motor embebido sin hardcodear la frase: {text}",
        )

    router = TriageRouter()
    router.model_intent = ContradictoryMotorModel()
    for text in lookalikes:
        assert_true(_route(router, text)["route"] != "motor", f"Falso Motor: {text}")

    real_command = "promueve el arte y mueve la caja"
    _, lexical_match = analyze_lexical_routing(real_command)
    assert_true(
        "constraint.boundary_lookalike_not_motor"
        not in lexical_match.matched_rule_ids,
        "Un token motor real separado debe seguir disponible.",
    )
    assert_true(_route(router, real_command)["route"] == "motor", "El comando real se bloqueó.")


def test_root_024_manual_save_control_respects_constraints() -> None:
    router = TriageRouter()
    router.model_intent = FakeConversationSaveModel()

    direct = _route(router, "guarda lo que hemos hablado")
    quoted = _route(router, '"guarda lo que hemos hablado"')
    assert_true(direct["intent"] == "CONVERSATION_SAVE", "El control directo debe guardar.")
    assert_true(
        quoted["intent"] != "CONVERSATION_SAVE",
        "El control citado debe obedecer constraint.protected_save.",
    )


def test_root_024_model_input_is_not_rewritten() -> None:
    raw_text = unicodedata.normalize("NFD", "  MUÉVETE HACIA EL CUBO  ")
    expected_model_text = raw_text.strip()
    model = RecordingModel()
    router = TriageRouter()
    router.model_intent = model
    _route(router, raw_text)
    assert_true(model.seen, "El modelo debe haberse invocado.")
    assert_true(
        all(value == expected_model_text for value in model.seen),
        "LexicalView no debe reemplazar el preprocessing desplegado del modelo.",
    )


def test_root_024_guardrail_uses_token_boundaries() -> None:
    assert_true(
        match_guardrail_intent("la gente cree que falla") is IntentType.UNKNOWN,
        "'te cree' no debe coincidir dentro de 'gente cree'.",
    )
    assert_true(
        match_guardrail_intent("un recreador trabaja aquí") is IntentType.UNKNOWN,
        "'creador' no debe coincidir dentro de 'recreador'.",
    )


def test_root_024_metamorphic_and_repeatability_contract() -> None:
    router = TriageRouter()
    router.model_intent = ContradictoryMotorModel()
    negative_variants = (
        "no muevas la caja",
        "  NO   MUEVAS   LA CAJA  ",
        "¡no muevas la caja!",
        unicodedata.normalize("NFD", "no muevas la caja"),
    )
    for text in negative_variants:
        assert_true(_route(router, text)["route"] != "motor", f"Variante insegura: {text}")

    oracle_variants = (
        "qué significa \"abrir\"",
        "  QUÉ   SIGNIFICA \"ABRIR\"  ",
        "¿qué significa \"abrir\"?",
        unicodedata.normalize("NFD", "qué significa \"abrir\""),
    )
    for text in oracle_variants:
        decision = _route(router, text)
        assert_true(decision["intent"] == "ORACLE_QUERY", f"Variante Oracle: {text}")
        assert_true(decision["route"] == "oracle", f"Ruta Oracle perdida: {text}")

    representative_cases = (
        "mueve la caja",
        "no muevas la caja",
        '"corre hacia la puerta"',
        "explica qué es una mutex",
        "hola, cómo estás",
    )
    stable_router = TriageRouter()
    for text in representative_cases:
        first = _route(stable_router, text)
        for _ in range(20):
            assert_true(
                _route(stable_router, text) == first,
                f"Resultado no determinista con mismo artifact/config/state: {text}",
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
    test_root_024_fixture_schema_and_authority_split()
    test_root_024_fixture_runner()
    test_root_024_rule_identity_and_priority_are_stable()
    test_root_024_lexical_view_normalization_and_protection()
    test_root_024_general_token_boundaries()
    test_root_024_manual_save_control_respects_constraints()
    test_root_024_model_input_is_not_rewritten()
    test_root_024_guardrail_uses_token_boundaries()
    test_root_024_metamorphic_and_repeatability_contract()
    test_authority_rebuke_stays_in_persona_even_if_ml_says_oracle()
    test_creator_authority_stays_in_persona()
    test_explicit_technical_queries_still_go_to_oracle()
    test_manual_conversation_save_uses_a_dedicated_intent()
    test_heuristic_memory_updates_do_not_save_the_whole_ram()
    test_retrained_random_forest_can_expand_conversation_save()
    print("[OK] test_triage_routing paso: autoridad social no salta al Oraculo.")


if __name__ == "__main__":
    main()
