import pytest
from pydantic import BaseModel

from rewind.llm import TokenCount, ask_json
from tests.fakes import FakeClient


class Answer(BaseModel):
    steps: list[int]


def no_duplicates(answer: Answer) -> str | None:
    return "step 2 is listed twice" if len(set(answer.steps)) != len(answer.steps) else None


def test_check_rejection_is_sent_back():
    client = FakeClient('{"steps": [1, 2, 2]}', '{"steps": [1, 2]}')
    answer, _ = ask_json(client, "model", [], Answer, TokenCount(), check=no_duplicates)
    assert answer.steps == [1, 2]
    assert "step 2 is listed twice" in client.calls[1][-1]["content"]


def test_check_failure_is_raised_after_retries():
    client = FakeClient(*['{"steps": [2, 2]}'] * 3)
    with pytest.raises(ValueError, match="listed twice"):
        ask_json(client, "model", [], Answer, TokenCount(), check=no_duplicates)
