"""Address matching: HUB deals ↔ Polycam captures.

Capture names are property addresses ("1500 Bay Rd, Miami Beach, FL"); deal
records carry addresses possibly with unit numbers, abbreviations, and ZIPs.
Normalize both sides, fuzzy-match with rapidfuzz, and score confidence.

Rules:
- score >= auto_accept_confidence (0.9): auto-match
- below: goes to the human review CSV (F3) or triggers an alert (F1)
- no match: NEVER guess — alert.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

# USPS-style suffix/directional abbreviations, expanded to one canonical form.
_ABBREV = {
    "st": "street", "str": "street", "rd": "road", "ave": "avenue",
    "av": "avenue", "blvd": "boulevard", "dr": "drive", "ln": "lane",
    "ct": "court", "pl": "place", "hwy": "highway", "ter": "terrace",
    "terr": "terrace", "pkwy": "parkway", "cir": "circle", "sq": "square",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}

# Unit designators: everything from the designator to the end of that token run
# is dropped ("#1462", "Apt 4B", "Unit 12", "Ste 300").
# Note: no leading \b before '#' — a word boundary never exists between a
# space and '#', which silently kills the match.
_UNIT_RE = re.compile(
    r"(?:#\s*[\w-]+|\b(?:apt\.?|apartment|unit|ste\.?|suite|no\.?|floor|fl)\s+#?\s*[\w-]+)\b",
    re.IGNORECASE,
)
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_address(raw: str) -> str:
    s = raw.strip().lower()
    s = _UNIT_RE.sub(" ", s)
    s = s.replace("#", " ")          # stray unit marker without designator
    s = _ZIP_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    tokens = [_ABBREV.get(t, t) for t in s.split()]
    return _WS_RE.sub(" ", " ".join(tokens)).strip()


def extract_unit(raw: str) -> str | None:
    """Kept separately: two deals in one building differ only by unit."""
    m = _UNIT_RE.search(raw)
    if not m:
        return None
    return re.sub(r"[^\w-]", "", m.group(0).split(None, 1)[-1]) or None


def score(deal_address: str, capture_name: str) -> float:
    a, b = normalize_address(deal_address), normalize_address(capture_name)
    if not a or not b:
        return 0.0
    # token_sort handles "Bay Rd 1500" vs "1500 Bay Road"; partial handles the
    # capture including city/state the deal omits (or vice versa).
    return max(fuzz.token_sort_ratio(a, b), fuzz.partial_ratio(a, b)) / 100.0


@dataclass
class Match:
    deal_id: str
    deal_address: str
    capture_id: str
    capture_name: str
    confidence: float
    unit: str | None


def best_match(deal: dict, captures: list[dict]) -> Match | None:
    """captures: [{'id': ..., 'name': ...}]. Returns the top-scoring capture
    or None when the library is empty."""
    addr = deal.get("address") or deal.get("name") or ""
    best, best_s = None, -1.0
    for cap in captures:
        s = score(addr, cap.get("name", ""))
        if s > best_s:
            best, best_s = cap, s
    if best is None:
        return None
    return Match(
        # Sales-deal rows: the record id IS the dealId. Finance rows carry a
        # distinct 'dealId' field — prefer it (the two-ID trap).
        deal_id=deal.get("dealId") or deal["id"],
        deal_address=addr,
        capture_id=best["id"],
        capture_name=best.get("name", ""),
        confidence=round(best_s, 3),
        unit=extract_unit(addr),
    )


def build_matching_table(deals: list[dict], captures: list[dict]) -> list[Match]:
    return [m for d in deals if (m := best_match(d, captures))]


def write_review_csv(matches: list[Match], path: str | Path,
                     auto_accept: float) -> int:
    """Emit ALL rows; 'approved' pre-filled 'yes' for >=threshold, blank below.
    Eran edits the blanks to yes/no (or fixes capture_id), once."""
    n_review = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["deal_id", "deal_address", "capture_id", "capture_name",
                    "confidence", "unit", "approved"])
        for m in sorted(matches, key=lambda m: m.confidence):
            auto = m.confidence >= auto_accept
            n_review += 0 if auto else 1
            w.writerow([m.deal_id, m.deal_address, m.capture_id,
                        m.capture_name, m.confidence, m.unit or "",
                        "yes" if auto else ""])
    return n_review


def read_approved_table(path: str | Path) -> list[Match]:
    approved = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("approved", "").strip().lower() == "yes":
                approved.append(Match(
                    deal_id=row["deal_id"], deal_address=row["deal_address"],
                    capture_id=row["capture_id"],
                    capture_name=row["capture_name"],
                    confidence=float(row["confidence"] or 0),
                    unit=row.get("unit") or None,
                ))
    return approved
