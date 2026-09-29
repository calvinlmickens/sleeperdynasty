"""Sleeper-only available-player pool and roster-specific waiver watch."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


WAIVER_COLUMNS = [
    "league_id", "season", "week", "generated_at_utc", "sleeper_player_id",
    "player_name", "position", "eligible_positions", "nfl_team", "status",
    "injury_status", "injury_body_part", "practice_status", "depth_chart_position",
    "depth_chart_order", "age", "years_exp", "projected_points", "projection_source",
    "projection_timestamp_utc", "projection_status", "projection_missing_flag",
    "name_missing_flag", "eligible_positions_missing_flag", "nfl_team_missing_flag",
    "injury_status_missing_flag", "role_data_missing_flag", "player_details_missing_flag",
    "recommendation_tier", "rank", "priority_score",
    "dynasty_value_score", "role_opportunity_score", "positional_need_score",
    "short_term_score", "reason", "suggested_drop_player_id", "suggested_drop_player_name",
    "suggested_drop_position",
]


def _num(value: Any) -> float | None:
    if value is None or pd.isna(value) or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text_or(value: Any, fallback: str) -> str:
    if value is None or pd.isna(value) or not str(value).strip():
        return fallback
    return str(value)


def _positions(player: dict[str, Any]) -> list[str]:
    value = player.get("fantasy_positions") or ([player.get("position")] if player.get("position") else [])
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    positions = list(dict.fromkeys(str(p).upper() for p in value if p))
    primary = str(player.get("position") or "").upper()
    if primary and primary not in positions:
        positions.insert(0, primary)
    return positions


def _owned_ids(rosters: list[dict[str, Any]]) -> set[str]:
    return {
        str(pid)
        for roster in rosters
        for pid in (roster.get("players") or [])
        if pid is not None and str(pid) not in {"", "0"}
    }


def _drop_candidates(
    target_roster_id: int | None,
    rosters: list[dict[str, Any]],
    players: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if target_roster_id is None:
        return []
    roster = next((r for r in rosters if int(r.get("roster_id", -1)) == target_roster_id), None)
    if not roster:
        return []
    starters = {str(pid) for pid in (roster.get("starters") or []) if pid and str(pid) != "0"}
    rows: list[dict[str, Any]] = []
    for pid in roster.get("players") or []:
        pid = str(pid)
        p = players.get(pid, {})
        if pid in starters:
            continue
        positions = _positions(p)
        pos = positions[0] if positions else ""
        age = _num(p.get("age"))
        depth = _num(p.get("depth_chart_order"))
        # Bench kickers/IDP depth and older, buried offensive players are the most
        # plausible churn spots. Unknown data stays neutral rather than becoming zero.
        score = (3 if pos == "K" else 2 if pos in {"DL", "LB", "DB", "DE", "DT", "CB", "S"} else 0)
        score += 2 if depth is not None and depth >= 3 else 0
        score += 1 if age is not None and age >= 29 else 0
        rows.append({"id": pid, "name": p.get("full_name") or pid, "position": pos, "score": score})
    return sorted(rows, key=lambda row: (-row["score"], row["position"], row["name"]))


def build_waiver_watch(
    *, league: dict[str, Any], week: int, league_id: str,
    rosters: list[dict[str, Any]], players: dict[str, dict[str, Any]],
    projection_payload: Any, target_roster_id: int | None,
    pulled_at: str, projection_source: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Compare current Sleeper roster ownership to Sleeper's current NFL player map."""
    errors: list[str] = []
    resolved_league_id = str(league.get("league_id") or "")
    if resolved_league_id != str(league_id):
        errors.append(f"league_id mismatch: expected {league_id}, received {resolved_league_id or 'missing'}")
    if not players:
        errors.append("Sleeper /players/nfl player universe is empty; availability cannot be established")

    owned = _owned_ids(rosters)
    if not owned:
        errors.append("current Sleeper rosters contain no player IDs")
    projection_rows: dict[str, dict[str, Any]] = {}
    if isinstance(projection_payload, list):
        for row in projection_payload:
            if not isinstance(row, dict):
                continue
            pid = row.get("player_id") or (row.get("player") or {}).get("player_id")
            if pid is not None:
                projection_rows[str(pid)] = row
    elif isinstance(projection_payload, dict):
        for pid, value in projection_payload.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("player_id", str(pid))
                projection_rows[str(pid)] = row

    target = next((r for r in rosters if target_roster_id is not None and int(r.get("roster_id", -1)) == int(target_roster_id)), None)
    if target_roster_id is not None and target is None:
        errors.append(f"target roster {target_roster_id} not found in current Sleeper rosters")
    target_counts: dict[str, int] = {}
    if target:
        for pid in target.get("players") or []:
            p = players.get(str(pid), {})
            for pos in _positions(p):
                target_counts[pos] = target_counts.get(pos, 0) + 1
    # This roster uses 1QB, 2RB, 2WR, 1TE, 3 IDP starters, a K, and 16 bench spots.
    # Reserve targets emphasize scarce dynasty offensive roles over replaceable IDP volume.
    desired = {"QB": 2, "RB": 5, "WR": 7, "TE": 2, "DL": 3, "LB": 3, "DB": 3, "K": 1}
    now = pulled_at or datetime.now(timezone.utc).isoformat()
    drops = _drop_candidates(target_roster_id, rosters, players)
    top_drop = drops[0] if drops else {}
    rows: list[dict[str, Any]] = []

    for pid in sorted(set(map(str, players)) - owned):
        p = players.get(pid)
        if not isinstance(p, dict) or not _positions(p):
            # Exclude NFL universe records without a position; avoid junk IDs.
            continue
        positions = _positions(p)
        primary = str(p.get("position") or positions[0]).upper()
        is_idp = bool(set(positions).intersection({"DL", "LB", "DB", "DE", "DT", "CB", "S"}))
        proj_row = projection_rows.get(pid, {})
        stats = proj_row.get("stats") if isinstance(proj_row.get("stats"), dict) else proj_row
        projected = None
        for key in ("pts_ppr", "pts_half_ppr", "pts_std", "fantasy_points", "projected_points"):
            projected = _num(stats.get(key)) if isinstance(stats, dict) else None
            if projected is None:
                projected = _num(proj_row.get(key))
            if projected is not None:
                break
        depth = _num(p.get("depth_chart_order"))
        age = _num(p.get("age"))
        years = _num(p.get("years_exp"))
        active = str(p.get("status") or "").casefold() in {"active", "act", "practice squad"}
        need = max(0, desired.get(primary, 1) - target_counts.get(primary, 0))
        positional_need = min(10.0, need * 2.5)
        dynasty = 5.0
        if age is not None:
            dynasty += max(-2.0, min(5.0, (27.0 - age) * 0.55))
        elif years is not None:
            dynasty += max(-1.0, min(3.0, (5.0 - years) * 0.35))
        draft_round = _num(p.get("draft_round"))
        if draft_round is not None:
            dynasty += 2.0 if draft_round == 1 else 1.0 if draft_round == 2 else 0.0
        dynasty = max(0.0, min(10.0, dynasty))
        role = 2.0 if depth is None else max(0.0, 10.0 - (depth - 1) * 2.0)
        role += 1.0 if p.get("depth_chart_position") else 0.0
        role = min(10.0, role)
        short_term = min(10.0, max(0.0, projected or 0.0) / 2.0) if projected is not None else 0.0
        # Offensive opportunity/dynasty value dominate. IDP receives a lower ceiling
        # so raw IDP projections cannot push deep defenders above viable skill players.
        if is_idp:
            score = 0.20 * dynasty + 0.25 * role + 0.10 * positional_need + 0.10 * short_term + 1.0
        else:
            score = 0.35 * dynasty + 0.25 * role + 0.20 * positional_need + 0.20 * short_term + (1.0 if active else 0.0)
        score = round(score, 2)

        if not is_idp and active and ((depth is not None and depth <= 2 and (projected is None or projected >= 5)) or (need >= 3 and age is not None and age <= 25)):
            tier = "PRIORITY_ADD"
        elif not is_idp and active and ((age is not None and age <= 25) or (years is not None and years <= 2)):
            tier = "SPECULATIVE_STASH"
        elif projected is not None and projected >= 7 and active:
            tier = "STREAMER"
        elif is_idp and active and depth is not None and depth <= 1 and need >= 2 and projected is not None and projected >= 5:
            tier = "PRIORITY_ADD"
        else:
            tier = "SPECULATIVE_STASH" if not is_idp else "STREAMER"

        reasons = []
        if need:
            reasons.append(f"{primary} roster need ({target_counts.get(primary, 0)} rostered; target {desired.get(primary, 1)})")
        if depth is not None:
            reasons.append(f"depth chart order {int(depth)}")
        if projected is not None:
            reasons.append(f"Sleeper Week {week} projection {projected:g}")
        if age is not None:
            reasons.append(f"age {age:g}")
        if not reasons:
            reasons.append("limited role and projection data; speculative only")
        rows.append({
            "league_id": resolved_league_id, "season": str(league.get("season") or ""), "week": week,
            "generated_at_utc": now, "sleeper_player_id": pid,
            "player_name": p.get("full_name") or p.get("first_name") or pid,
            "position": primary, "eligible_positions": ",".join(positions), "nfl_team": p.get("team"),
            "status": p.get("status"), "injury_status": p.get("injury_status"),
            "injury_body_part": p.get("injury_body_part"), "practice_status": p.get("practice_participation"),
            "depth_chart_position": p.get("depth_chart_position"), "depth_chart_order": depth,
            "age": age, "years_exp": years, "projected_points": projected,
            "projection_source": _text_or(proj_row.get("source_name"), projection_source) if projected is not None else "",
            "projection_timestamp_utc": _text_or(proj_row.get("source_timestamp_utc"), now) if projected is not None else "",
            "projection_status": "AVAILABLE" if projected is not None else "MISSING",
            "projection_missing_flag": projected is None,
            "name_missing_flag": not bool(p.get("full_name") or p.get("first_name")),
            "eligible_positions_missing_flag": not bool(positions),
            "nfl_team_missing_flag": not bool(p.get("team")),
            "injury_status_missing_flag": not bool(p.get("injury_status")),
            "role_data_missing_flag": depth is None and not bool(p.get("depth_chart_position")),
            "player_details_missing_flag": not bool(p.get("full_name") and p.get("status") and positions),
            "recommendation_tier": tier, "priority_score": score, "dynasty_value_score": round(dynasty, 2),
            "role_opportunity_score": round(role, 2), "positional_need_score": positional_need,
            "short_term_score": round(short_term, 2), "reason": "; ".join(reasons),
            "suggested_drop_player_id": top_drop.get("id"), "suggested_drop_player_name": top_drop.get("name"),
            "suggested_drop_position": top_drop.get("position"),
        })

    candidates = pd.DataFrame(rows, columns=WAIVER_COLUMNS)
    if not candidates.empty:
        # Preserve skill-player priorities over IDP projection volume; tie-break by score.
        candidates["_offense"] = candidates["position"].isin(["QB", "RB", "WR", "TE"]).map({True: 1, False: 0})
        candidates = candidates.sort_values(["_offense", "priority_score", "sleeper_player_id"], ascending=[False, False, True]).drop(columns="_offense")
        candidates["rank"] = range(1, len(candidates) + 1)
    else:
        candidates["rank"] = pd.Series(dtype="int64")

    summary_rows = []
    for tier in ("PRIORITY_ADD", "SPECULATIVE_STASH", "STREAMER"):
        subset = candidates[candidates["recommendation_tier"] == tier] if not candidates.empty else candidates
        summary_rows.append({
            "league_id": resolved_league_id, "season": str(league.get("season") or ""), "week": week,
            "generated_at_utc": now, "summary_type": tier, "candidate_count": int(len(subset)),
            "top_player_ids": ",".join(subset["sleeper_player_id"].astype(str).head(5)) if not subset.empty else "",
            "top_player_names": ",".join(subset["player_name"].astype(str).head(5)) if not subset.empty else "",
        })
    drop_ids = [row["id"] for row in drops[:5]]
    drop_names = [row["name"] for row in drops[:5]]
    summary_rows.append({
        "league_id": resolved_league_id, "season": str(league.get("season") or ""), "week": week,
        "generated_at_utc": now, "summary_type": "REALISTIC_DROP_CANDIDATES", "candidate_count": len(drops),
        "top_player_ids": ",".join(drop_ids), "top_player_names": ",".join(drop_names),
    })
    summary_rows.insert(0, {
        "league_id": resolved_league_id, "season": str(league.get("season") or ""), "week": week,
        "generated_at_utc": now, "summary_type": "AVAILABLE_POOL", "candidate_count": int(len(candidates)),
        "top_player_ids": ",".join(candidates["sleeper_player_id"].astype(str).head(5)) if not candidates.empty else "",
        "top_player_names": ",".join(candidates["player_name"].astype(str).head(5)) if not candidates.empty else "",
    })
    summary = pd.DataFrame(summary_rows)
    meta = {
        "status": "PASS" if not errors else "FAIL", "errors": errors,
        "generated_at_utc": now, "league_id": resolved_league_id, "season": str(league.get("season") or ""),
        "week": week, "target_roster_id": target_roster_id,
        "rostered_player_count": len(owned), "available_player_count": len(candidates),
        "waiver_pool_generated": bool(players) and not errors,
        "unique_candidate_ids": int(candidates["sleeper_player_id"].nunique()) if not candidates.empty else 0,
        "projection_candidate_count": int(candidates["projected_points"].notna().sum()) if not candidates.empty else 0,
        "projection_missing_count": int(candidates["projection_missing_flag"].sum()) if not candidates.empty else 0,
        "universe_source": "Sleeper /players/nfl minus current Sleeper /league/{league_id}/rosters",
        "projection_source": projection_source,
    }
    return candidates, summary, meta
