"""Stage 6i - pictures of every nucleus found, positives filled in. For checking.

The numbers in roi_measurements.csv say how many nuclei each ROI holds and how
many of them 06a called positive. Nothing in a table shows WHICH ones: whether
StarDist found the nuclei a person would, whether the disc sits where it was
meant to, whether the positives are the bright ones. This draws that, over the
marker channel in grey, so the counts can be looked at.

For each section 05c measured (the marker's done log, or rows in
roi_nuclei.csv) it writes, under `<out_root>/detection_overlays/<marker>/<animal>/`:

  <marker>_<scene_uid>_scene.png              the whole scene
  <marker>_<scene_uid>_roi<NN>_<region>.png   one crop per ROI
  <marker>_<scene_uid>_bg<NN>.png             one crop per background disc

Each crop is the box 05c segmented, at native pixels (nothing resampled, nothing
blanked), with a black strip stacked ABOVE it - never over it - holding the
identity, the counts, the colour legend and a 50 um bar (a shorter one,
labelled with its own length, on a crop too narrow for 50 um; none on one
too narrow for 10 um). Colours and the positivity rule are ls_overlay's,
which 06a counts by:

  positive     magenta 40% fill + magenta outline (counted, above the cut)
  censored     magenta fill + white outline (counted positive: clipped pixels)
  negative     cyan outline (counted, not above the cut)
  outside ROI  yellow outline (in the box, centroid outside the shape)
  artifact     orange outline (not counted)
  off tissue   blue outline (not counted)

and the ROI shape itself as a 1-px white line. A section with no cut (too few
background nuclei) calls nothing: every counted nucleus, censored or not, is
drawn cyan and the strip says "no cut: too few background nuclei".
Brightness is a viewing stretch only: 1st..99.8th percentile over every box
of the section, the same for all of a section's pictures so they compare.

The scene picture is the scene's whole rectangle as scanned, native pixels,
nothing blanked (a tile never scanned reads 0: black), with every box's
nuclei and shape drawn exactly as in its crop and a small white-on-black tag
with the box's number just above-left of it - the one thing drawn over
image pixels. Its strip gives the totals over the section's ROIs (background
discs are not ROIs) and a line per ROI, with a 500 um bar. It is far too big
to hold, so it is read, drawn and written BAND_ROWS rows at a time.

A picture that disagrees with the numbers is worse than none
------------------------------------------------------------

Outlines come from the label images 05c saved (detection_outlines/). A section
measured before 05c saved them is segmented again here, with 05c's own
`segment_box` and the CURRENT settings - and only when those are the settings
results/detection_settings.json says the rows were measured with; otherwise the
section is not drawn ("detection settings changed since measured"). The
re-segmented outlines are saved, so that is paid once.

Before anything is drawn, every ROI's outlines are checked against
roi_nuclei.csv one to one: the labels whose centroid maps inside the shape must
be exactly the rows' nucleus_ids, each centroid within 0.5 px of the row's. The
counts drawn must then equal 06a's n_nuclei / n_positive / n_censored for that
ROI. A section that fails either is not drawn - its old pictures are removed -
and gets one row in `detection_overlays/failures.csv` with the reason, and the
run ends with "<N> not drawn: see <failures.csv>".

`detection_overlays/index.csv` has one row per picture (keyed by its path
under detection_overlays/) with the counts drawn and a fingerprint of what
its pictures are drawn from: the section's own roi_nuclei.csv rows, its exact
cut, its box rows, its geometry row, the outline file, its fish and slide,
and of roi_measurements.csv only the section's columns the pictures show
(counts, %, cut, region) - not the study-wide Abercrombie factors or
densities, which change whenever any section is measured. A rerun skips a
section whose pictures carry the fingerprint it has now; `--force` redraws
them all.

Needs 06a: refused (exit 2) when roi_measurements.csv is missing or older than
roi_nuclei.csv, since the cut and the counts printed come from it. Not in the
run order - drawn on request.

`--no-scenes` / `--no-crops` skip one kind of picture: it is neither drawn
nor removed, and the other kind is redrawn only when out of date.

Crops 05c drew
--------------

05c draws each section's crops as it measures it (05c's CropDrawer, the same
functions as here) and lists them in `detection_overlays/index_05c.csv`, which
this stage reads and never writes. A crop listed there is kept as it is - not
redrawn, not re-segmented - and entered in index.csv when its data fingerprint
(ls_overlay.data_fingerprint: the section's rows, the exact cut in
positivity_cuts.csv, its box rows, geometry row, outline file, fish and slide)
equals the one computed now, and its counts, % positive and cut as printed
equal 06a's row for that ROI. Then its pixels and words are what this stage
would draw. Any other crop is drawn here as before; `--force` redraws them all.
So the Results button mostly draws the whole-scene pictures and fills gaps.

Whole-scene pictures are drawn `--workers` at a time (default: half the cores,
at most 4, as 01l), and the crops still to draw with them. Each section's
drawing starts only when there is memory for it - 01l's MemoryGate, with
job_memory's estimate: the section's box arrays, plus one band and the tile
cache for a scene, plus the largest crop's working arrays for crops - and a
section that cannot have it is listed as not drawn (a scene alone is dropped
first when the crops still fit). Sections are prepared, and their results
written to index.csv and failures.csv, on the main thread in order, so any
number of workers writes exactly what one writes.

What it costs. The first run re-runs StarDist once for every section measured
before 05c saved outlines (every section detected by an older 05c): about as
long as detection itself took. Those outlines are saved, so every later run
is fast, redrawing only what changed. Whole-scene pictures are large -
roughly 100-500 MB each at full resolution - so `--no-scenes` draws only the
crops; a trial with `--limit` shows what a full run will hold.

Without --marker every marker with measured sections is drawn, in the study's
order, and the run ends with one "<N> not drawn:" line over all of them. The
index and failures tables are shared; each row names its marker.

Run:  python 06i_detection_overlays.py
      python 06i_detection_overlays.py --marker AF488 --animal LS22 LS23
      python 06i_detection_overlays.py --limit 3 --force
      python 06i_detection_overlays.py --workers 1      # one scene at a time
"""

import argparse
import csv
import datetime
import hashlib
import importlib.util
import json
import os
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402,F401
import ls_io as IO  # noqa: E402
import ls_overlay as LO  # noqa: E402
import czi_read as CR  # noqa: E402

# 05c is loaded, not imported by name: its file name starts with a digit. Its
# segmentation, membership rule, outline file and settings record are used as
# they are, so a crop is drawn by the very code that counted it.
_spec = importlib.util.spec_from_file_location(
    "_d5_for_06i", os.path.join(_HERE, "05c_detect_rois.py"))
D5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D5)
G5 = D5.G5

OUT_ROOT = D5.OUT_ROOT
RESULTS = D5.RESULTS
NUCLEI_CSV = D5.NUCLEI_CSV
MEAS_CSV = os.path.join(RESULTS, "roi_measurements.csv")
#: 06a's cut per (section, marker) at full precision. roi_measurements.csv
#: rounds it to 0.1, and a median on the rounded value can sit either side of
#: the real one; nuclei are coloured by the cut they were counted by.
CUTS_CSV = os.path.join(RESULTS, "positivity_cuts.csv")
MANIFEST_CSV = D5.MANIFEST_SCENES_CSV
OUT_DIR = D5.OVERLAY_DIR
INDEX_CSV = os.path.join(OUT_DIR, "index.csv")
#: The crops 05c drew as it measured (05c's CropDrawer): read here, never
#: written. A crop listed there is kept as it is when it is still what this
#: stage would draw (adoptable).
INDEX_05C = D5.INDEX_05C
FAILURES_CSV = os.path.join(OUT_DIR, "failures.csv")

INDEX_KEYS = LO.INDEX_KEYS
FAILURE_KEYS = ["marker", "animal", "slide", "scene_uid", "reason"]

UM_PX = float(D5.BASE_PX_UM)
CROP_BAR_UM = D5.CROP_BAR_UM
SCENE_BAR_UM = 500
#: The picture kinds a crop run writes; a scene run writes SCENE_PICTURES.
CROP_PICTURES = ("roi", "background")
SCENE_PICTURES = ("scene",)
#: Native rows of the scene read, drawn and written at a time (as 04o reads
#: in bands): one band of a scene is held, never the whole picture.
BAND_ROWS = 1024
#: Smallest text height of a box's number tag, native px (~10 um). Larger
#: scenes get larger tags (tag_px), so the numbers can be read with the whole
#: scene fitted to a screen.
TAG_PX = 16
#: Sections whose roi_nuclei.csv rows are held at once. The file is millions
#: of rows once a second marker is in it (see 06a's _Disc), so it is scanned
#: once per batch rather than held whole.
BATCH = 32
#: The most a row's centroid may differ from its label's, in native px. The
#: CSV rounds to 0.1, so a true match is within 0.071.
CENTROID_TOL = 0.5
#: ls_overlay.kind_codes' code for a nucleus outside its box's shape.
OUTSIDE_CODE = LO.KINDS.index("outside") + 1
#: Scenes drawn at once with --workers 0 (the default): half the cores, at
#: most four, as 01l draws its scenes.
DEFAULT_WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))
#: Bytes held per pixel of a scene band while it is drawn: the plane read, its
#: float32 grey, the RGB band, the kind codes, the outline and "taken" masks,
#: each box's slice of codes and outlines, and the PNG rows and their
#: compressed copy. Rounded up.
BAND_BYTES_PER_PX = 24
#: Bytes held per pixel of every box for as long as its scene is drawn: its
#: label image (uint32), its marker plane, its shape outline. Rounded up.
BOX_BYTES_PER_PX = 12
#: Bytes per pixel of the crop being drawn, beyond its box's own: the grey
#: float32, the RGB image and its PIL copy, kind codes, outlines and shape
#: mask, and the PNG rows. Rounded up.
CROP_BYTES_PER_PX = 16


class NotDrawn(Exception):
    """A section that is not drawn; the message is failures.csv's reason.

    Its earlier pictures are kept: the section could not be drawn NOW (a read
    failed, settings changed, 06a needs re-running), which says nothing
    against what was drawn before."""


class Disagrees(NotDrawn):
    """Not drawn because the outlines or counts no longer agree with the
    numbers. Earlier pictures of the section go too: they would show
    nuclei, or counts, that the spreadsheet no longer has."""


# ---- geometry ---------------------------------------------------------------


#: 05c's array form of its ROI-membership rule (05c.grid_inside), and the
#: centroids it computes: the very functions 05c drew its crops with.
_poly_inside = D5._poly_inside
grid_inside = D5.grid_inside


def affine_of(g):
    """(M, Minv) from a geometry row, as 05c.main builds them."""
    M = np.array([[float(g["m00"]), float(g["m01"]), float(g["m02"])],
                  [float(g["m10"]), float(g["m11"]), float(g["m12"])]])
    return M, G5.invert_affine(M)


centroids = D5.centroids


def members(labels, x0, y0, inside, Minv):
    """{label: (czi_x, czi_y)} of the labels 05c counted: centroid inside."""
    out = {}
    for lid, (cx, cy) in centroids(labels, x0, y0).items():
        gx, gy = G5.apply_affine(Minv, cx, cy)
        if inside(gx, gy):
            out[lid] = (cx, cy)
    return out


# ---- inputs -----------------------------------------------------------------


def load_outlines(marker, uid, boxes, doc, model, scene=None):
    """({roi_index: (labels, x0, y0)}, "saved" | "re-segmented").

    The npz 05c saved when there is one. Otherwise every box is segmented
    again with 05c.segment_box, exactly as 05c's loop does it (the shared
    range first, for a marker that needs one), and saved with
    05c.save_outlines. `scene` is the CZI scene index, needed only then.
    Refused with NotDrawn when the settings now in force are not the ones the
    rows were measured with: outlines from other thresholds are not what was
    counted.
    """
    path = D5.outline_path(marker, uid)
    if os.path.exists(path):
        out = {}
        with np.load(path) as npz:
            for k in npz.files:
                if k.startswith("roi") and not k.endswith("_origin"):
                    x0, y0 = (int(v) for v in npz[k + "_origin"])
                    out[int(k[3:])] = (npz[k], x0, y0)
        return out, "saved"
    check_settings(marker)
    if scene is None:
        raise ValueError("re-segmenting needs the scene index")
    section_norm = {}
    if D5.needs_shared_norm(marker):
        lo, hi, _ = D5.section_range(doc, boxes, marker, scene)
        section_norm[marker] = (lo, hi)
    out = {}
    for bi, b in enumerate(boxes, 1):
        seg = D5.segment_box(doc, b, scene, model, D5.min_area_px_for(),
                             section_norm)
        if seg is None:
            continue
        out[bi] = (seg["labels"], int(b["czi_x0"]), int(b["czi_y0"]))
    D5.save_outlines(marker, uid, out)
    return out, "re-segmented"


def check_settings(marker):
    """Refuse (NotDrawn) to re-segment under thresholds other than the ones
    results/detection_settings.json says the rows were measured with."""
    if D5.backend_for(marker) != "stardist":
        return
    before = D5.recorded_settings(marker, True)
    if not D5.same_settings(before, D5.stardist_settings(marker)):
        raise NotDrawn("detection settings changed since measured")


def match(outlines, rows_by_roi, inside_by_roi, Minv):
    """None when the outlines are the rows, else the first reason they are not.

    Per ROI: the labels whose centroid maps inside the shape must be exactly
    the rows' nucleus_ids, and each row's czi_x/czi_y within CENTROID_TOL of
    its label's centroid. `rows_by_roi` is {roi_index: {nucleus_id: row}}.
    """
    for bi in sorted(set(outlines) | set(rows_by_roi)):
        rows = rows_by_roi.get(bi, {})
        if bi not in outlines:
            if rows:
                return (f"roi{bi:02d}: {len(rows)} nuclei in roi_nuclei.csv, "
                        f"no outlines saved for it")
            continue
        if bi not in inside_by_roi:
            return f"roi{bi:02d}: outlines for a box the section does not have"
        labels, x0, y0 = outlines[bi]
        got = members(labels, x0, y0, inside_by_roi[bi], Minv)
        if len(got) != len(rows):
            return (f"roi{bi:02d}: {len(got)} nuclei in outlines, "
                    f"{len(rows)} in roi_nuclei.csv")
        if set(got) != set(rows):
            only_o = sorted(set(got) - set(rows))[:3]
            only_r = sorted(set(rows) - set(got))[:3]
            return (f"roi{bi:02d}: nucleus ids differ (outlines only {only_o}, "
                    f"roi_nuclei.csv only {only_r})")
        for lid, (cx, cy) in sorted(got.items()):
            r = rows[lid]
            d = float(np.hypot(cx - float(r["czi_x"]), cy - float(r["czi_y"])))
            if d > CENTROID_TOL:
                return (f"roi{bi:02d}: nucleus {lid} is {d:.2f} px from its "
                        f"roi_nuclei.csv centroid")
    return None


def fingerprint(uid, marker, rows=None, cut=None, boxes=None, context=None):
    """sha1 of what a section's pictures are drawn from.

    Its roi_nuclei.csv rows (raw cells, in file order), its cut, its box rows,
    the bytes of its outline file, and `context`: everything else that
    changes a pixel or a word of a picture (scene_context). Each is read from
    disk when not given; main() passes what it has already read, so the big
    CSV is not rescanned per section."""
    if rows is None:
        rows = _scan_rows({uid}, marker)[1].get(uid, [])
    if cut is None:
        cut = _cuts(_read_measurements()).get((uid, marker), "")
    if boxes is None:
        G5.use_marker(marker)
        boxes = [b for b in G5.load_csv(G5.BOX_CSV) if b["scene_uid"] == uid]
    if context is None:
        G5.use_marker(marker)
        geom = {g["scene_uid"]: g for g in G5.load_csv(G5.GEOM_CSV)} \
            if os.path.exists(G5.GEOM_CSV) else {}
        context = scene_context(
            geom.get(uid), boxes[0].get("animal", "") if boxes else "",
            _slides().get(uid, ""), _exact_cuts().get((uid, marker)),
            [r for r in _read_measurements()
             if r["scene_uid"] == uid and r.get("marker") == marker])
    h = hashlib.sha1()
    for r in rows:
        h.update(("\x1f".join(r) + "\n").encode("utf-8"))
    h.update(b"\x1ecut\x1e" + str(cut).encode("utf-8"))
    h.update(b"\x1eboxes\x1e" + json.dumps([sorted(b.items()) for b in boxes])
             .encode("utf-8"))
    h.update(b"\x1econtext\x1e" + json.dumps(context, sort_keys=True)
             .encode("utf-8"))
    h.update(b"\x1enpz\x1e")
    path = D5.outline_path(marker, uid)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


#: The roi_measurements.csv columns a picture shows or checks against (the
#: strip's counts, %, cut and region; the scene total's withheld %). Nothing
#: else of 06a's row goes into the fingerprint: abercrombie_factor and the
#: densities are pooled over every section of a (marker, region), so they
#: change whenever any section is measured, and no picture shows them.
MEAS_SHOWN = ("roi_index", "roi_kind", "region", "n_nuclei", "n_positive",
              "frac_positive", "n_censored", "section_positivity_cut",
              "denominator_withheld_reason")


def scene_context(g, animal, slide, exact_cut, meas_rows):
    """What a section's pictures depend on besides rows, boxes and outlines:
    the affine that places the ROI shape, the identity on line 1, the exact
    cut nuclei are coloured by, and the MEAS_SHOWN columns of the section's
    own 06a rows - only what changes a pixel or a word of its pictures."""
    return {"geometry": sorted((g or {}).items()), "animal": animal,
            "slide": slide, "exact_cut": repr(exact_cut),
            "measurements": [[(k, r.get(k, "")) for k in MEAS_SHOWN] for r in
                             sorted(meas_rows, key=lambda r: int(r["roi_index"]))]}


def _exact_cuts():
    """{(uid, marker): cut} from 06a's positivity_cuts.csv, as floats."""
    with open(CUTS_CSV, newline="", encoding="utf-8") as fh:
        return {(r["scene_uid"], r["marker"]): float(r["cut"])
                for r in csv.DictReader(fh)}


def _scan_rows(uids, marker):
    """(header, {uid: [raw row, ...]}) of roi_nuclei.csv for `uids`, `marker`."""
    out = {}
    with open(NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd, None) or []
        mi = header.index("marker")
        for row in rd:
            if row and row[0] in uids and len(row) > mi and row[mi] == marker:
                out.setdefault(row[0], []).append(row)
    return header, out


def _read_measurements():
    with open(MEAS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _cuts(meas):
    """{(uid, marker): section_positivity_cut as written}: one per section."""
    return {(r["scene_uid"], r["marker"]): r.get("section_positivity_cut", "")
            for r in meas}


_slides = D5.slides


def _rows_with_numbers(uids, marker):
    """Sections with nucleus rows for `marker`: one streamed column."""
    have = set()
    with open(NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        mi = (next(rd, None) or []).index("marker")
        for row in rd:
            if row and row[0] in uids and len(row) > mi and row[mi] == marker:
                have.add(row[0])
    return have


# ---- drawing ----------------------------------------------------------------


#: The strip's words: ls_overlay's, which 05c's crops print too.
crop_lines = LO.crop_lines
_counts_line = LO.counts_line


def scene_lines(rec, roi_recs):
    """The scene strip's lines: identity, the totals (index row `rec`), then
    one line per ROI from `roi_recs` (its index-row-like counts)."""
    one = (f"{rec['marker']} | fish {rec['animal']} | slide {rec['slide']} | "
           f"scene {rec['scene_uid']} | whole scene")
    out = [one, f"{len(roi_recs)} ROIs: " + _counts_line(rec)]
    for r in roi_recs:
        head = f"ROI {int(r['roi_index']):02d} {r['region']}: {r['n_counted']} counted"
        if rec["cut"] == "":
            out.append(f"{head}, no cut")
            continue
        pct = f"{r['pct_positive']}%" if r["pct_positive"] != "" else "n/a"
        out.append(f"{head}, {r['n_positive']} positive ({pct})")
    return out


def draw_crop(path, plane, labels, kinds, outline, lo, hi, lines):
    """Grey marker, nuclei, the shape in white, the strip stacked above -
    ls_overlay.crop_image, which 05c draws its crops by."""
    LO.save_png(path, LO.crop_image(plane, labels, kinds, outline, lo, hi, lines,
                                    UM_PX, CROP_BAR_UM))


def tag_px(width):
    """Text height of the tags on a scene picture `width` px wide: 1/120 of
    the width, never under TAG_PX - about 100 px on a 12,000 px scene."""
    return max(TAG_PX, int(width) // 120)


def tag(n, size=TAG_PX):
    """Box number `n` as "NN", white on black: a small uint8 RGB array,
    text `size` px high."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=int(size))
    text = f"{int(n):02d}"
    left, top, right, bottom = font.getbbox(text)
    pad = 2
    img = Image.new("RGB", (right - left + 2 * pad, bottom - top + 2 * pad),
                    (0, 0, 0))
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=font,
                             fill=(255, 255, 255))
    return np.asarray(img, np.uint8).copy()


def _meet(top, left, h, w, r0, n, width):
    """Where an h x w array placed at (top, left) of the picture meets the
    band of rows r0..r0+n: (band rows, band cols, array rows, array cols)
    slices, or None. Columns are clipped to the picture's width."""
    a0, a1 = max(top, r0), min(top + h, r0 + n)
    c0, c1 = max(left, 0), min(left + w, width)
    if a0 >= a1 or c0 >= c1:
        return None
    return (slice(a0 - r0, a1 - r0), slice(c0, c1),
            slice(a0 - top, a1 - top), slice(c0 - left, c1 - left))


def scene_rect(g):
    """(x, y, w, h) of the scene, CZI px, from its geometry row (05a's)."""
    try:
        x, y, w, h = (int(float(g[k])) for k in
                      ("scene_x", "scene_y", "scene_w", "scene_h"))
    except (KeyError, TypeError, ValueError):
        raise NotDrawn(f"no scene rectangle in "
                       f"{os.path.basename(G5.GEOM_CSV)} - re-run "
                       f"05a_roi_geometry.py") from None
    if w < 1 or h < 1:
        raise NotDrawn(f"the scene rectangle is {w}x{h}")
    return x, y, w, h


def draw_scene(doc, g, marker, boxes, outlines, kinds_by_roi, lo, hi, path,
               lines):
    """The whole scene at native px, written band by band to `path`.

    The strip (`lines`, the legend, a 500 um bar) goes first. Then each band
    of BAND_ROWS rows is read (the marker plane, the scene named), put in
    grey with the section's stretch, and every box meeting it drawn as its
    crop is: the nuclei painted, the shape outlined; then each box's number
    tag (tag_px(width) high) on top. Per box, the shape outline and the tag
    are made once and the rows in the band sliced from them, so what is held
    is one band plus box-sized arrays. A box's nucleus outlines are found on
    its label rows one above and one below the band (within the box), so an
    outline on a band's edge is found exactly as in the crop: the picture
    does not depend on BAND_ROWS.

    Boxes can overlap. Their nuclei are composited into ONE kind map for the
    band before anything is painted, so a pixel is blended once, as in a
    crop. Where two boxes both have a nucleus at a pixel, a box that counts
    or flags it (any kind but "outside") wins over a box that has it
    outside its shape - a box's corners lie outside its own disc, and a
    neighbour's disc can reach into them - so a counted nucleus is never
    drawn yellow; between two that count it (or two that both have it
    outside) the lower roi_index draws it, fill and outline. Elsewhere each
    box draws its own. Shape outlines and tags of every box come after, in
    box order.
    """
    sx, sy, sw, sh = scene_rect(g)
    scene = int(g["scene_index"])
    _, Minv = affine_of(g)
    channel = D5.plane_index_for(marker)
    top_strip = LO.strip(sw, lines, True, UM_PX, SCENE_BAR_UM)
    size = tag_px(sw)

    marks = []      # (top, left, h, w, labels or None, kinds, shape outline)
    tags = []       # (top, left, tag array)
    for bi, b in enumerate(boxes, 1):
        x0, y0 = int(b["czi_x0"]), int(b["czi_y0"])
        w, h = int(b["czi_w"]), int(b["czi_h"])
        if w < 1 or h < 1:
            continue
        labels = outlines[bi][0] if bi in outlines else None
        marks.append((y0 - sy, x0 - sx, h, w, labels, kinds_by_roi.get(bi, {}),
                      D5.shape_outline(b, Minv, x0, y0, h, w)))
        t = tag(bi, size)
        tags.append((y0 - sy - t.shape[0], x0 - sx - t.shape[1], t))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with LO.PngRows(path, sw, top_strip.shape[0] + sh) as png:
        for r in range(0, top_strip.shape[0], BAND_ROWS):
            png.write(top_strip[r:r + BAND_ROWS])
        del top_strip
        for r0 in range(0, sh, BAND_ROWS):
            n = min(BAND_ROWS, sh - r0)
            plane = CR.read_plane(doc, D5._Rect(sx, sy + r0, sw, n), channel,
                                  scene=scene)
            if plane.shape != (n, sw):
                raise NotDrawn(f"rows {r0}..{r0 + n} of the scene read "
                               f"{plane.shape}, not {(n, sw)}")
            band = np.repeat(LO.grey(plane, lo, hi)[:, :, None], 3, axis=2)
            del plane
            code = np.zeros((n, sw), np.uint8)
            edge = np.zeros((n, sw), bool)
            taken = np.zeros((n, sw), bool)
            parts = []      # (band rows, band cols, kind codes, outlines)
            for top, left, h, w, labels, kinds, _ in marks:   # roi_index order
                at = _meet(top, left, h, w, r0, n, sw)
                if labels is None or at is None:
                    continue
                br, bc, ar, ac = at
                m0 = max(ar.start - 1, 0)
                m1 = min(ar.stop + 1, h)
                lab = labels[m0:m1]
                rows = slice(ar.start - m0, ar.stop - m0)
                parts.append((br, bc, LO.kind_codes(lab, kinds)[rows, ac],
                              LO.edges(lab)[rows, ac]))
            # Pass 1: every box's nuclei it counts or flags (any kind but
            # outside); pass 2: the outside ones, only where still free. A
            # nucleus in a box's corner, outside its shape but inside a
            # neighbour's, is drawn as the neighbour counts it.
            for counted in (True, False):
                for br, bc, kc, ke in parts:
                    want = (kc > 0) & ((kc != OUTSIDE_CODE) if counted
                                       else (kc == OUTSIDE_CODE))
                    free = want & ~taken[br, bc]
                    code[br, bc][free] = kc[free]
                    edge[br, bc][free] = ke[free]
                    taken[br, bc] |= want
            del taken, parts
            LO.paint_codes(band, code, edge)
            del code, edge
            for top, left, h, w, _, _, shape in marks:
                at = _meet(top, left, h, w, r0, n, sw)
                if at is not None:
                    br, bc, ar, ac = at
                    band[br, bc][shape[ar, ac]] = LO.SHAPE_COLOUR
            for top, left, t in tags:
                at = _meet(top, left, t.shape[0], t.shape[1], r0, n, sw)
                if at is not None:
                    br, bc, ar, ac = at
                    band[br, bc] = t[ar, ac]
            png.write(band)
            del band


def scene_record(prep, uid, marker, boxes, ident, fp, meas):
    """(index row, strip lines) of a section's scene picture.

    Totals are over its ROIs - roi_kind "roi", not background discs - from
    the counts drawn; a box too small for 05c to segment has no outlines and
    adds 06a's row for it (0). `meas` is {roi_index: roi_measurements row}.
    The total % positive is blank ("n/a" on the strip) wherever 06a withholds
    the fraction (denominator_withheld_reason: segmented on the marker
    itself), as each ROI's is.
    """
    roi_recs = []
    for bi, b in enumerate(boxes, 1):
        if b["roi_kind"] == "background":
            continue
        c = prep["counts_by_roi"].get(bi)
        if c is None:
            m = meas.get(bi, {})
            frac = m.get("frac_positive", "")
            c = {"n_counted": _num(m.get("n_nuclei", 0)) or 0,
                 "n_positive": (_num(m.get("n_positive", 0)) or 0
                                if ident["cut"] != "" else ""),
                 "pct_positive": (round(float(frac) * 100, 1)
                                  if frac != "" else ""),
                 "n_outside": 0, "n_artifact": 0, "n_off_tissue": 0,
                 "n_censored": _num(m.get("n_censored", 0)) or 0}
        roi_recs.append(dict(c, roi_index=bi, region=b["region"]))
    tot = {k: sum(int(r[k]) for r in roi_recs)
           for k in ("n_counted", "n_outside", "n_artifact", "n_off_tissue",
                     "n_censored")}
    # 06a withholds the fraction for a marker segmented on its own channel
    # (n_nuclei is then not a count of cells); the total withholds it too.
    withheld = any(meas.get(bi, {}).get("denominator_withheld_reason")
                   for bi, b in enumerate(boxes, 1) if b["roi_kind"] != "background")
    if ident["cut"] == "":
        tot["n_positive"] = tot["pct_positive"] = ""
    else:
        tot["n_positive"] = sum(int(r["n_positive"]) for r in roi_recs)
        tot["pct_positive"] = (round(100 * tot["n_positive"] / tot["n_counted"], 1)
                               if tot["n_counted"] and not withheld else "")
    path = os.path.join(LO.overlay_dir(OUT_ROOT, marker, ident["animal"]),
                        LO.overlay_name(marker, uid, "scene"))
    rec = {"path": _rel(path), "marker": marker, "animal": ident["animal"],
           "slide": ident["slide"], "scene_uid": uid, "picture": "scene",
           "roi_index": "", "region": "", "cut": ident["cut"],
           "um_per_px": UM_PX, "outlines": prep["how"],
           "drawn_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "fingerprint": fp}
    rec.update(tot)
    rec = {k: ("" if rec[k] is None else str(rec[k])) for k in INDEX_KEYS}
    roi_recs = [{k: str(v) for k, v in r.items()} for r in roi_recs]
    return rec, scene_lines(rec, roi_recs)


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def prepare_scene(doc, uid, marker, boxes, g, raw, header, cut, meas_by,
                  get_model):
    """Everything a section's pictures need, computed once.

    Returns {outlines, how, kinds_by_roi, counts_by_roi, planes, lo, hi, Minv,
    inside_by_roi}; raises NotDrawn with failures.csv's reason. `cut` is the
    exact cut from positivity_cuts.csv, or None for a section with none. Task 5's
    whole-scene picture is drawn from the same dict as the crops.
    """
    scene = int(g["scene_index"])
    _, Minv = affine_of(g)
    path = D5.outline_path(marker, uid)
    model = None
    if not os.path.exists(path):
        check_settings(marker)          # before the model is loaded for it
        model = get_model()
    outlines, how = load_outlines(marker, uid, boxes, doc, model, scene=scene)
    inside_by_roi = {bi: D5.roi_inside(b) for bi, b in enumerate(boxes, 1)}
    for bi, (labels, x0, y0) in outlines.items():
        if bi > len(boxes):
            raise Disagrees(f"roi{bi:02d}: outlines for a box the section "
                           f"does not have ({len(boxes)} boxes)")
        b = boxes[bi - 1]
        if (x0, y0) != (int(b["czi_x0"]), int(b["czi_y0"])):
            raise Disagrees(f"roi{bi:02d}: outlines were made at ({x0}, {y0}), "
                           f"the box is now at ({b['czi_x0']}, {b['czi_y0']})")
        if labels.shape != (int(b["czi_h"]), int(b["czi_w"])):
            raise Disagrees(f"roi{bi:02d}: outlines are {labels.shape}, the box "
                           f"is {b['czi_h']}x{b['czi_w']}")
    rows_by_roi = {}
    for r in raw:
        row = dict(zip(header, r))
        ids = rows_by_roi.setdefault(int(row["roi_index"]), {})
        nid = int(row["nucleus_id"])
        if nid in ids:
            raise Disagrees(f"roi{int(row['roi_index']):02d}: nucleus_id {nid} "
                           f"is in roi_nuclei.csv twice")
        ids[nid] = row
    why = match(outlines, rows_by_roi, inside_by_roi, Minv)
    if why:
        raise Disagrees(why)

    kinds_by_roi, counts_by_roi = {}, {}
    for bi in sorted(outlines):
        # 05c's section_kinds, box by box: ls_overlay.classify, the rule 05c
        # drew its crops by.
        try:
            k_by, c_by = D5.section_kinds({bi: outlines[bi]}, rows_by_roi, cut)
        except ValueError as exc:
            raise NotDrawn(str(exc)) from None
        kinds, counts = k_by[bi], c_by[bi]
        m = meas_by.get(bi)
        if m is None:
            raise Disagrees(f"roi{bi:02d}: no row in roi_measurements.csv - "
                           f"re-run 06a_roi_dataset.py")
        for mine, theirs in (("n_counted", "n_nuclei"),
                             ("n_positive", "n_positive"),
                             ("n_censored", "n_censored")):
            if str(counts[mine]) != str(_num(m.get(theirs, ""))):
                raise Disagrees(f"roi{bi:02d}: {counts[mine]} {mine[2:]} drawn, "
                               f"{m.get(theirs, '')!r} in roi_measurements.csv")
        counts["pct_positive"] = LO.pct_of(m.get("frac_positive", ""))
        kinds_by_roi[bi], counts_by_roi[bi] = kinds, counts

    # The base: the marker plane of every box, native px, and one stretch
    # over all of them, so every picture of the section compares.
    planes = {}
    for bi, b in enumerate(boxes, 1):
        w, h = int(b["czi_w"]), int(b["czi_h"])
        if w < 1 or h < 1:
            continue
        planes[bi] = CR.read_plane(
            doc, D5._Rect(int(b["czi_x0"]), int(b["czi_y0"]), w, h),
            D5.plane_index_for(marker), scene=scene)
        if bi in outlines and planes[bi].shape != outlines[bi][0].shape:
            raise NotDrawn(f"roi{bi:02d}: the marker plane read "
                           f"{planes[bi].shape}, the outlines are "
                           f"{outlines[bi][0].shape}")
    if not planes:
        raise NotDrawn("no box of this section could be read")
    lo, hi = LO.stretch(np.concatenate([p.ravel() for p in planes.values()]))
    return {"outlines": outlines, "how": how, "kinds_by_roi": kinds_by_roi,
            "counts_by_roi": counts_by_roi, "planes": planes, "lo": lo,
            "hi": hi, "Minv": Minv, "inside_by_roi": inside_by_roi}


def draw_crops(prep, uid, marker, boxes, ident, fp, skip=()):
    """One crop per box with outlines, drawn by 05c's draw_crop - the call
    05c makes as it measures. Returns the index rows written. A crop whose
    path is in `skip` (kept from 05c) is not drawn and not returned."""
    out, now = [], datetime.datetime.now().isoformat(timespec="seconds")
    for bi, (labels, x0, y0) in sorted(prep["outlines"].items()):
        b = boxes[bi - 1]
        rec = D5.crop_record(uid, marker, bi, b, ident, prep["counts_by_roi"][bi],
                             prep["how"], fp, now)
        if rec["path"] in skip:
            continue
        D5.draw_crop(rec, b, labels, x0, y0, prep["Minv"], prep["planes"][bi],
                     prep["kinds_by_roi"][bi], prep["lo"], prep["hi"])
        out.append(rec)
    return out


def crop_paths(marker, uid, boxes, animal):
    """The index paths of the crops this stage would draw of a section: one
    per box in its outline file. None when there is no outline file."""
    path = D5.outline_path(marker, uid)
    if not os.path.exists(path):
        return None
    with np.load(path) as npz:
        idx = sorted(int(k[3:]) for k in npz.files
                     if k.startswith("roi") and not k.endswith("_origin"))
    out = []
    for bi in idx:
        if bi > len(boxes):
            return None
        b = boxes[bi - 1]
        picture = "background" if b["roi_kind"] == "background" else "roi"
        out.append(_rel(os.path.join(LO.overlay_dir(OUT_ROOT, marker, animal),
                                     LO.overlay_name(marker, uid, picture, bi,
                                                     b["region"]))))
    return out


def adoptable(rows05, uid, marker, raw, exact_cut, boxes, g, ident, meas, fp):
    """{path: index row} of the crops 05c drew of a section that are still
    exactly what this stage would draw, re-stamped with its fingerprint `fp`.

    A 05c crop is kept when its row has no error, its file is there, its data
    fingerprint (ls_overlay.data_fingerprint) equals the one computed now from
    the files - the rows, the exact cut 06a used, the boxes, the geometry row,
    the outline file, fish and slide - and its counts, % and cut as printed
    equal 06a's row for the ROI. Then its pixels and words are the ones this
    stage would draw: nothing else goes into a crop. `meas` is
    {roi_index: roi_measurements row}.
    """
    mine = [r for r in rows05 if r.get("marker") == marker
            and r.get("scene_uid") == uid and not r.get("error")
            and r.get("picture") in CROP_PICTURES]
    if not mine:
        return {}
    dfp = LO.data_fingerprint(raw, exact_cut, boxes, g, D5.outline_path(marker, uid),
                              ident["animal"], ident["slide"])
    out = {}
    for r in mine:
        if r.get("data_fingerprint") != dfp or not os.path.exists(_abs(r["path"])):
            continue
        try:
            m = meas.get(int(r["roi_index"]))
        except ValueError:
            continue
        if m is None:
            continue
        want = (str(_num(m.get("n_nuclei", ""))), str(_num(m.get("n_positive", ""))),
                str(LO.pct_of(m.get("frac_positive", ""))),
                m.get("section_positivity_cut", ""), str(_num(m.get("n_censored", ""))))
        got = (r["n_counted"], r["n_positive"], r["pct_positive"], r["cut"],
               r["n_censored"])
        if got != want or r["cut"] != ident["cut"]:
            continue
        out[r["path"]] = dict({k: r.get(k, "") for k in INDEX_KEYS}, fingerprint=fp)
    return out


_rel = D5.overlay_rel
_abs = D5.overlay_abs


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---- the run ----------------------------------------------------------------


def refusal():
    """Why this cannot run now, or None. Checked before any folder is made."""
    if not os.path.exists(NUCLEI_CSV):
        return (f"no {NUCLEI_CSV} - run 05c_detect_rois.py, then "
                f"06a_roi_dataset.py, first")
    for path in (MEAS_CSV, CUTS_CSV):
        if not os.path.exists(path):
            return (f"no {path} - re-run 06a_roi_dataset.py: the cut and the "
                    f"counts on every picture come from it")
        if os.path.getmtime(path) < os.path.getmtime(NUCLEI_CSV):
            return (f"{os.path.basename(path)} is older than "
                    f"{os.path.basename(NUCLEI_CSV)} - re-run "
                    f"06a_roi_dataset.py so the counts drawn are the counts of "
                    f"these nuclei")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marker", choices=G5.MARKERS,
                    help="draw only this marker (default: every marker that "
                         "has measured sections, in the study's order)")
    ap.add_argument("--animal", nargs="+", metavar="A",
                    help="only these fish")
    ap.add_argument("--no-scenes", action="store_true",
                    help="skip the whole-scene pictures")
    ap.add_argument("--no-crops", action="store_true",
                    help="skip the ROI and background-disc crops")
    ap.add_argument("--limit", type=int, help="only the first N sections of each marker")
    ap.add_argument("--force", action="store_true",
                    help="redraw sections whose inputs have not changed (and "
                         "the crops 05c drew)")
    ap.add_argument("--workers", type=int, default=0, metavar="N",
                    help=f"whole-scene pictures drawn at once, each only when "
                         f"there is memory for it (default 0: half the cores, "
                         f"at most 4 - {DEFAULT_WORKERS} here)")
    args = ap.parse_args()

    why = refusal()
    if why:
        print("!! REFUSED: " + why)
        return 2
    markers = [args.marker] if args.marker else list(G5.MARKERS)
    total_fails, drawn_any = 0, False
    for marker in markers:
        G5.use_marker(marker)
        if not os.path.exists(G5.BOX_CSV):
            if args.marker:
                print(f"no {G5.BOX_CSV} - run 05a_roi_geometry.py "
                      f"--marker {marker} first")
                return 1
            print(f"marker: {marker}  skipped (no {os.path.basename(G5.BOX_CSV)}; run 05a "
                  f"--marker {marker})")
            continue
        rc, n_fail = _draw_marker(args, marker)
        if rc:
            return rc
        total_fails += n_fail
        drawn_any = True
    if not drawn_any:
        print("no marker has boxes - run 05a_roi_geometry.py first")
        return 1
    if total_fails:
        print(f"{total_fails} not drawn: see {FAILURES_CSV}")
    return 0


def job_memory(g, boxes, scene, crops):
    """Bytes held while a section's pictures are drawn: every box's label
    image, plane and shape outline (the prepared section the job holds);
    for the scene, a band of it in flight and the CZI's cache of decoded
    tiles across its width; for the crops, the largest crop's transients."""
    px = [max(int(b["czi_w"]), 0) * max(int(b["czi_h"]), 0) for b in boxes]
    need = sum(px) * BOX_BYTES_PER_PX
    if scene:
        _, _, sw, _ = scene_rect(g)
        need += BAND_ROWS * sw * BAND_BYTES_PER_PX + CR.tile_cache_bytes(sw)
    if crops and px:
        need += max(px) * CROP_BYTES_PER_PX
    return need


class _NoGate:
    """Lets every scene start: for when 01l's gate cannot be had."""

    def acquire(self, need):
        return None

    def release(self, need):
        pass


def memory_gate():
    """01l_scene_renders.py's MemoryGate - borrowed, not restated: a scene
    starts alone when RAM and page file can hold it, and beside others only
    while free RAM covers it on top of what they reserved."""
    try:
        spec = importlib.util.spec_from_file_location(
            "_01l_for_06i", os.path.join(_HERE, "01l_scene_renders.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.MemoryGate()
    except Exception as exc:  # noqa: BLE001 - a viewer, not a measurement
        print(f"  (free memory cannot be checked here - {type(exc).__name__}: "
              f"{exc}; scenes are drawn without that check)")
        return _NoGate()


def _draw_marker(args, marker):
    """Draw one marker's measured sections: (exit code, sections not drawn).

    Each section is checked and prepared here, on the main thread, in order;
    its pictures are then drawn by one of `--workers` threads, and its result
    taken back - index.csv, failures.csv, the line printed - here again, in
    the order the sections were started. So a run with several workers
    writes exactly what a run with one writes.
    """
    print(f"marker: {marker}  ({os.path.basename(G5.BOX_CSV)})")
    by_sec = {}
    for b in G5.load_csv(G5.BOX_CSV):
        by_sec.setdefault(b["scene_uid"], []).append(b)
    geom = {g["scene_uid"]: g for g in G5.load_csv(G5.GEOM_CSV)} \
        if os.path.exists(G5.GEOM_CSV) else {}

    # MEASURED, as 06a reads it: rows in roi_nuclei.csv, or logged done by
    # 05c (measured and found empty). Only those are drawn.
    logged = D5.read_done_log(D5.DONE_LOG, marker) or set()
    measured = (_rows_with_numbers(set(by_sec), marker) | logged) & set(by_sec)
    todo = sorted(measured)
    if args.animal:
        want = set(args.animal)
        todo = [u for u in todo if by_sec[u][0].get("animal", "") in want]
    if args.limit:
        todo = todo[:args.limit]

    meas = _read_measurements()
    cuts = _cuts(meas)
    exact_cuts = _exact_cuts()
    meas_by = {}
    for r in meas:
        if r.get("marker") == marker:
            meas_by.setdefault(r["scene_uid"], {})[int(r["roi_index"])] = r
    slides = _slides()
    groups = ([CROP_PICTURES] if not args.no_crops else []) \
        + ([SCENE_PICTURES] if not args.no_scenes else [])

    os.makedirs(OUT_DIR, exist_ok=True)
    index = {r["path"]: r for r in _read_csv(INDEX_CSV)}
    rows05 = _read_csv(INDEX_05C)
    fails = []
    # A failure listed by an earlier run stands until this run looks at that
    # section again, so --animal / --limit / --no-crops do not erase it.
    prior_fails = _read_csv(FAILURES_CSV)
    looked = set()
    model = []

    def get_model():
        if not model:
            model.append(D5.load_model())
        return model[0]

    def save_tables():
        IO.atomic_write_csv(INDEX_CSV, [index[k] for k in sorted(index)],
                            INDEX_KEYS)
        keep = [r for r in prior_fails if not (r.get("marker") == marker
                                               and r.get("scene_uid") in looked)]
        IO.atomic_write_csv(FAILURES_CSV, keep + fails, FAILURE_KEYS)

    def drop_pictures(uid, kinds, keep=()):
        for k in [k for k, r in index.items()
                  if r.get("marker") == marker and r.get("scene_uid") == uid
                  and r.get("picture") in kinds and k not in keep]:
            p = _abs(k)
            if os.path.exists(p):
                os.remove(p)
            del index[k]

    from pylibCZIrw import czi as pyczi
    workers = max(1, args.workers or DEFAULT_WORKERS)
    gate = memory_gate()
    tally = {"drawn": 0, "same": 0, "pictures": 0, "small": 0, "adopted": 0}

    def draw_job(uid, g, czi, boxes, prep, srec, lines, kinds_drawn, adopted,
                 ident, fp, need):
        """A section's pictures (worker thread): the scene, then the crops not
        kept from 05c. Returns their index rows; raises what failed."""
        try:
            recs = []
            if "scene" in kinds_drawn:
                with CR.open_czi(czi, int(float(g.get("scene_w") or 0)) or None,
                                 pyczi=pyczi) as doc:
                    draw_scene(doc, g, marker, boxes, prep["outlines"],
                               prep["kinds_by_roi"], prep["lo"], prep["hi"],
                               _abs(srec["path"]), lines)
                recs.append(srec)
            if "roi" in kinds_drawn:
                recs += list(adopted.values())
                if prep is not None:
                    recs += draw_crops(prep, uid, marker, boxes, ident, fp,
                                       skip=set(adopted))
            return recs
        finally:
            if need:
                gate.release(need)

    def finish(item):
        """Take one section's result back, in the order it was started."""
        uid, ident, boxes = item["uid"], item["ident"], item["boxes"]
        kinds_drawn, scene_why = item["kinds"], item["scene_why"]
        try:
            if item["error"] is not None:
                raise item["error"]
            recs = item["future"].result()
        except Disagrees as exc:
            reason, stale = str(exc), True
        except NotDrawn as exc:
            reason, stale = str(exc), False
        except Exception as exc:  # noqa: BLE001 - one section, not the run
            # A read that failed - the drive unplugged, a file moved -
            # says nothing against the pictures already drawn.
            reason, stale = f"{type(exc).__name__}: {exc}", False
        else:
            drop_pictures(uid, kinds_drawn, keep={r["path"] for r in recs})
            for r in recs:
                index[r["path"]] = r
            tally["drawn"] += 1
            tally["pictures"] += len(recs)
            tally["adopted"] += item["n_adopted"]
            tally["small"] += sum(1 for bi, b in enumerate(boxes, 1)
                                  if bi not in item["keys"]
                                  and min(int(b["czi_w"]), int(b["czi_h"])) < 8)
            if scene_why:
                fails.append({"marker": marker, "animal": ident["animal"],
                              "slide": ident["slide"], "scene_uid": uid,
                              "reason": scene_why})
            save_tables()
            n_crops = sum(1 for r in recs if r["picture"] != "scene")
            print(f"  {uid}: {n_crops} crops"
                  + (" + scene" if "scene" in kinds_drawn else "")
                  + f" ({item['how']} outlines"
                  + (f"; {item['n_adopted']} kept as 05c drew them"
                     if item["n_adopted"] else "") + ")"
                  + (f"; {scene_why}" if scene_why else ""))
            return
        # Pictures that would disagree with the numbers now go; any other
        # failure leaves the ones already drawn, index rows and all.
        if stale:
            drop_pictures(uid, set(CROP_PICTURES) | set(SCENE_PICTURES))
        fails.append({"marker": marker, "animal": ident["animal"],
                      "slide": ident["slide"], "scene_uid": uid,
                      "reason": reason})
        save_tables()
        print(f"  {uid}: NOT DRAWN - {reason}")

    print(f"{len(todo)} sections")
    if workers > 1 and len(todo) > 1 and not args.no_scenes:
        print(f"drawing up to {min(workers, len(todo))} scenes at once")
    pending = deque()
    with ThreadPoolExecutor(workers) as pool:
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            header, raw_by = _scan_rows(set(batch), marker)
            for uid in batch:
                boxes = by_sec[uid]
                raw = raw_by.get(uid, [])
                ident = {"animal": boxes[0].get("animal", ""),
                         "slide": slides.get(uid, ""),
                         "cut": cuts.get((uid, marker), "")}
                context = scene_context(
                    geom.get(uid), ident["animal"], ident["slide"],
                    exact_cuts.get((uid, marker)),
                    list(meas_by.get(uid, {}).values()))
                fp = fingerprint(uid, marker, raw, ident["cut"], boxes, context)

                def current(kinds):
                    mine = [r for r in index.values() if r.get("marker") == marker
                            and r.get("scene_uid") == uid
                            and r.get("picture") in kinds]
                    return bool(mine) and all(
                        r.get("fingerprint") == fp and os.path.exists(_abs(r["path"]))
                        for r in mine)

                if not groups:
                    continue
                looked.add(uid)
                # Each kind asked for is redrawn only when it is out of date, so
                # turning scenes back on does not redraw the crops.
                todo_kinds = [k for k in groups if args.force or not current(k)]
                if not todo_kinds:
                    tally["same"] += 1
                    continue
                kinds_drawn = {p for k in todo_kinds for p in k}
                item = {"uid": uid, "ident": ident, "boxes": boxes, "kinds": kinds_drawn,
                        "scene_why": None, "future": None, "error": None,
                        "how": "saved", "keys": set(), "n_adopted": 0}
                try:
                    g = geom.get(uid)
                    if g is None:
                        raise NotDrawn(f"no row in {os.path.basename(G5.GEOM_CSV)}")
                    if (uid, marker) not in cuts:
                        raise NotDrawn("no rows in roi_measurements.csv - re-run "
                                       "06a_roi_dataset.py")
                    cut = exact_cuts.get((uid, marker))
                    if (cut is None) != (ident["cut"] == ""):
                        raise NotDrawn("positivity_cuts.csv and "
                                       "roi_measurements.csv disagree on the cut - "
                                       "re-run 06a_roi_dataset.py")
                    if "scene" in kinds_drawn:
                        # Refused before anything is read. Only the scene picture
                        # needs the rectangle: without it the crops are still
                        # drawn, and the scene is listed as not drawn, its old
                        # picture kept (an old geometry file says nothing
                        # against it).
                        try:
                            scene_rect(g)
                        except NotDrawn as exc:
                            if "roi" not in kinds_drawn:
                                raise
                            item["scene_why"] = f"scene picture not drawn: {exc}"
                            kinds_drawn -= set(SCENE_PICTURES)
                    # The crops 05c drew as it measured, kept where they are
                    # still exactly what would be drawn here; --force redraws.
                    adopted, crops_left = {}, "roi" in kinds_drawn
                    if crops_left and not args.force:
                        want = crop_paths(marker, uid, boxes, ident["animal"])
                        if want is not None:
                            adopted = {p: r for p, r in adoptable(
                                rows05, uid, marker, raw, cut, boxes, g, ident,
                                meas_by.get(uid, {}), fp).items() if p in want}
                            crops_left = not set(want) <= set(adopted)
                    path = os.path.join(CONFIG["source_dir"], g["czi_file"])
                    prep = None
                    if "scene" in kinds_drawn or crops_left:
                        with CR.open_czi(path, int(float(g.get("scene_w") or 0)) or None,
                                         pyczi=pyczi) as doc:
                            prep = prepare_scene(doc, uid, marker, boxes, g, raw,
                                                 header, cut, meas_by.get(uid, {}),
                                                 get_model)
                        if prep["how"] == "re-segmented":
                            fp = fingerprint(uid, marker, raw, ident["cut"], boxes,
                                             context)
                        item["how"], item["keys"] = prep["how"], set(prep["outlines"])
                    else:
                        item["keys"] = {int(r["roi_index"]) for r in adopted.values()}
                    item["n_adopted"] = len(adopted)
                    srec = lines = None
                    if "scene" in kinds_drawn:
                        srec, lines = scene_record(prep, uid, marker, boxes,
                                                   ident, fp, meas_by.get(uid, {}))
                    # Every job that draws is gated on memory - crops as well
                    # as scenes, so a crops-only run with several workers
                    # cannot pile up prepared sections either.
                    need = 0
                    if prep is not None:
                        scene = "scene" in kinds_drawn
                        need = job_memory(g, boxes, scene, crops_left)
                        room = gate.acquire(need)
                        if room is not None and scene and crops_left:
                            why = (f"the scene picture needs about {need / 1e9:.1f} GB "
                                   f"and only {room / 1e9:.1f} GB can be had (memory "
                                   f"and page file) - close other programs")
                            item["scene_why"] = f"scene picture not drawn: {why}"
                            kinds_drawn -= set(SCENE_PICTURES)
                            need = job_memory(g, boxes, False, True)
                            room = gate.acquire(need)
                        if room is not None:
                            what = ("the scene picture needs" if "scene" in kinds_drawn
                                    else "the crops need")
                            raise NotDrawn(f"{what} about {need / 1e9:.1f} GB and only "
                                           f"{room / 1e9:.1f} GB can be had (memory and "
                                           f"page file) - close other programs")
                    item["future"] = pool.submit(
                        draw_job, uid, g, path, boxes, prep, srec, lines,
                        set(kinds_drawn), adopted, ident, fp, need)
                except Exception as exc:  # noqa: BLE001 - taken back by finish()
                    item["error"] = exc
                pending.append(item)
                while len(pending) >= workers:
                    finish(pending.popleft())
        while pending:
            finish(pending.popleft())
    save_tables()
    print(f"{tally['pictures']} pictures over {tally['drawn']} sections -> {OUT_DIR}"
          + (f"; {tally['adopted']} crops kept as 05c drew them"
             if tally["adopted"] else "")
          + (f"; {tally['same']} unchanged since drawn" if tally["same"] else ""))
    if tally["small"]:
        print(f"{tally['small']} boxes under 8 px not drawn (05c does not segment "
              f"them; 06a counts them as 0)")
    return 0, len(fails)


if __name__ == "__main__":
    raise SystemExit(main())
