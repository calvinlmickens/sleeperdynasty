from __future__ import annotations

import argparse
import json

from .health import run_healthcheck
from .pipeline import run_refresh


def main():
    p = argparse.ArgumentParser(description="Refresh Sleeper dynasty league state")
    p.add_argument("--week", type=int, required=False)
    p.add_argument("--output-dir", default="output")
    p.add_argument("--fixture-dir", default=None, help="Read local JSON fixtures instead of calling Sleeper")
    p.add_argument("--healthcheck", action="store_true", help="Test live Sleeper connectivity and league resolution only")
    p.add_argument("--skip-players-healthcheck", action="store_true", help="Skip the large /players/nfl endpoint during healthcheck")
    args = p.parse_args()

    if args.healthcheck:
        result = run_healthcheck(
            week=args.week,
            fixture_dir=args.fixture_dir,
            pull_players=not args.skip_players_healthcheck,
        )
        print(json.dumps(result.to_dict(), indent=2))
        if result.status != "PASS":
            raise SystemExit(2)
        return

    if args.week is None:
        p.error("--week is required unless --healthcheck is used")

    result = run_refresh(week=args.week, output_dir=args.output_dir, fixture_dir=args.fixture_dir)
    printable = {k: str(v) if not isinstance(v, list) else v for k, v in result.items()}
    print(json.dumps(printable, indent=2))
    if result["critical_failures"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
