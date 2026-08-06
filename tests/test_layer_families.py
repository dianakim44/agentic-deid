"""The layer -> family declaration in config/naming.yaml (DESIGN §3, §5).

The complementarity breakdown needs to know which layers are the rules ones. That
grouping is declared in the config and validated in `src/corpora/base.py`, and the
validation is what these tests are about — reading a mapping is not the hard part.

The failure this guards against has no symptom. A layer present in the `layer` axis
but in no family contributes to `neither` in the breakdown, which reads as "nothing
found it" rather than as a forgotten line of YAML. So the union must match exactly,
in both directions, and the test that pins that is
`test_a_new_layer_without_a_family_is_refused`.

    python3 -m pytest tests/test_layer_families.py -q
"""
from __future__ import annotations

import pytest

from src.corpora import base


@pytest.fixture(autouse=True)
def _clear_caches():
    """naming() and layer_families() are lru_cached; these tests monkeypatch them."""
    base.naming.cache_clear()
    base.layer_families.cache_clear()
    yield
    base.naming.cache_clear()
    base.layer_families.cache_clear()


def _with_naming(monkeypatch, data):
    """Replace the parsed naming.yaml wholesale.

    Patching the parsed dict rather than writing a temporary YAML file: the object
    under test is the validation, and going through the file would also test yaml's
    parser and the path resolution, which have their own tests.
    """
    base.naming.cache_clear()
    base.layer_families.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: data)


BASE = {
    "axes": {
        "layer": {"regex_checksum": "", "context_cue": "", "gazetteer": "",
                  "tagger": ""},
    },
    "layer_families": {"rules": ["regex_checksum", "context_cue", "gazetteer"],
                       "tagger": ["tagger"]},
}


def _naming(**overrides):
    import copy

    data = copy.deepcopy(BASE)
    data.update(copy.deepcopy(overrides))
    return data


# ─── the committed config ───────────────────────────────────────────────────

def test_the_committed_config_validates():
    """The real naming.yaml must pass. Everything downstream imports this."""
    families = base.layer_families()
    assert families == {"rules": ("regex_checksum", "context_cue", "gazetteer"),
                        "tagger": ("tagger",)}


def test_the_families_partition_the_layer_axis():
    """Union equals the axis, intersection is empty. Stated as the property, so a
    reader does not have to infer it from the fixture above."""
    layers = set(base.axis("layer"))
    members = [m for group in base.layer_families().values() for m in group]
    assert set(members) == layers
    assert len(members) == len(layers), "a layer is in more than one family"


def test_a_shared_name_is_allowed_only_for_a_family_of_one():
    """`tagger` is both a family and a layer, and that is the one safe collision.

    Families and layers are different levels of description, so a shared name is
    normally a way to set a span's `layer` to a family and have it validate. It is
    harmless here because the family has exactly one member: both readings of
    `layer="tagger"` name the same thing.
    """
    families = base.layer_families()
    layers = set(base.axis("layer"))
    for family, members in families.items():
        if family in layers:
            assert members == (family,), (
                f"family {family!r} shares a layer's name with more than one member; "
                "the two readings of that value would no longer agree"
            )


def test_a_shared_name_with_a_second_member_is_refused(monkeypatch):
    """The collision stops being safe the moment the family grows.

    A second learned layer in the `tagger` family makes `layer="tagger"` mean "some
    learned layer", which loses the per-layer provenance DESIGN §3 requires — and
    loses it silently, since the value still validates against the axis.
    """
    data = _naming()
    data["axes"]["layer"]["tagger_crf"] = "a second learned layer"
    data["layer_families"]["tagger"] = ["tagger", "tagger_crf"]
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="shares its name with a layer"):
        base.layer_families()


def test_family_of_answers_for_every_layer():
    for layer in base.axis("layer"):
        assert base.family_of(layer) in base.layer_families()


def test_family_of_refuses_an_unknown_layer():
    """No guessing from the name. `regex_checksum_v2` is not in a family, and the
    plausible-looking prefix must not be enough to place it."""
    with pytest.raises(base.CorpusError, match="not a layer"):
        base.family_of("regex_checksum_v2")
    with pytest.raises(base.CorpusError, match="not a layer"):
        base.family_of("rules")  # a family, not a layer


def test_family_of_a_shared_name_returns_the_family(monkeypatch):
    """`family_of("tagger")` is unambiguous exactly because the family has one member."""
    assert base.family_of("tagger") == "tagger"


# ─── what the validation refuses ────────────────────────────────────────────

def test_a_new_layer_without_a_family_is_refused(monkeypatch):
    """The case this whole declaration exists for.

    A layer is added to the axis and nobody updates the families. Its spans would be
    counted as `neither` — indistinguishable in the output from spans that genuinely
    nothing found. The check is a union comparison rather than a subset one precisely
    so that this fails loudly at load time.
    """
    data = _naming()
    data["axes"]["layer"]["embedding_knn"] = "a new layer nobody familied"
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="embedding_knn.*no family"):
        base.layer_families()


def test_a_family_naming_a_nonexistent_layer_is_refused(monkeypatch):
    """The other direction: a leftover from a rename.

    It contributes nothing to any count, so nothing would ever be visibly wrong; the
    breakdown would simply have a category that never fires.
    """
    data = _naming()
    data["layer_families"]["rules"].append("regex_checksums")  # typo'd plural
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="regex_checksums.*not values"):
        base.layer_families()


def test_a_layer_in_two_families_is_refused(monkeypatch):
    """The breakdown counts each layer once, so the families must partition.

    Double-counted, the layer inflates `both` while the totals still add up to
    something plausible — the arithmetic gives no hint.
    """
    data = _naming()
    data["layer_families"]["rules"].append("tagger")
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="both the"):
        base.layer_families()


def test_a_multi_member_family_named_after_a_layer_is_refused(monkeypatch):
    """Renaming `rules` to `gazetteer` — the same collision, on the bigger family.

    Here the ambiguity is not academic: `layer="gazetteer"` could mean the gazetteer
    layer or any of the three rules layers, and both readings validate.
    """
    data = _naming(layer_families={
        "gazetteer": ["regex_checksum", "context_cue", "gazetteer"],
        "tagger": ["tagger"]})
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="shares its name with a layer"):
        base.layer_families()


def test_a_missing_block_is_refused(monkeypatch):
    """Not "fall back to a sensible default". A default would be the hardcoded
    grouping this block exists to remove."""
    data = _naming()
    del data["layer_families"]
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="no layer_families"):
        base.layer_families()


def test_a_bare_string_family_is_refused(monkeypatch):
    """`tagger: tagger` instead of `tagger: [tagger]`.

    Without this check the string iterates character by character and the family
    becomes eight one-letter "layers" — which the union check would then reject with
    a confusing message about layers named `t`, `a`, `g`.
    """
    data = _naming(layer_families={
        "rules": ["regex_checksum", "context_cue", "gazetteer"],
        "tagger": "tagger"})
    _with_naming(monkeypatch, data)
    with pytest.raises(base.CorpusError, match="not a list"):
        base.layer_families()
