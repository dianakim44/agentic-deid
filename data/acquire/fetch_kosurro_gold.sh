#!/usr/bin/env bash
#
# ko-surro source release, the three reference files: guide the manual, credentialed
# acquisition. This script downloads NOTHING.
#
# `ko-surro` is a Korean surrogate corpus derived from PhysioNet "Deidentified Medical
# Text" v1.0 — 2,434 English nursing notes. Two of that release's five files are already
# held (the surrogate text and the de-identification tool's output). The three that are
# not held are the ones that carry the human reference:
#
#     id.deid        gold PHI offsets
#     id.types       gold PHI types
#     id-phi.phrase  a six-field index over the gold PHI instances
#
# Why they matter enough to have their own script. `ko-surro`'s gold was built by taking
# the placeholder positions in the tool-output file as the PHI coordinates. That file is a
# system output, not a human annotation: it holds 2,164 placeholders where the release's
# own reference is about 1,779 instances, and the producing project's manual review
# relabelled 142 of them as not-PHI. So the corpus currently scores predictions against a
# silver standard produced by the same kind of object being scored. The three files above
# replace it with the human reference, and they are part of the release already held — the
# same project, the same DUA, no new approval.
#
#   access  : PhysioNet credentialed + per-project DUA (checked 2026-08-27: the project
#             page states "Only credentialed users who sign the DUA can access the files"
#             and names CITI "Data or Specimens Only Research" as the required training).
#   version : v1.0, published 2007-12-18.
#   DOI     : 10.13026/jc2a-ca12   (version 1.0 — what this script pins)
#             10.13026/rma9-zb29   (latest version — deliberately NOT used)
#
# The DUA is per project and this is the same project the two held files came from, so an
# account that downloaded those has already signed it. That is an inference from the held
# files, not something the page states; step 1 is where it gets confirmed.
#
# Why this is not an automated fetch, for the same reason as fetch_carmen.sh: a PhysioNet
# credentialed download is bound to one approved individual, and a script carrying a
# working credential or session cookie would be a way to share access — which the DUA
# forbids. The human downloads; this script records the procedure and verifies the result.
set -euo pipefail

CORPUS="ko-surro"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_MAP="$ROOT/config/data_paths.local.yaml"
WANT=(id.deid id.types id-phi.phrase)
HELD=(id.text id.res)

cat <<'PROCEDURE'
────────────────────────────────────────────────────────────────────────────
ko-surro source reference files — manual, credentialed. Nothing is downloaded here.
────────────────────────────────────────────────────────────────────────────

1. Confirm access, logged in
   - Open the "Deidentified Medical Text" v1.0 project page on PhysioNet.
   - If the file list renders, the DUA for this project is already signed and there is
     nothing to apply for. If it shows the restricted-access notice instead, sign the
     DUA on that page; the CITI training requirement is the same one already satisfied
     for the other credentialed corpus held here.
   - Do not create a second account for this. Access is per individual.

2. Download, as the approved individual
       wget -c -nd -P <release-dir> \
            --user <physionet-username> --ask-password \
            https://physionet.org/files/deidentifiedmedicaltext/1.0/id.deid \
            https://physionet.org/files/deidentifiedmedicaltext/1.0/id.types \
            https://physionet.org/files/deidentifiedmedicaltext/1.0/id-phi.phrase
   - --ask-password, never a password on the command line: the command line is visible
     to other processes and lands in the shell history file.
   - <release-dir> is the directory that already holds id.text and id.res. The three new
     files belong beside them: they are the same release and describe the same 2,434
     documents, and splitting one release across two directories is how an offset gets
     compared against the wrong file.
   - Do NOT place them under this repository. This release is DUA-restricted.

3. Verify
   - The release ships no SHA256SUMS. What can be checked instead is internal
     consistency, which is stronger for our purpose than a checksum: the gold offsets
     have to land on the placeholders' positions in id.text, and that is exactly the
     measurement we want anyway.
         python tools/gold_provenance_check.py describe --release-dir <release-dir>
     reports each file's size, line count and field structure. Counts only — the tool
     never prints note text, and neither should any report derived from it.

4. Record the path
   - config/data_paths.local.yaml, under corpora:, key ko-surro, pointing at the
     directory that holds all five files. The template has the line commented out.
   - That file is gitignored. Do not commit it and do not paste the path anywhere.

5. Run the three measurements
         python tools/gold_provenance_check.py check --release-dir <release-dir>
     answers, in order:
       (a) where the difference between 2,164 placeholders and the human gold count
           comes from — decomposed into placeholders with no gold span, gold spans with
           no placeholder, and matched pairs;
       (b) what type the 659 untyped placeholders carry in the human gold;
       (c) the disagreement rate between ko-surro's silver reference and the human gold,
           including whether the producing project's 142 not-PHI relabels fall inside
           the set of placeholders the human gold does not support.

6. Handling rules, for as long as the data is on disk
   - Never quote a span surface form — not in docs/notes/, not in a commit message, not
     in an agent prompt, not in an exception message. Counts, offsets and type labels
     only. This release is real nursing text with surrogate PHI, so the same rule that
     applies to the other credentialed corpus applies here.
   - The gold files name PHI phrases directly. id-phi.phrase in particular is an index
     OF the PHI instances, so anything printed from it unfiltered is the worst case.
     The check tool reads it and reports only counts, offsets and types.

────────────────────────────────────────────────────────────────────────────
PROCEDURE

# ─── verify whatever the human has already done ────────────────────────────
if [[ ! -f "$LOCAL_MAP" ]]; then
    echo "STATUS: config/data_paths.local.yaml does not exist yet — step 4 not done."
    exit 0
fi

# Flat two-level map, read without a YAML dependency (as fetch_carmen.sh does).
REL="$(sed -n 's/^[[:space:]]*ko-surro:[[:space:]]*//p' "$LOCAL_MAP" | head -1)"
REL="${REL%%#*}"
REL="${REL%"${REL##*[![:space:]]}"}"
REL="${REL/#\~/$HOME}"

if [[ -z "$REL" ]]; then
    echo "STATUS: no ko-surro entry in config/data_paths.local.yaml — step 4 not done."
    exit 0
fi
if [[ "$REL" == *"path/to"* ]]; then
    echo "STATUS: ko-surro still holds the template placeholder — step 4 not done."
    exit 0
fi
if [[ ! -d "$REL" ]]; then
    echo "STATUS: ko-surro path is set but is not a directory. Check step 2."
    exit 1
fi

# Deliberately not echoing the resolved path: it is a DUA data location and this output
# may be pasted somewhere. Report presence and size, not location.
echo "STATUS: ko-surro release path is set and exists."
missing=0
for f in "${HELD[@]}" "${WANT[@]}"; do
    if [[ -f "$REL/$f" ]]; then
        n=$(wc -c < "$REL/$f" | tr -d ' ')
        printf '  ok      %-16s %12s bytes\n' "$f" "$n"
    else
        printf '  MISSING %-16s\n' "$f"
        missing=$((missing + 1))
    fi
done

echo
if [[ "$missing" -gt 0 ]]; then
    echo "$missing of 5 release files absent. Step 2 is incomplete."
    echo "Until the three reference files are present, ko-surro's gold is the"
    echo "tool-output placeholder set — a silver standard. Report it as one."
    exit 0
fi

echo "All five release files present. Next: step 5."
echo "Corpus ID: $CORPUS   (config/naming.yaml)"
echo "Cite: Neamatullah I, Douglass MM, Lehman LH, Reisner A, Villarroel M, Long WJ,"
echo "      Szolovits P, Moody GB, Mark RG, Clifford GD. Automated de-identification of"
echo "      free-text medical records. BMC Med Inform Decis Mak 2008;8:32."
echo "DOI : 10.13026/jc2a-ca12  (PhysioNet Credentialed Health Data License 1.5.0)"
