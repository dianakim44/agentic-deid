"""Corpus loaders.

`load(corpus_id)` is the entry point; the id is a value of the `corpus` axis in
config/naming.yaml. Everything corpus-agnostic is in `base`.
"""
from .base import (  # noqa: F401
    CorpusError,
    CorpusLoader,
    Document,
    SealError,
    Span,
    canonical_types,
    corpus_ids,
    corpus_root,
    count_by_split,
    count_by_type,
    count_spans,
    load,
    rule_langs,
    sealed_root,
)

__all__ = [
    "CorpusError",
    "CorpusLoader",
    "Document",
    "SealError",
    "Span",
    "canonical_types",
    "corpus_ids",
    "corpus_root",
    "count_by_split",
    "count_by_type",
    "count_spans",
    "load",
    "rule_langs",
    "sealed_root",
]
