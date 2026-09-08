"""Pruebas permanentes del schema de persistencia cognitiva V1 (ROOT-013)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from core.audit_trace import AuditTrace
from memory.chroma_client import ChromaClient
from memory.episodic_memory import EpisodicMemoryStore
from memory.memory_encoder import MemoryEncoder
from memory.memory_schema import (
    CANONICAL_DEFAULT_ORIGIN,
    EPISODIC_KIND,
    LEGACY_DEFAULT_ORIGIN,
    MEMORY_EXTRA_JSON_KEY,
    MEMORY_SCHEMA_VERSION,
    MEMORY_SCHEMA_VERSION_KEY,
    MEMORY_STATE_JSON_KEY,
    SEMANTIC_KIND,
    MemorySchemaCodec,
    MemorySchemaDecodeError,
    MemorySchemaEncodeError,
)
from memory.memory_types import EpisodicMemory, MemoryMetadata, SemanticMemory
from memory.semantic_memory import SemanticMemoryStore


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_CHROMA = (ROOT / "lazzaro_data").resolve()
FIXED = datetime(2026, 3, 4, 5, 6, 7, 8901)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class ReplayClient:
    def __init__(self) -> None:
        self.write: dict[str, Any] | None = None
        self.query_result: dict[str, Any] | None = None
        self.last_where: dict[str, Any] | None = None

    @staticmethod
    def combinar_where(base, extra):
        if not base:
            return extra
        if not extra:
            return base
        return {"$and": [base, extra]}

    def agregar(self, **kwargs: Any) -> bool:
        self.write = deepcopy(kwargs)
        return True

    def buscar(self, _query: str, _n: int, where: dict[str, Any] | None):
        self.last_where = deepcopy(where)
        return deepcopy(self.query_result)


class OfflineEmbeddingFunction:
    @staticmethod
    def name() -> str:
        return "root-013-offline"

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors = []
        for text in input:
            total = sum(ord(char) for char in text)
            vectors.append(
                [
                    float((total % 17) + 1),
                    float((len(text) % 11) + 1),
                    float((text.count("a") % 7) + 1),
                ]
            )
        return vectors

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def get_config(self) -> dict[str, Any]:
        return {}


def _metadata(extra: dict[str, Any] | None = None) -> MemoryMetadata:
    return MemoryMetadata(
        tipo_evento="root013_event",
        valencia=-0.25,
        impacto_emocional=0.8,
        timestamp=FIXED,
        extra=extra or {},
    )


def _episode(
    *,
    extra: dict[str, Any] | None = None,
    state: dict[str, bool | int | float] | None = None,
) -> EpisodicMemory:
    return EpisodicMemory(
        id="root013-episode",
        metadata=_metadata(extra),
        narrativa="episodio sintético ROOT-013",
        estado_emocional=state,
    )


def _semantic(
    *,
    extra: dict[str, Any] | None = None,
    origin: str = "manual",
) -> SemanticMemory:
    return SemanticMemory(
        id="root013-semantic",
        metadata=_metadata(extra),
        regla="regla sintética ROOT-013",
        origen=origin,
    )


def _replay_episode(memory: EpisodicMemory) -> tuple[EpisodicMemory, ReplayClient]:
    client = ReplayClient()
    store = EpisodicMemoryStore(client)  # type: ignore[arg-type]
    assert_true(store.almacenar(memory), "El episodio sintético debe escribirse.")
    assert client.write is not None
    client.query_result = {
        "ids": [client.write["ids"]],
        "documents": [client.write["documentos"]],
        "metadatas": [client.write["metadatos"]],
        "distances": [[0.125]],
    }
    recovered = store.buscar(memory.narrativa, n=1)[0].recuerdo
    assert isinstance(recovered, EpisodicMemory)
    return recovered, client


def _replay_semantic(memory: SemanticMemory) -> tuple[SemanticMemory, ReplayClient]:
    client = ReplayClient()
    store = SemanticMemoryStore(client)  # type: ignore[arg-type]
    assert_true(store.almacenar(memory), "La semántica sintética debe escribirse.")
    assert client.write is not None
    client.query_result = {
        "ids": [client.write["ids"]],
        "documents": [client.write["documentos"]],
        "metadatas": [client.write["metadatos"]],
        "distances": [[0.25]],
    }
    recovered = store.buscar(memory.regla, n=1)[0].recuerdo
    assert isinstance(recovered, SemanticMemory)
    return recovered, client


def _expect_decode_error(
    metadata: dict[str, Any],
    expected_kind: str,
    reason_code: str,
) -> MemorySchemaDecodeError:
    try:
        MemorySchemaCodec.decode_metadata(metadata, expected_kind)
    except MemorySchemaDecodeError as exc:
        assert_true(exc.reason_code == reason_code, f"Reason inesperado: {exc.reason_code}")
        return exc
    raise AssertionError(f"Debía fallar con {reason_code}.")


def _expect_encode_error(memory: EpisodicMemory, reason_code: str) -> None:
    try:
        MemoryEncoder.a_documento(memory)
    except MemorySchemaEncodeError as exc:
        assert_true(exc.reason_code == reason_code, f"Reason inesperado: {exc.reason_code}")
        return
    raise AssertionError(f"Debía fallar con {reason_code}.")


def _valid_ep_metadata() -> dict[str, Any]:
    return MemoryEncoder.a_documento(
        _episode(extra={"source": "payload"}, state={"energia": 0.3})
    ).metadata


def _valid_sem_metadata() -> dict[str, Any]:
    return MemoryEncoder.a_documento(
        _semantic(extra={"source": "payload"}, origin="manual")
    ).metadata


def _assert_episode_equal(before: EpisodicMemory, after: EpisodicMemory) -> None:
    assert_true(before.id == after.id, "ID episódico no preservado.")
    assert_true(before.narrativa == after.narrativa, "Narrativa no preservada.")
    assert_true(before.metadata.tipo_evento == after.metadata.tipo_evento, "Tipo de evento cambió.")
    assert_true(before.metadata.valencia == after.metadata.valencia, "Valencia cambió.")
    assert_true(
        before.metadata.impacto_emocional == after.metadata.impacto_emocional,
        "Impacto cambió.",
    )
    assert_true(before.metadata.timestamp == after.metadata.timestamp, "Timestamp cambió.")
    assert_true(before.metadata.extra == after.metadata.extra, "Extra episódica cambió.")
    assert_true(before.estado_emocional == after.estado_emocional, "Estado cambió.")


def _assert_semantic_equal(before: SemanticMemory, after: SemanticMemory) -> None:
    assert_true(before.id == after.id, "ID semántico no preservado.")
    assert_true(before.regla == after.regla, "Regla no preservada.")
    assert_true(before.metadata.tipo_evento == after.metadata.tipo_evento, "Tipo de evento cambió.")
    assert_true(before.metadata.valencia == after.metadata.valencia, "Valencia cambió.")
    assert_true(
        before.metadata.impacto_emocional == after.metadata.impacto_emocional,
        "Impacto cambió.",
    )
    assert_true(before.metadata.timestamp == after.metadata.timestamp, "Timestamp cambió.")
    assert_true(before.metadata.extra == after.metadata.extra, "Extra semántica cambió.")
    assert_true(before.origen == after.origen, "Origen cambió.")


_REAL_REPORT: dict[str, Any] | None = None


def _real_temp_report() -> dict[str, Any]:
    global _REAL_REPORT
    if _REAL_REPORT is not None:
        return _REAL_REPORT

    rich_extra = {
        "source": "synthetic",
        "nested": {"x": 1},
        "items": [1, 2],
        "nullable": None,
        "unicode": "lázzaro",
        "flag": True,
        "count": 1,
        "tipo": "EXTRA_NOT_AUTHORITY",
        MEMORY_SCHEMA_VERSION_KEY: 999,
        "origen": "terminal",
        "objeto": "cubo",
        "accion": "observar",
    }
    rich_state = {
        "energia": 0.3,
        "estres": 0.7,
        "dolor_fisico_reciente": False,
    }
    filter_extra = {
        "huella_guardado_conversacional": "manual-fingerprint",
        "id_transaccion_guardado": "manual-transaction",
        "transacciones_guardado_hashes": "hash-a",
        "huella_conversacional": "relationship-fingerprint",
        "lote_consolidacion": "batch-fingerprint",
        "object_id": "Cube_01",
        "human_concept": "cubo azul",
        "approved": True,
        "object_class": "cube",
        "affect": "calma",
        "weight": 0.75,
        "confirmed": True,
        "fuente": "vision",
        "canal": "percepcion",
        "accion": "recordar",
        "objeto": "Cube_01",
        "nested": {"semantic": True},
        "items": [1, "dos", None],
        "nullable": None,
        "unicode": "lázzaro semántico",
        "origen": "EXTRA_NOT_AUTHORITY",
        MEMORY_SCHEMA_VERSION_KEY: 998,
    }
    episode = EpisodicMemory(
        id="root013-real-episode",
        metadata=MemoryMetadata(
            tipo_evento="episode_filter",
            valencia=-0.2,
            impacto_emocional=0.8,
            timestamp=FIXED,
            extra=rich_extra,
        ),
        narrativa="rich episodic ROOT-013",
        estado_emocional=rich_state,
    )
    semantic = SemanticMemory(
        id="root013-real-semantic",
        metadata=MemoryMetadata(
            tipo_evento="manual_filter",
            valencia=0.4,
            impacto_emocional=0.9,
            timestamp=FIXED.replace(tzinfo=timezone.utc),
            extra=filter_extra,
        ),
        regla="rich semantic ROOT-013",
        origen="manual",
    )

    with TemporaryDirectory(prefix="root013-schema-") as directory:
        temp_path = Path(directory).resolve()
        assert_true(temp_path != ACTIVE_CHROMA, "No puede usarse la Chroma activa.")
        assert_true(ACTIVE_CHROMA not in temp_path.parents, "La DB temporal debe estar aislada.")
        client = ChromaClient(directory, collection_name="root013_schema")
        assert_true(client.disponible, "Chroma temporal no disponible.")
        client._collection._embedding_function = OfflineEmbeddingFunction()
        try:
            episodic_store = EpisodicMemoryStore(client)
            semantic_store = SemanticMemoryStore(client)
            assert_true(episodic_store.almacenar(episode), "Write episódico real falló.")
            assert_true(semantic_store.almacenar(semantic), "Write semántico real falló.")
            recovered_episode = episodic_store.buscar(episode.narrativa, n=5)[0].recuerdo
            recovered_semantic = semantic_store.buscar(semantic.regla, n=5)[0].recuerdo
            assert isinstance(recovered_episode, EpisodicMemory)
            assert isinstance(recovered_semantic, SemanticMemory)
            _assert_episode_equal(episode, recovered_episode)
            _assert_semantic_equal(semantic, recovered_semantic)

            filters = {
                "tipo_episodico": ({"tipo": "episodico"}, episode.id),
                "tipo_semantico": ({"tipo": "semantico"}, semantic.id),
                "tipo_evento": ({"tipo_evento": "manual_filter"}, semantic.id),
                "impacto": ({"impacto_emocional": {"$gte": 0.85}}, semantic.id),
                "valencia": ({"valencia": {"$gt": 0.0}}, semantic.id),
                "manual_fingerprint": (
                    {"huella_guardado_conversacional": "manual-fingerprint"},
                    semantic.id,
                ),
                "manual_transaction": ({"id_transaccion_guardado": "manual-transaction"}, semantic.id),
                "relationship": ({"huella_conversacional": "relationship-fingerprint"}, semantic.id),
                "batch": ({"lote_consolidacion": "batch-fingerprint"}, semantic.id),
                "alias": ({"human_concept": "cubo azul"}, semantic.id),
                "affect": ({"affect": "calma"}, semantic.id),
                "source": ({"fuente": "vision"}, semantic.id),
                "channel": ({"canal": "percepcion"}, semantic.id),
                "action": ({"accion": "recordar"}, semantic.id),
                "object": ({"objeto": "Cube_01"}, semantic.id),
                "origin": ({"origen": "manual"}, semantic.id),
            }
            filter_hits: dict[str, list[str]] = {}
            for name, (where, expected_id) in filters.items():
                rows = client.listar(where=where)
                ids = list((rows or {}).get("ids") or [])
                assert_true(expected_id in ids, f"Filtro productivo roto: {name}")
                filter_hits[name] = ids

            raw = client.listar(where={"tipo": "episodico"})
            raw_metadata = (raw or {}).get("metadatas", [])[0]
            assert isinstance(raw_metadata, dict)
            _REAL_REPORT = {
                "path": str(temp_path),
                "episode": recovered_episode,
                "semantic": recovered_semantic,
                "raw_episode_metadata": raw_metadata,
                "filter_hits": filter_hits,
            }
        finally:
            client.close()
    assert _REAL_REPORT is not None
    return _REAL_REPORT


def test_01_schema_v1_marker_exists_and_is_exact_int() -> None:
    metadata = _valid_ep_metadata()
    assert_true(type(metadata[MEMORY_SCHEMA_VERSION_KEY]) is int, "Version debe ser int exacto.")
    assert_true(metadata[MEMORY_SCHEMA_VERSION_KEY] == MEMORY_SCHEMA_VERSION, "Version inesperada.")


def test_02_episodic_basic_v1_roundtrip() -> None:
    before = _episode(state={"energia": 0.3})
    after, _ = _replay_episode(before)
    _assert_episode_equal(before, after)


def test_03_semantic_basic_v1_roundtrip() -> None:
    before = _semantic(origin="manual")
    after, _ = _replay_semantic(before)
    _assert_semantic_equal(before, after)


def test_04_emotional_state_none_is_preserved() -> None:
    after, client = _replay_episode(_episode(state=None))
    assert_true(after.estado_emocional is None, "None no debe colapsar.")
    assert client.write is not None
    assert_true(client.write["metadatos"][0][MEMORY_STATE_JSON_KEY] == "null", "None debe ser null.")


def test_05_emotional_state_empty_is_preserved() -> None:
    after, client = _replay_episode(_episode(state={}))
    assert_true(after.estado_emocional == {}, "Estado vacío no debe convertirse a None.")
    assert client.write is not None
    assert_true(client.write["metadatos"][0][MEMORY_STATE_JSON_KEY] == "{}", "Vacío debe ser {}.")


def test_06_emotional_state_nonempty_and_bool_are_preserved() -> None:
    state = {"energia": 0.3, "estres": 1, "dolor_fisico_reciente": False}
    after, _ = _replay_episode(_episode(state=state))
    assert_true(after.estado_emocional == state, "Estado real no preservado.")
    assert_true(type(after.estado_emocional["dolor_fisico_reciente"]) is bool, "Bool cambió de tipo.")


def test_07_scalar_extra_is_preserved() -> None:
    extra = {"source": "synthetic", "count": 2, "ratio": 0.5, "flag": False}
    after, _ = _replay_episode(_episode(extra=extra))
    assert_true(after.metadata.extra == extra, "Extra escalar no preservada.")


def test_08_nested_extra_is_preserved() -> None:
    extra = {"nested": {"x": 1, "child": {"ok": True}}}
    after, _ = _replay_episode(_episode(extra=extra))
    assert_true(after.metadata.extra == extra, "Extra nested no preservada.")


def test_09_list_extra_is_preserved() -> None:
    extra = {"items": [1, "dos", False, {"x": 3}]}
    after, _ = _replay_episode(_episode(extra=extra))
    assert_true(after.metadata.extra == extra, "Lista extra no preservada.")


def test_10_null_extra_value_is_preserved() -> None:
    after, _ = _replay_episode(_episode(extra={"nullable": None}))
    assert_true("nullable" in after.metadata.extra, "Key null perdida.")
    assert_true(after.metadata.extra["nullable"] is None, "Null cambió.")


def test_11_bool_and_int_extra_remain_distinct() -> None:
    after, _ = _replay_episode(_episode(extra={"boolean": True, "integer": 1}))
    assert_true(type(after.metadata.extra["boolean"]) is bool, "Bool se volvió int.")
    assert_true(type(after.metadata.extra["integer"]) is int, "Int cambió.")


def test_12_unicode_extra_is_preserved() -> None:
    after, client = _replay_episode(_episode(extra={"unicode": "lázzaro 🐕"}))
    assert_true(after.metadata.extra["unicode"] == "lázzaro 🐕", "Unicode cambió.")
    assert client.write is not None
    assert_true("lázzaro 🐕" in client.write["metadatos"][0][MEMORY_EXTRA_JSON_KEY], "JSON escapó Unicode.")


def test_13_extra_tipo_cannot_overwrite_episodic_kind() -> None:
    after, client = _replay_episode(_episode(extra={"tipo": "ataque"}))
    assert client.write is not None
    raw = client.write["metadatos"][0]
    assert_true(raw["tipo"] == EPISODIC_KIND, "Extra reclasificó episodio.")
    assert_true(after.metadata.extra["tipo"] == "ataque", "Colisión tipo no se preservó como data.")


def test_14_extra_timestamp_cannot_overwrite_timestamp() -> None:
    after, client = _replay_episode(_episode(extra={"timestamp": "fake"}))
    assert client.write is not None
    assert_true(client.write["metadatos"][0]["timestamp"] == FIXED.isoformat(), "Timestamp fue sobrescrito.")
    assert_true(after.metadata.extra["timestamp"] == "fake", "Extra timestamp se perdió.")


def test_15_semantic_extra_origin_cannot_overwrite_origin() -> None:
    after, client = _replay_semantic(_semantic(extra={"origen": "fake"}, origin="manual"))
    assert client.write is not None
    assert_true(client.write["metadatos"][0]["origen"] == "manual", "Origen fue sobrescrito.")
    assert_true(after.metadata.extra["origen"] == "fake", "Extra origen se perdió.")


def test_16_internal_namespace_collision_is_payload_only() -> None:
    extra = {MEMORY_SCHEMA_VERSION_KEY: 999, MEMORY_EXTRA_JSON_KEY: "data"}
    after, client = _replay_episode(_episode(extra=extra))
    assert client.write is not None
    raw = client.write["metadatos"][0]
    assert_true(raw[MEMORY_SCHEMA_VERSION_KEY] == 1, "Extra cambió version authority.")
    assert_true(after.metadata.extra == extra, "Namespace interno no sobrevivió como data.")


def test_17_nonserializable_object_fails_before_chroma() -> None:
    _expect_encode_error(_episode(extra={"x": object()}), "unsupported_json_value")


def test_18_nonstring_extra_key_is_rejected() -> None:
    memory = _episode(extra={1: "x"})  # type: ignore[dict-item]
    _expect_encode_error(memory, "non_string_json_key")


def test_19_nonfinite_nested_extra_is_rejected() -> None:
    for value in (float("nan"), float("inf"), -float("inf")):
        _expect_encode_error(_episode(extra={"nested": {"x": value}}), "non_finite_json_number")


def test_20_nonfinite_emotional_state_is_rejected() -> None:
    for value in (float("nan"), float("inf"), -float("inf")):
        _expect_encode_error(_episode(state={"energia": value}), "non_finite_emotional_state_number")


def test_21_emotional_state_does_not_accept_nested_json() -> None:
    memory = _episode(state={"energia": {"nested": 1}})  # type: ignore[dict-item]
    _expect_encode_error(memory, "unsupported_emotional_state_value")


def test_22_v1_output_contains_only_chroma_scalars() -> None:
    metadata = MemoryEncoder.a_documento(
        _episode(extra={"nested": {"x": 1}, "null": None}, state={"energia": 0.3})
    ).metadata
    assert_true(bool(metadata), "Metadata no puede quedar vacía.")
    for value in metadata.values():
        assert_true(isinstance(value, (str, bool, int, float)), "Metadata no escalar llegó a Chroma.")
        if isinstance(value, float):
            assert_true(math.isfinite(value), "Metadata Chroma contiene no-finito.")


def test_23_episodic_store_keeps_tipo_filter() -> None:
    _, client = _replay_episode(_episode())
    assert_true(client.last_where == {"tipo": EPISODIC_KIND}, "Filtro episódico cambió.")


def test_24_semantic_store_keeps_tipo_filter() -> None:
    _, client = _replay_semantic(_semantic())
    assert_true(client.last_where == {"tipo": SEMANTIC_KIND}, "Filtro semántico cambió.")


def test_25_productive_scalar_fields_are_projected() -> None:
    extra = {
        "huella_guardado_conversacional": "h",
        "id_transaccion_guardado": "t",
        "huella_conversacional": "r",
        "lote_consolidacion": "l",
        "object_id": "o",
        "human_concept": "c",
        "approved": True,
        "object_class": "cube",
        "affect": "calma",
        "weight": 0.5,
        "confirmed": True,
        "fuente": "vision",
        "canal": "percepcion",
        "accion": "mirar",
        "objeto": "Cube",
    }
    raw = MemoryEncoder.a_documento(_semantic(extra=extra)).metadata
    assert_true(all(raw.get(key) == value for key, value in extra.items()), "Proyección productiva incompleta.")


def test_26_projection_excludes_complex_and_reserved_values() -> None:
    extra = {"nested": {"x": 1}, "items": [1], "nullable": None, "tipo": "fake", "timestamp": "fake"}
    raw = MemoryEncoder.a_documento(_episode(extra=extra)).metadata
    for key in extra:
        assert_true(key not in raw or key in {"tipo", "timestamp"}, "Extra no segura fue proyectada.")
    assert_true(raw["tipo"] == EPISODIC_KIND and raw["timestamp"] == FIXED.isoformat(), "Core cambió.")


def test_27_legacy_v0_episode_reads() -> None:
    decoded = MemorySchemaCodec.decode_metadata(
        {
            "tipo": "episodico",
            "tipo_evento": "legacy",
            "valencia": -0.1,
            "impacto_emocional": 0.6,
            "timestamp": FIXED.isoformat(),
            "scalar_extra": "kept",
        },
        EPISODIC_KIND,
    )
    assert_true(decoded.schema_version is None, "V0 no debe inventar version.")
    assert_true(decoded.extra == {"scalar_extra": "kept"}, "Extra V0 cambió.")
    assert_true(decoded.estado_emocional is None, "V0 inventó estado.")


def test_28_legacy_v0_semantic_origin_present_reads() -> None:
    decoded = MemorySchemaCodec.decode_metadata(
        {
            "tipo": "semantico",
            "tipo_evento": "legacy",
            "valencia": 0.1,
            "impacto_emocional": 0.5,
            "timestamp": FIXED.isoformat(),
            "origen": "manual",
            "scalar_extra": 2,
        },
        SEMANTIC_KIND,
    )
    assert_true(decoded.origen == "manual", "Origen V0 presente cambió.")
    assert_true(decoded.extra == {"scalar_extra": 2}, "Extra semántica V0 cambió.")


def test_29_legacy_v0_semantic_origin_missing_uses_legacy_default() -> None:
    decoded = MemorySchemaCodec.decode_metadata({}, SEMANTIC_KIND)
    assert_true(decoded.origen == LEGACY_DEFAULT_ORIGIN, "Fallback legacy cambió.")


def test_30_legacy_other_internal_names_remain_extra_without_marker() -> None:
    decoded = MemorySchemaCodec.decode_metadata({MEMORY_EXTRA_JSON_KEY: "legacy-data"}, EPISODIC_KIND)
    assert_true(decoded.extra[MEMORY_EXTRA_JSON_KEY] == "legacy-data", "V0 perdió extra interna histórica.")


def test_31_unknown_schema_version_is_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_SCHEMA_VERSION_KEY] = 999
    error = _expect_decode_error(metadata, EPISODIC_KIND, "unknown_schema_version")
    assert_true(error.schema_version == 999, "Versión segura no quedó observable.")


def test_32_schema_version_true_is_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_SCHEMA_VERSION_KEY] = True
    _expect_decode_error(metadata, EPISODIC_KIND, "invalid_schema_version_type")


def test_33_schema_version_float_is_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_SCHEMA_VERSION_KEY] = 1.0
    _expect_decode_error(metadata, EPISODIC_KIND, "invalid_schema_version_type")


def test_34_schema_version_string_is_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_SCHEMA_VERSION_KEY] = "1"
    _expect_decode_error(metadata, EPISODIC_KIND, "invalid_schema_version_type")


def test_35_duplicate_extra_json_keys_are_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_EXTRA_JSON_KEY] = '{"x":1,"x":2}'
    _expect_decode_error(metadata, EPISODIC_KIND, "duplicate_extra_json_key")


def test_36_duplicate_state_json_keys_are_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_STATE_JSON_KEY] = '{"x":1,"x":2}'
    _expect_decode_error(metadata, EPISODIC_KIND, "duplicate_state_json_key")


def test_37_bad_extra_json_is_rejected() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_EXTRA_JSON_KEY] = "{"
    _expect_decode_error(metadata, EPISODIC_KIND, "invalid_extra_json")


def test_38_extra_json_root_must_be_object() -> None:
    metadata = _valid_ep_metadata()
    metadata[MEMORY_EXTRA_JSON_KEY] = "[]"
    _expect_decode_error(metadata, EPISODIC_KIND, "invalid_extra_json_root")


def test_39_required_v1_fields_fail_closed() -> None:
    for key, reason in (
        ("tipo_evento", "invalid_tipo_evento"),
        ("valencia", "invalid_valencia"),
        ("impacto_emocional", "invalid_impacto_emocional"),
        ("timestamp", "invalid_timestamp"),
        (MEMORY_EXTRA_JSON_KEY, "extra_json_wrong_type"),
    ):
        metadata = _valid_ep_metadata()
        metadata.pop(key)
        _expect_decode_error(metadata, EPISODIC_KIND, reason)


def test_40_tipo_missing_or_wrong_for_store_fails_closed() -> None:
    missing = _valid_ep_metadata()
    missing.pop("tipo")
    _expect_decode_error(missing, EPISODIC_KIND, "unexpected_memory_type")
    wrong = _valid_ep_metadata()
    wrong["tipo"] = SEMANTIC_KIND
    _expect_decode_error(wrong, EPISODIC_KIND, "unexpected_memory_type")


def test_41_core_numeric_bool_is_rejected() -> None:
    for key in ("valencia", "impacto_emocional"):
        metadata = _valid_ep_metadata()
        metadata[key] = True
        _expect_decode_error(metadata, EPISODIC_KIND, f"invalid_{key}")


def test_42_core_numeric_nonfinite_and_string_are_rejected() -> None:
    for key in ("valencia", "impacto_emocional"):
        for value in (float("nan"), float("inf"), -float("inf"), "0.5"):
            metadata = _valid_ep_metadata()
            metadata[key] = value
            _expect_decode_error(metadata, EPISODIC_KIND, f"invalid_{key}")


def test_43_bad_timestamp_is_rejected_without_normalization() -> None:
    metadata = _valid_ep_metadata()
    metadata["timestamp"] = "not-iso"
    _expect_decode_error(metadata, EPISODIC_KIND, "invalid_timestamp")
    aware = _valid_ep_metadata()
    aware["timestamp"] = "2026-03-04T05:06:07+05:30"
    decoded = MemorySchemaCodec.decode_metadata(aware, EPISODIC_KIND)
    assert_true(decoded.timestamp.utcoffset() is not None, "Timestamp aware se volvió naive.")


def test_44_semantic_origin_is_required_in_v1() -> None:
    metadata = _valid_sem_metadata()
    metadata.pop("origen")
    _expect_decode_error(metadata, SEMANTIC_KIND, "invalid_origen")


def test_45_state_bad_json_root_and_value_are_rejected() -> None:
    cases = (
        ("{", "invalid_state_json"),
        ("[]", "invalid_emotional_state_root"),
        ('{"nested":{"x":1}}', "unsupported_emotional_state_value"),
        ('{"x":1e999}', "non_finite_state_json_number"),
    )
    for payload, reason in cases:
        metadata = _valid_ep_metadata()
        metadata[MEMORY_STATE_JSON_KEY] = payload
        _expect_decode_error(metadata, EPISODIC_KIND, reason)


def test_46_v1_reconstruction_ignores_top_level_extra_projection() -> None:
    metadata = _valid_ep_metadata()
    metadata["source"] = "forged-projection"
    decoded = MemorySchemaCodec.decode_metadata(metadata, EPISODIC_KIND)
    assert_true(decoded.extra == {"source": "payload"}, "Proyección ganó authority.")


def test_47_v1_conflicting_unknown_projection_is_not_merged() -> None:
    metadata = _valid_ep_metadata()
    metadata["projection_only"] = "must-not-enter-extra"
    decoded = MemorySchemaCodec.decode_metadata(metadata, EPISODIC_KIND)
    assert_true("projection_only" not in decoded.extra, "V1 mezcló top-level desconocido.")


def test_48_projection_is_generated_from_canonical_extra() -> None:
    extra = {"source": "one", "nested": {"x": 1}, "tipo": "fake"}
    projection = MemorySchemaCodec.project_extra(extra, EPISODIC_KIND)
    assert_true(projection == {"source": "one"}, "Projection helper no es canónico.")


def test_49_scalar_extra_update_regenerates_payload_and_projection() -> None:
    original = _valid_sem_metadata()
    snapshot = deepcopy(original)
    updated = MemorySchemaCodec.update_scalar_extra(
        original,
        key="source",
        value="updated",
        expected_kind=SEMANTIC_KIND,
    )
    decoded = MemorySchemaCodec.decode_metadata(updated, SEMANTIC_KIND)
    assert_true(decoded.extra["source"] == "updated", "Payload no fue actualizado.")
    assert_true(updated["source"] == "updated", "Proyección no fue actualizada.")
    assert_true(original == snapshot, "Update helper mutó metadata caller.")


def test_50_manual_receipt_hash_update_keeps_single_authority() -> None:
    memory = _semantic(extra={"transacciones_guardado_hashes": "hash-a"})
    raw = MemoryEncoder.a_documento(memory).metadata
    updated = MemorySchemaCodec.update_scalar_extra(
        raw,
        key="transacciones_guardado_hashes",
        value="hash-a,hash-b",
        expected_kind=SEMANTIC_KIND,
    )
    decoded = MemorySchemaCodec.decode_metadata(updated, SEMANTIC_KIND)
    assert_true(updated["transacciones_guardado_hashes"] == "hash-a,hash-b", "Projection receipt stale.")
    assert_true(
        decoded.extra["transacciones_guardado_hashes"] == "hash-a,hash-b",
        "Payload receipt stale.",
    )


def test_51_legacy_scalar_update_keeps_v0_shape() -> None:
    legacy = {"tipo": "semantico", "source": "old"}
    updated = MemorySchemaCodec.update_scalar_extra(
        legacy,
        key="source",
        value="new",
        expected_kind=SEMANTIC_KIND,
    )
    assert_true(updated == {"tipo": "semantico", "source": "new"}, "Update V0 cambió shape.")
    assert_true(legacy["source"] == "old", "Update V0 mutó caller.")


def test_52_encoder_does_not_mutate_extra_or_state() -> None:
    extra = {"nested": {"items": [1, 2]}, "source": "synthetic"}
    state = {"energia": 0.3, "dolor_fisico_reciente": False}
    extra_before = deepcopy(extra)
    state_before = deepcopy(state)
    MemoryEncoder.a_documento(_episode(extra=extra, state=state))
    assert_true(extra == extra_before, "Encoder mutó extra caller.")
    assert_true(state == state_before, "Encoder mutó estado caller.")


def test_53_canonical_origin_default_is_coherent() -> None:
    direct = SemanticMemory("id", _metadata(), "rule")
    encoded = MemoryEncoder.codificar_semantica("rule")
    assert_true(direct.origen == CANONICAL_DEFAULT_ORIGIN, "Dataclass default divergente.")
    assert_true(encoded.origen == CANONICAL_DEFAULT_ORIGIN, "Encoder default divergente.")


def test_54_real_temp_chroma_rich_episode_roundtrip() -> None:
    report = _real_temp_report()
    episode = report["episode"]
    assert_true(episode.metadata.extra["nested"] == {"x": 1}, "Nested real Chroma perdido.")
    assert_true(episode.estado_emocional["dolor_fisico_reciente"] is False, "State real perdido.")
    assert_true(episode.metadata.extra[MEMORY_SCHEMA_VERSION_KEY] == 999, "Colisión interna perdida.")


def test_55_real_temp_chroma_rich_semantic_roundtrip() -> None:
    semantic = _real_temp_report()["semantic"]
    assert_true(semantic.origen == "manual", "Origen real Chroma cambió.")
    assert_true(semantic.metadata.extra["object_id"] == "Cube_01", "Extra semántica real perdida.")


def test_56_real_temp_chroma_productive_filters_work() -> None:
    hits = _real_temp_report()["filter_hits"]
    expected = {
        "tipo_episodico",
        "tipo_semantico",
        "tipo_evento",
        "impacto",
        "valencia",
        "manual_fingerprint",
        "manual_transaction",
        "relationship",
        "batch",
        "alias",
        "affect",
        "source",
        "channel",
        "action",
        "object",
        "origin",
    }
    assert_true(set(hits) == expected and all(hits.values()), "Matriz de filtros incompleta.")


def test_57_real_temp_chroma_raw_readers_keep_scalar_projection() -> None:
    metadata = _real_temp_report()["raw_episode_metadata"]
    assert_true(metadata["origen"] == "terminal", "Telescope perdió origen episódico.")
    assert_true(metadata["objeto"] == "cubo", "Telescope perdió objeto.")
    assert_true(metadata["accion"] == "observar", "Telescope perdió acción.")
    assert_true(metadata[MEMORY_SCHEMA_VERSION_KEY] == 1, "Authority de version cambió.")


def test_58_real_chroma_path_is_temporary_and_never_active() -> None:
    used = Path(_real_temp_report()["path"]).resolve()
    assert_true(used != ACTIVE_CHROMA, "El test construyó la Chroma activa.")
    assert_true(ACTIVE_CHROMA not in used.parents, "El test quedó dentro de Chroma activa.")


def test_59_audit_trace_v1_does_not_receive_memory_payload() -> None:
    trace = AuditTrace(
        stage="memory",
        memory={
            "retrieved_ids": ["memory-id"],
            "scores": [0.8],
            "created_ids": ["created-id"],
            "working_memory_count": 1,
        },
    )
    payload = json.loads(trace.to_json())
    assert_true(
        set(payload["memory"])
        == {"retrieved_ids", "scores", "created_ids", "working_memory_count"},
        "AuditTrace v1 cambió su contrato memory.",
    )
    rendered = json.dumps(payload, ensure_ascii=False)
    assert_true(MEMORY_EXTRA_JSON_KEY not in rendered, "AuditTrace recibió extra payload.")
    assert_true(MEMORY_STATE_JSON_KEY not in rendered, "AuditTrace recibió state payload.")
    assert_true("lázzaro" not in rendered, "AuditTrace recibió rich JSON.")


def test_60_decode_error_is_redacted() -> None:
    metadata = _valid_ep_metadata()
    canary = "PRIVATE_MEMORY_PAYLOAD_CANARY"
    metadata[MEMORY_EXTRA_JSON_KEY] = canary
    error = _expect_decode_error(metadata, EPISODIC_KIND, "invalid_extra_json")
    rendered = str(error)
    assert_true(canary not in rendered, "DecodeError filtró raw JSON.")
    assert_true("reason_code=invalid_extra_json" in rendered, "DecodeError perdió reason code.")


def test_61_nonhashable_memory_type_fails_closed_and_redacted() -> None:
    metadata = _valid_ep_metadata()
    metadata["tipo"] = ["episodico"]
    error = _expect_decode_error(metadata, EPISODIC_KIND, "unexpected_memory_type")
    assert_true(error.memory_type is None, "Tipo no seguro llegó al error público.")


def test_62_encoder_rejects_bool_and_nonfinite_core_numbers() -> None:
    for key, value in (
        ("valencia", True),
        ("impacto_emocional", False),
        ("valencia", float("nan")),
        ("impacto_emocional", float("inf")),
    ):
        memory = _episode()
        object.__setattr__(memory.metadata, key, value)
        _expect_encode_error(memory, f"invalid_{key}")


def test_63_encoder_rejects_falsey_non_object_extra() -> None:
    memory = MemoryEncoder.codificar_episodio("event", "narrative", extra=[])  # type: ignore[arg-type]
    _expect_encode_error(memory, "invalid_extra_root")


def test_64_canonical_json_is_compact_sorted_and_unicode() -> None:
    raw = MemoryEncoder.a_documento(
        _episode(extra={"z": 1, "á": "lázzaro", "a": [True, None]})
    ).metadata[MEMORY_EXTRA_JSON_KEY]
    assert_true(raw == '{"a":[true,null],"z":1,"á":"lázzaro"}', "JSON V1 no es canónico.")


def test_65_missing_state_payload_fails_closed() -> None:
    metadata = _valid_ep_metadata()
    metadata.pop(MEMORY_STATE_JSON_KEY)
    _expect_decode_error(metadata, EPISODIC_KIND, "state_json_wrong_type")


def test_66_projection_regeneration_removes_stale_noncanonical_field() -> None:
    metadata = _valid_sem_metadata()
    metadata["stale_projection"] = "forged"
    updated = MemorySchemaCodec.update_scalar_extra(
        metadata,
        key="source",
        value="updated",
        expected_kind=SEMANTIC_KIND,
    )
    assert_true("stale_projection" not in updated, "Projection stale sobrevivió regeneración.")
    assert_true(updated["source"] == "updated", "Projection actualizada ausente.")


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-013 schema fidelity: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
