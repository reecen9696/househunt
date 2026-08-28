"""realestate.com.au auction-results parser.

The pages are server-rendered with the application state embedded as JSON
in an inline script (window.ArgonautExchange). We parse that payload —
never the DOM — because embedded state is structured and far more stable
across redesigns than CSS classes. All JSON paths come from config.

Flow: entry page (/auction-results/vic) -> suburb index with result counts
      suburb page (/auction-results/<slug>) -> auctionResults rows
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..model import RawRow
from . import dig

logger = logging.getLogger(__name__)


class ReaParseError(Exception):
    pass


@dataclass
class SuburbRef:
    slug: str
    name: str
    postcode: Optional[str]
    result_count: int


def _extract_state(html: str, cfg: dict, field: str) -> dict:
    """Pull the embedded JSON out of the page.

    The payload is double-encoded: ArgonautExchange values are JSON strings.
    """
    pattern = cfg["state_script_regex"]
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        raise ReaParseError(
            "ArgonautExchange payload not found in page — structure drift or a "
            "bot-challenge page. Check sources.rea.state_script_regex."
        )
    try:
        exchange = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ReaParseError(f"ArgonautExchange payload is not valid JSON: {e}") from e

    bucket = exchange.get(cfg["exchange_key"])
    if not isinstance(bucket, dict) or field not in bucket:
        raise ReaParseError(
            f"Key {cfg['exchange_key']!r}.{field!r} missing from payload — "
            f"found {list(bucket) if isinstance(bucket, dict) else type(bucket)}"
        )
    return json.loads(bucket[field])


def _parse_week_ending(text: str | None) -> Optional[str]:
    """'Sun 09 Aug 2026' -> '2026-08-09'."""
    if not text:
        return None
    for fmt in ("%a %d %b %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    logger.warning("Could not parse week-ending date %r", text)
    return None


def parse_entry(html: str, cfg: dict) -> tuple[list[SuburbRef], Optional[str]]:
    """Entry page -> (suburb index, week-ending ISO date)."""
    state = _extract_state(html, cfg, cfg["entry_state_field"])
    paths = cfg["paths"]
    raw_suburbs = dig(state, paths["entry_suburbs"])
    if not isinstance(raw_suburbs, list):
        raise ReaParseError(
            f"Path {paths['entry_suburbs']!r} did not yield a list — structure drift."
        )
    suburbs = []
    for s in raw_suburbs:
        slug = dig(s, paths["suburb_slug"])
        name = dig(s, paths["suburb_name"])
        if not slug or not name:
            logger.warning("Suburb entry missing slug/name: %r", s)
            continue
        suburbs.append(SuburbRef(
            slug=slug,
            name=name,
            postcode=dig(s, paths["suburb_postcode"]),
            result_count=int(s.get("resultCount") or 0),
        ))
    week_ending = _parse_week_ending(dig(state, paths.get("entry_week_ending")))
    return suburbs, week_ending


def _clean_listing_url(url: str | None) -> Optional[str]:
    if not url:
        return None
    # trackedCanonical carries campaign query params; the path is the listing.
    url = url.split("?")[0]
    if url.startswith("/"):
        url = "https://www.realestate.com.au" + url
    return url


def parse_suburb(html: str, cfg: dict) -> list[RawRow]:
    """Suburb page -> RawRow per auction result."""
    state = _extract_state(html, cfg, cfg["suburb_state_field"])
    paths = cfg["paths"]
    rowp = paths["row"]

    results = dig(state, paths["results"])
    if results is None:
        raise ReaParseError(
            f"Path {paths['results']!r} missing from suburb payload — structure drift."
        )
    suburb_name = dig(state, paths.get("page_suburb_name")) or ""
    postcode = dig(state, paths.get("page_suburb_postcode"))
    week_ending = _parse_week_ending(dig(state, paths.get("week_ending")))

    rows: list[RawRow] = []
    for r in results:
        try:
            address = dig(r, rowp["address"])
            outcome_raw = dig(r, rowp["outcome"])
            if not address:
                logger.warning("Row without address in %s, skipping: %r",
                               suburb_name, str(r)[:200])
                continue

            land_size = None
            size_value = dig(r, rowp.get("land_size_value"))
            size_unit = dig(r, rowp.get("land_size_unit"))
            if size_value is not None and str(size_unit) == "SQUARE_METRES":
                try:
                    land_size = float(size_value)
                except (TypeError, ValueError):
                    pass

            agents = dig(r, rowp.get("agents")) or []
            agent_name = None
            if isinstance(agents, list) and agents:
                agent_name = dig(agents[0], rowp.get("agent_name", "name"))

            url = _clean_listing_url(dig(r, rowp.get("listing_url")))
            if not url:
                fallback = _clean_listing_url(dig(r, rowp.get("listing_url_fallback")))
                # canonicalLink is unreliable on REA (sometimes points at a
                # different state entirely) — only trust it if it looks local.
                if fallback and "-vic-" in fallback:
                    url = fallback

            listing_id = None
            if url:
                m = re.search(r"-(\d+)$", url)
                listing_id = m.group(1) if m else None

            image = dig(r, rowp.get("image"))
            if image:
                # The CDN URL is templated: .../{size}/<hash>.jpg
                image = str(image).replace("{size}", "500x375")

            bedrooms = dig(r, rowp.get("bedrooms"))
            rows.append(RawRow(
                source="rea",
                suburb=suburb_name,
                postcode=str(postcode) if postcode else None,
                address_raw=str(address).strip(),
                outcome_raw=str(outcome_raw) if outcome_raw is not None else "",
                outcome_display=dig(r, rowp.get("outcome_display")),
                event_type=dig(r, rowp.get("event_type")),
                price_display=dig(r, rowp.get("price_display")),
                max_bid_display=dig(r, rowp.get("max_bid_display")),
                property_type=dig(r, rowp.get("property_type")),
                bedrooms=int(bedrooms) if isinstance(bedrooms, (int, float)) else None,
                land_size_sqm=land_size,
                agency_name=dig(r, rowp.get("agency")),
                agent_name=agent_name,
                source_url=url,
                source_listing_id=listing_id or dig(r, rowp.get("event_id")),
                image_url=image,
                week_ending=week_ending,
            ))
        except Exception:
            logger.exception("Failed to parse a row in %s — skipping row", suburb_name)
    return rows
