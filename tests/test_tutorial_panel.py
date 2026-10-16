"""The tutorial's controller, side panel and pointer bubble, in the real window.

    python tests/test_tutorial_panel.py
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


from PySide6.QtCore import QRect                                # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QPushButton  # noqa: E402
from PySide6.QtWidgets import QMessageBox                       # noqa: E402
app = QApplication.instance() or QApplication([])
# Boxes the window would show (a slide read's summary) must not block the suite.
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

import _tutorial_fixture as TF                                  # noqa: E402
import tutorial_pack as TP                                      # noqa: E402
import tutorial_steps as TS                                     # noqa: E402
import tutorial as TU                                           # noqa: E402
import main_window as MW                                        # noqa: E402

pack = TP.open_pack(TF.make_pack(TMP))
study = TP.install(pack, TP.tutorial_home())
OUT = os.path.dirname(study)
w = MW.MainWindow(REPO, config_path=study)
w.resize(1400, 900)
w.show()
app.processEvents()


def ctl_on(sid):
    """Put the controller on step `sid` directly, as a resumed tutorial would be."""
    c.progress.current = sid
    c._enter()
    app.processEvents()


def btn(name):
    return w.findChild(QPushButton, name)


print("--- starting ---")
c = TU.TutorialController(w, pack, study)
c.start()
app.processEvents()
panel = w.findChild(QWidget, "tutorialPanel")
chk("the panel is shown", panel is not None and panel.isVisible())
chk("first step", c.current.sid, TS.STEPS[0].sid)
chk("on the first step's screen", w.stack.currentWidget() is w.pages["home"])
chk("Next is on for a next step", btn("tutNext").isEnabled())
chk("Back is off on the first step", btn("tutBack").isEnabled(), False)

print("--- moving ---")
btn("tutNext").click()
chk("next on a next-step advances", c.current.sid, TS.STEPS[1].sid)
chk("...and ticks the one before", TS.STEPS[0].sid in c.progress.done)
btn("tutBack").click()
chk("back returns", c.current.sid, TS.STEPS[0].sid)
btn("tutSkip").click()
chk("skip advances", c.current.sid, TS.STEPS[1].sid)
chk("...and records it skipped", TS.STEPS[0].sid in c.progress.skipped)

print("--- a step done by a click ---")
ctl_on("read_settings")
chk("on Set up", w.stack.currentWidget() is w.pages["setup"])
chk("Next waits for the click", btn("tutNext").isEnabled(), False)
w.findChild(QPushButton, "readSettings").click()
app.processEvents()
chk("the click ticks it and moves on", c.current.sid, "setup_cards")

print("--- a step done by a stage ---")
ctl_on("prepare_run")
chk("on Prepare", w.stack.currentWidget() is w.pages["prepare"])
w.stage_finished.emit("overviews", True)
chk("another stage does not tick it", c.current.sid, "prepare_run")
w.stage_finished.emit("manifest", True)
chk("its stage finishing ticks it and moves on", c.current.sid, "scene_pictures")

print("--- slow steps and ready-made results ---")
chk("a slow step offers ready-made results", btn("tutReady").isVisible())
w.stage_finished.emit("scene_renders", False)
chk("a failure is said in the panel", "did not finish" in c.panel.text.text())
chk("...and ready-made is still offered", btn("tutReady").isEnabled())
btn("tutReady").click()
app.processEvents()
chk("use_ready copies the pack's results",
    os.path.isfile(os.path.join(OUT, "qc", "scene_renders.csv")))
chk("...and ticks the step", "scene_pictures" in c.progress.done)
chk("...and moves on", c.current.sid, "composites")
chk("a step whose results the pack lacks does not offer them", btn("tutReady").isEnabled(), False)

print("--- resuming ---")
c.progress.save()
c2 = TU.TutorialController(w, pack, study)
chk("resume after restart: same step", c2.progress.current, "composites")
chk("...same ticks", c2.progress.done, c.progress.done)

print("--- another study pauses it ---")
w.study_opened.emit(STUDY.path)
app.processEvents()
chk("other study pauses", c.paused)
chk("...and hides the panel", panel.isVisible(), False)
cur = c.current.sid
w.stage_finished.emit("rgb_perk", True)
chk("...and nothing ticks meanwhile", c.current.sid, cur)
w.study_opened.emit(study)
app.processEvents()
chk("back in the tutorial study it resumes", (c.paused, panel.isVisible()), (False, True))

print("--- the pointer bubble ---")
bub = w.findChild(QWidget, "tutorialBubble")
chk("the controller made a pointer", bub is not None)
ctl_on("prepare_run")
target = w.findChild(QPushButton, "runAll_prepare")
chk("it points at a visible target", bub.attached_to, "target")
centre = target.mapTo(w, target.rect().center())
chk("...its outline holds the button", bub.outline.contains(centre))
before = QRect(bub.outline)
w.resize(1100, 800)
app.processEvents()
w.resize(1500, 950)
app.processEvents()
chk("resizing re-places it", bub.outline != before or bub.outline.contains(
    target.mapTo(w, target.rect().center())))
w.goto("results")
app.processEvents()
chk("another screen hides it", bub.isVisible(), False)
w.goto("prepare")
app.processEvents()
chk("coming back shows it again", bub.isVisible())
bub.show_for("noSuchControl", "Press it", w, c.panel)
chk("a missing target attaches to the panel", bub.attached_to, "panel")
chk("...and still says something", "Press it" in bub.label())
ctl_on("finish")
chk("a step with no target clears it", bub.isVisible(), False)

print("--- practice in the ROI curator ---")
import json                                                     # noqa: E402
ctl_on("curator_tour")
chk("a curator target is not drawn by the Qt pointer", bub.isVisible(), False)
chk("...it is handed to the page", "tutorialPoint" in w.curator._tut_point)
ctl_on("practice")
chk("the practice step opens the ROI curator's tab", w.curate.current, "roi_curator")
chk("...so the Done button can be pointed at", bub.attached_to, "target")
learner = json.loads(json.dumps(TF.ANSWER))
learner[TF.PRACTICE[0]]["polys"][0]["v"] = [x + 70 if i % 2 == 0 else x
                                            for i, x in enumerate(learner[TF.PRACTICE[0]]
                                                                  ["polys"][0]["v"])]
learner["LS22_s02a_sc00"] = {"plate": 5, "polys": TF.ANSWER[TF.PRACTICE[1]]["polys"]}
w.curator.store.write("ls_roi_curator_v1", json.dumps(learner))
w.curator.exported.emit(os.path.join(OUT, "exports", "roi_landmarks_x.csv"))
chk("only the regions file starts the check", w.findChild(QWidget, "tutCheck"), None)
w.curator.exported.emit(os.path.join(OUT, "exports", "roi_regions_x.csv"))
app.processEvents()
box = w.findChild(QWidget, "tutCheck")
chk("the comparison is shown", box is not None)
chk("...naming the region that is off", box is not None and "Dm" in box.text()
    and "overlaps" in box.text())
chk("...on the curator, not Measure", w.stack.currentWidget() is w.pages["curate"])
chk("...with the expert's outlines drawn", "tutorialShowKey" in w.curator._tut_key)
chk("Try again and Carry on are offered",
    (btn("tutRetry") is not None, btn("tutCarryOn") is not None), (True, True))
btn("tutRetry").click()
app.processEvents()
after = json.loads(w.curator.store.read("ls_roi_curator_v1"))
chk("try again clears the practice sections' outlines",
    [bool(after[u].get("polys")) for u in TF.PRACTICE], [False, False])
chk("...and keeps every other section's", bool(after["LS22_s02a_sc00"].get("polys")))
chk("...and keeps the plate matched", after[TF.PRACTICE[0]].get("plate"), 3)
chk("...the step is not done yet", c.current.sid, "practice")
w.curator.store.write("ls_roi_curator_v1", json.dumps(TF.ANSWER))
w.curator.exported.emit(os.path.join(OUT, "exports", "roi_regions_y.csv"))
app.processEvents()
chk("matching outlines are all good", "3 of 3" in w.findChild(QWidget, "tutCheck").text())
btn("tutCarryOn").click()
app.processEvents()
chk("carry on ticks the practice and moves on", c.current.sid, "place_rois")
chk("...and stops drawing the key", w.curator._tut_key, "")

print("--- the end ---")
ended = []
c.finished.connect(lambda: ended.append(1))
ctl_on("finish")
chk("finish stays on the screen it was on", c.current.screen, "finish")
btn("tutNext").click()
chk("Next on the last step finishes", ended, [1])

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
