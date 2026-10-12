"""ROOT-038 B10: strict raw PublicErrorEnvelope facade."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import pytest

from api.errors import build_public_error_envelope
from api.schemas import PublicErrorCode
from unity_client.test_root_038_response_envelope_boundary import (
    _unity_csharp_toolchain,
)


ROOT = Path(__file__).resolve().parents[1]
UNITY = ROOT / "unity_client"
API_CLIENT = (UNITY / "ApiClient.cs").read_text(encoding="utf-8-sig")


def _wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _matrix() -> list[dict[str, object]]:
    canonical = build_public_error_envelope(
        PublicErrorCode.INTERNAL_ERROR,
        correlation_id="00000000-0000-4000-8000-000000000001",
    ).model_dump(mode="json")
    pending = build_public_error_envelope(
        PublicErrorCode.RECONCILIATION_REQUIRED,
        correlation_id="00000000-0000-4000-8000-000000000002",
    ).model_dump(mode="json")
    cases: list[tuple[str, object, bool]] = [
        ("canonical_error", canonical, True),
        ("canonical_pending", pending, True),
        ("wrong_case", {"Version": 1, **{k: v for k, v in canonical.items() if k != "version"}}, False),
        ("extra_root", {**canonical, "future": 1}, False),
        ("extra_detail", {**canonical, "error": {**canonical["error"], "future": 1}}, False),
        ("string_version", {**canonical, "version": "1"}, False),
        ("unknown_code", {**canonical, "error": {**canonical["error"], "code": "FUTURE"}}, False),
        ("bad_guid", {**canonical, "error": {**canonical["error"], "correlation_id": "7"}}, False),
        ("wrong_status", {**canonical, "status": "pendiente"}, False),
        ("message_object", {**canonical, "error": {**canonical["error"], "message": {}}}, False),
    ]
    rows = [
        {"case_id": name, "raw": _wire(payload), "expected": expected}
        for name, payload, expected in cases
    ]
    canonical_raw = _wire(canonical)
    rows.extend(
        [
            {
                "case_id": "duplicate_root",
                "raw": canonical_raw.replace('"version":1', '"version":1,"version":1'),
                "expected": False,
            },
            {
                "case_id": "case_collision_detail",
                "raw": canonical_raw.replace(
                    '"code":"INTERNAL_ERROR"',
                    '"code":"INTERNAL_ERROR","Code":"TURN_ABORTED"',
                ),
                "expected": False,
            },
        ]
    )
    return rows


HARNESS = r'''
using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;

public static class Root038PublicErrorHarness
{
    public static int Main(string[] args)
    {
        int count = 0;
        foreach (string line in File.ReadLines(args[0], new UTF8Encoding(false, true)))
        {
            if (line.Length == 0) continue;
            JObject item = JObject.Parse(line);
            PublicErrorEnvelope envelope;
            bool actual = UnityPublicErrorWireContractV1.TryParse(
                (string)item["raw"], out envelope
            );
            bool expected = (bool)item["expected"];
            if (actual != expected || (actual && envelope == null) || (!actual && envelope != null))
            {
                Console.WriteLine("FAIL|" + item["case_id"] + "|actual=" + actual);
                return 1;
            }
            count++;
        }
        Console.WriteLine("PASS|" + count);
        return 0;
    }
}
'''


def test_actual_unity_public_error_raw_contract() -> None:
    toolchain = _unity_csharp_toolchain()
    if toolchain is None:
        pytest.skip("toolchain C# de Unity no disponible")
    mono, compiler, netstandard, newtonsoft = toolchain
    nonce = uuid4().hex
    temp = Path(tempfile.gettempdir())
    harness = temp / f"Root038PublicErrorHarness.{nonce}.cs"
    matrix = temp / f"Root038PublicErrorMatrix.{nonce}.jsonl"
    executable = temp / f"Root038PublicErrorHarness.{nonce}.exe"
    harness.write_text(HARNESS, encoding="utf-8-sig")
    matrix.write_text(
        "\n".join(_wire(case) for case in _matrix()) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    command = [
        str(mono), str(compiler), "/nologo", "/target:exe",
        f"/out:{executable}", f"/reference:{newtonsoft}",
        f"/reference:{netstandard}", str(UNITY / "DTOs.cs"),
        str(UNITY / "UnityPublicErrorWireContractV1.cs"), str(harness),
    ]
    try:
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=120)
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        environment = os.environ.copy()
        environment["MONO_PATH"] = str(newtonsoft.parent)
        executed = subprocess.run(
            [str(mono), str(executable), str(matrix)],
            text=True, capture_output=True, env=environment, timeout=120,
        )
        assert executed.returncode == 0, executed.stdout + executed.stderr
        assert f"PASS|{len(_matrix())}" in executed.stdout
    finally:
        harness.unlink(missing_ok=True)
        matrix.unlink(missing_ok=True)
        executable.unlink(missing_ok=True)


def test_api_client_uses_strict_error_facade_and_local_message_authority() -> None:
    assert "UnityPublicErrorWireContractV1.TryParse(body, out envelope)" in API_CLIENT
    assert "JsonConvert.DeserializeObject<PublicErrorEnvelope>(body)" not in API_CLIENT
    assert "MensajesPublicos.TryGetValue" in API_CLIENT
    assert "error.message se ignora deliberadamente" in API_CLIENT
