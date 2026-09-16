"""ROOT-008: contrato tipado, grounding de repair y proyeccion publica minima."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_engine.decision_outcome import (
    DecisionAttempt,
    DecisionDisposition,
    DecisionLayer,
    DecisionOutcome,
    DecisionQuality,
    DecisionReasonCode,
    MotorOutcomeStatus,
)
from ai_engine.identity_projection import IdentityContextV1
from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    LLMFailureClassV1,
    ProviderAttemptReceiptV1,
)
from ai_engine.knowledge_provenance import (
    CacheEntryIdentityV1,
    CacheLookupStatusV1,
    cache_receipt_for_lookup,
    cache_receipt_not_checked,
    legacy_unknown_provenance,
    local_deterministic_producer,
    provenance_for,
    retrieval_from_cache,
)
from ai_engine.motor_engine import MotorEngine
from ai_engine.motor_resolution import CandidateMatch, MotorResolution
from ai_engine.persona_engine import PersonaEngine
from ai_engine.provider_prompt_projection import project_chat_messages
from ai_engine.response_parser import ResponseParser
from ai_engine.schemas import ActionResponse, OracleResult
from ai_engine.oracle_memory import OracleCacheLookupResultV1
from ai_engine.structured_parse_types import StructuredParseStatus
from api.errors import install_public_error_handlers
from api.routes import router
from api.schemas import AccionSaliente, DecisionPublica, PublicErrorEnvelope, RespuestaAPI
from core.orchestrator import Orchestrator
from core.identity_snapshot import (
    IdentitySnapshotV1,
    PersonalitySnapshotV1,
    SelfStateSnapshotV1,
    build_static_self_v1,
)


IDENTITY = "Lazzaro"
MEMORY = "MEMORY_CANARY_ROOT008"
PERCEPTION = "PERCEPTION_CANARY_ROOT008"
ORACLE = "ORACLE_CANARY_ROOT008"
USER = "USER_CANARY_ROOT008"
FAILED_RAW = "FAILED_RAW_CANARY_ROOT008"

IDENTITY_CONTEXT = IdentityContextV1.from_snapshot(
    IdentitySnapshotV1(
        schema_version=1,
        generation=1,
        self=build_static_self_v1(),
        state=SelfStateSnapshotV1(energia=80, estres=10, foco=70),
        traits=PersonalitySnapshotV1(0, 0, 0, 0, 0),
    )
)


def _persona_raw(text: str = "Hola") -> str:
    return json.dumps(
        {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": text},
            "pensamiento_privado": "privado",
            "razon": "social",
        }
    )


def _persona_salvage(text: str = "Hola") -> str:
    return '{"accion":"hablar","parametros":{"texto_a_comunicar":"' + text + '"}'


class _Identity:
    def __init__(self) -> None:
        self.value = IDENTITY
        self.calls = 0

    def inyectar_en_prompt(self) -> str:
        self.calls += 1
        return self.value


class _Adapter:
    def __init__(
        self,
        responses: list[str],
        after_first: Callable[[], None] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.after_first = after_first
        self.calls: list[list[dict[str, str]]] = []

    def ejecutar_plan(self, mensajes, **kwargs) -> str:
        return self._next(mensajes, kwargs)

    def ejecutar_plan_result(self, mensajes, **kwargs) -> LLMCallResultV1:
        response = self._next(mensajes, kwargs)
        failure = None if response.strip() else LLMFailureClassV1.EMPTY_RESPONSE
        receipt = ProviderAttemptReceiptV1(
            provider="persona-fixture",
            model_id="persona-fixture",
            failure_class=failure,
            error_type=None if failure is None else "EmptyResponse",
            http_status=None,
            retry_after_seconds=None,
            started_ns=1,
            finished_ns=2,
        )
        return LLMCallResultV1(
            terminal=(
                LLMCallTerminalV1.SUCCESS
                if failure is None
                else LLMCallTerminalV1.NON_RETRYABLE_FAILURE
            ),
            final_text=response or None,
            final_provider="persona-fixture",
            final_model_id="persona-fixture",
            final_failure=failure,
            receipts=(receipt,),
        )

    def _next(self, mensajes, kwargs) -> str:
        del kwargs
        self.calls.append(deepcopy(mensajes))
        response = self.responses.pop(0)
        if len(self.calls) == 1 and self.after_first is not None:
            self.after_first()
        return response


def _serialized_call(plan) -> str:
    return "\n".join(message.content for message in project_chat_messages(plan))


class _UnusedMemory:
    def __init__(self) -> None:
        self.calls = 0

    def obtener_contexto_memoria(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        raise AssertionError("repair must not retrieve memory")


def _event(origin: str = "terminal"):
    from api.schemas import EventoEntrante

    return EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion="root-008-fixture",
        detalles={
            "mensaje": USER,
            "resumen_percepcion": PERCEPTION,
            "solicita_decision": True,
        },
    )


def _persona_turn(
    responses: list[str],
    *,
    origin: str = "terminal",
    after_first: Callable[[], None] | None = None,
):
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_state = _Identity()
    orchestrator.memory = _UnusedMemory()
    orchestrator.llm_adapter = _Adapter(responses, after_first)
    event = _event(origin)
    result = orchestrator._resolver_ruta_persona(
        event,
        {"percepcion": {"resumen_percepcion": PERCEPTION}},
        MEMORY,
        "exploradora",
        identity_context=IDENTITY_CONTEXT,
    )
    return result, orchestrator, event


def _oracle_turn(responses: list[str], *, origin: str = "terminal"):
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_state = _Identity()
    orchestrator.llm_adapter = _Adapter(responses)
    event = _event(origin)
    oracle = OracleResult(
        pregunta_original=USER,
        contenido_tecnico=ORACLE,
        resumen_para_persona="oracle summary",
        provenance=provenance_for(
            local_deterministic_producer(),
            cache_receipt_not_checked(),
        ),
    )
    result = orchestrator._verbalizar_resultado_oracle(
        event,
        {"percepcion": {"resumen_percepcion": PERCEPTION}},
        MEMORY,
        "exploradora",
        oracle,
        identity_context=IDENTITY_CONTEXT,
    )
    return result, orchestrator, oracle


def _motor_metadata(status: str, *, resolver_exception: bool = False) -> dict:
    return {
        "motor_resolution": {"status": status, "candidates": []},
        "motor_audit": (
            {"status_reason": "resolver_exception", "error_type": "RuntimeError"}
            if resolver_exception
            else {}
        ),
    }


def _outcome(
    disposition: DecisionDisposition = DecisionDisposition.ACTION,
    quality: DecisionQuality = DecisionQuality.VALIDATED,
    reason: DecisionReasonCode = DecisionReasonCode.PRIMARY_VALID,
) -> DecisionOutcome:
    return DecisionOutcome(
        disposition=disposition,
        quality=quality,
        attempt=DecisionAttempt.FIRST,
        layer=DecisionLayer.CORE,
        reason_codes=(reason,),
        retry_count=0,
    )


class _FixedOrchestrator:
    def __init__(self, action: AccionSaliente | None) -> None:
        self.action = action

    def procesar_evento(self, evento):
        del evento
        return self.action


def _post(action: AccionSaliente | None) -> dict:
    app = FastAPI()
    app.state.orchestrator = _FixedOrchestrator(action)
    install_public_error_handlers(app)
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/evento",
            json={
                "origen": "unity",
                "tipo_evento": "comando_usuario",
                "id_transaccion": "root-008-api",
                "detalles": {},
            },
        )
    assert response.status_code == 200
    return response.json()


# 1-15 Persona grounding and repair.
def test_01_canonical_persona_has_no_retry() -> None:
    response, orchestrator, _ = _persona_turn([_persona_raw()])
    assert len(orchestrator.llm_adapter.calls) == 1
    assert response.outcome.is_primary


def test_02_degraded_persona_uses_safe_salvage() -> None:
    response, _, _ = _persona_turn([_persona_salvage()])
    assert response.valido and response.accion == "hablar"
    assert response.outcome.quality is DecisionQuality.SALVAGED


def test_03_persona_error_has_exactly_one_retry() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert len(orchestrator.llm_adapter.calls) == 2


def test_04_valid_retry_is_typed_as_repair() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert response.outcome.attempt is DecisionAttempt.REPAIR
    assert response.outcome.quality is DecisionQuality.VALIDATED
    assert response.outcome.reason_codes[0] is DecisionReasonCode.RETRY_REPAIRED


def test_05_degraded_retry_keeps_salvaged_provenance() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_salvage()])
    assert response.valido and response.outcome.attempt is DecisionAttempt.REPAIR
    assert response.outcome.quality is DecisionQuality.SALVAGED


def test_06_failed_retry_becomes_deterministic_fallback() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, FAILED_RAW])
    assert response.valido and response.accion == "hablar"
    assert response.outcome.quality is DecisionQuality.DETERMINISTIC_FALLBACK


def test_07_retry_preserves_user_message() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert USER in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_08_retry_preserves_identity_snapshot() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert IDENTITY in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_09_retry_preserves_memory_snapshot() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert MEMORY in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_10_retry_preserves_perception_snapshot() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert PERCEPTION in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_11_retry_preserves_channel_restriction() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert "Canal terminal" in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_12_identity_mutation_does_not_reground_retry() -> None:
    holder: dict[str, object] = {}

    def mutate() -> None:
        identity = holder["orchestrator"].identity_state
        identity.value = "MUTATED_IDENTITY_ROOT008"

    orchestrator = object.__new__(Orchestrator)
    holder["orchestrator"] = orchestrator
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_state = _Identity()
    orchestrator.memory = _UnusedMemory()
    orchestrator.llm_adapter = _Adapter([FAILED_RAW, _persona_raw()], mutate)
    orchestrator._resolver_ruta_persona(_event(), {"percepcion": {"resumen_percepcion": PERCEPTION}}, MEMORY, "exploradora", identity_context=IDENTITY_CONTEXT)
    retry = _serialized_call(orchestrator.llm_adapter.calls[1])
    assert IDENTITY in retry and "MUTATED_IDENTITY_ROOT008" not in retry
    assert orchestrator.identity_state.calls == 0


def test_13_retry_does_not_retrieve_memory() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert orchestrator.memory.calls == 0


def test_14_retry_uses_original_perception_after_event_mutation() -> None:
    event = _event()
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_state = _Identity()
    orchestrator.llm_adapter = _Adapter(
        [FAILED_RAW, _persona_raw()],
        lambda: event.detalles.update({"resumen_percepcion": "MUTATED_PERCEPTION_ROOT008"}),
    )
    orchestrator._resolver_ruta_persona(event, {"percepcion": {"resumen_percepcion": PERCEPTION}}, MEMORY, "exploradora", identity_context=IDENTITY_CONTEXT)
    retry = _serialized_call(orchestrator.llm_adapter.calls[1])
    assert PERCEPTION in retry and "MUTATED_PERCEPTION_ROOT008" not in retry


def test_15_persona_never_exceeds_two_llm_calls() -> None:
    _, orchestrator, _ = _persona_turn([FAILED_RAW, FAILED_RAW, _persona_raw("third")])
    assert len(orchestrator.llm_adapter.calls) == 2


# 16-25 Oracle verbalization.
def test_16_oracle_verbalization_valid_first_try() -> None:
    response, orchestrator, _ = _oracle_turn([_persona_raw()])
    assert len(orchestrator.llm_adapter.calls) == 1
    assert response.outcome.layer is DecisionLayer.ORACLE_PERSONA


def test_17_malformed_oracle_verbalization_retries_once() -> None:
    _, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert len(orchestrator.llm_adapter.calls) == 2


def test_18_oracle_retry_preserves_exact_result_content() -> None:
    _, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert ORACLE in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_19_oracle_retry_preserves_memory() -> None:
    _, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert MEMORY in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_20_oracle_retry_preserves_identity() -> None:
    _, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert IDENTITY in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_21_oracle_retry_preserves_perception() -> None:
    _, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert PERCEPTION in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_22_oracle_retry_preserves_channel_restriction() -> None:
    _, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert "Canal terminal" in _serialized_call(orchestrator.llm_adapter.calls[1])


def test_23_oracle_repair_does_not_issue_second_query() -> None:
    response, orchestrator, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert response.outcome.attempt is DecisionAttempt.REPAIR
    assert len(orchestrator.llm_adapter.calls) == 2


def test_24_oracle_cache_is_not_looked_up_by_repair() -> None:
    class CachedOracle:
        def __init__(self) -> None:
            self.calls = 0

        def evaluar_cache(self, message, memory):
            del memory
            self.calls += 1
            cache_receipt = cache_receipt_for_lookup(
                message,
                status=CacheLookupStatusV1.LEGACY_ENTRY,
                threshold=0.35,
                matched_entry=CacheEntryIdentityV1(
                    entry_id="legacy-test-entry",
                    logical_key=None,
                    stored_query_sha256=None,
                    cache_schema_version=None,
                    knowledge_contract_version=None,
                    provenance_version=None,
                    entry_created_at_utc=None,
                ),
                distance=0.1,
            )
            result = OracleResult(
                pregunta_original=USER,
                contenido_tecnico=ORACLE,
                provenance=legacy_unknown_provenance(
                    retrieval=retrieval_from_cache(cache_receipt)
                ),
            )
            return OracleCacheLookupResultV1(1, result, cache_receipt)

    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.identity_state = _Identity()
    orchestrator.llm_adapter = _Adapter([FAILED_RAW, _persona_raw()])
    orchestrator.oracle_engine = CachedOracle()
    orchestrator.oracle_memory = object()
    orchestrator._resolver_ruta_oracle(_event(), {"percepcion": {"resumen_percepcion": PERCEPTION}}, MEMORY, "exploradora", identity_context=IDENTITY_CONTEXT)
    assert orchestrator.oracle_engine.calls == 1


def test_25_oracle_repair_marks_final_path() -> None:
    response, _, _ = _oracle_turn([FAILED_RAW, _persona_raw()])
    assert response.outcome.retry_count == 1
    assert response.outcome.primary_parse_status is StructuredParseStatus.ERROR
    assert response.outcome.final_parse_status is StructuredParseStatus.VALID


# 26-32 AI contract and public projection.
def test_26_primary_speech_is_operational_and_primary() -> None:
    response, _, _ = _persona_turn([_persona_raw()])
    assert response.valido and response.outcome.is_primary


def test_27_salvage_speech_is_operational_but_not_primary() -> None:
    response, _, _ = _persona_turn([_persona_salvage()])
    assert response.valido and not response.outcome.is_primary


def test_28_fallback_speech_is_operational_but_not_primary() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, FAILED_RAW])
    assert response.valido and response.outcome.fallback_used


def test_29_retry_success_is_operational_and_repaired() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert response.valido and response.outcome.attempt is DecisionAttempt.REPAIR


def test_30_action_projection_keeps_internal_outcome() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    outgoing = orchestrator._a_accion_saliente(response, "exploradora")
    assert outgoing.decision_outcome == response.outcome
    durable = orchestrator._serializar_accion_durable(outgoing)
    assert DecisionOutcome.model_validate(durable["decision_outcome"]) == response.outcome
    orchestrator.action_ledger = type("Ledger", (), {"get_action": lambda self, action_id: None})()
    restored = orchestrator._restaurar_accion_desde_ledger(durable)
    assert restored is not None and restored.decision_outcome == response.outcome


def test_31_public_projection_contains_only_three_fields() -> None:
    assert set(_outcome().public_projection()) == {"version", "disposition", "reason_code"}


def test_32_private_cognition_never_crosses_public_projection() -> None:
    internal = ActionResponse(
        pensamiento="PRIVATE_ROOT008",
        accion="hablar",
        parametros={"texto_a_comunicar": "public"},
        razon="PRIVATE_REASON_ROOT008",
        outcome=_outcome(),
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    body = _post(orchestrator._a_accion_saliente(internal, "exploradora"))
    encoded = json.dumps(body)
    assert "PRIVATE_ROOT008" not in encoded and "PRIVATE_REASON_ROOT008" not in encoded


# 33-45 Motor states.
def test_33_motor_ready_is_action() -> None:
    response = Orchestrator._action_response_desde_motor(
        {"accion": "navegar_a_objeto", "parametros": {"nombre_objeto": "Cube"}, "metadata": _motor_metadata("ready")},
        "",
    )
    assert response.outcome.disposition is DecisionDisposition.ACTION
    assert response.outcome.motor_status is MotorOutcomeStatus.READY


def test_34_motor_clarification_is_distinct() -> None:
    response = Orchestrator._respuesta_resolucion_motor({"estado": "requires_clarification", "accion": "agarrar_objeto", "metadata": _motor_metadata("requires_clarification")})
    assert response.outcome.disposition is DecisionDisposition.CLARIFICATION


def test_35_motor_not_found_is_blocked() -> None:
    response = Orchestrator._respuesta_resolucion_motor({"estado": "not_found", "accion": "agarrar_objeto", "metadata": _motor_metadata("not_found")})
    assert response.outcome.reason_codes[0] is DecisionReasonCode.TARGET_NOT_FOUND


def test_36_motor_not_allowed_is_blocked() -> None:
    response = Orchestrator._respuesta_resolucion_motor({"estado": "not_allowed", "accion": "agarrar_objeto", "metadata": _motor_metadata("not_allowed")})
    assert response.outcome.reason_codes[0] is DecisionReasonCode.ACTION_NOT_ALLOWED


def test_37_motor_unsafe_is_blocked() -> None:
    response = Orchestrator._respuesta_resolucion_motor({"estado": "unsafe", "accion": "agarrar_objeto", "metadata": _motor_metadata("unsafe")})
    assert response.outcome.disposition is DecisionDisposition.BLOCKED
    assert response.outcome.motor_status is MotorOutcomeStatus.UNSAFE


def test_38_passive_none_differs_from_unsafe_none() -> None:
    passive = Orchestrator._accion_pasiva()
    unsafe = Orchestrator._respuesta_resolucion_motor({"estado": "unsafe", "accion": "agarrar_objeto", "metadata": _motor_metadata("unsafe")})
    assert passive.accion == unsafe.accion == "none"
    assert passive.outcome.disposition is DecisionDisposition.NO_ACTION
    assert unsafe.outcome.disposition is DecisionDisposition.BLOCKED


def test_39_resolver_exception_differs_from_passive_none() -> None:
    failed = Orchestrator._respuesta_resolucion_motor({"estado": "requires_clarification", "accion": "agarrar_objeto", "metadata": _motor_metadata("requires_clarification", resolver_exception=True)})
    assert failed.outcome.disposition is DecisionDisposition.ERROR
    assert failed.outcome.reason_codes[0] is DecisionReasonCode.MOTOR_RESOLUTION_FAILED


def test_40_legacy_valid_motor_path_stays_compatible() -> None:
    response = Orchestrator._action_response_desde_motor({"accion": "girar", "parametros": {"grados": 90}, "metadata": {}}, "")
    assert response.valido and response.accion == "girar"
    assert response.outcome.motor_status is MotorOutcomeStatus.READY


def test_41_motor_resolution_metadata_survives_internal_projection() -> None:
    metadata = _motor_metadata("ready")
    response = Orchestrator._action_response_desde_motor({"accion": "girar", "parametros": {"grados": 90}, "metadata": metadata}, "")
    assert response.metadata["motor_resolution"]["status"] == "ready"


def test_42_api_distinguishes_blocked_none() -> None:
    action = AccionSaliente(id_accion="blocked", nombre="none").attach_decision_outcome(
        _outcome(DecisionDisposition.BLOCKED, reason=DecisionReasonCode.UNSAFE_BLOCK)
    )
    body = _post(action)
    assert body["accion"] is None
    assert body["decision"] == {"version": 1, "disposition": "blocked", "reason_code": "unsafe_block"}
    passive = AccionSaliente(id_accion="passive", nombre="none").attach_decision_outcome(
        _outcome(DecisionDisposition.NO_ACTION, reason=DecisionReasonCode.PASSIVE_NO_ACTION)
    )
    passive_body = _post(passive)
    assert passive_body["accion"] is None
    assert passive_body["decision"]["disposition"] == "no_action"
    for disposition, quality, reason in (
        (DecisionDisposition.INVALID, DecisionQuality.REJECTED, DecisionReasonCode.PARSE_ERROR),
        (DecisionDisposition.ERROR, DecisionQuality.FAILED, DecisionReasonCode.PIPELINE_ERROR),
    ):
        reserved_projection = DecisionPublica.from_outcome(
            _outcome(disposition, quality, reason)
        ).model_dump(mode="json")
        assert reserved_projection == {
            "version": 1,
            "disposition": disposition.value,
            "reason_code": reason.value,
        }


def test_43_execution_lifecycle_remains_separate() -> None:
    action = AccionSaliente(id_accion="ready", nombre="hablar", estado_ejecucion="dispatched").attach_decision_outcome(_outcome())
    body = _post(action)
    assert body["accion"]["estado_ejecucion"] == "dispatched"
    assert body["decision"]["disposition"] == "action"


def test_44_unsafe_has_zero_physical_action() -> None:
    response = Orchestrator._respuesta_resolucion_motor({"estado": "unsafe", "accion": "agarrar_objeto", "metadata": _motor_metadata("unsafe")})
    assert response.valido and response.accion == "none" and response.parametros == {}


def test_45_clarification_has_zero_physical_action() -> None:
    response = Orchestrator._respuesta_resolucion_motor({"estado": "requires_clarification", "accion": "agarrar_objeto", "metadata": _motor_metadata("requires_clarification")})
    assert response.accion == "hablar" and response.outcome.disposition is DecisionDisposition.CLARIFICATION


# 46-50 general parser/adapters.
def test_46_general_valid_none_is_legitimate_no_action() -> None:
    raw = json.dumps({"pensamiento": "x", "accion": "none", "parametros": {}, "razon": "x"})
    response = ResponseParser.parse(raw, {"none"})
    assert response.valido and response.outcome.disposition is DecisionDisposition.NO_ACTION


def test_47_general_degraded_is_invalid_not_no_action() -> None:
    raw = json.dumps({"accion": "none", "parametros": {}, "razon": "x"})
    response = ResponseParser.parse(raw, {"none"})
    assert not response.valido and response.outcome.disposition is DecisionDisposition.INVALID


def test_48_general_error_is_rejected() -> None:
    response = ResponseParser.parse(FAILED_RAW, {"none"})
    assert response.outcome.quality is DecisionQuality.REJECTED
    assert response.outcome.final_parse_status is StructuredParseStatus.ERROR


def test_49_fallback_outcome_survives_sanitization() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, FAILED_RAW])
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    sanitized = orchestrator._sanitizar_action_response(response)
    assert sanitized.outcome == response.outcome
    legacy = ActionResponse(
        pensamiento="",
        accion="navegar_a_posicion",
        parametros={"x": float("nan"), "y": 0, "z": 0},
        razon="fixture legacy",
    )
    rejected = orchestrator._sanitizar_action_response(legacy)
    assert rejected.outcome.quality is DecisionQuality.REJECTED
    assert rejected.outcome.attempt is DecisionAttempt.FIRST
    assert rejected.outcome.reason_codes == (
        DecisionReasonCode.INVALID_PARAMETERS,
        DecisionReasonCode.LEGACY_UNTYPED,
    )


def test_50_repeated_adapters_do_not_recompute_outcome() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    first = orchestrator._a_accion_saliente(response, "exploradora")
    second = orchestrator._a_accion_saliente(response, "exploradora")
    assert first.decision_outcome is second.decision_outcome


# 51-60 contract, privacy and authority.
def test_51_contract_version_is_strict_integer_one() -> None:
    for invalid in (True, 1.0, "1", 2):
        try:
            _outcome().evolve(contract_version=invalid)
        except ValidationError:
            continue
        raise AssertionError(f"invalid contract version accepted: {invalid!r}")


def test_52_impossible_combinations_are_rejected() -> None:
    invalid = [
        {"attempt": DecisionAttempt.REPAIR, "retry_count": 0},
        {"quality": DecisionQuality.REJECTED, "disposition": DecisionDisposition.ACTION},
        {"quality": DecisionQuality.FAILED, "disposition": DecisionDisposition.ACTION},
        {"motor_status": MotorOutcomeStatus.UNSAFE, "layer": DecisionLayer.CORE},
    ]
    for updates in invalid:
        try:
            _outcome().evolve(**updates)
        except ValidationError:
            continue
        raise AssertionError(f"impossible outcome accepted: {updates}")


def test_53_enums_are_finite() -> None:
    assert {item.value for item in DecisionDisposition} == {"action", "no_action", "clarification", "blocked", "invalid", "error"}
    assert len(DecisionReasonCode) == 21


def test_54_reason_code_projection_is_deterministic() -> None:
    outcome = _outcome(reason=DecisionReasonCode.CHANNEL_BLOCKED)
    assert outcome.public_projection()["reason_code"] == "channel_blocked"
    assert outcome.public_projection() == outcome.public_projection()


def test_55_outcome_contains_no_raw_exception() -> None:
    encoded = json.dumps(_outcome(DecisionDisposition.ERROR, DecisionQuality.FAILED, DecisionReasonCode.PIPELINE_ERROR).model_dump(mode="json"))
    assert "Traceback" not in encoded and "RuntimeError(" not in encoded


def test_56_failed_raw_is_not_stored_in_outcome() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, FAILED_RAW])
    assert FAILED_RAW not in json.dumps(response.outcome.model_dump(mode="json"))


def test_57_prompt_is_not_stored_in_outcome() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert "FORMATO DE SALIDA" not in json.dumps(response.outcome.model_dump(mode="json"))


def test_58_memory_is_not_stored_in_outcome() -> None:
    response, _, _ = _persona_turn([FAILED_RAW, _persona_raw()])
    assert MEMORY not in json.dumps(response.outcome.model_dump(mode="json"))


def test_59_public_schema_has_no_internal_provenance() -> None:
    public_fields = set(RespuestaAPI.model_fields["decision"].annotation.__args__[0].model_fields)
    assert public_fields == {"version", "disposition", "reason_code"}
    assert "decision_outcome" not in AccionSaliente.model_json_schema()["properties"]
    unity = (Path(__file__).resolve().parents[1] / "unity_client" / "DTOs.cs").read_text(encoding="utf-8")
    decision_dto = unity.split("public class DecisionPublica", 1)[1].split("}", 1)[0]
    assert "public DecisionPublica decision;" in unity
    assert all(field in decision_dto for field in ("version", "disposition", "reason_code"))
    assert all(field not in decision_dto for field in ("quality", "attempt", "layer", "retry_count"))


def test_60_decision_outcome_is_single_authority() -> None:
    internal = ActionResponse(
        pensamiento="",
        accion="none",
        parametros={},
        metadata={"disposition": "action", "reason_code": "primary_valid"},
        outcome=_outcome(
            DecisionDisposition.BLOCKED,
            reason=DecisionReasonCode.UNSAFE_BLOCK,
        ),
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    action = orchestrator._a_accion_saliente(internal, "exploradora")
    body = _post(action)
    assert body["decision"]["disposition"] == "blocked"
    assert set(PublicErrorEnvelope.model_fields) == {"version", "status", "error"}


def test_61_motor_diagnosis_resolves_each_target_exactly_once() -> None:
    perception = {
        "snapshot_id": "root-008-single-resolution",
        "source": "unity",
        "objetos_normalizados": [
            {
                "id": "Cube_01",
                "display_name": "Cube_01",
                "nombre": "Cube_01",
                "aliases": ["cubo"],
                "affordances": ["navegar"],
                "distance": 2.0,
            }
        ],
    }

    def resolution(status: str, target: str) -> MotorResolution:
        candidate = CandidateMatch(
            target_id=target,
            display_name=target,
            legacy_nombre_objeto=target,
            confidence=99.0,
            affordance_decision={"allowed": status not in {"not_allowed", "unsafe"}},
        )
        return MotorResolution(
            status=status,  # type: ignore[arg-type]
            action="navegar_a_objeto",
            target_id=target if status != "not_found" else None,
            target_display_name=target if status != "not_found" else None,
            legacy_nombre_objeto=target if status != "not_found" else None,
            confidence=99.0 if status != "not_found" else 0.0,
            candidates=[candidate] if status != "not_found" else [],
            reason=f"fixture {status}",
            audit={"status_reason": f"fixture_{status}"},
        )

    class ReasoningForbidden:
        def process(self, **kwargs):
            del kwargs
            raise AssertionError("a typed Motor diagnosis must not be reinterpreted")

    for status in (
        "ready",
        "requires_clarification",
        "not_found",
        "not_allowed",
        "unsafe",
    ):
        calls = {"count": 0}
        motor = MotorEngine()

        def changing_resolver(**kwargs):
            del kwargs
            calls["count"] += 1
            return resolution(status, "Cube_01" if calls["count"] == 1 else "Cube_02")

        motor._resolver_objetivo_fuzzy = changing_resolver  # type: ignore[method-assign]
        orchestrator = object.__new__(Orchestrator)
        orchestrator.motor_engine = motor
        orchestrator.reasoning = ReasoningForbidden()
        event = _event("unity")
        event.detalles["mensaje"] = "ve al cubo"
        event.detalles.update(perception)

        response = orchestrator._resolver_ruta_motor(
            evento=event,
            contexto_llm={},
            memoria_texto="",
            herramientas=["navegar_a_objeto"],
            herramientas_detalle={},
            politica="exploradora",
            tarea=None,
            triage_decision={"intent": "MOTOR_SIMPLE", "route": "motor"},
        )

        assert calls["count"] == 1, f"{status} resolved the target more than once"
        assert response.metadata["motor_resolution"]["status"] == status
        if status == "ready":
            assert response.accion == "navegar_a_objeto"
            assert response.parametros["nombre_objeto"] == "Cube_01"
            assert response.metadata["motor_resolution"]["target_id"] == "Cube_01"
            assert "Cube_02" not in json.dumps(response.model_dump(mode="json"))
        else:
            assert response.accion not in MotorEngine.ACCIONES_CON_OBJETO


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-008 decision outcome: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
