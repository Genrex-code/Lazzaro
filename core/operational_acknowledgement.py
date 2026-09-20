"""Code-owned operational acknowledgements, not Persona model output."""

from __future__ import annotations

from ai_engine.decision_outcome import (
    DecisionAttempt,
    DecisionDisposition,
    DecisionLayer,
    DecisionOutcome,
    DecisionQuality,
    DecisionReasonCode,
)
from ai_engine.schemas import ActionResponse
from memory.memory_manager import ManualConversationSaveResult


_MANUAL_SAVE_TEXT = {
    "saved": "Está bien, lo recordaré para después.",
    "already_saved": "Eso ya estaba guardado en mi mente; no hace falta duplicarlo.",
    "empty": "Todavía no tengo una conversación reciente en RAM que pueda guardar.",
    "error": (
        "No pude guardar lo que hablamos esta vez. "
        "Prefiero decírtelo antes que fingir que lo recordé."
    ),
}


def manual_conversation_save_acknowledgement(
    receipt: ManualConversationSaveResult,
) -> ActionResponse:
    """Render only a typed save status; never interpolate receipt prose publicly."""
    if not isinstance(receipt, ManualConversationSaveResult):
        raise TypeError("manual save acknowledgement requires a typed receipt")
    status = receipt.status if receipt.status in _MANUAL_SAVE_TEXT else "error"
    failure = status == "error"
    metadata = {
        "manual_memory_save_control": True,
        "manual_memory_save_status": status,
        "manual_memory_save_turn_count": receipt.turn_count,
        "operational_acknowledgement": "memory_core",
    }
    if receipt.fingerprint:
        metadata["manual_memory_save_fingerprint"] = receipt.fingerprint
    if receipt.memory_id:
        metadata["manual_memory_save_id"] = receipt.memory_id
    return ActionResponse(
        pensamiento="",
        accion="hablar",
        parametros={"texto_a_comunicar": _MANUAL_SAVE_TEXT[status]},
        razon="Acuse operativo de guardado conversacional manual.",
        valido=True,
        error=receipt.error if failure else None,
        metadata=metadata,
        outcome=DecisionOutcome(
            disposition=(DecisionDisposition.ERROR if failure else DecisionDisposition.ACTION),
            quality=(DecisionQuality.FAILED if failure else DecisionQuality.VALIDATED),
            attempt=DecisionAttempt.FIRST,
            layer=DecisionLayer.CORE,
            reason_codes=((DecisionReasonCode.PIPELINE_ERROR,) if failure else (DecisionReasonCode.PRIMARY_VALID,)),
            retry_count=0,
        ),
    )
