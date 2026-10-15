"""What the desktop app reads as a stage's progress.

The progress bar is fed from the lines a stage prints, so the parser has to
take the counters the stages really print and leave alone everything that only
looks like a count: paths, dates, decimals, a ratio over its total.

Run:  python tests/test_progress.py
"""

import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import progress as PR                                       # noqa: E402
import ls_progress as PG                                    # noqa: E402

failures = []


def chk(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:60} {got!r}")
    if not ok:
        failures.append(name)


def count(line):
    p = PR.parse(line)
    return None if p is None else (p.done, p.total, p.hidden)


# ---------------------------------------------------------------- counters the stages print
chk("bare counter (00d, 04g, 04j)", count("  12/454"), (12, 454, False))
chk("worded counter (04f)", count("  measured 3/40"), (3, 40, False))
chk("bracketed counter (01h, 04e)", count("  [7/120] LS53_1a -> P12  IoU 0.812"), (7, 120, False))
chk("printf counter (01k)", count("  measured 5/90  failed 0  (1.2/s, 0.3 min)"), (5, 90, False))
chk("05c counter", count("  4/300  LS53_1a_01  812 nuclei"), (4, 300, False))
chk("complete", count("sections reformatted: 40/40"), (40, 40, False))

# ---------------------------------------------------------------- things that are not counts
chk("path is not a count", count(r"wrote D:/LS-analysis/2026/10 done"), None)
chk("windows path is not a count", count(r"D:\LS-analysis\12/14\x"), None)
chk("decimal is not a count", count("rate 1.5/2.0 per s"), None)
chk("done over total is not a count", count("  5/3"), None)
chk("zero total is not a count", count("  0/0"), None)
chk("a rate is not a count", count("(0.42/s, 3.1 min)"), None)
chk("plain text", count("Discovered 12 CZI entries"), None)
chk("empty line", count(""), None)

# ---------------------------------------------------------------- the explicit protocol
p = PR.parse("@@progress 12/454 sections")
chk("marked line is hidden from the log", (p.done, p.total, p.what, p.hidden),
    (12, 454, "sections", True))
chk("marked line words", PR.words(p), "12 of 454 sections (2%)")
chk("marked line clamps done to total", count("@@progress 9/5"), (5, 5, True))
chk("malformed mark is not a count", count("@@progress soon"), None)

old = os.environ.pop("LS_PROGRESS", None)
try:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        PG.report(3, 10, "sections")
    chk("ls_progress is silent on the command line", buf.getvalue(), "")
    os.environ["LS_PROGRESS"] = "1"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        PG.report(3, 10, "sections")
        PG.report(1, 0, "nothing")
    chk("ls_progress prints the mark under the app", buf.getvalue(),
        "@@progress 3/10 sections\n")
    chk("and the app reads back what it printed",
        count(buf.getvalue().strip()), (3, 10, True))
finally:
    os.environ.pop("LS_PROGRESS", None)
    if old is not None:
        os.environ["LS_PROGRESS"] = old

# ---------------------------------------------------------------- time left, and the last run
chk("hours and minutes, to the nearest 5 min", PR.left_words(2 * 3600 + 9 * 60), "about 2 h 10 min left")
chk("whole hours", PR.left_words(3 * 3600 + 60), "about 3 h left")
chk("minutes", PR.left_words(4 * 60 + 10), "about 4 min left")
chk("under a minute", PR.left_words(40), "under a minute left")
chk("no estimate, no words", PR.left_words(None), "")
chk("a run's length in hours", PR.duration_words(3 * 3600 + 12 * 60 + 20), "3 h 12 min")
chk("a run's length in minutes", PR.duration_words(65), "1 min")
chk("a run's length under a minute", PR.duration_words(42), "42 s")

t = PR.TimeLeft()
chk("the first count gives no estimate", t.update(0, 100, 1000.0), None)
chk("nor after 10 s, however far it got", t.update(5, 100, 1010.0), None)
chk("nor after a minute at 1 percent", t.update(1, 100, 1060.0) is None, True)
t = PR.TimeLeft()
t.update(0, 100, 0.0)
chk("10 percent in 60 s leaves about 9 min", t.update(10, 100, 60.0), 540.0)
chk("a quick section after it does not halve the estimate",
    round(t.update(12, 100, 62.0)), round(88 * 62 / 12))
t = PR.TimeLeft()
t.update(200, 300, 0.0)
chk("a resumed stage counts its rate from where it resumed",
    t.update(230, 300, 300.0), 700.0)
chk("a new total (the next phase) starts the estimate again",
    t.update(1, 5, 310.0), None)
chk("done means nothing left", t.update(5, 5, 400.0) in (None, 0.0), True)

runs = [{"stage": "detect", "ok": True, "started": "2026-01-02T03:00:00+00:00",
         "ended": "2026-01-02T06:12:00+00:00"},
        {"stage": "detect", "ok": False, "started": "2026-01-03T03:00:00+00:00",
         "ended": "2026-01-03T03:00:09+00:00"},
        {"stage": "other", "ok": True, "started": "2026-01-04T03:00:00+00:00",
         "ended": "2026-01-04T03:00:40+00:00"},
        {"stage": "broken", "ok": True, "started": None, "ended": "2026-01-04T03:01:00+00:00"}]
chk("the last complete run, skipping a failed one", PR.last_took({"runs": runs}, "detect"),
    3 * 3600 + 12 * 60)
chk("a run without its start time is not a length", PR.last_took({"runs": runs}, "broken"), None)
chk("a stage never run has no length", PR.last_took({"runs": runs}, "nope"), None)
chk("no history has no length", PR.last_took(None, "detect"), None)
chk("the row's words", PR.last_run_words({"runs": runs}, "detect"), "Last run took 3 h 12 min")
chk("a run under a minute is not worth a line", PR.last_run_words({"runs": runs}, "other"), "")

print()
print(f"{len(failures)} failure(s)" if failures else "all passed")
sys.exit(1 if failures else 0)
