"""domain.com.au property profiles — land size and a real listing date.

Two things REA doesn't reliably give us:

* **Land size.** REA only carries it when the agent supplied it, which is
  often not at all.
* **A listing date that isn't inferred.** REA publishes none, and a
  Statement of Information gets re-issued on relist, so it dates the new
  campaign rather than the original.

Domain answers both, and — unlike property.com.au, whose URLs need a
PropTrack property ID that REA's payload doesn't contain — its profile URLs
are derivable straight from an address:

    /property-profile/13-fern-avenue-windsor-vic-3181   -> land size, features
    /13-fern-avenue-prahran-vic-3181-2020923711         -> dateListed

Verified 2026-08-13 against 13 Fern Avenue: profile gave landArea 203 (which
matches REA exactly) and the listing gave dateListed 2026-06-17 — the same
date an independent property.com.au reading produced.

Caveat that bites: Domain and REA disagree about suburb boundaries. The same
3181 address is *Windsor* on the profile URL and *Prahran* on the listing.
So candidate slugs have to be tried, not assumed.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..dating import parse_iso

logger = logging.getLogger(__name__)

PROFILE_URL = "https://www.domain.com.au/property-profile/{slug}"

_NEXT_DATA = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


@dataclass
class DomainProfile:
    land_size_sqm: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    car_spaces: Optional[int] = None
    property_type: Optional[str] = None
    listing_url: Optional[str] = None      # the live Domain listing, if any
    display_address: Optional[str] = None
    domain_property_id: Optional[str] = None


def profile_slug(street: str, suburb: str, state: str, postcode: str) -> str:
    """"13 Fern Avenue", "Windsor", "vic", "3181" -> the URL slug."""
    parts = [street, suburb, state, postcode]
    text = " ".join(p.strip() for p in parts if p)
    text = re.sub(r"[^\w\s/-]", "", text.lower())
    text = text.replace("/", "-")
    return re.sub(r"[\s_]+", "-", text).strip("-")


def candidate_slugs(street: str, suburbs: list[str], postcode: str,
                    state: str = "vic") -> list[str]:
    """One slug per plausible suburb name — Domain's boundaries differ from
    REA's, so the first guess is often a 404."""
    out = []
    for suburb in suburbs:
        if not suburb:
            continue
        slug = profile_slug(street, suburb, state, postcode)
        if slug not in out:
            out.append(slug)
    return out


def _apollo(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        return {}
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    props = payload.get("props", {}).get("pageProps", {})
    if props.get("statusCode") == 404:
        return {}
    return props.get("__APOLLO_STATE__") or {}


def parse_profile(html: str) -> Optional[DomainProfile]:
    """Land size and the live listing URL from a property-profile page."""
    state = _apollo(html)
    key = next((k for k in state if k.startswith("Property:")), None)
    if key is None:
        return None
    prop = state[key]

    def field(name):
        # Apollo stores arguments in the key, e.g. landArea({"unit":...}).
        for k, v in prop.items():
            if k == name or k.startswith(name + "("):
                return v
        return None

    def deref(ref):
        return state.get(ref["__ref"]) if isinstance(ref, dict) and "__ref" in ref else ref

    listing_url = None
    listings = field("listings") or []
    if listings:
        first = deref(listings[0]) or {}
        listing_url = first.get("seoUrl") or first.get("url")

    land = field("landArea")
    address = deref(field("address")) or {}
    return DomainProfile(
        land_size_sqm=float(land) if isinstance(land, (int, float)) else None,
        bedrooms=field("bedrooms"),
        bathrooms=field("bathrooms"),
        car_spaces=field("parkingSpaces"),
        property_type=field("type"),
        listing_url=listing_url,
        display_address=address.get("displayAddress"),
        domain_property_id=field("propertyId"),
    )


# The listing page carries the campaign's real start. dateListed is the
# useful one; createdOn tracks it closely and serves as a fallback.
_DATE_FIELDS = ("dateListed", "createdOn", "dateAvailable")


def parse_listing_date(html: str) -> Optional[date]:
    text = re.sub(r"\\+u002F", "/", html).replace("\\", "")
    for name in _DATE_FIELDS:
        m = re.search(rf'"{name}"\s*:\s*"([\d]{{4}}-[\d]{{2}}-[\d]{{2}})', text)
        if m:
            day = parse_iso(m.group(1))
            if day:
                logger.info("Domain %s = %s", name, day)
                return day
    return None
