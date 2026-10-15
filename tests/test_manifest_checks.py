"""00_manifest refuses a study it would otherwise hand downstream wrong.

Two things every later stage takes on trust from stage 0:

  * the configured `pixel_size_um` is the scale of every file. Nothing after
    00 reads the scale from the CZI, so a slide scanned with another objective
    would be measured as if it were not.
  * `scene_uid` names one section of one file. It is the key of every table,
    and a flat filename in several output folders (01k's `<uid>_clipped.png`),
    so two files giving one uid overwrite each other without any error.

The CZI reader is replaced by fakes, so this runs on header values alone.

Run:  python tests/test_manifest_checks.py
"""

import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

#: The live LS grammar, as pinned in tests/test_slide_naming.py: the marker
#: may ride in the file suffix, which the scene uid leaves out.
LS = {
    "pattern": r"^(?P<subject>LS\d+)_(?P<slide>\d+)(?P<replicate>[a-z])"
               r"(?P<rescan>-?)(?P<pass>_[A-Za-z0-9]+)?\.czi$",
    "case_insensitive": False,
}
STUDY = use_temp_study(slide_naming=LS, pixel_size_um=0.65)
M = load_stage("00_manifest.py")

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(60) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def fake_info(px_um, marker):
    """What czi_meta.summarise returns, for a two-scene, two-channel file."""
    return {
        "size_x": 100, "size_y": 100, "size_c": 2, "size_s": 2, "size_m": 1,
        "pixel_type": "Gray16", "component_bit_count": 16, "clip_ceiling": 65535,
        "acquired": "2025-12-24T09:13:45",
        "px_um_x": px_um, "px_um_y": px_um,
        "objective_mag": "10", "objective_na": "0.45", "camera": "cam",
        "shading_reference_mode": None, "online_stitching": "false",
        "channels": [
            {"name": "DAPI", "exposure_ms": 5.0, "pixel_type": "Gray16",
             "component_bit_count": 16},
            {"name": marker, "exposure_ms": 300.0, "pixel_type": "Gray16",
             "component_bit_count": 16},
        ],
        "scenes": [
            {"index": i, "name": f"ScanRegion{i}", "center_x_um": 9000.0 * i,
             "center_y_um": 0.0, "width_um": 7000.0, "height_um": 7000.0}
            for i in range(2)
        ],
    }


def run(files):
    """Run 00's main() over {filename: (px_um, marker)}. (exit code, output)."""
    M.discover_sources = lambda stack: [
        ("LOOSE", name, os.path.join(STUDY.source_dir, name),
         (lambda n: (lambda: contextlib.nullcontext(n)))(name))
        for name in files]
    M.read_metadata = lambda name: name
    M.summarise = lambda name: fake_info(*files[name])
    manifest = os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv")
    if os.path.exists(manifest):
        os.remove(manifest)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = M.main()
    return code or 0, out.getvalue(), os.path.exists(manifest)


print("--- a clean paired study passes ---")
# The live LS naming: the two marker passes of one slide differ by replicate
# letter, and LS53_2b-_A568 sits beside LS53_2c (AF488), so no uid repeats.
code, out, wrote = run({
    "LS53_2b-_A568.czi": (0.65, "AF568"),
    "LS53_2c.czi": (0.65, "AF488"),
    "LS45_8a.czi": (0.65, "AF568"),
    "LS45_8b.czi": (0.65, "AF488"),
})
chk("exit code is 0", code, 0)
chk("the manifest is written", wrote, True)

print("--- pixel size disagrees with the config ---")
code, out, wrote = run({
    "LS45_8a.czi": (0.65, "AF568"),
    "LS45_8b.czi": (0.325, "AF488"),
})
chk("exit code is non-zero", code != 0, True)
chk("the message names the file", "LS45_8b.czi" in out, True)
chk("...and its px_um", "0.325" in out, True)
chk("no manifest is written for downstream to read", wrote, False)

code, out, wrote = run({"LS45_8a.czi": (0.6515, "AF568")})
chk("0.23% off is header rounding, accepted", code, 0)
code, out, wrote = run({"LS45_8a.czi": (0.6536, "AF568")})
chk("0.55% off is refused", code != 0, True)
code, out, wrote = run({"LS45_8a.czi": (None, "AF568")})
chk("a file with no scale cannot be checked, refused", code != 0, True)

print("--- two files give the same scene uid ---")
code, out, wrote = run({
    "LS45_8b.czi": (0.65, "AF488"),
    "LS45_8b_A488.czi": (0.65, "AF488"),
})
chk("exit code is non-zero", code != 0, True)
chk("the message names the uid", "LS45_s08b_sc00" in out, True)
chk("...and both files", ("LS45_8b.czi" in out, "LS45_8b_A488.czi" in out), (True, True))
chk("no manifest is written for downstream to read", wrote, False)

# Across marker passes too: 01k's clip masks are one flat folder keyed by uid
# alone, so an AF568 and an AF488 file giving one uid still collide.
code, out, wrote = run({
    "LS53_2b-_A568.czi": (0.65, "AF568"),
    "LS53_2b-_A488.czi": (0.65, "AF488"),
})
chk("a repeat across the two marker passes is refused too", code != 0, True)

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
