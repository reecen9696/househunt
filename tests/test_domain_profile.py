"""Domain property profiles: land size and a non-inferred listing date.

Fixtures mirror the real structures read off Domain on 2026-08-13 for
13 Fern Avenue — Apollo state on the profile, dateListed on the listing.
"""
import json
from datetime import date
from pathlib import Path

import yaml

from passedin.config import Config
from passedin.domain_lookup import date_estimate, needs_lookup
from passedin.sources.domain_profile import (
    candidate_slugs,
    parse_listing_date,
    parse_profile,
    profile_slug,
)
from passedin.store import Store


def _config(tmp_path, **overrides):
    with open(Path(__file__).resolve().parent.parent / "config.yaml") as f:
        raw = yaml.safe_load(f)
    raw["run"]["data_dir"] = str(tmp_path / "data")
    raw["domain_profile"].update(overrides)
    return Config(raw, tmp_path)


def _profile_html(land=203, listing_url="https://www.domain.com.au/13-fern-avenue-prahran-vic-3181-2020923711"):
    # Apollo stores field arguments in the key, e.g. landArea({"unit":...}).
    state = {
        "ROOT_QUERY": {},
        "Property:UHJvcGVydHk6RUMtNDY5NS1JUQ==": {
            "__typename": "Property",
            "propertyId": "EC-4695-IQ",
            "type": "House",
            "bedrooms": 2,
            "bathrooms": 1,
            "parkingSpaces": None,
            'landArea({"unit":"SQUARE_METERS"})': land,
            'internalArea({"unit":"SQUARE_METERS"})': None,
            "timeline": [],
            "listings": [{"__ref": "Listing:1"}],
            "address": {"__ref": "Address:1"},
        },
        "Listing:1": {"__typename": "Listing", "seoUrl": listing_url,
                      "status": "LIVE"},
        "Address:1": {"__typename": "Address",
                      "displayAddress": "13 Fern Avenue, Prahran VIC 3181"},
    }
    payload = {"props": {"pageProps": {"__APOLLO_STATE__": state}}}
    return ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(payload) + "</script>")


def test_profile_slug_and_candidates():
    assert profile_slug("13 Fern Avenue", "Windsor", "vic", "3181") == \
        "13-fern-avenue-windsor-vic-3181"
    # Unit addresses keep their number, with the slash flattened.
    assert profile_slug("3/12 Smith St", "Prahran", "vic", "3181") == \
        "3-12-smith-st-prahran-vic-3181"
    # Domain files this address under Windsor, REA under Prahran, so both
    # spellings have to be tried.
    slugs = candidate_slugs("13 Fern Avenue", ["Prahran", "Windsor"], "3181")
    assert slugs == ["13-fern-avenue-prahran-vic-3181",
                     "13-fern-avenue-windsor-vic-3181"]


def test_parse_profile_reads_land_size_and_listing_url():
    p = parse_profile(_profile_html())
    assert p.land_size_sqm == 203.0
    assert p.bedrooms == 2 and p.bathrooms == 1
    assert p.property_type == "House"
    assert p.listing_url.endswith("2020923711")
    assert p.display_address == "13 Fern Avenue, Prahran VIC 3181"


def test_parse_profile_on_a_404_returns_nothing():
    html = ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"statusCode": 404}}})
            + "</script>")
    assert parse_profile(html) is None
    assert parse_profile("<html>no payload</html>") is None


def test_parse_listing_date():
    """The listing record carries the real campaign start — 17 Jun for
    13 Fern Avenue, matching an independent property.com.au reading."""
    html = '{"dateListed":"2026-06-17T17:55:17.000","daysOnMarket":34}'
    assert parse_listing_date(html) == date(2026, 6, 17)
    # escaped payloads (the usual case) parse too
    assert parse_listing_date(r'{\"dateListed\":\"2026-06-17T00:00:00\"}') == \
        date(2026, 6, 17)
    assert parse_listing_date('{"createdOn":"2026-05-01T00:00:00"}') == \
        date(2026, 5, 1)
    assert parse_listing_date("nothing here") is None


def test_date_estimate_is_documented():
    est = date_estimate({"date_listed": "2026-06-17"})
    assert est.basis == "domain-listed"
    assert est.documented is True
    assert date_estimate({"date_listed": None}) is None
    assert date_estimate(None) is None


# --- when is a paid fetch actually warranted? --------------------------------

def test_lookup_wanted_when_land_size_missing(tmp_path):
    config = _config(tmp_path)
    assert needs_lookup(land_size=None, campaign_basis="auction-inferred",
                        config=config) is True


def test_lookup_wanted_when_dating_rests_on_something_resettable(tmp_path):
    config = _config(tmp_path)
    assert needs_lookup(land_size=203, campaign_basis="soi-document",
                        config=config) is True
    assert needs_lookup(land_size=203, campaign_basis="observed-floor",
                        config=config) is True


def test_lookup_skipped_when_rea_already_answered(tmp_path):
    """Land size known and the date already anchored to a hard auction —
    nothing left for Domain to add, so don't pay for it."""
    config = _config(tmp_path)
    assert needs_lookup(land_size=423, campaign_basis="auction-inferred",
                        config=config) is False


def test_lookup_disabled_by_config(tmp_path):
    config = _config(tmp_path, enabled=False)
    assert needs_lookup(land_size=None, campaign_basis="observed-floor",
                        config=config) is False


def test_profile_cache_round_trip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    url = "https://www.realestate.com.au/property-house-vic-prahran-1"
    assert store.get_domain_profile(url) is None
    store.save_domain_profile(url, {
        "slug": "13-fern-avenue-windsor-vic-3181", "land_size_sqm": 203.0,
        "date_listed": "2026-06-17",
        "domain_listing_url": "https://www.domain.com.au/x-2020923711"})
    row = store.get_domain_profile(url + "?utm=1")     # query stripped
    assert row["land_size_sqm"] == 203.0
    assert row["date_listed"] == "2026-06-17"
    store.close()
