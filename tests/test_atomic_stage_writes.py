"""A crash while 00, 01 or 02 writes an output leaves the previous copy whole.

The manifest CSVs (00), display_ranges.json (01) and pairs.csv (02) are read
by every later stage as the truth about the study. Written in place with
`open(path, "w")`, a crash part-way through left a truncated file that the
next stage read without complaint. Each now goes to `<path>.tmp` and is
swapped in by `os.replace`, so a failure at the swap - the last moment it can
fail - must leave the old file exactly as it was.

The failure is a RuntimeError rather than an OSError, so ls_io's retry for a
vanishing drive does not sleep through it.

Run:  python tests/test_atomic_stage_writes.py
"""

import contextlib
import io
import json
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

STUDY = use_temp_study()
M = load_stage("00_manifest.py")
OV = load_stage("01_overviews.py")
P2 = load_stage("02_pair_passes.py")

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(60) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def crash_at_replace(label, path, old, write):
    """Put `old` at `path`, run `write` with os.replace failing, check `old` survived."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(old)
    raised = False
    with mock.patch("os.replace", side_effect=RuntimeError("crash at swap")), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            write()
        except RuntimeError:
            raised = True
    chk(f"{label}: the crash propagates", raised, True)
    with open(path, encoding="utf-8", newline="") as fh:
        chk(f"{label}: previous file intact", fh.read(), old)


OLD_CSV = "scene_uid,marker\nLS1_1a_s0,PCNA\n"
NEW_ROWS = [{"scene_uid": "LS9_9z_s9", "marker": "pERK"}]

for name in ("manifest_files.csv", "manifest_scenes.csv"):
    p = os.path.join(M.MANIFEST_DIR, name)
    crash_at_replace(f"00 {name}", p, OLD_CSV, lambda p=p: M._write_csv(p, NEW_ROWS))
p = os.path.join(M.QC_DIR, "slide_layout_summary.csv")
crash_at_replace("00 slide_layout_summary.csv", p, OLD_CSV,
                 lambda: M._write_csv(p, NEW_ROWS))

p = os.path.join(P2.OUT_ROOT, "pairs.csv")
crash_at_replace("02 pairs.csv", p, OLD_CSV, lambda: P2._write(p, NEW_ROWS))

# No scenes to sample: 01 computes an empty range set and writes it, which is
# enough to reach the write without a CZI.
OLD_JSON = json.dumps({"DAPI": {"lo": 10, "hi": 900}}, indent=2)
crash_at_replace("01 display_ranges.json", OV.RANGES_PATH, OLD_JSON,
                 lambda: OV.sample_display_ranges([], {}, 1, force=True))

# And the writes still land when nothing fails.
with contextlib.redirect_stdout(io.StringIO()):
    p = os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv")
    M._write_csv(p, NEW_ROWS + [{"scene_uid": "LS9_9z_s8", "marker": "pERK", "extra": "x"}])
with open(p, encoding="utf-8") as fh:
    chk("00 write: header carries every row's keys", fh.readline().strip(),
        "scene_uid,marker,extra")
chk("00 write: no temp file left", os.path.exists(p + ".tmp"), False)
with contextlib.redirect_stdout(io.StringIO()):
    p = os.path.join(P2.OUT_ROOT, "pairs.csv")
    P2._write(p, NEW_ROWS)
with open(p, encoding="utf-8") as fh:
    chk("02 write: new rows land", fh.read(), "scene_uid,marker\nLS9_9z_s9,pERK\n")
with contextlib.redirect_stdout(io.StringIO()):
    OV.sample_display_ranges([], {}, 1, force=True)
with open(OV.RANGES_PATH, encoding="utf-8") as fh:
    chk("01 write: ranges land", json.load(fh), {})

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
