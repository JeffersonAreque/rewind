"""Command line entry point: `python -m rewind <command>`."""

import argparse
import json
import sys
from pathlib import Path

from rewind.config import PROVIDERS, ROOT, load_provider


def cmd_config(args: argparse.Namespace) -> int:
    provider = load_provider(args.provider)
    print(f"Provider : {provider.name}")
    print(f"Base URL : {provider.base_url}")
    print(f"Model    : {provider.model or '(not set)'}")
    print(f"API key  : {'set' if provider.api_key else '(not set)'}")
    missing = provider.missing()
    if missing:
        print(f"\nMissing in .env: {', '.join(missing)}")
        return 1
    return 0


def cmd_screens(args: argparse.Namespace) -> int:
    from rewind.agents import screens
    from rewind.keyframes import Settings

    if not args.video.is_file():
        raise ValueError(f"video not found: {args.video}")
    settings = Settings(still_seconds=args.still, min_change=args.min_change)
    run_dir = screens.run(
        args.video, args.out, args.provider, args.dry_run, args.workers, args.max_frames, settings
    )
    if args.dry_run:
        return 0

    report = json.loads((run_dir / "screens.json").read_text(encoding="utf-8"))
    print(f"\n{len(report['screens'])} screens:")
    for screen in report["screens"]:
        fields = sum(len(s["fields"]) for s in screen["sections"])
        print(f"  {screen['name']:<28} {fields:2d} fields  {len(screen['buttons']):2d} buttons  "
              f"{len(screen['dialogs'])} dialogs  ({len(screen['frames'])} frames)")
    print("Navigation: " + " -> ".join(step["screen"] for step in report["navigation"]))
    tokens = report["tokens"]
    print(f"Tokens this run: {tokens['total']} ({tokens['reasoning']} reasoning, {tokens['calls']} calls); "
          f"{report['cached_frames']} frames from cache")
    if report["failed_frames"]:
        print(f"Failed frames: {report['failed_frames']}")
    print(f"\n{run_dir / 'screens.json'}")
    return 1 if report["failed_frames"] else 0


def cmd_flows(args: argparse.Namespace) -> int:
    from rewind.agents import flows

    if not (args.run / "screens.json").is_file():
        raise ValueError(f"{args.run} has no screens.json; run `python -m rewind screens VIDEO` first")
    path = flows.run(args.run, args.provider, args.workers)

    report = json.loads(path.read_text(encoding="utf-8"))
    steps = {s["n"]: s for s in report["steps"]}
    print(f"\n{len(steps)} steps, {len(report['workflows'])} workflows:")
    for wf in report["workflows"]:
        print(f"  {wf['name']:<28} steps {min(wf['steps'])}-{max(wf['steps'])}")
    print(f"{len(report['rules'])} rules:")
    for rule in report["rules"]:
        code = f" [{rule['code']}]" if rule["code"] else ""
        print(f"  {rule['kind']:<15} {rule['description']}{code}")
    for n in report["low_confidence"]:
        s = steps[n]
        print(f"Check step {n}: {s['target']} = {s['value']!r} (low confidence; also read as {s['variants']})")
    tokens = report["tokens"]
    print(f"Tokens this run: {tokens['total']} ({tokens['reasoning']} reasoning, {tokens['calls']} calls); "
          f"{report['cached_calls']} calls from cache")
    if report["failed_pairs"]:
        print(f"Failed pairs: {report['failed_pairs']}")
    print(f"\n{path}")
    return 1 if report["failed_pairs"] else 0


def cmd_build(args: argparse.Namespace) -> int:
    from rewind.agents import builder

    for name in ("screens.json", "flows.json"):
        if not (args.run / name).is_file():
            raise ValueError(f"{args.run} has no {name}; run the screens and flows commands first")
    app_dir = builder.run(args.run, args.provider, not args.no_test, args.workers)

    report = json.loads((app_dir / "build.json").read_text(encoding="utf-8"))
    print(f"\nLegacy elements kept:")
    for screen, c in report["coverage"].items():
        print(f"  {screen:<28} {c['kept']}/{c['legacy']} kept, {c['removed']} removed on purpose")
    print(f"Modernization changes: {report['changes']} (see {app_dir / 'CHANGES.md'})")
    for problem in report["unclaimed_rules"]:
        print(f"  note: {problem}; the replay tests still check it")
    absorbed = report["absorbed_steps"]
    if report["tests"]:
        passed = [t for t in report["tests"] if t["passed"]]
        print(f"Workflow replay: {len(passed)}/{len(report['tests'])} workflows passed "
              f"({report['steps']} recorded steps, {len(absorbed)} absorbed by changes: {absorbed})")
        for t in report["tests"]:
            print(f"  [{'ok' if t['passed'] else '--'}] {t['name']}" + (f": {t['error']}" if t["error"] else ""))
    tokens = report["tokens"]
    print(f"Tokens this run: {tokens['total']} ({tokens['reasoning']} reasoning, {tokens['calls']} calls); "
          f"{report['cached_calls']} calls from cache")
    print(f"\nRun the new app:  uv run python {app_dir / 'app.py'}")
    return 0 if all(t["passed"] for t in report["tests"]) else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="rewind", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config", help="show the active provider (no API calls)")
    config.add_argument("--provider", choices=PROVIDERS, help="override REWIND_PROVIDER")
    config.set_defaults(func=cmd_config)

    screens = commands.add_parser("screens", help="screens agent: find every screen of a video")
    screens.add_argument("video", type=Path, help="screen recording (MP4)")
    screens.add_argument("--out", type=Path, default=ROOT / "runs", help="folder for the run (default runs/)")
    screens.add_argument("--provider", choices=PROVIDERS, help="override REWIND_PROVIDER")
    screens.add_argument("--dry-run", action="store_true", help="only extract keyframes, no model calls")
    screens.add_argument("--workers", type=int, default=4, help="model calls in parallel (default 4)")
    screens.add_argument("--max-frames", type=int, help="analyse at most this many keyframes")
    screens.add_argument("--still", type=float, default=0.4, help="seconds the screen must stay still (default 0.4)")
    screens.add_argument("--min-change", type=int, default=60,
                         help="changed pixels at 320x180 to count as a new keyframe (default 60)")
    screens.set_defaults(func=cmd_screens)

    flows = commands.add_parser("flows", help="flows agent: steps, workflows and rules of a screens run")
    flows.add_argument("run", type=Path, help="run folder made by the screens command (runs/<id>)")
    flows.add_argument("--provider", choices=PROVIDERS, help="override REWIND_PROVIDER")
    flows.add_argument("--workers", type=int, default=4, help="model calls in parallel (default 4)")
    flows.set_defaults(func=cmd_flows)

    build = commands.add_parser("build", help="builder agent: generate the modern app and replay the workflows")
    build.add_argument("run", type=Path, help="run folder with screens.json and flows.json (runs/<id>)")
    build.add_argument("--provider", choices=PROVIDERS, help="override REWIND_PROVIDER")
    build.add_argument("--workers", type=int, default=4, help="model calls in parallel (default 4)")
    build.add_argument("--no-test", action="store_true", help="do not run the replay tests after building")
    build.set_defaults(func=cmd_build)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
