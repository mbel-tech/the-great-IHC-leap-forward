"""section_order survives a re-scanned slide; a resumed QC row is current.

Two defects, one column:

  * 00_manifest numbered `section_order` per (animal, marker, replicate
    letter). A slide scanned again under another letter - `8c` beside
    `1b..9b` - became a series of its own and was numbered 1..k, colliding
    with slide 1. 01d sorts by section_order then slide and 04b by
    section_order alone, so the two slides interleaved.
  * 01_overviews and 01k resumed past rows already on disk without looking
    at the manifest again, so after 00 was re-run a kept row carried the old
    section_order / slide / variant, and a row whose uid had left the
    manifest was never removed.

The CZI reader is replaced by fakes, as in test_manifest_checks.py and
test_partial_runs.py, so no slide is read.

Run:  python tests/test_rescan_order.py
"""

import contextlib
import csv
import io
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

#: The live LS grammar, as pinned in tests/test_slide_naming.py.
LS = {
    "pattern": r"^(?P<subject>LS\d+)_(?P<slide>\d+)(?P<replicate>[a-z])"
               r"(?P<rescan>-?)(?P<pass>_[A-Za-z0-9]+)?\.czi$",
    "case_insensitive": False,
}
STUDY = use_temp_study(slide_naming=LS, pixel_size_um=0.65)
M = load_stage("00_manifest.py", name="lsstage_00_rescan")

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def write(path, rows, cols=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fake_info(px_um, marker, n):
    """What czi_meta.summarise returns, for an n-scene, two-channel file."""
    return {
        "size_x": 100, "size_y": 100, "size_c": 2, "size_s": n, "size_m": 1,
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
            for i in range(n)
        ],
    }


def run00(files):
    """00's main() over {filename: (px_um, marker, n_scenes)} -> (code, scene rows)."""
    M.discover_sources = lambda stack, skipped=None: [
        ("LOOSE", name, os.path.join(STUDY.source_dir, name),
         (lambda n: (lambda: contextlib.nullcontext(n)))(name))
        for name in files]
    M.read_metadata = lambda name: name
    M.summarise = lambda name: fake_info(*files[name])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = M.main()
    scenes = os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv")
    return code or 0, read(scenes), out.getvalue()


def orders_by_slide(rows):
    out = {}
    for r in rows:
        out.setdefault(int(r["slide"]), set()).add(int(r["section_order"]))
    return out


# ------------------------------------------------------------ 00: re-scans
print("--- 00: a re-scanned slide is numbered inside its slide's place ---")
# Slides 1..4 of one animal under `b`; slide 2 scanned again as `2c` with one
# section fewer, slide 3 only ever as `3c`. Every scan is the same marker.
FILES = {
    "LS45_1b.czi": (0.65, "AF488", 3),
    "LS45_2b.czi": (0.65, "AF488", 3),
    "LS45_2c.czi": (0.65, "AF488", 2),
    "LS45_3c.czi": (0.65, "AF488", 3),
    "LS45_4b.czi": (0.65, "AF488", 2),
}
code, rows, out = run00(FILES)
chk("00 returns 0", code, 0)
by_slide = orders_by_slide(rows)
slides = sorted(by_slide)
chk("no two slides share a section_order",
    all(by_slide[a].isdisjoint(by_slide[b])
        for i, a in enumerate(slides) for b in slides[i + 1:]))
ordered = sorted(rows, key=lambda r: int(r["section_order"]))
chk("sorting by section_order alone keeps slide order (04b)",
    [int(r["slide"]) for r in ordered], sorted(int(r["slide"]) for r in ordered))
chk("...and by (section_order, slide) too (01d)",
    [int(r["slide"]) for r in sorted(
        rows, key=lambda r: (int(r["section_order"]), int(r["slide"])))],
    sorted(int(r["slide"]) for r in rows))
# The series 02 will use by default: most scenes per slide, earlier letter.
chosen = [r for r in rows if (r["slide"], r["variant"]) != ("2", "c")]
chk("the default choice of scans runs 1..N without a gap",
    sorted(int(r["section_order"]) for r in chosen), list(range(1, 12)))
two_c = sorted(int(r["section_order"]) for r in rows if r["file"] == "LS45_2c.czi")
chk("the re-scan of slide 2 is numbered inside slide 2's places", two_c, [4, 5])

# 00 keeps its own copy of 02's scan choice. The two must agree: the scans 02's
# choose_variants picks are the ones that must run 1..N without a gap.
P2 = load_stage("02_pair_passes.py", name="lsstage_02_rescan")


def picked_by_02(rows):
    """The scene rows of the scans 02_pair_passes.choose_variants picks."""
    typed = [dict(r, slide=int(r["slide"])) for r in rows]
    with contextlib.redirect_stdout(io.StringIO()):
        picked, _ = P2.choose_variants(typed)
    return [r for scans in picked.values() for r in scans]


chk("default: 02's chosen scans are the ones 00 numbers 1..N",
    sorted(int(r["section_order"]) for r in picked_by_02(rows)), list(range(1, 12)))

print("--- 00: a chosen_variants.csv override is honoured ---")
CV = os.path.join(M.MANIFEST_DIR, "chosen_variants.csv")
try:
    write(CV, [{"animal": "LS45", "slide": "2",
                "marker_channel": rows[0]["marker_channel"], "variant": "c"}])
    code, rows, out = run00(FILES)
    chk("00 returns 0", code, 0)
    chosen = [r for r in rows if (r["slide"], r["variant"]) != ("2", "b")]
    chk("the chosen scans (2c for slide 2) run 1..N without a gap",
        sorted(int(r["section_order"]) for r in chosen), list(range(1, 11)))
    chk("slide 3 follows slide 2's chosen scan",
        sorted(int(r["section_order"]) for r in rows if r["slide"] == "3"), [6, 7, 8])
    p02 = picked_by_02(rows)
    chk("override: 02 picks 2c too",
        sorted({r["variant"] for r in p02 if r["slide"] == 2}), ["c"])
    chk("...and its chosen scans are the ones 00 numbers 1..N",
        sorted(int(r["section_order"]) for r in p02), list(range(1, 11)))

    # A person-written file missing a column is refused by both, naming the
    # file and the column - not a bare KeyError.
    write(CV, [{"animal": "LS45", "slide": "2", "variant": "c"}])
    for who, fn in (("00", M.chosen_variant_overrides),
                    ("02", lambda: P2.choose_variants([]))):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fn()
            said = "no refusal"
        except SystemExit as exc:
            said = str(exc)
        except KeyError as exc:
            said = f"KeyError {exc}"
        chk(f"{who}: chosen_variants.csv without marker_channel is refused by name",
            ("chosen_variants.csv" in said, "marker_channel" in said,
             said.startswith("KeyError")), (True, True, False))
finally:
    if os.path.exists(CV):
        os.remove(CV)

print("--- 00: two markers of one animal are separate series ---")
code, rows, out = run00({
    "LS45_1a.czi": (0.65, "AF568", 2),
    "LS45_2a.czi": (0.65, "AF568", 2),
    "LS45_1b.czi": (0.65, "AF488", 3),
    "LS45_2b.czi": (0.65, "AF488", 2),
})
chk("00 returns 0", code, 0)
# By file, not by marker name: the study may rename a scan's channel.
for letter, want in (("a", [1, 2, 3, 4]), ("b", [1, 2, 3, 4, 5])):
    chk(f"the {letter} marker runs 1..N on its own",
        sorted(int(r["section_order"]) for r in rows if r["variant"] == letter),
        want)
chk("...under two marker names", len({r["marker_channel"] for r in rows}), 2)


# ------------------------------------------------------- 01 / 01k: resume
class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class _Doc:
    scenes_bounding_rectangle = {s: _Rect(100 * s, 0, 320, 240) for s in range(3)}

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom):
        h, w = max(int(round(roi[3] * zoom)), 8), max(int(round(roi[2] * zoom)), 8)
        rng = np.random.default_rng(1000 * scene + plane["C"])
        a = rng.integers(200, 3000, size=(h, w)).astype(np.uint16)
        a[h // 4:h // 2, w // 3:w // 2] = 20000
        return a[None, :, :]


class _PyCzi:
    @staticmethod
    def open_czi(path):
        return _Doc(path)


def run_main(mod, argv):
    saved = sys.argv
    sys.argv = argv
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main()
    except SystemExit as exc:
        rc = exc.code
    finally:
        sys.argv = saved
    return rc, out.getvalue()


print()
print("--- 01: a resumed row takes the manifest's current identity ---")
sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
OV = load_stage("01_overviews.py", name="lsstage_01_rescan")
OV.pyczi = _PyCzi
FNAME = "LS01_1a.czi"
open(os.path.join(STUDY.source_dir, FNAME), "wb").close()
marker = list(OV.MASK_MARKERS)[0]
SCENES_CSV = os.path.join(OV.OUT_ROOT, "manifest", "manifest_scenes.csv")
scenes = [{"scene_uid": f"LS01_s01a_sc{s:02d}", "file": FNAME, "animal": "LS01",
           "slide": "1", "variant": "a", "marker_channel": marker,
           "scene_index": str(s), "section_order": str(s + 1)} for s in range(3)]
write(SCENES_CSV, scenes)
for p in (OV.QC_CSV, OV.SAT_RAW_CSV):
    if os.path.exists(p):
        os.remove(p)
zoom = OV.BASE_PX_UM / OV.TARGET_UM
ranges = {"DAPI": {"lo": 200, "hi": 20000}, marker: {"lo": 200, "hi": 20000}}
with contextlib.redirect_stdout(io.StringIO()):
    OV.export(scenes, {}, ranges, {}, zoom, 0, False)
chk("the first export wrote a row per section", len(read(OV.QC_CSV)), 3)

# 00 is run again: the first section's order moves, its slide and letter are
# relabelled, and the last section leaves the manifest.
GONE = scenes[2]["scene_uid"]
moved = [dict(scenes[0], section_order="7", slide="5", variant="z"), scenes[1]]
write(SCENES_CSV, moved)
said = io.StringIO()
with contextlib.redirect_stdout(said):
    exported, _f, n = OV.export(moved, {}, ranges, {}, zoom, 0, False)
chk("nothing is exported again", exported, 0)
for label, path in (("focus.csv", OV.QC_CSV), ("saturation_raw.csv", OV.SAT_RAW_CSV)):
    got = {r["scene_uid"]: r for r in read(path)}
    chk(f"{label}: the vanished uid's row is dropped", GONE in got, False)
    first = got[scenes[0]["scene_uid"]]
    chk(f"{label}: the kept row's identity is the manifest's",
        (first["section_order"], first["slide"], first["variant"]), ("7", "5", "z"))
    chk(f"{label}: an unchanged row is untouched",
        got[scenes[1]["scene_uid"]]["section_order"], "2")
chk("01 says how many rows it refreshed and dropped",
    "refreshed 1" in said.getvalue() and "dropped 1" in said.getvalue())

print()
print("--- 01k: a resumed row takes the manifest's current identity ---")
K = load_stage("01k_saturation_raw.py", name="lsstage_01k_rescan")
moved2 = [dict(moved[0], section_order="9")]
write(SCENES_CSV, moved2)
# 01 has not run since: saturation_raw.csv still has both rows.
rc, said = run_main(K, ["01k", "--native-sample", "0", "--no-merge"])
chk("01k returns 0", rc, 0)
got = {r["scene_uid"]: r for r in read(K.OUT_CSV)}
chk("saturation_raw.csv: the vanished uid's row is dropped",
    sorted(got), [scenes[0]["scene_uid"]])
chk("saturation_raw.csv: the kept row's order is the manifest's",
    got[scenes[0]["scene_uid"]]["section_order"], "9")
chk("01k says how many rows it refreshed and dropped",
    "refreshed 1" in said and "dropped 1" in said)

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
