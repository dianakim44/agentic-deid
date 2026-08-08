"""Tests for src/eval/run_fold.py — the detection execution path.

This is what closes an arm. Rules could be written and counted before it existed, but a
dev-wide score — the quantity every comparison in DESIGN §4 is made of — had nowhere to
come from, so no arm could be reported.

What has to hold:

**One detection implementation, shared with the feedback tool.** The strongest test in
this file is `test_the_tool_and_the_run_path_agree_span_for_span`: the same rules over
the same fold must give `tools/check_rules.py` and this module the same spans. Two
implementations drift, and the drift's shape is the problem — "the sample says this rule
fires and the fold-wide score says it does not" is a state neither an author nor a
reader can act on. The mutation `check_rules_detects_separately` is what proves this
test is load-bearing rather than tautological.

**The four axes are validated and the arm's path comes from naming.yaml.** A typo mints
a directory that looks like another arm.

**`spans.jsonl` carries full provenance and no surface forms.** DESIGN §3's four values
on every span; nothing that could be note text (CLAUDE.md). The file is publishable and
the release screener allows it, which is exactly why the absence of text is asserted
structurally rather than trusted.

**Sealed is unreachable.** `--split test` is refused by name, with the pointer to
`src.eval.run_sealed_eval`, rather than returning an empty fold.

**`model_id` is `none` for a rule arm** (DESIGN §4) and comes from the config.

Runs against the real corpus where one is available and skips otherwise, because what is
being checked is behaviour over a fold rather than over a fixture of two documents.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import model_id_absent, naming                 # noqa: E402
from src.eval import run_fold as rf                                  # noqa: E402
from src.eval.scorer import ScorerError                              # noqa: E402
from src.rules import RuleSet, load_rules                            # noqa: E402

CORPUS = "es-meddocan"
ARM = dict(corpus=CORPUS, detector="R", supervision="sup-free",
           porting="port-oneshot")

#: Rules that actually fire on MEDDOCAN, one per regex-free matcher form. A rule file
#: whose rules match nothing would make every assertion here pass vacuously —
#: `rules/es.yaml` is exactly that file (it is a format example) and is deliberately
#: not used.
#:
#: `probe_org_dup` repeats `probe_org`'s term **on purpose**, and it is not padding: two
#: rules matching the same bytes are the only way to put byte-identical overlapping
#: predictions into the fold, and several guarantees below are unobservable without them.
#: A detector that quietly collapsed duplicates would pass every other test in this file
#: while taking the merge-policy decision away from the merge policy (DESIGN §4), and a
#: writer that dropped one would diverge from the feedback tool in a way nothing else
#: here would see. Two rules in a real file could of course overlap by accident; this
#: makes it happen on demand.
PROBE = """
version: 4
lang: es
rules:
  - rule_id: probe_cue
    layer: context_cue
    phi_type: NAME
    cue: ["Dr.", "Dra."]
    then: capitalised_words
  - rule_id: probe_org
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["Hospital"]
  - rule_id: probe_org_dup
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["Hospital"]
"""


# `corpus_present` comes from `tests/conftest.py`, which is the only place availability is
# decided. This file had its own copy for one commit — the third occurrence of the
# `except: skip` defect (`tests/mutations/README.md`), arrived by copying it out of
# `test_check_rules.py`, which had copied it in turn. Availability from `corpus_root()`;
# construction bare, so a broken loader fails these tests instead of skipping them.


@pytest.fixture(scope="module")
def probe_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("rules") / "probe.yaml"
    path.write_text(PROBE, encoding="utf-8")
    return path


@pytest.fixture
def ran(tmp_path, probe_file, corpus_present):
    spans, metrics, scored = rf.run_fold(
        **ARM, rules={"es": probe_file}, root=tmp_path)
    return spans, metrics, scored


# ─── the two tools cannot disagree ───────────────────────────────────────────


def tool_matches(probe_file: Path) -> list[tuple]:
    """Every match `tools/check_rules.py` saw, from its own `--verbose` output.

    Read out of the tool as a subprocess rather than by calling into it, because what
    has to agree is what the *tool* reports and not what a shared helper returns. A
    comparison against `detect_fold` would agree with itself by construction on either
    side of a divergence introduced downstream of it.
    """
    import re
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_rules.py"), "--corpus", CORPUS,
         "--rules", str(probe_file), "--verbose", "--audit"],
        capture_output=True, text=True, cwd=ROOT, check=True).stdout
    found = []
    for line in out.splitlines():
        m = re.fullmatch(r"  (\S+)  \[(\d+), (\d+)\)  (\S+)", line)
        if m:
            found.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)))
    return found


def test_the_tool_and_the_run_path_agree_span_for_span(tmp_path, probe_file,
                                                       corpus_present):
    """The load-bearing test: one detection implementation, two views of it.

    `check_rules.py` reports counts over a sample and this module scores the fold. If
    those came from separate implementations, a rule could fire in one and not the
    other — and the resulting question ("which of these two numbers is my rule's real
    effect?") has no answer available to an author or to a reader of `metrics.json`.

    Both sides are read from what each tool actually emitted: the tool's `--audit`
    listing and the written `spans.jsonl`. Comparing either one to `detect_fold` would
    be comparing it to the thing they share, which is green by construction the moment
    a divergence is introduced anywhere below that call.

    Asserted as an exact multiset identity rather than as matching totals or as sets:
    two implementations can agree on how many spans they found and disagree about
    which, a boundary differing by one character is precisely what the `fully_covered`
    definition is sensitive to, and a set would hide one side dropping a duplicate —
    which is why `probe_org_dup` is in the rule file.
    """
    from collections import Counter

    spans_file, _, _ = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path)
    written = Counter()
    for line in spans_file.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        written[(row["doc_id"], row["start"], row["end"], row["rule_id"])] += 1

    assert written == Counter(tool_matches(probe_file))
    assert written, "the probe rules matched nothing — the assertion would be vacuous"
    same_bytes = Counter((d, s, e) for d, s, e, _ in written.elements())
    assert max(same_bytes.values()) > 1, (
        "no byte-identical duplicate in the fold, so a side that dropped one would not "
        "be visible here — probe_org_dup is supposed to produce one")


def test_the_tool_calls_the_shared_detector(corpus_present):
    """Structural: the tool must not grow its own detection loop back.

    A behavioural test catches a drift that has already happened. This catches the edit
    that would cause one — `rule.finditer` inside the tool is a second implementation
    however faithful it starts out.
    """
    src = (ROOT / "tools" / "check_rules.py").read_text(encoding="utf-8")
    assert "detect_fold" in src
    assert ".finditer(" not in src, (
        "the feedback tool is iterating rules itself again; detection lives in "
        "src/eval/run_fold.detect_fold and the tool is a view of it")


def test_the_tools_report_the_same_dev_wide_coverage(tmp_path, probe_file,
                                                     corpus_present):
    """The one number both tools print, from both directions.

    `check_rules.py` prints dev-wide `fully_covered` coverage as a count; the scorer's
    `fully_covered` recall is over the same gold. They are computed by different code —
    the tool counts covered gold keys, the scorer runs the assignment matching — so
    agreement here is evidence about the shared detection rather than about shared
    arithmetic.
    """
    import re

    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_rules.py"), "--corpus", CORPUS,
         "--rules", str(probe_file)],
        capture_output=True, text=True, cwd=ROOT, check=True).stdout
    covered = int(re.search(r"dev-wide (\d+)/(\d+) covered", out).group(1))

    _, _, scored = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path)
    leak = scored["modes"]["fully_covered"]["leak"]
    assert covered == leak["denominator"] - leak["leaked"]


# ─── the arm's identity ──────────────────────────────────────────────────────


def test_the_results_land_on_the_naming_path(ran, tmp_path):
    spans, metrics, _ = ran
    stem = "results/es-meddocan/R/sup-free/port-oneshot"
    assert metrics == tmp_path / stem / "metrics.json"
    assert spans == tmp_path / stem / "spans.jsonl"


def test_the_two_files_are_always_in_the_same_directory(ran):
    """A spans file in one arm's directory and its metrics in another's is unreadable."""
    spans, metrics, _ = ran
    assert spans.parent == metrics.parent


@pytest.mark.parametrize("key,bad", [
    ("detector", "R+T"), ("supervision", "supfree"), ("porting", "port-agentic"),
])
def test_an_undefined_axis_value_is_refused(tmp_path, probe_file, corpus_present,
                                            key, bad):
    with pytest.raises(ScorerError, match="axis"):
        rf.run_fold(**{**ARM, key: bad}, rules={"es": probe_file}, root=tmp_path)


def test_nothing_is_written_when_the_arm_is_named_wrong(tmp_path, probe_file,
                                                        corpus_present):
    """Validation happens before the first write, so a bad arm leaves no directory."""
    with pytest.raises(ScorerError):
        rf.run_fold(**{**ARM, "porting": "port-agentic"}, rules={"es": probe_file},
                    root=tmp_path)
    assert not (tmp_path / "results").exists()


def test_the_split_travels_with_the_result_and_not_in_the_path(ran):
    _, metrics, _ = ran
    written = json.loads(metrics.read_text(encoding="utf-8"))
    assert written["run"]["split"] == "dev"
    assert "dev" not in metrics.parts


# ─── sealed is unreachable ───────────────────────────────────────────────────


def test_the_test_fold_is_refused_by_name(tmp_path, probe_file, corpus_present):
    """Not "returns nothing" — refused, with the path that is allowed to do it.

    An empty fold would read as a corpus problem and send whoever hit it looking in the
    wrong place, which is how someone ends up pointing a loader at sealed/ to check.
    """
    with pytest.raises(rf.FoldRunError, match="run_sealed_eval"):
        rf.run_fold(**{**ARM, "split": "test"}, rules={"es": probe_file}, root=tmp_path)


def test_the_cli_refuses_the_test_fold(corpus_present):
    done = subprocess.run(
        [sys.executable, "-m", "src.eval.run_fold", "--corpus", CORPUS,
         "--detector", "R", "--supervision", "sup-free", "--porting", "port-oneshot",
         "--split", "test"],
        capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 2
    assert "run_sealed_eval" in done.stderr
    assert "sealed" in done.stderr


def test_no_sealed_path_is_constructed():
    src = (ROOT / "src" / "eval" / "run_fold.py").read_text(encoding="utf-8")
    assert "sealed_root" not in src
    assert "sealed=True" not in src


def test_an_unknown_split_is_refused(tmp_path, probe_file, corpus_present):
    with pytest.raises(rf.FoldRunError, match="split axis"):
        rf.run_fold(**{**ARM, "split": "validation"}, rules={"es": probe_file},
                    root=tmp_path)


# ─── spans.jsonl: provenance in full, no text ────────────────────────────────


def test_every_span_carries_the_four_provenance_values(ran):
    """DESIGN §3: layer, detector, rule id, score — on every detected span."""
    spans, _, _ = ran
    rows = [json.loads(l) for l in spans.read_text(encoding="utf-8").splitlines()]
    assert rows
    layers = set(naming()["axes"]["layer"])
    for row in rows:
        assert row["layer"] in layers
        assert row["detector"] == "R"
        assert row["rule_id"].startswith("es:")
        assert "score" in row            # None is a value; absence is not
        assert row["agent_actions"] == []


def test_the_layer_is_the_rules_own_and_not_the_detectors(ran):
    """Two rules, two layers, one detector value. A derived layer would collapse them."""
    spans, _, _ = ran
    rows = [json.loads(l) for l in spans.read_text(encoding="utf-8").splitlines()]
    by_rule = {r["rule_id"]: r["layer"] for r in rows}
    assert by_rule["es:probe_cue"] == "context_cue"
    assert by_rule["es:probe_org"] == "gazetteer"
    assert {r["detector"] for r in rows} == {"R"}


def test_spans_jsonl_holds_no_surface_forms(ran):
    """The publishable file, checked structurally.

    Every value must be a doc id, an offset, an axis value, a rule id, or null. The
    fields are whitelisted in `write_spans` for this reason; this asserts the result
    rather than the intention, so a field added to `Span` cannot arrive here silently.
    """
    spans, _, _ = ran
    allowed_keys = {"doc_id", "start", "end", "phi_type", "layer", "detector",
                    "rule_id", "score", "agent_actions"}
    phi = set(naming()["axes"]["phi_type"])
    layers = set(naming()["axes"]["layer"])
    for line in spans.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        assert set(row) == allowed_keys, "an unreviewed field reached a public file"
        assert isinstance(row["start"], int) and isinstance(row["end"], int)
        assert row["phi_type"] in phi
        assert row["layer"] in layers
        assert "surface" not in row and "text" not in row


def code_of(relative: str) -> str:
    """A module's source with docstrings and comments removed.

    The structural tests below are about what the code *does*, and this module explains
    at length what it deliberately does not do — a grep over the raw text cannot tell
    `asdict()` in a paragraph rejecting it from `asdict()` in a call.
    """
    import re
    source = (ROOT / relative).read_text(encoding="utf-8")
    return re.sub(r"#.*", "", re.sub(r'"""(?:.|\n)*?"""', "", source))


def test_no_document_text_reaches_the_writer():
    """Structural, because the temptation is real: the surface is right there on Span."""
    code = code_of("src/eval/run_fold.py")
    assert "span.surface" not in code
    assert "asdict(" not in code, (
        "a whole-object dump publishes whatever field Span gains next; the fields are "
        "enumerated on purpose")
    assert "__dict__" not in code


def test_the_file_is_byte_identical_across_runs(tmp_path, probe_file, corpus_present):
    """Sorted output, so a re-run of the same rules produces no diff."""
    a, _, _ = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path / "a")
    b, _, _ = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()


def test_the_file_is_sorted_and_not_merely_stable(ran):
    """Identical reruns are not enough — the *order* has to be the stated one.

    `RuleSet.detect` iterates rules in file order, so the unsorted output is stable too
    and `test_the_file_is_byte_identical_across_runs` passes either way. What that does
    not survive is a change upstream: reorder the rules in the file, or group detection
    by rule instead of by document, and a committed results file gets a diff that a
    reviewer cannot distinguish from a change in what was detected. Sorting is a
    property of this writer, so it is asserted here rather than inherited.
    """
    spans, _, _ = ran
    rows = [json.loads(l) for l in spans.read_text(encoding="utf-8").splitlines()]
    keys = [(r["doc_id"], r["start"], r["end"], r["rule_id"] or "") for r in rows]
    assert keys == sorted(keys)
    assert len({k[0] for k in keys}) > 1, "one document cannot show an ordering"
    assert len({k[3] for k in keys}) > 1, (
        "all spans came from one rule, so a per-rule grouping would look sorted")


def test_overlapping_predictions_are_not_merged(ran):
    """Merge policy is a replaceable strategy (DESIGN §4) and not the detector's job.

    A detector that collapsed its own overlaps would make fixed-priority, union and
    agent-arbiter score identically, which is the comparison the axis exists for — they
    would all be handed a prediction set with the conflicts already settled. The scorer
    collapses byte-identical spans for the assignment matching and *reports the count*,
    which is the difference between a decision taken by the merge layer and one taken
    silently below it.

    `probe_org` and `probe_org_dup` share a term so the fold is guaranteed to contain
    byte-identical predictions from two different rules; without them this test would
    depend on the corpus happening to make two rules collide.
    """
    spans, metrics, _ = ran
    written = json.loads(metrics.read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in spans.read_text(encoding="utf-8").splitlines()]

    assert written["counts"]["pred"] == len(rows)
    assert written["modes"]["relaxed"]["duplicate_predictions"] > 0, (
        "the scorer saw no duplicates to collapse, so something upstream removed them")

    # The same bytes, twice, from two rules — reaching the file as two lines.
    same_bytes = Counter((r["doc_id"], r["start"], r["end"]) for r in rows)
    assert max(same_bytes.values()) > 1

    # And overlapping-but-not-identical spans are kept as well: the cue rule's names sit
    # inside no gazetteer term, so this asserts the general case rather than the
    # duplicate one.
    by_doc = defaultdict(list)
    for r in rows:
        by_doc[r["doc_id"]].append((r["start"], r["end"], r["rule_id"]))
    overlaps = sum(
        1 for spans_in in by_doc.values()
        for i, a in enumerate(spans_in) for b in spans_in[i + 1:]
        if a[0] < b[1] and a[1] > b[0]
    )
    assert overlaps > 0, "no overlap in the fold at all — nothing to preserve"


# ─── model_id and cost (DESIGN §4, CLAUDE.md) ────────────────────────────────


def test_a_rule_arm_records_the_explicit_absent_model(ran):
    _, metrics, _ = ran
    written = json.loads(metrics.read_text(encoding="utf-8"))
    assert written["run"]["model_id"] == model_id_absent() == "none"


def test_cost_is_zeros_and_wall_time_is_measured(ran):
    """Zero calls is a measurement; wall time is real compute and is not faked to 0."""
    _, metrics, _ = ran
    cost = json.loads(metrics.read_text(encoding="utf-8"))["cost"]
    assert cost["llm_calls"] == 0
    assert cost["prompt_tokens"] == 0 and cost["completion_tokens"] == 0
    assert isinstance(cost["wall_seconds"], (int, float))
    assert cost["wall_seconds"] >= 0


def test_the_rule_version_travels_with_the_result(ran):
    """CLAUDE.md: the rule version is recorded with the result, per file."""
    _, metrics, _ = ran
    run = json.loads(metrics.read_text(encoding="utf-8"))["run"]
    assert run["rules_version"] == {"es": 4}
    assert run["rules"] == ["es:probe_cue", "es:probe_org", "es:probe_org_dup"]


def test_the_schema_version_is_recorded(ran):
    from src.eval import scorer
    _, metrics, _ = ran
    written = json.loads(metrics.read_text(encoding="utf-8"))
    assert written["schema_version"] == scorer.SCHEMA_VERSION


# ─── every rule file the corpus declares ─────────────────────────────────────


def test_all_of_a_corpus_rule_files_are_loaded(monkeypatch, tmp_path, corpus_present):
    """DESIGN §5.2: `corpus_rule_langs` decides, and every listed file is loaded.

    `es-carmen` loads `es` and `cat`. There is no CARMEN loader yet, so this checks the
    mechanism on the corpus that exists — the run must go through `load_for_corpus`,
    which reads that list, rather than assuming one file per corpus.
    """
    seen = {}
    from src.eval import run_fold as module

    def spy(corpus, *, paths=None):
        from src.corpora.base import rule_langs
        seen["langs"] = rule_langs(corpus)
        return RuleSet()

    monkeypatch.setattr(module, "load_for_corpus", spy)
    rf.run_fold(**ARM, root=tmp_path)
    assert seen["langs"] == ["es"]


def test_an_empty_rule_set_still_produces_a_score(tmp_path, corpus_present):
    """Iteration 0. A leak rate of 100% is a result, not an error.

    This is what the baseline reports before its first call, and an arm that raised here
    would make "no rules yet" indistinguishable from "the run failed".
    """
    empty = tmp_path / "empty.yaml"
    empty.write_text("version: 1\nlang: es\nrules: []\n", encoding="utf-8")
    spans, metrics, scored = rf.run_fold(**ARM, rules={"es": empty}, root=tmp_path)
    assert spans.read_text(encoding="utf-8") == ""
    assert scored["headline"]["leak_rate"]["value"] == 1.0
    assert json.loads(metrics.read_text(encoding="utf-8"))["counts"]["pred"] == 0


# ─── the cli ─────────────────────────────────────────────────────────────────


def test_the_cli_reports_the_leak_rate_and_not_f1(probe_file, corpus_present,
                                                  tmp_path):
    """Leak rate is the headline and F1 is not (CLAUDE.md).

    Run into the real results root because the CLI has no `--root`; the arm used is
    a `porting` value nothing else writes, and the directory is removed afterwards.
    """
    import shutil
    out_dir = ROOT / "results" / CORPUS / "R" / "sup-free" / "port-multi"
    try:
        done = subprocess.run(
            [sys.executable, "-m", "src.eval.run_fold", "--corpus", CORPUS,
             "--detector", "R", "--supervision", "sup-free", "--porting", "port-multi",
             "--rules", str(probe_file)],
            capture_output=True, text=True, cwd=ROOT, check=True)
        assert "leak rate" in done.stdout
        assert "headline" in done.stdout
        assert "f1" not in done.stdout.lower()
        assert (out_dir / "metrics.json").exists()
        assert (out_dir / "spans.jsonl").exists()
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_the_cli_help_names_the_sealed_path(corpus_present):
    done = subprocess.run(
        [sys.executable, "-m", "src.eval.run_fold", "--help"],
        capture_output=True, text=True, cwd=ROOT, check=True)
    assert "run_sealed_eval" in done.stdout


def test_two_rule_files_need_the_language_named(probe_file, corpus_present):
    """`--rules` with a multi-file corpus is ambiguous, and the prefix depends on it."""
    done = subprocess.run(
        [sys.executable, "-m", "src.eval.run_fold", "--corpus", "es-carmen",
         "--detector", "R", "--supervision", "sup-free", "--porting", "port-oneshot",
         "--rules", str(probe_file)],
        capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 2
    assert "--lang" in done.stderr


def test_the_error_messages_hold_no_corpus_text(tmp_path, corpus_present):
    """CLAUDE.md: offsets, ids and lengths in messages — never a slice of a note.

    Asserted against the fold itself rather than against a list of forbidden words: what
    must not appear is *whatever this particular corpus says*, and a message built by
    formatting a document's text in would fail here without anyone having to predict
    which words it would carry. MEDDOCAN is synthetic, and the check is written as
    though it were not, because a check that only runs on the safe corpora is a check
    nobody can rely on (`test_offset_mismatch_message_quotes_no_surface` is the same
    argument in the loader).
    """
    from src.eval.run_fold import load_fold

    texts = [d.text for d in load_fold(CORPUS, "dev")]
    messages = []
    for bad in ({"split": "validation"}, {"split": "test"}, {"porting": "port-x"},
                {"corpus": "es-nonexistent"}):
        try:
            rf.run_fold(**{**ARM, **bad}, root=tmp_path)
        except Exception as exc:
            messages.append(str(exc))
    assert len(messages) == 4

    for message in messages:
        for i in range(len(message) - 16):
            window = message[i:i + 16]
            for text in texts:
                assert window not in text, (
                    f"an error message carries a {len(window)}-character slice of a "
                    f"dev document (message index {i})")
