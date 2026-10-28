"""Check that the verifier catches broken apps.

Verifies the healthy app of a run and 3 broken copies of it:
- no balance formula: paying no longer lowers the invoice balance;
- no required field: RUC / DNI can be saved empty;
- no EFECTIVO: the payment method loses its EFECTIVO option.

The healthy app must have no failed step or rule. Each broken copy must fail
the step where the break shows and a rule about it. Steps are found by what
they do (screen, action, target), rules by patterns on their text, so the
check does not depend on how the flows run numbered them. Exits 1 if
anything is missed.

Each copy is verified in runs/<id>/eval/<name>/ (app/, verification.json and
VERIFICATION.md). Comparisons are cached by image, so a broken copy only pays
for the screenshots that change.

Usage:
    uv run python scripts/eval_verifier.py [runs/<id>]

Without a run folder it uses the latest run that has a built app.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rewind.agents import verifier  # noqa: E402
from rewind.agents.screens import normalize  # noqa: E402


def no_balance_formula(spec: dict) -> None:
    for s in spec["screens"]:
        s["effects"] = [e for e in s.get("effects", []) if not e["target"].endswith(".balance")]


def no_required_field(spec: dict) -> None:
    for s in spec["screens"]:
        names = {f["name"] for f in s.get("fields", []) if normalize(f["legacy"]) == "RUC / DNI"}
        s["validations"] = [v for v in s.get("validations", []) if not (v["check"] == "required" and v["field"] in names)]


def no_efectivo(spec: dict) -> None:
    for entity in spec["entities"]:
        for f in entity["fields"]:
            f["enum_values"] = [v for v in f["enum_values"] if normalize(v) != "EFECTIVO"]
    for s in spec["screens"]:
        for f in s.get("fields", []):
            if normalize(f.get("default") or "") == "EFECTIVO":
                f["default"] = None


# name, break, the step that must fail (screen, action, words in its target or code), rule patterns
COPIES = [
    ("healthy", None, None, None),
    ("no-balance-formula", no_balance_formula, ("BUSQUEDA DE FACTURAS", "see_message", "OPERACION COMPLETADA"), ["SALDO"]),
    ("no-required-field", no_required_field, ("REGISTRO DE CLIENTES", "see_message", "0X0042"), ["0X0042"]),
    ("no-efectivo", no_efectivo, ("REGISTRAR PAGO", "open_screen", "REGISTRAR PAGO"), ["REGISTRAR PAGO"]),
]


def latest_run() -> Path:
    runs = sorted(p.parent.parent for p in (ROOT / "runs").glob("*/app/spec.json"))
    if not runs:
        raise SystemExit("no built app found; run `uv run python -m rewind build runs/<id>` first")
    return runs[-1]


def broken_copy(app: Path, dest: Path, breaks) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(app, dest, ignore=shutil.ignore_patterns("__pycache__", "data"))
    spec = json.loads((dest / "spec.json").read_text(encoding="utf-8"))
    before = json.dumps(spec)
    breaks(spec)
    if json.dumps(spec) == before:
        raise SystemExit(f"{breaks.__name__}: the spec has nothing to break")
    (dest / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")


def is_step(step: dict, screen: str, action: str, words: str) -> bool:
    return (normalize(step["screen"]) == screen and step["action"] == action
            and words in normalize(f"{step['target']} {step.get('code') or ''}"))


def rule_text(rule: dict) -> str:
    return normalize(" ".join(str(rule.get(k) or "") for k in ("kind", "screen", "field", "code", "description")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, nargs="?", help="run folder with a built app (default: latest)")
    args = parser.parse_args()
    run = args.run or latest_run()
    print(f"Run: {run}")

    missed, tokens = [], 0
    for name, breaks, step_wanted, rule_patterns in COPIES:
        out = run / "eval" / name
        app = run / "app"
        if breaks:
            app = out / "app"
            broken_copy(run / "app", app, breaks)
        print(f"\n{name.upper()}")
        report = json.loads(verifier.run(run, app, out, log=lambda _: None).read_text(encoding="utf-8"))
        tokens += report["tokens"]["total"]
        failed_steps = [s for s in report["steps"] if s["verdict"] == "fail"]
        failed_rules = [r for r in report["rules"] if r["verdict"] == "fail"]
        s = report["summary"]["steps"]
        print(f"  steps: {s['pass']} pass, {s['fail']} fail, {s['unclear']} unclear; "
              f"rules failed: {[r['n'] for r in failed_rules]}; {report['tokens']['total']} tokens")
        for st in failed_steps:
            why = st["replay_error"] or "; ".join((st["comparison"] or {}).get("differences", []))
            print(f"    step {st['n']} {st['action']} {' '.join(st['target'].split())}: {why[:150]}")

        if breaks is None:
            checks = [("no failed step", not failed_steps), ("no failed rule", not failed_rules)]
        else:
            checks = [
                (f"fails the {step_wanted[1]} {step_wanted[2]} step on {step_wanted[0]}",
                 any(is_step(st, *step_wanted) for st in failed_steps)),
                (f"fails a rule about {' '.join(rule_patterns)}",
                 any(all(re.search(p, rule_text(r)) for p in rule_patterns) for r in failed_rules)),
            ]
        for what, ok in checks:
            print(f"  [{'ok' if ok else '--'}] {what}")
            if not ok:
                missed.append(f"{name}: {what}")

    print(f"\nTokens this run: {tokens}")
    if missed:
        print("MISSED:\n" + "\n".join(f"  {m}" for m in missed), file=sys.stderr)
        return 1
    print("The verifier flagged every broken copy and passed the healthy app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
