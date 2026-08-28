"""Price enrichment: extraction patterns, dead links, budget cap."""
from conftest import CONFIG_PATH

import yaml

from passedin.enrich import enrich_records, extract_price_text
from passedin.fetch import FetchError, QuotaExceededError
from passedin.model import PropertyRecord


def _patterns():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["enrich"]["price_patterns"]


def test_extract_price_text_patterns():
    patterns = _patterns()
    assert extract_price_text(
        '<script>{"marketingPriceRange":"$1,050,000 - $1,150,000"}</script>',
        patterns) == "$1,050,000 - $1,150,000"
    assert extract_price_text(
        '<span class="property-price flex">$950,000</span>', patterns) == "$950,000"
    assert extract_price_text('<p>Contact agent</p>', patterns) is None
    # price-shaped key with non-price text must not match
    assert extract_price_text('{"priceText":"Contact Agent"}', patterns) is None


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        result = self.pages.get(url)
        if result is None:
            raise FetchError("404", status=404)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def _lead(pid, url, status="UNKNOWN"):
    return PropertyRecord(
        property_id=pid, address_raw="12 Smith St", address_norm="12 smith street",
        street_number="12", street="smith street", suburb="Testville",
        postcode="3999", outcome="PASSED_IN", outcome_raw="PASSED_IN",
        source_urls={"rea": url} if url else {}, price_status=status,
        week_ending="2026-08-09",
    )


def test_enrich_quoted_range_and_dead_link():
    live = "https://www.realestate.com.au/property-house-vic-testville-1"
    dead = "https://www.realestate.com.au/property-house-vic-testville-2"
    fetcher = FakeFetcher({live: '{"marketingPriceRange":"$1,000,000 - $1,100,000"}'})
    a, b = _lead("a", live), _lead("b", dead)
    stats = {}
    enrich_records([a, b], fetcher, {"enabled": True, "max_pages_per_run": 10,
                                     "price_patterns": _patterns()}, stats)
    assert (a.price_low, a.price_high, a.price_status) == (1000000, 1100000, "QUOTED")
    assert a.price_source_url == live
    assert b.price_status == "UNKNOWN"  # dead link falls through gracefully
    assert stats["enrich_dead_links"] == 1


def test_enrich_relisted_fixed_price():
    live = "https://www.realestate.com.au/property-house-vic-testville-3"
    fetcher = FakeFetcher({live: '<span class="property-price">$899,000</span>'})
    r = _lead("c", live)
    enrich_records([r], fetcher, {"enabled": True, "max_pages_per_run": 10,
                                  "price_patterns": _patterns()}, {})
    assert (r.price_low, r.price_status) == (899000, "RELISTED")


def test_enrich_quota_exhaustion_aborts_loudly():
    quota = QuotaExceededError("monthly limit exceeded", status=401)
    pages = {"https://x/0": quota, "https://x/1": quota, "https://x/2": quota}
    fetcher = FakeFetcher(pages)
    records = [_lead(str(i), f"https://x/{i}") for i in range(3)]
    stats = {}
    enrich_records(records, fetcher, {"enabled": True, "max_pages_per_run": 10,
                                      "price_patterns": _patterns()}, stats)
    assert len(fetcher.calls) == 1          # stopped after the first 401
    assert "limit" in stats["enrich_aborted"]
    assert not stats.get("enrich_dead_links")  # not misclassified as dead links


def test_enrich_respects_page_budget():
    pages = {f"https://x/{i}": "no price here" for i in range(5)}
    fetcher = FakeFetcher(pages)
    records = [_lead(str(i), f"https://x/{i}") for i in range(5)]
    enrich_records(records, fetcher, {"enabled": True, "max_pages_per_run": 2,
                                      "price_patterns": _patterns()}, {})
    assert len(fetcher.calls) == 2
