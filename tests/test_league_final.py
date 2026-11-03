import unittest
import pandas as pd
from dynasty_sleeper.league_final import build_league_final

class LeagueFinalTests(unittest.TestCase):
    def data(self):
        league={"league_id":"1402840136382742528","season":"2026","roster_positions":["QB"]*11+["BN"]}
        games=[];rows=[]
        for rid in range(1,11):
            ids=[str(rid*100+i) for i in range(11)]
            games.append({"roster_id":rid,"matchup_id":(rid+1)//2,"starters":ids,
                          "players_points":{p:2.0 for p in ids},"points":22.0})
            rows.extend({"roster_id":rid,"sleeper_player_id":p,"player_name":p,
                         "position":"QB","team_name":str(rid)} for p in ids)
        return league,games,pd.DataFrame(rows)
    def test_full_league(self):
        a,b,c=self.data()
        s=build_league_final(a,8,b,c,"now")
        self.assertEqual((s["team_count"],s["matchup_count"]),(10,5))
    def test_incomplete_fails(self):
        a,b,c=self.data()
        with self.assertRaises(RuntimeError):
            build_league_final(a,8,b[:-1],c,"now")
    def test_score_mismatch_fails(self):
        a,b,c=self.data()
        b[0]["points"]=99
        with self.assertRaises(RuntimeError):
            build_league_final(a,8,b,c,"now")
