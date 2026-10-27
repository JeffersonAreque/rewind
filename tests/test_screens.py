from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rewind.agents.screens import FrameAnalysis, analyze_cached, analyze_frame, cache_key, extract_json
from rewind.llm import TokenCount

VALID = '{"screen_name": "LOGIN", "window_title": "INICIO DE SESION"}'


@pytest.mark.parametrize("answer", [
    VALID,
    f"```json\n{VALID}\n```",
    f"Here it is:\n{VALID}\nDone.",
])
def test_extract_json(answer):
    assert FrameAnalysis.model_validate_json(extract_json(answer)).screen_name == "LOGIN"


class FakeClient:
    """Stands in for the OpenAI client: replies with the given answers in order."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **kwargs):
        self.calls.append(list(messages))
        message = SimpleNamespace(content=self.answers.pop(0), model_extra={"reasoning_content": "thinking"})
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def test_retry_sends_validation_error_back(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    client = FakeClient('{"screen_name": "LOGIN"}', VALID)
    tokens = TokenCount()

    analysis, reasoning = analyze_frame(client, "model", image, tokens)

    assert analysis.window_title == "INICIO DE SESION"
    assert reasoning == "thinking"
    assert tokens.calls == 2
    assert "window_title" in client.calls[1][-1]["content"]


def test_cache_avoids_second_call(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    client = FakeClient(VALID)

    first = analyze_cached(client, "model", image, tmp_path / "cache")
    second = analyze_cached(client, "model", image, tmp_path / "cache")

    assert not first["cached"] and second["cached"]
    assert second["analysis"] == first["analysis"]
    assert len(client.calls) == 1


def test_cache_key_depends_on_model_and_image(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert cache_key(a, "m1") != cache_key(b, "m1")
    assert cache_key(a, "m1") != cache_key(a, "m2")


def test_gives_up_after_retries(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"png")
    client = FakeClient("no json", "still no json", "nope")
    with pytest.raises(ValidationError):
        analyze_frame(client, "model", image, TokenCount())
