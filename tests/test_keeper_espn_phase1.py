from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from keeper_espn.pipeline_phase2 import run_refresh


class KeeperEspnPhase2Tests(unittest.TestCase):
    def test_fixture_refresh_promotes_validated_state(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["ESPN_LEAGUE_ID"] = "424242"
            os.environ["ESPN_TEAM_ID"] = "1"
            os.environ["ESPN_SEASON"] = "2026"
            fixture_dir = Path(__file__).parent / "keeper_fixtures"
            with TemporaryDirectory() as tmp:
                result = run_refresh(output_dir=tmp, fixture_dir=fixture_dir)
                self.assertEqual(result["status"], "PASS")
                latest = Path(tmp) / "latest"
                for name in (
                    "manifest.json",
                    "league_state.json",
                    "roster_state.json",
                    "matchup_state.json",
                    "keeper_state.json",
                    "player_pool.json",
                ):
                    self.assertTrue((latest / name).exists(), name)

                manifest = json.loads((latest / "manifest.json").read_text())
                league = json.loads((latest / "league_state.json").read_text())
                roster = json.loads((latest / "roster_state.json").read_text())
                matchup = json.loads((latest / "matchup_state.json").read_text())
                keeper = json.loads((latest / "keeper_state.json").read_text())
                pool = json.loads((latest / "player_pool.json").read_text())

                self.assertEqual(manifest["validation_status"], "PASS")
                self.assertEqual(manifest["nfl_week"], 2)
                self.assertEqual(league["platform_ir_slots"], 2)
                self.assertEqual(league["league_allowed_ir_slots"], 2)
                self.assertEqual(league["ir_slots"], 2)
                self.assertEqual(roster["team_name"], "Taylor Made")
                self.assertEqual(roster["roster_count"], 4)
                self.assertEqual(roster["league_allowed_ir_slots"], 2)
                self.assertEqual(roster["players"][3]["roster_status"], "BENCH")
                self.assertEqual(roster["players"][0]["keeper_round"], 5)
                self.assertEqual(roster["players"][0]["keeper_origin"], "DRAFTED")
                self.assertEqual(roster["players"][3]["keeper_round"], 17)
                self.assertEqual(roster["players"][3]["keeper_origin"], "UNDRAFTED_FA")
                self.assertTrue(keeper["draft_completed"])
                self.assertEqual(pool["player_count"], 2)
                self.assertEqual(pool["players"][0]["keeper_round_if_added"], 17)
                self.assertEqual(matchup["opponent_team_name"], "Weekly Opponent")
                self.assertEqual(matchup["projected_margin"], 5.5)
                self.assertEqual(matchup["matchup_status"], "PRE_GAME")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
