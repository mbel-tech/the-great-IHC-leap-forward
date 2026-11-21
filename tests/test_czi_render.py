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

import contextlib
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

tis = d8 > 0
for name, img in (("DAPI", d8), ("marker", m8)):
    sat = float((img[tis] == 255).mean())
    # 0.1% at or above the top, plus the half-step that rounds up to 255.
    chk(f"about 0.1% of the tissue saturates, no more ({name})", sat <= 0.002, f"{sat:.4%}")
old_sat = float((old[0][old[0] > 0] == 255).mean())
chk("...where 04a's rule alone would saturate the nuclei",
    old_sat > float((d8[tis] == 255).mean()), f"{old_sat:.2%} on the 768 route")
# A few very bright objects must not darken the whole section. Tissue around
# 1000 with 0.5% debris at 8000: the 99.9th percentile is the debris, and a
# linear map from there would put the tissue at the bottom of the range.
rng = np.random.default_rng(3)
vals = rng.normal(1000, 100, (400, 400)).astype(np.float32)
vals.flat[rng.choice(vals.size, vals.size // 200, replace=False)] = 8000
alltis = np.ones(vals.shape, bool)
out = O.stretch(vals.copy(), alltis)
chk("debris does not darken the tissue: its median sits where 04a puts it",
    abs(float(np.median(out)) - 0.2 * 255) <= 3, float(np.median(out)))
chk("...and still nothing saturates beyond the cap",
    float((out == 255).mean()) <= 0.006, f"{float((out == 255).mean()):.3%}")
plain = rng.normal(1000, 100, (400, 400)).astype(np.float32)
med = float(np.median(plain))
mad = float(np.median(np.abs(plain - med))) * 1.4826
want = np.rint(np.clip((plain - (med - mad)) / (5 * mad), 0, 1) * 255)
chk("where 04a's top is already high enough, the picture is exactly 04a's",
    O.display_gamma(0.2) == 1.0 and np.abs(O.stretch(plain.copy(), alltis) - want).max() <= 1)
chk("the render is drawn in strips, and they join",
    side13 > O.STRIP_ROWS and not (d8[O.STRIP_ROWS - 1:O.STRIP_ROWS + 1].sum(axis=1) == 0).any(), "")

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


# The drive-dropout checks below: DROP["at"] names the (plane, nth read) read
# that raises once, with the slides folder renamed away for 0.3 s; BREAK["at"]
# one that raises with the folder there. A drop also ends every handle opened
# before it (GEN), as a vanished volume does, so a redo that kept using an old
# handle fails instead of passing by luck.
DROP = {"at": None, "thread": None}
BREAK = {"at": None}
GEN = {"n": 0}
COUNT = {}


class _Doc:
    scenes_bounding_rectangle = {0: _R(RX, RY, RW, RH)}

    def __init__(self):
        self.gen = GEN["n"]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, roi, plane, scene, zoom):
        assert scene == 0 and zoom == 1.0, (scene, zoom)
        x, y, w, h = roi
        COUNT[plane["C"]] = COUNT.get(plane["C"], 0) + 1
        key = (plane["C"], COUNT[plane["C"]])
        if BREAK["at"] == key:
            raise RuntimeError("corrupt subblock")
        if self.gen != GEN["n"]:
            raise OSError(5, "Input/output error (a handle from before the drop)")
        if DROP["at"] == key:
            DROP["at"] = None
            GEN["n"] += 1
            DROP["thread"] = vanish_for(0.3)
            raise OSError(5, "Input/output error")
        return PLANES[plane["C"]][y - RY:y - RY + h, x - RX:x - RX + w][:, :, None]


def vanish_for(seconds):
    """Rename the slides folder away; a thread puts it back after `seconds`."""
    import threading
    import time
    gone = STUDY.source_dir + ".gone"
    os.rename(STUDY.source_dir, gone)

    def back():
        time.sleep(seconds)
        os.rename(gone, STUDY.source_dir)
    t = threading.Thread(target=back, daemon=True)
    t.start()
    return t


# pylibCZIrw's open_czi is a generator-based context manager: the DOCUMENT is
# what entering it returns, and the manager itself has no
# scenes_bounding_rectangle. Modelled that way, because a stand-in that was its
# own document hid exactly that bug until a real CZI found it.
@contextlib.contextmanager
def _open_czi(path):
    yield _Doc()


fake = types.ModuleType("pylibCZIrw")
fake.czi = types.SimpleNamespace(open_czi=_open_czi)
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

import shutil  # noqa: E402
tdir = paths["sections"] + "_rgb_thumb"
shutil.rmtree(tdir)
sys.argv = ["04o", "--marker", "AF568", "--include-excluded", "--thumbs", "--force",
            "--um-px", "2.6"]
O.main()
th = os.path.join(tdir, UID + ".png")
chk("one run with --thumbs builds the composite AND its thumbnail",
    os.path.exists(th) and Image.open(th).size == (256, 256)
    and Image.open(out).size[0] == O.render_side(M, NATIVE, 2.6))

# Memory: the native rectangle is read in bands, not in one piece.
calls = []
def counting(x, y, w, h):
    calls.append(h)
    return reader(1)(x, y, w, h)
whole = O.read_region(reader(1), (RX, RY, RX + RW - 1, RY + RH - 1), (RX, RY, RW, RH), 4,
                      band_rows=10 ** 9)
banded = O.read_region(counting, (RX, RY, RX + RW - 1, RY + RH - 1), (RX, RY, RW, RH), 4,
                       band_rows=256)
chk("a banded read gives exactly the single read's pixels",
    np.array_equal(whole[0], banded[0]) and whole[1:] == banded[1:])
chk("...fetching no more than one band of native rows at a time",
    len(calls) > 1 and max(calls) <= 256, (len(calls), max(calls)))

sys.argv = ["04o", "--marker", "AF568", "--include-excluded", "--force",
            "--um-px", "0.65"]
try:
    O.main()
    refused = False
except SystemExit as e:
    refused = "1.3" in str(e)
chk("a whole-section render finer than 1.3 um/px is refused, with the reason",
    refused)

print()
print("--- the drive drops while a section is drawn: redone, same bytes ---")
import glob  # noqa: E402
import io  # noqa: E402

DR = O.IO.drive_module()
chk("ls_drive loads", DR is not None)
DR.POLL = 0.05
os.environ["LS_WATCH"] = STUDY.source_dir
os.environ["LS_PROGRESS"] = "1"
DROP_ARGV = ["04o", "--marker", "AF568", "--include-excluded", "--force",
             "--um-px", "2.6"]
th = os.path.join(paths["sections"] + "_rgb_thumb", UID + ".png")


def run_o(argv):
    """(main()'s return, what it printed), with the render log started afresh
    (it appends, so a second run would otherwise differ by its rows)."""
    if os.path.exists(log):
        os.remove(log)
    COUNT.clear()
    saved = sys.argv
    sys.argv = list(argv)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = O.main()
    finally:
        sys.argv = saved
    return rc, buf.getvalue()


def o_snapshot():
    got = {}
    for p in (out, th, log):
        if os.path.exists(p):
            with open(p, "rb") as fh:
                got[os.path.basename(os.path.dirname(p)) + "/" + os.path.basename(p)] = fh.read()
    return got


def settle():
    if DROP["thread"] is not None:
        DROP["thread"].join(5)
        DROP["thread"] = None


rc, text = run_o(DROP_ARGV)
chk("uninterrupted render", rc is None and "@@drive" not in text, (rc, text[-200:]))
base_o = o_snapshot()
chk("...wrote the composite, its thumbnail and the log", len(base_o), 3)
# The marker plane's last band: part way through the section, the counterstain
# already read through the same handles.
chk("...in more than one band", COUNT.get(1, 0) > 1, dict(COUNT))
DROP["at"] = (1, COUNT.get(1, 1))
for p in (out, th):
    os.remove(p)
rc, text = run_o(DROP_ARGV)
settle()
chk("the drop fired", DROP["at"] is None, DROP["at"])
chk("04o with the drive dropped finishes", rc is None, (rc, text[-300:]))
chk("...says the drive came back, once", text.count("@@drive back") == 1,
    text.count("@@drive back"))
got = o_snapshot()
chk("...writes the same files", sorted(got) == sorted(base_o), sorted(got))
chk("...byte for byte (the file was reopened, not the old handle reused)",
    [k for k in base_o if got.get(k) != base_o[k]] == [],
    [k for k in base_o if got.get(k) != base_o[k]])
rows = list(csv.DictReader(open(log, encoding="utf-8"))) if os.path.exists(log) else []
chk("...one render log row, not one per attempt", len(rows) == 1, len(rows))
left = glob.glob(os.path.join(STUDY.out_root, "**", "*.tmp"), recursive=True)
chk("...no .tmp left", left == [], left)

print()
print("--- the slides folder is gone when the CZI is looked for ---")
os.remove(out)
t = vanish_for(0.3)
GEN["n"] += 1
rc, text = run_o(DROP_ARGV)
t.join(5)
chk("waits, then renders", rc is None, (rc, text[-300:]))
chk("...says the drive came back, once", text.count("@@drive back") == 1,
    text.count("@@drive back"))
got = o_snapshot()
chk("...byte for byte", [k for k in base_o if got.get(k) != base_o[k]] == [],
    [k for k in base_o if got.get(k) != base_o[k]])

print()
print("--- a wait between two sections of one CZI: the next opens it again ---")
# An ls_io write waits for a dropped drive by itself and then succeeds, so no
# render fails - but the CZI handles kept open for the next section of the same
# file are to the volume that went away (GEN), and must not be reused.
czi_src = O.CziSource("AF568")
frow = {"scene_uid": UID, "animal": "LS1", "marker_channel": "AF568", "um_px": "5.20"}
first = czi_src.render(UID, frow, DAPI_PNG, 0.0, False, None, None, None, 2.6)
t = vanish_for(0.3)
GEN["n"] += 1
with contextlib.redirect_stdout(io.StringIO()):
    DR.wait_for_drive("an atomic write")
t.join(5)
try:
    again = czi_src.render(UID, frow, DAPI_PNG, 0.0, False, None, None, None, 2.6)
    err = None
except OSError as exc:
    again, err = None, exc
czi_src.close(os.path.join(STUDY.out_root, "scratch_rgb"))
chk("the section after the wait renders, from the file opened again", err is None, err)
chk("...the same pixels", again is not None and all(
    np.array_equal(x, y) for x, y in zip(first, again)))

print()
print("--- a wait absorbed by the composite's write: the section is redone ---")
real_thumb = O.refresh_thumb
ABSORB = {"on": True}


def absorbing_thumb(rgb_dir, uid, size):
    # The composite is written; its thumbnail's write waits out a drop and then
    # succeeds. Nothing fails here, so only "the drive was lost during this
    # render" says the render must be done again.
    if ABSORB["on"]:
        ABSORB["on"] = False
        tt = vanish_for(0.3)
        GEN["n"] += 1
        DR.wait_for_drive("a thumbnail write")
        tt.join(5)
    real_thumb(rgb_dir, uid, size)


O.refresh_thumb = absorbing_thumb
calls_before = dict(COUNT)
rc, text = run_o(DROP_ARGV)
O.refresh_thumb = real_thumb
chk("the absorbed wait happened", ABSORB["on"] is False)
chk("finishes", rc is None, (rc, text[-300:]))
chk("...rendered twice: the section was redone", COUNT.get(1, 0) == 4, dict(COUNT))
got = o_snapshot()
chk("...byte for byte", [k for k in base_o if got.get(k) != base_o[k]] == [],
    [k for k in base_o if got.get(k) != base_o[k]])

print()
print("--- a read that fails with the folder there: as before ---")
BREAK["at"] = (1, 1)
raised = None
try:
    run_o(DROP_ARGV)
except RuntimeError as exc:
    raised = exc
BREAK["at"] = None
chk("the error stops the stage, as it always has", isinstance(raised, RuntimeError), raised)
czi_path = os.path.join(STUDY.source_dir, "LS1_1a.czi")
os.rename(czi_path, czi_path + ".away")
rc, text = run_o(DROP_ARGV)
os.rename(czi_path + ".away", czi_path)
# An excluded section here, so not built is said, not failed - as before.
chk("a CZI missing with the folder there is not built, at once",
    rc is None and "CZI not found" in text and "@@drive" not in text, (rc, text[-300:]))
os.environ.pop("LS_WATCH")
os.environ.pop("LS_PROGRESS")

print()
print("FAILED: %d" % fails if fails else "all passed")
sys.exit(1 if fails else 0)
