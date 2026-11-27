"""The reagent k check, shown and applied.

06a writes results/reagent_cut_check.csv (Task 2). Here: 06c's "Reagent
controls" sheet carries it as a "Cut check" block, labelled provisional; the
Results page shows each marker's suggested k against the k in force, with a
"Use the suggested multiplier…" button; and cut_apply.apply_suggested_k writes
positivity.cut_k[marker] with its provenance, refusing after 06b (the
unblinding) unless the operator types the confirmation.

    python tests/test_cut_apply.py
"""

import contextlib
import csv
import faulthandler
import gc
import hashlib
import io
import os
import sys
import tempfile

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


import ls_reagent_report as RR                                  # noqa: E402
import ls_project as LP                                         # noqa: E402
import ls_config as LC                                          # noqa: E402


def check_rows(suggest=True):
    """A pERK cut check: k 2.0 and 2.5 miss the target, 3.5 is the first that meets it."""
    rows = []
    for k, fp in zip(RR.K_GRID, (0.08, 0.04, 0.012, 0.006, 0.003, 0.001, 0.0)):
        r = dict.fromkeys(RR.CUT_CHECK_COLUMNS, "")
        r.update(marker="pERK", k=k, n_control_sections=2, n_control_nuclei=400,
                 control_fp=fp, control_fp_lo=max(0.0, fp - 0.005), control_fp_hi=fp + 0.01,
                 meets_target=int(fp <= 0.01), suggested=0)
        rows.append(r)
    if suggest:
        rows[3]["suggested"] = 1
    else:
        for r in rows:
            r.update(control_fp="", control_fp_lo="", control_fp_hi="", meets_target="",
                     suggested="", note="12 reagent-control nuclei, fewer than the minimum 50: "
                                         "no rate and no suggested k")
    return rows


def write_csv(path, rows, cols):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def out_root(name, suggest=True):
    root = os.path.join(STUDY.root, name)
    write_csv(os.path.join(root, "results", RR.CUT_CHECK_CSV), check_rows(suggest),
              RR.CUT_CHECK_COLUMNS)
    return root


import cut_apply as CA                                          # noqa: E402


def seen(root, marker="pERK"):
    """(k, sha) of the check as a page would have shown it: the marker's
    suggested k and the checksum of the bytes it was read from."""
    rows, sha = CA.read_check(root)
    return (CA.suggestion(rows, marker)[0] if rows else None), sha


def apply(project, root, marker, confirm=None, shown=None):
    k, sha = shown if shown is not None else seen(root, marker)
    return CA.apply_suggested_k(project, root, marker, confirm, shown_k=k, shown_sha=sha)


# ============================================================ apply_suggested_k
print("--- apply_suggested_k ---")
ROOT = out_root("o_apply")
csv_path = os.path.join(ROOT, "results", RR.CUT_CHECK_CSV)
project = LP.Project.open(STUDY.path)
ok, msg = apply(project, ROOT, "pERK")
chk("apply_writes_k_and_source: applied", ok, True)
chk("...cut_k['pERK'] is the suggested k", (project.get("positivity.cut_k") or {}).get("pERK"),
    3.5)
chk("...the '*' entry is kept", (project.get("positivity.cut_k") or {}).get("*"), 3.0)
src = project.get("positivity.cut_k_source") or {}
chk("...source['pERK']['by'] == 'controls'", (src.get("pERK") or {}).get("by"), "controls")
with open(csv_path, "rb") as fh:
    sha = hashlib.sha256(fh.read()).hexdigest()
chk("...the check sha is the file's", (src.get("pERK") or {}).get("check"), sha)
chk("...and when", bool((src.get("pERK") or {}).get("at")), True)
chk("...no flat 'by' beside the marker entries", "by" in src, False)
chk("...before unblinding: not marked as after it", "after_unblinding" in src["pERK"], False)
chk("...the decision is recorded by the cut check",
    [d["by"] for d in project.block.get("decisions", []) if d["key"] == "positivity.cut_k"],
    ["cut check"])
chk("...the config still validates", project.validate()[0], [])
chk("...the message names the marker and k", "pERK" in msg and "3.5" in msg, True)
ok2, msg2 = apply(project, ROOT, "PCNA")
chk("a marker with no suggestion: refused", ok2, False)

print("--- apply_after_unblinding_requires_confirmation ---")
ROOT_B = out_root("o_blind")
write_csv(os.path.join(ROOT_B, "results", "animal_metadata.csv"),
          [{"animal": "LS1", "treatment": "control"}], ["animal", "treatment"])
chk("unblinded() sees 06b's output", CA.unblinded(ROOT_B), True)
chk("...and not on a study 06b has not run on", CA.unblinded(ROOT), False)
project = LP.Project.open(STUDY.path)
before = (project.get("positivity.cut_k"), project.get("positivity.cut_k_source"),
          len(project.block.get("decisions", [])))
ok, msg = apply(project, ROOT_B, "pERK")
chk("apply_after_unblinding_requires_confirmation: refused without it", ok, False)
chk("...says the study is unblinded and what to type",
    "unblind" in msg and CA.CONFIRM_TEXT in msg, True)
chk("...config unchanged", (project.get("positivity.cut_k"),
                            project.get("positivity.cut_k_source"),
                            len(project.block.get("decisions", []))), before)
ok, _ = apply(project, ROOT_B, "pERK", "apply after unblindng")
chk("...a mistyped confirmation is refused", ok, False)
ok, _ = apply(project, ROOT_B, "pERK", "apply after unblinding")
chk("...'apply after unblinding' succeeds", ok, True)
chk("...and writes the k", (project.get("positivity.cut_k") or {}).get("pERK"), 3.5)
chk("apply_after_unblinding_is_recorded: cut_k_source says so",
    ((project.get("positivity.cut_k_source") or {}).get("pERK") or {}).get("after_unblinding"),
    True)
chk("...and the config still validates", project.validate()[0], [])
ok, msg = apply(project, os.path.join(STUDY.root, "nowhere"), "pERK")
chk("no check file: refused", ok, False)

print("--- apply_refuses_a_check_changed_since_shown ---")
ROOT_C = out_root("o_changed")
shown = seen(ROOT_C)
chk("(the page was shown k 3.5)", shown[0], 3.5)
# The hourly 06e loop rewrites the check between the page and the click: the
# suggestion is now 4.0, a k the operator never saw.
moved = check_rows()
moved[3]["suggested"], moved[4]["suggested"] = 0, 1
write_csv(os.path.join(ROOT_C, "results", RR.CUT_CHECK_CSV), moved, RR.CUT_CHECK_COLUMNS)
project = LP.Project.open(STUDY.path)
before = (project.get("positivity.cut_k"), project.get("positivity.cut_k_source"),
          len(project.block.get("decisions", [])))
ok, msg = apply(project, ROOT_C, "pERK", shown=shown)
chk("a new suggestion since shown: refused", ok, False)
chk("...saying the check changed and to reopen the page",
    "changed since it was shown" in msg and "reopen the page" in msg, True)
chk("...config unchanged", (project.get("positivity.cut_k"),
                            project.get("positivity.cut_k_source"),
                            len(project.block.get("decisions", []))), before)
# The same suggested k, but the table under it is not the one shown.
write_csv(os.path.join(ROOT_C, "results", RR.CUT_CHECK_CSV), check_rows(),
          RR.CUT_CHECK_COLUMNS + ["extra"])
ok, msg = apply(project, ROOT_C, "pERK", shown=(3.5, shown[1]))
chk("the same k from a different table: refused too",
    (ok, "changed since it was shown" in msg), (False, True))
chk("...config unchanged", (project.get("positivity.cut_k"),
                            project.get("positivity.cut_k_source"),
                            len(project.block.get("decisions", []))), before)
try:
    CA.apply_suggested_k(project, ROOT_C, "pERK", None)
    chk("apply without the k and checksum shown is not possible", False)
except TypeError:
    chk("apply without the k and checksum shown is not possible", True)

print("--- the config warns on a cut_k key that is not a marker ---")
cfg = {"acquisition": {"layout": "paired", "markers": ["pERK", "PCNA"]},
       "positivity": {"cut_k": {"*": 3.0, "pERK": 3.5, "pERKK": 4.0}}}
errs, warns = LC.validate(cfg)
chk("an unknown marker key warns", any("pERKK" in w and "positivity.cut_k" in w for w in warns),
    True)
chk("...and is not an error", any("pERKK" in e for e in errs), False)
chk("...a study marker does not warn", any("`pERK`" in w or "'pERK'" in w for w in warns), False)
cfg["acquisition"] = {"layout": "paired", "markers": []}
chk("...no markers declared: no warning",
    any("pERKK" in w for w in LC.validate(cfg)[1]), False)

# ============================================================== 06c sheet
print("--- sheet_has_cut_check ---")
G6C = load_stage("06c_excel_dataset.py", name="lsstage_06c_ca")
tmp = os.path.join(STUDY.root, "o_sheet", "results")
write_csv(os.path.join(tmp, RR.CUT_CHECK_CSV), check_rows(), RR.CUT_CHECK_COLUMNS)
rc = dict.fromkeys(RR.COLUMNS, "")
rc.update(scene_uid="RC1_s01_sc01", kind="no_primary", marker="pERK", region="Dm",
          pct_positive="0.6")
write_csv(os.path.join(tmp, RR.REPORT_CSV), [rc], RR.COLUMNS)
npath = os.path.join(tmp, "roi_nuclei.csv")
write_csv(npath, [], ["scene_uid", "marker"])
saved = (G6C.NUCLEI_CSV, G6C.MEAS_CSV)
G6C.NUCLEI_CSV, G6C.MEAS_CSV = npath, os.path.join(tmp, "roi_measurements.csv")
xlsx = os.path.join(tmp, "roi_dataset.xlsx")
pos = G6C.CONFIG.setdefault("positivity", {})
saved_src = pos.get("cut_k_source")
# pERK's k applied through the typed confirmation, PCNA's before unblinding.
pos["cut_k_source"] = {
    "pERK": {"by": "controls", "check": "ab", "at": "t", "after_unblinding": True},
    "PCNA": {"by": "controls", "check": "cd", "at": "t"}}
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code = G6C.main(["--out", xlsx])
finally:
    G6C.NUCLEI_CSV, G6C.MEAS_CSV = saved
    if saved_src is None:
        pos.pop("cut_k_source", None)
    else:
        pos["cut_k_source"] = saved_src
chk("06c runs", code, 0)
import openpyxl                                                 # noqa: E402
ws = openpyxl.load_workbook(xlsx)["Reagent controls"]
cells = [list(r) for r in ws.iter_rows(values_only=True)]
text = " ".join(str(c) for r in cells for c in r if c is not None)
chk("sheet_has_cut_check: the sheet says 'Cut check'", "Cut check" in text, True)
chk("...and 'provisional'", "provisional" in text, True)
hdr = [i for i, r in enumerate(cells) if r and r[0] == "marker" and "k" in r]
chk("...a table headed by CUT_CHECK_COLUMNS",
    list(cells[hdr[0]][:len(RR.CUT_CHECK_COLUMNS)]) if hdr else None, RR.CUT_CHECK_COLUMNS)
krows = [r for r in cells[hdr[0] + 1:] if r and r[0] == "pERK"] if hdr else []
chk("...one row per k", [r[1] for r in krows], list(RR.K_GRID))
chk("...the suggested row is marked", [r[1] for r in krows if r[10] == 1], [3.5])
chk("sheet_says_k_changed_after_unblinding: one line for pERK",
    "k for pERK was changed after unblinding" in text, True)
chk("...none for PCNA, applied before", "k for PCNA was changed" in text, False)
chk("...the reagent-control table is still there",
    any(r and r[0] == "scene_uid" for r in cells), True)

# ============================================================ the Results page
print("--- the Results page ---")
from PySide6.QtCore import QCoreApplication, QEvent             # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
asked, typed = [], []
QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.Yes)[1])
QMessageBox.warning = staticmethod(lambda *a, **k: asked.append(a[2]))
QInputDialog.getText = staticmethod(lambda *a, **k: (typed.append(a[2]),
                                                     ("apply after unblinding", True))[1])

import workflow as W                                            # noqa: E402
import screens as SC                                            # noqa: E402

results = SC.StageListScreen(W.SCREENS["results"])
applied = []
results.cut_k_applied.connect(applied.append)

project = LP.Project.open(STUDY.path)
results.show_cut_check(out_root("o_none", suggest=False), project)
chk("no_suggestion_no_button: the button is disabled", results.cut_btn.isEnabled(), False)
chk("...its label", results.cut_btn.text(), "Use the suggested multiplier…")
chk("...and says why", "fewer than the minimum" in results.cut_why.text(), True)
chk("...the panel is provisional", "provisional" in results.cut_text.text(), True)

results.show_cut_check(os.path.join(STUDY.root, "o_nothing"), project)
chk("no check file: the panel is hidden", results.cut_box.isHidden(), True)

results.show_cut_check(ROOT, project)
chk("a suggestion: the panel shows", results.cut_box.isHidden(), False)
chk("...suggested k against the k in force",
    "3.5" in results.cut_text.text() and "3" in results.cut_text.text(), True)
chk("...the button is enabled", results.cut_btn.isEnabled(), True)
results.cut_btn.click()
chk("...clicking asks first", len(asked), 1)
chk("...the question names the suggested k and the k in force",
    ("suggested k 3.5" in asked[0], "k in force now is 3" in asked[0]), (True, True))
chk("...and applies", (project.get("positivity.cut_k") or {}).get("pERK"), 3.5)
chk("...signals the window", len(applied), 1)
results.show_cut_check(ROOT, project)
chk("...now in force: the button is disabled", results.cut_btn.isEnabled(), False)
chk("...and says so", "in force" in results.cut_why.text(), True)

project = LP.Project.open(STUDY.path)
results.show_cut_check(ROOT_B, project)
asked.clear()
results.cut_btn.click()
chk("after 06b: the operator types the confirmation", len(typed), 1)
chk("...the prompt says the study is unblinded", "unblind" in typed[0], True)
chk("...and names the suggested k and the k in force",
    ("suggested k 3.5" in typed[0], "k in force now is 3" in typed[0]), (True, True))
chk("...and the typed text applies it", (project.get("positivity.cut_k") or {}).get("pERK"),
    3.5)


print("--- the page refuses a check rewritten after it was shown ---")
project = LP.Project.open(STUDY.path)
ROOT_D = out_root("o_page_changed")
results.show_cut_check(ROOT_D, project)
moved = check_rows()
moved[3]["suggested"], moved[4]["suggested"] = 0, 1
write_csv(os.path.join(ROOT_D, "results", RR.CUT_CHECK_CSV), moved, RR.CUT_CHECK_COLUMNS)
before = (project.get("positivity.cut_k"), project.get("positivity.cut_k_source"))
asked.clear()
results.cut_btn.click()
chk("the click is refused with a warning to reopen the page",
    any("changed since it was shown" in a for a in asked), True)
chk("...the question named the k shown, 3.5, not the 4 now in the file",
    "suggested k 3.5" in asked[0], True)
chk("...config unchanged",
    (project.get("positivity.cut_k"), project.get("positivity.cut_k_source")), before)
chk("...and the panel now shows the check as it is: suggested k 4",
    "suggested k 4, k in force 3" in results.cut_text.text(), True)
asked.clear()
results.cut_btn.click()
chk("a second click asks about the k now shown, 4",
    bool(asked) and "suggested k 4?" in asked[0], True)
chk("...and applies it", (project.get("positivity.cut_k") or {}).get("pERK"), 4.0)


# ================================== groups.by_animal filled counts as unblinded
# (operator ruling 2026-11-22). 06c's treatment column shows the groups as soon
# as groups.by_animal is filled, whether or not 06b has run. The labels below
# are placeholders and are never printed: only bools are compared.
print("--- a filled groups.by_animal is unblinded too, without 06b ---")
ROOT_G = out_root("o_groups")
chk("(no 06b output under this root)", CA.unblinded(ROOT_G), False)
project = LP.Project.open(STUDY.path)
project.set("groups.by_animal", {"X1": "gA", "X2": "gB"}, by="test", record=False)
chk("unblinded() with the project: a filled by_animal counts",
    CA.unblinded(ROOT_G, project), True)
before = (project.get("positivity.cut_k"), project.get("positivity.cut_k_source"),
          len(project.block.get("decisions", [])))
ok, msg = apply(project, ROOT_G, "pERK")
chk("by_animal filled, no 06b: the plain apply is refused", ok, False)
chk("...says the study is unblinded and what to type",
    "unblind" in msg and CA.CONFIRM_TEXT in msg, True)
chk("...config unchanged", (project.get("positivity.cut_k"),
                            project.get("positivity.cut_k_source"),
                            len(project.block.get("decisions", []))), before)
ok, _ = apply(project, ROOT_G, "pERK", CA.CONFIRM_TEXT)
chk("...the typed text applies it", ok, True)
chk("...and the provenance says after unblinding",
    ((project.get("positivity.cut_k_source") or {}).get("pERK") or {}).get("after_unblinding"),
    True)

for label, prep in (("empty", lambda p: p.set("groups.by_animal", {}, record=False)),
                    ("absent", lambda p: p.unset("groups.by_animal", record=False)),
                    ("no groups block", lambda p: p.unset("groups", record=False))):
    project = LP.Project.open(STUDY.path)
    prep(project)
    chk(f"by_animal {label}, no 06b: not unblinded", CA.unblinded(ROOT_G, project), False)
    ok, _ = apply(project, ROOT_G, "pERK")
    chk("...the plain apply works", ok, True)
    chk("...and is not marked after unblinding",
        "after_unblinding" in ((project.get("positivity.cut_k_source") or {}).get("pERK") or {}),
        False)

print("--- the page asks for the typed text when by_animal is filled ---")
project = LP.Project.open(STUDY.path)
project.set("groups.by_animal", {"X1": "gA"}, by="test", record=False)
ROOT_GP = out_root("o_groups_page")
results.show_cut_check(ROOT_GP, project)
asked.clear()
typed.clear()
results.cut_btn.click()
chk("by_animal filled, no 06b: the operator types the confirmation", len(typed), 1)
chk("...no plain Yes/No question", len(asked), 0)
chk("...the prompt says the study is unblinded", bool(typed) and "unblind" in typed[0], True)
chk("...the typed text applies it, recorded as after unblinding",
    ((project.get("positivity.cut_k") or {}).get("pERK"),
     ((project.get("positivity.cut_k_source") or {}).get("pERK") or {}).get("after_unblinding")),
    (3.5, True))
project = LP.Project.open(STUDY.path)
project.set("groups.by_animal", {}, record=False)
results.show_cut_check(out_root("o_groups_page_empty"), project)
asked.clear()
typed.clear()
results.cut_btn.click()
chk("by_animal empty, no 06b: the plain question, nothing typed", (len(asked), len(typed)),
    (1, 0))
chk("...and applies", (project.get("positivity.cut_k") or {}).get("pERK"), 3.5)


def teardown():
    """Take every Qt object down while the application still exists."""
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    results.deleteLater()
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    gc.collect()
    app.processEvents()


teardown()
print("\n%s" % ("FAILED: %s" % failures if failures else "all passed"))
sys.stdout.flush()
sys.exit(1 if failures else 0)
