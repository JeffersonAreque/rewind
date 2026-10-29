"""One HTML report for a whole run: runs/<id>/report.html.

It only reads what the agents already wrote (screens.json, flows.json,
app/spec.json, app/build.json, verification.json), so it costs no tokens.
Missing parts (a run not built or not verified yet) are left out.

The file stands alone: every image is embedded once as base64 and each
<img data-img="..."> gets it by key, so a keyframe shown three times is
stored once.
"""

import base64
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from rewind.agents.builder import as_built
from rewind.agents.screens import normalize

TEMPLATES = Path(__file__).resolve().parent / "templates"


def read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def describe(step: dict) -> str:
    value = f" = {step['value']!r}" if step.get("value") else ""
    return f"{step['action']} {' '.join(step['target'].split())}{value}"


def new_screen_image(name: str, steps: list[dict]) -> str | None:
    """The new app's screenshot right after it opened this legacy screen (or else any step on it)."""
    mine = [s for s in steps if s["new_image"] and s["verdict"] != "fail" and normalize(s["screen"]) == normalize(name)]
    opened = [s for s in mine if s["action"] == "open_screen" and normalize(s["target"]) == normalize(name)]
    return (opened or mine or [{"new_image": None}])[0]["new_image"]


def collect(run_dir: Path) -> dict:
    """Everything the report shows, with image paths relative to run_dir."""
    screens = read(run_dir / "screens.json")
    if screens is None:
        raise ValueError(f"{run_dir} has no screens.json")
    flows = read(run_dir / "flows.json")
    spec = read(run_dir / "app" / "spec.json")
    build = read(run_dir / "app" / "build.json")
    verification = read(run_dir / "verification.json")

    checked = {s["n"]: s for s in (verification or {}).get("steps", [])}
    modern = {normalize(s["legacy"]): s["title"] for s in (spec or {}).get("screens", [])}
    data = {
        "title": spec["title"] if spec else Path(screens["video"]).stem,
        "video": Path(screens["video"]).name,
        "run": run_dir.name,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": screens.get("model"),
        "screens": [{
            "name": s["name"], "modern": modern.get(normalize(s["name"])), "legacy_image": s["image"],
            "new_image": new_screen_image(s["name"], list(checked.values())),
            "fields": sum(len(sec["fields"]) for sec in s["sections"]), "buttons": len(s["buttons"]),
        } for s in screens["screens"]],
        "workflows": [], "rules": [], "changes": [], "steps": [], "tests": [], "summary": None,
    }
    if flows:
        steps = {s["n"]: s for s in flows["steps"]}
        data["workflows"] = [w | {"items": [{"n": n, "text": describe(steps[n]),
                                             "verdict": checked.get(n, {}).get("verdict")}
                                            for n in w["steps"] if n in steps]} for w in flows["workflows"]]
        verdicts = {r["n"]: r["verdict"] for r in (verification or {}).get("rules", [])}
        data["rules"] = [r | {"n": i, "verdict": verdicts.get(i)} for i, r in enumerate(flows["rules"], 1)]
    if spec:
        data["changes"] = [c | dict(zip(("before", "after"), as_built(c, spec))) for c in spec.get("changes", [])]
    if build:
        data["tests"] = build["tests"]
    if verification:
        data["summary"] = verification["summary"]
        data["steps"] = [s | {"text": describe(s)} for s in verification["steps"]]
    return data


def images(data: dict, run_dir: Path) -> dict[str, str]:
    """{relative path: data URI} for every image the report uses, each read once."""
    paths = {s[k] for s in data["screens"] + data["steps"] for k in ("legacy_image", "new_image") if s.get(k)}
    return {p: "data:image/png;base64," + base64.b64encode((run_dir / p).read_bytes()).decode()
            for p in sorted(paths) if (run_dir / p).is_file()}


def render(data: dict, embedded: dict[str, str]) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    script_safe = json.dumps(embedded).replace("</", "<\\/")  # nothing can close the <script> early
    return env.get_template("report.html").render(data=data, images=script_safe)


def write(run_dir: Path) -> Path:
    """Write and return runs/<id>/report.html."""
    data = collect(run_dir)
    path = run_dir / "report.html"
    path.write_text(render(data, images(data, run_dir)), encoding="utf-8")
    return path
