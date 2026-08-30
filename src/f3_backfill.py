"""F3: one-time backfill of Polycam plans into the HUB for all ACTIVE
projects (~100+). Resumable via the shared manifest; every re-run skips
completed rows.

Workflow (three subcommands):
  1. build-table   — HUB deals × Polycam library → fuzzy matches → review CSV
                     (state/matching_review.csv). Eran reviews rows under the
                     0.9 threshold ONCE, sets approved=yes/no (or corrects
                     capture_id), saves as state/matching_table.csv.
  2. run --pilot 3 — batch F1 over approved rows, capped (3 → 10 → --all).
  3. report        — final rollup: per-project counts, failures, skips, total
                     bytes (all moved server-side), written to
                     state/backfill_report.csv + printed.

Usage:
  python -m src.f3_backfill build-table
  python -m src.f3_backfill run --pilot 3
  python -m src.f3_backfill run --all
  python -m src.f3_backfill report
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from .alerts import alert
from .config import settings, state_path
from .f1_pipeline import F1Pipeline
from .hub_client import HubClient
from .manifest import Manifest
from .matching import build_matching_table, read_approved_table, write_review_csv
from . import polycam_export

log = logging.getLogger(__name__)


def build_table() -> None:
    hub = HubClient()
    deals = hub.list_sales_deals(status="ACCEPTED")
    with sync_playwright() as pw:
        ctx = polycam_export.open_browser(pw)
        try:
            captures = polycam_export.list_captures(ctx)
        finally:
            ctx.close()
    matches = build_matching_table(
        [{**d, "dealId": d.get("dealId") or d.get("id")} for d in deals],
        captures)
    review_csv = Path(settings()["matching"]["review_csv"])
    n_review = write_review_csv(matches, review_csv,
                                settings()["matching"]["auto_accept_confidence"])
    print(f"{len(matches)} matches written to {review_csv} — {n_review} rows "
          f"need review (blank 'approved' column). Review once, then save the "
          f"file as {settings()['matching']['table']}.")


def run(limit: int | None) -> None:
    table = Path(settings()["matching"]["table"])
    if not table.exists():
        raise SystemExit(
            f"{table} not found. Run 'build-table', review "
            f"{settings()['matching']['review_csv']}, and save it as {table}.")
    approved = read_approved_table(table)
    manifest = Manifest(state_path("manifest"))

    # Resumability: deals already F1-complete don't count against the cap.
    todo = [m for m in approved
            if not manifest.data["deals"].get(m.deal_id, {}).get("f1_complete_at")]
    if limit is not None:
        todo = todo[:limit]
    print(f"{len(approved)} approved, {len(todo)} to process this run.")

    done = failed = 0
    hub = HubClient()
    with sync_playwright() as pw:
        ctx = polycam_export.open_browser(pw)
        try:
            pipeline = F1Pipeline(hub, manifest, ctx)
            for m in todo:
                log.info("backfill: %s (%s)", m.deal_address, m.deal_id)
                try:
                    if pipeline.run_for_deal(m.deal_id, m.deal_address,
                                             m.capture_id):
                        done += 1
                    else:
                        failed += 1
                except Exception as e:  # keep the batch moving; row is retried
                    failed += 1
                    alert(f"backfill: {m.deal_address} crashed — {e}")
        finally:
            ctx.close()
    print(f"run finished: {done} complete, {failed} incomplete (re-run to retry).")


def report() -> None:
    manifest = Manifest(state_path("manifest"))
    rollup = manifest.report()
    out = state_path("manifest").parent / "backfill_report.csv"
    manifest.export_csv(out)
    print(json.dumps(rollup["totals"], indent=2))
    for did, row in sorted(rollup["per_deal"].items(),
                           key=lambda kv: kv[1]["address"] or ""):
        print(f"  {row['address'] or did}: {row['verified']} verified, "
              f"{row['skipped']} skipped, {row['failed']} failed, "
              f"{row['flagged']} flagged, {row['bytes']} bytes")
    print(f"detail rows: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-table")
    p_run = sub.add_parser("run")
    group = p_run.add_mutually_exclusive_group(required=True)
    group.add_argument("--pilot", type=int, metavar="N",
                       help="process at most N not-yet-complete projects")
    group.add_argument("--all", action="store_true")
    sub.add_parser("report")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "build-table":
        build_table()
    elif args.cmd == "run":
        run(None if args.all else args.pilot)
    else:
        report()


if __name__ == "__main__":
    main()
