"""Canonical ROOT-029 authority composition and immutable action binding."""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
import json
import math
from typing import Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ai_engine.affordance_resolver import SafetyDisposition, SafetyReasonCode
from ai_engine.perception_contracts import (
    IdentityStatusV1,
    PerceptionIdentityReceiptV1,
    PerceptionParseReceiptV1,
    PerceptionParseStatusV1,
)
from core.perception_freshness import (
    FreshnessStatusV1,
    PerceptionFreshnessReceiptV1,
    SupersessionStatusV1,
    TemporalEvidenceKindV1,
)
from core.perception_lifecycle import LifecycleStatusV1, PerceptionLifecycleReceiptV1


PERCEPTION_TARGET_ACTIONS = frozenset(
    {
        "navegar_a_objeto",
        "alejarse_de_objeto",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "aplicar_fuerza",
    }
)


class ParameterProvenanceKindV1(str, Enum):
    PERCEPTION_TARGET = "PERCEPTION_TARGET"
    EXPLICIT_COORDINATES = "EXPLICIT_COORDINATES"
    BODY_RELATIVE = "BODY_RELATIVE"
    HELD_OBJECT = "HELD_OBJECT"
    CONTROL = "CONTROL"
    BODY_EVENT = "BODY_EVENT"
    UNKNOWN = "UNKNOWN"


class AuthorityApplicabilityV1(str, Enum):
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


class PhysicalActionClassV1(str, Enum):
    TARGET_NAVIGATION = "TARGET_NAVIGATION"
    TARGET_RETREAT = "TARGET_RETREAT"
    TARGET_OBSERVATION = "TARGET_OBSERVATION"
    TARGET_CONTACT = "TARGET_CONTACT"
    TARGET_MANIPULATION = "TARGET_MANIPULATION"
    TARGET_FORCE = "TARGET_FORCE"
    NON_PERCEPTION = "NON_PERCEPTION"
    UNKNOWN = "UNKNOWN"


class GenerationBindingModeV1(str, Enum):
    REQUIRE_AUTHORIZED_GENERATION = "REQUIRE_AUTHORIZED_GENERATION"
    REJECT_IF_KNOWN_SUPERSEDED = "REJECT_IF_KNOWN_SUPERSEDED"
    ALLOW_ADVANCE_WITH_EXACT_TARGET = "ALLOW_ADVANCE_WITH_EXACT_TARGET"


class AuthorityStatusV1(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    INDETERMINATE = "INDETERMINATE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuthorityGateStatusV1(str, Enum):
    PASS = "PASS"
    DENY = "DENY"
    INDETERMINATE = "INDETERMINATE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuthorityGateNameV1(str, Enum):
    APPLICABILITY = "APPLICABILITY"
    CANONICAL_DATA = "CANONICAL_DATA"
    IDENTITY = "IDENTITY"
    LIFECYCLE = "LIFECYCLE"
    FRESHNESS = "FRESHNESS"
    CORRELATION = "CORRELATION"
    GROUNDING = "GROUNDING"
    AFFORDANCE = "AFFORDANCE"
    SAFETY = "SAFETY"
    INTERACTION = "INTERACTION"
    SCHEMA = "SCHEMA"
    TARGET_BINDING = "TARGET_BINDING"


class AuthorityReasonCodeV1(str, Enum):
    ALL_GATES_PASSED = "ALL_GATES_PASSED"
    NOT_PERCEPTION_GOVERNED = "NOT_PERCEPTION_GOVERNED"
    PROVENANCE_UNKNOWN = "PROVENANCE_UNKNOWN"
    CANONICAL_DATA_INVALID = "CANONICAL_DATA_INVALID"
    LEGACY_PERCEPTION_DENIED = "LEGACY_PERCEPTION_DENIED"
    IDENTITY_ABSENT = "IDENTITY_ABSENT"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    IDENTITY_CONTRADICTORY = "IDENTITY_CONTRADICTORY"
    IDENTITY_PARTIAL = "IDENTITY_PARTIAL"
    DUPLICATE_RECORD_STATE_CONFLICT = "DUPLICATE_RECORD_STATE_CONFLICT"
    LIFECYCLE_ABSENT = "LIFECYCLE_ABSENT"
    LIFECYCLE_CONTRADICTORY = "LIFECYCLE_CONTRADICTORY"
    LIFECYCLE_HISTORY_UNKNOWN = "LIFECYCLE_HISTORY_UNKNOWN"
    FRESHNESS_POLICY_MISSING = "FRESHNESS_POLICY_MISSING"
    FRESHNESS_NOT_SATISFIED = "FRESHNESS_NOT_SATISFIED"
    FRESHNESS_INDETERMINATE = "FRESHNESS_INDETERMINATE"
    RECEIVE_TIME_PROXY_DENIED = "RECEIVE_TIME_PROXY_DENIED"
    RECEIPT_CORRELATION_MISMATCH = "RECEIPT_CORRELATION_MISMATCH"
    GROUNDING_NOT_READY = "GROUNDING_NOT_READY"
    AFFORDANCE_DENIED = "AFFORDANCE_DENIED"
    AFFORDANCE_INDETERMINATE = "AFFORDANCE_INDETERMINATE"
    SAFETY_DENIED = "SAFETY_DENIED"
    SAFETY_INDETERMINATE = "SAFETY_INDETERMINATE"
    INTERACTION_TYPE_MISSING = "INTERACTION_TYPE_MISSING"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    RUNTIME_TARGET_REQUIRED = "RUNTIME_TARGET_REQUIRED"
    PERSISTENT_TARGET_WORLD_REQUIRED = "PERSISTENT_TARGET_WORLD_REQUIRED"
    BINDING_MISMATCH = "BINDING_MISMATCH"


class ParameterProvenanceV1(BaseModel):
    contract_version: StrictInt = 1
    kind: ParameterProvenanceKindV1
    producer: StrictStr
    code_owned: StrictBool = True

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("producer")
    @classmethod
    def _producer(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("producer cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def _trusted(self) -> "ParameterProvenanceV1":
        if not self.code_owned:
            raise ValueError("parameter provenance must be code-owned")
        return self


class TargetReferenceV1(BaseModel):
    contract_version: StrictInt = 1
    producer_instance_id: StrictStr
    stream_id: StrictStr
    producer_epoch_id: StrictStr
    logical_world_id: StrictStr | None = None
    producer_capture_id: StrictStr
    generation: StrictInt
    producer_object_id: StrictStr | None = None
    runtime_object_id: StrictStr

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("generation", mode="before")
    @classmethod
    def _generation(cls, value: Any) -> int:
        if type(value) is not int or value < 0 or value > (2**63) - 1:
            raise ValueError("generation must be a bounded strict integer")
        return value

    @field_validator(
        "producer_instance_id",
        "stream_id",
        "producer_epoch_id",
        "producer_capture_id",
        "runtime_object_id",
    )
    @classmethod
    def _required(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} cannot be empty")
        return cleaned

    @field_validator("logical_world_id", "producer_object_id")
    @classmethod
    def _optional(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def _persistent_scope(self) -> "TargetReferenceV1":
        if self.producer_object_id is not None and self.logical_world_id is None:
            raise ValueError("producer_object_id requires logical_world_id")
        return self


class PerceptionAuthorityPolicyV1(BaseModel):
    contract_version: StrictInt = 1
    policy_id: StrictStr
    freshness_policy_id: StrictStr
    generation_binding_mode: GenerationBindingModeV1

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("policy_id", "freshness_policy_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} cannot be empty")
        return cleaned


class PhysicalActionCandidateV1(BaseModel):
    contract_version: StrictInt = 1
    id_accion: StrictStr
    action_name: StrictStr
    canonical_parameters_json: StrictStr
    parameter_provenance: ParameterProvenanceV1
    action_class: PhysicalActionClassV1
    target_reference: TargetReferenceV1 | None
    interaction_type: StrictStr | None
    grounding_status: StrictStr | None
    affordance_allowed: StrictBool | None
    safety_disposition: SafetyDisposition | None
    safety_reason_code: SafetyReasonCode | None
    schema_valid: StrictBool

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("id_accion", "action_name", "canonical_parameters_json")
    @classmethod
    def _required(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def _canonical_json(self) -> "PhysicalActionCandidateV1":
        parsed = json.loads(self.canonical_parameters_json)
        if not isinstance(parsed, dict):
            raise ValueError("canonical_parameters_json must encode an object")
        if canonical_json(parsed) != self.canonical_parameters_json:
            raise ValueError("canonical_parameters_json is not canonical")
        return self

    def parameters_copy(self) -> dict[str, Any]:
        return json.loads(self.canonical_parameters_json)


class AuthorityGateReceiptV1(BaseModel):
    name: AuthorityGateNameV1
    status: AuthorityGateStatusV1
    reason_code: AuthorityReasonCodeV1

    model_config = ConfigDict(extra="forbid", frozen=True)


class PerceptionAuthorityReceiptV1(BaseModel):
    contract_version: StrictInt = 1
    authority_decision_id: StrictStr
    id_accion: StrictStr
    status: AuthorityStatusV1
    reason_code: AuthorityReasonCodeV1
    applicability: AuthorityApplicabilityV1
    policy_id: StrictStr | None
    freshness_policy_id: StrictStr | None
    generation_binding_mode: GenerationBindingModeV1 | None
    local_ingest_id: StrictStr | None
    target_reference: TargetReferenceV1 | None
    gates: tuple[AuthorityGateReceiptV1, ...]
    action_binding_sha256: StrictStr | None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @model_validator(mode="after")
    def _authorized_fields(self) -> "PerceptionAuthorityReceiptV1":
        if self.status is AuthorityStatusV1.AUTHORIZED:
            if self.target_reference is None or self.action_binding_sha256 is None:
                raise ValueError("authorized receipt requires target and binding")
        elif self.action_binding_sha256 is not None:
            raise ValueError("non-authorized receipt cannot carry binding")
        return self


class PerceptionAuthorityReferenceV1(BaseModel):
    version: StrictInt = 1
    authority_decision_id: StrictStr
    id_accion: StrictStr
    authority_policy_id: StrictStr
    freshness_policy_id: StrictStr
    generation_binding_mode: GenerationBindingModeV1
    producer_capture_id: StrictStr
    generation: StrictInt
    target: TargetReferenceV1
    action_name: StrictStr
    canonical_parameters_json: StrictStr
    interaction_type: StrictStr | None
    safety_disposition: SafetyDisposition
    safety_reason_code: SafetyReasonCode
    action_binding_sha256: StrictStr

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the exact integer 1")
        return value

    @model_validator(mode="after")
    def _coherent_binding(self) -> "PerceptionAuthorityReferenceV1":
        if self.id_accion.strip() == "" or self.action_name.strip() == "":
            raise ValueError("authority action identity cannot be empty")
        if self.producer_capture_id != self.target.producer_capture_id:
            raise ValueError("authority capture must match target capture")
        if self.generation != self.target.generation:
            raise ValueError("authority generation must match target generation")
        if len(self.action_binding_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.action_binding_sha256
        ):
            raise ValueError("action binding must be lowercase SHA-256")
        parsed = json.loads(self.canonical_parameters_json)
        if not isinstance(parsed, dict) or canonical_json(parsed) != self.canonical_parameters_json:
            raise ValueError("authority parameters must be canonical JSON")
        return self


class AuthorizedPhysicalActionV1(BaseModel):
    contract_version: StrictInt = 1
    id_accion: StrictStr
    action_name: StrictStr
    canonical_parameters_json: StrictStr
    target_reference: TargetReferenceV1
    interaction_type: StrictStr | None
    safety_disposition: SafetyDisposition
    safety_reason_code: SafetyReasonCode
    authority_reference: PerceptionAuthorityReferenceV1

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @model_validator(mode="after")
    def _same_authorized_semantics(self) -> "AuthorizedPhysicalActionV1":
        reference = self.authority_reference
        if (
            self.id_accion != reference.id_accion
            or self.action_name != reference.action_name
            or self.canonical_parameters_json != reference.canonical_parameters_json
            or self.target_reference != reference.target
            or self.interaction_type != reference.interaction_type
            or self.safety_disposition is not reference.safety_disposition
            or self.safety_reason_code is not reference.safety_reason_code
        ):
            raise ValueError("authorized action differs from its authority reference")
        if not self.binding_is_intact():
            raise ValueError("authorized action binding is not intact")
        return self

    def parameters_copy(self) -> dict[str, Any]:
        return json.loads(self.canonical_parameters_json)

    def binding_is_intact(self) -> bool:
        expected = action_binding_sha256(
            id_accion=self.id_accion,
            action_name=self.action_name,
            canonical_parameters_json=self.canonical_parameters_json,
            target=self.target_reference,
            authority_policy_id=self.authority_reference.authority_policy_id,
            freshness_policy_id=self.authority_reference.freshness_policy_id,
            generation_binding_mode=self.authority_reference.generation_binding_mode,
            interaction_type=self.interaction_type,
            safety_disposition=self.safety_disposition,
            safety_reason_code=self.safety_reason_code,
        )
        return expected == self.authority_reference.action_binding_sha256


def canonical_json(value: Any) -> str:
    _validate_json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("binding values must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("binding keys must be strings")
            _validate_json_value(item)
        return
    raise TypeError(f"unsupported binding value: {type(value).__name__}")


def action_binding_sha256(
    *,
    id_accion: str,
    action_name: str,
    canonical_parameters_json: str,
    target: TargetReferenceV1,
    authority_policy_id: str,
    freshness_policy_id: str,
    generation_binding_mode: GenerationBindingModeV1,
    interaction_type: str | None,
    safety_disposition: SafetyDisposition,
    safety_reason_code: SafetyReasonCode,
) -> str:
    payload = {
        "binding_version": 1,
        "id_accion": id_accion,
        "action_name": action_name,
        "parameters": json.loads(canonical_parameters_json),
        "target": target.model_dump(mode="json"),
        "authority_policy_id": authority_policy_id,
        "freshness_policy_id": freshness_policy_id,
        "generation_binding_mode": generation_binding_mode.value,
        "interaction_type": interaction_type,
        "safety_disposition": safety_disposition.value,
        "safety_reason_code": safety_reason_code.value,
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def applicability_for(provenance: ParameterProvenanceV1) -> AuthorityApplicabilityV1:
    if provenance.kind is ParameterProvenanceKindV1.PERCEPTION_TARGET:
        return AuthorityApplicabilityV1.REQUIRED
    if provenance.kind is ParameterProvenanceKindV1.UNKNOWN:
        return AuthorityApplicabilityV1.UNKNOWN
    return AuthorityApplicabilityV1.NOT_REQUIRED


class PerceptionAuthorityEvaluator:
    """Only semantic composer of the ROOT-029 gate bundle."""

    def evaluate(
        self,
        candidate: PhysicalActionCandidateV1,
        *,
        parse_receipt: PerceptionParseReceiptV1 | None,
        identity_receipt: PerceptionIdentityReceiptV1 | None,
        lifecycle_receipt: PerceptionLifecycleReceiptV1 | None,
        freshness_receipt: PerceptionFreshnessReceiptV1 | None,
        policy: PerceptionAuthorityPolicyV1 | None,
        authority_decision_id: str | None = None,
    ) -> tuple[PerceptionAuthorityReceiptV1, AuthorizedPhysicalActionV1 | None]:
        decision_id = str(authority_decision_id or uuid4())
        applicability = applicability_for(candidate.parameter_provenance)
        if applicability is AuthorityApplicabilityV1.NOT_REQUIRED:
            gate = self._gate(
                AuthorityGateNameV1.APPLICABILITY,
                AuthorityGateStatusV1.NOT_APPLICABLE,
                AuthorityReasonCodeV1.NOT_PERCEPTION_GOVERNED,
            )
            return (
                self._receipt(
                    decision_id,
                    candidate,
                    AuthorityStatusV1.NOT_APPLICABLE,
                    AuthorityReasonCodeV1.NOT_PERCEPTION_GOVERNED,
                    applicability,
                    policy,
                    (gate,),
                ),
                None,
            )
        if applicability is AuthorityApplicabilityV1.UNKNOWN:
            gate = self._gate(
                AuthorityGateNameV1.APPLICABILITY,
                AuthorityGateStatusV1.INDETERMINATE,
                AuthorityReasonCodeV1.PROVENANCE_UNKNOWN,
            )
            return (
                self._receipt(
                    decision_id,
                    candidate,
                    AuthorityStatusV1.INDETERMINATE,
                    AuthorityReasonCodeV1.PROVENANCE_UNKNOWN,
                    applicability,
                    policy,
                    (gate,),
                ),
                None,
            )

        gates = self._required_gates(
            candidate,
            parse_receipt=parse_receipt,
            identity=identity_receipt,
            lifecycle=lifecycle_receipt,
            freshness=freshness_receipt,
            policy=policy,
        )
        denials = [gate for gate in gates if gate.status is AuthorityGateStatusV1.DENY]
        indeterminate = [
            gate for gate in gates if gate.status is AuthorityGateStatusV1.INDETERMINATE
        ]
        if denials:
            status = AuthorityStatusV1.DENIED
            reason = denials[0].reason_code
        elif indeterminate:
            status = AuthorityStatusV1.INDETERMINATE
            reason = indeterminate[0].reason_code
        else:
            status = AuthorityStatusV1.AUTHORIZED
            reason = AuthorityReasonCodeV1.ALL_GATES_PASSED

        binding = None
        authorized = None
        if status is AuthorityStatusV1.AUTHORIZED:
            assert (
                policy is not None
                and candidate.target_reference is not None
                and candidate.safety_disposition is not None
                and candidate.safety_reason_code is not None
            )
            binding = action_binding_sha256(
                id_accion=candidate.id_accion,
                action_name=candidate.action_name,
                canonical_parameters_json=candidate.canonical_parameters_json,
                target=candidate.target_reference,
                authority_policy_id=policy.policy_id,
                freshness_policy_id=policy.freshness_policy_id,
                generation_binding_mode=policy.generation_binding_mode,
                interaction_type=candidate.interaction_type,
                safety_disposition=candidate.safety_disposition,
                safety_reason_code=candidate.safety_reason_code,
            )

        receipt = self._receipt(
            decision_id,
            candidate,
            status,
            reason,
            applicability,
            policy,
            tuple(gates),
            parse_receipt=parse_receipt,
            binding=binding,
        )
        if status is AuthorityStatusV1.AUTHORIZED:
            assert receipt.target_reference is not None
            reference = PerceptionAuthorityReferenceV1(
                authority_decision_id=receipt.authority_decision_id,
                id_accion=receipt.id_accion,
                authority_policy_id=receipt.policy_id,
                freshness_policy_id=receipt.freshness_policy_id,
                generation_binding_mode=receipt.generation_binding_mode,
                producer_capture_id=receipt.target_reference.producer_capture_id,
                generation=receipt.target_reference.generation,
                target=receipt.target_reference,
                action_name=candidate.action_name,
                canonical_parameters_json=candidate.canonical_parameters_json,
                interaction_type=candidate.interaction_type,
                safety_disposition=candidate.safety_disposition,
                safety_reason_code=candidate.safety_reason_code,
                action_binding_sha256=receipt.action_binding_sha256,
            )
            authorized = AuthorizedPhysicalActionV1(
                id_accion=candidate.id_accion,
                action_name=candidate.action_name,
                canonical_parameters_json=candidate.canonical_parameters_json,
                target_reference=receipt.target_reference,
                interaction_type=candidate.interaction_type,
                safety_disposition=candidate.safety_disposition,
                safety_reason_code=candidate.safety_reason_code,
                authority_reference=reference,
            )
            if not authorized.binding_is_intact():
                raise ValueError("authorized action binding is not intact")
        return receipt, authorized

    def _required_gates(
        self,
        candidate: PhysicalActionCandidateV1,
        *,
        parse_receipt: PerceptionParseReceiptV1 | None,
        identity: PerceptionIdentityReceiptV1 | None,
        lifecycle: PerceptionLifecycleReceiptV1 | None,
        freshness: PerceptionFreshnessReceiptV1 | None,
        policy: PerceptionAuthorityPolicyV1 | None,
    ) -> list[AuthorityGateReceiptV1]:
        gates = [self._gate(AuthorityGateNameV1.APPLICABILITY, AuthorityGateStatusV1.PASS)]
        if parse_receipt is None or parse_receipt.status is not PerceptionParseStatusV1.CANONICAL:
            reason = (
                AuthorityReasonCodeV1.LEGACY_PERCEPTION_DENIED
                if parse_receipt is not None
                and parse_receipt.status is PerceptionParseStatusV1.LEGACY_DEGRADED
                else AuthorityReasonCodeV1.CANONICAL_DATA_INVALID
            )
            gates.append(self._gate(AuthorityGateNameV1.CANONICAL_DATA, AuthorityGateStatusV1.DENY, reason))
        else:
            gates.append(self._gate(AuthorityGateNameV1.CANONICAL_DATA, AuthorityGateStatusV1.PASS))

        identity_status = identity.status if identity is not None else IdentityStatusV1.ABSENT
        if identity_status is IdentityStatusV1.RESOLVED:
            gates.append(self._gate(AuthorityGateNameV1.IDENTITY, AuthorityGateStatusV1.PASS))
        elif identity_status is IdentityStatusV1.CONTRADICTORY:
            gates.append(self._gate(AuthorityGateNameV1.IDENTITY, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.IDENTITY_CONTRADICTORY))
        elif identity_status is IdentityStatusV1.AMBIGUOUS:
            reason = (
                AuthorityReasonCodeV1.DUPLICATE_RECORD_STATE_CONFLICT
                if identity is not None and not identity.action_relevant_records_agree
                else AuthorityReasonCodeV1.IDENTITY_AMBIGUOUS
            )
            gates.append(self._gate(AuthorityGateNameV1.IDENTITY, AuthorityGateStatusV1.DENY, reason))
        elif identity_status is IdentityStatusV1.PARTIAL:
            gates.append(self._gate(AuthorityGateNameV1.IDENTITY, AuthorityGateStatusV1.INDETERMINATE, AuthorityReasonCodeV1.IDENTITY_PARTIAL))
        else:
            gates.append(self._gate(AuthorityGateNameV1.IDENTITY, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.IDENTITY_ABSENT))

        lifecycle_status = lifecycle.status if lifecycle else LifecycleStatusV1.ABSENT
        if lifecycle_status is LifecycleStatusV1.RESOLVED:
            gates.append(self._gate(AuthorityGateNameV1.LIFECYCLE, AuthorityGateStatusV1.PASS))
        elif lifecycle_status is LifecycleStatusV1.CONTRADICTORY:
            gates.append(self._gate(AuthorityGateNameV1.LIFECYCLE, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.LIFECYCLE_CONTRADICTORY))
        elif lifecycle_status is LifecycleStatusV1.HISTORY_UNKNOWN:
            gates.append(self._gate(AuthorityGateNameV1.LIFECYCLE, AuthorityGateStatusV1.INDETERMINATE, AuthorityReasonCodeV1.LIFECYCLE_HISTORY_UNKNOWN))
        else:
            gates.append(self._gate(AuthorityGateNameV1.LIFECYCLE, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.LIFECYCLE_ABSENT))

        if policy is None or freshness is None or freshness.policy_id is None:
            gates.append(self._gate(AuthorityGateNameV1.FRESHNESS, AuthorityGateStatusV1.INDETERMINATE, AuthorityReasonCodeV1.FRESHNESS_POLICY_MISSING))
        elif freshness.evidence_kind is TemporalEvidenceKindV1.RECEIVE_TIME_PROXY:
            gates.append(self._gate(AuthorityGateNameV1.FRESHNESS, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.RECEIVE_TIME_PROXY_DENIED))
        elif freshness.status is FreshnessStatusV1.SATISFIED:
            gates.append(self._gate(AuthorityGateNameV1.FRESHNESS, AuthorityGateStatusV1.PASS))
        elif freshness.status is FreshnessStatusV1.NOT_SATISFIED:
            gates.append(self._gate(AuthorityGateNameV1.FRESHNESS, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.FRESHNESS_NOT_SATISFIED))
        else:
            gates.append(self._gate(AuthorityGateNameV1.FRESHNESS, AuthorityGateStatusV1.INDETERMINATE, AuthorityReasonCodeV1.FRESHNESS_INDETERMINATE))

        correlated = self._correlated(parse_receipt, identity, lifecycle, freshness)
        if (
            correlated
            and policy is not None
            and freshness is not None
            and freshness.policy_id != policy.freshness_policy_id
        ):
            correlated = False
        gates.append(
            self._gate(
                AuthorityGateNameV1.CORRELATION,
                AuthorityGateStatusV1.PASS if correlated else AuthorityGateStatusV1.DENY,
                None if correlated else AuthorityReasonCodeV1.RECEIPT_CORRELATION_MISMATCH,
            )
        )
        grounding_ready = candidate.grounding_status == "ready"
        gates.append(self._gate(AuthorityGateNameV1.GROUNDING, AuthorityGateStatusV1.PASS if grounding_ready else AuthorityGateStatusV1.DENY, None if grounding_ready else AuthorityReasonCodeV1.GROUNDING_NOT_READY))
        if candidate.affordance_allowed is True:
            gates.append(self._gate(AuthorityGateNameV1.AFFORDANCE, AuthorityGateStatusV1.PASS))
        elif candidate.affordance_allowed is False:
            gates.append(self._gate(AuthorityGateNameV1.AFFORDANCE, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.AFFORDANCE_DENIED))
        else:
            gates.append(self._gate(AuthorityGateNameV1.AFFORDANCE, AuthorityGateStatusV1.INDETERMINATE, AuthorityReasonCodeV1.AFFORDANCE_INDETERMINATE))

        if (
            candidate.safety_disposition in {SafetyDisposition.SAFE, SafetyDisposition.CAUTION}
            and candidate.safety_reason_code is not None
        ):
            gates.append(self._gate(AuthorityGateNameV1.SAFETY, AuthorityGateStatusV1.PASS))
        elif candidate.safety_disposition is SafetyDisposition.UNSAFE:
            gates.append(self._gate(AuthorityGateNameV1.SAFETY, AuthorityGateStatusV1.DENY, AuthorityReasonCodeV1.SAFETY_DENIED))
        else:
            gates.append(self._gate(AuthorityGateNameV1.SAFETY, AuthorityGateStatusV1.INDETERMINATE, AuthorityReasonCodeV1.SAFETY_INDETERMINATE))

        interaction_required = candidate.action_name == "interactuar_objeto_3d"
        interaction_ok = not interaction_required or bool(candidate.interaction_type)
        gates.append(self._gate(AuthorityGateNameV1.INTERACTION, AuthorityGateStatusV1.PASS if interaction_ok else AuthorityGateStatusV1.INDETERMINATE, None if interaction_ok else AuthorityReasonCodeV1.INTERACTION_TYPE_MISSING))
        gates.append(self._gate(AuthorityGateNameV1.SCHEMA, AuthorityGateStatusV1.PASS if candidate.schema_valid else AuthorityGateStatusV1.DENY, None if candidate.schema_valid else AuthorityReasonCodeV1.SCHEMA_INVALID))

        target_ok = candidate.target_reference is not None and bool(candidate.target_reference.runtime_object_id)
        target_reason = AuthorityReasonCodeV1.RUNTIME_TARGET_REQUIRED
        if candidate.target_reference is not None and candidate.target_reference.producer_object_id and candidate.target_reference.logical_world_id is None:
            target_ok = False
            target_reason = AuthorityReasonCodeV1.PERSISTENT_TARGET_WORLD_REQUIRED
        gates.append(self._gate(AuthorityGateNameV1.TARGET_BINDING, AuthorityGateStatusV1.PASS if target_ok else AuthorityGateStatusV1.DENY, None if target_ok else target_reason))
        return gates

    @staticmethod
    def _correlated(
        parse_receipt: PerceptionParseReceiptV1 | None,
        identity: PerceptionIdentityReceiptV1 | None,
        lifecycle: PerceptionLifecycleReceiptV1 | None,
        freshness: PerceptionFreshnessReceiptV1 | None,
    ) -> bool:
        if not all((parse_receipt, identity, lifecycle, freshness)):
            return False
        data = parse_receipt.data
        if data is None:
            return False
        expected = (
            parse_receipt.local_ingest_id,
            data.producer_instance_id,
            data.stream_id,
            data.producer_epoch_id,
            data.producer_capture_id,
            data.generation,
        )
        return all(
            item == expected
            for item in (
                (
                    identity.local_ingest_id,
                    identity.producer_instance_id,
                    identity.stream_id,
                    identity.producer_epoch_id,
                    identity.producer_capture_id,
                    identity.generation,
                ),
                (
                    lifecycle.local_ingest_id,
                    lifecycle.producer_instance_id,
                    lifecycle.stream_id,
                    lifecycle.producer_epoch_id,
                    lifecycle.producer_capture_id,
                    lifecycle.generation,
                ),
                (
                    freshness.local_ingest_id,
                    freshness.producer_instance_id,
                    freshness.stream_id,
                    freshness.producer_epoch_id,
                    freshness.producer_capture_id,
                    freshness.generation,
                ),
            )
        )

    @staticmethod
    def _gate(
        name: AuthorityGateNameV1,
        status: AuthorityGateStatusV1,
        reason: AuthorityReasonCodeV1 | None = None,
    ) -> AuthorityGateReceiptV1:
        return AuthorityGateReceiptV1(
            name=name,
            status=status,
            reason_code=reason or AuthorityReasonCodeV1.ALL_GATES_PASSED,
        )

    @staticmethod
    def _receipt(
        decision_id: str,
        candidate: PhysicalActionCandidateV1,
        status: AuthorityStatusV1,
        reason: AuthorityReasonCodeV1,
        applicability: AuthorityApplicabilityV1,
        policy: PerceptionAuthorityPolicyV1 | None,
        gates: tuple[AuthorityGateReceiptV1, ...],
        *,
        parse_receipt: PerceptionParseReceiptV1 | None = None,
        binding: str | None = None,
    ) -> PerceptionAuthorityReceiptV1:
        return PerceptionAuthorityReceiptV1(
            authority_decision_id=decision_id,
            id_accion=candidate.id_accion,
            status=status,
            reason_code=reason,
            applicability=applicability,
            policy_id=policy.policy_id if policy else None,
            freshness_policy_id=policy.freshness_policy_id if policy else None,
            generation_binding_mode=policy.generation_binding_mode if policy else None,
            local_ingest_id=parse_receipt.local_ingest_id if parse_receipt else None,
            target_reference=candidate.target_reference,
            gates=gates,
            action_binding_sha256=binding,
        )


def derive_parameter_provenance(
    action_name: str,
    *,
    motor_resolution_present: bool,
    body_event: bool = False,
) -> ParameterProvenanceV1:
    """Trusted code-owned classification; never consumes arbitrary metadata labels."""

    if action_name in PERCEPTION_TARGET_ACTIONS and motor_resolution_present:
        kind = ParameterProvenanceKindV1.PERCEPTION_TARGET
        producer = "motor_resolution"
    elif action_name == "alejarse_de_objeto" and body_event:
        kind = ParameterProvenanceKindV1.BODY_EVENT
        producer = "critical_body_event"
    elif action_name == "navegar_a_posicion":
        kind = ParameterProvenanceKindV1.EXPLICIT_COORDINATES
        producer = "validated_action_schema"
    elif action_name == "girar_avatar":
        kind = ParameterProvenanceKindV1.BODY_RELATIVE
        producer = "validated_action_schema"
    elif action_name == "soltar_objeto":
        kind = ParameterProvenanceKindV1.HELD_OBJECT
        producer = "validated_action_schema"
    elif action_name in {"cancelar_tarea", "none", "hablar"}:
        kind = ParameterProvenanceKindV1.CONTROL
        producer = "orchestrator_action_kind"
    else:
        kind = ParameterProvenanceKindV1.UNKNOWN
        producer = "orchestrator_unknown"
    return ParameterProvenanceV1(kind=kind, producer=producer)


def physical_action_class(
    action_name: str,
    interaction_type: str | None,
) -> PhysicalActionClassV1:
    if action_name == "navegar_a_objeto":
        return PhysicalActionClassV1.TARGET_NAVIGATION
    if action_name == "alejarse_de_objeto":
        return PhysicalActionClassV1.TARGET_RETREAT
    if action_name == "agarrar_objeto":
        return PhysicalActionClassV1.TARGET_MANIPULATION
    if action_name == "aplicar_fuerza":
        return PhysicalActionClassV1.TARGET_FORCE
    if action_name == "interactuar_objeto_3d":
        if interaction_type in {"mirar", "inspeccionar"}:
            return PhysicalActionClassV1.TARGET_OBSERVATION
        if interaction_type == "tocar":
            return PhysicalActionClassV1.TARGET_CONTACT
        if interaction_type in {"usar", "activar"}:
            return PhysicalActionClassV1.TARGET_MANIPULATION
        if interaction_type == "golpear":
            return PhysicalActionClassV1.TARGET_FORCE
        return PhysicalActionClassV1.UNKNOWN
    if action_name in PERCEPTION_TARGET_ACTIONS:
        return PhysicalActionClassV1.UNKNOWN
    return PhysicalActionClassV1.NON_PERCEPTION
