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
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
