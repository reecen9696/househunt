"""Fixture builders that mirror the real REA page structure captured live
on 2026-08-12: window.ArgonautExchange with double-encoded JSON state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from passedin.config import Config  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def config(tmp_path):
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    raw["run"]["data_dir"] = str(tmp_path / "data")
    return Config(raw, tmp_path)


@pytest.fixture
def rea_cfg(config):
    return config.get("sources.rea")


def wrap_argonaut(field: str, state: dict) -> str:
    """Build a page embedding state exactly as REA does (double-encoded)."""
    exchange = {"resi-property_sales-events-web": {field: json.dumps(state)}}
    return (
        "<!doctype html><html><head><title>Auction Results</title></head><body>"
        "<div id='app'>rendered content</div>"
        f"<script>window.ArgonautExchange = {json.dumps(exchange)};</script>"
        "</body></html>"
    )


def make_rea_row(address="12 Smith Street", outcome="PASSED_IN", price=None,
                 max_bid=None, ptype="House", beds=3, land=None, event_id="1",
                 agency="Ray White - Testville", agent="Alex Agent",
                 url="/sold/property-house-vic-testville-151789868"):
    row = {
        "eventId": event_id,
        "eventType": "AUCTION_NO_SALE" if "PASSED" in outcome or outcome in
                     ("WITHDRAWN", "NO_BID") else "AUCTION_SALE",
        "outcome": {"value": outcome, "display": outcome.replace("_", " ").title()},
        "listing": {
            "canonicalLink": url,
            "trackedCanonical": {"templatedUrl": f"https://www.realestate.com.au{url}?cid=x"},
            "mainImage": {"templatedUrl": "https://i2.au.reastatic.net/{size}/abc123/image.jpg"},
            "address": address,
            "numBedrooms": beds,
            "size": ({"type": "LAND_SIZE", "display": str(land),
                      "unit": "SQUARE_METRES"} if land else None),
            "propertyType": {"display": ptype},
            "listingCompany": {"name": agency},
            "listers": [{"id": "99", "name": agent}],
        },
        "showGetInTouch": False,
    }
    if price:
        row["price"] = {"display": price}
    if max_bid:
        row["auctionMaxBid"] = {"display": max_bid}
    return row


def make_suburb_page(rows, suburb="Testville", postcode="3999",
                     end_date="Sun 09 Aug 2026") -> str:
    state = {
        "status": 200,
        "body": {
            "startDate": "Mon 03 Aug 2026",
            "endDate": end_date,
            "data": {
                "state": {"urlValue": "vic"},
                "suburb": {"urlValue": f"{suburb.lower()}-vic-{postcode}",
                           "name": suburb, "postcode": postcode},
                "auctionResults": rows,
            },
        },
    }
    return wrap_argonaut("location", state)


def make_entry_page(suburbs, end_date="Sun 09 Aug 2026") -> str:
    state = {
        "status": 200,
        "body": {
            "startDate": "Mon 03 Aug 2026",
            "endDate": end_date,
            "data": {
                "state": {"urlValue": "vic"},
                "stateStats": {"auction": {"clearanceRate": 57}},
                "suburbResults": [
                    {"suburb": {"urlValue": f"{name.lower().replace(' ', '-')}-vic-{pc}",
                                "name": name, "postcode": pc},
                     "resultCount": count}
                    for name, pc, count in suburbs
                ],
            },
        },
    }
    return wrap_argonaut("state", state)
