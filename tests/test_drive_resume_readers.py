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
# An ordinary failure: the read named here raises with the folder there.
BREAK = {"at": None}


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
    if BREAK["at"] == key:
        raise RuntimeError("corrupt subblock")
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
print("--- 01k: a file that fails part way with the folder there: as before ---")
write_csv(MANIFEST_SCENES, SCENES_K)
k_clear()
BREAK["at"] = (FA, 1, LOW)
rc, text = run(K, K_ARGV)
BREAK["at"] = None
chk("01k exits 1", rc, 1)
chk("...without waiting for a drive that never left", "@@drive" in text, False)
last = [ln for ln in text.splitlines() if ln.strip()][-1]
chk("...naming only the failed scene",
    [r["scene_uid"] for r in SCENES_K if r["scene_uid"] in last], ["LS05_s01a_sc01"])
with open(K.OUT_CSV, newline="", encoding="utf-8") as fh:
    chk("...the scene before it is still measured",
        sorted(r["scene_uid"] for r in csv.DictReader(fh)),
        ["LS05_s01a_sc00", "LS05_s02a_sc00", "LS05_s02a_sc01"])
got = k_snapshot()
chk("...its mask as uninterrupted",
    [k for k in base_k if "s01a_sc00" in k and got.get(k) != base_k[k]], [])

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

# ===================================================================== 01h

print()
print("--- 01h: the drive drops mid-file ---")
H = load_stage("01h_tilefield_raw.py")
# A small tile grid, so a run takes a moment rather than half a gigabyte: the
# stage reads these at call time. 204/12 = 17, 180/12 = 15, as 2040 and 1836.
H.TILE_PX, H.PITCH_PX = 204, 180
H.TILE_N, H.PITCH_N = H.TILE_PX // H.FIELD_DIVISOR, H.PITCH_PX // H.FIELD_DIVISOR
COUNT = {"open": 0, "asarray": 0}


class _Entry:
    is_pyramid = False
    dims = ("C", "Y", "X")

    def __init__(self, name, idx, c, scene, x, y, m):
        self.name, self.idx = name, idx
        self.start = (c, y, x)
        self.scene_index, self.mosaic_index = scene, m

    def asimage(self, czi):
        return self

    def asarray(self):
        COUNT["asarray"] += 1
        maybe_drop(("asarray", COUNT["asarray"]), self.name)
        # Different for every tile, so which tiles were picked shows in the field.
        return pixels(self.name, self.idx, shape=(H.TILE_PX, H.TILE_PX)) + np.uint16(500)


class _CziFile:
    def __init__(self, path, memmap=True):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        COUNT["open"] += 1
        maybe_drop(("open", COUNT["open"]), path)
        name = os.path.basename(path)
        self.subblock_directory = []
        for scene in range(4):
            for gy in range(3):
                for gx in range(3):
                    for c in (0, 1):
                        self.subblock_directory.append(_Entry(
                            name, len(self.subblock_directory), c, scene,
                            gx * H.PITCH_PX, gy * H.PITCH_PX, gy * 3 + gx))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Czifile:
    CziFile = _CziFile


H.czifile = _Czifile
HA, HB, HC = "LS06_s01a.czi", "LS06_s02a.czi", "LS06_s03a.czi"
for f in (HA, HB):
    open(os.path.join(STUDY.source_dir, f), "wb").close()
MANIFEST_FILES = os.path.join(STUDY.out_root, "manifest", "manifest_files.csv")
write_csv(MANIFEST_FILES, [{"file": f, "marker_channel": marker, "is_redundant_copy": "0"}
                           for f in (HA, HB)])
H_ARGV = ["01h", "--files", "2", "--tiles", "30"]


def h_clear():
    shutil.rmtree(H.FIELD_DIR, ignore_errors=True)
    COUNT.update(open=0, asarray=0)


def h_snapshot():
    # The numbers themselves; the PNG figure carries matplotlib's metadata.
    return snapshot(sorted(glob.glob(os.path.join(H.FIELD_DIR, "*.npy"))
                           + glob.glob(os.path.join(H.FIELD_DIR, "*.tif"))))


h_clear()
rc, text = run(H, H_ARGV)
chk("uninterrupted 01h exits zero", rc, 0)
base_h = h_snapshot()
chk("...wrote both channels' fields", len([k for k in base_h if k.endswith(".npy")]), 4)

h_clear()
DROP["at"] = ("asarray", 10)              # part way through the first file
rc, text = run(H, H_ARGV)
settle()
chk("the drop fired", DROP["at"], None)
chk("01h with the drive dropped exits zero", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = h_snapshot()
chk("...writes the same files", sorted(got), sorted(base_h))
chk("...byte for byte (same tiles picked)", [k for k in base_h if got.get(k) != base_h[k]], [])
chk("...no .tmp left", tmp_left(), [])

print()
print("--- 01h: a drop while the stitch geometry is read ---")
h_clear()
DROP["at"] = ("open", 3)                  # 2 sampled files, then stitch_map
rc, text = run(H, H_ARGV)
settle()
chk("the drop fired", DROP["at"], None)
chk("exits zero", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = h_snapshot()
chk("...byte for byte", [k for k in base_h if got.get(k) != base_h[k]], [])

print()
print("--- 01h: the folder is gone while the manifest's files are looked for ---")
h_clear()
t = vanish_for(0.3)
rc, text = run(H, H_ARGV)
t.join(5)
chk("exits zero after waiting", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = h_snapshot()
chk("...byte for byte", [k for k in base_h if got.get(k) != base_h[k]], [])

print()
print("--- 01h: a file that fails with the folder there is still a failure ---")
write_csv(MANIFEST_FILES, [{"file": f, "marker_channel": marker, "is_redundant_copy": "0"}
                           for f in (HA, HB, HC)])
open(os.path.join(STUDY.source_dir, HC), "wb").close()
real_init = _CziFile.__init__


def _broken_c(self, path, memmap=True):
    if os.path.basename(path) == HC:
        raise RuntimeError("truncated subblock directory")
    real_init(self, path, memmap)


_CziFile.__init__ = _broken_c
h_clear()
rc, text = run(H, ["01h", "--files", "3", "--tiles", "30"])
_CziFile.__init__ = real_init
chk("exits 1", rc, 1)
chk("...naming the broken file", HC in [ln for ln in text.splitlines() if ln.strip()][-1])
chk("...without waiting for a drive that never left", "@@drive" in text, False)
os.remove(os.path.join(STUDY.source_dir, HC))
write_csv(MANIFEST_FILES, [{"file": f, "marker_channel": marker, "is_redundant_copy": "0"}
                           for f in (HA, HB)])

print()
print("--- 01h: a file that fails part way keeps the tiles it gave ---")
import random  # noqa: E402
h_clear()
BREAK["at"] = ("asarray", 10)
failed_h = []
with contextlib.redirect_stdout(io.StringIO()) as said:
    st = H.collect_tiles([(os.path.join(STUDY.source_dir, HA), marker)], 30,
                         random.Random(H.SEED), failed_h)
BREAK["at"] = None
chk("one failed entry", [f for f, _ in failed_h], [HA])
chk("...the 9 tiles read before it are kept", sum(len(v) for v in st.values()), 9)
chk("...without waiting for a drive that never left", "@@drive" in said.getvalue(), False)

print()
print("--- 01h: Stopped is not swallowed as a per-file failure ---")
h_clear()
open(stop, "wb").close()
os.environ["LS_STOP_FILE"] = stop
DROP["at"] = ("asarray", 5)
stopped = False
# collect_tiles itself, on one file: through main(), the stitch-geometry read
# that follows would raise Stopped too and hide a per-file handler that
# swallowed it.
try:
    with contextlib.redirect_stdout(io.StringIO()):
        H.collect_tiles([(os.path.join(STUDY.source_dir, HA), marker)], 30,
                        random.Random(H.SEED), [])
except DR.Stopped:
    stopped = True
settle()
os.environ.pop("LS_STOP_FILE")
os.remove(stop)
chk("the stop file stops 01h", stopped)

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
