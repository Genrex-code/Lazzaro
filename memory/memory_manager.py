"""
memory_manager.py - Fachada principal del sistema de memoria de LAZZARO.
Orquesta todos los subsistemas y expone una API simple para el resto del agente.
"""

from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha1
from typing import Any, Optional

from core.logger import get_logger
from memory.chroma_client import ChromaClient
from memory.conversation_memory import ConversationMemory
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_encoder import MemoryEncoder
from memory.memory_filters import MemoryFilters
from memory.memory_retriever import MemoryRetriever
from memory.memory_summarizer import MemorySummarizer
from memory.memory_types import MemoryResult
from memory.semantic_memory import SemanticMemoryStore
from memory.working_memory import WorkingMemory

logger = get_logger(__name__)


class MemoryManager:
    """
    Punto unico de entrada para todas las operaciones de memoria.
    Se apoya en inyeccion de dependencias: recibe un ChromaClient ya configurado.
    """

    TIPOS_OBSERVACIONALES = {
        "observacion_visual",
        "vision_periodica",
        "percepcion_entorno",
        "lectura_sensorial",
        "inicio_simulacion",
        "actualizacion_estado",
    }
    TIPOS_CRITICOS = {
        "alerta_dolor_fisico",
        "error_critico",
        "fracaso",
        "loop_detectado",
        "sobrecarga",
    }

    def __init__(
        self,
        chroma_client: ChromaClient,
        working_memory_size: int = 20,
        conversation_memory_size: int = 12,
        consolidacion_umbral: int = 30,
        consolidacion_antiguedad_minutos: int = 5,
    ):
        self.client = chroma_client
        self.encoder = MemoryEncoder()
        self.episodic_store = EpisodicMemoryStore(self.client)
        self.semantic_store = SemanticMemoryStore(self.client)
        self.retriever = MemoryRetriever(self.episodic_store, self.semantic_store)
        self.filters = MemoryFilters()
        self.summarizer = MemorySummarizer()
        self.working_memory = WorkingMemory(max_size=working_memory_size)
        self.conversation_memory = ConversationMemory(max_turns=conversation_memory_size)
        self.consolidacion_umbral = consolidacion_umbral
        self.consolidacion_antiguedad_minutos = consolidacion_antiguedad_minutos
        self.revision_consolidacion_cada = max(5, min(10, consolidacion_umbral))
        self._observaciones_desde_revision = 0
        logger.info("MemoryManager inicializado correctamente.")

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
        Codifica y almacena un nuevo recuerdo episodico.
        Retorna el ID del recuerdo si se guardo, None en caso de error.
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
                self._programar_consolidacion_si_aplica(tipo_evento, extra, narrativa)
                return recuerdo.id

            logger.warning("No se pudo guardar la experiencia '%s'.", tipo_evento)
            return None
        except Exception as e:
            logger.error("Error al guardar experiencia: %s", e, exc_info=True)
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
        """Almacena una regla o abstraccion semantica."""
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

            logger.warning("No se pudo guardar la regla '%s'.", regla[:60])
            return None
        except Exception as e:
            logger.error("Error al guardar regla: %s", e, exc_info=True)
            return None

    def buscar_recuerdos(
        self,
        query: str,
        limite: int = 5,
        filtros_episodicos: Optional[dict] = None,
        filtros_semanticos: Optional[dict] = None,
    ) -> list[MemoryResult]:
        """Busca recuerdos combinados ordenados por relevancia."""
        try:
            return self.retriever.buscar(
                query=query,
                limite=limite,
                where_episodica=filtros_episodicos,
                where_semantica=filtros_semanticos,
            )
        except Exception as e:
            logger.error("Error en buscar_recuerdos: %s", e, exc_info=True)
            return []

    def obtener_contexto_memoria(
        self,
        query: str,
        limite: int = 5,
        max_caracteres: int = 600,
    ) -> str:
        """Recupera y resume recuerdos relevantes para inyectar en el prompt."""
        try:
            recuerdos = self.buscar_recuerdos(query=query, limite=limite)
            return self.summarizer.resumir(recuerdos, max_caracteres)
        except Exception as e:
            logger.error("Error al obtener contexto de memoria: %s", e, exc_info=True)
            return ""

    def agregar_accion(self, accion: str, resultado: str = "pendiente", detalles: Optional[dict] = None):
        """Registra una accion reciente en la memoria de trabajo."""
        if accion == "none" and resultado == "sin_accion":
            logger.debug("Memoria de trabajo ignora no-op pasivo.")
            return
        self.working_memory.agregar(accion, resultado, detalles)

    def obtener_ultimas_acciones(self, n: int = 5) -> str:
        """Devuelve un resumen textual de las ultimas acciones."""
        return self.working_memory.resumen()

    def registrar_turno_conversacional(
        self,
        mensaje_usuario: str,
        respuesta_lazzaro: str,
        pensamiento: str = "",
        emocion: str = "Fcl_ALL_Neutral",
        route: str = "",
        intent: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Registra un intercambio de dialogo reciente sin persistirlo en Chroma."""
        self.conversation_memory.registrar_turno(
            usuario=mensaje_usuario,
            lazzaro=respuesta_lazzaro,
            pensamiento=pensamiento,
            emocion=emocion,
            route=route,
            intent=intent,
            metadata=metadata,
        )

    def obtener_contexto_conversacional(self, n: int = 6) -> str:
        """Devuelve los ultimos turnos conversacionales para continuidad inmediata."""
        return self.conversation_memory.resumen(n=n)

    def close(self) -> None:
        """Cierra recursos persistentes de memoria."""
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def _programar_consolidacion_si_aplica(
        self,
        tipo_evento: str,
        extra: Optional[dict[str, Any]],
        narrativa: str,
    ) -> None:
        if tipo_evento in self.TIPOS_CRITICOS:
            return
        if not self._es_memoria_observacional(tipo_evento, extra or {}, narrativa):
            return

        self._observaciones_desde_revision += 1
        if self._observaciones_desde_revision < self.revision_consolidacion_cada:
            return

        self._observaciones_desde_revision = 0
        self.consolidar_memorias_si_hace_falta()

    def consolidar_memorias_si_hace_falta(self) -> None:
        """
        Resume recuerdos observacionales antiguos y los reemplaza por una regla semantica.
        Evita llenar el contexto con ruido de percepcion repetitiva.
        """
        candidatos = self._obtener_candidatos_consolidacion()
        if len(candidatos) < self.consolidacion_umbral:
            return

        ids = [candidato["id"] for candidato in candidatos]
        lote_consolidacion = self._calcular_lote_consolidacion(ids)

        if self._ya_existe_consolidacion_lote(lote_consolidacion):
            if self.client.eliminar(ids):
                logger.info(
                    "Se detecto un lote ya consolidado y se limpiaron %d memorias observacionales duplicadas.",
                    len(ids),
                )
            else:
                logger.warning(
                    "El lote %s ya estaba consolidado, pero no se pudieron eliminar sus episodios originales.",
                    lote_consolidacion,
                )
            return

        regla = self._construir_regla_consolidacion(candidatos)
        if not regla:
            return

        guardada = self.guardar_regla(
            regla=regla,
            tipo_evento="consolidacion_observacional",
            valencia=0.0,
            impacto_emocional=0.35,
            origen="determinista",
            extra={
                "consolidacion_de": len(candidatos),
                "tipo_fuente": "observacional",
                "lote_consolidacion": lote_consolidacion,
            },
        )
        if not guardada:
            return

        if self.client.eliminar(ids):
            logger.info(
                "Consolidadas %d memorias observacionales viejas en una regla semantica.",
                len(candidatos),
            )
        else:
            logger.warning(
                "La regla del lote %s se guardo, pero no fue posible eliminar los episodios originales.",
                lote_consolidacion,
            )

    def _obtener_candidatos_consolidacion(self) -> list[dict[str, Any]]:
        """Recupera recuerdos episodicos de bajo impacto aptos para consolidacion."""
        registros = self.client.listar(where={"tipo": "episodico"})
        if not registros:
            return []

        ids = registros.get("ids") or []
        docs = registros.get("documents") or []
        metas = registros.get("metadatas") or []
        if not ids:
            return []

        umbral_antiguedad = datetime.utcnow() - timedelta(minutes=self.consolidacion_antiguedad_minutos)
        candidatos: list[dict[str, Any]] = []
        for idx, doc_id in enumerate(ids):
            meta = metas[idx] if idx < len(metas) else {}
            doc = docs[idx] if idx < len(docs) else ""
            if not isinstance(meta, dict):
                continue

            tipo_evento = str(meta.get("tipo_evento", ""))
            if not self._es_memoria_observacional(tipo_evento, meta, doc):
                continue
            if tipo_evento in self.TIPOS_CRITICOS:
                continue

            impacto = float(meta.get("impacto_emocional", 0.5))
            if impacto >= 0.6:
                continue

            ts_raw = meta.get("timestamp")
            try:
                ts = datetime.fromisoformat(str(ts_raw)) if ts_raw else datetime.utcnow()
            except Exception:
                ts = datetime.utcnow()
            if ts > umbral_antiguedad:
                continue

            candidatos.append(
                {
                    "id": doc_id,
                    "documento": doc,
                    "tipo_evento": tipo_evento,
                    "metadata": meta,
                    "timestamp": ts,
                }
            )

        candidatos.sort(key=lambda candidato: candidato["timestamp"])
        return candidatos[: self.consolidacion_umbral]

    def _ya_existe_consolidacion_lote(self, lote_consolidacion: str) -> bool:
        registros = self.client.listar(where={"lote_consolidacion": lote_consolidacion}, limite=1)
        if not registros:
            return False
        return bool(registros.get("ids"))

    @staticmethod
    def _calcular_lote_consolidacion(ids: list[str]) -> str:
        lote = "|".join(sorted(ids))
        return sha1(lote.encode("utf-8")).hexdigest()[:16]

    def _es_memoria_observacional(self, tipo_evento: str, metadata: dict[str, Any], documento: str) -> bool:
        """Decide si una memoria es ruido perceptivo y conviene compactarla."""
        if tipo_evento in self.TIPOS_OBSERVACIONALES:
            return True
        if tipo_evento in self.TIPOS_CRITICOS or tipo_evento in {"resultado_accion", "comando_usuario"}:
            return False

        fuente = str(metadata.get("fuente", "")).lower()
        canal = str(metadata.get("canal", "")).lower()
        if fuente in {"vision", "percepcion", "sensorial"} or canal in {"vision", "percepcion"}:
            return True

        doc_norm = documento.lower()
        pistas = ("veo", "observ", "sensor", "vision", "entorno", "alrededor", "objeto mas cercano")
        return any(pista in doc_norm for pista in pistas)

    def _construir_regla_consolidacion(self, candidatos: list[dict[str, Any]]) -> str:
        """Resume de forma determinista un lote de observaciones viejas."""
        if not candidatos:
            return ""

        tipos = Counter(candidato["tipo_evento"] or "observacion" for candidato in candidatos)
        muestras = []
        for candidato in candidatos[:3]:
            doc = str(candidato["documento"]).strip().replace("\n", " ")
            if len(doc) > 90:
                doc = doc[:90] + "..."
            muestras.append(doc)

        tipo_principal, frecuencia = tipos.most_common(1)[0]
        return (
            f"Patron consolidado de percepcion: se registraron {len(candidatos)} observaciones antiguas "
            f"de baja prioridad; el tipo mas frecuente fue '{tipo_principal}' ({frecuencia} veces). "
            f"Muestras representativas: {' | '.join(muestras)}"
        )
