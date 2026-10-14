"""Curated shapes meet nuclei in one pixel convention.

The curator stores a click at pixel EDGES (pixel i spans [i, i+1]); 05a's
matrix and 05c's nucleus positions index pixel CENTRES (pixel i is at i).
Compared directly, every ROI and disc sat half a grid pixel (about 16 um)
down and right of where it was drawn. These drive 05c's real loop with an
identity geometry, so a grid index is a CZI pixel and the expected answer can
be read off the drawing.

SYNTHETIC; it pins the convention, not a count on a real section.

Run:  python tests/test_curator_offset.py
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
UID = "AB12_1a-s0"
N = 32

# The curator's square from 10 to 20 on its canvas: pixel EDGES, so it holds
# grid pixels 10..19, whose centres - as 05a and 05c index them - are 10..19.
# Its edges are therefore at 9.5 and 19.5 in grid-index coordinates.
SQUARE = "10 10;20 10;20 20;10 20"


def write_inputs(D5, sec_poly, sec_x="15", sec_y="15", sec_r="0"):
    D5.G5.use_marker("AF568")
    os.makedirs(D5.REFORMAT_DIR, exist_ok=True)
    # Identity: a CZI pixel IS a grid index, so a centroid is its own grid point.
    geom = {"scene_uid": UID, "animal": "AB12", "marker": "AF568",
            "czi_file": "AB12_1a.czi", "scene_index": "0",
            "m00": "1", "m01": "0", "m02": "0", "m10": "0", "m11": "1", "m12": "0"}
    box = {"scene_uid": UID, "animal": "AB12", "marker": "AF568",
           "roi_kind": "roi", "region": "Dm", "seed_n": "1",
           "czi_x0": "0", "czi_y0": "0", "czi_w": str(N), "czi_h": str(N),
           "sec_x": sec_x, "sec_y": sec_y, "sec_r": sec_r, "sec_poly": sec_poly}
    for path, row in ((D5.G5.GEOM_CSV, geom), (D5.G5.BOX_CSV, box)):
        with open(path, "w", newline="\n", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            w.writeheader()
            w.writerow(row)
    sec = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(sec, exist_ok=True)
    np.save(os.path.join(sec, f"{UID}_mask.npy"), np.ones((256, 256), dtype=bool))


def run(D5, labels):
    planes = {D5.DAPI_C: np.full((N, N), 100.0), D5.MARK_C: np.full((N, N), 500.0)}
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
        return sorted(int(r["nucleus_id"]) for r in csv.DictReader(fh))


def ell(labels, k, r, c):
    """Three pixels (r,c) (r,c+1) (r+1,c+1): centroid x = c + 2/3."""
    labels[r, c] = labels[r, c + 1] = labels[r + 1, c + 1] = k


def lle(labels, k, r, c):
    """Three pixels (r,c) (r,c+1) (r+1,c): centroid x = c + 1/3."""
    labels[r, c] = labels[r, c + 1] = labels[r + 1, c] = k


# 1  centroid x 9.67: inside the drawn square (pixel 10), which the old test
#    (x >= 10) threw away
# 2  centroid x 19.67: in pixel 20, outside it, which the old test kept
# 3  well inside, either way
labels = np.zeros((N, N), dtype=np.int32)
ell(labels, 1, 14, 9)
ell(labels, 2, 14, 19)
labels[14:17, 14:17] = 3

with temp_study(acquisition=PAIRED) as study:
    D5 = load_stage("05c_detect_rois.py")
    G5 = D5.G5

    print("=== the conversion ===")
    chk("a curator pixel edge is half a grid index back",
        tuple(float(a) for a in G5.curator_to_grid(10.0, 20.0)), (9.5, 19.5))
    chk("the centre of curator pixel 10 is grid index 10",
        tuple(float(a) for a in G5.curator_to_grid(10.5, 10.5)), (10.0, 10.0))

    print()
    print("=== a drawn region keeps exactly the pixels drawn ===")
    write_inputs(D5, SQUARE)
    kept = run(D5, labels)
    chk("the nucleus in its first column is kept", 1 in kept, True)
    chk("the nucleus just past its last column is not", 2 in kept, False)
    chk("the one in the middle is kept", 3 in kept, True)

    print()
    print("=== and so does a disc ===")
    # Centred on the middle of curator pixel 15 - grid index 15 - radius 5.
    # Before the fix the centre sat at 15.5, half a pixel right and down.
    labels2 = np.zeros((N, N), dtype=np.int32)
    lle(labels2, 1, 15, 10)          # (10.33, 15.33): 4.68 from 15, 5.18 from 15.5
    lle(labels2, 2, 15, 20)          # (20.33, 15.33): 5.33 from 15, 4.84 from 15.5
    write_inputs(D5, "", sec_x="15.5", sec_y="15.5", sec_r="5")
    kept = run(D5, labels2)
    chk("a disc keeps what is within its radius of where it was drawn",
        kept, [1])

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASS")
