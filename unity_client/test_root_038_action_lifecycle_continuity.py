"""ROOT-038 B15: continuidad contextual del ciclo de vida de acciones Unity."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "core" / "fixtures" / "root_038_action_binding_v1.jsonl"
DTOS = ROOT / "unity_client" / "DTOs.cs"
BOUNDARY = ROOT / "unity_client" / "ActionLifecycleContinuityV1.cs"
AGENT = ROOT / "unity_client" / "LazzaroAgent.cs"
AGENT_SOURCE = AGENT.read_text(encoding="utf-8-sig")


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


def _fixture_authority() -> dict[str, object]:
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        vector = json.loads(line)
        if vector["authority_reference"] is not None:
            return vector["authority_reference"]
    raise AssertionError("ROOT-038 fixture has no authority reference")


def _request(
    transaction: str,
    event_type: str = "comando_usuario",
    action_id: object = "A",
) -> dict[str, object]:
    details: dict[str, object] = {}
    if event_type == "confirmacion_accion":
        details["id_accion"] = action_id
    return {
        "tipo_evento": event_type,
        "id_transaccion": transaction,
        "detalles": details,
    }


def _action(
    *,
    origin: object = "tx-original",
    action_id: str = "A",
    name: str = "girar_avatar",
    parameters: dict[str, object] | None = None,
    authority: dict[str, object] | None = None,
    state: str = "dispatched",
    include_origin: bool = True,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id_accion": action_id,
        "nombre": name,
        "parametros": parameters if parameters is not None else {"grados": 90},
        "estado_ejecucion": state,
        "perception_authority": authority,
    }
    if include_origin:
        result["id_transaccion_origen"] = origin
    return result


def _new_case(
    case_id: str,
    action: dict[str, object],
    expected: bool,
    canonical_origin: str | None,
    event_type: str = "comando_usuario",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "mode": "new",
        "request": _request("tx-current", event_type),
        "response_action": action,
        "expected": expected,
        "canonical_origin": canonical_origin,
    }


def _confirmation_case(
    case_id: str,
    response_action: dict[str, object],
    expected: bool,
    *,
    requested_action_id: object = "A",
    pending_action_id: str = "A",
    pending_origin: str = "tx-original",
    pending_name: str = "girar_avatar",
    pending_parameters: dict[str, object] | None = None,
    pending_authority: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "mode": "confirmation",
        "request": _request(
            "tx-confirm", "confirmacion_accion", requested_action_id
        ),
        "response_action": response_action,
        "pending_action_id": pending_action_id,
        "pending_origin": pending_origin,
        "pending_name": pending_name,
        "pending_parameters": (
            pending_parameters if pending_parameters is not None else {"grados": 90}
        ),
        "pending_authority": pending_authority,
        "expected": expected,
    }


def _matrix() -> list[dict[str, object]]:
    authority = _fixture_authority()
    new_action = _action(
        origin="tx-current",
        action_id="new-A",
        name="navegar_a_posicion",
        parameters={"x": 1.0, "y": 2.0, "z": 3.0},
        authority=None,
    )
    cases = [
        _new_case("immediate explicit matching origin", deepcopy(new_action), True, "tx-current"),
        _new_case(
            "pending initial action",
            {**deepcopy(new_action), "estado_ejecucion": "awaiting_authorization"},
            True,
            "tx-current",
        ),
        _new_case(
            "new action origin missing",
            _action(
                action_id="new-A",
                name="navegar_a_posicion",
                parameters={"x": 1.0, "y": 2.0, "z": 3.0},
                authority=None,
                include_origin=False,
            ),
            True,
            "tx-current",
        ),
        _new_case("new action origin null", {**deepcopy(new_action), "id_transaccion_origen": None}, True, "tx-current"),
        _new_case("new action origin empty", {**deepcopy(new_action), "id_transaccion_origen": ""}, True, "tx-current"),
        _new_case("new action origin whitespace", {**deepcopy(new_action), "id_transaccion_origen": "   "}, True, "tx-current"),
        _new_case(
            "B15 tx-other exploit",
            {**deepcopy(new_action), "id_transaccion_origen": "tx-other"},
            False,
            None,
        ),
        _new_case(
            "result event creates a new action owned by current request",
            deepcopy(new_action),
            True,
            "tx-current",
            "resultado_accion",
        ),
    ]

    canonical = _action(authority=authority)
    cases.extend(
        [
            _confirmation_case(
                "valid approval", deepcopy(canonical), True, pending_authority=authority
            ),
            _confirmation_case(
                "valid rejection",
                {**deepcopy(canonical), "estado_ejecucion": "rejected"},
                True,
                pending_authority=authority,
            ),
            _confirmation_case(
                "valid skipped",
                {**deepcopy(canonical), "estado_ejecucion": "skipped"},
                True,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation missing origin",
                _action(authority=authority, include_origin=False),
                True,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation null origin",
                _action(origin=None, authority=authority),
                True,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation origin mismatch",
                _action(origin="tx-other", authority=authority),
                False,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation action id mismatch",
                _action(action_id="B", authority=authority),
                False,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation requested id mismatch",
                deepcopy(canonical),
                False,
                requested_action_id="B",
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation action name mismatch",
                _action(name="navegar_a_posicion", authority=authority),
                False,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation parameters mismatch",
                _action(parameters={"grados": 91}, authority=authority),
                False,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation authority changed",
                _action(
                    authority={**deepcopy(authority), "action_binding_sha256": "0" * 64}
                ),
                False,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation authority removed",
                _action(authority=None),
                False,
                pending_authority=authority,
            ),
            _confirmation_case(
                "confirmation authority added",
                _action(authority=authority),
                False,
                pending_authority=None,
            ),
        ]
    )
    return cases


HARNESS = r'''
using System;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;

public static class ActionLifecycleHarness
{
    public static int Main(string[] args)
    {
        int count = 0;
        foreach (string line in File.ReadLines(args[0], new UTF8Encoding(false, true)))
        {
            if (line.Length == 0) continue;
            JObject item = JObject.Parse(line);
            EventoEntrante request = item["request"].ToObject<EventoEntrante>();
            AccionSaliente responseAction = item["response_action"].ToObject<AccionSaliente>();
            bool expected = item["expected"].Value<bool>();
            bool actual;

            if (item["mode"].Value<string>() == "new")
            {
                string canonicalOrigin;
                actual = ActionLifecycleContinuityV1.TryResolveNewActionOrigin(
                    request, responseAction, out canonicalOrigin
                );
                string expectedOrigin = item["canonical_origin"].Type == JTokenType.Null
                    ? null
                    : item["canonical_origin"].Value<string>();
                if (!string.Equals(canonicalOrigin, expectedOrigin, StringComparison.Ordinal))
                {
                    Console.WriteLine("FAIL|origin|" + item["case_id"] + "|" + canonicalOrigin);
                    return 1;
                }
            }
            else
            {
                string requestedActionId;
                bool requestValid = ActionLifecycleContinuityV1.TryGetRequestedConfirmationActionId(
                    request, out requestedActionId
                );
                PerceptionAuthorityReferenceV1 pendingAuthority =
                    item["pending_authority"].Type == JTokenType.Null
                        ? null
                        : item["pending_authority"].ToObject<PerceptionAuthorityReferenceV1>();
                actual = requestValid && ActionLifecycleContinuityV1.IsValidConfirmationContinuation(
                    requestedActionId,
                    responseAction,
                    item["pending_action_id"].Value<string>(),
                    item["pending_origin"].Value<string>(),
                    item["pending_name"].Value<string>(),
                    (JObject)item["pending_parameters"],
                    pendingAuthority
                );
            }

            if (actual != expected)
            {
                Console.WriteLine(
                    "FAIL|decision|" + item["case_id"] + "|expected=" + expected + "|actual=" + actual
                );
                return 1;
            }
            count++;
        }

        Console.WriteLine("PASS|cases=" + count);
        return 0;
    }
}
'''


def test_actual_unity_toolchain_enforces_lifecycle_matrix() -> None:
    """Compile the pure B15 boundary with Unity's installed Mono/Newtonsoft."""

    toolchain = _unity_csharp_toolchain()
    if toolchain is None:
        pytest.skip("toolchain C# de Unity no disponible para B15")
    mono, compiler, netstandard, newtonsoft = toolchain

    nonce = uuid4().hex
    temp = Path(tempfile.gettempdir())
    harness_path = temp / f"ActionLifecycleHarness.{nonce}.cs"
    matrix_path = temp / f"ActionLifecycleMatrix.{nonce}.jsonl"
    executable_path = temp / f"ActionLifecycleHarness.{nonce}.exe"
    harness_path.write_text(HARNESS, encoding="utf-8-sig")
    matrix_path.write_text(
        "\n".join(
            json.dumps(case, ensure_ascii=False, separators=(",", ":"))
            for case in _matrix()
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        compiled = subprocess.run(
            [
                str(mono),
                str(compiler),
                "/nologo",
                "/target:exe",
                f"/out:{executable_path}",
                f"/reference:{newtonsoft}",
                f"/reference:{netstandard}",
                str(DTOS),
                str(BOUNDARY),
                str(harness_path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        environment = os.environ.copy()
        environment["MONO_PATH"] = str(newtonsoft.parent)
        executed = subprocess.run(
            [str(mono), str(executable_path), str(matrix_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
            env=environment,
        )
    finally:
        harness_path.unlink(missing_ok=True)
        matrix_path.unlink(missing_ok=True)
        executable_path.unlink(missing_ok=True)

    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert f"PASS|cases={len(_matrix())}" in executed.stdout


def test_matrix_covers_required_b15_mutations() -> None:
    names = {case["case_id"] for case in _matrix()}
    assert names >= {
        "B15 tx-other exploit",
        "immediate explicit matching origin",
        "pending initial action",
        "valid approval",
        "valid rejection",
        "valid skipped",
        "confirmation origin mismatch",
        "confirmation action id mismatch",
        "confirmation action name mismatch",
        "confirmation parameters mismatch",
        "confirmation authority changed",
        "confirmation authority removed",
        "confirmation authority added",
    }


def test_lazzaro_agent_validates_before_pending_mutation_or_execution() -> None:
    resolve = AGENT_SOURCE.index("TryResolveCanonicalActionContext(solicitud, respuesta.accion")
    pending_store = AGENT_SOURCE.index("accionesEsperandoAutorizacion[contexto.IdAccion] = contexto")
    first_remove = AGENT_SOURCE.index("accionesEsperandoAutorizacion.Remove(contexto.IdAccion)", resolve)
    enqueue = AGENT_SOURCE.index("EncolarInicioEjecucion(contexto)", resolve)
    physical_switch = AGENT_SOURCE.index("switch (contexto.Nombre)", resolve)
    assert resolve < pending_store
    assert resolve < first_remove < enqueue < physical_switch

    resolver = AGENT_SOURCE.split("private bool TryResolveCanonicalActionContext", 1)[1].split(
        "private void EjecutarCancelarTarea", 1
    )[0]
    confirmation_start = resolver.index(
        'if (solicitud.tipo_evento == "confirmacion_accion")'
    )
    new_action_start = resolver.index(
        "!ActionLifecycleContinuityV1.TryResolveNewActionOrigin"
    )
    confirmation_branch = resolver[confirmation_start:new_action_start]
    assert "contexto = pendiente" in confirmation_branch
    assert "new ContextoAccion" not in confirmation_branch


def test_execution_and_terminal_reporting_use_stored_canonical_context() -> None:
    processing = AGENT_SOURCE.split(
        "private void ProcesarRespuestaDelCerebro(\n        EventoEntrante solicitud", 1
    )[1].split("private bool TryResolveCanonicalActionContext", 1)[0]
    assert "switch (contexto.Nombre)" in processing
    assert "contexto.Parametros" in processing
    assert "switch (respuesta.accion.nombre)" not in processing
    assert "respuesta.accion.parametros" not in processing

    resolver = AGENT_SOURCE.split("private bool TryResolveCanonicalActionContext", 1)[1].split(
        "private void EjecutarCancelarTarea", 1
    )[0]
    assert "contexto = pendiente" in resolver
    assert "canonicalOrigin" in resolver
    assert "responseAction.id_transaccion_origen" not in resolver

    assert 'detalles["id_transaccion_origen"] = contexto.IdTransaccionOrigen' in AGENT_SOURCE
    assert "CompletarTareaUsuarioActual(contexto.IdTransaccionOrigen" in AGENT_SOURCE


def test_b15_boundary_is_pure_and_does_not_own_state_or_execution() -> None:
    source = BOUNDARY.read_text(encoding="utf-8-sig")
    for forbidden in (
        "accionesEsperandoAutorizacion",
        "EncolarInicioEjecucion",
        "ProcesarRespuestaDelCerebro",
        "CompletarTareaUsuarioActual",
        "Rigidbody",
        "AddForce",
    ):
        assert forbidden not in source
    assert "JToken.DeepEquals" in source
    assert "StringComparison.Ordinal" in source
