"""Flows agent.

Input:  a screens agent run (runs/<id>/: screens.json, frames.json, frames/*.png).
Output: runs/<id>/flows.json with the ordered user steps, the business
        workflows they form and the business rules the app shows.

Muse Spark Open looks at each pair of consecutive keyframes and says what
the user did in between. Code adds the facts Agent 1 already knows (screen
changes, messages) and cross-checks every typed value against later frames,
so a misread value is flagged instead of trusted. A last text-only call
groups the steps into workflows and infers the rules.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from rewind.agents.screens import FrameAnalysis
from rewind.config import ROOT
from rewind.llm import TokenCount, ask_json, cache_key, cached, image_url

CACHE_DIR = ROOT / "runs" / "cache" / "flows"


class Action(BaseModel):
    action: Literal["type", "click", "select"] = Field(
        description="type = text typed in a field; select = option of a list, radio button or table row; "
                    "click = button or menu entry"
    )
    target: str = Field(description="label of the field, button, menu entry or table, exactly as on screen")
    element: Literal["field", "button", "menu", "radio", "list", "row"]
    value: str | None = Field(None, description="text typed or option chosen, exactly as shown; null if not visible")
    value_visible: bool = Field(True, description="false for passwords and values that cannot be read")


class Observation(BaseModel):
    subject: str = Field(description="what changed, e.g. 'ESTADO of invoice F001-000110', 'status bar', 'CLIENTES REGISTRADOS rows'")
    before: str
    after: str


class PairAnalysis(BaseModel):
    actions: list[Action] = Field(default_factory=list, description="user actions between the two frames, in order")
    observations: list[Observation] = Field(
        default_factory=list,
        description="data that changed as a result and was not typed by the user (record counts, statuses, balances, new rows)",
    )


PAIR_PROMPT = f"""You see two consecutive keyframes, BEFORE and AFTER, of a screen recording of an old
business application. Between them the user did zero or more actions. List them in order.
Rules:
- "type" = typing text into a field. "select" = choosing an option of a list, a radio button
  or a row of a table (target = the table name, value = the row's first cell).
  "click" = pressing a button or a menu entry.
- Only list actions that explain a visible difference. A mouse move alone is not an action.
- When a new screen or a message box appears, include the click that caused it. Closing a
  message box is a click on its button.
- value: the exact text typed or option chosen, as seen in AFTER. Passwords and values you
  cannot read: value null and value_visible false. Never guess a value.
- Use labels exactly as on screen, without the trailing colon.
- observations: data that changed as a result and was not typed by the user (record counts,
  statuses, balances, new rows), each with its before and after value.
The text descriptions come from another reader and may contain reading mistakes; trust the images.
Answer with only one JSON object, no comments, matching this JSON schema:
{json.dumps(PairAnalysis.model_json_schema())}"""


def describe(frame: dict) -> str:
    """Short text version of Agent 1's reading of a frame, to share its vocabulary with the model."""
    a: FrameAnalysis = frame["analysis"]
    lines = [f"screen: {a.screen_name} (t={frame['time']}s)"]
    for section in a.sections:
        fields = ", ".join(
            f"{f.label}={f.value!r}" + ("" if f.editable else " (read-only)") for f in section.fields
        )
        lines.append(f"{section.name or 'fields'}: {fields}")
    if a.buttons:
        lines.append("buttons: " + ", ".join(b.label + ("" if b.enabled else " (disabled)") for b in a.buttons))
    for table in a.tables:
        lines.append(f"table {table.name}: {table.rows} rows, columns {', '.join(table.columns)}")
    if a.dialog:
        lines.append(f"message box ({a.dialog.kind}): {a.dialog.message!r} buttons {a.dialog.buttons}")
    if a.status_bar:
        lines.append("status bar: " + " | ".join(a.status_bar))
    return "\n".join(lines)


def pair_content(before: dict, after: dict, run_dir: Path) -> list[dict]:
    return [
        {"type": "text", "text": PAIR_PROMPT},
        {"type": "text", "text": "BEFORE\n" + describe(before)},
        {"type": "image_url", "image_url": {"url": image_url(run_dir / before["image"])}},
        {"type": "text", "text": "AFTER\n" + describe(after)},
        {"type": "image_url", "image_url": {"url": image_url(run_dir / after["image"])}},
    ]


def analyze_pair(client: OpenAI, model: str, before: dict, after: dict, run_dir: Path,
                 cache_dir: Path = CACHE_DIR) -> dict:
    """What the user did between two keyframes, cached by both images and their descriptions."""
    key = cache_key(
        model.encode(), PAIR_PROMPT.encode(),
        describe(before).encode(), (run_dir / before["image"]).read_bytes(),
        describe(after).encode(), (run_dir / after["image"]).read_bytes(),
    )
    content = pair_content(before, after, run_dir)
    return cached(cache_dir, key, PairAnalysis, lambda tokens: ask_json(client, model, content, PairAnalysis, tokens))


def load_frames(run_dir: Path) -> list[dict]:
    """Agent 1's frames that were read successfully, in time order."""
    frames = json.loads((run_dir / "frames.json").read_text(encoding="utf-8"))
    return [
        f | {"analysis": FrameAnalysis.model_validate(f["analysis"])}
        for f in sorted(frames, key=lambda f: f["time"])
        if "analysis" in f
    ]


def read_pairs(client: OpenAI, model: str, frames: list[dict], run_dir: Path, workers: int, log) -> list[dict]:
    """Analyse every pair of consecutive frames in parallel. Failed pairs carry an "error"."""

    def one(i: int) -> dict:
        before, after = frames[i], frames[i + 1]
        pair = {"from": before["index"], "to": after["index"], "time_from": before["time"], "time_to": after["time"]}
        try:
            result = analyze_pair(client, model, before, after, run_dir)
        except Exception as exc:  # one bad pair must not stop the run
            log(f"  {before['index']:3d}->{after['index']:<3d} ERROR {type(exc).__name__}: skipped")
            return pair | {"error": str(exc)}
        actions = "; ".join(
            f"{a.action} {a.target}" + (f"={a.value!r}" if a.value else "") for a in result["analysis"].actions
        ) or "(no action)"
        source = "cache" if result["cached"] else f"{result['tokens']['total']} tokens"
        log(f"  {before['index']:3d}->{after['index']:<3d} {actions}  ({source})")
        return pair | result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, range(len(frames) - 1)))
