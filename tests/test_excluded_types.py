"""The `excluded_types` block in config/naming.yaml (DESIGN §9.1).

This block exists for one sentence in one prompt. `docs/prompts/auditor.md` §1.1 requires
the §9.1 exclusions **named as out of scope rather than left to be inferred**, and the
reason is that inference from an absence is ambiguous in exactly the wrong direction: an
Auditor shown ten canonical types and nothing else can read the missing sex type as "not in
scope" or as "the list is a summary", and the second reading produces a flag on `madre` that
is not wrong about the text. It is an answer to a question this project does not ask, and it
lands in §4's case 2, the category the RuleAuthor may not act on.

So the names land in the content of a prompt, and CLAUDE.md puts such values in config
rather than in a module. The two properties worth pinning are therefore not about parsing:

  1. The block is **not** the `phi_type` axis and shares no key with it. A name in the axis
     is scored; §9.1 decided these are not. Both spellings would parse.
  2. The block is **not** derived from a loader's `excluded_types`, and is not its source.
     That attribute holds one corpus's own type names and drives the `excluded` flag at
     load. A derivation would silently shorten this list for a corpus whose loader is not
     written yet, and a silently shortened list is indistinguishable from "nothing is
     excluded". The two directions stay separate now that both corpora have loaders:
     `src/corpora/grascco.py` spells `NAME_TITLE` because that is GraSCCo's own `kind`, and
     `CORPUS_OWN_SPELLINGS` below is where that coincidence is declared.

    python3 -m pytest tests/test_excluded_types.py -q
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.corpora import base
from src.corpora.base import CorpusError, axis, excluded_types

SRC = Path(__file__).resolve().parents[1] / "src"

#: The one place a corpus's own type name is spelled the same as a §9.1 concept name, as
#: `{relative path: {constant name: the values it may hold}}`. GraSCCo's `kind` for a title
#: *is* `NAME_TITLE`, byte for byte, where MEDDOCAN's is `SEXO_SUJETO_ASISTENCIA` — so the
#: exactness argument in `test_no_exclusion_name_is_spelled_in_a_module` stops separating the
#: two spellings for this corpus and the site has to be named instead of inferred.
#:
#: Written out for the reason `tests/test_conftest.py`'s fixture classification is: a second
#: corpus whose names collide has to be added here, in a commit, rather than inheriting an
#: exemption that a pattern would have handed it silently.
CORPUS_OWN_SPELLINGS = {
    "corpora/grascco.py": {"EXCLUDED_TYPES": {"NAME_TITLE"}},
}


@pytest.fixture(autouse=True)
def _clear_caches():
    """`naming()` is lru_cached and these tests monkeypatch it."""
    base.naming.cache_clear()
    yield
    base.naming.cache_clear()


def test_the_three_exclusions_are_the_declared_ones():
    """Written out rather than read from the config, which would compare it to itself.

    Three, and DESIGN §9.1 names them: sex, family relationships, titles. A fourth appearing
    here without a §9.1 edit is a type quietly removed from the measurement — the exclusions
    cost 9.90% of MEDDOCAN's gold and 9.68% of GraSCCo's, and that cost is reported as a
    limitation, so the list is not something a caller extends in passing.
    """
    assert set(excluded_types()) == {"SEXO", "FAMILIARES", "NAME_TITLE"}


def test_every_exclusion_carries_a_reason():
    """§9.1 excludes for **two different reasons** and the block does not blur them.

    Sex and relationship words are not HIPAA Safe Harbor identifiers, so a detector scored
    on them is measured on something other than disclosure risk. `NAME_TITLE` is a different
    case: the two corpora annotate it incompatibly and neither is wrong. A list without the
    reasons invites the agent to guess which one applies, and the guess it would make for
    `NAME_TITLE` is the wrong one.
    """
    for name, reason in excluded_types().items():
        assert isinstance(reason, str) and reason.strip(), name


def test_no_exclusion_is_also_a_phi_type():
    """**The load-bearing property.**

    The axis is the scored vocabulary (DESIGN §9.0). A name in both places would be scored
    and simultaneously shown to the Auditor as out of scope — the agent withholds the flag
    and the scorer counts the miss, which is a recall loss with no visible cause.
    """
    assert set(excluded_types()) & set(axis("phi_type")) == set()


def test_a_name_in_both_places_is_refused(monkeypatch):
    """Checked in the accessor rather than left to review.

    This is the edit made for a good reason — "the Auditor keeps flagging titles, let us put
    `NAME_TITLE` in the axis so the flag validates" — and it reverses §9.1 without touching
    it.
    """
    real = base.naming()
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {
        **real, "excluded_types": {"NAME": "already an axis value"}})
    with pytest.raises(CorpusError) as excinfo:
        excluded_types()
    message = str(excinfo.value)
    assert "NAME" in message
    assert "phi_type" in message


def test_a_missing_block_is_refused_rather_than_defaulted(monkeypatch):
    """Absent is refused, because a default is how the list gets shorter.

    An accessor returning `{}` would let the config lose the block and the Auditor's frame
    keep rendering — with an "out of scope entirely" heading over nothing, which reads to an
    agent as though the project excludes nothing at all.
    """
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {})
    with pytest.raises(CorpusError, match="no `excluded_types` mapping"):
        excluded_types()


@pytest.mark.parametrize("block", [
    {},
    {"": "empty key"},
    {1: "not a string"},
    {"SEXO": ""},
    {"SEXO": "   "},
    {"SEXO": None},
    {"SEXO": 42},
])
def test_a_malformed_block_is_refused(monkeypatch, block):
    """Validated on read for `agent_roles()`'s reason: every key and every value here is
    text shown to an agent, and a key that is not a non-empty string is a line of the frame
    nobody can read.

    The rest of the config is left real rather than patched away, unlike
    `test_agent_role.py`'s equivalent. This accessor cross-checks the `phi_type` axis, so a
    config reduced to one key would fail on the missing axis instead of on the block under
    test, and the parametrisation would pass without ever reaching the validation it names.
    """
    real = base.naming()
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {**real, "excluded_types": block})
    with pytest.raises(CorpusError):
        excluded_types()


def test_the_accessor_returns_a_copy(monkeypatch):
    """A mutable view of the cached config would let one caller's edit reach the next
    prompt, and `naming()` is lru_cached, so the edit would outlive the call."""
    first = excluded_types()
    first["INVENTED"] = "not in the config"
    assert "INVENTED" not in excluded_types()


def _declared_here(tree: ast.Module, relative: str) -> set[int]:
    """Node ids of the constants `CORPUS_OWN_SPELLINGS` permits in this module.

    Only module-level `NAME = ...` statements are consulted, and only for the constant
    names the entry lists. A permitted value anywhere else in the same file — inside a
    function, a prompt string, a second constant — is not in the returned set and is
    reported like any other literal.
    """
    allowed_by_name = CORPUS_OWN_SPELLINGS.get(relative, {})
    permitted: set[int] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if not isinstance(target, ast.Name):
                continue
            values = allowed_by_name.get(target.id, set())
            if not values:
                continue
            for node in ast.walk(stmt.value):
                if isinstance(node, ast.Constant) and node.value in values:
                    permitted.add(id(node))
    return permitted


def literal_sites(path: Path, tree: ast.Module, names: set[str]) -> list[str]:
    """Every place `tree` holds one of `names` as a constant, exemptions applied."""
    relative = path.as_posix().split("/src/")[-1]
    permitted = _declared_here(tree, relative)
    return [f"{relative}:{node.lineno} {node.value!r}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and node.value in names
            and id(node) not in permitted]


def test_no_exclusion_name_is_spelled_in_a_module():
    """CLAUDE.md's rule, and the same shape as `test_the_tag_is_not_spelled_in_any_module`.

    The names land in a prompt, so no module holds them as literals. `src/corpora/base.py`
    and `src/corpora/meddocan.py` mention them in prose — that is documentation of the
    decision, not a value being used — so this scans for a name in a string literal or a
    collection, which is what using it looks like.

    The exception that has to stay legible: a loader's own `EXCLUDED_TYPES` holds
    corpus-specific names like `SEXO_SUJETO_ASISTENCIA`. Those are not these names, and the
    check is exact rather than substring for that reason — otherwise the corpus-specific
    constant would trip a check meant for the corpus-independent one.

    `de-grascco` is where the exactness argument runs out: the corpus's own `kind` for a
    title is spelled `NAME_TITLE`, the concept's own name. `CORPUS_OWN_SPELLINGS` names that
    one declaration site, and what makes the exemption safe is that the loader's set is not
    the list this test protects — the prompt reads `excluded_types()` from the config, and a
    name dropped from a loader's own set does not shorten it but makes 139 spans neither
    mapped nor excluded, which `classify()` refuses at load
    (`tests/test_grascco_loader.py::test_unknown_annotation_type_raises`).
    """
    names = set(excluded_types())
    offenders = [site
                 for path in sorted(SRC.rglob("*.py"))
                 for site in literal_sites(path, ast.parse(path.read_text(encoding="utf-8")),
                                           names)]
    assert offenders == [], (
        f"an §9.1 exclusion name appears as a literal at {offenders}. The names are read "
        "from config/naming.yaml via excluded_types(); a module holding one is a second "
        "place the list can be shortened (CLAUDE.md)."
    )


def test_the_exemption_covers_the_declaration_and_nothing_else():
    """The positive control the exemption needs, or it is a hole rather than a carve-out.

    Both halves are asserted against one synthetic module, parsed as a string and never
    written: the permitted constant at the named module-level assignment is silent (line 1),
    while the same name in a second module-level collection (line 2) and inside a function
    (line 4) is still reported. Without this, `CORPUS_OWN_SPELLINGS` is consistent with an
    implementation that exempts the whole file, or every `frozenset` in `src/`.
    """
    exempt = next(iter(CORPUS_OWN_SPELLINGS))
    constant, values = next(iter(CORPUS_OWN_SPELLINGS[exempt].items()))
    name = next(iter(values))
    source = (
        f"{constant} = frozenset({{{name!r}}})\n"
        f"PROMPT_TYPES = [{name!r}]\n"
        f"def describe():\n"
        f"    return {name!r}\n"
    )
    sites = literal_sites(SRC / exempt, ast.parse(source), {name})
    assert [site.split(":")[1].split(" ")[0] for site in sites] == ["2", "4"], (
        f"the exemption for {exempt}::{constant} did not stay on its own line: {sites}"
    )


def test_every_exempt_site_is_a_file_that_exists_and_still_holds_the_name():
    """An exemption for a file that moved, or for a literal that is gone, is stale.

    The failure this prevents is the quiet one: a loader renamed or rewritten leaves an
    entry that exempts a path nothing reads, and the next collision is covered by it.
    """
    for relative, by_name in CORPUS_OWN_SPELLINGS.items():
        path = SRC / relative
        assert path.is_file(), f"CORPUS_OWN_SPELLINGS names {relative}, which is not there"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for constant, values in by_name.items():
            assert _declared_here(tree, relative), (
                f"{relative} no longer declares {constant} with {sorted(values)} at module "
                "level; drop the exemption rather than leaving it to cover something else."
            )
