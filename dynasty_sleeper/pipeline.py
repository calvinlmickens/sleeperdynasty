from __future__ import annotations

from pathlib import Path

import pandas as pd

from .client import SleeperClient
from .config import DEFAULT_CONFIG, LeagueConfig
from .normalize import normalize_league_state, normalize_transactions
from .reconcile import reconcile_snapshot, critical_failures


def merge_transaction_master(master_path: Path, current: pd.DataFrame) -> pd.DataFrame:
    if master_path.exists():
        old = pd.read_csv(master_path, dtype={"transaction_id": str, "player_id": str})
        combined = pd.concat([old, current], ignore_index=True)
    else:
        combined = current.copy()
    if combined.empty:
        return combined
    # One transaction can have multiple action rows; action/player/pick fields form row identity.
    keys = ["transaction_id", "action", "player_id", "roster_id", "pick_season", "pick_round", "pick_original_roster_id", "pick_owner_id"]
    return combined.drop_duplicates(subset=[k for k in keys if k in combined.columns], keep="last")


def run_refresh(
    week: int,
    output_dir: str | Path = "output",
    config: LeagueConfig = DEFAULT_CONFIG,
    fixture_dir: str | Path | None = None,
    pull_players: bool = True,
) -> dict[str, Path | list[str]]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = SleeperClient(fixture_dir=fixture_dir)

    league = client.league(config.league_id)
    users = client.users(config.league_id)
    rosters = client.rosters(config.league_id)
    matchups = client.matchups(config.league_id, week)
    transactions = client.transactions(config.league_id, week)
    traded_picks = client.traded_picks(config.league_id)
    players = client.players_nfl() if pull_players else {}

    state = normalize_league_state(league, users, rosters, players, matchups, week)
    tx_current = normalize_transactions(transactions)
    report = reconcile_snapshot(league, rosters, state, config, matchups)
    failures = critical_failures(report)

    state_path = output_dir / "league_state_current.csv"
    tx_current_path = output_dir / "league_transactions_current.csv"
    tx_master_path = output_dir / "league_transactions_master.csv"
    report_path = output_dir / "reconciliation_report.csv"
    picks_path = output_dir / "traded_picks_current.csv"

    state.to_csv(state_path, index=False)
    tx_current.to_csv(tx_current_path, index=False)
    master = merge_transaction_master(tx_master_path, tx_current)
    master.to_csv(tx_master_path, index=False)
    report.to_csv(report_path, index=False)
    pd.DataFrame(traded_picks).to_csv(picks_path, index=False)

    return {
        "league_state": state_path,
        "transactions_current": tx_current_path,
        "transactions_master": tx_master_path,
        "traded_picks": picks_path,
        "reconciliation": report_path,
        "critical_failures": failures,
    }
