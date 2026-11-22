"""The autofluorescence model: an L2 logistic regression in numpy, for 06k.

Positive-unlabelled: the labelled class is above-cut nuclei of one kind of
reagent control (unstained, else no-primary - never both), the unlabelled class
above-cut nuclei of stained sections. A stained nucleus's score is the model's
probability that it belongs to the control class: "looks like a reagent-control
false positive". scikit-learn is not a dependency of this repo, so the fit is
written here: standardised features, balanced class weights, an L2 penalty on
every coefficient but the intercept, solved by iteratively reweighted least
squares (Newton) from zero. Started at zero with no random step, the fit is
deterministic; the seed is recorded with the model and is what 06k draws its
random review tiles with.

The features (features_matrix) are all section-relative, so a batch difference
of the background between animals cannot become a feature:

  area_um2, equiv_diam_um      size, from roi_nuclei.csv
  roundness, solidity          shape, from 06l's nucleus_features.csv
  dapi_z                       DAPI mean as a robust z within the section
                               ((x - median) / (1.4826 x MAD) of the section's
                               kept nuclei of that marker)
  marker_spreads               marker_median in spreads above the section's own
                               background discs ((x - median) / (1.4826 x MAD)
                               of the disc nuclei the cut is formed from)
  p90_over_median              marker_p90 / marker_median
  marker_cv, marker_spots      texture, from 06l
  other_spreads                under multiplex, the other marker's median in
                               spreads above ITS discs on the same section;
                               blank under paired

A blank cell is NaN, never 0. fit() imputes it with the training mean (0 once
standardised) and drops a column that is blank or constant throughout (the
other marker under paired); predict() does the same with the stored values.
"""

import warnings

import numpy as np

FEATURES = ["area_um2", "equiv_diam_um", "roundness", "solidity", "dapi_z",
            "marker_spreads", "p90_over_median", "marker_cv", "marker_spots",
            "other_spreads"]

#: The seed recorded with every model (and 06k's review draw).
SEED = 20261121
#: IRLS stops when no coefficient moves more than this, or after MAX_ITER steps.
TOL = 1e-8
MAX_ITER = 100
#: Probabilities are kept this far from 0 and 1, so a separable class cannot
#: make a weight of exactly 0.
EPS = 1e-9


def _num(v):
    if v is None or v == "":
        return np.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _rel(x, stats):
    """(x - centre) / spread, or NaN when there is no x, no stats or no spread."""
    if stats is None or not np.isfinite(x):
        return np.nan
    centre, spread = stats
    if not spread or spread <= 0:
        return np.nan
    return (x - centre) / spread


def features_matrix(rows, section_stats):
    """(X, FEATURES): one row per nucleus, one column per feature, NaN for blank.

    `rows`: dicts carrying scene_uid, marker, area_um2, equiv_diam_um,
    dapi_mean, marker_median, marker_p90 (roi_nuclei.csv), roundness, solidity,
    marker_cv, marker_spots, other_marker_median (nucleus_features.csv) and
    other_marker (the marker other_marker_median was read for; blank when none).

    `section_stats`: {(scene_uid, marker): {"bg": (median, spread) or None,
    "dapi": (median, spread) or None}} - the section's background-disc marker
    medians and its nuclei's DAPI means, spread = 1.4826 x MAD.
    """
    X = np.full((len(rows), len(FEATURES)), np.nan)
    for i, r in enumerate(rows):
        st = section_stats.get((r["scene_uid"], r["marker"])) or {}
        med, p90 = _num(r.get("marker_median")), _num(r.get("marker_p90"))
        other = r.get("other_marker") or ""
        ost = (section_stats.get((r["scene_uid"], other)) or {}) if other else {}
        X[i] = [
            _num(r.get("area_um2")),
            _num(r.get("equiv_diam_um")),
            _num(r.get("roundness")),
            _num(r.get("solidity")),
            _rel(_num(r.get("dapi_mean")), st.get("dapi")),
            _rel(med, st.get("bg")),
            p90 / med if np.isfinite(med) and med > 0 and np.isfinite(p90) else np.nan,
            _num(r.get("marker_cv")),
            _num(r.get("marker_spots")),
            _rel(_num(r.get("other_marker_median")), ost.get("bg")) if other else np.nan,
        ]
    return X, list(FEATURES)


def _design(model, X):
    """Standardised, imputed columns kept by the model, with an intercept."""
    X = np.asarray(X, dtype=float)[:, model["keep"]]
    Z = (X - model["mean"]) / model["sd"]
    Z[~np.isfinite(Z)] = 0.0
    return np.hstack([np.ones((Z.shape[0], 1)), Z])


def _sigmoid(t):
    return 1.0 / (1.0 + np.exp(-np.clip(t, -500, 500)))


def fit(X, y, l2=1.0, seed=SEED):
    """An L2 logistic regression of y (1 = control class) on X, by IRLS.

    Balanced class weights (each class sums to n / 2), standardised features,
    no penalty on the intercept. Returns {"keep", "mean", "sd", "beta",
    "l2", "seed", "n_iter", "n": (n0, n1)}; beta[0] is the intercept.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n1 = int(np.sum(y == 1))
    n0 = len(y) - n1
    if n0 == 0 or n1 == 0:
        raise ValueError(f"both classes are needed: {n0} unlabelled, {n1} labelled")
    # A column blank throughout (the other marker under paired) has no mean:
    # numpy warns, the column is dropped below.
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(X, axis=0)
        sd = np.nanstd(X, axis=0)
    keep = np.isfinite(mean) & np.isfinite(sd) & (sd > 0)
    model = {"keep": keep, "mean": mean[keep], "sd": sd[keep], "l2": float(l2),
             "seed": int(seed), "n": (n0, n1)}
    A = _design(model, X)
    w = np.where(y == 1, len(y) / (2.0 * n1), len(y) / (2.0 * n0))
    pen = np.full(A.shape[1], float(l2))
    pen[0] = 0.0
    beta = np.zeros(A.shape[1])
    it = 0
    for it in range(1, MAX_ITER + 1):
        p = np.clip(_sigmoid(A @ beta), EPS, 1 - EPS)
        grad = A.T @ (w * (y - p)) - pen * beta
        hess = (A * (w * p * (1 - p))[:, None]).T @ A + np.diag(pen)
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hess, grad, rcond=None)[0]
        beta = beta + step
        if np.max(np.abs(step)) < TOL:
            break
    model["beta"] = beta
    model["n_iter"] = it
    return model


def predict(model, X):
    """The control-class probability of each row of X, in [0, 1]."""
    return _sigmoid(_design(model, X) @ model["beta"])
