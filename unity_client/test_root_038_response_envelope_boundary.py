"""ROOT-038 B14: contrato raw estricto del envelope raiz RespuestaAPI."""

from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
import pytest
from pydantic import ValidationError

from api.schemas import RespuestaAPI


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = ROOT / "unity_client" / "ResponseEnvelopeWireBoundaryV1.cs"
API_CLIENT_PATH = ROOT / "unity_client" / "ApiClient.cs"
B9_PATH = ROOT / "unity_client" / "ResponseDuplicateWireBoundary.cs"
BOUNDARY = BOUNDARY_PATH.read_text(encoding="utf-8-sig")
API_CLIENT = API_CLIENT_PATH.read_text(encoding="utf-8-sig")
B9 = B9_PATH.read_text(encoding="utf-8-sig")


def _action() -> dict[str, object]:
    return {
        "id_accion": "action-7",
        "nombre": "navegar_a_posicion",
        "parametros": {"x": 1.0, "y": 2.0, "z": 3.0},
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "7",
        "parametros_descripcion": [],
        "perception_authority": None,
    }


def _payload() -> dict[str, object]:
    return {
        "status": "ok",
        "mensaje": "ok",
        "id_transaccion": "7",
        "accion": None,
        "decision": None,
        "timestamp_servidor": "2026-10-10T00:00:00Z",
    }


def _wire(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _with(field: str, value: object) -> str:
    payload = _payload()
    payload[field] = value
    return _wire(payload)


def _without(field: str) -> str:
    payload = _payload()
    payload.pop(field)
    return _wire(payload)


def _wrong_case(field: str, wire_name: str) -> str:
    payload = _payload()
    payload[wire_name] = payload.pop(field)
    return _wire(payload)


def _matrix() -> list[tuple[str, str, bool]]:
    cases: list[tuple[str, str, bool]] = [
        ("canonical response", _wire(_payload()), True),
        ("unknown root extra remains compatible", _wire({**_payload(), "future": 1}), True),
    ]

    for value in ("ok", "error", "pendiente"):
        cases.append((f"status {value}", _with("status", value), True))
    for label, value in (
        ("unknown", "unknown"),
        ("wrong-case value", "OK"),
        ("legacy spelling", "pending"),
        ("null", None),
        ("integer", 1),
        ("float", 1.2),
        ("boolean", True),
        ("object", {}),
        ("array", []),
    ):
        cases.append((f"status {label}", _with("status", value), False))

    for field in ("mensaje", "timestamp_servidor"):
        cases.extend(
            [
                (f"{field} string", _with(field, "value"), True),
                (f"{field} null", _with(field, None), False),
                (f"{field} integer", _with(field, 7), False),
                (f"{field} float", _with(field, 1.2), False),
                (f"{field} boolean", _with(field, True), False),
                (f"{field} object", _with(field, {}), False),
                (f"{field} array", _with(field, []), False),
            ]
        )

    cases.extend(
        [
            ("transaction string", _with("id_transaccion", "7"), True),
            ("transaction empty string", _with("id_transaccion", ""), True),
            ("transaction whitespace string", _with("id_transaccion", "   "), True),
            ("transaction null", _with("id_transaccion", None), True),
            ("transaction integer", _with("id_transaccion", 7), False),
            ("transaction float", _with("id_transaccion", 1.2), False),
            ("transaction boolean", _with("id_transaccion", True), False),
            ("transaction object", _with("id_transaccion", {}), False),
            ("transaction array", _with("id_transaccion", []), False),
        ]
    )

    for field, valid_object in (("accion", _action()), ("decision", {"version": 1})):
        cases.extend(
            [
                (f"{field} missing", _without(field), True),
                (f"{field} null", _with(field, None), True),
                (f"{field} object", _with(field, valid_object), True),
                (f"{field} string", _with(field, "value"), False),
                (f"{field} integer", _with(field, 7), False),
                (f"{field} float", _with(field, 1.2), False),
                (f"{field} boolean", _with(field, True), False),
                (f"{field} array", _with(field, []), False),
            ]
        )

    for field in (
        "status",
        "mensaje",
        "id_transaccion",
        "accion",
        "decision",
        "timestamp_servidor",
    ):
        cases.append((f"{field} missing", _without(field), True))

    for field, wire_name in (
        ("status", "Status"),
        ("mensaje", "Mensaje"),
        ("id_transaccion", "Id_transaccion"),
        ("accion", "Accion"),
        ("decision", "Decision"),
        ("timestamp_servidor", "Timestamp_servidor"),
    ):
        cases.append((f"B8 single wrong-case {field}", _wrong_case(field, wire_name), True))

    exploit = _payload()
    exploit["id_transaccion"] = 7
    exploit["accion"] = _action()
    cases.append(("B14 integer correlation exploit", _wire(exploit), False))
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


def test_actual_unity_newtonsoft_root_response_matrix() -> None:
    """Compila B14 con Mono/Newtonsoft de Unity; no abre Unity."""

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
        % (json.dumps(name), json.dumps(payload), "$true" if expected else "$false")
        for name, payload, expected in encoded
    )
    compiled_path = Path(tempfile.gettempdir()) / (
        f"ResponseEnvelopeWireBoundaryV1.{uuid4().hex}.dll"
    )
    exploit = next(
        payload
        for name, payload, expected in cases
        if name == "B14 integer correlation exploit" and not expected
    )
    encoded_exploit = base64.b64encode(exploit.encode("utf-8")).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$compiled = {json.dumps(str(compiled_path))}
& {json.dumps(str(mono))} {json.dumps(str(compiler))} /nologo /target:library ("/out:" + $compiled) ("/reference:" + {json.dumps(str(newtonsoft))}) ("/reference:" + {json.dumps(str(netstandard))}) {json.dumps(str(BOUNDARY_PATH))} {json.dumps(str(B9_PATH))}
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
    $actual = [ResponseEnvelopeWireBoundaryV1]::TryValidateResponse($raw)
    if ($actual -ne $expected) {{
        Write-Output ("FAIL|" + $name + "|expected=" + $expected + "|actual=" + $actual)
        $failed++
    }}
}}
if ($failed -ne 0) {{ exit 1 }}
$exploit = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({json.dumps(encoded_exploit)}))
if (-not [ResponseDuplicateWireBoundary]::TryValidateResponse($exploit)) {{
    Write-Output "FAIL|exploit|B9 must accept the non-duplicate document"
    exit 1
}}
if ([ResponseEnvelopeWireBoundaryV1]::TryValidateResponse($exploit)) {{
    Write-Output "FAIL|exploit|B14 must reject integer id_transaccion"
    exit 1
}}
Write-Output ("PASS|" + $cases.Count)
Write-Output "PASS_EXPLOIT|B9=true|B14=false"
"""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", suffix=".ps1", delete=False
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
    assert "PASS_EXPLOIT|B9=true|B14=false" in completed.stdout


def test_python_contract_and_actual_default_wire_shape() -> None:
    defaults = RespuestaAPI()
    dumped = defaults.model_dump(mode="json")
    encoded = jsonable_encoder(defaults)

    assert list(dumped) == [
        "status",
        "mensaje",
        "id_transaccion",
        "accion",
        "decision",
        "timestamp_servidor",
    ]
    assert dumped == encoded
    assert dumped["status"] == "ok"
    assert dumped["mensaje"] == ""
    assert dumped["id_transaccion"] is None
    assert dumped["accion"] is None
    assert dumped["decision"] is None
    assert isinstance(dumped["timestamp_servidor"], str)

    for invalid in (7, 1.2, True, {}, []):
        with pytest.raises(ValidationError):
            RespuestaAPI(id_transaccion=invalid)
    assert RespuestaAPI(id_transaccion="7").id_transaccion == "7"
    assert RespuestaAPI(id_transaccion=None).id_transaccion is None
    with_extra = RespuestaAPI(extra_future=1).model_dump(mode="json")
    assert "extra_future" not in with_extra
    assert list(with_extra) == list(dumped)


def test_b14_pipeline_order_and_failure_is_before_typed_deserialization() -> None:
    duplicate = API_CLIENT.index(
        "ResponseDuplicateWireBoundary.TryValidateResponse(responseBody)"
    )
    root = API_CLIENT.index(
        "ResponseEnvelopeWireBoundaryV1.TryValidateResponse(responseBody)"
    )
    action = API_CLIENT.index(
        "ActionEnvelopeWireBoundary.TryValidateResponse(responseBody)"
    )
    physical = API_CLIENT.index(
        "PhysicalActionWireBoundaryV1.TryValidateResponse(responseBody)"
    )
    authority = API_CLIENT.index(
        "AuthorityWireBoundary.TryValidateResponse(responseBody)"
    )
    typed = API_CLIENT.index("JsonConvert.DeserializeObject<RespuestaAPI>(responseBody)")
    binding = API_CLIENT.index("ActionBindingVerifierV1.TryVerifyResponse(respuesta)")
    assert duplicate < root < action < physical < authority < typed < binding
    assert '"INVALID_RESPONSE"' in API_CLIENT


def test_b8_and_b9_ownership_remain_separate() -> None:
    assert "StringComparison.OrdinalIgnoreCase" in BOUNDARY
    assert "HashSet<string>(StringComparer.OrdinalIgnoreCase)" in B9
    assert "HashSet<string>" not in BOUNDARY
    assert "id_accion" not in BOUNDARY
    assert "estado_ejecucion" not in BOUNDARY


def test_b14_exploit_is_rejected_before_correlation_or_dispatch() -> None:
    exploit = next(payload for name, payload, expected in _matrix() if name == "B14 integer correlation exploit" and not expected)
    assert '"id_transaccion":7' in exploit
    assert '"nombre":"navegar_a_posicion"' in exploit
    assert '"estado_ejecucion":"dispatched"' in exploit
    assert "JsonConvert.DeserializeObject<RespuestaAPI>" not in BOUNDARY
    assert "ProcesarRespuestaDelCerebro" not in BOUNDARY
