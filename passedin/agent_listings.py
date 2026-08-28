"""The listing date REA does publish — on the agent's own profile page.

A listing page states no date at all (`publishedDate` is null), which is why
campaign dating has had to reconstruct one from Statements of Information and
auction arithmetic. But the *agent's* profile page carries a roster of every
property they currently have on market, each stamped ``Listed 28 Jul 2026``.

That roster is server-rendered into the same ArgonautExchange payload the
auction-results pages use, under ``agentMapBuyListings`` — the full set, not
the three the page shows before you press "see more", and not behind the
Sold/For sale dropdown either. Switching that dropdown fires no request: both
channels are already in the HTML. So one fetch of one agent page dates every
property that agent is currently advertising, which is the cheapest date in
the whole pipeline — a single page covering ~20 properties instead of one
lookup each.

Read the date as "advertised by this date". It is REA's own record, so a
relist resets it, exactly like the portal counters described in `dating.py`.
A reset only ever moves the date *later*, so it stays a lower bound and is
safe to treat as documented — and because it is REA's claim, comparing it
against SOI or auction evidence is what finally exposes a reset on REA, which
until now only Domain could show.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Optional

from .dating import DateEstimate, plausible

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r"window\.ArgonautExchange\s*=\s*(\{.*?\});?\s*</script>",
                       re.DOTALL)
_EXCHANGE_KEY = "resi-agent_customer-profile-experience"
# The roster the dropdown reveals. `buyListings` holds only the first page
# (what renders before "see more"); the map variant holds them all.
_BUY_KEYS = ("agentMapBuyListings", "buyListings")

# "Listed 28 Jul 2026" / "Sold 15 Aug 2026"
_STATUS = re.compile(r"^\s*(Listed|Sold)\s+(\d{1,2}\s+\w{3,}\s+\d{4})\s*$",
                     re.IGNORECASE)

_LISTING_ID = re.compile(r"-(\d{6,})/?$")


def listing_id_from_url(url: str) -> Optional[str]:
    """/property-house-vic-caulfield+south-151882992 -> "151882992"."""
    if not url:
        return None
    m = _LISTING_ID.search(url.split("?")[0].rstrip("/"))
    return m.group(1) if m else None


def _unwrap(node):
    """ArgonautExchange values are sometimes JSON strings, sometimes objects."""
    if isinstance(node, str):
        try:
            return json.loads(node)
        except json.JSONDecodeError:
            return None
    return node


def agent_profile_urls(html: str) -> list[str]:
    """Agent profile links from a listing page, lead agent first.

    The lead agent is the one whose roster is most likely to carry the
    listing, and REA orders `listers` that way.
    """
    if not html:
        return []
    decoded = re.sub(r"\\+u002F", "/", html).replace("\\", "")
    out: list[str] = []
    for m in re.finditer(r'"(https://www\.realestate\.com\.au/agent/[a-z0-9\-]+)'
                         r'(?:\?[^"]*)?"', decoded, re.IGNORECASE):
        url = m.group(1)
        if url not in out:
            out.append(url)
    return out


def parse_agent_listings(html: str) -> dict[str, dict]:
    """For-sale roster from an agent profile page, keyed by listing id.

    Returns {} rather than raising when the payload is missing or shaped
    differently — a dating source that can't read a page must degrade to
    "no candidate", never take down the run.
    """
    if not html:
        return {}
    m = _STATE_RE.search(html)
    if not m:
        logger.info("No ArgonautExchange payload on agent page")
        return {}
    try:
        exchange = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.info("Agent page payload is not valid JSON: %s", e)
        return {}

    bucket = _unwrap(exchange.get(_EXCHANGE_KEY))
    if not isinstance(bucket, dict):
        return {}
    groups = _unwrap(bucket.get("AGENT_PROFILE_LISTINGS"))
    if not isinstance(groups, dict):
        return {}

    out: dict[str, dict] = {}
    for key in _BUY_KEYS:
        group = _unwrap(groups.get(key))
        listings = group.get("listings") if isinstance(group, dict) else None
        for row in listings or []:
            if not isinstance(row, dict):
                continue
            listing_id = str(row.get("id") or "").strip()
            if not listing_id or listing_id in out:
                continue
            address = row.get("address") or {}
            links = row.get("_links") or {}
            out[listing_id] = {
                "id": listing_id,
                "status_text": row.get("listingStatus"),
                "url": links.get("canonical"),
                "address": address.get("shortAddress"),
                "suburb": address.get("suburb"),
                "postcode": address.get("postcode"),
                "price_text": row.get("price"),
            }
    return out


def status_date(status_text: Optional[str], today: Optional[date] = None
                ) -> Optional[date]:
    """"Listed 28 Jul 2026" -> date(2026, 7, 28). Sold rows return None.

    A sold row is a past sale of a different campaign, so reading its date as
    a listing date would invent history.
    """
    if not status_text:
        return None
    m = _STATUS.match(str(status_text))
    if not m or m.group(1).lower() != "listed":
        return None
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            day = datetime.strptime(m.group(2).strip(), fmt).date()
        except ValueError:
            continue
        return day if plausible(day, today) else None
    return None


def estimate_from_roster(listing_url: str, roster: dict[str, dict],
                         today: Optional[date] = None) -> Optional[DateEstimate]:
    """The listed date for one property, out of an agent's parsed roster."""
    listing_id = listing_id_from_url(listing_url)
    if not listing_id:
        return None
    row = roster.get(listing_id)
    if not row:
        return None
    day = status_date(row.get("status_text"), today)
    if not day:
        return None
    return DateEstimate(
        day=day, basis="agent-listed",
        detail=f"agent profile roster: {row.get('status_text')}")
