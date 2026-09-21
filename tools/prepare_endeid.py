#!/usr/bin/env python3
"""Build `en-deid`'s corpus root from the source release, and seal its test fold.

`en-deid` and `ko-surro` are two halves of one PhysioNet release (DESIGN §6.5), and the
release directory is shared and read-only. This tool never writes to it.

Two stages, in this order, with `python3 -m src.split --corpus en-deid` between them —
the order DESIGN §6.2 requires, generate → freeze → seal:

    python3 tools/prepare_endeid.py stage --release-dir <the release>
    python3 -m src.split --corpus en-deid          # then commit splits/en-deid.json
    python3 tools/prepare_endeid.py seal

**Why a tool and not `mv`.** The other two corpora have a file per document, so sealing
a fold is moving files. Here all 2,434 records live inside three files, so taking the
test fold out of the corpus root means rewriting them — and the seal has to be "the path
is not known here" rather than "the path is known and politely avoided"
(`config/data_paths.example.yaml`), which a loader-side filter would not be.

**Records are routed as raw lines, never reassembled from parsed values.** The offsets in
`id.deid` are relative to the body in `id.text`, so a round trip through a parser that
normalised a line ending or dropped a trailing space would move every offset in the
affected record while leaving both files well-formed. The framing is located and the
bytes between are copied.

Data handling. The release is authentic clinical text under a DUA. Nothing here prints a
record body, a PHI phrase or a resolved path: counts, ids of files, and fold names only
(CLAUDE.md). Both destinations are places `tools/release_screen.py` refuses to let into a
commit — `data/` is deny-listed and `sealed/` is reported BLOCKED whatever `.gitignore`
says.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora import base  # noqa: E402
from src.corpora.endeid import (  # noqa: E402
    FILES,
    OFFSETS_FILE,
    TEXT_FILE,
    TYPES_FILE,
    _DEID_HEADER_RE,
    _END_MARK,
    _RECORD_START_RE,
)

CORPUS = "en-deid"

#: The fold whose records leave the corpus root. Read from the loader class rather than
#: spelled here, so that "which folds are sealed" has one answer (`base.CorpusLoader`).
def sealed_splits() -> tuple[str, ...]:
    from src.corpora.endeid import EndeidLoader

    return EndeidLoader.sealed_splits


# ─── routing a file's raw lines by record ──────────────────────────────────────


def split_text(lines: list[str], fold_of: dict[str, str]) -> dict[str, list[str]]:
    """`id.text`'s lines, routed by the fold of the record they belong to.

    A line outside any record raises: the release has none, and silently dropping one
    would remove bytes from whichever destination it belonged in.
    """
    out: dict[str, list[str]] = {}
    buf: list[str] = []
    doc_id: str | None = None
    for lineno, line in enumerate(lines, 1):
        match = _RECORD_START_RE.match(line)
        if match is not None:
            if doc_id is not None:
                raise SystemExit(f"{TEXT_FILE} line {lineno}: record opened inside one")
            doc_id = f"{match.group(1)}_{match.group(2)}"
            buf = [line]
            continue
        if doc_id is None:
            raise SystemExit(
                f"{TEXT_FILE} line {lineno} is outside every record. The release has "
                "no such line; refusing rather than deciding where it goes."
            )
        buf.append(line)
        if line.strip() == _END_MARK:
            fold = fold_of.get(doc_id)
            if fold is None:
                raise SystemExit(
                    f"{TEXT_FILE}: a record is in no fold of splits/{CORPUS}.json. "
                    "The split file and the release disagree; do not seal."
                )
            out.setdefault(fold, []).extend(buf)
            doc_id, buf = None, []
    if doc_id is not None:
        raise SystemExit(f"{TEXT_FILE} ends inside an open record")
    return out


def split_offsets(lines: list[str], fold_of: dict[str, str]) -> dict[str, list[str]]:
    """`id.deid`'s lines, routed by the header above them."""
    out: dict[str, list[str]] = {}
    fold: str | None = None
    for lineno, line in enumerate(lines, 1):
        header = _DEID_HEADER_RE.match(line)
        if header is not None:
            doc_id = f"{header.group(1)}_{header.group(2)}"
            fold = fold_of.get(doc_id)
            if fold is None:
                raise SystemExit(
                    f"{OFFSETS_FILE} line {lineno} frames a record that is in no fold "
                    f"of splits/{CORPUS}.json"
                )
        elif fold is None:
            if not line.strip():
                continue
            raise SystemExit(
                f"{OFFSETS_FILE} line {lineno} precedes every header. Refusing rather "
                "than deciding which fold it belongs to."
            )
        out.setdefault(fold, []).append(line)
    return out


def split_types(lines: list[str], fold_of: dict[str, str]) -> dict[str, list[str]]:
    """`id-phi.phrase`'s lines, routed by fields 1 and 2.

    Only the first two fields are read. The phrase is field 6 and is never split off —
    the line is copied whole, which is both the correct behaviour and the reason this
    function cannot leak a surface into a message.
    """
    out: dict[str, list[str]] = {}
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            raise SystemExit(f"{TYPES_FILE} line {lineno} has fewer than 3 fields")
        fold = fold_of.get(f"{parts[0]}_{parts[1]}")
        if fold is None:
            raise SystemExit(
                f"{TYPES_FILE} line {lineno} names a record that is in no fold of "
                f"splits/{CORPUS}.json"
            )
        out.setdefault(fold, []).append(line)
    return out


ROUTERS = {TEXT_FILE: split_text, OFFSETS_FILE: split_offsets, TYPES_FILE: split_types}


# ─── stages ────────────────────────────────────────────────────────────────────


def cmd_stage(release_dir: Path) -> int:
    """Copy the three files from the source release into the corpus root.

    A copy rather than a symlink: the next stage rewrites two of the three, and a
    symlink would make that a write to the shared read-only release.
    """
    # `corpus_root_to_create`, because this is the call that makes the directory exist.
    root = base.corpus_root_to_create(CORPUS)
    if base.sealed_root(CORPUS) is not None:
        print(
            f"{CORPUS} already has a sealed root configured. Staging now would rebuild "
            "the corpus root from the whole release and put the sealed fold back into "
            "it. Refusing.",
            file=sys.stderr,
        )
        return 1
    present = [name for name in FILES if (root / name).exists()]
    if present:
        print(
            f"the corpus root already holds {present}. Delete them deliberately if the "
            "corpus is genuinely being rebuilt — everything downstream is dated from "
            "the split file that was built on them.",
            file=sys.stderr,
        )
        return 1
    missing = [name for name in FILES if not (release_dir / name).is_file()]
    if missing:
        print(f"the release directory does not hold {missing}", file=sys.stderr)
        return 1
    root.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        data = (release_dir / name).read_bytes()
        (root / name).write_bytes(data)
        print(f"staged {name:16} {len(data)} bytes")
    print(f"\n{CORPUS} corpus root staged. Next: python3 -m src.split --corpus {CORPUS}")
    return 0


def cmd_seal() -> int:
    """Move the sealed fold's records out of the corpus root, per the frozen split."""
    from src import split as split_module

    sealed = base.sealed_root(CORPUS)
    if sealed is None:
        print(
            f"config/data_paths.local.yaml has no `sealed:` entry for {CORPUS}. Add one "
            "pointing at sealed/en-deid (the directory need not exist yet) and re-run: "
            "the seal is a path this code writes to, and inventing it here would put "
            "the corpus's test fold somewhere the config does not record.",
            file=sys.stderr,
        )
        return 1
    root = base.corpus_root(CORPUS)
    record = split_module.read(CORPUS)
    fold_of = split_module.fold_of(record)
    folds = sealed_splits()

    already = [name for name in FILES if (sealed / name).exists()]
    if already:
        print(
            f"the sealed root already holds {already}. The seal has run; a second run "
            "would read the sealed fold to rewrite it. Refusing.",
            file=sys.stderr,
        )
        return 1

    routed: dict[str, dict[str, list[str]]] = {}
    for name in FILES:
        lines = (root / name).read_text(encoding="utf-8").splitlines()
        routed[name] = ROUTERS[name](lines, fold_of)
        seen = set(routed[name])
        unknown = sorted(seen - set(record["folds"]))
        if unknown:
            print(f"{name}: routed lines into unknown folds {unknown}", file=sys.stderr)
            return 1

    # Written before anything is removed, and the corpus-root rewrite happens only after
    # every destination file exists. A crash between the two leaves the test fold in both
    # places, which a later `--check` catches; the reverse order could lose it.
    sealed.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        kept = [
            line
            for fold in folds
            for line in routed[name].get(fold, [])
        ]
        (sealed / name).write_text("\n".join(kept) + "\n", encoding="utf-8")
        print(f"sealed  {name:16} {len(kept)} lines")
    for name in FILES:
        kept = [
            line
            for fold in sorted(record["folds"])
            if fold not in folds
            for line in routed[name].get(fold, [])
        ]
        (root / name).write_text("\n".join(kept) + "\n", encoding="utf-8")
        print(f"root    {name:16} {len(kept)} lines")

    print(
        f"\n{CORPUS}: {', '.join(folds)} moved out of the corpus root. Verify with\n"
        f"    python3 -m src.split --corpus {CORPUS} --check"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="stage", required=True)
    stage = sub.add_parser("stage", help="copy the release into the corpus root")
    stage.add_argument("--release-dir", required=True, type=Path)
    sub.add_parser("seal", help="move the sealed fold out of the corpus root")
    args = parser.parse_args(argv)
    if args.stage == "stage":
        if not args.release_dir.is_dir():
            print("the release directory does not exist", file=sys.stderr)
            return 2
        return cmd_stage(args.release_dir)
    return cmd_seal()


if __name__ == "__main__":
    raise SystemExit(main())
