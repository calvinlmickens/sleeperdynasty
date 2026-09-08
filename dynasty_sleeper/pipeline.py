from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .client import SleeperClient
from .config import DEFAULT_CONFIG, LeagueConfig
from .normalize import normalize_league_state, normalize_transactions
from .reconcile import reconcile_snapshot, critical_failures, reconcile_roster_delta


def merge_transaction_master(master_path: Path, current: pd.DataFrame) -> pd.DataFrame:
    if master_path.exists() and master_path.stat().st_size > 0:
        old = pd.read_csv(master_path, dtype={"transaction_id": str, "player_id": str})
        combined = pd.concat([old, current], ignore_index=True)
    else:
        combined = current.copy()
    if combined.empty:
        return combined
    keys = ["transaction_id", "action", "player_id", "roster_id", "pick_season", "pick_round", "pick_original_roster_id", "pick_owner_id"]
    return combined.drop_duplicates(subset=[k for k in keys if k in combined.columns], keep="last")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _roster_summary(state: pd.DataFrame) -> pd.DataFrame:
    if state.empty:
        return pd.DataFrame()
    return (
        state.groupby(["league_id", "roster_id", "owner_id", "team_name", "waiver_position"], dropna=False)
        .agg(rostered_players=("sleeper_player_id", "nunique"), starters=("is_starter", "sum"), matchup_id=("matchup_id", "first"))
        .reset_index()
        .sort_values("roster_id")
    )


def _find_target_roster(summary: pd.DataFrame, target_team_name: str) -> int | None:
    if summary.empty or not target_team_name:
        return None
    exact = summary[summary["team_name"].astype(str).str.casefold() == target_team_name.casefold()]
    if len(exact) == 1:
        return int(exact.iloc[0]["roster_id"])
    return None


def run_refresh(
    week: int,
    output_dir: str | Path = "output",
    config: LeagueConfig = DEFAULT_CONFIG,
    fixture_dir: str | Path | None = None,
    pull_players: bool = True,
    previous_state: str | Path | None = None,
) -> dict[str, Path | list[str] | int | None]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = SleeperClient(fixture_dir=fixture_dir)
    pulled_at = datetime.now(timezone.utc).isoformat()

    league = client.league(config.league_id)
    users = client.users(config.league_id)
    rosters = client.rosters(config.league_id)
    matchups = client.matchups(config.league_id, week)
    traded_picks = client.traded_picks(config.league_id)
    players = client.players_nfl() if pull_players else {}

    # Pull season-to-date transaction rounds so a baseline snapshot can be reconciled
    # against any completed move observed in current ownership.
    tx_by_week: dict[int, list[dict[str, Any]]] = {}
    for tx_week in range(1, week + 1):
        tx_by_week[tx_week] = client.transactions(config.league_id, tx_week)

    _write_json(raw_dir / "league.json", league)
    _write_json(raw_dir / "users.json", users)
    _write_json(raw_dir / "rosters.json", rosters)
    _write_json(raw_dir / f"matchups_week_{week}.json", matchups)
    _write_json(raw_dir / "traded_picks.json", traded_picks)
    _write_json(raw_dir / "players_nfl.json", players)
    for tx_week, payload in tx_by_week.items():
        _write_json(raw_dir / f"transactions_week_{tx_week}.json", payload)

    state = normalize_league_state(league, users, rosters, players, matchups, week)
    tx_frames = [normalize_transactions(payload) for payload in tx_by_week.values()]
    tx_current = pd.concat(tx_frames, ignore_index=True) if tx_frames else pd.DataFrame()
    report = reconcile_snapshot(league, rosters, state, config, matchups)
    failures = critical_failures(report)
    summary = _roster_summary(state)
    target_roster_id = _find_target_roster(summary, config.target_team_name)

    state_path = output_dir / "league_state_current.csv"
    summary_path = output_dir / "roster_summary_current.csv"
    tx_current_path = output_dir / "league_transactions_current.csv"
    tx_master_path = output_dir / "league_transactions_master.csv"
    report_path = output_dir / "reconciliation_report.csv"
    picks_path = output_dir / "traded_picks_current.csv"
    delta_path = output_dir / "roster_delta_reconciliation.csv"
    manifest_path = output_dir / "manifest.json"

    state.to_csv(state_path, index=False)
    summary.to_csv(summary_path, index=False)
    tx_current.to_csv(tx_current_path, index=False)
    master = merge_transaction_master(tx_master_path, tx_current)
    master.to_csv(tx_master_path, index=False)
    report.to_csv(report_path, index=False)
    pd.DataFrame(traded_picks).to_csv(picks_path, index=False)

    baseline_status = "ESTABLISHED_THIS_RUN"
    delta_failures: list[str] = []
    if previous_state:
        previous_path = Path(previous_state)
        if previous_path.exists() and previous_path.stat().st_size > 0:
            previous = pd.read_csv(previous_path, dtype={"sleeper_player_id": str})
            delta_report = reconcile_roster_delta(previous, state, tx_current)
            baseline_status = "COMPARED_TO_PREVIOUS"
            if not delta_report.empty:
                delta_failures = delta_report.loc[delta_report["status"] == "FAIL", "sleeper_player_id"].astype(str).tolist()
        else:
            delta_report = pd.DataFrame([{"status": "BASELINE", "detail": "previous state path not found; current snapshot establishes baseline"}])
    else:
        delta_report = pd.DataFrame([{"status": "BASELINE", "detail": "no previous state supplied; current snapshot establishes baseline"}])
    delta_report.to_csv(delta_path, index=False)

    overall_status = "PASS" if not failures and not delta_failures else "FAIL"
    manifest = {
        "schema_version": "0.3",
        "generated_at_utc": pulled_at,
        "overall_status": overall_status,
        "league_id": config.league_id,
        "league_name_expected": config.league_name,
        "league_name_resolved": league.get("name"),
        "season": league.get("season"),
        "week": week,
        "target_team_name": config.target_team_name,
        "target_roster_id": target_roster_id,
        "target_roster_resolution": "PASS" if target_roster_id is not None else "REVIEW_REQUIRED",
        "baseline_status": baseline_status,
        "counts": {
            "users": len(users),
            "rosters": len(rosters),
            "rostered_player_rows": int(len(state)),
            "unique_rostered_players": int(state["sleeper_player_id"].nunique()) if not state.empty else 0,
            "matchup_rows": len(matchups),
            "transaction_action_rows": int(len(tx_current)),
            "traded_picks": len(traded_picks),
            "player_dictionary": len(players),
        },
        "critical_reconciliation_failures": failures,
        "unreconciled_roster_delta_player_ids": delta_failures,
        "files": {},
    }

    for p in [state_path, summary_path, tx_current_path, tx_master_path, report_path, picks_path, delta_path]:
        manifest["files"][p.name] = {"sha256": _sha256(p), "bytes": p.stat().st_size}
    _write_json(manifest_path, manifest)

    return {
        "league_state": state_path,
        "roster_summary": summary_path,
        "transactions_current": tx_current_path,
        "transactions_master": tx_master_path,
        "traded_picks": picks_path,
        "reconciliation": report_path,
        "roster_delta_reconciliation": delta_path,
        "manifest": manifest_path,
        "target_roster_id": target_roster_id,
        "critical_failures": failures + (["UNRECONCILED_ROSTER_DELTA"] if delta_failures else []),
    }
