"""Dating wired to the store: auction anchor, permanent cache, and the
honest-path case that must not be flagged.
"""
from datetime import date

import yaml
from pathlib import Path

from passedin.config import Config
from passedin.dating_sources import find_soi_links, soi_estimates
from passedin.dating_view import dating_for_row
from passedin.model import PropertyRecord, make_property_id
from passedin.normalise import normalise_address, normalise_suburb
from passedin.store import Store

TODAY = date(2026, 8, 12)


def _config(tmp_path):
    with open(Path(__file__).resolve().parent.parent / "config.yaml") as f:
        raw = yaml.safe_load(f)
    raw["run"]["data_dir"] = str(tmp_path / "data")
    return Config(raw, tmp_path)


def _track(store, **kw):
    # added_date is pinned so the tests don't drift as real time passes.
    payload = {"url": "https://www.realestate.com.au/property-house-vic-windsor-1",
               "added_date": TODAY.isoformat()}
    payload.update(kw)
    store.upsert_tracked(payload)
    return store.list_tracked()[0]


def test_auction_anchor_dates_a_tracked_property(tmp_path):
    """A pass-in the scan recorded gives a hard date to work back from,
    even though the listing itself publishes none."""
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    addr = normalise_address("12 Peel Street")
    store.upsert_records([PropertyRecord(
        property_id=make_property_id(addr.norm, normalise_suburb("Windsor"), "3181"),
        address_raw="12 Peel Street", address_norm=addr.norm,
        street_number=addr.street_number, street=addr.street,
        suburb="Windsor", postcode="3181", outcome="PASSED_IN",
        outcome_raw="PASSED_IN", week_ending="2026-05-17",
    )])
    # Full address as captured from a listing page, not the street-only form
    # the auction results store.
    row = _track(store, address="12 Peel Street, Windsor, Vic 3181",
                 suburb="Windsor")

    d = dating_for_row(row, store, config, TODAY)
    # 17 May results week minus a 28-day campaign -> 19 April
    assert d["campaign_basis"] == "auction-inferred"
    assert d["campaign_start"] == "2026-04-19"
    assert d["ever_auctioned"] is True
    assert d["documented"] is False        # inference, not a record
    store.close()


def test_honest_listing_stays_clean(tmp_path):
    """dateListed survived the pass-in: both clocks read 53 days, no flag."""
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    addr = normalise_address("12 Peel Street")
    store.upsert_records([PropertyRecord(
        property_id=make_property_id(addr.norm, normalise_suburb("Windsor"), "3181"),
        address_raw="12 Peel Street", address_norm=addr.norm,
        street_number=addr.street_number, street=addr.street,
        suburb="Windsor", postcode="3181", outcome="PASSED_IN",
        outcome_raw="PASSED_IN", week_ending="2026-07-19",
    )])
    row = _track(store, address="12 Peel Street", suburb="Windsor",
                 date_listed="2026-06-20")   # 53 days before TODAY

    d = dating_for_row(row, store, config, TODAY)
    assert d["days_on_market"] == 53
    assert d["days_claimed"] == 53
    assert d["clock_reset"] is False
    assert d["hidden_days"] == 0
    store.close()


def test_clock_reset_detected_from_cached_soi(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    url = "https://www.realestate.com.au/property-house-vic-windsor-1"
    store.save_campaign_date(url, None, None, None, None, [
        {"day": "2026-04-18", "basis": "soi-document",
         "detail": "https://cdn.agency.com/soi-1744934400-1.pdf"},
    ])
    row = _track(store, address="12 Peel Street", suburb="Windsor",
                 date_listed="2026-07-29")   # portal claims 14 days

    d = dating_for_row(row, store, config, TODAY)
    assert d["days_claimed"] == 14
    assert d["days_on_market"] == 116
    assert d["hidden_days"] == 102
    assert d["clock_reset"] is True
    assert d["documented"] is True
    assert d["current_listing_only"] is False
    store.close()


def test_confirmed_auction_dates_the_campaign_behind_a_relist(tmp_path):
    """A relist re-issues the SOI, so dating alone sees only the new
    campaign. A real auction date pushes the start back where it belongs —
    and the earliest event (a postponement) bounds it best.
    """
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    norm = normalise_address("13 Fern Avenue").norm
    store.save_domain_week("2026-07-11", [{
        "address_norm": norm, "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUPP", "outcome": "POSTPONED"}])
    store.save_domain_week("2026-07-25", [{
        "address_norm": norm, "suburb": "Prahran", "postcode": "3181",
        "result_raw": "AUPI", "outcome": "PASSED_IN"}])
    row = _track(store, address="13 Fern Avenue, Prahran, Vic 3181",
                 suburb="Prahran", url="https://x.example/fern",
                 date_listed="2026-07-27")   # the relist

    d = dating_for_row(row, store, config, TODAY)
    # Earliest auction event 11 Jul, minus a 28-day campaign -> 13 Jun
    assert d["campaign_start"] == "2026-06-13"
    assert d["days_on_market"] == 60          # not the 16 the relist implies
    assert d["days_since_auction"] == 18      # since the 25 Jul pass-in
    assert d["auction"]["state"] == "CONFIRMED_PASS_IN"
    store.close()


def test_days_since_auction_absent_without_an_auction(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    row = _track(store, address="9 Nowhere Street, Windsor, Vic 3181",
                 suburb="Windsor")
    assert dating_for_row(row, store, config, TODAY)["days_since_auction"] is None
    store.close()


def test_floor_only_when_nothing_else_is_known(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _config(tmp_path)
    row = _track(store, address="9 Nowhere Street", suburb="Windsor")
    d = dating_for_row(row, store, config, TODAY)
    assert d["campaign_basis"] == "observed-floor"
    assert d["floor_only"] is True
    assert d["current_listing_only"] is False
    assert d["ever_auctioned"] is False
    store.close()


def test_campaign_date_cache_is_permanent(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    url = "https://x.example/p1"
    assert store.get_campaign_date(url) is None
    store.save_campaign_date(url, "2026-04-18", "soi-document", "documented",
                             "doc.pdf", [{"day": "2026-04-18",
                                          "basis": "soi-document"}])
    cached = store.get_campaign_date(url + "?utm=1")   # query stripped
    assert cached["start_date"] == "2026-04-18"
    assert cached["basis"] == "soi-document"
    store.close()


# --- SOI link discovery -------------------------------------------------------

def test_soi_links_prefer_statement_of_information():
    html = ('<a href="/docs/contract-of-sale.pdf">Contract</a>'
            '<a href="https://cdn.agency.com/45-smith-1726712725-60120-'
            'StatementofInformation.pdf">SOI</a>')
    links = find_soi_links(html, "https://www.realestate.com.au/property-1")
    assert "StatementofInformation" in links[0]


def test_soi_estimate_dates_from_the_document_url():
    html = ('<a href="https://cdn.agency.com/45-smith-1726712725-60120-'
            'StatementofInformation.pdf">Statement of Information</a>')
    out = soi_estimates(html, "https://www.realestate.com.au/property-1", TODAY)
    assert out[0].basis == "soi-document"
    assert out[0].day == date(2024, 9, 19)


def test_soi_last_modified_header(monkeypatch):
    """REA rehosts the SOI under a content-hash filename, so the publication
    time comes from the CDN header instead of the filename."""
    import passedin.dating_sources as ds

    class FakeResponse:
        headers = {"Last-Modified": "Sat, 08 Aug 2026 07:11:49 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ds.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    est = ds.soi_last_modified("https://i2.au.reastatic.net/abc/statement.pdf",
                               today=TODAY)
    assert est.basis == "soi-document"
    assert est.day == date(2026, 8, 8)


def test_soi_last_modified_rejects_implausible_header(monkeypatch):
    import passedin.dating_sources as ds

    class FakeResponse:
        headers = {"Last-Modified": "Mon, 01 Jan 1990 00:00:00 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ds.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert ds.soi_last_modified("https://x.example/statement.pdf", today=TODAY) is None


def test_soi_median_period_is_a_separate_weaker_candidate():
    html = ("<p>Median sale price $1,150,000 for the period "
            "01 July 2023 to 30 June 2024</p>")
    out = soi_estimates(html, "", TODAY)
    assert [e.basis for e in out] == ["soi-median-period"]
    assert out[0].day == date(2024, 6, 30)
