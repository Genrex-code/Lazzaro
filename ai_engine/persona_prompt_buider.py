"""Typed PromptPlan builder for Persona (Capa 2)."""

from __future__ import annotations

from typing import Any

from ai_engine.identity_projection import IdentityContextV1
from ai_engine.prompt_plan import (
    ChannelPolicyV1,
    ContextBlockV1,
    ContextDataSourceV1,
    ContextEnvelopeV1,
    PromptPlanV1,
    PromptPurposeV1,
    RequestInstructionV1,
    build_persona_system_instructions,
)
from ai_engine.schemas import OracleResult
from core.logger import get_logger


logger = get_logger(__name__)


class PersonaPromptBuilder:
    """Build Persona plans without promoting runtime context to instructions."""

    @staticmethod
    def build_plan(
        *,
        mensaje_usuario: str | None,
        identity_context: IdentityContextV1,
        percepcion: dict[str, Any] | None = None,
        recuerdos: str = "",
        terminal_channel: bool = False,
        oracle_result: OracleResult | None = None,
    ) -> PromptPlanV1:
        if not isinstance(identity_context, IdentityContextV1):
            raise TypeError("identity_context debe ser IdentityContextV1")

        purpose = (
            PromptPurposeV1.PERSONA_ORACLE_VERBALIZATION
            if oracle_result is not None
            else PromptPurposeV1.PERSONA_SOCIAL
        )
        blocks = [ContextBlockV1(ContextDataSourceV1.IDENTITY, identity_context)]
        if percepcion:
            blocks.append(ContextBlockV1(ContextDataSourceV1.PERCEPTION, percepcion))
        if recuerdos:
            blocks.append(
                ContextBlockV1(
                    ContextDataSourceV1.MEMORY_AGGREGATE,
                    {"summary": recuerdos},
                )
            )
        if oracle_result is not None:
            blocks.append(
                ContextBlockV1(ContextDataSourceV1.ORACLE_RESULT, oracle_result)
            )

        request = None
        if isinstance(mensaje_usuario, str):
            request = RequestInstructionV1.current_user(mensaje_usuario)

        plan = PromptPlanV1(
            system=build_persona_system_instructions(
                purpose,
                ChannelPolicyV1.TERMINAL if terminal_channel else ChannelPolicyV1.DEFAULT,
            ),
            context=ContextEnvelopeV1(blocks),
            request=request,
        )
        logger.debug(
            "PromptPlan Persona construido purpose=%s blocks=%d request=%s.",
            purpose.value,
            len(plan.context.blocks),
            plan.request is not None,
        )
        return plan
