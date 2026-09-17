from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import re
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import requests


ROTOWIRE_NFL_RSS = "https://www.rotowire.com/rss/news.php?sport=NFL"
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
class AnalystCollection:
    items: list[dict[str, Any]]
    status: str
    source_names: list[str]
    errors: list[str]
    seen_ids: list[str]


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


def _stable_id(source: str, guid: str | None, link: str | None, title: str) -> str:
    raw = guid or link or title
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source.lower()}-{digest}"


def _classify(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ROLE_TERMS):
        return "CHALLENGE"
    if any(term in lowered for term in INJURY_TERMS):
        return "VALIDATION"
    return "VALIDATION"


def _match_target(
    *,
    title: str,
    description: str,
    targets: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    combined = _norm(f"{title} {description}")
    for target in targets:
        name = _norm(target.get("player_name"))
        if name and name in combined:
            return target
    return None


def parse_rotowire_rss(
    xml_text: str,
    *,
    target_players: list[dict[str, Any]],
    observed_at: str,
    previous_seen_ids: Iterable[str] | None = None,
    source_url: str = ROTOWIRE_NFL_RSS,
) -> tuple[list[dict[str, Any]], list[str]]:
    root = ET.fromstring(xml_text)
    previous_seen = {str(item) for item in (previous_seen_ids or [])}
    items: list[dict[str, Any]] = []
    seen_ids = list(previous_seen)

    for entry in root.findall(".//item"):
        title = _strip_html(entry.findtext("title"))
        description = _strip_html(entry.findtext("description"))
        link = _strip_html(entry.findtext("link")) or None
        guid = _strip_html(entry.findtext("guid")) or None
        published_at = _published_iso(entry.findtext("pubDate"))
        target = _match_target(
            title=title,
            description=description,
            targets=target_players,
        )
        if not target:
            continue

        item_id = _stable_id("rotowire", guid, link, title)
        if item_id not in seen_ids:
            seen_ids.append(item_id)
        if item_id in previous_seen:
            continue

        combined = f"{title}. {description}".strip()
        bucket = _classify(combined)
        items.append(
            {
                "item_id": item_id,
                "source_name": "RotoWire Public NFL RSS",
                "source_tier": "TIER_3",
                "source_url": link or source_url,
                "published_at": published_at,
                "observed_at": observed_at,
                "review_bucket": bucket,
                "category": "ROLE" if bucket == "CHALLENGE" else "PLAYER_NEWS",
                "player_id": target.get("player_id"),
                "player_name": target.get("player_name"),
                "headline": title,
                "detail": description[:700] if description else title,
                "confidence": "MODERATE",
                "tags": ["public", "analyst", "rss", "rotowire"],
            }
        )

    return items, seen_ids[-200:]


def collect_public_analyst_intelligence(
    *,
    target_players: list[dict[str, Any]],
    observed_at: str,
    previous_seen_ids: Iterable[str] | None = None,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> AnalystCollection:
    session = session or requests.Session()
    session.headers.update({"User-Agent": "keeper-league-advisor/public-analyst-0.1"})

    try:
        response = session.get(ROTOWIRE_NFL_RSS, timeout=timeout)
        response.raise_for_status()
        items, seen_ids = parse_rotowire_rss(
            response.text,
            target_players=target_players,
            observed_at=observed_at,
            previous_seen_ids=previous_seen_ids,
            source_url=ROTOWIRE_NFL_RSS,
        )
        return AnalystCollection(
            items=items,
            status="CURRENT",
            source_names=["RotoWire Public NFL RSS"],
            errors=[],
            seen_ids=seen_ids,
        )
    except Exception as exc:
        return AnalystCollection(
            items=[],
            status="FAILED",
            source_names=[],
            errors=[f"RotoWire NFL RSS: {type(exc).__name__}: {exc}"],
            seen_ids=[str(item) for item in (previous_seen_ids or [])][-200:],
        )
