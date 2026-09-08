from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LeagueConfig:
    league_id: str = "1402840136382742528"
    league_name: str = "1 Genius and 9 Idiots"
    expected_teams: int = 10
    expected_roster_size: int = 27
    expected_starters: int = 11
    bench_spots: int = 16
    trade_deadline_week: int = 10
    playoff_start_week: int = 15
    playoff_teams: int = 6
    rookie_draft_rounds: int = 3
    target_team_name: str = "RuffRyders Reloaded"


DEFAULT_CONFIG = LeagueConfig()
DEFAULT_OUTPUT_DIR = Path("output")
