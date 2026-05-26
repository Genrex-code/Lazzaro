"""
tools/registry.py - Catálogo central de herramientas para Lazzaro.
Utiliza decoradores para registrar automáticamente cualquier función
como una herramienta disponible para el LLM.
"""

from typing import Callable, Dict, Any, Type,Optional
from pydantic import BaseModel
from core.logger import get_logger

logger = get_logger(__name__)

class ToolRegistry:
    """
    Registro en memoria de todas las herramientas disponibles.
    Mapea el nombre de la herramienta con su función ejecutable y su esquema de validación.
    """
    
    _tools: Dict[str, dict] = {}

    @classmethod
    def register(
        cls, 
        name: str, 
        description: str, 
        params_schema: Type[BaseModel], 
        requires_confirmation: bool = False
    ):
        """
        Decorador para registrar una nueva herramienta.
        """
        def decorator(func: Callable):
            if name in cls._tools:
                logger.warning("Sobrescribiendo herramienta ya registrada: %s", name)
            
            cls._tools[name] = {
                "func": func,
                "description": description,
                "schema": params_schema,
                "requires_confirmation": requires_confirmation
            }
            logger.debug("Herramienta registrada: %s", name)
            return func
        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Optional[Dict[str, Any]]:
        """Devuelve la configuración de una herramienta por su nombre."""
        return cls._tools.get(name)

    @classmethod
    def get_all_tools_info(cls) -> list[str]:
        """
        Genera la lista de herramientas en formato texto para inyectarla 
        en el tool_prompt.txt del LLM.
        """
        info_list = []
        for name, data in cls._tools.items():
            schema_fields = data["schema"].schema().get("properties", {})
            params_desc = ", ".join([f"{k} ({v.get('type', 'any')})" for k, v in schema_fields.items()])
            info_list.append(f"- {name}: {data['description']} | Parámetros: {{{params_desc}}}")
        return info_list

    @classmethod
    def listar_nombres(cls) -> list[str]:
        """Devuelve los nombres de todas las herramientas registradas."""
        return list(cls._tools.keys())

    @classmethod
    def get_tools_for_prompt(cls) -> str:
        """Genera un texto con las herramientas y sus parámetros para el prompt del LLM."""
        lineas = []
        for name, data in cls._tools.items():
            schema = data["schema"].schema()
            props = schema.get("properties", {})
            params = []
            for pname, pinfo in props.items():
                tipo = pinfo.get("type", "any")
                desc = pinfo.get("description", "")
                params.append(f"{pname}: {tipo} ({desc})")
            linea = f"- {name}: {data['description']}\n  Parámetros: {{{', '.join(params)}}}"
            lineas.append(linea)
        return "\n".join(lineas)

    
