"""Deduplication (§9).

Exact pass: records sharing property_id (i.e. identical normalised address
+ suburb) merge into one, retaining every source URL and preferring
whichever side has the better price signal.

Fuzzy pass: near-identical normalised addresses within the same suburb are
merged but flagged merge_confidence: LOW — two different units in the same
block are not the same property, so a fuzzy match is surfaced, not assumed.
"""
from __future__ import annotations

import difflib
import logging

from .model import PropertyRecord

logger = logging.getLogger(__name__)

# Preference order for price_status when merging (§6 order).
_PRICE_RANK = {"QUOTED": 4, "RELISTED": 3, "BID_DERIVED": 2, "SOI": 1, "UNKNOWN": 0}

FUZZY_THRESHOLD = 0.90


def _merge(into: PropertyRecord, other: PropertyRecord) -> PropertyRecord:
    into.sources = sorted(set(into.sources) | set(other.sources))
    into.source_urls.update({k: v for k, v in other.source_urls.items() if v})
    into.source_listing_ids.update(other.source_listing_ids)

    if _PRICE_RANK.get(other.price_status, 0) > _PRICE_RANK.get(into.price_status, 0):
        into.price_low = other.price_low
        into.price_high = other.price_high
        into.price_status = other.price_status
        into.price_source_url = other.price_source_url

    for attr in ("bedrooms", "bathrooms", "car_spaces", "land_size_sqm",
                 "agency_name", "agent_name", "property_type", "highest_bid",
                 "vendor_bid", "sold_price"):
        if getattr(into, attr) is None and getattr(other, attr) is not None:
            setattr(into, attr, getattr(other, attr))
    return into


def dedupe(records: list[PropertyRecord]) -> tuple[list[PropertyRecord], int, int]:
    """Returns (merged records, exact merges, fuzzy merges)."""
    by_id: dict[str, PropertyRecord] = {}
    exact = 0
    for r in records:
        if r.property_id in by_id:
            _merge(by_id[r.property_id], r)
            exact += 1
        else:
            by_id[r.property_id] = r

    # Fuzzy second pass, within suburb only.
    merged = list(by_id.values())
    by_suburb: dict[str, list[PropertyRecord]] = {}
    for r in merged:
        by_suburb.setdefault(r.suburb.strip().lower(), []).append(r)

    fuzzy = 0
    dropped: set[str] = set()
    for group in by_suburb.values():
        for i, a in enumerate(group):
            if a.property_id in dropped:
                continue
            for b in group[i + 1:]:
                if b.property_id in dropped:
                    continue
                # Same street number required — fuzziness is for spelling and
                # abbreviation drift, never for different units or numbers.
                if a.street_number != b.street_number:
                    continue
                ratio = difflib.SequenceMatcher(None, a.address_norm, b.address_norm).ratio()
                if ratio >= FUZZY_THRESHOLD:
                    logger.warning(
                        "FUZZY MERGE (%.2f): %r + %r in %s — flagged LOW confidence",
                        ratio, a.address_raw, b.address_raw, a.suburb,
                    )
                    _merge(a, b)
                    a.merge_confidence = "LOW"
                    dropped.add(b.property_id)
                    fuzzy += 1

    return [r for r in merged if r.property_id not in dropped], exact, fuzzy
