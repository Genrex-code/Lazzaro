"""ROOT-038 B7: accion.parametros y cancelar_tarea fallan cerrado antes de Unity."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.schemas import AccionSaliente


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = ROOT / "unity_client" / "ActionEnvelopeWireBoundary.cs"
DTO_PATH = ROOT / "unity_client" / "DTOs.cs"
API_CLIENT_PATH = ROOT / "unity_client" / "ApiClient.cs"
AGENT_PATH = ROOT / "unity_client" / "LazzaroAgent.cs"
BOUNDARY = BOUNDARY_PATH.read_text(encoding="utf-8-sig")
API_CLIENT = API_CLIENT_PATH.read_text(encoding="utf-8-sig")
AGENT = AGENT_PATH.read_text(encoding="utf-8-sig")


def _response(action: object = ...) -> str:
    payload: dict[str, object] = {
        "status": "ok",
        "mensaje": "ok",
        "id_transaccion": "tx-1",
        "decision": None,
        "timestamp_servidor": "2026-10-10T00:00:00Z",
    }
    if action is not ...:
        payload["accion"] = action
    return json.dumps(payload, separators=(",", ":"))


def _cancel(parameters: object = ...) -> dict[str, object]:
    action: dict[str, object] = {
        "id_accion": "action-1",
        "nombre": "cancelar_tarea",
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "tx-1",
    }
    if parameters is not ...:
        action["parametros"] = parameters
    return action


def _other(parameters: object = ...) -> dict[str, object]:
    action = _cancel(parameters)
    action["nombre"] = "hablar"
    return action


def _with_action_field(field: str, value: object) -> dict[str, object]:
    action = _cancel({})
    action[field] = value
    return action


def _without_action_field(field: str) -> dict[str, object]:
    action = _cancel({})
    action.pop(field)
    return action


def _wrong_case_action_field(canonical: str, wire_name: str) -> dict[str, object]:
    action = _cancel({})
    action[wire_name] = action.pop(canonical)
    return action


def _matrix() -> list[tuple[str, str, bool]]:
    cases: list[tuple[str, str, bool]] = [
        ("action missing preserves no-action", _response(), True),
        ("action null preserves no-action", _response(None), True),
        ("cancel empty object", _response(_cancel({})), True),
        ("parameters missing", _response(_cancel()), False),
        ("parameters null", _response(_cancel(None)), False),
        ("parameters array", _response(_cancel([])), False),
        ("parameters string", _response(_cancel("")), False),
        ("parameters boolean", _response(_cancel(True)), False),
        ("parameters number", _response(_cancel(1)), False),
        (
            "cancelar_movimiento missing keeps field default",
            _response(_cancel({"cancelar_cola": False})),
            True,
        ),
        (
            "cancelar_cola missing keeps field default",
            _response(_cancel({"cancelar_movimiento": False})),
            True,
        ),
        (
            "motivo missing keeps field default",
            _response(_cancel({"texto_confirmacion": "listo"})),
            True,
        ),
        (
            "texto_confirmacion missing keeps field default",
            _response(_cancel({"motivo": "usuario"})),
            True,
        ),
        ("other action object", _response(_other({"texto_a_comunicar": "ok"})), True),
        ("other action missing parameters", _response(_other()), False),
        ("other action null parameters", _response(_other(None)), False),
    ]

    for field in ("cancelar_movimiento", "cancelar_cola"):
        cases.extend(
            [
                (f"{field} true", _response(_cancel({field: True})), True),
                (f"{field} false", _response(_cancel({field: False})), True),
                (f"{field} null", _response(_cancel({field: None})), False),
                (f"{field} string true", _response(_cancel({field: "true"})), False),
                (f"{field} string false", _response(_cancel({field: "false"})), False),
                (f"{field} zero", _response(_cancel({field: 0})), False),
                (f"{field} one", _response(_cancel({field: 1})), False),
                (f"{field} object", _response(_cancel({field: {}})), False),
                (f"{field} array", _response(_cancel({field: []})), False),
            ]
        )

    for field in ("motivo", "texto_confirmacion"):
        cases.extend(
            [
                (f"{field} string", _response(_cancel({field: "texto"})), True),
                (f"{field} empty string", _response(_cancel({field: ""})), True),
                (
                    f"{field} date-shaped JSON string",
                    _response(_cancel({field: "2026-10-10T00:00:00Z"})),
                    True,
                ),
                (f"{field} null", _response(_cancel({field: None})), False),
                (f"{field} boolean", _response(_cancel({field: True})), False),
                (f"{field} number", _response(_cancel({field: 1})), False),
                (f"{field} object", _response(_cancel({field: {}})), False),
                (f"{field} array", _response(_cancel({field: []})), False),
            ]
        )

    cases.extend(
        [
            (
                "all fields valid",
                _response(
                    _cancel(
                        {
                            "motivo": "usuario",
                            "cancelar_movimiento": False,
                            "cancelar_cola": True,
                            "texto_confirmacion": "listo",
                        }
                    )
                ),
                True,
            ),
            ("unknown parameter", _response(_cancel({"extra": True})), False),
            (
                "wrong-case parameter is unknown",
                _response(_cancel({"Cancelar_Movimiento": True})),
                False,
            ),
            (
                "parameters may precede action name",
                '{"status":"ok","accion":{"parametros":{},"nombre":"cancelar_tarea","id_accion":"action-1"}}',
                True,
            ),
            (
                "duplicate cancel parameter",
                '{"status":"ok","accion":{"nombre":"cancelar_tarea","parametros":{"cancelar_cola":true,"cancelar_cola":false}}}',
                False,
            ),
            (
                "duplicate parameters object",
                '{"status":"ok","accion":{"nombre":"cancelar_tarea","parametros":{},"parametros":{}}}',
                False,
            ),
        ]
    )

    invalid_scalar_values = (
        ("null", None),
        ("boolean true", True),
        ("boolean false", False),
        ("integer", 7),
        ("float", 1.2),
        ("object", {}),
        ("array", []),
    )
    for field in ("id_accion", "nombre"):
        cases.append(
            (f"{field} missing", _response(_without_action_field(field)), False)
        )
        for label, value in invalid_scalar_values:
            cases.append(
                (
                    f"{field} {label}",
                    _response(_with_action_field(field, value)),
                    False,
                )
            )
        for label, value in (("empty string", ""), ("whitespace string", "   ")):
            cases.append(
                (
                    f"{field} {label} remains Python-compatible",
                    _response(_with_action_field(field, value)),
                    True,
                )
            )

    cases.extend(
        [
            (
                "requiere_confirmacion missing defaults false",
                _response(_without_action_field("requiere_confirmacion")),
                True,
            ),
            (
                "requiere_confirmacion true",
                _response(_with_action_field("requiere_confirmacion", True)),
                True,
            ),
            (
                "requiere_confirmacion false",
                _response(_with_action_field("requiere_confirmacion", False)),
                True,
            ),
        ]
    )
    for label, value in (
        ("null", None),
        ("string true", "true"),
        ("string false", "false"),
        ("integer", 1),
        ("float", 1.0),
        ("object", {}),
        ("array", []),
    ):
        cases.append(
            (
                f"requiere_confirmacion {label}",
                _response(_with_action_field("requiere_confirmacion", value)),
                False,
            )
        )

    cases.append(
        (
            "estado_ejecucion missing defaults legacy_unknown",
            _response(_without_action_field("estado_ejecucion")),
            True,
        )
    )
    for state in (
        "legacy_unknown",
        "created",
        "awaiting_authorization",
        "authorized",
        "dispatched",
        "executing",
        "reported_success",
        "reported_failure",
        "rejected",
        "skipped",
    ):
        cases.append(
            (
                f"estado_ejecucion canonical {state}",
                _response(_with_action_field("estado_ejecucion", state)),
                True,
            )
        )
    for label, value in (
        ("null", None),
        ("unknown string", "UNKNOWN_VALUE"),
        ("integer", 7),
        ("boolean", True),
        ("object", {}),
        ("array", []),
    ):
        cases.append(
            (
                f"estado_ejecucion {label}",
                _response(_with_action_field("estado_ejecucion", value)),
                False,
            )
        )

    cases.extend(
        [
            (
                "id_transaccion_origen missing",
                _response(_without_action_field("id_transaccion_origen")),
                True,
            ),
            (
                "id_transaccion_origen null",
                _response(_with_action_field("id_transaccion_origen", None)),
                True,
            ),
            (
                "id_transaccion_origen string",
                _response(_with_action_field("id_transaccion_origen", "tx-2")),
                True,
            ),
            (
                "id_transaccion_origen empty string",
                _response(_with_action_field("id_transaccion_origen", "")),
                True,
            ),
            (
                "id_transaccion_origen whitespace string",
                _response(_with_action_field("id_transaccion_origen", "   ")),
                True,
            ),
        ]
    )
    for label, value in (
        ("boolean", True),
        ("integer", 7),
        ("float", 1.2),
        ("object", {}),
        ("array", []),
    ):
        cases.append(
            (
                f"id_transaccion_origen {label}",
                _response(_with_action_field("id_transaccion_origen", value)),
                False,
            )
        )

    for canonical, wire_name in (
        ("id_accion", "Id_accion"),
        ("nombre", "Nombre"),
        ("requiere_confirmacion", "Requiere_confirmacion"),
        ("estado_ejecucion", "Estado_ejecucion"),
        ("id_transaccion_origen", "Id_transaccion_origen"),
    ):
        cases.append(
            (
                f"B8 single wrong-case {canonical} remains compatible",
                _response(_wrong_case_action_field(canonical, wire_name)),
                True,
            )
        )

    physical = {
        "id_accion": 7,
        "nombre": "girar_avatar",
        "parametros": {"grados": 90},
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "tx-1",
        "perception_authority": None,
    }
    cases.append(
        (
            "mandatory physical numeric id_accion is rejected",
            _response(physical),
            False,
        )
    )
    return cases


def _unity_csharp_toolchain() -> tuple[Path, Path, Path, Path] | None:
    editors = Path(r"C:\Program Files\Unity\Hub\Editor")
    if not editors.exists():
        return None
    for editor in sorted(editors.iterdir(), reverse=True):
        mono_root = editor / "Editor" / "Data" / "MonoBleedingEdge"
        mono = mono_root / "bin" / "mono.exe"
        compiler = mono_root / "lib" / "mono" / "4.5" / "csc.exe"
        netstandard = mono_root / "lib" / "mono" / "4.5" / "Facades" / "netstandard.dll"
        newtonsoft = editor / "Editor" / "Data" / "Managed" / "Newtonsoft.Json.dll"
        if all(path.exists() for path in (mono, compiler, netstandard, newtonsoft)):
            return mono, compiler, netstandard, newtonsoft
    return None


def test_actual_newtonsoft_action_envelope_matrix() -> None:
    """Compila la frontera pura con el Newtonsoft de Unity; no abre Unity."""

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    toolchain = _unity_csharp_toolchain()
    if pwsh is None or toolchain is None:
        pytest.skip("toolchain C# de Unity no disponible para el probe puro")
    mono, compiler, netstandard, newtonsoft = toolchain

    cases = _matrix()
    encoded = [
        (name, base64.b64encode(payload.encode("utf-8")).decode("ascii"), expected)
        for name, payload, expected in cases
    ]
    case_lines = ",\n".join(
        "@(%s,%s,%s)"
        % (
            json.dumps(name),
            json.dumps(payload),
            "$true" if expected else "$false",
        )
        for name, payload, expected in encoded
    )
    compiled_path = Path(tempfile.gettempdir()) / (
        f"ActionEnvelopeWireBoundary.{uuid4().hex}.dll"
    )
    defaults_payload = _response(
        {
            "id_accion": "action-defaults",
            "nombre": "hablar",
            "parametros": {},
        }
    )
    defaults_b64 = base64.b64encode(defaults_payload.encode("utf-8")).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$compiled = {json.dumps(str(compiled_path))}
& {json.dumps(str(mono))} {json.dumps(str(compiler))} /nologo /target:library ("/out:" + $compiled) ("/reference:" + {json.dumps(str(newtonsoft))}) ("/reference:" + {json.dumps(str(netstandard))}) {json.dumps(str(BOUNDARY_PATH))} {json.dumps(str(DTO_PATH))}
if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
Add-Type -Path {json.dumps(str(newtonsoft))}
Add-Type -Path $compiled
$cases = @(
{case_lines}
)
$failed = 0
foreach ($case in $cases) {{
    $name = [string]$case[0]
    $raw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$case[1]))
    $expected = [bool]$case[2]
    $actual = [ActionEnvelopeWireBoundary]::TryValidateResponse($raw)
    if ($actual -ne $expected) {{
        Write-Output ("FAIL|" + $name + "|expected=" + $expected + "|actual=" + $actual)
        $failed++
    }}
}}
$defaultsRaw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({json.dumps(defaults_b64)}))
$defaults = [Newtonsoft.Json.JsonConvert]::DeserializeObject[RespuestaAPI]($defaultsRaw)
if (
    ($null -eq $defaults.accion) -or
    ($defaults.accion.requiere_confirmacion -ne $false) -or
    ($defaults.accion.estado_ejecucion -ne "legacy_unknown") -or
    ($null -ne $defaults.accion.id_transaccion_origen)
) {{
    Write-Output "FAIL|typed defaults did not match Python"
    $failed++
}}
if ($failed -ne 0) {{ exit 1 }}
Write-Output ("PASS|" + $cases.Count)
"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8-sig",
        suffix=".ps1",
        delete=False,
    ) as script_file:
        script_file.write(script)
        script_path = Path(script_file.name)
    try:
        completed = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-File", str(script_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    finally:
        script_path.unlink(missing_ok=True)
        compiled_path.unlink(missing_ok=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"PASS|{len(cases)}" in completed.stdout


def test_python_action_scalar_source_of_truth() -> None:
    base = {
        "id_accion": "action-1",
        "nombre": "girar_avatar",
        "parametros": {"grados": 90},
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "tx-1",
    }

    for required in ("id_accion", "nombre"):
        missing = dict(base)
        missing.pop(required)
        with pytest.raises(ValidationError):
            AccionSaliente.model_validate(missing)
        for value in (None, True, False, 7, 1.2, {}, []):
            invalid = dict(base)
            invalid[required] = value
            with pytest.raises(ValidationError):
                AccionSaliente.model_validate(invalid)
        for value in ("value", "", "   "):
            valid = dict(base)
            valid[required] = value
            assert getattr(AccionSaliente.model_validate(valid), required) == value

    missing_confirmation = dict(base)
    missing_confirmation.pop("requiere_confirmacion")
    assert AccionSaliente.model_validate(missing_confirmation).requiere_confirmacion is False
    for value in (True, False):
        valid = dict(base)
        valid["requiere_confirmacion"] = value
        assert AccionSaliente.model_validate(valid).requiere_confirmacion is value
    for value in (None, "true", "false", 0, 1, 1.0, {}, []):
        invalid = dict(base)
        invalid["requiere_confirmacion"] = value
        with pytest.raises(ValidationError):
            AccionSaliente.model_validate(invalid)

    lifecycle = (
        "legacy_unknown",
        "created",
        "awaiting_authorization",
        "authorized",
        "dispatched",
        "executing",
        "reported_success",
        "reported_failure",
        "rejected",
        "skipped",
    )
    missing_state = dict(base)
    missing_state.pop("estado_ejecucion")
    assert AccionSaliente.model_validate(missing_state).estado_ejecucion == "legacy_unknown"
    for value in lifecycle:
        valid = dict(base)
        valid["estado_ejecucion"] = value
        assert AccionSaliente.model_validate(valid).estado_ejecucion == value
    for value in (None, "UNKNOWN_VALUE", True, 7, 1.2, {}, []):
        invalid = dict(base)
        invalid["estado_ejecucion"] = value
        with pytest.raises(ValidationError):
            AccionSaliente.model_validate(invalid)

    for mode, value in (("missing", ...), ("null", None), ("string", "tx")):
        valid = dict(base)
        if value is ...:
            valid.pop("id_transaccion_origen")
        else:
            valid["id_transaccion_origen"] = value
        expected = None if mode in {"missing", "null"} else "tx"
        assert AccionSaliente.model_validate(valid).id_transaccion_origen == expected
    for value in (True, False, 7, 1.2, {}, []):
        invalid = dict(base)
        invalid["id_transaccion_origen"] = value
        with pytest.raises(ValidationError):
            AccionSaliente.model_validate(invalid)


def test_b7_runs_before_b5_typed_deserialization_and_b6() -> None:
    action_envelope = API_CLIENT.index(
        "ActionEnvelopeWireBoundary.TryValidateResponse(responseBody)"
    )
    authority = API_CLIENT.index("AuthorityWireBoundary.TryValidateResponse(responseBody)")
    deserialization = API_CLIENT.index(
        "JsonConvert.DeserializeObject<RespuestaAPI>(responseBody)"
    )
    binding = API_CLIENT.index("ActionBindingVerifierV1.TryVerifyResponse(respuesta)")

    assert action_envelope < authority < deserialization < binding


def test_rejection_precedes_all_cancellation_side_effects() -> None:
    guarded_branch = API_CLIENT.split(
        "if (ActionEnvelopeWireBoundary.TryValidateResponse(responseBody))",
        1,
    )[1].split("if (respuesta == null)", 1)[0]
    failure_branch = API_CLIENT.split("if (respuesta == null)", 1)[1].split(
        "else if", 1
    )[0]

    assert "JsonConvert.DeserializeObject<RespuestaAPI>" in guarded_branch
    assert '"INVALID_RESPONSE"' in failure_branch
    assert "onFailure?.Invoke" in failure_branch
    assert "onSuccess" not in failure_branch
    for forbidden in (
        "LazzaroAgent",
        "ProcesarRespuestaDelCerebro",
        "EjecutarCancelarTarea",
        "FrenarMovimiento",
        "CancelarFilaTareasUsuario",
        "ReportarResultadoAccion",
    ):
        assert forbidden not in BOUNDARY


def test_defaults_exist_only_inside_validated_cancel_handler() -> None:
    handler = AGENT.split("private void EjecutarCancelarTarea(", 1)[1].split(
        "private static string ConstruirManualEspacial()", 1
    )[0]

    assert 'parametros?["motivo"]?.ToString() ?? "Solicitud explicita del usuario."' in handler
    assert 'parametros?["cancelar_movimiento"]?.Value<bool>() ?? true' in handler
    assert 'parametros?["cancelar_cola"]?.Value<bool>() ?? true' in handler
    assert (
        'parametros?["texto_confirmacion"]?.ToString() ?? "De acuerdo, cancelo la tarea actual."'
        in handler
    )
    assert "movimiento.FrenarMovimiento()" in handler
    assert "CancelarFilaTareasUsuario(motivo)" in handler


def test_cancel_contract_is_scoped_and_uses_raw_json_token_types() -> None:
    assert 'string.Equals(actionName, "cancelar_tarea", StringComparison.Ordinal)' in BOUNDARY
    assert 'case "cancelar_movimiento":' in BOUNDARY
    assert 'case "cancelar_cola":' in BOUNDARY
    assert 'case "motivo":' in BOUNDARY
    assert 'case "texto_confirmacion":' in BOUNDARY
    assert "entry.Value != JsonToken.Boolean" in BOUNDARY
    assert "entry.Value != JsonToken.String" in BOUNDARY
    assert "reader.DateParseHandling = DateParseHandling.None" in BOUNDARY
    assert "JsonConvert.DeserializeObject" not in BOUNDARY
    assert "AuthorityWireBoundary" not in BOUNDARY
    assert "ActionBindingVerifierV1" not in BOUNDARY
