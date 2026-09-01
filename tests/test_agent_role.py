"""The `agent_role` vocabulary in config/naming.yaml (DESIGN §5.5).

Declared before `port-loop`'s Auditor calls anything, and tested here rather than only
where it is written, because the field's whole job is comparison across lines. RuleAuthor
and Auditor share one `agent_calls.jsonl`; `llm_calls` sums their lines, which is the right
answer for cost — it is what a round spent — and no answer at all for attribution. The
sentence "the Auditor accounts for half this arm's spend" is checkable only if each line
says whose it is.

The failure this guards against has no symptom in the file. A caller writing
`"RuleAuthor"` where another writes `"rule_author"` produces a log in which one agent's
calls are split across two spellings, every per-role total computed from it is wrong, and
the file is well-formed JSONL throughout. That is `check_agent_role`'s reason for
existing, and this file's reason for pinning the two values written out rather than read
from the config on both sides.

    python3 -m pytest tests/test_agent_role.py -q
"""
from __future__ import annotations

import pytest

from src.corpora import base
from src.corpora.base import CorpusError, agent_roles, check_agent_role


@pytest.fixture(autouse=True)
def _clear_caches():
    """`naming()` is lru_cached and these tests monkeypatch it."""
    base.naming.cache_clear()
    yield
    base.naming.cache_clear()


#: The five roles, written out. §3 counts agents by the file they produce: the RuleAuthor
#: writes `rules/{lang}.yaml`, the Auditor the audit report, and `port-multi`'s three
#: out-of-loop agents write `profile.json`, `mapping.yaml` and `lexicons/{lang}/`
#: (DESIGN §6.7.1).
ROLES = {"rule_author", "auditor", "profiler", "mapper", "lexicon_builder"}

#: The two the loop calls, and the three called before iteration 1. The split is what
#: `orchestrate.call_line()` enforces against the iteration number, so it is declared here
#: rather than only there — a log line is the only place "out of loop" is checkable, and it
#: is checkable because these two sets are disjoint.
LOOP_ROLES = {"rule_author", "auditor"}
OUT_OF_LOOP_ROLES = {"profiler", "mapper", "lexicon_builder"}


def test_the_five_roles_are_the_declared_ones():
    """Written out rather than compared to the config, which would compare it to itself.

    Three since 2026-09-01. They were absent while no arm called them, on the stated ground
    that a value nothing writes is not vocabulary; `port-multi` writes all three, and each
    has a prompt whose hash is in the window (`tests/test_window_widening.py`).
    """
    assert set(agent_roles()) == ROLES


def test_the_loop_roles_and_the_out_of_loop_roles_partition_the_vocabulary():
    """Every role is on exactly one side of iteration 0, and the two sides are disjoint.

    The partition is the fact `call_line()` enforces. A sixth role added to `naming.yaml`
    without being placed on a side would be a role the iteration rule cannot judge, and
    `call_line()` would either refuse every line that carries it or accept every one — both
    of which are decisions, and neither of which anyone made.
    """
    assert LOOP_ROLES | OUT_OF_LOOP_ROLES == ROLES
    assert not (LOOP_ROLES & OUT_OF_LOOP_ROLES)


def test_every_role_carries_a_description():
    """The mapping is value -> what it means, like every other closed vocabulary here.

    A bare list would parse and would leave the distinction between the two roles
    undocumented in the one file that is supposed to hold it.
    """
    for role, description in agent_roles().items():
        assert isinstance(description, str) and description.strip(), role


def test_the_role_names_match_their_prompt_templates():
    """**§3's "an agent is defined by the file it produces", checkable at the log layer.**

    `rule_author` and `auditor` are the stems of `docs/prompts/rule_author.md` and
    `auditor.md`. The correspondence is the point of the spelling — but no code derives one
    from the other, which `test_no_module_derives_a_role_from_a_filename` pins. This test
    asserts the agreement; that one asserts nobody computes it.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for role in agent_roles():
        expected = root / "docs" / "prompts" / f"{role}.md"
        assert expected.name == f"{role}.md"
        # Both templates now exist — `auditor.md` landed 2026-08-12, and the exemption this
        # loop carried for it is gone rather than left as a passing branch. The vocabulary
        # was still declared first, on purpose: the prompt is hashed into the freeze record
        # (DESIGN §5.5), and a value invented at the moment the prompt lands is a value
        # chosen while the arm is being wired. A **new** role is therefore added to
        # `naming.yaml` and to `docs/prompts/` in one commit, and this assertion is what
        # says so — a role declared without a template is a window hash over a file nobody
        # wrote.
        assert expected.exists(), expected


def test_a_role_outside_the_vocabulary_is_refused():
    with pytest.raises(CorpusError, match="not an agent role"):
        check_agent_role("RuleAuthor")


@pytest.mark.parametrize("bad", ["rule-author", "ruleauthor", "Auditor", "aud", ""])
def test_the_near_spellings_are_all_refused(bad):
    """The specific defect: two spellings of one agent, and a log that splits its calls.

    Each of these is a plausible thing for a caller to write, none of them is a value the
    config declares, and a log carrying two of them is one this project cannot total.
    """
    with pytest.raises(CorpusError, match="not an agent role"):
        check_agent_role(bad)


@pytest.mark.parametrize("role", sorted(ROLES))
def test_a_declared_role_is_returned_unchanged(role):
    assert check_agent_role(role) == role


def test_a_missing_block_is_refused_rather_than_defaulted(monkeypatch):
    """An empty vocabulary would make `check_agent_role` refuse everything, which reads as
    a caller bug rather than as a config that lost a block."""
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {})
    with pytest.raises(CorpusError, match="no `agent_role` mapping"):
        agent_roles()


@pytest.mark.parametrize("block", [
    {},
    {"": "empty key"},
    {1: "not a string"},
])
def test_a_malformed_block_is_refused(monkeypatch, block):
    """Validated on read for `termination_params()`'s reason: each key is a value written
    to a log, and a key that is not a non-empty string is one no reader can group by."""
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {"agent_role": block})
    with pytest.raises(CorpusError):
        agent_roles()


def test_no_module_derives_a_role_from_a_filename():
    """**The layer-from-detector-name prohibition, one field over (CLAUDE.md, DESIGN §3).**

    A mapping from prompt filename to role would be a component whose mistakes look like
    data: it would produce a plausible role for every line, including the lines it got
    wrong. The caller states its own role. Asserted structurally because the derivation is
    a one-line convenience that reads as removing duplication.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `Path(...).stem` / `.removesuffix(".md")` on anything, in a module that also
            # mentions a role — the two together are what a derivation looks like.
            if isinstance(node, ast.Attribute) and node.attr == "stem":
                text = path.read_text(encoding="utf-8")
                assert "agent_role" not in text and "check_agent_role" not in text, (
                    f"{path} takes a filename stem and handles agent roles. A role derived "
                    "from a prompt filename is a role no reader can distinguish from one "
                    "that was recorded (DESIGN §3)."
                )
