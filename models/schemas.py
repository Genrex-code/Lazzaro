from pydantic import BaseModel
from typing import Optional, Dict, Any

class EventoRoblox(BaseModel):
    tipo_evento: str           # ej. "colision", "tiempo_muerto", "comando"
    objeto_involucrado: str    # ej. "cubo_rojo", "pared", "ninguno"
    detalles: Optional[Dict[str, Any]] = None # JSON extra con coordenadas o datos