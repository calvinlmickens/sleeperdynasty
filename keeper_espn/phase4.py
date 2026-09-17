from __future__ import annotations

from datetime import datetime
from typing import Any


ACTIVEISH = {"ACTIVE", "UNKNOWN"}


def _team_row(league_state: dict[str, Any], team_id: int) -> dict[str, Any]:
    for row in league_state.get("standings") or []:
        if int(row.get("team_id", -1)) == int(team_id):
            return row
    return {}


def _roster_issue_summary(roster_state: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    ir_count = int(roster_state.get("ir_count") or 0)
    ir_limit = int(roster_state.get("league_allowed_ir_slots") or 0)
    if ir_limit and ir_count >= ir_limit:
        issues.append(
            {
                "type": "IR_CAPACITY",
                "severity": "INFO",
                "detail": f"League-allowed IR capacity is full ({ir_count}/{ir_limit}).",
            }
        )

    for player in roster_state.get("players") or []:
        status = str(player.get("injury_status") or "UNKNOWN")
        if player.get("roster_status") == "STARTER" and status not in ACTIVEISH:
            issues.append(
                {
                    "type": "STARTER_INJURY_STATUS",
                    "severity": "MONITOR",
                    "player_name": player.get("player_name"),
                    "injury_status": status,
                }
            )
    return issues


def _tci_inputs(
    *,
    standing: dict[str, Any],
    roster_state: dict[str, Any],
    matchup_state: dict[str, Any],
) -> dict[str, Any]:
    starters = [p for p in roster_state.get("players") or [] if p.get("roster_status") == "STARTER"]
    bench = [p for p in roster_state.get("players") or [] if p.get("roster_status") == "BENCH"]
    unavailable_starters = [
        p for p in starters if str(p.get("injury_status") or "UNKNOWN") not in ACTIVEISH
    ]
    return {
        "record": {
            "wins": standing.get("wins"),
            "losses": standing.get("losses"),
            "ties": standing.get("ties"),
        },
        "standing_rank": standing.get("standing_rank"),
        "points_for": standing.get("points_for"),
        "starter_count": len(starters),
        "bench_count": len(bench),
        "unavailable_or_limited_starters": len(unavailable_starters),
        "projected_margin": matchup_state.get("projected_margin"),
        "note": "Inputs only; final TCI requires Advisor evaluation.",
    }


def _relevant_flags(player: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    injury = str(player.get("injury_status") or "UNKNOWN")
    if injury not in ACTIVEISH:
        flags.append(f"INJURY_{injury}")
    if player.get("keeper_round") == 17:
        flags.append("R17_KEEPER_COST")
    if player.get("roster_status") == "IR":
        flags.append("ON_IR")
    return flags


def _roster_packet(roster_state: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for player in roster_state.get("players") or []:
        result.append(
            {
                "player_name": player.get("player_name"),
                "position": player.get("position"),
                "roster_status": player.get("roster_status"),
                "fantasy_slot": player.get("fantasy_slot"),
                "weekly_projection": player.get("weekly_projection"),
                "actual_points_if_final": player.get("weekly_actual"),
                "injury_status": player.get("injury_status"),
                "role_trend": None,
                "keeper_round": player.get("keeper_round"),
                "keeper_origin": player.get("keeper_origin"),
                "relevant_flags": _relevant_flags(player),
            }
        )
    return result


def _actionable_pool(player_pool_state: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    players = list(player_pool_state.get("players") or [])[:limit]
    result: list[dict[str, Any]] = []
    for player in players:
        result.append(
            {
                "player_name": player.get("player_name"),
                "position": player.get("position"),
                "availability": player.get("availability_status"),
                "projection": player.get("weekly_projection"),
                "ros_context": None,
                "role_trend": None,
                "keeper_round_if_added": player.get("keeper_round_if_added"),
                "percent_owned": player.get("percent_owned"),
                "injury_status": player.get("injury_status"),
                "why_included": "Top ESPN available-player result by percent owned; requires Advisor evaluation.",
            }
        )
    return result


def _material_deltas(delta_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_change": delta_state.get("material_change"),
        "summary": delta_state.get("summary"),
        "roster": delta_state.get("roster"),
        "keeper": delta_state.get("keeper"),
        "league": delta_state.get("league"),
        "matchup": delta_state.get("matchup"),
        "player_pool": delta_state.get("player_pool"),
    }


def _decision_queue(
    *,
    roster_state: dict[str, Any],
    delta_state: dict[str, Any],
    matchup_state: dict[str, Any],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []

    injury_players = []
    for player in roster_state.get("players") or []:
        injury = str(player.get("injury_status") or "UNKNOWN")
        roster_status = player.get("roster_status")
        if roster_status in {"STARTER", "BENCH"} and injury not in ACTIVEISH:
            injury_players.append(
                {
                    "player_name": player.get("player_name"),
                    "roster_status": roster_status,
                    "injury_status": injury,
                }
            )
    if injury_players:
        queue.append(
            {
                "type": "INJURY_MONITOR_REQUIRED",
                "reason": "One or more active-roster players have non-active injury designations.",
                "players": injury_players,
            }
        )

    summary = delta_state.get("summary") or {}
    pool_adds = int(summary.get("player_pool_additions") or 0)
    pool_drops = int(summary.get("player_pool_removals") or 0)
    roster_adds = int(summary.get("roster_adds") or 0)
    roster_drops = int(summary.get("roster_drops") or 0)

    if pool_adds or pool_drops:
        queue.append(
            {
                "type": "WAIVER_DECISION_REQUIRED",
                "reason": "The available-player pool changed since the prior validated run.",
                "player_pool_additions": pool_adds,
                "player_pool_removals": pool_drops,
            }
        )

    if roster_adds or roster_drops:
        queue.append(
            {
                "type": "DROP_DECISION_REQUIRED",
                "reason": "Taylor Made roster composition changed; confirm downstream roster implications.",
                "roster_adds": roster_adds,
                "roster_drops": roster_drops,
            }
        )

    keeper_changes = int(summary.get("keeper_changes") or 0)
    if keeper_changes:
        queue.append(
            {
                "type": "KEEPER_REVIEW_REQUIRED",
                "reason": "Keeper economics changed since the prior validated run.",
                "keeper_changes": keeper_changes,
            }
        )

    if matchup_state.get("matchup_status") == "PRE_GAME":
        starter_injury = any(
            p.get("roster_status") == "STARTER"
            and str(p.get("injury_status") or "UNKNOWN") not in ACTIVEISH
            for p in roster_state.get("players") or []
        )
        if starter_injury:
            queue.append(
                {
                    "type": "START_SIT_REQUIRED",
                    "reason": "Pregame lineup includes at least one starter with a non-active injury designation.",
                }
            )

    if not queue:
        queue.append(
            {
                "type": "NO_ACTION_REQUIRED",
                "reason": "No rule-based trigger was identified by the automation; Advisor may still review context.",
            }
        )
    return queue


def _opponent_packet(matchup_state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    roster = list(matchup_state.get("opponent_roster") or [])
    normalized = [
        {
            "player_name": player.get("player_name"),
            "position": player.get("position"),
            "roster_status": player.get("roster_status"),
            "fantasy_slot": player.get("fantasy_slot"),
            "weekly_projection": player.get("weekly_projection"),
            "injury_status": player.get("injury_status"),
        }
        for player in roster
    ]
    starters = [row for row in normalized if row.get("roster_status") == "STARTER"]
    starters.sort(
        key=lambda row: (
            row.get("weekly_projection") is None,
            -(float(row.get("weekly_projection") or 0.0)),
            str(row.get("player_name") or ""),
        )
    )
    key_players = starters[:5]
    projected_count = sum(1 for row in normalized if row.get("weekly_projection") is not None)
    coverage = {
        "opponent_roster_count": len(normalized),
        "projected_player_count": projected_count,
        "projection_coverage": (
            projected_count / len(normalized)
            if normalized
            else 0.0
        ),
    }
    return normalized, key_players, coverage


def _external_review_gate(manifest: dict[str, Any]) -> dict[str, Any]:
    generated_at = manifest.get("generated_at_et")
    weekday = None
    if generated_at:
        try:
            weekday = datetime.fromisoformat(str(generated_at)).strftime("%A").upper()
        except ValueError:
            weekday = None

    daily_focus = {
        "MONDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN"],
        "TUESDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "FANTASY_FOOTBALLERS", "LATE_ROUND", "ROTOWIRE"],
        "WEDNESDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN"],
        "THURSDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN", "FANTASY_FOOTBALLERS", "LATE_ROUND"],
        "FRIDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN", "FANTASY_FOOTBALLERS", "LATE_ROUND", "ACTION_NETWORK"],
        "SATURDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN", "FANTASY_FOOTBALLERS", "LATE_ROUND", "ACTION_NETWORK"],
        "SUNDAY": ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN"],
    }
    sources = daily_focus.get(
        weekday,
        ["ESPN", "OFFICIAL_NFL", "FANTASYPROS", "ROTOWIRE", "ESTABLISH_THE_RUN"],
    )
    return {
        "required_before_final_advisor_decision": True,
        "weekday": weekday,
        "required_source_checks": sources,
        "classification_required": [
            "MATERIAL_CHANGE",
            "VALIDATION",
            "CHALLENGE",
            "IGNORE",
        ],
        "source_gap_rule": (
            "If a prescribed source is unavailable, paywalled, stale, or has no relevant new content, "
            "record the gap explicitly and continue with the remaining source stack."
        ),
        "anti_tunnel_vision_rule": (
            "Do not finalize a daily workload from ESPN/official automation alone. "
            "Establish the baseline first, then run the external analyst challenge/validation sweep."
        ),
    }


def build_advisor_packet(
    *,
    manifest: dict[str, Any],
    team_id: int,
    league_state: dict[str, Any],
    roster_state: dict[str, Any],
    matchup_state: dict[str, Any],
    keeper_state: dict[str, Any],
    player_pool_state: dict[str, Any],
    delta_state: dict[str, Any],
) -> dict[str, Any]:
    standing = _team_row(league_state, team_id)
    roster_issues = _roster_issue_summary(roster_state)
    opponent_roster, key_opponent_players, opponent_coverage = _opponent_packet(matchup_state)

    known_gaps = [
        "Per-player actual points are not yet normalized for final-week recap use.",
        "External intelligence/role-trend/ROS context is not yet automated.",
        "Final TCI is intentionally not assigned by automation.",
    ]
    roster_projection_count = sum(
        1
        for player in roster_state.get("players") or []
        if player.get("weekly_projection") is not None
    )
    if roster_projection_count < len(roster_state.get("players") or []):
        known_gaps.append(
            "ESPN weekly projections are normalized, but some Taylor Made players do not have a current platform projection."
        )
    if opponent_coverage["projected_player_count"] < opponent_coverage["opponent_roster_count"]:
        known_gaps.append(
            "Opponent roster is normalized, but some opponent players do not have a current ESPN projection."
        )

    record = {
        "wins": standing.get("wins"),
        "losses": standing.get("losses"),
        "ties": standing.get("ties"),
    }

    return {
        "run_state": {
            "generated_at": manifest.get("generated_at_et"),
            "run_id": manifest.get("run_id"),
            "previous_validated_run_id": manifest.get("previous_validated_run_id"),
            "week": manifest.get("nfl_week"),
            "validation_status": manifest.get("validation_status"),
            "data_freshness": manifest.get("data_freshness_status"),
            "known_gaps": known_gaps,
        },
        "external_review_gate": _external_review_gate(manifest),
        "team_state": {
            "team_name": roster_state.get("team_name"),
            "record": record,
            "standing": standing.get("standing_rank"),
            "points_for": standing.get("points_for"),
            "waiver_priority": league_state.get("current_team_waiver_priority"),
            "current_tci_inputs": _tci_inputs(
                standing=standing,
                roster_state=roster_state,
                matchup_state=matchup_state,
            ),
            "major_roster_issues": roster_issues,
            "ir_usage": {
                "used": roster_state.get("ir_count"),
                "league_allowed": roster_state.get("league_allowed_ir_slots"),
                "effective_open": roster_state.get("effective_open_ir_slots"),
                "platform_slots": roster_state.get("platform_ir_slots"),
            },
        },
        "matchup": {
            "opponent": matchup_state.get("opponent_team_name"),
            "our_projection": matchup_state.get("our_projected_score"),
            "opponent_projection": matchup_state.get("opponent_projected_score"),
            "projected_margin": matchup_state.get("projected_margin"),
            "matchup_status": matchup_state.get("matchup_status"),
            "key_opponent_players": key_opponent_players,
            "opponent_roster": opponent_roster,
            "opponent_projection_coverage": opponent_coverage,
            "unresolved_matchup_conditions": (
                []
                if opponent_coverage["projected_player_count"] == opponent_coverage["opponent_roster_count"]
                else ["Some opponent players do not have a current ESPN projection."]
            ),
        },
        "taylor_made_roster": _roster_packet(roster_state),
        "actionable_player_pool": _actionable_pool(player_pool_state),
        "material_intelligence": [],
        "material_deltas": _material_deltas(delta_state),
        "decision_queue": _decision_queue(
            roster_state=roster_state,
            delta_state=delta_state,
            matchup_state=matchup_state,
        ),
        "weekly_result": None,
        "league_recap_summary": None,
        "automation_boundary": {
            "strategy_decisions_embedded": False,
            "final_tci_assigned": False,
            "note": "Automation collects, normalizes, flags rule-based review needs, and hands off to the Advisor.",
        },
    }
