"""Run records waiting for the drive are not lost silently.

  * Closing the window with a record still pending (the drive gone) asks first:
    "Wait for the drive" keeps the window open and the record pending.
  * The drive back before the close: the record is written, no prompt.
  * A record that has to be dropped (not the drive) logs a line naming the stage.
  * A record made while older ones wait is queued behind them.

    QT_QPA_PLATFORM=offscreen python tests/test_pending_record_close.py
"""

import json
import os
import sys
import tempfile
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

import main_window as MW                                        # noqa: E402
import ls_project as LP                                         # noqa: E402

# The prompt: record what it says and press the named button (the default if None).
asked = []
press = [None]


def fake_exec(self):
    asked.append((self.text(), [b.text() for b in self.buttons()],
                  self.defaultButton().text() if self.defaultButton() else None))
    want = press[0]
    for b in self.buttons():
        if (want is None and b is self.defaultButton()) or b.text() == want:
            b.click()
            return 0
    return 0


QMessageBox.exec = fake_exec

w = MW.MainWindow(REPO, config_path=STUDY.path)
w.show()
app.processEvents()

# A study whose file is inside a folder that can "disconnect".
drive = os.path.join(TMP, "drive")
os.makedirs(drive)
path = os.path.join(drive, "study.json")
with open(STUDY.path, encoding="utf-8") as fh:
    cfg = json.load(fh)
cfg["out_root"] = drive
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh)
w.session.project = LP.Project.open(path)
s = w.session


def stages_on_disk():
    with open(path, encoding="utf-8") as fh:
        return [r["stage"] for r in (json.load(fh).get("project") or {}).get("runs") or []]


def gone():
    os.rename(drive, drive + ".gone")


def back():
    os.rename(drive + ".gone", drive)


print("--- (a) closing with a record pending and the drive still gone ---")
gone()
s.record_run("05c", False, "a", "b", error="stopped while the drive was disconnected",
             title="05c Something")
chk("one record pending", s.pending_runs(), 1)
press[0] = None                                     # the default
w.close()
app.processEvents()
chk("the prompt was shown once", len(asked), 1)
text = asked[0][0] if asked else ""
chk("...says how many and why", ("1 run record" in text, "disconnected" in text), (True, True))
chk("...says closing loses it", "loses" in text and "history" in text)
chk("...two buttons, Wait the default",
    (asked[0][1], asked[0][2]) if asked else None,
    (["Wait for the drive", "Close anyway"], "Wait for the drive"))
chk("Wait keeps the window open", w.isVisible())
chk("...and the record pending", s.pending_runs(), 1)

print("--- the stop-after-it-finishes path asks again, and Close anyway closes ---")
w._close_when_idle = True
w.close()
app.processEvents()
chk("asked again on the next close attempt", len(asked), 2)
chk("...window still open", w.isVisible())
press[0] = "Close anyway"

print("--- (b) the drive returns before the close: written, no prompt ---")
press[0] = None
back()
del asked[:]
w.close()
app.processEvents()
chk("no prompt", asked, [])
chk("the record is on disk", stages_on_disk(), ["05c"])
chk("nothing pending", s.pending_runs(), 0)
chk("the window closed", w.isVisible(), False)

print("--- Close anyway closes ---")
w2 = MW.MainWindow(REPO, config_path=STUDY.path)
w2.show()
app.processEvents()
w2.session.project = LP.Project.open(path)
os.rename(drive, drive + ".gone")
w2.session.record_run("05d", True, "a", "b", title="05d X")
press[0] = "Close anyway"
del asked[:]
w2.close()
app.processEvents()
chk("prompted, window closed", (len(asked), w2.isVisible()), (1, False))
back()

print("--- (c) a dropped record is logged with the stage's title ---")
w3 = MW.MainWindow(REPO, config_path=STUDY.path)
w3.show()
app.processEvents()


class Broken:
    def record_run(self, *a, **k):
        raise OSError(13, "Permission denied")


w3.session._pending.append((Broken(), ("01", True, "a", "b"), {}, "01 Export section overviews"))
w3.session._record_pending()
log = w3.drawer.log.toPlainText()
chk("the line names the stage and the reason",
    ("could not record the run of 01 Export section overviews in the study" in log,
     "Permission denied" in log), (True, True))
chk("...and it was dropped", w3.session.pending_runs(), 0)

print("--- a record made while older ones wait is queued behind them ---")
w3.session.project = LP.Project.open(path)
os.rename(drive, drive + ".gone")
w3.session.record_run("A", True, "a", "b")
back()
w3.session.record_run("B", True, "a", "b")
chk("B waits behind A", w3.session.pending_runs(), 2)
w3.session._record_pending()
chk("recorded in order", stages_on_disk()[-2:], ["A", "B"])

w3._close_when_idle = False
w3.close()
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
