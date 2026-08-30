#!/usr/bin/env bash
# Build the developer handover zip. Run from the repo root:  bash tools/make_handover.sh
# Ships source, docs, tests and fixtures. Deliberately EXCLUDES every secret and
# machine-local artifact: .env, state/, polycam-profile/, caches, .git.
set -euo pipefail

STAMP="$(git log -1 --format=%cd --date=format:%Y%m%d 2>/dev/null || echo undated)"
NAME="Convert-Polycam-2-Revit_handover_${STAMP}"
OUT="dist/${NAME}.zip"
STAGE="dist/${NAME}"

rm -rf "$STAGE" "$OUT"
mkdir -p "$STAGE" dist

# --- source of truth: git's own file list, so nothing gitignored can leak -----
git ls-files -z | while IFS= read -r -d '' f; do
  mkdir -p "$STAGE/$(dirname "$f")"
  cp "$f" "$STAGE/$f"
done

# Untracked-but-intended deliverables (present before the handover commit lands).
for extra in docs/HANDOVER.md tests/fixtures/live_hub_inventory_1500bayrd.json \
             tools/dedupe_dryrun.py tests/test_live_inventory_dedupe.py; do
  [ -f "$extra" ] && { mkdir -p "$STAGE/$(dirname "$extra")"; cp "$extra" "$STAGE/$extra"; }
done

mkdir -p "$STAGE/state" && touch "$STAGE/state/.gitkeep"

# --- refuse to ship secrets ---------------------------------------------------
if [ -e "$STAGE/.env" ] || [ -d "$STAGE/polycam-profile" ]; then
  echo "ABORT: secret material staged (.env / polycam-profile)" >&2; exit 1
fi
# Match token VALUES, not the vocabulary: a real signed token is long, so a
# 16+ char query value or bearer token trips this while prose about "tempauth
# URLs" and the redaction test's dummy "?sig=SECRET" correctly do not.
SECRET_RE='([?&](tempauth|sig|sv|se|st|token|access_token|code)=[A-Za-z0-9%._~+/-]{16,}|Bearer[[:space:]]+[A-Za-z0-9._-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})'
if grep -rlIE "$SECRET_RE" "$STAGE" 2>/dev/null | grep -q .; then
  echo "ABORT: token-shaped value found in staged files:" >&2
  grep -rlIE "$SECRET_RE" "$STAGE" >&2
  exit 1
fi
# Belt and braces: an .env anywhere, or a populated API key, never ships.
if grep -rlIE '^[[:space:]]*HUB_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]+' "$STAGE" 2>/dev/null | grep -q .; then
  echo "ABORT: a populated HUB_API_KEY is staged" >&2; exit 1
fi

( cd dist && zip -qr "${NAME}.zip" "${NAME}" )
rm -rf "$STAGE"
echo "built $OUT"
unzip -l "$OUT" | tail -3
