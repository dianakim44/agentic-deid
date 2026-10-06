"""Figure 1, the architecture diagram, drawn to a fixed layout.

This is the one figure in the manuscript that states no measured quantity, so
unlike `tools/make_figures.py` it reads nothing: no `metrics.json`, no frozen
split, no `config/`. Every coordinate, label and constant in it is written out
here. That is the right shape for a diagram of the pipeline's structure --
there is nothing to recompute when a result moves -- and it is also what makes
the file's own correctness a matter of reading it against DESIGN rather than
of running it.

The three constants it does state are DESIGN's, and are repeated here rather
than read: the termination rule (DESIGN section 3), ten canonical PHI types and
four detection layers (config/naming.yaml's `phi_type` and `layer` axes). If
any of those change, this file does not notice, and nothing in the repository
will tell you -- the figure is prose about the design, checked the way prose is.

Usage:

    python tools/arch_figure.py [--out DIR]

Default output directory is `~/Desktop/figures` -- outside the repository, for
the same reason `make_figures.py` writes there: figures that accompany a
manuscript drawn from DUA-covered corpora are not committed (CLAUDE.md). This
figure carries no corpus value and would be safe to commit; it is written
beside the other four anyway, because a reviewer assembles one set of figures
and not two.
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle, Rectangle, Polygon

# The same list, in the same order, as `tools/make_figures.py`'s `FONT_STACK`:
# matplotlib takes the first family it can resolve, so Figure 1 and Figures 2-5
# are set in one face on any machine that has any of the three. Arial is the
# manuscript's; the other two are metric-compatible substitutes.
plt.rcParams["font.family"] = ["Arial", "Liberation Sans", "DejaVu Sans"]

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument(
    "--out", default=str(Path.home() / "Desktop" / "figures"),
    help="output directory; defaults outside the repository (CLAUDE.md)",
)
OUT = Path(ap.parse_args().out).expanduser()
OUT.mkdir(parents=True, exist_ok=True)

W, H = 16.0, 10.2
fig = plt.figure(figsize=(W, H), dpi=300)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")

C_AGENT = "#F6D7B0"; C_AGENT_E = "#C2712B"
C_DET = "#DCE6F2"; C_DET_E = "#3F6A9A"
C_ORCH = "#E8E8E8"; C_ORCH_E = "#4A4A4A"
C_INT = "#DDEBD9"; C_INT_E = "#4C7A43"
C_IO = "#FFFFFF"
ACC = "#C0561B"


def box(x, y, w, h, fc, ec, lw=1.4, ls="-", r=0.18, z=1):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.02,rounding_size={r}",
                       fc=fc, ec=ec, lw=lw, ls=ls, zorder=z)
    ax.add_patch(p)
    return p


def arrow(x1, y1, x2, y2, c="#333333", lw=1.6, style="-|>", ls="-", rad=0.0, z=5, ms=14):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=ms,
                        color=c, lw=lw, ls=ls, zorder=z,
                        connectionstyle=f"arc3,rad={rad}")
    ax.add_patch(a)


def robot(cx, cy, s=0.42, col=C_AGENT_E):
    head = FancyBboxPatch((cx - s, cy - s * 0.72), 2 * s, 1.44 * s,
                          boxstyle=f"round,pad=0.0,rounding_size={s*0.35}",
                          fc="#FFFFFF", ec=col, lw=1.6, zorder=6)
    ax.add_patch(head)
    for dx in (-0.42, 0.42):
        ax.add_patch(Circle((cx + dx * s, cy + 0.08 * s), 0.16 * s, fc=col, ec=col, zorder=7))
    ax.plot([cx - 0.3 * s, cx + 0.3 * s], [cy - 0.38 * s, cy - 0.38 * s], color=col, lw=1.6, zorder=7)
    ax.plot([cx, cx], [cy + 0.72 * s, cy + 1.05 * s], color=col, lw=1.6, zorder=6)
    ax.add_patch(Circle((cx, cy + 1.12 * s), 0.11 * s, fc=col, ec=col, zorder=7))
    ax.text(cx, cy - 1.15 * s, "LLM", ha="center", va="center", fontsize=7.5,
            color=col, fontweight="bold", zorder=7)


def gear(cx, cy, r=0.36, col=C_ORCH_E):
    import numpy as np
    n = 8
    pts = []
    for i in range(n * 2):
        ang = i * np.pi / n
        rr = r if i % 2 == 0 else r * 0.78
        pts.append((cx + rr * np.cos(ang), cy + rr * np.sin(ang)))
    ax.add_patch(Polygon(pts, closed=True, fc="#FFFFFF", ec=col, lw=1.6, zorder=6))
    ax.add_patch(Circle((cx, cy), r * 0.32, fc=col, ec=col, zorder=7))


def doc(x, y, w=0.42, h=0.54, col="#555555", fc="#FFFFFF", label=None):
    fold = w * 0.3
    pts = [(x, y), (x + w, y), (x + w, y + h - fold), (x + w - fold, y + h), (x, y + h)]
    ax.add_patch(Polygon(pts, closed=True, fc=fc, ec=col, lw=1.2, zorder=6))
    for k in range(3):
        yy = y + h * (0.25 + 0.18 * k)
        ax.plot([x + w * 0.18, x + w * 0.78], [yy, yy], color=col, lw=0.8, zorder=7)
    if label:
        ax.text(x + w / 2, y - 0.13, label, ha="center", va="top", fontsize=7.5, color=col, zorder=7)


def lock(cx, cy, s=0.22, col=C_INT_E):
    ax.add_patch(Rectangle((cx - s, cy - s), 2 * s, 1.5 * s, fc="#FFFFFF", ec=col, lw=1.4, zorder=7))
    import numpy as np
    t = np.linspace(0, np.pi, 30)
    ax.plot(cx + 0.65 * s * np.cos(t), cy + 0.5 * s + 0.75 * s * np.sin(t), color=col, lw=1.4, zorder=7)
    ax.add_patch(Circle((cx, cy - 0.2 * s), 0.18 * s, fc=col, ec=col, zorder=8))


# ---------------- top band ----------------
box(0.35, 8.55, 3.55, 1.35, C_IO, "#555555")
doc(0.6, 8.85)
ax.text(1.22, 9.55, "Input", fontsize=11.5, fontweight="bold", va="center")
ax.text(1.22, 9.12, "Development fold of the target corpus\n"
                    "Task frame: 10 canonical PHI types,\nlayer vocabulary", fontsize=8.6, va="center")

box(5.15, 8.45, 5.7, 1.55, C_ORCH, C_ORCH_E, lw=1.8)
gear(5.75, 9.22)
ax.text(6.35, 9.62, "Deterministic Orchestrator", fontsize=13.5, fontweight="bold", va="center")
ax.text(6.35, 9.08, "code, not an LLM — execution order · retries · budget\n"
                    "termination rule · cost ledger · window freeze",
        fontsize=8.6, va="center", color="#333333")

box(12.1, 8.55, 3.55, 1.35, C_IO, "#555555")
doc(12.35, 8.85, label=None)
doc(12.62, 8.78, fc="#F7F7F7")
ax.text(13.35, 9.55, "Output", fontsize=11.5, fontweight="bold", va="center")
ax.text(13.35, 9.12, "Rule file $r_T$ (YAML)\nper-round metrics\ncost account", fontsize=8.6, va="center")

arrow(3.95, 9.22, 5.1, 9.22, lw=2.0)
arrow(10.9, 9.22, 12.05, 9.22, lw=2.0)

ax.plot([0.25, W - 0.25], [8.2, 8.2], color="#888888", lw=1.0, ls=(0, (5, 4)), zorder=0)

# step labels
ax.text(2.0, 7.97, "Round 1  (≡ single call, port-oneshot)", fontsize=9.5,
        color=ACC, fontweight="bold", ha="left", va="center")
ax.text(14.0, 7.97, "Rounds 2…K  (feedback)", fontsize=9.5,
        color=ACC, fontweight="bold", ha="right", va="center")
arrow(6.0, 8.42, 2.6, 7.12, c=ACC, lw=2.0, rad=0.12)
arrow(10.0, 8.42, 13.4, 7.12, c=ACC, lw=2.0, rad=-0.12)

# ---------------- left: authoring agent ----------------
box(0.35, 2.55, 4.15, 4.55, "#FFFAF3", C_AGENT_E, lw=1.5, ls=(0, (5, 3)))
ax.text(2.42, 6.85, "Authoring agent", fontsize=12, fontweight="bold", ha="center", va="center")
box(0.65, 5.15, 3.55, 1.35, C_AGENT, C_AGENT_E)
robot(1.25, 5.88)
ax.text(1.95, 6.12, "RuleAuthor", fontsize=12, fontweight="bold", va="center")
ax.text(1.95, 5.62, "writes the complete\nrule file each round", fontsize=8.4, va="center")

ax.text(0.7, 4.82, "Prompt sections", fontsize=9, fontweight="bold", va="center")
secs = [("§1.1", "task frame"), ("§1.2", "current rules $r_{t-1}$"),
        ("§1.3", "per-type scores $s_{t-1}$"), ("§1.4", "uncovered-span sample $E_{t-1}$")]
for i, (k, v) in enumerate(secs):
    yy = 4.45 - i * 0.36
    box(0.7, yy - 0.15, 0.62, 0.3, "#FFFFFF", C_AGENT_E, lw=0.9, r=0.06)
    ax.text(1.01, yy, k, fontsize=8, ha="center", va="center", color=C_AGENT_E, fontweight="bold")
    ax.text(1.45, yy, v, fontsize=8.4, va="center")

doc(0.75, 2.75, w=0.38, h=0.48, col=C_AGENT_E)
ax.text(1.3, 3.03, "rules/{lang}.yaml", fontsize=8.6, fontweight="bold", va="center")
# The same three words as the layer boxes below and as `LAYER_DISPLAY`; this was
# the one place in the figure still naming them its own way (v16).
ax.text(1.3, 2.8, "pattern rules · context cues · gazetteer", fontsize=7.8, va="center", color="#444444")

# ---------------- centre: deterministic pipeline ----------------
box(5.0, 2.55, 6.0, 4.55, "#F7FAFD", C_DET_E, lw=1.5, ls=(0, (5, 3)))
ax.text(8.0, 6.85, "Deterministic detection and scoring", fontsize=12, fontweight="bold",
        ha="center", va="center")

ax.text(5.25, 6.42, "1) Layered detection", fontsize=9.5, fontweight="bold", va="center")
# The manuscript's words for the four layers, which is what `LAYER_DISPLAY` in
# tools/make_figures.py spells for Figures 2b and 5b. Capitalised here because
# these are box labels and lower case there because those are legend entries.
layers = [("Pattern\nrules", "#FFFFFF", "-"), ("Context\ncues", "#FFFFFF", "-"),
          ("Gazetteer", "#FFFFFF", "-"), ("Tagger\n(not run)", "#F2F2F2", (0, (3, 2)))]
for i, (nm, fc, ls) in enumerate(layers):
    x = 5.25 + i * 1.12
    box(x, 5.45, 0.98, 0.72, fc, C_DET_E, lw=1.1, ls=ls, r=0.08)
    ax.text(x + 0.49, 5.81, nm, fontsize=8.2, ha="center", va="center",
            color="#777777" if "not run" in nm else "#111111")
box(9.78, 5.45, 1.0, 0.72, C_DET, C_DET_E, lw=1.2, r=0.08)
ax.text(10.28, 5.81, "Recall-first\nmerge", fontsize=8.2, ha="center", va="center")
arrow(9.67, 5.81, 9.77, 5.81, lw=1.2, ms=10)

ax.text(5.25, 5.05, "2) Masker", fontsize=9.5, fontweight="bold", va="center")
box(5.25, 4.25, 5.53, 0.6, C_DET, C_DET_E, lw=1.1, r=0.08)
ax.text(8.0, 4.55, r"$\tilde{x} = \mathrm{mask}(x, P(x; r_t))$  —  detected spans → type tags",
        fontsize=8.8, ha="center", va="center")

ax.text(5.25, 3.85, "3) Scorer  (no agent calls it; it calls no agent)", fontsize=9.5,
        fontweight="bold", va="center")
box(5.25, 2.75, 2.66, 0.9, C_DET, C_DET_E, lw=1.1, r=0.08)
ax.text(6.58, 3.36, "Coverage (union)", fontsize=8.6, ha="center", va="center", fontweight="bold")
ax.text(6.58, 3.0, r"$L^{\mathrm{full}}, L^{\mathrm{rel}}$, per type", fontsize=8.6, ha="center", va="center")
box(8.12, 2.75, 2.66, 0.9, C_DET, C_DET_E, lw=1.1, r=0.08)
ax.text(9.45, 3.36, "Assignment (1 : 1)", fontsize=8.6, ha="center", va="center", fontweight="bold")
ax.text(9.45, 3.0, "Precision · Recall · $F_1$", fontsize=8.6, ha="center", va="center")

arrow(8.0, 5.42, 8.0, 4.88, lw=1.3, ms=11)
arrow(8.0, 4.22, 8.0, 3.68, lw=1.3, ms=11)

# gold store
box(6.9, 2.06, 2.2, 0.36, "#FFFFFF", "#777777", lw=1.0, r=0.06)
ax.text(8.0, 2.24, "gold spans $G$ (dev fold only)", fontsize=8, ha="center", va="center", color="#444444")
arrow(8.0, 2.43, 8.0, 2.72, c="#777777", lw=1.0, ms=9)

# ---------------- right: audit agent ----------------
box(11.5, 2.55, 4.15, 4.55, "#FFFAF3", C_AGENT_E, lw=1.5, ls=(0, (5, 3)))
ax.text(13.57, 6.85, "Audit agent", fontsize=12, fontweight="bold", ha="center", va="center")
box(11.8, 5.15, 3.55, 1.35, C_AGENT, C_AGENT_E)
robot(12.4, 5.88)
ax.text(13.1, 6.12, "Auditor", fontsize=12, fontweight="bold", va="center")
ax.text(13.1, 5.62, "one call per\ndevelopment document", fontsize=8.4, va="center")

items = ["reads the masked text $\\tilde{x}$ only",
         "never sees gold annotation",
         "flags suspected residual PHI",
         "declines with a reason from a\nclosed vocabulary"]
for i, t in enumerate(items):
    yy = 4.72 - i * 0.42
    ax.add_patch(Circle((11.98, yy + (0.08 if "\n" in t else 0)), 0.05, fc=C_AGENT_E, ec=C_AGENT_E, zorder=6))
    ax.text(12.12, yy, t, fontsize=8.4, va="center")

doc(11.9, 2.75, w=0.38, h=0.48, col=C_AGENT_E)
ax.text(12.45, 3.03, "audit report $a_t$", fontsize=8.6, fontweight="bold", va="center")
ax.text(12.45, 2.8, "validated per item", fontsize=7.8, va="center", color="#444444")

# ---------------- flows ----------------
# rules -> detection
arrow(3.3, 3.0, 5.22, 5.62, c=C_AGENT_E, lw=1.8, rad=-0.25)
ax.text(3.75, 4.55, "$r_t$", fontsize=10, color=C_AGENT_E, fontweight="bold")
# masker -> auditor
arrow(10.82, 4.55, 11.78, 4.9, c=C_AGENT_E, lw=1.8)
ax.text(11.12, 4.86, r"$\tilde{x}$", fontsize=10, color=C_AGENT_E)
# audit report -> RuleAuthor (long return path above)
# scorer -> RuleAuthor feedback
arrow(5.22, 3.2, 4.25, 4.12, c=ACC, lw=1.8, rad=0.0)
ax.text(4.2, 3.4, "$s_t, E_t$", fontsize=9.5, color=ACC, fontweight="bold", ha="center")
# audit -> RuleAuthor via bottom path
ax.plot([12.1, 12.1], [2.62, 1.92], color=ACC, lw=1.8, zorder=5)
ax.plot([12.1, 4.75], [1.92, 1.92], color=ACC, lw=1.8, zorder=5)
ax.plot([4.75, 4.75], [1.92, 3.55], color=ACC, lw=1.8, zorder=5)
arrow(4.75, 3.55, 4.25, 3.95, c=ACC, lw=1.8)
ax.text(10.6, 1.99, "$a_t$", fontsize=9.5, color=ACC, fontweight="bold")
# scorer -> orchestrator (L_t) along right side of centre
ax.plot([10.95, 11.25], [3.2, 3.2], color=C_ORCH_E, lw=1.4, zorder=5)
ax.plot([11.25, 11.25], [3.2, 8.15], color=C_ORCH_E, lw=1.4, zorder=5, ls=(0, (4, 2)))
arrow(11.25, 8.15, 10.75, 8.45, c=C_ORCH_E, lw=1.4)
ax.text(11.32, 7.6, r"$L_t,\ \gamma_t$", fontsize=9.5, color=C_ORCH_E)

# ---------------- bottom: integrity layer ----------------
box(0.35, 0.18, W - 0.7, 1.55, "#F6FAF5", C_INT_E, lw=1.5, ls=(0, (5, 3)))
ax.text(0.6, 1.5, "Integrity layer (fixed before the first call)", fontsize=11, fontweight="bold",
        va="center", color=C_INT_E)
cells = [
    ("Window freeze", "SHA-256 of prompts and sampling\nconfiguration; refused once the arm\nhas made a call"),
    ("Pre-registered termination", r"$\delta=\max(0.005,\,26/n_{\mathrm{dev}})$, $k=2$, $K=8$" "\nsigned gain, ceiling ≠ convergence"),
    ("Sealed test fold", "code gate on caller identity;\nlog appended before read;\nopened once per arm"),
    ("Cost accounting", "raw tokens, cache read/write,\nabandoned attempts recorded\nseparately"),
]
cw = (W - 1.05) / 4
for i, (t, d) in enumerate(cells):
    x = 0.55 + i * cw
    box(x, 0.3, cw - 0.15, 1.0, C_INT, C_INT_E, lw=1.1, r=0.1)
    lock(x + 0.32, 0.9)
    ax.text(x + 0.62, 1.1, t, fontsize=9.3, fontweight="bold", va="center")
    ax.text(x + 0.62, 0.65, d, fontsize=7.7, va="center", color="#333333")

fig.savefig(OUT / "fig1_architecture.pdf")            # vector
fig.savefig(OUT / "fig1_architecture.png", dpi=300)   # raster, 300 dpi
print(f"wrote fig 1 (pdf + png 300dpi) to {OUT}")
