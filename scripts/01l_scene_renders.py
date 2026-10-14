"""Stage 1l - a picture of every scene on every slide, straight from the CZI, for looking at.

The overviews (01) are 5.20 um/px and the curation composites (04o) are cut,
tilted and squashed into the reformatted frame. Neither is the plain answer to
"show me what is on this slide": every scene, the way it was scanned, sharp
enough to see nuclei. This is that, and nothing reads it - it exists to be
browsed in a file viewer.

For each scene of each CZI in the manifest it writes, under
`scene_renders/<marker>/`:

  stain+<counterstain>/<scene_uid>.png   the marker in its colour over the
                                         counterstain in its own - the same
                                         colours and the same MAX rule as the
                                         04o composites
  stain_only/<scene_uid>.png             the marker alone, in grey: grey uses
                                         all of the screen's brightness, where
                                         red or green alone uses a third of it,
                                         so faint signal stays readable

and one row per scene in `scene_renders/scene_renders.csv` saying which
picture is which and how it was drawn.

How the pixels are made
-----------------------

At `--um-px` (default 2.6, finest 1.3) from the CZI's own 16-bit planes,
always naming the scene (czi_read). The native 0.65 um/px pixels are read in
bands of rows and block-averaged down before the next band is fetched - 04o's
`read_region`, borrowed rather than restated - so the memory used is one band
plus the finished picture, whatever the scene's size. Block averaging is why
the picture cannot alias: every native pixel contributes, none is skipped.
The resolution is therefore a whole number of native pixels: 2.6 is 4x4
blocks, 1.3 is 2x2. A value in between is rounded to the nearest whole block
and the CSV records what was drawn.

The tile-field correction is applied as 01 applies it, when 01f has built one.

Brightness: `--range dataset` (the default) uses 01's frozen display range,
`qc/display_ranges.json`, so the same brightness means the same signal on
every picture - the reason the overviews froze theirs. At 2.6 um/px a nucleus
is no longer averaged with the neuropil around it, so the brightest ones can
saturate under a range set at 5.2. `--range section` stretches each scene to
its own tissue instead (01's percentiles, 1 to 99.5, inside 01's log-space
tissue mask): every picture well exposed, but brightness no longer compares
between pictures. Without a display_ranges.json the section range is used and
the CSV says so.

Nothing here is measured. 05c reads native pixels from the CZI, whatever these
pictures look like.

Run:  python 01l_scene_renders.py
      python 01l_scene_renders.py --um-px 1.3 --marker AF488
      python 01l_scene_renders.py --range section --force --limit 10
"""

import argparse
import csv
import importlib.util
import json
import os
import sys
import time

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402
import czi_read as CR  # noqa: E402

OUT_ROOT = CONFIG["out_root"]
SOURCE_DIR = CONFIG["source_dir"]
NATIVE_UM = float(CONFIG["pixel_size_um"])
MARKERS = list(CH.marker_names(CONFIG))

MANIFEST_CSV = os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv")
QC_DIR = os.path.join(OUT_ROOT, "qc")
RANGES_PATH = os.path.join(QC_DIR, "display_ranges.json")
FIELD_DIR = os.path.join(QC_DIR, "flatfield")
OUT_DIR = os.path.join(OUT_ROOT, "scene_renders")
INDEX_CSV = os.path.join(OUT_DIR, "scene_renders.csv")

DEFAULT_UM_PX = 2.6
#: The finest picture made. Native 0.65 um/px is a picture 4x the area of 1.3,
#: and on the operator's laptop it ran out of memory - in the PNG encoder and
#: in whatever opens it, not just here.
MIN_UM_PX = 1.3
#: Native rows per read. Smaller than 04o's 2048 because a whole scene is wider
#: than a cut section; at 1024 rows a band of a 20,000 px wide scene is 40 MB.
BAND_ROWS = 1024

ONLY_DIR = "stain_only"

INDEX_KEYS = ["scene_uid", "file", "animal", "slide", "variant",
              "marker_channel", "scene_index", "section_order",
              "um_per_px", "block", "width", "height", "range",
              "nuclear_lo", "nuclear_hi", "marker_lo", "marker_hi",
              "tilefield_applied", "stain_counterstain", "stain_only",
              "reader"]


def _load(script, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 04o owns the banded block-mean read, the composite and which plane is which.
# Borrowed so these pictures and the curation composites cannot drift apart in
# colour or in how a plane is read.
SR = _load("04o_section_rgb.py", "_01l_section_rgb")
MARKER_COLOURS = CH.marker_colours(CONFIG)
NUCLEAR_COLOUR = CH.nuclear_colour(CONFIG)


def nuclear_name():
    """The counterstain's name, for the folder: DAPI unless the study says."""
    chans = CH.parse((CONFIG.get("acquisition") or {}).get("channels"))
    nuc = CH.nuclear(chans)
    return nuc.name if nuc is not None and nuc.name else "DAPI"


def block_for(um_px, native=NATIVE_UM):
    """Native pixels per side of one output pixel: the nearest whole number."""
    return max(1, int(round(um_px / native)))


def tissue_threshold(image):
    """01's log-space Otsu - see 01_overviews.tissue_threshold."""
    sample = image[::3, ::3].ravel()
    positive = sample[sample > 0].astype(np.float64)
    if positive.size < 500:
        return float(image.max())
    hist, edges = np.histogram(np.log1p(positive), bins=512)
    hist = hist.astype(np.float64)
    centres = (edges[:-1] + edges[1:]) / 2.0
    w1, w2 = np.cumsum(hist), np.cumsum(hist[::-1])[::-1]
    valid = (w1 > 0) & (w2 > 0)
    m1 = np.cumsum(hist * centres) / np.maximum(w1, 1e-9)
    m2 = (np.cumsum((hist * centres)[::-1]) / np.maximum(w2[::-1], 1e-9))[::-1]
    var = w1 * w2 * (m1 - m2) ** 2
    var[~valid] = -1
    return float(np.expm1(centres[int(np.argmax(var))]))


def apply_field(plane, field, b):
    """Divide out 01f's tile field, at each block's centre in the tile fold.

    `plane` is block-averaged b x b from the scene origin, so output pixel j
    covers native [j*b, (j+1)*b) and its centre is j*b + (b - 1)/2 - the
    position 01 folds when it corrects the overview.
    """
    if field is None:
        return plane
    n = field.shape[0]

    def idx(length):
        native = np.arange(length, dtype=np.float64) * b + (b - 1) / 2.0
        return (np.round((native % SR.PITCH_PX) / SR.FIELD_DIVISOR).astype(np.int64)) % n

    gain = field[np.ix_(idx(plane.shape[0]), idx(plane.shape[1]))].astype(np.float32)
    out = plane.astype(np.float32)
    ok = gain > 0.05
    out[ok] /= gain[ok]
    np.clip(out, 0, 65535, out=out)
    return out


def read_scene(read, rect, b):
    """The whole scene rectangle, block-averaged b x b, as float32."""
    rx, ry, rw, rh = rect
    got = SR.read_region(read, (rx, ry, rx + rw - 1, ry + rh - 1), rect, b,
                         band_rows=max(b, BAND_ROWS // b * b))
    if got is None:
        return None
    return np.asarray(got[0], dtype=np.float32)


def section_range(values, tissue):
    """01's percentiles, 1 and 99.5, on this scene's own tissue."""
    inside = values[tissue] if tissue is not None and tissue.sum() >= 500 else values[values > 0]
    if inside.size < 50:
        return float(values.min()), float(values.max())
    return float(np.percentile(inside, 1.0)), float(np.percentile(inside, 99.5))


def to_8bit(values, lo, hi):
    hi = hi if hi > lo else lo + 1
    out = (values.astype(np.float32) - lo) * (255.0 / (hi - lo))
    np.clip(out, 0, 255, out=out)
    return np.rint(out).astype(np.uint8)


def load_ranges():
    if not os.path.exists(RANGES_PATH):
        return None
    with open(RANGES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_fields(indices):
    fields = {}
    for idx in set(indices):
        p = os.path.join(FIELD_DIR, f"tilefield_c{idx}.npy")
        if os.path.exists(p):
            fields[idx] = np.load(p)
    return fields


def load_index():
    if not os.path.exists(INDEX_CSV):
        return {}
    with open(INDEX_CSV, newline="", encoding="utf-8") as fh:
        return {r["scene_uid"]: r for r in csv.DictReader(fh)}


def save_png(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with IO.atomic_save(path) as tmp:
        Image.fromarray(img).save(tmp, format="PNG")


def render_scene(read_nuc, read_mark, rect, b, fields_nm, ranges, range_mode,
                 marker, colour):
    """(composite rgb, stain-only grey, row facts) for one scene, or None."""
    nf, mf = fields_nm
    nuc = read_scene(read_nuc, rect, b)
    if nuc is None:
        return None
    nuc = apply_field(nuc, nf, b)
    tissue = nuc > tissue_threshold(nuc) if range_mode == "section" else None

    if range_mode == "dataset":
        n_lo, n_hi = ranges["DAPI"]["lo"], ranges["DAPI"]["hi"]
        m_lo, m_hi = ranges[marker]["lo"], ranges[marker]["hi"]
    else:
        n_lo, n_hi = section_range(nuc, tissue)
    nuc8 = to_8bit(nuc, n_lo, n_hi)
    del nuc

    mark = apply_field(read_scene(read_mark, rect, b), mf, b)
    if range_mode == "section":
        m_lo, m_hi = section_range(mark, tissue)
    mark8 = to_8bit(mark, m_lo, m_hi)
    del mark

    rgb = SR.composite(mark8, nuc8, colour, NUCLEAR_COLOUR)
    facts = {"nuclear_lo": "%.0f" % n_lo, "nuclear_hi": "%.0f" % n_hi,
             "marker_lo": "%.0f" % m_lo, "marker_hi": "%.0f" % m_hi,
             "width": mark8.shape[1], "height": mark8.shape[0]}
    return rgb, mark8, facts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--um-px", type=float, default=DEFAULT_UM_PX, metavar="UM",
                    help=f"picture resolution in um per pixel (default {DEFAULT_UM_PX}; "
                         f"finest {MIN_UM_PX}). Rounded to whole native "
                         f"{NATIVE_UM} um pixels")
    ap.add_argument("--range", choices=["dataset", "section"], default="dataset",
                    help="dataset: 01's frozen brightness range, so pictures compare "
                         "(default). section: each scene stretched to its own tissue")
    ap.add_argument("--marker", choices=MARKERS or None, default=None,
                    help="only the scans of this marker (default: every marker)")
    ap.add_argument("--force", action="store_true",
                    help="redraw pictures that already exist (default skips them)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="stop after N scenes (0 = all)")
    args = ap.parse_args()

    if args.um_px < MIN_UM_PX - 1e-9:
        raise SystemExit(
            f"--um-px {args.um_px}: the finest picture this stage makes is "
            f"{MIN_UM_PX} um/px. A whole scene at the native {NATIVE_UM} is four "
            f"times the pixels again - more than the laptop holds - and nothing "
            f"is measured from these pictures anyway.")
    b = block_for(args.um_px)
    um_px = b * NATIVE_UM
    if abs(um_px - args.um_px) > 1e-6:
        print(f"{args.um_px} um/px rounded to {um_px:.2f} ({b}x{b} native pixels)")

    try:
        from pylibCZIrw import czi as pyczi
    except ImportError:
        raise SystemExit("this stage reads the CZIs and needs pylibCZIrw - run it with "
                         "work/czienv, the interpreter 01 runs on.")

    with open(MANIFEST_CSV, newline="", encoding="utf-8") as fh:
        scenes = [r for r in csv.DictReader(fh)
                  if not args.marker or r.get("marker_channel") == args.marker]

    range_mode = args.range
    ranges = load_ranges() if range_mode == "dataset" else None
    if range_mode == "dataset" and ranges is None:
        print(f"no {RANGES_PATH} yet (01 writes it) - stretching each scene to "
              f"its own tissue instead")
        range_mode = "section"

    nuc_dir = "stain+" + nuclear_name()
    index = load_index()
    by_file = {}
    for r in scenes:
        by_file.setdefault(r["file"], []).append(r)

    made = existing = 0
    failed = []
    t0 = time.time()
    fields_cache = {}
    for fname in sorted(by_file):
        rows = sorted(by_file[fname], key=lambda r: int(r["scene_index"]))
        marker = rows[0]["marker_channel"]
        colour = MARKER_COLOURS.get(marker)
        if colour is None:
            failed += [(r["scene_uid"], f"no display colour for {marker}") for r in rows]
            continue
        if range_mode == "dataset" and ("DAPI" not in ranges or marker not in ranges):
            failed += [(r["scene_uid"], f"{RANGES_PATH} has no range for {marker} - "
                        "use --range section") for r in rows]
            continue
        n_idx, m_idx = SR.plane_indices(marker)
        if (n_idx, m_idx) not in fields_cache:
            f = load_fields((n_idx, m_idx))
            fields_cache[(n_idx, m_idx)] = (f.get(n_idx), f.get(m_idx))
        fields_nm = fields_cache[(n_idx, m_idx)]
        mdir = os.path.join(OUT_DIR, marker)

        todo = []
        for r in rows:
            uid = r["scene_uid"]
            both = [os.path.join(mdir, nuc_dir, uid + ".png"),
                    os.path.join(mdir, ONLY_DIR, uid + ".png")]
            if not args.force and all(os.path.exists(p) for p in both):
                existing += 1
                continue
            todo.append(r)
        if args.limit:
            todo = todo[:max(0, args.limit - made)]
        if not todo:
            continue

        path = os.path.join(SOURCE_DIR, fname)
        if not os.path.exists(path):
            failed += [(r["scene_uid"], f"CZI not found: {path}") for r in todo]
            continue
        drawn = set()
        try:
            with pyczi.open_czi(path) as doc:
                rects = {int(k): (int(v.x), int(v.y), int(v.w), int(v.h))
                         for k, v in doc.scenes_bounding_rectangle.items()}
                for r in todo:
                    uid, si = r["scene_uid"], int(r["scene_index"])
                    if si not in rects:
                        failed.append((uid, f"scene {si} absent from {fname}"))
                        continue

                    def reader(idx, si=si):
                        return lambda x, y, w, h: CR.read_plane(
                            doc, SR.GEO._Rect(x, y, w, h), idx, scene=si)

                    got = render_scene(reader(n_idx), reader(m_idx), rects[si], b,
                                       fields_nm, ranges, range_mode, marker, colour)
                    if got is None:
                        failed.append((uid, "empty scene rectangle"))
                        continue
                    rgb, only, facts = got
                    p_rgb = os.path.join(mdir, nuc_dir, uid + ".png")
                    p_only = os.path.join(mdir, ONLY_DIR, uid + ".png")
                    save_png(rgb, p_rgb)
                    save_png(only, p_only)
                    index[uid] = {
                        "scene_uid": uid, "file": fname, "animal": r.get("animal", ""),
                        "slide": r.get("slide", ""), "variant": r.get("variant", ""),
                        "marker_channel": marker, "scene_index": si,
                        "section_order": r.get("section_order", ""),
                        "um_per_px": "%.2f" % um_px, "block": b, "range": range_mode,
                        "tilefield_applied": int(fields_nm[1] is not None),
                        "stain_counterstain": os.path.relpath(p_rgb, OUT_ROOT),
                        "stain_only": os.path.relpath(p_only, OUT_ROOT),
                        "reader": "pylibCZIrw", **facts}
                    drawn.add(uid)
                    made += 1
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            failed += [(r["scene_uid"], f"{type(exc).__name__}: {exc}") for r in todo
                       if r["scene_uid"] not in drawn]
            print(f"\n  !! {fname}: {exc}")

        # Checkpoint per file, as 01 does: this runs for a long time against a
        # drive that has dropped writes before.
        if index:
            IO.atomic_write_csv(INDEX_CSV, list(index.values()), INDEX_KEYS)
        rate = made / max(time.time() - t0, 1e-9)
        print(f"\r  drew {made} scenes  ({rate:.2f}/s, "
              f"{(time.time() - t0) / 60:.1f} min)", end="")
        if args.limit and made >= args.limit:
            break

    print()
    print(f"wrote {made} scenes at {um_px:.2f} um/px ({range_mode} range) to {OUT_DIR}")
    if existing:
        print(f"  {existing} already there, left alone (--force redraws them)")
    if failed:
        print(f"  {len(failed)} not drawn:")
        for uid, why in failed[:10]:
            print(f"    {uid}  {why}")


if __name__ == "__main__":
    main()
