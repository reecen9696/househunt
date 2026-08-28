"""Ranking (§8). Weights live in config — anything hardcoded here would be
wrong within a month.

Default intent: weeks-unsold first (a property unsold three weeks running is
a motivated vendor), no-bid above passed-in, priced above UNKNOWN, cheaper
first within that.
"""
from __future__ import annotations


def score(item: dict, weights: dict) -> float:
    s = 0.0
    s += (item.get("weeks_unsold") or 0) * float(weights.get("per_week_unsold", 100))
    s += float((weights.get("outcome") or {}).get(item.get("outcome"), 0))
    if item.get("price_low") is not None:
        s += float(weights.get("has_price", 0))
    if item.get("highest_bid") or item.get("vendor_bid"):
        s += float(weights.get("has_bid_figure", 0))
    return s


def sort_items(items: list[dict], ranking_cfg: dict) -> list[dict]:
    weights = ranking_cfg.get("weights") or {}

    def key(item):
        # Higher score first; then cheaper first, unknown price last.
        price = item.get("price_low")
        return (-score(item, weights), price is None, price or 0)

    return sorted(items, key=key)
