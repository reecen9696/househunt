"""Local HTML review page (§10).

Self-contained file: the dataset is embedded as JSON and all filtering,
sorting and re-cutting happens client-side — a review pass never needs a
re-run. Dismiss / rate / note actions POST back to the local server
(`passedin serve`); opened as a plain file they fall back to view-only.
"""
from __future__ import annotations

import json
from pathlib import Path

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Passed-In Finder</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #f6f5f2; --card: #ffffff; --ink: #1c1b18; --muted: #6f6a60;
    --line: #e3e0d8; --accent: #8a4b2d; --good: #2d6a4f; --warn: #b4531f;
    --flag: #a3320b; --soft: #efece5;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 80px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin-bottom: 10px; }
  .sub a { color: var(--accent); }
  .runbar { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
  .runbar button { font: inherit; font-size: 14px; font-weight: 600; padding: 6px 16px;
    border: none; border-radius: 8px; background: var(--accent); color: #fff; cursor: pointer; }
  .runbar button:disabled { opacity: .5; cursor: default; }
  .runbar #scan-status { font-size: 13px; color: var(--muted);
    font-family: ui-monospace, monospace; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; max-width: 640px; }
  pre.summary { background: var(--card); border: 1px solid var(--line);
    border-radius: 8px; padding: 12px 16px; font-size: 12.5px; overflow-x: auto; }
  pre.summary.problems { border-color: var(--flag); background: #fdf1ec; }
  .banner { border-radius: 8px; padding: 12px 16px; margin: 12px 0;
    font-size: 14px; line-height: 1.5; }
  .banner.error { background: #fbe4dd; border: 1.5px solid var(--flag);
    color: #7a2408; }
  .banner.warn { background: #fdf6e3; border: 1.5px solid #c9a227;
    color: #6b5511; }
  .banner strong { display: block; margin-bottom: 4px; }
  .banner ul { margin: 4px 0 0 18px; padding: 0; }
  .tabs { display: flex; gap: 4px; margin: 4px 0 16px; border-bottom: 2px solid var(--line); }
  .tabs button { font: inherit; font-size: 14px; font-weight: 600; padding: 8px 18px;
    border: none; background: none; color: var(--muted); cursor: pointer;
    border-bottom: 2.5px solid transparent; margin-bottom: -2px; }
  .tabs button.active { color: var(--ink); border-bottom-color: var(--accent); }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
    gap: 18px; }
  .pcard { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden; display: flex; flex-direction: column;
    box-shadow: 0 1px 4px rgba(0,0,0,.05); }
  .pcard a.photo { position: relative; display: block; }
  .pcard a.photo img { width: 100%; height: 205px; object-fit: cover; display: block;
    background: var(--soft); }
  .pcard a.photo .nophoto { width: 100%; height: 205px; display: flex; align-items: center;
    justify-content: center; color: var(--muted); background: var(--soft); }
  /* One row across the bottom of the photo: time on market at the left,
     auction status at the right. Flex rather than two absolute elements,
     so a long label pushes instead of overlapping. */
  .pcard .photobar { position: absolute; left: 10px; right: 10px; bottom: 10px;
    display: flex; gap: 6px; align-items: flex-end; justify-content: space-between;
    flex-wrap: wrap-reverse; }
  .pcard .chip { background: #fff; border-radius: 999px; padding: 3px 11px;
    font-size: 12px; font-weight: 600; box-shadow: 0 1px 4px rgba(0,0,0,.25); }
  .pcard .chip.long { color: var(--flag); }
  .pcard .chip.documented::before { content: "✓ "; color: var(--good); }
  .pcard .rightbadges { display: flex; gap: 6px; align-items: center;
    justify-content: flex-end; flex-wrap: wrap; margin-left: auto; }
  .pcard .reset { background: var(--flag); color: #fff; border-radius: 999px;
    padding: 3px 11px; font-size: 11.5px; font-weight: 700;
    box-shadow: 0 1px 4px rgba(0,0,0,.25); }
  .pcard .abadge { border-radius: 999px; padding: 3px 11px; font-size: 11.5px;
    font-weight: 700; box-shadow: 0 1px 4px rgba(0,0,0,.25); white-space: nowrap;
    cursor: help; }
  .pcard .abadge.confirmed { background: var(--flag); color: #fff; }
  .pcard .abadge.probable { background: #fdf6e3; color: #6b5511;
    border: 1px solid #c9a227; }
  .pcard .abadge.sold { background: #fff; color: var(--muted); }
  /* How it's being sold, and when. An auction inside the week is the one
     that changes what you do today, so it gets the loud treatment. */
  .pcard .abadge.auction { background: #eef4ff; color: #1b3f8b;
    border: 1px solid #9cb8ee; }
  .pcard .abadge.auction.soon { background: #1b3f8b; color: #fff;
    border-color: #1b3f8b; }
  .pcard .abadge.private { background: #fff; color: #4a4a4a;
    border: 1px solid #d5d5d5; }
  .pcard .dating { font-size: 12px; color: var(--muted); margin: 2px 0 6px; }
  .pcard .dating .real { color: var(--flag); font-weight: 700; }
  .pcard .dating .tag { display: inline-block; font-size: 10.5px;
    text-transform: uppercase; letter-spacing: .05em; padding: 1px 7px;
    border-radius: 999px; background: var(--soft); margin-left: 4px; }
  .pcard .dating .tag.documented { background: #dcefe4; color: var(--good); font-weight: 700; }
  .pcard .dating .tag.inferred { background: #f3e7d3; color: var(--warn); }
  .pcard .body { padding: 12px 14px 6px; }
  /* text-align/white-space are reset explicitly: the leads-tab `.price`
     rule right-aligns and nowraps, and would otherwise bleed in here. */
  .pcard .price { font-size: 18px; font-weight: 800; margin-bottom: 6px;
    text-align: left; white-space: normal; line-height: 1.25; }
  .pcard .price .muted { font-weight: 400; font-size: 13px; color: var(--muted); }
  .pcard .addrline a { color: inherit; text-decoration: none; font-size: 14.5px; }
  .pcard .addrline a:hover { text-decoration: underline; }
  .pcard .features { display: flex; gap: 12px; align-items: center; color: var(--ink);
    font-size: 13.5px; margin: 8px 0 2px; flex-wrap: wrap; }
  .pcard .features .sep { color: var(--muted); }
  .pcard .listedline { color: var(--muted); font-size: 12.5px; margin: 4px 0 8px; }
  .pcard .cardacts { display: flex; gap: 8px; align-items: center;
    padding: 8px 14px 12px; border-top: 1px solid var(--line); margin-top: auto; }
  .pcard select { font: inherit; font-size: 12px; border: 1px solid var(--line);
    border-radius: 6px; padding: 2px 5px; background: #fff; color: var(--ink); }
  .pcard input.tnote { flex: 1; font: inherit; font-size: 12.5px; border: 1px solid var(--line);
    border-radius: 6px; padding: 3px 8px; background: #fff; color: var(--ink); min-width: 60px; }
  .pcard a.planlink { font-size: 12px; color: var(--accent); white-space: nowrap; }
  .pcard button.rm { border: none; background: none; color: var(--muted);
    font-size: 15px; cursor: pointer; }
  .pcard button.rm:hover { color: var(--flag); }
  .addform { display: flex; gap: 8px; margin: 12px 0; }
  .addform input { flex: 1; font: inherit; font-size: 13px; padding: 6px 10px;
    border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--ink); }
  .addform button { font: inherit; font-size: 13px; font-weight: 600; padding: 6px 14px;
    border: none; border-radius: 6px; background: var(--accent); color: #fff; cursor: pointer; }
  .tracker-hint { color: var(--muted); font-size: 12.5px; margin: 10px 0; }
  /* Settings panel */
  .sform { background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 4px 20px 20px; max-width: 720px; }
  .sform .row { display: flex; gap: 16px; align-items: flex-start;
    padding: 14px 0; border-bottom: 1px solid var(--line); }
  .sform .row:last-of-type { border-bottom: none; }
  .sform label.name { width: 170px; flex: none; font-weight: 600;
    font-size: 13.5px; padding-top: 5px; }
  .sform .field { flex: 1; min-width: 0; }
  .sform .note { color: var(--muted); font-size: 12px; margin-top: 5px; }
  .sform input[type=text], .sform input[type=number] { font: inherit;
    font-size: 13.5px; padding: 6px 10px; border: 1px solid var(--line);
    border-radius: 6px; background: #fff; color: var(--ink); width: 200px; }
  .sform input.wide { width: 100%; }
  .sform .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .sform .chip2 { display: inline-flex; align-items: center; gap: 6px;
    background: var(--soft); border-radius: 999px; padding: 4px 6px 4px 12px;
    font-size: 13px; }
  .sform .chip2 button { border: none; background: none; cursor: pointer;
    color: var(--muted); font-size: 14px; line-height: 1; padding: 0 4px; }
  .sform .chip2 button:hover { color: var(--flag); }
  .sform .types { display: flex; flex-wrap: wrap; gap: 4px 18px; }
  .sform .types label { font-size: 13.5px; display: inline-flex; gap: 6px;
    align-items: center; }
  .sform .actions { display: flex; gap: 12px; align-items: center;
    padding-top: 18px; }
  .sform .actions button { font: inherit; font-size: 14px; font-weight: 600;
    padding: 7px 20px; border: none; border-radius: 8px;
    background: var(--accent); color: #fff; cursor: pointer; }
  .sform .actions button:disabled { opacity: .5; cursor: default; }
  .sform .smsg { font-size: 13px; color: var(--muted); }
  .sform .smsg.err { color: var(--flag); font-weight: 600; }
  .sform .smsg.ok { color: var(--good); font-weight: 600; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    background: var(--card); border: 1px solid var(--line); border-radius: 8px;
    padding: 10px 14px; margin: 16px 0; position: sticky; top: 8px; z-index: 5;
    box-shadow: 0 2px 10px rgba(0,0,0,.04); }
  .controls label { font-size: 12.5px; color: var(--muted); display: flex;
    gap: 6px; align-items: center; }
  .controls input, .controls select { font: inherit; font-size: 13px;
    padding: 3px 6px; border: 1px solid var(--line); border-radius: 6px;
    background: #fff; color: var(--ink); }
  .controls input[type="number"] { width: 110px; }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
    margin: 28px 0 10px; color: var(--muted); }
  h2 .count { color: var(--ink); }
  .card { background: var(--card); border: 1px solid var(--line);
    border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
    display: grid; grid-template-columns: auto 1fr auto; gap: 4px 16px; }
  .card .thumb { grid-row: 1 / span 2; width: 132px; height: 99px;
    object-fit: cover; border-radius: 6px; background: var(--soft); }
  .card .thumb.missing { display: flex; align-items: center; justify-content:
    center; color: var(--muted); font-size: 11px; }
  .card.low-confidence { border-style: dashed; border-color: var(--warn); }
  .card.dismissed { opacity: .45; }
  .addr { font-weight: 600; }
  .addr a { color: inherit; text-decoration: none; border-bottom: 1px solid var(--line); }
  .meta { color: var(--muted); font-size: 13px; }
  .price { text-align: right; font-weight: 600; white-space: nowrap; }
  .price .status { display: block; font-size: 11px; font-weight: 400;
    color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
  .price.unknown { color: var(--muted); font-style: italic; font-weight: 400; }
  .badges { grid-column: 2 / -1; display: flex; flex-wrap: wrap; gap: 6px;
    margin-top: 2px; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px;
    background: var(--soft); color: var(--ink); }
  .badge.outcome-NO_BID, .badge.outcome-PASSED_IN_VENDOR_BID { background: #fbe4d5; color: var(--flag); font-weight: 600; }
  .badge.outcome-PASSED_IN { background: #f3e7d3; color: var(--warn); font-weight: 600; }
  .badge.outcome-WITHDRAWN, .badge.outcome-UNREPORTED { background: #e8e4f0; }
  .badge.outcome-UNKNOWN { background: #fdd; color: var(--flag); font-weight: 700; }
  .badge.weeks { background: var(--accent); color: #fff; font-weight: 600; }
  .badge.changed { background: #dcefe4; color: var(--good); font-weight: 600; }
  .badge.fuzzy { background: #fdd; color: var(--flag); }
  .links a { font-size: 12px; margin-right: 10px; color: var(--accent); }
  .useracts { grid-column: 2 / -1; display: flex; gap: 8px; align-items: center;
    margin-top: 6px; }
  .card .thumb, .card .thumb-wrap { grid-row: 1 / span 3; }
  .useracts button { font-size: 12px; border: 1px solid var(--line);
    background: #fff; color: var(--ink); border-radius: 6px; padding: 2px 10px; cursor: pointer; }
  .useracts button.active { background: var(--ink); color: #fff; }
  .useracts input.note { flex: 1; font-size: 12.5px; border: 1px solid var(--line);
    border-radius: 6px; padding: 3px 8px; background: #fff; color: var(--ink); }
  .stars button { border: none; background: none; cursor: pointer; font-size: 15px;
    padding: 0 1px; color: #c9c4b8; }
  .stars button.on { color: var(--warn); }
  .empty { color: var(--muted); font-style: italic; padding: 6px 2px; }
  .note-hint { color: var(--muted); font-size: 11.5px; }
  #savebanner { position: fixed; bottom: 14px; right: 14px; background: var(--flag);
    color: #fff; padding: 8px 14px; border-radius: 8px; font-size: 13px;
    display: none; max-width: 380px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Passed-In Property Finder</h1>
  <div class="sub">Week ending <strong id="week"></strong> · generated <span id="gen"></span>
    · <a href="https://www.realestate.com.au/auction-results/vic" target="_blank" rel="noopener">source: REA auction results ↗</a></div>
  <div class="runbar">
    <button id="run-scan">▶ Run weekly scan</button>
    <span id="scan-status"></span>
  </div>
  <div class="tabs">
    <button class="active" data-tab="leads">Passed-in leads</button>
    <button data-tab="tracker">Property tracker</button>
    <button data-tab="settings">Settings</button>
  </div>
  <div id="tab-settings" hidden>
    <div class="tracker-hint">
      What the weekly scan looks for. Saved straight to <code id="cfg-path">config.yaml</code>;
      the next scan picks it up. Only these search criteria are editable here —
      selectors, JSON paths and rate limits stay in the file.
    </div>
    <div id="settings-form" class="sform"></div>
  </div>
  <div id="tab-tracker" hidden>
    <div class="tracker-hint">
      Track any listing: click the <strong>Passed-In Property Tracker</strong>
      Chrome extension while viewing it on realestate.com.au, or paste a
      listing URL below — details are fetched from the listing automatically.
      REA doesn't publish a listing date, so time on market is counted from
      the day you started tracking unless a source provides one.
    </div>
    <div class="addform">
      <input id="add-url" placeholder="https://www.realestate.com.au/property-…">
      <button id="add-btn">Add</button>
      <span id="add-status" class="tracker-hint" style="margin:0;white-space:nowrap"></span>
    </div>
    <div id="tracker-table"></div>
  </div>
  <div id="tab-leads">
  <div id="banners"></div>
  <pre class="summary" id="summary"></pre>
  <div class="controls">
    <label>Suburb <select id="f-suburb"><option value="">all</option></select></label>
    <label>Max price <input type="number" id="f-price" step="25000" placeholder="none"></label>
    <label>Min beds <input type="number" id="f-beds" min="0" max="6" style="width:60px"></label>
    <label>Outcome <select id="f-outcome"><option value="">all</option></select></label>
    <label><input type="checkbox" id="f-dismissed"> show dismissed</label>
    <label>Sort <select id="f-sort">
      <option value="rank">opportunity rank</option>
      <option value="price">price ↑</option>
      <option value="weeks">weeks unsold ↓</option>
      <option value="suburb">suburb A–Z</option>
    </select></label>
  </div>
  <div id="sections"></div>
  </div><!-- /tab-leads -->
</div>
<div id="savebanner"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const VIEW = JSON.parse(document.getElementById('data').textContent);
const SECTIONS = [
  ["new_this_week", "New this week"],
  ["still_available", "Still available"],
  ["stretch", "Stretch budget"],
  ["no_price", "No price data"],
  ["disappeared", "Disappeared (watch for relist)"],
  ["recently_sold", "Recently sold (market read)"],
];
document.getElementById('week').textContent = VIEW.week_ending;
document.getElementById('gen').textContent = VIEW.generated_at;
const sumEl = document.getElementById('summary');
sumEl.textContent = VIEW.summary_text;
if ((VIEW.summary_text || '').includes('CANARY')) sumEl.classList.add('problems');

const fmt = n => n == null ? null : '$' + Number(n).toLocaleString();
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

// --- error / empty-state banners -------------------------------------------
(function renderBanners() {
  const root = document.getElementById('banners');
  const problems = VIEW.problems || [];
  const quota = problems.filter(p => /quota|limit|credit/i.test(p));
  if (quota.length) {
    root.insertAdjacentHTML('beforeend', `<div class="banner error">
      <strong>⛔ Out of scrape.do credits — this week's data is incomplete.</strong>
      Fetching stopped part-way through. Top up or wait for the plan's monthly
      reset, then hit “Run weekly scan” again (already-fetched pages are cached
      and free).</div>`);
  }
  const others = problems.filter(p => !quota.includes(p));
  if (others.length) {
    root.insertAdjacentHTML('beforeend', `<div class="banner error">
      <strong>⚠ The last scan reported problems — treat this list as suspect:</strong>
      <ul>${others.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>`);
  }
  const totalItems = SECTIONS.reduce((n, [k]) => n + (VIEW.sections[k] || []).length, 0);
  if (totalItems === 0) {
    root.insertAdjacentHTML('beforeend', `<div class="banner warn">
      <strong>No properties to display for week ending ${esc(VIEW.week_ending)}.</strong>
      Likely causes: these suburbs haven't been scanned yet (hit “Run weekly
      scan”), the scan ran out of credits part-way (see above), no auctions in
      the configured suburbs this week, or the filters in config.yaml exclude
      everything that was found${VIEW.excluded_by_filters ? ` (${VIEW.excluded_by_filters} results were filtered out)` : ''}.</div>`);
  }
})();
const state = { suburb: '', price: null, beds: null, outcome: '', dismissed: false, sort: 'rank' };

// populate filter dropdowns from the data
const suburbs = new Set(), outcomes = new Set();
for (const [key] of SECTIONS) for (const it of VIEW.sections[key] || []) {
  if (it.suburb) suburbs.add(it.suburb);
  if (it.outcome) outcomes.add(it.outcome);
}
for (const s of [...suburbs].sort()) document.getElementById('f-suburb')
  .insertAdjacentHTML('beforeend', `<option>${s}</option>`);
for (const o of [...outcomes].sort()) document.getElementById('f-outcome')
  .insertAdjacentHTML('beforeend', `<option>${o}</option>`);

function passes(it) {
  if (!state.dismissed && it.dismissed) return false;
  if (state.suburb && it.suburb !== state.suburb) return false;
  if (state.outcome && it.outcome !== state.outcome) return false;
  if (state.beds != null && it.bedrooms != null && it.bedrooms < state.beds) return false;
  if (state.price != null && it.price_low != null && it.price_low > state.price) return false;
  return true;
}
function cmp(a, b) {
  switch (state.sort) {
    case 'price': return (a.price_low ?? 1e12) - (b.price_low ?? 1e12);
    case 'weeks': return (b.weeks_unsold || 0) - (a.weeks_unsold || 0);
    case 'suburb': return (a.suburb || '').localeCompare(b.suburb || '');
    default: return 0; // server order = opportunity rank
  }
}
function priceCell(it) {
  if (it.outcome && ['SOLD','SOLD_PRIOR','SOLD_AFTER'].includes(it.outcome))
    return `<div class="price">${fmt(it.sold_price) || 'price withheld'}<span class="status">sold</span></div>`;
  if (it.price_low == null)
    return `<div class="price unknown">no price signal<span class="status">UNKNOWN</span></div>`;
  const range = it.price_low === it.price_high ? fmt(it.price_low)
    : `${fmt(it.price_low)} – ${fmt(it.price_high)}`;
  return `<div class="price">${range}<span class="status">${it.price_status}</span></div>`;
}
function badges(it, section) {
  const b = [];
  b.push(`<span class="badge outcome-${it.outcome}">${(it.outcome || '').replaceAll('_',' ')}</span>`);
  if (it.weeks_unsold > 1) b.push(`<span class="badge weeks">${it.weeks_unsold} wks unsold</span>`);
  if (it.price_changed) b.push(`<span class="badge changed">price changed ${fmt(it.prev_price_low)} → ${fmt(it.price_low)}</span>`);
  if (it.merge_confidence === 'LOW') b.push(`<span class="badge fuzzy">fuzzy merge — verify</span>`);
  if (it.highest_bid) b.push(`<span class="badge">last bid ${fmt(it.highest_bid)}</span>`);
  if (it.vendor_bid) b.push(`<span class="badge">vendor bid ${fmt(it.vendor_bid)}</span>`);
  if (section === 'disappeared') b.push(`<span class="badge">last seen ${it.disappeared_since}</span>`);
  return `<div class="badges">${b.join('')}</div>`;
}
function links(it) {
  const urls = it.source_urls || {};
  const parts = Object.entries(urls)
    .map(([s, u]) => u ? `<a href="${u}" target="_blank" rel="noopener">${s} listing ↗</a>` : '');
  if (it.results_url)
    parts.push(`<a href="${it.results_url}" target="_blank" rel="noopener" title="the auction-results page this row was scraped from">verify result ↗</a>`);
  return `<span class="links">` + parts.join('') + `</span>`;
}
function meta(it) {
  const bits = [];
  if (it.property_type) bits.push(it.property_type);
  if (it.bedrooms != null) bits.push(`${it.bedrooms} bed`);
  if (it.bathrooms != null) bits.push(`${it.bathrooms} bath`);
  if (it.car_spaces != null) bits.push(`${it.car_spaces} car`);
  if (it.land_size_sqm) bits.push(`${it.land_size_sqm} m²`);
  if (it.agency_name) bits.push(it.agency_name);
  if (it.agent_name) bits.push(it.agent_name);
  return bits.join(' · ');
}
function stars(it) {
  let html = '<span class="stars">';
  for (let i = 1; i <= 5; i++)
    html += `<button data-star="${i}" class="${(it.user_rating || 0) >= i ? 'on' : ''}">★</button>`;
  return html + '</span>';
}
function card(it, section) {
  const low = it.merge_confidence === 'LOW' || it.outcome === 'UNKNOWN'
    || (it.price_low == null && section !== 'recently_sold');
  const listingUrl = Object.values(it.source_urls || {}).find(Boolean);
  const img = it.image_url
    ? `<a class="thumb-wrap" href="${listingUrl || '#'}" target="_blank" rel="noopener"><img class="thumb" loading="lazy" src="${it.image_url}" alt=""></a>`
    : `<div class="thumb missing">no photo</div>`;
  return `<div class="card ${low ? 'low-confidence' : ''} ${it.dismissed ? 'dismissed' : ''}"
              data-pid="${it.property_id}">
    ${img}
    <div>
      <div class="addr">${it.address_raw}, ${it.suburb} ${it.postcode || ''} ${links(it)}</div>
      <div class="meta">${meta(it)}</div>
    </div>
    ${priceCell(it)}
    ${badges(it, section)}
    <div class="useracts">
      <button class="dismiss ${it.dismissed ? 'active' : ''}">${it.dismissed ? 'undismiss' : 'dismiss'}</button>
      ${stars(it)}
      <input class="note" placeholder="notes…" value="${(it.user_notes || '').replace(/"/g, '&quot;')}">
    </div>
  </div>`;
}
function render() {
  const root = document.getElementById('sections');
  root.innerHTML = '';
  for (const [key, label] of SECTIONS) {
    const items = (VIEW.sections[key] || []).filter(passes);
    if (state.sort !== 'rank') items.sort(cmp);
    const total = (VIEW.sections[key] || []).length;
    root.insertAdjacentHTML('beforeend',
      `<h2>${label} <span class="count">${items.length}</span>${items.length !== total ? ` <span class="note-hint">of ${total}</span>` : ''}</h2>`);
    if (!items.length) { root.insertAdjacentHTML('beforeend', '<div class="empty">none</div>'); continue; }
    for (const it of items) root.insertAdjacentHTML('beforeend', card(it, key));
  }
}
function findItem(pid) {
  for (const [key] of SECTIONS)
    for (const it of VIEW.sections[key] || [])
      if (it.property_id === pid) return it;
  return null;
}
let warned = false;
async function persist(pid, fields) {
  try {
    const resp = await fetch('/api/user', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ property_id: pid, ...fields }),
    });
    if (!resp.ok) throw new Error(resp.status);
  } catch (e) {
    if (!warned) {
      warned = true;
      const b = document.getElementById('savebanner');
      b.textContent = 'Not saved — open this page via `python -m passedin serve` to persist dismissals, ratings and notes.';
      b.style.display = 'block';
      setTimeout(() => (b.style.display = 'none'), 8000);
    }
  }
}
document.getElementById('sections').addEventListener('click', e => {
  const cardEl = e.target.closest('.card');
  if (!cardEl) return;
  const it = findItem(cardEl.dataset.pid);
  if (!it) return;
  if (e.target.classList.contains('dismiss')) {
    it.dismissed = !it.dismissed;
    persist(it.property_id, { dismissed: it.dismissed });
    render();
  } else if (e.target.dataset.star) {
    it.user_rating = Number(e.target.dataset.star);
    persist(it.property_id, { rating: it.user_rating });
    render();
  }
});
document.getElementById('sections').addEventListener('change', e => {
  if (!e.target.classList.contains('note')) return;
  const cardEl = e.target.closest('.card');
  const it = findItem(cardEl.dataset.pid);
  it.user_notes = e.target.value;
  persist(it.property_id, { notes: it.user_notes });
});
// --- tabs + property tracker ------------------------------------------------
function showTab(name) {
  document.querySelectorAll('.tabs button').forEach(
    b => b.classList.toggle('active', b.dataset.tab === name));
  for (const t of ['leads', 'tracker', 'settings'])
    document.getElementById('tab-' + t).hidden = name !== t;
  try { localStorage.setItem('passedin.tab', name); } catch (e) {}
  if (name === 'tracker') loadTracker();
  if (name === 'settings') loadSettings();
}
document.querySelectorAll('.tabs button').forEach(
  btn => btn.addEventListener('click', () => showTab(btn.dataset.tab)));
// Stay on the tab you were reading across a reload — otherwise adding a
// property then refreshing drops you back on the leads tab, which looks
// exactly like the tracked property having vanished.
try {
  const saved = localStorage.getItem('passedin.tab');
  if (saved === 'tracker' || saved === 'settings') showTab(saved);
} catch (e) {}

function daysOn(t) {
  const start = t.date_listed || t.added_date;
  if (!start) return null;
  return Math.max(0, Math.round((Date.now() - new Date(start)) / 86400000));
}
// "Has it already failed at auction?" — a confirmed pass-in is a record;
// a probable one is an inference. The two are never merged, because the
// agent you're about to ring knows which is true.
// How this one is being sold right now, as opposed to auctionFlag() which
// reports what already happened to it. A scheduled auction is the deadline
// you're working to; "private sale" is the absence of one, and REA states it
// explicitly rather than leaving it unknown, so it's worth showing.
function saleBadge(t) {
  if (t.auction_date) {
    const d = new Date(t.auction_date + 'T00:00:00');
    const days = Math.round((d - new Date(new Date().toDateString())) / 86400000);
    // A past auction is the pass-in story, and auctionFlag() tells it.
    if (days < 0) return '';
    const when = d.toLocaleDateString('en-AU',
      { weekday: 'short', day: 'numeric', month: 'short' });
    const rel = days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`;
    const label = t.auction_text || when;
    return `<span class="abadge auction ${days <= 7 ? 'soon' : ''}"
      title="auction ${rel} — ${esc(label)}">🔨 ${esc(when)}${days <= 7 ? ` · ${rel}` : ''}</span>`;
  }
  if (t.sale_method === 'private')
    return `<span class="abadge private" title="no auction scheduled — REA states this listing is a private sale">Private sale</span>`;
  return '';
}
function auctionFlag(t) {
  const a = t.auction;
  if (!a || !a.state || a.state === 'NORMAL') return '';
  // The full reasoning lives in the tooltip — the badge itself has to read
  // at a glance next to the days-on-market chip.
  const why = [...(a.reasons || []),
               a.candidates && a.candidates.length
                 ? `Candidate auction Saturdays: ${a.candidates.join(', ')}.` : '']
              .filter(Boolean).join(' ');
  const day = a.auction_day
    ? new Date(a.auction_day + 'T00:00:00').toLocaleDateString('en-AU',
        { day: 'numeric', month: 'short' }) : '';
  if (a.state === 'CONFIRMED_PASS_IN')
    return `<span class="abadge confirmed" title="${esc(why)}">📞 Passed in ${esc(day)}</span>`;
  if (a.state === 'CONFIRMED_SOLD')
    return `<span class="abadge sold" title="${esc(why)}">Sold at auction ${esc(day)}</span>`;
  if (a.state === 'PROBABLE_PASS_IN')
    return `<span class="abadge probable" title="${esc(why)}">Probable past auction</span>`;
  if (a.state === 'POSSIBLE_PASS_IN')
    return `<span class="abadge probable" title="${esc(why)}">Possible past auction</span>`;
  return '';
}

function trackerCard(t) {
  const days = daysOn(t);
  const feats = [];
  if (t.bedrooms != null) feats.push(`🛏 ${t.bedrooms}`);
  if (t.bathrooms != null) feats.push(`🛁 ${t.bathrooms}`);
  if (t.car_spaces != null) feats.push(`🚗 ${t.car_spaces}`);
  if (t.land_size_sqm) feats.push(`⛶ ${Math.round(t.land_size_sqm)} m²`);
  if (t.property_type) feats.push(`<span class="sep">·</span> ${esc(t.property_type)}`);
  const photoInner = t.image_url
    ? `<img loading="lazy" src="${t.image_url}" alt="">`
    : `<div class="nophoto">no photo</div>`;
  // Days on market is reconstructed, not read — the portals reset their own
  // counter on relist. `floor_only` means we have no evidence beyond the
  // week we first saw it, so the figure is shown as a "+" lower bound.
  const dom = t.days_on_market;
  // Total time on market, and — where an auction actually happened — how
  // long the vendor has been sitting on that failed result since.
  const since = t.days_since_auction;
  const base = dom == null
    ? (days === 0 ? 'tracking from today' : `tracked ${days} days`)
    : (t.floor_only
        ? (dom === 0 ? 'tracking from today' : `${dom}+ days tracked`)
        : `${dom} days on market`);
  const chipLabel = since == null ? base : `${base} · ${since} since auction`;
  const chipTitle = t.campaign_detail
    ? `${t.campaign_basis}: ${t.campaign_detail}`
    : 'listing date not published by the source — counted from when you added it';
  const chip = (dom == null && days == null) ? '' :
    `<span class="chip ${(dom ?? days) > 45 ? 'long' : ''} ${t.documented ? 'documented' : ''}" title="${esc(chipTitle)}">${chipLabel}</span>`;
  // A gap between the portal's counter and the documentary evidence isn't a
  // data-quality problem — someone restarted the clock on a property that
  // failed and is still sitting there.
  const resetBadge = t.clock_reset
    ? `<span class="reset" title="the portal's counter was restarted">Clock reset · ${t.hidden_days}d hidden</span>` : '';
  const rightBadges = `<div class="rightbadges">${resetBadge}${saleBadge(t)}${auctionFlag(t)}</div>`;
  const datingLine = dom == null ? '' : `<div class="dating">
      ${t.clock_reset ? `${t.days_claimed}d listed / <span class="real">${dom}d real</span>` : `first advertised ${esc(t.campaign_start || '')}`}
      <span class="tag ${t.documented ? 'documented' : (t.floor_only ? '' : 'inferred')}">${t.documented ? 'documented' : (t.floor_only ? 'floor' : 'inferred')}</span>
    </div>`;
  const pending = t.address == null;
  return `<div class="pcard" data-tid="${t.tracked_id}">
    <a class="photo" href="${t.url}" target="_blank" rel="noopener" title="open on realestate.com.au">
      ${photoInner}<div class="photobar">${chip}${rightBadges}</div>
    </a>
    <div class="body">
      <div class="price">${t.price_text ? esc(t.price_text)
        : (pending ? '<span class="muted">fetching details…</span>'
                   : '<span class="muted">no price guide</span>')}</div>
      <div class="addrline"><a href="${t.url}" target="_blank" rel="noopener">${esc(t.address || t.url.replace(/^https?:\/\/[^/]+/, ''))}</a></div>
      ${datingLine}
      <div class="features">${feats.join(' ')}</div>
      <div class="listedline">
        ${[t.inspection_text ? esc(t.inspection_text) : null,
           t.auction_text ? `🔨 ${esc(t.auction_text)}` : null,
           t.date_listed ? `Listed ${esc(t.date_listed)}` : `Tracking since ${esc(t.added_date)}`
          ].filter(Boolean).join(' <span class="sep">|</span> ')}
      </div>
    </div>
    <div class="cardacts">
      ${t.floorplan_url ? `<a class="planlink" href="${t.floorplan_url}" target="_blank" rel="noopener">floor plan ↗</a>` : ''}
      <button class="rm" title="remove">✕</button>
    </div>
  </div>`;
}
// --- settings: what the weekly scan looks for -------------------------------
// Edits config.yaml through /api/settings. Only the search criteria are
// exposed; the server refuses anything outside its whitelist, so a bug here
// can't reach the selectors or rate limits.
let SETTINGS = null;

function chipRow(list, kind) {
  return list.map((v, i) =>
    `<span class="chip2">${esc(v)}<button data-kind="${kind}" data-i="${i}"
      title="remove">&times;</button></span>`).join('');
}

function renderSettings() {
  const v = SETTINGS.values, opts = SETTINGS.options.property_types;
  const root = document.getElementById('settings-form');
  root.innerHTML = `
    <div class="row"><label class="name">Suburbs</label><div class="field">
      <div class="chips" id="s-suburbs">${chipRow(v.suburbs, 'suburbs')}
        <input type="text" id="s-suburb-add" placeholder="add suburb + Enter"
               style="width:170px">
      </div>
      <div class="note">Names exactly as realestate.com.au spells them. Only
        these suburbs are fetched.</div>
    </div></div>

    <div class="row"><label class="name">Price ceiling</label><div class="field">
      <input type="number" id="s-price_ceiling" step="25000" value="${v.price_ceiling ?? ''}">
      <div class="note">Compared against the <em>lower</em> bound of a quoted
        range — vendors quote low.</div>
    </div></div>

    <div class="row"><label class="name">Stretch ceiling</label><div class="field">
      <input type="number" id="s-stretch_ceiling" step="25000" value="${v.stretch_ceiling ?? ''}"
             placeholder="none">
      <div class="note">Optional. Results between the two ceilings appear in
        their own section instead of being dropped.</div>
    </div></div>

    <div class="row"><label class="name">Min bedrooms</label><div class="field">
      <input type="number" id="s-min_bedrooms" min="0" max="10" value="${v.min_bedrooms ?? ''}"
             style="width:80px">
    </div></div>

    <div class="row"><label class="name">Property types</label><div class="field">
      <div class="types">${opts.map(t => `<label><input type="checkbox"
        class="s-type" value="${t}" ${v.property_types.includes(t) ? 'checked' : ''}>
        ${t}</label>`).join('')}</div>
      <div class="note">None ticked means every type. Rows with an unknown type
        are always kept rather than silently dropped.</div>
    </div></div>

    <div class="row"><label class="name">Min land size</label><div class="field">
      <input type="number" id="s-min_land_size_sqm" value="${v.min_land_size_sqm ?? ''}"
             placeholder="any" style="width:110px"> m&sup2;
      <div class="note">Only applied where a size is known — most auction rows
        carry none, and those are always included.</div>
    </div></div>

    <div class="row"><label class="name">Exclude streets</label><div class="field">
      <input type="text" class="wide" id="s-exclude_streets"
             value="${esc(v.exclude_streets.join(', '))}"
             placeholder="Bell St, Sydney Rd">
      <div class="note">Comma separated. Substring match on the street, for main
        roads and train lines.</div>
    </div></div>

    <div class="row"><label class="name">Exclude agencies</label><div class="field">
      <input type="text" class="wide" id="s-exclude_agencies"
             value="${esc(v.exclude_agencies.join(', '))}" placeholder="none">
      <div class="note">Comma separated.</div>
    </div></div>

    <div class="actions">
      <button id="s-save">Save</button>
      <span class="smsg" id="s-msg"></span>
    </div>`;

  document.getElementById('cfg-path').textContent = SETTINGS.config_path;
  root.querySelectorAll('.chip2 button').forEach(b =>
    b.addEventListener('click', () => {
      SETTINGS.values[b.dataset.kind].splice(+b.dataset.i, 1);
      renderSettings();
    }));
  const add = document.getElementById('s-suburb-add');
  add.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const name = add.value.trim();
    if (name && !SETTINGS.values.suburbs.includes(name)) {
      SETTINGS.values.suburbs.push(name);
      renderSettings();
      document.getElementById('s-suburb-add').focus();
    }
  });
  document.getElementById('s-save').addEventListener('click', saveSettings);
}

function num(id) {
  const el = document.getElementById(id);
  return el.value.trim() === '' ? null : el.value.trim();
}

async function saveSettings() {
  const btn = document.getElementById('s-save');
  const msg = document.getElementById('s-msg');
  const payload = {
    suburbs: SETTINGS.values.suburbs,
    price_ceiling: num('s-price_ceiling'),
    stretch_ceiling: num('s-stretch_ceiling'),
    min_bedrooms: num('s-min_bedrooms'),
    min_land_size_sqm: num('s-min_land_size_sqm'),
    exclude_streets: document.getElementById('s-exclude_streets').value,
    exclude_agencies: document.getElementById('s-exclude_agencies').value,
    property_types: [...document.querySelectorAll('.s-type:checked')].map(c => c.value),
  };
  btn.disabled = true;
  msg.className = 'smsg'; msg.textContent = 'saving…';
  try {
    const res = await fetch('/api/settings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'save failed');
    SETTINGS = data;
    renderSettings();
    const m = document.getElementById('s-msg');
    m.className = 'smsg ok';
    m.textContent = 'Saved — the next scan uses these.';
  } catch (e) {
    msg.className = 'smsg err';
    msg.textContent = String(e.message || e);
  } finally {
    const b = document.getElementById('s-save');
    if (b) b.disabled = false;
  }
}

async function loadSettings() {
  const root = document.getElementById('settings-form');
  try {
    SETTINGS = await fetch('/api/settings').then(r => r.json());
    if (SETTINGS.error) throw new Error(SETTINGS.error);
  } catch (e) {
    root.innerHTML = `<div class="banner warn"><strong>Settings need the local
      server.</strong> Open this page via <code>python -m passedin serve</code>
      to change what the scan looks for.</div>`;
    return;
  }
  renderSettings();
}

let trackerPoll = null;
async function loadTracker() {
  const root = document.getElementById('tracker-table');
  let data;
  try {
    data = await fetch('/api/tracked').then(r => r.json());
  } catch (e) {
    root.innerHTML = `<div class="banner warn"><strong>Tracker needs the local server.</strong>
      Open this page via <code>python -m passedin serve</code> to add and view tracked properties.</div>`;
    return;
  }
  const rows = data.rows || [];
  const errors = Object.entries(data.errors || {});
  let html = '';
  for (const [url, msg] of errors) {
    html += `<div class="banner error"><strong>Couldn't fetch listing details.</strong>
      ${esc(url)}<br>${esc(msg)}<br>The property is still tracked — press Add again
      on the same URL to retry.</div>`;
  }
  if (!rows.length) {
    html += `<div class="banner warn">No tracked properties yet — use the
      Chrome extension on a listing page, or paste a URL above.</div>`;
  } else {
    // Arrow, not a bare reference: .map() would pass the index as the 2nd arg.
    html += `<div class="cards">${rows.map(t => trackerCard(t)).join('')}</div>`;
  }
  root.innerHTML = html;

  // Details arrive asynchronously (a proxied listing fetch takes a while),
  // so keep refreshing while any are still in flight.
  clearTimeout(trackerPoll);
  if ((data.pending || []).length) {
    setStatus(`fetching details for ${data.pending.length} listing(s)…`);
    trackerPoll = setTimeout(loadTracker, 4000);
  } else {
    setStatus('');
  }
}
function setStatus(msg) {
  const el = document.getElementById('add-status');
  if (el) el.textContent = msg;
}
document.getElementById('tracker-table').addEventListener('click', async e => {
  if (!e.target.classList.contains('rm')) return;
  const card = e.target.closest('.pcard[data-tid]');
  await fetch('/api/tracked/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tracked_id: Number(card.dataset.tid) }) });
  loadTracker();
});
document.getElementById('add-btn').addEventListener('click', async () => {
  const input = document.getElementById('add-url');
  const url = input.value.trim();
  if (!url) return;
  const btn = document.getElementById('add-btn');
  btn.disabled = true;
  try {
    // The server saves the row immediately and pulls price, beds/baths/cars,
    // photos and floor plan in the background — so this returns at once and
    // the card fills in as the details land.
    const resp = await fetch('/api/track', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, fetch: true }) });
    if (resp.ok) {
      input.value = '';
      loadTracker();
    } else {
      alertBanner('Could not add that URL.');
    }
  } catch (e) {
    alertBanner('Could not reach the local server.');
  } finally {
    btn.disabled = false;
  }
});
document.getElementById('add-url').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('add-btn').click();
});
function alertBanner(msg) {
  const b = document.getElementById('savebanner');
  b.textContent = msg;
  b.style.display = 'block';
  setTimeout(() => (b.style.display = 'none'), 8000);
}

// --- run-scan button (works when the page is served by `passedin serve`) ---
const runBtn = document.getElementById('run-scan');
const scanStatus = document.getElementById('scan-status');
async function pollScan() {
  try {
    const s = await fetch('/api/scan/status').then(r => r.json());
    if (s.running) {
      runBtn.disabled = true;
      scanStatus.textContent = s.tail || 'scanning…';
      setTimeout(pollScan, 3000);
    } else if (s.exit_code !== null && s.exit_code !== undefined) {
      scanStatus.textContent = 'Scan finished — reloading report…';
      setTimeout(() => location.reload(), 1500);
    } else {
      runBtn.disabled = false;
    }
  } catch (e) {
    runBtn.disabled = false;
  }
}
runBtn.addEventListener('click', async () => {
  runBtn.disabled = true;
  scanStatus.textContent = 'starting…';
  try {
    const resp = await fetch('/api/scan', { method: 'POST' });
    if (!resp.ok) throw new Error(resp.status);
    pollScan();
  } catch (e) {
    runBtn.disabled = false;
    scanStatus.textContent = 'Could not start — open this page via `python -m passedin serve`.';
  }
});
// If a scan is already in flight when the page opens, show it.
fetch('/api/scan/status').then(r => r.json()).then(s => { if (s.running) pollScan(); }).catch(() => {});

for (const [id, key, kind] of [
  ['f-suburb', 'suburb', 'str'], ['f-price', 'price', 'num'],
  ['f-beds', 'beds', 'num'], ['f-outcome', 'outcome', 'str'],
  ['f-dismissed', 'dismissed', 'bool'], ['f-sort', 'sort', 'str'],
]) {
  document.getElementById(id).addEventListener('change', e => {
    state[key] = kind === 'bool' ? e.target.checked
      : kind === 'num' ? (e.target.value === '' ? null : Number(e.target.value))
      : e.target.value;
    render();
  });
}
render();
</script>
</body>
</html>
"""


def render_html(view: dict, summary_text: str, generated_at: str,
                out_path: Path | str) -> Path:
    view = dict(view)
    view["summary_text"] = summary_text
    view["generated_at"] = generated_at
    # </script> inside JSON strings would terminate the data block early.
    data = json.dumps(view).replace("</", "<\\/")
    html = _TEMPLATE.replace("__DATA__", data)
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
