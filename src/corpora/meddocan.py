"""MEDDOCAN loader.

Spanish synthetic clinical case studies, 1,000 annotated documents with the
shared task's official train 500 / dev 250 / test 250 split, plus 3,751
unannotated background documents that this loader ignores.

Two encodings of the same annotations ship: brat standoff (`.txt` + `.ann`) and
i2b2-style XML. **brat is read.** The offsets index the brat `.txt` and the two
are redundant, so reading both would mean choosing which to trust when they
disagree; reading one means a disagreement cannot arise.

Two things here are corpus-specific and neither is cosmetic:

  - 32 of 1,000 brat `.txt` files carry a UTF-8 BOM, and the shipped offsets
    count it. Stripped and shifted per DESIGN §9.7 — see `_read`.
  - `SEXO_SUJETO_ASISTENCIA` and `FAMILIARES_SUJETO_ASISTENCIA` are out of scope
    per DESIGN §9.1 but are loaded and flagged, because their volume (2,257
    spans, 9.90% of gold) is a reported limitation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import CorpusError, CorpusLoader, Document, Span

#: MEDDOCAN type -> canonical type. The human-authored `port-human` mapping of
#: DESIGN §9.0; `mappings/es-meddocan.yaml` is where the Mapper agent's version
#: will go. Kept as a literal here so that a mapping change is a reviewable diff.
TYPE_MAP: dict[str, str] = {
    # NAME — role (patient vs clinician) stays in subtype, per DESIGN §9.0
    "NOMBRE_SUJETO_ASISTENCIA": "NAME",
    "NOMBRE_PERSONAL_SANITARIO": "NAME",
    # DATE
    "FECHAS": "DATE",
    # AGE
    "EDAD_SUJETO_ASISTENCIA": "AGE",
    # LOCATION_AREA — TERRITORIO mixes place names and postcodes and is merged
    # rather than split, because splitting it means inferring gold (DESIGN §9.2)
    "TERRITORIO": "LOCATION_AREA",
    "PAIS": "LOCATION_AREA",
    # LOCATION_STREET
    "CALLE": "LOCATION_STREET",
    # ORGANISATION
    "HOSPITAL": "ORGANISATION",
    "INSTITUCION": "ORGANISATION",
    "CENTRO_SALUD": "ORGANISATION",
    # CONTACT
    "CORREO_ELECTRONICO": "CONTACT",
    "NUMERO_TELEFONO": "CONTACT",
    "NUMERO_FAX": "CONTACT",
    # ID — four subtypes collapse here; GraSCCo has one, and forcing agreement
    # would measure the annotation schema rather than the detector (DESIGN §9.0)
    "ID_SUJETO_ASISTENCIA": "ID",
    "ID_ASEGURAMIENTO": "ID",
    "ID_TITULACION_PERSONAL_SANITARIO": "ID",
    "ID_CONTACTO_ASISTENCIAL": "ID",
    "ID_EMPLEO_PERSONAL_SANITARIO": "ID",
    # PROFESSION
    "PROFESION": "PROFESSION",
    # OTHER — a residual bucket the corpus ships (ethnicity, marital status, and
    # stranger things). Kept in the leak-rate denominator as an irreducible floor
    # and explicitly not a rule-development target (DESIGN §9.4).
    "OTROS_SUJETO_ASISTENCIA": "OTHER",
}

#: Kept and flagged, not dropped (DESIGN §9.1). Sex and relationship words are
#: not HIPAA Safe Harbor identifiers — the FAMILIARES surfaces are common nouns
#: like `madre` — so scoring a detector on them measures something other than
#: disclosure risk. The excluded volume is reported as a limitation, which is why
#: the spans have to survive loading.
EXCLUDED_TYPES = frozenset(
    {
        "SEXO_SUJETO_ASISTENCIA",
        "FAMILIARES_SUJETO_ASISTENCIA",
    }
)

#: The official split, as shipped. Directory name -> naming.yaml split value.
#: They coincide for MEDDOCAN; the indirection is here so that a corpus whose
#: directories are named differently cannot tempt anyone into renaming a fold.
SPLIT_DIRS: dict[str, str] = {"train": "train", "dev": "dev", "test": "test"}


class MeddocanLoader(CorpusLoader):
    corpus_id = "es-meddocan"
    type_map = TYPE_MAP
    excluded_types = EXCLUDED_TYPES

    def _brat_dir(self, split_dir: str) -> Path:
        """Locate `{split}/brat/`, tolerating one wrapper directory.

        The Zenodo archive extracts to a `meddocan/` directory, but whether that
        wrapper survives depends on how the archive was unpacked. Probing two
        depths beats requiring every machine's checkout to match, and beats
        hardcoding the wrapper name in a path (CLAUDE.md: no hardcoded paths).
        """
        candidates = [
            self.root / split_dir / "brat",
            self.root / "meddocan" / split_dir / "brat",
        ]
        for path in candidates:
            if path.is_dir():
                return path
        raise CorpusError(
            f"{self.corpus_id}: no {split_dir}/brat directory under the corpus "
            "root. Check config/data_paths.local.yaml and "
            "data/acquire/fetch_meddocan.sh."
        )

    def _read(self) -> Iterator[Document]:
        for split_dir, split in SPLIT_DIRS.items():
            brat = self._brat_dir(split_dir)
            ann_files = sorted(brat.glob("*.ann"))
            if not ann_files:
                raise CorpusError(f"{self.corpus_id}: {brat} holds no .ann files")
            for ann_path in ann_files:
                yield self._read_document(ann_path, split)

    def _read_document(self, ann_path: Path, split: str) -> Document:
        txt_path = ann_path.with_suffix(".txt")
        if not txt_path.exists():
            raise CorpusError(
                f"{self.corpus_id}: {ann_path.name} has no matching .txt"
            )

        # Plain utf-8, deliberately not utf-8-sig: the shipped offsets count the
        # BOM as a character, so decoding it away without shifting the offsets
        # breaks all 761 spans in the 32 BOM files (DESIGN §9.7).
        raw = txt_path.read_text(encoding="utf-8")
        text, shift = self.strip_bom(raw)

        spans = [
            self._parse_line(line, line_no, ann_path, shift)
            for line_no, line in enumerate(
                ann_path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if line.strip()
        ]

        return Document(
            doc_id=ann_path.stem,
            corpus_id=self.corpus_id,
            text=text,
            spans=spans,
            split=split,
            had_bom=shift > 0,
        )

    def _parse_line(
        self, line: str, line_no: int, ann_path: Path, shift: int
    ) -> Span:
        """Parse one brat standoff line.

        Format, measured across all 22,795 lines of train+dev+test:

            T{n}<TAB>{TYPE} {start} {end}<TAB>{surface}

        Exactly three tab-separated fields, every line a `T` (text-bound)
        annotation, and zero multi-fragment lines — brat's `start end;start end`
        form does not occur. Parsed strictly rather than defensively: a release
        that introduced relation lines or discontinuous spans would need a
        decision about how they are scored, so it should fail here rather than be
        silently reshaped into something scoreable.
        """
        where = f"{ann_path.name}:{line_no}"
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 3:
            raise CorpusError(
                f"{self.corpus_id}: {where} has {len(fields)} tab-separated "
                "fields, expected 3 (T-id, type+offsets, surface)"
            )
        tag_id, middle, surface = fields
        if not tag_id.startswith("T"):
            raise CorpusError(
                f"{self.corpus_id}: {where} is a {tag_id[:1]!r} annotation; only "
                "text-bound T annotations are expected. A relation or attribute "
                "line needs a scoring decision before it can be loaded."
            )
        parts = middle.split()
        if len(parts) != 3:
            raise CorpusError(
                f"{self.corpus_id}: {where} has offsets {middle!r}; expected "
                "'TYPE start end'. Multi-fragment spans (start end;start end) do "
                "not occur in this release and have no scoring rule."
            )
        corpus_type, start_s, end_s = parts
        try:
            start, end = int(start_s), int(end_s)
        except ValueError as exc:
            raise CorpusError(
                f"{self.corpus_id}: {where} has non-integer offsets {middle!r}"
            ) from exc

        phi_type, excluded = self.classify(corpus_type)
        # Shift by the BOM length, not by re-searching for the surface: the
        # correction has to be arithmetic and uniform, or it silently repairs
        # some genuinely wrong offsets and hides them from assert_offsets.
        return Span(
            start=start - shift,
            end=end - shift,
            surface=surface,
            subtype=corpus_type,
            phi_type=phi_type,
            excluded=excluded,
        )
