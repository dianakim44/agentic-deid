"""CARMEN-I loader.

Authentic Hospital Clínic de Barcelona records, 2,000 documents, 8,231 PHI spans of
18 observed source types (28 declared), brat standoff. The fifth and last corpus. It
ships no split, so one is constructed per DESIGN §9.5 and frozen before any Spanish
or Catalan rule is written for it (§6.2).

**No span surface appears in any message this module can raise.** That rule is
CLAUDE.md's and applies to every corpus, but this is the corpus it was written for:
the text is real clinical narrative under a PhysioNet Contributor Review licence, and
an exception message travels into terminals, CI logs and issue trackers where
`tools/release_screen.py` cannot reach. Two consequences are visible below and are
deliberate rather than accidental:

  - the brat parser reports **field and part counts**, never the field contents. Its
    MEDDOCAN sibling prints the offset field (`{middle!r}`) on a malformed line,
    which is safe there because MEDDOCAN is synthetic. A malformed line's second
    field can hold anything, so here it is described and not shown.
  - a directory that is missing is named **relative to the corpus root**, never as an
    absolute path. A data location is not a surface form, but it is the other thing
    that must not end up in a public log.

Four things are corpus-specific, and the first three are why this is not a copy of
the MEDDOCAN brat loader:

  - **Two anonymisation variants ship, `replaced` and `masked`, and only `replaced`
    is read.** They are two renderings of the same annotations over the same
    documents, so reading both would mean deciding which to trust where they
    disagree — and they do disagree in one document (§8.5: 461 documents carry no
    annotation in `replaced` against 462 in `masked`). Reading one means the
    disagreement cannot arise, which is the rule `meddocan.py` applies to its two
    encodings. `replaced` rather than `masked` because `masked` substitutes
    placeholders for PHI: a detector pointed at it would be scored on finding the
    masking convention, not on finding PHI. Every count in DESIGN §9.0's `es-carmen`
    block is over `replaced`, so this choice is also what makes those counts the
    ones this loader reproduces.
  - **Two annotation layers ship per variant, and only the PHI layer is read.**
    `ann/{variant}/anon/` holds the 8,231 PHI spans over all 2,000 documents;
    `ann/{variant}/ner/` holds a medical-concept layer (symptom, procedure, disease,
    drug, species, human) over 500 of them. Those seven types are declared in the
    same `annotation.conf` as the 28 PHI types with nothing in the file separating
    them, so the layer boundary is observable only from the two directories — which
    is exactly why `CONCEPT_TYPES` is written out here and checked, rather than left
    for `classify()` to trip over if a concept span ever appears in the PHI layer.
  - **The document's language label is corpus data and lives in a shared file.**
    `CARMEN1_mappings.tsv` carries one row per document: `es` 1,697, `bi` (Spanish
    and Catalan mixed *within* one document) 264, `cat` 39. The frozen split
    stratifies on it (§9.5), so it is read per root, checked against the file list,
    and hashed into the per-document digest — a label the split's composition rests
    on must not be able to change without the digest moving.
  - **There is no patient key.** Not an omission in this loader: §8.5 tested the two
    candidate groupings the filenames suggest and both failed the §9.5 rule, so the
    largest defensible unit is the document. `has_patient_key` is False and
    `patient_key()` raises, which is the behaviour that keeps a document-random split
    from being announced as patient-disjoint.

The layout carries no fold — all 2,000 documents live in one directory — so there is
no `fold_dirs` here and `splits/es-carmen.json` is the only thing that assigns a
fold. The seal is therefore a second *root*, `sealed_reachable()` is the permission
for it, and an authorised sealed read that reaches nothing raises `SealError`. That
guard ships with this loader rather than being owed by it: `kosurro.py` closed the
same debt on 2026-09-22 and `base.fold_roots()`, `grascco.py` and `endeid.py` still
carry it (`docs/notes/sealed-eval-preflight.md`).
"""
from __future__ import annotations

import csv
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from .base import CorpusError, CorpusLoader, Document, SealError, Span

#: The release directory inside the corpus root, and the layout under it. Relative
#: paths throughout: these names go into error messages and absolute paths do not.
RELEASE = Path("CARMEN-I")

#: Which anonymisation variant is read. Module-level and named rather than inlined so
#: that "which variant produced this number" has one answer that a reader can find.
VARIANT = "replaced"

#: The PHI layer, and the medical-concept layer that is deliberately not read.
PHI_LAYER = "anon"
CONCEPT_LAYER = "ner"

#: `ann/{variant}/{layer}/` holds `{doc_id}.ann` and `{doc_id}.txt` side by side, and
#: the offsets index the `.txt` beside the `.ann`. A second copy of the same 2,000
#: files ships under `txt/{variant}/` and is **not** read: measured 2026-09-28, the
#: two copies are byte-identical in 2,000 of 2,000 documents, so reading the copy
#: brat itself pairs with the annotations removes a disagreement that could otherwise
#: arise between the text an offset indexes and the text a detector is pointed at.
ANN_ROOT = RELEASE / "ann"
UNREAD_TEXT_DIR = RELEASE / "txt"

#: One row per document: `filename`, `language`, `ner_annotations`.
MAPPINGS = RELEASE / "CARMEN1_mappings.tsv"
MAPPINGS_FIELDS = ("filename", "language", "ner_annotations")

#: brat's type declaration for the release. Read to check the loader's map against
#: the *schema* rather than against what happens to be annotated — DESIGN §9.0's
#: `es-carmen` block: "a map built from observed data alone would admit an unmapped
#: type silently on a future release that starts using one of the ten".
SCHEMA = ANN_ROOT / "annotation.conf"
SCHEMA_SECTION = "[entities]"

#: CARMEN-I source type -> canonical type (DESIGN §9.0's `es-carmen` block). Built
#: from the schema's 28 PHI types, not from the 18 observed: the ten with zero
#: instances are mapped here and their targets are decided in that block, because the
#: release that starts using one of them is the release nobody is reading a mapping
#: table during. A literal, so that a mapping change is a reviewable diff.
TYPE_MAP: dict[str, str] = {
    # DATE — the corpus's dominant type, 5,386 spans, 72.1% of canonical gold
    "FECHAS": "DATE",
    # AGE
    "EDAD_SUJETO_ASISTENCIA": "AGE",
    # NAME — role stays in subtype (§9.0). `NOMBRE_SUJETO_ASISTENCIA` is the patient
    # type and has **zero** instances: §9.0 reports patient-name recall as undefined
    # here rather than as a number, and the mapping exists so that the guard runs
    # before the filter, not because a span is expected.
    "NOMBRE_PERSONAL_SANITARIO": "NAME",
    "NOMBRE_SUJETO_ASISTENCIA": "NAME",
    # ORGANISATION — the corpus files hospitals and health centres separately from
    # institutions; all three are organisations here
    "HOSPITAL": "ORGANISATION",
    "INSTITUCION": "ORGANISATION",
    "CENTRO_SALUD": "ORGANISATION",
    # ID — `NUMERO_IDENTIF` is 227 of the 243 and names a number with no role, which
    # is the entry §9.0's block argues for at length. The three Safe Harbor
    # serial-number types take the same target and have zero instances.
    "NUMERO_IDENTIF": "ID",
    "ID_SUJETO_ASISTENCIA": "ID",
    "ID_CONTACTO_ASISTENCIAL": "ID",
    "ID_ASEGURAMIENTO": "ID",
    "ID_TITULACION_PERSONAL_SANITARIO": "ID",
    "ID_EMPLEO_PERSONAL_SANITARIO": "ID",
    "IDENTIF_VEHICULOS_NRSERIE_PLACAS": "ID",
    "IDENTIF_DISPOSITIVOS_NRSERIE": "ID",
    "IDENTIF_BIOMETRICOS": "ID",
    # LOCATION_AREA — `TERRITORIO` mixes place names and postcodes (§9.2)
    "TERRITORIO": "LOCATION_AREA",
    "PAIS": "LOCATION_AREA",
    # LOCATION_STREET
    "CALLE": "LOCATION_STREET",
    # CONTACT — phone is the only one observed; email and fax are declared and empty
    "NUMERO_TELEFONO": "CONTACT",
    "CORREO_ELECTRONICO": "CONTACT",
    "NUMERO_FAX": "CONTACT",
    # PROFESSION
    "PROFESION": "PROFESSION",
    # OTHER — MEDDOCAN's residual patient-attribute bucket, and the reason §9.4 keeps
    # OTHER in the leak-rate denominator for the Spanish pair
    "OTROS_SUJETO_ASISTENCIA": "OTHER",
}

#: Kept and flagged, not dropped (DESIGN §9.1). The first two are the same type names
#: MEDDOCAN excludes and cost 9.20% of this corpus's gold. The two internet-address
#: types are the §9.1 decisions of 2026-09-28: `URL_WEB` has one span and
#: `DIREC_PROT_INTERNET` none, and no other corpus in this project has a URL or
#: IP-address category — folding either into `CONTACT` would make one corpus's
#: `CONTACT` row count something the other four's do not, and per-type comparison is
#: one of the two headline quantities.
EXCLUDED_TYPES = frozenset(
    {
        "SEXO_SUJETO_ASISTENCIA",
        "FAMILIARES_SUJETO_ASISTENCIA",
        "URL_WEB",
        "DIREC_PROT_INTERNET",
    }
)

#: The medical-concept layer's types. Declared in the same `annotation.conf` as the
#: PHI types with no section marker between them, so this list is what makes the
#: layer boundary checkable: `_check_schema()` requires the declaration to be exactly
#: `TYPE_MAP | EXCLUDED_TYPES | CONCEPT_TYPES`, and `classify()` refuses a concept
#: type that turns up in the PHI layer rather than mapping it.
CONCEPT_TYPES = frozenset(
    {
        "SINTOMA",
        "PROCEDIMIENTO",
        "ENFERMEDAD",
        "FARMACO",
        "ENTIDAD_OBSERVABLE",
        "SPECIES",
        "HUMANO",
    }
)

#: The corpus's own per-document language labels, not `naming.yaml`'s `lang` axis.
#: **`bi` is not a language code** and must never become one: `rules/{lang}.yaml`
#: needs a file per value and there is no bilingual rule file (§8.4, §5.6, where
#: `corpus_rule_langs: [es, cat]` is the declaration an arm's call count comes from).
#: These three are corpus data on the footing of a `type_inventory` label, which is
#: why they are here and not in `config/naming.yaml`.
LANGUAGE_LABELS = frozenset({"es", "bi", "cat"})

#: The corpus's own document-type tokens, from the first field of the filename —
#: `CARMEN-I_{doctype}[_{section}]_{n}`. Measured: `IR` 1,201, `IA` 617, `IT` 172,
#: `CC` 5, `IE` 5. Declared rather than collected because the frozen split records a
#: composition over these labels (§9.5), and a new token appearing in a later release
#: would change what that recorded composition means while still looking like a
#: stratum. Also **not** the §7 document-type axis: that one is derived from text
#: cues by `doctype.py` and `es-carmen` declares no cues. 789 of these 2,000 units
#: are clinical *sections* rather than whole notes (§8.5), so the two must not be
#: confused — hence `filename_doctype` in `meta` rather than `document_type`.
DOCTYPE_TOKENS = frozenset({"IR", "IA", "IT", "CC", "IE"})

#: The one span in this release whose recorded surface is not the text at its recorded
#: offsets (DESIGN §9.7's `es-carmen` subsection, which holds the three witnesses that
#: say it is the surface field and not the offsets that is wrong). Keyed by document
#: and by the span's index within that document — the index `assert_offsets()` reports
#: — and the value pins the source type and both offsets so that the entry cannot
#: start excusing a different span if the release is reordered. The surface is taken
#: from the text for this span and for nothing else: a mismatch the pin does not name
#: raises, and a pinned span that *matches* raises too, because a release that fixed
#: the defect must not leave a standing permission to overwrite a surface behind.
KNOWN_SURFACE_DEFECTS: dict[tuple[str, int], tuple[str, int, int]] = {
    ("CARMEN-I_IA_EVOL_112", 4): ("FECHAS", 153, 157),
}

#: `CARMEN-I_{doctype}_{section}_{n}` with the section optional. Measured on all
#: 2,000 ids: every one parses, 9 distinct section tokens plus the sectionless `IR`,
#: `CC` and `IE` forms. Parsed rather than split on `_` because the section tokens
#: themselves contain `_` (`EXPLORACION_COMPLEMENTARIA`).
DOC_ID_RE = re.compile(
    r"^CARMEN-I_(?P<doctype>[A-Z]+)(?:_(?P<section>[A-Z_]+))?_(?P<n>\d+)$"
)


class CarmenLoader(CorpusLoader):
    corpus_id = "es-carmen"
    type_map = TYPE_MAP
    excluded_types = EXCLUDED_TYPES
    #: Empty, and this is the corpus fact rather than an omission: nothing in the
    #: layout encodes a fold. `fold_roots()` raises if anything calls it, and
    #: reachability is answered by `sealed_reachable()` in `source_roots()`.
    fold_dirs: dict[str, str] = {}
    #: No patient, encounter or record identifier exists anywhere in the release
    #: (§8.5), so §9.5's first branch does not apply and `patient_key()` must not be
    #: called. `patient_key_source` stays empty for the same reason.
    has_patient_key = False

    # -- layout --

    def source_roots(self) -> list[Path]:
        """The roots this read may open, unsealed first.

        The flat-layout counterpart of `fold_roots()`, taking its permission from the
        same place (`grascco.py` has the same shape and the same reason). When no fold
        is sealed this is the corpus root alone; once the seal exists the sealed root
        appears here only for a read `_authorise_sealed()` has already logged.
        """
        roots = [self.root]
        permitted = self.sealed_reachable()
        if permitted is not None:
            roots.append(permitted)
        return roots

    def _annotation_files(self, root: Path) -> list[Path]:
        """The PHI layer's `.ann` files under one root, empty if there are none.

        Empty rather than raising, and the two callers below are why: "there is nothing
        to read here" means different things for the two roots — a misconfigured path
        for the corpus root and a broken seal for the sealed one — and a missing
        directory and an empty one are the same fact for both. Deciding that here would
        mean this method knowing which root it was handed.
        """
        directory = root / ANN_ROOT / VARIANT / PHI_LAYER
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.ann"))

    def _nothing_to_read(self, root: Path, sealed: Path | None) -> Exception:
        """The exception for a root that holds no annotations. Two cases, two types.

        For the **sealed** root this is a `SealError`, because the access is already in
        `results/sealed_eval_log.md`: the run has spent a row on the test fold and must
        not go on to produce numbers from the unsealed folds under it. That is the
        `kosurro.py` guard of 2026-09-22, which `base.fold_roots()`, `grascco.py` and
        `endeid.py` still owe (`docs/notes/sealed-eval-preflight.md`), written here
        with the loader rather than after it — and reachable, which the `from_sealed`
        backstop in `_read()` is not.

        For the **corpus** root it is a `CorpusError` pointing at the configuration,
        which is what an absent or mistyped path looks like.
        """
        where = (ANN_ROOT / VARIANT / PHI_LAYER).as_posix()
        if sealed is not None and root == sealed:
            return SealError(
                f"{self.corpus_id}: a sealed read was authorised but the sealed root "
                f"has no {where} annotations, so the fold was not read at all. The "
                "log has already recorded this access; note in "
                "results/sealed_eval_log.md that the run did not complete, and fix "
                "the seal before running again."
            )
        return CorpusError(
            f"{self.corpus_id}: no {where} annotations under the corpus root. Check "
            "config/data_paths.local.yaml."
        )

    def source_files(self, doc_id: str) -> list[Path]:
        """The files one document is made of, for hashing into the split file.

        Searched across the roots this read may open rather than taking a fold
        argument, so the caller does not have to already know the answer the split
        file records. Once the test fold is sealed this cannot reach it — which is why
        the hashes are recorded before the seal, not after (§6.2).
        """
        found: list[Path] = []
        for root in self.source_roots():
            ann = root / ANN_ROOT / VARIANT / PHI_LAYER / f"{doc_id}.ann"
            txt = ann.with_suffix(".txt")
            if ann.exists():
                if not txt.exists():
                    raise CorpusError(
                        f"{self.corpus_id}: {doc_id!r} has an .ann file and no .txt "
                        "beside it"
                    )
                found.extend([ann, txt])
        if not found:
            raise CorpusError(f"{self.corpus_id}: no files for doc_id {doc_id!r}")
        if len(found) > 2:
            raise CorpusError(
                f"{self.corpus_id}: doc_id {doc_id!r} exists under more than one root"
            )
        return found

    def digest_parts(self, doc: Document) -> list[tuple[str, bytes]]:
        """The two files, plus the mappings row this document's stratum came from.

        The default hook would hash the `.ann` and `.txt` alone, which is the whole
        document as far as *detection* is concerned. It is not the whole of what the
        frozen split rests on: the language label comes from `CARMEN1_mappings.tsv`
        and §9.5's stratification is over it, so a release that reshuffled that file
        would leave every per-document digest intact while making the split file's
        recorded composition false. The label is hashed as a canonical
        `key=value` rendering rather than as the raw row, because what has to be
        stable is the two values the split reads, not the file's column order.
        """
        parts = [(path.name, path.read_bytes()) for path in self.source_files(doc.doc_id)]
        payload = (
            f"language={doc.meta['language_label']}\n"
            f"concept_layer={doc.meta['has_concept_layer']}\n"
        )
        parts.append((f"{MAPPINGS.name}:{doc.doc_id}", payload.encode("utf-8")))
        return parts

    # -- the release's own declarations --

    def _check_schema(self, root: Path) -> None:
        """The release's declared entity types must be exactly what this loader knows.

        Read per root, like everything else here: a sealed root is a corpus slice with
        its own declarations (DESIGN §6.1) and not a directory of leftovers, so it
        declares its own schema
        and is checked against it. Equality rather than containment in either
        direction, because the two failures need different fixes and both are real:

          - a **declared type this loader does not know** is the case DESIGN §9.0
            names — a later release using one of the ten empty types, or adding a
            type nobody has decided about. `classify()` would catch it too, but only
            once a span of it is annotated, and by then the decision is being taken
            against a number that has already moved.
          - a **type this loader knows and the release does not declare** is the
            quieter one. It means the map was built against a different release than
            the one on disk, and every count in §9.0's block belongs to the other
            one.
        """
        path = root / SCHEMA
        if not path.is_file():
            raise CorpusError(
                f"{self.corpus_id}: no {SCHEMA.as_posix()} under the root, so the "
                "release declares no schema to check the type map against"
            )
        declared: set[str] = set()
        in_section = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_section = stripped == SCHEMA_SECTION
                continue
            if in_section and stripped and not stripped.startswith("#"):
                declared.add(stripped)
        known = set(self.type_map) | set(self.excluded_types) | CONCEPT_TYPES
        if declared != known:
            undecided = sorted(declared - known)
            absent = sorted(known - declared)
            raise CorpusError(
                f"{self.corpus_id}: {SCHEMA.as_posix()} declares "
                f"{len(declared)} entity types and this loader knows {len(known)}. "
                f"Declared but not decided: {undecided}. Known but not declared: "
                f"{absent}. Decide the first list in DESIGN §9.0/§9.1 before "
                "loading; the second means the map and the release are different "
                "versions and every count in §9.0's block is over the other one."
            )

    def _mappings(self, root: Path) -> dict[str, tuple[str, bool]]:
        """doc_id -> (language label, whether the concept layer covers it).

        Read per root for the reason `_check_schema` gives. Nothing here is defaulted:
        a document with no row has no stratum, and inventing one would put a
        stratification the split file asserts on top of a label nobody recorded.
        """
        path = root / MAPPINGS
        if not path.is_file():
            raise CorpusError(
                f"{self.corpus_id}: no {MAPPINGS.as_posix()} under the root, so no "
                "document has a language label and §9.5's stratification has no "
                "variable"
            )
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            fields = tuple(reader.fieldnames or ())
            if fields != MAPPINGS_FIELDS:
                raise CorpusError(
                    f"{self.corpus_id}: {MAPPINGS.as_posix()} has columns {fields}, "
                    f"expected {MAPPINGS_FIELDS}"
                )
            rows = list(reader)
        labels: dict[str, tuple[str, bool]] = {}
        for row_no, row in enumerate(rows, start=2):
            doc_id = row["filename"]
            if doc_id in labels:
                raise CorpusError(
                    f"{self.corpus_id}: {MAPPINGS.as_posix()}:{row_no} repeats a "
                    "document id that an earlier row already labelled"
                )
            language = row["language"]
            if language not in LANGUAGE_LABELS:
                raise CorpusError(
                    f"{self.corpus_id}: {MAPPINGS.as_posix()}:{row_no} carries "
                    f"language label {language!r}, which is not one of "
                    f"{sorted(LANGUAGE_LABELS)}. These are the corpus's own labels "
                    "and a new one is a stratum the frozen split does not describe."
                )
            flag = row["ner_annotations"]
            if flag not in ("True", "False"):
                raise CorpusError(
                    f"{self.corpus_id}: {MAPPINGS.as_posix()}:{row_no} has "
                    f"ner_annotations {flag!r}, expected 'True' or 'False'"
                )
            labels[doc_id] = (language, flag == "True")
        return labels

    def _check_concept_layer(
        self, root: Path, labels: dict[str, tuple[str, bool]]
    ) -> None:
        """`ner_annotations` must describe the concept layer that is actually there.

        The flag is not read for anything — this loader reads the PHI layer only — and
        that is precisely why it is checked. It is the one claim `CARMEN1_mappings.tsv`
        makes that can be verified against the directory tree, so it is the evidence
        that the file describes *this* release rather than another one, and the
        language label in the same rows is the value the frozen split rests on.
        Measured 2026-09-28: 500 flagged, 500 files, and the same 500 under both
        variants.
        """
        directory = root / ANN_ROOT / VARIANT / CONCEPT_LAYER
        if not directory.is_dir():
            raise CorpusError(
                f"{self.corpus_id}: no "
                f"{(ANN_ROOT / VARIANT / CONCEPT_LAYER).as_posix()} directory under "
                "the root, so the ner_annotations column describes nothing"
            )
        present = {path.stem for path in directory.glob("*.ann")}
        flagged = {doc_id for doc_id, (_, flag) in labels.items() if flag}
        if present != flagged:
            raise CorpusError(
                f"{self.corpus_id}: {MAPPINGS.as_posix()} flags {len(flagged)} "
                f"documents as carrying concept annotations and "
                f"{(ANN_ROOT / VARIANT / CONCEPT_LAYER).as_posix()} holds "
                f"{len(present)} files; {len(flagged - present)} flagged documents "
                f"have no file and {len(present - flagged)} files are unflagged. The "
                "mappings file and this release are not the same version, and the "
                "language labels in it are what the frozen split stratifies on."
            )

    # -- reading --

    def _read(self) -> Iterator[Document]:
        from_sealed = 0
        sealed = self.sealed_reachable()
        for root in self.source_roots():
            self._check_schema(root)
            labels = self._mappings(root)
            ann_files = self._annotation_files(root)
            if not ann_files:
                raise self._nothing_to_read(root, sealed)
            self._check_concept_layer(root, labels)
            stems = {path.stem for path in ann_files}
            if stems != set(labels):
                raise CorpusError(
                    f"{self.corpus_id}: the PHI layer under "
                    f"{'the sealed' if root == sealed else 'the corpus'} root holds "
                    f"{len(stems)} documents and {MAPPINGS.as_posix()} labels "
                    f"{len(labels)}; {len(stems - set(labels))} documents have no row "
                    f"and {len(set(labels) - stems)} rows have no document. A row is "
                    "a document's language label and there is no default for it."
                )
            for ann_path in ann_files:
                if root == sealed:
                    from_sealed += 1
                yield self._read_document(ann_path, labels)
        if sealed is not None and from_sealed == 0:
            # A backstop, and unreachable as this method now stands: every root that
            # gets past `_nothing_to_read` has at least one `.ann` file and so yields at
            # least one document. It is here because that is a property of the current
            # loop and not of the invariant — the moment a filter appears between the
            # file list and the `yield` (which is how `kosurro.py` reached this state:
            # records without gold support are dropped), a sealed root can pass every
            # check above and still contribute nothing. `tests/test_carmen_loader.py`
            # exercises the reachable guard; this one has no test and says so.
            raise SealError(
                f"{self.corpus_id}: a sealed read was authorised but the sealed root "
                "holds no documents, so the fold was not read at all. The log has "
                "already recorded this access; note in results/sealed_eval_log.md "
                "that the run did not complete, and fix the seal before running "
                "again."
            )

    def _read_document(
        self, ann_path: Path, labels: dict[str, tuple[str, bool]]
    ) -> Document:
        doc_id = ann_path.stem
        txt_path = ann_path.with_suffix(".txt")
        if not txt_path.exists():
            raise CorpusError(
                f"{self.corpus_id}: {ann_path.name} has no matching .txt"
            )

        # Plain utf-8, deliberately not utf-8-sig, and the shift is applied to the
        # offsets rather than decoded away (DESIGN §9.7). Measured 2026-09-28: no
        # document in this release carries a BOM, so the shift is 0 in all 2,000. The
        # arithmetic stays because a release that gained one would otherwise move
        # every span in that document by one with nothing saying so — MEDDOCAN's 32
        # BOM files and GraSCCo's 5 are what that looks like when it is not handled.
        raw = txt_path.read_text(encoding="utf-8")
        text, shift = self.strip_bom(raw)

        spans = [
            self._parse_line(line, line_no, ann_path, shift)
            for line_no, line in enumerate(
                ann_path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if line.strip()
        ]

        corrected = self._apply_known_defects(doc_id, spans, text)
        language, has_concepts = labels[doc_id]
        doctype, section, index = self._filename_parts(doc_id)
        meta: dict[str, object] = {
            # `language_label` and not `lang`: these are the corpus's own labels and
            # `bi` is not a `naming.yaml` language. The name is what stops a call site
            # from handing `bi` to a `rules/{lang}.yaml` path.
            "language_label": language,
            # `filename_doctype` and not `document_type`: the §7 axis is derived from
            # text cues and `es-carmen` declares none, and 789 of these units are
            # sections rather than notes (§8.5).
            "filename_doctype": doctype,
            "filename_section": section,
            # The trailing number, which is what §9.5 step 1's candidate key for this
            # corpus pairs with the doctype: `IA_ANTECEDENTES_7` and
            # `IA_PROCESO_ACTUAL_7` read like two sections of one letter. Recorded here
            # rather than re-parsed in `src/split.py` because this is the file that owns
            # the id shape — a second pattern over the same ids is how one of the two
            # comes to apply a different rule (the same argument `stem_index` makes).
            "filename_index": index,
            "has_concept_layer": has_concepts,
        }
        if corrected:
            # Recorded the way `grascco.py` records its BOM-clipped spans: a
            # correction the loader made is a fact about the document, and a fact only
            # the loader's source states is one no result file can be audited against.
            meta["surface_corrected_spans"] = corrected
        return Document(
            doc_id=doc_id,
            corpus_id=self.corpus_id,
            text=text,
            spans=spans,
            # No fold: nothing in the layout encodes one, so the frozen split file is
            # the only authority and `_apply_split_file()` fills this in.
            split=None,
            had_bom=shift > 0,
            meta=meta,
        )

    def _apply_known_defects(
        self, doc_id: str, spans: list[Span], text: str
    ) -> list[int]:
        """Substitute the text for the surface of a pinned defective span. Returns the
        indices corrected, which is empty for 1,999 of the 2,000 documents.

        Both refusals below are the reason this is a pin and not a tolerance. The first
        keeps the pin from drifting onto a different span; the second retires the pin
        when the release stops needing it, because a permission to overwrite a surface
        that nothing is currently overwriting is a permission that will be used by the
        next data error instead of reporting it. DESIGN §9.7 holds the evidence that
        the offsets rather than the surface are what this release got wrong.
        """
        corrected: list[int] = []
        for (pinned_doc, index), pinned in sorted(KNOWN_SURFACE_DEFECTS.items()):
            if pinned_doc != doc_id:
                continue
            if index >= len(spans):
                raise CorpusError(
                    f"{self.corpus_id}: {doc_id} is pinned in "
                    f"KNOWN_SURFACE_DEFECTS at span index {index} but holds only "
                    f"{len(spans)} spans. The pin names a span that no longer exists, "
                    "so it describes a different release (DESIGN §9.7)."
                )
            span = spans[index]
            if (span.subtype, span.start, span.end) != pinned:
                raise CorpusError(
                    f"{self.corpus_id}: {doc_id} span {index} is pinned in "
                    f"KNOWN_SURFACE_DEFECTS as {pinned} and is "
                    f"{(span.subtype, span.start, span.end)}. The pin excuses one "
                    "span's recorded surface and must not be allowed to slide onto "
                    "another (DESIGN §9.7)."
                )
            if text[span.start : span.end] == span.surface:
                raise CorpusError(
                    f"{self.corpus_id}: {doc_id} span {index} is pinned in "
                    f"KNOWN_SURFACE_DEFECTS but its recorded surface now matches the "
                    "text, so the release has fixed the defect. Remove the entry — a "
                    "standing permission to overwrite a surface would correct the "
                    "next data error in this document instead of reporting it "
                    "(DESIGN §9.7)."
                )
            spans[index] = replace(span, surface=text[span.start : span.end])
            corrected.append(index)
        return corrected

    def _filename_parts(self, doc_id: str) -> tuple[str, str | None, int]:
        """`CARMEN-I_{doctype}[_{section}]_{n}` -> (doctype, section or None, n).

        The split's stratum and its §9.5 step-1 candidate key, so a failure here is a
        failure to stratify and not a cosmetic one. Both branches raise rather than
        falling back on a residual label: a document that landed in an `unparsed` or
        `other` stratum would be stratified on a label meaning "this code did not
        understand the id", and the split file would record that as a composition.
        """
        match = DOC_ID_RE.match(doc_id)
        if match is None:
            raise CorpusError(
                f"{self.corpus_id}: doc_id {doc_id!r} does not parse as "
                "CARMEN-I_{doctype}[_{section}]_{n}, so it has no document-type "
                "stratum. All 2,000 ids in this release parse; a new id shape needs "
                "a stratification decision (§9.5), not a default."
            )
        doctype = match.group("doctype")
        if doctype not in DOCTYPE_TOKENS:
            raise CorpusError(
                f"{self.corpus_id}: doc_id {doc_id!r} carries document-type token "
                f"{doctype!r}, which is not one of {sorted(DOCTYPE_TOKENS)}. The "
                "frozen split records a composition over those labels and a new one "
                "would change what that record means."
            )
        return doctype, match.group("section"), int(match.group("n"))

    def _parse_line(
        self, line: str, line_no: int, ann_path: Path, shift: int
    ) -> Span:
        """Parse one brat standoff line.

        Format, measured across all 8,231 lines of the `replaced` variant's PHI layer:

            T{n}<TAB>{TYPE} {start} {end}<TAB>{surface}

        Exactly three tab-separated fields, every line a `T` (text-bound) annotation,
        and zero multi-fragment lines — brat's `start end;start end` form does not
        occur. Parsed strictly rather than defensively: a release that introduced
        relation lines or discontinuous spans would need a decision about how they are
        scored, so it should fail here rather than be silently reshaped into something
        scoreable. That is `meddocan.py`'s rule and the two formats are the same one.

        **The messages differ from `meddocan.py`'s, and only in what they show.** That
        loader prints the offset field on a malformed line, which is safe because
        MEDDOCAN is synthetic; here a malformed field can hold clinical text, so the
        field is described by length and position and never quoted. The type name is
        the one piece of a line that is safe to print — it comes from a closed
        declared set this module checks against `annotation.conf` — and it is what
        makes a failure locatable.
        """
        where = f"{ann_path.name}:{line_no}"
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 3:
            raise CorpusError(
                f"{self.corpus_id}: {where} has {len(fields)} tab-separated fields, "
                "expected 3 (T-id, type+offsets, surface)"
            )
        tag_id, middle, surface = fields
        if not tag_id.startswith("T"):
            raise CorpusError(
                f"{self.corpus_id}: {where} is a {tag_id[:1]!r} annotation; only "
                "text-bound T annotations are expected. A relation or attribute line "
                "needs a scoring decision before it can be loaded."
            )
        parts = middle.split()
        if len(parts) != 3:
            raise CorpusError(
                f"{self.corpus_id}: {where} has {len(parts)} space-separated parts "
                f"in its second field ({len(middle)} characters), expected 3 "
                "('TYPE start end'). Multi-fragment spans (start end;start end) do "
                "not occur in this release and have no scoring rule. The field is "
                "not quoted: on a malformed line it can hold document text "
                "(CLAUDE.md)."
            )
        corpus_type, start_s, end_s = parts
        try:
            start, end = int(start_s), int(end_s)
        except ValueError as exc:
            raise CorpusError(
                f"{self.corpus_id}: {where} has non-integer offsets for a "
                f"{corpus_type} annotation"
            ) from exc
        if corpus_type in CONCEPT_TYPES:
            raise CorpusError(
                f"{self.corpus_id}: {where} is a {corpus_type} annotation, which is "
                f"the medical-concept layer's type and not PHI. The PHI layer is "
                f"{(ANN_ROOT / VARIANT / PHI_LAYER).as_posix()} and the concept "
                f"layer is {(ANN_ROOT / VARIANT / CONCEPT_LAYER).as_posix()}; a "
                "concept span in the PHI layer means the two have been mixed, and "
                "loading it would add 26,360 spans to a gold set of 8,231."
            )

        phi_type, excluded = self.classify(corpus_type)
        # Shift by the BOM length, not by re-searching for the surface: the correction
        # has to be arithmetic and uniform, or it silently repairs some genuinely
        # wrong offsets and hides them from assert_offsets.
        return Span(
            start=start - shift,
            end=end - shift,
            surface=surface,
            subtype=corpus_type,
            phi_type=phi_type,
            excluded=excluded,
        )
