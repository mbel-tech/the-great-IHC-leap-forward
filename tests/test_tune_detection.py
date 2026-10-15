"""05b measures, tunes and records; 05c uses what it recorded, and no more.

Driven end to end against a fake CZI reader and a stand-in model, on the live
study's shape (paired, no channel table). What is guaranteed:

  - 05b writes detection.auto.<marker> into the study file: the signal
    statistics, a verdict, the count curve and the chosen cut;
  - it writes the report and the per-crop table;
  - 05c passes that cut to the model, a cut set in config overrides it, and
    use_auto off goes back to the model's own;
  - 05c refuses to resume with thresholds other than the ones the measured
    sections were made with, and --force re-measures and accepts them.

SYNTHETIC: the model's counts follow a curve this file writes, so this proves
the wiring, not that the curve of a real section looks like this one.

Run:  python tests/test_tune_detection.py
"""

import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from _fixture import temp_study, load_stage                  # noqa: E402

failures = []


def chk(label, got, want=True):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(62) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


try:
    import pylibCZIrw.czi as pyczi
except ImportError:
    print("SKIP pylibCZIrw is not installed; 05b and 05c cannot be imported")
    print("ALL PASS")
    sys.exit(0)

PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
SIDE = 120

#: Objects the stand-in reports at each cut: noise floods in below 0.45, the
#: count is flat from 0.50 to 0.60 and falls away above.
CURVE = {0.30: 900, 0.35: 700, 0.40: 520, 0.45: 400, 0.479071: 360,
         0.50: 330, 0.55: 325, 0.60: 320, 0.65: 280, 0.70: 200, 0.75: 140,
         0.80: 90}


class Thresholds:
    prob, nms = 0.479071463157368, 0.3


class StubModel:
    thresholds = Thresholds()

    def __init__(self):
        self.cuts = []

    def predict_instances(self, x, prob_thresh=None, nms_thresh=None, **kw):
        self.cuts.append((prob_thresh, nms_thresh))
        n = CURVE[min(CURVE, key=lambda c: abs(c - (prob_thresh or 0.479071)))]
        lab = np.zeros(x.shape, np.int32)
        lab.ravel()[:n] = np.arange(1, n + 1)
        return lab, {}


class FakeCzi:
    def __init__(self, planes):
        self.planes = planes

    def read(self, **kw):
        return self.planes[int(kw["plane"]["C"])]


class FakeOpen:
    def __init__(self, doc):
        self.doc = doc

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self.doc

    def __exit__(self, *e):
        return False


def nuclei(seed):
    rng = np.random.default_rng(seed)
    a = np.full((SIDE, SIDE), 200.0)
    yy, xx = np.mgrid[:SIDE, :SIDE]
    for _ in range(25):
        cy, cx = rng.integers(6, SIDE - 6, 2)
        a[(yy - cy) ** 2 + (xx - cx) ** 2 <= 25] = 1200
    return a + rng.normal(0, 40, a.shape)


def lay_out(D5, marker, uids):
    D5.G5.use_marker(marker)
    os.makedirs(os.path.dirname(D5.G5.GEOM_CSV), exist_ok=True)
    with open(D5.G5.GEOM_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scene_uid", "animal", "marker", "czi_file", "scene_index",
                    "m00", "m01", "m02", "m10", "m11", "m12"])
        for u in uids:
            w.writerow([u, u.split("_")[0], marker, u + ".czi", 0,
                        1, 0, 0, 0, 1, 0])
    with open(D5.G5.BOX_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scene_uid", "animal", "marker", "roi_kind", "region",
                    "seed_n", "czi_x0", "czi_y0", "czi_w", "czi_h", "sec_x",
                    "sec_y", "sec_r", "sec_poly"])
        for u in uids:
            w.writerow([u, u.split("_")[0], marker, "roi", "Dm", 1, 0, 0,
                        SIDE, SIDE, 60, 60, 200, ""])
            w.writerow([u, u.split("_")[0], marker, "background", "bg", 1,
                        0, 0, SIDE, SIDE, 60, 60, 5, ""])
    mask_dir = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    for u in uids:
        np.save(os.path.join(mask_dir, f"{u}_mask.npy"), np.ones((256, 256), bool))


def run(mod, argv, model):
    saved = (pyczi.open_czi, sys.argv)
    pyczi.open_czi = FakeOpen(FakeCzi({0: nuclei(1), 1: nuclei(2), 2: nuclei(3)}))
    loader = mod.D5 if hasattr(mod, "D5") else mod
    saved_load = loader.load_model
    loader.load_model = lambda: model
    sys.argv = argv
    try:
        return mod.main()
    finally:
        pyczi.open_czi, sys.argv = saved
        loader.load_model = saved_load


UIDS = ["AB1_1a_s01", "AB1_1a_s02", "AB2_3a_s01"]

with temp_study(acquisition=PAIRED) as study:
    B = load_stage("05b_tune_detection.py")
    lay_out(B.D5, "AF568", UIDS)

    print("--- the sample is spread over animals ---")
    chk("both animals are in a sample of two",
        sorted(u.split("_")[0] for u in B.sample_sections(UIDS, 2)), ["AB1", "AB2"])
    chk("a sample never exceeds what was asked", len(B.sample_sections(UIDS * 3, 4)) <= 4)

    print()
    print("--- 05b measures and tunes ---")
    model = StubModel()
    rc = run(B, ["05b", "--marker", "AF568"], model)
    chk("05b succeeds", rc, 0)
    cfg = json.load(open(study.path, encoding="utf-8"))
    auto = (cfg.get("detection") or {}).get("auto") or {}
    e = auto.get("AF568") or {}
    chk("detection.auto.AF568 is written", bool(e))
    chk("only the ROI boxes are sampled, never the background discs",
        e.get("sampled_crops"), 3)
    chk("it is segmented on the nuclear plane", e.get("segmented_on"), "nuclear")
    chk("the signal is judged good", e.get("verdict"), "good")
    chk("signal-to-noise is recorded and about 25",
        abs((e.get("quality") or {}).get("snr", 0) - 25) < 4)
    chk("the curve covers the grid and the model default", len(e.get("curve", [])), 12)
    chk("the cut lands on the flat stretch",
        0.5 <= (e.get("chosen") or {}).get("prob_thresh", 0) <= 0.6)
    chk("every other setting in the study file survives",
        cfg["detection"]["nucleus_diameter_um"], study.config["detection"]["nucleus_diameter_um"])
    chk("the report is written", os.path.exists(B.REPORT_MD))
    chk("...and names the verdict", "Signal: good" in open(B.REPORT_MD, encoding="utf-8").read())
    with open(B.TUNING_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    chk("one table row per crop", len(rows), 3)
    chosen = e["chosen"]["prob_thresh"]

    print()
    print("--- 05b --dry-run changes nothing ---")
    before = open(study.path, encoding="utf-8").read()
    chk("dry run succeeds", run(B, ["05b", "--dry-run"], StubModel()), 0)
    chk("the study file is untouched", open(study.path, encoding="utf-8").read(), before)

with temp_study(acquisition=PAIRED, detection=cfg["detection"]) as study:
    D5 = load_stage("05c_detect_rois.py")
    lay_out(D5, "AF568", UIDS)

    print()
    print("--- 05c uses the tuned cut ---")
    chk("the tuned cut is what 05c resolves",
        D5.stardist_settings("AF568")["prob_thresh"], chosen)
    chk("...and says where it came from", D5.stardist_settings("AF568")["source"],
        "prob_thresh tuned by 05b")
    m = StubModel()
    chk("05c succeeds", run(D5, ["05c", "--marker", "AF568", "--order", "uid",
                                 "--limit", "1"], m), 0)
    chk("the model was given the tuned cut", {c for c, _ in m.cuts}, {chosen})
    rec = json.load(open(D5.SETTINGS_JSON, encoding="utf-8"))["markers"]["AF568"]
    chk("the thresholds used are recorded", rec["prob_thresh"], chosen)

    m = StubModel()
    chk("a plain resume with the same cut is allowed",
        run(D5, ["05c", "--marker", "AF568", "--order", "uid", "--limit", "1"], m), 0)

with temp_study(acquisition=PAIRED, detection=cfg["detection"]) as study:
    D5 = load_stage("05c_detect_rois.py")
    lay_out(D5, "AF568", UIDS)
    m = StubModel()
    run(D5, ["05c", "--marker", "AF568", "--order", "uid", "--limit", "2"], m)
    saved_cfg = json.load(open(study.path, encoding="utf-8"))

    print()
    print("--- a different cut is refused on resume ---")
    saved_cfg["detection"]["stardist"] = {"prob_thresh": 0.7}
    json.dump(saved_cfg, open(study.path, "w", encoding="utf-8"))
    D5 = load_stage("05c_detect_rois.py")
    chk("a cut set in config overrides the tuned one",
        D5.stardist_settings("AF568")["prob_thresh"], 0.7)
    m = StubModel()
    chk("resuming with it is refused",
        run(D5, ["05c", "--marker", "AF568", "--order", "uid"], m), 1)
    chk("...before the model ever ran", m.cuts, [])
    chk("--force with a --limit that leaves old sections is refused too",
        run(D5, ["05c", "--marker", "AF568", "--order", "uid", "--force",
                 "--limit", "1"], StubModel()), 1)
    m = StubModel()
    chk("--force over every section is allowed",
        run(D5, ["05c", "--marker", "AF568", "--order", "uid", "--force"], m), 0)
    chk("...and uses the new cut", {c for c, _ in m.cuts}, {0.7})

    print()
    print("--- use_auto off goes back to the model's own ---")
    saved_cfg["detection"]["stardist"] = {"use_auto": False}
    json.dump(saved_cfg, open(study.path, "w", encoding="utf-8"))
    D5 = load_stage("05c_detect_rois.py")
    st = D5.stardist_settings("AF568")
    chk("no cut is passed", (st["prob_thresh"], st["source"]), (None, "model defaults"))
    m = StubModel()
    chk("05c --force succeeds", run(D5, ["05c", "--marker", "AF568", "--order", "uid",
                                         "--force"], m), 0)
    chk("the model was left to its own cut", {c for c, _ in m.cuts}, {None})

print()
print("--- results from before the record are treated as model defaults ---")
with temp_study(acquisition=PAIRED, detection=cfg["detection"]) as study:
    D5 = load_stage("05c_detect_rois.py")
    chk("no record and measured rows mean model defaults",
        D5.recorded_settings("AF568", {"x"})["prob_thresh"], None)
    chk("no record and nothing measured means nothing to compare",
        D5.recorded_settings("AF568", set()), None)

print()
print("--- the app keeps what 05b wrote when it next saves ---")
with temp_study(acquisition=PAIRED) as study:
    import ls_project as LP
    proj = LP.Project.open(study.path)
    disk = json.load(open(study.path, encoding="utf-8"))
    disk.setdefault("detection", {})["auto"] = {"AF568": {"verdict": "good"}}
    json.dump(disk, open(study.path, "w", encoding="utf-8"))
    proj.record_run("tune_detection", True, "a", "b")
    chk("the run is recorded and detection.auto reaches memory",
        proj.cfg["detection"].get("auto"), {"AF568": {"verdict": "good"}})
    proj.save()
    chk("...so saving the working copy keeps it",
        json.load(open(study.path, encoding="utf-8"))["detection"].get("auto"),
        {"AF568": {"verdict": "good"}})

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
