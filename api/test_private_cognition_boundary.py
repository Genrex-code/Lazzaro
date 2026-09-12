"""Pruebas offline permanentes de la frontera de cognicion privada ROOT-009."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_engine.schemas import ActionResponse
from api.errors import install_public_error_handlers
from api.routes import router
from api.schemas import AccionSaliente, PublicErrorEnvelope, RespuestaAPI
from core.action_ledger import ActionLedger, TransactionClaimStatus
from core.audit_trace import AuditTrace
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from memory.conversation_memory import ConversationMemory
from terminal_client.chat import TerminalChatClient


PRIVATE_THOUGHT = "PRIVATE_THOUGHT_CANARY_9A11"
INTERNAL_REASON = "INTERNAL_REASON_CANARY_77BC"
PROMPT_CANARY = "PROMPT_CANARY_40DD"
MEMORY_CANARY = "MEMORY_CONTEXT_CANARY_31EF"
FORBIDDEN_FIELDS = {"pensamiento_privado", "razonamiento"}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bare_orchestrator() -> Orchestrator:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    orchestrator._notificaciones = deque(maxlen=50)
    orchestrator._notificaciones_lock = threading.Lock()
    orchestrator._transacciones_procesadas = OrderedDict()
    orchestrator._transacciones_cache_lock = threading.Lock()
    return orchestrator


def _internal_action(
    *,
    action: str = "hablar",
    parameters: dict | None = None,
    valid: bool = True,
    error: str | None = None,
) -> ActionResponse:
    return ActionResponse(
        pensamiento=PRIVATE_THOUGHT,
        accion=action,
        parametros=(
            parameters
            if parameters is not None
            else {"texto_a_comunicar": "Hola mundo"}
        ),
        razon=INTERNAL_REASON,
        valido=valid,
        error=error,
        metadata={"prompt": PROMPT_CANARY, "memory_context": MEMORY_CANARY},
    )


def _public_projection(internal: ActionResponse) -> AccionSaliente:
    return _bare_orchestrator()._a_accion_saliente(internal, "exploradora")


def _app(orchestrator: object) -> FastAPI:
    app = FastAPI()
    app.state.orchestrator = orchestrator
    install_public_error_handlers(app)
    app.include_router(router)
    return app


def _event(transaction_id: str) -> dict:
    return {
        "origen": "unity",
        "tipo_evento": "comando_usuario",
        "id_transaccion": transaction_id,
        "detalles": {"mensaje": "fixture"},
    }


class _FixedOrchestrator:
    def __init__(self, action: AccionSaliente | None) -> None:
        self.action = action

    def procesar_evento(self, evento) -> AccionSaliente | None:
        del evento
        return self.action


def _post_action(action: AccionSaliente | None, transaction_id: str) -> dict:
    with TestClient(_app(_FixedOrchestrator(action)), raise_server_exceptions=False) as client:
        response = client.post("/api/v1/evento", json=_event(transaction_id))
    assert_true(response.status_code == 200, "El fixture debe producir una respuesta publica valida.")
    return response.json()


def _assert_public_action(action: dict) -> None:
    assert_true(not FORBIDDEN_FIELDS.intersection(action), "Los campos privados deben estar ausentes.")
    serialized = json.dumps(action, ensure_ascii=False)
    for canary in (PRIVATE_THOUGHT, INTERNAL_REASON, PROMPT_CANARY, MEMORY_CANARY):
        assert_true(canary not in serialized, f"El canario privado cruzo el boundary: {canary}")


def test_01_internal_action_remains_rich_but_projection_is_minimal() -> None:
    internal = _internal_action()
    public = _public_projection(internal)
    assert_true(internal.pensamiento == PRIVATE_THOUGHT, "El thought interno debe conservarse.")
    assert_true(internal.razon == INTERNAL_REASON, "El reason interno debe conservarse.")
    assert_true(internal.metadata["prompt"] == PROMPT_CANARY, "Metadata interna no debe mutarse.")
    _assert_public_action(public.model_dump(mode="json"))
    assert_true(public.parametros == {"texto_a_comunicar": "Hola mundo"}, "La voz publica se conserva.")


def test_02_public_dto_rejects_legacy_private_fields() -> None:
    try:
        AccionSaliente(
            id_accion="root009-extra",
            nombre="hablar",
            parametros={"texto_a_comunicar": "Hola"},
            pensamiento_privado=PRIVATE_THOUGHT,
            razonamiento=INTERNAL_REASON,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("AccionSaliente no debe ignorar aliases privados legacy.")


def test_03_nested_response_serialization_has_structural_absence() -> None:
    internal = _internal_action()
    public = _public_projection(internal)
    payload = RespuestaAPI(status="ok", accion=public).model_dump(mode="json")
    assert_true(payload["accion"] is not None, "La respuesta debe conservar la accion.")
    _assert_public_action(payload["accion"])


def test_04_rest_hablar_keeps_only_explicit_public_speech() -> None:
    internal = _internal_action()
    public = _public_projection(internal).model_copy(
        update={"estado_ejecucion": "dispatched", "id_transaccion_origen": "root009-talk"}
    )
    payload = _post_action(public, "root009-talk")
    assert_true(payload["status"] == "ok", "Hablar debe seguir siendo success.")
    assert_true(
        payload["accion"]["parametros"]["texto_a_comunicar"] == "Hola mundo",
        "La voz publica contractual debe sobrevivir.",
    )
    _assert_public_action(payload["accion"])


def test_05_rest_motor_keeps_identity_params_and_lifecycle() -> None:
    internal = _internal_action(
        action="navegar_a_objeto",
        parameters={"nombre_objeto": "Cube"},
    )
    public = _public_projection(internal).model_copy(
        update={"estado_ejecucion": "dispatched", "id_transaccion_origen": "root009-motor"}
    )
    payload = _post_action(public, "root009-motor")
    action = payload["accion"]
    assert_true(action["nombre"] == "navegar_a_objeto", "La accion motora debe conservarse.")
    assert_true(action["parametros"] == {"nombre_objeto": "Cube"}, "Los parametros deben conservarse.")
    assert_true(action["id_accion"] == public.id_accion, "La identidad de accion debe conservarse.")
    assert_true(action["estado_ejecucion"] == "dispatched", "El lifecycle debe conservarse.")
    _assert_public_action(action)


def test_06_pending_authorization_keeps_contract_without_cognition() -> None:
    internal = _internal_action(
        action="interactuar_objeto_3d",
        parameters={"nombre_objeto": "Cube", "tipo_accion": "mirar"},
    )
    public = _public_projection(internal).model_copy(
        update={
            "requiere_confirmacion": True,
            "estado_ejecucion": "awaiting_authorization",
            "id_transaccion_origen": "root009-pending",
        }
    )
    payload = _post_action(public, "root009-pending")
    assert_true(payload["status"] == "pendiente", "Authorization debe conservar HTTP success pending.")
    assert_true(payload["accion"]["requiere_confirmacion"] is True, "El requisito debe conservarse.")
    assert_true(
        payload["accion"]["estado_ejecucion"] == "awaiting_authorization",
        "El estado awaiting_authorization debe conservarse.",
    )
    _assert_public_action(payload["accion"])


def test_07_private_thought_and_internal_error_never_create_notifications() -> None:
    orchestrator = _bare_orchestrator()
    thought = _internal_action(action="none", parameters={})
    failed = _internal_action(action="none", parameters={}, valid=False, error="RAW_INTERNAL_ERROR")
    assert_true(orchestrator._agregar_notificacion_si_corresponde(thought) is False, "Thought no notifica.")
    assert_true(orchestrator._agregar_notificacion_si_corresponde(failed) is False, "Error interno no notifica.")
    assert_true(orchestrator.obtener_notificaciones() == [], "La cola publica debe permanecer vacia.")


def test_08_openapi_schema_and_examples_are_structurally_private() -> None:
    schema = _app(_FixedOrchestrator(None)).openapi()
    action_schema = schema["components"]["schemas"]["AccionSaliente"]
    properties = set(action_schema.get("properties", {}))
    assert_true(not FORBIDDEN_FIELDS.intersection(properties), "OpenAPI no debe declarar cognition privada.")
    assert_true(action_schema.get("additionalProperties") is False, "El DTO debe rechazar extras.")
    action_serialized = json.dumps(action_schema, ensure_ascii=False)
    response_serialized = json.dumps(
        schema["components"]["schemas"]["RespuestaAPI"],
        ensure_ascii=False,
    )
    for forbidden in FORBIDDEN_FIELDS:
        assert_true(forbidden not in action_serialized + response_serialized, "OpenAPI conserva un alias privado.")
    for canary in (PRIVATE_THOUGHT, INTERNAL_REASON, PROMPT_CANARY, MEMORY_CANARY):
        assert_true(canary not in action_serialized + response_serialized, "OpenAPI contiene un canario privado.")


def test_09_terminal_uses_speech_only_even_against_legacy_payload() -> None:
    response = {
        "status": "ok",
        "accion": {
            "id_accion": "terminal-private-probe",
            "nombre": "hablar",
            "parametros": {"texto_a_comunicar": "Hola mundo"},
            "pensamiento_privado": PRIVATE_THOUGHT,
            "razonamiento": INTERNAL_REASON,
        },
    }
    rendered = TerminalChatClient._extraer_texto_hablar(response)
    assert_true(rendered == "Hola mundo", "Terminal debe devolver solo habla publica.")
    assert_true(PRIVATE_THOUGHT not in rendered and INTERNAL_REASON not in rendered, "Terminal filtro cognition.")


def test_10_conversation_memory_keeps_internal_thought_out_of_public_snapshot() -> None:
    memory = ConversationMemory()
    memory.registrar_turno("hola", "Hola mundo", pensamiento=PRIVATE_THOUGHT)
    internal = memory.obtener_ultimos(1)[0]
    public = memory.snapshot_publico()[0]
    assert_true(internal.pensamiento == PRIVATE_THOUGHT, "La RAM interna puede conservar thought.")
    serialized = json.dumps(asdict(public), default=str, ensure_ascii=False)
    assert_true(PRIVATE_THOUGHT not in serialized, "El snapshot publico no debe incluir thought.")
    assert_true("pensamiento" not in asdict(public), "La ausencia del snapshot debe ser estructural.")


def test_11_audittrace_observes_decision_without_cognition() -> None:
    class Writer:
        def __init__(self) -> None:
            self.traces: list[AuditTrace] = []

        def write(self, trace: AuditTrace) -> bool:
            self.traces.append(trace)
            return True

    internal = _internal_action(action="navegar_a_objeto", parameters={"nombre_objeto": "Cube"})
    orchestrator = _bare_orchestrator()
    writer = Writer()
    orchestrator.audit_writer = writer
    emitted = orchestrator._registrar_decision_audit(
        type("Event", (), {"id_transaccion": "root009-audit"})(),
        "root009-trace",
        layer="motor",
        action=internal.accion,
        valid=internal.valido,
    )
    assert_true(emitted and len(writer.traces) == 1, "El stage decision debe emitirse.")
    serialized = writer.traces[0].to_json()
    assert_true(PRIVATE_THOUGHT not in serialized and INTERNAL_REASON not in serialized, "AuditTrace no vuelca cognition.")
    assert_true(internal.pensamiento == PRIVATE_THOUGHT and internal.razon == INTERNAL_REASON, "Audit no muta cognition.")


def test_12_public_state_and_constellation_do_not_expose_action_cognition() -> None:
    orchestrator = _bare_orchestrator()
    orchestrator.behavior = BehaviorCore()
    state = orchestrator.obtener_estado_publico().model_dump(mode="json")
    constellation = orchestrator.obtener_constelacion_publica()
    serialized = json.dumps({"state": state, "constellation": constellation}, ensure_ascii=False)
    for forbidden in FORBIDDEN_FIELDS:
        assert_true(forbidden not in serialized, "State/constellation no deben declarar fields privados.")
    assert_true(PRIVATE_THOUGHT not in serialized and INTERNAL_REASON not in serialized, "No hay canarios privados.")


def test_13_cache_and_restart_replay_share_the_same_safe_projection() -> None:
    internal = _internal_action(
        action="navegar_a_objeto",
        parameters={"nombre_objeto": "Cube"},
    )
    initial = _public_projection(internal).model_copy(
        update={"estado_ejecucion": "dispatched", "id_transaccion_origen": "root009-replay"}
    )
    initial_dump = initial.model_dump(mode="json")
    _assert_public_action(initial_dump)

    cache_owner = _bare_orchestrator()
    cache_owner._cachear_transaccion("root009-replay", initial)
    cached = cache_owner._obtener_transaccion_cacheada("root009-replay")
    assert_true(cached is not None and cached.model_dump(mode="json") == initial_dump, "Cache cambia proyeccion.")

    with TemporaryDirectory(prefix="root009-ledger-") as directory:
        path = Path(directory) / "action_ledger.sqlite3"
        first = ActionLedger(path)
        claim = first.claim_transaction(
            "root009-replay",
            origin="unity",
            event_type="comando_usuario",
        )
        assert_true(claim.status == TransactionClaimStatus.CREATED, "El fixture debe reclamar la transaccion.")
        durable = cache_owner._serializar_accion_durable(initial)
        first.complete_transaction("root009-replay", durable)
        first.close()

        second = ActionLedger(path)
        replay_claim = second.claim_transaction(
            "root009-replay",
            origin="unity",
            event_type="comando_usuario",
        )
        assert_true(replay_claim.status == TransactionClaimStatus.COMPLETED, "Restart debe hallar completion.")
        restart_owner = _bare_orchestrator()
        restart_owner.action_ledger = second
        restored = restart_owner._restaurar_accion_desde_ledger(
            replay_claim.transaction.response_action
        )
        assert_true(restored is not None, "Restart debe reconstruir la accion.")
        restored_dump = restored.model_dump(mode="json")
        assert_true(restored_dump == initial_dump, "Initial/cache/restart deben ser identicos.")
        _assert_public_action(restored_dump)
        second.close()


def test_14_action_ledger_payload_contains_only_safe_operational_state() -> None:
    internal = _internal_action(
        action="navegar_a_objeto",
        parameters={"nombre_objeto": "Cube"},
    )
    public = _public_projection(internal)
    durable = _bare_orchestrator()._serializar_accion_durable(public)
    assert_true(
        set(durable or {})
        == {
            "id_accion",
            "nombre",
            "parametros",
            "requiere_confirmacion",
            "estado_ejecucion",
            "id_transaccion_origen",
            "decision_outcome",
        },
        "El payload durable debe contener solo accion y outcome tipado seguro.",
    )
    assert_true(
        set((durable or {})["decision_outcome"])
        == {
            "contract_version",
            "disposition",
            "quality",
            "attempt",
            "layer",
            "reason_codes",
            "retry_count",
            "primary_parse_status",
            "final_parse_status",
            "motor_status",
        },
        "El snapshot interno no debe incorporar cognicion ni payload libre.",
    )
    _assert_public_action(durable or {})


def test_15_root010_public_error_contract_is_unchanged() -> None:
    assert_true(
        set(PublicErrorEnvelope.model_fields) == {"version", "status", "error"},
        "ROOT-009 no debe modificar PublicErrorEnvelope v1.",
    )
    error_fields = set(PublicErrorEnvelope.model_fields["error"].annotation.model_fields)
    assert_true(error_fields == {"code", "message", "correlation_id"}, "PublicErrorDetail cambio.")


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-009 API: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
