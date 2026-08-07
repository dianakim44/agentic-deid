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

#: The only module allowed to load a sealed fold. Checked by import identity, not
#: by inspecting the call stack: a stack walk can be satisfied by naming a
#: function or a file the same thing, and the point of the gate is that satisfying
#: it takes a deliberate edit to a committed file rather than a clever caller.
SEALED_CALLER = "src.eval.run_sealed_eval"


class CorpusError(Exception):
    """Anything wrong with a corpus on disk, its config, or its annotations.

    Deliberately one exception type: every case here is "stop and tell a human",
    and callers have no recovery path that differs by cause.
    """


class SealError(CorpusError):
    """An attempt to reach a sealed fold from somewhere that may not.

    The one exception here with its own type, because it is the one failure whose
    right response is never "handle it and continue". It subclasses CorpusError so
    existing handlers still stop — but a handler that means to swallow a corpus
    problem has to name this type explicitly to swallow a seal breach too.
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


@lru_cache(maxsize=1)
def layer_families() -> dict[str, tuple[str, ...]]:
    """Family name -> the layers in it, validated. See DESIGN §3.

    The complementarity breakdown (DESIGN §5) reports rules only / tagger only /
    both / neither, which requires knowing which layers are the rules ones. That
    grouping lives in `config/naming.yaml`, not here: a layer whose value is read
    from the config while its family is hardcoded in Python is the same drift the
    "never derive a layer from a detector name" rule exists to prevent, just moved
    one level up.

    This validates rather than merely reads, and the union check is the load-bearing
    one. **Every layer must be in exactly one family, and no family may name a layer
    that does not exist.** A subset check would pass when a new layer is added to the
    axis and forgotten here — and then every span that layer emits falls into
    `neither`, which reads as "nothing found it" rather than as a configuration gap.
    That is a wrong number with no symptom, so it is refused at load time.

    Families and layers are different levels of description, and a family name that
    is also a layer name opens the way to filling a span's `layer` with a family.
    `tagger` is both, which is permitted under one condition: **a family may share a
    layer's name only if that layer is its sole member.** Then the two readings of the
    value agree — a span from the `tagger` family has `layer="tagger"` either way — so
    the ambiguity cannot produce a wrong value.

    The condition is not decoration. Add a second learned layer to the `tagger` family
    and the readings diverge: `layer="tagger"` would then be a valid value meaning
    "some learned layer", the provenance DESIGN §3 requires would be unrecoverable for
    those spans, and nothing would fail. This raises at that moment and says to rename
    the family, which is the edit that keeps the two namespaces separable.
    """
    families = naming().get("layer_families")
    if not families:
        raise CorpusError(
            "config/naming.yaml has no layer_families block. The complementarity "
            "breakdown (DESIGN §5) needs it, and deriving families in code would "
            "put the grouping somewhere naming.yaml cannot be checked against."
        )

    layers = set(axis("layer"))
    assigned: dict[str, str] = {}
    for family, members in families.items():
        if not isinstance(members, list):
            raise CorpusError(
                f"layer_families[{family!r}] is not a list. A family of one is "
                "written as a list too, so that it is not a special case."
            )
        if family in layers and members != [family]:
            raise CorpusError(
                f"family {family!r} shares its name with a layer but is not that "
                f"layer alone (members: {members}). Sharing a name is only safe for "
                "a family of one, where the family reading and the layer reading of "
                "the value agree. With more members, `layer=\"{0}\"` becomes a valid "
                "value meaning 'some layer of this family' and the per-layer "
                "provenance DESIGN §3 requires is unrecoverable. Rename the "
                "family.".format(family)
            )
        for layer in members:
            if layer in assigned:
                raise CorpusError(
                    f"layer {layer!r} is in both the {assigned[layer]!r} and "
                    f"{family!r} families. The complementarity breakdown counts "
                    "each layer once, so the families must partition the axis."
                )
            assigned[layer] = family

    missing = sorted(layers - set(assigned))
    if missing:
        raise CorpusError(
            f"layers {missing} are in the `layer` axis but in no family of "
            "layer_families. Add them to a family: an unfamilied layer's spans "
            "would be counted as `neither` in the complementarity breakdown, which "
            "reads as 'nothing found it' rather than as a missing declaration."
        )
    unknown = sorted(set(assigned) - layers)
    if unknown:
        raise CorpusError(
            f"layer_families names {unknown}, which are not values of the `layer` "
            f"axis (have: {sorted(layers)}). A family entry for a layer that does "
            "not exist is a leftover from a rename, and it silently contributes "
            "nothing to any count."
        )
    return {f: tuple(m) for f, m in families.items()}


def family_of(layer: str) -> str:
    """Which family a layer belongs to. Raises for an unknown layer.

    Never guesses from the name: `layer_families` is the only answer, so a layer
    added to the axis without a family is an error here rather than a silent
    `neither` in the complementarity breakdown.
    """
    for family, members in layer_families().items():
        if layer in members:
            return family
    raise CorpusError(
        f"{layer!r} is not a layer in config/naming.yaml "
        f"(have: {sorted(axis('layer'))})"
    )


def model_id_absent() -> str:
    """The `model_id` value an arm that called no language model records.

    From `config/naming.yaml`'s `model_id_absent`, not a literal here. `model_id` is
    not an axis — it holds the exact identifier a call was made with, which is an
    observation and not a controlled vocabulary (DESIGN §4) — but *this* value is
    vocabulary, and CLAUDE.md's rule applies to it like any other: a value that lands
    in a published results file is defined in the config.

    A string rather than `None` for the reason the cost block writes zeros: `None`
    cannot distinguish "not applicable" from "not recorded", and distinguishing them
    is the entire purpose of the field. The `R` arm runs no model and says so.
    """
    value = naming().get("model_id_absent")
    if not isinstance(value, str) or not value:
        raise CorpusError(
            "config/naming.yaml has no `model_id_absent` string. It is what an arm "
            "that called no model records in metrics.json's run block, and it lives "
            "in the config so that no module spells it as a literal."
        )
    return value


def path_template(key: str) -> str:
    """One `paths` template from naming.yaml, e.g. `path_template("humanlog")`.

    Templates live in the config and not in the modules that write to them, for the
    reason `axis()` exists: two modules holding the same literal are two literals, and
    the day one of them changes is the day the results are in two places. Raises on an
    unknown key rather than returning a default — a caller asking for a path the config
    does not declare has invented an artifact.

    The template is returned unformatted. Filling it is the caller's job, and the caller
    is what knows which components need checking against which axis (a `{porting}` value
    is an axis value, `{lang}` is not a corpus).
    """
    templates = naming().get("paths", {})
    if key not in templates:
        raise CorpusError(
            f"config/naming.yaml has no paths.{key} (has: {sorted(templates)}). "
            "Declare the path there rather than writing it as a literal — CLAUDE.md's "
            "rule that a new value goes into the config first applies to output paths, "
            "which is where the results of an arm are found."
        )
    return templates[key]


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
    return _resolve(raw, corpus_id)


def _resolve(raw: str, corpus_id: str) -> Path:
    """Expand and validate a configured path.

    Never echoes the resolved path. For a DUA corpus that is a data location, and
    this message travels into logs and issues.
    """
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_dir():
        raise CorpusError(
            f"the configured path for {corpus_id!r} is not a directory. "
            "Check config/data_paths.local.yaml and the acquisition script."
        )
    return path


def sealed_root(corpus_id: str) -> Path | None:
    """Where this corpus's sealed test fold lives, or None if it is not sealed.

    Reads the `sealed:` block **only**, which is why that block is separate from
    `corpora:` in the config. A single mapping would mean any code iterating over
    corpus paths reaches the sealed fold as a matter of course; the seal has to be
    "the path is not known here", not "the path is known and politely avoided".

    Returns None rather than raising when a corpus has no entry: not-yet-sealed is
    a real and distinct state (the split file has to be frozen first), and
    conflating it with a misconfiguration would push someone towards adding an
    entry that points at unsealed data.
    """
    if corpus_id not in axis("corpus"):
        raise CorpusError(
            f"{corpus_id!r} is not a corpus in config/naming.yaml "
            f"(have: {corpus_ids()})"
        )
    if not DATA_PATHS.exists():
        return None
    with open(DATA_PATHS, encoding="utf-8") as fh:
        mapping = (yaml.safe_load(fh) or {}).get("sealed") or {}
    raw = mapping.get(corpus_id)
    if not raw:
        return None
    return _resolve(raw, corpus_id)


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
    #: Folds that live behind `sealed/`. A tuple rather than the literal "test" so
    #: that a corpus needing a second held-out fold does not invite a string
    #: comparison somewhere else in the code.
    sealed_splits: tuple[str, ...] = ("test",)

    #: Fold directory name -> naming.yaml split value, in load order. A mapping
    #: rather than a list of names because MEDDOCAN's directories happen to be
    #: named after the folds and no other corpus is promised to be — a corpus whose
    #: `held_out/` directory holds the test fold must not be able to reach it just
    #: because the string differs from `"test"`.
    fold_dirs: dict[str, str] = {}

    def __init__(self, root: Path | None = None, use_split_file: bool = True) -> None:
        """`use_split_file=False` loads the corpus without `splits/{corpus}.json`.

        The default is True: for every ordinary caller the frozen split file is
        the authority on which fold a document is in, and a loader that produced
        folds from anywhere else would make the seal unenforceable.

        The False path exists for exactly two callers — the generator that writes
        the split file (it cannot read what it is about to create) and the test
        that checks the file against a recount. Both are in this repository and
        neither is a way to load data for an experiment.
        """
        if not self.corpus_id:
            raise CorpusError(f"{type(self).__name__} does not set corpus_id")
        self.root = root if root is not None else corpus_root(self.corpus_id)
        self.use_split_file = use_split_file
        #: Set for the duration of an authorised sealed read, and only there. An
        #: attribute rather than an argument threaded through `_read()` because
        #: `_read()` is a subclass hook and a flag it had to remember to honour
        #: would be a flag a new loader could forget.
        self._sealed_ok = False
        self._check_type_map()

    def fold_roots(self) -> dict[str, Path]:
        """Fold directory -> the root it lives under. The reachability decision.

        This is the single place that answers "which folds can be read", and it
        answers by returning paths rather than by filtering documents afterwards. A
        sealed fold that is not in this mapping is not skipped downstream — its
        directory is never looked at, so there is no later step that could forget.

        A sealed fold appears here only when `_sealed_ok` is set, which only
        `_authorise_sealed()` does, and only after the access has been logged.
        """
        if not self.fold_dirs:
            raise CorpusError(f"{type(self).__name__} does not set fold_dirs")
        unknown = sorted(set(self.fold_dirs.values()) - set(axis("split")))
        if unknown:
            raise CorpusError(
                f"{self.corpus_id}: fold_dirs maps to {unknown}, which are not "
                f"split values in config/naming.yaml (have: {split_names()})"
            )
        sealed = sealed_root(self.corpus_id)
        roots: dict[str, Path] = {}
        for fold_dir, fold in self.fold_dirs.items():
            if fold in self.sealed_splits and sealed is not None:
                if not self._sealed_ok:
                    continue
                roots[fold_dir] = sealed
            else:
                roots[fold_dir] = self.root
        if self._sealed_ok:
            # An authorised sealed read must actually reach every sealed fold. If
            # `fold_dirs` has no entry for one, the read would return the unsealed
            # folds alone — while the log records a test evaluation that happened.
            # A run that is counted but did not read the fold is worse than a
            # refusal: it spends a row and produces numbers from the wrong data.
            reachable = {self.fold_dirs[d] for d in roots}
            unreachable = sorted(set(self.sealed_splits) - reachable)
            if unreachable:
                raise SealError(
                    f"{self.corpus_id}: a sealed read was authorised but "
                    f"{unreachable} has no entry in fold_dirs, so the fold would "
                    "not be read at all. The log has already recorded this access; "
                    "note in results/sealed_eval_log.md that the run did not "
                    "complete, and fix the loader before running again."
                )
        return roots

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

    def load(
        self,
        sealed: bool = False,
        *,
        purpose: str | None = None,
        arms: str = "—",
    ) -> list[Document]:
        """Read the corpus, apply the frozen split, then assert every offset.

        Returns the **unsealed** folds only. For a corpus whose test fold has been
        moved to `sealed/`, that is train and dev; the sealed documents are not on
        disk under the corpus root at all, so this is not a filter that could be
        forgotten — there is nothing there to filter.

        `sealed=True` additionally reads the sealed fold, and only
        `src/eval/run_sealed_eval.py` may pass it. It logs the access before
        reading anything and refuses to proceed if the log cannot be written.
        `purpose` and `arms` go into that log row and are ignored otherwise.
        """
        if sealed:
            self._authorise_sealed(purpose=purpose, arms=arms)
        try:
            docs = list(self._read())
        finally:
            # Cleared even on failure: a half-read sealed fold must not leave the
            # loader in a state where the next ordinary `load()` reaches it.
            self._sealed_ok = False
        if not docs:
            raise CorpusError(f"{self.corpus_id}: no documents found under the root")
        seen: set[str] = set()
        for doc in docs:
            if doc.doc_id in seen:
                raise CorpusError(f"{self.corpus_id}: duplicate doc_id {doc.doc_id!r}")
            seen.add(doc.doc_id)
            doc.assert_offsets()
        if self.use_split_file:
            self._apply_split_file(docs)
        if not sealed:
            self._assert_no_sealed_fold(docs)
        return docs

    def _authorise_sealed(
        self, purpose: str | None = None, arms: str = "—"
    ) -> None:
        """Permit a sealed read, or raise. Called before anything is opened.

        Two conditions, both required:

          - the calling module is `SEALED_CALLER`. Checked by walking the frames'
            `__name__`, which cannot be satisfied by naming a local file
            suggestively — it requires the real module to be on the stack.
          - the access has been appended to `results/sealed_eval_log.md`. The
            append happens **here**, before the read, and a failure to append
            aborts the read. An evaluation that ran without being logged is worse
            than one that did not run, because it leaves the log looking complete.
        """
        import inspect

        callers = set()
        frame = inspect.currentframe()
        try:
            while frame is not None:
                callers.add(frame.f_globals.get("__name__", ""))
                frame = frame.f_back
        finally:
            del frame
        if SEALED_CALLER not in callers:
            raise SealError(
                f"{self.corpus_id}: the sealed fold may only be read from "
                f"{SEALED_CALLER}, and it is not on the call stack. The test fold "
                "is sealed (CLAUDE.md): rule development, agent iteration and "
                "checkpoint selection use dev. If a test evaluation is genuinely "
                "intended, run that script — it records the run in "
                "results/sealed_eval_log.md, which is the number the paper has to "
                "report."
            )

        if sealed_root(self.corpus_id) is None:
            raise SealError(
                f"{self.corpus_id}: a sealed read was requested but the corpus has "
                "no `sealed:` entry in config/data_paths.local.yaml, so no fold of "
                "it is sealed. Do not add one pointing at the unsealed corpus — "
                "seal the fold first (DESIGN §6: generate, freeze, seal)."
            )

        from ..eval.sealed_log import record_access

        # Logged before the flag is set, so a failed append leaves the sealed fold
        # unreachable rather than merely unread. record_access raises SealError.
        # This is the only call site: a second one would put two rows in the log for
        # one read, and the row count is the number the paper reports.
        record_access(self.corpus_id, purpose=purpose, arms=arms)
        self._sealed_ok = True

    def _assert_no_sealed_fold(self, docs: list[Document]) -> None:
        """No sealed fold may appear in an ordinary load.

        Belt and braces on top of the physical move: if a corpus is configured as
        sealed but its sealed documents are still reachable under the corpus root,
        the move did not happen or was undone, and every downstream result would be
        computed on data the seal claims is untouched.
        """
        if sealed_root(self.corpus_id) is None:
            return
        leaked = sorted(d.doc_id for d in docs if d.split in self.sealed_splits)
        if leaked:
            raise SealError(
                f"{self.corpus_id}: {len(leaked)} documents of the sealed fold(s) "
                f"{sorted(self.sealed_splits)} loaded from the unsealed corpus "
                f"root (first: {leaked[:3]}). The fold is configured as sealed but "
                "its documents are still present outside sealed/ — the move did "
                "not happen, or was undone."
            )

    def _apply_split_file(self, docs: list[Document]) -> None:
        """Set every document's fold from `splits/{corpus}.json`.

        The split file is the authority, not the directory layout — MEDDOCAN
        happens to encode the fold in its path and no other corpus here does, so
        making the path authoritative would produce a rule that works once. Where
        both exist they are cross-checked and a disagreement raises: a corpus
        re-release that moved a document between folds must stop the run, because
        silently honouring either source would move a document across the seal.

        `verify()` is deliberately not called here. It recounts every span in the
        corpus and load() is on the path of every experiment; the check runs in
        the test suite and in `python3 -m src.split --check`, where its cost is
        paid once rather than on every load.
        """
        from ..split import fold_of, read

        record = read(self.corpus_id)
        assigned = fold_of(record)
        missing = sorted({d.doc_id for d in docs} - set(assigned))
        if missing:
            raise CorpusError(
                f"{self.corpus_id}: {len(missing)} loaded documents are in no "
                f"fold of splits/{self.corpus_id}.json (first: {missing[:3]}). "
                "The corpus on disk and the frozen split disagree; resolve that "
                "before loading — an unfolded document is one the seal does not "
                "cover."
            )

        # A document the split file assigns but that did not load is normally a
        # corpus/file disagreement — except for a sealed fold on an ordinary load,
        # where its absence is exactly what the seal means.
        absent = sorted(set(assigned) - {d.doc_id for d in docs})
        unexplained = [
            doc_id
            for doc_id in absent
            if assigned[doc_id] not in self.sealed_splits
            or sealed_root(self.corpus_id) is None
        ]
        if unexplained:
            raise CorpusError(
                f"{self.corpus_id}: splits/{self.corpus_id}.json assigns "
                f"{len(unexplained)} documents that did not load (first: "
                f"{unexplained[:3]})"
            )
        for doc in docs:
            fold = assigned[doc.doc_id]
            if doc.split is not None and doc.split != fold:
                raise CorpusError(
                    f"{self.corpus_id}/{doc.doc_id}: the corpus places this "
                    f"document in {doc.split!r} but the frozen split file says "
                    f"{fold!r}. Do not proceed: one of the two moved a document "
                    "across the seal."
                )
            doc.split = fold

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
