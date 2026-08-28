"""Tracked-property enrichment: given a listing URL, fetch the page through
the pluggable fetch layer and extract everything the tracker card needs.

Extraction mirrors the Chrome extension's logic (embedded JSON first, JSON-LD
and meta tags as fallback) so a URL pasted into the UI ends up as rich as an
extension capture. Every field is optional: a partial parse still stores.
"""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from .agent_listings import agent_profile_urls
from .moneyparse import parse_money_range

logger = logging.getLogger(__name__)

# Tried in order per field; group 1 wins. REA listing pages carry these keys
# in their embedded application JSON.
_PATTERNS: dict[str, list[str]] = {
    "price_text": [
        r'"marketingPriceRange"\s*:\s*"([^"]+)"',
        r'"priceText"\s*:\s*"([^"]+)"',
        r'"displayPrice"\s*:\s*"([^"]+)"',
        r'<span[^>]*property-price[^>]*>([^<]+)<',
    ],
    "address": [r'"fullAddress"\s*:\s*"([^"]+)"'],
    "suburb": [r'"address"\s*:\s*\{[^{}]*?"suburb"\s*:\s*"([^"]+)"'],
    "postcode": [r'"address"\s*:\s*\{[^{}]*?"postcode"\s*:\s*"([^"]+)"'],
    "property_type": [
        # {"id":"house","display":"House"} on listing pages; {"display":...}
        # on auction-results rows.
        r'"propertyType"\s*:\s*\{[^{}]{0,60}?"display"\s*:\s*"([^"]+)"',
        r'"propertyType"\s*:\s*"([^"]+)"',
    ],
    # REA embeds features as GraphQL state: "generalFeatures":{"bedrooms":
    # {"value":3,...},"bathrooms":{"value":1,...},"parkingSpaces":{"value":0}}
    # (matched against the backslash-stripped view of the page).
    "bedrooms": [
        r'"generalFeatures"\s*:\s*\{"bedrooms"\s*:\s*\{"value"\s*:\s*(\d+)',
        r'"bedrooms"\s*:\s*(\d+)',
    ],
    "bathrooms": [
        r'"generalFeatures"\s*:\s*\{[^[\]]{0,120}?"bathrooms"\s*:\s*\{"value"\s*:\s*(\d+)',
        r'"bathrooms"\s*:\s*(\d+)',
    ],
    "car_spaces": [
        r'"generalFeatures"\s*:\s*\{[^[\]]{0,240}?"parkingSpaces"\s*:\s*\{"value"\s*:\s*(\d+)',
        r'"carSpaces"\s*:\s*(\d+)',
    ],
    "land_size_sqm": [
        r'"propertySizes"\s*:\s*\{[^[\]]{0,160}?"land"\s*:\s*\{"displayValue"\s*:\s*"([\d,.]+)"',
        r'"landSize"\s*:\s*\{[^{}]*?"displayValue"\s*:\s*"?([\d,.]+)',
    ],
    # The map pin, as REA geocoded it:
    #   "display":{"fullAddress":"...","geocode":{"latitude":-37.8,"longitude":145.0}}
    # It sits inside the address block the anchor points at, so anchoring
    # keeps it away from an agency office or a related listing. This is what
    # makes route planning possible without a geocoding call per property.
    "latitude": [r'"geocode"\s*:\s*\{\s*"latitude"\s*:\s*(-?\d+\.?\d*)'],
    "longitude": [r'"geocode"\s*:\s*\{[^{}]{0,80}?"longitude"\s*:\s*(-?\d+\.?\d*)'],
    # REA listing pages carry no listing/published date (publishedDate is
    # null), so time-on-market falls back to the date the property was added
    # to the tracker. These stay in case another source exposes one.
    "date_listed": [
        r'"dateFirstListed"\s*:\s*"([^"]+)"',
        r'"dateListed"\s*:\s*"([^"]+)"',
    ],
    "inspection_text": [
        r'"inspections"\s*:\s*\[\s*\{[^\[\]]{0,120}?"longLabel"\s*:\s*"([^"]+)"',
        r'"inspections"\s*:\s*\[\s*\{[^\[\]]{0,200}?"shortDate"\s*:\s*"([^"]+)"',
    ],
    # Only present while an auction is scheduled ("auction":null otherwise).
    "auction_text": [
        r'"auction"\s*:\s*\{[^\[\]]{0,200}?"longLabel"\s*:\s*"([^"]+)"',
        r'"auction"\s*:\s*\{[^\[\]]{0,200}?"shortDate"\s*:\s*"([^"]+)"',
    ],
    # The machine-readable half of the same block, e.g.
    # "auction":{"dateTime":{"value":"2026-09-12T11:00:00+10:00"}}. Kept
    # separate from auction_text because sorting and "is it this weekend"
    # need a date, not the "Sat 12 Sep at 11:00 am" label.
    "auction_datetime": [
        r'"auction"\s*:\s*\{[^\[\]]{0,200}?"value"\s*:\s*"(\d{4}-\d{2}-\d{2}T[^"]+)"',
    ],
    "agency_name": [r'"listingCompany"\s*:\s*\{[^{}]*?"name"\s*:\s*"([^"]+)"'],
    "agent_name": [r'"listers"\s*:\s*\[\s*\{[^{}]*?"name"\s*:\s*"([^"]+)"'],
    # Listing pages spell it primaryColour, auction-results rows primaryColor.
    "agency_color": [
        r'"branding"\s*:\s*\{[^{}]*?"primaryColou?r"\s*:\s*"(#[0-9a-fA-F]{3,8})"',
    ],
    # og:image first: it is a page-level tag naming THIS listing's hero shot.
    # The JSON mainImage keys repeat per media block, and the nearest one to
    # the address anchor is not reliably the hero.
    "image_url": [
        r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
        r'"mainImage"\s*:\s*\{[^{}]*?"templatedUrl"\s*:\s*"([^"]+)"',
    ],
    "floorplan_url": [
        r'"floorplans?"\s*:\s*\[\s*\{[^{}]*?"(?:templatedUrl|url)"\s*:\s*"([^"]+)"',
        r'<img[^>]+alt="[^"]*floorplan[^"]*"[^>]+src="([^"]+)"',
        r'<img[^>]+src="([^"]+)"[^>]+alt="[^"]*floorplan[^"]*"',
    ],
}

_INTS = {"bedrooms", "bathrooms", "car_spaces"}
_FLOATS = {"latitude", "longitude"}


_TITLE_PATTERNS = (
    r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
    r'<title[^>]*>([^<]+)</title>',
)


def _address_from_title(html: str) -> Optional[str]:
    """The listing's address as the page titles itself.

    "8 Salisbury Street, Caulfield North, Vic 3161 - House for Sale - ..."
    -> "8 Salisbury Street, Caulfield North, Vic 3161"
    """
    for pattern in _TITLE_PATTERNS:
        m = re.search(pattern, html, re.IGNORECASE)
        if not m:
            continue
        text = m.group(1).strip()
        # Trim the site's own suffix; keep everything before it.
        text = re.split(r"\s+[-|]\s+(?:House|Unit|Apartment|Townhouse|Villa|Land|"
                        r"Property|Acreage|Rural|Block)\b", text)[0]
        text = re.split(r"\s+[-|]\s+realestate\.com\.au", text)[0].strip()
        if re.match(r"^[\d/]", text):     # must start with a street number
            return text
    return None


def _suburb_words_from_url(url: str) -> list[str]:
    """/property-house-vic-coburg+north-151733260 -> ["coburg", "north"]."""
    m = re.search(r"property-[a-z+]+-[a-z]{2,3}-([a-z+\-]+)-\d+/?$", url)
    if not m:
        return []
    return [w for w in re.split(r"[+\-]", m.group(1)) if w]


# Listing pages also embed a carousel of *recommended* properties. The main
# listing's own fields cluster within ~6KB of its address in the payload,
# while the first related listing sits ~9KB away — so matches beyond this
# window are another property's and must be rejected. A field the main
# listing genuinely lacks (e.g. "land":null) then stays absent, rather than
# silently inheriting a neighbour's value.
_ANCHOR_WINDOW = 7500

# Fields whose JSON key belongs to the main listing only, so they must NOT be
# anchored. The auction block sits ~7.1–7.7KB from the address depending on how
# much media the listing carries, which straddles the window above — anchoring
# it silently lost the auction date on longer pages while keeping it on
# shorter ones. Verified across 27 cached listing pages: `"auction":` appears
# exactly once, or not at all, so there is no neighbouring value to inherit.
_PAGE_LEVEL_JSON = {"auction_text", "auction_datetime"}


def _json_array_span(view: str, open_bracket: int) -> str:
    """The `[...]` slice starting at `open_bracket`, bracket-matched.

    A bounded window won't do here: the listing's own `payload` block repeats
    every inspection *and* the auction start alongside them, so a regex that
    scans past the array's closing bracket picks up the auction as if it were
    an inspection. Depth counting stops exactly at the end of the array.
    """
    depth = 0
    in_string = False
    for i in range(open_bracket, len(view)):
        c = view[i]
        if in_string:
            # Escapes are already stripped from this view, so a quote always
            # opens or closes a string.
            if c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return view[open_bracket:i + 1]
    return ""


# Inside the array each entry is
#   {"display":{...},"startTime":"2026-08-29T10:00:00+10:00",
#    "endTime":"2026-08-29T10:30:00+10:00"}
_INSPECTION_TIMES = re.compile(
    r'"startTime"\s*:\s*"([^"]+)"\s*,\s*"endTime"\s*:\s*"([^"]+)"')


def parse_inspections(html: str) -> list[dict]:
    """Every scheduled open-for-inspection on a listing page, in time order.

    REA embeds the list twice — once as `Inspection` and once as
    `PersonalisedInspection` for its planner — with identical times, so the
    two are merged and de-duplicated on (start, end). Both carry timezone
    offsets, which are kept verbatim: Melbourne switches to daylight saving
    in October and a naive local time would silently shift an itinerary by an
    hour.

    Returns [{"start_time": iso, "end_time": iso}], empty when the agent has
    scheduled none (REA writes `"inspections":[]`, a positive statement that
    there is nothing to attend rather than a parse failure).
    """
    view = re.sub(r"\\+u002F", "/", html).replace("\\", "")
    seen: dict[tuple[str, str], dict] = {}
    for m in re.finditer(r'"inspections"\s*:\s*(\[)', view):
        for start, end in _INSPECTION_TIMES.findall(
                _json_array_span(view, m.start(1))):
            seen.setdefault((start, end), {"start_time": start, "end_time": end})
    return sorted(seen.values(), key=lambda i: i["start_time"])


def _anchored(pattern: str, view: str, anchor: int | None):
    """Closest match to the main listing's block, within the anchor window."""
    best, best_distance = None, None
    for m in re.finditer(pattern, view, re.IGNORECASE | re.DOTALL):
        if anchor is None:
            return m
        distance = abs(m.start() - anchor)
        if distance > _ANCHOR_WINDOW:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = m, distance
    return best


def parse_listing(html: str, url: str) -> dict:
    clean_url = url.split("?")[0]
    data: dict = {"url": clean_url}

    # The application state is JSON nested inside JS strings, so quotes and
    # slashes carry one or more levels of escaping (\", \\\", /).
    # Decode slashes, then match against a backslash-stripped view so one
    # pattern set covers every escape depth; the raw view still catches DOM
    # attributes and JSON-LD (which the stripping could mangle).
    decoded = re.sub(r"\\+u002F", "/", html)
    decoded = re.sub(r"\\+u0026", "&", decoded)
    stripped = decoded.replace("\\", "")

    # The page title names this listing and nothing else, so it is the
    # authoritative address. Needed because a page also carries the
    # *agency's* address, which shares the suburb and often appears first —
    # matching on suburb alone picked the agency's office.
    page_address = _address_from_title(html)
    if page_address:
        data["address"] = page_address

    # Anchor everything else to wherever that address appears in the payload.
    suburb_words = _suburb_words_from_url(clean_url)
    anchor = None
    best_score = -1.0
    for m in re.finditer(r'"fullAddress"\s*:\s*"([^"]+)"', stripped):
        candidate = m.group(1).strip()
        if page_address:
            score = SequenceMatcher(None, candidate.lower(),
                                    page_address.lower()).ratio()
        else:
            score = sum(w in candidate.lower() for w in suburb_words) / 10.0
        if score > best_score:
            best_score, anchor = score, m.start()
            if not page_address:
                data["address"] = candidate

    for field, patterns in _PATTERNS.items():
        if field in data:
            continue
        for pattern in patterns:
            if pattern.lstrip().startswith("<"):
                # DOM/meta patterns describe the page itself (one per page),
                # so they need no anchoring — and must read the raw markup,
                # which backslash-stripping would mangle.
                m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            elif field in _PAGE_LEVEL_JSON:
                m = re.search(pattern, stripped, re.IGNORECASE | re.DOTALL)
            else:
                m = _anchored(pattern, stripped, anchor)
            if m:
                data[field] = m.group(1).strip()
                break

    # JSON-LD address fallback
    if "address" not in data:
        for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                             html, re.DOTALL):
            try:
                blocks = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            for o in blocks if isinstance(blocks, list) else [blocks]:
                addr = o.get("address") if isinstance(o, dict) else None
                if isinstance(addr, dict) and addr.get("streetAddress"):
                    data["address"] = addr["streetAddress"]
                    data.setdefault("suburb", addr.get("addressLocality"))
                    data.setdefault("postcode", addr.get("postalCode"))
                    break

    # normalisation
    for f in _INTS & data.keys():
        try:
            data[f] = int(data[f])
        except (TypeError, ValueError):
            data.pop(f, None)
    for f in _FLOATS & data.keys():
        try:
            data[f] = float(data[f])
        except (TypeError, ValueError):
            data.pop(f, None)
    if "land_size_sqm" in data:
        try:
            data["land_size_sqm"] = float(str(data["land_size_sqm"]).replace(",", ""))
        except (TypeError, ValueError):
            data.pop("land_size_sqm", None)
    if data.get("date_listed"):
        data["date_listed"] = data["date_listed"][:10]

    # How the property is being sold. REA writes "auction":null on everything
    # that isn't going under the hammer, so an explicit null is a positive
    # statement of private sale — worth distinguishing from "we couldn't
    # tell", which is what a missing key means.
    if data.get("auction_datetime"):
        data["auction_date"] = data.pop("auction_datetime")[:10]
        data["sale_method"] = "auction"
    else:
        data.pop("auction_datetime", None)
        if re.search(r'"auction"\s*:\s*null', stripped):
            data["sale_method"] = "private"
    # An auction block with a label but no parseable timestamp is still an
    # auction; the label carries the date for a human.
    if data.get("auction_text") and not data.get("sale_method"):
        data["sale_method"] = "auction"

    # The agent's profile page is where REA publishes a listing date, so the
    # link is worth keeping alongside the listing (see agent_listings.py).
    agents = agent_profile_urls(html)
    if agents:
        data["agent_profile_url"] = agents[0]
    for f, size in (("image_url", "800x600"), ("floorplan_url", "1000x750")):
        if data.get(f):
            data[f] = data[f].replace("{size}", size)
    if data.get("price_text"):
        low, high = parse_money_range(data["price_text"])
        data["price_low"], data["price_high"] = low, high
    return data


def fetch_listing(url: str, config) -> dict:
    """Fetch + parse a listing through the configured fetch layer.

    Raises on fetch failure — the caller decides whether to store a bare
    row anyway.
    """
    from .fetch import build_fetcher
    fetcher = build_fetcher(config)
    try:
        html = fetcher.fetch(url)
    finally:
        fetcher.close()
    data = parse_listing(html, url)
    found = [k for k in data if k != "url" and data[k] is not None]
    logger.info("Fetched listing %s -> fields: %s", url, ", ".join(sorted(found)))
    return data
