"""
api/schemas.py - Modelos de datos robustos y bidireccionales para la API REST.
Soporta eventos desde Roblox, Unity y cualquier otro frontend,
asi como la devolucion de acciones estructuradas que el agente decide ejecutar.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


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


class ParametroHerramienta(BaseModel):
    """
    Descripcion de un parametro que acompana a una accion.
    Se incluye para ayudar al frontend a entender que se espera.
    """

    nombre: str
    tipo: Literal["float", "int", "str", "bool", "coordenadas", "array", "objeto"] = "str"
    descripcion: str = ""
    obligatorio: bool = True


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
    razonamiento: str = Field(
        "",
        description="Explicacion textual de por que el agente eligio esta accion.",
    )
    pensamiento_privado: str = Field(
        "",
        description="Monologo interno del agente para depuracion.",
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

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id_accion": "550e8400-e29b-41d4-a716-446655440000",
                "nombre": "navegar_a_objeto",
                "parametros": {"nombre_objeto": "Arbol_01"},
                "razonamiento": "Necesito acercarme al arbol mas cercano para explorarlo.",
                "pensamiento_privado": "La ruta esta libre y el objetivo sigue vigente.",
                "requiere_confirmacion": False,
            }
        }
    )


class RespuestaAPI(BaseModel):
    """
    Formato unificado de respuesta para cualquier endpoint.
    Incluye estado, mensaje y opcionalmente una accion a ejecutar.
    """

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
    timestamp_servidor: datetime = Field(
        default_factory=datetime.utcnow,
        description="Momento en que el servidor genero la respuesta.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "mensaje": "Evento procesado y accion generada.",
                "id_transaccion": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "accion": {
                    "id_accion": "550e8400-e29b-41d4-a716-446655440000",
                    "nombre": "interactuar_objeto_3d",
                    "parametros": {"nombre_objeto": "Arbol_01", "tipo_accion": "golpear"},
                    "razonamiento": "Ya estoy lo bastante cerca para interactuar con el arbol.",
                    "pensamiento_privado": "Debo aprovechar la proximidad antes de seguir moviendome.",
                    "requiere_confirmacion": False,
                },
            }
        }
    )


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


class NotificacionSaliente(BaseModel):
    """
    Mensaje espontaneo que el agente puede enviar al frontend
    sin que haya un evento de entrada previo.
    """

    tipo: str = Field(..., description="'info', 'alerta', 'error', 'pensamiento'")
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
