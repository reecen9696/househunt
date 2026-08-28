"""RawRow -> PropertyRecord normalisation.

Shared across sources: canonical outcome mapping, address normalisation,
stable property_id, and the bid figures that bound the reserve from below.
"""
from __future__ import annotations

import logging

from .model import PropertyRecord, RawRow, make_property_id
from .moneyparse import parse_money
from .normalise import normalise_address, normalise_suburb
from .outcomes import OutcomeMapper

logger = logging.getLogger(__name__)

SOLD_OUTCOMES = {"SOLD", "SOLD_PRIOR", "SOLD_AFTER"}


def to_record(row: RawRow, mapper: OutcomeMapper) -> PropertyRecord:
    addr = normalise_address(row.address_raw)
    suburb_norm = normalise_suburb(row.suburb)
    outcome = mapper.map(row.source, row.outcome_raw)

    record = PropertyRecord(
        property_id=make_property_id(addr.norm, suburb_norm, row.postcode),
        address_raw=row.address_raw,
        address_norm=addr.norm,
        street_number=addr.street_number,
        street=addr.street,
        suburb=row.suburb,
        postcode=row.postcode,
        outcome=outcome,
        outcome_raw=row.outcome_raw,
        sources=[row.source],
        source_urls={row.source: row.source_url} if row.source_url else {},
        source_listing_ids={row.source: row.source_listing_id} if row.source_listing_id else {},
        week_ending=row.week_ending,
        property_type=row.property_type,
        bedrooms=row.bedrooms,
        bathrooms=row.bathrooms,
        car_spaces=row.car_spaces,
        land_size_sqm=row.land_size_sqm,
        agency_name=row.agency_name,
        agent_name=row.agent_name,
        image_url=row.image_url,
    )

    # Published bid figure — the single most useful number in the dataset:
    # it bounds the reserve from below (§5).
    bid = parse_money(row.max_bid_display)
    if bid:
        if outcome == "PASSED_IN_VENDOR_BID":
            record.vendor_bid = bid
        else:
            record.highest_bid = bid
        # Fallback chain §6: a bid figure is price signal #2. Enrichment may
        # later upgrade this to a QUOTED range from the listing page.
        record.price_low = bid
        record.price_high = bid
        record.price_status = "BID_DERIVED"

    if outcome in SOLD_OUTCOMES:
        record.sold_price = parse_money(row.price_display)

    return record
