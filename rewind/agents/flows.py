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
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from rewind.agents.screens import PLACEHOLDER, FrameAnalysis, normalize
from rewind.config import ROOT, load_provider
from rewind.llm import TokenCount, ask_json, cache_key, cached, image_url, make_client

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


# ---------- Steps ----------

# Codes in error and warning messages, in the shapes old programs use: 0x0042, E-101, ORA-00001, SQL0803.
ERROR_CODE = re.compile(r"\b(?:0x[0-9A-F]{2,8}|[A-Z]{1,4}-\d{2,6}|[A-Z]{2,4}\d{3,6})\b", re.IGNORECASE)
SIMILAR = 0.8  # difflib ratio from which two readings count as the same value misread


class Step(BaseModel):
    n: int = 0
    screen: str
    action: Literal["open_screen", "type", "click", "select", "see_message"]
    target: str = Field(description="field, button, menu, table, screen name or message text")
    element: str | None = None  # field/button/menu/radio/list/row, or the message kind
    value: str | None = None
    value_visible: bool = True
    code: str | None = None  # error code of a message, e.g. 0x0042
    frame_from: int
    frame_to: int
    time_from: float
    time_to: float
    readings: list[dict] = Field(default_factory=list)  # every reading of the value: {"source", "value"}
    confidence: Literal["high", "medium", "low", "masked", "none"] = "none"
    variants: list[str] = Field(default_factory=list)  # readings that disagree with the chosen value


def similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio() >= SIMILAR


def find_reread(steps: list[Step], action, screen: str) -> Step | None:
    """The earlier step this action repeats, if any.

    A field still being filled shows up in two pairs (half typed, or misread
    once): same screen visit, same target, a similar value and no click in
    between. Those are one step with two readings, not two steps.
    """
    for step in reversed(steps):
        if step.action in ("open_screen", "click", "see_message"):
            return None
        if (step.screen == screen and step.action == action.action
                and normalize(step.target) == normalize(action.target)
                and (not step.value or not action.value or similar(step.value, action.value))):
            return step
    return None


def typed_before(first: dict) -> list[Step]:
    """Values already in the editable fields of the recording's first keyframe: the user typed
    or chose them before the screen first stood still (a fast login), so they are steps."""
    a, steps = first["analysis"], []
    where = {"frame_from": first["index"], "frame_to": first["index"], "time_from": first["time"], "time_to": first["time"]}
    for f in (f for s in a.sections for f in s.fields):
        if not f.editable or not f.value or PLACEHOLDER.match(normalize(f.value)):
            continue
        secret = f.type == "password"
        steps.append(Step(
            screen=a.screen_name, action="select" if f.type in ("select", "radio") else "type", target=f.label,
            element=f.type, value=None if secret else f.value, value_visible=not secret,
            readings=[] if secret else [{"source": f"frame {first['index']}", "value": f.value}], **where,
        ))
    return steps


def build_steps(frames: list[dict], pairs: list[dict]) -> list[Step]:
    """Ordered user steps: the model's actions for each pair plus the facts Agent 1
    already knows (screen changes and messages), which need no guessing."""
    first = frames[0]
    steps = [Step(
        screen=first["analysis"].screen_name, action="open_screen", target=first["analysis"].screen_name,
        frame_from=first["index"], frame_to=first["index"], time_from=first["time"], time_to=first["time"],
    ), *typed_before(first)]
    by_index = {f["index"]: f for f in frames}
    for pair in pairs:
        before, after = by_index[pair["from"]]["analysis"], by_index[pair["to"]]["analysis"]
        where = {"frame_from": pair["from"], "frame_to": pair["to"],
                 "time_from": pair["time_from"], "time_to": pair["time_to"]}
        source = f"pair {pair['from']}->{pair['to']}"

        for action in pair["analysis"].actions if "analysis" in pair else []:
            reading = [{"source": source, "value": action.value}] if action.value else []
            if action.action in ("type", "select") and (earlier := find_reread(steps, action, before.screen_name)):
                earlier.readings += reading
                earlier.value = action.value or earlier.value  # the later reading saw more of the value
                earlier.frame_to, earlier.time_to = where["frame_to"], where["time_to"]
                continue
            steps.append(Step(
                screen=before.screen_name, action=action.action, target=action.target, element=action.element,
                value=action.value, value_visible=action.value_visible, readings=reading, **where,
            ))

        if normalize(after.screen_name) != normalize(before.screen_name):
            steps.append(Step(screen=after.screen_name, action="open_screen", target=after.screen_name, **where))
        if after.dialog and (not before.dialog or normalize(before.dialog.message) != normalize(after.dialog.message)):
            code = ERROR_CODE.search(after.dialog.message) if after.dialog.kind in ("error", "warning") else None
            steps.append(Step(
                screen=after.screen_name, action="see_message", target=after.dialog.message,
                element=after.dialog.kind, code=code.group(0) if code else None, **where,
            ))

    for n, step in enumerate(steps, 1):
        step.n = n
    return steps


# ---------- Cross-checking values ----------


def field_readings(step: Step, frames: list[dict]) -> list[dict]:
    """How Agent 1 read the step's field in the frames after it was filled.

    Same screen: every frame until the field is emptied or changed. Other
    screens: only frames that show a similar value (e.g. the customer chosen
    in the search shown again on the payment screen).
    """
    label, reference = normalize(step.target), step.value
    readings = []
    for frame in frames:
        if frame["index"] <= step.frame_from:
            continue
        a = frame["analysis"]
        same_screen = normalize(a.screen_name) == normalize(step.screen)
        for field in (f for s in a.sections for f in s.fields if normalize(f.label) == label):
            if same_screen and (not field.value or (reference and not similar(field.value, reference))):
                return readings  # cleared or changed by the user: later frames say nothing about this step
            if same_screen or (reference and field.value and similar(field.value, reference)):
                readings.append({"source": f"frame {frame['index']}", "value": field.value, "time": frame["time"]})
                reference = reference or field.value
            break
    return readings


def text_readings(step: Step, frames: list[dict], pairs: list[dict]) -> list[dict]:
    """Later texts that show the value: a status bar 'LABEL: VALUE' (which can
    reveal a value never seen typed) or a status bar / observation containing it."""
    label = re.compile(rf"^\s*{re.escape(normalize(step.target))}\s+(.+?)\s*$")
    texts: dict[str, tuple[str, float]] = {}  # each distinct text counts once
    for frame in frames:
        if frame["index"] > step.frame_from:
            for line in frame["analysis"].status_bar:
                texts.setdefault(line, (f"status bar frame {frame['index']}", frame["time"]))
    for pair in pairs:
        if pair["to"] > step.frame_from and "analysis" in pair:
            for obs in pair["analysis"].observations:
                texts.setdefault(obs.after, (f"observation {pair['from']}->{pair['to']}", pair["time_to"]))

    readings = []
    for text, (source, time) in texts.items():
        # A status bar copied into one text joins its panels with "|".
        labelled = [m.group(1) for part in text.split("|") if (m := label.match(normalize(part)))]
        if labelled:
            readings.append({"source": source, "value": labelled[0], "time": time})
        elif step.value and normalize(step.value) in normalize(text):
            readings.append({"source": source, "value": step.value, "time": time})
    return readings


def is_password(step: Step, frames: list[dict]) -> bool:
    return any(
        f.type == "password"
        for frame in frames if normalize(frame["analysis"].screen_name) == normalize(step.screen)
        for s in frame["analysis"].sections for f in s.fields if normalize(f.label) == normalize(step.target)
    )


def decide(step: Step, readings: list[dict]) -> None:
    """Pick the value most readings agree on and say how sure we are.

    high: two or more readings, all equal. medium: a single reading, or a clear
    majority (at least twice all the others). low: no clear majority. The
    readings that disagree are kept in `variants`; nothing is made up.
    """
    groups: dict[str, list[dict]] = {}
    for reading in sorted(readings, key=lambda r: r.get("time", 0)):
        groups.setdefault(normalize(reading["value"]), []).append(reading)
    if not groups:
        return
    # Most readings wins; on a tie the latest reading (the value was more complete or less covered).
    winner = max(groups.values(), key=lambda g: (len(g), g[-1].get("time", 0)))
    others = sum(len(g) for g in groups.values() if g is not winner)
    step.value = winner[-1]["value"]
    step.variants = [g[-1]["value"] for g in groups.values() if g is not winner]
    if others == 0:
        step.confidence = "high" if len(winner) >= 2 else "medium"
    else:
        step.confidence = "medium" if len(winner) >= 2 * others else "low"


def cross_check(steps: list[Step], frames: list[dict], pairs: list[dict]) -> None:
    """Set value, confidence and variants of every type/select step, in place."""
    time_of = {f"pair {p['from']}->{p['to']}": p["time_to"] for p in pairs}
    for step in steps:
        if step.action not in ("type", "select"):
            continue
        step.readings = [r | {"time": r.get("time", time_of.get(r["source"], 0))} for r in step.readings]
        step.readings += field_readings(step, frames)
        step.readings += text_readings(step, frames, pairs)
        if step.readings:
            decide(step, step.readings)
        elif is_password(step, frames):
            step.value, step.confidence = None, "masked"


# ---------- Workflows and business rules ----------


class Workflow(BaseModel):
    name: str = Field(description="short business name, 2 to 4 words, e.g. 'Register customer'")
    goal: str = Field(description="what the user achieves, one sentence")
    steps: list[int] = Field(description="numbers of the steps of this workflow")
    screens: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    kind: Literal["required_field", "validation", "state_change", "calculation", "enablement", "navigation", "other"]
    screen: str
    field: str | None = None
    code: str | None = Field(None, description="error code shown by the app, e.g. 0x0042")
    description: str = Field(description="the rule in one sentence, with the values seen as evidence")
    evidence_steps: list[int] = Field(default_factory=list)


class Synthesis(BaseModel):
    workflows: list[Workflow]
    rules: list[Rule]


SYNTHESIS_PROMPT = f"""Below is what a user did in an old business application, reconstructed from a
screen recording: the numbered steps, the data changes observed between steps, and the screens.

1. Group the steps into business workflows: tasks with a business purpose (for example logging
   in, registering a customer, searching invoices, registering a payment). Every step belongs to
   exactly one workflow, and each workflow is a run of consecutive steps.
2. Infer the business rules that the application shows, only from this evidence:
   - required fields and validation errors, with their error codes;
   - state changes of records (status before -> after);
   - calculations, with the arithmetic using the numbers seen;
   - buttons that are only enabled under a condition;
   - where an action takes the user (navigation).
   Every rule cites the steps that show it. Do not add rules without evidence.

Answer with only one JSON object, no comments, matching this JSON schema:
{json.dumps(Synthesis.model_json_schema())}"""


def synthesis_input(steps: list[Step], pairs: list[dict], screens: list[dict]) -> str:
    """Plain-text evidence for the workflows and rules call."""
    lines = ["STEPS"]
    for s in steps:
        value = f" = {s.value!r}" if s.value else ""
        note = f" ({s.confidence} confidence)" if s.confidence in ("medium", "low") else ""
        code = f" [code {s.code}, {s.element}]" if s.code else (f" [{s.element}]" if s.action == "see_message" else "")
        lines.append(f"{s.n}. [{s.screen}] {s.action} {s.target}{value}{note}{code}")

    lines.append("\nDATA CHANGES")
    for p in pairs:
        if "analysis" not in p:
            continue
        around = [s.n for s in steps if s.frame_from >= p["from"] and s.frame_to <= p["to"]]
        when = f"during steps {around[0]}-{around[-1]}" if around else f"between frames {p['from']} and {p['to']}"
        for o in p["analysis"].observations:
            lines.append(f"- {when}: {o.subject}: {o.before} -> {o.after}")

    lines.append("\nSCREENS")
    for screen in screens:
        lines.append(f"- {screen['name']}")
        conditional = [b["label"] for b in screen["buttons"] if b["enabled"] and not b["always_enabled"]]
        if conditional:
            lines.append(f"  buttons sometimes disabled: {', '.join(conditional)}")
        for d in screen["dialogs"]:
            lines.append(f"  message ({d['kind']}): {d['message']!r}")
    return "\n".join(lines)


def check_synthesis(steps: list[Step]):
    """Reject workflows or rules that point at steps that do not exist or overlap."""
    numbers = {s.n for s in steps}

    def check(answer: Synthesis) -> str | None:
        seen: dict[int, str] = {}
        for wf in answer.workflows:
            for n in wf.steps:
                if n not in numbers:
                    return f"workflow {wf.name!r} lists step {n}, which does not exist (steps are 1-{len(steps)})"
                if n in seen:
                    return f"step {n} is in two workflows: {seen[n]!r} and {wf.name!r}"
                seen[n] = wf.name
        if missing := sorted(numbers - set(seen)):
            return f"steps {missing} are in no workflow; every step belongs to exactly one workflow"
        for rule in answer.rules:
            if bad := [n for n in rule.evidence_steps if n not in numbers]:
                return f"rule {rule.description!r} cites steps {bad}, which do not exist"
        return None

    return check


def synthesize(client: OpenAI, model: str, steps: list[Step], pairs: list[dict], screens: list[dict],
               cache_dir: Path = CACHE_DIR) -> dict:
    """Workflows and rules from the steps (one text-only call, cached by its input)."""
    evidence = synthesis_input(steps, pairs, screens)
    content = [{"type": "text", "text": SYNTHESIS_PROMPT}, {"type": "text", "text": evidence}]
    key = cache_key(model.encode(), SYNTHESIS_PROMPT.encode(), evidence.encode())
    return cached(
        cache_dir, key, Synthesis,
        lambda tokens: ask_json(client, model, content, Synthesis, tokens, check=check_synthesis(steps)),
    )


# ---------- Whole run ----------


def run(run_dir: Path, provider: str | None = None, workers: int = 4, log: Callable[[str], None] = print) -> Path:
    """Run the flows agent on a screens agent run. Writes and returns run_dir/flows.json."""
    screens_report = json.loads((run_dir / "screens.json").read_text(encoding="utf-8"))
    frames = load_frames(run_dir)
    if len(frames) < 2:
        raise ValueError(f"{run_dir} has fewer than 2 analysed keyframes")

    config = load_provider(provider)
    if missing := config.missing():
        raise ValueError(f"missing in .env: {', '.join(missing)}")
    client = make_client(config)

    log(f"{len(frames) - 1} keyframe pairs")
    pairs = read_pairs(client, config.model, frames, run_dir, workers, log)
    steps = build_steps(frames, pairs)
    cross_check(steps, frames, pairs)
    log("workflows and rules...")
    synthesis = synthesize(client, config.model, steps, pairs, screens_report["screens"])

    calls = [p for p in pairs if "analysis" in p] + [synthesis]
    spent = TokenCount()
    for call in calls:
        if not call["cached"]:
            spent.merge(TokenCount.from_dict(call["tokens"]))

    result = synthesis["analysis"]
    report = {
        "run": run_dir.as_posix(),
        "video": screens_report["video"],
        "created": datetime.now().isoformat(timespec="seconds"),
        "provider": config.name,
        "model": config.model,
        "steps": [s.model_dump() for s in steps],
        "workflows": [w.model_dump() for w in result.workflows],
        "rules": [r.model_dump() for r in result.rules],
        "observations": [
            {"from": p["from"], "to": p["to"], "time_from": p["time_from"], "time_to": p["time_to"]} | o.model_dump()
            for p in pairs if "analysis" in p for o in p["analysis"].observations
        ],
        "low_confidence": [s.n for s in steps if s.confidence == "low"],
        "tokens": spent.as_dict(),
        "cached_calls": sum(1 for c in calls if c["cached"]),
        "failed_pairs": [f"{p['from']}->{p['to']}" for p in pairs if "error" in p],
    }
    path = run_dir / "flows.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
