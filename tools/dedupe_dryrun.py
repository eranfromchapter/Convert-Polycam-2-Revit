"""Dry-run F1's dedupe against a real HUB inventory. READ-ONLY — never uploads.

Answers the only question that matters before you let F1 touch a live project:
*which files would it upload, and would any of them be duplicates?* There is no
delete tool in the HUB, so a duplicate is permanent.

This drives the ACTUAL production method (`F1Pipeline._existing_keys`) with a
stub HUB client, so its verdict tracks the real code rather than a copy of it.

Scope — read this before quoting a number
-----------------------------------------
Only the **url lane** is predicted. Those four names are deterministic
(`{slug}_{KIND_LABEL}{ext}`, f1_pipeline.py:139). The **relay lane** is not:
its members are named `{slug}_{member.name}` from whatever is actually inside
the Polycam zip (f1_pipeline.py:194), and nobody has listed one yet. Guessing
those names would invent findings, so this tool reports them as unknown.

That cuts both ways. A relay member could dedupe cleanly — if the floorplan zip
contains `RoomData.csv`, F1 uploads `1500BayRd_RoomData.csv`, whose dedupe key
`roomdata.csv` matches the live `1500bayrd_roomdata.csv`. Or it could not.
To settle it, list one zip and extend `planned_relay_uploads()`.

Usage
-----
    # against the committed snapshot of the reference project (no HUB access)
    python -m tools.dedupe_dryrun tests/fixtures/live_hub_inventory_1500bayrd.json

    # against a live pull: save hub_find_project_documents(dealId, response_format="json")
    python -m tools.dedupe_dryrun /path/to/live.json --address "22 Elm St #3, Boston, MA"

Exit code is 1 when a predicted upload does not dedupe, so this works as a
pre-flight gate.
"""
from __future__ import annotations

import argparse
import json
import sys
import types


def _stub_playwright() -> None:
    """Import f1_pipeline without a browser installed."""
    if "playwright" in sys.modules:
        return
    pw = types.ModuleType("playwright")
    sa = types.ModuleType("playwright.sync_api")
    for name in ("BrowserContext", "Page", "sync_playwright"):
        setattr(sa, name, object)
    sa.TimeoutError = type("TimeoutError", (Exception,), {})
    pw.sync_api = sa
    sys.modules["playwright"] = pw
    sys.modules["playwright.sync_api"] = sa


_stub_playwright()

from src.config import settings                                      # noqa: E402
from src.f1_pipeline import (                                        # noqa: E402
    F1Pipeline, PLANS_FOLDER, _KIND_LABEL, _dedupe_key, _document_type, addr_slug,
)
from src.hub_client import HubDocument                               # noqa: E402


class _StubHub:
    """Feeds a saved inventory to the real _existing_keys. Never writes."""

    def __init__(self, files):
        self._docs = [HubDocument(name=f["name"], folder=f.get("folder") or "",
                                  size=f.get("size"), download_url=None)
                      for f in files]

    def find_project_documents(self, deal_id, query=""):
        return list(self._docs)


def planned_url_lane_uploads(slug: str) -> list[str]:
    """The four deterministic url-lane names, in config order."""
    return [f"{slug}_{_KIND_LABEL[c['kind']]}{c['ext']}"
            for c in settings()["polycam"]["exports"] if c["lane"] == "url"]


def planned_relay_uploads(slug: str) -> list[str]:
    """Unknowable without listing a Polycam zip — see the module docstring.

    Extend this once someone lists one: F1 names each member
    f"{slug}_{member.name}" (f1_pipeline.py:194).
    """
    return []


def relay_lane_summary() -> list[str]:
    return [f"{c['kind']} ({len(c['members'])} members: {', '.join(c['members'])})"
            for c in settings()["polycam"]["exports"] if c["lane"] == "relay"]


def existing_keys(files, slug, *, descendants):
    """Dedupe set. `descendants=False` runs the REAL production method;
    `descendants=True` shows what a descendant-aware folder filter would see."""
    if not descendants:
        return F1Pipeline(hub=_StubHub(files), manifest=None,
                          browser_ctx=None)._existing_keys("dry-run", slug)
    target, keys = PLANS_FOLDER.lower(), set()
    for f in files:
        folder = (f.get("folder") or "").lower().rstrip("/")
        if target in folder or not folder:
            keys.add(_dedupe_key(f["name"], slug))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inventory", help="JSON from hub_find_project_documents")
    ap.add_argument("--address", help="deal address (default: _address in the file)")
    args = ap.parse_args()

    data = json.load(open(args.inventory, encoding="utf-8"))
    files = data["files"]
    address = args.address or data.get("_address")
    if not address:
        ap.error("no --address given and no '_address' key in the inventory file")
    slug = addr_slug(address)

    print(f"inventory    : {args.inventory}")
    print(f"address      : {address}")
    print(f"slug         : {slug}")
    print(f"PLANS_FOLDER : {PLANS_FOLDER!r}")
    print(f"files in HUB : {len(files)}")
    print(f"folders      : {', '.join(sorted({(f.get('folder') or '(root)') for f in files}))}")

    current = existing_keys(files, slug, descendants=False)   # real production code
    fixed = existing_keys(files, slug, descendants=True)
    print(f"\ndedupe set — production _existing_keys : {len(current)} of {len(files)} files")
    print(f"dedupe set — descendant-aware          : {len(fixed)} of {len(files)} files")
    if len(fixed) > len(current):
        print("  !! the folder filter is hiding existing files from dedupe")

    plan = planned_url_lane_uploads(slug) + planned_relay_uploads(slug)
    print(f"\nURL LANE — deterministic names ({len(plan)})")
    print(f"{'PLANNED UPLOAD':<34} {'TYPE':<9} {'PRODUCTION':<22} FIXED-FILTER")
    print("-" * 90)
    undeduped = 0
    for name in plan:
        key = _dedupe_key(name, slug)
        now = "SKIP (exists)" if key in current else "UPLOAD (new)"
        then = "SKIP (exists)" if key in fixed else "UPLOAD (new)"
        undeduped += key not in fixed
        print(f"{name:<34} {_document_type(name[name.rfind('.'):]):<9} {now:<22} {then}")
    print("-" * 90)
    print(f"{undeduped} of {len(plan)} would still be created with the folder filter "
          f"fixed (name-scheme mismatch).")

    print(f"\nRELAY LANE — names NOT predicted, so NOT counted above:")
    for line in relay_lane_summary():
        print(f"  - {line}")
    print("  F1 names these from the zip's real contents (f1_pipeline.py:194).")
    print("  List one Polycam zip to settle whether they dedupe.")

    if undeduped:
        print("\nDO NOT RUN F1 AGAINST THIS PROJECT — uploads here are permanent.")
    return 1 if undeduped else 0


if __name__ == "__main__":
    raise SystemExit(main())
