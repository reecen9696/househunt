"""Store idempotency, weeks-unsold tracking, change tracking and canary."""
import json

from passedin import canary
from passedin.assemble import build_view
from passedin.model import PropertyRecord, make_property_id
from passedin.normalise import normalise_address, normalise_suburb
from passedin.store import Store

LEADS = {"PASSED_IN", "PASSED_IN_VENDOR_BID", "NO_BID", "WITHDRAWN",
         "UNREPORTED", "UNKNOWN"}


def _record(address="12 Smith Street", suburb="Testville", outcome="PASSED_IN",
            week="2026-08-09", **kw):
    addr = normalise_address(address)
    pid = make_property_id(addr.norm, normalise_suburb(suburb), "3999")
    defaults = dict(
        property_id=pid, address_raw=address, address_norm=addr.norm,
        street_number=addr.street_number, street=addr.street, suburb=suburb,
        postcode="3999", outcome=outcome, outcome_raw=outcome,
        sources=["rea"], source_urls={"rea": "https://example/x"},
        week_ending=week, property_type="House", bedrooms=3,
    )
    defaults.update(kw)
    return PropertyRecord(**defaults)


def test_upsert_idempotent(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.upsert_records([_record()])
    store.upsert_records([_record()])  # same day re-run
    rows = store.snapshots_for_week("2026-08-09")
    assert len(rows) == 1


def test_weeks_unsold_and_history(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.upsert_records([_record(week="2026-07-26")])
    store.upsert_records([_record(week="2026-08-02")])
    store.upsert_records([_record(week="2026-08-09")])
    pid = _record().property_id
    assert store.weeks_unsold(pid, LEADS, "2026-08-09") == 3
    assert [h["week_ending"] for h in store.history(pid)] == \
           ["2026-07-26", "2026-08-02", "2026-08-09"]


def test_user_fields_survive_rescan(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.upsert_records([_record(week="2026-08-02")])
    pid = _record().property_id
    store.set_user_fields(pid, dismissed=True, notes="too close to train line")
    store.upsert_records([_record(week="2026-08-09")])  # next week's scan
    row = store.snapshots_for_week("2026-08-09")[0]
    assert row["dismissed"] == 1
    assert row["user_notes"] == "too close to train line"


def _view_config(tmp_path):
    import yaml
    from pathlib import Path
    from passedin.config import Config
    with open(Path(__file__).resolve().parent.parent / "config.yaml") as f:
        raw = yaml.safe_load(f)
    raw["run"]["data_dir"] = str(tmp_path / "data")
    raw["filters"]["suburbs"] = ["Testville"]
    return Config(raw, tmp_path)


def test_sections_new_vs_still_vs_sold(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _view_config(tmp_path)
    # week 1: A and B pass in
    store.upsert_records([
        _record(address="1 Alpha Street", week="2026-08-02",
                price_low=900000, price_high=950000, price_status="QUOTED"),
        _record(address="2 Beta Street", week="2026-08-02",
                price_low=800000, price_high=850000, price_status="QUOTED"),
    ])
    # week 2: A still passed in, B sold after auction, C is new
    store.upsert_records([
        _record(address="1 Alpha Street", week="2026-08-09",
                price_low=900000, price_high=950000, price_status="QUOTED"),
        _record(address="2 Beta Street", week="2026-08-09",
                outcome="SOLD_AFTER", sold_price=880000),
        _record(address="3 Gamma Street", week="2026-08-09",
                price_low=700000, price_high=750000, price_status="QUOTED"),
    ])
    view = build_view(store, config, "2026-08-09")
    sec = view["sections"]
    assert [i["address_raw"] for i in sec["new_this_week"]] == ["3 Gamma Street"]
    assert [i["address_raw"] for i in sec["still_available"]] == ["1 Alpha Street"]
    assert [i["address_raw"] for i in sec["recently_sold"]] == ["2 Beta Street"]
    assert sec["still_available"][0]["weeks_unsold"] == 2


def test_disappeared_retained_two_weeks(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _view_config(tmp_path)
    store.upsert_records([_record(address="9 Ghost Street", week="2026-08-02",
                                  price_low=900000, price_status="QUOTED")])
    store.upsert_records([_record(address="8 Here Street", week="2026-08-09",
                                  price_low=900000, price_status="QUOTED")])
    view = build_view(store, config, "2026-08-09")
    ghosts = [i["address_raw"] for i in view["sections"]["disappeared"]]
    assert ghosts == ["9 Ghost Street"]
    # three weeks later it is archived
    store.upsert_records([_record(address="8 Here Street", week="2026-08-30",
                                  price_low=900000, price_status="QUOTED")])
    view = build_view(store, config, "2026-08-30")
    assert view["sections"]["disappeared"] == []


def test_price_changed_flag(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _view_config(tmp_path)
    store.upsert_records([_record(week="2026-08-02", price_low=950000,
                                  price_high=1000000, price_status="QUOTED")])
    store.upsert_records([_record(week="2026-08-09", price_low=899000,
                                  price_high=899000, price_status="RELISTED")])
    view = build_view(store, config, "2026-08-09")
    item = view["sections"]["still_available"][0]
    assert item["price_changed"] is True
    assert item["prev_price_low"] == 950000


def test_unknown_price_never_dropped(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    config = _view_config(tmp_path)
    store.upsert_records([_record(week="2026-08-09")])  # no price signal at all
    view = build_view(store, config, "2026-08-09")
    assert len(view["sections"]["no_price"]) == 1


# --- canary -------------------------------------------------------------------

def test_canary_entry_and_volume():
    cfg = {"min_entry_suburbs": 100, "min_weekly_ratio": 0.3}
    assert canary.check_entry(5, cfg)
    assert not canary.check_entry(500, cfg)
    history = [("2026-08-09", 4), ("2026-08-02", 60), ("2026-07-26", 55)]
    assert canary.check_weekly_volume(4, history, cfg)
    assert not canary.check_weekly_volume(50, history, cfg)


def test_canary_parse_rate():
    cfg = {"min_suburb_parse_rate": 0.8}
    assert canary.check_suburb_parse_rate(10, 5, cfg)
    assert not canary.check_suburb_parse_rate(10, 9, cfg)
    assert canary.check_suburb_parse_rate(0, 0, cfg)
