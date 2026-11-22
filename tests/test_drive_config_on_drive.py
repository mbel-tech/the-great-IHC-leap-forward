"""A reader waits for the drive when the study file is on that drive.

study.json normally lives in out_root, on the USB drive that drops. ls_drive
used to read the watched folders from it afresh on every look; with the drive
gone the read failed, nothing was watched, every folder counted as reachable,
and a reader recorded the slide it was on as failed. Here the config lives in
out_root, LS_WATCH is NOT set, and the stand-in reader renames out_root away
mid-slide: the stage must wait, redo the slide once the drive is back, and
write exactly what an uninterrupted run writes.

01k (pylibCZIrw, stubbed) is the reader, as in test_drive_resume_readers.py.

Run:  python tests/test_drive_config_on_drive.py
"""

import contextlib
import csv
import glob
import hashlib
import io
import json
import os
import shutil
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

STUDY = use_temp_study()
# The study file inside out_root, as the app lays a study out.
CFG = os.path.join(STUDY.out_root, "study.json")
with open(STUDY.path, encoding="utf-8") as fh:
    _cfg = json.load(fh)
with open(CFG, "w", encoding="utf-8") as fh:
    json.dump(_cfg, fh)
os.environ["LS_CONFIG"] = CFG
os.environ.pop("LS_WATCH", None)
os.environ["LS_PROGRESS"] = "1"

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label:62} {'' if ok else f'{got!r} != {want!r}'}")
    if not ok:
        fails += 1


DROP = {"at": None, "thread": None}


def vanish_out_root(seconds):
    gone = STUDY.out_root + ".gone"
    os.rename(STUDY.out_root, gone)

    def back():
        time.sleep(seconds)
        os.rename(gone, STUDY.out_root)
    t = threading.Thread(target=back, daemon=True)
    t.start()
    return t


def pixels(name, *key, shape):
    seed = int(hashlib.sha1("|".join(map(str, (name, *key))).encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed).integers(300, 900, shape).astype(np.uint16)


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class _Doc:
    scenes_bounding_rectangle = {0: _Rect(0, 0, 800, 600), 1: _Rect(1000, 0, 800, 600)}

    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            raise FileNotFoundError(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom):
        name = os.path.basename(self.path)
        if DROP["at"] == (name, scene, zoom):
            DROP["at"] = None
            DROP["thread"] = vanish_out_root(0.3)
            raise OSError(5, "Input/output error", self.path)
        h, w = int(round(roi[3] * zoom)), int(round(roi[2] * zoom))
        a = pixels(name, scene, plane["C"], zoom, shape=(h, w))
        a[h // 4:3 * h // 4, w // 4:3 * w // 4] += np.uint16(15000)
        return a[None, :, :]


class _PyCzi:
    @staticmethod
    def open_czi(path):
        return _Doc(path)


sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
K = load_stage("01k_saturation_raw.py")
DR = K.IO.drive_module()
DR.POLL = 0.05
marker = list(K.MASK_MARKERS)[0]
LOW = K.OV.BASE_PX_UM / K.OV.TARGET_UM
FA, FB = "LS05_s01a.czi", "LS05_s02a.czi"
for f in (FA, FB):
    open(os.path.join(STUDY.source_dir, f), "wb").close()
SCENES = [{"scene_uid": f"{f[:-4]}_sc{s:02d}", "file": f, "animal": "LS05",
           "slide": sl, "variant": "a", "marker_channel": marker,
           "scene_index": str(s), "section_order": str(s)}
          for f, sl in ((FA, "1"), (FB, "2")) for s in (0, 1)]
MANIFEST = os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv")
os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
with open(MANIFEST, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(SCENES[0]))
    w.writeheader()
    w.writerows(SCENES)
ARGV = ["01k", "--native-sample", "0", "--no-merge"]


def run():
    out = io.StringIO()
    old = sys.argv
    sys.argv = ARGV
    try:
        with contextlib.redirect_stdout(out):
            try:
                rc = K.main()
            except SystemExit as e:
                rc = e.code if not isinstance(e.code, str) else 1
                print(e.code)
    finally:
        sys.argv = old
        if DROP["thread"] is not None:
            DROP["thread"].join(5)
            DROP["thread"] = None
    return (rc or 0), out.getvalue()


def clear():
    for p in (K.OUT_CSV, K.NATIVE_CSV):
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(K.MASK_DIR, ignore_errors=True)


def snap():
    got = {}
    for p in [K.OUT_CSV] + sorted(glob.glob(os.path.join(K.MASK_DIR, "*"))):
        if os.path.exists(p):
            with open(p, "rb") as fh:
                got[os.path.relpath(p, STUDY.out_root)] = fh.read()
    return got


print("--- 01k, study.json in out_root, no LS_WATCH: out_root drops mid-slide ---")
chk("LS_WATCH is not set", os.environ.get("LS_WATCH"), None)
clear()
rc, text = run()
chk("uninterrupted 01k exits zero", rc, 0)
base = snap()

clear()
DROP["at"] = (FA, 1, LOW)
rc, text = run()
chk("the drop fired", DROP["at"], None)
chk("01k exits zero: a wait, not a failure", rc, 0)
chk("...it waited for the drive", "@@drive lost" in text and "@@drive back" in text)
chk("...naming out_root", f"@@drive lost {STUDY.out_root} |" in text)
got = snap()
chk("...writes the same files", sorted(got), sorted(base))
chk("...byte for byte", [k for k in base if got.get(k) != base[k]], [])
with open(K.OUT_CSV, newline="", encoding="utf-8") as fh:
    uids = [r["scene_uid"] for r in csv.DictReader(fh)]
chk("...every scene once", sorted(uids), sorted(r["scene_uid"] for r in SCENES))

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
