"""Recover the matching week's pregame context without using a later week's Drive state."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import zipfile

import pandas as pd
import requests

LEAGUE_ID = "1402840136382742528"
TARGET_ROSTER_ID = 10


def valid_context(data: bytes, week: int) -> bool:
    try:
        df = pd.read_csv(io.BytesIO(data), dtype={"sleeper_player_id": str})
        if df.empty or not {"week", "league_id", "roster_id", "sleeper_player_id", "is_starter"}.issubset(df.columns):
            return False
        weeks = set(pd.to_numeric(df["week"], errors="coerce").dropna().astype(int))
        leagues = set(df["league_id"].astype(str))
        rosters = set(pd.to_numeric(df["roster_id"], errors="coerce").dropna().astype(int))
        return weeks == {week} and leagues == {LEAGUE_ID} and TARGET_ROSTER_ID in rosters and len(rosters) >= 2
    except (ValueError, KeyError, pd.errors.ParserError):
        return False


def recover(week: int, baseline: Path, destination: Path, repository: str, token: str) -> dict:
    current = baseline / "weekly_matchup_context.csv"
    if current.is_file() and valid_context(current.read_bytes(), week):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(current.read_bytes())
        return {"source": "current_drive_baseline", "week": week}

    if not repository or not token:
        raise RuntimeError("Missing GitHub repository or token for historical context recovery")

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    base = f"https://api.github.com/repos/{repository}"
    name = f"sleeper-refresh-week-{week}"
    checked = 0

    for page in range(1, 11):
        response = requests.get(f"{base}/actions/artifacts",
                                params={"per_page": 100, "page": page}, headers=headers, timeout=30)
        response.raise_for_status()
        artifacts = response.json().get("artifacts", [])
        if not artifacts:
            break
        for artifact in artifacts:
            if artifact.get("name") != name or artifact.get("expired"):
                continue
            run_id = (artifact.get("workflow_run") or {}).get("id")
            if not run_id:
                continue
            run = requests.get(f"{base}/actions/runs/{run_id}", headers=headers, timeout=30)
            run.raise_for_status()
            run_info = run.json()
            if run_info.get("conclusion") != "success" or not str(run_info.get("path", "")).endswith("dynasty-sleeper-refresh-v0_7.yml"):
                continue
            checked += 1
            archive_response = requests.get(artifact["archive_download_url"], headers=headers, timeout=90)
            archive_response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
                matches = [n for n in archive.namelist() if n.split("/")[-1] == "weekly_matchup_context.csv"]
                for member in matches:
                    content = archive.read(member)
                    if valid_context(content, week):
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(content)
                        return {"source": "successful_github_artifact", "week": week,
                                "run_id": run_id, "artifact_id": artifact["id"]}
        if len(artifacts) < 100:
            break
    raise RuntimeError(
        f"No validated Week {week} pregame context found in Drive or successful retained "
        f"GitHub artifacts (examined {checked} successful candidates); finalization safely halted."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", required=True, type=int)
    parser.add_argument("--baseline", default="baseline")
    parser.add_argument("--output", default="archived-context/pregame_context.csv")
    args = parser.parse_args()
    info = recover(args.week, Path(args.baseline), Path(args.output),
                   os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
    print("Validated pregame context recovery: " + json.dumps(info, sort_keys=True))


if __name__ == "__main__":
    main()
