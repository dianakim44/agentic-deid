"""Five round-scoped paths, one validation, four error types (`corpora.base.round_path`).

Every iteration-scoped results path in this project answers the same two questions before it
is built — is each component a value of its axis, and is the round a round — and until
2026-08-13 four modules answered them independently. Each said in its docstring that the
repetition was the module boundary rather than an oversight, because each raises the type its
own callers catch:

    run_fold._round_path      -> FoldRunError    (iterspans, itererrors)
    scorer.iter_metrics_path  -> ScorerError     (itermetrics)
    rules.arm_rules_path      -> RuleError       (armrules)

That reasoning is right about the *type* and was doing the work of an argument. The Auditor's
report needed the fifth (`audit.report_path` -> `AuditError`, `auditreport`), and five copies
of a check is where the cost stops being hypothetical: **what four copies drift on is what one
of them learns and the others do not, and every line of this is a check, so the drift is
silent by construction.** A path builder that stopped validating its axis does not raise
anything — it writes a results directory that names a cell of the experiment nothing defines.

So the check is one function taking `error` as a parameter, and this file is the pin on that
arrangement. Three properties:

  1. **All five agree**, asserted by running the same bad inputs through each and requiring a
     refusal from every one. Parametrized over the builders rather than written per builder,
     which is what makes a sixth builder's omission a failure here instead of a gap.
  2. **Each keeps its own error type.** The sharing is of the check, not of the exception —
     a caller catching `AuditError` must not have to catch `FoldRunError` to build a path.
  3. **The template lookup was already single and stays single.** Each `paths` key has exactly
     one reader; this changed where validation lives, not where a location is defined.

What is *not* asserted here is each path's shape. That stays with its own module's tests
(`test_run_fold.py`, `test_scorer.py`, `test_arm_rules_path.py`, `test_audit.py`), because the
shape is the template's and this file is about the check.

    python3 -m pytest tests/test_round_path.py -q
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import (                                        # noqa: E402
    ROUND_AXES, CorpusError, path_template, round_path,
)
from src.eval.run_fold import FoldRunError, errors_path, iter_spans_path  # noqa: E402
from src.eval.scorer import ScorerError, iter_metrics_path             # noqa: E402
from src.porting.audit import AuditError, report_path                  # noqa: E402
from src.rules import RuleError, arm_rules_path                        # noqa: E402

AXES = dict(corpus="es-meddocan", detector="RT", supervision="sup-free",
            porting="port-loop")

#: Every round-scoped path builder in the project: the function, the extra keywords its
#: template needs beyond the four axes, and the error type it must raise.
#:
#: Written out rather than discovered by walking `naming.yaml`'s `paths` for `{iteration}`.
#: A discovered list would grow on its own the day a sixth template is declared, which sounds
#: like the stronger test and is the weaker one: the thing that must fail is a new *builder*
#: that skipped the shared check, and that builder is not in the config.
BUILDERS = [
    ("run_fold.iter_spans_path", iter_spans_path, {}, FoldRunError),
    ("run_fold.errors_path", errors_path, {}, FoldRunError),
    ("scorer.iter_metrics_path", iter_metrics_path, {}, ScorerError),
    ("rules.arm_rules_path", arm_rules_path, {"lang": "es"}, RuleError),
    ("audit.report_path", report_path, {}, AuditError),
]

IDS = [name for name, _, _, _ in BUILDERS]


# ─── all five refuse the same things ─────────────────────────────────────────


@pytest.mark.parametrize("name,builder,extra,error", BUILDERS, ids=IDS)
@pytest.mark.parametrize("key,bad", [
    ("corpus", "es-nope"), ("detector", "R+T"), ("supervision", "supfree"),
    ("porting", "port-agentic"),
])
def test_every_builder_refuses_an_axis_value_naming_no_cell(name, builder, extra, error,
                                                            key, bad):
    """A results path names a cell of the experiment, so an unknown component mints one.

    `results/es-meddocan/rules-only/` sits beside `results/es-meddocan/R/` looking like a
    second detector, and an aggregation walking those directories reports it as one.
    """
    with pytest.raises(error, match="naming.yaml"):
        builder(**{**AXES, key: bad}, **extra, iteration=2)


@pytest.mark.parametrize("name,builder,extra,error", BUILDERS, ids=IDS)
@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "2", None, 2.0])
def test_every_builder_refuses_a_round_that_is_not_a_round(name, builder, extra, error, bad):
    """`iter0/` and `iter1.0/` put a round's record where nothing looks for it.

    `True` is in the list because `isinstance(True, int)` holds — a caller that passed a flag
    would silently name round 1. `2.0` is in it because it formats as `iter2.0`, which is the
    float case that looks most like it would work.
    """
    with pytest.raises(error, match="iteration"):
        builder(**AXES, **extra, iteration=bad)


@pytest.mark.parametrize("name,builder,extra,error", BUILDERS, ids=IDS)
def test_every_builder_names_the_artefact_it_was_about_to_misplace(name, builder, extra,
                                                                   error):
    """One shared check, and the message still says which call to look at.

    The cost of sharing is a refusal with five callers, and `artefact` is what pays it: a
    message reading only "iteration must be an integer >= 1" would send a reader to whichever
    of the round's four files they thought of first. Asserted as *distinctness* rather than
    against a list of the five words, because what makes the message useful is that no two
    builders produce the same one.
    """
    with pytest.raises(error) as caught:
        builder(**AXES, **extra, iteration=0)
    message = str(caught.value)
    others = [str(pytest.raises(e, lambda: b(**AXES, **x, iteration=0)).value)
              for n, b, x, e in BUILDERS if n != name]
    assert message not in others, (
        f"{name}'s refusal is word-for-word another builder's. `artefact` exists so a shared "
        "message still names the file that was about to be misplaced."
    )


@pytest.mark.parametrize("name,builder,extra,error", BUILDERS, ids=IDS)
def test_every_builder_raises_its_own_modules_error_type(name, builder, extra, error):
    """**Why `error` is a parameter and not a shared exception class.**

    Each of these modules raises the type its own callers already catch, and that was the
    stated reason the check was copied four times. Making it an argument keeps the reason and
    drops the copies. Asserted with a deliberately wrong axis rather than a wrong round, so
    the type is checked on the branch a caller is most likely to hit.
    """
    with pytest.raises(error):
        builder(**{**AXES, "porting": "nope"}, **extra, iteration=2)
    assert issubclass(error, Exception)


@pytest.mark.parametrize("name,builder,extra,error", BUILDERS, ids=IDS)
def test_every_builder_puts_the_round_in_a_directory(name, builder, extra, error):
    """`iter{N}/` and not `…_iter{N}.json` (DESIGN §5.3, §5.5).

    A filename suffix was the alternative and §5.3 refused it: reading which rounds exist
    becomes parsing filenames, and a round's files stop being one directory. Checked across
    all five because the property is what makes "the round is one record" true.
    """
    path = builder(**AXES, **extra, iteration=4, root=Path("/r"))
    assert path.parent.name == "iter4"
    assert "iter" not in path.name


def test_the_rounds_results_files_share_one_directory():
    """The point of the above, over the four files that land in the round's own directory.

    Three outputs — predictions, score, error list — and one input, the audit report, derived
    from round n−1's predictions. The report sits with them and still carries
    `masked_from_iteration`, because a directory listing does not say which spans it was built
    against (`auditor.md` banner).

    **`armrules` is excluded, and the exclusion is the template's own decision rather than an
    exception made here.** It is `…/rules/iter{N}/{lang}.yaml`: the `rules/` component is there
    because `release_screen.py` matches rule files by `(^|/)rules/[^/]+\\.ya?ml$` to enforce
    `rule_author.md` Prohibition 2, and a rule file outside that shape does not fail the check,
    it *skips* it (config/naming.yaml). So the round's rule state is a sibling directory, and
    the filename collision this test is about cannot arise there — `es.yaml` and `cat.yaml` are
    two files of one round by design (`corpus_rule_langs`).
    """
    root = Path("/r")
    paths = [b(**AXES, **x, iteration=4, root=root) for name, b, x, _ in BUILDERS
             if name != "rules.arm_rules_path"]
    assert len({p.parent for p in paths}) == 1
    assert len({p.name for p in paths}) == len(paths), (
        f"two of the round's files share a filename: {sorted(p.name for p in paths)}. "
        "They are in one directory, so a collision is one file overwriting another."
    )
    rules = arm_rules_path(**AXES, lang="es", iteration=4, root=root)
    assert rules.parent.name == "iter4" and rules.parent != paths[0].parent
    assert rules.parent.parent.name == "rules", (
        "the arm's rule path lost its `rules/` component. The screener matches rule files by "
        "that segment, and a rule file outside the pattern skips the surface-form check "
        "rather than failing it (config/naming.yaml, rule_author.md Prohibition 2)."
    )


# ─── the check is one place, and the templates are still one each ────────────


def _checks_a_round(fn: ast.FunctionDef) -> bool:
    """Does this function test `iteration`'s type itself? The round check's marker line."""
    return any(
        isinstance(node, ast.Call) and getattr(node.func, "id", "") == "isinstance"
        and len(node.args) == 2 and getattr(node.args[0], "id", "") == "iteration"
        for node in ast.walk(fn))


def _builds_a_path(fn: ast.FunctionDef) -> bool:
    """Does it look up a `paths` template? What makes a function a path builder here."""
    return any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", "") == "path_template"
             or getattr(node.func, "attr", "") == "path_template")
        for node in ast.walk(fn))


def test_no_path_builder_keeps_its_own_copy_of_the_check():
    """Structural, because a behavioural test cannot tell shared from duplicated.

    Five builders that each validated correctly and five that call one validator pass every
    test above identically. What separates them is whether a fix to the check reaches all
    five, and that is a property of the syntax tree: no path builder may re-implement the
    round check beside its call to the shared one.

    **Scoped to functions that build a path, and that is not a loophole.** `audit.report()`
    checks `iteration` itself and must keep doing so — it refuses a round before 2, which is a
    fact about the Auditor's schedule rather than about a location (round 1 has no predictions
    to mask). A test that flagged every `isinstance(iteration, int)` in `src/` would be asking
    a content validator to stop validating its content. What is forbidden is the pairing: a
    function that both formats a `paths` template and decides for itself what a round is.
    """
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if path.name == "base.py" and path.parent.name == "corpora":
            continue  # where the check lives
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _builds_a_path(node) \
                    and _checks_a_round(node):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
    assert not offenders, (
        f"the round check is re-implemented inside a path builder at {offenders}. It lives in "
        "`corpora.base.round_path` so that a fix to it reaches every round-scoped path; a "
        "second copy is the drift the sharing exists to prevent, and it is silent — a builder "
        "that stopped validating raises nothing, it writes to a cell nothing defines."
    )


def test_the_check_is_where_this_test_says_it_is():
    """The exemption above is one directory entry, so it is worth asserting it earns it.

    A test that skipped `corpora/base.py` and found the check absent from it would be
    reporting "nobody duplicates the check" about a check that no longer exists anywhere.
    """
    tree = ast.parse((ROOT / "src" / "corpora" / "base.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "round_path")
    assert _checks_a_round(fn) and _builds_a_path(fn)


@pytest.mark.parametrize("key", ["iterspans", "itererrors", "itermetrics", "armrules",
                                 "auditreport"])
def test_each_round_template_has_exactly_one_reader(key):
    """What was *never* duplicated and must stay that way: the template lookup.

    A `paths` key formatted in two functions is two definition sites for one location, which
    is a worse failure than a duplicated check — the check's copies at least agree until one
    changes, whereas two `.format()` calls disagree the moment either is edited. Counted over
    the whole of `src/`, as string literals, since that is how a key is named.
    """
    readers = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == key:
                readers.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert len(readers) == 1, (
        f"paths.{key} is named at {readers}. One key, one reader — two callers formatting "
        "one template is two places a results location is decided."
    )


def test_the_shared_builder_validates_what_the_template_names():
    """Not a fixed list of four. `armrules` has a fifth component (`{lang}`), and a builder
    that checked a hardcoded four would format an unchecked `lang` into a results path.

    Asserted from the template's own fields, so this stays true if a sixth template arrives
    with a component nobody thought of.
    """
    fields = {n for _, n, _, _ in
              __import__("string").Formatter().parse(path_template("armrules")) if n}
    assert "lang" in fields and set(ROUND_AXES) < fields
    with pytest.raises(RuleError, match="naming.yaml"):
        arm_rules_path(**AXES, lang="xx", iteration=2)


def test_a_component_the_template_does_not_name_is_refused():
    """Neither silently dropped nor formatted in. Both failures are quiet: an extra keyword
    that the template ignores means a caller believes it scoped a path it did not, and a
    template field nobody passed raises a `KeyError` from inside `.format()` — a message about
    a missing dict key rather than about a misplaced round.
    """
    with pytest.raises(ScorerError, match="itermetrics"):
        round_path("itermetrics", iteration=2, artefact="score", error=ScorerError,
                   lang="es", **AXES)
    with pytest.raises(RuleError, match="armrules"):
        round_path("armrules", iteration=2, artefact="rule file", error=RuleError, **AXES)


def test_an_undeclared_path_key_is_refused_by_the_config_and_not_defaulted():
    """Through `path_template`, so a builder cannot invent an artefact. A caller asking for a
    path `naming.yaml` does not declare has invented one, and the config is where a new output
    location is added first (CLAUDE.md).
    """
    with pytest.raises(CorpusError, match="no paths."):
        round_path("iterwhatever", iteration=2, artefact="thing", error=CorpusError, **AXES)


def test_the_round_axes_are_the_four_the_templates_share():
    """`ROUND_AXES` is read as an argument list by callers holding no run block — the loop
    driver, which has four axes and a round. Checked against the templates rather than
    trusted, since a constant naming a fifth axis would be a scoping claim no path makes.
    """
    for key in ("iterspans", "itererrors", "itermetrics", "auditreport"):
        fields = {n for _, n, _, _ in
                  __import__("string").Formatter().parse(path_template(key)) if n}
        assert fields == set(ROUND_AXES) | {"iteration"}, key
