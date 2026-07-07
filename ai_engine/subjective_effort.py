"""
Calculo auditable de esfuerzo subjetivo para acciones fisicas.

Este modulo no valida rutas ni decide si una accion puede ejecutarse. Solo
estima como se siente el esfuerzo segun estado interno y datos perceptivos.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Literal


EffortLabel = Literal["bajo", "medio", "alto", "critico"]


BASE_ACTION_COST: dict[str, float] = {
    "navegar_a_objeto": 1.0,
    "navegar_a_posicion": 1.0,
    "alejarse_de_objeto": 1.1,
    "girar_avatar": 0.35,
    "interactuar_objeto_3d": 0.65,
    "agarrar_objeto": 0.85,
    "soltar_objeto": 0.35,
    "aplicar_fuerza": 1.4,
}


@dataclass(slots=True)
class SubjectiveEffort:
    action: str
    score: float
    label: EffortLabel
    base_action_cost: float
    distance_factor: float
    fatigue: float
    stress: float
    familiarity_bonus: float
    physical_validation_impact: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "score": round(float(self.score), 4),
            "label": self.label,
            "base_action_cost": round(float(self.base_action_cost), 4),
            "distance_factor": round(float(self.distance_factor), 4),
            "fatigue": round(float(self.fatigue), 4),
            "stress": round(float(self.stress), 4),
            "familiarity_bonus": round(float(self.familiarity_bonus), 4),
            "physical_validation_impact": self.physical_validation_impact,
            "soft_context": self.soft_context,
        }

    @property
    def soft_context(self) -> str:
        return f"esfuerzo percibido {self.label} ({self.score:.2f})"


class SubjectiveEffortCalculator:
    """Aplica la formula inicial de Fase 7 sin tocar guardarrailes fisicos."""

    def calculate(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        identity_state: object | dict[str, Any] | None = None,
        familiarity_bonus: float | None = None,
    ) -> SubjectiveEffort | None:
        normalized_action = str(action or "").strip()
        base_cost = BASE_ACTION_COST.get(normalized_action)
        if base_cost is None:
            return None

        params = params if isinstance(params, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        fatigue = self._fatigue_from_state(identity_state)
        stress = self._stress_from_state(identity_state)
        distance_factor = self._distance_factor(normalized_action, params, metadata)
        bonus = self._familiarity_bonus(metadata, familiarity_bonus)

        score = base_cost * distance_factor * (1.0 + fatigue * 0.8) * (1.0 + stress * 0.4) - bonus
        score = max(0.0, score)
        return SubjectiveEffort(
            action=normalized_action,
            score=score,
            label=self._label(score),
            base_action_cost=base_cost,
            distance_factor=distance_factor,
            fatigue=fatigue,
            stress=stress,
            familiarity_bonus=bonus,
            physical_validation_impact=False,
        )

    @staticmethod
    def _distance_factor(action: str, params: dict[str, Any], metadata: dict[str, Any]) -> float:
        explicit_distance = _safe_float(
            params.get("distancia")
            or metadata.get("distancia_estimada")
            or metadata.get("distance_estimate")
        )
        if explicit_distance is not None:
            return _bounded_distance(explicit_distance)

        if action == "navegar_a_objeto":
            target_distance = _distance_from_motor_resolution(metadata)
            return _bounded_distance(target_distance if target_distance is not None else 1.0)

        if action == "alejarse_de_objeto":
            return 2.0

        if action == "navegar_a_posicion":
            distance_to_target = _distance_from_position(params, metadata)
            return _bounded_distance(distance_to_target if distance_to_target is not None else 1.0)

        if action == "girar_avatar":
            degrees = abs(_safe_float(params.get("grados")) or 90.0)
            return max(0.25, min(4.0, degrees / 90.0))

        if action == "aplicar_fuerza":
            force = _safe_float(params.get("fuerza")) or 10.0
            return max(0.5, min(6.0, force / 10.0))

        return 1.0

    @staticmethod
    def _fatigue_from_state(identity_state: object | dict[str, Any] | None) -> float:
        if identity_state is None:
            return 0.0
        fatigue = _read_state_number(identity_state, "fatiga", "cansancio")
        if fatigue is not None:
            return _normalize_ratio(fatigue)
        energy = _read_state_number(identity_state, "energia")
        if energy is None:
            return 0.0
        return 1.0 - _normalize_ratio(energy)

    @staticmethod
    def _stress_from_state(identity_state: object | dict[str, Any] | None) -> float:
        stress = _read_state_number(identity_state, "estres", "stress")
        return _normalize_ratio(stress or 0.0)

    @staticmethod
    def _familiarity_bonus(metadata: dict[str, Any], explicit_bonus: float | None) -> float:
        raw_bonus = explicit_bonus
        if raw_bonus is None:
            raw_bonus = _safe_float(metadata.get("familiaridad_bonus") or metadata.get("familiarity_bonus"))
        if raw_bonus is None:
            raw_bonus = 0.0
        return max(0.0, min(5.0, float(raw_bonus)))

    @staticmethod
    def _label(score: float) -> EffortLabel:
        if score < 3.0:
            return "bajo"
        if score < 8.0:
            return "medio"
        if score < 14.0:
            return "alto"
        return "critico"


def _read_state_number(identity_state: object | dict[str, Any], *names: str) -> float | None:
    if isinstance(identity_state, dict):
        for name in names:
            value = _safe_float(identity_state.get(name))
            if value is not None:
                return value
        return None

    obtener_estado = getattr(identity_state, "obtener_estado_completo", None)
    if callable(obtener_estado):
        try:
            state_dict = obtener_estado()
            if isinstance(state_dict, dict):
                value = _read_state_number(state_dict, *names)
                if value is not None:
                    return value
        except Exception:
            pass

    for name in names:
        value = _safe_float(getattr(identity_state, name, None))
        if value is not None:
            return value
    return None


def _distance_from_motor_resolution(metadata: dict[str, Any]) -> float | None:
    resolution = metadata.get("motor_resolution")
    if not isinstance(resolution, dict):
        return None

    candidates = resolution.get("candidates")
    if isinstance(candidates, list):
        target_id = str(resolution.get("target_id") or "").strip()
        target_name = str(resolution.get("target_display_name") or resolution.get("legacy_nombre_objeto") or "").strip()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_ids = {
                str(candidate.get("target_id") or "").strip(),
                str(candidate.get("display_name") or "").strip(),
                str(candidate.get("legacy_nombre_objeto") or "").strip(),
            }
            if not target_id and not target_name:
                return _safe_float(candidate.get("distance"))
            if target_id in candidate_ids or target_name in candidate_ids:
                return _safe_float(candidate.get("distance"))

    audit = metadata.get("motor_audit")
    if isinstance(audit, dict):
        selected = audit.get("selected_candidate")
        if isinstance(selected, dict):
            return _safe_float(selected.get("distance"))
    return None


def _distance_from_position(params: dict[str, Any], metadata: dict[str, Any]) -> float | None:
    current = metadata.get("posicion_actual") or metadata.get("current_position")
    if not isinstance(current, dict):
        return None

    x1 = _safe_float(current.get("x"))
    y1 = _safe_float(current.get("y"))
    z1 = _safe_float(current.get("z"))
    x2 = _safe_float(params.get("x"))
    y2 = _safe_float(params.get("y"))
    z2 = _safe_float(params.get("z"))
    if None in {x1, y1, z1, x2, y2, z2}:
        return None
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


def _bounded_distance(value: float) -> float:
    return max(0.25, min(25.0, float(value)))


def _normalize_ratio(value: float) -> float:
    numeric = float(value)
    if numeric > 1.0:
        numeric = numeric / 100.0
    return max(0.0, min(1.0, numeric))


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
