"""
memory_manager.py - Fachada principal del sistema de memoria de LAZZARO.
Orquesta todos los subsistemas y expone una API simple para el resto del agente.
"""

from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha1
import re
from typing import Any, Optional

from core.logger import get_logger
from memory.chroma_client import ChromaClient
from memory.conversation_memory import ConversationMemory
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
        """Registra un intercambio de dialogo reciente y persiste hechos relacionales claros."""
        self.conversation_memory.registrar_turno(
            usuario=mensaje_usuario,
            lazzaro=respuesta_lazzaro,
            pensamiento=pensamiento,
            emocion=emocion,
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
