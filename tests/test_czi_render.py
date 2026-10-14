"""`04o` rendering the curation composites from the CZI, not the overview.

The overview route throws detail away at every step - 5.20 um/px, 8-bit
through a clipped display range, squashed square, resampled three times - and
the operator places ROIs on what is left. The CZI route has to give the SAME
frame (or every landmark already placed moves) with the detail put back. What
can go wrong is quiet:

  * a frame that is off by a few pixels looks exactly as right as one that is
    not - so a feature at a known slide position has to land where 05a's
    matrix says it is;
  * a different frame altogether still makes a plausible picture - so the
    render, shrunk to 256, has to match the 256 greyscale 04a writes;
  * a render that is merely LARGER is not more detailed - two nuclei 6 um
    apart have to come out as two, where the overview route merges them;
  * the tile field, and the never-scanned outside of the scene, must come out
    as 01 draws them;
  * the stage itself, run end to end against a stand-in pylibCZIrw, has to
    write a composite at the asked resolution and say which render it is.

Run:  python tests/test_czi_render.py
"""

import csv
import os
import sys
import types

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

STUDY = use_temp_study(acquisition={"layout": "paired",
                                    "markers": ["AF568", "AF488"]})
O = load_stage("04o_section_rgb.py")
RF, GEO = O.RF, O.GEO

fails = 0


def chk(label, ok, got=""):
    global fails
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(64) + " " + str(got))


# ---------------------------------------------------------------- a slide

NATIVE = 0.65
OV_UM = 5.2
K = 8                                   # native px per overview px
RX, RY = 4000, 7000                     # scene rectangle origin on the slide
OW, OH = 400, 290                       # overview size - deliberately not square
RW, RH = OW * K, OH * K                 # scene rectangle, native

rng = np.random.default_rng(7)
yy, xx = np.mgrid[0:RH, 0:RW].astype(np.float32)
cx, cy = RW * 0.52, RH * 0.48
# An asymmetric section, tilted, so 04a's rotation and 180 resolution both act.
a = np.radians(23)
dx, dy = xx - cx, yy - cy
u = dx * np.cos(a) + dy * np.sin(a)
v = -dx * np.sin(a) + dy * np.cos(a)
tissue = (u / (RW * 0.36)) ** 2 + (v / (RH * 0.30)) ** 2 < 1
tissue &= ~((v > 0) & (np.abs(u) < RW * 0.08))       # a notch: dorsoventral asymmetry
dapi = np.where(tissue, 9000.0, 300.0) + rng.normal(0, 400, tissue.shape)
# Nuclei: small blobs, so the DAPI has texture at the scale that gets lost.
for _ in range(1500):
    py_, px_ = rng.integers(0, RH), rng.integers(0, RW)
    if tissue[py_, px_]:
        dapi[max(0, py_ - 3):py_ + 4, max(0, px_ - 3):px_ + 4] += 6000
dapi = np.clip(dapi, 0, 65535).astype(np.uint16)

mark = np.where(tissue, 1500.0, 200.0) + rng.normal(0, 60, tissue.shape)
# Two small marker-positive objects 4.5 um apart (7 native px) - closer than
# one 5.2 um overview pixel, at known places.
PAIR_Y = int(cy + RH * 0.05)
PAIR_X = (int(cx - 3), int(cx + 4))
for x in PAIR_X:
    mark[PAIR_Y - 1:PAIR_Y + 2, x - 1:x + 2] = 30000
# One isolated bright point to locate.
DOT = (int(cx + RW * 0.15), int(cy - RH * 0.05))            # (x, y), scene-relative
mark[DOT[1] - 2:DOT[1] + 3, DOT[0] - 2:DOT[0] + 3] = 40000
mark = np.clip(mark, 0, 65535).astype(np.uint16)

PLANES = {0: dapi, 1: mark}


def reader(idx):
    def read(x, y, w, h):
        return PLANES[idx][y - RY:y - RY + h, x - RX:x - RX + w]
    return read


def overview8(plane, lo, hi):
    """What 01 writes: block-mean to 5.2 um/px, then a clipped 8-bit range.

    Each block is centred on native K*j, the convention 05a's map assumes of
    the overview and that `05a --verify-czi` confirms pylibCZIrw's zoom keeps.
    A plain block mean would centre it on K*j + 3.5 and put this synthetic
    overview, not the code under test, half an overview pixel off.
    """
    shifted = np.roll(np.roll(plane, K // 2, axis=0), K // 2, axis=1)
    ov = shifted.reshape(OH, K, OW, K).mean(axis=(1, 3))
    return np.clip((ov - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


ODIR = os.path.join(STUDY.out_root, "overviews", "LS1", "AF568")
os.makedirs(ODIR, exist_ok=True)
UID = "LS1_1a_s00"
DAPI_PNG = os.path.join(ODIR, UID + "_DAPI.png")
MARK_PNG = os.path.join(ODIR, UID + "_MARK.png")
Image.fromarray(overview8(dapi, 300, 16000)).save(DAPI_PNG)
Image.fromarray(overview8(mark, 150, 5000)).save(MARK_PNG)

RECT = (RX, RY, RW, RH)
rep = {}
g256 = RF.reformat(DAPI_PNG, light_background=False, report=rep)
assert g256 is not None, "the synthetic section must give 04a a tissue mask"
grey256 = g256[0]
wmask, _ = O.work_mask(DAPI_PNG)

print("--- the size and the sampling ---")
M = O.czi_frame(rep, RECT, OV_UM, NATIVE)
side13 = O.render_side(M, NATIVE, 1.3)
s = np.linalg.svd(M[:, :2], compute_uv=False)
chk("no axis is sampled coarser than asked",
    s[0] * 256 / side13 * NATIVE <= 1.3 + 1e-9, f"{s[0] * 256 / side13 * NATIVE:.3f} um")
chk("native asks for one native pixel per output pixel",
    abs(s[0] * 256 / O.render_side(M, NATIVE, NATIVE) * NATIVE - NATIVE) < 0.01)
chk("bin factor is whole native pixels", (O.bin_factor(NATIVE, 1.3), O.bin_factor(NATIVE, 0.65),
                                          O.bin_factor(NATIVE, 2.6)) == (2, 1, 4))

d8, m8 = O.render_from_czi(reader(0), reader(1), rep, RECT, OV_UM, NATIVE, 1.3, wmask)
chk("the render is square at the computed side", d8.shape == (side13, side13) == m8.shape,
    d8.shape)

print()
print("--- the frame is 05a's frame ---")
# The brightest marker pixel is the isolated dot. Map that output pixel back
# through 05a's matrix: it has to land on the dot's slide position.
U, V = O.output_grid(side13)
X, Y = GEO.apply_affine(M, U, V)
raw = O.sample_plane(reader(1), X, Y, RECT, 2)


def centroid(img, floor):
    w = np.where(img > floor, img.astype(float) - floor, 0.0)
    ys, xs = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    return (xs * w).sum() / w.sum(), (ys * w).sum() / w.sum()


# The same dot found two independent ways. 04a's own route: an overview in
# which ONLY the dot shows (the pair is dimmer and falls below the range),
# carried through reformat() at S=1 - the picture 05a's --verify checks its
# matrix against. And this render, converted to canonical units.
DOT_PNG = os.path.join(ODIR, UID + "_DOT.png")
Image.fromarray(overview8(mark, 2000, 3000)).save(DOT_PNG)
dot256 = RF.reformat(DAPI_PNG, light_background=False, companion=DOT_PNG)[5]
u4, v4 = centroid(dot256, 0.3 * dot256.max())
ci, cj = centroid(raw, 35000)
uc, vc = ((ci + 0.5) * 256 / side13 - 0.5, (cj + 0.5) * 256 / side13 - 0.5)
err = float(np.hypot(uc - u4, vc - v4))
chk("a slide feature lands where 04a's frame has it", err < 0.5,
    f"{err:.2f} canonical px")
gx, gy = GEO.apply_affine(M, uc, vc)
err = float(np.hypot(gx - (RX + DOT[0]), gy - (RY + DOT[1])))
chk("...and 05a's matrix takes it back to the slide", err < 4.0,
    f"{err:.2f} native px")

from scipy import ndimage  # noqa: E402


def low(img):
    """The section's large-scale structure - what both routes must agree on."""
    return ndimage.gaussian_filter(img.astype(float), 3)


small = np.asarray(Image.fromarray(d8).resize((256, 256), Image.LANCZOS))
g = low(grey256)
r = float(np.corrcoef(low(small).ravel(), g.ravel())[0, 1])
chk("shrunk to 256 it is 04a's greyscale, same frame", r > 0.95, f"r={r:.3f}")
r2 = float(np.corrcoef(low(np.roll(small, 4, axis=1)).ravel(), g.ravel())[0, 1])
chk("...and a 4 px shift of it is measurably worse", r2 < r - 0.01, f"r={r2:.3f}")
mine = O.frame_masks(rep, *O.output_grid(256), wmask)[0]
theirs = g256[1]
iou = (mine & theirs).sum() / max((mine | theirs).sum(), 1)
chk("the tissue mask is 04a's, on 04a's grid", iou > 0.98, f"IoU={iou:.3f}")

print()
print("--- the detail is there ---")


def resolved(profile):
    """True when the profile dips below 70% of the lower peak between the two
    objects. The profile runs from t=-0.5 to t=1.5 with the objects at 0 and 1,
    so each half holds one of them."""
    p = profile.astype(float)
    mid = len(p) // 2
    i = int(np.argmax(p[:mid]))
    j = mid + int(np.argmax(p[mid:]))
    return p[i:j + 1].min() < 0.7 * min(p[i], p[j])


def pair_profile(img, side):
    u, v = GEO.apply_affine(GEO.invert_affine(M), np.array([RX + x for x in PAIR_X]),
                            np.array([RY + PAIR_Y] * 2))
    k = side / 256.0
    cols = ((u + 0.5) * k - 0.5)
    rows = ((v + 0.5) * k - 0.5)
    n = 40
    t = np.linspace(-0.5, 1.5, n)
    xs = cols[0] + t * (cols[1] - cols[0])
    ys = rows[0] + t * (rows[1] - rows[0])
    return ndimage.map_coordinates(img.astype(float), [ys, xs], order=1)


chk("two objects 4.5 um apart are two at 1.3 um/px", resolved(pair_profile(raw, side13)))
old = RF.reformat(DAPI_PNG, light_background=False, companion=MARK_PNG, render_scale=3)
chk("...and one blob on the overview route, at any output size",
    not resolved(pair_profile(old[5], old[5].shape[0])))

print()
print("--- tile field, scene edge, masks ---")
field = np.full((153, 153), 2.0)
halved = O.sample_plane(reader(1), X, Y, RECT, 2, field)
inside = raw > 0
chk("a gain of 2 halves every sample, as 01 divides it out",
    np.allclose(halved[inside], raw[inside] / 2, rtol=1e-4))
Xo = np.array([[RX - 50.0, RX + 10.0]])
Yo = np.array([[RY + 10.0, RY + 10.0]])
edge = O.sample_plane(reader(0), Xo, Yo, RECT, 1)
chk("outside the scene rectangle is 0, as 01 writes it", edge[0, 0] == 0 and edge[0, 1] > 0,
    edge.tolist())
chk("nothing outside 04a's tissue mask is drawn",
    bool((d8[~O.frame_masks(rep, U, V, wmask)[0]] == 0).all()))
art = np.zeros((OH, OW), bool)
art[60:90, 100:140] = True
d8a, _ = O.render_from_czi(reader(0), reader(1), rep, RECT, OV_UM, NATIVE, 1.3, wmask, art)
_, artm = O.frame_masks(rep, U, V, wmask, art)
chk("04g's artifact is blanked in the render", artm.any() and bool((d8a[artm] == 0).all()),
    int(artm.sum()))

print()
print("--- the stage, end to end, against a stand-in pylibCZIrw ---")


class _R:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class _Doc:
    scenes_bounding_rectangle = {0: _R(RX, RY, RW, RH)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, roi, plane, scene, zoom):
        assert scene == 0 and zoom == 1.0, (scene, zoom)
        x, y, w, h = roi
        return PLANES[plane["C"]][y - RY:y - RY + h, x - RX:x - RX + w][:, :, None]


fake = types.ModuleType("pylibCZIrw")
fake.czi = types.SimpleNamespace(open_czi=lambda path: _Doc())
sys.modules["pylibCZIrw"] = fake
sys.modules["pylibCZIrw.czi"] = fake.czi

open(os.path.join(STUDY.source_dir, "LS1_1a.czi"), "wb").close()
os.makedirs(os.path.join(STUDY.out_root, "manifest"), exist_ok=True)
with open(O.MANIFEST_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["scene_uid", "file", "scene_index"])
    w.writerow([UID, "LS1_1a.czi", 0])
os.makedirs(os.path.dirname(O.FOCUS_CSV), exist_ok=True)
with open(O.FOCUS_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["scene_uid", "animal", "marker_channel", "um_px"])
    w.writerow([UID, "LS1", "AF568", "5.20"])
paths = RF.marker_paths("AF568")
os.makedirs(os.path.dirname(paths["index"]), exist_ok=True)
with open(paths["index"], "w", newline="", encoding="utf-8") as fh:
    csv.writer(fh).writerow(["id", "kind"])
os.makedirs(paths["sections"] + "_rgb_thumb", exist_ok=True)

sys.argv = ["04o", "--marker", "AF568", "--include-excluded", "--um-px", "1.3"]
O.main()
out = os.path.join(paths["sections"] + "_rgb", UID + ".png")
chk("the stage wrote the composite", os.path.exists(out))
if os.path.exists(out):
    im = np.asarray(Image.open(out))
    chk("...at the 1.3 um/px side, in colour", im.shape == (side13, side13, 3), im.shape)
    chk("...its blue plane is the DAPI render", np.array_equal(im[..., 2], d8))
    th = os.path.join(paths["sections"] + "_rgb_thumb", UID + ".png")
    chk("...and the existing thumbnail set got its shrunk copy",
        os.path.exists(th) and Image.open(th).size == (256, 256))
log = paths["sections"] + "_rgb_render.csv"
rows = list(csv.DictReader(open(log, encoding="utf-8"))) if os.path.exists(log) else []
chk("the render log says which picture this is",
    len(rows) == 1 and rows[0]["source"] == "czi" and rows[0]["um_per_px"] == "1.3"
    and rows[0]["reader"] == "pylibCZIrw", rows)

sys.argv = ["04o", "--marker", "AF568", "--include-excluded", "--source", "overview",
            "--force"]
O.main()
chk("--source overview still builds the 768 composite",
    Image.open(out).size == (768, 768), Image.open(out).size)

print()
print("FAILED: %d" % fails if fails else "all passed")
sys.exit(1 if fails else 0)
