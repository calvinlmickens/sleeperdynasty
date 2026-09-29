from __future__ import annotations

import json
import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

# Fixture mode does not make HTTP requests. Keep this test runnable in the
# minimal automation environment where the declared `requests` dependency may
# not have been installed, while production continues to use the real package.
if importlib.util.find_spec("requests") is None:
    requests_stub = types.ModuleType("requests")
    class _Session:
        def __init__(self):
            self.headers = {}
    requests_stub.Session = _Session
    sys.modules["requests"] = requests_stub

from dynasty_sleeper.config import DEFAULT_CONFIG
from dynasty_sleeper.drive_sync import PERSISTENT_FILES
from dynasty_sleeper.guarded_pipeline import run_refresh
from dynasty_sleeper.waiver import build_waiver_watch


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"


class SleeperWaiverWatchTests(unittest.TestCase):
    def test_empty_sleeper_player_universe_reports_actionable_failure(self):
        watch, summary, meta = build_waiver_watch(
            league={"league_id": DEFAULT_CONFIG.league_id, "season": "2026"},
            week=3,
            league_id=DEFAULT_CONFIG.league_id,
            rosters=[{"roster_id": 10, "players": ["owned-1"]}],
            players={},
            projection_payload={},
            target_roster_id=10,
            pulled_at="2026-09-29T07:00:00+00:00",
            projection_source="Sleeper weekly projections endpoint",
        )
        self.assertEqual(meta["status"], "FAIL")
        self.assertFalse(meta["waiver_pool_generated"])
        self.assertIn("player universe is empty", " ".join(meta["errors"]))
        self.assertTrue(watch.empty)
        self.assertFalse(summary.empty)

    def test_nullable_enrichment_metadata_falls_back_to_refresh_timestamp(self):
        watch, _, meta = build_waiver_watch(
            league={"league_id": DEFAULT_CONFIG.league_id, "season": "2026"},
            week=3,
            league_id=DEFAULT_CONFIG.league_id,
            rosters=[{"roster_id": 1, "players": ["rostered-player"]}],
            players={"free-wr": {"full_name": "Sleeper WR", "fantasy_positions": ["WR"], "team": "TST", "status": "Active"}},
            projection_payload=[{"player_id": "free-wr", "projected_points": 12.0, "source_name": pd.NA, "source_timestamp_utc": pd.NA}],
            target_roster_id=None,
            pulled_at="2026-09-29T07:00:00+00:00",
            projection_source="validated weekly enrichment input",
        )
        self.assertEqual(meta["status"], "PASS")
        self.assertEqual(watch.iloc[0]["projection_source"], "validated weekly enrichment input")
        self.assertEqual(watch.iloc[0]["projection_timestamp_utc"], "2026-09-29T07:00:00+00:00")

    def test_week_three_run_builds_current_deduplicated_sleeper_only_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            for path in FIXTURES.glob("*.json"):
                shutil.copy(path, fixtures / path.name)

            # Exercise the normal Sleeper refresh path with a Week 3 payload.
            for name in ("matchups_week_1.json", "transactions_week_1.json"):
                shutil.copy(fixtures / name, fixtures / name.replace("week_1", "week_3"))
            for week in (2,):
                shutil.copy(fixtures / "transactions_week_1.json", fixtures / f"transactions_week_{week}.json")

            league_path = fixtures / "league.json"
            league = json.loads(league_path.read_text())
            league["league_id"] = DEFAULT_CONFIG.league_id
            league["season"] = "2026"
            league_path.write_text(json.dumps(league))

            rosters = json.loads((fixtures / "rosters.json").read_text())
            player_path = fixtures / "players_nfl.json"
            players = json.loads(player_path.read_text())
            # A current offensive priority, a young stash with no projection, and
            # a high-projection IDP test the skill-position weighting guardrail.
            players.update({
                "free_wr_2026": {"full_name": "Sleeper Week 3 WR", "fantasy_positions": ["WR"], "team": "TST", "status": "Active", "injury_status": None, "depth_chart_position": "WR", "depth_chart_order": 1, "age": 24, "years_exp": 2},
                "free_rb_2026": {"full_name": "Sleeper Week 3 RB", "fantasy_positions": ["RB"], "team": "TST", "status": "Active", "injury_status": None, "depth_chart_position": "RB", "depth_chart_order": 4, "age": 22, "years_exp": 1},
                "free_lb_2026": {"full_name": "Sleeper Week 3 LB", "fantasy_positions": ["LB"], "team": "TST", "status": "Active", "injury_status": None, "depth_chart_position": "LB", "depth_chart_order": 1, "age": 25, "years_exp": 3},
            })
            player_path.write_text(json.dumps(players))
            (fixtures / "projections_week_3.json").write_text(json.dumps([
                {"player_id": "free_wr_2026", "stats": {"pts_ppr": 16.5}},
                {"player_id": "free_wr_2026", "stats": {"pts_ppr": 16.5}},
                {"player_id": "free_lb_2026", "stats": {"pts_ppr": 45.0}},
            ]))

            # These stale Week 1 ESPN outputs must never be read by the Sleeper pipeline.
            (fixtures / "waiver_watch_v2.csv").write_text("player_id,player_name\nESPN_OLD_ID,ESPN stale player\n")
            (fixtures / "waiver_watch_summary_v2.csv").write_text("player_name\nESPN stale player\n")

            result = run_refresh(week=3, output_dir=root / "output", fixture_dir=fixtures)
            output = root / "output"
            watch = pd.read_csv(output / "waiver_watch_v2.csv", dtype={"sleeper_player_id": str})
            summary = pd.read_csv(output / "waiver_watch_summary_v2.csv")
            manifest = json.loads((output / "manifest.json").read_text())
            owned = {str(pid) for roster in rosters for pid in roster["players"]}

            self.assertEqual(result["critical_failures"], [])
            self.assertEqual(manifest["overall_status"], "PASS")
            self.assertEqual(manifest["league_id"], DEFAULT_CONFIG.league_id)
            self.assertEqual(manifest["week"], 3)
            self.assertEqual(manifest["waiver_watch"]["target_roster_id"], 10)
            self.assertEqual(watch["league_id"].astype(str).unique().tolist(), [DEFAULT_CONFIG.league_id])
            self.assertEqual(pd.to_numeric(watch["week"]).unique().tolist(), [3])
            self.assertFalse(owned.intersection(set(watch["sleeper_player_id"])))
            self.assertTrue(watch["sleeper_player_id"].is_unique)
            self.assertNotIn("ESPN_OLD_ID", set(watch["sleeper_player_id"]))
            self.assertNotIn("ESPN stale player", set(watch["player_name"]))

            missing = watch.set_index("sleeper_player_id").loc["free_rb_2026"]
            self.assertTrue(bool(missing["projection_missing_flag"]))
            self.assertEqual(missing["projection_status"], "MISSING")
            self.assertTrue(pd.isna(missing["projected_points"]))
            self.assertEqual(watch.set_index("sleeper_player_id").loc["free_wr_2026", "recommendation_tier"], "PRIORITY_ADD")
            self.assertLess(watch.set_index("sleeper_player_id").loc["free_wr_2026", "rank"], watch.set_index("sleeper_player_id").loc["free_lb_2026", "rank"])
            self.assertEqual(manifest["waiver_watch"]["available_player_count"], len(watch))
            self.assertEqual(manifest["waiver_watch"]["unique_candidate_ids"], len(watch))
            self.assertEqual(manifest["waiver_watch"]["outputs_generated"], {
                "waiver_pool": True,
                "waiver_watch_v2.csv": True,
                "waiver_watch_summary_v2.csv": True,
            })
            self.assertTrue(manifest["waiver_watch"]["generated_at_utc"])
            self.assertEqual(manifest["waiver_watch"]["status"], "PASS")
            self.assertEqual(manifest["files"]["waiver_watch_v2.csv"]["bytes"], (output / "waiver_watch_v2.csv").stat().st_size)
            self.assertEqual(len(summary), 5)
            self.assertTrue({"waiver_watch_v2.csv", "waiver_watch_summary_v2.csv"}.issubset(set(PERSISTENT_FILES)))


if __name__ == "__main__":
    unittest.main()
