"""ROOT-038 B5: autoridad JSON estricta antes de coercion tipada en Unity."""

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
BOUNDARY_PATH = ROOT / "unity_client" / "AuthorityWireBoundary.cs"
API_CLIENT_PATH = ROOT / "unity_client" / "ApiClient.cs"
BOUNDARY = BOUNDARY_PATH.read_text(encoding="utf-8-sig")
API_CLIENT = API_CLIENT_PATH.read_text(encoding="utf-8-sig")


def _authority(generation: object = 1) -> dict[str, object]:
    return {
        "version": 1,
        "authority_decision_id": "authority-1",
        "id_accion": "action-1",
        "authority_policy_id": "policy-1",
        "freshness_policy_id": "freshness-1",
        "generation_binding_mode": "REQUIRE_AUTHORIZED_GENERATION",
        "producer_capture_id": "capture-1",
        "generation": generation,
        "target": {
            "contract_version": 1,
            "producer_instance_id": "unity-1",
            "stream_id": "world",
            "producer_epoch_id": "epoch-1",
            "logical_world_id": "world-1",
            "producer_capture_id": "capture-1",
            "generation": generation,
            "producer_object_id": "box-1",
            "runtime_object_id": "42",
        },
        "action_name": "agarrar_objeto",
        "canonical_parameters_json": '{"nombre_objeto":"Caja"}',
        "interaction_type": None,
        "safety_disposition": "SAFE",
        "safety_reason_code": "NO_HAZARD",
        "action_binding_sha256": "0" * 64,
    }


def _response(authority: object = ...) -> str:
    action: dict[str, object] = {
        "id_accion": "action-1",
        "nombre": "agarrar_objeto",
        "parametros": {"nombre_objeto": "Caja"},
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "tx-1",
    }
    if authority is not ...:
        action["perception_authority"] = authority
    return json.dumps(
        {
            "status": "ok",
            "mensaje": "ok",
            "id_transaccion": "tx-1",
            "accion": action,
            "decision": None,
            "timestamp_servidor": "2026-10-10T00:00:00Z",
        },
        separators=(",", ":"),
    )


def _replace_once(payload: str, old: str, new: str) -> str:
    assert payload.count(old) >= 1
    return payload.replace(old, new, 1)


def _matrix() -> list[tuple[str, str, bool]]:
    valid_one = _response(_authority(1))
    version_path = '"version":1'
    target_version_path = '"contract_version":1'
    generation_path = '"generation":1'

    cases: list[tuple[str, str, bool]] = [
        ("legacy authority absent", _response(), True),
        ("legacy authority null", _response(None), True),
        ("valid generation zero", _response(_authority(0)), True),
        ("valid generation one", valid_one, True),
        ("valid generation int64 max", _response(_authority((2**63) - 1)), True),
        ("version numeric string", _replace_once(valid_one, version_path, '"version":"1"'), False),
        ("version float", _replace_once(valid_one, version_path, '"version":1.0'), False),
        ("version bool", _replace_once(valid_one, version_path, '"version":true'), False),
        ("version missing", _replace_once(valid_one, version_path + ",", ""), False),
        ("version null", _replace_once(valid_one, version_path, '"version":null'), False),
        (
            "target version numeric string",
            _replace_once(valid_one, target_version_path, '"contract_version":"1"'),
            False,
        ),
        (
            "target version float",
            _replace_once(valid_one, target_version_path, '"contract_version":1.0'),
            False,
        ),
        (
            "target version bool",
            _replace_once(valid_one, target_version_path, '"contract_version":true'),
            False,
        ),
        (
            "target version missing",
            _replace_once(valid_one, target_version_path + ",", ""),
            False,
        ),
        (
            "target version null",
            _replace_once(valid_one, target_version_path, '"contract_version":null'),
            False,
        ),
        (
            "authority generation numeric string",
            _replace_once(valid_one, generation_path, '"generation":"1"'),
            False,
        ),
        (
            "authority generation integral float",
            _replace_once(valid_one, generation_path, '"generation":1.0'),
            False,
        ),
        (
            "authority generation fractional float",
            _replace_once(valid_one, generation_path, '"generation":1.5'),
            False,
        ),
        (
            "authority generation bool",
            _replace_once(valid_one, generation_path, '"generation":true'),
            False,
        ),
        (
            "authority generation missing",
            _replace_once(valid_one, generation_path + ",", ""),
            False,
        ),
        (
            "authority generation null",
            _replace_once(valid_one, generation_path, '"generation":null'),
            False,
        ),
        (
            "authority generation negative",
            _replace_once(valid_one, generation_path, '"generation":-1'),
            False,
        ),
        (
            "authority generation overflow",
            _replace_once(valid_one, generation_path, f'"generation":{2**63}'),
            False,
        ),
        (
            "target generation numeric string",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":"1","producer_object_id"',
            ),
            False,
        ),
        (
            "target generation integral float",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":1.0,"producer_object_id"',
            ),
            False,
        ),
        (
            "target generation fractional float",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":1.5,"producer_object_id"',
            ),
            False,
        ),
        (
            "target generation bool",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":true,"producer_object_id"',
            ),
            False,
        ),
        (
            "target generation missing",
            _replace_once(valid_one, '"generation":1,"producer_object_id"', '"producer_object_id"'),
            False,
        ),
        (
            "target generation null",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":null,"producer_object_id"',
            ),
            False,
        ),
        (
            "target generation negative",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":-1,"producer_object_id"',
            ),
            False,
        ),
        (
            "target generation overflow",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                f'"generation":{2**63},"producer_object_id"',
            ),
            False,
        ),
        (
            "generation mismatch",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":2,"producer_object_id"',
            ),
            False,
        ),
        (
            "duplicate authority generation",
            _replace_once(valid_one, generation_path, '"generation":1,"generation":1'),
            False,
        ),
        (
            "duplicate target generation",
            _replace_once(
                valid_one,
                '"generation":1,"producer_object_id"',
                '"generation":1,"generation":1,"producer_object_id"',
            ),
            False,
        ),
        (
            "duplicate authority version",
            _replace_once(valid_one, version_path, '"version":1,"version":1'),
            False,
        ),
        (
            "duplicate target contract version",
            _replace_once(
                valid_one,
                target_version_path,
                '"contract_version":1,"contract_version":1',
            ),
            False,
        ),
        (
            "initial generation missing cannot become zero",
            _replace_once(
                _response(_authority(0)),
                '"generation":0,',
                "",
            ),
            False,
        ),
        (
            "duplicate noncritical authority member",
            _replace_once(
                valid_one,
                '"authority_policy_id":"policy-1"',
                '"authority_policy_id":"policy-1","authority_policy_id":"policy-1"',
            ),
            False,
        ),
        (
            "duplicate action member is rejected",
            _replace_once(
                valid_one,
                '"accion":',
                '"accion":null,"accion":',
            ),
            False,
        ),
        (
            "unrelated duplicate remains outside B5",
            '{"status":"ok","status":"ok","accion":null}',
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
        netstandard = (
            mono_root / "lib" / "mono" / "4.5" / "Facades" / "netstandard.dll"
        )
        newtonsoft = editor / "Editor" / "Data" / "Managed" / "Newtonsoft.Json.dll"
        if all(path.exists() for path in (mono, compiler, netstandard, newtonsoft)):
            return mono, compiler, netstandard, newtonsoft
    return None


def test_actual_newtonsoft_boundary_matrix() -> None:
    """Compila la clase pura con el Newtonsoft instalado; no abre Unity."""

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
        f"AuthorityWireBoundary.{uuid4().hex}.dll"
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
    $actual = [AuthorityWireBoundary]::TryValidateResponse($raw)
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


def test_boundary_runs_before_typed_response_deserialization() -> None:
    validation = API_CLIENT.index("AuthorityWireBoundary.TryValidateResponse(responseBody)")
    deserialization = API_CLIENT.index(
        "JsonConvert.DeserializeObject<RespuestaAPI>(responseBody)"
    )

    assert validation < deserialization
    guarded_branch = API_CLIENT.split(
        "if (AuthorityWireBoundary.TryValidateResponse(responseBody))", 1
    )[1].split("if (respuesta == null)", 1)[0]
    failure_branch = API_CLIENT.split("if (respuesta == null)", 1)[1].split(
        "else if", 1
    )[0]
    assert '"INVALID_RESPONSE"' in failure_branch
    assert "onFailure?.Invoke" in failure_branch
    assert "onSuccess" not in failure_branch
    assert "JsonConvert.DeserializeObject<RespuestaAPI>" in guarded_branch


def test_boundary_rejects_before_the_agent_execution_path_exists() -> None:
    assert "LazzaroAgent" not in BOUNDARY
    assert "ProcesarRespuestaDelCerebro" not in BOUNDARY
    assert "EncolarInicioEjecucion" not in BOUNDARY
    assert "ExactTargetResolver" not in BOUNDARY
    assert "JsonConvert.DeserializeObject<RespuestaAPI>" not in BOUNDARY


def test_duplicate_policy_is_scoped_to_the_authority_action_path() -> None:
    assert "DuplicatePropertyNameHandling" not in BOUNDARY
    assert "ObjectScope.Authority" in BOUNDARY
    assert "ObjectScope.Target" in BOUNDARY
    assert 'EqualsWireName(property, "accion")' in BOUNDARY
    assert 'EqualsWireName(property, "perception_authority")' in BOUNDARY
    assert "new HashSet<string>(StringComparer.OrdinalIgnoreCase)" in BOUNDARY


def test_original_json_integer_tokens_are_the_only_authority_source() -> None:
    assert "reader.TokenType != JsonToken.Integer" in BOUNDARY
    assert "Convert.ToInt64(reader.Value, CultureInfo.InvariantCulture)" in BOUNDARY
    assert "authorityGeneration == targetGeneration" in BOUNDARY
    assert "JsonTextReader" in BOUNDARY


def test_unity_still_does_not_recompute_action_binding_sha256() -> None:
    agent = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
    method = agent.split("private bool TryResolverObjetivoAutorizado(", 1)[1].split(
        "private static void AgregarDetallesExtra(", 1
    )[0]

    assert "action_binding_sha256" in method
    assert "string.IsNullOrWhiteSpace(authority.action_binding_sha256)" in method
    assert "SHA256" not in method
    assert "ComputeHash" not in method
