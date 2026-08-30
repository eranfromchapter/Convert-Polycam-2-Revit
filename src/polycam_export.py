"""Drive the logged-in Polycam web session with Playwright to harvest signed
export-download URLs — zero bytes through this process for url-lane formats.

Design constraints (learned the expensive way):
- Polycam has no public REST API (confirmed Aug 2026). Exports are per-capture
  UI actions: Download → pick format → Export → browser downloads a signed URL.
- We capture the SIGNED URL from the download/network event and CANCEL the
  local download for url-lane formats; the HUB fetches the URL server-side.
- Zip-container exports (floor-plan "Zip (all)", OBJ w/ textures) must be
  downloaded locally and unpacked, because the HUB rejects .zip — the relay
  lane in f1_pipeline handles their members.
- Selector strategy: text-based lookups only, isolated in this module, because
  UI drift is the #1 risk. The URL-capture mechanism (Playwright's Download
  event) survives redesigns; only the two clicks to reach the menu may need
  retuning. Spike S4 (spikes/s4_polycam_capture.py) is the rerunnable health
  check for exactly that.

Auth: persistent browser profile at POLYCAM_PROFILE_DIR, seeded once by a
human:  python -m src.polycam_export --login
Cookies never leave the profile directory (gitignored).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

from .config import settings

log = logging.getLogger(__name__)

LIBRARY_URL = settings()["polycam"]["library_url"]
CAPTURE_URL = settings()["polycam"]["capture_url_template"]

# Menu labels as they appear in the Polycam download panel. VERIFY IN S4 —
# these are the only strings expected to drift with UI updates.
_EXPORT_BUTTON_TEXTS = ("Download", "Export")


@dataclass
class ExportResult:
    kind: str
    signed_url: str | None       # url lane: handed to the HUB; never logged raw
    local_path: Path | None      # relay lane: downloaded zip for unpacking
    suggested_name: str


def _profile_dir() -> str:
    return os.environ.get("POLYCAM_PROFILE_DIR", "./polycam-profile")


def open_browser(pw, *, headless: bool = True) -> BrowserContext:
    return pw.chromium.launch_persistent_context(
        _profile_dir(),
        headless=headless,
        accept_downloads=True,
        executable_path=os.environ.get("PW_CHROMIUM_PATH") or None,
    )


def interactive_login() -> None:
    """One-time human step: opens a headed browser; log in, then close it.
    The persistent profile keeps the session for headless runs."""
    with sync_playwright() as pw:
        ctx = open_browser(pw, headless=False)
        page = ctx.new_page()
        page.goto(LIBRARY_URL)
        print("Log in to Polycam in the opened browser, wait for the library "
              "to load, then close the window.")
        try:
            page.wait_for_event("close", timeout=0)
        except PWTimeout:
            pass
        ctx.close()


def list_captures(ctx: BrowserContext) -> list[dict]:
    """Scrape the library for {id, name}. Names are property addresses — the
    matching key to HUB deals. Prefers harvesting capture links (stable URL
    shape /capture/<uuid>) over DOM structure."""
    page = ctx.new_page()
    try:
        page.goto(LIBRARY_URL, wait_until="networkidle")
        captures, seen = [], set()
        for a in page.locator("a[href*='/capture/']").all():
            href = a.get_attribute("href") or ""
            m = re.search(r"/capture/([0-9A-Fa-f-]{36})", href)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            name = (a.inner_text() or "").strip().split("\n")[0]
            captures.append({"id": m.group(1), "name": name})
        return captures
    finally:
        page.close()


def _open_download_panel(page: Page) -> None:
    for text in _EXPORT_BUTTON_TEXTS:
        btn = page.get_by_role("button", name=re.compile(text, re.I)).first
        try:
            btn.click(timeout=5000)
            return
        except PWTimeout:
            continue
    raise RuntimeError(
        "Could not find the Download/Export button — Polycam UI drift. "
        "Re-run spikes/s4_polycam_capture.py headed and update the selectors "
        "in src/polycam_export.py."
    )


def trigger_export(ctx: BrowserContext, capture_id: str, export_cfg: dict,
                   download_dir: Path | None = None) -> ExportResult:
    """Trigger one export and either capture its signed URL (url lane, local
    download cancelled) or save the file (relay lane, zips only)."""
    page = ctx.new_page()
    try:
        page.goto(CAPTURE_URL.format(capture_id=capture_id),
                  wait_until="networkidle")
        _open_download_panel(page)
        # Walk the menu path ("Mesh" -> "GLTF", or just "Spatial Report").
        for label in export_cfg["menu_path"]:
            page.get_by_text(re.compile(rf"^{re.escape(label)}", re.I)).first.click(
                timeout=10000)

        with page.expect_download(timeout=180_000) as dl_info:
            # Final confirm button, when the panel has one; harmless if the
            # format click above already started the download.
            try:
                page.get_by_role("button", name=re.compile("export|download", re.I)) \
                    .last.click(timeout=5000)
            except PWTimeout:
                pass
        download = dl_info.value
        signed_url = download.url
        name = download.suggested_filename

        if export_cfg["lane"] == "url":
            download.cancel()  # zero bytes locally — the HUB fetches server-side
            return ExportResult(kind=export_cfg["kind"], signed_url=signed_url,
                                local_path=None, suggested_name=name)

        # relay lane: zips the HUB rejects — keep the bytes, script-side only
        download_dir = download_dir or Path("tmp")
        download_dir.mkdir(parents=True, exist_ok=True)
        target = download_dir / name
        download.save_as(target)
        return ExportResult(kind=export_cfg["kind"], signed_url=signed_url,
                            local_path=target, suggested_name=name)
    finally:
        page.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Polycam session utilities")
    parser.add_argument("--login", action="store_true",
                        help="seed the persistent profile interactively (headed)")
    parser.add_argument("--list", action="store_true",
                        help="print the capture library as id<TAB>name")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.login:
        interactive_login()
        return
    if args.list:
        with sync_playwright() as pw:
            ctx = open_browser(pw)
            for cap in list_captures(ctx):
                print(f"{cap['id']}\t{cap['name']}")
            ctx.close()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
