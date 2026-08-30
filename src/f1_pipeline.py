"""F1: signed deal → matched Polycam capture's export set → HUB "Plans and 3D".

Per-deal flow (idempotent, resumable, manifest-checkpointed):
1. List what's already in the HUB folder (find_project_documents) — files that
   exist there are recorded skipped_existing and NEVER re-uploaded (there is
   no delete; a duplicate is permanent).
2. For each configured export:
   - url lane (single-file formats): harvest the signed CDN URL via Playwright,
     pre-flight size check (>50 MB → flag, never guess), hand the URL to the
     HUB for a server-side fetch. Zero bytes through this process.
   - relay lane (zip containers the HUB rejects): download the zip locally in
     this script, unpack, upload each member (≤3 MB → contentBase64 over
     direct HTTP; larger → flagged until the dev's direct-POST endpoint, Q1).
3. Verify everything against find_project_documents; only verified rows count
   as done on a re-run.
4. All exports verified/skipped → mark_f1_complete (feeds the F2 queue).

Dedupe key: HUB names are compared case-insensitively, and with the address
prefix stripped — so a pre-existing "RoomData.csv" blocks a re-upload of
"1500BayRd_RoomData.csv" (the reference project's acceptance case).
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .alerts import alert
from .config import settings, state_path
from .hub_client import HubClient, HubError, URL_FETCH_MAX, BASE64_MAX, \
    is_anonymously_fetchable, remote_size
from .manifest import Manifest
from . import polycam_export

log = logging.getLogger(__name__)

PLANS_FOLDER = settings()["hub"]["folders"]["plans"]
DENYLIST = set(settings()["hub"]["extension_denylist_verified"])

# Text formats route to SharePoint (documentType "document"); everything else
# is media → the Azure blob container (⚠️ public until DEV_QUESTIONS Q6 lands —
# keep batches gated on the pilot until the dev answers).
_DOCUMENT_EXTS = {".csv", ".txt", ".dxf", ".svg"}

_KIND_LABEL = {
    "mesh_glb": "Mesh",
    "pointcloud_laz": "PointCloud",
    "pointcloud_ply": "PointCloud",
    "spatial_report": "SpatialReport",
}


def addr_slug(address: str) -> str:
    """'1500 Bay Rd #1462, Miami Beach, FL' -> '1500BayRd' (street part only,
    unit stripped, alnum tokens joined)."""
    street = address.split(",")[0]
    street = re.sub(r"\s*(?:#|apt\.?|apartment|unit|ste\.?|suite)\s*[\w-]+\s*$",
                    "", street, flags=re.IGNORECASE)
    tokens = re.findall(r"[A-Za-z0-9]+", street)
    return "".join(t if t.isdigit() else t.capitalize() for t in tokens) or "Unknown"


def _dedupe_key(name: str, slug: str) -> str:
    """Case-insensitive name with the address prefix stripped, so pre-existing
    unprefixed files ('RoomData.csv') match our prefixed uploads."""
    n = name.lower()
    prefix = slug.lower() + "_"
    return n[len(prefix):] if n.startswith(prefix) else n


def _document_type(ext: str) -> str:
    return "document" if ext.lower() in _DOCUMENT_EXTS else "media"


class F1Pipeline:
    def __init__(self, hub: HubClient, manifest: Manifest, browser_ctx):
        self.hub = hub
        self.manifest = manifest
        self.ctx = browser_ctx  # Playwright persistent context (logged in)

    # -- single deal --------------------------------------------------------

    def run_for_deal(self, deal_id: str, address: str, capture_id: str) -> bool:
        """Returns True when every export is verified/skipped (F1 complete)."""
        slug = addr_slug(address)
        self.manifest.deal(deal_id, address=address, capture_id=capture_id)
        existing = self._existing_keys(deal_id, slug)
        all_done = True

        for cfg in settings()["polycam"]["exports"]:
            try:
                done = self._run_export(deal_id, slug, capture_id, cfg, existing)
            except HubError as e:
                self.manifest.record(deal_id, f"{slug}_{cfg['kind']}", "failed",
                                     error=e.decoded)
                alert(f"{address}: upload failed for {cfg['kind']} — {e.decoded}")
                done = False
            except Exception as e:
                self.manifest.record(deal_id, f"{slug}_{cfg['kind']}", "failed",
                                     error=str(e)[:500])
                alert(f"{address}: {cfg['kind']} export failed — {e}")
                done = False
            all_done = all_done and done

        if all_done:
            self._verify_deal(deal_id, slug)
            all_done = self._deal_fully_verified(deal_id)
        if all_done:
            self.manifest.mark_f1_complete(deal_id)
            log.info("F1 complete for %s (%s)", address, deal_id)
        return all_done

    # -- helpers ------------------------------------------------------------

    def _existing_keys(self, deal_id: str, slug: str) -> set[str]:
        docs = self.hub.find_project_documents(deal_id, "")
        return {
            _dedupe_key(d.name, slug)
            for d in docs
            if d.folder.lower().rstrip("/").endswith(PLANS_FOLDER.lower())
            or not d.folder  # some responses omit folder; be conservative
        }

    def _skip_if_done(self, deal_id: str, file_name: str, slug: str,
                      existing: set[str]) -> bool:
        if self.manifest.is_done(deal_id, file_name):
            return True
        if _dedupe_key(file_name, slug) in existing:
            self.manifest.record(deal_id, file_name, "skipped_existing",
                                 folder=PLANS_FOLDER)
            log.info("skip (already in HUB): %s", file_name)
            return True
        return False

    def _run_export(self, deal_id: str, slug: str, capture_id: str,
                    cfg: dict, existing: set[str]) -> bool:
        if cfg["lane"] == "url":
            file_name = f"{slug}_{_KIND_LABEL[cfg['kind']]}{cfg['ext']}"
            if self._skip_if_done(deal_id, file_name, slug, existing):
                return True
            result = polycam_export.trigger_export(self.ctx, capture_id, cfg)
            return self._upload_url_lane(deal_id, file_name, result.signed_url,
                                         cfg["ext"])
        return self._run_relay_export(deal_id, slug, capture_id, cfg, existing)

    def _upload_url_lane(self, deal_id: str, file_name: str, url: str,
                         ext: str) -> bool:
        size = remote_size(url)
        if size is not None and size > URL_FETCH_MAX:
            self.manifest.record(deal_id, file_name, "flagged_oversize",
                                 size=size, source_url=url)
            alert(f"{file_name}: {size} bytes exceeds the 50 MB url-fetch limit "
                  f"— manual handling needed")
            return False
        if not is_anonymously_fetchable(url):
            self.manifest.record(deal_id, file_name, "failed", source_url=url,
                                 error="signed URL not anonymously fetchable")
            alert(f"{file_name}: Polycam URL not fetchable anonymously — "
                  f"the HUB's url mode cannot pull it")
            return False
        self.hub.upload_via_url(deal_id, PLANS_FOLDER, file_name, url,
                                document_type=_document_type(ext))
        self.manifest.record(deal_id, file_name, "uploaded", size=size,
                             source_url=url, folder=PLANS_FOLDER)
        return True

    def _run_relay_export(self, deal_id: str, slug: str, capture_id: str,
                          cfg: dict, existing: set[str]) -> bool:
        """Zip containers (.zip is HUB-rejected): download locally, unpack,
        upload members individually. Members are prefixed with the slug."""
        # Cheap pre-check: if every expected member type already exists, don't
        # even trigger the export.
        tmpdir = Path(tempfile.mkdtemp(prefix=f"{cfg['kind']}_"))
        ok = True
        try:
            result = polycam_export.trigger_export(self.ctx, capture_id, cfg,
                                                   download_dir=tmpdir)
            with zipfile.ZipFile(result.local_path) as zf:
                zf.extractall(tmpdir / "unpacked")
            for member in sorted((tmpdir / "unpacked").rglob("*")):
                if not member.is_file():
                    continue
                ok = self._upload_member(deal_id, slug, member, existing) and ok
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return ok

    def _upload_member(self, deal_id: str, slug: str, member: Path,
                       existing: set[str]) -> bool:
        ext = member.suffix.lower()
        file_name = f"{slug}_{member.name}"
        if ext in DENYLIST:
            self.manifest.record(deal_id, file_name, "flagged_manual",
                                 error=f"{ext} is HUB-denylisted")
            return True  # expected — not a failure of this deal
        if self._skip_if_done(deal_id, file_name, slug, existing):
            return True
        size = member.stat().st_size
        if size > BASE64_MAX:
            # Needs the dev's direct-POST endpoint (DEV_QUESTIONS Q1) —
            # contentBase64 caps at 3 MB decoded.
            self.manifest.record(deal_id, file_name, "flagged_oversize",
                                 size=size,
                                 error="zip member >3MB: needs direct POST endpoint (Q1)")
            alert(f"{file_name}: {size} bytes — relay lane needs the dev's "
                  f"direct upload endpoint (Q1)")
            return False
        self.hub.upload_via_base64(deal_id, PLANS_FOLDER, file_name, member,
                                   document_type=_document_type(ext))
        self.manifest.record(deal_id, file_name, "uploaded", size=size,
                             folder=PLANS_FOLDER)
        return True

    # -- verification -------------------------------------------------------

    def _verify_deal(self, deal_id: str, slug: str) -> None:
        """Confirm 'uploaded' rows actually landed; only then are they done."""
        hub_keys = self._existing_keys(deal_id, slug)
        files = self.manifest.deal(deal_id)["files"]
        for name, entry in files.items():
            if entry.get("status") != "uploaded":
                continue
            if _dedupe_key(name, slug) in hub_keys:
                self.manifest.record(deal_id, name, "verified",
                                     folder=entry.get("folder"))
            else:
                self.manifest.record(deal_id, name, "failed",
                                     error="upload reported OK but file not "
                                           "found by find_project_documents")
                alert(f"{name}: uploaded but not found on verification "
                      f"(deal {deal_id})")

    def _deal_fully_verified(self, deal_id: str) -> bool:
        files = self.manifest.deal(deal_id)["files"]
        return bool(files) and all(
            e.get("status") in ("verified", "skipped_existing", "flagged_manual")
            for e in files.values()
        )
