from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from keeper_espn.pipeline_phase6 import run_refresh


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
                    "league_results.json",
                    "intelligence_state.json",
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
                self.assertEqual(league["effective_open_ir_slots"], 2)
                self.assertEqual(roster["effective_open_ir_slots"], 2)
                self.assertEqual(advisor["team_state"]["ir_usage"]["effective_open"], 2)
                self.assertEqual(league["waiver_system"], "PRIORITY")
                self.assertEqual(league["waiver_process_hour"], 4)
                self.assertNotIn("waiver_type", league)
                self.assertEqual(roster["players"][0]["keeper_round"], 5)
                self.assertEqual(roster["players"][3]["keeper_round"], 17)
                self.assertTrue(keeper["draft_completed"])
                self.assertEqual(pool["player_count"], 2)
                drafted_available = next(p for p in pool["players"] if p["player_id"] == "201")
                undrafted_available = next(p for p in pool["players"] if p["player_id"] == "202")
                self.assertEqual(drafted_available["keeper_round_if_added"], 6)
                self.assertEqual(drafted_available["keeper_origin_if_added"], "DRAFTED")
                self.assertEqual(undrafted_available["keeper_round_if_added"], 17)
                self.assertEqual(undrafted_available["keeper_origin_if_added"], "UNDRAFTED_FA")

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


    def test_finalized_prior_week_builds_full_league_recap(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["ESPN_LEAGUE_ID"] = "424242"
            os.environ["ESPN_TEAM_ID"] = "1"
            os.environ["ESPN_SEASON"] = "2026"
            base_fixture = Path(__file__).parent / "keeper_fixtures"

            with TemporaryDirectory() as tmp:
                fixture_dir = Path(tmp) / "final_fixture"
                shutil.copytree(base_fixture, fixture_dir)

                league_path = fixture_dir / "league.json"
                league = json.loads(league_path.read_text())
                league["schedule"].insert(
                    0,
                    {
                        "id": 10,
                        "matchupPeriodId": 1,
                        "winner": "HOME",
                        "home": {
                            "teamId": 1,
                            "totalPoints": 145.5,
                            "totalProjectedPoints": 131.0,
                        },
                        "away": {
                            "teamId": 2,
                            "totalPoints": 121.2,
                            "totalProjectedPoints": 128.0,
                        },
                    },
                )
                league_path.write_text(json.dumps(league, indent=2) + "\n")

                boxscore = {
                    "schedule": [
                        {
                            "id": 10,
                            "matchupPeriodId": 1,
                            "winner": None,
                            "home": {
                                "teamId": 1,
                                "totalPoints": 145.5,
                                "rosterForCurrentScoringPeriod": {
                                    "entries": [
                                        {
                                            "lineupSlotId": 0,
                                            "playerPoolEntry": {
                                                "appliedStatTotal": 30.0,
                                                "player": {
                                                    "id": 101,
                                                    "fullName": "Test Quarterback",
                                                    "defaultPositionId": 1,
                                                },
                                            },
                                        },
                                        {
                                            "lineupSlotId": 2,
                                            "playerPoolEntry": {
                                                "appliedStatTotal": 25.0,
                                                "player": {
                                                    "id": 102,
                                                    "fullName": "Test Runner",
                                                    "defaultPositionId": 2,
                                                },
                                            },
                                        },
                                        {
                                            "lineupSlotId": 4,
                                            "playerPoolEntry": {
                                                "appliedStatTotal": 20.0,
                                                "player": {
                                                    "id": 103,
                                                    "fullName": "Test Receiver",
                                                    "defaultPositionId": 3,
                                                },
                                            },
                                        },
                                        {
                                            "lineupSlotId": 20,
                                            "playerPoolEntry": {
                                                "appliedStatTotal": 18.5,
                                                "player": {
                                                    "id": 104,
                                                    "fullName": "Bench Runner",
                                                    "defaultPositionId": 2,
                                                },
                                            },
                                        },
                                    ]
                                },
                            },
                            "away": {
                                "teamId": 2,
                                "totalPoints": 121.2,
                                "rosterForCurrentScoringPeriod": {
                                    "entries": [
                                        {
                                            "lineupSlotId": 0,
                                            "playerPoolEntry": {
                                                "appliedStatTotal": 24.0,
                                                "player": {
                                                    "id": 2010,
                                                    "fullName": "Opponent Quarterback",
                                                    "defaultPositionId": 1,
                                                },
                                            },
                                        },
                                        {
                                            "lineupSlotId": 20,
                                            "playerPoolEntry": {
                                                "appliedStatTotal": 9.0,
                                                "player": {
                                                    "id": 2011,
                                                    "fullName": "Opponent Bench",
                                                    "defaultPositionId": 3,
                                                },
                                            },
                                        },
                                    ]
                                },
                            },
                        }
                    ]
                }
                (fixture_dir / "boxscore.json").write_text(json.dumps(boxscore, indent=2) + "\n")

                root = Path(tmp) / "output"
                result = run_refresh(output_dir=root, fixture_dir=fixture_dir)
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["week"], 2)
                self.assertEqual(result["results_week"], 1)
                self.assertEqual(result["results_status"], "FINAL_RESULTS")

                league_results = json.loads((root / "latest" / "league_results.json").read_text())
                advisor = json.loads((root / "latest" / "advisor_packet.json").read_text())

                self.assertEqual(league_results["week"], 1)
                self.assertEqual(league_results["results_status"], "FINAL_RESULTS")
                self.assertEqual(league_results["matchup_count"], 1)
                self.assertEqual(league_results["league_high_score"]["team_name"], "Taylor Made")
                self.assertEqual(league_results["league_high_score"]["score"], 145.5)
                self.assertEqual(league_results["closest_game"]["margin"], 24.3)
                self.assertEqual(
                    league_results["taylor_made_result"]["highest_bench_player"]["player_name"],
                    "Bench Runner",
                )
                self.assertEqual(
                    league_results["taylor_made_result"]["highest_bench_player"]["actual_points"],
                    18.5,
                )
                self.assertEqual(advisor["weekly_result"]["week"], 1)
                self.assertEqual(advisor["weekly_result"]["outcome"], "WIN")
                self.assertEqual(advisor["league_recap_summary"]["week"], 1)
                self.assertEqual(
                    advisor["league_recap_summary"]["team_with_most_bench_points"]["team_name"],
                    "Taylor Made",
                )
                self.assertNotIn(
                    "Per-player actual points are not yet normalized for final-week recap use.",
                    advisor["run_state"]["known_gaps"],
                )
        finally:
            os.environ.clear()
            os.environ.update(old)


    def test_external_intelligence_adapter_normalizes_and_matches(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["ESPN_LEAGUE_ID"] = "424242"
            os.environ["ESPN_TEAM_ID"] = "1"
            os.environ["ESPN_SEASON"] = "2026"
            base_fixture = Path(__file__).parent / "keeper_fixtures"

            with TemporaryDirectory() as tmp:
                intel_path = Path(tmp) / "intelligence.json"
                intel_path.write_text(
                    json.dumps(
                        {
                            "items": [
                                {
                                    "item_id": "official-1",
                                    "source_name": "NFL Team Injury Report",
                                    "source_tier": "TIER_1",
                                    "published_at": "2026-09-17T16:00:00-04:00",
                                    "review_bucket": "MATERIAL_CHANGE",
                                    "category": "INJURY",
                                    "player_name": "Test Receiver",
                                    "headline": "Test Receiver limited",
                                    "detail": "Player was limited in practice.",
                                    "confidence": "HIGH",
                                },
                                {
                                    "item_id": "analyst-1",
                                    "source_name": "32BeatWriters",
                                    "source_tier": "TIER_3",
                                    "published_at": "2026-09-17T15:00:00-04:00",
                                    "review_bucket": "CHALLENGE",
                                    "category": "ROLE",
                                    "player_name": "Available Runner",
                                    "headline": "Available Runner getting first-team work",
                                    "detail": "Beat report suggests increased opportunity.",
                                    "confidence": "MODERATE",
                                },
                                {
                                    "item_id": "unmatched-1",
                                    "source_name": "FantasyPros",
                                    "source_tier": "TIER_2",
                                    "published_at": "2026-09-17T14:00:00-04:00",
                                    "review_bucket": "VALIDATION",
                                    "category": "ROS",
                                    "player_name": "Not In Our Packet",
                                    "headline": "External player note",
                                    "detail": "Useful league context but not matched.",
                                    "confidence": "MODERATE",
                                },
                                {
                                    "item_id": "ignore-1",
                                    "source_name": "Social Discovery",
                                    "source_tier": "TIER_5",
                                    "published_at": "2026-09-17T13:00:00-04:00",
                                    "review_bucket": "IGNORE",
                                    "category": "RUMOR",
                                    "player_name": "Test Runner",
                                    "headline": "Unverified rumor",
                                    "detail": "Discovery only.",
                                    "confidence": "LOW",
                                },
                            ]
                        },
                        indent=2,
                    )
                    + "\n"
                )

                root = Path(tmp) / "output"
                result = run_refresh(
                    output_dir=root,
                    fixture_dir=base_fixture,
                    intelligence_file=intel_path,
                )
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["intelligence_status"], "CURRENT")
                self.assertEqual(result["intelligence_item_count"], 4)

                intelligence = json.loads((root / "latest" / "intelligence_state.json").read_text())
                advisor = json.loads((root / "latest" / "advisor_packet.json").read_text())

                self.assertEqual(intelligence["matched_roster_count"], 2)
                self.assertEqual(intelligence["matched_player_pool_count"], 1)
                self.assertEqual(intelligence["unmatched_count"], 1)
                self.assertEqual(intelligence["review_bucket_counts"]["MATERIAL_CHANGE"], 1)
                self.assertEqual(intelligence["review_bucket_counts"]["CHALLENGE"], 1)
                self.assertEqual(intelligence["review_bucket_counts"]["VALIDATION"], 1)
                self.assertEqual(intelligence["review_bucket_counts"]["IGNORE"], 1)

                visible_ids = [item["item_id"] for item in advisor["material_intelligence"]]
                self.assertEqual(
                    visible_ids,
                    ["official-1", "analyst-1", "unmatched-1"],
                )
                self.assertNotIn("ignore-1", visible_ids)
                self.assertEqual(
                    advisor["run_state"]["external_intelligence_status"],
                    "CURRENT",
                )
                self.assertNotIn(
                    "External intelligence/role-trend/ROS context is not yet automated.",
                    advisor["run_state"]["known_gaps"],
                )
        finally:
            os.environ.clear()
            os.environ.update(old)


    def test_official_intelligence_targets_only_actionable_pool(self) -> None:
        from keeper_espn.phase6 import intelligence_target_names

        roster_state = {
            "players": [
                {"player_name": "Roster One"},
                {"player_name": "Roster Two"},
            ]
        }
        player_pool_state = {
            "players": [
                {"player_name": f"Pool Player {i}"}
                for i in range(1, 26)
            ]
        }

        names = intelligence_target_names(
            roster_state,
            player_pool_state,
            pool_limit=20,
        )

        self.assertEqual(names[:2], ["Roster One", "Roster Two"])
        self.assertEqual(len(names), 22)
        self.assertIn("Pool Player 20", names)
        self.assertNotIn("Pool Player 21", names)
        self.assertNotIn("Pool Player 25", names)


    def test_official_nfl_public_collector_parsers(self) -> None:
        from keeper_espn.official_intel import parse_injury_html, parse_transactions_html

        injury_html = """
        <table>
          <tr><th>Player</th><th>Position</th><th>Injuries</th><th>Practice Status</th><th>Game Status</th></tr>
          <tr><td>Test Receiver</td><td>WR</td><td>Hamstring</td><td>Limited Participation in Practice</td><td>Questionable</td></tr>
          <tr><td>Unrelated Player</td><td>RB</td><td>Knee</td><td>Did Not Participate In Practice</td><td>Out</td></tr>
        </table>
        """
        injury_items = parse_injury_html(
            injury_html,
            target_names=["Test Receiver", "Available Runner"],
            observed_at="2026-09-17T18:00:00-04:00",
        )
        self.assertEqual(len(injury_items), 1)
        self.assertEqual(injury_items[0]["player_name"], "Test Receiver")
        self.assertEqual(injury_items[0]["source_tier"], "TIER_1")
        self.assertEqual(injury_items[0]["review_bucket"], "MATERIAL_CHANGE")
        self.assertEqual(injury_items[0]["category"], "INJURY")

        transaction_html = """
        <table>
          <tr><th>From</th><th>To</th><th>Date</th><th>Name</th><th>Position</th><th>Transaction</th></tr>
          <tr><td>Team A</td><td>Team B</td><td>09/17</td><td>Available Runner</td><td>RB</td><td>Traded</td></tr>
          <tr><td>Team C</td><td></td><td>09/17</td><td>Other Player</td><td>WR</td><td>Waived</td></tr>
        </table>
        """
        txn_items = parse_transactions_html(
            transaction_html,
            target_names=["Test Receiver", "Available Runner"],
            observed_at="2026-09-17T18:00:00-04:00",
            source_url="https://www.nfl.com/transactions/league/trades/2026/9",
            category="trades",
        )
        self.assertEqual(len(txn_items), 1)
        self.assertEqual(txn_items[0]["player_name"], "Available Runner")
        self.assertEqual(txn_items[0]["review_bucket"], "MATERIAL_CHANGE")
        self.assertIn("09/17", txn_items[0]["detail"])
        self.assertIn("Traded", txn_items[0]["headline"])


    def test_ir_players_do_not_create_injury_monitor_noise(self) -> None:
        from keeper_espn.phase4 import _decision_queue

        roster_state = {
            "players": [
                {
                    "player_name": "IR Player",
                    "roster_status": "IR",
                    "injury_status": "OUT",
                },
                {
                    "player_name": "Bench Player",
                    "roster_status": "BENCH",
                    "injury_status": "QUESTIONABLE",
                },
            ]
        }
        delta_state = {
            "summary": {
                "player_pool_additions": 0,
                "player_pool_removals": 0,
                "roster_adds": 0,
                "roster_drops": 0,
                "keeper_changes": 0,
            }
        }
        matchup_state = {"matchup_status": "PRE_GAME"}

        queue = _decision_queue(
            roster_state=roster_state,
            delta_state=delta_state,
            matchup_state=matchup_state,
        )

        injury_items = [q for q in queue if q["type"] == "INJURY_MONITOR_REQUIRED"]
        self.assertEqual(len(injury_items), 1)
        self.assertEqual(
            [p["player_name"] for p in injury_items[0]["players"]],
            ["Bench Player"],
        )


if __name__ == "__main__":
    unittest.main()
