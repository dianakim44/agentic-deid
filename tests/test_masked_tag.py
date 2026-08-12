"""The heterogeneous mask tag in config/naming.yaml (DESIGN §3).

Declared before the masker is written, which is the whole point of the timing: the masker's
input carries overlapping spans by design (`RuleSet.detect` preserves them so that merge
policy stays a replaceable strategy — DESIGN §4, §9.3), so an implementation that did not
have this rule in front of it would resolve the overlap by accident. The accident has a
shape: whichever span the loop happened to apply last would win the type, and the masker
would then hold a merge policy that every `port-loop` arm ran through regardless of the
policy it was configured with.

The rule is union-of-extents plus type-only-if-homogeneous, and this file pins the second
half. The value names **no** type, and the test that it names no type is the one that
matters — `[NAME]` for a heterogeneous union would pass every other check here while
restoring the arbitrary choice at the notation layer.

    python3 -m pytest tests/test_masked_tag.py -q
"""
from __future__ import annotations

import pytest

from src.corpora import base
from src.corpora.base import CorpusError, axis, masked_tag_heterogeneous


@pytest.fixture(autouse=True)
def _clear_caches():
    """`naming()` is lru_cached and these tests monkeypatch it."""
    base.naming.cache_clear()
    yield
    base.naming.cache_clear()


def test_the_tag_is_the_declared_one():
    """Written out rather than read from the config, which would compare it to itself.

    It is one value and not a vocabulary, so there is no membership to check — what there
    is to check is that the string in the config is the string this project decided on,
    because it goes into the Auditor's prompt and a silent change to it changes what an
    agent was shown.
    """
    assert masked_tag_heterogeneous() == "[PHI]"


def test_the_tag_names_no_phi_type():
    """**The load-bearing property, and the reason the value exists at all.**

    A heterogeneous union is one the arm's own detectors disagreed about. Printing one of
    the disputed types would pick a winner, which is the merge policy DESIGN §3 keeps out
    of the masker; printing it would also make the union indistinguishable in the masked
    text from a union where the detectors agreed. Both failures are invisible: the output
    is a well-formed tag either way.
    """
    tag = masked_tag_heterogeneous()
    assert tag.strip("[]") not in axis("phi_type")


def test_the_tag_looks_like_the_other_tags():
    """Bracketed, because the agent is told that a tag is not a candidate and that
    instruction has to cover this one without a second clause (`auditor.md` §1.2).

    A differently shaped marker would be a second thing to explain in the prompt, and the
    explanation would be about the masker's internals rather than about the text.
    """
    tag = masked_tag_heterogeneous()
    assert tag.startswith("[") and tag.endswith("]")
    assert tag.strip("[]")


@pytest.mark.parametrize("spelling", ["[NAME]", "NAME", "[DATE]", "[ORGANISATION]"])
def test_a_tag_spelled_as_a_type_is_refused(monkeypatch, spelling):
    """The check is in the accessor, not in a review comment.

    This is the edit someone makes for a good reason — "`[PHI]` is unhelpful in a prompt,
    `[NAME]` reads better" — and it silently reintroduces the tie-break. The message has to
    say why, because the person making the edit is not reading DESIGN §3 at that moment.

    The unbracketed spelling is here too: the guard strips brackets before looking the value
    up, so a bare `NAME` must not slip past a check written for `[NAME]`.
    """
    real = base.naming()
    base.naming.cache_clear()
    monkeypatch.setattr(
        base, "naming", lambda: {**real, "masked_tag_heterogeneous": spelling})
    with pytest.raises(CorpusError) as excinfo:
        masked_tag_heterogeneous()
    message = str(excinfo.value)
    assert spelling.strip("[]") in message
    assert "no type" in message


@pytest.mark.parametrize("value", [None, "", 42, [], {}])
def test_a_missing_or_malformed_tag_is_refused(monkeypatch, value):
    """Absent is refused rather than defaulted.

    A default in the accessor would let the config lose the value and the masker keep
    working, and the config is where DESIGN §3's decision is supposed to be legible.
    """
    real = base.naming()
    base.naming.cache_clear()
    patched = {**real}
    if value is None:
        patched.pop("masked_tag_heterogeneous", None)
    else:
        patched["masked_tag_heterogeneous"] = value
    monkeypatch.setattr(base, "naming", lambda: patched)
    with pytest.raises(CorpusError, match="masked_tag_heterogeneous"):
        masked_tag_heterogeneous()


def test_the_tag_is_not_spelled_in_any_module():
    """CLAUDE.md's rule: the value lands in a prompt and in the masker's output, so no
    module holds it as a literal.

    Asserted over `src/` rather than at the one call site that exists today, because the
    masker does not exist yet and the literal's natural home is the module being written
    next.
    """
    from pathlib import Path

    tag = masked_tag_heterogeneous()
    offenders = []
    for path in sorted(Path("src").rglob("*.py")):
        if tag in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == [], (
        f"{tag!r} appears as a literal in {offenders}. It is read from "
        "config/naming.yaml via masked_tag_heterogeneous() (DESIGN §3)."
    )
