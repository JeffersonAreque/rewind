from types import SimpleNamespace


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
