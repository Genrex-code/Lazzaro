"""
tools/simulated/movimiento_sim.py - Acciones dentro del mundo virtual 3D.
"""

from typing import Union
from tools.registry import ToolRegistry
from pydantic import BaseModel, Field

# 1. Definimos estrictamente qué necesita Lazzaro para moverse
class MoverseParams(BaseModel):
    direccion: str = Field(
        ...,
        description=(
            "'adelante', 'atras', 'izquierda', 'derecha'. "
            "Si usas rotacion_y para mirar a un nuevo rumbo y luego avanzar, normalmente usa 'adelante'. "
            "Usa 'izquierda' o 'derecha' solo para desplazamiento lateral."
        ),
    )
    # Usamos Union para que acepte float, int o string si Gemini se pone creativo
    distancia: Union[float, int, str] = Field(
        ...,
        description=(
            "Unidades o metros a recorrer en el entorno 3D. "
            "Debe ser mayor que 0 si realmente deseas avanzar. "
            "Si necesitas acercarte pero no conoces la distancia exacta, usa un paso corto positivo como 1.0."
        ),
    )
    # Aceptamos string o números
    velocidad: Union[str, float, int] = Field("caminar", description="'caminar', 'correr' o 'sigilo'")
    rotacion_y: float = Field(
        0.0,
        description=(
            "Grados de giro sobre el eje Y antes de avanzar. "
            "Ejemplo: 90 para mirar a la derecha y luego avanzar hacia adelante."
        ),
    )

# 2. Registramos la herramienta
@ToolRegistry.register(
    name="moverse_entorno_3d",
    description=(
        "Mueve el avatar de Lazzaro dentro del entorno virtual (Roblox/Unity). "
        "Puede girar primero con rotacion_y y despues desplazarse."
    ),
    params_schema=MoverseParams,
    requires_confirmation=False
)
def moverse_entorno_3d(params: MoverseParams) -> str:
    """
    Como es simulado, no ejecutamos código físico.
    Devolvemos el string que Lazzaro leerá en su memoria de trabajo para saber qué hizo.
    """
    # Limpiamos los datos de forma segura para no romper el motor
    try:
        distancia_segura = float(params.distancia)
    except (ValueError, TypeError):
        distancia_segura = 1.0 # Fallback si manda texto incomprensible
        
    velocidad_segura = str(params.velocidad)
    rotacion_segura = float(params.rotacion_y)

    return (
        f"[Mundo Virtual] Comando enviado al motor: {velocidad_segura} hacia "
        f"{params.direccion} por {distancia_segura} unidades con rotacion_y={rotacion_segura}."
    )


class InteraccionParams(BaseModel):
    nombre_objeto: str = Field(..., description="Nombre del objeto con el que interactuar (ej. 'puerta_principal')")
    tipo_accion: str = Field(..., description="'agarrar', 'presionar', 'mirar'")

@ToolRegistry.register(
    name="interactuar_objeto_3d",
    description="Permite a Lazzaro interactuar con elementos del mapa en Roblox o Unity.",
    params_schema=InteraccionParams
)
def interactuar_objeto_3d(params: InteraccionParams) -> str:
    return f"[Mundo Virtual] Intento de {params.tipo_accion} sobre el objeto '{params.nombre_objeto}'."
