"""GraSCCo loader.

German synthetic clinical narratives, 63 documents, 1,436 PHI spans of 20 types.
The corpus ships no split, so one is constructed per DESIGN §9.5 and frozen before
any German rule is written (§6.2).

Two encodings of the same annotations ship — UIMA CAS JSON and CAS XMI, both
exported from INCEpTION. **The JSON is read.** They are redundant, so reading both
would mean choosing which to trust when they disagree; reading one means a
disagreement cannot arise. That is MEDDOCAN's rule (`meddocan.py`) applied to a
different pair of encodings.

Four things here are corpus-specific, and the first three are the reason this file
is not a copy of the brat loader:

  - **The annotation file carries the text.** A CAS holds its subject of analysis
    inline as `sofaString`, and the offsets index *that*. A plain `.txt` also
    ships, and the two are identical in 63 of 63 documents (measured; re-asserted
    on every load below). The `.txt` is what the document text is taken from,
    because it is what a detector is pointed at, and the equality assertion is what
    licenses using CAS offsets against it.
  - **The annotations carry no surface string.** brat records the surface beside
    the offsets, so MEDDOCAN's loader can compare the two and catch an offset
    error. A CAS records offsets only, so slicing them out and calling the result
    the surface is unfalsifiable. Two independent checks replace that one:
    `sofaString` against the `.txt` (above), and the measured invariant that no
    gold span begins or ends on whitespace — 0 of 1,436 do, and a one-character
    offset slip is the error most likely to break it.
  - **Five documents carry a BOM, and in two of them the first gold span starts at
    index 0**, so the annotated surface begins with U+FEFF. §9.7 strips and shifts;
    that arithmetic alone would put those two spans at −1. The decision is written
    out in `_span` and it is a decision, not a fallback.
  - `NAME_TITLE` is out of scope per DESIGN §9.1 but is loaded and flagged, because
    its volume (139 spans, 9.68% of gold) is a reported limitation.

The layout carries no fold: all 63 documents live in one directory, so there is no
`fold_dirs` here and the frozen split file is the only thing that assigns a fold.
The seal is therefore a second *root* rather than a subdirectory the loader declines
to open, and `sealed_reachable()` is the permission for it — the same gate
`fold_roots()` uses for MEDDOCAN.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .base import CorpusError, CorpusLoader, Document, SealError, Span

#: GraSCCo `kind` -> canonical type. The human-authored mapping of DESIGN §9.0;
#: `mappings/de-grascco.yaml` is where a Mapper agent's version would go. Kept as a
#: literal here so that a mapping change is a reviewable diff.
TYPE_MAP: dict[str, str] = {
    # NAME — role (patient vs clinician vs relative) stays in subtype, per §9.0
    "NAME_PATIENT": "NAME",
    "NAME_DOCTOR": "NAME",
    "NAME_RELATIVE": "NAME",
    "NAME_USERNAME": "NAME",
    "NAME_EXT": "NAME",
    # DATE — birth dates are dates; that they are birth dates stays in subtype
    "DATE": "DATE",
    "DATE_BIRTH": "DATE",
    # AGE
    "AGE": "AGE",
    # LOCATION_AREA — GraSCCo separates city from postcode where MEDDOCAN's
    # TERRITORIO mixes them; merged to the coarse type rather than splitting
    # MEDDOCAN's gold by a heuristic (DESIGN §9.2)
    "LOCATION_CITY": "LOCATION_AREA",
    "LOCATION_ZIP": "LOCATION_AREA",
    "LOCATION_COUNTRY": "LOCATION_AREA",
    # LOCATION_STREET
    "LOCATION_STREET": "LOCATION_STREET",
    # ORGANISATION — the corpus files hospitals under LOCATION_*, which is its
    # schema's choice and not ours; a hospital is an organisation here
    "LOCATION_HOSPITAL": "ORGANISATION",
    "LOCATION_ORGANIZATION": "ORGANISATION",
    # CONTACT
    "CONTACT_EMAIL": "CONTACT",
    "CONTACT_PHONE": "CONTACT",
    "CONTACT_FAX": "CONTACT",
    # ID — one subtype here where MEDDOCAN has five; forcing agreement would
    # measure the annotation schema rather than the detector (DESIGN §9.0)
    "ID": "ID",
    # PROFESSION
    "PROFESSION": "PROFESSION",
    # No GraSCCo type maps to OTHER: the corpus ships no residual bucket. That is
    # a difference in the annotation schemas, and §9.4 keeps OTHER in the
    # leak-rate denominator for MEDDOCAN only because only MEDDOCAN has it.
}

#: Kept and flagged, not dropped (DESIGN §9.1). GraSCCo makes `Dr.` its own span
#: and MEDDOCAN excludes titles from the name span entirely, so scoring titles
#: would require a detector ported to German to emit spans Spanish gold counts as
#: false positives. The excluded volume is reported as a limitation, which is why
#: the spans have to survive loading.
EXCLUDED_TYPES = frozenset({"NAME_TITLE"})

#: Where the two encodings and the plain text live, relative to a corpus root. The
#: XMI directory is `grascco_pii_2_xmi` and is deliberately not read.
ANNOTATION_DIR = Path("annotations") / "grascco_pii_2_json"
TEXT_DIR = Path("text")

#: `{doc_id}.txtphi-pii_2.0.json`. The `.txt` inside the suffix is the annotated
#: file's name, not an extension of this one, which is why `Path.stem` cannot be
#: used to recover the document id.
ANNOTATION_SUFFIX = ".txtphi-pii_2.0.json"

#: The CAS types this loader reads. `webanno.custom.PHI` is INCEpTION's custom
#: layer; its label lives in the `kind` feature. Token and Sentence annotations
#: also ship and are not read — this loader does not tokenise, and a tokenisation
#: that disagreed with the tagger's would be a third answer to a question §5's
#: `tokenizer` field already answers once.
PHI_TYPE = "webanno.custom.PHI"
PHI_LABEL_FEATURE = "kind"
SOFA_TYPE = "uima.cas.Sofa"


class GrasccoLoader(CorpusLoader):
    corpus_id = "de-grascco"
    type_map = TYPE_MAP
    excluded_types = EXCLUDED_TYPES
    #: Empty, and this is the corpus fact rather than an omission: nothing in the
    #: layout encodes a fold. `fold_roots()` raises if anything calls it, and
    #: reachability is answered by `sealed_reachable()` in `source_roots()`.
    fold_dirs: dict[str, str] = {}

    # -- layout --

    def source_roots(self) -> list[Path]:
        """The roots this read may open, unsealed first.

        The flat-layout counterpart of `fold_roots()`, and it takes its permission
        from the same place. When no fold is sealed this is the corpus root alone
        and the test fold is read from it like any other document; once the seal
        exists, the sealed root appears here only for a read `_authorise_sealed()`
        has already logged.
        """
        roots = [self.root]
        permitted = self.sealed_reachable()
        if permitted is not None:
            roots.append(permitted)
        return roots

    def _annotation_files(self, root: Path) -> list[Path]:
        directory = root / ANNOTATION_DIR
        if not directory.is_dir():
            # The directory name relative to the root, never the absolute path: for
            # a sealed or out-of-tree corpus the path is a data location and does
            # not belong in a message that travels into logs (CLAUDE.md).
            raise CorpusError(
                f"{self.corpus_id}: no {ANNOTATION_DIR.as_posix()} directory under "
                "the root. Check config/data_paths.local.yaml."
            )
        return sorted(directory.glob(f"*{ANNOTATION_SUFFIX}"))

    def source_files(self, doc_id: str) -> list[Path]:
        """The files one document is made of, for hashing into the split file.

        Searched across the roots this read may open rather than taking a fold
        argument, so the caller does not have to already know the answer the split
        file records. Once the test fold is sealed this cannot reach it — which is
        why the hashes have to be recorded before the seal, not after (§6.2).
        """
        found: list[Path] = []
        for root in self.source_roots():
            annotation = root / ANNOTATION_DIR / f"{doc_id}{ANNOTATION_SUFFIX}"
            text = root / TEXT_DIR / f"{doc_id}.txt"
            if annotation.exists():
                if not text.exists():
                    raise CorpusError(
                        f"{self.corpus_id}: {doc_id!r} has an annotation file and no "
                        f"{TEXT_DIR.as_posix()}/{doc_id}.txt beside it"
                    )
                found.extend([annotation, text])
        if not found:
            raise CorpusError(f"{self.corpus_id}: no files for doc_id {doc_id!r}")
        if len(found) > 2:
            raise CorpusError(
                f"{self.corpus_id}: doc_id {doc_id!r} exists under more than one root"
            )
        return found

    # -- reading --

    def _read(self) -> Iterator[Document]:
        from_sealed = 0
        sealed = self.sealed_reachable()
        for root in self.source_roots():
            annotation_files = self._annotation_files(root)
            if not annotation_files:
                raise CorpusError(
                    f"{self.corpus_id}: {ANNOTATION_DIR.as_posix()} under "
                    f"{'the sealed' if root == sealed else 'the corpus'} root holds "
                    f"no {ANNOTATION_SUFFIX} files"
                )
            for annotation_path in annotation_files:
                if root == sealed:
                    from_sealed += 1
                yield self._read_document(annotation_path, root)
        if sealed is not None and from_sealed == 0:
            # The same invariant `fold_roots()` enforces for a fold-directory
            # corpus, and for the same reason: the access is already in
            # results/sealed_eval_log.md, so a read that returned only the unsealed
            # documents would produce numbers from the wrong data under a log row
            # that says the test fold was evaluated.
            raise SealError(
                f"{self.corpus_id}: a sealed read was authorised but the sealed root "
                "holds no documents, so the fold was not read at all. The log has "
                "already recorded this access; note in results/sealed_eval_log.md "
                "that the run did not complete, and fix the seal before running "
                "again."
            )

    def _read_document(self, annotation_path: Path, root: Path) -> Document:
        doc_id = annotation_path.name[: -len(ANNOTATION_SUFFIX)]
        sofa_string, annotations = self._read_cas(annotation_path, doc_id)

        text_path = root / TEXT_DIR / f"{doc_id}.txt"
        if not text_path.exists():
            raise CorpusError(
                f"{self.corpus_id}: {doc_id!r} has no "
                f"{TEXT_DIR.as_posix()}/{doc_id}.txt"
            )
        # Plain utf-8, deliberately not utf-8-sig: the CAS offsets count the BOM as
        # a character, so decoding it away without shifting the offsets moves every
        # span in the five BOM documents by one (DESIGN §9.7).
        raw = text_path.read_text(encoding="utf-8")
        if raw != sofa_string:
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: the CAS sofaString and the plain text "
                f"file differ — sofaString is {len(sofa_string)} characters, the "
                f"file is {len(raw)}. The offsets index the sofaString, so they "
                "cannot be applied to this file until it is known which one moved. "
                "No text is quoted here (CLAUDE.md)."
            )
        text, shift = self.strip_bom(raw)

        spans = []
        clipped = []
        for index, annotation in enumerate(annotations):
            span, was_clipped = self._span(annotation, index, text, shift, doc_id)
            spans.append(span)
            if was_clipped:
                clipped.append(index)

        return Document(
            doc_id=doc_id,
            corpus_id=self.corpus_id,
            text=text,
            spans=spans,
            # No fold: nothing in the layout encodes one, so the frozen split file
            # is the only authority and `_apply_split_file()` fills this in.
            split=None,
            had_bom=shift > 0,
            meta={"bom_clipped_spans": clipped} if clipped else {},
        )

    def _read_cas(self, path: Path, doc_id: str) -> tuple[str, list[dict]]:
        """The sofa string and the PHI annotations, from one CAS JSON file.

        Parsed strictly. A release that attached PHI to a second sofa, or shipped a
        `kind` this loader has no mapping for, needs a decision before it can be
        scored, so it fails here rather than being reshaped into something
        loadable.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                cas = json.load(handle)
        except json.JSONDecodeError as exc:
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: the annotation file is not JSON "
                f"(line {exc.lineno}, column {exc.colno})"
            ) from exc

        structures = cas.get("%FEATURE_STRUCTURES")
        if not isinstance(structures, list):
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: the annotation file has no "
                "'%FEATURE_STRUCTURES' list, so it is not the CAS JSON export this "
                "loader reads"
            )

        sofas = [fs for fs in structures if fs.get("%TYPE") == SOFA_TYPE]
        if len(sofas) != 1:
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: {len(sofas)} sofas in the CAS, expected "
                "1. With more than one, which text the offsets index becomes a "
                "decision and this loader does not have a rule for it."
            )
        sofa = sofas[0]
        sofa_string = sofa.get("sofaString")
        if not isinstance(sofa_string, str):
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: the sofa carries no 'sofaString', so "
                "the CAS does not contain its own text"
            )

        annotations = []
        for fs in structures:
            if fs.get("%TYPE") != PHI_TYPE:
                continue
            if int(fs["@sofa"]) != int(sofa["%ID"]):
                raise CorpusError(
                    f"{self.corpus_id}/{doc_id}: a {PHI_TYPE} annotation points at "
                    f"sofa {fs['@sofa']} and the only sofa is {sofa['%ID']}"
                )
            missing = sorted(
                {"begin", "end", PHI_LABEL_FEATURE} - set(fs)
            )
            if missing:
                raise CorpusError(
                    f"{self.corpus_id}/{doc_id}: a {PHI_TYPE} annotation is missing "
                    f"{missing}. CAS JSON omits a feature at its default value, so "
                    "an absent 'begin' would silently read as 0; this release writes "
                    "all three on all 1,436 annotations and a release that stops "
                    "doing so needs a decision, not a default."
                )
            annotations.append(fs)

        # Sorted by position, then by the corpus's own id, so the span order in a
        # loaded document does not depend on the order INCEpTION happened to
        # serialise: `assert_offsets` and the split file both report span indices.
        annotations.sort(key=lambda fs: (fs["begin"], fs["end"], fs["%ID"]))
        return sofa_string, annotations

    def _span(
        self, annotation: dict, index: int, text: str, shift: int, doc_id: str
    ) -> tuple[Span, bool]:
        """One PHI annotation as a `Span`, with the BOM decision written out.

        Returns the span and whether it was clipped at the BOM.

        **The BOM decision (DESIGN §9.7).** The shipped offsets count the BOM, so
        stripping it means subtracting its length from every offset — arithmetic and
        uniform, so that it cannot silently repair a genuinely wrong offset. In
        `Baastrup` and `Dupuytren` the first gold span begins at 0, so that
        subtraction alone would put it at −1: the annotated extent *includes* the
        byte-order mark. Those spans are clipped to 0 and keep their shifted end, so
        the span loses exactly the BOM and nothing else. The alternative — keeping
        the start at 0 and the end unshifted — was rejected: it preserves the
        length by pulling one further character of text into the span, which makes
        the surface something no human would call the identifier and breaks §9.7's
        stated reason for stripping rather than retaining. The clip is recorded per
        document in `meta['bom_clipped_spans']` so that the two cases stay
        countable instead of becoming a property of the loader nobody can see.
        """
        begin, end = annotation["begin"], annotation["end"]
        clipped = False
        start = begin - shift
        if start < 0:
            if shift == 0 or begin != 0:
                raise CorpusError(
                    f"{self.corpus_id}/{doc_id}: span {index} starts at {begin} with "
                    f"a BOM shift of {shift}, which is neither a span inside the BOM "
                    "nor a span after it. The offsets and the BOM disagree in a way "
                    "§9.7 has no rule for."
                )
            start = 0
            clipped = True
        end -= shift
        if end > len(text):
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: span {index} ends at {end} after the "
                f"BOM shift but the document is {len(text)} characters"
            )

        surface = text[start:end]
        if surface != surface.strip():
            # The check that replaces MEDDOCAN's surface comparison. A CAS carries
            # no surface string, so slicing the offsets and calling the result the
            # surface proves nothing; this invariant is independent of the slice and
            # holds on 1,436 of 1,436 spans as shipped. A one-character offset slip
            # is the error it is most likely to catch. Lengths and offsets only.
            raise CorpusError(
                f"{self.corpus_id}/{doc_id}: span {index} at [{start}, {end}) begins "
                "or ends on whitespace. No gold span in this release does (measured, "
                "1,436 of 1,436), so this is an offset error rather than an "
                "annotation style. No text is quoted here (CLAUDE.md)."
            )

        phi_type, excluded = self.classify(annotation[PHI_LABEL_FEATURE])
        return (
            Span(
                start=start,
                end=end,
                surface=surface,
                subtype=annotation[PHI_LABEL_FEATURE],
                phi_type=phi_type,
                excluded=excluded,
            ),
            clipped,
        )
