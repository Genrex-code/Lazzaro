"""Frontera tipada entre la clasificacion semantica y Core.

ROOT-026 mantiene separados el resultado semantico de Triage y los fallos que
impiden obtener o validar ese resultado. La policy operacional permanece en
``Orchestrator``; este modulo solo define y valida datos de frontera.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from ai_engine.triage_contracts import TriageDecisionV1


class TriageBoundaryFailureCodeV1(str, Enum):
    TRIAGE_EVALUATION_ERROR = "TRIAGE_EVALUATION_ERROR"
    TRIAGE_CONTRACT_ERROR = "TRIAGE_CONTRACT_ERROR"


_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_LEGACY_STRUCTURAL_FIELDS = (
    "intent",
    "route",
    "requires_memory",
    "requires_oracle",
)


class TriageBoundaryFailureV1(BaseModel):
    """Fallo de evaluacion/contrato sin afirmar una decision semantica."""

    contract_version: StrictInt = 1
    code: TriageBoundaryFailureCodeV1
    error_type: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$",
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version")
    @classmethod
    def validate_exact_version(cls, value: int) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the integer 1")
        return value


TriageEvaluationResultV1: TypeAlias = TriageDecisionV1 | TriageBoundaryFailureV1


def safe_triage_error_type(error: BaseException) -> str:
    """Extrae solo un identificador de clase acotado y seguro."""

    candidate = type(error).__name__
    if len(candidate) <= 96 and _SAFE_ERROR_TYPE.fullmatch(candidate):
        return candidate
    return "UnknownError"


def triage_evaluation_failure(error: BaseException) -> TriageBoundaryFailureV1:
    return TriageBoundaryFailureV1(
        code=TriageBoundaryFailureCodeV1.TRIAGE_EVALUATION_ERROR,
        error_type=safe_triage_error_type(error),
    )


def triage_contract_failure(error_type: str) -> TriageBoundaryFailureV1:
    """Construye un fallo con uno de los identificadores locales controlados."""

    candidate = str(error_type or "")
    if len(candidate) > 96 or not _SAFE_ERROR_TYPE.fullmatch(candidate):
        candidate = "InvalidTriageResult"
    return TriageBoundaryFailureV1(
        code=TriageBoundaryFailureCodeV1.TRIAGE_CONTRACT_ERROR,
        error_type=candidate,
    )


def validate_triage_legacy_result(value: object) -> TriageEvaluationResultV1:
    """Valida classification y su proyeccion operacional sin reclasificar."""

    if not isinstance(value, dict):
        return triage_contract_failure("InvalidTriageResult")
    if "classification" not in value:
        return triage_contract_failure("MissingClassification")

    try:
        decision = TriageDecisionV1.model_validate(value["classification"])
    except Exception:
        return triage_contract_failure("InvalidClassification")

    # Confidence es diagnostico legacy, no autoridad. Un valor fijo permite
    # reutilizar la unica proyeccion canonica de ROOT-025 y comparar solo su
    # estructura operacional.
    try:
        canonical = decision.to_legacy_dict(confidence=0.0)
    except Exception:
        return triage_contract_failure("InvalidClassification")
    for field_name in _LEGACY_STRUCTURAL_FIELDS:
        expected = canonical[field_name]
        actual = value.get(field_name)
        if type(actual) is not type(expected) or actual != expected:
            return triage_contract_failure("ContradictoryLegacyProjection")
    return decision
