"""The app asks about reagent controls and confirms them.

New study asks "Reagent controls in this folder?" (No, the default / Yes / Not
sure, check for me) and, on Create, opens the confirm list when the answer is
Yes or Not sure, or when the name scan found a strong match anyway. Set up >
Slides opens the same list. The list writes `reagent_controls.files`,
`.scenes`, `.rejected` and `.paired_with` and nothing else.

Also here, because they are the operator-facing ends of the same feature: the
workbook check leaves an animal that is only reagent controls off its missing
list, the Results page quotes the limitation text 06c would, the curators carry
the flag into each section's record, and 01d labels a control's caption.

    python tests/test_reagent_dialog.py
"""

import csv
import faulthandler
import gc
import json
import os
import sys
import tempfile
import time

# A crash at interpreter exit (CI run 44737876800 segfaulted after "all passed")
# names its frame instead of a bare "Segmentation fault".
faulthandler.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import use_temp_study, load_stage                # noqa: E402

STUDY = use_temp_study()
os.environ["LS_HOME"] = tempfile.mkdtemp()

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


src = open(os.path.join(HERE, "test_autodetect.py")).read().split('print("--- one scan per marker')[0]
ns = {"__name__": "autodetect_helpers", "__file__": os.path.join(HERE, "test_autodetect.py")}
exec(compile(src, "test_autodetect_helpers", "exec"), ns)
xml, write_czi = ns["xml"], ns["write_czi"]
DAPI, AF568 = ns["DAPI"], ns["AF568"]

from PySide6.QtCore import QCoreApplication, QEvent             # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox         # noqa: E402

app = QApplication.instance() or QApplication([])
warned = []
QMessageBox.warning = staticmethod(lambda p, t, x, *a, **k: warned.append(x))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

import autodetect as AD                                         # noqa: E402
import ls_project as LP                                         # noqa: E402
import reagent_dialog as RD                                     # noqa: E402
from new_study import NewStudyDialog                            # noqa: E402

opened = []
RD.ReagentControlsDialog.exec = lambda self: (opened.append(self), 0)[1]


def settle(dlg):
    end = time.monotonic() + 20
    while dlg.slide_read.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        QCoreApplication.processEvents()


def folder(name, files):
    path = os.path.join(STUDY.root, name)
    os.makedirs(path)
    for f in files:
        write_czi(os.path.join(path, f), xml([DAPI, AF568]))
    return path


def new_study(src, out, answer=None):
    dlg = NewStudyDialog()
    dlg.slides.edit.setText(src)
    dlg._read_slides()
    settle(dlg)
    dlg.output.edit.setText(os.path.join(STUDY.root, out))
    dlg.thickness.setValue(14.0)
    if answer:
        dlg.reagent_buttons[answer].setChecked(True)
    dlg._create()
    return dlg


print("--- New study: the question ---")
PLAIN = folder("plain", ["LS45_1a.czi", "LS45_2a.czi"])
opened.clear()
dlg = new_study(PLAIN, "o_default")
chk("default_no: No is checked on a fresh dialog",
    NewStudyDialog().reagent_buttons["no"].isChecked(), True)
chk("...the study is made", os.path.isfile(AD.study_file(os.path.join(STUDY.root, "o_default"))))
chk("...Create writes declared == no", dlg.project.get("reagent_controls.declared"), "no")
chk("...and saves it", LP.Project.open(dlg.path).get("reagent_controls.declared"), "no")
chk("...No with nothing in the names opens no confirm list", len(opened), 0)
chk("no_groups_by_animal_written: New study writes no groups",
    dlg.project.get("groups.by_animal") or {}, {})

opened.clear()
dlg = new_study(PLAIN, "o_unsure", answer="unsure")
chk("unsure_saved: Not sure writes unsure", dlg.project.get("reagent_controls.declared"),
    "unsure")
chk("...and opens the confirm list to check", len(opened), 1)
chk("empty_before_screen: with nothing found, the list says the screen runs in Prepare",
    opened[0].empty.text().count("Prepare") == 1 and "has proposed none"
    not in opened[0].empty.text() if opened else None, True)

print("--- New study: a strong match with No ---")
STRONG = folder("strong", ["LS45_1a.czi", "LS45_noPrimary.czi", "LS45_NC.czi"])
opened.clear()
dlg = new_study(STRONG, "o_strong")
chk("strong_match_opens_dialog: No and LS45_noPrimary.czi still opens the list", len(opened), 1)
chk("...the dialog is the reagent controls one",
    isinstance(opened[0], RD.ReagentControlsDialog) if opened else None, True)
d = opened[0] if opened else None
row = d.row_for("LS45_noPrimary.czi") if d else None
chk("...the strong row is pre-ticked", row is not None and row.accept.isChecked(), True)
chk("...with kind no_primary", row.kind.currentData() if row else None, "no_primary")
chk("...its token is shown", row is not None and "noPrimary" in row.label.text(), True)
weak = d.row_for("LS45_NC.czi") if d else None
chk("weak_not_preticked: LS45_NC.czi is listed", weak is not None, True)
chk("...unticked", weak is not None and weak.accept.isChecked(), False)
chk("...and says why", weak is not None and "ambiguous" in weak.label.text(), True)

print("--- the confirm list on its own ---")
SRC = folder("rc", ["LS45_1a.czi", "LS45_2a.czi", "LS45_noPrimary.czi", "NC_3.czi",
                    "LS45_3nc.czi", "LS46_1a.czi"])
OUT = os.path.join(STUDY.root, "rc_out")
os.makedirs(os.path.join(OUT, "manifest"))
os.makedirs(os.path.join(OUT, "qc"))
project = LP.Project.open(STUDY.path)
project.set("source_dir", SRC, by="test")
project.set("out_root", OUT, by="test")
project.set("slide_naming.pattern",
            r"^(?P<subject>[A-Za-z]+\d+)_(?P<slide>\d+)(?P<replicate>[a-z]*)\.czi$", by="test")
project.set("acquisition.channels", [
    {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
    {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1},
    {"name": "PCNA", "role": "marker", "czi_name": "AF488", "index": 2}], by="test")
project.set("groups.order", ["control", "NC"], by="test")
project.set("reagent_controls.declared", "yes", by="test")
before = json.dumps(project.cfg, sort_keys=True)

dlg = RD.ReagentControlsDialog(project, SRC)
nc = dlg.row_for("NC_3.czi")
chk("NC_3.czi is a group label here, so the weak token is not proposed", nc, None)
nc = dlg.add_manual("NC_3.czi")
chk("subject_field_only_when_unparsed: shown for NC_3.czi", nc.subject_box.isHidden(), False)
chk("...filled with a made-up ID and the slide from the name",
    (nc.subject.text(), nc.slide.value()), ("RC1", 3))
three = dlg.add_manual("LS45_3nc.czi")
chk("...and hidden for LS45_3nc.czi, which parses", three.subject_box.isHidden(), True)
chk("...a manual row is sourced manual", three.source, "manual")
chk("...the markers default to all", [b.isChecked() for b in three.markers.values()],
    [True, True])
chk("the help names the kinds in plain words",
    all(w in dlg.help.text() for w in ("without the primary antibody", "isotype", "unstained")),
    True)

np_row = dlg.row_for("LS45_noPrimary.czi")
chk("the strong row is listed and pre-ticked", np_row is not None and np_row.accept.isChecked(),
    True)
chk("...a file whose name does not parse shows the Animal ID field",
    np_row.subject_box.isHidden(), False)
np_row.subject.setText("LS45")
np_row.slide.setValue(9)
nc.accept.setChecked(True)
three.reject.setChecked(True)
chk("the dialog writes nothing before Save", json.dumps(project.cfg, sort_keys=True), before)
chk("Save works", dlg.save(), True)
files = project.get("reagent_controls.files") or {}
ent = files.get("LS45_noPrimary.czi") or {}
chk("accept_and_reject_written: Accept writes the entry",
    {k: ent.get(k) for k in ("kind", "markers", "source", "pattern")},
    {"kind": "no_primary", "markers": [], "source": "name", "pattern": "noPrimary"})
chk("...with the Animal ID and slide typed", (ent.get("subject"), ent.get("slide")),
    ("LS45", 9))
chk("...an unparsed manual file carries its made-up ID",
    {k: files.get("NC_3.czi", {}).get(k) for k in ("subject", "slide", "source")},
    {"subject": "RC1", "slide": 3, "source": "manual"})
chk("...Not a control writes the name to rejected",
    project.get("reagent_controls.rejected"), ["LS45_3nc.czi"])
chk("...declared is left alone", project.get("reagent_controls.declared"), "yes")
chk("...and nothing outside reagent_controls changed (the decision log aside)",
    sorted(k for k in project.cfg if k != "project" and json.dumps(project.cfg[k], sort_keys=True)
           != json.dumps(json.loads(before).get(k), sort_keys=True)),
    ["reagent_controls"])
rc_keys = {k for k in (project.get("reagent_controls") or {}) if not k.startswith("_")}
chk("...the block holds only declared and what the dialog writes",
    rc_keys <= {"declared", "files", "scenes", "rejected", "paired_with"}, True)
decided = [r for r in project.block.get("decisions", []) if r["key"].startswith("reagent_controls.")
           and r["by"] == "reagent controls"]
chk("...recorded as by reagent controls", len(decided) >= 2, True)

again = RD.ReagentControlsDialog(project, SRC)
chk("a rejected name is not proposed again", again.row_for("LS45_3nc.czi"), None)
chk("a confirmed one is listed, ticked",
    again.row_for("LS45_noPrimary.czi") is not None
    and again.row_for("LS45_noPrimary.czi").accept.isChecked(), True)
again.row_for("NC_3.czi").reject.setChecked(True)
again.save()
chk("un-confirming moves it to rejected",
    ("NC_3.czi" in (project.get("reagent_controls.files") or {}),
     "NC_3.czi" in project.get("reagent_controls.rejected")), (False, True))

print("--- the signal screen's candidates ---")
with open(os.path.join(OUT, "qc", "reagent_screen.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["scene_uid", "file", "marker", "f1", "f2", "f3", "z1", "z2", "z3",
                "n_features", "candidate", "rank", "status", "note"])
    w.writerow(["LS46_s01a_sc02", "LS46_1a.czi", "pERK", 1, 1, 1, 4, 4, 4, 3, True, 1,
                "candidate", ""])
    w.writerow(["LS46_s01a_sc02", "LS46_1a.czi", "PCNA", 1, 1, 1, 0, 0, 0, 3, False, "",
                "", ""])
    w.writerow(["LS45_s01a_sc01", "LS45_1a.czi", "pERK", 1, 1, 1, 1, 1, 1, 3, False, "",
                "review", ""])
scr = RD.ReagentControlsDialog(project, SRC)
cand = scr.row_for("LS46_s01a_sc02")
chk("screen_candidates_listed: the candidate is listed", cand is not None, True)
chk("...with source signal", cand.source if cand else None, "signal")
chk("...not ticked for you: the screen never assigns", cand.accept.isChecked() if cand else None,
    False)
chk("...the marker it was a candidate for is ticked",
    {m: b.isChecked() for m, b in cand.markers.items()} if cand else None,
    {"pERK": True, "PCNA": False})
rev = scr.row_for("LS45_s01a_sc01")
chk("review_rows_listed: a review row (one measure only) is listed too", rev is not None, True)
chk("...unticked", rev is not None and rev.accept.isChecked(), False)
chk("...and says it is weaker evidence",
    rev is not None and "weaker evidence" in rev.label.text(), True)
chk("...a candidate row does not", "weaker evidence" in cand.label.text(), False)
cand.accept.setChecked(True)
scr.save()
chk("...Accept writes a scene entry",
    {k: (project.get("reagent_controls.scenes") or {}).get("LS46_s01a_sc02", {}).get(k)
     for k in ("kind", "markers", "source", "pattern")},
    {"kind": "no_primary", "markers": ["pERK"], "source": "signal", "pattern": None})

print("--- pairing ---")
with open(os.path.join(OUT, "manifest", "manifest_scenes.csv"), "w", newline="",
          encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["scene_uid", "file", "animal", "marker_channel", "reagent_control"])
    w.writeheader()
    for uid, f, a in (("LS45_s09_sc01", "LS45_noPrimary.czi", "LS45"),
                      ("LS45_s09_sc02", "LS45_noPrimary.czi", "LS45"),
                      ("LS45_s01a_sc01", "LS45_1a.czi", "LS45"),
                      ("LS45_s02a_sc01", "LS45_2a.czi", "LS45"),
                      ("LS46_s01a_sc01", "LS46_1a.czi", "LS46")):
        w.writerow({"scene_uid": uid, "file": f, "animal": a, "marker_channel": "pERK",
                    "reagent_control": ""})
pd = RD.ReagentControlsDialog(project, SRC)
prow = pd.row_for("LS45_noPrimary.czi")
chk("pairing_override: the default pairing is shown first", prow.pairing_kind, "same_animal")
chk("...in words on the row", "same animal" in prow.pairing_label.text(), True)
pd.change_pairing("LS45_noPrimary.czi", ["LS46_s01a_sc01"])
chk("...Change pairing shows the operator's choice", prow.pairing_kind, "operator")
pd.save()
chk("...and Save writes paired_with",
    project.get("reagent_controls.paired_with"), {"LS45_noPrimary.czi": ["LS46_s01a_sc01"]})
chk("...the choices offered are stained sections, not the control's own",
    "LS45_s09_sc02" in pd.stained_choices("LS45_noPrimary.czi"), False)
chk("...a pairing change alone says to run 06a again",
    ("06a" in pd.rerun_text, "00  Read slide headers" in pd.rerun_text), (True, False))

print("--- a confirmed row: Decide later keeps it, Un-confirm removes it ---")
stored = dict(project.get("reagent_controls.files")["LS45_noPrimary.czi"])
keep = RD.ReagentControlsDialog(project, SRC)
krow = keep.row_for("LS45_noPrimary.czi")
chk("decide_later_keeps: a confirmed row offers Un-confirm",
    krow.unconfirm is not None and krow.unconfirm.text(), "Un-confirm")
chk("...a row never confirmed does not", keep.add_manual("LS46_1a.czi").unconfirm, None)
krow.subject.setText("LS99")            # an edit, then Decide later: the edit is not kept
krow.later.setChecked(True)
chk("...Save works", keep.save(), True)
chk("...Decide later leaves the stored entry exactly as it was, Animal ID and slide too",
    project.get("reagent_controls.files")["LS45_noPrimary.czi"], stored)
chk("...nothing changed, so no stage to re-run", (keep.rerun_text, keep.result_text), ("", ""))
un = RD.ReagentControlsDialog(project, SRC)
un.row_for("LS45_noPrimary.czi").unconfirm.setChecked(True)
un.save()
chk("unconfirm_removes: Un-confirm removes the entry",
    "LS45_noPrimary.czi" in (project.get("reagent_controls.files") or {}), False)
chk("...without marking it not a control",
    "LS45_noPrimary.czi" in (project.get("reagent_controls.rejected") or []), False)
chk("...and says so", "Un-confirmed" in un.result_text and "LS45_noPrimary.czi" in un.result_text,
    True)
chk("rerun_listed: Save names the stages to run again, in plain words",
    [w for w in ("00  Read slide headers", "02  Pair", "01  Export section overviews",
                 "05b", "05c", "06a") if w not in un.rerun_text], [])
chk("...in the result shown on Save too", un.rerun_text in un.result_text, True)
back = RD.ReagentControlsDialog(project, SRC)
chk("...an un-confirmed strong name is proposed again, pre-ticked",
    back.row_for("LS45_noPrimary.czi") is not None
    and back.row_for("LS45_noPrimary.czi").accept.isChecked(), True)
back.row_for("LS45_noPrimary.czi").subject.setText(stored["subject"])
back.row_for("LS45_noPrimary.czi").slide.setValue(stored["slide"])
back.save()

print("--- the workbook check ---")
import workbook_check as WC                                     # noqa: E402
WB = os.path.join(STUDY.root, "wb_out")
os.makedirs(os.path.join(WB, "manifest"))
with open(os.path.join(WB, "manifest", "manifest_files.csv"), "w", newline="",
          encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["file", "animal", "reagent_control"])
    w.writeheader()
    w.writerow({"file": "LS45_1a.czi", "animal": "LS45", "reagent_control": ""})
    w.writerow({"file": "NC_3.czi", "animal": "RC1", "reagent_control": ""})
with open(os.path.join(WB, "manifest", "manifest_scenes.csv"), "w", newline="",
          encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["scene_uid", "file", "animal", "marker_channel", "reagent_control"])
    w.writeheader()
    for m in ("pERK", "PCNA"):
        w.writerow({"scene_uid": "LS45_s01a_sc01", "file": "LS45_1a.czi", "animal": "LS45",
                    "marker_channel": m, "reagent_control": ""})
        w.writerow({"scene_uid": "RC1_s03_sc01", "file": "NC_3.czi", "animal": "RC1",
                    "marker_channel": m, "reagent_control": ""})
wcfg = {"out_root": WB, "acquisition": {"layout": "paired", "markers": ["pERK", "PCNA"]},
        "reagent_controls": {"files": {"NC_3.czi": {"kind": "no_primary", "markers": [],
                                                    "subject": "RC1", "slide": 3}}}}
chk("workbook_skips_control_only_animal: RC1 is not one of the study's animals",
    WC.study_animals(wcfg), ({"LS45": 1}, "manifest"))
wcfg["reagent_controls"]["files"]["NC_3.czi"]["markers"] = ["pERK"]
chk("...but an animal stained for another marker on that slide stays",
    sorted(WC.study_animals(wcfg)[0]), ["LS45", "RC1"])
wcfg["reagent_controls"]["files"]["NC_3.czi"]["markers"] = []
chk("...the manifest's display column is never what decides",
    sorted(WC.study_animals(dict(wcfg, reagent_controls={}))[0]), ["LS45", "RC1"])
FN = folder("wb_names", ["LS45_1a.czi", "LS47_1a.czi", "LS47_2a.czi"])
ncfg = {"out_root": "", "source_dir": FN,
        "slide_naming": {"pattern": r"^(?P<subject>[A-Za-z]+\d+)_(?P<slide>\d+)(?P<replicate>[a-z]*)\.czi$"},
        "reagent_controls": {"files": {"LS47_1a.czi": {"kind": "isotype", "markers": []},
                                       "LS47_2a.czi": {"kind": "isotype", "markers": []}}}}
chk("...from the file names too: an animal all of whose slides are controls is left out",
    WC.study_animals(ncfg), ({"LS45": 1}, "slide file names"))

print("--- the Results page ---")
import ls_reagent_report as LR                                  # noqa: E402
import workflow as W                                            # noqa: E402
import screens as SC                                            # noqa: E402
RES = os.path.join(STUDY.root, "res_out")
os.makedirs(os.path.join(RES, "results"))
results = SC.StageListScreen(W.SCREENS["results"])
results.show_limitation(RES)
chk("results_text_switches: no report file, the existing limitation text",
    W.limitation_text(RES), LR.NO_CONTROL_TEXT)
chk("...shown on the Results page", "not defensible" in results.limitation.text(), True)
with open(os.path.join(RES, "results", LR.REPORT_CSV), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, LR.COLUMNS)
    w.writeheader()
    w.writerow({"scene_uid": "LS45_s09_sc01", "kind": "no_primary", "marker": "pERK",
                "region": "", "note": "confirmed reagent control, not measured yet: 05c"})
results.show_limitation(RES)
chk("...a no_primary row not measured yet: still the existing text",
    "not defensible" in results.limitation.text(), True)
with open(os.path.join(RES, "results", LR.REPORT_CSV), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, LR.COLUMNS)
    w.writeheader()
    w.writerow({"scene_uid": "LS45_s09_sc01", "kind": "no_primary", "marker": "pERK",
                "region": "Dm", "n_nuclei": 40, "n_positive": 1, "pct_positive": 2.5})
results.show_limitation(RES)
chk("...a no_primary row: names the Reagent controls sheet",
    "Reagent controls" in results.limitation.text(), True)
chk("...the text is limitation_text's", W.limitation_text(RES),
    LR.limitation_text([{"kind": "no_primary", "marker": "pERK", "pct_positive": "2.5"}]))
chk("...and names the marker it covers", "pERK" in results.limitation.text(), True)
chk("the other screens carry no such line",
    getattr(SC.StageListScreen(W.SCREENS["measure"]), "limitation", None), None)

print("--- Set up > Slides ---")
import main_window as MW                                        # noqa: E402
win = MW.MainWindow(REPO, config_path=STUDY.path)
s = win.setup
chk("Set up > Slides has a Reagent controls button", hasattr(s, "reagent_btn"), True)
opened.clear()
s.session.project.set("source_dir", SRC, by="test")
s.reagent_btn.click()
chk("...which opens the same dialog", len(opened) == 1
    and isinstance(opened[0], RD.ReagentControlsDialog), True)
told = []
s.logged.connect(told.append)
_exec = RD.ReagentControlsDialog.exec
RD.ReagentControlsDialog.exec = lambda self: (setattr(self, "changed", True),
                                              setattr(self, "result_text", "run 00 again"), 1)[2]
s.reagent_btn.click()
RD.ReagentControlsDialog.exec = _exec
chk("...and what Save says to re-run reaches the app's message area", "run 00 again" in told,
    True)

print("--- 01d: a control's caption ---")
D1 = load_stage("01d_contactsheets.py", name="lsstage_01d_rcdlg")
lk = {("LS45_s09_sc01", "pERK"): {"kind": "no_primary"}}
r1 = {"scene_uid": "LS45_s09_sc01", "marker_channel": "pERK", "animal": "LS45",
      "section_order": 3, "slide": 9, "scene_index": 1}
r2 = dict(r1, scene_uid="LS45_s01a_sc01", slide=1)
chk("a reagent control's caption says so", "reagent control" in D1.caption(r1, lk), True)
chk("...a stained section's does not", "reagent control" in D1.caption(r2, lk), False)
chk("...the gallery cell too", "reagent control" in D1.gallery_caption(r1, lk, ""), True)

print("--- the curators' records ---")
K = load_stage("04k_level_curator.py", name="lsstage_04k_rcdlg")
kinds = K.reagent_kinds(project.cfg)
chk("04k: a section of a confirmed control file carries its kind",
    (kinds.get("LS45_s09_sc01"), kinds.get("LS45_s01a_sc01")), ("no_primary", None))



def teardown():
    """Take every Qt object down while the application still exists.

    Left to interpreter exit, the main window (its curator web view and local
    server), the dialogs held in `opened` and the New study dialogs (each with
    a SlideRead) are destroyed in whatever order Python's finalizer picks,
    after the QApplication - which crashed on CI. The order here is the one
    the app itself uses: stop the server, close, delete, let the deletions run.
    """
    # A slide read runs on a plain thread and signals back when it ends; one
    # still running at exit signals into a deleted object. Set up > Slides
    # starts one when source_dir changes, moments before this.
    import slide_read as SRD
    for job in [o for o in gc.get_objects() if isinstance(o, SRD.SlideRead)]:
        job.wait(60000)
    win.server.stop()
    opened.clear()
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    gc.collect()
    app.processEvents()


teardown()
print("\n%s" % ("FAILED: %s" % failures if failures else "all passed"))
sys.stdout.flush()
sys.exit(1 if failures else 0)
