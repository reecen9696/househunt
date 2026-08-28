"""Core record shapes shared by every module.

Each source parser emits RawRow; normalisation turns those into
PropertyRecord keyed by a stable property_id. One snapshot row is stored
per property per run week.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

CANONICAL_OUTCOMES = {
    "SOLD",
    "SOLD_PRIOR",
    "SOLD_AFTER",
    "PASSED_IN",
    "PASSED_IN_VENDOR_BID",
    "NO_BID",
    "WITHDRAWN",
    "POSTPONED",
    "UNREPORTED",
    "UNKNOWN",
}

PRICE_STATUSES = ("QUOTED", "RELISTED", "BID_DERIVED", "SOI", "UNKNOWN")


@dataclass
class RawRow:
    """One auction-result row exactly as a source parser read it."""

    source: str                      # "rea" | "domain"
    suburb: str
    postcode: Optional[str]
    address_raw: str
    outcome_raw: str                 # verbatim source label
    outcome_display: Optional[str] = None
    event_type: Optional[str] = None
    price_display: Optional[str] = None      # sold price text, if published
    max_bid_display: Optional[str] = None    # e.g. "Last bid $690,000"
    property_type: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    car_spaces: Optional[int] = None
    land_size_sqm: Optional[float] = None
    agency_name: Optional[str] = None
    agent_name: Optional[str] = None
    source_url: Optional[str] = None         # listing URL if the row links one
    source_listing_id: Optional[str] = None
    image_url: Optional[str] = None          # main photo, if the row has one
    week_ending: Optional[str] = None        # ISO date of the results week


@dataclass
class PropertyRecord:
    """Normalised, deduplicated record for one property in one run week."""

    property_id: str
    address_raw: str
    address_norm: str
    street_number: str
    street: str
    suburb: str
    postcode: Optional[str]

    outcome: str                     # canonical, see CANONICAL_OUTCOMES
    outcome_raw: str
    sources: list = field(default_factory=list)         # ["rea"] / ["rea","domain"]
    source_urls: dict = field(default_factory=dict)     # source -> url
    source_listing_ids: dict = field(default_factory=dict)

    week_ending: Optional[str] = None
    highest_bid: Optional[int] = None
    vendor_bid: Optional[int] = None

    price_low: Optional[int] = None
    price_high: Optional[int] = None
    price_status: str = "UNKNOWN"
    price_source_url: Optional[str] = None
    sold_price: Optional[int] = None          # for sold rows (market read)

    property_type: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    car_spaces: Optional[int] = None
    land_size_sqm: Optional[float] = None

    agency_name: Optional[str] = None
    agent_name: Optional[str] = None
    image_url: Optional[str] = None

    merge_confidence: str = "HIGH"   # LOW when a fuzzy dedupe merged this

    def display_address(self) -> str:
        return f"{self.address_raw}, {self.suburb}"


def make_property_id(address_norm: str, suburb: str, postcode: Optional[str]) -> str:
    key = f"{address_norm}|{suburb.strip().lower()}|{(postcode or '').strip()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
