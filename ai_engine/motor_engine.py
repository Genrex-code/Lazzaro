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

from .motor_schemas import ACTION_SCHEMA_MAP, MotorAction
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
    }

    def __init__(self):
        self.spatial = SpatialReasoner()

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
        accion = self._inferir_accion(mensaje)
        if accion is None:
            return None

        permitidas = set(herramientas_disponibles or self.ACCIONES_MOTORAS)
        if accion not in permitidas:
            logger.info("Motor determinista omitido: %s no esta permitida.", accion)
            return None

        parametros = self._construir_parametros_desde_texto(accion, mensaje, percepcion)
        if parametros is None:
            return None

        return self.procesar_comando_motor(accion, parametros, percepcion)

    def procesar_comando_motor(
        self,
        accion_propuesta: str,
        parametros: Dict[str, Any] | None,
        percepcion: Dict[str, Any] | None,
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

        parametros_limpios = self._enriquecer_parametros(accion, parametros_limpios, percepcion_limpia)

        try:
            params_validados = schema_cls(**parametros_limpios)
        except ValidationError as e:
            logger.warning("Parametros invalidos para '%s': %s", accion, e.errors())
            return self._none("Parametros invalidos. Accion bloqueada por Capa 1.")

        motor_action = MotorAction(
            accion=accion,
            parametros=params_validados.model_dump(exclude_none=True),
            razon=f"Accion '{accion}' validada por Capa 1.",
        )
        logger.debug("MotorAction generada: %s", motor_action.accion)
        return motor_action.model_dump()

    @staticmethod
    def _none(razon: str) -> Dict[str, Any]:
        return MotorAction(accion="none", parametros={}, razon=razon).model_dump()

    def _inferir_accion(self, mensaje: str) -> Optional[str]:
        texto = self.spatial.normalizar_texto(mensaje)
        if not texto:
            return None

        if any(token in texto for token in ("suelta", "soltar", "deja caer")):
            return "soltar_objeto"
        if any(token in texto for token in ("agarra", "coge", "recoge", "sosten")):
            return "agarrar_objeto"
        if any(token in texto for token in ("alejate", "apartate", "retrocede de", "deja de seguir")):
            return "alejarse_de_objeto"
        if any(token in texto for token in ("inspecciona", "interactua", "toca", "mira", "golpea", "usa", "activa")):
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

    def _enriquecer_parametros(
        self,
        accion: str,
        parametros: Dict[str, Any],
        percepcion: Dict[str, Any],
    ) -> Dict[str, Any]:
        if accion not in self.ACCIONES_CON_OBJETO:
            return parametros

        nombre = str(parametros.get("nombre_objeto", "")).strip()
        if not nombre:
            return parametros

        nombre_normalizado = self.spatial.normalizar_texto(nombre)
        if nombre_normalizado in {"mas_cercano", "objeto_mas_cercano", "cercano", "enfrente"}:
            cercano = self._nombre_objeto_mas_cercano(percepcion)
            if cercano:
                parametros["nombre_objeto"] = cercano
            return parametros

        nombre_visible = self._resolver_alias_visible(nombre, percepcion)
        if nombre_visible:
            parametros["nombre_objeto"] = nombre_visible
        return parametros

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

        resumen = percepcion.get("resumen_percepcion")
        if isinstance(resumen, str):
            nombres.extend(self.spatial.extraer_nombres_desde_resumen(resumen))

        nombres_unicos: list[str] = []
        for nombre in nombres:
            if nombre not in nombres_unicos:
                nombres_unicos.append(nombre)
        return nombres_unicos

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
        if "mira" in texto:
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
