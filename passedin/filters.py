"""Filtering (§8). All thresholds come from config.

Two rules that must never be violated:
- UNKNOWN-priced properties are never silently dropped — they classify into
  their own section for the reviewer to judge.
- Null land size means unknown and is INCLUDED; min_land_size_sqm only
  filters records that actually report a size.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def classify(item: dict, filters_cfg: dict) -> str | None:
    """Returns which bucket a lead belongs to, or None to exclude.

    Buckets: "in_budget" | "stretch" | "no_price"
    """
    if item.get("bedrooms") is not None:
        if item["bedrooms"] < int(filters_cfg.get("min_bedrooms") or 0):
            return None

    ptype = (item.get("property_type") or "").strip().lower()
    incl_types = {t.strip().lower() for t in (filters_cfg.get("property_types") or [])}
    # A known type outside the include list is dropped; an unknown type is
    # kept — silently dropping unclassified rows hides real leads.
    if incl_types and ptype and ptype not in incl_types:
        return None
    excl_types = {t.strip().lower() for t in (filters_cfg.get("exclude_property_types") or [])}
    if ptype and ptype in excl_types:
        return None

    wanted = wanted_suburbs(filters_cfg)
    if wanted is not None and item.get("suburb"):
        from .normalise import normalise_suburb
        if normalise_suburb(item["suburb"]) not in wanted:
            return None

    min_land = filters_cfg.get("min_land_size_sqm")
    land = item.get("land_size_sqm")
    if min_land is not None and land is not None and land < float(min_land):
        return None

    excl_agencies = {a.strip().lower() for a in (filters_cfg.get("exclude_agencies") or [])}
    agency = (item.get("agency_name") or "").strip().lower()
    if agency and any(x in agency for x in excl_agencies):
        return None

    excl_streets = [s.strip().lower() for s in (filters_cfg.get("exclude_streets") or [])]
    street = (item.get("street") or "").lower()
    address = (item.get("address_raw") or "").lower()
    if any(x in street or x in address for x in excl_streets):
        return None

    price_low = item.get("price_low")
    if price_low is None:
        return "no_price"

    ceiling = int(filters_cfg.get("price_ceiling") or 0)
    # Filter on the LOWER bound: vendors quote low; catch anything that might
    # land under budget rather than pre-emptively excluding (§6).
    if ceiling and price_low <= ceiling:
        return "in_budget"

    stretch = filters_cfg.get("stretch_ceiling")
    if stretch and price_low <= int(stretch):
        return "stretch"
    return None


def wanted_suburbs(filters_cfg: dict) -> set[str] | None:
    """Normalised set of configured suburbs, or None for 'all'."""
    if (filters_cfg.get("suburbs_mode") or "list") == "all":
        return None
    from .normalise import normalise_suburb
    return {normalise_suburb(s) for s in (filters_cfg.get("suburbs") or [])}
