from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


def _team_name(user: dict[str, Any] | None, roster_id: int) -> str:
    if not user:
        return f"Roster {roster_id}"
    metadata = user.get("metadata") or {}
    return metadata.get("team_name") or user.get("display_name") or user.get("username") or f"Roster {roster_id}"


def normalize_league_state(
    league: dict[str, Any],
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
    matchups: list[dict[str, Any]] | None = None,
    week: int | None = None,
) -> pd.DataFrame:
    """Create one normalized player-per-roster snapshot.

    Stable keys are league_id, roster_id, sleeper_player_id. Starter/bench state is
    derived from Sleeper's starter list. Matchup identity is attached by roster_id.
    """
    users_by_id = {str(u.get("user_id")): u for u in users}
    matchups_by_roster = {int(m["roster_id"]): m for m in (matchups or [])}
    rows: list[dict[str, Any]] = []
    pulled_at = datetime.now(timezone.utc).isoformat()

    for roster in rosters:
        roster_id = int(roster["roster_id"])
        owner_id = roster.get("owner_id")
        user = users_by_id.get(str(owner_id)) if owner_id is not None else None
        starters = [str(x) for x in (roster.get("starters") or []) if x is not None]
        starter_set = set(starters)
        roster_players = [str(x) for x in (roster.get("players") or []) if x is not None]
        matchup = matchups_by_roster.get(roster_id, {})
        matchup_id = matchup.get("matchup_id")

        for player_id in roster_players:
            p = players.get(player_id, {})
            rows.append({
                "pulled_at_utc": pulled_at,
                "league_id": str(league.get("league_id")),
                "season": str(league.get("season", "")),
                "week": week,
                "roster_id": roster_id,
                "owner_id": owner_id,
                "team_name": _team_name(user, roster_id),
                "sleeper_player_id": player_id,
                "player_name": p.get("full_name") or p.get("first_name") or player_id,
                "position": p.get("fantasy_positions", [None])[0] if p.get("fantasy_positions") else p.get("position"),
                "fantasy_positions": ",".join(p.get("fantasy_positions") or []),
                "nfl_team": p.get("team"),
                "status": p.get("status"),
                "injury_status": p.get("injury_status"),
                "injury_body_part": p.get("injury_body_part"),
                "depth_chart_position": p.get("depth_chart_position"),
                "depth_chart_order": p.get("depth_chart_order"),
                "espn_id": p.get("espn_id"),
                "sportradar_id": p.get("sportradar_id"),
                "is_starter": player_id in starter_set,
                "starter_order": starters.index(player_id) if player_id in starter_set else None,
                "matchup_id": matchup_id,
                "matchup_points": matchup.get("points"),
                "waiver_position": (roster.get("settings") or {}).get("waiver_position"),
                "wins": (roster.get("settings") or {}).get("wins"),
                "losses": (roster.get("settings") or {}).get("losses"),
                "ties": (roster.get("settings") or {}).get("ties"),
            })

    return pd.DataFrame(rows)


def normalize_transactions(transactions: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize Sleeper transactions to one action per row.

    One transaction can generate multiple add/drop/pick rows but retains a stable
    transaction_id for deduplication and auditability.
    """
    rows: list[dict[str, Any]] = []
    for tx in transactions:
        base = {
            "transaction_id": str(tx.get("transaction_id")),
            "type": tx.get("type"),
            "status": tx.get("status"),
            "created_ms": tx.get("created"),
            "status_updated_ms": tx.get("status_updated"),
            "week": tx.get("leg"),
            "creator_user_id": tx.get("creator"),
            "roster_ids": ",".join(str(x) for x in (tx.get("roster_ids") or [])),
            "waiver_bid": (tx.get("settings") or {}).get("waiver_bid"),
        }
        for player_id, roster_id in (tx.get("adds") or {}).items():
            rows.append({**base, "action": "ADD", "player_id": str(player_id), "roster_id": int(roster_id), "pick_season": None, "pick_round": None, "pick_original_roster_id": None, "pick_previous_owner_id": None, "pick_owner_id": None})
        for player_id, roster_id in (tx.get("drops") or {}).items():
            rows.append({**base, "action": "DROP", "player_id": str(player_id), "roster_id": int(roster_id), "pick_season": None, "pick_round": None, "pick_original_roster_id": None, "pick_previous_owner_id": None, "pick_owner_id": None})
        for pick in (tx.get("draft_picks") or []):
            rows.append({**base, "action": "PICK_TRADE", "player_id": None, "roster_id": None, "pick_season": pick.get("season"), "pick_round": pick.get("round"), "pick_original_roster_id": pick.get("roster_id"), "pick_previous_owner_id": pick.get("previous_owner_id"), "pick_owner_id": pick.get("owner_id")})
        if not (tx.get("adds") or tx.get("drops") or tx.get("draft_picks")):
            rows.append({**base, "action": "TRANSACTION", "player_id": None, "roster_id": None, "pick_season": None, "pick_round": None, "pick_original_roster_id": None, "pick_previous_owner_id": None, "pick_owner_id": None})
    return pd.DataFrame(rows)
