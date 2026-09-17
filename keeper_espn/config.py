from __future__ import annotations

from dataclasses import dataclass
import os


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class KeeperEspnConfig:
    league_id: str
    team_id: int
    season: int
    swid: str | None = None
    espn_s2: str | None = None
    team_name: str = "Taylor Made"

    @classmethod
    def from_env(cls, *, season: int | None = None) -> "KeeperEspnConfig":
        league_id = _clean(os.getenv("ESPN_LEAGUE_ID"))
        team_id_raw = _clean(os.getenv("ESPN_TEAM_ID"))
        if not league_id:
            raise RuntimeError("ESPN_LEAGUE_ID is required")
        if not team_id_raw:
            raise RuntimeError("ESPN_TEAM_ID is required")

        resolved_season = season or int(os.getenv("ESPN_SEASON", "2026"))
        return cls(
            league_id=league_id,
            team_id=int(team_id_raw),
            season=resolved_season,
            swid=_clean(os.getenv("SWID")),
            espn_s2=_clean(os.getenv("ESPN_S2")),
            team_name=_clean(os.getenv("ESPN_TEAM_NAME")) or "Taylor Made",
        )

    @property
    def is_private_authenticated(self) -> bool:
        return bool(self.swid and self.espn_s2)
