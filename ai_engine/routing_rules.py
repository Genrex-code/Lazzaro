"""
routing_rules.py - Reglas heurísticas ampliadas para clasificar intenciones del usuario.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List
import re
import unicodedata
from .intent_types import IntentType


MANUAL_CONVERSATION_SAVE_PHRASES = frozenset(
    {
        "guarda lo que hemos hablado",
        "guarda lo que tengas en ram en tu mente",
    }
)


class RuleTier(str, Enum):
    """Precedencia interna del analizador lexico de ROOT-024."""

    CONTROL = "control"
    CONSTRAINT = "constraint"
    POSITIVE = "positive"


@dataclass(frozen=True)
class ProtectedSpan:
    """Region que contiene texto citado, codigo, URL o path."""

    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class LexicalToken:
    """Token Unicode con offsets sobre ``LexicalView.normalized_text``."""

    value: str
    start: int
    end: int
    protected: bool


@dataclass(frozen=True)
class LexicalView:
    """Vista normalizada solo para reglas; nunca alimenta al modelo desplegado."""

    raw_text: str
    normalized_text: str
    tokens: tuple[LexicalToken, ...]
    protected_spans: tuple[ProtectedSpan, ...]


@dataclass(frozen=True)
class LexicalRule:
    """Regla tipada, identificable y con precedencia estable."""

    rule_id: str
    tier: RuleTier
    priority: int
    predicate: Callable[[LexicalView], bool]
    intent: IntentType | None = None
    forbidden_intents: frozenset[IntentType] = frozenset()
    forbidden_routes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LexicalMatch:
    """Resultado agregado: positivo opcional mas restricciones deterministas."""

    positive_intent: IntentType | None
    positive_rule_id: str | None
    positive_priority: int | None
    forbidden_intents: frozenset[IntentType]
    forbidden_routes: frozenset[str]
    matched_rule_ids: tuple[str, ...]


_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'`]+")
_PATH_RE = re.compile(r"(?<!\w)(?:[A-Za-z]:[\\/]|/)[^\s<>\"'`]+")


def _normalize_lexical_text(text: object) -> str:
    value = unicodedata.normalize("NFC", str(text or "")).casefold().strip()
    return re.sub(r"\s+", " ", value)


def _delimited_spans(text: str) -> list[ProtectedSpan]:
    pairs = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}
    spans: list[ProtectedSpan] = []
    index = 0
    while index < len(text):
        opener = text[index]
        closer = pairs.get(opener)
        if closer is None:
            index += 1
            continue

        # Un apostrofe interno no abre un string/cita.
        if (
            opener == "'"
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        ):
            index += 1
            continue

        closing_index = text.find(closer, index + 1)
        end = len(text) if closing_index < 0 else closing_index + 1
        spans.append(
            ProtectedSpan(
                kind="code" if opener == "`" else "quote",
                start=index,
                end=end,
            )
        )
        index = end
    return spans


def _merge_protected_spans(spans: list[ProtectedSpan]) -> tuple[ProtectedSpan, ...]:
    if not spans:
        return ()
    ordered = sorted(spans, key=lambda span: (span.start, span.end, span.kind))
    merged: list[ProtectedSpan] = [ordered[0]]
    for span in ordered[1:]:
        previous = merged[-1]
        if span.start >= previous.end:
            merged.append(span)
            continue
        merged[-1] = ProtectedSpan(
            kind=previous.kind if previous.kind == span.kind else "protected",
            start=previous.start,
            end=max(previous.end, span.end),
        )
    return tuple(merged)


def build_lexical_view(text: object) -> LexicalView:
    """Construye la representacion NFC/contextual aprobada por ROOT-024."""

    raw_text = str(text or "")
    normalized_text = _normalize_lexical_text(raw_text)
    spans = _delimited_spans(normalized_text)
    spans.extend(
        ProtectedSpan("url", match.start(), match.end())
        for match in _URL_RE.finditer(normalized_text)
    )
    spans.extend(
        ProtectedSpan("path", match.start(), match.end())
        for match in _PATH_RE.finditer(normalized_text)
    )
    protected_spans = _merge_protected_spans(spans)
    tokens = tuple(
        LexicalToken(
            value=match.group(0),
            start=match.start(),
            end=match.end(),
            protected=any(
                match.start() >= span.start and match.end() <= span.end
                for span in protected_spans
            ),
        )
        for match in _TOKEN_RE.finditer(normalized_text)
    )
    return LexicalView(
        raw_text=raw_text,
        normalized_text=normalized_text,
        tokens=tokens,
        protected_spans=protected_spans,
    )


def _as_lexical_view(value: str | LexicalView) -> LexicalView:
    return value if isinstance(value, LexicalView) else build_lexical_view(value)


def _phrase_tokens(phrase: str) -> tuple[str, ...]:
    normalized = _normalize_lexical_text(phrase)
    return tuple(match.group(0) for match in _TOKEN_RE.finditer(normalized))


def _contains_sequence(
    view: LexicalView,
    phrase: str,
    *,
    protected: bool | None = False,
) -> bool:
    expected = _phrase_tokens(phrase)
    if not expected or len(expected) > len(view.tokens):
        return False
    for start in range(len(view.tokens) - len(expected) + 1):
        window = view.tokens[start : start + len(expected)]
        if tuple(token.value for token in window) != expected:
            continue
        if protected is None or all(token.protected is protected for token in window):
            return True
    return False


def _contains_any_sequence(view: LexicalView, phrases: tuple[str, ...]) -> bool:
    return any(_contains_sequence(view, phrase) for phrase in phrases)


def _has_token(view: LexicalView, values: frozenset[str], *, protected: bool = False) -> bool:
    return any(token.value in values and token.protected is protected for token in view.tokens)


_MOTOR_ACTION_TOKENS = frozenset(
    {
        "abre", "abrir", "abras", "abres", "abrí",
        "camina", "correr", "corre", "gira", "girar",
        "mira", "mirar", "mires", "mover", "moverte", "mueva",
        "muevas", "mueve", "muévete", "muevete", "observa", "salta", "usa",
    }
)
_BOUNDARY_SENSITIVE_MOTOR_TOKENS = frozenset({"abre", "mover", "mueve", "usa"})
_MEMORY_UPDATE_TOKENS = frozenset(
    {"aprende", "guarda", "guardes", "memoriza", "recuerda", "recuerdes"}
)
_SAVE_TOKENS = frozenset({"guarda", "guardes", "guardar"})
_NEGATION_PHRASES = (
    ("no", "quiero"),
    ("no", "hagas"),
    ("no",),
    ("nunca",),
    ("evita",),
    ("sin",),
)


def _has_local_negation(view: LexicalView, governed_tokens: frozenset[str]) -> bool:
    tokens = view.tokens
    for action_index, action in enumerate(tokens):
        if action.protected or action.value not in governed_tokens:
            continue
        for phrase in _NEGATION_PHRASES:
            phrase_length = len(phrase)
            earliest = max(0, action_index - 5)
            for start in range(earliest, action_index - phrase_length + 1):
                window = tokens[start : start + phrase_length]
                if any(token.protected for token in window):
                    continue
                if tuple(token.value for token in window) != phrase:
                    continue
                between = view.normalized_text[window[-1].end : action.start]
                if not re.search(r"[,.;:!?]", between):
                    return True
    return False


def _protected_contains_token(view: LexicalView, values: frozenset[str]) -> bool:
    return any(token.protected and token.value in values for token in view.tokens)


def _is_code_context(view: LexicalView) -> bool:
    return (
        "==" in view.normalized_text
        and _has_token(view, frozenset({"if", "command"}))
    )


def _is_narrative_motor_context(view: LexicalView) -> bool:
    if not _has_token(view, _MOTOR_ACTION_TOKENS):
        return False
    return _contains_any_sequence(
        view,
        (
            "ayer",
            "mañana voy a",
            "me gusta",
            "hablemos de",
            "qué pasaría si",
            "que pasaria si",
        ),
    )


def _is_boundary_lookalike(view: LexicalView) -> bool:
    visible_values = tuple(
        token.value for token in view.tokens if not token.protected
    )
    contains_embedded_motor_lexeme = any(
        atom != value and atom in value
        for value in visible_values
        for atom in _BOUNDARY_SENSITIVE_MOTOR_TOKENS
    )
    contains_explicit_motor_token = any(
        value in _MOTOR_ACTION_TOKENS for value in visible_values
    )
    return contains_embedded_motor_lexeme and not contains_explicit_motor_token


def _is_manual_save(view: LexicalView) -> bool:
    return match_manual_conversation_save(view.raw_text)


def _is_technical_why_question(view: LexicalView) -> bool:
    return (
        _contains_any_sequence(view, ("por qué", "por que"))
        and _contains_sequence(view, "gente cree")
        and _has_token(view, frozenset({"código", "codigo", "falla", "fallo"}))
    )


def _is_explain_plan(view: LexicalView) -> bool:
    return _contains_any_sequence(view, ("explícame el plan", "explicame el plan"))


def _is_definition_question(view: LexicalView) -> bool:
    return _contains_any_sequence(view, ("qué significa", "que significa"))


def _is_code_error_question(view: LexicalView) -> bool:
    return bool(view.protected_spans) and _contains_sequence(view, "da error")


def _is_how_to_question(view: LexicalView) -> bool:
    return _contains_any_sequence(view, ("cómo se abre", "como se abre"))


ROOT_024_LEXICAL_RULES: tuple[LexicalRule, ...] = (
    LexicalRule(
        "control.manual_conversation_save",
        RuleTier.CONTROL,
        10,
        _is_manual_save,
        intent=IntentType.CONVERSATION_SAVE,
    ),
    LexicalRule(
        "constraint.boundary_lookalike_not_motor",
        RuleTier.CONSTRAINT,
        100,
        _is_boundary_lookalike,
        forbidden_routes=frozenset({"motor"}),
    ),
    LexicalRule(
        "constraint.negated_motor",
        RuleTier.CONSTRAINT,
        110,
        lambda view: _has_local_negation(view, _MOTOR_ACTION_TOKENS),
        forbidden_routes=frozenset({"motor"}),
    ),
    LexicalRule(
        "constraint.protected_motor",
        RuleTier.CONSTRAINT,
        120,
        lambda view: _protected_contains_token(view, _MOTOR_ACTION_TOKENS),
        forbidden_routes=frozenset({"motor"}),
    ),
    LexicalRule(
        "constraint.code_context_not_motor",
        RuleTier.CONSTRAINT,
        130,
        _is_code_context,
        forbidden_routes=frozenset({"motor"}),
    ),
    LexicalRule(
        "constraint.narrative_motor",
        RuleTier.CONSTRAINT,
        140,
        _is_narrative_motor_context,
        forbidden_routes=frozenset({"motor"}),
    ),
    LexicalRule(
        "constraint.negated_save",
        RuleTier.CONSTRAINT,
        150,
        lambda view: _has_local_negation(view, _SAVE_TOKENS),
        forbidden_intents=frozenset({IntentType.CONVERSATION_SAVE}),
    ),
    LexicalRule(
        "constraint.protected_save",
        RuleTier.CONSTRAINT,
        160,
        lambda view: any(
            _contains_sequence(view, phrase, protected=True)
            for phrase in MANUAL_CONVERSATION_SAVE_PHRASES
        ),
        forbidden_intents=frozenset({IntentType.CONVERSATION_SAVE}),
    ),
    LexicalRule(
        "constraint.negated_memory_update",
        RuleTier.CONSTRAINT,
        170,
        lambda view: _has_local_negation(view, _MEMORY_UPDATE_TOKENS),
        forbidden_intents=frozenset(
            {IntentType.MEMORY_UPDATE, IntentType.CONVERSATION_SAVE}
        ),
    ),
    LexicalRule(
        "constraint.protected_memory_update",
        RuleTier.CONSTRAINT,
        180,
        lambda view: _protected_contains_token(view, _MEMORY_UPDATE_TOKENS),
        forbidden_intents=frozenset(
            {IntentType.MEMORY_UPDATE, IntentType.CONVERSATION_SAVE}
        ),
    ),
    LexicalRule(
        "positive.oracle_technical_why",
        RuleTier.POSITIVE,
        210,
        _is_technical_why_question,
        intent=IntentType.ORACLE_QUERY,
    ),
    LexicalRule(
        "positive.oracle_explain_plan",
        RuleTier.POSITIVE,
        220,
        _is_explain_plan,
        intent=IntentType.ORACLE_QUERY,
    ),
    LexicalRule(
        "positive.oracle_definition",
        RuleTier.POSITIVE,
        230,
        _is_definition_question,
        intent=IntentType.ORACLE_QUERY,
    ),
    LexicalRule(
        "positive.oracle_code_error",
        RuleTier.POSITIVE,
        240,
        _is_code_error_question,
        intent=IntentType.ORACLE_QUERY,
    ),
    LexicalRule(
        "positive.oracle_how_to",
        RuleTier.POSITIVE,
        250,
        _is_how_to_question,
        intent=IntentType.ORACLE_QUERY,
    ),
)


def _validate_root_024_rules(rules: tuple[LexicalRule, ...]) -> None:
    rule_ids = [rule.rule_id for rule in rules]
    priorities = [rule.priority for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("ROOT-024 lexical rules contain duplicate rule_id")
    if len(priorities) != len(set(priorities)):
        raise ValueError("ROOT-024 lexical rules contain duplicate priority")
    if priorities != sorted(priorities):
        raise ValueError("ROOT-024 lexical rules must be declared in priority order")
    for rule in rules:
        if rule.tier in {RuleTier.CONTROL, RuleTier.POSITIVE} and rule.intent is None:
            raise ValueError(f"Positive lexical rule without intent: {rule.rule_id}")
        if rule.tier is RuleTier.CONSTRAINT and rule.intent is not None:
            raise ValueError(f"Constraint lexical rule has intent: {rule.rule_id}")


_validate_root_024_rules(ROOT_024_LEXICAL_RULES)


def analyze_lexical_routing(text: object) -> tuple[LexicalView, LexicalMatch]:
    """Evalua la tabla estable sin ejecutar modelo, fallback ni side effects."""

    view = build_lexical_view(text)
    matches = [rule for rule in ROOT_024_LEXICAL_RULES if rule.predicate(view)]
    positive = next(
        (
            rule
            for rule in matches
            if rule.tier in {RuleTier.CONTROL, RuleTier.POSITIVE}
        ),
        None,
    )
    return view, LexicalMatch(
        positive_intent=positive.intent if positive is not None else None,
        positive_rule_id=positive.rule_id if positive is not None else None,
        positive_priority=positive.priority if positive is not None else None,
        forbidden_intents=frozenset(
            intent
            for rule in matches
            for intent in rule.forbidden_intents
        ),
        forbidden_routes=frozenset(
            route
            for rule in matches
            for route in rule.forbidden_routes
        ),
        matched_rule_ids=tuple(rule.rule_id for rule in matches),
    )


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
            "camina", "mueve", "mover", "moverte", "muevas", "muévete", "ve a", "ve al", "acércate", "aléjate",
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


def match_guardrail_intent(texto: str | LexicalView) -> IntentType:
    """
    Reglas de prioridad maxima para casos relacionales que contienen palabras
    tecnicas, pero no son consultas tecnicas.
    """
    view = _as_lexical_view(texto)
    if not view.normalized_text:
        return IntentType.UNKNOWN

    if any(_contains_sequence(view, frase) for frase in SOCIAL_AUTHORITY_PHRASES):
        return IntentType.SOCIAL

    tiene_propiedad_tecnica = any(
        _contains_sequence(view, termino) for termino in TECHNICAL_OWNERSHIP_TERMS
    )
    tiene_regano = any(
        _contains_sequence(view, termino) for termino in SOCIAL_REBUKE_TERMS
    )
    if tiene_propiedad_tecnica and tiene_regano:
        return IntentType.SOCIAL

    return IntentType.UNKNOWN


def match_manual_conversation_save(texto: str) -> bool:
    """
    Reconoce la base determinista de frases aprobadas para persistir toda la RAM.

    La comparación tolera mayúsculas, acentos, puntuación y espacios, pero no
    convierte solicitudes genéricas de MEMORY_UPDATE en un guardado completo.
    Un Random Forest reentrenado sí puede ampliar esta intención por separado.
    """
    texto_normalizado = RoutingRule._normalizar(texto)
    texto_normalizado = re.sub(r"[^a-z0-9]+", " ", texto_normalizado)
    texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()
    return texto_normalizado in MANUAL_CONVERSATION_SAVE_PHRASES


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
