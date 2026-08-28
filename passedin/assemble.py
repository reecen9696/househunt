"""Assemble the review view-model from the store (§9 change tracking, §10).

Sections read differently and are kept distinct:
  new_this_week   — first appearance as a lead
  still_available — seen before, still unsold (weeks_unsold is the headline)
  stretch         — between price_ceiling and stretch_ceiling (if configured)
  no_price        — UNKNOWN price, ranked below priced results, never dropped
  recently_sold   — previously-tracked leads that have now sold (market read;
                    retires them from the active list)
  disappeared     — previously-open leads absent from this week's results,
                    retained for two weeks before archiving
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from .filters import classify, wanted_suburbs
from .normalise import normalise_suburb
from .rank import sort_items
from .store import Store

logger = logging.getLogger(__name__)

SOLD_OUTCOMES = {"SOLD", "SOLD_PRIOR", "SOLD_AFTER"}


def _results_url(suburb: str | None, postcode: str | None) -> str | None:
    """Link to the REA auction-results page a row was scraped from, so any
    outcome in the report can be eyeballed against the source."""
    if not suburb:
        return None
    slug = suburb.strip().lower().replace(" ", "-")
    if postcode:
        slug = f"{slug}-vic-{postcode}"
    return f"https://www.realestate.com.au/auction-results/{slug}"


def _row_to_item(s, store: Store, lead_outcomes: set[str], week: str) -> dict:
    item = {k: s[k] for k in s.keys()}
    item["suburb"] = s["p_suburb"]
    item["postcode"] = s["p_postcode"]
    item["results_url"] = _results_url(s["p_suburb"], s["p_postcode"])
    item["source_urls"] = json.loads(s["source_urls"] or "{}")
    item["sources"] = json.loads(s["sources"] or "[]")
    item["weeks_unsold"] = store.weeks_unsold(s["property_id"], lead_outcomes, week)
    item["is_new"] = s["first_seen_week"] == week
    item["dismissed"] = bool(s["dismissed"])
    item["history"] = [
        {k: h[k] for k in h.keys()} for h in store.history(s["property_id"])
    ]

    prev = store.previous_lead_snapshot(s["property_id"], week, lead_outcomes)
    item["price_changed"] = False
    if prev and prev["price_low"] is not None and item.get("price_low") is not None \
            and prev["price_low"] != item["price_low"]:
        item["price_changed"] = True
        item["prev_price_low"] = prev["price_low"]
        item["prev_price_high"] = prev["price_high"]
    return item


SECTION_NAMES = ("new_this_week", "still_available", "stretch",
                 "no_price", "recently_sold", "disappeared")


def _filters_summary(config) -> dict:
    filters_cfg = config.get("filters") or {}
    return {
        "price_ceiling": filters_cfg.get("price_ceiling"),
        "stretch_ceiling": filters_cfg.get("stretch_ceiling"),
        "min_bedrooms": filters_cfg.get("min_bedrooms"),
        "suburbs_mode": filters_cfg.get("suburbs_mode"),
        "suburb_count": len(filters_cfg.get("suburbs") or []),
    }


def empty_view(config) -> dict:
    """The view for a store with no scan in it yet.

    A hosted deployment starts with an empty volume, and the page is the only
    way to press "Run weekly scan" — so it has to render before there is any
    data, rather than the CLI bailing out the way it does locally.
    """
    return {
        "week_ending": None,
        "sections": {name: [] for name in SECTION_NAMES},
        "excluded_by_filters": 0,
        "filters": _filters_summary(config),
    }


def build_view(store: Store, config, week: str) -> dict:
    lead_outcomes = set(config.get("lead_outcomes") or [])
    filters_cfg = config.get("filters") or {}
    ranking_cfg = config.get("ranking") or {}

    snapshots = store.snapshots_for_week(week)
    sections = {name: [] for name in SECTION_NAMES}
    excluded_by_filters = 0

    for s in snapshots:
        item = _row_to_item(s, store, lead_outcomes, week)

        if item["outcome"] in SOLD_OUTCOMES:
            # Market read + retirement of prior leads: only interesting if we
            # were tracking it as a lead before this week.
            prev = store.previous_lead_snapshot(s["property_id"], week, lead_outcomes)
            if prev is not None:
                item["was_lead_weeks"] = item["weeks_unsold"]
                sections["recently_sold"].append(item)
            continue

        if item["outcome"] not in lead_outcomes:
            continue  # POSTPONED etc. — logged at parse time, not a current lead

        bucket = classify(item, filters_cfg)
        if bucket is None:
            excluded_by_filters += 1
            continue
        if bucket == "no_price":
            sections["no_price"].append(item)
        elif bucket == "stretch":
            sections["stretch"].append(item)
        elif item["is_new"]:
            sections["new_this_week"].append(item)
        else:
            sections["still_available"].append(item)

    # DISAPPEARED: open leads with no snapshot this week, retained 2 weeks.
    week_date = date.fromisoformat(week)
    wanted = wanted_suburbs(filters_cfg)
    for row in store.open_leads_absent_this_week(week, lead_outcomes):
        if row["last_week"] >= week:
            continue
        if wanted is not None and normalise_suburb(row["suburb"] or "") not in wanted:
            continue  # tracked under a previous suburb config — not shown
        last = date.fromisoformat(row["last_week"])
        if (week_date - last).days > 14:
            continue  # archived
        item = {k: row[k] for k in row.keys()}
        item["outcome"] = row["last_outcome"]
        item["weeks_unsold"] = store.weeks_unsold(row["property_id"], lead_outcomes,
                                                  row["last_week"])
        item["source_urls"] = json.loads(row["source_urls"] or "{}")
        item["results_url"] = _results_url(row["suburb"], row["postcode"])
        item["dismissed"] = bool(row["dismissed"])
        item["disappeared_since"] = row["last_week"]
        sections["disappeared"].append(item)

    for name in ("new_this_week", "still_available", "stretch", "no_price"):
        sections[name] = sort_items(sections[name], ranking_cfg)

    return {
        "week_ending": week,
        "sections": sections,
        "excluded_by_filters": excluded_by_filters,
        "filters": _filters_summary(config),
    }
