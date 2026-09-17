from __future__ import annotations

from typing import Any

from .client import EspnPull

POSITION_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}


def _draft_round_map(league: dict[str, Any]) -> dict[str, int]:
    detail = league.get("draftDetail") or {}
    picks = detail.get("picks") or []
    result: dict[str, int] = {}
    for pick in picks:
        player_id = pick.get("playerId")
        round_id = pick.get("roundId")
        if player_id is None or round_id is None:
            continue
        result[str(player_id)] = int(round_id)
    return result


def enrich_roster_keeper_fields(roster_state: dict[str, Any], pull: EspnPull) -> dict[str, Any]:
    rounds = _draft_round_map(pull.league)
    for player in roster_state.get("players", []):
        player_id = str(player.get("player_id"))
        if player_id in rounds:
            player["keeper_round"] = rounds[player_id]
            player["keeper_origin"] = "DRAFTED"
        else:
            player["keeper_round"] = 17
            player["keeper_origin"] = "UNDRAFTED_FA"
    return roster_state


def build_keeper_state(roster_state: dict[str, Any], pull: EspnPull) -> dict[str, Any]:
    detail = pull.league.get("draftDetail") or {}
    players = []
    for player in roster_state.get("players", []):
        players.append(
            {
                "player_id": player.get("player_id"),
                "player_name": player.get("player_name"),
                "position": player.get("position"),
                "roster_status": player.get("roster_status"),
                "keeper_round": player.get("keeper_round"),
                "keeper_origin": player.get("keeper_origin"),
            }
        )
    return {
        "season": pull.league.get("seasonId"),
        "draft_completed": bool(detail.get("drafted")),
        "keeper_rule": "MOST_RECENT_DRAFT_ROUND_ELSE_R17",
        "max_keepers": 2,
        "players": players,
    }


def _extract_pool_player(entry: dict[str, Any]) -> dict[str, Any] | None:
    player = entry.get("player") or (entry.get("playerPoolEntry") or {}).get("player") or {}
    if not player and entry.get("fullName"):
        player = entry
    player_id = player.get("id") or entry.get("id")
    if player_id is None:
        return None

    position_id = player.get("defaultPositionId")
    ownership = player.get("ownership") or {}
    status = entry.get("status") or (entry.get("playerPoolEntry") or {}).get("status") or "AVAILABLE"
    return {
        "player_id": str(player_id),
        "player_name": player.get("fullName") or player.get("name") or "Unknown Player",
        "position": POSITION_NAMES.get(position_id, str(position_id) if position_id is not None else "UNKNOWN"),
        "nfl_team_id": player.get("proTeamId"),
        "injury_status": player.get("injuryStatus") or "UNKNOWN",
        "availability_status": status,
        "percent_owned": ownership.get("percentOwned"),
        "percent_started": ownership.get("percentStarted"),
        "keeper_round_if_added": 17,
        "keeper_origin_if_added": "UNDRAFTED_FA",
    }


def build_player_pool_state(pull: EspnPull, *, week: int) -> dict[str, Any]:
    players = []
    seen: set[str] = set()
    for entry in pull.available_players:
        normalized = _extract_pool_player(entry)
        if not normalized:
            continue
        player_id = normalized["player_id"]
        if player_id in seen:
            continue
        seen.add(player_id)
        players.append(normalized)

    return {
        "week": week,
        "source": pull.player_pool_source,
        "selection_rule": "ESPN FREEAGENT/WAIVERS, capped at 250 and sorted by percent owned",
        "player_count": len(players),
        "players": players,
    }
