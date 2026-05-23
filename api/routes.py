from fastapi import APIRouter
import logging
from models.schemas import EventoRoblox
# from services.agente_services import procesar_evento_lazzaro  <-- Lo usarás pronto

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/evento")
async def recibir_evento(evento: EventoRoblox):
    logger.info(f"📥 Evento recibido desde Roblox: {evento.tipo_evento} con {evento.objeto_involucrado}")
    
    # Aquí llamaremos a tu servicio pesado:
    # respuesta = procesar_evento_lazzaro(evento)
    
    return {"status": "recibido", "accion_lazzaro": "pendiente"}