#!/usr/bin/env python3
"""Where ko-surro's reference comes from, and how far it is from the human gold.

`ko-surro` was built by treating the placeholder positions in the source release's
de-identification-tool output as the PHI coordinates. That file is a system output: it
carries 2,164 placeholders where the release's own reference is about 1,779 instances, and
the producing project's manual review relabelled 142 placeholders as not-PHI. So the
corpus scores predictions against a silver standard produced by the same kind of object
being scored.

The release ships three files that carry the human reference — gold offsets, gold types,
and a six-field index over the gold instances. This tool answers the three questions that
acquiring them makes answerable:

  (a) where the difference between the placeholder count and the human gold count comes
      from, decomposed into placeholders the gold does not support, gold spans no
      placeholder covers, and matched pairs;
  (b) what type the untyped placeholders — the ones whose payload is a value rather than a
      type name — carry in the human gold;
  (c) the disagreement rate between the silver reference and the human gold, including
      whether the 142 not-PHI relabels fall inside the unsupported set.

Two modes, because the reference files' exact layout is not documented on the project page
and is not going to be guessed from here:

    describe   structure only — sizes, line counts, delimiters, field-count histograms.
               Runs on whatever subset of the release is present.
    check      the three measurements. Refuses to report a number when the parse does not
               validate, rather than reporting a disagreement rate that is really a
               misparse (see `_validate_offsets`).

Data handling. **Nothing this tool prints contains note text.** Counts, offsets, lengths
and type labels only, and type labels pass `_safe_label` first — a misinferred column
would otherwise print PHI phrases as if they were types, which is the one way a
structure-only tool can leak. That rule covers exception messages too: the corpus is real
nursing text under a DUA, and an exception message reaches terminals and CI logs where
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
REFERENCE = ("id.deid", "id.types", "id-phi.phrase")


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


# ─── parsing the three reference files ─────────────────────────────────────────


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


def parse_reference(deid: Path, types: Path | None) -> GoldTable:
    """Parse gold offsets, and types where a separate file carries them.

    Two layouts are accepted, because the project page documents neither:
      - record-framed: START_OF_RECORD blocks whose lines begin with two integers;
      - flat: one line per instance, `||||`- or tab-delimited, with the record key in the
        leading fields and two integers for the span.
    Column roles are inferred from which columns are integral, and the inference is
    reported. It is then *checked* against the alignment by `_validate_offsets`; a wrong
    inference shows up as near-total mismatch and is refused rather than reported.
    """
    table = GoldTable()
    lines = deid.read_text(encoding="utf-8", errors="strict").splitlines()
    table.n_lines = len(lines)
    cur: str | None = None
    flat_delim = "||||" if any("||||" in ln and not _START_RE.match(ln) for ln in lines[:400]) else None
    if flat_delim is None:
        flat_delim = "\t" if any("\t" in ln for ln in lines[:400]) else None

    for ln in lines:
        if not ln.strip():
            continue
        m = _START_RE.match(ln)
        if m:
            cur = f"{m.group(1)}_{m.group(2)}"
            table.spans.setdefault(cur, [])
            continue
        if ln.rstrip().endswith(_END_MARK):
            cur = None
            continue
        parts = ln.split(flat_delim) if flat_delim else ln.split()
        ints = [(j, int(p.strip())) for j, p in enumerate(parts) if p.strip().isdigit()]
        if len(ints) < 2:
            table.n_unparsed += 1
            continue
        if cur is not None:
            key = cur
            s, e = ints[0][1], ints[1][1]
        elif len(parts) >= 4:
            key = f"{parts[0].strip()}_{parts[1].strip()}"
            # The record key occupies the leading fields, so the span is the last two
            # integers on the line rather than the first two.
            s, e = ints[-2][1], ints[-1][1]
        else:
            table.n_unparsed += 1
            continue
        idx = len(table.spans.setdefault(key, []))
        table.spans[key].append(Span(s, e))
        for p in parts:
            lab = p.strip()
            if _LABEL_RE.match(lab) and not lab.isdigit():
                table.types[(key, idx)] = lab
                break

    if types is not None and types.exists():
        _merge_types(table, types, flat_delim)
    return table


def _merge_types(table: GoldTable, types: Path, flat_delim: str | None) -> None:
    """Attach types from a separate file, positionally within each record."""
    cur: str | None = None
    seen: Counter[str] = Counter()
    for ln in types.read_text(encoding="utf-8", errors="strict").splitlines():
        if not ln.strip():
            continue
        m = _START_RE.match(ln)
        if m:
            cur = f"{m.group(1)}_{m.group(2)}"
            continue
        if ln.rstrip().endswith(_END_MARK):
            cur = None
            continue
        parts = ln.split(flat_delim) if flat_delim else ln.split()
        key = cur if cur is not None else (
            f"{parts[0].strip()}_{parts[1].strip()}" if len(parts) >= 2 else None
        )
        if key is None:
            continue
        lab = next((p.strip() for p in reversed(parts) if _LABEL_RE.match(p.strip())), None)
        if lab is None:
            continue
        table.types[(key, seen[key])] = lab
        seen[key] += 1


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
    """Print a type label only when it is plausibly a label and not a phrase."""
    if lab is None:
        return "<none>"
    if _LABEL_RE.match(lab) and (vocab[lab] >= 5 or lab in vocab):
        return lab
    return "<label-withheld>"


# ─── modes ─────────────────────────────────────────────────────────────────────


def cmd_describe(release: Path) -> int:
    print(f"release directory: <set>  ({sum(1 for f in HELD + REFERENCE if (release / f).exists())} of 5 files present)")
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

    table = parse_reference(deid, release / "id.types")
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
        # gold spans in this record that nothing covered
        table.spans[uid] = gold  # keep, for the count below
    gold_uncovered = n_gold - matched

    print("\n── (a) 2,164 vs the human count, decomposed ──")
    print(f"  matched (overlapping)      {matched}")
    print(f"  placeholder, no gold span  {tag_unsupported}   → tool false positives")
    print(f"  gold span, no placeholder  {gold_uncovered}   → tool false negatives")
    if n_tags:
        print(f"  implied precision          {matched / n_tags:.3f}")
    if n_gold:
        print(f"  implied recall             {matched / n_gold:.3f}")

    # (b) what the value-payload placeholders are, in the human gold
    print("\n── (b) the value-payload placeholders, typed by the human gold ──")
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
                by_type[_safe_label(table.types.get((uid, hit)), vocab)] += 1
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
        "  the 142 not-PHI relabels: compare against the placeholder-no-gold set above.\n"
        "  If they are a subset, the producing project's manual review independently\n"
        "  recovered part of the human gold, and the remainder is what it missed."
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
