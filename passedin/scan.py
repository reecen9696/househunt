"""The weekly scan: fetch -> parse -> normalise -> dedupe -> store ->
enrich -> canary -> report. One command, resumable, idempotent.

Resilience rules (§11): one source failing entirely must not fail the run;
one suburb failing to parse must not fail the source. Everything is
counted into the run summary, because a quiet failure looks exactly like a
quiet week.
"""
from __future__ import annotations

import logging
import time
from datetime import date

from . import canary as canary_checks
from .dedupe import dedupe
from .enrich import enrich_records
from .fetch import QuotaExceededError, build_fetcher
from .filters import classify, wanted_suburbs
from .model import PropertyRecord
from .normalise import normalise_suburb
from .outcomes import OutcomeMapper
from .pipeline import to_record
from .rank import sort_items
from .sources import rea
from .store import Store

logger = logging.getLogger(__name__)


def run_scan(config, refetch: bool = False, no_enrich: bool = False) -> dict:
    started = time.monotonic()
    stats: dict = {"sources": {}, "canary_problems": []}
    mapper = OutcomeMapper(config.get("outcome_mapping") or {})
    store = Store(config.db_path)
    fetcher = build_fetcher(config, refetch=refetch)
    lead_outcomes = set(config.get("lead_outcomes") or [])

    week_ending = None
    all_records: list[PropertyRecord] = []
    try:
        if config.get("sources.rea.enabled", False):
            try:
                week_ending, records = _scan_rea(config, fetcher, mapper, store, stats)
                all_records.extend(records)
            except Exception as e:
                # One source failing entirely must not fail the run.
                logger.exception("REA source failed")
                stats["sources"]["rea"] = {"failed": str(e)}
                stats["canary_problems"].append(f"REA source failed entirely: {e}")

        if config.get("sources.domain.enabled", False):
            logger.warning("Domain source is configured but not yet implemented "
                           "(deferred; REA-first build).")

        week_ending = week_ending or date.today().isoformat()

        # ---- enrichment (§6): spend the page budget on the best leads ------
        if not no_enrich and all_records:
            leads = [r for r in all_records if r.outcome in lead_outcomes]
            filters_cfg = config.get("filters") or {}
            candidates = []
            for r in leads:
                item = _record_item(r, store, lead_outcomes, week_ending)
                if classify(item, filters_cfg) is not None:
                    candidates.append((r, item))
            ranked = sort_items([item for _, item in candidates],
                                config.get("ranking") or {})
            order = {id(item): i for i, item in enumerate(ranked)}
            candidates.sort(key=lambda pair: order[id(pair[1])])
            enrich_records([r for r, _ in candidates], fetcher,
                           config.get("enrich") or {}, stats)
            for r, _ in candidates:
                store.update_snapshot_price(r.property_id, r.week_ending or week_ending,
                                            r.price_low, r.price_high,
                                            r.price_status, r.price_source_url)
    finally:
        fetcher.close()

    # ---- canary: weekly volume vs history ----------------------------------
    lead_count = sum(1 for r in all_records if r.outcome in lead_outcomes)
    stats["canary_problems"] += canary_checks.check_weekly_volume(
        lead_count, store.recent_run_counts(), config.get("canary") or {})

    stats["week_ending"] = week_ending
    stats["records_stored"] = len(all_records)
    stats["non_sales_found"] = lead_count
    stats["unrecognised_outcomes"] = dict(mapper.unrecognised)
    if mapper.unrecognised:
        stats["canary_problems"].append(
            f"Unrecognised outcome labels (emitted as UNKNOWN): {mapper.unrecognised}")
    stats["pages_fetched"] = fetcher.fetch_count
    stats["cache_hits"] = fetcher.cache_hits
    stats["elapsed_seconds"] = round(time.monotonic() - started, 1)

    store.record_run(week_ending, stats)
    store.close()
    return stats


def _record_item(r: PropertyRecord, store: Store, lead_outcomes: set[str],
                 week: str) -> dict:
    """Minimal dict view of a fresh record for filtering/ranking pre-report."""
    return {
        "outcome": r.outcome,
        "price_low": r.price_low,
        "highest_bid": r.highest_bid,
        "vendor_bid": r.vendor_bid,
        "bedrooms": r.bedrooms,
        "property_type": r.property_type,
        "land_size_sqm": r.land_size_sqm,
        "agency_name": r.agency_name,
        "street": r.street,
        "address_raw": r.address_raw,
        "weeks_unsold": store.weeks_unsold(r.property_id, lead_outcomes,
                                           r.week_ending or week),
    }


def _scan_rea(config, fetcher, mapper: OutcomeMapper, store: Store,
              stats: dict) -> tuple[str | None, list[PropertyRecord]]:
    cfg = config.get("sources.rea")
    src_stats = stats["sources"].setdefault("rea", {})

    entry_html = fetcher.fetch(cfg["entry_url"])
    suburbs, week_ending = rea.parse_entry(entry_html, cfg)
    src_stats["entry_suburbs"] = len(suburbs)
    stats["canary_problems"] += canary_checks.check_entry(
        len(suburbs), config.get("canary") or {})

    wanted = wanted_suburbs(config.get("filters") or {})
    if wanted is not None:
        targets = [s for s in suburbs if normalise_suburb(s.name) in wanted]
        matched_names = {normalise_suburb(s.name) for s in targets}
        missing = wanted - matched_names
        if missing:
            logger.info("Configured suburbs with no auction results this week: %s",
                        ", ".join(sorted(missing)))
    else:
        targets = suburbs
    src_stats["suburbs_targeted"] = len(targets)
    logger.info("REA: %d suburbs indexed, %d targeted", len(suburbs), len(targets))

    records: list[PropertyRecord] = []
    parsed_ok = attempted = rows_total = 0
    exact_merges = fuzzy_merges = 0
    for i, suburb in enumerate(targets, 1):
        url = cfg["suburb_url_template"].format(slug=suburb.slug)
        attempted += 1
        try:
            html = fetcher.fetch(url)
            rows = rea.parse_suburb(html, cfg)
            parsed_ok += 1
        except QuotaExceededError as e:
            # Every further request will fail the same way — stop fetching,
            # keep what landed, and make the truncation loud.
            logger.error("REA fetch quota exhausted at suburb %d/%d: %s",
                         i, len(targets), e)
            stats["canary_problems"].append(
                f"Fetch quota exhausted after {i - 1}/{len(targets)} suburbs — "
                f"results this week are TRUNCATED. ({e})")
            break
        except Exception:
            # One suburb failing must not fail the source. Log, count, go on.
            logger.exception("REA suburb %s failed (%d/%d)", suburb.name, i, len(targets))
            continue
        rows_total += len(rows)
        recs = [to_record(row, mapper) for row in rows]
        recs, ex, fz = dedupe(recs)
        exact_merges += ex
        fuzzy_merges += fz
        # Persist incrementally: a failure at suburb 40 keeps the first 39.
        store.upsert_records(recs)
        records.extend(recs)
        logger.info("REA %s: %d rows (%d/%d suburbs)", suburb.name, len(rows), i, len(targets))

    src_stats.update({
        "suburb_pages_parsed": parsed_ok,
        "rows_parsed": rows_total,
        "dedupe_exact_merges": exact_merges,
        "dedupe_fuzzy_merges": fuzzy_merges,
    })
    stats["canary_problems"] += canary_checks.check_suburb_parse_rate(
        attempted, parsed_ok, config.get("canary") or {})
    return week_ending, records
