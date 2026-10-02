"""Tests for the CARMEN-I loader.

The same two kinds as the four loader files before it, because the counts these tests
pin are the ones DESIGN §9.0 reports and the paper prints:

  - **Recount tests** re-derive the totals by a route that shares no code with the
    loader. Here that route is unusually good: the release ships an *aggregated* TSV
    (`tsv/{variant}/CARMEN-I_replaced_anon.tsv`, one row per span) which the loader
    deliberately does not read — §9.7 records why, it disagrees with the text in 38 of
    8,231 rows against the standoff's 1 — so it is an independent encoding of the same
    annotations rather than a second pass over the same bytes. The seal **recomputed**
    that file per root rather than copying it (`tools/prepare_carmen.py`), so it is still
    an independent encoding and it now encodes the visible corpus.
  - **Constant tests** pin §9.0's published figures, so a re-released corpus fails here
    instead of quietly moving every number in the paper.

**No surface form appears in this file, and that is a rule rather than a habit.** These
are real hospital records under a PhysioNet DUA (CLAUDE.md, "실제 병원 기록"), so no test
here writes a slice of the corpus into an assertion, a parametrisation or a failure
message. That constrains how the tests below are written:

  - Every assertion about a span is over offsets, lengths, counts and type names.
  - The failure-mode tests build synthetic brat trees in `tmp_path` with invented
    Spanish text, which is also what makes `test_a_malformed_offset_field_is_not_quoted`
    meaningful rather than accidental — the token it looks for exists only in this file.
  - The one span whose recorded surface this loader overwrites (§9.7) is asserted by
    document id, span index, type and offsets. Its text is not in this file, not in the
    loader, and not in DESIGN.

**These tests now see 1,600 documents, not 2,000 — the seal ran on 2026-10-01.** What
stood here the day before said that the constants were still the corpus-wide ones and
that `test_nothing_is_sealed_yet` was what would fail on the day the seal landed. It
did. The constants have come apart into a visible set and a `FULL_*` set the way the
GraSCCo and ko-surro files' did, under one rule: **a bare name is what the loader
reaches and a `FULL_` name is the corpus**. The `FULL_*` figures are no longer
independently checkable from here; `test_the_split_file_accounts_for_the_seal` is what
keeps them falsifiable, by requiring *visible recount + the frozen file's sealed block =
the corpus-wide total*, per type as well as in aggregate. That is weaker than the recount
it replaces, and it is what is left — the run that produced the `FULL_*` numbers is the
one the freeze commit records.

Three things the seal moved that are not just a number:

  - **The §9.7 pin is in the test fold.** `DEFECT_DOC` went behind the seal with it, so
    the visible corpus carries no corrected surface at all. The pin is still asserted —
    as the loader's own table, and as membership in the frozen file's test fold, which is
    a document id and not a surface — and the mechanism it drives is exercised by the
    synthetic pin tests at the end of this file rather than by the corpus.
  - **§5.1's and §9.1's published shares are corpus-wide** and are now arithmetic over
    the `FULL_*` constants. The visible shares are pinned separately and differ in the
    second digit, which is exactly the hazard the pre-seal file already had with 72.1%
    and 74.3%: three numbers of the same shape, none of which may be quoted as another.
  - **A rebuild of the split no longer runs.** `_build_constructed` refuses once a sealed
    root is declared, so the three `_achieved` recount halves reach the generator by
    calling `split._achieved` directly over the visible documents with the frozen file's
    fold assignment. The section below says what that can and cannot still show.

The three tests about the composition the split stratifies on (§9.5) predate the freeze
on purpose: they are what the deterministic small-cell rule was written against, and a
file recording an achieved composition means nothing unless the requested one is pinned
somewhere that fails when the corpus moves.

    python3 -m pytest tests/test_carmen_loader.py -q
"""
import collections
import csv
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import split  # noqa: E402
from src.corpora import CorpusError, base  # noqa: E402
from src.corpora.base import SealError  # noqa: E402
from src.corpora.carmen import (  # noqa: E402
    ANN_ROOT,
    CONCEPT_LAYER,
    CONCEPT_TYPES,
    DOCTYPE_TOKENS,
    EXCLUDED_TYPES,
    KNOWN_SURFACE_DEFECTS,
    LANGUAGE_LABELS,
    MAPPINGS,
    MAPPINGS_FIELDS,
    PHI_LAYER,
    RELEASE,
    SCHEMA,
    SCHEMA_SECTION,
    TYPE_MAP,
    UNREAD_TEXT_DIR,
    VARIANT,
    CarmenLoader,
)

# ─── expected values (DESIGN §9.0, §9.1, §9.5, §9.7) ────────────────────────
# **Two sets, and the naming rule is one sentence: a bare name is what the loader reaches
# and a `FULL_` name is the corpus.** The bare names were the corpus-wide ones until
# 2026-10-01 and were re-measured over the visible root on the day of the seal; the
# `FULL_*` ones are the 2026-09-28 whole-release measurement, unchanged, and are what
# DESIGN §9.0 publishes. Same shape as `tests/test_kosurro_loader.py` and for the same
# reason — after the seal a test that recomputed a corpus-wide figure would be reading
# the test fold to do it.
#
# The two sets are tied together by `test_the_split_file_accounts_for_the_seal`, which is
# the only thing keeping the `FULL_*` half falsifiable from here. The stratification
# block below is 2026-09-30 and the freeze's, and is corpus-wide by construction: it is
# read out of the frozen file rather than recomputed.

N_DOCS = 1600
N_SPANS = 6532
N_CANONICAL = 5934
N_EXCLUDED = 598

FULL_N_DOCS = 2000
FULL_N_SPANS = 8231
FULL_N_CANONICAL = 7473
FULL_N_EXCLUDED = 758

#: §9.0's `es-carmen` table, canonical column — `FULL_` — and the visible recount of it.
#: All ten canonical types still have gold in the visible 1,600, which is not automatic:
#: `CONTACT` has 17 of its 22 and a seal that took them all would have made §9.0's "every
#: canonical type has gold here" claim untestable rather than false.
CANONICAL_COUNTS = {
    "DATE": 4261,
    "AGE": 636,
    "ORGANISATION": 386,
    "ID": 206,
    "LOCATION_AREA": 182,
    "NAME": 122,
    "PROFESSION": 75,
    "OTHER": 29,
    "LOCATION_STREET": 20,
    "CONTACT": 17,
}
FULL_CANONICAL_COUNTS = {
    "DATE": 5386,
    "AGE": 815,
    "ORGANISATION": 497,
    "ID": 243,
    "LOCATION_AREA": 208,
    "NAME": 151,
    "PROFESSION": 91,
    "OTHER": 38,
    "CONTACT": 22,
    "LOCATION_STREET": 22,
}

#: The same table's source-type column, which is what the aggregated TSV counts by. All
#: 18 observed source types survive in the visible corpus, including the two that occur
#: twice and once — so `N_OBSERVED_TYPES` is still 18 and still measured, not inherited.
SUBTYPE_COUNTS = {
    "FECHAS": 4261,
    "EDAD_SUJETO_ASISTENCIA": 636,
    "SEXO_SUJETO_ASISTENCIA": 365,
    "HOSPITAL": 246,
    "FAMILIARES_SUJETO_ASISTENCIA": 232,
    "NUMERO_IDENTIF": 192,
    "NOMBRE_PERSONAL_SANITARIO": 122,
    "PAIS": 106,
    "INSTITUCION": 98,
    "TERRITORIO": 76,
    "PROFESION": 75,
    "CENTRO_SALUD": 42,
    "OTROS_SUJETO_ASISTENCIA": 29,
    "CALLE": 20,
    "NUMERO_TELEFONO": 17,
    "ID_SUJETO_ASISTENCIA": 12,
    "ID_CONTACTO_ASISTENCIAL": 2,
    "URL_WEB": 1,
}
FULL_SUBTYPE_COUNTS = {
    "FECHAS": 5386,
    "EDAD_SUJETO_ASISTENCIA": 815,
    "SEXO_SUJETO_ASISTENCIA": 458,
    "HOSPITAL": 316,
    "FAMILIARES_SUJETO_ASISTENCIA": 299,
    "NUMERO_IDENTIF": 227,
    "NOMBRE_PERSONAL_SANITARIO": 151,
    "INSTITUCION": 129,
    "PAIS": 118,
    "PROFESION": 91,
    "TERRITORIO": 90,
    "CENTRO_SALUD": 52,
    "OTROS_SUJETO_ASISTENCIA": 38,
    "CALLE": 22,
    "NUMERO_TELEFONO": 22,
    "ID_SUJETO_ASISTENCIA": 14,
    "ID_CONTACTO_ASISTENCIAL": 2,
    "URL_WEB": 1,
}
EXCLUDED_COUNTS = {
    "SEXO_SUJETO_ASISTENCIA": 365,
    "FAMILIARES_SUJETO_ASISTENCIA": 232,
    "URL_WEB": 1,
}
FULL_EXCLUDED_COUNTS = {
    "SEXO_SUJETO_ASISTENCIA": 458,
    "FAMILIARES_SUJETO_ASISTENCIA": 299,
    "URL_WEB": 1,
}

#: §9.0: the schema declares 28 PHI types and 18 are used. The ten unused ones are
#: mapped or excluded anyway, which is the property `test_every_declared_type_is_decided`
#: is about; nine map and `DIREC_PROT_INTERNET` is the tenth and is excluded (§9.1).
N_DECLARED_PHI = 28
N_OBSERVED_TYPES = 18
UNUSED_TYPES = {
    "NOMBRE_SUJETO_ASISTENCIA",
    "CORREO_ELECTRONICO",
    "NUMERO_FAX",
    "DIREC_PROT_INTERNET",
    "ID_ASEGURAMIENTO",
    "ID_TITULACION_PERSONAL_SANITARIO",
    "ID_EMPLEO_PERSONAL_SANITARIO",
    "IDENTIF_VEHICULOS_NRSERIE_PLACAS",
    "IDENTIF_DISPOSITIVOS_NRSERIE",
    "IDENTIF_BIOMETRICOS",
}

#: §9.5's strata. The `FULL_*` composition is the one the user's instruction names
#: (es 1,697 / bi 264 / cat 39) and the one the deterministic small-cell rule was written
#: against: 12 of the 15 possible cells non-empty, the smallest holding **one** document,
#: and a three-way split of one document not being a thing. It is corpus-wide, so after
#: the seal it is read out of the frozen file's `requested` block rather than recounted —
#: which is why it is still pinned here, as the thing that block is checked against.
#:
#: The bare names are the visible recount. Twelve cells still, and the smallest is still
#: `(CC, bi)` at one — but `(IE, es)` has fallen to **four**, below the threshold it sat
#: exactly on corpus-wide. That is why the recount halves below supply the frozen file's
#: collapse decision instead of re-deriving it: a rebuild over the visible corpus would
#: collapse `IE` as well and produce stratum names the frozen file does not have.
LANGUAGE_DOCS = {"es": 1357, "bi": 212, "cat": 31}
DOCTYPE_DOCS = {"IR": 961, "IA": 494, "IT": 137, "CC": 4, "IE": 4}
CROSS = {
    ("IR", "es"): 769,
    ("IA", "es"): 458,
    ("IR", "bi"): 177,
    ("IT", "es"): 123,
    ("IA", "bi"): 25,
    ("IR", "cat"): 15,
    ("IA", "cat"): 11,
    ("IT", "bi"): 9,
    ("IT", "cat"): 5,
    ("IE", "es"): 4,
    ("CC", "es"): 3,
    ("CC", "bi"): 1,
}
FULL_LANGUAGE_DOCS = {"es": 1697, "bi": 264, "cat": 39}
FULL_DOCTYPE_DOCS = {"IR": 1201, "IA": 617, "IT": 172, "CC": 5, "IE": 5}
FULL_CROSS = {
    ("IR", "es"): 961,
    ("IA", "es"): 573,
    ("IR", "bi"): 221,
    ("IT", "es"): 154,
    ("IA", "bi"): 31,
    ("IR", "cat"): 19,
    ("IA", "cat"): 13,
    ("IT", "bi"): 11,
    ("IT", "cat"): 7,
    ("IE", "es"): 5,
    ("CC", "es"): 4,
    ("CC", "bi"): 1,
}
SMALLEST_CELL = ("CC", "bi")

#: The corpus id, so the three `split` lookups below do not each spell it again.
CARMEN = "es-carmen"

#: What the cross above becomes once §9.5's small-cell rule has run, and what
#: `config/split.yaml` declares. Eleven strata, largest first — the order is an input to
#: `assign_folds`, so it is pinned as a sequence and not as a set. `CC/*` is the folded
#: one: `(CC, es)` = 4 and `(CC, bi)` = 1 are both under the threshold, so the language
#: folds inside the document type and the two become one stratum of five. `IE/es` sits
#: at exactly 5 and does **not** collapse, which is the `<` / `<=` boundary.
N_STRATA = 11
SMALL_CELL_THRESHOLD = 5
STRATA = {
    "IR/es": 961,
    "IA/es": 573,
    "IR/bi": 221,
    "IT/es": 154,
    "IA/bi": 31,
    "IR/cat": 19,
    "IA/cat": 13,
    "IT/bi": 11,
    "IT/cat": 7,
    "CC/*": 5,
    "IE/es": 5,
}
CARMEN_VARIABLE = "filename_doctype_x_language_label"
CARMEN_COLLAPSED = ["CC"]

#: The same eleven strata over the visible corpus — the frozen file's *names* with the
#: visible sizes, which is what `rebuilt` below reports. Two have fallen under the
#: threshold: `CC/*` 5 → 4 and `IE/es` 5 → 4. Pinned because that pair is the clearest
#: single statement of what the seal costs §9.5's balance guarantee, and because a
#: stratum that emptied entirely would otherwise be a zero nobody looked at.
VISIBLE_STRATA = {
    "IR/es": 769,
    "IA/es": 458,
    "IR/bi": 177,
    "IT/es": 123,
    "IA/bi": 25,
    "IR/cat": 15,
    "IA/cat": 11,
    "IT/bi": 9,
    "IT/cat": 5,
    "CC/*": 4,
    "IE/es": 4,
}

#: The frozen split, in documents (DESIGN §9.5, `config/split.yaml` 0.60/0.20/0.20).
#: `VISIBLE_SPLIT` is the same thing after the seal: the two folds an ordinary load
#: reaches, with `test` **absent** rather than present at zero. That distinction is what
#: `test_only_the_unsealed_folds_load` asserts — a fold reported as empty is the shape a
#: broken seal produces, and the shape `_nothing_to_read` exists to refuse.
CONSTRUCTED_SPLIT = {"train": 1200, "dev": 400, "test": 400}
VISIBLE_SPLIT = {"train": 1200, "dev": 400}
SEALED_FOLD = "test"

#: How the collapsed stratum's five documents were dealt out. Pinned because it is the
#: one stratum whose *existence* is the small-cell rule's doing: five documents is the
#: smallest size at which 0.60/0.20/0.20 gives every fold at least one, so 3/1/1 is the
#: only shape that honours the proportions and a different one means the rule collapsed
#: the wrong thing or nothing at all.
CC_SHARE = {"train": 3, "dev": 1, "test": 1}

#: §8.5: 789 of the 2,000 units are clinical sections rather than whole notes, which is
#: why `meta` says `filename_doctype` and not `document_type`. Nine section tokens plus
#: the sectionless form, and all nine tokens survive the seal.
N_SECTION_UNITS = 631
FULL_N_SECTION_UNITS = 789
N_SECTION_TOKENS = 9

#: The `ner_annotations` column, and the only claim the mappings file makes that can be
#: checked against the tree. The seal recomputed `CARMEN1_mappings.tsv` per root, so this
#: is a check of *this* root's mappings against *this* root's `ner/` directory.
N_CONCEPT_FLAGGED = 394
FULL_N_CONCEPT_FLAGGED = 500

#: Sparsity, both readings. `src/split.py`'s narrative uses the in-scope one — the
#: distinction `sparsity_counts_excluded_spans` exists to keep. The gap is 7 visible and
#: 9 corpus-wide, so two of the nine documents whose only spans are §9.1-excluded are in
#: the test fold; both readings are pinned on both sides rather than one being derived
#: from the other, because the gap is the whole point of the pair.
N_DOCS_WITH_NO_SPAN = 381
N_DOCS_WITH_NO_IN_SCOPE_SPAN = 388
FULL_N_DOCS_WITH_NO_SPAN = 461
FULL_N_DOCS_WITH_NO_IN_SCOPE_SPAN = 470

#: §9.7: no document in this release carries a BOM, and exactly one span's recorded
#: surface disagrees with the text at its offsets. **That one document is in the test
#: fold**, so `DEFECT_*` describes something the visible corpus no longer holds — see
#: "the one span §9.7 pins" below for what is asserted instead, and the synthetic pin
#: tests at the end of the file for what still exercises the mechanism.
N_BOM_DOCS = 0
DEFECT_DOC = "CARMEN-I_IA_EVOL_112"
DEFECT_INDEX = 4
DEFECT_PIN = ("FECHAS", 153, 157)
N_TSV_DISAGREEMENTS = 38


# ─── fixtures ───────────────────────────────────────────────────────────────
# `carmen_present` / `carmen_unsplit_loader` / `carmen_docs` are in tests/conftest.py
# and are defined nowhere else. A local availability check here is the defect that
# shipped four times; see that file's header.
#
# `carmen_docs` is session-scoped and shared, so nothing below mutates it. That matters
# more here than elsewhere: `_apply_known_defects` rewrites one span in place at a known
# *index*, so a test that re-sorted a document's span list would move what the pin names.


@pytest.fixture(scope="module")
def phi_dir(carmen_unsplit_loader):
    """The PHI layer, found without the loader's iteration.

    Through `source_roots()` rather than a literal path, so this cannot reach a sealed
    root the loader would refuse to open.
    """
    roots = carmen_unsplit_loader.source_roots()
    assert roots == [carmen_unsplit_loader.root]
    return roots[0] / ANN_ROOT / VARIANT / PHI_LAYER


@pytest.fixture(scope="module")
def recount(carmen_unsplit_loader):
    """Re-derive the span totals from the release's aggregated TSV.

    The independent encoding: one row per span, written by the corpus's own tooling,
    and an encoding this loader never opens (§9.7 — it disagrees with the text in 38
    rows where the standoff disagrees in 1, which is why the standoff is what is read).
    Parsed with `csv` on the `name`/`tag` columns only. The `text` column is not read at
    all and the `span` column is not needed: what this recount is for is *how many spans
    of which type each document has*, which is the claim §9.0's table makes.

    Document count cannot come from here — 381 visible documents have no span and so no
    row — so it comes from the directory listing and the mappings file, in the test below.

    **This file was recomputed by the seal, not copied.** `tools/prepare_carmen.py` filters
    it per root by the `name` column, on raw bytes, because its fourth column is the span
    surface and a copied aggregate would have left every sealed span's text in the visible
    root. So it still shares no code with the loader, and it now encodes 6,532 rows.
    """
    root = carmen_unsplit_loader.root
    path = root / RELEASE / "tsv" / VARIANT / f"CARMEN-I_{VARIANT}_{PHI_LAYER}.tsv"
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        assert reader.fieldnames == ["name", "tag", "span", "text"]
        rows = [(row["name"], row["tag"]) for row in reader]
    out = {
        "spans": len(rows),
        "by_subtype": dict(collections.Counter(tag for _, tag in rows)),
        "by_doc": dict(collections.Counter(name for name, _ in rows)),
    }
    return out


# ─── the loader agrees with an independent recount ──────────────────────────


def test_loader_matches_the_aggregated_tsv(carmen_docs, recount):
    """Totals, per-type counts, and per-document counts against the other encoding."""
    assert base.count_spans(carmen_docs) == recount["spans"]
    assert base.count_by_type(carmen_docs, canonical=False) == recount["by_subtype"]
    loaded = {
        doc.doc_id: len(doc.spans) for doc in carmen_docs if doc.spans
    }
    assert loaded == recount["by_doc"]


def test_the_recount_saw_the_corpus(recount):
    """The TSV must have parsed, or every comparison to it is vacuous."""
    assert recount["spans"] == N_SPANS
    assert recount["by_subtype"] == SUBTYPE_COUNTS
    assert len(recount["by_doc"]) == N_DOCS - N_DOCS_WITH_NO_SPAN


def test_the_document_count_comes_from_two_independent_listings(carmen_docs, phi_dir):
    """1,600, counted twice without the loader: the `.ann` files and the mappings rows.

    Neither route can come from the TSV, because a document with no span has no row
    there. Both are asserted because they are the two things the loader requires to
    agree (`_read`'s `stems != set(labels)` check), and a test that only counted one
    would pass on a release where the other had drifted.

    After the seal this is also the check that the seal moved the *mappings row* with the
    document: `CARMEN1_mappings.tsv` was recomputed per root, and a seal that moved 400
    `.ann` files while leaving 2,000 rows behind would be caught here and nowhere else in
    this file.
    """
    stems = {path.stem for path in phi_dir.glob("*.ann")}
    mappings = phi_dir.parent.parent.parent.parent / MAPPINGS
    with open(mappings, encoding="utf-8", newline="") as fh:
        labelled = {row["filename"] for row in csv.DictReader(fh, delimiter="\t")}

    assert len(stems) == N_DOCS
    assert len(labelled) == N_DOCS
    assert stems == labelled
    assert {doc.doc_id for doc in carmen_docs} == stems


# ─── the counts DESIGN §9.0 reports ─────────────────────────────────────────


def test_document_count(carmen_docs):
    assert len(carmen_docs) == N_DOCS


def test_document_ids_are_unique(carmen_docs):
    ids = [doc.doc_id for doc in carmen_docs]
    assert len(set(ids)) == len(ids)


def test_total_span_count(carmen_docs):
    assert base.count_spans(carmen_docs) == N_SPANS


def test_canonical_span_count(carmen_docs):
    assert base.count_spans(carmen_docs, in_scope_only=True) == N_CANONICAL


def test_the_three_totals_reconcile(carmen_docs):
    """5,934 + 598 = 6,532 visible, 7,473 + 758 = 8,231 corpus-wide.

    The property is that the mapping is exhaustive and no span is dropped silently, and it
    has to hold on both sides: the visible half is measured, and the `FULL_` half is
    arithmetic over pinned numbers that `test_the_split_file_accounts_for_the_seal` ties
    back to the frozen file.
    """
    excluded = sum(1 for doc in carmen_docs for span in doc.spans if span.excluded)
    assert excluded == N_EXCLUDED
    assert N_CANONICAL + N_EXCLUDED == N_SPANS
    assert base.count_spans(carmen_docs, in_scope_only=True) + excluded == N_SPANS
    assert FULL_N_CANONICAL + FULL_N_EXCLUDED == FULL_N_SPANS


def test_canonical_type_counts_match_design_table(carmen_docs):
    assert base.count_by_type(carmen_docs) == CANONICAL_COUNTS


def test_excluded_type_counts_match_design_table(carmen_docs):
    counts = collections.Counter(
        span.subtype for doc in carmen_docs for span in doc.spans if span.excluded
    )
    assert dict(counts) == EXCLUDED_COUNTS


def test_all_ten_canonical_types_have_gold(carmen_docs):
    """§9.0's claim that no other corpus in this project manages.

    Asserted as a set equality against `naming.yaml`'s axis rather than as a count, so
    a canonical type added to the vocabulary makes this fail rather than silently
    leaving the claim describing nine types.

    Asserted on **both** sets, because the claim is a corpus fact that has to survive the
    seal to be usable: the visible corpus is what a rule is developed against, so a
    canonical type the seal emptied would be one no dev-fold work could ever see.
    `CONTACT` is the one at risk — 17 visible of 22 — and it is why this is a separate
    assertion rather than a remark.
    """
    assert set(CANONICAL_COUNTS) == set(base.canonical_types())
    assert set(FULL_CANONICAL_COUNTS) == set(base.canonical_types())
    assert all(count > 0 for count in CANONICAL_COUNTS.values())
    assert all(count > 0 for count in FULL_CANONICAL_COUNTS.values())


def test_the_excluded_share_is_what_section_9_1_prints(carmen_docs):
    """9.21% of gold, and the two components §9.1's table breaks it into.

    §9.1's `es-carmen` row is a published limitation, so the share is pinned here the
    way GraSCCo's 9.68% is. `DIREC_PROT_INTERNET` contributes nothing — that is the
    point of §9.1's note that adding it left the total at 758.

    **The published share is the corpus-wide one**, so it is arithmetic over `FULL_*`.
    The visible share is 9.15% and is pinned beside it rather than instead of it: the two
    are a tenth of a point apart, which is exactly close enough for one to be quoted as
    the other if only one of them existed here.
    """
    assert round(100 * FULL_N_EXCLUDED / FULL_N_SPANS, 2) == 9.21
    assert round(100 * 458 / FULL_N_SPANS, 2) == 5.56
    assert round(100 * 299 / FULL_N_SPANS, 2) == 3.63
    assert round(100 * N_EXCLUDED / N_SPANS, 2) == 9.15
    assert "DIREC_PROT_INTERNET" not in EXCLUDED_COUNTS
    assert "DIREC_PROT_INTERNET" not in FULL_EXCLUDED_COUNTS


def test_the_largest_type_share_is_what_section_5_1_prints(carmen_docs):
    """72.1% of canonical gold is `DATE` — §5.1's concentration figure for this corpus.

    Over the in-scope total, which is the denominator §5.1 states. §5.1 also prints
    **74.3%**, and this is the test that says which is which: 74.3% is the same 5,386
    spans over the 7,246 canonical total that stood before §9.0 placed `NUMERO_IDENTIF`
    and `URL_WEB` on 2026-09-28. The denominator moved, not the corpus. Both are asserted
    so that neither can be quoted as the other, and 65.4% — the share of *all* gold
    including the §9.1 exclusions — is asserted as the third.

    **All three are corpus-wide and all three are now arithmetic over `FULL_*`.** Before
    the seal they were recomputed from the loaded spans, which was the stronger form; that
    form is gone, and what is left is that the published numbers are consistent with the
    pinned table and that the pinned table reconciles with the frozen file.

    What *is* still measured is the visible corpus: `DATE` is still the largest type and
    its visible share is **71.8%**. That number is deliberately not called a §5.1 figure,
    and no visible analogue of 74.3% is computed here — a fourth number of the same shape
    with no published referent would make the quoting hazard worse rather than better. The
    step [4] results reference §5.1's 74.3%, which is this test's `FULL_` line.
    """
    counts = base.count_by_type(carmen_docs)
    largest = max(counts.values())
    assert largest == counts["DATE"]
    assert counts == CANONICAL_COUNTS
    assert round(100 * largest / N_CANONICAL, 1) == 71.8

    full_largest = FULL_CANONICAL_COUNTS["DATE"]
    assert full_largest == max(FULL_CANONICAL_COUNTS.values())
    assert round(100 * full_largest / FULL_N_CANONICAL, 1) == 72.1
    assert round(100 * full_largest / (FULL_N_CANONICAL - 227), 1) == 74.3
    assert round(100 * full_largest / FULL_N_SPANS, 1) == 65.4


def test_patient_name_gold_is_empty_and_clinician_name_gold_is_not(carmen_docs):
    """§9.0/§5.1: the patient-name type is declared and has zero instances.

    An assertion rather than a gap, so it is asserted. `NAME`'s spans are all the
    clinician type — which is what makes patient-name recall *undefined* here rather
    than zero, and makes this corpus a precision-only probe for that role.

    The seal does not weaken this one: emptiness is what is being asserted, and the
    visible corpus is a subset, so 122 of 151 clinician names and zero patient names is
    the same claim over less data. This is the §5.1 record step [4]'s results reference as
    "NAME 부재" — the absence is of the *patient* type, not of `NAME`.
    """
    subtypes = collections.Counter(
        span.subtype
        for doc in carmen_docs
        for span in doc.spans
        if span.phi_type == "NAME"
    )
    assert dict(subtypes) == {"NOMBRE_PERSONAL_SANITARIO": CANONICAL_COUNTS["NAME"]}
    assert FULL_SUBTYPE_COUNTS["NOMBRE_PERSONAL_SANITARIO"] == FULL_CANONICAL_COUNTS["NAME"]
    assert "NOMBRE_SUJETO_ASISTENCIA" not in SUBTYPE_COUNTS
    assert "NOMBRE_SUJETO_ASISTENCIA" not in FULL_SUBTYPE_COUNTS
    assert TYPE_MAP["NOMBRE_SUJETO_ASISTENCIA"] == "NAME"


def test_numero_identif_is_most_of_the_id_row(carmen_docs):
    """§9.0's `NUMERO_IDENTIF` → `ID` decision, with the numbers that argued for it.

    227 of 243 is 93% of the `ID` row and 2.8% of corpus gold, which is what the block
    weighs against excluding the type. `subtype` keeps it recoverable, so the
    role-bearing subset is asserted too — that is the analysis the decision promises
    remains available.

    Both sides, because the decision's arithmetic is corpus-wide and the analysis it
    promises is done on the visible corpus. The ratio survives the seal almost exactly —
    93% of 243 and 93% of 206 — and the role-bearing count falls from 16 to 14, which is
    the number that matters: that subset is small enough that a seal taking most of it
    would have quietly removed the evidence the §9.0 block says remains available.
    """
    ids = [
        span for doc in carmen_docs for span in doc.spans if span.phi_type == "ID"
    ]
    subtypes = collections.Counter(span.subtype for span in ids)
    assert len(ids) == CANONICAL_COUNTS["ID"] == 206
    assert subtypes["NUMERO_IDENTIF"] == 192
    assert round(100 * 192 / 206) == 93
    role_bearing = len(ids) - subtypes["NUMERO_IDENTIF"]
    assert role_bearing == 14

    assert FULL_CANONICAL_COUNTS["ID"] == 243
    assert FULL_SUBTYPE_COUNTS["NUMERO_IDENTIF"] == 227
    assert round(100 * 227 / 243) == 93
    assert round(100 * 227 / FULL_N_SPANS, 1) == 2.8
    assert 243 - 227 == 16


# ─── the schema, and the ten types with no instances ────────────────────────


def test_the_declared_schema_is_exactly_what_the_loader_knows(carmen_unsplit_loader):
    """`annotation.conf` recounted independently, against the three constant sets.

    Parsed here rather than calling `_check_schema`, which would be the loader checking
    itself. The three-way partition is asserted as well as the union: a PHI type that
    drifted into `CONCEPT_TYPES` would keep the union intact while removing the type
    from the gold set entirely.
    """
    path = carmen_unsplit_loader.root / SCHEMA
    declared, in_section = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == SCHEMA_SECTION
            continue
        if in_section and stripped and not stripped.startswith("#"):
            declared.add(stripped)

    assert declared == set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES)
    assert len(set(TYPE_MAP) | set(EXCLUDED_TYPES)) == N_DECLARED_PHI
    assert not set(TYPE_MAP) & set(EXCLUDED_TYPES)
    assert not set(TYPE_MAP) & set(CONCEPT_TYPES)
    assert not set(EXCLUDED_TYPES) & set(CONCEPT_TYPES)


def test_every_declared_type_with_no_instances_is_still_decided(carmen_docs):
    """§9.0: the map is built from the schema, so the ten empty types are mapped anyway.

    The observed set is measured from the corpus and the unused set is the difference,
    which is the direction that matters: a release that started annotating one of the
    ten must fail `test_the_declared_schema...` at load time, not be discovered later
    when a count moves. Nine of the ten map; `DIREC_PROT_INTERNET` is excluded (§9.1).
    """
    observed = {span.subtype for doc in carmen_docs for span in doc.spans}
    assert len(observed) == N_OBSERVED_TYPES

    declared_phi = set(TYPE_MAP) | set(EXCLUDED_TYPES)
    assert observed <= declared_phi
    assert declared_phi - observed == UNUSED_TYPES
    assert UNUSED_TYPES - set(TYPE_MAP) == {"DIREC_PROT_INTERNET"}
    assert "DIREC_PROT_INTERNET" in EXCLUDED_TYPES


def test_the_variant_and_the_layer_are_the_ones_section_9_0_counted():
    """Which of the release's four annotation sets every number above is over.

    Pinned as constants because the alternative is not an error anywhere: the `masked`
    variant has the same layout and loads, and its texts substitute placeholders for the
    identifiers — so a detector evaluated against it would be scored on how well it
    finds the masking convention, and the counts would be 8,230 rather than 8,231. The
    `ner` layer has the same layout again and holds 26,360 medical-concept spans. Both
    substitutions produce a corpus that reads cleanly and answers a different question.
    """
    assert VARIANT == "replaced"
    assert PHI_LAYER == "anon"
    assert CONCEPT_LAYER == "ner"
    assert UNREAD_TEXT_DIR == RELEASE / "txt"


def test_type_map_targets_are_all_canonical():
    assert set(TYPE_MAP.values()) <= set(base.canonical_types())


def test_unknown_annotation_type_raises(carmen_unsplit_loader):
    with pytest.raises(CorpusError, match="neither mapped nor excluded"):
        carmen_unsplit_loader.classify("TIPO_QUE_NO_EXISTE")


def test_a_concept_type_is_not_classified_as_phi(carmen_unsplit_loader):
    """The concept layer's types are known to the schema check and to nothing else.

    `classify()` must refuse them rather than map them: 26,360 concept spans against a
    gold set of 8,231 is not a mapping error that anyone would catch downstream.
    """
    for concept_type in sorted(CONCEPT_TYPES):
        with pytest.raises(CorpusError, match="neither mapped nor excluded"):
            carmen_unsplit_loader.classify(concept_type)


def test_excluded_spans_are_kept_and_carry_no_canonical_type(carmen_docs):
    """§9.1: the exclusion volume is a published figure, so the spans must survive."""
    excluded = [span for doc in carmen_docs for span in doc.spans if span.excluded]
    assert len(excluded) == N_EXCLUDED
    assert all(span.phi_type is None for span in excluded)
    # Three of the four excluded types occur; `DIREC_PROT_INTERNET` has no instances.
    assert {span.subtype for span in excluded} == set(EXCLUDED_COUNTS)


def test_gold_spans_have_empty_provenance(carmen_docs):
    """Gold is not a detection. A layer on a gold span would make it one (DESIGN §3)."""
    for doc in carmen_docs:
        for span in doc.spans:
            assert span.layer is None
            assert span.detector is None
            assert span.rule_id is None
            assert span.agent_actions == []


# ─── offsets, and the one span §9.7 pins ────────────────────────────────────
# **The pinned document is in the test fold.** Until 2026-10-01 the three tests below
# loaded it, read the correction off `meta`, and asserted the span's type and both
# offsets. None of that is reachable now, and the honest replacement is smaller: the pin
# is a table in the loader's source, so the table is asserted directly; the claim that it
# names a sealed document is asserted as fold membership in the frozen file, which is a
# document id and not a surface; and the claim that the visible corpus needs no correction
# is asserted as zero rather than left implied.
#
# What is lost is the one thing a synthetic tree cannot supply — that the pin's offsets
# match the real document. That check ran on 2026-09-28 and again in the freeze commit, and
# it cannot run again without opening the test fold. What is *not* lost is coverage of
# `_apply_known_defects`: five synthetic tests at the end of this file drive every branch
# of it, and they were written that way before the seal was a reason to need them.


def test_every_span_slices_back_to_its_surface(carmen_docs):
    """`assert_offsets()` ran during the load; this asserts it over what came out.

    The comparison is `==` on the slice and the recorded surface, and no branch of it
    prints either. For all 6,532 visible spans the two readings are independent — the
    standoff's surface field was written by the corpus's tooling and the slice comes
    from the `.txt`. Corpus-wide it was 8,230 of 8,231; the one exception is the §9.7 pin,
    and that document is in the test fold, so after the seal there is no span here whose
    surface this loader wrote.
    """
    for doc in carmen_docs:
        for span in doc.spans:
            assert doc.text[span.start : span.end] == span.surface


def test_the_pin_names_exactly_one_span_and_describes_it_as_section_9_7_does():
    """§9.7's table, asserted as a table. One entry, and the shape of that entry.

    Recorded rather than silent for the reason GraSCCo's `bom_clipped_spans` is: a
    correction only the loader's source states is one no result file can be audited
    against. After the seal this is the whole of what can be said about the pin's content,
    and it needs no corpus — which is also why it is asserted by key *and* by value, so a
    pin rewritten to name a different span still fails here.

    The type and both offsets are what §9.7 decided to keep, so they are what is asserted;
    the text at them is not quoted here or anywhere. The length is asserted because it is
    the one thing that makes "a four-character date field" checkable without the
    characters, and it is derived from the offsets rather than from the corpus.
    """
    assert set(KNOWN_SURFACE_DEFECTS) == {(DEFECT_DOC, DEFECT_INDEX)}
    assert KNOWN_SURFACE_DEFECTS[(DEFECT_DOC, DEFECT_INDEX)] == DEFECT_PIN
    subtype, start, end = DEFECT_PIN
    assert TYPE_MAP[subtype] == "DATE"
    assert end - start == 4
    assert start < end


def test_the_pinned_document_went_behind_the_seal(record):
    """The pin names a test-fold document, stated as fold membership in the frozen file.

    A document id and nothing else — ids are not surface forms, which is what lets this be
    asserted at all. Both directions: it is in the sealed fold's list, and it is in neither
    of the other two, because "present in test" and "absent from train" are different
    claims and a split file could satisfy one while breaking the other.
    """
    folds = record["folds"]
    assert DEFECT_DOC in folds[SEALED_FOLD]["document_ids"]
    for fold in VISIBLE_SPLIT:
        assert DEFECT_DOC not in folds[fold]["document_ids"]


def test_no_visible_document_carries_a_corrected_surface(carmen_docs):
    """Zero of 1,600, and asserted as zero rather than left to follow from the test above.

    This is the measurement that makes the two tests above a pair instead of a restatement:
    the pin is in the loader for the whole corpus, so a *second* defect appearing in a
    visible document — a later release, or a pin whose offsets stopped matching — shows up
    here as a correction nobody recorded, and `_apply_known_defects` raises before that:
    an unpinned mismatch is a `CorpusError`, not a tolerance.
    """
    corrected = {
        doc.doc_id: doc.meta["surface_corrected_spans"]
        for doc in carmen_docs
        if "surface_corrected_spans" in doc.meta
    }
    assert corrected == {}
    assert not any(doc.doc_id == DEFECT_DOC for doc in carmen_docs)


def test_no_document_carries_a_bom(carmen_docs):
    """§9.7: 0 of 1,600, and it was 0 of 2,000 when the whole corpus was readable.

    `test_a_bom_shifts_every_offset_in_the_document` below is what keeps that arithmetic
    from being dead code, because this corpus cannot exercise it.
    """
    assert sum(1 for doc in carmen_docs if doc.had_bom) == N_BOM_DOCS
    assert not any(doc.text.startswith("﻿") for doc in carmen_docs)


def test_the_unread_text_copy_is_byte_identical_to_the_one_that_is_read(
    carmen_unsplit_loader, phi_dir
):
    """What licenses reading only the `.txt` beside the `.ann` (1,600 of 1,600).

    The release ships the same texts twice. The loader reads the copy brat pairs with
    the annotations and states this measurement as the reason; if the two copies ever
    diverged, an offset would index one text while a detector was pointed at the other.
    Compared as bytes and never decoded into an assertion.

    After the seal this is also the check that the seal moved **both** copies. The loader
    reads one of them, so a seal that moved `ann/replaced/anon/` and left
    `txt/replaced/` behind would leave 400 test-fold texts in the directory rule
    development reads — a leak of the fold's text with no effect on any count. Here it
    fails as a missing twin, which is why this test is the one that most needed its
    constant updated rather than deleted.
    """
    other = carmen_unsplit_loader.root / UNREAD_TEXT_DIR / VARIANT
    compared = 0
    for path in sorted(phi_dir.glob("*.txt")):
        twin = other / path.name
        assert twin.is_file(), path.name
        assert path.read_bytes() == twin.read_bytes(), path.name
        compared += 1
    assert compared == N_DOCS


# ─── the composition the frozen split was built on (§9.5) ───────────────────
# These were "the strata the frozen split **will** be built on" and were written before
# the freeze on purpose. The freeze happened and then the seal did, so what they measure is
# now the visible composition, and the corpus-wide composition they used to measure is read
# out of the frozen file's `requested` block by the section after next. Both halves are
# still here: a visible recount, and `FULL_*` constants the reconciliation test ties to the
# file. What no longer exists is a route from this file to a corpus-wide recount.


def test_language_labels_are_the_corpus_composition(carmen_docs):
    """es 1,357 / bi 212 / cat 31 visible, and the label is `language_label` and not `lang`.

    The name is load-bearing: `bi` is not a `naming.yaml` language and there is no
    `rules/bi.yaml`, so a document handing this value to a rule path would ask for a
    file that cannot exist (§5.6, §8.4). Asserting the key is asserting that.

    All three labels survive the seal, which step [4] depends on: §5.6 declares one call
    per *declared* language, and `cat` has 31 visible documents of its 39. A seal that had
    emptied `cat` would leave the arm making two calls over a language mix that no longer
    had two languages in it.
    """
    counts = collections.Counter(doc.meta["language_label"] for doc in carmen_docs)
    assert dict(counts) == LANGUAGE_DOCS
    assert set(counts) == set(LANGUAGE_LABELS)
    assert sum(counts.values()) == N_DOCS
    assert set(FULL_LANGUAGE_DOCS) == set(LANGUAGE_LABELS)
    assert sum(FULL_LANGUAGE_DOCS.values()) == FULL_N_DOCS
    assert all("lang" not in doc.meta for doc in carmen_docs)


def test_document_type_tokens_are_the_corpus_composition(carmen_docs):
    """IR 961 / IA 494 / IT 137 / CC 4 / IE 4 visible, under `filename_doctype`.

    Not `document_type`: that axis is derived from text cues by `doctype.py` and
    `es-carmen` declares none, and 789 of these units are clinical sections rather than
    whole notes (§8.5). Two different things with one plausible name is how a
    stratification ends up over the wrong variable.
    """
    counts = collections.Counter(doc.meta["filename_doctype"] for doc in carmen_docs)
    assert dict(counts) == DOCTYPE_DOCS
    assert set(counts) == set(DOCTYPE_TOKENS)
    assert sum(counts.values()) == N_DOCS
    assert set(FULL_DOCTYPE_DOCS) == set(DOCTYPE_TOKENS)
    assert sum(FULL_DOCTYPE_DOCS.values()) == FULL_N_DOCS
    assert all("document_type" not in doc.meta for doc in carmen_docs)


def test_the_unit_is_sometimes_a_section_and_the_count_is_known(carmen_docs):
    """§8.5: 631 visible sections of 789, 9 section tokens, and 969 whole notes.

    The figure §9.5's group decision rests on — a *document*-disjoint split over units
    that are partly sections needs the section-bearing count to be a measured number
    rather than an impression. All nine tokens survive the seal, which is asserted rather
    than assumed: the stratification is over `filename_doctype` and not over the section,
    so nothing in the split was arranged to keep them.
    """
    sections = [doc.meta["filename_section"] for doc in carmen_docs]
    named = [token for token in sections if token is not None]
    assert len(named) == N_SECTION_UNITS
    assert len(set(named)) == N_SECTION_TOKENS
    assert len(sections) - len(named) == N_DOCS - N_SECTION_UNITS
    assert N_SECTION_UNITS < FULL_N_SECTION_UNITS


def test_the_doctype_by_language_cross_tabulation_has_a_single_document_cell(
    carmen_docs,
):
    """The measurement the split's small-cell rule exists for (§9.5), recounted visibly.

    Twelve of the fifteen possible cells are non-empty and the smallest holds **one**
    document, so a proportional three-way split of every cell is not available: at
    0.60/0.20/0.20 a cell needs five documents before each fold can receive one. That was
    asserted over the whole corpus before `splits/es-carmen.json` was frozen, on purpose —
    the deterministic rule is written against those numbers. The corpus-wide cross is now
    read out of the frozen file instead
    (`test_the_requested_composition_is_recorded_beside_the_achieved_one`), and what is
    measured here is the visible cross.

    Both properties happen to survive: still twelve non-empty cells, and `(CC, bi)` is
    still the smallest at one. Neither is asserted as a *consequence* of the seal, because
    neither is — the seal could have emptied a cell, and the reconciliation test below is
    what would then say which one. What the seal did change is `(IE, es)`: 5 corpus-wide
    and 4 visible, so a cell that sat exactly on the threshold is now under it.
    """
    cross = collections.Counter(
        (doc.meta["filename_doctype"], doc.meta["language_label"])
        for doc in carmen_docs
    )
    assert dict(cross) == CROSS
    assert sum(cross.values()) == N_DOCS
    assert len(cross) == 12 == len(FULL_CROSS)
    assert min(cross, key=lambda cell: (cross[cell], cell)) == SMALLEST_CELL
    assert cross[SMALLEST_CELL] == 1
    # And the margins agree with the two one-dimensional tests above, so the three
    # cannot drift into describing different corpora.
    for doctype, total in DOCTYPE_DOCS.items():
        assert sum(n for (dt, _), n in cross.items() if dt == doctype) == total
    for language, total in LANGUAGE_DOCS.items():
        assert sum(n for (_, lang), n in cross.items() if lang == language) == total
    # The cell that crossed the threshold at the seal, named rather than left to be
    # noticed: `IE/es` is the `<` / `<=` boundary case the frozen file records as *not*
    # collapsed, and it would collapse in any rebuild from here.
    assert FULL_CROSS[("IE", "es")] == SMALL_CELL_THRESHOLD
    assert cross[("IE", "es")] < SMALL_CELL_THRESHOLD
    # No visible cell is larger than its corpus-wide self, which is the one thing a seal
    # cannot legitimately do and the cheapest check that the two tables are of one corpus.
    assert set(cross) <= set(FULL_CROSS)
    assert all(n <= FULL_CROSS[cell] for cell, n in cross.items())


def test_sparsity_has_two_readings_and_both_are_measured(carmen_docs):
    """381 visible documents with no span, 388 with no *in-scope* span.

    The difference is the distinction `sparsity_counts_excluded_spans` exists to protect:
    a note whose only span is §9.1-excluded carries no gold, and counting it as though it
    did is what put 727 into `splits/ko-surro.json`. Seven such documents are visible and
    nine exist, so the gap is *narrower* after the seal and still non-zero — which is the
    only thing that keeps the distinction exercised. A seal that had taken all nine would
    have left this test asserting a difference of zero and passing.
    """
    assert sum(1 for doc in carmen_docs if not doc.spans) == N_DOCS_WITH_NO_SPAN
    assert (
        sum(1 for doc in carmen_docs if not doc.in_scope_spans)
        == N_DOCS_WITH_NO_IN_SCOPE_SPAN
    )
    assert N_DOCS_WITH_NO_IN_SCOPE_SPAN > N_DOCS_WITH_NO_SPAN
    assert FULL_N_DOCS_WITH_NO_IN_SCOPE_SPAN > FULL_N_DOCS_WITH_NO_SPAN
    visible_gap = N_DOCS_WITH_NO_IN_SCOPE_SPAN - N_DOCS_WITH_NO_SPAN
    full_gap = FULL_N_DOCS_WITH_NO_IN_SCOPE_SPAN - FULL_N_DOCS_WITH_NO_SPAN
    assert (visible_gap, full_gap) == (7, 9)


def test_the_concept_layer_flag_describes_the_directory(carmen_docs, phi_dir):
    """394 flagged, and the same 394 files under `ner/` — recounted here.

    This is the one claim `CARMEN1_mappings.tsv` makes that is checkable against the
    tree, which makes it the evidence that the file describes *this* release rather
    than another one. The language label in the same rows is what the frozen split
    stratifies on, so that evidence is not incidental.

    After the seal it is evidence of something more specific: **both** the mappings file
    and the `ner/` directory were rewritten per root, by two different parts of
    `tools/prepare_carmen.py` — the mappings by row filtering and `ner/` by moving files —
    and this is the only test that requires the two to still agree. 394 of 500, so 106
    concept-layer documents went behind the seal with the PHI layer they pair with.
    """
    flagged = {doc.doc_id for doc in carmen_docs if doc.meta["has_concept_layer"]}
    present = {
        path.stem
        for path in (phi_dir.parent / CONCEPT_LAYER).glob("*.ann")
    }
    assert len(flagged) == N_CONCEPT_FLAGGED
    assert flagged == present


def test_the_digest_covers_the_language_label(carmen_docs, carmen_unsplit_loader):
    """The split's stratum is hashed, not just the two files a detector would read.

    A release that reshuffled `CARMEN1_mappings.tsv` would leave every `.ann` and `.txt`
    untouched, so the default digest would verify while the composition recorded in the
    split file became false. Asserted by naming the part, not by hashing twice: the
    payload is a canonical `key=value` rendering so that what is pinned is the two
    values the split reads and not the file's column order.
    """
    doc = carmen_docs[0]
    parts = dict(carmen_unsplit_loader.digest_parts(doc))
    key = f"{MAPPINGS.name}:{doc.doc_id}"
    assert set(parts) == {f"{doc.doc_id}.ann", f"{doc.doc_id}.txt", key}
    assert parts[key] == (
        f"language={doc.meta['language_label']}\n"
        f"concept_layer={doc.meta['has_concept_layer']}\n"
    ).encode("utf-8")


def test_source_files_returns_the_pair_and_nothing_else(carmen_docs, carmen_unsplit_loader):
    doc = carmen_docs[0]
    paths = carmen_unsplit_loader.source_files(doc.doc_id)
    assert [path.name for path in paths] == [f"{doc.doc_id}.ann", f"{doc.doc_id}.txt"]


def test_an_unknown_doc_id_has_no_files(carmen_unsplit_loader):
    with pytest.raises(CorpusError, match="no files for doc_id"):
        carmen_unsplit_loader.source_files("CARMEN-I_IR_999999")


# ─── the split file is the only authority on the fold ───────────────────────


def test_the_layout_encodes_no_fold(carmen_docs):
    """Nothing in the tree says which fold a document is in, and no key pretends to."""
    assert CarmenLoader.fold_dirs == {}
    assert {doc.split for doc in carmen_docs} == {None}


def test_calling_fold_roots_is_refused():
    """A corpus with no fold directories must not answer a question about them.

    Returning `{}` would make a fold-directory read iterate over nothing and load an
    empty corpus, which is the failure that looks like a clean run.
    """
    with pytest.raises(CorpusError, match="does not encode the fold"):
        CarmenLoader(use_split_file=False).fold_roots()


def test_there_is_no_patient_key(carmen_docs, carmen_unsplit_loader):
    """§8.5: no patient, encounter or record identifier exists in the release.

    So §9.5's first branch does not apply and the group is the document. Asserted as a
    refusal rather than as a `None`: a `patient_key()` that returned something would let
    a caller build patient-disjoint groups out of a value that is not a patient.
    """
    assert CarmenLoader.has_patient_key is False
    assert not CarmenLoader.patient_key_source
    with pytest.raises(CorpusError):
        carmen_unsplit_loader.patient_key(carmen_docs[0])


# ─── the frozen split file and the seal (DESIGN §9.5, §6.1, §6.2) ────────────
# Added 2026-09-30 with the freeze itself, and completed 2026-10-01 with the seal. What
# stood here before the freeze was `test_the_split_file_is_not_frozen_yet`, a test whose
# whole job was to fail on the freeze commit and name the work it owed: "a `carmen_loader`
# fixture, a `test_only_the_unsealed_folds_load`, and corpus-wide figures read from the
# file instead of recounted". Two of the three arrived with the freeze; the third could
# not, because `test_only_the_unsealed_folds_load` had no state to assert while every fold
# was visible, and it arrives here. The same sequence `kosurro_loader` (freeze, 2026-09-23)
# and `kosurro_sealed` (seal, 2026-09-28) went through from the other side.
#
# `test_nothing_is_sealed_yet` is what stood where `test_the_test_fold_is_sealed` is now.
# It failed on the seal commit, which was its whole job, and it named the same three
# things this section delivers.


@pytest.fixture(scope="module")
def record(carmen_present):
    """The frozen split file, parsed. No `try`: a file that does not parse is a defect."""
    return split.read(carmen_present)


def test_the_split_route_is_declared():
    assert split.SPLIT_ORIGIN[CARMEN] == "constructed"
    assert CARMEN in split.CONSTRUCTED_NARRATIVE


def test_fold_sizes_are_the_constructed_split(record):
    """All three folds, from the file. The file is the only thing that still knows `test`."""
    assert {f: b["n_documents"] for f, b in record["folds"].items()} == CONSTRUCTED_SPLIT
    assert sum(CONSTRUCTED_SPLIT.values()) == FULL_N_DOCS
    assert set(CONSTRUCTED_SPLIT) - set(VISIBLE_SPLIT) == {SEALED_FOLD}
    assert sum(VISIBLE_SPLIT.values()) == N_DOCS
    assert N_DOCS + CONSTRUCTED_SPLIT[SEALED_FOLD] == FULL_N_DOCS


def test_the_loader_gets_its_folds_from_the_split_file(carmen_loader):
    """The file's claim carried out, which the unsplit loader cannot show.

    `carmen_loader` reads `splits/es-carmen.json`; `carmen_unsplit_loader` does not. The
    layout encodes no fold (`fold_dirs == {}`), so a fold on a document is the split
    file's assignment and nothing else's, and the per-fold counts have to come back out
    the same way they went in — for the folds that are still on disk.
    """
    by_fold = collections.Counter(doc.split for doc in carmen_loader.load())
    assert dict(by_fold) == VISIBLE_SPLIT


def test_the_test_fold_is_sealed(carmen_sealed):
    """What `test_nothing_is_sealed_yet` was waiting to become (DESIGN §6.1).

    Nothing is opened: `sealed_root()` answers from the configured path, and the assertion
    is that the path is declared and is not the corpus root. The second half is the one
    worth stating — a `sealed:` entry pointing at the corpus root would satisfy "is not
    None" while sealing nothing, and this corpus is the first where the sealed root is a
    second *root* rather than a rewritten file, so the two paths are the whole mechanism.
    """
    sealed = base.sealed_root(carmen_sealed)
    assert sealed is not None
    assert sealed != base.corpus_root(carmen_sealed)
    assert sealed.name == carmen_sealed


def test_only_the_unsealed_folds_load(carmen_sealed, carmen_loader, carmen_docs):
    """The third owed item. `test` is **absent**, not present at zero.

    Three statements, because they fail differently. The loader that reads the split file
    returns two folds; the loader that does not read it returns the same documents with no
    fold at all, which is what every recount above is over; and no document id the frozen
    file assigns to `test` is reachable by either. The last one is the seal itself — the
    first two would both hold on a corpus whose test documents were still on disk and
    merely unlabelled.
    """
    by_fold = collections.Counter(doc.split for doc in carmen_loader.load())
    assert dict(by_fold) == VISIBLE_SPLIT
    assert SEALED_FOLD not in by_fold

    assert len(carmen_docs) == N_DOCS
    assert {doc.split for doc in carmen_docs} == {None}

    sealed_ids = set(split.read(carmen_sealed)["folds"][SEALED_FOLD]["document_ids"])
    assert len(sealed_ids) == CONSTRUCTED_SPLIT[SEALED_FOLD]
    assert not sealed_ids & {doc.doc_id for doc in carmen_docs}


def test_the_split_file_accounts_for_the_seal(carmen_sealed, record, carmen_docs):
    """Visible recount + the file's sealed block = the corpus-wide figures, per type.

    **This is the only thing keeping the `FULL_*` constants falsifiable from here.** Before
    the seal they were recounted directly; now the sealed half is *pinned* by the frozen
    file, so what is checked is an identity between three things — what the loader reaches,
    what the file says went behind the seal, and what §9.0 publishes. Any one of the three
    drifting breaks it, which is weaker than a recount and is what is left.

    Per type as well as in aggregate, in both directions. The aggregate alone would pass on
    a seal that took the right number of spans of the wrong types, and the loop alone would
    pass while the sealed block carried a type the corpus-wide table does not have — hence
    the two containment assertions after it. The same shape as
    `tests/test_kosurro_loader.py`'s test of the same name, deliberately: the two corpora
    were sealed three days apart by different mechanisms (a file rewrite there, a second
    root here) and the check that the split file still describes the whole corpus should not
    depend on which.
    """
    sealed = record["folds"][SEALED_FOLD]
    spans = [span for doc in carmen_docs for span in doc.spans]
    in_scope = [span for span in spans if not span.excluded]
    excluded = [span for span in spans if span.excluded]

    assert len(carmen_docs) + sealed["n_documents"] == FULL_N_DOCS
    assert len(spans) + sealed["n_spans"] == FULL_N_SPANS
    assert len(in_scope) + sealed["n_spans_in_scope"] == FULL_N_CANONICAL
    assert len(excluded) + sealed["n_spans_excluded"] == FULL_N_EXCLUDED

    # The file's own totals are the corpus-wide table, so the published constants and the
    # frozen record are two statements of one thing and cannot drift apart silently.
    totals = record["totals"]
    assert totals["n_documents"] == FULL_N_DOCS
    assert totals["n_spans"] == FULL_N_SPANS
    assert totals["n_spans_in_scope"] == FULL_N_CANONICAL
    assert totals["n_spans_excluded"] == FULL_N_EXCLUDED
    assert totals["spans_by_phi_type"] == FULL_CANONICAL_COUNTS
    assert totals["spans_by_excluded_type"] == FULL_EXCLUDED_COUNTS

    visible_canonical = base.count_by_type(carmen_docs)
    for phi_type, total in FULL_CANONICAL_COUNTS.items():
        assert (
            visible_canonical.get(phi_type, 0)
            + sealed["spans_by_phi_type"].get(phi_type, 0)
            == total
        ), phi_type
    visible_excluded = collections.Counter(span.subtype for span in excluded)
    for subtype, total in FULL_EXCLUDED_COUNTS.items():
        assert (
            visible_excluded.get(subtype, 0)
            + sealed["spans_by_excluded_type"].get(subtype, 0)
            == total
        ), subtype
    # Neither side may carry a type the corpus-wide table does not, which the loops above
    # cannot see: a `get(..., 0)` over the published keys never looks at the extra one.
    assert set(sealed["spans_by_phi_type"]) <= set(FULL_CANONICAL_COUNTS)
    assert set(sealed["spans_by_excluded_type"]) <= set(FULL_EXCLUDED_COUNTS)
    assert set(visible_canonical) <= set(FULL_CANONICAL_COUNTS)
    assert set(visible_excluded) <= set(FULL_EXCLUDED_COUNTS)
    # `URL_WEB` is the one excluded type with a single instance corpus-wide, and it is
    # visible. Named because it is the only type whose presence on one side of the seal is
    # decided by one document, so it is where an off-by-one in the seal would show.
    assert sealed["spans_by_excluded_type"].get("URL_WEB", 0) == 0
    assert visible_excluded["URL_WEB"] == FULL_EXCLUDED_COUNTS["URL_WEB"] == 1


def test_the_seed_is_recorded_with_the_split(record):
    assert record["provenance"]["seed"] == split.construction_params(CARMEN)["seed"]


# ─── what was asked for, beside what was delivered (§9.5 item 5) ──────────────
# The tests below are the killers for the three mutations that 2026-09-29 deliberately
# deferred to this commit (`tests/mutations/README.md`, "Deferred with a reason, not
# exempt"). They had no killer then because `splits/es-carmen.json` did not exist; the
# mutations and these tests arrive together, which is the condition that deferral set.
#
# **Each one has two halves, and it needs both.** The *constant* half reads the frozen
# file and pins the numbers §9.5 was written against. The *recount* half re-runs the
# generator and compares. The constant half alone cannot be a killer and the first draft of
# these tests found that out the hard way: a mutation to `_achieved` does not touch a file
# that is already on disk, so all three survived a suite that read only the record. What
# reaches the generator is running it, which is the same shape
# `test_grouping_audit_is_reproducible_from_the_corpus` has for the audit.
#
# **The route to the generator changed at the seal, one day after these tests landed.** The
# `rebuilt` fixture called `split.build`, and `_build_constructed` refuses once a sealed
# root is declared — a split constructed then would cover 1,600 of 2,000 documents (DESIGN
# §6.2). That refusal is correct and is itself asserted
# (`test_a_rebuild_of_the_split_is_refused_after_the_seal`), so the recount halves now call
# `split._achieved` directly over the visible documents with the frozen file's fold
# assignment. That still executes the mutated code, which is what makes them killers; what
# it no longer does is re-derive the corpus-wide composition, and the sections below say at
# each assertion which of the two it is.


@pytest.fixture(scope="module")
def recorded(record):
    """The stratification block as frozen. The constant half's subject."""
    return record["provenance"]["stratification"]


@pytest.fixture(scope="module")
def rebuilt(carmen_docs, record, recorded):
    """`split._achieved` re-run over the **visible** documents. The recount half's subject.

    Three of its five inputs are rebuilt from the corpus and two are taken from the frozen
    file, and which is which is the whole character of this fixture:

      - `units` is one singleton per visible document. §9.5's group for this corpus is the
        document, which `test_the_unit_is_one_document` asserts against the file's own
        `n_groups` rather than leaving it as an assumption of this construction.
      - `keys` comes from `split.stratum_keys` over the visible documents, so the cross
        this fixture reports is measured and not copied.
      - `fold_of_doc` comes from the frozen file. Re-running `assign_folds` would be the
        wrong move twice over: it would be a second opinion about membership rather than a
        check of what was recorded, and after the seal it has 1,600 documents to deal into
        three folds.
      - `strata_map` is built with the frozen file's `collapsed_primaries`, **not** with a
        fresh `collapse_small_cells`. This is the one place the seal forces a choice: `IE/es`
        held exactly five documents corpus-wide and holds four now, so a fresh collapse
        would fold `IE` as well and produce a stratum named `IE/*` that the frozen file does
        not have. Supplying the decision keeps the comparison about what `_achieved`
        *records*, which is where all three mutations live; the decision itself is
        `collapse_small_cells`'s and is tested with that function.

        Supplying it does not *suppress* it. `_achieved` re-derives `collapsed_primaries`
        and `cells_below_threshold` from the cells it measures and reports `["CC", "IE"]`
        beside the eleven strata it was handed, so the block it returns is internally
        inconsistent. That is asserted rather than worked around — it is the sharpest
        statement available of why a sealed checkout cannot rebuild this corpus's strata.

    So `by_stratum` and `label_mix` for `dev` and `train` are expected to come out
    byte-identical to the frozen file's — every document of those folds is visible — while
    `requested` describes the visible corpus and `proportion_of_documents` is over 1,600.
    Both differences are asserted explicitly below rather than being excluded from a
    comparison.
    """
    params = split.construction_params(CARMEN)
    units = [(doc.doc_id,) for doc in carmen_docs]
    keys = split.stratum_keys(params["stratify_by"], carmen_docs)
    fold_of_doc = split.fold_of(record)

    collapsed = set(recorded["requested"]["collapsed_primaries"])
    grouped: dict[str, list] = {name: [] for name in recorded["requested"]["strata"]}
    for unit in units:
        primary, secondary = keys[unit[0]]
        name = f"{primary}/*" if primary in collapsed else f"{primary}/{secondary}"
        grouped[name].append(unit)

    by_fold: dict[str, list] = {}
    for doc in carmen_docs:
        by_fold.setdefault(fold_of_doc[doc.doc_id], []).append(doc)

    return split._achieved(
        by_fold, units, fold_of_doc, params, strata_map=grouped, keys=keys
    )


def test_a_rebuild_of_the_split_is_refused_after_the_seal(carmen_sealed):
    """What the `rebuilt` fixture used to do, now a refusal (DESIGN §6.2).

    Asserted rather than left as the reason a fixture was rewritten. The refusal is the
    guard that stops a split from being constructed over 1,600 documents and committed as
    the file that defines the seal — the generate → freeze → seal order run backwards — and
    it is now the only route by which that guard is exercised on a real corpus.
    """
    with pytest.raises(CorpusError, match="already declares a sealed"):
        split.build(carmen_sealed)


def test_the_unit_is_one_document(record):
    """The assumption `rebuilt` builds its `units` on, checked against the frozen file.

    §8.5's units are partly clinical sections, so "one unit per document" is a claim about
    the *grouping* and not a tautology: §9.5's first branch does not apply here (there is no
    patient key), and the second put every document in its own group. If that ever stopped
    being true, `rebuilt`'s singletons would silently disagree with the file it is compared
    against, and every assertion below would be about two different partitions.
    """
    assert record["group_key"]["n_groups"] == FULL_N_DOCS
    for fold, block in record["provenance"]["stratification"]["achieved"].items():
        assert block["n_units"] == block["n_documents"] == CONSTRUCTED_SPLIT[fold]


def test_the_stratification_block_is_reproducible_from_the_corpus(recorded, rebuilt):
    """The frozen block, re-derived for the two visible folds. The broad statement.

    Equality over each visible fold's whole sub-block rather than over the fields the next
    three name, so a field added later arrives compared — the recorded composition is the
    file's only account of what the stratification did.

    Two fields are excluded and both are named, because "excluded from a comparison" is how
    a difference stops being read:

      - `proportion_of_documents` is a share of the corpus the rebuild can see, so it is
        0.25/0.75 here against 0.2/0.6 in the file. Asserted as those four numbers rather
        than skipped, which makes the denominator visible instead of inferred.
      - `test` is absent from the rebuild entirely. Asserted as absence.

    Everything else — `n_units`, `n_documents`, `n_spans_in_scope`, the density, the whole
    `by_stratum` table and the whole `label_mix` — must match exactly, because each of those
    is computed from the fold's own documents and every document of `dev` and `train` is
    visible. That is what makes this a recount and not a weaker restatement of the file.

    The first two assertions are the breadth the docstring claims: the rebuild's *keys* must
    be the frozen block's keys, which is what makes this the test that fails for all three
    mutations below rather than only for the two about `achieved`.
    """
    assert set(rebuilt) == set(recorded)
    assert set(rebuilt) >= {"requested", "achieved"}
    assert set(rebuilt["achieved"]) == set(VISIBLE_SPLIT)
    assert SEALED_FOLD not in rebuilt["achieved"]
    assert rebuilt["variable"] == recorded["variable"] == CARMEN_VARIABLE
    assert rebuilt["n_strata"] == recorded["n_strata"] == N_STRATA
    assert rebuilt["target_proportions"] == recorded["target_proportions"]

    moved = "proportion_of_documents"
    for fold in VISIBLE_SPLIT:
        mine, theirs = rebuilt["achieved"][fold], recorded["achieved"][fold]
        assert set(mine) == set(theirs)
        assert {k: v for k, v in mine.items() if k != moved} == {
            k: v for k, v in theirs.items() if k != moved
        }, fold
    assert {f: rebuilt["achieved"][f][moved] for f in VISIBLE_SPLIT} == {
        "dev": 0.25,
        "train": 0.75,
    }
    assert {f: recorded["achieved"][f][moved] for f in VISIBLE_SPLIT} == {
        "dev": 0.2,
        "train": 0.6,
    }


def test_the_requested_composition_is_recorded_beside_the_achieved_one(recorded, rebuilt):
    """Both blocks, or a reader cannot tell 11 strata from 12 cells.

    §9.5 item 5 is the requirement and `_achieved`'s docstring states the reason: "One
    block without the other is what makes a difference between the two invisible." The
    `achieved` half alone would show eleven well-balanced strata and say nothing about
    the cell of one document that had to be folded into another to get there.

    The constant half below is corpus-wide and comes out of the frozen file: twelve cells,
    the derived threshold, the two cells under it, `CC` as the collapsed primary, and eleven
    strata with `IE/es` sitting exactly on the boundary. **None of that is recountable from
    this machine any more** — it describes 2,000 documents — which is why the recount half
    is now about the block's *shape* over the visible corpus rather than its values.
    """
    assert recorded["variable"] == CARMEN_VARIABLE
    assert recorded["n_strata"] == N_STRATA
    assert set(recorded) >= {"requested", "achieved"}
    requested = recorded["requested"]
    assert requested["labels"] == {
        "primary": "filename_doctype",
        "secondary": "language_label",
    }
    # The cells *before* the collapse: twelve, and the smallest holds one document.
    assert {
        tuple(name.split("/")): n for name, n in requested["cells"].items()
    } == FULL_CROSS
    assert requested["n_cells_non_empty"] == len(FULL_CROSS) == 12
    assert requested["small_cell_threshold"] == SMALL_CELL_THRESHOLD
    assert requested["cells_below_threshold"] == {"CC/bi": 1, "CC/es": 4}
    assert requested["collapsed_primaries"] == CARMEN_COLLAPSED
    # And the strata after it: eleven, none of them still short.
    assert set(requested["strata"]) == set(STRATA)
    assert requested["strata"] == STRATA
    assert len(requested["strata"]) == N_STRATA
    assert requested["strata_still_below_threshold"] == {}
    # `IE/es` is exactly at the threshold and did not collapse — the boundary the rule
    # is `<` and not `<=`, asserted on the file rather than only on the function.
    assert requested["strata"]["IE/es"] == SMALL_CELL_THRESHOLD
    assert "IE" not in requested["collapsed_primaries"]

    # The recount half, and what it reaches is the generator rather than the file: a
    # `requested` block that is computed and thrown away is absent from this rebuild, which
    # is the form the mutation takes. So the first assertion is the key's presence.
    assert "requested" in rebuilt
    mine = rebuilt["requested"]
    # Everything that is a property of the block rather than of the corpus must agree
    # exactly: the labels crossed, the derived threshold, and its formula.
    assert mine["labels"] == requested["labels"]
    assert mine["small_cell_threshold"] == requested["small_cell_threshold"]
    assert mine["threshold_formula"] == requested["threshold_formula"]
    # The collapse decision does **not** agree, and that is the finding rather than a
    # tolerance. `_achieved` re-derives `collapsed_primaries` and `cells_below_threshold`
    # from the cells it measures — it does not read them off the `strata_map` it is handed —
    # so the rebuild collapses `IE` as well as `CC`. One `requested` block therefore reports
    # a collapse of two primaries beside eleven strata named for a collapse of one, which is
    # self-inconsistent and is exactly why the frozen file is the authority on this corpus's
    # strata and a rebuild from a sealed checkout is not.
    assert mine["collapsed_primaries"] == ["CC", "IE"]
    assert requested["collapsed_primaries"] == CARMEN_COLLAPSED
    assert mine["cells_below_threshold"] == {"CC/bi": 1, "CC/es": 3, "IE/es": 4}
    # And the cells are measured, so they are the *visible* cross — twelve again, and
    # smaller cell by cell. A rebuild that reported the frozen numbers here would mean the
    # block was copied rather than computed.
    assert {tuple(name.split("/")): n for name, n in mine["cells"].items()} == CROSS
    assert mine["n_cells_non_empty"] == 12
    assert sum(mine["cells"].values()) == N_DOCS
    assert sum(requested["cells"].values()) == FULL_N_DOCS
    # The eleven strata keep the frozen file's names, because this fixture supplied them,
    # and carry the visible sizes. Two of them are now under the threshold: `CC/*` lost one
    # of its five and `IE/es` lost one of its five, so the file's
    # `strata_still_below_threshold == {}` and this rebuild's two-entry table are both
    # correct about different corpora. That is the clearest single statement of what the
    # seal costs §9.5's guarantee, and it is asserted rather than remarked on.
    assert mine["strata"] == VISIBLE_STRATA
    assert set(mine["strata"]) == set(STRATA)
    assert sum(VISIBLE_STRATA.values()) == N_DOCS
    assert mine["strata_still_below_threshold"] == {"CC/*": 4, "IE/es": 4}
    assert requested["strata_still_below_threshold"] == {}


def test_each_fold_reports_its_own_share_of_every_stratum(recorded, rebuilt, carmen_docs):
    """`by_stratum` is the fold's share, not the stratum's size.

    The failure this pins is one where all three folds report the same table — which
    reads as perfect balance and reconciles with nothing. So the assertions are
    arithmetic: each fold's shares sum to that fold's document count, the three folds
    sum to each stratum's size, and the collapsed `CC/*` stratum's five documents come
    apart 3/1/1 rather than appearing five times.

    The recount half survives the seal intact, and it is the one that does. `by_stratum` for
    a fold is computed from that fold's own documents, and every document of `dev` and
    `train` is visible, so the rebuild's tables for those two must be **equal** to the
    frozen file's — not reconcilable with them. The sealed fold's table is then pinned, and
    the third block of assertions closes the loop: visible share + sealed share = the
    corpus-wide stratum size, for all eleven.
    """
    achieved = recorded["achieved"]
    assert set(achieved) == set(CONSTRUCTED_SPLIT)
    for fold, block in achieved.items():
        assert set(block["by_stratum"]) == set(STRATA)
        assert sum(block["by_stratum"].values()) == CONSTRUCTED_SPLIT[fold]
        assert block["n_documents"] == CONSTRUCTED_SPLIT[fold]
        assert block["n_units"] == CONSTRUCTED_SPLIT[fold]  # one unit per document
    for name, size in STRATA.items():
        assert sum(b["by_stratum"][name] for b in achieved.values()) == size
    assert {f: b["by_stratum"]["CC/*"] for f, b in achieved.items()} == CC_SHARE
    assert sum(CC_SHARE.values()) == STRATA["CC/*"]

    # The recount half. Reading the frozen file cannot tell the fold's share from the
    # stratum's size — both reconcile with *something* — so the table is re-derived. The
    # substitution that reports `strata_map` sizes instead gives every fold 769 for
    # `IR/es` here, which the arithmetic above would catch only after the rebuild reaches it.
    for fold in VISIBLE_SPLIT:
        assert rebuilt["achieved"][fold]["by_stratum"] == achieved[fold]["by_stratum"], fold
    # And it is not a vacuous equality: the two visible folds disagree with each other on
    # every stratum large enough to be dealt unevenly, so a mutation that made all folds
    # alike cannot pass the line above.
    assert (
        rebuilt["achieved"]["dev"]["by_stratum"]
        != rebuilt["achieved"]["train"]["by_stratum"]
    )

    # The loop the seal closes: visible + sealed = the corpus-wide stratum size. This is the
    # per-stratum form of `test_the_split_file_accounts_for_the_seal`, and the only place the
    # sealed fold's `by_stratum` is checked against anything.
    for name, size in STRATA.items():
        visible = sum(rebuilt["achieved"][f]["by_stratum"][name] for f in VISIBLE_SPLIT)
        assert visible == VISIBLE_STRATA[name], name
        assert visible + achieved[SEALED_FOLD]["by_stratum"][name] == size, name
    assert achieved[SEALED_FOLD]["by_stratum"]["CC/*"] == CC_SHARE[SEALED_FOLD] == 1


def test_the_label_mix_records_both_labels(recorded, rebuilt):
    """Both margins per fold, because the language is the confound being controlled.

    §9.5's reason for crossing at all is that 84% of bilingual documents are one
    document type, so a file recording the document-type mix and an empty language mix
    would omit exactly the half the stratification exists for — and record it as
    measured-and-none-found rather than as absent.

    This is the killer the seal leaves strongest. `label_mix` does not depend on the strata
    at all — it counts each fold's own documents by each label — so the rebuild's tables for
    `dev` and `train` are equal to the frozen file's with no allowance of any kind, and the
    margins close against `FULL_*` once the sealed fold's mix is added.
    """
    achieved = recorded["achieved"]
    for fold, block in achieved.items():
        mix = block["label_mix"]
        assert set(mix) == {"filename_doctype", "language_label"}
        for label, counts in mix.items():
            assert counts, f"{fold}/{label} is empty"
            assert sum(counts.values()) == CONSTRUCTED_SPLIT[fold]
    # The margins recount to the corpus-wide compositions, both dimensions.
    for label, expected in (
        ("filename_doctype", FULL_DOCTYPE_DOCS),
        ("language_label", FULL_LANGUAGE_DOCS),
    ):
        total: collections.Counter = collections.Counter()
        for block in achieved.values():
            total.update(block["label_mix"][label])
        assert dict(total) == expected
    # The recount half. `language_label` present-and-empty is the failure this names, and
    # the frozen file cannot distinguish "measured, none found" from "never measured" —
    # only re-deriving it can, because the mutation that counts one label leaves the key.
    for fold in VISIBLE_SPLIT:
        assert rebuilt["achieved"][fold]["label_mix"] == achieved[fold]["label_mix"], fold
        assert set(rebuilt["achieved"][fold]["label_mix"]) == {
            "filename_doctype",
            "language_label",
        }
        assert rebuilt["achieved"][fold]["label_mix"]["language_label"]
    # And the visible margins are the visible compositions, which ties this block to
    # `test_language_labels_are_the_corpus_composition` rather than leaving two independent
    # recounts of the same thing that could disagree without anything failing.
    for label, expected in (
        ("filename_doctype", DOCTYPE_DOCS),
        ("language_label", LANGUAGE_DOCS),
    ):
        total = collections.Counter()
        for fold in VISIBLE_SPLIT:
            total.update(rebuilt["achieved"][fold]["label_mix"][label])
        assert dict(total) == expected


# ─── failure modes, on synthetic trees ──────────────────────────────────────
# Written into `tmp_path`, never into the corpus, with invented Spanish text. That is
# what lets these tests run on a machine with no CARMEN-I checkout, and it is also what
# makes the "no surface in the message" tests below meaningful rather than accidental:
# the tokens they look for exist only in this file.

SYNTHETIC_TEXT = "Nota inventada: fecha 01/01/2000 y edad 40 anos.\n"
DATE = "01/01/2000"
DATE_START = SYNTHETIC_TEXT.index(DATE)
DATE_END = DATE_START + len(DATE)


def ann_line(tag_id, tag, start, end, surface):
    return f"{tag_id}\t{tag} {start} {end}\t{surface}"


def date_line(tag_id="T1", tag="FECHAS", start=DATE_START, end=DATE_END, surface=None):
    """One well-formed annotation over the synthetic date, or a variant of it."""
    if surface is None:
        surface = SYNTHETIC_TEXT[start:end]
    return ann_line(tag_id, tag, start, end, surface)


def schema_text(types=None):
    """An `annotation.conf` declaring exactly the types this loader knows.

    Generated from the loader's own constants, which is fine here and would not be in
    `test_the_declared_schema_is_exactly_what_the_loader_knows`: the subject of these
    trees is some other behaviour, and a hand-written copy of 35 type names would fail
    for a reason that has nothing to do with the test it is in.
    """
    if types is None:
        types = set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES)
    body = "\n".join(sorted(types))
    return f"# synthetic\n{SCHEMA_SECTION}\n{body}\n\n[relations]\n"


def write_root(
    path,
    docs=(("CARMEN-I_IR_1", "es", False, None),),
    *,
    types=None,
    text=SYNTHETIC_TEXT,
    mappings_rows=None,
    concept_stems=None,
    fields=MAPPINGS_FIELDS,
):
    """A synthetic release holding `docs`, in the layout the loader expects.

    `docs` is a sequence of `(doc_id, language, has_concepts, lines)`, where `lines` is
    `None` for the one well-formed date annotation. `mappings_rows`, `fields`, `types`
    and `concept_stems` override what the release *declares*, which is how the tests
    below make the declarations disagree with the tree.
    """
    phi = path / ANN_ROOT / VARIANT / PHI_LAYER
    concept = path / ANN_ROOT / VARIANT / CONCEPT_LAYER
    phi.mkdir(parents=True, exist_ok=True)
    concept.mkdir(parents=True, exist_ok=True)
    (path / SCHEMA).write_text(schema_text(types), encoding="utf-8")

    for doc_id, _language, _flag, lines in docs:
        (phi / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        body = [date_line()] if lines is None else list(lines)
        (phi / f"{doc_id}.ann").write_text(
            "".join(line + "\n" for line in body), encoding="utf-8"
        )

    if concept_stems is None:
        concept_stems = [doc_id for doc_id, _, flag, _ in docs if flag]
    for stem in concept_stems:
        (concept / f"{stem}.ann").write_text("", encoding="utf-8")
        (concept / f"{stem}.txt").write_text(text, encoding="utf-8")

    if mappings_rows is None:
        mappings_rows = [
            (doc_id, language, str(flag)) for doc_id, language, flag, _ in docs
        ]
    rows = ["\t".join(fields)] + ["\t".join(row) for row in mappings_rows]
    (path / MAPPINGS).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )
    return path


def loader_on(path):
    """A loader reading one synthetic root and no split file."""
    return CarmenLoader(root=path, use_split_file=False)


def test_a_synthetic_document_loads(tmp_path):
    """The positive control: without it every failure test below could pass vacuously."""
    docs = loader_on(write_root(tmp_path / "root")).load()
    assert len(docs) == 1
    assert docs[0].doc_id == "CARMEN-I_IR_1"
    assert len(docs[0].spans) == 1
    assert docs[0].spans[0].phi_type == "DATE"
    assert docs[0].meta["language_label"] == "es"
    assert docs[0].meta["filename_doctype"] == "IR"
    assert docs[0].meta["filename_section"] is None
    assert "surface_corrected_spans" not in docs[0].meta


def test_a_surface_that_does_not_match_the_text_raises_and_quotes_neither(tmp_path):
    """The offset check, and CLAUDE.md's message rule, in one test.

    Both strings here are invented in this file. If either reaches the message, the
    loader quotes span text — and a loader that does that for a synthetic corpus does it
    for the DUA-restricted one. Offsets and lengths only.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(surface="99/99/9999")])],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "99/99/9999" not in message
    assert DATE not in message
    assert f"[{DATE_START}, {DATE_END})" in message


def test_an_offset_past_the_end_of_the_document_raises(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(end=9999, surface=DATE)])],
    )
    with pytest.raises(CorpusError, match="characters"):
        loader_on(root).load()


def test_a_malformed_offset_field_is_not_quoted(tmp_path):
    """A multi-fragment or otherwise malformed second field can hold document text.

    brat's `start end;start end` form does not occur in this release and has no scoring
    rule, so it raises. The message reports how many parts the field had and how long it
    was; `meddocan.py` prints the field itself, which is safe there because MEDDOCAN is
    synthetic and is not safe here.
    """
    root = write_root(
        tmp_path / "root",
        [
            (
                "CARMEN-I_IR_1",
                "es",
                False,
                [f"T1\tFECHAS {DATE_START} 26;28 {DATE_END}\t{DATE}"],
            )
        ],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "Multi-fragment" in message
    assert "26;28" not in message
    assert DATE not in message
    assert "4 space-separated parts" in message


def test_a_line_with_the_wrong_field_count_raises_without_quoting_it(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [f"T1\tFECHAS {DATE_START} {DATE_END}"])],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "2 tab-separated fields" in str(exc.value)
    assert DATE not in str(exc.value)


def test_a_non_text_bound_annotation_raises(tmp_path):
    """Relation and attribute lines need a scoring decision before they can be loaded."""
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(tag_id="R1")])],
    )
    with pytest.raises(CorpusError, match="'R' annotation"):
        loader_on(root).load()


def test_non_integer_offsets_raise(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [f"T1\tFECHAS x y\t{DATE}"])],
    )
    with pytest.raises(CorpusError, match="non-integer offsets"):
        loader_on(root).load()


def test_a_concept_type_in_the_phi_layer_raises(tmp_path):
    """The layer boundary, enforced at the line rather than at the directory.

    The two layers are declared in one `annotation.conf` with no marker between them, so
    "this file is the PHI layer" is a claim about the directory and nothing else checks
    it. Loading a concept span would add 26,360 spans to a gold set of 8,231.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(tag="ENFERMEDAD")])],
    )
    with pytest.raises(CorpusError, match="medical-concept layer's type"):
        loader_on(root).load()


def test_a_declared_type_the_loader_does_not_know_raises(tmp_path):
    """The schema check's first direction: a release adding an undecided type."""
    types = set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES) | {"TIPO_NUEVO"}
    root = write_root(tmp_path / "root", types=types)
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "Declared but not decided: ['TIPO_NUEVO']" in str(exc.value)


def test_a_type_the_loader_knows_and_the_release_does_not_declare_raises(tmp_path):
    """The quieter direction: the map was built against a different release.

    Every count in §9.0's block would belong to that other release, and nothing in a
    load would say so — the type has no instances here either way.
    """
    types = (set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES)) - {"NUMERO_FAX"}
    root = write_root(tmp_path / "root", types=types)
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "Known but not declared: ['NUMERO_FAX']" in str(exc.value)


def test_a_missing_schema_raises(tmp_path):
    root = write_root(tmp_path / "root")
    (root / SCHEMA).unlink()
    with pytest.raises(CorpusError, match="declares no schema"):
        loader_on(root).load()


def test_a_document_with_no_mappings_row_raises(tmp_path):
    """No default language label: a document with no row has no stratum.

    Inventing one would put a stratification the split file asserts on top of a label
    nobody recorded.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, None), ("CARMEN-I_IR_2", "es", False, None)],
        mappings_rows=[("CARMEN-I_IR_1", "es", "False")],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "1 documents have no row" in message
    assert "no default for it" in message


def test_a_mappings_row_with_no_document_raises(tmp_path):
    root = write_root(
        tmp_path / "root",
        mappings_rows=[("CARMEN-I_IR_1", "es", "False"), ("CARMEN-I_IR_2", "es", "False")],
    )
    with pytest.raises(CorpusError, match="1 rows have no document"):
        loader_on(root).load()


def test_a_repeated_mappings_row_raises(tmp_path):
    """Two rows for one document is two language labels, and `csv` would keep the last."""
    root = write_root(
        tmp_path / "root",
        mappings_rows=[("CARMEN-I_IR_1", "es", "False"), ("CARMEN-I_IR_1", "cat", "False")],
    )
    with pytest.raises(CorpusError, match="repeats a document id"):
        loader_on(root).load()


def test_an_unknown_language_label_raises(tmp_path):
    """A fourth label is a stratum the frozen split does not describe."""
    root = write_root(
        tmp_path / "root", mappings_rows=[("CARMEN-I_IR_1", "eu", "False")]
    )
    with pytest.raises(CorpusError, match="not one of"):
        loader_on(root).load()


def test_a_non_boolean_concept_flag_raises(tmp_path):
    root = write_root(
        tmp_path / "root", mappings_rows=[("CARMEN-I_IR_1", "es", "true")]
    )
    with pytest.raises(CorpusError, match="ner_annotations"):
        loader_on(root).load()


def test_reordered_mappings_columns_raise(tmp_path):
    """Read by name, and the names are checked: a reordered release is a new file."""
    root = write_root(
        tmp_path / "root",
        fields=("filename", "ner_annotations", "language"),
        mappings_rows=[("CARMEN-I_IR_1", "False", "es")],
    )
    with pytest.raises(CorpusError, match="has columns"):
        loader_on(root).load()


def test_a_missing_mappings_file_raises(tmp_path):
    root = write_root(tmp_path / "root")
    (root / MAPPINGS).unlink()
    with pytest.raises(CorpusError, match="stratification has no variable"):
        loader_on(root).load()


def test_a_concept_flag_the_directory_does_not_support_raises(tmp_path):
    """The flag is not read for anything, which is exactly why it is checked.

    It is the one claim the mappings file makes that can be verified against the tree,
    so it is the evidence that the file and the release are the same version — and the
    language label in the same rows is what the split rests on.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", True, None)],
        concept_stems=[],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "1 flagged documents have no file" in str(exc.value)


def test_a_concept_file_no_row_flags_raises(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, None)],
        concept_stems=["CARMEN-I_IR_1"],
    )
    with pytest.raises(CorpusError, match="1 files are unflagged"):
        loader_on(root).load()


def test_a_missing_concept_directory_raises(tmp_path):
    root = write_root(tmp_path / "root")
    for path in (root / ANN_ROOT / VARIANT / CONCEPT_LAYER).iterdir():
        path.unlink()
    (root / ANN_ROOT / VARIANT / CONCEPT_LAYER).rmdir()
    with pytest.raises(CorpusError, match="describes nothing"):
        loader_on(root).load()


def test_a_document_with_no_text_file_raises(tmp_path):
    root = write_root(tmp_path / "root")
    (root / ANN_ROOT / VARIANT / PHI_LAYER / "CARMEN-I_IR_1.txt").unlink()
    with pytest.raises(CorpusError, match="no matching .txt"):
        loader_on(root).load()


def test_an_empty_phi_layer_raises_and_points_at_the_configuration(tmp_path):
    root = write_root(tmp_path / "root")
    for path in (root / ANN_ROOT / VARIANT / PHI_LAYER).iterdir():
        path.unlink()
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "data_paths.local.yaml" in str(exc.value)


def test_an_unparsable_doc_id_raises(tmp_path):
    """No residual stratum. A document in an `unparsed` band would be stratified on a
    label meaning "this code did not understand the id", and the split file would record
    that as a composition."""
    root = write_root(tmp_path / "root", [("CARMEN_1", "es", False, None)])
    with pytest.raises(CorpusError, match="does not parse as"):
        loader_on(root).load()


def test_an_unknown_document_type_token_raises(tmp_path):
    root = write_root(tmp_path / "root", [("CARMEN-I_ZZ_1", "es", False, None)])
    with pytest.raises(CorpusError, match="document-type token"):
        loader_on(root).load()


def test_a_section_bearing_doc_id_parses_into_three_parts(tmp_path):
    """The section tokens contain `_`, so the id is matched and not split on `_`.

    All three parts, because `filename_index` is what §9.5 step 1's candidate key for this
    corpus pairs with the doctype (`src/split.py`'s `_carmen_candidate_key`), and a
    section token that swallowed the trailing number would make every document its own
    candidate group without the audit's count changing shape.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IA_EXPLORACION_COMPLEMENTARIA_7", "cat", False, None)],
    )
    doc = loader_on(root).load()[0]
    assert doc.meta["filename_doctype"] == "IA"
    assert doc.meta["filename_section"] == "EXPLORACION_COMPLEMENTARIA"
    assert doc.meta["filename_index"] == 7


def test_a_sectionless_doc_id_still_carries_the_index(tmp_path):
    """`filename_section` is `None` and the index is the number, not a re-parse of the id.

    The two id shapes have to agree on what `filename_index` means, or the candidate key
    pairs a sectionless letter with nothing.
    """
    root = write_root(tmp_path / "root", [("CARMEN-I_IR_413", "es", False, None)])
    doc = loader_on(root).load()[0]
    assert doc.meta["filename_section"] is None
    assert doc.meta["filename_index"] == 413


def test_a_bom_shifts_every_offset_in_the_document(tmp_path):
    """The arithmetic the real corpus cannot exercise (0 of 2,000 documents).

    Kept in the loader and tested here for MEDDOCAN's and GraSCCo's reason: a release
    that gained a BOM would otherwise move every span in that document by one with
    nothing saying so. The correction is arithmetic and uniform rather than a re-search
    for the surface, which is what stops it from silently repairing genuinely wrong
    offsets.
    """
    root = write_root(
        tmp_path / "root",
        [
            (
                "CARMEN-I_IR_1",
                "es",
                False,
                [date_line(start=DATE_START + 1, end=DATE_END + 1, surface=DATE)],
            )
        ],
        text="﻿" + SYNTHETIC_TEXT,
    )
    doc = loader_on(root).load()[0]
    assert doc.had_bom is True
    assert not doc.text.startswith("﻿")
    assert (doc.spans[0].start, doc.spans[0].end) == (DATE_START, DATE_END)


# ─── the pin, on synthetic trees ────────────────────────────────────────────
# §9.7's entry excuses one span in one document. Its two refusals are what make it a
# pin rather than a tolerance, and both are exercised here — on a synthetic tree, by
# patching the table, so that neither test depends on the real document's text.


def defect_root(tmp_path, *, lines, doc_id=DEFECT_DOC):
    return write_root(tmp_path / "root", [(doc_id, "es", False, lines)])


def test_a_pinned_span_whose_surface_now_matches_raises(tmp_path, monkeypatch):
    """The release fixed the defect: the entry must be removed, not left standing.

    A standing permission to overwrite a surface corrects the *next* data error in that
    document instead of reporting it, which is the failure this refusal is for.
    """
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 0): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(tmp_path, lines=[date_line()])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "release has fixed the defect" in str(exc.value)


def test_a_pin_that_names_a_different_span_raises(tmp_path, monkeypatch):
    """The pin must not slide onto another span if the release is reordered."""
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 0): ("EDAD_SUJETO_ASISTENCIA", DATE_START, DATE_END)},
    )
    root = defect_root(tmp_path, lines=[date_line(surface="99/99/9999")])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "must not be allowed to slide onto another" in str(exc.value)


def test_a_pin_past_the_end_of_the_document_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 7): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(tmp_path, lines=[date_line()])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "span that no longer exists" in str(exc.value)


def test_the_pin_corrects_the_named_span_and_records_it(tmp_path, monkeypatch):
    """The positive case, and the only span the surface is taken from the text for.

    The offsets are kept — §9.7's three witnesses say the surface field is what this
    release got wrong — so the span that comes out spans the same characters and the
    document records which index was corrected.
    """
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 1): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(
        tmp_path,
        lines=[
            ann_line("T1", "EDAD_SUJETO_ASISTENCIA", 38, 40, SYNTHETIC_TEXT[38:40]),
            date_line(tag_id="T2", surface="99/99/9999"),
        ],
    )
    doc = loader_on(root).load()[0]
    assert doc.meta["surface_corrected_spans"] == [1]
    assert (doc.spans[1].start, doc.spans[1].end) == (DATE_START, DATE_END)
    assert doc.spans[1].surface == doc.text[DATE_START:DATE_END]
    assert doc.spans[0].surface == doc.text[38:40]


def test_the_correction_reaches_the_split_file_and_not_only_the_loader_s_meta(
    tmp_path, monkeypatch
):
    """The audit route, put back on a synthetic tree after the seal took the real one.

    What `meta["surface_corrected_spans"]` is *for* is one hop further on:
    `src/split.py`'s narrative copies it into
    `corpus_specific.surface_corrected_spans`, which is where `splits/es-carmen.json`
    names one document and one span index. That is the whole of the claim "a correction
    only the loader's source states is one no result file can be audited against" — the
    result file is the split file, and the test above stops at the loader.

    Written on 2026-10-02, because the 279-mutation full run of 2026-10-01 found
    `carmen_correction_not_recorded` surviving with one killer where two are required.
    Both lost killers read the correction off the real pinned document, which went behind
    the seal with it on 2026-10-01; the replacement that still touches the key asserts
    that the visible corpus corrects *nothing*, and an equality against `{}` is satisfied
    by a loader that records nothing. The floor was not what was wrong, so the floor is
    not what moved.

    `_carmen_narrative` is called directly, the way `tests/test_split_file.py` calls
    `split._achieved` for the same reason, and `units` is handed over rather than derived:
    `grouping()` would pull `grouping_audit` into a test that has nothing to say about it,
    and one mutation lives in there.
    """
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 1): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(
        tmp_path,
        lines=[
            ann_line("T1", "EDAD_SUJETO_ASISTENCIA", 38, 40, SYNTHETIC_TEXT[38:40]),
            date_line(tag_id="T2", surface="99/99/9999"),
        ],
    )
    loader = loader_on(root)
    docs = loader.load()
    narrative = split._carmen_narrative(docs, [[DEFECT_DOC]], loader)
    assert narrative["corpus_specific"]["surface_corrected_spans"] == {DEFECT_DOC: [1]}

    # The control, and it is labelled one because it is the shape that failed: a document
    # the loader did not correct must leave the block empty, and *this* assertion cannot
    # catch a loader that stopped recording. It says the block is derived rather than
    # constant; the assertion above is the one that says it is derived from the loader.
    clean = loader_on(write_root(tmp_path / "clean"))
    narrative = split._carmen_narrative(clean.load(), [["CARMEN-I_IR_1"]], clean)
    assert narrative["corpus_specific"]["surface_corrected_spans"] == {}


def test_a_mismatch_the_pin_does_not_name_raises(tmp_path, monkeypatch):
    """One span is excused; the second mismatch in the same document is still an error."""
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 1): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(
        tmp_path,
        lines=[
            ann_line("T1", "EDAD_SUJETO_ASISTENCIA", 38, 40, "77"),
            date_line(tag_id="T2", surface="99/99/9999"),
        ],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "[38, 40)" in message
    assert "77" not in message


def test_the_pinned_table_names_one_span(tmp_path):
    """The table itself, unpatched: one entry, and it is §9.7's.

    Asserted separately from the real-corpus test so that a second entry appearing is a
    failure even on a machine with no CARMEN-I checkout — an added pin is a decision
    DESIGN has to carry, and the cheapest place for it to go unnoticed is a tree nobody
    can load.
    """
    assert KNOWN_SURFACE_DEFECTS == {(DEFECT_DOC, DEFECT_INDEX): DEFECT_PIN}


# ─── the seal ───────────────────────────────────────────────────────────────


def test_the_sealed_root_is_unreachable_without_authorisation(
    carmen_sealed, carmen_unsplit_loader
):
    """`sealed_reachable()` is the only permission, and an ordinary load has none.

    This test was written before there was a sealed root and was phrased so that it would
    hold either way: whatever `sealed_root()` answers, an unauthorised loader's
    `source_roots()` is its own root and nothing else. It now requires the sealed root,
    because that is what makes it say something — the negative it asserts was unfalsifiable
    while there was nothing to reach, and the third assertion is the one that needed a real
    second root to be worth making.
    """
    assert carmen_unsplit_loader.sealed_reachable() is None
    assert carmen_unsplit_loader.source_roots() == [carmen_unsplit_loader.root]
    assert base.sealed_root(carmen_sealed) not in carmen_unsplit_loader.source_roots()


def test_an_empty_sealed_read_raises_rather_than_returning_the_rest(tmp_path):
    """A sealed read that reached no sealed document is a failure, not a smaller corpus.

    The access is in `results/sealed_eval_log.md` before anything is opened, so a read
    that quietly returned the unsealed documents would produce numbers from the wrong
    data under a log row saying the test fold was evaluated. This is the guard
    `kosurro.py` gained on 2026-09-22 and that `base.fold_roots`, `grascco.py` and
    `endeid.py` still owe; it is written here with the loader rather than after it.

    The sealed root is a well-formed release with an empty PHI layer, which is the shape
    a broken seal actually produces. `_sealed_ok` is set directly and `sealed_root` is
    patched for this test only: the subject is `_read`'s invariant, not the
    authorisation that precedes it, and authorising properly would mean appending to the
    real log.
    """
    corpus = write_root(tmp_path / "root")
    sealed = write_root(tmp_path / "sealed", docs=())
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        assert loader.sealed_reachable() == sealed
        with pytest.raises(SealError) as exc:
            loader.load()
    assert "did not complete" in str(exc.value)


def test_a_sealed_root_that_holds_documents_is_read(tmp_path):
    """The positive control for the guard above, and for `source_files` across roots.

    Without it, `test_an_empty_sealed_read...` would pass on a loader that never opened
    a sealed root at all. The second assertion is the other half of the same property:
    the permission is spent by the read, so `source_files` can no longer reach the
    sealed root afterwards — which is why §6.2 records the per-document hashes *before*
    the seal and not after.
    """
    corpus = write_root(tmp_path / "root")
    sealed = write_root(tmp_path / "sealed", [("CARMEN-I_IR_2", "cat", False, None)])
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        docs = loader.load()
        assert [doc.doc_id for doc in docs] == ["CARMEN-I_IR_1", "CARMEN-I_IR_2"]
        assert loader.sealed_reachable() is None
        with pytest.raises(CorpusError, match="no files for doc_id"):
            loader.source_files("CARMEN-I_IR_2")


def test_a_document_under_two_roots_is_refused(tmp_path):
    """The same id in the corpus and the sealed root is a seal that copied instead of
    moving, and DESIGN §6.1's separation is physical: for this corpus the seal is a file
    **move**, the first of the five where it is not a rewrite. `source_files` refuses it
    because a digest over four files is not the digest the split file recorded.

    `tools/prepare_carmen.py seal` re-checks the same property over all 2,000 ids after it
    runs, which is the measurement; this is the loader refusing to read the state that
    check exists to rule out."""
    corpus = write_root(tmp_path / "root")
    sealed = write_root(tmp_path / "sealed")
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        with pytest.raises(CorpusError, match="more than one root"):
            loader.source_files("CARMEN-I_IR_1")
