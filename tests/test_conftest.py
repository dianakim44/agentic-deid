"""The shared fixtures, and the prohibition that keeps them the only ones.

`tests/conftest.py` exists because one defect shipped four times. The defect was always
the same shape — availability decided by trying to *load* the corpus and skipping on any
exception — and it always arrived the same way: a new test file copied its fixture from
the nearest similar file. Three of the four occurrences were after the incident was
written up in `tests/mutations/README.md`, so a written warning is demonstrably not a
control. This file is the control.

Everything here is checked against the *syntax tree* rather than against behaviour, for
the reason the defect is hard to see at all: the defective form and the correct form
behave identically on a machine where the loader works, which is every machine where
anyone would notice. A behavioural test would pass on both. What separates them is
structure — which calls sit inside which `except` — so structure is what is asserted.

Four rules, and each one is a way the defect actually got in:

  1. **Only conftest may `pytest.skip` from inside a fixture.** A skip is a suite-wide
     decision; a file that makes it privately is a file whose tests can disappear without
     anything saying so.
  2. **Only conftest may name `corpus_root` or `sealed_root` outside a test body.** This
     is what forbids the module-level variant: `tests/test_show_human_window.py` computed
     availability in a plain function at import time and fed it to `skipif`, which rule 1
     alone would have missed because no fixture was involved.
  3. **No test file may define a fixture that shadows one of conftest's.** The direct
     prohibition: a local `corpus_present` silently wins over the shared one, and the
     suite would go on passing.
  4. **conftest's own fixtures keep availability and construction apart.** The
     availability fixtures may call nothing but the path resolvers; the construction
     fixtures may contain no `try` and no skip at all.

The mutation `conftest_availability_from_a_load` reverts rule 4 and is caught here.

    python3 -m pytest tests/test_conftest.py -q
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
CONFTEST = TESTS / "conftest.py"

#: conftest's fixtures, split by what they are allowed to do. Written out rather than
#: derived, so that adding a fixture there forces a decision here: an unclassified
#: fixture fails `test_every_shared_fixture_is_classified` instead of quietly inheriting
#: whichever set of permissions is laxer.
AVAILABILITY = {"corpus_present", "sealed_corpus"}
CONSTRUCTION = {"loader", "unsplit_loader"}

#: Calls an availability fixture may make. Both answer from a configured path and can
#: fail for one reason only — the corpus is not on this machine. `load`, `.load()` and a
#: loader constructor are all absent on purpose: each can fail for a dozen reasons that
#: have nothing to do with availability, and a skip that can mean a dozen things means
#: nothing.
PATH_RESOLVERS = {"corpus_root", "sealed_root"}

#: Names that decide availability. Rule 2 is about where these may appear.
AVAILABILITY_CALLS = PATH_RESOLVERS


def suite_files() -> list[Path]:
    return sorted(TESTS.glob("test_*.py"))


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def is_fixture(node: ast.FunctionDef) -> bool:
    """Whether a def is a pytest fixture, by any of the ways one is spelled."""
    for dec in node.decorator_list:
        call = dec.func if isinstance(dec, ast.Call) else dec
        name = call.attr if isinstance(call, ast.Attribute) else getattr(call, "id", "")
        if name == "fixture":
            return True
    return False


def fixtures(module: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in module.body
            if isinstance(n, ast.FunctionDef) and is_fixture(n)}


def calls_named(node: ast.AST) -> set[str]:
    """Every function name called anywhere under `node`, attribute or bare."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Attribute):
                found.add(fn.attr)
            elif isinstance(fn, ast.Name):
                found.add(fn.id)
    return found


def body_calls(fn: ast.FunctionDef) -> set[str]:
    """Calls in a function's body, decorators excluded.

    `@pytest.fixture(scope="session")` is a call too, and counting it would make every
    fixture look like it calls `fixture` — which is the sort of noise that gets a
    structural check loosened until it matches nothing.
    """
    found: set[str] = set()
    for stmt in fn.body:
        found |= calls_named(stmt)
    return found


def skips(fn: ast.FunctionDef) -> bool:
    return "skip" in body_calls(fn)


# ─── rule 1: a skip is not a file's private decision ─────────────────────────


def test_no_test_file_skips_from_inside_a_fixture():
    """Every corpus skip in the suite goes through conftest, or there is no single answer.

    A fixture that skips is a fixture that can delete tests, and the four occurrences of
    the original defect all did it from a fixture. Test *bodies* may still skip — two do,
    for a missing example rule file and a missing fetch script — because a skip in a body
    removes one named test rather than a fixture's whole dependent set.
    """
    for path in suite_files():
        for name, fn in fixtures(tree(path)).items():
            assert not skips(fn), (
                f"{path.name}::{name} calls pytest.skip inside a fixture. Availability "
                "belongs to tests/conftest.py; a local skip is how the same defect got "
                "into four files."
            )


# ─── rule 2: availability is not decided outside conftest, anywhere ──────────


def test_no_test_file_resolves_a_corpus_path_outside_a_test_body():
    """`corpus_root` in a fixture or at module level is an availability decision.

    Inside a test body it is the thing under test — `test_seal.py` asserts on what
    `corpus_root` returns, which is exactly what that file is for. Outside one it is a
    private answer to a suite-wide question, and it is the form
    `test_show_human_window.py` used: a module-level function feeding `skipif`, computed
    at import time, invisible to every other file. Rule 1 could not see that one, which
    is why there are two rules and not one.
    """
    for path in suite_files():
        module = tree(path)
        fixture_nodes = set(fixtures(module).values())

        for node in module.body:
            if node in fixture_nodes:
                where = f"the fixture {node.name!r}"          # type: ignore[attr-defined]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.startswith("test_"):
                continue                                      # a test body: allowed
            elif isinstance(node, ast.ClassDef):
                continue                                      # synthetic loaders
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                where = f"the helper {node.name!r}"
            else:
                where = "module level"
            found = calls_named(node) & AVAILABILITY_CALLS
            assert not found, (
                f"{path.name}: {where} calls {sorted(found)}. Corpus availability is "
                "decided once, in tests/conftest.py — request `corpus_present` instead."
            )


# ─── rule 3: the shared fixtures are not shadowed ────────────────────────────


def test_no_test_file_defines_a_fixture_conftest_already_defines():
    """A local definition wins over conftest's silently, which is the whole failure mode."""
    shared = set(fixtures(tree(CONFTEST)))
    assert shared == AVAILABILITY | CONSTRUCTION
    for path in suite_files():
        clash = set(fixtures(tree(path))) & shared
        assert not clash, (
            f"{path.name} redefines {sorted(clash)}, which tests/conftest.py already "
            "provides. A local fixture of the same name shadows the shared one and "
            "nothing reports it."
        )


def test_every_shared_fixture_is_used_by_something():
    """A fixture nobody requests is a conversion that was reverted and not noticed."""
    requesters: dict[str, list[str]] = {n: [] for n in AVAILABILITY | CONSTRUCTION}
    for path in [*suite_files(), CONFTEST]:
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.FunctionDef):
                for arg in node.args.args:
                    if arg.arg in requesters and node.name != arg.arg:
                        requesters[arg.arg].append(f"{path.name}::{node.name}")
    unused = sorted(n for n, users in requesters.items() if not users)
    assert not unused, f"nothing requests {unused}"


# ─── rule 4: conftest keeps availability and construction apart ──────────────


def test_every_shared_fixture_is_classified():
    """A new fixture in conftest must be declared here as one kind or the other."""
    assert set(fixtures(tree(CONFTEST))) == AVAILABILITY | CONSTRUCTION, (
        "tests/conftest.py gained or lost a fixture. Add it to AVAILABILITY or to "
        "CONSTRUCTION — an unclassified fixture is checked by neither rule below."
    )


def test_availability_fixtures_resolve_a_path_and_do_not_load():
    """The defect, stated as a structure: nothing but a path resolver may be called.

    This is the assertion the four occurrences would each have failed. `load`,
    `MeddocanLoader`, `.load()` — anything that reads the corpus — can raise for reasons
    that are not "the corpus is absent", and once one of those sits inside the `except`
    the skip stops meaning one thing.
    """
    conf = fixtures(tree(CONFTEST))
    for name in sorted(AVAILABILITY):
        called = body_calls(conf[name])
        # Its own upstream availability fixture is fine; `skip` is the point of it.
        allowed = PATH_RESOLVERS | {"skip"} | AVAILABILITY
        stray = called - allowed
        assert not stray, (
            f"conftest::{name} calls {sorted(stray)}. An availability fixture may call "
            f"only {sorted(PATH_RESOLVERS)}: those fail for one reason, and that is what "
            "makes a skip readable. Anything that loads belongs in a construction "
            "fixture, outside the except."
        )


def test_construction_fixtures_have_no_except_and_no_skip():
    """Availability was already settled upstream, so a failure here is a real bug.

    `loader` in `tests/test_meddocan_loader.py` was `MeddocanLoader()` inside
    `except CorpusError: pytest.skip(...)`. A type both mapped and excluded raises from
    `_check_type_map` at construction; the except reported it as an absent corpus, 27
    tests skipped, and the suite was green. With no handler here that same bug is 27
    errors.
    """
    conf = fixtures(tree(CONFTEST))
    for name in sorted(CONSTRUCTION):
        fn = conf[name]
        handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
        assert not handlers, (
            f"conftest::{name} handles an exception. Construction must not: a failure "
            "here is a loader bug and has to reach the test as one."
        )
        assert not skips(fn), f"conftest::{name} can skip; only availability may."
        assert any(a.arg in AVAILABILITY for a in fn.args.args), (
            f"conftest::{name} does not request an availability fixture, so it would "
            "construct a loader on a machine with no corpus and fail as if broken."
        )


def test_the_availability_fixture_is_reached_before_any_construction():
    """Ordering, as a property of the fixture graph rather than of a comment.

    pytest resolves arguments before the body runs, so a construction fixture that takes
    `corpus_present` cannot reach a loader on a machine without the corpus. Asserted
    because the alternative — a `corpus_root()` call at the top of each construction
    fixture — is one edit away and would put the two questions back in one place.
    """
    conf = fixtures(tree(CONFTEST))
    for name in sorted(CONSTRUCTION):
        assert PATH_RESOLVERS.isdisjoint(body_calls(conf[name])), (
            f"conftest::{name} resolves the path itself instead of depending on "
            "`corpus_present`, which is the split this file exists to hold."
        )


def test_the_defective_form_would_be_caught():
    """The rule 4 checks must be capable of failing, on the exact reverted text.

    Without this, the assertions above are consistent with `calls_named` returning
    nothing at all — and a structural check that silently matches nothing is the same
    class of defect as the one being prevented. The defective form is parsed here as a
    string; nothing on disk changes.
    """
    reverted = ast.parse(
        "import pytest\n"
        "@pytest.fixture\n"
        "def corpus_present():\n"
        "    try:\n"
        "        load(CORPUS)\n"
        "    except Exception as exc:\n"
        "        pytest.skip(str(exc))\n"
    )
    fn = fixtures(reverted)["corpus_present"]
    stray = body_calls(fn) - (PATH_RESOLVERS | {"skip"} | AVAILABILITY)
    assert stray == {"load", "str"}, (
        "the check that rejects a load inside an availability fixture did not see one"
    )


def test_conftest_is_not_in_the_forbidden_set_by_accident():
    """The rules apply to `test_*.py`; conftest is exempt because it is the one place.

    Named so the exemption is visible: a glob that happened to include conftest would
    make every rule above unsatisfiable, and someone would loosen the rules rather than
    the glob.
    """
    assert CONFTEST not in suite_files()
    assert not CONFTEST.name.startswith("test_")
