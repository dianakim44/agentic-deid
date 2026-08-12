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
    "tests/test_rules.py",
    "tests/test_check_rules.py",
    "tests/test_run_fold.py",
    "tests/test_conftest.py",
    "tests/test_arm_rules_path.py",
    "tests/test_prompt.py",
    "tests/test_seal_internals.py",
    "tests/test_bedrock.py",
    "tests/test_check_bedrock_logging.py",
    "tests/test_structure.py",
    "tests/test_orchestrate.py",
    "tests/test_termination.py",
    "tests/test_agent_role.py",
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
#: `rules/` is here for the two tests that load the committed example file. They skip
#: when it is absent, which is the state iteration 1 starts from — so without the
#: directory those two would skip rather than run, and a mutation to the loader would be
#: reported as caught by a suite that never exercised it.
COPIED = ("src", "tests", "config", "splits", "results", "tools", "docs", "rules")

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
ORCHESTRATE = "src/orchestrate.py"
SHOW_WINDOW = "tools/show_human_window.py"
RULES = "src/rules.py"
CHECK_RULES = "tools/check_rules.py"
RUN_FOLD = "src/eval/run_fold.py"
#: The config, mutated like any other guard. It is where output paths are declared, so a
#: path that loses an axis is edited here and not in a module — which is the point of the
#: rule that a new value goes into the config first: the collision is visible in one
#: committed file rather than distributed across the callers.
NAMING = "config/naming.yaml"
CONFTEST = "tests/conftest.py"
TEST_RUN_FOLD = "tests/test_run_fold.py"
PROMPT = "src/llm/prompt.py"
BEDROCK = "src/llm/bedrock.py"
#: The gate's producer. Mutated like any other guard, and for a sharper reason than
#: most: this file is what writes the compliance record the paper cites, so a defect
#: here does not lose evidence, it manufactures it.
CHECK_LOGGING = "tools/check_bedrock_logging.py"
PATCH_CHECK = "tools/check_patched_guarantees.py"
#: The pre-registered stopping rule (DESIGN §3). Separated from any loop driver on purpose —
#: see the module's own note — which is also what makes it mutable in isolation here: a
#: mutation to δ or to the ceiling branch cannot be confused with a bug in the thing it stops.
TERMINATION = "src/termination.py"

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
    # ─── the language layer (2026-08-11, Prohibition 2 across languages) ─────
    # The layer exists because the English-only vocabulary rejected 23 of 28 names in
    # the first port-oneshot output, all of them for naming a clinical formula in the
    # corpus language — which Prohibition 2 permits. Every mutation below is a way of
    # widening the vocabulary further than that, and each one looks like a
    # simplification of an awkward special case.
    Mutation(
        name="the_language_layer_is_keyed_on_the_id_the_model_wrote",
        path=SCREEN,
        anchor='        found = rule_id_findings(text, lang=rule_file_lang(path))',
        replacement='        found = rule_id_findings(text, lang=(text.split("rule_id:")[1]\n'
                    '                                 .split(":")[0].strip()\n'
                    '                                 if "rule_id:" in text and ":" in\n'
                    '                                 text.split("rule_id:")[1] else None))',
        breaks=(
            "The language is taken from the rule id's own prefix instead of from the "
            "file path. It reads as the more precise source — the id says what "
            "language it is in, and the path only says what file it is in — and for "
            "every legitimate file the two agree, so nothing looks wrong. The "
            "difference is who writes them: the path comes from the arm's "
            "configuration (`paths.armrules`, DESIGN §5.3) and the prefix is free "
            "text inside a file the model produced. Keyed on the prefix, the screened "
            "text nominates its own vocabulary, and `es:` in front of a surname buys "
            "the whole Spanish formula set for the name it precedes. It is also dead "
            "in practice in the reassuring direction: every id in both the committed "
            "`rules/es.yaml` and the first port-oneshot output is unprefixed, so a "
            "prefix-keyed layer widens nothing and passes the same tests."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_disagreeing_prefix_still_opens_the_layer",
        path=SCREEN,
        anchor="        allowed_extra = frozenset() if (sep and prefix.strip().lower() != lang) else extra",
        replacement="        allowed_extra = extra",
        breaks=(
            "A prefix that contradicts the path keeps the path's layer instead of "
            "dropping to the English vocabulary. This is the branch that decides what "
            "happens when the harness and the model disagree about what language a "
            "name is in, and deleting it looks like removing a case that cannot arise "
            "— the file is `es.yaml`, so why would an id say `de:`? Because the model "
            "writes the prefix, and a disagreement is precisely the state to treat as "
            "suspicious rather than as an opportunity to pick the wider set."
        ),
        min_kills=1,
    ),
    Mutation(
        name="an_unknown_language_gets_every_layer",
        path=SCREEN,
        anchor="    extra = RULE_ID_VOCAB_BY_LANG.get(lang, frozenset()) if lang else frozenset()",
        replacement="    extra = RULE_ID_VOCAB_BY_LANG.get(\n"
                    "        lang, frozenset().union(*RULE_ID_VOCAB_BY_LANG.values()))",
        breaks=(
            "An unrecognised language falls back to the union of every layer rather "
            "than to no layer. Presented as robustness — a new corpus should not fail "
            "screening merely because nobody has written its vocabulary yet — and it "
            "passes every per-language test, since each declared language still gets "
            "its own set. What it actually does is make the union reachable from any "
            "filename at all: one unclassified rule file, and a name may be built "
            "from Spanish, Catalan, German and Korean formulae at once. The union is "
            "the widest vocabulary in the tool and nothing should reach it, least of "
            "all by default."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_language_layer_is_a_substring_test",
        path=SCREEN,
        anchor="                   and p.lower() not in allowed_extra",
        replacement="                   and not any(p.lower() in w for w in allowed_extra)",
        breaks=(
            "Membership becomes a substring test against the layer. Every legitimate "
            "name still passes and the change reads like tolerance for inflection — "
            "`ano` inside `anos`, a stem inside its own plural. But the test now "
            "succeeds for any fragment of any listed word, and short fragments are "
            "what names are made of: `ana` passes on `anos`, `mar` on `marzo`, `don` "
            "would no longer need to be listed at all. A closed set stops being "
            "closed the moment membership is decided by containment."
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
    Mutation(
        name="freeze_guard_only_checks_the_file",
        path=HUMAN_ARM,
        anchor="    where = started_where(corpus, detector, supervision)\n"
               "    if where is not None:",
        replacement=("    where = started_where(corpus, detector, supervision)\n"
                     "    if False:"),
        breaks=(
            "Restores the hole this repository actually fell through, three times. What "
            "remains is the `path.exists()` branch, which refuses to *overwrite* and has "
            "nothing to say about `rm window_freeze.json` followed by a second call \u2014 "
            "`exists()` is False after the rm, so the write branch runs, hashes today's "
            "files, and reports a successful freeze. Nothing in the resulting file "
            "distinguishes it from the original: it claims to be the opening window and "
            "is not. A refusal conditioned on the presence of the thing being protected "
            "is not a refusal but a request, addressed to whoever is in a position to "
            "remove the evidence. See docs/notes/window-freeze-history.md."
        ),
        min_kills=1,
    ),
    Mutation(
        name="arm_started_reads_the_last_line_only",
        path=HUMAN_ARM,
        anchor=('    for line in lines:\n'
                '        line = line.strip()\n'
                '        if not line:\n'
                '            continue\n'
                '        if json.loads(line).get("human_minutes") is not None:\n'
                '            return True\n'
                '    return False'),
        replacement=('    kept = [x for x in lines if x.strip()]\n'
                     '    if not kept:\n'
                     '        return False\n'
                     '    return json.loads(kept[-1]).get("human_minutes") is not None'),
        breaks=(
            "The guard reads only the final log line, so the window re-opens the moment "
            "an event with a null `human_minutes` is appended \u2014 and appending a line "
            "is the one thing this arm does constantly. A `read_sample` at the start of "
            "iteration 7 would make the whole run's freeze writable again, after six "
            "iterations of a person's attention. It looks like a cheap optimisation over "
            "reading the file, agrees with the real implementation on any log whose last "
            "line happens to carry minutes, and turns an append-only file's central "
            "property \u2014 that the evidence stays in it \u2014 into a property of "
            "whichever line came last."
        ),
        min_kills=1,
    ),
    Mutation(
        name="zero_minutes_read_as_not_started",
        path=HUMAN_ARM,
        anchor='        if json.loads(line).get("human_minutes") is not None:',
        replacement='        if json.loads(line).get("human_minutes"):',
        breaks=(
            "`0` becomes indistinguishable from `null`. A logged zero is a recorded "
            "measurement \u2014 `log_line()` validates the field to accept it precisely "
            "because an event can take under a minute \u2014 and this reads it as "
            "\"nothing happened\", leaving the freeze writable for an arm that has "
            "recorded work. One character, and it agrees with the real implementation on "
            "every log where nobody was quick."
        ),
        min_kills=1,
    ),
    Mutation(
        name="started_where_reads_the_worktree_only",
        path=HUMAN_ARM,
        anchor="    return IN_HISTORY if _minutes_in_git_history(path) else None",
        replacement="    return None",
        breaks=(
            "Reverts the guard to its first version, which read the working tree and "
            "nothing else \u2014 so `rm human_log.jsonl` re-opens the freeze. One file, "
            "one command, and the guard's own input is gone; the arm then reads as never "
            "started and `freeze_window()` writes a record claiming to be the opening "
            "window. It is the louder of the two deletions, since the log is the arm's "
            "only record of what a person did and how long it took, but louder is not "
            "prevented, and the note documented this as an open hole before it was closed "
            "(docs/notes/window-freeze-history.md). What closes it is that the minutes "
            "were committed: `git log --all` over this one path finds them in any commit "
            "on any branch. Removing them from history is still possible and is exactly "
            "what this guard is for \u2014 a rewrite of a public repository's history is "
            "not a quiet act."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_real_arm_may_draw_a_practice_number",
        path=SAMPLE,
        anchor="    if not practice and iteration >= low:",
        replacement="    if False:",
        breaks=(
            "Removes half the band's refusal, in the direction that lets a rehearsal's "
            "numbers be reported as a run. The band exists so the rule-file schema, the "
            "three layer syntaxes and the feedback command can be learned without "
            "spending iteration 1's window; with this half gone, iteration 901 is a "
            "legal arm iteration and its provenance record says practice: false, so "
            "nothing distinguishes a rehearsal's output from a run's."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_rehearsal_may_draw_a_real_number",
        path=SAMPLE,
        anchor="    if practice and iteration < low:",
        replacement="    if False:",
        breaks=(
            "The other half, and the one whose failure is silent. A rehearsal aimed at "
            "iteration 1 draws iteration 1: the draw is seeded, so the window printed is "
            "byte-for-byte the window the real run would have shown, and nothing "
            "downstream records that it was read early. Nobody discovers it afterwards "
            "\u2014 there is no artifact to discover \u2014 which is why the check is on "
            "the way in and why the flag is declared by the caller rather than inferred "
            "from the number (inference cannot separate the two cases at all)."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_practice_window_may_overlap_iteration_one",
        path=HUMAN_ARM,
        anchor="    reserved = {e.key for e in draw(pool, corpus, reserved_for, n=n)}",
        replacement="    reserved = set()",
        breaks=(
            "Stops subtracting iteration 1's spans from the practice pool, so a rehearsal "
            "can show spans iteration 1 will later measure. That defeats the point of "
            "having a band at all: the number is different, the sample overlaps, and the "
            "spans the author already read are the ones iteration 1 no longer measures "
            "honestly. Subtraction rather than draw-and-retry, so disjointness is a "
            "property of the pool and the draw stays one seeded function."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_rule_layer_is_derived_from_the_rule_id",
        path=RULES,
        anchor="                    layer=rule.layer, detector=detector,",
        replacement='                    layer=("gazetteer" if "gaz" in rule.rule_id '
                    'else rule.layer), detector=detector,',
        breaks=(
            "Derives a span's layer from its rule id instead of copying the rule's "
            "declaration \u2014 the one thing DESIGN \u00a73 forbids, in the form it "
            "would actually take (a substring test that looks reasonable). Every span "
            "still carries a valid layer from naming.yaml, so nothing downstream "
            "complains; DESIGN \u00a77's per-layer comparison would then be measuring "
            "the substring test."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_gazetteer_term_is_a_regex",
        path=RULES,
        anchor='    return f"{left}{escaped}{right}"',
        replacement='    return f"{left}{term}{right}"',
        breaks=(
            "Interpolates a gazetteer term raw instead of escaping it, which makes the "
            "regex-free layer a regex layer without saying so. `C.S. (Norte)` is an "
            "ordinary institution name and a broken pattern: the rule fails to load, or "
            "\u2014 for a term whose metacharacters happen to compile \u2014 matches "
            "something the author did not write."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_gazetteer_term_needs_a_word_character_at_each_edge",
        path=RULES,
        anchor='    left = r"(?<!\\w)" if term[:1].isalnum() or term[:1] == "_" else ""',
        replacement='    left = r"\\b"',
        breaks=(
            "Restores the \\b that this module shipped with for one run and that "
            "tests/test_rules.py caught: \\b requires a word character on the inside of "
            "the boundary, so a term *beginning* with punctuation can never match. The "
            "rule loads, compiles, fires nowhere, and reads as a name that does not occur "
            "in the corpus \u2014 which is also what DESIGN \u00a77 reports as a "
            "negative result for the layer."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_cue_span_swallows_the_cue",
        path=RULES,
        anchor="        group = 1",
        replacement="        group = 0",
        breaks=(
            "Makes a context_cue span cover the cue words as well as the identifier. The "
            "cue is the evidence, not the PHI: a span starting at `Dr.` is scored against "
            "gold that starts at the name, so it misses under fully_covered while hitting "
            "under relaxed \u2014 a boundary error that reads as a scoring artefact, and "
            "one that would depress the layer DESIGN \u00a77 predicts most for."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_checksum_accepts_every_match",
        path=RULES,
        anchor="            if check is not None and not check(text[start:end]):",
        replacement="            if False:",
        breaks=(
            "Turns regex_checksum into regex. The layer's entire claim is shape plus "
            "arithmetic: without the check digit an eight-digit run followed by any "
            "letter is an identifier, and the precision the layer is supposed to buy "
            "disappears while its name still says it is there."
        ),
        min_kills=1,
    ),
    Mutation(
        name="an_unimplemented_checksum_is_ignored",
        path=RULES,
        anchor="        if checksum not in CHECKSUMS:",
        replacement="        if False:",
        breaks=(
            "Lets a rule name a checksum nobody implemented. `CHECKSUMS[self.checksum]` "
            "then raises at match time \u2014 mid-run, inside a detection pass, rather "
            "than at load with the rule id in hand. A rule file is written by an agent or "
            "by a person mid-iteration; refusing at load costs a line of output and "
            "refusing at first match costs a scoring round."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_lexicon_name_may_traverse_directories",
        path=RULES,
        anchor='    if not regex.fullmatch(r"[a-z0-9_]+", name):',
        replacement="    if False:",
        breaks=(
            "Removes the only check on the one path a rule file gets to name. "
            "`es/../../sealed/es-meddocan/test` is a valid-looking lexicon name, and "
            "reading it is the sealing violation that invalidates the whole experiment "
            "(CLAUDE.md). Rule files are authored by agents; this is not a rule to "
            "enforce by hoping nobody composes that string."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_declared_rule_file_language_is_trusted",
        path=RULES,
        anchor="    if declared != lang:",
        replacement="    if False:",
        breaks=(
            "Stops checking the file's own `lang:` against the language it was loaded as. "
            "The rule_id prefix comes from the load language, so a `cat` file loaded as "
            "`es` gives every span an `es:` prefix and DESIGN \u00a75.2's "
            "per-file precision attribution goes to the wrong file, consistently and "
            "invisibly."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_duplicate_rule_id_is_allowed",
        path=RULES,
        anchor="            raise RuleError(\n                f\"{r.rule_id}: duplicate rule_id in one file. Ids must be unique and \"",
        replacement="            pass  # noqa\n            _unused = (",
        breaks=(
            "Allows two rules to share an id. The by_rule counts and the rule_id on every "
            "span are the same identifier, so two rules' attribution merges into one "
            "bucket: an author looking at a precision figure per rule sees a number that "
            "belongs to neither rule."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_non_target_type_may_be_a_rule_target",
        path=RULES,
        anchor="    if phi_type in non_target_types():",
        replacement="    if False:",
        breaks=(
            "Lets a rule target OTHER. It is a residual bucket a corpus ships, not a "
            "phenomenon (rule_author.md Prohibition 4): a rule matching it scores against "
            "whatever the corpus could not classify, and the recall it buys is a property "
            "of that corpus's annotation practice rather than of a detector."
        ),
        min_kills=1,
    ),
    Mutation(
        name="check_rules_reads_every_fold",
        path=CHECK_RULES,
        anchor='        docs = load_fold(args.corpus, "dev")',
        replacement="        docs = [d for d in load(args.corpus)]",
        also=((CHECK_RULES,
               "from src.corpora.base import CorpusError, rule_langs",
               "from src.corpora import load\nfrom src.corpora.base import "
               "CorpusError, rule_langs"),),
        breaks=(
            "Drops the dev restriction from the feedback tool, so the rule author's "
            "forty-times-an-evening command scores against every fold the loader "
            "returns. The test fold's text is in sealed/ and the loader does not return "
            "it, so this is not by itself a seal break \u2014 it is the step before one, "
            "and it silently mixes folds in the numbers an author develops rules against "
            "(CLAUDE.md, DESIGN \u00a711.1)."
        ),
        min_kills=1,
    ),

    # \u2500\u2500 the two detection views cannot diverge \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    #
    # `tools/check_rules.py` shows a sample and `src/eval/run_fold.py` scores the fold,
    # from one implementation (`detect_fold`). These four are the ways that stops being
    # true. They are grouped because the failure they produce is the same one and it is
    # the worst shape available: the sample says a rule fires, the fold-wide metrics say
    # it does not, and nothing in either output says which is wrong. An author tunes
    # against the number that is lying to them and a reader comparing the tool's counts
    # to `metrics.json` has no way to notice.
    Mutation(
        name="check_rules_detects_separately",
        path=CHECK_RULES,
        anchor=(
            "    predictions = detect_fold(docs, "
            "RuleSet(rules=rules, versions=ruleset.versions))"
        ),
        replacement=(
            "    predictions = {}\n"
            "    for _doc in docs:\n"
            "        _out = []\n"
            "        for _rule in rules:\n"
            "            for _s, _e in _rule.finditer(_doc.text):\n"
            "                _out.append(type('P', (), {'start': _s, 'end': _e, "
            "'rule_id': _rule.rule_id})())\n"
            "        predictions[_doc.doc_id] = _out"
        ),
        breaks=(
            "The mutation the second detection implementation actually is. This one is "
            "*faithful* \u2014 it iterates the same rules over the same documents and, "
            "today, finds the same offsets. That is the point: a second implementation "
            "does not arrive broken, it arrives correct and then drifts when one side "
            "is changed. `test_the_tool_calls_the_shared_detector` is what catches it "
            "while it is still faithful, because a behavioural test cannot \u2014 by "
            "construction there is nothing yet to observe."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_detects_separately",
        path=RUN_FOLD,
        anchor="    predictions = detect_fold(docs, ruleset, detector=detector)",
        replacement=(
            "    predictions = {}\n"
            "    for _doc in docs:\n"
            "        _seen, _out = set(), []\n"
            "        for _rule in ruleset.rules:\n"
            "            for _s, _e in _rule.finditer(_doc.text):\n"
            "                if (_s, _e) in _seen:\n"
            "                    continue\n"
            "                _seen.add((_s, _e))\n"
            "                _out.append(Span(\n"
            "                    start=_s, end=_e, surface=_doc.text[_s:_e],\n"
            "                    subtype=_rule.rule_id, phi_type=_rule.phi_type,\n"
            "                    layer=_rule.layer, detector=detector,\n"
            "                    rule_id=_rule.rule_id, score=_rule.score))\n"
            "        predictions[_doc.doc_id] = _out"
        ),
        also=((RUN_FOLD,
               "    ROOT, CorpusError, Document, axis, model_id_absent, path_template,",
               "    ROOT, CorpusError, Document, Span, axis, model_id_absent, "
               "path_template,"),),
        breaks=(
            "The same divergence from the other side, and this is what a hand-rolled "
            "detection loop actually looks like once written: correct in structure, and "
            "it skips a span it has already seen at those offsets. The dedupe is the "
            "drift \u2014 two rules matching the same bytes is a merge-policy question "
            "(DESIGN \u00a74), and the run path answering it silently means the tool shows "
            "the author two matches while the score counted one. Caught by comparing "
            "the tool's own listing to spans.jsonl as a *multiset*: totals and sets "
            "both agree here, and only the count of the repeated span does not."
        ),
        min_kills=1,
    ),
    Mutation(
        name="detect_fold_drops_overlaps",
        path=RUN_FOLD,
        anchor=(
            "    return {doc.doc_id: ruleset.detect(doc.text, detector=detector) "
            "for doc in docs}"
        ),
        replacement=(
            "    out = {}\n"
            "    for doc in docs:\n"
            "        kept, taken = [], set()\n"
            "        for span in ruleset.detect(doc.text, detector=detector):\n"
            "            if any(span.start < e and span.end > s for s, e in taken):\n"
            "                continue\n"
            "            taken.add((span.start, span.end))\n"
            "            kept.append(span)\n"
            "        out[doc.doc_id] = kept\n"
            "    return out"
        ),
        breaks=(
            "Resolves overlaps inside the detector, first-rule-wins. Merge policy is a "
            "replaceable strategy (DESIGN \u00a74) and this takes the decision away from "
            "it: fixed-priority, union and agent-arbiter would then all score "
            "identically, because they would be handed a prediction set with the "
            "conflicts already settled. It also makes both tools drop the same spans, "
            "so the agreement test stays green \u2014 the divergence here is between "
            "the detector and the merge axis rather than between the two tools."
        ),
        min_kills=1,
    ),
    Mutation(
        name="spans_file_carries_the_surface",
        path=RUN_FOLD,
        anchor='                "phi_type": span.phi_type,',
        replacement=(
            '                "phi_type": span.phi_type,\n'
            '                "surface": span.surface,'
        ),
        breaks=(
            "Puts the matched text into `spans.jsonl`, which is a published file the "
            "release screener allows by pattern. CLAUDE.md permits offsets, types and "
            "verdicts with the text left out; this is the DUA violation the field "
            "whitelist in `write_spans` exists to prevent, and it is exactly the edit "
            "someone makes to debug a boundary."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_reads_the_sealed_fold",
        path=RUN_FOLD,
        anchor='    if split == "test":',
        replacement='    if split == "test" and False:',
        breaks=(
            "Lets `--split test` through to the loader. The loader's own gate still "
            "refuses the import, so this is not a seal break by itself \u2014 it is the "
            "removal of the layer that says *why* out loud. What reaches the caller "
            "instead is a corpus-shaped error, which sends whoever hit it looking for a "
            "missing fold rather than reading CLAUDE.md."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_omits_the_layer",
        path=RUN_FOLD,
        anchor='                "layer": span.layer,',
        replacement='                "layer": span.detector,',
        breaks=(
            "Derives the published layer from the detector instead of taking the one "
            "the matching rule declared \u2014 the exact substitution DESIGN \u00a73 and "
            "CLAUDE.md forbid. Every span from the `R` arm would then read as layer `R`, "
            "which is not a value of the layer axis, and the complementarity "
            "decomposition over layers would collapse to one bucket."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_writes_a_null_model_id",
        path=RUN_FOLD,
        anchor='        "model_id": model_id_absent(),',
        replacement='        "model_id": None,',
        breaks=(
            "Records `null` for an arm that called no model. `null` cannot be told from "
            "a field nobody filled in, so six months later the record does not say "
            "whether the `R` arm used no model or whether the run forgot to write down "
            "which one it used. Same principle as the cost block's zeros: absent is "
            "refused, explicitly-absent is recorded."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_hardcodes_the_absent_value",
        path=RUN_FOLD,
        anchor='        "model_id": model_id_absent(),',
        replacement='        "model_id": "none",',
        breaks=(
            "Writes the absent-model value as a literal instead of reading it from "
            "config/naming.yaml. Behaviourally identical today, which is why it is here: "
            "CLAUDE.md requires vocabulary that lands in a results file to be defined in "
            "the config, and the cost of ignoring that is only paid on the day the "
            "config changes and one of the two spellings does not."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_skips_axis_validation",
        path=RUN_FOLD,
        anchor="    check_run(run)\n    template = path_template(\"spans\")",
        replacement='    template = path_template("spans")',
        breaks=(
            "Writes `spans.jsonl` without validating the arm's axes, so a misspelled "
            "`porting` value mints a results directory that no axis defines and that "
            "reads as a fifth rung. `write_metrics` still validates, so the failure is "
            "an orphan spans file beside no metrics \u2014 the halfway state the "
            "validate-before-write ordering exists to make impossible."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_writes_unsorted_spans",
        path=RUN_FOLD,
        anchor=(
            '    rows.sort(key=lambda r: (r["doc_id"], r["start"], r["end"], '
            'r["rule_id"] or ""))'
        ),
        replacement="    pass",
        breaks=(
            "Leaves the span order to `RuleSet.detect`'s rule iteration. Stable today "
            "and stable for an upstream reason rather than a stated one, so a re-run of "
            "identical rules can produce a diff in a committed results file, and a "
            "reviewer cannot tell a reordering from a change in what was detected."
        ),
        min_kills=1,
    ),
    # ── an arm's rule files live under the arm (DESIGN §5.3) ─────────────────
    Mutation(
        name="arm_rules_path_drops_the_axes",
        path=NAMING,
        anchor=('  armrules: "results/{corpus}/{detector}/{supervision}/{porting}/'
                'rules/iter{iteration}/{lang}.yaml"'),
        replacement='  armrules: "rules/{lang}.yaml"',
        breaks=(
            "The state before DESIGN §5.3: `armrules` names the bootstrap file, so "
            "`port-oneshot` and `port-loop` write the same path and the second arm to "
            "run overwrites the first's rules. `paths.armfreeze`'s collision one level "
            "down, and worse — an overwritten record is visibly gone, while an "
            "overwritten input leaves a complete, internally consistent metrics.json "
            "with a plausible rules_version behind it, for a run whose premise no "
            "longer exists. Note the template still formats: `str.format` ignores "
            "unused keys, so nothing raises and every path collapses silently."
        ),
        min_kills=3,
    ),
    Mutation(
        name="arm_rules_path_drops_the_iteration",
        path=NAMING,
        anchor="rules/iter{iteration}/{lang}.yaml",
        replacement="rules/{lang}.yaml",
        breaks=(
            "Keeps the four axes and loses the round. `port-loop` rewrites its rule file "
            "every iteration and the sequence is the experimental record — it is "
            "what δ/k was computed over and the only thing that can answer which "
            "rules existed at iteration 4. This keeps the last round and discards the "
            "history, reducing the arm to its final state, which is exactly what §"
            "5.1 argues aggregates cannot carry."
        ),
        min_kills=2,
    ),
    Mutation(
        name="arm_rules_path_loses_the_rules_component",
        path=NAMING,
        anchor="{porting}/rules/iter{iteration}/{lang}.yaml",
        replacement="{porting}/iter{iteration}/{lang}.yaml",
        breaks=(
            "Every axis is still there and the collision is still closed, so nothing "
            "about the overwrite argument fails. What fails is invisible from the path: "
            "`release_screen.py` applies its rule_id mechanism-vocabulary check to files "
            "matching `rules/*.yaml`, and that check is the only enforcement "
            "rule_author.md Prohibition 2 has — a surname in a rule name reaches a "
            "public metrics.json through the by_rule block, and metrics.json is on the "
            "screener's *allow* list. Unmatched here does not mean rejected: the check "
            "never runs and the file is reported clean. Same class as a structural check "
            "that silently matches nothing."
        ),
        min_kills=1,
    ),
    Mutation(
        name="run_fold_infers_its_own_rule_path",
        path=RUN_FOLD,
        anchor="    ruleset = load_for_corpus(corpus, paths=rules)",
        replacement=(
            "    from ..rules import arm_rules_path\n"
            "    from ..corpora.base import rule_langs\n"
            "    if rules is None:\n"
            "        rules = {l: arm_rules_path(corpus=corpus, detector=detector,\n"
            "                                   supervision=supervision,\n"
            "                                   porting=porting, iteration=1, lang=l)\n"
            "                 for l in rule_langs(corpus)}\n"
            "    ruleset = load_for_corpus(corpus, paths=rules)"
        ),
        breaks=(
            "`run_fold` derives its input from its own axis arguments instead of being "
            "told (DESIGN §5.3). Behaviourally invisible on the happy path — "
            "inferring the right path and being handed it produce identical output, "
            "which is why the assertion is structural. What it costs is that the module "
            "has one possible input location, so a trial file and the bootstrap file "
            "each need a special case, and the input becomes a function of the run "
            "block: the coupling that lets a run read its own results directory. The "
            "hardcoded iteration=1 is the tell — an inferring version has to invent "
            "a round number it was never given."
        ),
        min_kills=1,
    ),
    Mutation(
        name="rule_source_not_recorded",
        path=RUN_FOLD,
        anchor=(
            '        "rules_source": {lang: p for lang, p in '
            'sorted(ruleset.sources.items())},'
        ),
        replacement="",
        breaks=(
            "Leaves `rules_version` and drops the path. The version is whatever the "
            "author declared, so it survives an overwrite looking correct; the path "
            "names the arm and the iteration. Without it the published record cannot "
            "say which file the numbers were computed from, which makes DESIGN §5.3's "
            "whole decision undetectable from the outside — the reader sees a "
            "well-formed metrics.json either way."
        ),
        min_kills=1,
    ),
    Mutation(
        name="rule_source_recorded_absolute",
        path=RULES,
        anchor="        return str(p.resolve().relative_to(ROOT.resolve()))",
        replacement="        return str(p.resolve())",
        breaks=(
            "Records the rule file's absolute path in a published run block. Names a "
            "person's home directory, and on a machine where the corpus checkout sits "
            "beside the repository it names the directory layout of DUA data — the "
            "`relative_to` call is what keeps a path from being the leak CLAUDE.md's "
            "offsets-and-types rule exists to prevent. Note it still returns a string "
            "and still identifies the file, so every test asserting that the field is "
            "present and non-empty passes."
        ),
        min_kills=1,
    ),
    # ── the renderer, and the type that keeps a filled prompt off disk (§6) ───
    # `render_offsets_are_document_offsets` moved here with the renderer itself: it used
    # to name `src/porting/human_arm.py`, and the harness reported it STALE rather than
    # passing when the anchor left that file, which is the whole reason a vanished anchor
    # is an outcome and not a skip.
    #
    # The three after it are about the type rather than the rendering. The convention is
    # carried by a type rather than by a rule at each call site, so they break its three
    # load-bearing properties: the renderer returns it, the text has no unnamed accessor,
    # and the terminal exit checks its destination. Each is an edit someone makes for a
    # good reason — a debug copy, a `.text` "for tests", a plain `print` — and none of
    # them fails on any machine where anyone would be looking.
    Mutation(
        name="render_offsets_are_document_offsets",
        path=PROMPT,
        anchor=(
            'f"     offsets   ({span.start - left}, {span.end - left}) within that '
            'context'
        ),
        replacement='f"     offsets   ({span.start}, {span.end}) within that context',
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
        name="renderer_writes_a_debug_copy",
        path=PROMPT,
        anchor="    text = \"\\n\".join(out)",
        replacement=(
            "    text = \"\\n\".join(out)\n"
            "    open(\"/tmp/last_prompt.txt\", \"w\").write(text)   # 'just while debugging'"
        ),
        breaks=(
            "The filled prompt on disk — the file rule_author.md §6 says must not exist, "
            "carrying ±120 characters of dev text per span. Nothing about the run "
            "changes: the prompt is identical, the model sees the same thing, every "
            "content assertion still passes, and on a DUA corpus the leak is a file "
            "nobody opens again. `release_screen.py` blocks the *committed* paths a "
            "filled instance would land under, but /tmp is not one of them, which is why "
            "the convention is 'never written' rather than 'never committed' and why the "
            "check is structural rather than a path pattern. This is also the mutation "
            "that justifies checking the renderer's interior and not only the type: the "
            "type is intact here and protects a value that already escaped."
        ),
        min_kills=1,
    ),
    Mutation(
        name="filled_prompt_exposes_its_text",
        path=PROMPT,
        anchor="    __slots__ = (\"_text\", \"_reference\")",
        replacement=(
            "    __slots__ = (\"_text\", \"_reference\")\n\n"
            "    @property\n"
            "    def text(self) -> str:\n"
            "        return self._text"
        ),
        breaks=(
            "An accessor not named for a destination, which is the whole distinction the "
            "type draws. `to_terminal` checks where it is going and `for_transport` "
            "declares it; `.text` answers to anything — a log line, a json.dumps of a "
            "record that happens to hold it, an f-string in an exception message. Adding "
            "it breaks nothing and is the natural edit for a caller that wants to assert "
            "on the text, so the closed public surface has to be the thing asserted."
        ),
        min_kills=1,
    ),
    Mutation(
        name="terminal_exit_does_not_check_the_destination",
        path=PROMPT,
        anchor="        if not hasattr(stream, \"isatty\") or not stream.isatty():",
        replacement="        if False:",
        breaks=(
            "`to_terminal` writes to whatever it is handed, so a redirected stream "
            "receives the window: `python tools/show_human_window.py > w.txt` succeeds "
            "and the contexts are on disk. The author sees nothing different — the same "
            "text, exit 0. `show_human_window.py` has its own isatty check, which is why "
            "this is caught rather than fatal, but that check is for the error message "
            "and this one is the guarantee: the next caller of the exit is the agent "
            "orchestrator, which has no such check of its own."
        ),
        min_kills=1,
    ),
    # ── the shared corpus fixture (tests/conftest.py) ────────────────────────
    # These two mutate the *suite* rather than the code under it, which no other
    # mutation here does. The justification is the incident record: the defect below
    # shipped four times, was caught by this harness each time, and was caught only
    # indirectly — by the outcome-count guard noticing that a loader mutation changed
    # how many tests existed. That is a diagnosis after the fact. These two make the
    # defect a direct failure, and make the guard that produces it measurable itself.
    Mutation(
        name="conftest_availability_from_a_load",
        path=CONFTEST,
        anchor=(
            "    from src.corpora.base import CorpusError, corpus_root\n"
            "\n"
            "    try:\n"
            "        corpus_root(CORPUS)"
        ),
        replacement=(
            "    from src.corpora.base import CorpusError, load\n"
            "\n"
            "    try:\n"
            "        load(CORPUS)"
        ),
        breaks=(
            "Reverts the shared fixture to the form that shipped four times: "
            "availability decided by loading the corpus, so every loader bug reads as "
            "\"the corpus is not on this machine\".\n"
            "\n"
            "**The number, because the number is the argument.** On its own this "
            "mutation changes nothing observable — the loader works, nothing skips, the "
            "suite is green either way, and that is why it survived four reviews. Its "
            "cost is only paid when a real bug arrives, so it was measured with one: "
            "`type_in_both_lists` applied alongside it.\n"
            "\n"
            "  correct fixture + that loader bug   31 failed, 47 errors, 518 passed\n"
            "  reverted fixture + that loader bug   3 failed,  0 errors, 499 passed, "
            "93 skipped\n"
            "\n"
            "**93 tests silently disabled, and 78 non-passing outcomes reduced to 3.** "
            "The 3 survivors are the tests that construct a loader directly rather than "
            "through the fixture; every recount, every fold assertion and every offset "
            "check is gone, and pytest reports it in the colour of success.\n"
            "\n"
            "Caught directly by `test_availability_fixtures_resolve_a_path_and_do_not_"
            "load`, which reads conftest's syntax tree: an availability fixture may call "
            "`corpus_root` or `sealed_root` and nothing else. Structural rather than "
            "behavioural on purpose — the two forms are behaviourally identical on every "
            "machine where anyone would look."
        ),
        min_kills=1,
    ),
    Mutation(
        name="test_file_shadows_the_shared_fixture",
        path=TEST_RUN_FOLD,
        anchor=(
            "# `corpus_present` comes from `tests/conftest.py`, which is the only place "
            "availability is"
        ),
        replacement=(
            '@pytest.fixture(scope="module")\n'
            "def corpus_present():\n"
            "    from src.corpora.base import CorpusError, load\n"
            "    try:\n"
            "        load(CORPUS)\n"
            "    except CorpusError as exc:\n"
            '        pytest.skip(f"{CORPUS} not on this machine: {exc}")\n'
            "\n"
            "\n"
            "# `corpus_present` comes from `tests/conftest.py`, which is the only place "
            "availability is"
        ),
        breaks=(
            "Puts a local `corpus_present` back into one test file, in the defective "
            "form. This is the *propagation* rather than the defect: pytest resolves the "
            "nearest definition, so the local one wins over conftest's silently and only "
            "this file's 27 tests are affected — which is how it went unnoticed while "
            "three files carried it.\n"
            "\n"
            "A single shared fixture is not by itself a control, because copying one back "
            "is a three-line edit. What makes it a control is that the copy is refused: "
            "caught by `test_no_test_file_skips_from_inside_a_fixture` (a skip is a "
            "suite-wide decision, not a file's) and by "
            "`test_no_test_file_defines_a_fixture_conftest_already_defines` (shadowing is "
            "the mechanism). Two tests rather than one because the fourth occurrence was "
            "a module-level function feeding `skipif`, which the fixture rule alone does "
            "not see."
        ),
        min_kills=2,
    ),
    # ─── the Bedrock client (src/llm/bedrock.py, DESIGN §10 A2) ─────────────
    # Five guarantees, and none of them is about correctness of output. Each one is a
    # thing the client refuses to do, which is the hard kind to test: the mutated
    # client returns a perfectly good `Response` in four of the five cases, and the
    # run it belongs to produces a rule file and a metrics.json either way.
    Mutation(
        name="logging_gate_defaults_to_open",
        path=BEDROCK,
        anchor="    if not module.checked_today():",
        replacement="    if False:",
        breaks=(
            "The gate stops being a gate. Every call goes through with no "
            "model-invocation logging check on record, which is the state "
            "`compliance.md` §3 says cannot be assumed — it is a mutable account "
            "setting and yesterday's `None` is evidence about yesterday.\n"
            "\n"
            "**Nothing observable changes.** The call succeeds, the arm writes its "
            "artefact, the scores are the same numbers. If logging happens to be "
            "enabled, Bedrock is writing the full prompt — which carries ±120 "
            "characters of dev-fold corpus context per span (`rule_author.md` §1.4) — "
            "to a bucket in this account, and the only difference visible from inside "
            "the run is that the run worked. That is why the gate is a refusal rather "
            "than a warning, and why this mutation has to be caught by a test rather "
            "than by anyone noticing.\n"
            "\n"
            "Caught by `test_the_gate_blocks_the_call_when_no_check_is_recorded` and "
            "`test_a_record_for_another_day_does_not_open_the_gate`. The second is the "
            "one that matters here: an open gate and a gate that accepts a stale "
            "record are the same failure at different speeds."
        ),
        min_kills=2,
    ),
    Mutation(
        name="absent_token_counts_default_to_zero",
        path=BEDROCK,
        anchor=(
            '    prompt_tokens = usage.get("inputTokens")\n'
            '    completion_tokens = usage.get("outputTokens")'
        ),
        replacement=(
            '    prompt_tokens = usage.get("inputTokens", 0)\n'
            '    completion_tokens = usage.get("outputTokens", 0)'
        ),
        breaks=(
            "A partial `usage` block becomes a cost block reading zero tokens. Not a "
            "missing measurement — a measurement asserting the call consumed nothing, "
            "sitting in the same column as real counts.\n"
            "\n"
            "CLAUDE.md requires cost beside quality precisely so that an improvement "
            "bought at twice the price is legible as one. A zero does not weaken that "
            "comparison, it strengthens it in the wrong direction: the arm that lost a "
            "`usage` field looks free. And the two-argument `.get` is the natural edit "
            "— it removes an exception from a code path nobody has seen fire, which is "
            "how defensive zeroes get added.\n"
            "\n"
            "Caught by `test_a_partial_usage_block_is_refused`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_mismatched_model_is_recorded_rather_than_refused",
        path=BEDROCK,
        anchor=(
            "        raise BedrockError(\n"
            '            f"the response reports model {reported!r} for a request naming "'
        ),
        replacement=(
            "        return check_model_resolution(MISMATCH)\n"
            "        raise BedrockError(\n"
            '            f"the response reports model {reported!r} for a request naming "'
        ),
        breaks=(
            "A response naming a different model than the request is written down "
            "instead of stopping the run. This is the mutation that looks like an "
            "improvement: it uses the declared vocabulary, it loses no information, and "
            "the disagreement ends up in `metrics.json` where a reader could find it. "
            "Recording it is strictly more data than refusing.\n"
            "\n"
            "It is still wrong, and the reason is what `mismatch` is for. The rung's "
            "output is attributed to a model in the paper, and a `mismatch` row means "
            "nobody can say which model produced it — so the artefact is unusable for "
            "the one purpose it exists for, and writing it down does not make it usable. "
            "A refused call costs one re-run; a recorded mismatch costs a number in "
            "§10 A2 that cannot be attributed and will not be noticed until someone "
            "greps the resolution column. `naming.yaml` declares the value so the "
            "refusal can name it; declaring it is not permission to emit it.\n"
            "\n"
            "Caught by `test_a_response_naming_a_different_model_is_refused` and "
            "`test_a_mismatch_stops_the_invoke_rather_than_being_recorded` — two tests "
            "because the second is the one that fails if a later caller catches the "
            "error and carries on."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_client_hardcodes_botocores_default_attempts",
        path=BEDROCK,
        anchor='        config=Config(retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"}),',
        replacement='        config=Config(retries={"max_attempts": 3, "mode": "standard"}),',
        breaks=(
            "One `invoke()` becomes up to three calls to Bedrock. `MAX_ATTEMPTS = 1` and "
            "its comment stay exactly as they are, which is the point — the constant "
            "still documents a guarantee the code no longer keeps, and the module "
            "docstring still says the transport is pinned.\n"
            "\n"
            "The damage is in the cost column and it is invisible: `Response.cost()` "
            "reports `llm_calls: 1` because the type is one call, so a throttled run "
            "bills three times and reports once. §10 A2 fixes format retries at zero on "
            "both arms so that a format failure is reportable rather than retried away; "
            "a transport that retries underneath that undoes it silently, and the arm "
            "that got throttled is the arm that looks cheap.\n"
            "\n"
            "Caught by `test_the_client_builder_passes_the_pinned_attempts`, which reads "
            "`_client`'s syntax tree and requires the name rather than the number. "
            "Structural because the behavioural check needs a throttle to fire."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_reply_text_is_taken_from_the_first_block",
        path=BEDROCK,
        anchor='    parts = [b["text"] for b in blocks if isinstance(b, Mapping) and "text" in b]',
        replacement=(
            '    parts = [b["text"] for b in blocks[:1] if isinstance(b, Mapping) and "text" in b]'
        ),
        breaks=(
            "Reverts the client to the shape the response *looks* like it has. This one "
            "is not hypothetical: it is what was written first, and it failed on the "
            "first real call. This model returns `reasoningContent` and *then* `text`, "
            "so the first block carries no text and a good reply is reported as having "
            "none.\n"
            "\n"
            "Worth keeping as a mutation rather than just as a fixed bug, because the "
            "fix is invisible in a fixture written by whoever holds the wrong model of "
            "the shape. A one-text-block fixture passes under both versions, and that "
            "is the fixture anyone would write from the API docs — which is why the "
            "fixtures in `test_bedrock.py` put a reasoning block first by default.\n"
            "\n"
            "Caught by `test_the_text_is_found_after_a_reasoning_block`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_logging_check_reports_an_unreadable_setting_as_clean",
        path=CHECK_LOGGING,
        anchor="        raise LoggingCheckError(\n            f\"could not read the logging configuration in {region}: \"",
        replacement="        return region, CLEAN\n        raise LoggingCheckError(\n            f\"could not read the logging configuration in {region}: \"",
        breaks=(
            "An IAM denial becomes a clean bill of health. The tool then appends a dated "
            "record saying logging is off in a region where it could not be read, the "
            "client's gate opens on that record, and `compliance.md` — the file the "
            "paper's ethics section cites — carries a measurement nobody made.\n"
            "\n"
            "This is the single worst failure in the pair of files, because it is the one "
            "that manufactures evidence rather than losing it. It is also the plausible "
            "edit: the check already tolerates six regions of which three are not used, "
            "and `AccessDeniedException` in `ap-northeast-2` reads like noise to be "
            "skipped. `cloudtrail:DescribeTrails` already returns exactly that for this "
            "principal (§3), so the case is live rather than imagined.\n"
            "\n"
            "**It SURVIVED when it was first written, and that is why it is here.** The "
            "first `test_check_bedrock_logging.py` patched `check_all` out in every test, "
            "so the region-level error path — the only place a denial becomes a refusal — "
            "was never executed by anything. Twenty tests passed, the tool was green, and "
            "the guarantee had no coverage at all. A test that patches out the function "
            "holding the guarantee cannot test it, and nothing but a mutation says so.\n"
            "\n"
            "Now caught by `test_an_unreadable_region_raises_rather_than_reporting_it_"
            "clean`, `test_an_unreadable_region_fails_the_run_and_appends_no_record` (the "
            "run failing while the row is still written would leave the gate open anyway), "
            "`test_a_transport_failure_is_also_not_a_clean_result` and "
            "`test_check_all_stops_at_the_first_unreadable_region` — all four driving a "
            "fake `boto3.client` so that the real `check_region` runs."
        ),
        min_kills=2,
    ),
    # ─── the lifecycle probe (2026-08-11, DESIGN §4's dated pin) ────────────
    # Four guarantees, and the shape of the risk is unusual: this probe is *optional*
    # metadata, so three of the four mutations below make the arm run better rather than
    # worse — a probe that raises, a record with a message in it, a block moved somewhere
    # more convenient. What each one damages is either the arm's one unrepeatable call or
    # the reading of a claim, and neither is visible in a passing run.
    Mutation(
        name="the_lifecycle_probe_can_abort_the_arm",
        path=BEDROCK,
        anchor="    except Exception as exc:                                        # noqa: BLE001",
        replacement="    except ValueError as exc:",
        breaks=(
            "A supplementary metadata lookup regains the power to stop the call. The four "
            "live failure modes are botocore's `ClientError`, a credentials error, a "
            "missing key in a changed envelope, and `ImportError` with no boto3 — none of "
            "them a `ValueError`, so every one of them now raises out of `model_lifecycle` "
            "and out of `run_arm` before `invoke()`.\n"
            "\n"
            "**What that costs is the arm.** The probe sits before the call precisely so a "
            "surprise happens while the run is still repeatable, and this mutation converts "
            "that safety into its opposite: the freeze has already been taken "
            "(`freeze_window` runs first), so an arm killed here has a frozen window, no "
            "call log line, and a metadata endpoint as the reason. Narrowing a bare "
            "`except` is also the most reviewable-looking edit in this file — the comment "
            "above it exists because 'catch what you mean' is right everywhere else.\n"
            "\n"
            "Caught by `test_the_probe_never_raises_whatever_the_failure_is` (four "
            "exception types, one of them deliberately not a `ValueError`) and "
            "`test_a_failed_probe_does_not_stop_the_arm`."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_probe_error_carries_the_exception_message",
        path=BEDROCK,
        anchor='            "probe_error": type(exc).__name__,',
        replacement='            "probe_error": str(exc),',
        breaks=(
            "CLAUDE.md's rule about exception text, one function over from where it is "
            "usually enforced. This dict is written to three files — `agent_calls.jsonl`, "
            "`metrics.json` and `paths.formatfailure` — and the first is deny-listed by "
            "`release_screen.py`, so nothing screens what lands in it.\n"
            "\n"
            "The message is the leak surface: a botocore error can quote the request it "
            "failed on, and the id it failed on is assembled from an argument. The rule is "
            "not conditional on this exception being about a model rather than a span "
            "(CLAUDE.md: not corpus-dependent, because a rule that varies by context is a "
            "rule whose violations go quiet). A type name is more than a null and less "
            "than a guess, which is exactly what a supplementary field should carry.\n"
            "\n"
            "Caught by `test_the_probe_error_is_a_type_name_and_never_the_message`, which "
            "raises with an invented surface form inside the message text."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_lifecycle_block_moves_into_the_run_block",
        path=RUN_FOLD,
        anchor='        "model_id": model_id_absent(),\n        **dict(model_record or {}),',
        replacement=(
            '        "model_id": model_id_absent(),\n'
            '        **dict(model_record or {}),\n'
            '        **dict(model_lifecycle or {}),'
        ),
        breaks=(
            "The one thing this record must never do. `start_of_life_time` is when the "
            "*id* appeared in Bedrock's catalogue and says nothing about which weights "
            "answered — measurement 4 of `docs/notes/baseline-model-family.md` establishes "
            "that and `GetInferenceProfile` closes the other route. Spread into the run "
            "block it sits beside `model_id_resolution`, where a reader takes it as "
            "evidence for the resolution verdict.\n"
            "\n"
            "**This is the sixth mutation family reintroduced as data.** That family is a "
            "mechanism resolving an ambiguity it cannot see, in the reassuring direction; "
            "the sixth member was a comment asserting a causal link nothing established. A "
            "timestamp filed under identity is the same defect where a comment cannot warn "
            "anyone, because it reaches `metrics.json` and a reader who never opens the "
            "module. The mutation is also *tidier* than the real code — one dict spread "
            "instead of an argument threaded through two functions.\n"
            "\n"
            "**It survived its first writing, with one killing test.** `MODEL_FIELDS` was "
            "assumed to cover this and does not: that check constrains `model_record`, and "
            "this mutation spreads a *different* argument into the same dict, one line "
            "below the check. `MODEL_FIELDS` also had no test of its own anywhere in the "
            "suite — a closed set nothing exercised. Both gaps were the same shape: a "
            "guarantee credited to a mechanism that does not reach it.\n"
            "\n"
            "Now caught by `test_the_lifecycle_record_stays_out_of_the_run_block` and "
            "`test_model_record_is_a_closed_set` in `tests/test_run_fold.py` (the writer's "
            "own layer), and by "
            "`test_no_home_flattens_the_lifecycle_fields_in_beside_the_model_ids` and "
            "`test_the_lifecycle_block_is_top_level_and_not_in_the_run_block` downstream."
        ),
        min_kills=2,
    ),
    Mutation(
        name="an_empty_lifecycle_mapping_is_written_as_no_probe",
        path=SCORER,
        anchor="    if model_lifecycle is not None and not model_lifecycle:",
        replacement="    if False:",
        breaks=(
            "The two states this block distinguishes collapse. `model_lifecycle()` returns "
            "an `unavailable` record for every failure and never an empty one, so an empty "
            "mapping reaching the writer means a caller assembled the block itself and lost "
            "a distinction on the way. Written as absent, it says the arm called no model — "
            "which is the `R` arm's record, and the opposite of what happened.\n"
            "\n"
            "The mutation looks like tolerance: an empty dict and no dict do produce the "
            "same file, so refusing one reads as pedantry. What it protects is that "
            "absence stays readable — the reason `SCHEMA_VERSION` moved for an optional "
            "field at all.\n"
            "\n"
            "Caught by `test_an_empty_lifecycle_mapping_is_refused`."
        ),
        min_kills=1,
    ),
    # ─── the seal's own modules (DESIGN §6) ─────────────────────────────────
    # An audit found `sealed_log.py` and `run_sealed_eval.py` had no mutation at all:
    # both seal mutations were in `base.py`, the call sites. So it was checked that
    # the log is appended and that the split is verified, and never checked what
    # either of those things does. These five aim at the functions themselves.
    Mutation(
        name="an_unreadable_tree_state_reads_as_clean",
        path=SEALED_LOG,
        anchor=(
            "    if commit is None or porcelain is None:\n"
            '        return commit, "unknown"'
        ),
        replacement=(
            "    if False:\n"
            '        return commit, "unknown"'
        ),
        breaks=(
            "git cannot be reached and the tree is reported **clean**. `porcelain` is "
            "`None`, `if porcelain` is false, and the falsy branch already says clean — "
            "so removing the guard does not produce an error, it produces the most "
            "reassuring answer available.\n"
            "\n"
            "**The second appearance of one question in this repository: what happens "
            "when the state cannot be read?** The first was "
            "`check_region`'s `except ClientError` in `tools/check_bedrock_logging.py`, "
            "where an IAM denial became a clean bill of health. Same shape, same "
            "direction, and both were written correctly and executed by nothing. Here "
            "the consequence is that `load_sealed` — which refuses anything that is not "
            "`clean` — proceeds, and `results/sealed_eval_log.md` gets a row asserting "
            "the tree was clean at a commit nobody could confirm. The row count is the "
            "paper's N and the tree column is what makes each row mean something.\n"
            "\n"
            "Caught by `test_a_directory_that_is_not_a_repository_is_unknown`, "
            "`test_a_repository_with_no_commits_is_unknown`, "
            "`test_git_being_absent_is_unknown_and_not_clean`, "
            "`test_unknown_is_not_clean_and_is_therefore_refused` and "
            "`test_an_unknown_tree_state_is_recorded_as_unknown` — none of which existed "
            "before the audit, when this branch had zero executions."
        ),
        min_kills=3,
    ),
    Mutation(
        name="a_dirty_tree_reads_as_clean",
        path=SEALED_LOG,
        anchor='    return commit, "dirty" if porcelain else "clean"',
        replacement='    return commit, "clean"',
        breaks=(
            "The state is never dirty. Every run proceeds — `load_sealed`'s refusal is "
            "intact and unreachable — and every row records `clean`, so the log says the "
            "commit describes the code that ran when it does not.\n"
            "\n"
            "This is the mutation the pre-audit suite could not catch, and the reason is "
            "worth stating: `test_a_dirty_tree_is_refused_by_default` patches "
            "`tree_state` to return `(\"abc123\", \"dirty\")`. It proves the refusal fires "
            "when told the tree is dirty. **It cannot notice that nothing ever tells it "
            "so.** A guarantee split across a detector and a refusal needs both tested, "
            "and patching one to test the other leaves the detector with no coverage at "
            "all.\n"
            "\n"
            "Caught by `test_a_modified_tracked_file_makes_the_tree_dirty`, "
            "`test_an_untracked_file_makes_the_tree_dirty`, "
            "`test_a_staged_but_uncommitted_change_makes_the_tree_dirty`, "
            "`test_committing_the_change_makes_it_clean_again` and "
            "`test_the_row_records_the_repositorys_real_tree_state` — all against a real "
            "repository in `tmp_path`, because the thing under test is what `git status` "
            "output is turned into."
        ),
        min_kills=3,
    ),
    Mutation(
        name="only_tracked_modifications_count_as_dirty",
        path=SEALED_LOG,
        anchor='    porcelain = _git("status", "--porcelain")',
        replacement='    porcelain = _git("diff", "--name-only")',
        breaks=(
            "A subtler version of the one above, and the plausible edit — `git diff` is "
            "what a person reaches for when they mean \"is anything changed\". It reports "
            "unstaged modifications to tracked files only, so an **untracked** file and a "
            "**staged** change both read as clean.\n"
            "\n"
            "Both omissions matter here. An untracked `.py` is code that would run and is "
            "in no commit; a staged change is exactly the state of a tree mid-`git add`, "
            "which is when someone is most likely to also run an evaluation. The mutation "
            "leaves a `dirty` state that fires for the one case anyone would test by hand, "
            "which is what makes it survivable.\n"
            "\n"
            "Caught by `test_an_untracked_file_makes_the_tree_dirty` and "
            "`test_a_staged_but_uncommitted_change_makes_the_tree_dirty` — the two cases "
            "written because `--porcelain` distinguishes three markers and `if porcelain:` "
            "is a truth test over all of them."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_frozen_split_check_ignores_a_moved_document",
        path=RUN_SEALED,
        anchor="        if assigned.get(doc.doc_id) != doc.split:",
        replacement="        if assigned.get(doc.doc_id) is None and False:",
        breaks=(
            "Drift stops being detected. The corpus on disk may have moved a document "
            "between folds since the freeze and the sealed evaluation runs anyway, on a "
            "corpus the split file no longer describes — after which no published number "
            "can be tied to a fold, and a document could have crossed the seal in either "
            "direction.\n"
            "\n"
            "**Before the audit this function was patched out in both tests that "
            "mentioned it and executed by none, and no mutation aimed at it.** It was a "
            "guard that existed, was documented, was called in the right order, and had "
            "never once run. That combination is the failure this whole file is written "
            "against: the call site was tested and the callee was not.\n"
            "\n"
            "Caught by `test_a_document_that_moved_folds_is_refused` and "
            "`test_a_document_absent_from_the_frozen_file_is_refused`."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_frozen_split_is_verified_after_the_read",
        path=RUN_SEALED,
        anchor=(
            "    loader = _loader_for(corpus_id)\n"
            "    # Verified before the sealed read, not after: if the frozen split and the"
        ),
        replacement=(
            "    loader = _loader_for(corpus_id)\n"
            "    if True:\n"
            "        docs = loader.load(sealed=True, purpose=purpose, arms=\"none\")\n"
            "        _verify_frozen_split(loader, corpus_id)\n"
            "        return docs\n"
            "    # Verified before the sealed read, not after: if the frozen split and the"
        ),
        breaks=(
            "The check still runs, still raises on drift, and is worthless: the fold has "
            "already been opened and the log has already spent a row. A refusal after the "
            "read tells you the numbers are invalid *and* that the test set was consumed "
            "producing them, which is the one outcome the ordering exists to prevent — "
            "`sealed_eval_log.md` cannot un-count a run.\n"
            "\n"
            "Ordering mutations are the kind a behavioural suite misses most easily, "
            "because every assertion about the outcome still holds: drift still raises, "
            "clean still passes. What changed is only *when*.\n"
            "\n"
            "Caught by `test_the_check_runs_before_the_sealed_read`, which records the "
            "order the two are called in rather than their results."
        ),
        min_kills=1,
    ),
    # ─── the structural check (tests/test_structure.py) ─────────────────────
    # The check that a guarantee is never only patched away. Its own two weakenings:
    # one loosens the verdict into a subset test, one removes the reason requirement
    # from the exemption list. Both leave a check that runs, reports, and exits 0.
    Mutation(
        name="the_patch_check_credits_a_whole_file",
        path=PATCH_CHECK,
        anchor=(
            "    return any(os.path.basename(ran_in) == base and ran == function\n"
            "               for ran_in, ran in executed)"
        ),
        replacement=(
            "    return any(os.path.basename(ran_in) == base\n"
            "               for ran_in, ran in executed)"
        ),
        breaks=(
            "The verdict becomes a subset test: **one executed function in a module "
            "vouches for every patched function in it.** `src/eval/sealed_log.py` has "
            "`record_access` running in a dozen tests, so `tree_state` would be credited "
            "without ever running — which is precisely the state the audit found and this "
            "check was written to report. The check still runs, still prints a count, and "
            "exits 0.\n"
            "\n"
            "This is the weakening to expect, because it is what a false positive tempts "
            "you into. `satisfies` already had to be loosened once for a real reason (a "
            "test that imports a *copy* of a module), and the next loosening in the same "
            "direction is this one. The difference is that the copy case keeps the "
            "function identity and this discards it.\n"
            "\n"
            "Caught by `test_execution_of_a_different_function_does_not_satisfy_a_candidate`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_patch_check_credits_a_bare_function_name",
        path=PATCH_CHECK,
        anchor=(
            "    return any(os.path.basename(ran_in) == base and ran == function\n"
            "               for ran_in, ran in executed)"
        ),
        replacement="    return any(ran == function for _ran_in, ran in executed)",
        breaks=(
            "The other subset weakening, in the other axis: the file is dropped and a "
            "same-named function anywhere satisfies the candidate. `axis` is defined in "
            "`src/corpora/base.py` and would be credited by any `axis` in any module, "
            "including one a test defined itself.\n"
            "\n"
            "Kept as a separate mutation from the one above because the two loosenings "
            "fail differently and a reviewer would accept them for different reasons — "
            "this one looks like tolerance for import aliasing, that one like tolerance "
            "for module copies.\n"
            "\n"
            "Caught by `test_execution_of_a_same_named_function_elsewhere_does_not_satisfy`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_patch_allowlist_stops_requiring_a_reason",
        path=PATCH_CHECK,
        anchor='        if len(entry.get("why", "").split()) < MIN_WHY_WORDS:',
        replacement='        if False:',
        breaks=(
            "An exemption no longer has to say why. `{\"file\": ..., \"function\": ...}` "
            "becomes a valid entry, and the fastest way to close a finding stops being "
            "*run the function* and becomes *add two lines of JSON*.\n"
            "\n"
            "The cost is deferred rather than immediate, which is what makes it "
            "survivable: nothing breaks today. What breaks is the review in six months, "
            "when nobody can tell whether an entry describes a function that genuinely "
            "cannot be executed or one that was inconvenient on a Friday. "
            "`tools/screen_allowlist.json` has the same requirement for the same reason, "
            "recorded there as: an entry nobody can evaluate later is an entry that gets "
            "renewed forever.\n"
            "\n"
            "Caught by `test_an_entry_must_carry_a_reason` and "
            "`test_an_entry_with_no_reason_at_all_is_refused`."
        ),
        min_kills=2,
    ),
    Mutation(
        name="a_stale_patch_exemption_is_ignored",
        path=PATCH_CHECK,
        anchor="    stale = sorted(set(allowed) - set(found))",
        replacement="    stale = []",
        breaks=(
            "An exemption outlives the function it describes. Rename `tree_state` or stop "
            "patching it, and the entry stays — pointing at nothing, and ready to cover "
            "whatever takes the name next. An exemption granted for one function silently "
            "becomes an exemption for its replacement, which is the failure mode of every "
            "allowlist that is never pruned.\n"
            "\n"
            "Caught by `test_a_stale_entry_fails_rather_than_being_ignored`."
        ),
        min_kills=1,
    ),
    # DESIGN §10 A2's mitigation, as schema 4 made it a property. Three mutations: one
    # drops the requirement, one drops the validation that keeps the requirement worth
    # having, and one widens the null-hash exemption into a way of omitting the hash.
    Mutation(
        name="the_provenance_fields_are_optional_again",
        path=SCORER,
        anchor=('REQUIRED_RUN = ("corpus", "detector", "supervision", "porting", "split", '
                '"model_id",\n                "generated", "commit", "tree")'),
        replacement=('REQUIRED_RUN = ("corpus", "detector", "supervision", "porting", '
                     '"split", "model_id")'),
        breaks=(
            "§10 A2 returns to the state it was written up in: the design names a date and "
            "a commit hash as its partial mitigation for an unresolvable model alias, and "
            "no results file has to carry either. `run_fold` still writes all three, so "
            "every existing metrics file looks identical and nothing fails — the loss is "
            "entirely in what a *new* writer is allowed to omit, and the orchestrator is "
            "the new writer.\n"
            "\n"
            "The consequence is the one A2 states: a re-run six months later that produces "
            "a different number cannot be attributed to the model or to the code. The "
            "mitigation does not bound what the alias resolved to; it bounds *when* it was "
            "resolved and by which revision, and a field the writer may omit bounds "
            "nothing for the runs that omit it.\n"
            "\n"
            "Caught by `test_a_run_without_the_provenance_fields_is_refused` (three "
            "parametrisations), `test_all_three_are_required_and_not_just_the_hash` and "
            "`test_the_commit_key_is_required_even_though_its_value_may_be_null`."
        ),
        min_kills=3,
    ),
    Mutation(
        name="generated_accepts_a_bare_date",
        path=SCORER,
        anchor=r'GENERATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")',
        replacement=r'GENERATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")',
        breaks=(
            "`generated` becomes a date, and a date cannot order two runs made on one day "
            "— which is the comparison the field exists for. A2's question is which of two "
            "numbers came from the earlier resolution of an alias, and on the day an alias "
            "moves, both runs carry the same string.\n"
            "\n"
            "This is the milder shape of the same failure as the mutation above: the field "
            "is still required, so the block still looks complete, and what is missing is "
            "the resolution that made it answer anything. A required field whose validation "
            "is loosened is worse than an absent one — an absent field is legible as "
            "missing, and `2026-08-09` reads as a measurement.\n"
            "\n"
            "Caught by `test_generated_must_be_a_utc_instant`, whose first parametrisation "
            "is exactly `2026-08-09`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_null_commit_needs_no_unknown_tree",
        path=SCORER,
        anchor='    if not run["commit"] and run["tree"] != "unknown":',
        replacement='    if False:',
        breaks=(
            "The null-hash exemption stops being paired, and a nullable field becomes an "
            "optional one. `{\"commit\": null, \"tree\": \"clean\"}` is then accepted: a "
            "run that read the working tree successfully — which means it ran a git "
            "command that also produced a revision — and recorded no revision. That is not "
            "a missing measurement, it is a contradiction, and it is the way to omit the "
            "hash while satisfying every other check.\n"
            "\n"
            "The exemption itself is right and its scope is the whole guarantee: null is "
            "permitted because `tree_state()` genuinely returns `(None, \"unknown\")` when "
            "git cannot be read, and a validator that refused it would force a writer to "
            "fabricate a hash — the thing `tree` exists to prevent. Unpaired, the exemption "
            "grants that same freedom to runs that had a hash available.\n"
            "\n"
            "Caught by `test_the_null_hash_is_accepted_only_with_an_unknown_tree`."
        ),
        min_kills=1,
    ),
    # ── the agent arm's window freeze (DESIGN §6.3) ──────────────────────────
    Mutation(
        name="the_arm_freeze_guard_only_checks_the_file",
        path=ORCHESTRATE,
        anchor="    where = called_where(corpus, detector, supervision, porting)\n"
               "    if where is not None:",
        replacement=(
            "    where = IN_LOG if freeze_path(\n"
            "        corpus, detector, supervision, porting).exists() else None\n"
            "    if where is not None:"
        ),
        breaks=(
            "**The defect this repository actually shipped, moved to the arm that will "
            "run.** The refusal stops asking whether the arm has called and asks whether "
            "the record is present, so `rm window_freeze.json` followed by a second "
            "freeze writes today's hashes and reports a successful freeze — the exact "
            "sequence `docs/notes/window-freeze-history.md` records running three times "
            "before iteration 1, each time reported honestly as a re-freeze and each time "
            "entirely outside the guard.\n"
            "\n"
            "A refusal conditioned on the presence of the thing being protected is not a "
            "refusal; it is a request addressed to whoever can remove the evidence. And "
            "for this arm the consequence is worse than it was for `port-human`: "
            "`port-oneshot` writes no per-line hashes (§6.3 permits that because *n*=1 "
            "makes the freeze and the call one moment), so the record is the only thing "
            "attesting to the window the call ran under. There is no second source to "
            "disagree with a rewritten one.\n"
            "\n"
            "Note the mutation also *refuses* in the ordinary pre-call case, where §6.3 "
            "permits a re-freeze — so it is caught from both directions, which is why the "
            "count is high. That is the shape of the original defect too: it was wrong "
            "about when to refuse and wrong about when to allow, and the second half was "
            "the half nobody noticed."
        ),
        min_kills=3,
    ),
    Mutation(
        name="the_freeze_record_drops_the_empty_block_marking",
        path=ORCHESTRATE,
        anchor='        "sections_shown": list(shown),\n        "sections_empty": list(empty),',
        replacement='        "sections_shown": list(shown),',
        breaks=(
            "The record stops saying which blocks the call did *not* carry, so a reader "
            "has to derive it from this module's `INPUT_BLOCKS` — and a reader who knows "
            "`INPUT_BLOCKS` is not the reader the field is for. `sampling_applied` "
            "survives, which is what makes this worth its own mutation rather than being "
            "folded into the next: the record still distinguishes the two cases and it no "
            "longer says what the distinction is about.\n"
            "\n"
            "The failure is a `port-oneshot` record that hashes `config/sampling.yaml` "
            "and lists §§1.1–1.2 as shown. Nothing in it is false. What it cannot answer "
            "is whether §§1.3–1.4 were empty by design or whether this arm simply did not "
            "record them, and DESIGN §4 makes that the difference between the baseline and "
            "a broken one."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_freeze_record_claims_the_sampling_parameters_applied",
        path=ORCHESTRATE,
        anchor='        "sampling_applied": SAMPLING_SECTION in shown,',
        replacement='        "sampling_applied": True,',
        breaks=(
            "The field stops being derived and becomes a constant, so every arm's record "
            "claims the sampling parameters governed its call. For `port-oneshot` that is "
            "false: it hashes the file and uses none of *n*, `min_per_type` or "
            "`context_chars`, because §4 truncates it before §1.4 exists.\n"
            "\n"
            "**This is the failure the field was added to prevent, restored.** A reader "
            "finding `sampling_sha256` in a record would conclude 40 spans at ±120 "
            "characters were shown — the same conclusion for the baseline and for "
            "`port-loop`, and wrong for one of them. §6.3 keeps the hash for "
            "comparability across arms, which is precisely why the record needs a field "
            "that says the hash did not govern this call."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_baseline_draws_error_spans",
        path=ORCHESTRATE,
        anchor="    shown = _check_sections(sections)",
        replacement=(
            "    from .porting.human_arm import initial_error_pool\n"
            "    from .sample import draw\n"
            "    _pool = initial_error_pool(corpus)\n"
            "    shown = _check_sections(sections)"
        ),
        breaks=(
            "**DESIGN §4's ladder condition broken in the direction that looks like an "
            "improvement.** The baseline draws the §1.4 pool, which at iteration 1 comes "
            "from an empty rule file — so `initial_error_pool()` derives it from the "
            "loader and the spans are dev **gold**, not model output being fed back.\n"
            "\n"
            "An arm shown 40 of those has dev information the other does not, and "
            "`port-loop` vs `port-oneshot` then differs in two things at once: whether "
            "gold spans were seen, and whether the arm continues. When `port-loop` wins, "
            "nothing in the record attributes the win to iteration — at the comparison "
            "the paper leads with. Worse, the arm that would look unfairly strong is the "
            "baseline, so the failure runs in the direction that flatters the rung above "
            "it.\n"
            "\n"
            "Caught structurally rather than behaviourally: "
            "`test_the_baseline_does_not_draw_or_render_error_spans` reads the module's "
            "AST for the call, because the plumbing is a two-line addition that a "
            "behavioural test would only notice once it changed a number."
        ),
        min_kills=1,
    ),
    # ── the arm itself: what it records, in which order (DESIGN §10 A2) ───────
    Mutation(
        name="the_call_is_logged_after_the_response_is_judged",
        path=ORCHESTRATE,
        anchor="    append_call(\n"
               "        call_line(ITERATION, prompt_reference=reference, model=model,",
        replacement="    _deferred = lambda: append_call(\n"
                    "        call_line(ITERATION, prompt_reference=reference, model=model,",
        also=((
            ORCHESTRATE,
            "    spans_file, metrics_file, scored = run_fold(",
            "    _deferred()\n"
            "    spans_file, metrics_file, scored = run_fold(",
        ),),
        breaks=(
            "The call log is written after the response has been through the validator "
            "instead of before, which is the freeze guard's premise read backwards. The "
            "line in `agent_calls.jsonl` is what fixes this arm's window — there are no "
            "per-line hashes to disagree with the record, because *n*=1 — so between the "
            "call and the log the window is still re-freezable, and a call that has already "
            "been made and paid for can be re-run under a different prompt with nothing on "
            "disk showing it.\n"
            "\n"
            "The deferred call still happens on the way to `run_fold`, which is what makes "
            "this the interesting shape rather than a dropped write: after a successful "
            "validation the log ends up byte-identical, so every assertion about the log's "
            "*contents* passes. What breaks is only the ordering — and it breaks visibly "
            "only in the branch where the response does not load, where the arm returns "
            "having made a call, paid for it, and recorded nothing.\n"
            "\n"
            "Caught by `test_the_call_is_logged_even_when_the_response_does_not_load` and "
            "`test_a_call_fixes_the_window_through_the_arm`. Note the second is the reason "
            "the first is not enough on its own: a suite that only checked the log after a "
            "*successful* run would be blind to this, which is why the arm tests drive the "
            "failure branch."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_format_failure_writes_zeroed_metrics_too",
        path=ORCHESTRATE,
        anchor="    except RuleError as exc:\n        failure = _write_failure(",
        replacement=(
            "    except RuleError as exc:\n"
            "        run_fold(corpus=corpus, detector=detector, supervision=supervision,\n"
            "                 porting=porting, split=split, model_record=model, cost=cost,\n"
            "                 root=ROOT)\n"
            "        failure = _write_failure("
        ),
        breaks=(
            "**DESIGN §10 A2's central distinction erased.** A format failure now leaves a "
            "`metrics.json` behind as well, scored over the bootstrap rule file — so the "
            "arm's directory holds a complete metrics file whose numbers are near zero, and "
            "that is indistinguishable from the opposite finding: a rule set that ran and "
            "caught almost nothing.\n"
            "\n"
            "This is why the failure is a *file name* rather than a `status` field inside "
            "the metrics. A2 reports how often each model family could not produce a "
            "loadable rule file, and it can only report it if that state has no metrics "
            "file — the moment both exist, an aggregation walking `results/` counts a "
            "format failure as a scored arm with a bad score, which understates capability "
            "and overstates compliance in the same number.\n"
            "\n"
            "Caught by `test_a_format_failure_writes_no_metrics_file`, which asserts the "
            "absence of both `metrics.json` and `spans.jsonl` rather than only the presence "
            "of `format_failure.json` — the presence assertion alone would pass here."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_arm_reports_no_model_and_no_cost_to_the_scorer",
        path=ORCHESTRATE,
        anchor="        split=split, rules={lang: rules_file}, model_record=model, cost=cost,",
        replacement="        split=split, rules={lang: rules_file},",
        breaks=(
            "The success branch stops telling `run_fold` what it called, so the published "
            "`metrics.json` carries `model_id: \"none\"` — the `naming.yaml` value meaning "
            "*this arm used no model* — and a cost block of three zeros, for an arm whose "
            "whole content is one LLM call.\n"
            "\n"
            "**Nothing about the resulting file looks wrong.** It is schema-valid, the run "
            "block is complete, and `model_id` holds a legitimate vocabulary value rather "
            "than a blank. It is the `R` arm's record written under `port-oneshot`, which "
            "makes the baseline appear to be a rules arm that cost nothing — and CLAUDE.md's "
            "requirement is precisely that cost travels beside quality, because an "
            "improvement obtained at 2× cost and one obtained at 1.05× are different "
            "results. Here the improvement reads as free.\n"
            "\n"
            "The defect is reachable because `run_fold` has to keep working without these "
            "arguments: it closes the `R` arm, which genuinely calls no model. So the "
            "absent-value default is correct in that module and wrong here, and only a test "
            "on this arm's metrics can tell the two apart — "
            "`test_the_metrics_record_the_model_that_was_called` and "
            "`test_the_metrics_cost_block_is_the_calls_and_not_zeros`."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_failure_record_paraphrases_the_validator",
        path=ORCHESTRATE,
        anchor="            split=split, model=model, response=response.text, error=str(exc),",
        replacement="            split=split, model=model, response=response.text,\n"
                    "            error=\"the response was not a valid rule file\",",
        breaks=(
            "The validator's own message is replaced by a summary of it, and §10 A2's third "
            "recorded content stops being evidence. \"The response was not a valid rule "
            "file\" is not something a reader can check and not something a later run can be "
            "compared against: every failure becomes the same failure, so a model that "
            "declared the wrong `lang`, a model that fenced its YAML and a model that "
            "invented a matcher key are one row in the appendix.\n"
            "\n"
            "The claim A2 makes is about a specific model's specific inability, and the "
            "paraphrase is the point at which that claim becomes unfalsifiable — the raw "
            "response is still on disk beside it, so the reader can re-derive the message, "
            "which is exactly the work the field existed to save and the reason nobody "
            "notices it is missing.\n"
            "\n"
            "Caught by `test_the_failure_record_holds_the_validators_own_message`, which "
            "asserts the message names the declared language it objected to."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_parse_error_quotes_the_line_it_choked_on",
        path=RULES,
        anchor="    with open(p, encoding=\"utf-8\") as fh:\n"
               "        try:\n"
               "            raw = yaml.safe_load(fh) or {}",
        replacement="    with open(p, encoding=\"utf-8\") as fh:\n"
                    "        try:\n"
                    "            raw = yaml.safe_load(fh.read()) or {}",
        also=((
            RULES,
            '                f"{p}: not parseable as YAML — {problem}"',
            '                f"{p}: not parseable as YAML: {exc}"  # noqa: F821\n'
            '                f""',
        ),),
        breaks=(
            "**CLAUDE.md's rule about exception text, in the file format where the parser "
            "volunteers the violation — and this is the mutation that shows the guarantee "
            "is not a coincidence.** Two edits, and they have to go together, which is the "
            "whole finding.\n"
            "\n"
            "`yaml.MarkedYAMLError.__str__` prints the offending source line whenever its "
            "`Mark` carries a buffer, and whether it does depends on how the input reached "
            "`pyyaml`: a stream leaves the buffer null (`yaml.reader.Reader.get_mark`), a "
            "string fills it. So `safe_load(fh)` → `safe_load(fh.read())` is the *first* "
            "edit — a refactor with no visible effect, matching what every other loader in "
            "this repository does — and interpolating `{exc}` instead of the picked-out "
            "`problem`/`context`/mark is the second. Either alone leaks nothing. Together "
            "they put the rule file's content into a `RuleError`.\n"
            "\n"
            "That content is an LLM's response, which can echo the §1.4 block of its own "
            "prompt, and the message goes to a terminal, a CI log, an issue and a stack "
            "trace — `tools/release_screen.py` reaches none of them. The mutation is also a "
            "*shorter and more helpful* message, which is how this class of defect always "
            "arrives: the debugging convenience is real and the leak is on the one path no "
            "screening covers.\n"
            "\n"
            "Caught by `test_the_parse_error_message_quotes_no_line_of_the_file`, whose "
            "response carries an invented surface form on the line *after* the syntax "
            "error. That is deliberate: the position `pyyaml` reports and the line it prints "
            "are different lines, so a test asserting on an offset would pass while the "
            "quoted line leaked, and the marker is what makes the assertion about content."
        ),
        min_kills=1,
    ),
    Mutation(
        name="delta_reverts_to_the_constant_half_point",
        path=TERMINATION,
        anchor='    d = max(params["delta_floor"], params["delta_spans"] / size)',
        replacement='    d = params["delta_floor"]',
        also=((
            TERMINATION,
            '    return max(params["delta_floor"], params["delta_spans"] / n_dev(corpus))',
            '    return params["delta_floor"]',
        ),),
        breaks=(
            "**δ becomes the constant 0.005 again, and on the corpus that exists today the "
            "number does not change.** es-meddocan's dev fold is 5,254 in-scope spans, above "
            "the `delta_spans / delta_floor` = 5,200 crossover, so the floor branch is the "
            "binding one there and `delta('es-meddocan')` returns 0.005 either way. Every "
            "test that exercises the real split file passes.\n"
            "\n"
            "What is lost is the thing DESIGN §3 pre-registered: the invariant is a **span "
            "count**, and the rate is derived from it. A fold five times smaller must show a "
            "rate five times larger to represent the same amount of found PHI, so a single "
            "fixed rate silently demands 26 spans on one corpus and 1.62 on GraSCCo's "
            "1,297-span corpus — a strict standard on the large fold and no standard at all "
            "on the small one, where 1.62 spans is inside the fold's own noise. The arm on "
            "the small corpus would then terminate on variation and report it as "
            "convergence.\n"
            "\n"
            "It is the sharpest mutation in this file for the reason the seal ones are: it "
            "produces **no wrong number at all today**. It becomes wrong on the corpus that "
            "has not been split yet, which is precisely why §3 pre-registered the formula "
            "before `de-grascco`'s split existed and why the four table rows are the formula "
            "evaluated in advance.\n"
            "\n"
            "Caught by `test_delta_is_the_span_count_on_every_fold_size` and "
            "`test_the_four_pre_registered_grascco_rows`, which work on synthetic fold sizes "
            "for exactly this reason — a δ test that only ever asks es-meddocan cannot see "
            "this edit."
        ),
        min_kills=2,
    ),
    Mutation(
        name="a_ceiling_stop_is_recorded_as_converged",
        path=TERMINATION,
        anchor="    if len(gains) >= k and all(g < d for g in gains[-k:]):\n"
               "        reason = CONVERGED\n"
               "    elif iterations >= ceiling:\n"
               "        reason = CEILING",
        replacement="    if len(gains) >= k and all(g < d for g in gains[-k:]):\n"
                    "        reason = CONVERGED\n"
                    "    elif iterations >= ceiling:\n"
                    "        reason = CONVERGED",
        breaks=(
            "**DESIGN §3's one prohibition, undone in one word.** An arm that ran out of "
            "iterations has not satisfied the convergence test, and §3 states that a "
            "ceiling-terminated run may not be described as converged — a run that stopped at "
            "8 with the leak rate still falling is a different claim from one that stopped at "
            "5 having converged.\n"
            "\n"
            "The mutated file is internally consistent, which is what makes it dangerous. "
            "`Termination.converged` is a property derived from `reason`, so it agrees; "
            "`check_termination` compares the two and finds them agreeing; the block is "
            "schema-valid and `iterations` still says 8. Nothing in the published file "
            "contradicts anything else in it. A reader would have to know the ceiling is 8 "
            "and *infer* that an 8-iteration convergence is suspicious — and the honest and "
            "the dishonest record differ only in a word whose meaning §3 supplies.\n"
            "\n"
            "This is why the guarantee is tested in three places rather than one. The "
            "property being derived from `reason` prevents a *contradictory* record; it does "
            "nothing about a wrong `reason`, and the scorer's cross-check is satisfied by "
            "this edit too. Only a test that reads the verdict for a still-improving arm at "
            "the cap can see it: `test_a_ceiling_stop_is_not_converged`, "
            "`test_the_ceiling_terminates_an_arm_that_is_still_improving`, and "
            "`test_convergence_wins_when_both_are_true` (which is what stops the fix from "
            "being 'check the cap first' — that would reclassify a real convergence as a "
            "budget exhaustion and understate the rule)."
        ),
        min_kills=2,
    ),
    Mutation(
        name="k_drops_to_one_so_consecutive_means_nothing",
        path=NAMING,
        anchor="  k: 2",
        replacement="  k: 1",
        breaks=(
            "**The stopping rule fires on a single thin iteration.** §3's k = 2 exists "
            "because the error sample is a seeded draw of 40 spans stratified by type (§1.4), "
            "so one iteration can land on a stratum the current rules already cover and "
            "produce a below-δ improvement for a reason unrelated to the arm having run out "
            "of ideas. k = 2 requires two independent draws to come back thin; k = 1 makes "
            "sampling variance indistinguishable from convergence.\n"
            "\n"
            "It also makes 'consecutive' vacuous — §3 records that k = 2 is the smallest "
            "value for which the word means anything at all, so this edit removes a clause "
            "from the pre-registration while leaving its wording in place.\n"
            "\n"
            "**Cost is where it would be argued for, and that is the trap.** Each k is a full "
            "RuleAuthor + Auditor + scorer pass spent purely to confirm a stop, roughly 135k "
            "tokens by §3's estimate, so k = 1 looks like a saving of one iteration in every "
            "run. What it actually buys is arms that stop early and report converged-looking "
            "results, and the run that was cut short is the one whose leak rate the paper "
            "quotes.\n"
            "\n"
            "The edit is in the config rather than a module, which is the point of the rule "
            "that a new value goes into `naming.yaml` first: the collision is visible in one "
            "committed file. Caught by `test_the_constants_are_the_pre_registered_ones`, "
            "`test_one_below_delta_iteration_is_not_convergence` and "
            "`test_convergence_needs_k_plus_one_iterations`."
        ),
        min_kills=3,
    ),

    # ─── the iteration-scoped paths (DESIGN §5.5) ──────────────────────────
    Mutation(
        name="the_audit_report_is_allowed_instead_of_denied",
        path=SCREEN,
        anchor='    r"(^|/)audit_report\\.json$",',
        replacement='    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/iter[0-9]+/audit_report\\.json$",',
        breaks=(
            "**The Auditor's report becomes a publishable path, and it still gets sniffed, "
            "so the mutation looks careful.** The edit does not delete the pattern — it "
            "moves the report from the deny list's shape to an ALLOW-shaped one, four axes "
            "and an iteration deep, exactly like the score beside it. Everything about it "
            "reads as tightening: it is more specific than the line it replaces.\n"
            "\n"
            "What it publishes is a list of positions an agent believes are surviving PHI in "
            "a DUA corpus — DESIGN §5.5 calls it the most concentrated such artefact the loop "
            "produces. The content sniffer does not save it. The file holds offsets, types "
            "and scores by construction and no surface forms, so it is exactly the kind of "
            "file `sniff()` passes: §6.1's allowlist argument runs the other way, that a path "
            "may be excused from the sniffer only when the path rules already publish it, and "
            "nothing requires publishing this one.\n"
            "\n"
            "It also un-gitignores nothing and so produces no BLOCKED line: the deny rule and "
            "the `.gitignore` entry are paired by "
            "`test_every_deny_listed_path_is_also_gitignored`, and a path that is ignored but "
            "not denied is reported as Quarantined — the class that reads as fine. Caught by "
            "`test_the_four_iteration_scoped_paths_split_two_and_two` and by the deny-sample "
            "sync tests, which fail on the pattern they no longer find."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_iteration_allow_pattern_covers_the_whole_directory",
        path=SCREEN,
        anchor='    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/iter[0-9]+/metrics\\.json$",',
        replacement='    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/iter[0-9]+/",',
        breaks=(
            "**The same publication, reached from the other side, and this is the shorter "
            "edit.** One pattern instead of two for the round's score and predictions — and "
            "it publishes the audit report and the per-span error export sitting in that same "
            "directory. The deny rules still catch both today, because `deny()` is consulted "
            "before ALLOW, so the *reported* classification does not change. What changes is "
            "that the two lists now disagree, and the disagreement is invisible until "
            "somebody edits either one.\n"
            "\n"
            "That is the reason §5.5 anchors these on filenames and the reason "
            "`config/naming.yaml` puts four files in one `iter{n}/` directory with two "
            "classifications: the directory is the wrong unit here, and a screener whose "
            "ALLOW list would publish a denied file is one deny-rule deletion away from doing "
            "it. Caught by `test_the_four_iteration_scoped_paths_split_two_and_two`, which "
            "asserts the denied two match *no* ALLOW pattern rather than only that they are "
            "denied."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_per_iteration_key_replaces_the_arm_level_one",
        path=NAMING,
        anchor='  metrics:  "results/{corpus}/{detector}/{supervision}/{porting}/metrics.json"',
        replacement='  metrics:  "results/{corpus}/{detector}/{supervision}/{porting}/iter{iteration}/metrics.json"',
        breaks=(
            "**The bullet DESIGN §5.5 had to correct, restored.** The superseded text said "
            "`paths.metrics` gains `{iteration}` *and* that the un-iterated path stays valid "
            "for the single-call arms, which cannot both hold of one template — a template is "
            "either formatted with an iteration or it is not. This edit takes the first half.\n"
            "\n"
            "`port-oneshot-nofence`'s `metrics.json` and `spans.jsonl` are committed at four "
            "axes deep. After this edit they are matched by no `ALLOW_PATTERNS` entry and "
            "reachable from no `metrics_path()` call, and §4 refused precisely that migration "
            "for a freeze record: a relocated result sits at a deeper path while nothing in "
            "its content records the move. The ladder's table also stops being a table — "
            "`port-loop`'s headline number would live at a path shape no other rung uses.\n"
            "\n"
            "Caught by `test_the_un_iterated_result_paths_are_still_allowed` and by "
            "`test_metrics_path_follows_the_naming_template`, which formats the template with "
            "the four axes and no iteration."
        ),
        min_kills=2,
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
