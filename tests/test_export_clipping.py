"""01_overviews writes the raw clipping record that 01k used to write in a
second pass over every CZI - and writes exactly what 01k writes.

The check that matters is equality: same saturation_raw.csv row, same mask
pixels, from the same 16-bit planes, whichever stage produced them. Then that a
01k run after a fresh export reads no pixels at all, because there is nothing
left for it to do.

A stand-in for pylibCZIrw serves deterministic planes with clipped pixels in
them, so no CZI and no real drive is touched.

Run:  python tests/test_export_clipping.py
"""

import csv
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

STUDY = use_temp_study()
OV = load_stage("01_overviews.py")
K = load_stage("01k_saturation_raw.py")

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label:62} {'' if ok else f'{got!r} != {want!r}'}")
    if not ok:
        fails += 1


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


READS = []


class _Doc:
    """Two scenes; each channel a seeded plane with a clipped block in it."""

    scenes_bounding_rectangle = {0: _Rect(100, 200, 640, 480),
                                 1: _Rect(900, 200, 560, 400)}

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom):
        READS.append((scene, plane["C"], zoom))
        h, w = int(round(roi[3] * zoom)), int(round(roi[2] * zoom))
        rng = np.random.default_rng(1000 * scene + plane["C"])
        a = rng.integers(200, 3000, size=(h, w)).astype(np.uint16)
        a[h // 4:h // 2, w // 3:w // 2] = 20000          # tissue, so a mask forms
        if plane["C"] == 1:
            a[h // 3:h // 3 + 3, w // 3:w // 3 + 5] = 65535   # clipped marker
            a[0, 0] = 65535                                  # and a lone pixel
        return a[None, :, :]


class _PyCzi:
    @staticmethod
    def open_czi(path):
        return _Doc(path)


OV.pyczi = _PyCzi
sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi

os.makedirs(STUDY.source_dir, exist_ok=True)
FNAME = "LS01_s01a.czi"
open(os.path.join(STUDY.source_dir, FNAME), "wb").close()
marker = list(OV.MASK_MARKERS)[0]
scenes = [{"scene_uid": f"LS01_s01a_sc{s:02d}", "file": FNAME, "animal": "LS01",
           "slide": "1", "variant": "a", "marker_channel": marker,
           "scene_index": str(s), "section_order": str(s)} for s in (0, 1)]

os.makedirs(os.path.join(STUDY.out_root, "manifest"), exist_ok=True)
with open(os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv"), "w",
          newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(scenes[0]))
    w.writeheader()
    w.writerows(scenes)

zoom = OV.BASE_PX_UM / OV.TARGET_UM
rng = np.random.default_rng(7)
fields = {c: rng.uniform(0.8, 1.2, size=(16, 16)).astype(np.float32) for c in (0, 1)}
ranges = {"DAPI": {"lo": 200, "hi": 20000}, marker: {"lo": 200, "hi": 20000}}

print("--- export writes the raw clipping record ---")
OV.export(scenes, fields, ranges, {}, zoom, 0, True)
reads_export = len(READS)
chk("export read each scene's two planes once", reads_export, 4)

with open(OV.SAT_RAW_CSV, newline="", encoding="utf-8") as fh:
    exported = {r["scene_uid"]: r for r in csv.DictReader(fh)}
chk("a saturation_raw row per section", sorted(exported), [r["scene_uid"] for r in scenes])
masks = {u: np.asarray(Image.open(os.path.join(OV.CENSOR_RAW_DIR, u + "_clipped.png")))
         for u in exported}
chk("a mask per section", len(masks), 2)

print()
print("--- and it is exactly what 01k writes ---")
doc = _Doc("x")
for r in scenes:
    s = int(r["scene_index"])
    rect = doc.scenes_bounding_rectangle[s]
    dapi_raw, mark_raw, corrected = K.measure(doc, rect, fields, zoom, scene=s)
    want = OV.raw_clip_record(r, FNAME, rect, s, zoom, dapi_raw, mark_raw, corrected,
                              OV.ceiling_for({}, FNAME))
    got = {k: v for k, v in exported[r["scene_uid"]].items()}
    chk(f"{r['scene_uid']}: same row", got, {k: str(v) for k, v in want.items()})
    again = np.asarray(Image.open(os.path.join(OV.CENSOR_RAW_DIR,
                                               r["scene_uid"] + "_clipped.png")))
    chk(f"{r['scene_uid']}: same mask pixels", bool(np.array_equal(again, masks[r["scene_uid"]])))
    chk(f"{r['scene_uid']}: mask is the raw plane at the ceiling, full size",
        bool(np.array_equal(again > 0, mark_raw >= 65535)))
    chk(f"{r['scene_uid']}: lone clipped pixel kept", bool(again[0, 0] > 0))

print()
print("--- focus.csv carries the same raw fractions ---")
with open(OV.QC_CSV, newline="", encoding="utf-8") as fh:
    focus = {r["scene_uid"]: r for r in csv.DictReader(fh)}
for u, r in exported.items():
    chk(f"{u}: marker fraction agrees",
        focus[u]["saturated_fraction_raw"], r["saturated_fraction_raw"])

print()
print("--- 01k after a fresh export reads nothing ---")
del READS[:]
argv = sys.argv
sys.argv = ["01k", "--native-sample", "0", "--no-merge"]
try:
    K.main()
finally:
    sys.argv = argv
chk("01k read no pixels", len(READS), 0)
with open(OV.SAT_RAW_CSV, newline="", encoding="utf-8") as fh:
    after = {r["scene_uid"]: r for r in csv.DictReader(fh)}
chk("01k left the rows as they were", after, exported)

print()
print("--- a section exported before the change is still backfilled ---")
gone = scenes[1]["scene_uid"]
os.remove(os.path.join(OV.CENSOR_RAW_DIR, gone + "_clipped.png"))
sys.argv = ["01k", "--native-sample", "0", "--no-merge"]
try:
    K.main()
finally:
    sys.argv = argv
chk("01k re-read only that section's two planes", len(READS), 2)
chk("...and wrote its mask back",
    os.path.exists(os.path.join(OV.CENSOR_RAW_DIR, gone + "_clipped.png")))

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
