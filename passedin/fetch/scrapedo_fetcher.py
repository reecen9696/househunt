"""scrape.do fetcher — the strategy the propertypath-backend actually used
for Kasada-protected REA-group pages (see its address_search_orchestrator:
`super=true&extraHeaders=true` with SCRAPE_DO_TOKEN).

Each request is proxied through scrape.do's unblocker. Costs credits per
request (super mode is priced higher) — the disk cache in front of this
means a week's pages are only ever paid for once.

Token comes from SCRAPE_DO_TOKEN in secrets.env, never from config.
"""
from __future__ import annotations

import logging
import os
import random
import time

from . import FetchError, QuotaExceededError

logger = logging.getLogger(__name__)

_API = "https://api.scrape.do"


class ScrapeDoFetcher:
    def __init__(self, timeout: int = 90, params: dict | None = None,
                 max_attempts: int = 3):
        import requests
        token = os.getenv("SCRAPE_DO_TOKEN")
        if not token:
            raise FetchError(
                "SCRAPE_DO_TOKEN is not set. Add it to secrets.env "
                "(see secrets.env.sample) or choose another fetch.fetcher."
            )
        self.token = token
        self.session = requests.Session()
        self.timeout = timeout
        # super=true routes through residential/mobile proxies and solves the
        # bot challenge; extraHeaders mirrors the backend's working call.
        self.params = {"super": "true", "extraHeaders": "true"}
        self.params.update(params or {})
        self.max_attempts = max(1, int(max_attempts))

    # Proxy-rotation hiccups (502 ROTATION_FAILED / "session not found") are
    # transient and clear on a retry; quota and auth failures never do.
    _TRANSIENT = (429, 500, 502, 503, 504)

    def fetch(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._fetch_once(url, attempt)
            except QuotaExceededError:
                raise
            except FetchError as e:
                if e.status not in self._TRANSIENT or attempt == self.max_attempts:
                    raise
                last = e
                delay = min(8.0, 2.0 * attempt) + random.uniform(0, 1.0)
                logger.warning("scrape.do transient %s on attempt %d/%d for %s — "
                               "retrying in %.1fs", e.status, attempt,
                               self.max_attempts, url, delay)
                time.sleep(delay)
        raise last  # unreachable, but keeps the contract explicit

    def _fetch_once(self, url: str, attempt: int) -> str:
        logger.info("scrape.do fetch (attempt %d): %s", attempt, url)
        resp = self.session.get(
            _API,
            params={"token": self.token, "url": url, **self.params},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            body = resp.text[:200]
            if resp.status_code in (401, 402, 429) and "limit" in body.lower():
                raise QuotaExceededError(
                    f"scrape.do request limit exceeded ({body})", status=resp.status_code)
            raise FetchError(f"scrape.do returned HTTP {resp.status_code} for {url}: "
                             f"{body}", status=resp.status_code)
        text = resp.text
        if len(text) < 1000:
            raise FetchError(f"scrape.do returned a suspiciously small body "
                             f"({len(text)} bytes) for {url}")
        return text

    def close(self) -> None:
        self.session.close()
