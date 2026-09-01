"""`port-multi`: three authoring calls outside the loop, and the guards that keep them there.

The rung's claim is a call *order* — Profiler, Mapper, LexiconBuilder, once each, before round 1
— and the thing worth testing is not that the happy path writes three files. It is that the
claim cannot be broken quietly. So this file is built around the three ways it can be:

  1. **A round rewrites an artefact.** `artefacts._refuse_existing` turns it into a refusal, and
     `test_a_second_write_of_each_artefact_is_refused` asserts it at all three paths.
  2. **Something that is not a writer edits one between rounds** — a hand fix after round 3's
     leak rate came in. No path rule can see this, because an edit is not an overwrite, and no
     in-process guard can either: `tools/run_loop.py` runs one round per process. That is what
     `freeze_artefacts()` is for and what `test_an_edited_artefact_is_drift` covers, once per
     artefact and once for a term list.
  3. **A module in the loop's import graph acquires a writer.** Asserted over the syntax tree,
     for `tests/test_arm_rules_path.py`'s reason: inferring the wrong thing and being handed the
     right thing produce identical output on the happy path.

The failure semantics are the other half. They differ per artefact deliberately — a profile
stops the arm, a mapping stops it on every corpus, a lexicon entry is dropped and counted — and
three tests assert the *difference*, because a validator that treated all three alike would pass
every test written about one of them.

The fixtures are the real vocabularies and the real inventory. A synthetic profile with invented
values was tried first and produced 3 profile plus 20 mapping refusals — which is the
vocabularies working, and is also a fixture that tests nothing about the code. So the mapping
here is `DESIGN.md` §9.0's own table read back through `corpora.meddocan`, and a departure from
it is written as a departure rather than as a different fixture.
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate, rules as rules_module, sample as sample_module   # noqa: E402
from src.corpora import meddocan                                             # noqa: E402
from src.corpora.base import CorpusError, lexicon_target_types               # noqa: E402
from src.llm import bedrock as bedrock_module                                 # noqa: E402
from src.orchestrate import CALLED, FORMAT_FAILURE                            # noqa: E402
from src.porting import artefacts, multi                                     # noqa: E402
from src.porting.artefacts import ArtefactError                              # noqa: E402
from src.sample import WINDOW_FILES                                           # noqa: E402

from test_bedrock import FakeControl, reply                                   # noqa: E402

CORPUS = "es-meddocan"
ARM = dict(corpus=CORPUS, detector="R", supervision="sup-free", porting=multi.PORTING)
MODEL = "us.anthropic.claude-opus-5"


# ─── the three responses, as the real vocabularies require them ──────────────

#: MEDDOCAN's own labels, from the loader rather than typed out: 20 mapped plus 2 excluded.
#: `DESIGN.md` §9.0 claims the two columns are exhaustive over the corpus, and this is that
#: claim's operational form — the Profiler's `type_inventory` is what the Mapper must cover.
INVENTORY_LABELS = sorted(set(meddocan.TYPE_MAP) | set(meddocan.EXCLUDED_TYPES))

#: A profile that validates: every schema field, every value from its declared vocabulary.
#: `cites` and `unresolved` are omitted — both are optional (`PROFILE_META`), and a fixture
#: carrying them would test the citation check rather than the path this file is about.
PROFILE = {
    "annotation_encoding": "brat_standoff",
    "text_location": "separate_file",
    "offset_unit": "character",
    "offset_base": "zero",
    "offset_end": "exclusive",
    "newline": "lf_only",
    "bom": "absent",
    "type_system_level": "flat",
    "type_inventory": INVENTORY_LABELS,
    "group_key": "document_id_stem",
    "patient_key_available": False,
}


def _basis(label: str, canonical: str) -> str:
    """The basis §2.2's table admits for this pairing, chosen the way the table constrains it.

    Not a free label: `residual_bucket` is admissible only for the residual type,
    `source_type_is_coarser` only for a canonical type DESIGN declares a merge, and
    `source_label_family` only where the label's family has at least two members. So the fixture
    derives the basis from the pairing instead of asserting one — a hardcoded `canonical_gloss`
    everywhere would pass validation while exercising one row of four.
    """
    if canonical == artefacts._residual_type():
        return "residual_bucket"
    if canonical in artefacts.merge_declaring_types():
        return "source_type_is_coarser"
    family = artefacts._label_family(label)
    if sum(1 for other in INVENTORY_LABELS
           if artefacts._label_family(other) == family) >= 2:
        return "source_label_family"
    return "canonical_gloss"


#: The Mapper's response, faithful to §9.0. The excluded half names the *concept* from
#: `excluded_types()`; which concept is not compared by `compare_with_design` (§4.2 declines to
#: write that answer key), so the fixture picks the concept whose name the label carries.
MAPPING = {
    "map": {label: {"canonical": canonical, "basis": _basis(label, canonical)}
            for label, canonical in meddocan.TYPE_MAP.items()},
    "excluded": {
        "SEXO_SUJETO_ASISTENCIA": {"excluded_type": "SEXO", "basis": "design_exclusion"},
        "FAMILIARES_SUJETO_ASISTENCIA": {"excluded_type": "FAMILIARES",
                                         "basis": "design_exclusion"},
    },
}

#: The LexiconBuilder's response. Terms are ASCII placeholders of at least `MIN_ENTRY_CHARS`
#: and are not place names: a fixture holding real institution names would put a gazetteer in
#: the repository, and what is under test is the writer and not the terms.
LEXICONS = {
    "lexicons": {
        "es": {
            "institutions": {"basis": "administrative_enumeration",
                             "entries": ["Zzqx Wbbl", "Vvfr Nnkt"]},
            "regions": {"basis": "general_knowledge_named_entities",
                        "entries": ["Mmpl Grrd"]},
        }
    }
}


def _text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Sequence:
    """A runtime that answers each `converse` with the next canned response.

    Three calls in one process is the arm's shape, so a single-response fake cannot express it:
    the Mapper's prompt is built from the Profiler's answer, and a stub returning one body would
    make every step's validation run against the same object.
    """

    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.texts:
            raise AssertionError(
                f"the arm made call {len(self.calls)} and this fake was given "
                f"{len(self.calls) - 1} responses. An unexpected call is the finding: these "
                "three agents are called once each and the count is the rung's definition."
            )
        return reply(self.texts.pop(0))


# ─── the harness ─────────────────────────────────────────────────────────────


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A redirected root with the real window files and the real inventory in place.

    Copied rather than invented, for `tests/test_loop.py`'s reason: `window_hashes()` then
    hashes the committed window and the freeze record is the one a real arm writes. The
    inventory is copied too — the Profiler's prompt is built from it and a stub one would make
    every `type_not_in_inventory` assertion a test of the stub.

    `artefacts.ROOT` is patched as well as `orchestrate`'s and `rules`'s: that module imports
    `ROOT` from `corpora.base` by value, so an unpatched copy writes the three artefacts into
    the real `results/` tree.
    """
    for name in WINDOW_FILES:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dest)
    inventory = artefacts.inventory_path(CORPUS)
    dest = tmp_path / inventory.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(inventory, dest)

    monkeypatch.setattr(orchestrate, "ROOT", tmp_path)
    monkeypatch.setattr(sample_module, "ROOT", tmp_path)
    monkeypatch.setattr(rules_module, "ROOT", tmp_path)
    monkeypatch.setattr(artefacts, "ROOT", tmp_path)
    monkeypatch.setattr(bedrock_module, "_require_logging_check", lambda: None)
    return tmp_path


@pytest.fixture(autouse=True)
def no_control_plane(monkeypatch):
    """Close the lifecycle probe's route to AWS for every test here.

    `tests/test_loop.py` does the same and for the same reason: without it a test of an
    authoring call reaches the control plane, and a suite that makes network calls acquires a
    `--no-network` flag and then stops covering the probe.
    """
    monkeypatch.setattr(bedrock_module, "_control_client", lambda: FakeControl())


def _author_all(tree, *, profile=PROFILE, mapping=MAPPING, lexicons=LEXICONS):
    """The three calls in order, then the freeze. Returns `(results, runtime)`."""
    runtime = Sequence(_text(profile), _text(mapping), _text(lexicons))
    common = dict(**ARM, model_id=MODEL, client=runtime, control_client=FakeControl())
    out = [multi.author_profile(**common)]
    if out[0]["outcome"] == CALLED:
        out.append(multi.author_mapping(profile=out[0]["profile"], **common))
    if len(out) == 2 and out[1]["outcome"] == CALLED:
        out.append(multi.author_lexicons(**common))
    return out, runtime


# ─── the happy path, and what it must have written ───────────────────────────


def test_three_calls_write_three_artefacts_and_nothing_else_calls(tree):
    """The rung, end to end: three calls, three artefacts, one role each in the log."""
    out, runtime = _author_all(tree)
    assert [step["outcome"] for step in out] == [CALLED, CALLED, CALLED]
    assert len(runtime.calls) == 3

    assert orchestrate.roles_called(*ARM.values()) == {
        multi.PROFILER: 1, multi.MAPPER: 1, multi.LEXICON_BUILDER: 1}
    assert artefacts.profile_path(**ARM).exists()
    assert artefacts.mapping_path(**ARM).exists()
    assert artefacts.manifest_path(**ARM).exists()
    assert (multi.lexicon_collection(**ARM) / "es" / "institutions.txt").exists()


def test_every_authoring_line_is_at_the_authoring_iteration(tree):
    """`role` and `iteration` are checked against each other, and this is the log's half of it.

    `call_line` refuses an out-of-loop role anywhere but `AUTHORING_ITERATION` and a loop role
    there, so "out of loop" is a property of the log rather than of this driver (DESIGN §6.7.1).
    Asserted on the written lines, because that is what a later reader has.
    """
    _author_all(tree)
    lines = orchestrate.read_calls(*ARM.values())
    assert len(lines) == 3
    for line in lines:
        assert line["iteration"] == orchestrate.AUTHORING_ITERATION
        assert line["role"] in multi.ROLE_ORDER
        # No drawn sample and the record says so explicitly rather than omitting the key —
        # none of these three agents is shown one (`call_line`).
        assert line["sample_reference"] is None
    assert [line["role"] for line in lines] == list(multi.ROLE_ORDER)


def test_the_window_is_frozen_before_the_profiler_and_not_again(tree):
    """"Freeze last" means before the arm's *first* call, which on this rung is the Profiler's.

    Two things are asserted together because either alone would pass on the bug: the record
    exists after the three calls, and its `revision` is 1. A driver that froze before each call
    would leave revision 3 and a window described as of the third; one that never froze would
    leave `run_iteration_1` to do it, and `freeze_window` refuses once a call line exists — the
    arm would be unrunnable, which is what `already_frozen` was added for.
    """
    _author_all(tree)
    path = orchestrate.freeze_path(*ARM.values())
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["revision"] == 1
    assert record["sections_shown"] == list(orchestrate.ONESHOT_SECTIONS)
    assert record["files"] == list(WINDOW_FILES)


def test_the_profile_hash_in_the_mapping_is_the_agents_profile(tree):
    """`profile_sha256` is over the validated profile object, not the record around it.

    The record carries `refused` and `counts`, which are the orchestrator's; a hash over those
    would make one profile produce two mapping hashes if a count changed. What has to be
    unforgeable is the join between a mapping and the inventory it mapped.
    """
    out, _ = _author_all(tree)
    import yaml
    mapping = yaml.safe_load(artefacts.mapping_path(**ARM).read_text(encoding="utf-8"))
    assert mapping["profile_sha256"] == artefacts._digest(
        artefacts._canonical_json(out[0]["profile"]))


def test_a_faithful_mapping_disagrees_with_the_design_nowhere(tree):
    """§4.2's pre-registered comparison, on a mapping that is §9.0's own table.

    `applied` is `design` because `es-meddocan` has a row there, and `compared_against_design`
    equals the label count — a mapping compared against a stale table would show up as a count
    short of `counts.source_types`.
    """
    out, _ = _author_all(tree)
    step = out[1]
    assert step["applied"] == artefacts.APPLIED_DESIGN
    assert step["disagreements"] == []
    assert step["counts"]["compared_against_design"] == len(INVENTORY_LABELS)


# ─── the three failure semantics, which differ on purpose ────────────────────


def test_a_refused_profile_stops_the_arm(tree):
    """It configures loading, so a wrong field is a corpus loaded wrongly and scored anyway."""
    bad = {**PROFILE, "offset_base": "one_based"}          # not a declared `offset_base`
    out, runtime = _author_all(tree, profile=bad)
    assert len(out) == 1
    assert out[0]["outcome"] == FORMAT_FAILURE
    assert len(runtime.calls) == 1, "the Mapper was called on a refused profile"

    # The artefact is written *and* the failure record beside it: the first holds the reason per
    # field, the second holds the response, and neither is derivable from the other.
    record = json.loads(artefacts.profile_path(**ARM).read_text(encoding="utf-8"))
    assert [r["reason"] for r in record["refused"]] == ["undeclared_value"]
    assert out[0]["failure_path"].exists()


def test_a_refused_mapping_stops_the_arm_on_a_corpus_the_design_maps(tree):
    """The strictness does not depend on which corpus is in hand.

    `es-meddocan` has a §9.0 row, so its mapping is recorded and compared rather than used — and
    a refusal stops the arm all the same. The alternative is a rule that branches on corpus,
    which is the shape CLAUDE.md rejects: a check that is loose on one corpus is loose on the
    corpus with no table to catch the error.
    """
    bad = {"map": {k: v for k, v in MAPPING["map"].items() if k != "CALLE"},
           "excluded": MAPPING["excluded"]}
    out, runtime = _author_all(tree, mapping=bad)
    assert len(out) == 2
    assert out[1]["outcome"] == FORMAT_FAILURE
    assert len(runtime.calls) == 2, "the LexiconBuilder was called after a refused mapping"

    import yaml
    record = yaml.safe_load(artefacts.mapping_path(**ARM).read_text(encoding="utf-8"))
    assert record["counts"]["by_refusal"] == {"unmapped_source_type": 1}


def test_a_refused_lexicon_entry_does_not_stop_the_arm(tree):
    """Dropped and counted, and the round runs on what survived.

    Not leniency: a thinner gazetteer is a weaker detector, and a weaker detector's cost is
    already the headline number this experiment reports. A profile's error shows up nowhere,
    which is why that one stops and this one does not.
    """
    thin = {"lexicons": {"es": {"institutions": {
        "basis": "administrative_enumeration",
        "entries": ["Zzqx Wbbl", "ab", "Zzqx Wbbl"]}}}}
    out, _ = _author_all(tree, lexicons=thin)
    assert out[2]["outcome"] == CALLED
    assert out[2]["counts"]["entries"] == 1
    assert out[2]["counts"]["by_refusal"] == {"entry_too_short": 1, "duplicate_entry": 1}
    assert (multi.lexicon_collection(**ARM) / "es" / "institutions.txt").read_text(
        encoding="utf-8") == "Zzqx Wbbl\n"


def test_no_stop_message_carries_a_rejected_value(tree):
    """Reasons and counts, and no surface form — CLAUDE.md, unconditionally.

    This string reaches `format_failure.json`, which is committed, and the terminal that ran the
    step. The rejected value is a value from the response, so a message quoting it would put an
    undeclared string into a committed file by the one route the screener does not cover.
    """
    marker = "ZZ_NEVER_A_DECLARED_VALUE_ZZ"
    out, _ = _author_all(tree, profile={**PROFILE, "offset_base": marker})
    record = json.loads(out[0]["failure_path"].read_text(encoding="utf-8"))
    assert marker not in record["error"]
    assert "undeclared_value" in record["error"]
    # The response itself is in the record verbatim, per DESIGN §10 A2 — that is the one place
    # it belongs and it is why the message does not need it.
    assert marker in record["response"]


def test_an_unparseable_response_names_the_path_it_did_not_write(tree):
    """The one case where `rules_path` names a path that does not exist, asserted as such.

    A response that is not a JSON object never reaches an artefact — unlike `run_arm`, which
    writes the raw text and then fails to load it. The field is filled with the would-be path
    because a null would change the failure record's shape for exactly the arms whose records
    matter most; the step result's `path` is `None`, which is how a caller tells the two apart.
    """
    out, runtime = _author_all(tree, profile="not an object at all")
    assert out[0]["outcome"] == FORMAT_FAILURE
    assert out[0]["path"] is None
    assert not artefacts.profile_path(**ARM).exists()
    record = json.loads(out[0]["failure_path"].read_text(encoding="utf-8"))
    assert record["rules_path"].endswith("profile.json")


# ─── the artefacts are inputs: the three guards ──────────────────────────────


def test_a_second_write_of_each_artefact_is_refused(tree):
    """Guard 1. The paths carry no `{iteration}`, so a second write is either a re-run or a
    round revising an input — and after the fact the two are the same bytes."""
    out, _ = _author_all(tree)
    with pytest.raises(ArtefactError, match="refusing to overwrite"):
        artefacts.write_profile({}, **ARM)
    with pytest.raises(ArtefactError, match="refusing to overwrite"):
        artefacts.write_mapping({}, **ARM)
    with pytest.raises(ArtefactError, match="refusing to overwrite"):
        artefacts.write_lexicons({"es": {"institutions": {"basis": "morphological_class",
                                                          "entries": ["Zzz"]}}},
                                 {}, **ARM)


@pytest.mark.parametrize("what", ["profile", "mapping", "manifest", "lexicon"])
def test_an_edited_artefact_is_drift(tree, what):
    """Guard 2, and the reason a freeze record exists at all.

    A hand edit is not an overwrite, so `_refuse_existing` cannot see it — and `tools/run_loop.py`
    runs one round per process, so no in-memory guard can either. Parametrised over a term list
    as well as the three files, because the collection is the artefact with a causal path into
    detection and an added term is the edit with a score attached.
    """
    _author_all(tree)
    multi.freeze_artefacts(**ARM)
    assert multi.artefact_drift(**ARM) == []
    assert multi.check_ready_for_round(2, **ARM, root=tree) is None

    targets = {
        "profile": (artefacts.profile_path(**ARM), "files.profile"),
        "mapping": (artefacts.mapping_path(**ARM), "files.mapping"),
        "manifest": (artefacts.manifest_path(**ARM), "files.lexicon_manifest"),
        "lexicon": (multi.lexicon_collection(**ARM) / "es" / "regions.txt",
                    "lexicons.es/regions.txt"),
    }
    path, key = targets[what]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert multi.artefact_drift(**ARM) == [key]
    problem = multi.check_ready_for_round(4, **ARM, root=tree)
    assert problem and key in problem and "restarts" in problem


def test_a_deleted_term_list_is_drift_too(tree):
    """A key on one side and not the other. A comparison over the intersection calls it clean."""
    _author_all(tree)
    multi.freeze_artefacts(**ARM)
    (multi.lexicon_collection(**ARM) / "es" / "regions.txt").unlink()
    assert multi.artefact_drift(**ARM) == ["lexicons.es/regions.txt"]


def test_the_freeze_refuses_a_second_take(tree):
    """The opposite of `freeze_window`'s choice, and the difference is what each attests.

    A window may legitimately be re-frozen up to the first call, so that record counts
    revisions. This one describes files the arm just wrote and will not write again; the one
    reason to want a second take is the reason there must not be one.
    """
    _author_all(tree)
    multi.freeze_artefacts(**ARM)
    with pytest.raises(ArtefactError, match="refusing to re-freeze"):
        multi.freeze_artefacts(**ARM)


def test_a_freeze_before_the_third_call_is_refused(tree):
    """All three or none. A freeze covering two would let round 1 run against a collection
    nothing attests to — which is the state the record exists to make impossible."""
    runtime = Sequence(_text(PROFILE), _text(MAPPING))
    common = dict(**ARM, model_id=MODEL, client=runtime, control_client=FakeControl())
    first = multi.author_profile(**common)
    multi.author_mapping(profile=first["profile"], **common)
    with pytest.raises(ArtefactError, match="not written yet"):
        multi.freeze_artefacts(**ARM)


def test_a_round_without_a_freeze_is_refused(tree):
    """And the refusal says not to freeze now, which is the mistake it would invite.

    A freeze taken at round 4 hashes the artefacts as they are then and claims they were round
    1's. `orchestrate.window_drift` refuses a missing record with the same sentence, and for the
    same reason.
    """
    _author_all(tree)
    problem = multi.check_ready_for_round(1, **ARM, root=tree)
    assert problem and "not frozen" in problem
    with pytest.raises(ArtefactError, match="Do NOT freeze now"):
        multi.artefact_drift(**ARM)


def test_a_round_before_the_three_calls_is_refused(tree):
    """Read from the call log and not from the files on disk.

    An artefact is what a call produced; a hand-written `mapping.yaml` satisfies a check that
    looks at paths. And exactly once rather than at least once — two Profiler calls means two
    profiles were authored and the rounds read whichever won.
    """
    assert "not one each" in multi.check_ready_for_round(1, **ARM, root=tree)
    _author_all(tree)
    multi.freeze_artefacts(**ARM)
    assert multi.check_ready_for_round(1, **ARM, root=tree) is None


def test_no_module_the_loop_imports_can_write_these_artefacts():
    """Guard 3, over the syntax tree.

    The three writers live in `src/porting/artefacts.py` and the only caller is
    `src/porting/multi.py`, which the loop does not import. A behavioural test cannot see this:
    a `loop.py` that called `write_profile` on a path that happens not to exist yet would write
    a profile and pass every other test in this file.

    Checked as *names appearing anywhere* in the module, not as call sites, because an alias or
    a `getattr` would slip a call-site check — and there is no legitimate reason for any of
    these names to appear in a module the loop imports.
    """
    writers = {"write_profile", "write_mapping", "write_lexicons", "freeze_artefacts"}
    loop_graph = ["src/porting/loop.py", "src/porting/audit.py", "src/eval/run_fold.py",
                  "src/orchestrate.py", "src/rules.py", "src/llm/prompt.py"]
    for rel in loop_graph:
        source = (ROOT / rel).read_text(encoding="utf-8")
        tree_ = ast.parse(source)
        found = sorted({node.id for node in ast.walk(tree_)
                        if isinstance(node, ast.Name) and node.id in writers}
                       | {node.attr for node in ast.walk(tree_)
                          if isinstance(node, ast.Attribute) and node.attr in writers})
        assert not found, (
            f"{rel} names {found}. `port-multi`'s three artefacts are inputs the loop reads and "
            "does not produce (DESIGN §4), and the loop's own import graph is where that stops "
            "being true first. The writers belong in src/porting/multi.py, which nothing here "
            "imports."
        )


def test_the_loop_is_not_imported_by_the_authoring_driver_the_other_way(tree):
    """`multi` imports `loop`, and that direction is the one that must hold.

    The rung reuses `loop.run_iteration_1` and `loop.run_iteration` unchanged, so the dependency
    points from the new module at the old one. If it ever reversed, the loop would be able to
    author, and the test above would be asserting something about a cycle rather than about a
    boundary.
    """
    source = (ROOT / "src" / "porting" / "loop.py").read_text(encoding="utf-8")
    assert "multi" not in {
        alias.name.split(".")[-1]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


# ─── what the round driver is handed ────────────────────────────────────────


def test_the_collection_written_is_the_collection_a_rule_resolves_against(tree):
    """One path by construction rather than by two templates agreeing.

    `write_lexicons` writes into `rules.arm_lexicon_root()`, `lexicon_collection()` returns it,
    and `run_sealed_eval._lexicon_root` reconstructs it. A rule saying `institutions` resolves
    against the file the LexiconBuilder wrote, or the arm fails at load in round 1.
    """
    out, _ = _author_all(tree)
    assert out[2]["lexicons"] == rules_module.arm_lexicon_root(**ARM)
    assert (out[2]["lexicons"] / "es" / "institutions.txt") in out[2]["lexicon_paths"]


def test_a_term_list_holds_the_terms_and_nothing_else(tree):
    """No header, no comment, no generated-by line (`lexicon_builder.md` §2.2).

    The `.txt` format has a prose channel the JSON schema does not, and that channel is exactly
    what §2.1 refuses to give the agent. So the orchestrator serialises terms only — one per
    line — and this is the assertion that keeps it that way.
    """
    _author_all(tree)
    text = (multi.lexicon_collection(**ARM) / "es" / "regions.txt").read_text(encoding="utf-8")
    assert text == "Mmpl Grrd\n"
    assert artefacts._COMMENT_MARK not in text


def test_the_manifest_carries_counts_and_no_entry_text(tree):
    """What keeps `paths.armlexiconmanifest` out of the deny classification.

    The reasoning in `config/naming.yaml` is explicit that a surface form appearing here would
    invalidate the screener decision rather than merely widen a schema, so it is asserted rather
    than trusted.
    """
    _author_all(tree)
    text = artefacts.manifest_path(**ARM).read_text(encoding="utf-8")
    manifest = json.loads(text)
    assert manifest["files"]["es/institutions"]["entries"] == 2
    for term in LEXICONS["lexicons"]["es"]["institutions"]["entries"]:
        assert term not in text


def test_the_freeze_record_carries_names_and_hashes_only(tree):
    """The same classification argument one file along, and the same assertion."""
    _author_all(tree)
    record = multi.freeze_artefacts(**ARM)
    text = multi.freeze_path(*ARM.values()).read_text(encoding="utf-8")
    assert set(record["files"]) == {multi.PROFILE_KEY, multi.MAPPING_KEY, multi.MANIFEST_KEY}
    assert set(record["lexicons"]) == {"es/institutions.txt", "es/regions.txt"}
    assert record["counts"]["lexicon_files"] == 2
    for term in LEXICONS["lexicons"]["es"]["institutions"]["entries"]:
        assert term not in text


def test_read_profile_returns_the_agents_object_for_the_resume(tree):
    """`--step mapping` in a second process reads this, and it must be the same object.

    The `profile` block and not the record around it: `mapping_record` hashes what it is given
    into `profile_sha256`, and a hash over the record's `counts` would move if a count did.
    """
    out, _ = _author_all(tree)
    assert multi.read_profile(**ARM, root=tree) == out[0]["profile"]


def test_read_profile_refuses_when_the_profiler_has_not_run(tree):
    """The call order is the rung, not an ordering this driver chose."""
    with pytest.raises(ArtefactError, match="no profile at"):
        multi.read_profile(**ARM, root=tree)


# ─── the transcribed design table, and the vocabulary it is read against ─────


def test_the_transcribed_table_is_the_loaders_own_map():
    """`DESIGN_MAPPINGS` against `corpora.meddocan`, so a stale copy fails here.

    The complement hazard is the opposite of a stale copy's: a label the table has no row for
    reads as *excluded* rather than as a gap. This closes it from both sides — the mapped half
    must equal `TYPE_MAP`, and the complement over the loader's own labels must equal
    `EXCLUDED_TYPES`.
    """
    table = artefacts.DESIGN_MAPPINGS[CORPUS]
    assert table == meddocan.TYPE_MAP
    complement = {label for label in INVENTORY_LABELS if label not in table}
    assert complement == set(meddocan.EXCLUDED_TYPES)


def test_the_comparison_reports_a_flip_and_names_no_exclusion_concept():
    """One disagreement, and `basis` is not compared.

    §9.1 assigns no concept per source label and `EXCLUDED_TYPES` carries none either, so a
    module pairing them would be writing the answer key — the thing §4.2 refuses. The
    disagreement record therefore says *which side* the agent put the label on and what it
    called it, and nothing about which exclusion concept it chose.
    """
    flipped_map = {**meddocan.TYPE_MAP, "SEXO_SUJETO_ASISTENCIA": {"canonical": "OTHER"}}
    kept_map = {k: {"canonical": v} for k, v in meddocan.TYPE_MAP.items()}
    kept_map["SEXO_SUJETO_ASISTENCIA"] = {"canonical": "OTHER"}
    kept_excluded = {"FAMILIARES_SUJETO_ASISTENCIA": {"excluded_type": "FAMILIARES"}}
    disagreements, compared, applied = artefacts.compare_with_design(
        CORPUS, kept_map, kept_excluded, type_inventory=INVENTORY_LABELS)
    assert applied == artefacts.APPLIED_DESIGN
    assert compared == len(INVENTORY_LABELS)
    assert disagreements == [{"source_type": "SEXO_SUJETO_ASISTENCIA", "agent": "OTHER",
                              "agent_excluded": False, "design": None,
                              "design_excluded": True}]
    assert all("basis" not in row for row in disagreements)
    assert flipped_map      # the fixture above is the same flip, kept for the reader


def test_the_merge_is_read_from_the_design_and_not_named_in_the_module():
    """`source_type_is_coarser`'s admissible targets, gloss-read like `non_target_types()`.

    Pinned rather than left to the reader: this is the one basis whose target set comes from
    prose, and an empty set would silently make every `source_type_is_coarser` entry a
    `basis_mismatch`. `merge_declaring_types()` raises on empty for that reason; this asserts
    the value it reads today.
    """
    assert artefacts.merge_declaring_types() == frozenset({"LOCATION_AREA"})
    assert artefacts._residual_type() == "OTHER"


def test_the_lexicon_target_types_are_a_subset_of_the_phi_type_axis():
    """A subset *declaration*, not a new vocabulary. A module literal would drift silently if a
    `phi_type` were renamed, which is the failure `config/naming.yaml`'s comment names."""
    from src.corpora.base import axis
    assert set(lexicon_target_types()) <= set(axis("phi_type"))
    assert lexicon_target_types() == ("ORGANISATION", "LOCATION_AREA", "LOCATION_STREET")


@pytest.mark.parametrize("block, match", [
    (None, "no `lexicon_target_types` list"),
    ([], "no `lexicon_target_types` list"),
    (["ORGANISATION", "HOSPITAL"], "not.*phi_type values"),
])
def test_a_target_type_that_is_not_a_phi_type_is_refused(monkeypatch, block, match):
    """The refusal that a literal in `src/llm/prompt.py` could not have made.

    Declaring a *subset* of an axis has a failure mode declaring a new vocabulary does not: a
    `phi_type` renamed out from under the list. The rename is the case worth having — it is
    silent, it happens in a different file, and its symptom without this check is an agent shown
    a type the rest of the config has no meaning for and term lists filed under it.
    """
    from src.corpora import base
    real = base.naming()
    patched = {k: v for k, v in real.items() if k != "lexicon_target_types"}
    if block is not None:
        patched["lexicon_target_types"] = block
    monkeypatch.setattr(base, "naming", lambda: patched)
    with pytest.raises(CorpusError, match=match):
        base.lexicon_target_types()


def test_a_declared_lexicon_name_is_a_name_the_loader_can_resolve():
    """The one check that would otherwise fail *after* the arm had spent its authoring call."""
    artefacts.check_lexicon_names()


# ─── the log reader the round-1 guard needed ─────────────────────────────────


def test_roles_called_is_empty_before_anything_and_counts_after(tree):
    """`arm_has_called()` cannot answer this rung's question and this is the narrowest thing
    that can: `port-multi`'s log is non-empty at the moment round 1 is checked."""
    assert orchestrate.roles_called(*ARM.values()) == {}
    _author_all(tree)
    assert orchestrate.arm_has_called(*ARM.values()) is True
    counts = orchestrate.roles_called(*ARM.values())
    assert counts.get(orchestrate.RULE_AUTHOR, 0) == 0
    assert sum(counts.values()) == 3
