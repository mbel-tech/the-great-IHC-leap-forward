"""ImageJ / Fiji ROI Manager files become an atlas plate set.

The .roi bytes are written here by a minimal encoder that follows ImageJ's
RoiEncoder layout, so the suite needs neither Fiji nor a ROI library. The
decoder was also checked against files written by the independent `roifile`
package when it was written.

Run:  python tests/test_atlas_imagej.py
"""

import csv
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
os.environ["LS_ATLAS_BUNDLE"] = ""

import ls_atlas_imagej as IJ  # noqa: E402
import ls_atlas_import as IMP  # noqa: E402

FAILS = []


def chk(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got {got!r}\n     want {want!r}"))
    if not ok:
        FAILS.append(name)


def roi_bytes(points, name="", kind=0):
    """An ImageJ .roi: version 228, integer coordinates, the name in header 2."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    top, left, bottom, right = min(ys), min(xs), max(ys), max(xs)
    n = len(points)
    head = bytearray(64)
    head[0:4] = b"Iout"
    struct.pack_into(">hBxhhhhH", head, 4, 228, kind, top, left, bottom, right, n)
    body = b"".join(struct.pack(">h", x - left) for x in xs)
    body += b"".join(struct.pack(">h", y - top) for y in ys)
    h2_off = 64 + len(body)
    name_off = h2_off + 64
    struct.pack_into(">i", head, 60, h2_off)
    h2 = bytearray(64)
    struct.pack_into(">ii", h2, 16, name_off, len(name))
    return bytes(head) + body + bytes(h2) + name.encode("utf-16-be")


def write_zip(path, rois):
    with zipfile.ZipFile(path, "w") as z:
        for i, (name, pts) in enumerate(rois):
            z.writestr("%04d.roi" % i, roi_bytes(pts, name))


tmp = tempfile.mkdtemp()
try:
    src = os.path.join(tmp, "my_atlas")
    os.makedirs(src)
    for stem in ("m_2", "m_10"):                        # natural order: 2 before 10
        Image.new("RGB", (200, 100), "white").save(os.path.join(src, stem + ".png"))
    write_zip(os.path.join(src, "m_2.zip"),
              [("Dm", [(10, 10), (90, 10), (90, 90), (10, 90)]),
               ("Dl", [(110, 10), (190, 10), (190, 90)])])
    write_zip(os.path.join(src, "m_10.zip"), [("Vv", [(20, 20), (60, 20), (40, 80)])])

    name, pts = IJ.read_roi(roi_bytes([(1, 2), (5, 2), (5, 9)], "TeO"))
    chk("a polygon ROI's name and points", (name, pts), ("TeO", [(1.0, 2.0), (5.0, 2.0), (5.0, 9.0)]))

    out = os.path.join(tmp, "set")
    rows, problems = IJ.build(src, out, "mouse")
    chk("builds with no problems", problems, [])
    chk("plates in natural file order", [(r["plate_id"], r["image_file"]) for r in rows],
        [("mouse_001", "m_2.png"), ("mouse_002", "m_10.png")])
    with open(os.path.join(out, "polygons.csv"), newline="", encoding="utf-8") as fh:
        polys = list(csv.DictReader(fh))
    chk("every outline is reviewed", {p["status"] for p in polys}, {"reviewed"})
    chk("regions keep their ROI names", sorted(p["region"] for p in polys), ["Dl", "Dm", "Vv"])
    dm = next(p for p in polys if p["region"] == "Dm")
    chk("vertices are fractions of the plate", dm["vertices"].split(";")[1], "0.45000 0.10000")
    rep = IMP.check(out, "polygons")
    chk("the result meets the plate-set contract", rep.errors, [])

    # What it refuses, with the file named.
    bad = os.path.join(tmp, "bad")
    os.makedirs(bad)
    Image.new("RGB", (50, 50)).save(os.path.join(bad, "a.png"))
    Image.new("RGB", (50, 50)).save(os.path.join(bad, "b.png"))
    write_zip(os.path.join(bad, "a.zip"), [("0012-0034", [(1, 1), (9, 1), (9, 9)])])
    _, problems = IJ.build(bad, os.path.join(tmp, "bad_set"), "x")
    chk("an automatic ROI name is refused", any("automatic name" in p for p in problems), True)
    chk("a plate without regions is named", any(p.startswith("b.png has no regions") for p in problems), True)
    chk("nothing is written when it refuses", os.path.exists(os.path.join(tmp, "bad_set")), False)
    line = bytearray(roi_bytes([(0, 0), (5, 5)], "ln", kind=3))
    try:
        IJ.read_roi(bytes(line))
        chk("a line ROI is refused", False, True)
    except IJ.RoiError as e:
        chk("a line ROI is refused", "not an area" in str(e), True)

    # --name imports it into the study.
    study = os.path.join(tmp, "study")
    os.makedirs(study)
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w") as fh:
        json.dump({"out_root": study}, fh)
    code = IJ.main([src, "--name", "mouse", "--out", os.path.join(tmp, "set2"), "--config", cfg_path])
    chk("--name builds and imports", code, 0)
    with open(cfg_path) as fh:
        cfg = json.load(fh)
    chk("the study now uses it", (cfg.get("atlas_source"), cfg["atlases"]["mouse"]["region_kind"]),
        ("mouse", "polygons"))
    chk("its plates are in the study", sorted(os.listdir(os.path.join(study, "atlas", "plates_mouse"))),
        ["m_10.png", "m_2.png", "plates.csv", "polygons.csv", "seeds.csv"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
