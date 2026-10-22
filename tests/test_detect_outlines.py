"""05c keeps each box's nucleus outlines, so the overlays can draw them.

roi_nuclei.csv holds a centroid per nucleus, not its shape, and a picture of
"what was counted" needs the shape. 05c already has every box's label image in
memory; it now writes them to `detection_outlines/<marker>/<uid>.npz` - one
`roi<NN>` per box it segmented, NN being the same `roi_index` the rows carry -
so 06i can draw them without running StarDist a second time.

What is checked here:

  * the npz is there, keyed by the rows' own roi_index, with the box origin
    and the StarDist settings it was made with;
  * every row's nucleus_id is a label in it, and the row's centroid is that
    label's centroid plus the origin - the file and the table describe the
    same objects;
  * a box StarDist found nothing in is saved all-zero ("measured, empty"),
    and a box too small to segment gets no key ("not measured");
  * --force rewrites the npz of the sections it redoes and no other, and a
    redone section that is then skipped loses its old npz;
  * segment_box / roi_inside can be called from outside main(), as 06i will.

SYNTHETIC, like test_detect_own_marker.py: a fake reader and a fake model.

Run:  python tests/test_detect_outlines.py
"""

import csv
import json
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
    """Two objects per crop - one inside the disc, one outside - or none on a
    flat crop (which normalises to all zeros)."""

    def __init__(self):
        self.calls = []

    def predict_instances(self, img, **kw):
        a = np.asarray(img)
        self.calls.append(a)
        lab = np.zeros(a.shape, dtype=np.int32)
        if np.ptp(a) > 0:
            lab[2:8, 2:8] = 1
            lab[12:18, 14:20] = 2
        return lab, {}


W, H = 24, 20
# Under the identity affine the first object of a crop at x0 sits at
# (x0 + 4.5, 104.5); the disc is placed on it and is too small to reach the
# second, which lands at (x0 + 16.5, 114.5).
R = 4


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def box(uid, marker, x, kind="roi", w=W):
    return {"scene_uid": uid, "animal": "AB12", "marker": marker,
            "roi_kind": kind, "region": "Dm" if kind == "roi" else "__background__",
            "seed_n": "1", "czi_x0": str(x), "czi_y0": "100",
            "czi_w": str(w), "czi_h": str(H),
            # curator px edges -> grid index is -0.5; put the centre on the
            # first object's centroid in grid coordinates.
            "sec_x": str(x + 4.5 + 0.5), "sec_y": str(104.5 + 0.5),
            "sec_r": str(R), "sec_poly": ""}


def drive(D5, marker, sections, planes, extra=(), masked=None):
    """Run 05c.main() over `sections` ({uid: [box, ...]})."""
    D5.G5.use_marker(marker)
    os.makedirs(D5.REFORMAT_DIR, exist_ok=True)
    geoms = [{"scene_uid": uid, "animal": "AB12", "marker": marker,
              "czi_file": "AB12_1a.czi", "scene_index": "6",
              "m00": "1", "m01": "0", "m02": "0",
              "m10": "0", "m11": "1", "m12": "0"} for uid in sections]
    with open(D5.G5.GEOM_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(geoms[0]))
        w.writeheader()
        w.writerows(geoms)
    boxes = [b for uid in sections for b in sections[uid]]
    with open(D5.G5.BOX_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(boxes[0]))
        w.writeheader()
        w.writerows(boxes)
    mask_dir = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    for uid in sections:
        p = os.path.join(mask_dir, f"{uid}_mask.npy")
        if masked is None or uid in masked:
            np.save(p, np.ones((256, 256), dtype=bool))
        elif os.path.exists(p):
            os.remove(p)

    fake, model = FakeCzi(planes), FakeModel()
    saved = (pyczi.open_czi, D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(fake)
    D5.load_model = lambda: model
    sys.argv = (["05c_detect_rois.py", "--marker", marker, "--order", "uid"]
                + list(extra))
    try:
        rc = D5.main()
    finally:
        pyczi.open_czi, D5.load_model, sys.argv = saved
    if rc != 0:
        raise SystemExit(f"05c.main() returned {rc} for {marker}")
    return fake, model, read_rows(D5.NUCLEI_CSV)


def plane(bg, patch=None, frac=0.0):
    rng = np.random.default_rng(int(bg))
    a = rng.normal(bg, bg * 0.02, (H, W))
    if patch is not None:
        a[: int(round(frac * H)), :] = patch
    return a


PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
MARKER = "AF568"
A, B = "AB12_1a-s0", "AB12_1a-s1"
XS = (100, 130, 160, 190)
dapi = plane(100, patch=900, frac=0.4)
flat = np.full((H, W), 300.0)
mark = plane(250)
PLANES = {(0, 100): dapi, (1, 100): mark,
          (0, 130): dapi, (1, 130): mark,
          (0, 160): flat, (1, 160): mark,     # StarDist finds nothing here
          (0, 190): dapi, (1, 190): mark}


def sec_boxes(uid):
    return [box(uid, MARKER, 100), box(uid, MARKER, 130, kind="background"),
            box(uid, MARKER, 160), box(uid, MARKER, 190, w=4)]  # too small


with temp_study(acquisition=PAIRED) as study:
    D5 = load_stage("05c_detect_rois.py")

    print("--- the reusable pieces are module-level ---")
    for name in ("segment_box", "roi_inside", "outline_path", "save_outlines",
                 "stardist_settings", "load_model", "plane_index_for"):
        chk(f"05c.{name} exists", callable(getattr(D5, name, None)), True)
    chk("OUTLINE_DIR is under out_root",
        getattr(D5, "OUTLINE_DIR", None),
        os.path.join(D5.OUT_ROOT, "detection_outlines"))
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    chk("outline_path", D5.outline_path(MARKER, A),
        os.path.join(D5.OUT_ROOT, "detection_outlines", MARKER, A + ".npz"))

    print()
    print("--- roi_inside: the loop's membership test, in grid coordinates ---")
    b = box(A, MARKER, 100)
    ins = D5.roi_inside(b)
    chk("the disc centre is inside", ins(104.5, 104.5), True)
    chk("a point just inside the radius", ins(104.5 + R - 0.01, 104.5), True)
    chk("a point just outside", ins(104.5 + R + 0.01, 104.5), False)
    pb = dict(b, sec_poly="10.5 10.5;20.5 10.5;20.5 20.5;10.5 20.5")
    pin = D5.roi_inside(pb)
    chk("a polygon, moved to grid indices: (10, 10) inside", pin(10.0, 10.0), True)
    chk("...(9.9, 15) outside", pin(9.9, 15.0), False)
    chk("...(19.9, 19.9) inside", pin(19.9, 19.9), True)

    print()
    print("--- one run over two sections ---")
    fake, model, rows = drive(D5, MARKER, {A: sec_boxes(A), B: sec_boxes(B)},
                              PLANES)
    pa, pb_ = D5.outline_path(MARKER, A), D5.outline_path(MARKER, B)
    chk("section A's npz exists", os.path.exists(pa), True)
    chk("section B's npz exists", os.path.exists(pb_), True)
    chk("no .part left behind",
        [f for f in os.listdir(os.path.dirname(pa)) if f.endswith(".part")], [])
    npz = np.load(pa)
    chk("keys: every segmented box, by roi_index, and the settings",
        sorted(npz.files),
        ["roi01", "roi01_origin", "roi02", "roi02_origin",
         "roi03", "roi03_origin", "settings"])
    chk("labels are uint32", npz["roi01"].dtype, np.dtype(np.uint32))
    chk("labels are the box's size", npz["roi01"].shape, (H, W))
    chk("origin is int64", npz["roi02_origin"].dtype, np.dtype(np.int64))
    chk("origin is [czi_x0, czi_y0]", npz["roi02_origin"].tolist(), [130, 100])
    chk("settings are stardist_settings(marker)",
        json.loads(str(npz["settings"])), D5.stardist_settings(MARKER))
    chk("the empty box is saved all-zero", int(npz["roi03"].max()), 0)
    chk("...the too-small box has no key", "roi04" in npz.files, False)

    a_rows = [r for r in rows if r["scene_uid"] == A]
    chk("A has one counted nucleus in each non-empty box", len(a_rows), 2)
    for ri in (1, 2):
        lab = npz[f"roi{ri:02d}"]
        ox, oy = npz[f"roi{ri:02d}_origin"].tolist()
        mine = [r for r in a_rows if int(r["roi_index"]) == ri]
        ids = {int(r["nucleus_id"]) for r in mine}
        chk(f"roi{ri:02d}: rows' nucleus_ids are labels in the npz",
            ids <= set(np.unique(lab).tolist()) - {0}, True)
        chk(f"roi{ri:02d}: the npz also has the nucleus outside the disc",
            len(set(np.unique(lab).tolist()) - {0}), 2)
        ok = True
        for r in mine:
            yy, xx = np.nonzero(lab == int(r["nucleus_id"]))
            cx, cy = ox + xx.mean(), oy + yy.mean()
            ok &= bool(abs(cx - float(r["czi_x"])) <= 0.5
                        and abs(cy - float(r["czi_y"])) <= 0.5)
        chk(f"roi{ri:02d}: each row's centroid is its label's + origin", ok, True)

    print()
    print("--- segment_box, called from outside main() ---")
    g = {"scene_index": "6"}
    got = D5.segment_box(FakeCzi(PLANES), b, int(g["scene_index"]),
                         FakeModel(), D5.min_area_px_for(), {})
    chk("it returns the loop's dict",
        sorted(got) if got else got,
        sorted(["labels", "seg_img", "dapi", "mark", "planes", "report",
                "seg_on", "backend", "shaped"]))
    chk("...whose labels are the saved ones",
        bool(np.array_equal(got["labels"], npz["roi01"])), True)
    chk("a box under 8 px is not segmented",
        D5.segment_box(FakeCzi(PLANES), box(A, MARKER, 190, w=4), 6,
                       FakeModel(), D5.min_area_px_for(), {}), None)
    # Contagion's ND12_s01a_sc15: DAPI at the ceiling with a few pixels just
    # under it. StarDist's NMS aborted the whole 05c process on that crop.
    near = np.full((H, W), 65535.0)
    near[0, :2] = 65522.0                   # under 1% of it: p1 is the ceiling
    sat ={**PLANES, (0, 220): near, (1, 220): mark}
    import contextlib, io                                   # noqa: E401
    said, sat_model = io.StringIO(), FakeModel()
    with contextlib.redirect_stdout(said):
        got = D5.segment_box(FakeCzi(sat), box(A, MARKER, 220), 6,
                             sat_model, D5.min_area_px_for(), {})
    chk("a near-saturated crop is not segmented", got, None)
    chk("...it never reaches the model", sat_model.calls, [])
    chk("...and the run says which box and why",
        "not segmented" in said.getvalue() and A in said.getvalue(), True)
    npz.close()

    print()
    print("--- --force rewrites only the sections it redoes ---")
    past = 1_000_000_000
    os.utime(pa, (past, past))
    os.utime(pb_, (past, past))
    b_bytes = open(pb_, "rb").read()
    rows_before = read_rows(D5.NUCLEI_CSV)
    drive(D5, MARKER, {A: sec_boxes(A), B: sec_boxes(B)}, PLANES,
          extra=["--force", "--limit", "1"])
    chk("A's npz was rewritten", os.path.getmtime(pa) != past, True)
    chk("B's npz was not touched", os.path.getmtime(pb_), past)
    chk("...nor its bytes", open(pb_, "rb").read() == b_bytes, True)
    rows_after = read_rows(D5.NUCLEI_CSV)
    chk("rows are the same after the redo",
        sorted(tuple(r.items()) for r in rows_after)
        == sorted(tuple(r.items()) for r in rows_before), True)

    print()
    print("--- a redone section that is then skipped loses its old npz ---")
    drive(D5, MARKER, {A: sec_boxes(A), B: sec_boxes(B)}, PLANES,
          extra=["--force", "--limit", "1"], masked={B})
    chk("A (no tissue mask now) has no npz", os.path.exists(pa), False)
    chk("B still has its npz", os.path.exists(pb_), True)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
