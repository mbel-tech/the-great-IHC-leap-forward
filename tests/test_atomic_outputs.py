"""The early stages write their CSVs atomically, and say so when they did nothing.

The app's done check trusts an output file's presence. Written in place with
`open(path, "w")`, a crash part-way through left a truncated CSV that looked
finished, so the stage was never re-run and the next one read half a table.
Each of the files below now goes to `<path>.tmp` through `ls_io` and is
swapped in by `os.replace` (REVIEW.md rule 3).

The source scan is an AST walk, not a substring search: every `open(...)` with
a text write mode in these scripts is a failure unless it is on ALLOWED with
its reason, so the next in-place write added to one of them fails here rather
than in front of an operator. Binary writes (`"wb"`) are not CSVs and are not
judged, and neither is `open(tmp, "w")` inside `with IO.atomic_save(path) as
tmp:` - that is the atomic route. An allow-list entry that no longer matches
anything also fails, so the list cannot rot into permission for a write that
has since moved.

Two stages also returned success having done nothing (rule 2): 01e printed
"nothing measured" and returned None, and 01k did the same when no section was
measured at all - a missing drive looked like a finished stage. Both are driven
here with nothing to read and must return non-zero. 01e is also driven to a
real write, with `analyse` stubbed so no CZI is needed, to show the success path
returns 0 and a crash at the swap leaves the previous CSV whole.

Run:  python tests/test_atomic_outputs.py
"""

import ast
import contextlib
import csv
import io
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage, SCRIPTS  # noqa: E402

STUDY = use_temp_study()

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


SCRIPTS_CHECKED = [
    "01e_tile_geometry.py",
    "01g_saturation_map.py",
    "01k_saturation_raw.py",
    "01b_pick_sections.py",
    "01i_multisection_review.py",
    "04a_reformat.py",
    "04a3b_enforce_sections.py",
    "04a_atlas_extract.py",
    "04b_atlas_match.py",
    "04e_register_elastix.py",
    "05b_tune_detection.py",
]

# (script, enclosing function, first argument as written) -> why it may stay.
ALLOWED = {
    ("04b_atlas_match.py", "build_curator", "out"):
        "atlas_curator.html - an HTML page, out of scope for this pass",
    ("04e_register_elastix.py", "write_point_file", "path"):
        "elastix point-set input in a per-section temp directory, read once by "
        "elastix in the same run; no done check reads it",
}


def _atomic_save_names(with_node):
    """Names bound by `with ...atomic_save(path) as tmp:` - the temp file the
    helper swaps in, which is the one place an `open(tmp, "w")` is right."""
    names = set()
    for item in with_node.items:
        call, var = item.context_expr, item.optional_vars
        if isinstance(call, ast.Call) and isinstance(var, ast.Name):
            f = call.func
            if (isinstance(f, ast.Attribute) and f.attr == "atomic_save") or (
                    isinstance(f, ast.Name) and f.id == "atomic_save"):
                names.add(var.id)
    return names


def text_writes(src):
    """(enclosing function, first-arg source, mode) for every text-mode write
    open, except a write to an `atomic_save` temp file inside its own block."""
    tree = ast.parse(src)
    found = []

    def visit(node, func, tmps=frozenset()):
        if isinstance(node, ast.With):
            tmps = tmps | _atomic_save_names(node)
        for child in ast.iter_child_nodes(node):
            here = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Call):
                f = child.func
                is_open = (isinstance(f, ast.Name) and f.id == "open") or (
                    isinstance(f, ast.Attribute) and f.attr == "open"
                    and isinstance(f.value, ast.Name) and f.value.id in ("io", "codecs"))
                if is_open and child.args:
                    mode = child.args[1] if len(child.args) > 1 else None
                    for kw in child.keywords:
                        if kw.arg == "mode":
                            mode = kw.value
                    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                        m = mode.value
                        target = child.args[0]
                        to_tmp = isinstance(target, ast.Name) and target.id in tmps
                        if "w" in m and "b" not in m and not to_tmp:
                            found.append((func, ast.unparse(target), m))
            visit(child, here, tmps)

    visit(tree, "<module>")
    return found


print("--- no in-place text write in these scripts ---")
seen_allowed = set()
for name in SCRIPTS_CHECKED:
    with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
        src = fh.read()
    bad = []
    for func, arg, mode in text_writes(src):
        key = (name, func, arg)
        if key in ALLOWED:
            seen_allowed.add(key)
        else:
            bad.append(f"{func}: open({arg}, {mode!r})")
    chk(f"{name}: no in-place write", bad, [])
    chk(f"{name}: writes through ls_io",
        "IO.atomic_write_csv(" in src or "IO.atomic_save(" in src, True)
for key, why in ALLOWED.items():
    chk(f"allow-list entry still matches: {key[0]} {key[1]}", key in seen_allowed, True)


print()
print("--- 01e: nothing measured is a failure ---")
E = load_stage("01e_tile_geometry.py")
os.makedirs(E.SOURCE_DIR, exist_ok=True)
out_csv = os.path.join(E.QC_DIR, "tile_geometry.csv")
argv = sys.argv
try:
    sys.argv = ["01e"]
    with contextlib.redirect_stdout(io.StringIO()) as said:
        rc = E.main()
finally:
    sys.argv = argv
chk("01e with no CZI returns 1", rc, 1)
chk("...and says why", "nothing measured" in said.getvalue(), True)
chk("...and writes no tile_geometry.csv", os.path.exists(out_csv), False)

print()
print("--- 01e: a measured run returns 0 and writes atomically ---")
FNAME = "LS01_s01a.czi"
open(os.path.join(E.SOURCE_DIR, FNAME), "wb").close()
ROW = {"file": FNAME, "scene": 0, "n_tiles": 4, "tile_w": 100, "tile_h": 80,
       "n_cols": 2, "n_rows": 2, "pitch_x_px": 90.0, "pitch_y_px": 72.0,
       "pitch_x_um": 58.5, "pitch_y_um": 46.8, "overlap_x_pct": 10.0,
       "overlap_y_pct": 10.0, "origin_x": 0, "origin_y": 0}
SUMMARY = {"file": FNAME, "n_scenes": 1, "n_tiles_total": 4,
           "pitch_x_px": 90.0, "pitch_y_px": 72.0}


def run_01e(row=ROW):
    saved = sys.argv
    try:
        sys.argv = ["01e", "--all"]
        with mock.patch.object(E, "analyse", lambda path: (SUMMARY, [dict(row)])), \
                contextlib.redirect_stdout(io.StringIO()):
            return E.main()
    finally:
        sys.argv = saved


chk("01e with a measured scene returns 0", run_01e(), 0)
with open(out_csv, newline="", encoding="utf-8") as fh:
    got = list(csv.reader(fh))
chk("...header in the old column order", got[0], list(ROW))
chk("...one row", len(got), 2)
chk("...no temp file left", os.path.exists(out_csv + ".tmp"), False)

with open(out_csv, encoding="utf-8", newline="") as fh:
    before = fh.read()
raised = False
with mock.patch("os.replace", side_effect=RuntimeError("crash at swap")):
    try:
        run_01e(dict(ROW, n_tiles=9))          # different rows, so a rewrite shows
    except RuntimeError:
        raised = True
chk("01e crash at the swap propagates", raised, True)
with open(out_csv, encoding="utf-8", newline="") as fh:
    chk("...previous tile_geometry.csv intact", fh.read(), before)


print()
print("--- 01k: a run that measured nothing is a failure ---")


class _PyCzi:
    @staticmethod
    def open_czi(path):                       # never reached: the CZI is absent
        raise AssertionError("01k opened a CZI that is not there")


sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
K = load_stage("01k_saturation_raw.py")
marker = list(K.MASK_MARKERS)[0]
scenes = [{"scene_uid": "LS01_s02a_sc00", "file": "LS01_s02a.czi", "animal": "LS01",
           "slide": "2", "variant": "a", "marker_channel": marker,
           "scene_index": "0", "section_order": "0"}]
os.makedirs(os.path.join(STUDY.out_root, "manifest"), exist_ok=True)
with open(os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv"), "w",
          newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(scenes[0]))
    w.writeheader()
    w.writerows(scenes)
try:
    sys.argv = ["01k", "--native-sample", "0", "--no-merge"]
    with contextlib.redirect_stdout(io.StringIO()) as said:
        rc = K.main()
finally:
    sys.argv = argv
chk("01k with its only CZI missing returns 1", rc, 1)
chk("...and says nothing was measured", "nothing measured" in said.getvalue(), True)


def _last_line(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


chk("...and the reason is the last line, not a row of =",
    "measured nothing" in _last_line(said.getvalue()), True)

print()
print("--- 01k: a run where only SOME files fail is a failure ---")
# Fresh: no earlier saturation_raw.csv, so no row is "kept stale" - the case
# that used to exit 0 with the missing file's sections simply absent.
GOOD_K, GONE_K = "LS01_s03a.czi", "LS01_s04a.czi"
open(os.path.join(K.SOURCE_DIR, GOOD_K), "wb").close()
scenes_k = [dict(scenes[0], scene_uid="LS01_s03a_sc00", file=GOOD_K, slide="3"),
            dict(scenes[0], scene_uid="LS01_s04a_sc00", file=GONE_K, slide="4")]
with open(os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv"), "w",
          newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(scenes_k[0]))
    w.writeheader()
    w.writerows(scenes_k)
if os.path.exists(K.OUT_CSV):
    os.remove(K.OUT_CSV)


class _Doc:
    scenes_bounding_rectangle = {0: object()}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _PyCziOne:
    @staticmethod
    def open_czi(path):
        return _Doc()


sys.modules["pylibCZIrw"].czi = _PyCziOne


def _record(r, fname, rect, s, zoom, dapi_raw, mark_raw, corrected, ceiling):
    return {"scene_uid": r["scene_uid"], "file": fname, "marker_channel": r["marker_channel"],
            "saturated_fraction_raw": 0.1, "saturated_fraction_dapi_raw": 0.0,
            "raw_over_corrected": "", "ceiling": ceiling}


try:
    sys.argv = ["01k", "--native-sample", "0", "--no-merge"]
    with mock.patch.object(K, "measure", lambda *a, **k: (None, None, None)), \
            mock.patch.object(K.OV, "raw_clip_record", _record), \
            contextlib.redirect_stdout(io.StringIO()) as said:
        rc = K.main()
finally:
    sys.argv = argv
    sys.modules["pylibCZIrw"].czi = _PyCzi
chk("01k with one of two files missing returns 1", rc, 1)
chk("...naming the section in the last line",
    "LS01_s04a_sc00" in _last_line(said.getvalue()), True)
with open(K.OUT_CSV, newline="", encoding="utf-8") as fh:
    chk("...and still keeps the section it did measure",
        [r["scene_uid"] for r in csv.DictReader(fh)], ["LS01_s03a_sc00"])

print()
print("--- 01g: nothing measured is a failure and writes no census ---")
G = load_stage("01g_saturation_map.py")
mk = G.MARKERS[0]
os.makedirs(os.path.dirname(G.QC_CSV), exist_ok=True)
QC_FIELDS = ["scene_uid", "animal", "slide", "marker_channel", "section_order", "um_px",
             "saturated_fraction", "saturated_fraction_raw"]
with open(G.QC_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=QC_FIELDS)
    w.writeheader()
    w.writerow({"scene_uid": "LS01_s05a_sc00", "animal": "LS01", "slide": "5",
                "marker_channel": mk, "section_order": "1", "um_px": "5.2",
                "saturated_fraction": "0", "saturated_fraction_raw": "0"})
census = os.path.join(G.REPORT_DIR, f"saturation_{mk}.csv")
try:
    sys.argv = ["01g"]
    with contextlib.redirect_stdout(io.StringIO()) as said:
        rc = G.main()
finally:
    sys.argv = argv
chk("01g with no overview for its only section returns 1", rc, 1)
chk("...and writes no saturation csv for the app to read as done",
    os.path.exists(census), False)
chk("...saying why, last", "no _MARK/_DAPI overview" in _last_line(said.getvalue()), True)

print()
print("--- 01g: some sections without an overview: census written, run fails ---")
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402
ov = os.path.join(G.OVERVIEW_DIR, "LS01", mk)
os.makedirs(ov, exist_ok=True)
yy, xx = np.mgrid[0:120, 0:120]
disk = ((yy - 60) ** 2 + (xx - 60) ** 2) < 45 ** 2
Image.fromarray(np.where(disk, 180, 5).astype(np.uint8)).save(
    os.path.join(ov, "LS01_s05a_sc00_DAPI.png"))
Image.fromarray(np.where(disk, 120, 10).astype(np.uint8)).save(
    os.path.join(ov, "LS01_s05a_sc00_MARK.png"))
with open(G.QC_CSV, "a", newline="", encoding="utf-8") as fh:
    csv.DictWriter(fh, fieldnames=QC_FIELDS).writerow(
        {"scene_uid": "LS01_s06a_sc00", "animal": "LS01", "slide": "6",
         "marker_channel": mk, "section_order": "2", "um_px": "5.2",
         "saturated_fraction": "0", "saturated_fraction_raw": "0"})
try:
    sys.argv = ["01g", "--figures", "0"]
    with contextlib.redirect_stdout(io.StringIO()) as said:
        rc = G.main()
finally:
    sys.argv = argv
chk("01g with one of two overviews missing returns 1", rc, 1)
chk("...counting the missing one last",
    "1 of 2" in _last_line(said.getvalue())
    and "LS01_s06a_sc00" in _last_line(said.getvalue()), True)
with open(census, newline="", encoding="utf-8") as fh:
    chk("...and still writes the census of the one it measured",
        [r["scene_uid"] for r in csv.DictReader(fh)], ["LS01_s05a_sc00"])

print()
print("--- 04a atlas extract: an empty gaps table replaces last run's ---")
AX = load_stage("04a_atlas_extract.py")
gaps = os.path.join(STUDY.out_root, "gaps_probe.csv")
with open(gaps, "w", encoding="utf-8") as fh:
    fh.write("plate_id,page,image_file,reason\nP1,1,p1.png,no region seeds on this plate\n")
with contextlib.redirect_stdout(io.StringIO()):
    AX._write_csv(gaps, [], ["plate_id", "page", "image_file", "reason"])
with open(gaps, newline="", encoding="utf-8") as fh:
    chk("04a gaps.csv with no gaps is header-only", list(csv.reader(fh)),
        [["plate_id", "page", "image_file", "reason"]])
with open(os.path.join(SCRIPTS, "04a_atlas_extract.py"), encoding="utf-8") as fh:
    src = fh.read()
import re  # noqa: E402
# Whitespace-blind: the call may be reflowed, but it must pass the columns.
chk("04a passes gaps.csv its columns",
    re.search(r'"gaps\.csv"\)\s*,\s*gap_rows\s*,\s*\[\s*"plate_id"\s*,\s*"page"\s*,'
              r'\s*"image_file"\s*,\s*"reason"\s*\]', src) is not None, True)

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
