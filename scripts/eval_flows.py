"""Score a flows agent run against the hand-written expected flows.

- Steps: the found steps are aligned with the expected ones in order
  (longest common subsequence on screen, action and target). Reports the
  share found and the precision (found steps that are really in the video).
- Values: of the expected typed or chosen values, how many are right. A wrong
  value the agent did not flag as low confidence is the worst error, so
  those are listed apart.
- Workflows: an expected workflow is found when a reported workflow covers
  the same steps with an overlap (Jaccard) of at least 60 %. Names don't count.
- Rules: an expected rule is found when one reported rule matches all its
  patterns (regular expressions on the rule's text).

The overall score is the mean of the four percentages.

Usage:
    uv run python scripts/eval_flows.py [runs/<id>] [--expected samples/facturacion-demo.expected-flows.json] [--min 80]

Without a run folder it scores the latest run that has a flows.json.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rewind.agents.screens import normalize  # noqa: E402

DEFAULT_EXPECTED = ROOT / "samples" / "facturacion-demo.expected-flows.json"
MIN_OVERLAP = 0.6


def latest_run() -> Path:
    runs = sorted(p.parent for p in (ROOT / "runs").glob("*/flows.json"))
    if not runs:
        raise SystemExit("no flows runs found; run `uv run python -m rewind flows runs/<id>` first")
    return runs[-1]


def same_step(expected: dict, found: dict) -> bool:
    if expected["action"] != found["action"] or normalize(expected["screen"]) != normalize(found["screen"]):
        return False
    if expected["action"] == "see_message":
        if expected.get("code"):
            return (found.get("code") or "").lower() == expected["code"].lower()
        return normalize(expected["target"]) in normalize(found["target"])
    return normalize(expected["target"]) == normalize(found["target"])


def align(expected: list[dict], found: list[dict]) -> dict[int, int]:
    """Longest common subsequence: {expected index: found index} of matched steps."""
    rows, cols = len(expected), len(found)
    best = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols - 1, -1, -1):
            best[i][j] = (best[i + 1][j + 1] + 1 if same_step(expected[i], found[j])
                          else max(best[i + 1][j], best[i][j + 1]))
    pairs, i, j = {}, 0, 0
    while i < rows and j < cols:
        if same_step(expected[i], found[j]) and best[i][j] == best[i + 1][j + 1] + 1:
            pairs[i], i, j = j, i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def percent(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 100.0


def describe_step(step: dict) -> str:
    value = f" = {step['value']!r}" if step.get("value") else ""
    return f"[{step['screen']}] {step['action']} {step['target'][:45]}{value}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, nargs="?", help="run folder or flows.json (default: latest run)")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--min", type=float, default=80, help="exit 1 below this overall %% (default 80)")
    args = parser.parse_args()

    run = args.run or latest_run()
    report_path = run / "flows.json" if run.is_dir() else run
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    print(f"Run:      {report_path}")
    print(f"Expected: {args.expected}\n")

    # Steps
    exp_steps, found_steps = expected["steps"], report["steps"]
    matched = align(exp_steps, found_steps)
    print("STEPS")
    for i, exp in enumerate(exp_steps):
        print(f"  [{'ok' if i in matched else '--'}] {i + 1:2d}. {describe_step(exp)}")
    extra = [found_steps[j] for j in range(len(found_steps)) if j not in matched.values()]
    for step in extra:
        print(f"  not in the video: {step['n']}. {describe_step(step)}")
    steps_pct = percent(len(matched), len(exp_steps))
    precision = percent(len(matched), len(found_steps))

    # Values
    print("\nVALUES")
    with_value = [i for i, e in enumerate(exp_steps) if "value" in e or e.get("masked")]
    right, unflagged = 0, []
    for i in with_value:
        exp = exp_steps[i]
        if i not in matched:
            print(f"  [--] {exp['target']}: step not found")
            continue
        got = found_steps[matched[i]]
        if exp.get("masked"):
            ok = got.get("value") is None
            shown = "masked" if ok else repr(got["value"])
        else:
            ok = normalize(got.get("value") or "") == normalize(exp["value"])
            shown = repr(got.get("value"))
        right += ok
        flag = f" ({got['confidence']}" + (f", also read as {got['variants']})" if got.get("variants") else ")")
        print(f"  [{'ok' if ok else '--'}] {exp['target']}: {shown}{flag}")
        if not ok and got.get("confidence") != "low":
            unflagged.append(exp["target"])
    values_pct = percent(right, len(with_value))

    # Workflows
    print("\nWORKFLOWS")
    found_to_expected = {found_steps[j]["n"]: i + 1 for i, j in matched.items()}
    found_wfs = [
        (wf["name"], {found_to_expected[n] for n in wf["steps"] if n in found_to_expected})
        for wf in report["workflows"]
    ]
    wf_found = 0
    for wf in expected["workflows"]:
        want = set(wf["steps"])
        name, overlap = max(
            ((name, len(want & got) / len(want | got)) for name, got in found_wfs), key=lambda x: x[1], default=("", 0.0)
        )
        ok = overlap >= MIN_OVERLAP
        wf_found += ok
        print(f"  [{'ok' if ok else '--'}] {wf['name']:<26} best match: {name!r} ({overlap:.0%} overlap)")
    workflows_pct = percent(wf_found, len(expected["workflows"]))

    # Rules
    print("\nRULES")
    texts = [normalize(" ".join(str(r.get(k) or "") for k in ("kind", "screen", "field", "code", "description")))
             for r in report["rules"]]
    rules_found = 0
    for rule in expected["rules"]:
        hit = next((t for t in texts if all(re.search(p, t, re.IGNORECASE) for p in rule["patterns"])), None)
        rules_found += hit is not None
        print(f"  [{'ok' if hit else '--'}] {rule['name']}")
    rules_pct = percent(rules_found, len(expected["rules"]))

    overall = (steps_pct + values_pct + workflows_pct + rules_pct) / 4
    print(f"\nSteps:     {steps_pct:5.1f}%  ({len(matched)}/{len(exp_steps)}); precision {precision:.1f}% "
          f"({len(matched)}/{len(found_steps)} found steps are in the video)")
    print(f"Values:    {values_pct:5.1f}%  ({right}/{len(with_value)})"
          + (f"; WRONG AND NOT FLAGGED: {', '.join(unflagged)}" if unflagged else "; every wrong value was flagged"))
    print(f"Workflows: {workflows_pct:5.1f}%  ({wf_found}/{len(expected['workflows'])})")
    print(f"Rules:     {rules_pct:5.1f}%  ({rules_found}/{len(expected['rules'])})")
    print(f"Overall:   {overall:5.1f}%")
    return 0 if overall >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
