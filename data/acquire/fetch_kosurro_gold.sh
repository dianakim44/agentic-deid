#!/usr/bin/env bash
#
# ko-surro human reference: fetch the gold PHI offsets and types.
#
# This script DOES download, unlike fetch_carmen.sh and unlike the version of this file
# written on 2026-08-27. The reason for the change is that the earlier version was wrong
# about where the reference lives, and therefore wrong about what it costs to get.
#
# What was wrong, kept here because the correction is the useful part. The PhysioNet
# "Deidentified Medical Text" release (2,434 English nursing notes, DUA-restricted) ships
# exactly two files:
#
#     id.text   the corpus with PHI replaced by realistic surrogates
#     id.res    the same notes with PHI replaced by the de-identification tool's tags
#
# The earlier version asserted that the same release also ships id.deid, id.types and
# id-phi.phrase, and that all three needed a credentialed download. Three of those four
# claims are false. The five-file list came from the *de-identification software package's*
# README, which names five corpus-related files in one manifest; that manifest was read as
# an inventory of the text release. It is not one. The text release's own project page says
# so explicitly: "For other gold standard corpus related files (such as the detected PHI
# location), please see the associated software package."
#
# Where the reference actually is:
#
#     https://physionet.org/content/deid/1.1/     the de-identification software package
#     id.deid          gold PHI offsets. Numbers only — no text of any kind.
#     id-phi.phrase    pid, note, start, end, type, and the PHI phrase (field 6).
#     shift.txt        per-patient date shifts.
#
# And that package is OPEN ACCESS — Open Data Commons Attribution License v1.0, "Anyone can
# access the files, as long as they conform to the terms of the specified license." No
# credential, no DUA, no application. The reference was obtainable the whole time.
#
# id.types does not exist. The package README names it ("Category of PHIs in id.deid") and
# no distribution contains it. Its content is available as id-phi.phrase field 5, so nothing
# is lost, but the name should not be propagated: it is a documentation artefact.
#
# The two held files remain DUA-restricted. Open access applies to the reference, not to the
# corpus text. Do not move id.text or id.res anywhere on the strength of this script.
set -euo pipefail

CORPUS="ko-surro"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_MAP="$ROOT/config/data_paths.local.yaml"
BASE="https://physionet.org/files/deid/1.1"
WANT=(id.deid id-phi.phrase shift.txt)
HELD=(id.text id.res)

# Sizes as published in the 1.1 directory listing, read 2026-08-28. A size check is weak as
# integrity goes, but the real verification is downstream and much stronger: the offsets have
# to land inside the record bodies of a file this script never touches, and
# gold_provenance_check.py refuses to report anything if they do not.
declare -a EXPECT_BYTES=(73117 50850 1860)

if [[ ! -f "$LOCAL_MAP" ]]; then
    echo "STATUS: config/data_paths.local.yaml does not exist yet."
    echo "        Create it from config/data_paths.example.yaml and set the ko-surro key to"
    echo "        the directory holding id.text and id.res."
    exit 1
fi

# Flat two-level map, read without a YAML dependency (as fetch_carmen.sh does).
REL="$(sed -n 's/^[[:space:]]*ko-surro:[[:space:]]*//p' "$LOCAL_MAP" | head -1)"
REL="${REL%%#*}"
REL="${REL%"${REL##*[![:space:]]}"}"
REL="${REL/#\~/$HOME}"

if [[ -z "$REL" || "$REL" == *"path/to"* ]]; then
    echo "STATUS: no usable ko-surro entry in config/data_paths.local.yaml."
    exit 1
fi
if [[ ! -d "$REL" ]]; then
    echo "STATUS: the ko-surro path is set but is not a directory."
    exit 1
fi

# Deliberately not echoing the resolved path: it is where DUA-restricted text sits and this
# output may be pasted somewhere. Presence and size only.
for f in "${HELD[@]}"; do
    if [[ ! -f "$REL/$f" ]]; then
        echo "STATUS: $f is not in the release directory. The reference files are offsets into"
        echo "        it, so fetching them into a directory without it would separate a"
        echo "        coordinate system from the thing it indexes. Stopping."
        exit 1
    fi
done

echo "Fetching the human reference into the release directory (open access, no credential)."
for i in "${!WANT[@]}"; do
    f="${WANT[$i]}"
    want="${EXPECT_BYTES[$i]}"
    code=$(curl -sS -w '%{http_code}' -o "$REL/$f.part" --max-time 120 "$BASE/$f")
    if [[ "$code" != "200" ]]; then
        rm -f "$REL/$f.part"
        printf '  FAIL    %-16s HTTP %s\n' "$f" "$code"
        echo
        echo "A 403 here would mean the package's access terms changed. A 404 would mean the"
        echo "file moved or was renamed — check the 1.1 file listing before assuming it is gone,"
        echo "because a 403 from PhysioNet's /files/ tree is returned for absent paths too and"
        echo "so says nothing about existence either way."
        exit 1
    fi
    got=$(wc -c < "$REL/$f.part" | tr -d ' ')
    mv "$REL/$f.part" "$REL/$f"
    if [[ "$got" == "$want" ]]; then
        printf '  ok      %-16s %12s bytes\n' "$f" "$got"
    else
        printf '  SIZE?   %-16s %12s bytes (listing said %s)\n' "$f" "$got" "$want"
    fi
done

echo
echo "Next: verify by measurement, not by checksum."
echo "    python tools/gold_provenance_check.py check --release-dir <release-dir>"
echo "It reports (a) the placeholder-vs-gold decomposition, (b) the gold types of the"
echo "value-payload placeholders, and (c) the silver-gold disagreement rate, and it REFUSES"
echo "to print (a)-(c) if the offsets do not land inside the record bodies."
echo
echo "Handling rules, unchanged and still binding:"
echo "  - id.deid is numbers only and safe to print. id-phi.phrase field 6 is the PHI phrase;"
echo "    never read it. The check tool splits that file with maxsplit=5 for this reason."
echo "  - Never quote a span surface form: not in docs/notes/, not in a commit message, not"
echo "    in an agent prompt, not in an exception message."
echo
echo "Corpus ID: $CORPUS   (config/naming.yaml)"
echo "Cite, for the corpus and for the reference alike:"
echo "  Neamatullah I, Douglass MM, Lehman LH, Reisner A, Villarroel M, Long WJ, Szolovits P,"
echo "  Moody GB, Mark RG, Clifford GD. Automated de-identification of free-text medical"
echo "  records. BMC Med Inform Decis Mak 2008;8:32."
echo "  Corpus   : 10.13026/jc2a-ca12  (PhysioNet Credentialed Health Data License 1.5.0)"
echo "  Reference: physionet.org/content/deid/1.1/  (Open Data Commons Attribution v1.0 —"
echo "             attribution is a licence condition, not a courtesy)"
