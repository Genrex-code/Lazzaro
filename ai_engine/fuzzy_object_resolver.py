"""
Resolver fuzzy aislado para grounding de objetos de Capa 1.

No llama Unity, Chroma ni LLM. Solo puntua objetos percibidos y devuelve una
resolucion auditable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterable

from thefuzz import fuzz

from core.logger import get_logger

from .affordance_resolver import AffordanceResolver
from .motor_resolution import CandidateCorrection, CandidateMatch, MotorResolution
from .perception_models import PerceivedObject, PerceptionSnapshot, normalize_perception, normalize_text

logger = get_logger(__name__)


THRESHOLDS: dict[str, tuple[int, int]] = {
    "MOTOR_SIMPLE": (88, 68),
    "MOTOR_REFERENCE": (82, 60),
    "PERCEPTION": (65, 45),
    "INTERACTION": (90, 72),
    "DESKTOP_FUTURE": (92, 80),
}

CONTEXT_BONUS_LIMIT = 10.0
FATIGUE_CONTEXT_BONUS = 6.0
CURIOSITY_CONTEXT_BONUS = 4.0
STRESS_CONTEXT_BONUS = 4.0
AFFECTIVE_CONTEXT_BONUS_LIMIT = 4.0

POSITIVE_AFFECTS = {"alivio", "descanso", "calma", "seguridad", "refugio"}
AVOIDANCE_AFFECTS = {"alerta", "peligro", "dolor", "miedo", "estres"}

REFERENCE_TERMS = {
    "eso",
    "ese",
    "esa",
    "aquel",
    "aquella",
    "enfrente",
    "frente",
    "cercano",
    "cercana",
    "mas_cercano",
}

QUERY_STOPWORDS = {
    "a",
    "al",
    "algo",
    "camina",
    "caminar",
    "coge",
    "con",
    "de",
    "del",
    "dirigete",
    "el",
    "en",
    "esa",
    "ese",
    "eso",
    "hacia",
    "ir",
    "la",
    "las",
    "lo",
    "los",
    "mira",
    "muevete",
    "para",
    "recoge",
    "te",
    "toca",
    "usa",
    "ve",
}


class FuzzyObjectResolver:
    """Resuelve objetos percibidos con scoring fuzzy auditable."""

    def __init__(
        self,
        affordance_resolver: AffordanceResolver | None = None,
        learned_alias_provider: Callable[[list[str] | None], dict[str, list[str]]] | None = None,
        affective_memory_provider: Callable[[list[PerceivedObject] | None], dict[str, list[dict[str, Any]]]] | None = None,
    ):
        self.affordances = affordance_resolver or AffordanceResolver()
        self.learned_alias_provider = learned_alias_provider
        self.affective_memory_provider = affective_memory_provider

    def resolve(
        self,
        query: str,
        objects: PerceptionSnapshot | Iterable[PerceivedObject] | dict[str, Any] | str | None,
        *,
        intent: str = "MOTOR_SIMPLE",
        action: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> MotorResolution:
        snapshot = self._coerce_snapshot(objects)
        normalized_intent = self._normalize_intent(intent, action)
        execute_threshold, clarify_threshold = THRESHOLDS[normalized_intent]
        normalized_query = self._normalize_query(query)

        objects, learned_alias_hits = self._objects_with_learned_aliases(snapshot.objects)
        affective_memory_hits = self._affective_hints_for_objects(objects)

        audit_base = {
            "query": str(query or ""),
            "normalized_query": normalized_query,
            "intent": normalized_intent,
            "snapshot_id": snapshot.snapshot_id,
            "threshold_execute": execute_threshold,
            "threshold_clarify": clarify_threshold,
            "context_bonus_limit": CONTEXT_BONUS_LIMIT,
            "unknown_affordances": list(snapshot.unknown_affordances),
            "learned_alias_hits": learned_alias_hits,
            "affective_memory_hits": affective_memory_hits,
        }

        if not normalized_query:
            return self._resolution(
                status="not_found",
                action=action,
                reason="Query vacia para resolver objeto.",
                audit={**audit_base, "status_reason": "empty_query"},
            )

        if not objects:
            return self._resolution(
                status="not_found",
                action=action,
                reason="No hay objetos percibidos para resolver.",
                audit={**audit_base, "status_reason": "empty_snapshot"},
            )

        matches = self._score_objects(
            normalized_query,
            objects,
            action=action,
            context=context or {},
            affective_hints=affective_memory_hits,
        )
        candidates = matches[:3]
        best = matches[0] if matches else None
        second = matches[1] if len(matches) > 1 else None

        blocking_status = self._blocking_affordance_status(best) if best else None
        if blocking_status:
            return self._resolution(
                status=blocking_status,
                action=action,
                match=best,
                candidates=candidates,
                reason=str(best.affordance_decision.get("reason") or "La accion no aplica al objeto seleccionado."),
                audit={
                    **audit_base,
                    "selected_candidate": best.to_dict(),
                    "rejected_candidates": [candidate.to_dict() for candidate in matches[1:4]],
                    "status_reason": "affordance_block",
                },
            )

        if best is None or best.confidence < clarify_threshold:
            return self._resolution(
                status="not_found",
                action=action,
                confidence=best.confidence if best else 0.0,
                candidates=candidates,
                reason="Ningun objeto supera el umbral de aclaracion.",
                audit={
                    **audit_base,
                    "selected_candidate": None,
                    "rejected_candidates": [candidate.to_dict() for candidate in candidates],
                    "status_reason": "below_clarify_threshold",
                },
            )

        if (
            second is not None
            and best.confidence >= clarify_threshold
            and second.confidence >= clarify_threshold
            and abs(best.confidence - second.confidence) <= 8
        ):
            return self._resolution(
                status="requires_clarification",
                action=action,
                match=best,
                candidates=candidates,
                reason="Candidatos cercanos en score requieren aclaracion.",
                audit={
                    **audit_base,
                    "selected_candidate": best.to_dict(),
                    "rejected_candidates": [candidate.to_dict() for candidate in matches[1:4]],
                    "status_reason": "close_candidates",
                },
            )

        candidate_correction = self._candidate_correction(normalized_query, best)
        if candidate_correction is not None:
            return self._resolution(
                status="requires_clarification",
                action=action,
                match=best,
                candidates=candidates,
                candidate_correction=candidate_correction,
                reason="El objeto pedido no coincide exactamente con la percepcion actual.",
                audit={
                    **audit_base,
                    "selected_candidate": best.to_dict(),
                    "candidate_correction": candidate_correction.to_dict(),
                    "rejected_candidates": [candidate.to_dict() for candidate in matches[1:4]],
                    "status_reason": "candidate_correction",
                },
            )

        if best.confidence < execute_threshold:
            return self._resolution(
                status="requires_clarification",
                action=action,
                match=best,
                candidates=candidates,
                reason="El mejor candidato esta en zona de aclaracion.",
                audit={
                    **audit_base,
                    "selected_candidate": best.to_dict(),
                    "rejected_candidates": [candidate.to_dict() for candidate in matches[1:4]],
                    "status_reason": "between_clarify_and_execute",
                },
            )

        return self._resolution(
            status="ready",
            action=action,
            match=best,
            candidates=candidates,
            reason="Objeto resuelto con confianza suficiente.",
            audit={
                **audit_base,
                "selected_candidate": best.to_dict(),
                "rejected_candidates": [candidate.to_dict() for candidate in matches[1:4]],
                "status_reason": "above_execute_threshold",
            },
        )

    resolver = resolve

    def _score_objects(
        self,
        normalized_query: str,
        objects: list[PerceivedObject],
        *,
        action: str | None,
        context: dict[str, Any],
        affective_hints: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[CandidateMatch]:
        requested_affordances = self.affordances.requested_from_query(normalized_query)
        scored: list[CandidateMatch] = []
        for obj in objects:
            unmatched_descriptors = self._unmatched_query_descriptors(
                normalized_query,
                obj,
                requested_affordances=requested_affordances,
            )
            name_score = self._name_score(normalized_query, obj)
            alias_score = self._alias_score(normalized_query, obj)
            affordance_decision = self.affordances.validate(obj, action)
            affordance_score = self.affordances.score(
                obj,
                action=action,
                requested_affordances=requested_affordances,
            )

            if requested_affordances:
                name_score = max(name_score, affordance_score)
                alias_score = max(alias_score, affordance_score)

            matching_affective_hints = self._matching_affective_hints(obj, affective_hints or {})
            context_bonus, context_components = self._context_bonus_components(
                obj,
                context,
                action=action,
                affective_hints=matching_affective_hints,
            )
            risk_penalty = affordance_decision.risk_penalty
            final_score = (
                name_score * 0.50
                + alias_score * 0.20
                + affordance_score * 0.20
                + context_bonus * 0.10
                - risk_penalty
            )
            final_score = max(0.0, min(100.0, final_score))
            components = {
                "name_score": round(name_score, 4),
                "alias_score": round(alias_score, 4),
                "affordance_score": round(affordance_score, 4),
                "context_bonus": round(context_bonus, 4),
                "state_context_bonus": round(context_components["state_context_bonus"], 4),
                "affective_bonus": round(context_components["affective_bonus"], 4),
                "risk_penalty": round(risk_penalty, 4),
                "final_score": round(final_score, 4),
                "unmatched_descriptor_count": len(unmatched_descriptors),
            }
            scored.append(
                CandidateMatch(
                    target_id=obj.id,
                    display_name=obj.display_name,
                    legacy_nombre_objeto=obj.display_name,
                    confidence=final_score,
                    score_components=components,
                    affordance_decision=affordance_decision.to_dict(),
                    distance=obj.distance,
                    position=obj.position,
                    unmatched_descriptors=unmatched_descriptors,
                    known_aliases=list(obj.aliases),
                    reason=self._candidate_reason(components),
                )
            )

        return sorted(scored, key=lambda candidate: candidate.confidence, reverse=True)

    def _affective_hints_for_objects(
        self,
        objects: list[PerceivedObject],
    ) -> dict[str, list[dict[str, Any]]]:
        provider = self.affective_memory_provider
        if not callable(provider) or not objects:
            return {}
        try:
            hints = provider(objects)
        except Exception as exc:
            logger.warning("Proveedor de memoria afectiva espacial fallo: %s", exc, exc_info=True)
            return {}
        return hints if isinstance(hints, dict) else {}

    def _objects_with_learned_aliases(
        self,
        objects: list[PerceivedObject],
    ) -> tuple[list[PerceivedObject], dict[str, list[str]]]:
        provider = self.learned_alias_provider
        if not callable(provider) or not objects:
            return objects, {}

        object_ids = [obj.id for obj in objects]
        try:
            learned_aliases = provider(object_ids)
        except Exception as exc:
            logger.warning("Proveedor de aliases aprendidos fallo: %s", exc, exc_info=True)
            return objects, {}
        if not isinstance(learned_aliases, dict):
            return objects, {}

        enriched: list[PerceivedObject] = []
        hits: dict[str, list[str]] = {}
        for obj in objects:
            aliases = list(obj.aliases)
            for key in (obj.id, obj.display_name):
                raw_aliases = learned_aliases.get(key)
                if not isinstance(raw_aliases, list):
                    continue
                for alias in raw_aliases:
                    alias_text = str(alias or "").strip()
                    if alias_text and alias_text not in aliases:
                        aliases.append(alias_text)
                        hits.setdefault(obj.id, []).append(alias_text)

            if aliases == obj.aliases:
                enriched.append(obj)
                continue

            enriched.append(
                PerceivedObject(
                    id=obj.id,
                    display_name=obj.display_name,
                    aliases=aliases,
                    position=obj.position,
                    screen_position=obj.screen_position,
                    distance=obj.distance,
                    affordances=list(obj.affordances),
                    source=obj.source,
                    confidence=obj.confidence,
                )
            )
        return enriched, hits

    @staticmethod
    def _coerce_snapshot(
        objects: PerceptionSnapshot | Iterable[PerceivedObject] | dict[str, Any] | str | None,
    ) -> PerceptionSnapshot:
        if isinstance(objects, PerceptionSnapshot):
            return objects
        if isinstance(objects, (dict, str)) or objects is None:
            return normalize_perception(objects)
        object_list = [obj for obj in objects if isinstance(obj, PerceivedObject)]
        return PerceptionSnapshot(snapshot_id="manual_objects", objects=object_list, source="unity")

    @staticmethod
    def _normalize_intent(intent: str, action: str | None) -> str:
        if action in {"interactuar_objeto_3d", "agarrar_objeto", "aplicar_fuerza"}:
            return "INTERACTION"
        normalized = normalize_text(intent).upper()
        return normalized if normalized in THRESHOLDS else "MOTOR_SIMPLE"

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized = normalize_text(query)
        normalized = normalized.replace("_", " ")
        tokens = [token for token in normalized.split() if token not in QUERY_STOPWORDS]
        return " ".join(tokens) or normalized

    @staticmethod
    def _name_score(query: str, obj: PerceivedObject) -> float:
        names = [obj.display_name, obj.id, obj.display_name.replace("_", " ")]
        names.extend(obj.aliases)
        return float(max(_fuzzy_score(query, name) for name in names if str(name or "").strip()) or 0.0)

    @staticmethod
    def _alias_score(query: str, obj: PerceivedObject) -> float:
        aliases = obj.aliases or [obj.display_name]
        return float(max(_fuzzy_score(query, alias) for alias in aliases if str(alias or "").strip()) or 0.0)

    @staticmethod
    def _context_bonus(obj: PerceivedObject, context: dict[str, Any]) -> float:
        bonus, _components = FuzzyObjectResolver._context_bonus_components(obj, context)
        return bonus

    @staticmethod
    def _context_bonus_components(
        obj: PerceivedObject,
        context: dict[str, Any],
        *,
        action: str | None = None,
        affective_hints: list[dict[str, Any]] | None = None,
    ) -> tuple[float, dict[str, float]]:
        state_bonus = 0.0
        affordances = set(obj.affordances)
        fatigue = _context_ratio(context, "fatiga", "cansancio")
        curiosity = _context_ratio(context, "curiosidad", "curiosity")
        stress = _context_ratio(context, "estres", "stress")

        if fatigue > 0.75 and {"da_sombra", "cubre", "descanso"} & affordances:
            state_bonus += FATIGUE_CONTEXT_BONUS
        if curiosity > 0.70 and "nuevo" in affordances:
            state_bonus += CURIOSITY_CONTEXT_BONUS
        if stress > 0.70 and {"peligro", "obstaculo", "bloquea_paso"} & affordances:
            state_bonus += STRESS_CONTEXT_BONUS

        affective_bonus = FuzzyObjectResolver._affective_context_bonus(
            affective_hints or [],
            action=action,
        )
        total = min(CONTEXT_BONUS_LIMIT, state_bonus + affective_bonus)
        return total, {
            "state_context_bonus": min(CONTEXT_BONUS_LIMIT, state_bonus),
            "affective_bonus": affective_bonus,
        }

    @staticmethod
    def _affective_context_bonus(
        hints: list[dict[str, Any]],
        *,
        action: str | None,
    ) -> float:
        bonus = 0.0
        for hint in hints:
            if not isinstance(hint, dict) or hint.get("confirmed") is not True:
                continue
            affect = normalize_text(hint.get("affect")).replace(" ", "_")
            weight = _normalize_ratio(_safe_float(hint.get("weight", 0.0)))
            if affect in POSITIVE_AFFECTS:
                bonus += weight * AFFECTIVE_CONTEXT_BONUS_LIMIT
            elif affect in AVOIDANCE_AFFECTS and action == "alejarse_de_objeto":
                bonus += weight * AFFECTIVE_CONTEXT_BONUS_LIMIT
        return min(AFFECTIVE_CONTEXT_BONUS_LIMIT, bonus)

    @staticmethod
    def _matching_affective_hints(
        obj: PerceivedObject,
        hints: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        keys = {obj.id, obj.display_name}
        for value in [obj.id, obj.display_name, *obj.aliases]:
            normalized = normalize_text(value).replace("-", "_").replace(" ", "_")
            if not normalized:
                continue
            keys.add(f"class:{normalized}")
            for token in normalized.split("_"):
                if token:
                    keys.add(f"class:{token}")

        for key in keys:
            entries = hints.get(key)
            if isinstance(entries, list):
                matches.extend(entry for entry in entries if isinstance(entry, dict))
        return matches

    @staticmethod
    def _unmatched_query_descriptors(
        query: str,
        obj: PerceivedObject,
        *,
        requested_affordances: set[str],
    ) -> list[str]:
        if requested_affordances:
            return []

        query_tokens = [token for token in query.split() if token and token not in QUERY_STOPWORDS]
        if not query_tokens:
            return []

        labels = [obj.id, obj.display_name, obj.display_name.replace("_", " "), *obj.aliases]
        label_tokens: set[str] = set()
        for label in labels:
            normalized_label = normalize_text(label).replace("_", " ")
            label_tokens.update(token for token in normalized_label.split() if token)

        unmatched: list[str] = []
        for token in query_tokens:
            if token in label_tokens:
                continue
            if any(fuzz.ratio(token, label_token) >= 82 for label_token in label_tokens):
                continue
            unmatched.append(token)
        return unmatched

    @staticmethod
    def _blocking_affordance_status(candidate: CandidateMatch) -> str | None:
        decision = candidate.affordance_decision
        if decision.get("allowed", True):
            return None
        components = candidate.score_components
        if max(components.get("name_score", 0.0), components.get("alias_score", 0.0)) < 70.0:
            return None
        status = str(decision.get("status") or "not_allowed")
        return "unsafe" if status == "unsafe" else "not_allowed"

    @staticmethod
    def _candidate_reason(components: dict[str, float]) -> str:
        strongest = max(
            ("name_score", "alias_score", "affordance_score", "context_bonus"),
            key=lambda key: components.get(key, 0.0),
        )
        return f"match por {strongest}"

    @staticmethod
    def _candidate_correction(query: str, candidate: CandidateMatch) -> CandidateCorrection | None:
        if not candidate.unmatched_descriptors:
            return None

        return CandidateCorrection(
            requested=query,
            suggested_id=candidate.target_id,
            suggested_display_name=candidate.display_name,
            legacy_nombre_objeto=candidate.legacy_nombre_objeto,
            score=candidate.confidence,
            requires_confirmation=True,
            reason=f"descriptor_sin_respaldo: {', '.join(candidate.unmatched_descriptors)}",
        )

    @staticmethod
    def _resolution(
        *,
        status: str,
        action: str | None,
        reason: str,
        audit: dict[str, Any],
        match: CandidateMatch | None = None,
        confidence: float | None = None,
        candidates: list[CandidateMatch] | None = None,
        candidate_correction: CandidateCorrection | None = None,
    ) -> MotorResolution:
        resolution = MotorResolution(
            status=status,  # type: ignore[arg-type]
            action=action,
            target_id=match.target_id if match else None,
            target_display_name=match.display_name if match else None,
            legacy_nombre_objeto=match.legacy_nombre_objeto if match else None,
            confidence=confidence if confidence is not None else (match.confidence if match else 0.0),
            candidates=candidates or [],
            candidate_correction=candidate_correction,
            reason=reason,
            audit=audit,
        )
        logger.debug("FUZZ_DECISION %s", resolution.to_dict())
        return resolution


def _fuzzy_score(query: str, value: object) -> int:
    normalized_value = normalize_text(value).replace("_", " ")
    if not normalized_value:
        return 0
    return max(
        fuzz.ratio(query, normalized_value),
        fuzz.partial_ratio(query, normalized_value),
        fuzz.token_set_ratio(query, normalized_value),
    )


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _context_ratio(context: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _safe_float(context.get(key))
        if value:
            return _normalize_ratio(value)

    nested = context.get("estado") or context.get("estado_interno") or context.get("identity_state")
    if isinstance(nested, dict):
        for key in keys:
            value = _safe_float(nested.get(key))
            if value:
                return _normalize_ratio(value)
    return 0.0


def _normalize_ratio(value: float) -> float:
    numeric = float(value)
    if numeric > 1.0:
        numeric = numeric / 100.0
    return max(0.0, min(1.0, numeric))
