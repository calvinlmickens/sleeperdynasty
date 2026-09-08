from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests


class SleeperClient:
    """Small read-only Sleeper API client.

    Production mode calls the public Sleeper API. Fixture mode reads JSON files,
    allowing the package to be validated in environments without outbound network access.
    """

    BASE_URL = "https://api.sleeper.app/v1"
    STATS_URL = "https://api.sleeper.com"

    def __init__(self, timeout: int = 20, fixture_dir: str | Path | None = None):
        self.timeout = timeout
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "dynasty-sleeper/0.1"})

    def _get(self, path: str, fixture_name: str | None = None) -> Any:
        if self.fixture_dir:
            if not fixture_name:
                raise ValueError("fixture_name required in fixture mode")
            file = self.fixture_dir / fixture_name
            with file.open("r", encoding="utf-8") as f:
                return json.load(f)

        url = f"{self.BASE_URL}{path}"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def league(self, league_id: str):
        return self._get(f"/league/{league_id}", "league.json")

    def users(self, league_id: str):
        return self._get(f"/league/{league_id}/users", "users.json")

    def rosters(self, league_id: str):
        return self._get(f"/league/{league_id}/rosters", "rosters.json")

    def matchups(self, league_id: str, week: int):
        return self._get(f"/league/{league_id}/matchups/{week}", f"matchups_week_{week}.json")

    def transactions(self, league_id: str, week: int):
        return self._get(f"/league/{league_id}/transactions/{week}", f"transactions_week_{week}.json")

    def traded_picks(self, league_id: str):
        return self._get(f"/league/{league_id}/traded_picks", "traded_picks.json")

    def players_nfl(self):
        # Sleeper recommends caching this large endpoint rather than repeatedly fetching it.
        return self._get("/players/nfl", "players_nfl.json")

    def weekly_projections(self, season: str | int, week: int):
        """Fetch Sleeper weekly projections from the separate stats/projection host.

        This endpoint is widely used by Sleeper clients but is not part of the
        documented core league API. Failure is handled as non-fatal enrichment loss.
        """
        if self.fixture_dir:
            file = self.fixture_dir / f"projections_week_{week}.json"
            if not file.exists():
                return {}
            with file.open("r", encoding="utf-8") as f:
                return json.load(f)
        url = f"{self.STATS_URL}/projections/nfl/{season}/{week}"
        r = self.session.get(url, params={"season_type": "regular"}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
