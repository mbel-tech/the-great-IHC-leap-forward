"""Detection-overlay drawing: what colour a nucleus is, and how it is painted.

Shared by the stage that writes overlay pictures and by 06a, so the rule that
decides "positive" lives in one place. Pure numpy; PIL is imported lazily by
anything that writes a file, so importing this module (06a does) adds no hard
dependency and never reads the study config.

Native pixels only: nothing here resamples. Every function works on the array
it is given at that array's own resolution.
"""

import hashlib
import json
import os
import re
import statistics
import struct
import zlib

import numpy as np

#: Draw order of the legend; also the full set of values kind_of can return.
KINDS = ("positive", "censored", "negative", "outside", "artifact", "off_tissue")

#: Outline colour per kind (RGB). Positive and censored fills are magenta;
#: censored differs from positive only by its white outline.
COLOURS = {
    "positive": (255, 0, 255),
    "censored": (255, 255, 255),
    "negative": (0, 255, 255),
    "outside": (255, 255, 0),
    "artifact": (255, 128, 0),
    "off_tissue": (64, 128, 255),
}
#: Fill colour for the kinds that are filled, and the weight of that fill.
FILL_COLOURS = {"positive": (255, 0, 255), "censored": (255, 0, 255)}
FILL_ALPHA = 0.4
#: Outline of the ROI shape / disc itself.
SHAPE_COLOUR = (255, 255, 255)


def is_positive(median, censored, cut):
    """The positivity rule, the one 06a counts by: censored, or above the cut."""
    return bool(censored or median > cut)


def kind_of(row, cut):
    """Which KINDS a roi_nuclei.csv row is drawn as.

    `row` is a dict of strings; None means the nucleus lies outside the ROI.
    Artifact is checked before off_tissue, as 06a does, so a nucleus with both
    flags is drawn as artifact. With no cut (`cut is None`) no counted nucleus
    is called, censored ones included (06a gives such a section no n_positive),
    so every counted nucleus is drawn negative: not called.

    A counted, uncensored row whose marker_median is empty or not a number
    cannot be judged either; 06a would crash on float(""), so this raises a
    ValueError naming the scene and nucleus rather than guess. (Flagged rows
    and the no-cut case never read the median, so they are unaffected.)
    """
    if row is None:
        return "outside"
    if row.get("artifact") == "1":
        return "artifact"
    if row.get("off_tissue") == "1":
        return "off_tissue"
    if cut is None:
        return "negative"
    if row.get("censored") == "1":
        return "censored"
    try:
        median = float(row["marker_median"])
    except (KeyError, ValueError):
        where = ", ".join(f"{k}={row[k]}" for k in ("scene_uid", "roi_index", "label", "nucleus_id")
                          if k in row) or "no id in row"
        raise ValueError(f"marker_median is {row.get('marker_median')!r}, not a number "
                         f"({where})") from None
    if is_positive(median, False, cut):
        return "positive"
    return "negative"


# ---- the per-section cut ----------------------------------------------------
#
# 06a counts by it and 05c draws by it, so it is written once, here. The
# arithmetic is 06a's to the operation (spread first, then times 3): a cut
# that differed in its last bit would call a nucleus sitting on it differently.

#: Fewest background nuclei a section needs for a cut; with fewer it has none.
CUT_MIN_NUCLEI = 5
#: The cut is this many spreads above the background median...
CUT_K = 3.0
#: ...and a spread is the MAD scaled to a normal SD.
MAD_SCALE = 1.4826


def mad(xs):
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def background_stats(values):
    """(median, spread, n) of a section's background medians, or None when
    there are fewer than CUT_MIN_NUCLEI. spread = MAD * 1.4826."""
    if len(values) < CUT_MIN_NUCLEI:
        return None
    return statistics.median(values), mad(values) * MAD_SCALE, len(values)


def section_cut(values):
    """median + 3 * 1.4826 * MAD of `values` (background_stats), or None."""
    got = background_stats(values)
    if got is None:
        return None
    m, s, _ = got
    return m + CUT_K * s


def section_values(rows):
    """The values a section's cut is taken from, in row order: marker_median
    of each background-disc row that is not censored, not on an artifact and
    not off the tissue - 06a's reading of each flag ("1" is set; an absent
    off_tissue column is not)."""
    out = []
    for r in rows:
        if r.get("artifact") == "1" or r.get("off_tissue") == "1":
            continue
        if r.get("roi_kind") == "background" and r.get("censored") != "1":
            out.append(float(r["marker_median"]))
    return out


def reads_nucleus_shaped(value):
    """How a `nucleus_shaped` cell reads, INCLUDING when there is no cell.

    THE LS COMPATIBILITY RULE, and it is the reason this is a function rather
    than `== "1"` inline. A roi_nuclei.csv written before 2026-09-08 has no
    such column, and every object in it came from the nuclear channel
    segmented by StarDist, because that was the only route 05c had. Its
    silence is therefore not "unknown" - it is True, and any other reading
    would withhold the Abercrombie correction from 961,233 rows that are
    nuclei. 05c backfills the column with exactly this reading when it resumes
    onto such a file.
    """
    if value is None or value == "":
        return True
    return str(value).strip().lower() not in ("0", "false", "no")


def own_channel(row):
    """Whether a roi_nuclei.csv row is a nucleus found on its marker's own
    channel - 06a's test for withholding the fraction positive."""
    return (bool(row.get("segmented_on")) and row["segmented_on"] == row.get("marker")
            and reads_nucleus_shaped(row.get("nucleus_shaped")))


def frac_positive(n_pos, n_nuc, cut, withheld):
    """06a's frac_positive: n_pos / n_nuc to 4 places, or "" with no cut, no
    nuclei, or a withheld denominator (`withheld` truthy)."""
    if cut is not None and n_nuc and not withheld:
        return round(n_pos / n_nuc, 4)
    return ""


def pct_of(frac):
    """A fraction as the % a picture prints (one decimal), "" for ""."""
    return round(float(frac) * 100, 1) if frac != "" else ""


def classify(ids, rows, cut):
    """(kinds, counts) of one box's labelled objects.

    `ids` are its label ids, `rows` {label id: roi_nuclei.csv row as strings}
    of the ones counted in the ROI (absent: outside the shape), `cut` the exact
    cut or None. counts has n_counted, n_positive ("" with no cut),
    n_outside, n_artifact, n_off_tissue and n_censored - censored is counted
    off the rows, not the kinds: with no cut a censored nucleus is drawn not
    called (negative), yet 06a still reports it in n_censored. kind_of's
    ValueError (a median that is not a number) propagates.
    """
    kinds = {}
    for lid in ids:
        kinds[lid] = kind_of(rows.get(lid), cut)
    n = {k: sum(1 for v in kinds.values() if v == k) for k in KINDS}
    n_cen = sum(1 for lid, k in kinds.items()
                if k in ("positive", "censored", "negative")
                and rows[lid].get("censored") == "1")
    return kinds, {"n_counted": n["positive"] + n["censored"] + n["negative"],
                   "n_positive": (n["positive"] + n["censored"]
                                  if cut is not None else ""),
                   "n_outside": n["outside"], "n_artifact": n["artifact"],
                   "n_off_tissue": n["off_tissue"], "n_censored": n_cen}


# ---- the overlay index, and the words on a crop -------------------------------

#: Columns of detection_overlays/index.csv (06i's): one row per picture.
INDEX_KEYS = ["path", "marker", "animal", "slide", "scene_uid", "picture",
              "roi_index", "region", "n_counted", "n_positive", "pct_positive",
              "cut", "n_outside", "n_artifact", "n_off_tissue", "n_censored",
              "um_per_px", "outlines", "drawn_at", "fingerprint"]


def crop_lines(rec):
    """The strip's two text lines for a crop, from its index row."""
    kind = "background" if rec["picture"] == "background" else "roi"
    region = "" if rec["region"] == "__background__" else rec["region"]
    one = (f"{rec['marker']} | fish {rec['animal']} | slide {rec['slide']} | "
           f"scene {rec['scene_uid']} | ROI {int(rec['roi_index']):02d} {kind}"
           + (f" {region}" if region else ""))
    return [one, counts_line(rec)]


def counts_line(rec):
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


def data_fingerprint(rows, exact_cut, boxes, geometry, npz_path, animal, slide):
    """sha1 of what a section's crops are drawn from, as 05c knows it.

    Its roi_nuclei.csv rows (lists of the cells as the file holds them, in
    file order), its exact cut (None for none), its box rows, its geometry
    row, the bytes of its outline file, its fish and slide. 05c stamps the
    crops it draws with it; 06i computes it again from the files and keeps a
    05c crop only when the two agree. No word of 06a's tables is in it - 05c
    has none - so 06i checks those itself.
    """
    h = hashlib.sha1()
    for r in rows:
        h.update(("\x1f".join(r) + "\n").encode("utf-8"))
    h.update(b"\x1ecut\x1e" + repr(exact_cut).encode("utf-8"))
    h.update(b"\x1eboxes\x1e" + json.dumps([sorted(b.items()) for b in boxes])
             .encode("utf-8"))
    h.update(b"\x1egeometry\x1e" + json.dumps(sorted((geometry or {}).items()))
             .encode("utf-8"))
    h.update(b"\x1eid\x1e" + json.dumps([animal, slide]).encode("utf-8"))
    h.update(b"\x1enpz\x1e")
    if os.path.exists(npz_path):
        with open(npz_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def stretch(sample):
    """(p1, p99.8) of `sample`: the display range, as 05c's overlay uses."""
    lo, hi = np.percentile(sample, (1, 99.8))
    return float(lo), float(hi)


def grey(plane, lo, hi):
    """`plane` mapped lo->0, hi->255, clipped, as a uint8 HxW array.

    float32, worked in place: one temporary the size of `plane`, not three
    float64 ones - a whole-scene band is 1024 x 20,000 px. The crops and the
    scene picture both come through here, so they stay pixel-identical."""
    g = np.array(plane, np.float32)
    g -= np.float32(lo)
    g /= np.float32(max(hi - lo, 1e-6))
    np.clip(g, 0, 1, out=g)
    g *= np.float32(255)
    return g.astype(np.uint8)


def edges(labels):
    """1-px outline: labelled pixels with a 4-neighbour of a different label.

    Array shifts rather than skimage.find_boundaries, so the frozen build
    carries no extra import (same as 05c's write_overlay).
    """
    return _edge(labels) & (labels > 0)


def _edge(lab):
    edge = np.zeros(lab.shape, bool)
    edge[:-1, :] |= lab[:-1, :] != lab[1:, :]
    edge[1:, :] |= lab[1:, :] != lab[:-1, :]
    edge[:, :-1] |= lab[:, :-1] != lab[:, 1:]
    edge[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    return edge


def paint(rgb, labels, kinds):
    """Draw nuclei onto `rgb` (HxWx3 uint8) in place: all fills, then outlines.

    `kinds` maps label id -> a KINDS value; labels not in it are left alone.
    Fills first so a neighbour's fill never covers another nucleus's outline.

    One lookup table (label id -> kind code, 0 = not drawn) is indexed with
    `labels` once, then each kind is painted in a single pass: at most
    len(KINDS) full-image operations however many nuclei there are. A mask per
    label would cost labels x H x W, minutes on a real crop.
    """
    paint_codes(rgb, kind_codes(labels, kinds), edges(labels))


def kind_codes(labels, kinds):
    """uint8 array shaped like `labels`: KINDS.index(kind) + 1 of each
    pixel's label, 0 where there is no label or it is not in `kinds`."""
    top = int(labels.max()) if labels.size else 0
    lut = np.zeros(top + 1, np.uint8)
    for lid, kind in kinds.items():
        if 0 < lid <= top:
            lut[lid] = KINDS.index(kind) + 1
    code = lut[np.clip(labels, 0, top)]
    code[labels <= 0] = 0
    return code


def paint_codes(rgb, code, edge):
    """paint's drawing step, given each pixel's kind code (kind_codes) and
    whether it is a nucleus outline (edges): every fill, then every outline,
    each pixel blended at most once. A caller compositing several label
    images (06i's whole-scene picture) builds one code map and paints once."""
    for i, kind in enumerate(KINDS, start=1):
        fill = FILL_COLOURS.get(kind)
        m = code == i
        if fill is not None and m.any():
            mix = (1 - FILL_ALPHA) * rgb[m].astype(float) + FILL_ALPHA * np.array(fill, float)
            rgb[m] = np.rint(mix).astype(np.uint8)
    for i, kind in enumerate(KINDS, start=1):
        rgb[edge & (code == i)] = COLOURS[kind]


def shape_mask_outline(h, w, inside):
    """1-px outline of the ROI shape, as a bool (h, w) array.

    `inside(xs, ys)` gets float arrays of pixel indices (xs = column, ys = row,
    both shaped (h, w); the index IS the pixel's coordinate, no +0.5) and
    returns a bool array. The outline is the mask's own boundary pixels, by the
    same 4-neighbour rule as `edges`, so it lies inside the shape.
    """
    ys, xs = np.mgrid[0:h, 0:w]
    mask = np.asarray(inside(xs.astype(float), ys.astype(float)), bool)
    return _edge(mask) & mask


LEGEND_LABELS = {
    "positive": "positive",
    "censored": "censored (clipped)",
    "negative": "negative",
    "outside": "outside ROI",
    "artifact": "artifact",
    "off_tissue": "off tissue",
}


def overlay_dir(out_root, marker, animal):
    """Folder holding one animal's overlay pictures for one marker."""
    return os.path.join(out_root, "detection_overlays", marker, animal)


def overlay_name(marker, uid, picture, roi_index=None, region=""):
    """File name of one overlay picture.

    `picture` is "scene", "roi" or "background"; roi_index numbers the ROI (or
    the background patch). Anything in the region that is not a letter, digit,
    dot or hyphen becomes "_" so the name is safe on every file system.
    """
    if picture == "scene":
        return f"{marker}_{uid}_scene.png"
    if picture == "background":
        return f"{marker}_{uid}_bg{int(roi_index):02d}.png"
    if picture != "roi":
        raise ValueError(f"unknown picture kind {picture!r}")
    safe = re.sub(r"[^A-Za-z0-9.\-]+", "_", region or "").strip("_")
    return f"{marker}_{uid}_roi{int(roi_index):02d}" + (f"_{safe}" if safe else "") + ".png"


def _wrap(font, text, width):
    """Split `text` into lines no wider than `width`, breaking at " | " first,
    then at spaces, then (a single over-long word) between characters."""
    def fits(t):
        return font.getlength(t) <= width

    def pack(pieces, sep):
        out, cur = [], ""
        for piece in pieces:
            trial = piece if not cur else cur + sep + piece
            if cur and not fits(trial):
                out.append(cur)
                cur = piece
            else:
                cur = trial
        if cur:
            out.append(cur)
        return out

    lines = []
    for chunk in pack(text.split(" | "), " | "):
        if fits(chunk):
            lines.append(chunk)
            continue
        for sub in pack(chunk.split(" "), " "):
            while not fits(sub) and len(sub) > 1:
                n = len(sub) - 1
                while n > 1 and not fits(sub[:n]):
                    n -= 1
                lines.append(sub[:n])
                sub = sub[n:]
            lines.append(sub)
    return lines or [""]


#: Bar lengths (um) tried, longest first, when the one asked for does not fit.
BAR_STEPS = (500, 200, 100, 50, 20, 10)


def bar_label(um):
    return f"{um} \u00b5m"


def fit_bar(bar_um, um_px, room, label_width, gap=0):
    """(um, px) of the scale bar to draw in `room` px, or None for no bar.

    bar_um when it and its label (`label_width(text)` px, `gap` px after the
    bar) fit; else the longest of BAR_STEPS under bar_um that does; else
    None. A bar is never shortened to fit: its length is always the length
    its label says."""
    for um in [bar_um] + [s for s in BAR_STEPS if s < bar_um]:
        px = max(1, round(um / um_px))
        if px + gap + label_width(bar_label(um)) <= room:
            return um, px
    return None


def strip(width, lines, legend, um_px, bar_um):
    """Black strip with white text: `lines`, then optionally the colour legend,
    then a scale bar of bar_um micrometres - or, when that does not fit the
    width, the longest of BAR_STEPS that does, labelled with its own length;
    no bar when none fits (fit_bar). Returns uint8 RGB (h, width, 3).

    Lines wider than `width` are wrapped, never cut: later stages put long
    summaries here. PIL is imported here, not at module level, so importing
    ls_overlay (06a does) needs no Pillow.
    """
    from PIL import Image, ImageDraw, ImageFont
    size = max(14, width // 90)
    font = ImageFont.load_default(size=size)
    pad = size // 2
    row_h = int(size * 1.4)
    room = max(width - 2 * pad, 1)

    text_rows = [ln for raw in lines for ln in _wrap(font, str(raw), room)]
    sw = size  # swatch edge
    items = []  # (x, y-row, kind) legend entries, flowed onto rows
    legend_rows = 0
    if legend:
        x, legend_rows = 0, 1
        for kind in KINDS:
            w = sw + pad // 2 + font.getlength(LEGEND_LABELS[kind]) + size
            if x and x + w > room:
                x, legend_rows = 0, legend_rows + 1
            items.append((x, legend_rows - 1, kind))
            x += int(w)
    bar = fit_bar(bar_um, um_px, room, font.getlength, gap=size)

    height = pad * 2 + row_h * (len(text_rows) + legend_rows + (1 if bar else 0))
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    y = pad
    for ln in text_rows:
        d.text((pad, y), ln, font=font, fill=(255, 255, 255))
        y += row_h
    for x, r, kind in items:
        top = y + r * row_h + (row_h - sw) // 2
        x0 = pad + x
        fill = FILL_COLOURS.get(kind)
        d.rectangle([x0, top, x0 + sw - 1, top + sw - 1],
                    fill=fill, outline=COLOURS[kind])
        d.text((x0 + sw + pad // 2, y + r * row_h), LEGEND_LABELS[kind],
               font=font, fill=(255, 255, 255))
    y += legend_rows * row_h
    if bar:
        um, bar_px = bar
        bar_t = 4
        by = y + (row_h - bar_t) // 2
        d.rectangle([pad, by, pad + bar_px - 1, by + bar_t - 1], fill=(255, 255, 255))
        d.text((pad + bar_px + size, y), bar_label(um), font=font, fill=(255, 255, 255))
    return np.asarray(img, np.uint8).copy()


def crop_image(plane, labels, kinds, outline, lo, hi, lines, um_px, bar_um):
    """One crop as uint8 RGB: the marker in grey (lo..hi), the nuclei painted
    by kind, the shape `outline` in white, and the strip of `lines`, the
    legend and a bar_um bar stacked above - never over - the image. The image
    area is `plane`'s own shape: nothing resampled. 05c and 06i both draw
    every crop through here."""
    rgb = np.repeat(grey(plane, lo, hi)[:, :, None], 3, axis=2)
    paint(rgb, labels, kinds)
    rgb[outline] = SHAPE_COLOUR
    return np.vstack([strip(rgb.shape[1], lines, True, um_px, bar_um), rgb])


def save_png(path, image):
    """Write uint8 RGB `image` to `path` as PNG, atomically, making its folder.
    PIL and ls_io are imported here, as strip imports PIL: 06a imports this
    module and needs neither."""
    from PIL import Image
    import ls_io
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with ls_io.atomic_save(path) as tmp:
        Image.fromarray(image).save(tmp, format="PNG")


class PngRows:
    """Write an 8-bit RGB PNG a block of rows at a time.

    Overlays can be far larger than is comfortable to hold (a whole scene at
    native pixels), so rows are compressed as they arrive and IDAT chunks are
    flushed once ~1 MiB has built up. Output goes to `path + ".part"` and is
    renamed only on a complete close, so a crash never leaves a truncated PNG
    under the real name. Usable as a context manager: an exception inside the
    `with` removes the .part and propagates.
    """

    CHUNK = 1 << 20

    def __init__(self, path, width, height):
        self.path, self.width, self.height = path, int(width), int(height)
        self.rows = 0
        self._z = zlib.compressobj(6)
        self._buf = bytearray()
        self._part = path + ".part"
        self._f = open(self._part, "wb")
        self._f.write(b"\x89PNG\r\n\x1a\n")
        self._chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))

    def _chunk(self, tag, data):
        self._f.write(struct.pack(">I", len(data)) + tag + data
                      + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    def _flush(self, final=False):
        while len(self._buf) >= self.CHUNK or (final and self._buf):
            self._chunk(b"IDAT", bytes(self._buf[:self.CHUNK]))
            del self._buf[:self.CHUNK]

    def write(self, rows):
        a = np.asarray(rows)
        if a.dtype != np.uint8 or a.ndim != 3 or a.shape[1:] != (self.width, 3):
            raise ValueError(f"rows must be uint8 (k, {self.width}, 3), "
                             f"got {a.dtype} {a.shape}")
        if self.rows + a.shape[0] > self.height:
            raise ValueError(f"{self.rows + a.shape[0]} rows written, height is {self.height}")
        raw = np.zeros((a.shape[0], 1 + self.width * 3), np.uint8)  # filter byte 0
        raw[:, 1:] = a.reshape(a.shape[0], -1)
        self._buf += self._z.compress(raw.tobytes())
        self.rows += a.shape[0]
        self._flush()

    def abort(self):
        """Close and delete the .part without producing a PNG."""
        if not self._f.closed:
            self._f.close()
        if os.path.exists(self._part):
            os.remove(self._part)

    def close(self):
        """Finish the PNG and rename it into place. Any failure on the way
        (a full disk) closes the file, deletes the .part and re-raises; a
        second close, after success or failure, does nothing."""
        if self._f.closed:
            return
        if self.rows != self.height:
            got = self.rows
            self.abort()
            raise ValueError(f"{got} rows written, height is {self.height}")
        try:
            self._buf += self._z.flush()
            self._flush(final=True)
            self._chunk(b"IEND", b"")
            self._f.close()
            os.replace(self._part, self.path)
        except BaseException:
            self.abort()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.abort()
            return False
        self.close()
        return False
