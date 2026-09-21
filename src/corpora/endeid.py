"""`en-deid` loader — PhysioNet's deidentified-medical-text release, English nursing notes.

2,434 authentic nursing notes over 163 patients, with a human reference of 1,779 PHI
spans in ten source types. DESIGN §7.1 records why this corpus populates the English
cell instead of MIMIC-III, and §9.0 records the type mapping. The corpus ships no
split, so one is constructed per DESIGN §9.5 and frozen before any English rule is
written (§6.2) — and its split is defined **first**, with `ko-surro`'s derived from it
(§6.5).

Four things here are corpus-specific, and the first two are why this file shares no
code with the other two loaders.

  - **A document is a record inside a file, not a file.** All 2,434 notes live in one
    `id.text`, framed `START_OF_RECORD=<patient>||||<note>||||` … `||||END_OF_RECORD`,
    and the two reference files are framed by the same two ids. So `source_files()`
    cannot answer "which files is this document made of" — every document would name
    the same three — and the per-document digest the split file records comes from
    `digest_parts()` instead, over the record's own bytes. That is also why the seal
    needs `tools/prepare_endeid.py`: moving a fold out of the corpus root means
    rewriting the three files, not moving files.

  - **The reference is split across two files and the offsets are in the one with no
    text.** `id.deid` holds `start start end` triples under a `Patient … Note …`
    header — the start written twice, which is the convention that identifies the
    format — and `id-phi.phrase` holds `<pid> <note> <start> <end> <type> <phrase>`.
    Offsets come from `id.deid`, types and surfaces from `id-phi.phrase`, and the two
    must agree span for span in file order before a type is attached.

  - **The recorded surface makes the offset assertion real.** `id-phi.phrase` field 6
    is the PHI phrase, so `Span.surface` is the annotation's own string and
    `assert_offsets()` compares it against the slice — the check MEDDOCAN's brat loader
    has and GraSCCo's CAS loader cannot. Measured: 1,779 of 1,779 agree exactly.
    GraSCCo's whitespace-edge invariant is therefore *not* asserted here, and would be
    wrong if it were: 5 of the 1,779 gold surfaces do begin or end on whitespace.

  - **Nine records carry no reference and are not loaded.** 2,425 of the 2,434 have an
    `id.deid` header; 1,690 of those carry zero spans, which is an assertion that the
    record holds no PHI, and nine have no header at all, which is absence of coverage.
    A prediction in them is uncheckable, so they are dropped at document level and
    counted in `uncovered` (DESIGN §9.0). This is not §9.1's mechanism — §9.1 keeps a
    span and flags it, and here there are no spans to keep.

Data handling. This is authentic clinical text under a DUA, so no message in this
module carries a surface, a phrase or a resolved path: span indices, offsets and
lengths only (CLAUDE.md). `tests/test_endeid_loader.py` asserts that of the exception
messages, as `tests/test_meddocan_loader.py` does for its loader.

`tools/gold_provenance_check.py` parses the same release framing for a different
question — where `ko-surro`'s silver reference came from — and deliberately never binds
field 6, because it *prints*. This loader binds it because `Span.surface` is what every
loader here holds. The two readers are cross-checked rather than merged:
`test_the_two_readers_of_the_release_agree` compares their record and span counts.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from .base import CorpusError, CorpusLoader, Document, SealError, Span

#: The three files a fold's worth of this corpus is made of, in the order they are read.
#: `id.res` also ships and is **not** read: it is the de-identification tool's output,
#: which is `ko-surro`'s silver basis (`docs/notes/ko-surro-gold-provenance.md`) and
#: would be a second, disagreeing answer to where the PHI is.
TEXT_FILE = "id.text"
OFFSETS_FILE = "id.deid"
TYPES_FILE = "id-phi.phrase"
FILES = (TEXT_FILE, OFFSETS_FILE, TYPES_FILE)

#: `START_OF_RECORD=<patient>||||<note index>||||`, then the body, then the end mark on
#: a line of its own. The end mark is required to be alone on its line: the alternative
#: — accepting it as a suffix — silently discards whatever preceded it on that line,
#: which would shorten one body and move every offset in it.
_RECORD_START_RE = re.compile(r"^START_OF_RECORD=([^|]+)\|\|\|\|([^|]*)\|\|\|\|\s*$")
_END_MARK = "||||END_OF_RECORD"

#: `id.deid`'s framing and its span lines. `start start end` — the start twice, and the
#: third field is the end rather than the length. The duplication is checked rather than
#: skipped over: it is what distinguishes this format from `(start, end, length)`, and
#: guessing which two of three fields are the span is the inference that has no place in
#: a loader (DESIGN §9.2).
_DEID_HEADER_RE = re.compile(r"^Patient\s+(\S+)\s+Note\s+(\S+)\s*$")
_DEID_SPAN_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$")

#: `id-phi.phrase` is whitespace-separated with five structural fields and the phrase
#: last, so it is split with this many splits and the remainder is the surface. A phrase
#: contains spaces; a split on all whitespace would scatter it across fields.
_PHRASE_FIELDS = 5

#: The release's ten types -> canonical type. DESIGN §9.0's `en-deid` subsection holds
#: the counts and the two decisions that are not mechanical; both are restated here
#: because a reader of this file should not have to take them on trust.
TYPE_MAP: dict[str, str] = {
    # NAME — whose name it is stays in subtype, per §9.0. `RelativeProxyName` is a
    # relative's or proxy's *name* and so is a Safe Harbor identifier; §9.1 excludes
    # relationship *words* (`madre`), which is a different thing and has no label here.
    "HCPName": "NAME",
    "PTName": "NAME",
    "PTNameInitial": "NAME",
    "RelativeProxyName": "NAME",
    # DATE — `DateYear` is 46 standalone year mentions, mapped here rather than
    # excluded so that this corpus's DATE denominator does not differ in kind from
    # MEDDOCAN's FECHAS and GraSCCo's DATE, neither of which separates years out. The
    # distinction survives in subtype (DESIGN §9.0).
    "Date": "DATE",
    "DateYear": "DATE",
    # LOCATION_AREA — the release has one undifferentiated `Location` where our set
    # separates area, street and organisation. §7.2's only admissible move for that
    # shape is merging ours down to one for this corpus; splitting the gold by
    # inference is refused by §9.2. The cost is stated in §9.0 and is real:
    # LOCATION_STREET and ORGANISATION predictions here are false positives by
    # construction, and this corpus's location rows are not cross-corpus comparable.
    "Location": "LOCATION_AREA",
    # CONTACT — telephone only; the release has no email or fax label.
    "Phone": "CONTACT",
    # AGE
    "Age": "AGE",
    # OTHER — the release's own residual bucket, 3 spans.
    "Other": "OTHER",
    # No en-deid type maps to ID, PROFESSION, LOCATION_STREET or ORGANISATION. That is
    # silence rather than zero: the release declares no category for an identifier or an
    # occupation, so one in the text is scored as a false positive, and this corpus's
    # aggregate precision is a lower bound (DESIGN §7.2, §9.0). Its recall is not
    # affected.
}

#: Empty, and that is a fact about the release rather than an omission: it carries no
#: sex, relationship-word or title category, so §9.1's three exclusions have no
#: counterpart here. `n_spans_excluded` is therefore 0 rather than unreported.
EXCLUDED_TYPES: frozenset[str] = frozenset()


class EndeidLoader(CorpusLoader):
    corpus_id = "en-deid"
    type_map = TYPE_MAP
    excluded_types = EXCLUDED_TYPES
    #: Empty for GraSCCo's reason and one more: there is no directory per fold because
    #: there is no file per document. `fold_roots()` raises if anything calls it, and
    #: reachability is answered by `sealed_reachable()` in `source_roots()`.
    fold_dirs: dict[str, str] = {}
    #: Declared, never inferred. The record header's first field is a patient
    #: identifier, so DESIGN §9.5's grouping is answered by the key and its surface-form
    #: fallback does not run. A flag rather than "does `patient_key` return something"
    #: for the reason a span's layer is not derived from its detector's name: a loader
    #: that half-implemented the key would then be treated as having one.
    has_patient_key = True
    #: What the split file's grouping audit reports as the basis of the grouping. The
    #: release's own field, named as such: a reader of splits/en-deid.json can then tell
    #: that the key was shipped and not reconstructed from a filename.
    patient_key_source = (
        f"the first field of each {TEXT_FILE} record header "
        f"(START_OF_RECORD=<patient>||||<note>||||), carried in Document.meta"
    )

    # -- layout --

    def source_roots(self) -> list[Path]:
        """The roots this read may open, unsealed first. GraSCCo's `source_roots`.

        The permission comes from `sealed_reachable()`, which is the single place that
        answers whether a sealed fold may be opened. Before the seal exists this is the
        corpus root alone and the test fold is read from it like any other record.
        """
        roots = [self.root]
        permitted = self.sealed_reachable()
        if permitted is not None:
            roots.append(permitted)
        return roots

    def _files(self, root: Path) -> tuple[Path, Path, Path]:
        """The three files under one root, all of them required.

        Named relative to the root and never absolutely: for this corpus the root is a
        data location under a DUA and does not belong in a message that travels into a
        log (CLAUDE.md).
        """
        paths = [root / name for name in FILES]
        missing = [name for name, path in zip(FILES, paths) if not path.is_file()]
        if missing:
            raise CorpusError(
                f"{self.corpus_id}: {missing} not found under "
                f"{'the sealed' if root != self.root else 'the corpus'} root. The "
                "corpus root is a directory tools/prepare_endeid.py builds from the "
                "source release; check config/data_paths.local.yaml."
            )
        return paths[0], paths[1], paths[2]

    def source_files(self, doc_id: str) -> list[Path]:
        """Refused, and the refusal is the corpus fact.

        Every document is a record inside the same three files, so an honest answer here
        would be the same three paths for all 2,434 of them — and the caller that wants
        this is the split file's per-document digest, which would then record one digest
        repeated 2,434 times and call it per document. `digest_parts()` is the answer
        instead.
        """
        raise CorpusError(
            f"{self.corpus_id}: a document is a record inside {list(FILES)}, not a file, "
            "so there are no per-document files to name. Use digest_parts(), which "
            "hashes the record's own bytes."
        )

    def digest_parts(self, doc: Document) -> list[tuple[str, bytes]]:
        """This record's named byte parts, for the split file's per-document digest.

        Three parts, one per source file, each holding only what belongs to this
        record: the body from `id.text`, and the record's own lines from the two
        reference files reassembled in the order they were read. A digest over the whole
        files would be identical for every document and would not detect a re-release
        that moved one note's annotations.

        The reference parts are reassembled rather than copied out of the file verbatim
        because the loader has already parsed them; reassembling from the parsed values
        makes the digest cover what was *loaded*, which is the thing the split file is
        making a claim about.
        """
        return [
            (TEXT_FILE, doc.text.encode("utf-8")),
            (
                OFFSETS_FILE,
                "\n".join(f"{s.start} {s.end}" for s in doc.spans).encode("utf-8"),
            ),
            (TYPES_FILE, "\n".join(s.subtype for s in doc.spans).encode("utf-8")),
        ]

    def patient_key(self, doc: Document) -> str:
        """The patient this note belongs to. DESIGN §9.5's first branch.

        Read from `meta`, which `_read()` filled from the record header's first field —
        not re-split out of `doc_id`. The composition `{patient}_{note}` is this
        loader's own, and a function that took it apart again would be a second answer
        to which half is the patient, derived from a string instead of from the file.
        """
        key = doc.meta.get("patient_id")
        if not isinstance(key, str) or not key:
            raise CorpusError(
                f"{self.corpus_id}/{doc.doc_id}: no patient_id in meta. The loader "
                "fills it from the record header; a document without one did not come "
                "from this loader."
            )
        return key

    # -- reading --

    def _read(self) -> Iterator[Document]:
        #: Record ids with no `id.deid` header, across every root this read opened. Reset
        #: here rather than in `__init__` so that a second load does not accumulate.
        self.uncovered: list[str] = []
        from_sealed = 0
        sealed = self.sealed_reachable()
        for root in self.source_roots():
            text_path, offsets_path, types_path = self._files(root)
            bodies = self._read_records(text_path)
            spans_by_doc = self._read_reference(offsets_path, types_path, bodies)
            if not bodies:
                raise CorpusError(
                    f"{self.corpus_id}: {TEXT_FILE} under "
                    f"{'the sealed' if root == sealed else 'the corpus'} root holds no "
                    "records"
                )
            for doc_id, (patient_id, body) in bodies.items():
                if doc_id not in spans_by_doc:
                    # No header in id.deid: coverage is absent, not empty. Dropped and
                    # counted (DESIGN §9.0) — a prediction here is uncheckable, and a
                    # document loaded with an empty gold list would make every such
                    # prediction a false positive on no evidence.
                    self.uncovered.append(doc_id)
                    continue
                if root == sealed:
                    from_sealed += 1
                yield Document(
                    doc_id=doc_id,
                    corpus_id=self.corpus_id,
                    text=body,
                    spans=spans_by_doc[doc_id],
                    # Nothing in the layout encodes a fold, so the frozen split file is
                    # the only authority and `_apply_split_file()` fills this in.
                    split=None,
                    had_bom=False,
                    meta={"patient_id": patient_id},
                )
        if sealed is not None and from_sealed == 0:
            # GraSCCo's invariant, for its reason exactly: the access is already in
            # results/sealed_eval_log.md, so a read that returned only the unsealed
            # records would produce numbers from the wrong data under a log row saying
            # the test fold was evaluated.
            raise SealError(
                f"{self.corpus_id}: a sealed read was authorised but the sealed root "
                "holds no records, so the fold was not read at all. The log has already "
                "recorded this access; note in results/sealed_eval_log.md that the run "
                "did not complete, and fix the seal before running again."
            )

    def _read_records(self, path: Path) -> dict[str, tuple[str, str]]:
        """`id.text` -> doc id -> (patient id, body). Strictly framed.

        The body is the lines between the markers joined with `\\n`, which is the base
        the reference offsets count from — measured, not assumed: all 1,779 gold spans
        land inside their body under this construction and all 1,779 slices equal the
        recorded phrase. A marker out of place raises rather than being resynchronised,
        because every offset in the affected record would shift by the length of
        whatever a resynchronisation swallowed.
        """
        records: dict[str, tuple[str, str]] = {}
        header: tuple[str, str] | None = None
        buf: list[str] = []
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            match = _RECORD_START_RE.match(line)
            if match is not None:
                if header is not None:
                    raise CorpusError(
                        f"{self.corpus_id}: {path.name} line {lineno} opens a record "
                        "inside an open one"
                    )
                header = (match.group(1), match.group(2))
                buf = []
                continue
            if line.strip() == _END_MARK:
                if header is None:
                    raise CorpusError(
                        f"{self.corpus_id}: {path.name} line {lineno} closes a record "
                        "with none open"
                    )
                doc_id = f"{header[0]}_{header[1]}"
                if doc_id in records:
                    raise CorpusError(
                        f"{self.corpus_id}: {path.name} holds two records with the "
                        f"same patient and note index (at line {lineno})"
                    )
                records[doc_id] = (header[0], "\n".join(buf))
                header, buf = None, []
                continue
            if _END_MARK in line:
                raise CorpusError(
                    f"{self.corpus_id}: {path.name} line {lineno} carries the record "
                    "end mark with text beside it. Accepting that would drop the text "
                    "before the mark from the body and move every offset in this "
                    "record; the release writes the mark on a line of its own."
                )
            if header is not None:
                buf.append(line)
                continue
            if line.strip():
                # Between records the release writes exactly one blank line — 2,434 of
                # them, all of length 0 (measured). A non-blank line out here would be
                # note text in no record, and ignoring it would drop that text from the
                # corpus silently: it would be scored against nothing, so PHI in it
                # could never be counted as a leak. It shifts no offset, which is
                # precisely why nothing else would notice.
                raise CorpusError(
                    f"{self.corpus_id}: {path.name} line {lineno} has content and is "
                    "outside every record. The release frames every line; refusing "
                    "rather than dropping it."
                )
        if header is not None:
            raise CorpusError(
                f"{self.corpus_id}: {path.name} ends inside an open record"
            )
        return records

    def _read_reference(
        self,
        offsets_path: Path,
        types_path: Path,
        bodies: dict[str, tuple[str, str]],
    ) -> dict[str, list[Span]]:
        """The gold spans, from `id.deid` for offsets and `id-phi.phrase` for the rest.

        A document with a header and no span lines gets an empty list, which is the
        release's assertion that the record holds no PHI, and is different from having
        no header at all — the caller drops the second and keeps the first.

        **Nothing is dropped on disagreement.** The two files agree on every span in
        every record as shipped (2,425 of 2,425 records, 1,779 of 1,779 spans), so a
        disagreement means the release changed and needs a decision; a loader that
        skipped the line would silently shorten the gold and make recall look better
        than it is.
        """
        offsets = self._read_offsets(offsets_path, bodies)
        typed = self._read_types(types_path)

        spans: dict[str, list[Span]] = {}
        for doc_id, extents in offsets.items():
            rows = typed.get(doc_id, [])
            if len(rows) != len(extents):
                raise CorpusError(
                    f"{self.corpus_id}/{doc_id}: {offsets_path.name} lists "
                    f"{len(extents)} spans and {types_path.name} lists {len(rows)}. "
                    "The type of each span is taken positionally, so the two files "
                    "must list the same spans in the same order."
                )
            out: list[Span] = []
            for index, ((start, end), (row_start, row_end, source_type, surface)) in (
                enumerate(zip(extents, rows))
            ):
                if (start, end) != (row_start, row_end):
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: span {index} is "
                        f"[{start}, {end}) in {offsets_path.name} and "
                        f"[{row_start}, {row_end}) in {types_path.name}. A type "
                        "attached to the wrong span is indistinguishable from a real "
                        "finding about the corpus."
                    )
                phi_type, excluded = self.classify(source_type)
                out.append(
                    Span(
                        start=start,
                        end=end,
                        # The annotation's own phrase, so `assert_offsets()` compares
                        # the recorded surface against the slice rather than against
                        # itself. This is the check GraSCCo's CAS cannot support.
                        surface=surface,
                        subtype=source_type,
                        phi_type=phi_type,
                        excluded=excluded,
                    )
                )
            spans[doc_id] = out
        return spans

    def _read_offsets(
        self, path: Path, bodies: dict[str, tuple[str, str]]
    ) -> dict[str, list[tuple[int, int]]]:
        """`id.deid` -> doc id -> extents, in file order."""
        out: dict[str, list[tuple[int, int]]] = {}
        current: str | None = None
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            header = _DEID_HEADER_RE.match(line)
            if header is not None:
                current = f"{header.group(1)}_{header.group(2)}"
                if current in out:
                    raise CorpusError(
                        f"{self.corpus_id}: {path.name} line {lineno} opens a second "
                        "header for a record it has already framed"
                    )
                out[current] = []
                continue
            span = _DEID_SPAN_RE.match(line)
            if span is None or current is None:
                raise CorpusError(
                    f"{self.corpus_id}: {path.name} line {lineno} is neither a "
                    "`Patient <id> Note <n>` header nor a `start start end` triple. "
                    "Skipping it would drop a gold span and make recall look better "
                    "than it is; no line content is quoted here (CLAUDE.md)."
                )
            first, second, third = (int(group) for group in span.groups())
            if first != second:
                raise CorpusError(
                    f"{self.corpus_id}: {path.name} line {lineno} does not repeat its "
                    "start. The duplicated start is what identifies this format, and "
                    "without it which two of the three fields are the span becomes a "
                    "guess (DESIGN §9.2)."
                )
            out[current].append((first, third))

        unknown = sorted(set(out) - set(bodies))
        if unknown:
            raise CorpusError(
                f"{self.corpus_id}: {path.name} frames {len(unknown)} records that "
                f"{TEXT_FILE} does not contain (first: {unknown[:3]}). Annotations "
                "with no text are not loadable and must not be counted as gold."
            )
        return out

    def _read_types(self, path: Path) -> dict[str, list[tuple[int, int, str, str]]]:
        """`id-phi.phrase` -> doc id -> (start, end, type, surface), in file order."""
        out: dict[str, list[tuple[int, int, str, str]]] = {}
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            parts = line.split(None, _PHRASE_FIELDS)
            if len(parts) <= _PHRASE_FIELDS:
                raise CorpusError(
                    f"{self.corpus_id}: {path.name} line {lineno} has "
                    f"{len(parts)} whitespace-separated fields, and this file's "
                    f"format is {_PHRASE_FIELDS} structural fields followed by the "
                    "phrase. No field content is quoted here (CLAUDE.md)."
                )
            patient, note, start, end, source_type, surface = parts
            if not (start.isdigit() and end.isdigit()):
                raise CorpusError(
                    f"{self.corpus_id}: {path.name} line {lineno} has a non-numeric "
                    "offset in field 3 or 4"
                )
            out.setdefault(f"{patient}_{note}", []).append(
                (int(start), int(end), source_type, surface)
            )
        return out
