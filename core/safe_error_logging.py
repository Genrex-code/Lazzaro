"""Diagnosticos seguros para boundaries con datos no confiables.

Este modulo no configura handlers ni intenta sanear tracebacks arbitrarios.
Solo permite campos estructurados, acotados y generados por el programa.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID, uuid4


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


def new_error_correlation_id() -> str:
    """Crea una identidad opaca exclusiva del boundary de error."""
    return str(uuid4())


def normalize_error_correlation_id(value: str | None) -> str:
    """Acepta solamente UUIDs; una entrada externa nunca se refleja como correlacion."""
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return new_error_correlation_id()


def safe_error_type(error: BaseException | None) -> str:
    """Extrae solo el nombre acotado de la clase, nunca mensaje ni repr."""
    if error is None:
        return "OperationalCondition"
    name = type(error).__name__
    return name if _SAFE_IDENTIFIER.fullmatch(name) else "Exception"


def _safe_identifier(value: str, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _SAFE_IDENTIFIER.fullmatch(normalized) else fallback


def log_safe_boundary_failure(
    logger: logging.Logger,
    *,
    component: str,
    code: str,
    error: BaseException | None = None,
    correlation_id: str | None = None,
    http_status: int | None = None,
    level: int = logging.ERROR,
) -> str:
    """Registra diagnostico minimo sin contenido controlado por la excepcion."""
    safe_correlation = normalize_error_correlation_id(correlation_id)
    safe_component = _safe_identifier(component, "unknown_component")
    safe_code = _safe_identifier(code, "INTERNAL_ERROR")
    safe_status = int(http_status) if isinstance(http_status, int) else 0
    logger.log(
        level,
        "Boundary failure component=%s code=%s correlation_id=%s "
        "error_type=%s http_status=%d",
        safe_component,
        safe_code,
        safe_correlation,
        safe_error_type(error),
        safe_status,
    )
    return safe_correlation
