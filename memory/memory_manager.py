"""
memory_manager.py - Fachada principal del sistema de memoria de LAZZARO.
Orquesta todos los subsistemas (encoder, stores, retriever, summarizer, working memory)
y expone una API simple para el resto del agente.
"""

from typing import Optional, List, Any

from memory.memory_types import (
    EpisodicMemory,
    SemanticMemory,
    MemoryResult,
)
from memory.memory_encoder import MemoryEncoder
from memory.chroma_client import ChromaClient
from memory.episodic_memory import EpisodicMemoryStore
from memory.semantic_memory import SemanticMemoryStore
from memory.memory_retriever import MemoryRetriever
from memory.memory_filters import MemoryFilters
from memory.memory_summarizer import MemorySummarizer
from memory.working_memory import WorkingMemory
from core.logger import get_logger

logger = get_logger(__name__)


class MemoryManager:
    """
    Punto único de entrada para todas las operaciones de memoria.
    Se apoya en inyección de dependencias: recibe un ChromaClient ya configurado.
    """

    def __init__(self, chroma_client: ChromaClient, working_memory_size: int = 20):
        """
        Args:
            chroma_client: Cliente ChromaDB inicializado externamente.
            working_memory_size: Tamaño máximo de la memoria de trabajo.
        """
        # Cliente de base de datos (ya inicializado)
        self.client = chroma_client

        # Encoder compartido
        self.encoder = MemoryEncoder()

        # Stores especializados
        self.episodic_store = EpisodicMemoryStore(self.client)
        self.semantic_store = SemanticMemoryStore(self.client)

        # Recuperador híbrido
        self.retriever = MemoryRetriever(self.episodic_store, self.semantic_store)

        # Filtros (factoría de condiciones where)
        self.filters = MemoryFilters()

        # Summarizer (determinista, para aligerar el prompt)
        self.summarizer = MemorySummarizer()

        # Memoria de trabajo (volátil)
        self.working_memory = WorkingMemory(max_size=working_memory_size)

        logger.info("MemoryManager inicializado correctamente.")

    # ------------------------------------------------------------------
    # Almacenamiento de experiencias
    # ------------------------------------------------------------------

    def guardar_experiencia(
        self,
        tipo_evento: str,
        narrativa: str,
        estado_emocional: Optional[dict[str, float]] = None,
        valencia: float = 0.0,
        impacto_emocional: float = 0.5,
        extra: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Codifica y almacena un nuevo recuerdo episódico.
        Retorna el ID del recuerdo si se guardó, None en caso de error.
        """
        try:
            recuerdo = self.encoder.codificar_episodio(
                tipo_evento=tipo_evento,
                narrativa=narrativa,
                estado_emocional=estado_emocional,
                valencia=valencia,
                impacto_emocional=impacto_emocional,
                extra=extra,
            )
            if self.episodic_store.almacenar(recuerdo):
                logger.debug("Experiencia guardada: %s (id=%s)", tipo_evento, recuerdo.id)
                return recuerdo.id
            else:
                logger.warning("No se pudo guardar la experiencia '%s'.", tipo_evento)
                return None
        except Exception as e:
            logger.error("Error al guardar experiencia: %s", e)
            return None

    def guardar_regla(
        self,
        regla: str,
        tipo_evento: str = "semantica",
        valencia: float = 0.0,
        impacto_emocional: float = 0.5,
        origen: str = "determinista",
        extra: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Almacena una regla o abstracción semántica.
        Retorna el ID o None si falló.
        """
        try:
            recuerdo = self.encoder.codificar_semantica(
                regla=regla,
                tipo_evento=tipo_evento,
                valencia=valencia,
                impacto_emocional=impacto_emocional,
                origen=origen,
                extra=extra,
            )
            if self.semantic_store.almacenar(recuerdo):
                logger.debug("Regla guardada: %s", regla[:60])
                return recuerdo.id
            else:
                logger.warning("No se pudo guardar la regla '%s'.", regla[:60])
                return None
        except Exception as e:
            logger.error("Error al guardar regla: %s", e)
            return None

    # ------------------------------------------------------------------
    # Recuperación de recuerdos
    # ------------------------------------------------------------------

    def buscar_recuerdos(
        self,
        query: str,
        limite: int = 5,
        filtros_episodicos: Optional[dict] = None,
        filtros_semanticos: Optional[dict] = None,
    ) -> List[MemoryResult]:
        """
        Busca recuerdos combinados (episódicos + semánticos) ordenados por relevancia.
        """
        try:
            return self.retriever.buscar(
                query=query,
                limite=limite,
                where_episodica=filtros_episodicos,
                where_semantica=filtros_semanticos,
            )
        except Exception as e:
            logger.error("Error en buscar_recuerdos: %s", e)
            return []

    def obtener_contexto_memoria(
        self,
        query: str,
        limite: int = 5,
        max_caracteres: int = 600,
    ) -> str:
        """
        Recupera y resume recuerdos relevantes para inyectar directamente en el prompt.
        """
        try:
            recuerdos = self.buscar_recuerdos(query=query, limite=limite)
            return self.summarizer.resumir(recuerdos, max_caracteres)
        except Exception as e:
            logger.error("Error al obtener contexto de memoria: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Memoria de trabajo (conveniencia)
    # ------------------------------------------------------------------

    def agregar_accion(self, accion: str, resultado: str = "pendiente", detalles: Optional[dict] = None):
        """Registra una acción reciente en la memoria de trabajo."""
        self.working_memory.agregar(accion, resultado, detalles)

    def obtener_ultimas_acciones(self, n: int = 5) -> str:
        """Devuelve un resumen textual de las últimas acciones."""
        return self.working_memory.resumen()