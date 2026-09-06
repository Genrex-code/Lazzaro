"""Pruebas permanentes del contrato numérico físico de ROOT-002."""

from __future__ import annotations

import math
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ai_engine.motor_engine import MotorEngine
from ai_engine.motor_schemas import (
    ApplyForceParams,
    MoveAwayParams,
    NavigateToObjectParams,
    NavigateToPositionParams,
    ReleaseObjectParams,
    RotateAvatarParams,
)
from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.action_ledger import TurnProcessingError
from core.orchestrator import Orchestrator
from core.tool_policy import ToolPolicy
from terminal_client.test_chat import _orquestador_aislado
from tools.executor import ActionExecutor
from tools.registry import ToolRegistry


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _physical_field_factories() -> list[tuple[str, Callable[[Any], BaseModel]]]:
    return [
        ("navegar_a_objeto.distancia", lambda value: NavigateToObjectParams(nombre_objeto="Cube", distancia=value)),
        ("navegar_a_posicion.x", lambda value: NavigateToPositionParams(x=value, y=0, z=0)),
        ("navegar_a_posicion.y", lambda value: NavigateToPositionParams(x=0, y=value, z=0)),
        ("navegar_a_posicion.z", lambda value: NavigateToPositionParams(x=0, y=0, z=value)),
        ("soltar_objeto.fuerza_lanzamiento", lambda value: ReleaseObjectParams(fuerza_lanzamiento=value)),
        ("alejarse_de_objeto.distancia", lambda value: MoveAwayParams(nombre_objeto="Cube", distancia=value)),
        ("girar_avatar.grados", lambda value: RotateAvatarParams(grados=value)),
        (
            "aplicar_fuerza.vector_x",
            lambda value: ApplyForceParams(nombre_objeto="Cube", vector_x=value, vector_y=0, vector_z=0, fuerza=1),
        ),
        (
            "aplicar_fuerza.vector_y",
            lambda value: ApplyForceParams(nombre_objeto="Cube", vector_x=0, vector_y=value, vector_z=0, fuerza=1),
        ),
        (
            "aplicar_fuerza.vector_z",
            lambda value: ApplyForceParams(nombre_objeto="Cube", vector_x=0, vector_y=0, vector_z=value, fuerza=1),
        ),
        (
            "aplicar_fuerza.fuerza",
            lambda value: ApplyForceParams(nombre_objeto="Cube", vector_x=1, vector_y=0, vector_z=0, fuerza=value),
        ),
    ]


def _assert_validation_error(factory: Callable[[Any], BaseModel], value: Any, label: str) -> None:
    try:
        factory(value)
    except ValidationError:
        return
    raise AssertionError(f"{label} debió ser rechazado: {value!r}")


def test_nan_positive_inf_and_negative_inf_are_rejected_for_every_physical_field() -> None:
    for field_name, factory in _physical_field_factories():
        for value in (float("nan"), float("inf"), float("-inf")):
            _assert_validation_error(factory, value, field_name)


def test_nonfinite_strings_are_rejected_for_every_physical_field() -> None:
    spellings = ("nan", "NaN", "inf", "+inf", "-inf", "infinity", "+Infinity", "-Infinity")
    for field_name, factory in _physical_field_factories():
        for value in spellings:
            _assert_validation_error(factory, value, field_name)


def test_bool_is_rejected_for_every_physical_field() -> None:
    for field_name, factory in _physical_field_factories():
        for value in (True, False):
            _assert_validation_error(factory, value, field_name)


def test_finite_numeric_strings_are_preserved_and_normalized() -> None:
    position = NavigateToPositionParams(x="1", y="-1", z="3.5")
    force = ApplyForceParams(
        nombre_objeto="Cube",
        vector_x="1",
        vector_y="-1",
        vector_z="3.5",
        fuerza="3.5",
    )

    assert_true(position.model_dump() == {"x": 1.0, "y": -1.0, "z": 3.5}, "Las coordenadas string deben normalizarse.")
    assert_true(force.vector_x == 1.0 and force.vector_y == -1.0 and force.vector_z == 3.5, "El vector string debe normalizarse.")
    assert_true(force.fuerza == 3.5, "La magnitud string finita debe conservarse.")


def test_unbounded_coordinates_and_vectors_keep_finite_large_values() -> None:
    for value in (0, -0.0, 1, -1, 1e9, -1e9):
        position = NavigateToPositionParams(x=value, y=value, z=value)
        force = ApplyForceParams(
            nombre_objeto="Cube",
            vector_x=value,
            vector_y=value,
            vector_z=value,
            fuerza=1,
        )
        assert_true(math.isfinite(position.x), "Una coordenada finita válida debe conservarse.")
        assert_true(math.isfinite(force.vector_x), "Un vector finito válido debe conservarse.")


def test_existing_physical_ranges_and_defaults_are_preserved() -> None:
    accepted = (
        NavigateToObjectParams(nombre_objeto="Cube", distancia=0.1).distancia == 0.1,
        NavigateToObjectParams(nombre_objeto="Cube", distancia=10.0).distancia == 10.0,
        MoveAwayParams(nombre_objeto="Cube", distancia=0.5).distancia == 0.5,
        MoveAwayParams(nombre_objeto="Cube", distancia=5.0).distancia == 5.0,
        RotateAvatarParams(grados=-360.0).grados == -360.0,
        RotateAvatarParams(grados=360.0).grados == 360.0,
        ReleaseObjectParams(fuerza_lanzamiento=0.0).fuerza_lanzamiento == 0.0,
        ReleaseObjectParams(fuerza_lanzamiento=20.0).fuerza_lanzamiento == 20.0,
        ApplyForceParams(nombre_objeto="Cube", vector_x=1, vector_y=0, vector_z=0, fuerza=0.0001).fuerza == 0.0001,
        ApplyForceParams(nombre_objeto="Cube", vector_x=1, vector_y=0, vector_z=0, fuerza=100.0).fuerza == 100.0,
        NavigateToObjectParams(nombre_objeto="Cube").distancia is None,
        MoveAwayParams(nombre_objeto="Cube").distancia == 2.0,
        ReleaseObjectParams().fuerza_lanzamiento == 0.0,
    )
    assert_true(all(accepted), "Los límites y defaults históricos deben conservarse.")

    rejected = (
        (lambda value: NavigateToObjectParams(nombre_objeto="Cube", distancia=value), (0.0, 10.1)),
        (lambda value: MoveAwayParams(nombre_objeto="Cube", distancia=value), (0.49, 5.1)),
        (lambda value: RotateAvatarParams(grados=value), (-360.1, 360.1)),
        (lambda value: ReleaseObjectParams(fuerza_lanzamiento=value), (-0.1, 20.1)),
        (
            lambda value: ApplyForceParams(nombre_objeto="Cube", vector_x=1, vector_y=0, vector_z=0, fuerza=value),
            (0.0, 100.1),
        ),
    )
    for factory, values in rejected:
        for value in values:
            _assert_validation_error(factory, value, "rango histórico")


def test_motor_engine_blocks_invalid_physical_parameters_without_replacement() -> None:
    cases = (
        ("navegar_a_posicion", {"x": float("nan"), "y": 0, "z": 0}),
        ("navegar_a_objeto", {"nombre_objeto": "Cube", "distancia": True}),
        ("alejarse_de_objeto", {"nombre_objeto": "Cube", "distancia": "inf"}),
        ("girar_avatar", {"grados": False}),
        ("soltar_objeto", {"fuerza_lanzamiento": "nan"}),
        (
            "aplicar_fuerza",
            {"nombre_objeto": "Cube", "vector_x": 1, "vector_y": float("inf"), "vector_z": 0, "fuerza": 1},
        ),
    )
    engine = MotorEngine()
    for action, parameters in cases:
        result = engine.procesar_comando_motor(action, parameters, percepcion={})
        assert_true(result["accion"] == "none", f"{action} inválida debe quedar bloqueada.")
        assert_true(result["parametros"] == {}, "No debe sustituir el parámetro por 0/None/default.")


class _InvalidMotorReasoning:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, **kwargs) -> ActionResponse:
        self.calls += 1
        return ActionResponse(
            pensamiento="",
            accion="navegar_a_posicion",
            parametros={"x": "nan", "y": 0, "z": 0},
            razon="Fallback sintético.",
            valido=True,
        )


def test_reasoning_fallback_cannot_reconstruct_an_invalid_physical_action() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.motor_engine = MotorEngine()
    orchestrator.motor_engine.diagnosticar_comando_usuario = lambda *args, **kwargs: {
        "estado": "sin_intencion",
        "accion": None,
        "parametros": None,
    }
    orchestrator.reasoning = _InvalidMotorReasoning()
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="root-002-reasoning-fallback",
        detalles={"mensaje": "fallback sintético"},
    )

    response = orchestrator._resolver_ruta_motor(
        evento=event,
        contexto_llm={},
        memoria_texto="",
        herramientas=["navegar_a_posicion"],
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
        triage_decision={"intent": "MOTOR_SIMPLE", "route": "motor"},
    )

    assert_true(orchestrator.reasoning.calls == 1, "El fallback controlado debe ejecutarse una vez.")
    assert_true(response.accion == "none" and response.valido is False, "Motor debe revalidar y bloquear el fallback no finito.")
    assert_true(response.parametros == {}, "El fallback no debe reconstruirse con 0/None/default.")


class _PhysicalRegistry:
    @staticmethod
    def listar_nombres() -> list[str]:
        return ["navegar_a_posicion"]

    @staticmethod
    def describir_herramientas(nombres: list[str]) -> dict[str, str]:
        return {name: name for name in nombres}

    @staticmethod
    def get_tool(nombre: str) -> dict[str, Any] | None:
        if nombre == "navegar_a_posicion":
            return {"requires_confirmation": False}
        return None


class _RecordingWriter:
    def __init__(self) -> None:
        self.traces = []

    def write(self, trace) -> bool:
        self.traces.append(trace)
        return True


def _run_audited_direct_motor(parameters: dict[str, Any], transaction_id: str):
    orchestrator, calls = _orquestador_aislado("motor")
    writer = _RecordingWriter()
    orchestrator.audit_writer = writer
    orchestrator.tool_registry = _PhysicalRegistry()

    def direct_motor(**kwargs) -> ActionResponse:
        calls["motor"] += 1
        return ActionResponse(
            pensamiento="",
            accion="navegar_a_posicion",
            parametros=parameters,
            razon="Producer directo de prueba.",
            valido=True,
        )

    orchestrator._resolver_ruta_motor = direct_motor
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": "ve a la posición", "solicita_decision": True},
    )
    try:
        outgoing = orchestrator.procesar_evento(event)
    except TurnProcessingError as exc:
        outgoing = exc
    return outgoing, writer.traces, calls


def test_critical_direct_producer_is_blocked_by_the_common_response_guard() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = _PhysicalRegistry()
    event = EventoEntrante(
        origen="unity",
        tipo_evento="alerta_dolor_fisico",
        id_transaccion="root-002-critical",
        detalles={
            "posicion_lazzaro": {"x": float("nan"), "y": 0, "z": 0},
            "direccion_escape": "atras",
            "distancia_escape": 1.5,
        },
    )

    direct = orchestrator._resolver_interrupcion_critica(event)
    guarded = orchestrator._sanitizar_action_response(direct)
    outgoing = orchestrator._a_accion_saliente(guarded, "exploradora")
    status, _ = orchestrator._gestionar_ejecucion(event, outgoing, guarded)

    assert_true(direct.accion == "navegar_a_posicion", "El fixture debe cubrir el producer directo que omite MotorEngine.")
    assert_true(guarded.accion == "none" and guarded.valido is False, "La barrera común debe bloquear el valor no finito.")
    assert_true(outgoing.parametros == {} and status == "sin_accion", "La interrupción inválida debe producir cero dispatch.")


def test_orchestrator_blocks_direct_or_fallback_nonfinite_before_dispatch() -> None:
    outgoing, traces, calls = _run_audited_direct_motor(
        {"x": float("nan"), "y": 0, "z": 0},
        "root-002-invalid",
    )
    stages = [trace.stage for trace in traces]

    assert_true(calls["motor"] == 1, "El producer debe ejecutarse una sola vez.")
    assert_true(isinstance(outgoing, TurnProcessingError), "La barrera final debe clasificar INVALID, no devolver success.")
    assert_true(not [trace for trace in traces if trace.stage in {"decision", "execution"}], "No debe fabricar decision ni dispatch para nonfinite.")
    assert_true(
        stages == ["event_start", "triage", "memory", "error", "event_end"],
        "AuditTrace v1 debe cerrar el fallo sin stages de exito.",
    )
    assert_true(
        next(trace for trace in traces if trace.stage == "error").error
        == {"type": "InvalidDecision", "component": "decision"},
        "La invalidación debe observarse sin ampliar el schema de AuditTrace.",
    )
    assert_true(len({trace.trace_id for trace in traces}) == 1, "El trace_id debe conservarse.")


class _PermissivePositionParams(BaseModel):
    x: float
    y: float
    z: float


def test_action_executor_revalidates_physical_parameters_before_local_call() -> None:
    original_tools = dict(ToolRegistry._tools)
    calls: list[_PermissivePositionParams] = []

    def execute(params: _PermissivePositionParams) -> str:
        calls.append(params)
        return "ok"

    ToolRegistry._tools["navegar_a_posicion"] = {
        "func": execute,
        "description": "test",
        "schema": _PermissivePositionParams,
        "requires_confirmation": False,
    }
    try:
        invalid = AccionSaliente(
            id_accion="root-002-local-invalid",
            nombre="navegar_a_posicion",
            parametros={"x": float("inf"), "y": 0, "z": 0},
        )
        invalid_result = ActionExecutor.ejecutar(invalid)
        assert_true(invalid_result.exito is False and calls == [], "El executor no debe invocar la herramienta inválida.")

        valid = AccionSaliente(
            id_accion="root-002-local-valid",
            nombre="navegar_a_posicion",
            parametros={"x": "1", "y": "-1", "z": "3.5"},
        )
        valid_result = ActionExecutor.ejecutar(valid)
        assert_true(valid_result.exito is True and len(calls) == 1, "El camino local válido debe conservarse.")
        assert_true(calls[0].model_dump() == {"x": 1.0, "y": -1.0, "z": 3.5}, "El executor debe recibir floats normalizados.")
    finally:
        ToolRegistry._tools.clear()
        ToolRegistry._tools.update(original_tools)


def test_valid_motor_path_and_audit_sequence_remain_equivalent() -> None:
    outgoing, traces, calls = _run_audited_direct_motor(
        {"x": "1", "y": "-1", "z": "3.5"},
        "root-002-valid",
    )
    decision = next(trace for trace in traces if trace.stage == "decision")
    execution = next(trace for trace in traces if trace.stage == "execution")

    assert_true(calls["motor"] == 1, "El camino válido no debe repetir Motor.")
    assert_true(outgoing is not None and outgoing.nombre == "navegar_a_posicion", "La acción válida debe conservarse.")
    assert_true(outgoing.parametros == {"x": 1.0, "y": -1.0, "z": 3.5}, "La acción válida debe normalizarse.")
    assert_true(decision.decision["valid"] is True and decision.decision["action"] == "navegar_a_posicion", "La decisión válida no cambia.")
    assert_true(
        execution.execution["status"] == "pending",
        "AuditTrace v1 debe conservar su mapeo histórico de despachada a pending.",
    )
    assert_true([trace.stage for trace in traces] == ["event_start", "triage", "memory", "decision", "execution", "event_end"], "La secuencia AuditTrace válida no cambia.")


def test_nonphysical_action_is_not_changed_by_numeric_guard() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    response = ActionResponse(
        pensamiento="",
        accion="hablar",
        parametros={"texto_a_comunicar": "1, NaN e infinito son texto."},
        razon="Control no físico.",
        valido=True,
    )
    assert_true(orchestrator._sanitizar_action_response(response) is response, "El guard numérico no debe alterar acciones no físicas.")
