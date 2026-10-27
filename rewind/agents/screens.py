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
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Literal

from openai import BadRequestError, OpenAI
from pydantic import BaseModel, Field, ValidationError

from rewind.config import ROOT, load_provider
from rewind.keyframes import Settings, extract
from rewind.llm import TokenCount, make_client, split_reasoning

RETRIES = 2
MAX_TOKENS = 12000
CACHE_DIR = ROOT / "runs" / "cache" / "screens"


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


def cache_key(image: Path, model: str) -> str:
    """Same image, model and prompt give the same key; editing the prompt invalidates old entries."""
    digest = hashlib.sha256()
    for part in (model.encode(), PROMPT.encode(), image.read_bytes()):
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def analyze_cached(client: OpenAI, model: str, image: Path, cache_dir: Path = CACHE_DIR) -> dict:
    """analyze_frame with a disk cache, so a repeated run does not pay twice.

    Returns {"analysis", "reasoning", "tokens", "cached"}; tokens are what the
    original call cost, and cached says whether this run reused it.
    """
    path = cache_dir / f"{cache_key(image, model)}.json"
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        record["analysis"] = FrameAnalysis.model_validate(record["analysis"])
        return record | {"cached": True}

    tokens = TokenCount()
    analysis, reasoning = analyze_frame(client, model, image, tokens)
    record = {"analysis": analysis, "reasoning": reasoning, "tokens": tokens.as_dict()}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record | {"analysis": analysis.model_dump()}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return record | {"cached": False}


# ---------- Grouping frames into screens ----------


def normalize(text: str) -> str:
    """Comparable form of a label: capitals, no accents, no colons, single spaces and
    no trailing dot ("MONTO S/." and "MONTO S/" are the same label)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.upper().replace(":", " ").split()).rstrip(" .")


def most_common(values: list):
    return Counter(values).most_common(1)[0][0]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(name).lower()).strip("-")


def merge_fields(observations: list[tuple[float, UIField]]) -> dict:
    """One field seen in several frames: majority vote for type and editable,
    union of options, and every distinct value with the time it appeared."""
    fields = [f for _, f in observations]
    editable = [f.editable for f in fields]
    values: list[dict] = []
    for time, f in observations:
        if f.value and (not values or values[-1]["value"] != f.value):
            values.append({"time": time, "value": f.value})
    return {
        "label": fields[0].label,
        "type": most_common([f.type for f in fields]),
        "editable": editable.count(True) >= editable.count(False),
        "options": list(dict.fromkeys(o for f in fields for o in f.options)),
        "values": values,
        "seen": len(fields),
    }


def merge_screen(name: str, frames: list[dict]) -> dict:
    """Merge every frame of one screen into a single description."""
    analyses: list[tuple[float, FrameAnalysis]] = [(f["time"], f["analysis"]) for f in frames]

    sections: dict[str, dict] = {}
    for time, a in analyses:
        for section in a.sections:
            entry = sections.setdefault(normalize(section.name), {"name": section.name, "fields": {}})
            for field in section.fields:
                entry["fields"].setdefault(normalize(field.label), []).append((time, field))

    buttons: dict[str, list[Button]] = {}
    for _, a in analyses:
        for button in a.buttons:
            buttons.setdefault(normalize(button.label), []).append(button)

    tables: dict[str, Table] = {}
    for _, a in analyses:
        for table in a.tables:
            key = normalize(table.name) or "|".join(map(normalize, table.columns))
            known = tables.get(key)
            best = table if known is None or len(table.columns) > len(known.columns) else known
            tables[key] = best.model_copy(update={"rows": max(table.rows, known.rows if known else 0)})

    dialogs: list[dict] = []
    for time, a in analyses:
        if a.dialog and normalize(a.dialog.message) not in {normalize(d["message"]) for d in dialogs}:
            dialogs.append({"time": time} | a.dialog.model_dump())

    # Representative image: no dialog in front, as many fields as possible.
    best = max(frames, key=lambda f: (f["analysis"].dialog is None, sum(len(s.fields) for s in f["analysis"].sections)))

    return {
        "id": slug(name),
        "name": name,
        "window_title": most_common([a.window_title for _, a in analyses]),
        "first_seen": analyses[0][0],
        "frames": [f["index"] for f in frames],
        "image": best["image"],
        "menus": list(dict.fromkeys(m for _, a in analyses for m in a.menus)),
        "sections": [
            {"name": s["name"], "fields": [merge_fields(obs) for obs in s["fields"].values()]}
            for s in sections.values()
        ],
        "buttons": [
            {"label": seen[0].label, "enabled": any(b.enabled for b in seen), "always_enabled": all(b.enabled for b in seen)}
            for seen in buttons.values()
        ],
        "tables": [t.model_dump() for t in tables.values()],
        "dialogs": dialogs,
    }


def group_screens(frames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Group analysed frames by screen name, in order of first appearance.

    Returns (screens, navigation). Navigation lists each screen visit in
    order; staying on the same screen is one visit. A dialog never opens a
    new screen: it is kept as an event of the screen behind it.
    """
    by_name: dict[str, list[dict]] = {}
    for frame in frames:
        by_name.setdefault(normalize(frame["analysis"].screen_name), []).append(frame)

    screens = [merge_screen(most_common([f["analysis"].screen_name for f in group]), group) for group in by_name.values()]
    ids = {key: screen["id"] for key, screen in zip(by_name, screens)}

    navigation: list[dict] = []
    for frame in frames:
        screen_id = ids[normalize(frame["analysis"].screen_name)]
        if not navigation or navigation[-1]["screen"] != screen_id:
            navigation.append({"screen": screen_id, "time": frame["time"]})
    return screens, navigation


# ---------- Whole run ----------


def run(
    video: Path,
    out_root: Path,
    provider: str | None = None,
    dry_run: bool = False,
    workers: int = 4,
    max_frames: int | None = None,
    settings: Settings = Settings(),
    log: Callable[[str], None] = print,
) -> Path:
    """Run the screens agent on a video. Returns the run folder."""
    run_dir = out_root / f"{datetime.now():%Y%m%d-%H%M%S}-{video.stem}"
    keyframes = extract(video, run_dir / "frames", settings, max_frames)
    log(f"{len(keyframes)} keyframes -> {run_dir / 'frames'}")
    if dry_run:
        for kf in keyframes:
            log(f"  {kf.index:3d}  {kf.time:5.1f}s  {kf.path.name}")
        return run_dir

    config = load_provider(provider)
    if missing := config.missing():
        raise ValueError(f"missing in .env: {', '.join(missing)}")
    client = make_client(config)

    def analyze(kf) -> dict:
        frame = {"index": kf.index, "time": kf.time, "image": kf.path.relative_to(run_dir).as_posix()}
        try:
            result = analyze_cached(client, config.model, kf.path)
        except (ValidationError, BadRequestError) as exc:
            log(f"  {kf.index:3d}  {kf.time:5.1f}s  ERROR {type(exc).__name__}: skipped")
            return frame | {"error": str(exc)}
        a = result["analysis"]
        source = "cache" if result["cached"] else f"{result['tokens']['total']} tokens"
        extra = f" + dialog {a.dialog.kind.upper()}" if a.dialog else ""
        log(f"  {kf.index:3d}  {kf.time:5.1f}s  {a.screen_name}{extra}  ({source})")
        return frame | result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = list(pool.map(analyze, keyframes))

    spent = TokenCount()
    for f in frames:
        if not f.get("cached", True):
            spent.merge(TokenCount(**{k: f["tokens"][k] for k in ("prompt", "completion", "reasoning", "calls")}))
    analysed = [f for f in frames if "analysis" in f]
    screens, navigation = group_screens(analysed)

    (run_dir / "frames.json").write_text(
        json.dumps(
            [f | {"analysis": f["analysis"].model_dump()} if "analysis" in f else f for f in frames],
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    ids = {normalize(s["name"]): s["id"] for s in screens}
    report = {
        "video": video.as_posix(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "provider": config.name,
        "model": config.model,
        "segmentation": "off",  # SAM 3.1 is optional and not enabled
        "keyframes": [
            {
                "index": f["index"],
                "time": f["time"],
                "image": f["image"],
                "screen": ids[normalize(f["analysis"].screen_name)] if "analysis" in f else None,
                "dialog": f["analysis"].dialog.message if "analysis" in f and f["analysis"].dialog else None,
            }
            for f in frames
        ],
        "screens": screens,
        "navigation": navigation,
        "tokens": spent.as_dict(),
        "cached_frames": sum(1 for f in analysed if f["cached"]),
        "failed_frames": [f["index"] for f in frames if "error" in f],
    }
    (run_dir / "screens.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return run_dir
