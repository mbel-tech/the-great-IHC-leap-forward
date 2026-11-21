"""Reader stages in a supervised child process (app/supervise.py).

A native libCZI crash while the slides drive drops would take the window with
it if the stage ran in the app's own process. So the stages that read CZI
pixels run in a child, and the app supervises it: a child that dies abnormally
while a watched folder is gone is started again once the folder is back; with
the drive there, the same death is an ordinary failure. The child's
`@@drive lost/back` lines become the run's `pauses`.

The child here is a stand-in (`python -c ...`); the real one is
`python -m app --stage <id> <study.json> --child`. Qt-free.

    python tests/test_supervise.py
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import write_study                                # noqa: E402

TMP = tempfile.mkdtemp(prefix="lssup_")
PATH = write_study(os.path.join(TMP, "s"))
os.environ["LS_CONFIG"] = PATH

import ls_project as LP                                         # noqa: E402
import progress as PR                                           # noqa: E402
import runner as RN                                             # noqa: E402
import supervise as SV                                          # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<62} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


DRIVE = os.path.join(TMP, "drive")
GONE = os.path.join(TMP, "drive_gone")
COUNT = os.path.join(TMP, "count")
STOP = os.path.join(TMP, "stop")
os.makedirs(DRIVE)
os.environ["LS_WATCH"] = DRIVE
POLL = 0.05


def drop():
    os.rename(DRIVE, GONE)


def restore():
    os.rename(GONE, DRIVE)


def later(delay, fn):
    t = threading.Timer(delay, fn)
    t.start()
    return t


def reset():
    for p in (COUNT, STOP):
        if os.path.exists(p):
            os.remove(p)
    if os.path.isdir(GONE):
        restore()


# The stand-in child: counts its launches in COUNT, then runs `first` on the
# first launch and `rest` on every later one (python statements).
CHILD = r"""
import os, sys, time
c = sys.argv[1]
n = int(open(c).read()) + 1 if os.path.exists(c) else 1
open(c, "w").write(str(n))
assert os.environ.get("LS_CHILD") == "1" and os.environ.get("LS_PROGRESS") == "1"
assert os.environ.get("LS_STOP_FILE")
def say(s, end="\n"):
    sys.stdout.write(s + end); sys.stdout.flush()
exec(sys.argv[2] if n == 1 else sys.argv[3])
"""


def child(first, rest="say('second launch'); sys.exit(0)"):
    return [sys.executable, "-c", CHILD, COUNT, first, rest]


def launches():
    return int(open(COUNT).read()) if os.path.exists(COUNT) else 0


def run(argv, **kw):
    lines = []
    kw.setdefault("poll", POLL)
    rc, pauses, tail = SV.run_child(argv, lines.append, STOP, **kw)
    return rc, pauses, tail, lines


print("--- progress: the @@drive markers ---")
chk("lost", PR.parse_drive("@@drive lost E:\\Slides | LS53_1a scene 3"),
    ("lost", "E:\\Slides", "LS53_1a scene 3"))
chk("back, seconds a float", PR.parse_drive("@@drive back 134.2"), ("back", 134.2))
chk("glued after a \\r counter fragment",
    PR.parse_drive("  12/454@@drive lost /mnt/x | slide 7"), ("lost", "/mnt/x", "slide 7"))
chk("glued back", PR.parse_drive("measured 3/9@@drive back 2.5"), ("back", 2.5))
chk("anything else is not a marker", PR.parse_drive("  12/454"), None)
chk("a malformed marker is not one", PR.parse_drive("@@drive sideways"), None)
chk("parse ignores a marker line", PR.parse("@@drive lost /x | slide 3/10"), None)
chk("...and a marker glued to a count", PR.parse("  12/454@@drive back 1.0"), None)
chk("parse still reads counts", PR.parse("  12/454").done, 12)
chk("DRIVE_MARK", PR.DRIVE_MARK, "@@drive ")
chk("pause words: none", PR.pause_words([]), "")
chk("pause words: two", PR.pause_words([{"seconds": 100}, {"seconds": 80}]), "paused 2x (3 min)")
chk("pause words: one short", PR.pause_words([{"seconds": 12.0}]), "paused 1x (12 s)")

print("--- child_argv ---")
chk("python -m app --stage <id> <study> --child --argv-json <list>",
    SV.child_argv("01", "/s/study.json", ["--force", "--n", 3])[1:],
    ["-m", "app", "--stage", "01", "/s/study.json", "--child", "--argv-json", '["--force", "--n", "3"]'])
chk("this interpreter, normally", SV.child_argv("01", "x")[0], sys.executable)
bindir = os.path.join(TMP, "bin")
os.makedirs(bindir)
for exe in ("python.exe", "pythonw.exe"):
    open(os.path.join(bindir, exe), "w").close()
old = sys.executable
sys.executable = os.path.join(bindir, "pythonw.exe")
try:
    chk("pythonw.exe is swapped for the console python.exe beside it",
        SV.child_argv("01", "x")[0], os.path.join(bindir, "python.exe"))
finally:
    sys.executable = old

print("--- a child that runs and pauses by itself ---")
reset()
rc, pauses, tail, lines = run(child(
    "say('  1/3', end='\\r'); say('\\n@@drive lost /mnt/slides | slide 2'); "
    "say('@@drive back 2.5'); say('all done'); sys.exit(0)"))
chk("rc 0", rc, 0)
chk("one launch", launches(), 1)
chk("one pause, from the child's lost/back pair", len(pauses), 1)
chk("...its seconds", pauses and pauses[0]["seconds"], 2.5)
chk("...its folder and label", pauses and (pauses[0]["folder"], pauses[0]["label"]),
    ("/mnt/slides", "slide 2"))
chk("...and when", bool(pauses and isinstance(pauses[0]["at"], str) and "T" in pauses[0]["at"]))
chk("lines stream, \\r as a line end", lines[0], "  1/3")
chk("the markers reach on_line", "@@drive back 2.5" in lines)
chk("the blank before a marker does not", "" in lines, False)
chk("tail is the last line", tail, "all done")

print("--- a child that fails with the drive present ---")
reset()
rc, pauses, tail, _ = run(child("say('no slides found'); sys.exit(1)"))
chk("rc 1, no relaunch", (rc, launches()), (1, 1))
chk("tail is its last word", tail, "no slides found")

print("--- abnormal death with the drive present ---")
reset()
rc, pauses, tail, _ = run(child("say('reading'); os._exit(3)"))
chk("rc 1 returned, no relaunch", (rc, launches()), (1, 1))
chk("tail names the raw status", "status 3" in tail and "reading" in tail)
if os.name == "posix":
    reset()
    rc, pauses, tail, _ = run(child("import signal; os.kill(os.getpid(), signal.SIGKILL)"))
    chk("killed by a signal, drive present: failure, no relaunch", (rc, launches()), (1, 1))
    chk("...the signal named", "signal 9" in tail)
    chk("...no pauses", pauses, [])

print("--- abnormal death while the drive is gone ---")
for name, first in (("exit 3", "drop_now(); os._exit(3)"),
                    ("SIGKILL", "drop_now(); import signal; os.kill(os.getpid(), signal.SIGKILL)")):
    if name == "SIGKILL" and os.name != "posix":
        continue
    reset()
    # The child itself drops the drive, so the death happens while it is gone.
    first = (f"def drop_now(): os.rename({DRIVE!r}, {GONE!r})\n"
             f"say('reading slide 4'); {first}")
    t = later(0.5, restore)
    rc, pauses, tail, lines = run(child(first))
    t.join()
    chk(f"{name}: relaunched once, final rc 0", (rc, launches()), (0, 2))
    chk(f"{name}: one pause", len(pauses), 1)
    chk(f"{name}: the pause lasted the wait", bool(pauses and pauses[0]["seconds"] >= 0.3))
    chk(f"{name}: its folder", pauses and pauses[0]["folder"], DRIVE)
    lost = [ln for ln in lines if PR.parse_drive(ln) and PR.parse_drive(ln)[0] == "lost"]
    back = [ln for ln in lines if PR.parse_drive(ln) and PR.parse_drive(ln)[0] == "back"]
    chk(f"{name}: the supervisor says lost once, and back", (len(lost), len(back)), (1, 1))
    chk(f"{name}: the second launch's output streams", "second launch" in lines)

print("--- a child that announced the loss itself, then crashed ---")
reset()
t = later(0.5, restore)
rc, pauses, tail, lines = run(child(
    f"os.rename({DRIVE!r}, {GONE!r}); say('\\n@@drive lost {DRIVE} | slide 9'); os._exit(3)"))
t.join()
chk("relaunched, rc 0", (rc, launches()), (0, 2))
lost = [ln for ln in lines if (PR.parse_drive(ln) or ("",))[0] == "lost"]
chk("no second lost line from the supervisor", len(lost), 1)
chk("one pause, with the child's label", [p["label"] for p in pauses], ["slide 9"])

print("--- exit 1 while the drive is gone is the stage's own failure ---")
reset()
t = later(0.5, restore)
rc, pauses, tail, _ = run(child(f"os.rename({DRIVE!r}, {GONE!r}); say('bad file'); sys.exit(1)"))
t.join()
chk("rc 1, no relaunch", (rc, launches()), (1, 1))

print("--- rc 1 with the drive still gone says no back ---")
reset()
rc, pauses, tail, lines = run(child(
    f"os.rename({DRIVE!r}, {GONE!r}); say('\\n@@drive lost {DRIVE} | slide 3'); sys.exit(1)"))
chk("rc 1, no relaunch", (rc, launches()), (1, 1))
chk("its pause recorded up to the end", [p["label"] for p in pauses], ["slide 3"])
chk("no back line while the drive is gone",
    any((PR.parse_drive(ln) or ("",))[0] == "back" for ln in lines), False)
restore()

print("--- a child that keeps dying without output is given up on ---")
reset()
t0 = time.time()
rc, pauses, tail, lines = run(child(
    f"say('\\n@@drive lost {DRIVE} | x'); os._exit(3)",
    f"say('\\n@@drive lost {DRIVE} | x'); os._exit(3)"))
chk("six launches: five relaunches, then failure", (rc, launches()), (1, 1 + SV.MAX_IDLE_RELAUNCHES))
chk("the error says so", "gave up" in tail and "status 3" in tail)
chk("...promptly", time.time() - t0 < 30)
reset()
rc, pauses, tail, lines = run(child(
    f"say('working'); say('\\n@@drive lost {DRIVE} | x'); os._exit(3)",
    "n = int(open(c).read())\n"
    f"if n < 9: say('working'); say('\\n@@drive lost {DRIVE} | x'); os._exit(3)\n"
    "say('done'); sys.exit(0)"))
chk("a child that prints between deaths is not capped", (rc, launches()), (0, 9))

print("--- on_line raising does not hang on a chatty child ---")
reset()


def boom(_line):
    raise RuntimeError("the log pane went away")


res = {}


def chatty():
    try:
        SV.run_child(child("while True: say('x' * 200)"), boom, STOP, poll=POLL)
    except RuntimeError as e:
        res["err"] = str(e)


th = threading.Thread(target=chatty, daemon=True)
th.start()
th.join(20)
chk("returns (does not hang)", th.is_alive(), False)
chk("...raising on_line's error", res.get("err"), "the log pane went away")

print("--- stop during the supervisor's wait ---")
reset()
t = later(0.4, lambda: open(STOP, "w").close())
t0 = time.time()
rc, pauses, tail, lines = run(child(f"os.rename({DRIVE!r}, {GONE!r}); os._exit(3)"))
t.join()
chk("returns promptly", time.time() - t0 < 5)
chk("failure, no relaunch", (rc != 0, launches()), (True, 1))
chk("error says why", tail, "stopped while the drive was disconnected")
chk("the unfinished pause is kept", len(pauses), 1)
chk("...without a back line: the drive is still gone",
    any((PR.parse_drive(ln) or ("",))[0] == "back" for ln in lines), False)
restore()

print("--- the Runner: a reader stage goes to the child ---")
reset()


class Reader:
    sid, title, script, cli_only, argv, reader = "01", "Overviews", "01_overviews.py", None, [], "pylibCZIrw"


real_argv = SV.child_argv
r = RN.Runner(os.path.join(REPO, "scripts"), PATH)
try:
    SV.child_argv = lambda sid, study, argv: child(
        "say('\\n@@drive lost /mnt/s | slide 1'); say('@@drive back 4.0'); say('wrote 3'); sys.exit(0)")
    got = []
    res = r.run(Reader(), on_line=got.append)
    chk("ok", res.ok, True)
    chk("pauses on the result", [p["seconds"] for p in res.pauses], [4.0])
    chk("output carries the whole text", "wrote 3" in res.output and "@@drive back 4.0" in res.output)
    chk("lines streamed", "wrote 3" in got)

    reset()
    SV.child_argv = lambda sid, study, argv: child("say('cannot read'); sys.exit(1)")
    res = r.run(Reader())
    chk("a failed child is a failed stage", (res.ok, res.error), (False, "cannot read"))
    chk("pauses default to []", res.pauses, [])

    reset()
    SV.child_argv = lambda sid, study, argv: child("os._exit(7)")
    res = r.run(Reader())
    chk("no output: the error names the status", (res.ok, "7" in (res.error or "")), (False, True))

    print("--- the stage argv (options, extra) reaches the child ---")
    reset()
    seen = {}

    def spy(sid, study, argv):
        seen["argv"] = argv
        return child("import json; say('ARGV ' + json.dumps(json.loads(sys.argv[-1]))); sys.exit(0)") \
            + real_argv(sid, study, argv)[1:]

    SV.child_argv = spy

    class WithOpts(Reader):
        argv = ["--marker", "{marker0}", "--force"]

    got = []
    res = r.run(WithOpts(), on_line=got.append, extra_argv=["--limit", "2"])
    chk("child_argv gets stage.argv + extra_argv, unexpanded",
        seen.get("argv"), ["--marker", "{marker0}", "--force", "--limit", "2"])
    chk("...and the child's command line carries it",
        'ARGV ["--marker", "{marker0}", "--force", "--limit", "2"]' in got)
    chk("...and it ran", res.ok, True)

    print("--- a stop before the run has its stop file is not lost ---")
    reset()
    SV.child_argv = lambda sid, study, argv: child("say('ran'); sys.exit(0)")
    r._lock.acquire()
    th = threading.Thread(target=lambda: seen.update(res=r.run(Reader())))
    th.start()
    time.sleep(0.3)
    r.stop()                       # the run is waiting on the lock: no stop file yet
    r._lock.release()
    th.join(20)
    chk("the pending stop is honoured", (seen["res"].ok, launches()), (False, 0))

    print("--- Runner.stop() during the child's own pause ---")
    reset()
    SV.child_argv = lambda sid, study, argv: child(
        f"os.rename({DRIVE!r}, {GONE!r}); say('\\n@@drive lost {DRIVE} | slide 5'); "
        "time.sleep(60)")
    t = later(0.8, r.stop)
    t0 = time.time()
    res = r.run(Reader())
    t.join()
    chk("ends at once", time.time() - t0 < 10)
    chk("a failure", res.ok, False)
    chk("stopped while the drive was disconnected", res.error,
        "stopped while the drive was disconnected")
    chk("its pause is kept", [p["label"] for p in res.pauses], ["slide 5"])
    chk("the stop file is cleared after the run", r._stop_file is None)
    restore()

    print("--- Runner.stop() with the drive there ---")
    reset()
    SV.child_argv = lambda sid, study, argv: child("say('working'); time.sleep(60)")
    t = later(0.8, r.stop)
    t0 = time.time()
    res = r.run(Reader())
    t.join()
    chk("ends at once, failed", (time.time() - t0 < 10, res.ok), (True, False))
    chk("no pauses", res.pauses, [])
    chk("says it was stopped", "stopped" in (res.error or ""))
    chk("...with the raw status", "status" in (res.error or ""))

    print("--- the next run is not stopped by the last one's stop ---")
    reset()
    SV.child_argv = lambda sid, study, argv: child("say('fine'); sys.exit(0)")
    chk("ok", r.run(Reader()).ok, True)
finally:
    SV.child_argv = real_argv

print("--- inside the child (LS_CHILD=1) a reader stage runs in-process ---")
scripts = os.path.join(TMP, "scripts")
os.makedirs(scripts)
with open(os.path.join(scripts, "99_probe.py"), "w", encoding="utf-8") as fh:
    fh.write("def main():\n    print('in process', __name__)\n    return 0\n")


class Probe(Reader):
    script = "99_probe.py"


os.environ["LS_CHILD"] = "1"
try:
    res = RN.Runner(scripts, PATH).run(Probe())
    chk("ran in this process", (res.ok, "lsstage_99_probe" in res.output), (True, True))
finally:
    os.environ.pop("LS_CHILD")

print("--- the run record keeps pauses and the error ---")
pz = [{"at": "2026-11-21T10:00:00+00:00", "seconds": 134.2, "folder": "E:\\Slides",
       "label": "LS53_1a scene 3"}]
LP.Project.open(PATH).record_run("01", False, "a", "b", pauses=pz,
                                 error="stopped while the drive was disconnected")
rec = LP.Project.open(PATH).block["runs"][-1]
chk("pauses round-trip to disk", rec.get("pauses"), pz)
chk("error round-trips", rec.get("error"), "stopped while the drive was disconnected")
LP.Project.open(PATH).record_run("01", True, "a", "b")
rec = LP.Project.open(PATH).block["runs"][-1]
chk("no pauses, no key", ("pauses" in rec, "error" in rec), (False, False))

shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
