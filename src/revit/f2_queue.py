"""F2 queue: runs on the Revit workstation (scheduled task / on-login), picks
up deals whose F1 is complete but which have no .rvt yet, and builds one.

Queued-not-instant by design: the AUTOM8LABS route needs Revit OPEN on this
machine. The manifest is the queue — nothing is lost while the workstation is
off, only delayed.

Per-deal job:
  1. find_project_documents → locate the F1 outputs (mesh GLB, floor-plan DXF,
     RoomData.csv) and download them via their tempauth URLs (deterministic
     script fetch; URLs are used, never logged or persisted).
  2. scan_to_dxf: GLB → Z-up/feet 3D-face DXF alongside the untouched Polycam
     floor-plan DXF.
  3. build_rvt: replay the proven AUTOM8LABS recipe → <Slug>_Existing.rvt +
     QA screenshot pair.
  4. File the .rvt into HUB → "Revit": url mode once the dev's Graph-auth fix
     lands (Q3); until then direct POST (Q1), else flag manual (~6.5 MB rvt is
     over the 3 MB contentBase64 cap).
  5. QA screenshots (small .png/.jpg) uploaded next to it; paths recorded in
     the manifest.

Usage: python -m src.revit.f2_queue [--deal DEAL_ID]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import requests

from ..alerts import alert
from ..config import settings, state_path
from ..f1_pipeline import addr_slug
from ..hub_client import HubClient, BASE64_MAX
from ..manifest import Manifest
from .build_rvt import RevitJob, ceiling_height_from_csv
from .scan_to_dxf import convert as scan_to_dxf

log = logging.getLogger(__name__)

REVIT_FOLDER = settings()["hub"]["folders"]["revit"]


def _download(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(target, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    return target


def _pick(docs, *needles: str):
    for d in docs:
        n = d.name.lower()
        if all(s in n for s in needles):
            return d
    return None


def run_deal(hub: HubClient, manifest: Manifest, deal_id: str) -> bool:
    d = manifest.deal(deal_id)
    address = d.get("address") or deal_id
    slug = addr_slug(address)
    workdir = Path(settings()["f2"]["workdir_template"].format(addr_slug=slug))
    dxf_dir = workdir / "dxf"

    docs = hub.find_project_documents(deal_id, "")
    glb = _pick(docs, ".glb")
    plan_dxf = _pick(docs, ".dxf")   # Polycam floor-plan DXF from F1
    room_csv = _pick(docs, "roomdata", ".csv") or _pick(docs, ".csv")
    if not glb or not plan_dxf:
        alert(f"F2 {address}: F1 outputs incomplete in HUB "
              f"(glb={bool(glb)}, dxf={bool(plan_dxf)}) — skipping")
        return False

    glb_path = _download(glb.download_url, workdir / "src" / glb.name)
    _download(plan_dxf.download_url, dxf_dir / plan_dxf.name)
    ceiling_ft = 8.0
    if room_csv:
        csv_path = _download(room_csv.download_url, workdir / "src" / room_csv.name)
        ceiling_ft = ceiling_height_from_csv(csv_path)

    scan_to_dxf(glb_path, dxf_dir / f"{slug}_Scan.dxf")

    job = RevitJob(slug, workdir, dxf_dir, ceiling_ft)
    rvt = job.run()

    # QA screenshots are small — base64 lane is fine.
    for shot in job.screenshots:
        hub.upload_via_base64(deal_id, REVIT_FOLDER, shot.name, shot,
                              document_type="media")
        manifest.record(deal_id, shot.name, "uploaded", size=shot.stat().st_size,
                        folder=REVIT_FOLDER)

    rvt_size = rvt.stat().st_size
    if rvt_size <= BASE64_MAX:
        hub.upload_via_base64(deal_id, REVIT_FOLDER, rvt.name, rvt,
                              document_type="media")
        manifest.record(deal_id, rvt.name, "uploaded", size=rvt_size,
                        folder=REVIT_FOLDER)
    else:
        # ~6.5 MB typical: needs the dev's direct POST endpoint (Q1) or
        # Graph-auth'd url mode (Q3). The model itself is built and safe on
        # disk — only the HUB filing is pending.
        manifest.record(deal_id, rvt.name, "flagged_manual", size=rvt_size,
                        folder=REVIT_FOLDER,
                        error=f"rvt at {rvt} awaiting upload path (Q1/Q3)")
        alert(f"F2 {address}: {rvt.name} built ({rvt_size} bytes) but exceeds "
              f"the 3 MB base64 cap — needs dev upload endpoint (Q1/Q3)")

    manifest.mark_f2_complete(deal_id)
    log.info("F2 complete for %s -> %s", address, rvt)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", help="process one specific dealId")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    hub = HubClient()
    manifest = Manifest(state_path("manifest"))
    pending = [args.deal] if args.deal else manifest.f1_complete_pending_f2()
    if not pending:
        log.info("F2 queue empty")
        return
    for deal_id in pending:
        try:
            run_deal(hub, manifest, deal_id)
        except Exception as e:
            alert(f"F2 job for {deal_id} failed — {e}")


if __name__ == "__main__":
    main()
