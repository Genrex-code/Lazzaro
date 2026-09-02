"""Probe aislado de contratos Motor/herramientas para Fase 2, Bloque 2.

Confirma el comportamiento actual; no expresa el contrato deseado. No instancia
Chroma, no usa red y restaura el registro global de herramientas antes de salir.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import sys
from typing import Any
import uuid


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Evita que los imports de la aplicacion configuren/escriban el log diario.
root_logger = logging.getLogger()
if not root_logger.handlers:
    root_logger.addHandler(logging.NullHandler())

from pydantic import BaseModel, ConfigDict

from ai_engine.intent_types import IntentType, Route, normalize_intent, route_for_intent
from ai_engine.motor_engine import MotorEngine
from ai_engine.motor_resolution import CandidateCorrection, MotorResolution
from ai_engine.motor_schemas import (
    ApplyForceParams,
    CancelTaskParams,
    MotorAction,
    NavigateToPositionParams,
)
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot, normalize_perception
from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from tools.base import ToolResult
from tools.executor import ActionExecutor
from tools.registry import ToolRegistry


RESULTS: list[dict[str, Any]] = []


def check(name: str, condition: bool, detail: Any = None) -> None:
    RESULTS.append({"name": name, "pass": bool(condition), "detail": detail})


class ProbeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int = 1


def probe_intents_and_routes() -> None:
    matrix = {
        "None": normalize_intent(None).value,
        "False": normalize_intent(False).value,
        "0": normalize_intent(0).value,
        "empty": normalize_intent("").value,
        "none_string": normalize_intent("none").value,
        "NOOP": normalize_intent("NOOP").value,
        "INTERNAL": normalize_intent("INTERNAL").value,
        "UNKNOWN": normalize_intent("UNKNOWN").value,
        "MOTOR_alias": normalize_intent(" motor ").value,
    }
    expected = {
        "None": "UNKNOWN",
        "False": "UNKNOWN",
        "0": "UNKNOWN",
        "empty": "UNKNOWN",
        "none_string": "UNKNOWN",
        "NOOP": "NOOP",
        "INTERNAL": "INTERNAL",
        "UNKNOWN": "UNKNOWN",
        "MOTOR_alias": "MOTOR_SIMPLE",
    }
    check("intent_sentinel_matrix", matrix == expected, matrix)

    routes = {
        intent.value: route_for_intent(intent).value
        for intent in (IntentType.NOOP, IntentType.INTERNAL, IntentType.UNKNOWN)
    }
    check(
        "noop_internal_unknown_routes_are_distinct",
        routes == {"NOOP": "noop", "INTERNAL": "internal", "UNKNOWN": "persona"},
        routes,
    )
    cut = {
        route.value: Orchestrator._triage_debe_cortar_flujo({"route": route.value})
        for route in (Route.NOOP, Route.INTERNAL, Route.PERSONA)
    }
    check(
        "operational_route_not_intent_controls_cut",
        cut == {"noop": True, "internal": True, "persona": False},
        cut,
    )
    check(
        "contradictory_intent_route_is_not_normalized_by_consumer",
        Orchestrator._triage_debe_cortar_flujo({"intent": "NOOP", "route": "persona"}) is False,
        {"intent": "NOOP", "route": "persona"},
    )


def probe_motor_schemas_and_resolution() -> None:
    first = MotorAction(accion="none")
    second = MotorAction(accion="none")
    first.parametros["only_first"] = True
    first.metadata["only_first"] = True
    check(
        "motor_action_mutable_defaults_are_isolated",
        second.parametros == {} and second.metadata == {},
        second.model_dump(),
    )

    cross_field = {
        "none_with_params": MotorAction(accion="none", parametros={"x": 1}).model_dump(),
        "physical_without_params": MotorAction(accion="navegar_a_objeto", parametros={}).model_dump(),
    }
    check(
        "motor_action_does_not_enforce_action_parameter_pair",
        cross_field["none_with_params"]["parametros"] == {"x": 1}
        and cross_field["physical_without_params"]["parametros"] == {},
        cross_field,
    )

    coercions = CancelTaskParams(cancelar_movimiento="false", cancelar_cola="true")
    position_from_strings = NavigateToPositionParams(x="1.25", y=0, z="-2")
    position_from_bools = NavigateToPositionParams(x=True, y=False, z=0)
    check(
        "strict_params_forbid_extras_but_coerce_scalars",
        coercions.cancelar_movimiento is False
        and coercions.cancelar_cola is True
        and position_from_strings.x == 1.25
        and position_from_bools.x == 1.0
        and position_from_bools.y == 0.0,
        {
            "cancelar_movimiento": coercions.cancelar_movimiento,
            "cancelar_cola": coercions.cancelar_cola,
            "string_x": position_from_strings.x,
            "bool_x": position_from_bools.x,
            "bool_y": position_from_bools.y,
        },
    )

    nonfinite: dict[str, dict[str, Any]] = {}
    for label, raw in (("nan", "nan"), ("positive_inf", "inf"), ("negative_inf", "-inf")):
        params = NavigateToPositionParams(x=raw, y=0, z=0)
        action = MotorAction(accion="navegar_a_posicion", parametros=params.model_dump())
        nonfinite[label] = {
            "accepted_finite": math.isfinite(params.x),
            "python_value": repr(params.x),
            "json": json.loads(action.model_dump_json())["parametros"]["x"],
        }
    force = ApplyForceParams(
        nombre_objeto="Probe",
        vector_x="nan",
        vector_y="inf",
        vector_z=0,
        fuerza=1,
    )
    check(
        "nonfinite_motor_coordinates_are_accepted_then_json_null",
        all(not item["accepted_finite"] and item["json"] is None for item in nonfinite.values())
        and math.isnan(force.vector_x)
        and math.isinf(force.vector_y),
        nonfinite,
    )
    engine_action = MotorEngine().procesar_comando_motor(
        "navegar_a_posicion",
        {"x": "nan", "y": "inf", "z": 0},
        percepcion={},
    )
    engine_response = Orchestrator._action_response_desde_motor(engine_action, pensamiento="")
    engine_public = AccionSaliente(
        id_accion="nonfinite-engine",
        nombre=engine_response.accion,
        parametros=engine_response.parametros,
    )
    engine_json = json.loads(engine_public.model_dump_json())
    check(
        "motor_engine_marks_nonfinite_action_valid_before_public_json_null",
        engine_action["accion"] == "navegar_a_posicion"
        and engine_response.valido is True
        and engine_json["parametros"]["x"] is None
        and engine_json["parametros"]["y"] is None,
        {
            "engine_action": engine_action,
            "action_response_valid": engine_response.valido,
            "public_json": engine_json,
        },
    )

    statuses: dict[str, dict[str, Any]] = {}
    for status in ("ready", "requires_clarification", "not_found", "not_allowed", "unsafe"):
        resolution = MotorResolution(status=status, action="agarrar_objeto", audit={"probe": status})
        metadata = resolution.to_metadata()
        statuses[status] = {
            "ready_property": resolution.ready,
            "metadata_status": metadata["motor_resolution"]["status"],
            "audit_separated": "audit" not in metadata["motor_resolution"]
            and metadata["motor_audit"] == {"probe": status},
        }
    check(
        "motor_resolution_preserves_five_statuses_in_internal_metadata",
        all(value["metadata_status"] == key for key, value in statuses.items())
        and statuses["ready"]["ready_property"] is True
        and all(not value["ready_property"] for key, value in statuses.items() if key != "ready")
        and all(value["audit_separated"] for value in statuses.values()),
        statuses,
    )

    invalid_status = MotorResolution(status="uncontracted_status")  # type: ignore[arg-type]
    check(
        "resolution_literal_is_not_runtime_enforced",
        invalid_status.status == "uncontracted_status" and invalid_status.ready is False,
        invalid_status.to_dict(),
    )

    class UnknownStatusMotor:
        ACCIONES_MOTORAS: set[str] = set()

        @staticmethod
        def diagnosticar_comando_usuario(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "estado": "uncontracted_status",
                "accion": "agarrar_objeto",
                "parametros": None,
                "metadata": {"motor_resolution": {"status": "uncontracted_status"}},
            }

    class ObservableReasoning:
        def __init__(self) -> None:
            self.calls = 0

        def process(self, **kwargs: Any) -> ActionResponse:
            self.calls += 1
            return ActionResponse(pensamiento="", accion="hablar", parametros={}, razon="fallback")

    status_consumer = Orchestrator.__new__(Orchestrator)
    status_consumer.motor_engine = UnknownStatusMotor()
    status_consumer.reasoning = ObservableReasoning()
    response = status_consumer._resolver_ruta_motor(
        evento=EventoEntrante(
            origen="unity",
            tipo_evento="comando_usuario",
            id_transaccion="contracts-unknown-motor-status",
            detalles={"mensaje": "probe"},
        ),
        contexto_llm={},
        memoria_texto="",
        herramientas=[],
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
    )
    check(
        "unknown_motor_status_falls_through_to_reasoning",
        status_consumer.reasoning.calls == 1 and response.razon == "fallback",
        {"reasoning_calls": status_consumer.reasoning.calls, "response": response.model_dump()},
    )

    correction = CandidateCorrection(
        requested="probe",
        suggested_id="id",
        suggested_display_name="display",
        score=1,
        requires_confirmation="false",  # type: ignore[arg-type]
    )
    check(
        "candidate_correction_bool_uses_python_truthiness",
        correction.to_dict()["requires_confirmation"] is True,
        correction.to_dict(),
    )


def probe_perception_contract() -> None:
    empty_inputs = {
        "None": normalize_perception(None),
        "False": normalize_perception(False),  # type: ignore[arg-type]
        "zero": normalize_perception(0),  # type: ignore[arg-type]
        "empty_list": normalize_perception([]),  # type: ignore[arg-type]
        "empty_string": normalize_perception(""),
    }
    ids = {name: snapshot.snapshot_id for name, snapshot in empty_inputs.items()}
    check(
        "different_empty_sentinels_share_generated_snapshot_id",
        len(set(ids.values())) == 1,
        ids,
    )

    zero_distance = normalize_perception(
        {"objects": [{"id": "zero", "display_name": "Zero", "distance": 0}]}
    )
    fallback_distance = normalize_perception(
        {
            "objects": [
                {"id": "zero", "display_name": "Zero", "distance": 0, "distancia": 9}
            ]
        }
    )
    check(
        "zero_distance_is_treated_as_absent",
        zero_distance.objects[0].distance is None and fallback_distance.objects[0].distance == 9.0,
        {
            "zero_only": zero_distance.objects[0].distance,
            "zero_with_legacy_fallback": fallback_distance.objects[0].distance,
        },
    )

    same_semantics_a = {"source": "unity", "objects": [], "marker": 1}
    same_semantics_b = {"marker": 1, "objects": [], "source": "unity"}
    order_ids = (
        normalize_perception(same_semantics_a).snapshot_id,
        normalize_perception(same_semantics_b).snapshot_id,
    )
    check(
        "generated_snapshot_id_depends_on_mapping_insertion_order",
        order_ids[0] != order_ids[1],
        list(order_ids),
    )

    raw = {"objects": [{"id": "one", "display_name": "One"}], "custom": {"value": 1}}
    snapshot = normalize_perception(raw)
    raw["custom"]["value"] = 2
    check(
        "perception_raw_is_retained_by_reference",
        snapshot.raw is raw and snapshot.to_dict()["raw"]["custom"]["value"] == 2,
        snapshot.to_dict()["raw"],
    )

    already_canonical = PerceptionSnapshot(
        snapshot_id="manual",
        source="invalid-source",
        objects=[PerceivedObject(id="x", display_name="X", confidence=3.5)],
    )
    normalized = normalize_perception(already_canonical)
    check(
        "canonical_perception_instance_bypasses_normalization",
        normalized is already_canonical
        and normalized.source == "invalid-source"
        and normalized.objects[0].confidence == 3.5,
        normalized.to_dict(),
    )


def _make_event(origin: str = "sandbox") -> EventoEntrante:
    return EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion=f"contracts-probe-{origin}",
        detalles={"mensaje": "probe"},
    )


def probe_action_and_tool_flow() -> None:
    orchestrator = Orchestrator.__new__(Orchestrator)

    class ConversionRegistry:
        @staticmethod
        def get_tool(name: str) -> dict[str, Any] | None:
            if name == "probe_action":
                return {"requires_confirmation": True}
            return None

    orchestrator.tool_registry = ConversionRegistry
    internal = ActionResponse(
        pensamiento="private",
        accion="probe_action",
        parametros={"value": 1},
        razon="reason",
        valido=False,
        error="internal-error",
        metadata={"motor_resolution": {"status": "unsafe"}, "probe": True},
    )
    public = orchestrator._a_accion_saliente(internal, politica="exploradora")
    public_keys = set(public.model_dump())
    check(
        "action_response_to_public_action_drops_valid_error_metadata",
        public.nombre == internal.accion
        and public.pensamiento_privado == internal.pensamiento
        and public.razonamiento == internal.razon
        and public.requiere_confirmacion is True
        and {"valido", "error", "metadata"}.isdisjoint(public_keys),
        {"internal": internal.model_dump(), "public": public.model_dump()},
    )

    responses: dict[str, ActionResponse] = {}
    public_statuses: dict[str, dict[str, Any]] = {}
    for status in ("requires_clarification", "not_found", "not_allowed", "unsafe"):
        response = Orchestrator._respuesta_resolucion_motor(
            {
                "estado": status,
                "accion": "agarrar_objeto",
                "metadata": {"motor_resolution": {"status": status, "candidates": []}},
            }
        )
        responses[status] = response
        converted = orchestrator._a_accion_saliente(response, politica="exploradora")
        public_statuses[status] = converted.model_dump()
    check(
        "motor_statuses_are_distinct_in_action_response_metadata",
        all(
            response.metadata["motor_resolution"]["status"] == status
            for status, response in responses.items()
        ),
        {status: response.metadata for status, response in responses.items()},
    )
    check(
        "public_action_has_no_machine_readable_motor_status",
        all("metadata" not in payload and "motor_resolution" not in payload for payload in public_statuses.values())
        and public_statuses["requires_clarification"]["nombre"] == "hablar"
        and public_statuses["not_found"]["nombre"] == "hablar"
        and public_statuses["not_allowed"]["nombre"] == "hablar"
        and public_statuses["unsafe"]["nombre"] == "none",
        public_statuses,
    )

    passive = Orchestrator._accion_pasiva()
    blocked = Orchestrator._action_response_desde_motor(
        {
            "accion": "none",
            "parametros": {},
            "razon": "blocked",
            "metadata": {"motor_resolution": {"status": "unsafe"}},
        },
        pensamiento="",
    )
    passive_out = orchestrator._a_accion_saliente(passive, politica="exploradora")
    blocked_out = orchestrator._a_accion_saliente(blocked, politica="exploradora")
    check(
        "valid_passive_none_and_invalid_blocked_none_collapse_at_public_boundary",
        passive.valido is True
        and blocked.valido is False
        and blocked.error is not None
        and passive_out.nombre == blocked_out.nombre == "none"
        and "valido" not in passive_out.model_dump()
        and "error" not in blocked_out.model_dump(),
        {
            "internal_passive": passive.model_dump(),
            "internal_blocked": blocked.model_dump(),
            "public_passive": passive_out.model_dump(),
            "public_blocked": blocked_out.model_dump(),
        },
    )

    original_tools = dict(ToolRegistry._tools)
    try:
        ToolRegistry._tools.clear()
        calls = {"count": 0}

        def returns_string_false(params: ProbeParams) -> ToolResult:
            calls["count"] += 1
            return ToolResult(exito="false", mensaje="string false")  # type: ignore[arg-type]

        ToolRegistry._tools["string_false"] = {
            "func": returns_string_false,
            "description": "probe",
            "schema": ProbeParams,
            "requires_confirmation": False,
        }
        action = AccionSaliente(id_accion="tool-string-false", nombre="string_false", parametros={})
        direct = ActionExecutor.ejecutar(action)
        orchestrator_status, orchestrator_details = orchestrator._gestionar_ejecucion(
            _make_event("sandbox"),
            action,
        )
        check(
            "tool_result_string_false_is_forwarded_and_treated_as_success",
            direct.exito == "false"
            and isinstance(direct.exito, str)
            and orchestrator_status == "ok"
            and calls["count"] == 2,
            {
                "direct_exito": repr(direct.exito),
                "orchestrator_status": orchestrator_status,
                "details": orchestrator_details,
            },
        )

        non_result_matrix: dict[str, dict[str, Any]] = {}
        for name, returned in (("false", False), ("none", None), ("empty_dict", {}), ("empty_list", [])):
            ToolRegistry._tools["non_result"] = {
                "func": lambda params, value=returned: value,
                "description": "probe",
                "schema": ProbeParams,
                "requires_confirmation": False,
            }
            result = ActionExecutor.ejecutar(
                AccionSaliente(id_accion=f"non-result-{name}", nombre="non_result", parametros={})
            )
            non_result_matrix[name] = {"exito": result.exito, "mensaje": result.mensaje}
        check(
            "all_non_toolresult_returns_are_normalized_to_success",
            all(item["exito"] is True for item in non_result_matrix.values()),
            non_result_matrix,
        )

        confirmation_calls = {"count": 0}

        def confirmed_tool(params: ProbeParams) -> str:
            confirmation_calls["count"] += 1
            return "executed"

        ToolRegistry._tools["confirm_probe"] = {
            "func": confirmed_tool,
            "description": "probe",
            "schema": ProbeParams,
            "requires_confirmation": True,
        }
        confirmation_action = AccionSaliente(
            id_accion="confirm-probe",
            nombre="confirm_probe",
            parametros={},
            requiere_confirmacion=True,
        )
        pending_status, _ = orchestrator._gestionar_ejecucion(
            _make_event("sandbox"),
            confirmation_action,
        )
        calls_after_orchestrator = confirmation_calls["count"]
        executor_result = ActionExecutor.ejecutar(confirmation_action)
        calls_after_executor = confirmation_calls["count"]
        denied = ActionExecutor.ejecutar(
            confirmation_action.model_copy(update={"requiere_confirmacion": False})
        )
        check(
            "same_confirmation_flag_means_pending_to_orchestrator_but_authorized_to_executor",
            pending_status == "pendiente_confirmacion"
            and calls_after_orchestrator == 0
            and executor_result.exito is True
            and calls_after_executor == 1
            and denied.exito is False,
            {
                "orchestrator": pending_status,
                "calls_after_orchestrator": calls_after_orchestrator,
                "executor_with_true": executor_result.exito,
                "calls_after_executor": calls_after_executor,
                "executor_with_false": denied.exito,
            },
        )
    finally:
        ToolRegistry._tools.clear()
        ToolRegistry._tools.update(original_tools)

    tool_a = ToolResult()
    tool_b = ToolResult()
    tool_a.datos_extra["only_a"] = True
    check("tool_result_mutable_default_is_isolated", tool_b.datos_extra == {}, tool_b.datos_extra)


def probe_behavior_result_consumer() -> None:
    matrix: dict[str, dict[str, Any]] = {}
    values = (
        ("false_bool", False),
        ("false_string", "false"),
        ("zero", 0),
        ("empty_string", ""),
        ("true_bool", True),
        ("true_string", "true"),
        ("one", 1),
        ("none", None),
    )
    for label, value in values:
        incoming = EventoEntrante(
            origen="unity",
            tipo_evento="resultado_accion",
            id_transaccion=f"contracts-result-{label}",
            detalles={"accion": "navegar_a_objeto", "exito": value},
        )
        behavior = BehaviorCore()
        behavior.iniciar_tarea("contracts-probe")
        behavior.procesar_evento(incoming.tipo_evento, incoming.detalles)
        history = behavior.task_context.historial if behavior.task_context is not None else []
        matrix[label] = {
            "task_attempt_recorded": bool(history),
            "task_success": history[-1]["exito"] if history else None,
            "emotional_event": behavior._resolver_evento_emocional(
                "resultado_accion",
                {"accion": "navegar_a_objeto", "exito": value},
            ),
        }
    check(
        "behavior_core_uses_truthiness_for_task_but_identity_for_emotion",
        matrix["false_bool"] == {
            "task_attempt_recorded": True,
            "task_success": False,
            "emotional_event": "exploracion_fallida",
        }
        and matrix["false_string"]["task_success"] is True
        and matrix["false_string"]["emotional_event"] == "resultado_accion"
        and matrix["zero"]["task_success"] is False
        and matrix["zero"]["emotional_event"] == "resultado_accion"
        and matrix["true_string"]["task_success"] is True
        and matrix["true_string"]["emotional_event"] == "resultado_accion"
        and matrix["none"]["task_attempt_recorded"] is False,
        matrix,
    )


def main() -> int:
    original_uuid4 = uuid.uuid4
    uuid_counter = {"value": 0}

    def deterministic_uuid4() -> uuid.UUID:
        uuid_counter["value"] += 1
        return uuid.UUID(int=uuid_counter["value"])

    uuid.uuid4 = deterministic_uuid4
    try:
        probe_intents_and_routes()
        probe_motor_schemas_and_resolution()
        probe_perception_contract()
        probe_action_and_tool_flow()
        probe_behavior_result_consumer()
    finally:
        uuid.uuid4 = original_uuid4

    failures = [result for result in RESULTS if not result["pass"]]
    summary = {
        "probe": "contracts_motor_tools",
        "checks": len(RESULTS),
        "passed": len(RESULTS) - len(failures),
        "failed": len(failures),
        "network_used": False,
        "chroma_used": False,
        "results": RESULTS,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
