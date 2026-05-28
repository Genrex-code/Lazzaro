import logging
import sys
from logging import Logger

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_LOG_LEVEL = logging.INFO


def _crear_stream_handler() -> logging.StreamHandler:
    stream = sys.stdout
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
    return logging.StreamHandler(stream)


def configurar_logger_root() -> None:
    """Configura el logger raiz una sola vez."""
    root = logging.getLogger()
    if root.handlers:
        return

    logging.basicConfig(
        level=_LOG_LEVEL,
        format=_LOG_FORMAT,
        handlers=[
            logging.FileHandler("lazzaro.log", encoding="utf-8"),
            _crear_stream_handler(),
        ],
    )


def get_logger(nombre: str | None = None) -> Logger:
    """Devuelve un logger configurado para el modulo solicitado."""
    configurar_logger_root()
    return logging.getLogger(nombre)


configurar_logger_root()
