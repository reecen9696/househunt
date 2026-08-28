"""Orchestrates campaign dating: gather candidates, resolve, cache.

Order matters for cost. archive.org needs no page load, so it runs first;
the listing page is only fetched when the archive comes back empty and a
paid fetch is actually worth spending. Everything is cached permanently,
because a campaign's start date does not change.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional  # noqa: F401  (used in signatures below)

from .agent_listings import (
    agent_profile_urls,
    estimate_from_roster,
    listing_id_from_url,
    parse_agent_listings,
)
from .dating import CampaignDating, DateEstimate, date_from_auction, parse_iso, resolve
from .dating_sources import (
    archive_first_capture,
    find_soi_links,
    history_estimate,
    soi_estimates,
    soi_last_modified,
)

logger = logging.getLogger(__name__)


def _to_json(candidates: list[DateEstimate]) -> list[dict]:
    return [{"day": c.day.isoformat(), "basis": c.basis, "detail": c.detail}
            for c in candidates]


def _from_json(raw: Optional[str]) -> list[DateEstimate]:
    if not raw:
        return []
    out = []
    for c in json.loads(raw):
        day = parse_iso(c.get("day"))
        if day:
            out.append(DateEstimate(day=day, basis=c.get("basis", "current-listing"),
                                    detail=c.get("detail")))
    return out


def date_campaign(url: str, store, config, *,
                  auction_date: Optional[date] = None,
                  listed_date: Optional[date] = None,
                  observed_since: Optional[date] = None,
                  fetcher=None, html: Optional[str] = None,
                  today: Optional[date] = None,
                  allow_network: bool = True,
                  agent_cache: Optional[dict] = None) -> CampaignDating:
    """Resolve when this campaign actually started.

    Free candidates (auction inference, the portal's own date, the observed
    floor) are always included. Network sources are consulted once per URL
    and cached forever.
    """
    today = today or date.today()
    campaign_days = int(config.get("dating.auction_campaign_days", 28) or 28)

    free: list[DateEstimate] = []
    inferred = date_from_auction(auction_date, campaign_days)
    if inferred:
        free.append(DateEstimate(
            day=inferred, basis="auction-inferred",
            detail=f"auction {auction_date.isoformat()} − {campaign_days}d campaign"))
    if listed_date:
        free.append(DateEstimate(day=listed_date, basis="current-listing",
                                 detail="date published by the portal"))
    if observed_since:
        free.append(DateEstimate(day=observed_since, basis="observed-floor",
                                 detail="first seen by this tool"))

    cached = store.get_campaign_date(url) if url else None
    if cached is not None:
        network = _from_json(cached["candidates"])
        if network:
            return resolve(free + network, today)
        # An empty cached result means the lookup found nothing — often
        # because the fetch failed transiently. Caching that permanently
        # would make the miss stick forever, so fall through and retry.

    if not url or not allow_network or not config.get("dating.enabled", True):
        return resolve(free, today)

    network = _lookup_network(url, config, fetcher, today, html,
                              agent_cache=agent_cache)
    dating = resolve(free + network, today)
    # Only cache a result that actually found something: a campaign's start
    # date never changes, but "we found nothing" is not a finding.
    if network:
        start = dating.start
        store.save_campaign_date(
            url,
            start.day.isoformat() if start else None,
            start.basis if start else None,
            start.kind if start else None,
            start.detail if start else None,
            _to_json(network))
    return dating


def _lookup_network(url: str, config, fetcher, today: date,
                    html: Optional[str] = None,
                    agent_cache: Optional[dict] = None) -> list[DateEstimate]:
    """Cheapest-first: archive.org, then the listing page only if needed."""
    found: list[DateEstimate] = []

    if config.get("dating.use_archive", True):
        capture = archive_first_capture(
            url, timeout=int(config.get("dating.archive_timeout_seconds", 30)),
            today=today)
        if capture:
            logger.info("archive.org dates %s to %s", url, capture.day)
            found.append(capture)

    # An archive capture is documented evidence and usually enough, so only
    # look at the page when we have it already or can afford to fetch it.
    if found and html is None:
        return found
    if html is None:
        if fetcher is None or not config.get("dating.use_listing_page", True):
            return found
        try:
            html = fetcher.fetch(url)
        except Exception as e:
            logger.info("Dating fetch failed for %s: %s", url, e)
            return found

    agent = _agent_estimate(url, html, config, fetcher, today, agent_cache)
    if agent:
        found.append(agent)

    found.extend(soi_estimates(html, url, today))
    history = history_estimate(html, today)
    if history:
        found.append(history)

    # REA rehosts the SOI under a content-hash filename, so when nothing in
    # the URL carries a date, ask the CDN when the document was published.
    if config.get("dating.use_soi_headers", True) and \
            not any(f.basis == "soi-document" for f in found):
        timeout = int(config.get("dating.archive_timeout_seconds", 30))
        for link in find_soi_links(html, url)[:2]:
            estimate = soi_last_modified(link, timeout=timeout, today=today)
            if estimate:
                logger.info("SOI dates %s to %s", url, estimate.day)
                found.append(estimate)
                break
    return found


def _agent_estimate(url: str, html: str, config, fetcher, today: date,
                    agent_cache: Optional[dict]) -> Optional[DateEstimate]:
    """The listed date REA publishes on the agent's profile, not the listing.

    One agent page carries that agent's whole for-sale roster, so the fetch is
    shared: `agent_cache` keeps parsed rosters for the life of a run, which
    turns ~20 per-property lookups into one page. A roster that doesn't
    contain the listing is still cached — knowing an agent is a dead end is
    worth as much as knowing they aren't.
    """
    if not config.get("dating.use_agent_page", True):
        return None
    if fetcher is None or not listing_id_from_url(url):
        return None

    cache = agent_cache if agent_cache is not None else {}
    max_agents = int(config.get("dating.max_agent_pages_per_listing", 2) or 2)

    for agent_url in agent_profile_urls(html)[:max_agents]:
        roster = cache.get(agent_url)
        if roster is None:
            try:
                roster = parse_agent_listings(fetcher.fetch(agent_url))
            except Exception as e:
                logger.info("Agent page fetch failed for %s: %s", agent_url, e)
                cache[agent_url] = {}
                continue
            cache[agent_url] = roster
            logger.info("Agent roster %s: %d for-sale listings", agent_url,
                        len(roster))
        estimate = estimate_from_roster(url, roster, today)
        if estimate:
            logger.info("Agent profile dates %s to %s", url, estimate.day)
            return estimate
    return None
