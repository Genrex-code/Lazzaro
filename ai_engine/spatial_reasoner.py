"""
spatial_reasoner.py - Razonamiento geometrico puro, sin llamadas a IA.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Optional

from core.logger import get_logger

logger = get_logger(__name__)


class SpatialReasoner:
    """Realiza calculos espaciales basados en la percepcion de Unity."""

    @staticmethod
    def normalizar_texto(texto: object) -> str:
        """Normaliza acentos y mayusculas para comparaciones robustas."""
        valor = str(texto or "").strip().lower()
        valor = unicodedata.normalize("NFD", valor)
        return "".join(char for char in valor if unicodedata.category(char) != "Mn")

    @staticmethod
    def _extract_position(obj: Dict[str, Any] | Any) -> Optional[Dict[str, float]]:
        """
        Extrae {x, y, z} de un objeto con coordenadas planas o dentro de 'posicion'.
        Devuelve None si el objeto no tiene forma de diccionario o no trae coordenadas.
        """
        if not isinstance(obj, dict):
            return None

        try:
            if all(k in obj for k in ("x", "y", "z")):
                return {k: float(obj[k]) for k in ("x", "y", "z")}

            pos = obj.get("posicion")
            if isinstance(pos, dict) and all(k in pos for k in ("x", "y", "z")):
                return {k: float(pos[k]) for k in ("x", "y", "z")}
        except (TypeError, ValueError):
            logger.warning("Coordenadas invalidas en objeto espacial: %r", obj)
        return None

    @staticmethod
    def calcular_distancia(pos1: Dict[str, Any], pos2: Dict[str, Any]) -> float:
        """Distancia euclidiana 3D entre dos puntos."""
        p1 = SpatialReasoner._extract_position(pos1)
        p2 = SpatialReasoner._extract_position(pos2)
        if not p1 or not p2:
            logger.warning("No se pudieron extraer coordenadas para calcular distancia.")
            return 9999.0
        return math.sqrt(
            (p2["x"] - p1["x"]) ** 2
            + (p2["y"] - p1["y"]) ** 2
            + (p2["z"] - p1["z"]) ** 2
        )

    @staticmethod
    def normalizar_lista_objetos(objetos_visibles: Any) -> List[Dict[str, Any]]:
        """Filtra la percepcion a una lista segura de diccionarios de objetos."""
        if not isinstance(objetos_visibles, list):
            return []
        return [obj for obj in objetos_visibles if isinstance(obj, dict)]

    def obtener_objeto_por_nombre(
        self,
        nombre: str,
        objetos_visibles: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Busca un objeto por nombre o coincidencia parcial, ignorando acentos."""
        objetivo = self.normalizar_texto(nombre)
        if not objetivo:
            return None

        for obj in self.normalizar_lista_objetos(objetos_visibles):
            nombre_obj = self.normalizar_texto(obj.get("nombre", ""))
            if objetivo == nombre_obj or objetivo in nombre_obj or nombre_obj in objetivo:
                return obj
        return None

    def calcular_punto_medio(
        self,
        obj1: Dict[str, Any],
        obj2: Dict[str, Any],
    ) -> Optional[Dict[str, float]]:
        """Devuelve las coordenadas exactas del punto medio entre dos objetos."""
        p1 = self._extract_position(obj1)
        p2 = self._extract_position(obj2)
        if not p1 or not p2:
            return None
        return {
            "x": round((p1["x"] + p2["x"]) / 2.0, 2),
            "y": round((p1["y"] + p2["y"]) / 2.0, 2),
            "z": round((p1["z"] + p2["z"]) / 2.0, 2),
        }

    def obtener_objeto_mas_cercano(
        self,
        pos_lazzaro: Dict[str, float],
        objetos_visibles: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Encuentra el objeto con menor distancia a la posicion de Lazzaro."""
        objetos = self.normalizar_lista_objetos(objetos_visibles)
        if not objetos:
            return None

        objeto_cercano = None
        distancia_minima = float("inf")
        for obj in objetos:
            if self._extract_position(obj) is None:
                continue
            distancia = self.calcular_distancia(pos_lazzaro, obj)
            if distancia < distancia_minima:
                distancia_minima = distancia
                objeto_cercano = obj
        return objeto_cercano

    def extraer_nombres_desde_resumen(self, resumen: str) -> list[str]:
        """
        Extrae nombres desde resumenes tipo:
        'Veo Capsule a 1.8m al norte, Cube a 9.7m al oeste.'
        """
        texto = str(resumen or "")
        texto = re.sub(r"^\s*(?:frente\s+a\s+mi\s+veo:|veo)\s*", "", texto, flags=re.IGNORECASE)
        nombres: list[str] = []
        for match in re.finditer(r"(?:,\s*)?([^,.]+?)\s+a\s+[\d.]+\s*m\b", texto, re.IGNORECASE):
            nombre = match.group(1).strip()
            if nombre and nombre not in nombres:
                nombres.append(nombre)
        return nombres
