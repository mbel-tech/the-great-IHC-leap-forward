"""Shape and texture of one nucleus, from its label image and the planes under it.

`features(label_img, nucleus_id, dapi, marker, other=None)` is what
06l_nucleus_features.py writes per row of roi_nuclei.csv, for the
autofluorescence score (06k) to learn from. The keys:

- `roundness`: 4 pi A / P^2, A the pixel area and P skimage's perimeter of the
  mask. About 0.9 for a filled disc of a nucleus's size. A pixel estimate, and
  for an object of a few pixels it can come out above 1 (37 for a line of
  three), so it is capped at 1; blank for an object with no perimeter.
- `solidity`: area over convex-hull area, both counted in pixels: the hull is
  the lattice pixels whose CENTRES lie in the convex hull of the nucleus's
  pixel centres. skimage's own `solidity` takes the hull of the pixel CORNERS,
  which no digital disc fills: it reads 0.86 for a disc of radius 4 px and
  0.95 at 12 px, so a small round nucleus would look less solid than a large
  one. Counted this way a digital disc is 1.0 at every size.
- `marker_cv`: the coefficient of variation of the marker inside the nucleus,
  taken ABOVE THE BOX'S OWN GLASS: sd / max(mean - b, s), with b the median and
  s the 1.4826 x MAD of the box's pixels that are in no nucleus. A plain
  sd / mean moves with an additive background, and two nuclei of different
  brightness swap order under the ~2x background batch between animals; this
  does not move at all under x * gain + offset. The floor s keeps a nucleus at
  the glass's level from dividing by about zero. Blank ("") when the box has no
  glass pixels, or the nucleus is at or below the glass and the glass is flat.
- `marker_spots`: the local maxima inside the nucleus above its own median +
  3 x 1.4826 x MAD. A maximum is a pixel at least as bright as each of its 8
  neighbours inside the nucleus; a flat-topped maximum is counted once. With a
  MAD of 0 the threshold is the median itself (strictly above it). Comparisons
  only, so the count does not move under x * gain + offset.
- `other_marker_median`: the other marker's median inside the nucleus, under
  the multiplex layout; "" when `other` is None (the paired layout).

`dapi` is accepted for the stated signature and feeds no key: area and DAPI
intensity are roi_nuclei.csv's already.

Every intensity is taken in float64, so a uint16 plane and its transform
compare exactly.
"""

import numpy as np
from scipy import ndimage

#: k in median + k x 1.4826 x MAD for a spot.
SPOT_K = 3.0
_MAD_SD = 1.4826
#: 8-connectivity, for the maxima and for counting a plateau once.
_EIGHT = np.ones((3, 3), bool)


def bboxes(label_img):
    """{label: (row slice, column slice)} of every label, from one pass."""
    out = {}
    for i, sl in enumerate(ndimage.find_objects(np.asarray(label_img)), 1):
        if sl is not None:
            out[i] = sl
    return out


def background(label_img, marker):
    """(median, 1.4826 x MAD) of the marker over the box's pixels in no
    nucleus, or None when every pixel is in one."""
    v = np.asarray(marker, dtype=np.float64)[np.asarray(label_img) == 0]
    if v.size == 0:
        return None
    med = float(np.median(v))
    return med, _MAD_SD * float(np.median(np.abs(v - med)))


_glass = background


def _pad(sl, shape):
    """`sl` grown by one pixel each side, inside `shape`."""
    return tuple(slice(max(s.start - 1, 0), min(s.stop + 1, n)) for s, n in zip(sl, shape))


def _hull_pixels(mask):
    """Pixels whose centres lie in the convex hull of `mask`'s pixel centres.

    A nucleus whose pixels lie on one line (or is one pixel) is its own hull:
    qhull has no 2-D hull to give for it.
    """
    ys, xs = np.nonzero(mask)
    pts = np.c_[ys, xs].astype(np.float64)
    if len(pts) < 3 or np.linalg.matrix_rank(pts - pts.mean(axis=0)) < 2:
        return int(len(pts))
    from skimage.morphology import convex_hull_image
    return max(int(convex_hull_image(mask, offset_coordinates=False).sum()), len(pts))


def _spots(vals2d, mask):
    """Local maxima of `vals2d` inside `mask` above the spot threshold."""
    v = vals2d[mask]
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    thr = med + SPOT_K * _MAD_SD * mad
    a = np.where(mask, vals2d, -np.inf)
    peak = mask & (a >= ndimage.maximum_filter(a, footprint=_EIGHT, mode="constant",
                                               cval=-np.inf)) & (a > thr)
    if not peak.any():
        return 0
    _lab, n = ndimage.label(peak, structure=_EIGHT)
    return int(n)


def features(label_img, nucleus_id, dapi, marker, other=None, *, bbox=None,
             background=None):
    """The five features of nucleus `nucleus_id`. See the module docstring.

    `bbox` (from `bboxes`) and `background` (from `background`) may be passed
    in by a caller measuring every nucleus of a box, so the box is scanned once
    rather than once per nucleus; each is computed here when absent.
    """
    del dapi
    labels = np.asarray(label_img)
    nid = int(nucleus_id)
    if bbox is None:
        found = bboxes((labels == nid).astype(np.uint8))
        if 1 not in found:
            raise ValueError(f"nucleus {nid} is not in the label image")
        bbox = found[1]
    if background is None:
        background = _glass(labels, marker)
    sl = _pad(bbox, labels.shape)
    mask = labels[sl] == nid
    if not mask.any():
        raise ValueError(f"nucleus {nid} is not in the label image")

    from skimage.measure import regionprops
    p = regionprops(mask.astype(np.uint8))[0]
    area, perim = float(p.area), float(p.perimeter)
    roundness = min(4.0 * np.pi * area / (perim * perim), 1.0) if perim > 0 else ""
    solidity = area / _hull_pixels(mask)

    m = np.asarray(marker, dtype=np.float64)[sl]
    vals = m[mask]
    cv = ""
    if background is not None:
        b, s = background
        denom = max(float(vals.mean()) - b, s)
        if denom > 0:
            cv = float(vals.std()) / denom
    spots = _spots(m, mask)

    om = ""
    if other is not None:
        om = float(np.median(np.asarray(other, dtype=np.float64)[sl][mask]))
    return {"roundness": roundness, "solidity": solidity, "marker_cv": cv,
            "marker_spots": spots, "other_marker_median": om}
