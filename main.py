"""
main.py - Punto de entrada de Lazzaro.

Notas de arquitectura:
  - El backend LLM activo se selecciona desde Settings.AI_PROVIDER.
  - La construccion concreta del cliente vive en ai_engine/client_factory.py.
  - Esto permite cambiar entre Ollama y Gemini sin reescribir el flujo.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_engine.client_factory import build_ai_client, describe_ai_backend
from ai_engine.reasoning_engine import ReasoningEngine
from api.errors import install_public_error_handlers
from api.routes import router
from config.settings import Settings
from core.behavior_core import BehaviorCore
from core.logger import get_logger
from core.orchestrator import Orchestrator
from core.safe_error_logging import log_safe_boundary_failure
from memory.chroma_client import ChromaClient
from memory.memory_manager import MemoryManager
from tools.registry import ToolRegistry

# Importar los modulos de herramientas para que los decoradores
# @ToolRegistry.register(...) se ejecuten y queden registradas.
import tools.simulated.interaccion_sim  # noqa: F401
import tools.simulated.keyboard_sim  # noqa: F401
import tools.simulated.mouse_sim  # noqa: F401
import tools.simulated.movimiento_sim  # noqa: F401
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Ciclo de vida de la aplicacion.
    Todo lo que va antes del 'yield' es startup; lo de despues es shutdown.
    """
    logger.info("=== Iniciando Lazzaro ===")
    app.state.orchestrator = None
    app.state.ai_client = None
    ai_client = None
    memory_manager = None

    try:
        logger.info("Cargando configuracion...")
        settings = Settings()

        logger.info("Inicializando BehaviorCore...")
        behavior_core = BehaviorCore()

        logger.info("Inicializando ChromaDB en '%s'...", settings.CHROMA_DB_PATH)
        chroma_client = ChromaClient(db_path=settings.CHROMA_DB_PATH)
        memory_manager = MemoryManager(chroma_client=chroma_client)

        # Punto principal para cambiar de backend en el futuro:
        # 1. Cambia AI_PROVIDER en .env o en Settings
        # 2. Si agregas otro proveedor, extiende ai_engine/client_factory.py
        logger.info("Configurando backend LLM: %s", describe_ai_backend(settings))
        ai_client = build_ai_client(settings)
        app.state.ai_client = ai_client

        if not ai_client.health_check():
            logger.warning(
                "El health check del backend LLM activo fallo. "
                "La app arrancara, pero el razonamiento podria responder vacio."
            )

        logger.info("Inicializando ReasoningEngine...")
        reasoning_engine = ReasoningEngine(ai_client=ai_client)

        herramientas_disponibles = ToolRegistry.listar_nombres()
        logger.info(
            "Herramientas registradas (%d): %s",
            len(herramientas_disponibles),
            herramientas_disponibles,
        )

        logger.info("Construyendo Orchestrator...")
        orchestrator = Orchestrator(
            behavior_core=behavior_core,
            memory_manager=memory_manager,
            reasoning_engine=reasoning_engine,
            tool_registry=ToolRegistry,
            action_ledger_path=settings.ACTION_LEDGER_PATH,
        )

        app.state.orchestrator = orchestrator
        logger.info("Lazzaro inicializado correctamente. Sistemas nominales.")
        logger.info("Chingaderita jalando con madre 👍🍞")

    except Exception as error:
        log_safe_boundary_failure(
            logger,
            component="startup",
            code="AGENT_UNAVAILABLE",
            error=error,
        )
        if memory_manager is not None:
            memory_manager.close()
        if ai_client is not None:
            ai_client.close()
        raise

    yield # no se para que esta madre :3 
    logger.info("=== Apagando Lazzaro ===")
    orchestrator = getattr(app.state, "orchestrator", None)
    if orchestrator is not None:
        close = getattr(orchestrator, "close", None)
        if callable(close):
            close()
    if memory_manager is not None:
        memory_manager.close()
    if ai_client is not None:
        ai_client.close()


def create_app() -> FastAPI:
    """Fabrica de la aplicacion FastAPI."""
    app = FastAPI(
        title="Lazzaro Backend",
        version="0.1",
        description="Sistema cognitivo autonomo con estado emocional, memoria y razonamiento con LLM.",
        lifespan=lifespan,
    )
    app.state.orchestrator = None
    app.state.ai_client = None
    install_public_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor Uvicorn en http://0.0.0.0:8000 ...")
    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
