"""Pruebas offline del contrato publico ROOT-010."""

from __future__ import annotations

from contextlib import contextmanager
import io
import logging
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.errors import (
    PUBLIC_ERROR_SPECS,
    build_public_error_envelope,
    install_public_error_handlers,
)
from api.routes import router
from api.schemas import AccionSaliente, PublicErrorCode, PublicErrorEnvelope
from core.action_ledger import (
    ActionLedgerError,
    TransactionConflictError,
    TransactionIncompleteError,
    TurnAbortedError,
    TurnProcessingError,
    TurnReconciliationRequiredError,
)


CANARY = "CANARY_API_KEY_7E4A"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@contextmanager
def _captured_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        handler.close()


def _app(orchestrator) -> FastAPI:
    app = FastAPI()
    app.state.orchestrator = orchestrator
    install_public_error_handlers(app)
    app.include_router(router)
    return app


def _client(orchestrator) -> TestClient:
    return TestClient(_app(orchestrator), raise_server_exceptions=False)


def _payload(transaction_id: str = "root010-transaction") -> dict:
    return {
        "origen": "unity",
        "tipo_evento": "actualizacion_estado",
        "id_transaccion": transaction_id,
        "detalles": {},
    }


def _assert_error(response, *, http_status: int, code: str, status: str) -> dict:
    assert_true(response.status_code == http_status, f"HTTP inesperado para {code}.")
    payload = response.json()
    assert_true(set(payload) == {"version", "status", "error"}, "Envelope top-level no es allowlist.")
    assert_true(payload["version"] == 1 and payload["status"] == status, "Version/status incorrectos.")
    assert_true(set(payload["error"]) == {"code", "message", "correlation_id"}, "Detalle no es allowlist.")
    assert_true(payload["error"]["code"] == code, "Codigo publico incorrecto.")
    UUID(payload["error"]["correlation_id"])
    assert_true("detail" not in payload and "accion" not in payload, "No debe reutilizar DTO de success.")
    return payload


class _Raising:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def procesar_evento(self, evento):
        del evento
        raise self.error


class _Noop:
    @staticmethod
    def procesar_evento(evento):
        del evento
        return None


def test_01_generic_exception_is_redacted_and_log_is_correlated() -> None:
    with _captured_logs() as logs:
        response = _client(_Raising(RuntimeError(f"token={CANARY}"))).post(
            "/api/v1/evento",
            json=_payload(),
        )
    payload = _assert_error(response, http_status=500, code="INTERNAL_ERROR", status="error")
    captured = logs.getvalue()
    assert_true(CANARY not in response.text and CANARY not in captured, "El canario no puede salir.")
    assert_true(payload["error"]["correlation_id"] in captured, "HTTP y log deben correlacionarse.")
    for field in ("component=api.evento", "code=INTERNAL_ERROR", "error_type=RuntimeError"):
        assert_true(field in captured, f"Falta diagnostico seguro: {field}")


def test_02_public_message_is_stable_across_exception_messages() -> None:
    first = _client(_Raising(RuntimeError("CANARY_A"))).post("/api/v1/evento", json=_payload("stable-a"))
    second = _client(_Raising(RuntimeError("CANARY_B"))).post("/api/v1/evento", json=_payload("stable-b"))
    one = first.json()["error"]
    two = second.json()["error"]
    assert_true(one["code"] == two["code"] == "INTERNAL_ERROR", "El code no depende del mensaje.")
    assert_true(one["message"] == two["message"], "El mensaje publico debe ser estable.")
    assert_true("CANARY_" not in first.text + second.text, "No debe reflejar exception text.")


def test_03_turn_processing_mapping() -> None:
    response = _client(_Raising(TurnProcessingError("motor", CANARY))).post(
        "/api/v1/evento", json=_payload()
    )
    _assert_error(response, http_status=500, code="TURN_PROCESSING_FAILED", status="error")
    assert_true(CANARY not in response.text, "failure_class interno no cruza HTTP.")


def test_04_turn_aborted_preserves_root007_http_500() -> None:
    response = _client(_Raising(TurnAbortedError(CANARY))).post(
        "/api/v1/evento", json=_payload()
    )
    _assert_error(response, http_status=500, code="TURN_ABORTED", status="error")


def test_05_reconciliation_mapping_preserves_pending_409() -> None:
    response = _client(_Raising(TurnReconciliationRequiredError(CANARY))).post(
        "/api/v1/evento", json=_payload()
    )
    _assert_error(response, http_status=409, code="RECONCILIATION_REQUIRED", status="pendiente")


def test_06_incomplete_mapping_preserves_pending_409() -> None:
    response = _client(_Raising(TransactionIncompleteError(CANARY))).post(
        "/api/v1/evento", json=_payload()
    )
    _assert_error(response, http_status=409, code="TRANSACTION_INCOMPLETE", status="pendiente")


def test_07_transaction_conflict_mapping() -> None:
    response = _client(_Raising(TransactionConflictError(CANARY))).post(
        "/api/v1/evento", json=_payload()
    )
    _assert_error(response, http_status=409, code="TRANSACTION_CONFLICT", status="error")


def test_08_action_ledger_mapping() -> None:
    response = _client(_Raising(ActionLedgerError(CANARY))).post(
        "/api/v1/evento", json=_payload()
    )
    _assert_error(
        response,
        http_status=503,
        code="OPERATIONAL_STORAGE_UNAVAILABLE",
        status="error",
    )
    assert_true(CANARY not in response.text, "El detalle del store no cruza HTTP.")


def test_09_orchestrator_unavailable_uses_same_envelope() -> None:
    response = _client(None).post("/api/v1/evento", json=_payload())
    _assert_error(response, http_status=503, code="AGENT_UNAVAILABLE", status="error")


def test_10_estado_failure_uses_same_envelope_and_new_correlation() -> None:
    class BrokenState:
        @staticmethod
        def obtener_estado_publico():
            raise RuntimeError(CANARY)

    response = _client(BrokenState()).get("/api/v1/estado")
    payload = _assert_error(response, http_status=500, code="STATE_READ_FAILED", status="error")
    UUID(payload["error"]["correlation_id"])
    assert_true(CANARY not in response.text, "Estado no filtra la excepcion.")


def test_11_constelacion_failure_uses_same_envelope() -> None:
    class BrokenConstellation:
        @staticmethod
        def obtener_constelacion_publica():
            raise RuntimeError(CANARY)

    response = _client(BrokenConstellation()).get("/api/v1/constelacion")
    _assert_error(response, http_status=500, code="CONSTELLATION_READ_FAILED", status="error")
    assert_true(CANARY not in response.text, "Constelacion no filtra excepcion ni traceback.")


def test_12_validation_422_is_safe_and_does_not_echo_input() -> None:
    response = _client(_Noop()).post(
        "/api/v1/evento",
        json={"origen": "unity", "tipo_evento": "", "id_transaccion": CANARY},
    )
    _assert_error(response, http_status=422, code="VALIDATION_ERROR", status="error")
    assert_true(CANARY not in response.text and "detail" not in response.json(), "422 no refleja payload.")


def test_13_valid_noop_and_pending_are_not_error_envelopes() -> None:
    noop = _client(_Noop()).post("/api/v1/evento", json=_payload("noop"))

    class Pending:
        @staticmethod
        def procesar_evento(evento):
            return AccionSaliente(
                id_accion="pending-action",
                nombre="navegar_a_objeto",
                estado_ejecucion="awaiting_authorization",
                id_transaccion_origen=evento.id_transaccion,
            )

    pending = _client(Pending()).post("/api/v1/evento", json=_payload("pending"))
    assert_true(noop.status_code == 200 and noop.json()["status"] == "ok", "No-op sigue success.")
    assert_true("error" not in noop.json(), "Success no usa error envelope.")
    assert_true(pending.status_code == 200 and pending.json()["status"] == "pendiente", "Authorization sigue pending valido.")


def test_14_notifications_failure_semantics_stay_root055_and_log_is_safe() -> None:
    class BrokenNotifications:
        @staticmethod
        def obtener_notificaciones():
            raise RuntimeError(CANARY)

    with _captured_logs() as logs:
        response = _client(BrokenNotifications()).get("/api/v1/notificaciones")
    assert_true(response.status_code == 200 and response.json() == [], "ROOT-055 debe seguir abierto.")
    assert_true(CANARY not in logs.getvalue(), "El boundary humano de notifications si debe ser seguro.")


def test_15_command_endpoint_inherits_event_error_contract() -> None:
    response = _client(_Raising(RuntimeError(CANARY))).post(
        "/api/v1/comando",
        json=_payload("command-error"),
    )
    _assert_error(response, http_status=500, code="INTERNAL_ERROR", status="error")


def test_16_error_dto_forbids_extra_fields() -> None:
    valid = build_public_error_envelope(PublicErrorCode.INTERNAL_ERROR).model_dump(mode="json")
    valid["private_detail"] = CANARY
    try:
        PublicErrorEnvelope.model_validate(valid)
    except ValidationError:
        pass
    else:
        raise AssertionError("El envelope debe rechazar extras.")


def test_17_taxonomy_has_fixed_nonempty_messages() -> None:
    assert_true(set(PUBLIC_ERROR_SPECS) == set(PublicErrorCode), "Todo code debe tener una spec unica.")
    for code, spec in PUBLIC_ERROR_SPECS.items():
        assert_true(bool(spec.message.strip()), f"{code.value} necesita mensaje fijo.")
        assert_true(CANARY not in spec.message, "Las templates no contienen input.")


def test_18_openapi_declares_runtime_error_envelope() -> None:
    schema = _app(_Noop()).openapi()
    responses = schema["paths"]["/api/v1/evento"]["post"]["responses"]
    for status in ("409", "422", "500", "503"):
        serialized = str(responses[status])
        assert_true("PublicErrorEnvelope" in serialized, f"OpenAPI {status} contradice runtime.")


def test_19_root026_reuses_conditional_public_error_mapping() -> None:
    root026_canary = "ROOT026_CANARY_SECRET"
    aborted = _client(_Raising(TurnProcessingError("triage", root026_canary))).post(
        "/api/v1/evento",
        json=_payload("root026-aborted-clean"),
    )
    partial = _client(_Raising(TurnReconciliationRequiredError(root026_canary))).post(
        "/api/v1/evento",
        json=_payload("root026-partial"),
    )

    _assert_error(aborted, http_status=500, code="TURN_PROCESSING_FAILED", status="error")
    _assert_error(partial, http_status=409, code="RECONCILIATION_REQUIRED", status="pendiente")
    assert_true(
        root026_canary not in aborted.text + partial.text,
        "El diagnostico privado no cruza HTTP.",
    )


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-010 API: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
