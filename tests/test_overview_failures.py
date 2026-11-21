"""01_overviews names every section it could not export, and says so in its
exit status.

A CZI that is not on the drive (unplugged, or still inside a zip) or that raises
while being read used to be counted into `failed` and skipped: no file name, no
scene uids, and main() finished normally, so the app reported success. 04a's
"unaccounted" check is built from focus.csv, so a section that never reached
focus.csv was invisible downstream too.

Now each such file is printed with its scene uids, qc/overview_failures.csv
lists one row per section, and main() exits non-zero. A clean run still writes
the CSV, header only, so a failure list from an earlier run cannot linger.

A stand-in for pylibCZIrw serves small planes, so no CZI is touched.

Run:  python tests/test_overview_failures.py
"""

import contextlib
import csv
import io
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

STUDY = use_temp_study()
OV = load_stage("01_overviews.py")

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


GOOD, MISSING, BROKEN = "LS01_s01a.czi", "LS01_s02a.czi", "LS01_s03a.czi"


class _Doc:
    scenes_bounding_rectangle = {0: _Rect(0, 0, 400, 300), 1: _Rect(500, 0, 400, 300)}

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        if os.path.basename(self.path) == BROKEN:
            raise RuntimeError("truncated subblock directory")
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom):
        h, w = int(round(roi[3] * zoom)), int(round(roi[2] * zoom))
        a = np.full((h, w), 500, np.uint16)
        a[h // 4:h // 2, w // 4:w // 2] = 20000
        return a[None, :, :]


class _PyCzi:
    @staticmethod
    def open_czi(path):
        return _Doc(path)


OV.pyczi = _PyCzi

marker = list(OV.MASK_MARKERS)[0]
FAIL_CSV = os.path.join(OV.QC_DIR, "overview_failures.csv")


def scene_rows(fname, slide):
    stem = fname[:-4]
    return [{"scene_uid": f"{stem}_sc{s:02d}", "file": fname, "animal": "LS01",
             "slide": slide, "variant": "a", "marker_channel": marker,
             "scene_index": str(s), "section_order": str(s)} for s in (0, 1)]


def write_manifest(scenes):
    os.makedirs(os.path.join(STUDY.out_root, "manifest"), exist_ok=True)
    with open(os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv"), "w",
              newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(scenes[0]))
        w.writeheader()
        w.writerows(scenes)


def run_main(*argv):
    """main()'s outcome as an exit status, the way sys.exit(main()) and the
    app's runner would see it, plus everything it printed."""
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["01_overviews.py", *argv]
    try:
        with contextlib.redirect_stdout(out):
            try:
                rc = OV.main()
            except SystemExit as e:
                rc = e.code
                if isinstance(rc, str):       # a message is a non-zero exit
                    print(rc)
                    rc = 1
    finally:
        sys.argv = old
    return (rc or 0), out.getvalue()


def read_failures():
    if not os.path.exists(FAIL_CSV):
        return None, None
    with open(FAIL_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        return rd.fieldnames, list(rd)


os.makedirs(STUDY.source_dir, exist_ok=True)
for f in (GOOD, BROKEN):
    open(os.path.join(STUDY.source_dir, f), "wb").close()
good, missing, broken = scene_rows(GOOD, "1"), scene_rows(MISSING, "2"), scene_rows(BROKEN, "3")
write_manifest(good + missing + broken)
os.makedirs(OV.QC_DIR, exist_ok=True)
with open(OV.RANGES_PATH, "w", encoding="utf-8") as fh:
    json.dump({"DAPI": {"lo": 200, "hi": 20000}, marker: {"lo": 200, "hi": 20000}}, fh)

print("--- a missing CZI and one that raises ---")
# No --force: that would resample display_ranges.json from the broken file.
rc, text = run_main()
chk("main() exits non-zero", rc != 0)
chk("the missing file is named", MISSING in text)
chk("the broken file is named", BROKEN in text)
for r in missing + broken:
    chk(f"{r['scene_uid']} printed", r["scene_uid"] in text)
keys, rows = read_failures()
chk("overview_failures.csv written", keys is not None)
if keys is not None:
    chk("one row per lost section", sorted(r["scene_uid"] for r in rows),
        sorted(r["scene_uid"] for r in missing + broken))
    by_uid = {r["scene_uid"]: r for r in rows}
    chk("row names its file", by_uid.get(missing[0]["scene_uid"], {}).get("file"), MISSING)
    chk("missing row says why",
        "missing" in by_uid.get(missing[0]["scene_uid"], {}).get("reason", "").lower())
    chk("broken row carries the error",
        "truncated subblock" in by_uid.get(broken[0]["scene_uid"], {}).get("reason", ""))
with open(OV.QC_CSV, newline="", encoding="utf-8") as fh:
    chk("the good file still exported", sorted(r["scene_uid"] for r in csv.DictReader(fh)),
        sorted(r["scene_uid"] for r in good))
chk("no .tmp left behind", os.path.exists(FAIL_CSV + ".tmp"), False)

print()
print("--- the drive is back: a clean resume clears the list ---")
open(os.path.join(STUDY.source_dir, MISSING), "wb").close()
write_manifest(good + missing)
rc, text = run_main()
chk("main() exits zero", rc, 0)
keys, rows = read_failures()
chk("overview_failures.csv still there", keys is not None)
chk("...header only", rows, [])
chk("...with its columns", bool(keys and "scene_uid" in keys and "file" in keys))

print()
print("--- the drive drops mid-file: 01 waits, redoes the file, same numbers ---")
# The slides are on a USB drive that goes away rather than failing a read. A
# read that raises while the slides folder is unreachable is not the file's
# fault: 01 waits for the folder and redoes the whole file, and what it writes
# must be byte for byte what an uninterrupted run writes - no duplicate rows,
# no section recorded as failed. A file that is missing while the folder IS
# reachable is still an ordinary failure.
import glob  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

DR = OV.IO.drive_module()
chk("ls_drive loads", DR is not None)
DR.POLL = 0.05
os.environ["LS_WATCH"] = STUDY.source_dir
os.environ["LS_PROGRESS"] = "1"

FA, FB, FC = "LS02_s01a.czi", "LS02_s02a.czi", "LS02_s03a.czi"
for f in (FA, FB):
    open(os.path.join(STUDY.source_dir, f), "wb").close()
drop_scenes = scene_rows(FA, "1") + scene_rows(FB, "2")
# One drop: (file, scene) whose read raises the first time, with the folder gone.
DROP = {"at": None}
# An ordinary failure: (file, scene) whose read raises with the folder there.
BREAK = {"at": None}


def _vanish_for(seconds):
    """Rename the slides folder away now; a thread puts it back later."""
    gone = STUDY.source_dir + ".gone"
    os.rename(STUDY.source_dir, gone)

    def back():
        time.sleep(seconds)
        os.rename(gone, STUDY.source_dir)
    t = threading.Thread(target=back, daemon=True)
    t.start()
    return t


class _BigDoc:
    """Scenes big enough to sample (>= 500 tissue pixels at 5.2 um/px), and
    pixels that differ per file, scene and channel, so a byte comparison means
    something."""
    scenes_bounding_rectangle = {0: _Rect(0, 0, 4000, 3000), 1: _Rect(5000, 0, 4000, 3000)}

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
        if BREAK["at"] == (name, scene):
            raise RuntimeError("corrupt subblock")
        if DROP["at"] == (name, scene):
            DROP["at"] = None
            DROP["thread"] = _vanish_for(0.3)
            raise OSError(5, "Input/output error", self.path)
        h, w = int(round(roi[3] * zoom)), int(round(roi[2] * zoom))
        seed = int(hashlib.sha1(f"{name}|{scene}|{plane['C']}".encode()).hexdigest()[:8], 16)
        a = np.random.default_rng(seed).integers(300, 900, (h, w)).astype(np.uint16)
        a[h // 4:3 * h // 4, w // 4:3 * w // 4] += np.uint16(15000 + 1000 * plane["C"])
        a[h // 3, w // 3:w // 2] = 65535          # a clipped run for the mask
        return a[None, :, :]


class _BigPyCzi:
    @staticmethod
    def open_czi(path):
        return _BigDoc(path)


import hashlib  # noqa: E402
OV.pyczi = _BigPyCzi


def clear_outputs():
    for p in (OV.QC_CSV, OV.SAT_RAW_CSV, FAIL_CSV):
        if os.path.exists(p):
            os.remove(p)
    for d in (OV.OVERVIEW_DIR, OV.CENSOR_RAW_DIR):
        shutil.rmtree(d, ignore_errors=True)


def snapshot():
    """Every output 01 wrote, as bytes, keyed by path under out_root."""
    out = {}
    for p in [OV.QC_CSV, OV.SAT_RAW_CSV, OV.RANGES_PATH] + sorted(
            glob.glob(os.path.join(OV.OVERVIEW_DIR, "**", "*.png"), recursive=True)
            + glob.glob(os.path.join(OV.CENSOR_RAW_DIR, "*.png"))):
        if not os.path.exists(p):
            continue
        with open(p, "rb") as fh:
            out[os.path.relpath(p, STUDY.out_root)] = fh.read()
    return out


def tmp_left():
    return sorted(glob.glob(os.path.join(STUDY.out_root, "**", "*.tmp"), recursive=True))


write_manifest(drop_scenes)
clear_outputs()
rc, text = run_main()
chk("uninterrupted run exits zero", rc, 0)
base = snapshot()
with open(OV.QC_CSV, newline="", encoding="utf-8") as fh:
    chk("...and exported every section", sorted(r["scene_uid"] for r in csv.DictReader(fh)),
        sorted(r["scene_uid"] for r in drop_scenes))

clear_outputs()
DROP["at"] = (FA, 1)
rc, text = run_main()
DROP.get("thread") and DROP["thread"].join(5)
chk("the drop fired", DROP["at"], None)
chk("a run with the drive dropped exits zero", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = snapshot()
chk("...writes the same files", sorted(got), sorted(base))
chk("...byte for byte", [k for k in base if got.get(k) != base[k]], [])
keys, rows = read_failures()
chk("...overview_failures.csv header only", rows, [])
with open(OV.QC_CSV, newline="", encoding="utf-8") as fh:
    uids = [r["scene_uid"] for r in csv.DictReader(fh)]
chk("...no duplicate rows", len(uids), len(set(uids)))
chk("...no .tmp left", tmp_left(), [])

print()
print("--- a drop, and a file missing while the folder is there ---")
clear_outputs()
write_manifest(drop_scenes + scene_rows(FC, "3"))
DROP["at"] = (FA, 1)
rc, text = run_main()
DROP.get("thread") and DROP["thread"].join(5)
chk("the drop fired", DROP["at"], None)
chk("main() exits non-zero", rc != 0)
keys, rows = read_failures()
chk("only the missing file is listed", sorted(r["scene_uid"] for r in rows or []),
    sorted(r["scene_uid"] for r in scene_rows(FC, "3")))
got = snapshot()
chk("...the dropped file's outputs are as uninterrupted",
    [k for k in base if got.get(k) != base[k] and "focus" not in k and "saturation" not in k],
    [])

print()
print("--- the folder is gone before a CZI is looked for: wait, not 'missing' ---")
clear_outputs()
write_manifest(drop_scenes)
t = _vanish_for(0.3)
rc, text = run_main()
t.join(5)
chk("exits zero", rc, 0)
chk("...after waiting for the drive", "@@drive back" in text)
got = snapshot()
chk("...byte for byte as uninterrupted", [k for k in base if got.get(k) != base[k]], [])

print()
print("--- a file that fails part way with the folder there: as before ---")
clear_outputs()
BREAK["at"] = (FA, 1)
rc, text = run_main()
BREAK["at"] = None
chk("main() exits non-zero", rc != 0)
chk("...without waiting for a drive that never left", "@@drive" in text, False)
keys, rows = read_failures()
chk("...only the failed scene is listed", [r["scene_uid"] for r in rows or []], ["LS02_s01a_sc01"])
chk("...with its error", "corrupt subblock" in (rows or [{}])[0].get("reason", ""))
with open(OV.QC_CSV, newline="", encoding="utf-8") as fh:
    chk("...the scene before it is still exported",
        sorted(r["scene_uid"] for r in csv.DictReader(fh)),
        ["LS02_s01a_sc00", "LS02_s02a_sc00", "LS02_s02a_sc01"])
got = snapshot()
chk("...and its outputs are as uninterrupted",
    [k for k in base if "s01a_sc00" in k and got.get(k) != base[k]], [])

print()
print("--- --force: the drop lands while the display ranges are sampled ---")
clear_outputs()
rc, text = run_main("--force")
chk("uninterrupted --force exits zero", rc, 0)
base_f = snapshot()
clear_outputs()
os.remove(OV.RANGES_PATH)
DROP["at"] = (FB, 0)                  # sampling reads FB's scene 0 before export does
rc, text = run_main("--force")
DROP.get("thread") and DROP["thread"].join(5)
chk("the drop fired", DROP["at"], None)
chk("a --force run with the drive dropped exits zero", rc, 0)
chk("...says the drive came back", "@@drive back" in text)
got = snapshot()
chk("...same display ranges and outputs, byte for byte",
    [k for k in base_f if got.get(k) != base_f[k]], [])
chk("...no .tmp left", tmp_left(), [])

print()
print("--- Stopped is not swallowed as a per-file failure ---")
clear_outputs()
stop = os.path.join(STUDY.root if hasattr(STUDY, "root") else os.path.dirname(STUDY.out_root),
                    "STOP")
open(stop, "wb").close()
os.environ["LS_STOP_FILE"] = stop
# One file only: with a second one, the wait before ITS exists check would
# raise Stopped too and hide a per-file handler that swallowed the first.
write_manifest(scene_rows(FA, "1"))
DROP["at"] = (FA, 0)
stopped = False
try:
    run_main()
except DR.Stopped:
    stopped = True
DROP.get("thread") and DROP["thread"].join(5)
os.environ.pop("LS_STOP_FILE")
os.remove(stop)
chk("the stop file stops the stage", stopped)

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
