"""Dedupe against the REAL contents of the reference project.

`test_f1_dedupe.py` tests the helpers against a two-file premise that the live
HUB no longer matches. This module pins F1's behaviour against a snapshot of what
"1500 Bay Rd #1462" actually holds (97 files, captured 2026-08-30 — see
tests/fixtures/live_hub_inventory_1500bayrd.json).

The xfail below is the acceptance target for the dedupe fix: make it pass without
`strict=False` and F1 is safe to point at a real project again.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

if "playwright" not in sys.modules:                        # no browser in CI
    _pw, _sa = types.ModuleType("playwright"), types.ModuleType("playwright.sync_api")
    for _n in ("BrowserContext", "Page", "sync_playwright"):
        setattr(_sa, _n, object)
    _sa.TimeoutError = type("TimeoutError", (Exception,), {})
    _pw.sync_api = _sa
    sys.modules["playwright"], sys.modules["playwright.sync_api"] = _pw, _sa

from src.f1_pipeline import PLANS_FOLDER, _dedupe_key, addr_slug  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "live_hub_inventory_1500bayrd.json"
ADDRESS = "1500 Bay Rd #1462, Miami Beach, FL"


@pytest.fixture(scope="module")
def live():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _existing_keys(files, slug, *, descendants):
    """F1._existing_keys — `descendants=False` is current production behaviour."""
    target, keys = PLANS_FOLDER.lower(), set()
    for f in files:
        folder = (f.get("folder") or "").lower().rstrip("/")
        hit = (target in folder) if descendants else folder.endswith(target)
        if hit or not folder:
            keys.add(_dedupe_key(f["name"], slug))
    return keys


def test_fixture_is_the_untruncated_snapshot(live):
    assert live["resultsTruncated"] is False and live["traversalCapHit"] is False
    assert len(live["files"]) == live["filesScanned"] == 97


def test_every_file_lives_in_a_subfolder_not_the_folder_root(live):
    """Why the endswith() filter fails: nothing sits directly in "Plans and 3D"."""
    folders = {f["folder"] for f in live["files"]}
    assert folders == {"/Plans and 3D/Polycam", "/Plans and 3D/Revit"}
    assert not any(f["folder"].lower().rstrip("/").endswith(PLANS_FOLDER.lower())
                   for f in live["files"])


def test_current_folder_filter_sees_nothing(live):
    """BUG: dedupe is blind to all 97 files, so F1 would re-upload everything."""
    keys = _existing_keys(live["files"], addr_slug(ADDRESS), descendants=False)
    assert keys == set()


def test_descendant_aware_filter_sees_everything(live):
    keys = _existing_keys(live["files"], addr_slug(ADDRESS), descendants=True)
    assert len(keys) == 97


def test_media_lane_wrote_empty_files(live):
    """18 media uploads landed at 0 bytes — including the DXF that F2 consumes."""
    zero = [f["name"] for f in live["files"] if f["size"] == 0]
    assert len(zero) == 18
    assert "1500bayrd_floorplan.dxf" in zero
    assert all(f["documentType"] == "media"
               for f in live["files"] if f["size"] == 0)


def test_binaries_were_base64_chunked_into_txt_parts(live):
    """Violates README architecture rule #1 (no bytes through an LLM context)."""
    parts = [f for f in live["files"] if f["name"].endswith(".txt")
             and "_part" in f["name"]]
    assert len(parts) == 67
    assert sum(p["size"] for p in parts) > 45_000_000


@pytest.mark.xfail(strict=False, reason=(
    "Known dedupe defect — the acceptance target for the handover. Two causes: "
    "(1) the endswith() folder filter hides every subfolder file; (2) even with "
    "that fixed, F1's planned names (1500BayRd_floorplan_all.dxf) do not match "
    "the live scheme (1500bayrd_floorplan.dxf). Uploads are permanent."))
def test_planned_uploads_dedupe_against_live_inventory(live):
    from tools.dedupe_dryrun import planned_uploads
    slug = addr_slug(ADDRESS)
    existing = _existing_keys(live["files"], slug, descendants=True)
    undeduped = [n for n in planned_uploads(slug)
                 if _dedupe_key(n, slug) not in existing]
    assert undeduped == [], f"{len(undeduped)} permanent duplicates: {undeduped}"
