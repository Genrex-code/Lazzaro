"""Pruebas permanentes de ROOT-005: success material exige attestation."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from api.schemas import EventoEntrante
from core.action_ledger import (
    ActionLedger,
    ActionState,
    ActionTransitionEvent,
    OutcomeRecordStatus,
)
from core.material_attestation import (
    MaterialAttestationStatus,
    validate_material_attestation,
)
from terminal_client.test_chat import _orquestador_aislado


ROOT = Path(__file__).resolve().parents[1]


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _executing_action(orchestrator, action_name: str, action_id: str = "material-a"):
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    transaction_id = f"origin-{action_id}"
    ledger.claim_transaction(transaction_id, origin="unity", event_type="comando_usuario")
    created = ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name=action_name,
        parameters={"nombre_objeto": "Cube"},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    assert_true(created.action is not None, "El fixture debe crear action.")
    ledger.transition_action(action_id, ActionTransitionEvent.AUTO_AUTHORIZE)
    ledger.transition_action(action_id, ActionTransitionEvent.DISPATCH)
    ledger.transition_action(action_id, ActionTransitionEvent.START_EXECUTION)
    ledger.complete_transaction(transaction_id, None)
    return ledger


def _grab_receipt(action_id: str = "material-a", *, status: str = "applied") -> dict:
    evidence = (
        {
            "held_before": None,
            "held_after": "Cube",
            "held_reference_changed": True,
            "parent_is_hold_point": True,
        }
        if status == "applied"
        else {"reason_code": "grab_postcondition_missing"}
    )
    return {
        "action_id": action_id,
        "effect_kind": "grab",
        "status": status,
        "object_id": "Cube",
        "evidence": evidence,
    }


def _outcome_event(
    transaction_id: str,
    action_id: str,
    action_name: str,
    success: bool,
    receipt: object = None,
) -> EventoEntrante:
    details = {
        "id_accion": action_id,
        "id_transaccion_origen": f"origin-{action_id}",
        "accion": action_name,
        "exito": success,
        "mensaje_resultado": "resultado",
    }
    if receipt is not None:
        details["material_attestation"] = receipt
    return EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion=transaction_id,
        detalles=details,
    )


def _method_body(source: str, method_name: str) -> str:
    start = source.index(method_name)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Metodo C# sin cierre: {method_name}")


def test_01_material_success_with_applied_receipt_is_accepted() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "agarrar_objeto")
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event("outcome-valid", "material-a", "agarrar_objeto", True, _grab_receipt())
    )
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED, "APPLIED valido debe aceptarse.")
    assert_true(ledger.get_action_state("material-a") == ActionState.REPORTED_SUCCESS, "Debe terminalizar success.")
    stored = ledger.get_outcome("material-a")
    assert_true(
        stored is not None
        and stored.payload.get("material_attestation", {}).get("status") == "applied",
        "El ledger debe conservar el receipt operacional minimo.",
    )


def test_02_success_without_receipt_never_terminalizes() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "agarrar_objeto")
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event("outcome-missing", "material-a", "agarrar_objeto", True)
    )
    assert_true(result.status == OutcomeRecordStatus.INVALID_ATTESTATION, "Debe rechazar success desnudo.")
    assert_true(ledger.get_action_state("material-a") == ActionState.EXECUTING, "No debe terminalizar.")
    assert_true(ledger.get_outcome("material-a") is None, "No debe persistir outcome falso.")


def test_03_no_effect_false_is_a_typed_terminal_failure() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "agarrar_objeto")
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event(
            "outcome-no-effect",
            "material-a",
            "agarrar_objeto",
            False,
            _grab_receipt(status="no_effect"),
        )
    )
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED, "NO_EFFECT tipado debe ser observable.")
    assert_true(ledger.get_action_state("material-a") == ActionState.REPORTED_FAILURE, "No-op no es success.")


def test_04_no_effect_cannot_claim_success() -> None:
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": _grab_receipt(status="no_effect")},
    )
    assert_true(result.status == MaterialAttestationStatus.STATUS_MISMATCH, "NO_EFFECT + true debe rechazarse.")


def test_05_receipt_action_id_mismatch_is_rejected() -> None:
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="action-a",
        success=True,
        payload={"material_attestation": _grab_receipt("action-b")},
    )
    assert_true(result.status == MaterialAttestationStatus.ACTION_ID_MISMATCH, "El ID exacto es obligatorio.")


def test_06_receipt_effect_kind_mismatch_is_rejected() -> None:
    receipt = _grab_receipt()
    receipt["effect_kind"] = "force"
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(result.status == MaterialAttestationStatus.EFFECT_MISMATCH, "No se cruza grab con force.")


def test_07_strict_outcome_bool_rejects_string_true() -> None:
    try:
        EventoEntrante(
            origen="unity",
            tipo_evento="resultado_accion",
            id_transaccion="string-bool",
            detalles={"id_accion": "material-a", "exito": "true"},
        )
    except ValidationError:
        return
    raise AssertionError("La string 'true' no debe convertirse por truthiness.")


def test_08_status_enum_is_case_sensitive() -> None:
    receipt = _grab_receipt()
    receipt["status"] = "APPLIED"
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(result.status == MaterialAttestationStatus.MALFORMED, "Enum wire debe ser exacto.")


def test_09_applied_without_postcondition_is_rejected() -> None:
    receipt = _grab_receipt()
    receipt["evidence"] = {}
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(result.status == MaterialAttestationStatus.INSUFFICIENT_EVIDENCE, "APPLIED requiere evidencia.")


def test_10_hablar_does_not_require_material_attestation() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "hablar")
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event("speech-outcome", "material-a", "hablar", True)
    )
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED, "Hablar es NON_MATERIAL.")
    assert_true(ledger.get_action_state("material-a") == ActionState.REPORTED_SUCCESS, "Hablar conserva lifecycle.")


def test_11_movement_outcome_contract_is_unchanged() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "navegar_a_objeto")
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event("move-outcome", "material-a", "navegar_a_objeto", True)
    )
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED, "Movimiento control no cambia.")
    assert_true(ledger.get_action_state("material-a") == ActionState.REPORTED_SUCCESS, "Movimiento termina success.")


def test_12_duplicate_valid_material_outcome_does_not_reapply() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "agarrar_objeto")
    first = orchestrator._registrar_outcome_entrante(
        _outcome_event("receipt-first", "material-a", "agarrar_objeto", True, _grab_receipt())
    )
    second = orchestrator._registrar_outcome_entrante(
        _outcome_event("receipt-second", "material-a", "agarrar_objeto", True, _grab_receipt())
    )
    outcomes = ledger.get_outcome("material-a")
    terminal = [
        item for item in ledger.get_action_transitions("material-a")
        if item.state_after == ActionState.REPORTED_SUCCESS
    ]
    assert_true(first.status == OutcomeRecordStatus.ACCEPTED, "Primero se acepta.")
    assert_true(second.status == OutcomeRecordStatus.DUPLICATE, "Retry exacto es duplicate.")
    assert_true(outcomes is not None and len(terminal) == 1, "Debe haber un solo efecto terminal.")


def test_13_unknown_material_outcome_has_zero_effect() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = ActionLedger(":memory:")
    orchestrator.action_ledger = ledger
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event("unknown-outcome", "unknown", "agarrar_objeto", True, _grab_receipt("unknown"))
    )
    assert_true(result.status == OutcomeRecordStatus.UNKNOWN, "Unknown sigue bajo ROOT-003.")
    assert_true(ledger.get_outcome("unknown") is None, "Unknown no crea outcome.")


def test_14_valid_interaction_requires_component_observation() -> None:
    receipt = {
        "action_id": "interaction-a",
        "effect_kind": "interact",
        "status": "applied",
        "object_id": "Switch",
        "evidence": {
            "effect_observed": True,
            "interaction_type": "activar",
            "observer": "SwitchMaterializer",
        },
    }
    result = validate_material_attestation(
        action_name="interactuar_objeto_3d",
        action_id="interaction-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(result.valid, "Interaccion soportada debe venir del componente observador.")


def test_15_unsupported_is_never_success() -> None:
    receipt = _grab_receipt(status="unsupported")
    valid = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=False,
        payload={"material_attestation": receipt},
    )
    forged = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(valid.valid, "UNSUPPORTED false debe conservar diagnostico.")
    assert_true(not forged.valid, "UNSUPPORTED nunca produce success.")


def test_16_failed_is_never_success() -> None:
    receipt = _grab_receipt(status="failed")
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(not result.valid, "FAILED nunca produce success.")


def test_17_force_requires_fixed_update_and_directional_delta() -> None:
    receipt = {
        "action_id": "force-a",
        "effect_kind": "force",
        "status": "applied",
        "object_id": "Cube",
        "evidence": {
            "fixed_update_observed": True,
            "velocity_before": [0.0, 0.0, 0.0],
            "velocity_after": [1.0, 0.0, 0.0],
            "directional_delta": 1.0,
        },
    }
    valid = validate_material_attestation(
        action_name="aplicar_fuerza", action_id="force-a", success=True,
        payload={"material_attestation": receipt},
    )
    receipt["evidence"]["directional_delta"] = 0.0
    no_delta = validate_material_attestation(
        action_name="aplicar_fuerza", action_id="force-a", success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(valid.valid and not no_delta.valid, "AddForce sin delta no basta.")


def test_18_release_requires_observed_ownership_loss() -> None:
    receipt = {
        "action_id": "release-a",
        "effect_kind": "release",
        "status": "applied",
        "object_id": "Cube",
        "evidence": {
            "held_before": "Cube",
            "held_after": None,
            "held_reference_changed": True,
            "parent_is_hold_point": False,
        },
    }
    valid = validate_material_attestation(
        action_name="soltar_objeto", action_id="release-a", success=True,
        payload={"material_attestation": receipt},
    )
    receipt["evidence"]["held_after"] = "Cube"
    unchanged = validate_material_attestation(
        action_name="soltar_objeto", action_id="release-a", success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(valid.valid and not unchanged.valid, "Release requiere perder ownership.")


def test_19_protocol_invalid_never_reaches_behavior_identity_or_alias() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "agarrar_objeto", "consumer-gate")
    calls = {"behavior": 0, "identity": 0, "alias": 0}
    orchestrator.behavior.procesar_evento = lambda *args, **kwargs: calls.__setitem__("behavior", calls["behavior"] + 1)
    orchestrator.identity_state.registrar_evento = lambda *args, **kwargs: calls.__setitem__("identity", calls["identity"] + 1)
    orchestrator._persistir_alias_pendiente = lambda *args, **kwargs: calls.__setitem__("alias", calls["alias"] + 1)
    orchestrator.procesar_evento(
        _outcome_event("forged-consumers", "consumer-gate", "agarrar_objeto", True)
    )
    assert_true(calls == {"behavior": 0, "identity": 0, "alias": 0}, "Protocolo invalido no alcanza consumidores.")
    assert_true(ledger.get_action_state("consumer-gate") == ActionState.EXECUTING, "La action sigue executing.")


def test_20_four_handlers_report_only_material_receipts() -> None:
    source = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8")
    for method in (
        "EjecutarInteraccion(ContextoAccion",
        "EjecutarAgarrar(ContextoAccion",
        "EjecutarSoltar(ContextoAccion",
        "EjecutarAplicarFuerza(ContextoAccion",
    ):
        body = _method_body(source, method)
        assert_true("ReportarResultadoAccion(" not in body, f"{method} no puede emitir bool desnudo.")
        assert_true("ReportarResultadoMaterial(" in body, f"{method} debe transportar receipt.")


def test_21_success_source_is_materializer_postcondition() -> None:
    source = (ROOT / "unity_client" / "ObjectMaterializationController.cs").read_text(encoding="utf-8")
    required = (
        "HeldObject == body.gameObject",
        "body.transform.parent == EffectiveHoldPoint",
        "HeldObject == null",
        "yield return new WaitForFixedUpdate()",
        "directionalDelta > Mathf.Epsilon",
        "IMaterialInteractable",
    )
    assert_true(all(token in source for token in required), "Los success sources deben observar postcondiciones.")


def test_22_debug_log_cannot_be_a_material_success_source() -> None:
    source = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8")
    for method in (
        "EjecutarInteraccion(ContextoAccion",
        "EjecutarAgarrar(ContextoAccion",
        "EjecutarSoltar(ContextoAccion",
        "EjecutarAplicarFuerza(ContextoAccion",
    ):
        body = _method_body(source, method)
        assert_true("Debug.Log(" not in body, f"{method} no debe convertir log en outcome.")
        assert_true(", true," not in body, f"{method} no debe codificar success literal.")


def test_23_movement_callback_control_remains_correlated() -> None:
    agent = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8")
    movement = (ROOT / "unity_client" / "MovementController.cs").read_text(encoding="utf-8")
    assert_true("accionesMovimientoPendientes.TryGetValue(idAccion" in agent, "Movimiento correlaciona ID.")
    assert_true("OnMovimientoCompletado?.Invoke(actionId, true" in movement, "Movimiento completa por callback.")


def test_24_payload_persists_only_small_material_receipt() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    payload = orchestrator._payload_outcome_minimo(
        {
            "material_attestation": _grab_receipt(),
            "prompt": "secret",
            "full_scene_dump": {"objects": list(range(100))},
        }
    )
    assert_true("material_attestation" in payload, "Ledger debe conservar evidencia minima.")
    assert_true("prompt" not in payload and "full_scene_dump" not in payload, "No persiste contexto cognitivo/escena.")


def test_25_nested_or_oversized_evidence_is_rejected() -> None:
    receipt = _grab_receipt()
    receipt["evidence"]["scene"] = {"dump": "forbidden"}
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(result.status == MaterialAttestationStatus.MALFORMED, "Evidence no es un scene database.")


def test_26_all_four_actions_enforce_status_matrix() -> None:
    applied = {
        "interactuar_objeto_3d": {
            "effect_kind": "interact",
            "evidence": {
                "effect_observed": True,
                "interaction_type": "activar",
                "observer": "SwitchMaterializer",
            },
        },
        "agarrar_objeto": {
            "effect_kind": "grab",
            "evidence": {
                "held_before": None,
                "held_after": "Cube",
                "held_reference_changed": True,
                "parent_is_hold_point": True,
            },
        },
        "soltar_objeto": {
            "effect_kind": "release",
            "evidence": {
                "held_before": "Cube",
                "held_after": None,
                "held_reference_changed": True,
                "parent_is_hold_point": False,
            },
        },
        "aplicar_fuerza": {
            "effect_kind": "force",
            "evidence": {
                "fixed_update_observed": True,
                "velocity_before": [0.0, 0.0, 0.0],
                "velocity_after": [1.0, 0.0, 0.0],
                "directional_delta": 1.0,
            },
        },
    }
    for action_name, contract in applied.items():
        receipt = {
            "action_id": "matrix-a",
            "effect_kind": contract["effect_kind"],
            "status": "applied",
            "object_id": "Cube",
            "evidence": contract["evidence"],
        }
        accepted = validate_material_attestation(
            action_name=action_name,
            action_id="matrix-a",
            success=True,
            payload={"material_attestation": receipt},
        )
        assert_true(accepted.valid, f"{action_name}: APPLIED observado debe aceptarse.")
        for negative_status in ("no_effect", "unsupported", "failed"):
            negative = dict(receipt)
            negative["status"] = negative_status
            negative["evidence"] = {"reason_code": f"matrix_{negative_status}"}
            valid_negative = validate_material_attestation(
                action_name=action_name,
                action_id="matrix-a",
                success=False,
                payload={"material_attestation": negative},
            )
            forged_negative = validate_material_attestation(
                action_name=action_name,
                action_id="matrix-a",
                success=True,
                payload={"material_attestation": negative},
            )
            assert_true(valid_negative.valid, f"{action_name}: {negative_status} false debe ser tipado.")
            assert_true(not forged_negative.valid, f"{action_name}: {negative_status} no puede ser success.")


def test_27_all_four_actions_reject_wrong_id_and_effect() -> None:
    effects = {
        "interactuar_objeto_3d": "interact",
        "agarrar_objeto": "grab",
        "soltar_objeto": "release",
        "aplicar_fuerza": "force",
    }
    for action_name, effect in effects.items():
        receipt = {
            "action_id": "wrong-id",
            "effect_kind": effect,
            "status": "no_effect",
            "object_id": "Cube",
            "evidence": {"reason_code": "matrix"},
        }
        wrong_id = validate_material_attestation(
            action_name=action_name,
            action_id="expected-id",
            success=False,
            payload={"material_attestation": receipt},
        )
        receipt["action_id"] = "expected-id"
        receipt["effect_kind"] = "force" if effect != "force" else "grab"
        wrong_effect = validate_material_attestation(
            action_name=action_name,
            action_id="expected-id",
            success=False,
            payload={"material_attestation": receipt},
        )
        assert_true(wrong_id.status == MaterialAttestationStatus.ACTION_ID_MISMATCH, f"{action_name}: ID fail closed.")
        assert_true(wrong_effect.status == MaterialAttestationStatus.EFFECT_MISMATCH, f"{action_name}: effect fail closed.")


def test_28_empty_or_boolean_attestation_is_protocol_invalid() -> None:
    for invalid in ({}, True):
        result = validate_material_attestation(
            action_name="agarrar_objeto",
            action_id="material-a",
            success=True,
            payload={"material_attestation": invalid},
        )
        assert_true(not result.valid, "Receipt vacio/booleano no prueba materializacion.")


def test_29_unity_negative_sources_are_explicit() -> None:
    source = (ROOT / "unity_client" / "ObjectMaterializationController.cs").read_text(encoding="utf-8")
    for reason_code in (
        "capability_missing",
        "hold_point_missing",
        "already_holding_object",
        "nothing_held",
        "rigidbody_missing",
        "rigidbody_blocked",
        "force_postcondition_missing",
    ):
        assert_true(reason_code in source, f"Falta outcome tipado {reason_code}.")


def test_30_cognitive_text_cannot_hide_inside_evidence() -> None:
    receipt = _grab_receipt()
    receipt["evidence"]["prompt"] = "private thought"
    result = validate_material_attestation(
        action_name="agarrar_objeto",
        action_id="material-a",
        success=True,
        payload={"material_attestation": receipt},
    )
    assert_true(result.status == MaterialAttestationStatus.MALFORMED, "Evidence usa allowlist operacional.")


def test_31_non_material_outcome_does_not_pollute_ledger_with_attestation() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = _executing_action(orchestrator, "hablar")
    result = orchestrator._registrar_outcome_entrante(
        _outcome_event("speech-noise", "material-a", "hablar", True, _grab_receipt())
    )
    stored = ledger.get_outcome("material-a")
    assert_true(result.status == OutcomeRecordStatus.ACCEPTED, "Hablar conserva contrato.")
    assert_true(
        stored is not None and "material_attestation" not in stored.payload,
        "Un outcome NON_MATERIAL no convierte el ledger en physics database.",
    )


def main() -> None:
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-005: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
