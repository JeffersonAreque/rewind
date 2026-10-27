"""Shared helpers for talking to the model provider.

The provider comes from .env (see rewind/config.py) and speaks the OpenAI
chat API. Muse Spark Open always reasons before answering; split_reasoning
keeps that reasoning apart from the final answer.
"""

import re
from dataclasses import dataclass

from openai import OpenAI

from rewind.config import ProviderConfig

THINK = re.compile(r"^\s*(?:<think>)?(.*?)</think>\s*", re.DOTALL)


def make_client(config: ProviderConfig) -> OpenAI:
    return OpenAI(base_url=config.base_url, api_key=config.api_key)


def split_reasoning(message) -> tuple[str, str]:
    """Return (reasoning, answer) from a chat message.

    Providers return the reasoning either in a separate field
    (reasoning_content / reasoning) or inline inside <think>...</think>.
    """
    extra = message.model_extra or {}
    reasoning = extra.get("reasoning_content") or extra.get("reasoning") or ""
    answer = message.content or ""
    match = THINK.match(answer)
    if match:
        reasoning = reasoning or match.group(1)
        answer = answer[match.end():]
    return reasoning.strip(), answer.strip()


@dataclass
class TokenCount:
    """Running total of tokens spent, reasoning included in completion."""

    prompt: int = 0
    completion: int = 0
    reasoning: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def add(self, usage) -> None:
        if usage is None:
            return
        details = getattr(usage, "completion_tokens_details", None)
        self.prompt += usage.prompt_tokens or 0
        self.completion += usage.completion_tokens or 0
        self.reasoning += getattr(details, "reasoning_tokens", None) or 0
        self.calls += 1

    def merge(self, other: "TokenCount") -> None:
        self.prompt += other.prompt
        self.completion += other.completion
        self.reasoning += other.reasoning
        self.calls += other.calls

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt": self.prompt,
            "completion": self.completion,
            "reasoning": self.reasoning,
            "total": self.total,
        }
