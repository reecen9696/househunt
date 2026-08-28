"""CSV export (§10): the full flat dataset for spreadsheet work."""
from __future__ import annotations

import csv
from pathlib import Path

_COLUMNS = [
    "section", "suburb", "postcode", "address_raw", "outcome", "outcome_raw",
    "weeks_unsold", "price_low", "price_high", "price_status",
    "highest_bid", "vendor_bid", "sold_price", "price_changed",
    "property_type", "bedrooms", "bathrooms", "car_spaces", "land_size_sqm",
    "agency_name", "agent_name", "sources", "rea_url", "domain_url", "image_url",
    "merge_confidence", "dismissed", "user_rating", "user_notes",
    "first_seen_week", "week_ending", "property_id",
]


def export_csv(view: dict, out_path: Path | str) -> int:
    rows = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for section, items in view["sections"].items():
            for item in items:
                row = dict(item)
                row["section"] = section
                urls = item.get("source_urls") or {}
                row["rea_url"] = urls.get("rea")
                row["domain_url"] = urls.get("domain")
                row["sources"] = "+".join(item.get("sources") or [])
                writer.writerow(row)
                rows += 1
    return rows
