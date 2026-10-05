"""Pruebas permanentes del contrato fail-closed de target de ROOT-001."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.motor_engine import MotorEngine
from ai_engine.motor_resolution import CandidateMatch, MotorResolution
from ai_engine.schemas import ActionResponse
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator
from terminal_client.test_chat import _orquestador_aislado


PHYSICAL_TARGET_ACTIONS = {
    "navegar_a_objeto",
    "alejarse_de_objeto",
    "interactuar_objeto_3d",
    "agarrar_objeto",
    "aplicar_fuerza",
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _object(
    object_id: str,
    *,
    aliases: list[str] | None = None,
    affordances: list[str] | None = None,
    distance: float | None = 2.0,
) -> dict[str, Any]:
    return {
        "id": object_id,
        "display_name": object_id,
        "nombre": object_id,
        "aliases": list(aliases or []),
        "affordances": list(affordances or ["mirar"]),
        "distance": distance,
    }


def _snapshot(*objects: dict[str, Any], snapshot_id: str = "root-001") -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "source": "unity",
        "objetos_normalizados": list(objects),
    }


class _ReasoningForbidden:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, **kwargs: Any) -> ActionResponse:
        self.calls += 1
        raise AssertionError("Reasoning no debe reinterpretar una resolucion Motor autoritativa.")


class _Registry:
    NAMES = {
        "hablar",
        "navegar_a_objeto",
        "alejarse_de_objeto",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "aplicar_fuerza",
    }

    @classmethod
    def get_tool(cls, name: str) -> dict[str, Any] | None:
        return {"requires_confirmation": False} if name in cls.NAMES else None


def _route(
    message: str,
    perception: dict[str, Any],
    *,
    motor: MotorEngine | None = None,
    intent: str = "MOTOR_SIMPLE",
) -> tuple[ActionResponse, str, _ReasoningForbidden]:
    engine = motor or MotorEngine()
    reasoning = _ReasoningForbidden()
    orchestrator = object.__new__(Orchestrator)
    orchestrator.motor_engine = engine
    orchestrator.reasoning = reasoning
    orchestrator.tool_registry = _Registry()
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=f"root-001-{abs(hash((message, intent)))}",
        detalles={"mensaje": message, **perception},
    )
    response = orchestrator._resolver_ruta_motor(
        evento=event,
        contexto_llm={},
        memoria_texto="",
        herramientas=list(_Registry.NAMES),
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
        triage_decision={"intent": intent, "route": "motor"},
    )
    response = orchestrator._sanitizar_action_response(response)
    outgoing = orchestrator._a_accion_saliente(response, "exploradora")
    execution_state, _ = orchestrator._gestionar_ejecucion(event, outgoing, response)
    assert_true(outgoing.nombre == response.accion, "ActionResponse y AccionSaliente deben coincidir.")
    return response, execution_state, reasoning


def _candidate(name: str = "Cube") -> CandidateMatch:
    return CandidateMatch(
        target_id=name,
        display_name=name,
        legacy_nombre_objeto=name,
        confidence=75.0,
        reason="fixture ROOT-001",
    )


def _resolution(status: str, name: str = "Cube") -> MotorResolution:
    candidate = _candidate(name)
    has_target = status != "not_found"
    return MotorResolution(
        status=status,  # type: ignore[arg-type]
        action="navegar_a_objeto",
        target_id=name if has_target else None,
        target_display_name=name if has_target else None,
        legacy_nombre_objeto=name if has_target else None,
        confidence=candidate.confidence if has_target else 0.0,
        candidates=[candidate] if has_target else [],
        reason=f"Resolucion sintetica {status}.",
        audit={"status_reason": f"synthetic_{status}"},
    )


def test_exact_red_target_ready_passes() -> None:
    perception = _snapshot(_object("Cube_rojo", aliases=["cubo rojo", "rojo"]))
    motor = MotorEngine()
    resolution = motor.fuzzy_resolver.resolve(
        "ve al cubo rojo",
        perception,
        action="navegar_a_objeto",
    )
    response, state, reasoning = _route("ve al cubo rojo", perception, motor=motor)

    assert_true(resolution.status == "ready", "El target exacto debe quedar ready.")
    assert_true(response.accion == "navegar_a_objeto", "El target ready debe conservar accion fisica.")
    assert_true(response.parametros == {"nombre_objeto": "Cube_rojo"}, "Debe conservar el target exacto.")
    assert_true(response.metadata["motor_resolution"]["status"] == "ready", "Debe conservar metadata ready.")
    assert_true(state == "despachada" and reasoning.calls == 0, "El camino ready debe conservar dispatch historico.")


def test_requested_red_with_only_blue_never_dispatches_blue() -> None:
    perception = _snapshot(_object("Cube_azul", aliases=["cubo", "cubo azul"]))
    motor = MotorEngine()
    original = motor.fuzzy_resolver.resolve(
        "ve al cubo rojo",
        perception,
        action="navegar_a_objeto",
    )
    diagnosis = motor.diagnosticar_comando_usuario(
        "ve al cubo rojo",
        perception,
        herramientas_disponibles=_Registry.NAMES,
    )
    route_motor = MotorEngine()
    resolver_calls = {"count": 0}
    original_resolve = route_motor.fuzzy_resolver.resolve

    def observed_resolve(*args: Any, **kwargs: Any) -> MotorResolution:
        resolver_calls["count"] += 1
        return original_resolve(*args, **kwargs)

    route_motor.fuzzy_resolver.resolve = observed_resolve
    response, _state, reasoning = _route("ve al cubo rojo", perception, motor=route_motor)

    assert_true(original.status == "requires_clarification", "El sustituto debe requerir aclaracion.")
    assert_true(original.target_id == "Cube_azul", "El fixture debe detectar el candidato azul incompatible.")
    assert_true(diagnosis["estado"] == "requires_clarification", "Legacy no debe elevar el diagnostico a lista.")
    assert_true(diagnosis["parametros"] is None, "El diagnostico bloqueado no debe materializar target.")
    assert_true(
        diagnosis["metadata"]["motor_resolution"]["status"] == "requires_clarification",
        "La causa original debe conservarse.",
    )
    assert_true(response.accion == "hablar", "Puede pedir aclaracion, pero no navegar.")
    assert_true(response.accion not in PHYSICAL_TARGET_ACTIONS, "No debe existir ActionResponse fisica al azul.")
    assert_true("nombre_objeto" not in response.parametros, "No debe existir target azul en la salida.")
    assert_true(response.metadata["motor_resolution"]["status"] == "requires_clarification", "Metadata no debe borrarse.")
    assert_true(resolver_calls["count"] == 1, "El target sustituido no debe entrar a una segunda resolucion.")
    assert_true(reasoning.calls == 0, "Reasoning no debe reinterpretar la resolucion bloqueada.")


def test_ambiguous_targets_remain_nonphysical() -> None:
    perception = _snapshot(
        _object("Cube_01", aliases=["cubo"], distance=1.5),
        _object("Cube_02", aliases=["cubo"], distance=3.0),
    )
    response, _state, reasoning = _route("ve al cubo", perception)

    assert_true(response.accion == "hablar", "La ambiguedad debe pedir aclaracion.")
    assert_true(response.accion not in PHYSICAL_TARGET_ACTIONS, "La ambiguedad no debe producir accion fisica.")
    assert_true(response.metadata["motor_resolution"]["status"] == "requires_clarification", "Debe conservar status.")
    assert_true(len(response.metadata["motor_resolution"]["candidates"]) == 2, "Debe conservar candidatos.")
    assert_true(reasoning.calls == 0, "No debe haber reinterpretacion posterior.")


def test_singleton_requires_clarification_is_authoritative() -> None:
    motor = MotorEngine()
    motor._resolver_objetivo_fuzzy = lambda **kwargs: _resolution("requires_clarification")  # type: ignore[method-assign]
    perception = _snapshot(_object("Cube", aliases=["cubo"]))
    diagnosis = motor.diagnosticar_comando_usuario("ve al cubo", perception)
    direct = motor.procesar_comando_motor("navegar_a_objeto", {"nombre_objeto": "Cube"}, perception)

    assert_true(diagnosis["estado"] == "requires_clarification", "Singleton no debe habilitar legacy.")
    assert_true(diagnosis["parametros"] is None, "No debe construir parametros legacy.")
    assert_true(direct["accion"] == "none", "Enrichment tampoco debe elevar singleton a ready.")
    assert_true(direct["metadata"]["motor_resolution"]["status"] == "requires_clarification", "Debe conservar metadata.")


def test_not_found_is_authoritative_in_diagnosis_and_enrichment() -> None:
    motor = MotorEngine()
    motor._resolver_objetivo_fuzzy = lambda **kwargs: _resolution("not_found")  # type: ignore[method-assign]
    perception = _snapshot(_object("Cube", aliases=["cubo"]))
    diagnosis = motor.diagnosticar_comando_usuario("ve al cubo", perception)
    direct = motor.procesar_comando_motor("navegar_a_objeto", {"nombre_objeto": "Cube"}, perception)

    assert_true(diagnosis["estado"] == "not_found" and diagnosis["parametros"] is None, "not_found debe bloquear legacy.")
    assert_true(direct["accion"] == "none", "not_found no debe volverse ejecutable durante enrichment.")
    assert_true(direct["metadata"]["motor_resolution"]["status"] == "not_found", "Debe conservar not_found.")


def test_not_allowed_and_unsafe_are_authoritative() -> None:
    perception = _snapshot(_object("Cube", aliases=["cubo"]))
    for status in ("not_allowed", "unsafe"):
        motor = MotorEngine()
        motor._resolver_objetivo_fuzzy = lambda **kwargs: _resolution(status)  # type: ignore[method-assign]
        diagnosis = motor.diagnosticar_comando_usuario("ve al cubo", perception)
        direct = motor.procesar_comando_motor("navegar_a_objeto", {"nombre_objeto": "Cube"}, perception)
        response, state, reasoning = _route("ve al cubo", perception, motor=motor)

        assert_true(diagnosis["estado"] == status and diagnosis["parametros"] is None, f"{status} debe bloquear.")
        assert_true(direct["accion"] == "none", f"{status} no debe producir MotorAction fisica.")
        assert_true(direct["metadata"]["motor_resolution"]["status"] == status, f"Debe conservar {status}.")
        assert_true(response.accion not in PHYSICAL_TARGET_ACTIONS, f"{status} no debe alcanzar salida fisica.")
        assert_true(reasoning.calls == 0, f"{status} no debe caer a Reasoning.")
        if status == "unsafe":
            assert_true(response.accion == "none" and state == "sin_accion", "unsafe debe terminar en no-op.")


def test_natural_not_allowed_keeps_affordance_block() -> None:
    perception = _snapshot(_object("Tree_01", aliases=["arbol"], affordances=["mirar", "da_sombra"]))
    response, _state, reasoning = _route("agarra el arbol", perception, intent="INTERACTION")

    assert_true(response.accion == "hablar", "not_allowed debe explicar el bloqueo sin accion fisica.")
    assert_true(response.metadata["motor_resolution"]["status"] == "not_allowed", "Debe conservar affordance block.")
    assert_true(reasoning.calls == 0, "not_allowed no debe reinterpretarse.")


def test_danger_singleton_no_longer_promotes_clarification() -> None:
    perception = _snapshot(_object("Cube", aliases=["cubo"], affordances=["mirar", "peligro"]))
    motor = MotorEngine()
    original = motor.fuzzy_resolver.resolve("ve al cubo", perception, action="navegar_a_objeto")
    response, _state, reasoning = _route("ve al cubo", perception, motor=motor)

    assert_true(original.status == "unsafe", "El peligro debe quedar bloqueado por autoridad de seguridad.")
    assert_true(response.accion == "none", "El peligro debe producir no-op, no navegar.")
    assert_true(response.accion not in PHYSICAL_TARGET_ACTIONS, "El singleton peligroso no debe despacharse fisicamente.")
    assert_true(response.metadata["motor_resolution"]["status"] == "unsafe", "Debe conservar la causa legacy.")
    assessment = response.metadata["motor_resolution"]["candidates"][0]["affordance_decision"]["safety_assessment"]
    assert_true(assessment["disposition"] == "UNSAFE", "Debe conservar la autoridad tipada.")
    assert_true(reasoning.calls == 0, "No debe caer a reinterpretacion interna.")


def test_exact_alias_learned_alias_and_fuzzy_typo_ready_paths_survive() -> None:
    perception = _snapshot(_object("Cube", aliases=["cubo"]))
    cases = (
        ("ve al cubo", None),
        ("ve al cuboo", None),
        ("ve a la caja", lambda object_ids: {"Cube": ["caja"]}),
    )
    for message, provider in cases:
        motor = MotorEngine()
        motor.fuzzy_resolver.learned_alias_provider = provider
        response, state, reasoning = _route(message, perception, motor=motor)
        assert_true(response.accion == "navegar_a_objeto", f"El ready valido debe sobrevivir: {message}")
        assert_true(response.parametros == {"nombre_objeto": "Cube"}, "Debe usar el target visible correcto.")
        assert_true(response.metadata["motor_resolution"]["status"] == "ready", "Debe conservar ready.")
        assert_true(state == "despachada" and reasoning.calls == 0, "El dispatch ready historico debe conservarse.")


def test_legacy_without_modern_resolution_is_preserved() -> None:
    calls = {"resolver": 0}
    motor = MotorEngine()

    def forbidden_resolver(*args: Any, **kwargs: Any) -> MotorResolution:
        calls["resolver"] += 1
        raise AssertionError("Percepcion legacy no debe invocar fuzzy moderno.")

    motor.fuzzy_resolver.resolve = forbidden_resolver
    perception = {"objetos_visibles": [{"nombre": "Cube"}]}
    diagnosis = motor.diagnosticar_comando_usuario("ve al cubo", perception)
    response, state, reasoning = _route("ve al cubo", perception, motor=motor)

    assert_true(diagnosis["estado"] == "lista", "Legacy sin MotorResolution debe mantenerse.")
    assert_true(response.accion == "navegar_a_objeto", "El nombre visible legacy exacto debe seguir funcionando.")
    assert_true(response.parametros == {"nombre_objeto": "Cube"}, "Legacy debe conservar target visible.")
    assert_true(response.metadata == {}, "No debe inventar una MotorResolution moderna.")
    assert_true(calls["resolver"] == 0 and reasoning.calls == 0, "La ruta legacy debe permanecer aislada.")
    assert_true(state == "despachada", "Debe conservar dispatch historico para legacy legitimo.")


def test_empty_modern_snapshot_cannot_fall_back_to_stale_legacy_names() -> None:
    perception = {
        "snapshot_id": "empty-modern",
        "source": "unity",
        "objetos_normalizados": [],
        "objetos_visibles": "Frente a mi veo: Cube a 2.0m.",
    }
    response, _state, reasoning = _route("ve al cubo", perception)

    assert_true(response.accion == "hablar", "El snapshot moderno vacio debe responder not_found.")
    assert_true(response.metadata["motor_resolution"]["status"] == "not_found", "No debe tratarlo como legacy.")
    assert_true("nombre_objeto" not in response.parametros, "No debe resucitar el nombre visible obsoleto.")
    assert_true(reasoning.calls == 0, "No debe reinterpretar un snapshot moderno vacio.")


def test_resolver_exception_with_modern_perception_fails_closed() -> None:
    calls = {"resolver": 0}
    motor = MotorEngine()

    def exploding_resolver(*args: Any, **kwargs: Any) -> MotorResolution:
        calls["resolver"] += 1
        raise RuntimeError("synthetic resolver failure")

    motor.fuzzy_resolver.resolve = exploding_resolver
    perception = _snapshot(_object("Cube", aliases=["cubo"]))
    response, _state, reasoning = _route("ve al cubo", perception, motor=motor)

    assert_true(calls["resolver"] == 1, "El resolver fallido no debe repetirse mediante segunda resolucion.")
    assert_true(response.accion == "hablar", "El fallo verificando target debe degradar a aclaracion no fisica.")
    assert_true(response.accion not in PHYSICAL_TARGET_ACTIONS, "La excepcion no debe conceder permiso fisico.")
    assert_true(response.metadata["motor_resolution"]["status"] == "requires_clarification", "Debe conservar bloqueo.")
    assert_true(response.metadata["motor_audit"]["status_reason"] == "resolver_exception", "Debe auditar la causa segura.")
    assert_true(response.metadata["motor_audit"]["error_type"] == "RuntimeError", "Debe conservar tipo, no elevar target.")
    assert_true(reasoning.calls == 0, "Reasoning no debe ocultar el fallo del resolver.")


class _RecordingWriter:
    def __init__(self) -> None:
        self.traces = []

    def write(self, trace: Any) -> bool:
        self.traces.append(trace)
        return True


def _audited_turn(message: str, perception: dict[str, Any], transaction_id: str):
    orchestrator, _calls = _orquestador_aislado("motor", intent="MOTOR_SIMPLE")
    del orchestrator.__dict__["_resolver_ruta_motor"]
    orchestrator.motor_engine = MotorEngine()
    writer = _RecordingWriter()
    orchestrator.audit_writer = writer
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": message, "solicita_decision": True, **perception},
    )
    outgoing = orchestrator.procesar_evento(event)
    return outgoing, writer.traces


def test_audittrace_preserves_ready_and_observes_wrong_target_block() -> None:
    valid, valid_traces = _audited_turn(
        "ve al cubo rojo",
        _snapshot(_object("Cube_rojo", aliases=["cubo rojo"]), snapshot_id="audit-ready"),
        "root-001-audit-ready",
    )
    blocked, blocked_traces = _audited_turn(
        "ve al cubo rojo",
        _snapshot(_object("Cube_azul", aliases=["cubo", "cubo azul"]), snapshot_id="audit-blocked"),
        "root-001-audit-blocked",
    )

    assert_true(valid is not None and valid.nombre == "navegar_a_objeto", "El trace ready debe conservar accion.")
    assert_true(valid.parametros == {"nombre_objeto": "Cube_rojo"}, "El trace ready debe conservar target.")
    assert_true(blocked is not None and blocked.nombre == "hablar", "El trace bloqueado solo puede aclarar.")
    assert_true("nombre_objeto" not in blocked.parametros, "AccionSaliente bloqueada no debe contener Cube_azul.")

    valid_stages = [trace.stage for trace in valid_traces]
    blocked_stages = [trace.stage for trace in blocked_traces]
    assert_true(valid_stages == ["event_start", "triage", "memory", "decision", "execution", "event_end"], "Audit v1 ready no cambia.")
    assert_true(blocked_stages == valid_stages, "La aclaracion usa los stages historicos sin ampliar AuditTrace.")
    assert_true(len({trace.trace_id for trace in valid_traces}) == 1, "El trace_id ready debe ser estable.")
    assert_true(len({trace.trace_id for trace in blocked_traces}) == 1, "El trace_id bloqueado debe ser estable.")

    valid_decision = next(trace for trace in valid_traces if trace.stage == "decision")
    blocked_decision = next(trace for trace in blocked_traces if trace.stage == "decision")
    valid_execution = next(trace for trace in valid_traces if trace.stage == "execution")
    blocked_execution = next(trace for trace in blocked_traces if trace.stage == "execution")
    assert_true(valid_decision.decision["action"] == "navegar_a_objeto", "Decision ready debe seguir fisica.")
    assert_true(blocked_decision.decision["action"] == "hablar", "Decision bloqueada no debe nombrar navegacion.")
    assert_true(valid_execution.execution["status"] == "pending", "ROOT-004 queda fuera: ready conserva pending.")
    assert_true(blocked_execution.execution["status"] == "pending", "La aclaracion hablada conserva lifecycle historico.")


def test_resolution_and_dispatch_are_deterministic_across_ten_runs() -> None:
    cases = (
        ("ve al cubo rojo", _snapshot(_object("Cube_rojo", aliases=["cubo rojo"]))),
        ("ve al cubo rojo", _snapshot(_object("Cube_azul", aliases=["cubo", "cubo azul"]))),
        ("ve al cubo", _snapshot(_object("Cube", aliases=["cubo"], affordances=["mirar", "peligro"]))),
    )
    for message, perception in cases:
        observations = []
        for _ in range(10):
            motor = MotorEngine()
            resolution = motor.fuzzy_resolver.resolve(message, perception, action="navegar_a_objeto")
            response, state, _reasoning = _route(message, perception, motor=motor)
            observations.append(
                (
                    resolution.status,
                    resolution.target_id,
                    tuple(candidate.target_id for candidate in resolution.candidates),
                    response.accion,
                    tuple(sorted(response.parametros.items())),
                    state,
                )
            )
        assert_true(len(set(observations)) == 1, f"El caso debe ser determinista: {message} {perception!r}")


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"ROOT001_FOCAL_PASS={len(tests)}")


if __name__ == "__main__":
    main()
