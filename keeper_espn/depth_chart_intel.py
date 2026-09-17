from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup


TEAM_DEPTH_CHART_URLS: dict[int, str] = {
    1: "https://www.atlantafalcons.com/team/depth-chart",
    2: "https://www.buffalobills.com/team/depth-chart",
    3: "https://www.chicagobears.com/team/depth-chart",
    4: "https://www.bengals.com/team/depth-chart",
    5: "https://www.clevelandbrowns.com/team/depth-chart",
    6: "https://www.dallascowboys.com/team/depth-chart",
    7: "https://www.denverbroncos.com/team/depth-chart",
    8: "https://www.detroitlions.com/team/depth-chart",
    9: "https://www.packers.com/team/depth-chart",
    10: "https://www.tennesseetitans.com/team/depth-chart",
    11: "https://www.colts.com/team/depth-chart",
    12: "https://www.chiefs.com/team/depth-chart",
    13: "https://www.raiders.com/team/depth-chart",
    14: "https://www.therams.com/team/depth-chart",
    15: "https://www.miamidolphins.com/team/depth-chart",
    16: "https://www.vikings.com/team/depth-chart",
    17: "https://www.patriots.com/team/depth-chart",
    18: "https://www.neworleanssaints.com/team/depth-chart",
    19: "https://www.giants.com/team/depth-chart",
    20: "https://www.newyorkjets.com/team/depth-chart",
    21: "https://www.philadelphiaeagles.com/team/depth-chart",
    22: "https://www.azcardinals.com/team/depth-chart",
    23: "https://www.steelers.com/team/depth-chart",
    24: "https://www.chargers.com/team/depth-chart",
    25: "https://www.49ers.com/team/depth-chart",
    26: "https://www.seahawks.com/team/depth-chart",
    27: "https://www.buccaneers.com/team/depth-chart",
    28: "https://www.commanders.com/team/depth-chart",
    29: "https://www.panthers.com/team/depth-chart",
    30: "https://www.jaguars.com/team/depth-chart",
    33: "https://www.baltimoreravens.com/team/depth-chart",
    34: "https://www.houstontexans.com/team/depth-chart",
}

POSITION_ALIASES = {
    "QB": {"QB"},
    "RB": {"RB", "HB", "FB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K", "PK"},
}

KNOWN_DEPTH_POSITIONS = {
    "QB", "RB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT",
    "DE", "DT", "NT", "EDGE", "OLB", "ILB", "LB", "CB", "S", "FS", "SS",
    "NB", "NCB", "K", "PK", "P", "LS", "KR", "PR",
}


@dataclass
class DepthChartCollection:
    snapshot: list[dict[str, Any]]
    items: list[dict[str, Any]]
    status: str
    source_names: list[str]
    errors: list[str]


def _norm(value: Any) -> str:
    text = str(value or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def _cells(row: Any) -> list[str]:
    return [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["th", "td"])]


def _position_match(player_position: Any, listed_position: str) -> bool:
    position = str(player_position or "").upper()
    return listed_position.upper() in POSITION_ALIASES.get(position, {position})


def parse_depth_chart_html(
    html: str,
    *,
    target_players: Iterable[dict[str, Any]],
    team_id: int,
    source_url: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    targets = [
        player
        for player in target_players
        if int(player.get("nfl_team_id") or -1) == int(team_id)
        and player.get("player_name")
    ]

    candidates: dict[str, list[dict[str, Any]]] = {
        str(player.get("player_id")): [] for player in targets
    }
    valid_rows = 0

    for row in soup.find_all("tr"):
        cells = _cells(row)
        if len(cells) < 2:
            continue
        listed_position = cells[0].strip().upper()
        if listed_position not in KNOWN_DEPTH_POSITIONS:
            continue
        valid_rows += 1
        for depth_rank, cell_text in enumerate(cells[1:], start=1):
            normalized_cell = _norm(cell_text)
            if not normalized_cell:
                continue
            for player in targets:
                player_id = str(player.get("player_id"))
                player_name = str(player.get("player_name"))
                normalized_name = _norm(player_name)
                if normalized_name and normalized_name in normalized_cell:
                    candidates[player_id].append(
                        {
                            "player_id": player.get("player_id"),
                            "player_name": player_name,
                            "position": player.get("position"),
                            "scope": player.get("scope"),
                            "nfl_team_id": team_id,
                            "source_url": source_url,
                            "observed_at": observed_at,
                            "found": True,
                            "listed_position": listed_position,
                            "depth_rank": depth_rank,
                            "depth_label": (
                                "FIRST"
                                if depth_rank == 1
                                else "SECOND"
                                if depth_rank == 2
                                else "THIRD"
                                if depth_rank == 3
                                else f"DEPTH_{depth_rank}"
                            ),
                            "position_match": _position_match(
                                player.get("position"),
                                listed_position,
                            ),
                        }
                    )

    if valid_rows == 0:
        raise RuntimeError("No recognizable depth-chart rows found")

    snapshot: list[dict[str, Any]] = []
    for player in targets:
        player_id = str(player.get("player_id"))
        rows = candidates.get(player_id) or []
        if rows:
            rows.sort(
                key=lambda item: (
                    0 if item.get("position_match") else 1,
                    int(item.get("depth_rank") or 99),
                    str(item.get("listed_position") or ""),
                )
            )
            chosen = dict(rows[0])
            chosen.pop("position_match", None)
            snapshot.append(chosen)
        else:
            snapshot.append(
                {
                    "player_id": player.get("player_id"),
                    "player_name": player.get("player_name"),
                    "position": player.get("position"),
                    "scope": player.get("scope"),
                    "nfl_team_id": team_id,
                    "source_url": source_url,
                    "observed_at": observed_at,
                    "found": False,
                    "listed_position": None,
                    "depth_rank": None,
                    "depth_label": None,
                }
            )
    return snapshot


def _snapshot_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("player_id")): row
        for row in rows
        if row.get("player_id") is not None
    }


def build_depth_chart_change_items(
    *,
    current_snapshot: list[dict[str, Any]],
    previous_snapshot: list[dict[str, Any]] | None,
    observed_at: str,
) -> list[dict[str, Any]]:
    if previous_snapshot is None:
        return []

    previous = _snapshot_index(previous_snapshot)
    items: list[dict[str, Any]] = []

    for current in current_snapshot:
        player_id = str(current.get("player_id"))
        before = previous.get(player_id)
        if before is None:
            continue

        changed = (
            before.get("nfl_team_id") != current.get("nfl_team_id")
            or before.get("found") != current.get("found")
            or before.get("listed_position") != current.get("listed_position")
            or before.get("depth_rank") != current.get("depth_rank")
        )
        if not changed:
            continue

        player_name = current.get("player_name")
        if current.get("found") and before.get("found"):
            detail = (
                f"Official depth chart changed from "
                f"{before.get('listed_position')} {before.get('depth_label')} "
                f"to {current.get('listed_position')} {current.get('depth_label')}."
            )
            headline = (
                f"{player_name} depth chart: "
                f"{before.get('depth_label')} → {current.get('depth_label')}"
            )
        elif current.get("found"):
            detail = (
                f"{player_name} is now listed on the official depth chart at "
                f"{current.get('listed_position')} {current.get('depth_label')}."
            )
            headline = f"{player_name} added to official depth chart"
        else:
            detail = (
                f"{player_name} was present on the prior official depth chart but "
                "is not listed on the current chart."
            )
            headline = f"{player_name} no longer listed on official depth chart"

        items.append(
            {
                "item_id": f"official-depth-{player_id}-{observed_at}",
                "source_name": "Official Team Depth Chart",
                "source_tier": "TIER_1",
                "source_url": current.get("source_url") or before.get("source_url"),
                "observed_at": observed_at,
                "review_bucket": "MATERIAL_CHANGE",
                "category": "ROLE",
                "player_id": current.get("player_id"),
                "player_name": player_name,
                "headline": headline,
                "detail": detail,
                "confidence": "HIGH",
                "tags": ["official", "depth-chart", "role-change"],
            }
        )

    return items


def collect_official_depth_chart_intelligence(
    *,
    target_players: list[dict[str, Any]],
    observed_at: str,
    previous_snapshot: list[dict[str, Any]] | None,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> DepthChartCollection:
    session = session or requests.Session()
    session.headers.update({"User-Agent": "keeper-league-advisor/depth-chart-0.1"})

    team_ids = sorted(
        {
            int(player["nfl_team_id"])
            for player in target_players
            if player.get("nfl_team_id") is not None
            and int(player["nfl_team_id"]) in TEAM_DEPTH_CHART_URLS
        }
    )

    snapshot: list[dict[str, Any]] = []
    errors: list[str] = []
    source_names: list[str] = []

    for team_id in team_ids:
        url = TEAM_DEPTH_CHART_URLS[team_id]
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            team_snapshot = parse_depth_chart_html(
                response.text,
                target_players=target_players,
                team_id=team_id,
                source_url=url,
                observed_at=observed_at,
            )
            snapshot.extend(team_snapshot)
            source_names.append("Official Team Depth Chart")
        except Exception as exc:
            errors.append(
                f"team {team_id} depth chart: {type(exc).__name__}: {exc}"
            )

    if source_names and errors:
        status = "DEGRADED"
    elif source_names:
        status = "CURRENT"
    elif team_ids:
        status = "FAILED"
    else:
        status = "MISSING"

    items = build_depth_chart_change_items(
        current_snapshot=snapshot,
        previous_snapshot=previous_snapshot,
        observed_at=observed_at,
    )

    return DepthChartCollection(
        snapshot=snapshot,
        items=items,
        status=status,
        source_names=sorted(set(source_names)),
        errors=errors,
    )
