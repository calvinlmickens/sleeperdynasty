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


class EspnFantasyClient:
    def __init__(self, config: KeeperEspnConfig, *, timeout: int = 30):
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "keeper-league-advisor/0.1"})
        if config.swid and config.espn_s2:
            self.session.cookies.set("SWID", config.swid)
            self.session.cookies.set("espn_s2", config.espn_s2)

    @property
    def league_url(self) -> str:
        return (
            f"{ESPN_BASE}/seasons/{self.config.season}/segments/0/"
            f"leagues/{self.config.league_id}"
        )

    def pull_league(self) -> EspnPull:
        # These views give Phase 1 enough information for league, roster,
        # matchup and standings normalization while keeping the request small.
        params = [
            ("view", "mSettings"),
            ("view", "mTeam"),
            ("view", "mRoster"),
            ("view", "mMatchup"),
            ("view", "mStandings"),
        ]
        response = self.session.get(self.league_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                "ESPN response was not JSON "
                f"(status={response.status_code}, content_type={content_type!r}, "
                f"final_url={response.url!r})"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(
                "ESPN league response was not a JSON object "
                f"(status={response.status_code}, content_type={content_type!r}, "
                f"final_url={response.url!r})"
            )
        return EspnPull(league=data, source=response.url)


def load_fixture(fixture_dir: str | Path) -> EspnPull:
    path = Path(fixture_dir) / "league.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing ESPN fixture: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("ESPN fixture league.json must contain a JSON object")
    return EspnPull(league=data, source=str(path))
