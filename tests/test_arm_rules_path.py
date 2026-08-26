"""An arm's rule files live under the arm, and nothing infers where (DESIGN §5.3).

`rules/{lang}.yaml` has no axis in it — not the four that name a cell, not the iteration.
`port-oneshot` and `port-loop` differ in nothing except how their rule file was produced,
so one shared path means the second arm to run overwrites the first. That is
`paths.armfreeze`'s collision one level down, and worse here in a specific way this file
is built around: **an overwritten record is visibly gone, and an overwritten input leaves
a plausible output behind.** After the overwrite `metrics.json` still holds a
`rules_version` integer and a sorted `rules` list, complete and internally consistent, for
a run whose input no longer exists. Nothing in it is wrong and nothing in it is checkable.

So three things are asserted, and each answers one half of that:

  1. **The path carries all five components** — four axes and the iteration. Checked by
     formatting, and separately by asserting no two arms and no two iterations can produce
     the same path, which is the property the collision is about rather than a spelling of
     the template.
  2. **`run_fold` is told which files to load.** Structurally: the module must not build
     an arm rule path from its own axis arguments. A behavioural test cannot see the
     difference — inferring the right path and being handed the right path produce
     identical output on the happy path — so this is asserted over the syntax tree, for
     `tests/test_conftest.py`'s reason.
  3. **`rules_source` reaches `metrics.json`.** The version integer stays plausible across
     an overwrite; the path does not. Without it in the run block the whole decision is
     undetectable from the published record.

`{iteration}` being a directory rather than a filename suffix is checked too: `es-carmen`
emits `es` and `cat` in one round, and one round's rule state is both files together.

**The last section is the same decision for the three auxiliary inputs** — profile, mapping
and lexicon — which `paths.armprofile`, `paths.armmapping` and `paths.armlexicon` scope to an
arm as of 2026-08-26. It is the third recurrence of the collision above and the file stays
the place it is asserted, because what makes it worth asserting separately is where the three
*differ* from `armrules`: they carry no `{iteration}`, their hand-written counterparts keep
living at axis-free paths that must not be disturbed, and only one of the three is denied by
the screener. The tests are kept here rather than in a new file so the mutation gate's
denominator does not move (CLAUDE.md).
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import ROUND_AXES, path_template                # noqa: E402
from src.rules import RuleError, arm_rules_path, load_rules           # noqa: E402

ARM = dict(corpus="es-meddocan", detector="R", supervision="sup-free",
           porting="port-oneshot")

RULE_FILE = """
version: 2
lang: es
rules:
  - rule_id: hospital_gazetteer
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["ZZZNEVERMATCHZZZ"]
"""


# ─── the path carries every component ────────────────────────────────────────


def test_the_arm_rule_path_carries_the_four_axes_and_the_iteration():
    p = arm_rules_path(**ARM, iteration=3, lang="es", root=Path("/r"))
    assert p == Path("/r/results/es-meddocan/R/sup-free/port-oneshot/rules/iter3/es.yaml")


def test_two_arms_cannot_write_the_same_rule_file():
    """The collision itself, stated as the property rather than as a template spelling.

    This is the whole decision: `port-oneshot` and `port-loop` are the same corpus, the
    same detector and the same supervision, and before DESIGN §5.3 they shared
    `rules/es.yaml`. Asserting the template string would pass on a template that had the
    axes in it but produced colliding paths anyway.
    """
    one = arm_rules_path(**{**ARM, "porting": "port-oneshot"},
                         iteration=1, lang="es", root=Path("/r"))
    loop = arm_rules_path(**{**ARM, "porting": "port-loop"},
                          iteration=1, lang="es", root=Path("/r"))
    assert one != loop


def test_two_iterations_cannot_write_the_same_rule_file():
    """`port-loop` rewrites its rule file every round and the sequence is the record.

    It is what the δ/k termination criterion was computed over and the only thing that
    can answer "which rules existed at iteration 4" afterwards. One path per arm keeps
    the last round and discards the history, which reduces the arm to its final state.
    """
    paths = {arm_rules_path(**ARM, iteration=i, lang="es", root=Path("/r"))
             for i in range(1, 12)}
    assert len(paths) == 11


def test_two_languages_of_one_iteration_sit_in_one_directory():
    """`{iteration}` is a directory, not a filename suffix (DESIGN §5.3).

    `es-carmen` emits `es` and `cat` in one round, and one round's rule state is both
    files. Under `iter3/{es,cat}.yaml` loading a round is listing a directory; under
    `{es,cat}_iter3.yaml` it is parsing filenames, and a parser is a component whose own
    error rate nothing here measures — §5.2's objection to a language selector.
    """
    es = arm_rules_path(**{**ARM, "corpus": "es-carmen"},
                        iteration=3, lang="es", root=Path("/r"))
    cat = arm_rules_path(**{**ARM, "corpus": "es-carmen"},
                         iteration=3, lang="cat", root=Path("/r"))
    assert es.parent == cat.parent
    assert es.parent.name == "iter3"


def test_the_screener_applies_the_rule_id_check_to_an_arm_path(tmp_path):
    """`tools/release_screen.py`'s rule_id vocabulary check has to reach the new path.

    That check is the only enforcement `rule_author.md` Prohibition 2 has — a surname in
    a rule name reaches a public `metrics.json` through the `by_rule` block by the
    intended path, and `metrics.json` is on the screener's *allow* list. A path the check
    does not match is not rejected: the check never runs, and the file is reported clean.
    That is the "silently matches nothing" failure, so this asserts through `sniff()` on a
    real path rather than against the pattern's text.

    The rule_id here is two lowercase ASCII tokens with no digits — the same shape as
    `street_type`, which is the point of the vocabulary being a vocabulary. Not a real
    surname: a test fixture holding one would put a surface form in the repository, which
    is what the check exists to prevent.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_screen_probe", ROOT / "tools" / "release_screen.py")
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)

    rel = str(arm_rules_path(**ARM, iteration=1, lang="es", root=tmp_path))
    target = Path(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "version: 1\nlang: es\nrules:\n  - rule_id: zzqx_wbbl\n"
        "    layer: gazetteer\n    phi_type: NAME\n    terms: [\"x\"]\n",
        encoding="utf-8")

    why = screen.sniff(str(target))
    assert why and "rule_id" in why, (
        "the screener did not apply its rule_id check to an arm's rule file. Either the "
        "arm path lost its `rules/` component or the screener's pattern did not follow "
        "it — and the failure is silent in the worse direction, because an unmatched "
        "file is reported clean rather than rejected."
    )


def test_that_check_still_passes_a_legitimate_mechanism_name(tmp_path):
    """The other half: a check that flagged everything would pass the test above too.

    Widening the screener's pattern to reach the arm path is only correct if it still
    admits the names it is supposed to admit — otherwise every agent arm stops on its own
    rule file and the pattern gets narrowed back.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_screen_probe2", ROOT / "tools" / "release_screen.py")
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)

    target = Path(arm_rules_path(**ARM, iteration=1, lang="es", root=tmp_path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "version: 1\nlang: es\nrules:\n  - rule_id: hospital_gazetteer\n"
        "    layer: gazetteer\n    phi_type: ORGANISATION\n    terms: [\"x\"]\n",
        encoding="utf-8")
    assert screen.sniff(str(target)) is None


# ─── unknown components are refused, not minted ──────────────────────────────


@pytest.mark.parametrize("bad", [
    {"corpus": "es-nonesuch"},
    {"detector": "rules-only"},
    {"supervision": "sup-none"},
    {"porting": "port-single"},
    {"lang": "xx"},
])
def test_an_unknown_component_is_refused(bad):
    """A results path names a cell, so a typo mints a cell instead of failing.

    `human_arm._arm_path()`'s reason, and the same one: `results/es-meddocan/rules-only/`
    would sit beside `results/es-meddocan/R/` looking like a second detector, and an
    aggregation walking these directories would report it as one.
    """
    with pytest.raises(RuleError):
        arm_rules_path(**{**ARM, "iteration": 1, "lang": "es", **bad})


@pytest.mark.parametrize("bad", [0, -1, 1.0, True, "1", None])
def test_an_iteration_that_is_not_a_positive_integer_is_refused(bad):
    """It is a path component. `iter1.0/` or `iterTrue/` puts a round where nothing looks."""
    with pytest.raises(RuleError):
        arm_rules_path(**ARM, iteration=bad, lang="es")


# ─── run_fold is told, and does not infer ────────────────────────────────────


def test_run_fold_does_not_build_an_arm_rule_path_from_its_own_axes():
    """Structural, because a behavioural test cannot tell inference from being told.

    On the happy path, a `run_fold` that derived `arm_rules_path()` from its own arguments
    and one that was handed the same paths produce identical output. What separates them
    is that the first has one possible input location — the arm being closed — so a trial
    file and the bootstrap file each need a special case, and the input becomes a function
    of the run block, which is the coupling that lets a run read its own results
    directory. So the assertion is about the syntax tree: `run_fold()` itself must not
    call the path builder. `main()` may — the CLI is where `--iteration` is turned into
    paths, and it passes them in.
    """
    module = ast.parse((ROOT / "src" / "eval" / "run_fold.py").read_text(
        encoding="utf-8"))
    fn = next(n for n in module.body
              if isinstance(n, ast.FunctionDef) and n.name == "run_fold")
    called = {c.func.attr if isinstance(c.func, ast.Attribute) else
              getattr(c.func, "id", "")
              for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "arm_rules_path" not in called, (
        "run_fold() builds an arm rule path itself. It is told which files to load "
        "(DESIGN §5.3); inferring the input from the run block is what makes an arm "
        "able to read its own results directory."
    )
    assert "rules" in {a.arg for a in fn.args.kwonlyargs}, (
        "run_fold() has no `rules` argument, so it cannot be told where the rule files "
        "are and must be inferring them."
    )


def test_the_cli_refuses_both_ways_of_saying_where_the_rules_are(capsys):
    """`--rules` and `--iteration` both name the input. A precedence order would be silent.

    Refused rather than resolved: a trial file scored under an arm's iteration number
    writes a run block naming one path while the reader has the other command in their
    shell history, and nothing in the output says which won.
    """
    from src.eval import run_fold as rf
    code = rf.main(["--corpus", "es-meddocan", "--detector", "R",
                    "--supervision", "sup-free", "--porting", "port-oneshot",
                    "--iteration", "1", "--rules", "/tmp/nope.yaml"])
    assert code == 2
    assert "both say where" in capsys.readouterr().err


# ─── the record says which file ──────────────────────────────────────────────


def test_the_source_path_is_recorded_for_a_file_that_was_read(tmp_path):
    """`rules_source`, the thing a version integer cannot be.

    Across an overwrite the version integer stays plausible — it is whatever the author
    declared — so it cannot say which arm and which iteration produced the rules the
    numbers were computed from. The path can.
    """
    path = tmp_path / "es.yaml"
    path.write_text(RULE_FILE, encoding="utf-8")
    rs = load_rules("es", path=path)
    assert rs.versions == {"es": 2}
    assert rs.sources == {"es": f"<outside-repo>/{path.name}"}


def test_an_absent_file_is_still_recorded_as_a_place_that_was_looked_at(tmp_path):
    """A zero-rule run has a premise too.

    Iteration 0 is a real state — the baseline before its first call — and "we read
    nothing" and "we looked here and it was not there" are different facts. An empty
    `sources` would make the run block silent about where.
    """
    rs = load_rules("es", path=tmp_path / "absent.yaml")
    assert rs.rules == []
    assert rs.sources == {"es": "<outside-repo>/absent.yaml"}


def test_a_repo_file_is_recorded_relative_and_never_absolute():
    """The run block is published: an absolute path names a home directory.

    On a machine where the corpus checkout sits beside the repository it also names the
    directory layout of DUA data, which is the sort of thing CLAUDE.md's "offsets and
    types only" rule exists to keep out of published files.
    """
    rs = load_rules("es", path=ROOT / "rules" / "es.yaml")
    assert rs.sources == {"es": "rules/es.yaml"}
    assert not Path(rs.sources["es"]).is_absolute()
    assert str(ROOT) not in rs.sources["es"]


def test_the_bootstrap_default_is_the_committed_example_and_not_an_arm_path():
    """`load_rules` with no path reads `paths.rules`, which no arm writes to.

    Kept as a default rather than made required because the bootstrap and a practice
    file both need it. What must not happen is the default quietly becoming an arm's
    path, which would make "the format example" and "somebody's results" the same file.
    """
    assert path_template("rules") == "rules/{lang}.yaml"
    assert "results/" not in path_template("rules")
    assert path_template("armrules").startswith("results/")


# ─── the three auxiliary inputs, scoped the same way (DESIGN §4) ──────────────

#: The agent-authored auxiliary inputs. `port-multi` adds one capability to `port-loop` and
#: it is the authorship of these three; before this scoping they were named only by axis-free
#: keys, so two arms' Profilers would write one file.
AUX_KEYS = ("armprofile", "armmapping", "armlexicon")

#: What each one replaces. The pairing is asserted rather than assumed because the whole
#: argument for adding keys instead of widening these is that the hand-written versions keep
#: their positions — `paths.lexicon` has a live consumer in `src/rules.py` and `profiles/`
#: holds tracked files.
HAND_KEYS = {"armprofile": "profile", "armmapping": "mapping", "armlexicon": "lexicon"}

#: The three of `ROUND_AXES` that distinguish one arm from another. `corpus` is the fourth and
#: is deliberately not here: the hand-written profile and mapping are per-corpus and say so in
#: their templates, so asserting they carry no axis at all would be asserting the wrong thing.
#: What must not appear in them is the axes that make a path an arm's.
ARM_AXES = {"detector", "supervision", "porting"}


def _fields(key):
    import string
    return {n for _, n, _, _ in string.Formatter().parse(path_template(key)) if n}


def test_the_auxiliary_input_paths_carry_the_four_axes():
    arm = "results/es-meddocan/R/sup-free/port-oneshot"
    assert path_template("armprofile").format(**ARM) == f"{arm}/profile.json"
    assert path_template("armmapping").format(**ARM) == f"{arm}/mapping.yaml"
    assert path_template("armlexicon").format(**ARM, lang="es") == f"{arm}/lexicons/es/"


@pytest.mark.parametrize("key", AUX_KEYS)
def test_two_arms_cannot_write_the_same_auxiliary_input(key):
    """The collision, stated as the property — the third recurrence of it.

    `armfreeze`/`humanfreeze` was two arms sharing a *record*, `armrules`/`rules` was two arms
    sharing a *rule input*, and this is two arms sharing the inputs the loop reads before it
    writes anything. It is the worst of the three in one specific way: a rule file is
    overwritten after the earlier arm has finished, but a later arm's Profiler can overwrite
    these while the earlier arm is still iterating. Then both arms run on inputs that are
    partly each other's, and neither arm's result corresponds to its own input.
    """
    extra = {"lang": "es"} if "lang" in _fields(key) else {}
    one = path_template(key).format(**{**ARM, "porting": "port-oneshot"}, **extra)
    multi = path_template(key).format(**{**ARM, "porting": "port-multi"}, **extra)
    assert one != multi


@pytest.mark.parametrize("key", AUX_KEYS)
def test_an_auxiliary_input_path_carries_no_iteration(key):
    """Where these part from `armrules`, and it is not an oversight.

    DESIGN §4 defines the capability these three constitute as authorship of *inputs the loop
    reads and never revises*. An `{iteration}` component would have the path assert they are
    re-authored each round, which is what that definition denies — so the shape to copy is
    `armfreeze`'s four axes, written once per arm, not `armrules`'s five.

    Asserted over the template's fields rather than by searching for the substring `iter`, so
    that a key spelling the round some other way is caught as well.
    """
    assert "iteration" not in _fields(key), (
        f"paths.{key} carries an iteration. §4's capability is authorship of an input the "
        "loop reads and never revises; a round component makes the path claim otherwise."
    )
    assert set(ROUND_AXES) <= _fields(key)


@pytest.mark.parametrize("key", AUX_KEYS)
def test_the_hand_written_counterpart_keeps_its_axis_free_path(key):
    """Why three keys were added instead of three templates being widened.

    `paths.lexicon` is read by `src/rules.py` when a rule declares its terms by list name — a
    live consumer that formats the template with `lang` and nothing else, so an axis appearing
    in it is a field that caller cannot fill. `paths.profile` names the position whose
    hand-written instances are tracked in `profiles/` today. Widening either would leave a
    committed input at a path no key names, which is the `armfreeze` migration §4 refused.
    """
    hand = HAND_KEYS[key]
    assert not ARM_AXES & _fields(hand), (
        f"paths.{hand} has gained an arm axis. It is the hand-written position, and for "
        "`lexicon` it is also what src/rules.py formats with `lang` alone."
    )
    assert path_template(hand) != path_template(key)
    assert not path_template(hand).startswith("results/")
    assert path_template(key).startswith("results/")


def test_only_the_agent_lexicon_is_denied_of_the_three(tmp_path):
    """The classification splits, and the split is the thing to assert.

    Each artefact inherits the classification of the hand-written version it replaces, because
    changing the author does not change the file's content risk: the profile's human version is
    tracked and the mapping's is published in DESIGN §9.0, so both are left to the content
    sniffer. The lexicon has no hand-written instance to inherit from and its content is a
    list of institution, region and department names and nothing else — a rule file carries
    term lists too, but beside patterns and cue words, which is the surface the rule_id
    vocabulary check and Prohibition 2 act on. This file has no such surface.

    Asserted through `deny()` on real relative paths, for the reason
    `test_the_screener_applies_the_rule_id_check_to_an_arm_path` gives: a pattern that
    silently matches nothing reports a file as clean rather than rejecting it.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_screen_probe3", ROOT / "tools" / "release_screen.py")
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)

    lex = path_template("armlexicon").format(**ARM, lang="es") + "terms.txt"
    assert screen.deny(lex), (
        "an arm's agent-authored lexicon is not denied. It is a bare list of identifying "
        "surface forms, and the screener cannot tell from the path whether it came from a "
        "public gazetteer or from dev text."
    )
    for key in ("armprofile", "armmapping"):
        assert not screen.deny(path_template(key).format(**ARM)), (
            f"paths.{key} is denied. Its hand-written counterpart is published, so the "
            "agent-authored one inherits that classification and is left to the sniffer; "
            "denying it would also hide a format record nobody can then read."
        )


def test_the_split_path_is_axis_free_on_purpose_and_stays_that_way():
    """The reason the recurrence verdict stops at three keys and does not reach this one.

    An arm axis here would let each arm choose its own folds, and then two arms' scores are
    numbers about different test sets — §4's ladder compares nothing. It also empties the
    seal: what is sealed is the fold this file names, so an arm that picks its own fold has
    sealed something of its own. `paths.rules` is excluded from the verdict for a different
    reason, which is that `armrules` exists.
    """
    assert path_template("split") == "splits/{corpus}.json"
    assert not ARM_AXES & _fields("split")
