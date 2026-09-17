from __future__ import annotations

from typing import Any

from .client import EspnPull
from .pipeline import POSITION_NAMES, _roster_status, _slot_name, _team_name


def _team_map(league: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(team["id"]): team for team in (league.get("teams") or []) if team.get("id") is not None}


def _side_team_id(side: dict[str, Any] | None) -> int | None:
    if not side:
        return None
    value = side.get("teamId")
    return int(value) if value is not None else None


def _player_actual(entry: dict[str, Any], *, week: int) -> float | None:
    pool = entry.get("playerPoolEntry") or {}
    applied = pool.get("appliedStatTotal")
    if applied is not None:
        return float(applied)

    player = pool.get("player") or {}
    for stat in player.get("stats") or pool.get("stats") or []:
        if int(stat.get("scoringPeriodId", -1)) != int(week):
            continue
        if int(stat.get("statTypeId", -1)) != 0:
            continue
        value = stat.get("appliedTotal")
        if value is not None:
            return float(value)
    return None


def _player_result(entry: dict[str, Any], *, week: int) -> dict[str, Any]:
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or {}
    player_id = player.get("id") or pool.get("id")
    position_id = player.get("defaultPositionId")
    slot_id = entry.get("lineupSlotId")
    return {
        "player_id": str(player_id) if player_id is not None else None,
        "player_name": player.get("fullName") or player.get("name") or "Unknown Player",
        "position": POSITION_NAMES.get(
            position_id,
            str(position_id) if position_id is not None else "UNKNOWN",
        ),
        "fantasy_slot": _slot_name(slot_id),
        "roster_status": _roster_status(slot_id),
        "actual_points": _player_actual(entry, week=week),
    }


def _boxscore_schedule(pull: EspnPull, *, week: int) -> list[dict[str, Any]]:
    if pull.boxscore:
        schedule = [
            matchup
            for matchup in (pull.boxscore.get("schedule") or [])
            if int(matchup.get("matchupPeriodId", -1)) == int(week)
        ]
        if schedule:
            return schedule
    return [
        matchup
        for matchup in (pull.league.get("schedule") or [])
        if int(matchup.get("matchupPeriodId", -1)) == int(week)
    ]


def _side_entries(side: dict[str, Any]) -> list[dict[str, Any]]:
    roster = side.get("rosterForCurrentScoringPeriod") or {}
    entries = roster.get("entries") or []
    return entries if isinstance(entries, list) else []


def _team_player_results(side: dict[str, Any], *, week: int) -> list[dict[str, Any]]:
    return [_player_result(entry, week=week) for entry in _side_entries(side)]


def _winner_team_id(matchup: dict[str, Any]) -> int | None:
    winner = matchup.get("winner")
    if winner == "HOME":
        return _side_team_id(matchup.get("home"))
    if winner == "AWAY":
        return _side_team_id(matchup.get("away"))
    return None


def _outcome(score: float | None, opponent_score: float | None) -> str | None:
    if score is None or opponent_score is None:
        return None
    if score > opponent_score:
        return "WIN"
    if score < opponent_score:
        return "LOSS"
    return "TIE"


def _score(side: dict[str, Any]) -> float | None:
    value = side.get("totalPoints")
    return float(value) if value is not None else None


def _team_result(
    *,
    team_id: int,
    team_name: str,
    side: dict[str, Any],
    opponent_id: int | None,
    opponent_name: str | None,
    opponent_side: dict[str, Any],
    week: int,
) -> dict[str, Any]:
    score = _score(side)
    opponent_score = _score(opponent_side)
    players = _team_player_results(side, week=week)
    starters = [p for p in players if p.get("roster_status") == "STARTER"]
    bench = [p for p in players if p.get("roster_status") == "BENCH"]
    ir = [p for p in players if p.get("roster_status") == "IR"]

    def total_known(rows: list[dict[str, Any]]) -> float | None:
        values = [p.get("actual_points") for p in rows if p.get("actual_points") is not None]
        return round(sum(float(v) for v in values), 3) if values else None

    highest_bench = None
    known_bench = [p for p in bench if p.get("actual_points") is not None]
    if known_bench:
        highest_bench = max(known_bench, key=lambda p: float(p["actual_points"]))

    return {
        "team_id": team_id,
        "team_name": team_name,
        "opponent_team_id": opponent_id,
        "opponent_team_name": opponent_name,
        "score": score,
        "opponent_score": opponent_score,
        "outcome": _outcome(score, opponent_score),
        "margin": (
            round(score - opponent_score, 3)
            if score is not None and opponent_score is not None
            else None
        ),
        "starter_points_total": total_known(starters),
        "bench_points_total": total_known(bench),
        "highest_bench_player": highest_bench,
        "starters": starters,
        "bench": bench,
        "ir": ir,
        "player_actuals_complete": bool(players)
        and all(p.get("actual_points") is not None for p in players),
    }


def build_league_results(
    pull: EspnPull,
    *,
    league_state: dict[str, Any],
    target_team_id: int,
) -> dict[str, Any]:
    current_week = int(league_state.get("current_week") or 0)
    week = int(pull.results_week or current_week)
    matchups = _boxscore_schedule(pull, week=week)
    teams = _team_map(pull.league)

    finalized = bool(matchups) and all(
        matchup.get("winner") in {"HOME", "AWAY", "TIE"} for matchup in matchups
    )

    matchup_rows: list[dict[str, Any]] = []
    team_results: list[dict[str, Any]] = []

    for matchup in matchups:
        home = matchup.get("home") or {}
        away = matchup.get("away") or {}
        home_id = _side_team_id(home)
        away_id = _side_team_id(away)
        if home_id is None or away_id is None:
            continue

        home_name = _team_name(teams.get(home_id, {"id": home_id}))
        away_name = _team_name(teams.get(away_id, {"id": away_id}))
        home_score = _score(home)
        away_score = _score(away)
        margin = (
            abs(home_score - away_score)
            if home_score is not None and away_score is not None
            else None
        )
        winner_id = _winner_team_id(matchup)
        winner_name = (
            home_name if winner_id == home_id else away_name if winner_id == away_id else None
        )

        matchup_rows.append(
            {
                "matchup_id": matchup.get("id"),
                "week": week,
                "home_team_id": home_id,
                "home_team_name": home_name,
                "home_score": home_score,
                "away_team_id": away_id,
                "away_team_name": away_name,
                "away_score": away_score,
                "winner_team_id": winner_id,
                "winner_team_name": winner_name,
                "winner_code": matchup.get("winner"),
                "margin": round(margin, 3) if margin is not None else None,
                "close_game": margin is not None and margin <= 5.0,
                "blowout": margin is not None and margin >= 30.0,
            }
        )

        team_results.append(
            _team_result(
                team_id=home_id,
                team_name=home_name,
                side=home,
                opponent_id=away_id,
                opponent_name=away_name,
                opponent_side=away,
                week=week,
            )
        )
        team_results.append(
            _team_result(
                team_id=away_id,
                team_name=away_name,
                side=away,
                opponent_id=home_id,
                opponent_name=home_name,
                opponent_side=home,
                week=week,
            )
        )

    scored = [row for row in team_results if row.get("score") is not None]
    scored_sorted = sorted(scored, key=lambda row: float(row["score"]), reverse=True)
    for rank, row in enumerate(scored_sorted, start=1):
        row["weekly_scoring_rank"] = rank

    high_team = scored_sorted[0] if scored_sorted else None
    low_team = scored_sorted[-1] if scored_sorted else None
    closest = min(
        (row for row in matchup_rows if row.get("margin") is not None),
        key=lambda row: float(row["margin"]),
        default=None,
    )
    biggest = max(
        (row for row in matchup_rows if row.get("margin") is not None),
        key=lambda row: float(row["margin"]),
        default=None,
    )

    teams_with_player_actuals = sum(
        1 for row in team_results if row.get("starters") or row.get("bench") or row.get("ir")
    )
    complete_player_actuals = bool(team_results) and all(
        row.get("player_actuals_complete") for row in team_results
    )

    if finalized and complete_player_actuals:
        results_status = "FINAL_RESULTS"
    else:
        results_status = "PARTIAL_RESULTS"

    taylor = next(
        (row for row in team_results if int(row.get("team_id", -1)) == int(target_team_id)),
        None,
    )

    return {
        "season": league_state.get("season"),
        "week": week,
        "current_espn_week": current_week,
        "results_status": results_status,
        "team_count": league_state.get("number_of_teams"),
        "matchup_count": len(matchup_rows),
        "matchups": matchup_rows,
        "team_results": team_results,
        "weekly_scoring_rankings": [
            {
                "rank": row.get("weekly_scoring_rank"),
                "team_id": row.get("team_id"),
                "team_name": row.get("team_name"),
                "score": row.get("score"),
            }
            for row in scored_sorted
        ],
        "league_high_score": (
            {
                "team_id": high_team.get("team_id"),
                "team_name": high_team.get("team_name"),
                "score": high_team.get("score"),
            }
            if high_team
            else None
        ),
        "league_low_score": (
            {
                "team_id": low_team.get("team_id"),
                "team_name": low_team.get("team_name"),
                "score": low_team.get("score"),
            }
            if low_team
            else None
        ),
        "closest_game": closest,
        "largest_margin_game": biggest,
        "standings_snapshot": league_state.get("standings") or [],
        "taylor_made_result": taylor,
        "completeness": {
            "team_results_final": finalized,
            "boxscore_available": pull.boxscore is not None,
            "teams_with_player_actuals": teams_with_player_actuals,
            "expected_teams": league_state.get("number_of_teams"),
            "player_actuals_complete": complete_player_actuals,
            "source": pull.boxscore_source or pull.source,
        },
    }


def build_weekly_result(league_results: dict[str, Any]) -> dict[str, Any] | None:
    result = league_results.get("taylor_made_result")
    if not result:
        return None
    return {
        "week": league_results.get("week"),
        "results_status": league_results.get("results_status"),
        "opponent": result.get("opponent_team_name"),
        "our_score": result.get("score"),
        "opponent_score": result.get("opponent_score"),
        "outcome": result.get("outcome"),
        "margin": result.get("margin"),
        "weekly_scoring_rank": result.get("weekly_scoring_rank"),
        "starter_points_total": result.get("starter_points_total"),
        "bench_points_total": result.get("bench_points_total"),
        "highest_bench_player": result.get("highest_bench_player"),
        "starters": result.get("starters"),
        "bench": result.get("bench"),
        "ir": result.get("ir"),
        "postmortem_note": "Factual result inputs only; Advisor determines decision quality versus variance.",
    }


def build_league_recap_summary(league_results: dict[str, Any]) -> dict[str, Any]:
    team_results = league_results.get("team_results") or []
    known_bench = [
        row for row in team_results if row.get("bench_points_total") is not None
    ]
    most_bench = (
        max(known_bench, key=lambda row: float(row["bench_points_total"]))
        if known_bench
        else None
    )
    return {
        "week": league_results.get("week"),
        "results_status": league_results.get("results_status"),
        "league_high_score": league_results.get("league_high_score"),
        "league_low_score": league_results.get("league_low_score"),
        "closest_game": league_results.get("closest_game"),
        "largest_margin_game": league_results.get("largest_margin_game"),
        "weekly_scoring_rankings": league_results.get("weekly_scoring_rankings"),
        "team_with_most_bench_points": (
            {
                "team_id": most_bench.get("team_id"),
                "team_name": most_bench.get("team_name"),
                "bench_points_total": most_bench.get("bench_points_total"),
                "highest_bench_player": most_bench.get("highest_bench_player"),
            }
            if most_bench
            else None
        ),
        "matchups": league_results.get("matchups"),
        "standings_snapshot": league_results.get("standings_snapshot"),
        "note": "Factual league recap inputs; narrative significance is determined by the Advisor.",
    }


def apply_results_to_advisor_packet(
    advisor_packet: dict[str, Any],
    *,
    league_results: dict[str, Any],
) -> dict[str, Any]:
    advisor_packet["weekly_result"] = build_weekly_result(league_results)
    advisor_packet["league_recap_summary"] = build_league_recap_summary(league_results)

    if league_results.get("completeness", {}).get("player_actuals_complete"):
        gaps = advisor_packet.get("run_state", {}).get("known_gaps") or []
        advisor_packet["run_state"]["known_gaps"] = [
            gap
            for gap in gaps
            if gap != "Per-player actual points are not yet normalized for final-week recap use."
        ]
    return advisor_packet
