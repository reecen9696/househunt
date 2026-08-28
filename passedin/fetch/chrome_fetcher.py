"""Undetected-Chrome fetcher — the implementation that gets past REA's bot
protection. Adapted from the existing propertypath-backend session layer
(app/utils/webdriver.py); one driver is kept alive for the whole run so
cookies and the Kasada session persist across page loads.
"""
from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from . import FetchError

logger = logging.getLogger(__name__)

_CFT_BASE = "https://storage.googleapis.com/chrome-for-testing-public"
_CFT_BUILDS = ("https://googlechromelabs.github.io/chrome-for-testing/"
               "latest-patch-versions-per-build-with-downloads.json")
_MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _installed_chrome_version() -> str | None:
    if sys.platform != "darwin" or not Path(_MAC_CHROME).exists():
        return None
    try:
        out = subprocess.run([_MAC_CHROME, "--version"], capture_output=True,
                             text=True, timeout=15).stdout
        return out.strip().split()[-1]  # "Google Chrome 151.0.7922.109"
    except Exception:
        return None


def _resolve_mac_arm_driver(cache_dir: Path) -> Path | None:
    """undetected-chromedriver hardcodes mac-x64 and downloads a binary that
    cannot run on Apple Silicon. Fetch the matching mac-arm64 chromedriver
    from the Chrome-for-Testing archive instead and hand it to UC directly.
    """
    if not (sys.platform == "darwin" and platform.machine() == "arm64"):
        return None
    version = _installed_chrome_version()
    if not version:
        return None

    target = cache_dir / f"chromedriver-{version}-mac-arm64"
    if target.exists():
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)

    urls = [f"{_CFT_BASE}/{version}/mac-arm64/chromedriver-mac-arm64.zip"]
    try:  # fall back to the latest patch of the same build if exact 404s
        with urllib.request.urlopen(_CFT_BUILDS, timeout=30) as resp:
            builds = json.load(resp)["builds"]
        build = ".".join(version.split(".")[:3])
        for dl in builds.get(build, {}).get("downloads", {}).get("chromedriver", []):
            if dl["platform"] == "mac-arm64":
                urls.append(dl["url"])
    except Exception as e:
        logger.debug("Chrome-for-Testing build index unavailable: %s", e)

    for url in urls:
        try:
            logger.info("Downloading arm64 chromedriver: %s", url)
            zip_path = cache_dir / "chromedriver.zip"
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path) as z:
                member = next(n for n in z.namelist() if n.endswith("/chromedriver"))
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            target.chmod(0o755)
            zip_path.unlink(missing_ok=True)
            _prepatch_and_sign(target)
            return target
        except Exception as e:
            logger.warning("chromedriver download failed (%s): %s", url, e)
    return None


def _prepatch_and_sign(driver: Path) -> None:
    """Apply UC's anti-detection patch now, then re-sign ad hoc.

    UC patches the binary in place at startup, which invalidates its code
    signature — and Apple Silicon macOS SIGKILLs binaries with broken
    signatures. Patching first and re-signing leaves UC nothing to modify
    (it skips already-patched binaries), so the signature stays valid.
    """
    try:
        from undetected_chromedriver.patcher import Patcher
        patcher = Patcher(executable_path=str(driver))
        if not patcher.is_binary_patched(str(driver)):
            patcher.patch_exe()
        subprocess.run(["codesign", "--force", "--sign", "-", str(driver)],
                       check=True, capture_output=True)
    except Exception as e:
        logger.warning("pre-patch/sign of chromedriver failed (%s); "
                       "continuing — UC may still make it work", e)


class ChromeFetcher:
    def __init__(self, headless: bool = True, version_main: int | None = None,
                 timeout: int = 45, driver_path: str | None = None,
                 driver_cache_dir: Path | str | None = None):
        self.headless = headless
        self.version_main = version_main
        self.timeout = timeout
        self.driver_path = driver_path
        self.driver_cache_dir = Path(driver_cache_dir or
                                     Path.home() / ".cache" / "passedin")
        self._driver = None
        self._temp_profile = None
        self._session_ready = False

    def _ensure_driver(self):
        if self._driver is not None:
            return self._driver
        try:
            import undetected_chromedriver as uc
        except ImportError as e:
            raise FetchError(
                "undetected-chromedriver is not installed. "
                "pip install undetected-chromedriver, or set fetch.fetcher: requests"
            ) from e

        self._temp_profile = tempfile.mkdtemp(prefix="passedin_udc_")
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-data-dir={self._temp_profile}")
        # No user-agent spoofing: a mismatched UA on a real Chrome build is a
        # fingerprint inconsistency that bot protection catches.
        if self.headless:
            options.add_argument("--headless=new")

        kwargs = {"options": options, "use_subprocess": True}
        if self.version_main:
            kwargs["version_main"] = int(self.version_main)
        driver_path = self.driver_path or _resolve_mac_arm_driver(self.driver_cache_dir)
        if driver_path:
            logger.info("Using chromedriver at %s", driver_path)
            kwargs["driver_executable_path"] = str(driver_path)
        try:
            self._driver = uc.Chrome(**kwargs)
        except Exception:
            shutil.rmtree(self._temp_profile, ignore_errors=True)
            raise
        self._driver.set_page_load_timeout(self.timeout)
        self._driver.set_script_timeout(self.timeout)
        return self._driver

    # Bot-challenge interstitials are tiny; real pages on these sites are
    # hundreds of KB. Below this size we assume the page hasn't settled yet.
    _MIN_REAL_PAGE_BYTES = 20_000

    # How long to keep waiting for the bootstrap page while an attended user
    # lets the bot check finish in the visible window.
    _BOOTSTRAP_TIMEOUT = 240

    _FETCH_JS = """
        const url = arguments[0], done = arguments[arguments.length - 1];
        fetch(url, {credentials: 'include'})
            .then(r => r.text().then(t => done({status: r.status, text: t})))
            .catch(e => done({status: 0, text: String(e)}));
    """

    def fetch(self, url: str) -> str:
        driver = self._ensure_driver()
        just_bootstrapped = False
        if not self._session_ready:
            self._bootstrap(driver, url)
            just_bootstrapped = True

        # Kasada blocks automated *navigations*, but requests made from inside
        # an established page session ride its cookies and headers untouched —
        # so with a session bootstrapped, pages are pulled via in-page fetch(),
        # which also returns pristine raw HTML (not the hydrated DOM).
        logger.info("in-session fetch: %s", url)
        result = driver.execute_async_script(self._FETCH_JS, url)
        status, text = result.get("status"), result.get("text", "")
        if status == 200 and len(text) >= 1000:
            return text
        if status in (404, 410):
            raise FetchError(f"HTTP {status} for {url}")
        if just_bootstrapped:
            # In-session fetch failed right after a good bootstrap: fall back
            # to the DOM we already have rather than looping.
            html = driver.page_source or ""
            if len(html) >= self._MIN_REAL_PAGE_BYTES:
                return html
            raise FetchError(f"Fetch failed post-bootstrap (status {status}) for {url}")
        logger.warning("in-session fetch got status %s (%d bytes) for %s — "
                       "re-bootstrapping session", status, len(text), url)
        self._session_ready = False
        return self.fetch(url)

    def _bootstrap(self, driver, url: str) -> None:
        """First navigation of the session: load the page and wait out the
        bot check. Headed + attended, a human letting the page load (or
        clicking a checkbox if shown) is what makes the session valid."""
        logger.info("chrome bootstrap: %s", url)
        driver.get(url)
        deadline = time.monotonic() + self._BOOTSTRAP_TIMEOUT
        nagged = False
        html = ""
        while time.monotonic() < deadline:
            html = driver.page_source or ""
            if len(html) >= self._MIN_REAL_PAGE_BYTES:
                self._session_ready = True
                return
            if not nagged and time.monotonic() - (deadline - self._BOOTSTRAP_TIMEOUT) > 20:
                nagged = True
                if self.headless:
                    break  # nobody can attend a headless window
                print("\n>>> ACTION MAY BE NEEDED: a Chrome window is open on "
                      "the auction-results page. If it shows a check or a "
                      "blank page, give it a moment or click through — the scan "
                      "continues automatically once the page loads. <<<\n",
                      flush=True)
            time.sleep(2.0)
        raise FetchError(
            f"Bootstrap page never settled ({len(html)} bytes) for {url} — "
            "bot challenge unresolved. Run with fetch.chrome.headless: false "
            "and let the page load in the visible window."
        )

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as e:
                logger.debug("error quitting driver: %s", e)
            self._driver = None
        if self._temp_profile:
            shutil.rmtree(self._temp_profile, ignore_errors=True)
            self._temp_profile = None
