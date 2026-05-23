import logging
from fastapi import FastAPI, Request
from api.routes import router
from core.state_engine import LazzaroState
from memory.vector_db import ChromaDBConnector
from ai_engine.gemini_client import GeminiClient
from tools.registry import ToolRegistry
from tools.simulated.keyboard_sim import KeyboardSim
from tools.simulated.mouse_sim import MouseSim
from services.agente_services import AgenteService
from config.settings import Settings
from core.logger import get_logger

# configuracion de logs fuertes :3
logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Fabrica la aplicacion FastApi con todos los componentes inyectados."""
    app = FastAPI(title ="Lazzaro backend", version = "0.1")

    #1.- cargar configuracion desde .env (usando settitngs.py)
    logger.info ("Cargando configuracion desde .env...")
    settings = Settings()
    
    #2.- inicializar componentes de manejo de errores

    try:
        logger.info("Inicializando estado emocional ...")
        estado = LazzaroState()

        logger.info("Inicializando base de datos vectorial (ChromaDB)...")
        vector_db = ChromaDBConnector(
            persist_directory = settings.CHROMA_DB_PATH
        )
        logger.info("configurando cliente gemini...")
        gemini_client = GeminiClient(
            api_key = settings.GEMINI_API_KEY
        )
# por el momento seran las simuladas ya luego reestablecere el uso de otras
# herramientas mas avanzadas, pero esto me permite avanzar con el desarrollo
# del backend sin depender de herramientas externas
        logger.info("Registrando herramientas... ")
        tool_registry = ToolRegistry()
        tool_registry.register_tool("keyboard", KeyboardSim())
        tool_registry.register_tool("mouse", MouseSim())

        logger.info("Construyendo servicio orquestador ...")
        agente_service = AgenteService(
            estado = estado,
            vector_db = vector_db,
            gemini_client = gemini_client,
            tool_registry = tool_registry
        )
        #guardamos el servicio en el estado global para que los endpoints puedan acceder a el
        app.state.agente_service = agente_service
        logger.info("AgenteService inicializado correctamente.")
        #excepcion con raise para que la app no levante si algo esta mal
    except Exception as e:
        logger.error(f"Error al inicializar componentes: {e}")
        raise

    #3.- incluir las rutas de api
    app.include_router(router)

    #4.- evento de inicio ( startup)

    @app.on_event("startup")
    async def startup_event():
        logger.info("Aplicacion iniciada correctamente con sistemas nominales cargados.")
        #desde aca cargaremos los recuerdos 
        #ejemplo #await vector_db.initialize() o algo asi si es necesario
    return app
#punto de entrada principal
app = create_app()

if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor Uvicorn en puerto 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)