"""The reagent-control signal screen: features and scoring. numpy only.

A reagent control (no primary, unstained) that nobody named is found here by how
its marker channel LOOKS, never by how bright it is. LOGS.md (2026-08-12)
records a ~2x background split between animal batches, so every feature below
is invariant to an additive offset and a positive gain on any channel: a
batch cannot move it. Absolute level and tissue/background contrast are not
used anywhere.

- F1, nuclear spot enrichment: the share of the marker's bright spots that
  sit in DAPI nuclei, over the nuclear area share. A stained nuclear marker is
  well above 1; autofluorescent puncta (lipofuscin, red cells) sit anywhere.
- F2, between-marker similarity: Spearman correlation of two marker channels.
  Autofluorescence is broad-band, so a no-primary multiplex section's channels
  look alike.
- F3, tail spread: log(p99.5 - p50) - log(p50 - p5). A specific stain has a
  heavy right tail; autofluorescence plus diffuse secondary binding a light one.

`score` turns them into robust z per marker across the study and proposes
candidates. It proposes only: a section becomes a reagent control when the
operator confirms it in Set up. The thresholds are provisional until the
validation run on a study with known controls.
"""

from collections import Counter

import numpy as np

#: Tiles per section, their side in native pixels.
TILES = 12
TILE_PX = 512
#: A spot is a local maximum above median + SPOT_K x 1.4826 x MAD of its tile.
SPOT_K = 5.0
#: Every available feature must reach this robust z for a candidate.
Z_CANDIDATE = 3.0

#: Gaussian sigma in native pixels: DAPI before Otsu (a nucleus is ~15 px
#: across at 0.65 um/px), and the marker before its maxima are found.
DAPI_SIGMA = 2.0
MARKER_SIGMA = 1.0

STATUSES = ("candidate", "review", "unusual", "")

_MAD_SD = 1.4826


# ---------------------------------------------------------------- image helpers

def _gauss(a, sigma):
    """Separable Gaussian, kernel summing to 1, edges reflected: linear and
    offset-preserving, so smooth(g*x + c) == g*smooth(x) + c everywhere."""
    a = np.asarray(a, dtype=np.float64)
    r = max(1, int(round(3 * sigma)))
    t = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-0.5 * (t / sigma) ** 2)
    k /= k.sum()
    h, w = a.shape
    p = np.pad(a, r, mode="reflect")
    rows = np.zeros((h, w + 2 * r))
    for i, wt in enumerate(k):
        rows += wt * p[i:i + h, :]
    cols = np.zeros((h, w))
    for i, wt in enumerate(k):
        cols += wt * rows[:, i:i + w]
    return cols


def _otsu(values, bins=256):
    """The above-threshold side of a plain (linear) Otsu split over a min-to-max
    histogram, as a flat bool array, or None for a flat input.

    Not 01_overviews.tissue_threshold: that one works in log space, which an
    additive offset changes. Here the bins span the data's own range, so a
    gain and an offset move the edges with the data and the split stays put.
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    lo, hi = float(v.min()), float(v.max())
    if not hi > lo:
        return None
    idx = np.minimum(((v - lo) / (hi - lo) * bins).astype(np.int64), bins - 1)
    hist = np.bincount(idx, minlength=bins).astype(np.float64)
    centres = np.arange(bins) + 0.5
    w1 = np.cumsum(hist)
    w2 = w1[-1] - w1
    m1 = np.cumsum(hist * centres) / np.maximum(w1, 1e-12)
    total = (hist * centres).sum()
    m2 = (total - np.cumsum(hist * centres)) / np.maximum(w2, 1e-12)
    var = w1 * w2 * (m1 - m2) ** 2
    var[(w1 <= 0) | (w2 <= 0)] = -1
    cut = int(np.argmax(var))
    return idx > cut


def _median_mad(v):
    med = float(np.median(v))
    return med, float(np.median(np.abs(v - med)))


def _local_maxima(a):
    """Pixels >= each of their 8 neighbours (edges padded by copying)."""
    p = np.pad(a, 1, mode="edge")
    h, w = a.shape
    peak = np.ones(a.shape, bool)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            peak &= a >= p[dy:dy + h, dx:dx + w]
    return peak


def _ranks(v):
    """Average ranks (ties share their mean rank), as for a Spearman."""
    order = np.argsort(v, kind="mergesort")
    sv = v[order]
    first = np.r_[True, sv[1:] != sv[:-1]]
    group = np.cumsum(first) - 1
    starts = np.flatnonzero(first)
    ends = np.r_[starts[1:], sv.size]
    avg = (starts + ends - 1) / 2.0
    out = np.empty(v.size)
    out[order] = avg[group]
    return out


# ---------------------------------------------------------------- features

def nuclear_mask(dapi):
    """Otsu on Gaussian-smoothed DAPI, per tile; None when the tile is flat."""
    return _otsu(_gauss(dapi, DAPI_SIGMA))


def f1_nuclear_enrichment(marker, dapi):
    """(spots in nuclei / spots) / (nuclear area / tile area), or None.

    None when there are no spots, when the marker tile is flat (MAD 0), or when
    the nuclear mask is empty or covers the whole tile: an enrichment over
    nothing is not a number worth ranking.
    """
    nuc = nuclear_mask(dapi)
    if nuc is None:
        return None
    nuc = nuc.reshape(np.shape(dapi))
    frac = float(nuc.mean())
    if frac <= 0.0 or frac >= 1.0:
        return None
    m = _gauss(marker, MARKER_SIGMA)
    med, mad = _median_mad(m)
    if mad <= 0:
        return None
    spots = _local_maxima(m) & (m > med + SPOT_K * _MAD_SD * mad)
    n = int(spots.sum())
    if n == 0:
        return None
    return (int((spots & nuc).sum()) / n) / frac


def f2_channel_similarity(a, b, mask):
    """Spearman correlation of two marker channels over the masked pixels, or
    None with fewer than 3 pixels or a constant channel."""
    mask = np.asarray(mask, bool)
    x = np.asarray(a, dtype=np.float64)[mask]
    y = np.asarray(b, dtype=np.float64)[mask]
    if x.size < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    rx -= rx.mean()
    ry -= ry.mean()
    den = float(np.sqrt((rx * rx).sum() * (ry * ry).sum()))
    if den <= 0:
        return None
    return float((rx * ry).sum() / den)


def f3_tail_spread(x, mask):
    """log(p99.5 - p50) - log(p50 - p5) over the masked pixels, or None when
    either spread is zero (a flat or empty tile)."""
    v = np.asarray(x, dtype=np.float64)[np.asarray(mask, bool)]
    if v.size < 3:
        return None
    p5, p50, p995 = np.percentile(v, [5, 50, 99.5])
    if not (p995 > p50 > p5):
        return None
    return float(np.log(p995 - p50) - np.log(p50 - p5))


# ---------------------------------------------------------------- scoring

def robust_z(values):
    """{key: (x - median) / (1.4826 x MAD)}; every z is 0 when the MAD is 0."""
    if not values:
        return {}
    keys = list(values)
    v = np.array([float(values[k]) for k in keys])
    med, mad = _median_mad(v)
    if mad <= 0:
        return {k: 0.0 for k in keys}
    return {k: float((x - med) / (_MAD_SD * mad)) for k, x in zip(keys, v)}


def _num(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _yes(v):
    """A guard or flag, given as a bool or as CSV text. None reads as ok:
    a guard that could not be checked is said so in the note, not failed."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "")
    return bool(v)


def _flag(v):
    return v is not None and v is not False and _yes(v)


#: (feature, its z column, the sign that makes "control-like" positive)
_FEATURES = (("f1", "z1", -1.0), ("f2", "z2", 1.0), ("f3", "z3", -1.0))


def score(rows):
    """Copies of `rows` with z1-z3, n_features, candidate, rank and status.

    Per marker, each feature becomes a robust z across the marker's sections,
    signed so "control-like" is positive (-F1, +F2, -F3). Rows with
    `confirmed=True` are already reagent controls: they are left out of the
    population and not scored. Rows with `rejected=True` (the operator said not
    a control) stay in the population and are never proposed again.

    status: `candidate` when every available z >= Z_CANDIDATE, at least two
    features are available and both guards hold; `review` for the same with
    only one feature; `unusual` when it looks like a control but a guard
    fails; `` otherwise. rank is the minimum available z: the weakest
    evidence decides.
    """
    out = [dict(r) for r in rows]
    for r in out:
        r["confirmed"] = _flag(r.get("confirmed"))
        r["rejected"] = _flag(r.get("rejected"))
    for marker in dict.fromkeys(r.get("marker") for r in out):
        mine = [i for i, r in enumerate(out) if r.get("marker") == marker]
        for feat, zcol, sign in _FEATURES:
            pop = {i: _num(out[i].get(feat)) for i in mine if not out[i]["confirmed"]}
            z = robust_z({i: v for i, v in pop.items() if v is not None})
            for i in mine:
                out[i][zcol] = sign * z[i] if i in z else None
    for r in out:
        zs = [r[z] for _f, z, _s in _FEATURES if r[z] is not None]
        r["n_features"] = len(zs) if not r["confirmed"] else sum(
            _num(r.get(f)) is not None for f, _z, _s in _FEATURES)
        r["rank"] = min(zs) if zs else None
        looks = bool(zs) and all(z >= Z_CANDIDATE for z in zs)
        guards = _yes(r.get("guard_dapi_ok")) and _yes(r.get("guard_exposure_ok"))
        status = ""
        if looks and not r["confirmed"] and not r["rejected"]:
            if not guards:
                status = "unusual"
            elif len(zs) >= 2:
                status = "candidate"
            else:
                status = "review"
        r["status"] = status
        r["candidate"] = status == "candidate"
    return out


def file_rollup(rows):
    """{file: fraction of its sections that are candidates}. A section counts
    once, as a candidate when any of its marker rows is."""
    by_file = {}
    for i, r in enumerate(rows):
        secs = by_file.setdefault(r.get("file"), {})
        uid = r.get("scene_uid", i)
        secs[uid] = secs.get(uid, False) or _flag(r.get("candidate"))
    return {f: sum(s.values()) / len(s) for f, s in by_file.items() if s}


# ---------------------------------------------------------------- guards and gate

def exposure_ok(exposures):
    """{file: True when its exposure equals the modal one, False when not,
    None when it has none recorded}. Pass one marker's files at a time."""
    vals = {f: _num(v) for f, v in exposures.items()}
    known = [v for v in vals.values() if v is not None]
    if not known:
        return {f: None for f in vals}
    mode = Counter(known).most_common(1)[0][0]
    return {f: (None if v is None else v == mode) for f, v in vals.items()}


def tissue_ok(fractions):
    """{uid: True when its DAPI tissue fraction is within the study's normal
    range (|robust z| <= Z_CANDIDATE), False when outside or unknown}.

    An area fraction, not an intensity: 01's tissue mask is a per-section
    threshold, so a batch does not move it. This catches failed or empty
    sections, which would otherwise look like controls.
    """
    known = {k: _num(v) for k, v in fractions.items()}
    vals = {k: v for k, v in known.items() if v is not None}
    if not vals:
        return {k: False for k in fractions}
    med, mad = _median_mad(np.array(list(vals.values())))
    if mad <= 0:
        # Most sections alike to the last digit: only those are in range. robust_z
        # would call every section 0 here, the empty one included.
        return {k: k in vals and vals[k] == med for k in fractions}
    z = robust_z(vals)
    return {k: (k in z and abs(z[k]) <= Z_CANDIDATE) for k in fractions}


def should_run(config, force):
    """Whether 01m screens: when the study says `unsure`, or `yes` with nothing
    confirmed yet, or when forced. `no`, and `yes` with entries, need no
    proposals."""
    if force:
        return True
    rc = (config or {}).get("reagent_controls") or {}
    declared = rc.get("declared") or "no"
    if declared == "unsure":
        return True
    if declared == "yes":
        return not (rc.get("files") or rc.get("scenes"))
    return False
