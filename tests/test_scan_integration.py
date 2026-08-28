"""End-to-end scan against fixture pages through a fake fetcher: entry ->
suburbs -> store -> view -> outputs, no network.
"""
import json

from conftest import make_entry_page, make_rea_row, make_suburb_page

from passedin.assemble import build_view
from passedin.report.csv_export import export_csv
from passedin.report.html import render_html
from passedin.scan import run_scan
from passedin.store import Store


class FixtureFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.fetch_count = 0
        self.cache_hits = 0

    def fetch(self, url):
        self.fetch_count += 1
        try:
            return self.pages[url]
        except KeyError:
            from passedin.fetch import FetchError
            raise FetchError(f"404 {url}")

    def close(self):
        pass


def _pages():
    entry = make_entry_page([("Brunswick", "3056", 3), ("Coburg", "3058", 1),
                             ("Elsewhere", "3999", 9)])
    brunswick = make_suburb_page([
        make_rea_row(address="5 Ballarat Court", outcome="PASSED_IN",
                     url="/property-house-vic-brunswick-101"),
        make_rea_row(address="40 Allan Street", outcome="SOLD_AUCTION_UNKNOWN",
                     price="$786,000", url="/sold/property-house-vic-brunswick-102"),
        make_rea_row(address="8 Vendor Lane", outcome="PASSED_IN_VENDOR_BID",
                     max_bid="Last bid $690,000",
                     url="/property-house-vic-brunswick-103"),
    ], suburb="Brunswick", postcode="3056")
    coburg = make_suburb_page([
        make_rea_row(address="77 Mystery Road", outcome="BRAND_NEW_LABEL",
                     url="/property-house-vic-coburg-104"),
    ], suburb="Coburg", postcode="3058")
    listing = json.dumps({"marketingPriceRange": "$1,000,000 - $1,100,000"})
    return {
        "https://www.realestate.com.au/auction-results/vic": entry,
        "https://www.realestate.com.au/auction-results/brunswick-vic-3056": brunswick,
        "https://www.realestate.com.au/auction-results/coburg-vic-3058": coburg,
        "https://www.realestate.com.au/property-house-vic-brunswick-101": listing,
    }


def test_full_scan(tmp_path, config, monkeypatch):
    config.raw["filters"]["suburbs"] = ["Brunswick", "Coburg"]
    config.raw["canary"]["min_entry_suburbs"] = 2
    fetcher = FixtureFetcher(_pages())
    monkeypatch.setattr("passedin.scan.build_fetcher", lambda cfg, refetch=False: fetcher)

    stats = run_scan(config)

    assert stats["week_ending"] == "2026-08-09"
    assert stats["sources"]["rea"]["rows_parsed"] == 4
    assert stats["non_sales_found"] == 3  # 2 pass-ins + 1 UNKNOWN
    # unknown label surfaced loudly, not dropped
    assert stats["unrecognised_outcomes"] == {"rea:BRAND_NEW_LABEL": 1}
    assert any("Unrecognised" in p or "BRAND_NEW_LABEL" in p
               for p in stats["canary_problems"])
    # enrichment priced the pass-in from its listing page
    assert stats.get("enrich_priced") == 1

    store = Store(config.db_path)
    view = build_view(store, config, "2026-08-09")
    sec = view["sections"]
    addrs = {i["address_raw"] for i in sec["new_this_week"]}
    assert "5 Ballarat Court" in addrs        # QUOTED $1.0m–$1.1m, under ceiling
    assert "8 Vendor Lane" in addrs           # BID_DERIVED $690k
    assert [i["address_raw"] for i in sec["no_price"]] == ["77 Mystery Road"]
    # ranking: vendor-bid outcome + cheaper beats plain passed-in
    assert sec["new_this_week"][0]["address_raw"] == "8 Vendor Lane"

    view["problems"] = ["Fetch quota exhausted after 3/26 suburbs — TRUNCATED."]
    html_path = render_html(view, "summary", "now", tmp_path / "r.html")
    html = html_path.read_text()
    assert "5 Ballarat Court" in html and "UNKNOWN" in html
    # problems are embedded for the banner renderer, and the quota banner
    # copy ships with the page
    assert "Fetch quota exhausted" in html
    assert "Out of scrape.do credits" in html
    n = export_csv(view, tmp_path / "e.csv")
    assert n == 3
    store.close()


def test_scan_idempotent_same_day(tmp_path, config, monkeypatch):
    config.raw["filters"]["suburbs"] = ["Brunswick", "Coburg"]
    config.raw["canary"]["min_entry_suburbs"] = 2
    monkeypatch.setattr("passedin.scan.build_fetcher",
                        lambda cfg, refetch=False: FixtureFetcher(_pages()))
    run_scan(config)
    run_scan(config)  # second run same day must not duplicate
    store = Store(config.db_path)
    assert len(store.snapshots_for_week("2026-08-09")) == 4
    store.close()


def test_one_suburb_failing_does_not_fail_source(tmp_path, config, monkeypatch):
    pages = _pages()
    del pages["https://www.realestate.com.au/auction-results/coburg-vic-3058"]
    config.raw["filters"]["suburbs"] = ["Brunswick", "Coburg"]
    config.raw["canary"]["min_entry_suburbs"] = 2
    config.raw["canary"]["min_suburb_parse_rate"] = 0.9
    monkeypatch.setattr("passedin.scan.build_fetcher",
                        lambda cfg, refetch=False: FixtureFetcher(pages))
    stats = run_scan(config)
    assert stats["sources"]["rea"]["rows_parsed"] == 3  # Brunswick still landed
    assert any("parsed" in p for p in stats["canary_problems"])  # and it was loud
