"""DESIGN §5.6 reaches the loop driver: one call per declared language, every round.

**Outside `tests/mutations/run.py`'s `TEST_FILES` on purpose.** v24d adds no mutation and
changes no kill count's denominator (`experiments/loop4_v24d/registration.yaml`). The
monolingual behaviour of `src/porting/loop.py` stays covered by `tests/test_loop.py`, which
is a member and ran unchanged against this change.

**Why `es-meddocan` declared bilingual, and not `es-carmen`.** `tests/test_orchestrate.py`'s
`two_langs` fixture gives the reason and it holds here too: what §5.6 fixes is the driver's
structure — N calls, N files, one window, the same error sample to every language, one
scoring run — and that structure is the same over any corpus. The patch is at `naming()` so
that every module asking `corpus_rule_langs` gets the same answer.

No corpus text, surface or document id appears here.
"""
from __future__ import annotations

import json

import pytest

from test_loop import (  # noqa: F401  (fixtures are imported by name for pytest)
    ARM, GOOD_RULES, MODEL, UNPARSEABLE, Transport, calls, no_control_plane, tree,
)
from src.orchestrate import FORMAT_FAILURE, SCORED, OrchestrateError, freeze_path
from src.porting import loop
from src.rules import arm_rules_path

CORPUS = ARM[0]

CAT_RULES = """
version: 1
lang: cat
rules:
  - rule_id: probe_servei
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["Servei Zzyzx"]
"""


@pytest.fixture
def two_langs(monkeypatch):
    from src.corpora import base

    real = base.naming()
    doubled = {**real, "corpus_rule_langs": {**real["corpus_rule_langs"],
                                             CORPUS: ["es", "cat"]}}
    monkeypatch.setattr(base, "naming", lambda: doubled)
    assert base.rule_langs(CORPUS) == ["es", "cat"]
    return ["es", "cat"]


class InOrder(Transport):
    """Answers the RuleAuthor's calls from a list, in call order; the Auditor as before.

    In order and not by inspecting the prompt for its language, because order is the thing
    §5.6 declares: the `es` call comes first because naming.yaml lists it first.
    """

    def __init__(self, replies: list[str]):
        super().__init__()
        self.replies = list(replies)

    def converse(self, **kwargs):
        from test_bedrock import reply
        from test_loop import sent_text

        self.sent.append(kwargs)
        is_audit = "Auditor prompt" in sent_text(kwargs)
        self.blocks.append("audit" if is_audit else "rules")
        if is_audit:
            return reply(self.audit_text)
        return reply(self.replies.pop(0))


def round_1(langs, client):
    return loop.run_iteration_1(corpus=CORPUS, lang=langs, model_id=MODEL, client=client)


# ─── round 1 ────────────────────────────────────────────────────────────────


def test_round_one_makes_one_call_per_language_and_scores_once(tree, two_langs,
                                                               corpus_present):
    fake = InOrder([GOOD_RULES, CAT_RULES])
    out = round_1(two_langs, fake)
    assert out["outcome"] == SCORED
    assert fake.rule_author_calls == 2
    assert set(out["rules_paths"]) == {"es", "cat"}
    for lang, path in out["rules_paths"].items():
        assert path == arm_rules_path(corpus=CORPUS, detector="R", supervision="sup-free",
                                      porting="port-loop", iteration=1, lang=lang,
                                      root=tree)
    # One scoring run over both files: the arm's record names both, and its cost is both.
    record = json.loads(out["metrics_path"].read_text(encoding="utf-8"))
    assert set(record["run"]["rules_source"]) == {"es", "cat"}
    assert record["cost"]["llm_calls"] == 2
    # And two call lines, in declared order, each priced as its own call.
    lines = [c for c in calls() if c.get("role") == "rule_author"]
    assert len(lines) == 2
    assert all(c["cost"]["llm_calls"] == 1 for c in lines)


def test_one_window_for_both_languages(tree, two_langs, corpus_present):
    round_1(two_langs, InOrder([GOOD_RULES, CAT_RULES]))
    record = json.loads(freeze_path(*ARM).read_text(encoding="utf-8"))
    assert record["revision"] == 1, "the window is the arm's, frozen once (§5.6 point 1)"


def test_a_subset_of_the_languages_is_refused_before_anything_runs(tree, two_langs):
    fake = InOrder([GOOD_RULES])
    with pytest.raises(OrchestrateError, match="declared rule languages|declares rule"):
        round_1("es", fake)
    assert fake.rule_author_calls == 0
    assert not freeze_path(*ARM).exists(), "refused before the freeze, so nothing is spent"


def test_the_declared_order_is_required(tree, two_langs):
    fake = InOrder([CAT_RULES, GOOD_RULES])
    with pytest.raises(OrchestrateError, match="declared order"):
        round_1(["cat", "es"], fake)
    assert fake.rule_author_calls == 0


def test_a_failed_first_language_ends_the_round_without_calling_the_second(
        tree, two_langs, corpus_present):
    """§5.6: the round is atomic, so the second call could only be paid for."""
    fake = InOrder([UNPARSEABLE, CAT_RULES])
    out = round_1(two_langs, fake)
    assert out["outcome"] == FORMAT_FAILURE
    assert out["failed_lang"] == "es"
    assert fake.rule_author_calls == 1
    assert out["metrics_path"] is None


def test_a_failed_second_language_records_both_calls_spend(tree, two_langs,
                                                            corpus_present):
    fake = InOrder([GOOD_RULES, UNPARSEABLE])
    out = round_1(two_langs, fake)
    assert out["outcome"] == FORMAT_FAILURE
    assert out["failed_lang"] == "cat"
    assert fake.rule_author_calls == 2
    assert out["cost"]["llm_calls"] == 2, "the failure record carries every call made"
    failure = json.loads(out["failure_path"].read_text(encoding="utf-8"))
    assert failure["cost"]["llm_calls"] == 2


# ─── later rounds ───────────────────────────────────────────────────────────


def test_round_two_shows_both_languages_the_same_sample_and_their_own_previous_file(
        tree, two_langs, corpus_present, monkeypatch):
    """§5.6 point 1 for the feedback rounds.

    The noaudit arm is used so that the round makes RuleAuthor calls only — the property
    under test is what those calls were shown, and an Auditor's 250 calls would only slow it.
    """
    noaudit = ("port-loop-noaudit-current")
    kw = dict(corpus=CORPUS, model_id=MODEL, porting=noaudit)
    assert loop.run_iteration_1(lang=two_langs, client=InOrder([GOOD_RULES, CAT_RULES]),
                                **kw)["outcome"] == SCORED

    seen = []
    real = loop.assemble_iteration_prompt

    def spy(**kwargs):
        seen.append((kwargs["lang"], kwargs["rules_path"], tuple(
            (e.doc_id, e.start, e.end) if hasattr(e, "doc_id") else repr(e)
            for e in kwargs["errors"])))
        return real(**kwargs)

    monkeypatch.setattr(loop, "assemble_iteration_prompt", spy)
    fake = InOrder([GOOD_RULES, CAT_RULES])
    out = loop.run_iteration(2, lang=two_langs, client=fake, **kw)
    assert out["outcome"] == SCORED
    assert fake.rule_author_calls == 2 and fake.audit_calls == 0
    assert [lang for lang, _, _ in seen] == ["es", "cat"]
    assert seen[0][2] == seen[1][2], "one window: the same error sample to both languages"
    assert seen[0][1] != seen[1][1]
    for lang, rules_path, _ in seen:
        assert rules_path == arm_rules_path(corpus=CORPUS, detector="R",
                                            supervision="sup-free", porting=noaudit,
                                            iteration=1, lang=lang, root=tree)
    lines = [c for c in calls(CORPUS, "R", "sup-free", noaudit)
             if c.get("iteration") == 2]
    assert len(lines) == 2
    assert lines[0]["sample_reference"] == lines[1]["sample_reference"]
    record = json.loads(out["metrics_path"].read_text(encoding="utf-8"))
    assert record["cost"]["llm_calls"] == 2


def test_a_single_language_corpus_still_passes_a_string_and_gets_one_call(
        tree, corpus_present):
    """The reduction §5.6 promises: for N = 1, what ran before."""
    fake = InOrder([GOOD_RULES])
    out = loop.run_iteration_1(corpus=CORPUS, lang="es", model_id=MODEL, client=fake)
    assert out["outcome"] == SCORED
    assert fake.rule_author_calls == 1
    assert out["rules_path"] == out["rules_paths"]["es"]
    record = json.loads(out["metrics_path"].read_text(encoding="utf-8"))
    assert record["cost"]["llm_calls"] == 1
