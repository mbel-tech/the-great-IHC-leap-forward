"""Building a filename pattern from marked-up spans of one real filename.

Kept apart from the Qt widget for the same reason `slides.py` is: the part with
the judgement in it must be testable without opening a window.

The operator marks which run of characters is the subject, which is the slide,
which is the replicate. Everything they did NOT mark is escaped literally, so a
generated pattern can never be looser than the example it came from - the text
the user typed is never used as regex syntax.

Two things this file exists to prevent.

**A pattern that matches the example and nothing else.** A generated pattern is
always previewed against every filename that was scanned, and the count of what
did not match is part of the result rather than something to notice later.

**A pattern with no defined split.** `(?P<a>[A-Za-z0-9]+)(?P<b>[A-Za-z0-9]+)`
has no answer to where a stops and b begins - the regex engine picks one, and
whichever it picks is not a decision anyone made. Adjacent spans of the same
character class are detected and the left one is pinned to the width it has in
the example, with a sentence saying so.
"""

import re

#: Group names the pipeline gives a meaning to. Anything else the operator adds
#: rides along as an extra column.
SUBJECT, SLIDE, REPLICATE = "subject", "slide", "replicate"
ROLES = (SUBJECT, SLIDE, REPLICATE)


class Span:
    """One marked run of the example filename."""

    def __init__(self, start, end, name, optional=False):
        self.start = int(start)
        self.end = int(end)
        self.name = str(name)
        self.optional = bool(optional)

    def text(self, example):
        return example[self.start:self.end]

    def __repr__(self):
        return f"<Span {self.name} {self.start}:{self.end}>"


def _kinds(text):
    """Which character families a run is made of."""
    out = set()
    for ch in text:
        if ch.isdigit():
            out.add("digit")
        elif ch.isascii() and ch.isalpha():
            out.add("alpha")
        else:
            out.add("other")
    return out


def infer_body(text):
    """A regex body for a marked run, inferred from the run itself.

    Never from anything the user typed: the shape comes from the characters
    that are actually there, so the pattern cannot be wider than the example.
    """
    if not text:
        return ""
    kinds = _kinds(text)
    if kinds == {"digit"}:
        return r"\d+"
    if kinds == {"alpha"}:
        return r"[A-Za-z]+"
    if kinds == {"alpha", "digit"} and re.fullmatch(r"[A-Za-z]+\d+", text):
        return r"[A-Za-z]+\d+"
    # Anything else: the literal set of characters present, which is narrow and
    # predictable even when it is not pretty.
    chars = "".join(sorted(set(text)))
    return "[" + re.escape(chars).replace("]", r"\]").replace("^", r"\^") + "]+"


def _pin(body, width):
    """The same body, fixed to `width` characters instead of one-or-more."""
    if body.endswith("+"):
        return f"{body[:-1]}{{{width}}}"
    return body


def _open_kinds(body):
    """What a body can consume, for the adjacency check."""
    kinds = set()
    if r"\d" in body:
        kinds.add("digit")
    if "A-Za-z" in body:
        kinds.add("alpha")
    if body.startswith("[") and not kinds:
        kinds.add("other")
    return kinds


def spans_to_pattern(example, spans, ignore_case=False):
    """(pattern, warnings) for the marked-up example.

    Spans must not overlap. Everything outside them is escaped literally,
    including the extension, so the pattern is exactly as specific as the
    example was.
    """
    spans = sorted(spans, key=lambda s: s.start)
    for a, b in zip(spans, spans[1:]):
        if b.start < a.end:
            raise ValueError(f"{a.name} and {b.name} overlap")
    if not any(s.name == SUBJECT for s in spans):
        raise ValueError("one span must be the subject")

    bodies = [infer_body(s.text(example)) for s in spans]
    warnings = []

    # Adjacent spans of the same family have no defined split, so pin the left
    # one to the width it has here and say why.
    for i, (a, b) in enumerate(zip(spans, spans[1:])):
        if a.end != b.start:
            continue
        if _open_kinds(bodies[i]) & _open_kinds(bodies[i + 1]):
            width = a.end - a.start
            bodies[i] = _pin(bodies[i], width)
            warnings.append(
                f"{a.name} is fixed at {width} character"
                f"{'s' if width != 1 else ''} here, because nothing separates "
                f"it from {b.name}. Leave a character unmarked between them to "
                f"lift that.")

    out, cursor = ["^"], 0
    for span, body in zip(spans, bodies):
        out.append(re.escape(example[cursor:span.start]))
        group = f"(?P<{span.name}>{body})"
        out.append(f"(?:{group})?" if span.optional else group)
        cursor = span.end
    out.append(re.escape(example[cursor:]))
    out.append("$")
    pattern = "".join(out)

    try:
        re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:                                 # pragma: no cover
        raise ValueError(f"the generated pattern is not valid: {exc}")
    return pattern, warnings


def preview(names, pattern, ignore_case=False):
    """How a pattern reads a whole list of filenames.

    Returns rows (failures first, because those are what need looking at),
    counts, and the problems that must be fixed before it can be saved.
    """
    result = {"rows": [], "matched": 0, "failed": 0, "problems": [],
              "pattern": pattern}
    try:
        compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        result["problems"].append(f"the pattern is not valid: {exc}")
        return result

    if SUBJECT not in compiled.groupindex:
        result["problems"].append(
            "the pattern has no (?P<subject>...) group. That group is the key "
            "every later table joins on, so it is not optional.")
        return result

    extras = [n for n in compiled.groupindex if n not in ROLES]
    seen = {}
    for name in names:
        m = compiled.match(name)
        if not m:
            result["rows"].append({"file": name, "ok": False, "why": "no match"})
            result["failed"] += 1
            continue
        g = m.groupdict()
        subject = g.get(SUBJECT) or ""
        row = {"file": name, "ok": True, SUBJECT: subject,
               SLIDE: g.get(SLIDE) or "", REPLICATE: g.get(REPLICATE) or "",
               "extras": {k: (g.get(k) or "") for k in extras}}
        if not subject:
            row["ok"], row["why"] = False, "subject is empty"
            result["failed"] += 1
        elif "_" in subject:
            row["ok"], row["why"] = False, "subject contains '_'"
            result["failed"] += 1
        else:
            result["matched"] += 1
            # Extra columns (a name suffix such as `-_A568`) tell two files
            # apart too: the manifest carries them, and a paired study keeps a
            # rescan of one marker beside the other marker's file this way.
            key = (subject, row[SLIDE], row[REPLICATE],
                   tuple(sorted(row["extras"].items())))
            seen.setdefault(key, []).append(name)
        result["rows"].append(row)

    result["rows"].sort(key=lambda r: (r["ok"], r["file"]))

    if not names:
        result["problems"].append("no files to check the pattern against.")
    elif result["matched"] == 0:
        result["problems"].append(
            "the pattern matches none of these files.")
    if any(r.get("why") == "subject contains '_'" for r in result["rows"]):
        result["problems"].append(
            "the subject captures an underscore. The scene uid separates on "
            "'_', and several stages take the subject back off a uid, so one "
            "there would hand them half an id.")

    # Two files reading as the same section is not a naming style, it is a
    # collision: every measurement from one would overwrite the other.
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    if clashes:
        first = next(iter(clashes.values()))
        result["problems"].append(
            f"{len(clashes)} group(s) of files read as the same section, "
            f"e.g. {', '.join(first[:3])}. Mark another part of the name so "
            f"they differ.")
    result["clashes"] = clashes
    return result


def can_save(result, accept_failures=False):
    """(bool, reason). A pattern is savable only when nothing is invisible."""
    if result["problems"]:
        return False, result["problems"][0]
    if result["failed"] and not accept_failures:
        return False, (f"{result['failed']} file(s) do not match. A file the "
                       f"pattern misses never reaches any stage and nothing "
                       f"says so - fix the pattern, or tick the box to leave "
                       f"them out deliberately.")
    return True, ""


def summary(result):
    total = result["matched"] + result["failed"]
    if not total:
        return "nothing scanned"
    if not result["failed"]:
        return f"all {total} filenames matched"
    return f"{result['matched']} of {total} matched — {result['failed']} did not"


# ---------------------------------------------------------------------------
# Guided: from one typed example to a pattern for every file
# ---------------------------------------------------------------------------
#
# The operator types (or picks) one filename and says which piece is the
# animal, which the slide number and which the replicate letter - or accepts
# the program's reading of it. The program then writes the pattern and checks
# it against every file. Where files do not match, it works down a fixed list
# of known causes (the fault tree), each tried only if it rescues files without
# making two of them read as the same section, and says in plain words what it
# changed and how many files that brought in. What is still unmatched is listed
# with the most likely reason.

#: Characters that separate the parts of a filename.
SEPARATORS = "_-. "


def split_ext(name):
    """("LS53_2b-_A568", ".czi"): the extension is the last dot onward."""
    dot = name.rfind(".")
    return (name, "") if dot <= 0 else (name[:dot], name[dot:])


def tokens(example):
    """[(start, end, text)] - the clickable pieces of the name, extension left out.

    Runs of letters, runs of digits and single separators are pieces, so a
    person can point at `LS`, `53`, `_`, `2` and `b` separately.
    """
    stem, _ext = split_ext(example)
    out, i = [], 0
    while i < len(stem):
        ch = stem[i]
        if ch.isdigit():
            kind = str.isdigit
        elif ch.isascii() and ch.isalpha():
            kind = lambda c: c.isascii() and c.isalpha()       # noqa: E731
        else:
            out.append((i, i + 1, ch))
            i += 1
            continue
        j = i
        while j < len(stem) and kind(stem[j]):
            j += 1
        out.append((i, j, stem[i:j]))
        i = j
    return out


def guess_spans(example):
    """The program's reading of a name: [Span] for subject, slide and replicate.

    The subject is the first word (separator to separator), or its letters plus
    digits when the word goes on; the slide is the next run of digits; a single
    letter straight after the slide is the replicate. `LS53_2b-_A568.czi` reads
    as LS53 / 2 / b.
    """
    toks = [t for t in tokens(example)]
    if not toks:
        return []
    spans = []
    # Subject: from the first letter-or-digit piece to the end of its word, or to
    # the end of letters+digits if the word carries on (LS53b would be LS53).
    words = [t for t in toks if t[2] not in SEPARATORS]
    if not words:
        return []
    first = toks.index(words[0])
    end_i = first
    if toks[first][2].isalpha() and first + 1 < len(toks) and toks[first + 1][2].isdigit():
        end_i = first + 1
    spans.append(Span(toks[first][0], toks[end_i][1], SUBJECT))
    rest = toks[end_i + 1:]
    slide_i = next((k for k, t in enumerate(rest) if t[2].isdigit()), None)
    if slide_i is None:
        return spans
    s = rest[slide_i]
    spans.append(Span(s[0], s[1], SLIDE))
    if slide_i + 1 < len(rest):
        r = rest[slide_i + 1]
        if r[2].isalpha() and len(r[2]) == 1:
            spans.append(Span(r[0], r[1], REPLICATE))
    return spans


#: The fault tree, in the order it is worked through: (option, plain words).
FIXES = (
    ("ignore_case", "Upper and lower case are treated the same (some files differ only in case)."),
    ("free_tail", "Text after the last marked part is allowed to differ (e.g. '-' or '_A568'); "
                  "it is kept as an extra column."),
    ("optional_replicate", "The replicate letter may be missing."),
    ("loose_separators", "Any of _ - . or a space may separate the parts."),
    ("loose_subject", "The animal ID may be any letters and digits."),
    ("free_head", "Text before the animal ID is allowed to differ."),
)


def _body(span, example, opts):
    if span.name == SUBJECT and opts.get("loose_subject"):
        return "[A-Za-z0-9]+"
    if span.name == REPLICATE:
        text = span.text(example)
        return "[A-Za-z]" if len(text) == 1 else infer_body(text)
    return infer_body(span.text(example))


def build_pattern(example, spans, opts=None):
    """The pattern for `example` marked up by `spans`, loosened as `opts` says.

    With no options it is as strict as the example (separators and extension
    are literal); each option is one of FIXES.
    """
    opts = opts or {}
    spans = sorted(spans, key=lambda s: s.start)
    for a, b in zip(spans, spans[1:]):
        if b.start < a.end:
            raise ValueError(f"{a.name} and {b.name} overlap")
    if not any(s.name == SUBJECT for s in spans):
        raise ValueError("one part must be the animal ID")
    stem, ext = split_ext(example)
    for s in spans:
        if s.end > len(stem):
            raise ValueError("the extension cannot be marked")
    bodies = [_body(s, example, opts) for s in spans]
    for i, (a, b) in enumerate(zip(spans, spans[1:])):
        if a.end == b.start and _open_kinds(bodies[i]) & _open_kinds(bodies[i + 1]):
            bodies[i] = _pin(bodies[i], a.end - a.start)

    def literal(text):
        if opts.get("loose_separators") and text and all(c in SEPARATORS for c in text):
            return "[-_. ]+"
        return re.escape(text)

    out = ["^"]
    head = stem[:spans[0].start]
    if opts.get("free_head"):
        out.append("(?P<prefix>.*?)")
    else:
        out.append(re.escape(head))
    cursor = spans[0].start
    for i, (span, body) in enumerate(zip(spans, bodies)):
        if i:
            out.append(literal(stem[cursor:span.start]))
        group = f"(?P<{span.name}>{body})"
        if span.name == REPLICATE and opts.get("optional_replicate"):
            group = f"{group}?" if body == "[A-Za-z]" else f"(?:{group})?"
        out.append(group)
        cursor = span.end
    tail = stem[cursor:]
    if opts.get("free_tail"):
        # Empty, or starting with something that is not a letter or digit, so it
        # cannot swallow the end of the part before it.
        out.append(r"(?P<suffix>[^A-Za-z0-9].*?)?")
    else:
        out.append(re.escape(tail))
    out.append(re.escape(ext) + "$")
    return "".join(out)


def _score(result):
    return result["matched"] - (1000 if result.get("clashes") else 0)


def generalise(example, spans, names, extra_fixes=True):
    """Work the fault tree: {pattern, ignore_case, steps, result, opts}.

    Starts strict, then tries each fix in FIXES on its own; a fix is kept when
    it brings in more files without making two files read as the same section.
    Repeats until nothing more helps. `steps` says what was changed, in plain
    words, with how many files each change brought in.
    """
    opts = {}
    pattern = build_pattern(example, spans, opts)
    result = preview(names, pattern, False)
    steps = []
    improved = True
    while improved and result["failed"] and extra_fixes:
        improved = False
        for name, words in FIXES:
            if opts.get(name):
                continue
            if name == "optional_replicate" and not any(s.name == REPLICATE for s in spans):
                continue
            trial = dict(opts, **{name: True})
            try:
                pat = build_pattern(example, spans, trial)
            except ValueError:
                continue
            res = preview(names, pat, bool(trial.get("ignore_case")))
            if res["problems"] and not result["problems"]:
                continue
            if _score(res) > _score(result):
                gained = res["matched"] - result["matched"]
                steps.append(f"{words} Brought in {gained} file{'s' if gained != 1 else ''}.")
                opts, pattern, result = trial, pat, res
                improved = True
                break
    return {"pattern": pattern, "ignore_case": bool(opts.get("ignore_case")),
            "steps": steps, "result": result, "opts": opts}


def why_not(name, example):
    """The most likely reason one file does not fit, in plain words."""
    stem, ext = split_ext(name)
    _s, ex_ext = split_ext(example)
    if ext.lower() != ex_ext.lower():
        return f"it is not a {ex_ext or 'slide'} file" if ext else "it has no extension"
    if not re.match(r"[A-Za-z0-9]", stem):
        return "it does not start with an animal ID"
    shape = re.sub(r"\d+", "9", re.sub(r"[A-Za-z]+", "a", stem))
    ex_shape = re.sub(r"\d+", "9", re.sub(r"[A-Za-z]+", "a", split_ext(example)[0]))
    if shape.count("9") < ex_shape.count("9"):
        return "it has fewer numbers than the example (a missing slide number?)"
    return "its parts are in a different order or shape from the example"


def readout(example, spans):
    """'animal LS53 · slide 2 · replicate b' - the reading, in words."""
    words = {SUBJECT: "animal", SLIDE: "slide", REPLICATE: "replicate"}
    return " · ".join(f"{words.get(s.name, s.name)} {s.text(example)}"
                      for s in sorted(spans, key=lambda s: s.start))


def shape_of(name):
    stem, ext = split_ext(name)
    return re.sub(r"\d+", "9", re.sub(r"[A-Za-z]+", "a", stem)) + ext.lower()


def auto(names, limit=12):
    """The program's best pattern for a list of filenames, with no one asked.

    Tries an example of each of the commonest name shapes, reads it with
    guess_spans and works the fault tree; the one that matches most files
    (without clashes) wins. Returns generalise()'s dict plus `example` and
    `spans`, or None when there is nothing to read.
    """
    from collections import Counter
    names = [n for n in names if n]
    if not names:
        return None
    shapes = Counter(shape_of(n) for n in names)
    best = None
    for shape, _n in shapes.most_common(limit):
        example = next(n for n in names if shape_of(n) == shape)
        spans = guess_spans(example)
        if not spans:
            continue
        try:
            got = generalise(example, spans, names)
        except ValueError:
            continue
        got.update(example=example, spans=spans)
        if best is None or _score(got["result"]) > _score(best["result"]):
            best = got
    return best
