"""Turning a stored row into the dating figures the report shows.

Kept separate from dating_service so the read path never touches the
network: candidates gathered once at add time are cached, and every later
render just re-resolves them.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .dating import DateEstimate, date_from_auction, parse_iso, resolve
from .dating_service import _from_json
from .normalise import normalise_address, normalise_suburb


def free_candidates(store, config, *, url: Optional[str], address: Optional[str],
                    suburb: Optional[str], listed_date: Optional[date],
                    observed_since: Optional[date]) -> list[DateEstimate]:
    """Candidates that cost nothing: the auction anchor, the portal's own
    date, and the floor set by when this tool first saw the property."""
    out: list[DateEstimate] = []
    campaign_days = int(config.get("dating.auction_campaign_days", 28) or 28)

    if address and suburb:
        # Tracked addresses are full ("8 Whitton Parade, Coburg North, Vic
        # 3058"); auction results store the street part only.
        street_only = address.split(",")[0]
        norm = normalise_address(street_only).norm

        # Every auction event we know of, from our own scans and from
        # Domain's archive. The EARLIEST one bounds the campaign best: a
        # postponement still proves the property was being advertised then.
        auction_days = []
        week = store.earliest_auction_week(norm, normalise_suburb(suburb))
        if parse_iso(week):
            auction_days.append(parse_iso(week))
        postcode_hint = None
        m = re.search(r"\b(\d{4})\b", address)
        if m:
            postcode_hint = m.group(1)
        for row in store.find_domain_results(norm, postcode_hint):
            day = parse_iso(row["week_day"])
            if day:
                auction_days.append(day)

        if auction_days:
            earliest = min(auction_days)
            inferred = date_from_auction(earliest, campaign_days)
            if inferred:
                out.append(DateEstimate(
                    day=inferred, basis="auction-inferred",
                    detail=(f"auction event {earliest.isoformat()} − "
                            f"{campaign_days}d campaign")))

    if listed_date:
        out.append(DateEstimate(day=listed_date, basis="current-listing",
                                detail="date published by the portal"))
    if observed_since:
        out.append(DateEstimate(day=observed_since, basis="observed-floor",
                                detail="first seen by this tool"))
    return out


def dating_for_row(row, store, config, today: Optional[date] = None) -> dict:
    """Dating figures for one tracked property, as plain JSON-able fields."""
    today = today or date.today()
    url = row["url"] if "url" in row.keys() else None
    address = row["address"] if "address" in row.keys() else None

    # The suburb column can be empty when the extension captured a sparse
    # page; fall back to parsing it out of the address text.
    suburb = row["suburb"] if "suburb" in row.keys() else None
    if not suburb and address and "," in address:
        suburb = address.split(",")[1].strip()

    candidates = free_candidates(
        store, config, url=url, address=address, suburb=suburb,
        listed_date=parse_iso(row["date_listed"] if "date_listed" in row.keys() else None),
        observed_since=parse_iso(row["added_date"] if "added_date" in row.keys() else None),
    )
    cached = store.get_campaign_date(url) if url else None
    if cached is not None:
        candidates += _from_json(cached["candidates"])

    # Domain's own listing date, when we've looked it up. Documented and
    # address-derived, so it sees past a REA relist.
    if url:
        profile = store.get_domain_profile(url)
        if profile is not None:
            from .domain_lookup import date_estimate
            estimate = date_estimate({k: profile[k] for k in profile.keys()})
            if estimate:
                candidates.append(estimate)

    dating = resolve(candidates, today)
    threshold = int(config.get("dating.clock_reset_threshold_days", 21) or 21)

    # Has it already been to auction and failed? Read-only here: records are
    # fetched once when the property is added, then answered from the cache.
    auction = {}
    if config.get("auction_check.enabled", True) and address:
        from .auction_check import assess
        street_only = address.split(",")[0]
        auction = assess(
            address_norm=normalise_address(street_only).norm,
            postcode=(row["postcode"] if "postcode" in row.keys() else None),
            campaign_start=dating.start.day if dating.start else None,
            days_on_market=dating.days_on_market(today),
            price_text=(row["price_text"] if "price_text" in row.keys() else None),
            store=store, config=config, fetcher=None, today=today,
            allow_network=False,
        ).as_dict()

    # Days since the auction itself, alongside the total campaign length —
    # the total is what the property has actually endured on market, the
    # since-auction figure is how long the vendor has been sitting on a
    # failed result.
    days_since_auction = None
    auction_day = parse_iso(auction.get("auction_day")) if auction else None
    if auction_day:
        days_since_auction = max(0, (today - auction_day).days)

    return {
        "auction": auction,
        "days_since_auction": days_since_auction,
        "campaign_start": dating.start.day.isoformat() if dating.start else None,
        "campaign_basis": dating.start.basis if dating.start else None,
        "campaign_kind": dating.start.kind if dating.start else None,
        "campaign_detail": dating.start.detail if dating.start else None,
        "days_on_market": dating.days_on_market(today),
        "days_claimed": dating.days_claimed(today),
        "hidden_days": dating.hidden_days(today),
        "clock_reset": dating.clock_reset(threshold, today),
        "documented": dating.is_documented,
        "floor_only": dating.start.basis == "observed-floor" if dating.start else False,
        "current_listing_only": dating.rests_only_on_current_listing,
        "ever_auctioned": any(c.basis == "auction-inferred" for c in dating.candidates),
    }
