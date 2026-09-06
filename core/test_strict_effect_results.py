"""Pruebas permanentes del contrato booleano/result explícito de ROOT-006."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from ai_engine.fuzzy_object_resolver import FuzzyObjectResolver
from ai_engine.learned_alias_candidates import (
    LearnedAliasCandidate,
    build_learned_alias_candidate,
)
from api.schemas import AccionSaliente, EventoEntrante
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from core.task_context import TaskContext
from memory.memory_manager import MemoryManager
from tools.base import ToolResult
from tools.executor import ActionExecutor
from tools.registry import ToolRegistry


INVALID_BOOLEANS = (
    "true",
    "false",
    "TRUE",
    "FALSE",
    1,
    0,
    1.0,
    0.0,
    None,
    "",
    [],
    {},
)


class _EmptyParams(BaseModel):
    pass


class _ListingClient:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata

    @staticmethod
    def combinar_where(base, extra):
        return {"$and": [base, extra]}

    def listar(self, **_kwargs):
        return {"ids": ["synthetic-memory"], "metadatas": [self.metadata]}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _execute_fake(
    returned: Any = None,
    *,
    exception: BaseException | None = None,
    return_annotation: object | None = None,
):
    name = "__root_006_probe__"
    previous = ToolRegistry._tools.get(name)
    calls: list[object] = []

    def handler(params: _EmptyParams):
        calls.append(params)
        if exception is not None:
            raise exception
        return returned

    if return_annotation is not None:
        handler.__annotations__["return"] = return_annotation

    ToolRegistry._tools[name] = {
        "func": handler,
        "description": "ROOT-006 synthetic handler",
        "schema": _EmptyParams,
        "requires_confirmation": False,
    }
    try:
        result = ActionExecutor.ejecutar(
            AccionSaliente(id_accion="root-006", nombre=name, parametros={})
        )
        return result, calls
    finally:
        if previous is None:
            ToolRegistry._tools.pop(name, None)
        else:
            ToolRegistry._tools[name] = previous


def _memory_reader(metadata: dict[str, Any]) -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.client = _ListingClient(metadata)
    manager._alias_aprendidos_guardados = set()
    manager._memorias_afectivas_guardadas = set()
    return manager


def _alias_metadata(*, approved: Any = None, include: bool = True) -> dict[str, Any]:
    metadata = {"object_id": "Tree_01", "human_concept": "arbol"}
    if include:
        metadata["approved"] = approved
    return metadata


def _affect_metadata(*, confirmed: Any = None, include: bool = True) -> dict[str, Any]:
    metadata = {
        "object_id": "Tree_01",
        "object_class": "tree",
        "affect": "alivio",
        "weight": 0.8,
        "source": "synthetic",
    }
    if include:
        metadata["confirmed"] = confirmed
    return metadata


def _alias_resolution() -> dict[str, Any]:
    return {
        "status": "ready",
        "action": "navegar_a_objeto",
        "target_id": "Tree_01",
        "target_display_name": "Tree_01",
        "confidence": 100.0,
        "candidates": [
            {
                "target_id": "Tree_01",
                "display_name": "Tree_01",
                "confidence": 100.0,
                "score_components": {"name_score": 100.0, "final_score": 100.0},
                "known_aliases": ["Tree_01"],
            }
        ],
    }


def test_tool_result_accepts_real_booleans() -> None:
    assert_true(ToolResult(exito=True).exito is True, "True debe conservarse.")
    assert_true(ToolResult(exito=False).exito is False, "False debe conservarse.")


def test_tool_result_rejects_every_ambiguous_boolean() -> None:
    for value in INVALID_BOOLEANS:
        try:
            ToolResult(exito=value)  # type: ignore[arg-type]
        except TypeError:
            continue
        raise AssertionError(f"ToolResult debio rechazar {value!r}.")
    try:
        ToolResult()  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("ToolResult.exito no debe tener un success implícito.")


def test_handler_tool_results_preserve_success_and_failure() -> None:
    successful, success_calls = _execute_fake(ToolResult(exito=True, mensaje="ok"))
    failed, failure_calls = _execute_fake(ToolResult(exito=False, mensaje="fallo"))
    assert_true(successful.exito is True, "ToolResult(True) debe conservar éxito.")
    assert_true(failed.exito is False, "ToolResult(False) debe conservar fallo.")
    assert_true(len(success_calls) == len(failure_calls) == 1, "Cada handler debe ejecutarse una vez.")


def test_raw_boolean_handler_returns_are_contract_failures() -> None:
    for value in (True, False):
        result, calls = _execute_fake(value)
        assert_true(result.exito is False, "Un bool crudo no declara el resultado del efecto.")
        assert_true(result.error == "Contrato de retorno invalido.", "Debe exponer fallo contractual.")
        assert_true(len(calls) == 1, "El adapter no debe reintentar el handler.")


def test_raw_none_handler_return_is_contract_failure() -> None:
    result, calls = _execute_fake(None)
    assert_true(result.exito is False, "None no puede significar éxito implícito.")
    assert_true(len(calls) == 1, "None no debe provocar una segunda ejecución.")


def test_raw_container_handler_returns_are_contract_failures() -> None:
    for value in ({}, {"value": 1}, []):
        result, calls = _execute_fake(value)
        assert_true(result.exito is False, "Un payload sin contrato no decide éxito.")
        assert_true(len(calls) == 1, "El payload inválido no debe reintentarse.")


def test_raw_string_handler_contract_requires_nonempty_message() -> None:
    successful, success_calls = _execute_fake("ok", return_annotation=str)
    assert_true(successful.exito is True and successful.mensaje == "ok", "El mensaje legacy válido debe conservarse.")
    assert_true(len(success_calls) == 1, "El mensaje válido debe ejecutar una vez.")
    for value in ("", "   ", "true", "false", "TRUE", "FALSE", " false "):
        failed, calls = _execute_fake(value, return_annotation=str)
        assert_true(failed.exito is False, "Un mensaje vacío/booleano no prueba éxito.")
        assert_true(len(calls) == 1, "El mensaje inválido no debe reintentarse.")

    untyped, calls = _execute_fake("ok")
    assert_true(untyped.exito is False, "Un string sin contrato declarado debe fallar cerrado.")
    assert_true(len(calls) == 1, "El string sin contrato no debe reintentarse.")


def test_handler_exception_is_a_single_controlled_failure() -> None:
    result, calls = _execute_fake(exception=RuntimeError("synthetic"))
    assert_true(result.exito is False, "La excepción debe convertirse en fallo.")
    assert_true(len(calls) == 1, "La excepción no debe reejecutar el efecto.")


def test_resultado_accion_accepts_bool_or_absent() -> None:
    for value in (True, False):
        event = EventoEntrante(
            origen="unity",
            tipo_evento="resultado_accion",
            id_transaccion=f"strict-{value}",
            detalles={"exito": value},
        )
        assert_true(event.detalles["exito"] is value, "El bool debe conservar identidad.")
    absent = EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion="strict-absent",
        detalles={},
    )
    assert_true("exito" not in absent.detalles, "Ausencia no debe inventar resultado.")


def test_resultado_accion_rejects_every_ambiguous_boolean() -> None:
    for value in INVALID_BOOLEANS:
        try:
            EventoEntrante(
                origen="unity",
                tipo_evento="resultado_accion",
                id_transaccion="strict-invalid",
                detalles={"exito": value},
            )
        except ValidationError:
            continue
        raise AssertionError(f"resultado_accion debio rechazar {value!r}.")


def test_confirmation_flags_require_real_booleans() -> None:
    for value in (True, False):
        action = AccionSaliente(
            id_accion="strict-confirmation",
            nombre="probe",
            requiere_confirmacion=value,
        )
        assert_true(action.requiere_confirmacion is value, "El flag booleano debe conservarse.")
    for value in INVALID_BOOLEANS:
        try:
            AccionSaliente(
                id_accion="strict-confirmation-invalid",
                nombre="probe",
                requiere_confirmacion=value,
            )
        except ValidationError:
            continue
        raise AssertionError(f"requiere_confirmacion debio rechazar {value!r}.")


def test_registry_and_executor_reject_ambiguous_confirmation_metadata() -> None:
    try:
        ToolRegistry.register(
            name="__root_006_invalid_registration__",
            description="probe",
            params_schema=_EmptyParams,
            requires_confirmation="false",  # type: ignore[arg-type]
        )
    except TypeError:
        pass
    else:
        raise AssertionError("ToolRegistry debió rechazar requires_confirmation ambiguo.")

    name = "__root_006_invalid_registry_metadata__"
    previous = ToolRegistry._tools.get(name)
    calls: list[object] = []

    def handler(params: _EmptyParams) -> ToolResult:
        calls.append(params)
        return ToolResult(exito=True)

    ToolRegistry._tools[name] = {
        "func": handler,
        "description": "probe",
        "schema": _EmptyParams,
        "requires_confirmation": "false",
    }
    try:
        result = ActionExecutor.ejecutar(
            AccionSaliente(id_accion="strict-registry", nombre=name)
        )
    finally:
        if previous is None:
            ToolRegistry._tools.pop(name, None)
        else:
            ToolRegistry._tools[name] = previous
    assert_true(result.exito is False and not calls, "Metadata ambigua debe bloquear antes del handler.")


def test_behavior_core_records_only_real_boolean_results() -> None:
    for value in (True, False):
        behavior = BehaviorCore()
        behavior.iniciar_tarea("root-006")
        behavior.procesar_evento("resultado_accion", {"accion": "navegar_a_objeto", "exito": value})
        history = behavior.task_context.historial if behavior.task_context else []
        assert_true(len(history) == 1 and history[0]["exito"] is value, "Task debe conservar el bool exacto.")

    for value in INVALID_BOOLEANS:
        behavior = BehaviorCore()
        behavior.iniciar_tarea("root-006")
        behavior.procesar_evento("resultado_accion", {"accion": "navegar_a_objeto", "exito": value})
        history = behavior.task_context.historial if behavior.task_context else []
        assert_true(not history, "Un resultado inválido no debe mutar TaskContext.")


def test_task_boundaries_reject_non_boolean_results() -> None:
    context = TaskContext("root-006")
    try:
        context.registrar_intento("navegar_a_objeto", "false")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("TaskContext debió rechazar el string false.")
    assert_true(context.intentos_totales == 0 and not context.historial, "El rechazo debe ocurrir antes de mutar.")

    behavior = BehaviorCore()
    try:
        behavior.finalizar_tarea("false")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("BehaviorCore.finalizar_tarea debió exigir bool.")


def test_alias_reader_approves_only_true_boolean() -> None:
    accepted = _memory_reader(_alias_metadata(approved=True)).obtener_aliases_aprendidos()
    assert_true(accepted == {"Tree_01": ["arbol"]}, "True debe aprobar el alias.")
    rejected_values = (False, *INVALID_BOOLEANS)
    for value in rejected_values:
        rejected = _memory_reader(_alias_metadata(approved=value)).obtener_aliases_aprendidos()
        assert_true(rejected == {}, f"Alias ambiguo/no aprobado debió rechazarse: {value!r}.")
    missing = _memory_reader(_alias_metadata(include=False)).obtener_aliases_aprendidos()
    assert_true(missing == {}, "Approval ausente debe fallar cerrado.")


def test_affective_reader_approves_only_true_boolean() -> None:
    accepted = _memory_reader(_affect_metadata(confirmed=True)).obtener_memorias_afectivas_espaciales()
    assert_true("Tree_01" in accepted, "True debe activar la memoria afectiva.")
    rejected_values = (False, *INVALID_BOOLEANS)
    for value in rejected_values:
        rejected = _memory_reader(_affect_metadata(confirmed=value)).obtener_memorias_afectivas_espaciales()
        assert_true(rejected == {}, f"Confirmed ambiguo/no aprobado debió rechazarse: {value!r}.")
    missing = _memory_reader(_affect_metadata(include=False)).obtener_memorias_afectivas_espaciales()
    assert_true(missing == {}, "Confirmed ausente debe fallar cerrado.")


def test_affective_writer_rejects_invalid_confirmation_before_side_effect() -> None:
    manager = object.__new__(MemoryManager)
    manager._memorias_afectivas_guardadas = set()
    calls: list[dict[str, Any]] = []
    manager._memoria_afectiva_ya_persistida = lambda **_kwargs: False
    manager.guardar_regla = lambda **kwargs: calls.append(kwargs) or "memory-id"

    invalid = manager.guardar_memoria_afectiva_espacial(
        object_id="Tree_01",
        affect="alivio",
        weight=0.8,
        confirmed="false",  # type: ignore[arg-type]
    )
    assert_true(invalid is None and calls == [], "El tipo inválido no debe escribir.")

    try:
        manager.guardar_memoria_afectiva_espacial(
            object_id="Tree_01",
            affect="alivio",
            weight=0.8,
        )
    except TypeError:
        pass
    else:
        raise AssertionError("El writer afectivo debe exigir confirmation explícita.")
    assert_true(calls == [], "La ausencia de confirmation no debe escribir.")

    valid = manager.guardar_memoria_afectiva_espacial(
        object_id="Tree_01",
        affect="alivio",
        weight=0.8,
        confirmed=False,
    )
    assert_true(valid == "memory-id", "False booleano sigue siendo un valor persistible.")
    assert_true(calls[0]["extra"]["confirmed"] is False, "El writer debe conservar False real.")


def test_alias_candidate_approval_and_action_success_are_strict() -> None:
    approved = LearnedAliasCandidate.from_dict(
        {"object_id": "Tree_01", "human_concept": "arbol", "confidence": 1.0, "approved": True}
    )
    assert_true(approved is not None and approved.approved is True, "True debe conservar approval.")
    missing = LearnedAliasCandidate.from_dict(
        {"object_id": "Tree_01", "human_concept": "arbol", "confidence": 1.0}
    )
    assert_true(missing is not None and missing.approved is False, "Approval ausente debe quedar no aprobado.")
    for value in INVALID_BOOLEANS:
        candidate = LearnedAliasCandidate.from_dict(
            {"object_id": "Tree_01", "human_concept": "arbol", "confidence": 1.0, "approved": value}
        )
        assert_true(candidate is not None and candidate.approved is False, "Valor ambiguo debe quedar no aprobado.")
        learned = build_learned_alias_candidate(
            user_query="ve al arbol",
            resolution=_alias_resolution(),
            action_success=value,  # type: ignore[arg-type]
            action="navegar_a_objeto",
        )
        assert_true(learned is None, "Un success ambiguo no debe habilitar aprendizaje.")

    try:
        LearnedAliasCandidate(
            object_id="Tree_01",
            human_concept="arbol",
            confidence=1.0,
            approved="false",  # type: ignore[arg-type]
        )
    except TypeError:
        pass
    else:
        raise AssertionError("El constructor del candidato debió exigir bool.")


def test_alias_writer_emits_real_true_approval() -> None:
    manager = object.__new__(MemoryManager)
    manager._alias_aprendidos_guardados = set()
    manager._alias_aprendido_ya_persistido = lambda _candidate: False
    calls: list[dict[str, Any]] = []
    manager.guardar_regla = lambda **kwargs: calls.append(kwargs) or "alias-id"
    candidate = LearnedAliasCandidate(
        object_id="Tree_01",
        human_concept="arbol",
        confidence=1.0,
    )

    result = manager.guardar_alias_aprendido(candidate)
    assert_true(result == "alias-id", "El alias válido debe persistirse.")
    assert_true(calls[0]["extra"]["approved"] is True, "El writer debe emitir True booleano.")


def test_fuzzy_affective_bonus_requires_true_boolean_confirmation() -> None:
    valid_bonus = FuzzyObjectResolver._affective_context_bonus(
        [{"affect": "alivio", "weight": 1.0, "confirmed": True}],
        action=None,
    )
    assert_true(valid_bonus > 0.0, "True debe permitir el bonus afectivo.")
    for value in (False, *INVALID_BOOLEANS):
        bonus = FuzzyObjectResolver._affective_context_bonus(
            [{"affect": "alivio", "weight": 1.0, "confirmed": value}],
            action=None,
        )
        assert_true(bonus == 0.0, "Un confirmed ambiguo no debe influir en resolución.")
    missing = FuzzyObjectResolver._affective_context_bonus(
        [{"affect": "alivio", "weight": 1.0}],
        action=None,
    )
    assert_true(missing == 0.0, "Confirmed ausente no debe influir en resolución.")


def test_invalid_local_tool_result_maps_to_failed_execution_without_retry() -> None:
    name = "__root_006_orchestrator_probe__"
    previous = ToolRegistry._tools.get(name)
    calls: list[object] = []

    def handler(params: _EmptyParams):
        calls.append(params)
        return False

    ToolRegistry._tools[name] = {
        "func": handler,
        "description": "ROOT-006 synthetic handler",
        "schema": _EmptyParams,
        "requires_confirmation": False,
    }
    try:
        orchestrator = object.__new__(Orchestrator)
        event = EventoEntrante(
            origen="sandbox",
            tipo_evento="comando_usuario",
            id_transaccion="root-006-orchestrator",
            detalles={"mensaje": "probe"},
        )
        action = AccionSaliente(id_accion="root-006-action", nombre=name, parametros={})
        status, details = orchestrator._gestionar_ejecucion(event, action)
    finally:
        if previous is None:
            ToolRegistry._tools.pop(name, None)
        else:
            ToolRegistry._tools[name] = previous

    assert_true(status == "error", "El retorno inválido debe llegar como fallo al Orchestrator.")
    assert_true(details is not None and details["error"] is not None, "El fallo debe ser observable.")
    assert_true(Orchestrator._mapear_resultado_ejecucion_audit(status) == "failed", "AuditTrace debe mapearlo a failed.")
    assert_true(len(calls) == 1, "El flujo no debe repetir el handler para auditar.")
