"""A declared prompt revision: what the pinned bytes are, and what declaring one did not move.

DESIGN §6.3's last subsection lets an arm declare that the revision it sends is older than the
file on disk, so that an Auditor-ablation arm can differ from `port-loop` in one thing instead
of two. That mechanism touched seven modules, and the whole of its value rests on two claims
that are not visible in any one of them:

  1. **The pinned bytes are the bytes.** `docs/prompts/pinned/rule_author.ddc48bdb.md` hashes
     to the `prompt_sha256` in `port-loop`'s `window_freeze.json`, and the round-1 prompt
     assembled under it is byte-identical to the round-1 prompt `port-loop` actually sent —
     the same character count and the same sha256 that arm's first call line recorded. That
     is the paired comparison's precondition, and it is checked here rather than discovered
     at the arm's second call.

  2. **Nothing else moved.** Ten arms have frozen a window. None of them declares a template,
     every one of them must still hash to the same record bytes, and `window_drift()` must
     still return for them exactly what an implementation that knows nothing about arms
     returns. An arm-aware resolution that leaked into the default would be invisible in the
     ten records — they are already written — and visible only in what `window_drift()` says
     about them today.

The second claim is the one that needed a test rather than a reading. `porting=None` and "this
arm declared nothing" are different routes to the same answer, there are four of them
(`arm_window`, `window_files`, `template_path`, `window_hashes`), and a default that is wrong
in one of them is wrong for every arm in the paper.

No surface form from any corpus appears here: the prompt is compared by length and hash
(CLAUDE.md), which is also the only way the comparison could be made at all — the round-1
prompt is not written to disk.

    python3 -m pytest tests/test_pinned_prompts.py -q
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora import base                                        # noqa: E402
from src.corpora.base import (CorpusError, arm_absent_agents,       # noqa: E402
                              arm_calls_agent, arm_templates, porting_arms,
                              porting_repetition_index, porting_repetition_of,
                              porting_repetition_set, porting_repetitions,
                              porting_rung)
from src.llm import prompt as prompt_module                         # noqa: E402
from src.orchestrate import (freeze_path, recorded_window_fields,    # noqa: E402
                             window_drift)
from src.sample import (DECLARABLE_TEMPLATES, PINNED_PROMPT_DIR,     # noqa: E402
                        PROMPT_TEMPLATE, WINDOW_FILES, SamplingError,
                        arm_window, template_path, window_files,
                        window_hashes)

#: The pinned revision and its full digest. `cc7bc4c^`'s `docs/prompts/rule_author.md`,
#: which is the revision `port-loop` ran its eight rounds under. The constant is written
#: out rather than read from the file it names, which is the point: a test that hashed the
#: file and compared the result to the file's hash would pass after any edit.
PINNED_RULE_AUTHOR = f"{PINNED_PROMPT_DIR}/rule_author.ddc48bdb.md"
PINNED_SHA256 = "ddc48bdb0a0c286ab8194ba38f49e97b3cf95f6463e6e69c93d30915eef52cfa"

#: The arm the pinned revision is paired with, and the arm that declares it.
PAIRED = ("es-meddocan", "R", "sup-free", "port-loop")
PINNED_RUNG = "port-loop-noaudit"
CURRENT_RUNG = "port-loop-noaudit-current"

#: The repetition set: the base and its two declared repetitions, in index order
#: (DESIGN §6.10). Written out rather than read from `porting_repetition_set()`, which is
#: the accessor several tests below are checking — reading it here would make them compare
#: the accessor with itself.
REPETITION_SET = (PINNED_RUNG, "port-loop-noaudit-rep2", "port-loop-noaudit-rep3")

#: `port-loop`'s round-1 prompt, from the `prompt_reference` of line 1 of its
#: `agent_calls.jsonl`: `text_chars` and `text_sha256`. Copied in as constants because that
#: log is deny-listed by `tools/release_screen.py` and is therefore not in a clone — and
#: because a test that only re-read the log would be comparing the log with itself.
#: `test_the_round_one_hash_is_the_one_port_loop_logged` closes the loop where the log is
#: present, so the constants cannot drift into being whatever makes the suite green.
PORT_LOOP_ROUND_1_CHARS = 53644
PORT_LOOP_ROUND_1_SHA256 = \
    "sha256:6528088f2ae3f8dad88e08468ae86ba2c37edc9ae61b49d270b47366372ba61b"

#: The arm's language and corpus, which §1.1 and §1.2 are built from. Round 1 of `port-loop`
#: on `es-meddocan`, which is the only round-1 prompt there is a recorded hash for.
PAIRED_LANG = "es"

#: Every arm with a freeze record on disk when the declaration mechanism was added, and the
#: sha256 of that record's bytes. Ten of them, four corpora, six `porting` values.
#:
#: **A table rather than a walk of `results/`**, and the difference is what the test asserts.
#: A walk would include `port-loop-noaudit`'s record the moment that arm freezes, and then
#: "did any record change" would be answered against a set that grows — which is the question
#: `test_no_existing_arms_freeze_record_changed` is not asking. These ten are the records that
#: existed before, so these ten are what "before and after" can be said about. A new arm is
#: not added here; it gets its own evidence where it is introduced.
FROZEN_RECORDS = {
    ("de-grascco", "R", "sup-free", "port-oneshot"):
        "3d2c64c2f81be073bfb7a2e654b58b559e20998d442a1360fd1736703bb6cd9f",
    ("en-deid", "R", "sup-free", "port-oneshot"):
        "10afd104eca50c007b100d9eac2308b0fd28b6f07a3299d2de245cd82082c935",
    ("es-carmen", "R", "sup-free", "port-oneshot"):
        "dd01de256d9a03b0595686fec0c92ddc1876a7bd8a6676237237c9ce3bf45049",
    ("es-meddocan", "R", "sup-free", "port-human"):
        "3f3af727038d4b2e01477227236bbfe655a8c0b0eeddad51289421ae7e370f25",
    ("es-meddocan", "R", "sup-free", "port-loop"):
        "de3c96781155a733f1072b00a7b047ef961436c5b4ebd7b83b5eee237e78b6de",
    ("es-meddocan", "R", "sup-free", "port-multi"):
        "a14cc1978968e8c11d4bd71ba0846711c8c7e55a975aa4b8ad3189779059a92c",
    ("es-meddocan", "R", "sup-free", "port-multi-noexample"):
        "7436760010273ddf8b8a00a64365f0ffd00024b1e053c59e76631995ff1580f4",
    ("es-meddocan", "R", "sup-free", "port-oneshot"):
        "7a7511d9494b0d818c05bd0612881772760e44006893e8e76abbd97d797e4ef3",
    ("es-meddocan", "R", "sup-free", "port-oneshot-nofence"):
        "00daa3fc5d746c88c5f84dfec410f20fd12331e3724f92fc1071fc2395fb319f",
    ("ko-surro", "R", "sup-free", "port-oneshot"):
        "3f6de62966d1619015eec7421180ff31f4f5d9fa821c208d4c87168dc3c42f68",
}


# ─── 1. the pinned bytes ────────────────────────────────────────────────────

def test_the_pinned_revision_hashes_to_the_digest_in_its_name():
    """The file is immutable, and this is what "immutable" is implemented as.

    The fence policy does not cover `docs/prompts/pinned/` (DESIGN §6.3), and this assertion
    is what is given in exchange. It is the stronger half of the trade: the fence check fails
    on an edit that *adds* a fenced block and passes on every other edit, while this fails on
    any edit at all — including an edit that takes the six fenced examples out, which is the
    edit a reader of the fence policy would make and the one that would destroy the record.
    """
    path = ROOT / PINNED_RULE_AUTHOR
    assert path.is_file(), f"{PINNED_RULE_AUTHOR} is missing; DESIGN §6.3 and that arm need it"
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    assert got == PINNED_SHA256, (
        f"{PINNED_RULE_AUTHOR} has been edited. A pinned revision is the record of what an "
        "arm with a published comparison ran under, so this is not a hash to update: restore "
        "the file. If the fence policy is what prompted the edit, read "
        "prompt.pinned_prompt_files() — that directory is deliberately outside it."
    )
    assert path.name == f"rule_author.{PINNED_SHA256[:8]}.md", (
        "the filename's digits and the file's hash disagree. The name is how a reader checks "
        "the pin without running anything (docs/prompts/pinned/README.md)."
    )


def test_the_pinned_revision_is_the_window_port_loop_froze():
    """Equality with the published record, which is what makes the pin a *pairing*.

    The previous test says the file has not moved; this says it is the right file. Read out of
    `port-loop`'s own `window_freeze.json` rather than written here a second time — the
    constant above is this repository's claim about the file, and the record is `port-loop`'s
    claim about its run, and the test is that the two agree.
    """
    record = json.loads(freeze_path(*PAIRED).read_text(encoding="utf-8"))
    assert record["prompt_sha256"] == f"sha256:{PINNED_SHA256}", (
        "the pinned revision is not the one port-loop froze, so an arm declaring it is not "
        "the paired control DESIGN §6.3 describes — it differs from port-loop in the prompt "
        "as well as in the Auditor, which is the confound the pin exists to remove."
    )


def test_the_pinned_directory_readme_lists_every_file_in_it():
    """One row per pinned file, and the prohibition in words.

    The hash assertions above are per file and this is what makes a *second* pinned file
    visible: added without a row and without its own constant, it would be governed by the
    partition test (so not unchecked) and by nothing else (so not pinned to anything).
    """
    readme = (ROOT / PINNED_PROMPT_DIR / "README.md").read_text(encoding="utf-8")
    assert "may be edited" in readme, readme[:200]
    for relative in prompt_module.pinned_prompt_files():
        name = Path(relative).name
        if name == "README.md":
            continue
        assert name in readme, (
            f"{relative} is in docs/prompts/pinned/ and not in that directory's README. "
            "Every pinned revision needs a row saying which arm pinned it and a sha256 "
            "assertion in this file; a file with neither is pinned in name only."
        )


# ─── 2. round 1, byte for byte ──────────────────────────────────────────────

def _round_one(porting):
    """The round-1 prompt's reference, for one arm. No text leaves this function."""
    filled = prompt_module.assemble_task_prompt(
        lang=PAIRED_LANG, corpus=PAIRED[0], porting=porting)
    return filled.reference()


def test_the_pinned_rung_reproduces_port_loops_round_one_byte_for_byte():
    """The precondition for reading the Auditor effect off one subtraction. DESIGN §6.3.

    §4 makes round 1 the no-feedback call in both arms — §§1.3 and 1.4 empty, no Auditor
    block of any kind — so under the same template bytes the two prompts are the same string
    or the ablation took something else with it. Asserted on the hash and the length, which
    are what the call line records; the prompt itself is not written down.

    This is also the only test in the suite that covers the whole resolution path at once:
    `naming.yaml`'s declaration, `arm_window()`, `template_path()`, `_template()` and the
    assembler. A break in any one of them moves this hash.
    """
    reference = _round_one(PINNED_RUNG)
    assert reference["text_chars"] == PORT_LOOP_ROUND_1_CHARS, (
        f"round 1 under {PINNED_RUNG} is {reference['text_chars']} characters and "
        f"port-loop's was {PORT_LOOP_ROUND_1_CHARS}. The two differ in something other than "
        "the Auditor, which round 1 does not call in either arm."
    )
    assert reference["text_sha256"] == PORT_LOOP_ROUND_1_SHA256, (
        "round 1 under the pinned rung is the same length as port-loop's and different "
        "bytes. Compare §1.1 and §1.2's inputs (the corpus type inventory and the type "
        "axis) before the template: the template is hash-pinned and they are not."
    )


def test_the_round_one_hash_is_the_one_port_loop_logged():
    """The constants above, against the arm's own call log. Skipped where the log is absent.

    `agent_calls.jsonl` is deny-listed and not in a clone, so this cannot be the primary
    assertion — but without it the two constants are only this file's memory of a run, and a
    future edit that made the previous test green by moving them would look like a fix.
    """
    path = freeze_path(*PAIRED).parent / "agent_calls.jsonl"
    if not path.exists():
        pytest.skip("agent_calls.jsonl is deny-listed; not present in this checkout")
    with open(path, encoding="utf-8") as fh:
        first = json.loads(fh.readline())
    assert first["iteration"] == 1 and first["role"] == "rule_author", (
        first["iteration"], first["role"])
    assert first["prompt_reference"]["text_chars"] == PORT_LOOP_ROUND_1_CHARS
    assert first["prompt_reference"]["text_sha256"] == PORT_LOOP_ROUND_1_SHA256


def test_the_current_revision_rung_differs_at_round_one_and_that_is_its_purpose():
    """The exemption, asserted as a difference rather than left as a note.

    `port-loop-noaudit-current` is exempt from the byte-identity check, and DESIGN §6.3 says
    the exemption *is* the measurement — the arm exists to show what the revision is worth on
    this rung. If its round 1 ever came out identical to `port-loop`'s, the exemption would be
    covering nothing and the second arm would be a duplicate of the first paid for twice.
    """
    assert _round_one(CURRENT_RUNG)["text_sha256"] != PORT_LOOP_ROUND_1_SHA256
    assert _round_one(CURRENT_RUNG)["text_sha256"] == _round_one(None)["text_sha256"], (
        "the arm that declares no template is not reading the current revision, which is the "
        "default every arm run to date got (DESIGN §6.3)."
    )


def test_the_two_ablation_rungs_differ_only_in_the_template():
    """Same code path, one window hash apart — the `port-multi-stripfence` pattern mirrored.

    Both prompts are the template followed by the same blocks, so the remainder after the
    template is the part the ablation could have disturbed and it is compared directly. A
    difference here means the two rungs are two experiments, and the revision effect read off
    them would include whatever else changed.

    **Compared as hashes of the remainder, and the failure message carries only hashes.** The
    two strings have to be in memory for the comparison and neither may reach a terminal: §1.2
    of a round-1 prompt is the type axis and §1.1 is a measured inventory, and the rule is
    applied without asking which of them would have been safe (CLAUDE.md). `for_transport()`
    is the named exit and this is the one test that uses it — a test is not the transport, but
    there is no accessor for "the bytes, for an equality check", and inventing one would widen
    `FilledPrompt.EXITS` for the convenience of a test.
    """
    pinned_text = (ROOT / PINNED_RULE_AUTHOR).read_text(encoding="utf-8")
    live_text = (ROOT / PROMPT_TEMPLATE).read_text(encoding="utf-8")
    a = prompt_module.assemble_task_prompt(
        lang=PAIRED_LANG, corpus=PAIRED[0], porting=PINNED_RUNG).for_transport()
    b = prompt_module.assemble_task_prompt(
        lang=PAIRED_LANG, corpus=PAIRED[0], porting=CURRENT_RUNG).for_transport()
    assert a.startswith(pinned_text), "the pinned rung's prompt does not open with the pinned "\
        "template, so the declaration was resolved somewhere other than the first block"
    assert b.startswith(live_text), "the current rung's prompt does not open with the live "\
        "template"
    rests = {name: hashlib.sha256(text[length:].encode("utf-8")).hexdigest()
             for name, text, length in ((PINNED_RUNG, a, len(pinned_text)),
                                        (CURRENT_RUNG, b, len(live_text)))}
    assert len(set(rests.values())) == 1, (
        "the two ablation rungs differ somewhere outside the template, so the difference "
        f"between them is not the revision alone. Remainder hashes: {rests}"
    )


# ─── 3. the declaration moves one field ─────────────────────────────────────

def test_the_declaration_replaces_one_window_entry_and_leaves_the_other_five():
    """In place, so the record's `files` list stays comparable across arms (DESIGN §6.3)."""
    declared = arm_window(PINNED_RUNG)
    assert list(declared) == list(WINDOW_FILES), "a declaration moved a position, not a path"
    assert declared[PROMPT_TEMPLATE] == PINNED_RULE_AUTHOR
    for name in WINDOW_FILES[1:]:
        assert declared[name] == name, name
    assert window_files(PINNED_RUNG)[0] == PINNED_RULE_AUTHOR
    assert window_files(PINNED_RUNG)[1:] == WINDOW_FILES[1:]


def test_the_pinned_hash_lands_in_prompt_sha256_like_any_other():
    """Keyed on the default path, so `port-loop`'s record and this arm's stay comparable.

    The field name comes from `WINDOW_HASH_FIELDS[PROMPT_TEMPLATE]` and not from the pinned
    file's own name. A `rule_author_ddc48bdb_sha256` would make the two records incomparable
    in exactly the field the comparison is about.
    """
    pinned = window_hashes(porting=PINNED_RUNG)
    live = window_hashes()
    assert pinned["prompt_sha256"] == f"sha256:{PINNED_SHA256}"
    assert pinned["prompt_sha256"] != live["prompt_sha256"], (
        "the live template now hashes to the pinned revision's digest, which would mean "
        "docs/prompts/rule_author.md has been reverted to cc7bc4c^ — the pin would still be "
        "correct and the second ablation rung would be measuring nothing."
    )
    assert {k: v for k, v in pinned.items() if k != "prompt_sha256"} == \
        {k: v for k, v in live.items() if k != "prompt_sha256"}


def test_a_declaration_outside_the_pinned_directory_is_refused(monkeypatch):
    """The positive control for `arm_window()`'s location check.

    A revision filed anywhere else under `docs/prompts/` is a live template: the fence policy
    applies to it, and someone will eventually bring it into compliance — silently changing
    what a published arm's counterpart ran under, months after the comparison was reported.

    Injected rather than written into `naming.yaml`, because the refused shape must not be
    committed to the config: a declaration that no arm may use is not a declaration to keep
    on disk, and the test needs it to exist for one call.
    """
    import src.sample as sample_module
    live_copy = PROMPT_TEMPLATE.replace(".md", ".ddc48bdb.md")
    monkeypatch.setattr(sample_module, "arm_templates",
                        lambda porting: {PROMPT_TEMPLATE: live_copy})
    with pytest.raises(SamplingError) as caught:
        sample_module.arm_window(PINNED_RUNG)
    assert PINNED_PROMPT_DIR in str(caught.value)
    assert live_copy in str(caught.value)


def test_only_the_rule_author_template_is_declarable():
    """The limit is which readers are arm-aware, and it is one (DESIGN §6.3, `src/sample.py`).

    Stated as a test because widening the tuple is a one-line change whose other half —
    threading `porting` to that file's reader — is in a different module. A record attesting
    to a window nothing ran under is the failure §6.3 opens with.
    """
    assert DECLARABLE_TEMPLATES == (PROMPT_TEMPLATE,)
    for porting, declaration in porting_arms().items():
        outside = set(declaration["templates"]) - set(DECLARABLE_TEMPLATES)
        assert outside == set(), (porting, sorted(outside))


# ─── 4. the Auditor's absence is declared, not derived ──────────────────────

def test_both_ablation_rungs_declare_the_auditor_absent():
    """The declaration the driver reads before paying for a call."""
    for porting in (PINNED_RUNG, CURRENT_RUNG):
        assert arm_absent_agents(porting) == ("auditor",), porting
        assert arm_calls_agent(porting, "auditor") is False, porting


def test_an_arm_that_declares_nothing_calls_every_agent():
    """The default does not move: absence of a declaration is not a declared absence.

    `port-loop` calls the Auditor and `port-oneshot` has no round that could, and neither of
    them says anything about it in `naming.yaml`. The accessor has to answer "not declared
    absent" for both — an accessor that derived the answer from whether the arm iterates
    would be deriving a role from an arm's name, which is the derivation this repository
    refuses everywhere (CLAUDE.md).
    """
    for porting in ("port-loop", "port-oneshot", "port-multi", "port-human"):
        assert arm_absent_agents(porting) == (), porting
        assert arm_calls_agent(porting, "auditor") is True, porting
        assert arm_templates(porting) == {}, porting
        assert template_path(PROMPT_TEMPLATE, porting) == PROMPT_TEMPLATE, porting


def test_the_absent_audit_block_and_the_real_one_describe_themselves_the_same_way():
    """One key set, so a reader of two records compares fields rather than looking for one.

    The empty block's reference carries `None` for every measurement and `True` for
    `audit_agent_absent`; the real one carries numbers and `False`. Unequal key sets would
    make "this arm made no Auditor call" a *missing* field in one record and a value in the
    other, and a missing field is indistinguishable from a writer that forgot.
    """
    absent = prompt_module._absent_audit_block(iteration=2)[1]
    assert set(absent) == set(prompt_module.AUDIT_REFERENCE_FIELDS)
    assert absent["audit_agent_absent"] is True
    assert absent["audit_block_sha256"].startswith("sha256:")
    assert [name for name, value in absent.items()
            if value is None] == [name for name in prompt_module.AUDIT_REFERENCE_FIELDS
                                  if name not in ("audit_agent_absent", "audit_block_sha256")]


def test_the_empty_block_is_the_same_bytes_at_every_round_and_on_both_rungs():
    """Both ablation arms render it identically, which is what leaves the template as the
    only difference between them (`test_the_two_ablation_rungs_differ_only_in_the_template`).

    The round number is accepted and deliberately unused. A block that named its round would
    differ between rounds, which is harmless, and between the two rungs only if they ever
    diverged in round count — which is exactly the difference this file is trying not to have.
    """
    blocks = {prompt_module._absent_audit_block(iteration=n)[0] for n in (2, 3, 8)}
    assert len(blocks) == 1


# ─── 5. what declaring one did not move ─────────────────────────────────────

@pytest.mark.parametrize("arm", sorted(FROZEN_RECORDS))
def test_no_existing_arms_freeze_record_changed(arm):
    """The bytes of all ten records on disk, against the digests taken before the change.

    The condition on the mechanism, in the form it was asked for: if one of these moves, stop.
    The records are committed artefacts of runs that are already spent, so there is no reading
    of a difference here that is not a defect — a widened writer, a repair script, or an
    arm-aware resolution that reached backwards through `freeze_window()`.
    """
    path = freeze_path(*arm)
    assert path.is_file(), f"{arm}: freeze record is gone — restore it from git, do not refreeze"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN_RECORDS[arm], (
        f"{arm}: this arm's freeze record has changed. It records a window a call already ran "
        "under, so the record is right and whatever rewrote it is wrong (DESIGN §6.3)."
    )


@pytest.mark.parametrize("arm", sorted(FROZEN_RECORDS))
def test_window_drift_is_unchanged_for_every_existing_arm(arm):
    """`window_drift()` became arm-aware. For these ten it must still answer as before.

    The comparison is against a reference implementation that resolves the window *globally* —
    `window_hashes()` with no arm, which is what the function did before the declaration
    existed — rather than against a list of field names copied in here. Both would catch a
    leak today; only this one keeps catching it after someone legitimately edits a prompt,
    because a hard-coded expectation would have to be edited in the same commit and the
    easiest edit is the one that makes the suite green.
    """
    record = json.loads(freeze_path(*arm).read_text(encoding="utf-8"))
    now = window_hashes()
    expected = [field for field in recorded_window_fields(record)
                if record.get(field) != now[field]]
    assert window_drift(*arm) == expected, (
        f"{arm}: the arm-aware drift check disagrees with an arm-blind one on an arm that "
        "declares no template. The declaration mechanism has changed the default, which is "
        "every arm in the paper."
    )


@pytest.mark.parametrize("arm", sorted(FROZEN_RECORDS))
def test_no_existing_arm_declares_a_template(arm):
    """What makes the test above a valid comparison, asserted rather than assumed.

    If one of these ten ever declares a revision, the arm-blind reference stops being the
    right answer for it and that test would start failing for a legitimate reason. It should
    fail *here* first, where the message says so, instead of there, where the message says the
    default has moved.
    """
    assert arm_templates(arm[3]) == {}, (
        f"{arm[3]} now declares a template. It has a frozen record from before declarations "
        "existed, so its window cannot be retargeted: the record would describe a file its "
        "calls never opened. Declare a new arm instead (DESIGN §6.3)."
    )


def test_the_ten_records_are_the_ones_that_predate_the_mechanism():
    """The table's size, so a shrinking table is a failure rather than a smaller test run.

    Ten entries, parametrised three ways above. `@parametrize` over an empty mapping is a pass
    with zero tests, which is the one way the three tests above can stop checking anything
    without anyone editing them.
    """
    assert len(FROZEN_RECORDS) == 10
    assert {arm[3] for arm in FROZEN_RECORDS} == {
        "port-oneshot", "port-oneshot-nofence", "port-human", "port-loop", "port-multi",
        "port-multi-noexample"}


# ─── 6. the arm reaches every writer ────────────────────────────────────────

def _call_line_sites(path):
    """Every `call_line(...)` call in one file, as `(lineno, keyword names)`.

    Parsed rather than grepped. The question is which *arguments* a call passes, and a text
    search answering it would have to decide where the call's parentheses close — which is
    the sort of reimplementation that reports a clean result on the one call formatted
    differently from the rest.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "call_line":
            sites.append((node.lineno, {kw.arg for kw in node.keywords}))
    return sites


def test_every_call_line_site_forwards_the_arm():
    """`porting` is optional at the signature and required at every caller. DESIGN §6.3.

    The default is `None` because `None` is the resolution every caller had before
    declarations existed, which is what keeps the ten records above still correct — and the
    cost of that default is that a driver which forgets the argument writes a call line
    claiming the live window for an arm that sent a pinned one. The line would be
    well-formed, the arm would run to completion, and the freeze record written seconds
    earlier would disagree with it.

    So the guard is here, over the source, rather than a required parameter: four drivers
    pass it today and the fifth is the one this test is for.
    """
    missing = []
    for path in sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tools").rglob("*.py")):
        for lineno, keywords in _call_line_sites(path):
            if "porting" not in keywords:
                missing.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert missing == [], (
        f"call_line() invoked without porting= at {missing}. The call line's window hashes "
        "are resolved through the arm, so a site that omits it records the live revision for "
        "an arm that may have sent a pinned one — and the freeze record written immediately "
        "before the call would disagree with the log of the call (DESIGN §6.3)."
    )


def test_the_source_scan_finds_the_sites_there_are():
    """The positive control. A parser that found nothing would pass the test above.

    Four production sites: the oneshot driver, the loop's round-1 and round-N RuleAuthor
    calls, and its Auditor call, plus `port-multi`'s authoring call. Asserted as a floor
    rather than an exact count — a sixth driver should not have to edit this number — and
    the floor is what makes "no sites found" fail.
    """
    found = sum(len(_call_line_sites(path))
                for path in sorted((ROOT / "src").rglob("*.py")))
    assert found >= 5, found


# ─── 7. the repetitions declare what the base declares ──────────────────────

@pytest.fixture
def _clear_naming_caches():
    """For the two refusal tests, which inject a `porting_repetitions` block.

    Scoped to those tests rather than autouse: the rest of this file reads the committed
    config, and clearing the cache around every test would hide a reader that depends on
    warm state.
    """
    base.naming.cache_clear()
    base.porting_arms.cache_clear()
    base.porting_repetitions.cache_clear()
    yield
    base.naming.cache_clear()
    base.porting_arms.cache_clear()
    base.porting_repetitions.cache_clear()


def test_the_set_is_the_base_and_two_repetitions_in_index_order():
    """Three runs of one configuration, and the base is not one of the declared two.

    The base's absence from `porting_repetitions` is the load-bearing part: the sealed-eval
    refusal keys on the presence of an entry, so an entry here for `port-loop-noaudit`
    would refuse the one opening DESIGN §6.4 assigns to it (§6.10).
    """
    assert porting_repetition_set(REPETITION_SET[0]) == REPETITION_SET
    assert sorted(porting_repetitions()) == sorted(REPETITION_SET[1:])
    assert porting_repetition_of(REPETITION_SET[0]) is None
    assert porting_repetition_index(REPETITION_SET[0]) == 1
    for index, porting in enumerate(REPETITION_SET[1:], start=2):
        assert porting_repetition_of(porting) == REPETITION_SET[0], porting
        assert porting_repetition_index(porting) == index, porting


def test_any_member_of_the_set_answers_for_the_whole_set():
    """So a report cannot name a subset by asking the wrong member (DESIGN §6.10).

    The range and mean of the final-round leak rates are statements about three runs. A
    caller holding `…-rep3` and asking what to report has to get all three back, because
    the alternative is a per-repetition table that reads like a three-arm ladder.
    """
    for porting in REPETITION_SET:
        assert porting_repetition_set(porting) == REPETITION_SET, porting


def test_every_repetition_declares_exactly_what_the_base_declares():
    """The condition on the mechanism, asserted and not only refused.

    `porting_repetitions()` refuses a divergence, so this is the invariant restated where a
    reader looks for it — and restated field by field against the base's own entry rather
    than against a copy of the expected values, because a copy would have to be edited in
    whichever commit moved the base.
    """
    expected = porting_arms()[REPETITION_SET[0]]
    for porting in REPETITION_SET[1:]:
        assert porting_arms()[porting] == expected, porting
        assert arm_templates(porting) == {PROMPT_TEMPLATE: PINNED_RULE_AUTHOR}, porting
        assert arm_absent_agents(porting) == ("auditor",), porting
        assert arm_calls_agent(porting, "auditor") is False, porting


def test_every_repetition_resolves_the_same_window_and_the_same_hashes():
    """`sampling` and the prompt revision, through the readers that write the record.

    The declaration being equal is a statement about `naming.yaml`; this is the statement
    about what `freeze_window()` will write. Both are needed: the window is resolved by
    `src/sample.py` from the declaration, and a reader that ignored the arm would make the
    three records agree for the wrong reason — so the pinned digest is asserted too.
    """
    for porting in REPETITION_SET:
        assert arm_window(porting) == arm_window(REPETITION_SET[0]), porting
        assert window_files(porting) == window_files(REPETITION_SET[0]), porting
        hashes = window_hashes(porting=porting)
        assert hashes == window_hashes(porting=REPETITION_SET[0]), porting
        assert hashes["prompt_sha256"] == f"sha256:{PINNED_SHA256}", porting


def test_round_one_is_byte_identical_across_the_repetition_set():
    """Same configuration means the same prompt, which is the premise of the comparison.

    Hashes only in the message, for the reason given in
    `test_the_two_ablation_rungs_differ_only_in_the_template`: these strings are a measured
    inventory and the type axis, and the rule against surface forms in output is applied
    without asking which corpus they came from (CLAUDE.md).
    """
    digests = {}
    for porting in REPETITION_SET:
        text = prompt_module.assemble_task_prompt(
            lang=PAIRED_LANG, corpus=PAIRED[0], porting=porting).for_transport()
        digests[porting] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert len(set(digests.values())) == 1, (
        "the repetition set's round-1 prompts are not identical, so the runs differ in "
        f"something other than the minute they were called. Digests: {digests}"
    )
    assert digests[REPETITION_SET[0]] == PORT_LOOP_ROUND_1_SHA256.split(":", 1)[1], (
        "the set's round-1 prompt is no longer `port-loop`'s, which is the byte identity "
        "the pinned revision exists for (DESIGN §6.3)."
    )


def test_the_repetitions_are_on_their_bases_rung_and_not_a_new_one():
    """A repetition runs the same driver, so it is rung membership and nothing more.

    The three other questions rung membership is sometimes read for — ladder point,
    termination verdict, seal opening — are answered no by `porting_repetitions`, and the
    third of them is tested in `tests/test_sealed_scoring.py` (DESIGN §6.10).
    """
    for porting in REPETITION_SET:
        assert porting_rung(porting) == porting_rung(REPETITION_SET[0]), porting


@pytest.mark.parametrize("porting", REPETITION_SET)
def test_the_frozen_records_of_the_set_agree_where_they_exist(porting):
    """The prompt hash and the model id, once the runs have happened.

    Skipped until then, on the precedent of
    `test_the_round_one_hash_is_the_one_port_loop_logged`: a repetition's record is written
    by `freeze_window()` at its first call, and asserting on an absent file would make this
    a test that fails for three weeks and is then edited. The assertion is the one the
    condition asked for — the set's freeze records carry one prompt hash between them — and
    it is checked pairwise against the base's so that a run under a moved window fails here
    rather than in the report.
    """
    path = freeze_path(*PAIRED[:3], porting)
    base_path = freeze_path(*PAIRED[:3], REPETITION_SET[0])
    if not (path.is_file() and base_path.is_file()):
        pytest.skip(f"{porting} has not frozen a window yet")
    record = json.loads(path.read_text(encoding="utf-8"))
    expected = json.loads(base_path.read_text(encoding="utf-8"))
    assert record["prompt_sha256"] == f"sha256:{PINNED_SHA256}", porting
    for field in ("prompt_sha256", "sampling_sha256", "agents_absent", "files"):
        assert record.get(field) == expected.get(field), (porting, field)
    if "model_id" in expected or "model_id" in record:
        assert record.get("model_id") == expected.get("model_id"), porting


def test_a_repetition_that_declares_something_else_is_refused(_clear_naming_caches,
                                                              monkeypatch):
    """The positive control for the equality refusal (DESIGN §6.10).

    Injected rather than committed, for the reason given in
    `test_a_declaration_outside_the_pinned_directory_is_refused`: the refused shape is not a
    shape to keep on disk. What it stands for is the real failure — a repetition retargeted
    to the live template, whose freeze record would then say so while the report called it
    a repetition of the pinned arm.

    **The divergence is one `porting_arms()` accepts, and the message is pinned by its
    distinctive phrase.** Both halves were got wrong first and the mutation survived: a
    `templates: {}` entry is refused by `porting_arms()` itself, two guards upstream, and
    the assertion `"port-loop-noaudit" in message` is satisfied by *any* message naming
    `port-loop-noaudit-rep2`, because one name is a prefix of the other. A substring
    assertion over values with a shared stem cannot say which guard refused.
    """
    config = dict(base.naming())
    arms = {name: dict(entry) for name, entry in config["porting_arms"].items()}
    arms[REPETITION_SET[1]] = dict(arms[REPETITION_SET[0]], agents_absent=["profiler"])
    config["porting_arms"] = arms
    monkeypatch.setattr(base, "naming", lambda: config)
    base.porting_arms.cache_clear()
    base.porting_repetitions.cache_clear()
    with pytest.raises(CorpusError) as caught:
        base.porting_repetitions()
    assert "declares something else in porting_arms" in str(caught.value)


def test_a_declared_first_repetition_is_refused(_clear_naming_caches, monkeypatch):
    """`index` starts at 2, and the reason is the seal rather than counting from one.

    An entry for the base would be a true statement — it is the first run — and it would
    silently withdraw the opening §6.4 grants it, because the refusal in `plan_arm` keys on
    the presence of an entry. So the shape is refused at the config rather than special-cased
    at the gate.

    **This refusal is subsumed for refusal purposes and kept for its message**, which is why
    it carries no mutation: a self-entry is also a chain (`of` names a key of the block, and
    that key is itself), so removing this check leaves the configuration refused one guard
    later with a different message. The phrase is asserted rather than the exception type, so
    the test says which of the two fired — `"itself"` alone is in both messages.

    **The injected block holds the self-entry alone.** With the real repetitions left in it,
    `rep2` is validated first and its `of` names a key of the block, so the chain refusal
    fires before the base's own entry is reached and the test would be asserting on a message
    about a shape it did not inject.
    """
    config = dict(base.naming())
    config["porting_repetitions"] = {
        REPETITION_SET[0]: {"of": REPETITION_SET[0], "index": 1},
    }
    monkeypatch.setattr(base, "naming", lambda: config)
    base.porting_arms.cache_clear()
    base.porting_repetitions.cache_clear()
    with pytest.raises(CorpusError) as caught:
        base.porting_repetitions()
    assert "names itself" in str(caught.value)


def test_a_repetition_of_a_repetition_is_refused(_clear_naming_caches, monkeypatch):
    """Chains are the shape that survives every other check (DESIGN §6.10).

    `…-rep3` declared a repetition *of `…-rep2`* passes the equality check — all three arms
    declare the same thing — and passes the index checks, because grouping by `of` gives
    `rep2` a set of one whose index is 2. So the configuration would be accepted, and then
    the repetition set would **split**: the base's set would hold two members and `rep3`'s a
    different two, with `rep2` in both. A report asking either one would get a subset, and
    the seal refusal would still fire on all three, so nothing downstream would notice.
    """
    config = dict(base.naming())
    config["porting_repetitions"] = {
        REPETITION_SET[1]: {"of": REPETITION_SET[0], "index": 2},
        REPETITION_SET[2]: {"of": REPETITION_SET[1], "index": 2},
    }
    monkeypatch.setattr(base, "naming", lambda: config)
    base.porting_arms.cache_clear()
    base.porting_repetitions.cache_clear()
    with pytest.raises(CorpusError) as caught:
        base.porting_repetitions()
    assert "do not chain" in str(caught.value)
