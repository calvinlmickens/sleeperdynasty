from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup


INJURY_URL = "https://www.nfl.com/injuries/"
TRANSACTION_CATEGORIES = (
    "trades",
    "signings",
    "reserve-list",
    "waivers",
    "terminations",
    "other",
)
TRANSACTION_URL = "https://www.nfl.com/transactions/league/{category}/{year}/{month}"


@dataclass
class OfficialIntelCollection:
    items: list[dict[str, Any]]
    status: str
    source_names: list[str]
    errors: list[str]


def _match_key(name: Any) -> str:
    text = str(name or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    parts = [p for p in text.split() if p not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return " ".join(parts)


def _target_map(names: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        key = _match_key(name)
        if key:
            result[key] = name
    return result


def _cells(row: Any) -> list[str]:
    return [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["th", "td"])]


def parse_injury_html(
    html: str,
    *,
    target_names: Iterable[str],
    observed_at: str,
    source_url: str = INJURY_URL,
) -> list[dict[str, Any]]:
    targets = _target_map(target_names)
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for row in soup.find_all("tr"):
        cells = _cells(row)
        if len(cells) < 4:
            continue
        player_name, position, injury, practice_status = cells[:4]
        game_status = cells[4] if len(cells) >= 5 else ""
        key = _match_key(player_name)
        canonical = targets.get(key)
        if not canonical:
            continue

        practice_lower = practice_status.lower()
        game_lower = game_status.lower()
        if any(token in game_lower for token in ("out", "doubtful", "questionable")):
            bucket = "MATERIAL_CHANGE"
        elif "did not participate" in practice_lower or "limited" in practice_lower:
            bucket = "MATERIAL_CHANGE"
        elif "full participation" in practice_lower:
            bucket = "VALIDATION"
        else:
            bucket = "VALIDATION"

        dedupe = (canonical, injury, practice_status, game_status)
        if dedupe in seen:
            continue
        seen.add(dedupe)

        detail_bits = []
        if injury:
            detail_bits.append(f"Injury: {injury}")
        if practice_status:
            detail_bits.append(f"Practice: {practice_status}")
        if game_status:
            detail_bits.append(f"Game status: {game_status}")

        headline = f"{canonical} — {practice_status or game_status or 'official injury report update'}"
        items.append(
            {
                "item_id": f"nfl-injury-{re.sub(r'[^a-z0-9]+', '-', key).strip('-')}-{len(items)+1}",
                "source_name": "NFL.com Official Injury Report",
                "source_tier": "TIER_1",
                "source_url": source_url,
                "observed_at": observed_at,
                "review_bucket": bucket,
                "category": "INJURY",
                "player_name": canonical,
                "headline": headline,
                "detail": "; ".join(detail_bits),
                "confidence": "HIGH",
                "tags": ["official", "injury-report", position] if position else ["official", "injury-report"],
            }
        )
    return items


def parse_transactions_html(
    html: str,
    *,
    target_names: Iterable[str],
    observed_at: str,
    source_url: str,
    category: str,
) -> list[dict[str, Any]]:
    targets = _target_map(target_names)
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for row in soup.find_all("tr"):
        cells = _cells(row)
        if len(cells) < 3:
            continue

        canonical = None
        name_cell = None
        for cell in cells:
            match = targets.get(_match_key(cell))
            if match:
                canonical = match
                name_cell = cell
                break
        if not canonical:
            continue

        transaction = cells[-1]
        date_text = cells[-3] if len(cells) >= 4 else ""
        dedupe = (canonical, date_text, transaction)
        if dedupe in seen:
            continue
        seen.add(dedupe)

        item_key = re.sub(r"[^a-z0-9]+", "-", _match_key(canonical)).strip("-")
        items.append(
            {
                "item_id": f"nfl-txn-{category}-{item_key}-{len(items)+1}",
                "source_name": "NFL.com Official Transactions",
                "source_tier": "TIER_1",
                "source_url": source_url,
                "observed_at": observed_at,
                "review_bucket": "MATERIAL_CHANGE",
                "category": "TRANSACTION",
                "player_name": canonical,
                "headline": f"{canonical} — {transaction}",
                "detail": f"Official NFL transaction{f' dated {date_text}' if date_text else ''}: {transaction}",
                "confidence": "HIGH",
                "tags": ["official", "transaction", category],
            }
        )
    return items


def _get(session: requests.Session, url: str, *, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def collect_official_nfl_intelligence(
    *,
    target_names: Iterable[str],
    observed_at: str,
    year: int,
    month: int,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> OfficialIntelCollection:
    session = session or requests.Session()
    session.headers.update({"User-Agent": "keeper-league-advisor/official-intel-0.1"})
    targets = [name for name in target_names if str(name).strip()]

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    successful_sources: list[str] = []

    try:
        html = _get(session, INJURY_URL, timeout=timeout)
        items.extend(
            parse_injury_html(
                html,
                target_names=targets,
                observed_at=observed_at,
                source_url=INJURY_URL,
            )
        )
        successful_sources.append("NFL.com Official Injury Report")
    except Exception as exc:
        errors.append(f"injuries: {type(exc).__name__}: {exc}")

    for category in TRANSACTION_CATEGORIES:
        url = TRANSACTION_URL.format(category=category, year=year, month=month)
        try:
            html = _get(session, url, timeout=timeout)
            items.extend(
                parse_transactions_html(
                    html,
                    target_names=targets,
                    observed_at=observed_at,
                    source_url=url,
                    category=category,
                )
            )
            successful_sources.append("NFL.com Official Transactions")
        except Exception as exc:
            errors.append(f"transactions/{category}: {type(exc).__name__}: {exc}")

    unique_sources = sorted(set(successful_sources))
    if unique_sources and errors:
        status = "DEGRADED"
    elif unique_sources:
        status = "CURRENT"
    else:
        status = "FAILED"

    items.sort(key=lambda item: (item.get("player_name") or "", item.get("category") or "", item.get("item_id") or ""))
    return OfficialIntelCollection(
        items=items,
        status=status,
        source_names=unique_sources,
        errors=errors,
    )
