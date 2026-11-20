"""New study asks for the marker names and the sampling workbook up front.

Before, New study asked for neither: the markers stayed as the dye names (or a
template placeholder like Marker1) until someone found Set up > Channels and
markers, and the sampling workbook was first looked at by 06b at Unblind, the
very end of a study. Now both are on the dialog, the markers filled in from the
slide headers once read, and Create saves them where Set up keeps them.

    python tests/test_new_study.py
"""

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
os.environ["LS_HOME"] = tempfile.mkdtemp()

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


# Reuse the header writers from test_autodetect (everything above its first scenario).
src = open(os.path.join(HERE, "test_autodetect.py")).read().split('print("--- one scan per marker')[0]
ns = {"__name__": "autodetect_helpers", "__file__": os.path.join(HERE, "test_autodetect.py")}
exec(compile(src, "test_autodetect_helpers", "exec"), ns)
xml, write_czi = ns["xml"], ns["write_czi"]
DAPI, AF568, AF488 = ns["DAPI"], ns["AF568"], ns["AF488"]

import autodetect as AD                                         # noqa: E402

PAIRED = os.path.join(STUDY.root, "paired")
MULTI = os.path.join(STUDY.root, "multi")
os.makedirs(PAIRED)
os.makedirs(MULTI)
for n in range(1, 3):
    write_czi(os.path.join(PAIRED, "LS22_%da.czi" % n), xml([DAPI, AF568]))
    write_czi(os.path.join(PAIRED, "LS22_%db.czi" % n), xml([DAPI, AF488]))
    write_czi(os.path.join(MULTI, "LS3%d_1a.czi" % n), xml([DAPI, AF488, AF568]))

print("--- the markers the headers name ---")
chk("paired: one per scan's marker channel", AD.markers_found(AD.detect(PAIRED)),
    ["AF568", "AF488"])
chk("multiplex: the marker channels of the table", AD.markers_found(AD.detect(MULTI)),
    ["AF488", "AF568"])
chk("nothing read: none", AD.markers_found(None), [])

print("--- naming them on a new project ---")
p, found = AD.new_project(PAIRED, os.path.join(STUDY.root, "o1"), 14, detection=AD.detect(PAIRED))
chk("paired names nothing wrong", AD.name_markers(p, {"AF568": "pERK", "AF488": "PCNA"}), [])
# Paired scans are matched to their marker by the channel name, so the dye
# name stays the marker and the antibody is what it stains - as the channel
# editor writes it.
chk("...the markers keep the scan names pairing needs", p.get("acquisition.markers"),
    ["AF568", "AF488"])
chk("...and what each stains is recorded", p.get("marker_identity"),
    {"AF568": "pERK", "AF488": "PCNA"})
chk("...the study is still valid", p.validate()[0], [])

p, found = AD.new_project(MULTI, os.path.join(STUDY.root, "o2"), 14, detection=AD.detect(MULTI))
chk("multiplex names nothing wrong", AD.name_markers(p, {"AF488": "PCNA", "AF568": "pERK"}), [])
chan = {c["czi_name"]: c["name"] for c in p.get("acquisition.channels")}
chk("...the channels are renamed, still found by their dye name", chan,
    {"DAPI": "DAPI", "AF488": "PCNA", "AF568": "pERK"})
chk("...the study is still valid", p.validate()[0], [])
p2, _ = AD.new_project(MULTI, os.path.join(STUDY.root, "o3"), 14, detection=AD.detect(MULTI))
chk("left as found changes nothing",
    (AD.name_markers(p2, {"AF488": "AF488", "AF568": ""}),
     [c["name"] for c in p2.get("acquisition.channels")]), ([], ["DAPI", "AF488", "AF568"]))
chk("two markers given one name is refused",
    bool(AD.name_markers(p2, {"AF488": "pERK", "AF568": "pERK"})), True)

print("--- the dialog ---")
from PySide6.QtCore import QCoreApplication                     # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox         # noqa: E402

app = QApplication.instance() or QApplication([])
warned = []
QMessageBox.warning = staticmethod(lambda p, t, x, *a, **k: warned.append(x))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

from new_study import NewStudyDialog                            # noqa: E402


def settle(dlg):
    end = time.monotonic() + 20
    while dlg.slide_read.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        QCoreApplication.processEvents()


book = os.path.join(STUDY.root, "sampling.xlsx")
open(book, "wb").close()
dlg = NewStudyDialog()
chk("no markers shown before the slides are read", dlg.marker_names(), {})
dlg.slides.edit.setText(PAIRED)
dlg._read_slides()
settle(dlg)
chk("after the read, a name per marker, the dye name to start with", dlg.marker_names(),
    {"AF568": "AF568", "AF488": "AF488"})
dlg.marker_edits["AF568"].setText("pERK")
chk("the dialog has a sampling workbook field", hasattr(dlg, "workbook"), True)
dlg.workbook.setValue(book)
out = os.path.join(STUDY.root, "made")
dlg.output.edit.setText(out)
dlg.thickness.setValue(14.0)
dlg._create()
chk("the study is made", os.path.isfile(AD.study_file(out)), True)
proj = dlg.project
chk("...with the marker named", (proj.get("marker_identity") or {}).get("AF568"), "pERK")
chk("...the other left as it was", "AF488" in (proj.get("marker_identity") or {}), False)
chk("...and the workbook kept as 06b's own option",
    proj.stage_options("join_sampling"), {"--xlsx": book})

import ls_project as LP                                         # noqa: E402
again = LP.Project.open(AD.study_file(out))
chk("both are in the saved study file",
    (again.get("marker_identity"), again.stage_options("join_sampling")),
    ({"AF568": "pERK"}, {"--xlsx": book}))

dlg = NewStudyDialog()
dlg.slides.edit.setText(MULTI)
dlg._read_slides()
settle(dlg)
dlg.marker_edits["AF488"].setText("pERK")
dlg.marker_edits["AF568"].setText("pERK")
out2 = os.path.join(STUDY.root, "made2")
dlg.output.edit.setText(out2)
dlg.thickness.setValue(14.0)
warned.clear()
dlg._create()
chk("one name for two markers is not made", os.path.exists(AD.study_file(out2)), False)
chk("...and says why", any("pERK" in w for w in warned), True)
chk("...with no workbook, none is kept", dlg.workbook.value(), "")

print("--- Set up > Slides: Check workbook ---")
# 06b's matching run early, on the fish the study has, writing nothing.
import openpyxl                                                 # noqa: E402
import workbook_check as WC                                     # noqa: E402

LIVE = ["Fish ID", "timepoint", "enviroment", "brackish Tank", "sea water  tank", "treatment",
        "Body weight(g)", "Fork Length (cm)", "Sex", "Brain for", "slicing IHC July 2025"]
good = os.path.join(STUDY.root, "groups.xlsx")
wb = openpyxl.Workbook()
wb.active.append(LIVE)
wb.active.append([22, 1, "brackish", "305", None, "control", None, None, "f", "IHC", "x"])
wb.save(good)

import main_window as MW                                        # noqa: E402

w = MW.MainWindow(REPO, config_path=STUDY.path)
s = w.setup
project = s.session.project
project.set("source_dir", PAIRED, by="test")
for key in ("slide_naming.pattern", "slide_naming.case_insensitive"):
    project.set(key, AD.detect(PAIRED).values[key], by="test")
chk("the study's fish, from the slide names before any manifest",
    WC.study_animals(project.cfg), ({"LS22": 4}, "slide file names"))
chk("Set up shows the workbook next to a Check button",
    hasattr(s, "workbook_field") and hasattr(s, "check_workbook_btn"), True)
s.workbook_field.setValue(good)
s.workbook_field.valueChanged.emit(good)
chk("...and keeps it as 06b's own option", project.stage_options("join_sampling"),
    {"--xlsx": good})
before = sorted(os.listdir(STUDY.root))
s.check_workbook_btn.click()
chk("...the check runs off the window's thread", s.workbook_check.busy, True)
end = time.monotonic() + 20
while s.workbook_check.busy and time.monotonic() < end:
    app.processEvents()
    time.sleep(0.005)
app.processEvents()
chk("...and says how many were matched", "1 of 1 animals (4 of 4 slides) matched to a group"
    in s.workbook_line.text(), True)
chk("...writing nothing", sorted(os.listdir(STUDY.root)), before)
chk("...not in the results either",
    os.path.exists(os.path.join(project.get("out_root"), "results", "animal_metadata.csv")),
    False)

print("--- Set up > Slides: Slide manifest ---")
import slide_manifest_dialog as SMD                             # noqa: E402
_shown = []
_mexec = SMD.SlideManifestDialog.exec
SMD.SlideManifestDialog.exec = lambda self: (_shown.append(self), 0)[1]
try:
    line_before = s.workbook_line.text()
    s.manifest_btn.click()
    chk("the manifest is built off the window's thread", s.manifest_job.busy, True)
    end = time.monotonic() + 20
    while s.manifest_job.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    chk("...and opens a window", len(_shown), 1)
    _m = _shown[0].manifest if _shown else {"rows": []}
    chk("...one row per animal, its treatment from the workbook",
        [(r["animal"], r["treatment"], r["files"], r["status"]) for r in _m["rows"]],
        [("LS22", "control", 4, "OK")])
    chk("...the button is back", (s.manifest_btn.isEnabled(), s.manifest_btn.text()),
        (True, "Slide manifest…"))
    chk("...the Check workbook line is left as it was", s.workbook_line.text(), line_before)
    chk("...writing nothing until Save", sorted(os.listdir(STUDY.root)), before)
finally:
    SMD.SlideManifestDialog.exec = _mexec

print("--- Set up > Slides: load the groups for Shotgun ---")
chk("blind until asked: no groups in the study", project.get("groups.by_animal") or {}, {})
chk("...and Remove is off", s.clear_groups_btn.isEnabled(), False)
from PySide6.QtWidgets import QMessageBox                       # noqa: E402
_q = QMessageBox.question
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
try:
    s.groups_btn.click()
    end = time.monotonic() + 20
    while s.workbook_check.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
finally:
    QMessageBox.question = _q
chk("Load keeps each fish's group for the deck", project.get("groups.by_animal"),
    {"LS22": "control"})
chk("...and the order the deck draws them in", project.get("groups.order"), ["control"])
chk("...recorded by name only, never by value",
    [d for d in project.block.get("decisions", []) if d["key"].startswith("groups.")
     and ("to" in d or "from" in d)], [])
chk("...the screen gives counts, not which fish",
    "control 1 animals" in s.groups_line.text() and "LS22" not in s.groups_line.text(), True)
chk("...and Remove turns on", s.clear_groups_btn.isEnabled(), True)
s.clear_groups_btn.click()
chk("Remove makes the study blind again",
    (project.get("groups.by_animal"), project.get("groups.order")), ({}, []))
chk("...and says so", "blind" in s.groups_line.text(), True)

print("--- Set up > Slides: Match the sheet ---")
import sheet_match as SM                                         # noqa: E402
other = os.path.join(STUDY.root, "other.xlsx")
wb = openpyxl.Workbook()
wb.active.append(["Code", "Animal", "Diet", "Week"])
wb.active.append(["a", "LS22", "high", 1])
wb.active.append(["b", "LS99", "low", 2])
wb.save(other)
dlg = SM.SheetMatchDialog(other, {"LS22": 4}, "slide file names", None)
roles = [c.currentText() for c in dlg.roles]
chk("the ID column is guessed from the slide names", roles[1], "Animal ID")
chk("...the check says the animal has a row", "1 of 1" in dlg.check.text(), True)
chk("...and no treatment yet: Save is off", dlg.save_btn.isEnabled(), False)
dlg.roles[2].setCurrentText("Treatment")
dlg.roles[3].setCurrentText("Treatment")
chk("two treatment columns make one group", "high \u00b7 1 1" in dlg.check.text(), True)
chk("...and Save turns on", dlg.save_btn.isEnabled(), True)
dlg.roles[0].setCurrentText("Animal ID")
chk("one ID column: choosing another moves it", dlg.roles[1].currentText(), "Keep")
chk("...and the check shows the slide has no row", "No row for LS22" in dlg.check.text(), True)
dlg.roles[1].setCurrentText("Animal ID")
chk("the window's choice", {k: v for k, v in dlg.mapping().items() if k != "fill"},
    {"id_column": "Animal", "group_columns": ["Diet", "Week"], "ignore_columns": [],
     "match": "auto"})
dlg.close()

_exec = SM.SheetMatchDialog.exec


def _save(self):
    self.roles[2].setCurrentText("Treatment")
    return True


SM.SheetMatchDialog.exec = _save
try:
    s.workbook_field.setValue(other)
    s.workbook_field.valueChanged.emit(other)
    s.match_sheet_btn.click()
    end = time.monotonic() + 20
    while s.workbook_check.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
finally:
    SM.SheetMatchDialog.exec = _exec
chk("Save keeps the choice in the study", (project.get("sampling") or {}).get("group_columns"),
    ["Diet"])
chk("...and the workbook is checked with it", "1 of 1 animals" in s.workbook_line.text(), True)

s.workbook_field.setValue(os.path.join(STUDY.root, "none.xlsx"))
s.check_workbook_btn.click()
end = time.monotonic() + 20
while s.workbook_check.busy and time.monotonic() < end:
    app.processEvents()
    time.sleep(0.005)
app.processEvents()
chk("a workbook that is not there is said so", "no workbook" in s.workbook_line.text(), True)

print("--- Set up > Slides: paste the sheet from the clipboard ---")
import ls_sampling as SAMPLING                                  # noqa: E402
chk("Set up has a Paste button", s.paste_sheet_btn.isEnabled(), True)
app.clipboard().setText("just one word")
chk("a clipboard with no table is refused", s.paste_sheet(), None)
chk("...and says so", "does not hold a table" in s.workbook_line.text(), True)
app.clipboard().setText("ID\tGroup\tTreatment\tDate to tank\r\n"
                        "LS22\tControl\tObserver\t25.11.04\r\n")
s.paste_sheet_btn.click()
pasted = os.path.join(project.folder(), SAMPLING.PASTED_NAME)
chk("Paste saves the table in the study folder and chooses it",
    (os.path.isfile(pasted), s.workbook_field.value(),
     project.stage_options("join_sampling").get("--xlsx")), (True, pasted, pasted))
chk("...and says what it kept", "1 fish rows and 4 columns" in s.workbook_line.text(), True)
project.set("sampling", {"id_column": "ID", "group_columns": ["Group", "Treatment"],
                         "match": "exact", "fill": []}, by="test")
s.check_workbook_btn.click()
end = time.monotonic() + 20
while s.workbook_check.busy and time.monotonic() < end:
    app.processEvents()
    time.sleep(0.005)
app.processEvents()
chk("...and the pasted sheet checks like a workbook",
    "1 of 1 animals" in s.workbook_line.text(), True)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
try:
    app.clipboard().setText("ID\tGroup\nLS22\tother\n")
    chk("pasting again over it asks first, and No keeps the old sheet",
        (s.paste_sheet(), SAMPLING.table(pasted)[1][0][1]), (None, "Control"))
finally:
    QMessageBox.question = _q

print("--- a sheet row two animals match: refused in red, left out by name ---")
import screens as SC                                             # noqa: E402
shared_book = os.path.join(STUDY.root, "shared.xlsx")
wb = openpyxl.Workbook()
for r in (["Fish", "Group", "Tank", "Sex"], [1, "control", 305, "F"], [5, "exercise", 303, "M"]):
    wb.active.append(r)
wb.save(shared_book)
_h, _r = SAMPLING.table(shared_book)
_m = {"id_column": "Fish", "group_columns": ["Group"], "match": "number", "fill": []}
_sum = SM.summary(_h, _r, ["LS1", "LS01", "LS5"], _m)
chk("Match the sheet: a shared row on a four-column sheet is named",
    (_sum["shared"], _sum["matched"]), ([("1", ["LS01", "LS1"])], 1))
_join = SAMPLING.join
SAMPLING.join = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
try:
    import contextlib as _cl
    import io as _sio
    _err = _sio.StringIO()
    with _cl.redirect_stderr(_err):
        _sum = SM.summary(_h, _r, ["LS5"], _m)
    chk("...and a fault in the join is a problem shown, not raised out of the slot",
        any("boom" in p for p in _sum["problems"]), True)
    chk("...naming the exception's type",
        any("ValueError: boom" in p for p in _sum["problems"]), True)
    chk("...with its traceback logged",
        ("Traceback" in _err.getvalue(), "ValueError: boom" in _err.getvalue()),
        (True, True))
finally:
    SAMPLING.join = _join
_rep = SAMPLING.check(shared_book, {"LS1": 1, "LS01": 1, "LS5": 1}, _m)
s._load_groups_after_check = False
s._workbook_checked(_rep)
chk("Check workbook: a shared row is shown in red",
    SC.COLOUR["failed"] in s.workbook_line.styleSheet(), True)
_dup = SAMPLING.check(shared_book, {"LS5": 1}, _m)
_dup["duplicate"] = ["LS7"]
s._workbook_checked(_dup)
chk("...and so is an animal on two rows", SC.COLOUR["failed"] in s.workbook_line.styleSheet(),
    True)
_told = []
QMessageBox.question = staticmethod(
    lambda _p, _t, text, *a, **k: (_told.append(text), QMessageBox.No)[1])
try:
    s._keep_groups(_rep)
finally:
    QMessageBox.question = _q
chk("Load the groups: the shared animals are listed as left out",
    bool(_told) and "Left out" in _told[0] and "LS01" in _told[0].split("Left out")[-1]
    and "LS1" in _told[0].split("Left out")[-1], True)

print("\n%s" % ("FAILED: %s" % failures if failures else "all passed"))
sys.exit(1 if failures else 0)
