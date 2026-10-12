"""ROOT-038 final: version dispatch and strict Unity response facade."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import pytest

from api.schemas import AccionSaliente, DecisionPublica, RespuestaAPI
from ai_engine.decision_outcome import DecisionDisposition, DecisionReasonCode
from core.test_root_038_action_binding_vectors import build_vectors
from unity_client.test_root_038_response_envelope_boundary import (
    _unity_csharp_toolchain,
)


ROOT = Path(__file__).resolve().parents[1]
UNITY = ROOT / "unity_client"
FACADE = UNITY / "UnityResponseWireContractV1.cs"
API_CLIENT = (UNITY / "ApiClient.cs").read_text(encoding="utf-8-sig")


def _wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _base_v1() -> dict[str, object]:
    return RespuestaAPI(
        status="ok",
        mensaje="ok",
        id_transaccion="tx-1",
    ).model_dump(mode="json")


def _action(name: str, parameters: dict[str, object]) -> dict[str, object]:
    return AccionSaliente(
        id_accion=f"action:{name}",
        nombre=name,
        parametros=parameters,
        estado_ejecucion="dispatched",
        id_transaccion_origen="tx-1",
    ).model_dump(mode="json")


def _authorized_action(vector: dict[str, object]) -> dict[str, object]:
    action = deepcopy(vector["action"])
    action.update(
        {
            "requiere_confirmacion": False,
            "estado_ejecucion": "dispatched",
            "id_transaccion_origen": "tx-1",
            "parametros_descripcion": [],
            "perception_authority": deepcopy(vector["authority_reference"]),
        }
    )
    return action


def _cases() -> list[dict[str, object]]:
    base = _base_v1()
    v0 = deepcopy(base)
    v0.pop("wire_version")

    v0_case = deepcopy(v0)
    v0_case["Status"] = v0_case.pop("status")

    v1_wrong_case = deepcopy(base)
    v1_wrong_case["Status"] = v1_wrong_case.pop("status")

    valid_position = deepcopy(base)
    valid_position["accion"] = _action(
        "navegar_a_posicion",
        {"x": 1.0, "y": -2.0, "z": 3.5},
    )
    v0_action_wrong_case = deepcopy(valid_position)
    v0_action_wrong_case.pop("wire_version")
    v0_action_wrong_case["accion"]["Nombre"] = v0_action_wrong_case["accion"].pop(
        "nombre"
    )
    v1_action_wrong_case = deepcopy(valid_position)
    v1_action_wrong_case["accion"]["Nombre"] = v1_action_wrong_case["accion"].pop(
        "nombre"
    )

    legacy_force = deepcopy(v0)
    legacy_force["accion"] = _action(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": 2.0,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 1.0,
        },
    )
    v1_nonunit = deepcopy(legacy_force)
    v1_nonunit["wire_version"] = 1

    force_vector = next(
        vector
        for vector in build_vectors()
        if vector["case_id"] == "apply_force_zero_components"
    )
    valid_authorized = deepcopy(base)
    valid_authorized["accion"] = _authorized_action(force_vector)

    decision = deepcopy(base)
    decision["decision"] = DecisionPublica(
        disposition=DecisionDisposition.ACTION,
        reason_code=DecisionReasonCode.PRIMARY_VALID,
    ).model_dump(mode="json")

    missing_authority = deepcopy(base)
    missing_authority["accion"] = _action(
        "navegar_a_objeto", {"nombre_objeto": "Caja", "distancia": 1.0}
    )

    overflow = deepcopy(valid_position)
    overflow["accion"]["parametros"]["x"] = 1.0e100
    underflow = deepcopy(valid_position)
    underflow["accion"]["parametros"]["x"] = 1.0e-100
    signed_zero = deepcopy(valid_position)
    signed_zero["accion"]["parametros"]["x"] = -0.0

    wrong_version = deepcopy(base)
    wrong_version["wire_version"] = 2
    string_version = deepcopy(base)
    string_version["wire_version"] = "1"
    missing_required = deepcopy(base)
    missing_required.pop("mensaje")
    extra = {**base, "future": 1}
    invalid_decision = deepcopy(decision)
    invalid_decision["decision"]["disposition"] = "future"

    cases = [
        ("valid_v0", v0, True, True),
        ("valid_v1", base, True, False),
        ("v0_wrong_case", v0_case, True, True),
        ("v1_wrong_case", v1_wrong_case, False, False),
        ("valid_position", valid_position, True, False),
        ("v0_action_wrong_case", v0_action_wrong_case, True, True),
        ("v1_action_wrong_case", v1_action_wrong_case, False, False),
        ("valid_authorized_force", valid_authorized, True, False),
        ("v0_nonunit_force", legacy_force, True, True),
        ("v1_nonunit_force", v1_nonunit, False, False),
        ("valid_decision", decision, True, False),
        ("invalid_decision_enum", invalid_decision, False, False),
        ("v1_missing_authority", missing_authority, False, False),
        ("float_overflow", overflow, False, False),
        ("float_underflow", underflow, False, False),
        ("signed_zero", signed_zero, False, False),
        ("unknown_version", wrong_version, False, False),
        ("malformed_version", string_version, False, False),
        ("v1_missing_required", missing_required, False, False),
        ("v1_extra", extra, False, False),
    ]
    rows = [
        {"case_id": name, "raw": _wire(payload), "expected": expected, "legacy": legacy}
        for name, payload, expected, legacy in cases
    ]
    rows.extend(
        [
            {
                "case_id": "decision_duplicate",
                "raw": _wire(base).replace(
                    '"decision":null',
                    '"decision":{"version":1,"version":1,"disposition":"action","reason_code":"primary_valid"}',
                ),
                "expected": False,
                "legacy": False,
            },
            {
                "case_id": "invalid_v1_never_downgrades",
                "raw": _wire(base).replace('"status":"ok"', '"Status":"ok"'),
                "expected": False,
                "legacy": False,
            },
        ]
    )
    for vector in build_vectors():
        payload = deepcopy(base)
        payload["accion"] = _authorized_action(vector)
        expected = vector["case_id"] not in {
            "position_double_min_subnormal",
            "position_double_max_finite",
        }
        rows.append(
            {
                "case_id": f"physical_golden_{vector['case_id']}",
                "raw": _wire(payload),
                "expected": expected,
                "legacy": False,
            }
        )
    return rows


HARNESS = r'''
using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;

public static class Root038V1FacadeHarness
{
    public static int Main(string[] args)
    {
        int count = 0;
        foreach (string line in File.ReadLines(args[0], new UTF8Encoding(false, true)))
        {
            if (line.Length == 0) continue;
            JObject item = JObject.Parse(line);
            string raw = (string)item["raw"];
            bool expected = (bool)item["expected"];
            bool expectedLegacy = (bool)item["legacy"];
            UnityResponseWireContractResultV1 result;
            bool actual = UnityResponseWireContractV1.TryParse(raw, out result);
            if (actual != expected)
            {
                Console.WriteLine("FAIL|" + item["case_id"] + "|actual=" + actual);
                return 1;
            }
            if (actual)
            {
                if (!result.success || result.typed_response == null || result.legacy_mode != expectedLegacy)
                {
                    Console.WriteLine("FAIL_RESULT|" + item["case_id"]);
                    return 1;
                }
            }
            else if (result == null || result.typed_response != null || result.success)
            {
                Console.WriteLine("FAIL_PARTIAL|" + item["case_id"]);
                return 1;
            }
            count++;
        }
        Console.WriteLine("PASS|" + count);
        return 0;
    }
}
'''


def test_actual_unity_facade_v0_v1_and_downgrade_matrix() -> None:
    toolchain = _unity_csharp_toolchain()
    if toolchain is None:
        pytest.skip("toolchain C# de Unity no disponible")
    mono, compiler, netstandard, newtonsoft = toolchain
    nonce = uuid4().hex
    temp = Path(tempfile.gettempdir())
    harness = temp / f"Root038V1FacadeHarness.{nonce}.cs"
    matrix = temp / f"Root038V1FacadeMatrix.{nonce}.jsonl"
    executable = temp / f"Root038V1FacadeHarness.{nonce}.exe"
    harness.write_text(HARNESS, encoding="utf-8-sig")
    matrix.write_text(
        "\n".join(_wire(case) for case in _cases()) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    sources = [
        "DTOs.cs",
        "ResponseDuplicateWireBoundary.cs",
        "ResponseEnvelopeWireBoundaryV1.cs",
        "ActionEnvelopeWireBoundary.cs",
        "PhysicalActionWireBoundaryV1.cs",
        "AuthorityWireBoundary.cs",
        "ActionBindingVerifierV1.cs",
        "UnityResponseWireContractV1.cs",
    ]
    command = [
        str(mono),
        str(compiler),
        "/nologo",
        "/target:exe",
        f"/out:{executable}",
        f"/reference:{newtonsoft}",
        f"/reference:{netstandard}",
        *(str(UNITY / source) for source in sources),
        str(harness),
    ]
    try:
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=120)
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        environment = os.environ.copy()
        environment["MONO_PATH"] = str(newtonsoft.parent)
        executed = subprocess.run(
            [str(mono), str(executable), str(matrix)],
            text=True,
            capture_output=True,
            env=environment,
            timeout=120,
        )
        assert executed.returncode == 0, executed.stdout + executed.stderr
        assert f"PASS|{len(_cases())}" in executed.stdout
    finally:
        harness.unlink(missing_ok=True)
        matrix.unlink(missing_ok=True)
        executable.unlink(missing_ok=True)


def test_api_client_delegates_raw_ordering_to_facade() -> None:
    assert "UnityResponseWireContractV1.TryParse(responseBody" in API_CLIENT
    for detail in (
        "ResponseDuplicateWireBoundary.TryValidateResponse(responseBody)",
        "ResponseEnvelopeWireBoundaryV1.TryValidateResponse(responseBody)",
        "ActionEnvelopeWireBoundary.TryValidateResponse(responseBody)",
        "PhysicalActionWireBoundaryV1.TryValidateResponse(responseBody)",
        "AuthorityWireBoundary.TryValidateResponse(responseBody)",
    ):
        assert detail not in API_CLIENT


def test_python_response_is_single_strict_v1_writer() -> None:
    payload = _base_v1()
    assert payload["wire_version"] == 1
    assert list(payload) == [
        "wire_version",
        "status",
        "mensaje",
        "id_transaccion",
        "accion",
        "decision",
        "timestamp_servidor",
    ]
