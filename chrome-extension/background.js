// Passed-In Property Tracker — one-click save of the listing you're viewing
// into your passed-in-finder server.
//
// Flow: toolbar click -> extract listing facts from the page (embedded JSON
// first, DOM as fallback) -> POST /api/track -> badge feedback on the icon.
//
// The server address lives in chrome.storage.sync (set it on the options
// page) so the same extension works against a local `passedin serve` and
// against the hosted deployment, which requires a password.

const DEFAULTS = { baseUrl: "http://127.0.0.1:8765", user: "reece", password: "" };

async function serverConfig() {
  const cfg = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...cfg };
}

// Runs INSIDE the page. Defensive by design: every field is optional except
// the URL — the backend accepts partial rows and they can be edited in the UI.
function extractListing() {
  const data = { url: location.href.split("?")[0] };
  const html = document.documentElement.innerHTML;
  const pick = (re) => {
    const m = html.match(re);
    return m ? m[1] : null;
  };

  // 1. JSON-LD structured data (stable across redesigns where present)
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const parsed = JSON.parse(s.textContent);
      for (const o of Array.isArray(parsed) ? parsed : [parsed]) {
        const addr = o && o.address;
        if (addr && addr.streetAddress && !data.address) {
          data.address = addr.streetAddress;
          data.suburb = addr.addressLocality || null;
          data.postcode = addr.postalCode || null;
        }
      }
    } catch (e) { /* ignore malformed blocks */ }
  }

  // 2. Embedded application JSON (REA pages carry these keys)
  data.price_text =
    pick(/"marketingPriceRange"\s*:\s*"([^"]+)"/) ||
    pick(/"priceText"\s*:\s*"([^"]+)"/) ||
    pick(/"displayPrice"\s*:\s*"([^"]+)"/);
  data.property_type = pick(/"propertyType"\s*:\s*\{[^{}]{0,60}?"display"\s*:\s*"([^"]+)"/) ||
    pick(/"propertyType"\s*:\s*"([^"]+)"/);
  const num = (re) => {
    const v = pick(re);
    return v == null ? null : Number(v);
  };
  data.bedrooms = num(/"bedrooms"\s*:\s*(\d+)/);
  data.bathrooms = num(/"bathrooms"\s*:\s*(\d+)/);
  data.car_spaces = num(/"carSpaces"\s*:\s*(\d+)/) || num(/"parkingSpaces"\s*:\s*(\d+)/);
  const listed = pick(/"dateFirstListed"\s*:\s*"([^"]+)"/) || pick(/"dateListed"\s*:\s*"([^"]+)"/);
  if (listed) data.date_listed = listed.slice(0, 10);
  data.inspection_text =
    pick(/"inspections"\s*:\s*\[\s*\{[^[\]]{0,120}?"longLabel"\s*:\s*"([^"]+)"/) ||
    pick(/"inspections"\s*:\s*\[\s*\{[^[\]]{0,200}?"shortDate"\s*:\s*"([^"]+)"/);
  data.auction_text =
    pick(/"auction"\s*:\s*\{[^[\]]{0,200}?"longLabel"\s*:\s*"([^"]+)"/) ||
    pick(/"auction"\s*:\s*\{[^[\]]{0,200}?"shortDate"\s*:\s*"([^"]+)"/);
  data.agency_name = pick(/"listingCompany"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"/);
  data.agent_name = pick(/"listers"\s*:\s*\[\s*\{[^{}]*?"name"\s*:\s*"([^"]+)"/);
  // Listing pages spell it primaryColour; auction-results rows primaryColor.
  data.agency_color = pick(/"branding"\s*:\s*\{[^{}]*?"primaryColou?r"\s*:\s*"(#[0-9a-fA-F]{3,8})"/);
  const land = pick(/"landSize"\s*:\s*\{[^{}]*?"displayValue"\s*:\s*"?([\d,.]+)/);
  if (land) data.land_size_sqm = Number(land.replace(/,/g, ""));

  // 3. DOM fallbacks
  if (!data.address) {
    const h1 = document.querySelector("h1");
    if (h1) data.address = h1.textContent.trim();
  }
  if (!data.price_text) {
    const el = document.querySelector('[class*="property-price"], [data-testid*="price"]');
    if (el) data.price_text = el.textContent.trim();
  }
  const og = document.querySelector('meta[property="og:image"]');
  if (og) data.image_url = og.getAttribute("content");
  if (!data.image_url) {
    const main = pick(/"mainImage"\s*:\s*\{[^}]*?"templatedUrl"\s*:\s*"([^"]+)"/);
    if (main) data.image_url = main.replace("{size}", "800x600");
  }

  // Floor plan: embedded media JSON first, then any img tagged as one.
  const plan =
    pick(/"floorplans?"\s*:\s*\[\s*\{[^}]*?"(?:templatedUrl|url)"\s*:\s*"([^"]+)"/i) ||
    pick(/"type"\s*:\s*"FLOORPLAN"[^}]*?"(?:templatedUrl|url)"\s*:\s*"([^"]+)"/i) ||
    pick(/"(?:templatedUrl|url)"\s*:\s*"([^"]+)"[^}]*?"type"\s*:\s*"FLOORPLAN"/i);
  if (plan) {
    data.floorplan_url = plan.replace("{size}", "1000x750");
  } else {
    const img = document.querySelector('img[alt*="loorplan" i], img[alt*="loor plan" i]');
    if (img && img.src) data.floorplan_url = img.src;
  }

  return data;
}

function badge(tabId, text, color) {
  chrome.action.setBadgeText({ tabId, text });
  chrome.action.setBadgeBackgroundColor({ tabId, color });
  setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }), 3000);
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !tab.url || !/realestate\.com\.au|domain\.com\.au|property\.com\.au/.test(tab.url)) {
    badge(tab.id, "n/a", "#888888");
    return;
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractListing,
    });
    const cfg = await serverConfig();
    const headers = { "Content-Type": "application/json" };
    // Only sent when a password is configured, so a local server that has no
    // auth enabled sees exactly the same request it always did.
    if (cfg.password) {
      headers.Authorization = "Basic " + btoa(`${cfg.user}:${cfg.password}`);
    }
    const resp = await fetch(cfg.baseUrl.replace(/\/$/, "") + "/api/track", {
      method: "POST",
      headers,
      body: JSON.stringify(result),
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    badge(tab.id, "✓", "#2d6a4f");
  } catch (e) {
    // Most common causes: the server isn't running (`passedin serve`), or
    // the address/password on the options page is wrong.
    console.error("Track failed:", e);
    badge(tab.id, "✗", "#a3320b");
  }
});
