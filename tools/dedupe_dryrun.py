"""Dry-run F1's dedupe against a real HUB inventory. READ-ONLY — never uploads.

Answers the only question that matters before you let F1 touch a live project:
*which files would it upload, and would any of them be duplicates?* There is no
delete tool in the HUB, so a duplicate is permanent.

Usage
-----
    # against the committed snapshot of the reference project (no HUB access needed)
    python -m tools.dedupe_dryrun tests/fixtures/live_hub_inventory_1500bayrd.json

    # against a live pull: save hub_find_project_documents(dealId, response_format="json")
    python -m tools.dedupe_dryrun /path/to/live.json --address "22 Elm St #3, Boston, MA"

Input JSON must contain {"files": [{"name": ..., "folder": ...}, ...]} — the exact
shape hub_find_project_documents returns.

Exit code is 1 when the planned upload set is NOT fully deduped against what is
already in the HUB, so this is usable as a pre-flight gate in a script.
"""
from __future__ import annotations

import argparse
import json
import sys
import types


def _stub_playwright() -> None:
    """Import f1_pipeline without a browser installed (we use only pure helpers)."""
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

from src.config import settings                                  # noqa: E402
from src.f1_pipeline import (                                    # noqa: E402
    PLANS_FOLDER, _dedupe_key, _document_type, addr_slug,
)

# Mirrors F1's url-lane naming (src/f1_pipeline.py `_KIND_LABEL`).
_KIND_LABEL = {
    "mesh_glb": "Mesh",
    "pointcloud_laz": "PointCloud",
    "pointcloud_ply": "PointCloud",
    "spatial_report": "SpatialReport",
}


def planned_uploads(slug: str) -> list[str]:
    """Every file name F1 would try to create, in config order."""
    names = []
    for cfg in settings()["polycam"]["exports"]:
        if cfg["lane"] == "url":
            names.append(f"{slug}_{_KIND_LABEL[cfg['kind']]}{cfg['ext']}")
        else:
            names.extend(f"{slug}_{cfg['kind']}{m}" for m in cfg["members"])
    return names


def existing_keys(files: list[dict], slug: str, *, descendants: bool) -> set[str]:
    """Reproduce F1._existing_keys.

    descendants=False is CURRENT production behaviour (an `endswith` test, so it
    sees only files sitting directly in "Plans and 3D"). descendants=True is the
    proposed fix — it also sees subfolders such as "Plans and 3D/Polycam".
    """
    keys = set()
    target = PLANS_FOLDER.lower()
    for f in files:
        folder = (f.get("folder") or "").lower().rstrip("/")
        hit = (target in folder) if descendants else folder.endswith(target)
        if hit or not folder:
            keys.add(_dedupe_key(f["name"], slug))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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
    folders = sorted({(f.get("folder") or "(root)") for f in files})
    print(f"folders      : {', '.join(folders)}")

    current = existing_keys(files, slug, descendants=False)
    fixed = existing_keys(files, slug, descendants=True)
    print(f"\ndedupe set — current (endswith) : {len(current)} of {len(files)} files")
    print(f"dedupe set — descendant-aware   : {len(fixed)} of {len(files)} files")
    if len(fixed) > len(current):
        print("  !! the folder filter is hiding existing files from dedupe")

    plan = planned_uploads(slug)
    print(f"\n{'PLANNED UPLOAD':<34} {'TYPE':<9} {'CURRENT':<24} FIXED-FILTER")
    print("-" * 92)
    dupes = 0
    for name in plan:
        key = _dedupe_key(name, slug)
        now = "SKIP (exists)" if key in current else "UPLOAD (new)"
        then = "SKIP (exists)" if key in fixed else "UPLOAD (new)"
        dupes += key not in fixed
        print(f"{name:<34} {_document_type(name[name.rfind('.'):]):<9} {now:<24} {then}")
    print("-" * 92)
    print(f"{len(plan)} planned; {dupes} would still be created even with the folder "
          f"filter fixed (name-scheme mismatch).")
    if dupes:
        print("\nDO NOT RUN F1 AGAINST THIS PROJECT — uploads here are permanent.")
    return 1 if dupes else 0


if __name__ == "__main__":
    raise SystemExit(main())
