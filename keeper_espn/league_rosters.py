from __future__ import annotations

from typing import Any

from .client import EspnPull
from .pipeline import _player_from_entry, _team_name


def build_league_rosters(pull: EspnPull, *, week: int) -> dict[str, Any]:
    teams = []
    for team in pull.league.get("teams") or []:
        entries = ((team.get("roster") or {}).get("entries") or [])
        players = [_player_from_entry(entry, week=week) for entry in entries]
        teams.append({
            "team_id": int(team["id"]),
            "team_name": _team_name(team),
            "roster_count": len(players),
            "players": players,
        })
    return {"week": week, "team_count": len(teams), "teams": teams}


def validate_league_rosters(state: dict[str, Any], league_state: dict[str, Any], roster_state: dict[str, Any]) -> list[str]:
    errors = []
    teams = state["teams"]
    if len(teams) != league_state["number_of_teams"]:
        errors.append("League roster team count does not match league state")
    ids = [team["team_id"] for team in teams]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate team ID in league rosters")
    owners = {}
    for team in teams:
        if not team["players"]:
            errors.append(f"Team {team['team_id']} has no roster players")
        for player in team["players"]:
            pid = player.get("player_id")
            if not pid or pid == "None":
                errors.append(f"Team {team['team_id']} has a player without an ID")
            elif pid in owners:
                errors.append(f"Player {pid} appears on multiple league rosters")
            else:
                owners[pid] = team["team_id"]
    own = next((team for team in teams if team["team_id"] == roster_state["team_id"]), None)
    if own is None or {p["player_id"] for p in own["players"]} != {p["player_id"] for p in roster_state["players"]}:
        errors.append("Taylor Made league roster does not match roster_state")
    return errors


def league_roster_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {"baseline_status": "FIRST_LEAGUE_ROSTER_SNAPSHOT", "changes": [], "change_count": 0}
    def index(state: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
        return {p["player_id"]: (team, p) for team in state["teams"] for p in team["players"]}
    old, new = index(previous), index(current)
    changes = []
    for pid in sorted(old.keys() | new.keys()):
        before, after = old.get(pid), new.get(pid)
        old_id = before[0]["team_id"] if before else None
        new_id = after[0]["team_id"] if after else None
        if old_id == new_id:
            continue  # Lineup and injury changes are available in the full snapshot.
        player = (after or before)[1]
        changes.append({
            "player_id": pid, "player_name": player["player_name"],
            "position": player["position"],
            "from_team_id": old_id, "from_team_name": before[0]["team_name"] if before else None,
            "to_team_id": new_id, "to_team_name": after[0]["team_name"] if after else None,
        })
    return {"baseline_status": "COMPARED_TO_PREVIOUS_LEAGUE_ROSTERS", "changes": changes, "change_count": len(changes)}
