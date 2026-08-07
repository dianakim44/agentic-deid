#!/usr/bin/env python3
"""Mutation checks for the corpus loaders. Not run by pytest.

Each mutation breaks one loader guarantee and the suite must notice. A test that
never fails proves nothing, so this is what licenses the claim that the loader
tests are load-bearing rather than decorative.

    python3 tests/mutations/run.py              # all mutations
    python3 tests/mutations/run.py --list       # names and anchors only
    python3 tests/mutations/run.py utf8_sig     # one mutation

Exit status is 0 when every mutation is caught by at least the expected number of
tests, 1 otherwise. See README.md for the table and for why this is code rather
than prose.

Mechanics: the repository's `src/`, `tests/` and `config/` are copied to a
temporary directory and `data/` is symlinked, so a mutation is applied to a
throwaway tree and never to the working copy. Interrupting the run cannot leave a
mutated file behind.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: All four suites. The split file is part of loading now — a mutation that
#: corrupts the folds must be able to be caught by the tests that check them — the
#: seal tests are what catch a gate bypass, and the release screener is the seal's
#: other half: it is what notices a sealed fold on its way into a public commit.
TEST_FILES = [
    "tests/test_meddocan_loader.py",
    "tests/test_split_file.py",
    "tests/test_seal.py",
    "tests/test_release_screen.py",
    "tests/test_layer_families.py",
    "tests/test_scorer.py",
    "tests/test_sample.py",
    "tests/test_human_arm.py",
    "tests/test_show_human_window.py",
]

#: Repository directories the loader tests need. `splits/` is here because the
#: loader reads `splits/{corpus}.json` to assign folds; without it every test
#: errors on a missing split file and the baseline is not green. `results/` is here
#: for `sealed_eval_log.md`: the gate refuses to run when the log is absent, so
#: without it every seal test would be caught by the wrong guard. `tools/` is here
#: for the release screener, which is mutated like any other guard.
#: `docs/` is here for one reason: `prompt_hash()` hashes
#: `docs/prompts/rule_author.md`, and DESIGN §11.1 records that hash in
#: `human_log.jsonl` to identify the window a `port-human` run was held to. Without
#: the file the hash tests error rather than fail, and errors count as kills — so
#: every sampling mutation would be reported as caught by three tests that never ran.
COPIED = ("src", "tests", "config", "splits", "results", "tools", "docs")

#: Single files copied alongside COPIED. `.gitignore` is one half of a rule the
#: screener encodes twice (DENY_EXCEPTIONS is the other), and
#: `test_gitignore_matches_deny_exceptions` checks the two agree — without the file
#: that test measures nothing. `CLAUDE.md` is named by the screener's allowlist, so
#: leaving it out makes the entry read as stale here and nowhere else — a tree that
#: differs from the real one in what the tests measure is worse than no tree.
COPIED_FILES = (".gitignore", "CLAUDE.md")


@dataclass(frozen=True)
class Mutation:
    """One way of breaking the loader.

    `anchor` is text that must be present in `path` for the mutation to mean what
    it says. A mutation whose anchor has vanished is reported as STALE rather than
    silently applying nothing — otherwise a refactor turns this harness into a
    file of no-ops that reports every mutation as caught.
    """

    name: str
    path: str
    anchor: str
    replacement: str
    breaks: str
    min_kills: int
    #: extra (path, anchor, replacement) edits, for mutations that are only
    #: faithful to their name when two places change together
    also: tuple[tuple[str, str, str], ...] = ()

    def apply(self, tree: Path) -> None:
        """Edit the tree, then verify the edit is one that could have had an effect.

        Three things are checked after every write, because a harness that miscounts
        its own failures as successes is worse than no harness — it reports green.
        See "Verifying the mutation" in README.md.
        """
        for path, anchor, replacement in (
            (self.path, self.anchor, self.replacement),
            *self.also,
        ):
            target = tree / path
            source = target.read_text(encoding="utf-8")

            # 1. The anchor exists. Otherwise the mutation is a no-op that would be
            #    reported as caught by however many tests happen to be failing.
            if anchor not in source:
                raise StaleMutation(
                    f"{self.name}: anchor not found in {path}. The code was "
                    "refactored; update the anchor here so the check keeps "
                    "testing what its name claims."
                )
            mutated = source.replace(anchor, replacement, 1)

            # 2. The file actually changed. An anchor equal to its replacement, or a
            #    replacement edited to match after a copy-paste, passes check 1 and
            #    mutates nothing.
            if mutated == source:
                raise StaleMutation(
                    f"{self.name}: the anchor was found in {path} but the file is "
                    "unchanged — the replacement is identical to the anchor. A "
                    "no-op mutation is caught by whatever was already failing."
                )
            target.write_text(mutated, encoding="utf-8")

            # 3. The result is still a runnable module. A SyntaxError takes out every
            #    test in the file at collection time, which pytest reports as errors
            #    and `kills()` counts — an emphatic pass for a mutation that never
            #    ran. Anchors are indentation-blind, so this is what notices.
            if path.endswith(".py"):
                import ast

                try:
                    ast.parse(mutated)
                except SyntaxError as exc:
                    raise StaleMutation(
                        f"{self.name}: {path} does not parse after the mutation "
                        f"({exc.msg} at line {exc.lineno}). The anchor probably "
                        "matched at the wrong indentation — a mutation that cannot "
                        "run is not a mutation that was caught."
                    ) from exc


class StaleMutation(Exception):
    pass


class BrokenSuite(Exception):
    """The suite did not run, as opposed to running and failing.

    Distinct from StaleMutation because the diagnosis differs: a stale mutation is
    fixed by updating an anchor, a broken suite means the mutated tree cannot be
    collected at all and the reported kill count describes nothing.
    """


BASE = "src/corpora/base.py"
MEDDOCAN = "src/corpora/meddocan.py"
SPLIT = "src/split.py"
SPLIT_FILE = "splits/es-meddocan.json"
SEALED_LOG = "src/eval/sealed_log.py"
RUN_SEALED = "src/eval/run_sealed_eval.py"
SCREEN = "tools/release_screen.py"
SCORER = "src/eval/scorer.py"
SAMPLE = "src/sample.py"
HUMAN_ARM = "src/porting/human_arm.py"
SHOW_WINDOW = "tools/show_human_window.py"

MUTATIONS = [
    Mutation(
        name="utf8_sig",
        path=MEDDOCAN,
        anchor='raw = txt_path.read_text(encoding="utf-8")',
        replacement='raw = txt_path.read_text(encoding="utf-8-sig")',
        breaks=(
            "Reads the text with utf-8-sig, so the BOM is removed at decode time "
            "and strip_bom finds nothing to shift. Every offset in the 32 BOM "
            "files is then one character too high. This is the mistake DESIGN "
            "§9.7 exists to prevent, and it changes no total."
        ),
        min_kills=23,
    ),
    Mutation(
        name="no_bom_shift",
        path=MEDDOCAN,
        anchor="            start=start - shift,\n            end=end - shift,",
        replacement="            start=start,\n            end=end,",
        breaks=(
            "Strips the BOM but leaves the offsets alone — the same one-character "
            "error as utf8_sig, reached from the other direction."
        ),
        min_kills=23,
    ),
    Mutation(
        name="assert_offsets_noop",
        path=BASE,
        anchor="        for i, span in enumerate(self.spans):",
        replacement="        return\n        for i, span in enumerate(self.spans):",
        breaks=(
            "Turns the §9.7 assertion into a no-op. Nothing about the counts "
            "changes, so only tests that slice spans themselves can notice."
        ),
        min_kills=3,
    ),
    Mutation(
        name="drop_excluded",
        path=BASE,
        anchor="            docs = list(self._read())",
        replacement=(
            "            docs = list(self._read())\n"
            "            for _d in docs:\n"
            "                _d.spans = [_s for _s in _d.spans if not _s.excluded]"
        ),
        breaks=(
            "Discards the §9.1 spans instead of flagging them. The canonical "
            "count stays right at 20,538 and the reported exclusion volume "
            "silently becomes unmeasurable."
        ),
        min_kills=11,
    ),
    Mutation(
        name="familiares_as_other",
        path=MEDDOCAN,
        # Moves the type from the excluded set into the map, rather than adding it
        # to both — being in both is a different fault, checked separately below.
        anchor=(
            '    "OTROS_SUJETO_ASISTENCIA": "OTHER",\n'
            "}"
        ),
        replacement=(
            '    "OTROS_SUJETO_ASISTENCIA": "OTHER",\n'
            '    "FAMILIARES_SUJETO_ASISTENCIA": "OTHER",\n'
            "}"
        ),
        breaks=(
            "Maps an excluded type into a canonical one. Every span is still "
            "loaded and every total still reconciles to 22,795 — the corruption "
            "is entirely in which spans are scored."
        ),
        min_kills=7,
        also=(
            (
                MEDDOCAN,
                '        "FAMILIARES_SUJETO_ASISTENCIA",\n',
                "",
            ),
        ),
    ),
    Mutation(
        name="type_in_both_lists",
        path=MEDDOCAN,
        anchor='    "OTROS_SUJETO_ASISTENCIA": "OTHER",',
        replacement=(
            '    "OTROS_SUJETO_ASISTENCIA": "OTHER",\n'
            '    "FAMILIARES_SUJETO_ASISTENCIA": "OTHER",'
        ),
        breaks=(
            "Leaves a type in both type_map and excluded_types, which "
            "_check_type_map rejects at construction. Added after the harness "
            "found that this case was being reported as a skip: the loader "
            "fixture caught every CorpusError and could not tell a real bug from "
            "an absent corpus. Kept as its own mutation so that regression stays "
            "covered."
        ),
        min_kills=2,
    ),
    Mutation(
        name="missing_test_fold",
        path=MEDDOCAN,
        anchor='{"train": "train", "dev": "dev", "test": "test"}',
        replacement='{"train": "train", "dev": "dev"}',
        breaks=(
            "Drops the test fold from `fold_dirs`. Before the seal this made the "
            "corpus silently 750 documents instead of 1,000 and 16 tests failed. "
            "Now 750 is the correct unsealed figure, so the count-based tests "
            "cannot see it — what remains visible is that an *authorised* sealed "
            "read would return no sealed documents while the log records a "
            "completed test evaluation. That is the sharper fault and the one the "
            "gate now raises on."
        ),
        # Lowered from 16 deliberately, and the reason is recorded above rather
        # than in a commit message: the coverage did not decay, the corpus stopped
        # being fully readable. Raising the number by adding a test that recounted
        # 1,000 documents would mean reading the sealed fold from the suite.
        min_kills=1,
    ),
    Mutation(
        name="bucket_unknown_types",
        path=BASE,
        anchor='        raise CorpusError(\n            f"{self.corpus_id}: annotation type',
        replacement=(
            '        return "OTHER", False\n'
            '        raise CorpusError(\n            f"{self.corpus_id}: annotation type'
        ),
        breaks=(
            "Sends an unmapped type to OTHER instead of raising. Invisible on "
            "today's corpus, and on the day a re-release adds a type it would "
            "quietly score that type as a residual bucket."
        ),
        min_kills=1,
    ),
    # ── the split file (DESIGN §9.6, CLAUDE.md's seal) ──────────────────────
    Mutation(
        name="split_verify_noop",
        path=SPLIT,
        anchor="    corpus_id = record[\"corpus\"]\n",
        replacement="    return\n    corpus_id = record[\"corpus\"]\n",
        breaks=(
            "Turns verify() into a no-op, so the split file's recorded summaries "
            "stop being checked against the corpus. The file would then be a "
            "comment rather than a claim, and a stale one would pass silently."
        ),
        min_kills=1,
    ),
    Mutation(
        name="split_ignores_membership",
        path=SPLIT,
        anchor="        ids_file = sorted(block[\"document_ids\"])",
        replacement=(
            "        return\n        ids_file = sorted(block[\"document_ids\"])"
        ),
        breaks=(
            "verify() stops comparing fold membership and checks only the "
            "aggregate counts. A file that swapped one dev document for one test "
            "document of the same span count would then verify — the exact shape "
            "of seal violation the counts cannot see."
        ),
        min_kills=1,
    ),
    Mutation(
        name="fold_from_directory_not_file",
        path=BASE,
        anchor="        if self.use_split_file:\n            self._apply_split_file(docs)",
        replacement="        pass",
        breaks=(
            "Documents keep the fold their directory implies and the frozen split "
            "file is never read. Every count still matches, because MEDDOCAN's "
            "directories and its split file agree today — the guarantee lost is "
            "that the file, not the disk layout, decides what is sealed."
        ),
        min_kills=1,
    ),
    Mutation(
        name="split_disagreement_ignored",
        path=BASE,
        anchor="            if doc.split is not None and doc.split != fold:",
        replacement="            if False:",
        breaks=(
            "The cross-check between the corpus's own fold and the frozen file is "
            "dropped, so the file silently overrides the disk. A re-release that "
            "moved a document out of test would be accepted without a word."
        ),
        min_kills=1,
    ),
    Mutation(
        name="top_level_leak_allowed",
        path=SPLIT,
        anchor="    extra = sorted(set(record) - REQUIRED_TOP_LEVEL)",
        replacement="    extra = []",
        breaks=(
            "Corpus-specific fields may then sit at the top level next to the "
            "common ones. Nothing fails today; the schema stops being shared the "
            "first time GraSCCo's generator adds its own key."
        ),
        min_kills=1,
    ),
    Mutation(
        name="grouping_numeric_suffix_only",
        path=SPLIT,
        anchor=r'STEM_RE = re.compile(r"^(?P<stem>.+)[-_](?P<suffix>[0-9]+|[A-Za-z]+)$")',
        replacement=(
            r'STEM_RE = re.compile(r"^(?P<stem>S\d{4}-\d+)-(?P<suffix>\d+)$")'
        ),
        breaks=(
            "Restores the digits-only stem rule that DESIGN §9.5 records as a past "
            "bug: it drops the 31 MEDDOCAN ids whose journal prefix contains a "
            "letter, so the grouping audit silently covers 969 of 1,000 documents "
            "and reports itself as complete."
        ),
        min_kills=1,
    ),
    Mutation(
        name="grouping_name_only",
        path=SPLIT,
        anchor=(
            '    return bool(shared["name"]) and '
            'bool(shared["record"] or shared["date"])'
        ),
        replacement='    return bool(shared["name"])',
        breaks=(
            "Weakens §9.5 step 2 to a name match alone, which groups the one stem "
            "sharing a bare given name with different surnames. One group forms "
            "where none should, and two documents stop being independent units. "
            "The one discriminating stem straddles the seal, so this is caught by "
            "applying the rule to the counts the split file records rather than by "
            "recounting the corpus — which is why `step_2_confirms` takes counts."
        ),
        min_kills=1,
    ),
    Mutation(
        name="split_file_span_count",
        path=SPLIT_FILE,
        anchor='"n_spans": 5801,',
        replacement='"n_spans": 5800,',
        breaks=(
            "Edits the committed split file rather than the code — a stale "
            "summary, which is what happens in practice when a corpus is "
            "re-released and the file is not regenerated. The recount must catch "
            "it. Direction reversed from the other mutations: the artefact is the "
            "suspect and the code is the check."
        ),
        min_kills=1,
    ),
    # ─── the seal (DESIGN §6) ───────────────────────────────────────────────
    # These are the mutations that matter most, because the seal is the one
    # guarantee whose violation cannot be detected after the fact: a test fold that
    # was looked at cannot be un-looked-at, and no downstream number reveals it.
    Mutation(
        name="sealed_callable_from_anywhere",
        path=BASE,
        anchor="        if SEALED_CALLER not in callers:",
        replacement="        if False:",
        breaks=(
            "Removes the caller check, so `load(sealed=True)` works from any "
            "module — an interactive session, a notebook, a rule-development "
            "script. The physical move still stands, so the fold is only reachable "
            "through this call; that is exactly why the gate has to hold. Note the "
            "log append survives this mutation, which is the point of having both: "
            "a bypass here still leaves a trace, and the trace is what makes it "
            "recoverable rather than merely wrong."
        ),
        min_kills=2,
    ),
    Mutation(
        name="log_append_disabled",
        path=BASE,
        anchor="        record_access(self.corpus_id, purpose=purpose, arms=arms)",
        replacement=(
            "        try:\n"
            "            record_access(self.corpus_id, purpose=purpose, arms=arms)\n"
            "        except Exception:\n"
            "            pass"
        ),
        breaks=(
            "Swallows a failed append, so an evaluation proceeds unlogged. The "
            "numbers are then real and the log says the test fold was never opened "
            "— the failure mode CLAUDE.md's 'evaluated N times' requirement exists "
            "to prevent, and the only one where the artefact actively misleads "
            "rather than merely omits. The counterpart of the mutation above: this "
            "one leaves no trace, which is why neither guard is sufficient alone."
        ),
        min_kills=2,
    ),
    Mutation(
        name="sealed_flag_not_cleared",
        path=BASE,
        anchor="        finally:\n            # Cleared even on failure",
        replacement="        finally:\n            pass\n        if False:\n            # Cleared even on failure",
        breaks=(
            "`_sealed_ok` survives the call that set it, so one authorised "
            "evaluation leaves that loader object permanently able to reach the "
            "sealed fold. Every subsequent ordinary `load()` on it silently "
            "includes the test fold, with no second log row — 250 documents appear "
            "in a dev number and nothing says so."
        ),
        min_kills=1,
    ),
    Mutation(
        name="sealed_root_falls_back_to_corpus",
        path=BASE,
        anchor="    raw = mapping.get(corpus_id)\n    if not raw:\n        return None\n    return _resolve(raw, corpus_id)",
        replacement=(
            "    raw = mapping.get(corpus_id)\n"
            "    if not raw:\n"
            "        return corpus_root(corpus_id)\n"
            "    return _resolve(raw, corpus_id)"
        ),
        breaks=(
            "A corpus with no `sealed:` entry resolves to its ordinary root, so "
            "`fold_roots()` treats unsealed data as sealed and a 'sealed "
            "evaluation' reads and logs it as a test run. Worse than a refusal: "
            "the log row is indistinguishable from a real evaluation, so the count "
            "the paper reports becomes wrong in the direction that flatters it."
        ),
        min_kills=1,
    ),
    Mutation(
        name="unsealed_load_filters_instead_of_not_reaching",
        path=BASE,
        anchor="                if not self._sealed_ok:\n                    continue\n                roots[fold_dir] = sealed",
        replacement=(
            "                roots[fold_dir] = sealed\n"
            "                if not self._sealed_ok:\n"
            "                    pass"
        ),
        breaks=(
            "`fold_roots()` hands out the sealed path unconditionally, so the "
            "sealed fold is read and then discarded downstream instead of never "
            "being opened. Every count still comes out right — `_apply_split_file` "
            "and `_assert_no_sealed_fold` are what notice — but the test fold's "
            "text has been read into memory on every ordinary load, unlogged. The "
            "distinction this defends is that the seal is a path that is not known, "
            "not a filter that is applied."
        ),
        min_kills=1,
    ),
    Mutation(
        name="staged_sealed_not_escalated",
        path=SCREEN,
        anchor="    blocked = [p for p in denied if visible(p)]",
        replacement=(
            "    blocked = [p for p in denied\n"
            "               if visible(p) and not p.startswith(SEALED_PREFIX)]"
        ),
        breaks=(
            "Exempts sealed paths from BLOCKED — the plausible reading of 'SEALED is "
            "expected, so it should not block a commit', and wrong: what is expected "
            "is a sealed fold git *cannot* see. With this, a staged sealed file is in "
            "neither list, the screener exits 0, and the fold goes into a public "
            "commit with the output saying nothing. This is the failure mode the "
            "separate SEALED line introduces, so it is the one that gets a mutation."
        ),
        min_kills=2,
    ),
    Mutation(
        name="sealed_exempt_from_exit_code",
        path=SCREEN,
        anchor="    if blocked or unexpected:\n        sys.exit(1)",
        replacement="    if unexpected:\n        sys.exit(1)",
        breaks=(
            "The exit status stops depending on BLOCKED. Kept as its own mutation "
            "because the SEALED change moved exactly this line's meaning: SEALED must "
            "not affect the exit code and BLOCKED must, and an edit that got the first "
            "half right could get the second half wrong in the same breath. The anchor "
            "read `blocked or suspect` until the allowlist split SUSPECT three ways — "
            "reported STALE on the first run afterwards, which is the anchor check "
            "earning its place: the old text was gone and the mutation was silently "
            "testing nothing."
        ),
        min_kills=1,
    ),
    Mutation(
        name="layer_family_union_becomes_subset",
        path=BASE,
        anchor="    missing = sorted(layers - set(assigned))",
        replacement="    missing = sorted(set(assigned) - layers)",
        breaks=(
            "The union check becomes a subset check in one direction only — the "
            "plausible reading of 'make sure every declared member is a real layer', "
            "which sounds like the whole job and is half of it. A layer added to the "
            "`layer` axis and left out of every family then validates, and every span "
            "it emits is counted as `neither` in the complementarity breakdown: "
            "indistinguishable in the output from spans that genuinely nothing found. "
            "The number is wrong, the arithmetic still adds up, and there is no "
            "symptom anywhere — which is why the check is a union comparison rather "
            "than the subset one it is easy to mistake it for."
        ),
        min_kills=1,
    ),
    Mutation(
        name="allowlist_may_name_corpus_paths",
        path=SCREEN,
        anchor='        if p.startswith("data/") or p.startswith(SEALED_PREFIX):',
        replacement='        if False:',
        breaks=(
            "The allowlist stops refusing corpus and sealed paths. What survives is "
            "the `deny(p)` check below, which covers most of them — so the mutation "
            "looks harmless right up to `data/README.md`, the one file published out "
            "of a denied prefix. That path is not denied, so with this edit a "
            "four-line JSON entry silences the content sniffer on a file inside the "
            "corpus tree, and note text pasted into it is published clean. The "
            "guarantee is that the allowlist can only ever excuse a file the path "
            "rules already publish, never widen what they publish."
        ),
        min_kills=1,
    ),
    Mutation(
        name="filled_prompt_paths_allowed",
        path=SCREEN,
        anchor='    r"(^|/)prompts?/(filled|rendered)/",',
        replacement='    r"(^|/)prompts?/(filled|rendered)/DOES_NOT_OCCUR/",',
        breaks=(
            "A filled RuleAuthor prompt stops being denied by the directory pattern. "
            "The remaining three patterns catch instances named for the convention, so "
            "the mutation looks harmless until an instance lands where the harness "
            "would naturally write it — `prompts/filled/iter03.md` — and that file "
            "holds the ±120-character context of every dev error span it was built "
            "from, which on a DUA corpus is note text (rule_author.md §7). The "
            "screener then reads it as an ordinary publishable file under "
            "`prompts/`, an ALLOW_HINTS prefix, so it is not merely unblocked: it is "
            "reported clean. Worth a separate mutation from the general deny-list "
            "checks because the convention here is 'never written to disk' rather "
            "than 'never committed', and these patterns are deliberately absent from "
            "`.gitignore` so that an instance on disk is BLOCKED rather than "
            "Quarantined — a gitignore entry would downgrade it to a summary line "
            "and exit 0."
        ),
        min_kills=1,
    ),
    Mutation(
        name="rule_id_vocabulary_not_checked",
        path=SCREEN,
        anchor="        unknown = [p for p in parts\n"
               "                   if p.lower() not in RULE_ID_VOCAB",
        replacement="        unknown = [p for p in parts\n"
                    "                   if False",
        breaks=(
            "The vocabulary check is removed and only the shape rules survive, which "
            "is the version this screener was first written as. It passes every "
            "legitimate mechanism name, so nothing looks wrong — and it also passes "
            "`es:perez_ruiz`, because `perez_ruiz` and `street_type` are both two "
            "lowercase ASCII tokens with no digits and no property of the string "
            "separates them. A rule name is published twice: in `rules/*.yaml` and "
            "again in every `metrics.json` `by_rule` block (DESIGN §9.3), so this is "
            "the bypass rule_author.md Prohibition 2 names — forbidding surface forms "
            "in patterns while leaving the free-text name the agent writes "
            "unchecked. The mutation is the argument for a positive vocabulary: the "
            "alternative that would catch this case by shape does not exist, and the "
            "alternative that would catch it by blacklist means storing the surname "
            "in the repository."
        ),
        min_kills=1,
    ),
    Mutation(
        name="greedy_allows_reuse",
        path=SCORER,
        anchor="        if gi in matched or pi in used:",
        replacement="        if gi in matched:",
        breaks=(
            "One prediction may be assigned to several gold spans, so the matching "
            "stops being one-to-one. A single wide prediction spanning two adjacent "
            "gold names then collects credit for both, and recall rises for producing "
            "one coarse span instead of two correct ones — the detector is rewarded "
            "for being less precise about boundaries. DESIGN §9.3 keeps the "
            "assignment one-to-one for exactly this reason, and the fixture that "
            "sees it is D1, the `[Juan][Pérez]` geometry the section is written "
            "around."
        ),
        min_kills=1,
    ),
    Mutation(
        name="fully_covered_is_relaxed",
        path=SCORER,
        anchor="    if mode == FULLY_COVERED:\n        return _covered_length(mark, union) == mark.length",
        replacement="    if mode == FULLY_COVERED:\n        return _covered_length(mark, union) > 0",
        breaks=(
            "The strict mode collapses into the lower bound: a gold span with one "
            "character covered counts as hidden. The leak rate is the headline "
            "quantity of this project and it is reported as `fully_covered`, so this "
            "edit makes the headline number a partial-overlap count while still "
            "labelling it `fully_covered`. Both modes then agree everywhere, which is "
            "the visible symptom — `leak_rate` and `leak_rate_lower_bound` become "
            "equal, and a bound equal to its estimate is not a bound."
        ),
        min_kills=1,
    ),
    Mutation(
        name="leak_rate_from_assignment",
        path=SCORER,
        anchor='            "leaked": len(leaked),',
        replacement='            "leaked": fn,',
        breaks=(
            "The leak rate is computed from the one-to-one assignment's false "
            "negatives instead of from coverage. This is the specific error DESIGN "
            "§9.3 was written to prevent: the assignment correctly denies credit for "
            "the second of two adjacent gold spans under one wide prediction, so a "
            "leak rate read off it reports a disclosed identifier in a document where "
            "every character of both identifiers is hidden. It over-reports leaks "
            "wherever the detector's span boundaries group differently from the gold "
            "guideline, and that gap has its own honest name in the output: "
            "`assignment_slack`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="greedy_tiebreak_dropped",
        path=SCORER,
        anchor=(
            "            candidates.append(\n"
            "                (-_overlap(g, p), g.start, g.end, p.start, p.end, pi, gi)\n"
            "            )"
        ),
        replacement=(
            "            candidates.append(\n"
            "                (-_overlap(g, p), pi, gi)\n"
            "            )"
        ),
        breaks=(
            "The sort key stops being a total order over span geometry: ties in "
            "overlap fall through to the emission index, so which of two equally "
            "overlapping predictions wins depends on the order the detector happened "
            "to return them in. Every metric downstream of the assignment then moves "
            "when the same spans arrive shuffled, which makes the numbers "
            "unreproducible without anything looking wrong in a single run — the "
            "output is self-consistent every time and different between times. Caught "
            "by scoring one input twice in different orders and comparing the whole "
            "result, since no single run can show it."
        ),
        min_kills=1,
    ),
    Mutation(
        name="by_rule_fp_from_coverage",
        path=SCORER,
        anchor=(
            "        matched_keys = frozenset(\n"
            "            (distinct[pi].start, distinct[pi].end, distinct[pi].phi_type)\n"
            "            for pi in matched.values()\n"
            "        )"
        ),
        replacement=(
            "        matched_keys = frozenset(\n"
            "            (p.start, p.end, p.phi_type) for p in distinct\n"
            "            if any(g.phi_type == p.phi_type and _overlap(g, p) > 0\n"
            "                   for g in pair.gold)\n"
            "        )"
        ),
        breaks=(
            "Per-rule false positives are computed from coverage instead of from the "
            "assignment: a rule's span counts as a hit whenever it overlaps a gold "
            "span of the right type, whether or not the assignment gave it the credit. "
            "The rule this hides is the one an author most needs to see — a rule whose "
            "spans always lose the assignment to a better-overlapping prediction "
            "contributes nothing but noise, and under coverage-based attribution it "
            "reads as harmless. Since `by_rule` exists so that a rule file can shrink "
            "rather than only grow (docs/prompts/rule_author.md §1.3), this mutation "
            "removes the one signal that licenses a deletion, while every aggregate in "
            "the file stays correct. Caught by D2, where the cue rule's span helps hide "
            "the identifier and still loses the credit."
        ),
        min_kills=1,
    ),
    Mutation(
        name="sample_seed_from_process_hash",
        path=SAMPLE,
        anchor='    return int.from_bytes(\n'
               '        hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")',
        replacement='    return abs(hash(material))',
        breaks=(
            "The sample seed is derived from Python's `hash()` instead of SHA-256. "
            "Python salts string hashing per process, so the seed is perfectly stable "
            "within one run and different in the next — and every in-process check of "
            "determinism passes, including checking the seed twice, checking it after "
            "reseeding `random`, and comparing two arms' seeds. The recorded seed then "
            "documents a draw nobody can repeat, which is worse than recording nothing: "
            "DESIGN §11.1 makes `port-human` interpretable only if both arms drew by the "
            "same procedure at the same iteration, and here the procedure is not even "
            "reproducible with itself. Caught only by the subprocess tests, which is why "
            "they spawn a fresh interpreter rather than call the function twice."
        ),
        min_kills=1,
    ),
    Mutation(
        name="sample_pool_not_sorted",
        path=SAMPLE,
        anchor=("    pool = sorted((e for e in set(errors) if e.phi_type not in "
                "blocked),\n                  key=lambda e: e.key)"),
        replacement=("    pool = [e for e in dict.fromkeys(errors) "
                     "if e.phi_type not in blocked]"),
        breaks=(
            "The error pool keeps the caller's iteration order instead of a canonical "
            "one. The seed still pins which indices are drawn, so the sample is "
            "reproducible from the log and the recorded seed is unchanged — while the "
            "spans those indices land on move whenever the caller builds its error list "
            "differently. Two arms at the same iteration would then differ for a reason "
            "that is neither the seed nor their errors, and nothing in the results says "
            "so. Deduplication survives the edit, so the sample size stays right."
        ),
        min_kills=1,
    ),
    Mutation(
        name="non_target_filter_removed",
        path=SAMPLE,
        anchor=('    return frozenset(\n'
                '        name for name, gloss in axis("phi_type").items()\n'
                '        if isinstance(gloss, str) and "not a rule-development target" '
                'in gloss)'),
        replacement="    return frozenset()",
        breaks=(
            "Nothing is excluded from the draw, so `OTHER` takes a slot in the window. "
            "With `min_per_type: 1` that is not an occasional accident but one slot in "
            "every iteration of every arm, handed to a type naming.yaml declares is not "
            "a rule-development target and rule_author.md Prohibition 4 forbids writing "
            "a rule for. The sample still holds n spans with every type represented, so "
            "it looks well-formed; the cost is a permanently wasted slot and an author "
            "invited to break a prohibition. This is the defect that shipped: it was "
            "found by reading the iteration-1 distribution, not by any test."
        ),
        min_kills=1,
    ),
    Mutation(
        name="non_target_types_hardcoded_not_read_from_config",
        path=SAMPLE,
        anchor=('    return frozenset(\n'
                '        name for name, gloss in axis("phi_type").items()\n'
                '        if isinstance(gloss, str) and "not a rule-development target" '
                'in gloss)'),
        replacement='    return frozenset({"OTHER"})',
        breaks=(
            "The exclusion stops being read from naming.yaml and becomes a literal. "
            "**Nothing breaks today** \u2014 `OTHER` is the only non-target type, so this "
            "and the real implementation are indistinguishable on the current config, "
            "and it is a shorter and more obvious-looking line. The defect is latent: "
            "the day a corpus ships a second residual bucket, that type is declared in "
            "naming.yaml, glossed as not a rule-development target, forbidden by "
            "Prohibition 4 \u2014 and drawn anyway, in every iteration of every arm, with "
            "the config and the code both looking correct in isolation. Caught only by "
            "a test that declares a second such type, which is why "
            "`a_second_non_target_type` exists as a fixture."
        ),
        min_kills=1,
    ),
    Mutation(
        name="initial_pool_excludes_train_instead_of_selecting_dev",
        path=HUMAN_ARM,
        anchor='        if doc.split != "dev":',
        replacement='        if doc.split == "train":',
        breaks=(
            "The iteration-1 error pool becomes every non-train document, so the test "
            "fold enters the window a person writes rules from — the seal violation "
            "CLAUDE.md says invalidates the whole experiment, arriving as a plausible "
            "spelling of the same filter. It leaves no trace in the log: the sample is "
            "the right size, the provenance is right, and the extra spans are "
            "indistinguishable from dev ones in a summary reporting counts by type. In "
            "this repository the sealed loader would refuse first, but the harness must "
            "not be the layer that depends on that."
        ),
        min_kills=1,
    ),
    Mutation(
        name="human_log_allowed_under_any_arm",
        path=SCREEN,
        anchor=r'    r"^results/[^/]+/[^/]+/[^/]+/port-human/human_log\.jsonl$",',
        replacement=r'    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/human_log\.jsonl$",',
        breaks=(
            "The porting component of the allowed path becomes a wildcard, so a "
            "`human_log.jsonl` under any arm is reported as reviewed. Nothing writes "
            "that file anywhere but `port-human` \u2014 DESIGN \u00a711.2 gives it one "
            "value of the axis \u2014 so its presence elsewhere means something "
            "unreviewed produced it, and the wildcard is what turns that signal into a "
            "line in the Explicitly allowed count. The edit reads as a harmless "
            "generalisation and matches the shape of every other pattern in the list."
        ),
        min_kills=1,
    ),
    Mutation(
        name="human_log_path_from_a_literal",
        path=HUMAN_ARM,
        anchor="    return ROOT / path_template(key).format(",
        replacement=('    return ROOT / ("results/{corpus}/{detector}/{supervision}/'
                     'port-human/" + ("human_log.jsonl" if key == LOG_KEY else '
                     '"window_freeze.json")).format('),
        breaks=(
            "The arm reconstructs its own output paths instead of reading "
            "`paths.humanlog` from naming.yaml, which DESIGN \u00a711.2 requires by name. "
            "Today the literal is identical, so nothing is wrong yet — the defect is "
            "that there are now two copies and the day one moves is the day results are "
            "written to one path and read from another. A reader looking for the "
            "authority on where this arm writes finds two, with no way to tell which "
            "the last run used."
        ),
        min_kills=1,
    ),
    Mutation(
        name="summary_reports_offsets",
        path=HUMAN_ARM,
        anchor='"documents_touched": len({e.doc_id for e in sample}),',
        replacement=('"documents_touched": len({e.doc_id for e in sample}),\n'
                     '        "spans": [(e.doc_id, e.start, e.end) for e in sample],'),
        breaks=(
            "The summary — the view built to be pasted into a terminal, a commit "
            "message, or a conversation — starts carrying (doc_id, offset) pairs, which "
            "are pointers into the corpus for anyone holding it. No surface form is "
            "quoted, which is exactly why it would survive review: the addition looks "
            "like extra rigour rather than a leak, and the same pairs are correct in "
            "the committed log, whose audience already holds the corpus."
        ),
        min_kills=1,
    ),
    Mutation(
        name="render_offsets_are_document_offsets",
        path=HUMAN_ARM,
        anchor='f"     offsets   ({e.start - left}, {e.end - left}) within that context',
        replacement='f"     offsets   ({e.start}, {e.end}) within that context',
        breaks=(
            "The rendered window labels document offsets as offsets within the "
            "context, so an author counting characters lands on the wrong span and one "
            "told to trust the numbers is handed a document coordinate — an invitation "
            "to open the file and read past the \u00b1120 characters DESIGN \u00a711.1 "
            "bounds the window at. For a span near the start of a document the two "
            "agree, so it reads correctly on whichever example is checked first."
        ),
        min_kills=1,
    ),
    Mutation(
        name="self_report_defaults_to_none",
        path=HUMAN_ARM,
        anchor=('def log_line(iteration: int, event: str, model_consulted: str, *, '
                'human_minutes=None,'),
        replacement=('def log_line(iteration: int, event: str, model_consulted: str '
                     '= "none", *, human_minutes=None,'),
        breaks=(
            "The rule_author.md \u00a78 self-report acquires a default, and the default "
            "is the exculpating value. Every existing caller keeps working and every "
            "line it writes says no model was consulted \u2014 which is true of the "
            "callers in this repository and says nothing about the ones a rule author "
            "writes. The field then records that nobody was asked rather than that "
            "nobody consulted a model, and the clause has no other enforcement: \u00a78 "
            "binds a person, and this field is what makes the obligation appear at "
            "every event instead of once at the start of the run. A default is a "
            "default for the clause."
        ),
        min_kills=1,
    ),
    Mutation(
        name="self_report_refuses_the_violation",
        path=HUMAN_ARM,
        anchor='    if model_consulted not in axis(CONSULTED_AXIS):',
        replacement=('    if model_consulted == VIOLATION:\n'
                     '        raise PortHumanError("rule_author.md \u00a78 forbids this")\n'
                     '    if model_consulted not in axis(CONSULTED_AXIS):'),
        breaks=(
            "The harness refuses to record a \u00a78 violation, which looks like "
            "enforcement and is the opposite. The clause binds a person and cannot be "
            "enforced by code at all \u2014 nothing in a rule file distinguishes a "
            "pattern its author designed from one they transcribed \u2014 so all this "
            "removes is the report. A field that rejects the answer it exists to "
            "capture collects only the other answers, and every log in the experiment "
            "then attests to a clean run by construction: the arm's integrity is "
            "documented by a file that could not have recorded its absence. Nothing "
            "in the output looks wrong, which is the same shape as the loader fixture's "
            "skip."
        ),
        min_kills=1,
    ),
    Mutation(
        name="rendered_window_may_be_redirected",
        path=SHOW_WINDOW,
        anchor="    if not args.counts_only and not sys.stdout.isatty():",
        replacement="    if False:",
        breaks=(
            "`python tools/show_human_window.py --corpus es-carmen > window.txt` "
            "succeeds, and the \u00b1120-character contexts of a DUA corpus are on "
            "disk \u2014 the file rule_author.md \u00a76 says must not exist. Nothing "
            "about the run looks different: the author sees the same window they would "
            "have seen, the script exits 0, and the leak is a file nobody looks at "
            "again. release_screen.py would have to catch it by content sniff at commit "
            "time, which is the layer this check exists to avoid depending on, and a "
            "terminal transcript or a scrollback capture is outside the screener "
            "entirely. The refusal is also deliberately before the corpus is loaded: a "
            "check that runs after the text is in memory is one exception away from "
            "having rendered it."
        ),
        min_kills=1,
    ),
]

COUNT_RE = re.compile(r"(\d+) (passed|failed|error|errors)")
#: pytest gave up before running anything. Then there is no kill count to read: the
#: number of collection errors is a property of the import, not of the guarantee.
NOT_RUN_RE = re.compile(r"Interrupted: \d+ error(s)? during collection|"
                        r"^INTERNALERROR", re.M)


def kills(output: str, *, expect_ran: bool = True) -> int:
    """Tests that failed or errored, from pytest's summary line.

    Errors count: a mutation that breaks the module-scoped fixture takes out
    whole tests, and those are caught tests, not uncounted ones.

    A collection interrupt does not count, and raises instead. The distinction is
    the one this harness got wrong once: a suite that *ran* and errored has told us
    the guarantee is checked, whereas a suite that could not be imported has told us
    nothing while producing a larger number. `expect_ran=False` for the baseline,
    which has its own reporting path.
    """
    if expect_ran and NOT_RUN_RE.search(output):
        raise BrokenSuite(
            "pytest stopped during collection, so no test ran. The number of "
            "collection errors is not a kill count — it is the same number for a "
            "mutation that works and one that broke the import."
        )
    counts = {kind.rstrip("s"): int(n) for n, kind in COUNT_RE.findall(output)}
    return counts.get("failed", 0) + counts.get("error", 0)


def outcomes(output: str) -> int:
    """Total tests pytest reported an outcome for: passed + failed + errored.

    Compared against the baseline to catch the milder version of a broken suite —
    one that collects, runs, and quietly reports on fewer tests than it should. A
    mutation is supposed to change which tests pass, never how many exist.
    """
    counts = {kind.rstrip("s"): int(n) for n, kind in COUNT_RE.findall(output)}
    return (counts.get("passed", 0) + counts.get("failed", 0)
            + counts.get("error", 0))


def make_tree(tmp: Path) -> Path:
    """A throwaway copy of the repository, enough to run the loader tests.

    `data/` is symlinked rather than copied — it is DUA-restricted and up to
    several GB. The symlink is read-only in practice because no mutation touches
    a data path, and copying would be both slow and a second place for restricted
    text to live.

    `sealed/` is symlinked for the same reasons and one more: a mutation that
    breaks the seal must be able to *fail* by reaching the real sealed fold's
    directory structure, so a fake one here would let a bypass look prevented. No
    mutation reads its contents, and neither does any test — the seal tests check
    that paths are absent and that the gate refuses, both decidable from the path.
    """
    tree = tmp / "repo"
    tree.mkdir()
    for name in COPIED:
        shutil.copytree(
            ROOT / name, tree / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    for name in COPIED_FILES:
        shutil.copy2(ROOT / name, tree / name)

    # `data/` is a real directory here and only `data/raw/` is symlinked. Symlinking
    # the whole of `data/` was simpler and made two screener tests unrunnable: `git
    # check-ignore` refuses a pathspec "beyond a symbolic link", so every question
    # about `data/acquire/...` came back rc=128, and the tests comparing .gitignore
    # with DENY_EXCEPTIONS were reading a git error as an answer. What is copied is
    # exactly the publishable part — the acquisition scripts and the READMEs — and the
    # corpora themselves stay behind the symlink.
    (tree / "data").mkdir()
    shutil.copytree(ROOT / "data" / "acquire", tree / "data" / "acquire")
    for readme in ROOT.glob("data/*.md"):
        shutil.copy2(readme, tree / "data" / readme.name)
    (tree / "data" / "raw").symlink_to(ROOT / "data" / "raw")
    if (ROOT / "sealed").exists():
        (tree / "sealed").symlink_to(ROOT / "sealed")
    # An empty git repository, so the screener's git questions have an answer here.
    # Without it `git check-ignore` runs against whatever repository happens to
    # contain the temporary directory — usually none — and the tests that compare
    # .gitignore with DENY_EXCEPTIONS would pass by both sides being unavailable.
    # Nothing is committed: history screening must find an empty history, not this
    # repository's.
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    return tree


def run_suite(tree: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES, "-q", "--no-header", "-p",
         "no:cacheprovider"],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="mutations to run (default: all)")
    parser.add_argument("--list", action="store_true", help="list and exit")
    args = parser.parse_args()

    selected = MUTATIONS
    if args.names:
        by_name = {m.name: m for m in MUTATIONS}
        unknown = sorted(set(args.names) - set(by_name))
        if unknown:
            print(f"unknown mutation(s): {unknown}", file=sys.stderr)
            print(f"available: {sorted(by_name)}", file=sys.stderr)
            return 2
        selected = [by_name[n] for n in args.names]

    if args.list:
        for m in selected:
            print(f"{m.name:24} {m.path}  (expect >= {m.min_kills} kills)")
        return 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pristine = make_tree(tmp)

        baseline = run_suite(pristine)
        if kills(baseline, expect_ran=False) or NOT_RUN_RE.search(baseline):
            print("BASELINE IS NOT GREEN — fix the suite before mutating.")
            print(baseline[-2000:])
            return 1
        base_outcomes = outcomes(baseline)
        print(f"baseline: {baseline.strip().splitlines()[-1]}\n")

        failures = []
        for mutation in selected:
            tree = tmp / f"mut_{mutation.name}"
            shutil.copytree(pristine, tree, symlinks=True)
            try:
                mutation.apply(tree)
                output = run_suite(tree)
                caught = kills(output)
                # A mutation changes which tests pass, never how many there are. A
                # smaller total means the suite was damaged rather than challenged,
                # and every "kill" in it is unattributable.
                ran = outcomes(output)
                if ran != base_outcomes:
                    raise BrokenSuite(
                        f"the suite reported on {ran} tests, baseline "
                        f"{base_outcomes}. The mutation changed how many tests exist, "
                        "so its kill count cannot be read as coverage of the "
                        "guarantee."
                    )
            except StaleMutation as exc:
                print(f"STALE   {mutation.name:24} {exc}")
                failures.append(mutation.name)
                continue
            except BrokenSuite as exc:
                print(f"BROKEN  {mutation.name:24} {exc}")
                failures.append(mutation.name)
                continue
            ok = caught >= mutation.min_kills
            print(
                f"{'ok     ' if ok else 'SURVIVED'} {mutation.name:24} "
                f"{caught:3} tests caught it (expected >= {mutation.min_kills})"
            )
            if not ok:
                failures.append(mutation.name)
            shutil.rmtree(tree)

    print()
    if failures:
        print(f"FAIL: {len(failures)} of {len(selected)} — {failures}")
        return 1
    print(f"all {len(selected)} mutations caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
