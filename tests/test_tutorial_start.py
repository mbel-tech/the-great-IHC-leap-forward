"""Starting the tutorial (Project screen, Help menu, the first-run offer) and leaving it.

    python tests/test_tutorial_start.py
"""

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


from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget  # noqa: E402
app = QApplication.instance() or QApplication([])
boxes = []
QMessageBox.information = staticmethod(lambda *a, **k: boxes.append(a[1:3]) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: boxes.append(a[1:3]) or QMessageBox.Ok)
asked = []
QMessageBox.question = staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.No)

import _tutorial_fixture as TF                                  # noqa: E402
import tutorial_pack as TP                                      # noqa: E402
import main_window as MW                                        # noqa: E402

os.environ["LS_TUTORIAL_PACK"] = TF.make_pack(TMP)
w = MW.MainWindow(REPO, config_path=STUDY.path)
w.show()
app.processEvents()
real = os.path.normcase(os.path.abspath(STUDY.path))


def norm(p):
    return os.path.normcase(os.path.abspath(p))


print("--- where it starts ---")
start = w.findChild(QPushButton, "tutorialStart")
chk("the Project screen has a tutorial button", start is not None)
chk("...saying Start before any progress", start.text(), "Start the tutorial")
helps = [a for m in w.menuBar().actions() if m.menu() for a in m.menu().actions()
         if a.text().startswith("Tutorial")]
chk("the Help menu has Tutorial…", len(helps), 1)

print("--- starting ---")
start.click()
app.processEvents()
home = TP.tutorial_home()
chk("the tutorial study is opened", norm(w.config_path), norm(os.path.join(home, "output",
                                                                            "study.json")))
chk("...with settings every stage accepts",
    [b for b in boxes if "problems" in (b[0] or "")], [])
chk("...its panel is shown", w.findChild(QWidget, "tutorialPanel").isVisible())
chk("...and the study it left is remembered", norm(w.tutorial.progress.previous_study), real)
w.tutorial.next()
app.processEvents()
w.tutorial.progress.save()

print("--- leaving ---")
w._thread = object()                     # a stage is running
w.leave_tutorial()
chk("leave while running is refused", norm(w.config_path) != real)
chk("...and says why", any("running" in (b[1] or "") for b in boxes))
w._thread = None
w.leave_tutorial()
app.processEvents()
chk("leaving reopens the study that was open", norm(w.config_path), real)
chk("...and hides the panel", w.findChild(QWidget, "tutorialPanel") is None
    or not w.findChild(QWidget, "tutorialPanel").isVisible())
w.refresh()
chk("the button now says Continue", start.text(), "Continue the tutorial")

print("--- continuing ---")
helps[0].trigger()
app.processEvents()
chk("continuing resumes at the same step", w.tutorial.progress.current, "project")
chk("...without remembering the tutorial as the study to go back to",
    norm(w.tutorial.progress.previous_study), real)

print("--- starting over ---")
import json                                                     # noqa: E402
w.curator.store.write("ls_roi_curator_v1", json.dumps(TF.ANSWER))
w.session.project.save(force=True)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
w._start_tutorial_over()
app.processEvents()
QMessageBox.question = staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.No)
chk("start over leaves no outlines from before",
    json.loads(w.curator.store.read("ls_roi_curator_v1") or "{}"), {})
chk("...and starts at the first step", w.tutorial.progress.current, "welcome")
saved = None
try:
    w.session.project.save()
except Exception as exc:                                        # noqa: BLE001
    saved = type(exc).__name__
chk("...and the fresh study saves without a conflict", saved, None)
chk("...still going back to the study it left", norm(w.tutorial.progress.previous_study), real)
w.leave_tutorial()
app.processEvents()

print("--- no pack ---")
pack_path = os.environ.pop("LS_TUTORIAL_PACK")
boxes.clear()
picked = []
MW.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: picked.append(1) or ("", ""))
w.start_tutorial()
chk("with no pack it says what is needed", len(boxes), 1)
chk("...then offers to choose one", picked, [1])
chk("...and opens nothing", norm(w.config_path), real)

print("--- a chosen pack that is no good ---")
bad = os.path.join(TMP, "not a pack")
os.makedirs(bad)
MW.QFileDialog.getOpenFileName = staticmethod(
    lambda *a, **k: picked.append(2) or (os.path.join(bad, "pack.json"), ""))
picked.clear()
w.start_tutorial()
chk("a bad pack is not remembered", TP.settings().get("pack"), None)
MW.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: picked.append(3) or ("", ""))
TP.save_settings({"pack": bad})              # as an older version would have left it
picked.clear()
w.start_tutorial()
chk("a remembered pack that is no good offers the picker again", picked, [3])
chk("...and is forgotten", TP.settings().get("pack"), None)

print("--- first run ---")
asked.clear()
w._offer_tutorial_once()
chk("with no demo study the first-run offer is not made", asked, [])
chk("...and is kept for when there is one", TP.settings().get("offered"), None)
os.environ["LS_TUTORIAL_PACK"] = pack_path
w._offer_tutorial_once()
w._offer_tutorial_once()
chk("the first-run offer appears once", len(asked), 1)
chk("...and is remembered", TP.settings().get("offered"), True)

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
