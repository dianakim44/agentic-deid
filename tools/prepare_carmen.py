#!/usr/bin/env python3
"""Stage `es-carmen`'s corpus root from the PhysioNet release, and seal its test fold.

Three stages, in this order, with `python3 -m src.split --corpus es-carmen` already done —
the order DESIGN §6.2 requires, generate → freeze → seal. The split file was frozen on
2026-09-30 against the release itself, so only the last two run here:

    python3 tools/prepare_carmen.py verify-release --release-dir <the release>
    python3 tools/prepare_carmen.py stage         --release-dir <the release>
    python3 tools/prepare_carmen.py seal

**Why a staged copy, and not a seal in place.** The other four corpora are either already
under `data/raw/` or are *derived* roots this repository computes. `es-carmen` was configured
to point straight at the unpacked PhysioNet release, and sealing in place would mean deleting
400 documents' files out of that release — the seal is physical separation (DESIGN §6.1), so
there is no version of it that leaves the release whole. Staging a copy first is what keeps
the release intact and re-unpackable, and `verify-release` is the check that says so before
and after. Everything this tool writes is under `data/` or `sealed/`, and both are deny-listed
in `tools/release_screen.py` and ignored by `.gitignore` by *mechanism* — `^data/` and
`^sealed/`, neither naming a corpus — so the staged root needs no new rule to be treated like
every other corpus root.

**What the seal moves is not what the loader reads.** `src/corpora/carmen.py` reads one
variant (`replaced`) of one layer (`anon`), and the release ships four more copies of the same
2,000 documents: the `masked` variant of both layers, and a second copy of every text under
`txt/{variant}/`. Those are corpus text under the same DUA, so leaving a test document's
`txt/replaced/` copy in the visible root would leave the fold's text where rule development
reads it, whatever this loader happens to open. The seal moves every per-document file of
every sealed document, across both variants, both layers and both text directories.

**Three kinds of file, and a file that is none of them stops the seal.** `plan()` classifies
every path under the root:

  - *per-document* — moves to the sealed root if its document is in a sealed fold;
  - *per-root derived* — a file describing the root it is in, so it is **recomputed** for each
    root rather than copied: `CARMEN1_mappings.tsv` (one row per document, and the language
    label the frozen split stratifies on), the four aggregate `tsv/` exports (one row per
    span, with the span's surface in the fourth column), and `SHA256SUMS.txt`;
  - *shared* — the release's own documents about itself, copied unchanged: `annotation.conf`,
    `README.md`, `LICENSE.txt`.

A path matching none of the three is a release this tool has not read, and the seal refuses
rather than sealing what it recognises and leaving the rest. That is the one failure mode a
seal cannot have: a partial seal reports success and looks identical to a complete one.

**Rows are filtered as raw lines, never reassembled.** The aggregate exports carry span
offsets in their third column; a round trip through a csv writer that requoted a field or
normalised a line ending would move an offset while leaving the file well-formed. The first
tab-separated field is read to decide, and the original bytes are written through — the same
rule `tools/prepare_endeid.py` states for the same reason.

Data handling. CARMEN-I is authentic Spanish and Catalan hospital text under a PhysioNet DUA.
Nothing here prints a body, a span surface, an annotation line or a resolved path: counts,
document ids, fold names and relative file names only (CLAUDE.md, applied without asking
which corpus). Document ids are the release's own file names and carry a document type and an
index, not a patient.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora import base  # noqa: E402
from src.corpora.carmen import (  # noqa: E402
    ANN_ROOT,
    CONCEPT_LAYER,
    MAPPINGS,
    MAPPINGS_FIELDS,
    PHI_LAYER,
    RELEASE,
    SCHEMA,
    UNREAD_TEXT_DIR,
)

CORPUS = "es-carmen"

#: Both anonymisation variants. `src/corpora/carmen.py` names the one it reads (`VARIANT =
#: "replaced"`) and has no reason to know about the other; the seal does, because a variant it
#: does not move is a variant of the test fold left in the visible root. Declared rather than
#: discovered, and `plan()` refuses a directory that is not in this set — a release shipping a
#: third variant must be read before it is sealed, not sealed by pattern.
VARIANTS = ("masked", "replaced")

#: Both annotation layers, for the same reason. The loader reads `anon` and checks `ner`'s
#: file list against the mappings flag; the seal moves both.
LAYERS = (PHI_LAYER, CONCEPT_LAYER)

#: The release's own manifest.
MANIFEST = Path("SHA256SUMS.txt")

#: macOS directory metadata, by exact file name. The release ships **six** of these inside
#: `CARMEN-I/` and the manifest lists all six (`CARMEN-I/`, `ann/`, `ann/masked/`,
#: `ann/replaced/`, `tsv/`, `txt/`); a seventh appears at the top level and the manifest does
#: not, because Finder wrote it after the unpack. None is corpus data, and a listing
#: of the sealed documents' file names is not something to carry into the visible root, so
#: all seven are excluded here — from the copy, from the manifest comparison on either side,
#: and from the manifests this tool writes. Excluded by exact name rather than by a pattern
#: that would also swallow something that mattered, and counted on every run so that the six
#: manifest entries this weakens the release check by are visible rather than assumed. The
#: number reported is the count in the *manifest* (6), not the count on disk (7): what the
#: check is weakened by is an entry it declines to verify, and the unlisted seventh was never
#: verifiable.
LOCAL_NOISE = ".DS_Store"


def is_local_noise(rel: Path) -> bool:
    return rel.name == LOCAL_NOISE

#: The release's documents about itself. Copied to both roots unchanged: a sealed root is a
#: corpus slice and not a directory of leftovers, so it carries the licence it is held under
#: and the schema its annotations declare. `visual.conf` is brat's rendering configuration —
#: type names, labels and colours, no corpus text — and it is listed by name rather than by a
#: `*.conf` glob for the reason `plan()`'s refusal exists: the next file the release adds under
#: `ann/` should stop the seal and be read, not be swept in by a pattern. It was found by that
#: refusal on the first attempt at this seal.
SHARED = (
    SCHEMA,
    ANN_ROOT / "visual.conf",
    RELEASE / "README.md",
    Path("LICENSE.txt"),
)

#: The aggregate exports, keyed by the column holding the document id. One row per span, with
#: the surface in the fourth column, so these are recomputed per root for the same reason
#: `CARMEN1_mappings.tsv` is.
EXPORTS = tuple(
    RELEASE / "tsv" / variant / f"CARMEN-I_{variant}_{layer}.tsv"
    for variant in VARIANTS
    for layer in LAYERS
)
EXPORT_ID_COLUMN = "name"


# ─── the release, and whether it is still the release ───────────────────────────


def read_manifest(release_dir: Path) -> dict[Path, str]:
    """`SHA256SUMS.txt` as relative path -> digest.

    The release's own manifest is the only thing that can say the release is intact, so it is
    parsed rather than regenerated: a digest this tool computed would attest to whatever is on
    disk now.
    """
    path = release_dir / MANIFEST
    if not path.is_file():
        raise SystemExit(
            f"{MANIFEST.as_posix()} is not in the release directory, so there is nothing to "
            "check the release against. This is the file that makes 'the release is still "
            "intact' a measurement instead of an impression."
        )
    digests: dict[Path, str] = {}
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        digest, _, name = line.partition(" ")
        name = name.strip()
        if len(digest) != 64 or not name:
            raise SystemExit(f"{MANIFEST.as_posix()}:{line_no} is not a sha256 line")
        digests[Path(name)] = digest
    return digests


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_against(directory: Path, digests: dict[Path, str], *, label: str) -> int:
    """Every manifest entry present under `directory` with its recorded digest.

    Reports missing files, extra files and digest mismatches as three separate numbers,
    because they mean three different things: a truncated unpack, a root that has been
    written to, and a corrupted file.
    """
    expected = {k: v for k, v in digests.items() if not is_local_noise(k)}
    skipped = len(digests) - len(expected)
    missing: list[Path] = []
    wrong: list[Path] = []
    checked = 0
    for name, digest in sorted(expected.items()):
        path = directory / name
        if not path.is_file():
            missing.append(name)
            continue
        checked += 1
        if sha256(path) != digest:
            wrong.append(name)
    present = {
        p.relative_to(directory)
        for p in directory.rglob("*")
        if p.is_file() and p.name != MANIFEST.name and not is_local_noise(p.relative_to(directory))
    }
    extra = sorted(present - set(expected))
    note = f", {skipped} {LOCAL_NOISE} entr{'y' if skipped == 1 else 'ies'} not checked"
    if missing or wrong or extra:
        print(f"{label}: {len(missing)} missing, {len(wrong)} wrong, {len(extra)} extra{note}")
        for name in missing[:10]:
            print(f"    missing  {name.as_posix()}")
        for name in wrong[:10]:
            print(f"    digest   {name.as_posix()}")
        for name in extra[:10]:
            print(f"    extra    {name.as_posix()}")
        return 1
    print(f"{label}: {checked} files, all digests match, nothing extra{note}")
    return 0


def cmd_verify_release(release_dir: Path) -> int:
    """The release against its own manifest. Run before staging and again after sealing."""
    digests = read_manifest(release_dir)
    return check_against(release_dir, digests, label="release")


def cmd_verify_root() -> int:
    """The corpus root against the manifest the seal wrote for it.

    The corpus root **only**. The sealed root has a manifest of its own and verifying it from
    here would mean opening `sealed/`, which CLAUDE.md forbids and which no amount of "it is
    only a digest" makes acceptable — a digest is computed by reading the bytes. The sealed
    root's manifest was written from the bytes the seal itself wrote, so its guarantee comes
    from the seal and not from a later re-read; `src/eval/run_sealed_eval.py` is the one
    entry point that may check it, under a logged access.
    """
    root = base.corpus_root(CORPUS)
    rc = check_against(root, read_manifest(root), label="corpus root")
    if base.sealed_root(CORPUS) is not None:
        print(
            "    sealed root: not checked. It carries its own SHA256SUMS.txt, written from "
            "the bytes the seal wrote; reading it from here would open the test fold."
        )
    return rc


# ─── classifying a root ─────────────────────────────────────────────────────────


def document_ids(root: Path) -> set[str]:
    """The documents under a root, from the PHI layer of the variant the loader reads.

    One definition, used by the seal for both roots. The loader's own `_read` requires this
    set and the mappings rows to be equal, so a root whose files and rows disagree fails at
    load; taking the file list as the definition here means the recomputed mappings cannot
    disagree with it by construction.
    """
    directory = root / ANN_ROOT / VARIANTS[-1] / PHI_LAYER
    if not directory.is_dir():
        raise SystemExit(
            f"{(ANN_ROOT / VARIANTS[-1] / PHI_LAYER).as_posix()} is not under the root, so "
            "the root holds no documents to seal"
        )
    return {path.stem for path in directory.glob("*.ann")}


def plan(root: Path) -> tuple[dict[Path, str], list[Path], list[Path]]:
    """Every file under `root`, classified. Returns (per-document, derived, shared).

    The per-document map is path -> document id. A path that matches none of the three
    classes raises: see the module docstring on why a seal must not proceed past a file it
    does not recognise.
    """
    per_document: dict[Path, str] = {}
    derived: list[Path] = []
    shared: list[Path] = []
    unknown: list[Path] = []
    docs = document_ids(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if is_local_noise(rel):
            continue
        owner = _per_document(rel, docs)
        if rel == MANIFEST or rel in EXPORTS or rel == MAPPINGS:
            derived.append(rel)
        elif rel in SHARED:
            shared.append(rel)
        elif owner is not None:
            per_document[rel] = owner
        else:
            unknown.append(rel)
    if unknown:
        raise SystemExit(
            f"{len(unknown)} file(s) under the corpus root are neither per-document, nor "
            "per-root derived, nor part of the release's own documentation, so the seal "
            "cannot say which side of it they belong on. First few: "
            f"{[p.as_posix() for p in unknown[:5]]}. Read them, extend this tool's three "
            "classes, and re-run — a seal that moves what it recognises and leaves the rest "
            "reports success and looks exactly like a complete one."
        )
    return per_document, derived, shared


def _per_document(rel: Path, docs: set[str]) -> str | None:
    """The document id a per-document path belongs to, or None if it is not one.

    Shape, not pattern: the parent directory must be one this tool declares, and the stem
    must be a document that exists. A `.txt` under an undeclared variant returns None and so
    reaches `plan`'s refusal, rather than being swept along by a glob.
    """
    parts = rel.parts
    if len(parts) == 5 and parts[:2] == (RELEASE.name, "ann"):
        _, _, variant, layer, name = parts
        if variant in VARIANTS and layer in LAYERS and rel.suffix in (".ann", ".txt"):
            return Path(name).stem if Path(name).stem in docs else None
        return None
    if len(parts) == 4 and parts[:2] == (RELEASE.name, UNREAD_TEXT_DIR.name):
        _, _, variant, name = parts
        if variant in VARIANTS and rel.suffix == ".txt":
            stem = Path(name).stem
            return stem if stem in docs else None
    return None


# ─── writing the two roots ──────────────────────────────────────────────────────


def filter_rows(source: Path, keep: set[str], column: str) -> bytes:
    """A tab-separated file reduced to the rows whose id column is in `keep`.

    Header preserved verbatim, data rows written as the bytes they were read as. The id
    column's position is read from the header rather than assumed, so a release that reorders
    its columns fails here instead of filtering on the wrong field.
    """
    data = source.read_bytes()
    lines = data.split(b"\n")
    header = lines[0]
    fields = header.decode("utf-8").rstrip("\r").split("\t")
    if column not in fields:
        raise SystemExit(
            f"{source.name} has columns {tuple(fields)} and no {column!r} column, so there "
            "is no field saying which document a row belongs to"
        )
    index = fields.index(column)
    out = [header]
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(b"\t")
        if len(parts) <= index:
            raise SystemExit(
                f"{source.name} has a row with {len(parts)} field(s) where the header "
                f"declares {len(fields)}"
            )
        if parts[index].decode("utf-8").strip() in keep:
            out.append(line)
    return b"\n".join(out) + b"\n"


def write_manifest(root: Path) -> int:
    """`SHA256SUMS.txt` for what this root actually holds.

    Recomputed rather than filtered from the release's copy, because three of the files in it
    were rewritten by the seal and their recorded digests are now about the other root's
    version. Each root is then self-verifying, and a diff against the release's manifest
    shows exactly which files moved.
    """
    rows = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or rel == MANIFEST or is_local_noise(rel):
            continue
        rows.append(f"{sha256(path)} {rel.as_posix()}")
    (root / MANIFEST).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def _counts(label: str, root: Path) -> None:
    docs = document_ids(root)
    files = sum(1 for p in root.rglob("*") if p.is_file())
    print(f"    {label:28} {len(docs):5} documents  {files:6} files")


# ─── stages ─────────────────────────────────────────────────────────────────────


def cmd_stage(release_dir: Path) -> int:
    """Copy the release into the configured corpus root, verified against its manifest."""
    root = base.corpus_root_to_create(CORPUS)
    if base.sealed_root(CORPUS) is not None:
        print(
            f"{CORPUS} already has a sealed root configured. Staging now would put a full "
            "copy of the corpus — the sealed fold with it — back into the corpus root. "
            "Refusing.",
            file=sys.stderr,
        )
        return 1
    if (root / RELEASE).exists():
        print(
            f"the corpus root already holds {RELEASE.as_posix()}. Delete it deliberately if "
            "the corpus is genuinely being re-staged: splits/es-carmen.json was frozen "
            "against this content and everything downstream is dated from it.",
            file=sys.stderr,
        )
        return 1
    if release_dir.resolve() == root.resolve():
        print(
            "the release directory and the configured corpus root are the same path. The "
            "whole point of staging is that the seal never touches the release — point "
            f"config/data_paths.local.yaml's corpora: {CORPUS} at a path under data/raw/ "
            "first.",
            file=sys.stderr,
        )
        return 1

    digests = read_manifest(release_dir)
    print("before staging:")
    if check_against(release_dir, digests, label="    release") != 0:
        print(
            "the release does not match its own manifest. Not copying: a staged root is only "
            "as good as what it was copied from.",
            file=sys.stderr,
        )
        return 1

    skipped = []
    copied = 0
    for name in sorted(digests):
        if is_local_noise(name):
            skipped.append(name)
            continue
        source = release_dir / name
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    shutil.copy2(release_dir / MANIFEST, root / MANIFEST)

    print(f"\nstaged {copied} files into the corpus root")
    for name in skipped:
        print(
            f"    not copied  {name.as_posix()}  (macOS directory metadata, not corpus data)"
        )
    print("\nafter staging:")
    rc = check_against(root, digests, label="    corpus root")
    rc |= check_against(release_dir, digests, label="    release")
    if rc != 0:
        return 1
    _counts("staged", root)
    print(
        f"\n{CORPUS} corpus root staged, and the release is byte-identical to what it was "
        "before. Next: add a `sealed:` entry and run\n"
        f"    python3 tools/prepare_carmen.py seal"
    )
    return 0


def cmd_seal() -> int:
    """Write the sealed root, then reduce the corpus root to the folds that stay."""
    from src import split as split_module
    from src.corpora.carmen import CarmenLoader

    sealed = base.sealed_root(CORPUS)
    if sealed is None:
        print(
            f"config/data_paths.local.yaml has no `sealed:` entry for {CORPUS}. Create "
            f"sealed/{CORPUS} and add an entry pointing at it, then re-run: the seal is a "
            "path this code writes to, and inventing it here would put the test fold "
            "somewhere the config does not record.",
            file=sys.stderr,
        )
        return 1
    if (sealed / RELEASE).exists():
        print(
            f"the sealed root already holds {RELEASE.as_posix()}. The seal has run; a second "
            "run would have to read the sealed fold to rewrite it. Refusing.",
            file=sys.stderr,
        )
        return 1

    root = base.corpus_root(CORPUS)
    record = split_module.read(CORPUS)
    fold_of = split_module.fold_of(record)
    folds = CarmenLoader.sealed_splits

    per_document, derived, shared = plan(root)
    docs = document_ids(root)
    unplaced = sorted(d for d in docs if fold_of.get(d) is None)
    if unplaced:
        print(
            f"{len(unplaced)} document(s) under the corpus root are in no fold of "
            f"splits/{CORPUS}.json, so nothing says which side of the seal they belong on. "
            f"First few: {unplaced[:5]}. Refusing.",
            file=sys.stderr,
        )
        return 1
    extra = sorted(d for d in fold_of if d not in docs)
    if extra:
        print(
            f"splits/{CORPUS}.json assigns {len(extra)} document(s) that are not under the "
            f"corpus root. First few: {extra[:5]}. The split file and this root are "
            "different versions of the corpus; do not seal.",
            file=sys.stderr,
        )
        return 1

    to_seal = {d for d in docs if fold_of[d] in folds}
    to_keep = docs - to_seal
    if not to_seal:
        print(
            f"the frozen split puts no document in {list(folds)}, so there is nothing to "
            "seal and the sealed root would be a tree the loader refuses to read.",
            file=sys.stderr,
        )
        return 1

    # The sealed root is written in full before the corpus root loses anything. A crash
    # between the two leaves the test fold in both places, which the per-root document counts
    # and `--check` catch; the reverse order could lose it.
    for rel in shared:
        target = sealed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    moved = 0
    for rel, doc_id in sorted(per_document.items()):
        if doc_id not in to_seal:
            continue
        target = sealed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
        moved += 1
    for rel in sorted(derived):
        if rel == MANIFEST:
            continue
        column = MAPPINGS_FIELDS[0] if rel == MAPPINGS else EXPORT_ID_COLUMN
        target = sealed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(filter_rows(root / rel, to_seal, column))
    sealed_rows = write_manifest(sealed)

    # Only now does the corpus root change.
    for rel, doc_id in sorted(per_document.items()):
        if doc_id in to_seal:
            (root / rel).unlink()
    for rel in sorted(derived):
        if rel == MANIFEST:
            continue
        column = MAPPINGS_FIELDS[0] if rel == MAPPINGS else EXPORT_ID_COLUMN
        (root / rel).write_bytes(filter_rows(root / rel, to_keep, column))
    root_rows = write_manifest(root)

    print(f"sealed  {', '.join(folds)}")
    _counts(f"sealed  {', '.join(folds)}", sealed)
    _counts("root    the folds that stay", root)
    print(f"    {'per-document files moved':28} {moved:5}")
    print(f"    {'manifest rows':28} {sealed_rows:5} sealed  {root_rows:6} root")

    overlap = document_ids(root) & document_ids(sealed)
    if overlap:
        print(
            f"\n{len(overlap)} document(s) are under both roots after the seal. The sealed "
            "fold has not left the corpus root; do not run anything against this corpus "
            "until it has.",
            file=sys.stderr,
        )
        return 1
    print(
        f"\n{CORPUS}: {', '.join(folds)} is out of the corpus root. Verify with\n"
        f"    python3 -m src.split --corpus {CORPUS} --check\n"
        f"    python3 tools/prepare_carmen.py verify-release --release-dir <the release>"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("verify-release", "check the PhysioNet release against its own SHA256SUMS.txt"),
        ("stage", "copy the release into the configured corpus root"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--release-dir", required=True, type=Path)
    sub.add_parser("seal", help="move the sealed fold out of the corpus root")
    sub.add_parser(
        "verify-root", help="check the corpus root against the manifest the seal wrote"
    )
    args = parser.parse_args(argv)

    if args.cmd == "verify-root":
        return cmd_verify_root()
    if args.cmd in ("verify-release", "stage"):
        release_dir = args.release_dir.expanduser()
        if not release_dir.is_dir():
            print("--release-dir is not a directory", file=sys.stderr)
            return 1
        if args.cmd == "verify-release":
            return cmd_verify_release(release_dir)
        return cmd_stage(release_dir)
    return cmd_seal()


if __name__ == "__main__":
    raise SystemExit(main())
