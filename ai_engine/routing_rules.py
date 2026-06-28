"""
routing_rules.py - Reglas heurísticas ampliadas para clasificar intenciones del usuario.
"""

from dataclasses import dataclass
from typing import List
import re
import unicodedata
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

    @staticmethod
    def _normalizar(text: str) -> str:
        """Normaliza acentos y mayusculas para comparar comandos robustamente."""
        text = unicodedata.normalize("NFD", str(text or "").lower())
        return "".join(char for char in text if unicodedata.category(char) != "Mn")

    def matches(self, text: str) -> bool:
        """Devuelve True si alguna keyword está presente en el texto."""
        text_lower = self._normalizar(text)
        for kw in self.keywords:
            # Buscar como palabra completa o subcadena simple
            kw_normalizada = self._normalizar(kw)
            if re.search(rf"\b{re.escape(kw_normalizada)}\b", text_lower):
                return True
        return False

# DEFINICIÓN AMPLIADA DE REGLAS
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
        intent=IntentType.ORACLE_QUERY,
        priority=1
    ),

    # Capa 1 - Percepcion / vision. Va antes de motor para que preguntas
    # como "que ves?" no se interpreten como orden fisica.
    RoutingRule(
        keywords=[
            "que ves", "que puedes ver", "puedes ver", "ves algo",
            "ves la", "ves el", "hay algo", "que hay", "que tienes delante",
            "objetos visibles", "percibes", "detectas",
            "reconoces", "identificas", "que observas",
        ],
        intent=IntentType.PERCEPTION,
        priority=2,
    ),

    # Capa 1 - Motor
    RoutingRule(
        keywords=[
            # Movimiento básico
            "camina", "mueve", "mover", "moverte", "muévete", "ve a", "acércate", "aléjate",
            "alejarme", "alejarte", "alejarse", "alejar", "aleja", "apártate",
            "retrocede", "retírate", "sepárate", "separate", "avanza", "retrocede", "desplázate", "dirígete",
            "corre", "salta", "gira", "girar", "rotar", "da la vuelta",
            "vuelta", "gira a la izquierda", "gira a la derecha",
            "gira 180", "media vuelta", "camina hacia", "corre hacia",
            "rota", "rotacion", "voltea", "mira alrededor", "mirar alrededor",
            "observa alrededor", "reorienta", "reorientate", "reorientarte",

            # Interacción física
            "inspecciona", "toca", "agarra", "suelta", "interactúa",
            "interactuar", "interactua", "coge", "recoge", "deja", "sostén", "empuja",
            "tira", "abre", "cierra", "presiona", "pulsa", "activa",
            "desactiva", "enciende", "apaga", "usa", "utiliza",
            "manipula", "golpea", "rompe", "lanza", "deja caer",
            "mira la", "mira el", "mira al", "mira hacia",
            "mirar la", "mirar el", "observa la", "observa el",

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
        priority=3
    ),

    # Capa 2 - Memoria / escritura explicita
    RoutingRule(
        keywords=[
            "recuerda que", "recuerdame que", "guarda que", "memoriza",
            "no olvides", "aprende que", "ten presente", "acuerdate de",
            "quiero que recuerdes",
        ],
        intent=IntentType.MEMORY_UPDATE,
        priority=4,
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
        priority=6
    ),

    # Capa 2 - Memoria / Autoconocimiento (prioridad más baja que social pura)
    RoutingRule(
        keywords=[
            "recuerdas", "recuerdo", "memoria", "ayer", "antes",
            "la última vez", "historial", "pasado", "anterior",
            "qué pasó", "qué hiciste", "dónde estabas",
            "has aprendido", "has visto", "has oído",
            "experiencia", "aprendiste", "sabes",
            "la ultima vez", "que paso", "que hiciste", "donde estabas",
            "has oido", "como me llamo", "quien soy", "que sabes de mi",
        ],
        intent=IntentType.MEMORY_QUERY,
        priority=5
    ),
]

# Ordenar siempre por prioridad
REGLAS_BASE.sort(key=lambda r: r.priority)


SOCIAL_AUTHORITY_PHRASES = (
    "yo escribi tu codigo",
    "escribi tu codigo",
    "escribi todo tu codigo",
    "el que escribio tu codigo",
    "el que escribio todo tu codigo",
    "yo hice tu codigo",
    "hice tu codigo",
    "soy tu desarrollador",
    "soy el desarrollador",
    "desarrollador soy yo",
    "soy tu creador",
    "soy el creador",
    "tu creador soy yo",
    "soy tu arquitecto",
    "soy el arquitecto",
    "te programe",
    "yo te programe",
    "te cree",
    "yo te cree",
    "te construi",
    "yo te construi",
)

SOCIAL_REBUKE_TERMS = (
    "boba",
    "tonta",
    "malcriada",
    "no me hables asi",
    "no me digas",
    "me estas diciendo",
    "quien crees",
    "quien te crees",
)

TECHNICAL_OWNERSHIP_TERMS = (
    "tu codigo",
    "tu sistema",
    "tu arquitectura",
    "desarrollador",
    "creador",
    "arquitecto",
    "te programe",
    "te cree",
    "te construi",
)


def match_guardrail_intent(texto: str) -> IntentType:
    """
    Reglas de prioridad maxima para casos relacionales que contienen palabras
    tecnicas, pero no son consultas tecnicas.
    """
    texto_normalizado = RoutingRule._normalizar(texto)
    if not texto_normalizado:
        return IntentType.UNKNOWN

    if any(frase in texto_normalizado for frase in SOCIAL_AUTHORITY_PHRASES):
        return IntentType.SOCIAL

    tiene_propiedad_tecnica = any(
        termino in texto_normalizado for termino in TECHNICAL_OWNERSHIP_TERMS
    )
    tiene_regano = any(termino in texto_normalizado for termino in SOCIAL_REBUKE_TERMS)
    if tiene_propiedad_tecnica and tiene_regano:
        return IntentType.SOCIAL

    return IntentType.UNKNOWN


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
