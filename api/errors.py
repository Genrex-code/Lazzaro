"""Mapper unico del error envelope publico v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.schemas import PublicErrorCode, PublicErrorDetail, PublicErrorEnvelope
from core.action_ledger import (
    ActionLedgerError,
    TransactionConflictError,
    TransactionIncompleteError,
    TurnAbortedError,
    TurnProcessingError,
    TurnReconciliationRequiredError,
)
from core.logger import get_logger
from core.safe_error_logging import (
    log_safe_boundary_failure,
    normalize_error_correlation_id,
)


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PublicErrorSpec:
    http_status: int
    status: Literal["error", "pendiente"]
    message: str


PUBLIC_ERROR_SPECS: dict[PublicErrorCode, PublicErrorSpec] = {
    PublicErrorCode.RECONCILIATION_REQUIRED: PublicErrorSpec(
        409,
        "pendiente",
        "El turno requiere reconciliacion antes de continuar.",
    ),
    PublicErrorCode.TURN_ABORTED: PublicErrorSpec(
        500,
        "error",
        "El turno fallo de forma controlada y no se volvera a ejecutar.",
    ),
    PublicErrorCode.TURN_PROCESSING_FAILED: PublicErrorSpec(
        500,
        "error",
        "No se pudo procesar el turno.",
    ),
    PublicErrorCode.TRANSACTION_INCOMPLETE: PublicErrorSpec(
        409,
        "pendiente",
        "La transaccion esta incompleta y requiere revision.",
    ),
    PublicErrorCode.TRANSACTION_CONFLICT: PublicErrorSpec(
        409,
        "error",
        "El identificador de transaccion ya pertenece a otro evento.",
    ),
    PublicErrorCode.OPERATIONAL_STORAGE_UNAVAILABLE: PublicErrorSpec(
        503,
        "error",
        "El almacenamiento operacional no esta disponible.",
    ),
    PublicErrorCode.AGENT_UNAVAILABLE: PublicErrorSpec(
        503,
        "error",
        "El agente no esta disponible.",
    ),
    PublicErrorCode.STATE_READ_FAILED: PublicErrorSpec(
        500,
        "error",
        "No se pudo leer el estado del agente.",
    ),
    PublicErrorCode.CONSTELLATION_READ_FAILED: PublicErrorSpec(
        500,
        "error",
        "No se pudo leer la constelacion cognitiva.",
    ),
    PublicErrorCode.VALIDATION_ERROR: PublicErrorSpec(
        422,
        "error",
        "La solicitud no cumple el contrato esperado.",
    ),
    PublicErrorCode.INTERNAL_ERROR: PublicErrorSpec(
        500,
        "error",
        "Ocurrio un error interno.",
    ),
}


class PublicErrorException(Exception):
    """Condicion publica tipada que sera materializada por el handler central."""

    def __init__(
        self,
        code: PublicErrorCode,
        *,
        component: str,
        error: BaseException | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.component = component
        self.error = error
        self.correlation_id = correlation_id


def public_code_for_exception(error: BaseException) -> PublicErrorCode:
    """Clasifica por tipos internos reales; el wire no depende de sus mensajes."""
    if isinstance(error, TurnReconciliationRequiredError):
        return PublicErrorCode.RECONCILIATION_REQUIRED
    if isinstance(error, TurnAbortedError):
        return PublicErrorCode.TURN_ABORTED
    if isinstance(error, TurnProcessingError):
        return PublicErrorCode.TURN_PROCESSING_FAILED
    if isinstance(error, TransactionIncompleteError):
        return PublicErrorCode.TRANSACTION_INCOMPLETE
    if isinstance(error, TransactionConflictError):
        return PublicErrorCode.TRANSACTION_CONFLICT
    if isinstance(error, ActionLedgerError):
        return PublicErrorCode.OPERATIONAL_STORAGE_UNAVAILABLE
    return PublicErrorCode.INTERNAL_ERROR


def build_public_error_envelope(
    code: PublicErrorCode,
    *,
    correlation_id: str | None = None,
) -> PublicErrorEnvelope:
    """Construye el DTO exclusivamente desde allowlists locales."""
    spec = PUBLIC_ERROR_SPECS[code]
    return PublicErrorEnvelope(
        status=spec.status,
        error=PublicErrorDetail(
            code=code,
            message=spec.message,
            correlation_id=normalize_error_correlation_id(correlation_id),
        ),
    )


def public_error_response(
    code: PublicErrorCode,
    *,
    component: str,
    error: BaseException | None = None,
    correlation_id: str | None = None,
) -> JSONResponse:
    """Genera respuesta y log correlacionados sin serializar la excepcion."""
    envelope = build_public_error_envelope(code, correlation_id=correlation_id)
    spec = PUBLIC_ERROR_SPECS[code]
    correlation = str(envelope.error.correlation_id)
    log_safe_boundary_failure(
        logger,
        component=component,
        code=code.value,
        error=error,
        correlation_id=correlation,
        http_status=spec.http_status,
    )
    return JSONResponse(
        status_code=spec.http_status,
        content=envelope.model_dump(mode="json"),
    )


def public_error_from_exception(
    error: BaseException,
    *,
    component: str,
    correlation_id: str | None = None,
) -> JSONResponse:
    return public_error_response(
        public_code_for_exception(error),
        component=component,
        error=error,
        correlation_id=correlation_id,
    )


async def _public_error_exception_handler(
    request: Request,
    error: PublicErrorException,
) -> JSONResponse:
    del request
    return public_error_response(
        error.code,
        component=error.component,
        error=error.error,
        correlation_id=error.correlation_id,
    )


async def _validation_error_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    del request
    return public_error_response(
        PublicErrorCode.VALIDATION_ERROR,
        component="api.validation",
        error=error,
    )


async def _unhandled_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    del request
    return public_error_response(
        PublicErrorCode.INTERNAL_ERROR,
        component="api.unhandled",
        error=error,
    )


def install_public_error_handlers(app: FastAPI) -> None:
    """Instala solo handlers ROOT-010; 404/405 conservan el contrato de FastAPI."""
    app.add_exception_handler(PublicErrorException, _public_error_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
