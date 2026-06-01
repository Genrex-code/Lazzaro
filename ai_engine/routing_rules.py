"""
routing_rules.py - Reglas heurísticas para clasificar intenciones del usuario.
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
# DEFINICIÓN DE REGLAS (Fácil de ampliar o cargar desde JSON/YAML)
# ----------------------------------------------------------------------
REGLAS_BASE: List[RoutingRule] = [
    # Capa 3 - Oráculo (mayor prioridad)
    RoutingRule(
        keywords=["código", "refactoriza", "arquitectura", "significa",
                  "qué es", "explícame", "por qué", "filosofía", "debug",
                  "error", "script", "función", "clase"],
        intent=IntentType.ORACLE,
        priority=1
    ),
    # Capa 1 - Motor
    RoutingRule(
        keywords=["camina", "mueve", "muévete", "ve a", "acércate",
                  "aléjate", "inspecciona", "toca", "agarra", "suelta",
                  "interactúa", "salta", "corre", "gira", "retrocede"],
        intent=IntentType.MOTOR_SIMPLE,
        priority=2
    ),
    # Capa 2 - Persona
    RoutingRule(
        keywords=["hola", "cómo", "qué tal", "nombre", "apodo",
                  "lazzaro", "lazz", "niña", "chica", "pelotita",
                  "sientes", "gusta", "cansada", "duele", "bien",
                  "mal", "gracias", "adiós", "quién eres"],
        intent=IntentType.SOCIAL,
        priority=3
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