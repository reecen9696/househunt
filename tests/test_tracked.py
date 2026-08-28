"""Tracked-properties store: upsert by URL, user fields preserved."""
from passedin.store import Store


def test_upsert_tracked_and_list(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    tid = store.upsert_tracked({
        "url": "https://www.realestate.com.au/property-house-vic-windsor-1?cid=x",
        "address": "12 Peel Street", "suburb": "Windsor",
        "price_text": "$1,100,000 - $1,200,000",
        "price_low": 1100000, "price_high": 1200000,
        "bedrooms": 3, "bathrooms": 2, "car_spaces": 1,
        "date_listed": "2026-07-20",
        "image_url": "https://i2.au.reastatic.net/800x600/a/image.jpg",
        "floorplan_url": "https://i2.au.reastatic.net/1000x750/b/plan.jpg",
    })
    rows = store.list_tracked()
    assert len(rows) == 1
    r = rows[0]
    assert r["tracked_id"] == tid
    assert r["url"].endswith("windsor-1")  # query string stripped
    assert r["status"] == "active"
    assert r["date_listed"] == "2026-07-20"
    assert r["car_spaces"] == 1
    assert r["floorplan_url"].endswith("plan.jpg")


def test_upsert_same_url_updates_not_duplicates(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    url = "https://www.realestate.com.au/property-house-vic-windsor-1"
    tid = store.upsert_tracked({"url": url, "address": "12 Peel Street"})
    store.update_tracked(tid, status="inspected", notes="north-facing yard")
    # re-add from the extension with richer data
    tid2 = store.upsert_tracked({"url": url, "price_text": "$1.15m", "bedrooms": 3})
    assert tid2 == tid
    rows = store.list_tracked()
    assert len(rows) == 1
    r = rows[0]
    assert r["address"] == "12 Peel Street"      # not clobbered by None
    assert r["price_text"] == "$1.15m"           # new fact landed
    assert r["status"] == "inspected"            # user fields preserved
    assert r["user_notes"] == "north-facing yard"


def test_remove_tracked(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    tid = store.upsert_tracked({"url": "https://x.example/1"})
    store.remove_tracked(tid)
    assert store.list_tracked() == []


def test_upsert_requires_url(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    import pytest
    with pytest.raises(ValueError):
        store.upsert_tracked({"address": "12 Peel Street"})
