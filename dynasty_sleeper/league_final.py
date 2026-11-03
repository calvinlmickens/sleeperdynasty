"""League-wide final matchups from Sleeper's historical weekly endpoint."""
from collections import defaultdict

def build_league_final(league, week, matchups, state, timestamp, team_count=10):
    if len(matchups) != team_count:
        raise RuntimeError("Incomplete league: matchup rows")
    state_lookup = {}
    for _, row in state.iterrows():
        state_lookup[(int(row["roster_id"]), str(row["sleeper_player_id"]))] = row
    slots = [x for x in league["roster_positions"] if x not in ("BN", "IR", "TAXI")]
    groups = defaultdict(list)
    seen = set()
    for m in matchups:
        rid = int(m["roster_id"])
        mid = int(m["matchup_id"])
        if rid in seen:
            raise RuntimeError("Duplicate roster in league final")
        seen.add(rid)
        starters = [str(x) for x in m["starters"]]
        if len(starters) != len(slots):
            raise RuntimeError("Unexpected starter slot count")
        points = {str(k): float(v) for k,v in m["players_points"].items() if v is not None}
        entries = []
        for slot, pid in zip(slots, starters):
            if pid == "0":
                entries.append({"slot": slot, "player_id": None, "points": 0.0})
                continue
            row = state_lookup.get((rid, pid))
            if row is None or pid not in points:
                raise RuntimeError("Missing historical starter or scoring: " + str(rid) + "/" + pid)
            entries.append({"slot": slot, "player_id": pid, "name": str(row["player_name"]),
                            "points": round(points[pid], 2)})
        score = round(float(m["points"]),2)
        if abs(sum(p["points"] for p in entries) - score) > 0.02:
            raise RuntimeError("Final team score does not match starters: " + str(rid))
        team_rows = state[state["roster_id"] == rid]
        groups[mid].append({"roster_id": rid, "team_name": str(team_rows.iloc[0]["team_name"]),
                            "score": score, "starters": entries})
    if len(seen) != team_count or len(groups) != team_count//2 or any(len(v)!=2 for v in groups.values()):
        raise RuntimeError("Incomplete league pairings")
    final = []
    for mid in sorted(groups):
        a,b = sorted(groups[mid], key=lambda x:x["roster_id"])
        final.append({"matchup_id": mid, "teams": [a,b], "margin": round(abs(a["score"]-b["score"]),2),
                      "winner_roster_id": a["roster_id"] if a["score"]>b["score"] else
                      b["roster_id"] if b["score"]>a["score"] else None})
    return {"snapshot_type": "FINAL_LEAGUE_WEEK", "status": "PASS",
            "season": str(league["season"]), "week": week, "league_id": str(league["league_id"]),
            "captured_at_utc": timestamp, "team_count": team_count,
            "matchup_count": len(final), "matchups": final}
