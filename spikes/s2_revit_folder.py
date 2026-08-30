"""S2 — does folderPath:"Revit" auto-create?

The project folder template has no "Revit" folder. One tiny .txt upload tells
us whether folderPath resolves-or-creates, or 4xxs (→ dev adds the folder to
the template, DEV_QUESTIONS.md Q5).

Usage: python -m spikes.s2_revit_folder
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from spikes.common import REFERENCE_DEAL_ID
from src.hub_client import HubClient, HubError


def main() -> None:
    hub = HubClient()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "_probe_revit_folder.txt"
        path.write_text("folder auto-create probe — safe to delete\n")
        try:
            hub.upload_via_base64(REFERENCE_DEAL_ID, "Revit",
                                  "_probe_revit_folder.txt", path,
                                  document_type="document")
            print("✅ folderPath:'Revit' worked — folder auto-created or already "
                  "exists. Verify placement in SharePoint, then close Q5.")
        except HubError as e:
            print(f"❌ rejected: {e}\n→ ask the dev to add 'Revit' to the "
                  f"project folder template (DEV_QUESTIONS.md Q5).")


if __name__ == "__main__":
    main()
