"""Tiny local review server.

Serves the HTML report, persists the user-owned fields (dismissed / rating
/ notes) to SQLite, and can launch the weekly scan from the page's
"Run weekly scan" button. Local, single-user, stdlib only.

The scan runs as a subprocess of the same interpreter (`-m passedin scan`),
so it regenerates report.html through the normal CLI path; the page polls
/api/scan/status and reloads when it finishes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import date, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .moneyparse import parse_money_range
from .store import Store

logger = logging.getLogger(__name__)


class ScanRunner:
    def __init__(self, base_dir: Path, log_dir: Path,
                 config_path: Path | None = None):
        self.base_dir = base_dir
        self.log_dir = log_dir
        # Passed through to the subprocess. Without it the scan falls back to
        # the config sitting next to the package, which is the wrong file
        # whenever the running config came from elsewhere (a hosted volume).
        self.config_path = config_path
        self.proc: subprocess.Popen | None = None
        self.log_path: Path | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return False
        self.log_path = self.log_dir / f"ui-scan-{datetime.now():%Y%m%d-%H%M%S}.log"
        log_file = open(self.log_path, "w", encoding="utf-8")
        cmd = [sys.executable, "-m", "passedin"]
        if self.config_path is not None:
            cmd += ["--config", str(self.config_path)]
        cmd.append("scan")
        # cwd is the config's own directory, which hosted is the data volume
        # rather than the source tree — so where the package lives has to be
        # stated explicitly or the subprocess cannot import it.
        env = dict(os.environ)
        pkg_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in (pkg_root, env.get("PYTHONPATH")) if p])
        self.proc = subprocess.Popen(
            cmd, cwd=self.base_dir, env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
        )
        logger.info("Scan started (pid %s), log: %s", self.proc.pid, self.log_path)
        return True

    def status(self) -> dict:
        tail = ""
        if self.log_path and self.log_path.exists():
            lines = [l.strip() for l in
                     self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                     if l.strip()]
            if lines:
                tail = lines[-1][:300]
        return {
            "running": self.running,
            "exit_code": self.proc.poll() if self.proc else None,
            "tail": tail,
        }


class Enricher:
    """Runs listing fetch + campaign dating off the request thread.

    A proxied listing fetch can take the better part of a minute, and doing
    it inline made the UI look hung. The row is saved first; this fills in
    the details afterwards and the page polls until they land.
    """

    def __init__(self, db_path: Path, config):
        self.db_path = db_path
        self.config = config
        self.active: dict[str, str] = {}   # url -> "fetching" | error message
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            return {"pending": [u for u, s in self.active.items() if s == "fetching"],
                    "errors": {u: s for u, s in self.active.items() if s != "fetching"}}

    def clear_error(self, url: str) -> None:
        with self._lock:
            self.active.pop(url, None)

    def start(self, payload: dict, needs_fetch: bool) -> bool:
        url = payload["url"]
        with self._lock:
            if self.active.get(url) == "fetching":
                return True
            self.active[url] = "fetching"
        threading.Thread(target=self._run, args=(dict(payload), needs_fetch),
                         daemon=True).start()
        return True

    def _run(self, payload: dict, needs_fetch: bool) -> None:
        url = payload["url"]
        error = None
        html = None
        try:
            if needs_fetch:
                try:
                    # Fetch once and reuse: dating reads the same page for the
                    # Statement of Information, so a second fetch is waste.
                    from .fetch import build_fetcher
                    from .tracker import parse_listing
                    fetcher = build_fetcher(self.config)
                    try:
                        html = fetcher.fetch(url)
                    finally:
                        fetcher.close()
                    fetched = parse_listing(html, url)
                    merged = {**fetched, **{k: v for k, v in payload.items()
                                            if v not in (None, "")}}
                    if merged.get("price_text") and merged.get("price_low") is None:
                        low, high = parse_money_range(merged["price_text"])
                        merged["price_low"], merged["price_high"] = low, high
                    store = Store(self.db_path)
                    try:
                        store.upsert_tracked(merged)
                    finally:
                        store.close()
                    payload = merged
                    logger.info("Enriched %s -> %s", url, merged.get("address"))
                except Exception as e:
                    error = str(e)
                    logger.warning("Listing fetch failed for %s: %s", url, e)

            store = Store(self.db_path)
            try:
                _date_campaign_safely(store, self.config, payload, html)
                _domain_profile_safely(store, self.config, payload)
                _auction_check_safely(store, self.config, payload)
            finally:
                store.close()
        except Exception as e:      # never let a worker die silently
            error = error or str(e)
            logger.exception("Enrichment worker failed for %s", url)
        finally:
            with self._lock:
                if error:
                    self.active[url] = error
                else:
                    self.active.pop(url, None)


def _domain_profile_safely(store, config, payload: dict) -> None:
    """Fill land size and/or a real listing date from Domain, if needed.

    Gated so it only spends fetches where REA actually fell short. Never
    fatal: the property simply keeps whatever REA gave.
    """
    if not config.get("domain_profile.enabled", True):
        return
    address = payload.get("address")
    url = payload.get("url")
    if not address or not url:
        return
    try:
        from .dating_view import dating_for_row
        from .domain_lookup import lookup, needs_lookup
        from .fetch import build_fetcher

        row = next((r for r in store.list_tracked()
                    if r["url"] == url.split("?")[0]), None)
        if row is None:
            return
        dating = dating_for_row(row, store, config)
        if not needs_lookup(land_size=row["land_size_sqm"],
                            campaign_basis=dating.get("campaign_basis"),
                            config=config):
            return

        street = address.split(",")[0]
        suburb = row["suburb"] or (address.split(",")[1].strip()
                                   if "," in address else None)
        fetcher = build_fetcher(config)
        try:
            found = lookup(url, street=street, suburb=suburb, address=address,
                           postcode=row["postcode"], store=store, config=config,
                           fetcher=fetcher)
        finally:
            fetcher.close()

        # Land size belongs on the property itself, not just the cache.
        if found.get("land_size_sqm") and not row["land_size_sqm"]:
            store.upsert_tracked({"url": url,
                                  "land_size_sqm": found["land_size_sqm"]})
        logger.info("Domain profile for %s: land=%s dateListed=%s", address,
                    found.get("land_size_sqm"), found.get("date_listed"))
    except Exception:
        logger.exception("Domain profile lookup failed for %s", url)


def _auction_check_safely(store, config, payload: dict) -> None:
    """Fetch the auction-result weeks this property might appear in.

    Done once at add time so the review page can answer instantly from the
    cache. Never fatal — an unverified property just reads as unproven.
    """
    if not config.get("auction_check.enabled", True):
        return
    address = payload.get("address")
    if not address:
        return
    try:
        from .auction_check import assess
        from .dating import parse_iso
        from .dating_view import dating_for_row
        from .fetch import build_fetcher
        from .normalise import normalise_address

        row = next((r for r in store.list_tracked()
                    if r["url"] == payload["url"].split("?")[0]), None)
        if row is None:
            return
        dating = dating_for_row(row, store, config)
        start = parse_iso(dating.get("campaign_start"))
        if start is None:
            return
        fetcher = build_fetcher(config)
        try:
            result = assess(
                address_norm=normalise_address(address.split(",")[0]).norm,
                postcode=payload.get("postcode") or row["postcode"],
                campaign_start=start,
                days_on_market=dating.get("days_on_market"),
                price_text=payload.get("price_text") or row["price_text"],
                store=store, config=config, fetcher=fetcher,
            )
        finally:
            fetcher.close()
        logger.info("Auction check for %s: %s", address, result.state)
    except Exception:
        logger.exception("Auction check failed for %s", payload.get("url"))


def _date_campaign_safely(store, config, payload: dict,
                          html: str | None = None) -> None:
    """Look up campaign-start evidence for a newly tracked property.

    Never fatal: a property with no datable evidence still tracks fine, it
    just shows a floor date instead of a documented one.
    """
    from .dating import parse_iso
    from .dating_service import date_campaign
    fetcher = None
    try:
        # A fetcher is still needed when the listing HTML is already in hand:
        # the agent's profile page is a second page, and it is where REA
        # actually publishes the listed date. The fetch layer caches to disk
        # per run date, so one agent serves their whole roster for free.
        need_fetcher = (html is None and config.get("dating.use_listing_page", True)) \
            or config.get("dating.use_agent_page", True)
        if need_fetcher:
            from .fetch import build_fetcher
            fetcher = build_fetcher(config)
        date_campaign(
            payload["url"], store, config,
            listed_date=parse_iso(payload.get("date_listed")),
            observed_since=date.today(),
            fetcher=fetcher, html=html,
        )
    except Exception:
        logger.exception("Campaign dating failed for %s", payload.get("url"))
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:
                pass


def _basic_auth_token() -> str | None:
    """The base64 "user:pass" this server will accept, or None to stay open.

    Auth turns on only when PASSEDIN_PASSWORD is set, so local single-user
    runs are unchanged. A hosted deployment must set it: /api/scan spends
    scrape.do credits and the tracker is personal.
    """
    password = os.environ.get("PASSEDIN_PASSWORD")
    if not password:
        return None
    user = os.environ.get("PASSEDIN_USER", "reece")
    return base64.b64encode(f"{user}:{password}".encode()).decode()


def _session_token(password: str) -> str:
    """The cookie value that proves the password was entered.

    Derived from the password rather than stored, so it survives a restart
    without any session table, and changing PASSEDIN_PASSWORD invalidates
    every existing cookie for free. Single-user: holding this token is
    exactly equivalent to having known the password.
    """
    return hashlib.sha256(f"passedin-session:{password}".encode()).hexdigest()


SESSION_COOKIE = "passedin_session"

# Shown in place of the report when signed out. Deliberately not the browser's
# Basic dialog: that cannot be styled, cannot be dismissed, and has no obvious
# way back once you cancel it.
_LOGIN_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Passed-In</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, -apple-system, sans-serif; margin: 0;
         min-height: 100vh; display: grid; place-items: center;
         background: #f6f6f4; color: #1a1a1a; }
  @media (prefers-color-scheme: dark) {
    body { background: #17181a; color: #ececec; }
    form { background: #212327 !important; border-color: #33363b !important; }
    input { background: #17181a !important; color: #ececec !important;
            border-color: #3a3d43 !important; }
  }
  form { background: #fff; border: 1px solid #e2e2dd; border-radius: 12px;
         padding: 28px 26px; width: min(20rem, 90vw);
         box-shadow: 0 1px 3px rgba(0,0,0,.06); }
  h1 { font-size: 17px; margin: 0 0 4px; }
  p.sub { margin: 0 0 18px; color: #6b6b6b; font-size: 13px; }
  input { width: 100%; padding: 9px 11px; font: inherit; border-radius: 7px;
          border: 1px solid #d5d5cf; box-sizing: border-box; }
  button { width: 100%; margin-top: 12px; padding: 9px; font: inherit;
           font-weight: 600; border: 0; border-radius: 7px;
           background: #2d6a4f; color: #fff; cursor: pointer; }
  button:hover { background: #245741; }
  .err { color: #a3320b; font-size: 13px; margin-top: 10px; min-height: 1em; }
</style>
<form id="f">
  <h1>Passed-In</h1>
  <p class="sub">Enter the password to continue.</p>
  <input id="pw" type="password" autocomplete="current-password" autofocus>
  <button type="submit">Sign in</button>
  <div class="err" id="err"></div>
</form>
<script>
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("err");
  err.textContent = "";
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: document.getElementById("pw").value }),
  });
  if (resp.ok) { location.reload(); }
  else { err.textContent = "Wrong password."; document.getElementById("pw").select(); }
});
</script>
"""


def serve(report_path: Path, db_path: Path, base_dir: Path, log_dir: Path,
          port: int = 8765, config=None, host: str = "127.0.0.1") -> None:
    report_path = Path(report_path)
    auth_token = _basic_auth_token()
    password = os.environ.get("PASSEDIN_PASSWORD")
    session_token = _session_token(password) if password else None
    runner = ScanRunner(base_dir, log_dir,
                        config_path=getattr(config, "path", None))
    enricher = Enricher(db_path, config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.info("%s " + fmt, self.address_string(), *args)

        def _json(self, payload, code: int = 200):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _cors(self):
            # Lets the Chrome extension's service worker call this server.
            # Origin stays "*" rather than credentialed: the extension sends
            # an explicit Authorization header, it does not use cookies.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization")

        def _authorised(self) -> bool:
            """A session cookie from the login form, or Basic credentials.

            Basic stays supported because the Chrome extension and the weekly
            GitHub Actions cron authenticate that way; only the browser is
            moved onto the cookie.
            """
            if auth_token is None:
                return True
            cookie = self._cookie(SESSION_COOKIE)
            if cookie and session_token and hmac.compare_digest(cookie, session_token):
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            return hmac.compare_digest(header[6:].strip(), auth_token)

        def _cookie(self, name: str) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            return SimpleCookie(raw)[name].value if name in SimpleCookie(raw) else None

        def _challenge(self) -> None:
            # No WWW-Authenticate: that header is what makes the browser throw
            # up its own unstyled credentials dialog, which the login form
            # replaces. Clients using Basic send it preemptively anyway.
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _login_page(self) -> None:
            body = _LOGIN_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _with_store(self, fn):
            store = Store(db_path)
            try:
                return fn(store)
            finally:
                store.close()

        def _tracked_payload(self, store) -> list[dict]:
            """Tracked rows plus reconstructed campaign dating. Read-only:
            the network lookups happened once, at add time."""
            out = []
            for row in store.list_tracked():
                item = {k: row[k] for k in row.keys()}
                if config is not None:
                    from .dating_view import dating_for_row
                    item.update(dating_for_row(row, store, config))
                out.append(item)
            return out

        @property
        def route(self) -> str:
            """Path without any query string, so cache-busting params like
            ?t=123 still route correctly."""
            return self.path.split("?", 1)[0]

        def do_GET(self):
            if not self._authorised():
                if self.route in ("/", "/index.html", "/report.html"):
                    return self._login_page()
                return self._challenge()
            if self.route in ("/", "/index.html", "/report.html"):
                body = report_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.route == "/api/scan/status":
                self._json(runner.status())
            elif self.route == "/api/tracked":
                self._json({"rows": self._with_store(self._tracked_payload),
                            **enricher.status()})
            elif self.route == "/api/settings":
                if config is None or config.path is None:
                    self._json({"error": "no config file loaded"}, 503)
                else:
                    from .settings import read_settings
                    self._json(read_settings(config.path))
            else:
                self.send_error(404)

        def _login(self) -> None:
            if session_token is None:
                # No password configured: nothing to sign in to.
                return self._json({"ok": True})
            try:
                supplied = str(self._body().get("password", ""))
            except (ValueError, json.JSONDecodeError):
                return self._json({"ok": False}, 400)
            if not hmac.compare_digest(supplied, password or ""):
                logger.warning("Failed sign-in from %s", self.address_string())
                return self._json({"ok": False}, 401)
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # Secure only over HTTPS, which behind Fly's proxy is what the
            # forwarded-proto header reports rather than the socket itself.
            https = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            cookie = (f"{SESSION_COOKIE}={session_token}; Path=/; HttpOnly; "
                      f"SameSite=Lax; Max-Age=31536000")
            self.send_header("Set-Cookie", cookie + ("; Secure" if https else ""))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.route == "/api/login":
                return self._login()
            if not self._authorised():
                return self._challenge()
            try:
                if self.route == "/api/scan":
                    started = runner.start()
                    self._json({"started": started, **runner.status()},
                               200 if started else 409)
                elif self.route == "/api/user":
                    payload = self._body()
                    self._with_store(lambda s: s.set_user_fields(
                        payload["property_id"],
                        dismissed=payload.get("dismissed"),
                        rating=payload.get("rating"),
                        notes=payload.get("notes")))
                    self._json({"ok": True})
                elif self.route == "/api/track":
                    payload = self._body()
                    if payload.get("price_text") and payload.get("price_low") is None:
                        low, high = parse_money_range(payload["price_text"])
                        payload["price_low"], payload["price_high"] = low, high
                    needs_fetch = bool(payload.pop("fetch", False)) or \
                        not payload.get("address")
                    # Save what we have immediately so the row appears at once;
                    # fetching a listing through the proxy can take a minute and
                    # must not hold the request open.
                    tracked_id = self._with_store(lambda s: s.upsert_tracked(payload))
                    logger.info("Tracked property saved: %s (%s)",
                                payload.get("address"), payload.get("url"))
                    enriching = False
                    if config is not None and payload.get("url"):
                        enriching = enricher.start(payload, needs_fetch)
                    self._json({"ok": True, "tracked_id": tracked_id,
                                "enriching": enriching})
                elif self.route == "/api/tracked/update":
                    payload = self._body()
                    self._with_store(lambda s: s.update_tracked(
                        int(payload["tracked_id"]),
                        status=payload.get("status"),
                        notes=payload.get("notes")))
                    self._json({"ok": True})
                elif self.route == "/api/settings":
                    from .settings import SettingsError, write_settings
                    if config is None or config.path is None:
                        self._json({"ok": False,
                                    "error": "no config file loaded"}, 503)
                    else:
                        try:
                            result = write_settings(config.path, self._body())
                        except SettingsError as e:
                            # A rejected value is the user mistyping, not a
                            # bug — it needs to reach the panel as a message,
                            # and config.yaml is already untouched.
                            self._json({"ok": False, "error": str(e)}, 400)
                        else:
                            # The scan runs as a subprocess and re-reads the
                            # file, but this process holds its own copy.
                            config.reload()
                            self._json({"ok": True, **result})
                elif self.route == "/api/tracked/remove":
                    payload = self._body()
                    self._with_store(lambda s: s.remove_tracked(int(payload["tracked_id"])))
                    self._json({"ok": True})
                else:
                    self.send_error(404)
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception:
                logger.exception("POST %s failed", self.path)
                self.send_error(500)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Review page: http://{host}:{port}/  (Ctrl-C to stop)")
    if auth_token is None and host != "127.0.0.1":
        logger.warning("Serving on %s with no PASSEDIN_PASSWORD set — "
                       "/api/scan and the tracker are open to anyone.", host)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
