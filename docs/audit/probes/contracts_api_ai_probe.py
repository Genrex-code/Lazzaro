"""Standalone evidence for Fase 2 / Block 2 API and AI contracts.

The probe imports real schemas, parsers, route functions and conversion methods,
but never starts the app, creates Chroma, calls an LLM, or writes AuditTrace. Human
logs are disabled before application imports. Output contains only contract facts
and booleans; synthetic private/error markers are never printed.

PASS means current behavior was reproduced, not that the contract is desirable.
Run only in a fresh process from any working directory.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
logging.getLogger().handlers[:] = [logging.NullHandler()]

from ai_engine.context_builder import ContextBuilder
from ai_engine.response_parser import ResponseParser
from ai_engine.schemas import ActionResponse, AgentContext, LLMResponse, PromptSections
from api.routes import enviar_comando_directo, procesar_evento
from api.schemas import AccionSaliente, EventoEntrante, RespuestaAPI
from core.orchestrator import Orchestrator


SYNTHETIC_PRIVATE = "AUDIT_PRIVATE_MARKER_NOT_REAL_REASONING"
SYNTHETIC_ERROR = "AUDIT_ERROR_MARKER_NOT_REAL_SECRET"


def _event(**updates) -> EventoEntrante:
    data = {
        "origen": "sandbox",
        "tipo_evento": "comando_usuario",
        "id_transaccion": " audit-contract-001 ",
        "detalles": {"mensaje": "hola", "solicita_decision": True},
    }
    data.update(updates)
    return EventoEntrante(**data)


def _action(**updates) -> ActionResponse:
    data = {
        "pensamiento": "resumen",
        "accion": "hablar",
        "parametros": {"texto_a_comunicar": "hola"},
        "razon": "respuesta social",
    }
    data.update(updates)
    return ActionResponse(**data)


class FakeRegistry:
    @staticmethod
    def get_tool(name):
        if name == "confirmable":
            return {"requires_confirmation": True}
        if name in {"hablar", "none"}:
            return {"requires_confirmation": False}
        return None


def schema_inventory():
    expected = {
        EventoEntrante: {"origen", "tipo_evento", "id_transaccion", "timestamp", "objeto_involucrado", "detalles"},
        AccionSaliente: {"id_accion", "nombre", "parametros", "razonamiento", "pensamiento_privado",
                         "requiere_confirmacion", "parametros_descripcion"},
        RespuestaAPI: {"status", "mensaje", "id_transaccion", "accion", "timestamp_servidor"},
        AgentContext: {"estado_narrativo", "valores_estado", "personalidad", "tarea", "memoria_texto",
                       "percepcion_texto", "constelacion_resumen", "herramientas", "herramientas_detalle",
                       "politica", "banderas", "timestamp", "proveedor"},
        PromptSections: {"system", "personalidad", "estado_emocional", "entorno", "objetivo", "memoria",
                         "herramientas", "restricciones", "formato_respuestas"},
        LLMResponse: {"pensamiento", "accion", "parametros", "razon"},
        ActionResponse: {"pensamiento", "accion", "parametros", "razon", "valido", "error", "metadata"},
    }
    for model, fields in expected.items():
        assert set(model.model_fields) == fields
    assert LLMResponse.model_config.get("extra") == "forbid"
    for model in (EventoEntrante, AccionSaliente, RespuestaAPI, AgentContext, ActionResponse):
        assert model.model_config.get("extra") in (None, "ignore")
    return {
        "models": len(expected),
        "fields": sum(len(fields) for fields in expected.values()),
        "llm_extra_policy": "forbid",
        "api_and_internal_extra_policy": "ignore",
    }


def evento_contract():
    event = _event(campo_futuro="ignored")
    assert event.id_transaccion == "audit-contract-001"
    assert not hasattr(event, "campo_futuro")
    assert event.timestamp is not None and event.timestamp.tzinfo is None

    explicit_null = _event(id_transaccion="null-time", timestamp=None)
    assert explicit_null.timestamp is None
    aware = _event(id_transaccion="aware-time", timestamp="2026-09-02T12:00:00Z")
    assert aware.timestamp and aware.timestamp.tzinfo is not None

    whitespace_type = _event(id_transaccion="whitespace-type", tipo_evento="   ")
    assert whitespace_type.tipo_evento == "   "
    defaults_a = EventoEntrante(origen="sandbox", tipo_evento="x", id_transaccion="a")
    defaults_b = EventoEntrante(origen="sandbox", tipo_evento="x", id_transaccion="b")
    defaults_a.detalles["mutated"] = True
    assert defaults_b.detalles == {}

    any_payload = _event(
        id_transaccion="any-payload",
        detalles={"exito": "false", "numero": "0", "vacio": "", "none_text": "none", "null": None},
    )
    assert any_payload.detalles["exito"] == "false"
    assert any_payload.detalles["numero"] == "0"

    rejected = []
    for label, kwargs in (
        ("blank_transaction", {"id_transaccion": "  "}),
        ("empty_event_type", {"tipo_evento": ""}),
        ("unknown_origin", {"origen": "web"}),
        ("null_details", {"detalles": None}),
    ):
        try:
            _event(**kwargs)
        except ValidationError:
            rejected.append(label)
    assert len(rejected) == 4
    return {
        "id_trimmed": True,
        "unknown_top_level_ignored": True,
        "missing_timestamp_default_is_naive_utc": True,
        "explicit_null_timestamp_preserved": True,
        "timezone_aware_input_preserved": True,
        "whitespace_event_type_accepted": True,
        "details_any_preserves_string_false": True,
        "mutable_defaults_isolated": True,
        "rejected_cases": rejected,
    }


def pydantic_coercion_and_empty():
    outgoing_false = AccionSaliente(
        id_accion="a", nombre="none", requiere_confirmacion="false"
    )
    outgoing_true = AccionSaliente(
        id_accion="b", nombre="", requiere_confirmacion="yes", campo_futuro="ignored"
    )
    internal_false = _action(valido="false")
    assert outgoing_false.requiere_confirmacion is False
    assert outgoing_true.requiere_confirmacion is True and outgoing_true.nombre == ""
    assert not hasattr(outgoing_true, "campo_futuro")
    assert internal_false.valido is False

    direct = _action(extra_internal="ignored")
    assert not hasattr(direct, "extra_internal")
    for field, value in (("parametros", None), ("metadata", None)):
        try:
            _action(**{field: value})
        except ValidationError:
            pass
        else:
            raise AssertionError(field + " accepted None")

    first = _action()
    second = _action()
    first.metadata["x"] = 1
    first.parametros["x"] = 1
    assert second.metadata == {} and "x" not in second.parametros
    return {
        "pydantic_string_false_to_bool_false": True,
        "pydantic_string_yes_to_bool_true": True,
        "details_any_not_coerced": True,
        "empty_action_name_accepted": True,
        "unknown_fields_ignored": True,
        "none_dicts_rejected": ["ActionResponse.parametros", "ActionResponse.metadata"],
        "mutable_defaults_isolated": True,
    }


def parser_sentinel_matrix():
    cases = {
        "missing": ('{"pensamiento":"p"}', "none", True),
        "empty": ('{"pensamiento":"p","accion":""}', "none", True),
        "whitespace": ('{"pensamiento":"p","accion":"   "}', "none", True),
        "none_lower": ('{"pensamiento":"p","accion":"none"}', "none", True),
        "none_upper": ('{"pensamiento":"p","accion":"NONE"}', "none", True),
        "null_text": ('{"pensamiento":"p","accion":" null "}', "none", True),
        "false_text": ('{"pensamiento":"p","accion":"false"}', "false", True),
        "null_json": ('{"pensamiento":"p","accion":null}', "none", False),
        "false_json": ('{"pensamiento":"p","accion":false}', "none", False),
    }
    observed = {}
    for name, (raw, expected_action, expected_valid) in cases.items():
        result = ResponseParser.parse(raw)
        assert result.accion == expected_action and result.valido is expected_valid
        observed[name] = {"action": result.accion, "valid": result.valido}

    no_json = ResponseParser.parse("")
    assert no_json.accion == "none" and not no_json.valido
    think = ResponseParser.parse(
        "<think>synthetic hidden block</think>"
        "{\"pensamiento\":\"visible summary\",\"accion\":\"none\"}"
    )
    assert think.pensamiento == "visible summary"
    extra = ResponseParser.parse(
        '{"pensamiento":"p","accion":"none","unknown":1}'
    )
    assert not extra.valido and extra.accion == "none"

    malformed_typed_raises = False
    try:
        ResponseParser.parse('{"pensamiento":{},"accion":"none"}')
    except ValidationError:
        malformed_typed_raises = True
    assert malformed_typed_raises
    return {
        "matrix": observed,
        "empty_raw_is_invalid_none": True,
        "think_block_removed": True,
        "unknown_llm_field_makes_invalid_response": True,
        "malformed_typed_json_can_escape_parser": True,
    }


def action_response_to_public():
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = FakeRegistry
    internal = _action(
        pensamiento=SYNTHETIC_PRIVATE,
        valido=False,
        error=SYNTHETIC_ERROR,
        metadata={"internal": {"sensitive": True}, "subjective_effort": {"total": 1}},
    )
    outgoing = orchestrator._a_accion_saliente(internal, "exploradora")
    public = outgoing.model_dump(mode="json")
    serialized = json.dumps(public)
    assert outgoing.pensamiento_privado == SYNTHETIC_PRIVATE
    assert outgoing.razonamiento == internal.razon
    assert public["parametros"] == internal.parametros
    assert not ({"valido", "error", "metadata"} & set(public))
    assert SYNTHETIC_ERROR not in serialized and "sensitive" not in serialized

    nested = {"nested": {"value": 1}}
    source = _action(parametros=nested)
    converted = orchestrator._a_accion_saliente(source, "exploradora")
    nested_shared = converted.parametros["nested"] is source.parametros["nested"]

    normal_none = _action(
        pensamiento="same", accion="none", parametros={}, razon="same",
        valido=True, error=None, metadata={},
    )
    failed_none = normal_none.model_copy(update={
        "valido": False, "error": SYNTHETIC_ERROR, "metadata": {"failure": True}
    })
    normal_public = orchestrator._a_accion_saliente(normal_none, "exploradora").model_dump()
    failed_public = orchestrator._a_accion_saliente(failed_none, "exploradora").model_dump()
    normal_public["id_accion"] = "normalized"
    failed_public["id_accion"] = "normalized"
    assert normal_public == failed_public
    return {
        "thought_renamed_to_public_private_field": True,
        "reason_renamed": True,
        "new_action_id_generated": True,
        "lost_fields": ["valido", "error", "metadata"],
        "private_marker_serialized": SYNTHETIC_PRIVATE in serialized,
        "error_marker_serialized": SYNTHETIC_ERROR in serialized,
        "valid_and_failed_none_become_identical_after_id_normalization": True,
        "nested_parameter_object_shared_after_conversion": nested_shared,
    }


def persona_dict_to_action_response():
    parsed_fallback = {
        "accion": "none",
        "parametros": {"texto_a_comunicar": "respuesta segura"},
        "pensamiento_privado": SYNTHETIC_PRIVATE,
        "razon": "fallback social",
        "error": SYNTHETIC_ERROR,
        "requiere_reintento": True,
        "recuperado_de_json_parcial": True,
        "campo_futuro": "ignored",
    }
    adapted = Orchestrator._action_response_desde_persona(parsed_fallback)
    assert adapted.accion == "hablar" and adapted.valido is True
    assert adapted.pensamiento == SYNTHETIC_PRIVATE
    assert adapted.razon == "fallback social"
    assert adapted.error is None and adapted.metadata == {}
    assert not hasattr(adapted, "requiere_reintento")
    assert not hasattr(adapted, "recuperado_de_json_parcial")

    empty = Orchestrator._action_response_desde_persona({
        "parametros": {"texto_a_comunicar": "   "},
        "error": SYNTHETIC_ERROR,
    })
    assert empty.accion == "hablar" and empty.valido is True
    assert empty.error is None
    assert empty.parametros["texto_a_comunicar"]
    return {
        "input_action_forced_to_hablar": True,
        "fallback_marked_valid": True,
        "thought_renamed": True,
        "lost_fields": ["error", "requiere_reintento", "recuperado_de_json_parcial", "campo_futuro"],
        "blank_public_text_replaced_by_fixed_fallback": True,
    }


class FakeOrchestrator:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.events = []

    def procesar_evento(self, event):
        self.events.append(event)
        if self.error is not None:
            raise self.error
        return self.result


async def _route_contract_async():
    none_orch = FakeOrchestrator(result=None)
    no_action = await procesar_evento(_event(id_transaccion="route-none"), none_orch)
    assert isinstance(no_action, RespuestaAPI)
    assert no_action.status == "ok" and no_action.accion is None

    outgoing_none = AccionSaliente(id_accion="none-action", nombre="none")
    object_orch = FakeOrchestrator(result=outgoing_none)
    action_object = await procesar_evento(_event(id_transaccion="route-object"), object_orch)
    assert action_object.status == "ok" and action_object.accion.nombre == "none"

    error_orch = FakeOrchestrator(error=RuntimeError(SYNTHETIC_ERROR))
    error_response = await procesar_evento(_event(id_transaccion="route-error"), error_orch)
    error_body = json.loads(error_response.body)
    assert error_response.status_code == 500 and error_body["status"] == "error"
    assert SYNTHETIC_ERROR in error_body["mensaje"]

    command = _event(id_transaccion="command-rewrite", tipo_evento="custom_event")
    command_orch = FakeOrchestrator(result=outgoing_none)
    command_result = await enviar_comando_directo(command, command_orch)
    assert command.tipo_evento == "comando_usuario"
    assert command_orch.events[0] is command and command_result.status == "ok"
    return {
        "python_none": {"status": no_action.status, "action_shape": "null"},
        "action_named_none": {"status": action_object.status, "action_shape": "object"},
        "synthetic_exception_text_crosses_public_error_message": True,
        "command_route_mutates_event_type": True,
    }


def route_contract(runner):
    return runner.run(_route_contract_async())


def context_projection():
    raw_task = {
        "objetivo_id": "task-1", "intentos_totales": 2, "intentos_fallidos": 1,
        "frustracion_local": 0.25, "umbral_abandono": 70.0,
        "loop_detectado": False, "estancamiento_detectado": True,
        "timestamp_inicio": "not-forwarded", "custom": "not-forwarded",
    }
    raw = {
        "estado_narrativo": "estable", "valores": {"energia": 80},
        "personalidad": {"curiosidad": 0.5}, "umbrales": {"alerta": False},
        "percepcion_resumida": "mesa", "constelacion_resumen": "resumen",
        "tarea": raw_task, "campo_no_declarado": "not-forwarded",
    }
    context = ContextBuilder.build(raw, herramientas=None, herramientas_detalle=None)
    dumped = context.model_dump()
    assert context.tarea == {
        "objetivo": "task-1", "intentos": 2, "fallos": 1,
        "frustracion": 0.25, "umbral_abandono": 70.0,
        "loop_detectado": False, "estancamiento": True,
    }
    assert "campo_no_declarado" not in dumped
    assert not ({"timestamp_inicio", "custom"} & set(context.tarea))
    assert context.herramientas == [] and context.herramientas_detalle == {}

    empty_task = ContextBuilder.build({"tarea": {}})
    assert empty_task.tarea is None
    sections = PromptSections(system="system", memoria="", objetivo="   ")
    assert sections.ensamblar() == "system\n\n   "
    return {
        "task_input_fields": len(raw_task),
        "task_output_fields": len(context.tarea),
        "task_fields_dropped": ["timestamp_inicio", "custom"],
        "unknown_context_fields_dropped": True,
        "none_and_empty_tools_collapse_to_empty": True,
        "empty_task_collapses_to_none": True,
        "prompt_empty_string_omitted_but_whitespace_preserved": True,
    }


CHECKS = (
    schema_inventory,
    evento_contract,
    pydantic_coercion_and_empty,
    parser_sentinel_matrix,
    action_response_to_public,
    persona_dict_to_action_response,
    context_projection,
)


def main():
    report = {"scope": "API/AI contracts", "network_allowed": False, "checks": []}
    failures = 0
    import chromadb

    with asyncio.Runner() as runner:
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
            with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
                with patch.object(chromadb, "PersistentClient", side_effect=AssertionError("Chroma forbidden")):
                    for check in CHECKS:
                        try:
                            evidence = check()
                            report["checks"].append({"name": check.__name__, "status": "PASS", "evidence": evidence})
                        except Exception as error:
                            failures += 1
                            report["checks"].append({
                                "name": check.__name__, "status": "FAIL", "error_type": type(error).__name__
                            })
                    try:
                        evidence = route_contract(runner)
                        report["checks"].append({"name": "route_contract", "status": "PASS", "evidence": evidence})
                    except Exception as error:
                        failures += 1
                        report["checks"].append({
                            "name": "route_contract", "status": "FAIL", "error_type": type(error).__name__
                        })
    report["passed"] = len(report["checks"]) - failures
    report["failed"] = failures
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
