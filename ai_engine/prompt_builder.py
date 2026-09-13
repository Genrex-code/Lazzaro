"""Typed Reasoning PromptPlan builder.

Legacy text templates remain repository history/fixtures only.  This productive
builder has a single authority: PromptPlanV1.
"""

from __future__ import annotations

from ai_engine.prompt_plan import (
    BehaviorPolicyV1,
    ContextBlockV1,
    ContextDataSourceV1,
    ContextEnvelopeV1,
    PromptPlanV1,
    RequestInstructionV1,
    ToolContractV1,
    build_reasoning_system_instructions,
)
from ai_engine.schemas import AgentContext
from core.logger import get_logger
from tools.registry import ToolRegistry


logger = get_logger(__name__)


class PromptBuilder:
    """Build a typed Reasoning plan without promoting runtime data."""

    def build(
        self,
        context: AgentContext,
        *,
        current_request: str | None = None,
    ) -> PromptPlanV1:
        blocks: list[ContextBlockV1] = []
        if context.identidad is not None:
            blocks.append(ContextBlockV1(ContextDataSourceV1.IDENTITY, context.identidad))
        elif context.personalidad:
            blocks.append(
                ContextBlockV1(
                    ContextDataSourceV1.IDENTITY,
                    {"legacy_traits": context.personalidad},
                )
            )

        operational = {
            "narrative": context.estado_narrativo,
            "values": context.valores_estado,
            "flags": context.banderas,
        }
        if any(operational.values()):
            blocks.append(
                ContextBlockV1(ContextDataSourceV1.OPERATIONAL_STATE, operational)
            )
        if context.percepcion_texto:
            blocks.append(
                ContextBlockV1(
                    ContextDataSourceV1.PERCEPTION,
                    {"summary": context.percepcion_texto},
                )
            )
        if context.constelacion_resumen:
            blocks.append(
                ContextBlockV1(
                    ContextDataSourceV1.CONSTELLATION,
                    {"summary": context.constelacion_resumen},
                )
            )
        if context.tarea:
            blocks.append(ContextBlockV1(ContextDataSourceV1.TASK, context.tarea))
        if context.memoria_texto:
            blocks.append(
                ContextBlockV1(
                    ContextDataSourceV1.MEMORY_AGGREGATE,
                    {"summary": context.memoria_texto},
                )
            )

        try:
            policy = BehaviorPolicyV1(context.politica)
        except ValueError:
            policy = BehaviorPolicyV1.EXPLORADORA
        tool_contract = ToolContractV1.from_registry(ToolRegistry, context.herramientas)
        request = (
            RequestInstructionV1.current_user(current_request)
            if isinstance(current_request, str)
            else None
        )
        plan = PromptPlanV1(
            system=build_reasoning_system_instructions(policy, tool_contract),
            context=ContextEnvelopeV1(blocks),
            request=request,
        )
        logger.debug(
            "PromptPlan reasoning construido blocks=%d request=%s.",
            len(plan.context.blocks),
            plan.request is not None,
        )
        return plan
