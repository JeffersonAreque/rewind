"""Command line entry point: `python -m rewind <command>`."""

import argparse
import sys

from rewind.config import PROVIDERS, load_provider


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="rewind", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config", help="show the active provider (no API calls)")
    config.add_argument("--provider", choices=PROVIDERS, help="override REWIND_PROVIDER")
    config.set_defaults(func=cmd_config)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
