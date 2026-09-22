#!/usr/bin/env python3
"""Derive `ko-surro`'s fold assignment from `splits/en-deid.json`. DESIGN §6.5, option B.

`en-deid` and `ko-surro` are the source and the surrogate of one PhysioNet release, so
their overlap is not partial and uncertain — it is **2,434 of 2,434, exactly known**. §6.5
adopts option B for that pair: corresponding documents take the same fold role in both
corpora, and `splits/en-deid.json` is the one defined first. This file is the derivation
that makes the second one a function of the first.

**What this tool does not do: write `splits/ko-surro.json`.** §6.5 states the reason and it
is not procedural. That schema's whole purpose is that a reader need not re-read the corpus
to know what a fold held — documents, tokens, spans per fold — and nothing in this
repository can read the Korean text yet, so a file written today would put unmeasured
assertions where the seal's reference point goes.

*Corrected 2026-09-22*: this docstring, §6.5, and the commit that added this file all said
the Korean text "is not on this machine." It is. What was checked was
`config/data_paths.local.yaml`'s `ko-surro` entry, which pointed at the source release —
`en-deid`'s own root — and an entry pointing at the wrong directory was read as the corpus
being absent. The three derived JSONL files are beside that release, 2,434 records each,
keyed by exactly this module's key space. **The conclusion survives the correction and its
ground changes**: not "there is no text to measure" but "there is no loader, no derived
corpus root, and no `SPLIT_ORIGIN` route to measure it with." So what exists before the
corpus is *readable* is the derivation, and when a loader arrives nothing about its split is
free: this module supplies the fold membership and `src/split.py` measures the contents.

Three properties are what make it a derivation rather than a second sampling:

  - **Composition only, never decomposition.** A ko-surro document is identified by its
    source note's `{patient}_{note index}`, and this file composes that key exactly as
    `src/corpora/endeid.py` and `tools/prepare_endeid.py` do. It never splits a composed id
    back apart — that would be a second answer to which half is the patient. The manifest
    therefore carries the two fields separately; see `read_manifest`.
  - **Refusal, not inference.** A document whose source note `en-deid`'s split does not
    assign is refused and counted, with a reason from `config/naming.yaml`'s
    `alignment_refusal`. The nine reference-less records are that population, and refusing
    them leaves the *same* nine documents outside both corpora's folds.
  - **Pinned to the frozen split.** Every derivation records `splits/en-deid.json`'s
    `manifest_digest` and freeze commit. A resampling of `en-deid` is a resampling of
    `ko-surro`, permanently (§6.5, "what B costs"), and the digest is what makes a stale
    derivation visible instead of merely wrong.

**Alignment is at document level only** (§6.5). A document's fold role transfers; a span's
does not — the human reference's offsets index the English `id.text`, and 59 of the 1,779
gold spans have no Korean counterpart at all (`ko-surro-gold-provenance.md` §7). Nothing
here licenses scoring one corpus with the other's reference.

**The seal is not read, and does not need to be.** Fold membership is a committed, readable
artefact; CLAUDE.md seals the test fold's *text*. This tool opens one split file and one
optional manifest of identifiers, consults no text, and opens no `sealed/` path. §6.5
records that the 2026-08-27 refusal of option B overstated its ground on exactly this
point.

Usage — today, with no loader able to enumerate the Korean corpus:

    python3 tools/derive_aligned_split.py --check

and once one exists, with a manifest it emits:

    python3 tools/derive_aligned_split.py --manifest <path>
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora import base  # noqa: E402

#: The split defined first and the one derived from it. Not arguments: §6.5 decides the
#: direction, and a `--from`/`--to` pair would make the decision look like a runtime
#: choice. If a third such pair ever exists it gets its own declaration here, next to the
#: section that argued for it.
SOURCE_CORPUS = "en-deid"
DERIVED_CORPUS = "ko-surro"


class AlignmentError(Exception):
    """The derivation does not hold. Distinct from a refusal, which drops one document."""


# ─── the key, composed in one direction ────────────────────────────────────────


@dataclass(frozen=True)
class SourceNote:
    """One manifest line: a ko-surro document and the source note it came from.

    `patient` and `note` are separate fields because that is how the producing project's
    record parser reads them out of `START_OF_RECORD=<patient>||||<note>||||`. Keeping them
    separate all the way to `key()` is what lets this file compose and never decompose.
    """

    document: str
    patient: str
    note: str
    line: int

    def key(self) -> str:
        """The source note's document id, composed as the loader composes it."""
        return f"{self.patient}_{self.note}"


def read_manifest(path: Path) -> list[SourceNote]:
    """`<ko-surro document>  <patient>  <note index>` per line; `#` comments and blanks skipped.

    A contract chosen here, and the intended producer is `ko-surro`'s loader when it
    exists. Three fields rather than a composed id for the reason in this module's
    docstring, and the document first because it is the thing being assigned.

    Nothing in a manifest is corpus text, and no message below echoes a field: a line
    number and a count are enough to find the line, and this release is under a DUA whether
    or not the field in question happens to be an identifier (CLAUDE.md applies the rule
    without asking which corpus).
    """
    notes: list[SourceNote] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 3:
            raise AlignmentError(
                f"manifest line {lineno} has {len(fields)} whitespace-separated fields, "
                "expected 3 (document, patient, note index). Refusing rather than "
                "guessing which field is which — that guess is how a fold assignment "
                "silently shifts by one."
            )
        notes.append(
            SourceNote(document=fields[0], patient=fields[1], note=fields[2], line=lineno)
        )
    return notes


# ─── what the frozen split assigns ─────────────────────────────────────────────


def source_split() -> dict:
    """`splits/en-deid.json`, schema-checked. The only authority on fold membership."""
    from src import split as split_module

    return split_module.read(SOURCE_CORPUS)


def assigned_folds(record: dict) -> dict[str, str]:
    """source note id -> fold, for every note the frozen split places."""
    from src import split as split_module

    return split_module.fold_of(record)


def unassigned_notes(record: dict) -> frozenset[str]:
    """Source notes the split file records as being in no fold, by name.

    `en-deid`'s nine reference-less records. Read from the split file's own
    `records_without_reference` rather than computed as "everything not in a fold", because
    the two differ in the case that matters: a note missing from the split file for some
    *other* reason must be `source_note_unknown` and not quietly reclassified as one of the
    nine.
    """
    listed = record["corpus_specific"].get("records_without_reference")
    if listed is None:
        raise AlignmentError(
            f"splits/{SOURCE_CORPUS}.json has no corpus_specific."
            "records_without_reference. The derivation needs to tell a note that is "
            "outside every fold for a recorded reason from one the split file never saw."
        )
    count = record["corpus_specific"].get("n_records_without_reference")
    if count != len(listed):
        raise AlignmentError(
            f"splits/{SOURCE_CORPUS}.json records "
            f"n_records_without_reference={count!r} and lists {len(listed)} of them"
        )
    return frozenset(listed)


# ─── the derivation ────────────────────────────────────────────────────────────


@dataclass
class Derivation:
    """A fold for every ko-surro document that has one, and a reason for each that does not."""

    #: `splits/en-deid.json`'s per-document manifest digest and freeze commit. A derivation
    #: carrying a different digest than the split file on disk was made against another
    #: sampling and must be re-run, not reconciled.
    source_manifest_digest: str
    source_commit: str
    assigned: dict[str, str]
    refused: dict[str, str]
    #: patient -> fold, accumulated from the manifest's own assigned notes. This is the
    #: "환자 단위" half of §6.5's option B made checkable: it is built, not assumed, and a
    #: patient whose notes disagree raises.
    patient_folds: dict[str, str]
    #: Refused document -> the fold whose *text* root it belongs in, decided by its
    #: patient. `tools/prepare_endeid.py` does the same thing for the same nine records on
    #: the English side, and for the same reason: a refused note still has text, and
    #: leaving a sealed patient's text where rule development reads it is the other half of
    #: the seal.
    text_side: dict[str, str]
    #: Refused documents whose patient appears nowhere else in the manifest, so nothing
    #: says which side of the seal their text belongs on. Reported, never guessed.
    unplaced: list[str]
    #: Source notes the frozen split assigns that no manifest line claimed. Not an error:
    #: before the corpus arrives this is all 2,425 of them.
    unclaimed: list[str]

    def counts(self) -> dict[str, int]:
        by_reason = {reason: 0 for reason in sorted(base.alignment_refusals())}
        for reason in self.refused.values():
            by_reason[reason] += 1
        return {
            "assigned": len(self.assigned),
            "refused": len(self.refused),
            **{f"refused.{name}": n for name, n in by_reason.items()},
            "unplaced": len(self.unplaced),
            "unclaimed": len(self.unclaimed),
        }

    def by_fold(self) -> dict[str, int]:
        out: dict[str, int] = {fold: 0 for fold in sorted(base.split_names())}
        for fold in self.assigned.values():
            out[fold] += 1
        return out


def derive(record: dict, notes: list[SourceNote]) -> Derivation:
    """Assign each ko-surro document the fold its source note occupies, or refuse it.

    Raises `AlignmentError` when the derivation itself does not hold — one patient's notes
    in two folds. That is not a refusal: a refusal drops a document, and this would leave a
    corpus half-aligned while every per-corpus check reported the seal intact, which is the
    failure DESIGN §6.5 opens with.
    """
    folds = assigned_folds(record)
    unassigned = unassigned_notes(record)

    claims: dict[str, list[SourceNote]] = {}
    for note in notes:
        claims.setdefault(note.key(), []).append(note)

    assigned: dict[str, str] = {}
    refused: dict[str, str] = {}
    patient_folds: dict[str, str] = {}
    duplicate = base.check_alignment_refusal("duplicate_source_note")
    for key, claimants in claims.items():
        if len(claimants) > 1:
            for claimant in claimants:
                refused[claimant.document] = duplicate
            continue
        note = claimants[0]
        fold = folds.get(key)
        if fold is None:
            refused[note.document] = base.check_alignment_refusal(
                "source_note_unassigned" if key in unassigned else "source_note_unknown"
            )
            continue
        assigned[note.document] = fold
        settled = patient_folds.setdefault(note.patient, fold)
        if settled != fold:
            raise AlignmentError(
                f"splits/{SOURCE_CORPUS}.json puts one patient's notes in two folds "
                f"({settled} and {fold}), seen at manifest line {note.line}. The folds "
                "are patient-disjoint by construction (DESIGN §9.5), so either the split "
                "file is corrupted or the manifest attributes a note to the wrong "
                "patient. Do not derive from it."
            )

    text_side: dict[str, str] = {}
    unplaced: list[str] = []
    for document in sorted(refused):
        # The patient of a refused document is known only from the manifest line that
        # named it, which is still present: refusal drops the document from the fold
        # assignment, not from the manifest.
        patients = {n.patient for n in notes if n.document == document}
        folds_for = {patient_folds[p] for p in patients if p in patient_folds}
        if len(folds_for) == 1:
            text_side[document] = folds_for.pop()
        else:
            unplaced.append(document)

    unclaimed = sorted(set(folds) - set(claims))
    return Derivation(
        source_manifest_digest=record["source"]["manifest_digest"],
        source_commit=record["repository"]["commit"],
        assigned=assigned,
        refused=refused,
        patient_folds=patient_folds,
        text_side=text_side,
        unplaced=unplaced,
        unclaimed=unclaimed,
    )


# ─── what can be checked before the corpus arrives ─────────────────────────────


def self_check(record: dict) -> dict:
    """Properties of the derivation that hold with no Korean corpus on disk.

    Every one of these is a statement about `splits/en-deid.json` and the mapping this
    module applies to it. None reads a corpus, so this is what "the derivation is fixed"
    means in the interval between `en-deid`'s freeze and `ko-surro`'s arrival.
    """
    folds = assigned_folds(record)
    unassigned = unassigned_notes(record)
    group_key = record["group_key"]
    crossing = group_key["crosses_split"]["n_groups_crossing"]

    overlap = sorted(unassigned & set(folds))
    if overlap:
        raise AlignmentError(
            f"splits/{SOURCE_CORPUS}.json lists {len(overlap)} record(s) as carrying no "
            "reference and also places them in a fold. One of the two is wrong and the "
            "derivation cannot tell which."
        )
    if crossing != 0:
        raise AlignmentError(
            f"splits/{SOURCE_CORPUS}.json reports {crossing} group(s) crossing the "
            "split. Option B transfers a fold role per document, so a group that crosses "
            "on the English side crosses on the Korean side too."
        )
    summed = sum(block["n_documents"] for block in record["folds"].values())
    if summed != len(folds):
        raise AlignmentError(
            f"splits/{SOURCE_CORPUS}.json: folds claim {summed} documents and list "
            f"{len(folds)} ids"
        )
    return {
        "assignable": len(folds),
        "refusable_now": len(unassigned),
        "key_space": len(folds) + len(unassigned),
        "n_groups": group_key["n_groups"],
        "groups_crossing": crossing,
        "manifest_digest": record["source"]["manifest_digest"],
        "commit": record["repository"]["commit"],
        "by_fold": {
            fold: block["n_documents"] for fold, block in sorted(record["folds"].items())
        },
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────


def _print_check(report: dict) -> None:
    print(f"derivation  {SOURCE_CORPUS} -> {DERIVED_CORPUS}   (DESIGN §6.5, option B)")
    print(f"  pinned to  manifest_digest {report['manifest_digest'][:16]}…")
    print(f"             freeze commit   {report['commit'][:12]}")
    print(f"  assignable source notes    {report['assignable']:5}")
    for fold, n in report["by_fold"].items():
        print(f"    {fold:6} {n:5}")
    print(
        f"  refused by construction    {report['refusable_now']:5}  "
        "(source notes the frozen split assigns to no fold)"
    )
    print(f"  key space                  {report['key_space']:5}")
    print(
        f"  patient groups             {report['n_groups']:5}, "
        f"{report['groups_crossing']} crossing the split"
    )
    print(
        f"\nno {DERIVED_CORPUS} manifest read, so no document was assigned. That is the "
        f"state DESIGN §6.5 describes: the derivation is fixed and splits/"
        f"{DERIVED_CORPUS}.json is not written until the corpus exists to measure."
    )


def _print_derivation(derivation: Derivation) -> None:
    counts = derivation.counts()
    reasons = base.alignment_refusals()
    print(f"derivation  {SOURCE_CORPUS} -> {DERIVED_CORPUS}   (DESIGN §6.5, option B)")
    print(f"  pinned to  manifest_digest {derivation.source_manifest_digest[:16]}…")
    print(f"             freeze commit   {derivation.source_commit[:12]}")
    print(f"  assigned                   {counts['assigned']:5}")
    for fold, n in derivation.by_fold().items():
        print(f"    {fold:6} {n:5}")
    print(f"  refused                    {counts['refused']:5}")
    for reason in sorted(reasons):
        print(f"    {reason:24} {counts[f'refused.{reason}']:5}  {reasons[reason]}")
    print(f"  patients seen              {len(derivation.patient_folds):5}")
    print(
        f"  refused, text side known   {len(derivation.text_side):5}  "
        "(by patient, as tools/prepare_endeid.py places the same records)"
    )
    if derivation.unplaced:
        print(
            f"  refused, text side UNKNOWN {len(derivation.unplaced):5}  "
            "— nothing says which side of the seal their text belongs on"
        )
    if derivation.unclaimed:
        print(
            f"  source notes unclaimed     {len(derivation.unclaimed):5}  "
            "(assigned a fold here, named by no manifest line)"
        )
    print(
        f"\nsplits/{DERIVED_CORPUS}.json is not written by this tool. The assignment above "
        f"is what src/split.py consumes once {DERIVED_CORPUS} is on disk and its fold "
        "contents can be measured (DESIGN §6.5)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help=f"{DERIVED_CORPUS} documents and their source notes, three fields per line",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what the derivation fixes with no manifest and no corpus on disk",
    )
    args = parser.parse_args(argv)
    if args.manifest is None and not args.check:
        parser.error("pass --manifest, or --check to report the derivation on its own")

    try:
        record = source_split()
        if args.manifest is None:
            _print_check(self_check(record))
            return 0
        if not args.manifest.is_file():
            print("the manifest does not exist", file=sys.stderr)
            return 2
        self_check(record)
        derivation = derive(record, read_manifest(args.manifest))
    except AlignmentError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    _print_derivation(derivation)
    # A refused document whose text has no side is a seal question with no answer, so it
    # fails rather than printing and returning 0.
    return 1 if derivation.unplaced else 0


if __name__ == "__main__":
    raise SystemExit(main())
