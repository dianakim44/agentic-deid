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
    somebody spends the whole run; at 185 mutations that is about fourteen hours serial —
    derived, not measured, from the 2.07 h measured across eight shards
    (`tests/mutations/README.md` §"What it actually costs, measured") — and until then the
    anchor is stale and
    nothing says so.

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


# ─── sharding: the parallel run must be a partition, and provably one tree ───
#
# Everything below is about `tests/mutations/parallel.py`, which turns thirteen serial
# hours into two. The saving is only worth having if a partial run cannot be recorded as
# a full one, so the driver's refusals are tested here — at millisecond cost, on
# synthetic shard records — rather than discovered during the two hours.


@pytest.mark.parametrize("shards", [1, 2, 3, 8, 13])
def test_the_shards_partition_the_mutations(shards):
    """Union of all shards is every mutation, once. Both halves matter, differently.

    A name in two shards is two suite runs spending the same six minutes twice and the
    gate learning nothing extra. A name in *no* shard is the failure this whole file is
    about: eight green shards, one guarantee never measured, and a log that reads as a
    full run. `run.py` slices `MUTATIONS[i::n]`, so this holds by construction — which is
    exactly the kind of claim that stops holding when somebody adds a filter to the slice.
    """
    seen = [m.name for i in range(shards) for m in harness.MUTATIONS[i::shards]]
    assert sorted(seen) == sorted(m.name for m in harness.MUTATIONS)
    assert len(seen) == len(set(seen))


def test_round_robin_spreads_the_expensive_mutations():
    """Contiguous blocks would put one file's mutations in one shard, and kill counts
    range over two orders of magnitude. Not a correctness property — a wall-clock one, and
    the reason `[i::n]` is a slice rather than a block."""
    per_shard = [sum(m.min_kills for m in harness.MUTATIONS[i::8]) for i in range(8)]
    assert max(per_shard) <= 3 * min(per_shard), per_shard


def test_the_fingerprint_is_deterministic_and_sees_content(tmp_path):
    """Two identical trees hash equal; a one-byte edit anywhere makes them differ.

    This is the invariant that lets eight shards claim they measured one tree. Equal
    baseline *totals* are not enough for that claim: two trees can collect the same number
    of tests and still behave differently, and a kill count is a statement about behaviour.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    for tree in (a, b):
        (tree / "src").mkdir(parents=True)
        (tree / "src" / "x.py").write_text("x = 1\n", encoding="utf-8")
        (tree / "top.txt").write_text("same\n", encoding="utf-8")
    assert harness.tree_fingerprint(a) == harness.tree_fingerprint(b)

    (b / "src" / "x.py").write_text("x = 2\n", encoding="utf-8")
    assert harness.tree_fingerprint(a) != harness.tree_fingerprint(b)


def test_the_fingerprint_notices_a_file_that_is_only_renamed(tmp_path):
    """Content-only hashing would call a rename identical. The path is in the digest
    because a test file under a different name is collected differently."""
    a, b = tmp_path / "a", tmp_path / "b"
    for tree in (a, b):
        tree.mkdir()
    (a / "one.py").write_text("x = 1\n", encoding="utf-8")
    (b / "two.py").write_text("x = 1\n", encoding="utf-8")
    assert harness.tree_fingerprint(a) != harness.tree_fingerprint(b)


def test_the_fingerprint_does_not_follow_symlinks(tmp_path):
    """`data/raw` and `sealed/` are symlinked into every tree. Hashing through them would
    read the sealed test fold — a DUA and blinding violation for the sake of a checksum —
    and would also make the fingerprint depend on corpus size. They are skipped, so a tree
    with the links hashes the same as one without."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("must not be read\n", encoding="utf-8")

    plain, linked = tmp_path / "plain", tmp_path / "linked"
    for tree in (plain, linked):
        tree.mkdir()
        (tree / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (linked / "sealed").symlink_to(outside, target_is_directory=True)
    (linked / "loose.txt").symlink_to(outside / "secret.txt")

    assert harness.tree_fingerprint(plain) == harness.tree_fingerprint(linked)


def test_the_marker_is_outside_the_fingerprint(tmp_path):
    """The marker is written by `apply()`, so including it would make a mutated tree's
    fingerprint differ from the pristine one it was copied from — and the fingerprint is
    about which *repository* the shard measured, not what it did to its copy."""
    a, b = tmp_path / "a", tmp_path / "b"
    for tree in (a, b):
        tree.mkdir()
        (tree / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (b / harness.MARKER).write_text("probe\n", encoding="utf-8")
    assert harness.tree_fingerprint(a) == harness.tree_fingerprint(b)


# ─── the driver's five refusals ──────────────────────────────────────────────

import importlib.util  # noqa: E402
import json  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "mutations_parallel", os.path.join(ROOT, "tests", "mutations", "parallel.py")
)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)

_CLEAN = {"head": "a" * 40, "porcelain": ""}


def _shard(i, names, *, complete=True, fingerprint="f" * 64, baseline=1696, exit=0,
           aborted=None):
    return {
        "shard": i, "exit": exit, "log": f"/tmp/shard{i}.log",
        "record": {
            "shard": i, "shards": 2, "selected": list(names),
            "fingerprint": fingerprint, "baseline_outcomes": baseline,
            "results": [{"name": n, "verdict": "caught", "kills": 3, "min_kills": 1,
                         "message": None} for n in names],
            "complete": complete, "aborted": aborted,
        },
    }


def test_two_good_shards_are_a_full_run():
    """The negative controls below are only meaningful if the positive one passes."""
    shards = [_shard(0, ["a", "c"]), _shard(1, ["b"])]
    assert driver.check(shards, ["a", "b", "c"], _CLEAN, _CLEAN) == []


def test_a_mutation_measured_by_nobody_is_refused():
    shards = [_shard(0, ["a"]), _shard(1, ["b"])]
    broken = driver.check(shards, ["a", "b", "c"], _CLEAN, _CLEAN)
    assert any("never measured" in r and "'c'" in r for r in broken), broken


def test_a_mutation_measured_twice_is_refused():
    """Not merely wasteful: the aggregate's verdict counts would sum past the number
    registered, and the next run's diff would compare against a doubled entry."""
    shards = [_shard(0, ["a", "b"]), _shard(1, ["b"])]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert any("more than once" in r for r in broken), broken


def test_shards_on_different_trees_are_refused():
    shards = [_shard(0, ["a"]), _shard(1, ["b"], fingerprint="e" * 64)]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert any("different trees" in r for r in broken), broken


def test_equal_baselines_do_not_excuse_unequal_fingerprints():
    """The whole reason the fingerprint exists. Both shards collected 1696 tests and the
    run is still refused, because the trees were not the same tree."""
    shards = [_shard(0, ["a"], baseline=1696),
              _shard(1, ["b"], baseline=1696, fingerprint="e" * 64)]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert broken and all("baseline" not in r for r in broken), broken


def test_disagreeing_baselines_are_refused():
    shards = [_shard(0, ["a"]), _shard(1, ["b"], baseline=1695)]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert any("baseline" in r for r in broken), broken


def test_a_shard_that_never_finished_is_refused():
    """`complete` is written last on purpose. A shard killed mid-run leaves results that
    look fine — this is the check that keeps twenty-one good measurements from being
    read as twenty-two."""
    shards = [_shard(0, ["a"]), _shard(1, ["b"], complete=False)]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert any("did not finish" in r for r in broken), broken


def test_a_shard_that_wrote_nothing_is_refused():
    """Distinguished from an unfinished shard because it has no results to report on:
    an absent file is what an OOM kill before the first write looks like."""
    shards = [_shard(0, ["a"]),
              {"shard": 1, "exit": -9, "record": None, "log": "/tmp/shard1.log"}]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert any("no JSON" in r for r in broken), broken
    assert any("never measured" in r for r in broken), broken


def test_an_unrecognised_exit_status_is_refused():
    """0 and 1 are the harness's own verdicts. Anything else — a signal, a traceback in
    `main` — means the exit code is not reporting on mutations at all."""
    shards = [_shard(0, ["a"]), _shard(1, ["b"], exit=2)]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    assert any("unrecognised status" in r for r in broken), broken


def test_a_working_tree_that_moved_under_the_run_is_refused():
    """Not a claim that the counts are wrong — every shard copied before the edit, so
    they are all about one tree. What is lost is the record's ability to say *which*
    tree, i.e. whether re-running at this commit reproduces these numbers."""
    after = {"head": "a" * 40, "porcelain": " M src/x.py"}
    shards = [_shard(0, ["a"]), _shard(1, ["b"])]
    broken = driver.check(shards, ["a", "b"], _CLEAN, after)
    assert any("changed while the run was in flight" in r for r in broken), broken


def test_a_commit_during_the_run_is_refused():
    """A commit can leave `porcelain` identical — clean before, clean after — while
    moving HEAD out from under the record. Both fields are compared for that reason."""
    shards = [_shard(0, ["a"]), _shard(1, ["b"])]
    broken = driver.check(shards, ["a", "b"], _CLEAN, {"head": "b" * 40, "porcelain": ""})
    assert any("changed while the run was in flight" in r for r in broken), broken


def test_a_dirty_tree_is_not_by_itself_a_refusal():
    """Running the gate on uncommitted work is the normal case — that is when it is
    useful. Dirtiness is recorded, not refused; only *change* during the run is refused."""
    dirty = {"head": "a" * 40, "porcelain": " M src/x.py"}
    shards = [_shard(0, ["a"]), _shard(1, ["b"])]
    assert driver.check(shards, ["a", "b"], dirty, dirty) == []


def test_an_incomplete_run_says_so_in_the_record_it_writes():
    """The refusal has to survive into `docs/notes/`, not just the exit code: the file is
    what a reader consults months later, and an unmarked partial entry there is the
    original failure with a longer fuse."""
    shards = [_shard(0, ["a"]), _shard(1, ["b"], complete=False)]
    broken = driver.check(shards, ["a", "b"], _CLEAN, _CLEAN)
    text, measured = driver.render("INCOMPLETE", broken, shards, ["a", "b"], 42.0,
                                  _CLEAN, _CLEAN, {"a": 3})
    assert "INCOMPLETE" in text
    assert "not a full-run record" in text
    assert "did not finish" in text


def test_the_full_run_covered_the_current_test_files():
    """The full-run trigger, enforced instead of remembered.

    Every kill count is a fraction of `TEST_FILES`. Change which files are in that list and
    the counts are not *older*, they are **about a different denominator** — the README says
    so in its own words, having gone 11 files → 28 and 531 tests → 1867 with a hundred-odd
    table cells written against the old suite. So the one condition under which nothing short
    of a full run will do is a change to this list, and that is a decidable condition rather
    than a judgement call, which is the whole reason it is a test.

    Deliberately a hard failure and not a skip. A skip here is the vacuous absence this file
    documents twice over — the loader fixture that turned a bug into `pytest.skip`, the
    assertion that passed on a substring two error messages shared — and "no full-run record
    exists" is not a reason to pass, it is the finding. Safe to be hard because this file is
    outside `TEST_FILES`: it cannot redden a mutation baseline, only the human's own suite.

    Cost of satisfying it is about two hours (`tests/mutations/parallel.py`), which is the
    price of the counts meaning anything. Everything *else* — a narrow `src/` change, a new
    assertion in one test file — is served by an impact-scope run; see
    `tests/mutations/README.md` §"When a full run is required".
    """
    sidecar = os.path.join(ROOT, "docs", "notes", "mutation-full-runs.counts.json")
    assert os.path.exists(sidecar), (
        "no full run has ever been recorded. Run `python3 tests/mutations/parallel.py`; "
        "until then no kill count in tests/mutations/README.md has a run behind it"
    )
    with open(sidecar, encoding="utf-8") as fh:
        recorded = json.load(fh)

    was, now = list(recorded["test_files"]), list(harness.TEST_FILES)
    added = [f for f in now if f not in was]
    removed = [f for f in was if f not in now]
    assert not added and not removed, (
        f"TEST_FILES changed since the full run of {recorded['date']} "
        f"(commit {recorded['commit'][:12]}): added {added}, removed {removed}. Every kill "
        "count is a fraction of this list, so the recorded ones are now about a different "
        "suite. A full run is required — impact scope cannot cover this, because the change "
        f"is to the denominator of all {len(harness.MUTATIONS)} counts and not to any one "
        "of them"
    )


def test_a_kill_count_that_fell_is_reported_ahead_of_the_increases():
    """Order is content here. A decrease means a test stopped seeing a defect it used to
    see, which is the only thing in the report that is a finding rather than a number."""
    shards = [_shard(0, ["a"]), _shard(1, ["b"])]
    text, measured = driver.render("full run", [], shards, ["a", "b"], 42.0,
                                   _CLEAN, _CLEAN, {"a": 9, "b": 1})
    assert measured == {"a": 3, "b": 3}
    assert "Decreases" in text
    assert text.index("Decreases") < text.index("Increases")
    assert "`a` **9 → 3**" in text
    assert "`b` 1 → 3" in text
