"""Read-only API/DTO contract probe for audit block 02.

The probe imports only ``api.schemas`` and parses API/Unity sources as text/AST.
It does not start FastAPI, compile Unity, open sockets, inspect credentials, or
touch runtime Chroma data.  PASS means the current behavior was reproduced; it
does not mean every reproduced behavior is desirable.
"""

from __future__ import annotations

import ast
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from api.schemas import AccionSaliente, EventoEntrante, RespuestaAPI


SCHEMA_PATH = ROOT / "api" / "schemas.py"
ROUTES_PATH = ROOT / "api" / "routes.py"
DTO_PATH = ROOT / "unity_client" / "DTOs.cs"
API_CLIENT_PATH = ROOT / "unity_client" / "ApiClient.cs"
AGENT_PATH = ROOT / "unity_client" / "LazzaroAgent.cs"
TERMINAL_PATH = ROOT / "terminal_client" / "chat.py"


EXPECTED_PYTHON_FIELDS = {
    "EventoEntrante": [
        "origen",
        "tipo_evento",
        "id_transaccion",
        "timestamp",
        "objeto_involucrado",
        "detalles",
    ],
    "AccionSaliente": [
        "id_accion",
        "nombre",
        "parametros",
        "razonamiento",
        "pensamiento_privado",
        "requiere_confirmacion",
        "parametros_descripcion",
    ],
    "RespuestaAPI": [
        "status",
        "mensaje",
        "id_transaccion",
        "accion",
        "timestamp_servidor",
    ],
}

EXPECTED_UNITY_FIELDS = {
    "EventoEntrante": [
        "origen",
        "tipo_evento",
        "id_transaccion",
        "objeto_involucrado",
        "detalles",
    ],
    "AccionSaliente": [
        "id_accion",
        "nombre",
        "pensamiento_privado",
        "razonamiento",
        "parametros",
        "requiere_confirmacion",
    ],
    "RespuestaAPI": [
        "status",
        "mensaje",
        "id_transaccion",
        "timestamp_servidor",
        "accion",
    ],
}


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def python_fields() -> dict[str, list[str]]:
    tree = ast.parse(read(SCHEMA_PATH))
    result: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in EXPECTED_PYTHON_FIELDS:
            continue
        result[node.name] = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
    return result


def unity_fields() -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    source = read(DTO_PATH)
    names: dict[str, list[str]] = {}
    types: dict[str, dict[str, str]] = {}
    for class_name in EXPECTED_UNITY_FIELDS:
        match = re.search(
            rf"public\s+class\s+{re.escape(class_name)}\s*\{{(?P<body>.*?)\n\}}",
            source,
            flags=re.DOTALL,
        )
        check(match is not None, f"Unity DTO class missing: {class_name}")
        fields = re.findall(
            r"^\s*public\s+([A-Za-z0-9_<>, ]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=[^;]+)?;",
            match.group("body"),
            flags=re.MULTILINE,
        )
        names[class_name] = [name for _, name in fields]
        types[class_name] = {name: type_name.strip() for type_name, name in fields}
    return names, types


def validation_rejected(factory, **kwargs: Any) -> bool:
    try:
        factory(**kwargs)
    except ValidationError:
        return True
    return False


def model_contract() -> tuple[dict[str, Any], dict[str, bool]]:
    event = EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion="  dto-known-001  ",
        objeto_involucrado="Cube",
        detalles={"accion": "navegar_a_objeto", "exito": False},
        campo_desconocido="se_descarta",
    )
    event_dump = event.model_dump(mode="json")
    event_roundtrip = EventoEntrante.model_validate(event_dump)

    string_false_event = EventoEntrante(
        origen="otro",
        tipo_evento="resultado_accion",
        id_transaccion="string-false",
        detalles={"exito": "false"},
    )

    action = AccionSaliente(
        id_accion="action-known-001",
        nombre="hablar",
        parametros={"texto_a_comunicar": "hola"},
        razonamiento="razon publica",
        pensamiento_privado="marcador privado sintetico",
        requiere_confirmacion="false",
        campo_desconocido="se_descarta",
    )
    response = RespuestaAPI(
        status="ok",
        mensaje="accion conocida",
        id_transaccion=event.id_transaccion,
        accion=action,
        campo_desconocido="se_descarta",
    )
    wire = response.model_dump(mode="json")
    response_roundtrip = RespuestaAPI.model_validate_json(response.model_dump_json())

    event_empty_a = EventoEntrante(
        origen="unity", tipo_evento="x", id_transaccion="mutable-event-a"
    )
    event_empty_b = EventoEntrante(
        origen="unity", tipo_evento="x", id_transaccion="mutable-event-b"
    )
    action_empty_a = AccionSaliente(id_accion="mutable-action-a", nombre="none")
    action_empty_b = AccionSaliente(id_accion="mutable-action-b", nombre="none")
    event_empty_a.detalles["only_a"] = True
    action_empty_a.parametros["only_a"] = True
    action_empty_a.parametros_descripcion.append(
        {"nombre": "only_a", "tipo": "bool"}
    )

    explicit_null_timestamp = EventoEntrante(
        origen="unity",
        tipo_evento="x",
        id_transaccion="null-timestamp",
        timestamp=None,
    )
    default_response = RespuestaAPI()
    pending_response = RespuestaAPI(status="pendiente")
    whitespace_event = EventoEntrante(
        origen="unity", tipo_evento=" ", id_transaccion="whitespace-event"
    )
    non_uuid_event = EventoEntrante(
        origen="unity", tipo_evento="x", id_transaccion="not-a-uuid"
    )

    checks = {
        "event_id_is_trimmed": event.id_transaccion == "dto-known-001",
        "event_false_bool_is_preserved": event.detalles["exito"] is False,
        "event_string_false_remains_string": (
            string_false_event.detalles["exito"] == "false"
            and isinstance(string_false_event.detalles["exito"], str)
        ),
        "event_unknown_top_level_is_ignored": "campo_desconocido" not in event_dump,
        "event_default_timestamp_is_datetime": isinstance(event.timestamp, datetime),
        "event_default_timestamp_is_naive": event.timestamp.utcoffset() is None,
        "event_json_timestamp_has_no_zone": (
            isinstance(event_dump["timestamp"], str)
            and not event_dump["timestamp"].endswith("Z")
            and "+" not in event_dump["timestamp"]
        ),
        "event_explicit_null_timestamp_is_preserved": explicit_null_timestamp.timestamp is None,
        "event_roundtrip_preserves_known_values": event_roundtrip == event,
        "event_blank_id_is_rejected": validation_rejected(
            EventoEntrante,
            origen="unity",
            tipo_evento="x",
            id_transaccion="   ",
        ),
        "event_non_uuid_id_is_accepted": non_uuid_event.id_transaccion == "not-a-uuid",
        "event_empty_tipo_is_rejected": validation_rejected(
            EventoEntrante,
            origen="unity",
            tipo_evento="",
            id_transaccion="empty-tipo",
        ),
        "event_whitespace_tipo_is_accepted": whitespace_event.tipo_evento == " ",
        "event_invalid_origin_is_rejected": validation_rejected(
            EventoEntrante,
            origen="Unity",
            tipo_evento="x",
            id_transaccion="invalid-origin",
        ),
        "event_null_details_is_rejected": validation_rejected(
            EventoEntrante,
            origen="unity",
            tipo_evento="x",
            id_transaccion="null-details",
            detalles=None,
        ),
        "action_string_false_is_coerced_safely": action.requiere_confirmacion is False,
        "action_empty_required_strings_are_accepted": (
            AccionSaliente(id_accion="", nombre="").id_accion == ""
        ),
        "action_unknown_top_level_is_ignored": (
            "campo_desconocido" not in action.model_dump(mode="json")
        ),
        "response_unknown_top_level_is_ignored": "campo_desconocido" not in wire,
        "response_default_id_is_null": default_response.id_transaccion is None,
        "response_default_action_is_null": default_response.accion is None,
        "response_pending_is_declared": pending_response.status == "pendiente",
        "response_unknown_status_is_rejected": validation_rejected(
            RespuestaAPI, status="pending"
        ),
        "response_roundtrip_preserves_known_values": response_roundtrip == response,
        "wire_private_thought_is_present": (
            wire["accion"]["pensamiento_privado"] == "marcador privado sintetico"
        ),
        "wire_parameter_descriptions_are_present": (
            wire["accion"]["parametros_descripcion"] == []
        ),
        "wire_server_timestamp_is_string": isinstance(wire["timestamp_servidor"], str),
        "event_mutable_default_is_isolated": event_empty_b.detalles == {},
        "action_parameters_default_is_isolated": action_empty_b.parametros == {},
        "action_parameter_description_default_is_isolated": (
            action_empty_b.parametros_descripcion == []
        ),
    }
    for label, condition in checks.items():
        check(condition, label)

    public = {
        "event_wire_keys": list(event_dump),
        "action_wire_keys": list(wire["accion"]),
        "response_wire_keys": list(wire),
        "event_unknown_policy": "ignore",
        "action_unknown_policy": "ignore",
        "response_unknown_policy": "ignore",
        "event_timestamp_when_omitted": "generated_naive_datetime",
        "event_timestamp_when_null": "null_preserved",
        "event_details_false_type": type(event.detalles["exito"]).__name__,
        "event_details_string_false_type": type(
            string_false_event.detalles["exito"]
        ).__name__,
        "action_string_false_result": action.requiere_confirmacion,
        "response_default_status": default_response.status,
        "response_declared_pending_literal": pending_response.status,
        "required_fields": {
            model.__name__: list(model.model_json_schema().get("required", []))
            for model in (EventoEntrante, AccionSaliente, RespuestaAPI)
        },
    }
    return public, checks


def static_contract() -> tuple[dict[str, Any], dict[str, bool]]:
    py_fields = python_fields()
    cs_fields, cs_types = unity_fields()
    routes = read(ROUTES_PATH)
    api_client = read(API_CLIENT_PATH)
    agent = read(AGENT_PATH)
    terminal = read(TERMINAL_PATH)

    for name, expected in EXPECTED_PYTHON_FIELDS.items():
        check(py_fields.get(name) == expected, f"Python fields changed: {name}")
    for name, expected in EXPECTED_UNITY_FIELDS.items():
        check(cs_fields.get(name) == expected, f"Unity fields changed: {name}")

    py_sets = {name: set(fields) for name, fields in py_fields.items()}
    cs_sets = {name: set(fields) for name, fields in cs_fields.items()}
    only_python = {
        name: sorted(py_sets[name] - cs_sets[name]) for name in EXPECTED_PYTHON_FIELDS
    }
    only_unity = {
        name: sorted(cs_sets[name] - py_sets[name]) for name in EXPECTED_PYTHON_FIELDS
    }

    checks = {
        "event_only_python_timestamp": only_python["EventoEntrante"] == ["timestamp"],
        "action_only_python_parameter_descriptions": (
            only_python["AccionSaliente"] == ["parametros_descripcion"]
        ),
        "response_names_match": not only_python["RespuestaAPI"] and not only_unity["RespuestaAPI"],
        "no_unity_only_fields": all(not fields for fields in only_unity.values()),
        "unity_confirmation_is_non_nullable_bool": (
            cs_types["AccionSaliente"]["requiere_confirmacion"] == "bool"
        ),
        "unity_action_parameters_are_untyped_object": (
            cs_types["AccionSaliente"]["parametros"] == "JObject"
        ),
        "unity_event_details_are_untyped_object_values": (
            cs_types["EventoEntrante"]["detalles"] == "Dictionary<string, object>"
        ),
        "api_error_exposes_exception_text_in_message": (
            'mensaje=f"Error interno: {str(e)}"' in routes
        ),
        "api_error_uses_http_500": (
            "JSONResponse(status_code=500" in routes
        ),
        "unity_only_deserializes_on_transport_success": (
            "request.result == UnityWebRequest.Result.Success" in api_client
            and "JsonConvert.DeserializeObject<RespuestaAPI>" in api_client
        ),
        "unity_transport_failure_surfaces_raw_body": (
            'string body = request.downloadHandler != null ? request.downloadHandler.text : "";'
            in api_client
            and 'codigo={request.responseCode}. {body}' in api_client
        ),
        "unity_pending_is_not_distinguished_from_success": (
            'respuesta.status == "error"' in api_client
            and 'respuesta.status == "pendiente"' not in api_client
        ),
        "unity_does_not_read_response_transaction_id": (
            "respuesta.id_transaccion" not in api_client
            and "respuesta.id_transaccion" not in agent
        ),
        "unity_does_not_read_server_timestamp": (
            "respuesta.timestamp_servidor" not in api_client
            and "respuesta.timestamp_servidor" not in agent
        ),
        "unity_does_not_read_action_id": "respuesta.accion.id_accion" not in agent,
        "unity_does_not_read_public_reason": "respuesta.accion.razonamiento" not in agent,
        "unity_reads_and_displays_private_thought": (
            "respuesta.accion.pensamiento_privado" in agent
            and 'textoPensamientoUI.text = $"[Pensando...]' in agent
        ),
        "unity_generates_request_transaction_id": (
            "id_transaccion = Guid.NewGuid().ToString()" in agent
        ),
        "unity_does_not_send_event_timestamp": "timestamp" not in cs_fields["EventoEntrante"],
        "unity_sends_action_result_success_as_bool": (
            "bool exito" in agent and 'detalles["exito"] = exito;' in agent
        ),
        "unity_action_names_are_a_local_switch_contract": (
            "switch (respuesta.accion.nombre)" in agent
            and 'case "navegar_a_objeto"' in agent
            and 'case "hablar"' in agent
        ),
        "unity_action_parameter_keys_are_consumed_outside_dto": (
            'parametros?["texto_a_comunicar"]' in agent
            and 'parametros?["nombre_objeto"]' in agent
            and 'TryLeerFloat(parametros, "x"' in agent
        ),
        "command_endpoint_overwrites_declared_event_type": (
            'comando.tipo_evento = "comando_usuario"' in routes
        ),
        "terminal_uses_independent_dict_contract": (
            'payload = {' in terminal
            and 'respuesta.get("accion")' in terminal
            and 'accion.get("parametros")' in terminal
        ),
        "terminal_does_not_read_private_thought": (
            'accion.get("pensamiento_privado")' not in terminal
        ),
    }
    for label, condition in checks.items():
        check(condition, label)

    public = {
        "python_fields": py_fields,
        "unity_fields": cs_fields,
        "unity_types": cs_types,
        "only_python": only_python,
        "only_unity": only_unity,
        "unity_response_fields_read": ["status", "mensaje", "accion"],
        "unity_action_fields_read": [
            "nombre",
            "parametros",
            "pensamiento_privado",
            "requiere_confirmacion",
        ],
        "unity_response_fields_never_read": ["id_transaccion", "timestamp_servidor"],
        "unity_action_fields_never_read": [
            "id_accion",
            "razonamiento",
            "parametros_descripcion (not declared in Unity DTO)",
        ],
        "formal_error_field": None,
        "error_representation": "status=error + mensaje over HTTP 500; Unity uses transport callback",
    }
    return public, checks


def main() -> None:
    static_observations, static_checks = static_contract()
    model_observations, model_checks = model_contract()
    result = {
        "probe": "block_02_api_unity_dto_contracts",
        "scope": {
            "network_used": False,
            "server_started": False,
            "unity_compiled": False,
            "runtime_data_touched": False,
        },
        "status": "PASS",
        "checks_passed": len(static_checks) + len(model_checks),
        "static_contract": static_observations,
        "pydantic_roundtrip": model_observations,
        "checks": {**static_checks, **model_checks},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
