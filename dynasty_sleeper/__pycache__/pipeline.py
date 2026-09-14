from __future__ import annotations


import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


import pandas as pd


from .client import SleeperClient
from .config import DEFAULT_CONFIG, LeagueConfig
from .normalize import normalize_league_state, normalize_transactions
from .reconcile import reconcile_snapshot, critical_failures, reconcile_roster_delta
from .matchup import load_enrichment, build_weekly_matchup_context, build_weekly_matchup_summary, write_framework_packet
from .enrichment import build_auto_player_week_context




def merge_transaction_master(master_path: Path, current: pd.DataFrame) -> pd.DataFrame:
    if master_path.exists() and master_path.stat().st_size > 0:
        old = pd.read_csv(master_path, dtype={"transaction_id": str, "player_id": str})
        combined = pd.concat([old, current], ignore_index=True)
    else:
        combined = current.copy()
    if combined.empty:
        return combined
    keys = ["transaction_id", "action", "player_id", "roster_id", "pick_season", "pick_round", "pick_original_roster_id", "pick_owner_id"]
    return combined.drop_duplicates(subset=[k for k in keys if k in combined.columns], keep="last")




def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)




def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()




def _roster_summary(state: pd.DataFrame) -> pd.DataFrame:
    if state.empty:
        return pd.DataFrame()
    return (
        state.groupby(["league_id", "roster_id", "owner_id", "team_name", "waiver_position"], dropna=False)
        .agg(rostered_players=("sleeper_player_id", "nunique"), starters=("is_starter", "sum"), matchup_id=("matchup_id", "first"))
        .reset_index()
        .sort_values("roster_id")
    )




def _find_target_roster(summary: pd.DataFrame, target_team_name: str) -> int | None:
    if summary.empty or not target_team_name:
        return None
    exact = summary[summary["team_name"].astype(str).str.casefold() == target_team_name.casefold()]
    if len(exact) == 1:
        return int(exact.iloc[0]["roster_id"])
    return None






def _number_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None




def _build_final_week_snapshot(
    *,
    league: dict[str, Any],
    week: int,
    pulled_at: str,
    matchups: list[dict[str, Any]],
    matchup_meta: dict[str, Any],
    pregame_context_path: str | Path,
) -> dict[str, Any]:
    """Join final Sleeper scoring to the last pregame lineup/projection context."""
    pregame_path = Path(pregame_context_path)
    if not pregame_path.exists() or pregame_path.stat().st_size == 0:
        raise RuntimeError(f"Pregame matchup context not found: {pregame_path}")


    context = pd.read_csv(pregame_path, dtype={"sleeper_player_id": str})
    if context.empty:
        raise RuntimeError("Pregame matchup context is empty")
    context_week = pd.to_numeric(context.get("week"), errors="coerce").dropna().astype(int).unique().tolist()
    if context_week != [week]:
        raise RuntimeError(f"Pregame context week mismatch: expected {week}, found {context_week}")


    target_roster_id = int(matchup_meta["target_roster_id"])
    opponent_roster_id = int(matchup_meta["opponent_roster_id"])
    matchup_by_roster = {
        int(row["roster_id"]): row
        for row in matchups
        if row.get("roster_id") is not None
    }
    missing = [rid for rid in (target_roster_id, opponent_roster_id) if rid not in matchup_by_roster]
    if missing:
        raise RuntimeError(f"Final Sleeper matchup rows missing for roster IDs: {missing}")


    def side_payload(roster_id: int, side: str) -> dict[str, Any]:
        matchup_row = matchup_by_roster[roster_id]
        actuals = {
            str(player_id): _number_or_none(points)
            for player_id, points in (matchup_row.get("players_points") or {}).items()
        }
        rows = context[context["roster_id"].astype(int) == roster_id].copy()
        rows["_starter_sort"] = pd.to_numeric(rows.get("starter_order"), errors="coerce")
        rows = rows.sort_values(
            ["is_starter", "_starter_sort", "player_name"],
            ascending=[False, True, True],
            na_position="last",
        )


        players: list[dict[str, Any]] = []
        for _, row in rows.iterrows():
            player_id = str(row["sleeper_player_id"])
            players.append(
                {
                    "sleeper_player_id": player_id,
                    "player_name": row.get("player_name"),
                    "position": row.get("position"),
                    "nfl_team": row.get("nfl_team"),
                    "lineup_slot": row.get("lineup_slot"),
                    "lineup_status": row.get("lineup_status"),
                    "is_starter": bool(row.get("is_starter")),
                    "starter_order": _number_or_none(row.get("starter_order")),
                    "pregame_projected_points": _number_or_none(row.get("projected_points_numeric")),
                    "actual_fantasy_points": actuals.get(player_id),
                }
            )


        starters = [player for player in players if player["is_starter"]]
        bench = [player for player in players if not player["is_starter"]]
        starter_actual_total = round(
            sum(player["actual_fantasy_points"] or 0.0 for player in starters),
            2,
        )
        return {
            "side": side,
            "roster_id": roster_id,
            "team_name": rows["team_name"].iloc[0] if not rows.empty else None,
            "final_score": _number_or_none(matchup_row.get("points")),
            "starter_actual_total": starter_actual_total,
            "starters": starters,
            "bench": bench,
        }


    target = side_payload(target_roster_id, "TARGET")
    opponent = side_payload(opponent_roster_id, "OPPONENT")
    target_score = target["final_score"]
    opponent_score = opponent["final_score"]
    result = (
        "WIN" if target_score is not None and opponent_score is not None and target_score > opponent_score
        else "LOSS" if target_score is not None and opponent_score is not None and target_score < opponent_score
        else "TIE" if target_score is not None and opponent_score is not None
        else "UNKNOWN"
    )


    return {
        "schema_version": "1.0",
        "snapshot_type": "FINAL_WEEK",
        "status": "PASS",
        "captured_at_utc": pulled_at,
        "projection_source": "last_pregame_weekly_matchup_context",
        "projection_captured_at_utc": context["pulled_at_utc"].dropna().iloc[0] if context["pulled_at_utc"].notna().any() else None,
        "season": str(league.get("season") or ""),
        "week": week,
        "league_id": str(league.get("league_id") or ""),
        "matchup_id": matchup_meta.get("matchup_id"),
        "result": result,
        "final_score": {
            "target": target_score,
            "opponent": opponent_score,
            "target_minus_opponent": round(target_score - opponent_score, 2) if target_score is not None and opponent_score is not None else None,
        },
        "target": target,
        "opponent": opponent,
    }




def run_refresh(
    week: int,
    output_dir: str | Path = "output",
    config: LeagueConfig = DEFAULT_CONFIG,
    fixture_dir: str | Path | None = None,
    pull_players: bool = True,
    previous_state: str | Path | None = None,
    enrichment_file: str | Path | None = None,
    finalize_week: bool = False,
    pregame_context: str | Path | None = None,
) -> dict[str, Path | list[str] | int | None]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = SleeperClient(fixture_dir=fixture_dir)
    pulled_at = datetime.now(timezone.utc).isoformat()


    league = client.league(config.league_id)
    users = client.users(config.league_id)
    rosters = client.rosters(config.league_id)
    matchups = client.matchups(config.league_id, week)
    traded_picks = client.traded_picks(config.league_id)
    players = client.players_nfl() if pull_players else {}


    projection_payload: Any = {}
    projection_fetch_status = "NOT_ATTEMPTED"
    projection_fetch_error: str | None = None
    if enrichment_file is None:
        try:
            projection_payload = client.weekly_projections(league.get("season") or 2026, week)
            projection_fetch_status = "PASS"
        except Exception as exc:
            # Projection availability must never invalidate otherwise trusted league state.
            projection_fetch_status = "FAIL_NONFATAL"
            projection_fetch_error = str(exc)


    # Pull season-to-date transaction rounds so a baseline snapshot can be reconciled
    # against any completed move observed in current ownership.
    tx_by_week: dict[int, list[dict[str, Any]]] = {}
    for tx_week in range(1, week + 1):
        tx_by_week[tx_week] = client.transactions(config.league_id, tx_week)


    _write_json(raw_dir / "league.json", league)
    _write_json(raw_dir / "users.json", users)
    _write_json(raw_dir / "rosters.json", rosters)
    _write_json(raw_dir / f"matchups_week_{week}.json", matchups)
    _write_json(raw_dir / "traded_picks.json", traded_picks)
    _write_json(raw_dir / "players_nfl.json", players)
    if enrichment_file is None:
        _write_json(raw_dir / f"projections_week_{week}.json", projection_payload)
    for tx_week, payload in tx_by_week.items():
        _write_json(raw_dir / f"transactions_week_{tx_week}.json", payload)


    state = normalize_league_state(league, users, rosters, players, matchups, week)
    tx_frames = [normalize_transactions(payload) for payload in tx_by_week.values()]
    tx_current = pd.concat(tx_frames, ignore_index=True) if tx_frames else pd.DataFrame()
    report = reconcile_snapshot(league, rosters, state, config, matchups)
    failures = critical_failures(report)
    summary = _roster_summary(state)
    target_roster_id = _find_target_roster(summary, config.target_team_name)


    state_path = output_dir / "league_state_current.csv"
    summary_path = output_dir / "roster_summary_current.csv"
    tx_current_path = output_dir / "league_transactions_current.csv"
    tx_master_path = output_dir / "league_transactions_master.csv"
    report_path = output_dir / "reconciliation_report.csv"
    picks_path = output_dir / "traded_picks_current.csv"
    delta_path = output_dir / "roster_delta_reconciliation.csv"
    manifest_path = output_dir / "manifest.json"
    matchup_context_path = output_dir / "weekly_matchup_context.csv"
    matchup_summary_path = output_dir / "weekly_matchup_summary.csv"
    framework_packet_path = output_dir / "framework_matchup_packet.md"
    player_week_context_path = output_dir / "player_week_context.csv"
    final_snapshot_path = output_dir / f"week_{week}_final_snapshot.json" if finalize_week else None


    state.to_csv(state_path, index=False)
    summary.to_csv(summary_path, index=False)
    tx_current.to_csv(tx_current_path, index=False)
    master = merge_transaction_master(tx_master_path, tx_current)
    master.to_csv(tx_master_path, index=False)
    report.to_csv(report_path, index=False)
    picks_df = pd.DataFrame(traded_picks)
    if picks_df.empty:
        picks_df = pd.DataFrame(columns=["season", "round", "roster_id", "previous_owner_id", "owner_id"])
    picks_df.to_csv(picks_path, index=False)


    baseline_status = "ESTABLISHED_THIS_RUN"
    delta_failures: list[str] = []
    if previous_state:
        previous_path = Path(previous_state)
        if previous_path.exists() and previous_path.stat().st_size > 0:
            previous = pd.read_csv(previous_path, dtype={"sleeper_player_id": str})
            delta_report = reconcile_roster_delta(previous, state, tx_current)
            baseline_status = "COMPARED_TO_PREVIOUS"
            if not delta_report.empty:
                delta_failures = delta_report.loc[delta_report["status"] == "FAIL", "sleeper_player_id"].astype(str).tolist()
        else:
            delta_report = pd.DataFrame([{"status": "BASELINE", "detail": "previous state path not found; current snapshot establishes baseline"}])
    else:
        delta_report = pd.DataFrame([{"status": "BASELINE", "detail": "no previous state supplied; current snapshot establishes baseline"}])
    delta_report.to_csv(delta_path, index=False)


    if enrichment_file:
        enrichment, enrichment_status = load_enrichment(enrichment_file, week)
        enrichment_meta = {"status": enrichment_status, "source": "provided_enrichment_file"}
    else:
        enrichment, enrichment_meta = build_auto_player_week_context(league, players, projection_payload, week)
        enrichment_status = "AUTO_LOADED" if enrichment_meta.get("status") == "LOADED" else str(enrichment_meta.get("status"))
    enrichment.to_csv(player_week_context_path, index=False)


    matchup_context, matchup_meta = build_weekly_matchup_context(league, state, target_roster_id, week, enrichment) if target_roster_id is not None else (pd.DataFrame(), {"status": "FAIL", "reason": "target_roster_unresolved"})
    matchup_context.to_csv(matchup_context_path, index=False)
    matchup_summary = build_weekly_matchup_summary(matchup_context, matchup_meta, enrichment_status)
    matchup_summary.to_csv(matchup_summary_path, index=False)
    write_framework_packet(framework_packet_path, matchup_context, matchup_summary)


    if finalize_week:
        if matchup_meta.get("status") != "PASS":
            raise RuntimeError("Cannot finalize week because matchup derivation did not pass")
        if pregame_context is None:
            raise RuntimeError("--pregame-context is required when --finalize-week is used")
        final_snapshot = _build_final_week_snapshot(
            league=league,
            week=week,
            pulled_at=pulled_at,
            matchups=matchups,
            matchup_meta=matchup_meta,
            pregame_context_path=pregame_context,
        )
        _write_json(final_snapshot_path, final_snapshot)


    overall_status = "PASS" if not failures and not delta_failures and matchup_meta.get("status") == "PASS" else "FAIL"
    manifest = {
        "schema_version": "0.7",
        "generated_at_utc": pulled_at,
        "overall_status": overall_status,
        "league_id": config.league_id,
        "league_name_expected": config.league_name,
        "league_name_resolved": league.get("name"),
        "season": league.get("season"),
        "week": week,
        "target_team_name": config.target_team_name,
        "target_roster_id": target_roster_id,
        "target_roster_resolution": "PASS" if target_roster_id is not None else "REVIEW_REQUIRED",
        "baseline_status": baseline_status,
        "counts": {
            "users": len(users),
            "rosters": len(rosters),
            "rostered_player_rows": int(len(state)),
            "unique_rostered_players": int(state["sleeper_player_id"].nunique()) if not state.empty else 0,
            "matchup_rows": len(matchups),
            "transaction_action_rows": int(len(tx_current)),
            "traded_picks": len(traded_picks),
            "player_dictionary": len(players),
        },
        "critical_reconciliation_failures": failures,
        "unreconciled_roster_delta_player_ids": delta_failures,
        "matchup_derivation_status": matchup_meta.get("status"),
        "opponent_roster_id": matchup_meta.get("opponent_roster_id"),
        "opponent_team_name": matchup_meta.get("opponent_team_name"),
        "external_enrichment_status": enrichment_status,
        "projection_fetch_status": projection_fetch_status,
        "projection_fetch_error": projection_fetch_error,
        "projection_enrichment_meta": enrichment_meta,
        "framework_ready": bool(matchup_summary.iloc[0].get("framework_ready", False)) if not matchup_summary.empty else False,
        "framework_gate": matchup_summary.iloc[0].get("framework_gate") if not matchup_summary.empty else "HOLD_MATCHUP_DERIVATION_FAILED",
        "final_snapshot_status": "PASS" if final_snapshot_path is not None else "NOT_REQUESTED",
        "files": {},
    }


    manifest_files = [state_path, summary_path, tx_current_path, tx_master_path, report_path, picks_path, delta_path, player_week_context_path, matchup_context_path, matchup_summary_path, framework_packet_path]
    if final_snapshot_path is not None:
        manifest_files.append(final_snapshot_path)
    for p in manifest_files:
        manifest["files"][p.name] = {"sha256": _sha256(p), "bytes": p.stat().st_size}
    _write_json(manifest_path, manifest)


    return {
        "league_state": state_path,
        "roster_summary": summary_path,
        "transactions_current": tx_current_path,
        "transactions_master": tx_master_path,
        "traded_picks": picks_path,
        "reconciliation": report_path,
        "roster_delta_reconciliation": delta_path,
        "manifest": manifest_path,
        "weekly_matchup_context": matchup_context_path,
        "weekly_matchup_summary": matchup_summary_path,
        "framework_matchup_packet": framework_packet_path,
        "player_week_context": player_week_context_path,
        "final_week_snapshot": final_snapshot_path,
        "target_roster_id": target_roster_id,
        "critical_failures": failures + (["UNRECONCILED_ROSTER_DELTA"] if delta_failures else []),
    }