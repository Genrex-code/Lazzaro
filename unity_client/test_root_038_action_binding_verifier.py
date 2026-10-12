"""Executable Unity-toolchain tests for ROOT-038 B6 binding verification."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "core" / "fixtures" / "root_038_action_binding_v1.jsonl"
VERIFIER = ROOT / "unity_client" / "ActionBindingVerifierV1.cs"
DTOS = ROOT / "unity_client" / "DTOs.cs"
API_CLIENT = (ROOT / "unity_client" / "ApiClient.cs").read_text(
    encoding="utf-8-sig"
)
VERIFIER_SOURCE = VERIFIER.read_text(encoding="utf-8-sig")
FACADE = (ROOT / "unity_client" / "UnityResponseWireContractV1.cs").read_text(
    encoding="utf-8-sig"
)


def _load_vectors() -> list[dict]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").split("\n")
        if line
    ]


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


def _authorized_action(vector: dict) -> dict:
    action = deepcopy(vector["action"])
    action["perception_authority"] = deepcopy(vector["authority_reference"])
    return action


def _mutation_matrix() -> list[dict]:
    vector = next(
        item for item in _load_vectors() if item["case_id"] == "apply_force_zero_components"
    )
    original = _authorized_action(vector)
    cases: list[dict] = []

    def mutate(name: str, operation) -> None:
        action = deepcopy(original)
        operation(action)
        cases.append({"case_id": name, "action": action})

    mutate("action_id", lambda action: action.__setitem__("id_accion", "action:mutated"))
    mutate("authority_action_id", lambda action: action["perception_authority"].__setitem__("id_accion", "action:mutated"))
    mutate("action_name", lambda action: action.__setitem__("nombre", "agarrar_objeto"))
    mutate("authority_action_name", lambda action: action["perception_authority"].__setitem__("action_name", "agarrar_objeto"))
    mutate("wire_parameters", lambda action: action["parametros"].__setitem__("fuerza", 100.0))
    mutate(
        "canonical_parameters",
        lambda action: action["perception_authority"].__setitem__(
            "canonical_parameters_json",
            action["perception_authority"]["canonical_parameters_json"].replace(
                '"fuerza":1.0', '"fuerza":100.0'
            ),
        ),
    )

    def mutate_both_parameters(action: dict) -> None:
        action["parametros"]["fuerza"] = 100.0
        action["perception_authority"]["canonical_parameters_json"] = action[
            "perception_authority"
        ]["canonical_parameters_json"].replace('"fuerza":1.0', '"fuerza":100.0')

    mutate("force_1_to_100_exploit", mutate_both_parameters)
    mutate(
        "target_identity",
        lambda action: action["perception_authority"]["target"].__setitem__(
            "runtime_object_id", "runtime:attacker-target"
        ),
    )

    def mutate_generation(action: dict) -> None:
        action["perception_authority"]["generation"] = 19
        action["perception_authority"]["target"]["generation"] = 19

    mutate("target_generation", mutate_generation)
    mutate(
        "authority_policy_id",
        lambda action: action["perception_authority"].__setitem__(
            "authority_policy_id", "root-029-authority-v2"
        ),
    )
    mutate(
        "freshness_policy_id",
        lambda action: action["perception_authority"].__setitem__(
            "freshness_policy_id", "root-029-freshness-v2"
        ),
    )
    mutate(
        "generation_binding_mode",
        lambda action: action["perception_authority"].__setitem__(
            "generation_binding_mode", "REJECT_IF_KNOWN_SUPERSEDED"
        ),
    )
    mutate(
        "interaction_type",
        lambda action: action["perception_authority"].__setitem__(
            "interaction_type", "tocar"
        ),
    )
    mutate(
        "safety_disposition",
        lambda action: action["perception_authority"].__setitem__(
            "safety_disposition", "CAUTION"
        ),
    )
    mutate(
        "safety_reason_code",
        lambda action: action["perception_authority"].__setitem__(
            "safety_reason_code", "HAZARD_OBSERVATION"
        ),
    )
    mutate(
        "uppercase_hash",
        lambda action: action["perception_authority"].__setitem__(
            "action_binding_sha256",
            action["perception_authority"]["action_binding_sha256"].upper(),
        ),
    )
    mutate(
        "short_hash",
        lambda action: action["perception_authority"].__setitem__(
            "action_binding_sha256", "0" * 63
        ),
    )
    mutate(
        "nonhex_hash",
        lambda action: action["perception_authority"].__setitem__(
            "action_binding_sha256", "g" * 64
        ),
    )
    mutate(
        "multiple_parameter_documents",
        lambda action: action["perception_authority"].__setitem__(
            "canonical_parameters_json", "{}{}"
        ),
    )
    mutate(
        "commented_parameter_document",
        lambda action: action["perception_authority"].__setitem__(
            "canonical_parameters_json", "{/*comment*/}"
        ),
    )
    return cases


HARNESS = r'''
using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;

public static class BindingVerifierHarness
{
    private static AccionSaliente LoadAction(JObject container)
    {
        JObject actionToken = (JObject)container["action"].DeepClone();
        if (container["authority_reference"] != null)
        {
            actionToken["perception_authority"] = container["authority_reference"].DeepClone();
        }
        return actionToken.ToObject<AccionSaliente>();
    }

    public static int Main(string[] args)
    {
        int golden = 0;
        foreach (string line in File.ReadLines(args[0], new UTF8Encoding(false, true)))
        {
            if (line.Length == 0) continue;
            JObject vector = JObject.Parse(line);
            AccionSaliente action = LoadAction(vector);
            string json;
            string hex;
            string hash;
            if (!ActionBindingVerifierV1.TryReconstruct(action, out json, out hex, out hash))
            {
                Console.WriteLine("FAIL|reconstruct|" + vector["case_id"]);
                return 1;
            }
            if (!string.Equals(json, (string)vector["canonical_binding_json"], StringComparison.Ordinal)
                || !string.Equals(hex, (string)vector["canonical_binding_utf8_hex"], StringComparison.Ordinal)
                || !string.Equals(hash, (string)vector["expected_sha256"], StringComparison.Ordinal))
            {
                Console.WriteLine("FAIL|golden|" + vector["case_id"]);
                Console.WriteLine(json);
                Console.WriteLine((string)vector["canonical_binding_json"]);
                Console.WriteLine(hex);
                Console.WriteLine((string)vector["canonical_binding_utf8_hex"]);
                Console.WriteLine(hash);
                Console.WriteLine((string)vector["expected_sha256"]);
                return 1;
            }
            if (!ActionBindingVerifierV1.TryVerifyResponse(new RespuestaAPI { accion = action }))
            {
                Console.WriteLine("FAIL|valid-response|" + vector["case_id"]);
                return 1;
            }
            golden++;
        }

        int mutations = 0;
        foreach (string line in File.ReadLines(args[1], new UTF8Encoding(false, true)))
        {
            if (line.Length == 0) continue;
            JObject mutation = JObject.Parse(line);
            AccionSaliente action = mutation["action"].ToObject<AccionSaliente>();
            if (ActionBindingVerifierV1.TryVerifyResponse(new RespuestaAPI { accion = action }))
            {
                Console.WriteLine("FAIL|mutation-accepted|" + mutation["case_id"]);
                return 1;
            }
            mutations++;
        }

        AccionSaliente legacy = new AccionSaliente
        {
            id_accion = "legacy",
            nombre = "girar_avatar",
            parametros = new JObject(),
            perception_authority = null,
        };
        if (!ActionBindingVerifierV1.TryVerifyResponse(new RespuestaAPI { accion = legacy })
            || !ActionBindingVerifierV1.TryVerifyResponse(new RespuestaAPI { accion = null })
            || ActionBindingVerifierV1.TryVerifyResponse(null))
        {
            Console.WriteLine("FAIL|legacy-boundary");
            return 1;
        }

        JObject first = JObject.Parse(File.ReadAllLines(args[0], new UTF8Encoding(false, true))[0]);
        AccionSaliente unpaired = LoadAction(first);
        unpaired.perception_authority.target.producer_instance_id = new string((char)0xD800, 1);
        string ignoredJson;
        string ignoredHex;
        string ignoredHash;
        if (ActionBindingVerifierV1.TryReconstruct(
            unpaired,
            out ignoredJson,
            out ignoredHex,
            out ignoredHash
        ))
        {
            Console.WriteLine("FAIL|unpaired-surrogate");
            return 1;
        }

        Console.WriteLine("PASS|golden=" + golden + "|mutations=" + mutations);
        return 0;
    }
}
'''


def test_actual_unity_toolchain_matches_python_vectors_and_rejects_mutations() -> None:
    """Compile and run the pure verifier with Unity's installed Mono/Newtonsoft."""

    toolchain = _unity_csharp_toolchain()
    if toolchain is None:
        pytest.skip("toolchain C# de Unity no disponible para el verifier puro")
    mono, compiler, netstandard, newtonsoft = toolchain

    nonce = uuid4().hex
    temp = Path(tempfile.gettempdir())
    harness_path = temp / f"BindingVerifierHarness.{nonce}.cs"
    mutations_path = temp / f"BindingVerifierMutations.{nonce}.jsonl"
    executable_path = temp / f"BindingVerifierHarness.{nonce}.exe"
    harness_path.write_text(HARNESS, encoding="utf-8-sig")
    mutations_path.write_text(
        "\n".join(
            json.dumps(case, ensure_ascii=False, separators=(",", ":"))
            for case in _mutation_matrix()
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    compile_command = [
        str(mono),
        str(compiler),
        "/nologo",
        "/target:exe",
        f"/out:{executable_path}",
        f"/reference:{newtonsoft}",
        f"/reference:{netstandard}",
        str(DTOS),
        str(VERIFIER),
        str(harness_path),
    ]
    try:
        compiled = subprocess.run(
            compile_command,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        environment = os.environ.copy()
        environment["MONO_PATH"] = str(newtonsoft.parent)
        executed = subprocess.run(
            [str(mono), str(executable_path), str(FIXTURE), str(mutations_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
            env=environment,
        )
    finally:
        harness_path.unlink(missing_ok=True)
        mutations_path.unlink(missing_ok=True)
        executable_path.unlink(missing_ok=True)

    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert f"PASS|golden={len(_load_vectors())}|mutations={len(_mutation_matrix())}" in executed.stdout


def test_verifier_is_between_b5_boundary_and_on_success() -> None:
    b5 = FACADE.index("AuthorityWireBoundary.TryValidateResponse(rawJson)")
    typed = FACADE.index("JsonConvert.DeserializeObject<RespuestaAPI>(rawJson)")
    b6 = FACADE.index("ActionBindingVerifierV1.TryVerifyResponse(response)")
    success = API_CLIENT.index("onSuccess?.Invoke(respuesta)")
    assert b5 < typed < b6
    assert API_CLIENT.index("UnityResponseWireContractV1.TryParse") < success

    invalid_branch = API_CLIENT.split("if (respuesta == null)", 1)[1].split(
        "else if", 1
    )[0]
    assert '"INVALID_RESPONSE"' in invalid_branch
    assert "onFailure?.Invoke" in invalid_branch
    assert "onSuccess" not in invalid_branch


def test_verifier_has_no_execution_or_material_effect_path() -> None:
    for forbidden in (
        "ProcesarRespuestaDelCerebro",
        "EncolarInicioEjecucion",
        "ExactTargetResolver",
        "TryRelease",
        "Rigidbody",
        "AddForce",
    ):
        assert forbidden not in VERIFIER_SOURCE
    assert "JsonConvert.SerializeObject" not in VERIFIER_SOURCE
    assert "new UTF8Encoding(false, true)" in VERIFIER_SOURCE


def test_required_mutation_matrix_is_complete() -> None:
    assert {case["case_id"] for case in _mutation_matrix()} >= {
        "action_id",
        "action_name",
        "wire_parameters",
        "canonical_parameters",
        "force_1_to_100_exploit",
        "target_identity",
        "target_generation",
        "authority_policy_id",
        "freshness_policy_id",
        "generation_binding_mode",
        "interaction_type",
        "safety_disposition",
        "safety_reason_code",
    }
