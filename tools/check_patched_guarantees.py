#!/usr/bin/env python3
"""Fail when a first-party function is only ever patched away and never actually runs.

The failure form this exists for, in the shape it arrived in:

    monkeypatch.setattr(sealed_log, "tree_state", lambda: ("abc123", "dirty"))
    with pytest.raises(SealError):
        run_sealed_eval.load_sealed(...)

That test passes, and it proves the refusal fires when something tells it the tree is
dirty. It cannot notice that nothing ever tells it so. `tree_state` — the function that
decides — had no coverage at all, and the suite reported the same green it reports now.
The same thing happened three more times: `_verify_frozen_split` was patched out in both
tests that mentioned it and executed by none; `check_all` was patched in all twenty tests
of `tools/check_bedrock_logging.py`, so the branch turning an unreadable IAM response into
a refusal never ran. That last one was found by a mutation surviving
(`tests/mutations/README.md`, "The Bedrock mutations, and the one that survived") — not by
any check. This is the check.

    python3 tools/check_patched_guarantees.py           # run the suite, then judge
    python3 tools/check_patched_guarantees.py --list    # candidates, no suite run

Exit 0 when every patched first-party function was also executed for real somewhere in the
suite, or is allowlisted with a reason. Exit 1 on a finding, 2 on a broken allowlist.

## Why the evidence is collected at runtime

A static version of this check is easy to write and gets the wrong answer. Asking "does
the name appear as a call anywhere in tests/" reports `_require_logging_check` as
uncovered, because the five tests that exercise it do so through a module-level alias
(`REAL_GATE = bedrock_module._require_logging_check`) and then call `invoke()`, which calls
it indirectly. The name never appears as a call. It runs on every one of those tests.

That is not a corner case, it is the normal way a gate gets tested: through the thing it
guards. Any static approximation has to either miss those or grow a list of blessed
idioms, and a check with a list of blessed idioms is a check that stops applying to code
written next month. Executing the suite and recording which code objects the interpreter
actually entered has no such gap — it answers the question the check is asking rather than
a proxy for it.

## Why this is a tool and not a pytest test

Runtime evidence needs the whole suite to have run, and a test inside the suite cannot see
past the tests that ran before it. The available shapes were:

  1. A `conftest.py` hook that profiles the session and judges in `pytest_sessionfinish`.
     Correct when the whole suite runs, and **vacuous under `-k` or a single file** — the
     unexecuted set would be enormous and the hook would have to disable itself. A control
     whose precondition is whatever the operator typed is the failure `tests/mutations/`
     records as "the fourth of the family": it reports success by not looking.
  2. This: a tool that runs the suite itself, in a subprocess, over the whole `tests/`
     directory, with no way to ask it for a subset. There is no argument that narrows what
     it judges, so there is no argument that weakens it.

The second also puts the evidence out of process. Nothing a test does to `sys.setprofile`,
to this module, or to its allowlist reader can affect a verdict formed in a different
interpreter. `tests/test_structure.py` holds the parts that do not need a suite run — the
allowlist's shape and the prohibition on patching the profiler — and is collected by
pytest for the ordinary reason: those parts are cheap and should fail on every run.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

#: Where first-party code lives. A patched name from outside these trees is not this
#: check's business: `subprocess.run`, `boto3.client` and `Path.write_text` are all patched
#: in the suite and all of them are somebody else's guarantee. Faking the boundary to the
#: outside world is how a test gets to run at all.
FIRST_PARTY = ("src", "tools")

ALLOWLIST = ROOT / "tools" / "patch_allowlist.json"

#: The smallest number of words that can state a reason a reader can evaluate later. Taken
#: from `tools/release_screen.py`'s allowlist rule, which had the same problem: an entry
#: nobody can assess is an entry that gets renewed forever.
MIN_WHY_WORDS = 8


class CheckError(Exception):
    """A broken allowlist, or a suite that could not be run. Not a finding."""


# ─── what is patched, and what of it is ours ─────────────────────────────────


def patch_sites(tests: Path = TESTS) -> dict[str, set[str]]:
    """Attribute name -> test files that replace it, over the whole suite.

    Read from the syntax tree rather than by importing: a test file that has to be
    imported to be surveyed is a test file whose import errors become this tool's errors,
    and the survey has to work on a machine where the corpus is absent.

    Both spellings are collected. `monkeypatch.setattr(mod, "name", ...)` is what the suite
    uses; `unittest.mock.patch("mod.name")` is what a new file might arrive with, and a
    check that only knows the current idiom silently stops covering the next one.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(tests.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "setattr" and len(node.args) >= 2:
                attr = node.args[1]
                if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                    found.setdefault(attr.value, set()).add(path.name)
            elif name in ("patch", "patch_object"):
                for arg in node.args[:1]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.setdefault(arg.value.rsplit(".", 1)[-1], set()).add(path.name)
    return found


def first_party_defs() -> dict[str, set[str]]:
    """Top-level function name -> the first-party files defining it, repo-relative.

    Top-level only, and deliberately. A method is reached through its instance and a
    subclass may legitimately replace one — `tests/test_seal_internals.py` defines a
    `TinyLoader` whose `_read` is its own, which is not a patch of anything. Module-level
    functions are the ones a `setattr` on a module can reach, which is the failure form
    here.

    Keyed by name and *carrying the files*, so a name defined in two first-party modules
    produces two candidates rather than one that either module can satisfy. Executing
    `src/a.py`'s `check()` is not evidence about `src/b.py`'s.
    """
    defs: dict[str, set[str]] = {}
    for tree_root in FIRST_PARTY:
        for path in sorted((ROOT / tree_root).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            try:
                module = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError as exc:                      # pragma: no cover - a broken tree
                raise CheckError(f"{rel} does not parse: {exc}")
            for node in module.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.setdefault(node.name, set()).add(rel)
    return defs


def candidates() -> dict[tuple[str, str], set[str]]:
    """(file, function) -> test files that patch it. Every one needs runtime evidence."""
    patched = patch_sites()
    defs = first_party_defs()
    out: dict[tuple[str, str], set[str]] = {}
    for name, files in patched.items():
        for definition in defs.get(name, ()):
            out[(definition, name)] = files
    return out


# ─── the allowlist ───────────────────────────────────────────────────────────


def load_allowlist(path: Path | None = None) -> dict[tuple[str, str], dict]:
    """Read and validate the allowlist. Returns {(file, function): entry}.

    Shaped after `tools/screen_allowlist.json` because the same abuses apply, and because
    an exemption list is the part of a check most likely to be widened under deadline:

      - **Both the file and the function are named.** An entry keyed on a bare function
        name would exempt every first-party definition of that name, including ones added
        later. This is the `data/acquire/*.sh` lesson: an entry keyed on anything broader
        than the thing itself stops naming what it permits.
      - **A reason a reader can evaluate.** Enforced by word count, which is crude and
        still catches the entry that says "flaky" or "N/A". An unevaluable entry is
        permanent by default.
      - **No stale entries.** An entry naming a function that is not a candidate — never
        patched, or no longer defined — fails rather than being ignored. A list that
        outlives what it describes is how the exemption for one function silently becomes
        an exemption for its replacement.
    """
    path = path or ALLOWLIST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CheckError(
            f"{path.name} is missing. It is committed; restore it rather than running "
            "without it, because an absent allowlist and an empty one are the same "
            "verdict here and only one of them is a decision anyone made.")
    except json.JSONDecodeError as exc:
        raise CheckError(f"{path.name} is not valid JSON: {exc}")

    entries: dict[tuple[str, str], dict] = {}
    for i, entry in enumerate(data.get("entries", [])):
        where = f"{path.name} entry {i}"
        file, function = entry.get("file", ""), entry.get("function", "")
        if not file or not function:
            raise CheckError(
                f"{where} must name both `file` and `function`. A bare function name "
                "would exempt every first-party definition of it, including later ones.")
        if len(entry.get("why", "").split()) < MIN_WHY_WORDS:
            raise CheckError(
                f"{where}: {file}::{function} needs a `why` of at least {MIN_WHY_WORDS} "
                "words that a reader can evaluate later. An entry nobody can assess is "
                "an entry that gets renewed forever.")
        key = (file, function)
        if key in entries:
            raise CheckError(f"{where}: {file}::{function} is listed twice")
        entries[key] = entry
    return entries


# ─── running the suite and recording what executed ───────────────────────────

#: The plugin, written to a temporary file and loaded with `-p`. In-process would be
#: simpler and would put the evidence in the same interpreter as the tests that are being
#: judged by it.
PLUGIN = '''\
import atexit, json, os, sys

WANTED = json.loads(os.environ["PATCH_CHECK_WANTED"])
TARGET = os.environ["PATCH_CHECK_OUT"]
NAMES = {name for _file, name in WANTED}
seen = set()


def _record(frame, event, arg):
    if event != "call":
        return
    code = frame.f_code
    if code.co_name not in NAMES:
        return
    # The full path, unfiltered. Deciding here which files count would discard the
    # evidence from tests that load a *copy* of a module — see `satisfies`.
    seen.add((code.co_filename, code.co_name))


def pytest_configure(config):
    import threading
    sys.setprofile(_record)
    threading.setprofile(_record)


def pytest_unconfigure(config):
    sys.setprofile(None)


@atexit.register
def _write():
    # atexit rather than pytest_unconfigure: a plugin that writes its evidence from a
    # pytest hook writes nothing when the run dies before that hook, and "no evidence"
    # must not be reachable by crashing the suite. The caller checks the file exists.
    with open(TARGET, "w", encoding="utf-8") as fh:
        json.dump(sorted(seen), fh)
'''


def run_suite(wanted: list[tuple[str, str]], *, tmp: Path) -> tuple[set[tuple[str, str]], int]:
    """Run the whole suite under the recorder. Returns (executed, pytest exit status).

    No parameter narrows the run. `-k`, a file list and a node id are all absent on
    purpose: the question is whether a function is executed *anywhere in the suite*, and a
    subset can only ever answer it in the direction of a false finding. An option to
    restrict the run would be an option to make this check say nothing.
    """
    plugin_dir = tmp / "plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "patch_recorder.py").write_text(PLUGIN, encoding="utf-8")
    out = tmp / "executed.json"

    env = dict(**_env(), PATCH_CHECK_WANTED=json.dumps(sorted(wanted)),
               PATCH_CHECK_OUT=str(out), PYTHONPATH=f"{plugin_dir}:{ROOT}")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS), "-q", "-p", "patch_recorder"],
        cwd=ROOT, env=env, capture_output=True, text=True)

    if not out.exists():
        raise CheckError(
            "the suite produced no execution record. Its exit status was "
            f"{proc.returncode}; the last lines were:\n{proc.stdout[-2000:]}")
    executed = {(f, n) for f, n in json.loads(out.read_text(encoding="utf-8"))}
    return executed, proc.returncode


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def satisfies(candidate: tuple[str, str], executed: set[tuple[str, str]]) -> bool:
    """Whether `(file, function)` was really executed, by basename and function name.

    Matching on the basename rather than the full path, because the strictest version of
    this got the wrong answer on its first run. `tests/test_check_bedrock_logging.py`
    copies `tools/check_bedrock_logging.py` into a temporary tree and imports the copy —
    which it does for a good reason, since the tool resolves `ROOT` from its own
    `__file__` and a copy is the only way to give it a `compliance.md` to write to. So
    `check_region` genuinely executes, in four tests written specifically to execute it,
    at a path under `/tmp`. Requiring the repository path would have reported the one
    guarantee this whole check exists to protect as unprotected.

    What that costs: a first-party function is credited when a same-named function in a
    same-named file ran anywhere, including a copy a test wrote itself. A test could
    therefore satisfy the check by exercising a stub it authored in a file it named to
    match. That is a real hole and it is the lesser one — it takes deliberate effort and
    leaves a file in the repository that says what it is, whereas the alternative fails
    on the honest idiom the suite already uses. Directory structure is not compared
    because the copies do not preserve it.
    """
    file, function = candidate
    base = os.path.basename(file)
    return any(os.path.basename(ran_in) == base and ran == function
               for ran_in, ran in executed)


# ─── reporting ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="print the candidates and exit without running the suite")
    args = ap.parse_args(argv)

    try:
        allowed = load_allowlist()
        found = candidates()
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    stale = sorted(set(allowed) - set(found))
    if stale:
        for file, function in stale:
            print(f"error: {ALLOWLIST.name} exempts {file}::{function}, which is not a "
                  "candidate — it is not patched in any test, or no longer defined there. "
                  "Remove the entry rather than leaving it to cover a later function of "
                  "the same name.", file=sys.stderr)
        return 2

    if args.list:
        print(f"{len(found)} patched first-party functions:")
        for (file, function), files in sorted(found.items()):
            mark = "allowed" if (file, function) in allowed else "       "
            print(f"  {mark}  {file}::{function}   patched in {', '.join(sorted(files))}")
        return 0

    if not found:
        print("no first-party function is patched anywhere in the suite.")
        return 0

    import tempfile

    with tempfile.TemporaryDirectory(prefix="patchcheck-") as tmp:
        executed, status = run_suite(sorted(found), tmp=Path(tmp))

    if status != 0:
        print(f"error: the suite did not pass (pytest exit {status}). Its verdict has to "
              "be sound before this one means anything: a function can be 'never "
              "executed' because the test that would have run it failed first.",
              file=sys.stderr)
        return 2

    never = sorted(k for k in found if not satisfies(k, executed))
    unexplained = [k for k in never if k not in allowed]

    print(f"{len(found)} patched first-party functions, "
          f"{len(found) - len(never)} executed for real, "
          f"{len(never) - len(unexplained)} allowlisted.")

    for file, function in never:
        if (file, function) in allowed:
            print(f"  allowed  {file}::{function}  — {allowed[(file, function)]['why']}")

    if not unexplained:
        return 0

    print()
    for file, function in unexplained:
        print(f"PATCHED AND NEVER RUN   {file}::{function}")
        print(f"   replaced in: {', '.join(sorted(found[(file, function)]))}")
        print("   Every test that mentions this function substitutes something else for "
              "it, so nothing in the suite executes it. If it holds a guarantee, that "
              "guarantee is untested and the passing test count does not say so.")
        print(f"   Either exercise it for real, or add it to {ALLOWLIST.name} with a "
              "reason someone can evaluate later.")
    print(f"\n{len(unexplained)} unexplained. See tests/mutations/README.md, "
          '"Unreadable state, twice".')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
