from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from .config import LeagueConfig


@dataclass
class CheckResult:
    check: str
    status: str
    detail: str


def reconcile_snapshot(
    league: dict[str, Any],
    rosters: list[dict[str, Any]],
    state: pd.DataFrame,
    config: LeagueConfig,
    matchups: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    checks: list[CheckResult] = []

    def add(name: str, ok: bool, detail: str):
        checks.append(CheckResult(name, "PASS" if ok else "FAIL", detail))

    add("league_id", str(league.get("league_id")) == config.league_id, str(league.get("league_id")))
    add("team_count", len(rosters) == config.expected_teams, f"found={len(rosters)} expected={config.expected_teams}")

    duplicate_owners = state.groupby("sleeper_player_id")["roster_id"].nunique()
    dupes = duplicate_owners[duplicate_owners > 1]
    add("unique_player_ownership", dupes.empty, f"duplicate_player_ids={len(dupes)}")

    unresolved = state[state["player_name"].astype(str) == state["sleeper_player_id"].astype(str)]
    add("player_id_resolution", unresolved.empty, f"unresolved={len(unresolved)}")

    roster_size_failures = []
    starter_failures = []
    starter_membership_failures = []
    for r in rosters:
        rid = int(r["roster_id"])
        players = {str(x) for x in (r.get("players") or []) if x is not None}
        starters = [str(x) for x in (r.get("starters") or []) if x is not None and str(x) != "0"]
        if len(players) != config.expected_roster_size:
            roster_size_failures.append((rid, len(players)))
        # During migration, allow explicit empty starter placeholders; otherwise require 11 IDs.
        if len(starters) > config.expected_starters:
            starter_failures.append((rid, len(starters)))
        missing = [p for p in starters if p not in players]
        if missing:
            starter_membership_failures.append((rid, missing))

    add("roster_size", not roster_size_failures, f"exceptions={roster_size_failures}")
    add("starter_count_upper_bound", not starter_failures, f"exceptions={starter_failures}")
    add("starters_owned_by_roster", not starter_membership_failures, f"exceptions={starter_membership_failures}")

    if matchups is not None:
        roster_ids = [int(m["roster_id"]) for m in matchups]
        add("matchup_roster_coverage", sorted(roster_ids) == sorted(int(r["roster_id"]) for r in rosters), f"matchup_rows={len(roster_ids)}")
        groups: dict[Any, list[int]] = {}
        for m in matchups:
            mid = m.get("matchup_id")
            groups.setdefault(mid, []).append(int(m["roster_id"]))
        bad = {mid: rids for mid, rids in groups.items() if mid is not None and len(rids) != 2}
        add("matchup_pairing", not bad, f"bad_matchups={bad}")

    return pd.DataFrame([asdict(c) for c in checks])


def critical_failures(report: pd.DataFrame) -> list[str]:
    return report.loc[report["status"] == "FAIL", "check"].tolist()


def roster_delta(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """Return ownership changes by sleeper_player_id for reconciliation against transactions."""
    if previous.empty or current.empty:
        return pd.DataFrame(columns=["sleeper_player_id", "previous_roster_id", "current_roster_id"])
    p = previous[["sleeper_player_id", "roster_id"]].drop_duplicates().rename(columns={"roster_id": "previous_roster_id"})
    c = current[["sleeper_player_id", "roster_id"]].drop_duplicates().rename(columns={"roster_id": "current_roster_id"})
    merged = p.merge(c, on="sleeper_player_id", how="outer")
    return merged[merged["previous_roster_id"] != merged["current_roster_id"]].reset_index(drop=True)
