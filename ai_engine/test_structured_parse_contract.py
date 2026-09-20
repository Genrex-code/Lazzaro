"""ROOT-020: corpus determinista del contrato de parsing estructurado."""

from __future__ import annotations

import inspect
import io
import json
import logging
from dataclasses import fields
from datetime import datetime, timezone
from unittest.mock import patch

from core.behavior_core import BehaviorCore
from core.identity_snapshot import IdentitySnapshotProvider, build_static_self_v1
from ai_engine.oracle_engine import OracleEngine
from ai_engine.knowledge_provenance import (
    CacheEntryIdentityV1,
    CacheLookupStatusV1,
    CacheStoreStatusV1,
    KnowledgeProducerKindV1,
    KnowledgeProducerRouteV1,
    ProducerReceiptV1,
    cache_receipt_for_lookup,
    local_deterministic_producer,
    oracle_cache_logical_key,
    oracle_query_sha256,
    with_cache_store_status,
)
from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    ProviderAttemptReceiptV1,
)
from ai_engine.persona_engine import PersonaEngine
from ai_engine.response_parser import ResponseParser
from ai_engine.structured_parse_types import (
    StructuredNormalization,
    StructuredParseIssueCode,
    StructuredParseResult,
    StructuredParseStatus,
    StructuredRawShape,
    StructuredTextSalvage,
    StructuredTextSalvageKind,
)
from api.schemas import AccionSaliente, EventoEntrante, RespuestaAPI
from core.audit_trace import AuditTrace
from core.orchestrator import Orchestrator


def _general(**overrides) -> str:
    payload = {
        "pensamiento": "decision breve",
        "accion": "none",
        "parametros": {},
        "razon": "contrato",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _persona(**overrides) -> str:
    payload = {
        "accion": "hablar",
        "parametros": {"texto_a_comunicar": "Hola"},
        "pensamiento_privado": "reflexion breve",
        "razon": "respuesta social",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _oracle(**overrides) -> str:
    payload = {
        "contenido_tecnico": "Diagnostico",
        "resumen_para_persona": "Resumen",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class _Memory:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[dict] = []

    def guardar_en_cache(self, pregunta, contenido_tecnico, **kwargs):
        if self.fail:
            raise RuntimeError("CACHE_CANARY_PRIVATE")
        self.writes.append(
            {
                "pregunta": pregunta,
                "contenido_tecnico": contenido_tecnico,
                **kwargs,
            }
        )
        return True

    def guardar_resultado_en_cache(self, resultado):
        if self.fail:
            raise RuntimeError("CACHE_CANARY_PRIVATE")
        self.writes.append(
            {
                "pregunta": resultado.pregunta_original,
                "contenido_tecnico": resultado.contenido_tecnico,
                "resumen_para_persona": resultado.resumen_para_persona,
                "fuente": resultado.fuente,
            }
        )
        identity = CacheEntryIdentityV1(
            entry_id="structured-fixture",
            logical_key=oracle_cache_logical_key(resultado.pregunta_original),
            stored_query_sha256=oracle_query_sha256(resultado.pregunta_original),
            cache_schema_version=1,
            knowledge_contract_version=1,
            provenance_version=1,
            entry_created_at_utc=datetime.now(timezone.utc),
        )
        return with_cache_store_status(
            resultado.provenance.retrieval.cache,
            CacheStoreStatusV1.STORED,
            stored_entry=identity,
        )


def _cache_receipt(query: str = "q"):
    return cache_receipt_for_lookup(
        query,
        status=CacheLookupStatusV1.MISS,
        threshold=0.35,
    )


def _producer(status: StructuredParseStatus):
    if status is StructuredParseStatus.ERROR:
        return local_deterministic_producer(
            final_parse_status=status,
            accepted_at_utc=datetime.now(timezone.utc),
        )
    return ProducerReceiptV1(
        kind=KnowledgeProducerKindV1.PROVIDER_GENERATED,
        route=KnowledgeProducerRouteV1.ORACLE_EXTERNAL,
        provider="structured-fixture",
        model_id="structured-fixture-model",
        final_parse_status=status,
        parse_contract_version=1,
        provider_fallback_used=False,
        scheduler_attempt_count=1,
        failure_classes=(),
        generation_terminal=LLMCallTerminalV1.SUCCESS,
        final_failure=None,
        accepted_at_utc=datetime.now(timezone.utc),
    )


def _project_oracle(engine, result, query, memory):
    return engine.proyectar_resultado(
        result,
        query,
        memory,
        producer=_producer(result.status),
        cache_receipt=_cache_receipt(query),
    )


def _keep_oracle(raw, query, memory):
    engine = OracleEngine()
    result = engine.parse_result(raw)
    return engine.proyectar_resultado(
        result,
        query,
        memory,
        producer=_producer(result.status),
        cache_receipt=_cache_receipt(query),
    )


class _Adapter:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def ejecutar_plan(self, mensajes, **kwargs):
        return self._next(mensajes, kwargs)

    def ejecutar_plan_result(self, mensajes, **kwargs):
        text = self._next(mensajes, kwargs)
        return LLMCallResultV1(
            terminal=LLMCallTerminalV1.SUCCESS,
            final_text=text,
            final_provider="parse-fixture",
            final_model_id="parse-fixture",
            final_failure=None,
            receipts=(
                ProviderAttemptReceiptV1(
                    provider="parse-fixture",
                    model_id="parse-fixture",
                    failure_class=None,
                    error_type=None,
                    http_status=None,
                    retry_after_seconds=None,
                    started_ns=1,
                    finished_ns=2,
                ),
            ),
        )

    def _next(self, mensajes, kwargs):
        del mensajes, kwargs
        self.calls += 1
        return self.responses.pop(0)


def _assert_invariants(result: StructuredParseResult) -> None:
    assert result.contract_version == 1
    assert type(result.contract_version) is int
    assert isinstance(result.status, StructuredParseStatus)
    if result.status is StructuredParseStatus.VALID:
        assert result.value is not None
        assert result.salvage is None
        assert result.reason_codes == ()
    elif result.status is StructuredParseStatus.DEGRADED:
        assert result.value is None
        assert result.reason_codes
    else:
        assert result.value is None
        assert result.salvage is None
        assert result.reason_codes


def test_01_general_canonical_valid() -> None:
    result = ResponseParser.parse_result(_general(), {"none"})
    assert result.status is StructuredParseStatus.VALID
    assert result.value is not None and result.value.valido


def test_02_general_wrong_field_type() -> None:
    result = ResponseParser.parse_result(_general(pensamiento=7), {"none"})
    assert result.status is StructuredParseStatus.DEGRADED
    assert StructuredParseIssueCode.SCHEMA_VALIDATION_FAILED in result.reason_codes


def test_03_pensamiento_dict_never_escapes() -> None:
    result = ResponseParser.parse_result(_general(pensamiento={}), {"none"})
    assert result.value is None
    assert result.status is StructuredParseStatus.DEGRADED


def test_04_general_missing_required_field() -> None:
    raw = json.dumps({"accion": "none", "parametros": {}, "razon": ""})
    assert ResponseParser.parse_result(raw).status is StructuredParseStatus.DEGRADED


def test_05_general_empty() -> None:
    result = ResponseParser.parse_result("")
    assert result.status is StructuredParseStatus.ERROR
    assert result.reason_codes == (StructuredParseIssueCode.EMPTY_OUTPUT,)


def test_06_general_whitespace() -> None:
    assert ResponseParser.parse_result(" \r\n ").raw_shape is StructuredRawShape.EMPTY


def test_07_general_null_root() -> None:
    result = ResponseParser.parse_result("null")
    assert result.reason_codes == (StructuredParseIssueCode.WRONG_ROOT_TYPE,)
    assert result.raw_shape is StructuredRawShape.NULL


def test_08_general_list_root() -> None:
    result = ResponseParser.parse_result("[]")
    assert result.raw_shape is StructuredRawShape.ARRAY
    assert result.status is StructuredParseStatus.ERROR


def test_09_general_number_root() -> None:
    assert ResponseParser.parse_result("12").raw_shape is StructuredRawShape.NUMBER


def test_10_general_boolean_root() -> None:
    assert ResponseParser.parse_result("true").raw_shape is StructuredRawShape.BOOLEAN


def test_11_general_plain_text() -> None:
    result = ResponseParser.parse_result("respuesta libre")
    assert result.reason_codes == (StructuredParseIssueCode.PLAIN_TEXT_ONLY,)
    assert ResponseParser.project_result(result).accion == "none"


def test_12_general_exact_fence_is_valid_normalization() -> None:
    result = ResponseParser.parse_result(f"```json\n{_general()}\n```", {"none"})
    assert result.status is StructuredParseStatus.VALID
    assert result.normalizations == (StructuredNormalization.FENCE_STRIPPED,)


def test_13_general_prose_wrapper_is_degraded() -> None:
    result = ResponseParser.parse_result(f"Aqui esta: {_general()} fin", {"none"})
    assert result.status is StructuredParseStatus.DEGRADED
    assert StructuredParseIssueCode.NONCANONICAL_WRAPPER in result.reason_codes


def test_14_general_trailing_garbage_is_degraded() -> None:
    result = ResponseParser.parse_result(_general() + " trailing", {"none"})
    assert result.status is StructuredParseStatus.DEGRADED


def test_15_general_multiple_objects_is_error() -> None:
    result = ResponseParser.parse_result(_general() + " " + _general())
    assert result.status is StructuredParseStatus.ERROR
    assert result.reason_codes == (StructuredParseIssueCode.MULTIPLE_OBJECTS,)


def test_16_general_duplicate_keys_is_error() -> None:
    raw = '{"pensamiento":"x","accion":"none","accion":"hablar","parametros":{},"razon":"r"}'
    result = ResponseParser.parse_result(raw)
    assert result.status is StructuredParseStatus.ERROR
    assert result.reason_codes == (StructuredParseIssueCode.DUPLICATE_KEYS,)


def test_17_persona_canonical_valid() -> None:
    result = PersonaEngine().parsear_resultado(_persona())
    assert result.status is StructuredParseStatus.VALID
    assert result.value is not None and result.value.texto_a_comunicar == "Hola"


def test_18_persona_partial_speech_is_degraded_salvage() -> None:
    raw = '{"accion":"hablar","parametros":{"texto_a_comunicar":"Hola segura"}'
    result = PersonaEngine().parsear_resultado(raw)
    assert result.status is StructuredParseStatus.DEGRADED
    assert result.value is None
    assert result.salvage is not None and result.salvage.text == "Hola segura"


def test_19_persona_partial_without_speech_is_error() -> None:
    result = PersonaEngine().parsear_resultado('{"accion":"hablar"')
    assert result.status is StructuredParseStatus.ERROR
    assert result.salvage is None


def test_20_persona_unknown_action_is_degraded() -> None:
    result = PersonaEngine().parsear_resultado(_persona(accion="teletransportar"))
    assert result.status is StructuredParseStatus.DEGRADED
    assert StructuredParseIssueCode.UNKNOWN_ACTION in result.reason_codes


def test_21_persona_physical_action_has_no_execution_authority() -> None:
    engine = PersonaEngine()
    result = engine.parsear_resultado(_persona(accion="mover"))
    projected = engine.proyectar_resultado(result)
    assert result.value is None
    assert projected["accion"] == "hablar"


def test_22_persona_wrong_speech_type_is_not_stringified() -> None:
    result = PersonaEngine().parsear_resultado(
        _persona(parametros={"texto_a_comunicar": {"secret": "x"}})
    )
    assert result.status is StructuredParseStatus.ERROR
    assert result.salvage is None


def test_23_persona_extra_field_is_not_valid() -> None:
    result = PersonaEngine().parsear_resultado(_persona(extra="ignored-before"))
    assert result.status is StructuredParseStatus.DEGRADED
    assert result.salvage is not None


def test_24_persona_fallback_is_marked_as_policy_fallback() -> None:
    data = PersonaEngine().parsear_respuesta("")
    assert data["primary_parse_valid"] is False
    assert data["structured_parse_status"] == "ERROR"
    assert data["requiere_reintento"] is True


def test_25_persona_retry_count_remains_one() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_provider = IdentitySnapshotProvider(
        build_static_self_v1(), BehaviorCore()
    )
    orchestrator.llm_adapter = _Adapter(["", _persona()])
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="root-020-retry",
        detalles={"mensaje": "hola"},
    )
    orchestrator._resolver_ruta_persona(event, {}, "", "exploradora")
    assert orchestrator.llm_adapter.calls == 2


def test_26_oracle_canonical_valid() -> None:
    result = OracleEngine().parse_result(_oracle())
    assert result.status is StructuredParseStatus.VALID
    assert result.value is not None and result.value.contenido_tecnico == "Diagnostico"


def test_27_oracle_fenced_valid() -> None:
    result = OracleEngine().parse_result(f"```json\n{_oracle()}\n```")
    assert result.status is StructuredParseStatus.VALID
    assert result.normalizations == (StructuredNormalization.FENCE_STRIPPED,)


def test_28_oracle_prose_is_degraded() -> None:
    result = OracleEngine().parse_result(f"Resultado: {_oracle()} fin")
    assert result.status is StructuredParseStatus.DEGRADED
    assert result.salvage is not None


def test_29_oracle_plain_is_degraded_not_cacheable() -> None:
    engine = OracleEngine()
    memory = _Memory()
    parse_result = engine.parse_result("Explicacion tecnica plana")
    _project_oracle(engine, parse_result, "q", memory)
    assert parse_result.status is StructuredParseStatus.DEGRADED
    assert memory.writes == []


def test_30_oracle_alias_is_noncanonical() -> None:
    result = OracleEngine().parse_result(json.dumps({"respuesta": "legacy"}))
    assert result.status is StructuredParseStatus.DEGRADED
    assert result.reason_codes == (StructuredParseIssueCode.NONCANONICAL_WRAPPER,)


def test_31_oracle_wrong_dict_list_types_are_not_stringified() -> None:
    for invalid in ({"x": 1}, ["x"]):
        result = OracleEngine().parse_result(_oracle(contenido_tecnico=invalid))
        assert result.status is StructuredParseStatus.ERROR
        assert result.salvage is None


def test_32_oracle_malformed_is_error() -> None:
    result = OracleEngine().parse_result('{"contenido_tecnico":')
    assert result.status is StructuredParseStatus.ERROR
    assert result.reason_codes == (StructuredParseIssueCode.MALFORMED_JSON,)


def test_33_oracle_duplicate_keys_is_error() -> None:
    raw = '{"contenido_tecnico":"a","contenido_tecnico":"b"}'
    result = OracleEngine().parse_result(raw)
    assert result.reason_codes == (StructuredParseIssueCode.DUPLICATE_KEYS,)


def test_34_oracle_multiple_objects_is_error() -> None:
    result = OracleEngine().parse_result(_oracle() + " " + _oracle())
    assert result.reason_codes == (StructuredParseIssueCode.MULTIPLE_OBJECTS,)


def test_35_only_valid_oracle_writes_once() -> None:
    memory = _Memory()
    _keep_oracle(_oracle(), "q", memory)
    assert len(memory.writes) == 1


def test_36_degraded_oracle_never_writes() -> None:
    memory = _Memory()
    _keep_oracle("texto plano", "q", memory)
    assert memory.writes == []


def test_37_error_oracle_never_writes() -> None:
    memory = _Memory()
    _keep_oracle("", "q", memory)
    assert memory.writes == []


def test_38_cache_failure_does_not_mutate_parse_status() -> None:
    engine = OracleEngine()
    result = engine.parse_result(_oracle())
    _project_oracle(engine, result, "q", _Memory(fail=True))
    assert result.status is StructuredParseStatus.VALID
    assert result.value is not None


def test_39_reason_codes_are_deterministic() -> None:
    raw = "prosa " + _persona(accion="mover") + " fin"
    first = PersonaEngine().parsear_resultado(raw)
    second = PersonaEngine().parsear_resultado(raw)
    assert first.reason_codes == second.reason_codes


def test_40_impossible_states_are_rejected() -> None:
    kwargs = {
        "contract_version": 1,
        "normalizations": (),
        "raw_shape": StructuredRawShape.OBJECT,
        "raw_length": 2,
    }
    bad = [
        dict(status=StructuredParseStatus.VALID, value=None, salvage=None, reason_codes=()),
        dict(status=StructuredParseStatus.DEGRADED, value="authority", salvage=None, reason_codes=(StructuredParseIssueCode.MALFORMED_JSON,)),
        dict(status=StructuredParseStatus.ERROR, value=None, salvage=StructuredTextSalvage(StructuredTextSalvageKind.PERSONA_SAFE_TEXT, "x"), reason_codes=(StructuredParseIssueCode.MALFORMED_JSON,)),
    ]
    for state in bad:
        try:
            StructuredParseResult(**kwargs, **state)
        except ValueError:
            continue
        raise AssertionError("impossible StructuredParseResult accepted")


def test_41_contract_version_is_strict_integer_one() -> None:
    for invalid in (True, 1.0, "1", 0, 2):
        try:
            StructuredParseResult(
                contract_version=invalid,
                status=StructuredParseStatus.ERROR,
                value=None,
                salvage=None,
                reason_codes=(StructuredParseIssueCode.EMPTY_OUTPUT,),
                normalizations=(),
                raw_shape=StructuredRawShape.EMPTY,
                raw_length=0,
            )
        except ValueError:
            continue
        raise AssertionError("non-strict contract_version accepted")


def test_42_raw_output_is_absent_from_result_contract() -> None:
    names = {field.name for field in fields(StructuredParseResult)}
    assert "raw" not in names
    assert "raw_response" not in names
    assert "prompt" not in names
    assert "memory_context" not in names


def test_43_raw_output_is_absent_from_touched_logs() -> None:
    canary = "RAW_PRIVATE_CANARY_020"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    loggers = [
        logging.getLogger("ai_engine.response_parser"),
        logging.getLogger("ai_engine.persona_engine"),
        logging.getLogger("ai_engine.oracle_engine"),
    ]
    for logger in loggers:
        logger.addHandler(handler)
    try:
        ResponseParser.parse_result("{broken " + canary)
        PersonaEngine().parsear_resultado("{broken " + canary)
        OracleEngine().parse_result("{broken " + canary)
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
    assert canary not in stream.getvalue()


def test_44_expected_malformed_inputs_do_not_escape() -> None:
    samples = ["", " ", "null", "[]", "1", "true", "plain", "{", '{"x":}', "{}"]
    for raw in samples:
        _assert_invariants(ResponseParser.parse_result(raw))
        _assert_invariants(PersonaEngine().parsear_resultado(raw))
        _assert_invariants(OracleEngine().parse_result(raw))


def test_45_unexpected_runtime_error_is_not_mislabeled() -> None:
    with patch(
        "ai_engine.structured_parse_types.json.JSONDecoder.raw_decode",
        side_effect=RuntimeError("internal bug"),
    ):
        try:
            ResponseParser.parse_result(_general())
        except RuntimeError:
            return
    raise AssertionError("unexpected parser bug was swallowed")


def test_46_unicode_is_preserved() -> None:
    result = PersonaEngine().parsear_resultado(
        _persona(parametros={"texto_a_comunicar": "Hola, niña 🌙"})
    )
    assert result.value is not None
    assert result.value.texto_a_comunicar == "Hola, niña 🌙"


def test_47_bounded_large_text() -> None:
    result = OracleEngine().parse_result("x" * 20_000)
    assert result.status is StructuredParseStatus.DEGRADED
    assert result.salvage is not None
    assert len(result.salvage.text) == 12_000
    assert result.raw_length == 20_000


def test_48_legacy_wrapper_parses_only_once() -> None:
    inspector = __import__(
        "ai_engine.structured_parse_types",
        fromlist=["inspect_json_object"],
    ).inspect_json_object

    engine = PersonaEngine()
    with patch(
        "ai_engine.persona_engine.inspect_json_object",
        wraps=inspector,
    ) as parser:
        engine.parsear_respuesta(_persona())
    assert parser.call_count == 1

    with patch(
        "ai_engine.response_parser.inspect_json_object",
        wraps=inspector,
    ) as parser:
        ResponseParser.parse(_general(), {"none"})
    assert parser.call_count == 1

    with patch(
        "ai_engine.oracle_engine.inspect_json_object",
        wraps=inspector,
    ) as parser:
        _keep_oracle(_oracle(), "q", _Memory())
    assert parser.call_count == 1


def test_49_canonical_persona_adds_no_llm_call() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_provider = IdentitySnapshotProvider(
        build_static_self_v1(), BehaviorCore()
    )
    orchestrator.llm_adapter = _Adapter([
        json.dumps({
            "version": 1,
            "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}],
        })
    ])
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="root-020-single",
        detalles={"mensaje": "hola"},
    )
    orchestrator._resolver_ruta_persona(event, {}, "", "exploradora")
    assert orchestrator.llm_adapter.calls == 1


def test_50_audit_trace_v1_has_no_parse_fields() -> None:
    names = {field.name for field in fields(AuditTrace)}
    assert not {"parse_status", "reason_codes", "normalizations"}.intersection(names)


def test_51_api_dtos_have_no_parse_fields() -> None:
    for model in (AccionSaliente, RespuestaAPI):
        assert not {
            "parse_status",
            "reason_codes",
            "normalizations",
        }.intersection(model.model_fields)


def test_52_ollama_preserves_parser_evidence() -> None:
    from ai_engine.ollama_client import OllamaClient

    source = inspect.getsource(OllamaClient.attempt_async)
    assert "_limpiar_respuesta" not in source
    assert "isinstance(text, str)" in source


def test_53_think_normalization_is_structural() -> None:
    result = ResponseParser.parse_result(
        f"<think>discarded</think>\n{_general()}",
        {"none"},
    )
    assert result.status is StructuredParseStatus.VALID
    assert result.normalizations == (StructuredNormalization.THINK_BLOCK_STRIPPED,)
    inside = ResponseParser.parse_result(
        _general(pensamiento="<think>contenido JSON</think>"),
        {"none"},
    )
    assert inside.status is StructuredParseStatus.VALID
    assert inside.normalizations == ()


def test_54_raw_shape_enum_is_finite() -> None:
    assert {item.value for item in StructuredRawShape} == {
        "EMPTY",
        "OBJECT",
        "ARRAY",
        "STRING",
        "NUMBER",
        "BOOLEAN",
        "NULL",
        "UNKNOWN",
    }


def test_55_deterministic_thirty_case_corpus() -> None:
    corpus = [
        _general(),
        f"```json\n{_general()}\n```",
        f"prose {_general()} end",
        _general() + " trailing",
        _general() + " " + _general(),
        '{"pensamiento":"x","accion":"none","accion":"x","parametros":{},"razon":""}',
        "",
        " ",
        "null",
        "[]",
        "0",
        "false",
        "plain",
        "{",
        '{"x":}',
        "{}",
        _persona(),
        _persona(accion="mover"),
        _persona(parametros={"texto_a_comunicar": []}),
        _persona(extra=True),
        '{"accion":"hablar","parametros":{"texto_a_comunicar":"safe"}',
        _oracle(),
        f"```json\n{_oracle()}\n```",
        f"prose {_oracle()}",
        json.dumps({"respuesta": "legacy"}),
        _oracle(contenido_tecnico={}),
        '{"contenido_tecnico":"a","contenido_tecnico":"b"}',
        _oracle() + " " + _oracle(),
        "Unicode técnico ñ 🌙",
        "x" * 4_096,
    ]
    assert len(corpus) == 30
    for raw in corpus:
        general = ResponseParser.parse_result(raw, {"none"})
        persona = PersonaEngine().parsear_resultado(raw)
        oracle = OracleEngine().parse_result(raw)
        for result in (general, persona, oracle):
            _assert_invariants(result)
        assert ResponseParser.project_result(general).accion in {"none"}
        assert PersonaEngine().proyectar_resultado(persona)["accion"] == "hablar"
        memory = _Memory()
        _project_oracle(OracleEngine(), oracle, "q", memory)
        if oracle.status is StructuredParseStatus.VALID:
            assert len(memory.writes) == 1
        else:
            assert memory.writes == []


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-020 structured parsing: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
