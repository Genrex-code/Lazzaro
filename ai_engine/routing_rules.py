"""
routing_rules.py - Reglas heurísticas ampliadas para clasificar intenciones del usuario.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re
from .intent_types import IntentType


@dataclass
class RoutingRule:
    """
    Una regla de enrutamiento basada en palabras clave.
    - keywords: lista de palabras o frases (se buscan como límite de palabra).
    - intent: tipo de intención asignada si coincide.
    - priority: orden de evaluación (menor = más prioritario).
    """
    keywords: List[str]
    intent: IntentType
    priority: int = 10

    def matches(self, text: str) -> bool:
        """Devuelve True si alguna keyword está presente en el texto."""
        text_lower = text.lower()
        for kw in self.keywords:
            # Buscar como palabra completa o subcadena simple
            if re.search(rf"\b{re.escape(kw.lower())}\b", text_lower):
                return True
        return False


# ----------------------------------------------------------------------
# DEFINICIÓN AMPLIADA DE REGLAS
# ----------------------------------------------------------------------
REGLAS_BASE: List[RoutingRule] = [
    # Capa 3 - Oráculo (mayor prioridad)
    RoutingRule(
        keywords=[
            # Conocimiento y educación
            "código", "refactoriza", "arquitectura", "significa", "qué es",
            "explícame", "por qué", "filosofía", "debug", "error", "script",
            "función", "clase", "algoritmo", "compilar", "lenguaje",
            "programación", "csharp", "python", "unity", "unity3d",
            "cómo se hace", "definición", "concepto", "teoría",
            "matemáticas", "física", "historia", "ciencia", "libro",
            "documentación", "tutorial", "ejemplo", "sintaxis",
            "api", "rest", "http", "json", "base de datos", "sql",
            "inteligencia artificial", "machine learning", "deep learning",
            "red neuronal", "modelo", "entrenamiento", "dataset",

            # Resolución de problemas
            "problema", "solución", "solucionar", "arreglar", "fallo",
            "error", "bug", "depurar", "optimizar", "rendimiento",
            "mejorar", "refactor", "reescribir", "analizar", "diagnosticar",

            # Tareas complejas
            "resumen", "resumir", "explicación larga", "informe",
            "documento", "ensayo", "artículo", "trabajo", "investigación",
            "análisis", "proyecto", "plan", "estrategia", "diseño",
            "arquitectura de software", "patrón de diseño",
        ],
        intent=IntentType.ORACLE,
        priority=1
    ),

    # Capa 1 - Motor
    RoutingRule(
        keywords=[
            # Movimiento básico
            "camina", "mueve", "mover", "moverte", "muévete", "ve a", "acércate", "aléjate",
            "retrocede", "avanza", "retrocede", "desplázate", "dirígete",
            "corre", "salta", "gira", "girar", "rotar", "da la vuelta",
            "vuelta", "gira a la izquierda", "gira a la derecha",
            "gira 180", "media vuelta", "camina hacia", "corre hacia",

            # Interacción física
            "inspecciona", "toca", "agarra", "suelta", "interactúa",
            "interactuar", "coge", "recoge", "deja", "sostén", "empuja",
            "tira", "abre", "cierra", "presiona", "pulsa", "activa",
            "desactiva", "enciende", "apaga", "usa", "utiliza",
            "manipula", "golpea", "rompe", "lanza", "deja caer",

            # Navegación avanzada
            "sigue", "persigue", "evade", "esquiva", "rodea", "atraviesa",
            "cruza", "sube", "baja", "trepa", "escala", "navega",
            "explora", "busca", "encuentra", "ve hacia", "dirígete a",
            "ve al punto", "coordenadas", "posición", "destino",
            "waypoint", "ruta", "camino", "sigue el camino",

            # Comandos de velocidad
            "más rápido", "más lento", "despacio", "rápido", "velocidad",
            "corriendo", "caminando", "sigilo", "sigiloso",

            # Los nombres de objetos por si solos NO deben marcar motor.
            # Ej. "reconoces el cubo" es conversacion/percepcion, no movimiento.
        ],
        intent=IntentType.MOTOR_SIMPLE,
        priority=2
    ),

    # Capa 2 - Persona
    RoutingRule(
        keywords=[
            # Saludos y despedidas
            "hola", "buenos días", "buenas tardes", "buenas noches",
            "qué tal", "cómo estás", "cómo te va", "saludos", "hey",
            "buenas", "holi", "buen día", "adiós", "hasta luego",
            "hasta pronto", "nos vemos", "bye", "chao", "adiós",

            # Identidad y autoconocimiento
            "nombre", "apodo", "lazzaro", "lazz", "niña", "chica",
            "pelotita", "quién eres", "cómo te llamas", "edad",
            "cuántos años tienes", "creadora", "creador", "origen",
            "de dónde vienes", "para qué sirves", "qué haces",
            "eres real", "eres consciente", "tienes sentimientos",

            # Emociones y estado
            "sientes", "gusta", "cansada", "cansado", "duele", "bien",
            "mal", "feliz", "triste", "enojada", "enojado", "aburrida",
            "aburrido", "asustada", "asustado", "sorprendida",
            "sorprendido", "emocionada", "emocionado", "preocupada",
            "preocupado", "estresada", "estresado", "relajada",
            "relajado", "cómo te sientes", "qué sientes",
            "estás bien", "estás mal", "te gusta", "te duele",
            "te cansas", "te estresas", "te diviertes",

            # Cortesía y agradecimientos
            "gracias", "muchas gracias", "te agradezco", "por favor",
            "de nada", "disculpa", "perdón", "lo siento", "con permiso",

            # Preferencias y opiniones
            "te gusta", "te gustaría", "prefieres", "qué piensas",
            "qué opinas", "cuál es tu", "tienes preferencia",
            "color favorito", "comida favorita", "pasatiempo",
            "hobby", "música", "película", "juego",

            # Conversación casual
            "cuéntame", "dime", "platica", "conversa", "charla",
            "habla conmigo", "cómo ha ido", "qué has hecho",
            "qué haces ahora", "en qué piensas", "estás ocupada",
            "estás libre", "te interrumpo", "molesto",
        ],
        intent=IntentType.SOCIAL,
        priority=3
    ),

    # Capa 2 - Memoria / Autoconocimiento (prioridad más baja que social pura)
    RoutingRule(
        keywords=[
            "recuerdas", "recuerdo", "memoria", "ayer", "antes",
            "la última vez", "historial", "pasado", "anterior",
            "qué pasó", "qué hiciste", "dónde estabas",
            "has aprendido", "has visto", "has oído",
            "experiencia", "aprendiste", "sabes",
        ],
        intent=IntentType.SOCIAL,  # Se envía a Persona con acceso a memoria
        priority=4
    ),
]

# Ordenar siempre por prioridad
REGLAS_BASE.sort(key=lambda r: r.priority)


def match_intent_by_keywords(texto: str) -> IntentType:
    """
    Evalúa el texto y devuelve la intención con mayor prioridad que coincida.
    Si varias coinciden, gana la de menor prioridad numérica.
    Si ninguna coincide, devuelve UNKNOWN.
    """
    if not texto:
        return IntentType.UNKNOWN

    for rule in REGLAS_BASE:
        if rule.matches(texto):
            return rule.intent
    return IntentType.UNKNOWN
