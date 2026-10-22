"""ls_overlay: the positivity rule, colours and nucleus drawing.

Run:  python tests/test_overlay_draw.py
"""

import importlib.util
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

_spec = importlib.util.spec_from_file_location("lsov", os.path.join(SCRIPTS, "ls_overlay.py"))
LO = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LO)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(54) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


chk("10 > 9.9 positive", LO.is_positive(10, False, 9.9), True)
chk("9.9 vs 9.9 not positive", LO.is_positive(9.9, False, 9.9), False)
chk("censored always positive", LO.is_positive(0, True, 1e9), True)


def row(med, cen=0, art=0, off=0):
    return {"marker_median": str(med), "censored": str(cen),
            "artifact": str(art), "off_tissue": str(off)}


chk("artifact beats off_tissue", LO.kind_of(row(50, art=1, off=1), 40), "artifact")
chk("off_tissue", LO.kind_of(row(50, off=1), 40), "off_tissue")
chk("censored", LO.kind_of(row(1, cen=1), 40), "censored")
chk("above cut positive", LO.kind_of(row(50), 40), "positive")
chk("below cut negative", LO.kind_of(row(30), 40), "negative")
chk("None is outside", LO.kind_of(None, 40), "outside")
chk("no cut, counted -> negative", LO.kind_of(row(50), None), "negative")
chk("old csv without off_tissue column",
    LO.kind_of({"marker_median": "5", "censored": "0", "artifact": "0"}, 4), "positive")

chk("artifact beats censored", LO.kind_of(row(1, cen=1, art=1), 40), "artifact")
try:
    LO.kind_of({**row(""), "scene_uid": "S1"}, 40)
    got = "no error"
except ValueError as ex:
    got = "S1" in str(ex)
chk("empty median raises ValueError naming scene", got, True)
chk("empty median, flagged row, no error", LO.kind_of(row("", cen=1), 40), "censored")

lab = np.zeros((20, 20), np.int32)
lab[2:8, 2:8] = 1
lab[10:16, 10:16] = 2
rgb = np.full((20, 20, 3), 100, np.uint8)
LO.paint(rgb, lab, {1: "positive", 2: "negative"})
e = LO.edges(lab)
chk("shape/dtype", (rgb.shape, rgb.dtype.name), ((20, 20, 3), "uint8"))
fill = tuple(int(x) for x in np.rint(0.6 * 100 + 0.4 * np.array([255, 0, 255])))
chk("pos edge colour", {tuple(p) for p in rgb[e & (lab == 1)]}, {(255, 0, 255)})
chk("pos interior fill", {tuple(p) for p in rgb[(lab == 1) & ~e]}, {fill})
chk("neg edge colour", {tuple(p) for p in rgb[e & (lab == 2)]}, {(0, 255, 255)})
chk("neg interior unchanged", {tuple(p) for p in rgb[(lab == 2) & ~e]}, {(100, 100, 100)})
chk("background unchanged", {tuple(p) for p in rgb[lab == 0]}, {(100, 100, 100)})
chk("6x6 square has 20 edge px", int((e & (lab == 1)).sum()), 20)

rgb = np.full((20, 20, 3), 100, np.uint8)
LO.paint(rgb, lab, {1: "censored"})
chk("censored edge white", {tuple(p) for p in rgb[LO.edges(lab) & (lab == 1)]}, {(255, 255, 255)})
chk("censored fill as positive", {tuple(p) for p in rgb[(lab == 1) & ~LO.edges(lab)]}, {fill})

import time

big = np.zeros((2000, 2000), np.int32)
n = 0
for r0 in range(0, 2000, 44):
    for c0 in range(0, 2000, 44):
        n += 1
        big[r0:r0 + 40, c0:c0 + 40] = n
bkinds = {i: LO.KINDS[i % len(LO.KINDS)] for i in range(1, n + 1)}
bimg = np.full((2000, 2000, 3), 100, np.uint8)
t0 = time.time()
LO.paint(bimg, big, bkinds)
dt = time.time() - t0
chk(f"paint 2000x2000, {n} labels under 2 s ({dt:.2f}s)", dt < 2.0, True)

g = LO.grey(np.array([[0.0, 10.0, 20.0, 30.0]]), 10.0, 20.0)
chk("grey maps lo/hi and clips", g.tolist(), [[0, 0, 255, 255]])
chk("grey dtype", g.dtype.name, "uint8")
chk("stretch p1/p99.8", LO.stretch(np.arange(1001)), (10.0, 998.0))

H = W = 41
inside = lambda xs, ys: (xs - 20) ** 2 + (ys - 20) ** 2 <= 15 ** 2
ol = LO.shape_mask_outline(H, W, inside)
yy, xx = np.mgrid[0:H, 0:W]
mask = inside(xx.astype(float), yy.astype(float))
chk("outline non-empty", bool(ol.any()), True)
chk("outline inside mask", bool((ol & ~mask).any()), False)
chk("deep interior not outline", bool(ol[20, 20] or ol[15:26, 15:26].any()), False)

# --- text strip, file names, streaming PNG --------------------------------
import tempfile

chk("name roi", LO.overlay_name("pERK", "LS22_s01a_sc06", "roi", 3, "Dm"),
    "pERK_LS22_s01a_sc06_roi03_Dm.png")
chk("name roi, region sanitised",
    LO.overlay_name("pERK", "LS22_s01a_sc06", "roi", 3, "OB/Tel x"),
    "pERK_LS22_s01a_sc06_roi03_OB_Tel_x.png")
chk("name background", LO.overlay_name("pERK", "LS22_s01a_sc06", "background", 1),
    "pERK_LS22_s01a_sc06_bg01.png")
chk("name scene", LO.overlay_name("pERK", "LS22_s01a_sc06", "scene"),
    "pERK_LS22_s01a_sc06_scene.png")
chk("overlay_dir", LO.overlay_dir("/o", "pERK", "LS22"),
    os.path.join("/o", "detection_overlays", "pERK", "LS22"))


def max_white_runs(img):
    runs = set()
    white = (img == 255).all(axis=2)
    for r in white:
        n = 0
        for v in r:
            if v:
                n += 1
            elif n:
                runs.add(n)
                n = 0
        if n:
            runs.add(n)
    return runs


st = LO.strip(800, ["a", "b"], True, 0.65, 50)
chk("strip width", st.shape[1], 800)
chk("strip height > 0", st.shape[0] > 0, True)
chk("strip dtype/channels", (st.dtype.name, st.shape[2]), ("uint8", 3))
chk("strip scale bar run of 77", 77 in max_white_runs(st), True)
flat = {tuple(p) for p in st.reshape(-1, 3)}
chk("strip has every kind colour", all(c in flat for c in LO.COLOURS.values()), True)
LO.strip(800, ["scale 0.65 \u00b5m/px"], False, 0.65, 50)
chk("strip with micro sign, no legend ok", True, True)

long_line = " | ".join(f"field{i}: value {i}" for i in range(40))
narrow = LO.strip(300, [long_line], True, 0.65, 50)
chk("long line wraps to taller strip", narrow.shape[0] > LO.strip(300, ["x"], True, 0.65, 50).shape[0], True)
chk("narrow strip width kept", narrow.shape[1], 300)

# A bar is never drawn at a length its label does not say: when bar_um (and
# its label) does not fit, the largest of 500/200/100/50/20/10 um that does
# is drawn and labelled; when none fits there is no bar at all.
chk("fit_bar: 50 um fits in 786 px", LO.fit_bar(50, 0.65, 786, lambda t: 37), (50, 77))
chk("fit_bar: 50 um does not fit in 106 px -> 20 um",
    LO.fit_bar(50, 0.65, 106, lambda t: 37, gap=14), (20, 31))
chk("fit_bar: 500 um in 386 px -> 200 um",
    LO.fit_bar(500, 0.65, 386, lambda t: 45, gap=14), (200, 308))
chk("fit_bar: nothing fits in 46 px -> None",
    LO.fit_bar(50, 0.65, 46, lambda t: 37, gap=14), None)
mid = LO.strip(120, [], False, 0.65, 50)
chk("strip 120 px, 50 um asked: a 20 um bar (31 px), not a cut 50 um one",
    (31 in max_white_runs(mid), 77 in max_white_runs(mid), max(max_white_runs(mid))),
    (True, False, 31))
tiny = LO.strip(60, [], False, 0.65, 50)
chk("strip 60 px: no bar fits, none drawn (all black)", int(tiny.max()), 0)
chk("no cut, a censored nucleus is not called: negative",
    LO.kind_of(row(1, cen=1), None), "negative")

with tempfile.TemporaryDirectory() as td:
    from PIL import Image
    rng = np.random.default_rng(0)
    im = rng.integers(0, 256, (37, 53, 3), dtype=np.uint8)
    path = os.path.join(td, "a.png")
    pw = LO.PngRows(path, 53, 37)
    for r0 in range(0, 37, 10):
        pw.write(im[r0:r0 + 10])
    pw.close()
    chk("PngRows round-trip", bool((np.asarray(Image.open(path)) == im).all()), True)
    chk("PngRows no .part left", os.path.exists(path + ".part"), False)

    path = os.path.join(td, "b.png")
    pw = LO.PngRows(path, 53, 37)
    pw.write(im[:10])
    try:
        pw.close()
        got = "no error"
    except ValueError:
        got = "ValueError"
    chk("PngRows short close raises", got, "ValueError")
    chk("PngRows short: no png, no part",
        (os.path.exists(path), os.path.exists(path + ".part")), (False, False))

    path = os.path.join(td, "c.png")
    try:
        with LO.PngRows(path, 53, 37) as pw:
            pw.write(im[:5])
            raise RuntimeError("boom")
    except RuntimeError:
        got = "RuntimeError"
    chk("PngRows with: exception propagates, part removed",
        (got, os.path.exists(path), os.path.exists(path + ".part")),
        ("RuntimeError", False, False))

    path = os.path.join(td, "d.png")
    with LO.PngRows(path, 53, 37) as pw:
        pw.write(im)
    chk("PngRows with: ok", os.path.exists(path), True)

    pw = LO.PngRows(os.path.join(td, "e.png"), 53, 37)
    bad = []
    for blk in (im.astype(np.int32)[:2], im[:2, :52], im[0]):
        try:
            pw.write(blk)
            bad.append("accepted")
        except ValueError:
            bad.append("ValueError")
    chk("PngRows rejects bad blocks", bad, ["ValueError"] * 3)
    try:
        pw.write(np.zeros((38, 53, 3), np.uint8))
        got = "accepted"
    except ValueError:
        got = "ValueError"
    chk("PngRows rejects too many rows", got, "ValueError")
    pw.abort()
    chk("abort leaves nothing", os.listdir(td).count("e.png.part"), 0)

    # A write that fails while closing (a full disk): the handle is closed,
    # the .part deleted, the error re-raised; a second close does nothing.
    path = os.path.join(td, "g.png")
    pw = LO.PngRows(path, 53, 37)
    pw.write(im)
    real = pw._f

    class FullDisk:
        @property
        def closed(self):
            return real.closed

        def write(self, b):
            raise OSError(28, "No space left on device")

        def close(self):
            real.close()

    pw._f = FullDisk()
    try:
        pw.close()
        got = "no error"
    except OSError as exc:
        got = exc.errno
    chk("PngRows failed close: re-raised, handle closed, no part, no png",
        (got, real.closed, os.path.exists(path + ".part"), os.path.exists(path)),
        (28, True, False, False))
    try:
        pw.close()
        got = "no-op"
    except Exception as exc:  # noqa: BLE001
        got = repr(exc)
    chk("PngRows: a second close is a no-op", got, "no-op")

    big = rng.integers(0, 256, (600, 700, 3), dtype=np.uint8)  # > 1 MiB raw, random
    path = os.path.join(td, "f.png")
    with LO.PngRows(path, 700, 600) as pw:
        for r0 in range(0, 600, 64):
            pw.write(big[r0:r0 + 64])
    chk("PngRows multi-IDAT round-trip", bool((np.asarray(Image.open(path)) == big).all()), True)

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
