"""The project history window: what it shows, what it withholds, and the notes.

The row builders are plain functions, tested directly; the window is driven offscreen.

Run:  python tests/test_project_history.py
"""

import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from PySide6.QtWidgets import QApplication, QTableWidget
except ImportError:
    print("SKIP PySide6 is not installed - the stages do not need it")
    sys.exit(0)

from _fixture import write_study                              # noqa: E402

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")

import ls_project as LP                                         # noqa: E402
import project_history as PH                                    # noqa: E402
import project_session as PS                                    # noqa: E402

app = QApplication([])
failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(70) + " " + repr(got)[:80])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


p = LP.Project.open(write_study(TMP))
p.set("pixel_size_um", 0.5, by="settings")
p.set("pixel_size_um", 0.6, by="settings")
p.set("groups.by_animal", {"LS1": "TREATMENT X"}, by="settings")
p.unset("section_thickness_um", by="settings")
p.set("acquisition.channels", [{"name": "c%d" % i, "pad": "x" * 30} for i in range(40)], by="channel_editor")
p.note_detected("/d/a.czi", [{"name": "DAPI", "band": "blue"}, {"name": "AF568", "band": "red"}, {"name": "Ch3"}])
p.save()
p.record_run("05c", True, "2026-01-02T03:00:00+00:00", "2026-01-02T03:05:00+00:00")
p.record_run("06a", False, "2026-01-02T04:00:00+00:00", "2026-01-02T04:00:09+00:00")
block = p.block

print("--- rows ---")
d = PH.decision_rows(block)
chk("decisions are newest first", [r[1] for r in d][:2], ["acquisition.channels", "section_thickness_um"])
chk("a removal reads as removed", d[1][3], "(removed)")
chk("a change shows was and now", [(r[2], r[3]) for r in d if r[1] == "pixel_size_um"], [("0.5", "0.6"), ("0.65", "0.5")])
chk("a first-time setting has an empty 'was'", [r for r in PH.decision_rows({"decisions": [{"key": "k", "to": 1, "by": "x"}]})][0][2], "")
tg = [r for r in d if r[1] == "groups.by_animal"][0]
chk("the treatment key shows that it changed, not what to", (tg[2], tg[3], "TREATMENT X" in json.dumps(d)), ("(not recorded)", "(not recorded)", False))
big = d[0]
chk("a large value is abbreviated, not dumped", len(big[3]) <= PH.SHORT and big[3].startswith("{"), True)
r = PH.run_rows(block)
chk("runs are newest first with their result", [(x[0], x[3]) for x in r], [("06a", "FAILED"), ("05c", "ok")])
chk("times read without the T", r[1][1], "2026-01-02 03:00:00")
chk("each run carries its config hash", len(r[0][4]), 12)
dt = PH.detected_rows(block)
chk("a CZI reading lists its channels with their bands", dt[0][2], "DAPI (blue); AF568 (red); Ch3")
chk("empty history gives no rows", (PH.decision_rows({}), PH.run_rows(None), PH.detected_rows({})), ([], [], []))

print("--- the window ---")
s = PS.ProjectSession(p)
dlg = PH.ProjectHistory(s)
chk("tabs count their rows", [dlg.tabs.tabText(i) for i in range(4)],
    ["Decisions (%d)" % len(d), "Runs (2)", "CZI files read (1)", "Notes"])
chk("the decisions table shows them", dlg.decisions.rowCount(), len(d))
chk("a cell holds the value", dlg.runs.item(0, 0).text(), "06a")
chk("the table cannot be edited", dlg.runs.editTriggers() == QTableWidget.NoEditTriggers, True)
chk("notes start empty and the project is clean", (dlg.notes.toPlainText(), s.dirty), ("", False))
dlg.notes.setPlainText("calibrated on 2026-10-13")
dlg.accept()
chk("closing keeps edited notes in the project", p.block["notes"], "calibrated on 2026-10-13")
chk("...which are then unsaved", s.dirty, True)
s.save()
chk("...and saved with the project", json.load(open(p.path))["project"]["notes"], "calibrated on 2026-10-13")
dlg2 = PH.ProjectHistory(s)
dlg2.reject()
chk("closing with notes unchanged changes nothing", s.dirty, False)
ro = LP.Project.open(p.path)
ro.read_only = True
dlg3 = PH.ProjectHistory(PS.ProjectSession(ro))
chk("a read-only project's notes cannot be edited", dlg3.notes.isReadOnly(), True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
