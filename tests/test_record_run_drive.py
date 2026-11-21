"""Recording a run waits for a dropped drive; in the window it never blocks.

study.json normally lives in out_root, on the USB drive that drops. A run that
ended while the drive was gone - "stopped while the drive was disconnected" is
the obvious one - used to be recorded in memory only (the file "did not
exist") or not at all (the read failed), so the run history never said so.

  * `Project.record_run` waits for the folder the study file is in and for
    out_root, with no limit, and stops on the stop file (LS_STOP_FILE).
  * `ls_project.record_run_at(path, ...)` - what the command line uses -
    waits before it opens the file too.
  * `record_run(..., wait=False)` never waits: with the drive gone it raises
    DriveGone and changes nothing. The window's session uses it, keeps the
    run, and records it from a timer once the drive is back, so the window
    is never frozen by the wait.

No LS_WATCH anywhere here: the layout is the one the app makes.

Run:  python tests/test_record_run_drive.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import write_study                                # noqa: E402

TMP = tempfile.mkdtemp(prefix="lsrec_")
os.environ.pop("LS_WATCH", None)
os.environ.pop("LS_PROGRESS", None)
os.environ["LS_HOME"] = os.path.join(TMP, "home")

import ls_io                                                    # noqa: E402
import ls_project as LP                                         # noqa: E402

DR = ls_io.drive_module()
DR.POLL = 0.05
failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<62} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


def on_drive(name):
    """A study whose study.json is inside its out_root. Returns (path, out_root)."""
    root = os.path.join(TMP, name)
    first = write_study(root)
    with open(first, encoding="utf-8") as fh:
        cfg = json.load(fh)
    path = os.path.join(cfg["out_root"], "study.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.remove(first)
    os.environ["LS_CONFIG"] = path
    return path, cfg["out_root"]


def drop_for(folder, seconds):
    os.rename(folder, folder + ".gone")
    t = threading.Timer(seconds, lambda: os.rename(folder + ".gone", folder))
    t.start()
    return t


def stages_on_disk(path):
    with open(path, encoding="utf-8") as fh:
        return [r["stage"] for r in (json.load(fh).get("project") or {}).get("runs") or []]


print("--- Project.record_run waits for the drive the study file is on ---")
path, out = on_drive("a")
p = LP.Project.open(path)
t = drop_for(out, 0.4)
t0 = time.time()
try:
    p.record_run("05c", False, "a", "b", error="stopped while the drive was disconnected")
    raised = None
except Exception as exc:                                        # noqa: BLE001
    raised = f"{type(exc).__name__}: {exc}"
t.join()
chk("no error", raised, None)
chk("...it waited for the drive", time.time() - t0 >= 0.3)
chk("...and the run is on disk", stages_on_disk(path), ["05c"])

print("--- record_run_at: the drive gone before the file is opened ---")
path, out = on_drive("b")
t = drop_for(out, 0.4)
try:
    LP.record_run_at(path, "01", True, "a", "b")
    raised = None
except Exception as exc:                                        # noqa: BLE001
    raised = f"{type(exc).__name__}: {exc}"
t.join()
chk("no error", raised, None)
chk("...the run is on disk", stages_on_disk(path), ["01"])

print("--- the wait stops on the stop file ---")
path, out = on_drive("c")
p = LP.Project.open(path)
stop = os.path.join(TMP, "stop.flag")
open(stop, "w").close()
os.environ["LS_STOP_FILE"] = stop
os.rename(out, out + ".gone")
try:
    p.record_run("05c", False, "a", "b")
    got = None
except Exception as exc:                                        # noqa: BLE001
    got = type(exc).__name__
finally:
    os.environ.pop("LS_STOP_FILE", None)
    os.remove(stop)
    os.rename(out + ".gone", out)
chk("Stopped is raised", got, "Stopped")
chk("...nothing recorded", stages_on_disk(path), [])

print("--- wait=False never waits: DriveGone, nothing changed ---")
path, out = on_drive("d")
p = LP.Project.open(path)
os.rename(out, out + ".gone")
t0 = time.time()
try:
    p.record_run("05c", True, "a", "b", wait=False)
    got = None
except LP.ProjectError as exc:
    got = type(exc).__name__
os.rename(out + ".gone", out)
chk("DriveGone, a ProjectError", (got, time.time() - t0 < 0.5), ("DriveGone", True))
chk("...not in memory either", [r["stage"] for r in p.block.get("runs") or []], [])
chk("...nor on disk", stages_on_disk(path), [])

print("--- a project with no file still records in memory ---")
q = LP.Project.new("x")
q.record_run("05d", False, "a", "b")
chk("kept in memory", [r["stage"] for r in q.block["runs"]], ["05d"])

print("--- the window's session: never blocks, records once the drive is back ---")
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None
    print("SKIP PySide6 is not installed")
if QApplication is not None:
    import project_session as PS                                # noqa: E402
    app = QApplication.instance() or QApplication([])
    path, out = on_drive("e")
    s = PS.ProjectSession(LP.Project.open(path))
    os.rename(out, out + ".gone")
    t0 = time.time()
    problem = s.record_run("05c", False, "a", "b",
                           error="stopped while the drive was disconnected")
    chk("returns at once", time.time() - t0 < 0.5)
    chk("...saying the drive is disconnected", "disconnected" in (problem or ""))
    chk("...one run waiting", s.pending_runs(), 1)
    end = time.time() + 1.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    chk("still waiting while the drive is gone", s.pending_runs(), 1)
    os.rename(out + ".gone", out)
    end = time.time() + 10
    while s.pending_runs() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    chk("recorded once the drive is back", stages_on_disk(path), ["05c"])
    chk("...nothing left waiting", s.pending_runs(), 0)
    chk("...the error kept", LP.Project.open(path).block["runs"][-1].get("error"),
        "stopped while the drive was disconnected")

    # A pending record that then fails for another reason (an OSError with the
    # drive back) is dropped, not retried on every tick for ever.
    class Broken:
        def record_run(self, *a, **k):
            raise OSError(13, "Permission denied")
    s._pending.append((Broken(), ("05c", True, "a", "b"), {}, "05c"))
    s._retry.start()
    end = time.time() + 5
    while s.pending_runs() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    chk("a pending record failing with an OSError is dropped", s.pending_runs(), 0)

shutil.rmtree(TMP, ignore_errors=True)
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
