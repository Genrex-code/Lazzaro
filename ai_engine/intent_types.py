"""
intent_types.py - Taxonomia formal de intenciones y rutas para el Triage Cognitivo.
"""
from enum import Enum

class IntentType(str, Enum):
    """
    Clasificacion de la intencion del evento entrante.
    Determina que capa cognitiva debe procesar la solicitud.
    """
    SOCIAL = "SOCIAL"               # Conversacion, identidad, saludos
    MOTOR_SIMPLE = "MOTOR_SIMPLE"   # Comandos de movimiento
    MOTOR_REFERENCE = "MOTOR_REFERENCE" # Comandos de movimiento relativos
    PERCEPTION = "PERCEPTION"       # Consultas sobre el entorno visual ("que ves?")
    MEMORY_QUERY = "MEMORY_QUERY"   # Consultas sobre el pasado o identidad
    MEMORY_UPDATE = "MEMORY_UPDATE" # Peticiones explicitas de recordar algo
    CORE_SYSTEM = "CORE_SYSTEM"     # Descanso, sistema, dolor fisico, inicio de simulacion
    ORACLE_QUERY = "ORACLE_QUERY"   # Preguntas profundas, internet, datos externos
    INTERNAL = "INTERNAL"           # Eventos internos (resultados de acciones)
    NOOP = "NOOP"                   # Telemetria pasiva, ignorar
    UNKNOWN = "UNKNOWN"             # Fallback cuando el ML y las reglas fallan

class Route(str, Enum):
    """
    Destino de la solicitud dentro del sistema multicapa.
    """
    MOTOR = "motor"         # Capa 1 - Cerebro motor y espacial
    PERSONA = "persona"     # Capa 2 - Personalidad, memoria, emociones
    ORACLE = "oracle"       # Capa 3 - Cortex / Oraculo pesado
    INTERNAL = "internal"   # Procesamiento interno sin LLM
    NOOP = "noop"           # Sin accion requerida

# MAPEO POR DEFECTO DE INTENCION ===> RUTA
INTENT_TO_ROUTE = {
    IntentType.SOCIAL: Route.PERSONA,
    IntentType.MOTOR_SIMPLE: Route.MOTOR,
    IntentType.MOTOR_REFERENCE: Route.MOTOR,
    IntentType.PERCEPTION: Route.MOTOR,
    IntentType.MEMORY_QUERY: Route.PERSONA,
    IntentType.MEMORY_UPDATE: Route.PERSONA,
    IntentType.CORE_SYSTEM: Route.PERSONA,
    IntentType.ORACLE_QUERY: Route.ORACLE,
    IntentType.INTERNAL: Route.INTERNAL,
    IntentType.NOOP: Route.NOOP,
    IntentType.UNKNOWN: Route.PERSONA  # Fallback: La Capa 2 maneja lo desconocido
}