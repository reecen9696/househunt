"""Campaign dating: URL timestamp extraction and source ranking.

The portals' days-on-market counter resets on relist, so these tests pin
down the reconstruction logic that replaces it.
"""
from datetime import date

from passedin.dating import (
    BASES,
    DateEstimate,
    date_from_auction,
    date_from_median_period,
    date_from_url,
    resolve,
)

TODAY = date(2026, 8, 12)


# --- Statement of Information URL timestamps ---------------------------------
# The four cases below are real agency CDN URLs from three different agency
# platforms, each stamping the upload time differently.

def test_soi_url_unix_seconds():
    assert date_from_url(
        "https://cdn.agency.com.au/docs/45-smith-st-1726712725-60120-"
        "StatementofInformation.pdf", TODAY) == date(2024, 9, 19)


def test_soi_url_unix_seconds_with_text_digits():
    # Trailing "24THAPR" and the 5-digit job number must not be misread.
    assert date_from_url(
        "https://files.agency.com/1777003617-67073-"
        "ONLINESOIAPPROVEDMING24THAPR.pdf", TODAY) == date(2026, 4, 24)


def test_soi_url_yyyymmddhhmmss():
    assert date_from_url(
        "https://cdn.example.com/soi/abc_1b90_20240729031027.pdf",
        TODAY) == date(2024, 7, 29)


def test_soi_url_unix_milliseconds():
    assert date_from_url(
        "https://cdn.example.com/uploads/1736834858656-xzrs315y7hd-"
        "9f/SOI.pdf", TODAY) == date(2025, 1, 14)


def test_soi_url_date_path_variant():
    assert date_from_url(
        "https://cdn.example.com/uploads/2024/07/29/statement.pdf",
        TODAY) == date(2024, 7, 29)


def test_listing_id_is_not_read_as_a_timestamp():
    """A long numeric listing ID is exactly what a loose pattern misreads.
    2019483746 as unix seconds lands in 2034, so it must be rejected rather
    than returned with false confidence."""
    assert date_from_url(
        "https://www.domain.com.au/13-fern-avenue-prahran-vic-3181-2019483746",
        TODAY) is None


def test_implausible_dates_rejected():
    assert date_from_url("https://x.example/doc-946684800-old.pdf", TODAY) is None  # 2000
    assert date_from_url("https://x.example/doc-2019483746.pdf", TODAY) is None     # future
    assert date_from_url("", TODAY) is None


def test_soi_median_period_end():
    text = ("Median sale price $1,150,000 for the period "
            "01 July 2023 to 30 June 2024, source: Vic Property Sales Report")
    assert date_from_median_period(text, TODAY) == date(2024, 6, 30)
    assert date_from_median_period("no period stated", TODAY) is None


def test_auction_inference_subtracts_campaign_length():
    # Passed in 16 May -> advertised from about 18 April at the latest.
    assert date_from_auction(date(2026, 5, 16), campaign_days=28) == date(2026, 4, 18)
    assert date_from_auction(None) is None


# --- ranking ------------------------------------------------------------------

def _est(basis, day, detail=None):
    return DateEstimate(day=day, basis=basis, detail=detail)


def test_every_basis_is_classified():
    for basis in ("soi-document", "archive-capture", "history-page",
                  "auction-inferred", "soi-median-period", "observed-floor",
                  "current-listing"):
        assert basis in BASES


def test_honest_listing_is_not_flagged():
    """dateListed survived the pass-in: both clocks agree, nothing hidden."""
    listed = date(2026, 6, 20)  # 53 days before TODAY
    d = resolve([
        _est("current-listing", listed),
        _est("auction-inferred", listed),
    ], TODAY)
    assert d.days_on_market(TODAY) == 53
    assert d.days_claimed(TODAY) == 53
    assert d.clock_reset(today=TODAY) is False
    assert d.hidden_days(TODAY) == 0


def test_single_reset_is_flagged_with_hidden_days():
    d = resolve([
        _est("current-listing", date(2026, 7, 29)),   # portal claims 14 days
        _est("soi-document", date(2026, 4, 18)),      # SOI says 116 days
    ], TODAY)
    assert d.days_claimed(TODAY) == 14
    assert d.days_on_market(TODAY) == 116
    assert d.hidden_days(TODAY) == 102
    assert d.clock_reset(today=TODAY) is True
    assert d.is_documented is True


def test_two_failures_with_reset_after_the_second():
    d = resolve([
        _est("current-listing", date(2026, 7, 20)),
        _est("auction-inferred", date(2026, 6, 1)),   # second auction
        _est("soi-document", date(2026, 2, 10)),      # original campaign
    ], TODAY)
    assert d.start.basis == "soi-document"
    assert d.start.day == date(2026, 2, 10)
    assert d.clock_reset(today=TODAY) is True


def test_recorded_history_date_beats_inference():
    d = resolve([
        _est("auction-inferred", date(2026, 4, 18)),
        _est("history-page", date(2026, 3, 1)),
    ], TODAY)
    assert d.start.basis == "history-page"
    assert d.is_documented is True


def test_floor_only_case():
    d = resolve([_est("observed-floor", date(2026, 7, 5))], TODAY)
    assert d.start.basis == "observed-floor"
    assert d.start.kind == "floor"
    assert d.is_documented is False
    assert d.clock_reset(today=TODAY) is False   # nothing to compare against


def test_no_auction_soi_case():
    """A private sale advertised for months: no auction to work back from,
    so the SOI is the only thing that dates it."""
    d = resolve([
        _est("current-listing", date(2026, 8, 1)),
        _est("soi-document", date(2026, 3, 15)),
    ], TODAY)
    assert d.start.basis == "soi-document"
    assert d.days_on_market(TODAY) == 150
    assert d.rests_only_on_current_listing is False


def test_archive_capture_beats_late_auction_inference():
    d = resolve([
        _est("auction-inferred", date(2026, 6, 10)),
        _est("archive-capture", date(2026, 2, 2)),
    ], TODAY)
    assert d.start.basis == "archive-capture"
    assert d.start.day == date(2026, 2, 2)


def test_trust_breaks_ties_only():
    same = date(2026, 5, 1)
    d = resolve([_est("observed-floor", same), _est("soi-document", same)], TODAY)
    assert d.start.basis == "soi-document"   # tie -> higher trust


def test_current_listing_only_is_marked_for_exclusion():
    d = resolve([_est("current-listing", date(2026, 8, 1))], TODAY)
    assert d.rests_only_on_current_listing is True


def test_implausible_candidates_are_discarded():
    d = resolve([
        _est("soi-document", date(1999, 1, 1)),       # too old
        _est("archive-capture", date(2027, 1, 1)),    # future
        _est("observed-floor", date(2026, 7, 1)),
    ], TODAY)
    assert d.start.basis == "observed-floor"


def test_no_candidates_resolves_to_nothing():
    d = resolve([], TODAY)
    assert d.start is None
    assert d.days_on_market(TODAY) is None
    assert d.clock_reset(today=TODAY) is False
