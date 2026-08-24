"""Tests for src/porting/loop.py — `port-loop`'s rounds, and where the stopping rule runs.

Three things are under test and they fail in different ways.

**The chain.** A round reads round *N−1* and nothing else: its rule file is §1.2, its
`metrics.json` is §1.3's score, the audit of its predictions is §1.3's other half, and a
seeded draw over its `errors.jsonl` is §1.4. So the tests assert *which round* each input
came from, not merely that an input arrived. An off-by-one driver produces a well-formed
prompt every round and a well-formed `metrics.json` every round, and nothing in either file
says the agent was shown the wrong round's feedback — which is why every one of those
assertions names a round number rather than checking for presence.

**The stopping rule's one home.** §3 pre-registers δ, k and the ceiling in
`src/termination.py`, and the round's `termination` block has to describe *that* round,
whose leak rate does not exist until `run_fold` has scored. `PendingTermination` is how the
driver sends the missing argument instead of the answer, and the three properties that makes
it worth having are each a test here: one scoring pass (the rate in the block is the rate in
the same file's headline), one writer (the driver reads the block back rather than patching
it), one implementation (`run_fold` never imports `should_stop`, and the driver never calls
it on a history including this round). The structural halves read syntax trees rather than
text, for `test_run_fold_does_not_sum_costs_itself`'s reason: the docstrings name the
functions they are drawing a boundary around, and a substring search would forbid explaining
the boundary in order to enforce it.

**Ceiling is not convergence.** `tests/test_termination.py` owns the rule's arithmetic. What
is checked here is that the distinction survives the round trip through a published file: a
run that ended at the cap has `reason: "ceiling"` and `converged: false` in
`iter{N}/metrics.json`, and the driver's own return value cannot say otherwise because it is
read out of that file.

**The transport is faked and the scorer is real.** `reply()` — the measured `converse`
response shape — comes from `tests/test_bedrock.py`, like `tests/test_orchestrate.py`'s: two
spellings of one response shape are two shapes and the one that drifts is the copy.
`Transport` below wraps it because this arm has two agents and one client answers both.
`run_fold` is *not* faked: it is
where the pending block is resolved, so a stub there would make every termination assertion
in this file a test of the stub. That costs a detection pass per round and needs the corpus,
so those tests ask for `corpus_present` (from `tests/conftest.py`, the only place
availability is decided). The tests that are about a refusal before any scoring — a bad
round number, a missing predecessor, a stopped arm — do not ask and run on any machine.

The Auditor is called once per dev document, so a round is 250 fake calls plus one. That is
fast because nothing leaves the process, and it is not reduced to a two-document fixture:
`documents_audited` and the round's `llm_calls` are the numbers under test and both are
functions of the fold's size.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate, rules as rules_module, sample as sample_module  # noqa: E402
from src.eval.scorer import iter_metrics_path                                # noqa: E402
from src.llm import bedrock as bedrock_module                                # noqa: E402
from src.orchestrate import (                                                # noqa: E402
    FORMAT_FAILURE, SCORED, OrchestrateError, freeze_path, freeze_window, log_path,
)
from src.porting import loop                                                 # noqa: E402
from src.rules import arm_rules_path                                         # noqa: E402
from src.sample import WINDOW_FILES                                          # noqa: E402
from src.termination import (                                                # noqa: E402
    CEILING, CONVERGED, PendingTermination, Termination, TerminationError, should_stop,
    termination_params,
)

#: `reply()` and `FakeControl` come from the transport's own suite. Imported by module name:
#: `tests/` has no `__init__.py`, so pytest's default import mode puts this directory on
#: `sys.path`.
from test_bedrock import FakeControl, reply                                  # noqa: E402

CORPUS = "es-meddocan"
LANG = "es"
ARM = (CORPUS, "R", "sup-free", "port-loop")

#: The four axes as the driver takes them, keyword for keyword.
ARM_KW = dict(corpus=CORPUS, lang=LANG)

MODEL = "us.anthropic.claude-opus-5"

#: A rule file that loads and fires on MEDDOCAN. It has to fire: a file matching nothing
#: would let every round score an empty prediction set, and then the audit would mask a
#: document with no tags for a reason unrelated to the arm.
GOOD_RULES = """
version: 1
lang: es
rules:
  - rule_id: probe_org
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["Hospital"]
"""

#: A second rule file, byte-different from the first so that "which round's rule file was
#: shown as §1.2" is answerable from the prompt's own hash rather than from a path alone.
OTHER_RULES = GOOD_RULES.replace('terms: ["Hospital"]', 'terms: ["Hospital", "Centro"]')

#: What a model returns when it fences its YAML or stops mid-file. Fails in the parser,
#: which is the `RuleError` the format-failure branch is for.
UNPARSEABLE = "```yaml\nversion: 1\nlang: es\nrules: [\n"

#: An Auditor response that parses: no flags, which is a measurement (`auditor.md` §2.1).
NO_FLAGS = '{"flags": []}'


# ─── the harness ─────────────────────────────────────────────────────────────


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A redirected root with the real window files in place.

    Copied rather than invented so `window_hashes()` hashes the committed window and the
    freeze record is the record a real round would write — a stub would make every drift
    assertion a test of the stub. `src.rules.ROOT` is redirected too, for
    `tests/test_orchestrate.py`'s reason: `rules._relative()` reduces a path against its own
    module's, so an unpatched copy reports every file this arm writes as outside the repo.
    """
    for name in WINDOW_FILES:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dest)
    monkeypatch.setattr(orchestrate, "ROOT", tmp_path)
    monkeypatch.setattr(sample_module, "ROOT", tmp_path)
    monkeypatch.setattr(rules_module, "ROOT", tmp_path)
    monkeypatch.setattr(bedrock_module, "_require_logging_check", lambda: None)
    return tmp_path


@pytest.fixture(autouse=True)
def no_control_plane(monkeypatch):
    """Close the lifecycle probe's route to AWS for every test in this file.

    Patched at the client factory and not at `model_lifecycle`, so the function under test
    still runs and only the socket is gone — `tests/test_orchestrate.py`'s fixture, and its
    reasoning: patching `model_lifecycle` itself would make the record every call line
    carries a stub's.
    """
    monkeypatch.setattr(bedrock_module, "_control_client",
                        lambda region=None: FakeControl())


def sent_text(call: dict) -> str:
    """The prompt a `converse` call carried, joined back across any cache boundary.

    The Auditor's calls are sent as two text blocks with a `cachePoint` between them (DESIGN
    §5.4) and the RuleAuthor's as one, so a helper reading `content[0]["text"]` sees an audit
    prompt truncated at the boundary — which is `auditor.md` §1.1's frame with §1.2's masked
    document missing, exactly the half these tests search. Joining is not a workaround for the
    split: §4 requires the concatenation to be the bytes the uncached call would have sent, so
    reading the prompt this way is reading what the model read.
    """
    return "".join(b["text"] for b in call["messages"][0]["content"] if "text" in b)


class Transport:
    """A `converse` client that answers by role, and records the order it was asked in.

    One object for both agents because there is one transport: the driver passes the same
    `client` to the RuleAuthor's call and to the Auditor's N, and a fixture with two clients
    could not notice a driver that sent an audit prompt to the wrong assembler. Which agent
    is being answered is read off the prompt's `block` reference — `"audit"` for
    `assemble_audit_prompt`, otherwise the RuleAuthor's — which is the same field the driver's
    own `role` is *not* derived from (DESIGN §5.5), so this fake cannot make a driver that
    derived it look right.

    `blocks` is the sequence of prompt blocks in call order. It is what the round-shape tests
    read: `auditor.md` §1.3 puts every audit call before the RuleAuthor's, because the report
    is an *input* to that call.
    """

    def __init__(self, rules_text: str = GOOD_RULES, audit_text: str = NO_FLAGS):
        self.rules_text = rules_text
        self.audit_text = audit_text
        self.blocks: list[str] = []
        self.sent: list[dict] = []

    def converse(self, **kwargs):
        self.sent.append(kwargs)
        text = sent_text(kwargs)
        # The Auditor's template is one of the two window files, and its banner is what
        # distinguishes the two prompts in the transport — the same thing a reader of the
        # sent bytes would look at.
        is_audit = "Auditor prompt" in text
        self.blocks.append("audit" if is_audit else "rules")
        return reply(self.audit_text if is_audit else self.rules_text)

    @property
    def rule_author_calls(self) -> int:
        return sum(1 for b in self.blocks if b == "rules")

    @property
    def audit_calls(self) -> int:
        return sum(1 for b in self.blocks if b == "audit")


def a_frozen_arm(tree):
    """The freeze record and one call line: the state round 1 leaves behind.

    Written directly rather than by running round 1, for the tests whose subject is a
    *later* round. Round 1 is exercised end to end by `test_round_one_...` below; using it
    as a fixture for round 5 would make every one of these tests also a test of it, and a
    round-1 regression would then fail thirty tests that are not about round 1.
    """
    freeze_window(*ARM, sections=loop.ONESHOT_SECTIONS)
    path = log_path(*ARM)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"call_id": "c1", "iteration": 1,
                             "role": "rule_author"}) + "\n")
    return path


def calls(*arm) -> list[dict]:
    path = log_path(*(arm or ARM))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def round_metrics(tree, iteration: int) -> dict:
    return json.loads(iter_metrics_path(
        corpus=CORPUS, detector="R", supervision="sup-free", porting="port-loop",
        iteration=iteration, root=tree).read_text(encoding="utf-8"))


def run_round_1(tree, client=None, **kw):
    return loop.run_iteration_1(**ARM_KW, model_id=MODEL,
                                client=client or Transport(), **kw)


def run_round(tree, iteration: int, client=None, **kw):
    return loop.run_iteration(iteration, **ARM_KW, model_id=MODEL,
                              client=client or Transport(), **kw)


# ─── round 1: the arm's first call, and `port-oneshot`'s procedure ───────────


def test_round_one_freezes_shows_two_blocks_and_calls_once(tree, corpus_present):
    """§§1.1–1.2 and nothing else, which is what makes round 1 the baseline's call.

    `sections_empty` is asserted as well as `sections_shown`, because "these blocks did not
    apply" and "these blocks applied" must not read the same in the record (DESIGN §4).
    """
    fake = Transport()
    out = run_round_1(tree, fake)

    assert out["outcome"] == SCORED
    assert out["iteration"] == 1
    assert fake.rule_author_calls == 1
    assert fake.audit_calls == 0, "round 1 has no predictions to audit"
    record = json.loads(freeze_path(*ARM).read_text(encoding="utf-8"))
    assert record["sections_shown"] == ["1.1", "1.2"]
    assert record["sections_empty"] == ["1.3", "1.4"]
    assert record["sampling_applied"] is False


def test_round_one_writes_a_round_scoped_score_and_the_headline_copy(tree, corpus_present):
    """§5.5's two writes: `iter1/metrics.json` and the un-iterated copy, from one pass.

    The results path is round-scoped because this arm *has* rounds — `run_arm`'s is not, and
    that asymmetry is pinned in `tests/test_orchestrate.py`. Here what matters is that both
    exist after round 1, because round 2 reads the first and a reader reads the second.
    """
    out = run_round_1(tree)
    assert out["metrics_path"].name == "metrics.json"
    assert out["metrics_path"].parent.name == "port-loop"
    assert round_metrics(tree, 1)["headline"]["leak_rate"]["value"] == \
        json.loads(out["metrics_path"].read_text(encoding="utf-8"))[
            "headline"]["leak_rate"]["value"]


def test_round_one_records_not_applicable_and_the_arm_continues(tree, corpus_present):
    """One leak rate has no first difference, so §3's rule has nothing to say yet.

    `not_applicable` and not a null `reason`: a driver that called `should_stop` on a
    sequence of length one would get `reason: null`, because convergence needs `k + 1`
    observations — indistinguishable from correct today, and wrong the moment `k` is 1 in a
    sensitivity check. `improvements` is empty for the same reason, and this is the same
    record `port-oneshot` gets, which is right: at this point the two arms have done the same
    thing.
    """
    run_round_1(tree)
    block = round_metrics(tree, 1)["termination"]
    assert block["reason"] == "not_applicable"
    assert block["converged"] is False
    assert block["improvements"] == []


# ─── which round each input came from ────────────────────────────────────────


def test_a_later_round_audits_the_previous_rounds_predictions(tree, corpus_present):
    """`masked_from_iteration` is *N−1*, and it is stated rather than derived from a listing.

    The audit report is an input to round *N* built from round *N−1*'s `spans.jsonl`. A
    driver that masked its own unwritten output would be recording flags against predictions
    that do not exist, and `audit.report()` refuses the pair that says so — which is why the
    assertion is on the number in the file and not on the call count.
    """
    run_round_1(tree)
    out = run_round(tree, 2)
    report = json.loads(out["audit_report_path"].read_text(encoding="utf-8"))
    assert report["iteration"] == 2
    assert report["masked_from_iteration"] == 1


def audit_prompts(fake: Transport) -> list[str]:
    """The text of every audit prompt the transport was sent, in call order."""
    return [sent_text(c) for c in fake.sent if "Auditor prompt" in sent_text(c)]


#: `OTHER_RULES`' extra term as the gazetteer matches it — on a word boundary, so the
#: inflected `Centros` in one dev document is *correctly* left unmasked and a bare substring
#: search would report the masker as broken.
UNMASKED_TERM = re.compile(r"Centro\b")


def test_a_later_round_masks_the_previous_rounds_spans_and_not_round_ones(
        tree, corpus_present):
    """The number in the report and the spans actually masked are two claims.

    `masked_from_iteration` is what the driver *says* it masked, and `audit.report()` checks
    only that it agrees with the round — which a driver reading some other round's
    `spans.jsonl` satisfies, because it records what it was told. So this reads the masked
    text instead. Round 2 authors a rule file that catches a term round 1's does not, and the
    assertion is that round 3's Auditor never sees that term in the clear.

    Two-sided on purpose: the same term *is* in round 2's audit prompts, whose masking comes
    from round 1's spans. Without that half, a term absent from the fold entirely would make
    the second assertion pass against any driver.
    """
    run_round_1(tree, Transport(rules_text=GOOD_RULES))
    second = Transport(rules_text=OTHER_RULES)
    run_round(tree, 2, second)
    third = Transport()
    run_round(tree, 3, third)

    assert any(UNMASKED_TERM.search(text) for text in audit_prompts(second)), (
        "round 2's Auditor did not see `Centro` in the clear, so this test is measuring "
        "nothing — round 1's rule file has no such term and its spans are what round 2 masks")
    assert not [text for text in audit_prompts(third) if UNMASKED_TERM.search(text)], (
        "round 3's Auditor saw `Centro` unmasked, so the masker was given some round other "
        "than 2's predictions — round 2's rule file catches that term (DESIGN §5.5)")


def test_the_audit_runs_before_the_rule_author_is_called(tree, corpus_present):
    """The report is §1.3's other half, so it must exist before the prompt is assembled.

    Asserted on the transport's call order rather than on the files, because both orders
    leave the same files behind: a driver that called the RuleAuthor first and audited
    afterwards would write a complete `audit_report.json` for a round whose prompt never
    contained it, and every path assertion in this file would still pass.
    """
    run_round_1(tree)
    fake = Transport()
    run_round(tree, 2, fake)
    assert fake.blocks[-1] == "rules"
    assert set(fake.blocks[:-1]) == {"audit"}


def test_every_dev_document_is_audited_including_the_ones_with_no_predictions(
        tree, corpus_present):
    """One call per document (`auditor.md` §1.3), and the fold's size is the round's cost.

    A document with no predictions masks to a document with no tags, which is what the arm's
    output *was* there — skipping it would make `documents_audited` a count of the documents
    the rules happened to fire on, and `documents_with_no_flags` would then have a different
    denominator from the one it names.
    """
    from src.eval.run_fold import load_fold

    n_docs = len(load_fold(CORPUS, "dev"))
    run_round_1(tree)
    fake = Transport()
    out = run_round(tree, 2, fake)
    assert fake.audit_calls == n_docs
    report = json.loads(out["audit_report_path"].read_text(encoding="utf-8"))
    assert report["documents_audited"] == n_docs


def test_the_previous_rounds_rule_file_is_the_one_shown_as_section_1_2(
        tree, corpus_present):
    """§1.2 is round *N−1*'s output under `paths.armrules`, never `rules/{lang}.yaml`.

    The bootstrap file is a committed *format example*; showing it would present the agent
    with rules the arm did not author (DESIGN §5.3). Asserted by making the two rounds'
    files differ and checking the round-3 prompt carries round 2's — a driver reading round
    1's, or the bootstrap file, produces a prompt that is well-formed either way.
    """
    run_round_1(tree, Transport(rules_text=GOOD_RULES))
    run_round(tree, 2, Transport(rules_text=OTHER_RULES))
    fake = Transport()
    run_round(tree, 3, fake)
    sent = [sent_text(c) for c in fake.sent if "Auditor prompt" not in sent_text(c)]
    assert len(sent) == 1
    assert "Centro" in sent[0], (
        "round 3 was not shown round 2's rule file — the round-1 file has no `Centro` term "
        "and neither does rules/es.yaml")


def test_the_sample_is_drawn_over_the_previous_rounds_errors(tree, corpus_present):
    """§1.4 is a seeded draw over round *N−1*'s `errors.jsonl`, and the seed is (corpus, N).

    The draw is the caller's and not the assembler's, because an assembler that drew would
    be a second place the seed is applied — DESIGN §11.1's premise is that both arms draw
    through one function. Asserted by reproducing the draw here from the same file: a driver
    that drew at *N−1*, or over the wrong round's errors, gets a different set of spans and
    a prompt that looks right.
    """
    from src.eval.run_fold import errors_path, read_errors
    from src.sample import draw

    run_round_1(tree, Transport(rules_text=GOOD_RULES))
    # Round 2's rule file catches a term round 1's does not, so the two rounds' `errors.jsonl`
    # differ and "which round was drawn over" is answerable. With the same rules every round
    # the two files are byte-identical and a driver drawing over round 1 forever passes.
    run_round(tree, 2, Transport(rules_text=OTHER_RULES))
    run_round(tree, 3)

    axes = dict(corpus=CORPUS, detector="R", supervision="sup-free", porting="port-loop",
                root=tree)
    assert read_errors(iteration=1, **axes) != read_errors(iteration=2, **axes), (
        "rounds 1 and 2 left the same error list, so this test cannot see which one was "
        "drawn over — the two rule files must differ in what they catch")
    # Both rounds, because at round 2 "the previous round" and "round 1" are the same file:
    # a driver that drew over round *N−2*, or over round 1 every time, is only visible from
    # round 3 on — `test_three_consecutive_rounds_chain`'s argument at §1.4.
    for iteration in (2, 3):
        assert errors_path(iteration=iteration - 1, **axes).exists()
        expected = draw(read_errors(iteration=iteration - 1, **axes), CORPUS, iteration)
        line = [c for c in calls()
                if c["role"] == "rule_author" and c["iteration"] == iteration][0]
        shown = line["sample_reference"]["spans"]
        assert [(s["doc_id"], s["span_index"]) for s in shown] == \
            [(e.doc_id, e.span_index) for e in expected], (
            f"round {iteration} was not shown a draw over round {iteration - 1}'s errors")


def test_a_round_reads_the_round_scoped_score_and_not_the_headline_copy(
        tree, corpus_present):
    """The un-iterated `metrics.json` is whichever round ran last (DESIGN §5.5).

    A driver pointed at it answers "which round's score is this" with "the most recent one",
    which is right by accident at round 2 and wrong from round 3 on. Asserted by corrupting
    the headline copy after round 2 and checking round 3 still runs: a driver reading it
    would fail on the corruption, and one reading `iter2/metrics.json` cannot see it.
    """
    run_round_1(tree)
    out2 = run_round(tree, 2)
    out2["metrics_path"].write_text("{}\n", encoding="utf-8")
    out3 = run_round(tree, 3)
    assert out3["outcome"] == SCORED


# ─── the chain refuses a gap, and a format failure is the same mechanism ─────


def test_a_later_round_on_an_arm_that_never_called_is_refused(tree):
    """The complement of the freeze guard, not a second copy of it.

    `freeze_window()` refuses once a call line exists; this refuses a later round *before*
    one, because a round 2 without a round 1 is a round with no predecessor. Neither
    condition implies the other and both are one predicate read from opposite sides.
    """
    with pytest.raises(OrchestrateError, match="has made no call"):
        run_round(tree, 2)


def test_a_round_whose_predecessor_left_no_score_is_refused(tree):
    """The gap check and the format-failure guard are one read.

    `_leak_rates` walks rounds 1..N−1 through `_previous_round`, which refuses a missing
    `metrics.json`. So a skipped round and a round that ended in a format failure — which
    writes `format_failure.json` and deliberately no score — stop the arm by the same
    mechanism, with no flag anywhere remembering which happened.
    """
    a_frozen_arm(tree)
    with pytest.raises(OrchestrateError, match="no score for round 1"):
        run_round(tree, 2)


def test_a_round_after_a_format_failure_is_refused(tree, corpus_present):
    """End to end, because the two halves are in different modules.

    Round 2 answers with unparseable YAML: `format_failure.json` is written, `metrics.json`
    is not, `stop` is True and `termination` is None. Round 3 then refuses on the absent
    score — which is DESIGN §5.5's decision that a format failure ends the arm, enforced by
    the shape of what the failing round wrote rather than by a boolean.
    """
    run_round_1(tree)
    out = run_round(tree, 2, Transport(rules_text=UNPARSEABLE))
    assert out["outcome"] == FORMAT_FAILURE
    assert out["failure_path"].exists()
    assert out["metrics_path"] is None
    assert out["termination"] is None
    assert out["stop"] is True

    with pytest.raises(OrchestrateError, match="no score for round 2"):
        run_round(tree, 3)


def test_a_failed_round_still_reports_the_arms_total(tree, corpus_present):
    """`FAILURE_SCHEMA` 2 has `cost` and no `cost_to_date`, so the driver returns it.

    The gap is recorded in DESIGN §5.5 rather than closed here: adding the key is a schema
    bump. What must hold meanwhile is that the caller is not the one left reconstructing the
    total from two files, so the return value carries it even though the record cannot.
    """
    run_round_1(tree)
    out = run_round(tree, 2, Transport(rules_text=UNPARSEABLE))
    assert out["cost_to_date"]["llm_calls"] >= out["cost"]["llm_calls"]
    written = json.loads(out["failure_path"].read_text(encoding="utf-8"))
    assert "cost" in written


@pytest.mark.parametrize("bad", [1, 0, -1, True, "2", 2.0, None])
def test_round_one_is_not_this_functions_round(tree, bad):
    """`run_iteration()` runs rounds 2 and up, and round 1 is not reachable by argument.

    Round 1 freezes the window and shows §§1.3–1.4 empty, which is what makes it the
    baseline's call (DESIGN §4). `True` is in the list because `isinstance(True, int)` is
    true and `True == 1`, so a bare `>= FIRST_ITERATED` check would accept it as round 1.
    """
    with pytest.raises(OrchestrateError, match="runs rounds"):
        run_round(tree, bad)


# ─── the stopping rule: one call, one writer, one implementation ─────────────


def test_the_rounds_termination_block_is_about_that_round(tree, corpus_present):
    """`iterations` counts the rounds *through this one*, so round 2's block says 2.

    This is what `not_applicable` at round 2 got wrong: it was round 1's record written at a
    round where it was no longer true. A block naming the wrong count is a published claim
    about how far the arm got.
    """
    run_round_1(tree)
    out = run_round(tree, 2)
    assert out["termination"]["iterations"] == 2
    assert out["termination"]["reason"] is None, "two rounds cannot converge at k=2"
    assert out["stop"] is False


def test_the_rate_in_the_block_is_the_rate_in_the_same_files_headline(tree, corpus_present):
    """One scoring pass, which is the property `PendingTermination` exists to keep.

    Scoring twice to get the rate first would give two passes that could differ — a rule
    file edited between them, a non-deterministic detector added later — with *neither file
    looking wrong*, because each would be internally consistent with its own pass. The
    block's last improvement is this round's rate subtracted from the previous round's, so
    the two numbers in one file must agree by arithmetic.
    """
    run_round_1(tree)
    out = run_round(tree, 2)
    written = json.loads(out["metrics_path"].read_text(encoding="utf-8"))
    this_rate = written["headline"]["leak_rate"]["value"]
    previous = round_metrics(tree, 1)["headline"]["leak_rate"]["value"]
    assert written["termination"]["improvements"] == pytest.approx(
        [previous - this_rate])


def test_the_history_the_driver_passes_excludes_this_round(tree, monkeypatch,
                                                           corpus_present):
    """Rounds 1..N−1 and not 1..N, or the round's own rate is counted twice.

    A pre-seeded history makes `improvements` end in a spurious `0.0` — an iteration that
    changed nothing — which is *below δ* and therefore counts toward stopping. So the arm
    converges one round early with every field in the record looking plausible. Captured at
    the boundary because that is where the argument is: what the driver hands over is the
    thing that must not already contain the answer.
    """
    seen = {}
    real = loop.run_fold

    def capture(**kw):
        seen[kw["iteration"]] = kw.get("termination", "absent")
        return real(**kw)

    monkeypatch.setattr(loop, "run_fold", capture)
    run_round_1(tree)
    run_round(tree, 2)

    assert seen[1] == "absent", (
        "round 1 passed a termination argument; a sequence of length one has no first "
        "difference, and `not_applicable` is the writer's to record")
    pending = seen[2]
    assert isinstance(pending, PendingTermination)
    assert pending.previous_leak_rates == (
        round_metrics(tree, 1)["headline"]["leak_rate"]["value"],)


def test_the_driver_reads_the_verdict_back_rather_than_computing_it(tree, monkeypatch,
                                                                    corpus_present):
    """One writer: the block in the file is the block the driver reports.

    A driver that recomputed `should_stop()` after the write would be a second answer to
    "did the arm stop", and the two could disagree while each was internally consistent.
    Asserted by editing the written file before the driver reads it back: a recomputing
    driver ignores the edit and returns its own verdict.
    """
    run_round_1(tree)
    real = loop.run_fold

    def patch_after(**kw):
        spans, metrics, scored = real(**kw)
        written = json.loads(metrics.read_text(encoding="utf-8"))
        written["termination"]["reason"] = CEILING
        metrics.write_text(json.dumps(written), encoding="utf-8")
        return spans, metrics, scored

    monkeypatch.setattr(loop, "run_fold", patch_after)
    out = run_round(tree, 2)
    assert out["termination"]["reason"] == CEILING
    assert out["stop"] is True


def test_a_metrics_file_with_no_termination_block_is_refused(
        tree, monkeypatch, corpus_present):
    """Refused rather than defaulted: the block is required by the scorer, so its absence
    means the file is not the one this round wrote."""
    run_round_1(tree)
    real = loop.run_fold

    def strip_block(**kw):
        spans, metrics, scored = real(**kw)
        written = json.loads(metrics.read_text(encoding="utf-8"))
        del written["termination"]
        metrics.write_text(json.dumps(written), encoding="utf-8")
        return spans, metrics, scored

    monkeypatch.setattr(loop, "run_fold", strip_block)
    with pytest.raises(OrchestrateError, match="no usable termination block"):
        run_round(tree, 2)


def test_the_writer_never_imports_the_stopping_rule(tree):
    """One implementation of a pre-registered decision, as a property of the import list.

    `run_fold` resolves a `PendingTermination` by calling `resolve()` on it, and `resolve()`
    calls `should_stop`. The rule therefore has one home even though the verdict is computed
    inside the writer. A `run_fold` that imported `should_stop` would be a second place §3's
    decision could be made — and it would look like a simplification, since the argument
    would then be a corpus and a list.

    Read from the syntax tree and not from the text: the docstrings in that module name
    `should_stop` on purpose, and a substring search would forbid explaining the boundary in
    order to enforce it.
    """
    module = ast.parse((ROOT / "src" / "eval" / "run_fold.py").read_text(encoding="utf-8"))
    imported = {
        alias.name if isinstance(node, ast.Import) else alias.name
        for node in ast.walk(module)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "PendingTermination" in imported, (
        "the pending type is what the writer resolves; this test is measuring the wrong "
        "module if it is absent")
    assert "should_stop" not in imported, (
        "src/eval/run_fold.py imports should_stop, so the writer can now decide §3's "
        "question itself. The rate travels, not the verdict (DESIGN §3, §5.5)."
    )


def test_the_pending_type_holds_no_verdict_and_cannot_be_edited(tree):
    """Frozen, and `reason` does not exist on it: there is nothing here to adjust.

    `Termination` is frozen because a verdict must not be adjustable by the code that acted
    on it. This type is frozen for that reason and holds strictly less — the corpus and a
    history — so a caller cannot pre-decide the answer and hand it to the writer.
    """
    pending = PendingTermination(corpus=CORPUS, previous_leak_rates=(0.5,))
    with pytest.raises((AttributeError, TypeError)):
        pending.previous_leak_rates = (0.4,)      # type: ignore[misc]
    assert not hasattr(pending, "reason")
    assert isinstance(pending.resolve(0.4), Termination)


def test_resolve_is_should_stop_over_the_history_plus_the_rate(tree):
    """The whole of the type's behaviour, pinned against the rule it delegates to.

    Any threshold, branch or test added inside `resolve()` would be a second implementation
    of §3's rule reachable only through the writer — the arrangement this type was built to
    avoid rather than a shortcut it enables.
    """
    rates = (0.60, 0.55, 0.54)
    pending = PendingTermination(corpus=CORPUS, previous_leak_rates=rates)
    assert pending.resolve(0.539).record() == should_stop(CORPUS, [*rates, 0.539]).record()


# ─── the arm obeys the verdict ───────────────────────────────────────────────


def a_history(tree, rates):
    """Seed rounds 1..len(rates) with the given dev leak rates and their rule files.

    The scores are edited into files a real round wrote, so every other field in them is a
    real one — a hand-built `metrics.json` would let a driver that read some other key pass.
    """
    freeze_window(*ARM, sections=loop.ONESHOT_SECTIONS)
    path = log_path(*ARM)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"call_id": "c1", "iteration": 1,
                             "role": "rule_author"}) + "\n")
    from src.eval.run_fold import run_fold

    rules_file = None
    for i, rate in enumerate(rates, 1):
        rules_file = arm_rules_path(corpus=CORPUS, detector="R", supervision="sup-free",
                                    porting="port-loop", iteration=i, lang=LANG, root=tree)
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        rules_file.write_text(GOOD_RULES, encoding="utf-8")
        run_fold(corpus=CORPUS, detector="R", supervision="sup-free", porting="port-loop",
                 rules={LANG: rules_file}, root=tree, iteration=i)
        target = iter_metrics_path(corpus=CORPUS, detector="R", supervision="sup-free",
                                   porting="port-loop", iteration=i, root=tree)
        written = json.loads(target.read_text(encoding="utf-8"))
        written["headline"]["leak_rate"]["value"] = rate
        target.write_text(json.dumps(written), encoding="utf-8")
    return rules_file


def test_a_converged_arm_refuses_the_next_round(tree, corpus_present):
    """k consecutive below-δ rounds, then no round *k + 2*.

    Obeying the pre-registered rule rather than warning about it: a round past the stop is a
    round whose own termination block the rule cannot evaluate, because `should_stop` raises
    above the ceiling and the sequence would be longer than the run the pre-registration
    covers.
    """
    delta = should_stop(CORPUS, [0.5, 0.5]).delta
    thin = delta / 10
    a_history(tree, [0.50, 0.50 - thin, 0.50 - 2 * thin])
    assert should_stop(CORPUS, [0.50, 0.50 - thin, 0.50 - 2 * thin]).reason == CONVERGED
    with pytest.raises(OrchestrateError, match="the arm stopped at round 3"):
        run_round(tree, 4)


def test_a_ceiling_stop_refuses_the_next_round_and_is_not_convergence(tree, corpus_present):
    """§3's prohibition, on the arm rather than on the verdict.

    An arm that reached the cap while still improving is `ceiling`, and the round after it
    does not run. `tests/test_termination.py` owns the arithmetic; what this adds is that
    the two endings are distinguishable in the driver's refusal, so a reader of a stopped
    arm can tell a budget exhaustion from a converged loop.
    """
    ceiling = termination_params()["ceiling"]
    steep = [0.90 - 0.05 * i for i in range(ceiling)]
    verdict = should_stop(CORPUS, steep)
    assert verdict.reason == CEILING and not verdict.converged
    a_history(tree, steep)
    with pytest.raises(OrchestrateError, match="'ceiling'"):
        run_round(tree, ceiling + 1)


def test_the_ceiling_reason_survives_into_the_published_file(tree, corpus_present):
    """The reason a run ended is in `metrics.json`, not only in a return value.

    Written for the ceiling case specifically, because that is the one §3 forbids
    mislabelling: `converged` is a property derived from `reason`, so a file saying
    `ceiling` cannot also claim convergence — not because a validator rejects it, but
    because the state cannot be constructed.
    """
    ceiling = termination_params()["ceiling"]
    steep = [0.90 - 0.05 * i for i in range(ceiling - 1)]
    a_history(tree, steep)
    out = run_round(tree, ceiling)
    assert out["termination"]["reason"] == CEILING
    assert out["termination"]["converged"] is False
    assert out["stop"] is True
    written = round_metrics(tree, ceiling)["termination"]
    assert written["reason"] == CEILING and written["converged"] is False


def test_running_past_the_ceiling_is_refused_by_the_rule_itself(tree, corpus_present):
    """The last line of defence, and it is not this module's.

    Even if a driver ignored `stop`, `should_stop` raises on a sequence longer than the cap:
    an arm past the ceiling has already violated §3, and reporting a reason for it would
    describe a run the pre-registration does not cover.
    """
    ceiling = termination_params()["ceiling"]
    with pytest.raises(TerminationError, match="ceiling"):
        should_stop(CORPUS, [0.5 - 0.001 * i for i in range(ceiling + 1)])


# ─── the round's cost is 1 + N, and the driver does not add it up ────────────


def test_the_rounds_cost_is_the_rule_author_and_every_auditor_call(tree, corpus_present):
    """`llm_calls` = 1 + N, which is what the round cost (DESIGN §5.5, schema 7).

    A round that priced only the RuleAuthor would make the Auditor free, and the ladder's
    cost comparison is against the total — an arm whose improvement looks free is the
    failure CLAUDE.md's cost-beside-quality rule is for.
    """
    from src.eval.run_fold import load_fold

    n_docs = len(load_fold(CORPUS, "dev"))
    run_round_1(tree)
    out = run_round(tree, 2)
    assert out["cost"]["llm_calls"] == n_docs + 1


def test_the_arm_total_grows_by_the_round_and_the_driver_holds_the_accumulator(
        tree, corpus_present):
    """`cost_to_date` = the previous round's total plus this round's, added by the scorer.

    The driver holds the accumulator and `scorer.sum_costs` does the arithmetic, because a
    rung whose driver both decides how many calls to make and computes its own total is a
    rung pricing itself. `run_fold` never accumulates either, which is its own test.
    """
    run_round_1(tree)
    out2 = run_round(tree, 2)
    out3 = run_round(tree, 3)
    assert out3["cost_to_date"]["llm_calls"] == \
        out2["cost_to_date"]["llm_calls"] + out3["cost"]["llm_calls"]
    written = round_metrics(tree, 3)
    assert written["cost"]["llm_calls"] == out3["cost"]["llm_calls"]
    assert written["cost_to_date"]["llm_calls"] == out3["cost_to_date"]["llm_calls"]


def test_every_call_is_logged_with_its_round_and_its_role(tree, corpus_present):
    """Two agents, one `agent_calls.jsonl`, and `role` is passed rather than derived.

    `llm_calls` sums the lines, so a role that came from the template filename or from which
    assembler produced the prompt would split one agent's calls across two spellings the
    moment either changed (DESIGN §5.5, and §3's layer-from-detector-name prohibition one
    field over). Asserted as the multiset of (round, role) pairs.
    """
    from src.eval.run_fold import load_fold

    n_docs = len(load_fold(CORPUS, "dev"))
    run_round_1(tree)
    run_round(tree, 2)
    seen = [(c["iteration"], c["role"]) for c in calls()]
    assert seen.count((1, "rule_author")) == 1
    assert seen.count((2, "auditor")) == n_docs
    assert seen.count((2, "rule_author")) == 1
    assert seen[-1] == (2, "rule_author"), "the RuleAuthor's line is the round's last"


# ─── the Auditor's calls are cached and the RuleAuthor's is not (§5.4, §11.3) ──
# N is the reason: the round sends `auditor.md` plus the banner plus §1.1's frame — 80.7% of
# an average audit call, measured 2026-08-16 — once per dev document, and once for the
# RuleAuthor. One template repeated N times is a cache; one prompt sent once is not. What is
# checked here is that `cache=True` reaches only the N, and that the round's block reports the
# one write and the N−1 reads rather than a saving.


def cache_points(call: dict) -> int:
    return sum(1 for b in call["messages"][0]["content"] if "cachePoint" in b)


def test_only_the_auditors_calls_carry_a_cache_point(tree, corpus_present):
    """**The RuleAuthor's prompt is sent whole**, and that is a decision rather than an omission.

    Its §1.2 is the previous round's rule file and its §1.3 is that round's audit report, so
    the prefix that would be retained changes every round and the write would never be read.
    The mutation that splits it too is in `tests/mutations/run.py`; this is the assertion that
    kills it.
    """
    run_round_1(tree)
    fake = Transport()
    run_round(tree, 2, fake)
    audits = [c for c in fake.sent if "Auditor prompt" in sent_text(c)]
    authors = [c for c in fake.sent if "Auditor prompt" not in sent_text(c)]
    assert len(authors) == 1
    assert all(cache_points(c) == 1 for c in audits)
    assert cache_points(authors[0]) == 0


def test_round_one_makes_no_cached_call_and_writes_no_caching_block(tree, corpus_present):
    """Round 1 is one RuleAuthor call and no audit (`port-oneshot`'s procedure, §5.5).

    So the block is absent, and the absence is the record: schema 8 makes it optional precisely
    so that "this round did not cache" and "cached and never hit" stay different statements.
    """
    fake = Transport()
    out = run_round_1(tree, fake)
    assert all(cache_points(c) == 0 for c in fake.sent)
    assert out.get("caching") is None
    assert "caching" not in round_metrics(tree, 1)


def test_the_rounds_caching_block_is_one_write_and_the_rest_reads(tree, corpus_present):
    """The 5m TTL's model, as the round's own record.

    Intra-round gaps are seconds and inter-round gaps are 40–80 minutes, so one write per round
    and reads for the remaining N−1 is what a 5m lifetime buys. The fake serves a write on
    every call, so what this can assert is the shape: the block exists, it names the one
    declared boundary and TTL, and its counts are sums over the audit calls only.
    """
    from src.eval.run_fold import load_fold

    n_docs = len(load_fold(CORPUS, "dev"))
    run_round_1(tree)
    out = run_round(tree, 2)
    block = out["caching"]
    assert set(block) == {"enabled", "boundary", "ttl", "read_tokens", "write_tokens"}
    assert block["enabled"] is True
    assert (block["boundary"], block["ttl"]) == ("after_audit_frame", "5m")
    written = round_metrics(tree, 2)
    assert written["caching"] == block
    # The RuleAuthor's call contributes nothing: N documents' worth of writes, not N+1.
    assert block["write_tokens"] % n_docs == 0


def test_the_rounds_cost_still_carries_the_raw_total_beside_the_block(tree, corpus_present):
    """§11.3: both numbers in one file, and the headline is the one that did not shrink.

    A round whose `prompt_tokens` fell because a service served the same bytes from its own
    memory would read, in the column the 1.9× standard is judged on, as a round that did less
    work. It did not. So the cost block is unchanged by caching and the reads are published
    beside it, where a reader who wants the billed basis can subtract.
    """
    run_round_1(tree)
    out1 = run_round(tree, 2)
    written = round_metrics(tree, 2)
    assert written["cost"]["prompt_tokens"] == out1["cost"]["prompt_tokens"]
    assert written["cost"]["prompt_tokens"] > written["caching"]["read_tokens"]
    assert set(written["cost"]) == {"llm_calls", "prompt_tokens", "completion_tokens",
                                    "wall_seconds"}


def test_cache_true_appears_once_in_the_project_and_it_is_the_audit_call(tree):
    """Structural, because "only the Auditor's calls" is not a property of any single round.

    A second `invoke(..., cache=True)` added anywhere — a rung, a probe, a helper — would be a
    prompt this project never measured a boundary for, and the behavioural test above would
    still pass. Read off the syntax tree of every module that calls `invoke`.
    """
    sites = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree_ = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree_):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", None)
            if name != "invoke":
                continue
            for keyword in node.keywords:
                if keyword.arg == "cache":
                    sites.append((path.relative_to(ROOT).as_posix(), node.lineno))
    assert [p for p, _ in sites] == ["src/porting/loop.py"], (
        "`cache=True` is passed at exactly one call site (DESIGN §5.4): the Auditor's, whose "
        f"prompt is the only one with a declared boundary. Found {sites}")


def test_a_format_failure_in_a_cached_round_still_records_what_was_retained(
        tree, corpus_present):
    """`format_failure.json` is written *instead of* `metrics.json`, so it carries the block too.

    The round still made N audit calls and a third party still held those bytes; a failure file
    with a cost block and no caching block would leave that unrecorded for exactly the rounds
    that went wrong.
    """
    run_round_1(tree)
    out = run_round(tree, 2, Transport(rules_text=UNPARSEABLE))
    assert out["outcome"] == FORMAT_FAILURE
    written = json.loads(out["failure_path"].read_text(encoding="utf-8"))
    assert written["caching"]["boundary"] == "after_audit_frame"
    assert written["caching"]["enabled"] is True


# ─── one procedure for every later round ────────────────────────────────────


def test_there_is_no_per_round_function(tree):
    """Rounds 2, 3 and 8 are one body, because they are one procedure.

    A `run_iteration_3()` would be a second copy whose drift from the first is undetectable:
    the two would assemble prompts under the same `porting` value and every result would
    still look right. What differs between rounds is the round number and the length of the
    history, and both are arguments or read from disk.
    """
    names = {n.name for n in ast.walk(ast.parse(
        (ROOT / "src" / "porting" / "loop.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef)}
    assert "run_iteration" in names and "run_iteration_1" in names
    assert not [n for n in names if n.startswith("run_iteration_")
                and n != "run_iteration_1"], (
        "a second per-round function appeared. Round 1 is its own because it freezes the "
        "window; every round after it is `run_iteration(iteration, ...)`.")


def test_three_consecutive_rounds_chain(tree, corpus_present):
    """The loop, run. Each round's inputs are the previous round's outputs, three deep.

    Round 3 is where an off-by-one that is invisible at round 2 shows up: `previous` is
    `iteration - 1` computed once, so a driver that hard-coded 1 anywhere would audit round
    1's predictions at round 3 and draw over round 1's errors, and the arm would still
    produce three well-formed rounds.
    """
    run_round_1(tree)
    out2 = run_round(tree, 2)
    out3 = run_round(tree, 3)
    assert out2["iteration"] == 2 and out3["iteration"] == 3
    assert out3["termination"]["iterations"] == 3
    report3 = json.loads(out3["audit_report_path"].read_text(encoding="utf-8"))
    assert report3["masked_from_iteration"] == 2
    assert "iter3" in out3["rules_path"].as_posix()


def test_a_later_round_does_not_freeze_and_reports_drift_instead(tree, corpus_present):
    """The freeze is once per arm, and round 2 is the first round that can report drift.

    `port-oneshot` has one call and no "mid" to drift in. `window_drift()` reports rather
    than refuses — an edit to the prompt's prose and a change to `n` are different events
    and only a person can tell them apart — so the list is on the return value.
    """
    run_round_1(tree)
    revision = json.loads(freeze_path(*ARM).read_text(encoding="utf-8"))["revision"]
    (tree / "config" / "sampling.yaml").write_text(
        (ROOT / "config" / "sampling.yaml").read_text(encoding="utf-8")
        + "\n# an edit after the freeze\n", encoding="utf-8")
    out = run_round(tree, 2)
    assert out["window_drift"] == ["sampling_sha256"]
    assert json.loads(freeze_path(*ARM).read_text(encoding="utf-8"))["revision"] == \
        revision, "a later round re-froze the window"


@pytest.mark.parametrize("bad_kw", [
    dict(model_id=""),
    dict(model_id=None),
    dict(lang="fr"),
])
def test_a_round_that_cannot_produce_a_scored_file_is_refused_before_anything_runs(
        tree, bad_kw):
    """Both conditions checked before any call, so a refusal leaves no record behind.

    A bad `lang` discovered after the call would have authored a rule file no corpus loads,
    with the arm's `revision` as the only trace.
    """
    a_frozen_arm(tree)
    kw = {**ARM_KW, "model_id": MODEL, **bad_kw}
    with pytest.raises(OrchestrateError):
        loop.run_iteration(2, **kw, client=Transport())
    assert calls() == [{"call_id": "c1", "iteration": 1, "role": "rule_author"}]


# ─── a round re-run: draws, and the spend that bought nothing (§5.5.2) ───────
# An incomplete round may be re-attempted, because refusing the re-run lets a transport timeout
# end the arm — round 5 of the real `port-loop` died twice on the RuleAuthor call after all 250
# audit calls had completed. Each attempt audits the fold again, so one round can hold 250 × M
# Auditor lines in the log against one canonical report, and the two things these tests are about
# are that no attempt's report is lost and that no attempt's spend is either.


def draw_report(tree, iteration: int, draw: int) -> dict:
    from src.porting.audit import draw_path
    return json.loads(draw_path(corpus=CORPUS, detector="R", supervision="sup-free",
                                porting="port-loop", iteration=iteration, draw=draw,
                                root=tree).read_text(encoding="utf-8"))


def test_a_round_audited_once_is_draw_one_and_abandons_nothing(tree, corpus_present):
    """The state of every round in the record before this existed, and the default is the fact.

    `draw_index` is written unconditionally because it is always knowable; `abandoned_spend` is
    absent because absence is how "nothing was abandoned" is recorded (schema 9). A zero block
    would say something different and something false — an abandoned attempt that spent nothing
    cannot happen.
    """
    run_round_1(tree)
    out = run_round(tree, 2)
    assert out["draw_index"] == 1
    assert out["abandoned_spend"] is None
    assert "abandoned_spend" not in round_metrics(tree, 2)
    assert json.loads(out["audit_report_path"].read_text(encoding="utf-8"))["draw_index"] == 1
    assert draw_report(tree, 2, 1)["draw_index"] == 1


def test_a_re_run_round_does_not_overwrite_the_earlier_draws_report(tree, corpus_present):
    """**The failure this path exists to prevent.** Before it, attempt 2 wrote over attempt 1's
    report and `agent_calls.jsonl` and `audit_report.json` then disagreed about how many times
    the round was audited — with the log being the one that was right.

    Both preserved reports are compared for content, not just existence: a second draw writing an
    identical file would satisfy an existence check while still having lost attempt 1's record.

    **This test also exhibits the hole DESIGN §5.5.2 refused to close.** The round it re-runs here
    *scored*, and the driver permits that: nothing in `run_iteration` looks at whether round *N*
    already has a `metrics.json`, so a second draw taken in knowledge of the first is mechanically
    available and is §6's prohibition. What this change bought is that the act now leaves `draw2/`,
    a `draw_index: 2` and a non-zero `attempts_abandoned` behind it — detectability, not
    prevention. Nothing here should be read as endorsing the re-run of a scored round.
    """
    run_round_1(tree)
    first = run_round(tree, 2, Transport(audit_text=NO_FLAGS))
    second = run_round(tree, 2, Transport(audit_text=NO_FLAGS))
    assert (first["draw_index"], second["draw_index"]) == (1, 2)
    assert first["audit_draw_path"] != second["audit_draw_path"]
    assert draw_report(tree, 2, 1)["draw_index"] == 1
    assert draw_report(tree, 2, 2)["draw_index"] == 2
    assert first["audit_report_path"] == second["audit_report_path"]


def test_the_canonical_copy_carries_the_total_and_a_preserved_draw_does_not(tree,
                                                                           corpus_present):
    """Presence of `draws_total` means "this is the latest draw", which is a readable fact
    rather than a convention. It is absent from the preserved copies because at the moment
    draw 2 is written nobody knows whether there will be a draw 3, and writing `2` there would
    publish a false count in every round that turns out to have more.
    """
    run_round_1(tree)
    run_round(tree, 2)
    out = run_round(tree, 2)
    canonical = json.loads(out["audit_report_path"].read_text(encoding="utf-8"))
    assert canonical["draw_index"] == 2 and canonical["draws_total"] == 2
    assert "draws_total" not in draw_report(tree, 2, 1)
    assert "draws_total" not in draw_report(tree, 2, 2)
    # Two independent counts of the same thing, which is the point of keeping both.
    round_dir = out["audit_draw_path"].parent.parent
    assert canonical["draws_total"] == sum(
        1 for child in round_dir.iterdir() if child.name.startswith("draw"))


def test_the_re_run_totals_the_abandoned_attempts_from_the_log(tree, corpus_present):
    """Measured from `agent_calls.jsonl`, which is the only place the number exists.

    Nothing is inferred from the draw count or the fold size: the lines already present for this
    round when the attempt begins are read one at a time. The first attempt here made 250 audit
    calls and one RuleAuthor call, so 251 is the count that must appear — and `calls_unmeasured`
    is 0 because that attempt's RuleAuthor call did return and did log its usage.
    """
    from src.eval.run_fold import load_fold

    n_docs = len(load_fold(CORPUS, "dev"))
    run_round_1(tree)
    first = run_round(tree, 2)
    second = run_round(tree, 2)
    block = second["abandoned_spend"]
    assert block["attempts_abandoned"] == 1
    assert block["calls_abandoned"] == n_docs + 1 == first["cost"]["llm_calls"]
    assert block["prompt_tokens_abandoned"] == first["cost"]["prompt_tokens"]
    assert block["calls_unmeasured"] == 0
    assert round_metrics(tree, 2)["abandoned_spend"] == block


def test_the_abandoned_spend_is_not_summed_into_what_the_arm_published(tree, corpus_present):
    """**The whole reason the block is named the way it is.** This is spend that bought no
    result, and adding it to the figure DESIGN §11.3's 1.9× standard is read off would price the
    rung for work that produced nothing while making it look more productive per token.

    So the re-run's `cost` is the successful attempt's own, and every key of the abandoned block
    is suffixed such that `sum_costs` would reject it outright.
    """
    from src.eval.scorer import REQUIRED_COST, sum_costs

    run_round_1(tree)
    first = run_round(tree, 2)
    second = run_round(tree, 2)
    assert second["cost"]["llm_calls"] == first["cost"]["llm_calls"]
    assert set(second["cost"]) == set(REQUIRED_COST)
    assert not set(second["abandoned_spend"]) & set(REQUIRED_COST)
    with pytest.raises(Exception):
        sum_costs([second["cost"], second["abandoned_spend"]])


def test_an_attempt_that_died_on_the_rule_author_call_is_counted_as_unmeasured(tree):
    """**Where the honesty is.** A call that never returned wrote no log line and reported no
    usage, so its tokens are gone for good — DESIGN §3's HTTP-attempt limitation as a hole in a
    different record. What can still be counted is how many attempts hit it: a draw written with
    no RuleAuthor line for the round is an attempt that died on that call.

    So the four totals beside `calls_unmeasured` are lower bounds whenever it is above zero, and
    this is the case that makes them so.
    """
    a_frozen_arm(tree)
    path = log_path(*ARM)
    with open(path, "a", encoding="utf-8") as fh:
        for n in range(3):
            fh.write(json.dumps({
                "call_id": f"a{n}", "iteration": 4, "role": "auditor",
                "cost": {"llm_calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                         "wall_seconds": 1.5}}) + "\n")
    block = loop._abandoned_spend(*ARM, iteration=4, draws_before=1)
    assert block["attempts_abandoned"] == 1
    assert block["calls_abandoned"] == 3
    assert block["prompt_tokens_abandoned"] == 300
    assert block["wall_seconds_abandoned"] == 4.5
    assert block["calls_unmeasured"] == 1


def test_the_abandoned_spend_counts_only_this_rounds_lines(tree):
    """Every line carries the round it belonged to, and an attempt at round 4 must not be
    charged for round 3's calls — which would make the abandoned figure grow with the arm's
    length rather than with what was abandoned.
    """
    a_frozen_arm(tree)
    path = log_path(*ARM)
    with open(path, "a", encoding="utf-8") as fh:
        for iteration in (3, 4):
            fh.write(json.dumps({
                "call_id": f"c{iteration}", "iteration": iteration, "role": "auditor",
                "cost": {"llm_calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                         "wall_seconds": 1.0}}) + "\n")
    assert loop._abandoned_spend(*ARM, iteration=4,
                                draws_before=1)["prompt_tokens_abandoned"] == 100


def test_the_first_attempt_of_a_round_reads_no_block_at_all(tree):
    """`None`, not a block of zeros, and not an error on an arm with no log yet.

    "First attempt" means **both** records are empty — no draw and no logged call. The two are
    checked as a union rather than the draw count alone, for the reason the next test gives.
    """
    a_frozen_arm(tree)
    assert loop._abandoned_spend(*ARM, iteration=2, draws_before=0) is None


def test_an_attempt_that_died_before_writing_a_draw_is_still_counted(tree):
    """**Round 6's regression, and the case that showed the draw count is the wrong gate.**

    Round 6's first attempt took a Bedrock 500 on auditor call 123 of 250. The audit never
    finished, so no `draw1/` directory was written and `next_draw` correctly returned 1 — but 122
    calls had been paid for and logged. Gating the block on `draws_before < 1` returned `None` for
    that round, which under the absent-means-unrecorded convention publishes "nothing was
    abandoned" and is exactly the silent disappearance the block exists to prevent.

    So a logged line is sufficient on its own. `attempts_abandoned` reads 1 here even though no
    draw exists, and `calls_unmeasured` reads 1 because the attempt logged no RuleAuthor line —
    the call it died on is one whose tokens were spent and never reported.
    """
    a_frozen_arm(tree)
    with open(log_path(*ARM), "a", encoding="utf-8") as fh:
        for n in range(122):
            fh.write(json.dumps({
                "call_id": f"d{n}", "iteration": 6, "role": "auditor",
                "cost": {"llm_calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                         "wall_seconds": 2.0}}) + "\n")
    block = loop._abandoned_spend(*ARM, iteration=6, draws_before=0)
    assert block is not None, (
        "a round with 122 paid calls and no preserved draw reported nothing abandoned")
    assert block["attempts_abandoned"] == 1
    assert block["calls_abandoned"] == 122
    assert block["prompt_tokens_abandoned"] == 12200
    assert block["wall_seconds_abandoned"] == 244.0
    assert block["calls_unmeasured"] == 1


def test_the_attempt_count_is_a_lower_bound_and_the_call_count_is_not(tree):
    """The two figures are sourced differently and only one of them can undercount.

    `calls_abandoned` sums the lines themselves, so it is exact for everything that was logged.
    `attempts_abandoned` is `max(draws_before, 1 if any line else 0)`, so two attempts that both
    died mid-audit — leaving lines but no draws — still read 1: nothing in the log marks where one
    attempt ended and the next began. Dividing lines by the fan-out would recover the number only
    by assuming the fan-out never changed, trading a visible lower bound for an invisible guess.

    This test pins the undercount deliberately. It is a documented limit, not a bug to be fixed by
    making the number up, and the exact count stays available in the two public records.
    """
    a_frozen_arm(tree)
    with open(log_path(*ARM), "a", encoding="utf-8") as fh:
        for n in range(30):  # two dead attempts' worth, no draw from either
            fh.write(json.dumps({
                "call_id": f"e{n}", "iteration": 6, "role": "auditor",
                "cost": {"llm_calls": 1, "prompt_tokens": 10, "completion_tokens": 1,
                         "wall_seconds": 0.5}}) + "\n")
    block = loop._abandoned_spend(*ARM, iteration=6, draws_before=0)
    assert block["calls_abandoned"] == 30, "the exact figure is exact"
    assert block["attempts_abandoned"] == 1, "the lower bound is a lower bound"


def test_a_preserved_draw_outranks_the_logged_lines_in_the_attempt_count(tree):
    """When both records speak, the larger is taken. Two preserved draws are two attempts even if
    the second died early enough to log almost nothing, and the reverse — lines but no draw — is
    covered above. `max` of the two is the only combination that never reads below what either
    record alone establishes.
    """
    a_frozen_arm(tree)
    with open(log_path(*ARM), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "call_id": "f0", "iteration": 6, "role": "auditor",
            "cost": {"llm_calls": 1, "prompt_tokens": 10, "completion_tokens": 1,
                     "wall_seconds": 0.5}}) + "\n")
    block = loop._abandoned_spend(*ARM, iteration=6, draws_before=2)
    assert block["attempts_abandoned"] == 2
    assert block["calls_abandoned"] == 1
    assert block["calls_unmeasured"] == 2


def test_the_draw_number_is_taken_before_the_audit_calls_are_made(tree, corpus_present):
    """Ordering, and it is load-bearing twice over: the number goes into the report those calls
    produce, and the abandoned spend has to be totalled from the log *as it stands now* —
    after this attempt logs its own 250 lines the two attempts are no longer separable.
    """
    run_round_1(tree)
    run_round(tree, 2)
    fake = Transport()
    out = run_round(tree, 2, fake)
    n_before = out["abandoned_spend"]["calls_abandoned"]
    assert n_before == fake.audit_calls + 1, (
        "the abandoned total absorbed this attempt's own calls, which means the log was read "
        "after the audit ran rather than before")
    assert draw_report(tree, 2, 2)["draw_index"] == 2
