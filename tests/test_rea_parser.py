from conftest import make_entry_page, make_rea_row, make_suburb_page

import pytest

from passedin.sources import rea


def test_parse_entry(rea_cfg):
    html = make_entry_page([("Brunswick", "3056", 12), ("Coburg", "3058", 7)])
    suburbs, week = rea.parse_entry(html, rea_cfg)
    assert week == "2026-08-09"
    assert [s.name for s in suburbs] == ["Brunswick", "Coburg"]
    assert suburbs[0].slug == "brunswick-vic-3056"
    assert suburbs[0].result_count == 12


def test_parse_suburb_passed_in(rea_cfg):
    html = make_suburb_page([
        make_rea_row(address="5 Ballarat Court", outcome="PASSED_IN"),
        make_rea_row(address="40 Allan Street", outcome="SOLD_AUCTION_UNKNOWN",
                     price="$786,000"),
        make_rea_row(address="8 Vendor Lane", outcome="PASSED_IN_VENDOR_BID",
                     max_bid="Last bid $690,000", land=480),
    ])
    rows = rea.parse_suburb(html, rea_cfg)
    assert len(rows) == 3
    pi, sold, vb = rows
    assert pi.suburb == "Testville"
    assert pi.postcode == "3999"
    assert pi.outcome_raw == "PASSED_IN"
    assert pi.week_ending == "2026-08-09"
    assert pi.price_display is None
    assert sold.price_display == "$786,000"
    assert vb.max_bid_display == "Last bid $690,000"
    assert vb.land_size_sqm == 480.0
    assert vb.agency_name == "Ray White - Testville"
    assert vb.agent_name == "Alex Agent"
    # tracking params stripped, host normalised
    assert vb.source_url == "https://www.realestate.com.au/sold/property-house-vic-testville-151789868"
    assert vb.source_listing_id == "151789868"
    # image template resolved to a concrete size
    assert vb.image_url == "https://i2.au.reastatic.net/500x375/abc123/image.jpg"


def test_parse_suburb_untrusted_canonical_fallback(rea_cfg):
    # canonicalLink pointing interstate must not be trusted (observed live).
    row = make_rea_row(url="/property-house-qld-bli+bli-129456410")
    row["listing"]["trackedCanonical"] = {}
    html = make_suburb_page([row])
    rows = rea.parse_suburb(html, rea_cfg)
    assert rows[0].source_url is None


def test_missing_payload_raises(rea_cfg):
    with pytest.raises(rea.ReaParseError):
        rea.parse_entry("<html><body>a challenge page</body></html>", rea_cfg)


def test_row_failure_does_not_kill_page(rea_cfg):
    good = make_rea_row(address="1 Good Street")
    bad = {"eventId": "x", "outcome": None, "listing": None}  # malformed row
    html = make_suburb_page([bad, good])
    rows = rea.parse_suburb(html, rea_cfg)
    assert len(rows) == 1
    assert rows[0].address_raw == "1 Good Street"
