"""Idempotent, resumable run manifest.

One JSON checkpoint (state/manifest.json): per deal → per file →
status/size/timestamp/source. Re-runs skip completed rows. Writes are atomic
(temp file + os.replace) so a killed run never corrupts the checkpoint.

Statuses: pending → uploaded → verified | failed | skipped_existing |
flagged_oversize | flagged_manual.

Source URLs are stored REDACTED (no query strings) — signed/tempauth URLs are
short-lived secrets and must not persist or appear in logs.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .hub_client import redact_url

DONE_STATUSES = {"verified", "skipped_existing"}
# 'uploaded' is intentionally NOT done: verification via find_project_documents
# must confirm it before a re-run will skip it.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Manifest:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"deals": {}}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -- deals --------------------------------------------------------------

    def deal(self, deal_id: str, *, address: str | None = None,
             capture_id: str | None = None) -> dict:
        d = self.data["deals"].setdefault(
            deal_id, {"address": address, "capture_id": capture_id, "files": {},
                      "f1_complete_at": None, "f2_complete_at": None})
        if address:
            d["address"] = address
        if capture_id:
            d["capture_id"] = capture_id
        return d

    def deal_ids(self) -> list[str]:
        return list(self.data["deals"].keys())

    # -- files --------------------------------------------------------------

    def file_status(self, deal_id: str, file_name: str) -> str | None:
        return (self.data["deals"].get(deal_id, {})
                .get("files", {}).get(file_name, {}).get("status"))

    def is_done(self, deal_id: str, file_name: str) -> bool:
        return self.file_status(deal_id, file_name) in DONE_STATUSES

    def record(self, deal_id: str, file_name: str, status: str, *,
               size: int | None = None, source_url: str | None = None,
               folder: str | None = None, error: str | None = None) -> None:
        entry = self.deal(deal_id)["files"].setdefault(file_name, {})
        entry.update({
            "status": status,
            "updated_at": _now(),
        })
        if size is not None:
            entry["size"] = size
        if folder is not None:
            entry["folder"] = folder
        if source_url is not None:
            entry["source_url"] = redact_url(source_url)
        if error is not None:
            entry["error"] = error
        elif "error" in entry:
            del entry["error"]
        self.save()

    def mark_f1_complete(self, deal_id: str) -> None:
        self.deal(deal_id)["f1_complete_at"] = _now()
        self.save()

    def mark_f2_complete(self, deal_id: str) -> None:
        self.deal(deal_id)["f2_complete_at"] = _now()
        self.save()

    def f1_complete_pending_f2(self) -> list[str]:
        return [did for did, d in self.data["deals"].items()
                if d.get("f1_complete_at") and not d.get("f2_complete_at")]

    # -- reporting ----------------------------------------------------------

    def report(self) -> dict:
        """Final-report rollup: per-project counts, failures, skips, bytes."""
        per_deal, totals = {}, {"files": 0, "verified": 0, "skipped": 0,
                                "failed": 0, "flagged": 0, "bytes": 0}
        for did, d in self.data["deals"].items():
            row = {"address": d.get("address"), "verified": 0, "skipped": 0,
                   "failed": 0, "flagged": 0, "bytes": 0}
            for entry in d.get("files", {}).values():
                totals["files"] += 1
                st = entry.get("status", "")
                if st == "verified":
                    row["verified"] += 1
                    row["bytes"] += entry.get("size") or 0
                elif st == "skipped_existing":
                    row["skipped"] += 1
                elif st.startswith("flagged"):
                    row["flagged"] += 1
                elif st == "failed":
                    row["failed"] += 1
            for k in ("verified", "skipped", "failed", "flagged", "bytes"):
                totals[k] += row[k]
            per_deal[did] = row
        return {"per_deal": per_deal, "totals": totals}

    def export_csv(self, path: str | Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["deal_id", "address", "file_name", "status", "size",
                        "folder", "updated_at", "error"])
            for did, d in sorted(self.data["deals"].items()):
                for name, e in sorted(d.get("files", {}).items()):
                    w.writerow([did, d.get("address"), name, e.get("status"),
                                e.get("size"), e.get("folder"),
                                e.get("updated_at"), e.get("error", "")])
