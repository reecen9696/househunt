"""Working out when a campaign *actually* started.

Days-on-market is not a field you read, it's a field you reconstruct.
Both portals compute it from the current listing record only, so withdrawing
a stale listing and relisting it fresh resets the counter to zero — a
deliberate agent tactic, and Australia has no MLS holding a cumulative
figure to fall back on. The result is that on exactly the properties worth
chasing (failed campaign, motivated vendor, still sitting) the published
number understates reality, often by months.

So we gather candidate start dates from several sources and take the
**earliest defensible one**: every source is a lower bound on how long the
property has been advertised, so the earliest date yields the strongest
supportable duration. Trust only breaks ties and sets the label.

Nothing here fabricates a date. Every candidate carries its basis, and the
report distinguishes documented evidence from inference — that distinction
is what tells you whether a figure is safe to quote at an agent.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Basis -> (trust, kind). Trust orders tie-breaks only; the earliest date
# wins regardless. Kind drives the label shown in the report.
BASES: dict[str, tuple[int, str]] = {
    "soi-document": (6, "documented"),      # upload stamp on the Statement of Information
    "domain-listed": (5, "documented"),     # dateListed on the Domain listing record
    "archive-capture": (5, "documented"),   # first archive.org capture of the listing
    "agent-listed": (5, "documented"),      # "Listed 28 Jul 2026" on the agent's profile
    "history-page": (4, "documented"),      # property-history page stated the date
    "auction-inferred": (3, "inferred"),    # auction date minus campaign length
    "soi-median-period": (2, "inferred"),   # SOI median period end (must be <=6mo old)
    "observed-floor": (1, "floor"),         # first week this tool saw it
    "current-listing": (0, "current"),      # what the portal claims; the resettable one
}

# Bases that are a portal stating a date for the listing record it holds right
# now. These are the resettable figures, so they are what a clock reset is
# measured *against* — the gap between one of these and the documentary
# evidence is the hidden time. `agent-listed` is REA's own claim, which is why
# adding it finally makes reset detection work on REA and not just Domain.
#
# It is still counted as documented above: a relist only ever moves the date
# later, so it remains a lower bound on how long the property has been
# advertised, and a lower bound is safe to quote.
PORTAL_CLAIMED = ("current-listing", "agent-listed")

# A campaign start before this is not credible for a live listing, and one in
# the future is nonsense. Both indicate a number that isn't a timestamp —
# long numeric listing IDs are the usual culprit.
_EARLIEST_PLAUSIBLE = date(2015, 1, 1)


@dataclass(frozen=True)
class DateEstimate:
    """One candidate campaign-start date, and where it came from."""

    day: date
    basis: str
    detail: Optional[str] = None

    @property
    def trust(self) -> int:
        return BASES.get(self.basis, (0, "current"))[0]

    @property
    def kind(self) -> str:
        return BASES.get(self.basis, (0, "current"))[1]

    @property
    def documented(self) -> bool:
        return self.kind == "documented"


@dataclass
class CampaignDating:
    """The resolved answer for one property."""

    start: Optional[DateEstimate]           # earliest defensible start
    current_listing: Optional[DateEstimate]  # what the portal claims, if known
    candidates: list[DateEstimate]

    def days_on_market(self, today: Optional[date] = None) -> Optional[int]:
        if self.start is None:
            return None
        return max(0, ((today or date.today()) - self.start.day).days)

    def days_claimed(self, today: Optional[date] = None) -> Optional[int]:
        if self.current_listing is None:
            return None
        return max(0, ((today or date.today()) - self.current_listing.day).days)

    def hidden_days(self, today: Optional[date] = None) -> Optional[int]:
        """How much time the portal's counter is not showing."""
        real, claimed = self.days_on_market(today), self.days_claimed(today)
        if real is None or claimed is None:
            return None
        return max(0, real - claimed)

    def clock_reset(self, threshold_days: int = 21,
                    today: Optional[date] = None) -> bool:
        """True when the portal's counter is materially behind the evidence.

        Not a data-quality problem: someone restarted the counter on a
        property that failed and is still sitting there.
        """
        hidden = self.hidden_days(today)
        return hidden is not None and hidden >= threshold_days

    @property
    def is_documented(self) -> bool:
        return self.start is not None and self.start.documented

    @property
    def rests_only_on_current_listing(self) -> bool:
        """Age known only from the resettable figure — must be kept out of
        any 'long on market' ranking, or it surfaces fresh listings and
        buries genuinely stale ones."""
        return self.start is not None and self.start.basis == "current-listing"


def plausible(day: Optional[date], today: Optional[date] = None) -> bool:
    if day is None:
        return False
    return _EARLIEST_PLAUSIBLE <= day <= (today or date.today())


def resolve(candidates: list[DateEstimate],
            today: Optional[date] = None) -> CampaignDating:
    """Earliest defensible date wins; trust breaks ties."""
    usable = [c for c in candidates if plausible(c.day, today)]
    # The portal's claim, preferring an explicit one over the agent roster
    # when both are known — they are the same number from two places, and a
    # disagreement should read as the listing record's own figure.
    current = next(
        (c for basis in PORTAL_CLAIMED for c in usable if c.basis == basis), None)
    start = min(usable, key=lambda c: (c.day, -c.trust)) if usable else None
    return CampaignDating(start=start, current_listing=current,
                          candidates=sorted(usable, key=lambda c: c.day))


# ---------------------------------------------------------------------------
# Source 1: the Statement of Information
#
# Victoria requires an SOI for every residential sale: included with online
# advertising, displayed at every inspection, and re-issued whenever the
# indicative price changes. Agencies host the PDF on their own CDN and nearly
# all of them stamp the upload time into the filename. An agent can reset a
# portal's counter with two clicks; backdating this needs a new statutory
# document.
# ---------------------------------------------------------------------------

_DIGIT_RUN = re.compile(r"\d+")
_DATE_PATH = re.compile(r"/(20\d{2})[/\-_](\d{1,2})[/\-_](\d{1,2})(?:[/\-_]|$)")


def date_from_url(url: str, today: Optional[date] = None) -> Optional[date]:
    """Pull an upload timestamp out of a document URL.

    Handles unix seconds, unix milliseconds, YYYYMMDDHHMMSS and date paths.
    Implausible results are rejected rather than returned with false
    confidence — a long numeric listing ID looks exactly like a timestamp to
    a loose pattern, and reading one as a date would silently invent history.
    """
    if not url:
        return None
    today = today or date.today()

    m = _DATE_PATH.search(url)
    if m:
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if plausible(day, today):
                return day
        except ValueError:
            pass

    best: Optional[date] = None
    for run in _DIGIT_RUN.findall(url):
        day = _digits_to_date(run, today)
        if day and (best is None or day < best):
            best = day
    return best


def _digits_to_date(run: str, today: date) -> Optional[date]:
    try:
        if len(run) == 14:                      # YYYYMMDDHHMMSS
            day = datetime.strptime(run, "%Y%m%d%H%M%S").date()
        elif len(run) == 13:                    # unix milliseconds
            day = datetime.fromtimestamp(int(run) / 1000, UTC).date()
        elif len(run) == 10:                    # unix seconds
            day = datetime.fromtimestamp(int(run), UTC).date()
        elif len(run) == 8 and run.startswith("20"):   # YYYYMMDD
            day = datetime.strptime(run, "%Y%m%d").date()
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    return day if plausible(day, today) else None


# The SOI quotes a median for a stated period, and that median may be no more
# than six months old when the statement is prepared — so the period's end
# date bounds the campaign even when the PDF itself can't be read.
_MEDIAN_PERIOD = re.compile(
    r"(\d{1,2}\s+\w+\s+\d{4})\s*(?:to|–|-|—)\s*(\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE,
)


def date_from_median_period(text: str, today: Optional[date] = None) -> Optional[date]:
    """Period end from an SOI median statement, e.g. '01 July 2023 to 30 June 2024'."""
    if not text:
        return None
    m = _MEDIAN_PERIOD.search(text)
    if not m:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            day = datetime.strptime(m.group(2).strip(), fmt).date()
        except ValueError:
            continue
        return day if plausible(day, today) else None
    return None


# ---------------------------------------------------------------------------
# Source 2: auction date minus campaign length
#
# A failed auction is a hard, unfakeable date, and it works retrospectively,
# which observation cannot. The campaign length is an assumption, so a longer
# campaign makes this estimate late — it understates rather than overstates.
# ---------------------------------------------------------------------------

def date_from_auction(auction_day: Optional[date], campaign_days: int = 28
                      ) -> Optional[date]:
    if auction_day is None:
        return None
    return auction_day - timedelta(days=campaign_days)


def parse_iso(value) -> Optional[date]:
    """Lenient ISO-ish date parse; returns None rather than raising."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
