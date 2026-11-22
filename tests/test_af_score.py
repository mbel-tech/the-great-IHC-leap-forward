"""The autofluorescence flag (ls_af_model, 06k_af_score) and its 06c sheet.

The model first: on two Gaussian clouds the numpy logistic regression separates
them (AUC > 0.9) and gives the same coefficients twice. Then the stage, on a
synthetic paired study run through the real 06a: it trains on the unstained
reagent controls when they have at least 200 above-cut nuclei, else on the
no-primary ones, never both; below 200 of either it writes nothing and says how
many it had; it leaves roi_measurements.csv and roi_corrected.csv byte for byte;
and 06c shows the positive rate without flagged nuclei beside the raw one,
labelled provisional.

    python tests/test_af_score.py
"""

import argparse
import contextlib
import csv
import hashlib
import io
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts"), HERE]

from _fixture import use_temp_study, load_stage  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(70) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def auc(score, y):
    """Mann-Whitney AUC of `score` for the class y == 1."""
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    n1 = int(np.sum(y == 1))
    n0 = len(y) - n1
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


# ===================================================== the model
print("--- fit_separates ---")
import ls_af_model as AF  # noqa: E402

rng = np.random.default_rng(7)
X = np.vstack([rng.normal(0.0, 1.0, (300, 4)), rng.normal(1.5, 1.0, (200, 4))])
y = np.r_[np.zeros(300), np.ones(200)]
m1 = AF.fit(X, y)
m2 = AF.fit(X, y)
p = AF.predict(m1, X)
chk("scores are probabilities in [0, 1]", bool(np.all((p >= 0) & (p <= 1))))
chk("AUC on two Gaussian clouds > 0.9", auc(p, y) > 0.9)
chk("deterministic: the same coefficients twice",
    np.array_equal(m1["beta"], m2["beta"]))
chk("...and the same scores", np.array_equal(p, AF.predict(m2, X)))
Xn = X.copy()
Xn[::7, 2] = np.nan
Xn[:, 3] = 5.0                       # a constant column: dropped, not divided by 0
mn = AF.fit(Xn, y)
pn = AF.predict(mn, Xn)
chk("a blank (NaN) feature is imputed, a constant column dropped: still finite",
    bool(np.all(np.isfinite(pn))))
chk("...and the dropped column is recorded", list(mn["keep"]), [True, True, True, False])
# Balanced weights: a 10:1 imbalance must not pull every score to the majority.
Xi = np.vstack([rng.normal(0.0, 1.0, (1000, 2)), rng.normal(3.0, 1.0, (100, 2))])
yi = np.r_[np.zeros(1000), np.ones(100)]
pi = AF.predict(AF.fit(Xi, yi), Xi)
chk("balanced class weights: most of the minority class scores >= 0.5",
    float(np.mean(pi[yi == 1] >= 0.5)) > 0.8)

print("--- features_matrix ---")
stats = {("S1", "AF568"): {"bg": (100.0, 10.0), "dapi": (1000.0, 100.0)},
         ("S1", "AF488"): {"bg": (50.0, 5.0), "dapi": (1000.0, 100.0)}}
row = {"scene_uid": "S1", "marker": "AF568", "area_um2": "40", "equiv_diam_um": "7.1",
       "roundness": "0.9", "solidity": "0.95", "dapi_mean": "1200", "marker_median": "160",
       "marker_p90": "240", "marker_cv": "0.3", "marker_spots": "2",
       "other_marker_median": "65", "other_marker": "AF488"}
Xf, names = AF.features_matrix([row], stats)
got = dict(zip(names, Xf[0].tolist()))
chk("the features are the spec's, in order", names, AF.FEATURES)
chk("DAPI is a robust z within the section", got["dapi_z"], 2.0)
chk("marker_median in spreads above the section's discs", got["marker_spreads"], 6.0)
chk("p90 over median", got["p90_over_median"], 1.5)
chk("the other marker in spreads above its own discs", got["other_spreads"], 3.0)
Xb, _ = AF.features_matrix([dict(row, marker_cv="", other_marker_median="",
                                 other_marker="")], stats)
chk("blank cells are NaN, never 0",
    [bool(np.isnan(v)) for v in (Xb[0][names.index("marker_cv")],
                                 Xb[0][names.index("other_spreads")])], [True, True])
Xz, _ = AF.features_matrix([row], {("S1", "AF568"): {"bg": (100.0, 0.0), "dapi": None}})
chk("a spread of 0 or no stats leaves the feature blank",
    [bool(np.isnan(Xz[0][names.index(n)])) for n in ("marker_spreads", "dapi_z")],
    [True, True])

# ===================================================== the stage
print("--- the stage: fixture ---")
CTL_U, CTL_N = "A1_s01_sc03", "A1_s01_sc04"
STAINED = ["A1_s01_sc01", "A1_s01_sc02"]
UNFEATURED = "A1_s01_sc05"           # stained, measured, not yet featured by 06l
STUDY = use_temp_study(
    acquisition={"layout": "paired", "markers": ["AF568", "AF488"]},
    reagent_controls={"declared": "yes", "scenes": {
        CTL_U: {"kind": "unstained", "markers": [], "source": "manual"},
        CTL_N: {"kind": "no_primary", "markers": [], "source": "manual"}}})
G6A = load_stage("06a_roi_dataset.py", name="lsstage_06a_af")
G5 = G6A.G5
K = load_stage("06k_af_score.py", name="lsstage_06k_af")
G6C = load_stage("06c_excel_dataset.py", name="lsstage_06c_af")
RES = os.path.join(STUDY.out_root, "results")
os.makedirs(RES, exist_ok=True)
mdir = os.path.join(STUDY.out_root, "manifest")
os.makedirs(mdir, exist_ok=True)
ALL = STAINED + [CTL_U, CTL_N, UNFEATURED]
with open(os.path.join(mdir, "manifest_scenes.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["scene_uid", "file", "animal", "marker_channel"])
    w.writeheader()
    for u in ALL:
        w.writerow({"scene_uid": u, "file": "A1_1.czi", "animal": "A1",
                    "marker_channel": "AF568"})

NUC_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "roi_index", "nucleus_id", "czi_x", "czi_y", "sec_x", "sec_y",
            "area_um2", "equiv_diam_um", "dapi_mean", "marker_mean",
            "marker_median", "marker_p90", "censored", "artifact", "off_tissue"]
BOX_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "sec_x", "sec_y", "sec_r", "czi_file", "scene_index", "czi_cx",
            "czi_cy", "czi_x0", "czi_y0", "czi_w", "czi_h", "axis_a_um", "axis_b_um"]
BPATH = os.path.join(tempfile.mkdtemp(prefix="af_boxes_"), "roi_boxes.csv")


def af_like(r):
    """Granular, irregular - autofluorescence-like features."""
    return {"roundness": r.normal(0.6, 0.05), "solidity": r.normal(0.8, 0.04),
            "marker_cv": r.normal(0.9, 0.1), "marker_spots": int(r.integers(4, 9))}


def stain_like(r):
    return {"roundness": r.normal(0.9, 0.03), "solidity": r.normal(0.97, 0.01),
            "marker_cv": r.normal(0.25, 0.05), "marker_spots": int(r.integers(0, 2))}


def write_study(n_unstained, n_no_primary):
    """roi_nuclei.csv, the boxes, nucleus_features.csv and its done log.

    Every section: 20 background nuclei around 100 (cut ~ 115), and ROI nuclei
    of Dm. Controls: n above the cut, all autofluorescence-like, plus 30 below.
    Stained: 200 stain-like and 40 autofluorescence-like above the cut, 60 below.
    """
    r = np.random.default_rng(20261121)
    nuc, feats, boxes = [], [], []
    plan = {CTL_U: (0, n_unstained), CTL_N: (0, n_no_primary)}
    for u in STAINED + [UNFEATURED]:
        plan[u] = (200, 40)
    for u in ALL:
        n_stain, n_af = plan[u]
        for kind, region in (("roi", "Dm"), ("background", "__background__")):
            boxes.append({"scene_uid": u, "animal": "A1", "marker": "AF568",
                          "roi_kind": kind, "region": region, "seed_n": "1", "sec_x": 128,
                          "sec_y": 128, "sec_r": 20, "czi_file": "A1_1.czi",
                          "scene_index": 0, "czi_cx": 0, "czi_cy": 0, "czi_x0": 0,
                          "czi_y0": 0, "czi_w": 100, "czi_h": 100, "axis_a_um": 1000.0,
                          "axis_b_um": 1000.0})
        nid = 0
        items = ([(2, "background", float(r.normal(100, 5)), stain_like(r))
                  for _ in range(20)]
                 + [(1, "roi", float(r.normal(300, 30)), stain_like(r)) for _ in range(n_stain)]
                 + [(1, "roi", float(r.normal(250, 30)), af_like(r)) for _ in range(n_af)]
                 + [(1, "roi", float(r.normal(50, 5)), stain_like(r)) for _ in range(30)])
        for idx, kind, v, f in items:
            nid += 1
            nuc.append({"scene_uid": u, "animal": "A1", "marker": "AF568", "roi_kind": kind,
                        "region": "Dm" if kind == "roi" else "__background__",
                        "seed_n": "1", "roi_index": idx, "nucleus_id": nid,
                        "czi_x": 10 * nid, "czi_y": 50, "sec_x": 128, "sec_y": 128,
                        "area_um2": round(float(r.normal(40, 4)), 1),
                        "equiv_diam_um": round(float(r.normal(7, 0.4)), 2),
                        "dapi_mean": round(float(r.normal(1500, 150)), 1),
                        "marker_mean": round(v, 1), "marker_median": round(v, 1),
                        "marker_p90": round(v * float(r.uniform(1.1, 1.6)), 1),
                        "censored": 0, "artifact": 0, "off_tissue": 0})
            feats.append({"scene_uid": u, "marker": "AF568", "roi_index": idx,
                          "nucleus_id": nid, "roundness": round(f["roundness"], 4),
                          "solidity": round(f["solidity"], 4),
                          "marker_cv": round(f["marker_cv"], 4),
                          "marker_spots": f["marker_spots"], "other_marker_median": ""})
    G5L.use_marker("AF568")
    for path, cols, rows in ((os.path.join(RES, "roi_nuclei.csv"), NUC_COLS, nuc),
                             (BPATH, BOX_COLS, boxes),
                             # 06l reads the marker's own box file for its fingerprint.
                             (G5L.BOX_CSV, BOX_COLS, boxes),
                             (os.path.join(RES, "nucleus_features.csv"),
                              ["scene_uid", "marker", "roi_index", "nucleus_id", "roundness",
                               "solidity", "marker_cv", "marker_spots",
                               "other_marker_median"],
                              [f for f in feats if f["scene_uid"] != UNFEATURED])):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    # The done log as 06l writes it: each featured section with 06l's own
    # fingerprint of what its rows were computed from.
    write_done(u for u in ALL if u != UNFEATURED)


def fingerprints_06l():
    """{(marker, uid): fingerprint} by 06l's own plan(), as a run would log them."""
    with contextlib.redirect_stdout(io.StringIO()):
        work, _gone = K.stage_06l().plan(argparse.Namespace(marker=None))
    return {(m, u): fp for m, _b, _g, secs in work for u, fp in secs}


def write_done(uids):
    fps = fingerprints_06l()
    with open(os.path.join(RES, "nucleus_features_done.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["marker", "scene_uid", "fingerprint"])
        w.writeheader()
        w.writerows({"marker": "AF568", "scene_uid": u,
                     "fingerprint": fps[("AF568", u)]} for u in uids)


G5.all_boxes = lambda: G5.load_csv(BPATH)
G6C.G5.all_boxes = lambda: G6C.G5.load_csv(BPATH)


def run(mod, argv=None):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = mod.main(argv) if argv is not None else mod.main()
    return code, out.getvalue()


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


SCORES = os.path.join(RES, "af_scores.csv")
SECTIONS = os.path.join(RES, "af_scored_sections.csv")
PNG = os.path.join(STUDY.out_root, "qc", "af_review.png")
KEY = os.path.join(STUDY.out_root, "qc", "af_review.csv")


def prepare(n_u, n_n):
    write_study(n_u, n_n)
    code, out = run(G6A)
    assert code == 0, out


def scenario(n_u, n_n):
    prepare(n_u, n_n)
    return run(K, [])


# The review crops, on a stand-in for pylibCZIrw (as tests/test_nucleus_features.py):
# a 64 px gradient per read, and a geometry row per section so 06k finds the scene.
READS = []


class _Doc:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom=1.0):
        READS.append((tuple(roi), int(plane["C"]), int(scene)))
        x, y, w, h = roi
        return (np.add.outer(np.arange(h), np.arange(w)) * 10 + 100).astype(np.uint16)[None]


class _PyCzi:
    @staticmethod
    def open_czi(path, **_kw):
        return _Doc()


sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
G5L = K.stage_06l().G5
G5L.use_marker("AF568")
os.makedirs(os.path.dirname(G5L.GEOM_CSV), exist_ok=True)
with open(G5L.GEOM_CSV, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["scene_uid", "animal", "marker", "czi_file",
                                       "scene_index", "scene_w"])
    w.writeheader()
    for i, u in enumerate(ALL, 1):
        w.writerow({"scene_uid": u, "animal": "A1", "marker": "AF568",
                    "czi_file": "A1_1.czi", "scene_index": i, "scene_w": 400})


print("--- unstained_preferred ---")
prepare(250, 300)
meas = os.path.join(RES, "roi_measurements.csv")
corr = os.path.join(RES, "roi_corrected.csv")
UNTOUCHED = [os.path.join(RES, n) for n in ("roi_nuclei.csv", "roi_measurements.csv",
                                             "roi_corrected.csv", "positivity_cuts.csv",
                                             "reagent_control_cuts.csv")]
before = [sha(p) for p in UNTOUCHED]
code, out = run(K, [])
print("     " + out.strip().replace("\n", "\n     "))
rows = read(SCORES)
chk("06k runs", code, 0)
chk("af_scores.csv is written", rows is not None)
chk("its columns are the stated ones", list(rows[0]) if rows else None,
    ["scene_uid", "marker", "roi_index", "nucleus_id", "af_score", "flag_name",
     "flagged", "trained_on"])
chk("250 unstained: trained on unstained", sorted({r["trained_on"] for r in rows}),
    ["unstained"])
chk("...and the flag is af_suspect", sorted({r["flag_name"] for r in rows}), ["af_suspect"])
chk("only stained sections are scored, never a control",
    sorted({r["scene_uid"] for r in rows}), STAINED)
chk("every above-cut stained nucleus of a featured section is scored", len(rows), 2 * 240)
chk("the section not yet featured is skipped and reported",
    "1 section(s)" in out and "not yet featured" in out)
chk("flagged is score >= 0.5",
    all(r["flagged"] == ("1" if float(r["af_score"]) >= 0.5 else "0") for r in rows))
nuc_by = {(r["scene_uid"], r["roi_index"], r["nucleus_id"]): r
          for r in read(os.path.join(RES, "nucleus_features.csv"))}
spots = [(float(r["af_score"]), int(nuc_by[(r["scene_uid"], r["roi_index"],
                                             r["nucleus_id"])]["marker_spots"]))
         for r in rows]
hi = np.mean([s for s, n in spots if n >= 4])
lo = np.mean([s for s, n in spots if n < 4])
chk("autofluorescence-like stained nuclei score higher than stain-like ones", hi > lo)
secs = read(SECTIONS)
chk("the scored sections are listed (06c reads them)",
    sorted((r["scene_uid"], r["marker"]) for r in secs or [] if r["role"] == "stained"),
    [(u, "AF568") for u in STAINED])
chk("the review strip is drawn", os.path.exists(PNG))
from PIL import Image  # noqa: E402
with Image.open(PNG) as im:
    chk("...24 tiles of 64 px across", im.size[0], 24 * K.TILE)
chk("...from the slide: one 64 px read per tile, none grey",
    (len(READS), {r[0][2:] for r in READS}, "review crops not read" in out,
     "no geometry" in out), (48, {(64, 64)}, False, False))
with Image.open(PNG) as im:
    px = np.asarray(im.convert("RGB"))
chk("...a tile holds the stretched crop, not the grey placeholder",
    tuple(px[18 + 32, 32]) != (60, 60, 60) and px[18:18 + 64, :64].std() > 10)
key = read(KEY)
chk("...with a key: 24 top and 24 random tiles",
    sorted({s: sum(1 for k in key if k["strip"] == s)
            for s in ("top", "random")}.items()),
    [("random", 24), ("top", 24)])
chk("...and a blank label column for the operator",
    {k["operator_label"] for k in key}, {""})
first = sha(SCORES)
run(K, [])
chk("deterministic: a second run writes the same af_scores.csv", sha(SCORES), first)

print("--- counts_unchanged ---")
chk("roi_corrected.csv exists in this fixture (a paired no-primary control)",
    os.path.exists(corr))
chk("roi_nuclei, roi_measurements, roi_corrected and both cut files byte-identical "
    "after 06k (hashed before its first run)", [sha(p) for p in UNTOUCHED], before)

print("--- sheet_has_without_flagged ---")
import openpyxl  # noqa: E402
xlsx = os.path.join(RES, "roi_dataset.xlsx")
code6c, out6c = run(G6C, ["--out", xlsx])
chk("06c runs", code6c, 0)
wb = openpyxl.load_workbook(xlsx)
chk("an af_flag sheet", G6C.AF_SHEET in wb.sheetnames)
chk("the existing sheets are untouched and first", wb.sheetnames[:4],
    ["by_roi", "by_disc", "by_section", "coverage"])
vals = [[c.value for c in row] for row in wb[G6C.AF_SHEET].iter_rows()]
text = " ".join(str(v[0]) for v in vals if v and isinstance(v[0], str))
chk("its header says provisional", "provisional" in text)
head = [v for v in vals if v and v[0] == "sample"][0]
i = head.index("frac_positive")
chk("frac_positive_without_flagged right beside the raw frac_positive",
    head[i + 1:i + 3], ["frac_positive_without_flagged", "n_flagged"])
body = [dict(zip(head, v)) for v in vals[vals.index(head) + 1:] if v and v[0]]
dm = [b for b in body if b["ROI"] == "Dm"][0]
chk("raw rate is by_roi's",
    dm["frac_positive"],
    [dict(zip([c.value for c in wb["by_roi"][1]], [c.value for c in r]))
     for r in wb["by_roi"].iter_rows(min_row=2)][0]["frac_positive"])
chk("the section not yet scored blanks the rate, with a note",
    (dm["frac_positive_without_flagged"] in (None, ""), "not scored" in (dm["note"] or "")),
    (True, True))
# With every stained section scored, the rate is (positives - flagged) / (n - flagged).


def sub_sheet(extra_meas=(), extra_gate=(), extra_nuclei=()):
    """06c over a copy of results/ without the unfeatured section (plus
    `extra_meas` rows, and `extra_gate` rows in af_gate.csv): (header lines, Dm row)."""
    d = tempfile.mkdtemp(prefix="af_meas_")
    sub = os.path.join(d, "roi_measurements.csv")
    keep_rows = [r for r in read(meas) if r["scene_uid"] != UNFEATURED] + list(extra_meas)
    with open(sub, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(read(meas)[0]))
        w.writeheader()
        w.writerows(keep_rows)
    for name in ("af_scores.csv", "af_scored_sections.csv", "af_gate.csv",
                 "positivity_cuts.csv", "reagent_control_cuts.csv"):
        with open(os.path.join(RES, name), "rb") as a, \
                open(os.path.join(d, name), "wb") as b:
            b.write(a.read())
    if extra_gate:
        with open(os.path.join(d, "af_gate.csv"), "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=K.GATE_COLUMNS)
            w.writerows(extra_gate)
    nuc = G6C.NUCLEI_CSV
    if extra_nuclei:
        nuc = os.path.join(d, "roi_nuclei.csv")
        with open(G6C.NUCLEI_CSV, "rb") as a, open(nuc, "wb") as b:
            b.write(a.read())
        with open(nuc, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=NUC_COLS).writerows(extra_nuclei)
    saved = (G6C.MEAS_CSV, G6C.NUCLEI_CSV)
    G6C.MEAS_CSV, G6C.NUCLEI_CSV = sub, nuc
    try:
        code, out = run(G6C, ["--out", xlsx])
        assert code == 0, out
    finally:
        G6C.MEAS_CSV, G6C.NUCLEI_CSV = saved
    v = [[c.value for c in row] for row in openpyxl.load_workbook(xlsx)[G6C.AF_SHEET].iter_rows()]
    lines = " ".join(str(x[0]) for x in v[:v.index(head)] if x and isinstance(x[0], str))
    b = [dict(zip(head, x)) for x in v[v.index(head) + 1:] if x and x[0]]
    return lines, [r for r in b if r["ROI"] == "Dm"][0]


lines, dm = sub_sheet()
n_flag = sum(1 for r in rows if r["flagged"] == "1")
chk("n_flagged is the flagged nuclei of the region", dm["n_flagged"], n_flag)
chk("frac_positive_without_flagged = (n_positive - flagged) / (n_nuclei - flagged)",
    dm["frac_positive_without_flagged"],
    round((dm["n_positive"] - n_flag) / (dm["n_nuclei"] - n_flag), 4))
chk("...and below the raw rate", dm["frac_positive_without_flagged"] < dm["frac_positive"])


print("--- 06c and 06k name the same files ---")
chk("06c reads the files 06k writes",
    (G6C.AF_SCORES, G6C.AF_SECTIONS, G6C.AF_GATE),
    tuple(os.path.basename(p) for p in (K.SCORES_CSV, K.SECTIONS_CSV, K.GATE_CSV)))
chk("...and 06a's cut files by the names 06a writes them",
    (G6C.AF_CUTS, G6C.AF_RC_CUTS),
    tuple(os.path.basename(p) for p in (G6A.CUTS_CSV, G6A.RC_CUTS_CSV)))

print("--- a section measured empty counts as scored, with 0 flagged ---")
empty = dict(read(meas)[0], scene_uid="A1_s01_sc06", n_nuclei="0", n_positive="0")
empty = {k: (v if k not in ("n_nuclei", "n_positive") else "0") for k, v in empty.items()}
empty["roi_kind"], empty["region"], empty["roi_index"] = "roi", "Dm", "1"
# Its background disc has nuclei: empty means no nucleus in its ROIs.
empty_bg = dict(empty, roi_kind="background", region="__background__", roi_index="2",
                n_nuclei="7", n_positive="0")
bg_nuc = [dict(read(os.path.join(RES, "roi_nuclei.csv"))[0], scene_uid="A1_s01_sc06",
               roi_kind="background", region="__background__", roi_index="2",
               nucleus_id=str(i)) for i in range(1, 8)]
_l, dm_e = sub_sheet(extra_meas=[empty, empty_bg], extra_nuclei=bg_nuc)
chk("...the rate is still given, unchanged",
    (dm_e["frac_positive_without_flagged"], dm_e["note"] or ""),
    (dm["frac_positive_without_flagged"], ""))

print("--- a marker below the gate is named on the sheet ---")
gate = read(K.GATE_CSV)
chk("06k records each marker's counts and what it trained on",
    [(r["marker"], r["n_unstained"], r["n_no_primary"], r["trained_on"]) for r in gate],
    [("AF568", "250", "300", "unstained")])
chk("...with a blank reason when scored", [r["reason"] for r in gate], [""])
lines_g, _dm = sub_sheet(extra_gate=[
    {"marker": "AF488", "n_unstained": 12, "n_no_primary": 34, "trained_on": "",
     "reason": "below_gate"},
    {"marker": "AF647", "n_unstained": 250, "n_no_primary": 0, "trained_on": "",
     "reason": "no_stained"}])
chk("...and the sheet header says the unscored one was not scored, and why",
    "AF488 not scored: 12 unstained / 34 no_primary < 200" in lines_g)
chk("...a marker past the gate with nothing to score is not said to be below it",
    ("AF647 not scored: no stained nucleus above the cut with features" in lines_g,
     "AF647 not scored: 250" in lines_g), (True, False))

print("--- flags older than the cut are not used ---")
secs = read(SECTIONS)
cuts_now = {(r["scene_uid"], r["marker"]): r["cut"]
            for r in read(os.path.join(RES, "positivity_cuts.csv"))}
chk("af_scored_sections records the cut each stained section was scored against",
    all(r["cut"] == cuts_now[(r["scene_uid"], r["marker"])]
        for r in secs if r["role"] == "stained"))
rc_now = {(r["scene_uid"], r["marker"]): r["cut"]
          for r in read(os.path.join(RES, "reagent_control_cuts.csv"))}
chk("...and the cut of each control it trained on",
    sorted((r["scene_uid"], r["cut"] == rc_now[(r["scene_uid"], r["marker"])])
           for r in secs if r["role"] == "control"), [(CTL_U, True)])
G6A.CONFIG.setdefault("positivity", {})
_saved_k = G6A.CONFIG["positivity"].get("cut_k")
G6A.CONFIG["positivity"]["cut_k"] = {"*": 4.0}
try:
    assert run(G6A)[0] == 0
finally:
    if _saved_k is None:
        G6A.CONFIG["positivity"].pop("cut_k", None)
    else:
        G6A.CONFIG["positivity"]["cut_k"] = _saved_k
_l, dm_s = sub_sheet()
chk("cut_k changed and 06a rerun, 06k not: the row is blank, never a number",
    dm_s["frac_positive_without_flagged"] in (None, ""))
chk("...with the note", "flags older than the cut - run 06k" in (dm_s["note"] or ""))
assert run(G6A)[0] == 0

print("--- 06l features out of date are not joined ---")
# 06l has run: the done log carries its fingerprints. Then 05c re-detects
# STAINED[0] and its nuclei come back renumbered - the same ids, so every id
# still has a features row, but not its own nucleus's - and 06a runs; 06l does
# not. Joined by id, the wrong nuclei's features would be scored, silently.
assert run(K, [])[0] == 0
npath = os.path.join(RES, "roi_nuclei.csv")
nrows = read(npath)
ids = [r["nucleus_id"] for r in nrows if r["scene_uid"] == STAINED[0] and r["roi_kind"] == "roi"]
it = iter(ids[::-1])
for r in nrows:
    if r["scene_uid"] == STAINED[0] and r["roi_kind"] == "roi":
        r["nucleus_id"] = next(it)
with open(npath, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(nrows[0]))
    w.writeheader()
    w.writerows(nrows)
assert run(G6A)[0] == 0
code, out = run(K, [])
chk("06k still runs on the other sections", code, 0)
chk("the renumbered section is not scored",
    STAINED[0] in {r["scene_uid"] for r in read(SCORES) or []}, False)
chk("...nor listed as a scored section",
    [r["scene_uid"] for r in read(SECTIONS) or []
     if r["role"] == "stained" and r["scene_uid"] == STAINED[0]], [])
chk("...the other stained section still is",
    STAINED[1] in {r["scene_uid"] for r in read(SCORES) or []}, True)
chk("...and the stage says its 06l features are out of date",
    "out of date" in out and "06l_nucleus_features.py" in out and STAINED[0] in out)
_l, dm_o = sub_sheet()
chk("06c: the row is blank, never a number",
    dm_o["frac_positive_without_flagged"] in (None, ""))
chk("...with the note that 06l's features are out of date",
    "06l features out of date - run 06l" in (dm_o["note"] or ""), True)

print("--- unstained below the gate: no_primary ---")
code, out = scenario(150, 300)
rows = read(SCORES)
chk("150 unstained: trained on no_primary",
    sorted({r["trained_on"] for r in rows or []}), ["no_primary"])
chk("...and the flag is control_like",
    sorted({r["flag_name"] for r in rows or []}), ["control_like"])

print("--- gate_blocks ---")
code, out = scenario(150, 150)
print("     " + out.strip().replace("\n", "\n     "))
chk("150 of either kind: no af_scores.csv (the earlier one is removed)",
    os.path.exists(SCORES), False)
chk("...no scored-sections list and no review strip",
    (os.path.exists(SECTIONS), os.path.exists(PNG), os.path.exists(KEY)),
    (False, False, False))
chk("...and the message names the count and the gate",
    "150" in out and "200" in out)
chk("...and the stage says it did nothing (non-zero)", code != 0)
code6c, _ = run(G6C, ["--out", xlsx])
chk("06c without af_scores.csv: no af_flag sheet",
    G6C.AF_SHEET in openpyxl.load_workbook(xlsx).sheetnames, False)

print("--- a missing input removes the earlier outputs too ---")
scenario(250, 300)
chk("(outputs there first)", os.path.exists(SCORES))
os.remove(os.path.join(RES, "nucleus_features_done.csv"))
code, out = run(K, [])
chk("no done log: exit 1 and every earlier output removed",
    (code, [os.path.exists(p) for p in (SCORES, SECTIONS, K.GATE_CSV, PNG, KEY)]),
    (1, [False] * 5))

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
