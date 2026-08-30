# The HUB Error Decoder

The HUB's error messages actively mislead. `src/hub_client.py` bakes this table
into every raised `HubError`; this file is the human-readable reference.

| HUB message | Actual meaning |
|---|---|
| `Not found. Check that the id is correct.` | You passed the **project record id** (from `hub_list_all_projects`), not the **dealId** — or a bad `folderPath`. |
| `Unauthorized. The HUB_API_KEY is missing…` (on `url:` mode) | The **remote host** returned 401 to the HUB's **anonymous** server-side fetch. Your API key is fine. Use a public/signed URL. |
| `Unexpected error — HUB upload failed (400)` | File **extension/content-type not in the allowlist** (e.g. `.zip`, `.md`). Never zip to shrink a file — the zip itself is rejected. |

## The two-ID trap (cost a full day once)

- `hub_list_all_projects` → returns a project record `id` (e.g. `a68e4358-…`).
  **Rejected** by upload/detail endpoints.
- `hub_upload_document`, `hub_get_project_section`, `hub_find_project_documents`
  → require the **`dealId`**, which comes from `hub_list_sales_deals` (search by
  address) or from `hub_list_finance_projects` rows (field `dealId`, distinct
  from `id`).

Both are UUIDs, so they cannot be told apart by shape. `hub_client` therefore
only ever accepts dealIds resolved through the sales-deals / finance-projects
paths, and decodes any `Not found` as "probably the wrong id kind".

## Other verified behaviors

- `folderPath` is case-insensitive, resolved from the project root.
- Upload allowlist (verified): `.csv .txt .dxf .jpg`. Verified rejected:
  `.zip .md`. Pending one-shot probe (spike S1): `.pdf .glb .obj .mtl .ply
  .laz .rvt .png .svg`.
- Storage routing: `documentType:"document"` (text) → SharePoint
  (`chapter413.sharepoint.com/sites/sharehub/HUB/<project>/<folder>/`);
  `documentType:"media"` (binary) → Azure blob
  `chapterdev.blob.core.windows.net/public/<dealId>/<folderId>/<name>`.
  ⚠️ That container is **public** and on a **dev** account — client property
  scans must not batch into it until the dev fixes privacy (DEV_QUESTIONS Q6).
- `url:` mode is **anonymous-only** (≤50 MB): the HUB attaches no auth when
  fetching. Graph/OneDrive URLs fail; public/signed CDN URLs work.
- `contentBase64` exists (≤3 MB decoded) — allowed **only** inside a worker
  script via direct HTTP, **never** via an LLM tool call.
- There is **no delete tool**. Duplicates and junk are permanent until the dev
  cleans SharePoint. Check existence before every upload.
- `hub_find_project_documents` returns name/folder/size + **tempauth** download
  URLs. Never log the tempauth URLs (this repo redacts query strings).
