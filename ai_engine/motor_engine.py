"""
motor_engine.py - Capa 1 (Cerebro Motor).

El motor es frio y determinista: valida acciones fisicas, resuelve comandos
simples sin LLM cuando es seguro, y nunca maneja personalidad ni conversacion.
"""

from __future__ import annotations

from dataclasses import replace
import re
from typing import Any, Dict, Iterable, Optional

from core.logger import get_logger

from .fuzzy_object_resolver import FuzzyObjectResolver
from .motor_schemas import (
    ACTION_SCHEMA_MAP,
    MotorActionAdapterV1,
    MotorActionContractFailureV1,
)
from .motor_resolution import InteractionType, MotorDiagnosis, MotorResolution
from .perception_models import PerceptionSnapshot, normalize_perception
from .spatial_reasoner import SpatialReasoner

logger = get_logger(__name__)


class MotorEngine:
    """Procesa y valida comandos motores antes de enviarlos a Unity."""

    ACCIONES_MOTORAS = set(ACTION_SCHEMA_MAP)
    ACCIONES_CON_OBJETO = {
        "navegar_a_objeto",
        "alejarse_de_objeto",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "aplicar_fuerza",
    }
    ALIAS_OBJETOS = {
        "cube": ("cube", "cubo"),
        "sphere": ("sphere", "esfera", "pelota", "bola"),
        "capsule": ("capsule", "capsula", "capsula", "cápsula"),
        "plane": ("plane", "plano", "suelo", "piso"),
        "flor": ("flor", "flores"),
        "arbol": ("arbol",),
        "roca": ("roca",),
        "hongo": ("hongo",),
    }
    INTERACTION_LEXICON = {
        "mira": InteractionType.MIRAR,
        "mirar": InteractionType.MIRAR,
        "observa": InteractionType.MIRAR,
        "observar": InteractionType.MIRAR,
        "inspecciona": InteractionType.INSPECCIONAR,
        "inspeccionar": InteractionType.INSPECCIONAR,
        "toca": InteractionType.TOCAR,
        "tocar": InteractionType.TOCAR,
        "golpea": InteractionType.GOLPEAR,
        "golpear": InteractionType.GOLPEAR,
        "usa": InteractionType.USAR,
        "usar": InteractionType.USAR,
        "activa": InteractionType.ACTIVAR,
        "activar": InteractionType.ACTIVAR,
    }
    GENERIC_INTERACTION_TOKENS = frozenset({"interactua", "interactuar"})

    def __init__(self):
        self.spatial = SpatialReasoner()
        self.fuzzy_resolver = FuzzyObjectResolver()

    def resolver_comando_usuario(
        self,
        mensaje: str,
        percepcion: Dict[str, Any],
        herramientas_disponibles: Iterable[str] | None = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Intenta resolver comandos motores obvios sin LLM.
        Devuelve None si el comando requiere interpretacion ambigua.
        """
        diagnostico = self.diagnosticar_comando_usuario(
            mensaje,
            percepcion=percepcion,
            herramientas_disponibles=herramientas_disponibles,
        )
        if not diagnostico.ready:
            return None

        return self.procesar_diagnostico_motor(diagnostico)

    def diagnosticar_comando_usuario(
        self,
        mensaje: str,
        percepcion: Dict[str, Any],
        herramientas_disponibles: Iterable[str] | None = None,
        intent: str = "MOTOR_SIMPLE",
        contexto_motor: dict[str, Any] | None = None,
    ) -> MotorDiagnosis:
        """
        Clasifica un comando motor sin llamar al LLM.
        Permite distinguir entre "no es motor" y "es motor, pero falta objetivo".
        """
        interaction_requested, interaction_type = self._analizar_interaccion_textual(
            mensaje
        )
        accion = self._inferir_accion(
            mensaje,
            interaction_requested=interaction_requested,
        )
        if accion is None:
            return MotorDiagnosis(estado="sin_intencion")

        permitidas = set(herramientas_disponibles or self.ACCIONES_MOTORAS)
        if accion not in permitidas:
            logger.info("Motor determinista omitido: %s no esta permitida.", accion)
            return MotorDiagnosis(estado="no_permitida", accion=accion)

        if accion in self.ACCIONES_CON_OBJETO:
            return self._diagnosticar_accion_con_objeto(
                accion=accion,
                mensaje=mensaje,
                percepcion=percepcion,
                intent=intent,
                contexto_motor=contexto_motor or {},
                interaction_type=interaction_type,
            )

        parametros = self._construir_parametros_desde_texto(accion, mensaje, percepcion)
        if parametros is None:
            return MotorDiagnosis(estado="ambigua", accion=accion)

        return MotorDiagnosis(
            estado="lista",
            accion=accion,
            parametros=parametros,
        )

    def procesar_comando_motor(
        self,
        accion_propuesta: str,
        parametros: Dict[str, Any] | None,
        percepcion: Dict[str, Any] | None,
        intent: str = "MOTOR_SIMPLE",
        contexto_motor: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Valida una accion motora propuesta por reglas o por el LLM.
        Devuelve siempre una MotorAction valida o un none seguro.
        """
        accion = str(accion_propuesta or "").strip()
        parametros_limpios = dict(parametros) if isinstance(parametros, dict) else {}
        percepcion_limpia = percepcion if isinstance(percepcion, dict) else {}

        if accion == "none":
            return self._none("Accion motora none recibida.")

        schema_cls = ACTION_SCHEMA_MAP.get(accion)
        if schema_cls is None:
            logger.warning("Accion motora desconocida: %s. Se devuelve none.", accion)
            return self._none(f"Accion '{accion}' no reconocida por el Motor.")

        interaction_type: InteractionType | None = None
        if accion == "interactuar_objeto_3d":
            valid_interaction, interaction_type = self._canonicalizar_interaccion_estructurada(
                parametros_limpios,
                contexto_motor or {},
            )
            if not valid_interaction:
                return self._none(
                    "Parametros invalidos. Accion bloqueada por Capa 1."
                )

        parametros_limpios, metadata = self._enriquecer_parametros(
            accion,
            parametros_limpios,
            percepcion_limpia,
            intent=intent,
            contexto_motor=contexto_motor or {},
            interaction_type=interaction_type,
        )
        if metadata.get("motor_resolution", {}).get("status") not in {None, "ready"}:
            estado = str(metadata.get("motor_resolution", {}).get("status", "none"))
            return self._none(
                f"Resolucion motora '{estado}' bloqueo la accion.",
                metadata=metadata,
            )

        return self._materializar_accion_motor(
            accion,
            parametros_limpios,
            metadata,
        )

    def procesar_diagnostico_motor(
        self,
        diagnostico: MotorDiagnosis,
    ) -> Dict[str, Any]:
        """Materializa un diagnostico sin volver a seleccionar su objetivo."""
        if not isinstance(diagnostico, MotorDiagnosis):
            raise TypeError("diagnostico must be MotorDiagnosis")

        diagnosis_snapshot = diagnostico.to_dict()
        metadata = dict(diagnosis_snapshot.get("metadata") or {})
        if not diagnostico.ready:
            return self._none(
                f"Diagnostico motor '{diagnostico.estado}' no es ejecutable.",
                metadata=metadata,
            )

        accion = str(diagnostico.accion or "").strip()
        parametros = dict(diagnosis_snapshot.get("parametros") or {})
        if accion not in ACTION_SCHEMA_MAP:
            return self._none("Diagnostico motor con accion desconocida.", metadata=metadata)

        resolucion = diagnostico.resolucion_motor
        if resolucion is not None:
            if not self._postcondiciones_resolucion_preservadas(
                accion,
                parametros,
                resolucion,
            ):
                return self._none(
                    "Diagnostico motor inconsistente. Accion bloqueada por Capa 1.",
                    metadata=metadata,
                )

        return self._materializar_accion_motor(accion, parametros, metadata)

    @staticmethod
    def _postcondiciones_resolucion_preservadas(
        accion: str,
        parametros: Dict[str, Any],
        resolucion: MotorResolution,
    ) -> bool:
        """Revalida coherencia/affordance sin ejecutar otra resolucion de target."""
        if not resolucion.ready or (resolucion.action and resolucion.action != accion):
            return False

        if accion in MotorEngine.ACCIONES_CON_OBJETO:
            target = (
                resolucion.legacy_nombre_objeto
                or resolucion.target_display_name
                or resolucion.target_id
                or ""
            )
            if not target or str(parametros.get("nombre_objeto", "")).strip() != target:
                return False

            selected = next(
                (
                    candidate
                    for candidate in resolucion.candidates
                    if candidate.target_id == resolucion.target_id
                ),
                None,
            )
            if selected is not None and selected.affordance_decision.get("allowed") is False:
                return False

            if accion == "interactuar_objeto_3d":
                if resolucion.interaction_type is None:
                    return False
                if parametros.get("tipo_accion") != resolucion.interaction_type.value:
                    return False

        return True

    @staticmethod
    def _materializar_accion_motor(
        accion: str,
        parametros: Dict[str, Any],
        metadata: dict[str, Any],
    ) -> Dict[str, Any]:
        """Aplica schema/numeric contract y crea MotorAction, sin resolver targets."""

        canonical = MotorActionAdapterV1.parse(accion, parametros)
        if isinstance(canonical, MotorActionContractFailureV1):
            logger.warning(
                "Contrato Motor invalido para '%s': reason_code=%s",
                accion,
                canonical.reason_code.value,
            )
            return MotorEngine._none(
                "Parametros invalidos. Accion bloqueada por Capa 1.",
                metadata={
                    **metadata,
                    "motor_contract_failure": canonical.reason_code.value,
                },
            )

        logger.debug("MotorAction generada: %s", canonical.action.value)
        return canonical.to_legacy_dict(
            reason=f"Accion '{accion}' validada por Capa 1.",
            metadata=metadata,
        )

    @staticmethod
    def _none(razon: str, metadata: dict[str, Any] | None = None) -> Dict[str, Any]:
        canonical = MotorActionAdapterV1.require("none", {})
        return canonical.to_legacy_dict(reason=razon, metadata=metadata)

    def _inferir_accion(
        self,
        mensaje: str,
        *,
        interaction_requested: bool,
    ) -> Optional[str]:
        texto = self.spatial.normalizar_texto(mensaje)
        if not texto:
            return None

        if any(token in texto for token in ("suelta", "soltar", "deja caer")):
            return "soltar_objeto"
        if any(token in texto for token in ("agarra", "coge", "recoge", "sosten")):
            return "agarrar_objeto"
        if any(
            token in texto
            for token in (
                "alejate",
                "alejarme",
                "alejarte",
                "alejarse",
                "aleja",
                "apartate",
                "retirate",
                "separate",
                "retrocede de",
                "deja de seguir",
            )
        ):
            return "alejarse_de_objeto"
        if self._es_comando_giro(texto):
            return "girar_avatar"
        if interaction_requested:
            return "interactuar_objeto_3d"
        if self._parsear_coordenadas(texto) is not None:
            return "navegar_a_posicion"
        if any(
            token in texto
            for token in ("camina", "mueve", "mover", "moverte", "muevete", "ve a", "acercate", "dirigete", "navega")
        ):
            return "navegar_a_objeto"
        return None

    def _construir_parametros_desde_texto(
        self,
        accion: str,
        mensaje: str,
        percepcion: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if accion == "navegar_a_posicion":
            return self._parsear_coordenadas(mensaje)

        if accion == "girar_avatar":
            return self._parsear_giro(mensaje)

        if accion == "soltar_objeto":
            return {"fuerza_lanzamiento": 0.0}

        if accion in self.ACCIONES_CON_OBJETO:
            nombre = self._extraer_objeto_objetivo(mensaje, percepcion)
            if not nombre:
                logger.info("Motor determinista no encontro objeto objetivo en: %s", mensaje)
                return None

            parametros: Dict[str, Any] = {"nombre_objeto": nombre}
            if accion == "alejarse_de_objeto":
                parametros["distancia"] = 2.0
            return parametros

        return None

    def _diagnosticar_accion_con_objeto(
        self,
        *,
        accion: str,
        mensaje: str,
        percepcion: Dict[str, Any],
        intent: str,
        contexto_motor: dict[str, Any],
        interaction_type: InteractionType | None,
    ) -> MotorDiagnosis:
        resolucion = self._resolver_objetivo_fuzzy(
            query=mensaje,
            accion=accion,
            percepcion=percepcion,
            intent=intent,
            contexto_motor=contexto_motor,
            interaction_type=interaction_type,
        )
        if resolucion is not None:
            metadata = resolucion.to_metadata()
            self._adjuntar_contexto_alias(metadata, contexto_motor, accion)
            if resolucion.status == "ready":
                parametros = self._parametros_desde_resolucion(accion, resolucion)
                return MotorDiagnosis(
                    estado="lista",
                    accion=accion,
                    parametros=parametros,
                    metadata=metadata,
                    resolucion_motor=resolucion,
                )

            return MotorDiagnosis(
                estado=resolucion.status,
                accion=accion,
                metadata=metadata,
                resolucion_motor=resolucion,
            )

        parametros = self._construir_parametros_legacy_con_objeto(
            accion,
            mensaje,
            percepcion,
            interaction_type=interaction_type,
        )
        if parametros is None:
            logger.info("Motor determinista no encontro objeto objetivo en: %s", mensaje)
            return MotorDiagnosis(estado="falta_objetivo", accion=accion)

        if accion == "interactuar_objeto_3d":
            target = str(parametros.get("nombre_objeto") or "").strip()
            status = "ready" if interaction_type is not None else "requires_clarification"
            legacy_resolution = MotorResolution(
                status=status,
                action=accion,
                interaction_type=interaction_type,
                target_id=target or None,
                target_display_name=target or None,
                legacy_nombre_objeto=target or None,
                reason=(
                    "Objetivo legacy resuelto con semantica de interaccion explicita."
                    if interaction_type is not None
                    else "La interaccion requiere un tipo de accion explicito."
                ),
                audit={
                    "status_reason": (
                        "legacy_target_ready"
                        if interaction_type is not None
                        else "interaction_type_missing"
                    )
                },
            )
            metadata = legacy_resolution.to_metadata()
            if interaction_type is None:
                return MotorDiagnosis(
                    estado="requires_clarification",
                    accion=accion,
                    metadata=metadata,
                    resolucion_motor=legacy_resolution,
                )
            return MotorDiagnosis(
                estado="lista",
                accion=accion,
                parametros=self._parametros_desde_resolucion(
                    accion,
                    legacy_resolution,
                ),
                metadata=metadata,
                resolucion_motor=legacy_resolution,
            )

        return MotorDiagnosis(estado="lista", accion=accion, parametros=parametros)

    def _construir_parametros_legacy_con_objeto(
        self,
        accion: str,
        mensaje: str,
        percepcion: Dict[str, Any],
        *,
        interaction_type: InteractionType | None,
    ) -> Optional[Dict[str, Any]]:
        nombre = self._extraer_objeto_objetivo(mensaje, percepcion)
        if not nombre:
            return None

        parametros: Dict[str, Any] = {"nombre_objeto": nombre}
        if accion == "alejarse_de_objeto":
            parametros["distancia"] = 2.0
        if accion == "interactuar_objeto_3d":
            if interaction_type is not None:
                parametros["tipo_accion"] = interaction_type.value
        return parametros

    def _parametros_desde_resolucion(
        self,
        accion: str,
        resolucion: MotorResolution,
    ) -> Dict[str, Any]:
        nombre = resolucion.legacy_nombre_objeto or resolucion.target_display_name or resolucion.target_id or ""
        parametros: Dict[str, Any] = {"nombre_objeto": nombre}
        if accion == "alejarse_de_objeto":
            parametros["distancia"] = 2.0
        if accion == "interactuar_objeto_3d":
            if resolucion.interaction_type is not None:
                parametros["tipo_accion"] = resolucion.interaction_type.value
        return parametros

    @staticmethod
    def _es_comando_giro(texto: str) -> bool:
        return any(
            token in texto
            for token in (
                "gira",
                "girar",
                "rota",
                "rotar",
                "rotacion",
                "voltea",
                "da la vuelta",
                "media vuelta",
                "mira alrededor",
                "mirar alrededor",
                "observa alrededor",
                "reorienta",
                "reorientate",
                "reorientarte",
            )
        )

    @staticmethod
    def _parsear_giro(mensaje: str) -> Dict[str, float]:
        texto_original = str(mensaje or "")
        texto = SpatialReasoner.normalizar_texto(texto_original)

        grados: float | None = None
        match = re.search(
            "(-?\\d+(?:[\\.,]\\d+)?)\\s*(?:grados?|deg|\\u00b0|\\*)?",
            texto_original,
            flags=re.IGNORECASE,
        )
        if match:
            grados = float(match.group(1).replace(",", "."))

        if grados is None:
            if "media vuelta" in texto or "da la vuelta" in texto or "voltea" in texto:
                grados = 180.0
            elif "izquierda" in texto:
                grados = -90.0
            elif "derecha" in texto:
                grados = 90.0
            elif "alrededor" in texto or "reorient" in texto:
                grados = 90.0
            else:
                grados = 90.0

        if "izquierda" in texto and grados > 0:
            grados = -grados
        elif "derecha" in texto and grados < 0:
            grados = abs(grados)

        grados = max(-360.0, min(360.0, grados))
        return {"grados": round(grados, 2)}

    def _enriquecer_parametros(
        self,
        accion: str,
        parametros: Dict[str, Any],
        percepcion: Dict[str, Any],
        *,
        intent: str = "MOTOR_SIMPLE",
        contexto_motor: dict[str, Any] | None = None,
        interaction_type: InteractionType | None = None,
    ) -> tuple[Dict[str, Any], dict[str, Any]]:
        metadata: dict[str, Any] = {}
        if accion not in self.ACCIONES_CON_OBJETO:
            return parametros, metadata

        nombre = str(parametros.get("nombre_objeto", "")).strip()
        if not nombre:
            return parametros, metadata

        resolucion = self._resolver_objetivo_fuzzy(
            query=nombre,
            accion=accion,
            percepcion=percepcion,
            intent=intent,
            contexto_motor=contexto_motor or {},
            interaction_type=interaction_type,
        )
        if resolucion is not None:
            metadata = resolucion.to_metadata()
            self._adjuntar_contexto_alias(metadata, contexto_motor or {}, accion)
            if resolucion.status == "ready":
                parametros["nombre_objeto"] = (
                    resolucion.legacy_nombre_objeto
                    or resolucion.target_display_name
                    or resolucion.target_id
                    or nombre
                )
                if accion == "interactuar_objeto_3d" and resolucion.interaction_type is not None:
                    parametros["tipo_accion"] = resolucion.interaction_type.value
            return parametros, metadata

        nombre_normalizado = self.spatial.normalizar_texto(nombre)
        if nombre_normalizado in {"mas_cercano", "objeto_mas_cercano", "cercano", "enfrente"}:
            cercano = self._nombre_objeto_mas_cercano(percepcion)
            if cercano:
                parametros["nombre_objeto"] = cercano
        else:
            nombre_visible = self._resolver_alias_visible(nombre, percepcion)
            if nombre_visible:
                parametros["nombre_objeto"] = nombre_visible

        if accion == "interactuar_objeto_3d":
            target = str(parametros.get("nombre_objeto") or nombre).strip()
            status = "ready" if interaction_type is not None else "requires_clarification"
            legacy_resolution = MotorResolution(
                status=status,
                action=accion,
                interaction_type=interaction_type,
                target_id=target or None,
                target_display_name=target or None,
                legacy_nombre_objeto=target or None,
                reason=(
                    "Objetivo legacy resuelto con semantica de interaccion explicita."
                    if interaction_type is not None
                    else "La interaccion requiere un tipo de accion explicito."
                ),
                audit={
                    "status_reason": (
                        "legacy_target_ready"
                        if interaction_type is not None
                        else "interaction_type_missing"
                    )
                },
            )
            metadata = legacy_resolution.to_metadata()
            self._adjuntar_contexto_alias(metadata, contexto_motor or {}, accion)
            if interaction_type is not None:
                parametros["tipo_accion"] = interaction_type.value
        return parametros, metadata

    def _resolver_objetivo_fuzzy(
        self,
        *,
        query: str,
        accion: str,
        percepcion: Dict[str, Any],
        intent: str,
        contexto_motor: dict[str, Any],
        interaction_type: InteractionType | None,
    ) -> MotorResolution | None:
        if not self._percepcion_tiene_objetos_normalizados(percepcion):
            return None

        try:
            percepcion_resoluble = self._percepcion_con_aliases_legacy(percepcion)
            return self.fuzzy_resolver.resolve(
                query,
                percepcion_resoluble,
                intent=intent,
                action=accion,
                interaction_type=interaction_type,
                context=contexto_motor,
            )
        except Exception as exc:
            logger.warning(
                "FuzzyObjectResolver fallo con percepcion moderna; accion bloqueada: %s",
                exc,
                exc_info=True,
            )
            return MotorResolution(
                status="requires_clarification",
                action=accion,
                interaction_type=interaction_type,
                reason="No fue posible verificar el objetivo contra la percepcion actual.",
                audit={
                    "status_reason": "resolver_exception",
                    "error_type": type(exc).__name__,
                },
            )

    def _percepcion_con_aliases_legacy(
        self,
        percepcion: Dict[str, Any],
    ) -> PerceptionSnapshot:
        """Hace visibles aliases historicos antes de decidir ejecutabilidad."""
        percepcion_moderna = dict(percepcion)
        percepcion_moderna.pop("objetos_visibles", None)
        percepcion_moderna.pop("resumen_percepcion", None)
        snapshot = normalize_perception(percepcion_moderna)
        objects = []
        for obj in snapshot.objects:
            aliases = list(obj.aliases)
            names = {
                self.spatial.normalizar_texto(obj.id),
                self.spatial.normalizar_texto(obj.display_name),
            }
            for canonical, known_aliases in self.ALIAS_OBJETOS.items():
                if not any(canonical in name for name in names):
                    continue
                for alias in known_aliases:
                    if alias not in aliases:
                        aliases.append(alias)
            objects.append(replace(obj, aliases=aliases))
        return replace(snapshot, objects=objects)

    @staticmethod
    def _percepcion_tiene_objetos_normalizados(percepcion: Dict[str, Any]) -> bool:
        if not isinstance(percepcion, dict):
            return False
        for key in ("objetos_normalizados", "objects", "perceived_objects"):
            value = percepcion.get(key)
            if isinstance(value, list):
                return True
        return False

    def _extraer_objeto_objetivo(self, mensaje: str, percepcion: Dict[str, Any]) -> Optional[str]:
        texto = self.spatial.normalizar_texto(mensaje)
        if any(token in texto for token in ("mas cercano", "objeto cercano", "enfrente", "frente")):
            return self._nombre_objeto_mas_cercano(percepcion)

        for nombre in self._nombres_visibles(percepcion):
            nombre_normalizado = self.spatial.normalizar_texto(nombre)
            if nombre_normalizado and nombre_normalizado in texto:
                return nombre

        return self._resolver_alias_visible(mensaje, percepcion)

    def _resolver_alias_visible(self, texto_objetivo: str, percepcion: Dict[str, Any]) -> Optional[str]:
        texto = self.spatial.normalizar_texto(texto_objetivo)
        visibles = self._nombres_visibles(percepcion)

        for canonical, aliases in self.ALIAS_OBJETOS.items():
            if not any(alias in texto for alias in aliases):
                continue

            for nombre in visibles:
                if canonical in self.spatial.normalizar_texto(nombre):
                    return nombre
            return canonical.capitalize()

        return None

    def _nombre_objeto_mas_cercano(self, percepcion: Dict[str, Any]) -> Optional[str]:
        objeto_mas_cercano = percepcion.get("objeto_mas_cercano")
        if isinstance(objeto_mas_cercano, dict):
            nombre = objeto_mas_cercano.get("nombre")
            if nombre:
                return str(nombre)

        nombres = self._nombres_visibles(percepcion)
        return nombres[0] if nombres else None

    def _nombres_visibles(self, percepcion: Dict[str, Any]) -> list[str]:
        nombres: list[str] = []
        objetos = percepcion.get("objetos_visibles")

        if isinstance(objetos, list):
            for obj in objetos:
                if isinstance(obj, dict) and obj.get("nombre"):
                    nombres.append(str(obj["nombre"]))
        elif isinstance(objetos, str):
            nombres.extend(self.spatial.extraer_nombres_desde_resumen(objetos))

        normalizados = percepcion.get("objetos_normalizados")
        if isinstance(normalizados, list):
            for obj in normalizados:
                if not isinstance(obj, dict):
                    continue
                for clave in ("nombre", "display_name", "id"):
                    valor = obj.get(clave)
                    if valor:
                        nombres.append(str(valor))

        resumen = percepcion.get("resumen_percepcion")
        if isinstance(resumen, str):
            nombres.extend(self.spatial.extraer_nombres_desde_resumen(resumen))

        nombres_unicos: list[str] = []
        for nombre in nombres:
            if nombre not in nombres_unicos:
                nombres_unicos.append(nombre)
        return nombres_unicos

    @staticmethod
    def _adjuntar_contexto_alias(
        metadata: dict[str, Any],
        contexto_motor: dict[str, Any],
        accion: str,
    ) -> None:
        user_query = str(contexto_motor.get("user_query") or contexto_motor.get("mensaje_usuario") or "").strip()
        if not user_query:
            return
        metadata["alias_learning"] = {
            "user_query": user_query,
            "action": accion,
        }

    @classmethod
    def _analizar_interaccion_textual(
        cls,
        mensaje: str,
    ) -> tuple[bool, InteractionType | None]:
        """Extrae evidencia por tokens completos; nunca inventa un subtipo."""

        texto = SpatialReasoner.normalizar_texto(mensaje)
        tokens = re.findall(r"[a-z0-9]+", texto)
        tipos = {
            cls.INTERACTION_LEXICON[token]
            for token in tokens
            if token in cls.INTERACTION_LEXICON
        }
        interaction_requested = bool(tipos) or any(
            token in cls.GENERIC_INTERACTION_TOKENS for token in tokens
        )
        if len(tipos) != 1:
            return interaction_requested, None
        return interaction_requested, next(iter(tipos))

    @classmethod
    def _inferir_tipo_interaccion(cls, mensaje: str) -> InteractionType | None:
        """Compatibilidad privada para probes: proyecta el parser canonico."""

        return cls._analizar_interaccion_textual(mensaje)[1]

    @classmethod
    def _canonicalizar_interaccion_estructurada(
        cls,
        parametros: dict[str, Any],
        contexto_motor: dict[str, Any],
    ) -> tuple[bool, InteractionType | None]:
        """Distingue ausencia de contrato presente-invalido antes de grounding."""

        if "tipo_accion" in parametros:
            raw_value = parametros["tipo_accion"]
            if isinstance(raw_value, InteractionType):
                return True, raw_value
            if type(raw_value) is not str:
                return False, None
            try:
                return True, InteractionType(raw_value)
            except ValueError:
                return False, None

        user_query = str(contexto_motor.get("user_query") or "")
        return True, cls._inferir_tipo_interaccion(user_query)

    @staticmethod
    def _parsear_coordenadas(mensaje: str) -> Optional[Dict[str, float]]:
        texto = str(mensaje or "")
        patrones = [
            r"x\s*[:=]?\s*(-?\d+(?:\.\d+)?)\D+y\s*[:=]?\s*(-?\d+(?:\.\d+)?)\D+z\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
            r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)",
        ]
        for patron in patrones:
            match = re.search(patron, texto, flags=re.IGNORECASE)
            if match:
                x, y, z = (float(match.group(i)) for i in range(1, 4))
                return {"x": x, "y": y, "z": z}
        return None
