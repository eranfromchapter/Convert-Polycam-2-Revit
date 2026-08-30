# Convert-Polycam-2-Revit

Chapter's automation for filing Polycam scan data into the HUB and generating
Revit existing-conditions projects, triggered by contract signing.

| Function | What it does | Entry point |
|---|---|---|
| **F1** | Deal hits ACCEPTED ("Signed") → matched Polycam capture's export set → HUB project "Plans and 3D" | `python -m src.f1_watch` (hourly) |
| **F2** | After F1: build `<Addr>_Existing.rvt` on the Revit workstation → HUB "Revit" | `python -m src.revit.f2_queue` |
| **F3** | One-time backfill for all active projects (~100+), resumable | `python -m src.f3_backfill …` |

## Architecture rules (non-negotiable)

1. **File bytes never pass through an LLM context.** Bytes move server-side
   (signed Polycam CDN URL → HUB `url:` fetch, ≤50 MB) or inside these worker
   scripts. No base64 in tool calls, ever.
2. **MCP/metadata vs HTTP/payloads.** Deal listing, existence checks and
   verification are metadata; uploads are `url:` mode or direct HTTP.
3. **Idempotent + resumable.** `state/manifest.json` checkpoints every file;
   re-runs skip done rows; existence is checked before every upload — there is
   **no delete tool**, duplicates are permanent.
4. **Human gate only where judgment is real:** the one-time matching-table
   review, and per-file >50 MB exceptions.
5. **Fail loudly with decoded errors** — the HUB's messages mislead; see
   [docs/ERROR_DECODER.md](docs/ERROR_DECODER.md) (memorize the two-ID trap).

## Setup (runner = the Chapter Revit workstation)

```bash
pip install -r requirements.txt
playwright install chromium          # skip on the Claude remote container
cp .env.example .env                 # fill in HUB_BASE_URL / HUB_API_KEY (dev, Q1)
python -m src.polycam_export --login # one-time human Polycam login (headed)
```

Then fill the real endpoint paths into `config/settings.json → hub.endpoints`
(they ship as `PLACEHOLDER:` and every call refuses to run until replaced —
values come from the dev, [docs/DEV_QUESTIONS.md](docs/DEV_QUESTIONS.md)).

## Phase 0 — run the spikes first

```bash
python -m spikes.s1_allowlist          # extension probe (permanent files! read the header)
python -m spikes.s2_revit_folder       # does folderPath:"Revit" auto-create?
python -m spikes.s3_deal_polling       # ACCEPTED-deal fields & polling shape
python -m spikes.s4_polycam_capture --headed   # export URL harvest dry run / UI health check
python -m spikes.s6_manifest_roundtrip # duplicate-detection dry run (no uploads)
```

S5 is the dev-confirmation list in `docs/DEV_QUESTIONS.md`. Update
`config/settings.json → extension_allowlist_verified` with S1's results.

## Scheduling

Windows Task Scheduler on the workstation:

- hourly: `python -m src.f1_watch` (poll ACCEPTED deals, diff, run F1)
- hourly (offset 30 min) or at logon with Revit open:
  `python -m src.revit.f2_queue` (F2 jobs queue off the manifest — nothing is
  lost while the workstation is off, only delayed)

If the dev ships the deal→ACCEPTED webhook (Q2), point its handler at
`f1_watch.run_new_deals(only_deal_ids={...})`; the pipeline is trigger-agnostic.

## F3 backfill runbook

```bash
python -m src.f3_backfill build-table   # deals × Polycam library → state/matching_review.csv
# Eran reviews rows with blank 'approved' (confidence < 0.9), sets yes/no,
# saves the file as state/matching_table.csv — once.
python -m src.f3_backfill run --pilot 3   # includes the reference project
python -m src.f3_backfill run --pilot 10
python -m src.f3_backfill run --all       # nightly, resumable — re-run to retry
python -m src.f3_backfill report          # per-project counts, failures, bytes
```

## Reference project (end-to-end test)

Deal **"1500 Bay Rd #1462"**, dealId `db3a0a03-32fd-4a26-8aef-a644e93563ad`,
capture `E4E8C071-9901-4F51-81A7-3D89B665E391`. Its "Plans and 3D" already
holds `RoomData.csv` + a notes `.txt` — F1 must detect and skip them
(dedupe strips the address prefix and compares case-insensitively; proven in
`tests/test_f1_dedupe.py` and dry-run by `spikes.s6_manifest_roundtrip`).

## Tests

```bash
python -m pytest tests/ -q
```

Covers: address normalization/fuzzy matching, manifest idempotency and
tempauth-URL redaction, the error decoder, dedupe keys, documentType routing.
The scan→DXF converter is smoke-testable offline:
`python -m src.revit.scan_to_dxf <mesh.glb> <out.dxf>`.

## Layout

```
src/            pipeline modules (F1/F3) + revit/ (F2)
spikes/         Phase-0 probes, kept rerunnable as health checks
config/         non-secret settings (folders, endpoints, export formats)
docs/           ERROR_DECODER.md, DEV_QUESTIONS.md
state/          gitignored runtime state: manifest, seen-deals, match tables
tests/          offline unit tests
```
