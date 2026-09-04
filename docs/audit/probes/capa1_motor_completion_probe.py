"""Final authorized Block 6 audit: only outstanding synthetic matrices.

Read-only process, no clients, transport, executor or persistence. JSONL stdout
contains compact observations, never the 249 preserved parameter rows. The
caller can preserve it under docs/audit/evidence/. Known findings are evidence,
not a safety PASS. --group permits rerunning only a failed harness group.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
import socket
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
from capa1_motor_continuation_probe import WRITE_ATTEMPTS, read_only_barrier
from capa1_motor_deep_probe import BLOCKED, deny, hashes


def emit(group, data):
    print(json.dumps({"group": group, "data": data}, ensure_ascii=True,
                     allow_nan=False, separators=(",", ":")), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", "matrices", "audit"], default="all")
    group = parser.parse_args().group
    before = hashes()
    sys.addaudithook(read_only_barrier)
    with ExitStack() as guards:
        guards.enter_context(patch.object(socket.socket, "connect", deny))
        guards.enter_context(patch.object(socket.socket, "connect_ex", deny))
        guards.enter_context(patch.object(socket, "create_connection", deny))
        from ai_engine.motor_engine import MotorEngine
        from ai_engine.motor_schemas import ACTION_SCHEMA_MAP
        from ai_engine.perception_models import normalize_perception
        from ai_engine.schemas import ActionResponse
        from api.schemas import EventoEntrante, RespuestaAPI
        from core.orchestrator import Orchestrator
        from memory.chroma_client import ChromaClient
        from tools.executor import ActionExecutor
        import chromadb
        import ai_engine.fuzzy_object_resolver as fuzzy_module
        guards.enter_context(patch.object(ChromaClient, "__init__", deny))
        guards.enter_context(patch.object(chromadb, "PersistentClient", deny))
        guards.enter_context(patch.object(chromadb, "Client", deny))
        guards.enter_context(patch.object(ActionExecutor, "ejecutar", deny))

        class Registry:
            @staticmethod
            def listar_nombres():
                return [*ACTION_SCHEMA_MAP, "hablar"]

            @staticmethod
            def describir_herramientas(names):
                return {name: name for name in names}

            @staticmethod
            def get_tool(name):
                return {"requires_confirmation": False} if name in Registry.listar_nombres() else None

        class Reasoning:
            def __init__(self, response=None):
                self.response = response or ActionResponse(pensamiento="synthetic", accion="none")
                self.calls = 0

            def process(self, **kwargs):
                self.calls += 1
                return self.response

        def obj(name, aliases, affordances, **extra):
            return {"id": name, "display_name": name, "aliases": aliases,
                    "affordances": affordances, **extra}

        cube = obj("Cube", ["cubo"], ["mirar", "empujable", "agarrable"])
        wall = obj("Wall", ["pared"], ["mirar"])
        sphere = obj("Sphere", ["esfera"], ["mirar", "agarrable"])
        door = obj("Door", ["puerta"], ["mirar", "activable"])
        red = obj("Cube_rojo", ["cubo", "cubo rojo"], ["mirar"])
        blue = obj("Cube_azul", ["cubo", "cubo azul"], ["mirar"])

        def perception(objects):
            return {"objetos_normalizados": deepcopy(objects)}

        def resolution(result):
            return {"status": result.status, "target": result.target_id,
                    "confidence": result.confidence,
                    "reason": result.audit.get("status_reason"),
                    "candidates": [{"id": c.target_id, "score": c.confidence,
                                    "unmatched": c.unmatched_descriptors} for c in result.candidates],
                    "correction": result.candidate_correction is not None}

        def minimal(motor=None, response=None):
            orch = object.__new__(Orchestrator)
            orch.motor_engine = motor or MotorEngine()
            orch.reasoning = Reasoning(response)
            orch.tool_registry = Registry()
            return orch

        def event(query, snap):
            return EventoEntrante(origen="unity", tipo_evento="comando_usuario",
                                  id_transaccion="block6-completion", detalles={"mensaje": query, **snap})

        def finish(orch, response, evt):
            response = orch._sanitizar_action_response(orch._blindar_salida_terminal(evt, response))
            outgoing = orch._a_accion_saliente(response, "exploradora")
            state, _ = orch._gestionar_ejecucion(evt, outgoing, response)
            return {"action": outgoing.nombre, "valid": response.valido,
                    "parameters": outgoing.parametros, "dispatch": state,
                    "confirmation": outgoing.requiere_confirmacion,
                    "resolution_status": response.metadata.get("motor_resolution", {}).get("status")}

        def route(orch, query, snap):
            evt = event(query, snap)
            with patch.object(orch.motor_engine, "procesar_comando_motor",
                              wraps=orch.motor_engine.procesar_comando_motor) as counted:
                response = orch._resolver_ruta_motor(
                    evento=evt, contexto_llm={}, memoria_texto="", herramientas=Registry.listar_nombres(),
                    herramientas_detalle={}, politica="exploradora", tarea=None,
                    triage_decision={"intent": "MOTOR_SIMPLE", "route": "motor"})
            return {**finish(orch, response, evt), "motor_calls": counted.call_count,
                    "reasoning_calls": orch.reasoning.calls}

        def boom(*args, **kwargs):
            raise RuntimeError("synthetic injected failure")

        if group in {"all", "matrices"}:
            recovered = []
            for name, query, objects in [
                ("two_cubes_red", "ve al cubo rojo", [red, blue]),
                ("duplicate_names", "ve al cubo", [dict(red, display_name="Cube"), dict(blue, display_name="Cube")])]:
                motor, snap = MotorEngine(), perception(objects)
                recovered.append({"case": name, "query": query, "visible": objects,
                                  "fuzzy": resolution(motor.fuzzy_resolver.resolve(query, snap, action="navegar_a_objeto")),
                                  "output": route(minimal(motor), query, snap)})
            emit("resolution_recovered", recovered)

            # Natural verbs and explicit action contracts are different entry points.
            cases = [
                ("tomar_esfera", "toma la esfera", sphere, "agarrar_objeto", None),
                ("tomar_pared", "toma la pared", wall, "agarrar_objeto", None),
                ("empujar_cubo", "empuja el cubo", cube, "aplicar_fuerza", None),
                ("empujar_pared", "empuja la pared", wall, "aplicar_fuerza", None),
                ("abrir_puerta", "abre la puerta", door, "interactuar_objeto_3d", "activar"),
                ("abrir_cubo", "abre el cubo", cube, "interactuar_objeto_3d", "activar"),
                ("golpear", "golpea el cubo", dict(cube, affordances=["mirar"]), "interactuar_objeto_3d", "golpear"),
                ("aplicar_fuerza", "aplica fuerza al cubo", cube, "aplicar_fuerza", None),
                ("navegacion", "ve al cubo", cube, "navegar_a_objeto", None),
            ]
            rows = []
            for name, query, target, action, interaction in cases:
                motor, snap = MotorEngine(), perception([target])
                direct = motor.fuzzy_resolver.affordances.validate(
                    normalize_perception(snap).objects[0], action, interaction_type=interaction)
                params = {"nombre_objeto": target["display_name"]}
                if interaction:
                    params["tipo_accion"] = interaction
                if action == "aplicar_fuerza":
                    params.update(vector_x=1, vector_y=0, vector_z=0, fuerza=1)
                diagnosis = motor.diagnosticar_comando_usuario(query, snap)
                fuzzy = motor.fuzzy_resolver.resolve(query, snap, action=action)
                with patch.object(motor, "_puede_usar_fallback_legacy", wraps=motor._puede_usar_fallback_legacy) as legacy:
                    # Observe actual legacy acceptance, not merely calls to the predicate.
                    legacy_results = []
                    original = MotorEngine._puede_usar_fallback_legacy
                    legacy.side_effect = lambda r: (legacy_results.append(original(r)) or legacy_results[-1])
                    natural = route(minimal(motor), query, snap)
                explicit_orch = minimal()
                explicit_response = explicit_orch._action_response_desde_motor(
                    explicit_orch.motor_engine.procesar_comando_motor(action, params, snap), "")
                rows.append({"case": name, "query": query, "action_requested": action,
                             "interaction_type": interaction, "affordances": target["affordances"],
                             "direct": {k: direct.to_dict()[k] for k in ["status", "allowed", "required", "matched", "risk_penalty"]},
                             "fuzzy": resolution(fuzzy), "diagnosis": diagnosis["estado"],
                             "natural_legacy": any(legacy_results), "natural": natural,
                             "explicit": finish(explicit_orch, explicit_response, event(query, snap))})
            emit("affordances", rows)

            motor, snap = MotorEngine(), perception([cube])
            resolver = motor.fuzzy_resolver
            providers = []
            for name, attr, callback, query in [
                ("alias_exception", "learned_alias_provider", boom, "ve al cubo"),
                ("affect_exception", "affective_memory_provider", boom, "ve al cubo"),
                ("alias_baseline", "learned_alias_provider", None, "ve a la caja"),
                ("alias_learned", "learned_alias_provider", lambda ids: {"Cube": ["caja"]}, "ve a la caja"),
                ("affect_baseline", "affective_memory_provider", None, "ve al cubo"),
                ("affect_positive", "affective_memory_provider", lambda objs: {"Cube": [{"affect": "calma", "weight": 1, "confirmed": True}]}, "ve al cubo")]:
                with patch.object(resolver, attr, callback):
                    providers.append({"case": name, "fuzzy": resolution(resolver.resolve(query, snap, action="navegar_a_objeto")),
                                      "output": route(minimal(motor), query, snap)})
            emit("providers", providers)

            sensitivities = []
            for name, objects, query in [
                ("object_order_ab", [red, blue], "ve al cubo"),
                ("object_order_ba", [blue, red], "ve al cubo"),
                ("alias_order_ab", [dict(cube, aliases=["cubo", "caja"])], "ve a la caja"),
                ("alias_order_ba", [dict(cube, aliases=["caja", "cubo"])], "ve a la caja"),
                ("id_original", [cube], "ve a Cube"),
                ("id_changed", [dict(cube, id="changed")], "ve a Cube"),
                ("confidence_0", [dict(cube, confidence=0)], "ve al cubo"),
                ("confidence_1", [dict(cube, confidence=1)], "ve al cubo")]:
                sensitivities.append({"case": name, "fuzzy": resolution(resolver.resolve(query, perception(objects), action="navegar_a_objeto"))})
            mutable = normalize_perception(perception([cube]))
            before_mutation = resolution(resolver.resolve("ve a la caja", mutable, action="navegar_a_objeto"))
            mutable.objects[0].aliases.append("caja")
            sensitivities.append({"case": "mutable_snapshot", "before": before_mutation,
                                  "after": resolution(resolver.resolve("ve a la caja", mutable, action="navegar_a_objeto"))})
            emit("sensitivity", sensitivities)

            failures = []
            for name in ["normalize_exception", "fuzzy_exception", "affordance_exception", "parser_exception", "motor_exception"]:
                motor = MotorEngine()
                target, attribute = {
                    "normalize_exception": (fuzzy_module, "normalize_perception"),
                    "fuzzy_exception": (motor.fuzzy_resolver, "resolve"),
                    "affordance_exception": (motor.fuzzy_resolver.affordances, "validate"),
                    "parser_exception": (motor, "_inferir_accion"),
                    "motor_exception": (motor, "procesar_comando_motor"),
                }[name]
                with patch.object(target, attribute, boom):
                    try:
                        result = {"escaped": None, "output": route(minimal(motor), "ve al cubo", snap)}
                    except Exception as exc:
                        result = {"escaped": type(exc).__name__, "output": None}
                failures.append({"case": name, **result})
            orch = minimal()
            failures.append({"case": "sanitization_unknown", "output": finish(
                orch, ActionResponse(pensamiento="", accion="invented"), event("synthetic", {}))})
            dto = RespuestaAPI(status="ok", accion=orch._a_accion_saliente(
                ActionResponse(pensamiento="", accion="hablar", parametros={"synthetic": object()}), "exploradora"))
            try:
                dto.model_dump_json()
                serialization = "no_exception"
            except Exception as exc:
                serialization = type(exc).__name__
            failures.append({"case": "serialization_unsupported_object", "escaped": serialization,
                             "boundary": "DTO JSON only; synthetic non-JSON Python value, not ordinary HTTP input"})
            emit("failures", failures)

            deterministic = []
            for name, query, objects in [
                ("ready", "ve al cubo", [cube]), ("ambiguous", "ve al cubo", [red, blue]),
                ("not_allowed", "coge la pared", [wall]), ("alias_affect", "ve a la caja", [cube]),
                ("rotation", "gira 90 grados", [])]:
                motor, snap_case = MotorEngine(), perception(objects)
                motor.fuzzy_resolver.learned_alias_provider = lambda ids: {"Cube": ["caja"]}
                motor.fuzzy_resolver.affective_memory_provider = lambda objects: {"Cube": [{"affect": "calma", "weight": 1, "confirmed": True}]}
                samples = []
                for _ in range(10):
                    # Full MotorResolution + full ActionResponse (no UUID/timing DTO fields).
                    fuzzy = motor.fuzzy_resolver.resolve(query, snap_case, action="navegar_a_objeto").to_dict()
                    evt = event(query, snap_case)
                    ar = minimal(motor)._resolver_ruta_motor(evento=evt, contexto_llm={}, memoria_texto="",
                        herramientas=Registry.listar_nombres(), herramientas_detalle={}, politica="exploradora",
                        tarea=None, triage_decision={"intent": "MOTOR_SIMPLE", "route": "motor"})
                    samples.append(json.dumps({"fuzzy": fuzzy, "response": ar.model_dump()}, sort_keys=True, allow_nan=False))
                assert len(set(samples)) == 1, name
                deterministic.append({"case": name, "runs": 10, "unique_full_results": len(set(samples))})
            emit("determinism", deterministic)

        if group in {"all", "audit"}:
            from terminal_client.test_chat import _orquestador_aislado

            class Writer:
                def __init__(self, mode):
                    self.mode, self.rows = mode, []

                def write(self, trace):
                    self.rows.append(json.loads(trace.to_json()))
                    if self.mode == "raise":
                        raise OSError("synthetic audit writer failure")
                    return self.mode == "ok"

            records = []
            for name, query, objects, fallback in [
                ("ready", "ve al cubo", [cube], None),
                ("clarification", "ve al cubo", [red, blue], None),
                ("invalid", "request inferido", [], ActionResponse(pensamiento="", accion="girar_avatar", parametros={"grados": "invalid"})),
                ("reasoning_fallback", "request inferido", [], ActionResponse(pensamiento="", accion="girar_avatar", parametros={"grados": 90})),
                ("parser_failure", "ve al cubo", [cube], None),
            ]:
                runs = []
                for mode in ["ok", "false", "raise"]:
                    orch, _ = _orquestador_aislado("motor", intent="MOTOR_SIMPLE")
                    del orch.__dict__["_resolver_ruta_motor"]
                    orch.motor_engine, orch.reasoning = MotorEngine(), Reasoning(fallback)
                    orch.tool_registry, orch.audit_writer = Registry(), Writer(mode)
                    with ExitStack() as observations:
                        diag = observations.enter_context(patch.object(orch.motor_engine, "diagnosticar_comando_usuario", wraps=orch.motor_engine.diagnosticar_comando_usuario))
                        process = observations.enter_context(patch.object(orch.motor_engine, "procesar_comando_motor", wraps=orch.motor_engine.procesar_comando_motor))
                        fuzzy = observations.enter_context(patch.object(orch.motor_engine.fuzzy_resolver, "resolve", wraps=orch.motor_engine.fuzzy_resolver.resolve))
                        execution = observations.enter_context(patch.object(orch, "_gestionar_ejecucion", wraps=orch._gestionar_ejecucion))
                        if name == "parser_failure":
                            observations.enter_context(patch.object(orch.motor_engine, "_inferir_accion", boom))
                        try:
                            outgoing = orch.procesar_evento(event(query, perception(objects)))
                            output = {"action": outgoing.nombre, "parameters": outgoing.parametros}
                            escaped = None
                        except Exception as exc:
                            output, escaped = None, type(exc).__name__
                        counts = {"diagnosis": diag.call_count, "process": process.call_count,
                                  "fuzzy": fuzzy.call_count, "reasoning": orch.reasoning.calls,
                                  "execution": execution.call_count}
                        rows = orch.audit_writer.rows
                        assert rows[0]["stage"] == "event_start" and rows[-1]["stage"] == "event_end"
                        assert len({r["trace_id"] for r in rows}) == 1
                        decision = next((r["decision"] for r in rows if r["stage"] == "decision"), None)
                        exe = next((r["execution"] for r in rows if r["stage"] == "execution"), None)
                        if escaped is None and outgoing is not None:
                            assert decision["layer"] == "motor" and decision["action"] == output["action"]
                            assert execution.call_args.args[1].nombre == output["action"]
                            assert decision["valid"] is (name != "invalid")
                            assert exe["status"] == ("skipped" if name == "invalid" else "pending")
                        assert counts == {
                            "ready": {"diagnosis": 1, "process": 1, "fuzzy": 2, "reasoning": 0, "execution": 1},
                            "clarification": {"diagnosis": 1, "process": 0, "fuzzy": 1, "reasoning": 0, "execution": 1},
                            "invalid": {"diagnosis": 1, "process": 1, "fuzzy": 0, "reasoning": 1, "execution": 1},
                            "reasoning_fallback": {"diagnosis": 1, "process": 1, "fuzzy": 0, "reasoning": 1, "execution": 1},
                            "parser_failure": {"diagnosis": 1, "process": 0, "fuzzy": 0, "reasoning": 0, "execution": 0},
                        }[name]
                        runs.append({"writer": mode, "output": output, "escaped": escaped, "calls": counts,
                                     "stages": [r["stage"] for r in rows], "same_trace_id": True,
                                     "decision": decision, "execution": exe})
                assert all(r["output"] == runs[0]["output"] and r["calls"] == runs[0]["calls"] and r["escaped"] == runs[0]["escaped"] for r in runs)
                records.append({"case": name, "writer_invariance": True, "runs": runs})
            emit("audit", records)

    after = hashes()
    assert before == after, "Protected hash changed"
    assert not BLOCKED and not WRITE_ATTEMPTS, "Isolation attempt: stop"
    emit("integrity", {"protected_hashes": after, "protected_unchanged": True,
                       "blocked_operations": BLOCKED, "write_attempts": WRITE_ATTEMPTS,
                       "physical_execution": False, "network": False, "real_alias_persistence": False,
                       "status": "PASS_OBSERVATIONS_NOT_SAFETY", "group_executed": group})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
