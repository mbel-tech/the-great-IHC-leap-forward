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
    nuc, feats, boxes, done = [], [], [], []
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
        if u != UNFEATURED:
            done.append({"marker": "AF568", "scene_uid": u, "fingerprint": "x"})
    for path, cols, rows in ((os.path.join(RES, "roi_nuclei.csv"), NUC_COLS, nuc),
                             (BPATH, BOX_COLS, boxes),
                             (os.path.join(RES, "nucleus_features.csv"),
                              ["scene_uid", "marker", "roi_index", "nucleus_id", "roundness",
                               "solidity", "marker_cv", "marker_spots",
                               "other_marker_median"],
                              [f for f in feats if f["scene_uid"] != UNFEATURED]),
                             (os.path.join(RES, "nucleus_features_done.csv"),
                              ["marker", "scene_uid", "fingerprint"], done)):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)


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


def scenario(n_u, n_n):
    write_study(n_u, n_n)
    code, out = run(G6A)
    assert code == 0, out
    return run(K, [])


print("--- unstained_preferred ---")
code, out = scenario(250, 300)
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
    sorted((r["scene_uid"], r["marker"]) for r in secs or []),
    [(u, "AF568") for u in STAINED])
chk("the review strip is drawn", os.path.exists(PNG))
from PIL import Image  # noqa: E402
with Image.open(PNG) as im:
    chk("...24 tiles of 64 px across", im.size[0], 24 * K.TILE)
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
meas = os.path.join(RES, "roi_measurements.csv")
corr = os.path.join(RES, "roi_corrected.csv")
chk("roi_corrected.csv exists in this fixture (a paired no-primary control)",
    os.path.exists(corr))
before = (sha(meas), sha(corr))
run(K, [])
chk("roi_measurements.csv and roi_corrected.csv byte-identical after 06k",
    (sha(meas), sha(corr)), before)

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
saved_meas = G6C.MEAS_CSV
sub = os.path.join(tempfile.mkdtemp(prefix="af_meas_"), "roi_measurements.csv")
keep_rows = [r for r in read(meas) if r["scene_uid"] != UNFEATURED]
with open(sub, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(keep_rows[0]))
    w.writeheader()
    w.writerows(keep_rows)
for name in ("af_scores.csv", "af_scored_sections.csv"):
    with open(os.path.join(RES, name), "rb") as a, \
            open(os.path.join(os.path.dirname(sub), name), "wb") as b:
        b.write(a.read())
G6C.MEAS_CSV = sub
try:
    code6c, out6c = run(G6C, ["--out", xlsx])
finally:
    G6C.MEAS_CSV = saved_meas
wb = openpyxl.load_workbook(xlsx)
vals = [[c.value for c in row] for row in wb[G6C.AF_SHEET].iter_rows()]
body = [dict(zip(head, v)) for v in vals[vals.index(head) + 1:] if v and v[0]]
dm = [b for b in body if b["ROI"] == "Dm"][0]
n_flag = sum(1 for r in rows if r["flagged"] == "1")
chk("n_flagged is the flagged nuclei of the region", dm["n_flagged"], n_flag)
chk("frac_positive_without_flagged = (n_positive - flagged) / (n_nuclei - flagged)",
    dm["frac_positive_without_flagged"],
    round((dm["n_positive"] - n_flag) / (dm["n_nuclei"] - n_flag), 4))
chk("...and below the raw rate", dm["frac_positive_without_flagged"] < dm["frac_positive"])

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

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
