#!/usr/bin/env python3
"""Build `ko-surro`'s corpus root from the Korean derivation and the source release, and seal it.

`ko-surro` and `en-deid` are two halves of one PhysioNet release (DESIGN §6.5): the Korean
corpus is the release's own automatic de-identification (`id.res`) translated, with a
surrogate substituted for each placeholder. Both directories this tool reads — the release
and the producing project's `derived/` — are shared and read-only, and nothing here writes to
either.

Three stages, in this order, with `python3 -m src.split --corpus ko-surro` between the second
and the third — the order DESIGN §6.2 requires, generate → freeze → seal:

    python3 tools/prepare_kosurro.py stage --release-dir <the release> --derived-dir <derived>
    python3 -m src.split --corpus ko-surro          # then commit splits/ko-surro.json
    python3 tools/prepare_kosurro.py seal

and, for `tools/derive_aligned_split.py`'s command-line form:

    python3 tools/prepare_kosurro.py manifest --out <path>

**`stage` is not a copy.** `en-deid`'s is: the release ships files this repository can read
as they are. Here the corpus root is *computed*, because the span set DESIGN §6.5 (v)
pre-registered does not exist in any shipped file. It is the Korean silver spans whose source
English placeholder the human reference of `id.deid` supports, and constructing it needs
three files joined:

  - `id.res` and `id.text` — the masked and original English bodies, aligned by
    `tools/gold_provenance_check.align` so every placeholder has a position in `id.text`;
  - `id.deid` — the human reference, whose offsets index `id.text`;
  - the Korean `ko_surrogate.jsonl` — the published corpus, whose spans carry the source
    placeholder literal and so can be joined back to a placeholder position for position.

The verdict per span is then the one-to-one overlap `gold_provenance_check check` reports, and
the numbers this tool must reproduce are in `docs/notes/ko-surro-gold-provenance.md` §10:
2,158 Korean spans, 1,614 supported, 544 not, 0 unjoined.

**What the corpus root does not contain.** Not `src_tag`, and not `surrogate` under that name.
30.5% of placeholder payloads are values rather than type names (§10.7), so the literal is
corpus text, and the whole reason the derived root exists is that this repository reads a form
of the corpus with the join already done and the literal left behind. `src/corpora/kosurro.py`
refuses a record that carries either key. The surrogate *string* is written, as `surface`, for
the reason every loader here writes one: `Document.assert_offsets` compares it against the
slice, and a span whose offsets have drifted is then a refusal rather than a silent
mis-scoring.

**Why the join is done here and not in the loader.** It needs the English release, and reading
it on every load would make `ko-surro`'s span set depend on two corpora being present and
would put the source bodies in the path of every Korean arm. Done once here, the verdict is a
recorded fact in the derived root that `reference.json` counts and the loader recounts.

Data handling. Both inputs are authentic clinical text under a DUA — one English, one its
Korean surrogate — and neither is less restricted than the other. Nothing here prints a body,
a placeholder literal, a surrogate value, a PHI phrase or a resolved path: counts, file names,
fold names, span indices and offsets only (CLAUDE.md, applied without asking which corpus).
Both destinations are places `tools/release_screen.py` refuses to let into a commit — `data/`
is deny-listed and `sealed/` is reported BLOCKED whatever `.gitignore` says.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora import base  # noqa: E402
from src.corpora.kosurro import (  # noqa: E402
    CORPUS_FILE,
    FILES,
    REFERENCE_BASIS,
    REFERENCE_FILE,
)

CORPUS = "ko-surro"

#: The release files the join needs, and what each supplies. `id-phi.phrase` is read only so
#: that `parse_reference` can check the two reference files list the same spans in the same
#: order; no type of its own is used here, because §9.0 types this corpus from its own 26-tag
#: vocabulary and typing it from the English reference is refused there.
RELEASE_FILES = ("id.res", "id.text", "id.deid", "id-phi.phrase")

#: The producing project's published Korean corpus. Arm B, the one the paper distributes;
#: `ko_placeholder.jsonl` is the deterministic Arm A derivation and `ko_tagged.jsonl` carries
#: no span structure, so neither is the corpus (`config/data_paths.local.yaml`).
DERIVED_FILE = "ko_surrogate.jsonl"

#: Below this fraction of gold spans landing inside their own record body, the reference parse
#: is wrong rather than the corpus disagreeing, and no verdict is written.
#: `gold_provenance_check check` uses the same threshold for the same reason.
MIN_LAND = 0.95


def sealed_splits() -> tuple[str, ...]:
    """The folds whose records leave the corpus root, from the loader class.

    Read rather than spelled here so "which folds are sealed" keeps one answer
    (`base.CorpusLoader`), as in `tools/prepare_endeid.py`.
    """
    from src.corpora.kosurro import KosurroLoader

    return KosurroLoader.sealed_splits


# ─── the join, from three release files and one Korean file ─────────────────────


def _placeholder_payloads(body: str) -> list[str]:
    """Each placeholder's payload, in order, normalised the one way this tool normalises.

    `align()` enumerates placeholders with the same regex in the same order, so index *i*
    here is index *i* there. Taken from the body directly rather than from `Aligned.label`,
    which is `None` for a value payload by design — 30.5% of them — and joining on it would
    silently drop every date.
    """
    from tools.gold_provenance_check import _TAG_RE

    return [match.group(1).strip() for match in _TAG_RE.finditer(body)]


def _korean_payload(src_tag: str, uid: str, position: int) -> str:
    """The payload inside a Korean span's source placeholder literal.

    The literal is `[**payload**]` or `[**payload **]` — the release writes both. Nothing
    about the payload reaches the message: a record id and a span position locate the span,
    and the value is exactly what may not be echoed (§10.7).
    """
    literal = src_tag.strip()
    if not (literal.startswith("[**") and literal.endswith("**]")):
        raise SystemExit(
            f"{DERIVED_FILE}: record {uid} span {position} has a src_tag that is not a "
            f"placeholder literal ({len(literal)} characters). Refusing rather than "
            "guessing where the payload is — that guess decides which placeholder the span "
            "joins to, and so decides its verdict."
        )
    return literal[3:-3].strip()


def supported_placeholders(release_dir: Path) -> tuple[dict[str, set[int]], dict[str, int]]:
    """Which placeholders the human reference supports, by record and placeholder index.

    The one-to-one overlap `gold_provenance_check check` reports, recomputed here rather than
    parsed out of its output: this is the definition of DESIGN §6.5 (v)'s span set, and a
    span set defined by scraping a report is one that changes when the report's formatting
    does. A placeholder is supported iff it overlaps a gold span that no earlier placeholder
    of the same record has already claimed.
    """
    from tools import gold_provenance_check as gpc

    masked = gpc.parse_records(release_dir / "id.res")
    original = gpc.parse_records(release_dir / "id.text")
    if len(masked) != len(original):
        raise SystemExit(
            f"the release's id.res holds {len(masked)} records and id.text holds "
            f"{len(original)}. They are the same corpus before and after masking, so this "
            "is not a corpus this tool can join."
        )
    pairs = list(zip(masked, original))
    mismatched = sum(1 for a, b in pairs if a.uid != b.uid)
    if mismatched:
        raise SystemExit(
            f"the release's id.res and id.text disagree on the record key in {mismatched} "
            f"of {len(pairs)} positions"
        )

    aligned = {a.uid: gpc.align(a, b) for a, b in pairs}
    bodies = {b.uid: b.body for _, b in pairs}
    table = gpc.parse_reference(release_dir / "id.deid", release_dir / "id-phi.phrase")
    basis, land = gpc._validate_offsets(table, aligned, bodies)
    if land < MIN_LAND:
        raise SystemExit(
            f"only {land:.1%} of the human reference's spans land inside their own record "
            f"body under the {basis} convention, below the {MIN_LAND:.0%} floor. That is "
            "what a wrong column-role inference looks like and it is indistinguishable "
            "from real disagreement, so no verdict is written. Fix "
            "gold_provenance_check.parse_reference first."
        )

    supported: dict[str, set[int]] = {}
    counts = {"placeholders": 0, "supported": 0, "unrecoverable": 0}
    for uid, tags in aligned.items():
        gold = table.spans.get(uid, [])
        claimed: set[int] = set()
        hits: set[int] = set()
        for tag in tags:
            counts["placeholders"] += 1
            if tag.span is None:
                counts["unrecoverable"] += 1
                continue
            hit = next(
                (
                    index
                    for index, span in enumerate(gold)
                    if index not in claimed
                    and not (span.end <= tag.span.start or tag.span.end <= span.start)
                ),
                None,
            )
            if hit is None:
                continue
            claimed.add(hit)
            hits.add(tag.tag_index)
            counts["supported"] += 1
        supported[uid] = hits
    counts["records_with_reference"] = len(table.spans)
    return supported, counts


def korean_records(derived_dir: Path) -> list[dict]:
    """The published Korean corpus, one dict per line, in file order."""
    path = derived_dir / DERIVED_FILE
    records: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"{DERIVED_FILE} line {lineno} is not valid JSON (column {exc.colno})"
            ) from None
    return records


def build_records(release_dir: Path, derived_dir: Path) -> tuple[list[dict], dict[str, int]]:
    """The derived root's records: Korean text, spans, and each span's gold-support verdict.

    The join is **exact and total on the Korean side**. Within a record, placeholders are
    ordered and so are the spans, and each span claims the first not-yet-claimed placeholder
    carrying the same literal. A span that claims none refuses the whole stage: there are
    2,158 of them and all 2,158 join (§10.2), so one that does not means the Korean corpus and
    the release have come apart, and a verdict guessed for it would be a silent change to the
    scoring basis.

    Not a positional join, even though the sequences are identical in 2,402 of 2,434 records.
    26 records are reordered by translation and 6 drop a placeholder, so position *i* is not
    position *i* there — and a positional join would have produced a verdict for every span
    while being wrong in those 32.
    """
    from tools import gold_provenance_check as gpc

    supported, release_counts = supported_placeholders(release_dir)
    payloads = {
        record.uid: _placeholder_payloads(record.body)
        for record in gpc.parse_records(release_dir / "id.res")
    }
    # The patient and the note index come from the release's own parser, which reads them out
    # of the record header as two fields. The Korean file carries only the composed `uid`, and
    # this tool never takes a composed key apart — `tools/derive_aligned_split.py`'s first
    # property, for the same reason: splitting it would be a second answer to which half is
    # the patient.
    header = {
        record.uid: (record.rec_id, record.sub_id)
        for record in gpc.parse_records(release_dir / "id.text")
    }

    out: list[dict] = []
    counts = {
        "records": 0,
        "records_without_reference": 0,
        "silver_spans": 0,
        "gold_supported": 0,
        "gold_unsupported": 0,
    }
    for index, korean in enumerate(korean_records(derived_dir)):
        uid = korean.get("uid")
        if not isinstance(uid, str) or uid not in header:
            raise SystemExit(
                f"{DERIVED_FILE} record {index} carries a uid the release does not have. "
                "The two are 2,434 of 2,434 by construction (DESIGN §6.5); a uid outside "
                "the release means the Korean corpus was built from something else."
            )
        patient, note = header[uid]
        text = korean["text"] if "text" in korean else korean.get("ko")
        if not isinstance(text, str):
            raise SystemExit(
                f"{DERIVED_FILE} record {index} has no Korean body under 'text' or 'ko'"
            )
        available = list(payloads[uid])
        claimed: set[int] = set()
        spans: list[dict] = []
        raw_spans = sorted(korean.get("spans", []), key=lambda s: s["start"])
        for position, raw in enumerate(raw_spans):
            payload = _korean_payload(raw["src_tag"], uid, position)
            tag_index = next(
                (
                    i
                    for i, candidate in enumerate(available)
                    if i not in claimed and candidate == payload
                ),
                None,
            )
            if tag_index is None:
                raise SystemExit(
                    f"{DERIVED_FILE}: record {uid} span {position} "
                    f"({raw['start']}..{raw['end']}) carries a source placeholder literal "
                    f"that no unclaimed placeholder of that record in id.res matches "
                    f"({len(available)} placeholders, {len(claimed)} already claimed). The "
                    "join is total on 2,158 of 2,158 spans as published; refusing rather "
                    "than writing a span with no verdict."
                )
            claimed.add(tag_index)
            surrogate = raw["surrogate"]
            if text[raw["start"] : raw["end"]] != surrogate:
                raise SystemExit(
                    f"{DERIVED_FILE}: record {uid} span {position} "
                    f"({raw['start']}..{raw['end']}) does not contain the surrogate the "
                    f"same span records (slice {raw['end'] - raw['start']} characters, "
                    f"surrogate {len(surrogate)}). Every span agrees as published; a "
                    "disagreement means the offsets and the text came from different runs."
                )
            is_supported = tag_index in supported.get(uid, set())
            counts["gold_supported" if is_supported else "gold_unsupported"] += 1
            spans.append(
                {
                    "start": raw["start"],
                    "end": raw["end"],
                    "type": raw["type"],
                    "surface": surrogate,
                    "gold_supported": is_supported,
                }
            )
        counts["records"] += 1
        counts["silver_spans"] += len(spans)
        # `supported` has an entry for every record, including those the reference never
        # mentions, so membership there says nothing. The reference's own record headers are
        # the authority: a header with no span means PHI-free *according to the reference*,
        # and no header means the reference does not speak about the record (DESIGN §9.0).
        has_reference = uid in _reference_headers(release_dir)
        if not has_reference:
            counts["records_without_reference"] += 1
            if spans:
                raise SystemExit(
                    f"{DERIVED_FILE}: record {uid} is outside the human reference and "
                    f"carries {len(spans)} joined span(s). All nine such records carry 0 "
                    "spans as published; a span here would have no verdict to record."
                )
        out.append(
            {
                "uid": uid,
                "patient": patient,
                "note": note,
                "has_reference": has_reference,
                "text": text,
                "spans": spans,
            }
        )
    counts["placeholders_in_release"] = release_counts["placeholders"]
    counts["placeholders_supported_in_release"] = release_counts["supported"]
    return out, counts


_HEADERS: dict[Path, frozenset[str]] = {}


def _reference_headers(release_dir: Path) -> frozenset[str]:
    """Records the human reference has a header for, cached per release directory.

    A header and no span means "no PHI here" and is a statement; no header means the
    reference says nothing about the record. The distinction decides `has_reference`, so it
    is read from `id.deid`'s headers and not from whether a record has spans.
    """
    if release_dir not in _HEADERS:
        from tools import gold_provenance_check as gpc

        table = gpc.parse_reference(release_dir / "id.deid", None)
        _HEADERS[release_dir] = frozenset(table.spans)
    return _HEADERS[release_dir]


# ─── writing a root ────────────────────────────────────────────────────────────


def count_records(records: list[dict]) -> dict[str, int]:
    """The counts `reference.json` carries, over whichever records a root holds.

    Recomputed from the records rather than carried along, so that the sidecar of the sealed
    root describes the sealed root and not the corpus it was cut from. The loader recounts
    these on every load and refuses a disagreement.
    """
    return {
        "records": len(records),
        "records_without_reference": sum(
            1 for record in records if not record["has_reference"]
        ),
        "silver_spans": sum(len(record["spans"]) for record in records),
        "gold_supported": sum(
            1 for record in records for span in record["spans"] if span["gold_supported"]
        ),
        "gold_unsupported": sum(
            1
            for record in records
            for span in record["spans"]
            if not span["gold_supported"]
        ),
    }


def write_root(root: Path, records: list[dict]) -> dict[str, int]:
    """Write one root's two files. Returns the counts written beside them."""
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records
    ]
    (root / CORPUS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = count_records(records)
    reference = {
        "corpus": CORPUS,
        "reference": REFERENCE_BASIS,
        "reference_note": (
            "A Korean silver span is gold iff the human reference of the source release's "
            "id.deid supports the English placeholder it was injected from (DESIGN §6.5 "
            "(v), pre-registered 2026-09-22). Written by tools/prepare_kosurro.py, which "
            "does the join once; the loader applies the recorded verdict and never reads "
            "the English release."
        ),
        "counts": counts,
        "counts_note": (
            "Over the records in this root only. The loader recounts every one of them and "
            "refuses a disagreement — the split file's denominators come from the file "
            "that was read, so a sidecar that had drifted from its data would move a "
            "leak-rate denominator with nothing saying so."
        ),
    }
    (root / REFERENCE_FILE).write_text(
        json.dumps(reference, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return counts


def _print_counts(where: str, counts: dict[str, int]) -> None:
    print(f"{where}")
    for name, value in counts.items():
        print(f"    {name:28} {value:6}")


# ─── stages ────────────────────────────────────────────────────────────────────


def cmd_stage(release_dir: Path, derived_dir: Path) -> int:
    """Compute the derived root from the release and the Korean corpus."""
    root = base.corpus_root_to_create(CORPUS)
    if base.sealed_root(CORPUS) is not None:
        print(
            f"{CORPUS} already has a sealed root configured. Staging now would rebuild the "
            "corpus root from every record and put the sealed fold back into it. Refusing.",
            file=sys.stderr,
        )
        return 1
    present = [name for name in FILES if (root / name).exists()]
    if present:
        print(
            f"the corpus root already holds {present}. Delete them deliberately if the "
            "corpus is genuinely being rebuilt — everything downstream is dated from the "
            "split file that was built on them.",
            file=sys.stderr,
        )
        return 1
    missing = [name for name in RELEASE_FILES if not (release_dir / name).is_file()]
    if missing:
        print(f"the release directory does not hold {missing}", file=sys.stderr)
        return 1
    if not (derived_dir / DERIVED_FILE).is_file():
        print(f"the derived directory does not hold {DERIVED_FILE}", file=sys.stderr)
        return 1

    records, counts = build_records(release_dir, derived_dir)
    written = write_root(root, records)
    _print_counts(f"staged  {CORPUS_FILE} + {REFERENCE_FILE}", written)
    print(
        f"    {'placeholders in id.res':28} "
        f"{counts['placeholders_in_release']:6}  (the Korean corpus carries "
        f"{written['silver_spans']}; the difference is the release's own excluded spans)"
    )
    print(
        f"\n{CORPUS} corpus root staged. Next: python3 -m src.split --corpus {CORPUS}"
    )
    return 0


def cmd_seal() -> int:
    """Move the sealed fold's records out of the corpus root, per the frozen split."""
    from src import split as split_module

    sealed = base.sealed_root(CORPUS)
    if sealed is None:
        print(
            f"config/data_paths.local.yaml has no `sealed:` entry for {CORPUS}. Add one "
            f"pointing at sealed/{CORPUS} (the directory need not exist yet) and re-run: "
            "the seal is a path this code writes to, and inventing it here would put the "
            "corpus's test fold somewhere the config does not record.",
            file=sys.stderr,
        )
        return 1
    already = [name for name in FILES if (sealed / name).exists()]
    if already:
        print(
            f"the sealed root already holds {already}. The seal has run; a second run "
            "would read the sealed fold to rewrite it. Refusing.",
            file=sys.stderr,
        )
        return 1

    root = base.corpus_root(CORPUS)
    record = split_module.read(CORPUS)
    fold_of = split_module.fold_of(record)
    folds = sealed_splits()

    records = [
        json.loads(line)
        for line in (root / CORPUS_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # A record with no reference is in no fold, and still has text. Its patient decides which
    # side of the seal that text belongs on — the same rule `tools/prepare_endeid.py` applies
    # to the same nine records, and for the same reason: leaving a sealed patient's note
    # where rule development reads it is the other half of the seal.
    patient_side: dict[str, str] = {}
    for item in records:
        fold = fold_of.get(item["uid"])
        if fold is None:
            continue
        if patient_side.setdefault(item["patient"], fold) != fold:
            print(
                f"splits/{CORPUS}.json puts one patient's notes in more than one fold. The "
                "folds are patient-disjoint by construction and derived from a split that "
                "is too, so this is a corrupted split file — do not seal.",
                file=sys.stderr,
            )
            return 1

    unplaced = [
        item["uid"]
        for item in records
        if fold_of.get(item["uid"]) is None and item["patient"] not in patient_side
    ]
    if unplaced:
        print(
            f"{len(unplaced)} record(s) are in no fold and belong to a patient that appears "
            "in no fold, so nothing says which side of the seal their text belongs on. "
            "Refusing.",
            file=sys.stderr,
        )
        return 1

    def side(item: dict) -> str:
        fold = fold_of.get(item["uid"])
        return fold if fold is not None else patient_side[item["patient"]]

    to_seal = [item for item in records if side(item) in folds]
    to_keep = [item for item in records if side(item) not in folds]
    if not to_seal:
        print(
            f"the frozen split puts no record in {list(folds)}, so there is nothing to "
            "seal and the seal would be a pair of empty files the loader refuses to read.",
            file=sys.stderr,
        )
        return 1

    # The sealed root is written before the corpus root is rewritten. A crash between the two
    # leaves the sealed fold in both places, which `--check` catches; the reverse order could
    # lose it.
    _print_counts(f"sealed  {', '.join(folds)}", write_root(sealed, to_seal))
    _print_counts("root    the remaining folds", write_root(root, to_keep))
    print(
        f"\n{CORPUS}: {', '.join(folds)} moved out of the corpus root. Verify with\n"
        f"    python3 -m src.split --corpus {CORPUS} --check"
    )
    return 0


def cmd_manifest(out: Path) -> int:
    """Emit `<document> <patient> <note index>` for `tools/derive_aligned_split.py`.

    `src/split.py`'s derived route builds the same rows in memory and never writes them, so
    this exists for the command-line derivation and for checking the two agree. Three fields,
    never a composed key, for that module's stated reason.
    """
    from src.corpora.kosurro import KosurroLoader

    loader = KosurroLoader(use_split_file=False)
    rows = sorted(
        (doc.doc_id, doc.meta["patient_id"], doc.meta["note_index"])
        for doc in loader.load()
    )
    out.write_text(
        "# ko-surro document, source patient, source note index\n"
        + "".join(f"{document}  {patient}  {note}\n" for document, patient, note in rows),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} manifest lines")
    print(
        f"  {len(loader.uncovered)} record(s) carry no reference and are not loaded, so they "
        "are not in the manifest — the derivation refuses them by the same reason "
        "(source_note_unassigned) whether or not a line names them."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="stage", required=True)
    stage = sub.add_parser(
        "stage", help="compute the derived root from the release and the Korean corpus"
    )
    stage.add_argument("--release-dir", required=True, type=Path)
    stage.add_argument("--derived-dir", required=True, type=Path)
    sub.add_parser("seal", help="move the sealed fold out of the corpus root")
    manifest = sub.add_parser(
        "manifest", help="emit the derivation's three-field manifest"
    )
    manifest.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.stage == "stage":
        for name, path in (("release", args.release_dir), ("derived", args.derived_dir)):
            if not path.is_dir():
                print(f"the {name} directory does not exist", file=sys.stderr)
                return 2
        return cmd_stage(args.release_dir, args.derived_dir)
    if args.stage == "manifest":
        return cmd_manifest(args.out)
    return cmd_seal()


if __name__ == "__main__":
    raise SystemExit(main())
