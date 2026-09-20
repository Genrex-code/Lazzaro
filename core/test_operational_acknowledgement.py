"""ROOT-017 review: manual save is a typed Memory/Core acknowledgement."""

from __future__ import annotations

import inspect

import pytest

from ai_engine.decision_outcome import DecisionLayer
from api.schemas import EventoEntrante
from core.operational_acknowledgement import manual_conversation_save_acknowledgement
from core.orchestrator import Orchestrator
from memory.memory_manager import ManualConversationSaveResult


@pytest.mark.parametrize("status,expected", [
    ("saved", "Está bien, lo recordaré para después."),
    ("already_saved", "Eso ya estaba guardado en mi mente; no hace falta duplicarlo."),
    ("empty", "Todavía no tengo una conversación reciente en RAM que pueda guardar."),
    ("error", "No pude guardar lo que hablamos esta vez. Prefiero decírtelo antes que fingir que lo recordé."),
])
def test_manual_ack_is_code_owned_and_receipt_driven(status, expected):
    receipt = ManualConversationSaveResult(
        status=status,
        turn_count=7,
        memory_id="MEMORY_PROSE_CANARY",
        fingerprint="USER_PROSE_CANARY",
        error="ORACLE_PROSE_CANARY",
    )
    result = manual_conversation_save_acknowledgement(receipt)
    text = result.parametros["texto_a_comunicar"]
    assert text == expected
    assert all(token not in text for token in (
        "MEMORY_PROSE_CANARY", "USER_PROSE_CANARY", "ORACLE_PROSE_CANARY", "7",
    ))
    assert result.outcome.layer is DecisionLayer.CORE
    assert result.metadata["operational_acknowledgement"] == "memory_core"


def test_manual_ack_does_not_call_persona_model_or_parser():
    receipt = ManualConversationSaveResult(status="saved", turn_count=3)

    class Memory:
        def guardar_fotografia_conversacional(self, *, id_transaccion):
            assert id_transaccion == "manual-ack-fixture"
            return receipt

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError(f"manual acknowledgement reached Persona/LLM: {name}")

    orchestrator = object.__new__(Orchestrator)
    orchestrator.memory = Memory()
    orchestrator.persona_engine = Forbidden()
    orchestrator.llm_adapter = Forbidden()
    event = EventoEntrante(
        origen="terminal", tipo_evento="comando_usuario",
        id_transaccion="manual-ack-fixture", detalles={"mensaje": "USER_PROSE_CANARY"},
    )
    result = orchestrator._resolver_guardado_conversacional(event)
    assert result.parametros["texto_a_comunicar"] == "Está bien, lo recordaré para después."
    assert result.outcome.layer is DecisionLayer.CORE


def test_productive_persona_routes_require_boundary_before_public_adapter():
    for method in (
        Orchestrator._resolver_ruta_persona,
        Orchestrator._verbalizar_resultado_oracle,
    ):
        source = inspect.getsource(method)
        assert "bridge.prepare(" in source
        assert "bridge.finish(" in source
        assert source.index("bridge.finish(") < source.index("_action_response_desde_persona(")
        assert "persona_engine.parsear_resultado(" not in source
        assert "persona_engine.proyectar_resultado(" not in source
    save_source = inspect.getsource(Orchestrator._resolver_guardado_conversacional)
    assert "manual_conversation_save_acknowledgement(resultado)" in save_source
    assert "persona_engine" not in save_source
