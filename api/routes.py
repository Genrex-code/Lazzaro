"""
api/routes.py - Endpoints REST para Roblox, Unity y otros clientes.
Recibe eventos externos, los pasa al orquestador central y devuelve acciones.
"""

from fastapi import APIRouter, Request, Depends, Response
from fastapi.concurrency import run_in_threadpool

from api.errors import (
    PublicErrorException,
    public_error_from_exception,
    public_error_response,
)
from api.schemas import (
    DecisionPublica,
    EventoEntrante,
    RespuestaAPI,
    EstadoAgente,
    NotificacionSaliente,
    PublicErrorCode,
    PublicErrorEnvelope,
    CapabilityPublicaV1,
    LivenessPublicaV1,
    ReadinessPublicaV1,
    ReconciliacionTransaccionV1,
    ReconciliacionTransaccionRespuestaV1,
)
from core.runtime_capabilities import RuntimeCapabilityHolder, RuntimeOverall
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

@router.get("/health/live", response_model=LivenessPublicaV1)
async def obtener_liveness() -> LivenessPublicaV1:
    """Liveness sólo afirma que la API puede contestar esta solicitud."""

    return LivenessPublicaV1()


@router.get(
    "/health/ready",
    response_model=ReadinessPublicaV1,
    responses={503: {"model": ReadinessPublicaV1}},
)
async def obtener_readiness(request: Request, response: Response) -> ReadinessPublicaV1:
    """Publica la última evidencia efectiva de la generación del lifespan."""

    holder = getattr(request.app.state, "runtime_capabilities", None)
    if not isinstance(holder, RuntimeCapabilityHolder):
        holder = RuntimeCapabilityHolder()
    snapshot = holder.snapshot()
    if snapshot.overall is RuntimeOverall.UNAVAILABLE:
        response.status_code = 503
    return ReadinessPublicaV1(
        overall=snapshot.overall,
        verification=snapshot.verification,
        capabilities={
            item.name: CapabilityPublicaV1(
                status=item.status,
                reason_code=item.reason_code,
            )
            for item in snapshot.capabilities
        },
    )

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
        decision = (
            DecisionPublica.from_outcome(accion.decision_outcome)
            if accion is not None
            else None
        )

        # Si no hay acción, devolvemos estado ok sin acción
        if accion is None or accion.nombre == "none":
            return RespuestaAPI(
                status="ok",
                mensaje="Evento procesado, pero el agente no requiere acción inmediata.",
                id_transaccion=evento.id_transaccion,
                accion=None,
                decision=decision,
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
            decision=decision,
        )

    except ActionLedgerError as error:
        return public_error_from_exception(error, component="api.evento")
    except Exception as error:
        return public_error_from_exception(error, component="api.evento")


@router.post(
    "/transacciones/reconciliar",
    response_model=ReconciliacionTransaccionRespuestaV1,
    responses=_EVENT_ERROR_RESPONSES,
)
async def reconciliar_transaccion(
    request_body: ReconciliacionTransaccionV1,
    orchestrator=Depends(get_orchestrator),
):
    """Lee receipts durables; no reclama ni vuelve a procesar la transaccion."""

    try:
        # La consulta es una proyeccion SQLite acotada y read-only. Mantenerla
        # directa evita introducir otra cola/worker como autoridad de delivery.
        result, action = orchestrator.reconciliar_transaccion(
            transaction_id=request_body.id_transaccion,
            origin=request_body.origen,
            event_type=request_body.tipo_evento,
            request_identity_version=request_body.request_identity_version,
            request_fingerprint_sha256=request_body.request_fingerprint_sha256,
        )
        return ReconciliacionTransaccionRespuestaV1(
            id_transaccion=result.transaction_id,
            resolution=result.resolution,
            directive=result.directive,
            receipt_kind=result.receipt_kind,
            receipt_status=result.receipt_status,
            accion=action,
        )
    except ActionLedgerError as error:
        return public_error_from_exception(error, component="api.reconciliation")
    except Exception as error:
        return public_error_from_exception(error, component="api.reconciliation")


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
