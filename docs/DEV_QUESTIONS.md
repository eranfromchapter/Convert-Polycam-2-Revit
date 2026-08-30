# Open questions for the HUB dev

Status legend: ⬜ open · ✅ answered (record the answer inline) · ❌ won't do

1. ⬜ **Upload endpoint + key** — exact HTTPS endpoint(s) + `HUB_API_KEY` for
   direct script POST uploads, and the content rules (multipart?
   `contentBase64` ≤3 MB decoded only?). Needed wherever `url:` mode can't
   reach a file (zip-relay lane, workstation `.rvt`). Fill the real paths into
   `config/settings.json → hub.endpoints` (replace the `PLACEHOLDER:` values).
2. ⬜ **Webhook on deal→ACCEPTED** — emit a POST/queue event on the status
   transition? If yes: payload shape + target. If no: we poll hourly
   (`src/f1_watch.py`, already built as the fallback).
3. ⬜ **Graph-auth on `url:` fetch** — attach the SharePoint/Graph credential
   the HUB already uses for its own storage when fetching a `url:` upload.
   Unlocks direct OneDrive→HUB transfers (source files in "Chapter Project
   1500", and the F2 `.rvt` handoff). Feasible? ETA?
4. ⬜ **Extension allowlist** — after spike S1, add whichever of
   `.pdf .glb .obj .mtl .ply .laz .rvt .png .svg` the server rejects.
5. ⬜ **"Revit" folder** — if spike S2 shows `folderPath:"Revit"` does not
   auto-create, add "Revit" to the project folder template.
6. ⬜ **⚠️ Blob privacy** — binary/`media` uploads land in
   `chapterdev.blob.core.windows.net/public/<dealId>/…`: a PUBLIC container on
   a DEV account, holding client property scans. Make it private (or route
   media to SharePoint like documents) **before** we batch 100+ projects.
7. ⬜ **Cleanup** — delete via SharePoint the 4 junk test files in
   1500 Bay Rd #1462 → Plans and 3D:
   `_typetest.dxf`, `_urltest.dxf`, `_keytest.dxf`, `_polycamtest.jpg`.

## For Eran (not the dev)

- Confirm the runner location: plan assumes the Chapter Revit workstation runs
  everything (Task Scheduler, hourly).
- Someone does the one-time interactive Polycam login on the runner to seed the
  persistent browser profile: `python -m src.polycam_export --login`.
