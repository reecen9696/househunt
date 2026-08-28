# UI redesign — critical analysis and required changes

Reviewed 2026-08-28 against the live page (`python -m passedin serve`, 15
tracked properties, one scanned week) at 1280px and at a 390px phone
viewport. Everything below is grounded in what actually rendered, not in
what the code intends to render.

The reference point is realestate.com.au's listing card: photo, price,
address, four feature numbers, one line of dates. Nothing else. Every field
on our card has to justify itself against that bar.

---

## 1. What the page is for

One person, two jobs:

| Job | When | Device | Question being answered |
|---|---|---|---|
| **Tracker** — the shortlist | All week; at inspections; on the couch | Phone first | *Which of my properties needs action, and what's the edge the portal isn't telling me?* |
| **Leads** — the weekly scan | Sunday morning | Desktop | *Who failed at auction this week that I should look at?* |

The tracker is the product. The leads scan feeds it. Today the leads tab is
the default, the tracker is a second-class tab, and there is no path from a
lead to the tracker at all. That inversion is the single biggest structural
fault, and the rest of this document follows from fixing it.

---

## 2. Critical analysis — Tracker tab

### 2.1 The card says the same thing three times

For 493 Waverley Road the card currently shows:

```
[photo]  ✓ 1 days on market                    🔨 Sat, 19 Sept
$1,200,000 - $1,300,000
493 Waverley Road, Malvern East, Vic 3145
first advertised 2026-08-27  DOCUMENTED
🛏 3 🛁 2 🚗 4 ⛶ 759 m²  ·  House
Inspection tomorrow 10:00 am | 🔨 Sat 19 Sep at 10:30 am | Tracking since 2026-08-28
                                                              ✕
```

- **Time on market appears three times**: the photo chip (`1 days on
  market`), the dating line (`first advertised 2026-08-27`), and the footer
  (`Tracking since 2026-08-28`). All three derive from the same date.
- **The auction date appears twice**: photo badge and footer line.
- **`DOCUMENTED` is on 14 of 15 cards.** A tag that is almost always
  present carries no information. It is the default, not the exception —
  the tag should exist only for the *non*-default (`est.`).
- **`Tracking since …` tells the user nothing they can act on.** It is a
  provenance detail for the dating engine. It belongs in a tooltip/detail
  view, never on the card face.
- **`1 days on market` is both ungrammatical and not a signal.** Days on
  market is only interesting when it is large (≥ 30–45 d) or when the
  portal's counter disagrees with the evidence (clock reset). For a
  three-day-old listing it is visual noise occupying the most valuable
  pixels on the card (the photo overlay).

### 2.2 The photo overlay is a badge dump

Up to four pills sit on the photo: DOM chip, clock-reset, sale-method,
auction-flag. On narrow cards they wrap into two rows and cover a third of
the image. REA puts *nothing* on the photo.

- `Private sale` is the **absence** of an auction. It is not a status; it
  should not be a pill. The dates line already says whether there is an
  auction.
- The photo should carry **at most one** overlay, and only the single most
  decision-changing fact, in priority order:
  1. `Passed in 18 Jul` (confirmed) — red. This is the whole point of the tool.
  2. `Clock reset · 102 d hidden` — red.
  3. `Auction Sat · 2 days` — dark, only when ≤ 7 days.
  4. `Probable past auction` — amber.
  5. Nothing.

### 2.3 Stale text presented as current

The card renders captured strings verbatim, and several are relative:

- `Inspection tomorrow 10:00 am` was captured on the 28th. On the 29th it
  is wrong; on the 30th it is describing the past. Three cards (ids 9, 10,
  11) show `Inspection Sat 15 Aug …` — thirteen days gone.
- Card 10 (1a Tudor Street) had its auction on **15 Aug**. The card shows
  no auction badge (past dates are suppressed) and no result. The most
  important fact about that property — *what happened at the auction* —
  is invisible. It should say `Auctioned 15 Aug · result unknown` until the
  scan or a re-fetch answers it.

Rule: **relative words (`tomorrow`, `in 2 days`) are computed at render
time from structured dates, never stored.** Past inspections are dropped.
Past auctions without a result become an explicit prompt.

### 2.4 Price is raw scraped text

Actual values on the page right now:

```
$1,200,000 - $1,300,000
For Sale: $1,100,000 - $1,200,000
PRIVATE SALE: $980,000 - $1,078,000
Auction Saturday 19 September at 11am        ← in the PRICE slot
FOR SALE VIA PRIVATE APPOINTMENT
```

`price_low` / `price_high` are already parsed and stored. The card should
render **from the numbers** — `$1.1m – $1.2m` — and fall back to text only
when unparsed, with the text demoted to a secondary line (`Via private
appointment`, `Contact agent`). Never show an auction time as a price.

### 2.5 The user's own notes are invisible

The database holds real notes (`Auction tomorrow — check Monday whether it
passed in`, `Private sale. Check price, then offer 1.…`) and a `status`
column, and the server exposes `POST /api/tracked/update` for both. The
card renders **neither**. CSS for `.pcard select` and `input.tnote` exists
but nothing emits the elements. The single highest-value line on any card —
what *I* decided about it — is hidden.

### 2.6 Remove is one mis-tap from data loss

`✕` is a 15px bare button with no confirmation. It deletes the row and with
it the cached dating evidence and auction check. On a phone this will
happen. Removal belongs behind an overflow menu, and the default lifecycle
action should be **archive**, not delete.

### 2.7 No ordering, no grouping, no lifecycle

Fifteen cards in insertion order. Meanwhile the data contains:

- 1 auction **tomorrow** (29 Aug)
- 2 auctions on 12 Sep, 5 on 19 Sep
- 2 **confirmed pass-ins** (34 and 41 days sitting on a failed result)
- 2 possible past auctions
- 1 property at 317 days on market

None of that structures the page. A tracker is a to-do list; it must be
ordered by urgency.

### 2.8 Internal vocabulary leaks into the UI

`DOCUMENTED`, `INFERRED`, `floor`, `soi-document`, `agent-listed`,
`POSSIBLE_PASS_IN`. These are the dating engine's names. The user-facing
vocabulary needs at most: nothing (documented), `est.` (inferred), `≥`
(floor). The provenance detail (`Statement of Information published
2026-08-27`) goes in the tooltip/detail.

### 2.9 Smaller but real

- Address carries `, Vic 3145` on every card. Melbourne-only tool; drop
  state and postcode. Street on the first line, suburb secondary (as REA).
- ISO dates (`2026-08-27`) in prose. Use `27 Aug`.
- Emoji icons (🛏🛁🚗⛶) render differently per platform, are coloured, and
  are heavier than the numbers they label. Use monochrome inline SVG.
- Text sizes on the card: 18 / 14.5 / 13.5 / 12.5 / 12 / 11.5 / 10.5 px.
  Six sizes on one card; four of them below 13px. Mobile minimum is 13px
  for secondary text, 16px for inputs (below 16px iOS zooms on focus).
- Five semantic colours (accent brown, flag red, warn amber, good green,
  auction blue) plus agency brand colours. Reduce to: one accent, one alert
  (red = passed in / clock reset), neutrals for everything else.
- The onboarding paragraph (5 lines on mobile) is shown on every visit,
  and is now **factually wrong** — it says REA doesn't publish a listing
  date so time-on-market counts from tracking, but 14/15 cards are dated
  from SOI or the agent roster. This copy belongs in the empty state only.
- `title=` tooltips do not exist on touch devices. "Hide it in a tooltip"
  must mean a tappable disclosure on mobile (an `ⓘ` that expands a detail
  row, or a `<details>`), with `title` as the desktop convenience.
- Photo is a fixed 205px height; use `aspect-ratio: 4/3` so it scales.
- The no-photo placeholder is a 205px grey slab. Shrink it.
- The `.pcard .price` rule has to explicitly undo the leads-tab `.price`
  rule (`text-align`, `white-space`). Styles leak across tabs; scope them.

### 2.10 Mobile, specifically

At 390px the first property card starts **320px down the viewport** (38% of
the screen). Above it: title, "Week ending … generated … source" line, the
Run scan button, tabs, the five-line hint, the add form. None of the first
three belong on the tracker at all.

Also: touch targets (`✕`, stars, floor-plan link) are 15–20px; the sticky
filter bar on leads is 154px tall; the save toast is bottom-*right*, off
the thumb.

**Prerequisite:** `serve.py` binds `127.0.0.1`. A phone cannot reach it.
"Nice on mobile" is meaningless until the server can bind a LAN/Tailscale
address (`--host` flag). No auth, so LAN/VPN only — document that.

---

## 3. Critical analysis — Leads tab

- **A CLI dump is pasted into the page.** The `<pre>` run summary is 213px
  tall on mobile, monospace, overflows horizontally, and duplicates the
  canary warning that is *also* shown in the red banner directly above it.
  Replace with one line — `Scanned 28 Aug · 10 pages · 3 non-sales · ⚠ 1
  warning` — with the full text behind a disclosure.
- **Empty sections render as headings.** `NEW THIS WEEK 0 / none`, `STILL
  AVAILABLE 0 / none`, `STRETCH BUDGET 0 / none` — three empty headings
  before the first property. Hide empty sections.
- **The lead card breaks at phone width.** `grid-template-columns: auto 1fr
  auto` with a fixed 132px thumb and a `nowrap` price leaves ~100px for the
  address column; measured page width 428px on a 390px viewport
  (horizontal scroll). Needs a stacked layout under ~600px.
- **Links in the headline.** `rea listing ↗ verify result ↗` sit inline
  with the address in bold. Move to a footer row.
- **Two different card designs for the same thing.** A lead and a tracked
  property are both "a property"; they should share one card component
  with a compact/list variant. Different fonts, thumb sizes, action
  patterns (5-star + dismiss + note vs. ✕ only) for the same object is the
  main reason the app feels unfinished.
- **No "Track" action.** The lead has a REA URL; `POST /api/track` exists.
  One tap should move a lead into the tracker. This is the workflow the
  tool was built for and it is missing.
- **5-star rating** is five 15px targets and a five-point scale for a
  yes/maybe/no decision. Replace with the same status/note pattern as the
  tracker; "Track" is the yes, "Dismiss" is the no.
- **Run weekly scan** is the most prominent button on every page, for an
  action taken once a week. Demote to a secondary button inside the leads
  header with `last scan 5 days ago`.
- **Filter bar** is sticky and 154px tall on mobile; on desktop it's fine
  as a single row. Collapse behind a `Filter` button on mobile.
- Week-ending / generated / source line belongs to this tab, not the app
  header.

---

## 4. Show / hide decisions — the tracker card

The test: *does a house-hunter change what they do today because of this
field?*

| Field | Decision | Where |
|---|---|---|
| Photo | **Show** | Card top, 4:3 |
| Price (formatted from `price_low/high`) | **Show** — primary | 20px bold |
| Address (street, suburb) | **Show** | Under price; drop state/postcode |
| Beds / baths / cars / land | **Show** | One icon row |
| Property type | Show, muted, end of icon row | `· House` |
| Next inspection | **Show if future** — computed relative (`Sat 10:00 am`, `Tomorrow 10:00 am`) | Dates line |
| Auction date | **Show if future**; ≤7 d → photo pill | Dates line + pill |
| Past auction, no result | **Show as prompt** `Auctioned 15 Aug · result unknown` | Dates line, amber |
| Confirmed pass-in | **Show** — the #1 signal, red pill + `41 days since` | Photo pill + dates line |
| Probable / possible pass-in | Show, amber pill (one level only: "Possibly passed in") | Photo pill |
| Days on market | Show, **small & muted**; red only ≥ 45 d or clock reset | Meta line |
| Clock reset | **Show** — red pill `Clock reset · 102 d hidden` | Photo pill |
| `est.` / `≥` qualifier | Show only when not documented | Suffix on DOM |
| `DOCUMENTED` tag | **Hide** | — |
| `first advertised 2026-08-27` | **Hide** (tooltip: "Listed 27 Aug · Statement of Information") | Detail |
| `Tracking since …` | **Hide** (tooltip) | Detail |
| Dating basis / detail | **Hide** (tooltip) | Detail |
| `Private sale` pill | **Hide** — implied by no auction | — |
| Agent + agency | Show, small, muted — it's who you ring | Footer |
| Agency brand colour banner | **Hide** — decoration | — |
| My note | **Show** when present; tap to edit | Above footer |
| Status | **Show** — pill/select | Footer |
| Floor plan | Show as footer link | Footer |
| Open on REA | Photo + address are the link (as REA) | — |
| Re-fetch / Remove | Overflow `⋯` menu; remove confirms | Footer |

### Proposed card anatomy

```
┌──────────────────────────────────┐
│ [Passed in 18 Jul]               │  ← at most one pill, top-left
│                                  │
│            photo 4:3             │
│                                  │
├──────────────────────────────────┤
│ $1.1m – $1.21m                   │  20px / 700
│ 24 Florence Street, Prahran      │  15px
│ ⌂3  ⌂1  ⌂1  233 m²  · House      │  14px, svg icons
│ Inspection Sat 10:00 am          │  14px
│ Passed in 18 Jul · 41 days ago   │  14px, red text (this row only when relevant)
│ 73 days on market ⓘ              │  13px muted (red if ≥45 or reset)
│ “Check price, then offer 1.1”    │  14px, my note, italic
│ Watching ▾   Floor plan   Karen Chung · Jellis Craig   ⋯ │  footer, 13px
└──────────────────────────────────┘
```

Mobile is the same card, one column, full width, 16px side gutters.

---

## 5. Information architecture (target)

```
┌ sticky top bar (48px) ─────────────────────────────────────┐
│ Passed-In Finder      [ Tracker (15) | Leads (3 new) ]     │
└────────────────────────────────────────────────────────────┘

TRACKER (default tab)
  toolbar:  [+ Add listing]   Sort: Needs attention ▾   [Watching 13 · Archived 2]
  ── This week (2) ──        auction/inspection within 7 days
  ── Passed in (4) ──        confirmed / probable / possible past auction
  ── Watching (9) ──         everything else, by days on market desc
  ── Archived (0) ── ▸       collapsed; status ≠ active

LEADS
  header:  Week ending 23 Aug · scanned 28 Aug · ⚠ 1 warning ▸    [Run scan]
  filters: single row (desktop) / [Filter ▾] button (mobile)
  sections: only non-empty ones; same card component, list variant,
            with outcome pill + [Track] [Dismiss] [note]
```

"Needs attention" ordering = auction ≤7 d → confirmed pass-in → probable →
clock reset → inspection ≤2 d → days on market desc → added desc.

---

## 6. Required changes, prioritised

Everything renders from the template string in `passedin/report/html.py`
unless noted.

### P0 — structure and correctness

1. **Tracker is the default tab** (first in order, default on first load;
   keep the localStorage memory).
2. **Move week/generated/source line and Run scan into the leads tab.**
   App header becomes a 48px sticky bar with name + tabs.
3. **Rebuild the tracker card** to the anatomy in §4. Concretely:
   - render price from `price_low/high` with a short formatter
     (`$980k`, `$1.15m – $1.25m`); text fallback demoted; never show an
     auction string as price;
   - address without `, Vic NNNN`;
   - SVG feature icons; type muted at end of row;
   - one dates line, computed at render from `auction_date` (and a
     structured inspection date — see P0.6); past inspections dropped;
     past auction without result → `Auctioned 15 Aug · result unknown`;
   - one photo pill max, priority order from §2.2;
   - DOM as a muted meta line with `est.` / `≥` qualifier; `ⓘ` opens a
     detail row containing basis, detail, first-advertised, tracking-since,
     and days-claimed vs. real;
   - remove `DOCUMENTED` tag, `first advertised`, `Tracking since`,
     `Private sale` pill;
   - fix `1 days`.
4. **Surface status and notes** on the card via the existing
   `POST /api/tracked/update`. Status vocabulary: `active` (Watching),
   `inspected`, `offer`, `archived`. Note shown as a line when set, tap to
   edit inline.
5. **Grouping + sort** per §5. Archived group collapsed by default.
6. **Structured inspection time.** Add an `inspection_datetime` pattern in
   `tracker.py` / the extension (REA's `inspections[].startTime` sits next
   to `longLabel`) so "tomorrow" is computed, not stored. Until then, drop
   the inspection line when it was captured more than a day ago.
7. **Remove → overflow menu with confirm**; add `Archive` as the default
   lifecycle action; add `Re-fetch details` (re-POST the URL — already
   supported).
8. **"Track" button on lead cards** → `POST /api/track {url, fetch:true}`,
   then switch to the tracker. Lead card also gets a "Tracked ✓" state if
   its URL is already in the tracker.
9. **Leads on mobile**: stacked card layout under 600px (photo on top or
   80px thumb, price under address, actions on their own row). Kill the
   horizontal overflow.
10. **`serve --host`** option (default `127.0.0.1`) so the page can be
    opened from a phone on the LAN/Tailscale. README note: no auth.

### P1 — polish that changes the feel

11. **Run summary** → one-line status + disclosure; de-duplicate the canary
    warning; hide empty sections.
12. **Filters** → single row on desktop, `Filter` button on mobile; not
    sticky at phone width.
13. **Unify the card component** — one `propertyCard(item, variant)` used
    by both tabs; leads variant adds the outcome pill, weeks-unsold, last
    bid, and the `verify result` link in the footer.
14. **Replace 5-star + dismiss** on leads with Track / Dismiss / note.
15. **Type scale**: 20 / 15 / 14 / 13 px only. Inputs 16px. Touch targets
    ≥ 44px. `-webkit-text-size-adjust: 100%`.
16. **Colour**: one accent, one alert red, neutrals; agency colour dropped.
17. **Photo `aspect-ratio: 4/3`**, `object-fit: cover`, small placeholder.
18. **Toast** bottom-centre, above the safe-area inset.
19. **Empty states** carry the onboarding copy (extension / paste URL);
    remove the always-on paragraph; fix the stale "REA doesn't publish a
    date" claim.
20. **Add listing** as a `+ Add` button that reveals the input (mobile),
    inline input on desktop; show a skeleton card while `pending`.

### P2 — hygiene

21. Split the template into `static/index.html`, `app.css`, `app.js`
    served by `serve.py`; keep `report.html` as the file-mode fallback
    (embed at build). The 700-line Python string is why the two tabs
    leak styles into each other.
22. Scope CSS by tab/variant (`.tracker .card`, `.leads .card`).
23. `prefers-color-scheme: dark` via the existing `:root` tokens.
24. `<meta name="theme-color">` + `apple-mobile-web-app-capable` so it can
    be pinned to a phone home screen.

---

## 7. What is deliberately *not* proposed

- No map, no compare view, no charts. The card list is the product.
- No agency banner / agent photo (the REA reference has one; for us it is
  the vendor's marketing, not our signal).
- No new data sources. Every change above works on fields already in
  `/api/tracked` except the structured inspection time (P0.6).
- No change to the dating or auction-check engines — only to how much of
  their output reaches the card face.
