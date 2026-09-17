from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import requests

from .config import KeeperEspnConfig


ESPN_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"


@dataclass
class EspnPull:
    league: dict[str, Any]
    source: str
    available_players: list[dict[str, Any]]
    player_pool_source: str | None = None
    boxscore: dict[str, Any] | None = None
    boxscore_source: str | None = None
    results_week: int | None = None


def _latest_finalized_week(league: dict[str, Any]) -> int | None:
    current_week = int(league.get("scoringPeriodId") or 0)
    by_week: dict[int, list[dict[str, Any]]] = {}
    for matchup in league.get("schedule") or []:
        try:
            week = int(matchup.get("matchupPeriodId"))
        except (TypeError, ValueError):
            continue
        if week > current_week:
            continue
        by_week.setdefault(week, []).append(matchup)

    finalized: list[int] = []
    for week, matchups in by_week.items():
        if matchups and all(m.get("winner") in {"HOME", "AWAY", "TIE"} for m in matchups):
            finalized.append(week)
    return max(finalized) if finalized else None


class EspnFantasyClient:
    def __init__(self, config: KeeperEspnConfig, *, timeout: int = 30):
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "keeper-league-advisor/0.3"})
        if config.swid and config.espn_s2:
            self.session.cookies.set("SWID", config.swid)
            self.session.cookies.set("espn_s2", config.espn_s2)

    @property
    def league_url(self) -> str:
        return (
            f"{ESPN_BASE}/seasons/{self.config.season}/segments/0/"
            f"leagues/{self.config.league_id}"
        )

    @staticmethod
    def _json_object(response: requests.Response, *, label: str) -> dict[str, Any]:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                f"ESPN {label} response was not JSON "
                f"(status={response.status_code}, content_type={content_type!r}, "
                f"final_url={response.url!r})"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(
                f"ESPN {label} response was not a JSON object "
                f"(status={response.status_code}, content_type={content_type!r}, "
                f"final_url={response.url!r})"
            )
        return data

    def pull_league(self) -> EspnPull:
        params = [
            ("view", "mSettings"),
            ("view", "mTeam"),
            ("view", "mRoster"),
            ("view", "mMatchup"),
            ("view", "mStandings"),
            ("view", "mDraftDetail"),
        ]
        response = self.session.get(self.league_url, params=params, timeout=self.timeout)
        league = self._json_object(response, label="league")

        current_week = int(league.get("scoringPeriodId") or 0)
        player_filter = {
            "players": {
                "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                "limit": 250,
                "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            }
        }
        pool_response = self.session.get(
            self.league_url,
            params=[("view", "kona_player_info"), ("scoringPeriodId", str(current_week))],
            headers={"X-Fantasy-Filter": json.dumps(player_filter, separators=(",", ":"))},
            timeout=self.timeout,
        )
        pool = self._json_object(pool_response, label="player-pool")
        available_players = pool.get("players") or []
        if not isinstance(available_players, list):
            raise RuntimeError("ESPN player-pool response did not contain a players list")

        results_week = _latest_finalized_week(league)
        boxscore: dict[str, Any] | None = None
        boxscore_source: str | None = None
        if results_week is not None:
            box_response = self.session.get(
                self.league_url,
                params=[
                    ("view", "mBoxscore"),
                    ("matchupPeriodId", str(results_week)),
                    ("scoringPeriodId", str(results_week)),
                ],
                timeout=self.timeout,
            )
            boxscore = self._json_object(box_response, label="boxscore")
            boxscore_source = box_response.url

        return EspnPull(
            league=league,
            source=response.url,
            available_players=available_players,
            player_pool_source=pool_response.url,
            boxscore=boxscore,
            boxscore_source=boxscore_source,
            results_week=results_week,
        )


def load_fixture(fixture_dir: str | Path) -> EspnPull:
    fixture_dir = Path(fixture_dir)
    path = fixture_dir / "league.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing ESPN fixture: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("ESPN fixture league.json must contain a JSON object")

    pool_path = fixture_dir / "player_pool.json"
    available_players: list[dict[str, Any]] = []
    if pool_path.exists():
        pool_data = json.loads(pool_path.read_text(encoding="utf-8"))
        if not isinstance(pool_data, list):
            raise RuntimeError("ESPN fixture player_pool.json must contain a JSON array")
        available_players = pool_data

    results_week = _latest_finalized_week(data)
    boxscore_path = fixture_dir / "boxscore.json"
    boxscore: dict[str, Any] | None = None
    if boxscore_path.exists():
        boxscore_data = json.loads(boxscore_path.read_text(encoding="utf-8"))
        if not isinstance(boxscore_data, dict):
            raise RuntimeError("ESPN fixture boxscore.json must contain a JSON object")
        boxscore = boxscore_data

    return EspnPull(
        league=data,
        source=str(path),
        available_players=available_players,
        player_pool_source=str(pool_path) if pool_path.exists() else None,
        boxscore=boxscore,
        boxscore_source=str(boxscore_path) if boxscore_path.exists() else None,
        results_week=results_week,
    )
