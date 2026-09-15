from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from keeper_espn.pipeline import run_refresh


class KeeperEspnPhase1Tests(unittest.TestCase):
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
                self.assertTrue((latest / "manifest.json").exists())
                self.assertTrue((latest / "league_state.json").exists())
                self.assertTrue((latest / "roster_state.json").exists())
                self.assertTrue((latest / "matchup_state.json").exists())

                manifest = json.loads((latest / "manifest.json").read_text())
                roster = json.loads((latest / "roster_state.json").read_text())
                matchup = json.loads((latest / "matchup_state.json").read_text())

                self.assertEqual(manifest["validation_status"], "PASS")
                self.assertEqual(manifest["nfl_week"], 2)
                self.assertEqual(roster["team_name"], "Taylor Made")
                self.assertEqual(roster["roster_count"], 4)
                self.assertEqual(roster["players"][3]["roster_status"], "BENCH")
                self.assertEqual(matchup["opponent_team_name"], "Weekly Opponent")
                self.assertEqual(matchup["projected_margin"], 5.5)
                self.assertEqual(matchup["matchup_status"], "PRE_GAME")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
