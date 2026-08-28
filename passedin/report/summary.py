"""Run summary (§10): printed to console and embedded at the top of the
HTML. If the tool quietly returns 4 results when it returned 60 last week,
this is what catches it.
"""
from __future__ import annotations


def format_summary(stats: dict) -> str:
    lines = []
    lines.append(f"Week ending:        {stats.get('week_ending')}")
    lines.append(f"Pages fetched:      {stats.get('pages_fetched', 0)} "
                 f"(+{stats.get('cache_hits', 0)} from cache)")
    for source, s in (stats.get("sources") or {}).items():
        if "failed" in s:
            lines.append(f"  {source}: FAILED — {s['failed']}")
            continue
        lines.append(
            f"  {source}: {s.get('entry_suburbs', '?')} suburbs indexed, "
            f"{s.get('suburbs_targeted', '?')} targeted, "
            f"{s.get('suburb_pages_parsed', '?')} parsed, "
            f"{s.get('rows_parsed', '?')} rows"
        )
        lines.append(
            f"    dedupe: {s.get('dedupe_exact_merges', 0)} exact, "
            f"{s.get('dedupe_fuzzy_merges', 0)} fuzzy (flagged)"
        )
    lines.append(f"Non-sales found:    {stats.get('non_sales_found', 0)}")
    if stats.get("enrich_priced") is not None or stats.get("enrich_dead_links") \
            or stats.get("enrich_aborted") or stats.get("enrich_errors"):
        lines.append(
            f"Enrichment:         {stats.get('enrich_priced', 0)} priced, "
            f"{stats.get('enrich_dead_links', 0)} dead links, "
            f"{stats.get('enrich_errors', 0)} errors, "
            f"{stats.get('enrich_no_price', 0)} no price found"
        )
        if stats.get("enrich_aborted"):
            lines.append(f"  ENRICHMENT ABORTED: {stats['enrich_aborted']}")
    unrec = stats.get("unrecognised_outcomes") or {}
    if unrec:
        lines.append(f"UNRECOGNISED OUTCOME LABELS: {unrec}")
    problems = stats.get("canary_problems") or []
    if problems:
        lines.append("")
        lines.append("!!! CANARY PROBLEMS " + "!" * 40)
        for p in problems:
            lines.append(f"  - {p}")
    lines.append(f"Elapsed:            {stats.get('elapsed_seconds', '?')}s")
    return "\n".join(lines)
