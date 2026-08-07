"""Load `rules/{lang}.yaml` and run it over a document. The `R` arm's detector.

Three layers, one file, and the layer is declared per rule rather than inferred from
anything (DESIGN §3). What differs between the layers is only how a rule says *what to
match*; everything after the match — the span, its provenance, its `rule_id` prefix — is
the same code, because a per-layer emit path is three places the provenance can drift.

| layer | how the rule specifies its matcher | regex needed |
|---|---|---|
| `gazetteer` | `terms:` — a list of literal strings, or `lexicon:` naming a file of them | no |
| `context_cue` | `cue:` + `then:`, two halves the engine joins | no, for the common case |
| `regex_checksum` | `pattern:` + optional `checksum:` | yes |

**`gazetteer` needs no regex at all, and `context_cue` needs none for the ordinary
case.** That is a deliberate property of the schema and not an accident of it: an author
who can only express themselves in regex writes regex-shaped rules, and DESIGN §7's
prediction is about layers, so a layer that is harder to author is a layer that looks
weaker for reasons that have nothing to do with the phenomenon being measured.

**The regex dialect is `regex`, not `re`.** `\\p{Lu}` and `\\p{L}` appear in
`rule_author.md` §2's own example, and `re` does not implement them — a rule written to
the documented dialect would raise at load time under `re`, which is the version of this
that fails loudly, or worse, silently match nothing if the author simplified it away.

**Nothing here quotes matched text in an exception.** A rule that misbehaves does so on
corpus text, and the useful report is the offsets and the rule id (CLAUDE.md). The one
surface material that legitimately appears in this module's *inputs* is the rule file's
own `terms` and `cue` words, which are clinical formulae by Prohibition 2 and are already
public in a committed file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import regex
import yaml

from .corpora.base import (
    ROOT, CorpusError, Span, axis, family_of, path_template, rule_langs,
)

#: Regex flags a rule may ask for, and what each maps to. An allowlist because `flags`
#: is free text in the file: `regex.DOTALL` would let `.` cross a note's line boundaries
#: and turn a narrow rule into one that spans paragraphs, and there is no reason a
#: de-identification rule needs it.
FLAGS = {
    "unicode": regex.UNICODE,
    "ignorecase": regex.IGNORECASE,
    "multiline": regex.MULTILINE,
}

#: The three layers a rule file may declare, as a subset of naming.yaml's `layer` axis.
#: Derived from `layer_families` rather than listed: the rules family is what
#: `rules/*.yaml` produces, by definition, and a fourth rules-family layer added to the
#: config must reach this module without an edit here (DESIGN §3).
def rule_layers() -> frozenset[str]:
    return frozenset(l for l in axis("layer") if family_of(l) == "rules")


#: Checksum algorithms a `regex_checksum` rule may name. A rule declares the algorithm
#: and the engine holds the implementation, because a check digit is arithmetic and a
#: YAML file expressing arithmetic would be a small programming language nobody tested.
#: Adding one is a function here plus a line in this dict, in a commit.
def _mod23_letter(digits: str) -> str:
    """The DNI/NIE control letter: the digits mod 23 indexed into a fixed table."""
    return "TRWAGMYFPDXBNJZSQVHLCKE"[int(digits) % 23]


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class RuleError(CorpusError):
    """A rule file that cannot be loaded or run as written.

    Subclasses CorpusError so the existing "stop and tell a person" handling applies. A
    rule file is authored by an agent or by a person under §11.1, and a rule that half
    works is worse than one that refuses: it contributes spans to a scored arm.
    """


def _dni_ok(text: str) -> bool:
    """`12345678Z` — eight digits and the mod-23 control letter."""
    body = regex.sub(r"[^0-9A-Za-z]", "", text)
    if len(body) != 9 or not body[:8].isdigit():
        return False
    return body[8].upper() == _mod23_letter(body[:8])


def _nie_ok(text: str) -> bool:
    """`X1234567L` — a leading X/Y/Z standing for 0/1/2, then as DNI."""
    body = regex.sub(r"[^0-9A-Za-z]", "", text).upper()
    if len(body) != 9 or body[0] not in "XYZ" or not body[1:8].isdigit():
        return False
    return body[8] == _mod23_letter(str("XYZ".index(body[0])) + body[1:8])


def _luhn_check(text: str) -> bool:
    digits = regex.sub(r"[^0-9]", "", text)
    return len(digits) >= 2 and _luhn_ok(digits)


def _mod10_check(text: str) -> bool:
    """A plain mod-10 total, for identifier schemes that use one."""
    digits = regex.sub(r"[^0-9]", "", text)
    return bool(digits) and sum(int(d) for d in digits) % 10 == 0


CHECKSUMS = {
    "dni_mod23": _dni_ok,
    "nie_mod23": _nie_ok,
    "luhn": _luhn_check,
    "mod10": _mod10_check,
}


@dataclass(frozen=True)
class Rule:
    """One compiled rule. Immutable: a rule that can be adjusted mid-run is not a rule.

    `matcher` is a compiled pattern whatever the layer — the three authoring forms are
    three ways of *writing* one, and they converge here so that emission is one code
    path. `group` names which capture group is the span: `context_cue`'s generated
    pattern puts the identifier in a group so the cue words are matched but not covered,
    which is the whole point of the layer (the cue is the evidence, not the PHI).
    """

    rule_id: str
    layer: str
    phi_type: str
    matcher: regex.Pattern
    group: int = 0
    checksum: str | None = None
    score: float | None = None
    source: str = ""

    def finditer(self, text: str):
        """Yield `(start, end)` for each match, after any checksum. Offsets only."""
        check = CHECKSUMS[self.checksum] if self.checksum else None
        for m in self.matcher.finditer(text):
            start, end = m.span(self.group)
            if start < 0 or end <= start:
                continue
            if check is not None and not check(text[start:end]):
                continue
            yield start, end


@dataclass
class RuleSet:
    """The rules loaded for one detection run, across one or more files.

    A list rather than a dict keyed by id: `es-carmen` loads `es` and `cat` (DESIGN
    §5.2), and both files may define `doctor_prefix`. The prefixes make the ids unique;
    the ordering is the file order, and it does not matter because the merge is a union.
    """

    rules: list[Rule] = field(default_factory=list)
    versions: dict[str, int] = field(default_factory=dict)

    def detect(self, text: str, detector: str = "R") -> list[Span]:
        """Every rule's matches on `text`, as prediction spans with provenance.

        `layer` is copied from the rule that matched — never derived from the rule's
        name, the detector, or the pattern's shape (DESIGN §3). `rule_id` arrives already
        prefixed with the language of the file it came from, which is why prefixing
        happens at load and not here: a span emitted from a `RuleSet` assembled by hand
        in a test carries the same shape as one from disk.

        Overlapping matches from different rules are all returned. Deduplication and
        merge policy are somebody else's job (DESIGN §4, §9.3) — a detector that resolved
        its own overlaps would make every merge policy produce the same spans.
        """
        out = []
        for rule in self.rules:
            for start, end in rule.finditer(text):
                out.append(Span(
                    start=start, end=end, surface=text[start:end],
                    subtype=rule.rule_id, phi_type=rule.phi_type,
                    layer=rule.layer, detector=detector,
                    rule_id=rule.rule_id, score=rule.score))
        return out


def _compile(pattern: str, flags, rule_id: str, what: str) -> regex.Pattern:
    """Compile with `regex`, and report a failure without quoting the pattern body.

    The pattern is not corpus text and is committed, so quoting it would be safe here —
    but `rule_author.md` §2 allows cue words in patterns, and an author debugging a
    rule against a real note is one paste away from a pattern that holds one. The rule id
    identifies the rule uniquely, which is all a reader needs to go and look.
    """
    try:
        return regex.compile(pattern, flags)
    except regex.error as exc:
        raise RuleError(
            f"{rule_id}: the {what} does not compile ({exc.__class__.__name__}: "
            f"{exc.msg if hasattr(exc, 'msg') else 'invalid pattern'} at position "
            f"{getattr(exc, 'pos', '?')}). The pattern body is not quoted here; the "
            "rule id identifies it. Dialect is the `regex` module, so \\p{Lu} and "
            "\\p{L} are available (rule_author.md §2)."
        ) from exc


def _bounded(term: str) -> str:
    """One escaped term with the boundary assertions its own edges call for.

    Not `\\b`: a `\\b` requires a word character on the inside of the boundary, so a term
    ending in punctuation — `C.S. (Norte)`, an ordinary institution name — can never
    match at all. The rule loads, compiles, fires nowhere, and reads as a name that does
    not occur in the corpus. That is the exact failure mode a gazetteer layer must not
    have, since "matched nothing" is also what DESIGN §7 reports as a negative result.

    So each edge is asserted only where asserting it means something: a term that begins
    with a word character must not begin mid-word, and one that begins with `(` has
    nothing to be inside of. `(?<!\\w)`/`(?!\\w)` rather than `\\b` for the same reason —
    they say "not continuing a word", which is the actual requirement.
    """
    escaped = regex.escape(term)
    left = r"(?<!\w)" if term[:1].isalnum() or term[:1] == "_" else ""
    right = r"(?!\w)" if term[-1:].isalnum() or term[-1:] == "_" else ""
    return f"{left}{escaped}{right}"


def _terms_pattern(terms) -> str:
    """A gazetteer's term list as one alternation, longest first.

    Longest first because `regex`'s alternation is first-match, not longest-match: with
    `Hospital` before `Hospital Clinic`, the shorter always wins and the longer term is
    unreachable — a silent failure, since the rule still fires and the span is just
    short. Sorting by length here means a term list needs no ordering discipline from
    whoever writes it, which is the point of the layer.

    Case folding is the layer's declared property in naming.yaml ("dictionary membership
    under case folding"), so it is the default and `case_sensitive: true` opts out.
    """
    ordered = sorted({t for t in terms if t}, key=lambda t: (-len(t), t))
    return "(?:" + "|".join(_bounded(t) for t in ordered) + ")"


def _cue_pattern(cue, then: str, gap: int) -> str:
    """`cue` words followed by `then`, with the match group on `then` alone.

    A lookbehind would be the direct translation and `regex` supports variable-length
    ones, but writing it by hand is what this form exists to avoid: the author supplies
    two halves in reading order and the engine assembles them.

    The group is on `then`, so the emitted span covers the identifier and not the cue.
    A rule that swallowed `Dr. ` into the span would be scored against gold that starts
    at the name, and would lose the assignment under `fully_covered` while looking like a
    hit under `relaxed` — the kind of boundary error that reads as a scoring artefact.
    """
    cues = "|".join(regex.escape(c) for c in sorted(set(cue), key=lambda c: (-len(c), c)))
    return rf"(?:{cues})[\s:.,]{{0,{gap}}}({then})"


#: The `then` shorthands, so the common `context_cue` rule needs no regex. Each is a
#: named piece of language-neutral structure — "a capitalised word", "some digits" —
#: which is what a cue rule's right-hand side almost always is.
THEN_SHORTHAND = {
    "capitalised_word": r"\p{Lu}\p{L}+",
    "capitalised_words": r"\p{Lu}\p{L}+(?:[\s\-]\p{Lu}\p{L}+){0,3}",
    "number": r"\d+",
    "digits": r"[\d.\-/]{2,}",
    "word": r"\p{L}+",
    "rest_of_line": r"[^\n]+",
}


def _rule_from(raw: dict, lang: str, source: str) -> Rule:
    """Validate one rule mapping and compile it. Every field checked, nothing inferred.

    The checks are here rather than at first use because a rule file is written by an
    agent or by a person mid-iteration, and a rule that loads and then behaves
    unexpectedly costs a scoring round to notice. A rule that refuses to load costs a
    line of output.
    """
    if not isinstance(raw, dict):
        raise RuleError(f"{source}: a rule must be a mapping, got "
                        f"{type(raw).__name__}")
    rid = raw.get("rule_id")
    if not isinstance(rid, str) or not rid:
        raise RuleError(f"{source}: a rule has no rule_id (required: rule_id, layer, "
                        "phi_type, and one matcher form)")
    if ":" in rid:
        raise RuleError(
            f"{source}: rule_id {rid!r} carries a prefix, but the loader prepends the "
            f"language of the file it read ({lang}:). Writing it here produces "
            f"{lang}:{rid} (rule_author.md §2)."
        )
    prefixed = f"{lang}:{rid}"

    layer = raw.get("layer")
    if layer not in rule_layers():
        raise RuleError(
            f"{prefixed}: layer must be one of {sorted(rule_layers())} — a rules-family "
            "layer from config/naming.yaml's layer axis. It is declared per rule and "
            "never inferred from the rule's name or its matcher (DESIGN §3), and "
            f"{layer!r} is not one."
        )
    phi_type = raw.get("phi_type")
    if phi_type not in axis("phi_type"):
        raise RuleError(
            f"{prefixed}: phi_type {phi_type!r} is not in config/naming.yaml's phi_type "
            f"axis (have: {sorted(axis('phi_type'))})."
        )
    from .sample import non_target_types
    if phi_type in non_target_types():
        raise RuleError(
            f"{prefixed}: no rule may target {phi_type!r} — naming.yaml declares it as "
            "not a rule-development target and rule_author.md's Prohibition 4 forbids "
            "it. A residual bucket a corpus ships is not a phenomenon."
        )

    flags = regex.UNICODE
    for name in raw.get("flags", []) or []:
        if name not in FLAGS:
            raise RuleError(
                f"{prefixed}: {name!r} is not an allowed flag (have: "
                f"{sorted(FLAGS)}). The list is an allowlist because a flag changes what "
                "a pattern means globally — DOTALL would let `.` cross a note's line "
                "boundaries and silently widen every rule in the file."
            )
        flags |= FLAGS[name]

    forms = [k for k in ("pattern", "terms", "lexicon", "cue") if raw.get(k)]
    if len(forms) != 1:
        raise RuleError(
            f"{prefixed}: exactly one matcher form per rule, got {forms or 'none'}. "
            "The forms are pattern: (regex), terms:/lexicon: (gazetteer), and cue: + "
            "then: (context cue). Two forms in one rule would leave which one fired "
            "unrecorded, and the by_rule block attributes to a rule and not to a form."
        )
    form = forms[0]
    group = 0
    checksum = raw.get("checksum")

    if form == "pattern":
        body = raw["pattern"]
        if not isinstance(body, str):
            raise RuleError(f"{prefixed}: pattern must be a string")
        matcher = _compile(body, flags, prefixed, "pattern")
    elif form in ("terms", "lexicon"):
        terms = (raw["terms"] if form == "terms"
                 else _read_lexicon(raw["lexicon"], prefixed))
        if not isinstance(terms, list) or not all(isinstance(t, str) and t
                                                  for t in terms):
            raise RuleError(f"{prefixed}: terms must be a list of non-empty strings")
        if not raw.get("case_sensitive"):
            flags |= regex.IGNORECASE
        matcher = _compile(_terms_pattern(terms), flags, prefixed, "terms")
    else:
        cue = raw["cue"]
        if isinstance(cue, str):
            cue = [cue]
        if not isinstance(cue, list) or not all(isinstance(c, str) and c for c in cue):
            raise RuleError(f"{prefixed}: cue must be a string or a list of strings")
        then = raw.get("then")
        if not isinstance(then, str) or not then:
            raise RuleError(
                f"{prefixed}: a cue rule needs `then:` — what follows the cue. Either a "
                f"shorthand ({sorted(THEN_SHORTHAND)}) or a regex. Without it the rule "
                "would match the cue words themselves, and the cue is the evidence, not "
                "the identifier."
            )
        then_body = THEN_SHORTHAND.get(then, then)
        gap = raw.get("gap", 3)
        if not isinstance(gap, int) or isinstance(gap, bool) or not 0 <= gap <= 40:
            raise RuleError(f"{prefixed}: gap must be an integer in 0..40, got {gap!r}")
        matcher = _compile(_cue_pattern(cue, then_body, gap), flags,
                           prefixed, "cue/then pattern")
        group = 1

    if checksum is not None:
        if layer != "regex_checksum":
            raise RuleError(
                f"{prefixed}: checksum is only meaningful on a regex_checksum rule, not "
                f"on {layer!r}. The layer names the mechanism a span came from, so a "
                "check-digit validation declared under another layer would attribute "
                "DESIGN §7's per-layer results to the wrong mechanism."
            )
        if checksum not in CHECKSUMS:
            raise RuleError(
                f"{prefixed}: {checksum!r} is not an implemented checksum (have: "
                f"{sorted(CHECKSUMS)}). A rule names the algorithm and this module holds "
                "the arithmetic — expressing a check digit in YAML would be a small "
                "programming language with no tests."
            )

    score = raw.get("score")
    if score is not None and not (isinstance(score, (int, float))
                                  and not isinstance(score, bool)
                                  and 0.0 <= float(score) <= 1.0):
        raise RuleError(f"{prefixed}: score must be a number in 0..1, got {score!r}")

    return Rule(rule_id=prefixed, layer=layer, phi_type=phi_type, matcher=matcher,
                group=group, checksum=checksum,
                score=float(score) if score is not None else None, source=source)


def _read_lexicon(ref: str, rule_id: str) -> list[str]:
    """A term list from `lexicons/{lang}/{name}.txt`. One term per line, `#` comments.

    A plain text file rather than YAML, because this is the LexiconBuilder's artifact
    (DESIGN §3) and it is a list — a format with no syntax is a format an author cannot
    get wrong, and the file may be long.
    """
    if not isinstance(ref, str) or "/" not in ref:
        raise RuleError(
            f"{rule_id}: lexicon must be '{{lang}}/{{name}}', e.g. 'es/institutions' — "
            f"got {ref!r}. The lang component is the lexicon's own language and need not "
            "equal the rule file's."
        )
    lang, _, name = ref.partition("/")
    if lang not in axis("lang"):
        raise RuleError(f"{rule_id}: {lang!r} is not a lang in config/naming.yaml")
    if not regex.fullmatch(r"[a-z0-9_]+", name):
        raise RuleError(
            f"{rule_id}: lexicon name must be [a-z0-9_]+, got {name!r}. A path "
            "component from a rule file is attacker-adjacent input in the sense that "
            "matters here: `../../sealed/es-meddocan/test` is a valid-looking name."
        )
    path = ROOT / path_template("lexicon").format(lang=lang) / f"{name}.txt"
    if not path.exists():
        raise RuleError(
            f"{rule_id}: no lexicon at {path.relative_to(ROOT)}. A gazetteer rule "
            "naming a list that does not exist would silently match nothing, which "
            "reads as a rule that does not generalise."
        )
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    if not terms:
        raise RuleError(f"{rule_id}: the lexicon at {path.relative_to(ROOT)} is empty")
    return terms


def load_rules(lang: str, *, path: Path | None = None) -> RuleSet:
    """One `rules/{lang}.yaml`, validated and compiled.

    `path` overrides the location for a practice or test file, and is the reason a
    rehearsal never touches `rules/es.yaml` — the practice file lives outside the
    repository and iteration 1 starts from zero rules
    (`docs/notes/port-human-practice.md`).
    """
    if lang not in axis("lang"):
        raise RuleError(
            f"{lang!r} is not a lang in config/naming.yaml (have: "
            f"{sorted(axis('lang'))}). {{lang}} is the language of the rule *file*, not "
            "of the corpus (DESIGN §5.2)."
        )
    p = path or ROOT / path_template("rules").format(lang=lang)
    if not Path(p).exists():
        return RuleSet()
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise RuleError(f"{p}: the file must be a mapping with 'version', 'lang' and "
                        "'rules' keys")
    declared = raw.get("lang")
    if declared != lang:
        raise RuleError(
            f"{p}: the file declares lang {declared!r} but was loaded as {lang!r}. The "
            "declaration is checked rather than trusted because the rule_id prefix comes "
            "from the file's language, and a mismatch would attribute every span to the "
            "wrong file (DESIGN §5.2)."
        )
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RuleError(f"{p}: 'version' must be an integer >= 1, got {version!r}. It is "
                        "recorded with the results (rule_author.md §2).")
    rules = [_rule_from(r, lang, str(p)) for r in raw.get("rules") or []]
    seen = set()
    for r in rules:
        if r.rule_id in seen:
            raise RuleError(
                f"{r.rule_id}: duplicate rule_id in one file. Ids must be unique and "
                "stable across iterations — the by_rule counts and the rule_id on every "
                "span are the same identifier, so a duplicate merges two rules' "
                "attribution into one bucket (rule_author.md §2)."
            )
        seen.add(r.rule_id)
    return RuleSet(rules=rules, versions={lang: version})


def load_for_corpus(corpus: str, *, paths: dict[str, Path] | None = None) -> RuleSet:
    """Every rule file `corpus` loads, per `corpus_rule_langs` (DESIGN §5.2).

    All of them, unioned. No per-document language selection: a selector's own error
    rate is not measured by any metric in this project, so its mistakes would be
    reported inside detection performance (DESIGN §5.2, naming.yaml).
    """
    combined = RuleSet()
    for lang in rule_langs(corpus):
        part = load_rules(lang, path=(paths or {}).get(lang))
        combined.rules.extend(part.rules)
        combined.versions.update(part.versions)
    return combined
