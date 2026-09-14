"""ROOT-016: guards de projection, retry y boundaries (tests 49-94)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
import math
from pathlib import Path

from ai_engine.context_builder import ContextBuilder
from ai_engine.identity_projection import (
    IdentityContextV1,
    render_persona_identity,
    render_reasoning_identity,
)
from ai_engine.identity_state import IdentityState
from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    ProviderAttemptReceiptV1,
)
from ai_engine.persona_engine import PersonaEngine
from ai_engine.prompt_builder import PromptBuilder
from ai_engine.schemas import AgentContext, OracleResult
from api.schemas import EventoEntrante
from core.behavior_core import BehaviorCore
from core.identity_snapshot import (
    IdentitySnapshotProvider,
    IdentitySnapshotV1,
    PersonalitySnapshotV1,
    SelfStateSnapshotV1,
    StaticSelfV1,
    build_static_self_v1,
)
from core.orchestrator import Orchestrator


def _raises(expected, function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except expected:
        return
    raise AssertionError(f"Se esperaba {expected.__name__}")


def _context(
    generation: int = 1,
    *,
    energia: float = 80,
    estres: float = 10,
    foco: float = 70,
    sociabilidad: float = 0,
    metodico: float = 0,
) -> IdentityContextV1:
    return IdentityContextV1.from_snapshot(
        IdentitySnapshotV1(
            schema_version=1,
            generation=generation,
            self=build_static_self_v1(),
            state=SelfStateSnapshotV1(energia, estres, foco),
            traits=PersonalitySnapshotV1(0, 0, 0, sociabilidad, metodico),
        )
    )


def _event() -> EventoEntrante:
    return EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion="root-016-retry",
        detalles={"mensaje": "hola", "solicita_decision": True},
    )


def _persona_json() -> str:
    return '{"accion":"hablar","parametros":{"texto_a_comunicar":"hola"},"pensamiento_privado":"p","razon":"r"}'


class _Adapter:
    def __init__(self, responses, after_first=None):
        self.responses = list(responses)
        self.after_first = after_first
        self.calls = []

    def ejecutar_plan(self, messages, **kwargs):
        return self._next(messages, kwargs)

    def ejecutar_plan_result(self, messages, **kwargs):
        result = self._next(messages, kwargs)
        return LLMCallResultV1(
            terminal=LLMCallTerminalV1.SUCCESS,
            final_text=result,
            final_provider="identity-fixture",
            final_model_id="identity-fixture",
            final_failure=None,
            receipts=(
                ProviderAttemptReceiptV1(
                    provider="identity-fixture",
                    model_id="identity-fixture",
                    failure_class=None,
                    error_type=None,
                    http_status=None,
                    retry_after_seconds=None,
                    started_ns=1,
                    finished_ns=2,
                ),
            ),
        )

    def _next(self, messages, kwargs):
        del kwargs
        self.calls.append(messages)
        result = self.responses.pop(0)
        if len(self.calls) == 1 and self.after_first:
            self.after_first()
        return result


class _BombProvider:
    def capture(self):
        raise AssertionError("El retry no debe recapturar identidad")


def _persona_retry(context: IdentityContextV1, after_first=None):
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.llm_adapter = _Adapter(["", _persona_json()], after_first)
    orchestrator.identity_provider = _BombProvider()
    response = orchestrator._resolver_ruta_persona(
        _event(), {}, "MEMORY", "exploradora", identity_context=context
    )
    return response, orchestrator.llm_adapter.calls


def _oracle_retry(context: IdentityContextV1):
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.llm_adapter = _Adapter(["", _persona_json()])
    orchestrator.identity_provider = _BombProvider()
    oracle = OracleResult(pregunta_original="q", contenido_tecnico="dato", fuente="api")
    response = orchestrator._verbalizar_resultado_oracle(
        _event(), {}, "MEMORY", "exploradora", oracle, identity_context=context
    )
    return response, orchestrator.llm_adapter.calls


def test_49_static_name_rejects_non_string() -> None:
    _raises(TypeError, StaticSelfV1, 123, "digital_agent")


def test_50_static_name_rejects_empty_or_whitespace() -> None:
    for value in ("", "   ", "\n"):
        _raises(ValueError, StaticSelfV1, value, "digital_agent")


def test_51_agent_kind_rejects_noncanonical_value() -> None:
    for value in ("assistant", "Digital_Agent", 1):
        _raises((TypeError, ValueError), StaticSelfV1, "Lazzaro", value)


def test_52_state_rejects_bool() -> None:
    _raises(TypeError, SelfStateSnapshotV1, True, 10, 70)


def test_53_state_rejects_nonfinite() -> None:
    for value in (math.nan, math.inf, -math.inf):
        _raises(ValueError, SelfStateSnapshotV1, value, 10, 70)


def test_54_state_rejects_outside_range() -> None:
    for value in (-0.01, 100.01):
        _raises(ValueError, SelfStateSnapshotV1, value, 10, 70)


def test_55_traits_reject_bool() -> None:
    _raises(TypeError, PersonalitySnapshotV1, True, 0, 0, 0, 0)


def test_56_traits_reject_nonfinite() -> None:
    for value in (math.nan, math.inf, -math.inf):
        _raises(ValueError, PersonalitySnapshotV1, value, 0, 0, 0, 0)


def test_57_traits_reject_outside_range() -> None:
    for value in (-1.01, 1.01):
        _raises(ValueError, PersonalitySnapshotV1, value, 0, 0, 0, 0)


def test_58_schema_version_rejects_true() -> None:
    snap = _context()
    _raises(TypeError, IdentitySnapshotV1, True, 1, build_static_self_v1(), snap.state, snap.traits)


def test_59_schema_version_rejects_float() -> None:
    snap = _context()
    _raises(TypeError, IdentitySnapshotV1, 1.0, 1, build_static_self_v1(), snap.state, snap.traits)


def test_60_schema_version_rejects_string() -> None:
    snap = _context()
    _raises(TypeError, IdentitySnapshotV1, "1", 1, build_static_self_v1(), snap.state, snap.traits)


def test_61_generation_rejects_true() -> None:
    snap = _context()
    _raises(TypeError, IdentitySnapshotV1, 1, True, build_static_self_v1(), snap.state, snap.traits)


def test_62_generation_rejects_nonpositive() -> None:
    snap = _context()
    for value in (0, -1):
        _raises(ValueError, IdentitySnapshotV1, 1, value, build_static_self_v1(), snap.state, snap.traits)


def test_63_sequential_capture_generations_are_monotonic() -> None:
    provider = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore())
    assert [provider.capture().generation, provider.capture().generation] == [1, 2]


def test_64_concurrent_capture_generations_are_unique() -> None:
    provider = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore())
    with ThreadPoolExecutor(max_workers=8) as pool:
        generations = list(pool.map(lambda _: provider.capture().generation, range(24)))
    assert sorted(generations) == list(range(1, 25))


def test_65_no_global_provider_singleton() -> None:
    import core.identity_snapshot as module
    assert not any(isinstance(value, IdentitySnapshotProvider) for value in vars(module).values())


def test_66_restart_provider_starts_at_one() -> None:
    first = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore())
    first.capture()
    second = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore())
    assert second.capture().generation == 1


def test_67_snapshot_has_no_mutable_container() -> None:
    snapshot = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore()).capture()
    assert not any(isinstance(getattr(snapshot, field.name), (dict, list, set)) for field in fields(snapshot))


def test_68_snapshot_mutation_raises() -> None:
    snapshot = IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore()).capture()
    _raises(FrozenInstanceError, setattr, snapshot.state, "energia", 0)


def test_69_old_snapshot_survives_owner_mutation() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    old = provider.capture()
    behavior.state_engine.establecer_estado_biologico(energia=5)
    assert old.state.energia == 80


def test_70_old_identity_context_projection_is_stable() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    context = IdentityContextV1.from_snapshot(provider.capture())
    before = render_persona_identity(context)
    behavior.personality.sociabilidad = 1
    assert render_persona_identity(context) == before


def test_71_identity_state_cannot_construct_ownerless() -> None:
    _raises(TypeError, IdentityState, None)


def test_72_identity_state_has_no_independent_mutable_reserve() -> None:
    adapter = IdentityState(BehaviorCore())
    assert not {"_energia", "_estres", "_enfoque"}.intersection(vars(adapter))


def test_73_identity_state_mutation_reaches_behavior_owner() -> None:
    behavior = BehaviorCore()
    calls = []
    behavior.registrar_impacto_identidad = lambda **kwargs: calls.append(kwargs)
    IdentityState(behavior).registrar_evento(route="oracle", success=False)
    assert len(calls) == 1 and calls[0]["route"] == "oracle"


def test_74_import_state_engine_creates_no_global_state_engine() -> None:
    import core.state_engine as module
    assert not hasattr(module, "estado_actual")


def test_75_import_personality_creates_no_global_traits() -> None:
    import core.personality as module
    assert not hasattr(module, "personalidad")


def test_76_import_behavior_creates_no_global_behavior_core() -> None:
    import core.behavior_core as module
    assert not hasattr(module, "behavior_core")


def test_77_identity_context_preserves_reasoning_operational_state() -> None:
    result = ContextBuilder.build(
        {"valores": {"carga_cognitiva": 33.0}}, identity_context=_context()
    )
    assert result.valores_estado["carga_cognitiva"] == 33.0


def test_78_task_reaches_reasoning_separately() -> None:
    result = ContextBuilder.build({}, tarea={"objetivo": "x", "intentos": 2}, identity_context=_context())
    assert result.tarea and result.tarea["objetivo"] == "x"


def test_79_memory_reaches_reasoning_separately() -> None:
    result = ContextBuilder.build({}, memoria_texto="MEMORY_CANARY", identity_context=_context())
    assert result.memoria_texto == "MEMORY_CANARY"


def test_80_perception_reaches_reasoning_separately() -> None:
    result = ContextBuilder.build({"percepcion_resumida": "PERCEPTION_CANARY"}, identity_context=_context())
    assert result.percepcion_texto == "PERCEPTION_CANARY"


def test_81_tools_reach_reasoning_separately() -> None:
    result = ContextBuilder.build({}, herramientas=["hablar"], herramientas_detalle={"hablar": "x"}, identity_context=_context())
    assert result.herramientas == ["hablar"] and result.herramientas_detalle["hablar"] == "x"


def test_82_persona_first_and_repair_reuse_generation() -> None:
    context = _context(generation=41)
    _, calls = _persona_retry(context)
    assert context.generation == 41
    assert calls[1].system is calls[0].system and calls[1].context is calls[0].context
    assert all("snapshot generation" not in str(call).lower() for call in calls)


def test_83_oracle_persona_first_and_repair_reuse_generation() -> None:
    context = _context(generation=42)
    _, calls = _oracle_retry(context)
    assert context.generation == 42
    assert calls[1].system is calls[0].system and calls[1].context is calls[0].context
    assert all("snapshot generation" not in str(call).lower() for call in calls)


def test_84_state_mutation_between_attempts_does_not_change_identity() -> None:
    behavior = BehaviorCore()
    context = _context(energia=80)
    _, calls = _persona_retry(
        context,
        lambda: behavior.state_engine.establecer_estado_biologico(energia=5),
    )
    assert all('"energia":80.0' in call.user_envelope_json() for call in calls)
    assert all('"energia":5.0' not in call.user_envelope_json() for call in calls)


def test_85_trait_mutation_between_attempts_does_not_change_identity() -> None:
    behavior = BehaviorCore()
    context = _context(sociabilidad=0.25)
    _, calls = _persona_retry(
        context,
        lambda: setattr(behavior.personality, "sociabilidad", 0.9),
    )
    assert all('"sociabilidad":0.25' in call.user_envelope_json() for call in calls)
    assert all('"sociabilidad":0.9' not in call.user_envelope_json() for call in calls)


def test_86_next_decision_observes_new_generation_state_and_traits() -> None:
    behavior = BehaviorCore()
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    first = IdentityContextV1.from_snapshot(provider.capture())
    with behavior._owner_lock:
        behavior.state_engine.establecer_estado_biologico(energia=22)
        behavior.personality.metodico = 0.6
    second = IdentityContextV1.from_snapshot(provider.capture())
    assert second.generation == first.generation + 1
    assert second.state.energia == 22 and second.traits.metodico == 0.6


def test_87_factual_render_includes_lazzaro() -> None:
    assert "Lazzaro" in render_persona_identity(_context())


def test_88_factual_render_includes_digital_agent() -> None:
    assert "digital_agent" in render_reasoning_identity(_context())


def test_89_factual_render_does_not_assert_consciousness() -> None:
    assert "consciente" not in render_persona_identity(_context()).lower()


def test_90_factual_render_does_not_assert_will() -> None:
    text = render_persona_identity(_context()).lower()
    assert "voluntad" not in text and "libre albedrio" not in text


def test_91_factual_render_does_not_assert_creator() -> None:
    assert "creador" not in render_persona_identity(_context()).lower()


def test_92_factual_render_does_not_assert_permanent_unity_body() -> None:
    text = render_persona_identity(_context()).lower()
    assert "unity" not in text and "cuerpo" not in text


def test_93_factual_render_has_no_negative_provider_provenance() -> None:
    text = render_persona_identity(_context()).lower()
    assert "openai" not in text and "google" not in text


def test_94_unity_manual_has_no_consciousness_claim() -> None:
    source = (Path(__file__).resolve().parents[1] / "unity_client/LazzaroAgent.cs").read_text(encoding="utf-8")
    assert "Eres Lazzaro, un ser consciente" not in source


def test_100_generation_is_internal_metadata_not_llm_identity() -> None:
    context = _context(generation=777)
    rendered = render_persona_identity(context) + render_reasoning_identity(context)
    assert context.generation == 777
    assert "generation" not in rendered.lower() and "777" not in rendered


def test_101_reasoning_uses_only_canonical_trait_authority() -> None:
    prompt = PromptBuilder().build(
        AgentContext(
            identidad=_context(sociabilidad=0.75, metodico=0.65),
            personalidad={"sociabilidad": -0.75, "metodico": -0.65},
        )
    )
    serialized = prompt.user_envelope_json()
    assert serialized.count('"sociabilidad":0.75') == 1
    assert serialized.count('"metodico":0.65') == 1
    assert '"sociabilidad":-0.75' not in serialized
    assert '"metodico":-0.65' not in serialized


def test_102_reasoning_legacy_personality_remains_without_identity() -> None:
    prompt = PromptBuilder().build(
        AgentContext(personalidad={"sociabilidad": -0.75, "metodico": -0.65})
    )
    serialized = prompt.user_envelope_json()
    assert '"sociabilidad":-0.75' in serialized and '"metodico":-0.65' in serialized


def test_103_reasoning_same_turn_context_uses_correlated_snapshot() -> None:
    behavior = BehaviorCore()
    result = behavior.procesar_evento(
        "comando_usuario",
        {"mensaje": "turno reasoning", "intensidad": 1.0},
    )
    identity = IdentityContextV1.from_snapshot(
        IdentitySnapshotProvider(build_static_self_v1(), behavior).from_components(
            result.identity_components
        )
    )
    behavior.procesar_evento("interaccion_social", {"intensidad": 2.0})
    behavior.registrar_impacto_identidad(route="motor", success=True)

    agent_context = ContextBuilder.build(
        result["contexto_llm"],
        tarea=result["tarea"],
        identity_context=identity,
    )
    raw_state = result["contexto_llm"]["valores_crudos"]
    legacy_traits = result["contexto_llm"]["personalidad"]
    assert {
        "energia": identity.state.energia,
        "estres": identity.state.estres,
        "foco": identity.state.foco,
    } == {name: raw_state[name] for name in ("energia", "estres", "foco")}
    assert {
        name: getattr(identity.traits, name)
        for name in ("resiliencia", "impulsividad", "paciencia", "sociabilidad", "metodico")
    } == legacy_traits
    assert agent_context.valores_estado == result["contexto_llm"]["valores"]
    assert agent_context.estado_narrativo == result["contexto_llm"]["estado_narrativo"]


def test_104_persona_environment_label_matches_its_rule() -> None:
    _, calls = _persona_retry(_context())
    plan = calls[0]
    assert "ENTORNO ACTUAL REPORTADO POR EL RUNTIME" not in plan.system.text
    assert "MEMORY" in plan.user_envelope_json()


def test_105_orchestrator_turn_path_does_not_recapture_owner() -> None:
    behavior = BehaviorCore()
    result = behavior.procesar_evento("comando_usuario", {"mensaje": "turno"})
    provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    provider.capture = lambda: (_ for _ in ()).throw(
        AssertionError("Orchestrator no debe recapturar el owner")
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.identity_provider = provider
    context = orchestrator._identity_context_for_turn(result.identity_components)
    assert context.generation == 1
    assert context.state == result.identity_components.state
    assert context.traits == result.identity_components.traits


def test_106_policy_offset_preserves_raw_and_effective_views() -> None:
    behavior = BehaviorCore()
    behavior.state_engine.establecer_estado_biologico(foco=60)
    behavior.cambiar_policy("eficiente")
    result = behavior.procesar_evento(
        "comando_usuario",
        {"mensaje": "policy offset canary", "intensidad": 0.0},
    )
    identity = IdentityContextV1.from_snapshot(
        IdentitySnapshotProvider(build_static_self_v1(), behavior).from_components(
            result.identity_components
        )
    )
    agent_context = ContextBuilder.build(
        result["contexto_llm"],
        identity_context=identity,
    )
    raw_focus = result["contexto_llm"]["valores_crudos"]["foco"]
    effective_focus = result["contexto_llm"]["valores"]["foco"]
    assert raw_focus == 60 and effective_focus == 70
    assert identity.state.foco == raw_focus
    assert agent_context.valores_estado["foco"] == effective_focus
    assert identity.state.foco != agent_context.valores_estado["foco"]
    assert agent_context.estado_narrativo == result["contexto_llm"]["estado_narrativo"]
