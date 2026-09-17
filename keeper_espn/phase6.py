from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ALLOWED_TIERS = {"TIER_1", "TIER_2", "TIER_3", "TIER_4", "TIER_5"}
ALLOWED_BUCKETS = {"MATERIAL_CHANGE", "VALIDATION", "CHALLENGE", "IGNORE"}
TIER_ORDER = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3, "TIER_4": 4, "TIER_5": 5}
BUCKET_ORDER = {"MATERIAL_CHANGE": 1, "CHALLENGE": 2, "VALIDATION": 3, "IGNORE": 4}


def _norm_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness(published_at: Any, *, generated_at: str) -> str:
    published = _parse_iso(published_at)
    generated = _parse_iso(generated_at)
    if published is None or generated is None:
        return "UNKNOWN"
    age_hours = max(0.0, (generated - published).total_seconds() / 3600.0)
    if age_hours <= 36:
        return "CURRENT"
    if age_hours <= 96:
        return "AGING"
    return "STALE"


def load_intelligence_input(path: str | Path | None) -> tuple[list[dict[str, Any]], str | None]:
    if path is None:
        return [], None
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Missing external intelligence input: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        items = data.get("items")
    else:
        items = data
    if not isinstance(items, list):
        raise RuntimeError("External intelligence input must be a JSON list or an object with an items list")
    return [item for item in items if isinstance(item, dict)], str(path)


def _entity_indexes(
    roster_state: dict[str, Any],
    player_pool_state: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}

    for scope, rows in (
        ("ROSTER", roster_state.get("players") or []),
        ("PLAYER_POOL", player_pool_state.get("players") or []),
    ):
        for player in rows:
            entity = {
                "scope": scope,
                "player_id": player.get("player_id"),
                "player_name": player.get("player_name"),
                "position": player.get("position"),
            }
            player_id = player.get("player_id")
            if player_id is not None:
                by_id[str(player_id)] = entity
            name = _norm_name(player.get("player_name"))
            if name and name not in by_name:
                by_name[name] = entity
    return by_id, by_name


def _normalize_item(
    raw: dict[str, Any],
    *,
    generated_at: str,
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    sequence: int,
) -> dict[str, Any]:
    source_tier = str(raw.get("source_tier") or "").upper()
    if source_tier not in ALLOWED_TIERS:
        source_tier = "TIER_5"

    bucket = str(raw.get("review_bucket") or raw.get("classification") or "VALIDATION").upper()
    if bucket not in ALLOWED_BUCKETS:
        bucket = "VALIDATION"

    entity = None
    raw_player_id = raw.get("player_id")
    if raw_player_id is not None:
        entity = by_id.get(str(raw_player_id))
    if entity is None:
        entity = by_name.get(_norm_name(raw.get("player_name")))

    published_at = raw.get("published_at") or raw.get("observed_at")
    freshness = _freshness(published_at, generated_at=generated_at)

    return {
        "item_id": str(raw.get("item_id") or f"intel-{sequence:03d}"),
        "source_name": raw.get("source_name") or "Unknown Source",
        "source_tier": source_tier,
        "source_url": raw.get("source_url"),
        "published_at": published_at,
        "observed_at": raw.get("observed_at"),
        "freshness": freshness,
        "review_bucket": bucket,
        "category": str(raw.get("category") or "OTHER").upper(),
        "headline": raw.get("headline"),
        "detail": raw.get("detail"),
        "confidence": str(raw.get("confidence") or "UNKNOWN").upper(),
        "tags": list(raw.get("tags") or []),
        "matched_scope": entity.get("scope") if entity else "UNMATCHED",
        "matched_player_id": entity.get("player_id") if entity else None,
        "matched_player_name": entity.get("player_name") if entity else raw.get("player_name"),
        "matched_position": entity.get("position") if entity else None,
    }


def build_intelligence_state(
    *,
    raw_items: list[dict[str, Any]],
    input_source: str | None,
    generated_at: str,
    week: int,
    roster_state: dict[str, Any],
    player_pool_state: dict[str, Any],
) -> dict[str, Any]:
    by_id, by_name = _entity_indexes(roster_state, player_pool_state)

    items = [
        _normalize_item(
            raw,
            generated_at=generated_at,
            by_id=by_id,
            by_name=by_name,
            sequence=i,
        )
        for i, raw in enumerate(raw_items, start=1)
    ]
    items.sort(
        key=lambda item: (
            BUCKET_ORDER.get(item["review_bucket"], 9),
            TIER_ORDER.get(item["source_tier"], 9),
            item["item_id"],
        )
    )

    source_names = sorted({str(item.get("source_name")) for item in items if item.get("source_name")})
    source_status = "CURRENT" if items else "MISSING"
    if items and all(item.get("freshness") == "STALE" for item in items):
        source_status = "STALE"
    elif items and any(item.get("freshness") == "STALE" for item in items):
        source_status = "AGING"

    summary = {bucket: 0 for bucket in ALLOWED_BUCKETS}
    for item in items:
        summary[item["review_bucket"]] += 1

    return {
        "week": week,
        "generated_at": generated_at,
        "input_source": input_source,
        "source_status": source_status,
        "source_names": source_names,
        "item_count": len(items),
        "matched_roster_count": sum(1 for item in items if item["matched_scope"] == "ROSTER"),
        "matched_player_pool_count": sum(1 for item in items if item["matched_scope"] == "PLAYER_POOL"),
        "unmatched_count": sum(1 for item in items if item["matched_scope"] == "UNMATCHED"),
        "review_bucket_counts": summary,
        "items": items,
    }


def validate_intelligence_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(state.get("items"), list):
        errors.append("Intelligence state items must be a list")
        return errors

    ids: list[str] = []
    for item in state["items"]:
        if item.get("source_tier") not in ALLOWED_TIERS:
            errors.append(f"Invalid source tier for intelligence item {item.get('item_id')}")
        if item.get("review_bucket") not in ALLOWED_BUCKETS:
            errors.append(f"Invalid review bucket for intelligence item {item.get('item_id')}")
        ids.append(str(item.get("item_id")))
    if len(ids) != len(set(ids)):
        errors.append("Duplicate intelligence item IDs")
    return errors


def apply_intelligence_to_advisor_packet(
    advisor_packet: dict[str, Any],
    *,
    intelligence_state: dict[str, Any],
    limit: int = 25,
) -> dict[str, Any]:
    visible = [
        item
        for item in intelligence_state.get("items") or []
        if item.get("review_bucket") != "IGNORE"
    ][:limit]

    advisor_packet["material_intelligence"] = visible
    advisor_packet["run_state"]["external_intelligence_status"] = intelligence_state.get("source_status")
    advisor_packet["run_state"]["external_intelligence_item_count"] = intelligence_state.get("item_count")

    if intelligence_state.get("item_count"):
        gaps = advisor_packet["run_state"].get("known_gaps") or []
        advisor_packet["run_state"]["known_gaps"] = [
            gap
            for gap in gaps
            if gap != "External intelligence/role-trend/ROS context is not yet automated."
        ]
        advisor_packet["run_state"]["known_gaps"].append(
            "External intelligence is normalized, but role trend/ROS interpretation remains an Advisor responsibility."
        )
    return advisor_packet
