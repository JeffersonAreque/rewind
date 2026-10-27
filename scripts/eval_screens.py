"""Score a screens agent run against the hand-written expected answers.

Every expected item is worth one point: each screen found, each field found,
its editable flag, its type, each button, menu entry, table column and
dialog, plus one point for the navigation order. The accuracy is points
earned over points possible. Precision is the share of reported fields and
buttons (on matched screens) that really exist, so made-up elements count
against the agent.

Usage:
    uv run python scripts/eval_screens.py [runs/<id>] [--expected samples/facturacion-demo.expected.json] [--min 80]

Without a run folder it scores the latest run in runs/.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rewind.agents.screens import normalize  # noqa: E402

DEFAULT_EXPECTED = ROOT / "samples" / "facturacion-demo.expected.json"


class Score:
    def __init__(self) -> None:
        self.earned = 0
        self.possible = 0

    def check(self, ok: bool) -> bool:
        self.possible += 1
        self.earned += ok
        return ok

    @property
    def percent(self) -> float:
        return 100 * self.earned / self.possible if self.possible else 100.0


def latest_run() -> Path:
    runs = sorted(p.parent for p in (ROOT / "runs").glob("*/screens.json"))
    if not runs:
        raise SystemExit("no runs found; run `uv run python -m rewind screens VIDEO` first")
    return runs[-1]


def find_screen(expected: dict, found: list[dict]) -> dict | None:
    names = {normalize(n) for n in [expected["name"], *expected.get("aliases", [])]}
    for screen in found:
        if normalize(screen["name"]) in names:
            return screen
    return None


def mark(ok: bool) -> str:
    return "ok" if ok else "--"


def score_screen(expected: dict, screen: dict, total: Score, lines: list[str]) -> tuple[int, int]:
    """Score one matched screen. Returns (real items reported, items reported) for precision."""
    fields = {normalize(f["label"]): f for s in screen["sections"] for f in s["fields"]}
    buttons = {normalize(b["label"]) for b in screen["buttons"]}
    menus = {normalize(m) for m in screen["menus"]}
    columns = {normalize(c) for t in screen["tables"] for c in t["columns"]}
    dialogs = [normalize(d["message"]) for d in screen["dialogs"]]

    lines.append(f"  {'field':<18} found  editable  type")
    for exp in expected["fields"]:
        got = fields.get(normalize(exp["label"]))
        found = total.check(got is not None)
        editable = total.check(found and got["editable"] == exp["editable"])
        kind = total.check(found and got["type"] == exp["type"])
        detail = "" if not found or kind else f"(got {got['type']})"
        lines.append(f"  {exp['label']:<18} {mark(found):<6} {mark(editable):<9} {mark(kind)} {detail}")

    def items(title: str, wanted: list[str], have) -> None:
        if not wanted:
            return
        hits = [total.check(have(w)) for w in wanted]
        missing = [w for w, ok in zip(wanted, hits) if not ok]
        lines.append(f"  {title:<18} {sum(hits)}/{len(wanted)}" + (f"  missing: {', '.join(missing)}" if missing else ""))

    items("buttons", expected["buttons"], lambda b: normalize(b) in buttons)
    items("menus", expected["menus"], lambda m: normalize(m) in menus)
    items("table columns", [c for t in expected["tables"] for c in t["columns"]], lambda c: normalize(c) in columns)
    items("dialogs", expected["dialogs"], lambda d: any(normalize(d) in got for got in dialogs))

    expected_fields = {normalize(f["label"]) for f in expected["fields"]}
    expected_buttons = {normalize(b) for b in expected["buttons"]}
    extra = sorted(set(fields) - expected_fields) + sorted(buttons - expected_buttons)
    if extra:
        lines.append(f"  not in the app:    {', '.join(extra)}")
    reported = len(fields) + len(buttons)
    return reported - len(extra), reported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, nargs="?", help="run folder or screens.json (default: latest run)")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--min", type=float, default=80, help="exit 1 below this accuracy %% (default 80)")
    args = parser.parse_args()

    run = args.run or latest_run()
    report_path = run / "screens.json" if run.is_dir() else run
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    print(f"Run:      {report_path}")
    print(f"Expected: {args.expected}\n")

    total = Score()
    real = reported = 0
    names: dict[str, str] = {}
    for exp in expected["screens"]:
        screen = find_screen(exp, report["screens"])
        print(f"[{mark(total.check(screen is not None))}] {exp['name']}"
              + (f"  (found as {screen['name']})" if screen else "  NOT FOUND"))
        if screen is None:
            # Everything inside a missing screen is a miss.
            total.possible += 3 * len(exp["fields"]) + len(exp["buttons"]) + len(exp["menus"])
            total.possible += sum(len(t["columns"]) for t in exp["tables"]) + len(exp["dialogs"])
            continue
        names[screen["id"]] = exp["name"]
        lines: list[str] = []
        ok, count = score_screen(exp, screen, total, lines)
        real, reported = real + ok, reported + count
        print("\n".join(lines) + "\n")

    found_nav = [names.get(step["screen"], step["screen"]) for step in report["navigation"]]
    nav_ok = total.check([normalize(n) for n in found_nav] == [normalize(n) for n in expected["navigation"]])
    print(f"[{mark(nav_ok)}] navigation: {' -> '.join(found_nav)}")

    extra_screens = [s["name"] for s in report["screens"] if s["id"] not in names]
    if extra_screens:
        print(f"Screens not in the app: {', '.join(extra_screens)}")

    precision = 100 * real / reported if reported else 100.0
    print(f"\nAccuracy:  {total.percent:.1f}%  ({total.earned}/{total.possible} expected items)")
    print(f"Precision: {precision:.1f}%  ({real}/{reported} reported fields and buttons exist)")
    return 0 if total.percent >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
