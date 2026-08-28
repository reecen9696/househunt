"""Pluggable fetch layer (§4.1).

Everything downstream talks to `Fetcher.fetch(url) -> str (html)`. Concrete
implementations (undetected Chrome, plain requests, or the existing
propertypath scraper's session layer) are injected at run start; if one
breaks or gets rate-limited another can be swapped in without touching
parsing, filtering or output.

CachingFetcher wraps any implementation and writes every fetched page to
disk by run date + URL hash, so re-parsing never requires re-fetching.
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
from datetime import date
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class QuotaExceededError(FetchError):
    """The paid fetch layer is out of credits — no point continuing to fetch."""


class Fetcher(Protocol):
    def fetch(self, url: str) -> str: ...
    def close(self) -> None: ...


class Throttle:
    """Sequential politeness delay, randomised in [min_s, max_s]."""

    def __init__(self, min_s: float, max_s: float):
        self.min_s = min_s
        self.max_s = max_s
        self._last = 0.0

    def wait(self) -> None:
        delay = random.uniform(self.min_s, self.max_s)
        elapsed = time.monotonic() - self._last
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last = time.monotonic()


def url_cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


class CachingFetcher:
    """Disk-caching wrapper. Cache layout: <cache_dir>/<YYYY-MM-DD>/<hash>.html

    A same-day re-run reads from cache and generates no traffic, which also
    makes parser development safe.
    """

    def __init__(self, inner: Fetcher, cache_dir: Path, throttle: Throttle,
                 run_date: date | None = None, refetch: bool = False):
        self.inner = inner
        self.run_date = run_date or date.today()
        self.dir = cache_dir / self.run_date.isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.throttle = throttle
        self.refetch = refetch
        self.fetch_count = 0
        self.cache_hits = 0

    def _path(self, url: str) -> Path:
        return self.dir / f"{url_cache_key(url)}.html"

    def fetch(self, url: str) -> str:
        path = self._path(url)
        if path.exists() and not self.refetch:
            self.cache_hits += 1
            logger.debug("cache hit: %s", url)
            return path.read_text(encoding="utf-8")
        self.throttle.wait()
        html = self.inner.fetch(url)
        self.fetch_count += 1
        path.write_text(html, encoding="utf-8")
        # Keep an index of hash -> url alongside, for debugging cached runs.
        index = self.dir / "index.txt"
        with open(index, "a", encoding="utf-8") as f:
            f.write(f"{url_cache_key(url)}\t{url}\n")
        return html

    def close(self) -> None:
        self.inner.close()


def build_fetcher(config, refetch: bool = False) -> CachingFetcher:
    """Construct the configured fetcher wrapped in caching + throttling."""
    kind = config.get("fetch.fetcher", "chrome")
    if kind == "chrome":
        from .chrome_fetcher import ChromeFetcher
        inner = ChromeFetcher(
            headless=bool(config.get("fetch.chrome.headless", True)),
            version_main=config.get("fetch.chrome.version_main"),
            timeout=int(config.get("fetch.timeout_seconds", 45)),
            driver_path=config.get("fetch.chrome.driver_path"),
            driver_cache_dir=config.data_dir / "chromedriver",
        )
    elif kind == "scrapedo":
        from .scrapedo_fetcher import ScrapeDoFetcher
        inner = ScrapeDoFetcher(
            timeout=int(config.get("fetch.timeout_seconds", 90)),
            params=config.get("fetch.scrapedo.params") or {},
        )
    elif kind == "requests":
        from .requests_fetcher import RequestsFetcher
        inner = RequestsFetcher(timeout=int(config.get("fetch.timeout_seconds", 45)))
    else:
        raise FetchError(f"Unknown fetcher {kind!r} in config (fetch.fetcher)")

    throttle = Throttle(
        float(config.get("fetch.min_delay_seconds", 2.5)),
        float(config.get("fetch.max_delay_seconds", 6.0)),
    )
    return CachingFetcher(inner, config.cache_dir, throttle, refetch=refetch)
