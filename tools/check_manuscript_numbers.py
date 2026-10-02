#!/usr/bin/env python3
"""Check a manuscript draft's numbers against the files they were taken from.

This reads a draft and writes nothing to it. It parses the `Figure captions`
section and Tables 1, 3, 5, 6 and 7, pulls the numbers out of them, reads the
same `metrics.json` files and frozen `splits/{corpus}.json` files the figures
are drawn from, and prints **only disagreements**. A clean run prints the
number of checks and nothing else.

Three rules govern it:

1. **The draft is an input and never an output.** No sentence is generated,
   rewritten or suggested. The repository holds no copy of the manuscript
   either: the path is an argument, and a path inside this repository is
   refused, because the repository is public and an unsubmitted draft would be
   published by pushing it (CLAUDE.md).
2. **No manuscript prose reaches the output.** A mismatch names the table, the
   row and the column, and prints the two numbers. A cell that does not parse
   as a number or a known marker is reported by length, not by content — a
   draft may quote a corpus, and quoting a cell back would carry that into a
   terminal and a CI log (CLAUDE.md).
3. **Silence has to mean agreement, not an unread table.** Every check is
   counted, and a table, column, row or caption phrase the parser cannot find
   is reported as UNCHECKED rather than skipped. A reworded caption therefore
   shows up instead of quietly passing.

Derived columns (Table 1's `Gain`, Table 3's `Difference`) are checked as
arithmetic over the two recorded values, since the repository stores the
values and not their difference.

What this does **not** cover: Table 2 (covered spans by layer and round) and
Table 4 (Auditor output by round) are not checked, and nothing in the running
prose is checked — only the five tables named above and the captions. Silence
from this tool says nothing about those.

Usage
-----
    python tools/check_manuscript_numbers.py ~/Desktop/aiim-draft-v11.md

Exit status is 1 if anything mismatched or could not be checked, so it can be
run before a submission the way `release_screen.py` is run before a commit.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_figures import (  # noqa: E402  (same directory, shared loaders)
    BOUND_MODE,
    DISPLAY_NAME,
    HEADLINE_MODE,
    HUMAN_REFERENCE,
    REPO,
    Arm,
    check_display_names,
    discover,
    label_lines,
    load_json,
    naming,
    one,
    reference_kind,
)

#: Table 7's column headers against the `layer` axis of `config/naming.yaml`.
#: The manuscript names the layers in prose and the files name them by id; this
#: is the one place the two are paired, and `check_layer_columns()` refuses an id
#: the axis does not declare, the same guard `DISPLAY_NAME` has.
LAYER_COLUMN = {
    "Context cues": "context_cue",
    "Gazetteer": "gazetteer",
    "Pattern rules": "regex_checksum",
}

#: Markers a table cell may carry instead of a number. Both are claims about the
#: files and are checked as such: `sparse` against the scorer's own flag, the
#: dash against the type being absent from the reference.
SPARSE_MARKER = "sparse"
ABSENT_MARKERS = {"-", "n/a", ""}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


# --------------------------------------------------------------------------- #
# reading the draft
# --------------------------------------------------------------------------- #


def manuscript_path(raw: str) -> Path:
    """Resolve the draft's path, refusing one inside this public repository."""
    path = Path(raw).expanduser().resolve()
    if path.is_relative_to(REPO):
        raise SystemExit(
            "refusing a manuscript path inside the repository: the repository is "
            "public, so a draft committed here is a draft published. Keep the "
            "draft outside and pass its path."
        )
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    return path


def normalise(cell: str) -> str:
    """Strip markdown emphasis and unify the dashes a word processor inserts."""
    text = cell.replace("*", "").replace("`", "").strip()
    text = text.replace("−", "-")                       # minus sign
    text = text.replace("–", "-").replace("—", "-")  # en, em dash
    text = text.replace(" ", " ")                       # non-breaking space
    return text.strip()


def table_rows(text: str, number: int) -> list[list[str]] | None:
    """The pipe table that follows `**Table N.**`, as rows of cleaned cells."""
    anchor = re.search(rf"^\*\*Table {number}\.\*\*", text, re.MULTILINE)
    if anchor is None:
        return None
    rows = []
    for line in text[anchor.end():].split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [normalise(c) for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):   # the header rule
            continue
        rows.append(cells)
    return rows or None


def caption(text: str, number: int) -> str | None:
    """The body of `**Fig. N.**` inside the `Figure captions` section."""
    section = re.search(r"^## Figure captions$(.*?)(?=^## |\Z)", text,
                        re.MULTILINE | re.DOTALL)
    if section is None:
        return None
    block = re.search(rf"\*\*Fig\. {number}\.\*\*(.*?)(?=\*\*Fig\. |\Z)",
                      section.group(1), re.DOTALL)
    return normalise(block.group(1)) if block else None


# --------------------------------------------------------------------------- #
# comparing
# --------------------------------------------------------------------------- #


@dataclass
class Report:
    """Counts every comparison, so that silence can only mean agreement."""

    checks: int = 0
    mismatches: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    def gap(self, where: str, what: str) -> None:
        self.unchecked.append(f"UNCHECKED  {where} · {what}")

    def opaque(self, where: str, what: str, cell: str) -> None:
        # The cell itself is not printed: a draft may quote a corpus.
        self.unchecked.append(
            f"UNCHECKED  {where} · {what} · cell is not a number or a known "
            f"marker ({len(cell)} chars)"
        )

    def number(self, where: str, what: str, cell: str, found: float | int | None,
               tolerance: float = 0.0) -> None:
        """Compare a cell against a file value *at the cell's own precision*.

        The draft rounds; the files do not. Reading the cell's decimal places
        off the cell is what lets 0.154 and 0.15355… agree while 0.155 does not,
        without a tolerance chosen here.
        """
        if found is None:
            self.gap(where, f"{what} · no such value in the files")
            return
        value = parse_number(cell)
        if value is None:
            self.opaque(where, what, cell)
            return
        said, decimals = value
        rounded = half_up(found, decimals) if decimals is not None else float(found)
        self.checks += 1
        if abs(said - rounded) > tolerance + 1e-12:
            shown = f"{rounded:.{decimals}f}" if decimals is not None else f"{rounded:,}"
            self.mismatches.append(
                f"MISMATCH   {where} · {what} · draft {cell} · files {shown}"
            )

    def claim(self, where: str, what: str, holds: bool, says: str, found: str) -> None:
        """Compare a non-numeric claim (a marker, a dash) against the files."""
        self.checks += 1
        if not holds:
            self.mismatches.append(
                f"MISMATCH   {where} · {what} · draft {says} · files {found}"
            )


def half_up(value: float | int, decimals: int) -> float:
    """Round the way a typeset table rounds.

    `round()` sends an exact half to the even neighbour, so a recorded 0.3125
    would print as 0.312 here and 0.313 in the draft, and the tool would report
    its own convention as the draft's error.
    """
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def parse_number(cell: str) -> tuple[float, int | None] | None:
    """`'-0.0384'` -> `(-0.0384, 4)`; `'3,132'` -> `(3132.0, None)`."""
    text = cell.replace(",", "").replace("%", "").strip()
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
        return None
    decimals = len(text.split(".")[1]) if "." in text else None
    return float(text), decimals


def word_number(text: str) -> int | None:
    lowered = text.strip().lower().replace(",", "")
    if lowered.isdigit():
        return int(lowered)
    return WORD_NUMBERS.get(lowered)


def check_layer_columns() -> None:
    declared = set(naming()["axes"]["layer"])
    unknown = sorted(set(LAYER_COLUMN.values()) - declared)
    if unknown:
        raise SystemExit(
            "LAYER_COLUMN values that are not layer ids in config/naming.yaml: "
            + ", ".join(unknown)
        )


# --------------------------------------------------------------------------- #
# the arms the manuscript reports
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Subject:
    """The records the manuscript's five tables and three captions report on."""

    loop: Arm
    rounds: list[Arm]
    sealed: Arm
    singles: dict[str, Arm]
    paths: dict

    @property
    def baseline(self) -> Arm:
        return self.singles[self.loop.corpus]


def subject() -> Subject:
    paths = naming()["paths"]
    dev = [a for a in discover(paths, "metrics") if a.split == "dev"]
    loop = one([a for a in dev if a.iterations > 1], "iterating arm")

    rounds = []
    for i in range(1, loop.iterations + 1):
        path = REPO / paths["itermetrics"].format(
            corpus=loop.corpus, detector=loop.run["detector"],
            supervision=loop.run["supervision"], porting=loop.porting, iteration=i,
        )
        rounds.append(Arm(path, load_json(path)))

    sealed_path = REPO / paths["sealedmetrics"].format(
        corpus=loop.corpus, detector=loop.run["detector"],
        supervision=loop.run["supervision"], porting=loop.porting,
    )

    singles: dict[str, list[Arm]] = {}
    for arm in (a for a in dev if a.iterations == 1):
        singles.setdefault(arm.corpus, []).append(arm)

    return Subject(
        loop=loop,
        rounds=rounds,
        sealed=Arm(sealed_path, load_json(sealed_path)),
        singles={c: one(v, f"single-call arm on {c}") for c, v in singles.items()},
        paths=paths,
    )


def rule_count(s: Subject, iteration: int) -> int | None:
    """How many rules the round's own rule files hold, across its languages."""
    template = s.paths["armrules"].format(
        corpus=s.loop.corpus, detector=s.loop.run["detector"],
        supervision=s.loop.run["supervision"], porting=s.loop.porting,
        iteration=iteration, lang="*",
    )
    files = sorted(REPO.glob(template))
    if not files:
        return None
    return sum(len(yaml.safe_load(f.read_text()).get("rules", [])) for f in files)


def corpus_of_label(label: str) -> str | None:
    """`CARMEN-I (es, ca)` or its bare `es, ca` -> the corpus id."""
    for corpus, name in DISPLAY_NAME.items():
        if label == name or label == label_lines(name)[1].strip("()"):
            return corpus
    return None


def leak(arm: Arm, mode: str) -> dict:
    return arm.data["modes"][mode]["leak"]


# --------------------------------------------------------------------------- #
# the tables
# --------------------------------------------------------------------------- #


def check_table_one(text: str, s: Subject, rep: Report) -> None:
    where = "Table 1"
    rows = table_rows(text, 1)
    if rows is None:
        rep.gap(where, "table not found")
        return
    header, body = rows[0], rows[1:]
    if len(body) != len(s.rounds):
        rep.gap(where, f"draft has {len(body)} rounds, the arm ran {len(s.rounds)}")

    for row in body:
        if len(row) != len(header):
            rep.gap(where, f"a row has {len(row)} cells against {len(header)} headers")
            continue
        cells = dict(zip(header, row))
        index = word_number(cells.get("Round", ""))
        if index is None or not 1 <= index <= len(s.rounds):
            rep.gap(where, "a row does not name a round this arm ran")
            continue
        arm, place = s.rounds[index - 1], f"{where} · round {index}"

        rep.number(place, "fully_covered", cells.get(HEADLINE_MODE, ""),
                   arm.leak(HEADLINE_MODE))
        rep.number(place, "relaxed", cells.get(BOUND_MODE, ""),
                   arm.leak(BOUND_MODE))

        spans = [c.strip() for c in cells.get("Leaked spans", "").split("/")]
        rep.number(place, "leaked spans", spans[0], leak(arm, HEADLINE_MODE)["leaked"])
        if len(spans) > 1:
            rep.number(place, "leak denominator", spans[1],
                       leak(arm, HEADLINE_MODE)["denominator"])

        # The gain is a subtraction of two recorded rates; the files hold the
        # rates, so what is checked is the arithmetic.
        gain = cells.get("Gain", "")
        if index == 1:
            rep.claim(place, "gain", gain in ABSENT_MARKERS, gain,
                      "round 1 has no previous round")
        else:
            previous = s.rounds[index - 2].leak(HEADLINE_MODE)
            rep.number(place, "gain over the previous round", gain,
                       previous - arm.leak(HEADLINE_MODE))

        rep.number(place, "rules in the round's rule files", cells.get("Rules", ""),
                   rule_count(s, index))


def check_table_three(text: str, s: Subject, rep: Report) -> None:
    where = "Table 3"
    rows = table_rows(text, 3)
    if rows is None:
        rep.gap(where, "table not found")
        return
    header, body = rows[0], rows[1:]
    dev_types, test_types = s.loop.by_type(), s.sealed.by_type()

    for row in body:
        cells = dict(zip(header, row))
        raw = cells.get("Type", "")
        phi_type = re.sub(r"\s*\(.*\)$", "", raw).strip()
        marked_sparse = SPARSE_MARKER in raw.lower()
        place = f"{where} · {phi_type}"

        dev, test = dev_types.get(phi_type), test_types.get(phi_type)
        if dev is None or test is None:
            rep.gap(place, "type is not in the records of both folds")
            continue

        rep.number(place, "development", cells.get("Dev", ""), dev["leak_rate"])
        rep.number(place, "sealed test", cells.get("Test", ""), test["leak_rate"])
        rep.number(place, "difference (test - development)", cells.get("Difference", ""),
                   test["leak_rate"] - dev["leak_rate"])

        # A `(sparse)` marker is a claim about the scorer's flag. The reverse is
        # not checked: a type flagged on one fold only is a judgement the draft
        # explains in prose, and this tool does not read prose.
        if marked_sparse:
            rep.claim(place, "sparse marker", dev["sparse"] or test["sparse"],
                      "(sparse)", "the scorer flags it on neither fold")


def check_table_five(text: str, s: Subject, rep: Report) -> None:
    where = "Table 5"
    rows = table_rows(text, 5)
    if rows is None:
        rep.gap(where, "table not found")
        return
    header, body = rows[0], rows[1:]
    if len(body) != len(s.singles):
        rep.gap(where, f"draft has {len(body)} corpora, the records hold "
                       f"{len(s.singles)} single-call arms")

    words = [dict(zip(header, r)).get("Reference", "") for r in body]
    human_word = max(set(words), key=words.count) if words else ""

    for row in body:
        cells = dict(zip(header, row))
        label = cells.get("Corpus", "")
        corpus = corpus_of_label(label)
        if corpus is None or corpus not in s.singles:
            rep.gap(where, f"a row names no corpus this project records "
                           f"({len(label)} chars)")
            continue
        arm, place = s.singles[corpus], f"{where} · {corpus}"

        rep.number(place, "authoring calls", cells.get("Calls", ""), arm.calls)
        rep.number(place, "development gold", cells.get("Dev gold", ""),
                   leak(arm, HEADLINE_MODE)["denominator"])
        rep.number(place, "leak fully_covered",
                   cells.get(f"Leak {HEADLINE_MODE}", ""), arm.leak(HEADLINE_MODE))
        rep.number(place, "leak relaxed", cells.get(f"Leak {BOUND_MODE}", ""),
                   arm.leak(BOUND_MODE))
        rep.number(place, "F1 relaxed", cells.get(f"F1 ({BOUND_MODE})", ""),
                   arm.data["modes"][BOUND_MODE]["overall"]["f1"])

        # The reference column is checked as a distinction and not as a word: the
        # draft may call it what it likes, but exactly the corpora whose frozen
        # split records a non-human reference must be the ones set apart.
        said_human = cells.get("Reference", "") == human_word
        is_human = reference_kind(corpus) == HUMAN_REFERENCE
        rep.claim(place, "reference kind", said_human == is_human,
                  "the same kind as the majority" if said_human else "a kind of its own",
                  "a human reference" if is_human else "not a human reference")


def check_table_six(text: str, s: Subject, rep: Report) -> None:
    where = "Table 6"
    rows = table_rows(text, 6)
    if rows is None:
        rep.gap(where, "table not found")
        return
    header, body = rows[0], rows[1:]

    columns = {}
    for column in header[1:]:
        corpus = corpus_of_label(column)
        if corpus is None or corpus not in s.singles:
            rep.gap(where, f"a column names no corpus this project records "
                           f"({len(column)} chars)")
            continue
        columns[column] = corpus

    for row in body:
        cells = dict(zip(header, row))
        phi_type = cells.get(header[0], "")
        for column, corpus in columns.items():
            cell = cells.get(column, "")
            place = f"{where} · {phi_type} · {corpus}"
            record = s.singles[corpus].by_type().get(phi_type)

            if cell in ABSENT_MARKERS:
                rep.claim(place, "dash", record is None or not record["gold"],
                          "the reference does not annotate it",
                          "the reference has "
                          f"{record['gold'] if record else 0} gold spans")
                continue
            if record is None:
                rep.gap(place, "type is not in this arm's records")
                continue
            if cell.lower() == SPARSE_MARKER:
                rep.claim(place, "sparse marker", record["sparse"],
                          SPARSE_MARKER,
                          f"the scorer does not flag it ({record['gold']} gold spans)")
                continue

            # `0.258 (221)` — the rate and the fold's gold for that type.
            parts = re.fullmatch(r"([^()]+)\(([^)]*)\)", cell)
            if parts is None:
                rep.opaque(place, "rate and n", cell)
                continue
            rep.number(place, "leak rate", parts.group(1).strip(), record["leak_rate"])
            rep.number(place, "n", parts.group(2).strip(), record["gold"])
            rep.claim(place, "not marked sparse", not record["sparse"],
                      "a rate", "the scorer flags the type sparse")


def check_table_seven(text: str, s: Subject, rep: Report) -> None:
    where = "Table 7"
    rows = table_rows(text, 7)
    if rows is None:
        rep.gap(where, "table not found")
        return
    header, body = rows[0], rows[1:]

    for row in body:
        cells = dict(zip(header, row))
        label = cells.get("Corpus", "")
        corpus = corpus_of_label(label)
        if corpus is None or corpus not in s.singles:
            rep.gap(where, f"a row names no corpus this project records "
                           f"({len(label)} chars)")
            continue
        covered = (s.singles[corpus].data["modes"][HEADLINE_MODE]
                   ["complementarity"]["layers"]["covered"])
        for column, layer in LAYER_COLUMN.items():
            if column not in cells:
                rep.gap(f"{where} · {corpus}", f"no column for the {layer} layer")
                continue
            rep.number(f"{where} · {corpus}", f"{layer} covered spans",
                       cells[column], covered.get(layer))


# --------------------------------------------------------------------------- #
# the captions
# --------------------------------------------------------------------------- #


def phrase(text: str, pattern: str, where: str, what: str, rep: Report) -> str | None:
    """A caption's number, found by the sentence that states it."""
    found = re.search(pattern, text)
    if found is None:
        rep.gap(where, f"{what} · the phrase that states it is not in the caption")
        return None
    return found.group(1)


def check_caption_one(text: str, s: Subject, rep: Report) -> None:
    where = "Fig. 1 caption"
    body = caption(text, 1)
    if body is None:
        rep.gap(where, "caption not found")
        return

    dev_n = phrase(body, r"n = ([\d,]+) in-scope gold spans", where,
                   "development denominator", rep)
    if dev_n:
        rep.number(where, "development denominator", dev_n,
                   leak(s.loop, HEADLINE_MODE)["denominator"])

    difference = re.search(r"([\d.]+) \(n = (\d+)\)", body)
    if difference is None:
        rep.gap(where, "observed difference · the phrase that states it is absent")
    else:
        rep.number(where, "observed difference", difference.group(1),
                   abs(s.rounds[0].leak(HEADLINE_MODE)
                       - s.baseline.leak(HEADLINE_MODE)))
        # The difference is two runs and the caption says so; two is a count of
        # records, not a sample size chosen in prose.
        rep.number(where, "runs the difference is between", difference.group(2), 2)

    for what, pattern in (
        ("rounds run", r"over\s+(\w+)\s+rounds"),
        ("pre-registered ceiling", r"ceiling of (\w+) rounds"),
    ):
        said = phrase(body, pattern, where, what, rep)
        if said is None:
            continue
        value = word_number(said)
        if value is None:
            rep.opaque(where, what, said)
        else:
            rep.number(where, what, str(value), s.loop.iterations)

    sealed_n = phrase(body, r"sealed test fold\s*\(n = ([\d,]+)\)", where,
                      "sealed denominator", rep)
    if sealed_n:
        rep.number(where, "sealed denominator", sealed_n,
                   leak(s.sealed, HEADLINE_MODE)["denominator"])


def check_caption_two(text: str, s: Subject, rep: Report) -> None:
    where = "Fig. 2 caption"
    body = caption(text, 2)
    if body is None:
        rep.gap(where, "caption not found")
        return

    said = phrase(body, r"on (\w+) corpora", where, "corpora shown", rep)
    if said is not None:
        value = word_number(said)
        if value is None:
            rep.opaque(where, "corpora shown", said)
        else:
            rep.number(where, "corpora shown", str(value), len(s.singles))

    # `one authoring call per language`: the arm's call count against the number
    # of rule languages its corpus is configured to load.
    if re.search(r"one authoring call per language", body):
        langs = naming().get("corpus_rule_langs", {})
        multi = {c: a for c, a in s.singles.items() if a.calls > 1}
        if len(multi) != 1:
            rep.gap(where, f"the per-language claim names one arm, the records "
                           f"hold {len(multi)} with more than one call")
        else:
            corpus, arm = next(iter(multi.items()))
            declared = langs.get(corpus)
            if not declared:
                rep.gap(f"{where} · {corpus}",
                        "config/naming.yaml declares no rule languages for it")
            else:
                rep.number(f"{where} · {corpus}", "calls, one per language",
                           str(arm.calls), len(declared))


def check_caption_three(text: str, s: Subject, rep: Report) -> None:
    where = "Fig. 3 caption"
    body = caption(text, 3)
    if body is None:
        rep.gap(where, "caption not found")
        return

    said = phrase(body, r"Types with (\w+) or fewer gold spans", where,
                  "sparse threshold", rep)
    if said is None:
        return
    threshold = word_number(said)
    if threshold is None:
        rep.opaque(where, "sparse threshold", said)
        return

    # The threshold is not stored as a number anywhere, so it is checked against
    # the flags it would have produced: on each fold, the scorer flags exactly
    # the types at or below it. A disagreement means the draft states a
    # threshold that did not generate these records.
    for fold, arm in (("development", s.loop), ("sealed test", s.sealed)):
        for phi_type, record in sorted(arm.by_type().items()):
            rep.claim(
                f"{where} · {fold} · {phi_type}",
                f"sparse at n <= {threshold}",
                record["sparse"] == (record["gold"] <= threshold),
                f"n <= {threshold} means sparse",
                f"the scorer flags sparse={record['sparse']} at "
                f"{record['gold']} gold spans",
            )


# --------------------------------------------------------------------------- #


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manuscript", help="path to the draft, outside this repository")
    args = ap.parse_args()

    path = manuscript_path(args.manuscript)
    check_display_names()
    check_layer_columns()

    text = path.read_text()
    s = subject()
    rep = Report()

    for check in (check_table_one, check_table_three, check_table_five,
                  check_table_six, check_table_seven, check_caption_one,
                  check_caption_two, check_caption_three):
        check(text, s, rep)

    for line in rep.mismatches + rep.unchecked:
        print(line)

    print(f"{rep.checks} checks · {len(rep.mismatches)} mismatched · "
          f"{len(rep.unchecked)} unchecked")
    raise SystemExit(1 if rep.mismatches or rep.unchecked else 0)


if __name__ == "__main__":
    main()
