"""`python -m app --run <screen> <study.json>`: Run all remaining without the window.

There was no way to create a study's results from a command line, so running
the Contagion slides remotely meant a hand-written script over autodetect,
workflow and runner. `app/headless.py` is that script, in the repo: the same
plan the screen's button makes, the same options and run records, one stage at
a time, stopping at the first failure.

The stages themselves are stood in for here; what is checked is the loop
around them.

    python tests/test_headless.py
"""

import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import write_study                                # noqa: E402

TMP = tempfile.mkdtemp()
PATH = write_study(os.path.join(TMP, "s"))
os.environ["LS_CONFIG"] = PATH

import headless as H                                            # noqa: E402
import ls_project as LP                                         # noqa: E402
import workflow as W                                            # noqa: E402
from runner import StageResult                                  # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<62} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


class StandIn:
    """Answers like Runner.run, failing the stages named in `fail`."""

    def __init__(self, fail=()):
        self.fail, self.ran = set(fail), []

    def names(self):
        return H.Runner(H.SCRIPTS, PATH).names()

    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        if on_line:
            on_line(f"{stage.sid} says hello")
        ok = stage.sid not in self.fail
        return StageResult(stage.sid, ok, 0.1, "", None if ok else "refused")


plan = W.run_plan("prepare", H.study(PATH, StandIn()))[0]
chk("the fixture study has stages to run", len(plan) > 3)

out = io.StringIO()
r = StandIn()
results = H.run_screen(PATH, "prepare", runner=r, out=out)
chk("every planned stage runs, in plan order", r.ran, plan)
chk("...and each one is reported ok", [ok for _sid, ok, _s, _e in results], [True] * len(plan))
chk("the stage's own lines reach the output", f"{plan[0]} says hello" in out.getvalue())
runs = LP.Project.open(PATH).block.get("runs") or []
chk("each run is recorded in the study file, as the window does",
    [x["stage"] for x in runs][-len(plan):], plan)

PATH2 = write_study(os.path.join(TMP, "s2"))
r = StandIn(fail={plan[1]})
results = H.run_screen(PATH2, "prepare", runner=r, out=io.StringIO())
chk("it stops at the first failure", r.ran, plan[:2])
chk("...which is reported with its message", results[-1][1:], (False, 0.1, "refused"))
chk("...and recorded as failed", (LP.Project.open(PATH2).block.get("runs") or [])[-1]["ok"], False)

print("--- pauses for a dropped drive reach the run record ---")
PZ = [{"at": "2026-11-21T10:00:00+00:00", "seconds": 134.2, "folder": "E:\\Slides",
       "label": "LS53_1a scene 3"}]


class Paused(StandIn):
    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        if stage.sid == plan[0]:
            return StageResult(stage.sid, True, 0.1, "", pauses=PZ)
        return StageResult(stage.sid, False, 0.1, "",
                           "stopped while the drive was disconnected\nmore", pauses=PZ)


PATH3 = write_study(os.path.join(TMP, "s3"))
H.run_screen(PATH3, "prepare", runner=Paused(), out=io.StringIO())
runs = (LP.Project.open(PATH3).block.get("runs") or [])[-2:]
chk("an ok run keeps its pauses", (runs[0]["ok"], runs[0].get("pauses")), (True, PZ))
chk("...and no error", "error" in runs[0], False)
chk("a failed run keeps its pauses", runs[1].get("pauses"), PZ)
chk("...and the first line of its error", runs[1].get("error"),
    "stopped while the drive was disconnected")

print("--- one stage: --stage ---")


def fresh(name):
    return write_study(os.path.join(TMP, name))


def runs_of(path):
    return LP.Project.open(path).block.get("runs") or []


def stage(path, sid, runner=None, **kw):
    """run_stage's (ok, rc), what it printed, and the stand-in that ran."""
    out, r = io.StringIO(), runner or StandIn()
    ok, rc = H.run_stage(path, sid, runner=r, out=out, **kw)
    return ok, rc, out.getvalue(), r


PS = fresh("st1")
ok, rc, said, r = stage(PS, "manifest")
chk("--stage manifest runs that one stage", (ok, rc, r.ran), (True, 0, ["manifest"]))
chk("...its own lines reach the output", "manifest says hello" in said)
chk("...and it is recorded once, as the window records it",
    [x["stage"] for x in runs_of(PS)], ["manifest"])

ok, rc, said, r = stage(PS, "manifest", runner=StandIn(fail={"manifest"}))
chk("a stage that fails: exit 1", (ok, rc), (False, 1))
chk("...recorded as failed, with its error", (runs_of(PS)[-1]["ok"], runs_of(PS)[-1].get("error")),
    (False, "refused"))


def refused(name, sid, words, **kw):
    path = fresh("ref_" + name)
    before = len(runs_of(path))
    ok, rc, said, r = stage(path, sid, **kw)
    chk(f"refused, exit 2: {name}", (ok, rc), (False, 2))
    chk(f"...saying why ({words!r})", words in said, True)
    chk("...running nothing and recording nothing", (r.ran, len(runs_of(path))), ([], before))
    return said, path


refused("an unknown stage", "nope", "no stage called 'nope'")
refused("unblinding without --unblind", "join_sampling", "--unblind", force=True)
said, where = refused("a curator page", "roi_curator", "roi_curator.html", force=True)
with open(where, encoding="utf-8") as fh:
    OUT_ROOT = json.load(fh)["out_root"]
chk("...the page's path under out_root is printed",
    os.path.join(OUT_ROOT, "reformatted", "roi_curator.html") in said)
refused("a stage the app does not run (cli_only)", "fiji_export", "Fiji", force=True)
refused("a stage for another layout", "pair", "Not part of this study", force=True)
refused("a blocked stage without --force", "verify", "manifest")
refused("an unknown option", "manifest", "no option 'nosuch'", opts={"nosuch": "1"})
refused("an option value of the wrong kind", "czi_selftest", "pixels",
        opts={"pixels": "lots"})

PF = fresh("force")
ok, rc, _said, r = stage(PF, "verify", force=True)
chk("a blocked stage runs with --force", (ok, rc, r.ran), (True, 0, ["verify"]))
PU = fresh("unblind")
ok, rc, _said, r = stage(PU, "join_sampling", force=True, unblind=True)
chk("unblinding runs with --unblind (and --force past its needs)", (ok, rc, r.ran),
    (True, 0, ["join_sampling"]))


class Seen(StandIn):
    def run(self, stage, on_line=None, extra_argv=()):
        self.argv = list(stage.argv)
        return StandIn.run(self, stage, on_line, extra_argv)


PO = fresh("opts")
ok, rc, _said, r = stage(PO, "czi_selftest", runner=Seen(), opts={"pixels": "3"})
chk("--opt pixels=3 reaches the stage as the window would pass it", r.argv, ["--pixels", "3"])
chk("...and the run records it", runs_of(PO)[-1].get("options"), {"--pixels": 3})
ok, rc, _said, r = stage(PO, "czi_selftest", runner=Seen(), opts={"--pixels": "2"})
chk("--opt takes the flag with its dashes too", r.argv, ["--pixels", "2"])


class Says(StandIn):
    def __init__(self, error, **flags):
        StandIn.__init__(self)
        self.error, self.flags = error, flags

    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        return StageResult(stage.sid, False, 0.1, "", self.error, **self.flags)


ok, rc, said, _r = stage(fresh("ctrlc"), "manifest",
                         runner=Says("KeyboardInterrupt: \n\ntb", interrupted=True))
chk("Ctrl+C during the stage: exit 130", rc, 130)
ok, rc, said, _r = stage(fresh("ctrlc_text"), "manifest",
                         runner=Says("KeyboardInterrupt: only in the text"))
chk("...read from the result's flag, not its error text", rc, 1)
PST = fresh("stopped")
ok, rc, said, _r = stage(PST, "manifest",
                         runner=Says("Stopped: stopped while waiting for E:\\\n\ntb",
                                     stopped=True))
chk("stopped while the drive was gone: exit 1", rc, 1)
chk("...saying so", "stopped while the drive was disconnected" in said)
chk("...and recorded with that error", runs_of(PST)[-1].get("error"),
    "stopped while the drive was disconnected")

print("--- the supervised child's side: --child ---")
PC = fresh("child")
ok, rc, said, r = stage(PC, "verify", runner=Seen(), child=True, argv=["--crc", "{out_root}"])
chk("--child runs the stage with exactly the argv it is given", (rc, r.argv),
    (0, ["--crc", "{out_root}"]))
chk("...past its needs (the parent decided)", r.ran, ["verify"])
chk("...and never records: the parent does", runs_of(PC), [])
chk("...nor leaves LS_CHILD set behind it", os.environ.get("LS_CHILD"), None)
ok, rc, said, _r = stage(PC, "manifest", runner=Says("SystemExit-like refusal\nmore"),
                         child=True, argv=[])
chk("a failing child exits 1", rc, 1)
chk("...with its error as its last line (the parent's record)",
    said.strip().splitlines()[-1], "SystemExit-like refusal")
ok, rc, said, _r = stage(PC, "manifest", runner=Says("Stopped: x\n\ntb", stopped=True),
                         child=True, argv=[])
chk("a child stopped in a pause says so last", (rc, said.strip().splitlines()[-1]),
    (1, "stopped while the drive was disconnected"))
chk("an unknown stage in a child: exit 2", stage(PC, "nope", child=True, argv=[])[1], 2)

print("--- --list ---")
PL = fresh("list")
out = io.StringIO()
chk("--list exits 0", H.list_stages(PL, out=out), 0)
rows = {ln.split()[0]: ln for ln in out.getvalue().splitlines() if ln.strip()}
now = H.study(PL, StandIn())
want = [s.sid for s in H.S.STAGES if W.applies(s, now)[0]]
chk("every stage that applies is listed", [s for s in want if s not in rows], [])
chk("a stage not run yet", rows["manifest"].rstrip().endswith("not run"))
chk("a blocked stage names what it waits for", "blocked: manifest - " in rows["verify"])
chk("...and the group is shown", "Ingest and QC" in rows["verify"])
chk("a stage for another layout", rows["pair"].rstrip().endswith("not for this study"))
chk("a stage the app does not run", "cli only: needs Fiji" in rows["fiji_export"])
chk("the title is shown", H.S.BY_ID["manifest"].title in rows["manifest"])

print("--- the window's hold: a curator page built, nothing exported ---")
PW = fresh("waiting")
with open(PW, encoding="utf-8") as fh:
    OUT_W = json.load(fh)["out_root"]
os.makedirs(os.path.join(OUT_W, "reformatted"), exist_ok=True)
open(os.path.join(OUT_W, "reformatted", "roi_curator.html"), "w").close()
ok, rc, said, r = stage(PW, "roi_geometry")
chk("--stage after a curator not exported yet is refused", (rc, r.ran), (2, []))
chk("...with the window's words", "Waiting for your ROI export" in said)
chk("...and nothing recorded", runs_of(PW), [])
out = io.StringIO()
H.list_stages(PW, out=out)
rows = {ln.split()[0]: ln for ln in out.getvalue().splitlines() if ln.strip()}
chk("--list shows it held, with the window's words",
    "blocked: roi_curator - Waiting for your ROI export" in rows["roi_geometry"])
chk("...and the curator as waiting for your export",
    "waiting for your export" in rows["roi_curator"])
ok, rc, said, r = stage(PW, "roi_geometry", force=True)
chk("--force still runs it", (rc, r.ran), (0, ["roi_geometry"]))

print("--- the terminal sees words, not the app's marker lines ---")
MARKS = ["@@progress 3/10 sections", "@@drive lost E:\\Slides | LS53_1a scene 3",
         "@@drive back 134", "section 2/5 written"]


class Marks(StandIn):
    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        for m in MARKS:
            on_line(m)
        return StageResult(stage.sid, True, 0.1, "")


ok, rc, said, _r = stage(fresh("marks"), "manifest", runner=Marks())
chk("@@progress lines are dropped", "@@progress" in said, False)
chk("a lost drive reads as a sentence",
    "slides drive unreachable (E:\\Slides), paused at LS53_1a scene 3; waiting, Ctrl+C to stop"
    in said)
chk("...and its return", "drive back after 134 s, carrying on" in said)
chk("...no raw @@drive line", "@@drive" in said, False)
chk("an ordinary count line is kept", "section 2/5 written" in said)
out = io.StringIO()
H.run_screen(fresh("marks_run"), "prepare", runner=Marks(), out=out)
chk("--run filters the same way",
    ("@@" in out.getvalue(), "drive back after 134 s, carrying on" in out.getvalue()),
    (False, True))
ok, rc, said, _r = stage(fresh("marks_child"), "manifest", runner=Marks(), child=True, argv=[])
chk("--child passes the markers through raw, for its parent",
    all(m in said for m in MARKS))

print("--- Ctrl+C in --run ---")


class CtrlC(StandIn):
    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        return StageResult(stage.sid, False, 0.1, "", "KeyboardInterrupt: ", interrupted=True)


PK = fresh("run_ctrlc")
try:
    H.run_screen(PK, "prepare", runner=CtrlC(), out=io.StringIO())
    raised = False
except KeyboardInterrupt:
    raised = True
chk("a Ctrl+C inside a stage stops --run", raised)
chk("...after recording the run as failed", [x["ok"] for x in runs_of(PK)], [False])
real = H.run_screen
H.run_screen = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
try:
    chk("...and --run exits 130", H.main(["app", "--run", "prepare", PK]), 130)
finally:
    H.run_screen = real

print("--- Runner sets stopped / interrupted from what was raised ---")
SDIR2 = os.path.join(TMP, "flag_scripts")
os.makedirs(SDIR2, exist_ok=True)
with open(os.path.join(SDIR2, "ki.py"), "w", encoding="utf-8") as fh:
    fh.write("def main():\n    raise KeyboardInterrupt\n")
with open(os.path.join(SDIR2, "stop.py"), "w", encoding="utf-8") as fh:
    fh.write("import sys\nsys.path.insert(0, %r)\nimport ls_io\n"
             "def main():\n    raise ls_io.drive_module().Stopped('stopped while waiting')\n"
             % H.SCRIPTS)
import copy                                                     # noqa: E402
import runner as RN                                             # noqa: E402
PR_ = fresh("flags")
rr = H.Runner(SDIR2, PR_)


def fake(script):
    st = copy.copy(H.S.BY_ID["manifest"])
    st.script, st.argv = script, []
    return st


res = rr.run(fake("ki.py"))
chk("in process: KeyboardInterrupt -> interrupted", (res.ok, res.interrupted, res.stopped),
    (False, True, False))
res = rr.run(fake("stop.py"))
chk("in process: ls_drive.Stopped -> stopped", (res.ok, res.stopped, res.interrupted),
    (False, True, False))
chk("...recorded as the stopped error", res.record_error(),
    "stopped while the drive was disconnected")
real = RN.SV.run_child
try:
    RN.SV.run_child = lambda *a, **k: (130, [], "KeyboardInterrupt:")
    res = rr.run(H.S.BY_ID["czi_selftest"])
    chk("a child's exit 130 -> interrupted", (res.interrupted, res.stopped), (True, False))
    RN.SV.run_child = lambda *a, **k: (1, [], RN.SV.STOPPED_IN_PAUSE)
    res = rr.run(H.S.BY_ID["czi_selftest"])
    chk("the supervisor's stopped-in-a-pause -> stopped", (res.stopped, res.interrupted),
        (True, False))
    RN.SV.run_child = lambda *a, **k: (1, [], "some other failure")
    res = rr.run(H.S.BY_ID["czi_selftest"])
    chk("...any other failure is neither", (res.stopped, res.interrupted), (False, False))
finally:
    RN.SV.run_child = real

print("--- a reader stage through the real supervised child ---")
STANDIN = r"""
import json, os, sys
def main():
    print("stand-in argv " + json.dumps(sys.argv[1:]))
    print("stand-in pid %d child %s" % (os.getpid(), os.environ.get("LS_CHILD")))
    if "99" in sys.argv:
        raise SystemExit("stand-in refuses 99")
    if "130" in sys.argv:
        raise KeyboardInterrupt
    return 0
"""
SDIR = os.path.join(TMP, "standin_scripts")
os.makedirs(SDIR, exist_ok=True)
with open(os.path.join(SDIR, H.S.BY_ID["czi_selftest"].script), "w", encoding="utf-8") as fh:
    fh.write(STANDIN)
PB = fresh("childreal")
os.environ.pop("LS_CHILD", None)
ok, rc, said, _r = stage(PB, "czi_selftest", runner=H.Runner(SDIR, PB), opts={"pixels": "3"})
chk("a reader stage runs through Runner and the supervisor", (ok, rc), (True, 0))
chk("...the argv reached the stand-in", 'stand-in argv ["--pixels", "3"]' in said)
pid = [ln for ln in said.splitlines() if ln.startswith("stand-in pid")]
chk("...its output streamed back", len(pid), 1)
chk("...from a child process, with LS_CHILD set",
    bool(pid) and pid[0].split()[2] != str(os.getpid()) and pid[0].endswith("child 1"))
chk("...exactly one run record, written by the parent",
    [(x["stage"], x["ok"], x.get("options")) for x in runs_of(PB)],
    [("czi_selftest", True, {"--pixels": 3})])
ok, rc, said, _r = stage(PB, "czi_selftest", runner=H.Runner(SDIR, PB), opts={"pixels": "99"})
chk("a refusal in the child fails the stage", (ok, rc), (False, 1))
chk("...and its message is what the parent records", runs_of(PB)[-1].get("error"),
    "stand-in refuses 99")
ok, rc, said, _r = stage(PB, "czi_selftest", runner=H.Runner(SDIR, PB), opts={"pixels": "130"})
chk("Ctrl+C in the real child: exit 130, recorded as failed",
    (rc, runs_of(PB)[-1]["ok"], len(runs_of(PB))), (130, False, 3))

print("--- the run is recorded after the drive the study file is on comes back ---")
import shutil                                                   # noqa: E402
import threading                                                # noqa: E402


def on_drive(name):
    """A study whose study.json is inside out_root, as the app lays one out."""
    first = fresh(name)
    with open(first, encoding="utf-8") as fh:
        cfg = json.load(fh)
    path = os.path.join(cfg["out_root"], "study.json")
    shutil.copy(first, path)
    os.remove(first)
    return path, cfg["out_root"]


class DropsAtEnd(StandIn):
    """A stage that ends with the drive gone; it is back 0.5 s later."""

    def __init__(self, out_root, ok=True):
        super().__init__()
        self.out_root, self.ok = out_root, ok

    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        os.rename(self.out_root, self.out_root + ".gone")
        threading.Timer(0.5, lambda: os.rename(self.out_root + ".gone", self.out_root)).start()
        return StageResult(stage.sid, self.ok, 0.1, "",
                           None if self.ok else "stopped while the drive was disconnected",
                           stopped=not self.ok)


H.Runner(H.SCRIPTS, PATH)                   # LS_PROGRESS=1, as on a real command line
watch_was = os.environ.pop("LS_WATCH", None)
PD, OUT_D = on_drive("ondrive")
ok, rc, said, _r = stage(PD, "manifest", runner=DropsAtEnd(OUT_D, ok=False))
chk("a run that ended with the drive gone is recorded once it is back",
    [(x["stage"], x["ok"], x.get("error")) for x in runs_of(PD)],
    [("manifest", False, "stopped while the drive was disconnected")])
chk("...the wait is said in words", "drive back after" in said and "unreachable" in said)
chk("...not as raw marker lines", "@@drive" in said, False)
PD2, OUT_D2 = on_drive("ondrive2")
os.environ["LS_CONFIG"] = PD2
results = H.run_screen(PD2, "prepare", runner=DropsAtEnd(OUT_D2), out=io.StringIO())
chk("--run records it too", [x["stage"] for x in runs_of(PD2)][:1], [results[0][0]])
if watch_was is not None:
    os.environ["LS_WATCH"] = watch_was
os.environ["LS_CONFIG"] = PATH

print("--- the command line ---")
env = dict(os.environ, PYTHONIOENCODING="utf-8")
p = subprocess.run([sys.executable, "-m", "app", "--run", "nowhere", PATH], cwd=REPO,
                   env=env, capture_output=True, text=True, timeout=120)
chk("an unknown screen is refused, with exit status 2", p.returncode, 2)
chk("...naming the screens there are", "prepare" in p.stderr)
p = subprocess.run([sys.executable, "-m", "app", "--run", "prepare",
                    os.path.join(TMP, "missing.json")], cwd=REPO, env=env,
                   capture_output=True, text=True, timeout=120)
chk("a study file that is not there is refused", p.returncode, 2)
p = subprocess.run([sys.executable, "-m", "app", "--stage", "nope", PATH], cwd=REPO,
                   env=env, capture_output=True, text=True, timeout=120)
chk("--stage with an unknown stage exits 2", p.returncode, 2)
chk("...naming it", "nope" in p.stdout + p.stderr)
p = subprocess.run([sys.executable, "-m", "app", "--list", PATH], cwd=REPO,
                   env=env, capture_output=True, text=True, timeout=120)
chk("--list from the command line exits 0", p.returncode, 0)
chk("...listing the stages", "manifest" in p.stdout)
p = subprocess.run([sys.executable, "-m", "app", "--help"], cwd=REPO,
                   env=env, capture_output=True, text=True, timeout=120)
chk("--help exits 0 without starting the window", p.returncode, 0)
chk("...and names --run, --stage and --list",
    all(w in p.stdout for w in ("--run", "--stage", "--list")))
p = subprocess.run([sys.executable, "-m", "app", "--stage", "manifest"], cwd=REPO,
                   env=env, capture_output=True, text=True, timeout=120)
chk("--stage without a study is refused with the usage", (p.returncode, "--stage" in p.stderr),
    (2, True))

# An on/off option given a word that is neither on nor off is refused, not read
# as off: `--opt force=ture` used to run without the redo it asked for.
try:
    H._options("overviews", {"force": "ture"})
    chk("a mistyped on/off value is refused", False)
except ValueError as exc:
    chk("a mistyped on/off value is refused, naming the value", "ture" in str(exc))
chk("...while true/off still read as on/off",
    (H._options("overviews", {"force": "true"}), H._options("overviews", {"force": "off"})),
    ({"--force": True}, {"--force": False}))

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
