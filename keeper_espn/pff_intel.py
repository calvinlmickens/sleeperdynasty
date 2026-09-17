from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import re
from typing import Any, Iterable
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup


PFF_RSS_DIRECTORY = "https://www.pff.com/pff-rss"

ESPN_TEAM_NAMES: dict[int, str] = {
    1: "Atlanta Falcons",
    2: "Buffalo Bills",
    3: "Chicago Bears",
    4: "Cincinnati Bengals",
    5: "Cleveland Browns",
    6: "Dallas Cowboys",
    7: "Denver Broncos",
    8: "Detroit Lions",
    9: "Green Bay Packers",
    10: "Tennessee Titans",
    11: "Indianapolis Colts",
    12: "Kansas City Chiefs",
    13: "Las Vegas Raiders",
    14: "Los Angeles Rams",
    15: "Miami Dolphins",
    16: "Minnesota Vikings",
    17: "New England Patriots",
    18: "New Orleans Saints",
    19: "New York Giants",
    20: "New York Jets",
    21: "Philadelphia Eagles",
    22: "Arizona Cardinals",
    23: "Pittsburgh Steelers",
    24: "Los Angeles Chargers",
    25: "San Francisco 49ers",
    26: "Seattle Seahawks",
    27: "Tampa Buccaneers",
    28: "Washington Commanders",
    29: "Carolina Panthers",
    30: "Jacksonville Jaguars",
    33: "Baltimore Ravens",
    34: "Houston Texans",
}

ROLE_TERMS = (
    "starter",
    "starting",
    "first-team",
    "first team",
    "workload",
    "snap",
    "snaps",
    "targets",
    "target share",
    "backfield",
    "committee",
    "depth chart",
    "promoted",
    "demoted",
    "role",
    "usage",
    "routes",
    "route participation",
)
INJURY_TERMS = (
    "injury",
    "injured",
    "questionable",
    "doubtful",
    "out",
    "limited",
    "practice",
    "hamstring",
    "ankle",
    "knee",
    "concussion",
)


@dataclass
class PffCollection:
    items: list[dict[str, Any]]
    status: str
    source_names: list[str]
    errors: list[str]
    seen_ids: list[str]
    feed_count: int


def _norm(value: Any) -> str:
    text = html.unescape(str(value or "")).lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return " ".join(text.split())


def _strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


def _published_iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _stable_id(guid: str | None, link: str | None, title: str) -> str:
    raw = guid or link or title
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"pff-{digest}"


def _classify(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ROLE_TERMS):
        return "CHALLENGE"
    if any(term in lowered for term in INJURY_TERMS):
        return "VALIDATION"
    return "VALIDATION"


def discover_team_feeds(directory_html: str) -> dict[str, str]:
    soup = BeautifulSoup(directory_html, "html.parser")
    feeds: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        team_name = " ".join(anchor.get_text(" ", strip=True).split())
        href = str(anchor.get("href") or "")
        if not team_name or "/feed/teams/" not in href:
            continue
        feeds[_norm(team_name)] = urljoin(PFF_RSS_DIRECTORY, href)
    return feeds


def _match_target(title: str, description: str, targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    combined = _norm(f"{title} {description}")
    for target in targets:
        name = _norm(target.get("player_name"))
        if name and name in combined:
            return target
    return None


def parse_pff_team_rss(
    xml_text: str,
    *,
    target_players: list[dict[str, Any]],
    observed_at: str,
    previous_seen_ids: Iterable[str] | None = None,
    source_url: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    root = ET.fromstring(xml_text)
    previous_seen = {str(item) for item in (previous_seen_ids or [])}
    seen_ids = list(previous_seen)
    items: list[dict[str, Any]] = []

    for entry in root.findall(".//item"):
        title = _strip_html(entry.findtext("title"))
        description = _strip_html(entry.findtext("description"))
        link = _strip_html(entry.findtext("link")) or None
        guid = _strip_html(entry.findtext("guid")) or None
        target = _match_target(title, description, target_players)
        if not target:
            continue

        item_id = _stable_id(guid, link, title)
        if item_id not in seen_ids:
            seen_ids.append(item_id)
        if item_id in previous_seen:
            continue

        combined = f"{title}. {description}".strip()
        bucket = _classify(combined)
        items.append(
            {
                "item_id": item_id,
                "source_name": "PFF Public Team RSS",
                "source_tier": "TIER_3",
                "source_url": link or source_url,
                "published_at": _published_iso(entry.findtext("pubDate")),
                "observed_at": observed_at,
                "review_bucket": bucket,
                "category": "ROLE" if bucket == "CHALLENGE" else "PLAYER_NEWS",
                "player_id": target.get("player_id"),
                "player_name": target.get("player_name"),
                "headline": title,
                "detail": description[:700] if description else title,
                "confidence": "MODERATE",
                "tags": ["public", "analyst", "rss", "pff"],
            }
        )

    return items, seen_ids[-300:]


def emit_after_baseline(
    items: list[dict[str, Any]],
    *,
    previous_initialized: bool,
) -> list[dict[str, Any]]:
    return items if previous_initialized else []


def collect_pff_public_intelligence(
    *,
    target_players: list[dict[str, Any]],
    observed_at: str,
    previous_seen_ids: Iterable[str] | None = None,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> PffCollection:
    session = session or requests.Session()
    session.headers.update({"User-Agent": "keeper-league-advisor/pff-rss-0.1"})

    try:
        directory_response = session.get(PFF_RSS_DIRECTORY, timeout=timeout)
        directory_response.raise_for_status()
        discovered = discover_team_feeds(directory_response.text)
    except Exception as exc:
        return PffCollection(
            items=[],
            status="FAILED",
            source_names=[],
            errors=[f"PFF RSS directory: {type(exc).__name__}: {exc}"],
            seen_ids=[str(item) for item in (previous_seen_ids or [])][-300:],
            feed_count=0,
        )

    by_team: dict[int, list[dict[str, Any]]] = {}
    for player in target_players:
        team_id = player.get("nfl_team_id")
        if team_id is None:
            continue
        by_team.setdefault(int(team_id), []).append(player)

    items: list[dict[str, Any]] = []
    seen_ids = [str(item) for item in (previous_seen_ids or [])]
    errors: list[str] = []
    successful_feeds = 0

    for team_id, team_targets in sorted(by_team.items()):
        team_name = ESPN_TEAM_NAMES.get(team_id)
        if not team_name:
            continue
        feed_url = discovered.get(_norm(team_name))
        if not feed_url:
            errors.append(f"PFF feed missing for {team_name}")
            continue
        try:
            response = session.get(feed_url, timeout=timeout)
            response.raise_for_status()
            feed_items, seen_ids = parse_pff_team_rss(
                response.text,
                target_players=team_targets,
                observed_at=observed_at,
                previous_seen_ids=seen_ids,
                source_url=feed_url,
            )
            items.extend(feed_items)
            successful_feeds += 1
        except Exception as exc:
            errors.append(f"PFF {team_name} RSS: {type(exc).__name__}: {exc}")

    if successful_feeds and errors:
        status = "DEGRADED"
    elif successful_feeds:
        status = "CURRENT"
    elif by_team:
        status = "FAILED"
    else:
        status = "MISSING"

    return PffCollection(
        items=items,
        status=status,
        source_names=["PFF Public Team RSS"] if successful_feeds else [],
        errors=errors,
        seen_ids=seen_ids[-300:],
        feed_count=successful_feeds,
    )
