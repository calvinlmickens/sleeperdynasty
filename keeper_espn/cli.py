from __future__ import annotations

import argparse
import json

from .pipeline_phase3 import run_refresh


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Keeper League Advisor ESPN state")
    parser.add_argument("--output-dir", default="keeper_output")
    parser.add_argument("--fixture-dir", default=None, help="Read fixture league.json instead of ESPN")
    parser.add_argument("--season", type=int, default=None, help="Override ESPN_SEASON")
    args = parser.parse_args()

    result = run_refresh(
        output_dir=args.output_dir,
        fixture_dir=args.fixture_dir,
        season=args.season,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
