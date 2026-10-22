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
identity, the counts, the colour legend and a 50 um bar. Colours and the
positivity rule are ls_overlay's, which 06a counts by:

  positive     magenta 40% fill + magenta outline (counted, above the cut)
  censored     magenta fill + white outline (counted positive: clipped pixels)
  negative     cyan outline (counted, not above the cut)
  outside ROI  yellow outline (in the box, centroid outside the shape)
  artifact     orange outline (not counted)
  off tissue   blue outline (not counted)

and the ROI shape itself as a 1-px white line. Brightness is a viewing stretch
only: 1st..99.8th percentile over every box of the section, the same for all of
a section's pictures so they compare.

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
under detection_overlays/) with the counts drawn and a fingerprint of the
inputs: the section's roi_nuclei.csv rows, its cut, its boxes and the outline
file. A rerun skips a section whose pictures carry the fingerprint it has now;
`--force` redraws them all.

Needs 06a: refused (exit 2) when roi_measurements.csv is missing or older than
roi_nuclei.csv, since the cut and the counts printed come from it. Not in the
run order - drawn on request.

`--no-scenes` / `--no-crops` skip one kind of picture: it is neither drawn
nor removed, and the other kind is redrawn only when out of date.

Run:  python 06i_detection_overlays.py
      python 06i_detection_overlays.py --marker AF488 --animal LS22 LS23
      python 06i_detection_overlays.py --limit 3 --force
"""

import argparse
import csv
import datetime
import hashlib
import importlib.util
import json
import os
import sys

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
MANIFEST_CSV = os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv")
OUT_DIR = os.path.join(OUT_ROOT, "detection_overlays")
INDEX_CSV = os.path.join(OUT_DIR, "index.csv")
FAILURES_CSV = os.path.join(OUT_DIR, "failures.csv")

INDEX_KEYS = ["path", "marker", "animal", "slide", "scene_uid", "picture",
              "roi_index", "region", "n_counted", "n_positive", "pct_positive",
              "cut", "n_outside", "n_artifact", "n_off_tissue", "n_censored",
              "um_per_px", "outlines", "drawn_at", "fingerprint"]
FAILURE_KEYS = ["marker", "animal", "slide", "scene_uid", "reason"]

UM_PX = float(D5.BASE_PX_UM)
CROP_BAR_UM = 50
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


def _poly_inside(gx, gy, v):
    """05c.point_in_poly over arrays: the same even-odd test, the same
    comparisons, so a pixel is inside here exactly when a nucleus at that
    point would be counted. An edge with yi == yj never satisfies
    `(yi > y) != (yj > y)`, so it is skipped rather than divided by."""
    gx = np.asarray(gx, float)
    gy = np.asarray(gy, float)
    inside = np.zeros(np.broadcast(gx, gy).shape, bool)
    n = len(v)
    j = n - 1
    for i in range(n):
        xi, yi = float(v[i][0]), float(v[i][1])
        xj, yj = float(v[j][0]), float(v[j][1])
        if yi != yj:
            cross = (yi > gy) != (yj > gy)
            inside ^= cross & (gx < (xj - xi) * (gy - yi) / (yj - yi) + xi)
        j = i
    return inside


def grid_inside(b):
    """05c.roi_inside(b), taking arrays: is each grid point inside the shape?

    05c's disc test is plain arithmetic and takes arrays already; its polygon
    test is a Python loop per point, which over the ~4 million pixels of a
    crop's shape mask takes minutes. The polygon goes through _poly_inside
    instead, the same rule vectorised (tests/test_detection_overlays.py checks
    the two agree point for point)."""
    poly = G5.parse_poly(b.get("sec_poly", ""))
    if poly is None:
        return D5.roi_inside(b)
    poly = np.column_stack(G5.curator_to_grid(poly[:, 0], poly[:, 1]))
    return lambda gx, gy: _poly_inside(gx, gy, poly)


def affine_of(g):
    """(M, Minv) from a geometry row, as 05c.main builds them."""
    M = np.array([[float(g["m00"]), float(g["m01"]), float(g["m02"])],
                  [float(g["m10"]), float(g["m11"]), float(g["m12"])]])
    return M, G5.invert_affine(M)


def centroids(labels, x0, y0):
    """{label: (czi_x, czi_y)}: each label's centroid, as 05c computes it."""
    from skimage.measure import regionprops
    return {int(p.label): (x0 + p.centroid[1], y0 + p.centroid[0])
            for p in regionprops(np.asarray(labels))}


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


def scene_context(g, animal, slide, exact_cut, meas_rows):
    """What a section's pictures depend on besides rows, boxes and outlines:
    the affine that places the ROI shape, the identity on line 1, the exact
    cut nuclei are coloured by, and 06a's rows (counts, pct, region) that the
    strip prints."""
    return {"geometry": sorted((g or {}).items()), "animal": animal,
            "slide": slide, "exact_cut": repr(exact_cut),
            "measurements": [sorted(r.items()) for r in
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


def _slides():
    """{scene_uid: slide} from 00's manifest, or {} when there is none."""
    if not os.path.exists(MANIFEST_CSV):
        return {}
    with open(MANIFEST_CSV, newline="", encoding="utf-8") as fh:
        return {r.get("scene_uid", ""): r.get("slide", "")
                for r in csv.DictReader(fh)}


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


def crop_lines(rec):
    """The strip's two text lines for a crop, from its index row."""
    kind = "background" if rec["picture"] == "background" else "roi"
    region = "" if rec["region"] == "__background__" else rec["region"]
    one = (f"{rec['marker']} | fish {rec['animal']} | slide {rec['slide']} | "
           f"scene {rec['scene_uid']} | ROI {int(rec['roi_index']):02d} {kind}"
           + (f" {region}" if region else ""))
    return [one, _counts_line(rec)]


def _counts_line(rec):
    """Counts, cut and exclusions of an index row: line 2 of a strip."""
    if rec["cut"] == "":
        counted = f"{rec['n_counted']} counted, no cut: too few background nuclei"
    else:
        pct = f"{rec['pct_positive']}%" if rec["pct_positive"] != "" else "n/a"
        counted = (f"{rec['n_counted']} counted, {rec['n_positive']} positive "
                   f"({pct}), cut {rec['cut']}")
    return (f"{counted} | not counted: {rec['n_outside']} outside ROI, "
            f"{rec['n_artifact']} artifact, {rec['n_off_tissue']} off tissue | "
            f"{rec['n_censored']} censored")


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
    """Grey marker, nuclei, the shape in white, the strip stacked above."""
    from PIL import Image
    rgb = np.repeat(LO.grey(plane, lo, hi)[:, :, None], 3, axis=2)
    LO.paint(rgb, labels, kinds)
    rgb[outline] = LO.SHAPE_COLOUR
    top = LO.strip(rgb.shape[1], lines, True, UM_PX, CROP_BAR_UM)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with IO.atomic_save(path) as tmp:
        Image.fromarray(np.vstack([top, rgb])).save(tmp, format="PNG")


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
    crop: where two boxes both have a nucleus at a pixel, the box with the
    lower roi_index draws it (fill and outline); elsewhere each box draws
    its own. Shape outlines and tags of every box come after, in box order.
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
        inside = grid_inside(b)

        def in_px(xs, ys, x0=x0, y0=y0, inside=inside):
            gx, gy = G5.apply_affine(Minv, x0 + xs, y0 + ys)
            return inside(gx, gy)

        labels = outlines[bi][0] if bi in outlines else None
        marks.append((y0 - sy, x0 - sx, h, w, labels, kinds_by_roi.get(bi, {}),
                      LO.shape_mask_outline(h, w, in_px)))
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
            for top, left, h, w, labels, kinds, _ in marks:   # roi_index order
                at = _meet(top, left, h, w, r0, n, sw)
                if labels is None or at is None:
                    continue
                br, bc, ar, ac = at
                m0 = max(ar.start - 1, 0)
                m1 = min(ar.stop + 1, h)
                lab = labels[m0:m1]
                rows = slice(ar.start - m0, ar.stop - m0)
                here = lab[rows, ac] > 0
                free = here & ~taken[br, bc]
                code[br, bc][free] = LO.kind_codes(lab, kinds)[rows, ac][free]
                edge[br, bc][free] = LO.edges(lab)[rows, ac][free]
                taken[br, bc] |= here
            del taken
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
    if ident["cut"] == "":
        tot["n_positive"] = tot["pct_positive"] = ""
    else:
        tot["n_positive"] = sum(int(r["n_positive"]) for r in roi_recs)
        tot["pct_positive"] = (round(100 * tot["n_positive"] / tot["n_counted"], 1)
                               if tot["n_counted"] else "")
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
    for bi, (labels, x0, y0) in sorted(outlines.items()):
        rows = rows_by_roi.get(bi, {})
        kinds = {}
        for lid in centroids(labels, x0, y0):
            try:
                kinds[lid] = LO.kind_of(rows.get(lid), cut)
            except ValueError as exc:
                raise NotDrawn(f"roi{bi:02d}: {exc}") from None
        n = {k: sum(1 for v in kinds.values() if v == k) for k in LO.KINDS}
        counts = {"n_counted": n["positive"] + n["censored"] + n["negative"],
                  "n_positive": (n["positive"] + n["censored"]
                                 if cut is not None else ""),
                  "n_outside": n["outside"], "n_artifact": n["artifact"],
                  "n_off_tissue": n["off_tissue"], "n_censored": n["censored"]}
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
        frac = m.get("frac_positive", "")
        counts["pct_positive"] = round(float(frac) * 100, 1) if frac != "" else ""
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


def draw_crops(prep, uid, marker, boxes, ident, fp):
    """One crop per box with outlines. Returns the index rows written."""
    out, now = [], datetime.datetime.now().isoformat(timespec="seconds")
    folder = LO.overlay_dir(OUT_ROOT, marker, ident["animal"])
    for bi, (labels, x0, y0) in sorted(prep["outlines"].items()):
        b = boxes[bi - 1]
        picture = "background" if b["roi_kind"] == "background" else "roi"
        name = LO.overlay_name(marker, uid, picture, bi, b["region"])
        path = os.path.join(folder, name)
        rec = {"path": _rel(path), "marker": marker, "animal": ident["animal"],
               "slide": ident["slide"], "scene_uid": uid, "picture": picture,
               "roi_index": bi, "region": b["region"],
               "cut": ident["cut"], "um_per_px": UM_PX,
               "outlines": prep["how"], "drawn_at": now, "fingerprint": fp}
        rec.update(prep["counts_by_roi"][bi])
        rec = {k: ("" if rec[k] is None else str(rec[k])) for k in INDEX_KEYS}
        inside = grid_inside(b)
        Minv = prep["Minv"]

        def in_px(xs, ys, x0=x0, y0=y0, inside=inside):
            gx, gy = G5.apply_affine(Minv, x0 + xs, y0 + ys)
            return inside(gx, gy)

        h, w = labels.shape
        outline = LO.shape_mask_outline(h, w, in_px)
        draw_crop(path, prep["planes"][bi], labels, prep["kinds_by_roi"][bi],
                  outline, prep["lo"], prep["hi"], crop_lines(rec))
        out.append(rec)
    return out


def _rel(path):
    return os.path.relpath(path, OUT_DIR).replace(os.sep, "/")


def _abs(rel):
    return os.path.join(OUT_DIR, *rel.split("/"))


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
    _default = G5.MARKERS[0] if G5.MARKERS else None
    ap.add_argument("--marker", choices=G5.MARKERS, default=_default,
                    help=f"which marker's sections to draw (default {_default})")
    ap.add_argument("--animal", nargs="+", metavar="A",
                    help="only these fish")
    ap.add_argument("--no-scenes", action="store_true",
                    help="skip the whole-scene pictures")
    ap.add_argument("--no-crops", action="store_true",
                    help="skip the ROI and background-disc crops")
    ap.add_argument("--limit", type=int, help="only the first N sections")
    ap.add_argument("--force", action="store_true",
                    help="redraw sections whose inputs have not changed")
    args = ap.parse_args()

    why = refusal()
    if why:
        print("!! REFUSED: " + why)
        return 2
    marker = args.marker
    G5.use_marker(marker)
    if not os.path.exists(G5.BOX_CSV):
        print(f"no {G5.BOX_CSV} - run 05a_roi_geometry.py --marker {marker} first")
        return 1
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
    n_drawn = n_same = n_pictures = n_small = 0
    print(f"{len(todo)} sections")
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
                n_same += 1
                continue
            kinds_drawn = {p for k in todo_kinds for p in k}
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
                    scene_rect(g)       # refused before anything is read
                path = os.path.join(CONFIG["source_dir"], g["czi_file"])
                recs = []
                with CR.open_czi(path, int(float(g.get("scene_w") or 0)) or None,
                                 pyczi=pyczi) as doc:
                    prep = prepare_scene(doc, uid, marker, boxes, g, raw, header,
                                         cut, meas_by.get(uid, {}), get_model)
                    if prep["how"] == "re-segmented":
                        fp = fingerprint(uid, marker, raw, ident["cut"], boxes,
                                         context)
                    if "scene" in kinds_drawn:
                        rec, lines = scene_record(prep, uid, marker, boxes,
                                                  ident, fp, meas_by.get(uid, {}))
                        draw_scene(doc, g, marker, boxes, prep["outlines"],
                                   prep["kinds_by_roi"], prep["lo"], prep["hi"],
                                   _abs(rec["path"]), lines)
                        recs.append(rec)
                if "roi" in kinds_drawn:
                    recs += draw_crops(prep, uid, marker, boxes, ident, fp)
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
                n_drawn += 1
                n_pictures += len(recs)
                n_small += sum(1 for bi, b in enumerate(boxes, 1)
                               if bi not in prep["outlines"]
                               and min(int(b["czi_w"]), int(b["czi_h"])) < 8)
                save_tables()
                n_crops = sum(1 for r in recs if r["picture"] != "scene")
                print(f"  {uid}: {n_crops} crops"
                      + (" + scene" if "scene" in kinds_drawn else "")
                      + f" ({prep['how']} outlines)")
                continue
            # Pictures that would disagree with the numbers now go; any other
            # failure leaves the ones already drawn, index rows and all.
            if stale:
                drop_pictures(uid, set(CROP_PICTURES) | set(SCENE_PICTURES))
            fails.append({"marker": marker, "animal": ident["animal"],
                          "slide": ident["slide"], "scene_uid": uid,
                          "reason": reason})
            save_tables()
            print(f"  {uid}: NOT DRAWN - {reason}")
    save_tables()
    print(f"{n_pictures} pictures over {n_drawn} sections -> {OUT_DIR}"
          + (f"; {n_same} unchanged since drawn" if n_same else ""))
    if n_small:
        print(f"{n_small} boxes under 8 px not drawn (05c does not segment "
              f"them; 06a counts them as 0)")
    if fails:
        print(f"{len(fails)} not drawn: see {FAILURES_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
