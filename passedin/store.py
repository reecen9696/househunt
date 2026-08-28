"""SQLite persistence (§7, §11).

Single file, no server. One `properties` row per physical property carrying
identity and the user-owned fields (dismissed / rating / notes — without
these the same 40 irrelevant properties reappear weekly and the tool dies
by week three). One `snapshots` row per property per results week: runs are
snapshots appended to history, never replacements.

Writes happen incrementally during a scan (per suburb), so a failure at
suburb 40 of 60 keeps the first 39.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from .model import PropertyRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    property_id     TEXT PRIMARY KEY,
    address_raw     TEXT NOT NULL,
    address_norm    TEXT NOT NULL,
    street_number   TEXT,
    street          TEXT,
    suburb          TEXT NOT NULL,
    postcode        TEXT,
    first_seen_week TEXT,
    last_seen_week  TEXT,
    dismissed       INTEGER NOT NULL DEFAULT 0,
    user_rating     INTEGER,
    user_notes      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshots (
    property_id      TEXT NOT NULL,
    week_ending      TEXT NOT NULL,
    run_date         TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    outcome_raw      TEXT,
    sources          TEXT,   -- JSON list
    source_urls      TEXT,   -- JSON object source -> url
    highest_bid      INTEGER,
    vendor_bid       INTEGER,
    price_low        INTEGER,
    price_high       INTEGER,
    price_status     TEXT NOT NULL DEFAULT 'UNKNOWN',
    price_source_url TEXT,
    sold_price       INTEGER,
    property_type    TEXT,
    bedrooms         INTEGER,
    bathrooms        INTEGER,
    car_spaces       INTEGER,
    land_size_sqm    REAL,
    agency_name      TEXT,
    agent_name       TEXT,
    image_url        TEXT,
    merge_confidence TEXT NOT NULL DEFAULT 'HIGH',
    PRIMARY KEY (property_id, week_ending)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date     TEXT NOT NULL,
    week_ending  TEXT,
    summary_json TEXT
);

-- Manually tracked properties (added via the Chrome extension or the UI),
-- independent of the weekly auction-results pipeline.
CREATE TABLE IF NOT EXISTS tracked_properties (
    tracked_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL UNIQUE,
    address       TEXT,
    suburb        TEXT,
    postcode      TEXT,
    property_type TEXT,
    bedrooms      INTEGER,
    bathrooms     INTEGER,
    car_spaces    INTEGER,
    price_text    TEXT,     -- advertised guide / estimate, verbatim
    price_low     INTEGER,
    price_high    INTEGER,
    date_listed   TEXT,     -- REA does not publish one; usually null
    inspection_text TEXT,   -- e.g. "Inspection Sat 15 Aug 10:30 am"
    auction_text  TEXT,     -- e.g. "Auction Sat 15 Aug"
    land_size_sqm REAL,
    image_url     TEXT,
    floorplan_url TEXT,
    agency_name   TEXT,
    agent_name    TEXT,
    agency_color  TEXT,     -- brand colour for the card banner
    status        TEXT NOT NULL DEFAULT 'active',
    user_notes    TEXT NOT NULL DEFAULT '',
    added_date    TEXT NOT NULL
);

-- Campaign start dates. Cached permanently and keyed by listing URL: a
-- campaign's start date never changes, so this is one lookup per address
-- ever, not one per weekly run.
CREATE TABLE IF NOT EXISTS campaign_dates (
    url          TEXT PRIMARY KEY,
    start_date   TEXT,
    basis        TEXT,
    kind         TEXT,
    detail       TEXT,
    candidates   TEXT,   -- JSON list of {day, basis, detail}
    looked_up_at TEXT NOT NULL
);

-- Domain's dated auction-results archive, cached permanently: a past
-- Saturday's results don't change, and one fetched week answers the
-- question for every property that auctioned that day.
CREATE TABLE IF NOT EXISTS domain_auction_weeks (
    week_day   TEXT PRIMARY KEY,   -- the auction Saturday, ISO
    fetched_at TEXT NOT NULL,
    row_count  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS domain_auction_rows (
    week_day     TEXT NOT NULL,
    address_norm TEXT NOT NULL,
    suburb       TEXT,
    postcode     TEXT,
    result_raw   TEXT,
    outcome      TEXT,
    property_type TEXT,
    bedrooms     INTEGER,
    agency       TEXT,
    price_text   TEXT,
    url          TEXT,
    PRIMARY KEY (week_day, address_norm, postcode)
);

-- Domain property profiles: land size and a real listing date, keyed by
-- the address slug. Cached permanently — land size doesn't change, and a
-- campaign's start date doesn't either.
CREATE TABLE IF NOT EXISTS domain_profiles (
    url             TEXT PRIMARY KEY,   -- the tracked property's listing URL
    slug            TEXT,
    land_size_sqm   REAL,
    bedrooms        INTEGER,
    bathrooms       INTEGER,
    car_spaces      INTEGER,
    property_type   TEXT,
    domain_listing_url TEXT,
    date_listed     TEXT,
    display_address TEXT,
    looked_up_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_domain_rows_addr
    ON domain_auction_rows(address_norm, postcode);
CREATE INDEX IF NOT EXISTS idx_snapshots_week ON snapshots(week_ending);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        # Migrations for stores created before these columns existed.
        for table, column, decl in (
            ("snapshots", "image_url", "TEXT"),
            ("tracked_properties", "floorplan_url", "TEXT"),
            ("tracked_properties", "land_size_sqm", "REAL"),
            ("tracked_properties", "agent_name", "TEXT"),
            ("tracked_properties", "agency_color", "TEXT"),
            ("tracked_properties", "inspection_text", "TEXT"),
            ("tracked_properties", "auction_text", "TEXT"),
            ("tracked_properties", "auction_date", "TEXT"),
            ("tracked_properties", "sale_method", "TEXT"),
            ("tracked_properties", "agent_profile_url", "TEXT"),
        ):
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- writes --------------------------------------------------------------

    def upsert_records(self, records: Iterable[PropertyRecord],
                       run_date: Optional[str] = None) -> int:
        """Idempotent: (property_id, week_ending) is the natural key, so
        running twice on the same day cannot duplicate records."""
        run_date = run_date or date.today().isoformat()
        n = 0
        cur = self.conn.cursor()
        for r in records:
            week = r.week_ending or run_date
            cur.execute(
                """INSERT INTO properties
                       (property_id, address_raw, address_norm, street_number,
                        street, suburb, postcode, first_seen_week, last_seen_week)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(property_id) DO UPDATE SET
                       address_raw = excluded.address_raw,
                       last_seen_week = MAX(last_seen_week, excluded.last_seen_week)""",
                (r.property_id, r.address_raw, r.address_norm, r.street_number,
                 r.street, r.suburb, r.postcode, week, week),
            )
            cur.execute(
                """INSERT OR REPLACE INTO snapshots
                       (property_id, week_ending, run_date, outcome, outcome_raw,
                        sources, source_urls, highest_bid, vendor_bid,
                        price_low, price_high, price_status, price_source_url,
                        sold_price, property_type, bedrooms, bathrooms, car_spaces,
                        land_size_sqm, agency_name, agent_name, image_url,
                        merge_confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r.property_id, week, run_date, r.outcome, r.outcome_raw,
                 json.dumps(r.sources), json.dumps(r.source_urls),
                 r.highest_bid, r.vendor_bid,
                 r.price_low, r.price_high, r.price_status, r.price_source_url,
                 r.sold_price, r.property_type, r.bedrooms, r.bathrooms,
                 r.car_spaces, r.land_size_sqm, r.agency_name, r.agent_name,
                 r.image_url, r.merge_confidence),
            )
            n += 1
        self.conn.commit()
        return n

    def update_snapshot_price(self, property_id: str, week_ending: str,
                              price_low: Optional[int], price_high: Optional[int],
                              price_status: str, price_source_url: Optional[str]) -> None:
        self.conn.execute(
            """UPDATE snapshots SET price_low=?, price_high=?, price_status=?,
                                    price_source_url=?
               WHERE property_id=? AND week_ending=?""",
            (price_low, price_high, price_status, price_source_url,
             property_id, week_ending),
        )
        self.conn.commit()

    def set_user_fields(self, property_id: str, dismissed: Optional[bool] = None,
                        rating: Optional[int] = None, notes: Optional[str] = None) -> None:
        sets, params = [], []
        if dismissed is not None:
            sets.append("dismissed=?"); params.append(int(dismissed))
        if rating is not None:
            sets.append("user_rating=?"); params.append(rating)
        if notes is not None:
            sets.append("user_notes=?"); params.append(notes)
        if not sets:
            return
        params.append(property_id)
        self.conn.execute(f"UPDATE properties SET {', '.join(sets)} WHERE property_id=?",
                          params)
        self.conn.commit()

    def record_run(self, week_ending: Optional[str], summary: dict) -> None:
        self.conn.execute(
            "INSERT INTO runs (run_date, week_ending, summary_json) VALUES (?,?,?)",
            (date.today().isoformat(), week_ending, json.dumps(summary)),
        )
        self.conn.commit()

    # --- reads ---------------------------------------------------------------

    def latest_week(self) -> Optional[str]:
        row = self.conn.execute("SELECT MAX(week_ending) AS w FROM snapshots").fetchone()
        return row["w"] if row and row["w"] else None

    def snapshots_for_week(self, week_ending: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.*, p.address_raw, p.address_norm, p.suburb AS p_suburb,
                      p.postcode AS p_postcode, p.street, p.street_number,
                      p.first_seen_week, p.last_seen_week,
                      p.dismissed, p.user_rating, p.user_notes
               FROM snapshots s JOIN properties p USING (property_id)
               WHERE s.week_ending = ?""",
            (week_ending,),
        ).fetchall()

    def history(self, property_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT week_ending, outcome, price_low, price_high, price_status
               FROM snapshots WHERE property_id=? ORDER BY week_ending""",
            (property_id,),
        ).fetchall()

    def weeks_unsold(self, property_id: str, lead_outcomes: set[str],
                     up_to_week: str) -> int:
        row = self.conn.execute(
            f"""SELECT COUNT(DISTINCT week_ending) AS n FROM snapshots
                WHERE property_id=? AND week_ending<=?
                  AND outcome IN ({','.join('?' * len(lead_outcomes))})""",
            (property_id, up_to_week, *sorted(lead_outcomes)),
        ).fetchone()
        return int(row["n"] or 0)

    def previous_lead_snapshot(self, property_id: str, before_week: str,
                               lead_outcomes: set[str]) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            f"""SELECT * FROM snapshots
                WHERE property_id=? AND week_ending<?
                  AND outcome IN ({','.join('?' * len(lead_outcomes))})
                ORDER BY week_ending DESC LIMIT 1""",
            (property_id, before_week, *sorted(lead_outcomes)),
        ).fetchone()

    def open_leads_absent_this_week(self, week_ending: str,
                                    lead_outcomes: set[str]) -> list[sqlite3.Row]:
        """Properties whose latest snapshot before this week was a lead, and
        which have no snapshot this week — DISAPPEARED candidates."""
        return self.conn.execute(
            f"""SELECT p.*, s.outcome AS last_outcome, s.week_ending AS last_week,
                       s.price_low, s.price_high, s.price_status,
                       s.property_type, s.bedrooms, s.agency_name, s.source_urls
                FROM properties p
                JOIN snapshots s ON s.property_id = p.property_id
                 AND s.week_ending = (SELECT MAX(week_ending) FROM snapshots s2
                                      WHERE s2.property_id = p.property_id)
                WHERE s.week_ending < ?
                  AND s.outcome IN ({','.join('?' * len(lead_outcomes))})""",
            (week_ending, *sorted(lead_outcomes)),
        ).fetchall()

    # --- tracked properties (extension / tracker tab) -------------------------

    _TRACKED_FIELDS = ("address", "suburb", "postcode", "property_type",
                       "bedrooms", "bathrooms", "car_spaces", "price_text",
                       "price_low", "price_high", "date_listed", "land_size_sqm",
                       "image_url", "floorplan_url", "agency_name", "agent_name",
                       "agency_color", "inspection_text", "auction_text",
                       "auction_date", "sale_method", "agent_profile_url")

    def upsert_tracked(self, data: dict) -> int:
        """Add or refresh a tracked property by URL. Listing facts are
        updated on re-add; user-owned fields (status, notes) and the
        original added_date are preserved."""
        url = (data.get("url") or "").split("?")[0].strip()
        if not url:
            raise ValueError("tracked property needs a url")
        values = {f: data.get(f) for f in self._TRACKED_FIELDS}
        existing = self.conn.execute(
            "SELECT tracked_id FROM tracked_properties WHERE url=?", (url,)).fetchone()
        if existing:
            sets = ", ".join(f"{f}=COALESCE(?, {f})" for f in self._TRACKED_FIELDS)
            self.conn.execute(
                f"UPDATE tracked_properties SET {sets} WHERE tracked_id=?",
                (*[values[f] for f in self._TRACKED_FIELDS], existing["tracked_id"]))
            self.conn.commit()
            return int(existing["tracked_id"])
        cur = self.conn.execute(
            f"""INSERT INTO tracked_properties
                (url, {', '.join(self._TRACKED_FIELDS)}, added_date)
                VALUES (?{', ?' * len(self._TRACKED_FIELDS)}, ?)""",
            (url, *[values[f] for f in self._TRACKED_FIELDS],
             # Caller may supply this when backfilling a property that was
             # being watched before it was entered here.
             data.get("added_date") or date.today().isoformat()))
        self.conn.commit()
        return int(cur.lastrowid)

    def list_tracked(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tracked_properties ORDER BY added_date DESC, tracked_id DESC"
        ).fetchall()

    def update_tracked(self, tracked_id: int, status: Optional[str] = None,
                       notes: Optional[str] = None) -> None:
        sets, params = [], []
        if status is not None:
            sets.append("status=?"); params.append(status)
        if notes is not None:
            sets.append("user_notes=?"); params.append(notes)
        if not sets:
            return
        params.append(tracked_id)
        self.conn.execute(
            f"UPDATE tracked_properties SET {', '.join(sets)} WHERE tracked_id=?",
            params)
        self.conn.commit()

    def remove_tracked(self, tracked_id: int) -> None:
        self.conn.execute("DELETE FROM tracked_properties WHERE tracked_id=?",
                          (tracked_id,))
        self.conn.commit()

    def earliest_auction_week(self, address_norm: str,
                              suburb: str) -> Optional[str]:
        """Earliest results week this address appeared at auction, if ever.

        A failed auction is a hard, unfakeable date and the only anchor that
        works retrospectively, so it dates campaigns the portals have reset.
        """
        row = self.conn.execute(
            """SELECT MIN(s.week_ending) AS w FROM snapshots s
               JOIN properties p USING (property_id)
               WHERE p.address_norm = ? AND LOWER(p.suburb) = LOWER(?)""",
            (address_norm, suburb),
        ).fetchone()
        return row["w"] if row and row["w"] else None

    # --- Domain dated auction results (permanent cache) -----------------------

    def domain_week_cached(self, week_day: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM domain_auction_weeks WHERE week_day=?", (week_day,)
        ).fetchone() is not None

    def save_domain_week(self, week_day: str, rows: list[dict]) -> None:
        cur = self.conn.cursor()
        for r in rows:
            cur.execute(
                """INSERT OR REPLACE INTO domain_auction_rows
                   (week_day, address_norm, suburb, postcode, result_raw,
                    outcome, property_type, bedrooms, agency, price_text, url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (week_day, r["address_norm"], r.get("suburb"), r.get("postcode"),
                 r.get("result_raw"), r.get("outcome"), r.get("property_type"),
                 r.get("bedrooms"), r.get("agency"), r.get("price_text"),
                 r.get("url")))
        cur.execute(
            """INSERT OR REPLACE INTO domain_auction_weeks
               (week_day, fetched_at, row_count) VALUES (?,?,?)""",
            (week_day, date.today().isoformat(), len(rows)))
        self.conn.commit()

    def find_domain_results(self, address_norm: str,
                            postcode: Optional[str]) -> list[sqlite3.Row]:
        """Auction results for an address across every cached week.

        Matched on postcode rather than suburb name: sources disagree about
        suburb boundaries (the same 3181 address is filed as Windsor by one
        and Prahran by another), and a name mismatch would split one
        property into two.
        """
        if postcode:
            return self.conn.execute(
                """SELECT * FROM domain_auction_rows
                   WHERE address_norm=? AND postcode=? ORDER BY week_day""",
                (address_norm, postcode)).fetchall()
        return self.conn.execute(
            "SELECT * FROM domain_auction_rows WHERE address_norm=? ORDER BY week_day",
            (address_norm,)).fetchall()

    def find_local_results(self, address_norm: str,
                           postcode: Optional[str]) -> list[sqlite3.Row]:
        """Auction results this tool scraped itself, by address."""
        if postcode:
            return self.conn.execute(
                """SELECT s.week_ending, s.outcome, s.outcome_raw, s.agency_name,
                          s.sold_price, p.suburb
                   FROM snapshots s JOIN properties p USING (property_id)
                   WHERE p.address_norm=? AND p.postcode=?
                   ORDER BY s.week_ending""",
                (address_norm, postcode)).fetchall()
        return self.conn.execute(
            """SELECT s.week_ending, s.outcome, s.outcome_raw, s.agency_name,
                      s.sold_price, p.suburb
               FROM snapshots s JOIN properties p USING (property_id)
               WHERE p.address_norm=? ORDER BY s.week_ending""",
            (address_norm,)).fetchall()

    # --- Domain property profiles ---------------------------------------------

    def get_domain_profile(self, url: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM domain_profiles WHERE url=?", (url.split("?")[0],)
        ).fetchone()

    def save_domain_profile(self, url: str, data: dict) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO domain_profiles
               (url, slug, land_size_sqm, bedrooms, bathrooms, car_spaces,
                property_type, domain_listing_url, date_listed, display_address,
                looked_up_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (url.split("?")[0], data.get("slug"), data.get("land_size_sqm"),
             data.get("bedrooms"), data.get("bathrooms"), data.get("car_spaces"),
             data.get("property_type"), data.get("domain_listing_url"),
             data.get("date_listed"), data.get("display_address"),
             date.today().isoformat()))
        self.conn.commit()

    # --- campaign dating cache ------------------------------------------------

    def get_campaign_date(self, url: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM campaign_dates WHERE url=?", (url.split("?")[0],)
        ).fetchone()

    def save_campaign_date(self, url: str, start_date: Optional[str],
                           basis: Optional[str], kind: Optional[str],
                           detail: Optional[str], candidates: list) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO campaign_dates
               (url, start_date, basis, kind, detail, candidates, looked_up_at)
               VALUES (?,?,?,?,?,?,?)""",
            (url.split("?")[0], start_date, basis, kind, detail,
             json.dumps(candidates), date.today().isoformat()),
        )
        self.conn.commit()

    def latest_run_summary(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT summary_json FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
        if not row or not row["summary_json"]:
            return None
        try:
            return json.loads(row["summary_json"])
        except json.JSONDecodeError:
            return None

    def recent_run_counts(self, limit: int = 5) -> list[tuple[str, int]]:
        """(week_ending, lead count) for recent weeks — anomaly detection."""
        rows = self.conn.execute(
            """SELECT week_ending, COUNT(*) AS n FROM snapshots
               WHERE outcome NOT IN ('SOLD','SOLD_PRIOR','SOLD_AFTER','POSTPONED')
               GROUP BY week_ending ORDER BY week_ending DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [(r["week_ending"], r["n"]) for r in rows]
