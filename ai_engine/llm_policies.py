"""
llm_policies.py - Politicas que modifican el comportamiento del LLM.
"""

from typing import List, Optional

from core.logger import get_logger

logger = get_logger(__name__)


POLICY_CONFIGS = {
    "cautelosa": {
        "temperature": 0.4,
        "max_tokens": 512,
        "permitir_herramientas_riesgosas": False,
        "autonomia": "baja",
        "descripcion": "Evita riesgos, prefiere pedir ayuda, responde con pasos seguros.",
    },
    "exploradora": {
        "temperature": 0.9,
        "max_tokens": 1024,
        "permitir_herramientas_riesgosas": True,
        "autonomia": "alta",
        "descripcion": "Curiosidad elevada, prueba herramientas nuevas, mayor creatividad.",
    },
    "eficiente": {
        "temperature": 0.5,
        "max_tokens": 768,
        "permitir_herramientas_riesgosas": False,
        "autonomia": "media",
        "descripcion": "Busca la solucion mas rapida y con menos pasos, minimiza divagaciones.",
    },
    "social": {
        "temperature": 0.7,
        "max_tokens": 1024,
        "permitir_herramientas_riesgosas": False,
        "autonomia": "media",
        "descripcion": "Prefiere interaccion, pide confirmacion antes de actuar, tono amigable.",
    },
    "autonoma": {
        "temperature": 0.8,
        "max_tokens": 1024,
        "permitir_herramientas_riesgosas": True,
        "autonomia": "maxima",
        "descripcion": "Maxima autonomia, toma decisiones sin preguntar, asume riesgos calculados.",
    },
}

POLITICAS_DISPONIBLES = list(POLICY_CONFIGS.keys())


class LLMPolicyManager:
    """Gestiona las politicas del LLM."""

    @staticmethod
    def obtener_configuracion(nombre_politica: str) -> dict:
        if nombre_politica not in POLICY_CONFIGS:
            logger.warning("Politica '%s' no encontrada. Usando 'exploradora'.", nombre_politica)
            nombre_politica = "exploradora"
        return POLICY_CONFIGS[nombre_politica].copy()

    @staticmethod
    def ajustar_parametros(
        nombre_politica: str,
        herramientas: List[str],
        herramientas_riesgosas: Optional[List[str]] = None,
        proveedor: str = "generic",
    ) -> dict:
        config = LLMPolicyManager.obtener_configuracion(nombre_politica)
        gen_params = {
            "temperature": config["temperature"],
            "max_output_tokens": config["max_tokens"],
            "response_mime_type": "application/json",
        }

        if "ollama" in proveedor.lower():
            gen_params["temperature"] = min(float(gen_params["temperature"]), 0.35)
            gen_params["top_p"] = 0.9
            gen_params["top_k"] = 30

        if not config["permitir_herramientas_riesgosas"] and herramientas_riesgosas:
            herramientas_filtradas = [h for h in herramientas if h not in herramientas_riesgosas]
        else:
            herramientas_filtradas = list(herramientas)

        return {
            "generation": gen_params,
            "herramientas_filtradas": herramientas_filtradas,
            "autonomia": config["autonomia"],
            "descripcion": config["descripcion"],
        }
