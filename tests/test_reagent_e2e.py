"""A reagent control scan sharing a slide with a stained scan, end to end.

The paired layout's trap: `LS45_3nc.czi` is a no-primary control scanned on
slide 3 for AF568, and `LS45_3a.czi` is the stained AF568 scan of the same
slide. They share (animal, slide, marker), so 02's re-scan resolution used to
keep the stained scan and drop the control: no pairs.csv row, so 04m called it
unpaired, 04n queued it last or not at all, and the report never listed it.

Here the control is followed through every stage that decides whether it is
seen: 00 (manifest, its own series), 02 (carried as its own entry, never
instead of the stained scan), a 05a boxes fixture, 05c's order (controls
first) and 06a, where it must appear in results/reagent_controls.csv - with
its measures where 05c measured it, and as a "not measured yet" row, saying
why, where it did not. It never appears in roi_measurements.csv.

Run:  python tests/test_reagent_e2e.py
"""

import contextlib
import csv
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts"), HERE]

from _fixture import use_temp_study, load_stage  # noqa: E402

NAMING = {"pattern": r"^(?P<subject>[A-Za-z]+\d+)(?:_(?P<slide>\d+)(?P<replicate>[a-z]*))?"
                     r"(?P<rest>_[A-Za-z]+)?\.czi$",
          "case_insensitive": True}
CONTROL = "LS45_3nc.czi"
STUDY = use_temp_study(
    slide_naming=NAMING, pixel_size_um=0.65,
    acquisition={"layout": "paired", "markers": ["AF568", "AF488"]},
    reagent_controls={"declared": "yes",
                      "files": {CONTROL: {"kind": "no_primary", "markers": [],
                                          "source": "name"}}})
M = load_stage("00_manifest.py", name="lsstage_00_e2e")
P2 = load_stage("02_pair_passes.py", name="lsstage_02_e2e")
D5 = load_stage("05c_detect_rois.py", name="lsstage_05c_e2e")
G6A = load_stage("06a_roi_dataset.py", name="lsstage_06a_e2e")
G5 = G6A.G5
import ls_reagent_controls as RC  # noqa: E402
import ls_reagent_report as RR  # noqa: E402

src = open(os.path.join(HERE, "test_autodetect.py")).read().split('print("--- one scan per marker')[0]
ns = {"__name__": "autodetect_helpers", "__file__": os.path.join(HERE, "test_autodetect.py")}
exec(compile(src, "test_autodetect_helpers", "exec"), ns)
xml, write_czi = ns["xml"], ns["write_czi"]
DAPI, AF568, AF488 = ns["DAPI"], ns["AF568"], ns["AF488"]

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# Scenes per file: the control has MORE scenes than the stained scan, the case
# in which "most scenes" used to pick it.
N_SCENES = {"LS45_3a.czi": 2, CONTROL: 3, "LS45_3b.czi": 2, "LS45_4a.czi": 2,
            "LS45_4b.czi": 2}
_read, _summ = M.read_metadata, M.summarise
M.read_metadata = lambda fh: (os.path.basename(fh.name), _read(fh))


def _summarise(tagged):
    name, root = tagged
    info = _summ(root)
    n = N_SCENES.get(name, 2)
    info["scenes"] = [{"index": i, "name": f"ScanRegion{i}", "center_x_um": 9000.0 * i,
                       "center_y_um": 0.0, "width_um": 7000.0, "height_um": 7000.0}
                      for i in range(1, n + 1)]
    info["size_s"] = n
    return info


M.summarise = _summarise

# ---------------------------------------------------------------- 00
print("--- 00: the manifest ---")
for p in glob.glob(os.path.join(STUDY.source_dir, "*.czi")):
    os.remove(p)
for name, marker in (("LS45_3a.czi", AF568), (CONTROL, AF568), ("LS45_4a.czi", AF568),
                     ("LS45_3b.czi", AF488), ("LS45_4b.czi", AF488)):
    write_czi(os.path.join(STUDY.source_dir, name), xml([DAPI, marker]))
with contextlib.redirect_stdout(io.StringIO()) as out00:
    code = M.main() or 0
chk("00 runs", code, 0)
scenes = read(os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv"))
ctl_uids = sorted(r["scene_uid"] for r in scenes if r["file"] == CONTROL)
chk("the control's scenes are in the manifest, flagged",
    (ctl_uids, sorted({r["reagent_control"] for r in scenes if r["file"] == CONTROL})),
    (["LS45_s03nc_sc01", "LS45_s03nc_sc02", "LS45_s03nc_sc03"], ["no_primary"]))
chk("...sharing animal, slide and marker with the stained scan",
    sorted({(r["animal"], r["slide"], r["marker_channel"]) for r in scenes
            if r["file"] in (CONTROL, "LS45_3a.czi")}), [("LS45", "3", "AF568")])

# ---------------------------------------------------------------- 02
print("--- 02: pairing the passes ---")
with contextlib.redirect_stdout(io.StringIO()):
    P2.main()
pairs = read(os.path.join(P2.OUT_ROOT, "pairs.csv"))
a_uids = [r["af568_scene_uid"] for r in pairs if r["af568_scene_uid"]]
chk("e2e_02: the control's scenes reach pairs.csv", [u for u in ctl_uids if u in a_uids],
    ctl_uids)
chk("...and the stained scan of slide 3 is still the one paired with the other pass",
    sorted(r["af568_scene_uid"] for r in pairs if r["status"] == "matched"
           and r["af568_scene_uid"].startswith("LS45_s03")),
    ["LS45_s03a_sc01", "LS45_s03a_sc02"])
chk("...the control is matched with no stained scan",
    [r["af488_scene_uid"] for r in pairs if r["af568_scene_uid"] in ctl_uids], ["", "", ""])
stained_secs = [r["section_uid"] for r in pairs if r["af568_scene_uid"] not in ctl_uids]
chk("...and numbered after the animal's stained sections",
    sorted(r["section_uid"] for r in pairs if r["af568_scene_uid"] in ctl_uids),
    [f"LS45_sec{i:03d}" for i in range(len(stained_secs) + 1, len(stained_secs) + 4)])
chk("...the internal series key is in no file", any("#rc" in v for r in pairs for v in r.values()),
    False)

# ---------------------------------------------------------------- 05a (fixture)
print("--- 05a boxes (fixture) and 05c's order ---")
BOX_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "sec_x", "sec_y", "sec_r", "czi_file", "scene_index", "czi_cx",
            "czi_cy", "czi_x0", "czi_y0", "czi_w", "czi_h",
            "axis_a_um", "axis_b_um"]
# Boxes on the stained sections and on two of the control's three scenes: the
# third was never given ROIs (excluded in curation, say).
BOXED = ["LS45_s03a_sc01", "LS45_s03a_sc02", "LS45_s04a_sc01", "LS45_s04a_sc02",
         "LS45_s03nc_sc01", "LS45_s03nc_sc02"]
boxes = []
for uid in BOXED:
    for kind, region in (("roi", "Dm"), ("background", "__background__")):
        boxes.append({"scene_uid": uid, "animal": "LS45", "marker": "AF568", "roi_kind": kind,
                      "region": region, "seed_n": "1", "sec_x": 128, "sec_y": 128,
                      "sec_r": 20, "czi_file": "x.czi", "scene_index": 0, "czi_cx": 0,
                      "czi_cy": 0, "czi_x0": 0, "czi_y0": 0, "czi_w": 100, "czi_h": 100,
                      "axis_a_um": 1000.0, "axis_b_um": 1000.0})
bpath = os.path.join(STUDY.out_root, "reformatted", "roi_boxes_AF568.csv")
os.makedirs(os.path.dirname(bpath), exist_ok=True)
with open(bpath, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=BOX_COLS)
    w.writeheader()
    w.writerows(boxes)

lookup = RC.manifest_lookup(G6A.CONFIG)
with contextlib.redirect_stdout(io.StringIO()):
    order = D5.measure_order(BOXED, set(), lookup, "AF568", "balanced")
chk("e2e_05c: 05c measures the control's boxed sections first",
    order[:2], ["LS45_s03nc_sc01", "LS45_s03nc_sc02"])

# ---------------------------------------------------------------- 06a
print("--- 06a: the report ---")
NUC_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "roi_index", "nucleus_id", "czi_x", "czi_y", "sec_x", "sec_y",
            "area_um2", "equiv_diam_um", "dapi_mean", "marker_mean",
            "marker_median", "marker_p90", "censored", "artifact"]
# A partial run, as 05c leaves it: the first control section and every stained
# one measured; the second control section boxed but not reached yet.
MEASURED = {"LS45_s03nc_sc01": ([100] * 10, [90, 95, 105, 200]),
            **{u: ([80, 90, 100, 110, 120], [100, 200, 150, 140]) for u in BOXED[:4]}}
nuc, nid = [], 0
for uid, (bg, roi) in MEASURED.items():
    for idx, kind, region, vals in ((1, "roi", "Dm", roi), (2, "background", "__background__", bg)):
        for v in vals:
            nid += 1
            nuc.append({"scene_uid": uid, "animal": "LS45", "marker": "AF568", "roi_kind": kind,
                        "region": region, "seed_n": "1", "roi_index": idx, "nucleus_id": nid,
                        "czi_x": 0, "czi_y": 0, "sec_x": 128, "sec_y": 128, "area_um2": 50,
                        "equiv_diam_um": 10.0, "dapi_mean": 1000, "marker_mean": v,
                        "marker_median": v, "marker_p90": v, "censored": 0, "artifact": 0})
res = os.path.join(STUDY.out_root, "results")
os.makedirs(res, exist_ok=True)
npath = os.path.join(res, "roi_nuclei.csv")
with open(npath, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=NUC_COLS)
    w.writeheader()
    w.writerows(nuc)
keep = (G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes)
G6A.NUCLEI_CSV = npath
G6A.MEAS_CSV = os.path.join(res, "roi_measurements.csv")
G6A.SPEC_CSV = os.path.join(res, "detector_specificity.csv")
G5.all_boxes = lambda: G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()) as out6a:
        code = G6A.main()
finally:
    G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes = keep
chk("06a runs", code, 0)
rep = read(os.path.join(res, RR.REPORT_CSV))
meas = read(os.path.join(res, "roi_measurements.csv"))
by = {r["scene_uid"]: r for r in rep}
chk("e2e_06a: every scene of the control is in reagent_controls.csv", sorted(by), ctl_uids)
chk("...the measured one with its measures, paired with its animal's stained sections",
    (by["LS45_s03nc_sc01"]["n_nuclei"], by["LS45_s03nc_sc01"]["pct_positive"] != "",
     by["LS45_s03nc_sc01"]["pairing"]), ("4", True, "same_animal"))
chk("...the boxed one 05c has not reached: a row, saying so",
    by["LS45_s03nc_sc02"]["note"], RR.NOT_MEASURED + ": " + G6A.WHY_NOT_DETECTED)
chk("...the one with no ROIs: a row, saying so",
    by["LS45_s03nc_sc03"]["note"], RR.NOT_MEASURED + ": " + G6A.WHY_NO_BOXES)
chk("...and 06a says so", "not measured yet" in out6a.getvalue(), True)
chk("no row of the control in roi_measurements.csv",
    [r["scene_uid"] for r in meas if r["scene_uid"] in ctl_uids], [])
chk("...while the stained scan of the same slide is there",
    sorted({r["scene_uid"] for r in meas if r["scene_uid"].startswith("LS45_s03a")}),
    ["LS45_s03a_sc01", "LS45_s03a_sc02"])

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
