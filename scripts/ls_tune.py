"""How good a channel's signal is, and which detection settings it calls for.

No config and no CZI here: `05b_tune_detection.py` reads the crops and writes
the result, and this module only measures and decides, so every rule in it can
be tested on synthetic frames.

WHAT IS MEASURED, per crop, at the native pixel size (a crop is a window cut
out of an ROI box, never a resampled one - resolution is never traded for
speed here):

    background   median of the pixels below the crop's Otsu cut
    foreground   median of the pixels above it
    noise        robust SD of the pixel-to-pixel differences, / sqrt 2

(The Otsu cut is found on a 3x3 mean of the crop, so that it separates
nuclei from background even when the noise is close to the signal; the
statistics are then read off the raw pixels either side of it.)
    snr          (foreground - background) / noise
    contrast     (foreground - background) / (foreground + background)
    coverage     fraction of the crop above the Otsu cut
    saturated    fraction of the crop at the file's clip ceiling

The noise is taken from neighbouring-pixel differences rather than from the
background's own spread because it does not depend on how much of the crop is
background. A dense DAPI field can be nine-tenths nuclei; the darkest quarter
of it is then nucleus edge, and its MAD is the edge gradient, not the camera.
A difference of two neighbours cancels anything smooth - the background level,
a vignette, the inside of a nucleus - and leaves the noise, sqrt 2 times over.
Edges are in the differences too, but they are a small fraction of the pixels
and the MAD ignores a small fraction by construction.

WHAT IS DECIDED, per marker, once for the whole study:

    prob_thresh  StarDist's object-probability cut, chosen where the object
                 count is least sensitive to it (see `pick_prob_thresh`)
    verdict      good / marginal / poor, with the reasons in words

ONE SETTING PER MARKER, NEVER PER SECTION. A cut tuned section by section
would follow each section's own brightness, and brightness follows treatment
when the treatment changes expression: the tuning would then be a
treatment-correlated bias in every count. The sample is spread evenly over
animals and never reads the group key.

nms_thresh and the normalisation percentiles are NOT tuned. The model was
trained with a 1..99.8 percentile stretch and its own overlap threshold; moving
those changes what the network sees rather than where its answer is cut, and
nothing measurable here says by how much. They can still be set by hand.
"""

import math

import numpy as np

#: The probability cuts tried. Below 0.3 StarDist returns noise as objects on
#: any real frame; above 0.8 it drops nuclei it is plainly sure of. The model's
#: own default (0.479 for 2D_versatile_fluo) is added to the grid.
PROB_GRID = tuple(round(0.30 + 0.05 * i, 2) for i in range(11))

#: How much flatter than the default's a cut must be for the default to be
#: abandoned. Without this margin a curve that is flat everywhere would be
#: "tuned" to whichever point won by a rounding error.
KEEP_DEFAULT_MARGIN = 0.8

#: How far either side of a cut its count's slope is measured over.
SLOPE_SPAN = 0.05

#: Signal-to-noise below which detection on this plane is called poor, and
#: below which it is called marginal. On synthetic 7 um nuclei at 0.65 um/px
#: StarDist finds essentially all of them at SNR 5, starts missing dim ones
#: near 3 and returns mostly noise below 2.
SNR_POOR = 3.0
SNR_MARGINAL = 5.0

#: A fraction of saturated pixels above which the brightest nuclei are
#: measured as lower bounds (05c flags each one as `censored`).
SATURATED_WARN = 0.005

#: Coefficient of variation of the per-section signal (foreground minus
#: background) above which staining or imaging is uneven across the study.
UNEVEN_CV = 0.5


def otsu(a, bins=256):
    """Otsu's cut on a 1-D sample; the midpoint for a flat one."""
    a = np.asarray(a, dtype=np.float64).ravel()
    lo, hi = float(a.min()), float(a.max())
    if not hi > lo:
        return lo
    hist, edges = np.histogram(a, bins=bins, range=(lo, hi))
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    s0 = np.cumsum(hist * centres)
    m0 = s0 / np.maximum(w0, 1)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(edges[int(np.argmax(between[:-1])) + 1])


def noise_sigma(image):
    """Robust SD of the noise, from horizontal neighbour differences."""
    a = np.asarray(image, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] < 2:
        return 0.0
    d = np.diff(a, axis=1).ravel()
    med = np.median(d)
    return float(1.4826 * np.median(np.abs(d - med)) / math.sqrt(2))


def box_mean(image, r=1):
    """Mean over a (2r+1)-square neighbourhood, edges repeated. Same shape."""
    a = np.pad(np.asarray(image, dtype=np.float64), r, mode="edge")
    c = np.cumsum(np.cumsum(a, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def quality(image, ceiling=None):
    """The signal statistics of one crop. Always the same keys."""
    a = np.asarray(image, dtype=np.float64)
    out = {"background": 0.0, "foreground": 0.0, "noise": 0.0, "snr": 0.0,
           "contrast": 0.0, "coverage": 0.0, "saturated": 0.0, "px": int(a.size)}
    if a.size == 0:
        return out
    ok = np.isfinite(a)
    if not ok.any():
        return out
    flat = a[ok]
    # WHICH pixel is signal is decided on a 3x3 mean, and nothing else is: at
    # a signal-to-noise of a few the raw histogram is one hump, and Otsu then
    # splits the noise of the background in half. Averaging nine pixels cuts
    # the noise threefold and leaves a 7 um nucleus (about 11 px across here)
    # standing. Every number below is still read off the raw pixels.
    if a.ndim == 2 and min(a.shape) >= 3 and ok.all():
        sm = box_mean(a)
        is_fg = sm > otsu(sm)
        below, above = a[~is_fg], a[is_fg]
    else:
        cut = otsu(flat)
        below, above = flat[flat <= cut], flat[flat > cut]
    bg = float(np.median(below)) if below.size else float(np.median(flat))
    fg = float(np.median(above)) if above.size else bg
    sd = noise_sigma(a)
    out.update(background=bg, foreground=fg, noise=sd,
               coverage=float(above.size) / flat.size)
    out["snr"] = (fg - bg) / sd if sd > 0 else (math.inf if fg > bg else 0.0)
    out["contrast"] = (fg - bg) / (fg + bg) if (fg + bg) > 0 else 0.0
    if ceiling is not None:
        out["saturated"] = float(np.count_nonzero(flat >= ceiling)) / flat.size
    return out


def _median(vals):
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    return float(np.median(vals)) if vals else None


def summarise(rows):
    """Study-level numbers from per-crop `quality` rows that carry a scene_uid.

    Each SECTION gets one vote - the median of its crops - before the study
    median is taken, so a section with eight ROIs does not count eight times.
    """
    by_sec = {}
    for r in rows:
        by_sec.setdefault(r["scene_uid"], []).append(r)
    keys = ("background", "foreground", "noise", "snr", "contrast",
            "coverage", "saturated")
    per_sec = [{k: _median([r[k] for r in rs]) for k in keys}
               for rs in by_sec.values()]
    out = {k: _median([s[k] for s in per_sec]) for k in keys}
    signal = [s["foreground"] - s["background"] for s in per_sec
              if s["foreground"] is not None and s["background"] is not None]
    if len(signal) >= 2 and np.mean(signal) > 0:
        out["signal_cv_between_sections"] = float(np.std(signal) / np.mean(signal))
    else:
        out["signal_cv_between_sections"] = None
    snrs = [s["snr"] for s in per_sec if s["snr"] is not None]
    out["snr_worst_section"] = float(min(snrs)) if snrs else None
    out["sections"] = len(by_sec)
    out["crops"] = len(rows)
    return out


def verdict(summary):
    """(word, [reasons]) for a `summarise` result. Words a person can act on."""
    reasons, word = [], "good"
    snr = summary.get("snr")
    if snr is None:
        return "unknown", ["no pixels were measured"]
    if snr < SNR_POOR:
        word = "poor"
        reasons.append(f"signal-to-noise is {snr:.1f}, below {SNR_POOR:g}: "
                       f"dim nuclei will be missed whatever the settings")
    elif snr < SNR_MARGINAL:
        word = "marginal"
        reasons.append(f"signal-to-noise is {snr:.1f}, below {SNR_MARGINAL:g}: "
                       f"the dimmest nuclei are at risk")
    worst = summary.get("snr_worst_section")
    if worst is not None and worst < SNR_POOR <= snr:
        word = "marginal" if word == "good" else word
        reasons.append(f"the weakest sampled section has signal-to-noise "
                       f"{worst:.1f}; check it in the QC pictures")
    sat = summary.get("saturated") or 0.0
    if sat > SATURATED_WARN:
        word = "marginal" if word == "good" else word
        reasons.append(f"{sat:.1%} of pixels are saturated: the brightest "
                       f"nuclei are measured as lower bounds")
    cv = summary.get("signal_cv_between_sections")
    if cv is not None and cv > UNEVEN_CV:
        word = "marginal" if word == "good" else word
        reasons.append(f"signal varies a lot between sections (CV {cv:.2f}); "
                       f"one study-wide setting is still used, so that no "
                       f"section is tuned to its own brightness")
    if not reasons:
        reasons.append(f"signal-to-noise {snr:.1f}, little saturation, even "
                       f"across sections")
    return word, reasons


def pick_prob_thresh(curve, default):
    """The probability cut where the object count is least sensitive to it.

    `curve` is [(prob_thresh, n_objects)] summed over the sample. Too low a cut
    and StarDist adds noise as objects, so the count climbs steeply; too high
    and it drops real nuclei, so it falls steeply. Between the two the count
    barely moves - the plateau - and a cut there means the count reflects the
    nuclei rather than where the cut happened to be put. Sensitivity is the
    change in log count per unit of cut, from each point's neighbours.

    The model's default is kept unless some cut is clearly flatter
    (KEEP_DEFAULT_MARGIN): moving it needs a reason the data shows.

    Returns (chosen, why). `chosen` is None when the curve says nothing - too
    few points, or no objects anywhere - and the default then stands.
    """
    pts = sorted((float(p), int(n)) for p, n in curve)
    if len(pts) < 3 or all(n == 0 for _, n in pts):
        return None, "too few objects in the sample to judge"
    # Each point's slope is taken across a fixed span either side of it, not
    # from its immediate neighbours: the model's 0.479 sits 0.021 from 0.50,
    # and a count difference over a step that small is mostly the count's own
    # graininess.
    slope = {}
    for p, n in pts:
        lower = [(q, m) for q, m in pts if q <= p - SLOPE_SPAN + 1e-9]
        upper = [(q, m) for q, m in pts if q >= p + SLOPE_SPAN - 1e-9]
        if not lower or not upper or n <= 0:
            continue
        (p0, n0), (p1, n1) = lower[-1], upper[0]
        if n0 <= 0 or n1 <= 0:
            continue
        slope[p] = abs(math.log(n0) - math.log(n1)) / (p1 - p0)
    if not slope:
        return None, "too few objects in the sample to judge"
    best = min(slope, key=lambda p: (slope[p], abs(p - default)))
    near = min(slope, key=lambda p: abs(p - default))
    if abs(near - default) < 1e-9 and slope[best] >= KEEP_DEFAULT_MARGIN * slope[near]:
        return default, (f"the count is about as stable at the model's own "
                         f"{default:.3f} as anywhere else")
    return best, (f"the count changes least around {best:.2f} "
                  f"({slope[best]:.2f} vs {slope[near]:.2f} log-count per unit "
                  f"at the model's {default:.3f})")


def count_curve(model, images, thresholds, nms_thresh=None, n_tiles_for=None):
    """[(prob_thresh, total objects)] over `images`, already normalised.

    The network is run ONCE per image and only the cheap non-maximum
    suppression is repeated per cut, through StarDist's own
    `_instances_from_prediction` - the same code `predict_instances` calls.
    A model without it (an older StarDist, or a test stand-in) is asked for
    instances at each cut instead, which gives the same counts more slowly.
    """
    totals = {float(p): 0 for p in thresholds}
    for x in images:
        nt = n_tiles_for(x) if n_tiles_for else None
        fast = hasattr(model, "_instances_from_prediction") and hasattr(model, "predict")
        if fast:
            prob, dist = model.predict(x, n_tiles=nt, show_tile_progress=False)
        for p in totals:
            kw = {"prob_thresh": p}
            if nms_thresh is not None:
                kw["nms_thresh"] = nms_thresh
            if fast:
                labels, _ = model._instances_from_prediction(x.shape, prob, dist, **kw)
            else:
                labels, _ = model.predict_instances(
                    x, n_tiles=nt, verbose=False, show_tile_progress=False, **kw)
            totals[p] += int(np.asarray(labels).max())
    return sorted(totals.items())
