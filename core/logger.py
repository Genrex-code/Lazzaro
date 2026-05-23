import logging
from logging import Logger

# Configuración por defecto para todo el proyecto
_log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_log_level = logging.INFO

def configurar_logger_root():
    """Configura el logger raíz una sola vez (si no se ha hecho ya)."""
    logging.basicConfig(
        level=_log_level,
        format=_log_format,
        handlers=[
            logging.FileHandler("lazzaro.log"),
            logging.StreamHandler()
        ]
    )

def get_logger(nombre: str | None = None) -> Logger:
    """
    Devuelve un logger configurado. Si no se ha configurado el root,
    lo hace automáticamente.
    """
    # Asegura que el logger raíz tenga al menos la configuración básica
    if not logging.getLogger().hasHandlers():
        configurar_logger_root()
    return logging.getLogger(nombre)

# Ejecutar configuración inicial al importar el módulo por primera vez
configurar_logger_root()


#con esto puedo hacer 
# from core.logger import get_logger
#logger = get_logger(__name__)
#logger.info("Mensaje")