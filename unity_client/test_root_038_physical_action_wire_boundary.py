"""ROOT-038 B11: paridad raw-wire del contrato de acciones fisicas."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

import pytest

from ai_engine.motor_schemas import (
    MotorActionAdapterV1,
    MotorActionContractFailureV1,
    MotorActionV1,
)


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = ROOT / "unity_client" / "PhysicalActionWireBoundaryV1.cs"
API_CLIENT_PATH = ROOT / "unity_client" / "ApiClient.cs"
AGENT_PATH = ROOT / "unity_client" / "LazzaroAgent.cs"
BOUNDARY = BOUNDARY_PATH.read_text(encoding="utf-8-sig")
API_CLIENT = API_CLIENT_PATH.read_text(encoding="utf-8-sig")
FACADE = (ROOT / "unity_client" / "UnityResponseWireContractV1.cs").read_text(
    encoding="utf-8-sig"
)
AGENT = AGENT_PATH.read_text(encoding="utf-8-sig")


def _response(action: str | None = None, parameters: object = ...) -> str:
    payload: dict[str, object] = {
        "status": "ok",
        "mensaje": "probe",
        "id_transaccion": "tx-b11",
        "decision": None,
        "timestamp_servidor": "2026-10-10T00:00:00Z",
    }
    if action is not None:
        outgoing: dict[str, object] = {
            "id_accion": "action-b11",
            "nombre": action,
            "requiere_confirmacion": False,
            "estado_ejecucion": "dispatched",
            "id_transaccion_origen": "tx-b11",
            "perception_authority": None,
        }
        if parameters is not ...:
            outgoing["parametros"] = parameters
        payload["accion"] = outgoing
    return json.dumps(payload, separators=(",", ":"), allow_nan=True)


def _replace_parameter_token(payload: str, field: str, replacement: str) -> str:
    marker = json.dumps(field) + ":"
    start = payload.index(marker) + len(marker)
    decoder = json.JSONDecoder()
    _, consumed = decoder.raw_decode(payload[start:])
    return payload[:start] + replacement + payload[start + consumed :]


def _valid_parameters() -> dict[str, dict[str, object]]:
    return {
        "navegar_a_objeto": {"nombre_objeto": "Caja", "distancia": 1.5},
        "navegar_a_posicion": {"x": 1.25, "y": -2.0, "z": 3.0},
        "alejarse_de_objeto": {"nombre_objeto": "Fuego", "distancia": 2.0},
        "girar_avatar": {"grados": -45.0},
        "interactuar_objeto_3d": {
            "nombre_objeto": "Panel",
            "tipo_accion": "tocar",
        },
        "agarrar_objeto": {"nombre_objeto": "Caja"},
        "soltar_objeto": {"fuerza_lanzamiento": 0.0},
        "aplicar_fuerza": {
            "nombre_objeto": "Caja",
            "vector_x": 1.0,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 5.0,
        },
    }


def _matrix() -> list[tuple[str, str, bool]]:
    valid = _valid_parameters()
    cases: list[tuple[str, str, bool]] = [
        ("action absent", _response(), True),
        ("action null", _response(), True),
        ("nonphysical hablar excluded", _response("hablar", {"texto": "hola"}), True),
        ("cancel remains B7", _response("cancelar_tarea", {}), True),
    ]
    cases.extend(
        (f"canonical {action}", _response(action, parameters), True)
        for action, parameters in valid.items()
    )

    cases.extend(
        [
            (
                "navigate object distance missing",
                _response("navegar_a_objeto", {"nombre_objeto": "Caja"}),
                True,
            ),
            (
                "navigate object distance null canonicalizes as absent",
                _response(
                    "navegar_a_objeto",
                    {"nombre_objeto": "Caja", "distancia": None},
                ),
                True,
            ),
            (
                "navigate object lower bound",
                _response(
                    "navegar_a_objeto",
                    {"nombre_objeto": "Caja", "distancia": 0.1},
                ),
                True,
            ),
            (
                "navigate object upper bound",
                _response(
                    "navegar_a_objeto",
                    {"nombre_objeto": "Caja", "distancia": 10.0},
                ),
                True,
            ),
            (
                "release force missing",
                _response("soltar_objeto", {}),
                True,
            ),
            (
                "release force null",
                _response("soltar_objeto", {"fuerza_lanzamiento": None}),
                False,
            ),
            (
                "release force upper bound",
                _response("soltar_objeto", {"fuerza_lanzamiento": 20.0}),
                True,
            ),
            (
                "move away distance missing",
                _response("alejarse_de_objeto", {"nombre_objeto": "Fuego"}),
                True,
            ),
            (
                "move away distance null",
                _response(
                    "alejarse_de_objeto",
                    {"nombre_objeto": "Fuego", "distancia": None},
                ),
                False,
            ),
            (
                "move away lower bound",
                _response(
                    "alejarse_de_objeto",
                    {"nombre_objeto": "Fuego", "distancia": 0.5},
                ),
                True,
            ),
            (
                "move away upper bound",
                _response(
                    "alejarse_de_objeto",
                    {"nombre_objeto": "Fuego", "distancia": 5.0},
                ),
                True,
            ),
            (
                "rotate lower bound",
                _response("girar_avatar", {"grados": -360.0}),
                True,
            ),
            (
                "rotate upper bound",
                _response("girar_avatar", {"grados": 360.0}),
                True,
            ),
            (
                "force magnitude upper bound",
                _response(
                    "aplicar_fuerza",
                    {
                        "nombre_objeto": "Caja",
                        "vector_x": 2.0,
                        "vector_y": 0.0,
                        "vector_z": 0.0,
                        "fuerza": 100.0,
                    },
                ),
                True,
            ),
            (
                "finite double max position remains Python-valid",
                _response(
                    "navegar_a_posicion",
                    {"x": sys.float_info.max, "y": 0.0, "z": 0.0},
                ),
                True,
            ),
            (
                "tiny positive force remains Python-valid",
                _response(
                    "aplicar_fuerza",
                    {
                        "nombre_objeto": "Caja",
                        "vector_x": 1.0,
                        "vector_y": 0.0,
                        "vector_z": 0.0,
                        "fuerza": float.fromhex("0x0.0000000000001p-1022"),
                    },
                ),
                True,
            ),
        ]
    )

    required_fields = {
        "navegar_a_objeto": ("nombre_objeto",),
        "navegar_a_posicion": ("x", "y", "z"),
        "alejarse_de_objeto": ("nombre_objeto",),
        "girar_avatar": ("grados",),
        "interactuar_objeto_3d": ("nombre_objeto", "tipo_accion"),
        "agarrar_objeto": ("nombre_objeto",),
        "aplicar_fuerza": (
            "nombre_objeto",
            "vector_x",
            "vector_y",
            "vector_z",
            "fuerza",
        ),
    }
    for action, parameters in valid.items():
        for field in required_fields.get(action, ()):
            missing = dict(parameters)
            missing.pop(field)
            cases.append(
                (
                    f"required field missing {action}.{field}",
                    _response(action, missing),
                    False,
                )
            )
        extra = dict(parameters)
        extra["extra"] = 1
        cases.append((f"extra field {action}", _response(action, extra), False))

    for interaction_type in (
        "mirar",
        "tocar",
        "inspeccionar",
        "golpear",
        "usar",
        "activar",
    ):
        cases.append(
            (
                f"canonical interaction {interaction_type}",
                _response(
                    "interactuar_objeto_3d",
                    {"nombre_objeto": "Panel", "tipo_accion": interaction_type},
                ),
                True,
            )
        )

    for action, parameters in valid.items():
        for field, value in parameters.items():
            if isinstance(value, float):
                string_value = dict(parameters)
                string_value[field] = str(value)
                cases.append(
                    (f"numeric string {action}.{field}", _response(action, string_value), False)
                )

    navigate_strings = _response(
        "navegar_a_posicion",
        {"x": "1.25", "y": "2", "z": "3"},
    )
    force_strings = _response(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": "1",
            "vector_y": "0",
            "vector_z": "0",
            "fuerza": "5",
        },
    )
    cases.extend(
        [
            ("mandatory navigate numeric strings", navigate_strings, False),
            ("mandatory force numeric strings", force_strings, False),
            (
                "boolean numeric",
                _response("navegar_a_posicion", {"x": True, "y": 0, "z": 0}),
                False,
            ),
            (
                "null required numeric",
                _response("navegar_a_posicion", {"x": None, "y": 0, "z": 0}),
                False,
            ),
            (
                "object numeric",
                _response("navegar_a_posicion", {"x": {}, "y": 0, "z": 0}),
                False,
            ),
            (
                "array numeric",
                _response("navegar_a_posicion", {"x": [], "y": 0, "z": 0}),
                False,
            ),
            (
                "object name number",
                _response("agarrar_objeto", {"nombre_objeto": 7}),
                False,
            ),
            (
                "object name boolean",
                _response("agarrar_objeto", {"nombre_objeto": True}),
                False,
            ),
            (
                "interaction type number",
                _response(
                    "interactuar_objeto_3d",
                    {"nombre_objeto": "Panel", "tipo_accion": 1},
                ),
                False,
            ),
            (
                "interaction type boolean",
                _response(
                    "interactuar_objeto_3d",
                    {"nombre_objeto": "Panel", "tipo_accion": False},
                ),
                False,
            ),
            (
                "interaction type wrong case",
                _response(
                    "interactuar_objeto_3d",
                    {"nombre_objeto": "Panel", "tipo_accion": "TOCAR"},
                ),
                False,
            ),
            (
                "wrong-case parameter field",
                _response("agarrar_objeto", {"Nombre_objeto": "Caja"}),
                False,
            ),
            (
                "navigate distance below range",
                _response(
                    "navegar_a_objeto",
                    {"nombre_objeto": "Caja", "distancia": 0.099},
                ),
                False,
            ),
            (
                "move distance above range",
                _response(
                    "alejarse_de_objeto",
                    {"nombre_objeto": "Fuego", "distancia": 5.001},
                ),
                False,
            ),
            ("rotation above range", _response("girar_avatar", {"grados": 360.1}), False),
            (
                "release below range",
                _response("soltar_objeto", {"fuerza_lanzamiento": -0.1}),
                False,
            ),
            (
                "force zero magnitude",
                _response(
                    "aplicar_fuerza",
                    {
                        "nombre_objeto": "Caja",
                        "vector_x": 1.0,
                        "vector_y": 0.0,
                        "vector_z": 0.0,
                        "fuerza": 0.0,
                    },
                ),
                False,
            ),
            (
                "force zero direction",
                _response(
                    "aplicar_fuerza",
                    {
                        "nombre_objeto": "Caja",
                        "vector_x": -0.0,
                        "vector_y": 0.0,
                        "vector_z": -0.0,
                        "fuerza": 1.0,
                    },
                ),
                False,
            ),
        ]
    )

    nonfinite = _response("navegar_a_posicion", {"x": 1.0, "y": 0.0, "z": 0.0})
    cases.extend(
        [
            ("NaN numeric", _replace_parameter_token(nonfinite, "x", "NaN"), False),
            ("Infinity numeric", _replace_parameter_token(nonfinite, "x", "Infinity"), False),
            ("negative Infinity numeric", _replace_parameter_token(nonfinite, "x", "-Infinity"), False),
        ]
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


def test_actual_unity_newtonsoft_physical_parameter_matrix() -> None:
    """Compila B11 con el Mono/Newtonsoft de Unity, sin abrir Unity."""

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
        f"PhysicalActionWireBoundaryV1.{uuid4().hex}.dll"
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
    $actual = [PhysicalActionWireBoundaryV1]::TryValidateResponse($raw)
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


def test_python_missing_null_and_default_source_of_truth() -> None:
    cases = [
        ("navegar_a_objeto", {"nombre_objeto": "Caja"}, {"nombre_objeto": "Caja"}),
        (
            "navegar_a_objeto",
            {"nombre_objeto": "Caja", "distancia": None},
            {"nombre_objeto": "Caja"},
        ),
        (
            "navegar_a_objeto",
            {"nombre_objeto": "Caja", "distancia": 1.5},
            {"nombre_objeto": "Caja", "distancia": 1.5},
        ),
        ("soltar_objeto", {}, {"fuerza_lanzamiento": 0.0}),
        ("soltar_objeto", {"fuerza_lanzamiento": 1.5}, {"fuerza_lanzamiento": 1.5}),
        (
            "alejarse_de_objeto",
            {"nombre_objeto": "Caja"},
            {"nombre_objeto": "Caja", "distancia": 2.0},
        ),
        (
            "alejarse_de_objeto",
            {"nombre_objeto": "Caja", "distancia": 1.5},
            {"nombre_objeto": "Caja", "distancia": 1.5},
        ),
    ]
    for action, raw, expected in cases:
        parsed = MotorActionAdapterV1.parse(action, raw)
        assert isinstance(parsed, MotorActionV1)
        assert parsed.parameters_dict() == expected

    for action, raw in (
        ("soltar_objeto", {"fuerza_lanzamiento": None}),
        ("alejarse_de_objeto", {"nombre_objeto": "Caja", "distancia": None}),
    ):
        rejected = MotorActionAdapterV1.parse(action, raw)
        assert isinstance(rejected, MotorActionContractFailureV1)


def test_b11_runs_in_the_approved_pipeline_position() -> None:
    b9 = FACADE.index("ResponseDuplicateWireBoundary.TryValidateResponse(rawJson)")
    b7 = FACADE.index("ActionEnvelopeWireBoundary.TryValidateResponse(rawJson)")
    b11 = FACADE.index("PhysicalActionWireBoundaryV1.TryValidateResponse(rawJson)")
    b5 = FACADE.index("AuthorityWireBoundary.TryValidateResponse(rawJson)")
    typed = FACADE.index("JsonConvert.DeserializeObject<RespuestaAPI>(rawJson)")
    b6 = FACADE.index("ActionBindingVerifierV1.TryVerifyResponse(response)")

    assert b9 < b7 < b11 < b5 < typed < b6


def test_failure_uses_invalid_response_before_any_success_or_physical_path() -> None:
    guarded = FACADE.split(
        "PhysicalActionWireBoundaryV1.TryValidateResponse(rawJson)", 1
    )[1]
    failure = API_CLIENT.split("if (respuesta == null)", 1)[1].split("else if", 1)[0]

    assert "JsonConvert.DeserializeObject<RespuestaAPI>" in guarded
    assert '"INVALID_RESPONSE"' in failure
    assert "onSuccess" not in failure
    for forbidden in (
        "ProcesarRespuestaDelCerebro",
        "EncolarInicioEjecucion",
        "NavegarAPosicion",
        "ApplyForce",
        "TryRelease",
    ):
        assert forbidden not in BOUNDARY


def test_handlers_no_longer_coerce_numeric_strings() -> None:
    parser = AGENT.split(
        "private static bool TryLeerFloat(", 1
    )[1].split("private static bool TryLeerFuerzaLanzamiento", 1)[0]
    move_away = AGENT.split(
        "private void EjecutarAlejarseDeObjeto(", 1
    )[1].split("private void EjecutarGirarAvatar", 1)[0]
    navigate_distance = AGENT.split(
        "private static bool TryLeerDistanciaNavegacionObjeto(", 1
    )[1].split("private void EjecutarInteraccion", 1)[0]

    assert "token.Type != JTokenType.Integer" in parser
    assert "token.Type != JTokenType.Float" in parser
    assert "token.Value<double>()" in parser
    assert "float.TryParse" not in parser
    assert 'TryGetValue("distancia"' in move_away
    assert 'TryLeerFloat(parametros, "distancia"' in move_away
    assert "Value<float>()" not in move_away
    assert "token.Type == JTokenType.Null" in navigate_distance
    assert navigate_distance.index("token.Type == JTokenType.Null") < navigate_distance.index(
        "token.Type != JTokenType.Integer"
    )


def test_contract_surface_is_narrow_and_canonical() -> None:
    for action in _valid_parameters():
        assert f'case "{action}":' in BOUNDARY
    assert 'case "cancelar_tarea":' not in BOUNDARY
    assert 'case "hablar":' not in BOUNDARY
    assert "StringComparer.Ordinal" in BOUNDARY
    assert "StringComparison.OrdinalIgnoreCase" in BOUNDARY
    assert "double.IsNaN" in BOUNDARY
    assert "double.IsInfinity" in BOUNDARY
