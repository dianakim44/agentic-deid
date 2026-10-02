#!/usr/bin/env python3
"""Manuscript figures, built from the recorded numbers and nothing else.

Two rules govern this file and both are checkable by reading it:

1. **No measured value is written here.** Every number plotted is read from a
   `metrics.json` written by `src/eval/scorer.py` or from a frozen
   `splits/{corpus}.json`. That includes the things that look like constants:
   the number of rounds comes from `termination.iterations`, the bracketed
   difference is computed from the two runs that shared a prompt, and the arms
   that appear in Figure 2 are the ones whose records say they did not iterate.
   A reader checking a figure against the files should never find a literal
   here to blame.
2. **No corpus text reaches the output.** Only PHI type names, corpus labels
   and counts are drawn, and type names come from `config/naming.yaml`'s
   `phi_type` axis. Nothing in this file reads a corpus, a `spans.jsonl` or a
   rule file, so no surface form can reach a figure, a caption or an exception
   message (CLAUDE.md).
3. **Nothing inside the figures points back at this repository.** Arm ids,
   schema versions and `docs/DESIGN.md` section numbers stay in the code and in
   the files; the drawn text carries manuscript labels and manuscript section
   numbers only, because a reader of the paper cannot follow the others.

Paths are taken from `config/naming.yaml`'s `paths` block rather than written
out, so a layout change moves the figures with it. Arm coordinates (detector,
supervision, porting) are discovered by globbing those templates and read back
out of each file's own `run` block.

Black-and-white print is the target: fills are greyscale with hatching, lines
differ in dash pattern, and every series has its own marker. Colour is not
load-bearing anywhere.

Usage
-----
    python tools/make_figures.py [--out DIR]

Default output directory is `~/Desktop/figures` — outside the repository,
because figures built from DUA-covered corpora are not committed (CLAUDE.md).

This file draws figures and writes no caption prose. The captions are in the
manuscript, and a second copy of them here would drift from it the moment
either was edited; what replaces that copy is `tools/check_manuscript_numbers.py`,
which reads the manuscript's captions and tables and checks their numbers
against these same files.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
import yaml  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: DESIGN §9.3: the headline leak rate is `fully_covered` and `relaxed` is reported
#: beside it as a lower bound. Both are plotted; neither is derived from the other.
HEADLINE_MODE = "fully_covered"
BOUND_MODE = "relaxed"

#: `splits/*.json` records `corpus_specific.reference` only where the reference is
#: not purely human — ko-surro's `human-verified silver` is the one case (DESIGN
#: §6.5 (v), §9.3). Absence of the key is therefore the human-gold case, and this
#: is a label for the legend, not a measured value.
HUMAN_REFERENCE = "human gold"

#: Corpus labels for drawn text and captions, exactly as the manuscript's Table 5
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

    def leak(self, mode: str = HEADLINE_MODE) -> float:
        return float(self.data["modes"][mode]["leak"]["rate"])

    def by_type(self, mode: str = HEADLINE_MODE) -> dict:
        return self.data["modes"][mode]["by_type"]


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


# --------------------------------------------------------------------------- #
# figure 1 — the loop arm's trajectory
# --------------------------------------------------------------------------- #


def figure_one(paths: dict, out: Path) -> dict:
    dev_arms = [a for a in discover(paths, "metrics") if a.split == "dev"]

    loop = one([a for a in dev_arms if a.iterations > 1], "iterating arm")
    # The comparison point is the same corpus's non-iterating arm. DESIGN §3
    # (2026-08-21): its prompt was byte-identical to the loop's round 1, so the
    # two together are the one observed difference at n = 2.
    oneshot = one(
        [a for a in dev_arms if a.corpus == loop.corpus and a.iterations == 1],
        f"single-call arm on {loop.corpus}",
    )

    rounds = loop.iterations
    iters = []
    for i in range(1, rounds + 1):
        p = REPO / paths["itermetrics"].format(
            corpus=loop.run["corpus"],
            detector=loop.run["detector"],
            supervision=loop.run["supervision"],
            porting=loop.porting,
            iteration=i,
        )
        iters.append(Arm(p, load_json(p)))

    xs = list(range(1, rounds + 1))
    fc = [a.leak(HEADLINE_MODE) for a in iters]
    rx = [a.leak(BOUND_MODE) for a in iters]

    sealed_path = REPO / paths["sealedmetrics"].format(
        corpus=loop.run["corpus"],
        detector=loop.run["detector"],
        supervision=loop.run["supervision"],
        porting=loop.porting,
    )
    sealed = Arm(sealed_path, load_json(sealed_path))

    # Two runs sent the same prompt bytes and came back apart by this much. It is
    # one observed difference at n = 2, not an estimate of spread, so it is drawn
    # as a bracket between those two points and never as a band over the series
    # (DESIGN §3, docs/notes/call-variance.md).
    difference = abs(fc[0] - oneshot.leak(HEADLINE_MODE))

    fig, ax = plt.subplots(figsize=(150 * MM, 95 * MM))

    ax.plot(
        xs, fc, color="black", linestyle="-", marker="o", markersize=5,
        linewidth=1.6, label=f"{HEADLINE_MODE} (headline)", zorder=3,
    )
    ax.plot(
        xs, rx, color="black", linestyle=":", marker="s", markersize=4.5,
        markerfacecolor="white", linewidth=1.4,
        label=f"{BOUND_MODE} (lower bound)", zorder=3,
    )

    single_x, single_y = xs[0] - 0.32, oneshot.leak(HEADLINE_MODE)
    ax.plot(
        [single_x], [single_y],
        linestyle="none", marker="^", markersize=8, markerfacecolor="white",
        markeredgecolor="black", markeredgewidth=1.3,
        label=f"single call ({single_y:.3f})",
        zorder=4,
    )

    ax.plot(
        [xs[-1] + 0.32], [sealed.leak(HEADLINE_MODE)],
        linestyle="none", marker="*", markersize=14, markerfacecolor="black",
        markeredgecolor="black",
        label=f"sealed test fold, round {rounds} rules "
              f"({sealed.leak(HEADLINE_MODE):.3f})",
        zorder=4,
    )

    # The bracket spans exactly the two points it is about: round 1 and the single
    # call. Light leaders tie each end to its own marker so the span cannot be read
    # as applying to the rest of the series.
    bx, cap = xs[0] - 0.60, 0.05
    lo, hi = sorted((fc[0], single_y))
    ax.plot([bx, bx], [lo, hi], color="black", linewidth=1.1, zorder=3)
    for y, to_x in ((fc[0], xs[0]), (single_y, single_x)):
        ax.plot([bx, bx + cap], [y, y], color="black", linewidth=1.1, zorder=3)
        ax.plot([bx + cap, to_x], [y, y], color="0.45", linewidth=0.7,
                linestyle=(0, (1, 2)), zorder=1)
    # The label sits in the empty band above the bracket and points down at its
    # top cap, so the leader is short and crosses neither series: both lines are
    # below the bracket from round 2 onwards, and `relaxed` is below it at round 1
    # too.
    ax.annotate(
        f"two runs of identical prompt bytes:\ndifference {difference:.3f} (n = 2)",
        xy=(bx, hi),
        xytext=(xs[0] - 0.80, 0.655),
        fontsize=7.5, ha="left", va="bottom",
        # Grey and thin, so a leader cannot be mistaken for one of the two series.
        arrowprops=dict(arrowstyle="->", linewidth=0.7, color="0.35", shrinkB=3),
    )

    # Reason and convergence both come off the record; the sentence is plain
    # English rather than the field spelling, which means nothing to a reader of
    # the paper.
    converged = bool(loop.data["termination"]["converged"])
    ax.annotate(
        f"stop: {loop.termination_reason}\n"
        f"({rounds} rounds, pre-registered;\n"
        f"{'converged' if converged else 'not convergence'})",
        xy=(xs[-1], fc[-1]),
        xytext=(xs[-1] - 1.5, fc[-1] + 0.17),
        fontsize=8,
        ha="left",
        arrowprops=dict(arrowstyle="->", linewidth=0.7, color="0.35"),
    )

    ax.set_xlabel("round")
    ax.set_ylabel(
        f"leak rate, development fold "
        f"(n = {loop.data['modes'][HEADLINE_MODE]['leak']['denominator']})"
    )
    ax.set_xticks(xs)
    ax.set_xlim(xs[0] - 0.85, xs[-1] + 0.7)
    ax.set_ylim(0, max(fc + [oneshot.leak(HEADLINE_MODE)]) + 0.2)
    ax.grid(axis="y", linestyle="-", linewidth=0.4, color="0.85", zorder=0)
    ax.set_axisbelow(True)
    ax.legend(fontsize=7.5, loc="upper right", framealpha=1.0)

    save(fig, out, "figure1_port_loop_trajectory")

    return {
        "corpus": loop.corpus,
        "porting": loop.porting,
        "rounds": rounds,
        "dev_n": loop.data["modes"][HEADLINE_MODE]["leak"]["denominator"],
        "round1_fc": fc[0],
        "final_fc": fc[-1],
        "final_rx": rx[-1],
        "oneshot_calls": oneshot.calls,
        "oneshot_fc": oneshot.leak(HEADLINE_MODE),
        "difference": difference,
        "sealed_fc": sealed.leak(HEADLINE_MODE),
        "sealed_rx": sealed.leak(BOUND_MODE),
        "sealed_n": sealed.data["modes"][HEADLINE_MODE]["leak"]["denominator"],
        "reason": loop.termination_reason,
        "rules_version": loop.run["rules_version"],
    }


# --------------------------------------------------------------------------- #
# figure 2 — the five single-call arms
# --------------------------------------------------------------------------- #


def reference_kind(corpus: str) -> str:
    split = load_json(REPO / "splits" / f"{corpus}.json")
    return split.get("corpus_specific", {}).get("reference", HUMAN_REFERENCE)


def figure_two(paths: dict, out: Path) -> dict:
    dev_arms = [a for a in discover(paths, "metrics") if a.split == "dev"]
    singles = [a for a in dev_arms if a.iterations == 1]

    by_corpus: dict[str, list[Arm]] = {}
    for arm in singles:
        by_corpus.setdefault(arm.corpus, []).append(arm)
    arms = [one(v, f"single-call arm on {k}") for k, v in by_corpus.items()]
    arms.sort(key=lambda a: a.leak(HEADLINE_MODE))

    # The arm deviations to mark are read off the records, not listed here: the
    # porting value that is not the majority one, and any arm above the modal
    # call count. DESIGN §7 conditions (4) and (5) are what the marks stand for.
    modal_porting = max({a.porting for a in arms}, key=lambda p: sum(a.porting == p for a in arms))
    modal_calls = max({a.calls for a in arms}, key=lambda c: sum(a.calls == c for a in arms))

    refs = {a.corpus: reference_kind(a.corpus) for a in arms}
    kinds = sorted(set(refs.values()))
    hatch_for = {kind: ("" if kind == HUMAN_REFERENCE else "///") for kind in kinds}
    grey_for = {kind: ("0.72" if kind == HUMAN_REFERENCE else "0.92") for kind in kinds}

    fig, ax = plt.subplots(figsize=(155 * MM, 100 * MM))
    xs = range(len(arms))

    labels: list[str] = []
    footnotes: dict[str, str] = {}
    for x, arm in zip(xs, arms):
        kind = refs[arm.corpus]
        ax.bar(
            x, arm.leak(HEADLINE_MODE), width=0.62,
            facecolor=grey_for[kind], edgecolor="black", linewidth=1.1,
            hatch=hatch_for[kind], zorder=2,
        )
        ax.plot(
            [x], [arm.leak(BOUND_MODE)], linestyle="none", marker="D",
            markersize=6, markerfacecolor="white", markeredgecolor="black",
            markeredgewidth=1.2, zorder=4,
        )
        ax.text(
            x, arm.leak(HEADLINE_MODE) + 0.022, f"{arm.leak(HEADLINE_MODE):.3f}",
            ha="center", va="bottom", fontsize=8.5,
        )

        # A deviating arm gets a mark under its own tick; what the mark means is
        # said in the caption, because the sentence that says it names an arm id
        # and a design section and neither belongs inside the figure.
        marks = ""
        if arm.porting != modal_porting:
            marks += "†"
            footnotes["†"] = arm.corpus
        if arm.calls != modal_calls:
            marks += "‡"
            footnotes["‡"] = arm.corpus
        # Three short lines rather than one long one: two of the five labels name
        # the same corpus and differ only in the language, so a wide label would
        # run into its neighbour exactly where the distinction is.
        name, langs = label_lines(display(arm.corpus))
        labels.append(
            f"{name}{(' ' + marks) if marks else ''}\n{langs}\n"
            f"n = {arm.data['modes'][HEADLINE_MODE]['leak']['denominator']}"
        )

    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel("leak rate, development fold")
    ax.set_ylim(0, 1.0)
    ax.grid(axis="y", linestyle="-", linewidth=0.4, color="0.85")
    ax.set_axisbelow(True)

    handles = [
        Patch(facecolor=grey_for[k], edgecolor="black", hatch=hatch_for[k],
              label=f"reference: {k}")
        for k in kinds
    ]
    handles += [
        Line2D([], [], linestyle="none", marker="D", markerfacecolor="white",
               markeredgecolor="black", markersize=6,
               label=f"{BOUND_MODE} (lower bound)"),
    ]
    ax.legend(
        handles=handles, fontsize=7.5, loc="upper left", framealpha=1.0,
        title=f"bars: {HEADLINE_MODE} (headline)", title_fontsize=7.5,
    )

    save(fig, out, "figure2_five_corpora_oneshot")

    return {
        "order": [a.corpus for a in arms],
        "marks": footnotes,
        "fc": {a.corpus: a.leak(HEADLINE_MODE) for a in arms},
        "rx": {a.corpus: a.leak(BOUND_MODE) for a in arms},
        "n": {a.corpus: a.data["modes"][HEADLINE_MODE]["leak"]["denominator"] for a in arms},
        "porting": {a.corpus: a.porting for a in arms},
        "calls": {a.corpus: a.calls for a in arms},
        "reference": refs,
        "modal_porting": modal_porting,
    }


# --------------------------------------------------------------------------- #
# figure 3 — dev against the sealed test fold, per type
# --------------------------------------------------------------------------- #


def figure_three(paths: dict, out: Path) -> dict:
    dev_arms = [a for a in discover(paths, "metrics") if a.split == "dev"]
    loop = one([a for a in dev_arms if a.iterations > 1], "iterating arm")

    sealed_path = REPO / paths["sealedmetrics"].format(
        corpus=loop.run["corpus"],
        detector=loop.run["detector"],
        supervision=loop.run["supervision"],
        porting=loop.porting,
    )
    sealed = Arm(sealed_path, load_json(sealed_path))

    dev_types, test_types = loop.by_type(), sealed.by_type()

    # §9.4: a type the scorer flagged sparse on *either* fold is dropped from the
    # per-type figure and the omission is stated. The flag is the scorer's, read
    # from the file; the threshold is not repeated here.
    kept, dropped = [], []
    for t in sorted(set(dev_types) | set(test_types)):
        d, s = dev_types.get(t), test_types.get(t)
        if d is None or s is None or not d["gold"] or not s["gold"]:
            dropped.append((t, "absent on one fold"))
        elif d["sparse"] or s["sparse"]:
            dropped.append((t, "sparse (§9.4)"))
        else:
            kept.append(t)

    kept.sort(key=lambda t: dev_types[t]["leak_rate"])
    # The type to highlight is the largest dev-to-test movement, chosen by
    # measurement rather than named here.
    focus = max(kept, key=lambda t: abs(test_types[t]["leak_rate"] - dev_types[t]["leak_rate"]))

    fig, ax = plt.subplots(figsize=(150 * MM, 105 * MM))
    ys = range(len(kept))

    for y, t in zip(ys, kept):
        dv, tv = dev_types[t]["leak_rate"], test_types[t]["leak_rate"]
        heavy = t == focus
        ax.plot(
            [dv, tv], [y, y], color="black",
            linestyle="-" if heavy else "--",
            linewidth=2.2 if heavy else 1.0, zorder=2,
        )
        ax.plot([dv], [y], linestyle="none", marker="o", markersize=8.5,
                markerfacecolor="white", markeredgecolor="black",
                markeredgewidth=1.3, zorder=3)
        ax.plot([tv], [y], linestyle="none", marker="X", markersize=7,
                markerfacecolor="black", markeredgecolor="black", zorder=4)
        if heavy:
            ax.annotate(
                f"{tv - dv:+.3f}",
                xy=((dv + tv) / 2, y), xytext=((dv + tv) / 2, y + 0.34),
                ha="center", fontsize=8.5, fontweight="bold",
            )

    ax.set_yticks(list(ys))
    ax.set_yticklabels(
        [f"{t}\n{dev_types[t]['gold']} / {test_types[t]['gold']}" for t in kept],
        fontsize=8.5,
    )
    ax.set_ylim(-0.6, len(kept) - 0.3)
    ax.set_xlabel(f"leak rate, {HEADLINE_MODE}")
    ax.set_xlim(0, 1.0)
    ax.grid(axis="x", linestyle="-", linewidth=0.4, color="0.85")
    ax.set_axisbelow(True)

    handles = [
        Line2D([], [], linestyle="none", marker="o", markerfacecolor="white",
               markeredgecolor="black", markersize=8.5,
               label="development fold "
                     f"(n = {loop.data['modes'][HEADLINE_MODE]['leak']['denominator']})"),
        Line2D([], [], linestyle="none", marker="X", color="black", markersize=7,
               label="test fold, sealed "
                     f"(n = {sealed.data['modes'][HEADLINE_MODE]['leak']['denominator']})"),
        Line2D([], [], color="black", linestyle="-", linewidth=2.2,
               label=f"largest movement: {focus}"),
    ]
    ax.legend(handles=handles, fontsize=8, loc="lower right", framealpha=1.0)

    save(fig, out, "figure3_dev_vs_sealed_by_type")

    return {
        "kept": kept,
        "dropped": dropped,
        "focus": focus,
        "dev": {t: dev_types[t]["leak_rate"] for t in kept},
        "test": {t: test_types[t]["leak_rate"] for t in kept},
        "gold": {t: (dev_types[t]["gold"], test_types[t]["gold"]) for t in kept},
        "dropped_gold": {
            t: (dev_types.get(t, {}).get("gold"), test_types.get(t, {}).get("gold"))
            for t, _ in dropped
        },
        "corpus": loop.corpus,
        "porting": loop.porting,
        "rounds": loop.iterations,
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #


def save(fig, out: Path, stem: str) -> None:
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
    paths = naming()["paths"]
    plt.rcParams.update({
        "font.size": 9,
        "axes.linewidth": 0.8,
        "pdf.fonttype": 42,  # embed TrueType rather than Type 3
        "savefig.bbox": "tight",
    })

    f1 = figure_one(paths, out)
    f2 = figure_two(paths, out)
    f3 = figure_three(paths, out)

    print(f"wrote 3 figures (pdf + png 300dpi) to {out}")
    print(f"  fig 1: {f1['corpus']} {f1['porting']}, {f1['rounds']} rounds, "
          f"difference {f1['difference']:.3f}, sealed {f1['sealed_fc']:.3f}")
    print(f"  fig 2: {', '.join(f2['order'])}")
    print(f"  fig 3: {len(f3['kept'])} types, "
          f"{len(f3['dropped'])} omitted, focus {f3['focus']}")


if __name__ == "__main__":
    main()
