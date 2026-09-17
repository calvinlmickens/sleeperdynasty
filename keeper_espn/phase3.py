from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object")
    return data


def load_validated_snapshot(snapshot_dir: str | Path) -> dict[str, dict[str, Any]] | None:
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = _read_json(manifest_path)
    if manifest.get("validation_status") != "PASS":
        return None

    required = (
        "league_state.json",
        "roster_state.json",
        "matchup_state.json",
        "keeper_state.json",
        "player_pool.json",
    )
    if any(not (snapshot_dir / name).exists() for name in required):
        return None

    return {
        "manifest": manifest,
        "league_state": _read_json(snapshot_dir / "league_state.json"),
        "roster_state": _read_json(snapshot_dir / "roster_state.json"),
        "matchup_state": _read_json(snapshot_dir / "matchup_state.json"),
        "keeper_state": _read_json(snapshot_dir / "keeper_state.json"),
        "player_pool": _read_json(snapshot_dir / "player_pool.json"),
    }


def _player_index(state: dict[str, Any], key: str = "players") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for player in state.get(key) or []:
        player_id = player.get("player_id")
        if player_id is not None:
            result[str(player_id)] = player
    return result


def _player_brief(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": player.get("player_id"),
        "player_name": player.get("player_name"),
        "position": player.get("position"),
        "roster_status": player.get("roster_status"),
        "fantasy_slot": player.get("fantasy_slot"),
        "injury_status": player.get("injury_status"),
        "keeper_round": player.get("keeper_round"),
        "keeper_origin": player.get("keeper_origin"),
    }


def _field_changes(
    before: dict[str, Any],
    after: dict[str, Any],
    fields: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field in fields:
        old = before.get(field)
        new = after.get(field)
        if old != new:
            changes[field] = {"before": old, "after": new}
    return changes


def _team_standing(league_state: dict[str, Any], team_id: int) -> dict[str, Any] | None:
    for row in league_state.get("standings") or []:
        if int(row.get("team_id", -1)) == int(team_id):
            return row
    return None


def build_delta_state(
    *,
    current_run_id: str,
    team_id: int,
    league_state: dict[str, Any],
    roster_state: dict[str, Any],
    matchup_state: dict[str, Any],
    keeper_state: dict[str, Any],
    player_pool_state: dict[str, Any],
    previous: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if previous is None:
        return {
            "baseline_status": "FIRST_VALIDATED_RUN",
            "current_run_id": current_run_id,
            "previous_validated_run_id": None,
            "week_before": None,
            "week_after": league_state.get("current_week"),
            "material_change": False,
            "summary": {
                "roster_adds": 0,
                "roster_drops": 0,
                "roster_player_changes": 0,
                "keeper_changes": 0,
                "player_pool_additions": 0,
                "player_pool_removals": 0,
            },
            "roster": {"added": [], "removed": [], "changed": []},
            "keeper": {"changed": []},
            "league": {},
            "matchup": {},
            "player_pool": {"added": [], "removed": [], "added_count": 0, "removed_count": 0},
        }

    prev_manifest = previous["manifest"]
    prev_league = previous["league_state"]
    prev_roster = previous["roster_state"]
    prev_matchup = previous["matchup_state"]
    prev_keeper = previous["keeper_state"]
    prev_pool = previous["player_pool"]

    old_roster = _player_index(prev_roster)
    new_roster = _player_index(roster_state)
    added_ids = [pid for pid in new_roster if pid not in old_roster]
    removed_ids = [pid for pid in old_roster if pid not in new_roster]

    roster_changed: list[dict[str, Any]] = []
    roster_fields = (
        "roster_status",
        "fantasy_slot",
        "injury_status",
        "keeper_round",
        "keeper_origin",
    )
    for player_id in sorted(set(old_roster) & set(new_roster)):
        changes = _field_changes(old_roster[player_id], new_roster[player_id], roster_fields)
        if changes:
            roster_changed.append(
                {
                    "player_id": player_id,
                    "player_name": new_roster[player_id].get("player_name"),
                    "changes": changes,
                }
            )

    old_keeper = _player_index(prev_keeper)
    new_keeper = _player_index(keeper_state)
    keeper_changed: list[dict[str, Any]] = []
    for player_id in sorted(set(old_keeper) & set(new_keeper)):
        changes = _field_changes(
            old_keeper[player_id],
            new_keeper[player_id],
            ("keeper_round", "keeper_origin"),
        )
        if changes:
            keeper_changed.append(
                {
                    "player_id": player_id,
                    "player_name": new_keeper[player_id].get("player_name"),
                    "changes": changes,
                }
            )

    league_changes: dict[str, Any] = {}
    if prev_league.get("current_team_waiver_priority") != league_state.get("current_team_waiver_priority"):
        league_changes["waiver_priority"] = {
            "before": prev_league.get("current_team_waiver_priority"),
            "after": league_state.get("current_team_waiver_priority"),
        }

    old_standing = _team_standing(prev_league, team_id) or {}
    new_standing = _team_standing(league_state, team_id) or {}
    standing_changes = _field_changes(
        old_standing,
        new_standing,
        ("wins", "losses", "ties", "points_for", "points_against", "standing_rank", "playoff_rank_or_status"),
    )
    if standing_changes:
        league_changes["standing"] = standing_changes

    matchup_changes = _field_changes(
        prev_matchup,
        matchup_state,
        (
            "opponent_team_id",
            "opponent_team_name",
            "our_current_score",
            "opponent_current_score",
            "our_projected_score",
            "opponent_projected_score",
            "projected_margin",
            "actual_margin",
            "matchup_status",
        ),
    )

    old_pool = _player_index(prev_pool)
    new_pool = _player_index(player_pool_state)
    pool_added_ids = [pid for pid in new_pool if pid not in old_pool]
    pool_removed_ids = [pid for pid in old_pool if pid not in new_pool]

    def pool_brief(player: dict[str, Any]) -> dict[str, Any]:
        return {
            "player_id": player.get("player_id"),
            "player_name": player.get("player_name"),
            "position": player.get("position"),
            "availability_status": player.get("availability_status"),
            "percent_owned": player.get("percent_owned"),
            "keeper_round_if_added": player.get("keeper_round_if_added"),
        }

    material_change = bool(
        added_ids
        or removed_ids
        or roster_changed
        or keeper_changed
        or league_changes
        or matchup_changes
        or pool_added_ids
        or pool_removed_ids
        or prev_league.get("current_week") != league_state.get("current_week")
    )

    return {
        "baseline_status": "COMPARED_TO_PREVIOUS_VALIDATED",
        "current_run_id": current_run_id,
        "previous_validated_run_id": prev_manifest.get("run_id"),
        "week_before": prev_league.get("current_week"),
        "week_after": league_state.get("current_week"),
        "material_change": material_change,
        "summary": {
            "roster_adds": len(added_ids),
            "roster_drops": len(removed_ids),
            "roster_player_changes": len(roster_changed),
            "keeper_changes": len(keeper_changed),
            "player_pool_additions": len(pool_added_ids),
            "player_pool_removals": len(pool_removed_ids),
        },
        "roster": {
            "added": [_player_brief(new_roster[pid]) for pid in added_ids],
            "removed": [_player_brief(old_roster[pid]) for pid in removed_ids],
            "changed": roster_changed,
        },
        "keeper": {"changed": keeper_changed},
        "league": league_changes,
        "matchup": matchup_changes,
        "player_pool": {
            "added": [pool_brief(new_pool[pid]) for pid in pool_added_ids[:25]],
            "removed": [pool_brief(old_pool[pid]) for pid in pool_removed_ids[:25]],
            "added_count": len(pool_added_ids),
            "removed_count": len(pool_removed_ids),
            "list_cap": 25,
        },
    }
