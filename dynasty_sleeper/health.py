from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from .client import SleeperClient
from .config import DEFAULT_CONFIG, LeagueConfig


@dataclass
class HealthCheckResult:
    status: str
    checked_at_utc: str
    league_id: str
    league_name_expected: str
    league_name_resolved: str | None
    league_resolved: bool
    rosters_returned: int | None
    users_returned: int | None
    matchups_returned: int | None
    players_endpoint: str
    week_checked: int | None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_healthcheck(
    week: int | None = None,
    config: LeagueConfig = DEFAULT_CONFIG,
    fixture_dir: str | None = None,
    pull_players: bool = True,
) -> HealthCheckResult:
    """Verify that the package runtime can reach and resolve the configured Sleeper league.

    PASS requires:
      * league endpoint resolves configured league id
      * users endpoint responds
      * rosters endpoint responds with expected team count
      * current-week matchup endpoint responds when week is supplied
      * player metadata endpoint responds when pull_players=True

    A failure never falls back to stale league state silently.
    """
    client = SleeperClient(fixture_dir=fixture_dir)
    checked = datetime.now(timezone.utc).isoformat()

    league_name = None
    roster_count = None
    user_count = None
    matchup_count = None
    players_state = "NOT_CHECKED"

    try:
        league = client.league(config.league_id)
        league_name = league.get("name") if isinstance(league, dict) else None
        resolved_id = str(league.get("league_id")) if isinstance(league, dict) and league.get("league_id") is not None else None
        if resolved_id != str(config.league_id):
            raise ValueError(f"League ID mismatch: expected {config.league_id}, received {resolved_id}")

        users = client.users(config.league_id)
        user_count = len(users) if isinstance(users, list) else None

        rosters = client.rosters(config.league_id)
        roster_count = len(rosters) if isinstance(rosters, list) else None
        if roster_count != config.expected_teams:
            raise ValueError(f"Roster count mismatch: expected {config.expected_teams}, received {roster_count}")

        if week is not None:
            matchups = client.matchups(config.league_id, week)
            matchup_count = len(matchups) if isinstance(matchups, list) else None
            if matchup_count != config.expected_teams:
                raise ValueError(
                    f"Matchup row count mismatch for week {week}: expected {config.expected_teams}, received {matchup_count}"
                )

        if pull_players:
            players = client.players_nfl()
            if not isinstance(players, dict) or not players:
                raise ValueError("Sleeper player metadata endpoint returned an empty or invalid payload")
            players_state = f"PASS ({len(players)} players)"

        return HealthCheckResult(
            status="PASS",
            checked_at_utc=checked,
            league_id=config.league_id,
            league_name_expected=config.league_name,
            league_name_resolved=league_name,
            league_resolved=True,
            rosters_returned=roster_count,
            users_returned=user_count,
            matchups_returned=matchup_count,
            players_endpoint=players_state,
            week_checked=week,
        )
    except Exception as exc:
        return HealthCheckResult(
            status="FAIL",
            checked_at_utc=checked,
            league_id=config.league_id,
            league_name_expected=config.league_name,
            league_name_resolved=league_name,
            league_resolved=league_name is not None,
            rosters_returned=roster_count,
            users_returned=user_count,
            matchups_returned=matchup_count,
            players_endpoint=players_state,
            week_checked=week,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
