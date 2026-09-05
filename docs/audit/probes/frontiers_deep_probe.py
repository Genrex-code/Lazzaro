"""Offline evidence probe for Phase 2 / Block 9 (API/terminal/Unity frontiers).

PASS_REPRODUCED means that the current boundary behavior was characterized.  It
does not mean the behavior is safe or approved.  The probe uses an in-memory
FastAPI TestClient, httpx.MockTransport, synthetic DTOs and source inspection.
It never starts a server or Unity, opens Chroma, calls an LLM, or executes an
action.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys
from typing import Any
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
logging.getLogger().handlers = [logging.NullHandler()]

PROTECTED = [
    "api/__init__.py",
    "api/routes.py",
    "api/schemas.py",
    "main.py",
    "terminal_client/__init__.py",
    "terminal_client/__main__.py",
    "terminal_client/chat.py",
    "terminal_client/test_chat.py",
    "unity_client/ApiClient.cs",
    "unity_client/DTOs.cs",
    "unity_client/InteractableMetadata.cs",
    "unity_client/LazzaroAgent.cs",
    "unity_client/MovementController.cs",
    "unity_client/PerceptionSystem.cs",
    "unity_client/SomatoSensorySystem.cs",
    "core/orchestrator.py",
    "core/audit_trace.py",
    "core/audit_writer.py",
    "ai_engine/motor_schemas.py",
    "lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin",
    "lazzaro_data/644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin",
    "lazzaro_data/chroma.sqlite3",
]

CHECKS: list[dict[str, Any]] = []
UNEXPECTED_EXTERNAL: list[dict[str, Any]] = []
WRITE_ATTEMPTS: list[str] = []
EXPECTED_PLATFORM_PROBES: list[str] = []
EXPECTED_SYSTEM_PROBES: list[str] = []


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def protected_hashes() -> dict[str, str]:
    return {name: sha256(ROOT / name) for name in PROTECTED}


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def check(name: str, condition: bool, detail: Any = None) -> None:
    row: dict[str, Any] = {"name": name, "status": "PASS" if condition else "FAIL"}
    if detail is not None:
        row["detail"] = detail
    CHECKS.append(row)
    if not condition:
        raise AssertionError(f"{name}: {detail!r}")


def deny(*_args: Any, **_kwargs: Any) -> None:
    raise PermissionError("Block 9 external operation forbidden")


def audit_barrier(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
        frame = sys._getframe()
        stack: list[str] = []
        while frame is not None and len(stack) < 16:
            stack.append(f"{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}")
            frame = frame.f_back
        internal_event_loop = any(
            marker in stack
            for marker in (
                "windows_events.py:_make_self_pipe",
                "proactor_events.py:_make_self_pipe",
                "socket.py:socketpair",
            )
        )
        if internal_event_loop:
            EXPECTED_SYSTEM_PROBES.append(event)
            return
        if event == "socket.bind" and "connection.py:_has_ipv6" in stack:
            EXPECTED_SYSTEM_PROBES.append(event)
        else:
            UNEXPECTED_EXTERNAL.append({"event": event, "stack": stack})
        deny()
    if event == "subprocess.Popen":
        frame = sys._getframe()
        platform_probe = False
        while frame is not None:
            if frame.f_code.co_filename.endswith("platform.py") and frame.f_code.co_name == "_syscmd_ver":
                platform_probe = True
                break
            frame = frame.f_back
        if platform_probe:
            EXPECTED_PLATFORM_PROBES.append(event)
        else:
            UNEXPECTED_EXTERNAL.append({"event": event})
        deny()
    if event in {"os.system", "os.startfile", "sqlite3.connect"}:
        UNEXPECTED_EXTERNAL.append({"event": event})
        deny()
    if event == "open":
        path, mode, flags = args
        if os.path.normcase(str(path)) == os.path.normcase(os.devnull):
            return
        write_mode = isinstance(mode, str) and any(char in mode for char in "wax+")
        write_flags = isinstance(flags, int) and bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if write_mode or write_flags:
            WRITE_ATTEMPTS.append(str(path))
            deny()
    if event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir"}:
        WRITE_ATTEMPTS.append(event)
        deny()


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.action: Any = None
        self.error: Exception | None = None
        self.notification_error = False

    def procesar_evento(self, event: Any) -> Any:
        self.calls.append(event)
        if self.error is not None:
            raise self.error
        return self.action

    def obtener_notificaciones(self) -> list[Any]:
        if self.notification_error:
            raise RuntimeError("SYNTHETIC_NOTIFICATION_FAILURE")
        return []

    def obtener_estado_publico(self) -> dict[str, Any]:
        return {
            "dopamina": 50.0, "estres": 20.0, "energia": 80.0,
            "confianza": 60.0, "motivacion": 70.0, "foco": 65.0,
            "personalidad": {}, "tarea_actual": None,
        }

    def obtener_constelacion_publica(self) -> dict[str, Any]:
        return {"nodes": [], "edges": []}


class RecordingWriter:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def write(self, trace: Any) -> bool:
        self.rows.append(json.loads(trace.to_json()))
        return True


def base_event(transaction_id: str = "block9-api-001") -> dict[str, Any]:
    return {
        "origen": "unity",
        "tipo_evento": "comando_usuario",
        "id_transaccion": transaction_id,
        "objeto_involucrado": "Cube",
        "detalles": {"mensaje": "SYNTHETIC", "solicita_decision": True},
    }


def response_json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def method_body(source: str, method_name: str) -> str:
    match = re.search(rf"\b{re.escape(method_name)}\s*\([^)]*\)\s*\{{", source)
    if not match:
        raise AssertionError(f"method not found: {method_name}")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise AssertionError(f"unclosed method: {method_name}")


def unity_fields(source: str, class_name: str) -> list[tuple[str, str, str | None]]:
    match = re.search(
        rf"public\s+class\s+{re.escape(class_name)}\s*\{{(?P<body>.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise AssertionError(f"class not found: {class_name}")
    rows: list[tuple[str, str, str | None]] = []
    for type_name, name, default in re.findall(
        r"^\s*public\s+([A-Za-z0-9_<>, ]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*([^;]+))?;",
        match.group("body"),
        flags=re.MULTILINE,
    ):
        rows.append((name, type_name.strip(), default.strip() or None))
    return rows


def main() -> int:
    initial_hashes = protected_hashes()
    dto_source = read("unity_client/DTOs.cs")
    api_client_source = read("unity_client/ApiClient.cs")
    agent_source = read("unity_client/LazzaroAgent.cs")
    movement_source = read("unity_client/MovementController.cs")
    perception_source = read("unity_client/PerceptionSystem.cs")
    pain_source = read("unity_client/SomatoSensorySystem.cs")
    orchestrator_source = read("core/orchestrator.py")

    sys.addaudithook(audit_barrier)
    with ExitStack() as stack:
        from ai_engine.schemas import ActionResponse
        from api.routes import router
        from api.schemas import AccionSaliente, EventoEntrante, RespuestaAPI
        from core.orchestrator import Orchestrator
        from memory.chroma_client import ChromaClient
        from terminal_client.chat import TerminalChatClient, TerminalChatError
        from terminal_client.test_chat import _orquestador_aislado
        from tools.executor import ActionExecutor

        stack.enter_context(patch.object(ChromaClient, "__init__", deny))
        stack.enter_context(patch.object(ActionExecutor, "ejecutar", deny))

        fake = FakeOrchestrator()
        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator = fake
        client = TestClient(app, raise_server_exceptions=False)

        openapi = app.openapi()
        endpoint_methods = {
            path: sorted(method.upper() for method in operations)
            for path, operations in openapi["paths"].items()
        }
        expected_endpoints = {
            "/api/v1/evento": ["POST"],
            "/api/v1/estado": ["GET"],
            "/api/v1/notificaciones": ["GET"],
            "/api/v1/constelacion": ["GET"],
            "/api/v1/comando": ["POST"],
        }
        check("api_endpoint_inventory", endpoint_methods == expected_endpoints, endpoint_methods)
        request_content_types = list(
            openapi["paths"]["/api/v1/evento"]["post"]["requestBody"]["content"]
        )
        check("api_declares_application_json", request_content_types == ["application/json"])

        fake.action = None
        normal = client.post("/api/v1/evento", json=base_event())
        check("valid_event_returns_200_ok_null_action", normal.status_code == 200 and normal.json()["status"] == "ok" and normal.json()["accion"] is None)
        accepted = fake.calls[-1]
        check("timestamp_default_is_naive_utc_datetime", accepted.timestamp is not None and accepted.timestamp.utcoffset() is None)

        validation_cases = {
            "empty_body": client.post("/api/v1/evento", content=b"", headers={"content-type": "application/json"}),
            "invalid_json": client.post("/api/v1/evento", content=b"{", headers={"content-type": "application/json"}),
            "unknown_origin": client.post("/api/v1/evento", json={**base_event("bad-origin"), "origen": "UNITY"}),
            "missing_transaction": client.post("/api/v1/evento", json={key: value for key, value in base_event("missing") .items() if key != "id_transaccion"}),
            "blank_transaction": client.post("/api/v1/evento", json={**base_event("blank"), "id_transaccion": "   "}),
            "invalid_timestamp": client.post("/api/v1/evento", json={**base_event("bad-time"), "timestamp": "not-a-time"}),
        }
        check("validation_failures_are_422", all(value.status_code == 422 for value in validation_cases.values()), {key: value.status_code for key, value in validation_cases.items()})

        unknown_type = client.post("/api/v1/evento", json={**base_event("unknown-type"), "tipo_evento": "future_event"})
        check("unknown_event_type_crosses_api", unknown_type.status_code == 200 and fake.calls[-1].tipo_evento == "future_event")
        long_id = "x" * 4096
        long_result = client.post("/api/v1/evento", json=base_event(long_id))
        check("long_transaction_id_is_accepted", long_result.status_code == 200 and len(fake.calls[-1].id_transaccion) == 4096)

        flexible = base_event("flexible-details")
        flexible["extra_top_level"] = "discarded"
        flexible["detalles"] = {
            "mensaje": None, "empty": "", "bool_string": "false",
            "number_string": "1.25", "unknown_nested": {"future": True},
        }
        flexible_result = client.post("/api/v1/evento", json=flexible)
        flexible_event = fake.calls[-1]
        check("flexible_details_preserve_nested_types", flexible_result.status_code == 200 and flexible_event.detalles == flexible["detalles"])
        check("unknown_top_level_is_ignored", not hasattr(flexible_event, "extra_top_level"))

        explicit_null_time = client.post("/api/v1/evento", json={**base_event("null-time"), "timestamp": None})
        check("explicit_null_timestamp_is_accepted", explicit_null_time.status_code == 200 and fake.calls[-1].timestamp is None)

        nan_payload = json.dumps(base_event("nan-input")).replace(
            '"detalles": {"mensaje": "SYNTHETIC", "solicita_decision": true}',
            '"detalles": {"x": NaN, "positive": Infinity, "negative": -Infinity}',
        )
        nan_input = client.post("/api/v1/evento", content=nan_payload, headers={"content-type": "application/json"})
        nan_event = fake.calls[-1]
        check("nonfinite_json_input_reaches_flexible_details", nan_input.status_code == 200 and nan_event.detalles["x"] != nan_event.detalles["x"])

        wrong_content_type = client.post("/api/v1/evento", content=json.dumps(base_event("text-body")), headers={"content-type": "text/plain"})
        check("text_plain_json_body_is_rejected", wrong_content_type.status_code == 422, wrong_content_type.status_code)

        action = AccionSaliente(
            id_accion="action-boundary-001",
            nombre="hablar",
            parametros={"texto_a_comunicar": "PUBLIC_SYNTHETIC"},
            razonamiento="REASON_SYNTHETIC",
            pensamiento_privado="PRIVATE_SYNTHETIC",
            requiere_confirmacion=False,
            parametros_descripcion=[{"nombre": "texto_a_comunicar", "tipo": "str"}],
        )
        fake.action = action
        action_response = client.post("/api/v1/evento", json=base_event("full-action"))
        action_wire = action_response.json()["accion"]
        check("api_action_wire_has_all_pydantic_fields", action_response.status_code == 200 and set(action_wire) == set(AccionSaliente.model_fields), sorted(action_wire))
        check("private_thought_and_reason_are_rest_public", action_wire["pensamiento_privado"] == "PRIVATE_SYNTHETIC" and action_wire["razonamiento"] == "REASON_SYNTHETIC")

        fake.action = AccionSaliente(
            id_accion="nonfinite-output", nombre="navegar_a_posicion",
            parametros={"x": float("nan"), "y": float("inf"), "z": float("-inf")},
        )
        nonfinite_output = client.post("/api/v1/evento", json=base_event("nonfinite-output"))
        nonfinite_output_body = response_json(nonfinite_output)
        check(
            "nonfinite_api_output_is_serialized_as_200_nulls",
            nonfinite_output.status_code == 200
            and isinstance(nonfinite_output_body, dict)
            and nonfinite_output_body["accion"]["parametros"] == {"x": None, "y": None, "z": None},
            {"status": nonfinite_output.status_code, "body": nonfinite_output_body},
        )

        fake.action = None
        fake.error = RuntimeError("SYNTHETIC_INTERNAL_SECRET_MARKER")
        functional_error = client.post("/api/v1/evento", json=base_event("functional-error"))
        check("functional_exception_returns_500_error_envelope", functional_error.status_code == 500 and functional_error.json()["status"] == "error")
        check("functional_exception_message_crosses_rest", "SYNTHETIC_INTERNAL_SECRET_MARKER" in functional_error.text)
        fake.error = None

        forced_command = {**base_event("forced-command"), "tipo_evento": "future_event", "origen": "otro"}
        command_result = client.post("/api/v1/comando", json=forced_command)
        check("command_endpoint_forces_comando_usuario", command_result.status_code == 200 and fake.calls[-1].tipo_evento == "comando_usuario" and fake.calls[-1].origen == "otro")

        fake.notification_error = True
        notification_failure = client.get("/api/v1/notificaciones")
        check("notification_failure_degrades_to_200_empty", notification_failure.status_code == 200 and notification_failure.json() == [])
        fake.notification_error = False

        # Full offline terminal roundtrip through MockTransport -> in-memory ASGI.
        captured_terminal_requests: list[dict[str, Any]] = []
        fake.action = action

        def asgi_bridge(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            captured_terminal_requests.append(payload)
            api_response = client.request(request.method, request.url.path, json=payload)
            return httpx.Response(api_response.status_code, content=api_response.content, headers={"content-type": api_response.headers.get("content-type", "application/json")})

        terminal_http = httpx.Client(base_url="http://synthetic.invalid", transport=httpx.MockTransport(asgi_bridge))
        terminal = TerminalChatClient(http_client=terminal_http)
        terminal_text = terminal.enviar_mensaje("  hola frontera  ", id_transaccion="terminal-fixed-id")
        terminal.close()
        terminal_payload = captured_terminal_requests[-1]
        check("terminal_social_roundtrip_displays_only_public_text", terminal_text == "PUBLIC_SYNTHETIC")
        check("terminal_builds_expected_event", terminal_payload == {
            "origen": "terminal", "tipo_evento": "comando_usuario",
            "id_transaccion": "terminal-fixed-id", "objeto_involucrado": "ninguno",
            "detalles": {"mensaje": "hola frontera", "solicita_decision": True, "modo_conversacion": True},
        })
        check("terminal_does_not_display_private_or_reason", all(marker not in terminal_text for marker in ("PRIVATE_SYNTHETIC", "REASON_SYNTHETIC")))

        def terminal_case(body: Any, status_code: int = 200) -> str:
            transport = httpx.MockTransport(lambda _request: httpx.Response(status_code, json=body))
            local_http = httpx.Client(base_url="http://synthetic.invalid", transport=transport)
            local = TerminalChatClient(http_client=local_http)
            try:
                local.enviar_mensaje("synthetic")
            except Exception as exc:  # evidence records the public exception only
                return f"{type(exc).__name__}:{exc}"
            finally:
                local.close()
            return "NO_ERROR"

        terminal_failures = {
            "motor": terminal_case({"status": "ok", "accion": {"nombre": "navegar_a_objeto", "parametros": {"nombre_objeto": "Cube"}}}),
            "none": terminal_case({"status": "ok", "accion": None}),
            "pending": terminal_case({"status": "pendiente", "mensaje": "SYNTHETIC_PENDING", "accion": None}),
            "malformed": terminal_case(["not", "an", "object"]),
            "server_error": terminal_case({"status": "error", "mensaje": "SYNTHETIC_INTERNAL_SECRET_MARKER"}, 500),
        }
        check("terminal_blocks_motor", "bloqueo una accion no conversacional" in terminal_failures["motor"])
        check("terminal_rejects_none_pending_and_malformed", all(terminal_failures[key].startswith("TerminalChatError:") for key in ("none", "pending", "malformed")))
        check("terminal_sanitizes_5xx_body", "SYNTHETIC_INTERNAL_SECRET_MARKER" not in terminal_failures["server_error"] and "error interno" in terminal_failures["server_error"])

        motor_triage = {"intent": "MOTOR_SIMPLE", "route": "motor", "confidence": 0.87, "reason": "SYNTHETIC"}
        terminal_event = EventoEntrante(origen="terminal", tipo_evento="comando_usuario", id_transaccion="terminal-adaptation", detalles={"mensaje": "ve al cubo"})
        adapted = Orchestrator._adaptar_triage_para_terminal(terminal_event, motor_triage)
        check("terminal_adapts_motor_to_persona_and_preserves_original_route", adapted["route"] == "persona" and adapted["terminal_original_route"] == "motor")

        # Same-ID cache behavior and AuditTrace identity using the existing isolated fixture.
        isolated, isolated_calls = _orquestador_aislado("persona", intent="SOCIAL")
        isolated.audit_writer = RecordingWriter()
        duplicate_event = EventoEntrante(origen="unity", tipo_evento="comando_usuario", id_transaccion="same-id-retry", detalles={"mensaje": "SYNTHETIC", "solicita_decision": True})
        first_action = isolated.procesar_evento(duplicate_event)
        second_action = isolated.procesar_evento(duplicate_event)
        trace_rows = isolated.audit_writer.rows
        traces = OrderedDict()
        for row in trace_rows:
            traces.setdefault(row["trace_id"], []).append(row["stage"])
        check("same_transaction_is_cached_after_completed_first_call", isolated_calls["persona"] == 1 and first_action.id_accion == second_action.id_accion)
        check("cache_hit_creates_new_trace_without_triage", len(traces) == 2 and list(traces.values())[1] == ["event_start", "event_end"], traces)

        # The permissive alias-result correlation demonstrates the inherited root.
        correlation_orchestrator = Orchestrator.__new__(Orchestrator)
        correlation_orchestrator._alias_candidates_pendientes = OrderedDict([
            ("action-old", {"id_accion": "action-old", "action": "navegar_a_objeto", "target_name": "Cube", "motor_resolution": {"target_id": "cube-1"}}),
            ("action-current", {"id_accion": "action-current", "action": "navegar_a_objeto", "target_name": "Cube", "motor_resolution": {"target_id": "cube-1"}}),
        ])
        exact = correlation_orchestrator._buscar_alias_pendiente_para_resultado({"id_accion": "action-old", "accion": "navegar_a_objeto", "objeto": "Cube"})
        duplicate = correlation_orchestrator._buscar_alias_pendiente_para_resultado({"id_accion": "action-old", "accion": "navegar_a_objeto", "objeto": "Cube"})
        check("exact_action_id_correlates_when_pending", exact is not None and exact["id_accion"] == "action-old")
        check("duplicate_old_outcome_can_fall_back_to_current_candidate", duplicate is not None and duplicate["id_accion"] == "action-current")

        # Python/Unity DTO and source contract.
        unity_dtos = {
            name: [{"name": field, "type": type_name, "default": default} for field, type_name, default in unity_fields(dto_source, name)]
            for name in ("EventoEntrante", "AccionSaliente", "RespuestaAPI")
        }
        python_dtos = {
            "EventoEntrante": list(EventoEntrante.model_fields),
            "AccionSaliente": list(AccionSaliente.model_fields),
            "RespuestaAPI": list(RespuestaAPI.model_fields),
        }
        check("unity_event_omits_python_timestamp", "timestamp" in python_dtos["EventoEntrante"] and "timestamp" not in {row["name"] for row in unity_dtos["EventoEntrante"]})
        check("unity_action_omits_parameter_descriptions", "parametros_descripcion" in python_dtos["AccionSaliente"] and "parametros_descripcion" not in {row["name"] for row in unity_dtos["AccionSaliente"]})
        check("unity_uses_newtonsoft_and_utf8_json", "JsonConvert.SerializeObject" in api_client_source and "JsonConvert.DeserializeObject<RespuestaAPI>" in api_client_source and "Encoding.UTF8.GetBytes" in api_client_source)
        check("unity_direct_snake_case_names_match_python", all("_" in row["name"] or row["name"] in {"origen", "detalles", "status", "mensaje", "accion", "nombre", "parametros", "razonamiento"} for rows in unity_dtos.values() for row in rows))

        dispatcher_actions = re.findall(r'case\s+"([^"]+)"\s*:', method_body(agent_source, "ProcesarRespuestaDelCerebro"))
        expected_dispatcher = [
            "navegar_a_objeto", "navegar_a_posicion", "alejarse_de_objeto",
            "girar_avatar", "cancelar_tarea", "interactuar_objeto_3d",
            "agarrar_objeto", "soltar_objeto", "aplicar_fuerza", "hablar",
        ]
        check("unity_dispatcher_allowlist", dispatcher_actions == expected_dispatcher, dispatcher_actions)
        check("unity_unknown_action_reports_failure", "Unity no tenia implementado un manejador" in method_body(agent_source, "ProcesarRespuestaDelCerebro"))
        check("unity_confirmation_true_omits_and_reports_failure", "requiere_confirmacion" in method_body(agent_source, "ProcesarRespuestaDelCerebro") and "requiere confirmacion manual" in method_body(agent_source, "ProcesarRespuestaDelCerebro"))
        check("unity_null_action_returns_without_task_completion", "respuesta == null || respuesta.accion == null" in method_body(agent_source, "ProcesarRespuestaDelCerebro") and "return;" in method_body(agent_source, "ProcesarRespuestaDelCerebro"))
        check("unity_accepts_any_non_error_status", 'respuesta.status == "error"' in api_client_source and "else\n                    {\n                        onSuccess?.Invoke(respuesta);" in api_client_source)
        check("unity_has_timeout_but_no_retry_or_backoff", "request.timeout = Mathf.Clamp" in api_client_source and "retry" not in api_client_source.lower() and "backoff" not in api_client_source.lower())
        check("unity_transport_failure_includes_full_body", "request.downloadHandler.text" in api_client_source and 'Fallo de conexion' in api_client_source)

        nav_position_body = method_body(agent_source, "EjecutarNavegarAPosicion")
        check("unity_navigation_requires_all_xyz_before_vector", all(f'TryLeerFloat(parametros, "{axis}"' in nav_position_body for axis in "xyz") and nav_position_body.index("new Vector3") > nav_position_body.index("return;"))
        check("unity_float_reader_has_no_finite_guard", "float.TryParse" in method_body(agent_source, "TryLeerFloat") and all(token not in method_body(agent_source, "TryLeerFloat") for token in ("IsNaN", "IsInfinity", "IsFinite")))
        check("unity_target_navigation_reaches_movement_controller", "movimiento.NavegarAObjeto(objeto)" in method_body(agent_source, "EjecutarNavegarAObjeto") and "navAgent.SetDestination" in movement_source)
        check("unity_position_navigation_reaches_movement_controller", "movimiento.NavegarAPosicion" in nav_position_body and "IniciarNavegacionA" in movement_source)
        check("unity_turn_reaches_movement_controller", "movimiento.GirarAvatar" in method_body(agent_source, "EjecutarGirarAvatar"))

        stub_methods = ["EjecutarInteraccion", "EjecutarAgarrar", "EjecutarSoltar", "EjecutarAplicarFuerza"]
        stub_evidence = {}
        for name in stub_methods:
            body = method_body(agent_source, name)
            stub_evidence[name] = {
                "reports_success": "ReportarResultadoAccion" in body and "true" in body,
                "calls_movement": "movimiento." in body,
                "calls_rigidbody": "AddForce" in body or "Rigidbody" in body,
            }
        check("unity_four_nonmovement_handlers_report_success_without_material_effect", all(row["reports_success"] and not row["calls_movement"] and not row["calls_rigidbody"] for row in stub_evidence.values()), stub_evidence)

        result_body = method_body(agent_source, "ReportarResultadoAccion")
        send_body = method_body(agent_source, "EnviarEvento")
        check("unity_result_contains_action_success_message_and_perception", all(token in result_body for token in ('detalles["accion"]', 'detalles["exito"]', 'detalles["mensaje_resultado"]', "percepcion.EscanearEntorno")))
        check("unity_result_omits_action_and_original_transaction_ids", "id_accion" not in result_body and "id_transaccion" not in result_body)
        check("unity_every_return_event_gets_new_transaction_id", "Guid.NewGuid().ToString()" in send_body)
        check("unity_result_completes_current_task_locally", "CompletarTareaUsuarioActual(exito, mensaje)" in result_body)

        perception_fields = re.findall(r'\{\s*"([^"]+)"\s*,', method_body(perception_source, "EscanearEntorno"))
        normalized_fields = re.findall(r'\{\s*"([^"]+)"\s*,', method_body(perception_source, "GenerarObjetosNormalizados"))
        check("unity_perception_has_snapshot_and_source_but_no_timestamp", "snapshot_id" in perception_fields and "source" in perception_fields and "timestamp" not in perception_fields)
        check("unity_perceived_objects_have_expected_metadata", set(("id", "display_name", "nombre", "runtime_instance_id", "aliases", "position", "distance", "distancia", "affordances", "source", "confidence")).issubset(normalized_fields))
        check("unity_snapshot_id_is_state_derived_not_generation_id", '"unity"' in method_body(perception_source, "CrearSnapshotId") and "Guid" not in method_body(perception_source, "CrearSnapshotId") and "Time." not in method_body(perception_source, "CrearSnapshotId"))
        check("unity_perception_sorts_by_distance_only", "distA.CompareTo(distB)" in method_body(perception_source, "ObtenerObjetosInteractuables"))

        pain_payload = method_body(pain_source, "CrearDetallesImpacto")
        report_pain = method_body(agent_source, "ReportarDolor")
        check("unity_pain_event_contract", 'EnviarEvento("alerta_dolor_fisico"' in report_pain and all(field in report_pain + pain_payload for field in ("estado_fisico", "direccion_escape", "distancia_escape", "velocidad_relativa", "dolor", "riesgo", "contactos")))
        check("unity_pain_has_no_capture_timestamp_or_body_part", "timestamp" not in report_pain + pain_payload and "body" not in (report_pain + pain_payload).lower())

        # Synthetic Unity -> Python payloads preserve flexible metadata at the API.
        fake.action = None
        unity_results = []
        for label, success in (("success", True), ("failure", False)):
            payload = {
                "origen": "unity", "tipo_evento": "resultado_accion",
                "id_transaccion": f"new-result-{label}", "objeto_involucrado": "Cube",
                "detalles": {
                    "accion": "navegar_a_objeto", "exito": success,
                    "mensaje_resultado": f"SYNTHETIC_{label.upper()}",
                    "snapshot_id": "unity_0.00_0.00_0.00_Cube_1.00",
                    "objetos_normalizados": [],
                },
            }
            response = client.post("/api/v1/evento", json=payload)
            observed = fake.calls[-1]
            unity_results.append({"case": label, "status_code": response.status_code, "id_transaccion": observed.id_transaccion, "details": observed.detalles})
        check("unity_success_and_failure_bool_survive_api_input", unity_results[0]["details"]["exito"] is True and unity_results[1]["details"]["exito"] is False)

        success_matrix = []
        success_inputs = [
            ("true", True, True), ("false", False, True),
            ("string_true", "true", True), ("string_false", "false", True),
            ("one", 1, True), ("zero", 0, True),
            ("null", None, True), ("missing", None, False),
        ]
        for label, value, present in success_inputs:
            details = {"accion": "navegar_a_objeto"}
            if present:
                details["exito"] = value
            model = EventoEntrante(
                origen="unity", tipo_evento="resultado_accion",
                id_transaccion=f"success-type-{label}", detalles=details,
            )
            raw = model.detalles.get("exito")
            success_matrix.append({
                "input": label,
                "present": "exito" in model.detalles,
                "python_type": type(raw).__name__ if "exito" in model.detalles else "absent",
                "python_value": raw,
                "task_context_bool_projection": bool(raw) if raw is not None else None,
                "identity_true": raw is True,
                "identity_false": raw is False,
            })
        check(
            "flexible_success_values_preserve_wire_types",
            [row["python_type"] for row in success_matrix]
            == ["bool", "bool", "str", "str", "int", "int", "NoneType", "absent"],
            success_matrix,
        )

        # Static AuditTrace/dispatch boundary assertions.
        check("orchestrator_maps_despachada_to_pending", '"despachada": "pending"' in orchestrator_source)
        check("unity_dispatch_is_recorded_before_physical_outcome", 'return (\n            "despachada"' in orchestrator_source)
        check("audit_trace_id_is_internal_not_public_dto", "trace_id" in orchestrator_source and "trace_id" not in python_dtos["EventoEntrante"] and "trace_id" not in python_dtos["RespuestaAPI"])

        final_hashes = protected_hashes()
        check("protected_files_unchanged_during_probe", final_hashes == initial_hashes)
        check("no_unexpected_external_operations", not UNEXPECTED_EXTERNAL, UNEXPECTED_EXTERNAL)
        check("no_write_attempts", not WRITE_ATTEMPTS, WRITE_ATTEMPTS)

        evidence = {
            "block": "FASE 2 / BLOQUE 9 / FRONTERAS API-TERMINAL-UNITY",
            "date": "2026-09-05",
            "semantics": "PASS_REPRODUCED documents current behavior; it is not a safety approval.",
            "command": "python -B docs/audit/probes/frontiers_deep_probe.py",
            "status": "PASS_REPRODUCED",
            "api": {
                "endpoints": endpoint_methods,
                "declared_request_content_types": request_content_types,
                "validation_statuses": {key: value.status_code for key, value in validation_cases.items()},
                "unknown_type_status": unknown_type.status_code,
                "long_id_length_accepted": len(long_id),
                "nan_input_status": nan_input.status_code,
                "wrong_content_type_status": wrong_content_type.status_code,
                "action_wire_fields": sorted(action_wire),
                "nonfinite_output": {"status_code": nonfinite_output.status_code, "body": nonfinite_output_body},
                "functional_error": {"status_code": functional_error.status_code, "body": response_json(functional_error)},
                "notification_failure": {"status_code": notification_failure.status_code, "body": notification_failure.json()},
            },
            "terminal": {
                "request": terminal_payload,
                "display": terminal_text,
                "failure_cases": terminal_failures,
                "motor_adaptation": {"original_route": motor_triage["route"], "effective_route": adapted["route"]},
            },
            "dto_matrix": {"python": python_dtos, "unity": unity_dtos},
            "unity": {
                "serializer": "Newtonsoft.Json / JsonConvert; UTF-8 UnityWebRequest",
                "dispatcher_actions": dispatcher_actions,
                "stub_success_without_material_effect": stub_evidence,
                "result_fields": ["perception snapshot", "accion", "exito", "mensaje_resultado", "optional extra"],
                "result_action_id": False,
                "result_original_transaction_id": False,
                "result_new_transaction_id": True,
                "perception_fields": sorted(set(perception_fields)),
                "normalized_object_fields": sorted(set(normalized_fields)),
                "pain_event": "alerta_dolor_fisico",
            },
            "correlation": {
                "completed_same_id_cached": True,
                "cache_hit_new_trace": True,
                "cache_trace_stages": list(traces.values())[1],
                "duplicate_old_result_fell_back_to": duplicate["id_accion"] if duplicate else None,
            },
            "unity_to_python": unity_results,
            "success_type_matrix": success_matrix,
            "isolation": {
                "network": False,
                "server_process": False,
                "unity_or_play_mode": False,
                "real_llm": False,
                "active_chroma_opened": False,
                "action_executor_called": False,
                "physical_execution": False,
                "synthetic_fixtures_only": True,
                "unexpected_external_operations": UNEXPECTED_EXTERNAL,
                "expected_platform_subprocess_probes_blocked": len(EXPECTED_PLATFORM_PROBES),
                "expected_ipv6_capability_binds_blocked": len(EXPECTED_SYSTEM_PROBES),
                "write_attempts": WRITE_ATTEMPTS,
                "protected_files_unchanged_during_probe": final_hashes == initial_hashes,
            },
            "protected_hashes_sha256": final_hashes,
            "checks": CHECKS,
        }
        print(json.dumps(evidence, ensure_ascii=True, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
