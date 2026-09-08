from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


OUTPUT_COLUMNS = [
    "sleeper_player_id",
    "week",
    "projected_points",
    "baseline_projection",
    "league_adjusted_projection",
    "projection_method",
    "projection_scoring_keys_used",
    "projection_warning",
    "injury_status_external",
    "practice_status",
    "game_total",
    "team_total",
    "weather_flag",
    "role_change_flag",
    "ecosystem_note",
    "source_name",
    "source_timestamp_utc",
]


def _float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _projection_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalize the known Sleeper projection response shapes.

    The endpoint is not part of Sleeper's core documented league API, so this
    parser intentionally accepts both list and id-keyed dictionary payloads.
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows: list[dict[str, Any]] = []
        for pid, value in payload.items():
            if not isinstance(value, dict):
                continue
            row = dict(value)
            row.setdefault("player_id", str(pid))
            rows.append(row)
        return rows
    return []


def _baseline_projection(stats: dict[str, Any], row: dict[str, Any]) -> float | None:
    # Prefer direct fantasy-point fields if Sleeper supplies them. They are a
    # fallback/reference only; the league-adjusted score is preferred below.
    for key in ("pts_ppr", "pts_half_ppr", "pts_std", "fantasy_points", "projected_points"):
        v = _float(stats.get(key))
        if v is not None:
            return v
        v = _float(row.get(key))
        if v is not None:
            return v
    return None


def _league_adjusted_projection(stats: dict[str, Any], scoring: dict[str, Any]) -> tuple[float | None, list[str]]:
    total = 0.0
    used: list[str] = []
    for key, weight_raw in scoring.items():
        weight = _float(weight_raw)
        stat = _float(stats.get(key))
        if weight is None or weight == 0 or stat is None:
            continue
        total += weight * stat
        used.append(str(key))
    if not used:
        return None, []
    return round(total, 4), sorted(used)


def build_auto_player_week_context(
    league: dict[str, Any],
    players: dict[str, dict[str, Any]],
    projection_payload: Any,
    week: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the automatically retrievable part of weekly enrichment.

    Automated here:
      * Sleeper weekly projections (undocumented projection endpoint)
      * league-scoring directional recalculation from projected stat components
      * Sleeper injury/practice/depth-chart state

    Deliberately NOT fabricated here:
      * Vegas/team totals
      * weather
      * beat-writer/role-change intelligence
      * ecosystem-confidence judgments

    Those remain a separate live-intelligence layer.
    """
    rows = _projection_rows(projection_payload)
    scoring = league.get("scoring_settings") or {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row.get("player_id") or (row.get("player") or {}).get("player_id")
        if pid is not None:
            by_id[str(pid)] = row

    now = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    projection_count = 0
    adjusted_count = 0

    for pid, player in players.items():
        row = by_id.get(str(pid), {})
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else row
        if not isinstance(stats, dict):
            stats = {}
        baseline = _baseline_projection(stats, row)
        adjusted, keys_used = _league_adjusted_projection(stats, scoring)
        preferred = adjusted if adjusted is not None else baseline
        if preferred is not None:
            projection_count += 1
        if adjusted is not None:
            adjusted_count += 1

        warning = ""
        if row and adjusted is None and baseline is not None:
            warning = "League-adjusted stat components unavailable; using Sleeper baseline fantasy projection."
        elif row and adjusted is not None:
            warning = "Directional custom-scoring projection; threshold/long-play bonuses only contribute when Sleeper projects matching stat keys."
        elif not row:
            warning = "No Sleeper weekly projection row returned."

        out.append({
            "sleeper_player_id": str(pid),
            "week": week,
            "projected_points": preferred,
            "baseline_projection": baseline,
            "league_adjusted_projection": adjusted,
            "projection_method": "league_adjusted_sleeper_stats" if adjusted is not None else ("sleeper_baseline" if baseline is not None else "none"),
            "projection_scoring_keys_used": ",".join(keys_used),
            "projection_warning": warning,
            "injury_status_external": player.get("injury_status"),
            "practice_status": player.get("practice_participation"),
            "game_total": pd.NA,
            "team_total": pd.NA,
            "weather_flag": pd.NA,
            "role_change_flag": pd.NA,
            "ecosystem_note": pd.NA,
            "source_name": "Sleeper projection endpoint + Sleeper player state",
            "source_timestamp_utc": now,
        })

    df = pd.DataFrame(out, columns=OUTPUT_COLUMNS)
    meta = {
        "status": "LOADED" if projection_count else "NO_PROJECTIONS",
        "projection_rows_received": len(rows),
        "projection_players_available": projection_count,
        "league_adjusted_players_available": adjusted_count,
        "source": "Sleeper weekly projections (undocumented endpoint) + public player state",
        "warning": "Projection endpoint is not part of Sleeper's documented core league API; refresh degrades safely if unavailable.",
    }
    return df, meta
