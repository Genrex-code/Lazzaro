"""Contrato fail-closed para receipts de efectos materiales externos.

No describe el lifecycle de una accion (autorizar/despachar/ejecutar).  Su unica
responsabilidad es decidir si un consumer aporto evidencia operacional minima y
coherente antes de permitir un outcome terminal para una accion material.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping


class MaterialEffectStatus(str, Enum):
    APPLIED = "applied"
    NO_EFFECT = "no_effect"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class MaterialEffectKind(str, Enum):
    INTERACT = "interact"
    GRAB = "grab"
    RELEASE = "release"
    FORCE = "force"


class MaterialAttestationStatus(str, Enum):
    VALID = "valid"
    NOT_REQUIRED = "not_required"
    MISSING = "missing"
    MALFORMED = "malformed"
    ACTION_ID_MISMATCH = "action_id_mismatch"
    EFFECT_MISMATCH = "effect_mismatch"
    STATUS_MISMATCH = "status_mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


MATERIAL_ACTION_EFFECTS: dict[str, MaterialEffectKind] = {
    "interactuar_objeto_3d": MaterialEffectKind.INTERACT,
    "agarrar_objeto": MaterialEffectKind.GRAB,
    "soltar_objeto": MaterialEffectKind.RELEASE,
    "aplicar_fuerza": MaterialEffectKind.FORCE,
}

_TOP_LEVEL_KEYS = {"action_id", "effect_kind", "status", "object_id", "evidence"}
_MAX_EVIDENCE_FIELDS = 16
_MAX_TEXT_LENGTH = 256
_COMMON_EVIDENCE_KEYS = {"reason_code", "observer", "distance"}
_EVIDENCE_KEYS_BY_EFFECT: dict[str, set[str]] = {
    "interact": _COMMON_EVIDENCE_KEYS
    | {"effect_observed", "interaction_type"},
    "grab": _COMMON_EVIDENCE_KEYS
    | {"held_before", "held_after", "held_reference_changed", "parent_is_hold_point"},
    "release": _COMMON_EVIDENCE_KEYS
    | {
        "held_before",
        "held_after",
        "held_reference_changed",
        "parent_is_hold_point",
        "throw_impulse_requested",
        "throw_impulse_applied",
    },
    "force": _COMMON_EVIDENCE_KEYS
    | {
        "fixed_update_observed",
        "velocity_before",
        "velocity_after",
        "directional_delta",
        "position_delta",
    },
}


@dataclass(frozen=True)
class MaterialEffectReceipt:
    action_id: str
    effect_kind: MaterialEffectKind
    status: MaterialEffectStatus
    object_id: str
    evidence: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "effect_kind": self.effect_kind.value,
            "status": self.status.value,
            "object_id": self.object_id,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class MaterialAttestationValidation:
    status: MaterialAttestationStatus
    receipt: MaterialEffectReceipt | None = None

    @property
    def valid(self) -> bool:
        return self.status in {
            MaterialAttestationStatus.VALID,
            MaterialAttestationStatus.NOT_REQUIRED,
        }


def validate_material_attestation(
    *,
    action_name: str,
    action_id: str,
    success: bool,
    payload: Mapping[str, Any],
) -> MaterialAttestationValidation:
    """Valida el wire receipt contra la identidad canonica del ledger."""

    expected_effect = MATERIAL_ACTION_EFFECTS.get(str(action_name or "").strip())
    if expected_effect is None:
        return MaterialAttestationValidation(MaterialAttestationStatus.NOT_REQUIRED)
    if type(success) is not bool:
        return MaterialAttestationValidation(MaterialAttestationStatus.MALFORMED)

    raw = payload.get("material_attestation")
    if raw is None:
        return MaterialAttestationValidation(MaterialAttestationStatus.MISSING)
    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        return MaterialAttestationValidation(MaterialAttestationStatus.MALFORMED)

    receipt_action_id = raw.get("action_id")
    effect_kind = raw.get("effect_kind")
    status_raw = raw.get("status")
    object_id = raw.get("object_id")
    evidence = raw.get("evidence")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (receipt_action_id, effect_kind, status_raw, object_id)
    ):
        return MaterialAttestationValidation(MaterialAttestationStatus.MALFORMED)
    if receipt_action_id.strip() != action_id:
        return MaterialAttestationValidation(MaterialAttestationStatus.ACTION_ID_MISMATCH)
    try:
        parsed_effect = MaterialEffectKind(effect_kind.strip())
    except ValueError:
        return MaterialAttestationValidation(MaterialAttestationStatus.MALFORMED)
    if parsed_effect != expected_effect:
        return MaterialAttestationValidation(MaterialAttestationStatus.EFFECT_MISMATCH)
    try:
        material_status = MaterialEffectStatus(status_raw.strip())
    except ValueError:
        return MaterialAttestationValidation(MaterialAttestationStatus.MALFORMED)
    if (success is True) != (material_status == MaterialEffectStatus.APPLIED):
        return MaterialAttestationValidation(MaterialAttestationStatus.STATUS_MISMATCH)
    if not _safe_evidence(evidence, parsed_effect.value):
        return MaterialAttestationValidation(MaterialAttestationStatus.MALFORMED)

    receipt = MaterialEffectReceipt(
        action_id=receipt_action_id.strip(),
        effect_kind=parsed_effect,
        status=material_status,
        object_id=object_id.strip(),
        evidence=dict(evidence),
    )
    if not _has_required_evidence(receipt):
        return MaterialAttestationValidation(
            MaterialAttestationStatus.INSUFFICIENT_EVIDENCE,
            receipt,
        )
    return MaterialAttestationValidation(MaterialAttestationStatus.VALID, receipt)


def canonical_material_attestation(value: Any) -> dict[str, Any] | None:
    """Reduce un receipt ya validado a su payload operacional pequeno."""

    if not isinstance(value, dict) or set(value) != _TOP_LEVEL_KEYS:
        return None
    evidence = value.get("evidence")
    effect_kind = str(value.get("effect_kind") or "").strip()
    if not _safe_evidence(evidence, effect_kind):
        return None
    return {
        "action_id": str(value.get("action_id") or "").strip(),
        "effect_kind": effect_kind,
        "status": str(value.get("status") or "").strip(),
        "object_id": str(value.get("object_id") or "").strip(),
        "evidence": dict(evidence),
    }


def _safe_evidence(value: Any, effect_kind: str) -> bool:
    if not isinstance(value, dict) or len(value) > _MAX_EVIDENCE_FIELDS:
        return False
    allowed_keys = _EVIDENCE_KEYS_BY_EFFECT.get(effect_kind)
    if allowed_keys is None or not set(value).issubset(allowed_keys):
        return False
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            return False
        if type(item) is bool or item is None:
            continue
        if isinstance(item, str):
            if len(item) > _MAX_TEXT_LENGTH:
                return False
            continue
        if _finite_number(item):
            continue
        if isinstance(item, list) and len(item) <= 3 and all(_finite_number(v) for v in item):
            continue
        return False
    return True


def _has_required_evidence(receipt: MaterialEffectReceipt) -> bool:
    evidence = receipt.evidence
    if receipt.status != MaterialEffectStatus.APPLIED:
        return isinstance(evidence.get("reason_code"), str) and bool(
            evidence["reason_code"].strip()
        )
    if receipt.effect_kind == MaterialEffectKind.INTERACT:
        return (
            evidence.get("effect_observed") is True
            and isinstance(evidence.get("interaction_type"), str)
            and bool(evidence["interaction_type"].strip())
            and isinstance(evidence.get("observer"), str)
            and bool(evidence["observer"].strip())
        )
    if receipt.effect_kind == MaterialEffectKind.GRAB:
        return (
            evidence.get("held_reference_changed") is True
            and evidence.get("parent_is_hold_point") is True
            and evidence.get("held_after") == receipt.object_id
        )
    if receipt.effect_kind == MaterialEffectKind.RELEASE:
        return (
            evidence.get("held_reference_changed") is True
            and evidence.get("parent_is_hold_point") is False
            and evidence.get("held_before") == receipt.object_id
            and evidence.get("held_after") is None
        )
    if receipt.effect_kind == MaterialEffectKind.FORCE:
        return (
            evidence.get("fixed_update_observed") is True
            and _vector3(evidence.get("velocity_before"))
            and _vector3(evidence.get("velocity_after"))
            and _positive_number(evidence.get("directional_delta"))
        )
    return False


def _finite_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _positive_number(value: Any) -> bool:
    return _finite_number(value) and float(value) > 0.0


def _vector3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(
        _finite_number(component) for component in value
    )
