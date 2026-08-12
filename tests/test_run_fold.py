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
import re
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


def test_the_rule_file_location_travels_with_the_result(ran):
    """`rules_source` — which file, not just which revision (DESIGN §5.3).

    Beside `rules_version` rather than instead of it, because the two answer different
    questions and neither implies the other. The version is whatever the author declared,
    so it stays plausible across an overwrite; the path names the arm and the iteration.
    That asymmetry is the reason §5.3 moved rule files under the arm at all: an
    overwritten *record* is visibly gone, an overwritten *input* leaves a complete and
    consistent metrics file whose premise no longer exists.
    """
    _, metrics, _ = ran
    run = json.loads(metrics.read_text(encoding="utf-8"))["run"]
    assert set(run["rules_source"]) == {"es"}
    # A pytest tmp_path is outside the repository, and an absolute path in a published
    # run block names a home directory (and, where the corpus sits beside the repo, the
    # layout of DUA data). Recorded as the filename with a marker.
    assert run["rules_source"]["es"].startswith("<outside-repo>/")
    assert str(ROOT) not in run["rules_source"]["es"]


def test_the_schema_version_is_recorded(ran):
    from src.eval import scorer
    _, metrics, _ = ran
    written = json.loads(metrics.read_text(encoding="utf-8"))
    assert written["schema_version"] == scorer.SCHEMA_VERSION


LIFECYCLE = {"model_arn": "arn:…:foundation-model/anthropic.x",
             "model_name": "Claude Opus 4.5", "status": "ACTIVE",
             "start_of_life_time": "2025-11-24T00:00:00+00:00"}


def test_model_record_is_a_closed_set(tmp_path, probe_file, corpus_present):
    """`MODEL_FIELDS` is the whole of what a caller may put in the run block from here.

    A caller that could add any key would be a second assembler of that block, and the
    reason this function assembles it is that one writer per record is what makes the
    record checkable. The rejected key here is the plausible one: a caller holding a
    lifecycle probe's output has three fields to report and a fourth thing to file.
    """
    with pytest.raises(rf.FoldRunError, match="model_lifecycle"):
        rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path,
                    model_record={"model_id": "x", "model_lifecycle": LIFECYCLE})


def test_the_lifecycle_record_stays_out_of_the_run_block(tmp_path, probe_file,
                                                         corpus_present):
    """Its own argument, passed through to the writer, and never merged.

    `start_of_life_time` is when the *id* appeared in Bedrock's catalogue — not what
    answered (`docs/notes/baseline-model-family.md` §"측정 결과" 4). Inside the run block it
    would sit beside `model_id_resolution` and read as evidence for a verdict it cannot
    support, which is the sixth mutation family filed as data instead of as a comment.
    """
    _, metrics, _ = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path,
                                model_lifecycle=LIFECYCLE)
    written = json.loads(metrics.read_text(encoding="utf-8"))
    assert written["model_lifecycle"] == LIFECYCLE
    assert not [f for f in written["run"] if "lifecycle" in f or "start_of_life" in f]
    # Not spread flat either: `model_arn` one key over from `model_id` is an
    # identifier-looking field in the place identifiers are read from.
    assert not [f for f in written["run"] if f in LIFECYCLE]


def test_a_rule_arm_writes_no_lifecycle_block(ran):
    """The `R` arm probes nothing because it calls nothing, and the block is omitted rather
    than nulled — absence here means "no probe", which is a fact about the arm."""
    _, metrics, _ = ran
    assert "model_lifecycle" not in json.loads(metrics.read_text(encoding="utf-8"))


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


# ─── the round's three files (DESIGN §5.5) ───────────────────────────────────


def rel_round(key: str, iteration: int) -> str:
    """The repository-relative iteration-scoped path for the probe arm, from the template.

    Formatted from `naming.yaml` rather than assembled here, so a template that loses an
    axis fails these tests instead of being reproduced by them.
    """
    from src.corpora.base import path_template
    return path_template(key).format(**ARM, iteration=iteration)


def rel_errors(iteration: int) -> str:
    return rel_round("itererrors", iteration)


@pytest.fixture
def ran_with_errors(tmp_path, probe_file, corpus_present):
    """The same run as `ran`, scored as round 3 of an iterating arm."""
    spans, metrics, scored = rf.run_fold(
        **ARM, rules={"es": probe_file}, root=tmp_path, iteration=3)
    return spans, metrics, scored, tmp_path / rel_errors(3)


def error_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_no_error_list_is_written_unless_the_arm_iterates(ran, tmp_path):
    """Opt-in, and asserted over the whole tree rather than at one path.

    The file is a map of the residual identifiers in the fold and only an iterating arm
    has a use for it (DESIGN §5.5). A run that produced one anyway would leave that
    artefact in every arm's directory as a by-product of a feature the arm does not use,
    which is `score()`'s objection to widening its own return, one layer out. Checked by
    walking the tree because "not at the path I expected" is the weaker claim: an export
    written to an un-iterated path would satisfy it.
    """
    assert not list(tmp_path.rglob("errors.jsonl"))


def test_a_non_iterating_arm_writes_no_round_directory_at_all(ran, tmp_path):
    """**`iteration=None` means the un-iterated pair and nothing else** (DESIGN §5.5).

    The alternative — every arm writes `iter1/` too — was available and is refused, and the
    reason is on `run_fold`. `port-oneshot-nofence`'s `metrics.json` and `spans.jsonl` are
    committed at four axes, so an unconditional `iter1/` would put a second copy of a
    published result beside them, created by a feature that arm does not have; `iter1/` under
    an arm with no rounds is a false statement about the arm; and `iter1/errors.jsonl` would
    then be written by every arm on every corpus.

    Asserted by walking the tree, like the error-list test above, because the strong claim is
    that no round directory exists — not that the one directory this test could name is
    absent.
    """
    assert not [p for p in tmp_path.rglob("iter*") if p.is_dir()]


def test_the_round_s_three_files_land_together(ran_with_errors, tmp_path):
    """Predictions, score and errors, one directory, one record (DESIGN §5.5).

    The failure this guards is the partial scoping the un-widened design would have had: a
    round's score under `iter{N}/` with its predictions overwritten arm-wide every round,
    which leaves `iter3/errors.jsonl` derived from spans nothing still holds.
    """
    _, metrics, _, errors = ran_with_errors
    round_dir = tmp_path / rel_errors(3)
    assert round_dir.parent.name == "iter3"
    for name in ("spans.jsonl", "metrics.json", "errors.jsonl"):
        assert (round_dir.parent / name).exists(), f"{name} missing from the round"
    # And the round sits under the arm the un-iterated score is in.
    assert errors.parent.parent == metrics.parent


def test_the_round_scoped_paths_are_not_returned(ran_with_errors):
    """`run_fold` still returns (spans, metrics, scored) — the un-iterated pair.

    A five-element return would make every caller unpack values that are `None` for every
    arm but the iterating ones, and each round path is a pure function of the four axes and
    the round, so the driver that asked for them can name them (`iter_spans_path`,
    `errors_path`, `scorer.iter_metrics_path`).
    """
    returned = rf.run_fold.__annotations__["return"]
    assert "tuple[Path, Path, dict]" in str(returned)
    spans, metrics, scored, _ = ran_with_errors
    assert isinstance(spans, Path) and isinstance(metrics, Path)
    assert isinstance(scored, dict)
    # The returned pair is the un-iterated one, which is what makes every arm's headline
    # readable at one path (DESIGN §5.5's argument against final-score-only-at-iterN).
    assert spans.parent.name == ARM["porting"]
    assert metrics.parent.name == ARM["porting"]


def test_the_final_rounds_duplicate_is_byte_identical_to_the_round_copy(ran_with_errors,
                                                                       tmp_path):
    """**One scoring pass, two paths** (DESIGN §5.5) — the property the duplication rests on.

    Two `score()` calls could differ (a rule file edited between them, a non-deterministic
    detector added later) and *neither file would look wrong*, because each would be
    internally consistent with the pass that produced it. Byte equality is the strongest
    available check on that from the outside, and it holds for `metrics.json` too because the
    round is a path component and never a field: a payload that named its own round could not
    be duplicated at all.
    """
    spans, metrics, _, _ = ran_with_errors
    round_spans = tmp_path / rel_round("iterspans", 3)
    round_metrics = tmp_path / rel_round("itermetrics", 3)
    assert round_spans.read_bytes() == spans.read_bytes()
    assert round_metrics.read_bytes() == metrics.read_bytes()
    # Non-empty, or the two assertions above are b"" == b"".
    assert spans.read_bytes()
    # And the two are genuinely two files rather than one path built twice.
    assert round_metrics != metrics and round_spans != spans


def test_the_fold_is_detected_once_and_scored_once(monkeypatch, tmp_path, probe_file,
                                                   corpus_present):
    """**The guarantee §5.5's duplication rests on, stated as the thing it actually is.**

    Byte equality of the two copies is what a reader can check afterwards, and it is not the
    guarantee: two deterministic passes produce identical bytes, so a second `score()` call
    would pass that test on every corpus in the repository today. The guarantee is that there
    is one pass, which is why this counts calls.

    What the counting buys is the case that has not happened yet. A rule file edited mid-run,
    a corpus re-exported, or a detector with any non-determinism in it — the `RT` and `T` arms
    are on the ladder — and the round's copy and the un-iterated copy would disagree with
    *neither file looking wrong*: each internally consistent with its own pass, the run, cost
    and termination blocks identical in both, and nothing recording which pass produced which.
    A second pass also doubles every round's detection cost, which `cost.wall_seconds` would
    report faithfully as the fold getting slower.

    Asserted on the iterating call, which is the one that writes four files from one pass.
    """
    from src.eval import run_fold as module

    calls = Counter()
    real_score, real_detect = module.score, module.detect_fold

    def counting_score(*args, **kwargs):
        calls["score"] += 1
        return real_score(*args, **kwargs)

    def counting_detect(*args, **kwargs):
        calls["detect"] += 1
        return real_detect(*args, **kwargs)

    monkeypatch.setattr(module, "score", counting_score)
    monkeypatch.setattr(module, "detect_fold", counting_detect)
    rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path, iteration=3)
    assert calls["detect"] == 1, "the fold was detected more than once"
    assert calls["score"] == 1, (
        "the fold was scored more than once — the round's copy and the un-iterated copy "
        "must come from one pass (DESIGN §5.5)")


def test_the_round_number_is_a_path_component_and_not_a_field(ran_with_errors, tmp_path):
    """It is recoverable from the path, so nothing in the payload records it.

    A round number inside the run block would be a premise of the numbers that
    `metrics_path` could later be asked to format — §4's fifth-path-component rejection
    arriving from the other side — and it would make the final round's two copies unequal,
    which is what the duplication rule forbids. `termination.iterations` already carries how
    many rounds an arm has run, and that is a different quantity from which round this is.
    """
    _, metrics, _, _ = ran_with_errors
    written = json.loads((tmp_path / rel_round("itermetrics", 3)).read_text(
        encoding="utf-8"))
    assert not [k for k in written["run"] if "iter" in k.lower()]
    assert "iteration" not in written
    # The path says 3; the block says how many rounds ran, which for this probe is the
    # non-iterating default and deliberately not 3.
    assert written["termination"]["iterations"] == 1


def test_every_row_is_a_reference_and_nothing_else(ran_with_errors):
    """CLAUDE.md: offsets, types and verdicts. The fields are whitelisted in the writer;
    this asserts the result, so a field added to `ErrorSpan` cannot arrive here silently.

    Stronger than the same test on `spans.jsonl` needs to be, because this file's rows come
    from *gold*: every `missed` row is the position of an identifier the pipeline left in
    the text, and this file is the input to the §1.4 window.
    """
    from src.sample import ERROR_KINDS

    _, _, _, errors = ran_with_errors
    rows = error_rows(errors)
    assert rows, "the probe arm leaked nothing and matched nothing — vacuous"
    phi = set(naming()["axes"]["phi_type"])
    for row in rows:
        assert set(row) == {"doc_id", "span_index", "phi_type", "kind", "start", "end"}, (
            "an unreviewed field reached the file the next round's window is built from")
        assert row["kind"] in ERROR_KINDS
        assert row["phi_type"] in phi
        for field in ("span_index", "start", "end"):
            assert isinstance(row[field], int) and not isinstance(row[field], bool)
        assert row["start"] <= row["end"]


def test_the_rows_carry_no_slice_of_any_dev_document(ran_with_errors, corpus_present):
    """The structural check is not enough on its own: `doc_id` is a string field.

    `write_errors` enumerates its fields, so a surface form could only arrive by way of a
    value — and the one string value here is an identifier the corpus assigns. Checked
    against the fold itself for `test_the_error_messages_hold_no_corpus_text`'s reason:
    what must not appear is whatever this particular corpus says, and MEDDOCAN being
    synthetic is not a reason to write the check as though it were.
    """
    from src.eval.run_fold import load_fold

    _, _, _, errors = ran_with_errors
    texts = [d.text for d in load_fold(CORPUS, "dev")]
    blob = errors.read_text(encoding="utf-8")
    for i in range(0, len(blob) - 16):
        window = blob[i:i + 16]
        for text in texts:
            assert window not in text, (
                f"the error list carries a {len(window)}-character slice of a dev "
                f"document (offset {i})")


def test_the_file_is_what_the_scorer_returned_for_the_same_fold(ran_with_errors,
                                                                probe_file):
    """One scoring pass: the file's rows are `error_spans()` over the same pairs.

    Re-derived here through the same public path rather than compared against a stored
    expectation, because what has to hold is that `run_fold` wrote *the scoring it
    published* — a writer that re-ran detection, or scored a second `from_documents`, would
    produce a plausible file that the metrics beside it are not about.
    """
    from src.eval.scorer import error_spans, from_documents

    _, _, _, errors = ran_with_errors
    docs = load_fold_docs()
    ruleset = load_rules_for(probe_file)
    pairs, _excluded = from_documents(docs, rf.detect_fold(docs, ruleset, detector="R"))
    expected = [
        {"doc_id": e.doc_id, "span_index": e.span_index, "phi_type": e.phi_type,
         "kind": e.kind, "start": e.start, "end": e.end}
        for e in error_spans(pairs)
    ]
    assert error_rows(errors) == expected
    assert expected


def load_fold_docs():
    from src.eval.run_fold import load_fold
    return load_fold(CORPUS, "dev")


def load_rules_for(probe_file: Path) -> RuleSet:
    from src.rules import load_for_corpus
    return load_for_corpus(CORPUS, paths={"es": probe_file})


def test_the_two_halves_agree_with_the_two_published_numbers(ran_with_errors):
    """`missed` is the `fully_covered` leak set and `false_positive` is the `relaxed`
    assignment's — the two numbers an iterating arm is trying to move (DESIGN §9.3, §5.5).

    Asserted against `metrics.json` rather than against the scorer's constants, because the
    failure this guards is the export and the report drifting apart: an arm shown the
    `relaxed` leak set would spend its rounds on a number nobody publishes as the headline,
    and nothing in either file would look wrong.
    """
    _, metrics, _, errors = ran_with_errors
    written = json.loads(metrics.read_text(encoding="utf-8"))
    rows = error_rows(errors)
    kinds = Counter(r["kind"] for r in rows)

    assert kinds["missed"] == written["modes"]["fully_covered"]["leak"]["leaked"]
    assert kinds["false_positive"] == written["modes"]["relaxed"]["overall"]["fp"]
    # Both halves non-empty, or one of the two assertions above is 0 == 0.
    assert kinds["missed"] and kinds["false_positive"]
    # And the leak set is the *stricter* one: relaxed leaks fewer, so an export built
    # from the relaxed mode would pass the first assertion only by coincidence.
    assert (written["modes"]["relaxed"]["leak"]["leaked"]
            < written["modes"]["fully_covered"]["leak"]["leaked"]), (
        "the two modes leak the same count on this fold, so this test cannot see which "
        "one the export came from")


def test_the_file_is_sorted_and_byte_identical_across_runs(tmp_path, probe_file,
                                                           corpus_present):
    """`ErrorSpan.key` order, so the sample drawn from it does not depend on emission order.

    `src.sample.draw` sorts before drawing for exactly this reason — the seed fixes the
    choice of indices and the caller's order fixes which spans those land on. Sorting here
    too is not redundant: this file is the record of what the agent was shown, and a record
    whose byte content depends on a dict rebuild cannot be diffed between rounds.
    """
    a, _, _ = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path / "a",
                          iteration=1)
    b, _, _ = rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path / "b",
                          iteration=1)
    first = (tmp_path / "a" / rel_errors(1))
    second = (tmp_path / "b" / rel_errors(1))
    assert first.read_bytes() == second.read_bytes()

    rows = error_rows(first)
    keys = [(r["doc_id"], r["start"], r["end"], r["phi_type"], r["kind"],
             r["span_index"]) for r in rows]
    assert keys == sorted(keys)
    assert len({k[0] for k in keys}) > 1, "one document cannot show an ordering"
    assert len({k[4] for k in keys}) > 1, "one error kind cannot show this ordering"


def test_the_writer_sorts_what_it_is_given(tmp_path):
    """Not only what `error_spans()` hands it — the file's order is this writer's property.

    `write_errors` is public and the loop driver is a caller. A writer that trusted its
    input would make the file's bytes a function of whoever assembled the list, which is
    the same defect one layer up from the sampler's.
    """
    from src.sample import ErrorSpan

    run = {"corpus": CORPUS, "detector": "R", "supervision": "sup-free",
           "porting": "port-oneshot", "split": "dev", "model_id": "none",
           "generated": "2026-08-12T00:00:00Z", "commit": None, "tree": "unknown"}
    spans = [
        ErrorSpan(doc_id="d2", span_index=0, phi_type="NAME", kind="missed",
                  start=5, end=9),
        ErrorSpan(doc_id="d1", span_index=3, phi_type="DATE", kind="false_positive",
                  start=1, end=4),
    ]
    path = rf.write_errors(spans, run, 2, root=tmp_path)
    assert [r["doc_id"] for r in error_rows(path)] == ["d1", "d2"]


#: The round-scoped path builders this module exposes, so the checks below run on each
#: rather than on whichever one was written first. `scorer.iter_metrics_path` is the third
#: of the round's files and is checked in `test_scorer.py` — it raises `ScorerError`, which
#: is the type that module's callers catch, and the split is deliberate (see `_round_path`).
ROUND_BUILDERS = ("errors_path", "iter_spans_path")


@pytest.mark.parametrize("builder", ROUND_BUILDERS)
@pytest.mark.parametrize("key,bad", [
    ("corpus", "es-meddocan-dev"), ("detector", "R+T"), ("supervision", "supfree"),
    ("porting", "port-agentic"),
])
def test_a_round_path_refuses_an_axis_value_naming_no_cell(builder, key, bad):
    with pytest.raises(rf.FoldRunError, match="naming.yaml"):
        getattr(rf, builder)(**{**ARM, key: bad}, iteration=1)


@pytest.mark.parametrize("builder", ROUND_BUILDERS)
@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "1", None])
def test_a_round_path_refuses_a_round_that_is_not_a_round(builder, bad):
    """`iter0/` and `iter1.0/` put a round's record where nothing looks for it.

    `True` is in the list because `isinstance(True, int)` holds: a boolean reaching here is
    a caller that passed a flag, and `iter1/` is the round it would silently name.

    Parametrized over both builders because the shared `_round_path` is what makes them
    agree, and a later edit that gave one of them its own copy of the check would pass a
    test written against a single function.
    """
    with pytest.raises(rf.FoldRunError, match="iteration"):
        getattr(rf, builder)(**ARM, iteration=bad)


@pytest.mark.parametrize("builder,filename", [
    ("errors_path", "errors.jsonl"), ("iter_spans_path", "spans.jsonl"),
])
def test_the_round_is_a_directory_and_the_axes_are_above_it(builder, filename):
    """`paths.itererrors` and `paths.iterspans`' shape, through the functions
    (DESIGN §5.3, §5.5)."""
    path = getattr(rf, builder)(**ARM, iteration=4, root=Path("/r"))
    assert path == Path(
        f"/r/results/es-meddocan/R/sup-free/port-oneshot/iter4/{filename}")


def test_the_refusal_names_which_of_the_rounds_files_was_misplaced():
    """One shared check, and the message still says which call to look at.

    `_round_path` exists so the three components are validated in one place; the cost of
    that is a message with two callers, and `artefact` is what pays it. A refusal reading
    only "iteration must be an integer >= 1" would send a reader to whichever of the
    round's writes they thought of first.
    """
    with pytest.raises(rf.FoldRunError, match="error list"):
        rf.errors_path(**ARM, iteration=0)
    with pytest.raises(rf.FoldRunError, match="prediction list"):
        rf.iter_spans_path(**ARM, iteration=0)


def test_nothing_is_written_when_the_round_is_not_a_round(tmp_path, probe_file,
                                                         corpus_present):
    """Validated before the first write, like the arm's name.

    A run that wrote `iter{N}/spans.jsonl` and then raised on the error list would leave the
    round's directory holding predictions with no score beside them, which is the
    half-written results directory `FoldRunError` exists to prevent. Nothing at all is
    written, including the un-iterated pair: the refusal is about the round, and a run that
    left a four-deep `metrics.json` behind would put a score in the arm's directory for a
    round that has no directory of its own.
    """
    with pytest.raises(rf.FoldRunError, match="iteration"):
        rf.run_fold(**ARM, rules={"es": probe_file}, root=tmp_path, iteration=0)
    assert not (tmp_path / "results").exists()


def test_the_export_path_is_denied_by_the_screener():
    """The path rule is the other half of the defence, and it is checked on the real path.

    `ErrorSpan` has no text field by construction, which is what makes this file safe to
    exist on a DUA corpus; the deny rule is about what it is even so — a list of the
    offsets of every missed identifier in the fold, drawn from gold (DESIGN §5.5). Asserted
    through `deny()` on the path this module actually builds, not against the pattern's
    text: a pattern that matched nothing would be a rule reported as present and never run.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_screen_probe_errors", ROOT / "tools" / "release_screen.py")
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)

    rel = rel_errors(3)
    assert screen.deny(rel)
    # And not published from the other side. `deny()` is consulted first, so an ALLOW
    # entry reaching this path would not change the verdict today — it would make the two
    # lists disagree, one deny-rule deletion away from publishing the file.
    assert not any(re.search(p, rel) for p in screen.ALLOW_PATTERNS)


def test_the_export_path_is_gitignored():
    """Paired with the deny rule. Denied-but-not-ignored is reported as BLOCKED only once
    somebody stages it; ignored-but-not-denied is reported as Quarantined, which reads as
    fine. `test_every_deny_listed_path_is_also_gitignored` is the general form.
    """
    done = subprocess.run(
        ["git", "check-ignore", "-q", rel_errors(3)],
        cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, f"{rel_errors(3)} is not gitignored"


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


def test_the_cli_round_flag_says_where_to_read_and_where_to_write(monkeypatch, tmp_path):
    """**One `--iteration`, both jobs** (DESIGN §5.3, §5.5).

    The flag already chose which rule files to read. Passing it to `run_fold` as well is what
    makes it also choose where the round's record goes, and a second flag for the write is
    what would make the broken state expressible from a shell: round 4's rules scored into
    round 3's directory, or into no round's directory at all — the arm-wide overwrite §5.5
    corrected.

    Monkeypatched rather than run as a subprocess: what is under test is the argument reaching
    the call, and a real run would need round 4's rule files to exist and would write into the
    live results tree.
    """
    from src.eval import run_fold as module

    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return (tmp_path / "spans.jsonl", tmp_path / "metrics.json",
                {"headline": {"leak_rate": {"value": 1.0, "mode": "fully_covered"},
                              "leak_rate_lower_bound": {"value": 1.0, "mode": "relaxed"}},
                 "counts": {"documents": {"total": 0}, "gold": {"in_scope": 0},
                            "pred": 0}})

    monkeypatch.setattr(module, "run_fold", spy)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    module.main(["--corpus", CORPUS, "--detector", "R", "--supervision", "sup-free",
                 "--porting", "port-loop", "--iteration", "4"])
    assert seen["iteration"] == 4
    # And the same 4 chose the input path, so the two cannot name different rounds.
    assert "iter4" in str(seen["rules"]["es"])


def test_the_cli_names_the_round_directory_only_when_there_is_one(monkeypatch, tmp_path,
                                                                 capsys):
    """Printed as a directory, not as three filenames — one of them is deny-listed.

    `errors.jsonl` is `paths.itererrors`, and this output goes to a terminal, into CI logs and
    into shell history, which are the paths `release_screen.py` does not reach (CLAUDE.md). A
    directory is what a reader needs; the filenames are in `naming.yaml`.
    """
    from src.eval import run_fold as module

    def spy(**kwargs):
        return (tmp_path / "spans.jsonl", tmp_path / "metrics.json",
                {"headline": {"leak_rate": {"value": 1.0, "mode": "fully_covered"},
                              "leak_rate_lower_bound": {"value": 1.0, "mode": "relaxed"}},
                 "counts": {"documents": {"total": 0}, "gold": {"in_scope": 0},
                            "pred": 0}})

    monkeypatch.setattr(module, "run_fold", spy)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    base = ["--corpus", CORPUS, "--detector", "R", "--supervision", "sup-free",
            "--porting", "port-loop"]

    module.main([*base, "--iteration", "4"])
    printed = capsys.readouterr().out
    assert "iter4" in printed
    assert "errors.jsonl" not in printed

    # A non-iterating run has no round to name, and a line reading `iter1/` would be the
    # printed form of the artefact this arm deliberately does not write.
    module.main(base)
    assert "iter" not in capsys.readouterr().out


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
