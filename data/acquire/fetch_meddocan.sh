#!/usr/bin/env bash
#
# Fetch MEDDOCAN into data/raw/es-meddocan/.
#
# MEDDOCAN corpus: gold standard annotations for Medical Document Anonymization
# on Spanish clinical case reports. Marimon et al., 2020.
#
#   concept DOI : 10.5281/zenodo.4279322   (always resolves to the latest version)
#   version DOI : 10.5281/zenodo.4279323   (v1.0 — what this script pins)
#   licence     : CC-BY-4.0, open access
#
# The concept DOI given in the task description resolves to record 4279323; the
# version DOI is pinned here so a future upload cannot silently change the corpus
# under a frozen split.
#
# Downloads nothing that is not already public. The extracted corpus is ignored
# by git (see .gitignore) and blocked by tools/release_screen.py.
set -euo pipefail

CORPUS="es-meddocan"
RECORD="4279323"
ZIPNAME="meddocan.zip"
EXPECT_BYTES="11738792"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="$ROOT/data/raw/$CORPUS"
DL="$ROOT/data/raw/.download"
URL="https://zenodo.org/api/records/$RECORD/files/$ZIPNAME/content"

mkdir -p "$RAW" "$DL"

if [ -n "$(ls -A "$RAW" 2>/dev/null)" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "$RAW is not empty. Set FORCE=1 to re-download."
  exit 0
fi

echo "==> downloading MEDDOCAN (record $RECORD, $ZIPNAME)"
curl -fSL --retry 3 --retry-delay 2 -o "$DL/$ZIPNAME" "$URL"

got=$(wc -c < "$DL/$ZIPNAME" | tr -d ' ')
echo "==> downloaded $got bytes (expected $EXPECT_BYTES)"
if [ "$got" != "$EXPECT_BYTES" ]; then
  echo "!! size differs from the pinned version. The record may have been updated."
  echo "!! Verify the DOI before using this for an experiment; a changed corpus"
  echo "!! invalidates any frozen split." >&2
fi

echo "==> extracting into $RAW"
unzip -q -o "$DL/$ZIPNAME" -d "$RAW"
rm -f "$DL/$ZIPNAME"

echo "==> done. top level:"
find "$RAW" -maxdepth 2 -mindepth 1 -type d | sed "s|$ROOT/||" | sort
echo
echo "Cite: Marimon M, Gonzalez-Agirre A, Intxaurrondo A, Rodriguez H, Lopez Martin JA,"
echo "      Villegas M, Krallinger M. Automatic De-identification of Medical Texts in"
echo "      Spanish: the MEDDOCAN Track. IberLEF/SEPLN 2019."
echo "DOI : 10.5281/zenodo.4279323  (CC-BY-4.0)"
