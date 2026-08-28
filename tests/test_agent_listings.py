"""The agent profile page as a campaign-dating source.

A REA listing page publishes no date at all, but the agent's profile stamps
every property they have on market with "Listed 28 Jul 2026". The payload
below mirrors the real structure captured 2026-08-28 from
/agent/dion-besser-328641: an ArgonautExchange bucket whose
AGENT_PROFILE_LISTINGS holds the full roster under `agentMapBuyListings`
and only the first page under `buyListings`.
"""
import json
from datetime import date

from passedin.agent_listings import (
    agent_profile_urls,
    estimate_from_roster,
    listing_id_from_url,
    parse_agent_listings,
    status_date,
)
from passedin.dating import resolve

TODAY = date(2026, 8, 28)

TARGET = "https://www.realestate.com.au/property-house-vic-caulfield+south-151882992"


def _listing(listing_id, status, short="335 Bambra Road", suburb="Caulfield South"):
    return {
        "id": listing_id,
        "price": "$1,000,000 - $1,100,000",
        "address": {"shortAddress": short, "suburb": suburb,
                    "state": "Vic", "postcode": "3162"},
        "propertyType": "House",
        "_links": {"canonical":
                   f"https://www.realestate.com.au/property-house-vic-"
                   f"{suburb.lower().replace(' ', '+')}-{listing_id}"},
        "listingStatus": status,
    }


def _agent_page(buy=None, sold=None, buy_first_page=None, encoded=False):
    groups = {
        "agentMapBuyListings": {"listings": buy or []},
        "agentMapSoldListings": {"listings": sold or []},
        "buyListings": {"listings": buy_first_page or []},
    }
    bucket = {"AGENT_PROFILE_LISTINGS": groups}
    # REA double-encodes some exchange buckets as JSON strings; the parser
    # has to survive either shape.
    payload = {"resi-agent_customer-profile-experience":
               json.dumps(bucket) if encoded else bucket}
    return ("<html><script>window.ArgonautExchange = "
            + json.dumps(payload) + ";</script></html>")


# --- reading the roster ------------------------------------------------------

def test_parses_full_roster_not_just_the_visible_page():
    """The map roster holds every listing; `buyListings` holds only the three
    shown before "see more". Missing this is the difference between dating
    one property and dating twenty."""
    html = _agent_page(
        buy=[_listing("151882992", "Listed 28 Jul 2026"),
             _listing("152036524", "Listed 13 Aug 2026", "211/449 Hawthorn Road"),
             _listing("149823400", "Listed 05 Dec 2025", "43B Leopold Street")],
        buy_first_page=[_listing("151882992", "Listed 28 Jul 2026")])
    roster = parse_agent_listings(html)
    assert set(roster) == {"151882992", "152036524", "149823400"}


def test_parses_double_encoded_bucket():
    html = _agent_page(buy=[_listing("151882992", "Listed 28 Jul 2026")],
                       encoded=True)
    assert "151882992" in parse_agent_listings(html)


def test_missing_payload_yields_no_candidates_rather_than_raising():
    """A dating source that can't read a page must degrade to "no date" —
    never take down a run that would otherwise have succeeded."""
    assert parse_agent_listings("<html>bot challenge</html>") == {}
    assert parse_agent_listings("") == {}
    assert parse_agent_listings(
        "<script>window.ArgonautExchange = {\"other-key\": {}};</script>") == {}


# --- turning a status line into a date ---------------------------------------

def test_listed_status_becomes_a_date():
    assert status_date("Listed 28 Jul 2026", TODAY) == date(2026, 7, 28)


def test_sold_status_is_not_a_listing_date():
    """A sold row is a previous campaign. Reading its date as a listing date
    would invent history for the property currently on market."""
    assert status_date("Sold 15 Aug 2026", TODAY) is None


def test_implausible_status_dates_are_rejected():
    assert status_date("Listed 28 Jul 2035", TODAY) is None   # future
    assert status_date("Listed 03 Feb 1998", TODAY) is None   # pre-portal
    assert status_date("Under offer", TODAY) is None
    assert status_date(None, TODAY) is None


# --- listing identity --------------------------------------------------------

def test_listing_id_from_url():
    assert listing_id_from_url(TARGET) == "151882992"
    assert listing_id_from_url(TARGET + "?cid=abc") == "151882992"
    assert listing_id_from_url("https://www.realestate.com.au/agent/x-1") is None


def test_agent_profile_urls_are_deduped_and_ordered():
    """REA lists the lead agent first and repeats each link (escaped in the
    JSON payload and again in the DOM); the lead must stay first."""
    html = (r'{"listers":[{"name":"Dion","_links":{"canonical":'
            r'"https://www.realestate.com.au/agent/'
            r'dion-besser-328641?cid={cid}"}},'
            r'{"name":"Charles","_links":{"canonical":'
            r'"https://www.realestate.com.au/agent/charles-callis-3645908"}}]}'
            r'<a href="https://www.realestate.com.au/agent/dion-besser-328641">x</a>')
    assert agent_profile_urls(html) == [
        "https://www.realestate.com.au/agent/dion-besser-328641",
        "https://www.realestate.com.au/agent/charles-callis-3645908",
    ]


# --- the estimate it produces ------------------------------------------------

def test_estimate_for_a_listing_in_the_roster():
    roster = parse_agent_listings(
        _agent_page(buy=[_listing("151882992", "Listed 28 Jul 2026")]))
    est = estimate_from_roster(TARGET, roster, TODAY)
    assert est.day == date(2026, 7, 28)
    assert est.basis == "agent-listed"
    # A relist only ever moves this date later, so it is a lower bound on how
    # long the property has been advertised — safe to quote at an agent.
    assert est.documented


def test_no_estimate_when_the_agent_does_not_hold_the_listing():
    roster = parse_agent_listings(
        _agent_page(buy=[_listing("152036524", "Listed 13 Aug 2026")]))
    assert estimate_from_roster(TARGET, roster, TODAY) is None


def test_sold_roster_entry_yields_no_estimate():
    """The property could appear under the agent's sold listings from an
    earlier campaign; that must not date the current one."""
    roster = parse_agent_listings(
        _agent_page(buy=[_listing("151882992", "Sold 15 Aug 2026")]))
    assert estimate_from_roster(TARGET, roster, TODAY) is None


# --- how it lands in the resolver -------------------------------------------

def test_earliest_candidate_still_wins_over_the_agent_date():
    """An SOI predating the agent's date means the listing was relisted; the
    earliest defensible date is the point of the whole exercise."""
    from passedin.dating import DateEstimate
    dating = resolve([
        DateEstimate(day=date(2026, 7, 28), basis="agent-listed"),
        DateEstimate(day=date(2026, 3, 2), basis="soi-document"),
    ], TODAY)
    assert dating.start.basis == "soi-document"


def test_agent_date_supplies_the_portal_claim_so_resets_surface_on_rea():
    """REA publishes no listed date on the listing itself, so before this
    source there was nothing to measure a reset against. The gap between
    REA's own claim and the documentary evidence is the hidden time."""
    from passedin.dating import DateEstimate
    dating = resolve([
        DateEstimate(day=date(2026, 7, 28), basis="agent-listed"),
        DateEstimate(day=date(2026, 3, 2), basis="soi-document"),
    ], TODAY)
    assert dating.current_listing is not None
    assert dating.days_claimed(TODAY) == 31
    assert dating.days_on_market(TODAY) == 179
    assert dating.hidden_days(TODAY) == 148
    assert dating.clock_reset(threshold_days=21, today=TODAY)


def test_explicit_portal_date_is_preferred_over_the_roster_for_the_claim():
    from passedin.dating import DateEstimate
    dating = resolve([
        DateEstimate(day=date(2026, 7, 28), basis="agent-listed"),
        DateEstimate(day=date(2026, 8, 1), basis="current-listing"),
    ], TODAY)
    assert dating.current_listing.basis == "current-listing"


def test_agent_date_alone_does_not_read_as_resting_on_the_resettable_figure():
    """`rests_only_on_current_listing` keeps fresh listings out of the
    long-on-market view. An agent-listed date is documented evidence, so a
    property whose only date is an old one must still rank as stale."""
    from passedin.dating import DateEstimate
    dating = resolve([DateEstimate(day=date(2023, 6, 23), basis="agent-listed")],
                     TODAY)
    assert not dating.rests_only_on_current_listing
    assert dating.is_documented
