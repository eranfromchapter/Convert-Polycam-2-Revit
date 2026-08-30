"""HTTP client for the HUB API.

Architecture rules enforced here:
- Metadata over HTTP with the API key; file bytes move server-side (`url` mode)
  or via direct POST from this process — NEVER through an LLM context.
- Only dealIds are accepted (the two-ID trap): resolve them via
  list_sales_deals / list_finance_projects, never from hub_list_all_projects.
- Every HUB error is re-raised as HubError with the DECODED meaning attached
  (the HUB's own messages actively mislead — see docs/ERROR_DECODER.md).
- tempauth download URLs returned by find_project_documents are never logged;
  redact_url() strips query strings before anything reaches a log line.

Endpoint paths live in config/settings.json → hub.endpoints and are
PLACEHOLDERs until the dev supplies the real ones (DEV_QUESTIONS.md Q1);
calling a placeholder endpoint raises immediately with that pointer.
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

from .config import require_env, settings

log = logging.getLogger(__name__)

URL_FETCH_MAX = settings()["hub"]["url_fetch_max_bytes"]        # 50 MB
BASE64_MAX = settings()["hub"]["content_base64_max_bytes"]      # 3 MB decoded


def redact_url(url: str) -> str:
    """Strip query string (tempauth tokens, signatures) for safe logging."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) + (
        "?<redacted>" if parts.query else ""
    )


class HubError(RuntimeError):
    """A HUB API failure, carrying the decoded meaning of the raw message."""

    def __init__(self, raw_message: str, decoded: str, status: int | None = None):
        self.raw_message = raw_message
        self.decoded = decoded
        self.status = status
        super().__init__(f"HUB said: {raw_message!r} — actually means: {decoded}")


def decode_hub_error(raw: str, *, used_url_mode: bool) -> str:
    """Map the HUB's misleading messages to what actually went wrong."""
    r = raw.lower()
    if "not found" in r and "id is correct" in r:
        return (
            "You almost certainly passed the PROJECT record id, not the dealId "
            "(or folderPath is bad). Get the dealId from list_sales_deals / "
            "list_finance_projects, never from hub_list_all_projects."
        )
    if "unauthorized" in r and "hub_api_key" in r and used_url_mode:
        return (
            "The REMOTE host returned 401 to the HUB's anonymous server-side "
            "fetch — your API key is fine. The url must be public/signed "
            "(Graph/OneDrive URLs fail until the dev adds Graph-auth, Q3)."
        )
    if "unexpected error" in r and "400" in r:
        return (
            "File extension/content-type is not in the HUB allowlist (e.g. "
            ".zip, .md). Never zip to shrink a file — the zip itself gets "
            "rejected. See ERROR_DECODER.md for the current allowlist."
        )
    return "No decoder entry for this message — treat the raw text with suspicion."


@dataclass
class HubDocument:
    name: str
    folder: str
    size: int | None
    download_url: str | None  # tempauth — never log this field

    def __repr__(self) -> str:  # keep tempauth URLs out of logs/tracebacks
        return f"HubDocument(name={self.name!r}, folder={self.folder!r}, size={self.size})"


class HubClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or require_env("HUB_BASE_URL")).rstrip("/")
        self.api_key = api_key or require_env("HUB_API_KEY")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    # -- plumbing -----------------------------------------------------------

    def _endpoint(self, name: str) -> str:
        path = settings()["hub"]["endpoints"][name]
        if path.startswith("PLACEHOLDER"):
            raise RuntimeError(
                f"HUB endpoint '{name}' is still a placeholder in "
                f"config/settings.json. Get the real path + HUB_API_KEY from "
                f"the dev (docs/DEV_QUESTIONS.md Q1) before running this."
            )
        return self.base_url + path

    def _request(self, method: str, endpoint_name: str, *, used_url_mode: bool = False,
                 **kwargs) -> dict | list:
        url = self._endpoint(endpoint_name)
        resp = self.session.request(method, url, timeout=120, **kwargs)
        if resp.ok:
            return resp.json() if resp.content else {}
        try:
            raw = resp.json().get("message") or resp.text
        except ValueError:
            raw = resp.text
        raise HubError(raw, decode_hub_error(raw, used_url_mode=used_url_mode),
                       status=resp.status_code)

    # -- metadata -----------------------------------------------------------

    def list_sales_deals(self, *, query: str | None = None,
                         status: str | None = None) -> list[dict]:
        """Deals carry the dealId used by all upload/detail endpoints.
        Contract-signed == status ACCEPTED (facet label 'Signed')."""
        params: dict = {}
        if query:
            params["query"] = query
        if status:
            params["status"] = status
        data = self._request("GET", "list_sales_deals", params=params)
        return data if isinstance(data, list) else data.get("deals", [])

    def list_finance_projects(self) -> list[dict]:
        """Rows carry both 'id' (useless project record id) and 'dealId'."""
        data = self._request("GET", "list_finance_projects")
        return data if isinstance(data, list) else data.get("projects", [])

    def find_project_documents(self, deal_id: str, query: str = "") -> list[HubDocument]:
        data = self._request("GET", "find_project_documents",
                             params={"dealId": deal_id, "query": query})
        rows = data if isinstance(data, list) else data.get("documents", [])
        return [
            HubDocument(
                name=r.get("name", ""),
                folder=r.get("folder", ""),
                size=r.get("size"),
                download_url=r.get("downloadUrl") or r.get("url"),
            )
            for r in rows
        ]

    # -- uploads (bytes move server-side or from this process, never via LLM) --

    def upload_via_url(self, deal_id: str, folder_path: str, file_name: str,
                       url: str, *, document_type: str | None = None) -> dict:
        """Preferred lane: hand the HUB a public/signed URL; it fetches
        server-side (anonymous, ≤50 MB). Zero bytes through this process."""
        payload = {
            "dealId": deal_id,
            "folderPath": folder_path,
            "fileName": file_name,
            "url": url,
        }
        if document_type:
            payload["documentType"] = document_type
        log.info("upload_via_url %s -> %s/%s (src=%s)",
                 file_name, deal_id, folder_path, redact_url(url))
        return self._request("POST", "upload_document", json=payload,
                             used_url_mode=True)

    def upload_via_base64(self, deal_id: str, folder_path: str, file_name: str,
                          file_path: str | Path,
                          *, document_type: str | None = None) -> dict:
        """Fallback lane for small local files (≤3 MB decoded), e.g. members
        unpacked from a Polycam zip. This runs inside a worker script over
        direct HTTP — the permitted use of contentBase64. It must NEVER be
        driven through an LLM tool call."""
        data = Path(file_path).read_bytes()
        if len(data) > BASE64_MAX:
            raise ValueError(
                f"{file_name} is {len(data)} bytes; contentBase64 caps at "
                f"{BASE64_MAX}. Use upload_via_url or the dev's direct POST "
                f"endpoint (DEV_QUESTIONS.md Q1)."
            )
        payload = {
            "dealId": deal_id,
            "folderPath": folder_path,
            "fileName": file_name,
            "contentBase64": base64.b64encode(data).decode("ascii"),
        }
        if document_type:
            payload["documentType"] = document_type
        log.info("upload_via_base64 %s (%d bytes) -> %s/%s",
                 file_name, len(data), deal_id, folder_path)
        return self._request("POST", "upload_document", json=payload)


def remote_size(url: str) -> int | None:
    """Pre-flight content-length check so >50 MB files get flagged, not guessed."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=60)
        if resp.ok and resp.headers.get("content-length"):
            return int(resp.headers["content-length"])
        # Some CDNs refuse HEAD; fall back to a 0-byte ranged GET.
        resp = requests.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=60)
        cr = resp.headers.get("content-range", "")
        if "/" in cr:
            return int(cr.rsplit("/", 1)[1])
    except (requests.RequestException, ValueError):
        pass
    return None


def is_anonymously_fetchable(url: str) -> bool:
    """The HUB's url-mode fetch is anonymous; verify the URL works without auth."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=60)
        if resp.status_code == 405:  # HEAD not allowed — try a ranged GET
            resp = requests.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=60)
        return resp.ok or resp.status_code == 206
    except requests.RequestException:
        return False
