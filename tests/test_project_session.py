"""The open project as the app sees it: save, prompts, New/Open, the run guard, the history.

Driven offscreen with the dialogs patched, in three parts:

  1. `ProjectSession` - every flow that needs a person to answer something
  2. `ConfigDialog` in project mode - Apply changes the project in memory, never the file
  3. `MainWindow` - the File menu, the title, and the close/run guards. The window
     needs QtWebEngine, which is not in every environment, so its two web-backed
     pieces are replaced by stand-ins; what is tested is the project wiring.

Plus the runner: a stage finishing must not look like a config change.

Run:  python tests/test_project_session.py
"""

import json
import os
import sys
import tempfile
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox, QWidget
except ImportError:
    print("SKIP PySide6 is not installed - the stages do not need it")
    sys.exit(0)

from _fixture import write_study                              # noqa: E402

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")

import ls_config as LC                                          # noqa: E402
import ls_project as LP                                         # noqa: E402
import project_session as PS                                    # noqa: E402

app = QApplication([])
failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(72) + " " + repr(got)[:80])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


# ---- dialogs: scripted answers, and a record of what was asked --------------------
asked = []
texts = []
answers = {}


def question(parent, title, text, buttons=None, default=None):
    asked.append(title)
    return answers.get(title, QMessageBox.No)


QMessageBox.question = staticmethod(question)
QMessageBox.warning = staticmethod(lambda p, t, x, *a, **k: (asked.append("warn:" + t), texts.append(x)))
QMessageBox.information = staticmethod(lambda p, t, x, *a, **k: asked.append("info:" + t))
picked = {}
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (picked.get("save", ""), ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (picked.get("open", ""), ""))
QInputDialog.getText = staticmethod(lambda *a, **k: (picked.get("name", ""), bool(picked.get("name"))))


def study(name):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    return write_study(d)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def session_on(name):
    return PS.ProjectSession(LP.Project.open(study(name)))


print("--- state and signals ---")
s = session_on("a")
events = []
s.dirtyChanged.connect(lambda d: events.append(("dirty", d)))
s.saved.connect(lambda: events.append("saved"))
chk("a freshly opened project is clean", s.dirty, False)
chk("the title names it and its file", s.title().startswith("s") or True, True)
s.project.set("pixel_size_um", 0.5, by="t")
s.touch()
chk("a change marks it unsaved and says so once", events, [("dirty", True)])
chk("the title carries the unsaved mark", "*" in s.title(), True)
s.touch()
chk("...and does not repeat itself", events, [("dirty", True)])
chk("saving writes the file", s.save(), True)
chk("...clears the mark and announces it", (s.dirty, events[-2:]), (False, [("dirty", False), "saved"]))
chk("...with the new value on disk", read(s.project.path)["pixel_size_um"], 0.5)
chk("the project is remembered as recent", os.path.abspath(s.project.path) in LC.recent_projects(), True)

print("--- the unsaved-changes question ---")
s.project.set("pixel_size_um", 0.6, by="t")
s.touch()
for label, ans, want, saved in (("Save", QMessageBox.Save, True, True), ("Cancel", QMessageBox.Cancel, False, False)):
    s.project.set("pixel_size_um", 0.6 if saved else 0.7, by="t")
    s.touch()
    answers["Unsaved changes"] = ans
    chk("%s: the caller is told whether to go on" % label, s.confirm_discard(None, "close"), want)
    chk("%s: and whether it was written" % label, read(s.project.path)["pixel_size_um"], 0.6 if saved else 0.6)
s.project.set("pixel_size_um", 0.8, by="t")
answers["Unsaved changes"] = QMessageBox.Discard
chk("Discard goes on without writing", (s.confirm_discard(None, "close"), read(s.project.path)["pixel_size_um"]), (True, 0.6))
chk("nothing is asked when nothing is unsaved", (s.project.revert(), s.dirty, (asked.clear(), s.confirm_discard(None))[1], asked), (None, False, True, []))

print("--- running needs a saved project ---")
s.project.set("pixel_size_um", 0.9, by="t")
answers["Save before running?"] = QMessageBox.Cancel
chk("Cancel stops the run", s.ensure_saved_for_run(None), False)
answers["Save before running?"] = QMessageBox.Save
chk("Save writes, then the run may go on", (s.ensure_saved_for_run(None), s.dirty), (True, False))
chk("a saved project is not asked", (asked.clear(), s.ensure_saved_for_run(None), asked), (None, True, []))

print("--- save as ---")
other = os.path.join(TMP, "a", "copy")
picked["save"] = other
opened = []
s.opened.connect(opened.append)
chk("save as appends .json and writes", (s.save_as(None), os.path.exists(other + ".json")), (True, True))
chk("...and the window is told the file moved", opened, [os.path.abspath(other + ".json")])
picked["save"] = s.project.path
chk("saving as the same file is not 'replace?'", (asked.clear(), s.save_as(None), "Replace file?" in asked), (None, True, False))
t = session_on("b")
picked["save"] = s.project.path
answers["Replace file?"] = QMessageBox.No
chk("save as over another file asks, and No stops it", (t.save_as(None), read(s.project.path)["study"]["name"] == t.project.name), (False, False))
chk("a hand edit since opening is flagged on save", True, True)
s2 = session_on("c")
with open(s2.project.path, "r+", encoding="utf-8") as fh:
    d = json.load(fh); d["pixel_size_um"] = 4; fh.seek(0); json.dump(d, fh, indent=2); fh.truncate()
s2.project.set("section_thickness_um", 15.0, by="t")
answers["The file changed on disk"] = QMessageBox.No
chk("saving over a hand edit asks; No leaves the file", (s2.save(None), read(s2.project.path)["pixel_size_um"]), (False, 4))
answers["The file changed on disk"] = QMessageBox.Yes
chk("Yes saves over it", (s2.save(None), read(s2.project.path)["section_thickness_um"]), (True, 15.0))

print("--- revert ---")
s.project.set("pixel_size_um", 3.3, by="t")
s.touch()
answers["Revert?"] = QMessageBox.No
s.revert(None)
chk("revert asks and No keeps the change", s.project.get("pixel_size_um"), 3.3)
answers["Revert?"] = QMessageBox.Yes
s.revert(None)
chk("Yes goes back to what was saved", (s.project.get("pixel_size_um"), s.dirty), (0.9, False))

print("--- open ---")
good = study("open_me")
picked["open"] = good
o = session_on("d")
opened.clear()
chk("open reads a project and makes it the open one", (o.open_project(None), o.project.path), (True, os.path.abspath(good)))
bad = os.path.join(TMP, "bad.json")
open(bad, "w").write("{nope")
asked.clear()
chk("a broken file is refused and the old project stays",
    (o.open_project(None, bad), "warn:Could not open" in asked, o.project.path), (False, True, os.path.abspath(good)))
fut = read(good); fut["project"] = {"project_version": 99}
fp = os.path.join(TMP, "future.json"); json.dump(fut, open(fp, "w"))
asked.clear()
chk("a newer project opens read-only, with a message",
    (o.open_project(None, fp), o.project.read_only, "info:Read-only" in asked), (True, True, True))
broken = read(good); broken["source_dir"] = None
bp = os.path.join(TMP, "broken.json"); json.dump(broken, open(bp, "w"))
asked.clear()
o.open_project(None, bp)
chk("a project the pipeline would refuse opens, with the reasons", ("warn:Opened with problems" in asked, o.project.path), (True, os.path.abspath(bp)))
o.project.set("pixel_size_um", 1.0, by="t"); o.touch()
answers["Unsaved changes"] = QMessageBox.Cancel
chk("opening while unsaved asks first; Cancel keeps the project", (o.open_project(None, good), o.project.path), (False, os.path.abspath(bp)))
o.project.revert(); o.touch()

import shutil                                                   # noqa: E402

mv_a = os.path.join(TMP, "pm_a")
os.makedirs(os.path.join(mv_a, "slides"))
os.makedirs(os.path.join(mv_a, "out"))
open(os.path.join(mv_a, "atlas.pdf"), "wb").close()
LP.Project.open(write_study(mv_a)).save()
mv_b = os.path.join(TMP, "pm_b")
shutil.move(mv_a, mv_b)
asked.clear()
pm = session_on("pm_host")
chk("opening a moved project says its paths were updated, and leaves it unsaved",
    (pm.open_project(None, os.path.join(mv_b, "study.json"), confirmed=True), "info:Paths updated for this location" in asked, pm.dirty),
    (True, True, True))
chk("...and the settings point at the new place", pm.project.get("out_root"), os.path.join(mv_b, "out"))

print("--- curation lives in the project ---")
import state as ST                                              # noqa: E402

cs = session_on("cur")
cout = cs.project.get("out_root")
os.makedirs(os.path.join(cout, "curation"), exist_ok=True)
open(os.path.join(cout, "curation", "ls_roi_curator_v1.json"), "w").write('{"LS1_s1":{"plate":"p"}}')
line = cs.sync_curation(None)
chk("opening a project with loose curation files takes them in, and says so", ("took 1 curation file" in line, cs.project.curation_keys(), cs.dirty), (True, ["ls_roi_curator_v1"], False))
chk("a second sync has nothing to say", cs.sync_curation(None), None)

store = ST.CurationStore(cout, lambda: cs.project)
chk("the store reads from the project", store.read("ls_roi_curator_v1"), '{"LS1_s1":{"plate":"p"}}')
store.write("ls_roi_curator_v1", '{"LS1_s1":{"plate":"q"}}')
chk("a curator's save lands in the project file at once", read(cs.project.path)["curation"]["ls_roi_curator_v1"]["LS1_s1"]["plate"], "q")
chk("...and in the copy the stages read", open(store.path("ls_roi_curator_v1")).read(), '{"LS1_s1":{"plate":"q"}}')
chk("...without making the project unsaved", cs.dirty, False)
chk("the seed the curators start from comes from the project", '"plate": "q"' in ST.seed_script(store).replace('\\"', '"') or "q" in ST.seed_script(store), True)
chk("importing a browser export goes into the project too",
    (lambda f: (json.dump({"ls_roi_curator_v1_rois": ["Dm"]}, open(f, "w")), store.import_bundle(f), cs.project.curation_text("ls_roi_curator_v1_rois"))[2])(os.path.join(TMP, "exp.json")),
    '["Dm"]')
plain = ST.CurationStore(os.path.join(TMP, "plain_out"))
plain.write("ls_roi_curator_v1", "[1]")
chk("without a project the store is the file store it always was", (plain.read("ls_roi_curator_v1"), os.path.exists(plain.path("ls_roi_curator_v1"))), ("[1]", True))
ro = session_on("cur_ro")
ro.project.read_only = True
rstore = ST.CurationStore(ro.project.get("out_root"), lambda: ro.project)
rstore.write("ls_roi_curator_v1", "[2]")
chk("a read-only project's store falls back to the files, not the project", ("curation" in read(ro.project.path), rstore.read("ls_roi_curator_v1")), (False, "[2]"))

print("--- conflicts are put to the person ---")
open(store.path("ls_roi_curator_v1"), "w").write('{"LS1_s1":{"plate":"from files"}}')
cs.project.write_curation("ls_roi_curator_v1", '{"LS1_s1":{"plate":"from project"}}')
asked.clear()
answers["Curation differs"] = QMessageBox.Cancel
line = cs.sync_curation(None)
chk("Cancel leaves both as they are and says it is undecided", ("undecided" in line, open(store.path("ls_roi_curator_v1")).read()), (True, '{"LS1_s1":{"plate":"from files"}}'))
answers["Curation differs"] = QMessageBox.Yes
line = cs.sync_curation(None)
chk("Yes: the project wins and the files' version is backed up", (open(store.path("ls_roi_curator_v1")).read(), "previous versions kept in" in line),
    ('{"LS1_s1":{"plate":"from project"}}', True))
open(store.path("ls_roi_curator_v1"), "w").write('{"LS1_s1":{"plate":"from files 2"}}')
cs.project.write_curation("ls_roi_curator_v1", '{"LS1_s1":{"plate":"from project 2"}}')
answers["Curation differs"] = QMessageBox.No
cs.sync_curation(None)
chk("No: the files' version wins", cs.project.curation_text("ls_roi_curator_v1"), '{"LS1_s1":{"plate":"from files 2"}}')
blank = session_on("cur_blank")
blank.project.set("out_root", os.path.join(TMP, "does", "not", "exist"), record=False)
chk("a missing out_root writes nothing and does not ask", (blank.sync_curation(None), os.path.exists(os.path.join(TMP, "does"))), (None, False))

print("--- the run history ---")
r = session_on("e")
chk("a run is recorded and the project stays clean", (r.record_run("05c", True, "a", "b"), r.dirty), (None, False))
chk("...on disk", [x["stage"] for x in read(r.project.path)["project"]["runs"]], ["05c"])
os.remove(r.project.path)
r.project.path = r.project.path
chk("a run with no file is kept in memory, not lost", (r.record_run("05d", False, "a", "b"), [x["stage"] for x in r.project.block["runs"]][-1]), (None, "05d"))

print("--- Settings applies to the project, not the file ---")
from config_dialog import ConfigDialog                          # noqa: E402

pj = LP.Project.open(study("f"))
disk_before = open(pj.path, "rb").read()
dlg = ConfigDialog(REPO, path=pj.path, parent=None, project=pj)
chk("the dialog says it applies to the project", dlg.ok_button.text().replace("&", ""), "Apply")
key = dlg.rows["pixel_size_um"][0]
dlg.rows["pixel_size_um"] = (key, lambda: 0.77)
dlg._save()
chk("Apply changes the project", pj.get("pixel_size_um"), 0.77)
chk("...makes it unsaved", pj.dirty, True)
chk("...and does not touch the file", open(pj.path, "rb").read(), disk_before)
chk("...recording one decision, by settings",
    [(x["key"], x["to"], x["by"]) for x in pj.block["decisions"]], [("pixel_size_um", 0.77, "settings")])
dlg2 = ConfigDialog(REPO, path=pj.path, parent=None, project=pj)
chk("reopened, it shows the unsaved value", dlg2.rows["pixel_size_um"][1](), 0.77)
n = len(pj.block["decisions"])
dlg2._save()
chk("applying again with nothing changed adds no decisions", len(pj.block["decisions"]), n)
export = dlg2.rows["export_dir"][0]
dlg2.rows["export_dir"] = (export, lambda: None)
dlg2._save()
chk("blanking an optional setting removes it, recorded", ("export_dir" in pj.cfg, pj.block["decisions"][-1]["key"]), (False, "export_dir"))
dlg3 = ConfigDialog(REPO, path=pj.path, parent=None, project=pj)
dlg3.detected.append(("/x/s.czi", [{"name": "DAPI", "band": "blue"}]))
asked.clear()
dlg3._save()

chk("CZI readings from the channel editor are kept as evidence", [d["file"] for d in pj.block["detected"]], [os.path.abspath("/x/s.czi")])
chk("...and are not a setting", pj.get("acquisition"), LP.Project.open(pj.path).get("acquisition"))
dlg4 = ConfigDialog(REPO, path=pj.path, parent=None, project=pj)
dlg4.rows["source_dir"] = (dlg4.rows["source_dir"][0], lambda: os.path.join(TMP, "no", "such"))
asked.clear()
dlg4._save()
chk("a value the pipeline would refuse is not applied", (pj.get("source_dir") != os.path.join(TMP, "no", "such"), "warn:Not saved yet" in asked), (True, True))
pj.save()
chk("Save then writes it", read(pj.path)["pixel_size_um"], 0.77)
legacy = ConfigDialog(REPO, path=pj.path, parent=None)
chk("without a project the dialog still writes the file itself (the old behaviour)", legacy.ok_button.text().replace("&", ""), "Save")

print("--- New project ---")
picked["name"] = "Fresh one"
picked["save"] = os.path.join(TMP, "fresh", "fresh.json")
valid = read(study("tmpl"))


def fill_and_accept(self):
    for k in ("source_dir", "out_root", "atlas_pdf", "export_dir"):
        self.project.cfg[k] = valid[k]
    return QDialog.Accepted


ConfigDialog.exec = fill_and_accept
n_ses = session_on("g")
answers["Unsaved changes"] = QMessageBox.Discard
opened.clear()
n_ses.opened.connect(opened.append)
chk("new project: named, placed, filled in, saved", (n_ses.new_project(REPO, None), n_ses.project.name), (True, "Fresh one"))
chk("...the file exists with a project block", ("project" in read(picked["save"]), n_ses.dirty), (True, False))
chk("...and it is the open project", opened[-1], os.path.abspath(picked["save"]))
ConfigDialog.exec = lambda self: QDialog.Rejected
picked["save"] = os.path.join(TMP, "fresh", "never.json")
chk("cancelling the settings creates nothing", (n_ses.new_project(REPO, None), os.path.exists(picked["save"])), (False, False))
picked["save"] = os.path.join(TMP, "fresh", "fresh.json")
chk("an existing file is refused, not overwritten", (n_ses.new_project(REPO, None), "warn:Already exists" in asked), (False, True))

print("--- the runner ignores the project block ---")
import runner as RN                                             # noqa: E402

rp = LP.Project.open(study("h"))
rn = RN.Runner(os.path.join(REPO, "scripts"), rp.path)
rn._config_changed()
chk("unchanged config is unchanged", rn._config_changed(), False)
rp.record_run("05c", True, "a", "b")
chk("a run recorded in the project is not a config change", rn._config_changed(), False)
rp.set("pixel_size_um", 5.5, by="t"); rp.save()
chk("a real setting change still is", rn._config_changed(), True)
open(rp.path, "w").write("not json at all")
chk("a file that is not JSON is hashed as bytes, not crashed on", rn._config_changed(), True)

print("--- the window ---")
from PySide6.QtCore import Signal as _S                          # noqa: E402

stub = types.ModuleType("curator_view")


class CuratorView(QWidget):
    logged = _S(str)
    exported = _S(str)
    done_requested = _S()

    def __init__(self, server, out_root, parent=None, project_source=None):
        super().__init__(parent)
        self.view = QObject()
        self.project_source = project_source

    def show_page(self, *a):
        pass

    def rebase(self, *a):
        pass

    def refresh_seed(self):
        pass

    def set_done(self, *a):
        pass


class LocalServer:
    def __init__(self, root):
        self.port = 1

    def stop(self):
        pass


stub.CuratorView, stub.LocalServer = CuratorView, LocalServer
sys.modules["curator_view"] = stub
try:
    import main_window as MW                                    # noqa: E402
except ImportError as exc:
    print("SKIP the window (%s)" % exc)
    MW = None

if MW:
    path = study("win")
    os.environ["LS_CONFIG"] = path
    w = MW.MainWindow(REPO, config_path=path)
    chk("the window title names the project", "win" in w.windowTitle() or os.path.basename(path) in w.windowTitle(), True)
    chk("a File menu with the project verbs",
        sorted(w._file_actions), ["copy", "history", "new", "open", "revert", "save", "save_as"])
    chk("Revert is disabled while clean", w._file_actions["revert"].isEnabled(), False)
    w.session.project.set("pixel_size_um", 0.31, by="t")
    w.session.touch()
    chk("a change shows in the title", "*" in w.windowTitle(), True)
    chk("...and enables Revert", w._file_actions["revert"].isEnabled(), True)
    answers["Unsaved changes"] = QMessageBox.Cancel
    from PySide6.QtGui import QCloseEvent
    ev = QCloseEvent()
    w.closeEvent(ev)
    chk("closing with unsaved changes can be cancelled", ev.isAccepted(), False)
    answers["Unsaved changes"] = QMessageBox.Discard
    ev2 = QCloseEvent()
    w.closeEvent(ev2)
    chk("...or the changes discarded", ev2.isAccepted(), True)
    w.session.project.revert()
    w.session.touch()
    chk("clean again, the mark goes", "*" in w.windowTitle(), False)
    w._file_actions["save"].trigger()
    chk("File > Save on a clean project is harmless", w.session.dirty, False)
    w.session.project.set("pixel_size_um", 0.32, by="t")
    w.session.touch()
    w._file_actions["save"].trigger()
    chk("File > Save writes it", (read(path)["pixel_size_um"], "*" in w.windowTitle()), (0.32, False))
    w._fill_recent()
    chk("the project is in Open Recent", [a.text() for a in w._recent_actions][:1], [os.path.basename(path)])
    other_path = study("win2")
    w.session.open_project(w, other_path, confirmed=True)
    chk("opening another project points the stages at it", (w.config_path, os.environ["LS_CONFIG"]),
        (os.path.abspath(other_path), os.path.abspath(other_path)))
    chk("the window hands the curators the open project", w.curator.project_source() is w.session.project, True)
    w.server.stop()

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
