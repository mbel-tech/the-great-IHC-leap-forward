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

`@@drive lost <folder> | <label>` and `@@drive back <seconds>` (scripts/ls_drive.py)
say the slides drive went and came back. `parse_drive` reads them, wherever on the
line they are - a `\r  12/454` counter has no newline after it, so a marker can
arrive glued to one - and `parse` never takes their numbers for a count.

The time arithmetic is here too, Qt-free so it can be tested without a window:
how long a running stage has left (TimeLeft, left_words) and how long a stage's
last complete run took (last_took, last_run_words).
"""

import re
from datetime import datetime

MARK = "@@progress "
DRIVE_MARK = "@@drive "

_DRIVE = re.compile(r"@@drive (?:lost (?P<folder>.*?) \| (?P<label>.*?)|back (?P<secs>\d+(?:\.\d+)?))\s*$")

_COUNT = re.compile(r"(?<![\w./\\-])(\d{1,7})\s?/\s?(\d{1,7})(?![\w./\\])")


class Progress:
    __slots__ = ("done", "total", "what", "hidden")

    def __init__(self, done, total, what="", hidden=False):
        self.done, self.total, self.what, self.hidden = done, total, what, hidden

    def __repr__(self):
        return f"Progress({self.done}/{self.total} {self.what!r}, hidden={self.hidden})"


def parse(line):
    """A Progress for a line that carries a count, else None."""
    if not line or DRIVE_MARK in line:
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


def parse_drive(line):
    """("lost", folder, label), ("back", seconds) or None for one line."""
    if not line or DRIVE_MARK not in line:
        return None
    m = _DRIVE.search(line)
    if not m:
        return None
    if m.group("secs") is not None:
        return ("back", float(m.group("secs")))
    return ("lost", m.group("folder"), m.group("label"))


def pause_words(pauses):
    """`paused 2x (3 min)` for a run's `pauses`; '' when there were none."""
    if not pauses:
        return ""
    total = sum(float(p.get("seconds") or 0) for p in pauses)
    return f"paused {len(pauses)}x ({duration_words(total)})"


def words(p):
    """`12 of 454 sections (3%)` for the bar's label."""
    pct = int(100 * p.done / p.total) if p.total else 0
    what = f" {p.what}" if p.what else ""
    return f"{p.done:,} of {p.total:,}{what} ({pct}%)"


# An estimate from the first few sections is mostly start-up (loading models,
# opening the first CZI), so none is offered until the stage has been counting
# for this long and has got this far through.
SETTLE_S = 30
SETTLE_FRACTION = 0.03


class TimeLeft:
    """Seconds left for one running stage, from the counts it prints.

    The rate is everything done since the first count over the time since then,
    not the last interval: one quick or slow section moves it a little, never
    halves or doubles it. Counting from the first count rather than the start
    also means a stage that resumes at 200/300 is timed on what it is doing now.
    A new total is a new phase of the stage (or a new stage), so it starts over.
    """

    def __init__(self):
        self._base = None               # (done, total, time) of the first count

    def update(self, done, total, now):
        """Seconds left after this count, or None while there is too little to go on."""
        b = self._base
        if b is None or total != b[1] or done < b[0]:
            self._base = (done, total, now)
            return None
        if done >= total:
            return 0.0
        moved, spent = done - b[0], now - b[2]
        if spent < SETTLE_S or moved <= 0 or moved / total < SETTLE_FRACTION:
            return None
        return (total - done) * spent / moved


def _hours_minutes(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m} min" if m else f"{h} h"


def left_words(seconds):
    """`about 2 h 10 min left`, `about 4 min left`, `under a minute left`; '' for None.

    Over an hour it is to the nearest five minutes: the estimate is not better
    than that, and a figure that changes every count is noise.
    """
    if seconds is None:
        return ""
    if seconds < 60:
        return "under a minute left"
    m = round(seconds / 60)
    if m < 60:
        return f"about {m} min left"
    return f"about {_hours_minutes(5 * round(seconds / 300))} left"


def duration_words(seconds):
    """`3 h 12 min`, `4 min`, `42 s`: how long something took."""
    if seconds < 60:
        return f"{int(round(seconds))} s"
    m = round(seconds / 60)
    return f"{m} min" if m < 60 else _hours_minutes(m)


def _stamp(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def last_took(block, sid):
    """Seconds the newest complete (ok) run of `sid` took, from the project's run
    history (ls_project's `project.runs`); None if there is none to go on."""
    for r in reversed((block or {}).get("runs") or []):
        if r.get("stage") != sid or not r.get("ok"):
            continue
        a, b = _stamp(r.get("started")), _stamp(r.get("ended"))
        if a is None or b is None or (a.tzinfo is None) != (b.tzinfo is None):
            return None
        return max(0.0, (b - a).total_seconds())
    return None


def last_run_words(block, sid):
    """`Last run took 3 h 12 min` for a stage's row; '' when it took under a minute,
    where the time is not worth a line, or there is no complete run."""
    s = last_took(block, sid)
    return f"Last run took {duration_words(s)}" if s is not None and s >= 60 else ""
