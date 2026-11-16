"""First-run and partial-run inputs that used to crash.

Run:  python tests/test_empty_guards.py
"""

import csv
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

# This suite imports stage modules, which read config at import. Without a
# config of its own it would fall through to the operator's live study and
# then pass or fail on their data. See tests/_fixture.py.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study  # noqa: E402

STUDY = use_temp_study()
PY = sys.executable


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


tmp = tempfile.mkdtemp(prefix="guards_")
try:
    # 00b: no archives -> no rows, and the writer must still produce a header.
    V = load("v", "00b_verify_extraction.py")
    chk("00b check() with an empty inventory", V.check({}, False), [])
    chk("00b names its columns", V.STATUS_KEYS[:2], ["file", "zip"])

    # 01k: summarise() on nothing measured returns instead of raw.max()
    K = load("k", "01k_saturation_raw.py")
    K.summarise([], measured=0, failed=0)
    chk("01k summarise([]) returns", True, True)

    # 04a preview with n=1: subplots(2, 1) must still index as [row, col]
    RF = load("rf", "04a_reformat.py")
    sec, pla = os.path.join(tmp, "sec"), os.path.join(tmp, "pla")
    os.makedirs(sec); os.makedirs(pla)
    Image.fromarray(np.zeros((8, 8), np.uint8)).save(os.path.join(sec, "s1.png"))
    Image.fromarray(np.zeros((8, 8), np.uint8)).save(os.path.join(pla, "p1.png"))
    RF.REFORMAT_DIR = tmp
    RF.preview([{"kind": "plate", "id": "p1", "angle": 0.0},
                {"kind": "section", "id": "s1", "angle": 0.0}], sec, pla, 1)
    chk("04a preview(n=1) writes", os.path.exists(os.path.join(tmp, "reformat_preview.png")), True)

    # 02: a blank centre cell reads as nan, not a crash
    P = load("p", "02_pair_passes.py")
    P.MANIFEST_DIR = tmp
    with open(os.path.join(tmp, "manifest_scenes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["slide", "scene_index", "slide_serial", "section_order",
                                           "center_x_um", "center_y_um"])
        w.writeheader()
        w.writerow({"slide": 1, "scene_index": 0, "slide_serial": 1, "section_order": 1,
                    "center_x_um": "", "center_y_um": "12.5"})
    rows = P.load_scenes()
    chk("02 blank centre -> nan", np.isnan(rows[0]["center_x_um"]), True)
    chk("02 filled centre still parses", rows[0]["center_y_um"], 12.5)

    # 01_overviews: a marker missing from display_ranges.json is a clear stop
    O = load("o", "01_overviews.py")
    chk("01 channel_range returns the pair", O.channel_range({"DAPI": {"lo": 1, "hi": 9}}, "DAPI"), (1, 9))
    msg = ""
    try:
        O.channel_range({"DAPI": {"lo": 1, "hi": 9}}, "AF488")
    except SystemExit as exc:
        msg = str(exc)
    chk("01 missing marker names the file to delete", "display_ranges.json" in msg and "AF488" in msg, True)

    # 01g: no module-level fallback list; analyse() reports through its argument
    G = load("g", "01g_saturation_map.py")
    chk("01g has no module-level _fallbacks", hasattr(G, "_fallbacks"), False)
    fb = []
    got = G.analyse({"animal": "LSX", "marker_channel": "AF568", "scene_uid": "nope",
                     "slide": "1", "section_order": "1"}, 5.2, fb)
    chk("01g analyse() on a missing section returns None", got, None)
    chk("...and records no fallback", fb, [])
    # report() printed main()'s local `fallbacks` and raised NameError at the
    # end of every 01g run; it now takes the list as an argument.
    import contextlib
    import io
    buf = io.StringIO()
    err = None
    try:
        with contextlib.redirect_stdout(buf):
            G.report([{"scene_uid": "s1", "animal": "LSX", "verdict": "negligible",
                       "contrast": 2.0}], "AF568", ["s1"])
    except Exception as exc:  # noqa: BLE001
        err = repr(exc)
    chk("01g report() runs to the end", err, None)
    chk("...and names the proxy fallbacks", "fell back to the 8-bit proxy" in buf.getvalue(), True)

    # 04l: --worklist that does not exist stops with a message, not a traceback
    r = subprocess.run([PY, os.path.join(SCRIPTS, "04l_roi_curator.py"),
                        "--worklist", os.path.join(tmp, "nope.csv"),
                        "--out", os.path.join(tmp, "x.html")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    chk("04l missing worklist exits 1", r.returncode, 1)
    chk("...without a traceback", "Traceback" in r.stderr, False)
    chk("...and names 04n", "04n_roi_worklist" in (r.stdout + r.stderr), True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------- stages that never said "failed"
# Each of these printed its problem and exited 0, so the app showed the step
# done. Now each returns 1 with the reason as its LAST printed line, which is
# the line app/runner.py shows.
import contextlib  # noqa: E402
import io  # noqa: E402
import zipfile  # noqa: E402

import tifffile  # noqa: E402


def last_line(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def call_main(mod, argv):
    saved = sys.argv
    out = io.StringIO()
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(out):
            try:
                rc = mod.main()
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
                print(exc.code)
    finally:
        sys.argv = saved
    return rc, out.getvalue()


tmp = tempfile.mkdtemp(prefix="guards2_")
try:
    # 00b: a corrupt, a missing and an unreadable archive each fail the run.
    V = load("v2", "00b_verify_extraction.py")
    src = os.path.join(tmp, "slides")
    os.makedirs(src)
    V.SOURCE_DIR, V.QC_DIR = src, os.path.join(tmp, "qc")
    rc, out = call_main(V, ["00b"])
    chk("00b with no archives has nothing to verify: 0", rc, 0)
    with zipfile.ZipFile(os.path.join(src, "batch.zip"), "w") as z:
        z.writestr("A_1a.czi", b"12345")
        z.writestr("A_1b.czi", b"12345")
    with open(os.path.join(src, "A_1a.czi"), "wb") as fh:
        fh.write(b"1234567")                       # larger than recorded: corrupt
    with open(os.path.join(src, "broken.zip"), "wb") as fh:
        fh.write(b"not a zip")
    rc, out = call_main(V, ["00b"])
    chk("00b with a corrupt, a missing and a bad archive returns 1", rc, 1)
    chk("...counting each in its last line",
        all(w in last_line(out) for w in ("1 size mismatch", "1 missing",
                                          "1 archive problem")), True)
    with open(os.path.join(src, "A_1a.czi"), "wb") as fh:
        fh.write(b"12345")
    with open(os.path.join(src, "A_1b.czi"), "wb") as fh:
        fh.write(b"12345")
    os.remove(os.path.join(src, "broken.zip"))
    rc, out = call_main(V, ["00b"])
    chk("00b with every CZI matching returns 0", rc, 0)

    # 01f: build with no usable section, verify with no field.
    F = load("f", "01f_tilefield.py")
    F.FIELD_DIR = os.path.join(tmp, "flatfield")
    F.OUT_ROOT = tmp
    blank = os.path.join(tmp, "blank_c0.tif")
    tifffile.imwrite(blank, np.zeros((64, 64), np.uint16))
    rc, out = call_main(F, ["01f", "build", blank])
    chk("01f build with no usable section returns 1", rc, 1)
    chk("...naming the channel last", "channel(s) [0]" in last_line(out), True)
    rc, out = call_main(F, ["01f", "verify", blank])
    chk("01f verify with no field returns 1", rc, 1)
    chk("...saying so last", "run 01f build first" in last_line(out), True)

    # 01c: nothing to measure, and nothing measurable.
    C = load("c", "01c_measure_tile_artifact.py")
    C.REPORT_DIR = os.path.join(tmp, "tile_artifact")
    rc, out = call_main(C, ["01c", os.path.join(tmp, "nowhere", "*.tif")])
    chk("01c with no sections returns 1 (was None)", rc, 1)
    rc, out = call_main(C, ["01c", blank])
    chk("01c with nothing measurable returns 1", rc, 1)
    chk("...saying so last", "no section could be measured" in last_line(out), True)
    chk("...and writes no summary for the app to read as done",
        os.path.exists(C.summary_path(tmp)), False)

    # 01h: a channel with too few tiles is counted and fails the run.
    H = load("h", "01h_tilefield_raw.py")
    H.SOURCE_DIR = src
    H.OUT_ROOT = tmp
    H.FIELD_DIR = os.path.join(tmp, "flatfield_raw")
    os.makedirs(os.path.join(tmp, "manifest"), exist_ok=True)
    with open(os.path.join(tmp, "manifest", "manifest_files.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "marker_channel", "is_redundant_copy"])
        w.writeheader()
        w.writerow({"file": "A_1a.czi", "marker_channel": "M1", "is_redundant_copy": "0"})
        w.writerow({"file": "gone.czi", "marker_channel": "M1", "is_redundant_copy": "0"})
    H.collect_tiles = lambda paths, n, rng, failed=None: (
        failed.append(("A_1a.czi", "boom")) if failed is not None else None,
        {"DAPI": [np.ones((H.TILE_N, H.TILE_N))] * 3})[1]
    H.stitch_map = lambda path: None
    rc, out = call_main(H, ["01h", "--files", "2"])
    chk("01h with too few tiles for a channel returns 1", rc, 1)
    chk("...naming the channel and the unreadable file last",
        "no field for DAPI" in last_line(out) and "A_1a.czi" in last_line(out), True)
    chk("...and counts the manifest file not on disk", "1 of 2 file(s)" in out, True)

    # 01d: a focus.csv section with no overview PNG is counted, and fails.
    D = load("d", "01d_contactsheets.py")
    D.SHEET_DIR = os.path.join(tmp, "sheets")
    D.OVERVIEW_DIR = os.path.join(tmp, "overviews")
    D.QC_CSV = os.path.join(tmp, "focus.csv")
    with open(D.QC_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["scene_uid", "animal", "slide", "variant",
                                           "marker_channel", "scene_index", "section_order",
                                           "focus_score", "tissue_fraction",
                                           "tissue_area_mm2", "saturated_fraction"])
        w.writeheader()
        for i in (1, 2):
            w.writerow({"scene_uid": f"A_s01a_sc0{i}", "animal": "A", "slide": "1",
                        "variant": "a", "marker_channel": "M1", "scene_index": str(i),
                        "section_order": str(i), "focus_score": "1", "tissue_fraction": "0.5",
                        "tissue_area_mm2": "1", "saturated_fraction": "0"})
    present = os.path.join(D.OVERVIEW_DIR, "A", "M1")
    os.makedirs(present)
    Image.fromarray(np.zeros((20, 30, 3), np.uint8)).save(
        os.path.join(present, "A_s01a_sc01_RGB.png"))
    rc, out = call_main(D, ["01d"])
    chk("01d with a section's overview missing returns 1", rc, 1)
    chk("...counting it in the last line",
        "1 of 2 section(s)" in last_line(out) and "A_s01a_sc02" in last_line(out), True)
    Image.fromarray(np.zeros((20, 30, 3), np.uint8)).save(
        os.path.join(present, "A_s01a_sc02_RGB.png"))
    rc, out = call_main(D, ["01d"])
    chk("01d with every overview present returns 0", rc, 0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
