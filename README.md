# Passed-In Property Finder

Surfaces Melbourne properties that **failed to sell at auction** — passed in,
no bid, withdrawn, unreported — within a budget and suburb set, so they can be
reviewed and approached directly while the vendor is softest (the days right
after a failed campaign).

Built to requirements in `passed-in-property-finder-requirements.md`.
Current state: **REA-first build** (realestate.com.au live; domain.com.au
parser deferred — its schema notes are captured in `config.yaml`).

## The weekly ritual

```bash
python -m passedin scan     # Sunday morning. Fetch → parse → store → report.
python -m passedin serve    # open the review page; dismiss / rate / note
```

Or don't run it at all: the hosted deployment does the scan on a schedule and
keeps the review page up. See [Hosted deployment](#hosted-deployment).

Optionally run `scan` again Tuesday to catch late-reported results and
"sold after auction" corrections — runs are idempotent per results-week.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp secrets.env.sample secrets.env   # add your scrape.do token
.venv/bin/python -m passedin scan
```

The default fetcher proxies through **scrape.do** (`SCRAPE_DO_TOKEN` in
`secrets.env`) — the same unblocker the propertypath-backend uses.
Verified 2026-08: realestate.com.au's Kasada protection blocks local
undetected-chromedriver even headed and attended, so the proxy is the
reliable path. Every page is disk-cached per run date, so credits are spent
once per page per week; re-parses are free.

## Property tracker

A second tab in the review page tracks individual listings you're watching,
independent of the weekly auction pipeline. Each property renders as a card:
photo, agency banner (brand colour + agent), price guide, beds/baths/cars,
land size, inspection/auction times, floor plan link, status and notes.
Clicking the card opens the listing on realestate.com.au.

Two ways to add one:

- **Chrome extension** (`chrome-extension/`, see its README) — one click on
  any listing page you're viewing.
- **Paste a URL** into the tracker tab — the server fetches the listing
  through the same pluggable fetch layer and fills in every field.

Both paths converge on `POST /api/track`, so a property added either way is
identical. Re-adding a URL refreshes the listing facts and preserves your
status and notes.

## Time on market (campaign dating)

Days-on-market is not a field you read — it's a field you reconstruct. Both
portals compute it from the *current* listing record, so withdrawing a stale
listing and relisting it resets the counter to zero, and Australia has no MLS
holding a cumulative figure. On exactly the properties worth chasing, the
published number understates reality. See `docs/dating-a-campaign.md`.

So the tool gathers candidate start dates and takes the **earliest
defensible** one, since each is a lower bound on how long the property has
been advertised. Trust breaks ties and sets the label:

| Basis | Kind | Where it comes from |
|---|---|---|
| `soi-document` | Documented | Publication time of the Statement of Information |
| `archive-capture` | Documented | First archive.org capture of the listing URL |
| `agent-listed` | Documented | `Listed 28 Jul 2026` on the listing agent's profile page |
| `history-page` | Documented | A property-history section stating the first date |
| `auction-inferred` | Inferred | Auction date − `auction_campaign_days`; can't be reset |
| `soi-median-period` | Inferred | SOI median period end (must be ≤6 months old) |
| `observed-floor` | Floor | First week this tool saw it; shown as `Nd+` |
| `current-listing` | Weakest | What the portal claims — the resettable number |

When the portal's counter is ≥21 days behind the evidence, the card shows
both figures (`14d listed / 116d real`) and flags **Clock reset · 102d
hidden**. That gap isn't a data-quality problem; it's someone restarting the
clock on a property that failed and is still sitting there.

**Cost.** Cheapest-first: archive.org needs no page load, and the listing
page is only fetched when the archive comes back empty. Results are cached
permanently in `campaign_dates` — a campaign's start date never changes — so
it's one lookup per address ever, not per week.

Two findings from verifying this against live pages:

- REA rehosts the agency's Statement of Information under a **content-hash
  filename**, discarding the timestamped filename the spec relies on. The
  publication time is instead read from the CDN's `Last-Modified` header via
  a free HEAD request. Read it as "advertised by this date" — the SOI is
  re-issued on price changes, so a late upload can be a mid-campaign
  re-issue. Harmless, because the earliest candidate wins.
- **archive.org has no captures of individual REA listing URLs** in testing,
  so in practice the SOI and the auction anchor do the work.

Known soft spot, carried over from the spec: `auction_campaign_days: 28` is
an assumption, so a longer campaign makes `auction-inferred` dates late.
Wrong in the safe direction — read those as "at least N days".

### The listing date REA does publish

A REA listing page states no date, but the listing **agent's profile page**
stamps every property they currently have on market with `Listed 28 Jul
2026`. That roster is server-rendered into the same `ArgonautExchange`
payload the auction-results pages use, under `agentMapBuyListings`.

Two things make it the cheapest date in the pipeline:

- **The whole roster is already in the HTML.** Switching the Sold/For sale
  dropdown fires no request, and "see more" reveals rows that were always
  there — `buyListings` holds the first three, `agentMapBuyListings` all of
  them. So one fetch dates *every* property that agent is advertising
  (verified 2026-08-28: 18 listings from one page), and the per-run disk
  cache makes each additional property on the same agent free.
- **It is REA's own claim**, which is exactly what clock-reset detection was
  missing. Resets previously only surfaced on Domain; now the gap between
  REA's stated date and the SOI or auction evidence exposes them on REA too.

Read it as "advertised by this date". A relist resets it like any portal
counter — but a reset only ever moves the date *later*, so it stays a lower
bound and is ranked as documented. The earliest-candidate rule then does the
rest: where an SOI predates it, the SOI still wins.

Verified 2026-08-28 on 335 Bambra Road, Caulfield South: the listing page
carried no date at all, and the agent roster gave **Listed 28 Jul 2026**.

## Commands

| Command | What it does |
|---|---|
| `scan [--refetch] [--no-enrich]` | Weekly run. Caches every page to disk; a same-day re-run re-parses from cache with zero traffic. |
| `report [--week W]` | Rebuild HTML + CSV from the store, no fetching. |
| `serve [--port 8765]` | Review page at localhost with dismiss/rating/notes persisted to SQLite. |
| `export [--week W]` | CSV only. |

Outputs land in `data/`: `report.html`, `export.csv`, `passedin.sqlite`,
`cache/<date>/`, `logs/`.

## How it works

```
fetch layer (pluggable: scrapedo | chrome | requests)   config.yaml
      │  every page cached to data/cache/<date>/         │
      ▼                                                   ▼
sources/rea.py — parses embedded JSON state (ArgonautExchange),
      │          never the DOM; all JSON paths from config
      ▼
pipeline.py — canonical outcomes (table-driven, UNKNOWN is loud),
      │       address normalisation, stable property_id, bid capture
      ▼
dedupe.py — exact merge on normalised address; fuzzy merges flagged LOW
      ▼
store.py — SQLite; one snapshot per property per week, appended history
      ▼
enrich.py — listing pages fetched for the best leads (budgeted);
      │     price chain: QUOTED range → published bid → RELISTED price
      ▼
assemble.py + report/ — sections (new / still available / stretch /
                        no-price / disappeared / recently sold),
                        config-driven ranking, HTML + CSV + run summary
```

Key behaviours enforced throughout (and covered by tests):

- **UNKNOWN-priced leads are never dropped** — they get their own section.
- **Unrecognised outcome labels** are logged loudly, surfaced as `UNKNOWN`,
  and listed in the run summary — never silently skipped.
- **Null land size means unknown → included**; the filter only applies to
  known sizes.
- **Budget filter uses the range's lower bound** — vendors quote low.
- **Fuzzy dedupe merges are flagged** `merge_confidence: LOW`, never assumed.
- **Parse canary**: suburb-index size, per-suburb parse rate, and
  week-over-week volume are checked every run; problems flip the exit code
  to 2 and are printed in the summary. Selector rot looks like a quiet
  week — this is what catches it.
- **Resumable / resilient**: pages cache to disk before parsing, records
  persist per suburb, one bad suburb or row never kills the run.

## Config

Everything tunable is in `config.yaml`, commented: suburbs, price ceiling
(and optional stretch ceiling), bedrooms, property-type/agency/street
exclusions, outcome label mappings, JSON paths, ranking weights, rate
limits, canary thresholds.

**The search criteria are also editable from the review page** — the
*Settings* tab exposes suburbs, both price ceilings, minimum bedrooms,
property types, minimum land size and the street/agency exclusions. Saving
writes straight back to `config.yaml`, so there is no second source of
truth, and the next scan picks it up.

Two things make that safe to do from a browser:

- **Only those criteria are writable.** The server holds a whitelist; a
  request naming anything else — a selector, a JSON path, a rate limit — is
  refused. A typo in the panel can't disable the parse canary.
- **The file keeps its comments.** They are this project's documentation, so
  saving does not re-dump the parsed document (which erases them) or use a
  round-trip YAML library (which moves them around — emptying a list ate a
  whole section header in testing). Instead only the exact lines of the keys
  you changed are rewritten; every other byte is passed through, and the
  result is re-parsed and checked before it replaces the real file. A
  rejected value leaves `config.yaml` untouched. Secrets (if a remote fetcher is wired in) go in
`secrets.env` (gitignored, see `secrets.env.sample`).

Rate limits default to a 2.5–6 s randomised delay between sequential
requests — human-scale. Both source sites' terms prohibit automated
collection; this is a personal-use tool at low volume, cached aggressively,
and its data must not be redistributed.

## Has it already failed at auction?

For a property currently for sale, the tracker flags whether it has *already*
been to auction and failed to sell — the strongest reason to ring the agent.
No portal states this: REA records no pass-in history, and property.com.au's
timeline covers sold / rent / leased / withdrawn only. See
`docs/two-date-auction-detection.md`.

The method narrows the auction, if there was one, to one or two Saturdays
(campaigns run 3–5 weeks and finish on a Saturday), then checks actual
result records:

1. **This tool's own weekly scans** — free, already in the database.
2. **Domain's dated auction-results archive** (~6 months retained) — the only
   public record naming a failed auction.

```bash
python -m passedin auctions --weeks 12    # cache 12 Saturdays of results
```

Worth doing once: a cached week is shared by **every** property, so after a
backfill any address is matched against months of results instantly and
without needing to guess when its campaign started. That matters because a
Statement of Information re-issued on relist dates the *new* campaign, hiding
the original one behind the reset.

States, strongest first — and a probable pass-in is **never** collapsed into a
confirmed one, because the agent you're about to ring knows which is true:

| State | Meaning |
|---|---|
| **Confirmed pass-in** | Found in a result record with a non-sale outcome |
| **Confirmed sold** | Found sold — sale fell through, or it's a different property |
| **Probable pass-in** | Well past the area benchmark *and* quoting a single figure, but unverified |
| **Possible pass-in** | One of those signals only |
| **Normal** | Neither, or too recent for an auction to have run |

Two rules that shape the output: an address **absent** from the results is
reported as unproven, never as "no auction" — agents frequently don't report
failures. And addresses match on **postcode**, not suburb name, because the
sources disagree (the same 3181 address is filed as Windsor by one and
Prahran by another).

Staleness is measured as a ratio against `auction_check.suburb_median_days`
(default 30) rather than an absolute day count. That default is an
**assumption** — a real per-suburb median would come from property.com.au,
which isn't wired in.

## Land size and a listing date that isn't inferred

REA leaves two gaps: it only publishes land size when the agent supplied
one, and it publishes no listing date at all. Domain fills both, and its
profile URLs are derivable straight from an address:

```
/property-profile/13-fern-avenue-windsor-vic-3181   -> land size, features
/13-fern-avenue-prahran-vic-3181-2020923711         -> dateListed
```

```bash
python -m passedin domain --limit 10     # fill the gaps that matter
```

Verified 2026-08-13 on 13 Fern Avenue: the profile gave **203 m²** (matching
REA exactly) and the listing gave **dateListed 2026-06-17** — the same date
an independent property.com.au reading produced, and one that survives the
REA relist an SOI can't see past. It becomes a `domain-listed` candidate,
ranked as documented.

**property.com.au is not usable for this.** Its URLs require a PropTrack
property ID (`.../27-pid-3756367/`), REA's payload doesn't contain one, and
its only free-text address lookup is a GraphQL call in a lazily-loaded
chunk. Domain needs no ID at all.

Two things to know:

- **Suburb names differ between and within sources.** The same 3181 address
  is *Windsor* on Domain's profile URL, *Prahran* on Domain's own listing,
  and *Prahran* on REA. Candidate slugs are tried in turn; add known pairs
  to `domain_profile.suburb_aliases`.
- **It costs up to two premium fetches per property**, so both are gated:
  the profile is only fetched when REA gave no land size, and the listing
  only when the campaign date still rests on something resettable
  (`current-listing`, `observed-floor`, or an SOI that a relist would have
  re-issued). A property with a hard auction anchor is skipped entirely.

## Hosted deployment

Live at **https://passedin-reece.fly.dev** — one always-on Fly.io machine in
Sydney, so the weekly scan and the tracker no longer need a laptop running.
Log in with the username and password held in the app's Fly secrets.

```bash
fly deploy                       # ship a change
fly logs --app passedin-reece    # watch a scan
fly ssh console --app passedin-reece
```

**State** lives on a 3 GB volume mounted at `/data`: the SQLite store, the
page cache, logs, and the live `config.yaml`. Nothing is in the image.

**Config has two halves that need opposite homes.** The search criteria are
written back by the settings panel, so they must survive a redeploy; the
scrape selectors are the thing you edit when a source breaks, so they must
come from the image. `deploy/bootstrap.py` reconciles them on every boot —
the image's file wins, then the saved criteria are re-applied on top through
the same whitelisted, comment-preserving writer the panel uses.

**Auth** is HTTP Basic, and it switches on only when `PASSEDIN_PASSWORD` is
set. A local `passedin serve` leaves it unset and behaves exactly as before;
hosted, it keeps `/api/scan` — which spends scrape.do credits — and the
tracker private. It is a single shared login, not per-user accounts: the
tables still have no user scope, so this is a lock on the front door rather
than multi-tenancy.

**The machine is deliberately not set to auto-stop.** A scan runs as a
background subprocess with no open HTTP connection, so an idle-based suspend
would kill a run mid-flight and waste the credits it had already spent.

**The weekly scan** is a GitHub Actions cron (`.github/workflows/weekly-scan.yml`)
that POSTs to `/api/scan` — Fly has no precise scheduler, and the endpoint
the review page already uses is the same button. It fires 22:00 UTC Saturday,
which is 8am Sunday AEST and 9am AEDT. Repo secrets `PASSEDIN_URL`,
`PASSEDIN_USER` and `PASSEDIN_PASSWORD` drive it; `workflow_dispatch` runs it
by hand. A 409 (scan already running) is treated as success, not failure.

**The image** installs `requirements-deploy.txt`, not `requirements.txt`:
`build_fetcher` imports each fetcher lazily and the deployment runs
`fetch.fetcher: scrapedo`, so selenium and undetected-chromedriver are never
imported and are left out — 39 MB instead of several hundred. Switching the
hosted config to `chrome` would need them back, plus a browser in the image.

**The extension** stores its server address and password in
`chrome.storage.sync`, set on its options page, so the same build works
against localhost and against the hosted origin.

### Still local-only if you scale it up

- **Storage** is entirely behind `store.py` (one class, plain SQL). Moving to
  Postgres means swapping that class's connection and the few SQLite-specific
  bits (`INSERT OR REPLACE`, `AUTOINCREMENT`); no other module touches the DB.
  Schema changes are applied by the idempotent migration loop in `Store.__init__`.
- **More than one user** would need a per-user scope on every table, real
  accounts instead of the shared Basic login, and tighter CORS than the
  current `*`.
- **The scan is a subprocess**, one at a time per machine. Concurrent users or
  multiple machines would want a job queue.

## Extending

- **Domain source**: implement `sources/domain.py` parsing `__NEXT_DATA__`
  (schema notes + result-code mapping already in `config.yaml`), set
  `sources.domain.enabled: true`. Dedupe/merge across sources already works
  via normalised-address property IDs.
- **Different fetch layer**: anything with `fetch(url) -> html` and
  `close()` can be dropped into `passedin/fetch/` and selected in config —
  e.g. the propertypath-backend Browserless session.

## Tests

```bash
.venv/bin/python -m pytest
```

Fixtures replicate the real REA embedded-JSON structure (captured
2026-08-12), including the double-encoded payload, interstate-pointing
`canonicalLink` quirk, and vendor-bid rows.
