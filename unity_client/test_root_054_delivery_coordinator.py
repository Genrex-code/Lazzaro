"""ROOT-054: delivery durable, reconciliacion y handoff fisico ACK-first."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import pytest

from core.transport_delivery import request_fingerprint_sha256_v1
from unity_client.test_root_038_response_envelope_boundary import (
    _unity_csharp_toolchain,
)


ROOT = Path(__file__).resolve().parents[1]
UNITY = ROOT / "unity_client"
AGENT = (UNITY / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
API = (UNITY / "ApiClient.cs").read_text(encoding="utf-8-sig")
OUTBOX = (UNITY / "UnityDeliveryOutboxV1.cs").read_text(encoding="utf-8-sig")
IDENTITY = (UNITY / "UnityDeliveryIdentityV1.cs").read_text(encoding="utf-8-sig")
WIRE = (UNITY / "UnityDeliveryReconciliationWireV1.cs").read_text(
    encoding="utf-8-sig"
)


def test_start_receipt_precedes_every_physical_handler() -> None:
    start = AGENT.split("private void ProcesarStartConfirmado(", 1)[1].split(
        "private void ProcesarRespuestaDelCerebro(\n        string", 1
    )[0]
    assert 'receipt.receipt_kind != "ACTION_LIFECYCLE"' in start
    assert 'receipt.receipt_status != "APPLIED"' in start
    assert 'receipt.receipt_status != "ALREADY_APPLIED"' in start
    assert "if (!deliveryOutbox.MarkExecutionHandoff" in start
    assert 'MarkPaused(evento.id_transaccion, "OUTBOX_WRITE_FAILED")' in start
    assert start.index("MarkExecutionHandoff") < start.index(
        "EjecutarAccionConfirmada(contexto)"
    )

    response = AGENT.split(
        "private void ProcesarRespuestaDelCerebro(\n        EventoEntrante solicitud", 1
    )[1].split("private void EjecutarAccionConfirmada", 1)[0]
    assert "EncolarInicioEjecucion(contexto)" in response
    assert "switch (contexto.Nombre)" not in response
    execution = AGENT.split("private void EjecutarAccionConfirmada", 1)[1].split(
        "private bool TryResolveCanonicalActionContext", 1
    )[0]
    assert "switch (contexto.Nombre)" in execution


def test_http_2xx_lifecycle_is_not_treated_as_semantic_ack() -> None:
    transport = AGENT.split("private void ProcesarRespuestaTransporte", 1)[1].split(
        "private void ProcesarFalloTransporte", 1
    )[0]
    assert "RequiereReceiptDurable(evento.tipo_evento)" in transport
    assert "StartCoroutine(ReconciliarEvento(evento))" in transport
    assert transport.index("RequiereReceiptDurable") < transport.index(
        "ProcesarRespuestaDelCerebro"
    )
    assert "UnityDeliveryReconciliationWireV1.TryParse" in API


def test_only_proven_not_found_requeues_the_original_request() -> None:
    reconciliation = AGENT.split("private void ProcesarReconciliacion(", 1)[1].split(
        "private IEnumerator ReconciliarEventoDespues", 1
    )[0]
    retry = reconciliation.split('if (receipt.directive == "RETRY_SAME_TX")', 1)[1].split(
        'if (receipt.directive == "WAIT")', 1
    )[0]
    assert 'receipt.resolution != "NOT_FOUND"' in retry
    assert "request_identity_version != UnityDeliveryIdentityV1.Version" in retry
    assert "EncolarEventoExistente(evento" in retry
    assert reconciliation.count("EncolarEventoExistente(evento") == 1


def test_restart_reconciles_and_execution_handoff_never_reexecutes() -> None:
    restore = AGENT.split("private void RestaurarDeliveryPendiente()", 1)[1].split(
        "public void ConfirmarAccion", 1
    )[0]
    assert "ExecutionHandoff" in restore
    assert "StartCoroutine(ReconciliarEvento(record.request))" in restore
    assert "EncolarEventoExistente(record.request" not in restore
    assert "EXECUTION_CONTEXT_UNAVAILABLE" in AGENT
    assert "accionesEntregadasAEjecucion.Add" in AGENT


def test_outbox_is_transport_only_and_persists_before_send() -> None:
    enqueue = AGENT.split("public string EnviarEvento(", 1)[1].split(
        "private void EncolarEventoExistente", 1
    )[0]
    assert enqueue.index("deliveryOutbox.Enqueue(evento)") < enqueue.index(
        "EncolarEventoExistente(evento"
    )
    assert "canonical_parameters_json" not in OUTBOX
    assert "authority_decision_id" not in OUTBOX
    assert "action_binding_sha256" not in OUTBOX
    assert "exito material" in OUTBOX
    assert "File.Replace" in OUTBOX


def test_reconciliation_wire_is_strict_and_reuses_root_038_action_facade() -> None:
    assert "HasExactFields(root, RootFields)" in WIRE
    assert "ResponseDuplicateWireBoundary.TryValidateResponse" in WIRE
    assert "UnityResponseWireContractV1.TryParse" in WIRE
    assert "IsAllowedCombination(" in WIRE
    assert 'case "NOT_FOUND":' in WIRE
    assert 'directive == "RETRY_SAME_TX"' in WIRE


def _compile_and_run(sources: list[str], harness: str, arguments: list[str]) -> str:
    toolchain = _unity_csharp_toolchain()
    if toolchain is None:
        pytest.skip("toolchain C# de Unity no disponible")
    mono, compiler, netstandard, newtonsoft = toolchain
    nonce = uuid4().hex
    temporary = Path(tempfile.gettempdir())
    harness_path = temporary / f"Root054Harness.{nonce}.cs"
    executable = temporary / f"Root054Harness.{nonce}.exe"
    harness_path.write_text(harness, encoding="utf-8-sig")
    command = [
        str(mono),
        str(compiler),
        "/nologo",
        "/target:exe",
        f"/out:{executable}",
        f"/reference:{newtonsoft}",
        f"/reference:{netstandard}",
        *(str(UNITY / source) for source in sources),
        str(harness_path),
    ]
    try:
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=120)
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        environment = os.environ.copy()
        environment["MONO_PATH"] = str(newtonsoft.parent)
        executed = subprocess.run(
            [str(mono), str(executable), *arguments],
            text=True,
            capture_output=True,
            env=environment,
            timeout=120,
        )
        assert executed.returncode == 0, executed.stdout + executed.stderr
        value = executed.stdout.strip()
        return value.removeprefix("\ufeff").removeprefix("ï»¿")
    finally:
        harness_path.unlink(missing_ok=True)
        executable.unlink(missing_ok=True)


def test_actual_csharp_request_fingerprint_matches_python() -> None:
    event = {
        "origen": "unity",
        "tipo_evento": "comando_usuario",
        "id_transaccion": "tx-á-054",
        "objeto_involucrado": "Caja",
        "detalles": {
            "mensaje": "mueve la caja",
            "nested": {"z": True, "a": [1, 1.25, None]},
        },
        "request_identity_version": None,
        "request_fingerprint_sha256": None,
    }
    expected = request_fingerprint_sha256_v1(
        transaction_id=event["id_transaccion"],
        origin=event["origen"],
        event_type=event["tipo_evento"],
        involved_object=event["objeto_involucrado"],
        details=event["detalles"],
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as stream:
        json.dump(event, stream, ensure_ascii=False, separators=(",", ":"))
        event_path = Path(stream.name)
    harness = r'''
using System;
using System.IO;
using Newtonsoft.Json;
public static class Root054Harness {
    public static int Main(string[] args) {
        EventoEntrante value = JsonConvert.DeserializeObject<EventoEntrante>(File.ReadAllText(args[0]));
        if (!UnityDeliveryIdentityV1.TryPrepare(value)) return 2;
        Console.Write(value.request_fingerprint_sha256);
        return 0;
    }
}
'''
    try:
        actual = _compile_and_run(
            ["DTOs.cs", "UnityDeliveryIdentityV1.cs"],
            harness,
            [str(event_path)],
        )
    finally:
        event_path.unlink(missing_ok=True)
    assert actual == expected


def test_actual_csharp_outbox_survives_restart_without_new_identity() -> None:
    harness = r'''
using System;
using System.Collections.Generic;
public static class Root054Harness {
    public static int Main(string[] args) {
        var box = new UnityDeliveryOutboxV1(args[0]);
        var request = new EventoEntrante {
            origen = "unity", tipo_evento = "comando_usuario",
            id_transaccion = "tx-outbox", objeto_involucrado = "ninguno",
            detalles = new Dictionary<string, object> { { "mensaje", "hola" } }
        };
        if (!box.Enqueue(request) || !box.MarkSending("tx-outbox")) return 2;
        if (!box.MarkUnknown("tx-outbox", "TRANSPORT_TIMEOUT")) return 3;
        string fingerprint = request.request_fingerprint_sha256;
        var reopened = new UnityDeliveryOutboxV1(args[0]);
        var rows = reopened.SnapshotPending();
        if (rows.Count != 1 || rows[0].state != UnityDeliveryOutboxV1.Unknown) return 4;
        if (rows[0].send_attempt_count != 1) return 5;
        if (rows[0].request.request_fingerprint_sha256 != fingerprint) return 6;
        Console.Write("PASS");
        return 0;
    }
}
'''
    with tempfile.TemporaryDirectory(prefix="root054-outbox-", dir=ROOT) as directory:
        outbox_path = Path(directory) / "outbox.json"
        output = _compile_and_run(
            ["DTOs.cs", "UnityDeliveryIdentityV1.cs", "UnityDeliveryOutboxV1.cs"],
            harness,
            [str(outbox_path)],
        )
    assert output == "PASS"


def test_actual_csharp_reconciliation_wire_matrix() -> None:
    action = {
        "id_accion": "action-054",
        "nombre": "girar_avatar",
        "parametros": {"grados": 90.0},
        "requiere_confirmacion": False,
        "estado_ejecucion": "dispatched",
        "id_transaccion_origen": "origin-054",
        "parametros_descripcion": [],
        "perception_authority": None,
    }
    base = {
        "version": 1,
        "id_transaccion": "tx-054",
        "resolution": "NOT_FOUND",
        "directive": "RETRY_SAME_TX",
        "receipt_kind": "NONE",
        "receipt_status": "NONE",
        "accion": None,
    }
    completed = {
        **base,
        "resolution": "COMPLETED_WITH_ACTION",
        "directive": "RESOLVED",
        "receipt_kind": "ACTION_LIFECYCLE",
        "receipt_status": "APPLIED",
        "accion": action,
    }
    invalid_pair = {**base, "resolution": "UNKNOWN"}
    false_start_ack = {
        **base,
        "resolution": "UNKNOWN",
        "directive": "RESOLVED",
        "receipt_kind": "ACTION_LIFECYCLE",
        "receipt_status": "APPLIED",
    }
    wrong_case = dict(base)
    wrong_case["Resolution"] = wrong_case.pop("resolution")
    cases = [
        (base, True),
        (completed, True),
        (invalid_pair, False),
        (false_start_ack, False),
        (wrong_case, False),
    ]
    duplicate = json.dumps(base, separators=(",", ":")).replace(
        '"resolution":"NOT_FOUND"',
        '"resolution":"NOT_FOUND","resolution":"UNKNOWN"',
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    ) as stream:
        for payload, expected in cases:
            stream.write(
                json.dumps(
                    {"raw": json.dumps(payload, separators=(",", ":")), "expected": expected},
                    separators=(",", ":"),
                )
                + "\n"
            )
        stream.write(
            json.dumps({"raw": duplicate, "expected": False}, separators=(",", ":"))
            + "\n"
        )
        matrix = Path(stream.name)
    harness = r'''
using System;
using System.IO;
using Newtonsoft.Json.Linq;
public static class Root054Harness {
    public static int Main(string[] args) {
        int count = 0;
        foreach (string line in File.ReadAllLines(args[0])) {
            JObject item = JObject.Parse(line);
            bool expected = item["expected"].Value<bool>();
            bool actual = UnityDeliveryReconciliationWireV1.TryParse(
                item["raw"].Value<string>(), out ReconciliacionTransaccionRespuestaV1 result
            );
            if (actual != expected || (actual && result == null)) return 2;
            count++;
        }
        Console.Write("PASS|" + count);
        return 0;
    }
}
'''
    try:
        output = _compile_and_run(
            [
                "DTOs.cs",
                "ResponseDuplicateWireBoundary.cs",
                "ResponseEnvelopeWireBoundaryV1.cs",
                "ActionEnvelopeWireBoundary.cs",
                "PhysicalActionWireBoundaryV1.cs",
                "AuthorityWireBoundary.cs",
                "ActionBindingVerifierV1.cs",
                "UnityResponseWireContractV1.cs",
                "UnityDeliveryReconciliationWireV1.cs",
            ],
            harness,
            [str(matrix)],
        )
    finally:
        matrix.unlink(missing_ok=True)
    assert output == "PASS|6"
