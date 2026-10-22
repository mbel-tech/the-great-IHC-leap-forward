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
    """A plane per (scene, channel, box x0), and a record of every read.

    A read that is not one box's (the whole-scene picture's bands) is cut
    from that scene's canvas, which holds every box's plane at its place."""

    def __init__(self, planes):
        self.planes = planes
        self.calls = []

    def read(self, **kw):
        self.calls.append(kw)
        s, c = int(kw["scene"]), int(kw["plane"]["C"])
        x, y, w, h = (int(v) for v in kw["roi"])
        got = self.planes.get((s, c, x))
        if got is not None and got.shape == (h, w):
            return got.copy()
        sx, sy, _, _ = SCENE_RECT
        return canvas(s, c)[y - sy:y - sy + h, x - sx:x - sx + w].copy()


class BrokenCzi(FakeCzi):
    """Reads of one scene fail as an unplugged drive does."""

    def __init__(self, planes, scene):
        super().__init__(planes)
        self.scene = scene

    def read(self, **kw):
        if int(kw["scene"]) == self.scene:
            raise OSError(5, "Input/output error")
        return super().read(**kw)


class Unopenable:
    def __call__(self, path, *a, **kw):
        raise FileNotFoundError(2, "No such file or directory", path)


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
            B: [box(B, 10), box(B, 80, kind="background"),
                dict(box(B, 190), czi_w="4")]}       # too small to segment
PLANES = {
    (6, 0, 10): dapi(ROI1, 1), (6, 1, 10): mark(ROI1, 1),
    (6, 0, 80): dapi(BG, 2), (6, 1, 80): mark({s: None for s in BG}, 2),
    (6, 0, 150): np.full((H, W), 300.0), (6, 1, 150): mark({}, 3),
    (7, 0, 10): dapi([P], 4), (7, 1, 10): mark({P: 2000.0}, 4),
    (7, 0, 80): dapi(BG[:2], 5), (7, 1, 80): mark({}, 5),
    (7, 1, 190): mark({}, 6)[:, :4],
}


#: The scenes' rectangle in CZI px (x, y, w, h): every box inside it, the
#: first box near enough its top-left corner that its tag is clipped.
SCENE_RECT = (5, 10, 220, 80)
#: Rows / columns (scene px) of scene 6 that were never scanned: they read 0.
UNSCANNED = (slice(72, 80), slice(0, 40))


def canvas(scene, channel):
    """The whole scene rectangle of one channel: noise, every box's plane at
    its place, and an unscanned corner of 0s in scene 6."""
    sx, sy, sw, sh = SCENE_RECT
    a = np.random.default_rng(100 + scene).normal(170, 3, (sh, sw))
    if scene == 6:
        a[UNSCANNED] = 0
    for (s, c, x), plane in PLANES.items():
        if (s, c) == (scene, channel):
            h, w = plane.shape
            a[Y0 - sy:Y0 - sy + h, x - sx:x - sx + w] = plane
    return a


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
              "m10": "0", "m11": "1", "m12": "0",
              "scene_x": str(SCENE_RECT[0]), "scene_y": str(SCENE_RECT[1]),
              "scene_w": str(SCENE_RECT[2]), "scene_h": str(SCENE_RECT[3])}
             for u in SECTIONS]
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


def run_06i(OV, *extra, opener=None):
    saved = (pyczi.open_czi, OV.D5.load_model, sys.argv)
    pyczi.open_czi = opener or FakeOpenCzi(FakeCzi(PLANES))
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
    cuts_csv = os.path.join(study.out_root, "results", "positivity_cuts.csv")
    os.remove(cuts_csv)
    rc, out = run_06i(OV)
    chk("no positivity_cuts.csv: exit 2", rc, 2)
    chk("...the message names 06a", "06a" in out, True)
    chk("...no output folder", os.path.exists(out_dir), False)
    run_06a()
    os.utime(cuts_csv, (1_000_000_000, 1_000_000_000))
    rc, out = run_06i(OV)
    chk("positivity_cuts.csv older than roi_nuclei.csv: exit 2", rc, 2)
    chk("...no output folder", os.path.exists(out_dir), False)
    run_06a()
    meas = read_rows(meas_csv)

    print()
    print("--- 06a publishes the exact cut ---")
    cuts = read_rows(cuts_csv)
    chk("positivity_cuts.csv: one row, for the section with a cut",
        [(r["scene_uid"], r["marker"]) for r in cuts], [(A, MARKER)])
    chk("...header", list(cuts[0]) if cuts else None, ["scene_uid", "marker", "cut"])
    exact = float(cuts[0]["cut"]) if cuts else None
    chk("...written with repr: it round-trips", repr(exact) == cuts[0]["cut"], True)
    chk("...and rounds to roi_measurements' cut",
        f"{round(exact, 1)}", [r["section_positivity_cut"] for r in meas
                               if r["scene_uid"] == A][0])

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
    chk("nothing not drawn", "not drawn:" in out, False)
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
    sc_a = os.path.join(folder, overlay_name(MARKER, A, "scene"))
    sc_b = os.path.join(folder, overlay_name(MARKER, B, "scene"))
    chk("a scene picture per section",
        sorted(f for f in os.listdir(folder) if "_scene" in f),
        sorted(os.path.basename(p) for p in (sc_a, sc_b)))

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
    chk("a box under 8 px is summed up, not drawn",
        "1 boxes under 8 px not drawn" in out, True)
    chk("...no crop for it", [f for f in os.listdir(folder) if f"_{B}_roi03" in f], [])

    print()
    print("--- the whole-scene picture ---")
    sx, sy, sw, sh = SCENE_RECT
    srow = index.get(os.path.relpath(sc_a, out_dir).replace(os.sep, "/"), {})
    chk("index.csv has a row for section A's scene", bool(srow), True)
    chk("...picture scene, no roi_index, no region",
        [srow.get(k) for k in ("picture", "roi_index", "region")], ["scene", "", ""])
    chk("...counts: totals over the ROIs (not the background disc)",
        [srow.get(k) for k in ("n_counted", "n_positive", "pct_positive", "n_outside",
                               "n_artifact", "n_off_tissue", "n_censored")],
        ["3", "2", "66.7", "1", "1", "1", "1"])
    chk("...cut == section_positivity_cut", srow.get("cut"), m1["section_positivity_cut"])
    chk("...same fingerprint as the crops", srow.get("fingerprint"), irow.get("fingerprint"))
    cut_a = m1["section_positivity_cut"]
    s_lines = [f"{MARKER} | fish AB12 | slide 1 | scene {A} | whole scene",
               f"2 ROIs: 3 counted, 2 positive (66.7%), cut {cut_a} | not counted: "
               f"1 outside ROI, 1 artifact, 1 off tissue | 1 censored",
               "ROI 01 Dm: 3 counted, 2 positive (66.7%)",
               "ROI 03 Vd: 0 counted, 0 positive (n/a)"]
    s_img = png(sc_a)
    s_st = strip(sw, s_lines, True, 0.65, 500)
    chk("scene height == strip + scene rect height", s_img.shape[0], s_st.shape[0] + sh)
    chk("scene width == scene rect width", s_img.shape[1], sw)
    chk("the strip: identity, totals, a line per ROI, legend, 500 um bar",
        bool(np.array_equal(s_img[:s_st.shape[0]], s_st)), True)
    s_area = s_img[s_st.shape[0]:]
    for p, x in ((roi1, 10), (bg2, 80), (roi3, 150)):
        chk(f"the scene's {os.path.basename(p)[len(MARKER) + len(A) + 2:]} region "
            f"== the crop's image",
            bool(np.array_equal(s_area[Y0 - sy:Y0 - sy + H, x - sx:x - sx + W],
                                png(p)[-H:])), True)
    chk("section B's scene holds its roi01 crop too",
        bool(np.array_equal(png(sc_b)[-sh:][Y0 - sy:Y0 - sy + H, 10 - sx:10 - sx + W],
                            png(b_roi1)[-H:])), True)
    covered = np.zeros((sh, sw), bool)
    for x in (10, 80, 150):
        covered[Y0 - sy:Y0 - sy + H, x - sx:x - sx + W] = True
    chk("tag text height on a 220 px scene: TAG_PX", OV.tag_px(sw), 16)
    for bi, x in ((1, 10), (2, 80), (3, 150)):
        t = OV.tag(bi, OV.tag_px(sw))
        th, tw = t.shape[:2]
        top, left = Y0 - sy - th, x - sx - tw
        r0, c0 = max(top, 0), max(left, 0)
        chk(f"tag {bi:02d} sits just above-left of its box, clipped",
            bool(np.array_equal(s_area[r0:Y0 - sy, c0:x - sx],
                                t[r0 - top:, c0 - left:])), True)
        covered[r0:Y0 - sy, c0:x - sx] = True
    chk("tag 01 really is clipped (the test means something)",
        Y0 - sy - OV.tag(1).shape[0] < 0 and 10 - sx - OV.tag(1).shape[1] < 0, True)
    chk("a tag is white text on black",
        sorted({tuple(v) for v in OV.tag(2).reshape(-1, 3)} & {(0, 0, 0), (255, 255, 255)}),
        [(0, 0, 0), (255, 255, 255)])
    g_all = grey(canvas(6, 1), lo, hi)
    chk("everywhere else: grey(marker), nothing blanked, no marks",
        bool(np.array_equal(s_area[~covered], np.repeat(g_all[~covered][:, None], 3, 1))),
        True)
    chk("an unscanned corner reads 0: black", s_area[UNSCANNED].max(), 0)
    brow = index.get(os.path.relpath(sc_b, out_dir).replace(os.sep, "/"), {})
    chk("section B's scene row: no cut, blank n_positive and pct",
        [brow.get(k) for k in ("cut", "n_positive", "pct_positive")], ["", "", ""])

    print()
    print("--- the scene is written in bands; the band size changes nothing ---")
    band_saved = OV.BAND_ROWS
    chk("BAND_ROWS is 1024", band_saved, 1024)
    doc7 = FakeCzi(PLANES)
    OV.BAND_ROWS = 7
    rc, out = run_06i(OV, "--force", "--no-crops", opener=FakeOpenCzi(doc7))
    chk("7-row bands: exit 0", rc, 0)
    banded = {p: png(p) for p in (sc_a, sc_b)}
    reads = [c["roi"] for c in doc7.calls if int(c["roi"][0]) == sx]
    chk("...read 7 rows at a time, the whole width",
        sorted({(w, h) for _, _, w, h in reads}), [(sw, sh % 7), (sw, 7)])
    chk("...12 bands per scene", len(reads), 2 * -(-sh // 7))
    OV.BAND_ROWS = 10_000
    rc, out = run_06i(OV, "--force", "--no-crops")
    OV.BAND_ROWS = band_saved
    chk("one 10,000-row band: the same pixels",
        all(np.array_equal(png(p), a) for p, a in banded.items()), True)

    print()
    print("--- draw_scene on its own: overlapping boxes, the scene edge, big tags ---")

    class ArrayCzi:
        """Reads cut from one array whose [0, 0] is CZI (0, 0)."""

        def __init__(self, arr):
            self.arr = arr

        def read(self, **kw):
            x, y, w, h = (int(v) for v in kw["roi"])
            return self.arr[y:y + h, x:x + w].copy()

    def geom_for(x, y, w, h):
        return {"scene_index": "6", "m00": "1", "m01": "0", "m02": "0",
                "m10": "0", "m11": "1", "m12": "0", "scene_x": str(x),
                "scene_y": str(y), "scene_w": str(w), "scene_h": str(h)}

    def crop_like(arr, b, labels, kinds, lo_, hi_):
        """The image area of b's crop, made as draw_crops/draw_crop make it."""
        x0, y0, w, h = (int(b[k]) for k in ("czi_x0", "czi_y0", "czi_w", "czi_h"))
        rgb = np.repeat(grey(arr[y0:y0 + h, x0:x0 + w], lo_, hi_)[:, :, None], 3, 2)
        OV.LO.paint(rgb, labels, kinds)
        inside = OV.grid_inside(b)
        rgb[shape_mask_outline(h, w, lambda xs, ys: inside(x0 + xs, y0 + ys))] = SHAPE_COLOUR
        return rgb

    def scene_of(arr, rect, boxes_, outl, kinds_by, lo_, hi_, name):
        path = os.path.join(study.out_root, "scratch", name)
        OV.draw_scene(ArrayCzi(arr), geom_for(*rect), MARKER, boxes_, outl, kinds_by,
                      lo_, hi_, path, [name])
        return png(path)[-rect[3]:]

    base = np.random.default_rng(7).normal(200, 4, (100, 200))
    base[40:46, 55:61] = 2000.0                 # one nucleus, in both boxes
    base[30:36, 35:41] = 2000.0                 # one in box 1 only
    b1, b2 = box(A, 10), box(A, 40)            # x 10..70 and 40..100
    l1 = np.zeros((H, W), np.int32)
    l1[20:26, 45:51] = 1
    l1[10:16, 25:31] = 2
    l2 = np.zeros((H, W), np.int32)
    l2[20:26, 15:21] = 1
    k1, k2 = {1: "positive", 2: "positive"}, {1: "positive"}
    lo2, hi2 = 100.0, 3000.0
    ov = scene_of(base, (0, 0, 200, 100), [b1, b2], {1: (l1, 10, 20), 2: (l2, 40, 20)},
                  {1: k1, 2: k2}, lo2, hi2, "overlap.png")
    c1 = crop_like(base, b1, l1, k1, lo2, hi2)
    c2 = crop_like(base, b2, l2, k2, lo2, hi2)
    gv = int(grey(base[42:43, 57:58], lo2, hi2)[0, 0])
    once = np.rint((1 - FILL_ALPHA) * gv + FILL_ALPHA * np.array([255, 0, 255])).astype(int)
    chk("overlapping boxes: a shared nucleus is blended once",
        ov[42, 57].tolist(), once.tolist())
    chk("...the same as in box 1's crop", ov[42, 57].tolist(), c1[22, 47].tolist())
    chk("...and in box 2's crop", ov[42, 57].tolist(), c2[22, 17].tolist())
    chk("...its outline is magenta", ov[40, 57].tolist(), list(COLOURS["positive"]))
    chk("a nucleus in box 1 only: as in box 1's crop",
        ov[30:36, 35:41].tolist(), c1[10:16, 25:31].tolist())

    # A nucleus in box 1's corner, outside disc 1 (centre x 35.5, r 20) but
    # inside disc 2 (centre x 65.5): box 2 counts it, so the scene must show
    # it counted, as box 2's crop does - not yellow, as box 1's corner has it.
    base3 = np.random.default_rng(9).normal(200, 4, (100, 200))
    base3[42:48, 63:69] = 2000.0
    m1_ = np.zeros((H, W), np.int32)
    m1_[22:28, 53:59] = 1
    m2_ = np.zeros((H, W), np.int32)
    m2_[22:28, 23:29] = 1
    ko1, ko2 = {1: "outside"}, {1: "positive"}
    ovo = scene_of(base3, (0, 0, 200, 100), [b1, b2], {1: (m1_, 10, 20), 2: (m2_, 40, 20)},
                   {1: ko1, 2: ko2}, lo2, hi2, "overlap_outside.png")
    co2 = crop_like(base3, b2, m2_, ko2, lo2, hi2)
    chk("overlap: counted in box 2, outside box 1's disc -> drawn counted",
        ovo[42:48, 63:69].tolist(), co2[22:28, 23:29].tolist())
    chk("...its outline is magenta, not yellow", ovo[42, 65].tolist(),
        list(COLOURS["positive"]))
    # And the other way round: box 1 counts it, box 2 has it outside.
    ovo2 = scene_of(base3, (0, 0, 200, 100), [b1, b2], {1: (m1_, 10, 20), 2: (m2_, 40, 20)},
                    {1: {1: "negative"}, 2: {1: "outside"}}, lo2, hi2, "overlap_outside2.png")
    chk("...counted by the lower box, outside the higher: the lower's colour",
        ovo2[42, 65].tolist(), list(COLOURS["negative"]))

    # A scene rectangle that cuts two boxes: x 40..140 over boxes at 10..70
    # and 110..170, each with nuclei inside and across the scene's edge.
    bl, br_ = box(A, 10), box(A, 110)
    ll = np.zeros((H, W), np.int32)
    ll[10:16, 32:38] = 1                        # inside, by the left edge
    ll[25:31, 27:33] = 2                        # across the left edge (x 40)
    lr = np.zeros((H, W), np.int32)
    lr[15:21, 27:33] = 1                        # across the right edge (x 140)
    lr[30:36, 5:11] = 2
    base2 = np.random.default_rng(8).normal(200, 4, (100, 200))
    for lab, x in ((ll, 10), (lr, 110)):
        base2[Y0:Y0 + H, x:x + W][lab > 0] = 2000.0
    kl, kr = {1: "positive", 2: "negative"}, {1: "censored", 2: "positive"}
    ed = scene_of(base2, (40, 10, 100, 80), [bl, br_], {1: (ll, 10, Y0), 2: (lr, 110, Y0)},
                  {1: kl, 2: kr}, lo2, hi2, "edge.png")
    chk("a box across the scene's left edge: its visible part is its crop's",
        bool(np.array_equal(ed[Y0 - 10:Y0 - 10 + H, 0:30],
                            crop_like(base2, bl, ll, kl, lo2, hi2)[:, 30:])), True)
    chk("a box across the right edge: likewise",
        bool(np.array_equal(ed[Y0 - 10:Y0 - 10 + H, 70:100],
                            crop_like(base2, br_, lr, kr, lo2, hi2)[:, :30])), True)
    ed7 = None
    OV.BAND_ROWS = 7
    try:
        ed7 = scene_of(base2, (40, 10, 100, 80), [bl, br_],
                       {1: (ll, 10, Y0), 2: (lr, 110, Y0)}, {1: kl, 2: kr},
                       lo2, hi2, "edge7.png")
    finally:
        OV.BAND_ROWS = band_saved
    chk("...and the same in 7-row bands", bool(np.array_equal(ed7, ed)), True)

    chk("tag_px(12000) == 100", OV.tag_px(12000), 100)
    wide = np.zeros((200, 12000))
    bw = dict(box(A, 600), czi_y0="150")
    wd = scene_of(wide, (0, 0, 12000, 200), [bw], {}, {}, 0.0, 1.0, "wide.png")
    t = OV.tag(1, 100)
    chk("a 12,000 px wide scene: the tag is 100 px text",
        t.shape[0] > 70 and t.shape[0] <= 110, True)
    chk("...placed just above-left of its box",
        bool(np.array_equal(wd[150 - t.shape[0]:150, 600 - t.shape[1]:600], t)), True)

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
        [r["section_positivity_cut"] for r in meas if r["scene_uid"] == B], ["", "", ""])
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
    print("--- a transient read error keeps the pictures, even under --force ---")
    index_before = open(OV.INDEX_CSV, encoding="utf-8").read()
    for p in pngs:
        os.utime(p, (past, past))
    rc, out = run_06i(OV, "--force", opener=FakeOpenCzi(BrokenCzi(PLANES, 6)))
    chk("exit 0", rc, 0)
    chk("'1 not drawn:'", "1 not drawn:" in out, True)
    fails = read_rows(OV.FAILURES_CSV)
    chk("failures.csv names section A",
        [(f["scene_uid"], "Input/output error" in f["reason"]) for f in fails], [(A, True)])
    chk("section A's pictures are untouched",
        [os.path.getmtime(p) for p in pngs if f"_{A}_" in p],
        [past] * len([p for p in pngs if f"_{A}_" in p]))
    chk("...and its index rows",
        sorted(r["path"] for r in read_rows(OV.INDEX_CSV) if r["scene_uid"] == A),
        sorted(r["path"] for r in csv.DictReader(io.StringIO(index_before))
               if r["scene_uid"] == A))
    rc, out = run_06i(OV, "--force", opener=Unopenable())
    chk("an unopenable CZI: '2 not drawn:'", "2 not drawn:" in out, True)
    chk("...every picture still there", all(os.path.exists(p) for p in pngs), True)
    chk("...index unchanged", sorted(r["path"] for r in read_rows(OV.INDEX_CSV)),
        sorted(r["path"] for r in csv.DictReader(io.StringIO(index_before))))
    rc, out = run_06i(OV)
    chk("reachable again: nothing failed", "not drawn:" in out, False)

    print()
    print("--- the fingerprint covers the strip's identity ---")
    write_rows(os.path.join(study.out_root, "manifest", "manifest_scenes.csv"),
               [{"scene_uid": A, "animal": "AB12", "slide": "2"},
                {"scene_uid": B, "animal": "AB12", "slide": "1"}])
    a_png = [p for p in pngs if f"_{A}_" in p]
    b_png = [p for p in pngs if f"_{B}_" in p]
    for p in pngs:
        os.utime(p, (past, past))
    rc, out = run_06i(OV)
    chk("a new slide number redraws section A",
        all(os.path.getmtime(p) != past for p in a_png), True)
    chk("...and not section B", [os.path.getmtime(p) for p in b_png], [past] * len(b_png))
    chk("...whose index row says slide 2",
        sorted({r["slide"] for r in read_rows(OV.INDEX_CSV) if r["scene_uid"] == A}), ["2"])

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
    chk("restored: drawn again, nothing failed", "not drawn:" in out, False)
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
    print("--- a median on the rounded cut: coloured by the exact one ---")
    # Background medians chosen so the cut is 200 + 3*1.4826*0.11 = 200.489...,
    # written 200.5; a nucleus at 200.5 is above the real cut and not above
    # the written one. 06a counts it positive, and so must the picture.
    rows = read_rows(nuc_csv)
    bg_rows = [r for r in rows if r["scene_uid"] == A and r["roi_index"] == "2"]
    for r, v in zip(bg_rows, ("199.8", "199.89", "200.0", "200.0", "200.11", "200.2")):
        r["marker_median"] = v
    at(A, 1, N)["marker_median"] = "200.5"
    nrow = [r for r in rows if r["scene_uid"] == A and r["roi_index"] == "1"
            and abs(float(r["czi_x"]) - (10 + N[1] + 2.5)) < 0.6
            and abs(float(r["czi_y"]) - (Y0 + N[0] + 2.5)) < 0.6][0]
    nrow["marker_median"] = "200.5"
    write_rows(nuc_csv, rows)
    run_06a()
    meas = read_rows(meas_csv)
    exact = float(read_rows(cuts_csv)[0]["cut"])
    m1 = [r for r in meas if r["scene_uid"] == A and r["roi_index"] == "1"][0]
    chk("the written cut is 200.5", m1["section_positivity_cut"], "200.5")
    chk("...the exact one just below it", 200.48 < exact < 200.5, True)
    chk("06a counts the nucleus at 200.5 positive", m1["n_positive"], "3")
    rc, out = run_06i(OV, "--force")
    chk("drawn: nothing failed", "not drawn:" in out, False)
    irow = [r for r in read_rows(OV.INDEX_CSV) if r["scene_uid"] == A
            and r["roi_index"] == "1"][0]
    chk("index n_positive == roi_measurements", irow["n_positive"], m1["n_positive"])
    chk("index cut is the written one", irow["cut"], "200.5")
    area = png(roi1)[-H:]
    chk("the nucleus at 200.5 is drawn positive",
        area[edge_px(N)].tolist(), list(COLOURS["positive"]))

    def a_pictures():
        return [os.path.join(folder, f) for f in pngs_in(folder) if f"_{A}_" in f]

    def mtimes(paths):
        return [os.path.getmtime(p) for p in paths]

    print()
    print("--- the fingerprint holds only what the pictures show ---")
    meas_rows = read_rows(meas_csv)
    meas_orig = [dict(r) for r in meas_rows]
    fp0 = OV.fingerprint(A, MARKER)
    a_now = a_pictures()
    for p in a_now:
        os.utime(p, (past, past))
    study_wide = ("abercrombie_factor", "mean_nucleus_diam_um", "cell_density_per_mm2",
                  "positive_density_per_mm2", "profile_density_per_mm2")
    for r in meas_rows:
        if r["scene_uid"] == A:
            for k in study_wide:
                r[k] = "123.4"
    write_rows(meas_csv, meas_rows)
    chk("Abercrombie / density columns changed: same fingerprint",
        OV.fingerprint(A, MARKER), fp0)
    rc, out = run_06i(OV)
    chk("...nothing redrawn", mtimes(a_now), [past] * len(a_now))
    m_a1 = [r for r in meas_rows if r["scene_uid"] == A and r["roi_index"] == "1"][0]
    saved_np = m_a1["n_positive"]
    m_a1["n_positive"] = str(int(saved_np) + 1)
    write_rows(meas_csv, meas_rows)
    chk("n_positive changed: a new fingerprint", OV.fingerprint(A, MARKER) != fp0, True)
    m_a1["n_positive"] = saved_np
    m_a1["frac_positive"] = "0.5"
    write_rows(meas_csv, meas_rows)
    chk("frac_positive changed: a new fingerprint", OV.fingerprint(A, MARKER) != fp0, True)
    rc, out = run_06i(OV)
    chk("...and section A is redrawn", all(t != past for t in mtimes(a_now)), True)
    write_rows(meas_csv, meas_orig)
    run_06a()
    rc, out = run_06i(OV)

    print()
    print("--- a marker segmented on its own channel: no % positive anywhere ---")
    rows = read_rows(nuc_csv)
    orig = [dict(r) for r in rows]
    for r in rows:
        if r["scene_uid"] == A:
            r["segmented_on"] = MARKER
    write_rows(nuc_csv, rows)
    run_06a()
    meas = read_rows(meas_csv)
    m_rois = [r for r in meas if r["scene_uid"] == A and r["roi_kind"] == "roi"]
    chk("06a withholds frac_positive for A's ROIs", [r["frac_positive"] for r in m_rois],
        ["", ""])
    chk("...and says why", all(r["denominator_withheld_reason"] for r in m_rois), True)
    rc, out = run_06i(OV)
    chk("drawn: nothing failed", "not drawn:" in out, False)
    index = {r["path"]: r for r in read_rows(OV.INDEX_CSV)}
    srow = index.get(os.path.relpath(sc_a, out_dir).replace(os.sep, "/"), {})
    crow = index.get(os.path.relpath(roi1, out_dir).replace(os.sep, "/"), {})
    chk("the crop's index row: no pct", crow.get("pct_positive"), "")
    chk("the scene's index row: no pct either", srow.get("pct_positive"), "")
    n_pos = m_rois[0]["n_positive"]
    cut_now = m_rois[0]["section_positivity_cut"]
    s_lines = [f"{MARKER} | fish AB12 | slide 2 | scene {A} | whole scene",
               f"2 ROIs: 3 counted, {n_pos} positive (n/a), cut {cut_now} | not counted: "
               f"1 outside ROI, 1 artifact, 1 off tissue | 1 censored",
               f"ROI 01 Dm: 3 counted, {n_pos} positive (n/a)",
               "ROI 03 Vd: 0 counted, 0 positive (n/a)"]
    s_st = strip(sw, s_lines, True, 0.65, 500)
    chk("the scene strip's totals say n/a",
        bool(np.array_equal(png(sc_a)[:s_st.shape[0]], s_st)), True)
    write_rows(nuc_csv, orig)
    run_06a()
    rc, out = run_06i(OV)

    print()
    print("--- no cut: every counted nucleus not called, censored included ---")
    rows = read_rows(nuc_csv)
    orig = [dict(r) for r in rows]
    pb_rows = [r for r in rows if r["scene_uid"] == B and r["roi_index"] == "1"]
    chk("section B's roi01 has one nucleus", len(pb_rows), 1)
    pb_rows[0]["censored"] = "1"
    write_rows(nuc_csv, rows)
    run_06a()
    mb1 = [r for r in read_rows(meas_csv) if r["scene_uid"] == B and r["roi_index"] == "1"][0]
    chk("06a: no cut, no n_positive, 1 censored",
        [mb1[k] for k in ("section_positivity_cut", "n_positive", "n_censored")], ["", "", "1"])
    rc, out = run_06i(OV)
    chk("drawn: nothing failed", "not drawn:" in out, False)
    brow = [r for r in read_rows(OV.INDEX_CSV) if r["scene_uid"] == B
            and r["roi_index"] == "1"][0]
    chk("...index n_censored == 06a's", brow["n_censored"], "1")
    chk("...strip says no cut", "no cut: too few background nuclei" in OV.crop_lines(brow)[1],
        True)
    ab = png(b_roi1)[-H:]
    sb = png(sc_b)[-sh:][Y0 - sy:Y0 - sy + H, 10 - sx:10 - sx + W]
    inner = (P[0] + 2, P[1] + 2)
    for name, a in (("crop", ab), ("scene", sb)):
        px = a[inner].tolist()
        chk(f"{name}: the censored nucleus is not filled (grey inside)",
            px[0] == px[1] == px[2], True)
        chk(f"{name}: ...its edge is cyan: not called", a[edge_px(P)].tolist(),
            list(COLOURS["negative"]))
    write_rows(nuc_csv, orig)
    run_06a()
    rc, out = run_06i(OV)

    print()
    print("--- no scene rectangle: the crops are drawn, the scene is not ---")
    OV.G5.use_marker(MARKER)
    geo = read_rows(OV.G5.GEOM_CSV)
    geo_orig = [dict(g) for g in geo]
    for g in geo:
        if g["scene_uid"] == A:
            for k in ("scene_x", "scene_y", "scene_w", "scene_h"):
                g[k] = ""
    write_rows(OV.G5.GEOM_CSV, geo)
    a_now = a_pictures()
    a_crops = [p for p in a_now if "_scene" not in p]
    for p in a_now:
        os.utime(p, (past, past))
    rc, out = run_06i(OV)
    chk("exit 0", rc, 0)
    chk("section A's crops are drawn", bool(a_crops) and all(t != past for t in mtimes(a_crops)),
        True)
    chk("...its scene picture is kept, not redrawn", mtimes([sc_a]), [past])
    chk("...and so is its index row",
        any(r["path"].endswith(os.path.basename(sc_a)) for r in read_rows(OV.INDEX_CSV)), True)
    fails = read_rows(OV.FAILURES_CSV)
    chk("failures.csv: section A, the scene rectangle named",
        [(f["scene_uid"], "scene rectangle" in f["reason"]) for f in fails], [(A, True)])
    chk("'1 not drawn:'", "1 not drawn:" in out, True)
    write_rows(OV.G5.GEOM_CSV, geo_orig)
    rc, out = run_06i(OV)
    chk("rectangle back: nothing failed", "not drawn:" in out, False)

    print()
    print("--- --no-crops, --no-scenes, --animal ---")
    shutil.rmtree(out_dir)
    rc, out = run_06i(OV, "--no-crops")
    chk("--no-crops: exit 0", rc, 0)
    chk("--no-crops: only the scene pictures",
        pngs_in(folder), sorted(os.path.basename(p) for p in (sc_a, sc_b)))
    for p in (sc_a, sc_b):
        os.utime(p, (past, past))
    rc, out = run_06i(OV, "--animal", "XX99")
    chk("--animal XX99: nothing drawn",
        pngs_in(folder), sorted(os.path.basename(p) for p in (sc_a, sc_b)))
    rc, out = run_06i(OV, "--animal", "AB12", "--no-scenes")
    chk("--animal AB12 --no-scenes: crops drawn", os.path.exists(b_roi1), True)
    chk("...the scene pictures neither redrawn nor removed",
        [os.path.getmtime(p) for p in (sc_a, sc_b)], [past, past])
    crops_now = [os.path.join(folder, f) for f in pngs_in(folder) if "_scene" not in f]
    for p in crops_now:
        os.utime(p, (past, past))
    rc, out = run_06i(OV, "--no-crops")
    chk("--no-crops: the crops neither redrawn nor removed",
        [os.path.getmtime(p) for p in crops_now], [past] * len(crops_now))
    shutil.rmtree(out_dir)
    rc, out = run_06i(OV, "--no-scenes")
    chk("--no-scenes: no _scene.png",
        [f for f in pngs_in(folder) if "_scene" in f], [])
    chk("...the crops are drawn", os.path.exists(roi1), True)
    crops_now = [os.path.join(folder, f) for f in pngs_in(folder)]
    for p in crops_now:
        os.utime(p, (past, past))
    rc, out = run_06i(OV)
    chk("then a full run: the scene pictures are drawn",
        [os.path.exists(p) for p in (sc_a, sc_b)], [True, True])
    chk("...and the up-to-date crops are not redrawn",
        [os.path.getmtime(p) for p in crops_now], [past] * len(crops_now))
    rc, out = run_06i(OV, "--animal", "XX99", "--force")
    chk("--animal XX99 --force: 0 sections", "0 sections" in out, True)

    print("--- two markers measured: every marker is drawn without --marker ---")
    MARKER2 = "AF488"
    C2 = "AB12_2a-s0"
    SC2 = 8
    sec2 = [dict(box(C2, 10), marker=MARKER2),
            dict(box(C2, 80, kind="background"), marker=MARKER2)]
    PLANES[(SC2, 0, 10)] = dapi([P], 7)
    PLANES[(SC2, 1, 10)] = mark({P: 2000.0}, 7)
    PLANES[(SC2, 0, 80)] = dapi(BG[:2], 8)
    PLANES[(SC2, 1, 80)] = mark({}, 8)
    D5.G5.use_marker(MARKER2)
    os.makedirs(os.path.dirname(D5.G5.GEOM_CSV), exist_ok=True)
    write_rows(D5.G5.GEOM_CSV, [{
        "scene_uid": C2, "animal": "AB12", "marker": MARKER2,
        "czi_file": "AB12_2a.czi", "scene_index": str(SC2),
        "m00": "1", "m01": "0", "m02": "0", "m10": "0", "m11": "1", "m12": "0",
        "scene_x": str(SCENE_RECT[0]), "scene_y": str(SCENE_RECT[1]),
        "scene_w": str(SCENE_RECT[2]), "scene_h": str(SCENE_RECT[3])}])
    write_rows(D5.G5.BOX_CSV, sec2)
    np.save(os.path.join(D5.REFORMAT_DIR, "sections", f"{C2}_mask.npy"),
            np.ones((256, 256), dtype=bool))
    saved = (pyczi.open_czi, D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(FakeCzi(PLANES))
    D5.load_model = lambda: FakeModel()
    sys.argv = ["05c_detect_rois.py", "--marker", MARKER2, "--order", "uid"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc2 = D5.main()
    finally:
        pyczi.open_czi, D5.load_model, sys.argv = saved
    chk("05c measured the second marker", rc2, 0)
    run_06a()
    shutil.rmtree(out_dir, ignore_errors=True)

    def run_all_markers(*extra):
        saved = (pyczi.open_czi, OV.D5.load_model, sys.argv)
        pyczi.open_czi = FakeOpenCzi(FakeCzi(PLANES))
        OV.D5.load_model = lambda: MODEL
        sys.argv = ["06i_detection_overlays.py"] + list(extra)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = OV.main()
        finally:
            pyczi.open_czi, OV.D5.load_model, sys.argv = saved
        print("     | " + buf.getvalue().strip().replace("\n", "\n     | "))
        return rc, buf.getvalue()

    f1 = os.path.join(out_dir, MARKER, "AB12")
    f2 = os.path.join(out_dir, MARKER2, "AB12")
    rc, out = run_all_markers("--no-scenes")
    chk("no --marker: exit 0", rc, 0)
    chk("...pictures for the first marker", len(pngs_in(f1)) > 0, True)
    chk("...and for the second, in its own folder", len(pngs_in(f2)) > 0, True)
    chk("...second marker's pictures are named for it",
        all(f.startswith(MARKER2 + "_") for f in pngs_in(f2)), True)
    idx = read_rows(os.path.join(out_dir, "index.csv"))
    chk("...one shared index names both markers",
        sorted({r["marker"] for r in idx}), sorted([MARKER, MARKER2]))
    shutil.rmtree(out_dir)
    rc, out = run_all_markers("--no-scenes", "--marker", MARKER2)
    chk("--marker limits the run to one marker",
        (len(pngs_in(f1)), len(pngs_in(f2)) > 0), (0, True))
    chk("...and the index holds only that marker",
        sorted({r["marker"] for r in read_rows(os.path.join(out_dir, "index.csv"))}),
        [MARKER2])

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
