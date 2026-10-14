"""Merge policies: how two detectors' spans become one output. DESIGN §12.2.

CLAUDE.md requires the merge to be a **replaceable strategy** — fixed-priority, union and
agent-arbiter must be able to diverge over the same detection results — and that is what
this package is. Nothing here detects anything; it takes a rules span list and a tagger
span list and returns one list.

Two modules:

  - `policy` — applies one policy to one type, and a whole policy map to a document.
  - `selector` — RT-Sel, the deterministic solve of the policy space (DESIGN §12.3).

The vocabulary is `config/naming.yaml`'s `merge_policy` and `merge_theta`. Nothing here
writes a policy name as a literal.
"""
