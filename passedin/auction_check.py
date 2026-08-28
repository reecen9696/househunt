"""Has this property already been to auction and failed to sell?

No portal states this. REA records no pass-in history, and property.com.au's
timeline covers sold / rent / leased / withdrawn only — a pass-in is none of
those. So it has to be established indirectly. See
docs/two-date-auction-detection.md.

The method: a long campaign narrows the auction, if there was one, to one or
two specific Saturdays (Melbourne campaigns run three to five weeks and
finish on a Saturday). That turns "search six months of results" into
"check one page". Those Saturdays are then verified against actual result
records, which is the only step that proves anything.

Two record sources, cheapest first:
  1. this tool's own weekly scans — free, already in the database
  2. Domain's dated auction-results archive — one fetch per Saturday, cached
     permanently and shared across every property that auctioned that day

Crucially, an address *absent* from the results is not evidence there was no
auction: agents frequently never report a failure. That case stays
"unproven", never "no auction". And a probable pass-in is never collapsed
into a confirmed one — the difference matters when you're on the phone to an
agent, because they know which one is true.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Outcomes that mean "did not sell under the hammer" — the leads.
NON_SALE = {"PASSED_IN", "PASSED_IN_VENDOR_BID", "NO_BID", "WITHDRAWN",
            "POSTPONED", "UNREPORTED", "UNKNOWN"}
SOLD = {"SOLD", "SOLD_PRIOR", "SOLD_AFTER"}

# Which non-sale actually answers "has it failed at auction?". A property
# can appear several times — postponed one week, then passed in a fortnight
# later — and a postponement on its own is a scheduling change, not a
# failure, so it must never be reported as a pass-in.
_RESULT_PRIORITY = {
    "PASSED_IN": 5, "PASSED_IN_VENDOR_BID": 5, "NO_BID": 5,
    "WITHDRAWN": 4, "UNREPORTED": 3, "UNKNOWN": 3, "POSTPONED": 1,
}


def _rank(row) -> tuple:
    """Best result first: a real failure outranks a postponement, and among
    equals the most recent auction wins."""
    return (_RESULT_PRIORITY.get(row["outcome"], 0), row["week_day"] or "")

# Output states, strongest first.
CONFIRMED_PASS_IN = "CONFIRMED_PASS_IN"
CONFIRMED_SOLD = "CONFIRMED_SOLD"
PROBABLE_PASS_IN = "PROBABLE_PASS_IN"
POSSIBLE_PASS_IN = "POSSIBLE_PASS_IN"
STALE_NO_AUCTION = "STALE_NO_AUCTION"
NORMAL = "NORMAL"


@dataclass
class AuctionAssessment:
    state: str = NORMAL
    auction_day: Optional[date] = None      # when it went to auction
    result_raw: Optional[str] = None        # source's own label
    outcome: Optional[str] = None           # canonical
    source: Optional[str] = None            # "local-scan" | "domain-archive"
    agency: Optional[str] = None
    candidates: list = field(default_factory=list)   # Saturdays worth checking
    checked: list = field(default_factory=list)      # Saturdays actually checked
    reasons: list = field(default_factory=list)
    stale_ratio: Optional[float] = None     # days on market ÷ area benchmark

    @property
    def confirmed(self) -> bool:
        return self.state in (CONFIRMED_PASS_IN, CONFIRMED_SOLD)

    @property
    def worth_a_call(self) -> bool:
        return self.state in (CONFIRMED_PASS_IN, PROBABLE_PASS_IN,
                              POSSIBLE_PASS_IN)

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "auction_day": self.auction_day.isoformat() if self.auction_day else None,
            "result_raw": self.result_raw,
            "outcome": self.outcome,
            "source": self.source,
            "agency": self.agency,
            "stale_ratio": self.stale_ratio,
            "candidates": [d.isoformat() for d in self.candidates],
            "checked": [d.isoformat() for d in self.checked],
            "reasons": self.reasons,
            "confirmed": self.confirmed,
            "worth_a_call": self.worth_a_call,
        }


def candidate_saturdays(campaign_start: date, weeks=(3, 4, 5),
                        today: Optional[date] = None) -> list[date]:
    """The Saturdays an auction would have fallen on, given a campaign start.

    Melbourne campaigns run three to five weeks of advertising and finish on
    a Saturday; four weeks is the base case. Future dates are dropped — an
    auction that hasn't happened yet can't have been passed in.
    """
    today = today or date.today()
    out: list[date] = []
    for w in weeks:
        candidate = campaign_start + timedelta(days=w * 7)
        # Advance to the next Saturday on or after the candidate (Sat == 5).
        saturday = candidate + timedelta(days=(5 - candidate.weekday()) % 7)
        if saturday <= today and saturday not in out:
            out.append(saturday)
    return sorted(out)


_SINGLE_FIGURE = re.compile(r"^\s*\$[\d,]+(?:\.\d+)?\s*$")


def price_is_single_figure(price_text: Optional[str]) -> bool:
    """A relist after a pass-in typically shows one asking figure.

    Auction campaigns advertise no price or a range, so a lone figure on a
    long-running campaign corroborates a failed auction. Corroboration only —
    plenty of honest private sales quote a single number.
    """
    if not price_text:
        return False
    cleaned = re.sub(r"(?i)\b(private sale|for sale|buyers?|offers?|from|guide)\b",
                     "", price_text).strip(" :–-")
    if re.search(r"(?i)auction|range|contact|eoi|expression", price_text):
        return False
    return bool(_SINGLE_FIGURE.match(cleaned))


def assess(*, address_norm: str, postcode: Optional[str],
           campaign_start: Optional[date], days_on_market: Optional[int],
           price_text: Optional[str], store, config,
           fetcher=None, today: Optional[date] = None,
           allow_network: bool = True) -> AuctionAssessment:
    """Work out whether this property has already failed at auction."""
    today = today or date.today()
    cfg = config.get("auction_check") or {}
    result = AuctionAssessment()

    # --- 1. Records we already hold ---------------------------------------
    for row in store.find_local_results(address_norm, postcode):
        if row["outcome"] in NON_SALE:
            result.state = CONFIRMED_PASS_IN
            result.outcome = row["outcome"]
            result.result_raw = row["outcome_raw"]
            result.auction_day = _saturday_of(row["week_ending"])
            result.source = "local-scan"
            result.agency = row["agency_name"]
            result.reasons.append(
                f"Found in this tool's own auction-results scan for the week "
                f"ending {row['week_ending']}.")
            return result
        if row["outcome"] in SOLD:
            result.state = CONFIRMED_SOLD
            result.outcome = row["outcome"]
            result.auction_day = _saturday_of(row["week_ending"])
            result.source = "local-scan"
            result.reasons.append(
                f"Sold at auction per this tool's scan, week ending "
                f"{row['week_ending']}.")

    # --- 2. Any Domain week already cached ---------------------------------
    # Weeks are shared across every property, so searching all of them is
    # free and catches auctions the campaign-start arithmetic would miss —
    # notably when the SOI was re-issued on relist, which hides the original
    # campaign behind the reset.
    cached_hits = store.find_domain_results(address_norm, postcode)
    if cached_hits:
        history = ", ".join(f"{h['week_day']} {h['result_raw']}"
                            for h in sorted(cached_hits, key=lambda h: h["week_day"]))
        best = max(cached_hits, key=_rank)
        found = _from_domain_row(best)
        found.reasons.append(
            f"Domain's auction results record: {history}.")
        if best["outcome"] in NON_SALE and best["outcome"] != "POSTPONED":
            if len(cached_hits) > 1:
                found.reasons.append(
                    "More than one auction event — a campaign that has been "
                    "rescheduled or run twice is a soft one.")
            return found
        if best["outcome"] in SOLD:
            found.reasons.append(
                "Sold — either the sale fell through, or this is a different "
                "property. Check the address before discarding.")
            return found
        # Postponed only: an auction was scheduled but we have no result for
        # it, so keep looking rather than calling it a failure.
        result.reasons.append(
            f"Auction postponed ({best['week_day']}); no result recorded since.")

    # --- 3. Narrow to candidate Saturdays ---------------------------------
    if campaign_start is None:
        result.reasons.append("No campaign start date, so no auction window "
                              "can be derived.")
        return result
    weeks = tuple(cfg.get("campaign_weeks") or (3, 4, 5))
    result.candidates = candidate_saturdays(campaign_start, weeks, today)
    if not result.candidates:
        result.reasons.append("Campaign is too recent for an auction to have "
                              "happened yet.")
        return result

    # --- 4. Fetch any candidate week not cached yet -----------------------
    if allow_network and cfg.get("use_domain_archive", True):
        budget = int(cfg.get("max_weeks_per_check", 3) or 3)
        for saturday in result.candidates:
            if budget <= 0:
                break
            if store.domain_week_cached(saturday.isoformat()):
                result.checked.append(saturday)
                continue
            if not _ensure_week(saturday, store, config, fetcher):
                continue
            budget -= 1
            result.checked.append(saturday)
            hit = next((r for r in store.find_domain_results(address_norm, postcode)
                        if r["week_day"] == saturday.isoformat()), None)
            if hit is None:
                continue
            found = _from_domain_row(hit)
            found.candidates = result.candidates
            found.checked = result.checked
            found.reasons.append(
                f"Domain's auction results for {saturday.isoformat()} list this "
                f"address as {hit['result_raw']}.")
            if found.outcome in NON_SALE:
                return found
            result.state = CONFIRMED_SOLD
            result.auction_day = found.auction_day
            result.reasons.append(
                f"Domain's results for {saturday.isoformat()} show a sale — "
                f"check the address before discarding.")
            return result

    # --- 5. Nothing found: infer, and say so ------------------------------
    if result.state == CONFIRMED_SOLD:
        return result

    # Staleness is relative to how long things normally take to sell here,
    # not an absolute day count — the ratio is the number worth quoting,
    # because it compares against the market's own benchmark.
    median = float(cfg.get("suburb_median_days", 30) or 30)
    ratio_threshold = float(cfg.get("stale_ratio", 1.5) or 1.5)
    stale_ratio = (days_on_market / median) if days_on_market and median else None
    is_stale = stale_ratio is not None and stale_ratio > ratio_threshold
    if stale_ratio is not None:
        result.stale_ratio = round(stale_ratio, 2)
    single_figure = price_is_single_figure(price_text)

    absent_note = ("Not listed in the auction results for the candidate "
                   "Saturdays. That is not proof there was no auction — "
                   "agents frequently never report a failure.")
    stale_note = (f"Advertised {days_on_market} days, {stale_ratio:.1f}× the "
                  f"{median:.0f}-day benchmark for the area."
                  if stale_ratio is not None else "")
    if is_stale and single_figure:
        result.state = PROBABLE_PASS_IN
        result.reasons += [
            stale_note + " Now quoting a single asking figure, which is the "
            "usual shape of a relist after a failed auction.",
            absent_note,
        ]
    elif is_stale:
        result.state = POSSIBLE_PASS_IN if result.candidates else STALE_NO_AUCTION
        result.reasons += [stale_note, absent_note]
    elif single_figure:
        result.state = POSSIBLE_PASS_IN
        result.reasons += ["Quoting a single asking figure.", absent_note]
    else:
        result.reasons.append(absent_note)
    return result


def _from_domain_row(hit) -> AuctionAssessment:
    day = None
    try:
        day = date.fromisoformat(hit["week_day"])
    except (ValueError, TypeError):
        pass
    return AuctionAssessment(
        state=CONFIRMED_PASS_IN if hit["outcome"] in NON_SALE else CONFIRMED_SOLD,
        auction_day=day, result_raw=hit["result_raw"], outcome=hit["outcome"],
        source="domain-archive", agency=hit["agency"])


def backfill_domain_weeks(store, config, fetcher, weeks: int = 12,
                          today: Optional[date] = None) -> dict:
    """Cache the last N Saturdays of Domain auction results.

    One fetch per Saturday, shared by every property ever checked — so this
    is the efficient way to buy coverage: after a backfill, matching any
    address against months of results costs nothing and needs no guess about
    when its campaign started.
    """
    today = today or date.today()
    last_saturday = today - timedelta(days=(today.weekday() - 5) % 7)
    summary = {"fetched": [], "cached": [], "failed": []}
    for i in range(weeks):
        saturday = last_saturday - timedelta(days=7 * i)
        key = saturday.isoformat()
        if store.domain_week_cached(key):
            summary["cached"].append(key)
            continue
        if _ensure_week(saturday, store, config, fetcher):
            summary["fetched"].append(key)
        else:
            summary["failed"].append(key)
    return summary


def _saturday_of(week_ending: Optional[str]) -> Optional[date]:
    """Results weeks are stamped with their end date; auctions run Saturday."""
    if not week_ending:
        return None
    try:
        day = date.fromisoformat(week_ending)
    except ValueError:
        return None
    return day - timedelta(days=(day.weekday() - 5) % 7)


def _ensure_week(saturday: date, store, config, fetcher) -> bool:
    """Fetch and cache one Saturday of Domain results. Returns True if the
    week is available locally afterwards."""
    key = saturday.isoformat()
    if store.domain_week_cached(key):
        return True
    if fetcher is None:
        return False
    from .outcomes import OutcomeMapper
    from .normalise import normalise_address
    from .sources.domain_results import parse_week, week_url

    city = (config.get("auction_check.domain_city") or "melbourne")
    url = week_url(saturday, city)
    try:
        html = fetcher.fetch(url)
        auction_day, rows = parse_week(html)
    except Exception as e:
        logger.warning("Domain results fetch failed for %s: %s", key, e)
        return False
    if auction_day and auction_day != saturday:
        logger.info("Domain returned %s for requested %s", auction_day, saturday)

    mapper = OutcomeMapper(config.get("outcome_mapping") or {})
    store.save_domain_week(key, [{
        "address_norm": normalise_address(r.address).norm,
        "suburb": r.suburb,
        "postcode": r.postcode,
        "result_raw": r.result_raw,
        "outcome": mapper.map("domain", r.result_raw),
        "property_type": r.property_type,
        "bedrooms": r.bedrooms,
        "agency": r.agency,
        "price_text": r.price_text,
        "url": r.url,
    } for r in rows])
    logger.info("Cached %d Domain results for %s", len(rows), key)
    return True
