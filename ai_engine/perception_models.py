"""
Modelos canonicos de percepcion para Capa 1.

Este modulo no depende de Unity, Chroma ni LLM. Su trabajo es convertir entradas
legacy o futuras en objetos perceptivos estables para resolucion motora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import unicodedata
from typing import Any


VALID_SOURCES = {"unity", "desktop", "ocr", "ui_tree"}

ALLOWED_AFFORDANCES = {
    "obstaculo",
    "transitable",
    "bloquea_paso",
    "punto_refugio",
    "mirar",
    "tocar",
    "agarrable",
    "empujable",
    "activable",
    "inspeccionable",
    "da_sombra",
    "cubre",
    "ilumina",
    "descanso",
    "peligro",
    "recurso",
    "clicable",
    "escribible",
    "arrastrable",
    "ventana",
    "boton",
    "campo_texto",
}


@dataclass(slots=True)
class PerceivedObject:
    id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    position: dict[str, float] | None = None
    screen_position: dict[str, float] | None = None
    distance: float | None = None
    affordances: list[str] = field(default_factory=list)
    source: str = "unity"
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "position": dict(self.position) if self.position is not None else None,
            "screen_position": dict(self.screen_position) if self.screen_position is not None else None,
            "distance": self.distance,
            "affordances": list(self.affordances),
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class PerceptionSnapshot:
    snapshot_id: str
    objects: list[PerceivedObject] = field(default_factory=list)
    source: str = "unity"
    raw: dict[str, Any] | str | None = None
    unknown_affordances: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "objects": [obj.to_dict() for obj in self.objects],
            "source": self.source,
            "raw": self.raw,
            "unknown_affordances": list(self.unknown_affordances),
        }


def normalize_text(value: object) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def normalize_perception(
    perception: PerceptionSnapshot | dict[str, Any] | str | None,
    *,
    source: str = "unity",
) -> PerceptionSnapshot:
    """Convierte percepcion legacy o canonica a `PerceptionSnapshot`."""

    if isinstance(perception, PerceptionSnapshot):
        return perception

    normalized_source = _normalize_source(source)
    if isinstance(perception, str):
        objects = _objects_from_legacy_text(perception, source=normalized_source)
        return PerceptionSnapshot(
            snapshot_id=_build_snapshot_id(normalized_source, perception),
            objects=objects,
            source=normalized_source,
            raw=perception,
        )

    if not isinstance(perception, dict):
        return PerceptionSnapshot(
            snapshot_id=_build_snapshot_id(normalized_source, ""),
            objects=[],
            source=normalized_source,
            raw=perception,
        )

    normalized_source = _normalize_source(str(perception.get("source") or perception.get("origen") or normalized_source))
    raw_objects = _extract_raw_objects(perception)
    unknown_affordances: list[str] = _normalize_aliases(perception.get("unknown_affordances"))
    objects = [
        _object_from_mapping(obj, source=normalized_source, unknown_affordances=unknown_affordances)
        for obj in raw_objects
    ]
    objects = [obj for obj in objects if obj is not None]

    if not objects:
        legacy_text = perception.get("objetos_visibles") or perception.get("resumen_percepcion")
        if isinstance(legacy_text, str):
            objects = _objects_from_legacy_text(legacy_text, source=normalized_source)

    snapshot_id = str(
        perception.get("snapshot_id")
        or perception.get("id_snapshot")
        or perception.get("perception_id")
        or _build_snapshot_id(normalized_source, perception)
    )
    return PerceptionSnapshot(
        snapshot_id=snapshot_id,
        objects=objects,
        source=normalized_source,
        raw=perception,
        unknown_affordances=_unique_preserve_order(unknown_affordances),
    )


def normalize_affordances(values: Any) -> tuple[list[str], list[str]]:
    if values is None:
        return [], []
    if isinstance(values, str):
        values = [part.strip() for part in re.split(r"[,;|]", values) if part.strip()]
    if not isinstance(values, (list, tuple, set)):
        return [], [str(values)]

    allowed: list[str] = []
    unknown: list[str] = []
    for raw_value in values:
        normalized = normalize_text(raw_value).replace(" ", "_").replace("-", "_")
        if not normalized:
            continue
        if normalized in ALLOWED_AFFORDANCES:
            allowed.append(normalized)
        else:
            unknown.append(normalized)
    return _unique_preserve_order(allowed), _unique_preserve_order(unknown)


def _object_from_mapping(
    raw: dict[str, Any],
    *,
    source: str,
    unknown_affordances: list[str],
) -> PerceivedObject | None:
    display_name = str(
        raw.get("display_name")
        or raw.get("nombre")
        or raw.get("name")
        or raw.get("id")
        or ""
    ).strip()
    if not display_name:
        return None

    object_id = str(raw.get("id") or raw.get("object_id") or raw.get("nombre") or display_name).strip()
    aliases = _normalize_aliases(raw.get("aliases") or raw.get("alias") or raw.get("nombres") or [])
    if display_name not in aliases:
        aliases.insert(0, display_name)

    affordances, unknown = normalize_affordances(raw.get("affordances") or raw.get("tags") or raw.get("etiquetas"))
    unknown_affordances.extend(unknown)

    return PerceivedObject(
        id=object_id,
        display_name=display_name,
        aliases=_unique_preserve_order(aliases),
        position=_normalize_position(raw.get("position") or raw.get("posicion") or raw),
        screen_position=_normalize_position(raw.get("screen_position") or raw.get("posicion_pantalla")),
        distance=_normalize_float(raw.get("distance") or raw.get("distancia")),
        affordances=affordances,
        source=source,
        confidence=_clamp_confidence(raw.get("confidence", raw.get("confianza", 1.0))),
    )


def _objects_from_legacy_text(text: str, *, source: str) -> list[PerceivedObject]:
    clean_text = str(text or "").strip()
    if not clean_text or "no veo" in normalize_text(clean_text):
        return []

    body = re.sub(r"^\s*(?:frente\s+a\s+mi\s+veo:|veo:|veo)\s*", "", clean_text, flags=re.IGNORECASE)
    body = body.rstrip(".")
    body = re.sub(r"\s+y\s+(?=[\w\s_-]+\s+a\s+\d)", ", ", body, flags=re.IGNORECASE)
    segments = [segment.strip() for segment in body.split(",") if segment.strip()]
    objects: list[PerceivedObject] = []
    for segment in segments:
        match = re.match(
            r"(?P<name>.+?)(?:\s+a\s+(?P<distance>\d+(?:[.,]\d+)?)\s*m\b.*)?$",
            segment,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        distance = _normalize_float(match.group("distance"))
        objects.append(
            PerceivedObject(
                id=name,
                display_name=name,
                aliases=[name],
                distance=distance,
                source=source,
                confidence=0.75 if distance is None else 0.85,
            )
        )
    return objects


def _extract_raw_objects(perception: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("objects", "objetos", "objetos_normalizados", "perceived_objects"):
        value = perception.get(key)
        if isinstance(value, list):
            return [obj for obj in value if isinstance(obj, dict)]

    visible = perception.get("objetos_visibles")
    if isinstance(visible, list):
        return [obj for obj in visible if isinstance(obj, dict)]
    return []


def _normalize_aliases(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [part.strip() for part in re.split(r"[,;|]", values) if part.strip()]
    if not isinstance(values, (list, tuple, set)):
        return []
    return [str(value).strip() for value in values if str(value or "").strip()]


def _normalize_position(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    keys = ("x", "y", "z")
    if not all(key in value for key in keys):
        return None
    try:
        return {key: float(value[key]) for key in keys}
    except (TypeError, ValueError):
        return None


def _normalize_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _clamp_confidence(value: Any) -> float:
    numeric = _normalize_float(value)
    if numeric is None:
        return 1.0
    return max(0.0, min(1.0, numeric))


def _normalize_source(value: str) -> str:
    normalized = normalize_text(value)
    return normalized if normalized in VALID_SOURCES else "unity"


def _build_snapshot_id(source: str, raw: object) -> str:
    payload = repr(raw).encode("utf-8", errors="ignore")
    digest = hashlib.sha1(payload).hexdigest()[:12]
    return f"{source}_{digest}"


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
