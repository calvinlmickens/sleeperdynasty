from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from keeper_espn.pipeline_phase4 import run_refresh


class KeeperEspnPhase3Tests(unittest.TestCase):
    def test_fixture_refresh_tracks_delta_and_preserves_lkg(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["ESPN_LEAGUE_ID"] = "424242"
            os.environ["ESPN_TEAM_ID"] = "1"
            os.environ["ESPN_SEASON"] = "2026"
            base_fixture = Path(__file__).parent / "keeper_fixtures"

            with TemporaryDirectory() as tmp:
                root = Path(tmp) / "output"

                first = run_refresh(output_dir=root, fixture_dir=base_fixture)
                self.assertEqual(first["status"], "PASS")

                latest = root / "latest"
                lkg = root / "last_known_good"
                for name in (
                    "manifest.json",
                    "league_state.json",
                    "roster_state.json",
                    "matchup_state.json",
                    "keeper_state.json",
                    "player_pool.json",
                    "delta_state.json",
                    "advisor_packet.json",
                ):
                    self.assertTrue((latest / name).exists(), name)
                    self.assertTrue((lkg / name).exists(), name)

                first_manifest = json.loads((latest / "manifest.json").read_text())
                first_delta = json.loads((latest / "delta_state.json").read_text())
                advisor = json.loads((latest / "advisor_packet.json").read_text())
                league = json.loads((latest / "league_state.json").read_text())
                roster = json.loads((latest / "roster_state.json").read_text())
                keeper = json.loads((latest / "keeper_state.json").read_text())
                pool = json.loads((latest / "player_pool.json").read_text())

                self.assertEqual(first_manifest["validation_status"], "PASS")
                self.assertEqual(first_manifest["previous_validated_run_id"], None)
                self.assertEqual(first_delta["baseline_status"], "FIRST_VALIDATED_RUN")
                self.assertFalse(first_delta["material_change"])
                self.assertEqual(advisor["run_state"]["validation_status"], "PASS")
                self.assertEqual(advisor["team_state"]["team_name"], "Taylor Made")
                self.assertFalse(advisor["automation_boundary"]["strategy_decisions_embedded"])
                self.assertFalse(advisor["automation_boundary"]["final_tci_assigned"])
                self.assertTrue(any(q["type"] == "INJURY_MONITOR_REQUIRED" for q in advisor["decision_queue"]))
                self.assertTrue(any(q["type"] == "START_SIT_REQUIRED" for q in advisor["decision_queue"]))
                self.assertEqual(league["league_allowed_ir_slots"], 2)
                self.assertEqual(roster["league_allowed_ir_slots"], 2)
                self.assertEqual(roster["players"][0]["keeper_round"], 5)
                self.assertEqual(roster["players"][3]["keeper_round"], 17)
                self.assertTrue(keeper["draft_completed"])
                self.assertEqual(pool["player_count"], 2)

                changed_fixture = Path(tmp) / "changed_fixture"
                shutil.copytree(base_fixture, changed_fixture)
                changed_league_path = changed_fixture / "league.json"
                changed_league = json.loads(changed_league_path.read_text())
                team = next(t for t in changed_league["teams"] if t["id"] == 1)
                team["waiverRank"] = 2
                team["roster"]["entries"] = [
                    e
                    for e in team["roster"]["entries"]
                    if e["playerPoolEntry"]["player"]["id"] != 104
                ]
                team["roster"]["entries"].append(
                    {
                        "lineupSlotId": 20,
                        "playerPoolEntry": {
                            "player": {
                                "id": 105,
                                "fullName": "New Bench Receiver",
                                "proTeamId": 7,
                                "defaultPositionId": 3,
                                "injuryStatus": "ACTIVE",
                            }
                        },
                    }
                )
                changed_league["schedule"][0]["home"]["totalProjectedPoints"] = 140.0
                changed_league_path.write_text(json.dumps(changed_league, indent=2) + "\n")

                changed_pool_path = changed_fixture / "player_pool.json"
                changed_pool = json.loads(changed_pool_path.read_text())
                changed_pool = [p for p in changed_pool if p["player"]["id"] != 202]
                changed_pool.append(
                    {
                        "status": "FREEAGENT",
                        "player": {
                            "id": 203,
                            "fullName": "New Available Tight End",
                            "proTeamId": 8,
                            "defaultPositionId": 4,
                            "injuryStatus": "ACTIVE",
                            "ownership": {"percentOwned": 12.0, "percentStarted": 1.0},
                        },
                    }
                )
                changed_pool_path.write_text(json.dumps(changed_pool, indent=2) + "\n")

                second = run_refresh(output_dir=root, fixture_dir=changed_fixture)
                self.assertEqual(second["status"], "PASS")
                self.assertEqual(second["previous_validated_run_id"], first["run_id"])
                self.assertTrue(second["material_change"])

                second_manifest = json.loads((latest / "manifest.json").read_text())
                second_delta = json.loads((latest / "delta_state.json").read_text())
                second_advisor = json.loads((latest / "advisor_packet.json").read_text())
                self.assertEqual(
                    second_manifest["previous_validated_run_id"],
                    first["run_id"],
                )
                self.assertEqual(
                    second_delta["baseline_status"],
                    "COMPARED_TO_PREVIOUS_VALIDATED",
                )
                self.assertEqual(second_delta["summary"]["roster_adds"], 1)
                self.assertEqual(second_delta["summary"]["roster_drops"], 1)
                self.assertEqual(second_delta["summary"]["player_pool_additions"], 1)
                self.assertEqual(second_delta["summary"]["player_pool_removals"], 1)
                self.assertEqual(
                    second_delta["league"]["waiver_priority"],
                    {"before": 5, "after": 2},
                )
                self.assertEqual(second_delta["roster"]["added"][0]["player_name"], "New Bench Receiver")
                self.assertEqual(second_delta["roster"]["removed"][0]["player_name"], "Bench Runner")
                self.assertEqual(second_advisor["material_deltas"]["summary"]["roster_adds"], 1)
                self.assertEqual(second_advisor["material_deltas"]["summary"]["roster_drops"], 1)
                self.assertEqual(second_advisor["team_state"]["waiver_priority"], 2)
                self.assertEqual(len(second_advisor["actionable_player_pool"]), 2)

                second_lkg_run_id = json.loads((lkg / "manifest.json").read_text())["run_id"]
                self.assertEqual(second_lkg_run_id, second["run_id"])

                invalid_fixture = Path(tmp) / "invalid_fixture"
                shutil.copytree(changed_fixture, invalid_fixture)
                invalid_league_path = invalid_fixture / "league.json"
                invalid_league = json.loads(invalid_league_path.read_text())
                invalid_team = next(t for t in invalid_league["teams"] if t["id"] == 1)
                invalid_team["roster"]["entries"].extend(
                    [
                        {
                            "lineupSlotId": 21,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 301,
                                    "fullName": "IR Player One",
                                    "proTeamId": 9,
                                    "defaultPositionId": 2,
                                    "injuryStatus": "OUT",
                                }
                            },
                        },
                        {
                            "lineupSlotId": 21,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 302,
                                    "fullName": "IR Player Two",
                                    "proTeamId": 10,
                                    "defaultPositionId": 3,
                                    "injuryStatus": "OUT",
                                }
                            },
                        },
                        {
                            "lineupSlotId": 21,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 303,
                                    "fullName": "IR Player Three",
                                    "proTeamId": 11,
                                    "defaultPositionId": 3,
                                    "injuryStatus": "OUT",
                                }
                            },
                        },
                    ]
                )
                invalid_league_path.write_text(json.dumps(invalid_league, indent=2) + "\n")

                failed = run_refresh(output_dir=root, fixture_dir=invalid_fixture)
                self.assertEqual(failed["status"], "FAIL")
                self.assertTrue(failed["last_known_good_preserved"])
                preserved_lkg_run_id = json.loads((lkg / "manifest.json").read_text())["run_id"]
                self.assertEqual(preserved_lkg_run_id, second["run_id"])
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
