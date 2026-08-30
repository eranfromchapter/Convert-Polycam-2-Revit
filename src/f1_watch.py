"""F1 trigger: poll ACCEPTED deals hourly, diff against state, run the
pipeline for new signings.

This is the FALLBACK trigger (Windows Task Scheduler, hourly). If the dev
delivers the deal→ACCEPTED webhook (DEV_QUESTIONS Q2), point the webhook
handler at run_new_deals() with the payload's dealId instead — the pipeline
itself is trigger-agnostic.

Matching: a signed deal is matched against the Polycam library via
matching.best_match. Confidence below the auto-accept threshold, or an empty
library → one-line alert to Eran, NEVER a guess. The approved F3 matching
table (state/matching_table.csv) is consulted first so human-reviewed pairs
always win.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from .alerts import alert
from .config import settings, state_path
from .f1_pipeline import F1Pipeline
from .hub_client import HubClient
from .manifest import Manifest
from .matching import best_match, read_approved_table
from . import polycam_export

log = logging.getLogger(__name__)

ACCEPTED = "ACCEPTED"  # facet label in the HUB UI: "Signed"


def _load_seen(path: Path) -> set[str]:
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")))
    return set()


def _save_seen(path: Path, seen: set[str]) -> None:
    path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


def _approved_lookup() -> dict[str, tuple[str, str]]:
    """deal_id -> (capture_id, capture_name) from the human-reviewed table."""
    table = Path(settings()["matching"]["table"])
    if not table.exists():
        return {}
    return {m.deal_id: (m.capture_id, m.capture_name)
            for m in read_approved_table(table)}


def run_new_deals(only_deal_ids: set[str] | None = None) -> int:
    """Poll → diff → pipeline. Returns the number of deals processed.
    only_deal_ids narrows to specific deals (webhook mode / manual rerun)."""
    hub = HubClient()
    seen_path = state_path("deals_seen")
    seen = _load_seen(seen_path)
    manifest = Manifest(state_path("manifest"))
    approved = _approved_lookup()
    threshold = settings()["matching"]["auto_accept_confidence"]

    deals = hub.list_sales_deals(status=ACCEPTED)
    new = [d for d in deals
           if (d.get("dealId") or d.get("id")) not in seen
           and (only_deal_ids is None
                or (d.get("dealId") or d.get("id")) in only_deal_ids)]
    if not new:
        log.info("no new ACCEPTED deals (%d total, %d seen)", len(deals), len(seen))
        return 0

    processed = 0
    with sync_playwright() as pw:
        ctx = polycam_export.open_browser(pw)
        try:
            captures = polycam_export.list_captures(ctx)
            pipeline = F1Pipeline(hub, manifest, ctx)
            for deal in new:
                deal_id = deal.get("dealId") or deal.get("id")
                address = deal.get("address") or deal.get("name") or deal_id
                capture_id = None
                if deal_id in approved:
                    capture_id = approved[deal_id][0]
                else:
                    m = best_match({**deal, "dealId": deal_id}, captures)
                    if m and m.confidence >= threshold:
                        capture_id = m.capture_id
                    elif m:
                        alert(f"unmatched deal {address!r}: best Polycam "
                              f"candidate {m.capture_name!r} at "
                              f"{m.confidence:.2f} < {threshold} — review needed")
                    else:
                        alert(f"unmatched deal {address!r}: Polycam library "
                              f"returned no captures")
                if capture_id is None:
                    # Not marked seen — retried next cycle until a human fixes
                    # the matching table or the capture appears.
                    continue
                if pipeline.run_for_deal(deal_id, address, capture_id):
                    seen.add(deal_id)
                    _save_seen(seen_path, seen)
                processed += 1
        finally:
            ctx.close()
    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_new_deals()
