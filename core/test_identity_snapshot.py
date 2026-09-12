"""ROOT-016: contratos, ownership y captura canonica (tests 01-48)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, is_dataclass
import ast
import math
from pathlib import Path
import threading

from ai_engine.identity_state import IdentityState
from core.behavior_core import BehaviorCore
from core.identity_snapshot import (
    IdentityComponentsV1,
    IdentitySnapshotProvider,
    IdentitySnapshotV1,
    PersonalitySnapshotV1,
    SelfStateSnapshotV1,
    StaticSelfV1,
    build_static_self_v1,
)


def _state(**updates) -> SelfStateSnapshotV1:
    values = {"energia": 80.0, "estres": 10.0, "foco": 70.0}
    values.update(updates)
    return SelfStateSnapshotV1(**values)


def _traits(**updates) -> PersonalitySnapshotV1:
    values = {
        "resiliencia": 0.1,
        "impulsividad": -0.2,
        "paciencia": 0.3,
        "sociabilidad": 0.4,
        "metodico": 0.5,
    }
    values.update(updates)
    return PersonalitySnapshotV1(**values)


def _snapshot(**updates) -> IdentitySnapshotV1:
    values = {
        "schema_version": 1,
        "generation": 1,
        "self": build_static_self_v1(),
        "state": _state(),
        "traits": _traits(),
    }
    values.update(updates)
    return IdentitySnapshotV1(**values)


def _raises(expected, function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except expected:
        return
    raise AssertionError(f"Se esperaba {expected.__name__}")


def test_01_static_factory_has_exact_name() -> None:
    assert build_static_self_v1().canonical_name == "Lazzaro"


def test_02_static_factory_has_exact_kind() -> None:
    assert build_static_self_v1().agent_kind == "digital_agent"


def test_03_static_factory_returns_immutable_values() -> None:
    _raises(FrozenInstanceError, setattr, build_static_self_v1(), "canonical_name", "X")


def test_04_state_canonicalizes_ints_to_float() -> None:
    assert all(type(getattr(_state(), field.name)) is float for field in fields(SelfStateSnapshotV1))


def test_05_traits_canonicalize_ints_to_float() -> None:
    value = PersonalitySnapshotV1(0, 0, 0, 0, 0)
    assert all(type(getattr(value, field.name)) is float for field in fields(value))


def test_06_state_accepts_closed_lower_boundary() -> None:
    assert _state(energia=0).energia == 0.0


def test_07_state_accepts_closed_upper_boundary() -> None:
    assert _state(foco=100).foco == 100.0


def test_08_traits_accept_closed_lower_boundary() -> None:
    assert _traits(resiliencia=-1).resiliencia == -1.0


def test_09_traits_accept_closed_upper_boundary() -> None:
    assert _traits(metodico=1).metodico == 1.0


def test_10_snapshot_schema_is_one() -> None:
    assert _snapshot().schema_version == 1


def test_11_snapshot_generation_is_positive() -> None:
    assert _snapshot(generation=7).generation == 7


def test_12_snapshot_rejects_wrong_static_type() -> None:
    _raises(TypeError, _snapshot, self=object())


def test_13_snapshot_rejects_wrong_state_type() -> None:
    _raises(TypeError, _snapshot, state={"energia": 80})


def test_14_snapshot_rejects_wrong_traits_type() -> None:
    _raises(TypeError, _snapshot, traits=(0, 0, 0, 0, 0))


def test_15_nested_contracts_are_dataclasses() -> None:
    assert all(is_dataclass(value) for value in (_snapshot(), _state(), _traits()))


def test_16_nested_contracts_use_slots() -> None:
    assert not any(hasattr(value, "__dict__") for value in (_snapshot(), _state(), _traits()))


def test_17_snapshot_contains_only_approved_state_fields() -> None:
    assert {field.name for field in fields(SelfStateSnapshotV1)} == {"energia", "estres", "foco"}


def test_18_snapshot_contains_only_five_traits() -> None:
    assert {field.name for field in fields(PersonalitySnapshotV1)} == {
        "resiliencia", "impulsividad", "paciencia", "sociabilidad", "metodico"
    }


def test_19_snapshot_excludes_task() -> None:
    assert "task" not in {field.name for field in fields(IdentitySnapshotV1)}


def test_20_snapshot_excludes_memory() -> None:
    assert "memory" not in {field.name for field in fields(IdentitySnapshotV1)}


def test_21_snapshot_excludes_perception() -> None:
    assert "perception" not in {field.name for field in fields(IdentitySnapshotV1)}


def test_22_snapshot_excludes_timestamp_and_uuid() -> None:
    names = {field.name for field in fields(IdentitySnapshotV1)}
    assert not names.intersection({"captured_at", "timestamp", "uuid", "trace_id"})


def test_23_behavior_capture_returns_copies() -> None:
    behavior = BehaviorCore()
    state, traits = behavior.capture_identity_components()
    state["energia"] = 0
    traits["sociabilidad"] = 1
    fresh_state, fresh_traits = behavior.capture_identity_components()
    assert fresh_state["energia"] != 0 and fresh_traits["sociabilidad"] != 1


def test_24_behavior_capture_reads_three_state_values() -> None:
    state, _ = BehaviorCore().capture_identity_components()
    assert set(state) == {"energia", "estres", "foco"}


def test_25_behavior_capture_reads_five_traits() -> None:
    _, traits = BehaviorCore().capture_identity_components()
    assert set(traits) == {"resiliencia", "impulsividad", "paciencia", "sociabilidad", "metodico"}


def test_26_provider_rejects_non_static_self() -> None:
    _raises(TypeError, IdentitySnapshotProvider, object(), BehaviorCore())


def test_27_provider_rejects_non_owner() -> None:
    _raises(TypeError, IdentitySnapshotProvider, build_static_self_v1(), object())


def test_28_provider_first_capture_is_generation_one() -> None:
    provider = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore())
    assert provider.capture().generation == 1


def test_29_provider_preserves_static_value() -> None:
    static = build_static_self_v1()
    provider = IdentitySnapshotProvider(static, BehaviorCore())
    assert provider.capture().self is static


def test_30_failed_capture_does_not_consume_generation() -> None:
    class Owner:
        calls = 0

        def capture_identity_components(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic")
            return ({"energia": 80, "estres": 10, "foco": 70}, {name: 0 for name in ("resiliencia", "impulsividad", "paciencia", "sociabilidad", "metodico")})

    provider = IdentitySnapshotProvider(build_static_self_v1(), Owner())
    _raises(RuntimeError, provider.capture)
    assert provider.capture().generation == 1


def test_31_failed_validation_does_not_consume_generation() -> None:
    class Owner:
        calls = 0

        def capture_identity_components(self):
            self.calls += 1
            energy = math.nan if self.calls == 1 else 80
            return ({"energia": energy, "estres": 10, "foco": 70}, {name: 0 for name in ("resiliencia", "impulsividad", "paciencia", "sociabilidad", "metodico")})

    provider = IdentitySnapshotProvider(build_static_self_v1(), Owner())
    _raises(ValueError, provider.capture)
    assert provider.capture().generation == 1


def test_32_provider_serializes_component_capture() -> None:
    active = 0
    maximum = 0
    calls = 0
    guard = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()

    class Owner:
        def capture_identity_components(self):
            nonlocal active, maximum, calls
            with guard:
                calls += 1
                call = calls
                active += 1
                maximum = max(maximum, active)
            if call == 1:
                first_entered.set()
                assert release_first.wait(timeout=2)
            with guard:
                active -= 1
            return ({"energia": 80, "estres": 10, "foco": 70}, {name: 0 for name in ("resiliencia", "impulsividad", "paciencia", "sociabilidad", "metodico")})

    provider = IdentitySnapshotProvider(build_static_self_v1(), Owner())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provider.capture)
        assert first_entered.wait(timeout=2)
        second = pool.submit(provider.capture)
        assert maximum == 1
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert maximum == 1


def test_33_provider_dependency_is_one_way() -> None:
    source = Path(__file__).with_name("behavior_core.py").read_text(encoding="utf-8")
    assert "IdentitySnapshotProvider" not in source


def test_34_identity_adapter_requires_owner() -> None:
    _raises(TypeError, IdentityState, None)


def test_35_identity_adapter_reads_owner_state() -> None:
    behavior = BehaviorCore()
    adapter = IdentityState(behavior)
    behavior.state_engine.establecer_estado_biologico(energia=42)
    assert adapter.energia == 42


def test_36_identity_adapter_delegates_mutation_to_owner() -> None:
    behavior = BehaviorCore()
    calls = []
    behavior.registrar_impacto_identidad = lambda **kwargs: calls.append(kwargs)
    IdentityState(behavior).registrar_evento(route="persona", success=True)
    assert calls == [{"route": "persona", "success": True, "es_descanso": False, "intensidad": 1.0}]


def test_37_identity_adapter_has_no_local_reserve_fields() -> None:
    adapter = IdentityState(BehaviorCore())
    assert not any(hasattr(adapter, name) for name in ("_energia", "_estres", "_enfoque"))


def test_38_productive_mutation_changes_single_owner_state() -> None:
    behavior = BehaviorCore()
    before = behavior.state_engine.obtener_estado_biologico(proyectar_descanso=False)
    behavior.registrar_impacto_identidad(route="motor", success=True)
    after = behavior.state_engine.obtener_estado_biologico(proyectar_descanso=False)
    assert after["energia"] == before["energia"] - 5


def test_39_productive_trait_mutation_is_visible_next_capture() -> None:
    behavior = BehaviorCore()
    before = behavior.capture_identity_components()[1]
    behavior.procesar_evento("interaccion_social", {"intensidad": 2.0})
    after = behavior.capture_identity_components()[1]
    assert after["sociabilidad"] > before["sociabilidad"]


def test_40_capture_waits_for_behavior_owner_lock() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    started = threading.Event()
    completed = threading.Event()
    with behavior._owner_lock:
        worker = threading.Thread(target=lambda: (started.set(), provider.capture(), completed.set()))
        worker.start()
        assert started.wait(timeout=2)
        assert not completed.is_set()
    worker.join(timeout=2)
    assert completed.is_set()


def test_41_transversal_capture_observes_coherent_supported_write() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    started = threading.Event()
    result = []
    with behavior._owner_lock:
        behavior.state_engine.establecer_estado_biologico(energia=33)
        behavior.personality.sociabilidad = 0.75
        worker = threading.Thread(
            target=lambda: (started.set(), result.append(provider.capture()))
        )
        worker.start()
        assert started.wait(timeout=2)
    worker.join(timeout=2)
    snap = result[0]
    assert snap.state.energia == 33 and snap.traits.sociabilidad == 0.75


def test_42_restart_restores_runtime_state_defaults() -> None:
    first = BehaviorCore()
    first.state_engine.establecer_estado_biologico(energia=12)
    second = BehaviorCore()
    assert second.state_engine.obtener_estado_biologico(proyectar_descanso=False)["energia"] == 80


def test_43_restart_restores_trait_defaults() -> None:
    first = BehaviorCore()
    first.personality.sociabilidad = 0.8
    assert BehaviorCore().personality.sociabilidad == 0


def test_44_restart_restores_task_to_none() -> None:
    first = BehaviorCore()
    first.iniciar_tarea("x")
    assert BehaviorCore().task_context is None


def test_45_static_values_survive_runtime_reconstruction() -> None:
    assert build_static_self_v1() == build_static_self_v1()


def test_46_new_provider_restarts_generation() -> None:
    assert IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore()).capture().generation == 1


def test_47_no_identity_persistence_dependency() -> None:
    tree = ast.parse(Path(__file__).with_name("identity_snapshot.py").read_text(encoding="utf-8"))
    imports = {node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)}
    assert not imports.intersection({"sqlite3", "chromadb"})


def test_48_snapshot_does_not_expose_owner_objects() -> None:
    snap = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore()).capture()
    assert not any(isinstance(value, BehaviorCore) for value in (snap.self, snap.state, snap.traits))
    main_source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "IdentitySnapshotProvider(static_self, behavior_core)" in main_source
    assert "identity_provider=identity_provider" in main_source


def test_95_identity_components_are_typed_frozen_values() -> None:
    components = IdentityComponentsV1(state=_state(), traits=_traits())
    assert is_dataclass(components) and not hasattr(components, "__dict__")
    _raises(FrozenInstanceError, setattr, components, "state", _state(energia=1))


def test_96_provider_from_components_never_reads_owner() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    behavior.capture_identity_components = lambda: (_ for _ in ()).throw(
        AssertionError("from_components no debe releer el owner")
    )
    snapshot = provider.from_components(
        IdentityComponentsV1(state=_state(energia=31), traits=_traits(metodico=0.61))
    )
    assert snapshot.generation == 1
    assert snapshot.state.energia == 31 and snapshot.traits.metodico == 0.61


def test_97_invalid_components_do_not_consume_generation() -> None:
    provider = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore())
    _raises(TypeError, provider.from_components, object())
    assert provider.capture().generation == 1


def test_98_behavior_result_correlates_components_with_context() -> None:
    result = BehaviorCore().procesar_evento(
        "comando_usuario",
        {"mensaje": "captura correlacionada"},
    )
    components = result.identity_components
    raw_state = result["contexto_llm"]["valores_crudos"]
    traits = result["contexto_llm"]["personalidad"]
    assert isinstance(result, dict)
    assert components.state.energia == raw_state["energia"]
    assert components.state.estres == raw_state["estres"]
    assert components.state.foco == raw_state["foco"]
    assert components.traits.sociabilidad == traits["sociabilidad"]
    assert components.traits.metodico == traits["metodico"]


def test_99_interleaving_preserves_same_turn_identity() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    turn_a_captured = threading.Event()
    turn_b_done = threading.Event()
    observed: dict[str, object] = {}
    errors: list[BaseException] = []

    def turn_a() -> None:
        try:
            result = behavior.procesar_evento(
                "comando_usuario",
                {"mensaje": "turno A", "intensidad": 1.0},
            )
            observed["result"] = result
            turn_a_captured.set()
            assert turn_b_done.wait(timeout=2)
            observed["snapshot"] = provider.from_components(result.identity_components)
        except BaseException as exc:
            errors.append(exc)

    def turn_b() -> None:
        try:
            assert turn_a_captured.wait(timeout=2)
            behavior.procesar_evento("interaccion_social", {"intensidad": 2.0})
            behavior.registrar_impacto_identidad(route="motor", success=True)
            turn_b_done.set()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=turn_a), threading.Thread(target=turn_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    if errors:
        raise errors[0]

    result = observed["result"]
    snapshot = observed["snapshot"]
    context = result["contexto_llm"]
    assert snapshot.state.energia == context["valores_crudos"]["energia"]
    assert snapshot.traits.sociabilidad == context["personalidad"]["sociabilidad"]
    fresh = provider.capture()
    assert fresh.state.energia != snapshot.state.energia
    assert fresh.traits.sociabilidad != snapshot.traits.sociabilidad
