"""Filling REA's gaps from Domain, without spending fetches needlessly.

Two fetches are possible per property (profile, then listing), at premium
rates, so both are gated: the profile is only fetched when REA left the
land size blank, and the listing only when the campaign date still rests on
something resettable. Results are cached permanently.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from .dating import DateEstimate, parse_iso
from .normalise import normalise_suburb
from .sources.domain_profile import (
    PROFILE_URL,
    candidate_slugs,
    parse_listing_date,
    parse_profile,
)

logger = logging.getLogger(__name__)


def _suburb_variants(suburb: Optional[str], address: Optional[str],
                     config) -> list[str]:
    """Domain files some addresses under a neighbouring suburb, so try the
    obvious name first and then any configured aliases."""
    out = []
    if suburb:
        out.append(suburb)
    if address:
        parts = [p.strip() for p in address.split(",")]
        if len(parts) > 1 and parts[1] not in out:
            out.append(parts[1])
    aliases = config.get("domain_profile.suburb_aliases") or {}
    for name in list(out):
        for alias in aliases.get(normalise_suburb(name), []):
            if alias not in out:
                out.append(alias)
    return out


def lookup(url: str, *, street: str, suburb: Optional[str],
           address: Optional[str], postcode: Optional[str],
           store, config, fetcher,
           want_land: bool = True, want_date: bool = True) -> dict:
    """Fetch (or reuse) Domain's profile for a property.

    Returns the stored row as a plain dict. Missing pieces simply stay
    absent — nothing here is fatal.
    """
    cached = store.get_domain_profile(url)
    if cached is not None:
        row = {k: cached[k] for k in cached.keys()}
        # Only go back for the listing date if it's still missing and wanted.
        if not (want_date and not row.get("date_listed")
                and row.get("domain_listing_url")):
            return row
    else:
        row = {}

    if not postcode or not street:
        logger.info("Not enough address detail to build a Domain slug for %s", url)
        return row

    state = config.get("domain_profile.state") or "vic"
    if not row.get("domain_listing_url"):
        profile = None
        slugs = candidate_slugs(street, _suburb_variants(suburb, address, config),
                                postcode, state)
        for slug in slugs[:int(config.get("domain_profile.max_slug_tries", 3) or 3)]:
            try:
                html = fetcher.fetch(PROFILE_URL.format(slug=slug))
            except Exception as e:
                logger.info("Domain profile fetch failed for %s: %s", slug, e)
                continue
            profile = parse_profile(html)
            if profile is not None:
                logger.info("Domain profile %s -> land=%s listing=%s",
                            slug, profile.land_size_sqm, profile.listing_url)
                row.update({
                    "slug": slug,
                    "land_size_sqm": profile.land_size_sqm,
                    "bedrooms": profile.bedrooms,
                    "bathrooms": profile.bathrooms,
                    "car_spaces": profile.car_spaces,
                    "property_type": profile.property_type,
                    "domain_listing_url": profile.listing_url,
                    "display_address": profile.display_address,
                })
                break
            logger.info("Domain profile slug %s did not resolve", slug)
        if profile is None:
            store.save_domain_profile(url, row)
            return row

    # The listing record is where the real campaign start lives.
    if want_date and row.get("domain_listing_url") and not row.get("date_listed"):
        try:
            html = fetcher.fetch(row["domain_listing_url"])
            listed = parse_listing_date(html)
            if listed:
                row["date_listed"] = listed.isoformat()
        except Exception as e:
            logger.info("Domain listing fetch failed for %s: %s",
                        row.get("domain_listing_url"), e)

    store.save_domain_profile(url, row)
    return row


def date_estimate(row: dict | None) -> Optional[DateEstimate]:
    """Domain's own listing date as a dating candidate.

    Documented and address-derived, so it survives a REA relist — which is
    exactly the case a Statement of Information can't see past.
    """
    if not row:
        return None
    day = parse_iso(row.get("date_listed"))
    if not day:
        return None
    return DateEstimate(day=day, basis="domain-listed",
                        detail=f"Domain listing dateListed {day.isoformat()}")


def needs_lookup(*, land_size, campaign_basis: Optional[str], config) -> bool:
    """Is a Domain fetch actually worth paying for on this property?"""
    if not config.get("domain_profile.enabled", True):
        return False
    if land_size in (None, 0) and config.get("domain_profile.for_land_size", True):
        return True
    # A campaign start that rests on a resettable or inferred source is worth
    # replacing with Domain's documented date.
    weak = set(config.get("domain_profile.weak_bases")
               or ["current-listing", "observed-floor", "soi-document",
                   "soi-median-period"])
    return campaign_basis in weak and config.get("domain_profile.for_dates", True)
