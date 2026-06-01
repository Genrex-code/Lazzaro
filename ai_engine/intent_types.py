"""
intent_types.py - taxonomia formal de intenciones y rutas para el Triage Cognitivo.
"""

from enum import Enum

class IntentType(str,Enum):
    """
    clasificicion de la intencion del evento entrante.
    determina que capa cognitiva debe procesar la solicitud.
    """
    
    SOCIAL = "SOCIAL"  # Conversacion,identidad,saludos
    MOTOR_SIMPLE = "MOTOR_SIMPLE" # Comandos de movimiento o acciones directas
    MOTOR_COMPLEJO = "MOTOR_COMPLEJO" # Tareas que requieren planificacion o cadenas de comandos, navegacion multipaso, mantener pestañas simultaneas etc
    ORACLE = "ORACLE" # Preguntas que requieren conocimiento profundo, razonamiento complejo o acceso a datos externos
    INTERNAL = "INTERNAL" # Eventos internos (resultados de acciones, telemetria)
    NOOP = "NOOP" # SIN ACCION REQUERIDA
    UNKNOWN = "UNKNOWN" # No se pudo determinar la intencion ( ahora si le puse k :3)


class Route(str,Enum):
    """Destino de la solicitud dentro del sistema multicapa"""
    MOTOR = "motor" # capa 1 - cerebro motor
    PERSONA = "persona" # capa 2 - personalidad, memoria, emociones
    ORACLE = "oracle" # capa 3 - Cortex / oraculo
    INTERNAL = "internal" # PROSESAMIENTO INTERNO SIN LLM (core/orquestador)
    NOOP = "noop" # SIN ACCION REQUERIDA

#MAPEO POR DEFECTO DE INTENCION --- > RUTA
INTENT_TO_ROUTE = {
    IntentType.SOCIAL: Route.PERSONA,
    IntentType.MOTOR_SIMPLE: Route.MOTOR,
    IntentType.MOTOR_COMPLEJO: Route.MOTOR,
    IntentType.ORACLE: Route.ORACLE,
    IntentType.INTERNAL: Route.INTERNAL,
    IntentType.NOOP: Route.NOOP,
    IntentType.UNKNOWN: Route.PERSONA,  # Fallback: la Capa 2 maneja lo desconocido
}