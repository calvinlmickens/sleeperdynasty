from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


ENRICHMENT_COLUMNS = [
    "sleeper_player_id",
    "week",
    "projected_points",
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


def load_enrichment(path: str | Path | None, week: int) -> tuple[pd.DataFrame, str]:
    """Load an optional standardized external weekly context file.

    The Sleeper pipeline owns league-state truth. This adapter only attaches football-
    context fields that Sleeper cannot reliably supply (projections, practice/news,
    game environment, role/ecosystem notes). Missing enrichment never mutates league
    state; it simply keeps the framework readiness gate closed.
    """
    if not path:
        return pd.DataFrame(columns=ENRICHMENT_COLUMNS), "NOT_CONFIGURED"
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(columns=ENRICHMENT_COLUMNS), "NOT_FOUND"
    df = pd.read_csv(p, dtype={"sleeper_player_id": str})
    for c in ENRICHMENT_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    if "week" in df.columns:
        wk = pd.to_numeric(df["week"], errors="coerce")
        df = df[(wk.isna()) | (wk == week)].copy()
    df["sleeper_player_id"] = df["sleeper_player_id"].astype(str)
    return df[ENRICHMENT_COLUMNS].drop_duplicates(subset=["sleeper_player_id"], keep="last"), "LOADED"


def _slot_by_starter_order(league: dict[str, Any]) -> dict[int, str]:
    positions = [str(x) for x in (league.get("roster_positions") or [])]
    starters = [p for p in positions if p != "BN"]
    return {i: slot for i, slot in enumerate(starters)}


def build_weekly_matchup_context(
    league: dict[str, Any],
    state: pd.DataFrame,
    target_roster_id: int,
    week: int,
    enrichment: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Derive the target team's live matchup view from validated Sleeper state."""
    if state.empty:
        return pd.DataFrame(), {"status": "FAIL", "reason": "league_state_empty"}

    target_rows = state[state["roster_id"] == target_roster_id]
    if target_rows.empty:
        return pd.DataFrame(), {"status": "FAIL", "reason": "target_roster_not_found"}
    matchup_id = target_rows["matchup_id"].iloc[0]
    pair = state[state["matchup_id"] == matchup_id].copy()
    roster_ids = sorted(pair["roster_id"].dropna().astype(int).unique().tolist())
    if len(roster_ids) != 2 or target_roster_id not in roster_ids:
        return pd.DataFrame(), {"status": "FAIL", "reason": f"matchup_pair_invalid:{roster_ids}"}
    opponent_roster_id = next(r for r in roster_ids if r != target_roster_id)

    slots = _slot_by_starter_order(league)
    pair["side"] = pair["roster_id"].map(lambda r: "TARGET" if int(r) == target_roster_id else "OPPONENT")
    pair["is_target_team"] = pair["roster_id"].astype(int) == target_roster_id
    pair["is_opponent_team"] = pair["roster_id"].astype(int) == opponent_roster_id
    pair["lineup_status"] = pair["is_starter"].map({True: "Starter", False: "Bench"})
    pair["lineup_slot"] = pair.apply(
        lambda r: slots.get(int(r["starter_order"]), "STARTER") if bool(r["is_starter"]) and pd.notna(r["starter_order"]) else "BN",
        axis=1,
    )

    if enrichment is not None and not enrichment.empty:
        pair = pair.merge(enrichment, on="sleeper_player_id", how="left", suffixes=("", "_enrich"))
    else:
        for c in ENRICHMENT_COLUMNS:
            if c not in pair.columns and c != "sleeper_player_id":
                pair[c] = pd.NA

    projection = pd.to_numeric(pair.get("projected_points"), errors="coerce")
    pair["projected_points_numeric"] = projection
    pair["projection_available"] = projection.notna()
    pair["effective_injury_status"] = pair.get("injury_status_external").combine_first(pair.get("injury_status"))

    ordered = [
        "pulled_at_utc", "season", "league_id", "week", "matchup_id", "side",
        "roster_id", "owner_id", "team_name", "is_target_team", "is_opponent_team",
        "sleeper_player_id", "player_name", "position", "fantasy_positions", "nfl_team",
        "lineup_slot", "lineup_status", "is_starter", "starter_order",
        "status", "injury_status", "injury_status_external", "effective_injury_status", "practice_status",
        "depth_chart_position", "depth_chart_order", "projected_points_numeric", "projection_available",
        "game_total", "team_total", "weather_flag", "role_change_flag", "ecosystem_note",
        "source_name", "source_timestamp_utc", "matchup_points",
    ]
    for c in ordered:
        if c not in pair.columns:
            pair[c] = pd.NA
    pair = pair[ordered].sort_values(["side", "is_starter", "starter_order", "player_name"], ascending=[False, False, True, True])

    target_name = target_rows["team_name"].iloc[0]
    opponent_name = state[state["roster_id"] == opponent_roster_id]["team_name"].iloc[0]
    meta = {
        "status": "PASS",
        "week": week,
        "matchup_id": matchup_id,
        "target_roster_id": target_roster_id,
        "target_team_name": target_name,
        "opponent_roster_id": opponent_roster_id,
        "opponent_team_name": opponent_name,
    }
    return pair.reset_index(drop=True), meta


def build_weekly_matchup_summary(context: pd.DataFrame, meta: dict[str, Any], enrichment_status: str) -> pd.DataFrame:
    if context.empty or meta.get("status") != "PASS":
        return pd.DataFrame([{"status": "FAIL", "detail": meta.get("reason", "matchup context unavailable")}])

    target = context[context["side"] == "TARGET"]
    opp = context[context["side"] == "OPPONENT"]

    def total(df: pd.DataFrame, starters: bool | None = None) -> float | None:
        x = df if starters is None else df[df["is_starter"] == starters]
        vals = pd.to_numeric(x["projected_points_numeric"], errors="coerce")
        if vals.notna().sum() == 0:
            return None
        return round(float(vals.sum(skipna=True)), 2)

    t_st = total(target, True)
    o_st = total(opp, True)
    t_bn = total(target, False)
    o_bn = total(opp, False)
    t_full = total(target, None)
    o_full = total(opp, None)

    def spread(a, b):
        return round(float(a - b), 2) if a is not None and b is not None else None

    t_top = target[~target["is_starter"]].copy()
    o_top = opp[~opp["is_starter"]].copy()
    t_top["_p"] = pd.to_numeric(t_top["projected_points_numeric"], errors="coerce")
    o_top["_p"] = pd.to_numeric(o_top["projected_points_numeric"], errors="coerce")
    t_top = t_top.sort_values("_p", ascending=False, na_position="last")
    o_top = o_top.sort_values("_p", ascending=False, na_position="last")

    projection_coverage = int(context["projection_available"].sum())
    projection_required = len(context)
    framework_ready = enrichment_status == "LOADED" and projection_coverage >= 22  # all starters at minimum

    row = {
        "status": "PASS",
        "generated_from": "validated_sleeper_state",
        "season": str(context["season"].iloc[0]),
        "league_id": str(context["league_id"].iloc[0]),
        "week": int(context["week"].iloc[0]),
        "matchup_id": meta["matchup_id"],
        "target_roster_id": meta["target_roster_id"],
        "target_team_name": meta["target_team_name"],
        "opponent_roster_id": meta["opponent_roster_id"],
        "opponent_team_name": meta["opponent_team_name"],
        "target_starter_count": int(target["is_starter"].sum()),
        "opponent_starter_count": int(opp["is_starter"].sum()),
        "target_bench_count": int((~target["is_starter"]).sum()),
        "opponent_bench_count": int((~opp["is_starter"]).sum()),
        "target_current_score": float(target["matchup_points"].dropna().iloc[0]) if target["matchup_points"].notna().any() else None,
        "opponent_current_score": float(opp["matchup_points"].dropna().iloc[0]) if opp["matchup_points"].notna().any() else None,
        "target_starter_projected_total": t_st,
        "opponent_starter_projected_total": o_st,
        "starter_projected_spread_target_minus_opp": spread(t_st, o_st),
        "target_bench_projected_total": t_bn,
        "opponent_bench_projected_total": o_bn,
        "bench_projection_advantage_target_minus_opp": spread(t_bn, o_bn),
        "target_full_roster_projected_total": t_full,
        "opponent_full_roster_projected_total": o_full,
        "full_roster_projected_spread_target_minus_opp": spread(t_full, o_full),
        "target_top_bench_player": t_top.iloc[0]["player_name"] if len(t_top) and pd.notna(t_top.iloc[0]["_p"]) else None,
        "target_top_bench_projection": float(t_top.iloc[0]["_p"]) if len(t_top) and pd.notna(t_top.iloc[0]["_p"]) else None,
        "opponent_top_bench_player": o_top.iloc[0]["player_name"] if len(o_top) and pd.notna(o_top.iloc[0]["_p"]) else None,
        "opponent_top_bench_projection": float(o_top.iloc[0]["_p"]) if len(o_top) and pd.notna(o_top.iloc[0]["_p"]) else None,
        "enrichment_status": enrichment_status,
        "projection_coverage_rows": projection_coverage,
        "projection_context_rows": projection_required,
        "framework_ready": framework_ready,
        "framework_gate": "OPEN_FOR_CPI_WUS_FLEX" if framework_ready else "HOLD_EXTERNAL_INTELLIGENCE_REQUIRED",
    }
    return pd.DataFrame([row])


def write_framework_packet(path: str | Path, context: pd.DataFrame, summary: pd.DataFrame) -> None:
    path = Path(path)
    row = summary.iloc[0].to_dict() if not summary.empty else {}
    lines = [
        "# Dynasty Framework Matchup Packet — Sleeper Input Layer",
        "",
        f"Season: {row.get('season', '')}",
        f"Week: {row.get('week', '')}",
        f"League ID: {row.get('league_id', '')}",
        "",
        "## Platform-State Validation",
        f"- Source: validated Sleeper league state",
        f"- Matchup: {row.get('target_team_name', '')} vs {row.get('opponent_team_name', '')}",
        f"- Target roster ID: {row.get('target_roster_id', '')}",
        f"- Opponent roster ID: {row.get('opponent_roster_id', '')}",
        f"- Framework gate: **{row.get('framework_gate', '')}**",
        f"- External enrichment: {row.get('enrichment_status', '')}",
        "",
        "## Projection Context",
    ]
    if row.get("framework_ready"):
        lines += [
            f"- Target starter projected total: {row.get('target_starter_projected_total')}",
            f"- Opponent starter projected total: {row.get('opponent_starter_projected_total')}",
            f"- Starter spread (target - opponent): {row.get('starter_projected_spread_target_minus_opp')}",
        ]
    else:
        lines += [
            "- Projection/enrichment adapter is not sufficiently populated.",
            "- CPI, WUS, FLEX, Ecosystem Confidence, and final lineup construction must remain on HOLD.",
        ]

    for side, title in [("TARGET", "Target Starters"), ("OPPONENT", "Opponent Starters")]:
        lines += ["", f"## {title}"]
        sub = context[(context["side"] == side) & (context["is_starter"])].sort_values("starter_order")
        for _, r in sub.iterrows():
            proj = r.get("projected_points_numeric")
            proj_text = f" — proj {proj:.2f}" if pd.notna(proj) else ""
            injury = r.get("effective_injury_status")
            injury_text = f" — {injury}" if pd.notna(injury) and str(injury).strip() else ""
            lines.append(f"- {r.get('lineup_slot')}: {r.get('player_name')} ({r.get('position')}){proj_text}{injury_text}")

    lines += [
        "",
        "## Governance Note",
        "This packet is an input-layer product only. It does not assign CPI, WUS, FLEX posture, strategy mode, or final lineup decisions until current external football intelligence has been attached and the framework gate is open.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
