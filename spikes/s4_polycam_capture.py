"""S4 — Polycam export network-capture dry run (and UI-drift health check).

Against ONE capture (the reference), triggers every configured export and
reports per format: signed URL host+path (query REDACTED), anonymous
fetchability (what the HUB's url-mode fetch needs), and content-length.
No HUB uploads, no bytes kept (url-lane downloads are cancelled; relay-lane
zips land in a temp dir that is deleted).

Run --headed the first time (and whenever Polycam ships a redesign) to watch
the clicks and retune the menu labels in config/settings.json /
src/polycam_export.py.

Usage: python -m spikes.s4_polycam_capture [--headed] [--capture CAPTURE_ID]
"""
from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from spikes.common import REFERENCE_CAPTURE_ID
from src.config import settings
from src.hub_client import is_anonymously_fetchable, redact_url, remote_size
from src import polycam_export


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--capture", default=REFERENCE_CAPTURE_ID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    tmpdir = Path(tempfile.mkdtemp(prefix="s4_"))
    rows = []
    try:
        with sync_playwright() as pw:
            ctx = polycam_export.open_browser(pw, headless=not args.headed)
            try:
                for cfg in settings()["polycam"]["exports"]:
                    try:
                        r = polycam_export.trigger_export(
                            ctx, args.capture, cfg, download_dir=tmpdir)
                        size = remote_size(r.signed_url)
                        anon = is_anonymously_fetchable(r.signed_url)
                        rows.append((cfg["kind"], "✅",
                                     f"anon={'✅' if anon else '❌'}",
                                     f"{size or '?'} bytes",
                                     redact_url(r.signed_url)))
                    except Exception as e:
                        rows.append((cfg["kind"], "❌", "", "", str(e)[:120]))
            finally:
                ctx.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n| export | triggered | HUB-fetchable | size | url (redacted) |")
    print("|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print("\nAny ❌ triggered → UI drift: re-run --headed and retune the menu "
          "labels. Any anon=❌ → that format can't use url mode; it must go "
          "through the relay lane.")


if __name__ == "__main__":
    main()
