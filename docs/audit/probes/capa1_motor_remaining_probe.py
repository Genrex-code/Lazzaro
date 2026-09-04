"""Second authorized Bloque 6 continuation, after the known NaN case.

Known numeric variants do not stop this runner. A demonstrated wrong-target
dispatch does stop it. Only in-memory boundaries run; no transport/executor.
"""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import fields
import json
import math
import socket
import sys
from typing import Any
from unittest.mock import patch

sys.dont_write_bytecode = True

from capa1_motor_continuation_probe import WRITE_ATTEMPTS, read_only_barrier
from capa1_motor_deep_probe import BLOCKED, deny, hashes


def safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "+Inf" if value > 0 else "-Inf"
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def main() -> int:
    initial = hashes()
    sys.addaudithook(read_only_barrier)
    report: dict[str, Any] = {}
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", deny))
        stack.enter_context(patch.object(socket.socket, "connect_ex", deny))
        stack.enter_context(patch.object(socket, "create_connection", deny))
        from pydantic import ValidationError
        from ai_engine.motor_engine import MotorEngine
        from ai_engine.motor_schemas import ACTION_SCHEMA_MAP
        from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot, normalize_perception
        from ai_engine.schemas import ActionResponse
        from api.schemas import EventoEntrante, RespuestaAPI
        from core.orchestrator import Orchestrator
        from core.tool_policy import ToolPolicy
        from memory.chroma_client import ChromaClient
        from tools.executor import ActionExecutor
        import chromadb

        stack.enter_context(patch.object(ChromaClient, "__init__", deny))
        stack.enter_context(patch.object(chromadb, "PersistentClient", deny))
        stack.enter_context(patch.object(chromadb, "Client", deny))
        stack.enter_context(patch.object(ActionExecutor, "ejecutar", deny))

        class RegistryFake:
            @staticmethod
            def get_tool(name: str) -> dict[str, Any] | None:
                if name not in {*ACTION_SCHEMA_MAP, "hablar"}:
                    return None
                return {"requires_confirmation": False}

        class ReasoningFake:
            def __init__(self, response: ActionResponse | None = None, raises: bool = False):
                self.calls = 0
                self.response = response
                self.raises = raises

            def process(self, **kwargs: Any) -> ActionResponse:
                self.calls += 1
                if self.raises:
                    raise RuntimeError("synthetic reasoning error")
                return self.response or ActionResponse(pensamiento="", accion="none")

        def minimal(motor: MotorEngine | None = None, reasoning: ReasoningFake | None = None):
            orch = object.__new__(Orchestrator)
            orch.motor_engine = motor or MotorEngine()
            orch.reasoning = reasoning or ReasoningFake()
            orch.tool_registry = RegistryFake()
            return orch

        def event(query: str = "synthetic", perception: dict[str, Any] | None = None):
            return EventoEntrante(
                origen="unity", tipo_evento="comando_usuario", id_transaccion="block6-remaining",
                detalles={"mensaje": query, **(perception or {})},
            )

        def finish(orch: Orchestrator, response: ActionResponse, evt: EventoEntrante):
            response = orch._blindar_salida_terminal(evt, response)
            response = orch._sanitizar_action_response(response)
            outgoing = orch._a_accion_saliente(response, "exploradora")
            state, _detail = orch._gestionar_ejecucion(evt, outgoing, response)
            dto = RespuestaAPI(status="ok", accion=outgoing)
            return {
                "action": outgoing.nombre, "valid": response.valido,
                "parameters": safe(outgoing.parametros),
                "dto_json_parameters": json.loads(dto.model_dump_json())["accion"]["parametros"],
                "metadata": safe(response.metadata), "error": response.error,
                "requires_confirmation": outgoing.requiere_confirmacion, "dispatch": state,
            }

        def route(orch: Orchestrator, query: str, perception: dict[str, Any]):
            evt = event(query, perception)
            process_calls = []
            process = orch.motor_engine.procesar_comando_motor

            def counted(*args: Any, **kwargs: Any):
                result = process(*args, **kwargs)
                process_calls.append(safe(result))
                return result

            with patch.object(orch.motor_engine, "procesar_comando_motor", counted):
                response = orch._resolver_ruta_motor(
                    evento=evt, contexto_llm={}, memoria_texto="",
                    herramientas=ToolPolicy().seleccionar(
                        [*ACTION_SCHEMA_MAP, "hablar"], origen="unity", route="motor",
                    ), herramientas_detalle={}, politica="exploradora", tarea=None,
                    triage_decision={"intent": "MOTOR_SIMPLE", "route": "motor"},
                )
            result = finish(orch, response, evt)
            result["reasoning_calls"] = orch.reasoning.calls
            result["motor_process_calls"] = len(process_calls)
            return result

        full_affordance_snapshot = {"objetos_normalizados": [{
            "id": "Cube", "display_name": "Cube", "aliases": ["cubo"],
            "affordances": ["mirar", "tocar", "activable", "agarrable", "empujable"],
        }]}

        # Every field of every current Motor schema, not only x. No x=NaN replay.
        bases = {
            "navegar_a_objeto": {"nombre_objeto": "Cube", "distancia": 1.0},
            "navegar_a_posicion": {"x": 1.0, "y": 1.0, "z": 1.0},
            "interactuar_objeto_3d": {"nombre_objeto": "Cube", "tipo_accion": "mirar"},
            "agarrar_objeto": {"nombre_objeto": "Cube"},
            "soltar_objeto": {"fuerza_lanzamiento": 1.0},
            "alejarse_de_objeto": {"nombre_objeto": "Cube", "distancia": 1.0},
            "girar_avatar": {"grados": 1.0},
            "aplicar_fuerza": {"nombre_objeto": "Cube", "vector_x": 1.0, "vector_y": 1.0, "vector_z": 1.0, "fuerza": 1.0},
            "cancelar_tarea": {"motivo": "synthetic", "cancelar_movimiento": True,
                               "cancelar_cola": True, "texto_confirmacion": "synthetic"},
        }
        values = [("+Inf", float("inf")), ("-Inf", -float("inf")), ("-1", -1),
                  ("1", 1), ("1e9", 1e9), ("True", True), ("False", False),
                  ("None", None), ('"1"', "1"), ("wrong_dict", {})]
        numeric_records = []
        for action, base in bases.items():
            for field_name in base:
                for label, value in [*values, ("missing", None)]:
                    params = dict(base)
                    if label == "missing":
                        params.pop(field_name)
                    else:
                        params[field_name] = value
                    try:
                        validated = ACTION_SCHEMA_MAP[action](**params)
                        field_value = getattr(validated, field_name)
                        schema_status = "accepted" if label != "missing" and type(field_value) is type(value) else "coerced_or_default"
                        schema_value = safe(field_value)
                    except ValidationError:
                        schema_status, schema_value = "rejected", None
                    orch = minimal()
                    motor_result = orch.motor_engine.procesar_comando_motor(action, params, full_affordance_snapshot)
                    response = orch._action_response_desde_motor(motor_result, "")
                    result = finish(orch, response, event())
                    outgoing_value = result["parameters"].get(field_name, "<absent>")
                    json_value = result["dto_json_parameters"].get(field_name, "<absent>")
                    numeric_records.append({
                        "schema": action, "field": field_name, "input": label,
                        "schema_status": schema_status, "schema_value": schema_value,
                        "action": result["action"], "valid": result["valid"],
                        "value": outgoing_value, "json_value": json_value,
                        "changed_on_serialization": outgoing_value != json_value,
                        "dispatch": result["dispatch"],
                    })
            for label, params in [("all_missing", {}), ("extra_field", {**base, "unexpected": 1})]:
                result = minimal().motor_engine.procesar_comando_motor(action, params, full_affordance_snapshot)
                numeric_records.append({"schema": action, "input": label, "action": result["accion"]})
        report["parameters"] = numeric_records

        perception_cases = {
            "none": None, "empty": {}, "empty_snapshot": {"objects": []},
            "malformed_list": [1], "malformed_objects": {"objects": [None, 4, "bad", {}]},
            "no_id": {"objects": [{"display_name": "Cube"}]},
            "no_name": {"objects": [{"id": "ID_ONLY"}]},
            "object_id_only": {"objects": [{"object_id": "ID_ONLY"}]},
            "duplicate_ids": {"objects": [{"id": "same", "display_name": "red"}, {"id": "same", "display_name": "blue"}]},
            "duplicate_names": {"objects": [{"id": "A", "display_name": "Cube"}, {"id": "B", "display_name": "Cube"}]},
            "confidence_negative": {"objects": [{"id": "Cube", "confidence": -1}]},
            "confidence_high": {"objects": [{"id": "Cube", "confidence": 10}]},
            "confidence_nan": {"objects": [{"id": "Cube", "confidence": float("nan")}]},
            "position_partial": {"objects": [{"id": "Cube", "position": {"x": 1, "y": 2}}]},
            "zero_distance": {"objects": [{"id": "Cube", "distance": 0}]},
            "field_priority": {"objects": [], "objetos_normalizados": [{"id": "ignored"}]},
            "stale_timestamp": {"timestamp": "1900-01-01", "objects": [{"id": "Cube"}]},
        }
        perception_records = []
        for name, raw in perception_cases.items():
            snap = normalize_perception(raw)
            perception_records.append({"case": name, "objects": [obj.to_dict() for obj in snap.objects],
                                       "raw_is_input": snap.raw is raw})
        raw = {"objects": [{"id": "Cube", "aliases": ["old"], "position": {"x": 1, "y": 2, "z": 3}}]}
        snap = normalize_perception(raw)
        raw["objects"][0]["aliases"].append("later")
        raw["objects"][0]["position"]["x"] = 99
        perception_records.append({"case": "mutate_raw_after_normalize", "snapshot": snap.objects[0].to_dict(),
                                   "raw_aliases": snap.raw["objects"][0]["aliases"], "raw_same": snap.raw is raw})
        reused = normalize_perception(snap)
        snap.objects[0].aliases.append("shared")
        perception_records.append({"case": "dataclass_reuse", "same_snapshot": reused is snap,
                                   "same_object": reused.objects[0] is snap.objects[0],
                                   "observed_aliases": list(reused.objects[0].aliases)})
        perception_records.append({"case": "contract_fields", "snapshot_fields": [f.name for f in fields(PerceptionSnapshot)],
                                   "object_fields": [f.name for f in fields(PerceivedObject)]})
        report["perception"] = perception_records

        wall = {"objetos_normalizados": [{"id": "Wall", "display_name": "Wall", "aliases": ["pared"], "affordances": ["mirar"]}]}
        reasoning_records = []
        reasoning_cases = [
            ("A_valid_motor", "request inferido", "girar_avatar", {"grados": 90}, False),
            ("B_unknown_action", "request inferido", "motor_inventado", {}, False),
            ("C_nonmotor", "request inferido", "hablar", {"texto_a_comunicar": "synthetic"}, False),
            ("D_none", "request inferido", "none", {}, False),
            ("E_exception", "request inferido", "none", {}, True),
            ("F_invalid_params", "request inferido", "girar_avatar", {"grados": "invalid"}, False),
            ("G_blocked_diagnosis", "agarra la pared", "agarrar_objeto", {"nombre_objeto": "Wall"}, False),
            ("G_inferred_blocked_target", "request inferido", "agarrar_objeto", {"nombre_objeto": "Wall"}, False),
        ]
        for name, query, action, params, raises in reasoning_cases:
            reasoning = ReasoningFake(ActionResponse(pensamiento="", accion=action, parametros=params), raises=raises)
            result = route(minimal(reasoning=reasoning), query, wall)
            reasoning_records.append({"case": name, **result})
        report["reasoning"] = reasoning_records

        red = {"id": "Cube_rojo", "display_name": "Cube_rojo", "aliases": ["cubo", "cubo rojo"], "affordances": ["mirar"]}
        blue = {"id": "Cube_azul", "display_name": "Cube_azul", "aliases": ["cubo", "cubo azul"], "affordances": ["mirar"]}
        resolution_cases = [
            ("two_cubes", "ve al cubo", [red, blue], {}),
            ("two_cubes_red", "ve al cubo rojo", [red, blue], {}),
            ("duplicate_names", "ve al cubo", [dict(red, display_name="Cube"), dict(blue, display_name="Cube")], {}),
            ("exact_id", "ve a Cube_rojo", [red, blue], {}),
            ("snapshot_alias", "ve a la caja", [dict(red, aliases=["caja"])], {}),
            ("learned_alias", "ve a la caja", [red], {"Cube_rojo": ["caja"]}),
            ("ambiguous_alias", "ve a la caja", [red, blue], {"Cube_rojo": ["caja"], "Cube_azul": ["caja"]}),
            ("stale_alias", "ve a la caja", [blue], {"absent": ["caja"]}),
            ("typo", "ve al cuboo", [red, blue], {}),
            ("reversed_objects", "ve al cubo", [blue, red], {}),
            ("missing_descriptor_target", "ve al cubo rojo", [blue], {}),
        ]
        resolution_records = []
        stopped = False
        for name, query, objects, aliases in resolution_cases:
            snapshot = {"objetos_normalizados": deepcopy(objects)}
            motor = MotorEngine()
            motor.fuzzy_resolver.learned_alias_provider = lambda ids, data=aliases: data
            fuzzy = motor.fuzzy_resolver.resolve(query, snapshot, action="navegar_a_objeto")
            diagnosis = motor.diagnosticar_comando_usuario(query, snapshot)
            result = route(minimal(motor), query, snapshot)
            record = {"case": name, "query": query, "visible": objects,
                      "fuzzy": fuzzy.to_dict(), "diagnosis": diagnosis, "output": result}
            resolution_records.append(record)
            if (name == "missing_descriptor_target" and fuzzy.candidate_correction is not None
                    and "rojo" in fuzzy.candidates[0].unmatched_descriptors
                    and result["action"] == "navegar_a_objeto"
                    and result["parameters"].get("nombre_objeto") == "Cube_azul"
                    and result["dispatch"] == "despachada"):
                stopped = True
                break
        report["resolution"] = resolution_records

    final = hashes()
    assert final == initial, "Protected files modified"
    assert not BLOCKED and not WRITE_ATTEMPTS, "Isolation event requires a separate stop"
    report.update({
        "probe": "capa1_motor_remaining_probe", "status": "PASS_REPRODUCED",
        "audit_gate": "STOP_WRONG_TARGET" if stopped else "PARTIAL_CONTINUE",
        "full_block_complete": False, "protected_files_unchanged": final == initial,
        "protected_hashes": final, "blocked_operations": BLOCKED,
        "write_attempts": WRITE_ATTEMPTS, "transport_sent": False,
        "physical_execution": False,
        "limitations": ["No HTTP or client serialization path executed; DTO JSON only.",
                        "Reasoning and registry are controlled fakes, not live providers.",
                        "Remaining matrices/regression are not executed after a material stop."],
    })
    print(json.dumps(safe(report), ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
