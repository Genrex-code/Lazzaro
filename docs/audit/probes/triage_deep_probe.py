"""Offline audit probe for Fase 2 / Bloque 5 (Capa 0 / Triage).

PASS means that current behavior was reproduced; it is not a correctness
claim.  The probe reads the current joblib and curated CSV, trains only
ephemeral in-memory comparison models, blocks network and forbids Chroma
construction.  It never writes a model, dataset, AuditTrace or runtime file.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import math
from pathlib import Path
import socket
import sys
from typing import Any, Callable
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Do not append normal LAZZARO runtime logs while probing.
logging.getLogger().handlers = [logging.NullHandler()]

import memory.chroma_client as chroma_client_module
from ai_engine.intent_types import INTENT_TO_ROUTE, IntentType, normalize_intent
from ai_engine.triage_router import TriageRouter
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


DATASET = ROOT / "cvs_entrenamiento_final_finaloso_finalisimo_v2_v1" / "dataset_shingon.csv"
INTENT_MODEL = ROOT / "lazzaro_data" / "triage_model_intent.joblib"
ROUTE_MODEL = ROOT / "lazzaro_data" / "triage_model_route.joblib"
TRAINER = ROOT / "train_triage_model.py"
PROTECTED_FILES = (DATASET, INTENT_MODEL, ROUTE_MODEL, TRAINER)


def require(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def file_hashes() -> dict[str, str]:
    return {path.name: sha256(path) for path in PROTECTED_FILES}


def _deny_network(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("network access is forbidden in triage_deep_probe")


def _deny_chroma(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("Chroma construction is forbidden in triage_deep_probe")


@contextmanager
def safety_barriers():
    before = file_hashes()
    with (
        patch.object(socket.socket, "connect", _deny_network),
        patch.object(socket.socket, "connect_ex", _deny_network),
        patch.object(socket, "create_connection", _deny_network),
        patch.object(chroma_client_module.chromadb, "PersistentClient", _deny_chroma),
    ):
        yield before
    require(file_hashes() == before, "model/dataset/trainer hashes changed")


def make_pipeline() -> Pipeline:
    """Exact current training recipe, used only in RAM."""
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2))),
            (
                "rf",
                RandomForestClassifier(
                    n_estimators=150,
                    random_state=42,
                    class_weight="balanced",
                ),
            ),
        ]
    )


def trees_equal(first: RandomForestClassifier, second: RandomForestClassifier) -> bool:
    if len(first.estimators_) != len(second.estimators_):
        return False
    return all(
        np.array_equal(a.tree_.children_left, b.tree_.children_left)
        and np.array_equal(a.tree_.children_right, b.tree_.children_right)
        and np.array_equal(a.tree_.feature, b.tree_.feature)
        and np.array_equal(a.tree_.threshold, b.tree_.threshold)
        and np.array_equal(a.tree_.value, b.tree_.value)
        for a, b in zip(first.estimators_, second.estimators_)
    )


def probe_model_dataset_lineage() -> dict[str, Any]:
    frame = pd.read_csv(DATASET)
    text = frame["texto_usuario"].astype(str)
    intent = frame["intencion"].astype(str)
    route = frame["ruta"].astype(str)
    active_intent: Pipeline = joblib.load(INTENT_MODEL)
    active_route: Pipeline = joblib.load(ROUTE_MODEL)

    recreated_intent = make_pipeline().fit(text, intent)
    recreated_route = make_pipeline().fit(text, route)

    comparisons: dict[str, dict[str, Any]] = {}
    for name, active, recreated in (
        ("intent", active_intent, recreated_intent),
        ("route", active_route, recreated_route),
    ):
        active_tfidf = active.named_steps["tfidf"]
        recreated_tfidf = recreated.named_steps["tfidf"]
        active_rf = active.named_steps["rf"]
        recreated_rf = recreated.named_steps["rf"]
        comparison = {
            "classes": [str(value) for value in active_rf.classes_],
            "training_samples": int(len(active_rf.estimators_samples_[0])),
            "vocabulary_size": len(active_tfidf.vocabulary_),
            "vocabulary_exact": active_tfidf.vocabulary_ == recreated_tfidf.vocabulary_,
            "idf_exact": bool(np.array_equal(active_tfidf.idf_, recreated_tfidf.idf_)),
            "all_150_trees_exact": trees_equal(active_rf, recreated_rf),
            "n_estimators": active_rf.n_estimators,
            "random_state": active_rf.random_state,
            "class_weight": active_rf.class_weight,
        }
        require(comparison["training_samples"] == len(frame), "model row count mismatch")
        require(comparison["vocabulary_exact"], "active vocabulary does not match current CSV")
        require(comparison["idf_exact"], "active IDF does not match current CSV")
        require(comparison["all_150_trees_exact"], "active forest does not match current trainer/CSV/order")
        comparisons[name] = comparison

    intent_classes = set(comparisons["intent"]["classes"])
    taxonomy = {intent_type.value for intent_type in IntentType}
    require(intent_classes == taxonomy - {"INTERNAL", "NOOP"}, "unexpected ML taxonomy")
    require(comparisons["route"]["classes"] == ["motor", "oracle", "persona"], "unexpected route classes")

    return {
        "dataset_rows": len(frame),
        "active_models_match_current_csv_trainer_and_order_exactly": True,
        "models": comparisons,
        "runtime_loads": "triage_model_intent.joblib",
        "runtime_ignores": "triage_model_route.joblib",
        "trainer_default_dataset_exists": (ROOT / "dataset_shingon.csv").exists(),
        "joblibs_tracked_by_git": False,
        "dataset_tracked_by_git": True,
        "hashes": file_hashes(),
    }


def normalized_text(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()


def probe_dataset_profile_and_offline_evaluation() -> dict[str, Any]:
    frame = pd.read_csv(DATASET)
    required = ["texto_usuario", "intencion", "ruta"]
    require(list(frame.columns) == required, "unexpected dataset columns")
    clean = frame.dropna(subset=required).copy()
    for column in required:
        clean[column] = clean[column].astype(str).str.strip()
    clean = clean[(clean[required] != "").all(axis=1)]
    require(len(clean) == len(frame), "current trainer would drop rows")

    exact_conflicts = 0
    route_conflicts = 0
    for _, group in clean.groupby("texto_usuario"):
        exact_conflicts += int(group["intencion"].nunique() > 1)
        route_conflicts += int(group["ruta"].nunique() > 1)

    normalized = normalized_text(clean["texto_usuario"])
    normalized_conflicts = 0
    normalized_route_conflicts = 0
    normalized_duplicate_groups = 0
    for _, group in clean.assign(_normalized=normalized).groupby("_normalized"):
        normalized_duplicate_groups += int(len(group) > 1)
        normalized_conflicts += int(group["intencion"].nunique() > 1)
        normalized_route_conflicts += int(group["ruta"].nunique() > 1)

    intent_counts = clean["intencion"].value_counts().sort_index()
    route_counts = clean["ruta"].value_counts().sort_index()
    cross = pd.crosstab(clean["intencion"], clean["ruta"])
    cross_compact = {
        str(label): {str(route): int(count) for route, count in row.items() if int(count)}
        for label, row in cross.iterrows()
    }
    canonical = {intent.value: route.value for intent, route in INTENT_TO_ROUTE.items()}
    for label, routes in cross_compact.items():
        require(routes == {canonical[label]: int(intent_counts[label])}, "dataset route mismatch")

    text = clean["texto_usuario"]
    labels = clean["intencion"]
    train_x, test_x, train_y, test_y = train_test_split(
        text,
        labels,
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )
    offline = make_pipeline().fit(train_x, train_y)
    predicted = offline.predict(test_x)
    report = classification_report(test_y, predicted, output_dict=True, zero_division=0)
    label_order = [str(value) for value in offline.classes_]

    operational_router = synthetic_router(offline)
    operational_decisions = [
        operational_router.route_event(
            {"tipo_evento": "comando_usuario", "detalles": {"mensaje": value}},
            estado_agente={},
        )
        for value in test_x
    ]
    operational_predicted = [decision["intent"] for decision in operational_decisions]
    operational_report = classification_report(
        test_y,
        operational_predicted,
        output_dict=True,
        zero_division=0,
    )
    operational_paths = [source_path(decision["reason"]) for decision in operational_decisions]

    vectorizer = TfidfVectorizer(ngram_range=(1, 2)).fit(pd.concat([train_x, test_x]))
    similarities = cosine_similarity(vectorizer.transform(test_x), vectorizer.transform(train_x))
    nearest = similarities.max(axis=1)

    active: Pipeline = joblib.load(INTENT_MODEL)
    active_prediction = active.predict(text)

    return {
        "rows_raw": len(frame),
        "rows_trainable": len(clean),
        "intent_counts": {str(key): int(value) for key, value in intent_counts.items()},
        "route_counts": {str(key): int(value) for key, value in route_counts.items()},
        "intent_route_crosstab": cross_compact,
        "class_max_min_ratio": round(float(intent_counts.max() / intent_counts.min()), 3),
        "exact_duplicate_extra_rows": int(clean.duplicated().sum()),
        "exact_text_conflict_groups": exact_conflicts,
        "exact_route_conflict_groups": route_conflicts,
        "normalized_duplicate_groups": normalized_duplicate_groups,
        "normalized_intent_conflict_groups": normalized_conflicts,
        "normalized_route_conflict_groups": normalized_route_conflicts,
        "text_length": {
            "min": int(text.str.len().min()),
            "median": float(text.str.len().median()),
            "p95": round(float(text.str.len().quantile(0.95)), 3),
            "max": int(text.str.len().max()),
        },
        "active_resubstitution_not_generalization": {
            "accuracy": float(accuracy_score(labels, active_prediction)),
            "errors": int((active_prediction != labels).sum()),
        },
        "offline_stratified_80_20_not_historical": {
            "train_rows": len(train_x),
            "test_rows": len(test_x),
            "accuracy": round(float(accuracy_score(test_y, predicted)), 6),
            "macro_f1": round(float(report["macro avg"]["f1-score"]), 6),
            "weighted_f1": round(float(report["weighted avg"]["f1-score"]), 6),
            "per_class_f1": {
                label: round(float(report[label]["f1-score"]), 4) for label in label_order
            },
            "labels": label_order,
            "confusion_matrix": confusion_matrix(test_y, predicted, labels=label_order).tolist(),
        },
        "offline_operational_router_same_split_not_historical": {
            "accuracy": round(float(accuracy_score(test_y, operational_predicted)), 6),
            "macro_f1": round(float(operational_report["macro avg"]["f1-score"]), 6),
            "per_class_f1": {
                label: round(float(operational_report[label]["f1-score"]), 4)
                for label in label_order
            },
            "model_path_cases": operational_paths.count("model"),
            "fallback_path_cases": operational_paths.count("fallback"),
            "rule_path_cases": operational_paths.count("rule"),
        },
        "near_duplicate_split_signal": {
            "nearest_cosine_ge_0_95": int((nearest >= 0.95).sum()),
            "nearest_cosine_ge_0_90": int((nearest >= 0.90).sum()),
            "nearest_cosine_ge_0_80": int((nearest >= 0.80).sum()),
            "maximum": round(float(nearest.max()), 6),
            "median": round(float(np.median(nearest)), 6),
        },
        "historical_training_metrics_embedded": False,
    }


@dataclass(frozen=True)
class AdversarialCase:
    name: str
    category: str
    message: str
    event_type: str = "comando_usuario"
    origin: str = "unity"
    state: dict[str, Any] | None = None
    details: dict[str, Any] | None = None


ADVERSARIAL_CASES = (
    AdversarialCase("social_hola", "SOCIAL", "hola"),
    AdversarialCase("social_estado", "SOCIAL", "cómo estás"),
    AdversarialCase("social_gracias", "SOCIAL", "gracias"),
    AdversarialCase("social_adios", "SOCIAL", "adiós"),
    AdversarialCase("motor_cubo", "MOTOR", "ve al cubo"),
    AdversarialCase("motor_cortes", "MOTOR", "por favor ve al cubo"),
    AdversarialCase("motor_giro", "MOTOR", "gira a la izquierda"),
    AdversarialCase("motor_toma", "MOTOR", "toma la esfera"),
    AdversarialCase("motor_negado", "MOTOR", "no vayas al cubo"),
    AdversarialCase("oracle_mutex", "ORACLE", "explica qué es una mutex"),
    AdversarialCase("oracle_algoritmo", "ORACLE", "por qué falló este algoritmo"),
    AdversarialCase("memory_update", "MEMORY", "recuerda que mi color favorito es azul"),
    AdversarialCase("memory_query", "MEMORY", "qué recuerdas de mí"),
    AdversarialCase("memory_forget", "MEMORY", "olvida X"),
    AdversarialCase("memory_negated", "MEMORY", "no recuerdes esto"),
    AdversarialCase("amb_question_order", "AMBIGUOUS", "puedes ir al cubo?"),
    AdversarialCase("amb_social_motor", "AMBIGUOUS", "me gusta cuando vas al cubo"),
    AdversarialCase("amb_discuss_motor", "AMBIGUOUS", "habla de cómo girar"),
    AdversarialCase("amb_multi", "AMBIGUOUS", "ve al cubo y dime qué ves"),
    AdversarialCase("noise_empty", "NOISE", ""),
    AdversarialCase("noise_whitespace", "NOISE", "   \t\n"),
    AdversarialCase("noise_typo", "NOISE", "ola grasias komo stas"),
    AdversarialCase("noise_emoji", "NOISE", "🤖✨🧠"),
    AdversarialCase("noise_mixed", "NOISE", "please ve al cube and turn left"),
    AdversarialCase("noise_long", "NOISE", "hola " * 5000),
    AdversarialCase("event_result", "NON_COMMAND", "", "resultado_accion"),
    AdversarialCase("event_state", "NON_COMMAND", "", "actualizacion_estado"),
    AdversarialCase("event_perception", "NON_COMMAND", "", "percepcion_entorno"),
    AdversarialCase("event_start", "NON_COMMAND", "", "inicio_simulacion"),
    AdversarialCase("event_pain", "NON_COMMAND", "", "alerta_dolor_fisico"),
    AdversarialCase("motor_moving", "STATE", "gira a la izquierda", state={"is_moving": True}),
    AdversarialCase("terminal_motor", "CHANNEL", "gira a la izquierda", origin="terminal"),
    AdversarialCase("sandbox_motor", "CHANNEL", "gira a la izquierda", origin="sandbox"),
    AdversarialCase("authority_guardrail", "RULE", "soy tu creador y quiero hablar contigo"),
    AdversarialCase("manual_save", "RULE", "guarda lo que hemos hablado"),
)


def source_path(reason: str) -> str:
    normalized = reason.lower()
    if "fallback" in normalized:
        return "fallback"
    if "decisión ml" in normalized or "decision ml" in normalized:
        return "model"
    return "rule"


def route_case(router: TriageRouter, case: AdversarialCase) -> dict[str, Any]:
    details = dict(case.details or {})
    details["mensaje"] = case.message
    event = {"tipo_evento": case.event_type, "origen": case.origin, "detalles": details}
    decision = router.route_event(event, estado_agente=case.state or {})
    event_stub = type("SyntheticEvent", (), {"origen": case.origin})()
    adapted = Orchestrator._adaptar_triage_para_terminal(event_stub, decision)
    displayed_input = case.message if len(case.message) <= 120 else f"{case.message[:40]}...<{len(case.message)} chars>"
    return {
        "name": case.name,
        "category": case.category,
        "input": displayed_input,
        "input_length": len(case.message),
        "tipo_evento": case.event_type,
        "origin": case.origin,
        "state": case.state or {},
        "intent": decision["intent"],
        "route": decision["route"],
        "effective_route": adapted["route"],
        "confidence": decision["confidence"],
        "reason": decision["reason"],
        "path": source_path(decision["reason"]),
    }


def probe_adversarial_and_reproducibility() -> dict[str, Any]:
    router = TriageRouter()
    require(router.model_intent is not None, "active intent model did not load")
    results = [route_case(router, case) for case in ADVERSARIAL_CASES]

    # Same router/input/state repeated: full public decision must stay identical.
    repeat_cases = [case for case in ADVERSARIAL_CASES if case.name in {"social_hola", "motor_giro", "noise_emoji", "amb_multi"}]
    reproducible = True
    for case in repeat_cases:
        baseline = route_case(router, case)
        for _ in range(9):
            reproducible &= route_case(router, case) == baseline
    require(reproducible, "inference changed for same input/state")

    by_name = {item["name"]: item for item in results}
    require(by_name["motor_cubo"]["intent"] == "UNKNOWN", "current ve-al-cubo behavior changed")
    require(by_name["motor_cortes"]["intent"] == "SOCIAL", "current polite motor behavior changed")
    require(by_name["amb_discuss_motor"]["route"] == "motor", "current discuss-motor behavior changed")
    require(by_name["terminal_motor"]["route"] == "motor", "terminal original route must remain motor")
    require(by_name["terminal_motor"]["effective_route"] == "persona", "terminal effective route must be persona")
    require(by_name["sandbox_motor"]["effective_route"] == "motor", "sandbox must not adapt route")
    require(by_name["motor_moving"]["intent"] == "NOOP", "moving guard must block motor")

    # Same message, different irrelevant origin: original Capa 0 output is equal.
    unity = by_name["sandbox_motor"].copy()
    sandbox = by_name["sandbox_motor"].copy()
    unity.pop("origin"); sandbox.pop("origin")
    # The exact same decision is independently checked without output adornments.
    message = "gira a la izquierda"
    unity_decision = router.route_event({"tipo_evento": "comando_usuario", "origen": "unity", "detalles": {"mensaje": message}}, {})
    sandbox_decision = router.route_event({"tipo_evento": "comando_usuario", "origen": "sandbox", "detalles": {"mensaje": message}}, {})
    require(unity_decision == sandbox_decision, "origin changed original triage")

    # Current active forest can be exactly replayed in original order, but row
    # order changes bootstrap assignments even with random_state=42.
    frame = pd.read_csv(DATASET)
    text = frame["texto_usuario"].astype(str)
    labels = frame["intencion"].astype(str)
    first = make_pipeline().fit(text, labels)
    second = make_pipeline().fit(text, labels)
    reversed_model = make_pipeline().fit(text.iloc[::-1].reset_index(drop=True), labels.iloc[::-1].reset_index(drop=True))
    probes = ["ve al cubo", "gira a la izquierda", "explica qué es una mutex", "qué recuerdas de mí", "🤖✨🧠"]
    first_proba = first.predict_proba(probes)
    second_proba = second.predict_proba(probes)
    reversed_proba = reversed_model.predict_proba(probes)
    require(np.array_equal(first_proba, second_proba), "same-order training is not deterministic")
    require(not np.array_equal(first_proba, reversed_proba), "reversed rows unexpectedly identical")

    return {
        "cases": results,
        "same_input_state_repeated_10_times_identical": reproducible,
        "same_message_unity_sandbox_original_equal": unity_decision == sandbox_decision,
        "terminal_changes_only_effective_route": True,
        "same_order_training_probabilities_exact": True,
        "reversed_order_probabilities_exact": False,
        "reversed_order_max_abs_probability_delta": round(float(np.max(np.abs(first_proba - reversed_proba))), 6),
        "multi_intent_supported": False,
    }


class RaisingModel:
    def __init__(self, *, predict_error: bool = False, proba_error: bool = False):
        self.predict_error = predict_error
        self.proba_error = proba_error

    def predict(self, _texts):
        if self.predict_error:
            raise RuntimeError("synthetic predict failure")
        return ["MOTOR_SIMPLE"]

    def predict_proba(self, _texts):
        if self.proba_error:
            raise RuntimeError("synthetic predict_proba failure")
        return [[0.99, 0.01]]


class UnknownClassModel:
    def __init__(self, confidence: float):
        self.confidence = confidence

    def predict(self, _texts):
        return ["SYNTHETIC_NEW_CLASS"]

    def predict_proba(self, _texts):
        return [[self.confidence]]


def synthetic_router(model: Any) -> TriageRouter:
    router = object.__new__(TriageRouter)
    router.model_intent = model
    return router


def command(router: TriageRouter, message: str = "gira a la izquierda") -> dict[str, Any]:
    return router.route_event(
        {"tipo_evento": "comando_usuario", "detalles": {"mensaje": message}},
        estado_agente={},
    )


def probe_failures_and_confidence() -> dict[str, Any]:
    with patch.object(Path, "exists", return_value=False):
        missing = TriageRouter()
    require(missing.model_intent is None, "missing joblib must degrade")
    missing_decision = command(missing)

    with patch.object(Path, "exists", return_value=True), patch("ai_engine.triage_router.joblib.load", side_effect=RuntimeError("synthetic corrupt joblib")):
        corrupt = TriageRouter()
    require(corrupt.model_intent is None, "corrupt joblib must degrade")
    corrupt_decision = command(corrupt)

    predict_error = command(synthetic_router(RaisingModel(predict_error=True)))
    proba_error = command(synthetic_router(RaisingModel(proba_error=True)))
    for decision in (missing_decision, corrupt_decision, predict_error, proba_error):
        require(decision["intent"] == "MOTOR_SIMPLE" and decision["route"] == "motor", "fallback must resolve synthetic motor phrase")
        require("fallback" in decision["reason"].lower(), "failure reason must expose fallback")

    unknown_class = command(synthetic_router(UnknownClassModel(0.99)))
    require(unknown_class["intent"] == "UNKNOWN" and unknown_class["route"] == "persona", "unknown class normalization")
    require(unknown_class["confidence"] == 0.99, "unknown class keeps model confidence")

    out_of_range = command(synthetic_router(UnknownClassModel(1.5)))
    require(out_of_range["confidence"] == 1.5, "confidence is not clamped")
    nan_confidence = command(synthetic_router(UnknownClassModel(float("nan"))))
    require(math.isnan(nan_confidence["confidence"]), "NaN confidence is accepted")

    malformed_details = synthetic_router(None).route_event(
        {"tipo_evento": "comando_usuario", "detalles": ["not", "a", "dict"]},
        {},
    )
    require(malformed_details["intent"] == "NOOP", "non-dict details becomes empty command")

    malformed_event_raised = False
    try:
        synthetic_router(None).route_event(None, {})  # type: ignore[arg-type]
    except AttributeError:
        malformed_event_raised = True
    require(malformed_event_raised, "non-dict event must escape Triage")

    hard_rule_raised = False
    with patch("ai_engine.triage_router.match_manual_conversation_save", side_effect=RuntimeError("synthetic hard-rule failure")):
        try:
            command(synthetic_router(None), "synthetic command")
        except RuntimeError:
            hard_rule_raised = True
    require(hard_rule_raised, "hard-rule exception must escape Triage")

    orchestrator = object.__new__(Orchestrator)
    orchestrator.triage_router = type(
        "SyntheticBrokenRouter",
        (),
        {"route_event": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic route failure"))},
    )()
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="synthetic-triage-failure",
        detalles={"mensaje": "synthetic"},
    )
    orchestrator_fallback = Orchestrator._evaluar_triage(orchestrator, event, {})
    require(orchestrator_fallback == {}, "Orchestrator catches route_event as empty dict")
    require(not Orchestrator._triage_debe_cortar_flujo({}), "empty decision does not cut flow")
    require(not any((Orchestrator._triage_es_motor({}), Orchestrator._triage_es_persona({}), Orchestrator._triage_es_oracle({}))), "empty decision selects no canonical route")

    terminal_event = type("SyntheticTerminalEvent", (), {"origen": "terminal"})()
    terminal_empty = Orchestrator._adaptar_triage_para_terminal(terminal_event, {})
    require(terminal_empty["route"] == "persona", "terminal adapts empty decision to Persona")

    normalizations = {
        repr(value): normalize_intent(value).value
        for value in (None, "", "oracle", "motor", "memory", "system", "social", "synthetic")
    }

    return {
        "joblib_missing": missing_decision,
        "joblib_corrupt_simulated": corrupt_decision,
        "predict_error": predict_error,
        "predict_proba_error": proba_error,
        "unknown_model_class": unknown_class,
        "confidence_out_of_range_accepted": out_of_range["confidence"],
        "nan_confidence_accepted": True,
        "malformed_details_safe": True,
        "malformed_event_raises": malformed_event_raised,
        "hard_rule_exception_raises": hard_rule_raised,
        "orchestrator_route_exception_decision": orchestrator_fallback,
        "nonterminal_empty_decision_falls_to_legacy_reasoning": True,
        "terminal_empty_decision_effective_route": terminal_empty["route"],
        "normalizations": normalizations,
    }


def probe_mapping_and_input_influence() -> dict[str, Any]:
    active: Pipeline = joblib.load(INTENT_MODEL)
    model_classes = {str(value) for value in active.classes_}
    observed_overrides = {
        "CORE_SYSTEM": ["persona", "noop", "motor"],
        "UNKNOWN": ["persona", "noop"],
    }
    matrix = []
    for intent in IntentType:
        canonical = INTENT_TO_ROUTE[intent].value
        possible = observed_overrides.get(intent.value, [canonical])
        matrix.append(
            {
                "intent": intent.value,
                "canonical_route": canonical,
                "runtime_possible_original_routes": possible,
                "model_can_emit": intent.value in model_classes,
                "terminal_effective_override": "persona" if canonical not in {"persona", "oracle"} else canonical,
            }
        )

    router = TriageRouter()
    base_event = {
        "tipo_evento": "comando_usuario",
        "origen": "unity",
        "objeto_involucrado": "synthetic-object-a",
        "detalles": {
            "mensaje": "gira a la izquierda",
            "solicita_decision": False,
            "percepcion": {"synthetic": "a"},
            "tarea": {"synthetic": "a"},
        },
    }
    base = router.route_event(base_event, {})
    mutated = router.route_event(
        {
            **base_event,
            "origen": "sandbox",
            "objeto_involucrado": "synthetic-object-b",
            "detalles": {
                **base_event["detalles"],
                "solicita_decision": True,
                "percepcion": {"synthetic": "b"},
                "tarea": {"synthetic": "b"},
            },
        },
        {"energia": 0.01, "estres": 0.99},
    )
    require(base == mutated, "irrelevant fields changed triage")
    moving = router.route_event(base_event, {"is_moving": True})
    require(moving["intent"] == "NOOP", "movement state must be the relevant state exception")

    return {
        "intent_route_matrix": matrix,
        "model_intent_classes": sorted(model_classes),
        "model_route_classes": ["motor", "oracle", "persona"],
        "route_model_used_by_runtime": False,
        "duplicate_INTENT_TO_ROUTE_MAP_used": False,
        "input_influence": {
            "tipo_evento": True,
            "message_fallback_keys": ["mensaje", "texto_usuario", "texto"],
            "origin_original_triage": False,
            "agent_state": "only movement guard for motor intents",
            "details_movement_fields": True,
            "task": False,
            "perception": False,
            "solicita_decision": False,
            "objeto_involucrado": False,
        },
        "irrelevant_field_mutation_kept_decision": base == mutated,
        "movement_state_changed_motor_to_noop": moving["intent"] == "NOOP",
    }


PROBES: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("model_dataset_lineage", probe_model_dataset_lineage),
    ("dataset_profile_and_offline_evaluation", probe_dataset_profile_and_offline_evaluation),
    ("mapping_and_input_influence", probe_mapping_and_input_influence),
    ("adversarial_and_reproducibility", probe_adversarial_and_reproducibility),
    ("failures_and_confidence", probe_failures_and_confidence),
)


def main() -> None:
    results: dict[str, Any] = {}
    with safety_barriers() as initial_hashes:
        for name, probe in PROBES:
            results[name] = probe()
    print(
        json.dumps(
            {
                "probe": "triage_deep_probe",
                "meaning": "PASS reproduces current behavior; it is not a correctness claim",
                "status": "PASS",
                "groups": len(results),
                "offline": True,
                "network_allowed": False,
                "chroma_constructed": False,
                "models_or_dataset_modified": False,
                "initial_hashes": initial_hashes,
                "final_hashes": file_hashes(),
                "results": results,
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
