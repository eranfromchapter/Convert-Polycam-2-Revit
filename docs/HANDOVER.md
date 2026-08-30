# Handover — Convert-Polycam-2-Revit

**Prepared:** 2026-08-30 · **Repo:** https://github.com/eranfromchapter/Convert-Polycam-2-Revit
**Owner (outgoing):** Eran Polack · **Status:** feature-complete in code, **not deployed, not safe to run against live data yet**

---

## 1. Read this first

The code is written and its unit tests pass. It has **never run in production**, and there is one
defect that must be fixed before it does:

> **Do not point F1 or F3 at a live HUB project until the dedupe defect in §5 is fixed.**
> The HUB has **no delete API**. Every duplicate this system creates is permanent and can only be
> removed by a developer manually cleaning SharePoint.

That single sentence is the most important thing in this document.

---

## 2. What the system does

Chapter's PMs take Polycam 3D scans of a property. When the client signs, someone has to file that
scan into the HUB project and hand the architect a Revit model of existing conditions. This
automates both.

| | What it does | Entry point | Trigger |
|---|---|---|---|
| **F1** | Deal hits `ACCEPTED` ("Signed") → find the matching Polycam capture → pull its export set → file into the HUB project's **"Plans and 3D"** folder | `python -m src.f1_watch` | hourly (Task Scheduler) |
| **F2** | After F1: build `<Addr>_Existing.rvt` on the Revit workstation → file into HUB **"Revit"** | `python -m src.revit.f2_queue` | hourly, +30 min offset |
| **F3** | One-time backfill across all ~100+ active projects, resumable | `python -m src.f3_backfill …` | manual / nightly |

"Production" is **not a web service**. It is three scheduled Python jobs on the **Chapter Revit
workstation** (Windows Task Scheduler). There is no URL to open; you observe it through the HUB
Files tab and `state/manifest.json`.

---

## 3. Getting it running locally (verified 2026-08-30)

This works from a clean checkout — I ran it end to end in a fresh virtualenv:

```bash
git clone https://github.com/eranfromchapter/Convert-Polycam-2-Revit
cd Convert-Polycam-2-Revit
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest tests/ -q          # => 30 passed, 2 xfailed
```

The two `xfailed` are deliberate — they track the dedupe defect (§5) and move when you fix it.

Two more steps are needed before anything can touch the HUB, and **neither can be done without
Eran or the HUB dev**:

```bash
cp .env.example .env                  # HUB_BASE_URL is already filled in; add HUB_API_KEY <-- you must be given this
python -m src.polycam_export --login  # one-time interactive Polycam login, on the runner machine
playwright install chromium           # once per machine
```

You can do real work before those land: everything in §5 and the whole test suite runs offline.

### Offline things you can run today

```bash
# Reproduce the blocking defect against a real 97-file HUB snapshot (no HUB access needed)
python -m tools.dedupe_dryrun tests/fixtures/live_hub_inventory_1500bayrd.json

# Smoke-test the scan converter on any mesh
python -m src.revit.scan_to_dxf <mesh.glb> <out.dxf>
```

---

## 4. What is done vs. what is not

| Area | State | Notes |
|---|---|---|
| Address matching (`src/matching.py`) | **Done** | rapidfuzz, USPS abbreviations, unit extraction, human-review CSV. Tested. |
| Manifest / idempotency (`src/manifest.py`) | **Done** | Atomic writes, resumable, redacts signed URLs. Tested. |
| HUB error decoding (`src/hub_client.py`) | **Done** | See `docs/ERROR_DECODER.md`. Worth reading before you debug anything. |
| Scan → DXF (`src/revit/scan_to_dxf.py`) | **Runs, one real bug** | Converts a mesh to a layered DXF (`Scan-Walls/Floors/Ceilings/Objects`, AC1015, feet) — but it **drops scene-graph node transforms**; see §6d. |
| F1 pipeline (`src/f1_pipeline.py`) | **Written, defective** | Dedupe is broken — §5. |
| F1 watcher (`src/f1_watch.py`) | **Written, unrun** | Polling fallback; webhook path stubbed pending Q2. |
| F3 backfill (`src/f3_backfill.py`) | **Written, unrun** | Depends on the same defective dedupe. |
| F2 queue + Revit build (`src/revit/`) | **Written, unverified** | `build_rvt.py` replays a recipe that worked once through the AUTOM8LABS connector by hand. Every MCP tool call is marked `VERIFY` — parameter names have **not** been confirmed programmatically. Needs the workstation. |
| Polycam export capture (`src/polycam_export.py`) | **Written, unverified** | Playwright scraping of poly.cam. Brittle by nature: it breaks whenever Polycam changes its UI. |
| HUB endpoints (`config/settings.json`) | **BLOCKED** | All five are `PLACEHOLDER:` — §6. |
| Deployment | **Not started** | No scheduled tasks, no runner set up, no monitoring. |

The Phase-0 spikes in `spikes/` are written and rerunnable as health checks, but **S1 has a
permanent side effect** — it uploads probe files that cannot be deleted. Read its header before
running it.

---

## 5. ⚠️ The blocking defect: F1's dedupe does not work

I ran F1's real dedupe code against the live contents of the reference project
(`1500 Bay Rd #1462`, dealId `db3a0a03-32fd-4a26-8aef-a644e93563ad`) on 2026-08-30.

**Result: F1's dedupe is blind to all 97 files, and every url-lane upload it plans would be
created as a new, permanent file.**

```
dedupe set — production _existing_keys : 0 of 97 files
dedupe set — descendant-aware          : 97 of 97 files
4 of 4 url-lane uploads would still be created with the folder filter fixed.
```

**On the numbers.** Four is the *confirmed* count, not the total. Only the url lane has
deterministic names (`{slug}_{KIND_LABEL}{ext}`, `f1_pipeline.py:139`). The relay lane's eight
members are named `{slug}_{member.name}` from the Polycam zip's real contents
(`f1_pipeline.py:194`), and **nobody has listed one of those zips**, so their names are unknown.
An earlier draft of this report guessed them and quoted "12 duplicates" — that was wrong, and the
guess cut both ways: if the floorplan zip contains `RoomData.csv`, F1 would upload
`1500BayRd_RoomData.csv`, whose dedupe key `roomdata.csv` *matches* the live
`1500bayrd_roomdata.csv` and would dedupe correctly. **Listing one Polycam export zip is the
cheapest way to close this**, and it is the first thing to do after fixing Cause 1.

Reproduce it yourself, offline:

```bash
python -m tools.dedupe_dryrun tests/fixtures/live_hub_inventory_1500bayrd.json
```

There are **two independent causes**. Fixing only the first is not enough.

### Cause 1 — the folder filter never matches

`src/f1_pipeline.py:123` keeps a file only if its folder *ends with* `"Plans and 3D"`:

```python
if d.folder.lower().rstrip("/").endswith(PLANS_FOLDER.lower())
```

But the live folders are `/Plans and 3D/Polycam` (66 files) and `/Plans and 3D/Revit` (31 files).
Those end with `polycam` and `revit`. **Nothing sits directly in `Plans and 3D`**, so the dedupe
set comes back empty and every file reads as new.

Note the related inconsistency: uploads target `folderPath="Plans and 3D"`
(`f1_pipeline.py:164,211`) while the real content lives one level deeper. The read path and the
write path disagree about the folder tree. Decide which is correct — probably uploading into
`Plans and 3D/Polycam` — and make both sides agree.

### Cause 2 — the naming schemes don't match

Even with a descendant-aware filter (all 97 files then visible), **none of the four url-lane
uploads dedupe**, because `_dedupe_key` compares against names this code never generated. These
four rows are verifiable — the names are deterministic:

| F1 would upload | its dedupe key | closest live file | its key |
|---|---|---|---|
| `1500BayRd_Mesh.glb` | `mesh.glb` | `1500_bay_rd-_miami_beach-_fl.glb` | *(prefix isn't the slug at all)* |
| `1500BayRd_PointCloud.laz` | `pointcloud.laz` | `1500bayrd_pointcloud_laz_part01.txt` | `pointcloud_laz_part01.txt` |
| `1500BayRd_PointCloud.ply` | `pointcloud.ply` | `1500bayrd_pointcloud_ply_part01.txt` | `pointcloud_ply_part01.txt` |
| `1500BayRd_SpatialReport.pdf` | `spatialreport.pdf` | `1500bayrd_spatialreport_part01_pages01-06.pdf` | `spatialreport_part01…` |

The eight relay-lane members are **not** in this table on purpose — their names come from inside
the Polycam zip and are unknown until someone lists one.

The existing 97 files were put there by an **earlier, different process** using a different naming
convention. You must decide: teach `_dedupe_key` to recognise the legacy scheme, or accept the
existing files as canonical and change what F1 generates.

### Acceptance target

`tests/test_live_inventory_dedupe.py` drives the **real** `F1Pipeline._existing_keys` through a
stub HUB client, so production changes move the results. Current state on the unfixed code is
`30 passed, 2 xfailed`. What to expect as you work:

| Test | now | after fixing Cause 1 | after fixing both |
|---|---|---|---|
| `test_production_existing_keys_is_blind_to_the_whole_project` | passes | **fails** (deliberately — flip it) | fails |
| `test_production_existing_keys_sees_every_file` | xfail | xpass | xpass |
| `test_url_lane_uploads_dedupe_against_live_inventory` | xfail | xfail | **passes** |

The last row is the acceptance target. The first row is a tripwire that fails *on purpose* the
moment the folder filter is fixed — swap its assertion then, and it stops you from "fixing" the
defect without noticing.

An earlier draft of this file reimplemented `_existing_keys` inside the test instead of calling
it, which meant fixing the production bug changed no test result at all. If you ever add a test
here, drive the real method.

---

## 6. Other findings from the live data

6a–6c were observed in the same 2026-08-30 snapshot (`tests/fixtures/live_hub_inventory_1500bayrd.json`);
6d is a code defect found while smoke-testing the converter.

### 6a. 18 files in the HUB are 0 bytes

Every `.png` (15), plus the `.svg`, `.glb` and `.dxf` — all `documentType: "media"`, all zero
length. The `.rvt` files, also `media`, uploaded fine (5.7–6.6 MB). So the binary/blob lane wrote
empty files for a whole batch and **nothing detected it**.

This matters twice over: the empty `1500bayrd_floorplan.dxf` is exactly the input F2 consumes, and
it means uploads are currently succeeding without verifying that bytes arrived. Whatever
verification exists needs a size assertion.

### 6b. Binaries were base64-chunked into `.txt` files

67 files named `…_partNN.txt`, uniformly 786,420 bytes, totalling **45.7 MB** — binaries split
into base64 text to get past the extension allowlist and the 3 MB cap.

This directly violates architecture rule #1 (§8): *file bytes never pass through an LLM context.*
It is not this repo's code that did it, but it is now sitting in the project and shapes what
dedupe sees. Treat those files as legacy debris, and do not reproduce the pattern.

### 6c. Three `.rvt` copies

`1500BayRd_Existing.rvt`, `_Existing.0001.rvt`, `_Existing.0002.rvt` — the base and `.0002` are
byte-identical (6,569,984). Revit's incremental-save names were uploaded as separate files. A
concrete example of the no-delete problem.

---

### 6d. `scan_to_dxf` silently drops scene-graph node transforms

`src/revit/scan_to_dxf.py:53-54` iterates `scene_or_mesh.geometry.items()`, which yields each
submesh's **local** vertices. The node transform that positions it in the scene is never applied.
Verified with trimesh: a box placed at a node translation of `x=10` reports `scene.bounds` out to
`10.5`, while the geometry the converter reads still spans `-0.5…0.5`.

So any GLB whose scene graph positions submeshes by node transform produces a DXF with everything
collapsed onto the origin — **no error, just a wrong model**. Whether Polycam's own exports trip
this depends on whether they ship a single mesh at identity or a positioned graph; nobody has
checked. That check is cheap and belongs before F2 is trusted. The fix is to bake transforms in
(`scene.dump(concatenate=True)`, or apply `scene.graph[name][0]` per part) — but note the module's
`Wall_*` material-name classification relies on per-submesh names, so concatenating naively would
lose the wall layer. Fix it carefully.

## 7. Blockers and open questions

`docs/DEV_QUESTIONS.md` is the live list. Status as of this handover:

| # | Question | Status |
|---|---|---|
| **Q1** | Upload endpoint + `HUB_API_KEY` | **Partially resolved — see below.** Key still needed. |
| Q2 | Webhook on deal→ACCEPTED | Open. Hourly polling is the working fallback; don't block on it. |
| Q3 | Graph-auth on `url:` fetch | Open. Blocks direct OneDrive→HUB and the F2 `.rvt` handoff. |
| Q4 | Extension allowlist | Open — needs spike S1 (⚠️ permanent side effects). |
| Q5 | "Revit" folder auto-create | Open — needs spike S2. **Now looks answered by observation**: `/Plans and 3D/Revit` exists live. |
| Q6 | ⚠️ **Blob privacy** | Open and important. Binary uploads land in `chapterdev.blob.core.windows.net/public/<dealId>/…` — a **public container on a dev account**, holding client property scans. Must be fixed before batching 100+ projects. |
| Q7 | Junk test-file cleanup | **Appears done** — nothing remains at the `Plans and 3D` root. |

### Q1: three of the five endpoints are now known

The HUB's own MCP server documents its REST routes, and I read them off it directly:

| `config/settings.json` key | Route | Confidence |
|---|---|---|
| `upload_document` | `POST /api/projects/{dealId}/document-media/upload` — multipart, field `files` | **"Route CONFIRMED live"** per the MCP tool's own description |
| `list_sales_deals` | `POST /api/sales/project/deals-list/` | documented (note: `perPage` max is **10**) |
| `list_finance_projects` | `POST /api/finance/{userId}/projects/` | documented |
| `find_project_documents` | *(no single route — a recursive folder walk)* | must be built or asked for |
| `get_project_section` | *(no single route — section-keyed dispatcher)* | must be built or asked for |

Two caveats: the MCP authenticates as a signed-in user, so **this does not give you an API key** —
a script still needs its own credential from the dev. And treat these as strong leads to confirm,
not as gospel. `src/hub_client.py:100` refuses to run while a path still starts with `PLACEHOLDER`,
which is the correct fail-closed behaviour — don't remove that guard, satisfy it.

---

## 8. Architecture rules (non-negotiable)

From the README, restated because they are the reason the code looks the way it does:

1. **File bytes never pass through an LLM context.** Bytes move server-side (signed Polycam CDN
   URL → HUB `url:` fetch, ≤50 MB) or inside the worker scripts. No base64 in tool calls, ever.
2. **MCP/metadata vs HTTP/payloads.** Listing, existence checks and verification are metadata;
   uploads are `url:` mode or direct HTTP.
3. **Idempotent + resumable.** `state/manifest.json` checkpoints every file; re-runs skip done
   rows; existence is checked before every upload — **there is no delete tool**.
4. **Human gate only where judgment is real:** the one-time matching-table review, and per-file
   >50 MB exceptions.
5. **Fail loudly with decoded errors.** The HUB's messages mislead — see §9.

Rule 3 is the one the current defect breaks. Rule 1 is the one the legacy files broke.

---

## 9. Traps that will cost you a day

**The two-ID trap.** `hub_list_all_projects` returns a project *record* `id`. The upload, section
and document endpoints require the **`dealId`**, from `hub_list_sales_deals` or the `dealId` field
of `hub_list_finance_projects`. Both are UUIDs — indistinguishable by shape. Passing the wrong one
yields `Not found. Check that the id is correct.` This reportedly cost a full day once.

**Misleading error messages.** `docs/ERROR_DECODER.md` has the full table. The worst:
`Unauthorized. The HUB_API_KEY is missing…` on `url:` mode does **not** mean your key is wrong — it
means the *remote host* returned 401 to the HUB's anonymous server-side fetch.

**`url:` mode is anonymous-only.** The HUB attaches no auth when fetching. OneDrive/Graph URLs
fail; public or signed CDN URLs work. This is what Q3 is about.

**Never zip to shrink a file.** `.zip` is rejected by the allowlist, so the zip itself fails.

**Signed URLs are secrets — and `redact_url()` does not catch all of them.**
`hub_find_project_documents` returns tempauth download URLs; never log or persist them.
`src/hub_client.py:36 redact_url()` exists for this and the manifest uses it, **but it only strips
the query string**. A SharePoint sharing link carries its token in the *path*
(`https://…/:t:/s/sharehub/IQDQ0It4Qw…`), and that link *is* the credential — `redact_url()`
returns it intact. Treat path-borne share links as unredacted. `tools/make_handover.sh` scans for
them; `redact_url()` does not, and widening it is a small, worthwhile fix.

**A live HUB MCP connector bypasses every guard in this repo.** The `PLACEHOLDER:` endpoints and
`require_env()` fail closed *for the Python path only*. If your environment has Chapter's HUB MCP
server attached, tools like `hub_upload_document`, `hub_update_project` and `hub_close_project`
write to production directly — no API key, no endpoint config, none of this repo's checks. Reading
through the MCP (`hub_find_project_documents`) is exactly how the §5 evidence was gathered and is
safe; writing through it can create the same permanent files this whole document is about
preventing. Know which of those tools you have before you experiment.

---

## 10. Suggested order of work

1. **Fix the dedupe defect (§5).** Nothing else is safe until this is done. Target:
   `test_url_lane_uploads_dedupe_against_live_inventory` passing without `xfail` (see the table in §5). Offline, ~a day.
2. **List one Polycam export zip** and extend `planned_relay_uploads()` in
   `tools/dedupe_dryrun.py`. Until you do, eight of the twelve planned uploads have unknown names
   and the dedupe picture is only two-thirds known. Cheap, and it either removes a worry or finds
   four more.
3. **Add a post-upload size assertion** so the 0-byte failure in §6a can never pass verification
   again. Offline.
4. **Get `HUB_API_KEY` and confirm the endpoints** (§7 Q1) from the HUB dev. Then run the spikes,
   S1 first — and read its header, it leaves permanent files.
5. **Escalate Q6 (public blob container)** before any batch run. This is a client-data exposure
   question, not a code question; it needs Eran and the dev, not you.
6. **Dry-run F1 on the reference project only.** Verify by eye in the HUB Files tab before
   widening.
7. **Set up the workstation** and verify F2's `VERIFY`-marked MCP parameter names against the live
   AUTOM8LABS connector. Needs Revit open and hands on the machine.
8. **F3 backfill last**, gated: `--pilot 3`, then `--pilot 10`, then `--all`. The matching-table
   review is a one-time human step Eran must do.
9. **Only then** schedule the Task Scheduler jobs.

---

## 11. Repo map

```
src/
  config.py           settings + .env loading; require_env() for secrets
  hub_client.py       HUB REST client, error decoding, redact_url, PLACEHOLDER guard
  alerts.py           one-line alerts (SMTP optional, always to state/alerts.log)
  matching.py         address normalization + fuzzy deal↔capture matching
  manifest.py         atomic, resumable run manifest (the queue and the audit log)
  polycam_export.py   Playwright capture of Polycam export URLs
  f1_pipeline.py      F1 core  ⚠️ contains the dedupe defect
  f1_watch.py         hourly ACCEPTED-deal poller
  f3_backfill.py      bulk backfill CLI
  revit/
    scan_to_dxf.py    mesh -> Z-up/feet layered DXF  (runs; drops node transforms, 6d)
    build_rvt.py      AUTOM8LABS MCP recipe replay   (VERIFY markers - unconfirmed)
    f2_queue.py       workstation queue driven off the manifest
spikes/               Phase-0 probes, rerunnable as health checks (S1 has side effects!)
tools/
  dedupe_dryrun.py    read-only pre-flight: what would F1 upload? (added for this handover)
  make_handover.sh    rebuilds this handover zip
config/settings.json  folders, endpoints (PLACEHOLDER), export formats, allowlists
docs/                 ERROR_DECODER.md, DEV_QUESTIONS.md, HANDOVER.md (this file)
tests/                offline unit tests + fixtures/live_hub_inventory_1500bayrd.json
state/                gitignored runtime state — manifest, seen-deals, match tables
```

---

## 12. What is NOT in this package, and what you must be given

Deliberately excluded — **ask Eran**:

- `.env` — real `HUB_BASE_URL` and `HUB_API_KEY` (the key does not exist yet; the dev must issue it)
- `polycam-profile/` — the logged-in Polycam browser profile (contains auth cookies; seed it
  yourself with `python -m src.polycam_export --login`)
- `state/` — runtime manifests and the human-reviewed matching table
- All SharePoint download / preview URLs were stripped from the bundled fixture: they carry signed
  tokens and share credentials.

Access you will need:

| Need | From |
|---|---|
| HUB API key + endpoint confirmation | the HUB dev (via Eran) |
| Polycam account login | Eran |
| The Chapter Revit workstation (Windows, Revit 2027, AUTOM8LABS connector v3.6.2) | Eran |
| GitHub repo write access | Eran |
| A decision on Q6 (public blob container) | Eran + the HUB dev |

---

## 13. Reference project

Use this for every end-to-end test:

- **Deal:** `1500 Bay Rd #1462` — dealId `db3a0a03-32fd-4a26-8aef-a644e93563ad`
- **Polycam capture:** `E4E8C071-9901-4F51-81A7-3D89B665E391`
- **Live contents as of 2026-08-30:** 97 files across `/Plans and 3D/Polycam` and
  `/Plans and 3D/Revit` — snapshotted in `tests/fixtures/live_hub_inventory_1500bayrd.json`

Note that the README's description of this project ("already holds `RoomData.csv` + a notes
`.txt`") is **stale** — it described an earlier state. The fixture is the current truth.
