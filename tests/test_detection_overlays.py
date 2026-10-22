"""06i draws what 05c counted and 06a called positive, one crop per ROI.

The pictures are only worth having if they agree with the numbers, so this
drives the real stages end to end on a synthetic study: 05c segments a fake
CZI with a fake model (and saves the outlines), 06a computes the cut and the
per-ROI counts, then 06i draws. What is checked:

  * a crop is the box at native pixels with a strip stacked above it: the
    image area is exactly grey(marker) wherever no nucleus or shape outline
    is - no resampling, no blanking - and the strip is exactly the strip of
    the text 06i says it wrote;
  * colours: a positive's interior is the 40% magenta blend, a negative's
    edge cyan, a nucleus outside the disc yellow, an artifact orange, an
    off-tissue nucleus blue, the ROI shape white;
  * index.csv's counts and cut equal roi_measurements.csv's for that ROI;
  * a section whose npz is gone is re-segmented, to identical pixels, and a
    re-segmentation under changed StarDist settings is refused;
  * a section whose outlines disagree with roi_nuclei.csv is not drawn and is
    listed in failures.csv, and stdout says "1 not drawn:";
  * a rerun with nothing changed draws nothing;
  * an empty ROI says "0 counted", a section with no cut says so, and a stale
    or missing roi_measurements.csv is refused (exit 2) before any folder is
    made.

SYNTHETIC: a fake CZI reader and a fake model, as test_detect_outlines.py.

Run:  python tests/test_detection_overlays.py
"""

import contextlib
import csv
import io
import json
import os
import shutil
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
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + " " + repr(got)[:120])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


try:
    import pylibCZIrw.czi as pyczi                           # noqa: E402
    import csbdeep.utils                                     # noqa: E402,F401
    from PIL import Image                                    # noqa: E402
    from skimage.measure import label as sk_label           # noqa: E402
except ImportError as exc:                                   # noqa: BLE001
    print(f"SKIP {exc} - the detection loop cannot be driven here")
    sys.exit(0)


class FakeCzi:
    """A plane per (scene, channel, box x0), and a record of every read."""

    def __init__(self, planes):
        self.planes = planes
        self.calls = []

    def read(self, **kw):
        self.calls.append(kw)
        return self.planes[(int(kw["scene"]), int(kw["plane"]["C"]),
                            int(kw["roi"][0]))].copy()


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
    """Every bright blob of the normalised crop is one nucleus."""

    def __init__(self):
        self.calls = 0

    def predict_instances(self, img, **kw):
        self.calls += 1
        return sk_label(np.asarray(img) > 0.5).astype(np.int32), {}


W, H, R = 60, 50, 20
Y0 = 20
MARKER = "AF568"
PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
A, B = "AB12_1a-s0", "AB12_1a-s1"
SCENE = {A: 6, B: 7}

# Nuclei in scene A's roi01 (box px, 6x6 squares), and their marker value.
P, C, N, ART, OFF, OUT = (12, 14), (12, 24), (22, 14), (22, 24), (32, 20), (2, 50)
ROI1 = {P: 2000.0, C: 65535.0, N: 150.0, ART: 150.0, OFF: 150.0, OUT: 150.0}
BG = [(12, 20), (12, 30), (22, 20), (22, 30), (22, 40), (32, 25)]


def dapi(squares, seed):
    a = np.random.default_rng(seed).normal(100, 2, (H, W))
    for r, c in squares:
        a[r:r + 6, c:c + 6] = 1000.0
    return a


def mark(values, seed):
    a = np.random.default_rng(seed + 1000).normal(200, 4, (H, W))
    for (r, c), v in values.items():
        if v is not None:
            a[r:r + 6, c:c + 6] = v
    return a


def box(uid, x, kind="roi", region="Dm"):
    gx, gy = x + 25, Y0 + 25           # disc centre in grid (= CZI) px
    return {"scene_uid": uid, "animal": "AB12", "marker": MARKER,
            "roi_kind": kind,
            "region": region if kind == "roi" else "__background__",
            "seed_n": "1", "czi_x0": str(x), "czi_y0": str(Y0),
            "czi_w": str(W), "czi_h": str(H),
            "sec_x": str(gx + 0.5), "sec_y": str(gy + 0.5),
            "sec_r": str(R), "sec_poly": "", "area_um2": "1000.0"}


SECTIONS = {A: [box(A, 10), box(A, 80, kind="background"), box(A, 150, region="Vd")],
            B: [box(B, 10), box(B, 80, kind="background")]}
PLANES = {
    (6, 0, 10): dapi(ROI1, 1), (6, 1, 10): mark(ROI1, 1),
    (6, 0, 80): dapi(BG, 2), (6, 1, 80): mark({s: None for s in BG}, 2),
    (6, 0, 150): np.full((H, W), 300.0), (6, 1, 150): mark({}, 3),
    (7, 0, 10): dapi([P], 4), (7, 1, 10): mark({P: 2000.0}, 4),
    (7, 0, 80): dapi(BG[:2], 5), (7, 1, 80): mark({}, 5),
}


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def drive_05c(D5):
    D5.G5.use_marker(MARKER)
    geoms = [{"scene_uid": u, "animal": "AB12", "marker": MARKER,
              "czi_file": "AB12_1a.czi", "scene_index": str(SCENE[u]),
              "m00": "1", "m01": "0", "m02": "0",
              "m10": "0", "m11": "1", "m12": "0"} for u in SECTIONS]
    os.makedirs(os.path.dirname(D5.G5.GEOM_CSV), exist_ok=True)
    write_rows(D5.G5.GEOM_CSV, geoms)
    write_rows(D5.G5.BOX_CSV, [b for u in SECTIONS for b in SECTIONS[u]])
    mask_dir = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    for u in SECTIONS:
        np.save(os.path.join(mask_dir, f"{u}_mask.npy"),
                np.ones((256, 256), dtype=bool))
    saved = (pyczi.open_czi, D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(FakeCzi(PLANES))
    D5.load_model = lambda: FakeModel()
    sys.argv = ["05c_detect_rois.py", "--marker", MARKER, "--order", "uid"]
    try:
        rc = D5.main()
    finally:
        pyczi.open_czi, D5.load_model, sys.argv = saved
    if rc != 0:
        raise SystemExit(f"05c.main() returned {rc}")


def run_06a():
    with contextlib.redirect_stdout(io.StringIO()):
        rc = load_stage("06a_roi_dataset.py").main()
    if rc != 0:
        raise SystemExit(f"06a.main() returned {rc}")


MODEL = FakeModel()


def run_06i(OV, *extra):
    saved = (pyczi.open_czi, OV.D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(FakeCzi(PLANES))
    OV.D5.load_model = lambda: MODEL
    sys.argv = ["06i_detection_overlays.py", "--marker", MARKER] + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = OV.main()
    finally:
        pyczi.open_czi, OV.D5.load_model, sys.argv = saved
    out = buf.getvalue()
    print("     | " + out.strip().replace("\n", "\n     | "))
    return rc, out


def png(path):
    return np.asarray(Image.open(path).convert("RGB"))


def pngs_in(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if f.endswith(".png"))


def edge_px(origin):
    """A pixel on the top edge of a 6x6 square, away from its corners."""
    return origin[0], origin[1] + 2


with temp_study(acquisition=PAIRED) as study:
    D5 = load_stage("05c_detect_rois.py")
    drive_05c(D5)
    os.makedirs(os.path.join(study.out_root, "manifest"), exist_ok=True)
    write_rows(os.path.join(study.out_root, "manifest", "manifest_scenes.csv"),
               [{"scene_uid": A, "animal": "AB12", "slide": "1"},
                {"scene_uid": B, "animal": "AB12", "slide": "1"}])
    nuc_csv = D5.NUCLEI_CSV
    rows = read_rows(nuc_csv)

    # Flag the two nuclei that are to be drawn as artifact / off tissue.
    def at(uid, roi, origin):
        cx, cy = 10 + origin[1] + 2.5, Y0 + origin[0] + 2.5
        hit = [r for r in rows if r["scene_uid"] == uid and r["roi_index"] == str(roi)
               and abs(float(r["czi_x"]) - cx) < 0.6 and abs(float(r["czi_y"]) - cy) < 0.6]
        assert len(hit) == 1, (uid, roi, origin, len(hit))
        return hit[0]
    at(A, 1, ART)["artifact"] = "1"
    at(A, 1, OFF)["off_tissue"] = "1"
    write_rows(nuc_csv, rows)

    OV = load_stage("06i_detection_overlays.py")
    out_dir = os.path.join(study.out_root, "detection_overlays")
    meas_csv = os.path.join(study.out_root, "results", "roi_measurements.csv")

    print("--- refused without a current roi_measurements.csv ---")
    rc, out = run_06i(OV)
    chk("no roi_measurements.csv: exit 2", rc, 2)
    chk("...the message names 06a", "06a" in out, True)
    chk("...no output folder", os.path.exists(out_dir), False)
    run_06a()
    os.utime(meas_csv, (1_000_000_000, 1_000_000_000))
    rc, out = run_06i(OV)
    chk("roi_measurements.csv older than roi_nuclei.csv: exit 2", rc, 2)
    chk("...the message names 06a", "06a" in out, True)
    chk("...no output folder", os.path.exists(out_dir), False)
    run_06a()
    meas = read_rows(meas_csv)

    print()
    print("--- the polygon test is vectorised and agrees with 05c's ---")
    pb = dict(box(A, 10), sec_poly="20.5 30.5;50.5 28.5;55.5 60.5;30.5 66.5;25.5 50.5")
    rng = np.random.default_rng(0)
    gx, gy = rng.uniform(15, 60, 4000), rng.uniform(25, 70, 4000)
    scalar = D5.roi_inside(pb)
    want = np.array([scalar(x, y) for x, y in zip(gx, gy)])
    chk("grid_inside(poly) == roi_inside(poly) on 4000 points",
        bool(np.array_equal(OV.grid_inside(pb)(gx, gy), want)), True)
    db = box(A, 10)
    scalar = D5.roi_inside(db)
    want = np.array([scalar(x, y) for x, y in zip(gx, gy)])
    chk("grid_inside(disc) == roi_inside(disc)",
        bool(np.array_equal(OV.grid_inside(db)(gx, gy), want)), True)

    print()
    print("--- one run: a crop per ROI and background disc ---")
    MODEL.calls = 0
    rc, out = run_06i(OV)
    chk("exit 0", rc, 0)
    chk("saved outlines used: the model was not run", MODEL.calls, 0)
    chk("nothing not drawn", "not drawn" in out, False)
    folder = os.path.join(out_dir, MARKER, "AB12")
    from ls_overlay import (overlay_dir, overlay_name, grey, stretch, strip,
                            shape_mask_outline, COLOURS, FILL_ALPHA, SHAPE_COLOUR)
    chk("overlay_dir is detection_overlays/<marker>/<animal>",
        overlay_dir(study.out_root, MARKER, "AB12"), folder)
    roi1 = os.path.join(folder, overlay_name(MARKER, A, "roi", 1, "Dm"))
    bg2 = os.path.join(folder, overlay_name(MARKER, A, "background", 2))
    roi3 = os.path.join(folder, overlay_name(MARKER, A, "roi", 3, "Vd"))
    b_roi1 = os.path.join(folder, overlay_name(MARKER, B, "roi", 1, "Dm"))
    for p in (roi1, bg2, roi3, b_roi1):
        chk(f"{os.path.basename(p)} exists", os.path.exists(p), True)
    chk("no scene pictures yet", [f for f in os.listdir(folder) if "_scene" in f], [])

    img = png(roi1)
    index = {r["path"]: r for r in read_rows(OV.INDEX_CSV)}
    rel = os.path.relpath(roi1, out_dir).replace(os.sep, "/")
    key = rel if rel in index else roi1
    irow = index.get(key, {})
    chk("index.csv has a row for the roi01 crop", bool(irow), True)
    chk("INDEX_KEYS", list(read_rows(OV.INDEX_CSV)[0]), OV.INDEX_KEYS)
    lines = OV.crop_lines(irow)
    st = strip(W, lines, True, float(irow["um_per_px"]), 50)
    chk("height == strip + czi_h", img.shape[0], st.shape[0] + H)
    chk("width == czi_w", img.shape[1], W)
    chk("the strip is exactly the strip of crop_lines(row)",
        bool(np.array_equal(img[:st.shape[0]], st)), True)
    chk("line 1 names marker, fish, slide, scene, ROI",
        lines[0], f"{MARKER} | fish AB12 | slide 1 | scene {A} | ROI 01 roi Dm")
    chk("line 2: counts, cut, exclusions",
        lines[1].startswith("3 counted, 2 positive (66.7%), cut ")
        and lines[1].endswith("| not counted: 1 outside ROI, 1 artifact, "
                              "1 off tissue | 1 censored"), True)

    area = img[st.shape[0]:].astype(int)
    lo, hi = stretch(np.concatenate([PLANES[(6, 1, x)].ravel() for x in (10, 80, 150)]))
    g = grey(PLANES[(6, 1, 10)], lo, hi).astype(int)
    labels = np.load(D5.outline_path(MARKER, A))["roi01"]
    ring = shape_mask_outline(H, W, lambda xs, ys: (10 + xs - 35) ** 2
                              + (Y0 + ys - 45) ** 2 <= R * R)
    plain = (labels == 0) & ~ring
    chk("away from nuclei and the shape, pixels are grey(mark, lo, hi)",
        bool(np.array_equal(area[plain], np.repeat(g[plain][:, None], 3, 1))), True)
    chk("the ROI shape is outlined in SHAPE_COLOUR",
        bool((area[ring & (labels == 0)] == SHAPE_COLOUR).all()), True)
    r_, c_ = P[0] + 2, P[1] + 2
    blend = np.rint((1 - FILL_ALPHA) * g[r_, c_]
                    + FILL_ALPHA * np.array([255, 0, 255])).astype(int)
    chk("a positive's interior is the 40% magenta blend", area[r_, c_].tolist(), blend.tolist())
    chk("a negative's edge is cyan", area[edge_px(N)].tolist(), list(COLOURS["negative"]))
    chk("a censored nucleus's edge is white", area[edge_px(C)].tolist(), list(COLOURS["censored"]))
    chk("a nucleus outside the disc has a yellow edge",
        area[edge_px(OUT)].tolist(), list(COLOURS["outside"]))
    chk("an artifact row has an orange edge", area[edge_px(ART)].tolist(),
        list(COLOURS["artifact"]))
    chk("an off-tissue row has a blue edge", area[edge_px(OFF)].tolist(),
        list(COLOURS["off_tissue"]))

    m1 = [r for r in meas if r["scene_uid"] == A and r["roi_index"] == "1"][0]
    chk("index n_counted == roi_measurements n_nuclei", irow.get("n_counted"), m1["n_nuclei"])
    chk("index n_positive == roi_measurements n_positive", irow.get("n_positive"), m1["n_positive"])
    chk("index cut == section_positivity_cut", irow.get("cut"), m1["section_positivity_cut"])
    chk("a cut exists for section A", m1["section_positivity_cut"] != "", True)
    chk("index identity", [irow.get(k) for k in ("marker", "animal", "slide", "scene_uid",
                                                 "picture", "roi_index", "region")],
        [MARKER, "AB12", "1", A, "roi", "1", "Dm"])
    chk("index exclusions", [irow.get(k) for k in ("n_outside", "n_artifact",
                                                   "n_off_tissue", "n_censored")],
        ["1", "1", "1", "1"])
    chk("index outlines == saved", irow.get("outlines"), "saved")
    chk("index um_per_px", float(irow.get("um_per_px", 0)), 0.65)

    print()
    print("--- review focus: an empty ROI, a section with no cut ---")
    rows3 = [r for r in index.values() if r["scene_uid"] == A and r["roi_index"] == "3"]
    chk("the empty ROI has a crop", len(rows3), 1)
    l3 = OV.crop_lines(rows3[0])
    chk("...whose strip says '0 counted'", l3[1].startswith("0 counted"), True)
    img3 = png(roi3)
    st3 = strip(W, l3, True, 0.65, 50)
    chk("...and draws that strip", bool(np.array_equal(img3[:st3.shape[0]], st3)), True)
    rowsb = [r for r in index.values() if r["scene_uid"] == B and r["roi_index"] == "1"]
    lb = OV.crop_lines(rowsb[0])
    chk("section B (2 background nuclei) has no cut in 06a",
        [r["section_positivity_cut"] for r in meas if r["scene_uid"] == B], ["", ""])
    chk("...its strip says 'no cut: too few background nuclei'",
        "no cut: too few background nuclei" in lb[1], True)
    chk("...and its index cut is blank", rowsb[0]["cut"], "")

    print()
    print("--- a second run with nothing changed draws nothing ---")
    past = 1_000_000_000
    pngs = sorted(os.path.join(folder, f) for f in os.listdir(folder))
    for p in pngs:
        os.utime(p, (past, past))
    before_index = open(OV.INDEX_CSV, encoding="utf-8").read()
    rc, out = run_06i(OV)
    chk("exit 0", rc, 0)
    chk("no picture rewritten", [os.path.getmtime(p) for p in pngs], [past] * len(pngs))
    chk("index unchanged", open(OV.INDEX_CSV, encoding="utf-8").read(), before_index)

    print()
    print("--- no npz: re-segmented, identical pixels ---")
    a_imgs = {p: png(p) for p in pngs if f"_{A}_" in p}
    os.remove(D5.outline_path(MARKER, A))
    MODEL.calls = 0
    rc, out = run_06i(OV, "--force")
    chk("exit 0", rc, 0)
    chk("the model ran for section A's boxes", MODEL.calls, 3)
    chk("the npz is saved again", os.path.exists(D5.outline_path(MARKER, A)), True)
    index = read_rows(OV.INDEX_CSV)
    chk("section A's rows say re-segmented",
        sorted({r["outlines"] for r in index if r["scene_uid"] == A}), ["re-segmented"])
    chk("section B's rows say saved",
        sorted({r["outlines"] for r in index if r["scene_uid"] == B}), ["saved"])
    chk("identical pixels", all(np.array_equal(png(p), a) for p, a in a_imgs.items()), True)

    print()
    print("--- outlines that disagree with roi_nuclei.csv: not drawn ---")
    rows = read_rows(nuc_csv)
    orig = [dict(r) for r in rows]
    victim = [r for r in rows if r["scene_uid"] == A and r["roi_index"] == "1"][0]
    victim["nucleus_id"] = "99"
    write_rows(nuc_csv, rows)
    run_06a()
    rc, out = run_06i(OV)
    chk("exit 0", rc, 0)
    chk("stdout says '1 not drawn:'", "1 not drawn:" in out, True)
    chk("...and names failures.csv", OV.FAILURES_CSV in out, True)
    chk("section A's pictures are gone", [p for p in os.listdir(folder) if f"_{A}_" in p], [])
    chk("...and its index rows",
        [r for r in read_rows(OV.INDEX_CSV) if r["scene_uid"] == A], [])
    chk("section B's are still there", os.path.exists(b_roi1), True)
    fails = read_rows(OV.FAILURES_CSV)
    chk("one failures.csv row", len(fails), 1)
    chk("...for section A", fails[0]["scene_uid"] if fails else None, A)
    chk("...naming the roi", "roi01" in (fails[0]["reason"] if fails else ""), True)
    print("     reason: " + (fails[0]["reason"] if fails else ""))

    write_rows(nuc_csv, orig)
    run_06a()
    rc, out = run_06i(OV)
    chk("restored: drawn again, nothing failed", "not drawn" in out, False)
    chk("...failures.csv is header only", read_rows(OV.FAILURES_CSV), [])
    chk("...section A's crop is back", os.path.exists(roi1), True)

    print()
    print("--- re-segmenting under changed StarDist settings is refused ---")
    with open(D5.SETTINGS_JSON, encoding="utf-8") as fh:
        rec = json.load(fh)
    saved_rec = json.dumps(rec)
    rec["markers"][MARKER] = {"prob_thresh": 0.9, "nms_thresh": None,
                              "source": "a different run"}
    with open(D5.SETTINGS_JSON, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.remove(D5.outline_path(MARKER, A))
    MODEL.calls = 0
    rc, out = run_06i(OV, "--force")
    fails = read_rows(OV.FAILURES_CSV)
    chk("1 not drawn", "1 not drawn:" in out, True)
    chk("reason: detection settings changed since measured",
        [f["reason"] for f in fails], ["detection settings changed since measured"])
    chk("the model was not run", MODEL.calls, 0)
    chk("no npz written from other settings",
        os.path.exists(D5.outline_path(MARKER, A)), False)
    with open(D5.SETTINGS_JSON, "w", encoding="utf-8") as fh:
        fh.write(saved_rec)

    print()
    print("--- --no-crops draws no crops; --animal limits ---")
    shutil.rmtree(out_dir)
    rc, out = run_06i(OV, "--no-crops")
    chk("--no-crops: exit 0", rc, 0)
    chk("--no-crops: no crop written",
        pngs_in(folder), [])
    rc, out = run_06i(OV, "--animal", "XX99")
    chk("--animal XX99: nothing drawn",
        pngs_in(folder), [])
    rc, out = run_06i(OV, "--animal", "AB12", "--no-scenes")
    chk("--animal AB12 --no-scenes: crops drawn", os.path.exists(b_roi1), True)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
