"""
api/routes.py - Endpoints REST para Roblox, Unity y otros clientes.
Recibe eventos externos, los pasa al orquestador central y devuelve acciones.
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from api.schemas import (
    EventoEntrante,
    RespuestaAPI,
    EstadoAgente,
    NotificacionSaliente,
)
from core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["lazzaro"])

# Dependencia para obtener el orquestador desde app.state
async def get_orchestrator(request: Request):
    """Obtiene el orquestador inyectado en el estado de la aplicación."""
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail="Orquestador no disponible. Espere a que el agente esté listo.",
        )
    return orch

# Endpoints principales

@router.post("/evento", response_model=RespuestaAPI)
async def procesar_evento(
    evento: EventoEntrante,
    orchestrator = Depends(get_orchestrator),
):
    """
    Recibe un evento desde Roblox, Unity o el sandbox.
    El orquestador actualiza el estado interno, consulta la memoria,
    razona con la IA y devuelve la acción a ejecutar (si procede).
    """
    logger.info("Evento recibido: origen=%s, tipo=%s, id_transaccion=%s",
                evento.origen, evento.tipo_evento, evento.id_transaccion)

    try:
        # Delegar al orquestador central (devuelve una AccionSaliente o None)
        accion = await run_in_threadpool(orchestrator.procesar_evento, evento)

        # Si no hay acción, devolvemos estado ok sin acción
        if accion is None:
            return RespuestaAPI(
                status="ok",
                mensaje="Evento procesado, pero el agente no requiere acción inmediata.",
                id_transaccion=evento.id_transaccion,
                accion = None
            )

        # Si hay acción, la empaquetamos
        return RespuestaAPI(
            status="ok",
            mensaje="Acción generada correctamente.",
            id_transaccion=evento.id_transaccion,
            accion=accion,
        )

    except Exception as e:
        logger.error("Error al procesar evento %s: %s", evento.id_transaccion, e, exc_info=True)
        error = RespuestaAPI(
            status="error",
            mensaje=f"Error interno: {str(e)}",
            id_transaccion=evento.id_transaccion,
            accion=None,
        )
        return JSONResponse(status_code=500, content=error.model_dump(mode="json"))


@router.get("/estado", response_model=EstadoAgente)
async def obtener_estado(request: Request):
    """
    Devuelve el estado interno actual del agente (emocional y de personalidad)
    para que el frontend pueda visualizarlo en tiempo real.
    """
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Agente no disponible")

    # Obtener snapshot del estado desde el core (el orchestrator expone un método)
    try:
        estado = orch.obtener_estado_publico()
        return estado
    except Exception as e:
        logger.error("Error al obtener estado: %s", e)
        raise HTTPException(status_code=500, detail="Error al leer el estado del agente")


@router.get("/notificaciones", response_model=list[NotificacionSaliente])
async def obtener_notificaciones(request: Request):
    """
    Recupera mensajes espontáneos que el agente quiere comunicar al exterior,
    por ejemplo, avisos de cansancio, sugerencias, etc.
    """
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Agente no disponible")

    # El orchestrator mantiene una cola de notificaciones salientes
    try:
        notificaciones = orch.obtener_notificaciones()
        return notificaciones
    except Exception as e:
        logger.error("Error al obtener notificaciones: %s", e)
        return []


@router.get("/constelacion")
async def obtener_constelacion(request: Request):
    """
    Devuelve el snapshot cognitivo actual del agente en formato estructurado.
    Es util para depuracion, visualizacion y telemetria del estado interno.
    """
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(status_code=503, detail="Agente no disponible")

    try:
        return await run_in_threadpool(orch.obtener_constelacion_publica)
    except Exception as e:
        logger.error("Error al obtener constelacion cognitiva: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Error al leer la constelacion cognitiva")


@router.post("/comando", response_model=RespuestaAPI)
async def enviar_comando_directo(
    comando: EventoEntrante,
    orchestrator = Depends(get_orchestrator),
):
    """
    Recibe texto directo desde clientes administrativos, sandbox o terminal.
    Se procesa como `comando_usuario`; el origen determina la politica efectiva.
    """
    # Forzamos el tipo de evento para que el core lo interprete como orden directa
    comando.tipo_evento = "comando_usuario"
    return await procesar_evento(comando, orchestrator)
