"""01k and 01h wait out a dropped drive and redo the slide they were on.

The slides are on a USB drive that goes away rather than failing a read. A read
that raises while the slides folder is unreachable is not the file's fault: the
stage waits for the folder (ls_drive.per_slide), reads the whole file again,
and writes exactly what an uninterrupted run writes - same bytes, no duplicate
rows, nothing recorded as failed. A file missing while the folder IS reachable
is still an ordinary failure, recorded as before.

The drop is real as far as the stage can tell: a stand-in reader renames the
watched slides folder away and raises OSError, and a thread renames it back
0.3 s later. pylibCZIrw (01k) and czifile (01h) are stand-ins, so no CZI is
touched. 01_overviews' own drop checks are in test_overview_failures.py.

Run:  python tests/test_drive_resume_readers.py
"""

import contextlib
import csv
import glob
import hashlib
import io
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
os.environ["LS_WATCH"] = STUDY.source_dir
os.environ["LS_PROGRESS"] = "1"

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label:62} {'' if ok else f'{got!r} != {want!r}'}")
    if not ok:
        fails += 1


# One drop at a time: DROP["at"] names the read that raises (once), with the
# slides folder renamed away for 0.3 s.
DROP = {"at": None, "thread": None}


def vanish_for(seconds):
    gone = STUDY.source_dir + ".gone"
    os.rename(STUDY.source_dir, gone)

    def back():
        time.sleep(seconds)
        os.rename(gone, STUDY.source_dir)
    t = threading.Thread(target=back, daemon=True)
    t.start()
    return t


def maybe_drop(key, path):
    if DROP["at"] == key:
        DROP["at"] = None
        DROP["thread"] = vanish_for(0.3)
        raise OSError(5, "Input/output error", path)


def settle():
    if DROP["thread"] is not None:
        DROP["thread"].join(5)
        DROP["thread"] = None


def pixels(name, *key, shape):
    seed = int(hashlib.sha1("|".join(map(str, (name, *key))).encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed).integers(300, 900, shape).astype(np.uint16)


def run(mod, argv):
    """main()'s exit status, and what it printed."""
    out = io.StringIO()
    old = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(out):
            try:
                rc = mod.main()
            except SystemExit as e:
                rc = e.code
                if isinstance(rc, str):
                    print(rc)
                    rc = 1
    finally:
        sys.argv = old
    return (rc or 0), out.getvalue()


def snapshot(paths):
    out = {}
    for p in paths:
        if os.path.exists(p):
            with open(p, "rb") as fh:
                out[os.path.relpath(p, STUDY.out_root)] = fh.read()
    return out


def tmp_left():
    return sorted(glob.glob(os.path.join(STUDY.out_root, "**", "*.tmp"), recursive=True))


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ===================================================================== 01k

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
        maybe_drop((name, scene, zoom), self.path)
        h, w = int(round(roi[3] * zoom)), int(round(roi[2] * zoom))
        a = pixels(name, scene, plane["C"], zoom, shape=(h, w))
        a[h // 4:3 * h // 4, w // 4:3 * w // 4] += np.uint16(15000)
        a[h // 3, w // 3:w // 2] = 65535          # some clipping to measure
        return a[None, :, :]


class _PyCzi:
    @staticmethod
    def open_czi(path):
        return _Doc(path)


sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
K = load_stage("01k_saturation_raw.py")
DR = K.IO.drive_module()
chk("ls_drive loads", DR is not None)
DR.POLL = 0.05
marker = list(K.MASK_MARKERS)[0]
LOW = K.OV.BASE_PX_UM / K.OV.TARGET_UM

FA, FB, FC = "LS05_s01a.czi", "LS05_s02a.czi", "LS05_s03a.czi"
for f in (FA, FB):
    open(os.path.join(STUDY.source_dir, f), "wb").close()


def scene_rows(fname, slide):
    stem = fname[:-4]
    return [{"scene_uid": f"{stem}_sc{s:02d}", "file": fname, "animal": "LS05",
             "slide": slide, "variant": "a", "marker_channel": marker,
             "scene_index": str(s), "section_order": str(s)} for s in (0, 1)]


SCENES_K = scene_rows(FA, "1") + scene_rows(FB, "2")
MANIFEST_SCENES = os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv")
K_ARGV = ["01k", "--native-sample", "0", "--no-merge"]


def k_clear():
    for p in (K.OUT_CSV, K.NATIVE_CSV):
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(K.MASK_DIR, ignore_errors=True)


def k_snapshot():
    return snapshot([K.OUT_CSV, K.NATIVE_CSV] + sorted(glob.glob(os.path.join(K.MASK_DIR, "*"))))


print("--- 01k: the drive drops mid-file ---")
write_csv(MANIFEST_SCENES, SCENES_K)
k_clear()
rc, text = run(K, K_ARGV)
chk("uninterrupted 01k exits zero", rc, 0)
base_k = k_snapshot()
with open(K.OUT_CSV, newline="", encoding="utf-8") as fh:
    chk("...measured every section", sorted(r["scene_uid"] for r in csv.DictReader(fh)),
        sorted(r["scene_uid"] for r in SCENES_K))

k_clear()
DROP["at"] = (FA, 1, LOW)
rc, text = run(K, K_ARGV)
settle()
chk("the drop fired", DROP["at"], None)
chk("01k with the drive dropped exits zero", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = k_snapshot()
chk("...writes the same files", sorted(got), sorted(base_k))
chk("...byte for byte", [k for k in base_k if got.get(k) != base_k[k]], [])
with open(K.OUT_CSV, newline="", encoding="utf-8") as fh:
    uids = [r["scene_uid"] for r in csv.DictReader(fh)]
chk("...no duplicate rows", len(uids), len(set(uids)))
chk("...no .tmp left", tmp_left(), [])

print()
print("--- 01k: a drop, and a file missing while the folder is there ---")
write_csv(MANIFEST_SCENES, SCENES_K + scene_rows(FC, "3"))
k_clear()
DROP["at"] = (FA, 1, LOW)
rc, text = run(K, K_ARGV)
settle()
chk("the drop fired", DROP["at"], None)
chk("01k exits 1", rc, 1)
last = [ln for ln in text.splitlines() if ln.strip()][-1]
chk("...naming only the missing file's sections",
    sorted(u for u in (r["scene_uid"] for r in SCENES_K + scene_rows(FC, "3")) if u in last),
    sorted(r["scene_uid"] for r in scene_rows(FC, "3")))
got = k_snapshot()
chk("...the dropped file measured as uninterrupted",
    [k for k in base_k if k != os.path.relpath(K.OUT_CSV, STUDY.out_root)
     and got.get(k) != base_k[k]], [])

print()
print("--- 01k: the folder is gone before a CZI is looked for ---")
write_csv(MANIFEST_SCENES, SCENES_K)
k_clear()
t = vanish_for(0.3)
rc, text = run(K, K_ARGV)
t.join(5)
chk("exits zero after waiting", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = k_snapshot()
chk("...byte for byte", [k for k in base_k if got.get(k) != base_k[k]], [])

print()
print("--- 01k: a drop during the native-resolution sample ---")
NATIVE_ARGV = ["01k", "--native-sample", "4", "--no-merge"]
k_clear()
rc, text = run(K, NATIVE_ARGV)
chk("uninterrupted native sample exits zero", rc, 0)
base_n = k_snapshot()
chk("...wrote the native csv", os.path.relpath(K.NATIVE_CSV, STUDY.out_root) in base_n)
k_clear()
# The native sample reads each scene at 0.125 first; 1.0 is its second read.
DROP["at"] = (FB, 0, 1.0)
rc, text = run(K, NATIVE_ARGV)
settle()
chk("the drop fired", DROP["at"], None)
chk("exits zero", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = k_snapshot()
chk("...byte for byte, native csv included",
    [k for k in base_n if got.get(k) != base_n[k]], [])

print()
print("--- 01k: Stopped is not swallowed as a per-file failure ---")
write_csv(MANIFEST_SCENES, scene_rows(FA, "1"))
k_clear()
stop = os.path.join(os.path.dirname(STUDY.out_root), "STOP")
open(stop, "wb").close()
os.environ["LS_STOP_FILE"] = stop
DROP["at"] = (FA, 0, LOW)
stopped = False
try:
    run(K, K_ARGV)
except DR.Stopped:
    stopped = True
settle()
os.environ.pop("LS_STOP_FILE")
os.remove(stop)
chk("the stop file stops 01k", stopped)

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
