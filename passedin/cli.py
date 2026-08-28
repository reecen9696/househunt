"""Command-line interface.

  python -m passedin scan      # the Sunday-morning command
  python -m passedin report    # rebuild HTML + CSV from the store (no fetch)
  python -m passedin serve     # review page with persistence
  python -m passedin export    # CSV only
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .assemble import build_view
from .config import Config, load_secrets
from .report.csv_export import export_csv
from .report.html import render_html
from .report.summary import format_summary
from .store import Store

logger = logging.getLogger(__name__)


def _setup_logging(config: Config) -> None:
    log_file = config.log_dir / f"run-{datetime.now():%Y%m%d-%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _report_paths(config: Config) -> tuple[Path, Path]:
    return (config.data_dir / "report.html", config.data_dir / "export.csv")


def _generate_outputs(config: Config, week: str | None, stats: dict | None = None) -> int:
    store = Store(config.db_path)
    try:
        week = week or store.latest_week()
        if not week:
            print("No data in the store yet — run `python -m passedin scan` first.")
            return 1
        view = build_view(store, config, week)
        # Surface the last scan's problems (quota exhaustion, parse canary,
        # source failures) in the report itself, not just the console.
        run_stats = stats if stats is not None else (store.latest_run_summary() or {})
        problems = list(run_stats.get("canary_problems") or [])
        if run_stats.get("enrich_aborted"):
            problems.append(f"Price enrichment aborted: {run_stats['enrich_aborted']}")
        view["problems"] = problems
        view["stale_after_days"] = config.get("dating.stale_after_days", 60)
    finally:
        store.close()

    summary_text = format_summary(stats) if stats else \
        f"Week ending: {week} (rebuilt from store, no fetch)"
    html_path, csv_path = _report_paths(config)
    render_html(view, summary_text, datetime.now().strftime("%Y-%m-%d %H:%M"), html_path)
    n = export_csv(view, csv_path)

    counts = {k: len(v) for k, v in view["sections"].items()}
    print(f"\nSections: {counts}")
    print(f"Filtered out: {view['excluded_by_filters']}")
    print(f"HTML report: {html_path}")
    print(f"CSV export:  {csv_path} ({n} rows)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="passedin",
                                     description="Passed-in property finder")
    parser.add_argument("--config", default=None,
                        help="Path to config.yaml (default: alongside the package)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Weekly scan: fetch, parse, store, report")
    p_scan.add_argument("--refetch", action="store_true",
                        help="Ignore today's page cache and fetch fresh")
    p_scan.add_argument("--no-enrich", action="store_true",
                        help="Skip listing-page price enrichment")

    p_report = sub.add_parser("report", help="Rebuild outputs from the store (no fetch)")
    p_report.add_argument("--week", default=None, help="Week ending (YYYY-MM-DD)")

    p_serve = sub.add_parser("serve", help="Serve the review page with persistence")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--week", default=None)

    p_export = sub.add_parser("export", help="CSV export only")
    p_export.add_argument("--week", default=None)

    p_domain = sub.add_parser(
        "domain",
        help="Fill missing land sizes and real listing dates from Domain "
             "property profiles, for tracked properties that need it")
    p_domain.add_argument("--limit", type=int, default=10,
                          help="Max properties to look up (default 10)")
    p_domain.add_argument("--all", action="store_true",
                          help="Ignore the 'needs it' gate and refresh every one")

    p_auctions = sub.add_parser(
        "auctions",
        help="Cache Domain's dated auction results so past pass-ins can be "
             "matched against any tracked property")
    p_auctions.add_argument("--weeks", type=int, default=12,
                            help="How many Saturdays back to fetch (default 12)")

    args = parser.parse_args(argv)

    config_path = args.config or Path(__file__).resolve().parent.parent / "config.yaml"
    config = Config.load(config_path)
    load_secrets(config.base_dir)
    _setup_logging(config)

    if args.command == "scan":
        from .scan import run_scan
        stats = run_scan(config, refetch=args.refetch, no_enrich=args.no_enrich)
        print("\n" + "=" * 64)
        print(format_summary(stats))
        print("=" * 64)
        rc = _generate_outputs(config, stats.get("week_ending"), stats)
        if stats.get("canary_problems"):
            print("\nCANARY PROBLEMS — treat this week's output as suspect.",
                  file=sys.stderr)
            return 2
        return rc

    if args.command == "report":
        return _generate_outputs(config, args.week)

    if args.command == "serve":
        html_path, _ = _report_paths(config)
        if _generate_outputs(config, args.week) != 0:
            # Empty store: render the shell anyway. On a fresh deployment the
            # page is the only way to reach "Run weekly scan", so refusing to
            # start would leave no way in.
            from .assemble import empty_view
            render_html(empty_view(config), "No scan yet — run one to begin.",
                        datetime.now().strftime("%Y-%m-%d %H:%M"), html_path)
        from .serve import serve
        # PORT/HOST come from the platform when hosted; the defaults keep a
        # local `passedin serve` bound to loopback as before.
        port = int(os.environ.get("PORT") or args.port)
        host = os.environ.get("HOST", "127.0.0.1")
        serve(html_path, config.db_path, base_dir=config.base_dir,
              log_dir=config.log_dir, port=port, config=config, host=host)
        return 0

    if args.command == "domain":
        from .dating_view import dating_for_row
        from .domain_lookup import lookup, needs_lookup
        from .fetch import build_fetcher
        store = Store(config.db_path)
        fetcher = build_fetcher(config)
        done = skipped = 0
        try:
            for row in store.list_tracked():
                if done >= args.limit:
                    break
                address = row["address"]
                if not address or not row["postcode"]:
                    skipped += 1
                    continue
                dating = dating_for_row(row, store, config)
                if not args.all and not needs_lookup(
                        land_size=row["land_size_sqm"],
                        campaign_basis=dating.get("campaign_basis"),
                        config=config):
                    skipped += 1
                    continue
                suburb = row["suburb"] or (address.split(",")[1].strip()
                                           if "," in address else None)
                found = lookup(row["url"], street=address.split(",")[0],
                               suburb=suburb, address=address,
                               postcode=row["postcode"], store=store,
                               config=config, fetcher=fetcher)
                if found.get("land_size_sqm") and not row["land_size_sqm"]:
                    store.upsert_tracked(
                        {"url": row["url"],
                         "land_size_sqm": found["land_size_sqm"]})
                print(f"  {address}: land={found.get('land_size_sqm') or '—'} "
                      f"listed={found.get('date_listed') or '—'}")
                done += 1
        finally:
            fetcher.close()
            store.close()
        print(f"Looked up {done}, skipped {skipped} (nothing to gain or "
              f"address incomplete).")
        return 0

    if args.command == "auctions":
        from .auction_check import backfill_domain_weeks
        from .fetch import build_fetcher
        store = Store(config.db_path)
        fetcher = build_fetcher(config)
        try:
            summary = backfill_domain_weeks(store, config, fetcher, args.weeks)
        finally:
            fetcher.close()
            store.close()
        print(f"Fetched {len(summary['fetched'])} week(s), "
              f"{len(summary['cached'])} already cached, "
              f"{len(summary['failed'])} failed.")
        if summary["failed"]:
            print("  failed:", ", ".join(summary["failed"]))
        return 0

    if args.command == "export":
        return _generate_outputs(config, args.week)

    return 1
