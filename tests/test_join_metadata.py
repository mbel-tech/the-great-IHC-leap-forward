"""Workbook columns by name, groups checked against the unblinding, 12 shapes.

Run:  python tests/test_join_metadata.py
"""

import sys
import csv
import importlib.util
import os
import re
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")

# This suite imports stage modules, which read config at import. Without a
# config of its own it would fall through to the operator's live study and
# then pass or fail on their data. See tests/_fixture.py.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study  # noqa: E402

STUDY = use_temp_study()


def load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(54) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


B = load("b", "06b_join_sampling.py")
# The live header, typos and double space included.
LIVE = {0: "Fish ID", 1: "timepoint", 2: "enviroment", 3: "brackish Tank", 4: "sea water  tank",
        5: "treatment", 6: "freshwater to brackish trasfer date", 7: "brackish to sea trasfer date",
        8: "Heart ID", 9: "Body weight(g)", 10: "Fork Length (cm)", 11: "Sex", 12: "Hearts in PFA",
        13: "Brain for", 14: "Stress test", 15: "notes", 16: "date", 17: "slicing IHC July 2025",
        18: "slicing MD MARCH 2026"}
col = B.resolve_columns(LIVE)
chk("live header resolves to the old indices",
    col, {"fish": 0, "timepoint": 1, "environment": 2, "brackish_tank": 3, "sea_tank": 4,
          "treatment": 5, "body_weight_g": 9, "fork_length_cm": 10, "sex": 11, "brain_for": 13,
          "sliced_july_2025": 17})
# Excel writes a formatted blank cell, and an empty row, as self-closing tags.
# Read as an open tag, the blank cell swallowed its neighbour's value (a blank
# treatment took the tank number) and the empty row swallowed the next row.
import zipfile  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    book = os.path.join(tmp, "s.xlsx")
    sheet_xml = (
        '<worksheet><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="s"><v>2</v></c></row>'
        '<row r="2"><c r="A2"><v>7</v></c><c r="B2" s="3"/><c r="C2"><v>303</v></c></row>'
        '<row r="3" spans="1:3"/>'
        '<row r="4"><c r="A4"><v>8</v></c><c r="B4" t="inlineStr"><is><t>EX</t></is></c>'
        '<c r="C4"><v>404</v></c></row>'
        '</sheetData></worksheet>')
    with zipfile.ZipFile(book, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   "<sst><si><t>Fish ID</t></si><si><t>treatment</t></si>"
                   "<si><t>brackish Tank</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    rows = B.read_sheet(book)
    chk("a styled blank cell stays blank", rows[1].get(1, ""), "")
    chk("...and its neighbour keeps its value", rows[1].get(2), "303")
    chk("an empty row is its own (empty) row", rows[2], {})
    chk("...and the row after it is read whole", rows[3], {0: "8", 1: "EX", 2: "404"})

shifted = {0: "Fish ID", 1: "new column", **{k + 1: v for k, v in LIVE.items() if k > 0}}
chk("an inserted column moves every index", B.resolve_columns(shifted)["treatment"], 6)
msg = ""
try:
    B.resolve_columns({k: v for k, v in LIVE.items() if k != 5})
except SystemExit as exc:
    msg = str(exc)
chk("a missing column stops and is named", "treatment" in msg, True)

C = load("c", "06c_excel_dataset.py")
tmp = tempfile.mkdtemp(prefix="meta_")
try:
    meta = os.path.join(tmp, "animal_metadata.csv")
    chk("no metadata yet: config stands", C.resolve_groups({"LS1": "control"}, meta), {"LS1": "control"})
    with open(meta, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["animal", "treatment"])
        w.writeheader()
        w.writerows([{"animal": "LS1", "treatment": "control"}, {"animal": "LS2", "treatment": "exercise"}])
    chk("agreement: metadata fills what config lacks",
        C.resolve_groups({"LS1": "control"}, meta), {"LS1": "control", "LS2": "exercise"})
    msg = ""
    try:
        C.resolve_groups({"LS1": "exercise"}, meta)
    except SystemExit as exc:
        msg = str(exc)
    chk("disagreement fails and names the animal", "LS1" in msg and "06b" in msg, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

with open(os.path.join(REPO, "analysis", "plot_roi_figures.R"), encoding="utf-8") as fh:
    r_src = fh.read()
# SHAPE IS NO LONGER PER ANIMAL, and these three checks had to move with it.
#
# They used to read a 12-entry `SHAPES` palette and assert that `animal_shapes`
# STOPS rather than recycling, because recycling was silent: a thirteenth
# animal reused the first animal's shape and two animals on one panel became
# indistinguishable with nothing said. The operator replaced the whole palette
# with one shape for every point on 2026-09-07 (plot_roi_figures.R:130-141) and
# separates the arms by colour instead, so `SHAPES` and `animal_shapes` are
# gone - and this file kept searching for them, which is why it failed with
# `'NoneType' object has no attribute 'group'` rather than a named assertion.
#
# One shape cannot recycle, so what is worth guarding now is that a per-animal
# shape scale has not come BACK without its overflow check coming back too.
chk("one shape for every point",
    bool(re.search(r"^PT_SHAPE <- \d+$", r_src, re.M)), True)
chk("no per-animal shape palette is left behind", "animal_shapes" in r_src, False)
chk("...and nothing recycles a scale to fit", "rep_len" in r_src, False)

# ---- Check workbook: 06b's matching, run early and writing nothing ---------
# Set up's "Check workbook" is the same join 06b makes at Unblind, so it must
# read with 06b's own functions - two readers would let the check pass a
# workbook the join then refuses.
S = load("ls_sampling", "ls_sampling.py")


def _where(fn):
    """The file a function is defined in: one reader, wherever it is imported from."""
    return os.path.basename(fn.__code__.co_filename)


chk("06b reads the sheet with the shared reader",
    _where(B.read_sheet), _where(S.read_sheet))
chk("...resolves its header with it",
    _where(B.resolve_columns), _where(S.resolve_columns))
chk("...and 06c's reader is the same one",
    _where(C.G6B.read_table), _where(S.read_table))

import openpyxl  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    book = os.path.join(tmp, "sampling.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([LIVE[i] for i in range(len(LIVE))])

    def fish_row(fid, treatment, tank, sliced="x"):
        row = [None] * len(LIVE)
        row[0], row[1], row[3], row[5], row[17] = fid, 1, tank, treatment, sliced
        return row
    ws.append(fish_row(1, "control", "305"))
    ws.append(fish_row(2, "exercise", "303"))
    ws.append(fish_row(3, None, "307"))          # blank, the tank recovers it
    ws.append(fish_row(4, None, "999"))          # blank, and no tank says
    wb.save(book)
    before = sorted(os.listdir(tmp))
    rep = S.check(book, {"LS1": 2, "LS2": 2, "LS3": 1, "LS4": 1, "LS9": 3})
    chk("check: no error on a readable workbook", rep["error"], None)
    chk("check: fish matched to a group", sorted(rep["matched"]), ["LS1", "LS2", "LS3"])
    chk("check: ...and the slides they cover", rep["slides_matched"], 5)
    chk("check: a fish with no row is named", rep["missing"], ["LS9"])
    chk("check: a blank group no tank recovers is named", rep["blank"], [("LS4", "999")])
    chk("check: a group read from the tank is said so", rep["from_tank"], ["LS3"])
    chk("check: groups counted as 06b would write them", rep["groups"],
        {"control": 1, "exercise": 2})
    chk("check: nothing written beside the workbook", sorted(os.listdir(tmp)), before)
    chk("group_key: the Shotgun key holds only fish with a group",
        S.group_key(rep), {"order": ["control", "exercise"],
                           "by_animal": {"LS1": "control", "LS2": "exercise",
                                         "LS3": "exercise"}})
    chk("group_key: a failed check gives no key",
        S.group_key({"error": "x", "by_animal": {"LS1": "control"}}),
        {"order": [], "by_animal": {}})
    lines = S.words(rep)
    chk("check: the words name the missing fish", any("LS9" in ln for ln in lines), True)
    # The same rows through 06b's own join give the same groups.
    col, sheet = S.read_table(book)
    j = S.join(["LS1", "LS2", "LS3", "LS4"], col, sheet)
    chk("06b's join and the check agree on every group",
        {a: m["treatment"] for a, m in j["meta"].items()},
        {"LS1": "control", "LS2": "exercise", "LS3": "exercise", "LS4": ""})

    junk = os.path.join(tmp, "junk.xlsx")
    open(junk, "wb").write(b"not a workbook")
    rep = S.check(junk, {"LS1": 1})
    chk("check: an unreadable workbook is an error, not a crash",
        bool(rep["error"]) and rep["matched"] == [], True)
    rep = S.check(os.path.join(tmp, "nope.xlsx"), {"LS1": 1})
    chk("check: a missing workbook says so", "no workbook" in (rep["error"] or ""), True)
    wb = openpyxl.Workbook()
    wb.active.append(["Fish ID", "something else"])
    odd = os.path.join(tmp, "odd.xlsx")
    wb.save(odd)
    rep = S.check(odd, {"LS1": 1})
    chk("check: a header 06b cannot read names the column",
        "treatment" in (rep["error"] or ""), True)

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
