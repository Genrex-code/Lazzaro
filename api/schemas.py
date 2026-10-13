"""
api/schemas.py - Modelos de datos robustos y bidireccionales para la API REST.
Soporta eventos desde Roblox, Unity y cualquier otro frontend,
asi como la devolucion de acciones estructuradas que el agente decide ejecutar.
"""

from datetime import datetime
from enum import Enum
import time
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ai_engine.decision_outcome import DecisionDisposition, DecisionOutcome, DecisionReasonCode
from core.runtime_capabilities import (
    CapabilityName,
    CapabilityReasonCode,
    CapabilityStatus,
    RuntimeOverall,
    RuntimeVerification,
)
from core.perception_authority import PerceptionAuthorityReferenceV1
from core.transport_delivery import (
    DeliveryDirectiveV1,
    DeliveryResolutionV1,
    DurableReceiptKindV1,
    DurableReceiptStatusV1,
    request_fingerprint_sha256_v1,
    validate_delivery_reconciliation,
    validate_request_identity,
)


class LivenessPublicaV1(BaseModel):
    version: Literal[1] = 1
    status: Literal["LIVE"] = "LIVE"

    model_config = ConfigDict(extra="forbid")


class CapabilityPublicaV1(BaseModel):
    status: CapabilityStatus
    reason_code: CapabilityReasonCode

    model_config = ConfigDict(extra="forbid")


class ReadinessPublicaV1(BaseModel):
    """Proyección allowlisted; no contiene detalle de providers ni errores."""

    version: Literal[1] = 1
    overall: RuntimeOverall
    verification: RuntimeVerification
    capabilities: Dict[CapabilityName, CapabilityPublicaV1]

    model_config = ConfigDict(extra="forbid")


class EventoEntrante(BaseModel):
    """
    Evento que envia un frontend al agente.
    Campos minimos requeridos y un payload flexible segun el tipo de evento.
    """

    origen: Literal["roblox", "unity", "sandbox", "terminal", "otro"] = Field(
        ...,
        description="Fuente del evento. Permite filtrar o cambiar comportamiento si es necesario.",
    )
    tipo_evento: str = Field(
        ...,
        min_length=1,
        description="Identificador del suceso: 'actualizacion_estado', 'alerta_dolor_fisico', etc.",
    )
    id_transaccion: str = Field(
        ...,
        min_length=1,
        description=(
            "Idempotency key canonica, opaca, estable y no vacia generada por el cliente. "
            "No se reutiliza para otro evento."
        ),
    )
    timestamp: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        description="Momento en que ocurrio el evento en el cliente. Si no se envia, usamos UTC ahora.",
    )
    objeto_involucrado: str = Field(
        "ninguno",
        description="Entidad principal afectada: nombre de herramienta, objeto del mundo, etc.",
    )
    detalles: Dict[str, Any] = Field(
        default_factory=dict,
        description="Payload flexible con informacion especifica del evento.",
    )
    request_identity_version: Optional[StrictInt] = Field(
        None,
        description="Version de identidad semantica de delivery; ausente en clientes legacy.",
    )
    request_fingerprint_sha256: Optional[StrictStr] = Field(
        None,
        description="SHA-256 de la proyeccion semantica V1 del request.",
    )
    _received_at_utc_ms: int = PrivateAttr(default_factory=lambda: time.time_ns() // 1_000_000)
    _local_ingest_id: str = PrivateAttr(default_factory=lambda: str(uuid4()))

    @property
    def received_at_utc_ms(self) -> int:
        return self._received_at_utc_ms

    @property
    def local_ingest_id(self) -> str:
        return self._local_ingest_id

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "origen": "unity",
                "tipo_evento": "actualizacion_estado",
                "id_transaccion": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "objeto_involucrado": "ninguno",
                "detalles": {
                    "posicion_lazzaro": {"x": 1.2, "y": 0.0, "z": 3.4},
                    "objetos_visibles": "Veo Arbol_01 a 2.4m al norte.",
                    "solicita_decision": False,
                },
            }
        }
    )

    @field_validator("id_transaccion")
    @classmethod
    def id_no_vacio(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("id_transaccion no puede estar vacio")
        return value.strip()

    @model_validator(mode="after")
    def lifecycle_exige_identidad_y_payload_estricto(self) -> "EventoEntrante":
        if self.tipo_evento not in {
            "resultado_accion",
            "confirmacion_accion",
            "inicio_ejecucion_accion",
        }:
            return self
        action_id = self.detalles.get("id_accion")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError(
                f"{self.tipo_evento}.detalles.id_accion es requerido y no puede estar vacio"
            )
        self.detalles["id_accion"] = action_id.strip()
        if (
            self.tipo_evento == "resultado_accion"
            and ("exito" not in self.detalles or type(self.detalles["exito"]) is not bool)
        ):
            raise ValueError("resultado_accion.detalles.exito debe ser un bool estricto")
        if (
            self.tipo_evento == "confirmacion_accion"
            and ("approved" not in self.detalles or type(self.detalles["approved"]) is not bool)
        ):
            raise ValueError("confirmacion_accion.detalles.approved debe ser un bool estricto")
        return self

    @model_validator(mode="after")
    def identidad_delivery_es_versionada_y_verificable(self) -> "EventoEntrante":
        version, fingerprint = validate_request_identity(
            self.request_identity_version,
            self.request_fingerprint_sha256,
        )
        if version is None:
            return self
        expected = request_fingerprint_sha256_v1(
            transaction_id=self.id_transaccion,
            origin=self.origen,
            event_type=self.tipo_evento,
            involved_object=self.objeto_involucrado,
            details=self.detalles,
        )
        if fingerprint != expected:
            raise ValueError("request_fingerprint_sha256 no coincide con el request V1")
        return self


class ReconciliacionTransaccionV1(BaseModel):
    version: Literal[1] = 1
    id_transaccion: StrictStr
    origen: Literal["roblox", "unity", "sandbox", "terminal", "otro"]
    tipo_evento: StrictStr
    request_identity_version: Optional[StrictInt] = None
    request_fingerprint_sha256: Optional[StrictStr] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("version debe ser el entero exacto 1")
        return value

    @field_validator("id_transaccion", "tipo_evento")
    @classmethod
    def _required_string(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("campo requerido no puede estar vacio")
        return cleaned

    @model_validator(mode="after")
    def _identity_pair(self) -> "ReconciliacionTransaccionV1":
        validate_request_identity(
            self.request_identity_version,
            self.request_fingerprint_sha256,
        )
        return self


class ParametroHerramienta(BaseModel):
    """
    Descripcion de un parametro que acompana a una accion.
    Se incluye para ayudar al frontend a entender que se espera.
    """

    nombre: str
    tipo: Literal["float", "int", "str", "bool", "coordenadas", "array", "objeto"] = "str"
    descripcion: str = ""
    obligatorio: bool = True


class DecisionPublica(BaseModel):
    """Proyeccion publica minima del outcome; no contiene provenance interna."""

    version: Literal[1] = 1
    disposition: DecisionDisposition
    reason_code: DecisionReasonCode

    model_config = ConfigDict(extra="forbid")

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the exact integer 1")
        return value

    @classmethod
    def from_outcome(cls, outcome: DecisionOutcome) -> "DecisionPublica":
        return cls.model_validate(outcome.public_projection())


class AccionSaliente(BaseModel):
    """
    Accion que el agente ha decidido ejecutar.
    Se envia al frontend para que la materialice en su entorno.
    """

    id_accion: str = Field(
        ...,
        description="Identificador unico de la accion (para correlacion con logs y memoria).",
    )
    nombre: str = Field(
        ...,
        description="Nombre de la accion: 'mover_mouse', 'escribir_teclado', 'hablar', etc.",
    )
    parametros: Dict[str, Any] = Field(
        default_factory=dict,
        description="Argumentos necesarios para ejecutar la accion.",
    )
    requiere_confirmacion: StrictBool = Field(
        False,
        description="Si es True, el frontend deberia pedir confirmacion antes de ejecutar.",
    )
    estado_ejecucion: Literal[
        "legacy_unknown",
        "created",
        "awaiting_authorization",
        "authorized",
        "dispatched",
        "executing",
        "reported_success",
        "reported_failure",
        "rejected",
        "skipped",
    ] = Field(
        "legacy_unknown",
        description="Snapshot durable del lifecycle; independiente del status HTTP.",
    )
    id_transaccion_origen: Optional[str] = Field(
        None,
        description="Transaccion que creó la identidad canonica de esta accion.",
    )
    parametros_descripcion: List[ParametroHerramienta] = Field(
        default_factory=list,
        description="Metadatos opcionales que describen los parametros esperados.",
    )
    perception_authority: Optional[PerceptionAuthorityReferenceV1] = Field(
        None,
        description=(
            "Referencia ROOT-029 minima para una accion fisica ligada a percepcion."
        ),
    )
    _decision_outcome: DecisionOutcome = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        self._decision_outcome = DecisionOutcome.legacy_for_action(self.nombre)

    @property
    def decision_outcome(self) -> DecisionOutcome:
        return self._decision_outcome

    def attach_decision_outcome(self, outcome: DecisionOutcome) -> "AccionSaliente":
        if not isinstance(outcome, DecisionOutcome):
            raise TypeError("outcome must be DecisionOutcome")
        self._decision_outcome = outcome
        return self

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "id_accion": "550e8400-e29b-41d4-a716-446655440000",
                "nombre": "navegar_a_objeto",
                "parametros": {"nombre_objeto": "Arbol_01"},
                "requiere_confirmacion": False,
            }
        }
    )


class ReconciliacionTransaccionRespuestaV1(BaseModel):
    version: Literal[1] = 1
    id_transaccion: StrictStr
    resolution: DeliveryResolutionV1
    directive: DeliveryDirectiveV1
    receipt_kind: DurableReceiptKindV1
    receipt_status: DurableReceiptStatusV1
    accion: Optional[AccionSaliente] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("version debe ser el entero exacto 1")
        return value

    @model_validator(mode="after")
    def _retry_only_for_not_found(self) -> "ReconciliacionTransaccionRespuestaV1":
        validate_delivery_reconciliation(
            resolution=self.resolution,
            directive=self.directive,
            receipt_kind=self.receipt_kind,
            receipt_status=self.receipt_status,
            has_action=self.accion is not None,
        )
        return self


class RespuestaAPI(BaseModel):
    """
    Formato unificado de respuesta para cualquier endpoint.
    Incluye estado, mensaje y opcionalmente una accion a ejecutar.
    """

    wire_version: Literal[1] = Field(
        1,
        description="Version del contrato Python-Unity de la respuesta.",
    )
    status: Literal["ok", "error", "pendiente"] = Field(
        "ok",
        description="Resultado del procesamiento.",
    )
    mensaje: str = Field(
        "",
        description="Descripcion legible del resultado.",
    )
    id_transaccion: Optional[str] = Field(
        None,
        description="ID de la transaccion original (si se proporciono).",
    )
    accion: Optional[AccionSaliente] = Field(
        None,
        description="Accion que el agente desea ejecutar en el frontend.",
    )
    decision: Optional[DecisionPublica] = Field(
        None,
        description=(
            "Resultado semantico seguro de la decision, independiente de que accion sea null."
        ),
    )
    timestamp_servidor: datetime = Field(
        default_factory=datetime.utcnow,
        description="Momento en que el servidor genero la respuesta.",
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "wire_version": 1,
                "status": "ok",
                "mensaje": "Evento procesado y accion generada.",
                "id_transaccion": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "decision": {
                    "version": 1,
                    "disposition": "action",
                    "reason_code": "primary_valid",
                },
                "accion": {
                    "id_accion": "550e8400-e29b-41d4-a716-446655440000",
                    "nombre": "interactuar_objeto_3d",
                    "parametros": {"nombre_objeto": "Arbol_01", "tipo_accion": "golpear"},
                    "requiere_confirmacion": False,
                },
            }
        }
    )

    @field_validator("wire_version", mode="before")
    @classmethod
    def _strict_wire_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("wire_version must be the exact integer 1")
        return value


class PublicErrorCode(str, Enum):
    """Taxonomia estable y versionada del boundary publico de errores."""

    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    TURN_ABORTED = "TURN_ABORTED"
    TURN_PROCESSING_FAILED = "TURN_PROCESSING_FAILED"
    TRANSACTION_INCOMPLETE = "TRANSACTION_INCOMPLETE"
    TRANSACTION_CONFLICT = "TRANSACTION_CONFLICT"
    OPERATIONAL_STORAGE_UNAVAILABLE = "OPERATIONAL_STORAGE_UNAVAILABLE"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    STATE_READ_FAILED = "STATE_READ_FAILED"
    CONSTELLATION_READ_FAILED = "CONSTELLATION_READ_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PublicErrorDetail(BaseModel):
    """Unico detalle permitido dentro del error envelope v1."""

    code: PublicErrorCode
    message: str
    correlation_id: UUID

    model_config = ConfigDict(extra="forbid")


class PublicErrorEnvelope(BaseModel):
    """Contrato publico dedicado; no reutiliza el DTO de respuestas exitosas."""

    version: Literal[1] = 1
    status: Literal["error", "pendiente"]
    error: PublicErrorDetail

    model_config = ConfigDict(extra="forbid")

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the exact integer 1")
        return value


class NotificacionSaliente(BaseModel):
    """
    Mensaje espontaneo que el agente puede enviar al frontend
    sin que haya un evento de entrada previo.
    """

    tipo: str = Field(..., description="Tipo publico explicito, por ejemplo 'info' o 'alerta'.")
    mensaje: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EstadoAgente(BaseModel):
    """
    Version resumida del estado interno para que el frontend pueda visualizarlo.
    """

    dopamina: float
    estres: float
    energia: float
    confianza: float
    motivacion: float
    foco: float
    personalidad: Dict[str, float] = Field(default_factory=dict)
    tarea_actual: Optional[str] = None
