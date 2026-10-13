"""Contratos internos ROOT-054 para identidad y reconciliacion de delivery.

Este modulo no decide acciones, lifecycle ni exito material. Unicamente expresa
la identidad semantica del request y proyecta evidencia durable ya existente.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any


REQUEST_IDENTITY_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DeliveryResolutionV1(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"
    UNVERIFIABLE = "UNVERIFIABLE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED_NO_ACTION = "COMPLETED_NO_ACTION"
    COMPLETED_WITH_ACTION = "COMPLETED_WITH_ACTION"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"


class DeliveryDirectiveV1(str, Enum):
    RETRY_SAME_TX = "RETRY_SAME_TX"
    WAIT = "WAIT"
    RESOLVED = "RESOLVED"
    TERMINAL = "TERMINAL"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"


class DurableReceiptKindV1(str, Enum):
    NONE = "NONE"
    TRANSACTION = "TRANSACTION"
    ACTION_LIFECYCLE = "ACTION_LIFECYCLE"
    OUTCOME = "OUTCOME"


class DurableReceiptStatusV1(str, Enum):
    NONE = "NONE"
    COMMITTED = "COMMITTED"
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    MISSING = "MISSING"


class RequestIdentityRelationshipV1(str, Enum):
    LEGACY_COMPATIBLE = "LEGACY_COMPATIBLE"
    VERIFIED_MATCH = "VERIFIED_MATCH"
    VERIFIED_CONFLICT = "VERIFIED_CONFLICT"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class DeliveryReconciliationResultV1:
    transaction_id: str
    resolution: DeliveryResolutionV1
    directive: DeliveryDirectiveV1
    receipt_kind: DurableReceiptKindV1 = DurableReceiptKindV1.NONE
    receipt_status: DurableReceiptStatusV1 = DurableReceiptStatusV1.NONE
    response_action: dict[str, Any] | None = None
    action_id: str | None = None

    def __post_init__(self) -> None:
        has_response_action = isinstance(self.response_action, dict)
        has_action_id = isinstance(self.action_id, str) and bool(self.action_id.strip())
        validate_delivery_reconciliation(
            resolution=self.resolution,
            directive=self.directive,
            receipt_kind=self.receipt_kind,
            receipt_status=self.receipt_status,
            has_action=has_response_action or has_action_id,
        )


_EXPECTED_DIRECTIVE = {
    DeliveryResolutionV1.NOT_FOUND: DeliveryDirectiveV1.RETRY_SAME_TX,
    DeliveryResolutionV1.UNKNOWN: DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
    DeliveryResolutionV1.UNVERIFIABLE: DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
    DeliveryResolutionV1.IN_PROGRESS: DeliveryDirectiveV1.WAIT,
    DeliveryResolutionV1.COMPLETED_NO_ACTION: DeliveryDirectiveV1.RESOLVED,
    DeliveryResolutionV1.COMPLETED_WITH_ACTION: DeliveryDirectiveV1.RESOLVED,
    DeliveryResolutionV1.FAILED_TERMINAL: DeliveryDirectiveV1.TERMINAL,
    DeliveryResolutionV1.REQUIRES_RECONCILIATION: (
        DeliveryDirectiveV1.REQUIRES_RECONCILIATION
    ),
}

_ALLOWED_RECEIPTS = {
    DeliveryResolutionV1.NOT_FOUND: {
        (DurableReceiptKindV1.NONE, DurableReceiptStatusV1.NONE),
    },
    DeliveryResolutionV1.UNKNOWN: {
        (DurableReceiptKindV1.NONE, DurableReceiptStatusV1.NONE),
    },
    DeliveryResolutionV1.UNVERIFIABLE: {
        (DurableReceiptKindV1.NONE, DurableReceiptStatusV1.NONE),
    },
    DeliveryResolutionV1.IN_PROGRESS: {
        (DurableReceiptKindV1.NONE, DurableReceiptStatusV1.NONE),
    },
    DeliveryResolutionV1.COMPLETED_NO_ACTION: {
        (DurableReceiptKindV1.TRANSACTION, DurableReceiptStatusV1.COMMITTED),
        (DurableReceiptKindV1.ACTION_LIFECYCLE, DurableReceiptStatusV1.APPLIED),
        (
            DurableReceiptKindV1.ACTION_LIFECYCLE,
            DurableReceiptStatusV1.ALREADY_APPLIED,
        ),
        (DurableReceiptKindV1.OUTCOME, DurableReceiptStatusV1.ACCEPTED),
        (DurableReceiptKindV1.OUTCOME, DurableReceiptStatusV1.DUPLICATE),
    },
    DeliveryResolutionV1.COMPLETED_WITH_ACTION: {
        (DurableReceiptKindV1.TRANSACTION, DurableReceiptStatusV1.COMMITTED),
        (DurableReceiptKindV1.ACTION_LIFECYCLE, DurableReceiptStatusV1.APPLIED),
        (
            DurableReceiptKindV1.ACTION_LIFECYCLE,
            DurableReceiptStatusV1.ALREADY_APPLIED,
        ),
    },
    DeliveryResolutionV1.FAILED_TERMINAL: {
        (DurableReceiptKindV1.NONE, DurableReceiptStatusV1.NONE),
        (DurableReceiptKindV1.ACTION_LIFECYCLE, DurableReceiptStatusV1.REJECTED),
        (DurableReceiptKindV1.OUTCOME, DurableReceiptStatusV1.REJECTED),
    },
    DeliveryResolutionV1.REQUIRES_RECONCILIATION: {
        (DurableReceiptKindV1.NONE, DurableReceiptStatusV1.NONE),
        (DurableReceiptKindV1.ACTION_LIFECYCLE, DurableReceiptStatusV1.MISSING),
        (DurableReceiptKindV1.OUTCOME, DurableReceiptStatusV1.MISSING),
    },
}


def validate_delivery_reconciliation(
    *,
    resolution: DeliveryResolutionV1,
    directive: DeliveryDirectiveV1,
    receipt_kind: DurableReceiptKindV1,
    receipt_status: DurableReceiptStatusV1,
    has_action: bool,
) -> None:
    """Valida la matriz causal completa antes de proyectar o consumir un ACK."""

    if directive is not _EXPECTED_DIRECTIVE[resolution]:
        raise ValueError("resolution/directive de delivery incompatibles")
    if (resolution is DeliveryResolutionV1.COMPLETED_WITH_ACTION) != has_action:
        raise ValueError("accion incompatible con resolution de delivery")
    if (receipt_kind, receipt_status) not in _ALLOWED_RECEIPTS[resolution]:
        raise ValueError("receipt incompatible con resolution de delivery")


def validate_request_identity(
    version: object | None,
    fingerprint: object | None,
) -> tuple[int | None, str | None]:
    """Valida el par version/fingerprint sin fabricar identidad legacy."""

    if version is None and fingerprint is None:
        return None, None
    if version is None or fingerprint is None:
        raise ValueError("request identity version y fingerprint deben viajar juntos")
    if type(version) is not int or version != REQUEST_IDENTITY_VERSION:
        raise ValueError("request_identity_version debe ser el entero exacto 1")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise ValueError("request_fingerprint_sha256 debe ser SHA-256 lowercase")
    return version, fingerprint


def classify_request_identity_relationship(
    *,
    stored_version: object | None,
    stored_fingerprint: object | None,
    incoming_version: object | None,
    incoming_fingerprint: object | None,
) -> RequestIdentityRelationshipV1:
    """Compara identidad durable y entrante sin fabricar equivalencia legacy/V1."""

    try:
        stored = validate_request_identity(stored_version, stored_fingerprint)
    except (TypeError, ValueError):
        return RequestIdentityRelationshipV1.UNVERIFIABLE
    incoming = validate_request_identity(incoming_version, incoming_fingerprint)
    stored_is_legacy = stored[0] is None
    incoming_is_legacy = incoming[0] is None
    if stored_is_legacy and incoming_is_legacy:
        return RequestIdentityRelationshipV1.LEGACY_COMPATIBLE
    if stored_is_legacy != incoming_is_legacy:
        return RequestIdentityRelationshipV1.UNVERIFIABLE
    if stored == incoming:
        return RequestIdentityRelationshipV1.VERIFIED_MATCH
    return RequestIdentityRelationshipV1.VERIFIED_CONFLICT


def request_identity_payload(
    *,
    transaction_id: str,
    origin: str,
    event_type: str,
    involved_object: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    """Proyeccion semantica V1; timestamp y metadata de delivery no son identidad."""

    return {
        "detalles": details,
        "id_transaccion": transaction_id,
        "objeto_involucrado": involved_object,
        "origen": origin,
        "tipo_evento": event_type,
    }


def request_fingerprint_sha256_v1(
    *,
    transaction_id: str,
    origin: str,
    event_type: str,
    involved_object: str,
    details: dict[str, Any],
) -> str:
    canonical = _canonical_json(
        request_identity_payload(
            transaction_id=transaction_id,
            origin=origin,
            event_type=event_type,
            involved_object=involved_object,
            details=details,
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    _validate_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("request identity no admite floats no finitos")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("request identity requiere claves string")
            _validate_json(item)
        return
    raise TypeError(f"tipo no JSON en request identity: {type(value).__name__}")
