# Dating a campaign

How the tool works out when a property was *actually* first advertised, and why the number the portals publish can't be used for it.

---

## The problem

The starting assumption was that days-on-market is a field you read. It isn't. It's a field you reconstruct.

Both Domain and realestate.com.au publish a days-on-market figure, and both compute it from the **current listing record only**. When a property is withdrawn and relisted, a new listing record is created and the counter starts at zero.

Withdrawing a stale listing and relisting it fresh, specifically so the counter reads zero, is a deliberate and well-documented agent tactic. Buyers gravitate to new listings, so a stale property gets a new face.

In the United States this is partly recoverable: the MLS tracks cumulative days on market across listings, and a buyer's agent can pull it. **Australia has no MLS.** The portals are the only public record, and their clock is per-campaign. There is no cumulative figure to fall back on.

The consequence is specific and bad:

> On precisely the properties worth chasing — failed campaign, motivated vendor, still sitting — the published days-on-market figure understates reality. Often by months.

A property showing "12 days on market" may have been for sale since February.

This mattered because the earlier version of this tool displayed that field directly, next to a passed-in auction result, as though the two were consistent.

---

## What was already available

One anchor existed before this step: **the auction date itself**.

A failed auction is a hard, unfakeable date. Melbourne auction campaigns run roughly four weeks of advertising, so a property that passed in on 16 May was advertised from about 18 April at the latest. Subtract the campaign length from the auction date and you have a defensible floor.

This is free — the auction date is already collected — and it works **retrospectively**, which observation cannot.

Two limits, though:

1. **It's an inference, not a record.** The 28-day campaign length is an assumption. A six-week campaign makes the estimate about two weeks late.
2. **It requires an auction.** A private-sale listing that has been advertised for four months is the textbook motivated vendor, and there is no auction to work back from. That entire category was invisible.

The second limit is what drove this step.

---

## What was found

### 1. The Statement of Information

Victoria requires a Statement of Information for every residential property sale. The requirements are the useful part:

- it must be **included with online advertising**
- it must be displayed at every open for inspection
- it must be provided to a prospective buyer within two business days of a request
- it must be **updated if the indicative selling price changes**

So a dated statutory document is attached to every live listing, and it is only regenerated on a price change.

Better still, agencies host the PDF on their own CDN, and nearly all of them stamp the upload time into the filename. Three real examples, from three different agency platforms:

| URL fragment | Decodes to |
|---|---|
| `...-1726712725-60120-StatementofInformation.pdf` | 19 Sep 2024 (unix seconds) |
| `1777003617-67073-ONLINESOIAPPROVEDMING24THAPR.pdf` | 24 Apr 2026 (unix seconds) |
| `..._1b90_20240729031027.pdf` | 29 Jul 2024 03:10:27 (YYYYMMDDHHMMSS) |
| `.../uploads/1736834858656-xzrs315y7hd-.../SOI.pdf` | 14 Jan 2025 (unix milliseconds) |

An agent can reset a portal's counter with two clicks. Backdating this requires preparing a new statutory document.

There is also a weaker, independent signal inside the SOI. It quotes a median sale price for a stated period — "01 July 2023 to 30 June 2024" — and that median must be **no more than six months old** when the statement is prepared. So the period's end date bounds campaign start even when the PDF itself can't be read.

### 2. archive.org

The CDX API returns every capture of a URL. The first capture is third-party evidence that the listing existed by that date.

This is the cheapest source in the whole tool: a plain API call, no page load, nothing for a bot detector to see. Coverage of individual listing URLs is patchy, but when it hits, it's a recorded observation rather than arithmetic.

---

### 3. The agent's profile page

A listing page publishes no date at all — `publishedDate` is null, which is why the two sources above had to exist. But the listing **agent's** profile page carries a roster of every property they currently have on market, each stamped `Listed 28 Jul 2026`.

It is server-rendered into the same `ArgonautExchange` payload the auction-results pages use, under `AGENT_PROFILE_LISTINGS.agentMapBuyListings`.

Two things about the page are worth knowing, because both look like obstacles and neither is one:

- **The Sold / For sale dropdown fires no request.** Both channels are already in the HTML; switching it is pure client-side rendering.
- **"See more" reveals nothing new.** `buyListings` holds the first three rows, `agentMapBuyListings` holds all of them. Parsing the map variant gets the full roster from the initial response.

So one fetch dates *every* property that agent is advertising — 18 listings from a single page when this was verified on 2026-08-28. Combined with the per-run disk cache, the second and subsequent properties sharing an agent cost nothing at all. It is the cheapest date in the pipeline by a wide margin.

The date is REA's own record, so a relist resets it exactly like the portal counters this whole document is about. That is fine, and it is why it is ranked as documented anyway: **a reset only ever moves the date later**, so the value remains a lower bound on how long the property has been advertised — and a lower bound is what every source here is. Where an SOI predates it, the earliest-candidate rule means the SOI still wins.

What it unlocks is bigger than the date itself. Clock-reset detection needs *the portal's claim* to measure the evidence against, and REA published none — so resets only ever surfaced on Domain. REA's stated listing date closes that gap.

---

## How it resolves

Seven sources, ranked. The **earliest defensible date wins**, because every source is a lower bound on how long the property has been advertised. Trust only breaks ties and sets the label.

| Basis | Kind | Notes |
|---|---|---|
| `soi-document` | Documented | Upload timestamp on the Statement of Information |
| `archive-capture` | Documented | First archive.org capture of the listing URL |
| `agent-listed` | Documented | `Listed 28 Jul 2026` on the listing agent's profile page |
| `history-page` | Documented | A property-history page stated the first campaign date |
| `auction-inferred` | Inferred | Auction date minus campaign length. Can't be reset; needs an auction |
| `soi-median-period` | Inferred | SOI median period end, which must be ≤6 months old |
| `observed-floor` | Floor | First week this tool saw it. Unfakeable but a lower bound, shown with `+` |
| `current-listing` | Weakest | What the portal claims. The number that gets reset |

Rows dated from the first four carry a **Documented** tag. That distinction is the point — it tells you whether a figure is safe to quote at an agent.

### Cost

Dating runs cheapest-first. archive.org needs no page load; the listing page is only opened if the archive comes back empty. Results are **cached permanently**, because a campaign's start date never changes. So the cost is one lookup per address ever, not per week — in practice a handful of new addresses each run.

---

## The reset clock became the best signal in the tool

Once both clocks are known, comparing them is free.

When the current listing claims far less time than the documentary evidence requires, the report shows both figures — `14d listed / 116d real` — and tags the row **Clock reset · 102d hidden**.

That gap is not a data quality problem. Someone deliberately restarted the counter on a property that failed and is still sitting there. It's a confession, and it's now the strongest single indicator in the report.

The threshold is a 21-day gap, so ordinary reporting lag doesn't trip it. A test case confirms the honest path stays clean: when `dateListed` survives a pass-in, total and campaign both read 53 days and nothing is flagged.

---

## The category this opened up

With dating no longer dependent on an auction, a second view became possible: **Long on market**.

Currently listed, more than `staleAfterDays` (default 60) since first advertised, auction or not, longest first. Rows that got there without a single Saturday are tagged **never auctioned**.

One deliberate exclusion: anything whose age rests only on `current-listing` is kept out of this view entirely. `agent-listed` is deliberately *not* excluded — it is a lower bound rather than a live counter, so an old one is evidence of staleness rather than an artefact of it. Including it would rank by the resettable figure — surfacing fresh listings and burying genuinely stale ones. Exactly backwards.

---

## What's verified and what isn't

**Verified against real data.** The URL date extraction was tested against the four real agency CDN URLs above and passes all four, plus a date-path variant. More importantly it produces **no false positive** on `https://www.domain.com.au/13-fern-avenue-prahran-vic-3181-2019483746` — a long numeric listing ID is exactly the kind of thing a loose regex would misread as a timestamp. Implausible dates (before 2015, or in the future) are rejected rather than returned with false confidence.

**Verified by construction.** The ranking logic has seven test cases covering: an honest listing, a single reset, two failures with a reset after the second, a recorded history date beating inference, a floor-only case, a no-auction SOI case, and an archive capture beating a late auction inference.

**Not verified.** Whether the SOI link is reliably present in the DOM of a real Domain or REA listing page, and what it looks like when it is. The extractor looks for links matching statement-of-information patterns or any PDF, then prefers whichever names itself an SOI — but the selector logic is guessing at markup I haven't seen.

**Known soft spot.** `auctionCampaignDays: 28` is an assumption. A longer campaign makes the inferred start late, so `auction-inferred` figures understate rather than overstate. Wrong in the safer direction, but it means "116d real" should be read as "at least 116 days."

---

## How to read it in practice

- **Documented + Clock reset** — the strongest position. You know how long it's really been, you know the agent tried to hide it, and it's still for sale.
- **Documented, no reset** — a real number you can use in conversation.
- **Inferred** — treat as a floor. Fine for ranking, not for quoting.
- **Floor only** — the tool hasn't been running long enough for this address. It will improve on its own.
