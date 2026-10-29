"""Shared helpers for talking to the model provider.

The provider comes from .env (see rewind/config.py) and speaks the OpenAI
chat API. Muse Spark Open always reasons before answering; split_reasoning
keeps that reasoning apart from the final answer.

ask_json is how every agent asks for structured output: JSON validated with
pydantic, retried with the error sent back, and cached on disk by `cached`.
"""

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, ValidationError

from rewind.config import ProviderConfig

THINK = re.compile(r"^\s*(?:<think>)?(.*?)</think>\s*", re.DOTALL)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
RETRIES = 2
MAX_TOKENS = 24000  # reasoning included: a big spec can think for more than 12k tokens before its JSON
CUT_OFF = ("Your answer was cut off by the token limit before the JSON object. Reason more briefly, "
           "then answer with only the JSON object.")

T = TypeVar("T", bound=BaseModel)


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

    @classmethod
    def from_dict(cls, data: dict) -> "TokenCount":
        return cls(**{key: data[key] for key in ("prompt", "completion", "reasoning", "calls")})


# ---------- Structured answers ----------


def extract_json(answer: str) -> str:
    """Take the JSON object out of an answer that may wrap it in ``` or prose."""
    answer = FENCE.sub("", answer.strip())
    start, end = answer.find("{"), answer.rfind("}")
    return answer[start:end + 1] if start != -1 and end > start else answer


def image_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


_schema_supported = True


def complete(client: OpenAI, model: str, messages: list[dict], schema: type[BaseModel]):
    """Chat call that asks for schema-constrained JSON when the provider allows it.

    Baseten accepts response_format; a provider that rejects it falls back to
    the schema written in the prompt, which pydantic still validates.
    """
    global _schema_supported
    if _schema_supported:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        try:
            return client.chat.completions.create(
                model=model, messages=messages, max_tokens=MAX_TOKENS, response_format=response_format
            )
        except BadRequestError as exc:
            if "response_format" not in str(exc) and "json_schema" not in str(exc):
                raise
            _schema_supported = False
    return client.chat.completions.create(model=model, messages=messages, max_tokens=MAX_TOKENS)


def ask_json(
    client: OpenAI,
    model: str,
    content: list[dict],
    schema: type[T],
    tokens: TokenCount,
    check: Callable[[T], str | None] | None = None,
) -> tuple[T, str]:
    """Ask for one JSON object matching `schema`. Returns it and the model's reasoning.

    `check` can reject a valid object with a reason (for example, a step
    number that does not exist). A malformed or rejected answer is sent back
    with the error, up to RETRIES times; then the error is raised.
    """
    messages: list[dict] = [{"role": "user", "content": content}]
    for attempt in range(RETRIES + 1):
        response = complete(client, model, messages, schema)
        tokens.add(response.usage)
        reasoning, answer = split_reasoning(response.choices[0].message)
        try:
            result = schema.model_validate_json(extract_json(answer))
        except ValidationError as exc:
            if attempt == RETRIES:
                raise
            cut_off = getattr(response.choices[0], "finish_reason", None) == "length" and "{" not in answer
            problem = CUT_OFF if cut_off else str(exc)
        else:
            problem = check(result) if check else None
            if problem is None:
                return result, reasoning
            if attempt == RETRIES:
                raise ValueError(problem)
        retry = problem if problem == CUT_OFF else (
            f"That JSON is not valid:\n{problem}\nAnswer again with only the corrected JSON object.")
        messages += [{"role": "assistant", "content": answer}, {"role": "user", "content": retry}]
    raise AssertionError("unreachable")


# ---------- Disk cache ----------


def cache_key(*parts: bytes) -> str:
    """Stable key for a call: change any part (model, prompt, image...) and the key changes."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def cached(cache_dir: Path, key: str, schema: type[T], compute: Callable[[TokenCount], tuple[T, str]],
           check: Callable[[T], str | None] | None = None) -> dict:
    """Run `compute` once per key and keep its answer on disk, so a repeated run does not pay twice.

    A cached answer that the current `check` rejects (the checks got stricter
    since it was stored) is computed again. Returns {"analysis", "reasoning",
    "tokens", "cached"}; tokens are what the original call cost, and cached
    says whether this run reused it.
    """
    path = cache_dir / f"{key}.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        record["analysis"] = schema.model_validate(record["analysis"])
        if check is None or check(record["analysis"]) is None:
            return record | {"cached": True}

    tokens = TokenCount()
    result, reasoning = compute(tokens)
    record = {"analysis": result, "reasoning": reasoning, "tokens": tokens.as_dict()}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record | {"analysis": result.model_dump()}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return record | {"cached": False}
