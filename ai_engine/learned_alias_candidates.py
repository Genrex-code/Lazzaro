"""
Candidatos auditables para alias aprendidos de Capa 1.

Capa 1 no persiste estos candidatos. Solo los emite cuando una accion exitosa
respalda que un concepto humano puede apuntar a un objeto percibido.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .motor_resolution import MotorResolution
from .perception_models import normalize_text


MIN_ALIAS_LEARNING_SCORE = 92.0

ALIAS_STOPWORDS = {
    "a",
    "al",
    "camina",
    "caminar",
    "coge",
    "de",
    "del",
    "dirigete",
    "el",
    "en",
    "hacia",
    "ir",
    "la",
    "las",
    "lo",
    "los",
    "mira",
    "muevete",
    "recoge",
    "toca",
    "usa",
    "ve",
}


@dataclass(slots=True)
class LearnedAliasCandidate:
    object_id: str
    human_concept: str
    confidence: float
    source: str = "fuzzy_match"
    evidence: str = ""
    action: str | None = None
    target_display_name: str | None = None
    legacy_nombre_objeto: str | None = None
    approved: bool = False

    def __post_init__(self) -> None:
        if type(self.approved) is not bool:
            raise TypeError("LearnedAliasCandidate.approved debe ser un bool estricto")

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "human_concept": self.human_concept,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence": self.evidence,
            "action": self.action,
            "target_display_name": self.target_display_name,
            "legacy_nombre_objeto": self.legacy_nombre_objeto,
            "approved": self.approved,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LearnedAliasCandidate" | None:
        object_id = str(value.get("object_id") or "").strip()
        human_concept = normalize_alias_concept(value.get("human_concept"))
        if not object_id or not human_concept:
            return None
        try:
            confidence = float(value.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        return cls(
            object_id=object_id,
            human_concept=human_concept,
            confidence=max(0.0, min(1.0, confidence)),
            source=str(value.get("source") or "fuzzy_match").strip() or "fuzzy_match",
            evidence=str(value.get("evidence") or "").strip(),
            action=str(value.get("action") or "").strip() or None,
            target_display_name=str(value.get("target_display_name") or "").strip() or None,
            legacy_nombre_objeto=str(value.get("legacy_nombre_objeto") or "").strip() or None,
            approved=value.get("approved") is True,
        )


def build_learned_alias_candidate(
    *,
    user_query: str,
    resolution: MotorResolution | dict[str, Any],
    action_success: bool,
    action: str | None = None,
    source: str = "fuzzy_match",
) -> LearnedAliasCandidate | None:
    if action_success is not True:
        return None

    resolution_dict = resolution.to_dict() if isinstance(resolution, MotorResolution) else resolution
    if not isinstance(resolution_dict, dict) or resolution_dict.get("status") != "ready":
        return None

    selected = _selected_candidate(resolution_dict)
    object_id = str(
        resolution_dict.get("target_id")
        or selected.get("target_id")
        or ""
    ).strip()
    if not object_id:
        return None

    confidence_percent = _lexical_confidence_percent(resolution_dict, selected)
    if confidence_percent < MIN_ALIAS_LEARNING_SCORE:
        return None

    human_concept = normalize_alias_concept(user_query)
    if not human_concept:
        return None

    if _concept_known_for_object(human_concept, object_id, resolution_dict, selected):
        return None

    display_name = str(
        resolution_dict.get("target_display_name")
        or selected.get("display_name")
        or object_id
    ).strip()
    legacy_name = str(
        resolution_dict.get("legacy_nombre_objeto")
        or selected.get("legacy_nombre_objeto")
        or display_name
    ).strip()

    return LearnedAliasCandidate(
        object_id=object_id,
        human_concept=human_concept,
        confidence=confidence_percent / 100.0,
        source=source,
        evidence=f"usuario dijo '{human_concept}' y accion fue exitosa",
        action=action or str(resolution_dict.get("action") or "").strip() or None,
        target_display_name=display_name,
        legacy_nombre_objeto=legacy_name,
        approved=False,
    )


def normalize_alias_concept(value: object) -> str:
    normalized = normalize_text(value).replace("_", " ").replace("-", " ")
    tokens = [token for token in normalized.split() if token and token not in ALIAS_STOPWORDS]
    return " ".join(tokens)


def alias_key(object_id: str, human_concept: str) -> str:
    return f"{normalize_text(object_id).replace(' ', '_')}::{normalize_alias_concept(human_concept)}"


def _selected_candidate(resolution: dict[str, Any]) -> dict[str, Any]:
    audit = resolution.get("audit")
    if isinstance(audit, dict) and isinstance(audit.get("selected_candidate"), dict):
        return audit["selected_candidate"]
    candidates = resolution.get("candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        return candidates[0]
    return {}


def _lexical_confidence_percent(resolution: dict[str, Any], selected: dict[str, Any]) -> float:
    components = selected.get("score_components")
    if not isinstance(components, dict):
        audit = resolution.get("audit")
        if isinstance(audit, dict):
            selected_audit = audit.get("selected_candidate")
            if isinstance(selected_audit, dict):
                components = selected_audit.get("score_components")

    scores = [resolution.get("confidence"), selected.get("confidence")]
    if isinstance(components, dict):
        scores.extend(
            [
                components.get("name_score"),
                components.get("alias_score"),
                components.get("final_score"),
            ]
        )
    numeric_scores = []
    for score in scores:
        try:
            numeric_scores.append(float(score))
        except (TypeError, ValueError):
            continue
    return max(numeric_scores or [0.0])


def _concept_known_for_object(
    human_concept: str,
    object_id: str,
    resolution: dict[str, Any],
    selected: dict[str, Any],
) -> bool:
    known_values = {
        object_id,
        str(resolution.get("target_display_name") or ""),
        str(resolution.get("legacy_nombre_objeto") or ""),
        str(selected.get("display_name") or ""),
        str(selected.get("legacy_nombre_objeto") or ""),
    }
    aliases = selected.get("known_aliases")
    if isinstance(aliases, list):
        known_values.update(str(alias or "") for alias in aliases)

    normalized_concept = normalize_alias_concept(human_concept)
    for value in known_values:
        normalized = normalize_alias_concept(value)
        if normalized_concept == normalized:
            return True
    return False
