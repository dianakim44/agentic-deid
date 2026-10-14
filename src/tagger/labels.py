"""The BIO label set, derived from the `phi_type` axis and from nothing else.

DESIGN §12.1. Ten canonical types give 21 labels: `O`, plus `B-` and `I-` for each.

**Derived from `base.canonical_types()` at import, not written out.** A written-out list
is a second copy of the axis, and the failure mode is silent: a type added to
`config/naming.yaml` and not here produces a tagger that cannot emit it, which looks
exactly like a tagger that never learned it. The one thing written out is the count, as
an assertion — `N_LABELS` is in `registration.yaml` and a mismatch between the two means
the axis moved after the pre-registration, which is a fact about the experiment and not a
detail to absorb.

The ordering is `canonical_types()`'s, which is sorted, so a label id means the same thing
across seeds and across runs. Nothing keys on the integer outside one training run, but a
checkpoint does, and a reordering would silently make one seed's weights decode as another
seed's types.
"""
from __future__ import annotations

from ..corpora.base import CorpusError, canonical_types

#: Loss-masked positions: special tokens, and tokens overlapping a §9.1 excluded span.
#: Torch's own convention, named here so the two reasons for it are visible at the call
#: sites. An excluded span is neither a positive nor a negative (DESIGN §12.1), which is
#: a third state that `O` cannot express — labelling it `O` would train the model that
#: the excluded extent is not PHI, which is not what the exclusion says.
IGNORE_INDEX = -100

OUTSIDE = "O"


def label_names() -> list[str]:
    """`['O', 'B-AGE', 'I-AGE', ...]` in axis order."""
    names = [OUTSIDE]
    for phi_type in canonical_types():
        names.append(f"B-{phi_type}")
        names.append(f"I-{phi_type}")
    return names


def label_ids() -> dict[str, int]:
    return {name: index for index, name in enumerate(label_names())}


def n_labels() -> int:
    return len(label_names())


def phi_type_of(label: str) -> str | None:
    """`'B-NAME'` -> `'NAME'`; `'O'` -> `None`. Raises on anything else.

    Raises rather than returning `None` for an unknown label, because the two answers
    mean opposite things to the decoder: `None` means "this token is outside a span" and
    an unknown label means the label set and the checkpoint disagree. Collapsing them
    would turn a decoding bug into a tagger that quietly finds nothing.
    """
    if label == OUTSIDE:
        return None
    if label[:2] in ("B-", "I-") and label[2:] in canonical_types():
        return label[2:]
    raise CorpusError(
        f"{label!r} is not a BIO label over the phi_type axis "
        f"(have: {label_names()})"
    )


def check_n_labels(expected: int) -> int:
    """Confirm the axis still yields the pre-registered label count.

    `expected` comes from `experiments/hybrid_v24/registration.yaml`. The check is here
    rather than in a test alone because it has to hold at training time: a seed trained
    against a moved axis is not comparable to the seeds before it, and the cheapest place
    to find that out is before the first epoch rather than in the results table.
    """
    actual = n_labels()
    if actual != expected:
        raise CorpusError(
            f"the phi_type axis yields {actual} BIO labels and the registration "
            f"pre-registered {expected}. The axis moved after 2026-10-14. A seed "
            "trained now is not comparable to one trained before the move, so this "
            "refuses rather than widening: decide whether the registration is being "
            "amended (DESIGN §12 first) or whether the axis edit was unintended."
        )
    return actual
