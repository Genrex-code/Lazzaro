"""
Validador de affordances para Capa 1.

No resuelve nombres ni habla con Unity. Solo decide si una accion fisica aplica
a un `PerceivedObject` ya seleccionado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .perception_models import PerceivedObject, normalize_text


ACTION_REQUIRED_AFFORDANCES: dict[str, set[str]] = {
    "agarrar_objeto": {"agarrable"},
    "aplicar_fuerza": {"empujable"},
    "interactuar_objeto_3d": {"mirar", "tocar", "activable", "inspeccionable"},
}

INTERACTION_REQUIRED_AFFORDANCES: dict[str, set[str]] = {
    "mirar": {"mirar", "inspeccionable"},
    "observar": {"mirar", "inspeccionable"},
    "inspeccionar": {"mirar", "inspeccionable"},
    "tocar": {"tocar"},
    "golpear": {"empujable", "tocar"},
    "usar": {"activable", "inspeccionable"},
    "activar": {"activable"},
}

ACTION_OPTIONAL_AFFORDANCES: dict[str, set[str]] = {
    "navegar_a_objeto": {"transitable", "punto_refugio", "da_sombra", "cubre", "descanso"},
    "alejarse_de_objeto": {"obstaculo", "peligro", "bloquea_paso"},
}

AFFORDANCE_QUERY_TERMS: dict[str, set[str]] = {
    "cubre": {"cubre", "cubrir", "cubrirme", "cubrirte", "protege", "proteger", "refugio"},
    "da_sombra": {"sombra", "sombreado", "cubrirme", "cubrirte", "refugio"},
    "punto_refugio": {"refugio", "esconder", "esconderme", "cubrirme", "cubrirte"},
    "ilumina": {"luz", "ilumina", "iluminar", "claro"},
    "descanso": {"descansar", "descanso", "reposar"},
    "peligro": {"peligro", "peligroso", "amenaza"},
    "recurso": {"recurso", "material", "suministro"},
    "clicable": {"clic", "click", "presiona", "boton"},
    "escribible": {"escribe", "campo", "texto"},
}


@dataclass(slots=True)
class AffordanceDecision:
    allowed: bool
    status: str
    action: str | None
    object_id: str
    display_name: str
    required: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    risk_penalty: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "action": self.action,
            "object_id": self.object_id,
            "display_name": self.display_name,
            "required": list(self.required),
            "present": list(self.present),
            "missing": list(self.missing),
            "matched": list(self.matched),
            "risk_penalty": round(float(self.risk_penalty), 4),
            "reason": self.reason,
        }


class AffordanceResolver:
    """Evalua affordances canonicas para acciones fisicas."""

    def validate(
        self,
        obj: PerceivedObject,
        action: str | None,
        *,
        interaction_type: str | None = None,
    ) -> AffordanceDecision:
        present = set(obj.affordances)
        required = self.required_for_action(action, interaction_type=interaction_type)
        matched = sorted(required & present)
        missing = sorted(required - present)
        risk_penalty = self.risk_penalty(obj, action)

        if required and not matched:
            return AffordanceDecision(
                allowed=False,
                status="not_allowed",
                action=action,
                object_id=obj.id,
                display_name=obj.display_name,
                required=sorted(required),
                present=sorted(present),
                missing=missing,
                matched=[],
                risk_penalty=risk_penalty,
                reason=f"La accion '{action}' no aplica a '{obj.display_name}'.",
            )

        if risk_penalty >= 50.0:
            return AffordanceDecision(
                allowed=False,
                status="unsafe",
                action=action,
                object_id=obj.id,
                display_name=obj.display_name,
                required=sorted(required),
                present=sorted(present),
                missing=missing,
                matched=matched,
                risk_penalty=risk_penalty,
                reason=f"'{obj.display_name}' esta marcado como peligroso para esa accion.",
            )

        return AffordanceDecision(
            allowed=True,
            status="allowed",
            action=action,
            object_id=obj.id,
            display_name=obj.display_name,
            required=sorted(required),
            present=sorted(present),
            missing=missing,
            matched=matched,
            risk_penalty=risk_penalty,
            reason="Affordances compatibles con la accion.",
        )

    def score(
        self,
        obj: PerceivedObject,
        *,
        action: str | None = None,
        requested_affordances: set[str] | None = None,
        interaction_type: str | None = None,
    ) -> float:
        present = set(obj.affordances)
        requested = requested_affordances or set()
        if requested:
            return 100.0 if requested & present else 0.0

        required = self.required_for_action(action, interaction_type=interaction_type)
        if required:
            return 100.0 if required & present else 0.0

        optional = ACTION_OPTIONAL_AFFORDANCES.get(str(action or ""), set())
        if optional and optional & present:
            return 100.0
        return 100.0

    def requested_from_query(self, query: str) -> set[str]:
        normalized_query = normalize_text(query).replace("_", " ")
        tokens = set(normalized_query.split())
        requested: set[str] = set()
        for affordance, terms in AFFORDANCE_QUERY_TERMS.items():
            if tokens & terms or any(term in normalized_query for term in terms):
                requested.add(affordance)
        return requested

    @staticmethod
    def required_for_action(action: str | None, *, interaction_type: str | None = None) -> set[str]:
        normalized_action = str(action or "").strip()
        if normalized_action == "interactuar_objeto_3d" and interaction_type:
            normalized_type = normalize_text(interaction_type)
            return set(INTERACTION_REQUIRED_AFFORDANCES.get(normalized_type, ACTION_REQUIRED_AFFORDANCES[normalized_action]))
        return set(ACTION_REQUIRED_AFFORDANCES.get(normalized_action, set()))

    @staticmethod
    def risk_penalty(obj: PerceivedObject, action: str | None) -> float:
        if "peligro" not in obj.affordances:
            return 0.0
        if action in {None, "describir", "mirar"}:
            return 5.0
        return 20.0
