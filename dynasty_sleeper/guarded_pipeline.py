from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd

from . import pipeline


def _starter_slots(league: dict[str, Any]) -> list[str]:
    return [
        str(slot)
        for slot in (league.get("roster_positions") or [])
        if str(slot).upper() not in {"BN", "IR", "TAXI"}
    ]


def _align_pregame_context_to_weekly_starters(
    *,
    league: dict[str, Any],
    week: int,
    matchups: list[dict[str, Any]],
    matchup_meta: dict[str, Any],
    pregame_context_path: str | Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Reconcile pregame context to Sleeper's actual weekly starter arrays.

    Pregame projections remain untouched. Only lineup identity/order/slot fields are
    reconciled to the requested week's matchup endpoint so final recap outputs cannot
    inherit a later week's current-roster starter array.
    """
    path = Path(pregame_context_path)
    context = pd.read_csv(path, dtype={"sleeper_player_id": str})
    if context.empty:
        raise RuntimeError("Pregame matchup context is empty")

    context_week = pd.to_numeric(context.get("week"), errors="coerce").dropna().astype(int).unique().tolist()
    if context_week != [week]:
        raise RuntimeError(f"Pregame context week mismatch: expected {week}, found {context_week}")

    matchup_by_roster = {
        int(row["roster_id"]): row
        for row in matchups
        if row.get("roster_id") is not None
    }
    roster_ids = [int(matchup_meta["target_roster_id"]), int(matchup_meta["opponent_roster_id"])]
    slots = _starter_slots(league)
    validation_rows: list[dict[str, Any]] = []

    for roster_id in roster_ids:
        if roster_id not in matchup_by_roster:
            raise RuntimeError(f"Final Sleeper matchup row missing for roster ID {roster_id}")

        matchup_row = matchup_by_roster[roster_id]
        raw_starters = [str(x) for x in (matchup_row.get("starters") or []) if x is not None]
        expected = {
            player_id: idx
            for idx, player_id in enumerate(raw_starters)
            if player_id and player_id != "0"
        }
        if len(expected) != len([x for x in raw_starters if x and x != "0"]):
            raise RuntimeError(f"Duplicate starter IDs in Sleeper matchup array for roster {roster_id}")

        roster_mask = pd.to_numeric(context["roster_id"], errors="coerce") == roster_id
        roster_rows = context[roster_mask]
        context_ids = set(roster_rows["sleeper_player_id"].astype(str))
        missing = sorted(set(expected) - context_ids)
        if missing:
            raise RuntimeError(
                f"Pregame context cannot be reconciled to Sleeper starters for roster {roster_id}; missing player IDs: {missing}"
            )

        context.loc[roster_mask, "is_starter"] = context.loc[roster_mask, "sleeper_player_id"].astype(str).isin(expected)
        context.loc[roster_mask, "starter_order"] = context.loc[roster_mask, "sleeper_player_id"].astype(str).map(expected)
        context.loc[roster_mask, "lineup_status"] = context.loc[roster_mask, "is_starter"].map({True: "Starter", False: "Bench"})

        def slot_for_player(player_id: str) -> str:
            order = expected.get(str(player_id))
            if order is None:
                return "BN"
            if order >= len(slots):
                raise RuntimeError(
                    f"Sleeper starter index {order} exceeds configured starter slots for roster {roster_id}"
                )
            return slots[order]

        context.loc[roster_mask, "lineup_slot"] = context.loc[roster_mask, "sleeper_player_id"].astype(str).map(slot_for_player)

        generated = {
            str(row["sleeper_player_id"]): int(row["starter_order"])
            for _, row in context[roster_mask & (context["is_starter"] == True)].iterrows()  # noqa: E712
        }
        if generated != expected:
            raise RuntimeError(
                f"Finalization starter reconciliation failed for roster {roster_id}: expected={expected} generated={generated}"
            )

        validation_rows.append({
            "roster_id": roster_id,
            "status": "PASS",
            "starter_count": len(expected),
            "starter_ids": list(expected),
        })

    return context, {
        "status": "PASS",
        "source": "Sleeper weekly matchup starters",
        "rosters": validation_rows,
    }


def _guarded_final_week_snapshot(
    *,
    league: dict[str, Any],
    week: int,
    pulled_at: str,
    matchups: list[dict[str, Any]],
    matchup_meta: dict[str, Any],
    pregame_context_path: str | Path,
) -> dict[str, Any]:
    aligned, validation = _align_pregame_context_to_weekly_starters(
        league=league,
        week=week,
        matchups=matchups,
        matchup_meta=matchup_meta,
        pregame_context_path=pregame_context_path,
    )

    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", suffix=".csv", prefix="starter-aligned-", delete=False, encoding="utf-8") as tmp:
            temp_path = Path(tmp.name)
            aligned.to_csv(tmp, index=False)

        snapshot = _ORIGINAL_BUILD_FINAL_WEEK_SNAPSHOT(
            league=league,
            week=week,
            pulled_at=pulled_at,
            matchups=matchups,
            matchup_meta=matchup_meta,
            pregame_context_path=temp_path,
        )
        snapshot["starter_assignment_validation"] = validation
        return snapshot
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


_ORIGINAL_BUILD_FINAL_WEEK_SNAPSHOT = pipeline._build_final_week_snapshot
pipeline._build_final_week_snapshot = _guarded_final_week_snapshot


def run_refresh(*args: Any, **kwargs: Any):
    return pipeline.run_refresh(*args, **kwargs)
