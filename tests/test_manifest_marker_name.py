"""00_manifest names a multiplex scan's marker the way the study names it.

New study offers a name for each marker, and a multiplex study keeps it in the
channel table (`czi_name: AF568`, called `pERK`). Every later stage picks its
scenes by comparing the manifest's `marker_channel` with that name. The
manifest used to write the scan's own AF568, so on the Contagion slides 01 wrote
no clipping masks and 01k refused the study: "no scene in manifest_scenes.csv is
of a declared marker ['pERK']".

The CZI reader is replaced by fakes, so this runs on header values alone.

Run:  python tests/test_manifest_marker_name.py
"""

import contextlib
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

NAMING = {"pattern": r"^(?P<subject>[A-Za-z]+\d+)_(?P<slide>\d+)(?P<replicate>[a-z])?\.czi$",
          "case_insensitive": True}
STUDY = use_temp_study(
    slide_naming=NAMING, pixel_size_um=0.3251,
    acquisition={"layout": "multiplex", "markers": [], "channels": [
        {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
        {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1}]},
    display={"colours": {"pERK": "red"}})
M = load_stage("00_manifest.py")

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(60) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def fake_info(name):
    return {
        "size_x": 100, "size_y": 100, "size_c": 2, "size_s": 2, "size_m": 1,
        "pixel_type": "Gray16", "component_bit_count": 16, "clip_ceiling": 65535,
        "acquired": "2026-02-24T09:13:45", "px_um_x": 0.3251, "px_um_y": 0.3251,
        "objective_mag": "20", "objective_na": "0.8", "camera": "cam",
        "shading_reference_mode": None, "online_stitching": "false",
        "channels": [
            {"name": "DAPI", "exposure_ms": 5.0, "pixel_type": "Gray16",
             "component_bit_count": 16},
            {"name": "AF568", "exposure_ms": 300.0, "pixel_type": "Gray16",
             "component_bit_count": 16},
        ],
        "scenes": [
            {"index": i, "name": f"ScanRegion{i}", "center_x_um": 9000.0 * i,
             "center_y_um": 0.0, "width_um": 7000.0, "height_um": 7000.0}
            for i in range(2)
        ],
    }


files = ["CD16_1a.czi", "PO16_2a.czi"]
M.discover_sources = lambda stack: [
    ("LOOSE", name, os.path.join(STUDY.source_dir, name),
     (lambda n: (lambda: contextlib.nullcontext(n)))(name)) for name in files]
M.read_metadata = lambda name: name
M.summarise = fake_info
with contextlib.redirect_stdout(io.StringIO()):
    code = M.main()
chk("the manifest is written", code or 0, 0)

with open(os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv"), newline="",
          encoding="utf-8") as fh:
    scenes = list(csv.DictReader(fh))
with open(os.path.join(M.MANIFEST_DIR, "manifest_files.csv"), newline="",
          encoding="utf-8") as fh:
    file_rows = list(csv.DictReader(fh))
chk("every scene is of the study's marker, pERK",
    sorted({r["marker_channel"] for r in scenes}), ["pERK"])
chk("...and every file", sorted({r["marker_channel"] for r in file_rows}), ["pERK"])

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
