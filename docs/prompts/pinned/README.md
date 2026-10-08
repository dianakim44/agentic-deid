# Pinned prompt revisions

**Nothing in this directory may be edited.** Every file here is a byte-for-byte copy of an
older revision of a prompt in `docs/prompts/`, kept because an arm declared that revision and
ran under it. The bytes *are* the record: a run's `window_freeze.json` names the path and
hashes the file, so an edit here would silently change what a completed arm claims to have
been shown. There is no edit to this directory that is not that.

Each file is named `<prompt>.<first eight hex digits of its own sha256>.md`, and
`tests/test_pinned_prompts.py` asserts the full hash — so the name is checkable rather than
descriptive, and a file whose bytes moved fails before any arm reads it.

The mechanism, the reasons it is a committed file rather than a `git show`, and what a
declaration is are in **DESIGN §6.3**, subsection "An arm may declare which prompt revision it
sends". The declarations themselves are in `config/naming.yaml` under `porting_templates`.

| file | sha256 | the revision it is | declared by |
|---|---|---|---|
| `rule_author.ddc48bdb.md` | `ddc48bdb0a0c…f52cfa` | `docs/prompts/rule_author.md` at `cc7bc4c^`, the last revision carrying the worked examples | `port-loop-noaudit` |

`port-loop` ran under these bytes without needing this directory — it froze them while they
were the file on disk, and `results/es-meddocan/R/sup-free/port-loop/window_freeze.json`
records the same hash. That record is why the copy is verifiable rather than asserted: the two
agree, and the test checks that they do.
