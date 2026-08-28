"""Detecting a past failed auction from a campaign start date."""
from datetime import date
from pathlib import Path

import yaml

from passedin.auction_check import (
    CONFIRMED_PASS_IN,
    CONFIRMED_SOLD,
    NORMAL,
    POSSIBLE_PASS_IN,
    PROBABLE_PASS_IN,
    assess,
    candidate_saturdays,
    price_is_single_figure,
)
from passedin.config import Config
from passedin.model import PropertyRecord, make_property_id
from passedin.normalise import normalise_address, normalise_suburb
from passedin.sources.domain_results import parse_week, week_url
from passedin.store import Store

TODAY = date(2026, 8, 13)


def _config(tmp_path):
    with open(Path(__file__).resolve().parent.parent / "config.yaml") as f:
        raw = yaml.safe_load(f)
    raw["run"]["data_dir"] = str(tmp_path / "data")
    return Config(raw, tmp_path)


# --- candidate Saturdays -----------------------------------------------------

def test_candidate_saturdays_from_the_worked_example():
    """13 Fern Avenue, Windsor: listed 17 Jun 2026 (a Wednesday) gives
    11 / 18 / 25 Jul, with 18 Jul the four-week base case."""
    days = candidate_saturdays(date(2026, 6, 17), today=TODAY)
    assert days == [date(2026, 7, 11), date(2026, 7, 18), date(2026, 7, 25)]
    assert all(d.weekday() == 5 for d in days)


def test_future_saturdays_are_dropped():
    # An auction that hasn't happened yet can't have been passed in.
    days = candidate_saturdays(date(2026, 8, 1), today=TODAY)
    assert all(d <= TODAY for d in days)


def test_campaign_starting_on_a_saturday_stays_on_saturdays():
    days = candidate_saturdays(date(2026, 6, 20), today=TODAY)   # a Saturday
    assert days == [date(2026, 7, 11), date(2026, 7, 18), date(2026, 7, 25)]


# --- price shape --------------------------------------------------------------

def test_single_figure_price_detection():
    assert price_is_single_figure("$1,125,000") is True
    assert price_is_single_figure("PRIVATE SALE: $980,000") is True
    assert price_is_single_figure("$980,000 - $1,078,000") is False
    assert price_is_single_figure("Auction $1,100,000") is False
    assert price_is_single_figure("Contact Agent") is False
    assert price_is_single_figure(None) is False


# --- assessment ---------------------------------------------------------------

def _tracked(store, address="13 Fern Avenue", postcode="3181"):
    return normalise_address(address).norm, postcode


def _assess(store, config, *, start=date(2026, 6, 17), days=57,
            price="$1,125,000", address="13 Fern Avenue", postcode="3181"):
    return assess(address_norm=normalise_address(address).norm,
                  postcode=postcode, campaign_start=start,
                  days_on_market=days, price_text=price, store=store,
                  config=config, fetcher=None, today=TODAY,
                  allow_network=False)


def test_local_scan_history_confirms_a_pass_in(tmp_path):
    """This tool's own weekly scan is the cheapest proof available."""
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    addr = normalise_address("13 Fern Avenue")
    store.upsert_records([PropertyRecord(
        property_id=make_property_id(addr.norm, normalise_suburb("Windsor"), "3181"),
        address_raw="13 Fern Avenue", address_norm=addr.norm,
        street_number=addr.street_number, street=addr.street,
        suburb="Windsor", postcode="3181", outcome="PASSED_IN",
        outcome_raw="PASSED_IN", week_ending="2026-07-19")])

    r = _assess(store, config)
    assert r.state == CONFIRMED_PASS_IN
    assert r.source == "local-scan"
    assert r.auction_day == date(2026, 7, 18)   # Saturday of that results week
    assert r.confirmed and r.worth_a_call
    store.close()


def test_domain_archive_confirms_a_pass_in(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    store.save_domain_week("2026-07-18", [{
        "address_norm": normalise_address("13 Fern Avenue").norm,
        "suburb": "Prahran", "postcode": "3181", "result_raw": "AUPI",
        "outcome": "PASSED_IN", "agency": "Jellis Craig Stonnington",
    }])
    r = _assess(store, config)
    assert r.state == CONFIRMED_PASS_IN
    assert r.source == "domain-archive"
    assert r.auction_day == date(2026, 7, 18)
    assert r.result_raw == "AUPI"
    store.close()


def test_suburb_disagreement_does_not_split_the_property(tmp_path):
    """property.com.au files this address as Windsor; REA says Prahran.
    Matching keys on postcode so it stays one property."""
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    store.save_domain_week("2026-07-18", [{
        "address_norm": normalise_address("13 Fern Avenue").norm,
        "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUPI", "outcome": "PASSED_IN",
    }])
    # Asking about the same address under the other suburb name still hits.
    r = _assess(store, config)
    assert r.state == CONFIRMED_PASS_IN
    store.close()


def test_postponed_then_passed_in_reports_the_pass_in(tmp_path):
    """Real case: 13 Fern Avenue was postponed on 11 Jul and passed in on
    25 Jul. A postponement is a scheduling change, not a failure, so the
    pass-in must be the headline — and both events are worth showing."""
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    norm = normalise_address("13 Fern Avenue").norm
    store.save_domain_week("2026-07-11", [{
        "address_norm": norm, "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUPP", "outcome": "POSTPONED"}])
    store.save_domain_week("2026-07-25", [{
        "address_norm": norm, "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUPI", "outcome": "PASSED_IN"}])

    r = _assess(store, config)
    assert r.state == CONFIRMED_PASS_IN
    assert r.auction_day == date(2026, 7, 25)      # not the postponement
    assert r.result_raw == "AUPI"
    joined = " ".join(r.reasons)
    assert "2026-07-11 AUPP" in joined and "2026-07-25 AUPI" in joined
    assert "rescheduled or run twice" in joined
    store.close()


def test_postponed_alone_is_not_a_pass_in(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    store.save_domain_week("2026-07-11", [{
        "address_norm": normalise_address("13 Fern Avenue").norm,
        "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUPP", "outcome": "POSTPONED"}])
    r = _assess(store, config)
    assert r.state != CONFIRMED_PASS_IN
    assert any("postponed" in x.lower() for x in r.reasons)
    store.close()


def test_probable_pass_in_when_stale_with_a_single_figure(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    r = _assess(store, config)          # 57 days, single figure, no records
    assert r.state == PROBABLE_PASS_IN
    assert r.worth_a_call
    assert not r.confirmed
    assert any("never report a failure" in x for x in r.reasons)
    store.close()


def test_absent_from_results_is_never_reported_as_no_auction(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    store.save_domain_week("2026-07-18", [{
        "address_norm": normalise_address("99 Other Street").norm,
        "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUSD", "outcome": "SOLD",
    }])
    r = _assess(store, config)
    assert r.state != NORMAL                    # not dismissed
    assert r.state == PROBABLE_PASS_IN
    assert any("not proof there was no auction" in x for x in r.reasons)
    store.close()


def test_sold_result_is_flagged_for_checking_not_treated_as_a_lead(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    store.save_domain_week("2026-07-18", [{
        "address_norm": normalise_address("13 Fern Avenue").norm,
        "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUSD", "outcome": "SOLD",
    }])
    r = _assess(store, config)
    assert r.state == CONFIRMED_SOLD
    assert r.worth_a_call is False
    assert any("fell through" in x for x in r.reasons)
    store.close()


def test_range_price_on_a_stale_listing_is_only_possible(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    r = _assess(store, config, price="$950,000 - $1,000,000")
    assert r.state == POSSIBLE_PASS_IN
    store.close()


def test_fresh_listing_is_normal(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    r = _assess(store, config, start=date(2026, 8, 10), days=3,
                price="$950,000 - $1,000,000")
    assert r.state == NORMAL
    assert r.candidates == []      # too recent for any auction to have run
    store.close()


def test_no_campaign_start_yields_nothing(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    r = _assess(store, config, start=None, days=None)
    assert r.state == NORMAL
    assert any("No campaign start" in x for x in r.reasons)
    store.close()


# --- Domain archive parsing ---------------------------------------------------

def test_week_url():
    assert week_url(date(2026, 7, 18)) == \
        "https://www.domain.com.au/auction-results/melbourne/2026-07-18/"


def test_parse_week_reads_addresses_and_results():
    import json
    payload = {"props": {"pageProps": {"componentProps": {
        "auctionDate": "2026-07-18T00:00:00",
        "salesListings": [{"suburb": "Windsor", "listings": [
            {"unitNumber": "", "streetNumber": "13", "streetName": "Fern",
             "streetType": "Ave", "suburb": "Windsor", "postcode": "3181",
             "result": "AUPI", "propertyType": "House", "bedrooms": 2,
             "agencyName": "Jellis Craig"},
            {"unitNumber": "3", "streetNumber": "12", "streetName": "Smith",
             "streetType": "St", "suburb": "Prahran", "postcode": "3181",
             "result": "AUSD", "propertyType": "Unit", "bedrooms": 1,
             "agencyName": "Other"},
        ]}]}}}}
    html = ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(payload) + '</script>')
    day, rows = parse_week(html)
    assert day == date(2026, 7, 18)
    assert [r.address for r in rows] == ["13 Fern Ave", "3/12 Smith St"]
    assert [r.result_raw for r in rows] == ["AUPI", "AUSD"]
    assert rows[0].postcode == "3181"
