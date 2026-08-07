"""Tests for src/sample.py — the error-span draw shared by both porting arms.

What is load-bearing here is not that the draw is *correct* (there is no correct
sample) but that it is a **pure function of (corpus, iteration, error list)**. DESIGN
§11.1 makes `port-human` interpretable only if the two arms drew by the same procedure
at the same iteration, and that claim is checked by a test or it is a hope.

Two properties get their own sections because they fail silently. A seed that depends
on process state is reproducible inside one run, so a same-process test passes; the
subprocess test is what sees it. And a draw that depends on the order the caller
happened to iterate its errors is reproducible from the log — the log records the seed,
which did not change — while the sample differs in fact.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.sample import (                                        # noqa: E402
    ERROR_KINDS, FALSE_POSITIVE, MISSED, ErrorSpan, SamplingError, _allocate,
    config, draw, file_hash, non_target_types, prompt_hash, provenance,
    sample_seed, window_hashes,
)


# ─── fixtures ───────────────────────────────────────────────────────────────
# Span references only: no text anywhere in this file, per CLAUDE.md. The offsets
# are arbitrary and the doc_ids are invented.

def err(doc: str, index: int, phi_type: str = "NAME", kind: str = MISSED,
        start: int | None = None) -> ErrorSpan:
    at = index * 10 if start is None else start
    return ErrorSpan(doc_id=doc, span_index=index, phi_type=phi_type,
                     kind=kind, start=at, end=at + 6)


def pool(n: int, phi_type: str = "NAME", doc: str = "d1") -> list[ErrorSpan]:
    return [err(doc, i, phi_type) for i in range(n)]


def mixed_pool() -> list[ErrorSpan]:
    """A distribution shaped like a real one: two dominant types and a sparse one."""
    out = pool(60, "NAME", "d1") + pool(30, "DATE", "d2")
    out += [err("d3", i, "ID") for i in range(8)]
    out += [err("d4", 0, "PROFESSION")]
    return out


# ─── the seed is a function of the iteration and nothing else ───────────────

def test_the_seed_is_stable_within_a_process():
    assert sample_seed("es-meddocan", 3) == sample_seed("es-meddocan", 3)


def test_the_seed_is_stable_across_processes():
    """The one test that can see a `hash()`-derived or clock-derived seed.

    Python salts string hashing per process, so a seed built on `hash()` is constant
    within a run and different in the next. Every in-process check of determinism
    passes on that implementation, which is why this spawns a fresh interpreter — with
    PYTHONHASHSEED explicitly unset, since a pinned salt would hide the same defect.
    """
    code = ("import sys; sys.path.insert(0, %r);"
            "from src.sample import sample_seed;"
            "print(sample_seed('es-meddocan', 7))" % str(ROOT))
    runs = []
    for _ in range(3):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True, env={"PATH": "/usr/bin:/bin"})
        runs.append(out.stdout.strip())
    assert runs[0] == runs[1] == runs[2]
    assert runs[0] == str(sample_seed("es-meddocan", 7))


def test_different_iterations_give_different_seeds():
    seeds = {sample_seed("es-meddocan", i) for i in range(1, 30)}
    assert len(seeds) == 29


def test_different_corpora_give_different_seeds():
    assert sample_seed("es-meddocan", 4) != sample_seed("de-grascco", 4)


def test_the_seed_does_not_depend_on_call_order():
    """Two orders of the same calls agree — no shared RNG walking between them."""
    first = [sample_seed("es-meddocan", i) for i in (1, 2, 3)]
    _ = [sample_seed("de-grascco", i) for i in (9, 8, 7)]
    second = [sample_seed("es-meddocan", i) for i in (3, 2, 1)][::-1]
    assert first == second


def test_the_seed_does_not_depend_on_the_process_rng():
    import random
    random.seed(1)
    a = sample_seed("es-meddocan", 5)
    random.seed(999)
    [random.random() for _ in range(100)]
    assert sample_seed("es-meddocan", 5) == a


def test_the_corpus_and_iteration_cannot_collide():
    """A delimiter-free join would make ('x-a', 12) and ('x-a1', 2) one seed."""
    assert sample_seed("es-meddocan", 12) != sample_seed("es-meddocan", 1)


def test_an_unknown_corpus_is_refused():
    with pytest.raises(SamplingError, match="not a corpus"):
        sample_seed("es-nonesuch", 1)


def test_iteration_zero_is_refused():
    with pytest.raises(SamplingError, match="integer >= 1"):
        sample_seed("es-meddocan", 0)


def test_a_boolean_iteration_is_refused():
    """`True == 1` in Python, so a bool would otherwise be iteration 1 silently."""
    with pytest.raises(SamplingError):
        sample_seed("es-meddocan", True)


# ─── the draw is a pure function of its inputs ──────────────────────────────

def test_the_draw_is_reproducible():
    errors = mixed_pool()
    assert draw(errors, "es-meddocan", 3, n=40) == draw(errors, "es-meddocan", 3, n=40)


def test_the_draw_does_not_depend_on_input_order():
    """Reversed input, same sample. This is what the `key` sort buys.

    Without it the seed pins the indices and the caller's iteration order pins which
    spans those indices hit — so the log records an unchanged seed while the sample
    changes, which is the worst available combination.
    """
    errors = mixed_pool()
    a = draw(errors, "es-meddocan", 3, n=40)
    b = draw(list(reversed(errors)), "es-meddocan", 3, n=40)
    assert a == b


def test_the_draw_does_not_depend_on_the_process_rng():
    import random
    errors = mixed_pool()
    random.seed(4)
    a = draw(errors, "es-meddocan", 6, n=40)
    random.seed(77)
    [random.random() for _ in range(50)]
    assert draw(errors, "es-meddocan", 6, n=40) == a


def test_the_draw_is_reproducible_across_processes():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from src.sample import draw, ErrorSpan, MISSED\n"
        "errs = [ErrorSpan('d%%d' %% (i %% 3), i, 'NAME' if i %% 2 else 'DATE',\n"
        "                  MISSED, i * 10, i * 10 + 5) for i in range(50)]\n"
        "s = draw(errs, 'es-meddocan', 5, n=12)\n"
        "print([(e.doc_id, e.span_index) for e in s])\n" % str(ROOT)
    )
    runs = []
    for _ in range(2):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True, env={"PATH": "/usr/bin:/bin"})
        runs.append(out.stdout.strip())
    assert runs[0] == runs[1]
    assert runs[0] != "[]"


def test_different_iterations_draw_different_samples():
    errors = mixed_pool()
    samples = {tuple(e.key for e in draw(errors, "es-meddocan", i, n=20))
               for i in range(1, 10)}
    assert len(samples) > 1


def test_two_arms_at_one_iteration_share_the_procedure():
    """The premise DESIGN §11.1 rests on, as a test.

    The arms hold different error lists — that is the experiment — but the seed is a
    function of (corpus, iteration) only, so an arm-dependent seed cannot creep in. It
    is checked by giving the same list twice: if the arm entered the derivation there
    would be no way to call it identically here, and there is.
    """
    agent_errors = mixed_pool()
    human_errors = mixed_pool()[:70]
    assert draw(agent_errors, "es-meddocan", 4, n=20) == draw(
        list(reversed(agent_errors)), "es-meddocan", 4, n=20)
    # And the two arms' samples differ only because their error pools do.
    assert draw(human_errors, "es-meddocan", 4, n=20) == draw(
        list(reversed(human_errors)), "es-meddocan", 4, n=20)


# ─── size and stratification ────────────────────────────────────────────────

def test_the_sample_is_n_when_the_pool_is_larger():
    assert len(draw(mixed_pool(), "es-meddocan", 2, n=40)) == 40


def test_a_small_pool_yields_everything_it_has():
    errors = pool(7)
    assert len(draw(errors, "es-meddocan", 2, n=40)) == 7


def test_an_empty_pool_yields_nothing():
    assert draw([], "es-meddocan", 2, n=40) == []


def test_n_zero_yields_nothing():
    """The DUA case: on a corpus where text may not leave, the agent's n is 0."""
    assert draw(mixed_pool(), "es-carmen", 2, n=0) == []


def test_duplicate_error_references_are_collapsed():
    errors = pool(5) + pool(5)
    assert len(draw(errors, "es-meddocan", 1, n=40)) == 5


def test_every_type_with_an_error_appears():
    """min_per_type: a sparse type is in the sample or it is never fixed."""
    sample = draw(mixed_pool(), "es-meddocan", 3, n=40)
    assert {e.phi_type for e in sample} == {"NAME", "DATE", "ID", "PROFESSION"}


def test_the_sparse_type_appears_at_every_iteration():
    """A uniform draw would miss it in most iterations. That is the whole point."""
    for i in range(1, 12):
        sample = draw(mixed_pool(), "es-meddocan", i, n=40)
        assert any(e.phi_type == "PROFESSION" for e in sample), i


def test_a_dominant_type_does_not_take_the_whole_sample():
    sample = draw(mixed_pool(), "es-meddocan", 3, n=40)
    names = sum(1 for e in sample if e.phi_type == "NAME")
    assert names < 40


def test_allocation_is_roughly_proportional():
    """60 NAME / 30 DATE at n=40 should put NAME above DATE, both well represented."""
    sample = draw(mixed_pool(), "es-meddocan", 3, n=40)
    counts = {t: sum(1 for e in sample if e.phi_type == t)
              for t in ("NAME", "DATE", "ID", "PROFESSION")}
    assert counts["NAME"] > counts["DATE"] > counts["ID"] > 0
    assert counts["PROFESSION"] == 1


def test_no_span_is_drawn_twice():
    sample = draw(mixed_pool(), "es-meddocan", 3, n=40)
    assert len({e.key for e in sample}) == len(sample)


def test_the_sample_is_sorted():
    sample = draw(mixed_pool(), "es-meddocan", 3, n=40)
    assert [e.key for e in sample] == sorted(e.key for e in sample)


def test_false_positives_and_misses_both_appear():
    errors = pool(20, "NAME") + [
        err("d9", i, "NAME", FALSE_POSITIVE, start=1000 + i * 10) for i in range(20)]
    sample = draw(errors, "es-meddocan", 3, n=40)
    assert {e.kind for e in sample} == set(ERROR_KINDS)


def test_a_negative_n_is_refused():
    with pytest.raises(SamplingError, match="non-negative"):
        draw(mixed_pool(), "es-meddocan", 1, n=-1)


# ─── the allocation itself ──────────────────────────────────────────────────

def test_allocation_totals_n():
    got = _allocate({"NAME": 60, "DATE": 30, "ID": 8, "PROFESSION": 1}, 40, 1)
    assert sum(got.values()) == 40


def test_allocation_never_exceeds_a_types_available_errors():
    counts = {"NAME": 3, "DATE": 2}
    got = _allocate(counts, 40, 1)
    assert all(got[t] <= counts[t] for t in got)


def test_allocation_is_independent_of_dict_order():
    a = _allocate({"NAME": 10, "DATE": 10, "ID": 10}, 7, 1)
    b = _allocate({"ID": 10, "NAME": 10, "DATE": 10}, 7, 1)
    assert a == b


def test_allocation_with_more_types_than_draws_drops_the_rarest():
    """n smaller than the number of types: the reserve cannot be met for all."""
    got = _allocate({"NAME": 50, "DATE": 40, "ID": 3, "PROFESSION": 1}, 2, 1)
    assert sum(got.values()) == 2
    assert set(got) == {"NAME", "DATE"}


def test_allocation_ignores_types_with_no_errors():
    got = _allocate({"NAME": 10, "DATE": 0}, 5, 1)
    assert set(got) == {"NAME"}


def test_allocation_of_nothing_is_nothing():
    assert _allocate({}, 40, 1) == {}


# ─── types no rule may target are not sampled ──────────────────────────────

def test_other_is_a_non_target_type():
    """Read from naming.yaml's own gloss, not hardcoded here or in sample.py."""
    assert non_target_types() == frozenset({"OTHER"})


def test_a_non_target_type_is_never_drawn():
    """Prohibition 4 forbids a rule for OTHER, so a slot spent on it is wasted."""
    errors = pool(30, "NAME") + [err("d9", i, "OTHER", start=900 + i * 10)
                                 for i in range(20)]
    for i in range(1, 8):
        sample = draw(errors, "es-meddocan", i, n=20)
        assert all(e.phi_type != "OTHER" for e in sample), i


def test_min_per_type_does_not_smuggle_a_non_target_type():
    """The interaction that makes this load-bearing: min_per_type guarantees one of
    every type with any error, so without the filter OTHER would appear in the window
    of every iteration of every arm."""
    errors = pool(30, "NAME") + [err("d9", 0, "OTHER", start=900)]
    sample = draw(errors, "es-meddocan", 1, n=40)
    assert len(sample) == 30
    assert all(e.phi_type != "OTHER" for e in sample)


def test_a_pool_of_only_non_target_types_yields_nothing():
    errors = [err("d9", i, "OTHER", start=900 + i * 10) for i in range(5)]
    assert draw(errors, "es-meddocan", 1, n=40) == []


# ─── the span reference carries no text ─────────────────────────────────────

def test_error_span_has_no_text_field():
    """A field that could hold a surface form gets filled with one (CLAUDE.md)."""
    fields = set(ErrorSpan.__dataclass_fields__)
    assert fields == {"doc_id", "span_index", "phi_type", "kind", "start", "end"}


def test_an_undeclared_phi_type_is_refused():
    with pytest.raises(SamplingError, match="not a phi_type"):
        ErrorSpan("d1", 0, "PATIENT_SURNAME", MISSED, 0, 5)


def test_an_unknown_error_kind_is_refused():
    with pytest.raises(SamplingError, match="not an error kind"):
        ErrorSpan("d1", 0, "NAME", "leaked", 0, 5)


def test_a_reversed_span_is_refused():
    with pytest.raises(SamplingError):
        ErrorSpan("d1", 0, "NAME", MISSED, 9, 4)


def test_sampling_messages_carry_no_surface():
    """The convention test CLAUDE.md requires: offsets and indices, never text."""
    with pytest.raises(SamplingError) as exc:
        ErrorSpan("d1", 3, "NAME", MISSED, 40, 12)
    message = str(exc.value)
    assert "40" in message and "12" in message
    assert "No surface form is quoted" in message


# ─── what is recorded ───────────────────────────────────────────────────────

def test_provenance_is_json_serialisable():
    json.dumps(provenance("es-meddocan", 3))


def test_provenance_records_the_seed_as_a_value():
    """Recording the inputs only would agree with any derivation scheme."""
    rec = provenance("es-meddocan", 3)
    assert rec["seed"] == sample_seed("es-meddocan", 3)


def test_provenance_records_the_parameters():
    rec = provenance("es-meddocan", 3)
    cfg = config()
    assert rec["n_error_spans"] == cfg["n_error_spans"]
    assert rec["context_chars"] == cfg["context_chars"]
    assert rec["min_per_type"] == cfg["min_per_type"]
    assert rec["seed_scheme"] == cfg["seed_scheme"]
    assert rec["stratified_by"] == "phi_type"


def test_provenance_records_an_overridden_n():
    """The DUA case has to be visible in the results, not inferred from silence."""
    assert provenance("es-carmen", 3, n=0)["n_error_spans"] == 0


def test_the_config_holds_the_parameters_the_prompt_states():
    """n = 40 and ±120 characters are in the config, not in prose only."""
    cfg = config()
    assert cfg["n_error_spans"] == 40
    assert cfg["context_chars"] == 120
    assert cfg["min_per_type"] == 1


# ─── the prompt hash ────────────────────────────────────────────────────────

def test_prompt_hash_is_stable():
    assert prompt_hash() == prompt_hash()


def test_a_hash_moves_when_the_content_moves(tmp_path):
    """An uncommitted edit must move it — that is the event it exists to catch."""
    a = tmp_path / "p.md"
    a.write_text("one", encoding="utf-8")
    first = prompt_hash(str(a))
    a.write_text("two", encoding="utf-8")
    assert prompt_hash(str(a)) != first


def test_window_hashes_name_both_files():
    """n and the context width live in the config, not the prompt (DESIGN §11.2)."""
    got = window_hashes()
    assert set(got) == {"prompt_sha256", "sampling_sha256"}
    assert got["prompt_sha256"] == file_hash("docs/prompts/rule_author.md")
    assert got["sampling_sha256"] == file_hash("config/sampling.yaml")


def test_the_two_window_hashes_are_distinct():
    """One hash for both files would be a record that cannot say which changed."""
    got = window_hashes()
    assert got["prompt_sha256"] != got["sampling_sha256"]


def test_window_hashes_are_json_serialisable():
    json.dumps(window_hashes())


def test_prompt_hash_is_labelled_with_its_algorithm():
    assert prompt_hash().startswith("sha256:")


def test_the_prompt_template_exists_where_the_hash_looks():
    assert (ROOT / "docs" / "prompts" / "rule_author.md").is_file()
