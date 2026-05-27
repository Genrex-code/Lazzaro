"""
llm_policies.py - Políticas que modifican el comportamiento del LLM.
Ajustan parámetros como temperatura, herramientas disponibles y restricciones
según la política activa. No deben confundirse con las políticas de comportamiento
del core (behavior_core.py), que afectan al estado emocional y la percepción.
"""

from typing import Optional, List
from core.logger import get_logger

logger = get_logger(__name__)

# Políticas disponibles con sus configuraciones por defecto
POLICY_CONFIGS = {
    "cautelosa": {
        "temperature": 0.4,
        "max_tokens": 512,
        "permitir_herramientas_riesgosas": False,
        "autonomia": "baja",
        "descripcion": "Evita riesgos, prefiere pedir ayuda, responde con pasos seguros."
    },
    "exploradora": {
        "temperature": 0.9,
        "max_tokens": 1024,
        "permitir_herramientas_riesgosas": True,
        "autonomia": "alta",
        "descripcion": "Curiosidad elevada, prueba herramientas nuevas, mayor creatividad."
    },
    "eficiente": {
        "temperature": 0.5,
        "max_tokens": 768,
        "permitir_herramientas_riesgosas": False,
        "autonomia": "media",
        "descripcion": "Busca la solución más rápida y con menos pasos, minimiza divagaciones."
    },
    "social": {
        "temperature": 0.7,
        "max_tokens": 1024,
        "permitir_herramientas_riesgosas": False,
        "autonomia": "media",
        "descripcion": "Prefiere interacción, pide confirmación antes de actuar, tono amigable."
    },
    "autonoma": {
        "temperature": 0.8,
        "max_tokens": 1024,
        "permitir_herramientas_riesgosas": True,
        "autonomia": "maxima",
        "descripcion": "Máxima autonomía, toma decisiones sin preguntar, asume riesgos calculados."
    }
}

# Lista de políticas válidas
POLITICAS_DISPONIBLES = list(POLICY_CONFIGS.keys())


class LLMPolicyManager:
    """
    Gestiona las políticas del LLM. Dado un nombre de política, devuelve
    la configuración correspondiente (temperatura, tokens, restricciones).
    """

    @staticmethod
    def obtener_configuracion(nombre_politica: str) -> dict:
        """
        Retorna un diccionario con los parámetros de la política solicitada.
        Si no existe, devuelve la política por defecto ('exploradora').
        """
        if nombre_politica not in POLICY_CONFIGS:
            logger.warning("Política '%s' no encontrada. Usando 'exploradora'.", nombre_politica)
            nombre_politica = "exploradora"
        return POLICY_CONFIGS[nombre_politica].copy()

    @staticmethod
    def ajustar_parametros(
        nombre_politica: str,
        herramientas: List[str],
        herramientas_riesgosas: Optional[List[str]] = None
    ) -> dict:
        """
        Ajusta los parámetros de generación y filtra las herramientas según la política.

        Args:
            nombre_politica: nombre de la política activa.
            herramientas: lista completa de herramientas disponibles.
            herramientas_riesgosas: lista de herramientas consideradas de riesgo.

        Returns:
            Diccionario con:
                - 'generation': dict con temperature, max_tokens, etc.
                - 'herramientas_filtradas': list de herramientas permitidas.
                - 'autonomia': str indicando el nivel de autonomía.
        """
        config = LLMPolicyManager.obtener_configuracion(nombre_politica)
        gen_params = {
            "temperature": config["temperature"],
            "max_output_tokens": config["max_tokens"],
            "response_mime_type": "application/json",
        }

        # Filtrar herramientas según riesgo
        if not config["permitir_herramientas_riesgosas"] and herramientas_riesgosas:
            herramientas_filtradas = [
                h for h in herramientas
                if h not in herramientas_riesgosas
            ]
        else:
            herramientas_filtradas = list(herramientas)

        return {
            "generation": gen_params,
            "herramientas_filtradas": herramientas_filtradas,
            "autonomia": config["autonomia"],
            "descripcion": config["descripcion"]
        }
