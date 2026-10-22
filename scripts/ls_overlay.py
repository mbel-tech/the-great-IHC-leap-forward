"""Detection-overlay drawing: what colour a nucleus is, and how it is painted.

Shared by the stage that writes overlay pictures and by 06a, so the rule that
decides "positive" lives in one place. Pure numpy; PIL is imported lazily by
anything that writes a file, so importing this module (06a does) adds no hard
dependency and never reads the study config.

Native pixels only: nothing here resamples. Every function works on the array
it is given at that array's own resolution.
"""

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
    """
    if row is None:
        return "outside"
    if row.get("artifact") == "1":
        return "artifact"
    if row.get("off_tissue") == "1":
        return "off_tissue"
    if row.get("censored") == "1":
        return "censored"
    if cut is not None and is_positive(float(row["marker_median"]), False, cut):
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
    """
    edge = edges(labels)
    for lid, kind in kinds.items():
        m = labels == lid
        if not m.any():
            continue
        fill = FILL_COLOURS.get(kind)
        if fill is not None:
            mix = (1 - FILL_ALPHA) * rgb[m].astype(float) + FILL_ALPHA * np.array(fill, float)
            rgb[m] = np.rint(mix).astype(np.uint8)
    for lid, kind in kinds.items():
        rgb[edge & (labels == lid)] = COLOURS[kind]


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
