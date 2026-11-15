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

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
