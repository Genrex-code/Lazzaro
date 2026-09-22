"""Router cognitivo de Capa 0 con contrato de clasificacion discriminado."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import joblib
except ImportError:  # El fallback heuristico debe seguir vivo sin dependencias ML.
    joblib = None

from .intent_types import INTENT_TO_ROUTE, IntentType, Route, normalize_intent, route_for_intent
from .routing_rules import (
    LexicalMatch,
    analyze_lexical_routing,
    match_guardrail_intent,
    match_intent_by_keywords,
)
from .triage_contracts import (
    TriageDecisionV1,
    TriageFallbackStatusV1,
    TriageModelStatusV1,
    TriageOperationalOverrideV1,
    TriageOutcomeV1,
    TriageReasonCodeV1,
    TriageSourceV1,
)


logger = logging.getLogger(__name__)

INTENT_TO_ROUTE_MAP = {
    intent.value: route.value for intent, route in INTENT_TO_ROUTE.items()
}

MOTOR_BLOCKED_WHILE_MOVING = {
    IntentType.MOTOR_SIMPLE,
    IntentType.MOTOR_REFERENCE,
}

_TECHNICAL_MODEL_FAILURES = {
    TriageModelStatusV1.UNAVAILABLE,
    TriageModelStatusV1.ERROR,
    TriageModelStatusV1.INCOMPATIBLE_LABEL,
}


@dataclass
class _LegacyDiagnostics:
    """Datos del dict historico; no forman parte de TriageDecisionV1."""

    confidence: float = 0.5
    human_reason: str = "Decisión de Capa 0."


@dataclass(frozen=True)
class _ModelAttempt:
    status: TriageModelStatusV1
    raw_label: str | None = None
    intent: IntentType | None = None


@dataclass(frozen=True)
class _FallbackAttempt:
    status: TriageFallbackStatusV1
    intent: IntentType | None = None


class TriageRouter:
    def __init__(self):
        self.model_intent = None
        self.intent_path = (
            Path(__file__).resolve().parents[1]
            / "lazzaro_data"
            / "triage_model_intent.joblib"
        )

        if joblib is None:
            logger.warning(
                "joblib no esta disponible; Capa 0 usara fallback heuristico."
            )
            return

        try:
            if self.intent_path.exists():
                self.model_intent = joblib.load(self.intent_path)
                logger.info("Cerebro de Intención Random Forest cargado y listo.")
            else:
                logger.warning("No se encontró el modelo .joblib de intención.")
        except Exception as exc:
            # La limpieza global de human logs no pertenece a ROOT-025.
            logger.error("Error cargando modelo ML de intención: %s", exc)

    def route_event(
        self,
        evento: Dict[str, Any],
        estado_agente: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Wrapper compatible: una clasificacion y una proyeccion canonica."""

        diagnostics = _LegacyDiagnostics()
        decision = self.classify_event(
            evento,
            estado_agente=estado_agente,
            _diagnostics=diagnostics,
        )
        return decision.to_legacy_dict(confidence=diagnostics.confidence)

    def classify_event(
        self,
        evento: Dict[str, Any],
        estado_agente: Optional[Dict[str, Any]] = None,
        *,
        _diagnostics: _LegacyDiagnostics | None = None,
    ) -> TriageDecisionV1:
        """Autoridad tipada de una unica clasificacion de Triage."""

        diagnostics = _diagnostics if _diagnostics is not None else _LegacyDiagnostics()
        tipo_evento = evento.get("tipo_evento", "desconocido")
        detalles_raw = evento.get("detalles", {}) or {}
        detalles = detalles_raw if isinstance(detalles_raw, dict) else {}
        texto_usuario = (
            detalles.get("mensaje")
            or detalles.get("texto_usuario")
            or detalles.get("texto")
            or ""
        )
        texto_usuario = str(texto_usuario).strip()

        event_decision = self._classify_hard_event(tipo_evento, diagnostics)
        if event_decision is not None:
            return event_decision

        if tipo_evento != "comando_usuario":
            diagnostics.confidence = 0.0
            diagnostics.human_reason = self._bounded_reason(
                f"Evento '{tipo_evento}' no soportado por Triage."
            )
            return self._event_decision(
                semantic_intent=IntentType.NOOP,
                route=Route.NOOP,
                reason_code=TriageReasonCodeV1.UNSUPPORTED_EVENT,
                rule_id="event.unsupported",
                human_reason=diagnostics.human_reason,
            )

        if not texto_usuario:
            diagnostics.confidence = 1.0
            diagnostics.human_reason = "Comando vacío. Ignorado."
            return self._event_decision(
                semantic_intent=IntentType.NOOP,
                route=Route.NOOP,
                reason_code=TriageReasonCodeV1.EMPTY_COMMAND,
                rule_id="event.empty_command",
                human_reason=diagnostics.human_reason,
            )

        lexical_view, lexical_match = analyze_lexical_routing(texto_usuario)
        positive_allowed = (
            lexical_match.positive_intent is not None
            and self._lexical_candidate_allowed(
                lexical_match.positive_intent,
                lexical_match,
            )
        )

        if (
            lexical_match.positive_rule_id == "control.manual_conversation_save"
            and positive_allowed
        ):
            diagnostics.confidence = 1.0
            diagnostics.human_reason = (
                "Solicitud explícita de guardar la RAM conversacional."
            )
            return self._lexical_decision(
                IntentType.CONVERSATION_SAVE,
                lexical_match.positive_rule_id,
                diagnostics.human_reason,
            )

        guardrail_intent = normalize_intent(match_guardrail_intent(lexical_view))
        if guardrail_intent is not IntentType.UNKNOWN:
            diagnostics.confidence = 0.95
            diagnostics.human_reason = (
                "Guardrail relacional: autoridad/regano sobre el sistema "
                "se procesa en Capa Social antes que Oraculo."
            )
            return self._lexical_decision(
                guardrail_intent,
                "guardrail.relational_authority",
                diagnostics.human_reason,
            )

        if lexical_match.positive_intent is not None and positive_allowed:
            lexical_intent = normalize_intent(lexical_match.positive_intent)
            diagnostics.confidence = 0.95
            diagnostics.human_reason = (
                "Regla léxica determinista de Capa 0: "
                f"{lexical_match.positive_rule_id}."
            )
            decision = self._lexical_decision(
                lexical_intent,
                lexical_match.positive_rule_id or "lexical.unknown",
                diagnostics.human_reason,
            )
            return self._apply_movement_gate(
                decision,
                estado_agente,
                detalles,
                diagnostics,
            )

        model_attempt = self._predict_intent_with_ml(texto_usuario, diagnostics)
        if (
            model_attempt.status is TriageModelStatusV1.ACCEPTED
            and model_attempt.intent is not None
            and not self._lexical_candidate_allowed(model_attempt.intent, lexical_match)
        ):
            diagnostics.human_reason += (
                " Candidato ML descartado por una frontera léxica determinista."
            )
            model_attempt = _ModelAttempt(
                status=TriageModelStatusV1.VETOED,
                raw_label=model_attempt.raw_label,
            )

        if model_attempt.status is TriageModelStatusV1.ACCEPTED:
            assert model_attempt.intent is not None
            decision = TriageDecisionV1(
                outcome=TriageOutcomeV1.DECIDED,
                source=TriageSourceV1.MODEL,
                semantic_intent=model_attempt.intent,
                route=route_for_intent(model_attempt.intent),
                reason_code=TriageReasonCodeV1.MODEL_ACCEPTED,
                model_status=model_attempt.status,
                fallback_status=TriageFallbackStatusV1.NOT_ATTEMPTED,
                raw_label=model_attempt.raw_label,
                human_reason=self._bounded_reason(diagnostics.human_reason),
            )
            return self._apply_movement_gate(
                decision,
                estado_agente,
                detalles,
                diagnostics,
            )

        fallback_attempt = self._predict_intent_with_rules(texto_usuario, diagnostics)
        if (
            fallback_attempt.status is TriageFallbackStatusV1.ACCEPTED
            and fallback_attempt.intent is not None
            and not self._lexical_candidate_allowed(fallback_attempt.intent, lexical_match)
        ):
            diagnostics.confidence = min(diagnostics.confidence, 0.4)
            diagnostics.human_reason += (
                " Candidato fallback descartado por una frontera léxica; "
                "se conserva el no-match existente."
            )
            fallback_attempt = _FallbackAttempt(
                status=TriageFallbackStatusV1.VETOED,
            )

        decision = self._resolve_attempts(
            model_attempt,
            fallback_attempt,
            diagnostics,
        )
        return self._apply_movement_gate(
            decision,
            estado_agente,
            detalles,
            diagnostics,
        )

    def _classify_hard_event(
        self,
        tipo_evento: Any,
        diagnostics: _LegacyDiagnostics,
    ) -> TriageDecisionV1 | None:
        rules = {
            "actualizacion_estado": (
                IntentType.NOOP,
                Route.NOOP,
                "event.actualizacion_estado",
                "Telemetría pasiva. Ignorada.",
                None,
            ),
            "resultado_accion": (
                IntentType.INTERNAL,
                Route.INTERNAL,
                "event.resultado_accion",
                "Resultado de acción procesado internamente.",
                None,
            ),
            "inicio_simulacion": (
                IntentType.CORE_SYSTEM,
                Route.NOOP,
                "event.inicio_simulacion",
                "Inicio de simulación procesado como evento de sistema.",
                TriageOperationalOverrideV1.EVENT_ROUTE_OVERRIDE,
            ),
            "alerta_dolor_fisico": (
                IntentType.CORE_SYSTEM,
                Route.MOTOR,
                "event.alerta_dolor_fisico",
                "Interrupción física crítica. Ruta motora segura sin ML.",
                TriageOperationalOverrideV1.EVENT_ROUTE_OVERRIDE,
            ),
        }
        rule = rules.get(tipo_evento)
        if rule is None:
            return None
        semantic_intent, route, rule_id, reason, override = rule
        diagnostics.confidence = 1.0
        diagnostics.human_reason = reason
        return self._event_decision(
            semantic_intent=semantic_intent,
            route=route,
            reason_code=TriageReasonCodeV1.EVENT_RULE_ACCEPTED,
            rule_id=rule_id,
            human_reason=reason,
            operational_override=override,
        )

    @staticmethod
    def _event_decision(
        *,
        semantic_intent: IntentType,
        route: Route,
        reason_code: TriageReasonCodeV1,
        rule_id: str,
        human_reason: str,
        operational_override: TriageOperationalOverrideV1 | None = None,
    ) -> TriageDecisionV1:
        return TriageDecisionV1(
            outcome=TriageOutcomeV1.DECIDED,
            source=TriageSourceV1.EVENT_RULE,
            semantic_intent=semantic_intent,
            route=route,
            operational_override=operational_override,
            reason_code=reason_code,
            model_status=TriageModelStatusV1.NOT_ATTEMPTED,
            fallback_status=TriageFallbackStatusV1.NOT_ATTEMPTED,
            accepted_rule_id=rule_id,
            human_reason=human_reason,
        )

    @staticmethod
    def _lexical_decision(
        intent: IntentType,
        rule_id: str,
        human_reason: str,
    ) -> TriageDecisionV1:
        return TriageDecisionV1(
            outcome=TriageOutcomeV1.DECIDED,
            source=TriageSourceV1.LEXICAL,
            semantic_intent=intent,
            route=route_for_intent(intent),
            reason_code=TriageReasonCodeV1.LEXICAL_RULE_ACCEPTED,
            model_status=TriageModelStatusV1.NOT_ATTEMPTED,
            fallback_status=TriageFallbackStatusV1.NOT_ATTEMPTED,
            accepted_rule_id=rule_id,
            human_reason=human_reason,
        )

    def _predict_intent_with_ml(
        self,
        texto_usuario: str,
        diagnostics: _LegacyDiagnostics,
    ) -> _ModelAttempt:
        diagnostics.confidence = 0.5
        diagnostics.human_reason = "ML no disponible. Usando fallback heurístico."
        if not self.model_intent:
            return _ModelAttempt(status=TriageModelStatusV1.UNAVAILABLE)

        raw_label: str | None = None
        try:
            raw_value = self.model_intent.predict([texto_usuario])[0]
            raw_label = self._bounded_raw_label(raw_value)
            proba = self.model_intent.predict_proba([texto_usuario])[0]
            confidence = round(float(max(proba)), 2)
            diagnostics.confidence = confidence

            intent = normalize_intent(raw_label)
            if intent is IntentType.UNKNOWN and raw_label.strip().upper() != "UNKNOWN":
                diagnostics.human_reason = (
                    "ML emitió un label fuera del contrato. Usando fallback heurístico."
                )
                return _ModelAttempt(
                    status=TriageModelStatusV1.INCOMPATIBLE_LABEL,
                    raw_label=raw_label,
                )
            if confidence < 0.60:
                diagnostics.human_reason = (
                    f"ML con baja confianza ({confidence}). Usando fallback heurístico."
                )
                return _ModelAttempt(
                    status=TriageModelStatusV1.LOW_CONFIDENCE,
                    raw_label=raw_label,
                )

            diagnostics.human_reason = (
                f"Decisión ML Capa 0. Confianza: {confidence}."
            )
            return _ModelAttempt(
                status=TriageModelStatusV1.ACCEPTED,
                raw_label=raw_label,
                intent=intent,
            )
        except Exception as exc:
            logger.error("Error en predicción ML: %s", exc)
            diagnostics.confidence = 0.5
            diagnostics.human_reason = "ML falló. Usando fallback heurístico."
            return _ModelAttempt(
                status=TriageModelStatusV1.ERROR,
                raw_label=raw_label,
            )

    @staticmethod
    def _predict_intent_with_rules(
        texto_usuario: str,
        diagnostics: _LegacyDiagnostics,
    ) -> _FallbackAttempt:
        try:
            intent = normalize_intent(match_intent_by_keywords(texto_usuario))
            if intent is IntentType.UNKNOWN:
                diagnostics.confidence = min(diagnostics.confidence, 0.4)
                status = TriageFallbackStatusV1.NO_MATCH
                resolved_intent = None
            else:
                diagnostics.confidence = max(diagnostics.confidence, 0.55)
                status = TriageFallbackStatusV1.ACCEPTED
                resolved_intent = intent
            diagnostics.human_reason += (
                f" Fallback heurístico resolvió intent={intent.value}."
            )
            return _FallbackAttempt(status=status, intent=resolved_intent)
        except Exception as exc:
            logger.error("Error en fallback heurístico: %s", exc)
            diagnostics.human_reason = (
                "ML y fallback heurístico fallaron. Usando UNKNOWN."
            )
            diagnostics.confidence = 0.5
            return _FallbackAttempt(status=TriageFallbackStatusV1.ERROR)

    @staticmethod
    def _resolve_attempts(
        model_attempt: _ModelAttempt,
        fallback_attempt: _FallbackAttempt,
        diagnostics: _LegacyDiagnostics,
    ) -> TriageDecisionV1:
        if fallback_attempt.status is TriageFallbackStatusV1.ACCEPTED:
            assert fallback_attempt.intent is not None
            outcome = (
                TriageOutcomeV1.DEGRADED
                if model_attempt.status in _TECHNICAL_MODEL_FAILURES
                else TriageOutcomeV1.DECIDED
            )
            return TriageDecisionV1(
                outcome=outcome,
                source=TriageSourceV1.FALLBACK,
                semantic_intent=fallback_attempt.intent,
                route=route_for_intent(fallback_attempt.intent),
                reason_code=TriageReasonCodeV1.FALLBACK_ACCEPTED,
                model_status=model_attempt.status,
                fallback_status=fallback_attempt.status,
                raw_label=model_attempt.raw_label,
                human_reason=TriageRouter._bounded_reason(diagnostics.human_reason),
            )

        if fallback_attempt.status is TriageFallbackStatusV1.ERROR:
            outcome = TriageOutcomeV1.FAILED
            reason_code = TriageReasonCodeV1.FALLBACK_ERROR
        elif model_attempt.status in _TECHNICAL_MODEL_FAILURES:
            outcome = TriageOutcomeV1.FAILED
            reason_code = {
                TriageModelStatusV1.UNAVAILABLE: TriageReasonCodeV1.MODEL_UNAVAILABLE,
                TriageModelStatusV1.ERROR: TriageReasonCodeV1.MODEL_ERROR,
                TriageModelStatusV1.INCOMPATIBLE_LABEL: (
                    TriageReasonCodeV1.MODEL_LABEL_INCOMPATIBLE
                ),
            }[model_attempt.status]
        else:
            outcome = TriageOutcomeV1.UNCERTAIN
            reason_code = TriageReasonCodeV1.NO_ACCEPTABLE_CANDIDATE

        return TriageDecisionV1(
            outcome=outcome,
            source=None,
            semantic_intent=None,
            route=None,
            reason_code=reason_code,
            model_status=model_attempt.status,
            fallback_status=fallback_attempt.status,
            raw_label=model_attempt.raw_label,
            human_reason=TriageRouter._bounded_reason(diagnostics.human_reason),
        )

    @staticmethod
    def _apply_movement_gate(
        decision: TriageDecisionV1,
        estado_agente: Optional[Dict[str, Any]],
        detalles: Dict[str, Any],
        diagnostics: _LegacyDiagnostics,
    ) -> TriageDecisionV1:
        if (
            decision.semantic_intent not in MOTOR_BLOCKED_WHILE_MOVING
            or not TriageRouter._agente_en_movimiento(estado_agente, detalles)
        ):
            return decision

        diagnostics.confidence = 0.95
        diagnostics.human_reason = (
            "Agente en movimiento. Comando motor ignorado hasta terminar."
        )
        return decision.evolve(
            route=Route.NOOP,
            operational_override=TriageOperationalOverrideV1.MOVEMENT_BLOCKED,
            reason_code=TriageReasonCodeV1.MOVEMENT_BLOCKED,
            human_reason=diagnostics.human_reason,
        )

    @staticmethod
    def _lexical_candidate_allowed(
        value: str | IntentType | None,
        lexical_match: LexicalMatch,
    ) -> bool:
        intent = normalize_intent(value)
        route = route_for_intent(intent).value
        return (
            intent not in lexical_match.forbidden_intents
            and route not in lexical_match.forbidden_routes
        )

    @staticmethod
    def _agente_en_movimiento(
        estado_agente: Optional[Dict[str, Any]],
        detalles: Dict[str, Any],
    ) -> bool:
        claves_movimiento = (
            "is_moving",
            "esta_moviendo",
            "en_movimiento",
            "movimiento_activo",
        )
        valores_verdaderos = {
            "1",
            "true",
            "si",
            "sí",
            "yes",
            "moving",
            "moviendo",
            "en_movimiento",
        }

        for fuente in (estado_agente, detalles):
            if not isinstance(fuente, dict):
                continue
            for clave in claves_movimiento:
                valor = fuente.get(clave)
                if isinstance(valor, bool):
                    if valor:
                        return True
                    continue
                if str(valor or "").strip().lower() in valores_verdaderos:
                    return True

        estado_fisico = str(detalles.get("estado_fisico", "")).strip().lower()
        return estado_fisico in {"moving", "moviendo", "en movimiento"}

    @staticmethod
    def _bounded_raw_label(value: Any) -> str:
        return str(value).strip()[:128] or "<EMPTY>"

    @staticmethod
    def _bounded_reason(value: Any) -> str:
        return str(value or "")[:500]
