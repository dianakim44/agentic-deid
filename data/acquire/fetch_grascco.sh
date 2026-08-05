#!/usr/bin/env bash
#
# Fetch GraSCCo into data/raw/de-grascco/.
#
# Two Zenodo records carry the name "GraSCCo" and they are NOT interchangeable:
#
#   10.5281/zenodo.6539130   concept DOI, "GraSCCo" — 63 plain .txt letters,
#                            NO PHI annotations. Latest version 1.1 (2026-03-05,
#                            record 18874981).
#   10.5281/zenodo.11502328  concept DOI, "GraSCCo_PII" — the same letters WITH
#                            PII/PHI annotations. Version 2 (2025-09-07,
#                            record 15747389) is what this script pins.
#
# De-identification needs the annotated release, so GraSCCo_PII_V2 is used.
# Its UIMA CAS JSON files embed the document text (the "sofa"), so the plain-text
# record is not additionally required — but it is fetched as well, because the
# .txt files are the authoritative newline/encoding form of each document and the
# CAS sofa is a re-serialisation of it.
#
#   licence : CC-BY-4.0, open access (both records)
#
# The extracted corpus is ignored by git and blocked by tools/release_screen.py.
set -euo pipefail

CORPUS="de-grascco"
PII_RECORD="15747389"       # GraSCCo_PII_V2, annotations
TXT_RECORD="18874981"       # GraSCCo v1.1, plain text

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="$ROOT/data/raw/$CORPUS"
DL="$ROOT/data/raw/.download"

mkdir -p "$RAW" "$DL"

if [ -n "$(ls -A "$RAW" 2>/dev/null)" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "$RAW is not empty. Set FORCE=1 to re-download."
  exit 0
fi

fetch_file () {   # record, filename, destdir
  local rec="$1" name="$2" dest="$3"
  local url="https://zenodo.org/api/records/$rec/files/$name/content"
  echo "  - $name"
  curl -fSL --retry 3 --retry-delay 2 -o "$DL/$name" "$url"
  case "$name" in
    *.zip) unzip -q -o "$DL/$name" -d "$dest" ;;
    *)     mkdir -p "$dest"; mv "$DL/$name" "$dest/" ;;
  esac
  rm -f "$DL/$name"
}

echo "==> GraSCCo_PII_V2 annotations (record $PII_RECORD)"
mkdir -p "$RAW/annotations"
fetch_file "$PII_RECORD" "grascco_pii_2_json.zip" "$RAW/annotations"
fetch_file "$PII_RECORD" "grascco_pii_2_xmi.zip"  "$RAW/annotations"

echo "==> type system and annotation guide (record $PII_RECORD)"
mkdir -p "$RAW/schema"
fetch_file "$PII_RECORD" "GeMTeX_PII_2_Typesystem.xml"        "$RAW/schema"
fetch_file "$PII_RECORD" "GeMTeX_PII_2_layer_inception.json"  "$RAW/schema"
fetch_file "$PII_RECORD" "GeMTeX_Annoguide_DeID_2_202509.pdf" "$RAW/schema"

echo "==> GraSCCo plain text (record $TXT_RECORD)"
mkdir -p "$RAW/text"
# The plain-text record publishes one .txt per document rather than an archive,
# so the file list is read from the Zenodo API instead of being hard-coded.
python3 - "$TXT_RECORD" "$RAW/text" <<'PY'
import json, sys, urllib.request, os, time
rec, dest = sys.argv[1], sys.argv[2]
meta = json.load(urllib.request.urlopen(
    f"https://zenodo.org/api/records/{rec}", timeout=60))
files = meta.get("files", [])
print(f"  {len(files)} files")
for f in files:
    out = os.path.join(dest, f["key"])
    for attempt in range(3):
        try:
            urllib.request.urlretrieve(f["links"]["self"], out)
            break
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)
print("  done")
PY

echo
echo "==> done. layout:"
find "$RAW" -maxdepth 2 -mindepth 1 -type d | sed "s|$ROOT/||" | sort
echo
echo "Cite (text)        : Modersohn L, Schulz S, Lohr C, Hahn U. GraSCCo — the first"
echo "                     publicly shareable, multiply-alienated German clinical text"
echo "                     corpus. GMDS 2022."
echo "Cite (annotations) : Lohr C, Faller J, Riedel A, et al. GeMTeX's De-Identification"
echo "                     in Action: Lessons Learned & Devil's Details."
echo "                     Stud Health Technol Inform. 2025;331:274-282."
echo "DOI  : 10.5281/zenodo.15747389 (PII v2) · 10.5281/zenodo.18874981 (text v1.1)"
echo "       both CC-BY-4.0"
