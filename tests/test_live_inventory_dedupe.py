"""Dedupe against the REAL contents of the reference project.

`test_f1_dedupe.py` tests the helpers against a two-file premise that the live
HUB no longer matches. This module drives the ACTUAL production method,
`F1Pipeline._existing_keys`, against a snapshot of what "1500 Bay Rd #1462"
really holds (97 files, captured 2026-08-30 — see
tests/fixtures/live_hub_inventory_1500bayrd.json).

Driving the real method matters: an earlier version of this file reimplemented
`_existing_keys` locally, so fixing the production bug changed no test result
and the "acceptance target" was green either way. Everything here goes through
F1Pipeline with a stub HUB client, so a production fix moves these tests.

The xfail at the bottom is the acceptance target for the dedupe fix.
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

from src.f1_pipeline import F1Pipeline, PLANS_FOLDER, _dedupe_key, addr_slug  # noqa: E402
from src.hub_client import HubDocument                                        # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "live_hub_inventory_1500bayrd.json"
ADDRESS = "1500 Bay Rd #1462, Miami Beach, FL"
DEAL_ID = "db3a0a03-32fd-4a26-8aef-a644e93563ad"


@pytest.fixture(scope="module")
def live():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _StubHub:
    """Returns the snapshot in the shape HubClient.find_project_documents does.

    download_url is None throughout: the fixture's URLs were stripped because
    SharePoint links carry credentials. The live call DOES return them
    (src/hub_client.py:149) — do not infer anything about the live response
    shape from this stub.
    """

    def __init__(self, files):
        self._docs = [
            HubDocument(name=f["name"], folder=f["folder"], size=f["size"],
                        download_url=None)
            for f in files
        ]

    def find_project_documents(self, deal_id, query=""):
        return list(self._docs)


def _pipeline(files):
    """A real F1Pipeline; only the HUB is stubbed."""
    return F1Pipeline(hub=_StubHub(files), manifest=None, browser_ctx=None)


# --- the snapshot itself ----------------------------------------------------

def test_fixture_is_the_untruncated_snapshot(live):
    assert live["resultsTruncated"] is False and live["traversalCapHit"] is False
    assert len(live["files"]) == live["filesScanned"] == 97


def test_every_file_lives_in_a_subfolder_not_the_folder_root(live):
    """Why the endswith() filter fails: nothing sits directly in "Plans and 3D"."""
    assert {f["folder"] for f in live["files"]} == {
        "/Plans and 3D/Polycam", "/Plans and 3D/Revit"}
    assert not any(f["folder"].lower().rstrip("/").endswith(PLANS_FOLDER.lower())
                   for f in live["files"])


# --- the production method --------------------------------------------------

def test_production_existing_keys_is_blind_to_the_whole_project(live):
    """BUG (f1_pipeline.py:123): the real method sees 0 of 97 files.

    This test drives production code, so it FLIPS when the folder filter is
    fixed — that is the point. Fixing the bug should turn this red and
    `test_production_existing_keys_sees_every_file` green; update both together.
    """
    keys = _pipeline(live["files"])._existing_keys(DEAL_ID, addr_slug(ADDRESS))
    assert keys == set(), (
        "f1_pipeline.py:123 now matches subfolders — the dedupe defect looks "
        "fixed. Swap the assertion in this pair of tests.")


@pytest.mark.xfail(strict=False, reason=(
    "Cause 1 of the dedupe defect: _existing_keys uses an endswith() folder "
    "filter (f1_pipeline.py:123) so every file in a subfolder is invisible. "
    "Fix that and this passes."))
def test_production_existing_keys_sees_every_file(live):
    keys = _pipeline(live["files"])._existing_keys(DEAL_ID, addr_slug(ADDRESS))
    assert len(keys) == 97


# --- damage already in the project -----------------------------------------

def test_media_lane_wrote_empty_files(live):
    """18 media uploads landed at 0 bytes — including the DXF that F2 consumes."""
    zero = [f["name"] for f in live["files"] if f["size"] == 0]
    assert len(zero) == 18
    assert "1500bayrd_floorplan.dxf" in zero
    assert all(f["documentType"] == "media"
               for f in live["files"] if f["size"] == 0)


def test_binaries_were_base64_chunked_into_txt_parts(live):
    """Violates README architecture rule #1 (no bytes through an LLM context)."""
    parts = [f for f in live["files"]
             if f["name"].endswith(".txt") and "_part" in f["name"]]
    assert len(parts) == 67
    assert sum(p["size"] for p in parts) > 45_000_000


# --- the acceptance target --------------------------------------------------

@pytest.mark.xfail(strict=False, reason=(
    "ACCEPTANCE TARGET for the dedupe fix. Two independent causes: (1) the "
    "endswith() folder filter hides every subfolder file; (2) F1's url-lane "
    "names (1500BayRd_Mesh.glb) do not match the legacy scheme "
    "(1500_bay_rd-_miami_beach-_fl.glb). Both must be fixed. Uploads are "
    "permanent — there is no delete tool."))
def test_url_lane_uploads_dedupe_against_live_inventory(live):
    """Only the url lane is checked, because only its names are knowable.

    Relay-lane members are named `{slug}_{member.name}` from the zip's actual
    contents (f1_pipeline.py:194) — nobody has listed a Polycam zip, so those
    names cannot be predicted here without inventing them.
    """
    from tools.dedupe_dryrun import planned_url_lane_uploads
    slug = addr_slug(ADDRESS)
    existing = _pipeline(live["files"])._existing_keys(DEAL_ID, slug)
    undeduped = [n for n in planned_url_lane_uploads(slug)
                 if _dedupe_key(n, slug) not in existing]
    assert undeduped == [], f"{len(undeduped)} permanent duplicates: {undeduped}"
