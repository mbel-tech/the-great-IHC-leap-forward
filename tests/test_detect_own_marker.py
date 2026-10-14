"""A paired marker segmented on its OWN channel: the LS PCNA (AF488) route.

The AF488 scans carry a DAPI plane that is noise at native resolution, so
05c cannot segment a counterstain there. `acquisition.marker_segmentation`
says "segment this marker on its own channel", and 05c then has to do three
things that the nuclear route never needed:

  * give every crop of a section ONE intensity range, because stretching each
    crop to its own percentiles makes a background disc's noise look like
    signal and a bright zone's dim nuclei vanish;
  * record that range, one row per (section, marker), in
    results/roi_normalisation.csv;
  * leave the other marker (pERK, nuclear) exactly as it was.

SYNTHETIC, AND SAY SO. A fake reader and a fake model stand in for the CZI and
StarDist, so this proves the plumbing - which plane reaches the model, on what
scale, and what is written - not that StarDist finds PCNA+ nuclei. That is the
operator's pilot (docs/nuclei-detection-after-roi-curation.md section 4.8).

Run:  python tests/test_detect_own_marker.py
"""

import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from _fixture import temp_study, load_stage                  # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(60) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


try:
    import pylibCZIrw.czi as pyczi                           # noqa: E402
    import csbdeep.utils                                     # noqa: E402,F401
except ImportError as exc:                                   # noqa: BLE001
    print(f"SKIP {exc} - the detection loop cannot be driven here")
    sys.exit(0)

PAIRED_OWN = {
    "layout": "paired", "markers": ["AF568", "AF488"], "channels": [],
    "marker_segmentation": {
        "AF488": {"segment": "own", "backend": "stardist",
                  "nucleus_shaped": True}}}


class FakeCzi:
    """A plane per (channel, box x0), and a record of every read."""

    def __init__(self, planes):
        self.planes = planes
        self.calls = []

    def read(self, **kw):
        self.calls.append(kw)
        return self.planes[(int(kw["plane"]["C"]), int(kw["roi"][0]))]


class FakeOpenCzi:
    def __init__(self, doc):
        self.doc = doc

    def __call__(self, path, *a, **kw):
        return self

    def __enter__(self):
        return self.doc

    def __exit__(self, *exc):
        return False


class FakeModel:
    """Returns one known object per crop and records what it was shown."""

    def __init__(self):
        self.calls = []

    def predict_instances(self, img, **kw):
        a = np.asarray(img)
        self.calls.append(a)
        lab = np.zeros(a.shape, dtype=np.int32)
        lab[2:8, 2:8] = 1
        return lab, {}


W, H = 24, 20
SEC = (112, 110, 50)            # inside this disc under an identity affine


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def drive(D5, marker, uid, planes, xs, force=False):
    """Run 05c.main() over one section with one ROI at each x in `xs`."""
    D5.G5.use_marker(marker)
    os.makedirs(D5.REFORMAT_DIR, exist_ok=True)
    geom = {"scene_uid": uid, "animal": "AB12", "marker": marker,
            "czi_file": "AB12_1a.czi", "scene_index": "6",
            "m00": "1", "m01": "0", "m02": "0",
            "m10": "0", "m11": "1", "m12": "0"}
    with open(D5.G5.GEOM_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(geom))
        w.writeheader()
        w.writerow(geom)
    sx, sy, sr = SEC
    boxes = [{"scene_uid": uid, "animal": "AB12", "marker": marker,
              "roi_kind": "roi" if i == 0 else "background",
              "region": "Dm" if i == 0 else "__background__",
              "seed_n": "1", "czi_x0": str(x), "czi_y0": "100",
              "czi_w": str(W), "czi_h": str(H),
              "sec_x": str(sx), "sec_y": str(sy), "sec_r": str(sr),
              "sec_poly": ""} for i, x in enumerate(xs)]
    with open(D5.G5.BOX_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(boxes[0]))
        w.writeheader()
        w.writerows(boxes)
    mask_dir = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    np.save(os.path.join(mask_dir, f"{uid}_mask.npy"),
            np.ones((256, 256), dtype=bool))

    fake, model = FakeCzi(planes), FakeModel()
    saved = (pyczi.open_czi, D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(fake)
    D5.load_model = lambda: model
    sys.argv = (["05c_detect_rois.py", "--marker", marker, "--order", "uid"]
                + (["--force"] if force else []))
    try:
        rc = D5.main()
    finally:
        pyczi.open_czi, D5.load_model, sys.argv = saved
    if rc != 0:
        raise SystemExit(f"05c.main() returned {rc} for {marker}")
    return fake, model, read_rows(D5.NUCLEI_CSV)


def plane(bg, patch=None, frac=0.0):
    """A plane of noisy background, optionally with a fraction set to `patch`."""
    rng = np.random.default_rng(int(bg))
    a = rng.normal(bg, bg * 0.02, (H, W))
    if patch is not None:
        a[: int(round(frac * H)), :] = patch
    return a


with temp_study(acquisition=PAIRED_OWN) as study:
    D5 = load_stage("05c_detect_rois.py")

    print("--- the declaration reaches the dispatch ---")
    chk("AF488 is segmented on its own plane",
        D5.segment_plane_for("AF488"), "AF488")
    chk("...with stardist", D5.backend_for("AF488"), "stardist")
    chk("...and its objects are nuclei", D5.nucleus_shaped_for("AF488"), True)
    chk("AF568 is untouched: the counterstain", D5.segment_plane_for("AF568"),
        "nuclear")
    chk("...still stardist", D5.backend_for("AF568"), "stardist")
    chk("AF488 needs a shared range", D5.needs_shared_norm("AF488"), True)
    chk("AF568 does not", D5.needs_shared_norm("AF568"), False)

    print()
    print("--- two crops of one section share ONE range ---")
    # ROI: dim background with a bright quarter. Background disc: dim and
    # nearly flat. Stretched per crop the disc would fill 0..1; under a shared
    # range it stays near the bottom.
    roi = plane(100, patch=1000, frac=0.3)
    bgd = plane(100, patch=140, frac=0.2)
    dapi = plane(5)                     # noise: must never reach the model
    planes = {(0, 100): dapi, (1, 100): roi, (0, 120): dapi, (1, 120): bgd}
    fake, model, rows = drive(D5, "AF488", "AB12_1a-s0", planes, [100, 120])

    chk("the model ran once per crop", len(model.calls), 2)
    lo, hi = (float(v) for v in np.percentile(
        np.concatenate([roi.ravel(), bgd.ravel()]), [1, 99.8]))
    want_roi = ((roi.astype(np.float32) - np.float32(lo))
                / (np.float32(hi) - np.float32(lo) + np.float32(1e-20)))
    chk("the ROI crop was scaled by the section's range",
        bool(np.allclose(model.calls[0], want_roi, atol=1e-5)), True)
    chk("the background crop was NOT stretched to fill 0..1",
        float(model.calls[1].max()) < 0.2, True)
    chk("...yet is still above zero (it has its patch)",
        float(model.calls[1].max()) > 0.0, True)
    chk("the counterstain plane was never what StarDist saw",
        any(np.allclose(c, 0) for c in model.calls), False)
    chk("one object per crop", len(rows), 2)
    chk("segmented_on records the marker itself",
        {r["segmented_on"] for r in rows}, {"AF488"})
    chk("backend records stardist", {r["backend"] for r in rows}, {"stardist"})
    chk("nucleus_shaped is 1", {r["nucleus_shaped"] for r in rows}, {"1"})
    chk("marker_mean is the marker plane's, not the counterstain's",
        abs(float(rows[0]["marker_mean"]) - float(roi[2:8, 2:8].mean())) < 1.0,
        True)

    print()
    print("--- the range is recorded, once per section and marker ---")
    norm = read_rows(D5.NORM_CSV)
    chk("one row", len(norm), 1)
    chk("it names the section and marker",
        (norm[0]["scene_uid"], norm[0]["marker"]), ("AB12_1a-s0", "AF488"))
    chk("...the plane it came from", norm[0]["segmented_on"], "AF488")
    chk("...how many crops it spans", norm[0]["n_crops"], "2")
    chk("...the low end", abs(float(norm[0]["norm_lo"]) - lo) < 0.01, True)
    chk("...and the high end", abs(float(norm[0]["norm_hi"]) - hi) < 0.01, True)

    print()
    print("--- resume does not measure or record it again ---")
    fake2, model2, rows2 = drive(D5, "AF488", "AB12_1a-s0", planes, [100, 120])
    chk("nothing was segmented", len(model2.calls), 0)
    chk("no rows were added", len(rows2), 2)
    chk("no second normalisation row", len(read_rows(D5.NORM_CSV)), 1)

    print()
    print("--- --force replaces the section's row, not the file ---")
    drive(D5, "AF488", "AB12_1a-s0", planes, [100, 120], force=True)
    chk("still one normalisation row", len(read_rows(D5.NORM_CSV)), 1)
    chk("...and still two nuclei rows", len(read_rows(D5.NUCLEI_CSV)), 2)

# --------------------------------------------------------------------------
print()
print("--- the nuclear marker in the same study does not move ---")
with temp_study(acquisition=PAIRED_OWN) as study:
    D5 = load_stage("05c_detect_rois.py")
    dapi = plane(100, patch=900, frac=0.4)
    mark = plane(250)
    planes = {(0, 100): dapi, (1, 100): mark}
    fake, model, rows = drive(D5, "AF568", "AB12_2a-s0", planes, [100])
    chk("segmented on the counterstain", rows[0]["segmented_on"], "nuclear")
    from csbdeep.utils import normalize
    chk("...scaled per crop exactly as before",
        bool(np.allclose(model.calls[0], normalize(dapi, 1, 99.8))), True)
    chk("dapi_mean is the counterstain's",
        abs(float(rows[0]["dapi_mean"]) - float(dapi[2:8, 2:8].mean())) < 1.0,
        True)
    chk("no normalisation record for a per-crop marker",
        os.path.exists(D5.NORM_CSV), False)

# --------------------------------------------------------------------------
print()
print("--- a flat section has no range, and is skipped out loud ---")
with temp_study(acquisition=PAIRED_OWN) as study:
    D5 = load_stage("05c_detect_rois.py")
    flat = np.full((H, W), 300.0)
    planes = {(0, 100): flat, (1, 100): flat}
    fake, model, rows = drive(D5, "AF488", "AB12_3a-s0", planes, [100])
    chk("the model was never run", len(model.calls), 0)
    chk("no rows were written", len(rows), 0)
    chk("no range was recorded", read_rows(D5.NORM_CSV), [])

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
