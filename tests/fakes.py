from types import SimpleNamespace


class FakeClient:
    """Stands in for the OpenAI client: replies with the given answers in order.
    An empty answer is one the token limit cut off (finish_reason "length")."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **kwargs):
        self.calls.append(list(messages))
        answer = self.answers.pop(0)
        message = SimpleNamespace(content=answer, model_extra={"reasoning_content": "thinking"})
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None)
        choice = SimpleNamespace(message=message, finish_reason="length" if answer == "" else "stop")
        return SimpleNamespace(choices=[choice], usage=usage)
