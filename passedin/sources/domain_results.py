"""domain.com.au dated auction-results archive.

Domain keeps roughly six months of dated result pages
(`/auction-results/melbourne/YYYY-MM-DD/`), which is the only public record
that says a specific address failed to sell on a specific Saturday. REA
publishes no pass-in history at all, and property.com.au's timeline covers
sold / rent / leased / withdrawn only — a pass-in is none of those.

The page embeds its state as `__NEXT_DATA__`, so we parse that rather than
the DOM. Result codes are mapped through the same table-driven config as
everything else (outcome_mapping.domain).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

_NEXT_DATA = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

URL_TEMPLATE = "https://www.domain.com.au/auction-results/{city}/{day}/"


class DomainResultsError(Exception):
    pass


@dataclass
class DomainRow:
    address: str          # "40 Allan St" / "3/12 Smith St"
    suburb: str
    postcode: Optional[str]
    result_raw: str       # AUPI / AUSD / AUVB / ...
    property_type: Optional[str] = None
    bedrooms: Optional[int] = None
    agency: Optional[str] = None
    price_text: Optional[str] = None
    url: Optional[str] = None


def week_url(day: date, city: str = "melbourne") -> str:
    return URL_TEMPLATE.format(city=city, day=day.isoformat())


def _address_of(listing: dict) -> str:
    parts = [
        str(listing.get("streetNumber") or "").strip(),
        str(listing.get("streetName") or "").strip(),
        str(listing.get("streetType") or "").strip(),
    ]
    street = " ".join(p for p in parts if p)
    unit = str(listing.get("unitNumber") or "").strip()
    return f"{unit}/{street}" if unit else street


def parse_week(html: str) -> tuple[Optional[date], list[DomainRow]]:
    """(auction date, rows) from a dated auction-results page."""
    m = _NEXT_DATA.search(html)
    if not m:
        raise DomainResultsError(
            "__NEXT_DATA__ payload not found — structure drift, a redirect, "
            "or a bot-challenge page.")
    try:
        payload = json.loads(m.group(1))
        props = payload["props"]["pageProps"]["componentProps"]
    except (json.JSONDecodeError, KeyError) as e:
        raise DomainResultsError(f"Unexpected Domain payload shape: {e}") from e

    auction_day = None
    raw_date = props.get("auctionDate")
    if raw_date:
        try:
            auction_day = datetime.fromisoformat(str(raw_date)).date()
        except ValueError:
            logger.warning("Could not parse Domain auctionDate %r", raw_date)

    rows: list[DomainRow] = []
    for group in props.get("salesListings") or []:
        for listing in group.get("listings") or []:
            try:
                address = _address_of(listing)
                if not address:
                    continue
                price = listing.get("price")
                if isinstance(price, dict):
                    price = price.get("display")
                rows.append(DomainRow(
                    address=address,
                    suburb=(listing.get("suburb") or group.get("suburb") or "").strip(),
                    postcode=str(listing.get("postcode") or "").strip() or None,
                    result_raw=str(listing.get("result") or "").strip(),
                    property_type=listing.get("propertyType"),
                    bedrooms=listing.get("bedrooms"),
                    agency=listing.get("agencyName"),
                    price_text=price if isinstance(price, str) else None,
                    url=listing.get("domainPropertyDetailsUrl"),
                ))
            except Exception:
                logger.exception("Failed to parse a Domain result row — skipping")
    return auction_day, rows
