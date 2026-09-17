from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
from uuid import uuid4

from .client import EspnFantasyClient, load_fixture
from .config import KeeperEspnConfig
from .phase2 import build_keeper_state, build_player_pool_state, enrich_roster_keeper_fields
from .phase3 import build_delta_state, load_validated_snapshot
from .pipeline import (
    ET,
    SCHEMA_VERSION,
    _now_et,
    _write_json,
    build_league_state,
    build_matchup_state,
    build_roster_state,
    validate_phase1,
)
from .pipeline_phase2 import apply_league_ir_rule, validate_phase2


def _replace_snapshot(source_dir: Path, target_dir: Path) -> None:
    staging = target_dir.parent / f".{target_dir.name}-staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source_dir, staging)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    staging.replace(target_dir)


def run_refresh(
    *,
    output_dir: str | Path = "keeper_output",
    fixture_dir: str | Path | None = None,
    season: int | None = None,
) -> dict:
    config = KeeperEspnConfig.from_env(season=season)
    pulled_at = _now_et()
    run_id = f"{datetime.now(ET).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    root = Path(output_dir)
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    last_known_good = root / "last_known_good"
    previous = load_validated_snapshot(last_known_good)

    try:
        pull = load_fixture(fixture_dir) if fixture_dir else EspnFantasyClient(config).pull_league()
        league_state = build_league_state(pull, config)
        week = int(league_state["current_week"])
        roster_state = build_roster_state(pull, config, week=week)
        apply_league_ir_rule(league_state=league_state, roster_state=roster_state)
        roster_state = enrich_roster_keeper_fields(roster_state, pull)
        matchup_state = build_matchup_state(pull, config, week=week)
        keeper_state = build_keeper_state(roster_state, pull)
        player_pool_state = build_player_pool_state(pull, week=week)

        errors = validate_phase1(
            config=config,
            league_state=league_state,
            roster_state=roster_state,
            matchup_state=matchup_state,
        )
        errors.extend(
            validate_phase2(
                keeper_state=keeper_state,
                player_pool_state=player_pool_state,
                roster_state=roster_state,
            )
        )
        status = "PASS" if not errors else "FAIL"

        previous_run_id = previous["manifest"].get("run_id") if previous else None
        delta_state = build_delta_state(
            current_run_id=run_id,
            team_id=config.team_id,
            league_state=league_state,
            roster_state=roster_state,
            matchup_state=matchup_state,
            keeper_state=keeper_state,
            player_pool_state=player_pool_state,
            previous=previous,
        )

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
            "previous_validated_run_id": previous_run_id,
            "validation_status": status,
            "validation_errors": errors,
            "data_freshness_status": "CURRENT",
            "source_status": {
                "espn": "CURRENT",
                "league_source": pull.source,
                "player_pool_source": pull.player_pool_source,
            },
            "outputs": [
                "league_state.json",
                "roster_state.json",
                "matchup_state.json",
                "keeper_state.json",
                "player_pool.json",
                "delta_state.json",
            ],
            "last_known_good_policy": "PASS_ONLY_PROMOTION",
        }

        _write_json(run_dir / "manifest.json", manifest)
        _write_json(run_dir / "league_state.json", league_state)
        _write_json(run_dir / "roster_state.json", roster_state)
        _write_json(run_dir / "matchup_state.json", matchup_state)
        _write_json(run_dir / "keeper_state.json", keeper_state)
        _write_json(run_dir / "player_pool.json", player_pool_state)
        _write_json(run_dir / "delta_state.json", delta_state)

        if errors:
            diagnostic_dir = root / "diagnostics" / run_id
            diagnostic_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(run_dir, diagnostic_dir)
            return {
                "status": "FAIL",
                "run_id": run_id,
                "errors": errors,
                "run_dir": str(run_dir),
                "last_known_good_preserved": previous is not None,
            }

        latest = root / "latest"
        _replace_snapshot(run_dir, latest)
        _replace_snapshot(run_dir, last_known_good)

        return {
            "status": "PASS",
            "run_id": run_id,
            "previous_validated_run_id": previous_run_id,
            "week": week,
            "team_name": roster_state.get("team_name"),
            "opponent": matchup_state.get("opponent_team_name"),
            "player_pool_count": player_pool_state.get("player_count"),
            "material_change": delta_state.get("material_change"),
            "latest_dir": str(latest),
            "last_known_good_dir": str(last_known_good),
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
            "previous_validated_run_id": (
                previous["manifest"].get("run_id") if previous else None
            ),
            "validation_status": "FAIL",
            "validation_errors": [f"{type(exc).__name__}: {exc}"],
            "data_freshness_status": "MISSING",
            "source_status": {"espn": "FAILED"},
            "last_known_good_policy": "PASS_ONLY_PROMOTION",
        }
        _write_json(run_dir / "manifest.json", manifest)
        diagnostic_dir = root / "diagnostics" / run_id
        diagnostic_dir.parent.mkdir(parents=True, exist_ok=True)
        if not diagnostic_dir.exists():
            shutil.copytree(run_dir, diagnostic_dir)
        return {
            "status": "FAIL",
            "run_id": run_id,
            "errors": manifest["validation_errors"],
            "run_dir": str(run_dir),
            "last_known_good_preserved": previous is not None,
        }
