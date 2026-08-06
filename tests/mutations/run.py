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
#: Both loader suites. The split file is part of loading now — a mutation that
#: corrupts the folds must be able to be caught by the tests that check them.
TEST_FILES = ["tests/test_meddocan_loader.py", "tests/test_split_file.py"]

#: Repository directories the loader tests need. `splits/` is here because the
#: loader reads `splits/{corpus}.json` to assign folds; without it every test
#: errors on a missing split file and the baseline is not green.
COPIED = ("src", "tests", "config", "splits")


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
        for path, anchor, replacement in (
            (self.path, self.anchor, self.replacement),
            *self.also,
        ):
            target = tree / path
            source = target.read_text(encoding="utf-8")
            if anchor not in source:
                raise StaleMutation(
                    f"{self.name}: anchor not found in {path}. The loader was "
                    "refactored; update the anchor here so the check keeps "
                    "testing what its name claims."
                )
            target.write_text(
                source.replace(anchor, replacement, 1), encoding="utf-8"
            )


class StaleMutation(Exception):
    pass


BASE = "src/corpora/base.py"
MEDDOCAN = "src/corpora/meddocan.py"
SPLIT = "src/split.py"
SPLIT_FILE = "splits/es-meddocan.json"

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
        anchor="        docs = list(self._read())",
        replacement=(
            "        docs = list(self._read())\n"
            "        for _d in docs:\n"
            "            _d.spans = [_s for _s in _d.spans if not _s.excluded]"
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
            "Loads only train and dev. The suite must not be satisfiable by a "
            "corpus that is silently 750 documents instead of 1,000."
        ),
        min_kills=16,
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
            '        grouped = bool(shared["name"]) and '
            'bool(shared["record"] or shared["date"])'
        ),
        replacement='        grouped = bool(shared["name"])',
        breaks=(
            "Weakens §9.5 step 2 to a name match alone, which groups the one stem "
            "sharing a bare given name with different surnames. One group forms "
            "where none should, and two documents stop being independent units."
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
]

COUNT_RE = re.compile(r"(\d+) (passed|failed|error|errors)")


def kills(output: str) -> int:
    """Tests that failed or errored, from pytest's summary line.

    Errors count: a mutation that breaks the module-scoped fixture takes out
    whole tests, and those are caught tests, not uncounted ones.
    """
    counts = {kind.rstrip("s"): int(n) for n, kind in COUNT_RE.findall(output)}
    return counts.get("failed", 0) + counts.get("error", 0)


def make_tree(tmp: Path) -> Path:
    """A throwaway copy of the repository, enough to run the loader tests.

    `data/` is symlinked rather than copied — it is DUA-restricted and up to
    several GB. The symlink is read-only in practice because no mutation touches
    a data path, and copying would be both slow and a second place for restricted
    text to live.
    """
    tree = tmp / "repo"
    tree.mkdir()
    for name in COPIED:
        shutil.copytree(
            ROOT / name, tree / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (tree / "data").symlink_to(ROOT / "data")
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
        base_kills = kills(baseline)
        if base_kills:
            print("BASELINE IS NOT GREEN — fix the suite before mutating.")
            print(baseline[-2000:])
            return 1
        print(f"baseline: {baseline.strip().splitlines()[-1]}\n")

        failures = []
        for mutation in selected:
            tree = tmp / f"mut_{mutation.name}"
            shutil.copytree(pristine, tree, symlinks=True)
            try:
                mutation.apply(tree)
            except StaleMutation as exc:
                print(f"STALE   {mutation.name:24} {exc}")
                failures.append(mutation.name)
                continue
            caught = kills(run_suite(tree))
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
