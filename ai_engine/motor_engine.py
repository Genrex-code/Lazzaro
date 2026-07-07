"""
motor_engine.py - Capa 1 (Cerebro Motor).

El motor es frio y determinista: valida acciones fisicas, resuelve comandos
simples sin LLM cuando es seguro, y nunca maneja personalidad ni conversacion.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from pydantic import ValidationError

from core.logger import get_logger

from .fuzzy_object_resolver import FuzzyObjectResolver
from .motor_schemas import ACTION_SCHEMA_MAP, MotorAction
from .motor_resolution import MotorResolution
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
        if diagnostico["estado"] != "lista":
            return None

        return self.procesar_comando_motor(
            str(diagnostico["accion"]),
            diagnostico["parametros"],
            percepcion,
        )

    def diagnosticar_comando_usuario(
        self,
        mensaje: str,
        percepcion: Dict[str, Any],
        herramientas_disponibles: Iterable[str] | None = None,
        intent: str = "MOTOR_SIMPLE",
        contexto_motor: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Clasifica un comando motor sin llamar al LLM.
        Permite distinguir entre "no es motor" y "es motor, pero falta objetivo".
        """
        accion = self._inferir_accion(mensaje)
        if accion is None:
            return {"estado": "sin_intencion", "accion": None, "parametros": None}

        permitidas = set(herramientas_disponibles or self.ACCIONES_MOTORAS)
        if accion not in permitidas:
            logger.info("Motor determinista omitido: %s no esta permitida.", accion)
            return {"estado": "no_permitida", "accion": accion, "parametros": None}

        if accion in self.ACCIONES_CON_OBJETO:
            return self._diagnosticar_accion_con_objeto(
                accion=accion,
                mensaje=mensaje,
                percepcion=percepcion,
                intent=intent,
                contexto_motor=contexto_motor or {},
            )

        parametros = self._construir_parametros_desde_texto(accion, mensaje, percepcion)
        if parametros is None:
            return {"estado": "ambigua", "accion": accion, "parametros": None}

        return {"estado": "lista", "accion": accion, "parametros": parametros}

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

        parametros_limpios, metadata = self._enriquecer_parametros(
            accion,
            parametros_limpios,
            percepcion_limpia,
            intent=intent,
            contexto_motor=contexto_motor or {},
        )
        if metadata.get("motor_resolution", {}).get("status") not in {None, "ready"}:
            estado = str(metadata.get("motor_resolution", {}).get("status", "none"))
            return self._none(
                f"Resolucion motora '{estado}' bloqueo la accion.",
                metadata=metadata,
            )

        try:
            params_validados = schema_cls(**parametros_limpios)
        except ValidationError as e:
            logger.warning("Parametros invalidos para '%s': %s", accion, e.errors())
            return self._none("Parametros invalidos. Accion bloqueada por Capa 1.", metadata=metadata)

        motor_action = MotorAction(
            accion=accion,
            parametros=params_validados.model_dump(exclude_none=True),
            razon=f"Accion '{accion}' validada por Capa 1.",
            metadata=metadata,
        )
        logger.debug("MotorAction generada: %s", motor_action.accion)
        return motor_action.model_dump()

    @staticmethod
    def _none(razon: str, metadata: dict[str, Any] | None = None) -> Dict[str, Any]:
        return MotorAction(accion="none", parametros={}, razon=razon, metadata=metadata or {}).model_dump()

    def _inferir_accion(self, mensaje: str) -> Optional[str]:
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
        if any(token in texto for token in ("inspecciona", "interactua", "toca", "mira", "observa", "golpea", "usa", "activa")):
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
            if accion == "interactuar_objeto_3d":
                parametros["tipo_accion"] = self._inferir_tipo_interaccion(mensaje)
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
    ) -> Dict[str, Any]:
        resolucion = self._resolver_objetivo_fuzzy(
            query=mensaje,
            accion=accion,
            percepcion=percepcion,
            intent=intent,
            contexto_motor=contexto_motor,
        )
        if resolucion is not None:
            metadata = resolucion.to_metadata()
            self._adjuntar_contexto_alias(metadata, contexto_motor, accion)
            if resolucion.status == "ready":
                parametros = self._parametros_desde_resolucion(accion, mensaje, resolucion)
                return {
                    "estado": "lista",
                    "accion": accion,
                    "parametros": parametros,
                    "metadata": metadata,
                }

            parametros_legacy = self._construir_parametros_legacy_con_objeto(accion, mensaje, percepcion)
            if parametros_legacy is not None and self._puede_usar_fallback_legacy(resolucion):
                return {"estado": "lista", "accion": accion, "parametros": parametros_legacy, "metadata": {}}

            return {
                "estado": resolucion.status,
                "accion": accion,
                "parametros": None,
                "metadata": metadata,
                "resolucion_motor": resolucion.to_dict(),
            }

        parametros = self._construir_parametros_legacy_con_objeto(accion, mensaje, percepcion)
        if parametros is None:
            logger.info("Motor determinista no encontro objeto objetivo en: %s", mensaje)
            return {"estado": "falta_objetivo", "accion": accion, "parametros": None}

        return {"estado": "lista", "accion": accion, "parametros": parametros, "metadata": {}}

    def _construir_parametros_legacy_con_objeto(
        self,
        accion: str,
        mensaje: str,
        percepcion: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        nombre = self._extraer_objeto_objetivo(mensaje, percepcion)
        if not nombre:
            return None

        parametros: Dict[str, Any] = {"nombre_objeto": nombre}
        if accion == "alejarse_de_objeto":
            parametros["distancia"] = 2.0
        if accion == "interactuar_objeto_3d":
            parametros["tipo_accion"] = self._inferir_tipo_interaccion(mensaje)
        return parametros

    def _parametros_desde_resolucion(
        self,
        accion: str,
        mensaje: str,
        resolucion: MotorResolution,
    ) -> Dict[str, Any]:
        nombre = resolucion.legacy_nombre_objeto or resolucion.target_display_name or resolucion.target_id or ""
        parametros: Dict[str, Any] = {"nombre_objeto": nombre}
        if accion == "alejarse_de_objeto":
            parametros["distancia"] = 2.0
        if accion == "interactuar_objeto_3d":
            parametros["tipo_accion"] = self._inferir_tipo_interaccion(mensaje)
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
            if self._puede_usar_fallback_legacy(resolucion):
                nombre_visible = self._resolver_alias_visible(nombre, percepcion)
                if nombre_visible:
                    parametros["nombre_objeto"] = nombre_visible
                    return parametros, {}
            return parametros, metadata

        nombre_normalizado = self.spatial.normalizar_texto(nombre)
        if nombre_normalizado in {"mas_cercano", "objeto_mas_cercano", "cercano", "enfrente"}:
            cercano = self._nombre_objeto_mas_cercano(percepcion)
            if cercano:
                parametros["nombre_objeto"] = cercano
            return parametros, metadata

        nombre_visible = self._resolver_alias_visible(nombre, percepcion)
        if nombre_visible:
            parametros["nombre_objeto"] = nombre_visible
        return parametros, metadata

    def _resolver_objetivo_fuzzy(
        self,
        *,
        query: str,
        accion: str,
        percepcion: Dict[str, Any],
        intent: str,
        contexto_motor: dict[str, Any],
    ) -> MotorResolution | None:
        if not self._percepcion_tiene_objetos_normalizados(percepcion):
            return None

        try:
            return self.fuzzy_resolver.resolve(
                query,
                percepcion,
                intent=intent,
                action=accion,
                context=contexto_motor,
            )
        except Exception as exc:
            logger.warning("FuzzyObjectResolver fallo; se conserva fallback legacy: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _percepcion_tiene_objetos_normalizados(percepcion: Dict[str, Any]) -> bool:
        if not isinstance(percepcion, dict):
            return False
        for key in ("objetos_normalizados", "objects", "perceived_objects"):
            value = percepcion.get(key)
            if isinstance(value, list) and value:
                return True
        return False

    @staticmethod
    def _puede_usar_fallback_legacy(resolucion: MotorResolution) -> bool:
        if resolucion.status not in {"not_found", "requires_clarification"}:
            return False
        return len(resolucion.candidates) <= 1

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

    @staticmethod
    def _inferir_tipo_interaccion(mensaje: str) -> str:
        texto = SpatialReasoner.normalizar_texto(mensaje)
        if "golpea" in texto:
            return "golpear"
        if "toca" in texto:
            return "tocar"
        if "usa" in texto:
            return "usar"
        if "activa" in texto:
            return "activar"
        if "mira" in texto or "observa" in texto:
            return "mirar"
        return "inspeccionar"

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
