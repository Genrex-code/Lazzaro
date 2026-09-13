"""Provider projections for :mod:`ai_engine.prompt_plan`.

This is the only boundary that assigns provider-native roles.  Context JSON is
kept opaque and is never recursively interpreted as a message list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_engine.prompt_plan import PromptPlanV1, PromptProjectionUnsupported


ProviderRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ProviderMessageV1:
    role: ProviderRole
    content: str


def project_chat_messages(plan: PromptPlanV1) -> tuple[ProviderMessageV1, ...]:
    """Project a plan into fixed-role chat messages without parsing its payload."""
    if not isinstance(plan, PromptPlanV1):
        raise PromptProjectionUnsupported("provider projection requires PromptPlanV1")
    messages: list[ProviderMessageV1] = [
        ProviderMessageV1(role="system", content=plan.system.text),
        ProviderMessageV1(role="user", content=plan.user_envelope_json()),
    ]
    if plan.assistant_evidence is not None:
        messages.append(
            ProviderMessageV1(role="assistant", content=plan.assistant_evidence.text)
        )
        messages.append(
            ProviderMessageV1(role="user", content=plan.repair_request.text)
        )
    return tuple(messages)


def project_openai_messages(plan: PromptPlanV1) -> list[dict[str, str]]:
    return [
        {"role": message.role, "content": message.content}
        for message in project_chat_messages(plan)
    ]


def project_gemini_parts(plan: PromptPlanV1) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Return Gemini system instruction plus fixed user/model content tuples."""
    projected = project_chat_messages(plan)
    system = projected[0].content
    contents: list[tuple[str, str]] = []
    for message in projected[1:]:
        role = "model" if message.role == "assistant" else "user"
        contents.append((role, message.content))
    return system, tuple(contents)
