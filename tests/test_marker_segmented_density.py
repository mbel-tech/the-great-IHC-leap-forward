"""Nuclei found on the marker itself: positive density, and no denominator.

When a paired marker is segmented on its own channel (the LS PCNA scans, whose
DAPI plane is noise), `n_nuclei` is "objects the marker lit up". A fraction of
it, or a "cell density" built from it, would read as a statement about cells
and not be one. So 06a withholds `frac_positive` and `cell_density_per_mm2` for
such a marker and says why in `denominator_withheld_reason`; 06c and 06d carry
that rather than recomputing the two numbers from counts. What the marker CAN
say - the positive count and the positive density - stays.

Abercrombie's h for such a marker is the size of the objects counted as
positive, not of everything StarDist outlined on the green channel.

SYNTHETIC. This pins the arithmetic and the plumbing; it says nothing about
whether PCNA+ nuclei are found.

Run:  python tests/test_marker_segmented_density.py
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from _fixture import use_temp_study, load_stage             # noqa: E402

STUDY = use_temp_study()
A6 = load_stage("06a_roi_dataset.py")
G5 = A6.G5

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(60) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


NUC_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "roi_index", "nucleus_id", "czi_x", "czi_y", "sec_x", "sec_y",
            "area_um2", "equiv_diam_um", "dapi_mean", "marker_mean",
            "marker_median", "marker_p90", "censored", "artifact",
            "off_tissue", "segmented_on", "backend", "nucleus_shaped"]
BOX_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "sec_x", "sec_y", "sec_r", "czi_file", "scene_index", "czi_cx",
            "czi_cy", "czi_x0", "czi_y0", "czi_w", "czi_h",
            "axis_a_um", "axis_b_um"]
# Five background objects: median 100, MAD 10 -> cut = 100 + 3*14.826 = 144.5
BG = [80.0, 90.0, 100.0, 110.0, 120.0]
# (value, diameter): two clear the cut, one does not
ROI_OBJECTS = [(100.0, 5.0), (200.0, 8.0), (150.0, 10.0)]


def box(uid, marker, kind, region):
    return {"scene_uid": uid, "animal": "LS01", "marker": marker,
            "roi_kind": kind, "region": region, "seed_n": "1",
            "sec_x": 128, "sec_y": 128, "sec_r": 20, "czi_file": "x.czi",
            "scene_index": 0, "czi_cx": 0, "czi_cy": 0, "czi_x0": 0,
            "czi_y0": 0, "czi_w": 100, "czi_h": 100,
            "axis_a_um": 1000.0, "axis_b_um": 1000.0}


def nucleus(uid, marker, kind, region, idx, nid, value, diam, seg_on):
    return {"scene_uid": uid, "animal": "LS01", "marker": marker,
            "roi_kind": kind, "region": region, "seed_n": "1",
            "roi_index": idx, "nucleus_id": nid, "czi_x": 0, "czi_y": 0,
            "sec_x": 128, "sec_y": 128, "area_um2": 50, "equiv_diam_um": diam,
            "dapi_mean": 1000, "marker_mean": value, "marker_median": value,
            "marker_p90": value, "censored": 0, "artifact": 0, "off_tissue": 0,
            "segmented_on": seg_on, "backend": "stardist",
            "nucleus_shaped": "1"}


def fixture(tmp, legacy=False):
    """AF488 segmented on itself, AF568 on the counterstain, one section each."""
    nuc, boxes = [], []
    nid = 0
    for uid, marker, seg_on in (("S1", "AF488", "AF488"),
                                ("S2", "AF568", "nuclear")):
        boxes.append(box(uid, marker, "roi", "Dm"))
        boxes.append(box(uid, marker, "background", "__background__"))
        for v, d in ROI_OBJECTS:
            nid += 1
            nuc.append(nucleus(uid, marker, "roi", "Dm", 1, nid, v, d, seg_on))
        for v in BG:
            nid += 1
            nuc.append(nucleus(uid, marker, "background", "__background__",
                               2, nid, v, 7.0, seg_on))
    cols = NUC_COLS
    if legacy:
        cols = [c for c in NUC_COLS
                if c not in ("segmented_on", "backend", "nucleus_shaped")]
    npath, bpath = os.path.join(tmp, "n.csv"), os.path.join(tmp, "b.csv")
    for path, cs, rows in ((npath, cols, nuc), (bpath, BOX_COLS, boxes)):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cs, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return npath, bpath


def run_06a(tmp, stem, **kw):
    keep = (A6.NUCLEI_CSV, A6.MEAS_CSV, A6.SPEC_CSV, G5.all_boxes)
    npath, bpath = fixture(tmp, **kw)
    A6.NUCLEI_CSV = npath
    A6.MEAS_CSV = os.path.join(tmp, stem + "_measurements.csv")
    A6.SPEC_CSV = os.path.join(tmp, stem + "_specificity.csv")
    G5.all_boxes = lambda: G5.load_csv(bpath)
    try:
        rc = A6.main()
        meas = G5.load_csv(A6.MEAS_CSV)
        spec = G5.load_csv(A6.SPEC_CSV)
        paths = (npath, bpath, A6.MEAS_CSV)
    finally:
        A6.NUCLEI_CSV, A6.MEAS_CSV, A6.SPEC_CSV, G5.all_boxes = keep
    return rc, meas, spec, paths


def main():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="lsmsd_")
    try:
        print("--- 06a: the marker segmented on itself ---")
        rc, meas, spec, paths = run_06a(tmp, "own")
        chk("06a exits 0", rc, 0)
        chk("every row carries denominator_withheld_reason, blank included",
            all("denominator_withheld_reason" in r for r in meas), True)
        pcna = [r for r in meas if r["marker"] == "AF488"]
        perk = [r for r in meas if r["marker"] == "AF568"]
        roi = next(r for r in pcna if r["roi_kind"] == "roi")
        chk("the reason names the channel it came from",
            "AF488" in roi["denominator_withheld_reason"], True)
        chk("...and says there is no count of all cells",
            "all cells" in roi["denominator_withheld_reason"], True)
        chk("the background disc is withheld too",
            all(r["denominator_withheld_reason"] for r in pcna), True)
        chk("frac_positive is blank", roi["frac_positive"], "")
        chk("cell_density_per_mm2 is blank", roi["cell_density_per_mm2"], "")
        chk("n_positive stands", roi["n_positive"], "2")
        chk("positive_density_per_mm2 stands",
            float(roi["positive_density_per_mm2"]) > 0, True)
        chk("n_nuclei is still the object count", roi["n_nuclei"], "3")

        print()
        print("--- h is the size of the objects counted as positive ---")
        h_pos = (8.0 + 10.0) / 2
        chk("mean_nucleus_diam_um is the positives' mean",
            float(roi["mean_nucleus_diam_um"]), h_pos)
        chk("...and the Abercrombie factor follows from it",
            round(float(roi["abercrombie_factor"]), 4),
            round(A6.T_UM / (A6.T_UM + h_pos), 4))
        bgd = next(r for r in pcna if r["roi_kind"] == "background")
        chk("a background disc takes the marker's positive-object h",
            round(float(bgd["abercrombie_factor"]), 4),
            round(A6.T_UM / (A6.T_UM + h_pos), 4))

        print()
        print("--- the counterstain-segmented marker does not move ---")
        proi = next(r for r in perk if r["roi_kind"] == "roi")
        chk("its reason is blank", proi["denominator_withheld_reason"], "")
        chk("frac_positive is there", proi["frac_positive"], "0.6667")
        chk("cell_density_per_mm2 is there",
            float(proi["cell_density_per_mm2"]) > 0, True)
        h_all = (5.0 + 8.0 + 10.0) / 3
        chk("h is the mean of ALL its nuclei, as it always was",
            round(float(proi["mean_nucleus_diam_um"]), 2), round(h_all, 2))

        print()
        print("--- detector_specificity ---")
        s_pcna = next(s for s in spec if s["marker"] == "AF488")
        s_perk = next(s for s in spec if s["marker"] == "AF568")
        chk("every row carries bg_false_positives_per_mm2",
            all("bg_false_positives_per_mm2" in s for s in spec), True)
        chk("the withheld marker has no false-positive RATE",
            s_pcna["false_positive_rate"], "")
        chk("...but has false positives per mm2",
            s_pcna["bg_false_positives_per_mm2"] != "", True)
        chk("...and says why", bool(s_pcna["denominator_withheld_reason"]), True)
        chk("the counterstain marker keeps its rate",
            s_perk["false_positive_rate"] != "", True)

        print()
        print("--- a file with no provenance columns is untouched ---")
        rc, lmeas, _, _ = run_06a(tmp, "legacy", legacy=True)
        chk("06a exits 0", rc, 0)
        chk("no row is withheld",
            {r["denominator_withheld_reason"] for r in lmeas}, {""})
        chk("...and every ROI keeps its fraction",
            all(r["frac_positive"] != "" for r in lmeas
                if r["roi_kind"] == "roi"), True)

        print()
        print("--- 06c: carried, not recomputed ---")
        npath, bpath, mpath = paths
        C6 = load_stage("06c_excel_dataset.py")
        C6.NUCLEI_CSV, C6.MEAS_CSV = npath, mpath
        C6.G5.BOX_CSV = bpath
        C6.G5.all_boxes = lambda: C6.G5.load_csv(bpath)
        C6.animal_environment = lambda: {}
        out = os.path.join(tmp, "wb.xlsx")
        try:
            from openpyxl import load_workbook
            rc = C6.main(["--out", out])
            rows = list(load_workbook(out)["by_roi"].values)
            cols = list(rows[0])
            recs = {r["marker"]: r for r in (dict(zip(cols, x))
                                             for x in rows[1:])}
            chk("06c exits 0", rc, 0)
            chk("by_roi has the column", "denominator_withheld_reason" in cols,
                True)
            a, b = recs["AF488"], recs["AF568"]
            chk("PCNA's frac_positive is blank", a["frac_positive"] in ("", None),
                True)
            chk("PCNA's cells_per_mm2 is blank", a["cells_per_mm2"] in ("", None),
                True)
            chk("PCNA keeps n_positive and positive_cells_per_mm2",
                (a["n_positive"], a["positive_cells_per_mm2"] is not None),
                (2, True))
            chk("PCNA's reason is carried",
                "AF488" in (a["denominator_withheld_reason"] or ""), True)
            chk("pERK keeps its fraction", b["frac_positive"], 0.6667)
            chk("...and its reason is blank",
                b["denominator_withheld_reason"] in ("", None), True)
        except ModuleNotFoundError as exc:
            print(f"skip  06c ({exc})")

        print()
        print("--- 06d: the same, per slide ---")
        D6 = load_stage("06d_excel_by_slide.py")
        meas_rows = G5.load_csv(mpath)
        out6 = D6.group_rows(meas_rows, lambda u: "slide1", "slide", {}, {})
        by_mk = {r["marker"]: r for r in out6}
        chk("PCNA's frac_positive is blank", by_mk["AF488"]["frac_positive"], "")
        chk("PCNA's cells_per_mm2 is blank", by_mk["AF488"]["cells_per_mm2"], "")
        chk("PCNA's reason is carried",
            "AF488" in by_mk["AF488"]["denominator_withheld_reason"], True)
        chk("pERK keeps its fraction", by_mk["AF568"]["frac_positive"], 0.6667)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("ALL PASS" if not failures else f"{len(failures)} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
