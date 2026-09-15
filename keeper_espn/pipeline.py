from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .client import EspnFantasyClient, EspnPull, load_fixture
from .config import KeeperEspnConfig

ET = ZoneInfo("America/New_York")
SCHEMA_VERSION = "0.2"

# ESPN lineup slot IDs are stable identifiers in the fantasy API. Unknown slots
# are preserved instead of guessed.
SLOT_NAMES = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "D/ST",
    17: "K",
    20: "BE",
    21: "IR",
    23: "FLEX",
}
POSITION_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}


def _now_et() -> str:
    return datetime.now(ET).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")


def _team_name(team: dict[str, Any]) -> str:
    location = str(team.get("location") or "").strip()
    nickname = str(team.get("nickname") or "").strip()
    name = " ".join(x for x in (location, nickname) if x).strip()
    return name or str(team.get("name") or f"Team {team.get('id')}")


def _find_team(league: dict[str, Any], team_id: int) -> dict[str, Any]:
    for team in league.get("teams") or []:
        if int(team.get("id", -1)) == team_id:
            return team
    raise RuntimeError(f"Configured ESPN_TEAM_ID {team_id} not found in league response")


def _slot_name(slot_id: Any) -> str:
    try:
        slot_int = int(slot_id)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return SLOT_NAMES.get(slot_int, f"SLOT_{slot_int}")


def _roster_status(slot_id: Any) -> str:
    try:
        slot_int = int(slot_id)
    except (TypeError, ValueError):
        return "STARTER"
    if slot_int == 20:
        return "BENCH"
    if slot_int == 21:
        return "IR"
    return "STARTER"


def _player_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or {}
    player_id = player.get("id") or pool.get("id")
    slot_id = entry.get("lineupSlotId")
    position_id = player.get("defaultPositionId")
    return {
        "player_id": str(player_id) if player_id is not None else None,
        "player_name": player.get("fullName") or player.get("name") or "Unknown Player",
        "nfl_team_id": player.get("proTeamId"),
        "position": POSITION_NAMES.get(position_id, str(position_id) if position_id is not None else "UNKNOWN"),
        "fantasy_slot": _slot_name(slot_id),
        "roster_status": _roster_status(slot_id),
        "injury_status": player.get("injuryStatus") or "UNKNOWN",
        "bye_week": None,
        "keeper_round": None,
        "keeper_origin": "UNKNOWN",
    }


def _extract_lineup_counts(league: dict[str, Any]) -> dict[str, int]:
    roster = ((league.get("settings") or {}).get("rosterSettings") or {})
    raw = roster.get("lineupSlotCounts") or {}

    def count(slot_id: int) -> int:
        return int(raw.get(str(slot_id), raw.get(slot_id, 0)) or 0)

    return {
        "starting_qb": count(0),
        "starting_rb": count(2),
        "starting_wr": count(4),
        "starting_te": count(6),
        "starting_flex": count(23),
        "starting_dst": count(16),
        "starting_k": count(17),
        "bench_slots": count(20),
        "ir_slots": count(21),
    }


def _regular_and_playoff_weeks(league: dict[str, Any]) -> tuple[int | None, int | None]:
    schedule = ((league.get("settings") or {}).get("scheduleSettings") or {})
    regular_end = schedule.get("matchupPeriodCount")
    playoff_start = int(regular_end) + 1 if regular_end is not None else None
    return int(regular_end) if regular_end is not None else None, playoff_start


def _standing_row(team: dict[str, Any]) -> dict[str, Any]:
    overall = ((team.get("record") or {}).get("overall") or {})
    return {
        "team_id": int(team["id"]),
        "team_name": _team_name(team),
        "wins": int(overall.get("wins", 0) or 0),
        "losses": int(overall.get("losses", 0) or 0),
        "ties": int(overall.get("ties", 0) or 0),
        "points_for": float(overall.get("pointsFor", 0.0) or 0.0),
        "points_against": float(overall.get("pointsAgainst", 0.0) or 0.0),
        "standing_rank": team.get("rankCalculatedFinal") or team.get("playoffSeed"),
        "playoff_rank_or_status": team.get("playoffSeed"),
    }


def build_league_state(pull: EspnPull, config: KeeperEspnConfig) -> dict[str, Any]:
    league = pull.league
    regular_end, playoff_start = _regular_and_playoff_weeks(league)
    counts = _extract_lineup_counts(league)
    team = _find_team(league, config.team_id)
    scoring = ((league.get("settings") or {}).get("scoringSettings") or {})
    passing_td = None
    for item in scoring.get("scoringItems") or []:
        if item.get("statId") == 4:  # ESPN passing TD stat ID
            passing_td = item.get("points")
            break

    return {
        "league_id": str(league.get("id") or config.league_id),
        "league_name": league.get("settings", {}).get("name") or league.get("name"),
        "season": int(league.get("seasonId") or config.season),
        "current_week": int(league.get("scoringPeriodId") or 0),
        "regular_season_end_week": regular_end,
        "playoff_start_week": playoff_start,
        "number_of_teams": len(league.get("teams") or []),
        "scoring_format": "ESPN_CUSTOM",
        "passing_td_points": passing_td,
        **counts,
        "waiver_type": ((league.get("settings") or {}).get("acquisitionSettings") or {}).get("waiverProcessHour"),
        "current_team_waiver_priority": team.get("waiverRank"),
        "standings": [_standing_row(t) for t in (league.get("teams") or [])],
        "source": pull.source,
    }


def build_roster_state(pull: EspnPull, config: KeeperEspnConfig, *, week: int) -> dict[str, Any]:
    team = _find_team(pull.league, config.team_id)
    entries = ((team.get("roster") or {}).get("entries") or [])
    players = [_player_from_entry(entry) for entry in entries]
    statuses = [p["roster_status"] for p in players]
    expected_total = sum(_extract_lineup_counts(pull.league).values())
    return {
        "team_id": config.team_id,
        "team_name": _team_name(team),
        "week": week,
        "roster_count": len(players),
        "starter_count": statuses.count("STARTER"),
        "bench_count": statuses.count("BENCH"),
        "ir_count": statuses.count("IR"),
        "open_roster_slots": max(0, expected_total - len(players)),
        "players": players,
    }


def _side_team_id(side: dict[str, Any] | None) -> int | None:
    if not side:
        return None
    team_id = side.get("teamId")
    return int(team_id) if team_id is not None else None


def _find_matchup(league: dict[str, Any], *, week: int, team_id: int) -> dict[str, Any]:
    schedule = league.get("schedule") or []
    candidates = [m for m in schedule if int(m.get("matchupPeriodId", -1)) == week]
    for matchup in candidates:
        home_id = _side_team_id(matchup.get("home"))
        away_id = _side_team_id(matchup.get("away"))
        if team_id in {home_id, away_id}:
            return matchup
    raise RuntimeError(f"No ESPN matchup found for team {team_id} in matchup period {week}")


def _score(side: dict[str, Any] | None, key: str) -> float | None:
    if not side:
        return None
    value = side.get(key)
    return float(value) if value is not None else None


def build_matchup_state(pull: EspnPull, config: KeeperEspnConfig, *, week: int) -> dict[str, Any]:
    matchup = _find_matchup(pull.league, week=week, team_id=config.team_id)
    home = matchup.get("home") or {}
    away = matchup.get("away") or {}
    home_id = _side_team_id(home)
    away_id = _side_team_id(away)
    ours, opp = (home, away) if home_id == config.team_id else (away, home)
    opp_id = _side_team_id(opp)
    if opp_id is None:
        raise RuntimeError("Opponent team ID missing from ESPN matchup")
    opponent = _find_team(pull.league, opp_id)

    current_score = _score(ours, "totalPoints")
    opponent_score = _score(opp, "totalPoints")
    our_projection = _score(ours, "totalProjectedPointsLive")
    if our_projection is None:
        our_projection = _score(ours, "totalProjectedPoints")
    opponent_projection = _score(opp, "totalProjectedPointsLive")
    if opponent_projection is None:
        opponent_projection = _score(opp, "totalProjectedPoints")

    if matchup.get("winner") in {"HOME", "AWAY", "TIE"}:
        status = "FINAL"
    elif any((current_score or 0, opponent_score or 0)):
        status = "IN_PROGRESS"
    else:
        status = "PRE_GAME"

    return {
        "week": week,
        "our_team_id": config.team_id,
        "opponent_team_id": opp_id,
        "opponent_team_name": _team_name(opponent),
        "our_current_score": current_score,
        "opponent_current_score": opponent_score,
        "our_projected_score": our_projection,
        "opponent_projected_score": opponent_projection,
        "projected_margin": (
            our_projection - opponent_projection
            if our_projection is not None and opponent_projection is not None
            else None
        ),
        "actual_margin": (
            current_score - opponent_score
            if current_score is not None and opponent_score is not None
            else None
        ),
        "matchup_status": status,
        "players_remaining_us": None,
        "players_remaining_opponent": None,
        "remaining_players": [],
    }


def validate_phase1(
    *,
    config: KeeperEspnConfig,
    league_state: dict[str, Any],
    roster_state: dict[str, Any],
    matchup_state: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if str(league_state.get("league_id")) != str(config.league_id):
        errors.append("League ID mismatch")
    if int(league_state.get("season", -1)) != int(config.season):
        errors.append("Season mismatch")
    if roster_state.get("team_id") != config.team_id:
        errors.append("Target team mismatch")
    if roster_state.get("roster_count", 0) <= 0:
        errors.append("Target roster is empty")

    player_ids = [p.get("player_id") for p in roster_state.get("players", []) if p.get("player_id")]
    if len(player_ids) != len(set(player_ids)):
        errors.append("Duplicate player IDs on Taylor Made roster")

    if matchup_state.get("our_team_id") != config.team_id:
        errors.append("Matchup target team mismatch")
    if matchup_state.get("opponent_team_id") is None:
        errors.append("Opponent not identified")
    standing_ids = {row.get("team_id") for row in league_state.get("standings", [])}
    if config.team_id not in standing_ids:
        errors.append("Taylor Made missing from standings")
    if matchup_state.get("opponent_team_id") not in standing_ids:
        errors.append("Opponent missing from standings")
    if int(matchup_state.get("week", -1)) != int(league_state.get("current_week", -2)):
        errors.append("Matchup week does not match ESPN current scoring period")
    return errors


def run_refresh(
    *,
    output_dir: str | Path = "keeper_output",
    fixture_dir: str | Path | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    config = KeeperEspnConfig.from_env(season=season)
    pulled_at = _now_et()
    run_id = f"{datetime.now(ET).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    root = Path(output_dir)
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    try:
        pull = load_fixture(fixture_dir) if fixture_dir else EspnFantasyClient(config).pull_league()
        league_state = build_league_state(pull, config)
        week = int(league_state["current_week"])
        roster_state = build_roster_state(pull, config, week=week)
        matchup_state = build_matchup_state(pull, config, week=week)
        errors = validate_phase1(
            config=config,
            league_state=league_state,
            roster_state=roster_state,
            matchup_state=matchup_state,
        )
        status = "PASS" if not errors else "FAIL"

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at_et": pulled_at,
            "nfl_week": week,
            "season": config.season,
            "platform": "ESPN",
            "league_id": config.league_id,
            "team_id": config.team_id,
            "team_name": roster_state.get("team_name") or config.team_name,
            "previous_validated_run_id": None,
            "validation_status": status,
            "validation_errors": errors,
            "data_freshness_status": "CURRENT",
            "source_status": {"espn": "CURRENT", "source": pull.source},
        }

        _write_json(run_dir / "manifest.json", manifest)
        _write_json(run_dir / "league_state.json", league_state)
        _write_json(run_dir / "roster_state.json", roster_state)
        _write_json(run_dir / "matchup_state.json", matchup_state)

        if errors:
            diagnostic_dir = root / "diagnostics" / run_id
            diagnostic_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(run_dir, diagnostic_dir)
            return {"status": "FAIL", "run_id": run_id, "errors": errors, "run_dir": str(run_dir)}

        latest = root / "latest"
        staging = root / ".latest-staging"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(run_dir, staging)
        if latest.exists():
            shutil.rmtree(latest)
        staging.replace(latest)
        return {
            "status": "PASS",
            "run_id": run_id,
            "week": week,
            "team_name": roster_state.get("team_name"),
            "opponent": matchup_state.get("opponent_team_name"),
            "latest_dir": str(latest),
        }
    except Exception as exc:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at_et": pulled_at,
            "season": config.season,
            "platform": "ESPN",
            "league_id": config.league_id,
            "team_id": config.team_id,
            "validation_status": "FAIL",
            "validation_errors": [f"{type(exc).__name__}: {exc}"],
            "data_freshness_status": "MISSING",
            "source_status": {"espn": "FAILED"},
        }
        _write_json(run_dir / "manifest.json", manifest)
        diagnostic_dir = root / "diagnostics" / run_id
        diagnostic_dir.parent.mkdir(parents=True, exist_ok=True)
        if not diagnostic_dir.exists():
            shutil.copytree(run_dir, diagnostic_dir)
        return {"status": "FAIL", "run_id": run_id, "errors": manifest["validation_errors"], "run_dir": str(run_dir)}
