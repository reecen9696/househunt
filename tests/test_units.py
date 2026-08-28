import logging

from passedin.dedupe import dedupe
from passedin.filters import classify
from passedin.model import RawRow, make_property_id
from passedin.moneyparse import parse_money, parse_money_range
from passedin.normalise import normalise_address, normalise_suburb
from passedin.outcomes import OutcomeMapper
from passedin.pipeline import to_record
from passedin.rank import sort_items


# --- money -------------------------------------------------------------------

def test_parse_money():
    assert parse_money("$786,000") == 786000
    assert parse_money("Last bid $690,000") == 690000
    assert parse_money("$1.2m") == 1200000
    assert parse_money("$850k") == 850000
    assert parse_money("Contact Agent") is None
    assert parse_money(None) is None
    assert parse_money("$45") is None  # fragment guard


def test_parse_money_range():
    assert parse_money_range("$1,050,000 - $1,150,000") == (1050000, 1150000)
    assert parse_money_range("$950,000") == (950000, 950000)
    assert parse_money_range("no price") == (None, None)


# --- addresses ----------------------------------------------------------------

def test_normalise_address_expands_and_units():
    a = normalise_address("3/12 Smith St")
    b = normalise_address("Unit 3, 12 Smith Street")
    assert a.norm == b.norm == "3/12 smith street"
    assert a.street_number == "3/12"
    assert a.street == "smith street"


def test_normalise_suburb():
    assert normalise_suburb("Sth Yarra") == normalise_suburb("South Yarra")


def test_property_id_stable_across_sources():
    a = normalise_address("40 Allan St")
    b = normalise_address("40 Allan Street")
    assert make_property_id(a.norm, "brunswick", "3056") == \
           make_property_id(b.norm, "brunswick", "3056")


# --- outcomes -----------------------------------------------------------------

def test_outcome_mapping_and_unknown_is_loud(caplog):
    mapper = OutcomeMapper({"rea": {"PASSED_IN": "PASSED_IN"}})
    assert mapper.map("rea", "PASSED_IN") == "PASSED_IN"
    with caplog.at_level(logging.WARNING):
        assert mapper.map("rea", "SOME_NEW_LABEL") == "UNKNOWN"
    assert "UNRECOGNISED" in caplog.text
    assert mapper.unrecognised == {"rea:SOME_NEW_LABEL": 1}


# --- pipeline -----------------------------------------------------------------

def _row(**kw):
    defaults = dict(source="rea", suburb="Testville", postcode="3999",
                    address_raw="12 Smith St", outcome_raw="PASSED_IN",
                    week_ending="2026-08-09")
    defaults.update(kw)
    return RawRow(**defaults)


def _mapper():
    return OutcomeMapper({"rea": {
        "PASSED_IN": "PASSED_IN",
        "PASSED_IN_VENDOR_BID": "PASSED_IN_VENDOR_BID",
        "SOLD_AT_AUCTION": "SOLD",
    }})


def test_to_record_bid_derived_price():
    r = to_record(_row(max_bid_display="Last bid $690,000"), _mapper())
    assert r.highest_bid == 690000
    assert (r.price_low, r.price_high, r.price_status) == (690000, 690000, "BID_DERIVED")


def test_to_record_vendor_bid():
    r = to_record(_row(outcome_raw="PASSED_IN_VENDOR_BID",
                       max_bid_display="Last bid $730,000"), _mapper())
    assert r.vendor_bid == 730000
    assert r.highest_bid is None


def test_to_record_sold_price():
    r = to_record(_row(outcome_raw="SOLD_AT_AUCTION", price_display="$786,000"),
                  _mapper())
    assert r.sold_price == 786000
    assert r.price_status == "UNKNOWN"  # sale price is not a lead price signal


# --- dedupe -------------------------------------------------------------------

def test_dedupe_exact_merges_sources():
    a = to_record(_row(address_raw="40 Allan St"), _mapper())
    a.sources = ["rea"]; a.source_urls = {"rea": "https://rea/x"}
    b = to_record(_row(address_raw="40 Allan Street"), _mapper())
    b.sources = ["domain"]; b.source_urls = {"domain": "https://domain/y"}
    b.price_low, b.price_high, b.price_status = 900000, 950000, "QUOTED"
    merged, exact, fuzzy = dedupe([a, b])
    assert (len(merged), exact, fuzzy) == (1, 1, 0)
    assert merged[0].sources == ["domain", "rea"]
    assert merged[0].price_status == "QUOTED"  # better signal preferred
    assert merged[0].merge_confidence == "HIGH"


def test_dedupe_fuzzy_is_flagged_not_assumed():
    a = to_record(_row(address_raw="12 Smith Street"), _mapper())
    b = to_record(_row(address_raw="12 Smyth Street"), _mapper())
    merged, exact, fuzzy = dedupe([a, b])
    assert (len(merged), exact, fuzzy) == (1, 0, 1)
    assert merged[0].merge_confidence == "LOW"


def test_dedupe_different_units_never_merge():
    a = to_record(_row(address_raw="1/12 Smith Street"), _mapper())
    b = to_record(_row(address_raw="2/12 Smith Street"), _mapper())
    merged, exact, fuzzy = dedupe([a, b])
    assert (len(merged), exact, fuzzy) == (2, 0, 0)


# --- filters ------------------------------------------------------------------

FILTERS = {
    "price_ceiling": 1200000, "stretch_ceiling": 1300000, "min_bedrooms": 2,
    "exclude_property_types": ["Apartment"], "min_land_size_sqm": 300,
    "exclude_agencies": [], "exclude_streets": ["Bell St"],
}


def _item(**kw):
    base = dict(price_low=1000000, bedrooms=3, property_type="House",
                land_size_sqm=None, agency_name="X", street="smith street",
                address_raw="12 Smith St", outcome="PASSED_IN", weeks_unsold=1)
    base.update(kw)
    return base


def test_classify_buckets():
    assert classify(_item(), FILTERS) == "in_budget"
    assert classify(_item(price_low=1250000), FILTERS) == "stretch"
    assert classify(_item(price_low=1500000), FILTERS) is None
    assert classify(_item(price_low=None), FILTERS) == "no_price"


def test_classify_null_land_size_included():
    # null land size means unknown -> include; known-small -> exclude
    assert classify(_item(land_size_sqm=None), FILTERS) == "in_budget"
    assert classify(_item(land_size_sqm=200), FILTERS) is None


def test_classify_exclusions():
    assert classify(_item(property_type="Apartment"), FILTERS) is None
    assert classify(_item(bedrooms=1), FILTERS) is None
    assert classify(_item(bedrooms=None), FILTERS) == "in_budget"  # unknown included
    assert classify(_item(address_raw="200 Bell St"), FILTERS) is None


def test_classify_property_type_include_list():
    f = dict(FILTERS, property_types=["House"])
    assert classify(_item(property_type="House"), f) == "in_budget"
    assert classify(_item(property_type="Townhouse"), f) is None
    assert classify(_item(property_type=None), f) == "in_budget"  # unknown included


def test_classify_suburb_list():
    f = dict(FILTERS, suburbs_mode="list", suburbs=["Windsor", "Elwood"])
    assert classify(_item(suburb="Windsor"), f) == "in_budget"
    assert classify(_item(suburb="Brunswick"), f) is None


# --- ranking ------------------------------------------------------------------

def test_rank_order():
    cfg = {"weights": {"per_week_unsold": 100,
                       "outcome": {"NO_BID": 60, "PASSED_IN": 40},
                       "has_price": 25, "has_bid_figure": 10}}
    three_weeks = _item(weeks_unsold=3, price_low=1100000)
    no_bid = _item(outcome="NO_BID")
    passed_in = _item()
    unpriced = _item(price_low=None)
    cheap = _item(price_low=800000)
    items = [unpriced, passed_in, no_bid, three_weeks, cheap]
    ranked = sort_items(items, cfg)
    assert ranked[0] is three_weeks          # weeks unsold dominates
    assert ranked[1] is no_bid               # no-bid above passed-in
    assert ranked[2] is cheap                # price ascending within score
    assert ranked[-1] is unpriced            # unpriced ranks below priced
