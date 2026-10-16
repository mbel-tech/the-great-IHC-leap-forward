"""The tutorial's curation check: a learner's region outlines against an expert's.

Both sides are ROI-curator state, {section uid: {"polys": [{"v": [x0, y0, x1,
y1, ...], "roi": n, "region": name, ...}], ...}}, in the section's own pixels.
ROIs are matched by number on the same section; an ROI drawn in several parts
is the union of its parts. Overlap is intersection over union, worked out by
filling both outlines on the section's pixel grid (no geometry library).
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

GOOD = 0.5


@dataclass(frozen=True)
class Result:
    section: str
    roi: int
    region: str
    kind: str           # good / off / missing / extra
    overlap: float


def _fill(polys, x0, y0, w, h, k):
    img = Image.new("1", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for v in polys:
        pts = [((v[i] - x0) * k, (v[i + 1] - y0) * k) for i in range(0, len(v) - 1, 2)]
        if len(pts) >= 3:
            draw.polygon(pts, fill=1)
    return np.asarray(img, dtype=bool)


def iou(a, b):
    """Overlap of two lists of polygons (flat vertex lists), 0.0 to 1.0."""
    every = [v for v in a + b if len(v) >= 6]
    if not a or not b or not every:
        return 0.0
    xs = [x for v in every for x in v[0::2]]
    ys = [y for v in every for y in v[1::2]]
    x0, y0 = min(xs), min(ys)
    span = max(max(xs) - x0, max(ys) - y0, 1.0)
    # Small outlines are scaled up so the edge pixels do not dominate.
    k = max(1.0, 64.0 / span)
    w = int((max(xs) - x0) * k) + 2
    h = int((max(ys) - y0) * k) + 2
    ma, mb = _fill(a, x0, y0, w, h, k), _fill(b, x0, y0, w, h, k)
    union = np.logical_or(ma, mb).sum()
    return float(np.logical_and(ma, mb).sum() / union) if union else 0.0


def _by_roi(state, section):
    out = {}
    for p in ((state or {}).get(section) or {}).get("polys") or []:
        roi = p.get("roi")
        if roi is None:
            continue
        verts, region = out.setdefault(int(roi), ([], p.get("region") or ""))
        verts.append(list(p.get("v") or []))
    return out


def compare(learner, key, sections):
    """One Result per ROI in the key or drawn by the learner, on the given sections."""
    results = []
    for sec in sections:
        mine, theirs = _by_roi(learner, sec), _by_roi(key, sec)
        for roi in sorted(set(mine) | set(theirs)):
            if roi not in mine:
                results.append(Result(sec, roi, theirs[roi][1], "missing", 0.0))
            elif roi not in theirs:
                results.append(Result(sec, roi, mine[roi][1], "extra", 0.0))
            else:
                o = iou(mine[roi][0], theirs[roi][0])
                results.append(Result(sec, roi, theirs[roi][1] or mine[roi][1],
                                      "good" if o >= GOOD else "off", round(o, 3)))
    return results


def words(r):
    name = f"ROI {r.roi}" + (f" ({r.region})" if r.region else "")
    if r.kind == "missing":
        return f"{name}: not drawn; the expert outlined it."
    if r.kind == "extra":
        return f"{name}: you drew it, but it is not in the expert's outlines."
    share = ("almost all" if r.overlap >= 0.9 else "most" if r.overlap >= 0.66
             else "about half" if r.overlap >= 0.4 else "about a third" if r.overlap >= 0.25
             else "little")
    verdict = "good" if r.kind == "good" else "check the dashed outline"
    return f"{name}: {share} of it overlaps the expert's outline ({r.overlap:.0%}); {verdict}."
