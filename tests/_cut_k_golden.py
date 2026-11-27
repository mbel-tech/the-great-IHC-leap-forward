"""Run 06a on a fixed fixture with a given scripts folder; write what it wrote.

Not a test. tests/test_cut_k.py runs it, in a child process each time, to show
that a study with no `positivity` block gets byte-identical 06a outputs from
the code before and after the cut multiplier became configurable.

    python tests/_cut_k_golden.py <scripts dir> <out dir> <mode>

`mode` is what the study config says about the cut:
  none      no `positivity` block at all (every study before this key)
  explicit  positivity.cut_k = {"*": 3.0}, the default written out
  k4        positivity.cut_k = {"AF568": 4.0}, a real change

The fixture is a frozen copy of tests/test_roi_dataset.py's (its main and its
two-marker fixtures, as of 19f24a6), plus a third section with two background
discs so the leave-one-disc-out rate (detector_specificity.csv) is computed. It
is copied rather than imported so that editing that suite's fixture cannot
silently move the committed golden files in tests/golden/cut_k/.

The golden files there were made by running this against the scripts of
19f24a6:   python tests/_cut_k_golden.py <19f24a6>/scripts tests/golden/cut_k none
"""

import csv
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["LS_ATLAS_BUNDLE"] = ""

#: The files compared, by the name each fixture's copy is written under.
OUTPUTS = ("roi_measurements.csv", "positivity_cuts.csv", "detector_specificity.csv")
FIXTURES = ("main", "two_marker")

NUC_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "roi_index", "nucleus_id", "czi_x", "czi_y", "sec_x", "sec_y",
            "area_um2", "equiv_diam_um", "dapi_mean", "marker_mean",
            "marker_median", "marker_p90", "censored", "artifact"]
BOX_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "sec_x", "sec_y", "sec_r", "czi_file", "scene_index", "czi_cx",
            "czi_cy", "czi_x0", "czi_y0", "czi_w", "czi_h",
            "axis_a_um", "axis_b_um"]
BG_S1 = [80.0, 90.0, 100.0, 110.0, 120.0]
DIAM = 10.0
AXIS = 1000.0


def nucleus(uid, kind, region, idx, nid, value, diam=DIAM, cen=0, art=0,
            animal="LS01"):
    return {"scene_uid": uid, "animal": animal, "marker": "AF568",
            "roi_kind": kind, "region": region, "seed_n": "1",
            "roi_index": idx, "nucleus_id": nid, "czi_x": 0, "czi_y": 0,
            "sec_x": 128, "sec_y": 128, "area_um2": 50, "equiv_diam_um": diam,
            "dapi_mean": 1000, "marker_mean": value, "marker_median": value,
            "marker_p90": value, "censored": cen, "artifact": art}


def box(uid, kind, region, animal="LS01"):
    return {"scene_uid": uid, "animal": animal, "marker": "AF568",
            "roi_kind": kind, "region": region, "seed_n": "1",
            "sec_x": 128, "sec_y": 128, "sec_r": 20, "czi_file": "x.czi",
            "scene_index": 0, "czi_cx": 0, "czi_cy": 0, "czi_x0": 0,
            "czi_y0": 0, "czi_w": 100, "czi_h": 100,
            "axis_a_um": AXIS, "axis_b_um": AXIS}


def main_fixture():
    nuc, boxes = [], []
    boxes.append(box("S1", "roi", "Dm"))
    boxes.append(box("S1", "background", "__background__"))
    nid = 0
    for v in (100.0, 200.0, 150.0, 140.0):
        nid += 1
        nuc.append(nucleus("S1", "roi", "Dm", 1, nid, v))
    nid += 1
    nuc.append(nucleus("S1", "roi", "Dm", 1, nid, 5.0, cen=1))
    nid += 1
    nuc.append(nucleus("S1", "roi", "Dm", 1, nid, 9999.0, diam=99.0, art=1))
    for v in BG_S1:
        nid += 1
        nuc.append(nucleus("S1", "background", "__background__", 2, nid, v))
    boxes.append(box("S2", "roi", "Dm"))
    boxes.append(box("S2", "background", "__background__"))
    for v in (100.0, 200.0):
        nid += 1
        nuc.append(nucleus("S2", "roi", "Dm", 1, nid, v))
    for v in (80.0, 90.0, 110.0, 120.0):
        nid += 1
        nuc.append(nucleus("S2", "background", "__background__", 2, nid, v))
    # S3 (not in test_roi_dataset): two background discs of six, so each can
    # be left out against the other - the leave-one-disc-out rate.
    boxes.append(box("S3", "roi", "Dm", animal="LS02"))
    boxes.append(box("S3", "background", "__background__", animal="LS02"))
    boxes.append(box("S3", "background", "__background__", animal="LS02"))
    for v in (100.0, 130.0, 150.0, 175.0, 260.0):
        nid += 1
        nuc.append(nucleus("S3", "roi", "Dm", 1, nid, v, animal="LS02"))
    for idx, vals in ((2, (90.0, 95.0, 100.0, 105.0, 110.0, 150.0)),
                      (3, (85.0, 100.0, 102.0, 108.0, 112.0, 140.0))):
        for v in vals:
            nid += 1
            nuc.append(nucleus("S3", "background", "__background__", idx, nid, v,
                               animal="LS02"))
    return nuc, boxes


def two_marker_fixture():
    nuc, boxes = [], []
    nid = 0
    for uid, mk in (("M1", "AF568"), ("M2", "AF488")):
        boxes.append(dict(box(uid, "roi", "Dm"), marker=mk))
        boxes.append(dict(box(uid, "background", "__background__"), marker=mk))
        diam = 10.0 if mk == "AF568" else 6.0
        for v in (100.0, 200.0, 150.0):
            nid += 1
            nuc.append(dict(nucleus(uid, "roi", "Dm", 1, nid, v, diam=diam),
                            marker=mk))
        for v in BG_S1:
            nid += 1
            nuc.append(dict(nucleus(uid, "background", "__background__", 2, nid, v),
                            marker=mk))
    return nuc, boxes


def write_config(tmp, mode):
    sys.path.insert(0, HERE)
    from _fixture import write_study
    path = write_study(tmp)
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.pop("positivity", None)
    if mode == "explicit":
        cfg["positivity"] = {"cut_k": {"*": 3.0}}
    elif mode == "k4":
        cfg["positivity"] = {"cut_k": {"AF568": 4.0}}
    elif mode != "none":
        raise SystemExit(f"unknown mode {mode!r}")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)
    return path


def main(scripts, out, mode):
    scripts = os.path.abspath(scripts)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="cutk_")
    try:
        os.environ["LS_CONFIG"] = write_config(tmp, mode)
        os.environ.pop("LS_CONFIG_STRICT", None)
        sys.path.insert(0, scripts)
        spec = importlib.util.spec_from_file_location(
            "g6a", os.path.join(scripts, "06a_roi_dataset.py"))
        g6a = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g6a)
        lo = g6a.LO
        # Which code ran, so the caller can refuse a comparison of new with new.
        info = {"ls_overlay": os.path.abspath(lo.__file__),
                "06a": os.path.abspath(g6a.__file__),
                "section_cut_takes_k": "k" in lo.section_cut.__code__.co_varnames,
                "config": json.load(open(os.environ["LS_CONFIG"], encoding="utf-8"))
                          .get("positivity")}
        for name, build in (("main", main_fixture), ("two_marker", two_marker_fixture)):
            work = os.path.join(tmp, name)
            os.makedirs(work)
            nuc, boxes = build()
            npath = os.path.join(work, "roi_nuclei.csv")
            bpath = os.path.join(work, "roi_boxes.csv")
            for path, cols, rows in ((npath, NUC_COLS, nuc), (bpath, BOX_COLS, boxes)):
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=cols)
                    w.writeheader()
                    w.writerows(rows)
            g6a.NUCLEI_CSV = npath
            g6a.MEAS_CSV = os.path.join(work, "roi_measurements.csv")
            g6a.SPEC_CSV = os.path.join(work, "detector_specificity.csv")
            g6a.CUTS_CSV = os.path.join(work, "positivity_cuts.csv")
            g6a.G5.all_boxes = lambda p=bpath: g6a.G5.load_csv(p)
            rc = g6a.main()
            if rc != 0:
                raise SystemExit(f"06a exited {rc} on the {name} fixture")
            for f in OUTPUTS:
                shutil.copyfile(os.path.join(work, f), os.path.join(out, f"{name}__{f}"))
        print(json.dumps(info))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
