import logging
import sys
import threading
from pathlib import Path
from logging import Logger

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_LOG_LEVEL = logging.INFO
_BASE_DIR = Path(__file__).resolve().parents[1]
_LOG_DIR = _BASE_DIR / "logs"
_LOG_FILE = _LOG_DIR / "lazzaro.log"
_CONFIG_LOCK = threading.Lock()
_FALLBACK_REPORTED = False


def _crear_stream_handler() -> logging.StreamHandler:
    stream = sys.stdout
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
    return logging.StreamHandler(stream)


def _descartar_handler(root: Logger, handler: logging.Handler | None) -> None:
    if handler is None:
        return
    root.removeHandler(handler)
    try:
        handler.close()
    except OSError:
        # Un flush/cierre del archivo fallido tampoco puede abortar startup.
        pass


def configurar_logger_root() -> None:
    """Configura el logger raiz; si falla el archivo, degrada a stderr."""
    global _FALLBACK_REPORTED
    with _CONFIG_LOCK:
        root = logging.getLogger()
        # basicConfig historicamente respetaba cualquier handler raiz existente.
        if root.handlers:
            return

        file_handler: logging.Handler | None = None
        stream_handler: logging.Handler | None = None
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
            stream_handler = _crear_stream_handler()
            logging.basicConfig(
                level=_LOG_LEVEL,
                format=_LOG_FORMAT,
                handlers=[file_handler, stream_handler],
            )
            # Otro configurador pudo ganar la carrera sobre el root global.
            if file_handler not in root.handlers:
                _descartar_handler(root, file_handler)
                _descartar_handler(root, stream_handler)
        except OSError:
            for handler in (file_handler, stream_handler):
                _descartar_handler(root, handler)
            fallback: logging.Handler | None = None
            try:
                fallback = logging.StreamHandler(sys.stderr)
                logging.basicConfig(
                    level=_LOG_LEVEL,
                    format=_LOG_FORMAT,
                    handlers=[fallback],
                )
                if fallback not in root.handlers:
                    _descartar_handler(root, fallback)
                    return
            except OSError:
                _descartar_handler(root, fallback)
                return
            if not _FALLBACK_REPORTED:
                _FALLBACK_REPORTED = True
                try:
                    sys.stderr.write("WARNING: file logging unavailable; using stderr.\n")
                except (OSError, ValueError):
                    pass
        except Exception:
            for handler in (file_handler, stream_handler):
                _descartar_handler(root, handler)
            raise


def get_logger(nombre: str | None = None) -> Logger:
    """Devuelve un logger configurado para el modulo solicitado."""
    configurar_logger_root()
    return logging.getLogger(nombre)


configurar_logger_root()
