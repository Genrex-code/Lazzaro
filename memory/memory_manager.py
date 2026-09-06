"""
memory_manager.py - Fachada principal del sistema de memoria de LAZZARO.
Orquesta todos los subsistemas y expone una API simple para el resto del agente.
"""

from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha1, sha256
import json
import re
import threading
from typing import Any, Literal, Optional

from ai_engine.learned_alias_candidates import LearnedAliasCandidate, alias_key
from core.logger import get_logger
from memory.chroma_client import ChromaClient
from memory.conversation_memory import ConversationMemory, PublicConversationTurn
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_decay import MemoryDecay
from memory.memory_encoder import MemoryEncoder
from memory.memory_filters import MemoryFilters
from memory.memory_retriever import MemoryRetriever
from memory.memory_summarizer import MemorySummarizer
from memory.memory_types import MemoryResult
from memory.semantic_memory import SemanticMemoryStore
from memory.working_memory import WorkingMemory

logger = get_logger(__name__)


@dataclass(frozen=True)
class MemoryContextResult:
    """Contexto resumido y metadata ordenada de la misma recuperación."""

    texto: str = ""
    retrieved_ids: tuple[str, ...] = ()
    scores: tuple[float, ...] = ()


@dataclass(frozen=True)
class ManualConversationSaveResult:
    """Resultado explícito del guardado durable de una fotografía conversacional."""

    status: Literal["saved", "already_saved", "empty", "error"]
    memory_id: Optional[str] = None
    fingerprint: str = ""
    turn_count: int = 0
    started_at: str = ""
    ended_at: str = ""
    error: Optional[str] = None

    @property
    def persisted(self) -> bool:
        return self.status in {"saved", "already_saved"}


@dataclass(frozen=True)
class _ManualConversationLookup:
    """Resultado triestado para no confundir ausencia con fallo de lectura."""

    status: Literal["found", "missing", "error"]
    record: Optional[tuple[str, dict[str, Any]]] = None
    error: Optional[str] = None


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
    MANUAL_SAVE_MAX_TURNS = 12
    MANUAL_SAVE_MAX_TEXT_CHARS = 700
    MANUAL_SAVE_MAX_DOCUMENT_CHARS = 20_000
    MANUAL_SAVE_DIGEST_CHARS = 470
    MANUAL_SAVE_MAX_TRANSACTION_HASHES = 128
    MANUAL_SAVE_MAX_PENDING_SNAPSHOTS = 2000

    def __init__(
        self,
        chroma_client: ChromaClient,
        working_memory_size: int = 20,
        conversation_memory_size: int = 12,
        consolidacion_umbral: int = 30,
        consolidacion_antiguedad_minutos: int = 5,
        consolidacion_importancia_maxima: float = 0.25,
    ):
        self.client = chroma_client
        self.encoder = MemoryEncoder()
        self.episodic_store = EpisodicMemoryStore(self.client)
        self.semantic_store = SemanticMemoryStore(self.client)
        self.retriever = MemoryRetriever(self.episodic_store, self.semantic_store)
        self.filters = MemoryFilters()
        self.decay = MemoryDecay()
        self.summarizer = MemorySummarizer()
        self.working_memory = WorkingMemory(max_size=working_memory_size)
        self.conversation_memory = ConversationMemory(max_turns=conversation_memory_size)
        self.consolidacion_umbral = consolidacion_umbral
        self.consolidacion_antiguedad_minutos = consolidacion_antiguedad_minutos
        self.consolidacion_importancia_maxima = consolidacion_importancia_maxima
        self.revision_consolidacion_cada = max(5, min(10, consolidacion_umbral))
        self._observaciones_desde_revision = 0
        self._hechos_conversacionales_guardados: set[str] = set()
        self._alias_aprendidos_guardados: set[str] = set()
        self._memorias_afectivas_guardadas: set[str] = set()
        self._guardado_conversacional_lock = threading.RLock()
        self._fotografias_conversacionales_pendientes: OrderedDict[
            str,
            tuple[PublicConversationTurn, ...],
        ] = OrderedDict()
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
                logger.debug(
                    "Regla guardada: tipo_evento=%s id=%s",
                    tipo_evento,
                    recuerdo.id,
                )
                return recuerdo.id

            logger.warning(
                "No se pudo guardar la regla: tipo_evento=%s.",
                tipo_evento,
            )
            return None
        except Exception as e:
            logger.error(
                "Error al guardar regla: tipo_evento=%s error_type=%s",
                tipo_evento,
                type(e).__name__,
            )
            return None

    def guardar_alias_aprendido(
        self,
        candidato: LearnedAliasCandidate | dict[str, Any],
    ) -> Optional[str]:
        """Aprueba y persiste un alias aprendido si no existe ya."""
        candidate = (
            candidato
            if isinstance(candidato, LearnedAliasCandidate)
            else LearnedAliasCandidate.from_dict(candidato)
        )
        if candidate is None:
            return None

        key = alias_key(candidate.object_id, candidate.human_concept)
        if key in self._alias_aprendidos_guardados:
            logger.debug("Alias aprendido duplicado en cache: %s", key)
            return None
        if self._alias_aprendido_ya_persistido(candidate):
            self._alias_aprendidos_guardados.add(key)
            logger.debug("Alias aprendido ya existia en memoria semantica: %s", key)
            return None

        regla = (
            f"Alias aprendido: '{candidate.human_concept}' se refiere a "
            f"'{candidate.object_id}'."
        )
        metadata = {
            "object_id": candidate.object_id,
            "human_concept": candidate.human_concept,
            "confidence": candidate.confidence,
            "source": candidate.source,
            "evidence": candidate.evidence,
            "action": candidate.action or "",
            "target_display_name": candidate.target_display_name or "",
            "legacy_nombre_objeto": candidate.legacy_nombre_objeto or "",
            "approved": True,
        }
        recuerdo_id = self.guardar_regla(
            regla=regla,
            tipo_evento="alias_aprendido",
            valencia=0.1,
            impacto_emocional=0.35,
            origen=candidate.source,
            extra=metadata,
        )
        if recuerdo_id:
            self._alias_aprendidos_guardados.add(key)
        return recuerdo_id

    def obtener_aliases_aprendidos(
        self,
        object_ids: list[str] | None = None,
        limite: int = 200,
    ) -> dict[str, list[str]]:
        """Lee aliases aprendidos aprobados para uso rapido del resolver fuzzy."""
        try:
            where = self.client.combinar_where(
                {"tipo_evento": "alias_aprendido"},
                {"tipo": "semantico"},
            )
            registros = self.client.listar(where=where, limite=limite)
            if not registros:
                return {}

            allowed = set(object_ids or [])
            metadatas = registros.get("metadatas") or []
            aliases: dict[str, list[str]] = {}
            for meta in metadatas:
                if not isinstance(meta, dict):
                    continue
                object_id = str(meta.get("object_id") or "").strip()
                human_concept = str(meta.get("human_concept") or "").strip()
                approved = meta.get("approved")
                if not object_id or not human_concept or approved is not True:
                    continue
                if allowed and object_id not in allowed:
                    continue
                bucket = aliases.setdefault(object_id, [])
                if human_concept not in bucket:
                    bucket.append(human_concept)
                    self._alias_aprendidos_guardados.add(alias_key(object_id, human_concept))
            return aliases
        except Exception as e:
            logger.error("Error al obtener aliases aprendidos: %s", e, exc_info=True)
            return {}

    def _alias_aprendido_ya_persistido(self, candidate: LearnedAliasCandidate) -> bool:
        aliases = self.obtener_aliases_aprendidos([candidate.object_id])
        conceptos = aliases.get(candidate.object_id, [])
        normalized_key = alias_key(candidate.object_id, candidate.human_concept)
        return any(alias_key(candidate.object_id, concepto) == normalized_key for concepto in conceptos)

    def guardar_memoria_afectiva_espacial(
        self,
        *,
        object_id: str | None = None,
        object_class: str = "",
        affect: str,
        weight: float,
        source: str = "event",
        confirmed: bool,
    ) -> Optional[str]:
        """Guarda asociacion afectiva espacial como metadata, nunca como comando."""
        if type(confirmed) is not bool:
            logger.warning("Memoria afectiva espacial con confirmed no booleano.")
            return None

        normalized_object_id = str(object_id or "").strip()
        normalized_object_class = _normalize_memory_token(object_class)
        normalized_affect = _normalize_memory_token(affect)
        normalized_source = _normalize_memory_token(source) or "event"
        clamped_weight = _clamp_float(weight, 0.0, 1.0)

        if not normalized_object_id and not normalized_object_class:
            logger.warning("Memoria afectiva espacial sin object_id ni object_class.")
            return None
        if not normalized_affect:
            logger.warning("Memoria afectiva espacial sin affect.")
            return None

        key = _affective_memory_key(
            normalized_object_id,
            normalized_object_class,
            normalized_affect,
            normalized_source,
        )
        if key in self._memorias_afectivas_guardadas:
            logger.debug("Memoria afectiva espacial duplicada en cache: %s", key)
            return None
        if self._memoria_afectiva_ya_persistida(
            object_id=normalized_object_id,
            object_class=normalized_object_class,
            affect=normalized_affect,
            source=normalized_source,
        ):
            self._memorias_afectivas_guardadas.add(key)
            logger.debug("Memoria afectiva espacial ya existia: %s", key)
            return None

        subject = normalized_object_id or normalized_object_class
        regla = (
            f"Memoria afectiva espacial: '{subject}' se asocia con "
            f"'{normalized_affect}' (peso {clamped_weight:.2f})."
        )
        metadata = {
            "object_id": normalized_object_id,
            "object_class": normalized_object_class,
            "affect": normalized_affect,
            "weight": clamped_weight,
            "source": normalized_source,
            "confirmed": confirmed,
        }
        recuerdo_id = self.guardar_regla(
            regla=regla,
            tipo_evento="memoria_afectiva_espacial",
            valencia=_affective_valence(normalized_affect),
            impacto_emocional=max(0.2, clamped_weight),
            origen=normalized_source,
            extra=metadata,
        )
        if recuerdo_id:
            self._memorias_afectivas_guardadas.add(key)
        return recuerdo_id

    def obtener_memorias_afectivas_espaciales(
        self,
        object_refs: list[Any] | None = None,
        limite: int = 200,
    ) -> dict[str, list[dict[str, Any]]]:
        """Lee asociaciones afectivas aprobadas para sesgo acotado del resolver."""
        try:
            allowed_ids, allowed_classes = _object_refs_to_filters(object_refs)
            where = self.client.combinar_where(
                {"tipo_evento": "memoria_afectiva_espacial"},
                {"tipo": "semantico"},
            )
            registros = self.client.listar(where=where, limite=limite)
            if not registros:
                return {}

            metadatas = registros.get("metadatas") or []
            hints: dict[str, list[dict[str, Any]]] = {}
            for meta in metadatas:
                if not isinstance(meta, dict):
                    continue
                object_id = str(meta.get("object_id") or "").strip()
                object_class = _normalize_memory_token(meta.get("object_class"))
                affect = _normalize_memory_token(meta.get("affect"))
                source = _normalize_memory_token(meta.get("source")) or "event"
                confirmed = meta.get("confirmed")
                weight = _clamp_float(meta.get("weight", 0.0), 0.0, 1.0)
                if not affect or confirmed is not True or weight <= 0.0:
                    continue

                key = ""
                if object_id:
                    if allowed_ids and object_id not in allowed_ids:
                        continue
                    key = object_id
                elif object_class:
                    if allowed_classes and object_class not in allowed_classes:
                        continue
                    key = f"class:{object_class}"
                if not key:
                    continue

                hint = {
                    "object_id": object_id,
                    "object_class": object_class,
                    "affect": affect,
                    "weight": weight,
                    "source": source,
                    "confirmed": confirmed,
                }
                bucket = hints.setdefault(key, [])
                if hint not in bucket:
                    bucket.append(hint)
                    self._memorias_afectivas_guardadas.add(
                        _affective_memory_key(object_id, object_class, affect, source)
                    )
            return hints
        except Exception as e:
            logger.error("Error al obtener memorias afectivas espaciales: %s", e, exc_info=True)
            return {}

    def _memoria_afectiva_ya_persistida(
        self,
        *,
        object_id: str,
        object_class: str,
        affect: str,
        source: str,
    ) -> bool:
        hints = self.obtener_memorias_afectivas_espaciales(None)
        target_key = _affective_memory_key(object_id, object_class, affect, source)
        for entries in hints.values():
            for entry in entries:
                if _affective_memory_key(
                    str(entry.get("object_id") or "").strip(),
                    _normalize_memory_token(entry.get("object_class")),
                    _normalize_memory_token(entry.get("affect")),
                    _normalize_memory_token(entry.get("source")) or "event",
                ) == target_key:
                    return True
        return False

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
            return self.obtener_contexto_memoria_con_metadata(
                query=query,
                limite=limite,
                max_caracteres=max_caracteres,
            ).texto
        except Exception as e:
            logger.error("Error al obtener contexto de memoria: %s", e, exc_info=True)
            return ""

    def obtener_contexto_memoria_con_metadata(
        self,
        query: str,
        limite: int = 5,
        max_caracteres: int = 600,
    ) -> MemoryContextResult:
        """Resume una sola recuperación y expone únicamente IDs/scores alineados."""
        try:
            recuerdos = self.buscar_recuerdos(query=query, limite=limite)
        except Exception as e:
            logger.error("Error al obtener contexto de memoria: %s", e, exc_info=True)
            return MemoryContextResult()

        try:
            texto = str(self.summarizer.resumir(recuerdos, max_caracteres) or "")
        except Exception as e:
            logger.error("Error al resumir contexto de memoria: %s", e, exc_info=True)
            texto = ""

        try:
            pares = tuple(
                (
                    str(resultado.recuerdo.id),
                    float(resultado.metadata_score),
                )
                for resultado in recuerdos
            )
        except Exception as e:
            logger.warning(
                "No se pudo extraer metadata de la recuperación; se conserva el contexto. error_type=%s",
                type(e).__name__,
            )
            return MemoryContextResult(texto=texto)

        return MemoryContextResult(
            texto=texto,
            retrieved_ids=tuple(memory_id for memory_id, _ in pares),
            scores=tuple(score for _, score in pares),
        )

    def obtener_contexto_relacional(
        self,
        query: str,
        limite: int = 4,
        max_caracteres: int = 500,
    ) -> str:
        """
        Recupera memorias relacionales persistentes antes de los recuerdos generales.

        Estas reglas vienen de hechos conversacionales selectivos (apodos,
        identidad del usuario/creador y preferencias claras). Se leen solo
        cuando el evento ya sobrevivio al triage, igual que el resto del
        contexto de memoria.
        """
        try:
            query_limpia = str(query or "").strip()
            if not query_limpia:
                query_limpia = "identidad relacion usuario apodo nombre preferencias creador"

            recuerdos = self.semantic_store.buscar(
                query=query_limpia,
                n=max(1, limite * 3),
                where={"tipo_evento": "memoria_relacional"},
            )
            if not recuerdos:
                return ""

            recuerdos_vigentes = self._seleccionar_memorias_relacionales_vigentes(recuerdos)
            lineas = ["MEMORIA RELACIONAL PERSISTENTE:"]
            for resultado in recuerdos_vigentes[:limite]:
                regla = str(getattr(resultado.recuerdo, "regla", "") or "").strip()
                if not regla:
                    continue
                lineas.append(f"- {self._recortar_texto(regla, 180)}")

            if len(lineas) == 1:
                return ""
            return self._recortar_texto("\n".join(lineas), max_caracteres)
        except Exception as e:
            logger.error("Error al obtener memoria relacional: %s", e, exc_info=True)
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

    def obtener_cantidad_memoria_trabajo(self) -> int:
        """Cuenta el snapshot actual de la cola volátil sin exponer su contenido."""
        try:
            return len(self.working_memory.obtener_todas())
        except Exception as e:
            logger.warning(
                "No se pudo contar la memoria de trabajo. error_type=%s",
                type(e).__name__,
            )
            return 0

    def registrar_turno_conversacional(
        self,
        mensaje_usuario: str,
        respuesta_lazzaro: str,
        pensamiento: str = "",
        route: str = "",
        intent: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Registra un intercambio de dialogo reciente y persiste hechos relacionales claros."""
        self.conversation_memory.registrar_turno(
            usuario=mensaje_usuario,
            lazzaro=respuesta_lazzaro,
            pensamiento=pensamiento,
            route=route,
            intent=intent,
            metadata=metadata,
        )
        self._registrar_hechos_conversacionales(
            mensaje_usuario=mensaje_usuario,
            respuesta_lazzaro=respuesta_lazzaro,
            route=route,
            intent=intent,
            metadata=metadata,
        )

    def obtener_contexto_conversacional(self, n: int = 6) -> str:
        """Devuelve los ultimos turnos conversacionales para continuidad inmediata."""
        return self.conversation_memory.resumen(n=n)

    def guardar_fotografia_conversacional(
        self,
        id_transaccion: str,
    ) -> ManualConversationSaveResult:
        """Ejecuta un intento manual y emite exactamente un evento estructurado."""
        try:
            resultado = self._guardar_fotografia_conversacional(id_transaccion)
        except Exception as exc:
            try:
                self._registrar_intento_guardado_conversacional(
                    id_transaccion=id_transaccion,
                    resultado=ManualConversationSaveResult(status="error"),
                    error_type=type(exc).__name__,
                )
            except Exception:
                pass
            raise

        try:
            self._registrar_intento_guardado_conversacional(
                id_transaccion=id_transaccion,
                resultado=resultado,
            )
        except Exception:
            pass
        return resultado

    def _guardar_fotografia_conversacional(
        self,
        id_transaccion: str,
    ) -> ManualConversationSaveResult:
        """
        Persiste una copia pública de toda la RAM sin vaciarla ni consolidarla.

        La transacción y la huella se verifican en Chroma para conservar
        idempotencia después de reinicios, no solo durante el proceso actual.
        """
        transaccion = str(id_transaccion or "").strip()
        if not transaccion:
            return ManualConversationSaveResult(
                status="error",
                error="El guardado manual requiere un id_transaccion.",
            )

        lock = getattr(self, "_guardado_conversacional_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._guardado_conversacional_lock = lock

        with lock:
            snapshot = self._obtener_o_capturar_fotografia_pendiente(transaccion)
            existente_transaccion = self._buscar_guardado_por_transaccion(transaccion)
            if existente_transaccion.status == "error":
                return ManualConversationSaveResult(
                    status="error",
                    error=existente_transaccion.error
                    or "No se pudo comprobar la idempotencia del guardado.",
                )
            if existente_transaccion.status == "found" and existente_transaccion.record:
                self._olvidar_fotografia_pendiente(transaccion)
                return self._resultado_guardado_existente(existente_transaccion.record)

            if not snapshot:
                self._olvidar_fotografia_pendiente(transaccion)
                return ManualConversationSaveResult(status="empty")

            fingerprint = self._huella_fotografia_conversacional(snapshot)
            existente_huella = self._buscar_guardado_conversacional(
                "huella_guardado_conversacional",
                fingerprint,
            )
            if existente_huella.status == "error":
                return ManualConversationSaveResult(
                    status="error",
                    fingerprint=fingerprint,
                    turn_count=len(snapshot),
                    error=existente_huella.error
                    or "No se pudo comprobar si la conversación ya estaba guardada.",
                )
            if existente_huella.status == "found" and existente_huella.record:
                resultado_existente = self._resultado_guardado_existente(
                    existente_huella.record,
                    fallback_fingerprint=fingerprint,
                    fallback_turn_count=len(snapshot),
                )
                recibo_asociado = self._asociar_transaccion_a_guardado(
                    existente_huella.record,
                    transaccion,
                )
                if not recibo_asociado:
                    return ManualConversationSaveResult(
                        status="error",
                        memory_id=resultado_existente.memory_id,
                        fingerprint=resultado_existente.fingerprint,
                        turn_count=resultado_existente.turn_count,
                        started_at=resultado_existente.started_at,
                        ended_at=resultado_existente.ended_at,
                        error=(
                            "La conversación ya existe, pero Chroma no confirmó "
                            "el recibo durable de esta transacción."
                        ),
                    )
                self._olvidar_fotografia_pendiente(transaccion)
                return resultado_existente

            documento = self._documento_fotografia_conversacional(snapshot)
            inicio = self._timestamp_utc(snapshot[0].timestamp)
            fin = self._timestamp_utc(snapshot[-1].timestamp)
            if len(documento) > self.MANUAL_SAVE_MAX_DOCUMENT_CHARS:
                return ManualConversationSaveResult(
                    status="error",
                    fingerprint=fingerprint,
                    turn_count=len(snapshot),
                    started_at=inicio,
                    ended_at=fin,
                    error=(
                        "La fotografía conversacional excede el límite "
                        f"de {self.MANUAL_SAVE_MAX_DOCUMENT_CHARS} caracteres."
                    ),
                )

            error_guardado: Optional[str] = None
            memory_id: Optional[str] = None
            transaction_hash = self._huella_transaccion_guardado(transaccion)

            try:
                memory_id = self.guardar_regla(
                    regla=documento,
                    tipo_evento="memoria_conversacional_manual",
                    valencia=0.25,
                    impacto_emocional=0.8,
                    origen="guardado_manual_conversacion",
                    extra={
                        "version_esquema": 1,
                        "fuente": "guardado_manual_ram_conversacional",
                        "huella_guardado_conversacional": fingerprint,
                        "id_transaccion_guardado": transaccion,
                        "transacciones_guardado_hashes": transaction_hash,
                        "cantidad_turnos": len(snapshot),
                        "inicio_utc": inicio,
                        "fin_utc": fin,
                    },
                )
            except Exception as exc:
                error_guardado = str(exc)
                logger.error(
                    "Error inesperado guardando fotografia conversacional: "
                    "error_type=%s",
                    type(exc).__name__,
                )

            if memory_id:
                self._olvidar_fotografia_pendiente(transaccion)
                return ManualConversationSaveResult(
                    status="saved",
                    memory_id=memory_id,
                    fingerprint=fingerprint,
                    turn_count=len(snapshot),
                    started_at=inicio,
                    ended_at=fin,
                )

            # Cubre una escritura confirmada por el backend seguida de timeout/error.
            existente_despues = self._buscar_guardado_conversacional(
                "huella_guardado_conversacional",
                fingerprint,
            )
            if existente_despues.status == "found" and existente_despues.record:
                self._olvidar_fotografia_pendiente(transaccion)
                return self._resultado_guardado_existente(
                    existente_despues.record,
                    fallback_fingerprint=fingerprint,
                    fallback_turn_count=len(snapshot),
                )
            if existente_despues.status == "error" and not error_guardado:
                error_guardado = existente_despues.error

            return ManualConversationSaveResult(
                status="error",
                fingerprint=fingerprint,
                turn_count=len(snapshot),
                started_at=inicio,
                ended_at=fin,
                error=error_guardado or "La memoria persistente no confirmó la escritura.",
            )

    @classmethod
    def _registrar_intento_guardado_conversacional(
        cls,
        id_transaccion: str,
        resultado: ManualConversationSaveResult,
        error_type: str = "",
    ) -> None:
        """Registra solo telemetría segura; nunca incluye contenido conversacional."""
        status = str(resultado.status or "error").strip().lower()
        if status not in {"saved", "already_saved", "empty", "error"}:
            status = "error"

        try:
            turn_count = max(0, int(resultado.turn_count))
        except (TypeError, ValueError):
            turn_count = 0

        transaction_id = cls._valor_seguro_log(id_transaccion, max_chars=160)
        fingerprint = cls._valor_seguro_log(
            str(resultado.fingerprint or "")[:12],
            max_chars=12,
        )
        persistent_write_confirmed = "true" if resultado.persisted else "false"

        campos = (
            "event=conversation_memory_save "
            f"transaction_id={transaction_id} "
            f"status={status} "
            f"turn_count={turn_count} "
            f"fingerprint={fingerprint} "
            f"persistent_write_confirmed={persistent_write_confirmed}"
        )
        if status == "error":
            tipo_error = cls._valor_seguro_log(
                error_type or "ManualConversationSaveError",
                max_chars=96,
            )
            campos += f" error_type={tipo_error}"

        try:
            logger.info(campos)
        except Exception:
            # La telemetría nunca debe cambiar el resultado funcional del guardado.
            return

    @staticmethod
    def _valor_seguro_log(valor: Any, max_chars: int) -> str:
        """Convierte un identificador técnico en un único token sin controles."""
        texto = str(valor or "").strip()
        texto = re.sub(r"[\x00-\x20\x7f]+", "_", texto)
        return texto[: max(0, max_chars)]

    def _obtener_o_capturar_fotografia_pendiente(
        self,
        id_transaccion: str,
    ) -> tuple[PublicConversationTurn, ...]:
        """Fija el snapshot del primer intento para que un retry no capture turnos nuevos."""
        pendientes = getattr(self, "_fotografias_conversacionales_pendientes", None)
        if not isinstance(pendientes, OrderedDict):
            pendientes = OrderedDict()
            self._fotografias_conversacionales_pendientes = pendientes

        if id_transaccion in pendientes:
            pendientes.move_to_end(id_transaccion)
            return pendientes[id_transaccion]

        snapshot = self.conversation_memory.snapshot_publico(
            max_turns=self.MANUAL_SAVE_MAX_TURNS,
            max_text_chars=self.MANUAL_SAVE_MAX_TEXT_CHARS,
        )
        pendientes[id_transaccion] = snapshot
        pendientes.move_to_end(id_transaccion)
        while len(pendientes) > self.MANUAL_SAVE_MAX_PENDING_SNAPSHOTS:
            pendientes.popitem(last=False)
        return snapshot

    def _olvidar_fotografia_pendiente(self, id_transaccion: str) -> None:
        pendientes = getattr(self, "_fotografias_conversacionales_pendientes", None)
        if isinstance(pendientes, OrderedDict):
            pendientes.pop(id_transaccion, None)

    def _buscar_guardado_conversacional(
        self,
        clave: str,
        valor: str,
    ) -> _ManualConversationLookup:
        """Busca una memoria manual por metadata escalar compatible con Chroma."""
        client = getattr(self, "client", None)
        listar = getattr(client, "listar", None)
        if not callable(listar):
            return _ManualConversationLookup(
                status="error",
                error="El backend de memoria no permite consultar guardados existentes.",
            )

        try:
            registros = listar(where={clave: valor}, limite=1)
        except Exception as exc:
            logger.warning(
                "No se pudo comprobar guardado conversacional existente: error_type=%s",
                type(exc).__name__,
            )
            return _ManualConversationLookup(status="error", error=str(exc))

        if registros is None:
            return _ManualConversationLookup(
                status="error",
                error="Chroma no confirmó la consulta de idempotencia.",
            )
        ids = registros.get("ids") or []
        metadatas = registros.get("metadatas") or []
        if not ids:
            return _ManualConversationLookup(status="missing")
        metadata = metadatas[0] if metadatas and isinstance(metadatas[0], dict) else {}
        return _ManualConversationLookup(
            status="found",
            record=(str(ids[0]), dict(metadata)),
        )

    def _buscar_guardado_por_transaccion(
        self,
        id_transaccion: str,
    ) -> _ManualConversationLookup:
        exacto = self._buscar_guardado_conversacional(
            "id_transaccion_guardado",
            id_transaccion,
        )
        if exacto.status != "missing":
            return exacto

        client = getattr(self, "client", None)
        listar = getattr(client, "listar", None)
        if not callable(listar):
            return _ManualConversationLookup(
                status="error",
                error="El backend no permite consultar recibos de transacción.",
            )
        try:
            registros = listar(where={"tipo_evento": "memoria_conversacional_manual"})
        except Exception as exc:
            logger.warning(
                "No se pudieron consultar recibos de guardado: error_type=%s",
                type(exc).__name__,
            )
            return _ManualConversationLookup(status="error", error=str(exc))
        if registros is None:
            return _ManualConversationLookup(
                status="error",
                error="Chroma no confirmó la consulta de recibos de guardado.",
            )

        ids = registros.get("ids") or []
        metadatas = registros.get("metadatas") or []
        transaction_hash = self._huella_transaccion_guardado(id_transaccion)
        for index, memory_id in enumerate(ids):
            metadata = (
                metadatas[index]
                if index < len(metadatas) and isinstance(metadatas[index], dict)
                else {}
            )
            hashes = {
                item
                for item in str(metadata.get("transacciones_guardado_hashes") or "").split(",")
                if item
            }
            if transaction_hash in hashes:
                return _ManualConversationLookup(
                    status="found",
                    record=(str(memory_id), dict(metadata)),
                )
        return _ManualConversationLookup(status="missing")

    def _asociar_transaccion_a_guardado(
        self,
        registro: tuple[str, dict[str, Any]],
        id_transaccion: str,
    ) -> bool:
        """Añade un recibo hash al mismo recuerdo, sin crear otra unidad semántica."""
        memory_id, metadata = registro
        transaction_hash = self._huella_transaccion_guardado(id_transaccion)
        hashes = [
            item
            for item in str(metadata.get("transacciones_guardado_hashes") or "").split(",")
            if item
        ]
        if transaction_hash in hashes:
            return True

        hashes.append(transaction_hash)
        if len(hashes) > self.MANUAL_SAVE_MAX_TRANSACTION_HASHES:
            hashes = [hashes[0], *hashes[-(self.MANUAL_SAVE_MAX_TRANSACTION_HASHES - 1) :]]

        actualizar = getattr(getattr(self, "client", None), "actualizar_metadatos", None)
        if not callable(actualizar):
            logger.warning("El backend no permite asociar el nuevo recibo de transacción.")
            return False

        metadata_actualizada = dict(metadata)
        metadata_actualizada["transacciones_guardado_hashes"] = ",".join(hashes)
        try:
            return bool(
                actualizar(
                    ids=[memory_id],
                    metadatos=[metadata_actualizada],
                )
            )
        except Exception as exc:
            logger.warning(
                "No se pudo asociar recibo de transacción al guardado: error_type=%s",
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _huella_transaccion_guardado(id_transaccion: str) -> str:
        return sha256(str(id_transaccion or "").strip().encode("utf-8")).hexdigest()

    @staticmethod
    def _resultado_guardado_existente(
        registro: tuple[str, dict[str, Any]],
        *,
        fallback_fingerprint: str = "",
        fallback_turn_count: int = 0,
    ) -> ManualConversationSaveResult:
        memory_id, metadata = registro
        try:
            turn_count = int(metadata.get("cantidad_turnos", fallback_turn_count))
        except (TypeError, ValueError):
            turn_count = fallback_turn_count
        return ManualConversationSaveResult(
            status="already_saved",
            memory_id=memory_id,
            fingerprint=str(
                metadata.get("huella_guardado_conversacional")
                or fallback_fingerprint
            ),
            turn_count=turn_count,
            started_at=str(metadata.get("inicio_utc") or ""),
            ended_at=str(metadata.get("fin_utc") or ""),
        )

    @classmethod
    def _huella_fotografia_conversacional(
        cls,
        snapshot: tuple[PublicConversationTurn, ...],
    ) -> str:
        payload = {
            "version_esquema": 1,
            "turnos": [
                {
                    "timestamp_utc": cls._timestamp_utc(turno.timestamp),
                    "usuario": turno.usuario,
                    "lazzaro": turno.lazzaro,
                }
                for turno in snapshot
            ],
        }
        canonico = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonico.encode("utf-8")).hexdigest()

    @classmethod
    def _documento_fotografia_conversacional(
        cls,
        snapshot: tuple[PublicConversationTurn, ...],
    ) -> str:
        inicio = cls._timestamp_utc(snapshot[0].timestamp)
        fin = cls._timestamp_utc(snapshot[-1].timestamp)
        resumen_compacto = cls._resumen_compacto_fotografia(snapshot)
        lineas = [
            "MEMORIA CONVERSACIONAL GUARDADA POR SOLICITUD DEL USUARIO",
            "INDICE COMPACTO DE TODOS LOS TURNOS:",
            resumen_compacto,
            f"Periodo UTC: {inicio} - {fin}",
            f"Intercambios publicos: {len(snapshot)}",
            "TRANSCRIPCION PUBLICA:",
        ]
        for indice, turno in enumerate(snapshot, start=1):
            timestamp = cls._timestamp_utc(turno.timestamp)
            if turno.usuario:
                lineas.append(f"[{indice}][{timestamp}] Usuario: {turno.usuario}")
            if turno.lazzaro:
                lineas.append(f"[{indice}][{timestamp}] Lazzaro: {turno.lazzaro}")
        return "\n".join(lineas)

    @classmethod
    def _resumen_compacto_fotografia(
        cls,
        snapshot: tuple[PublicConversationTurn, ...],
    ) -> str:
        """Representa todos los turnos dentro del prefijo que Persona sí recibe."""
        separador = " | "
        presupuesto = cls.MANUAL_SAVE_DIGEST_CHARS
        por_turno = max(
            16,
            (presupuesto - len(separador) * max(0, len(snapshot) - 1))
            // max(1, len(snapshot)),
        )
        fragmentos = []
        for indice, turno in enumerate(snapshot, start=1):
            texto = f"T{indice} U:{turno.usuario} L:{turno.lazzaro}"
            fragmentos.append(cls._compactar_extremos(texto, por_turno))
        resumen = separador.join(fragmentos)
        return cls._compactar_extremos(resumen, presupuesto)

    @staticmethod
    def _compactar_extremos(texto: str, limite: int) -> str:
        limpio = " ".join(str(texto or "").split())
        if len(limpio) <= limite:
            return limpio
        if limite <= 7:
            return limpio[:limite]
        izquierda = (limite - 3) // 2
        derecha = limite - 3 - izquierda
        return f"{limpio[:izquierda]}...{limpio[-derecha:]}"

    @staticmethod
    def _timestamp_utc(timestamp: datetime) -> str:
        if timestamp.tzinfo is None:
            timestamp_utc = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp_utc = timestamp.astimezone(timezone.utc)
        return timestamp_utc.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _registrar_hechos_conversacionales(
        self,
        mensaje_usuario: str,
        respuesta_lazzaro: str = "",
        route: str = "",
        intent: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Guarda solo hechos relacionales explicitos; no persiste cada turno."""
        hechos = self._extraer_hechos_relacionales_detallados(mensaje_usuario, respuesta_lazzaro)
        if not hechos:
            return

        metadata_base = metadata or {}
        for hecho in hechos:
            regla = str(hecho.get("regla", "")).strip()
            if not regla:
                continue

            huella = str(hecho.get("huella_conversacional") or "").strip()
            if not huella:
                huella = self._calcular_huella_relacional(
                    categoria=str(hecho.get("categoria_relacional", "")),
                    valor=str(hecho.get("valor_relacional", "")),
                    estado=str(hecho.get("estado_relacional", "")),
                    regla=regla,
                )

            if huella in self._hechos_conversacionales_guardados:
                continue
            if self._hecho_relacional_ya_persistido(huella):
                self._hechos_conversacionales_guardados.add(huella)
                continue

            guardada = self.guardar_regla(
                regla=regla,
                tipo_evento="memoria_relacional",
                valencia=0.25,
                impacto_emocional=0.65,
                origen="conversacion_selectiva",
                extra={
                    "fuente": "memoria_conversacional_fase_4",
                    "route": route,
                    "intent": intent,
                    "id_transaccion": metadata_base.get("id_transaccion"),
                    "accion": metadata_base.get("accion"),
                    "huella_conversacional": huella,
                    "categoria_relacional": hecho.get("categoria_relacional", ""),
                    "valor_relacional": hecho.get("valor_relacional", ""),
                    "estado_relacional": hecho.get("estado_relacional", "activo"),
                },
            )
            if guardada:
                self._hechos_conversacionales_guardados.add(huella)

    @classmethod
    def _extraer_hechos_conversacionales(
        cls,
        mensaje_usuario: str,
        respuesta_lazzaro: str = "",
    ) -> list[str]:
        """Extrae hechos pequenos y estables desde texto conversacional explicito."""
        return [
            str(hecho.get("regla", "")).strip()
            for hecho in cls._extraer_hechos_relacionales_detallados(
                mensaje_usuario,
                respuesta_lazzaro,
            )
            if str(hecho.get("regla", "")).strip()
        ]

    @classmethod
    def _extraer_hechos_relacionales_detallados(
        cls,
        mensaje_usuario: str,
        respuesta_lazzaro: str = "",
    ) -> list[dict[str, str]]:
        """Extrae hechos relacionales con metadata para vigencia y deduplicacion."""
        texto_original = " ".join(str(mensaje_usuario or "").split())
        if not texto_original:
            return []

        texto = cls._normalizar_texto_busqueda(texto_original)
        hechos: list[dict[str, str]] = []

        apodo_revocado = cls._buscar_primer_grupo(
            texto,
            (
                r"\bya\s+no\s+te\s+(?:dire|digo|llamare|llamo)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bno\s+te\s+(?:dire|digo|llamare|llamo)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bdeje\s+de\s+(?:decirte|llamarte)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bdeja\s+de\s+(?:decirte|llamarte)\s+['\"]?([a-z0-9_-]{2,32})",
            ),
        )
        if apodo_revocado:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: el usuario pidio no volver a llamar a Lazzaro '{apodo_revocado}'.",
                    categoria="apodo_lazzaro",
                    valor=apodo_revocado,
                    estado="revocado",
                )
            )

        nombre_usuario_revocado = cls._buscar_primer_grupo(
            texto,
            (
                r"\bya\s+no\s+me\s+(?:digas|llames)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bno\s+me\s+(?:digas|llames)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bdeja\s+de\s+(?:decirme|llamarme)\s+['\"]?([a-z0-9_-]{2,32})",
            ),
        )
        if nombre_usuario_revocado:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: el usuario pidio que Lazzaro ya no lo llame '{nombre_usuario_revocado}'.",
                    categoria="nombre_usuario",
                    valor=nombre_usuario_revocado,
                    estado="revocado",
                )
            )

        apodo = cls._buscar_primer_grupo(
            texto,
            (
                r"\bte\s+(?:puse|di|doy|dije)\s+(?:el\s+)?apodo\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bte\s+suelo\s+llamar\s+['\"]?([a-z0-9_-]{2,32})",
                r"\btu\s+apodo\s+(?:es|sera)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bte\s+apod(?:e|o)\s+['\"]?([a-z0-9_-]{2,32})",
            ),
        )
        if apodo:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: el usuario suele llamar a Lazzaro '{apodo}'.",
                    categoria="apodo_lazzaro",
                    valor=apodo,
                    estado="activo",
                )
            )

        nombre_lazzaro = cls._buscar_primer_grupo(
            texto,
            (
                r"\btu\s+nombre\s+(?:verdadero\s+)?(?:es|sera)\s+['\"]?([a-z0-9_-]{2,32})",
                r"\btu\s+designacion\s+(?:es|sera)\s+['\"]?([a-z0-9_-]{2,32})",
            ),
        )
        if nombre_lazzaro:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: el usuario afirma que el nombre de Lazzaro es '{nombre_lazzaro}'.",
                    categoria="nombre_lazzaro",
                    valor=nombre_lazzaro,
                    estado="activo",
                )
            )

        nombre_usuario = cls._buscar_primer_grupo(
            texto,
            (
                r"\bme\s+llamo\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bmi\s+nombre\s+es\s+['\"]?([a-z0-9_-]{2,32})",
                r"\bllamame\s+['\"]?([a-z0-9_-]{2,32})",
            ),
        )
        if nombre_usuario:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: el usuario se identifica como '{nombre_usuario}'.",
                    categoria="nombre_usuario",
                    valor=nombre_usuario,
                    estado="activo",
                )
            )

        if cls._contiene_autoridad_creador(texto):
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=(
                        "Memoria relacional: el usuario afirma ser Perro, creador, desarrollador "
                        "o arquitecto principal de Lazzaro."
                    ),
                    categoria="autoridad_usuario",
                    valor="perro_creador",
                    estado="activo",
                )
            )

        preferencia = cls._buscar_preferencia(texto_original, "me gusta")
        if preferencia:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: al usuario le gusta {preferencia}.",
                    categoria=f"preferencia_usuario:{cls._normalizar_valor_relacional(preferencia)}",
                    valor=preferencia,
                    estado="activo",
                )
            )

        rechazo = cls._buscar_preferencia(texto_original, "no me gusta")
        if rechazo:
            hechos.append(
                cls._crear_hecho_relacional(
                    regla=f"Memoria relacional: al usuario no le gusta {rechazo}.",
                    categoria=f"rechazo_usuario:{cls._normalizar_valor_relacional(rechazo)}",
                    valor=rechazo,
                    estado="activo",
                )
            )

        return cls._deduplicar_hechos_relacionales(hechos)

    @classmethod
    def _crear_hecho_relacional(
        cls,
        regla: str,
        categoria: str,
        valor: str,
        estado: str = "activo",
    ) -> dict[str, str]:
        valor_limpio = str(valor or "").strip(" .,;:!?\"'")
        categoria_limpia = str(categoria or "").strip().lower()
        estado_limpio = str(estado or "activo").strip().lower()
        huella = cls._calcular_huella_relacional(
            categoria=categoria_limpia,
            valor=valor_limpio,
            estado=estado_limpio,
            regla=regla,
        )
        return {
            "regla": regla,
            "categoria_relacional": categoria_limpia,
            "valor_relacional": valor_limpio,
            "estado_relacional": estado_limpio,
            "huella_conversacional": huella,
        }

    @staticmethod
    def _normalizar_texto_busqueda(texto: str) -> str:
        tabla = str.maketrans(
            "\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1"
            "\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1",
            "aeiouunAEIOUUN",
        )
        return str(texto or "").translate(tabla).lower()

    @staticmethod
    def _buscar_primer_grupo(texto: str, patrones: tuple[str, ...]) -> str:
        for patron in patrones:
            match = re.search(patron, texto, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .,;:!?\"'")
        return ""

    @staticmethod
    def _contiene_autoridad_creador(texto: str) -> bool:
        patrones = (
            r"\bsoy\s+tu\s+(?:creador|desarrollador|arquitecto)\b",
            r"\byo\s+soy\s+tu\s+(?:creador|desarrollador|arquitecto)\b",
            r"\bel\s+desarrollador\s+soy\s+yo\b",
            r"\byo\s+(?:escribi|hice|construi|programe)\s+tu\s+codigo\b",
            r"\bcomo\s+tu\s+(?:creador|desarrollador|arquitecto)\b",
            r"\bsoy\s+perro\b",
            r"\byo\s+soy\s+perro\b",
        )
        return any(re.search(patron, texto, flags=re.IGNORECASE) for patron in patrones)

    @classmethod
    def _buscar_preferencia(cls, texto_original: str, marcador: str) -> str:
        texto = " ".join(str(texto_original or "").split())
        prefijo = r"(?<!no\s)" if marcador == "me gusta" else ""
        patron = rf"{prefijo}\b{re.escape(marcador)}\s+([^?.!,;]{{3,90}})"
        match = re.search(patron, texto, flags=re.IGNORECASE)
        if not match:
            return ""
        preferencia = match.group(1).strip(" .,;:!?\"'")
        if cls._parece_pregunta(preferencia):
            return ""
        return preferencia[:90].strip()

    @staticmethod
    def _parece_pregunta(texto: str) -> bool:
        texto_norm = f" {texto.lower()} "
        return any(pista in texto_norm for pista in (" te gusta ", " que opinas ", " puedes ", " quieres "))

    @staticmethod
    def _deduplicar_hechos(hechos: list[str]) -> list[str]:
        vistos: set[str] = set()
        resultado: list[str] = []
        for hecho in hechos:
            clave = hecho.lower()
            if clave in vistos:
                continue
            vistos.add(clave)
            resultado.append(hecho)
        return resultado

    @staticmethod
    def _deduplicar_hechos_relacionales(hechos: list[dict[str, str]]) -> list[dict[str, str]]:
        vistos: set[str] = set()
        resultado: list[dict[str, str]] = []
        for hecho in hechos:
            huella = str(hecho.get("huella_conversacional", "")).strip()
            if not huella:
                huella = MemoryManager._calcular_huella_relacional(
                    categoria=str(hecho.get("categoria_relacional", "")),
                    valor=str(hecho.get("valor_relacional", "")),
                    estado=str(hecho.get("estado_relacional", "")),
                    regla=str(hecho.get("regla", "")),
                )
            if huella in vistos:
                continue
            vistos.add(huella)
            resultado.append(hecho)
        return resultado

    @staticmethod
    def _normalizar_valor_relacional(valor: str) -> str:
        texto = MemoryManager._normalizar_texto_busqueda(valor)
        texto = re.sub(r"[^a-z0-9_-]+", "_", texto).strip("_")
        return texto[:48] or "valor"

    @staticmethod
    def _calcular_huella_relacional(
        categoria: str,
        valor: str,
        estado: str,
        regla: str,
    ) -> str:
        base = "|".join(
            (
                MemoryManager._normalizar_valor_relacional(categoria),
                MemoryManager._normalizar_valor_relacional(valor),
                MemoryManager._normalizar_valor_relacional(estado),
                MemoryManager._normalizar_texto_busqueda(regla),
            )
        )
        return sha1(base.encode("utf-8")).hexdigest()[:16]

    def _hecho_relacional_ya_persistido(self, huella: str) -> bool:
        """Evita duplicados despues de reiniciar buscando la huella en Chroma."""
        huella_limpia = str(huella or "").strip()
        if not huella_limpia:
            return False

        client = getattr(self, "client", None)
        listar = getattr(client, "listar", None)
        if not callable(listar):
            return False

        try:
            registros = listar(where={"huella_conversacional": huella_limpia}, limite=1)
        except Exception as e:
            logger.debug("No se pudo verificar huella relacional existente: %s", e)
            return False

        if not registros:
            return False
        return bool(registros.get("ids"))

    def _seleccionar_memorias_relacionales_vigentes(
        self,
        recuerdos: list[MemoryResult],
    ) -> list[MemoryResult]:
        """Prioriza el hecho mas reciente por categoria y conserva revocaciones explicitas."""
        if not recuerdos:
            return []

        seleccion: dict[str, MemoryResult] = {}
        sin_categoria: list[MemoryResult] = []

        for recuerdo in recuerdos:
            metadata = getattr(recuerdo.recuerdo, "metadata", None)
            extra = getattr(metadata, "extra", {}) if metadata else {}
            categoria = str(extra.get("categoria_relacional", "")).strip()
            if not categoria:
                sin_categoria.append(recuerdo)
                continue

            existente = seleccion.get(categoria)
            if existente is None or self._timestamp_relacional(recuerdo) >= self._timestamp_relacional(existente):
                seleccion[categoria] = recuerdo

        vigentes = list(seleccion.values()) + sin_categoria
        vigentes.sort(
            key=lambda resultado: (
                self._estado_relacional_prioridad(resultado),
                self._timestamp_relacional(resultado),
                getattr(resultado, "metadata_score", 0.0),
            ),
            reverse=True,
        )
        return vigentes

    @staticmethod
    def _timestamp_relacional(resultado: MemoryResult) -> datetime:
        metadata = getattr(resultado.recuerdo, "metadata", None)
        timestamp = getattr(metadata, "timestamp", None)
        return timestamp if isinstance(timestamp, datetime) else datetime.min

    @staticmethod
    def _estado_relacional_prioridad(resultado: MemoryResult) -> int:
        metadata = getattr(resultado.recuerdo, "metadata", None)
        extra = getattr(metadata, "extra", {}) if metadata else {}
        estado = str(extra.get("estado_relacional", "activo")).strip().lower()
        return 1 if estado == "activo" else 0

    @staticmethod
    def _recortar_texto(texto: str, limite: int) -> str:
        texto_limpio = " ".join(str(texto or "").split())
        if len(texto_limpio) <= limite:
            return texto_limpio
        return texto_limpio[: limite - 3].rstrip() + "..."

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

            decaimiento = self.decay.calcular_importancia_desde_metadata(
                timestamp=ts,
                impacto_emocional=impacto,
                vida_media_horas=72.0,
                minimo_recencia=0.05,
            )
            if decaimiento["importancia_total"] > self.consolidacion_importancia_maxima:
                continue

            candidatos.append(
                {
                    "id": doc_id,
                    "documento": doc,
                    "tipo_evento": tipo_evento,
                    "metadata": meta,
                    "timestamp": ts,
                    "decaimiento": decaimiento,
                }
            )

        candidatos.sort(
            key=lambda candidato: (
                candidato["decaimiento"]["importancia_total"],
                candidato["timestamp"],
            )
        )
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


def _normalize_memory_token(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _affective_memory_key(object_id: str, object_class: str, affect: str, source: str) -> str:
    return "::".join(
        [
            str(object_id or "").strip(),
            _normalize_memory_token(object_class),
            _normalize_memory_token(affect),
            _normalize_memory_token(source) or "event",
        ]
    )


def _object_refs_to_filters(object_refs: list[Any] | None) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    classes: set[str] = set()
    for ref in object_refs or []:
        if isinstance(ref, str):
            ids.add(ref)
            classes.update(_class_tokens_from_text(ref))
            continue

        object_id = str(getattr(ref, "id", "") or "").strip()
        if object_id:
            ids.add(object_id)
            classes.update(_class_tokens_from_text(object_id))

        display_name = str(getattr(ref, "display_name", "") or "").strip()
        if display_name:
            classes.update(_class_tokens_from_text(display_name))

        aliases = getattr(ref, "aliases", [])
        if isinstance(aliases, list):
            for alias in aliases:
                classes.update(_class_tokens_from_text(alias))
    return ids, classes


def _class_tokens_from_text(value: object) -> set[str]:
    normalized = _normalize_memory_token(value)
    if not normalized:
        return set()
    tokens = {normalized}
    tokens.update(token for token in normalized.split("_") if token)
    return tokens


def _affective_valence(affect: str) -> float:
    normalized = _normalize_memory_token(affect)
    if normalized in {"alivio", "descanso", "calma", "seguridad", "refugio"}:
        return 0.4
    if normalized in {"alerta", "peligro", "dolor", "miedo", "estres"}:
        return -0.4
    return 0.0
