"""
main.py - Punto de entrada de Lazzaro.
Construye la aplicación FastAPI con todos los componentes correctamente inyectados.

Correcciones aplicadas vs versión anterior:
  - LazzaroState → StateEngine (nombre correcto de la clase)
  - ChromaDBConnector → ChromaClient (nombre correcto de la clase)
  - CHROMA_DB_PATH añadido a Settings (faltaba en config)
  - GeminiClient recibe Settings completo, no api_key suelto
  - Se construye Orchestrator correctamente y se guarda en app.state
  - Se importan los módulos de herramientas para que los decoradores
    @ToolRegistry.register se ejecuten (registro automático)
  - Se elimina el uso de register_tool() que no existe
  - AgenteService eliminado del arranque (no existe; el Orchestrator lo reemplaza)
  - startup_event usa la sintaxis moderna de FastAPI (lifespan)
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from config.settings import Settings
from core.logger import get_logger
from core.state_engine import StateEngine
from core.personality import PersonalityTraits
from core.psychology import PsychologyEngine
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator

from memory.chroma_client import ChromaClient
from memory.memory_manager import MemoryManager

from ai_engine.gemini_client import GeminiClient
from ai_engine.reasoning_engine import ReasoningEngine

from tools.registry import ToolRegistry

# Importar los módulos de herramientas para que los decoradores
# @ToolRegistry.register(...) se ejecuten y queden registradas.
import tools.simulated.keyboard_sim   # noqa: F401
import tools.simulated.mouse_sim      # noqa: F401
import tools.simulated.movimiento_sim # noqa: F401
# Las herramientas reales NO se importan aquí para evitar que
# queden disponibles para el LLM fuera de un entorno sandbox.
# Descomenta la siguiente línea solo si estás en modo sandbox controlado:
# import tools.real.keyboard_real
# import tools.real.mouse_real

from api.routes import router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Ciclo de vida de la aplicación (reemplaza los deprecados @app.on_event).
    Todo lo que va antes del 'yield' es startup; lo de después es shutdown.
    """
    logger.info("=== Iniciando Lazzaro ===")
    app.state.orchestrator = None
    gemini_client = None

    # 1. Cargar configuración desde .env
    logger.info("Cargando configuración...")
    settings = Settings()

    try:
        # 2. Inicializar capa de comportamiento (estado, personalidad, psicología)
        logger.info("Inicializando BehaviorCore...")
        behavior_core = BehaviorCore()

        # 3. Inicializar base de datos vectorial (ChromaDB)
        logger.info("Inicializando ChromaDB en '%s'...", settings.CHROMA_DB_PATH)
        chroma_client = ChromaClient(db_path=settings.CHROMA_DB_PATH)
        memory_manager = MemoryManager(chroma_client=chroma_client)

        # 4. Inicializar cliente de IA (Gemini)
        logger.info("Configurando cliente Gemini (modelo: %s)...", settings.GEMINI_MODEL_NAME)
        gemini_client = GeminiClient(settings=settings)

        # 5. Inicializar motor de razonamiento
        logger.info("Inicializando ReasoningEngine...")
        reasoning_engine = ReasoningEngine(ai_client=gemini_client)

        # 6. Las herramientas ya están registradas gracias a los imports de arriba.
        herramientas_disponibles = ToolRegistry.listar_nombres()
        logger.info(
            "Herramientas registradas (%d): %s",
            len(herramientas_disponibles),
            herramientas_disponibles,
        )

        # 7. Construir el Orchestrator central
        logger.info("Construyendo Orchestrator...")
        orchestrator = Orchestrator(
            behavior_core=behavior_core,
            memory_manager=memory_manager,
            reasoning_engine=reasoning_engine,
            tool_registry=ToolRegistry,  # pasamos la clase, no una instancia
        )

        # 8. Guardar en app.state para que los endpoints puedan accederlo
        app.state.orchestrator = orchestrator
        logger.info("✅ Lazzaro inicializado correctamente. Sistemas nominales.")

    except Exception as e:
        logger.error("❌ Error crítico durante el arranque: %s", e, exc_info=True)
        raise

    yield  # La app está corriendo — aquí FastAPI maneja los requests

    # --- Shutdown ---
    logger.info("=== Apagando Lazzaro ===")
    if gemini_client is not None:
        gemini_client.close()
    # Si en el futuro necesitas cerrar conexiones o hacer flush de memoria, hazlo aquí.


def create_app() -> FastAPI:
    """Fábrica de la aplicación FastAPI."""
    app = FastAPI(
        title="Lazzaro Backend",
        version="0.1",
        description="Sistema cognitivo autónomo con estado emocional, memoria y razonamiento con LLM.",
        lifespan=lifespan,
    )
    app.state.orchestrator = None
    app.include_router(router)
    return app


# Punto de entrada
app = create_app()

if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor Uvicorn en http://0.0.0.0:8000 ...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
