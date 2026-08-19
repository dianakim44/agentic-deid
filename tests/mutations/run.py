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
    "tests/test_audit.py",
    "tests/test_masked_tag.py",
    "tests/test_window_widening.py",
    "tests/test_call_role.py",
    "tests/test_loop.py",
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
#: The Auditor's flag validator. Mutated separately from the loop driver for
#: `src/termination.py`'s reason: it is the file that decides what a refusal means, and a
#: mutation to a refusal cannot then be confused with a bug in the thing that calls it.
AUDIT = "src/porting/audit.py"
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
#: `port-loop`'s driver. Mutated separately from `src/termination.py` for that constant's
#: reason read the other way: this file is what *obeys* the pre-registered rule and assembles
#: each round from the previous one, so a mutation to the chain cannot be confused with a
#: mutation to the rule it consults. The off-by-one family lives here — rounds are the only
#: place in this repository where a one-off produces a complete, well-formed, wrong artefact.
LOOP = "src/porting/loop.py"

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

    # ─── the round's cost and the arm's total (schema 7, DESIGN §11.3) ───────
    # `port-loop`'s iteration is 1 + N calls, so `metrics.json` carries two cost blocks and
    # the 1.9× standard is read off one of them. Every mutation here leaves a schema-valid
    # file with four plausible numbers in both blocks — none of them fails a shape check,
    # and the wrong figure is only wrong against a run history the file does not contain.
    Mutation(
        name="the_arms_total_is_the_last_rounds_cost",
        path=RUN_FOLD,
        anchor='        cost_to_date={**NO_LLM_COST, **dict(cost_to_date or cost or {}),\n'
               '                      "wall_seconds": to_date_seconds},',
        replacement='        cost_to_date={**NO_LLM_COST, **dict(cost or {}),\n'
                    '                      "wall_seconds": to_date_seconds},',
        breaks=(
            "**The arm's total becomes whatever the last round spent.** The accumulator the "
            "driver passes is dropped and the round's own block is written in both places, so "
            "an eight-iteration `port-loop` publishes a `cost_to_date` of roughly 2.2M tokens "
            "instead of 15.6M — and DESIGN §11.3's cost comparison is read off exactly that "
            "field.\n"
            "\n"
            "Nothing in the file contradicts anything else in it. Both blocks are complete and "
            "four-keyed, `check_cost_to_date` is satisfied because they are *equal* (the "
            "relation refuses only a total that is smaller), and equality is the correct state "
            "for every arm on the ladder except `port-loop` past iteration 1 — which is what "
            "makes the edit invisible to every non-iterating arm's tests. The number is wrong "
            "only against a run history no single file holds.\n"
            "\n"
            "Caught by `test_the_round_and_the_arm_are_written_as_the_two_numbers_they_are` "
            "and `test_run_fold_does_not_sum_costs_itself`, which is why those tests pass a "
            "total that differs from the round rather than a realistic-looking one."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_folds_seconds_go_to_the_round_and_not_the_arm",
        path=RUN_FOLD,
        anchor='    to_date_seconds = round(elapsed + float((cost_to_date or cost or {})\n'
               '                                            .get("wall_seconds", 0.0)), 3)',
        replacement='    to_date_seconds = round(float((cost_to_date or cost or {})\n'
                    '                                  .get("wall_seconds", 0.0)), 3)',
        breaks=(
            "The detection pass's time is added to the round's block and not to the arm's, so "
            "the total is below the sum of the rounds it contains — by one fold's compute per "
            "iteration, which on `es-meddocan` is the larger part of a rule pass. The round's "
            "own file then reports seconds the arm's figure denies.\n"
            "\n"
            "`wall_seconds` is the only key `run_fold` measures rather than passes through, so "
            "this is the one place the two blocks can drift by construction, and it drifts in "
            "the direction that makes the iterating arm look cheaper. Both blocks stay "
            "complete and plausible: a total above the round's calls and below their sum is "
            "not a contradiction any single file exposes, and `check_cost_to_date` passes "
            "because the total is still larger than the round's.\n"
            "\n"
            "Caught by `test_the_detection_pass_lands_in_both_blocks`, which measures the "
            "difference each block grew by rather than asserting either number."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_writer_adds_the_rounds_up_itself",
        path=SCORER,
        anchor="    to_date = dict(cost) if cost_to_date is None else dict(cost_to_date)",
        replacement="    to_date = sum_costs([cost] + ([] if cost_to_date is None\n"
                    "                                  else [dict(cost_to_date)]))",
        breaks=(
            "**A second accumulator, in the writer.** The scorer now adds the round into the "
            "total it was handed, so every round is counted twice — once by the driver's "
            "accumulator and once here — and an eight-iteration arm publishes roughly double "
            "its real spend.\n"
            "\n"
            "The shape is what makes it worth a mutation rather than the size of the error. "
            "The mutated writer's file agrees with itself perfectly: `cost_to_date` is above "
            "`cost`, both blocks are complete, and `check_cost_to_date` is satisfied *more* "
            "comfortably than before. It disagrees only with the run, and nothing records "
            "which of the two accumulators produced the published figure — the same shape "
            "§5.5's duplication rule and §3's stopping rule are both about, one producer per "
            "number.\n"
            "\n"
            "It also passes the non-iterating arms untouched in the one case they exercise: "
            "with `cost_to_date` omitted the sum of a single block is that block. So `R` and "
            "the `port-oneshot` rungs see nothing.\n"
            "\n"
            "Caught by `test_the_writer_does_not_derive_the_total_from_anything`, which hands "
            "the writer a total no sum of that round could produce."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_total_below_its_round_is_published",
        path=SCORER,
        anchor="    check_cost_to_date(cost, to_date)",
        replacement="",
        breaks=(
            "The one relation between the two blocks that a reader holding a single file "
            "cannot verify stops being checked. A `cost_to_date` below the `cost` it contains "
            "is a reset accumulator, or the two arguments passed the other way round — and "
            "the second is the likelier: both are `REQUIRED_COST` blocks with identical "
            "shapes, so no type error and no shape check can tell them apart.\n"
            "\n"
            "Swapped, the published file says round 3 spent the whole arm's budget and the arm "
            "spent one round's, and every downstream reading of §11.3's comparison is inverted "
            "while both blocks stay complete and well-formed. This is the guard-whose-"
            "precondition-was-never-asked shape: the property is true of a correct driver and "
            "unchecked at the writer, so it holds for one code path rather than for the file.\n"
            "\n"
            "Caught by `test_a_total_below_the_round_it_contains_is_refused`, "
            "`test_the_relation_is_checked_key_by_key` and "
            "`test_a_total_below_the_round_reaches_the_scorers_check`."
        ),
        min_kills=2,
    ),
    Mutation(
        name="summing_takes_the_longest_call_as_the_wall_time",
        path=SCORER,
        anchor="        for key in REQUIRED_COST:\n            total[key] += block[key]",
        replacement="        for key in REQUIRED_COST:\n"
                    "            if key == \"wall_seconds\":\n"
                    "                total[key] = max(total[key], block[key])\n"
                    "            else:\n"
                    "                total[key] += block[key]",
        breaks=(
            "`wall_seconds` becomes the longest single call instead of the calls' sum, so an "
            "iteration's 1 + N calls report the time of one of them. On a dev fold of any size "
            "the Auditor dominates the call count, and the arm's reported compute collapses to "
            "roughly one document's worth per round.\n"
            "\n"
            "It is the mutation with the best argument for it, which is why it is here: taking "
            "a maximum is what you would write if you thought the field were elapsed "
            "wall-clock, and for a concurrent driver it would be closer to right. The driver "
            "issues the Auditor's documents sequentially, so the sum is the honest number — "
            "and `sum_costs` says so in prose precisely because the alternative is defensible "
            "enough to be chosen silently. The token counts and `llm_calls` stay correct, so "
            "every test about the call count passes and the cost block stays four-keyed.\n"
            "\n"
            "Caught by `test_summing_adds_every_key_including_wall_seconds`, which is written "
            "over three calls with distinct times for this reason — two calls of equal length "
            "could not tell a sum from a maximum."
        ),
        min_kills=1,
    ),
    Mutation(
        name="summing_carries_an_undeclared_key_into_the_total",
        path=SCORER,
        anchor="        extra = sorted(set(block) - set(REQUIRED_COST))\n"
               "        if extra:\n"
               "            raise ScorerError(\n"
               "                f\"costs[{index}] has unexpected key(s) {extra}. The cost block "
               "is closed to \"",
        replacement="        extra: list[str] = []\n"
                    "        if extra:\n"
                    "            raise ScorerError(\n"
                    "                f\"costs[{index}] has unexpected key(s) {extra}. The cost "
                    "block is closed to \"",
        breaks=(
            "The cost block stops being closed on the way in. A caller passing Bedrock's own "
            "`inputTokens`/`input_tokens` beside `prompt_tokens` — the plausible edit, since "
            "those are the provider's names for the same quantity — now has it silently "
            "ignored by the sum rather than refused, and the total is short by whatever that "
            "field held.\n"
            "\n"
            "The failure is quiet in both directions. The sum still returns four keys, so the "
            "published block is valid and comparable-looking; and a reader who added a fifth "
            "key deliberately gets a total that does not include it, with nothing saying so. "
            "This is the `termination` block's closed-set argument at the cost block: a field "
            "this project never declared cannot be told from part of the cost model.\n"
            "\n"
            "Caught by `test_summing_refuses_a_key_the_cost_block_does_not_declare`."
        ),
        min_kills=1,
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

    # ─── the per-span error export (DESIGN §5.5, §9.3) ─────────────────────
    Mutation(
        name="the_export_index_is_the_in_scope_position",
        path=SCORER,
        anchor="                span_index=g.span_index, start=g.start, end=g.end,",
        replacement="                span_index=gi, start=g.start, end=g.end,",
        breaks=(
            "**The referent moves and every value stays a valid index.** `gi` is the gold "
            "span's position in the *in-scope* list — what `_records` is looping over "
            "— and the reference DESIGN §11.2 fixes is a position in the "
            "document's own `spans` list, which `from_documents` counts including the "
            "§9.1-excluded types. On a document whose first span is a "
            "`SEXO_SUJETO_ASISTENCIA` the two differ by one; on MEDDOCAN that is most "
            "documents, and the offset varies per document with how many excluded spans "
            "came before.\n"
            "\n"
            "Nothing anywhere looks wrong. `errors.jsonl` is well-formed, every "
            "`span_index` is a non-negative integer that resolves to a real span in the "
            "document, the offsets beside it are untouched and correct, and no metric "
            "changes at all — the index enters no numerator or denominator "
            "(`test_the_index_does_not_enter_any_metric`). The wrongness is visible only "
            "to whoever holds the corpus and resolves the reference, which is the property "
            "that made this the referent: inert to everyone else.\n"
            "\n"
            "What it costs is that iteration 1 and iteration 4 stop meaning the same thing "
            "by `(doc_id, span_index)`. `initial_error_pool()` enumerates the unfiltered "
            "list, so the bootstrap pool and every later round's pool would index two "
            "different lists while both files validated — and the sample drawn from "
            "them is deduplicated and diffed across rounds by exactly that pair. Caught by "
            "`test_span_index_is_the_documents_own_list_and_not_the_in_scope_subset`, "
            "which puts the excluded span *first* so a filtered index names span 0 for a "
            "span that is span 1."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_export_reads_the_missing_index_as_zero",
        path=SCORER,
        anchor="            if item.span_index is None:",
        replacement="            if False:",
        breaks=(
            "The refusal becomes a default. With the check gone, `ErrorSpan` is "
            "constructed with `span_index=None`, `__post_init__` compares it with `<` and "
            "raises a `TypeError` — which is *this* mutation's tell and not the "
            "guarantee: swap the constructor for `item.span_index or 0` and the same edit "
            "is silent. That is what the anchor is protecting. Every span with no index "
            "would be exported as span 0 of its document, which resolves to a real span "
            "and to the wrong one, and 40 window slots would be spent on references to "
            "whatever sits first in each document.\n"
            "\n"
            "The reason to refuse rather than substitute is that there is nothing to "
            "substitute *from*: at this point the in-scope position is the only number in "
            "hand and it is the wrong list (see "
            "`the_export_index_is_the_in_scope_position`). A `Mark` reaching here without "
            "an index means it was built directly rather than by `from_documents`, which "
            "is a caller bug and not a missing value. Caught by "
            "`test_a_mark_without_an_index_is_refused_rather_than_defaulted`, which also "
            "asserts the message locates the span as `[0, 4)` without quoting it "
            "(CLAUDE.md)."
        ),
        min_kills=1,
    ),
    Mutation(
        name="missed_is_the_unmatched_gold_rather_than_the_uncovered",
        path=SCORER,
        anchor="        source = ([(r.doc_id, r) for r in records if not r.covered]",
        replacement="        source = ([(r.doc_id, r) for r in records if not r.matched]",
        breaks=(
            "**The verdict computed on the assignment instead of on coverage — the "
            "one substitution that makes the export bigger and reads as more thorough.** "
            "`matched` is one-to-one credit and `covered` is whether the union of same-type "
            "predictions hid the span, and DESIGN §9.3's whole point is that these are "
            "two matchings answering two questions. Every uncovered span is unmatched, so "
            "the mutated export is a superset: the leak set plus `assignment_slack`.\n"
            "\n"
            "Every span it adds is an identifier that **is already hidden**. D1's gold "
            "`[0, 4)` is covered by one wide prediction and loses the assignment to its "
            "neighbour; shown to a rule author as missed, it asks for a rule against text "
            "that is already masked. So the arm spends its rounds writing rules for "
            "non-leaks, the leak rate — the headline §3's stopping rule is "
            "computed on — stops moving, and the run terminates on δ having "
            "improved nothing. The window's own header still says \"missed = leaked under "
            "fully_covered\" (`rule_author.md` §1.4), so the prompt asserts the "
            "definition the data no longer satisfies.\n"
            "\n"
            "It is invisible in `metrics.json`, which is not widened and reports "
            "`assignment_slack` as its own figure either way. Caught by "
            "`test_a_covered_but_unmatched_gold_span_is_not_reported_as_missed`, "
            "`test_a_jointly_covered_gold_span_is_not_missed_either` and "
            "`test_the_export_and_the_metrics_agree_on_the_counts_they_share`, which "
            "compares each half against the mode it is drawn from rather than against a "
            "stored count."
        ),
        min_kills=3,
    ),
    Mutation(
        name="both_halves_of_the_export_use_one_mode",
        path=SCORER,
        anchor="ERROR_MODE = {MISSED: HEADLINE_MODE[\"leak_rate\"],\n"
               "              FALSE_POSITIVE: HEADLINE_MODE[\"precision\"]}",
        replacement="ERROR_MODE = {MISSED: HEADLINE_MODE[\"leak_rate\"],\n"
                    "              FALSE_POSITIVE: HEADLINE_MODE[\"leak_rate\"]}",
        breaks=(
            "One mode for both halves, which is the tidier-looking constant: two matchings "
            "over one mode instead of two. It leaves `missed` correct, so the leak rate the "
            "stopping rule watches still moves and the arm still converges.\n"
            "\n"
            "What changes is the other half. Under `fully_covered` a prediction that covers "
            "most of a gold span is unmatched, so it is exported as a false positive while "
            "the *published* precision — `relaxed`, per `HEADLINE_MODE` — counts "
            "it as a hit. The author is then shown correct predictions as errors and asked "
            "to narrow rules that are already scoring, and the arm degrades the number it "
            "is optimising while every file involved stays internally consistent: the "
            "export agrees with `modes.fully_covered.overall.fp`, which is a real figure in "
            "the same `metrics.json`.\n"
            "\n"
            "This is why `ERROR_MODE` is derived from `HEADLINE_MODE` rather than written "
            "as two literals — the headline choice belongs to the reporting layer and "
            "may move, and the window has to follow it. Caught by "
            "`test_false_positives_come_from_the_relaxed_assignment` and "
            "`test_the_two_modes_are_derived_from_the_headline_and_not_written_down`."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_round_s_files_are_written_by_every_arm",
        path=RUN_FOLD,
        anchor="    if iteration is not None:\n"
               "        # The round's three files",
        replacement="    if True:\n"
                    "        # The round's three files",
        also=(
            (RUN_FOLD,
             "        write_spans(predictions, run, root=root, iteration=iteration)",
             "        write_spans(predictions, run, root=root, "
             "iteration=iteration or 1)"),
            (RUN_FOLD,
             "        write_errors(error_spans(pairs), run, iteration, root=root)",
             "        write_errors(error_spans(pairs), run, iteration or 1, root=root)"),
            (RUN_FOLD,
             "        write_metrics(scored, iteration=iteration, **metrics_args)",
             "        write_metrics(scored, iteration=iteration or 1, **metrics_args)"),
        ),
        breaks=(
            "Opt-in becomes always-on, in the silent form. The bare `if True:` would fail "
            "loudly on `iter{iteration}` formatted with `None`, so `or 1` goes in at all "
            "three writes: every arm on every corpus then produces an `iter1/` directory "
            "holding a copy of its score, a copy of its predictions, and "
            "`iter1/errors.jsonl` — a list of the positions of every missed identifier in "
            "the fold, as a permanent by-product of a feature only the iterating arms "
            "use.\n"
            "\n"
            "`port-oneshot-nofence`'s `metrics.json` and `spans.jsonl` are committed at "
            "four axes, so this puts a second copy of a published result beside them, and "
            "the `iter1` naming it is a false statement about an arm that has no rounds. "
            "This is the direction §5.5 explicitly decided against, and the decision is "
            "recorded on `run_fold` because both readings are defensible — a uniform tree "
            "is the argument for, and three separate costs are the argument against.\n"
            "\n"
            "The deny rule and the `.gitignore` entry hold either way, so the error list "
            "is not published — the cost is a file on disk that should not exist, which "
            "is `rule_author.md` §6's rule about rendered windows one artefact over, and "
            "the same argument §5.5 gives for not widening `score()`'s return: an "
            "iterating arm's input should not be every arm's output. The two allowed "
            "files are worse in the other direction: they *are* publishable, so a "
            "duplicate of a committed result reaches a commit with nothing objecting.\n"
            "\n"
            "Caught by `test_no_error_list_is_written_unless_the_arm_iterates` and "
            "`test_a_non_iterating_arm_writes_no_round_directory_at_all`, both of which "
            "walk the whole results tree rather than checking one path — an artefact "
            "written to a path a test did not name satisfies the narrower assertion."
        ),
        min_kills=2,
    ),
    Mutation(
        name="only_the_score_is_scoped_to_the_round",
        path=RUN_FOLD,
        anchor="        write_spans(predictions, run, root=root, iteration=iteration)\n"
               "        write_errors(error_spans(pairs), run, iteration, root=root)\n",
        replacement="        write_errors(error_spans(pairs), run, iteration, root=root)\n",
        breaks=(
            "**The round-scoped write of the predictions is dropped and the other two "
            "stay.** This is the design §5.5 corrected on 2026-08-12, restored: scope the "
            "score, leave `spans.jsonl` arm-wide. It reads as the smaller change and it "
            "loses more than scoping nothing would.\n"
            "\n"
            "Every round's score survives and every round's error list survives, so the "
            "record looks complete. But `iter{N}/errors.jsonl` is *derived* from round "
            "N's predictions against gold, and the predictions it was derived from are "
            "overwritten by round N+1 — so from round 2 onward the arm holds a list of "
            "missed identifiers that nothing in the repository can re-derive or check "
            "against the spans it came from. The one file that could contradict it is "
            "gone, and nothing about the remaining files looks wrong: each round's "
            "`metrics.json` is internally consistent, and the arm-wide `spans.jsonl` is a "
            "valid prediction file for *some* round.\n"
            "\n"
            "That is the shape §5.3 objected to for rule files, one artefact over: an "
            "overwritten *record* is visibly gone and an overwritten *premise* leaves a "
            "complete file behind whose input no longer exists. Caught by "
            "`test_the_round_s_three_files_land_together`, which checks all three names in "
            "the round's directory rather than the one this mutation keeps."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_final_rounds_duplicate_comes_from_a_second_scoring",
        path=RUN_FOLD,
        anchor="    spans_file = write_spans(predictions, run, root=root)\n"
               "    metrics_file = write_metrics(scored, **metrics_args)",
        replacement=(
            "    fresh = detect_fold(load_fold(corpus, split), ruleset, "
            "detector=detector)\n"
            "    again, again_excluded = from_documents(load_fold(corpus, split), fresh)\n"
            "    spans_file = write_spans(fresh, run, root=root)\n"
            "    metrics_file = write_metrics(\n"
            "        score(again, excluded_gold=again_excluded), **metrics_args)"
        ),
        breaks=(
            "**The un-iterated pair comes from a second detection and a second scoring "
            "instead of from the one this function already did.** On today's code the two "
            "passes agree — detection is deterministic and the rule file has not moved "
            "between them — so this mutation changes no byte of any output on any corpus. "
            "It is in the harness because *that* is the failure: the guarantee §5.5 rests "
            "on is not \"the two files happen to match\", it is \"there is one scoring "
            "pass, so they cannot differ\", and a second pass removes the guarantee while "
            "leaving the property.\n"
            "\n"
            "What it costs the day something does move: a rule file edited mid-run, a "
            "corpus re-exported, or a detector with any non-determinism in it (a "
            "tagger — the `RT` and `T` arms are on the ladder) and `metrics.json` and "
            "`iter{N}/metrics.json` disagree, with **neither file looking wrong**. Each "
            "is internally consistent with the pass that produced it; the run block, the "
            "cost block and the `termination` block are identical in both. Nothing in "
            "either file records which pass it came from, so there is no way to tell "
            "which number the arm's headline should be — and §5.5's whole argument for "
            "duplicating the final round is that two identical files are checkable while "
            "a headline that has to be computed is not.\n"
            "\n"
            "It also doubles the detection cost of every round, which `cost.wall_seconds` "
            "would report faithfully while `llm_calls` stayed put — a real regression "
            "that reads as the fold getting slower.\n"
            "\n"
            "**Caught by `test_the_fold_is_detected_once_and_scored_once`, and not by the "
            "byte-equality test beside it** — which is the point worth recording. "
            "`test_the_final_rounds_duplicate_is_byte_identical_to_the_round_copy` is what "
            "a reader can check on a finished run, and it passes under this mutation on "
            "every corpus here, because both passes are deterministic. So the guarantee "
            "had to be tested as what it is — one call — rather than as its currently "
            "observable consequence. Both tests stay: the byte comparison is the property "
            "§5.5 promises, and the call count is the mechanism that delivers it."
        ),
        min_kills=1,
    ),

    # ─── the Auditor's flag validator (auditor.md §2.3) ─────────────────────
    Mutation(
        name="an_unknown_flag_field_is_ignored_instead_of_refused",
        path=AUDIT,
        anchor="    if set(item) - FLAG_FIELDS or not {\"line\", \"start\", \"end\", "
               "\"phi_type\"} <= set(item):",
        replacement="    if not {\"line\", \"start\", \"end\", \"phi_type\"} <= set(item):",
        breaks=(
            "**`auditor.md` §3's prohibition stops being enforced, and every legitimate "
            "flag still validates.** The edit keeps the required-field check and drops the "
            "whitelist, which is the version a reviewer would call more permissive in the "
            "harmless direction: unknown keys are ignored rather than rejected, the way "
            "most JSON consumers work.\n"
            "\n"
            "What it ignores is the field the surface form arrives in. §3 removes every "
            "free-text field from a flag because any justification for a span is a "
            "description of that span's text and the shortest honest one is a quotation — "
            "so the natural thing for a model to add is `\"reason\": \"the name after "
            "'Dr.'\"`, with the name in it. Ignored means not written, today; the failure "
            "is that the *next* edit to the prompt or the report assembler can start "
            "carrying it and nothing objects. This is the whitelist rule "
            "`write_errors()` follows (DESIGN §5.5.1) in the place where 'the day it is "
            "added' means publishing a residual identifier from a DUA fold into a file "
            "under `results/` in a public repository.\n"
            "\n"
            "Caught by `test_an_unknown_field_is_refused_rather_than_ignored`, which is "
            "parametrised over the eight field names a helpful model would reach for."
        ),
        min_kills=1,
    ),
    Mutation(
        name="an_out_of_range_column_is_snapped_to_the_line",
        path=AUDIT,
        anchor="    if end > line.length:",
        replacement="    if False:  # end is clamped below instead\n"
                    "        pass\n"
                    "    end = min(end, line.length)\n"
                    "    if end <= start:",
        breaks=(
            "**Repair instead of refusal, in the form that looks like robustness.** A flag "
            "whose `end` runs past its line is clamped to the line rather than refused, so "
            "the report keeps a flag the agent's coordinates could not support — at a "
            "position the agent never claimed, with nothing in the file saying so. "
            "`counts.refused` goes down, which reads as the model having done better.\n"
            "\n"
            "`auditor.md` §2.3 is explicit that the validator refuses rather than repairs, "
            "and this mutation is why the sentence is in the prompt rather than only in the "
            "code: a clamped flag is indistinguishable in the report from a correct one, "
            "and the RuleAuthor is then shown a marked sample span (§4) whose boundary is "
            "this function's arithmetic rather than the Auditor's claim.\n"
            "\n"
            "It also destroys the diagnostic. A round where the model lost the coordinate "
            "scheme should show up as a refusal count; clamped, it shows up as ordinary "
            "flags at line ends. Caught by "
            "`test_a_span_past_the_end_of_its_line_crosses_a_line` and "
            "`test_nothing_is_repaired`, the second of which exists because each "
            "individual refusal test is also consistent with a validator that repaired "
            "some other case."
        ),
        min_kills=2,
    ),
    Mutation(
        name="a_flag_overlapping_a_mask_tag_is_kept_when_it_is_not_contained",
        path=AUDIT,
        anchor="        if start < col + length and col < end:",
        replacement="        if col <= start and end <= col + length:",
        breaks=(
            "**Overlap becomes containment, so a flag half over a mask tag survives and "
            "gets translated.** The edit reads as a tightening of the same idea — it still "
            "refuses flags *inside* a tag, which is what the reason is named for — and it "
            "passes any test that only flags a whole tag.\n"
            "\n"
            "A flag partly over a tag has no boundary the corpus can resolve: the part "
            "inside the tag corresponds to a span that was replaced, so `_to_document()` "
            "translates a column that has no document counterpart. Worse, the flag that "
            "survives is *plausible* — it points at real text adjacent to a detected span, "
            "which is exactly where a missed identifier often sits, so the wrong offsets "
            "land in the report looking like the report's most credible entries.\n"
            "\n"
            "Caught by `test_any_overlap_with_a_tag_is_refused_not_only_containment`, "
            "parametrised over the four ways a span can overlap a tag without being "
            "contained by it, and by `test_a_flag_touching_a_tag_boundary_is_not_refused` "
            "— the complement, which is what stops the fix from being 'refuse anything "
            "near a tag' and losing the common case."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_report_reads_its_own_round_as_the_masked_one",
        path=AUDIT,
        anchor="    if masked_from_iteration != iteration - 1:",
        replacement="    if masked_from_iteration not in (iteration, iteration - 1):",
        breaks=(
            "**The report may claim it audited the round it belongs to.** `iter4/"
            "audit_report.json` with `masked_from_iteration: 4` says the arm audited round "
            "4's predictions — which do not exist when the Auditor runs, since the Auditor "
            "is round 4's *first* step (`auditor.md` banner). The record then attributes a "
            "round's flags to the wrong `spans.jsonl`.\n"
            "\n"
            "Every downstream number stays consistent, which is the whole difficulty: flag "
            "counts, per-type counts and the marked sample all come out the same, because "
            "the field is a *label* on the derivation rather than an input to it. The file "
            "is well-formed and its own arithmetic agrees with itself. What breaks is the "
            "one question the field exists to answer — 'which predictions was this "
            "computed from' — and it breaks in the direction that makes an arm look like "
            "it audited fresher output than it did.\n"
            "\n"
            "The permissive form is what a caller would reach for while wiring the loop "
            "driver, at the moment they are unsure which round number they hold. Caught by "
            "`test_masked_from_iteration_must_be_the_previous_round`."
        ),
        min_kills=1,
    ),
    Mutation(
        name="tags_out_of_order_are_sorted_instead_of_refused",
        path=AUDIT,
        anchor="        _check_tags(self.length, self.doc_offset, self.tags)",
        replacement="        object.__setattr__(self, 'tags', tuple(sorted(self.tags)))\n"
                    "        _check_tags(self.length, self.doc_offset, self.tags)",
        breaks=(
            "**The masker's likeliest bug is silently repaired, and the contract stops being "
            "a contract.** `_to_document()` walks the tags once, left to right, and stops at "
            "the first tag ending after the column it is translating. That is correct for "
            "ascending non-overlapping tags and *silently wrong* for any other order: "
            "measured before the check existed, the same two tags reversed translated column "
            "5 to document 5 instead of 12, raising nothing.\n"
            "\n"
            "Sorting looks strictly better than refusing, and it is the wrong fix because of "
            "the direction the defect travels. **The masker applies replacements "
            "right-to-left** (DESIGN §3), so descending is its natural emission order — "
            "exactly the one that breaks the walk. A sort makes that emission produce correct "
            "offsets on every call, so the masker ships with the defect permanently hidden: "
            "it can emit in any order forever, and no test, no run and no report says so. "
            "The first time it matters is the first time something *else* consumes that map "
            "and does not sort.\n"
            "\n"
            "This is the same division `AuditError` already draws — a refused flag is the "
            "agent's mistake and is data, a broken mask map is the harness's and is an "
            "exception — applied to the one precondition that has no symptom. Caught by "
            "`test_tags_out_of_order_are_refused_and_not_sorted`, and by "
            "`test_the_refusal_says_why_it_is_not_a_sort`, which pins the argument into the "
            "message because `sorted()` is the repair a reader reaches for."
        ),
        min_kills=2,
    ),
    Mutation(
        name="overlapping_mask_tags_are_accepted",
        path=AUDIT,
        anchor="        if col < previous_end:",
        replacement="        if col < previous_end and False:",
        breaks=(
            "**Ascending order is still checked; the non-overlap half is not.** Two tags may "
            "then share columns, and `_to_document()` double-counts the shared ones — every "
            "column after the overlap translates too far by exactly the overlap's width. A "
            "number, not a failure, and the flags carrying it look like every other flag.\n"
            "\n"
            "The input that produces overlapping tags is a specific and likely masker bug: "
            "emitting one tag per overlapping *span* instead of one per union of extents, "
            "which is the rule DESIGN §3 fixes precisely because `RuleSet.detect` preserves "
            "overlaps by design (§4, §9.3). So this check is what stands between that "
            "mistake and a report of wrong offsets — and the mistake is in the component "
            "that has not been written yet, which is why the check exists before it.\n"
            "\n"
            "Caught by `test_overlapping_tags_are_refused`, parametrised over the three ways "
            "two tags can share a column, and guarded from the other side by "
            "`test_adjacent_tags_are_not_refused` — adjacency is the common case (393 gold "
            "pairs within one character on es-meddocan dev), so a check that refused "
            "touching tags would refuse ordinary documents."
        ),
        min_kills=3,
    ),
    Mutation(
        name="drift_is_checked_against_todays_window_not_the_recorded_one",
        path=ORCHESTRATE,
        anchor="    return [field for field in recorded_window_fields(frozen)",
        replacement="    return [field for field in now",
        breaks=(
            "**A widening of the window reaches backwards, and it reports the frozen arms as "
            "wrong.** This is the pre-2026-08-12 line, restored: the compared fields come from "
            "today's `window_hashes()` instead of from the record. Every arm frozen under the "
            "two-file window is then compared against `auditor_sha256`, a file its call never "
            "saw, and `window_drift()` reports permanent drift on `port-oneshot` and "
            "`port-oneshot-nofence`.\n"
            "\n"
            "The damage is not the false alarm, it is what the false alarm means. "
            "`window_drift()` is documented as saying that the record and the files disagree "
            "about a call that has already happened; a reader who trusts that reads it as the "
            "record being wrong. It is not wrong — it is the only thing in the repository "
            "that still says what those calls were held to (DESIGN §6.3, "
            "`docs/notes/window-freeze-history.md`). The repair a reader would reach for is "
            "re-freezing, which hashes today's files onto a record about last week's call.\n"
            "\n"
            "Caught by `tests/test_window_widening.py`, against the committed records rather "
            "than fixtures: a record built under a redirected root cannot fail to be "
            "retroactively rewritten, so a fixture-based test of this would pass either way."
        ),
        min_kills=2,
    ),
    Mutation(
        name="the_recorded_files_list_is_ignored_in_favour_of_the_fields_present",
        path=SAMPLE,
        anchor="    files = record.get(\"files\")",
        replacement="    files = None",
        breaks=(
            "**The record's own claim about its window stops being read, and the fallback "
            "answers from whatever keys happen to be there.** Two hash fields on a record "
            "naming two files is the same answer either way, which is why this mutation "
            "survived the whole suite when it was first written — the committed records cannot "
            "distinguish the branches.\n"
            "\n"
            "They differ on the record a widened writer produces against an old window: two "
            "files named, three hashes written. The `files` list is what the record *claims* "
            "and a stray field is not a claim, so the strict branch compares two fields and "
            "the fallback compares three — reporting drift on `auditor_sha256` for a call that "
            "never saw it. That record does not exist yet, and the point of the branch is that "
            "it never has to.\n"
            "\n"
            "Caught by `test_the_files_list_outranks_the_hash_fields_present`, which is the "
            "synthetic record the committed ones are not. The survival is recorded in this "
            "file: a surviving mutation is usually a missing test, and that one was a wrong "
            "belief about which line held the guarantee."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_audit_report_gets_a_second_path_key",
        path=NAMING,
        anchor=(
            '  auditreport: "results/{corpus}/{detector}/{supervision}/{porting}'
            '/iter{iteration}/audit_report.json"'
        ),
        replacement=(
            '  auditreport: "results/{corpus}/{detector}/{supervision}/{porting}'
            '/iter{iteration}/audit_report.json"\n'
            '  leakreport: "results/{corpus}/{detector}/{supervision}/{porting}'
            '/iter{iteration}/audit_report.json"'
        ),
        breaks=(
            "**The near-miss this repository actually had, on 2026-08-13, as a mutation.** "
            "The loop's implementation order called for a `paths.leakreport` — DESIGN §5.5's "
            "two bullets about `reports/leaks_{iter}.json` name a path that must be denied "
            "and axis-scoped — and `paths.auditreport` from `c998610` is already that file. "
            "So this is the state the repository was one commit from being in, and the "
            "reason the test exists is that nothing else here notices it.\n"
            "\n"
            "**Every check that could plausibly catch it passes.** The new key formats to a "
            "real path under a real arm at a real round. `deny()` denies it — the pattern is "
            "anchored on the filename. `.gitignore` ignores it, for the same reason. "
            "`test_the_four_iteration_scoped_paths_split_two_and_two` names its four keys "
            "explicitly and does not iterate the block, so a fifth is invisible to it; the "
            "deny-sample table is keyed on patterns rather than on keys and is unchanged; "
            "`path_template('leakreport')` resolves. The screener reports nothing, because "
            "there is nothing wrong with the path.\n"
            "\n"
            "What breaks is one layer up and only under a second writer: DESIGN §3's \"two "
            "agents never write the same file\" is checkable only while each artefact has one "
            "name. With two, a driver holding `leakreport` and a validator holding "
            "`auditreport` agree on every byte until one of them moves, and no record says "
            "which name produced the file. That is worse than §5.3's axis-free path, where "
            "the two arms at least collide visibly on one path.\n"
            "\n"
            "Caught by `test_no_two_path_keys_name_one_file`, which compares formatted "
            "results over the whole `paths` block rather than the four keys of the split "
            "test — the difference being that a fifth key is exactly what the enumerated "
            "test cannot see."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_call_role_is_written_without_being_validated",
        path=ORCHESTRATE,
        anchor='        "role": check_agent_role(role),',
        replacement='        "role": role,',
        breaks=(
            "**One agent's calls split across two spellings, in the file every per-role cost "
            "figure is computed from.** The field is written, the log is well-formed JSONL, "
            "and `\"RuleAuthor\"` beside `\"rule_author\"` totals as two agents — which is the "
            "defect `check_agent_role()` exists for and `tests/test_agent_role.py` describes "
            "as having no symptom in the file.\n"
            "\n"
            "What makes it worth a mutation rather than only a unit test is where the wrong "
            "value would come from. `port-loop`'s driver passes the Auditor's role at one "
            "call site and the RuleAuthor's at another, in a module that also handles "
            "`porting` and prompt template names — all strings, none of them interchangeable. "
            "An unvalidated field there is a field the vocabulary rule in CLAUDE.md does not "
            "reach.\n"
            "\n"
            "Caught by `test_a_role_outside_the_vocabulary_is_refused_at_write_time` in "
            "`tests/test_call_role.py`, which is deliberately *not* a test of the "
            "near-spellings — those are `test_agent_role.py`'s, on `check_agent_role()` "
            "itself. What this one asserts is that `call_line()` is on the validated path, "
            "which is the half of the guarantee a test of the validator cannot see."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_role_is_appended_at_the_end_of_the_line",
        path=ORCHESTRATE,
        anchor='        "role": check_agent_role(role),\n        "outcome": outcome,',
        replacement='        "outcome": outcome,',
        also=((
            ORCHESTRATE,
            "        \"generated\": _now(),\n        **window_hashes(),",
            "        \"generated\": _now(),\n"
            "        \"role\": check_agent_role(role),\n"
            "        **window_hashes(),",
        ),),
        breaks=(
            "**The same fields in a different order, which is the version that looks like a "
            "tidier diff.** Every value is present and validated; `role` simply sits after "
            "`generated` instead of beside `iteration`. Nothing about one line changes.\n"
            "\n"
            "What changes is the log as a document. `(iteration, role)` is what a per-round "
            "per-role cost total groups by, and those two keys adjacent at the head of the "
            "line are what makes `port-loop`'s log readable by eye — six rounds, two agents, "
            "twelve lines, and the grouping visible in the first twenty characters of each. "
            "Buried after a timestamp and before three hashes it is not. This is the same "
            "argument `human_arm.FIELDS` makes about its own tail, and the reason it is a "
            "mutation is that field order is the one property a reviewer reads past.\n"
            "\n"
            "Caught by `test_the_role_sits_beside_the_iteration`. It is the only test in the "
            "suite that could catch it, which is the point: the ordering is not implied by "
            "anything else that is checked."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_audit_report_is_read_as_the_previous_rounds_file",
        path=PROMPT,
        anchor="    if stated != iteration:",
        replacement="    if stated != iteration - 1:",
        breaks=(
            "**The second near-miss of 2026-08-13, and this one was in the file rather than "
            "one commit away.** `_audit_block()` shipped with exactly this check. It reads "
            "`auditor.md`'s handover backwards: the Auditor runs as round *n*'s first step, "
            "so its report is written under round *n* with `iteration: n` and records "
            "`masked_from_iteration: n−1`. A reader demanding `iteration == n−1` refuses the "
            "correct report and accepts the round-old one.\n"
            "\n"
            "**Both directions are wrong and neither is loud.** The refusal is a `PromptError` "
            "with a message that reads plausibly, so a driver written against it would be "
            "written to satisfy it — by passing the previous round's report, which is the "
            "artefact the check was supposed to reject. The prompt then carries round *n−2*'s "
            "flags under a heading naming *n−1*, and the agent reads them against a rule file "
            "two revisions newer: every flag it has already fixed reappears, and DESIGN §3's "
            "shrinking-report mechanism (a residual fixed at round n is *masked* at round n+1 "
            "and cannot be flagged again) is silently switched off.\n"
            "\n"
            "Nothing downstream notices. `audit.report()` validates the pair against each "
            "other, not against the round reading it, so both files are internally consistent; "
            "the flags are well-formed, in range, and in the right corpus; the reference form "
            "records the number it was given. The count in `metrics.json` is right about a "
            "report that answers the wrong question.\n"
            "\n"
            "Caught by `test_the_report_must_be_this_rounds_and_must_audit_the_previous_one`, "
            "which asserts the accepted case as well as the three refused ones — a test of "
            "refusals alone passes on a reader that refuses everything."
        ),
        min_kills=1,
    ),
    Mutation(
        name="only_the_round_the_report_names_is_checked",
        path=PROMPT,
        anchor=(
            "    masked_from = report.get(\"masked_from_iteration\")\n"
            "    if masked_from != iteration - 1:"
        ),
        replacement=(
            "    masked_from = report.get(\"masked_from_iteration\")\n"
            "    if False:"
        ),
        breaks=(
            "**One number checked where the file carries two, which is the state that looks "
            "sufficient.** `iteration` is verified against this call and `masked_from_iteration` "
            "is trusted — and trusting it is the whole error, because it is the field a "
            "consistent-but-off-by-one driver gets wrong. `audit.report()` writes whatever "
            "relationship it was told to write and validates only that the pair agrees; a "
            "driver that called the Auditor on round *n−2*'s spans while labelling the round "
            "correctly produces a file this reader accepts.\n"
            "\n"
            "The visible consequence is the heading, which is rendered from `masked_from`: the "
            "prompt would tell the agent the flags describe a round they do not. That is the "
            "reason both numbers are read here rather than one — the check and the sentence "
            "the agent reads come from the same field.\n"
            "\n"
            "Caught by the same test's third and fourth cases, which pass a report whose two "
            "numbers `report()` would never have written together."
        ),
        min_kills=1,
    ),
    Mutation(
        name="round_one_reassembles_the_baselines_prompt",
        path=PROMPT,
        anchor="        return assemble_task_prompt(lang=lang, corpus=corpus, "
               "rules_path=rules_path)",
        replacement=(
            "        frame = _task_frame(lang, corpus)\n"
            "        rules_block, rules_ref = _current_rules(lang, rules_path)\n"
            "        empty = \"\\n\".join([\n"
            "            f\"### {section} — EMPTY for this call\"\n"
            "            for section in EMPTY_SECTIONS\n"
            "        ])\n"
            "        text = \"\\n\\n\".join([\n"
            "            _template(),\n"
            "            INPUT_BANNER,\n"
            "            frame,\n"
            "            rules_block,\n"
            "            empty,\n"
            "            \"There is no previous iteration, so there are no scores and no \"\n"
            "            \"error spans: \"\n"
            "            f\"§{' and §'.join(EMPTY_SECTIONS)} of the template above are empty \"\n"
            "            \"for this call rather than withheld. This is the arm's definition \"\n"
            "            \"and not a gap in the harness (DESIGN §4). Do not ask for them and \"\n"
            "            \"do not substitute anything for them — a profile summary or a type \"\n"
            "            \"inventory standing in for the score block would make this call \"\n"
            "            \"something other than the no-feedback baseline it is.\",\n"
            "            f\"Emit the complete rules/{lang}.yaml and nothing else.\",\n"
            "        ])\n"
            "        return FilledPrompt(text, {\n"
            "            \"block\": \"task_frame\",\n"
            "            \"lang\": lang,\n"
            "            \"corpus\": corpus,\n"
            "            \"sections_filled\": list(FILLED_SECTIONS),\n"
            "            \"sections_empty\": list(EMPTY_SECTIONS),\n"
            "            **rules_ref,\n"
            "            \"text_chars\": len(text),\n"
            "            \"text_sha256\": _digest(text),\n"
            "            \"window_files\": {name: file_hash(name) for name in WINDOW_FILES},\n"
            "        })"
        ),
        breaks=(
            "**A copy that agrees byte for byte on the day it is written.** This is the whole "
            "of `assemble_task_prompt()`'s body inlined into round 1's branch — every "
            "sentence, both constants, the same reference form. `port-loop` round 1 and "
            "`port-oneshot` produce identical prompts and identical hashes, and the equality "
            "test passes.\n"
            "\n"
            "It breaks on the next edit to anything §§1.1–1.2 are made of, which is the point: "
            "DESIGN §4's claim is that the two rungs are shown *the same thing*, and that "
            "claim can rest on one code path or on two implementations someone remembers to "
            "keep equal. Under this mutation a widened `_task_frame()` reaches the baseline "
            "and not round 1 of the loop, the two arms diverge in an unrecorded way, and the "
            "measured difference between the rungs stops being feedback.\n"
            "\n"
            "Caught by `test_round_one_delegates_rather_than_reassembling`, which is "
            "structural for exactly this reason — the behavioural test it sits next to cannot "
            "see the difference, today."
        ),
        min_kills=1,
    ),
    Mutation(
        name="round_one_ignores_the_feedback_it_was_handed",
        path=PROMPT,
        anchor=") if value is not None]",
        replacement=") if value]",
        breaks=(
            "**Truthiness for presence, which is the same line with two characters removed.** "
            "`if value` instead of `if value is not None` reads as idiomatic Python and is "
            "wrong for exactly the values a round-1 call plausibly carries: `{}` from a "
            "metrics block that was read but empty, `[]` from an error pool that came back "
            "with nothing, `0` from a context width. All falsy, all dropped, and the prompt "
            "that comes out is a correct round-1 prompt.\n"
            "\n"
            "That is the asymmetry the refusal exists for. There is no round 0 to score or "
            "draw from, so a caller holding this data computed it somewhere else — which means "
            "its iteration counter is off by one for every round that follows, and round 1 is "
            "the only round where the discrepancy is visible at all. Absorbing the argument "
            "leaves the prompt right and the driver wrong, with nothing in the run record "
            "saying so; refusing it turns an arm-wide defect into a stack trace on call 1.\n"
            "\n"
            "The same two characters are the whole of the mirror check one branch down, where "
            "the direction reverses: an empty error pool at round 3 is a *supplied* block and "
            "must not be reported as missing.\n"
            "\n"
            "Caught by `test_round_one_refuses_feedback_rather_than_ignoring_it`, parametrised "
            "over five falsy values for this reason — the same test written with a populated "
            "metrics dict passes under the mutation."
        ),
        min_kills=1,
    ),
    Mutation(
        name="an_undefined_rate_prints_as_zero",
        path=PROMPT,
        anchor="    if value is None:\n        return \"  n/a\"",
        replacement="    if value is None:\n        return f\"{0.0:.3f}\"",
        breaks=(
            "**`None` rendered as `0.000`, which reads as measured-and-clean.** The scorer "
            "writes `None` for a rate whose denominator is zero (`_prf`, `_mean`) and it means "
            "*undefined*: a type with false positives and no gold in this fold, or a macro "
            "average over no scored type. Printed as a number it becomes the best possible "
            "score, in the column the agent scans for what to work on.\n"
            "\n"
            "The direction is what makes it worth a mutation. An agent shown `leak_rate 0.000` "
            "for a type the fold cannot score concludes the type is handled — and the types "
            "this happens to are the sparse ones DESIGN §9.4 already says not to over-fit. "
            "The opposite error (a real 0.000 printed as `n/a`) would waste a round; this one "
            "hides a hole and looks like progress.\n"
            "\n"
            "Caught by `test_an_undefined_rate_reads_as_not_available_and_not_as_zero`, which "
            "asserts on the field's position in the row rather than on `n/a` appearing "
            "somewhere in the line: the fixture's type has a measured 0.000 precision beside "
            "an undefined leak rate, so a looser assertion would pass with the two swapped."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_score_block_carries_the_run_and_cost_blocks_too",
        path=PROMPT,
        anchor="    block_text = \"\\n\".join(lines)\n    first = modes[sorted(modes)[0]]",
        replacement=(
            "    for extra in (\"run\", \"cost\"):\n"
            "        if metrics.get(extra):\n"
            "            lines += [f\"{extra}:\", \"\"]\n"
            "            for key, value in sorted(metrics[extra].items()):\n"
            "                lines.append(f\"  {key} {value}\")\n"
            "            lines.append(\"\")\n"
            "    block_text = \"\\n\".join(lines)\n"
            "    first = modes[sorted(modes)[0]]"
        ),
        breaks=(
            "**The reduction turned back into a forward, which is the shape a reviewer asks "
            "for.** `metrics.json` has the blocks; the block builder already walks it; adding "
            "the last two is four lines and reads as completeness. Nothing malfunctions — the "
            "prompt assembles, the hash changes, the agent gets strictly more information.\n"
            "\n"
            "What arrives with it is `model_id`, `commit`, `wall_seconds` and the token counts. "
            "Two distinct problems. The run block is a set of facts about the harness that the "
            "agent must not act on, and an agent that can see `commit` can reason about being "
            "one of several runs. The cost block is worse: an agent shown its own token spend "
            "can reason about the budget, and `rule_author.md` §5 puts budget outside its "
            "decisions precisely because the cheap move available to it — emit fewer rules — "
            "improves the number it can see and damages the one being measured. CLAUDE.md "
            "requires cost to be reported *beside* quality, to a reader, not to the agent "
            "generating both.\n"
            "\n"
            "The prompt space is the second cost. §4 allocates it to §1.4, and a run block is "
            "paid for at every round of every arm.\n"
            "\n"
            "Caught by `test_the_run_and_cost_blocks_are_not_forwarded`, which injects both "
            "blocks into the fixture — the committed `metrics.json` has them, but a test built "
            "on the scorer's return alone would not, and that is the version of this test that "
            "would have passed under the mutation."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_heterogeneous_union_prints_one_of_its_types",
        path=PROMPT,
        anchor=(
            "    tag = (TAG_FORM.format(phi_type=next(iter(phi_types))) if len(phi_types) == 1\n"
            "           else masked_tag_heterogeneous())"
        ),
        replacement="    tag = TAG_FORM.format(phi_type=sorted(phi_types)[0])",
        breaks=(
            "**The masker acquires a merge policy, and it is a well-formed one.** A union "
            "whose spans disagreed now prints the alphabetically first type instead of the "
            "tag that names none. Every downstream property still holds: the tag is "
            "bracketed, the geometry is unchanged, `_check_tags` passes, the Auditor reads "
            "it as a tag and does not flag it (`auditor.md` §1.2 — a tag is not a "
            "candidate), and no offset moves. The masked text is *correct-looking* and one "
            "component has silently started resolving overlaps.\n"
            "\n"
            "That is the failure DESIGN §3 was written before the masker existed to "
            "prevent. `RuleSet.detect` preserves overlapping matches precisely so that merge "
            "policy stays a replaceable strategy comparable on identical detections (§4, "
            "§9.3); a policy baked in here runs inside every `port-loop` arm regardless of "
            "the policy that arm was configured with, and the arm's record would not say so. "
            "`sorted(...)[0]` is the shape the accident actually takes — not a considered "
            "precedence order, just whichever type the implementation happened to reach "
            "first.\n"
            "\n"
            "Caught by the tests that assert the heterogeneous tag prints and that it is "
            "read from the config, and by the two count tests: `n_heterogeneous_tags` is "
            "computed from `phi_types` and stays right, so what changes is the *text* while "
            "the number that reports it does not — which is why both are asserted."
        ),
        min_kills=3,
    ),
    Mutation(
        name="the_mask_tags_are_emitted_in_the_order_they_were_applied",
        path=PROMPT,
        anchor=(
            "    pieces = [(piece, len(masked) - piece.from_right - piece.length)\n"
            "              for piece in reversed(walk)]"
        ),
        replacement=(
            "    pieces = [(piece, len(masked) - piece.from_right - piece.length)\n"
            "              for piece in walk]"
        ),
        breaks=(
            "**The one reversal is dropped, so the tags come out descending by column.** "
            "The masker applies replacements right-to-left (DESIGN §3), so its natural "
            "emission order is the reverse of the order `audit._check_tags` requires — the "
            "point that check's docstring makes about why it refuses instead of sorting, "
            "written before this module existed. This is that mistake.\n"
            "\n"
            "Every offset in the map is still correct; only the order is wrong. On a line "
            "with one tag or none the output is byte-identical, which is most lines and "
            "almost every small fixture — so this survives any test whose document has one "
            "tag per line. Where two tags share a line, `MaskedLine.__post_init__` raises "
            "`AuditError` and the masker fails loudly, which is the intended outcome: a "
            "caller bug goes back to the caller rather than being repaired into a "
            "double-counted column.\n"
            "\n"
            "Caught by the ordering test and by every round-trip over a two-tag line, and "
            "guarded from the other side by "
            "`test_the_masker_emits_more_than_one_tag_per_line_so_the_order_is_testable` — "
            "without a fixture known to put two tags on one line, all of those pass on the "
            "mutant and the check would be measuring nothing."
        ),
        min_kills=3,
    ),
    Mutation(
        name="the_history_is_pre_seeded_with_this_rounds_rate",
        path=LOOP,
        anchor=(
            "        termination=PendingTermination(corpus=corpus,\n"
            "                                       previous_leak_rates=tuple(previous_rates)),"
        ),
        replacement=(
            "        termination=PendingTermination(\n"
            "            corpus=corpus,\n"
            "            previous_leak_rates=(*previous_rates, previous_rates[-1]),\n"
            "        ),"
        ),
        breaks=(
            "**The history is extended to cover the round being scored, using the last known "
            "rate as the placeholder.** The name reads as rounds 1..N and the round is N, so "
            "handing over a sequence one short looks like the bug rather than the design. What "
            "the pending type is *for* is that the round's own rate does not exist yet, and the "
            "only value available to stand in for it is the previous round's.\n"
            "\n"
            "So `resolve()` appends the measured rate to a history that already has an entry for "
            "this round, and every consequence is quiet. `improvements` gains a spurious `0.0` — "
            "an iteration that changed nothing — which is below δ by definition and therefore "
            "*counts toward stopping*: at k = 2 one genuinely thin round beside the phantom is "
            "enough, so the arm converges a round early and the block says `converged` with a "
            "plausible `improvements` list. `iterations` reads one too high, which is the same "
            "defect in the field a reader would use to check the first. Nothing in the file "
            "contradicts anything else in it, and the leak rates themselves are all real "
            "measurements.\n"
            "\n"
            "Caught by `test_the_history_the_driver_passes_excludes_this_round`, which captures "
            "the argument at the boundary rather than asserting on the record — what the driver "
            "hands over is the thing that must not already contain the answer — and by "
            "`test_the_rounds_termination_block_is_about_that_round` and "
            "`test_the_rate_in_the_block_is_the_rate_in_the_same_files_headline`, which see the "
            "count and the arithmetic from the published side."
        ),
        min_kills=3,
    ),
    Mutation(
        name="the_writer_calls_the_stopping_rule_itself",
        path=RUN_FOLD,
        anchor="from ..termination import PendingTermination, Termination, not_applicable",
        replacement=(
            "from ..termination import (PendingTermination, Termination, not_applicable,\n"
            "                          should_stop)"
        ),
        also=((
            RUN_FOLD,
            "    if isinstance(termination, PendingTermination):\n"
            "        termination = termination.resolve("
            "scored[\"headline\"][\"leak_rate\"][\"value\"])",
            "    if isinstance(termination, PendingTermination):\n"
            "        termination = should_stop(\n"
            "            corpus,\n"
            "            (*termination.previous_leak_rates,\n"
            "             scored[\"headline\"][\"leak_rate\"][\"value\"]),\n"
            "        )",
        ),),
        breaks=(
            "**The only mutation in this file that changes no byte of any output.** The verdict "
            "is identical, because `resolve()` is one line and that line is this one: "
            "`should_stop(self.corpus, (*self.previous_leak_rates, leak_rate))`. Every "
            "`termination` block, every `converged` flag, every refusal of a round after a stop "
            "comes out the same. It also reads as a simplification — one indirection removed, "
            "and the call site now says plainly what it does.\n"
            "\n"
            "What it costs is that §3's pre-registered decision acquires a second home, in the "
            "module that writes the file the decision is published in. The reason to refuse that "
            "is not today's behaviour but the next edit: a writer holding `should_stop` can grow "
            "a branch — a mode check, a floor on `n_dev`, a special case for the first round — "
            "and the arm's record would carry a verdict no reader of `src/termination.py` can "
            "reproduce. `PendingTermination` exists so that the one quantity crossing the "
            "boundary is a float and the rule stays where it was registered.\n"
            "\n"
            "Caught only by `test_the_writer_never_imports_the_stopping_rule`, and it is "
            "structural for exactly that reason: no behavioural test can see this, today or "
            "under any fixture. It reads the import list from the syntax tree rather than the "
            "text, because that module's docstrings name `should_stop` on purpose — a substring "
            "search would forbid explaining the boundary in order to enforce it."
        ),
        min_kills=1,
    ),
    Mutation(
        name="converged_is_stored_beside_the_reason",
        path=TERMINATION,
        anchor=(
            "    @property\n"
            "    def converged(self) -> bool:\n"
            "        \"\"\"True only for `converged`. DESIGN §3's prohibition, as one line of code."
        ),
        replacement=(
            "    #: Whether the arm converged, set from `reason` by whoever builds the record.\n"
            "    converged: bool\n"
            "\n"
            "    def _converged_note(self) -> str:\n"
            "        \"\"\"True only for `converged`. DESIGN §3's prohibition, as one line of code."
        ),
        also=(
            (
                TERMINATION,
                "        \"\"\"\n        return self.reason == CONVERGED\n",
                "        \"\"\"\n        return \"\"\n",
            ),
            (
                TERMINATION,
                "        n_dev=size,\n        improvements=tuple(gains),",
                "        n_dev=size,\n        converged=reason == CONVERGED,\n"
                "        improvements=tuple(gains),",
            ),
            (
                TERMINATION,
                "        n_dev=size,\n        improvements=(),",
                "        n_dev=size,\n        converged=False,\n        improvements=(),",
            ),
        ),
        breaks=(
            "**The derived flag becomes a stored field, and every value in every published file "
            "stays correct.** `should_stop` sets it from `reason` at construction, "
            "`not_applicable` sets it false, `record()` copies it out, and "
            "`scorer.check_termination`'s cross-check finds the two agreeing. There is no fixture "
            "and no corpus on which the two disagree, because the one producer computes them "
            "together. It is also the shape a reader expects: a dataclass field beside the other "
            "nine, instead of a property that has to be explained.\n"
            "\n"
            "What it removes is the *mechanism* rather than a value. §3 forbids describing a "
            "ceiling-terminated run as converged, and the way that prohibition is kept is that "
            "the contradictory state cannot be constructed — not that a validator rejects it, "
            "which would imply the state exists and is caught. With a field it exists: "
            "`Termination(reason=CEILING, converged=True, ...)` is a legal instance, and the "
            "caller who reaches it is the one the prohibition is about, since `write_metrics` "
            "takes a mapping and a hand-assembled block is the path around the dataclass in the "
            "first place.\n"
            "\n"
            "Frozen is what hides it. `verdict.converged = True` raises whether the attribute is "
            "a property or a field, so `test_converged_cannot_be_set` — which was written as the "
            "mechanism's test — passes on the mutant, and so does every assertion on the value. "
            "Caught by `test_converged_is_not_a_field_and_no_caller_can_supply_one`, which "
            "asserts the shape three ways: not in `dataclasses.fields`, a `property` on the "
            "class, and refused as a constructor argument."
        ),
        min_kills=1,
    ),
    Mutation(
        name="a_later_round_audits_and_samples_round_one",
        path=LOOP,
        anchor=(
            "    predictions = read_spans(corpus=corpus, detector=detector, "
            "supervision=supervision,\n"
            "                             porting=porting, iteration=previous, "
            "root=orchestrate.ROOT)"
        ),
        replacement=(
            "    predictions = read_spans(corpus=corpus, detector=detector, "
            "supervision=supervision,\n"
            "                             porting=porting, iteration=ITERATION, "
            "root=orchestrate.ROOT)"
        ),
        also=((
            LOOP,
            "        read_errors(corpus=corpus, detector=detector, supervision=supervision,\n"
            "                    porting=porting, iteration=previous, root=orchestrate.ROOT),",
            "        read_errors(corpus=corpus, detector=detector, supervision=supervision,\n"
            "                    porting=porting, iteration=ITERATION, root=orchestrate.ROOT),",
        ),),
        breaks=(
            "**§1.3's predictions and §1.4's error pool come from round 1 instead of round "
            "*N−1*, and at round 2 that is the same round.** The named constant is what makes it "
            "plausible: `ITERATION` is round 1 and it is right there in the module, used by "
            "`run_iteration_1` five times, so reaching for it reads as using the arm's own "
            "vocabulary rather than hard-coding a number. Round 2 is then correct by accident, "
            "which is the round every early test of a loop exercises.\n"
            "\n"
            "From round 3 on the arm is a different experiment and nothing says so. The Auditor "
            "is shown round 1's predictions masked — so the residual PHI it reports is residual "
            "with respect to rules two rounds out of date, and `masked_from_iteration` still says "
            "*N−1* because `audit.report()` records what the driver told it. The §1.4 sample is "
            "drawn over round 1's `errors.jsonl`, so the agent is asked to fix errors its own "
            "later rules may already have caught, at the seed that names round *N*. Every file "
            "is well-formed, every count is internally consistent, and the arm's improvement "
            "curve is real — it is just no longer feedback on the previous round, which is the "
            "one thing `port-loop` measures. The comment on `previous = iteration - 1` names "
            "this failure and the mutation is that comment coming true.\n"
            "\n"
            "Caught by "
            "`test_a_later_round_masks_the_previous_rounds_spans_and_not_round_ones` and "
            "`test_the_sample_is_drawn_over_the_previous_rounds_errors`, both of which run three "
            "rounds and make the rule files differ in what they catch. Written any other way "
            "they pass on the mutant: two rounds cannot distinguish *N−1* from 1, and identical "
            "rule files leave byte-identical predictions and error lists whichever round is "
            "read."
        ),
        min_kills=2,
    ),
    Mutation(
        name="a_round_with_no_score_walks_back_to_the_last_one",
        path=LOOP,
        anchor="    if not path.exists():\n        raise OrchestrateError(",
        replacement=(
            "    while not path.exists() and iteration > ITERATION:\n"
            "        iteration -= 1\n"
            "        path = iter_metrics_path(corpus=corpus, detector=detector,\n"
            "                                 supervision=supervision, porting=porting,\n"
            "                                 iteration=iteration, root=orchestrate.ROOT)\n"
            "    if not path.exists():\n"
            "        raise OrchestrateError("
        ),
        breaks=(
            "**A missing round's score is replaced by the last round that has one, and the "
            "refusal is kept for the case where there is none.** It reads as robustness: an arm "
            "whose round 4 left no `metrics.json` continues from round 3 rather than dying, the "
            "guard still fires when nothing has been scored at all, and no message or docstring "
            "has to change.\n"
            "\n"
            "It dissolves the one mechanism DESIGN §5.5 relies on. A format failure ends the arm "
            "by *not writing a score* — `format_failure.json` is written instead, deliberately, "
            "because zeros in a metrics file would read as a rule set that ran and caught "
            "nothing — and no flag anywhere records that it happened. Walking back means round 3 "
            "after a failed round 2 reads round 1's file as round 2's: `_leak_rates` then holds "
            "round 1's rate twice, so `improvements` gains the same phantom `0.0` that "
            "`the_history_is_pre_seeded_with_this_rounds_rate` manufactures, and the arm can stop "
            "on it. The same edit swallows a genuine gap, which is the other half of what this "
            "one read guarantees: rounds are contiguous from 1 because each is read "
            "individually.\n"
            "\n"
            "The mutant does not get all the way through the round — `read_spans` refuses the "
            "predictions the failed round also never wrote — and that is the point rather than a "
            "mitigation. What the guard buys is *which* refusal a reader gets: `no score for "
            "round 2 … either that round has not run, or it ended in a format failure` names the "
            "round and the two states it can be in, where the mutant raises a `FoldRunError` "
            "about an absent prediction list from inside the audit step, two modules away from "
            "the decision that was actually violated.\n"
            "\n"
            "Caught by `test_a_round_after_a_format_failure_is_refused`, which runs the whole "
            "sequence — round 2 answers with unparseable YAML, round 3 is attempted — and "
            "matches the refusal's text rather than merely asserting that something raised."
        ),
        min_kills=1,
    ),
    # ── prompt caching: the transport moved and the cost column must not (DESIGN §11.3) ──
    # Five mutations, and they are one family: each is a way for a service's transport
    # optimisation to end up wearing the clothes of a result. The first splits a prompt whose
    # prefix changes every round; the second and third make the saving unfalsifiable by
    # deleting the number that contradicts it or the check that verifies it; the fourth moves
    # the boundary onto the masked document. The fifth is the decision itself — whether the
    # consumer is a keyword or an inference — and it is the one whose defence had to be built
    # rather than found.
    Mutation(
        name="the_rule_authors_prompt_is_cached_too",
        path=LOOP,
        # `run_iteration`'s RuleAuthor call and not `run_iteration_1`'s. The two lines are
        # identical, so the anchor takes the following line to pick the round-2+ one — which is
        # also the only one the `also` edit reaches, since round 1 delegates to
        # `assemble_task_prompt` and gets no boundary. That asymmetry is the mutation's point.
        anchor=(
            "    response = invoke(prompt, model_id=model_id, client=client, **kwargs)\n"
            "    model = response.model_record()"
        ),
        replacement=(
            "    response = invoke(prompt, model_id=model_id, client=client, cache=True, "
            "**kwargs)\n"
            "    model = response.model_record()"
        ),
        also=((
            PROMPT,
            "    return FilledPrompt(text, {\n"
            '        "block": "iteration",\n'
            '        "lang": lang,',
            "    return FilledPrompt(text, {\n"
            '        "cache_after": len(_template()) + 2,\n'
            '        "cache_boundary": CACHE_BOUNDARY,\n'
            '        "block": "iteration",\n'
            '        "lang": lang,',
        ),),
        breaks=(
            "**The RuleAuthor's prompt is split and cached too, at the end of its template.** "
            "The two edits together are what makes it faithful: the assembler grows a boundary "
            "and the call site consumes it, which is the change anyone extending caching to a "
            "second agent would write. It reads as obvious economy — `rule_author.md` is a "
            "committed file sent on every round of every arm, so caching it is the same argument "
            "that justified caching `auditor.md`.\n"
            "\n"
            "It is not the same argument, and the difference is *N*. The Auditor's prefix is one "
            "template repeated once per dev document within a round, seconds apart: one write "
            "and N−1 reads inside a 5m TTL (`config/naming.yaml`'s `caching_ttl` gloss, measured "
            "2026-08-16). The RuleAuthor is called **once per round** and rounds are 40–80 "
            "minutes apart, so every write expires before the next call and the cache never "
            "hits. What the arm buys is a `cacheWrite` charge on every round — Bedrock prices a "
            "write above a plain input token — recorded as `write_tokens` with `read_tokens: 0` "
            "for the arm's whole life. §11.3's cost comparison then reports `port-loop` as more "
            "expensive than the loop actually is, in the direction that makes the 1.9× standard "
            "*harder* to clear, which is why nothing looks wrong: a cost overrun that hurts your "
            "own arm reads as conservatism.\n"
            "\n"
            "Worse than the price is what it does to §4. Round 1 of `port-loop` and "
            "`port-oneshot`'s single call are required to be shown the same bytes, and "
            "`assemble_task_prompt` is the shared code path that guarantees it. The boundary "
            "added here is on `assemble_iteration_prompt`'s return, which round 1 does not take "
            "— so rounds 2+ are transported differently from round 1 of the same arm, and the "
            "`caching` block for round 1 stays absent while later rounds report a boundary that "
            "no measurement in this project ever validated. `cache_after` is `len(_template()) + "
            "2` and nothing checks that it falls where a block ends: the offset is admissible, "
            "so `for_transport_blocks` accepts it.\n"
            "\n"
            "Caught by `test_only_the_auditors_calls_carry_a_cache_point`, which counts "
            "`cachePoint` blocks per call and asserts the RuleAuthor's is zero — and by "
            "`test_cache_true_appears_once_in_the_project_and_it_is_the_audit_call`, which reads "
            "every `invoke` call in `src/` off the syntax tree and requires the single "
            "`cache=` keyword to be in `src/porting/loop.py`. The second is what makes the "
            "mutation's *shape* visible: this is the family that arrives as one keyword added at "
            "a second call site, and a behavioural test on one round would pass on it if the "
            "fake happened not to distinguish the two prompts."
        ),
        min_kills=2,
    ),
    Mutation(
        name="prompt_tokens_is_what_the_invoice_was_computed_on",
        path=BEDROCK,
        anchor="    read, write = _cache_tokens(usage)\n    prompt_tokens = input_tokens + read + write",
        replacement="    read, write = _cache_tokens(usage)\n    prompt_tokens = input_tokens",
        also=((
            BEDROCK,
            "    if prompt_tokens + completion_tokens != total:",
            "    if prompt_tokens + read + write + completion_tokens != total:",
        ),),
        breaks=(
            "**`prompt_tokens` becomes `inputTokens` — the billed basis — and the cross-check is "
            "adjusted so it still passes.** The second edit is what makes this the plausible "
            "version rather than a crash: leave it out and `_usage` refuses every cached call, "
            "which anyone would notice in a minute. With it, the arithmetic is still verified "
            "against `totalTokens`, every test that reads a `caching` block still passes, and "
            "`sum_costs` still adds. The change reads as a *correction* — these are the tokens "
            "the invoice was computed on, and CLAUDE.md says to report cost.\n"
            "\n"
            "What it reports is a 340× reduction in a column that measures work. Measured "
            "2026-08-16: `inputTokens` was 7193 on the control call and 21 on the cache read, "
            "for two calls on which the model read the same text. `port-loop`'s prompt tokens "
            "would fall by roughly the Auditor's share of the round — `auditor.md` is 80.7% of "
            "an average audit call, and 1.71M of the round's 2.12M prompt tokens are that one "
            "template sent 250 times — and the arm would appear to clear §11.3's pre-registered "
            "1.9× standard on the strength of a service's transport optimisation. That is a "
            "claim about AWS's cache infrastructure sitting in the place a claim about role "
            "specialisation belongs. Nothing in the file contradicts it: the `caching` block "
            "would report the reads, but a reader has no way to know that the reads had already "
            "been *subtracted* from `prompt_tokens` rather than being reported beside a raw "
            "total, because both files have the same shape and neither says which.\n"
            "\n"
            "The direction is what makes it dangerous. The previous mutation costs the arm money "
            "and reads as conservatism; this one earns the arm its headline result and reads as "
            "accuracy. Caught by "
            "`test_prompt_tokens_is_the_raw_total_on_all_three_probe_calls` — twice, on the write "
            "and the read parametrisations, since the control call has no cache tokens and its "
            "case is *unaffected*, which is itself the finding: an uncached arm's cost column is "
            "identical under this mutation and only a cached call can see it. Also by "
            "`test_the_billed_basis_is_recoverable_and_is_not_the_headline`, which asserts both "
            "numbers and their difference, and by "
            "`test_the_write_call_and_the_read_call_are_told_apart_by_the_block`, which requires "
            "the write and the read to report the same `prompt_tokens` — the property that makes "
            "the column mean work rather than billing."
        ),
        min_kills=3,
    ),
    Mutation(
        name="the_assembled_total_is_trusted_rather_than_checked",
        path=BEDROCK,
        anchor="    if prompt_tokens + completion_tokens != total:",
        replacement="    if False:",
        breaks=(
            "**The cross-check against `totalTokens` is removed and the key is still "
            "required.** The read stays, the type check stays, the message stays, and every "
            "response this project has ever seen produces identical numbers — so nothing fails "
            "today and the branch reads as a check that was found to be redundant.\n"
            "\n"
            "It removes the only reason the 2026-08-16 measurement was worth making. Bedrock "
            "publishes the same quantity twice, by paths that do not share an implementation: "
            "the three components, and its own `totalTokens`, which was 7197 on the control, the "
            "write and the read while the components moved between them. That redundancy is what "
            "lets `prompt_tokens = inputTokens + cacheRead + cacheWrite` be *verified* rather "
            "than asserted from a docstring. Without it the cost column is a single unverified "
            "sum, and the failure it was built for is the one that cannot be noticed any other "
            "way: a platform that redefines `cacheReadInputTokens` to overlap `inputTokens`, or "
            "adds a fourth component, produces plausible numbers in every arm and every file. "
            "The arms stay internally consistent and the comparison between them is wrong, which "
            "is the shape of defect DESIGN §11.3 exists to prevent and no downstream test can "
            "see — `sum_costs` adds whatever it is given, and a leak rate is unaffected.\n"
            "\n"
            "Caught by `test_the_assembled_total_is_cross_checked_against_the_envelopes_own`, "
            "which perturbs one component of a measured envelope so the sum no longer reaches "
            "7197 and requires the refusal. That is the only test that can catch it: a mutation "
            "removing a check is invisible to every input on which the check passes, so the "
            "suite has to carry an input on which it must fail."
        ),
        min_kills=1,
    ),
    Mutation(
        name="the_cache_boundary_crosses_onto_the_masked_document",
        path=PROMPT,
        anchor='    cache_after = len(cached_prefix) + 2',
        replacement=(
            '    cache_after = len(cached_prefix) + 2 + len(\n'
            '        f"### {MASKED_DOCUMENT} The masked document — "\n'
            "        f\"{_count(reference['n_lines'], 'line', 'lines')}, \"\n"
            "        f\"{_count(reference['n_tags'], 'mask tag', 'mask tags')}\"\n"
            '    ) + 2'
        ),
        breaks=(
            "**The boundary moves past §1.2's heading, so the masked document's first bytes are "
            "on the cached side.** As written it takes in the heading and its two counts and "
            "nothing else, which is the version that survives review: the heading is "
            "*structural*, it is the same sentence on every call, and moving it into the cached "
            "prefix looks like extending the cache by a few dozen constant characters. The "
            "docstring's claim still reads true — the document is still 'after' the boundary in "
            "the sense the sentence is skimmed for.\n"
            "\n"
            "It is not constant, and it is not the document's neighbour by accident. The counts "
            "are `n_lines` and `n_tags` of *this* document, so the cached prefix now differs "
            "between documents: the cache misses on nearly every call and the round pays a write "
            "per document instead of one — visible only as `write_tokens` roughly N times too "
            "large in a block whose magnitude nobody has a prior for. And the boundary is now "
            "the wrong *kind* of thing. `after_audit_frame` is a `config/naming.yaml` value "
            "whose gloss states exactly which bytes are retained, and `auditor.md` §6 publishes "
            "that statement to the agent; a boundary one block further along makes both false "
            "while the recorded value still says `after_audit_frame`. Extend the same edit by "
            "one more join and the document's masked text itself is what a third party retains "
            "for five minutes — the offset is the only thing standing between a public-bytes "
            "claim and corpus text on someone else's disk, and it is an integer nobody re-derives "
            "downstream.\n"
            "\n"
            "Caught by `test_the_masked_document_is_on_the_far_side_and_is_never_cached`, which "
            "asserts the *filled* heading with this document's counts is absent from the cached "
            "side — a bare `### 1.2` assertion would fail on the correct split, since that "
            "string is in the committed template and legitimately cached — and by "
            "`test_the_boundary_is_the_end_of_the_frame_and_not_one_join_short_or_long`, which "
            "pins the offset to the frame's end rather than to a neighbourhood of it. The other "
            "two assertions on this offset "
            "(`test_the_cached_side_is_the_three_things_the_bullet_names` and "
            "`test_the_offset_is_not_found_by_searching`) pass on this mutant and are what stop "
            "the neighbouring variants: the cached side still contains the three things §6 names, "
            "and it now contains a fourth."
        ),
        min_kills=1,
    ),
    Mutation(
        name="caching_is_inferred_from_the_prompt_carrying_a_boundary",
        path=BEDROCK,
        anchor=(
            "    if not cache:\n"
            '        return [{"text": prompt.for_transport()}], None, None\n'
            "\n"
            "    reference = prompt.reference()\n"
            '    missing = [key for key in ("cache_after", "cache_boundary") '
            "if key not in reference]"
        ),
        replacement=(
            "    reference = prompt.reference()\n"
            '    if not cache and "cache_after" in reference:\n'
            "        cache = True\n"
            "    if not cache:\n"
            '        return [{"text": prompt.for_transport()}], None, None\n'
            "\n"
            '    missing = [key for key in ("cache_after", "cache_boundary") '
            "if key not in reference]"
        ),
        breaks=(
            "**`cache=True` stops being the decision: a prompt whose reference form carries a "
            "boundary is cached whether or not anyone asked.** This is the design that was "
            "considered and rejected on 2026-08-18 (DESIGN §5.4), and the mutation exists "
            "because a rejected design defended only by a docstring is not defended. Every "
            "refusal in the function is kept — `cache=True` on a boundary-less prompt still "
            "raises — the keyword still works, and the behaviour on today's code is "
            "*byte-identical*, because the only assembler that produces a boundary is the only "
            "call site that passes the keyword. It reads as removing a redundant argument.\n"
            "\n"
            "The failure is not in this commit, it is in the next one. The moment any assembler "
            "grows a `cache_after` — a second agent, a widened window, a probe reusing "
            "`FilledPrompt` — its calls begin being cached silently, at a boundary nobody chose "
            "for that prompt and no measurement validated. The mutation "
            "`the_rule_authors_prompt_is_cached_too` above then arrives as an **omission** "
            "rather than an edit: adding two keys to a reference dict starts a cache, with no "
            "`cache=True` anywhere in the diff for a reviewer to stop at, and §4's byte-identical "
            "claim about round 1 breaks without a line to point to. That is the whole argument "
            "for the keyword — the boundary's producer stays single and the consumption decision "
            "stays explicit — and it is an argument about diffs, so no test of the current "
            "behaviour can carry it.\n"
            "\n"
            "Caught by `test_caching_is_never_inferred_from_the_prompt_carrying_a_boundary`, "
            "which is the test this mutation was written to force into existence: it hands "
            "`invoke` a prompt whose reference form carries `cache_after` and `cache_boundary`, "
            "omits the keyword, and requires one content block and no `caching` record. And "
            "incidentally by `test_the_cached_call_sends_the_same_bytes_as_the_uncached_one`, "
            "whose *uncached* control is a boundary-carrying prompt and therefore stops being "
            "uncached on the mutant.\n"
            "\n"
            "**`test_cache_is_off_by_default` does not catch it, and that is the entry's point.** "
            "The signature is untouched: the default is still `False` and still keyword-only, and "
            "the override happens in the body. A structural test on the parameter is a test that "
            "the keyword *exists*, not that it decides anything — so the behavioural test is the "
            "whole defence, and one purpose-written test is what stands between this decision and "
            "a docstring. That is the sixth family exactly: before it was written the design was "
            "held in place by prose plus the coincidence that the one assembler with a boundary "
            "is the one call site with the keyword."
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
