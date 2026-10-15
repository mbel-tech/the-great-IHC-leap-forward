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
  * existing pictures are kept unless --force, and finer than native is refused;
  * clipped pixels neither carry the tile field's pattern nor set the range,
    and a frozen range pinned at the clip ceiling is replaced per scene;
  * the index keeps one row per picture, so runs into other folders add rows.

Run:  python tests/test_scene_renders.py
"""

import contextlib
import csv
import io
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
ZOOMS = []


class _Doc:
    scenes_bounding_rectangle = {k: _R(*v) for k, v in RECTS.items()}

    def read(self, roi, plane, scene, zoom):
        x, y, w, h = roi
        rx, ry = RECTS[scene][:2]
        READS.append((scene, h))
        ZOOMS.append(zoom)
        native = PLANES[scene][plane["C"]][y - ry:y - ry + h, x - rx:x - rx + w]
        if zoom != 1.0:
            # Zeiss's stored copy: its own averaging, rounded to its own
            # integers - not the block mean the scan route computes, but near it.
            b = int(round(1 / zoom))
            hh, ww = native.shape[0] // b * b, native.shape[1] // b * b
            small = native[:hh, :ww].astype(np.float64).reshape(
                hh // b, b, ww // b, b).mean(axis=(1, 3))
            return (np.floor(small) + 7).astype(np.uint16)[:, :, None]
        return native[:, :, None]


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


def run_failing(*argv):
    """The SystemExit message of a run expected to end as a failure, else ''."""
    try:
        run(*argv)
    except SystemExit as exc:
        return str(exc.code)
    return ""


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

def index_rows(root=PICS):
    return {r["scene_uid"]: r for r in csv.DictReader(open(L.INDEX_CSV, encoding="utf-8"))
            if r["stain_only_png"].startswith(root)}


rows = index_rows()
chk("the index has a row per scene", sorted(rows) == UIDS, sorted(rows))
r0 = rows.get(UIDS[0], {})
chk("...saying how it was drawn",
    r0.get("um_per_px") == "2.60" and r0.get("block") == "4" and r0.get("range") == "dataset"
    and r0.get("reader") == "pylibCZIrw" and r0.get("stain_only_png", "").endswith("LS1_1a_s00.png")
    and r0.get("stain_counterstain_png", "").endswith("LS1_1a_s00.png")
    and r0.get("marker_range_from") == "dataset",
    r0)
chk("reads were banded", max(h for _, h in READS) <= L.BAND_ROWS)

print()
print("--- banding does not change the picture ---")
read = lambda x, y, w, h: PLANES[0][1][y - 2000:y - 2000 + h, x - 1000:x - 1000 + w]  # noqa: E731
L.BAND_ROWS = 8
banded = L.read_blocks(read, RECTS[0], 2)
L.BAND_ROWS = 1024
whole = L.read_blocks(read, RECTS[0], 2)
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
rows = index_rows()
chk("...and the index says so", rows[UIDS[1]]["range"] == "section"
    and rows[UIDS[1]]["um_per_px"] == "1.30")
chk("rounding to whole native pixels", (L.block_for(2.6), L.block_for(1.3), L.block_for(2.0))
    == (4, 2, 3))
OTHER = os.path.join(STUDY.root, "elsewhere")
run("--out-dir", OTHER, "--limit", "1")
chk("--out-dir sends one run elsewhere", os.path.exists(pic("stain_only", UIDS[0], OTHER)))
chk("...with its own index row, the first folder's rows kept",
    UIDS[0] in index_rows(OTHER) and sorted(index_rows()) == UIDS)
run("--um-px", "0.65", "--force")
only065 = np.asarray(Image.open(pic("stain_only", UIDS[0])))
chk("--um-px 0.65 draws the native pixels", only065.shape == (H, W), only065.shape)
try:
    run("--um-px", "0.3")
    chk("finer than native is refused", False)
except SystemExit as exc:
    chk("finer than native is refused", "0.65" in str(exc))

print()
print("--- Zeiss's preview copy, when chosen ---")
READS.clear(); ZOOMS.clear()
run()
chk("the default reads the scan itself", ZOOMS and set(ZOOMS) == {1.0}, set(ZOOMS))
chk("...and the index says so", index_rows()[UIDS[0]].get("source") == "scan")
ZOOMS.clear()
run("--source", "zeiss_preview")
chk("choosing the preview redraws without --force", len(ZOOMS) > 0, len(ZOOMS))
chk("...from the stored 1/4 copy at 2.6 um/px, one read per plane",
    ZOOMS == [0.25] * 4, ZOOMS)
r0 = index_rows()[UIDS[0]]
chk("...the index says where the pixels came from", r0.get("source") == "zeiss_preview", r0)
prev = np.asarray(Image.open(pic("stain_only", UIDS[0])))
chk("...same size as the scan route", prev.shape == (H // 4, W // 4), prev.shape)
want_m = expect8(block_mean(PLANES[0][1], 4), 200, 20000)
diff = np.abs(prev.astype(int) - want_m)
chk("...close to it, but its own pixels", 0 < diff.max() <= 3, diff.max())
ZOOMS.clear()
run("--source", "zeiss_preview")
chk("a second preview run reads nothing", ZOOMS == [], len(ZOOMS))
run("--source", "zeiss_preview", "--um-px", "0.65", "--limit", "1")
chk("at 0.65 there is no smaller copy: the scan is read",
    ZOOMS and set(ZOOMS) == {1.0}
    and index_rows()[UIDS[0]].get("source") == "scan", set(ZOOMS))
ZOOMS.clear()
run("--force")
chk("back to the scan route with the default", set(ZOOMS) == {1.0})

print()
print("--- clipping ---")
CEIL = 65535
flat = np.full((40, 40), 30000.0, np.float32)
flat[10:30, 10:30] = CEIL                       # a clipped patch
ramp = np.linspace(0.8, 1.1, L.SR.PITCH_PX // L.SR.FIELD_DIVISOR, dtype=np.float32)
field = np.tile(ramp, (ramp.size, 1))
got = L.apply_field(flat.copy(), field, 4, flat >= CEIL - 0.5)
chk("a clipped patch stays flat at the ceiling under the field",
    np.all(got[10:30, 10:30] == CEIL))
chk("...while unclipped pixels are still corrected", not np.allclose(got[0], 30000))
tissue = np.ones((100, 100), bool)
vals = np.tile(np.linspace(100, 5000, 100, dtype=np.float32), (100, 1))
vals[:, :3] = CEIL                              # 3% of the tissue clipped
lo, hi = L.section_range(vals, tissue, vals >= CEIL - 0.5)
chk("clipped pixels do not set the top of the range", hi < 6000, (lo, hi))
chk("a frozen range at the ceiling is pinned", L.pinned({"lo": 1070, "hi": 65535})
    and not L.pinned({"lo": 200, "hi": 20000}))
with open(L.RANGES_PATH, "w", encoding="utf-8") as fh:
    json.dump({"DAPI": RANGES["DAPI"], "AF568": {"lo": 1070, "hi": 65535}}, fh)
run("--force", "--limit", "1")
r0 = index_rows()[UIDS[0]]
chk("a pinned marker range is stretched per scene",
    r0["marker_range_from"] == "scene" and r0["nuclear_range_from"] == "dataset"
    and float(r0["marker_hi"]) < 65535, (r0["marker_lo"], r0["marker_hi"]))
only = np.asarray(Image.open(pic("stain_only", UIDS[0])))
chk("...so the tissue fills the range", only.max() >= 250 and np.median(only) > 30,
    (int(np.median(only)), int(only.max())))
with open(L.RANGES_PATH, "w", encoding="utf-8") as fh:
    json.dump(RANGES, fh)

print()
print("--- a scene that does not fit in memory is skipped, not fatal ---")
real = L.available_memory
L.available_memory = lambda: int(L.MEMORY_HEADROOM) + 1000   # less than a scene
before = os.path.getmtime(pic("stain_only", UIDS[0]))
run_failing("--um-px", "0.65", "--force")
chk("nothing drawn when it does not fit",
    os.path.getmtime(pic("stain_only", UIDS[0])) == before)
# Two CZIs this time: the limit has to hold across files, not only within one.
open(os.path.join(STUDY.source_dir, "LS1_1b.czi"), "wb").close()
kept = open(L.MANIFEST_CSV, encoding="utf-8").read()
with open(L.MANIFEST_CSV, "a", newline="", encoding="utf-8") as fh:
    csv.writer(fh).writerow(["LS1_1b_s00", "LS1_1b.czi", "LS1", "1", "b", "AF568", 0, 2])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ended = run_failing("--um-px", "0.65", "--force", "--limit", "1")
chk("--limit counts skipped scenes too", "1 not drawn" in buf.getvalue(),
    buf.getvalue().strip().splitlines()[-3:])
chk("...and a scene not drawn fails the stage", "1 scene(s) not drawn" in ended, ended)
with open(L.MANIFEST_CSV, "w", newline="", encoding="utf-8") as fh:
    fh.write(kept)
L.available_memory = lambda: None           # platform cannot say: draw anyway
chk("...and a platform that cannot say does not block", L.fits(10 ** 12) is None)
L.available_memory = real
chk("memory estimate scales with the area", L.memory_needed((0, 0, 20000, 20000), 1)
    > 5 * L.memory_needed((0, 0, 20000, 20000), 4))
chk("this machine reports its free memory", isinstance(real(), int))

print()
print("--- one scene that cannot be written does not stop the slide ---")
# A stand-in writer that refuses scene 0, as Windows does when its picture is
# open in a viewer: scene 1 of the same CZI must still be drawn, scene 0 named,
# and the stage end as a failure so the app does not show it Done.
real_save = L.save_png


def refusing_save(img, path):
    if os.path.basename(path) == UIDS[0] + ".png":
        raise PermissionError(13, "Permission denied", path)
    real_save(img, path)


L.save_png = refusing_save
before = {uid: os.path.getmtime(pic("stain_only", uid)) for uid in UIDS}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ended = run_failing("--force")
L.save_png = real_save
chk("the other scene of the slide is still drawn",
    os.path.getmtime(pic("stain_only", UIDS[1])) > before[UIDS[1]])
chk("the failed scene is named on screen, with why",
    UIDS[0] in buf.getvalue() and "Permission denied" in buf.getvalue(),
    buf.getvalue().strip().splitlines()[-3:])
chk("the stage ends as a failure, naming the count and the list",
    "1 scene(s) not drawn" in ended and L.FAILURES_CSV in ended, ended)
listed = list(csv.DictReader(open(L.FAILURES_CSV, encoding="utf-8")))
chk("the failures list names that scene and why",
    [r["scene_uid"] for r in listed] == [UIDS[0]]
    and "PermissionError" in listed[0]["reason"] and listed[0]["file"] == "LS1_1a.czi",
    listed)
run()
chk("...and a clean run empties the list",
    list(csv.DictReader(open(L.FAILURES_CSV, encoding="utf-8"))) == [])

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
