"""Builder agent.

Input:  a run with screens.json (Agent 1) and flows.json (Agent 2).
Output: runs/<id>/app/, a modern web app that replaces the legacy one:
        the fixed runtime (Flask + SQLite) plus spec.json, seed.json,
        CHANGES.md and one replay test per workflow.

Muse Spark Open never writes code. It answers JSON that pydantic and the
checkers in rewind/builder/spec.py validate:
  A. the rows of each legacy table, read from the first keyframe that shows them (seed data);
  B. the data model, the modernization changes and one spec per legacy screen.
Then code copies the runtime, writes the tests from flows.json and runs them.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, Field

from rewind.agents.flows import load_frames
from rewind.agents.screens import normalize
from rewind.builder import spec as S
from rewind.builder.testgen import write_tests
from rewind.config import ROOT, load_provider
from rewind.llm import TokenCount, ask_json, cache_key, cached, image_url, make_client

CACHE_DIR = ROOT / "runs" / "cache" / "builder"
RUNTIME = ROOT / "rewind" / "builder" / "runtime"


def schema_text(model: type[BaseModel]) -> str:
    return "Answer with only one JSON object, no comments, matching this JSON schema:\n" + json.dumps(
        model.model_json_schema()
    )


def call(client: OpenAI, model: str, prompt: str, evidence: str, schema: type[BaseModel],
         check: Callable | None = None, image: Path | None = None) -> dict:
    """One cached, validated Muse Spark call."""
    content = [{"type": "text", "text": prompt}, {"type": "text", "text": evidence}]
    parts = [model.encode(), prompt.encode(), evidence.encode()]
    if image:
        content.append({"type": "image_url", "image_url": {"url": image_url(image)}})
        parts.append(image.read_bytes())
    return cached(CACHE_DIR, cache_key(*parts), schema,
                  lambda tokens: ask_json(client, model, content, schema, tokens, check=check), check=check)


# ---------- A. Seed data from the table keyframes ----------


class TableRows(BaseModel):
    rows: list[list[str]] = Field(description="data rows top to bottom; each row has one cell per column")


SEED_PROMPT = """This screenshot of an old business application shows the table "{table}" with the
columns {columns}. Transcribe its {count} data rows, top to bottom. Copy every cell exactly as
shown (same capitals, punctuation and currency); an empty cell is "". Do not add rows that are
not visible. """


def first_tables(frames: list[dict]) -> dict[str, tuple[dict, list[str], int]]:
    """For each legacy table, the first frame where it has rows: the data before the user changed it."""
    found = {}
    for frame in frames:
        for table in frame["analysis"].tables:
            if table.rows > 0 and normalize(table.name) not in {normalize(t) for t in found}:
                found[table.name] = (frame, table.columns, table.rows)
    return found


def read_table(client, model, run_dir: Path, table: str, frame: dict, columns: list[str], count: int) -> dict:
    def check(answer: TableRows) -> str | None:
        if len(answer.rows) != count:
            return f"the table shows {count} data rows, you gave {len(answer.rows)}"
        if bad := [i + 1 for i, row in enumerate(answer.rows) if len(row) != len(columns)]:
            return f"rows {bad} do not have {len(columns)} cells ({', '.join(columns)})"
        return None

    prompt = SEED_PROMPT.format(table=table, columns=", ".join(columns), count=count) + schema_text(TableRows)
    return call(client, model, prompt, f"Frame {frame['index']} at {frame['time']}s", TableRows, check,
                image=run_dir / frame["image"])


# ---------- B. Data model, modernization and screens ----------

MODEL_PROMPT = """You design the data model of a modern web app that replaces an old business
application. The evidence comes from a screen recording: the screens and their fields, the rows
of each table, the user's steps and the business rules.
- Entities get short English snake_case names (customer, invoice, payment...) and typed fields:
  text, money (amounts), date, enum (closed list: every value seen plus values the rules imply),
  ref (link to another entity) or serial (a code made from the record number, like C0006).
- Labels are in Spanish, modern sentence case, with accents.
- A record created by a transaction screen (for example a payment) is its own entity with a ref
  to the record it updates.
- Map every column of every legacy table (seed) to a field, or null when it is not stored.
""" + schema_text(S.DataModel)

MODERNIZE_PROMPT = """You modernize the user interface of an old business application. Its business
rules and workflows must keep working exactly; only the interface changes. Propose concrete
changes, each one of these kinds:
- merge_screens: a search screen and the screen it opens, when the steps show they are always used
  together; screens = [search screen, opened screen].
- rename_label: a clearer label. inline_validation: errors next to the field instead of a dialog
  that must be closed. remove_dialog_step: a success message that no longer needs a click.
- default_value, reorder_fields, remove_element (a field, button or menu entry no step uses,
  for example a menu that repeats other entries).
Each change gives how it was (legacy), how it will be (modernized), the reason and the numbers of
the steps that justify it. Also give the side navigation: one item per legacy menu entry kept
(modern label, legacy label, legacy screen it opens); every legacy menu entry is kept or removed
with remove_element. Give the app a short modern title in Spanish.
""" + schema_text(S.Modernization)

SCREEN_PROMPT = """Write the spec of one screen of the modern app that replaces the legacy screen
below. A fixed engine runs it and supports four kinds:
- login: fields named username and password.
- create: a form that creates a record of `entity`, plus a table listing those records.
- search: filters, a selectable results table and buttons; an `open` button opens another screen
  with the selected row (requires_selection when the legacy button was disabled until a row was picked).
- transaction: shows a `parent` record read-only in `shown`; its form creates an `entity` record
  that updates the parent through `effects`.
Rules:
- Keep every legacy field, button, table and column with its exact legacy label in `legacy`,
  unless a remove_element change removes it, and give each a clear modern Spanish `label`.
- Field names are entity field names from the data model.
- Validations reproduce the legacy errors with their exact code and message and cite the number of
  the business rule they come from.
- Effects use safe expressions with entity.field names only (e.g. invoice.balance - payment.amount);
  a status uses cases, the first true case wins.
- Apply the modernization changes that name this screen: inline_validation -> error_style "inline";
  remove_dialog_step -> success_style "toast"; merge_screens -> the opened screen gets embedded_in =
  the search screen.
- success.goto and button targets are legacy screen names; after saving go where the recording went.
- enforces: the numbers of the business rules this screen makes happen (a change made when this
  screen saves belongs here even if it is seen on another screen).
""" + schema_text(S.ScreenSpec)


def screen_summary(screen: dict) -> dict:
    return {k: screen[k] for k in ("name", "window_title", "menus", "sections", "buttons", "tables", "dialogs")}


def numbered_steps(steps: list[dict]) -> str:
    lines = []
    for s in steps:
        value = f" = {s['value']!r}" if s.get("value") else ""
        code = f" [{s['code']}]" if s.get("code") else ""
        lines.append(f"{s['n']}. [{s['screen']}] {s['action']} {s['target']}{value}{code}")
    return "\n".join(lines)


def numbered_rules(rules: list[dict]) -> str:
    return "\n".join(
        f"{i}. ({r['kind']}, {r['screen']}) {r['description']}" + (f" [code {r['code']}]" if r.get("code") else "")
        for i, r in enumerate(rules, 1)
    )


# ---------- C. Assemble the app ----------


def assemble(model: S.DataModel, mod: S.Modernization, screens: list[S.ScreenSpec], flows: dict) -> dict:
    """spec.json for the runtime: legacy screen names become page ids."""
    ids = {normalize(s.legacy): S.slug(s.legacy) for s in screens}
    to_id = lambda name: ids.get(normalize(name or ""))  # noqa: E731

    out_screens = []
    for s in screens:
        data = s.model_dump()
        data["id"] = to_id(s.legacy)
        data["embedded_in"] = to_id(s.embedded_in)
        for a in data["actions"]:
            a["target"] = to_id(a["target"])
        if data["success"]:
            data["success"]["goto"] = to_id(data["success"]["goto"])
        out_screens.append(data)

    login = next(s for s in screens if s.kind == "login")
    steps = flows["steps"]
    user_label = next(f.legacy for f in login.fields if f.name == "username")
    users = sorted({s["value"] for s in steps if s["action"] == "type" and s.get("value")
                    and normalize(s["target"]) == normalize(user_label)}) or ["ADMIN"]
    after_login = next((s["target"] for s in steps if s["action"] == "open_screen"
                        and normalize(s["target"]) != normalize(login.legacy)), screens[0].legacy)
    return {
        "title": mod.app_title,
        "currency": model.currency,
        "users": users,
        "home": to_id(after_login),
        "entities": [e.model_dump() for e in model.entities],
        "nav": [n.model_dump() | {"screen": to_id(n.screen)} for n in mod.nav],
        "screens": out_screens,
        "changes": [c.model_dump() for c in mod.changes],
    }


def seed_records(model: S.DataModel, tables: dict[str, list[str]], rows: dict[str, list[list[str]]]) -> dict:
    """Records per entity from the transcribed tables, in record-number order when a serial code shows it."""
    entities = {e.name: e for e in model.entities}
    seed = {e.name: [] for e in model.entities}
    for mapping in model.seed:
        table = next((t for t in tables if normalize(t) == normalize(mapping.table)), None)
        if table is None:
            continue
        fields = {f.name: f for f in entities[mapping.entity].fields}
        given = {normalize(c): f for c, f in mapping.columns.items()}
        records = []
        for row in rows.get(table, []):
            record, order = {}, 0
            for column, cell in zip(tables[table], row):
                name = given.get(normalize(column))
                if not name or name not in fields:
                    continue
                if fields[name].type == "serial":
                    order = int(re.sub(r"\D", "", cell) or 0)
                else:
                    record[name] = cell
            records.append((order, record))
        seed[mapping.entity] += [r for _, r in sorted(records, key=lambda x: x[0])]
    return seed


def as_built(change: dict, spec: dict) -> tuple[str, str]:
    """(legacy, modernized) of a change, described from the built spec, not from the model's words."""
    by_legacy = {normalize(s["legacy"]): s for s in spec["screens"]}
    screens = [by_legacy.get(normalize(n), {"legacy": n, "title": n}) for n in change["screens"]]
    names = " and ".join(s["legacy"] for s in screens)
    element = change.get("element") or ""
    found = None
    for s in screens:
        items = s.get("fields", []) + s.get("shown", []) + s.get("filters", []) + s.get("actions", [])
        items += (s.get("table") or {}).get("columns", [])
        found = found or next((x for x in items if normalize(x["legacy"]) == normalize(element)), None)
    kind = change["kind"]
    if kind == "merge_screens" and len(screens) == 2:
        return (f"{screens[0]['legacy']} and {screens[1]['legacy']} are separate windows",
                f"{screens[1]['title']} opens as a side panel of {screens[0]['title']}, on the same page")
    if kind == "rename_label" and found:
        return f"«{found['legacy']}»", f"«{found['label']}»"
    if kind == "inline_validation":
        return f"Error dialog that must be closed with ACEPTAR ({names})", "Error shown under the field, nothing to close"
    if kind == "remove_dialog_step":
        return f"Success dialog that must be closed with ACEPTAR ({names})", "Success shown as a toast that leaves on its own"
    if kind == "default_value" and found:
        default = "today's date" if found.get("default") == "today" else repr(found.get("default"))
        return f"«{found['legacy']}»", f"«{found['label']}» starts with {default}"
    if kind == "reorder_fields" and screens:
        s = screens[0]
        order = [x["label"] for x in s.get("shown", []) + s.get("fields", []) + s.get("filters", [])]
        return f"Legacy field order on {s['legacy']}", "Order: " + ", ".join(order)
    if kind == "remove_element":
        return f"«{element}» on {names}", "Removed"
    return change["legacy"], change["modernized"]


def changes_report(spec: dict, flows: dict, steps: list[dict], cover: dict) -> str:
    rules = flows["rules"]
    enforced = {n: s["title"] for s in spec["screens"] for n in s.get("enforces", [])}
    lines = [
        f"# {spec['title']}: legacy vs modernized",
        "",
        "Muse Spark Open proposed each change with the steps of the recording that justify it.",
        "Rewind checked that every legacy element, business rule and recorded step is still covered,",
        "and describes each change below from what was actually built.",
        "",
        "| # | Change | Legacy | Modernized (as built) | Why (Muse Spark) | Evidence steps |",
        "|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(spec["changes"], 1):
        legacy, modern = as_built(c, spec)
        cells = [str(i), c["kind"], legacy, modern, c["reason"], ", ".join(map(str, c["evidence_steps"]))]
        lines.append("| " + " | ".join(x.replace("|", "/").replace("\n", " ") for x in cells) + " |")
    lines += ["", "## Business rules kept", ""]
    for i, r in enumerate(rules, 1):
        where = enforced.get(i, "checked by the replay tests only")
        lines.append(f"{i}. {r['description']} ({where})")
    absorbed = [s for s in steps if s.get("absorbed")]
    lines += ["", "## Steps the new interface no longer needs", ""]
    lines += [f"- Step {s['n']} ({s['action']} {s['target']} on {s['screen']}): {s['absorbed']}" for s in absorbed] or ["- None"]
    lines += ["", "## Legacy elements", ""]
    for name, c in cover.items():
        lines.append(f"- {name}: {c['kept']} of {c['legacy']} kept, {c['removed']} removed on purpose")
    return "\n".join(lines) + "\n"


APP_README = """# {title}

Modern replacement of the legacy app seen in `{video}`, generated by Rewind.

```
uv run python app.py            # http://127.0.0.1:5001
uv run python app.py --reset    # start again from the data of the recording
uv run pytest tests             # replay the recorded workflows
```

Users: {users}. The password comes from `APP_ADMIN_PASSWORD`; if it is empty, a random one is
printed on the first start. `CHANGES.md` lists what changed from the legacy interface and why.
"""


# ---------- E. Run the replay tests ----------


def run_tests(app_dir: Path) -> list[dict]:
    """Run tests/ of the app and return one result per test."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"],
            cwd=app_dir.resolve(), capture_output=True, text=True,
        )
        if not report.exists() or not list(ET.parse(report).getroot().iter("testcase")):
            output = (done.stdout + done.stderr).strip().splitlines()
            return [{"name": "(no tests ran)", "passed": False, "error": output[-1] if output else "no report"}]
        results = []
        for case in ET.parse(report).getroot().iter("testcase"):
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            message = (failure.get("message") or "") if failure is not None else ""
            results.append({"name": case.get("name"), "passed": failure is None,
                            "error": message.splitlines()[0][:300] if message else None})
        return results


# ---------- Whole run ----------


def run(run_dir: Path, provider: str | None = None, run_tests_after: bool = True, workers: int = 4,
        log: Callable[[str], None] = print) -> Path:
    """Build runs/<id>/app from screens.json and flows.json. Returns the app folder."""
    screens_report = json.loads((run_dir / "screens.json").read_text(encoding="utf-8"))
    flows = json.loads((run_dir / "flows.json").read_text(encoding="utf-8"))
    frames = load_frames(run_dir)
    legacy_screens = screens_report["screens"]
    names = [s["name"] for s in legacy_screens]

    config = load_provider(provider)
    if missing := config.missing():
        raise ValueError(f"missing in .env: {', '.join(missing)}")
    client, model = make_client(config), config.model
    calls: list[dict] = []

    def done(result: dict, what: str) -> dict:
        calls.append(result)
        source = "cache" if result["cached"] else f"{result['tokens']['total']} tokens"
        log(f"  {what}  ({source})")
        return result

    # A. Seed
    log("A. seed data from the tables")
    found = first_tables(frames)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {t: pool.submit(read_table, client, model, run_dir, t, *found[t]) for t in found}
    tables = {t: found[t][1] for t in found}
    rows = {t: done(f.result(), f"{t}: {found[t][2]} rows from frame {found[t][0]['index']}")["analysis"].rows
            for t, f in futures.items()}

    # B1. Data model
    log("B. spec")
    evidence = json.dumps({
        "screens": [screen_summary(s) for s in legacy_screens],
        "tables": {t: {"columns": tables[t], "rows": rows[t]} for t in tables},
    }, ensure_ascii=False) + "\n\nSTEPS\n" + numbered_steps(flows["steps"]) + "\n\nRULES\n" + numbered_rules(flows["rules"])
    data_model = done(call(client, model, MODEL_PROMPT, evidence, S.DataModel,
                           lambda m: "\n".join(S.check_data_model(m, tables, rows)) or None),
                      "data model")["analysis"]

    # B2. Modernization
    evidence = json.dumps({"screens": [screen_summary(s) for s in legacy_screens],
                           "entities": [e.model_dump() for e in data_model.entities]}, ensure_ascii=False)
    evidence += "\n\nSTEPS\n" + numbered_steps(flows["steps"]) + "\n\nWORKFLOWS\n" + "\n".join(
        f"- {w['name']}: steps {w['steps']}" for w in flows["workflows"]) + "\n\nRULES\n" + numbered_rules(flows["rules"])
    mod = done(call(client, model, MODERNIZE_PROMPT, evidence, S.Modernization,
                    lambda m: "\n".join(S.check_modernization(m, legacy_screens, flows["steps"])) or None),
               "modernization")["analysis"]

    # B3. One spec per legacy screen
    defaults = {normalize(n): S.legacy_defaults(frames, n) for n in names}

    def screen_call(screen: dict) -> dict:
        own_steps = [s for s in flows["steps"] if normalize(s["screen"]) == normalize(screen["name"])]
        evidence = json.dumps({
            "legacy_screen": screen_summary(screen),
            "all_legacy_screens": names,
            "data_model": data_model.model_dump(),
            "modernization_changes": [c.model_dump() for c in mod.changes],
        }, ensure_ascii=False)
        evidence += "\n\nSTEPS ON THIS SCREEN\n" + numbered_steps(own_steps)
        evidence += "\n\nALL STEPS\n" + numbered_steps(flows["steps"]) + "\n\nRULES\n" + numbered_rules(flows["rules"])
        own_defaults = defaults[normalize(screen["name"])]
        return call(client, model, SCREEN_PROMPT, evidence, S.ScreenSpec,
                    lambda s: "\n".join(S.check_screen(s, screen, data_model, mod, flows["steps"], flows["rules"],
                                                       names, own_defaults)) or None)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(screen_call, legacy_screens))
    specs = [done(r, f"screen {s['name']}")["analysis"] for r, s in zip(results, legacy_screens)]

    # C. App folder. A change that leaves the legacy behaviour as it was is not a change.
    noop = [c for c in mod.changes if S.is_noop(c, specs, defaults)]
    mod = mod.model_copy(update={"changes": [c for c in mod.changes if c not in noop]})
    for c in noop:
        log(f"  dropped no-op change: {c.kind} {c.element} (the legacy screen already did that)")
    app_spec = assemble(data_model, mod, specs, flows)
    seed = seed_records(data_model, tables, rows)
    app_dir = run_dir / "app"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    shutil.copytree(RUNTIME, app_dir, ignore=shutil.ignore_patterns("__pycache__", "__init__.py"))
    (app_dir / "spec.json").write_text(json.dumps(app_spec, ensure_ascii=False, indent=1), encoding="utf-8")
    (app_dir / "seed.json").write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")

    # D. Replay tests
    steps = write_tests(app_dir, flows, app_spec, defaults)
    cover = {s.legacy: S.coverage(s, screen, mod.changes) for s, screen in zip(specs, legacy_screens)}
    (app_dir / "CHANGES.md").write_text(changes_report(app_spec, flows, steps, cover), encoding="utf-8")
    (app_dir / "README.md").write_text(APP_README.format(
        title=app_spec["title"], video=screens_report["video"], users=", ".join(app_spec["users"])), encoding="utf-8")

    # E. Run them
    tests = []
    if run_tests_after:
        log("E. replaying the recorded workflows on the new app")
        tests = run_tests(app_dir)

    spent = TokenCount()
    for c in calls:
        if not c["cached"]:
            spent.merge(TokenCount.from_dict(c["tokens"]))
    workflows = {f"test_{S.slug(w['name']).replace('-', '_')}": w for w in flows["workflows"]}
    report = {
        "run": run_dir.as_posix(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "provider": config.name,
        "model": model,
        "coverage": cover,
        "changes": len(app_spec["changes"]),
        "dropped_noop_changes": [c.model_dump() for c in noop],
        "unclaimed_rules": S.check_rules_enforced(specs, flows["rules"]),
        "steps": len(steps),
        "absorbed_steps": [s["n"] for s in steps if s.get("absorbed")],
        "tests": [t | {"steps": workflows.get(t["name"], {}).get("steps", [])} for t in tests],
        "tokens": spent.as_dict(),
        "cached_calls": sum(1 for c in calls if c["cached"]),
    }
    (app_dir / "build.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return app_dir
