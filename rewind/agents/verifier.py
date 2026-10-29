"""Verifier agent.

Input:  a run with flows.json and its keyframes, and the app the builder made.
Output: runs/<id>/verification.json and VERIFICATION.md, a step-by-step
        report that replays each flow in the new app and compares every
        step visually against the matching keyframe.

1. The replay tests run once per workflow with REWIND_EVIDENCE set: each step
   leaves a screenshot of the new app and whether it replayed.
2. Muse Spark Open compares each screenshot with the step's keyframe
   (frame_to) and answers same_data, same_message and same_outcome (pass,
   fail or unclear) plus the differences. Style, layout, labels, today's
   date and the listed modernization changes don't count. Answers are
   validated and cached by image hash.
3. Code gives the verdicts: a step passes when it replayed and the comparison
   passed; a rule passes when all its evidence steps pass. "unclear" is a
   warning, never a pass.
"""

import json
import os
import shutil
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from rewind.agents.builder import as_built, run_tests, schema_text
from rewind.builder.testgen import TEMPLATE
from rewind.config import ROOT, load_provider
from rewind.llm import TokenCount, ask_json, cache_key, cached, image_url, make_client

CACHE_DIR = ROOT / "runs" / "cache" / "verifier"

Verdict = Literal["pass", "fail", "unclear"]


class Comparison(BaseModel):
    same_data: Verdict = Field(description="the values on screen match: typed values, selected record, rows, amounts, status")
    same_message: Verdict = Field(description="the same error or success message (code and text) is shown, or neither shows one")
    same_outcome: Verdict = Field(description="the step had the same effect: saved or not, same screen open, same buttons enabled")
    differences: list[str] = Field(description="each difference behind a fail or unclear, in one short English sentence")

    @property
    def verdict(self) -> str:
        checks = (self.same_data, self.same_message, self.same_outcome)
        return "fail" if "fail" in checks else "unclear" if "unclear" in checks else "pass"


def check_comparison(answer: Comparison) -> str | None:
    if answer.verdict != "pass" and not answer.differences:
        return "a check is fail or unclear: list the differences behind it"
    return None


# ---------- Comparison (Muse Spark vision) ----------

COMPARE_PROMPT = """You check that a new web app does what an old business application did. Image 1
is the old application, a keyframe of a screen recording. Image 2 is the new app after replaying the
same recorded steps. The new app has a modern interface on purpose, so these are never differences:
- style, colors, fonts, layout, sizes and positions, window frames, menus shown as a side bar;
- labels or titles worded differently, in another case or with accents;
- today's date in a date field, and anything only the old app shows (status bar, clock, taskbar);
- the modernization changes listed below (for example, two windows merged into one page, an error
  shown under the field instead of a dialog, or a toast instead of a dialog that needs ACEPTAR).
Judge only the business behaviour after THIS STEP:
- same_data: values from this step and the steps before it (typed or chosen values, the selected
  record, table rows and their amounts, status, balance) are the same in both images.
- same_message: the same error or success message, with the same code and text, or none in both.
- same_outcome: the step had the same effect (a record saved or not, the same screen or panel open,
  the same button enabled or disabled).
Answer "pass" when it matches, "fail" when the new app clearly differs and "unclear" when the images
cannot tell. Image 1 may have been taken after later steps too (they are listed); what those later
steps changed is not a difference, and what Image 1 cannot show is "unclear", never "fail".
""" + schema_text(Comparison)


def describe(step: dict) -> str:
    value = f" = {step['value']!r}" if step.get("value") else ""
    code = f" [{step['code']}]" if step.get("code") else ""
    return f"{step['n']}. [{step['screen']}] {step['action']} {' '.join(step['target'].split())}{value}{code}"


def step_evidence(step: dict, steps: list[dict], changes: list[str]) -> str:
    """What the model needs to judge one step: the steps before, the step, the later steps in Image 1."""
    before = [s for s in steps if s["n"] < step["n"]]
    later = [s for s in steps if s["n"] > step["n"] and s["frame_to"] == step["frame_to"]]
    text = "STEPS BEFORE (done in both apps)\n" + ("\n".join(map(describe, before)) or "none")
    text += "\n\nTHIS STEP\n" + describe(step)
    if step.get("confidence") == "masked":
        text += ("\nThe new app was replayed with its own test password, so the number of mask characters "
                 "differs on purpose and is never a difference.")
    if later:
        text += "\n\nLATER STEPS ALSO DONE BEFORE IMAGE 1 WAS TAKEN (not yet in Image 2)\n" + "\n".join(map(describe, later))
    text += "\n\nMODERNIZATION CHANGES (never differences)\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(changes, 1))
    return text


def compare(client: OpenAI, model: str, step: dict, steps: list[dict], changes: list[str],
            legacy: Path, new: Path) -> dict:
    """One cached Muse Spark call: the step's keyframe against the new app's screenshot."""
    text = step_evidence(step, steps, changes)
    content = [
        {"type": "text", "text": COMPARE_PROMPT},
        {"type": "text", "text": text},
        {"type": "text", "text": "Image 1: the old application (recording)."},
        {"type": "image_url", "image_url": {"url": image_url(legacy)}},
        {"type": "text", "text": "Image 2: the new app."},
        {"type": "image_url", "image_url": {"url": image_url(new)}},
    ]
    key = cache_key(model.encode(), COMPARE_PROMPT.encode(), text.encode(), legacy.read_bytes(), new.read_bytes())
    return cached(CACHE_DIR, key, Comparison,
                  lambda tokens: ask_json(client, model, content, Comparison, tokens, check=check_comparison),
                  check=check_comparison)


# ---------- Verdicts ----------


def replay_results(folder: Path, steps: list[dict], workflows: list[dict]) -> dict[int, dict]:
    """Per step: replay "pass", "fail", "not reached" or "absorbed", from the files the replay left."""
    workflow_of = {n: w["name"] for w in workflows for n in w["steps"]}
    failed: dict[str, int] = {}
    out = {}
    for s in steps:
        n, wf = s["n"], workflow_of.get(s["n"])
        path = folder / f"step_{n:02d}.json"
        if s.get("absorbed"):
            out[n] = {"replay": "absorbed", "error": None}
        elif path.exists():
            error = json.loads(path.read_text(encoding="utf-8"))["error"]
            out[n] = {"replay": "fail" if error else "pass", "error": error}
            if error:
                failed.setdefault(wf, n)
        else:
            why = f"step {failed[wf]} of this workflow failed first" if wf in failed else "the replay did not reach it"
            out[n] = {"replay": "not reached", "error": why}
    return out


def step_verdict(replay: str, comparison: Comparison | None) -> str:
    """pass only when the step replayed and the comparison passed; unclear is never a pass."""
    if replay == "absorbed":
        return "absorbed"
    if replay != "pass":
        return "fail"
    return comparison.verdict if comparison else "unclear"


def rule_verdict(evidence: list[int], verdicts: dict[int, str]) -> str:
    """fail if an evidence step fails, else unclear if one is unclear (or none can be checked), else pass."""
    found = [verdicts[n] for n in evidence if verdicts.get(n, "absorbed") != "absorbed"]
    if "fail" in found:
        return "fail"
    return "unclear" if not found or "unclear" in found else "pass"


def counts(items: list[dict], kinds: tuple[str, ...] = ("pass", "fail", "unclear")) -> dict[str, int]:
    found = Counter(i["verdict"] for i in items)
    return {k: found[k] for k in kinds}


# ---------- Report ----------

LABEL = {"pass": "pass", "fail": "**FAIL**", "unclear": "unclear (warning)", "absorbed": "absorbed"}


def cell(text) -> str:
    return " ".join(str(text).split()).replace("|", "/")


def report_md(result: dict) -> str:
    steps, rules = result["steps"], result["rules"]
    s, r = result["summary"]["steps"], result["summary"]["rules"]
    lines = [
        f"# {result['title']}: verification",
        "",
        "The new app replayed the steps of the recording, one workflow at a time, and left a screenshot",
        "after each step. Muse Spark Open compared each screenshot with the recording's keyframe for that",
        "step. Style, layout, labels, today's date and the changes listed in CHANGES.md don't count.",
        "A step passes only when it replayed and the comparison passed; a rule passes only when all its",
        "evidence steps pass. \"Unclear\" is a warning, never a pass.",
        "",
        f"**Steps:** {s['pass']} pass, {s['fail']} fail, {s['unclear']} unclear, "
        f"{s['absorbed']} absorbed by changes. "
        f"**Rules:** {r['pass']} pass, {r['fail']} fail, {r['unclear']} unclear.",
        "",
        "## Rules",
        "",
        "| # | Rule | Evidence steps | Verdict |",
        "|---|---|---|---|",
    ]
    for rule in rules:
        lines.append(f"| {rule['n']} | {cell(rule['description'])} | {', '.join(map(str, rule['evidence_steps']))} "
                     f"| {LABEL[rule['verdict']]} |")
    lines += ["", "## Steps", "",
              "| Step | Workflow | Action | Replay | Data | Message | Outcome | Verdict |",
              "|---|---|---|---|---|---|---|---|"]
    for st in steps:
        c = st["comparison"] or {}
        lines.append("| " + " | ".join([
            str(st["n"]), cell(st["workflow"] or ""), cell(describe(st).split(". ", 1)[1]), st["replay"],
            c.get("same_data", "-"), c.get("same_message", "-"), c.get("same_outcome", "-"), LABEL[st["verdict"]],
        ]) + " |")
    lines += ["", "## Evidence", ""]
    for st in steps:
        if st["verdict"] == "absorbed":
            continue
        lines += [f"### Step {st['n']}: {LABEL[st['verdict']]}", "", f"`{cell(describe(st))}`", ""]
        if st["new_image"] or st["legacy_image"]:
            legacy = f"![step {st['n']} in the recording]({st['legacy_image']})" if st["legacy_image"] else "-"
            new = f"![step {st['n']} in the new app]({st['new_image']})" if st["new_image"] else "-"
            lines += [f"| Recording (keyframe {st['frame_to']}) | New app |", "|---|---|", f"| {legacy} | {new} |", ""]
        if st["replay_error"]:
            lines.append(f"- Replay: {st['replay_error']}")
        lines += [f"- {d}" for d in (st["comparison"] or {}).get("differences", [])]
        if st["comparison_error"]:
            lines.append(f"- Comparison failed: {st['comparison_error']}")
        lines.append("")
    absorbed = [st for st in steps if st["verdict"] == "absorbed"]
    lines += ["## Steps absorbed by changes", ""]
    lines += [f"- Step {st['n']} (`{cell(describe(st))}`): {st['absorbed']}" for st in absorbed] or ["- None"]
    return "\n".join(lines) + "\n"


# ---------- Whole run ----------


def run(run_dir: Path, app_dir: Path | None = None, out_dir: Path | None = None, provider: str | None = None,
        workers: int = 4, log: Callable[[str], None] = print) -> Path:
    """Verify the app (default runs/<id>/app) against the recording. Returns verification.json."""
    app_dir, out_dir = app_dir or run_dir / "app", out_dir or run_dir
    flows = json.loads((run_dir / "flows.json").read_text(encoding="utf-8"))
    frames = {f["index"]: run_dir / f["image"] for f in json.loads((run_dir / "frames.json").read_text(encoding="utf-8"))}
    spec = json.loads((app_dir / "spec.json").read_text(encoding="utf-8"))
    steps = json.loads((app_dir / "tests" / "steps.json").read_text(encoding="utf-8"))

    config = load_provider(provider)
    if missing := config.missing():
        raise ValueError(f"missing in .env: {', '.join(missing)}")
    client, model = make_client(config), config.model

    # 1. Replay once per workflow, with a screenshot after each step
    log("1. replaying each workflow on the new app")
    for name in ("replay.py", "conftest.py"):  # an app built before the screenshot hook gets it too
        shutil.copy(TEMPLATE / name, app_dir / "tests" / name)
    folder = out_dir / "evidence" / "new"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    tests = run_tests(app_dir, {"REWIND_EVIDENCE": str(folder.resolve())})
    for t in tests:
        log(f"  [{'ok' if t['passed'] else '--'}] {t['name']}" + (f": {t['error']}" if t["error"] else ""))
    replayed = replay_results(folder, steps, flows["workflows"])

    # 2. Compare every step that replayed with its keyframe
    log("2. comparing each step with the recording")
    changes = [f"{c['kind']}: " + " -> ".join(as_built(c, spec)) for c in spec.get("changes", [])]
    todo = [s for s in steps if replayed[s["n"]]["replay"] == "pass" and frames.get(s["frame_to"])]

    def one(step: dict) -> dict:
        try:
            return compare(client, model, step, steps, changes, frames[step["frame_to"]], folder / f"step_{step['n']:02d}.png")
        except ValueError as exc:  # the model never gave a valid answer
            return {"error": str(exc).splitlines()[0][:300], "cached": False, "tokens": TokenCount().as_dict()}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = dict(zip((s["n"] for s in todo), pool.map(one, todo)))
    for n, a in answers.items():
        source = "cache" if a["cached"] else f"{a['tokens']['total']} tokens"
        log(f"  step {n}: {a['analysis'].verdict if 'analysis' in a else 'error'}  ({source})")

    # 3. Verdicts
    workflow_of = {n: w["name"] for w in flows["workflows"] for n in w["steps"]}
    rel = lambda p: Path(os.path.relpath(p, out_dir)).as_posix()  # noqa: E731
    results = []
    for s in steps:
        n, replay = s["n"], replayed[s["n"]]
        comparison = answers.get(n, {}).get("analysis")
        new_image = folder / f"step_{n:02d}.png"
        legacy_image = frames.get(s["frame_to"])
        results.append({
            "n": n, "screen": s["screen"], "action": s["action"], "target": s["target"],
            "value": s.get("value"), "code": s.get("code"), "workflow": workflow_of.get(n),
            "frame_to": s["frame_to"], "absorbed": s.get("absorbed"),
            "legacy_image": rel(legacy_image) if legacy_image and not s.get("absorbed") else None,
            "new_image": rel(new_image) if new_image.exists() else None,
            "replay": replay["replay"], "replay_error": replay["error"],
            "comparison": comparison.model_dump() if comparison else None,
            "comparison_error": answers.get(n, {}).get("error"),
            "verdict": step_verdict(replay["replay"], comparison),
        })
    verdicts = {r["n"]: r["verdict"] for r in results}
    rules = [r | {"n": i, "verdict": rule_verdict(r["evidence_steps"], verdicts)} for i, r in enumerate(flows["rules"], 1)]

    spent = TokenCount()
    for a in answers.values():
        if not a["cached"]:
            spent.merge(TokenCount.from_dict(a["tokens"]))
    result = {
        "run": run_dir.as_posix(),
        "app": app_dir.as_posix(),
        "title": spec["title"],
        "created": datetime.now().isoformat(timespec="seconds"),
        "provider": config.name,
        "model": model,
        "summary": {"steps": counts(results, ("pass", "fail", "unclear", "absorbed")), "rules": counts(rules)},
        "workflows": tests,
        "steps": results,
        "rules": rules,
        "tokens": spent.as_dict(),
        "cached_calls": sum(1 for a in answers.values() if a["cached"]),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "verification.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "VERIFICATION.md").write_text(report_md(result), encoding="utf-8")
    return path
