"""05c decides `censored` from each nucleus's own native pixels.

A nucleus is censored when any marker pixel under its nuclear mask sits at the
file's clip_ceiling (manifest/manifest_files.csv). This used to be read off
04j's mask on the 256 grid, and then repaired by 06f from a 5.2 um/px raw clip
mask sampled at the centroid. Both are a grid point standing in for a 7 um
object, so both can be wrong in two directions; this pins those two cases and
the two where every method agrees, against 06f's own sampling code run on a
mask built from the same pixels.

`artifact` gets the same treatment: decided on 04g's own 5.2 um/px mask at the
centroid's native position, against the 256 copy 04a resampled from it twice.

SYNTHETIC. The planes, the model and the clip mask are made up here; what this
guarantees is the rule and the plumbing, not a count on a real section.

Run:  python tests/test_native_censor.py
"""

import csv
import os
import sys
import types

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from _fixture import temp_study, load_stage                  # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(58) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


# 05c imports pylibCZIrw and csbdeep inside its loop. Neither is needed to
# check a flag, and neither is installed everywhere these tests run, so a
# stand-in is registered for whichever is missing. The reader is replaced
# below either way.
try:
    import pylibCZIrw.czi                                    # noqa: F401
except ImportError:
    pkg = types.ModuleType("pylibCZIrw")
    pkg.czi = types.ModuleType("pylibCZIrw.czi")
    pkg.czi.open_czi = None
    sys.modules["pylibCZIrw"] = pkg
    sys.modules["pylibCZIrw.czi"] = pkg.czi
try:
    import csbdeep.utils                                     # noqa: F401
except ImportError:
    pkg = types.ModuleType("csbdeep")
    pkg.utils = types.ModuleType("csbdeep.utils")
    pkg.utils.normalize = lambda img, *a, **kw: np.asarray(img, dtype=np.float32)
    sys.modules["csbdeep"] = pkg
    sys.modules["csbdeep.utils"] = pkg.utils

import pylibCZIrw.czi as pyczi                               # noqa: E402


class FakeCzi:
    def __init__(self, planes):
        self.planes = planes

    def read(self, **kw):
        return self.planes[int(kw["plane"]["C"])]


class FakeOpen:
    def __init__(self, doc):
        self.doc = doc

    def __call__(self, *a, **kw):
        return self

    def __enter__(self):
        return self.doc

    def __exit__(self, *exc):
        return False


class FakeModel:
    def __init__(self, labels):
        self.labels = labels

    def predict_instances(self, img, **kw):
        return self.labels, {}


PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
FILE = "AB12_1a.czi"
UID = "AB12_1a-s0"
X0 = Y0 = 96            # on the 8 px grid of the 5.2 um mask below
N = 64                  # box side, native px
CEIL = 4095             # a 12-bit file: 65535 would never fire on it
ZOOM = 0.125            # 0.65 -> 5.2 um/px, as 01k reads it


def write_inputs(D5, ceiling_rows):
    D5.G5.use_marker("AF568")
    os.makedirs(D5.REFORMAT_DIR, exist_ok=True)
    geom = {"scene_uid": UID, "animal": "AB12", "marker": "AF568",
            "czi_file": FILE, "scene_index": "0",
            "m00": "1", "m01": "0", "m02": "0", "m10": "0", "m11": "1", "m12": "0"}
    box = {"scene_uid": UID, "animal": "AB12", "marker": "AF568",
           "roi_kind": "roi", "region": "Dm", "seed_n": "1",
           "czi_x0": str(X0), "czi_y0": str(Y0), "czi_w": str(N), "czi_h": str(N),
           "sec_x": "128", "sec_y": "128", "sec_r": "120", "sec_poly": ""}
    for path, row in ((D5.G5.GEOM_CSV, geom), (D5.G5.BOX_CSV, box)):
        with open(path, "w", newline="\n", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            w.writeheader()
            w.writerow(row)
    sec = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(sec, exist_ok=True)
    np.save(os.path.join(sec, f"{UID}_mask.npy"), np.ones((256, 256), dtype=bool))
    man = os.path.join(D5.OUT_ROOT, "manifest")
    os.makedirs(man, exist_ok=True)
    path = os.path.join(man, "manifest_files.csv")
    if ceiling_rows is None:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "clip_ceiling"])
        w.writerows(ceiling_rows)


def run(D5, mark, labels):
    planes = {D5.DAPI_C: np.full((N, N), 100.0), D5.MARK_C: mark}
    saved = (pyczi.open_czi, D5.load_model, sys.argv)
    pyczi.open_czi = FakeOpen(FakeCzi(planes))
    D5.load_model = lambda: FakeModel(labels)
    sys.argv = ["05c_detect_rois.py", "--marker", "AF568", "--order", "uid", "--force"]
    try:
        rc = D5.main()
    finally:
        pyczi.open_czi, D5.load_model, sys.argv = saved
    if rc != 0:
        raise SystemExit(f"05c.main() returned {rc}")
    with open(D5.NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        return {int(r["nucleus_id"]): r for r in csv.DictReader(fh)}


# Four 10x10 nuclei, one per quadrant of the box. Box-local coordinates; the
# 8 px cells of the 5.2 um mask start at box-local 0 because X0, Y0 are on it.
#   1  clean, and so is everything near it                 -> 0 everywhere
#   2  every pixel clipped                                  -> 1 everywhere
#   3  ONE clipped pixel on its rim, in a different 5.2 um
#      cell from its centroid                               -> native 1, 06f 0
#   4  clean, but a clipped neighbour outside the mask shares
#      the 5.2 um cell its centroid falls in                -> native 0, 06f 1
labels = np.zeros((N, N), dtype=np.int32)
labels[3:13, 3:13] = 1                    # centroid (7.5, 7.5): cell (0, 0)
labels[3:13, 35:45] = 2
labels[35:45, 3:13] = 3                   # centroid (39.5, 7.5): cell (4, 0)
labels[35:45, 35:45] = 4                  # centroid (39.5, 39.5): cell (4, 4)

mark = np.full((N, N), 800.0)
mark[3:13, 35:45] = CEIL
mark[44, 12] = CEIL                       # rim of 3, in cell (5, 1)
mark[33, 33] = CEIL                       # off 4's mask, in cell (4, 4)

print("=== the rule ===")
with temp_study(acquisition=PAIRED) as study:
    D5 = load_stage("05c_detect_rois.py")
    chk("a pixel AT the ceiling is clipped",
        D5.clipped(np.array([10, CEIL]), CEIL), True)
    chk("one below it is not", D5.clipped(np.array([10, CEIL - 1]), CEIL), False)
    chk("an empty mask is not", D5.clipped(np.array([]), CEIL), False)

    print()
    print("=== native flags, against 06f on the same nuclei ===")
    write_inputs(D5, [[FILE, CEIL], ["other.czi", 65535]])
    rows = run(D5, mark, labels)
    native = {k: int(r["censored"]) for k, r in rows.items()}
    chk("all four nuclei were measured", sorted(native), [1, 2, 3, 4])

    # 06f's own sampling, on a raw clip mask at 5.2 um built from the same
    # plane - a cell is clipped when any native pixel in it is, the most
    # generous reading 01k could give it.
    D6 = load_stage("06f_recensor_nuclei.py")
    cells = (mark >= CEIL).reshape(N // 8, 8, N // 8, 8).any(axis=(1, 3))
    ov = np.zeros((X0 // 8 + N // 8 + 4,) * 2, dtype=bool)
    ov[Y0 // 8:Y0 // 8 + N // 8, X0 // 8:X0 // 8 + N // 8] = cells
    mpath = os.path.join(study.out_root, "clip_mask.png")
    Image.fromarray((ov * 255).astype(np.uint8)).save(mpath)
    cache = D6.MaskCache({UID: (0.0, 0.0, ZOOM, mpath)})
    old = {k: int(cache.sample(UID, float(r["czi_x"]), float(r["czi_y"]))[0])
           for k, r in rows.items()}

    chk("clean nucleus: neither flags it", (native[1], old[1]), (0, 0))
    chk("fully clipped nucleus: both flag it", (native[2], old[2]), (1, 1))
    chk("one clipped rim pixel: native flags it, 06f misses it",
        (native[3], old[3]), (1, 0))
    chk("clipped neighbour in its cell: 06f flags it, native not",
        (native[4], old[4]), (0, 1))

    print()
    print("=== the ceiling is the file's own ===")
    write_inputs(D5, [[FILE, 65535]])
    rows = run(D5, mark, labels)
    chk("at 65535, 4095 is a value, not a clip",
        sorted(int(r["censored"]) for r in rows.values()), [0, 0, 0, 0])

    write_inputs(D5, None)
    rows = run(D5, mark, labels)
    chk("no manifest: falls back to 65535",
        sorted(int(r["censored"]) for r in rows.values()), [0, 0, 0, 0])
    sat = mark.copy()
    sat[3:13, 3:13] = 65535
    rows = run(D5, sat, labels)
    chk("...which still catches a 16-bit clip",
        int(rows[1]["censored"]), 1)

    # A section measured with no _artifact.npy gets artifact=0 on every
    # nucleus - the same value a clean section gets. write_inputs() lays down
    # a tissue mask and no artifact mask, so every run above was one of those.
    print()
    print("=== a missing artifact mask is reported, not silent ===")
    import contextlib
    import io

    def run_quiet():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run(D5, mark, labels)
        return buf.getvalue()

    def recorded():
        if not os.path.exists(D5.NO_ARTIFACT_CSV):
            return None
        with open(D5.NO_ARTIFACT_CSV, newline="", encoding="utf-8") as fh:
            return sorted((r["scene_uid"], r["marker"]) for r in csv.DictReader(fh))

    # Another marker's row is not this run's to drop.
    os.makedirs(os.path.dirname(D5.NO_ARTIFACT_CSV), exist_ok=True)
    with open(D5.NO_ARTIFACT_CSV, "w", newline="", encoding="utf-8") as fh:
        fh.write("scene_uid,marker\nAB13_2a-s0,AF488\n")
    write_inputs(D5, [[FILE, CEIL]])
    out = run_quiet()
    chk("warned before measuring",
        out.find("no artifact mask") != -1
        and out.find("no artifact mask") < out.find(" nuclei -> "), True)
    chk("...and again at the end",
        out.rfind("no artifact mask") > out.find(" nuclei -> "), True)
    chk("the section is recorded, the other marker's row kept",
        recorded(), [(UID, "AF568"), ("AB13_2a-s0", "AF488")])
    chk("no .tmp left beside it",
        os.path.exists(D5.NO_ARTIFACT_CSV + ".tmp"), False)

    np.save(os.path.join(D5.REFORMAT_DIR, "sections", f"{UID}_artifact.npy"),
            np.zeros((256, 256), dtype=bool))
    out = run_quiet()
    chk("with the mask in place: no warning",
        "no artifact mask" in out, False)
    chk("...and the re-measured section leaves the record",
        recorded(), [("AB13_2a-s0", "AF488")])

    # `artifact` is decided on 04g's own mask at 5.2 um/px, at the centroid's
    # native position - not on the 256 copy, which 04a made by resizing that
    # mask by nearest neighbour twice (overview -> 400 working grid -> 256).
    # Here a grid cell is 40 native px = 26 um, as on a real section, and the
    # .npy is built by exactly those two resizes at angle 0 with a full-frame
    # silhouette, so its crop and pad are the identity.
    print()
    print("=== artifact: 04g's mask at the native position, not the 256 grid ===")
    K = 40                       # native px per grid cell
    SC = 8                       # native px per overview px (0.65 -> 5.2 um)
    OV = 256 * K // SC           # overview side, px
    AX0 = AY0 = 960              # box origin: grid 24, overview 120
    geom = {"scene_uid": UID, "animal": "AB12", "marker": "AF568",
            "czi_file": FILE, "scene_index": "0",
            "scene_x": "0", "scene_y": "0", "scene_w": str(OV * SC),
            "scene_h": str(OV * SC), "overview_um_px": "5.2",
            "native_um_px": "0.65", "src_w": str(OV), "src_h": str(OV),
            "m00": str(K), "m01": "0", "m02": "0",
            "m10": "0", "m11": str(K), "m12": "0"}
    box = {"scene_uid": UID, "animal": "AB12", "marker": "AF568",
           "roi_kind": "roi", "region": "Dm", "seed_n": "1",
           "czi_x0": str(AX0), "czi_y0": str(AY0), "czi_w": str(N),
           "czi_h": str(N), "sec_x": "25", "sec_y": "25", "sec_r": "20",
           "sec_poly": ""}
    for path, row in ((D5.G5.GEOM_CSV, geom), (D5.G5.BOX_CSV, box)):
        with open(path, "w", newline="\n", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            w.writeheader()
            w.writerow(row)
    # The same four nuclei. Their centroids, in overview px and grid cells:
    #   1 (120.9, 120.9) -> grid (24, 24), which samples overview (123, 123)
    #   2 (124.9, 120.9) -> grid (25, 24), samples (126, 123)
    #   3 (120.9, 124.9) -> grid (24, 25), samples (123, 126)
    #   4 (124.9, 124.9) -> grid (25, 25), samples (126, 126)
    ov = np.zeros((OV, OV), dtype=np.uint8)
    ov[120:122, 120:122] = 1     # 10 um bubble under 1; its cell samples past it
    ov[126:128, 126:128] = 2     # 10 um fleck 3 um off 4, on 4's cell's sample
    ov[120:124, 124:127] = 1     # under 2 AND on its cell's sample: both agree
    os.makedirs(os.path.join(study.out_root, "artifacts"), exist_ok=True)
    png = os.path.join(study.out_root, "artifacts", f"{UID}_artifact.png")
    Image.fromarray(ov).save(png)
    grid = np.asarray(Image.fromarray(ov).resize((400, 400), Image.NEAREST)
                      .resize((256, 256), Image.NEAREST)) > 0
    np.save(os.path.join(D5.REFORMAT_DIR, "sections", f"{UID}_artifact.npy"),
            grid)
    plain = np.full((N, N), 800.0)
    rows = run(D5, plain, labels)
    art = {k: int(r["artifact"]) for k, r in rows.items()}
    chk("the grid copy lost the bubble and kept the fleck",
        (bool(grid[24, 24]), bool(grid[25, 25])), (False, True))
    chk("all four nuclei were measured", sorted(art), [1, 2, 3, 4])
    chk("10 um bubble under the centroid: flagged", art[1], 1)
    chk("clean, but a fleck 3 um away fills its cell: not flagged", art[4], 0)
    chk("inside an artifact both frames agree on: flagged", art[2], 1)
    chk("clean and clear: not flagged", art[3], 0)

    # The .npy still decides WHETHER a section is masked: 04a writes it only
    # when the section's mask was not rejected on review. Without 04g's PNG
    # (or a geometry row from before 05a recorded the scene rectangle) there
    # is nothing finer to read, so the grid copy is used - and said.
    os.remove(png)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rows = run(D5, plain, labels)
    chk("no 04g mask: falls back to the grid copy",
        [int(rows[k]["artifact"]) for k in (1, 2, 3, 4)], [0, 1, 0, 1])
    chk("...and says how many sections did",
        "1 sections had artifacts decided on the 256 grid" in buf.getvalue(),
        True)

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASS")
