"""Pruebas permanentes y deterministas de atomicidad/ownership para ROOT-012."""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
import threading
from typing import Any, Callable

from ai_engine.learned_alias_candidates import LearnedAliasCandidate
from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante, NotificacionSaliente
from core.audit_trace import AuditTrace
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from memory.memory_manager import MemoryManager
from memory.working_memory import WorkingMemory


WAIT_SECONDS = 3.0


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class ObservedLock:
    """Lock instrumentado con señal determinista de contención."""

    def __init__(self, *, reentrant: bool = False) -> None:
        self._lock = threading.RLock() if reentrant else threading.Lock()
        self._meta_lock = threading.Lock()
        self._owners: dict[int, int] = {}
        self.contended = threading.Event()
        self.acquisitions = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        acquired = self._lock.acquire(False)
        if not acquired:
            if not blocking:
                return False
            self.contended.set()
            acquired = (
                self._lock.acquire()
                if timeout == -1
                else self._lock.acquire(timeout=timeout)
            )
        if acquired:
            owner = threading.get_ident()
            with self._meta_lock:
                self._owners[owner] = self._owners.get(owner, 0) + 1
                self.acquisitions += 1
        return acquired

    def release(self) -> None:
        owner = threading.get_ident()
        with self._meta_lock:
            depth = self._owners.get(owner, 0) - 1
            if depth > 0:
                self._owners[owner] = depth
            else:
                self._owners.pop(owner, None)
        self._lock.release()

    def held_by_current_thread(self) -> bool:
        with self._meta_lock:
            return self._owners.get(threading.get_ident(), 0) > 0

    def __enter__(self) -> "ObservedLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def _event(transaction_id: str) -> EventoEntrante:
    return EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": transaction_id},
    )


def _action(name: str) -> AccionSaliente:
    return AccionSaliente(id_accion=f"action-{name}", nombre="hablar", parametros={})


def _join_threads(threads: list[threading.Thread], errors: list[BaseException]) -> None:
    for thread in threads:
        thread.join(timeout=WAIT_SECONDS)
        assert_true(not thread.is_alive(), f"El thread {thread.name} no terminó.")
    if errors:
        raise errors[0]


def _thread_target(errors: list[BaseException], function: Callable[[], None]) -> Callable[[], None]:
    def run() -> None:
        try:
            function()
        except BaseException as exc:
            errors.append(exc)

    return run


def _bare_orchestrator() -> Orchestrator:
    orchestrator = object.__new__(Orchestrator)
    orchestrator._transacciones_procesadas = OrderedDict()
    orchestrator._guardados_conversacionales_pendientes = OrderedDict()
    orchestrator._alias_candidates_pendientes = OrderedDict()
    orchestrator._notificaciones = deque(maxlen=50)
    orchestrator._transacciones_cache_lock = threading.Lock()
    orchestrator._guardados_conversacionales_lock = threading.Lock()
    orchestrator._alias_candidates_lock = threading.Lock()
    orchestrator._notificaciones_lock = threading.Lock()
    orchestrator._transacciones_lock_guard = threading.Lock()
    orchestrator._transacciones_locks = {}
    orchestrator.MAX_TRANSACCIONES_CACHE = 2000
    orchestrator._begin_audit_timing = lambda: None
    orchestrator._end_audit_timing = lambda: None
    orchestrator._iniciar_captura_llm_audit = lambda: None
    orchestrator._consumir_llm_audit = lambda: []
    orchestrator._registrar_errores_llm_audit = lambda *_args, **_kwargs: 0
    orchestrator._registrar_error_audit = lambda *_args, **_kwargs: False
    orchestrator._registrar_limite_audit = lambda *_args, **_kwargs: True
    orchestrator._elapsed_total_ms = lambda _started_ns: 0.0
    return orchestrator


class BlockingBehavior(BehaviorCore):
    def __init__(self) -> None:
        super().__init__()
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self._metrics_lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def _procesar_evento_locked(self, tipo_evento: str, detalles: dict | None = None) -> dict:
        with self._metrics_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
            first = self.calls == 1
        try:
            if first:
                self.first_entered.set()
                assert_true(
                    self.release_first.wait(timeout=WAIT_SECONDS),
                    "No se liberó la primera sección Behavior.",
                )
            return {"transaction": tipo_evento, "details": dict(detalles or {})}
        finally:
            with self._metrics_lock:
                self.active -= 1


def test_different_ids_serialize_behavior_and_overlap_after_snapshot() -> None:
    behavior = BlockingBehavior()
    owner_lock = ObservedLock(reentrant=True)
    behavior._owner_lock = owner_lock
    orchestrator = _bare_orchestrator()
    post_behavior = threading.Barrier(2)
    results: list[AccionSaliente] = []
    errors: list[BaseException] = []

    def pipeline(evento: EventoEntrante, trace_id: str | None = None) -> AccionSaliente:
        behavior.procesar_evento(evento.tipo_evento, evento.detalles)
        post_behavior.wait(timeout=WAIT_SECONDS)
        return _action(evento.id_transaccion)

    orchestrator._procesar_evento_serializado = pipeline
    start = threading.Barrier(3)

    def process(transaction_id: str) -> None:
        start.wait(timeout=WAIT_SECONDS)
        results.append(orchestrator.procesar_evento(_event(transaction_id)))

    threads = [
        threading.Thread(target=_thread_target(errors, lambda: process("different-a"))),
        threading.Thread(target=_thread_target(errors, lambda: process("different-b"))),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=WAIT_SECONDS)
    assert_true(behavior.first_entered.wait(timeout=WAIT_SECONDS), "Ningún turno entró a Behavior.")
    assert_true(owner_lock.contended.wait(timeout=WAIT_SECONDS), "El segundo turno no contendió por Behavior.")
    assert_true(behavior.max_active == 1, "Dos IDs mutaron Behavior simultáneamente.")
    behavior.release_first.set()
    _join_threads(threads, errors)
    assert_true(behavior.max_active == 1 and len(results) == 2, "Behavior debe ser serializable.")


def test_same_id_remains_serialized_and_idempotent() -> None:
    orchestrator = _bare_orchestrator()
    transaction_lock = ObservedLock(reentrant=True)
    orchestrator._transacciones_locks["same-id"] = {"lock": transaction_lock, "users": 0}
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = {"functional": 0}
    results: list[AccionSaliente] = []
    errors: list[BaseException] = []

    def pipeline(evento: EventoEntrante, trace_id: str | None = None) -> AccionSaliente:
        cached = orchestrator._obtener_transaccion_cacheada(evento.id_transaccion)
        if cached is not None:
            return cached
        calls["functional"] += 1
        first_entered.set()
        assert_true(release_first.wait(timeout=WAIT_SECONDS), "No se liberó el primer same-ID.")
        result = _action("same-id")
        orchestrator._cachear_transaccion(evento.id_transaccion, result)
        return result

    orchestrator._procesar_evento_serializado = pipeline
    first = threading.Thread(
        target=_thread_target(errors, lambda: results.append(orchestrator.procesar_evento(_event("same-id"))))
    )
    second = threading.Thread(
        target=_thread_target(errors, lambda: results.append(orchestrator.procesar_evento(_event("same-id"))))
    )
    first.start()
    assert_true(first_entered.wait(timeout=WAIT_SECONDS), "El primer same-ID no entró.")
    second.start()
    assert_true(transaction_lock.contended.wait(timeout=WAIT_SECONDS), "El retry no esperó el lock per-ID.")
    release_first.set()
    _join_threads([first, second], errors)
    assert_true(calls["functional"] == 1, "El mismo ID ejecutó dos veces el trabajo funcional.")
    assert_true(len(results) == 2 and results[0] is results[1], "El retry debe reutilizar el cache actual.")


def test_behavior_snapshot_does_not_change_retroactively() -> None:
    behavior = BehaviorCore()
    behavior.iniciar_tarea("objetivo-a")
    first = behavior.procesar_evento(
        "interaccion_social",
        {"objeto_mas_cercano": {"nombre": "objeto-a", "distancia": 1.0}},
    )
    frozen = deepcopy(first)
    behavior.iniciar_tarea("objetivo-b")
    behavior.procesar_evento(
        "critica",
        {"objeto_mas_cercano": {"nombre": "objeto-b", "distancia": 2.0}},
    )
    assert_true(first == frozen, "Un turno posterior reescribió el snapshot anterior.")
    assert_true(first["tarea"]["objetivo_id"] == "objetivo-a", "El snapshot mezcló TaskContext posterior.")


def test_cache_hit_and_lru_touch_are_atomic() -> None:
    orchestrator = _bare_orchestrator()
    action_a = _action("cache-a")
    action_b = _action("cache-b")
    orchestrator._transacciones_procesadas = OrderedDict((("a", action_a), ("b", action_b)))
    result = orchestrator._obtener_transaccion_cacheada("a")
    assert_true(result is action_a, "El hit debe conservar el valor actual del cache.")
    assert_true(list(orchestrator._transacciones_procesadas) == ["b", "a"], "El hit debe tocar LRU.")


def test_cache_put_and_eviction_are_atomic_and_bounded() -> None:
    orchestrator = _bare_orchestrator()
    orchestrator.MAX_TRANSACCIONES_CACHE = 2
    orchestrator._cachear_transaccion("a", _action("a"))
    orchestrator._cachear_transaccion("b", _action("b"))
    orchestrator._cachear_transaccion("c", _action("c"))
    assert_true(list(orchestrator._transacciones_procesadas) == ["b", "c"], "PUT debe expulsar el LRU.")


def test_historical_cache_race_no_longer_raises_key_error() -> None:
    class PausingCache(OrderedDict):
        def __init__(self, *args: object) -> None:
            super().__init__(*args)
            self.observed = threading.Event()
            self.release = threading.Event()

        def get(self, key: object, default: object = None) -> object:
            value = super().get(key, default)
            if key == "old":
                self.observed.set()
                assert_true(self.release.wait(timeout=WAIT_SECONDS), "No se liberó cache.get.")
            return value

    orchestrator = _bare_orchestrator()
    cache = PausingCache((("old", _action("old")),))
    lock = ObservedLock()
    orchestrator._transacciones_procesadas = cache
    orchestrator._transacciones_cache_lock = lock
    orchestrator.MAX_TRANSACCIONES_CACHE = 1
    read: list[AccionSaliente | None] = []
    errors: list[BaseException] = []
    reader = threading.Thread(
        target=_thread_target(errors, lambda: read.append(orchestrator._obtener_transaccion_cacheada("old")))
    )
    writer = threading.Thread(
        target=_thread_target(errors, lambda: orchestrator._cachear_transaccion("new", _action("new")))
    )
    reader.start()
    assert_true(cache.observed.wait(timeout=WAIT_SECONDS), "El reader no observó el hit.")
    writer.start()
    assert_true(lock.contended.wait(timeout=WAIT_SECONDS), "El writer no esperó el cache lock.")
    cache.release.set()
    _join_threads([reader, writer], errors)
    assert_true(read and read[0] is not None, "El hit coherente debe devolverse.")
    assert_true(list(cache) == ["new"] and len(cache) == 1, "La eviction debe quedar coherente.")


def test_private_notification_sources_do_not_touch_queue_during_drain() -> None:
    class PausingClearDeque(deque):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.before_clear = threading.Event()
            self.release = threading.Event()
            self.pause_once = True

        def clear(self) -> None:
            if self.pause_once:
                self.pause_once = False
                self.before_clear.set()
                assert_true(self.release.wait(timeout=WAIT_SECONDS), "No se liberó notification.clear.")
            super().clear()

    initial = NotificacionSaliente(tipo="info", mensaje="initial")
    queue = PausingClearDeque((initial,), maxlen=50)
    orchestrator = _bare_orchestrator()
    lock = ObservedLock()
    orchestrator._notificaciones = queue
    orchestrator._notificaciones_lock = lock
    first_batch: list[list[NotificacionSaliente]] = []
    errors: list[BaseException] = []
    drainer = threading.Thread(
        target=_thread_target(errors, lambda: first_batch.append(orchestrator.obtener_notificaciones()))
    )
    drainer.start()
    assert_true(queue.before_clear.wait(timeout=WAIT_SECONDS), "El drain no llegó a clear.")
    emitted = orchestrator._agregar_notificacion_si_corresponde(
        ActionResponse(pensamiento="private", accion="none", error="internal")
    )
    assert_true(emitted is False, "Cognition y errores internos no producen notificaciones.")
    assert_true(not lock.contended.is_set(), "La fuente privada no debe intentar escribir la cola.")
    queue.release.set()
    _join_threads([drainer], errors)
    second_batch = orchestrator.obtener_notificaciones()
    assert_true([item.mensaje for item in first_batch[0]] == ["initial"], "El primer batch debe ser estable.")
    assert_true(second_batch == [], "La fuente privada no debe dejar un segundo batch.")


def test_two_notification_drains_do_not_duplicate() -> None:
    orchestrator = _bare_orchestrator()
    orchestrator._notificaciones.append(
        NotificacionSaliente(tipo="info", mensaje="public intent fixture")
    )
    first = orchestrator.obtener_notificaciones()
    second = orchestrator.obtener_notificaciones()
    assert_true(len(first) == 1 and second == [], "Dos drains no deben duplicar elementos.")


def test_working_memory_append_and_snapshot_are_owned() -> None:
    working = WorkingMemory(max_size=4)
    lock = ObservedLock()
    working._lock = lock
    errors: list[BaseException] = []
    lock.acquire()
    writer = threading.Thread(
        target=_thread_target(errors, lambda: working.agregar("owned", "ok"))
    )
    writer.start()
    assert_true(lock.contended.wait(timeout=WAIT_SECONDS), "WorkingMemory.append no usó su owner lock.")
    lock.release()
    _join_threads([writer], errors)
    assert_true([item.accion for item in working.obtener_todas()] == ["owned"], "Append protegido se perdió.")


def test_working_memory_concurrent_read_write_returns_coherent_snapshots() -> None:
    class PausingIterDeque(deque):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.iterating = threading.Event()
            self.release = threading.Event()
            self.pause_once = True

        def __iter__(self):
            if self.pause_once:
                self.pause_once = False
                self.iterating.set()
                assert_true(self.release.wait(timeout=WAIT_SECONDS), "No se liberó el snapshot WorkingMemory.")
            return super().__iter__()

    working = WorkingMemory(max_size=4)
    working.agregar("before", "ok")
    buffer = PausingIterDeque(working.obtener_todas(), maxlen=4)
    lock = ObservedLock()
    working._buffer = buffer
    working._lock = lock
    snapshots: list[list[Any]] = []
    errors: list[BaseException] = []
    reader = threading.Thread(
        target=_thread_target(errors, lambda: snapshots.append(working.obtener_todas()))
    )
    writer = threading.Thread(
        target=_thread_target(errors, lambda: working.agregar("after", "ok"))
    )
    reader.start()
    assert_true(buffer.iterating.wait(timeout=WAIT_SECONDS), "El reader no inició snapshot.")
    writer.start()
    assert_true(lock.contended.wait(timeout=WAIT_SECONDS), "El writer no esperó el snapshot.")
    buffer.release.set()
    _join_threads([reader, writer], errors)
    assert_true([item.accion for item in snapshots[0]] == ["before"], "El snapshot mezcló un append parcial.")
    assert_true([item.accion for item in working.obtener_todas()] == ["before", "after"], "La cola final es incoherente.")


def test_consolidation_counter_does_not_lose_ram_increments() -> None:
    manager = object.__new__(MemoryManager)
    manager._ram_state_lock = threading.Lock()
    manager._observaciones_desde_revision = 0
    manager.revision_consolidacion_cada = 10
    manager.consolidar_memorias_si_hace_falta = lambda: None
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def increment() -> None:
        start.wait(timeout=WAIT_SECONDS)
        manager._programar_consolidacion_si_aplica("observacion_visual", {}, "probe")

    threads = [
        threading.Thread(target=_thread_target(errors, increment)),
        threading.Thread(target=_thread_target(errors, increment)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=WAIT_SECONDS)
    _join_threads(threads, errors)
    assert_true(manager._observaciones_desde_revision == 2, "Dos incrementos RAM colapsaron en uno.")


def test_memory_ram_lock_is_released_before_persistent_operations() -> None:
    manager = object.__new__(MemoryManager)
    lock = ObservedLock()
    manager._ram_state_lock = lock
    manager._alias_aprendidos_guardados = set()
    candidate = LearnedAliasCandidate(
        object_id="Tree_01",
        human_concept="arbol",
        confidence=1.0,
    )

    def persistent_check(_candidate: LearnedAliasCandidate) -> bool:
        assert_true(not lock.held_by_current_thread(), "El RAM lock alcanzó un lookup persistente.")
        return False

    def persistent_write(**_kwargs: object) -> str:
        assert_true(not lock.held_by_current_thread(), "El RAM lock alcanzó una escritura persistente.")
        return "alias-memory"

    manager._alias_aprendido_ya_persistido = persistent_check
    manager.guardar_regla = persistent_write
    result = manager.guardar_alias_aprendido(candidate)
    assert_true(result == "alias-memory" and lock.acquisitions >= 2, "El cache RAM no quedó protegido.")

    manager._observaciones_desde_revision = 0
    manager.revision_consolidacion_cada = 1
    consolidated = threading.Event()

    def consolidate() -> None:
        assert_true(not lock.held_by_current_thread(), "El RAM lock alcanzó consolidación Chroma.")
        consolidated.set()

    manager.consolidar_memorias_si_hace_falta = consolidate
    manager._programar_consolidacion_si_aplica("observacion_visual", {}, "probe")
    assert_true(consolidated.is_set(), "El token de consolidación no se ejecutó fuera del lock.")


def test_orchestrator_pending_collections_use_their_owners() -> None:
    orchestrator = _bare_orchestrator()
    conversation_lock = ObservedLock()
    alias_lock = ObservedLock()
    orchestrator._guardados_conversacionales_lock = conversation_lock
    orchestrator._alias_candidates_lock = alias_lock
    errors: list[BaseException] = []

    conversation_lock.acquire()
    conversation_writer = threading.Thread(
        target=_thread_target(
            errors,
            lambda: orchestrator._marcar_guardado_conversacional_pendiente("pending", "exploradora"),
        )
    )
    conversation_writer.start()
    assert_true(conversation_lock.contended.wait(timeout=WAIT_SECONDS), "Pending save no usó su lock.")
    conversation_lock.release()
    _join_threads([conversation_writer], errors)

    response = ActionResponse(
        pensamiento="",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "Tree_01"},
        metadata={
            "motor_resolution": {"status": "ready", "target_id": "Tree_01"},
            "alias_learning": {"user_query": "ve al arbol"},
        },
    )
    alias_lock.acquire()
    alias_writer = threading.Thread(
        target=_thread_target(
            errors,
            lambda: orchestrator._registrar_alias_candidato_pendiente(response, _action("pending-alias")),
        )
    )
    alias_writer.start()
    assert_true(alias_lock.contended.wait(timeout=WAIT_SECONDS), "Pending alias no usó su lock.")
    alias_lock.release()
    _join_threads([alias_writer], errors)
    assert_true("pending" in orchestrator._guardados_conversacionales_pendientes, "Pending save se perdió.")
    assert_true(orchestrator._alias_candidates_pendientes, "Pending alias se perdió.")


def test_lock_order_has_no_nested_behavior_or_resource_deadlock() -> None:
    behavior = BehaviorCore()
    finished = threading.Event()
    errors: list[BaseException] = []

    def nested_behavior() -> None:
        behavior.iniciar_tarea("nested")
        behavior.finalizar_tarea(True)
        finished.set()

    thread = threading.Thread(target=_thread_target(errors, nested_behavior))
    thread.start()
    assert_true(finished.wait(timeout=WAIT_SECONDS), "Behavior→Behavior/StateEngine produjo deadlock.")
    _join_threads([thread], errors)

    orchestrator = _bare_orchestrator()
    cache_lock = ObservedLock()
    orchestrator._transacciones_cache_lock = cache_lock
    cache_lock.acquire()
    notification_finished = threading.Event()
    independent = threading.Thread(
        target=_thread_target(
            errors,
            lambda: (orchestrator.obtener_notificaciones(), notification_finished.set()),
        )
    )
    independent.start()
    assert_true(notification_finished.wait(timeout=WAIT_SECONDS), "Notification lock dependió del cache lock.")
    cache_lock.release()
    _join_threads([independent], errors)


def test_concurrent_audit_traces_keep_transaction_ids_separate() -> None:
    class RecordingWriter:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.traces: list[AuditTrace] = []

        def write(self, trace: AuditTrace) -> bool:
            with self._lock:
                self.traces.append(trace)
            return True

    orchestrator = _bare_orchestrator()
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    orchestrator.behavior = type(
        "SnapshotBehavior",
        (),
        {"obtener_snapshot_audit": lambda self: {"state": {}, "task": {}}},
    )()
    orchestrator._registrar_limite_audit = Orchestrator._registrar_limite_audit.__get__(orchestrator)
    gate = threading.Barrier(2)
    orchestrator._procesar_evento_serializado = (
        lambda evento, trace_id=None: (gate.wait(timeout=WAIT_SECONDS), _action(evento.id_transaccion))[1]
    )
    errors: list[BaseException] = []
    threads = [
        threading.Thread(
            target=_thread_target(errors, lambda: orchestrator.procesar_evento(_event("trace-a")))
        ),
        threading.Thread(
            target=_thread_target(errors, lambda: orchestrator.procesar_evento(_event("trace-b")))
        ),
    ]
    for thread in threads:
        thread.start()
    _join_threads(threads, errors)
    grouped: dict[str, list[AuditTrace]] = {}
    for trace in writer.traces:
        grouped.setdefault(trace.id_transaccion, []).append(trace)
    assert_true(set(grouped) == {"trace-a", "trace-b"}, "AuditTrace mezcló IDs de transacción.")
    trace_ids: set[str] = set()
    for transaction_id, traces in grouped.items():
        assert_true([trace.stage for trace in traces] == ["event_start", "event_end"], f"Stages inválidos para {transaction_id}.")
        ids = {trace.trace_id for trace in traces}
        assert_true(len(ids) == 1, f"Los límites de {transaction_id} no comparten trace_id.")
        trace_ids.update(ids)
    assert_true(len(trace_ids) == 2, "Dos transacciones reutilizaron el mismo trace_id.")


def main() -> None:
    tests = [
        test_different_ids_serialize_behavior_and_overlap_after_snapshot,
        test_same_id_remains_serialized_and_idempotent,
        test_behavior_snapshot_does_not_change_retroactively,
        test_cache_hit_and_lru_touch_are_atomic,
        test_cache_put_and_eviction_are_atomic_and_bounded,
        test_historical_cache_race_no_longer_raises_key_error,
        test_private_notification_sources_do_not_touch_queue_during_drain,
        test_two_notification_drains_do_not_duplicate,
        test_working_memory_append_and_snapshot_are_owned,
        test_working_memory_concurrent_read_write_returns_coherent_snapshots,
        test_consolidation_counter_does_not_lose_ram_increments,
        test_memory_ram_lock_is_released_before_persistent_operations,
        test_orchestrator_pending_collections_use_their_owners,
        test_lock_order_has_no_nested_behavior_or_resource_deadlock,
        test_concurrent_audit_traces_keep_transaction_ids_separate,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] ROOT-012 shared state atomicity.")


if __name__ == "__main__":
    main()
