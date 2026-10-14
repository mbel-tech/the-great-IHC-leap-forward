"""Turn atlas plates with regions drawn in ImageJ / Fiji into an atlas the app can use.

The simple way to bring your own atlas: one image per plate, and for each plate
the regions drawn in Fiji's ROI Manager and saved next to it under the same name.

    my_atlas/
        mouse_001.png      a plate (png, jpg or tif)
        mouse_001.zip      its regions: ROI Manager > More > Save...
        mouse_002.png
        mouse_002.zip
        ...

Each ROI's NAME is the region it outlines (ROI Manager > Rename...). Polygon,
freehand, traced, rectangle and oval ROIs are read; lines, points and composite
shapes are refused with the plate and ROI named, so nothing is dropped quietly.
A ROI still carrying ImageJ's automatic name ("0123-0456") is refused too: that
name would become a measurement key.

This writes the plate-set folder `ls_atlas_import` checks and registers
(plates.csv, polygons.csv, seeds.csv and the images), with every outline
marked `reviewed` because a person drew it. With --name it also imports it into
the active study in the same step. Plates are taken in file-name order, so name
them so they sort front to back. See docs/adding-your-own-atlas.md.

    python scripts/ls_atlas_imagej.py my_atlas --name mouse            # build + import
    python scripts/ls_atlas_imagej.py my_atlas --out my_atlas_set      # build only
"""

import argparse
import csv
import io
import math
import os
import re
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import atlas_polygons as POLY                                   # noqa: E402

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
PLATE_KEYS = ["plate_id", "page", "image_file", "px_w", "px_h", "n_seeds", "regions"]
SEED_HEADER = ["plate_id", "page", "plate_seq", "region", "region_raw", "is_unknown",
               "colour_hex", "from_raster", "x_px", "y_px", "x_frac", "y_frac"]

# ImageJ's ROI file layout (ij/io/RoiDecoder.java). Big-endian throughout.
_TYPES = {0: "polygon", 1: "rect", 2: "oval", 3: "line", 4: "freeline",
          5: "polyline", 6: "noroi", 7: "freehand", 8: "traced", 9: "angle", 10: "point"}
_AREAS = ("polygon", "rect", "oval", "freehand", "traced")
_SUB_PIXEL = 128
_DEFAULT_NAME = re.compile(r"^\d{4}-\d{4}(-\d{4})?(-\d+)?$")
OVAL_POINTS = 72


class RoiError(ValueError):
    """A ROI that cannot become a region outline, in words a person can act on."""


def read_roi(data, label="ROI"):
    """(name, [(x, y), ...]) in image pixels from one ImageJ .roi file's bytes."""
    if len(data) < 64 or data[:4] != b"Iout":
        raise RoiError("%s is not an ImageJ ROI file" % label)

    def u16(o):
        return struct.unpack(">H", data[o:o + 2])[0]

    def i16(o):
        return struct.unpack(">h", data[o:o + 2])[0]

    def i32(o):
        return struct.unpack(">i", data[o:o + 4])[0]

    def f32(o):
        return struct.unpack(">f", data[o:o + 4])[0]

    version = u16(4)
    kind = _TYPES.get(data[6], "unknown")
    top, left, bottom, right = i16(8), i16(10), i16(12), i16(14)
    n = u16(16)
    if n == 0 and version >= 228:
        n = i32(18)
    options = u16(50)

    name = ""
    header2 = i32(60) if version >= 218 else 0
    if 0 < header2 and header2 + 24 <= len(data):
        off, length = i32(header2 + 16), i32(header2 + 20)
        if off > 0 and length > 0 and off + 2 * length <= len(data):
            name = data[off:off + 2 * length].decode("utf-16-be")
    shown = "%s %r" % (label, name) if name else label

    if kind == "rect" and i32(36) > 0:
        raise RoiError("%s is a composite shape (made with AND/OR/XOR); split it into "
                       "single outlines" % shown)
    if kind not in _AREAS:
        raise RoiError("%s is a %s, not an area; draw regions with the polygon, freehand, "
                       "rectangle or oval tool" % (shown, kind))

    if kind == "rect":
        pts = [(left, top), (right, top), (right, bottom), (left, bottom)]
    elif kind == "oval":
        cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
        rx, ry = (right - left) / 2.0, (bottom - top) / 2.0
        pts = [(cx + rx * math.cos(2 * math.pi * k / OVAL_POINTS),
                cy + ry * math.sin(2 * math.pi * k / OVAL_POINTS)) for k in range(OVAL_POINTS)]
    else:
        base = 64
        if version >= 222 and options & _SUB_PIXEL:
            fbase = base + 4 * n
            pts = [(f32(fbase + 4 * k), f32(fbase + 4 * (n + k))) for k in range(n)]
        else:
            pts = [(left + i16(base + 2 * k), top + i16(base + 2 * (n + k))) for k in range(n)]
    return name, [(float(x), float(y)) for x, y in pts]


def read_rois(path):
    """[(name, points)] from a .roi file or a ROI Manager .zip, in saved order."""
    if path.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(path) as z:
            for entry in z.namelist():
                if entry.lower().endswith(".roi"):
                    label = "%s: %s" % (os.path.basename(path), entry)
                    name, pts = read_roi(z.read(entry), label)
                    out.append((name or os.path.splitext(os.path.basename(entry))[0], pts))
        return out
    with open(path, "rb") as fh:
        name, pts = read_roi(fh.read(), os.path.basename(path))
    return [(name or os.path.splitext(os.path.basename(path))[0], pts)]


def _natural(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def find_plates(src):
    """[(image_file, roi_file or None)] in file-name order."""
    files = os.listdir(src)
    out = []
    for f in sorted(files, key=_natural):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in IMAGE_EXTS:
            continue
        roi = next((c for c in (stem + ".zip", stem + ".roi", stem + "_RoiSet.zip",
                                "RoiSet_" + stem + ".zip") if c in files), None)
        out.append((f, roi))
    return out


def build(src, out, prefix):
    """Write the plate-set folder `out` from `src`. Returns (plates, problems).

    `problems` lists what stops it, one line each; nothing is written then.
    """
    from PIL import Image

    plates, problems, rows, polys_by_plate = find_plates(src), [], [], {}
    if not plates:
        problems.append("no plate images (%s) in %s" % (", ".join(IMAGE_EXTS), src))
    if not re.match(r"^[a-z][a-z0-9_]*$", prefix):
        problems.append("the plate id prefix %r must be lower-case letters, digits and _" % prefix)
    for seq, (image, roi_file) in enumerate(plates, 1):
        pid = "%s_%03d" % (prefix, seq)
        try:
            with Image.open(os.path.join(src, image)) as im:
                w, h = im.size
        except OSError:
            problems.append("%s cannot be opened as an image" % image)
            continue
        rows.append({"plate_id": pid, "page": seq, "image_file": image, "px_w": w,
                     "px_h": h, "n_seeds": 0, "regions": ""})
        if roi_file is None:
            problems.append("%s has no regions: save its ROI Manager as %s.zip beside it"
                            % (image, os.path.splitext(image)[0]))
            continue
        try:
            rois = read_rois(os.path.join(src, roi_file))
        except (RoiError, zipfile.BadZipFile, OSError) as e:
            problems.append(str(e))
            continue
        polys = []
        for name, pts in rois:
            name = name.strip()
            if _DEFAULT_NAME.match(name):
                problems.append("%s: ROI %r still has ImageJ's automatic name; rename it to the "
                                "region it outlines" % (roi_file, name))
                continue
            if len(pts) < 3:
                problems.append("%s: ROI %r has fewer than 3 points" % (roi_file, name))
                continue
            off = [(x, y) for x, y in pts if not (-0.5 <= x <= w + 0.5 and -0.5 <= y <= h + 0.5)]
            if off:
                problems.append("%s: ROI %r reaches off the %dx%d plate (was it drawn on a "
                                "resized copy?)" % (roi_file, name, w, h))
                continue
            v = [[min(max(x / w, 0.0), 1.0), min(max(y / h, 0.0), 1.0)] for x, y in pts]
            polys.append({"roi_number": 0, "region": name, "status": "reviewed",
                          "label_conf": 1.0, "v": v})
        POLY.renumber(polys)
        polys_by_plate[pid] = polys
        rows[-1]["regions"] = "|".join(sorted({p["region"] for p in polys}))
    if problems:
        return rows, problems

    tmp = out + ".building"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for r in rows:
        shutil.copy2(os.path.join(src, r["image_file"]), os.path.join(tmp, r["image_file"]))
    with open(os.path.join(tmp, "plates.csv"), "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, PLATE_KEYS)
        wr.writeheader()
        wr.writerows(rows)
    POLY.write_polygons(os.path.join(tmp, "polygons.csv"), polys_by_plate)
    with open(os.path.join(tmp, "seeds.csv"), "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(SEED_HEADER)
    if os.path.exists(out):
        shutil.rmtree(tmp, ignore_errors=True)
        return rows, ["%s already exists; choose another --out" % out]
    os.rename(tmp, out)
    return rows, []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", help="folder of plate images, each with its ROI Manager .zip beside it")
    ap.add_argument("--name", help="import it into the active study under this name "
                                   "(lower-case letters, digits, _)")
    ap.add_argument("--label", help="a readable name for menus")
    ap.add_argument("--mirror", action="store_true",
                    help="the regions cover one hemisphere and may be mirrored about the midline")
    ap.add_argument("--out", help="where to write the plate set (default: <src>/plate_set)")
    ap.add_argument("--config", help="the study's config file (default: the active study)")
    a = ap.parse_args(argv)

    out = a.out or os.path.join(a.src, "plate_set")
    rows, problems = build(a.src, out, a.name or "plate")
    if problems:
        print("not built - fix these in Fiji and run again:")
        for p in problems:
            print("  - " + p)
        return 1
    n = sum(len(r["regions"].split("|")) for r in rows if r["regions"])
    print("%d plates, %d regions -> %s" % (len(rows), n, out))
    if not a.name:
        print("  (give --name to import it into the study)")
        return 0

    import ls_atlas_import as IMP
    import ls_config as LC
    try:
        res = IMP.import_atlas(LC.resolve_path(a.config), out, a.name, "polygons", a.label,
                               mirror_allowed=a.mirror)
    except ValueError as e:
        print("built, but not imported: %s" % e)
        return 1
    print(res["report"].text())
    print("imported %r into %s and selected it as the study's atlas" % (res["name"], res["dest"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
