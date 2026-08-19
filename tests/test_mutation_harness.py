"""Tests for the mutation harness itself.

`tests/mutations/run.py` is what licenses the claim that the other suites are
load-bearing. That makes its own failure mode the worst one available here: a
harness that miscounts its self-inflicted breakage as a kill reports green while
testing nothing, and every guarantee downstream of it becomes a claim about a check
rather than a check.

It has already happened once, which is why these tests exist rather than a comment.
Wrapping `docs = list(self._read())` in a `try/finally` re-indented it; the
`drop_excluded` anchor went on matching the old text at the wrong nesting level; the
resulting file did not parse; pytest errored on every test in it; and the run printed
"caught by 37 tests". Nothing had been tested. Note the shape — it is the same one as
the loader fixture that turned a real bug into `pytest.skip`, one layer up: a
mechanism that cannot tell "the check worked" from "the check could not run" resolves
the ambiguity in the reassuring direction.

Deliberately not in `run.py`'s own TEST_FILES: mutating the loader must not change
how many tests the harness runs, and these do not exercise the loader.

    python3 -m pytest tests/test_mutation_harness.py -q
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "mutations"))

import run as harness  # noqa: E402


def _mutation(**kwargs):
    base = dict(
        name="probe",
        path="m.py",
        anchor="x = 1",
        replacement="x = 2",
        breaks="a probe, not a real mutation",
        min_kills=1,
    )
    base.update(kwargs)
    return harness.Mutation(**base)


def _tree(tmp_path, source="x = 1\n"):
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    return tmp_path


# ─── step 1: the anchor must exist ──────────────────────────────────────────


def test_a_missing_anchor_is_stale_not_applied(tmp_path):
    """A refactor that removes the anchored text must be reported, not ignored."""
    with pytest.raises(harness.StaleMutation, match="anchor not found"):
        _mutation(anchor="y = 9").apply(_tree(tmp_path))


def test_a_present_anchor_applies(tmp_path):
    tree = _tree(tmp_path)
    _mutation().apply(tree)
    assert (tree / "m.py").read_text(encoding="utf-8") == "x = 2\n"


# ─── step 2: the file must actually change ──────────────────────────────────


def test_a_replacement_equal_to_its_anchor_is_stale(tmp_path):
    """The no-op that passes the anchor check.

    Reachable by ordinary means: a copy-paste where the replacement was never
    edited, or an anchor updated to match the code *after* the code changed in the
    direction the mutation was meant to introduce. It would then be reported as
    caught by however many tests happened to be failing for other reasons.
    """
    with pytest.raises(harness.StaleMutation, match="unchanged"):
        _mutation(replacement="x = 1").apply(_tree(tmp_path))


# ─── step 3: the result must be runnable ────────────────────────────────────


def test_a_mutation_that_does_not_parse_is_stale(tmp_path):
    """The recorded incident, reproduced in miniature.

    A SyntaxError takes out every test in the file at collection time. Those are
    errors, `kills()` counts errors, and the count is large — so the harness's own
    breakage is indistinguishable from a thoroughly caught mutation unless the
    parse is checked.
    """
    with pytest.raises(harness.StaleMutation, match="does not parse"):
        _mutation(replacement="x = (").apply(_tree(tmp_path))


def test_the_indentation_case_specifically(tmp_path):
    """Anchors are indentation-blind, which is how the real one got through.

    The anchor matched a line that had been re-indented into a `try:` block, so the
    replacement's own indentation was wrong for its new position. Nothing about the
    anchor check can notice that; only parsing the result can.
    """
    source = "def f():\n    try:\n        a = 1\n    finally:\n        pass\n"
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    mutation = _mutation(
        anchor="        a = 1",
        replacement="        a = 1\n    b = 2",   # dedented into the try block
    )
    with pytest.raises(harness.StaleMutation, match="does not parse"):
        mutation.apply(tmp_path)


def test_a_non_python_file_is_not_parsed(tmp_path):
    """`split_file_span_count` mutates JSON. The parse check must not reject it."""
    (tmp_path / "d.json").write_text('{"n": 1}\n', encoding="utf-8")
    _mutation(path="d.json", anchor='"n": 1', replacement='"n": 2').apply(tmp_path)
    assert (tmp_path / "d.json").read_text(encoding="utf-8") == '{"n": 2}\n'


def test_every_edit_of_a_multi_part_mutation_is_verified(tmp_path):
    """`also` edits get the same three checks as the primary one.

    `familiares_as_other` is only faithful to its name when both of its edits land,
    so a stale second anchor must fail the mutation rather than half-apply it.
    """
    _tree(tmp_path)
    with pytest.raises(harness.StaleMutation, match="anchor not found"):
        _mutation(also=(("m.py", "not present anywhere", ""),)).apply(tmp_path)


# ─── step 0: the tree must not already carry a mutation ─────────────────────


def test_a_second_mutation_on_the_same_tree_is_refused(tmp_path):
    """The probe-tree contamination, as a check.

    Three per-test attributions in README.md were wrong because a tree was built from a
    shell whose working directory had drifted into an already-mutated copy, and the second
    edit landed on top of the first. Every count in that run was right — `main()` copies
    `pristine` per mutation — and the reading of which tests caught which mutation was not.
    Nothing in the output tells the two apart: both edits apply cleanly, the suite runs, and
    the number is a real number about a tree nobody meant to build.

    So the refusal is at *construction*: the second `apply()` raises before the suite runs,
    and the count that could be misread is never produced.
    """
    tree = _tree(tmp_path)
    _mutation().apply(tree)
    with pytest.raises(harness.ContaminatedTree, match="already carries a mutation"):
        _mutation(name="second", anchor="x = 2", replacement="x = 3").apply(tree)
    assert (tree / "m.py").read_text(encoding="utf-8") == "x = 2\n", (
        "the refused mutation must not have edited anything"
    )


def test_the_marker_names_what_was_applied(tmp_path):
    """The marker is read by a human when a probe result looks surprising, so it has to say
    *which* mutation, not merely that there was one. `--probe` prints it beside the tree's
    absolute path for that reason."""
    tree = _tree(tmp_path)
    _mutation(name="the_first_one").apply(tree)
    assert (tree / harness.MARKER).read_text(encoding="utf-8").strip() == "the_first_one"


def test_a_stale_mutation_leaves_the_tree_usable(tmp_path):
    """A mutation that raised did not mutate, so the tree must not be marked as carrying
    one. Otherwise fixing an anchor and retrying reports contamination instead — a refusal
    for a reason that is not true, which is its own kind of misleading count."""
    tree = _tree(tmp_path)
    with pytest.raises(harness.StaleMutation):
        _mutation(anchor="y = 9").apply(tree)
    assert not (tree / harness.MARKER).exists()
    _mutation().apply(tree)
    assert (tree / "m.py").read_text(encoding="utf-8") == "x = 2\n"


# ─── reading pytest's output ────────────────────────────────────────────────


def test_a_collection_interrupt_is_not_a_kill_count():
    """"Interrupted: N errors during collection" means no test ran at all."""
    output = (
        "ERROR tests/test_x.py\n"
        "!!!!!!!! Interrupted: 1 error during collection !!!!!!!!\n"
        "1 error in 0.07s\n"
    )
    with pytest.raises(harness.BrokenSuite, match="collection"):
        harness.kills(output)


def test_the_baseline_may_read_a_broken_suite_without_raising():
    """The baseline reports the breakage itself, so it asks with expect_ran=False."""
    output = "!!!! Interrupted: 1 error during collection !!!!\n1 error in 0.1s\n"
    assert harness.kills(output, expect_ran=False) == 1


def test_ordinary_failures_are_counted():
    output = "2 failed, 130 passed in 2.58s\n"
    assert harness.kills(output) == 2
    assert harness.outcomes(output) == 132


def test_errors_count_as_kills():
    """A mutation that breaks a fixture takes out whole tests. Those are caught."""
    output = "3 errors, 129 passed in 2.6s\n"
    assert harness.kills(output) == 3


def test_outcomes_detects_a_suite_that_shrank():
    """The milder broken-suite case: it collects, runs, and covers less.

    `run.py` compares this against the baseline. A mutation is supposed to change
    which tests pass, never how many exist — so an equal-or-different total is the
    difference between a kill count that means something and one that does not.
    """
    assert harness.outcomes("132 passed in 2.9s\n") == 132
    assert harness.outcomes("40 passed in 1.1s\n") == 40


def test_internal_error_is_not_a_kill_count():
    with pytest.raises(harness.BrokenSuite):
        harness.kills("INTERNALERROR> Traceback (most recent call last):\n")


# ─── the table and the code must agree ──────────────────────────────────────


def test_every_mutation_is_documented():
    """A mutation absent from README.md is one nobody can interpret the count of."""
    readme = os.path.join(ROOT, "tests", "mutations", "README.md")
    with open(readme, encoding="utf-8") as fh:
        text = fh.read()
    undocumented = [m.name for m in harness.MUTATIONS if f"`{m.name}`" not in text]
    assert not undocumented, f"not in README.md: {undocumented}"


def test_mutation_names_are_unique():
    names = [m.name for m in harness.MUTATIONS]
    assert len(names) == len(set(names))


def test_every_mutation_targets_a_file_that_exists():
    """A stale path would be reported as a missing anchor, which reads as a refactor."""
    for m in harness.MUTATIONS:
        for path in (m.path, *(p for p, _, _ in m.also)):
            assert os.path.exists(os.path.join(ROOT, path)), f"{m.name}: {path}"


def test_every_anchor_is_present_in_its_target():
    """The same check `apply()` makes, made against the working tree instead of a copy.

    `apply()` already refuses a vanished anchor, so nothing here is a new guarantee —
    what is new is *when the answer arrives*. `apply()` runs inside the harness, one
    mutation per full suite run, so a refactor that moves an anchor is reported only when
    somebody spends the whole run; at 170 mutations and six minutes each that is fifteen
    hours, and until then the anchor is stale and nothing says so.

    Two were, and that is why this exists rather than the comment above it, which
    considered a stale *path* and not a stale anchor. `run_fold_skips_axis_validation`
    stopped matching when §5.5's round widening inserted a branch between `check_run(run)`
    and the template; `absent_token_counts_default_to_zero` stopped matching when
    `prompt_tokens` became the raw total and this read was renamed `input_tokens`. Both
    edits were correct and neither mentioned this harness — which is the point: the drift
    is caused by ordinary work on the code the mutation is aimed at, so the notice has to
    be cheap enough to run beside that work.

    It is a string search, the same one `apply()` performs, so it costs milliseconds and
    fails in the pytest suite on the commit that moves the code.
    """
    stale = []
    for m in harness.MUTATIONS:
        for path, anchor, _ in ((m.path, m.anchor, m.replacement), *m.also):
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                if anchor not in fh.read():
                    stale.append(f"{m.name} -> {path}")
    assert not stale, (
        "anchors no longer present in their target; update them in tests/mutations/run.py "
        f"so each mutation still tests what its name claims: {stale}"
    )
