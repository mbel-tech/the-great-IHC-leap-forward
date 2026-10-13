"""Sharing a project from the app: Export a copy, and opening a zip copy as a new project.

Offscreen with scripted dialogs. What matters here is the wiring and the safety defaults: a copy
is blinded unless the person confirms otherwise, a zip is only made when extras are asked for,
and opening a zip never writes anything before the person has chosen where it goes.

Run:  python tests/test_bundle_dialog.py
"""

import json
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
except ImportError:
    print("SKIP PySide6 is not installed - the stages do not need it")
    sys.exit(0)

from _fixture import write_study                              # noqa: E402

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")

import bundle_dialog as BD                                      # noqa: E402
import ls_bundle as LB                                          # noqa: E402
import ls_project as LP                                         # noqa: E402
import project_session as PS                                    # noqa: E402

app = QApplication([])
failures = []
SECRET = "TREATMENT-X-SECRET"


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(72) + " " + repr(got)[:80])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


asked, answers, picked = [], {}, {}
QMessageBox.question = staticmethod(lambda p, t, x, *a, **k: (asked.append(t), answers.get(t, QMessageBox.No))[1])
QMessageBox.warning = staticmethod(lambda p, t, x, *a, **k: (asked.append("warn:" + t), answers.get("warn:" + t, QMessageBox.No))[1])
QMessageBox.information = staticmethod(lambda p, t, x, *a, **k: asked.append("info:" + t))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (picked.get("save", ""), ""))


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


d = os.path.join(TMP, "src")
os.makedirs(d)
cfg_path = write_study(d, groups={"order": ["A", SECRET], "by_animal": {"LS1": SECRET}})
out = os.path.join(d, "out")
w(os.path.join(out, "reformatted", "rotation_overrides.csv"), "scene_uid,extra_rotation\nA,90\n")
sess = PS.ProjectSession(LP.Project.open(cfg_path))
sess.project.write_curation("ls_roi_curator_v1", '{"LS1_s1":{"plate":"p"}}', out_root=out)

print("--- the options window ---")
dlg = BD.ExportCopyDialog(sess.project.cfg, out)
chk("blinded is the default, extras are off", dlg.choices(), {"blinded": True, "operator_csvs": False, "atlases": False})
chk("with no extras it says it is just the project file", "just the project file" in dlg.size.text().lower(), True)
dlg.operator.setChecked(True)
chk("with extras it says it is a zip, and how big", ".zip" in dlg.size.text() and "1 file" in dlg.size.text(), True)
dlg.blinded.setChecked(False)
asked.clear()
dlg._ok()
chk("unchecking blinded asks first, and No keeps the window open", ("warn:Include the treatment key?" in asked, dlg.result() == QDialog.Accepted), (True, False))
answers["warn:Include the treatment key?"] = QMessageBox.Yes
dlg._ok()
chk("...Yes accepts", dlg.result() == QDialog.Accepted, True)
answers.clear()

print("--- export a copy ---")
QDialog.exec = lambda self: QDialog.Accepted
picked["save"] = os.path.join(TMP, "share", "copy.json")
dest = sess.export_copy(None)
cp = json.load(open(dest))
chk("by default: one json, blinded, curation inside", (dest.endswith(".json"), cp["groups"]["by_animal"], cp["curation"]["ls_roi_curator_v1"]["LS1_s1"]["plate"]), (True, {}, "p"))
chk("...and no treatment value in the text", SECRET in open(dest).read(), False)
chk("...the original is untouched", json.load(open(cfg_path))["groups"]["by_animal"], {"LS1": SECRET})

sess.project.set("pixel_size_um", 0.5, record=False)
asked.clear()
answers["Save before running?"] = QMessageBox.Cancel
chk("an unsaved project is saved first, or the export is cancelled", (sess.export_copy(None), "Save before running?" in asked), (None, True))
answers["Save before running?"] = QMessageBox.Save
sess.project.revert(); sess.touch()


def zip_choices(self):
    return {"blinded": True, "operator_csvs": True, "atlases": False}


orig = BD.ExportCopyDialog.choices
BD.ExportCopyDialog.choices = zip_choices
picked["save"] = os.path.join(TMP, "share", "copy.zip")
zdest = sess.export_copy(None)
chk("with extras it is a zip holding them", (zdest.endswith(".zip"), "operator/reformatted/rotation_overrides.csv" in zipfile.ZipFile(zdest).namelist()), (True, True))
chk("...and the bundle verifies", LB.inspect(zdest)["blinded"], True)
BD.ExportCopyDialog.choices = orig
QDialog.exec = lambda self: QDialog.Rejected
chk("cancelling the options writes nothing", sess.export_copy(None), None)

print("--- opening a zip copy as a new project ---")
from config_dialog import ConfigDialog                          # noqa: E402

dest_project = os.path.join(TMP, "recv", "mine.json")
recv_out = os.path.join(TMP, "recv", "data")
os.makedirs(recv_out)
w(os.path.join(recv_out, "reformatted", "rotation_overrides.csv"), "my own rotations")
good = write_study(os.path.join(TMP, "recv_paths"))
vals = json.load(open(good))


def fill(self):
    for k in ("source_dir", "atlas_pdf", "export_dir"):
        self.project.cfg[k] = vals[k]
    self.project.cfg["out_root"] = recv_out
    return QDialog.Accepted


ConfigDialog.exec = fill
rs = PS.ProjectSession(LP.Project.open(write_study(os.path.join(TMP, "recv_host"))))
opened = []
rs.opened.connect(opened.append)
picked["save"] = dest_project
answers["Replace files?"] = QMessageBox.No
asked.clear()
chk("opening the zip makes a new project at the chosen place", (rs.open_project(None, zdest, confirmed=True), os.path.exists(dest_project)), (True, True))
chk("...which the window is told about", opened[-1], os.path.abspath(dest_project))
chk("...with the sender's curation inside and the receiver's own paths", (rs.project.curation_text("ls_roi_curator_v1"), rs.project.get("out_root")), ('{"LS1_s1":{"plate":"p"}}', recv_out))
chk("...the receiver's differing file was asked about, and kept", ("Replace files?" in asked, open(os.path.join(recv_out, "reformatted", "rotation_overrides.csv")).read()), (True, "my own rotations"))
rs.sync_curation(None)
chk("the curation copies the stages read appear on sync", os.path.exists(os.path.join(recv_out, "curation", "ls_roi_curator_v1.json")), True)

untouched = os.path.join(TMP, "recv2", "x.json")
picked["save"] = untouched
tam = os.path.join(TMP, "tam.zip")
with zipfile.ZipFile(zdest) as zin, zipfile.ZipFile(tam, "w") as zout:
    for i in zin.infolist():
        data = zin.read(i.filename)
        zout.writestr(i.filename, data + b"!" if i.filename.startswith("operator/") else data)
asked.clear()
chk("a tampered zip is refused before anything is written", (rs.open_project(None, tam, confirmed=True), "warn:Could not open the bundle" in asked, os.path.exists(untouched)), (False, True, False))
picked["save"] = ""
chk("choosing no place for it opens nothing", rs.open_project(None, zdest, confirmed=True), False)
picked["save"] = dest_project
chk("an existing file is never overwritten", (rs.open_project(None, zdest, confirmed=True), "warn:Already exists" in asked), (False, True))
ConfigDialog.exec = lambda self: QDialog.Rejected
picked["save"] = os.path.join(TMP, "recv3", "y.json")
chk("cancelling the settings writes nothing", (rs.open_project(None, zdest, confirmed=True), os.path.exists(picked["save"])), (False, False))

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
