"""`01l` drawing every scene of every slide from the CZI, for viewing.

What can go wrong is quiet: a picture of the wrong scene looks as plausible as
the right one, a downsample that skips pixels looks sharp and drops nuclei, and
a picture that is merely drawn at the wrong brightness still looks like tissue.
So this runs the stage end to end against a stand-in pylibCZIrw holding two
overlapping scenes with different content, and checks:

  * each picture is its own scene (the read names the scene);
  * 2.6 and 1.3 um/px give 4x4 and 2x2 block means of the native pixels, the
    same array however many bands it was read in;
  * the two folders hold the composite and the stain alone, with the
    counterstain in the blue plane;
  * the frozen range is 01's, and --range section stretches per scene;
  * existing pictures are kept unless --force, and finer than 1.3 is refused.

Run:  python tests/test_scene_renders.py
"""

import contextlib
import csv
import json
import os
import sys
import types

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

# The pictures go to a folder chosen in the study's settings, outside out_root,
# as an operator choosing "whichever folder" would.
PICS = tempfile.mkdtemp(prefix="scene_pics_")
atexit.register(shutil.rmtree, PICS, True)
STUDY = use_temp_study(acquisition={"layout": "paired",
                                    "markers": ["AF568", "AF488"]},
                       scene_render={"out_dir": PICS, "um_per_px": 2.6,
                                     "range": "dataset"})

fails = 0


def chk(label, ok, got=""):
    global fails
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(64) + " " + str(got))


# ---------------------------------------------------------------- a slide
# Two scenes whose rectangles overlap, as 2241 of 2572 do in this dataset.
# Scene 1 is bright where scene 0 is dim, so a read of the wrong one shows.
W, H = 412, 300                         # deliberately not multiples of 4
RECTS = {0: (1000, 2000, W, H), 1: (1200, 2100, W, H)}
rng = np.random.default_rng(3)
PLANES = {}
for s, level in ((0, 2000.0), (1, 12000.0)):
    dapi = rng.normal(level, 200, (H, W))
    mark = rng.normal(level / 4, 50, (H, W))
    PLANES[s] = {0: np.clip(dapi, 0, 65535).astype(np.uint16),
                 1: np.clip(mark, 0, 65535).astype(np.uint16)}
# Two bright marker dots 4 native px apart in scene 0 - one 2.6 um pixel each.
PLANES[0][1][100:104, 200:204] = 30000
PLANES[0][1][100:104, 208:212] = 30000


class _R:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


READS = []


class _Doc:
    scenes_bounding_rectangle = {k: _R(*v) for k, v in RECTS.items()}

    def read(self, roi, plane, scene, zoom):
        assert zoom == 1.0, zoom
        x, y, w, h = roi
        rx, ry = RECTS[scene][:2]
        READS.append((scene, h))
        return PLANES[scene][plane["C"]][y - ry:y - ry + h, x - rx:x - rx + w][:, :, None]


@contextlib.contextmanager
def _open_czi(path):
    yield _Doc()


fake = types.ModuleType("pylibCZIrw")
fake.czi = types.SimpleNamespace(open_czi=_open_czi)
sys.modules["pylibCZIrw"] = fake
sys.modules["pylibCZIrw.czi"] = fake.czi

L = load_stage("01l_scene_renders.py")

open(os.path.join(STUDY.source_dir, "LS1_1a.czi"), "wb").close()
os.makedirs(os.path.dirname(L.MANIFEST_CSV), exist_ok=True)
UIDS = ["LS1_1a_s00", "LS1_1a_s01"]
with open(L.MANIFEST_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["scene_uid", "file", "animal", "slide", "variant",
                "marker_channel", "scene_index", "section_order"])
    for i, uid in enumerate(UIDS):
        w.writerow([uid, "LS1_1a.czi", "LS1", "1", "a", "AF568", i, i])
os.makedirs(L.QC_DIR, exist_ok=True)
RANGES = {"DAPI": {"lo": 1000, "hi": 14000}, "AF568": {"lo": 200, "hi": 20000}}
with open(L.RANGES_PATH, "w", encoding="utf-8") as fh:
    json.dump(RANGES, fh)


def block_mean(a, b):
    h, w = a.shape[0] // b * b, a.shape[1] // b * b
    return a[:h, :w].astype(np.float64).reshape(h // b, b, w // b, b).mean(axis=(1, 3))


def expect8(a, lo, hi):
    return np.clip(np.rint((a - lo) * 255.0 / (hi - lo)), 0, 255).astype(np.uint8)


def run(*argv):
    sys.argv = ["01l"] + list(argv)
    L.main()


def pic(folder, uid, root=PICS):
    return os.path.join(root, "AF568", folder, uid + ".png")


print("--- the pictures, at 2.6 um/px ---")
run()
chk("the folder is the study's scene_render.out_dir", L.OUT_DIR == PICS, L.OUT_DIR)
chk("...and the index stays under out_root",
    L.INDEX_CSV == os.path.join(STUDY.out_root, "qc", "scene_renders.csv"))
chk("the counterstain folder is named after it", L.nuclear_name() == "DAPI")
for s, uid in enumerate(UIDS):
    p_rgb, p_only = pic("stain+DAPI", uid), pic("stain_only", uid)
    chk(f"scene {s}: both pictures written", os.path.exists(p_rgb) and os.path.exists(p_only))
    if not (os.path.exists(p_rgb) and os.path.exists(p_only)):
        continue
    rgb, only = np.asarray(Image.open(p_rgb)), np.asarray(Image.open(p_only))
    want_n = expect8(block_mean(PLANES[s][0], 4), 1000, 14000)
    want_m = expect8(block_mean(PLANES[s][1], 4), 200, 20000)
    chk(f"scene {s}: 4x4 native blocks, whole blocks only", only.shape == (H // 4, W // 4),
        only.shape)
    chk(f"scene {s}: stain-only is the marker, grey, dataset range",
        only.ndim == 2 and np.abs(only.astype(int) - want_m).max() <= 1)
    chk(f"scene {s}: blue plane is the counterstain",
        np.abs(rgb[..., 2].astype(int) - want_n).max() <= 1)
    chk(f"scene {s}: red plane is the marker (AF568 draws red)",
        np.array_equal(rgb[..., 0], only))

only0 = np.asarray(Image.open(pic("stain_only", UIDS[0])))
row = only0[25]
chk("two dots one 2.6 um pixel apart stay two", row[50] == 255 and row[52] == 255
    and row[51] < 100, row[49:54])

rows = {r["scene_uid"]: r for r in csv.DictReader(open(L.INDEX_CSV, encoding="utf-8"))}
chk("the index has a row per scene", sorted(rows) == UIDS, sorted(rows))
r0 = rows.get(UIDS[0], {})
chk("...saying how it was drawn",
    r0.get("um_per_px") == "2.60" and r0.get("block") == "4" and r0.get("range") == "dataset"
    and r0.get("reader") == "pylibCZIrw" and r0.get("stain_only", "").endswith("LS1_1a_s00.png"),
    r0)
chk("reads were banded", max(h for _, h in READS) <= L.BAND_ROWS)

print()
print("--- banding does not change the picture ---")
read = lambda x, y, w, h: PLANES[0][1][y - 2000:y - 2000 + h, x - 1000:x - 1000 + w]  # noqa: E731
L.BAND_ROWS = 8
banded = L.read_scene(read, RECTS[0], 2)
L.BAND_ROWS = 1024
whole = L.read_scene(read, RECTS[0], 2)
chk("8-row bands equal one read", np.array_equal(banded, whole))
chk("...and equal the 2x2 block mean", np.allclose(whole, block_mean(PLANES[0][1], 2)))
half = np.full((L.SR.PITCH_PX // L.SR.FIELD_DIVISOR,) * 2, 0.5, np.float32)
chk("the tile field divides out", np.allclose(L.apply_field(whole.copy(), half, 2),
                                             np.minimum(whole * 2, 65535)))

print()
print("--- kept, forced, and the finer settings ---")
READS.clear()
run()
chk("a second run reads nothing", READS == [], len(READS))
READS.clear()
run("--um-px", "1.3", "--range", "section")
chk("a new resolution redraws without --force", len(READS) > 0, len(READS))
only13 = np.asarray(Image.open(pic("stain_only", UIDS[1])))
chk("--um-px 1.3 draws 2x2 blocks", only13.shape == (H // 2, W // 2), only13.shape)
chk("--range section fills the range on its own scene",
    only13.min() <= 5 and only13.max() >= 250, (only13.min(), only13.max()))
rows = {r["scene_uid"]: r for r in csv.DictReader(open(L.INDEX_CSV, encoding="utf-8"))}
chk("...and the index says so", rows[UIDS[1]]["range"] == "section"
    and rows[UIDS[1]]["um_per_px"] == "1.30")
chk("rounding to whole native pixels", (L.block_for(2.6), L.block_for(1.3), L.block_for(2.0))
    == (4, 2, 3))
OTHER = os.path.join(STUDY.root, "elsewhere")
run("--out-dir", OTHER, "--limit", "1")
chk("--out-dir sends one run elsewhere", os.path.exists(pic("stain_only", UIDS[0], OTHER)))
run("--um-px", "0.65", "--force")
only065 = np.asarray(Image.open(pic("stain_only", UIDS[0])))
chk("--um-px 0.65 draws the native pixels", only065.shape == (H, W), only065.shape)
try:
    run("--um-px", "0.3")
    chk("finer than native is refused", False)
except SystemExit as exc:
    chk("finer than native is refused", "0.65" in str(exc))

print()
print("--- a scene that does not fit in memory is skipped, not fatal ---")
real = L.available_memory
L.available_memory = lambda: L.MEMORY_HEADROOM + 1000   # less than a scene
before = os.path.getmtime(pic("stain_only", UIDS[0]))
run("--um-px", "0.65", "--force")
chk("nothing drawn when it does not fit",
    os.path.getmtime(pic("stain_only", UIDS[0])) == before)
L.available_memory = lambda: None           # platform cannot say: draw anyway
chk("...and a platform that cannot say does not block", L.fits(10 ** 12) is None)
L.available_memory = real
chk("memory estimate scales with the area", L.memory_needed((0, 0, 4000, 4000), 1)
    > 5 * L.memory_needed((0, 0, 4000, 4000), 4))
chk("this machine reports its free memory", isinstance(real(), int))

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
