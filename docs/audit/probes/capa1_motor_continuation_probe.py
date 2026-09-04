"""Authorized Bloque 6 continuation: numeric boundary gate, entirely in RAM.

Does not repeat the danger/legacy checkpoint. Stops at the first additional
non-finite value reaching dispatch classification, as required by the resumed
authorization. This is evidence, not a complete Motor test suite or safety PASS.
"""

from __future__ import annotations

from contextlib import ExitStack
import json
import math
import os
import socket
import sys
from typing import Any
from unittest.mock import patch

sys.dont_write_bytecode = True

from capa1_motor_deep_probe import BLOCKED, audit_barrier, deny, hashes


WRITE_ATTEMPTS: list[str] = []


def read_only_barrier(event: str, args: tuple[Any, ...]) -> None:
    audit_barrier(event, args)
    if event == "sqlite3.connect":
        BLOCKED.append(event)
        deny()
    if event == "open":
        _path, mode, flags = args
        write_mode = isinstance(mode, str) and any(char in mode for char in "wax+")
        write_flags = isinstance(flags, int) and bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if write_mode or write_flags:
            WRITE_ATTEMPTS.append(event)
            deny()
    if event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir"}:
        WRITE_ATTEMPTS.append(event)
        deny()


def numeric(value: Any) -> dict[str, Any]:
    return {
        "type": type(value).__name__, "repr": repr(value),
        "finite": math.isfinite(value) if isinstance(value, (int, float)) else None,
    }


def main() -> int:
    initial = hashes()
    sys.addaudithook(read_only_barrier)
    records: list[dict[str, Any]] = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", deny))
        stack.enter_context(patch.object(socket.socket, "connect_ex", deny))
        stack.enter_context(patch.object(socket, "create_connection", deny))

        from ai_engine.motor_engine import MotorEngine
        from ai_engine.motor_schemas import NavigateToPositionParams
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

        class RegistryMetadataOnly:
            @staticmethod
            def get_tool(name: str) -> dict[str, Any] | None:
                if name != "navegar_a_posicion":
                    return None
                # Confirmation=False matches the actual simulated registration.
                # No tool function exists in this fake registry.
                return {"schema": NavigateToPositionParams, "requires_confirmation": False}

        class ReasoningFake:
            def __init__(self, x: float) -> None:
                self.x = x
                self.calls = 0

            def process(self, **kwargs: Any) -> ActionResponse:
                self.calls += 1
                return ActionResponse(
                    pensamiento="synthetic numeric boundary test",
                    accion="navegar_a_posicion", parametros={"x": self.x, "y": 0.0, "z": 0.0},
                    valido=True,
                )

        # Finite control first; do not run Inf/other matrices after a new stop.
        for label, x in [("finite_control", 0.0), ("nan_boundary", float("nan"))]:
            schema = NavigateToPositionParams(x=x, y=0.0, z=0.0)
            orch = object.__new__(Orchestrator)
            orch.motor_engine = MotorEngine()
            orch.reasoning = ReasoningFake(x)
            orch.tool_registry = RegistryMetadataOnly()
            event = EventoEntrante(
                origen="unity", tipo_evento="comando_usuario",
                id_transaccion=f"block6-resume-{label}",
                detalles={"mensaje": "destino por coordenadas"},
            )
            diagnosis = orch.motor_engine.diagnosticar_comando_usuario(
                event.detalles["mensaje"], {}, herramientas_disponibles=["navegar_a_posicion"],
            )
            assert diagnosis["estado"] == "sin_intencion"
            tools_allowed = ToolPolicy().seleccionar(
                ["navegar_a_posicion"], origen=event.origen, route="motor",
            )
            process = orch.motor_engine.procesar_comando_motor
            captured_motor: list[dict[str, Any]] = []

            def observe_process(*args: Any, **kwargs: Any) -> dict[str, Any]:
                result = process(*args, **kwargs)
                captured_motor.append(result)
                return result

            with patch.object(orch.motor_engine, "procesar_comando_motor", observe_process):
                response = orch._resolver_ruta_motor(
                    evento=event, contexto_llm={}, memoria_texto="",
                    herramientas=tools_allowed, herramientas_detalle={},
                    politica="exploradora", tarea=None,
                    triage_decision={"intent": "MOTOR_SIMPLE", "route": "motor"},
                )
            assert orch.reasoning.calls == 1 and len(captured_motor) == 1
            response = orch._blindar_salida_terminal(event, response)
            response = orch._sanitizar_action_response(response)
            outgoing = orch._a_accion_saliente(response, "exploradora")
            dispatch_state, _detail = orch._gestionar_ejecucion(event, outgoing, response)
            # DTO only, mirroring the route's wrapper. No API/HTTP transport run.
            api_dto = RespuestaAPI(status="ok", id_transaccion=event.id_transaccion, accion=outgoing)
            serialized_dto = json.loads(api_dto.model_dump_json())

            assert response.accion == "navegar_a_posicion" and response.valido
            assert dispatch_state == "despachada" and not outgoing.requiere_confirmacion
            new_stop = not math.isfinite(outgoing.parametros["x"])
            if new_stop:
                assert serialized_dto["accion"]["parametros"]["x"] is None
            records.append({
                "case": label, "request": event.detalles["mensaje"],
                "diagnosis": diagnosis, "schema_x": numeric(schema.x),
                "motor_x": numeric(captured_motor[0]["parametros"]["x"]),
                "action_response_x": numeric(response.parametros["x"]),
                "outgoing_x": numeric(outgoing.parametros["x"]),
                "api_python_x": numeric(api_dto.model_dump()["accion"]["parametros"]["x"]),
                "dto_json_parameters": serialized_dto["accion"]["parametros"],
                "action": response.accion, "valid": response.valido,
                "error": response.error, "metadata": response.metadata,
                "requires_confirmation": outgoing.requiere_confirmacion,
                "dispatch_state": dispatch_state, "reasoning_calls": orch.reasoning.calls,
                "motor_process_calls": len(captured_motor),
                "stop_nonfinite_dispatch": new_stop,
            })
            if new_stop:
                break

    final = hashes()
    assert final == initial, "Protected files changed"
    assert not BLOCKED and not WRITE_ATTEMPTS, "Isolation attempt requires separate STOP"
    stopped = any(record["stop_nonfinite_dispatch"] for record in records)
    print(json.dumps({
        "probe": "capa1_motor_continuation_probe",
        "status": "PASS_REPRODUCED", "audit_gate": "STOP_NONFINITE_DISPATCH" if stopped else "CONTINUE",
        "completed_block": False, "records": records,
        "protected_hashes": final, "protected_files_unchanged": final == initial,
        "blocked_operations": BLOCKED, "write_attempts": WRITE_ATTEMPTS,
        "network_used": False, "physical_execution": False,
        "live_clients_constructed": False, "real_aliases_persisted": False,
        "limitations": [
            "Input from controlled Reasoning fake, not an actual LLM sample.",
            "Motor revalidation ran exactly once; this is not evidence of a Reasoning bypass.",
            "Dispatch means in-memory classification only; nothing was sent.",
            "DTO model_dump_json was tested, not the FastAPI HTTP serialization path.",
            "Serialized DTO x is null, not a NaN token; no Unity physics effect is asserted.",
            "Remaining parameter cases, full audit and regression stop at this gate.",
        ],
    }, ensure_ascii=True, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
