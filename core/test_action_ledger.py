"""Pruebas permanentes de ROOT-003: identidad canonica y action ledger."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import threading

from pydantic import ValidationError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.action_ledger import (
    ActionLedger,
    ActionLedgerError,
    ActionRecordStatus,
    OutcomeConsumptionStatus,
    OutcomeRecordStatus,
    TransactionClaimStatus,
    TransactionIncompleteError,
)
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from terminal_client.test_chat import _orquestador_aislado


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _claim_action(
    ledger: ActionLedger,
    *,
    transaction_id: str = "tx-action",
    action_id: str = "action-1",
    action_name: str = "navegar_a_objeto",
    task_instance_id: str | None = "task-1",
    complete: bool = True,
) -> None:
    claim = ledger.claim_transaction(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
    )
    assert_true(claim.status == TransactionClaimStatus.CREATED, "El fixture debe reclamar una transaccion nueva.")
    recorded = ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name=action_name,
        parameters={"nombre_objeto": "Cube"},
        requires_confirmation=False,
        task_instance_id=task_instance_id,
        intended_client="unity",
        outcome_expected=True,
    )
    assert_true(recorded.status == ActionRecordStatus.CREATED, "El fixture debe crear una accion durable.")
    if complete:
        status = ledger.complete_transaction(
            transaction_id,
            {
                "id_accion": action_id,
                "nombre": action_name,
                "parametros": {"nombre_objeto": "Cube"},
                "requiere_confirmacion": False,
            },
        )
        assert_true(status.value == "completed", "El fixture debe completar la transaccion.")


def _outcome_event(
    transaction_id: str,
    action_id: str,
    *,
    action_name: str = "navegar_a_objeto",
    success: bool = True,
    origin_transaction_id: str | None = None,
) -> EventoEntrante:
    details = {
        "id_accion": action_id,
        "accion": action_name,
        "exito": success,
        "mensaje_resultado": "resultado sintetico",
    }
    if origin_transaction_id is not None:
        details["id_transaccion_origen"] = origin_transaction_id
    return EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion=transaction_id,
        detalles=details,
    )


def _attach_ledger(orchestrator: Orchestrator, ledger: ActionLedger) -> Orchestrator:
    orchestrator.action_ledger = ledger
    return orchestrator


def test_transaction_claim_is_unique_and_opaque() -> None:
    ledger = ActionLedger(":memory:")
    first = ledger.claim_transaction("opaque-client-key", origin="unity", event_type="comando_usuario")
    second = ledger.claim_transaction("opaque-client-key", origin="unity", event_type="comando_usuario")
    assert_true(first.status == TransactionClaimStatus.CREATED, "El primer claim debe ganar.")
    assert_true(second.status == TransactionClaimStatus.INCOMPLETE, "El segundo claim debe ver incomplete.")
    ledger.close()


def test_transaction_contract_conflict_fails_closed() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("tx-conflict", origin="unity", event_type="comando_usuario")
    conflict = ledger.claim_transaction("tx-conflict", origin="terminal", event_type="percepcion_entorno")
    assert_true(conflict.status == TransactionClaimStatus.CONFLICT, "No se debe reutilizar una key para otro evento.")
    ledger.close()


def test_action_id_unique_and_transaction_lookup_exact() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger, complete=False)
    by_id = ledger.get_action("action-1")
    by_tx = ledger.get_action_for_transaction("tx-action")
    assert_true(by_id is not None and by_tx == by_id, "Ambos indices deben resolver la misma accion.")
    ledger.claim_transaction("tx-other", origin="unity", event_type="comando_usuario")
    conflict = ledger.record_action(
        action_id="action-1",
        transaction_id="tx-other",
        action_name="navegar_a_objeto",
        parameters={},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
    )
    assert_true(conflict.status == ActionRecordStatus.CONFLICT, "action_id no puede pertenecer a dos requests.")
    ledger.close()


def test_exact_outcome_is_accepted_once_and_consumed_explicitly() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger)
    receipt = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-1",
        success=True,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "ok"},
    )
    assert_true(receipt.status == OutcomeRecordStatus.ACCEPTED, "El primer outcome exacto debe aceptarse.")
    assert_true(receipt.action is not None and receipt.action.transaction_id == "tx-action", "Debe conservar request→action.")
    assert_true(receipt.outcome is not None and receipt.outcome.consumed_at is None, "Recepcion y consumo son hechos distintos.")
    assert_true(ledger.mark_outcome_consumed("action-1") == OutcomeConsumptionStatus.CONSUMED, "Debe marcar consumo.")
    assert_true(ledger.get_outcome("action-1").consumed_at is not None, "El consumo debe ser consultable.")
    ledger.close()


def test_duplicate_and_conflicting_outcome_preserve_original() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger)
    first = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-first",
        success=True,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "original"},
    )
    duplicate = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-duplicate",
        success=True,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "original"},
    )
    conflict = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-conflict",
        success=False,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "distinto"},
    )
    stored = ledger.get_outcome("action-1")
    assert_true(first.status == OutcomeRecordStatus.ACCEPTED, "El original debe aceptarse.")
    assert_true(duplicate.status == OutcomeRecordStatus.DUPLICATE, "Payload identico debe ser duplicate.")
    assert_true(conflict.status == OutcomeRecordStatus.CONFLICT, "Payload distinto debe ser conflict.")
    assert_true(stored.success is True and stored.payload["mensaje_resultado"] == "original", "Nunca debe sobrescribirse el original.")
    ledger.close()


def test_unknown_id_is_not_rescued_by_exact_name() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger)
    result = ledger.record_outcome(
        action_id="wrong-id",
        outcome_transaction_id="outcome-unknown",
        success=True,
        action_name="navegar_a_objeto",
        payload={},
    )
    assert_true(result.status == OutcomeRecordStatus.UNKNOWN, "El nombre no debe rescatar un ID desconocido.")
    assert_true(ledger.get_outcome("action-1") is None, "La accion real no debe consumirse por similitud.")
    ledger.close()


def test_name_or_origin_transaction_mismatch_is_fail_safe() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger)
    wrong_name = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-name-mismatch",
        success=True,
        action_name="girar_avatar",
        payload={},
    )
    wrong_origin = ledger.record_outcome(
        action_id="action-1",
        outcome_transaction_id="outcome-origin-mismatch",
        success=True,
        action_name="navegar_a_objeto",
        originating_transaction_id="tx-equivocada",
        payload={},
    )
    assert_true(wrong_name.status == OutcomeRecordStatus.MISMATCH, "El ID identifica, pero nombre incompatible bloquea efectos.")
    assert_true(wrong_origin.status == OutcomeRecordStatus.MISMATCH, "La transaccion de origen incompatible debe observarse.")
    assert_true(ledger.get_outcome("action-1") is None, "Mismatch no debe crear outcome aceptado.")
    ledger.close()


def test_api_rejects_missing_or_blank_action_id_and_ambiguous_success() -> None:
    invalid_details = (
        {"exito": True},
        {"id_accion": " ", "exito": True},
        {"id_accion": "action-1"},
        {"id_accion": "action-1", "exito": 1},
    )
    for index, details in enumerate(invalid_details):
        try:
            EventoEntrante(
                origen="unity",
                tipo_evento="resultado_accion",
                id_transaccion=f"invalid-outcome-{index}",
                detalles=details,
            )
        except ValidationError:
            continue
        raise AssertionError(f"El outcome invalido {index} debio rechazarse.")


def test_same_transaction_retry_returns_same_action_without_pipeline_replay() -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    calls = {"behavior": 0}
    original = orchestrator.behavior.procesar_evento

    def counted(*args, **kwargs):
        calls["behavior"] += 1
        return original(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted
    event = EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion="durable-retry",
        detalles={"mensaje": "hola"},
    )
    first = orchestrator.procesar_evento(event)
    second = orchestrator.procesar_evento(event)
    assert_true(calls["behavior"] == 1, "El retry no debe ejecutar Behavior otra vez.")
    assert_true(first is second and first.id_accion == second.id_accion, "Cache RAM puede optimizar tras consultar autoridad durable.")
    ledger.close()


def test_failure_before_ram_cache_blocks_retry_and_zero_replay() -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    calls = {"behavior": 0, "working": 0}
    original = orchestrator.behavior.procesar_evento

    def counted(*args, **kwargs):
        calls["behavior"] += 1
        return original(*args, **kwargs)

    def fail_after_side_effect(**kwargs):
        calls["working"] += 1
        raise RuntimeError("fallo sintetico antes del cache RAM")

    orchestrator.behavior.procesar_evento = counted
    orchestrator.memory.agregar_accion = fail_after_side_effect
    event = EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion="pre-cache-failure",
        detalles={"mensaje": "haz una vez"},
    )
    try:
        orchestrator.procesar_evento(event)
    except RuntimeError:
        pass
    else:
        raise AssertionError("El primer intento debio fallar antes del cache.")
    try:
        orchestrator.procesar_evento(event)
    except TransactionIncompleteError:
        pass
    else:
        raise AssertionError("El retry incompleto debe bloquearse.")
    assert_true(calls == {"behavior": 1, "working": 1}, "Ningun side effect debe repetirse.")
    ledger.close()


def test_ledger_write_failure_happens_before_external_dispatch() -> None:
    orchestrator, _ = _orquestador_aislado("motor")
    real = ActionLedger(":memory:")

    class FailingLedger:
        def claim_transaction(self, *args, **kwargs):
            return real.claim_transaction(*args, **kwargs)

        @staticmethod
        def record_action(**kwargs):
            raise ActionLedgerError("write failure")

    orchestrator.action_ledger = FailingLedger()
    orchestrator._resolver_ruta_motor = lambda **kwargs: ActionResponse(
        pensamiento="",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "Cube"},
        razon="prueba",
    )
    dispatch = {"calls": 0}

    def observed_dispatch(*args, **kwargs):
        dispatch["calls"] += 1
        return "despachada", {}

    orchestrator._gestionar_ejecucion = observed_dispatch
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="ledger-write-failure",
        detalles={"mensaje": "ve al cubo", "solicita_decision": True},
    )
    try:
        orchestrator.procesar_evento(event)
    except ActionLedgerError:
        pass
    else:
        raise AssertionError("La persistencia fallida debe cerrar el flujo.")
    assert_true(dispatch["calls"] == 0, "No debe existir dispatch fisico antes de persistir id_accion.")
    real.close()


def test_unknown_and_duplicate_outcomes_do_not_reapply_consumers() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    _claim_action(ledger)
    calls = {"behavior": 0}
    original = orchestrator.behavior.procesar_evento

    def counted(*args, **kwargs):
        calls["behavior"] += 1
        return original(*args, **kwargs)

    orchestrator.behavior.procesar_evento = counted
    unknown = orchestrator.procesar_evento(_outcome_event("unknown-event", "unknown-action"))
    first = orchestrator.procesar_evento(_outcome_event("accepted-event", "action-1"))
    duplicate = orchestrator.procesar_evento(_outcome_event("duplicate-event", "action-1"))
    assert_true(unknown is None and first is None and duplicate is None, "Outcomes son receipts, no nuevas acciones.")
    assert_true(calls["behavior"] == 1, "Solo el outcome aceptado debe llegar a Behavior.")
    assert_true(len(orchestrator.memory.acciones) == 1, "Duplicate/unknown no deben llegar a WorkingMemory.")
    ledger.close()


def test_late_outcome_does_not_mutate_replacement_task() -> None:
    orchestrator, _ = _orquestador_aislado("internal")
    behavior = BehaviorCore()
    behavior.iniciar_tarea("Task X")
    task_x = behavior.task_context.task_instance_id
    behavior.iniciar_tarea("Task Y")
    assert_true(behavior.task_context.task_instance_id != task_x, "Reset debe crear una instancia nueva.")
    orchestrator.behavior = behavior
    ledger = ActionLedger(":memory:")
    _attach_ledger(orchestrator, ledger)
    _claim_action(ledger, task_instance_id=task_x)
    orchestrator.procesar_evento(_outcome_event("late-outcome", "action-1"))
    assert_true(behavior.task_context.objetivo_id == "Task Y", "Task Y debe seguir activa.")
    assert_true(behavior.task_context.intentos_totales == 0, "Outcome de X no debe registrar intento en Y.")
    assert_true(ledger.get_outcome("action-1").consumed_at is not None, "El late outcome si queda consumido en ledger.")
    ledger.close()


def test_reordered_outcomes_resolve_only_by_action_id() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger, transaction_id="tx-a", action_id="action-a")
    _claim_action(ledger, transaction_id="tx-b", action_id="action-b", action_name="girar_avatar")
    result_b = ledger.record_outcome(
        action_id="action-b",
        outcome_transaction_id="outcome-b",
        success=False,
        action_name="girar_avatar",
        payload={},
    )
    result_a = ledger.record_outcome(
        action_id="action-a",
        outcome_transaction_id="outcome-a",
        success=True,
        action_name="navegar_a_objeto",
        payload={},
    )
    assert_true(result_b.action.transaction_id == "tx-b", "B debe resolver B aunque llegue primero.")
    assert_true(result_a.action.transaction_id == "tx-a", "A debe resolver A aunque llegue despues.")
    ledger.close()


def test_alias_candidate_lookup_is_exact_without_recency_fallback() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator._alias_candidates_pendientes = OrderedDict(
        (
            ("action-a", {"id_accion": "action-a", "action": "navegar_a_objeto"}),
            ("action-b", {"id_accion": "action-b", "action": "navegar_a_objeto"}),
        )
    )
    orchestrator._alias_candidates_lock = threading.Lock()
    exact = orchestrator._buscar_alias_pendiente_para_resultado(
        {"id_accion": "action-a", "accion": "navegar_a_objeto"}
    )
    duplicate = orchestrator._buscar_alias_pendiente_para_resultado(
        {"id_accion": "action-a", "accion": "navegar_a_objeto"}
    )
    unknown = orchestrator._buscar_alias_pendiente_para_resultado(
        {"id_accion": "action-c", "accion": "navegar_a_objeto"}
    )
    missing = orchestrator._buscar_alias_pendiente_para_resultado(
        {"accion": "navegar_a_objeto", "nombre_objeto": "Cube"}
    )
    assert_true(exact is not None and exact["id_accion"] == "action-a", "Solo A debe consumirse.")
    assert_true(duplicate is None and unknown is None and missing is None, "No debe existir fallback heuristico.")
    assert_true(list(orchestrator._alias_candidates_pendientes) == ["action-b"], "B debe permanecer intacta.")


def test_restart_preserves_transaction_action_and_same_id() -> None:
    with TemporaryDirectory(prefix="root003-restart-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first = ActionLedger(path)
        _claim_action(first, transaction_id="restart-tx", action_id="restart-action")
        first.close()
        second = ActionLedger(path)
        claim = second.claim_transaction("restart-tx", origin="unity", event_type="comando_usuario")
        action = second.get_action_for_transaction("restart-tx")
        assert_true(claim.status == TransactionClaimStatus.COMPLETED, "Restart debe conservar completion.")
        assert_true(action is not None and action.action_id == "restart-action", "Restart no debe generar action B.")
        second.close()


def test_restart_preserves_duplicate_outcome_detection() -> None:
    with TemporaryDirectory(prefix="root003-outcome-restart-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first = ActionLedger(path)
        _claim_action(first)
        accepted = first.record_outcome(
            action_id="action-1",
            outcome_transaction_id="outcome-a",
            success=True,
            action_name="navegar_a_objeto",
            payload={"mensaje_resultado": "ok"},
        )
        first.mark_outcome_consumed("action-1")
        first.close()
        second = ActionLedger(path)
        duplicate = second.record_outcome(
            action_id="action-1",
            outcome_transaction_id="outcome-b",
            success=True,
            action_name="navegar_a_objeto",
            payload={"mensaje_resultado": "ok"},
        )
        assert_true(accepted.status == OutcomeRecordStatus.ACCEPTED, "Fixture debe aceptar el original.")
        assert_true(duplicate.status == OutcomeRecordStatus.DUPLICATE, "Restart debe recordar el duplicate.")
        assert_true(duplicate.outcome.consumed_at is not None, "Debe recordar que ya produjo efectos internos.")
        second.close()


def test_outcome_attempt_classification_is_consultable_after_restart() -> None:
    with TemporaryDirectory(prefix="root003-attempt-restart-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first = ActionLedger(path)
        unknown = first.record_outcome(
            action_id="unknown-action",
            outcome_transaction_id="outcome-unknown",
            success=True,
            action_name="navegar_a_objeto",
            payload={"mensaje_resultado": "sin action"},
        )
        assert_true(unknown.status == OutcomeRecordStatus.UNKNOWN, "Debe clasificar UNKNOWN.")
        first.close()

        second = ActionLedger(path)
        attempt = second.get_outcome_attempt("outcome-unknown")
        assert_true(attempt is not None, "El intento debe sobrevivir restart.")
        assert_true(
            attempt.classification == OutcomeRecordStatus.UNKNOWN
            and attempt.action_id == "unknown-action",
            "La clasificacion durable debe conservar su ID exacto.",
        )
        second.close()


def test_outcome_transaction_reuse_with_another_action_is_conflict() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger, transaction_id="tx-a", action_id="action-a")
    _claim_action(ledger, transaction_id="tx-b", action_id="action-b")
    first = ledger.record_outcome(
        action_id="action-a",
        outcome_transaction_id="outcome-shared",
        success=True,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "A"},
    )
    reused = ledger.record_outcome(
        action_id="action-b",
        outcome_transaction_id="outcome-shared",
        success=True,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "A"},
    )
    assert_true(first.status == OutcomeRecordStatus.ACCEPTED, "El primer receipt debe ganar.")
    assert_true(
        reused.status == OutcomeRecordStatus.CONFLICT,
        "Una transaccion de outcome no puede migrar a otra action.",
    )
    assert_true(ledger.get_outcome("action-b") is None, "No debe crear outcome para B.")
    ledger.close()


def test_ledger_close_is_idempotent() -> None:
    ledger = ActionLedger(":memory:")
    ledger.close()
    ledger.close()


def test_concurrent_same_transaction_has_one_winner() -> None:
    ledger = ActionLedger(":memory:")
    barrier = threading.Barrier(2)
    statuses = []

    def worker() -> None:
        barrier.wait(timeout=2)
        statuses.append(
            ledger.claim_transaction("concurrent-tx", origin="unity", event_type="comando_usuario").status
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert_true(all(not thread.is_alive() for thread in threads), "La concurrencia no debe colgarse.")
    assert_true(statuses.count(TransactionClaimStatus.CREATED) == 1, "Solo un thread debe crear el claim.")
    assert_true(statuses.count(TransactionClaimStatus.INCOMPLETE) == 1, "El otro debe observar el claim existente.")
    ledger.close()


def test_concurrent_same_action_registration_is_atomic() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("concurrent-action-tx", origin="unity", event_type="comando_usuario")
    barrier = threading.Barrier(2)
    statuses = []

    def worker() -> None:
        barrier.wait(timeout=2)
        statuses.append(
            ledger.record_action(
                action_id="concurrent-action",
                transaction_id="concurrent-action-tx",
                action_name="navegar_a_objeto",
                parameters={},
                requires_confirmation=False,
                task_instance_id=None,
                intended_client="unity",
                outcome_expected=True,
            ).status
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert_true(statuses.count(ActionRecordStatus.CREATED) == 1, "Solo un insert debe crear la accion.")
    assert_true(statuses.count(ActionRecordStatus.EXISTING) == 1, "El segundo debe observar identidad estable.")
    ledger.close()


def test_concurrent_same_action_outcome_accepts_once() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger)
    barrier = threading.Barrier(2)
    statuses = []

    def worker(index: int) -> None:
        barrier.wait(timeout=2)
        statuses.append(
            ledger.record_outcome(
                action_id="action-1",
                outcome_transaction_id=f"concurrent-outcome-{index}",
                success=True,
                action_name="navegar_a_objeto",
                payload={},
            ).status
        )

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert_true(statuses.count(OutcomeRecordStatus.ACCEPTED) == 1, "Solo un outcome debe aceptarse.")
    assert_true(statuses.count(OutcomeRecordStatus.DUPLICATE) == 1, "El otro debe ser duplicate.")
    ledger.close()


def test_operational_payload_excludes_secrets_private_thought_and_prompt() -> None:
    ledger = ActionLedger(":memory:")
    ledger.claim_transaction("privacy-tx", origin="unity", event_type="comando_usuario")
    ledger.record_action(
        action_id="privacy-action",
        transaction_id="privacy-tx",
        action_name="navegar_a_objeto",
        parameters={
            "nombre_objeto": "Cube",
            "api_key": "SECRET_VALUE",
            "prompt": "PRIVATE_PROMPT",
            "pensamiento_privado": "PRIVATE_THOUGHT",
        },
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
    )
    stored = ledger.get_action("privacy-action")
    assert_true(stored.parameters == {"nombre_objeto": "Cube"}, "Solo parametros operacionales deben persistirse.")
    ledger.close()


def test_action_adapter_preserves_id_through_durable_roundtrip() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    outgoing = orchestrator._a_accion_saliente(
        ActionResponse(
            pensamiento="no persistir",
            accion="navegar_a_objeto",
            parametros={"nombre_objeto": "Cube"},
            razon="razon publica",
        ),
        "exploradora",
    )
    payload = orchestrator._serializar_accion_durable(outgoing)
    restored = orchestrator._restaurar_accion_desde_ledger(payload)
    assert_true(restored.id_accion == outgoing.id_accion, "El adapter no debe regenerar id_accion.")
    assert_true(restored.nombre == outgoing.nombre and restored.parametros == outgoing.parametros, "La accion operacional debe reconstruirse.")
    restored_payload = restored.model_dump(mode="python")
    assert_true(
        "pensamiento_privado" not in restored_payload and "razonamiento" not in restored_payload,
        "El replay durable no debe reconstruir campos cognitivos ni siquiera vacios.",
    )


def test_sandbox_synchronous_outcome_is_recorded_and_consumed() -> None:
    ledger = ActionLedger(":memory:")
    _claim_action(ledger, transaction_id="sandbox-tx", action_id="sandbox-action")
    receipt = ledger.record_outcome(
        action_id="sandbox-action",
        outcome_transaction_id="sandbox:sandbox-action",
        success=True,
        action_name="navegar_a_objeto",
        payload={"mensaje_resultado": "tool result"},
    )
    consumed = ledger.mark_outcome_consumed("sandbox-action")
    assert_true(receipt.status == OutcomeRecordStatus.ACCEPTED, "ToolResult sincronico debe tener outcome canonico.")
    assert_true(consumed == OutcomeConsumptionStatus.CONSUMED, "El receipt sincronico debe cerrarse localmente.")
    ledger.close()


def test_no_action_completion_survives_restart_without_phantom_action() -> None:
    with TemporaryDirectory(prefix="root003-none-") as directory:
        path = Path(directory) / "ledger.sqlite3"
        first = ActionLedger(path)
        first.claim_transaction("no-action", origin="unity", event_type="resultado_accion")
        first.complete_transaction("no-action", None)
        first.close()
        second = ActionLedger(path)
        claim = second.claim_transaction("no-action", origin="unity", event_type="resultado_accion")
        assert_true(claim.status == TransactionClaimStatus.COMPLETED, "NO ACTION debe ser completion durable.")
        assert_true(claim.transaction.action_id is None and claim.transaction.response_action is None, "No debe crear action fantasma.")
        second.close()


def test_unity_source_roundtrips_action_identity_without_last_action_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    agent = (root / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
    movement = (root / "unity_client" / "MovementController.cs").read_text(encoding="utf-8-sig")
    dto = (root / "unity_client" / "DTOs.cs").read_text(encoding="utf-8-sig")
    assert_true('detalles["id_accion"] = contexto.IdAccion;' in agent, "Cada resultado debe devolver id_accion.")
    assert_true('detalles["id_transaccion_origen"] = contexto.IdTransaccionOrigen;' in agent, "Debe conservar la request de origen.")
    assert_true("respuesta => ProcesarRespuestaDelCerebro(evento.id_transaccion, respuesta)" in agent, "La response debe viajar con request exacta.")
    assert_true("Action<string, bool, string> OnMovimientoCompletado" in movement, "Movimiento debe devolver actionId exacto.")
    assert_true("UltimaAccionMovimiento" not in agent + movement, "No debe existir correlacion por ultima accion.")
    assert_true("public string id_accion;" in dto, "El DTO de accion debe cruzar id_accion.")


def test_action_ledger_is_not_chroma_or_audittrace_storage() -> None:
    source = Path(__file__).with_name("action_ledger.py").read_text(encoding="utf-8-sig").lower()
    assert_true("chromadb" not in source and "audittrace" not in source, "El ledger debe ser operacional e independiente.")
    assert_true("import sqlite3" in source, "SQLite stdlib debe ser el storage elegido.")


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"[OK] ROOT-003: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
