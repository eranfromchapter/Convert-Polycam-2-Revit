"""S6 — manifest round-trip + duplicate-detection dry run on the reference
project. NO uploads: proves that the pre-existing files in "Plans and 3D"
(RoomData.csv, the notes .txt) would be detected and skipped by F1, and that
a scratch manifest skips completed rows on re-run.

Usage: python -m spikes.s6_manifest_roundtrip
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from spikes.common import REFERENCE_ADDRESS, REFERENCE_DEAL_ID
from src.f1_pipeline import addr_slug, _dedupe_key
from src.hub_client import HubClient
from src.manifest import Manifest


def main() -> None:
    hub = HubClient()
    slug = addr_slug(REFERENCE_ADDRESS)
    docs = hub.find_project_documents(REFERENCE_DEAL_ID, "")
    print(f"{len(docs)} documents on the reference deal:")
    existing = set()
    for d in docs:
        key = _dedupe_key(d.name, slug)
        existing.add(key)
        print(f"  {d.folder}/{d.name} ({d.size} bytes) -> dedupe key {key!r}")

    # The acceptance case: our prefixed upload names must collide with the
    # unprefixed pre-existing files.
    for candidate in (f"{slug}_RoomData.csv", "RoomData.csv"):
        hit = _dedupe_key(candidate, slug) in existing
        print(f"would F1 skip {candidate!r}? {'✅ yes' if hit else '❌ NO — bug'}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        m = Manifest(path)
        m.record(REFERENCE_DEAL_ID, f"{slug}_Mesh.glb", "verified", size=500_000)
        m2 = Manifest(path)  # simulate a fresh process resuming
        assert m2.is_done(REFERENCE_DEAL_ID, f"{slug}_Mesh.glb"), \
            "re-loaded manifest lost the verified row"
        print("✅ manifest round-trip: verified row survives reload and skips")


if __name__ == "__main__":
    main()
