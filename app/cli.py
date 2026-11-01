from __future__ import annotations

import argparse

from app.config import DEFAULT_OUTPUT, DEFAULT_SEARCH, Job
from app.scraper import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape Thomasnet supplier search results into JSON and CSV files."
    )
    parser.add_argument("--search", default=DEFAULT_SEARCH, help="Supplier search term.")
    parser.add_argument("--start-page", type=int, default=1, help="Results page to start on. Default: 1.")
    parser.add_argument("--max-pages", type=int, default=0, help="How many pages to scrape. 0 means every page.")
    parser.add_argument(
        "--max-suppliers",
        type=int,
        default=0,
        help="How many new supplier profiles to save. 0 means no limit.",
    )
    parser.add_argument("--delay", type=float, default=2.5, help="Seconds to wait between profiles. Default: 2.5.")
    parser.add_argument("--headless", action="store_true", help="Hide the Chrome window. A visible window is more reliable.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Folder for JSON and CSV files.")
    parser.add_argument("--no-resume", action="store_true", help="Scrape suppliers again even if they are already saved.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_page < 1:
        raise SystemExit("Start page must be 1 or more.")
    if args.max_pages < 0 or args.max_suppliers < 0:
        raise SystemExit("Limits cannot be negative.")
    if args.delay < 0.5:
        raise SystemExit("Use a delay of at least 0.5 seconds.")
    job = Job(
        search_term=args.search.strip(),
        start_page=args.start_page,
        max_pages=args.max_pages,
        max_suppliers=args.max_suppliers,
        delay=args.delay,
        headless=args.headless,
        output_dir=args.output,
        resume=not args.no_resume,
    )
    if not job.search_term:
        raise SystemExit("Enter a search term.")
    summary = run(job)
    if summary.get("error"):
        return 1
    return 0
