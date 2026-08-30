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
if grep -rlIE '(tempauth|[?&](sig|sv|se|access_token)=|Bearer [A-Za-z0-9._-]{20,})' "$STAGE" \
     --exclude=HANDOVER.md --exclude=ERROR_DECODER.md \
     --exclude=live_hub_inventory_1500bayrd.json 2>/dev/null | grep -q .; then
  echo "ABORT: possible token/signed URL in staged files" >&2
  grep -rlIE '(tempauth|[?&](sig|sv|se|access_token)=)' "$STAGE" >&2 || true
  exit 1
fi

( cd dist && zip -qr "${NAME}.zip" "${NAME}" )
rm -rf "$STAGE"
echo "built $OUT"
unzip -l "$OUT" | tail -3
