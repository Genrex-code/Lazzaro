"""
constelacion_cognitiva.py

Modulo independiente para modelar una "constelacion" de nodos cognitivos.
Por ahora no depende del resto del ecosistema de Lazzaro; solo ofrece una
estructura limpia y reutilizable para registrar estados, rasgos, banderas
y relaciones entre ellos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


ValorNodo = float | int | str | bool | None


@dataclass(slots=True)
class NodoCognitivo:
    """Representa una unidad de informacion dentro de la constelacion."""

    nombre: str
    categoria: str
    valor: ValorNodo = None
    descripcion: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def a_dict(self) -> dict[str, Any]:
        """Serializa el nodo a un diccionario simple."""
        return {
            "nombre": self.nombre,
            "categoria": self.categoria,
            "valor": self.valor,
            "descripcion": self.descripcion,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class EnlaceCognitivo:
    """Relacion simple entre dos nodos de la constelacion."""

    origen: str
    destino: str
    relacion: str
    peso: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def a_dict(self) -> dict[str, Any]:
        """Serializa el enlace a un diccionario simple."""
        return {
            "origen": self.origen,
            "destino": self.destino,
            "relacion": self.relacion,
            "peso": self.peso,
            "metadata": dict(self.metadata),
        }


class ConstelacionCognitiva:
    """
    Contenedor principal del modulo.

    Mantiene nodos cognitivos, enlaces y un pequeno bloque de contexto libre
    para guardar metadatos generales del snapshot.
    """

    def __init__(self, nombre: str = "constelacion_cognitiva") -> None:
        self.nombre = nombre
        self._nodos: dict[str, NodoCognitivo] = {}
        self._enlaces: list[EnlaceCognitivo] = []
        self._contexto: dict[str, Any] = {}

    @property
    def nodos(self) -> dict[str, NodoCognitivo]:
        """Devuelve una copia superficial del mapa de nodos."""
        return dict(self._nodos)

    @property
    def enlaces(self) -> list[EnlaceCognitivo]:
        """Devuelve una copia superficial de los enlaces."""
        return list(self._enlaces)

    @property
    def contexto(self) -> dict[str, Any]:
        """Devuelve una copia del contexto general."""
        return dict(self._contexto)

    def esta_vacia(self) -> bool:
        """Indica si la constelacion no contiene nodos."""
        return not self._nodos

    def limpiar(self) -> None:
        """Reinicia por completo la constelacion."""
        self._nodos.clear()
        self._enlaces.clear()
        self._contexto.clear()

    def agregar_nodo(self, nodo: NodoCognitivo) -> NodoCognitivo:
        """Inserta o reemplaza un nodo por nombre."""
        if not nodo.nombre.strip():
            raise ValueError("El nombre del nodo no puede estar vacio.")
        if not nodo.categoria.strip():
            raise ValueError("La categoria del nodo no puede estar vacia.")
        self._nodos[nodo.nombre] = nodo
        return nodo

    def crear_nodo(
        self,
        nombre: str,
        categoria: str,
        valor: ValorNodo = None,
        descripcion: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> NodoCognitivo:
        """Crea y registra un nodo sin exponer la construccion al llamador."""
        nodo = NodoCognitivo(
            nombre=nombre,
            categoria=categoria,
            valor=valor,
            descripcion=descripcion,
            metadata=metadata or {},
        )
        return self.agregar_nodo(nodo)

    def obtener_nodo(self, nombre: str) -> NodoCognitivo | None:
        """Recupera un nodo por nombre si existe."""
        return self._nodos.get(nombre)

    def eliminar_nodo(self, nombre: str) -> bool:
        """
        Elimina un nodo y purga los enlaces que lo referencian.

        Retorna True si el nodo existia, False en caso contrario.
        """
        if nombre not in self._nodos:
            return False

        del self._nodos[nombre]
        self._enlaces = [
            enlace
            for enlace in self._enlaces
            if enlace.origen != nombre and enlace.destino != nombre
        ]
        return True

    def conectar(
        self,
        origen: str,
        destino: str,
        relacion: str,
        peso: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> EnlaceCognitivo:
        """Crea un enlace entre dos nodos existentes."""
        if origen not in self._nodos:
            raise KeyError(f"El nodo de origen '{origen}' no existe.")
        if destino not in self._nodos:
            raise KeyError(f"El nodo de destino '{destino}' no existe.")
        if not relacion.strip():
            raise ValueError("La relacion del enlace no puede estar vacia.")

        enlace = EnlaceCognitivo(
            origen=origen,
            destino=destino,
            relacion=relacion,
            peso=float(peso),
            metadata=metadata or {},
        )
        self._enlaces.append(enlace)
        return enlace

    def actualizar_contexto(self, **valores: Any) -> None:
        """Fusiona valores en el contexto general del snapshot."""
        self._contexto.update(valores)

    def a_dict(self) -> dict[str, Any]:
        """Convierte toda la constelacion a un diccionario serializable."""
        return {
            "nombre": self.nombre,
            "contexto": dict(self._contexto),
            "nodos": [nodo.a_dict() for nodo in self._nodos.values()],
            "enlaces": [enlace.a_dict() for enlace in self._enlaces],
        }

    def a_json(self, indent: int = 2) -> str:
        """Convierte la constelacion a JSON legible."""
        return json.dumps(self.a_dict(), ensure_ascii=False, indent=indent)

    def resumen(self) -> str:
        """Resumen corto para depuracion o logs."""
        return (
            f"{self.nombre}: "
            f"{len(self._nodos)} nodos, "
            f"{len(self._enlaces)} enlaces, "
            f"{len(self._contexto)} claves de contexto"
        )

    @classmethod
    def desde_estado_bruto(
        cls,
        estado: dict[str, ValorNodo] | None = None,
        rasgos: dict[str, ValorNodo] | None = None,
        banderas: dict[str, ValorNodo] | None = None,
        contexto: dict[str, Any] | None = None,
        nombre: str = "constelacion_cognitiva",
    ) -> "ConstelacionCognitiva":
        """
        Construye una constelacion a partir de diccionarios simples.

        Esto permite usar el modulo ya mismo, aunque el resto del ecosistema
        aun no este acoplado a el.
        """
        constelacion = cls(nombre=nombre)

        for categoria, bloque in (
            ("estado", estado or {}),
            ("rasgo", rasgos or {}),
            ("bandera", banderas or {}),
        ):
            for clave, valor in bloque.items():
                constelacion.crear_nodo(
                    nombre=f"{categoria}:{clave}",
                    categoria=categoria,
                    valor=valor,
                    descripcion=f"{categoria} '{clave}'",
                )

        if contexto:
            constelacion.actualizar_contexto(**contexto)

        return constelacion


__all__ = [
    "ConstelacionCognitiva",
    "EnlaceCognitivo",
    "NodoCognitivo",
    "ValorNodo",
]
