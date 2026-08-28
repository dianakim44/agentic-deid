#!/usr/bin/env python3
"""Where ko-surro's reference comes from, and how far it is from the human gold.

`ko-surro` was built by treating the placeholder positions in the source release's
de-identification-tool output as the PHI coordinates. That file is a system output, and the
human reference is a separate artefact: `id.deid` lists 1,779 gold PHI locations where the
tool output carries 2,164 placeholders.

Where the reference lives, because this was got wrong once. It is **not** in the credentialed
text release, which ships exactly `id.text` and `id.res`. It is in the *de-identification
software package*, which is **open access** — `id.deid` (gold offsets, numbers only) and
`id-phi.phrase` (offsets, types and the PHI phrase). The package README also names an
`id.types`; that file is in the manifest and in no distribution, and its content is available
as `id-phi.phrase` field 5 instead. See docs/notes/ko-surro-gold-provenance.md.

Three questions this answers:

  (a) where the difference between the placeholder count and the human gold count comes
      from, decomposed into placeholders the gold does not support, gold spans no
      placeholder covers, gold spans sharing a placeholder with a neighbour, and matches;
  (b) what type the untyped placeholders — the ones whose payload is a value rather than a
      type name — carry in the human gold;
  (c) the disagreement rate between the silver reference and the human gold.

The decomposition is externally checkable and checks out: `runStat.pl` in the same package
reports 59 false negatives and recall 0.967 against this reference, and (a) reproduces both
exactly. A parse that got the column roles wrong could not do that.

Two modes:

    describe   structure only — sizes, line counts, delimiters, field-count histograms.
               Runs on whatever subset of the release is present.
    check      the three measurements. Refuses to report a number when the parse does not
               validate, rather than reporting a disagreement rate that is really a
               misparse (see `_validate_offsets`).

Data handling. **Nothing this tool prints contains note text.** Counts, offsets, lengths and
type labels only. `id.deid` holds no text at all. `id-phi.phrase` does — field 6 is the PHI
phrase — and every read of that file splits with maxsplit=5 and discards the remainder, so
the phrase is never bound to a name. That is structural rather than a print-time guard,
because a print-time guard is what the earlier version had and it did not hold: a one-word
phrase is label-shaped. The rule covers exception messages too — the corpus is real nursing
text under a DUA, and an exception message reaches terminals and CI logs where
`tools/release_screen.py` cannot follow it.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ─── the release's own record framing ──────────────────────────────────────────
# START_OF_RECORD=<patient>||||<note index>||||   ...body...   ||||END_OF_RECORD
_START_RE = re.compile(r"^START_OF_RECORD=([^|]+)\|\|\|\|([^|]*)\|\|\|\|\s*$")
_END_MARK = "||||END_OF_RECORD"

# Placeholders are bracket-star delimited: [** ... **]
_TAG_RE = re.compile(r"\[\*\*(.*?)\*\*\]", re.DOTALL)

# A payload that is nothing but digits and separators is a value, not a type name.
_VALUELIKE_RE = re.compile(r"^[\d\s\-/.'’]*$")

# Conservative: a type label is a bare identifier. Anything else is treated as
# possibly-a-phrase and is not printed.
_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,39}$")

HELD = ("id.text", "id.res")
# Two files, not three. The de-identification package's README lists a fifth corpus file,
# `id.types` ("Category of PHIs in id.deid"), and that file is not distributed — it appears
# in the manifest and in no file listing. The types are in `id-phi.phrase` field 5 instead,
# so nothing is lost; see docs/notes/ko-surro-gold-provenance.md for how the manifest was
# mistaken for an inventory.
REFERENCE = ("id.deid", "id-phi.phrase")

# `id.deid`: `Patient <pid>  Note <n>` framing, then one line per instance.
_DEID_HEADER_RE = re.compile(r"^Patient\s+(\S+)\s+Note\s+(\S+)\s*$")
# `start  start  end` — the start is written twice. Not (start, end, length): the third
# field is the end, and field 3 minus field 1 is the span length.
_DEID_SPAN_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$")

# `id-phi.phrase`: `<pid> <note> <start> <end> <type> <text>`, whitespace-separated. The
# sixth field is the PHI phrase itself and may contain spaces, so every read of this file
# splits with maxsplit=5 and discards the remainder. That is why the leak path is closed
# structurally here rather than guarded downstream: the text is never bound to a name.
_PHRASE_FIELDS = 5


@dataclass(frozen=True)
class Record:
    rec_id: str
    sub_id: str
    body: str

    @property
    def uid(self) -> str:
        return f"{self.rec_id}_{self.sub_id}"


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class Aligned:
    """A placeholder, its payload shape, and where it lands in the surrogate text."""

    uid: str
    tag_index: int
    payload_is_value: bool
    label: str | None  # the payload as a type name, when it is one
    span: Span | None  # position in the surrogate body; None if unrecoverable


@dataclass
class GoldTable:
    """Parsed human reference. `basis` records which offset convention validated."""

    spans: dict[str, list[Span]] = field(default_factory=dict)
    types: dict[tuple[str, int], str] = field(default_factory=dict)
    basis: str = "unknown"
    n_lines: int = 0
    n_unparsed: int = 0


# ─── parsing the two held files ────────────────────────────────────────────────


def parse_records(path: Path) -> list[Record]:
    """Split a release file into records. Order is preserved; ids are not unique alone."""
    records: list[Record] = []
    header: tuple[str, str] | None = None
    buf: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        m = _START_RE.match(raw)
        if m:
            if header is not None:
                raise ValueError(f"{path.name}: record start at line {lineno} inside an open record")
            header = (m.group(1), m.group(2))
            buf = []
            continue
        if raw.strip() == _END_MARK or raw.rstrip().endswith(_END_MARK):
            if header is None:
                raise ValueError(f"{path.name}: record end at line {lineno} with no open record")
            records.append(Record(header[0], header[1], "\n".join(buf)))
            header, buf = None, []
            continue
        if header is not None:
            buf.append(raw)
    if header is not None:
        raise ValueError(f"{path.name}: file ends inside an open record")
    return records


def align(masked: Record, surrogate: Record) -> list[Aligned]:
    """Recover each placeholder's span in the surrogate body.

    The two bodies are the same document before and after masking, so the literal text
    between placeholders is common to both. Walking those literals forward pins each
    placeholder to the gap between its neighbours. A gap is returned as None rather than
    guessed when a literal cannot be located, so an unrecoverable span is visible as one.
    """
    out: list[Aligned] = []
    cursor = 0
    pos = 0
    for i, m in enumerate(_TAG_RE.finditer(masked.body)):
        literal = masked.body[cursor : m.start()]
        found = surrogate.body.find(literal, pos) if literal else pos
        payload = m.group(1).strip()
        is_value = bool(_VALUELIKE_RE.match(payload))
        label = payload if (not is_value and _LABEL_RE.match(payload.split()[0] if payload.split() else "")) else None
        if found < 0:
            out.append(Aligned(masked.uid, i, is_value, label, None))
            cursor = m.end()
            continue
        start = found + len(literal)
        # The next literal's start bounds this placeholder's replacement on the right.
        nxt = _TAG_RE.search(masked.body, m.end())
        tail = masked.body[m.end() : nxt.start()] if nxt else masked.body[m.end() :]
        probe = tail[:24]
        if probe:
            e = surrogate.body.find(probe, start)
            end = e if e >= 0 else start
        else:
            end = len(surrogate.body)
        out.append(
            Aligned(masked.uid, i, is_value, label, Span(start, end) if end >= start else None)
        )
        cursor = m.end()
        pos = start
    return out


# ─── parsing the two reference files ───────────────────────────────────────────


def sniff(path: Path) -> dict:
    """Structure of a reference file. Never returns field *values*."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    delim = "||||" if any("||||" in ln and not _START_RE.match(ln) for ln in lines[:400]) else None
    if delim is None:
        delim = "\t" if any("\t" in ln for ln in lines[:400]) else "ws"
    counts: Counter[int] = Counter()
    numeric_cols: Counter[int] = Counter()
    for ln in lines:
        if not ln.strip() or _START_RE.match(ln) or ln.rstrip().endswith(_END_MARK):
            continue
        parts = ln.split(delim) if delim != "ws" else ln.split()
        counts[len(parts)] += 1
        for j, p in enumerate(parts):
            if p.strip().isdigit():
                numeric_cols[j] += 1
    return {
        "bytes": path.stat().st_size,
        "lines": len(lines),
        "record_headers": sum(1 for ln in lines if _START_RE.match(ln)),
        "delimiter": delim,
        "field_counts": dict(sorted(counts.items())),
        "numeric_columns": dict(sorted(numeric_cols.items())),
    }


def parse_reference(deid: Path, phrase: Path | None) -> GoldTable:
    """Parse the gold offsets from `id.deid`, and the gold types from `id-phi.phrase`.

    Both formats are documented in the de-identification package's README and were confirmed
    against the files, so nothing here is inferred. That matters beyond tidiness: the earlier
    version of this function inferred column roles by scanning every field for something
    label-shaped, which on `id-phi.phrase` reaches field 6 — the PHI phrase. A one-word
    phrase is label-shaped, so a surname could be stored and later printed as a type. The
    fix is structural rather than a guard: field 6 is never split off, so it is never bound.

    `id.deid` is offsets only and carries no text at all, which is why it is the file the
    span table is built from. `id-phi.phrase` supplies types positionally, in file order
    within each record — the same order `id.deid` lists them in, which `check` verifies by
    requiring the two files to agree on every span before any type is attached.
    """
    table = GoldTable()
    lines = deid.read_text(encoding="utf-8", errors="strict").splitlines()
    table.n_lines = len(lines)
    cur: str | None = None

    for ln in lines:
        if not ln.strip():
            continue
        m = _DEID_HEADER_RE.match(ln)
        if m:
            cur = f"{m.group(1)}_{m.group(2)}"
            table.spans.setdefault(cur, [])
            continue
        m = _DEID_SPAN_RE.match(ln)
        if m is None or cur is None:
            table.n_unparsed += 1
            continue
        f1, f2, f3 = (int(g) for g in m.groups())
        if f1 != f2:
            # The duplicated-start convention is what identifies this format. If it does not
            # hold, the file is not what this parser was written for and guessing which two
            # of the three fields are the span is exactly the inference this rewrite removed.
            table.n_unparsed += 1
            continue
        table.spans[cur].append(Span(f1, f3))

    if phrase is not None and phrase.exists():
        _merge_types(table, phrase)
    return table


def _merge_types(table: GoldTable, phrase: Path) -> None:
    """Attach types from `id-phi.phrase`, positionally within each record.

    The span coordinates in this file are read too, and used only to check that it lists the
    same spans in the same order as `id.deid`. A type attached to the wrong span would be
    indistinguishable from a real finding about the corpus, so a mismatch drops the type
    rather than recording it.
    """
    seen: Counter[str] = Counter()
    for ln in phrase.read_text(encoding="utf-8", errors="strict").splitlines():
        if not ln.strip():
            continue
        # maxsplit=5: fields 1-5 are structural, and the remainder is the PHI phrase, which
        # is deliberately left unsplit and unread.
        parts = ln.split(None, _PHRASE_FIELDS)
        if len(parts) < _PHRASE_FIELDS:
            table.n_unparsed += 1
            continue
        pid, note, start, end, lab = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not (start.isdigit() and end.isdigit()):
            table.n_unparsed += 1
            continue
        key = f"{pid}_{note}"
        idx = seen[key]
        seen[key] += 1
        spans = table.spans.get(key)
        if spans is None or idx >= len(spans):
            table.n_unparsed += 1
            continue
        if spans[idx] != Span(int(start), int(end)):
            table.n_unparsed += 1
            continue
        table.types[(key, idx)] = lab


def _validate_offsets(table: GoldTable, aligned: dict[str, list[Aligned]], bodies: dict[str, str]) -> tuple[str, float]:
    """Decide whether offsets are body-relative or file-absolute, and how well they land.

    Returns the winning convention and the fraction of gold spans that fall inside their
    record's body under it. A misparse cannot survive this: it scores near zero.
    """
    total = sum(len(v) for v in table.spans.values()) or 1
    inside = 0
    for uid, spans in table.spans.items():
        n = len(bodies.get(uid, ""))
        for sp in spans:
            if 0 <= sp.start <= sp.end <= n:
                inside += 1
    # A file-absolute convention cannot be checked per record without the concatenation
    # offsets, and guessing between conventions would hide a misparse behind whichever
    # scored better. So one convention is checked and a bad score is reported as one.
    return ("body-relative", inside / total)


def _safe_label(lab: str | None, vocab: Counter[str]) -> str:
    """Print a type label only when it is a bare identifier the type column actually holds.

    `vocab` is the reference's own type column. The membership test used to be
    `vocab[lab] >= 5 or lab in vocab`, whose second clause admits anything already in the
    table — so it guarded nothing once a phrase had been stored. Now that field 6 is never
    read a phrase cannot be stored, and this stays as the second line of defence with the
    ineffective clause removed: a label prints only if the column shows it repeatedly.
    """
    if lab is None:
        return "<none>"
    if _LABEL_RE.match(lab) and vocab[lab] >= 5:
        return lab
    return "<label-withheld>"


# ─── modes ─────────────────────────────────────────────────────────────────────


def cmd_describe(release: Path) -> int:
    n_present = sum(1 for f in HELD + REFERENCE if (release / f).exists())
    print(f"release directory: <set>  ({n_present} of {len(HELD + REFERENCE)} files present)")
    for name in HELD + REFERENCE:
        p = release / name
        if not p.exists():
            print(f"\n{name}: ABSENT")
            continue
        info = sniff(p)
        print(f"\n{name}:")
        for k, v in info.items():
            print(f"  {k:18} {v}")
    missing = [f for f in REFERENCE if not (release / f).exists()]
    if missing:
        print(f"\n{len(missing)} reference file(s) absent: {', '.join(missing)}")
        print("Until they are present, ko-surro's gold is the placeholder set — silver.")
    return 0


def cmd_check(release: Path, min_land: float) -> int:
    res, txt = release / "id.res", release / "id.text"
    for p in (res, txt):
        if not p.exists():
            print(f"cannot run: {p.name} absent", file=sys.stderr)
            return 2
    masked, surrogate = parse_records(res), parse_records(txt)
    if len(masked) != len(surrogate):
        print(f"record counts differ: {len(masked)} vs {len(surrogate)}", file=sys.stderr)
        return 2
    pairs = list(zip(masked, surrogate))
    if any(a.uid != b.uid for a, b in pairs):
        n = sum(1 for a, b in pairs if a.uid != b.uid)
        print(f"record keys differ in {n} of {len(pairs)} positions", file=sys.stderr)
        return 2

    aligned: dict[str, list[Aligned]] = {}
    bodies: dict[str, str] = {}
    for a, b in pairs:
        aligned[a.uid] = align(a, b)
        bodies[a.uid] = b.body

    all_tags = [t for v in aligned.values() for t in v]
    n_tags = len(all_tags)
    n_value = sum(1 for t in all_tags if t.payload_is_value)
    n_unrecovered = sum(1 for t in all_tags if t.span is None)
    vocab = Counter(t.label for t in all_tags if t.label)

    print("── silver reference, as ko-surro uses it ──")
    print(f"  records                    {len(pairs)}")
    print(f"  placeholders               {n_tags}")
    print(f"  payload is a type name     {n_tags - n_value}")
    print(f"  payload is a value         {n_value}  ({n_value / max(n_tags, 1):.1%})")
    print(f"  span unrecoverable         {n_unrecovered}")
    # Raw payloads carry an instance number ("<type> <n>"), so the raw variety is much
    # larger than the type vocabulary. Both are reported: the skeleton count is the one
    # that corresponds to a type set, the raw count is what a naive parse would see.
    skeletons = Counter(re.sub(r"\d+", "#", lab) for lab in vocab)
    print(f"  distinct payloads, raw     {len(vocab)}")
    print(f"  distinct payloads, digits normalised   {len(skeletons)}")

    deid = release / "id.deid"
    if not deid.exists():
        print("\nid.deid absent — (a), (b) and (c) are not answerable. Run describe first.")
        return 1

    table = parse_reference(deid, release / "id-phi.phrase")
    basis, land = _validate_offsets(table, aligned, bodies)
    n_gold = sum(len(v) for v in table.spans.values())
    print("\n── human reference, as parsed ──")
    print(f"  lines read                 {table.n_lines}")
    print(f"  lines not parsed           {table.n_unparsed}")
    print(f"  gold spans                 {n_gold}")
    print(f"  offset convention          {basis}")
    print(f"  spans inside their record  {land:.1%}")
    if land < min_land:
        print(
            f"\nREFUSED: only {land:.1%} of gold spans land inside their record body, below the\n"
            f"--min-land threshold of {min_land:.0%}. That is what a wrong column-role inference\n"
            f"looks like, and it is indistinguishable from real disagreement, so no\n"
            f"disagreement rate is reported. Re-run describe, read the field-count histogram,\n"
            f"and fix parse_reference() before trusting anything below."
        )
        return 1

    # (a) where the difference comes from
    matched = 0
    tag_unsupported = 0
    shared = 0
    for uid, tags in aligned.items():
        gold = table.spans.get(uid, [])
        used = set()
        for t in tags:
            if t.span is None:
                continue
            hit = next(
                (
                    j
                    for j, g in enumerate(gold)
                    if j not in used and not (g.end <= t.span.start or t.span.end <= g.start)
                ),
                None,
            )
            if hit is None:
                tag_unsupported += 1
            else:
                used.add(hit)
                matched += 1
        # A gold span may overlap a placeholder that one-to-one matching already spent on a
        # neighbour. Counted separately: it is neither a match nor evidence of a miss.
        for j, g in enumerate(gold):
            if j in used:
                continue
            if any(
                t.span is not None and not (g.end <= t.span.start or t.span.end <= g.start)
                for t in tags
            ):
                shared += 1
    gold_uncovered = n_gold - matched - shared

    print("\n── (a) the placeholder count vs the human count, decomposed ──")
    print(f"  matched (overlapping)      {matched}")
    print(f"  placeholder, no gold span  {tag_unsupported}   → tool false positives")
    print(f"  gold span, no placeholder  {gold_uncovered}   → unmatched gold")
    print(f"  gold spans sharing a matched placeholder  {shared}")
    if n_tags:
        print(f"  implied precision          {matched / n_tags:.3f}")
    if n_gold:
        print(f"  implied recall, 1:1        {matched / n_gold:.3f}")
        print(f"  implied recall, any overlap {(matched + shared) / n_gold:.3f}")
    print(
        "  Matching is one-to-one, so a placeholder spanning two adjacent gold instances\n"
        "  matches one and leaves the other unmatched. The `sharing` row counts those; the\n"
        "  two recall figures bracket the truth and the difference is a merging artefact,\n"
        "  not a miss."
    )

    # (b) what the value-payload placeholders are, in the human gold
    # The vocabulary to check a gold label against is the gold type column — passing the
    # silver payload vocabulary here withheld every label, since the two share no strings.
    gold_vocab: Counter[str] = Counter(table.types.values())
    print("\n── (b) the value-payload placeholders, typed by the human gold ──")
    print(f"  gold type vocabulary       {len(gold_vocab)} labels over {sum(gold_vocab.values())} spans")
    by_type: Counter[str] = Counter()
    unmatched_value = 0
    for uid, tags in aligned.items():
        gold = table.spans.get(uid, [])
        for t in tags:
            if not t.payload_is_value or t.span is None:
                continue
            hit = next(
                (
                    j
                    for j, g in enumerate(gold)
                    if not (g.end <= t.span.start or t.span.end <= g.start)
                ),
                None,
            )
            if hit is None:
                unmatched_value += 1
            else:
                by_type[_safe_label(table.types.get((uid, hit)), gold_vocab)] += 1
    for lab, n in by_type.most_common():
        print(f"  {lab:28} {n}")
    print(f"  {'no gold span':28} {unmatched_value}")

    # (c) silver vs gold disagreement
    disagree = tag_unsupported + gold_uncovered
    denom = max(n_gold, 1)
    print("\n── (c) silver against human gold ──")
    print(f"  disagreeing spans          {disagree}")
    print(f"  rate over human gold       {disagree / denom:.1%}")
    print(
        "  The producing project's 142 not-PHI relabels were joined against this reference\n"
        "  on 2026-08-28: 139 of them fall in the unsupported set and 3 do not, and 37\n"
        "  unsupported placeholders were kept. That join needs that project's results, which\n"
        "  are outside this repository, so it is recorded rather than recomputed here —\n"
        "  docs/notes/ko-surro-gold-provenance.md §8."
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("describe", "check"):
        s = sub.add_parser(name)
        s.add_argument("--release-dir", required=True, type=Path)
        if name == "check":
            s.add_argument("--min-land", type=float, default=0.95)
    a = ap.parse_args()
    if not a.release_dir.is_dir():
        print("release directory does not exist", file=sys.stderr)
        return 2
    if a.mode == "describe":
        return cmd_describe(a.release_dir)
    return cmd_check(a.release_dir, a.min_land)


if __name__ == "__main__":
    raise SystemExit(main())
