"""ROOT-038 B9: propiedades duplicadas fallan antes de la deserializacion Unity."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = ROOT / "unity_client" / "ResponseDuplicateWireBoundary.cs"
API_CLIENT_PATH = ROOT / "unity_client" / "ApiClient.cs"
BOUNDARY = BOUNDARY_PATH.read_text(encoding="utf-8-sig")
API_CLIENT = API_CLIENT_PATH.read_text(encoding="utf-8-sig")
FACADE = (ROOT / "unity_client" / "UnityResponseWireContractV1.cs").read_text(
    encoding="utf-8-sig"
)
FIXTURE = ROOT / "core" / "fixtures" / "root_038_action_binding_v1.jsonl"


def _canonical_action() -> dict[str, object]:
    return {
        "id_accion": "action-1",
        "nombre": "girar_avatar",
        "parametros": {"grados": 90},
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "tx-1",
        "parametros_descripcion": [],
        "perception_authority": None,
    }


def _canonical_response(action: object = ...) -> str:
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


def _valid_binding_response() -> str:
    vector = json.loads(
        next(line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line)
    )
    action = dict(vector["action"])
    action["perception_authority"] = vector["authority_reference"]
    return _canonical_response(action)


def _matrix() -> list[tuple[str, str, bool]]:
    exploit = (
        '{"status":"error","status":"ok","mensaje":"x",'
        '"id_transaccion":"tx","accion":{"id_accion":"a",'
        '"nombre":"hablar","nombre":"girar_avatar",'
        '"parametros":{"grados":90},"estado_ejecucion":"dispatched"}}'
    )
    cases: list[tuple[str, str, bool]] = [
        ("canonical response", _canonical_response(_canonical_action()), True),
        ("action absent", _canonical_response(), True),
        ("action null", _canonical_response(None), True),
        (
            "valid B7 cancel",
            _canonical_response(
                {
                    "id_accion": "cancel-1",
                    "nombre": "cancelar_tarea",
                    "parametros": {},
                    "estado_ejecucion": "dispatched",
                }
            ),
            True,
        ),
        ("valid B5 and B6 binding", _valid_binding_response(), True),
        ("single root wrong casing remains B8", '{"Status":"ok","accion":null}', True),
        (
            "single action wrong casing remains B8",
            '{"status":"ok","accion":{"Nombre":"hablar","Parametros":{}}}',
            True,
        ),
        ("root duplicate status same", '{"status":"ok","status":"ok"}', False),
        ("root duplicate status different", '{"status":"error","status":"ok"}', False),
        ("root status case collision", '{"status":"error","Status":"ok"}', False),
        ("root duplicate mensaje", '{"mensaje":"a","mensaje":"b"}', False),
        (
            "root duplicate transaction",
            '{"id_transaccion":"a","id_transaccion":"b"}',
            False,
        ),
        ("root duplicate action", '{"accion":null,"accion":null}', False),
        ("root duplicate decision", '{"decision":null,"decision":null}', False),
        (
            "root duplicate timestamp",
            '{"timestamp_servidor":"a","timestamp_servidor":"b"}',
            False,
        ),
        ("root duplicate unknown", '{"future":1,"future":1}', False),
        (
            "action duplicate id",
            '{"accion":{"id_accion":"a","id_accion":"a","parametros":{}}}',
            False,
        ),
        (
            "action duplicate name same",
            '{"accion":{"nombre":"hablar","nombre":"hablar","parametros":{}}}',
            False,
        ),
        (
            "action duplicate name different",
            '{"accion":{"nombre":"hablar","nombre":"girar_avatar","parametros":{}}}',
            False,
        ),
        (
            "action name case collision",
            '{"accion":{"nombre":"hablar","Nombre":"girar_avatar","parametros":{}}}',
            False,
        ),
        (
            "action duplicate parameters",
            '{"accion":{"parametros":{},"parametros":{}}}',
            False,
        ),
        (
            "action duplicate confirmation",
            '{"accion":{"requiere_confirmacion":false,"requiere_confirmacion":true,"parametros":{}}}',
            False,
        ),
        (
            "action duplicate execution state",
            '{"accion":{"estado_ejecucion":"created","estado_ejecucion":"dispatched","parametros":{}}}',
            False,
        ),
        (
            "action duplicate origin transaction",
            '{"accion":{"id_transaccion_origen":"a","id_transaccion_origen":"b","parametros":{}}}',
            False,
        ),
        (
            "action duplicate parameter descriptions",
            '{"accion":{"parametros_descripcion":[],"parametros_descripcion":[],"parametros":{}}}',
            False,
        ),
        (
            "action duplicate authority",
            '{"accion":{"perception_authority":null,"perception_authority":null,"parametros":{}}}',
            False,
        ),
        (
            "action duplicate unknown",
            '{"accion":{"future":1,"future":2,"parametros":{}}}',
            False,
        ),
        ("mandatory exploit", exploit, False),
        (
            "parameter duplicate remains owned by B7",
            '{"accion":{"nombre":"hablar","parametros":{"texto":"a","texto":"b"}}}',
            True,
        ),
        (
            "authority duplicate remains owned by B5",
            '{"accion":{"nombre":"hablar","parametros":{},"perception_authority":{"version":1,"version":1}}}',
            True,
        ),
        (
            "decision duplicate remains outside B9",
            '{"decision":{"version":1,"version":1},"accion":null}',
            True,
        ),
    ]
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


def test_actual_newtonsoft_duplicate_matrix() -> None:
    """Compila B9 con el Newtonsoft de Unity; no abre Unity."""

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
        f"ResponseDuplicateWireBoundary.{uuid4().hex}.dll"
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$compiled = {json.dumps(str(compiled_path))}
& {json.dumps(str(mono))} {json.dumps(str(compiler))} /nologo /target:library ("/out:" + $compiled) ("/reference:" + {json.dumps(str(newtonsoft))}) ("/reference:" + {json.dumps(str(netstandard))}) {json.dumps(str(BOUNDARY_PATH))}
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
    $actual = [ResponseDuplicateWireBoundary]::TryValidateResponse($raw)
    if ($actual -ne $expected) {{
        Write-Output ("FAIL|" + $name + "|expected=" + $expected + "|actual=" + $actual)
        $failed++
    }}
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


def test_b9_runs_before_b7_b5_typed_deserialization_and_b6() -> None:
    duplicate = FACADE.index("ResponseDuplicateWireBoundary.TryValidateResponse(rawJson)")
    action_envelope = FACADE.index("ActionEnvelopeWireBoundary.TryValidateResponse(rawJson)")
    authority = FACADE.index("AuthorityWireBoundary.TryValidateResponse(rawJson)")
    deserialization = FACADE.index("JsonConvert.DeserializeObject<RespuestaAPI>(rawJson)")
    binding = FACADE.index("ActionBindingVerifierV1.TryVerifyResponse(response)")

    assert duplicate < action_envelope < authority < deserialization < binding


def test_mandatory_exploit_is_rejected_before_typed_execution() -> None:
    matrix = {name: (payload, expected) for name, payload, expected in _matrix()}
    exploit, expected = matrix["mandatory exploit"]

    assert expected is False
    assert '"status":"error","status":"ok"' in exploit
    assert '"nombre":"hablar","nombre":"girar_avatar"' in exploit

    guarded = FACADE.split(
        "if (!ResponseDuplicateWireBoundary.TryValidateResponse(rawJson))", 1
    )[1]
    failure = API_CLIENT.split("if (respuesta == null)", 1)[1].split("else if", 1)[0]
    assert "TryParseV0" in guarded and "TryParseV1" in guarded
    assert '"INVALID_RESPONSE"' in failure
    assert "onSuccess" not in failure


def test_boundary_is_structural_and_has_no_side_effect_path() -> None:
    assert "new HashSet<string>(StringComparer.OrdinalIgnoreCase)" in BOUNDARY
    assert "ObjectScope.Root" in BOUNDARY
    assert "ObjectScope.Action" in BOUNDARY
    assert "ObjectScope.Parameters" not in BOUNDARY
    assert "JsonConvert.DeserializeObject" not in BOUNDARY
    for forbidden in (
        "LazzaroAgent",
        "ProcesarRespuestaDelCerebro",
        "EncolarInicioEjecucion",
        "GirarAvatar",
        "FrenarMovimiento",
        "onSuccess",
    ):
        assert forbidden not in BOUNDARY


def test_single_wrong_case_names_are_not_decided_by_b9() -> None:
    matrix = {name: expected for name, _, expected in _matrix()}
    assert matrix["single root wrong casing remains B8"] is True
    assert matrix["single action wrong casing remains B8"] is True
