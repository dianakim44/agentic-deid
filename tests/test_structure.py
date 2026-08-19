"""The check that a guarantee is never *only* patched away — its cheap half.

`tools/check_patched_guarantees.py` is the check. It runs the whole suite under a profiler
and fails when a first-party function is replaced by tests and executed by none, which is
the failure form that produced four holes in this repository: `tree_state`'s dirty
detector, its `unknown` branch, `_verify_frozen_split`, and `check_region`'s handling of an
unreadable IAM response. Each of those was written correctly, was documented, and had no
coverage, and in every case the suite was green and the test count said nothing.

This file holds the parts of that check which do not need a suite run:

  1. **The candidate survey works** — it finds the patch sites and the first-party
     definitions, on text rather than on imports.
  2. **The allowlist cannot be widened quietly** — an entry names a file and a function,
     carries an evaluable reason, and dies when what it describes goes away.
  3. **The check cannot be disabled from inside the suite** — no test may touch the
     profiler, the recorder, or the allowlist, and the checker takes no argument that
     narrows what it judges.
  4. **The check can fail** — measured on a synthetic patched-and-never-run function,
     because a structural check that matches nothing is the defect it is meant to prevent.

## The axis this covers, against the one `tests/test_conftest.py` covers

The two are opposites, and the pair is the point:

    test_conftest.py     a test doing too much  — deciding availability privately, so a
                         real bug can present itself as an absent corpus and delete tests
    this file            a test removing too much — replacing the function that holds a
                         guarantee, so the guarantee leaves the suite entirely

Both are ways a green suite reports more than it measured, and neither is visible in a
count of passing tests. `tests/test_conftest.py` exists because one defect shipped four
times; this one exists because a second defect shipped four times. In both cases the
incident had already been written up in `tests/mutations/README.md` before its last
occurrences, which is the argument for a check rather than a note: prose does not fail.

    python3 -m pytest tests/test_structure.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
CHECKER = ROOT / "tools" / "check_patched_guarantees.py"
ALLOWLIST = ROOT / "tools" / "patch_allowlist.json"

sys.path.insert(0, str(ROOT))


def load_checker(module_name="_patch_checker"):
    """Import the tool by path. It lives in `tools/`, which is not a package."""
    spec = importlib.util.spec_from_file_location(module_name, CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return load_checker()


def suite_files() -> list[Path]:
    """Every test file, with the glob's result pinned before it is returned.

    Three tests below loop over this and assert an absence inside the loop, so an empty
    list makes all three pass having examined nothing — and `tests/test_conftest.py`'s
    copy of this function feeds five more, one of which (`CONFTEST not in suite_files()`)
    would go from true-for-the-right-reason to true-for-no-reason. Nothing asserted that
    until now; the glob was correct by construction and would have failed silently on the
    day it stopped being (`tests/mutations/README.md`, "the glob that was right and
    unasserted").

    The pin is *this file finding itself* rather than a count or a bare non-emptiness
    check: a count drifts as files are added, non-emptiness survives a glob that has
    drifted to a neighbouring directory with tests in it, and the self-reference cannot
    be satisfied by any directory but the right one.
    """
    files = sorted(TESTS.glob("test_*.py"))
    assert Path(__file__).resolve() in files, (
        f"suite_files() globbed {TESTS} and did not find this file. Every loop over it "
        "asserts an absence, so the result is a suite-wide pass over nothing."
    )
    return files


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ─── 1. the survey finds what is really there ───────────────────────────────


def test_the_survey_finds_the_monkeypatch_sites(checker):
    """Measured against a known site rather than against a count, which would drift."""
    sites = checker.patch_sites()
    assert "tree_state" in sites
    assert "test_seal.py" in sites["tree_state"]


def test_the_survey_also_understands_mock_patch(checker, tmp_path):
    """The suite uses monkeypatch throughout. A new file may not, and a check that knows
    only the current idiom stops covering the next one without saying so."""
    (tmp_path / "test_elsewhere.py").write_text(
        "from unittest import mock\n"
        "def test_x():\n"
        "    with mock.patch('src.eval.sealed_log.tree_state'):\n"
        "        pass\n", encoding="utf-8")
    sites = checker.patch_sites(tmp_path)
    assert sites.get("tree_state") == {"test_elsewhere.py"}


def test_the_survey_reads_text_and_does_not_import(checker):
    """Every test file is surveyed, including ones that cannot be imported on a machine
    without the corpus. Importing to survey would make an unrelated import error look
    like a clean result — the shape this whole family is about."""
    source = CHECKER.read_text(encoding="utf-8")
    survey = source[source.index("def patch_sites"):source.index("def first_party_defs")]
    assert "import_module" not in survey and "exec_module" not in survey


def test_first_party_definitions_carry_their_file(checker):
    """Keyed by name *and* file, so a name defined in two modules yields two candidates.
    Running `src/a.py`'s `check()` is not evidence about `src/b.py`'s."""
    defs = checker.first_party_defs()
    assert "src/eval/sealed_log.py" in defs["tree_state"]
    assert all(isinstance(v, set) for v in defs.values())


def test_only_first_party_functions_are_candidates(checker):
    """`subprocess.run`, `boto3.client` and `Path.write_text` are all patched in the suite.
    They are somebody else's guarantee, and faking the boundary to the outside world is
    how a test gets to run at all."""
    patched = checker.patch_sites()
    assert {"run", "client", "write_text"} <= set(patched), "the survey missed these"
    names = {name for _file, name in checker.candidates()}
    assert not ({"run", "client", "write_text"} & names)


def test_paths_and_constants_are_not_candidates(checker):
    """The discriminator, and the reason it needs no curated list: patching **data or a
    path** is legitimate and patching **the function holding the guarantee** is not.

    `human_arm.ROOT` (36 sites), `sealed_log.LOG` and `bedrock_module.__file__` are all
    directories or constants — they redirect a test at a temporary tree, which is what
    makes it a test rather than a run against the real repository. None of them is a
    `def`, so this check never sees them, and no allowlist entry is needed to say so.
    `tests/test_seal.py:152` states the same rule in prose: "the substitution is
    deliberately placed at the *data* and never at the frame."
    """
    patched = checker.patch_sites()
    assert {"ROOT", "LOG", "__file__"} <= set(patched)
    names = {name for _file, name in checker.candidates()}
    assert not ({"ROOT", "LOG", "__file__"} & names)


def test_the_known_guarantees_are_all_candidates(checker):
    """The four functions the audit found, named. If a refactor moved one out of the
    candidate set, this check would go on passing and cover less."""
    found = {f"{file}::{fn}" for file, fn in checker.candidates()}
    for expected in (
        "src/eval/sealed_log.py::tree_state",
        "src/eval/run_sealed_eval.py::_verify_frozen_split",
        "tools/check_bedrock_logging.py::check_region",
        "tools/check_bedrock_logging.py::check_all",
    ):
        assert expected in found, f"{expected} is no longer being checked"


# ─── 2. the allowlist cannot be widened quietly ──────────────────────────────


def entry(**kw) -> dict:
    base = {"file": "src/x.py", "function": "f",
            "why": "a reason with plainly more than eight words in it"}
    base.update(kw)
    return base


def written(tmp_path: Path, *entries) -> Path:
    path = tmp_path / "patch_allowlist.json"
    path.write_text(json.dumps({"version": 1, "entries": list(entries)}), encoding="utf-8")
    return path


def test_the_committed_allowlist_is_valid(checker):
    assert isinstance(checker.load_allowlist(), dict)


def test_an_entry_must_name_a_file_as_well_as_a_function(checker, tmp_path):
    """A bare function name would exempt every first-party definition of it, including
    ones added after the entry — the `data/acquire/*.sh` lesson, one file over."""
    path = written(tmp_path, entry(file=""))
    with pytest.raises(checker.CheckError, match="both"):
        checker.load_allowlist(path)


def test_an_entry_must_carry_a_reason(checker, tmp_path):
    path = written(tmp_path, entry(why="flaky"))
    with pytest.raises(checker.CheckError, match="words"):
        checker.load_allowlist(path)


def test_an_entry_with_no_reason_at_all_is_refused(checker, tmp_path):
    path = written(tmp_path, {"file": "src/x.py", "function": "f"})
    with pytest.raises(checker.CheckError, match="words"):
        checker.load_allowlist(path)


def test_a_duplicate_entry_is_refused(checker, tmp_path):
    """Two entries for one function are two reasons, and the second silently wins."""
    path = written(tmp_path, entry(),
                   entry(why="a different reason, also comfortably longer than the minimum"))
    with pytest.raises(checker.CheckError, match="twice"):
        checker.load_allowlist(path)


def test_a_missing_allowlist_is_refused_rather_than_treated_as_empty(checker, tmp_path):
    """An absent list and an empty list are the same verdict, and only one of them is a
    decision somebody made."""
    with pytest.raises(checker.CheckError, match="missing"):
        checker.load_allowlist(tmp_path / "gone.json")


def test_a_malformed_allowlist_is_refused(checker, tmp_path):
    path = tmp_path / "patch_allowlist.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(checker.CheckError, match="valid JSON"):
        checker.load_allowlist(path)


def test_a_broken_allowlist_exits_two_and_not_one(checker, monkeypatch):
    """Two is "the check could not run"; one is "the check found something". Collapsing
    them would let a deliberately broken allowlist read as a clean result."""
    monkeypatch.setattr(checker, "ALLOWLIST", Path("/nonexistent/patch_allowlist.json"))
    assert checker.main([]) == 2


def test_a_stale_entry_fails_rather_than_being_ignored(checker, tmp_path, monkeypatch,
                                                      capsys):
    """An exemption for a function that is no longer patched would sit there and cover
    whatever takes the name next."""
    monkeypatch.setattr(checker, "ALLOWLIST",
                        written(tmp_path, entry(file="src/gone.py", function="vanished")))
    assert checker.main([]) == 2
    assert "not a candidate" in capsys.readouterr().err


# ─── 3. the check cannot be switched off from inside the suite ───────────────


#: Calls that modify a file, plus the two that set an interpreter-wide hook. Named as
#: calls and matched on the syntax tree rather than as substrings, for a reason the first
#: draft of this file demonstrated: a substring ban on "setprofile" fails on the test that
#: forbids it, and a substring ban on "check_patched_guarantees" fails on
#: `tests/test_seal_internals.py`, which names the tool in a docstring to explain why a
#: test exists. Mentioning a control is how it stays understood; calling it is the risk.
MUTATING_CALLS = {"write_text", "write_bytes", "unlink", "rename", "replace", "chmod",
                  "rmtree", "remove", "truncate"}
HOOK_CALLS = {"setprofile", "settrace"}

#: Text that identifies the check's own machinery in a path expression.
OWN_MACHINERY = ("patch_allowlist", "check_patched_guarantees", "ALLOWLIST", "CHECKER")


def calls_in(module: ast.Module):
    """Every call in a module, as (name, unparsed receiver)."""
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                yield fn.attr, ast.unparse(fn.value)
            elif isinstance(fn, ast.Name):
                yield fn.id, ""


def test_no_test_touches_the_profiler():
    """The evidence is collected by `sys.setprofile` in a subprocess. A test that set the
    profiler — even innocently, for its own timing — could blank the record that judges
    everything after it. Checking the place a control could be defeated is what was
    missing every time this project lost one: the call site was tested and the callee
    was not.
    """
    for path in suite_files():
        for name, _receiver in calls_in(tree(path)):
            assert name not in HOOK_CALLS, (
                f"{path.name} calls {name}. The record that "
                "tools/check_patched_guarantees.py judges by is built from the profiler; a "
                "test that replaces it can blank that record for every test after it."
            )


def test_no_test_writes_to_the_checker_or_its_allowlist():
    """A test that edits either one is a test that grants itself an exemption.

    No file is exempt, including this one — which is why the rule is about *writes to
    those paths* rather than about mentioning them. This file writes a dozen allowlists
    and every one of them is under `tmp_path`, so it passes the same check as everybody
    else. An exemption here would be the one place nobody would look.
    """
    for path in suite_files():
        for name, receiver in calls_in(tree(path)):
            if name not in MUTATING_CALLS and name != "open":
                continue
            assert not any(token in receiver for token in OWN_MACHINERY), (
                f"{path.name} calls {name} on {receiver}, which is the checker or its "
                "allowlist. A test that can edit its own exemption list is not held by it."
            )


def test_no_test_replaces_the_checkers_own_functions():
    """`load_allowlist` returning `{}` and `satisfies` returning `True` are both one
    `setattr` away, and either would make the check unconditionally clean. This file
    imports the tool by path, so a patch of it would be visible here and nowhere else."""
    checker_names = {"load_allowlist", "satisfies", "candidates", "patch_sites",
                     "first_party_defs", "run_suite"}
    for path in suite_files():
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "setattr" \
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value not in checker_names, (
                    f"{path.name} patches {node.args[1].value!r}, which belongs to "
                    "tools/check_patched_guarantees.py. The check would then be judging a "
                    "substitute for itself — the exact failure form it exists to report."
                )


def test_the_checker_has_no_option_that_narrows_what_it_judges():
    """`-k`, a file list, a node id: each would let the check pass by looking at less.

    "Never executed" is a claim about the whole suite. A run over part of it produces
    findings in the direction of a false alarm and, once someone silences those the easy
    way, a control whose scope is whatever the operator typed. That is the fourth-of-the-
    family failure in `tests/mutations/README.md`, and the tool is shaped so the option
    does not exist to be passed.
    """
    source = CHECKER.read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(source))
             if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument"]
    flags = {a.value for c in calls for a in c.args if isinstance(a, ast.Constant)}
    assert flags == {"--list"}, (
        f"the checker gained the options {sorted(flags - {'--list'})}. --list prints "
        "candidates and judges nothing; anything that narrows the run narrows the verdict."
    )


def test_the_suite_is_run_whole():
    """The subprocess is handed the tests directory and nothing else."""
    source = CHECKER.read_text(encoding="utf-8")
    assert 'str(TESTS), "-q"' in source, (
        "the checker no longer runs the whole tests/ directory in one pass"
    )
    assert '"-k"' not in source


def test_the_recorder_writes_its_evidence_even_if_the_suite_dies(checker):
    """`atexit`, not `pytest_unconfigure`. A plugin that reports from a pytest hook reports
    nothing when the run dies before that hook — and the absence of evidence must not be
    reachable by crashing the suite. The caller treats a missing file as an error, not as
    "nothing executed", and both halves are needed."""
    assert "atexit.register" in checker.PLUGIN
    assert "produced no execution record" in CHECKER.read_text(encoding="utf-8")


def test_a_failing_suite_is_not_a_clean_verdict(checker):
    """A function is "never executed" when the test that would have run it fails first.
    The unreadable-state rule, one more time: an unusable measurement is not a good one."""
    source = CHECKER.read_text(encoding="utf-8")
    assert "did not pass" in source and "status != 0" in source


# ─── 4. the check is capable of failing ──────────────────────────────────────


def test_a_patched_and_never_run_function_is_reported(checker, tmp_path):
    """The load-bearing test of this file, on the shape the audit actually found.

    Everything above asserts that the check is well formed, and all of it is consistent
    with a check that reports nothing at all — which is the defect it exists to prevent,
    one level up. So the failing case is constructed: a first-party module with a
    `verify()` nobody calls, a test that patches it out, and an execution record in which
    it does not appear.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "guard.py").write_text(
        "def verify(x):\n    if not x:\n        raise ValueError('refused')\n",
        encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_thing.py").write_text(
        "def test_it(monkeypatch):\n"
        "    monkeypatch.setattr(guard, 'verify', lambda x: None)\n",
        encoding="utf-8")

    patched = checker.patch_sites(tests)
    assert patched == {"verify": {"test_thing.py"}}

    candidate = ("src/guard.py", "verify")
    assert not checker.satisfies(candidate, set()), "an empty record satisfied a candidate"
    assert checker.satisfies(candidate, {("/anywhere/src/guard.py", "verify")})


def test_execution_of_a_different_function_does_not_satisfy_a_candidate(checker):
    """The obvious way to weaken this into a subset test: credit the file rather than the
    function. Then one executed function in a module vouches for every patched one in it."""
    assert not checker.satisfies(
        ("src/eval/sealed_log.py", "tree_state"),
        {("/x/src/eval/sealed_log.py", "record_access")})


def test_execution_of_a_same_named_function_elsewhere_does_not_satisfy(checker):
    """And the other subset weakening: credit the name rather than the (file, name) pair.

    `axis` is defined in `src/corpora/base.py` and patched through two different module
    aliases; if a same-named function in an unrelated file could satisfy it, the check
    would report on names rather than on functions.
    """
    assert not checker.satisfies(
        ("src/corpora/base.py", "axis"), {("/x/src/other.py", "axis")})


def test_a_copy_of_a_module_counts_as_the_module(checker):
    """Deliberate, and it is the reason `satisfies` compares basenames.

    `tests/test_check_bedrock_logging.py` copies the tool into a temporary tree and
    imports the copy, because the tool resolves `ROOT` from its own `__file__` and a copy
    is the only way to give it a `compliance.md` to write to. `check_region` therefore
    executes under `/tmp`. The first version of this check required the repository path
    and reported the one guarantee it was written to protect as unprotected. The cost of
    the looser rule is recorded in `satisfies`'s docstring.
    """
    assert checker.satisfies(
        ("tools/check_bedrock_logging.py", "check_region"),
        {("/tmp/pytest-x/tools/check_bedrock_logging.py", "check_region")})


def test_the_committed_checker_reports_no_candidate_without_an_entry(checker):
    """The steady state, asserted so that reaching it again is what closing a finding
    means. This does not run the suite — `tools/check_patched_guarantees.py` does that,
    and it is the authority. What is asserted here is that the allowlist is empty, i.e.
    that no finding has ever been closed by exempting it.
    """
    assert checker.load_allowlist() == {}, (
        "the allowlist is no longer empty. That may be right — but the intended way to "
        "close a finding is to exercise the function, not to explain why nobody does."
    )
