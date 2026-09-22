"""`ko-surro` loader — the Korean surrogate corpus derived from `en-deid`'s release.

2,434 Korean notes, one per record of the same PhysioNet release `src/corpora/endeid.py`
reads: the producing project ran the release's own de-identification tool, translated
`id.res` into Korean and substituted surrogates for the tool's placeholders. So this corpus
and `en-deid` are two halves of one release, which is why its split is *derived* from
`splits/en-deid.json` rather than sampled (DESIGN §6.5, §9.6) and why its span set is
defined against the English human reference (§6.5 (v)).

Cited as data, and separate in code lineage: CLAUDE.md's first section, and
`docs/notes/ko-surro-gold-provenance.md` for what the reference is and how it was measured.

Five things here are corpus-specific.

  - **The reference is human-verified silver, and the loader is what applies the filter.**
    §6.5 (v) pre-registered the span set: a Korean silver span is gold iff the human
    reference of `id.deid` supports the English placeholder it was injected from. The
    derived root records that verdict per span (`gold_supported`) and this loader drops the
    rest — 544 of 2,158 as shipped — counting them in `n_spans_not_gold_supported`. That is
    a **third** mechanism, neither §9.1's exclusion (which keeps the span and flags it) nor
    `en-deid`'s uncovered records (which drop a document): the span exists, silver asserts
    it is PHI, and the authority does not support it. DESIGN §9.0's `ko-surro` block states
    it in full.

  - **Classification happens before the filter, deliberately.** Every one of the 2,158 spans
    goes through `classify()`, so an unmapped tag raises even when it occurs only among the
    dropped spans — three tags do (§9.0). Filtering first would make the type map's
    exhaustiveness depend on the filter's outcome.

  - **`NOT_PHI_RESTORED` is excluded by §9.1's mechanism and is not on §9.1's list.** 3 of
    its 142 spans survive the filter and they load flagged, so `n_spans_excluded` is a
    reported 3. §9.1's note says why `config/naming.yaml`'s three-name block does not grow.

  - **`src_tag` and `surrogate` are corpus text and this loader refuses to see them.**
    30.5% of source placeholder payloads are values rather than type names
    (`ko-surro-gold-provenance.md` §10.7), so the placeholder literal is as text-bearing as
    the note itself. `tools/prepare_kosurro.py` does not write either key into the derived
    root, and `_record_fields()` refuses a record that carries one: the rule is enforced
    where the data enters rather than remembered at every message. `type` — the normalised
    tag — is the safe field and is the only one any message here names.

  - **A document is a record inside one file.** `ko-surro.jsonl` holds all 2,434, so
    `source_files()` refuses for `en-deid`'s reason and `digest_parts()` answers instead.
    Sealing is a rewrite of that file (`tools/prepare_kosurro.py`), not a move.

Data handling. Korean clinical surrogate text derived from a DUA-restricted release: no
message in this module carries a surface, a placeholder literal or a resolved path — span
indices, offsets, lengths and counts only (CLAUDE.md, applied without asking which corpus).
`tests/test_kosurro_loader.py` asserts that of the exception messages.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .base import CorpusError, CorpusLoader, Document, SealError, Span

#: The two files a root holds. The corpus is one JSON Lines file — the records are not
#: separate files, so the reference and the corpus cannot be split across roots — and
#: `reference.json` is the provenance of the gold-support filter, written per root with that
#: root's own counts so the cross-check below is the same in the sealed root as here.
CORPUS_FILE = "ko-surro.jsonl"
REFERENCE_FILE = "reference.json"
FILES = (CORPUS_FILE, REFERENCE_FILE)

#: What `reference.json` must say it is. A literal rather than a `naming.yaml` value for
#: `src/split.py`'s `SPLIT_ORIGIN` reason: it names a DESIGN decision about how one corpus's
#: reference was constructed, not an axis any result path or span field takes. A root built
#: against raw silver would carry a different string and is refused rather than loaded.
REFERENCE_BASIS = "human-verified silver"

#: Exactly the keys a derived record and one of its spans may carry. Closed sets rather
#: than a minimum, because the two keys that must never appear — `src_tag`, `surrogate` —
#: are keys of the *source* files, and a loader that read the fields it wanted and ignored
#: the rest would load a root that still carried them without anyone noticing.
RECORD_FIELDS = frozenset({"uid", "patient", "note", "has_reference", "text", "spans"})
SPAN_FIELDS = frozenset({"start", "end", "type", "surface", "gold_supported"})

#: Keys of the source derivation that carry corpus text. Named so the refusal can say which
#: rule was broken; naming a *key* is not naming its content.
TEXT_BEARING_FIELDS = ("src_tag", "surrogate")

#: Source tag -> canonical type, for 25 of the corpus's 26 tags. The 26th is
#: `NOT_PHI_RESTORED`, below: excluded rather than mapped, so the two collections together
#: are the exhaustive vocabulary `_check_type_map()` tests against. DESIGN §9.0's `ko-surro`
#: block holds the per-tag counts and the judgements; the ones that are not simple
#: translations of an `en-deid` decision are restated here.
TYPE_MAP: dict[str, str] = {
    # NAME — whose name it is, and which part of it, stays in subtype (§9.0). The tool's
    # vocabulary is finer than any human scheme here: twelve tags, of which `NAME_INITIAL`
    # and `INITIAL` survive the filter 0 times and are mapped anyway, because the map is
    # checked against all 2,158 spans and not only the 1,614 that load.
    "LAST_NAME": "NAME",
    "FIRST_NAME": "NAME",
    "NAME": "NAME",
    "KNOWN_PATIENT_LASTNAME": "NAME",
    "KNOWN_PATIENT_FIRSTNAME": "NAME",
    "DOCTOR_LAST_NAME": "NAME",
    "DOCTOR_FIRST_NAME": "NAME",
    "MALE_FIRST_NAME": "NAME",
    "FEMALE_FIRST_NAME": "NAME",
    "INITIALS": "NAME",
    "NAME_INITIAL": "NAME",
    "INITIAL": "NAME",
    # DATE — `DATE_LITERAL_PENDING_REVIEW` is the tag whose PHI status the producing
    # project left open, and the gold-support filter answers it: its 19 spans split 9
    # supported / 10 not, so excluding the tag would discard 9 spans the human reference
    # supports in order to avoid a review that has already happened for this set (§9.0).
    "DATE": "DATE",
    "DATE_LITERAL": "DATE",
    "DATE_LITERAL_PENDING_REVIEW": "DATE",
    # ORGANISATION — the tool separates hospital, ward and company where `en-deid`'s human
    # reference has one `Location`. That is why the aligned pair's location/organisation
    # rows are not comparable in either direction (§9.0, §7): 242 spans here against 0 gold
    # `ORGANISATION` on the English side, and they are largely the same physical spans.
    "HOSPITAL": "ORGANISATION",
    "WARDNAME": "ORGANISATION",
    "COMPANY": "ORGANISATION",
    # LOCATION_AREA / LOCATION_STREET — kept apart, unlike `en-deid`, because this corpus
    # actually distinguishes them.
    "LOCATION": "LOCATION_AREA",
    "STREET_ADDRESS": "LOCATION_STREET",
    # CONTACT — telephone and fax share one tag in the source vocabulary, which is why the
    # distinction MEDDOCAN makes is not available here; `subtype` carries what there is.
    "TELEPHONE_FAX": "CONTACT",
    "PAGER_NUMBER": "CONTACT",
    "E-MAIL_ADDRESS": "CONTACT",
    # AGE — the release marks only ages over 89, as `en-deid` does, and for the same
    # HIPAA §164.514(b)(2)(i)(C) reason. 3 spans in scope.
    "AGE_OVER": "AGE",
    # ID — wards have their own tag, so a "unit number" is a record number and `ID` is the
    # only canonical target for one. 1 span in scope, and the human reference types that
    # span `Date`: the row is in the map for exhaustiveness and carries no claim (§9.0).
    "UNIT_NUMBER": "ID",
    # No tag maps to PROFESSION or OTHER. Silence rather than zero, as on `en-deid`: the
    # tool has no category for an occupation, so one in the text is scored as a false
    # positive and this corpus's aggregate precision is a lower bound (§7.2, §9.0).
}

#: One tag, and it uses §9.1's *mechanism* without joining §9.1's three-name *list* — the
#: list is the cross-corpus concept vocabulary an Auditor is shown, and this is a provenance
#: tag no detector can emit (DESIGN §9.1's 2026-09-22 note). 139 of its 142 spans are
#: dropped by the gold-support filter before they reach here; the surviving 3 are spans the
#: producing project restored as not-PHI where the human reference says a date is present,
#: so they load flagged and `n_spans_excluded` reads 3 rather than 0.
EXCLUDED_TYPES: frozenset[str] = frozenset({"NOT_PHI_RESTORED"})


class KosurroLoader(CorpusLoader):
    corpus_id = "ko-surro"
    type_map = TYPE_MAP
    excluded_types = EXCLUDED_TYPES
    #: Empty for `en-deid`'s reason: there is no directory per fold because there is no file
    #: per document. `fold_roots()` raises if anything calls it and `sealed_reachable()`
    #: answers reachability in `source_roots()`.
    fold_dirs: dict[str, str] = {}
    #: Declared, never inferred. The patient is the source release's own record-header
    #: field, carried through the derived root by `tools/prepare_kosurro.py` — and it is the
    #: field `en-deid` groups on, which is what makes the two corpora's folds the same folds.
    has_patient_key = True
    patient_key_source = (
        f"the source release's own patient field, copied into each {CORPUS_FILE} record by "
        "tools/prepare_kosurro.py and carried in Document.meta — the same field "
        "splits/en-deid.json groups on, which is what option B's alignment means "
        "(DESIGN §6.5)"
    )

    # -- layout --

    def source_roots(self) -> list[Path]:
        """The roots this read may open, unsealed first. `en-deid`'s `source_roots`."""
        roots = [self.root]
        permitted = self.sealed_reachable()
        if permitted is not None:
            roots.append(permitted)
        return roots

    def _files(self, root: Path) -> tuple[Path, Path]:
        """The two files under one root, both required, named relative to the root."""
        paths = [root / name for name in FILES]
        missing = [name for name, path in zip(FILES, paths) if not path.is_file()]
        if missing:
            raise CorpusError(
                f"{self.corpus_id}: {missing} not found under "
                f"{'the sealed' if root != self.root else 'the corpus'} root. The corpus "
                "root is a directory tools/prepare_kosurro.py builds from the Korean "
                "derived files and the source release; check "
                "config/data_paths.local.yaml."
            )
        return paths[0], paths[1]

    def source_files(self, doc_id: str) -> list[Path]:
        """Refused, for `en-deid`'s reason: a document is a record inside a file."""
        raise CorpusError(
            f"{self.corpus_id}: a document is a record inside {CORPUS_FILE}, not a file, "
            "so there are no per-document files to name. Use digest_parts(), which hashes "
            "the record's own bytes."
        )

    def digest_parts(self, doc: Document) -> list[tuple[str, bytes]]:
        """This record's named byte parts, for the split file's per-document digest.

        Four parts: the text, the loaded spans' offsets and subtypes, and the *count* of
        spans the filter denied. The fourth is there because the denied spans are part of
        what `prepare_kosurro.py` wrote and a digest over the loaded spans alone would not
        notice the filter moving — and it is a count rather than their offsets because no
        measurement depends on where a denied span was, so recording more would be
        precision this corpus does not use.
        """
        denied = doc.meta.get("n_spans_not_gold_supported", 0)
        return [
            (f"{CORPUS_FILE}:text", doc.text.encode("utf-8")),
            (
                f"{CORPUS_FILE}:offsets",
                "\n".join(f"{s.start} {s.end}" for s in doc.spans).encode("utf-8"),
            ),
            (
                f"{CORPUS_FILE}:types",
                "\n".join(s.subtype for s in doc.spans).encode("utf-8"),
            ),
            (f"{CORPUS_FILE}:denied", str(denied).encode("ascii")),
        ]

    def patient_key(self, doc: Document) -> str:
        """The patient this note belongs to. DESIGN §9.5's first branch.

        Read from `meta`, never re-split out of `doc_id`: the composition
        `{patient}_{note}` is the release's and `tools/derive_aligned_split.py` composes it
        in one direction only, so a function here that took it apart again would be a
        second answer to which half is the patient.
        """
        key = doc.meta.get("patient_id")
        if not isinstance(key, str) or not key:
            raise CorpusError(
                f"{self.corpus_id}/{doc.doc_id}: no patient_id in meta. The loader fills "
                "it from the record's own patient field; a document without one did not "
                "come from this loader."
            )
        return key

    # -- reading --

    def _read(self) -> Iterator[Document]:
        #: Records with no reference on the English side, across every root this read
        #: opened. The same nine `en-deid` drops, and `src/split.py` cross-checks that.
        self.uncovered: list[str] = []
        #: Silver spans the human reference does not support, dropped and counted.
        self.not_gold_supported = 0
        from_sealed = 0
        sealed = self.sealed_reachable()
        for root in self.source_roots():
            corpus_path, reference_path = self._files(root)
            where = "the sealed" if root == sealed else "the corpus"
            records = self._read_records(corpus_path, where)
            if not records:
                raise CorpusError(
                    f"{self.corpus_id}: {CORPUS_FILE} under {where} root holds no records"
                )
            #: Per root, because `reference.json` is written per root with that root's own
            #: counts. `self.uncovered` is the corpus-wide list and is deliberately not the
            #: same number: a sealed read checks the sealed sidecar *after* the corpus root
            #: has already appended to that list, so comparing against its length would
            #: refuse the read for having found the records it was asked to find.
            counted = {
                "records": 0,
                "silver": 0,
                "supported": 0,
                "denied": 0,
                "without_reference": 0,
            }
            for doc in self._documents(records, counted):
                if root == sealed:
                    from_sealed += 1
                yield doc
            self._check_reference(reference_path, where, counted)
        if sealed is not None and from_sealed == 0:
            # `en-deid`'s invariant for its reason exactly: the access is already in
            # results/sealed_eval_log.md, so a read that returned only the unsealed
            # records would produce numbers from the wrong data under a log row saying the
            # test fold was evaluated.
            raise SealError(
                f"{self.corpus_id}: a sealed read was authorised but the sealed root "
                "holds no records, so the fold was not read at all. The log has already "
                "recorded this access; note in results/sealed_eval_log.md that the run "
                "did not complete, and fix the seal before running again."
            )

    def _read_records(self, path: Path, where: str) -> list[dict]:
        """`ko-surro.jsonl` -> one dict per line, in file order.

        A line that is not an object, or an object whose keys are not exactly
        `RECORD_FIELDS`, raises. The strictness is the point: two of the source
        derivation's keys carry corpus text and must not travel into this repository's
        reach, so an unexpected key is refused rather than ignored.
        """
        records: list[dict] = []
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                # Only the position, never the fragment json would quote.
                raise CorpusError(
                    f"{self.corpus_id}: {CORPUS_FILE} line {lineno} under {where} root is "
                    f"not valid JSON (at column {exc.colno})"
                ) from None
            if not isinstance(record, dict):
                raise CorpusError(
                    f"{self.corpus_id}: {CORPUS_FILE} line {lineno} is a "
                    f"{type(record).__name__}, and every line is one record object"
                )
            self._check_fields(record, RECORD_FIELDS, f"{CORPUS_FILE} line {lineno}")
            records.append(record)
        return records

    def _check_fields(self, obj: dict, allowed: frozenset[str], at: str) -> None:
        """Exactly `allowed`, or refuse — naming keys, never values.

        The two text-bearing keys get their own message because they mean something
        specific: the object came from the source derivation rather than from
        `tools/prepare_kosurro.py`, and the whole point of the derived root is that the
        placeholder literal and the surrogate value do not enter it (§10.7 of the
        provenance note).
        """
        present = set(obj)
        carried = [name for name in TEXT_BEARING_FIELDS if name in present]
        if carried:
            raise CorpusError(
                f"{self.corpus_id}: {at} carries {carried}, which is corpus text: a "
                "placeholder literal is a value 30.5% of the time and a surrogate always "
                "is. The derived root is written without them "
                "(tools/prepare_kosurro.py); this object came from the source derivation "
                "and must not be loaded."
            )
        unknown = sorted(present - allowed)
        missing = sorted(allowed - present)
        if unknown or missing:
            raise CorpusError(
                f"{self.corpus_id}: {at} has unexpected key(s) {unknown} and is missing "
                f"{missing}. The derived root's schema is closed — a field this loader "
                "does not know is a field nothing checks."
            )

    def _documents(self, records: list[dict], counted: dict) -> Iterator[Document]:
        """Records -> documents, applying the gold-support filter."""
        for index, record in enumerate(records):
            doc_id = record["uid"]
            if not isinstance(doc_id, str) or not doc_id:
                raise CorpusError(
                    f"{self.corpus_id}: {CORPUS_FILE} record {index} has no uid"
                )
            counted["records"] += 1
            for field in ("patient", "note"):
                value = record[field]
                if not isinstance(value, str) or not value:
                    # Both are checked here and not only where they are used, because
                    # `src/split.py`'s derived route composes `{patient}_{note}` and
                    # compares it against `splits/en-deid.json`'s document ids: a non-string
                    # note index composes a key that matches nothing, and the derivation
                    # would report the record as `source_note_unknown` — a refusal reading
                    # as though the source split had lost a note.
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: {field} is a "
                        f"{type(value).__name__}, and both halves of the source note's "
                        "key are strings the release wrote"
                    )
            spans_raw = record["spans"]
            if not isinstance(spans_raw, list):
                raise CorpusError(
                    f"{self.corpus_id}/{doc_id}: spans is a "
                    f"{type(spans_raw).__name__}, expected a list"
                )
            counted["silver"] += len(spans_raw)
            if record["has_reference"] is not True:
                # No reference on the English side: coverage is absent, not empty — the
                # same nine records `en-deid` drops (DESIGN §9.0). A document loaded with
                # an empty gold list would make every prediction in it a false positive on
                # no evidence, and here the filter would additionally make a record whose
                # silver spans were all denied look identical to one the reference calls
                # PHI-free.
                if spans_raw:
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: has_reference is false and the "
                        f"record still carries {len(spans_raw)} span(s). A record outside "
                        "the reference cannot have gold-support verdicts; the derived root "
                        "is inconsistent."
                    )
                self.uncovered.append(doc_id)
                counted["without_reference"] += 1
                continue
            spans: list[Span] = []
            #: This record's denied spans, separate from the corpus total in `counted`. The
            #: digest of a record is about that record, so a running total here would make
            #: every document's digest depend on every earlier document's spans — and file
            #: order would become part of the manifest without anything saying so.
            denied_here = 0
            for position, raw in enumerate(spans_raw):
                if not isinstance(raw, dict):
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: span {position} is a "
                        f"{type(raw).__name__}, expected an object"
                    )
                self._check_fields(raw, SPAN_FIELDS, f"{doc_id} span {position}")
                source_type = raw["type"]
                if not isinstance(source_type, str) or not source_type:
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: span {position} has no type"
                    )
                # Classified before the filter, so the type map is checked against every
                # span the corpus carries. Three tags occur only among the denied spans
                # (DESIGN §9.0) and would otherwise never be checked at all.
                phi_type, excluded = self.classify(source_type)
                supported = raw["gold_supported"]
                if not isinstance(supported, bool):
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: span {position} has a "
                        f"{type(supported).__name__} gold_supported, expected a boolean. "
                        "The verdict decides whether the span is gold at all (DESIGN "
                        "§6.5 (v)); a truthy value is not a verdict."
                    )
                if not supported:
                    # The reference does not support this span: not loaded, and counted.
                    # Neither §9.1's exclusion nor en-deid's uncovered record — see the
                    # module docstring and DESIGN §9.0.
                    self.not_gold_supported += 1
                    counted["denied"] += 1
                    denied_here += 1
                    continue
                counted["supported"] += 1
                start, end = raw["start"], raw["end"]
                if not isinstance(start, int) or not isinstance(end, int):
                    raise CorpusError(
                        f"{self.corpus_id}/{doc_id}: span {position} has non-integer "
                        "offsets"
                    )
                spans.append(
                    Span(
                        start=start,
                        end=end,
                        # The surrogate string as `prepare_kosurro.py` read it from the
                        # Korean derived file, so `assert_offsets()` compares it against
                        # the slice rather than against itself. Measured on the source:
                        # 2,158 of 2,158 agree.
                        surface=raw["surface"],
                        subtype=source_type,
                        phi_type=phi_type,
                        excluded=excluded,
                    )
                )
            yield Document(
                doc_id=doc_id,
                corpus_id=self.corpus_id,
                text=record["text"],
                spans=spans,
                # Nothing in the layout encodes a fold; splits/ko-surro.json is the only
                # authority and `_apply_split_file()` fills this in.
                split=None,
                had_bom=False,
                meta={
                    "patient_id": record["patient"],
                    "note_index": record["note"],
                    "n_spans_not_gold_supported": denied_here,
                },
            )

    def _check_reference(self, path: Path, where: str, counted: dict) -> None:
        """`reference.json` must describe the root that was just read.

        Two things are checked and they fail for different reasons. The **basis** is the
        §6.5 (v) decision: a root built against raw silver, or against the human reference,
        is a different experiment and is refused rather than loaded. The **counts** are
        facts about the file — how many records it holds, how many silver spans, and how the
        filter split them — recounted here rather than trusted, because the split file's
        denominators are built on them and a derived root whose sidecar drifted from its
        data would move a leak-rate denominator with nothing saying so.

        What is deliberately *not* checked is the in-scope total: that depends on this
        loader's own type map and exclusions, and asserting it against a number
        `prepare_kosurro.py` wrote would make the sidecar a second copy of §9.0's policy.
        """
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CorpusError(
                f"{self.corpus_id}: {REFERENCE_FILE} under {where} root is not valid "
                f"JSON (line {exc.lineno}, column {exc.colno})"
            ) from None
        basis = record.get("reference")
        if basis != REFERENCE_BASIS:
            raise CorpusError(
                f"{self.corpus_id}: {REFERENCE_FILE} under {where} root declares "
                f"reference {basis!r}, and this corpus's reference is "
                f"{REFERENCE_BASIS!r} (DESIGN §6.5 (v), pre-registered 2026-09-22). A "
                "root built on another span set is another experiment."
            )
        counts = record.get("counts")
        if not isinstance(counts, dict):
            raise CorpusError(
                f"{self.corpus_id}: {REFERENCE_FILE} under {where} root has no counts "
                "block, so nothing the loader read can be checked against it"
            )
        expected = {
            "records": counted["records"],
            "records_without_reference": counted["without_reference"],
            "silver_spans": counted["silver"],
            "gold_supported": counted["supported"],
            "gold_unsupported": counted["denied"],
        }
        disagree = sorted(
            name for name, value in expected.items() if counts.get(name) != value
        )
        if disagree:
            raise CorpusError(
                f"{self.corpus_id}: {REFERENCE_FILE} under {where} root disagrees with "
                f"{CORPUS_FILE} on {disagree}. The sidecar records what the filter did "
                "and the loader recounts it; a disagreement means the two were written at "
                "different times, and the split file's denominators come from the file "
                "that was read."
            )
