"""Plain-HTTP fetcher. Fine for cached re-parses, local fixtures and hosts
without bot protection; REA will 403 this — use the chrome fetcher there.
"""
from __future__ import annotations

import logging

from . import FetchError

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}


class RequestsFetcher:
    def __init__(self, timeout: int = 45):
        import requests
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self.timeout = timeout

    def fetch(self, url: str) -> str:
        logger.info("http fetch: %s", url)
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        return resp.text

    def close(self) -> None:
        self.session.close()
