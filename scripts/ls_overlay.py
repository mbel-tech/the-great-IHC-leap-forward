"""Detection-overlay drawing: what colour a nucleus is, and how it is painted.

Shared by the stage that writes overlay pictures and by 06a, so the rule that
decides "positive" lives in one place. Pure numpy; PIL is imported lazily by
anything that writes a file, so importing this module (06a does) adds no hard
dependency and never reads the study config.

Native pixels only: nothing here resamples. Every function works on the array
it is given at that array's own resolution.
"""

import os
import re
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
    flags is drawn as artifact. With no cut (`cut is None`) a counted,
    uncensored nucleus cannot be judged and is drawn negative.

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
    if row.get("censored") == "1":
        return "censored"
    if cut is None:
        return "negative"
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


def stretch(sample):
    """(p1, p99.8) of `sample`: the display range, as 05c's overlay uses."""
    lo, hi = np.percentile(sample, (1, 99.8))
    return float(lo), float(hi)


def grey(plane, lo, hi):
    """`plane` mapped lo->0, hi->255, clipped, as a uint8 HxW array."""
    g = np.clip((np.asarray(plane, float) - lo) / max(hi - lo, 1e-6), 0, 1)
    return (g * 255).astype(np.uint8)


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
    top = int(labels.max()) if labels.size else 0
    lut = np.zeros(top + 1, np.uint8)
    for lid, kind in kinds.items():
        if 0 < lid <= top:
            lut[lid] = KINDS.index(kind) + 1
    code = lut[np.clip(labels, 0, top)]
    code[labels <= 0] = 0
    edge = edges(labels)
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


def strip(width, lines, legend, um_px, bar_um):
    """Black strip with white text: `lines`, then optionally the colour legend,
    then a scale bar of bar_um micrometres. Returns uint8 RGB (h, width, 3).

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
    bar_px = max(1, round(bar_um / um_px))
    bar_px = min(bar_px, room)

    height = pad * 2 + row_h * (len(text_rows) + legend_rows + 1)
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
    bar_t = 4
    by = y + (row_h - bar_t) // 2
    d.rectangle([pad, by, pad + bar_px - 1, by + bar_t - 1], fill=(255, 255, 255))
    d.text((pad + bar_px + size, y), f"{bar_um} \u00b5m", font=font, fill=(255, 255, 255))
    return np.asarray(img, np.uint8).copy()


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
        if self._f.closed:
            return
        if self.rows != self.height:
            got = self.rows
            self.abort()
            raise ValueError(f"{got} rows written, height is {self.height}")
        self._buf += self._z.flush()
        self._flush(final=True)
        self._chunk(b"IEND", b"")
        self._f.close()
        os.replace(self._part, self.path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.abort()
            return False
        self.close()
        return False
