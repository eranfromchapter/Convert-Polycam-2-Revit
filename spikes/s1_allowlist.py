"""S1 — extension allowlist probe.

Uploads one TINY but format-plausible file per pending extension to the
reference deal and records accept/reject. Results go to stdout as a markdown
table — paste it into docs/ERROR_DECODER.md and update
config/settings.json → extension_allowlist_verified.

⚠️ Probe files are permanent (no delete). All named _probe_<ext-name>.<ext>.

Usage: python -m spikes.s1_allowlist
"""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from spikes.common import PLANS_FOLDER, REFERENCE_DEAL_ID
from src.hub_client import HubClient, HubError

# Minimal magic-number-correct stubs — HUB validation is extension/content-type
# based, so a few honest header bytes suffice without shipping real scans.
_STUBS: dict[str, bytes] = {
    ".pdf": b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
    ".png": bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd4"
        "0000000049454e44ae426082"),
    ".svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
    ".ply": b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\n"
            b"property float y\nproperty float z\nend_header\n0 0 0\n",
    ".obj": b"# probe\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
    ".mtl": b"# probe\nnewmtl Wall_0\nKd 1 1 1\n",
    ".glb": struct.pack("<4sII", b"glTF", 2, 12),
    ".laz": b"LASF" + b"\x00" * 60,
    ".rvt": bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 56,
}


def main() -> None:
    hub = HubClient()
    results: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for ext, payload in _STUBS.items():
            name = f"_probe_{ext.lstrip('.')}{ext}"
            path = Path(tmp) / name
            path.write_bytes(payload)
            try:
                hub.upload_via_base64(REFERENCE_DEAL_ID, PLANS_FOLDER, name, path)
                results.append((ext, "✅ accepted"))
            except HubError as e:
                results.append((ext, f"❌ rejected — {e.decoded}"))

    print("\n| ext | result |\n|---|---|")
    for ext, res in results:
        print(f"| `{ext}` | {res} |")
    print("\nUpdate config/settings.json extension_allowlist_verified, and ask "
          "the dev to extend the server allowlist for rejected extensions "
          "(DEV_QUESTIONS.md Q4).")


if __name__ == "__main__":
    main()
