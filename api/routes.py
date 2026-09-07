"""
api/routes.py - Endpoints REST para Roblox, Unity y otros clientes.
Recibe eventos externos, los pasa al orquestador central y devuelve acciones.
"""

from fastapi import APIRouter, Request, Depends
from fastapi.concurrency import run_in_threadpool

from api.errors import (
    PublicErrorException,
    public_error_from_exception,
    public_error_response,
)
from api.schemas import (
    EventoEntrante,
    RespuestaAPI,
    EstadoAgente,
    NotificacionSaliente,
    PublicErrorCode,
    PublicErrorEnvelope,
)
from core.logger import get_logger
from core.action_ledger import ActionLedgerError
from core.safe_error_logging import log_safe_boundary_failure

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["lazzaro"])

_EVENT_ERROR_RESPONSES = {
    409: {"model": PublicErrorEnvelope},
    422: {"model": PublicErrorEnvelope},
    500: {"model": PublicErrorEnvelope},
    503: {"model": PublicErrorEnvelope},
}
_READ_ERROR_RESPONSES = {
    500: {"model": PublicErrorEnvelope},
    503: {"model": PublicErrorEnvelope},
}

# Dependencia para obtener el orquestador desde app.state
async def get_orchestrator(request: Request):
    """Obtiene el orquestador inyectado en el estado de la aplicación."""
    orch = request.app.state.orchestrator
    if orch is None:
        raise PublicErrorException(
            PublicErrorCode.AGENT_UNAVAILABLE,
            component="api.orchestrator_dependency",
        )
    return orch

# Endpoints principales

@router.post(
    "/evento",
    response_model=RespuestaAPI,
    responses=_EVENT_ERROR_RESPONSES,
)
async def procesar_evento(
    evento: EventoEntrante,
    orchestrator = Depends(get_orchestrator),
):
    """
    Recibe un evento desde Roblox, Unity o el sandbox.
    El orquestador actualiza el estado interno, consulta la memoria,
    razona con la IA y devuelve la acción a ejecutar (si procede).
    """
    logger.info("Evento recibido en API: origen=%s", evento.origen)

    try:
        # Delegar al orquestador central (devuelve una AccionSaliente o None)
        accion = await run_in_threadpool(orchestrator.procesar_evento, evento)

        # Si no hay acción, devolvemos estado ok sin acción
        if accion is None or accion.nombre == "none":
            return RespuestaAPI(
                status="ok",
                mensaje="Evento procesado, pero el agente no requiere acción inmediata.",
                id_transaccion=evento.id_transaccion,
                accion = None
            )

        # El status API describe el request; el estado durable vive en la acción.
        esperando_autorizacion = (
            accion.estado_ejecucion == "awaiting_authorization"
        )
        return RespuestaAPI(
            status="pendiente" if esperando_autorizacion else "ok",
            mensaje=(
                "Acción pendiente de autorización explícita."
                if esperando_autorizacion
                else "Acción procesada correctamente."
            ),
            id_transaccion=evento.id_transaccion,
            accion=accion,
        )

    except ActionLedgerError as error:
        return public_error_from_exception(error, component="api.evento")
    except Exception as error:
        return public_error_from_exception(error, component="api.evento")


@router.get(
    "/estado",
    response_model=EstadoAgente,
    responses=_READ_ERROR_RESPONSES,
)
async def obtener_estado(request: Request):
    """
    Devuelve el estado interno actual del agente (emocional y de personalidad)
    para que el frontend pueda visualizarlo en tiempo real.
    """
    orch = request.app.state.orchestrator
    if orch is None:
        raise PublicErrorException(
            PublicErrorCode.AGENT_UNAVAILABLE,
            component="api.estado",
        )

    # Obtener snapshot del estado desde el core (el orchestrator expone un método)
    try:
        estado = orch.obtener_estado_publico()
        return estado
    except Exception as error:
        return public_error_response(
            PublicErrorCode.STATE_READ_FAILED,
            component="api.estado",
            error=error,
        )


@router.get(
    "/notificaciones",
    response_model=list[NotificacionSaliente],
    responses={503: {"model": PublicErrorEnvelope}},
)
async def obtener_notificaciones(request: Request):
    """
    Recupera mensajes espontáneos que el agente quiere comunicar al exterior,
    por ejemplo, avisos de cansancio, sugerencias, etc.
    """
    orch = request.app.state.orchestrator
    if orch is None:
        raise PublicErrorException(
            PublicErrorCode.AGENT_UNAVAILABLE,
            component="api.notificaciones",
        )

    # El orchestrator mantiene una cola de notificaciones salientes
    try:
        notificaciones = orch.obtener_notificaciones()
        return notificaciones
    except Exception as error:
        # ROOT-055 conserva deliberadamente failure -> [] en este packet.
        log_safe_boundary_failure(
            logger,
            component="api.notificaciones",
            code=PublicErrorCode.INTERNAL_ERROR.value,
            error=error,
        )
        return []


@router.get("/constelacion", responses=_READ_ERROR_RESPONSES)
async def obtener_constelacion(request: Request):
    """
    Devuelve el snapshot cognitivo actual del agente en formato estructurado.
    Es util para depuracion, visualizacion y telemetria del estado interno.
    """
    orch = request.app.state.orchestrator
    if orch is None:
        raise PublicErrorException(
            PublicErrorCode.AGENT_UNAVAILABLE,
            component="api.constelacion",
        )

    try:
        return await run_in_threadpool(orch.obtener_constelacion_publica)
    except Exception as error:
        return public_error_response(
            PublicErrorCode.CONSTELLATION_READ_FAILED,
            component="api.constelacion",
            error=error,
        )


@router.post(
    "/comando",
    response_model=RespuestaAPI,
    responses=_EVENT_ERROR_RESPONSES,
)
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
