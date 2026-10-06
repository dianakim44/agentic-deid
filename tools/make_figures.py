#!/usr/bin/env python3
"""The manuscript's data figures (2-5), built from the recorded numbers only.

Four rules govern this file and all four are checkable by reading it:

1. **No measured value is written here.** Every number plotted is read from a
   `metrics.json` written by `src/eval/scorer.py`, from an Auditor report, from
   the arm's call log, or from a frozen `splits/{corpus}.json`. That includes
   the things that look like constants: the number of rounds comes from
   `termination.iterations`, the difference between two runs of identical
   prompt bytes is computed from the two arms that shared them, the rounds that
   get an arrow are the ones whose recorded gain exceeds that difference, the
   sparse threshold is imported from the scorer that applied it, and the arms in
   Figure 5 are the ones whose records say they did not iterate. A reader
   checking a figure against the files should never find a literal here to
   blame.
2. **No corpus text reaches the output.** Only PHI type names, corpus labels and
   counts are drawn, and type names come from `config/naming.yaml`'s `phi_type`
   axis. Nothing here reads a corpus, a `spans.jsonl` or a rule file, so no
   surface form can reach a figure, a caption or an exception message
   (CLAUDE.md).
3. **Nothing inside the figures points back at this repository.** Arm ids,
   schema versions and `docs/DESIGN.md` section numbers stay in the code and in
   the files; the drawn text carries manuscript labels and manuscript section
   numbers only, because a reader of the paper cannot follow the others.
4. **Figure 4 reads two denied paths, and its numbers go only into the
   figure.** The Auditor's reports (`auditreport`) and the arm's call log
   (`agentlog`) are deny-listed by name in `tools/release_screen.py`, so they
   are not in this repository and the figures built from them are not committed
   either. The loop's abandoned expenditure exists in no `metrics.json` -- the
   arm-level `abandoned_spend` block covers one round, and round 5's two dead
   attempts predate the field entirely -- so the only way to the published-
   versus-actual pair that Figure 4a draws is summation over the log minus
   `cost_to_date`. Nothing from either path is printed to stdout: a figure
   written outside the repository is one thing, and a value in a terminal or a
   CI log, where `release_screen.py` does not reach, is another.

Paths are taken from `config/naming.yaml`'s `paths` block rather than written
out, so a layout change moves the figures with it. Arm coordinates (detector,
supervision, porting) are discovered by globbing those templates and read back
out of each file's own `run` block.

Figure 1 is the architecture diagram. It carries no measured value, so it is not
built here: `tools/arch_figure.py` draws it, reads nothing, and writes beside
these four.

Colour is load-bearing in these figures, which is a change from their greyscale
predecessors: the layer palette is shared across Figures 2b and 5b so that a
layer keeps its colour between them, and 5c is a sequential heatmap. The one
distinction that must survive a greyscale print is the kind of reference behind
a bar, and that is hatched as well as coloured.

This file draws figures and writes no caption prose. The captions are in the
manuscript, and a second copy of them here would drift from it the moment
either was edited; what replaces that copy is `tools/check_manuscript_numbers.py`,
which reads the manuscript's captions and tables and checks their numbers
against these same files.

Usage
-----
    python tools/make_figures.py [--out DIR]

Default output directory is `~/Desktop/figures` -- outside the repository,
because figures built from DUA-covered corpora are not committed (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
import numpy as np  # noqa: E402
import yaml  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: The threshold behind every `sparse` flag in every `metrics.json`, imported
#: from the scorer that applied it rather than restated. Figure 5c prints it.
from src.eval.scorer import SPARSE_MAX  # noqa: E402

#: DESIGN §9.3: the headline leak rate is `fully_covered` and `relaxed` is reported
#: beside it as a lower bound. Both are plotted; neither is derived from the other.
HEADLINE_MODE = "fully_covered"
BOUND_MODE = "relaxed"

#: `splits/*.json` records `corpus_specific.reference` only where the reference is
#: not purely human — ko-surro's `human-verified silver` is the one case (DESIGN
#: §6.5 (v), §9.3). Absence of the key is therefore the human-gold case, and this
#: is a label for the legend, not a measured value.
HUMAN_REFERENCE = "human gold"

#: Corpus labels for drawn text and captions, exactly as the manuscript's Table 1
#: writes them. **This is the only place a corpus label is spelled out**, and it is
#: a presentation table, not a second naming scheme: the keys are the
#: `config/naming.yaml` corpus ids, those ids stay the vocabulary everywhere else in
#: the repository (CLAUDE.md), and `display()` refuses an id that is not a key rather
#: than inventing a label for it.
DISPLAY_NAME = {
    "de-grascco": "GraSCCo (de)",
    "es-meddocan": "MEDDOCAN (es)",
    "es-carmen": "CARMEN-I (es, ca)",
    "en-deid": "Nursing notes (en)",
    "ko-surro": "Nursing notes (ko)",
}

#: Layer labels for drawn text and legends, exactly as the manuscript writes them.
#: The same arrangement as `DISPLAY_NAME`, for the same reason: **this is the only
#: place a layer label is spelled out**, the keys are `config/naming.yaml`'s `layer`
#: axis values, those ids stay the vocabulary everywhere else (CLAUDE.md), and
#: `layer_display()` refuses an id that is not a key rather than inventing a label.
#: Figure 1's boxes say these same four words (`tools/arch_figure.py`), which is the
#: reason this table is worth having: in v14 the two figures sat in one manuscript
#: calling the last layer `learned tagger` and `Tagger`.
LAYER_DISPLAY = {
    "regex_checksum": "pattern rules",
    "context_cue": "context cues",
    "gazetteer": "gazetteer",
    "tagger": "tagger",
}

#: The colour and marker each layer keeps across Figures 2b and 5b, so that a layer
#: holds its appearance between them. Separate from the labels above so that a label
#: is written once; the iteration order is the order the series are drawn in.
#: `check_layer_series()` holds both tables to the axis, so a renamed layer fails
#: here instead of vanishing from a legend.
LAYER_SERIES = {
    "context_cue": ("#E09B2D", "s"),
    "gazetteer": ("#13A07A", "D"),
    "regex_checksum": ("#1B6CA8", "o"),
    "tagger": ("#7B4EA3", "^"),
}

#: The rest of the palette. `WORSE` marks movement in the wrong direction and
#: `BETTER` movement in the right one; `WITHIN` is for movement the one observed
#: difference between identical prompts cannot distinguish from noise.
HEADLINE_COLOUR = "#1B6CA8"
BOUND_COLOUR = "#79C0E8"
WORSE = "#CE5A12"
BETTER = "#13A07A"
WITHIN = "#7F7F7F"
SEALED_COLOUR = "#E8A33D"
BAR_FILL = "#C3D7E8"

#: The font stack, in preference order, shared with `tools/arch_figure.py` so that
#: Figure 1 and Figures 2-5 are set in one face. Arial is the manuscript's; the
#: other two are the metric-compatible substitutes that Linux and matplotlib's own
#: bundle supply, so a machine without Arial renders at the same widths rather
#: than at the same name.
FONT_STACK = ["Arial", "Liberation Sans", "DejaVu Sans"]

#: Figure 5c marks a type whose extents differ between two references that were
#: applied to **the same documents**. Two corpora qualify when their frozen folds
#: list identical document ids, and a type is marked when its gold counts on that
#: shared fold differ by this factor or more — or when one reference annotates it
#: and the other does not. The factor is a reading threshold and not a measured
#: value, so it is named once, here, and `figure_five` reports which types it
#: picked: the surrogate reference's recorded recall accounts for a shortfall of a
#: few per cent, and nothing near a doubling.
EXTENT_FACTOR = 2.0

#: The `phi_type` axis's residual bucket, which `config/naming.yaml` describes as
#: "shipped by a corpus; not a rule-development target". It gets no row in Figure
#: 5c and no extent mark, because neither statement would be about a type.
#: `check_residual_type()` holds the name to the axis.
RESIDUAL_TYPE = "OTHER"

MM = 1 / 25.4


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def naming() -> dict:
    return yaml.safe_load((REPO / "config" / "naming.yaml").read_text())


def display(corpus: str) -> str:
    """The manuscript's label for a corpus id, or a refusal naming the id."""
    try:
        return DISPLAY_NAME[corpus]
    except KeyError:
        raise SystemExit(
            f"no manuscript label for corpus id {corpus!r}; add it to DISPLAY_NAME "
            "in this file (it is the one place labels are written)"
        ) from None


def layer_display(layer: str) -> str:
    """The manuscript's label for a layer id, or a refusal naming the id."""
    try:
        return LAYER_DISPLAY[layer]
    except KeyError:
        raise SystemExit(
            f"no manuscript label for layer id {layer!r}; add it to LAYER_DISPLAY "
            "in this file (it is the one place labels are written)"
        ) from None


def label_lines(label: str) -> tuple[str, str]:
    """`GraSCCo (de)` -> `('GraSCCo', '(de)')`, for a tick that must stay narrow."""
    m = re.match(r"^(.*?)\s+(\(.*\))$", label)
    return (m.group(1), m.group(2)) if m else (label, "")


def check_display_names() -> None:
    """Every label must key off a declared corpus id, so none can be invented."""
    declared = set(naming()["axes"]["corpus"])
    unknown = sorted(set(DISPLAY_NAME) - declared)
    if unknown:
        raise SystemExit(
            "DISPLAY_NAME keys that are not corpus ids in config/naming.yaml: "
            + ", ".join(unknown)
        )


def check_layer_series() -> None:
    """Same guard for the layer tables, against the `layer` axis.

    Both of them: a label with no series would draw nothing and a series with no
    label would draw with no legend entry, and neither failure names itself.
    """
    declared = set(naming()["axes"]["layer"])
    for what, table in (("LAYER_DISPLAY", LAYER_DISPLAY),
                        ("LAYER_SERIES", LAYER_SERIES)):
        unknown = sorted(set(table) - declared)
        if unknown:
            raise SystemExit(
                f"{what} keys that are not layers in config/naming.yaml: "
                + ", ".join(unknown)
            )
        missing = sorted(declared - set(table))
        if missing:
            raise SystemExit(
                f"layers declared in config/naming.yaml with no entry in {what}: "
                + ", ".join(missing)
            )


def check_residual_type() -> None:
    """The residual bucket must be a declared type, so a rename fails here."""
    declared = naming()["axes"]["phi_type"]
    if RESIDUAL_TYPE not in declared:
        raise SystemExit(
            f"RESIDUAL_TYPE {RESIDUAL_TYPE!r} is not a phi_type in "
            "config/naming.yaml"
        )


def template_glob(template: str) -> str:
    """`results/{corpus}/{detector}/...` -> `results/*/*/...` for discovery."""
    return re.sub(r"\{[a-z_]+\}", "*", template)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


@dataclass(frozen=True)
class Arm:
    """One scored run, as its own `metrics.json` describes it."""

    path: Path
    data: dict

    @property
    def run(self) -> dict:
        return self.data["run"]

    @property
    def corpus(self) -> str:
        return self.run["corpus"]

    @property
    def porting(self) -> str:
        return self.run["porting"]

    @property
    def split(self) -> str:
        return self.run["split"]

    @property
    def calls(self) -> int:
        return self.data["cost"]["llm_calls"]

    @property
    def iterations(self) -> int:
        """1 where the record carries no `termination` block (schema 5 predates it)."""
        return int(self.data.get("termination", {}).get("iterations", 1))

    @property
    def termination_reason(self) -> str | None:
        return self.data.get("termination", {}).get("reason")

    def mode(self, mode: str = HEADLINE_MODE) -> dict:
        return self.data["modes"][mode]

    def leak(self, mode: str = HEADLINE_MODE) -> float:
        return float(self.mode(mode)["leak"]["rate"])

    def denominator(self, mode: str = HEADLINE_MODE) -> int:
        return int(self.mode(mode)["leak"]["denominator"])

    def by_type(self, mode: str = HEADLINE_MODE) -> dict:
        return self.mode(mode)["by_type"]

    def covered(self, mode: str = HEADLINE_MODE) -> dict:
        return self.mode(mode)["complementarity"]["layers"]["covered"]

    def covered_by_type(self, mode: str = HEADLINE_MODE) -> dict:
        return self.mode(mode)["complementarity"]["by_type"]


def discover(paths: dict, key: str) -> list[Arm]:
    found = sorted(REPO.glob(template_glob(paths[key])))
    return [Arm(p, load_json(p)) for p in found]


def one(arms: list[Arm], what: str) -> Arm:
    """Refuse an ambiguous selection rather than pick by order on disk."""
    if len(arms) != 1:
        raise SystemExit(
            f"expected exactly one {what}, found {len(arms)}: "
            + ", ".join(str(a.path.relative_to(REPO)) for a in arms)
        )
    return arms[0]


def arm_path(paths: dict, key: str, arm: Arm, **extra) -> Path:
    return REPO / paths[key].format(
        corpus=arm.run["corpus"],
        detector=arm.run["detector"],
        supervision=arm.run["supervision"],
        porting=arm.porting,
        **extra,
    )


# --------------------------------------------------------------------------- #
# the iterating arm, and the single call its round 1 shared a prompt with
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Loop:
    """The iterating arm with everything hung off it that the figures need."""

    arm: Arm
    oneshot: Arm
    rounds: list[Arm]
    sealed: Arm

    @property
    def n(self) -> int:
        return len(self.rounds)

    @property
    def xs(self) -> list[int]:
        return list(range(1, self.n + 1))

    def series(self, mode: str = HEADLINE_MODE) -> list[float]:
        return [a.leak(mode) for a in self.rounds]

    @property
    def delta(self) -> float:
        """The one observed difference between two runs of identical prompt bytes.

        DESIGN §3: the single-call arm's prompt was byte-identical to round 1's,
        so these two records are a pair at n = 2. It is one observation and not
        an estimate of spread, which is why it is drawn as a bracket between
        exactly those two points and as a band only on the difference panel,
        where what it bounds is a difference of the same kind.
        """
        return abs(self.rounds[0].leak() - self.oneshot.leak())

    @property
    def gains(self) -> dict[int, float]:
        """Recorded gain per round, keyed by the round it was measured at.

        `termination.improvements[i]` is the gain from round i + 1 to round
        i + 2, so the list is read against the rounds and not re-differenced
        here: the orchestrator's own numbers are what the pre-registered rule
        was applied to.
        """
        improvements = self.arm.data["termination"]["improvements"]
        return {i + 2: float(g) for i, g in enumerate(improvements)}

    @property
    def resolved(self) -> dict[int, float]:
        """The rounds whose gain the pair at n = 2 can tell apart from nothing."""
        return {r: g for r, g in self.gains.items() if abs(g) > self.delta}

    @property
    def regression(self) -> int | None:
        """The round that went backwards, if one did."""
        worse = [r for r, g in self.gains.items() if g < 0]
        return min(worse) if worse else None


def dev_arms(paths: dict) -> list[Arm]:
    return [a for a in discover(paths, "metrics") if a.split == "dev"]


def loop_of(paths: dict) -> Loop:
    arms = dev_arms(paths)
    arm = one([a for a in arms if a.iterations > 1], "iterating arm")
    oneshot = one(
        [a for a in arms if a.corpus == arm.corpus and a.iterations == 1],
        f"single-call arm on {arm.corpus}",
    )
    rounds = [
        Arm(p, load_json(p))
        for p in (
            arm_path(paths, "itermetrics", arm, iteration=i)
            for i in range(1, arm.iterations + 1)
        )
    ]
    sealed_path = arm_path(paths, "sealedmetrics", arm)
    return Loop(arm, oneshot, rounds, Arm(sealed_path, load_json(sealed_path)))


def reference_kind(corpus: str) -> str:
    split = load_json(REPO / "splits" / f"{corpus}.json")
    return split.get("corpus_specific", {}).get("reference", HUMAN_REFERENCE)


# --------------------------------------------------------------------------- #
# the two denied paths: the Auditor's reports and the arm's call log
# --------------------------------------------------------------------------- #


def audit_reports(paths: dict, loop: Loop) -> dict[int, dict]:
    """Each round's Auditor report, where one was produced.

    A clone of this repository has none of these: the path is deny-listed, so
    the figure is drawn where the arm ran and not from the checkout. A missing
    round is omitted rather than filled, and `figure_four` says how many it drew.
    """
    found = {}
    for i in range(1, loop.n + 1):
        p = arm_path(paths, "auditreport", loop.arm, iteration=i)
        if p.exists():
            found[i] = load_json(p)
    return found


def call_log_total(paths: dict, loop: Loop) -> dict | None:
    """What the arm actually spent, summed over the call log.

    The log is one line per call, carrying a cost block and hashes; it holds no
    prompt or response text. Published spend is in `cost_to_date`, and the
    difference between the two is the expenditure that bought nothing — which no
    `metrics.json` records in full, because round 5's dead attempts predate the
    `abandoned_spend` field. Returns None where the log is absent.
    """
    p = arm_path(paths, "agentlog", loop.arm)
    if not p.exists():
        return None
    total = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "wall_seconds": 0.0}
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        cost = json.loads(line)["cost"]
        for key in total:
            total[key] += cost[key]
    return total


def tokens(cost: dict) -> int:
    """Prompt and completion together: what a token multiple is a multiple of."""
    return int(cost["prompt_tokens"]) + int(cost["completion_tokens"])


# --------------------------------------------------------------------------- #
# figure 2 — iterative refinement: the trajectory, and what each layer covered
# --------------------------------------------------------------------------- #


def figure_two(paths: dict, out: Path) -> dict:
    loop = loop_of(paths)
    xs, fc, rx = loop.xs, loop.series(HEADLINE_MODE), loop.series(BOUND_MODE)
    delta = loop.delta

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(300 * MM, 105 * MM))

    # ── (a) the trajectory ────────────────────────────────────────────────── #
    ax.fill_between(xs, rx, fc, color=BOUND_COLOUR, alpha=0.25, zorder=1)
    ax.plot(xs, fc, color=HEADLINE_COLOUR, marker="o", markersize=6,
            linewidth=2.0, label=f"{HEADLINE_MODE} (headline)", zorder=3)
    ax.plot(xs, rx, color=BOUND_COLOUR, linestyle="--", marker="s",
            markersize=5, linewidth=1.8, label=f"{BOUND_MODE} (lower bound)",
            zorder=3)

    single_x = xs[0] - 0.42
    single_fc, single_rx = loop.oneshot.leak(), loop.oneshot.leak(BOUND_MODE)
    ax.plot([single_x], [single_fc], linestyle="none", marker="^",
            markersize=11, color="black", label="single call", zorder=4)
    ax.plot([single_x], [single_rx], linestyle="none", marker="^",
            markersize=11, markerfacecolor="white", markeredgecolor="black",
            markeredgewidth=1.3, zorder=4)

    sealed_x, sealed_fc = xs[-1] + 0.42, loop.sealed.leak()
    ax.plot([sealed_x], [sealed_fc], linestyle="none", marker="*",
            markersize=19, markerfacecolor=SEALED_COLOUR,
            markeredgecolor="black", markeredgewidth=1.0,
            label=f"sealed test fold (round-{loop.n} rules)", zorder=5)
    ax.annotate(f"{sealed_fc:.3f}", xy=(sealed_x, sealed_fc),
                xytext=(sealed_x - 0.25, sealed_fc + 0.085), fontsize=8.5,
                ha="center",
                arrowprops=dict(arrowstyle="-", linewidth=0.7, color="0.35",
                                shrinkB=8))

    # The bracket spans exactly the two points it is about. Its label sits in the
    # empty band above, because both series are below the bracket from round 2 on.
    brx, cap = single_x - 0.22, 0.055
    lo, hi = sorted((fc[0], single_fc))
    ax.plot([brx, brx], [lo, hi], color=WORSE, linewidth=1.4, zorder=3)
    for y in (lo, hi):
        ax.plot([brx, brx + cap], [y, y], color=WORSE, linewidth=1.4, zorder=3)
    ax.annotate(
        f"identical prompt bytes,\ntwo runs: Δ = {delta:.3f} (n = 2)",
        xy=(brx, hi), xytext=(xs[0] + 0.15, hi + 0.055),
        fontsize=8.5, color=WORSE, ha="left", va="bottom",
        arrowprops=dict(arrowstyle="-", linewidth=1.0, color=WORSE, shrinkB=2),
    )

    # An arrow only where the pair at n = 2 can tell the change from nothing, and
    # pointing the way the change went. Which rounds those are is measured.
    for r, gain in loop.resolved.items():
        ax.annotate(
            "", xy=(r - 0.1, fc[r - 1]), xytext=(r - 0.1, fc[r - 2]),
            arrowprops=dict(arrowstyle="-|>", linewidth=1.4, shrinkA=2, shrinkB=2,
                            color=BETTER if gain > 0 else WORSE),
        )

    converged = bool(loop.arm.data["termination"]["converged"])
    ceiling = loop.arm.data["termination"]["ceiling"]
    ax.axvline(xs[-1], color="0.45", linestyle=":", linewidth=1.0, zorder=1)
    ax.text(xs[-1] - 0.15, max(fc) * 0.56,
            f"stop: {loop.arm.termination_reason} K = {ceiling}\n"
            f"({'converged' if converged else 'not convergence'})",
            fontsize=8.5, color="0.35", ha="right", va="bottom")

    ax.set_xlabel("round $t$")
    ax.set_ylabel(f"leak rate $L$ (development fold, "
                  f"n = {loop.arm.denominator():,})")
    ax.set_xticks(xs)
    ax.set_xlim(brx - 0.2, sealed_x + 0.35)
    ax.set_ylim(0, max(fc + [single_fc]) + 0.18)
    ax.grid(axis="y", linestyle="-", linewidth=0.5, color="0.88")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8.5, loc="upper right", framealpha=1.0)
    ax.annotate("a", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-42, 10), textcoords="offset points",
                fontsize=13, fontweight="bold")

    # ── (b) what each layer covered, round by round ───────────────────────── #
    # The single call sits at x = 0 as the series' own starting point: it is the
    # same detector configured by a rule file written without feedback.
    bxs = list(range(0, loop.n + 1))
    series = {}
    for layer in LAYER_SERIES:
        counts = [loop.oneshot.covered().get(layer, 0)]
        counts += [a.covered().get(layer, 0) for a in loop.rounds]
        series[layer] = counts

    # A layer that covered nothing anywhere is left out, which is itself a claim
    # and is checked as one (`check_covered_by_layer` in the manuscript checker).
    drawn = [layer for layer, counts in series.items() if any(counts)]
    drawn.sort(key=lambda layer: series[layer][-1], reverse=True)
    for layer in drawn:
        colour, marker = LAYER_SERIES[layer]
        bx.plot(bxs, series[layer], color=colour, marker=marker, markersize=6,
                linewidth=1.8, label=layer_display(layer), zorder=3)

    if loop.regression is not None:
        r = loop.regression
        bx.axvspan(r - 0.4, r + 0.4, color=WORSE, alpha=0.10, zorder=0)
        bx.text(r, max(max(c) for c in series.values()) * 1.02,
                f"round {r}\nregression", fontsize=8.5, color=WORSE,
                ha="center", va="bottom")

    # The step to annotate is the largest single-round gain of the layer that
    # covered nothing in round 1 — measured, not named, and the type that
    # accounts for most of it is measured the same way.
    late = min(drawn, key=lambda layer: series[layer][1])
    steps = [(series[late][i] - series[late][i - 1], i) for i in range(2, loop.n + 1)]
    gain, at = max(steps)
    if gain > 0:
        before = loop.rounds[at - 2].covered_by_type()
        after = loop.rounds[at - 1].covered_by_type()
        per_type = {
            t: after[t]["layers"]["covered"].get(late, 0)
               - before.get(t, {}).get("layers", {}).get("covered", {}).get(late, 0)
            for t in after
        }
        driver = max(per_type, key=lambda t: per_type[t])
        bx.annotate(
            f"{layer_display(late)} step\n"
            f"{series[late][at - 1]} → {series[late][at]}\n"
            f"({per_type[driver]} {driver})",
            xy=(at, series[late][at]), xytext=(at + 0.5, series[late][at] + gain * 1.5),
            fontsize=8.5, color=LAYER_SERIES[late][0], ha="left",
            arrowprops=dict(arrowstyle="->", linewidth=0.9,
                            color=LAYER_SERIES[late][0], shrinkB=5),
        )

    bx.set_xlabel("round $t$")
    bx.set_ylabel(f"gold spans covered ({HEADLINE_MODE})")
    bx.set_xticks(bxs)
    bx.set_xticklabels(["single\ncall"] + [str(x) for x in loop.xs], fontsize=9)
    bx.set_ylim(0, max(max(c) for c in series.values()) * 1.18)
    bx.grid(axis="y", linestyle="-", linewidth=0.5, color="0.88")
    bx.set_axisbelow(True)
    bx.legend(fontsize=8.5, loc="center right", framealpha=1.0)
    bx.annotate("b", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-46, 10), textcoords="offset points",
                fontsize=13, fontweight="bold")

    save(fig, out, "fig2_iteration")

    return {
        "corpus": loop.arm.corpus,
        "rounds": loop.n,
        "dev_n": loop.arm.denominator(),
        "delta": delta,
        "resolved": sorted(loop.resolved),
        "regression": loop.regression,
        "layers": drawn,
        "omitted_layers": [layer for layer in series if layer not in drawn],
        "step": {"layer": late, "round": at, "gain": gain, "type": driver}
                if gain > 0 else None,
        "sealed": loop.sealed.leak(),
        "rules_version": loop.arm.run["rules_version"],
    }


# --------------------------------------------------------------------------- #
# figure 3 — the sealed fold against development, per type
# --------------------------------------------------------------------------- #


def figure_three(paths: dict, out: Path) -> dict:
    loop = loop_of(paths)
    dev_types, test_types = loop.arm.by_type(), loop.sealed.by_type()
    delta = loop.delta

    # §9.4: a type the scorer flagged sparse on *either* fold is dropped and the
    # omission is stated. The flag is the scorer's, read from the file.
    kept, dropped = [], []
    for t in sorted(set(dev_types) | set(test_types)):
        d, s = dev_types.get(t), test_types.get(t)
        if d is None or s is None or not d["gold"] or not s["gold"]:
            dropped.append((t, "absent on one fold"))
        elif d["sparse"] or s["sparse"]:
            dropped.append((t, "sparse"))
        else:
            kept.append(t)

    # Ordered by the development fold, highest at the top, so that the order is
    # one the rules were written with sight of and the sealed fold is only ever
    # read against it. Both panels use it, so they read down together.
    kept.sort(key=lambda t: dev_types[t]["leak_rate"], reverse=True)
    diffs = {t: test_types[t]["leak_rate"] - dev_types[t]["leak_rate"] for t in kept}
    # The types whose movement the pair at n = 2 can tell from nothing, measured.
    resolved = [t for t in kept if abs(diffs[t]) > delta]

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(300 * MM, 110 * MM),
                                 gridspec_kw={"width_ratios": [1.45, 1.0]})
    ys = list(range(len(kept)))

    # ── (a) the pair of points per type ───────────────────────────────────── #
    for y, t in zip(ys, kept):
        dv, tv = dev_types[t]["leak_rate"], test_types[t]["leak_rate"]
        if t in resolved:
            ax.plot([dv, tv], [y, y], color=WORSE if diffs[t] > 0 else BETTER,
                    linewidth=3.0, solid_capstyle="butt", zorder=2)
            ax.annotate(f"{diffs[t]:+.3f}", xy=(max(dv, tv), y),
                        xytext=(8, 0), textcoords="offset points",
                        va="center", fontsize=9, fontweight="bold",
                        color=WORSE if diffs[t] > 0 else BETTER)
        ax.plot([dv], [y], linestyle="none", marker="o", markersize=9,
                markerfacecolor="white", markeredgecolor="black",
                markeredgewidth=1.4, zorder=3)
        ax.plot([tv], [y], linestyle="none", marker="X", markersize=10,
                color="black", zorder=4)

    # The two aggregates are a few thousandths apart, so each label goes on the
    # side of its own line that the other is not on.
    for rate, colour, name, side in (
        (loop.arm.leak(), HEADLINE_COLOUR, "dev", "right"),
        (loop.sealed.leak(), SEALED_COLOUR, "test", "left"),
    ):
        ax.axvline(rate, color=colour, linestyle=":", linewidth=1.3, zorder=1)
        # Above the top row: the y axis is inverted, so that is the low end.
        ax.annotate(f"{name} {rate:.3f}", xy=(rate, -0.62),
                    xytext=(-3 if side == "right" else 3, 3),
                    textcoords="offset points",
                    ha=side, va="bottom", fontsize=8.5, color=colour)

    ax.set_yticks(ys)
    ax.set_yticklabels(
        [f"{t}\n{dev_types[t]['gold']:,} / {test_types[t]['gold']:,}" for t in kept],
        fontsize=9,
    )
    # Inverted, with a row of headroom at the top for the aggregate labels. Panel
    # (b) takes the same limits, so the two panels' rows line up.
    ax.set_ylim(len(kept) - 0.5, -1.05)
    ax.set_xlabel(f"leak rate, {HEADLINE_MODE}")
    ax.set_xlim(0, 1.0)
    ax.grid(axis="x", linestyle="-", linewidth=0.5, color="0.88")
    ax.set_axisbelow(True)
    ax.legend(
        handles=[
            Line2D([], [], linestyle="none", marker="o", markerfacecolor="white",
                   markeredgecolor="black", markersize=9, label="development fold"),
            Line2D([], [], linestyle="none", marker="X", color="black",
                   markersize=10, label="sealed test fold"),
            Line2D([], [], color=WORSE, linewidth=3.0,
                   label="increase > Δ"),
        ],
        fontsize=9, loc="lower right", framealpha=1.0,
    )
    ax.annotate("a", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-80, 12), textcoords="offset points",
                fontsize=13, fontweight="bold")

    # ── (b) the difference, against the band the pair at n = 2 sets ───────── #
    # Here the band is legitimate: it bounds a difference, which is the same kind
    # of quantity the pair at n = 2 measured.
    bx.axvspan(-delta, delta, color="0.90", zorder=0)
    bx.axvline(0, color="black", linewidth=0.9, zorder=2)
    for y, t in zip(ys, kept):
        d = diffs[t]
        colour = BETTER if d < 0 else (WORSE if d > delta else WITHIN)
        bx.barh(y, d, height=0.62, color=colour, edgecolor="black",
                linewidth=0.6, zorder=3)
        bx.annotate(f"{d:+.3f}", xy=(d, y),
                    xytext=(5 if d >= 0 else -5, 0), textcoords="offset points",
                    ha="left" if d >= 0 else "right", va="center", fontsize=9,
                    fontweight="bold" if t in resolved else "normal",
                    color=colour if t in resolved else "black")

    bx.set_yticks(ys)
    bx.set_yticklabels([])
    bx.set_ylim(len(kept) - 0.5, -1.05)
    bx.set_xlabel("test − development")
    span = max(abs(d) for d in diffs.values())
    bx.set_xlim(-delta * 1.6, span * 1.28)
    bx.text(0, -0.62, "±Δ", ha="center", va="bottom", fontsize=9, color="0.35")
    bx.grid(axis="x", linestyle="-", linewidth=0.5, color="0.88")
    bx.set_axisbelow(True)
    bx.annotate("b", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-16, 12), textcoords="offset points",
                fontsize=13, fontweight="bold")

    save(fig, out, "fig3_sealed")

    return {
        "corpus": loop.arm.corpus,
        "rounds": loop.n,
        "kept": kept,
        "dropped": dropped,
        "delta": delta,
        "resolved": resolved,
        "dev": {t: dev_types[t]["leak_rate"] for t in kept},
        "test": {t: test_types[t]["leak_rate"] for t in kept},
        "gold": {t: (dev_types[t]["gold"], test_types[t]["gold"]) for t in kept},
        "dev_aggregate": loop.arm.leak(),
        "test_aggregate": loop.sealed.leak(),
    }


# --------------------------------------------------------------------------- #
# figure 4 — what the rounds cost, and what the Auditor's output was worth
# --------------------------------------------------------------------------- #


def figure_four(paths: dict, out: Path) -> dict:
    loop = loop_of(paths)
    fc = loop.series()

    # Cumulative published calls, accumulated from each round's own record rather
    # than from a per-round rate: round 1 has no previous output to audit and
    # recorded one call where the other seven recorded 1 + 250, so `251 * rounds`
    # would put 2,008 on an axis whose first point is the claim that the single
    # call and round 1 cost the same.
    cumulative, running = [], 0
    for a in loop.rounds:
        running += a.calls
        cumulative.append(running)

    published = loop.arm.data["cost_to_date"]
    actual = call_log_total(paths, loop)
    baseline = loop.oneshot.data["cost"]

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(300 * MM, 105 * MM))

    # ── (a) leak rate against cumulative calls ────────────────────────────── #
    ax.plot(cumulative, fc, color=HEADLINE_COLOUR, marker="o", markersize=6,
            linewidth=2.0, label="iterative arm, round $t$", zorder=3)
    for x, y, r in zip(cumulative, fc, loop.xs):
        ax.annotate(str(r), xy=(x, y), xytext=(4, 5), textcoords="offset points",
                    fontsize=8.5, color=HEADLINE_COLOUR)
    ax.plot([baseline["llm_calls"]], [loop.oneshot.leak()], linestyle="none",
            marker="^", markersize=12, color="black", label="single call", zorder=4)

    ax.annotate(
        f"cost parity: round 1 is\n{loop.delta:.3f} worse than the\nsingle call",
        xy=(cumulative[0], fc[0]),
        xytext=(cumulative[0] * 1.6, fc[0] - 0.26),
        fontsize=8.5, ha="left",
        arrowprops=dict(arrowstyle="-", linewidth=0.8, color="0.25", shrinkB=4),
    )

    # The two totals are close enough on a log axis that their labels would sit on
    # top of each other, so each goes on the side of its own line that the other
    # is not on: published to the left, actual to the right.
    top = max(fc + [loop.oneshot.leak()])
    for total, colour, side, text in (
        (published, "0.35", "right",
         f"{published['llm_calls']:,} calls\n"
         f"{tokens(published) / tokens(baseline):,.0f}× tokens"),
        (actual, WORSE, "left",
         None if actual is None else
         f"{actual['llm_calls']:,} incl.\nabandoned\n"
         f"({tokens(actual) / tokens(baseline):,.0f}×)"),
    ):
        if total is None:
            continue
        ax.axvline(total["llm_calls"], color=colour, linestyle=":", linewidth=1.1,
                   zorder=1)
        ax.annotate(text, xy=(total["llm_calls"], top),
                    xytext=(-5 if side == "right" else 5, -2),
                    textcoords="offset points", fontsize=8.5, color=colour,
                    ha=side, va="top")
    if actual is not None:
        ax.set_xlim(right=actual["llm_calls"] * 2.4)

    ax.set_xscale("log")
    ax.set_xlabel("cumulative model calls (log scale)")
    ax.set_ylabel(f"leak rate, {HEADLINE_MODE}")
    ax.set_ylim(0, top * 1.14)
    ax.grid(linestyle="-", linewidth=0.5, color="0.88")
    ax.set_axisbelow(True)
    ax.legend(fontsize=8.5, loc="lower left", framealpha=1.0)
    ax.annotate("a", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-48, 10), textcoords="offset points",
                fontsize=13, fontweight="bold")

    # ── (b) how much of the Auditor's output survived ─────────────────────── #
    reports = audit_reports(paths, loop)
    yielded = {}
    for r, report in sorted(reports.items()):
        audited = int(report["documents_audited"])
        yielded[r] = {
            "audited": audited,
            "with_flag": audited - int(report["documents_with_no_flags"]),
            "malformed": int(report["counts"]["by_refusal"]["malformed"]),
        }

    if yielded:
        rs = sorted(yielded)
        per_round = {y["audited"] for y in yielded.values()}
        if len(per_round) != 1:
            raise SystemExit(
                "the Auditor audited a different number of documents in "
                "different rounds, so the bars have no common denominator: "
                + ", ".join(f"round {r}: {yielded[r]['audited']}" for r in rs)
            )
        audited = per_round.pop()
        bars = [yielded[r]["with_flag"] for r in rs]
        bx.bar(rs, bars, width=0.66, color=BETTER, edgecolor="black",
               linewidth=0.8, zorder=3,
               label="documents with ≥1 surviving flag")
        for r, v in zip(rs, bars):
            bx.annotate(f"{v}", xy=(r, v), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=8.5)

        cx = bx.twinx()
        share = [100.0 * yielded[r]["malformed"] / audited for r in rs]
        cx.plot(rs, share, color=WORSE, marker="s", markersize=6, linewidth=1.8,
                zorder=4, label=f"malformed responses (% of {audited})")
        cx.set_ylabel("malformed (%)", color=WORSE)
        cx.tick_params(axis="y", colors=WORSE)
        cx.set_ylim(0, 100)

        calls = sum(y["audited"] for y in yielded.values())
        kept_total = sum(y["with_flag"] for y in yielded.values())
        bx.annotate(
            f"{kept_total:,} / {calls:,} calls\nreturned a surviving\n"
            f"flag ({100.0 * kept_total / calls:.1f}%)",
            xy=(1.0, 1.0), xycoords="axes fraction", xytext=(-6, -6),
            textcoords="offset points", ha="right", va="top", fontsize=8.5,
        )

        bx.set_xticks(rs)
        bx.set_ylim(0, audited)
        bx.set_ylabel(f"documents (of {audited} per round)")
        handles = bx.get_legend_handles_labels()
        twin = cx.get_legend_handles_labels()
        fig.legend(handles[0] + twin[0], handles[1] + twin[1],
                   fontsize=9, loc="lower center", ncol=2, frameon=False,
                   bbox_to_anchor=(0.75, -0.04))
    else:
        bx.text(0.5, 0.5, "no Auditor report on this filesystem\n"
                          "(the path is denied; see the module docstring)",
                transform=bx.transAxes, ha="center", va="center", fontsize=9,
                color="0.35")

    bx.set_xlabel("round $t$")
    bx.grid(axis="y", linestyle="-", linewidth=0.5, color="0.88")
    bx.set_axisbelow(True)
    bx.annotate("b", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-48, 10), textcoords="offset points",
                fontsize=13, fontweight="bold")

    save(fig, out, "fig4_cost_audit")

    # Nothing from either denied path goes into this summary: the Auditor's own
    # numbers are the figure's content and not the terminal's (rule 4 above).
    return {
        "corpus": loop.arm.corpus,
        "rounds": loop.n,
        "cumulative_published": cumulative,
        "published_calls": published["llm_calls"],
        "token_multiple": tokens(published) / tokens(baseline),
        "log_read": actual is not None,
        "rounds_audited": len(yielded),
    }


# --------------------------------------------------------------------------- #
# figure 5 — one authoring call on each of the five corpora
# --------------------------------------------------------------------------- #


def single_call_arms(paths: dict) -> list[Arm]:
    """The non-iterating arm of each corpus, lowest leak rate first."""
    by_corpus: dict[str, list[Arm]] = {}
    for arm in (a for a in dev_arms(paths) if a.iterations == 1):
        by_corpus.setdefault(arm.corpus, []).append(arm)
    arms = [one(v, f"single-call arm on {k}") for k, v in by_corpus.items()]
    arms.sort(key=lambda a: a.leak())
    return arms


def shared_fold_pair(arms: list[Arm], fold: str = "dev") -> tuple[str, str] | None:
    """Two corpora whose frozen fold lists the same documents, if there are two.

    This is what licenses Figure 5c's extent mark: where the documents are
    identical, a disagreement about how many spans of a type they contain is a
    disagreement between the references and not between the corpora.
    """
    ids = {}
    for arm in arms:
        split = load_json(REPO / "splits" / f"{arm.corpus}.json")
        ids[arm.corpus] = tuple(split["folds"][fold]["document_ids"])
    pairs = [
        (a, b)
        for i, a in enumerate(sorted(ids))
        for b in sorted(ids)[i + 1:]
        if ids[a] == ids[b]
    ]
    if len(pairs) > 1:
        raise SystemExit(
            "more than one pair of corpora shares a fold document for document, "
            "so the extent mark has no single basis: "
            + ", ".join(f"{a}/{b}" for a, b in pairs)
        )
    return pairs[0] if pairs else None


def extent_marked(arms: list[Arm], pair: tuple[str, str] | None) -> list[str]:
    """Types the shared-document pair's two references count differently."""
    if pair is None:
        return []
    a, b = (next(x for x in arms if x.corpus == c) for c in pair)
    ga, gb = a.by_type(), b.by_type()
    marked = []
    for t in sorted((set(ga) | set(gb)) - {RESIDUAL_TYPE}):
        na = int(ga.get(t, {}).get("gold", 0))
        nb = int(gb.get(t, {}).get("gold", 0))
        if na == nb == 0:
            continue
        if min(na, nb) == 0 or max(na, nb) / min(na, nb) >= EXTENT_FACTOR:
            marked.append(t)
    return marked


def figure_five(paths: dict, out: Path) -> dict:
    arms = single_call_arms(paths)
    refs = {a.corpus: reference_kind(a.corpus) for a in arms}
    kinds = sorted(set(refs.values()), key=lambda k: k != HUMAN_REFERENCE)
    hatch_for = {k: ("" if k == HUMAN_REFERENCE else "///") for k in kinds}

    # A deviating arm gets a mark under its own tick; what the mark means is said
    # in the caption, which can name an arm id and a design section as the figure
    # cannot. Which arms deviate is read off the records.
    modal_porting = max({a.porting for a in arms},
                        key=lambda p: sum(a.porting == p for a in arms))
    modal_calls = max({a.calls for a in arms},
                      key=lambda c: sum(a.calls == c for a in arms))
    marks, footnotes = {}, {}
    for arm in arms:
        m = ""
        if arm.porting != modal_porting:
            m += "†"
            footnotes["†"] = arm.corpus
        if arm.calls != modal_calls:
            m += "‡"
            footnotes["‡"] = arm.corpus
        marks[arm.corpus] = m

    def tick(arm: Arm, with_n: bool = True) -> str:
        name, langs = label_lines(display(arm.corpus))
        m = marks[arm.corpus]
        line = f"{name}\n{langs}{m}"
        return f"{line}\nn = {arm.denominator():,}" if with_n else line

    fig = plt.figure(figsize=(320 * MM, 205 * MM))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.25], hspace=0.42,
                            wspace=0.20)
    ax, bx = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])
    cx = fig.add_subplot(grid[1, :])
    xs = list(range(len(arms)))

    # ── (a) the aggregate rate per corpus ─────────────────────────────────── #
    for x, arm in zip(xs, arms):
        kind = refs[arm.corpus]
        ax.bar(x, arm.leak(), width=0.62, facecolor=BAR_FILL,
               edgecolor="black", linewidth=1.1, hatch=hatch_for[kind], zorder=2)
        ax.plot([x], [arm.leak(BOUND_MODE)], linestyle="none", marker="D",
                markersize=7, markerfacecolor="white", markeredgecolor="black",
                markeredgewidth=1.3, zorder=4)
        ax.text(x, arm.leak() + 0.022, f"{arm.leak():.3f}", ha="center",
                va="bottom", fontsize=9)

    ax.set_xticks(xs)
    ax.set_xticklabels([tick(a) for a in arms], fontsize=8)
    ax.set_ylabel("leak rate, development fold")
    ax.set_ylim(0, 1.0)
    ax.grid(axis="y", linestyle="-", linewidth=0.5, color="0.88")
    ax.set_axisbelow(True)
    ax.legend(
        handles=[
            Patch(facecolor=BAR_FILL, edgecolor="black", hatch=hatch_for[k],
                  label=f"{HEADLINE_MODE}, {k}")
            for k in kinds
        ] + [
            Line2D([], [], linestyle="none", marker="D", markerfacecolor="white",
                   markeredgecolor="black", markersize=7, label=BOUND_MODE),
        ],
        fontsize=8.5, loc="upper left", framealpha=1.0,
    )
    ax.annotate("a", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-46, 12), textcoords="offset points",
                fontsize=13, fontweight="bold")

    # ── (b) the share of gold each layer covered ──────────────────────────── #
    shares = {
        layer: [a.covered().get(layer, 0) / a.denominator() for a in arms]
        for layer in LAYER_SERIES
    }
    drawn = [layer for layer, v in shares.items() if any(v)]
    width = 0.8 / len(drawn)
    for i, layer in enumerate(drawn):
        colour, _ = LAYER_SERIES[layer]
        offset = (i - (len(drawn) - 1) / 2) * width
        bx.bar([x + offset for x in xs], shares[layer], width=width * 0.92,
               color=colour, edgecolor="black", linewidth=0.7,
               label=layer_display(layer), zorder=3)

    # The corpus whose rule file contained no rule of a layer is pointed at, and
    # which corpus that is comes off the records.
    for layer in drawn:
        empty = [i for i, v in enumerate(shares[layer]) if v == 0]
        if len(empty) == 1:
            i = empty[0]
            bx.annotate(
                f"no {layer_display(layer)}\nwritten",
                xy=(xs[i] + (drawn.index(layer) - (len(drawn) - 1) / 2) * width, 0),
                xytext=(-26, 86), textcoords="offset points",
                fontsize=8.5, ha="center",
                arrowprops=dict(arrowstyle="->", linewidth=0.9, color="black"),
            )
            break

    bx.set_xticks(xs)
    bx.set_xticklabels([tick(a, with_n=False) for a in arms], fontsize=8)
    bx.set_ylabel("share of dev gold covered by layer")
    bx.grid(axis="y", linestyle="-", linewidth=0.5, color="0.88")
    bx.set_axisbelow(True)
    bx.legend(fontsize=8.5, loc="upper right", framealpha=1.0)
    bx.annotate("b", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-46, 12), textcoords="offset points",
                fontsize=13, fontweight="bold")

    # ── (c) the per-type rate, corpus by corpus ───────────────────────────── #
    pair = shared_fold_pair(arms)
    marked = extent_marked(arms, pair)
    tables = {a.corpus: a.by_type() for a in arms}

    def usable(t: str) -> int:
        return sum(
            1 for a in arms
            if t in tables[a.corpus]
            and not tables[a.corpus][t]["sparse"]
            and tables[a.corpus][t]["leak_rate"] is not None
        )

    def gold(t: str) -> int:
        return sum(int(tables[a.corpus].get(t, {}).get("gold", 0)) for a in arms)

    # Rows a reader may compare come first, so the figure reads top-down from the
    # comparable to the marked; within each group, the types the most corpora give
    # a usable number for come first. The residual bucket is not a row.
    types = sorted(
        {t for table in tables.values() for t in table} - {RESIDUAL_TYPE},
        key=lambda t: (t in marked, -usable(t), -gold(t)),
    )

    rates = np.full((len(types), len(arms)), np.nan)
    for row, t in enumerate(types):
        for col, arm in enumerate(arms):
            cell = tables[arm.corpus].get(t)
            if cell is None or cell["leak_rate"] is None or cell["sparse"]:
                continue
            rates[row, col] = cell["leak_rate"]

    image = cx.imshow(np.ma.masked_invalid(rates), cmap="YlOrRd", vmin=0.0,
                      vmax=1.0, aspect="auto")
    image.cmap.set_bad("white")

    for row, t in enumerate(types):
        for col, arm in enumerate(arms):
            cell = tables[arm.corpus].get(t)
            if cell is not None and cell["sparse"]:
                cx.add_patch(Rectangle((col - 0.5, row - 0.5), 1, 1,
                                       facecolor="0.88", edgecolor="white",
                                       hatch="....", linewidth=0.8, zorder=2))
                cx.text(col, row, f"sparse (n ≤ {SPARSE_MAX})", ha="center",
                        va="center", fontsize=8, color="0.3", zorder=3)
            elif cell is None or cell["leak_rate"] is None:
                cx.text(col, row, "not annotated", ha="center", va="center",
                        fontsize=8, color="0.55", zorder=3)
            else:
                value = cell["leak_rate"]
                cx.text(col, row, f"{value:.3f}", ha="center", va="center",
                        fontsize=9, zorder=3,
                        color="white" if value > 0.72 else "black")

    # The column whose reference is not human gold is boxed and said to be so,
    # because every comparison across that boundary is a comparison of kinds.
    for col, arm in enumerate(arms):
        if refs[arm.corpus] == HUMAN_REFERENCE:
            continue
        cx.add_patch(Rectangle((col - 0.5, -0.5), 1, len(types),
                               fill=False, edgecolor="black", linewidth=2.2,
                               linestyle=(0, (5, 3)), zorder=5))
        cx.annotate("surrogate reference", xy=(col, -0.5), xytext=(0, 8),
                    textcoords="offset points", ha="center", fontsize=9)

    cx.set_xticks(xs)
    cx.set_xticklabels([display(a.corpus) + marks[a.corpus] for a in arms],
                       fontsize=9)
    cx.set_yticks(range(len(types)))
    cx.set_yticklabels(
        [f"{t}  ‖" if t in marked else t for t in types], fontsize=9,
    )
    cx.set_xticks(np.arange(-0.5, len(arms), 1), minor=True)
    cx.set_yticks(np.arange(-0.5, len(types), 1), minor=True)
    cx.grid(which="minor", color="white", linewidth=1.2)
    cx.tick_params(which="minor", length=0)
    bar = fig.colorbar(image, ax=cx, fraction=0.016, pad=0.015)
    bar.set_label(f"leak rate, {HEADLINE_MODE}", fontsize=9)
    cx.annotate("c", xy=(0.0, 1.0), xycoords="axes fraction",
                xytext=(-108, 22), textcoords="offset points",
                fontsize=13, fontweight="bold")

    save(fig, out, "fig5_five_corpora", tight=False)

    return {
        "order": [a.corpus for a in arms],
        "marks": footnotes,
        "fc": {a.corpus: a.leak() for a in arms},
        "rx": {a.corpus: a.leak(BOUND_MODE) for a in arms},
        "n": {a.corpus: a.denominator() for a in arms},
        "reference": refs,
        "layers": drawn,
        "omitted_layers": [layer for layer in shares if layer not in drawn],
        "types": types,
        "shared_fold_pair": pair,
        "extent_marked": marked,
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #


def save(fig, out: Path, stem: str, tight: bool = True) -> None:
    # A figure carrying a colourbar has an axes `tight_layout` cannot place, and
    # it warns and guesses rather than failing; those lay themselves out.
    if tight:
        fig.tight_layout()
    fig.savefig(out / f"{stem}.pdf")            # vector
    fig.savefig(out / f"{stem}.png", dpi=300)   # raster, 300 dpi
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", default=str(Path.home() / "Desktop" / "figures"),
        help="output directory; defaults outside the repository (CLAUDE.md)",
    )
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    check_display_names()
    check_layer_series()
    check_residual_type()
    paths = naming()["paths"]
    plt.rcParams.update({
        # The same list, in the same order, as `tools/arch_figure.py`: matplotlib
        # takes the first family it can resolve, so a machine with Arial renders
        # all five figures in one face and a machine with none of the three falls
        # back once rather than per figure. It warns when it falls back.
        "font.family": FONT_STACK,
        "font.size": 9.5,
        "axes.linewidth": 0.8,
        "pdf.fonttype": 42,  # embed TrueType rather than Type 3
        "savefig.bbox": "tight",
    })

    f2 = figure_two(paths, out)
    f3 = figure_three(paths, out)
    f4 = figure_four(paths, out)
    f5 = figure_five(paths, out)

    print(f"wrote 4 figures (pdf + png 300dpi) to {out}")
    print(f"  fig 2: {f2['corpus']}, {f2['rounds']} rounds, "
          f"Δ {f2['delta']:.3f}, arrows at rounds {f2['resolved']}, "
          f"layers {', '.join(f2['layers'])}")
    print(f"  fig 3: {len(f3['kept'])} types, {len(f3['dropped'])} omitted, "
          f"resolved movement: {', '.join(f3['resolved']) or 'none'}")
    print(f"  fig 4: {f4['rounds_audited']} rounds audited, "
          f"{f4['token_multiple']:,.0f}× published tokens"
          f"{'' if f4['log_read'] else ' (no call log on this filesystem)'}")
    print(f"  fig 5: {', '.join(f5['order'])}; "
          f"extent-marked {', '.join(f5['extent_marked']) or 'none'} "
          f"from {'/'.join(f5['shared_fold_pair']) if f5['shared_fold_pair'] else 'no shared fold'}")
    arch = REPO / "tools" / "arch_figure.py"
    print(f"  fig 1 is the architecture diagram; "
          f"{'tools/arch_figure.py draws it' if arch.exists() else 'nothing here draws it'}")


if __name__ == "__main__":
    main()
