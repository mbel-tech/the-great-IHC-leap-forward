"""Read how far a running stage has got from the lines it prints.

Two kinds of line count:

  * `@@progress 12/454 sections`, written by scripts/ls_progress.py only when
    the app runs the stage. These are for the bar alone and stay out of the log.

  * The counters most stages already print, such as `\\r  12/454`,
    `measured 12/454  failed 0` or `[12/454] LS53_1a ...`. The first `N/M` on
    the line is taken, provided it looks like a count: whole numbers, N no
    larger than M, and not part of a path, a date or a decimal.

Anything else returns None and the bar stays as it was (busy, if no count has
arrived yet).
"""

import re

MARK = "@@progress "

_COUNT = re.compile(r"(?<![\w./\\-])(\d{1,7})\s?/\s?(\d{1,7})(?![\w./\\])")


class Progress:
    __slots__ = ("done", "total", "what", "hidden")

    def __init__(self, done, total, what="", hidden=False):
        self.done, self.total, self.what, self.hidden = done, total, what, hidden

    def __repr__(self):
        return f"Progress({self.done}/{self.total} {self.what!r}, hidden={self.hidden})"


def parse(line):
    """A Progress for a line that carries a count, else None."""
    if not line:
        return None
    text = line.strip()
    if text.startswith(MARK):
        m = _COUNT.match(text[len(MARK):])
        if not m:
            return None
        done, total = int(m.group(1)), int(m.group(2))
        what = text[len(MARK) + m.end():].strip()
        if total <= 0:
            return None
        return Progress(min(done, total), total, what, hidden=True)
    m = _COUNT.search(text)
    if not m:
        return None
    done, total = int(m.group(1)), int(m.group(2))
    if total <= 0 or done > total:
        return None
    return Progress(done, total)


def words(p):
    """`12 of 454 sections (3%)` for the bar's label."""
    pct = int(100 * p.done / p.total) if p.total else 0
    what = f" {p.what}" if p.what else ""
    return f"{p.done:,} of {p.total:,}{what} ({pct}%)"
