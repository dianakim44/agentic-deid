#!/usr/bin/env bash
#
# CARMEN-I: guide the manual, credentialed acquisition. This script downloads NOTHING.
#
# CARMEN-I is real clinical text from the Hospital Clinic of Barcelona — discharge
# letters, referrals and radiology reports, 2020-03 to 2022-03, COVID-19 admissions.
# The sensitive elements were annotated and replaced with synthetic equivalents, but
# the clinical narrative is authentic patient text, not a synthetic construction like
# MEDDOCAN or GraSCCo.
#
#   access  : PhysioNet credentialed. Requires a completed training certificate and a
#             signed DUA, plus a separate CARMEN-I registration stating intended use.
#   licence : CC-BY-SA-4.0 on the corpus itself, ON TOP OF the PhysioNet
#             Contributor Review Health Data License v1.5.0 access conditions.
#             The permissive corpus licence does NOT relax the access agreement:
#             clause 3 of the PhysioNet licence forbids sharing access with anyone.
#
# Why this is not an automated fetch, unlike fetch_meddocan.sh and fetch_grascco.sh:
# those pin open Zenodo DOIs and can be re-downloaded by anyone. A PhysioNet
# credentialed download is bound to one approved individual. A script that embedded a
# working credential or session cookie would be a way to share access — exactly what
# the DUA forbids. So the human does the download; the script records the procedure
# and verifies the result.
#
# Why the corpus lives OUTSIDE this repository: it is DUA-restricted, and anything
# under data/ is one `git add` mistake away from a public repository. Keeping it
# outside the tree means tools/release_screen.py cannot even see it, and no staging
# accident is possible. The path is recorded in config/data_paths.local.yaml, which is
# gitignored.
set -euo pipefail

CORPUS="es-carmen"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_MAP="$ROOT/config/data_paths.local.yaml"

cat <<'PROCEDURE'
────────────────────────────────────────────────────────────────────────────
CARMEN-I acquisition — manual, credentialed. Nothing is downloaded by this script.
────────────────────────────────────────────────────────────────────────────

1. Credentials
   - Hold a current PhysioNet credentialed account (CITI "Data or Specimens Only
     Research" certificate uploaded and approved).
   - Sign the Contributor Review Health Data License v1.5.0 for CARMEN-I.
   - Complete the separate CARMEN-I registration describing intended use. The
     authors require this so patients can be informed how shared data is used.

2. Download, as the approved individual
   - Fetch the CARMEN-I archive from its PhysioNet project page.
   - Do NOT place it under this repository. Choose a directory outside the tree,
     for example  ~/<somewhere-outside-the-repo>/carmen-i .
     The example is deliberately not a real path: this file is committed to a public
     repository, and step 5 says not to publish the location.

3. Verify
   - The release ships SHA256SUMS.txt. Verify before use:
         cd <corpus-root> && shasum -a 256 -c SHA256SUMS.txt
   - Expected top level: CARMEN-I/ , LICENSE.txt , SHA256SUMS.txt

4. Record the path
   - Copy the template if you have not already:
         cp config/data_paths.example.yaml config/data_paths.local.yaml
   - Set the es-carmen entry to the directory containing CARMEN-I/ .
   - config/data_paths.local.yaml is gitignored. Do not commit it, and do not
     paste the path into a commit message, issue, or profile file.

5. Handling rules, for as long as the data is on disk
   - Never quote a span surface form. Not in profiles/, not in docs/notes/, not in
     a commit message, not in an agent prompt. Counts, offsets and type labels only.
     This is stricter than the rule applied to MEDDOCAN and GraSCCo, which are
     synthetic and where sample surfaces were quoted.
   - Two anonymisation variants ship. `txt/masked/` replaces every sensitive item
     with its bracketed type label and contains no surrogate values at all; it is
     the safer variant to read while developing. `txt/replaced/` carries synthetic
     surrogates and reads like a real note — treat it as if it were identifying.
   - If you find text you suspect could identify someone, report it: PhysioNet
     clause 5 (PHI-report@physionet.org) and the CARMEN-I authors (infosic@clinic.cat).

────────────────────────────────────────────────────────────────────────────
PROCEDURE

# ─── verify whatever the human has already done ────────────────────────────
if [[ ! -f "$LOCAL_MAP" ]]; then
    echo "STATUS: config/data_paths.local.yaml does not exist yet — step 4 not done."
    exit 0
fi

# Read the mapping without a YAML dependency: the file is a flat two-level map.
CARMEN_PATH="$(sed -n 's/^[[:space:]]*es-carmen:[[:space:]]*//p' "$LOCAL_MAP" | head -1)"
CARMEN_PATH="${CARMEN_PATH%%#*}"
CARMEN_PATH="${CARMEN_PATH%"${CARMEN_PATH##*[![:space:]]}"}"
CARMEN_PATH="${CARMEN_PATH/#\~/$HOME}"

if [[ -z "$CARMEN_PATH" ]]; then
    echo "STATUS: no es-carmen entry in config/data_paths.local.yaml — step 4 not done."
    exit 0
fi
if [[ "$CARMEN_PATH" == *"path/to"* ]]; then
    echo "STATUS: es-carmen still holds the template placeholder — step 4 not done."
    exit 0
fi
if [[ ! -d "$CARMEN_PATH" ]]; then
    echo "STATUS: es-carmen path is set but is not a directory. Check step 2."
    exit 1
fi

# Deliberately not echoing the resolved path: it is a DUA data location, and this
# output may be pasted somewhere. Report structure, not location.
echo "STATUS: es-carmen path is set and exists."
# maxdepth 1 below, so every directory listed here must be one that holds files
# directly. CARMEN-I/tsv holds only subdirectories and would report "1 file".
for d in CARMEN-I CARMEN-I/txt/masked CARMEN-I/txt/replaced \
         CARMEN-I/ann/masked/anon CARMEN-I/ann/masked/ner \
         CARMEN-I/ann/replaced/anon CARMEN-I/ann/replaced/ner \
         CARMEN-I/tsv/masked CARMEN-I/tsv/replaced; do
    if [[ -d "$CARMEN_PATH/$d" ]]; then
        n=$(find "$CARMEN_PATH/$d" -maxdepth 1 -type f | wc -l | tr -d ' ')
        printf '  ok      %-34s %6s files\n' "$d" "$n"
    else
        printf '  MISSING %-34s\n' "$d"
    fi
done

if [[ -f "$CARMEN_PATH/SHA256SUMS.txt" ]]; then
    echo "  ok      SHA256SUMS.txt present — run step 3 if you have not."
else
    echo "  MISSING SHA256SUMS.txt"
fi

echo
echo "Inventory: profiles/es-carmen.raw.json   (counts and offsets only, no surfaces)"
echo "Corpus ID: $CORPUS   (config/naming.yaml)"
