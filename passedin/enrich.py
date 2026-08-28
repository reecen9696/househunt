"""Price resolution (§6) — the hard part.

Non-sales carry no sale price, so the budget filter needs a price derived in
this order of preference:
  1. QUOTED    — advertised range scraped from the listing page
  2. BID_DERIVED — highest/vendor bid published in the results row
  3. RELISTED  — post-auction asking price (captured on later runs when the
                 listing page shows a fixed price after relist)
  4. SOI       — statement of information (not implemented; placeholder)
  5. UNKNOWN   — surfaced, never dropped

Dead listing links are expected (listings vanish within hours of a failed
auction) and fall through gracefully to the next signal. A price is never
fabricated: everything here is scraped text run through the money parser.
"""
from __future__ import annotations

import logging
import re

from .fetch import FetchError, QuotaExceededError
from .model import PropertyRecord
from .moneyparse import parse_money_range

logger = logging.getLogger(__name__)


def extract_price_text(html: str, patterns: list[str]) -> str | None:
    """First price-looking capture from config-ordered regexes."""
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = m.group(1).strip()
            if "$" in text:
                return text
    return None


def enrich_records(records: list[PropertyRecord], fetcher, enrich_cfg: dict,
                   stats: dict) -> None:
    """Fetch listing pages for leads and upgrade their price signal in place.

    `records` should arrive best-leads-first: the page budget
    (max_pages_per_run) is spent on the strongest candidates.
    """
    if not enrich_cfg.get("enabled", True):
        return
    patterns = enrich_cfg.get("price_patterns") or []
    budget = int(enrich_cfg.get("max_pages_per_run", 40))
    fetched = 0

    for r in records:
        if fetched >= budget:
            logger.info("Enrichment page budget (%d) exhausted", budget)
            break
        if r.price_status == "QUOTED":
            continue
        url = r.source_urls.get("rea") or r.source_urls.get("domain")
        if not url:
            stats["enrich_no_url"] = stats.get("enrich_no_url", 0) + 1
            continue
        try:
            html = fetcher.fetch(url)
        except QuotaExceededError as e:
            # Out of paid requests — nothing further can succeed this run.
            logger.error("ENRICHMENT ABORTED: %s", e)
            stats["enrich_aborted"] = str(e)
            break
        except FetchError as e:
            if e.status in (404, 410):
                # Dead link — expected post-auction. Keep the signal we had.
                logger.info("Listing gone (%s): %s", e, url)
                stats["enrich_dead_links"] = stats.get("enrich_dead_links", 0) + 1
            else:
                logger.warning("Enrichment fetch failed (%s): %s", e, url)
                stats["enrich_errors"] = stats.get("enrich_errors", 0) + 1
            continue
        except Exception:
            logger.exception("Enrichment fetch failed for %s", url)
            stats["enrich_errors"] = stats.get("enrich_errors", 0) + 1
            continue
        fetched += 1

        text = extract_price_text(html, patterns)
        low, high = parse_money_range(text)
        if low is None:
            stats["enrich_no_price"] = stats.get("enrich_no_price", 0) + 1
            continue

        # A fixed asking price on a /sold/-free page after the auction is a
        # relist; a range is the campaign quote. Either way it outranks a bid.
        status = "RELISTED" if (low == high and "/sold/" not in url) else "QUOTED"
        r.price_low, r.price_high = low, high
        r.price_status = status
        r.price_source_url = url
        stats["enrich_priced"] = stats.get("enrich_priced", 0) + 1
        logger.info("Priced %s: %s–%s (%s)", r.display_address(), low, high, status)
