"""The learned span tagger — the `tagger` layer of DESIGN §3, registered in §12.

The `layer` axis has carried `tagger` since the beginning and §6.4's layer table
records it as 0 in all five arms, because nothing filled it. This package is what
fills it: one `xlm-roberta-base` over the five train folds pooled, BIO over the ten
canonical types, 512-token windows at stride 128.

Three modules, split by what they are allowed to see:

  - `labels` — the BIO label set, derived from the `phi_type` axis and nothing else.
  - `windows` — text to windows to BIO, and predictions back to character spans.
  - `train` — the training loop and the epoch-selection rule.

**Data handling, and it is the constraint that shapes this package.** The train folds
include three DUA-restricted corpora. No module here puts a surface, a token string or
a line of corpus text into a message, a log, a progress line or an exception — offsets,
indices, lengths and counts only (CLAUDE.md, applied without asking which corpus).
`tests/test_tagger_windows.py` asserts that of the exception messages, the same way
`tests/test_meddocan_loader.py` does for its loader.

That rule is why the training loop prints a loss and a span count and never a sample,
and why `windows.py` raises with a window index and a token index where a conventional
tokeniser wrapper would show the token.
"""
