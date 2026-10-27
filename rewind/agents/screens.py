"""Screens agent.

Input:  screen recording of the legacy app (MP4).
Output: runs/<id>/screens.json with every distinct screen (fields, buttons,
        menus, tables, dialogs) and the order they appear in, plus the
        keyframes as PNG.

Muse Spark Open does all the reading: each keyframe goes to the model,
which answers with structured JSON validated by pydantic. SAM 3.1 is not
used; the optional `box` of each element is where it would plug in.
"""

import base64
import json
import re
from pathlib import Path
from typing import Literal

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, Field, ValidationError

from rewind.llm import TokenCount, split_reasoning

RETRIES = 2
MAX_TOKENS = 12000


class Box(BaseModel):
    """Approximate position in pixels of the screenshot."""

    x: int
    y: int
    w: int
    h: int


class UIField(BaseModel):
    label: str = Field(description="label text exactly as shown, without the trailing colon")
    type: Literal["text", "password", "select", "radio", "checkbox", "date", "amount", "other"] = "text"
    value: str = Field("", description="current value; password fields show only the mask")
    editable: bool = Field(description="white background = true, grey background = false")
    options: list[str] = Field(default_factory=list, description="visible choices of radio buttons or lists")
    box: Box | None = None


class Section(BaseModel):
    name: str = Field("", description="title of the group box, empty if there is none")
    fields: list[UIField] = Field(default_factory=list)


class Button(BaseModel):
    label: str
    enabled: bool = Field(True, description="grey label = disabled")
    box: Box | None = None


class Table(BaseModel):
    name: str = Field("", description="title of the group box around the table")
    columns: list[str]
    rows: int = Field(0, description="number of data rows visible")


class Dialog(BaseModel):
    kind: Literal["error", "warning", "info", "question"]
    title: str
    message: str
    buttons: list[str] = Field(default_factory=list)


class FrameAnalysis(BaseModel):
    screen_name: str = Field(description="name of the screen, usually from the window title, e.g. REGISTRO DE CLIENTES")
    window_title: str
    menus: list[str] = Field(default_factory=list, description="top-level menu bar entries")
    sections: list[Section] = Field(default_factory=list)
    buttons: list[Button] = Field(default_factory=list, description="buttons of the main window, not of the dialog")
    tables: list[Table] = Field(default_factory=list)
    dialog: Dialog | None = Field(None, description="message box shown on top of the window, if any")
    status_bar: list[str] = Field(default_factory=list)


PROMPT_VERSION = 1
PROMPT = f"""You are reading one screenshot of an old desktop business application.
Describe the user interface as JSON. Rules:
- Copy every text exactly as shown (same language, same capitals). Do not translate.
- screen_name names the screen itself, not a dialog over it. Use the text inside the
  window title (for "APP v2.3 - [CUSTOMERS]" it is "CUSTOMERS"); for a login window use its title.
- A text box with a WHITE background is editable; one with a GREY background is read-only.
- A button whose label is grey is disabled.
- Field labels go without the trailing colon. Group fields under the box (frame) they are in.
- Window controls (minimize, maximize, close) and the taskbar are not buttons of the screen.
- Put tables (grids) in "tables", not in "fields".
- If a message box is open, describe it in "dialog" and still describe the window behind it.
- "box" is optional: the approximate pixel rectangle of the element in this image.

Answer with only one JSON object, no comments, matching this JSON schema:
{json.dumps(FrameAnalysis.model_json_schema())}
"""

FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def extract_json(answer: str) -> str:
    """Take the JSON object out of an answer that may wrap it in ``` or prose."""
    answer = FENCE.sub("", answer.strip())
    start, end = answer.find("{"), answer.rfind("}")
    return answer[start:end + 1] if start != -1 and end > start else answer


def image_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "frame_analysis", "schema": FrameAnalysis.model_json_schema()},
}
_schema_supported = True


def complete(client: OpenAI, model: str, messages: list[dict]):
    """Chat call that asks for schema-constrained JSON when the provider allows it.

    Baseten accepts response_format; a provider that rejects it falls back to
    the schema written in the prompt, which pydantic still validates.
    """
    global _schema_supported
    if _schema_supported:
        try:
            return client.chat.completions.create(
                model=model, messages=messages, max_tokens=MAX_TOKENS, response_format=RESPONSE_FORMAT
            )
        except BadRequestError as exc:
            if "response_format" not in str(exc) and "json_schema" not in str(exc):
                raise
            _schema_supported = False
    return client.chat.completions.create(model=model, messages=messages, max_tokens=MAX_TOKENS)


def analyze_frame(client: OpenAI, model: str, image: Path, tokens: TokenCount) -> tuple[FrameAnalysis, str]:
    """Ask the model to read one keyframe. Returns the analysis and the model's reasoning.

    A malformed answer is sent back with the validation error, up to RETRIES times.
    """
    messages: list[dict] = [{
        "role": "user",
        "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": image_url(image)}},
        ],
    }]
    for attempt in range(RETRIES + 1):
        response = complete(client, model, messages)
        tokens.add(response.usage)
        reasoning, answer = split_reasoning(response.choices[0].message)
        try:
            return FrameAnalysis.model_validate_json(extract_json(answer)), reasoning
        except ValidationError as exc:
            if attempt == RETRIES:
                raise
            messages += [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": f"That JSON is not valid:\n{exc}\nAnswer again with only the corrected JSON object."},
            ]
    raise AssertionError("unreachable")
