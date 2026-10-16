"""The whole tutorial, start to finish, on a synthetic demo pack.

Each step's done condition is fired the way the app would fire it, and the
panel's step and screen are checked at every point. Then the same again by
skipping everything.

    python tests/test_tutorial_walk.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import use_temp_study                             # noqa: E402

STUDY = use_temp_study()
TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")
os.environ["LS_TUTORIAL_HOME"] = os.path.join(TMP, "Tutorial study")

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<66} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


from PySide6.QtWidgets import QAbstractButton, QApplication, QMessageBox  # noqa: E402
app = QApplication.instance() or QApplication([])
for name in ("information", "warning"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

import _tutorial_fixture as TF                                  # noqa: E402
import tutorial_steps as TS                                     # noqa: E402
import main_window as MW                                        # noqa: E402

os.environ["LS_TUTORIAL_PACK"] = TF.make_pack(TMP)
w = MW.MainWindow(REPO, config_path=STUDY.path)
w.resize(1400, 900)
w.show()
app.processEvents()
chk("the tutorial starts", w.start_tutorial())
c = w.tutorial
OUT = c.out_root
ended = []
c.finished.connect(lambda: ended.append(1))


def fire(step):
    kind, arg = TS.parse_done(step.done)
    if kind == "next":
        c.next()
    elif kind == "click":
        w.findChild(QAbstractButton, step.target).click()
    elif kind == "stage":
        w.stage_finished.emit(arg, True)
    elif kind == "file":
        path = os.path.join(OUT, arg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        w.stage_finished.emit("manifest", True)
    elif kind == "curation-checked":
        w.curator.store.write("ls_roi_curator_v1", json.dumps(TF.ANSWER))
        w.curator.exported.emit(os.path.join(OUT, "exports", "roi_regions_walk.csv"))
        app.processEvents()
        w.findChild(QAbstractButton, "tutCarryOn").click()
    app.processEvents()


print("--- every step, done the way the app does it ---")
for i, step in enumerate(TS.STEPS):
    chk(f"{i + 1:2}: on {step.sid}", c.current.sid, step.sid)
    if step.screen in w.pages:
        chk(f"    ...on its screen ({step.screen})", w.stack.currentWidget() is w.pages[step.screen])
    if TS.parse_done(step.done)[0] != "next" and step.sid not in c.progress.done:
        chk("    ...Next waits until it is done", c.panel.next.isEnabled(), False)
    fire(step)
    if TS.parse_done(step.done)[0] != "next" and c.current.sid == step.sid and i < len(TS.STEPS) - 1:
        c.next()                       # already done on entering: the learner presses Next
        app.processEvents()
chk("the last Next finishes the tutorial", ended, [1])
chk("every step is ticked", c.progress.done, {s.sid for s in TS.STEPS})
chk("the panel shows the finish text", TS.STEPS[-1].title in c.panel.title.text())

print("--- skipping everything ---")
w.leave_tutorial()
app.processEvents()
w.start_tutorial(fresh=True)
c = w.tutorial
ended = []
c.finished.connect(lambda: ended.append(1))
chk("a fresh start begins at the first step", c.current.sid, TS.STEPS[0].sid)
for _ in TS.STEPS:
    c.panel.skip.click()
    app.processEvents()
chk("skipping every step also reaches the end", ended, [1])
chk("...with every step marked skipped", c.progress.skipped, {s.sid for s in TS.STEPS})

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
