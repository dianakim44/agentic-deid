"""Corpus-agnostic loading interface.

One `Document` per unit, one `Span` per annotation. What is corpus-specific lives
in a `CorpusLoader` subclass; everything here holds for every corpus.

Three rules this module exists to enforce, all from CLAUDE.md and DESIGN.md:

  - **No path is hardcoded.** Corpus roots come from
    `config/data_paths.local.yaml`, so code never knows whether a corpus sits
    inside the repository or outside it (DUA corpora are outside).
  - **No vocabulary is hardcoded.** Canonical types, layers and split names are
    read from `config/naming.yaml`. A value not defined there is an error, not a
    new value.
  - **Gold offsets are asserted, not trusted.** Every span is sliced out of the
    loaded text and compared to the surface the annotation recorded
    (DESIGN §9.7). A mismatch raises.

Usage:

    from src.corpora import load
    docs = load("es-meddocan")
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAMING = ROOT / "config" / "naming.yaml"
DATA_PATHS = ROOT / "config" / "data_paths.local.yaml"
DATA_PATHS_EXAMPLE = ROOT / "config" / "data_paths.example.yaml"

BOM = "﻿"


class CorpusError(Exception):
    """Anything wrong with a corpus on disk, its config, or its annotations.

    Deliberately one exception type: every case here is "stop and tell a human",
    and callers have no recovery path that differs by cause.
    """


# ─── configuration ──────────────────────────────────────────────────────────
# Read once and cached. These files are the single definition site for
# identifiers (naming.yaml) and locations (data_paths.local.yaml); nothing below
# may fall back to a literal if a lookup misses.


@lru_cache(maxsize=1)
def naming() -> dict:
    """The contents of config/naming.yaml."""
    with open(NAMING, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def axis(name: str) -> dict:
    """One axis of naming.yaml, e.g. `axis("phi_type")`.

    Raises rather than returning empty, because an absent axis means the code and
    the config have diverged — exactly the drift naming.yaml exists to prevent.
    """
    axes = naming()["axes"]
    if name not in axes:
        raise CorpusError(
            f"config/naming.yaml has no {name!r} axis (has: {sorted(axes)}). "
            "Add the axis there rather than hardcoding values."
        )
    return axes[name]


def corpus_ids() -> list[str]:
    return sorted(axis("corpus"))


def canonical_types() -> list[str]:
    """The canonical PHI types, from naming.yaml (see DESIGN §9.0)."""
    return sorted(axis("phi_type"))


def split_names() -> list[str]:
    return sorted(axis("split"))


def rule_langs(corpus_id: str) -> list[str]:
    """Rule-file languages this corpus loads (DESIGN §5.2).

    A list even when it has one element: monolingual is not a special case.
    """
    mapping = naming()["corpus_rule_langs"]
    if corpus_id not in mapping:
        raise CorpusError(
            f"no corpus_rule_langs entry for {corpus_id!r} in config/naming.yaml"
        )
    return list(mapping[corpus_id])


def corpus_root(corpus_id: str) -> Path:
    """Where this corpus lives on this machine.

    From config/data_paths.local.yaml only. The path may be inside or outside the
    repository and callers must not care — DUA corpora are kept outside, where
    tools/release_screen.py cannot see them.
    """
    if corpus_id not in axis("corpus"):
        raise CorpusError(
            f"{corpus_id!r} is not a corpus in config/naming.yaml "
            f"(have: {corpus_ids()})"
        )
    if not DATA_PATHS.exists():
        raise CorpusError(
            f"{DATA_PATHS} does not exist. Copy the template:\n"
            f"    cp {DATA_PATHS_EXAMPLE.relative_to(ROOT)} "
            f"{DATA_PATHS.relative_to(ROOT)}"
        )
    with open(DATA_PATHS, encoding="utf-8") as fh:
        mapping = (yaml.safe_load(fh) or {}).get("corpora") or {}
    raw = mapping.get(corpus_id)
    if not raw:
        raise CorpusError(
            f"config/data_paths.local.yaml has no path for {corpus_id!r}. "
            "See data/acquire/ for how to obtain it."
        )
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_dir():
        # Deliberately not echoing the resolved path: for a DUA corpus that is a
        # data location, and this message may end up in a log or an issue.
        raise CorpusError(
            f"the configured path for {corpus_id!r} is not a directory. "
            "Check config/data_paths.local.yaml and the acquisition script."
        )
    return path


# ─── data model ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Span:
    """One annotated or detected span of text.

    Character offsets into `Document.text`, 0-based, `end` exclusive, after any
    BOM shift (DESIGN §9.7). `surface` is the text itself, kept so the offsets
    can be re-asserted at any later point without re-reading the corpus.

    Type fields, and why there are three:

      - `phi_type` — the canonical type, a value of naming.yaml's `phi_type`
        axis. Cross-corpus scoring uses this and only this. `None` when the span
        is excluded (below), because no canonical type honestly applies.
      - `subtype` — the corpus's own type, preserved verbatim. Needed for the
        per-type reporting DESIGN §5.1 requires, and never used in cross-corpus
        scoring: the corpora do not partition the space the same way, so forcing
        agreement would measure the annotation schema rather than the detector
        (DESIGN §9.0).
      - `excluded` — set when the corpus type is out of scope per DESIGN §9.1
        (`SEXO_*`, `FAMILIARES_*`, `NAME_TITLE`). The span is **kept**, not
        dropped: the excluded volume is a reported limitation, so it has to be
        countable. Anything scoring spans must filter on this flag.

    Provenance fields (DESIGN §3) are empty on gold spans. `layer` is a value of
    naming.yaml's `layer` axis, filled in by the detector that emitted the span
    and never derived from a detector name. `rule_id` carries the rule file's
    language as a prefix (`es:doctor_prefix`), so precision is attributable to
    the file that produced the match (DESIGN §5.2). Agents do not get a `layer`:
    they do not create spans, and their interventions go in `agent_actions`.
    """

    start: int
    end: int
    surface: str
    subtype: str
    phi_type: str | None = None
    excluded: bool = False

    # provenance — empty for gold, filled by whatever emitted a prediction
    layer: str | None = None
    detector: str | None = None
    rule_id: str | None = None
    score: float | None = None
    agent_actions: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise CorpusError(f"empty or inverted span: [{self.start}, {self.end})")
        if self.start < 0:
            raise CorpusError(f"negative span start: {self.start}")
        if self.excluded and self.phi_type is not None:
            raise CorpusError(
                f"excluded span {self.subtype!r} must not carry a canonical type "
                f"(got {self.phi_type!r})"
            )
        if not self.excluded and self.phi_type is None:
            raise CorpusError(
                f"span {self.subtype!r} has no canonical type and is not marked "
                "excluded — every gold span is either mapped or explicitly "
                "excluded (DESIGN §9.0)"
            )
        if self.phi_type is not None and self.phi_type not in axis("phi_type"):
            raise CorpusError(
                f"{self.phi_type!r} is not a phi_type in config/naming.yaml "
                f"(have: {canonical_types()})"
            )
        if self.layer is not None and self.layer not in axis("layer"):
            raise CorpusError(
                f"{self.layer!r} is not a layer in config/naming.yaml "
                f"(have: {sorted(axis('layer'))}). Layers are read from the "
                "config, never derived from a detector name (DESIGN §3)."
            )

    @property
    def is_gold(self) -> bool:
        return self.layer is None and self.detector is None

    @property
    def in_scope(self) -> bool:
        """True for spans that count towards a metric (DESIGN §9.1)."""
        return not self.excluded


@dataclass(slots=True)
class Document:
    """One unit of a corpus, with its gold spans.

    `doc_id` is the corpus's own identifier. `split` is a value of naming.yaml's
    `split` axis, or `None` for a corpus that ships no split (one is then built
    per DESIGN §9.5 and frozen before any rule is written).

    `text` has had a leading BOM stripped, and every offset in `spans` has been
    shifted to match (DESIGN §9.7). `had_bom` records that this happened, so the
    correction can be counted rather than assumed.
    """

    doc_id: str
    corpus_id: str
    text: str
    spans: list[Span] = field(default_factory=list)
    split: str | None = None
    had_bom: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.split is not None and self.split not in axis("split"):
            raise CorpusError(
                f"{self.split!r} is not a split in config/naming.yaml "
                f"(have: {split_names()})"
            )
        if self.text.startswith(BOM):
            raise CorpusError(
                f"{self.doc_id}: text still begins with U+FEFF. The loader must "
                "strip the BOM and shift offsets (DESIGN §9.7)."
            )

    @property
    def in_scope_spans(self) -> list[Span]:
        return [s for s in self.spans if s.in_scope]

    def assert_offsets(self) -> None:
        """Slice every span out of the text and compare it to its surface.

        DESIGN §9.7: the loader asserts, it does not trust. Raises on the first
        mismatch, naming the span's index within the document so the failure is
        locatable without re-deriving it.

        No span surface appears in the message. Some corpora are DUA-restricted
        real clinical text (CARMEN-I), an exception message travels into logs and
        issues, and a check that is safe for one corpus and not another is a
        check nobody can trust. Lengths and offsets locate the fault.
        """
        for i, span in enumerate(self.spans):
            if span.end > len(self.text):
                raise CorpusError(
                    f"{self.corpus_id}/{self.doc_id}: span {i} "
                    f"({span.subtype}) ends at {span.end} but the document is "
                    f"{len(self.text)} characters"
                )
            sliced = self.text[span.start : span.end]
            if sliced != span.surface:
                raise CorpusError(
                    f"{self.corpus_id}/{self.doc_id}: span {i} ({span.subtype}) "
                    f"at [{span.start}, {span.end}) does not match its recorded "
                    f"surface — sliced {len(sliced)} chars, annotation recorded "
                    f"{len(span.surface)}"
                    + (
                        " (document carried a BOM; check the §9.7 offset shift)"
                        if self.had_bom
                        else ""
                    )
                )


# ─── loader interface ───────────────────────────────────────────────────────


class CorpusLoader:
    """Base class for one corpus's loader.

    A subclass sets `corpus_id` and implements `_read()`. Everything shared —
    resolving the root, stripping BOMs, mapping types, asserting offsets — is
    here, so a new corpus cannot quietly skip a step that every corpus needs.
    """

    corpus_id: str = ""
    #: corpus type -> canonical type. See DESIGN §9.0.
    type_map: dict[str, str] = {}
    #: corpus types kept but not scored. See DESIGN §9.1.
    excluded_types: frozenset[str] = frozenset()

    def __init__(self, root: Path | None = None) -> None:
        if not self.corpus_id:
            raise CorpusError(f"{type(self).__name__} does not set corpus_id")
        self.root = root if root is not None else corpus_root(self.corpus_id)
        self._check_type_map()

    def _check_type_map(self) -> None:
        """Fail at construction if the mapping disagrees with naming.yaml."""
        allowed = axis("phi_type")
        unknown = sorted(set(self.type_map.values()) - set(allowed))
        if unknown:
            raise CorpusError(
                f"{self.corpus_id}: type_map targets {unknown}, which are not "
                f"phi_type values in config/naming.yaml (have: "
                f"{canonical_types()}). Add them there first."
            )
        both = sorted(set(self.type_map) & set(self.excluded_types))
        if both:
            raise CorpusError(
                f"{self.corpus_id}: {both} are both mapped and excluded — a type "
                "is one or the other (DESIGN §9.0, §9.1)"
            )

    # -- subclass hook --

    def _read(self) -> Iterator[Document]:
        raise NotImplementedError

    # -- shared machinery --

    def load(self) -> list[Document]:
        """Read the corpus, then assert every document's offsets."""
        docs = list(self._read())
        if not docs:
            raise CorpusError(f"{self.corpus_id}: no documents found under the root")
        seen: set[str] = set()
        for doc in docs:
            if doc.doc_id in seen:
                raise CorpusError(f"{self.corpus_id}: duplicate doc_id {doc.doc_id!r}")
            seen.add(doc.doc_id)
            doc.assert_offsets()
        return docs

    def strip_bom(self, text: str) -> tuple[str, int]:
        """Remove a leading BOM. Returns the text and the offset shift.

        DESIGN §9.7, applied identically to every corpus. Note that reading with
        `encoding='utf-8-sig'` instead would be wrong: the shipped offsets count
        the BOM, so decoding it away without shifting breaks every span in the
        file. Read as plain utf-8 and shift here.
        """
        if text.startswith(BOM):
            return text[len(BOM) :], len(BOM)
        return text, 0

    def classify(self, corpus_type: str) -> tuple[str | None, bool]:
        """corpus type -> (canonical type, excluded).

        Every type must be either mapped or explicitly excluded. An unrecognised
        type raises rather than being dropped or bucketed into `OTHER`: a silently
        discarded gold span makes recall look better than it is, and the release
        added a type we have not decided about.
        """
        if corpus_type in self.type_map:
            return self.type_map[corpus_type], False
        if corpus_type in self.excluded_types:
            return None, True
        raise CorpusError(
            f"{self.corpus_id}: annotation type {corpus_type!r} is neither mapped "
            "nor excluded. Decide it in DESIGN §9.0/§9.1 and add it to the "
            "loader; do not drop it."
        )


# ─── registry ───────────────────────────────────────────────────────────────


def _loaders() -> dict[str, type[CorpusLoader]]:
    """Imported lazily so one broken loader cannot break the others."""
    from . import meddocan

    return {meddocan.MeddocanLoader.corpus_id: meddocan.MeddocanLoader}


def load(corpus_id: str, root: Path | None = None) -> list[Document]:
    """Load one corpus by its naming.yaml id."""
    loaders = _loaders()
    if corpus_id not in loaders:
        known = sorted(loaders)
        if corpus_id in axis("corpus"):
            raise CorpusError(
                f"{corpus_id!r} is a known corpus but has no loader yet "
                f"(implemented: {known})"
            )
        raise CorpusError(
            f"{corpus_id!r} is not a corpus in config/naming.yaml "
            f"(have: {corpus_ids()})"
        )
    return loaders[corpus_id](root=root).load()


# ─── counting helpers ───────────────────────────────────────────────────────
# Used by tests and by profile reconciliation. Kept here so that "how many spans
# are in scope" has one implementation rather than one per caller.


def count_by_split(docs: Sequence[Document]) -> dict[str | None, int]:
    counts: dict[str | None, int] = {}
    for doc in docs:
        counts[doc.split] = counts.get(doc.split, 0) + 1
    return counts


def count_spans(docs: Sequence[Document], *, in_scope_only: bool = False) -> int:
    return sum(
        len(doc.in_scope_spans if in_scope_only else doc.spans) for doc in docs
    )


def count_by_type(
    docs: Sequence[Document], *, canonical: bool = True
) -> dict[str, int]:
    """Span counts by canonical type, or by the corpus's own type.

    Excluded spans have no canonical type, so they are absent from the canonical
    tally and present in the subtype one. That asymmetry is the point: the two
    views should not silently reconcile.
    """
    counts: dict[str, int] = {}
    for doc in docs:
        for span in doc.spans:
            key = span.phi_type if canonical else span.subtype
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
    return counts
