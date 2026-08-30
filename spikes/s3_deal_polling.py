"""S3 — deal-status polling shape.

Lists ACCEPTED ("Signed") deals and reports: row count, available fields
(do we get a status-change timestamp for cheap diffing?), and the id fields
present (dealId vs id — the two-ID trap). Confirms hourly polling is viable
before wiring f1_watch to a schedule.

Usage: python -m spikes.s3_deal_polling
"""
from __future__ import annotations

import json

from src.hub_client import HubClient


def main() -> None:
    hub = HubClient()
    deals = hub.list_sales_deals(status="ACCEPTED")
    print(f"{len(deals)} ACCEPTED deals")
    if not deals:
        return
    sample = deals[0]
    print("fields on a deal row:", sorted(sample.keys()))
    for key in ("dealId", "id", "address", "name", "status", "updatedAt",
                "statusChangedAt", "acceptedAt"):
        print(f"  {key}: {json.dumps(sample.get(key))}")
    print("\nIf no status-change timestamp exists, f1_watch's full-list diff "
          "against state/deals_seen.json stands (already implemented).")


if __name__ == "__main__":
    main()
