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

print()
print(f"{len(failures)} failure(s)" if failures else "all passed")
sys.exit(1 if failures else 0)
