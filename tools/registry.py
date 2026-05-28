"""
tools/registry.py - Catalogo central de herramientas para Lazzaro.
Utiliza decoradores para registrar automaticamente cualquier funcion
como una herramienta disponible para el LLM.
"""

from typing import Any, Callable, Dict, Optional, Type

from pydantic import BaseModel

from core.logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:
    """
    Registro en memoria de todas las herramientas disponibles.
    Mapea el nombre de la herramienta con su funcion ejecutable y su esquema.
    """

    _tools: Dict[str, dict] = {}

    @staticmethod
    def _schema_dict(schema_model: Type[BaseModel]) -> dict[str, Any]:
        if hasattr(schema_model, "model_json_schema"):
            return schema_model.model_json_schema()
        return schema_model.schema()

    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        params_schema: Type[BaseModel],
        requires_confirmation: bool = False,
    ):
        """Decorador para registrar una nueva herramienta."""

        def decorator(func: Callable):
            if name in cls._tools:
                logger.warning("Sobrescribiendo herramienta ya registrada: %s", name)

            cls._tools[name] = {
                "func": func,
                "description": description,
                "schema": params_schema,
                "requires_confirmation": requires_confirmation,
            }
            logger.debug("Herramienta registrada: %s", name)
            return func

        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Optional[Dict[str, Any]]:
        """Devuelve la configuracion de una herramienta por su nombre."""
        return cls._tools.get(name)

    @classmethod
    def get_all_tools_info(cls) -> list[str]:
        """
        Genera la lista de herramientas en formato texto para inyectarla
        en el prompt del LLM.
        """
        info_list = []
        for name, data in cls._tools.items():
            schema_fields = cls._schema_dict(data["schema"]).get("properties", {})
            params_desc = ", ".join(f"{k} ({v.get('type', 'any')})" for k, v in schema_fields.items())
            info_list.append(f"- {name}: {data['description']} | Parametros: {{{params_desc}}}")
        return info_list

    @classmethod
    def listar_nombres(cls) -> list[str]:
        """Devuelve los nombres de todas las herramientas registradas."""
        return list(cls._tools.keys())

    @classmethod
    def get_tools_for_prompt(cls) -> str:
        """Genera un texto con las herramientas y sus parametros para el prompt del LLM."""
        lineas = []
        for name, data in cls._tools.items():
            schema = cls._schema_dict(data["schema"])
            props = schema.get("properties", {})
            params = []
            for pname, pinfo in props.items():
                tipo = pinfo.get("type", "any")
                desc = pinfo.get("description", "")
                params.append(f"{pname}: {tipo} ({desc})")
            linea = f"- {name}: {data['description']}\n  Parametros: {{{', '.join(params)}}}"
            lineas.append(linea)
        return "\n".join(lineas)
