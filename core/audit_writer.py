"""Writer JSONL aislado para AuditTrace (OBS-002).

El writer no conoce al Orchestrator ni vuelve a serializar/sanear datos. Su
unica entrada valida es AuditTrace y su unica representacion es to_json().
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from core.audit_trace import AuditTrace


_BASE_DIR = Path(__file__).resolve().parents[1]
AUDIT_LOG_FILE = _BASE_DIR / "logs" / "audit_trace.jsonl"

# Un lock compartido protege tambien a escritores distintos que apunten al
# mismo archivo dentro del proceso.
_WRITE_LOCK = threading.Lock()

# La guardia es por hilo y se mantiene activa mientras se emite el warning. Si
# algun handler humano intentara auditar ese warning, la llamada reentrante se
# rechaza sin volver a loguear y se corta el ciclo.
_WRITE_GUARD = threading.local()


@dataclass(frozen=True, slots=True)
class AuditWriteResult:
    """Resultado observable de un intento de append, sin detalles sensibles."""

    success: bool
    path: Path
    error_type: str | None = None

    def __bool__(self) -> bool:
        return self.success


class AuditTraceJSONLWriter:
    """Agrega una linea UTF-8 por AuditTrace sin propagar fallos al runtime."""

    def __init__(
        self,
        path: str | Path = AUDIT_LOG_FILE,
        *,
        human_logger: logging.Logger | None = None,
    ) -> None:
        self.path = Path(path)
        self._human_logger = human_logger

    def write(self, trace: AuditTrace) -> AuditWriteResult:
        """Intenta agregar una traza y siempre devuelve exito/fallo observable."""
        if getattr(_WRITE_GUARD, "active", False):
            return AuditWriteResult(
                success=False,
                path=self.path,
                error_type="ReentrantAuditWrite",
            )

        _WRITE_GUARD.active = True
        try:
            if not isinstance(trace, AuditTrace):
                raise TypeError("AuditTraceJSONLWriter.write requiere AuditTrace")

            # AuditTrace es el unico dueño de la serializacion y el saneamiento.
            json_line = trace.to_json()
            if "\n" in json_line or "\r" in json_line:
                raise ValueError("AuditTrace.to_json() debe producir una sola linea")
            encoded_line = (json_line + "\n").encode("utf-8")

            with _WRITE_LOCK:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
                file_descriptor = os.open(self.path, flags, 0o600)
                try:
                    written = os.write(file_descriptor, encoded_line)
                    if written != len(encoded_line):
                        raise OSError("append JSONL incompleto")
                finally:
                    os.close(file_descriptor)

            return AuditWriteResult(success=True, path=self.path)
        except Exception as exc:
            error_type = exc.__class__.__name__
            self._warn_human_logger(error_type)
            return AuditWriteResult(
                success=False,
                path=self.path,
                error_type=error_type,
            )
        finally:
            _WRITE_GUARD.active = False

    def _warn_human_logger(self, error_type: str) -> None:
        """Avisa por el canal humano sin permitir que el aviso propague fallos."""
        try:
            logger = self._human_logger
            if logger is None:
                # Import tardio: el writer no configura ni envuelve el logger.
                from core.logger import get_logger

                logger = get_logger(__name__)
            logger.warning(
                "No se pudo escribir AuditTrace JSONL; la ejecucion continuara. "
                "path=%s error_type=%s",
                self.path,
                error_type,
            )
        except Exception:
            # Incluso un handler humano defectuoso no puede derribar el runtime
            # ni volver a entrar recursivamente al writer.
            return
