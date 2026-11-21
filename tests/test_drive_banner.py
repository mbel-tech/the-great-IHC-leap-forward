"""The window flags a slides drive that went away, and lets it be stopped.

A stage prints `@@drive lost <folder> | <label>` when the drive goes and
`@@drive back <seconds>` when it returns (scripts/ls_drive.py). The window keeps
both out of the log; after 10 s of a drop it shows an amber banner in the drawer
and one system notification, turns the Stop button into "Stop now", and clears
all of it when the drive is back or the stage ends.

    QT_QPA_PLATFORM=offscreen python tests/test_drive_banner.py
"""

import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import use_temp_study                             # noqa: E402

STUDY = use_temp_study()
TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<66} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


from PySide6.QtCore import QCoreApplication                     # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox         # noqa: E402
app = QApplication.instance() or QApplication([])
boxes = []
QMessageBox.information = staticmethod(lambda *a, **k: boxes.append(a[1:3]) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: boxes.append(a[1:3]) or QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

import main_window as MW                                        # noqa: E402
import runner as RN                                             # noqa: E402

w = MW.MainWindow(REPO, config_path=STUDY.path)
w.show()
app.processEvents()
d = w.drawer

notes = []
shown = [True]


def fake_notify(title, body):
    notes.append((title, body))
    return shown[0]


w._notify = fake_notify


def pump(until, secs=5.0):
    end = time.time() + secs
    while not until() and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return until()


def log():
    return d.log.toPlainText()


def banner_up():
    return d.banner.isVisibleTo(d)


LOST = "@@drive lost E:\\Slides | LS53_1a scene 3"

print("--- a drop that lasts: banner, one notification, Stop now ---")
w._drive.after = 0
w._stage_line(LOST)
chk("the lost line is not in the log", "@@drive" in log(), False)
chk("the banner shows once the wait is over", pump(banner_up))
chk("...naming the folder and what was paused",
    ("E:\\Slides" in d.banner.text(), "LS53_1a scene 3" in d.banner.text()), (True, True))
chk("...in the theme's warning colour", MW.T.WARN in d.banner.styleSheet())
chk("one notification", notes, [("Slides drive disconnected",
                                 notes[0][1] if notes else None)])
chk("...ending 'will resume automatically'",
    bool(notes) and notes[0][1].endswith("will resume automatically"))
chk("Stop is shown and reads Stop now",
    (d.stop_btn.isVisibleTo(d), d.stop_btn.isEnabled(), d.stop_btn.text()),
    (True, True, "Stop now"))
w._stage_line("@@drive lost E:\\Slides | LS53_1a scene 4")
pump(lambda: False, 0.1)
chk("a second lost in the same drop does not notify again", len(notes), 1)
chk("...nor lose the button's own text", d._stop_saved[0] if d._stop_saved else None,
    "Stop after this stage")
w._stage_line("@@drive back 134")
chk("back hides the banner", banner_up(), False)
chk("...logs how long it was out and what is redone",
    "drive back after 2 min, redoing LS53_1a scene 4" in log())
chk("...and says it is resuming", notes[-1][0], "Drive back - resuming")
chk("Stop goes back to how it was",
    (d.stop_btn.isVisibleTo(d), d.stop_btn.text()), (False, "Stop after this stage"))
chk("no raw marker reached the log", "@@drive" in log(), False)

print("--- a short drop: only the log line ---")
del notes[:]
w._drive.after = 10.0
w._stage_line(LOST)
chk("the wait is running", w._drive_timer.isActive())
w._stage_line("@@drive back 3")
chk("back cancels the wait", w._drive_timer.isActive(), False)
pump(lambda: False, 0.2)
chk("no banner", banner_up(), False)
chk("no notification", notes, [])
chk("the log line is there", "drive back after 3 s, redoing LS53_1a scene 3" in log())

print("--- a notification that was never shown is not followed by one ---")
shown[0] = False
w._drive.after = 0
w._stage_line(LOST)
pump(banner_up)
w._stage_line("@@drive back 20")
chk("only the first was tried", [t for t, _ in notes], ["Slides drive disconnected"])
shown[0] = True
del notes[:]

print("--- a marker glued after a counter ---")
w._stage_line("  12/454@@drive lost E:\\Slides | LS1 s1")
chk("the counter part is kept", log().rstrip().endswith("12/454") or "  12/454" in log())
w._stage_line("@@drive back 1")
w._stage_line("@@drive something unreadable")
chk("an unreadable marker is dropped too", "@@drive" in log(), False)
del notes[:]

print("--- the real notifier ---")
w2_tray = []


class FakeTray:
    avail = True

    def __init__(self, icon, parent=None):
        self.icon = icon
        w2_tray.append(self)
        self.msgs = []

    @staticmethod
    def isSystemTrayAvailable():
        return FakeTray.avail

    def show(self):
        self.visible = True

    def showMessage(self, title, body, *a):
        self.msgs.append((title, body))

    Information = Warning = 0


real_tray = MW.QSystemTrayIcon
MW.QSystemTrayIcon = FakeTray
try:
    real_notify = MW.MainWindow._notify
    FakeTray.avail = False
    chk("no system tray: nothing shown, nothing created",
        (real_notify(w, "a", "b"), w2_tray), (False, []))
    FakeTray.avail = True
    chk("a tray: shown", real_notify(w, "a", "b"), True)
    real_notify(w, "c", "d")
    chk("...from one tray icon made on first use, with the app icon",
        (len(w2_tray), w2_tray[0].msgs, w2_tray[0].icon.isNull()),
        (1, [("a", "b"), ("c", "d")], False))
finally:
    MW.QSystemTrayIcon = real_tray

print("--- a stage that ends mid-drop, and Stop now ---")
recorded = []
real_record = w.session.record_run


def record(sid, ok, *a, **k):
    recorded.append((sid, ok, k.get("pauses"), k.get("error")))
    return None


w.session.record_run = record
release = threading.Event()
stops = []
PAUSES = [{"at": "2026-11-21T10:00:00", "seconds": 30.0, "folder": "E:\\Slides",
           "label": "LS53_1a scene 3"}]


def run_dropping(stage, on_line=None, extra_argv=()):
    on_line(LOST)
    release.wait(10)
    stopped = bool(stops)
    return RN.StageResult(stage.sid, False, 0.1, "",
                          "stopped while the drive was disconnected" if stopped else "it broke",
                          pauses=PAUSES, stopped=stopped)


def stand_in_stop():
    stops.append(True)
    release.set()


w.runner.run = run_dropping
w.runner.stop = stand_in_stop


def idle():
    return w._thread is None and not w._queue


# Ends with no back, before the 10 s are up.
w._drive.after = 10.0
release.clear()
w.run_stages(["scene_renders"])
pump(lambda: w._drive_timer.isActive())
release.set()
pump(idle)
chk("a stage that ended before the flag was due cancels the wait",
    (w._drive_timer.isActive(), banner_up()), (False, False))

# Ends with no back while flagged.
w._drive.after = 0
release.clear()
del notes[:]
w.run_stages(["scene_renders", "pair"])
pump(banner_up)
chk("the banner is up during the run", banner_up())
release.set()
pump(idle)
chk("a stage that ends with no back clears the banner", banner_up(), False)
chk("...and no back notification follows", [t for t, _ in notes], ["Slides drive disconnected"])
chk("...and the Stop button is hidden again", d.stop_btn.isVisibleTo(d), False)

# Stop now.
release.clear()
del stops[:], recorded[:]
w.run_stages(["scene_renders", "pair"])
pump(banner_up)
chk("Stop now is offered", (d.stop_btn.text(), d.stop_btn.isEnabled()), ("Stop now", True))
d.stop_btn.click()
chk("Stop now calls Runner.stop", stops, [True])
pump(idle)
chk("the stopped run is recorded as failed, with its pauses and why",
    recorded, [("scene_renders", False, PAUSES, "stopped while the drive was disconnected")])
chk("the banner is gone", banner_up(), False)
chk("the queue does not go on to the next stage", w._queue, [])
w.session.record_run = real_record

print()
print(f"{len(failures)} failure(s)" if failures else "all passed")
sys.exit(1 if failures else 0)
