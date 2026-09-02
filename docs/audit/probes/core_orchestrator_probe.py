"""Probe aislado de Fase 2 / Bloque 3: Core y Orchestrator.

PASS significa que el comportamiento actual fue reproducido, no que sea deseable.
El probe usa únicamente fakes/objetos en memoria: no abre red, proveedores, Unity
ni la Chroma de uso diario.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from collections import OrderedDict, deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(project_root))

logging.disable(logging.CRITICAL)

from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from core.task_context import TaskContext
from core.tool_policy import ToolPolicy


ROOT = Path(__file__).resolve().parents[3]
ACTIVE_CHROMA = (ROOT / "lazzaro_data").resolve()


class AuditSafetyBarrier(PermissionError):
    pass


def _is_active_chroma_path(value) -> bool:
    if value is None or isinstance(value, int):
        return False
    try:
        resolved = Path(os.fsdecode(value)).resolve()
    except (TypeError, ValueError, OSError):
        return False
    return resolved == ACTIVE_CHROMA or ACTIVE_CHROMA in resolved.parents


def install_safety_barriers() -> None:
    """Bloquea red y cualquier escritura/SQLite contra la Chroma activa."""

    def audit_hook(audit_event: str, args: tuple) -> None:
        if audit_event in {
            "socket.connect",
            "socket.sendto",
            "socket.sendmsg",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
        }:
            raise AuditSafetyBarrier("Network operation blocked by core audit probe")
        if audit_event == "sqlite3.connect" and args and _is_active_chroma_path(args[0]):
            raise AuditSafetyBarrier("Active Chroma SQLite blocked by core audit probe")
        if audit_event == "open" and args and _is_active_chroma_path(args[0]):
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise AuditSafetyBarrier("Active Chroma write blocked by core audit probe")
        if audit_event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.replace"}:
            if any(_is_active_chroma_path(value) for value in args[:2]):
                raise AuditSafetyBarrier("Active Chroma mutation blocked by core audit probe")

    sys.addaudithook(audit_hook)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class NullAuditWriter:
    def write(self, trace) -> bool:
        return True


class BoundaryWriter(NullAuditWriter):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.starts = 0
        self.second_start = threading.Event()

    def write(self, trace) -> bool:
        if trace.stage == "event_start":
            with self._lock:
                self.starts += 1
                if self.starts == 2:
                    self.second_start.set()
        return True


class FakeBehavior:
    def __init__(self) -> None:
        self.calls = 0
        self.state_mutations = 0
        self.task = None

    def procesar_evento(self, tipo_evento: str, detalles: dict) -> dict:
        self.calls += 1
        self.state_mutations += 1
        return {
            "estado": {"energia": 80.0, "estres": 10.0, "foco": 70.0},
            "narrativa": "estado de prueba",
            "contexto_llm": {},
            "tarea": self.task,
            "policy_activa": "exploradora",
            "es_interrupcion_critica": tipo_evento == "alerta_dolor_fisico",
            "tipo_memoria": tipo_evento,
            "percepcion_resumida": "",
        }

    def obtener_snapshot_audit(self) -> dict:
        return {
            "state": {"mutations": self.state_mutations},
            "task": dict(self.task or {}),
        }

    def obtener_resumen(self) -> dict:
        return {
            "estado": {"mutations": self.state_mutations},
            "personalidad": {},
            "tarea": self.task,
        }

    def cancelar_tarea(self, motivo: str = "") -> dict | None:
        previous = self.task
        self.task = None
        return previous


class FirstCallBlockingBehavior(FakeBehavior):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.second_behavior_entry = threading.Event()
        self._counter_lock = threading.Lock()

    def procesar_evento(self, tipo_evento: str, detalles: dict) -> dict:
        with self._counter_lock:
            next_call = self.calls + 1
        if next_call == 1:
            self.entered.set()
            require(self.release.wait(timeout=5), "No se liberó el primer BehaviorCore.")
        else:
            self.second_behavior_entry.set()
        return super().procesar_evento(tipo_evento, detalles)


class BarrierBehavior(FakeBehavior):
    def __init__(self) -> None:
        super().__init__()
        self.barrier = threading.Barrier(2)
        self.overlap_confirmed = False
        self._counter_lock = threading.Lock()

    def procesar_evento(self, tipo_evento: str, detalles: dict) -> dict:
        with self._counter_lock:
            self.calls += 1
            self.state_mutations += 1
        try:
            index = self.barrier.wait(timeout=5)
        except threading.BrokenBarrierError as exc:
            raise AssertionError("IDs distintos no alcanzaron BehaviorCore en paralelo.") from exc
        if index == 0:
            self.overlap_confirmed = True
        return {
            "estado": {"energia": 80.0, "estres": 10.0, "foco": 70.0},
            "narrativa": "estado de prueba",
            "contexto_llm": {},
            "tarea": None,
            "policy_activa": "exploradora",
            "es_interrupcion_critica": False,
            "tipo_memoria": tipo_evento,
            "percepcion_resumida": "",
        }


class FirstFailureBehavior(FakeBehavior):
    def procesar_evento(self, tipo_evento: str, detalles: dict) -> dict:
        self.calls += 1
        self.state_mutations += 1
        if self.calls == 1:
            raise RuntimeError("simulated behavior failure")
        return {
            "estado": {"energia": 80.0, "estres": 10.0, "foco": 70.0},
            "narrativa": "recuperado",
            "contexto_llm": {},
            "tarea": None,
            "policy_activa": "exploradora",
            "es_interrupcion_critica": False,
            "tipo_memoria": tipo_evento,
            "percepcion_resumida": "",
        }


class FakeMemory:
    def __init__(
        self,
        *,
        fail_first_working_write: bool = False,
        fail_reads: bool = False,
        fail_episode_write: bool = False,
    ) -> None:
        self.reads = 0
        self.episodes: list[dict] = []
        self.working_attempts = 0
        self.actions: list[dict] = []
        self.turns: list[dict] = []
        self.aliases: list[object] = []
        self.consolidations = 0
        self.fail_first_working_write = fail_first_working_write
        self.fail_reads = fail_reads
        self.fail_episode_write = fail_episode_write

    def obtener_contexto_memoria_con_metadata(self, **kwargs):
        self.reads += 1
        if self.fail_reads:
            raise RuntimeError("simulated memory read failure")
        return SimpleNamespace(texto="", retrieved_ids=(), scores=())

    def obtener_contexto_conversacional(self, n: int = 6) -> str:
        if self.fail_reads:
            raise RuntimeError("simulated conversation read failure")
        return ""

    def obtener_contexto_relacional(self, **kwargs) -> str:
        if self.fail_reads:
            raise RuntimeError("simulated relational read failure")
        return ""

    def obtener_ultimas_acciones(self) -> str:
        if self.fail_reads:
            raise RuntimeError("simulated working read failure")
        return "sin acciones"

    def obtener_cantidad_memoria_trabajo(self) -> int:
        return len(self.actions)

    def guardar_experiencia(self, **kwargs) -> str | None:
        if self.fail_episode_write:
            raise RuntimeError("simulated episode write failure")
        self.episodes.append(dict(kwargs))
        return f"episode-{len(self.episodes)}"

    def agregar_accion(self, **kwargs) -> None:
        self.working_attempts += 1
        if self.fail_first_working_write and self.working_attempts == 1:
            raise RuntimeError("simulated working-memory failure")
        if kwargs.get("accion") == "none" and kwargs.get("resultado") == "sin_accion":
            return
        self.actions.append(dict(kwargs))

    def registrar_turno_conversacional(self, **kwargs) -> None:
        self.turns.append(dict(kwargs))

    def guardar_alias_aprendido(self, candidate) -> str:
        self.aliases.append(candidate)
        return f"alias-{len(self.aliases)}"

    def consolidar_memorias_si_hace_falta(self) -> None:
        self.consolidations += 1


class FakeTriage:
    def __init__(self, route: str, intent: str | None = None) -> None:
        self.route = route
        self.intent = intent or f"TEST_{route.upper()}"
        self.calls = 0

    def route_event(self, evento: dict, estado_agente: dict) -> dict:
        self.calls += 1
        return {
            "intent": self.intent,
            "route": self.route,
            "confidence": 0.99,
            "reason": "decisión del probe",
        }


class FakeRegistry:
    _known = {
        "hablar": {"requires_confirmation": False},
        "navegar_a_objeto": {"requires_confirmation": False},
        "cancelar_tarea": {"requires_confirmation": False},
    }

    def listar_nombres(self) -> list[str]:
        return list(self._known)

    def describir_herramientas(self, nombres: list[str]) -> dict[str, str]:
        return {name: name for name in nombres}

    def get_tool(self, name: str) -> dict | None:
        return self._known.get(name)


class FakeIdentity:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def registrar_evento(self, **kwargs) -> None:
        self.events.append(dict(kwargs))

    def inyectar_en_prompt(self) -> str:
        return "estado de prueba"

    def obtener_estado_completo(self) -> dict:
        return {"energia": 80.0, "estres": 10.0, "enfoque": 70.0}


class FakeReasoning:
    def __init__(self, response: ActionResponse) -> None:
        self.response = response
        self.calls = 0

    def process(self, **kwargs) -> ActionResponse:
        self.calls += 1
        return self.response


def event(transaction_id: str, *, origin: str = "unity", message: str = "hola") -> EventoEntrante:
    return EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": message, "solicita_decision": True},
    )


def build_orchestrator(
    *,
    route: str = "persona",
    intent: str | None = None,
    behavior: FakeBehavior | None = None,
    memory: FakeMemory | None = None,
    response: ActionResponse | None = None,
    writer=None,
) -> tuple[Orchestrator, dict[str, int]]:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.behavior = behavior or FakeBehavior()
    orchestrator.memory = memory or FakeMemory()
    response = response or ActionResponse(
        pensamiento="pensamiento del probe",
        accion="hablar",
        parametros={"texto_a_comunicar": "respuesta del probe"},
        razon="respuesta determinista",
        valido=True,
    )
    orchestrator.reasoning = FakeReasoning(response)
    orchestrator.tool_registry = FakeRegistry()
    orchestrator.triage_router = FakeTriage(route, intent=intent)
    orchestrator.tool_policy = ToolPolicy()
    orchestrator.identity_state = FakeIdentity()
    orchestrator.audit_writer = writer or NullAuditWriter()
    orchestrator._transacciones_procesadas = OrderedDict()
    orchestrator._guardados_conversacionales_pendientes = OrderedDict()
    orchestrator._alias_candidates_pendientes = OrderedDict()
    orchestrator._notificaciones = deque(maxlen=50)
    orchestrator._transacciones_lock_guard = threading.Lock()
    orchestrator._transacciones_locks = {}
    orchestrator._descanso_terminal_lock = threading.RLock()
    orchestrator._descanso_terminal_pendiente = None
    orchestrator._adjuntar_esfuerzo_subjetivo = lambda value: value

    counters = {"persona": 0, "motor": 0, "oracle": 0, "execution": 0}

    def resolve_persona(**kwargs) -> ActionResponse:
        counters["persona"] += 1
        return response

    def resolve_motor(**kwargs) -> ActionResponse:
        counters["motor"] += 1
        return response

    def resolve_oracle(**kwargs) -> ActionResponse:
        counters["oracle"] += 1
        return response

    orchestrator._resolver_ruta_persona = resolve_persona
    orchestrator._resolver_ruta_motor = resolve_motor
    orchestrator._resolver_ruta_oracle = resolve_oracle

    original_execution = orchestrator._gestionar_ejecucion

    def observed_execution(evento, accion_saliente, action_response=None):
        counters["execution"] += 1
        return original_execution(
            evento,
            accion_saliente,
            action_response=action_response,
        )

    orchestrator._gestionar_ejecucion = observed_execution
    return orchestrator, counters


def run_threads(orchestrator: Orchestrator, events: list[EventoEntrante]) -> tuple[list, list[BaseException]]:
    results: list[object | None] = [None] * len(events)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            results[index] = orchestrator.procesar_evento(events[index])
        except BaseException as exc:  # La evidencia debe conservar excepciones de threads.
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(events))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=8)
        require(not thread.is_alive(), "Un thread del probe quedó bloqueado.")
    return results, errors


def probe_task_commands_and_late_outcome() -> dict:
    core = BehaviorCore()
    objectives = []
    for message in ("hola", "como estas?", "ve al cubo", "recuerda esto"):
        core.procesar_evento("comando_usuario", {"mensaje": message})
        objectives.append(core.task_context.objetivo_id if core.task_context else None)
    require(objectives == ["hola", "como estas?", "ve al cubo", "recuerda esto"], "Cada comando debe reemplazar la tarea.")

    core.procesar_evento("comando_usuario", {"mensaje": "ve al cubo"})
    core.procesar_evento("comando_usuario", {"mensaje": "hola"})
    core.procesar_evento(
        "resultado_accion",
        {"accion": "navegar_a_objeto", "exito": False},
    )
    summary = core.task_context.obtener_resumen() if core.task_context else {}
    require(summary.get("objetivo_id") == "hola", "El resultado tardío debe caer sobre la tarea social vigente.")
    require(summary.get("intentos_totales") == 1 and summary.get("intentos_fallidos") == 1, "El outcome debe mutar la tarea equivocada.")

    cancellation_core = BehaviorCore()
    cancellation_core.iniciar_tarea("ve al cubo")
    cancellation_core.procesar_evento("comando_usuario", {"mensaje": "detente"})
    cancelled = cancellation_core.cancelar_tarea("probe") or {}
    require(
        cancelled.get("objetivo_id") == "detente",
        "BehaviorCore reemplaza el objetivo antes de que Orchestrator cancele.",
    )
    return {
        "objectives": objectives,
        "late_outcome_task": summary.get("objetivo_id"),
        "cancelled_objective_reported": cancelled.get("objetivo_id"),
    }


def probe_task_reset_and_detector_amplification() -> dict:
    task = TaskContext("primera")
    task.umbral_abandono = 47.0
    task.reset("segunda")
    require(task.umbral_abandono == 47.0, "reset conserva el umbral previo en baseline.")

    class CountingTask(TaskContext):
        def __init__(self) -> None:
            super().__init__("contador")
            self.detector_calls = {"loop": 0, "stagnation": 0, "compulsion": 0}

        def detectar_loop(self, ventana: int = 5) -> bool:
            self.detector_calls["loop"] += 1
            return False

        def detectar_estancamiento(self, umbral_fallos_consecutivos: int = 4) -> bool:
            self.detector_calls["stagnation"] += 1
            return False

        def detectar_compulsion(self, ventana_tiempo: float = 30.0, repeticiones: int = 5) -> bool:
            self.detector_calls["compulsion"] += 1
            return False

    counting = CountingTask()
    counting.obtener_resumen()
    require(
        counting.detector_calls == {"loop": 1, "stagnation": 2, "compulsion": 2},
        "obtener_resumen reevalúa detectores de forma redundante.",
    )

    core = BehaviorCore()
    core.iniciar_tarea("objetivo estable")
    applied: list[str] = []
    original_apply = core.state_engine.aplicar_evento

    def recording_apply(tipo_evento: str, intensidad: float = 1.0, contexto=None):
        applied.append(tipo_evento)
        return original_apply(tipo_evento, intensidad, contexto)

    core.state_engine.aplicar_evento = recording_apply
    for _ in range(5):
        core.procesar_evento("resultado_accion", {"accion": "hablar", "exito": False})
    require(
        applied[-4:] == ["fracaso", "loop_detectado", "fracaso", "sobrecarga"],
        "El quinto fallo no activó las cuatro mutaciones emocionales esperadas.",
    )
    require(core.task_context is not None, "Los detectores/umbral no abandonan la tarea automáticamente.")
    return {
        "reset_threshold": task.umbral_abandono,
        "summary_detector_calls": counting.detector_calls,
        "fifth_outcome_events": applied[-4:],
        "task_still_active": core.task_context is not None,
    }


def probe_legacy_instances_and_audit_snapshot() -> dict:
    import core.behavior_core as behavior_module
    import core.personality as personality_module
    import core.state_engine as state_module

    fresh = BehaviorCore()
    require(behavior_module.behavior_core is not fresh, "El global legacy debe ser otra instancia.")
    require(behavior_module.behavior_core.state_engine is not state_module.estado_actual, "Los StateEngine globales son distintos.")
    require(behavior_module.behavior_core.personality is not personality_module.personalidad, "Las personalidades globales son distintas.")

    fresh.iniciar_tarea("snapshot pasivo")
    before = {
        "state": fresh.state_engine.obtener_estado(),
        "task": fresh.task_context.obtener_resumen(),
        "history_len": len(fresh.task_context.historial),
    }
    snapshot = fresh.obtener_snapshot_audit()
    after = {
        "state": fresh.state_engine.obtener_estado(),
        "task": fresh.task_context.obtener_resumen(),
        "history_len": len(fresh.task_context.historial),
    }
    require(before == after, "El snapshot AuditTrace debe ser pasivo respecto al estado/tarea.")
    require(set(snapshot) == {"state", "task"}, "El snapshot debe permanecer acotado.")
    return {
        "legacy_objects_distinct": True,
        "audit_snapshot_passive": True,
    }


def probe_sequential_idempotency_and_cache_window() -> dict:
    orchestrator, counters = build_orchestrator()
    same_event = event("same-id")
    first = orchestrator.procesar_evento(same_event)
    second = orchestrator.procesar_evento(same_event)
    require(second is first, "El cache devuelve la misma instancia AccionSaliente.")
    require(orchestrator.behavior.calls == 1, "Mismo ID no debe repetir BehaviorCore tras cache.")
    require(orchestrator.triage_router.calls == 1, "Mismo ID no debe repetir Triage.")
    require(len(orchestrator.memory.episodes) == 1 and len(orchestrator.memory.actions) == 1, "Mismo ID no duplica memorias tras cache.")
    require(counters["execution"] == 1 and len(orchestrator.identity_state.events) == 1, "Mismo ID no duplica ejecución/IdentityState.")

    orchestrator.procesar_evento(event("different-id", message="hola"))
    require(orchestrator.behavior.calls == 2, "Contenido igual con ID distinto debe reprocesarse.")

    bounded, _ = build_orchestrator()
    bounded.MAX_TRANSACCIONES_CACHE = 2
    bounded.procesar_evento(event("bounded-1"))
    bounded.procesar_evento(event("bounded-2"))
    bounded.procesar_evento(event("bounded-3"))
    require("bounded-1" not in bounded._transacciones_procesadas, "La ventana debe expulsar el ID más antiguo.")
    calls_before_replay = bounded.behavior.calls
    bounded.procesar_evento(event("bounded-1"))
    require(bounded.behavior.calls == calls_before_replay + 1, "Un ID expulsado vuelve a ejecutar todo el flujo.")
    return {
        "same_id_returns_same_object": True,
        "same_id_behavior_calls": 1,
        "different_id_behavior_calls": orchestrator.behavior.calls,
        "bounded_replay_reprocessed": True,
    }


def probe_concurrent_same_and_different_ids() -> dict:
    behavior = FirstCallBlockingBehavior()
    writer = BoundaryWriter()
    same, counters = build_orchestrator(behavior=behavior, writer=writer)
    results: list[object | None] = [None, None]
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            results[index] = same.procesar_evento(event("concurrent-same"))
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=worker, args=(0,))
    first_thread.start()
    require(behavior.entered.wait(timeout=5), "El primer thread no alcanzó BehaviorCore.")
    second_thread = threading.Thread(target=worker, args=(1,))
    second_thread.start()
    require(writer.second_start.wait(timeout=5), "El segundo thread no alcanzó event_start.")
    require(not behavior.second_behavior_entry.is_set(), "Same ID entró dos veces a BehaviorCore simultáneamente.")
    behavior.release.set()
    first_thread.join(timeout=8)
    second_thread.join(timeout=8)
    require(not first_thread.is_alive() and not second_thread.is_alive(), "Los retries same-ID no terminaron.")
    require(not errors, f"Errores en same-ID: {[type(error).__name__ for error in errors]}")
    require(results[0] is results[1], "El segundo same-ID debe observar el cache del primero.")
    require(behavior.calls == 1 and counters["execution"] == 1, "Same-ID concurrente debe ejecutar una vez.")

    parallel_behavior = BarrierBehavior()
    different, different_counters = build_orchestrator(behavior=parallel_behavior)
    different_results, different_errors = run_threads(
        different,
        [event("concurrent-a"), event("concurrent-b")],
    )
    require(not different_errors, f"Errores en IDs distintos: {[type(error).__name__ for error in different_errors]}")
    require(all(result is not None for result in different_results), "Ambos IDs distintos deben terminar.")
    require(parallel_behavior.overlap_confirmed, "IDs distintos deben solaparse dentro del BehaviorCore compartido.")
    require(different_counters["execution"] == 2, "Cada ID distinto debe ejecutar su rama.")
    return {
        "same_id_behavior_calls": behavior.calls,
        "same_id_execution_calls": counters["execution"],
        "different_ids_overlap_in_behavior": parallel_behavior.overlap_confirmed,
        "different_ids_execution_calls": different_counters["execution"],
    }


def probe_retry_after_pre_cache_failure() -> dict:
    memory = FakeMemory(fail_first_working_write=True)
    orchestrator, counters = build_orchestrator(memory=memory)

    execution_counter = {"calls": 0}

    def simulated_local_execution(evento, accion_saliente, action_response=None):
        execution_counter["calls"] += 1
        return "ok", {"mensaje": "ejecutada", "error": None, "datos_extra": None}

    orchestrator._gestionar_ejecucion = simulated_local_execution
    target = event("retry-after-working-failure", origin="sandbox")
    try:
        orchestrator.procesar_evento(target)
    except RuntimeError as exc:
        require("working-memory" in str(exc), "Debe propagarse el fallo posterior a ejecución.")
    else:
        raise AssertionError("La primera ejecución debía fallar antes del cache.")

    require(target.id_transaccion not in orchestrator._transacciones_procesadas, "El fallo pre-cache no deja recibo.")
    recovered = orchestrator.procesar_evento(target)
    require(recovered is not None, "El retry debe completar el flujo.")
    require(execution_counter["calls"] == 2, "El retry repite la ejecución ya realizada.")
    require(orchestrator.behavior.calls == 2, "El retry repite BehaviorCore.")
    require(len(memory.episodes) == 2, "El retry duplica escritura episódica previa.")
    require(memory.working_attempts == 2 and len(memory.actions) == 1, "WorkingMemory falla una vez y luego acepta.")
    return {
        "execution_calls": execution_counter["calls"],
        "behavior_calls": orchestrator.behavior.calls,
        "episode_writes": len(memory.episodes),
        "cached_only_after_retry": target.id_transaccion in orchestrator._transacciones_procesadas,
    }


def probe_behavior_partial_failure_retry() -> dict:
    behavior = FirstFailureBehavior()
    orchestrator, counters = build_orchestrator(behavior=behavior)
    target = event("behavior-partial")
    first = orchestrator.procesar_evento(target)
    second = orchestrator.procesar_evento(target)
    require(first is None and second is not None, "BehaviorCore fallido retorna None y permite retry.")
    require(behavior.state_mutations == 2, "La mutación anterior a la excepción no se revierte.")
    require(counters["execution"] == 1, "Solo el retry recuperado llega a ejecución.")
    return {
        "first_result": None,
        "partial_state_mutations": behavior.state_mutations,
        "retry_execution_calls": counters["execution"],
    }


def probe_invalid_action_side_effects_and_memory_fail_open() -> dict:
    invalid = ActionResponse(
        pensamiento="pensamiento tras fallo",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "cubo"},
        razon="salida inválida",
        valido=False,
        error="simulated invalid decision",
    )
    orchestrator, counters = build_orchestrator(response=invalid)
    result = orchestrator.procesar_evento(event("invalid-unity"))
    require(result is not None and result.nombre == "none", "Fuera de terminal, inválido se degrada a none.")
    require(len(orchestrator.memory.episodes) == 1, "La memoria episódica se escribió antes de validar.")
    require("invalid-unity" in orchestrator._transacciones_procesadas, "La salida inválida también se cachea.")
    require(counters["execution"] == 1, "La gestión de ejecución clasifica el no-op.")
    require(len(orchestrator.identity_state.events) == 1 and orchestrator.identity_state.events[0]["success"] is False, "IdentityState registra fallo lógico.")
    require(len(orchestrator._notificaciones) == 2, "El inválido none genera pensamiento y error.")

    terminal, _ = build_orchestrator(response=invalid)
    terminal_result = terminal.procesar_evento(event("invalid-terminal", origin="terminal"))
    require(terminal_result is not None and terminal_result.nombre == "hablar", "Terminal convierte el inválido en hablar.")
    require(len(terminal.memory.actions) == 1 and len(terminal.memory.turns) == 1, "El fallback terminal registra acción y turno.")
    require(terminal.identity_state.events[0]["success"] is False, "El error preservado marca fallo biológico.")

    failing_memory = FakeMemory(fail_reads=True, fail_episode_write=True)
    fail_open, fail_open_counters = build_orchestrator(memory=failing_memory)
    fail_open_result = fail_open.procesar_evento(event("memory-fail-open"))
    require(fail_open_result is not None and fail_open_result.nombre == "hablar", "Persona continúa si Memory falla.")
    require(fail_open_counters["persona"] == 1 and fail_open_counters["execution"] == 1, "El fallo de Memory no corta decisión/dispatch.")
    require("memory-fail-open" in fail_open._transacciones_procesadas, "El resultado sin memoria se cachea.")
    return {
        "invalid_unity_action": result.nombre,
        "invalid_unity_notifications": len(orchestrator._notificaciones),
        "invalid_terminal_action": terminal_result.nombre,
        "memory_failure_continued": True,
    }


def probe_motor_exception_repeats_predecision_effects() -> dict:
    class BombMotor:
        def diagnosticar_comando_usuario(self, *args, **kwargs):
            raise RuntimeError("simulated motor diagnostic failure")

    memory = FakeMemory()
    orchestrator, _ = build_orchestrator(route="motor", memory=memory)
    orchestrator.motor_engine = BombMotor()
    del orchestrator.__dict__["_resolver_ruta_motor"]
    target = event("motor-failure")
    failures = 0
    for _ in range(2):
        try:
            orchestrator.procesar_evento(target)
        except RuntimeError as exc:
            require("motor diagnostic" in str(exc), "Debe propagarse el fallo Motor no capturado.")
            failures += 1
    require(failures == 2, "Ambos intentos deben fallar antes del cache.")
    require(orchestrator.behavior.calls == 2 and len(memory.episodes) == 2, "Cada retry repite estado y memoria predecisión.")
    require(target.id_transaccion not in orchestrator._transacciones_procesadas, "Motor fallido no crea cache.")
    return {
        "propagated_failures": failures,
        "behavior_calls": orchestrator.behavior.calls,
        "episode_writes": len(memory.episodes),
    }


def probe_alias_correlation_fallback() -> dict:
    orchestrator = object.__new__(Orchestrator)
    orchestrator._alias_candidates_pendientes = OrderedDict(
        [
            ("action-a", {"id_accion": "action-a", "action": "navegar_a_objeto", "target_name": "cubo-a", "motor_resolution": {"target_id": "a"}}),
            ("action-b", {"id_accion": "action-b", "action": "navegar_a_objeto", "target_name": "cubo-b", "motor_resolution": {"target_id": "b"}}),
        ]
    )
    matched = orchestrator._buscar_alias_pendiente_para_resultado({"id_accion": "unknown-action"})
    require(matched is not None and matched["id_accion"] == "action-b", "Un ID desconocido sin datos cae al candidato más reciente.")
    require("action-b" not in orchestrator._alias_candidates_pendientes, "El fallback consume el candidato incorrecto.")
    duplicate = orchestrator._buscar_alias_pendiente_para_resultado({"id_accion": "unknown-action"})
    require(duplicate is not None and duplicate["id_accion"] == "action-a", "Un duplicado puede consumir otro candidato.")
    return {
        "unknown_result_matched": matched["id_accion"],
        "duplicate_result_matched": duplicate["id_accion"],
    }


def probe_tool_policy_and_confirmation_boundary() -> dict:
    policy = ToolPolicy()
    available = ["hablar", "navegar_a_objeto", "herramienta_admin"]
    require(policy.seleccionar(available, origen="terminal", route="motor") == ["hablar"], "Terminal solo recibe hablar.")
    require(policy.seleccionar(available, origen="unity", route="persona") == ["hablar"], "Persona solo recibe hablar.")
    require(
        policy.seleccionar(available, origen="sandbox", route="reasoning") == available,
        "Sandbox conserva la lista; ToolPolicy no valida parámetros ni ejecuta.",
    )

    orchestrator = object.__new__(Orchestrator)
    pending = AccionSaliente(
        id_accion="confirmation-pending",
        nombre="herramienta",
        parametros={},
        requiere_confirmacion=True,
    )
    direct = pending.model_copy(update={"id_accion": "confirmation-false", "requiere_confirmacion": False})
    fake_result = SimpleNamespace(exito=True, mensaje="ok", error=None, datos_extra=None)
    with patch("core.orchestrator.ActionExecutor.ejecutar", return_value=fake_result) as executor:
        pending_status, _ = orchestrator._gestionar_ejecucion(
            event("confirmation-a", origin="sandbox"),
            pending,
        )
        require(executor.call_count == 0, "requiere_confirmacion=True detiene antes del executor.")
        direct_status, _ = orchestrator._gestionar_ejecucion(
            event("confirmation-b", origin="sandbox"),
            direct,
        )
        require(executor.call_count == 1, "requiere_confirmacion=False sí entra al executor local.")
    return {
        "tool_policy_role": "filter_only",
        "confirmation_true_status": pending_status,
        "confirmation_false_status": direct_status,
    }


def probe_unprotected_cache_and_notifications() -> dict:
    class PausingCache(OrderedDict):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.value_observed = threading.Event()
            self.release_get = threading.Event()

        def get(self, key, default=None):
            value = super().get(key, default)
            if key == "old":
                self.value_observed.set()
                require(self.release_get.wait(timeout=5), "No se liberó cache.get.")
            return value

    orchestrator = object.__new__(Orchestrator)
    old_action = AccionSaliente(id_accion="old-action", nombre="hablar", parametros={})
    new_action = AccionSaliente(id_accion="new-action", nombre="hablar", parametros={})
    cache = PausingCache([("old", old_action)])
    orchestrator._transacciones_procesadas = cache
    orchestrator.MAX_TRANSACCIONES_CACHE = 1
    observed_error: list[BaseException] = []

    def read_old() -> None:
        try:
            orchestrator._obtener_transaccion_cacheada("old")
        except BaseException as exc:
            observed_error.append(exc)

    reader = threading.Thread(target=read_old)
    reader.start()
    require(cache.value_observed.wait(timeout=5), "El reader no observó el valor viejo.")
    orchestrator._cachear_transaccion("new", new_action)
    cache.release_get.set()
    reader.join(timeout=5)
    require(not reader.is_alive(), "El reader de cache quedó bloqueado.")
    require(len(observed_error) == 1 and isinstance(observed_error[0], KeyError), "get + move_to_end puede correr contra eviction.")

    class PausingClearDeque(deque):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.before_clear = threading.Event()
            self.release_clear = threading.Event()

        def clear(self) -> None:
            self.before_clear.set()
            require(self.release_clear.wait(timeout=5), "No se liberó notification.clear.")
            super().clear()

    queue = PausingClearDeque(["initial"], maxlen=50)
    orchestrator._notificaciones = queue
    returned: list[list] = []
    consumer = threading.Thread(target=lambda: returned.append(orchestrator.obtener_notificaciones()))
    consumer.start()
    require(queue.before_clear.wait(timeout=5), "El consumidor no llegó a clear.")
    queue.append("late")
    queue.release_clear.set()
    consumer.join(timeout=5)
    require(not consumer.is_alive(), "El consumidor de notificaciones quedó bloqueado.")
    require(returned == [["initial"]] and list(queue) == [], "Una notificación entre snapshot y clear se pierde.")
    return {
        "cache_race_error": type(observed_error[0]).__name__,
        "late_notification_lost": True,
    }


PROBES = [
    ("task_commands_and_late_outcome", probe_task_commands_and_late_outcome),
    ("task_reset_and_detector_amplification", probe_task_reset_and_detector_amplification),
    ("legacy_instances_and_audit_snapshot", probe_legacy_instances_and_audit_snapshot),
    ("sequential_idempotency_and_cache_window", probe_sequential_idempotency_and_cache_window),
    ("concurrent_same_and_different_ids", probe_concurrent_same_and_different_ids),
    ("retry_after_pre_cache_failure", probe_retry_after_pre_cache_failure),
    ("behavior_partial_failure_retry", probe_behavior_partial_failure_retry),
    ("invalid_action_and_memory_fail_open", probe_invalid_action_side_effects_and_memory_fail_open),
    ("motor_exception_repeats_predecision_effects", probe_motor_exception_repeats_predecision_effects),
    ("alias_correlation_fallback", probe_alias_correlation_fallback),
    ("tool_policy_and_confirmation_boundary", probe_tool_policy_and_confirmation_boundary),
    ("unprotected_cache_and_notifications", probe_unprotected_cache_and_notifications),
]


def main() -> int:
    install_safety_barriers()
    report = {"probe": "core_orchestrator", "status": "PASS", "tests": []}
    for name, probe in PROBES:
        try:
            evidence = probe()
        except Exception as exc:
            report["status"] = "FAIL"
            report["tests"].append(
                {
                    "name": name,
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        else:
            report["tests"].append(
                {"name": name, "status": "PASS", "evidence": evidence}
            )

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
