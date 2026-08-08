"""The language-model path: prompt assembly, transport, and what may be recorded.

Nothing here writes a filled prompt to disk. That is the convention `prompt.py`
implements as a type rather than as a rule, and the reason is in
`docs/prompts/rule_author.md` §6: a filled RuleAuthor prompt carries ±120 characters
of dev text around every span in the sample, which is the corpus rather than a
description of it.
"""
