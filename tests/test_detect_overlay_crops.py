"""05c draws the ROI and background-disc crops as it detects; 06i keeps them.

The crops used to wait for the Results button: 06i read every box again and
drew it. 05c already holds the marker plane of every box it segments, so it
draws each crop there and then, on a background thread, with the very code 06i
draws by. What is checked, end to end on a synthetic study (fake CZI, fake
model, as tests/test_detection_overlays.py):

  * after 05c the crops are under detection_overlays/<marker>/<animal>/ and
    listed in detection_overlays/index_05c.csv - not index.csv, which is 06i's;
  * their counts and cut are what 06a then publishes for each ROI;
  * after 06a, 06i adopts them: not redrawn (mtime unchanged), listed in
    index.csv; and a crop 05c drew is pixel-identical to the one 06i draws
    with --force;
  * a section whose rows or cut changed after 05c is redrawn by 06i, the
    others still adopted;
  * --no-overlays draws nothing; a picture that cannot be written is logged
    and costs the measurements nothing (roi_nuclei.csv byte-identical to a
    run without overlays);
  * 05c --force replaces only the redone sections' pictures;
  * 06i --workers 2 writes exactly what --workers 1 writes.

SYNTHETIC: a fake CZI reader and a fake model.

Run:  python tests/test_detect_overlay_crops.py
"""

import contextlib
import csv
import io
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


W, H, R = 60, 50, 20
Y0 = 20
MARKER = "AF568"
PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
A, B = "AB12_1a-s0", "AB12_1a-s1"
SCENE = {A: 6, B: 7}
SCENE_RECT = (5, 10, 220, 80)

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
    gx, gy = x + 25, Y0 + 25
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
    (7, 1, 190): mark({}, 6)[:, :4], (7, 0, 190): dapi([], 6)[:, :4],
}


def canvas(scene, channel):
    sx, sy, sw, sh = SCENE_RECT
    a = np.random.default_rng(100 + scene).normal(170, 3, (sh, sw))
    for (s, c, x), plane in PLANES.items():
        if (s, c) == (scene, channel):
            h, w = plane.shape
            a[Y0 - sy:Y0 - sy + h, x - sx:x - sx + w] = plane
    return a


class FakeCzi:
    """A plane per (scene, channel, box x0); anything else from the canvas.
    Planes come back uint16, as a scan's do."""

    def __init__(self, planes):
        self.planes = planes

    def read(self, **kw):
        s, c = int(kw["scene"]), int(kw["plane"]["C"])
        x, y, w, h = (int(v) for v in kw["roi"])
        got = self.planes.get((s, c, x))
        if got is None or got.shape != (h, w):
            sx, sy, _, _ = SCENE_RECT
            got = canvas(s, c)[y - sy:y - sy + h, x - sx:x - sx + w]
        return np.clip(np.rint(got), 0, 65535).astype(np.uint16)


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
    def predict_instances(self, img, **kw):
        return sk_label(np.asarray(img) > 0.5).astype(np.int32), {}


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


def setup_inputs(D5, out_root):
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
    os.makedirs(os.path.join(out_root, "manifest"), exist_ok=True)
    write_rows(os.path.join(out_root, "manifest", "manifest_scenes.csv"),
               [{"scene_uid": A, "animal": "AB12", "slide": "1"},
                {"scene_uid": B, "animal": "AB12", "slide": "1"}])


def run_05c(D5, *extra):
    saved = (pyczi.open_czi, D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(FakeCzi(PLANES))
    D5.load_model = lambda: FakeModel()
    sys.argv = ["05c_detect_rois.py", "--marker", MARKER, "--order", "uid"] + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = D5.main()
    finally:
        pyczi.open_czi, D5.load_model, sys.argv = saved
    out = buf.getvalue()
    print("     | " + out.strip().replace("\n", "\n     | "))
    return rc, out


def run_06a():
    with contextlib.redirect_stdout(io.StringIO()):
        rc = load_stage("06a_roi_dataset.py").main()
    if rc != 0:
        raise SystemExit(f"06a.main() returned {rc}")


def run_06i(OV, *extra):
    saved = (pyczi.open_czi, OV.D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpenCzi(FakeCzi(PLANES))
    OV.D5.load_model = lambda: FakeModel()
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


PAST = 1_000_000_000


def age(paths):
    for p in paths:
        os.utime(p, (PAST, PAST))


def aged(paths):
    return [os.path.getmtime(p) == PAST for p in paths]


with temp_study(acquisition=PAIRED) as study:
    D5 = load_stage("05c_detect_rois.py")
    LO = D5.LO
    setup_inputs(D5, study.out_root)
    ov_dir = os.path.join(study.out_root, "detection_overlays")
    folder = os.path.join(ov_dir, MARKER, "AB12")
    idx05 = os.path.join(ov_dir, "index_05c.csv")
    names = {(A, 1): LO.overlay_name(MARKER, A, "roi", 1, "Dm"),
             (A, 2): LO.overlay_name(MARKER, A, "background", 2),
             (A, 3): LO.overlay_name(MARKER, A, "roi", 3, "Vd"),
             (B, 1): LO.overlay_name(MARKER, B, "roi", 1, "Dm"),
             (B, 2): LO.overlay_name(MARKER, B, "background", 2)}
    paths = {k: os.path.join(folder, v) for k, v in names.items()}
    a_paths = [p for (u, _), p in paths.items() if u == A]
    b_paths = [p for (u, _), p in paths.items() if u == B]

    print("--- 05c draws the crops as it detects ---")
    rc, out = run_05c(D5)
    chk("05c exit 0", rc, 0)
    chk("a crop per segmented box, under <marker>/<animal>/",
        pngs_in(folder), sorted(names.values()))
    chk("...no whole-scene picture (06i draws those)",
        [f for f in pngs_in(folder) if "_scene" in f], [])
    chk("D5.INDEX_05C is detection_overlays/index_05c.csv", D5.INDEX_05C, idx05)
    rows05 = read_rows(idx05)
    chk("index_05c.csv: one row per crop", len(rows05), 5)
    chk("...columns: 06i's index columns + data_fingerprint, error",
        list(rows05[0]) if rows05 else None, LO.INDEX_KEYS + ["data_fingerprint", "error"])
    chk("...no errors", [r["error"] for r in rows05], [""] * 5)
    chk("...paths are relative to detection_overlays/",
        sorted(r["path"] for r in rows05),
        sorted(f"{MARKER}/AB12/{n}" for n in names.values()))
    chk("05c writes neither index.csv nor failures.csv",
        [os.path.exists(os.path.join(ov_dir, f)) for f in ("index.csv", "failures.csv")],
        [False, False])
    r05 = {(r["scene_uid"], int(r["roi_index"])): r for r in rows05}
    a1 = r05.get((A, 1), {})
    chk("roi01 of A: 5 counted, 2 positive (40.0%), 1 outside, 1 censored",
        [a1.get(k) for k in ("n_counted", "n_positive", "pct_positive", "n_outside",
                             "n_censored", "outlines")],
        ["5", "2", "40.0", "1", "1", "saved"])
    chk("section B (2 background nuclei): no cut, blank n_positive",
        [r05.get((B, 1), {}).get(k) for k in ("cut", "n_positive", "pct_positive")],
        ["", "", ""])
    img = png(paths[(A, 1)])
    st = LO.strip(W, LO.crop_lines(a1), True, 0.65, 50)
    chk("the crop is the box at native px under the strip of crop_lines(row)",
        (img.shape[:2], bool(np.array_equal(img[:st.shape[0]], st))),
        ((st.shape[0] + H, W), True))
    chk("section B's strip says no cut",
        "no cut: too few background nuclei" in LO.crop_lines(r05.get((B, 1), {}))[1], True)

    print()
    print("--- 06a publishes the same counts and cut ---")
    run_06a()
    meas = {(r["scene_uid"], int(r["roi_index"])): r
            for r in read_rows(os.path.join(study.out_root, "results",
                                            "roi_measurements.csv"))}
    agree = []
    for k, r in r05.items():
        m = meas[k]
        frac = m["frac_positive"]
        agree.append((r["n_counted"] == m["n_nuclei"], r["n_positive"] == m["n_positive"],
                      r["cut"] == m["section_positivity_cut"],
                      r["pct_positive"] == (str(round(float(frac) * 100, 1)) if frac else ""),
                      r["n_censored"] == m["n_censored"]))
    chk("every crop's counts, %, cut and censored == roi_measurements.csv",
        agree, [(True,) * 5] * 5)

    print()
    print("--- 06i adopts them: not redrawn, listed in index.csv ---")
    OV = load_stage("06i_detection_overlays.py")
    index05_before = open(idx05, encoding="utf-8").read()
    pix05 = {k: png(p) for k, p in paths.items()}
    age(paths.values())
    rc, out = run_06i(OV)
    chk("06i exit 0", rc, 0)
    chk("nothing not drawn", "not drawn:" in out, False)
    chk("no crop redrawn (mtime unchanged)", aged(paths.values()), [True] * 5)
    index = {r["path"]: r for r in read_rows(OV.INDEX_CSV)}
    rel = {k: f"{MARKER}/AB12/{n}" for k, n in names.items()}
    chk("index.csv lists every crop", all(rel[k] in index for k in rel), True)
    chk("...and the two scene pictures",
        sorted(r["path"] for r in index.values() if r["picture"] == "scene"),
        sorted(f"{MARKER}/AB12/{LO.overlay_name(MARKER, u, 'scene')}" for u in (A, B)))
    sfp = {r["scene_uid"]: r["fingerprint"] for r in index.values()
           if r["picture"] == "scene"}
    chk("...adopted rows carry 06i's fingerprint (the scene's)",
        all(index[rel[k]]["fingerprint"] == sfp[k[0]] for k in rel), True)
    chk("...and the counts 05c drew",
        all(all(index[rel[k]][c] == r05[k][c] for c in LO.INDEX_KEYS
                if c not in ("fingerprint",)) for k in rel), True)
    chk("06i never writes index_05c.csv", open(idx05, encoding="utf-8").read(), index05_before)
    chk("the scene picture holds the adopted crop, pixel for pixel",
        bool(np.array_equal(
            png(os.path.join(folder, LO.overlay_name(MARKER, A, "scene")))[-SCENE_RECT[3]:]
            [Y0 - SCENE_RECT[1]:Y0 - SCENE_RECT[1] + H, 10 - SCENE_RECT[0]:10 - SCENE_RECT[0] + W],
            pix05[(A, 1)][-H:])), True)
    age(paths.values())
    rc, out = run_06i(OV)
    chk("a second 06i run: still nothing redrawn", aged(paths.values()), [True] * 5)

    print()
    print("--- what 05c drew is what 06i --force draws ---")
    rc, out = run_06i(OV, "--force", "--no-scenes")
    chk("--force redraws every crop", aged(paths.values()), [False] * 5)
    chk("...to identical pixels",
        {k: bool(np.array_equal(png(p), pix05[k])) for k, p in paths.items()},
        {k: True for k in paths})

    print()
    print("--- rows or cut changed after 05c: that section is redrawn ---")
    nuc_csv = D5.NUCLEI_CSV
    rc, out = run_05c(D5, "--force")
    chk("05c --force: exit 0", rc, 0)
    rows = read_rows(nuc_csv)
    orig = [dict(r) for r in rows]
    bg_a = [r for r in rows if r["scene_uid"] == A and r["roi_index"] == "2"]
    for r in bg_a:
        r["marker_median"] = str(round(float(r["marker_median"]) + 5.0, 1))
    write_rows(nuc_csv, rows)
    cut_before = meas[(A, 1)]["section_positivity_cut"]
    run_06a()
    cuts_csv = os.path.join(study.out_root, "results", "positivity_cuts.csv")
    meas = {(r["scene_uid"], int(r["roi_index"])): r
            for r in read_rows(os.path.join(study.out_root, "results",
                                            "roi_measurements.csv"))}
    chk("the tampered background row moved A's cut",
        meas[(A, 1)]["section_positivity_cut"] != cut_before, True)
    os.remove(OV.INDEX_CSV)
    age(paths.values())
    rc, out = run_06i(OV, "--no-scenes")
    chk("exit 0, nothing not drawn", (rc, "not drawn:" in out), (0, False))
    chk("section A's crops are redrawn", aged(a_paths), [False] * 3)
    chk("section B's are adopted, not redrawn", aged(b_paths), [True] * 2)
    chk("index.csv lists all five",
        sorted(r["path"] for r in read_rows(OV.INDEX_CSV)), sorted(rel.values()))
    a1_06i = [r for r in read_rows(OV.INDEX_CSV) if r["path"] == rel[(A, 1)]][0]
    chk("...A's roi01 with 06a's new cut", a1_06i["cut"], meas[(A, 1)]["section_positivity_cut"])

    write_rows(nuc_csv, orig)
    run_06a()
    cuts = read_rows(cuts_csv)
    for r in cuts:
        if r["scene_uid"] == A:
            r["cut"] = repr(float(r["cut"]) + 1e-9)
    write_rows(cuts_csv, cuts)
    os.remove(OV.INDEX_CSV)
    age(paths.values())
    rc, out = run_06i(OV, "--no-scenes")
    chk("rows restored, exact cut nudged by 1e-9: A redrawn", aged(a_paths), [False] * 3)
    chk("...B adopted", aged(b_paths), [True] * 2)
    run_06a()

    print()
    print("--- 05c --force replaces only the redone sections' pictures ---")
    stale = os.path.join(folder, LO.overlay_name(MARKER, A, "roi", 9, "Dl"))
    with open(stale, "wb") as fh:
        fh.write(b"old")
    rows05 = read_rows(idx05)
    write_rows(idx05, rows05 + [dict(rows05[0], path=f"{MARKER}/AB12/{os.path.basename(stale)}",
                                     roi_index="9", region="Dl")])
    b_rows_before = [r for r in read_rows(idx05) if r["scene_uid"] == B]
    age(paths.values())
    rc, out = run_05c(D5, "--force", "--limit", "1")
    chk("05c --force --limit 1: exit 0", rc, 0)
    chk("section A's pictures are redrawn", aged(a_paths), [False] * 3)
    chk("section B's are untouched", aged(b_paths), [True] * 2)
    chk("...and so are its index_05c rows",
        [r for r in read_rows(idx05) if r["scene_uid"] == B], b_rows_before)
    chk("a picture 05c listed for a redone section, no longer drawn, is gone",
        os.path.exists(stale), False)
    chk("...and its row", [r for r in read_rows(idx05) if r["roi_index"] == "9"], [])

    print()
    print("--- --no-overlays draws nothing ---")
    shutil.rmtree(ov_dir)
    rc, out = run_05c(D5, "--force", "--no-overlays")
    chk("05c --no-overlays: exit 0", rc, 0)
    chk("...no pictures", pngs_in(folder), [])
    chk("...no index_05c.csv", os.path.exists(idx05), False)
    ref_nuclei = open(nuc_csv, "rb").read()
    ref_log = open(D5.DONE_LOG, "rb").read()

    print()
    print("--- a picture that cannot be written costs the measurements nothing ---")
    os.makedirs(ov_dir, exist_ok=True)
    with open(os.path.join(ov_dir, MARKER), "w") as fh:
        fh.write("a file where the marker's folder should be")
    rc, out = run_05c(D5, "--force")
    chk("05c exit 0", rc, 0)
    chk("roi_nuclei.csv byte-identical to the run without overlays",
        open(nuc_csv, "rb").read() == ref_nuclei, True)
    chk("...and the done log", open(D5.DONE_LOG, "rb").read() == ref_log, True)
    chk("the failure is said on stdout", "crop not drawn" in out, True)
    err = read_rows(idx05)
    chk("index_05c.csv: a row per crop, each with its error",
        (len(err), all(r["error"] for r in err)), (5, True))
    os.remove(os.path.join(ov_dir, MARKER))
    rc, out = run_05c(D5, "--force")
    chk("folder back: drawn, no errors",
        (pngs_in(folder), [r["error"] for r in read_rows(idx05)]),
        (sorted(names.values()), [""] * 5))

    print()
    print("--- 06i --workers 2 writes what --workers 1 writes ---")
    run_06a()

    def snapshot():
        pics = {f: png(os.path.join(folder, f)) for f in pngs_in(folder)}
        idx = sorted(tuple((k, v) for k, v in r.items() if k != "drawn_at")
                     for r in read_rows(OV.INDEX_CSV))
        return pics, idx, read_rows(OV.FAILURES_CSV)

    rc1, out1 = run_06i(OV, "--force", "--workers", "1")
    one = snapshot()
    rc2, out2 = run_06i(OV, "--force", "--workers", "2")
    two = snapshot()
    chk("both exit 0", (rc1, rc2), (0, 0))
    chk("--workers 2 says it draws 2 scenes at once", "2 scenes at once" in out2, True)
    chk("same pictures", sorted(one[0]) == sorted(two[0])
        and all(np.array_equal(one[0][f], two[0][f]) for f in one[0]), True)
    chk("...including both scene pictures",
        sum(1 for f in two[0] if "_scene" in f), 2)
    chk("same index.csv (drawn_at aside)", two[1], one[1])
    chk("same failures.csv", two[2], one[2])
    rc0, out0 = run_06i(OV, "--force", "--workers", "0")
    chk("--workers 0 is the default (half the cores, at most 4)", rc0, 0)
    chk("OV.DEFAULT_WORKERS: half the cores, at most 4",
        OV.DEFAULT_WORKERS, max(1, min(4, (os.cpu_count() or 2) // 2)))

    print()
    print("--- the app offers both switches ---")
    sys.path.insert(0, os.path.join(REPO, "app"))
    import workflow as WF                                    # noqa: E402
    chk("detect: --no-overlays", "--no-overlays" in [o.flag for o in WF.OPTIONS["detect"]], True)
    chk("detection_overlays: --workers",
        "--workers" in [o.flag for o in WF.OPTIONS["detection_overlays"]], True)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
