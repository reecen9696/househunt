"""Where campaign-start candidates come from.

Dating runs cheapest-first: archive.org is a plain API call with no page
load and nothing for a bot detector to see, so it is tried before the
listing page is opened at all. Results are cached permanently by the
caller — a campaign's start date never changes — so the cost is one lookup
per address ever, not one per week.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Optional

from .dating import (
    DateEstimate,
    date_from_median_period,
    date_from_url,
    plausible,
)

logger = logging.getLogger(__name__)

_CDX = "https://web.archive.org/cdx/search/cdx"


def archive_first_capture(url: str, timeout: int = 20,
                          today: Optional[date] = None) -> Optional[DateEstimate]:
    """First archive.org capture of the listing URL.

    Third-party evidence that the listing existed by that date. Coverage of
    individual listing URLs is patchy, but when it hits it is a recorded
    observation rather than arithmetic.
    """
    query = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp",
        "limit": "1",
        "filter": "statuscode:200",
    })
    try:
        with urllib.request.urlopen(f"{_CDX}?{query}", timeout=timeout) as resp:
            rows = json.load(resp)
    except Exception as e:
        logger.info("archive.org lookup failed for %s: %s", url, e)
        return None
    # First row is the header ["timestamp"]; captures follow.
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    try:
        day = datetime.strptime(str(rows[1][0])[:8], "%Y%m%d").date()
    except (ValueError, IndexError, TypeError):
        return None
    if not plausible(day, today):
        return None
    return DateEstimate(day=day, basis="archive-capture",
                        detail=f"web.archive.org capture {day.isoformat()}")


# --- Statement of Information ------------------------------------------------
# Every Victorian residential listing must publish one, and agencies host the
# PDF on their own CDN with the upload time in the filename.

_SOI_HINTS = ("statement-of-information", "statementofinformation",
              "statement_of_information", "statement", "soi", "s.o.i")

_LINK = re.compile(r'href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']', re.IGNORECASE)
_ANY_PDF_IN_JSON = re.compile(r'"(https?://[^"]+?\.pdf[^"]*)"', re.IGNORECASE)


def find_soi_links(html: str, base_url: str = "") -> list[str]:
    """PDF links from a listing page, SOI-looking ones first.

    NOTE: the selector logic here is the least-verified part of dating — it
    guesses at markup that has not been confirmed on a live Domain or REA
    listing page. It looks for any PDF, then prefers whichever names itself
    a Statement of Information, so a miss degrades to "no SOI candidate"
    rather than a wrong date.
    """
    found: list[str] = []
    for match in list(_LINK.finditer(html)) + list(_ANY_PDF_IN_JSON.finditer(html)):
        link = match.group(1).replace("\\/", "/")
        if link.startswith("//"):
            link = "https:" + link
        elif link.startswith("/") and base_url:
            parsed = urllib.parse.urlparse(base_url)
            link = f"{parsed.scheme}://{parsed.netloc}{link}"
        if link not in found:
            found.append(link)

    def looks_like_soi(u: str) -> bool:
        low = u.lower()
        return any(h in low for h in _SOI_HINTS)

    return sorted(found, key=lambda u: not looks_like_soi(u))


def soi_estimates(html: str, base_url: str = "",
                  today: Optional[date] = None) -> list[DateEstimate]:
    """Candidates derived from the Statement of Information."""
    out: list[DateEstimate] = []
    for link in find_soi_links(html, base_url):
        day = date_from_url(link, today)
        if day:
            out.append(DateEstimate(day=day, basis="soi-document", detail=link))
            break   # first (SOI-preferred) dated document is enough

    # Weaker, independent signal: the median period quoted inside the SOI is
    # visible in the page text on many listings even when the PDF isn't read.
    period_end = date_from_median_period(html, today)
    if period_end:
        out.append(DateEstimate(day=period_end, basis="soi-median-period",
                                detail="SOI median period end"))
    return out


def soi_last_modified(pdf_url: str, timeout: int = 20,
                      today: Optional[date] = None) -> Optional[DateEstimate]:
    """Publication time of an SOI PDF, from the CDN's Last-Modified header.

    Needed because REA rehosts the agency's Statement of Information under a
    content-hash filename, which discards the agency's own timestamped name.
    A HEAD request costs nothing (no body) and the header is a real upload
    time.

    Read it as "advertised by this date", not "advertised from": the SOI is
    re-issued whenever the indicative price changes, so a late upload can be
    a mid-campaign re-issue. That is safe here because the earliest candidate
    wins — a re-issue simply loses to a better source.
    """
    req = urllib.request.Request(pdf_url, method="HEAD")
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            header = resp.headers.get("Last-Modified")
    except Exception as e:
        logger.info("SOI HEAD failed for %s: %s", pdf_url, e)
        return None
    if not header:
        return None
    try:
        day = parsedate_to_datetime(header).date()
    except (TypeError, ValueError):
        return None
    if not plausible(day, today):
        return None
    return DateEstimate(day=day, basis="soi-document",
                        detail=f"Statement of Information published {day.isoformat()}")


# --- property history --------------------------------------------------------

_HISTORY_PATTERNS = (
    r'"firstListedDate"\s*:\s*"([\d-]{10})',
    r'"campaignStartDate"\s*:\s*"([\d-]{10})',
    r'[Ff]irst listed[^<\d]{0,40}(\d{1,2}\s+\w{3,9}\s+\d{4})',
)


def history_estimate(html: str, today: Optional[date] = None
                     ) -> Optional[DateEstimate]:
    """A property-history section that states the first campaign date."""
    for pattern in _HISTORY_PATTERNS:
        m = re.search(pattern, html)
        if not m:
            continue
        raw = m.group(1)
        day = None
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
            try:
                day = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if day and plausible(day, today):
            return DateEstimate(day=day, basis="history-page",
                                detail=f"listing history: {raw}")
    return None
