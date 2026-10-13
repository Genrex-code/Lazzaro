"""Contrato HTTP read-only de reconciliacion ROOT-054."""

from __future__ import annotations

import json

from api.routes import reconciliar_transaccion
from api.schemas import (
    AccionSaliente,
    ReconciliacionTransaccionRespuestaV1,
    ReconciliacionTransaccionV1,
)
from core.transport_delivery import (
    DeliveryDirectiveV1,
    DeliveryReconciliationResultV1,
    DeliveryResolutionV1,
    DurableReceiptKindV1,
    DurableReceiptStatusV1,
)


class _FakeOrchestrator:
    def __init__(self, result, action=None, error: Exception | None = None) -> None:
        self.result = result
        self.action = action
        self.error = error
        self.calls = []

    def reconciliar_transaccion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result, self.action


def _request(**overrides):
    value = {
        "version": 1,
        "id_transaccion": "tx-054",
        "origen": "unity",
        "tipo_evento": "comando_usuario",
        "request_identity_version": 1,
        "request_fingerprint_sha256": "a" * 64,
    }
    value.update(overrides)
    return value


def _invoke(orchestrator, payload):
    request = ReconciliacionTransaccionV1.model_validate(payload)
    coroutine = reconciliar_transaccion(request, orchestrator)
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("La ruta read-only no debe suspender ni iniciar trabajo adicional")


def test_not_found_is_the_only_public_retry_directive() -> None:
    result = DeliveryReconciliationResultV1(
        "tx-054",
        DeliveryResolutionV1.NOT_FOUND,
        DeliveryDirectiveV1.RETRY_SAME_TX,
    )
    fake = _FakeOrchestrator(result)
    response = _invoke(fake, _request())
    assert isinstance(response, ReconciliacionTransaccionRespuestaV1)
    assert response.model_dump(mode="json") == {
        "version": 1,
        "id_transaccion": "tx-054",
        "resolution": "NOT_FOUND",
        "directive": "RETRY_SAME_TX",
        "receipt_kind": "NONE",
        "receipt_status": "NONE",
        "accion": None,
    }
    assert fake.calls == [
        {
            "transaction_id": "tx-054",
            "origin": "unity",
            "event_type": "comando_usuario",
            "request_identity_version": 1,
            "request_fingerprint_sha256": "a" * 64,
        }
    ]


def test_lifecycle_receipt_and_action_are_projected_without_new_authority() -> None:
    action = AccionSaliente(
        id_accion="action-054",
        nombre="girar_avatar",
        parametros={"grados": 90.0},
        estado_ejecucion="dispatched",
        id_transaccion_origen="origin-054",
    )
    result = DeliveryReconciliationResultV1(
        "tx-054",
        DeliveryResolutionV1.COMPLETED_WITH_ACTION,
        DeliveryDirectiveV1.RESOLVED,
        DurableReceiptKindV1.ACTION_LIFECYCLE,
        DurableReceiptStatusV1.APPLIED,
        action_id="action-054",
    )
    response = _invoke(
        _FakeOrchestrator(result, action),
        _request(tipo_evento="confirmacion_accion"),
    )
    payload = response.model_dump(mode="json")
    assert payload["resolution"] == "COMPLETED_WITH_ACTION"
    assert payload["receipt_kind"] == "ACTION_LIFECYCLE"
    assert payload["receipt_status"] == "APPLIED"
    assert payload["accion"]["id_accion"] == "action-054"
    assert payload["accion"]["estado_ejecucion"] == "dispatched"


def test_legacy_reconciliation_request_remains_explicitly_unverifiable() -> None:
    result = DeliveryReconciliationResultV1(
        "legacy-054",
        DeliveryResolutionV1.UNVERIFIABLE,
        DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
    )
    response = _invoke(
        _FakeOrchestrator(result),
        _request(
            id_transaccion="legacy-054",
            request_identity_version=None,
            request_fingerprint_sha256=None,
        ),
    )
    assert response.resolution is DeliveryResolutionV1.UNVERIFIABLE
    assert response.directive is DeliveryDirectiveV1.REQUIRES_RECONCILIATION


def test_unexpected_reconciliation_failure_uses_redacted_public_envelope() -> None:
    fake = _FakeOrchestrator(None, error=RuntimeError("SECRET_ROOT_054"))
    response = _invoke(fake, _request())
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "SECRET_ROOT_054" not in response.body.decode("utf-8")
