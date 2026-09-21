"""
main.py - Punto de entrada de Lazzaro.

Notas de arquitectura:
  - El backend LLM activo se selecciona desde Settings.AI_PROVIDER.
  - La construccion concreta del cliente vive en ai_engine/client_factory.py.
  - Esto permite cambiar entre Ollama y Gemini sin reescribir el flujo.
"""

from contextlib import AsyncExitStack, asynccontextmanager
import logging

from fastapi import FastAPI

from ai_engine.client_factory import build_ai_client, describe_ai_backend
from ai_engine.reasoning_engine import ReasoningEngine
from api.errors import install_public_error_handlers
from api.routes import router
from config.settings import Settings
from core.behavior_core import BehaviorCore
from core.identity_snapshot import IdentitySnapshotProvider, build_static_self_v1
from core.logger import get_logger
from core.orchestrator import Orchestrator
from core.safe_error_logging import log_safe_boundary_failure
from core.runtime_capabilities import (
    RuntimeCapabilityHolder,
    RuntimeOverall,
)
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


class _CleanupFailures:
    """Retiene el primer fallo sin interrumpir los demas cierres."""

    def __init__(self) -> None:
        self.first: BaseException | None = None

    def close(self, stage: str, resource: object) -> None:
        try:
            resource.close()
        except BaseException as error:
            if self.first is None:
                self.first = error
            try:
                log_safe_boundary_failure(
                    logger,
                    component=f"startup.cleanup.{stage}",
                    code="AGENT_UNAVAILABLE",
                    error=error,
                    level=logging.WARNING,
                )
            except Exception:
                # El diagnostico secundario nunca sustituye la causa primaria.
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Ciclo de vida de la aplicacion.
    Todo lo que va antes del 'yield' es startup; lo de despues es shutdown.
    """
    logger.info("=== Iniciando Lazzaro ===")
    readiness_holder = getattr(app.state, "runtime_capabilities", None)
    if not isinstance(readiness_holder, RuntimeCapabilityHolder):
        readiness_holder = RuntimeCapabilityHolder()
        app.state.runtime_capabilities = readiness_holder
    readiness_sink = readiness_holder.begin_generation()
    app.state.orchestrator = None
    app.state.ai_client = None
    cleanup = _CleanupFailures()
    primary_error: BaseException | None = None
    startup_complete = False
    try:
        async with AsyncExitStack() as resources:
            logger.info("Cargando configuracion...")
            settings = Settings()

            logger.info("Inicializando BehaviorCore...")
            behavior_core = BehaviorCore()
            static_self = build_static_self_v1()
            identity_provider = IdentitySnapshotProvider(static_self, behavior_core)

            logger.info("Inicializando ChromaDB en '%s'...", settings.CHROMA_DB_PATH)
            chroma_client = ChromaClient(db_path=settings.CHROMA_DB_PATH)
            resources.callback(cleanup.close, "chroma", chroma_client)
            memory_manager = MemoryManager(
                chroma_client=chroma_client,
                memory_write_ledger_path=settings.MEMORY_WRITE_LEDGER_PATH,
                owns_chroma_client=False,
            )
            set_memory_observer = getattr(
                memory_manager,
                "set_capability_observer",
                None,
            )
            if callable(set_memory_observer):
                set_memory_observer(readiness_sink)
            readiness_sink.observe_memory_composition(
                backend_available=bool(getattr(chroma_client, "disponible", False)),
                ledger_open=bool(
                    getattr(
                        getattr(memory_manager, "memory_write_ledger", None),
                        "is_open",
                        False,
                    )
                ),
            )
            resources.callback(cleanup.close, "memory", memory_manager)

            logger.info("Configurando backend LLM: %s", describe_ai_backend(settings))
            ai_client = build_ai_client(settings)
            set_ai_observer = getattr(ai_client, "set_capability_observer", None)
            if callable(set_ai_observer):
                set_ai_observer(readiness_sink)
            resources.callback(cleanup.close, "ai", ai_client)
            typed_health_check = getattr(ai_client, "health_check_result_async", None)
            if callable(typed_health_check):
                health_result = await typed_health_check()
                healthy = bool(getattr(health_result, "succeeded", False))
            else:
                healthy = bool(await ai_client.health_check_async())
                readiness_sink.observe_llm_health_boolean(healthy)
            if not healthy:
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
                identity_provider=identity_provider,
                action_ledger_path=settings.ACTION_LEDGER_PATH,
            )
            resources.callback(cleanup.close, "orchestrator", orchestrator)

            app.state.ai_client = ai_client
            app.state.orchestrator = orchestrator
            readiness_sink.mark_orchestration_available()
            readiness = readiness_holder.snapshot()
            log_readiness = (
                logger.warning
                if readiness.overall is RuntimeOverall.DEGRADED
                else logger.info
            )
            log_readiness(
                "Lazzaro inicializado. readiness=%s verification=%s.",
                readiness.overall.value,
                readiness.verification.value,
            )
            logger.info("Chingaderita jalando con madre 👍🍞")
            startup_complete = True
            try:
                yield
            finally:
                readiness_holder.close_generation(readiness_sink.generation_id)
                app.state.orchestrator = None
                app.state.ai_client = None
                try:
                    logger.info("=== Apagando Lazzaro ===")
                except Exception:
                    pass
    except BaseException as error:
        primary_error = error
        if not startup_complete and isinstance(error, Exception):
            try:
                log_safe_boundary_failure(
                    logger,
                    component="startup",
                    code="AGENT_UNAVAILABLE",
                    error=error,
                )
            except Exception:
                pass
        raise
    finally:
        readiness_holder.close_generation(readiness_sink.generation_id)
        app.state.orchestrator = None
        app.state.ai_client = None
        if primary_error is None and cleanup.first is not None:
            raise cleanup.first


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
    app.state.runtime_capabilities = RuntimeCapabilityHolder()
    install_public_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    logger.info("Iniciando servidor Uvicorn en http://0.0.0.0:8000 ...")
    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
