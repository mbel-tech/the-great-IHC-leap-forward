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

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
