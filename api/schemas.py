"""
api/schemas.py - Modelos de datos robustos y bidireccionales para la API REST.
Soporta eventos desde Roblox, Unity y cualquier otro frontend,
así como la devolución de acciones estructuradas que el agente decide ejecutar.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, validator


# ------------------------------------------------------------------
# 1. Eventos entrantes (Roblox, Unity, etc.)
# ------------------------------------------------------------------

class EventoEntrante(BaseModel):
    """
    Evento que envía un frontend al agente.
    Campos mínimos requeridos y un payload flexible según el tipo de evento.
    """
    origen: Literal["roblox", "unity", "sandbox", "otro"] = Field(
        ...,
        description="Fuente del evento. Permite filtrar o cambiar comportamiento si es necesario."
    )
    tipo_evento: str = Field(
        ...,
        min_length=1,
        description="Identificador del suceso: 'recompensa', 'error_critico', 'comando_usuario', etc."
    )
    id_transaccion: str = Field(
        ...,
        min_length=1,
        description="UUID único generado por el cliente. Garantiza idempotencia y trazabilidad."
    )
    timestamp: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        description="Momento en que ocurrió el evento en el cliente. Si no se envía, usamos UTC ahora."
    )
    objeto_involucrado: str = Field(
        "ninguno",
        description="Entidad principal afectada: nombre de herramienta, objeto del mundo, etc."
    )
    detalles: Dict[str, Any] = Field(
        default_factory=dict,
        description="Payload flexible con información específica del evento."
    )

    @validator("id_transaccion")
    def id_no_vacio(cls, v):
        if not v or not v.strip():
            raise ValueError("id_transaccion no puede estar vacío")
        return v.strip()

    class Config:
        schema_extra = {
            "example": {
                "origen": "roblox",
                "tipo_evento": "colision",
                "id_transaccion": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "objeto_involucrado": "cubo_rojo",
                "detalles": {"velocidad": 10.5, "punto_impacto": {"x": 1.2, "y": 3.4}}
            }
        }


# ------------------------------------------------------------------
# 2. Acciones que el agente decide ejecutar (salida)
# ------------------------------------------------------------------

class ParametroHerramienta(BaseModel):
    """
    Descripción de un parámetro que acompaña a una acción.
    Se incluye para ayudar al frontend a entender qué se espera.
    """
    nombre: str
    tipo: Literal["float", "int", "str", "bool", "coordenadas", "array", "objeto"] = "str"
    descripcion: str = ""
    obligatorio: bool = True


class AccionSaliente(BaseModel):
    """
    Acción que el agente ha decidido ejecutar.
    Se envía al frontend para que la materialice en su entorno.
    """
    id_accion: str = Field(
        ...,
        description="Identificador único de la acción (para correlación con logs y memoria)."
    )
    nombre: str = Field(
        ...,
        description="Nombre de la acción: 'mover_mouse', 'escribir_teclado', 'hablar', etc."
    )
    parametros: Dict[str, Any] = Field(
        default_factory=dict,
        description="Argumentos necesarios para ejecutar la acción."
    )
    razonamiento: str = Field(
        "",
        description="Explicación textual de por qué el agente eligió esta acción."
    )
    pensamiento_privado: str = Field(
        "",
        description="Monólogo interno del agente (visible para depuración, no para el usuario final)."
    )
    requiere_confirmacion: bool = Field(
        False,
        description="Si es True, el frontend debería pedir confirmación al usuario antes de ejecutar."
    )
    parametros_descripcion: List[ParametroHerramienta] = Field(
        default_factory=list,
        description="Metadatos opcionales que describen los parámetros esperados."
    )

    class Config:
        schema_extra = {
            "example": {
                "id_accion": "550e8400-e29b-41d4-a716-446655440000",
                "nombre": "mover_mouse",
                "parametros": {"x": 500, "y": 300},
                "razonamiento": "Necesito hacer clic en el botón 'Aceptar'.",
                "pensamiento_privado": "El botón está en la posición (500,300) según mi última observación.",
                "requiere_confirmacion": False
            }
        }


# ------------------------------------------------------------------
# 3. Respuesta estándar de la API
# ------------------------------------------------------------------

class RespuestaAPI(BaseModel):
    """
    Formato unificado de respuesta para cualquier endpoint.
    Incluye estado, mensaje y opcionalmente una acción a ejecutar.
    """
    status: Literal["ok", "error", "pendiente"] = Field(
        "ok",
        description="Resultado del procesamiento."
    )
    mensaje: str = Field(
        "",
        description="Descripción legible del resultado."
    )
    id_transaccion: Optional[str] = Field(
        None,
        description="ID de la transacción original (si se proporcionó)."
    )
    accion: Optional[AccionSaliente] = Field(
        None,
        description="Acción que el agente desea ejecutar en el frontend."
    )
    timestamp_servidor: datetime = Field(
        default_factory=datetime.utcnow,
        description="Momento en que el servidor generó la respuesta."
    )

    class Config:
        schema_extra = {
            "example": {
                "status": "ok",
                "mensaje": "Evento procesado y acción generada.",
                "id_transaccion": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "accion": {
                    "id_accion": "550e8400-e29b-41d4-a716-446655440000",
                    "nombre": "hablar",
                    "parametros": {"texto": "¡He completado la tarea!"},
                    "razonamiento": "Quiero informar al usuario del éxito.",
                    "pensamiento_privado": "Estoy orgulloso de este logro.",
                    "requiere_confirmacion": False
                }
            }
        }


# ------------------------------------------------------------------
# 4. Modelos auxiliares para notificaciones y estado
# ------------------------------------------------------------------

class NotificacionSaliente(BaseModel):
    """
    Mensaje espontáneo que el agente puede enviar al frontend
    sin que haya un evento de entrada previo (por ejemplo, un aviso de descanso).
    """
    tipo: str = Field(..., description="'info', 'alerta', 'error', 'pensamiento'")
    mensaje: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EstadoAgente(BaseModel):
    """
    Versión resumida del estado interno para que el frontend pueda visualizarlo.
    """
    dopamina: float
    estres: float
    energia: float
    confianza: float
    motivacion: float
    foco: float
    personalidad: Dict[str, float] = Field(default_factory=dict)
    tarea_actual: Optional[str] = None