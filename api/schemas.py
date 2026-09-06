"""
api/schemas.py - Modelos de datos robustos y bidireccionales para la API REST.
Soporta eventos desde Roblox, Unity y cualquier otro frontend,
asi como la devolucion de acciones estructuradas que el agente decide ejecutar.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

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
        description="UUID unico generado por el cliente. Garantiza idempotencia y trazabilidad.",
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
    def resultado_accion_exige_exito_booleano(self) -> "EventoEntrante":
        if (
            self.tipo_evento == "resultado_accion"
            and "exito" in self.detalles
            and type(self.detalles["exito"]) is not bool
        ):
            raise ValueError("resultado_accion.detalles.exito debe ser un bool estricto")
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
