"""The corrected positive rate, from paired no-primary reagent controls.

results/roi_corrected.csv, one row per stained (section, marker, region): the
raw pooled frac_positive beside a Rogan-Gladen correction for the paired
no_primary controls' false-positive rate f, (p - f) / (1 - f), assuming
sensitivity 1, with a Monte-Carlo interval from Jeffreys posteriors. Only the
operator's pairing, the same file or the same animal; never study-wide. A
marker segmented on its own channel gets a density correction instead.
roi_measurements.csv is never changed by any of it.

Synthetic nuclei throughout.

Run:  python tests/test_reagent_correction.py
"""

import contextlib
import csv
import hashlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
sys.path[:0] = [SCRIPTS, HERE]

from _fixture import use_temp_study, load_stage  # noqa: E402

CHANNELS = [
    {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
    {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1},
    {"name": "PCNA", "role": "marker", "czi_name": "AF488", "index": 2},
]
RC_BLOCK = {
    "declared": "yes",
    "scenes": {
        # On A1's first file, a control for pERK only: its PCNA is stained.
        "A1_s01_sc02": {"kind": "no_primary", "markers": ["pERK"], "source": "manual"},
        # On A1's second file, a control for PCNA only: its pERK is stained.
        "A1_s02_sc01": {"kind": "no_primary", "markers": ["PCNA"], "source": "manual"},
    },
}
STUDY = use_temp_study(
    acquisition={"layout": "multiplex", "channels": CHANNELS},
    reagent_controls=RC_BLOCK)

import ls_reagent_report as RR  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def approx(a, b, tol=1e-9):
    return a != "" and b != "" and abs(float(a) - float(b)) <= tol


# ===================================================== rogan_gladen
print("--- rg_point ---")
rg = RR.rogan_gladen(30, 100, 5, 100)
chk("(0.30 - 0.05) / 0.95", approx(rg["corrected"], (0.30 - 0.05) / 0.95), True)
chk("its keys", sorted(rg), ["corrected", "hi", "lo", "note"])

print("--- rg_interval_contains_point ---")
chk("lo <= corrected <= hi", rg["lo"] <= rg["corrected"] <= rg["hi"], True)
chk("...and the interval is a real one", rg["lo"] < rg["hi"], True)
chk("...inside [0, 1]", 0.0 <= rg["lo"] and rg["hi"] <= 1.0, True)
chk("deterministic with the seed", RR.rogan_gladen(30, 100, 5, 100), rg)
chk("another seed gives another interval",
    RR.rogan_gladen(30, 100, 5, 100, seed=1)["lo"] != rg["lo"], True)
allpos = RR.rogan_gladen(20, 20, 1, 100)
chk("every stained nucleus positive: the interval still holds the point (1.0)",
    allpos["lo"] <= allpos["corrected"] <= allpos["hi"], True)

print("--- rg_below_fp_blank ---")
low = RR.rogan_gladen(3, 100, 5, 100)
chk("p <= f: blank, never 0", (low["corrected"], low["lo"], low["hi"]), ("", "", ""))
chk("...with the note", low["note"], "at or below the reagent control's false-positive rate")
chk("p == f: blank too", RR.rogan_gladen(5, 100, 5, 100)["corrected"], "")
chk("no stained nucleus: blank", RR.rogan_gladen(0, 0, 5, 100)["corrected"], "")
chk("no control nucleus: blank", RR.rogan_gladen(5, 100, 0, 0)["corrected"], "")

print("--- density_correction ---")
dc = RR.density_correction(50, 1.0, 10, 2.0)
chk("d - d_f = 50/1 - 10/2", approx(dc["corrected"], 45.0, 1e-6), True)
chk("lo <= corrected <= hi", dc["lo"] <= dc["corrected"] <= dc["hi"], True)
chk("deterministic", RR.density_correction(50, 1.0, 10, 2.0), dc)
chk("d <= d_f: blank, with a note",
    (RR.density_correction(4, 1.0, 10, 2.0)["corrected"],
     bool(RR.density_correction(4, 1.0, 10, 2.0)["note"])), ("", True))

# ===================================================== controls_for
print("--- inverse_pairing_tiers ---")
SCENES = [
    # (uid, file, animal, marker)
    ("A1_s01_sc01", "A1_1.czi", "A1", "pERK"),
    ("A1_s01_sc02", "A1_1.czi", "A1", "pERK"),     # control, same file
    ("A1_s02_sc01", "A1_2.czi", "A1", "pERK"),     # control, same animal, own file
    ("A1_s03_sc01", "A1_3.czi", "A1", "pERK"),     # stained, own file
    ("B2_s01_sc01", "B2_1.czi", "B2", "pERK"),     # stained, no control anywhere
    ("C3_s01_sc01", "C3_1.czi", "C3", "pERK"),     # stained
    ("Z9_s01_sc01", "Z9_1.czi", "Z9", "pERK"),     # control the operator pairs with C3
]
ROWS = [{"scene_uid": u, "file": f, "animal": a, "marker_channel": m}
        for u, f, a, m in SCENES]
LOOKUP = {("A1_s01_sc02", "pERK"): {"kind": "no_primary"},
          ("A1_s02_sc01", "pERK"): {"kind": "no_primary"},
          ("Z9_s01_sc01", "pERK"): {"kind": "no_primary"}}
CFG = {"reagent_controls": {"declared": "yes", "paired_with": {}}}
chk("a same-file control wins over a same-animal one",
    RR.controls_for("A1_s01_sc01", "pERK", LOOKUP, ROWS, CFG), ("same_file", ["A1_s01_sc02"]))
chk("...a section on its own file gets both of its animal's controls",
    RR.controls_for("A1_s03_sc01", "pERK", LOOKUP, ROWS, CFG),
    ("same_animal", ["A1_s01_sc02", "A1_s02_sc01"]))
chk("with no same-animal control: none, never study-wide",
    RR.controls_for("B2_s01_sc01", "pERK", LOOKUP, ROWS, CFG), ("none", []))
CFG_PW = {"reagent_controls": {"declared": "yes",
                               "paired_with": {"Z9_s01_sc01": ["A1_s01_sc01", "C3_s01_sc01"]}}}
chk("paired_with wins over both",
    RR.controls_for("A1_s01_sc01", "pERK", LOOKUP, ROWS, CFG_PW), ("operator", ["Z9_s01_sc01"]))
chk("...and pairs across animals",
    RR.controls_for("C3_s01_sc01", "pERK", LOOKUP, ROWS, CFG_PW), ("operator", ["Z9_s01_sc01"]))
chk("...by file name too",
    RR.controls_for("C3_s01_sc01", "pERK", LOOKUP, ROWS,
                    {"reagent_controls": {"declared": "yes",
                                          "paired_with": {"Z9_1.czi": ["C3_1.czi"]}}}),
    ("operator", ["Z9_s01_sc01"]))
iso = dict(LOOKUP)
iso[("A1_s01_sc02", "pERK")] = {"kind": "isotype"}
chk("an isotype control is never used: the same-animal no_primary one is",
    RR.controls_for("A1_s01_sc01", "pERK", iso, ROWS, CFG), ("same_animal", ["A1_s02_sc01"]))
# The operator's pairing is exclusive: Z9 is paired with C3 only, so a stained
# section on Z9's own file (or animal) does not get it.
ROWS_X = ROWS + [{"scene_uid": "Z9_s01_sc02", "file": "Z9_1.czi", "animal": "Z9",
                  "marker_channel": "pERK"}]
chk("a control the operator paired elsewhere is not taken by the file rule",
    RR.controls_for("Z9_s01_sc02", "pERK", LOOKUP, ROWS_X, CFG_PW), ("none", []))
chk("...and is by it without the override",
    RR.controls_for("Z9_s01_sc02", "pERK", LOOKUP, ROWS_X, CFG), ("same_file", ["Z9_s01_sc01"]))
CFG_PW_A1 = {"reagent_controls": {"declared": "yes",
                                  "paired_with": {"A1_s01_sc02": ["C3_s01_sc01"]}}}
chk("...a pinned same-file control gives way to the same-animal one",
    RR.controls_for("A1_s01_sc01", "pERK", LOOKUP, ROWS, CFG_PW_A1),
    ("same_animal", ["A1_s02_sc01"]))
print("--- pins that do not take effect (round 2) ---")
PROWS = [
    {"scene_uid": "F1_s01_sc01", "file": "F1.czi", "animal": "A", "marker_channel": "PCNA"},
    {"scene_uid": "F1_s01_sc02", "file": "F1.czi", "animal": "A", "marker_channel": "PCNA"},
    {"scene_uid": "F2_s01_sc01", "file": "F2.czi", "animal": "B", "marker_channel": "pERK"},
    {"scene_uid": "F3_s01_sc01", "file": "F3.czi", "animal": "C", "marker_channel": "PCNA"},
]
PLK = {("F1_s01_sc01", "PCNA"): {"kind": "no_primary"}}


def pin(*targets, key="F1_s01_sc01"):
    return {"reagent_controls": {"declared": "yes", "paired_with": {key: list(targets)}}}


for label, cfg in (("a pin to another marker's section", pin("F2_s01_sc01")),
                   ("a stale pin (not in the manifest)", pin("GONE_s01_sc01")),
                   ("a stale pin by file", pin("GONE.czi", key="F1.czi"))):
    rep = RR.pairing_for("F1_s01_sc01", "PCNA", PLK, PROWS, cfg)
    got = RR.controls_for("F1_s01_sc02", "PCNA", PLK, PROWS, cfg)
    chk(f"{label}: the control is still used on its file",
        got, ("same_file", ["F1_s01_sc01"]))
    chk("...and the report pairs it the same way",
        rep, ("same_file", ["F1_s01_sc02"]))
live = pin("F3_s01_sc01")
chk("a pin that takes effect: operator, in the report",
    RR.pairing_for("F1_s01_sc01", "PCNA", PLK, PROWS, live), ("operator", ["F3_s01_sc01"]))
chk("...for the section it names",
    RR.controls_for("F3_s01_sc01", "PCNA", PLK, PROWS, live), ("operator", ["F1_s01_sc01"]))
chk("...and exclusive: not taken on its own file",
    RR.controls_for("F1_s01_sc02", "PCNA", PLK, PROWS, live), ("none", []))
chk("a pin written as one string is a one-item list, not its letters",
    RR.controls_for("F3_s01_sc01", "PCNA", PLK, PROWS,
                    {"reagent_controls": {"declared": "yes",
                                          "paired_with": {"F1_s01_sc01": "F3_s01_sc01"}}}),
    ("operator", ["F1_s01_sc01"]))
chk("a control is never paired with itself",
    RR.controls_for("A1_s01_sc02", "pERK", LOOKUP, ROWS, CFG)[1].count("A1_s01_sc02"), 0)


# ===================================================== corrected_rows
def nuc(uid, value, marker="pERK", region="Dm", kind="roi", cen=False, own=False):
    return {"scene_uid": uid, "marker": marker,
            "region": "__background__" if kind == "background" else region,
            "roi_kind": kind, "marker_median": float(value), "censored": cen, "own": own}


def one(rows, uid, marker="pERK", region="Dm"):
    got = [r for r in rows if (r["scene_uid"], r["marker"], r["region"]) == (uid, marker, region)]
    return got[0] if got else None


print("--- corrected_rows ---")
# The stained section: 100 nuclei in Dm, 30 positive.
STAINED = {("S1", "pERK", "Dm"): {"n": 100, "pos": 30, "area": 1.0, "own": False, "ab": 1.0}}
# Two controls on its file: C1 with a cut of 100 (60 ROI nuclei in Dm, 3 above);
# C2 with no cut of its own.
CTL = ([nuc("C1", 200) for _ in range(3)] + [nuc("C1", 50) for _ in range(57)]
       + [nuc("C2", 500) for _ in range(40)])
rows = RR.corrected_rows(STAINED, CTL, {("C1", "pERK"): 100.0},
                         {("S1", "pERK"): ("same_file", ["C1", "C2"])}, fp_min_nuclei=50)
chk("header is ROI_CORRECTED_COLUMNS", list(rows[0]), RR.ROI_CORRECTED_COLUMNS)
chk("the exact column list", RR.ROI_CORRECTED_COLUMNS,
    ["scene_uid", "marker", "region", "n_nuclei", "n_positive", "frac_positive",
     "frac_positive_corrected", "frac_positive_corrected_lo", "frac_positive_corrected_hi",
     "positive_density_per_mm2", "positive_density_corrected",
     "positive_density_corrected_lo", "positive_density_corrected_hi",
     "fp_rate", "fp_rate_n", "fp_rate_source", "correction_note"])
r = one(rows, "S1")
chk("raw columns carried", (r["n_nuclei"], r["n_positive"], r["frac_positive"]), (100, 30, 0.3))
chk("a nuclear marker: the density columns blank, never 0",
    (r["positive_density_per_mm2"], r["positive_density_corrected"]), ("", ""))
chk("f = 3 / 60 from C1 only", (r["fp_rate"], r["fp_rate_n"]), (0.05, 60))
chk("corrected = (0.3 - 0.05) / 0.95",
    approx(r["frac_positive_corrected"], round((0.3 - 0.05) / 0.95, 4)), True)
chk("the interval holds it",
    r["frac_positive_corrected_lo"] <= r["frac_positive_corrected"]
    <= r["frac_positive_corrected_hi"], True)

print("--- control_without_cut_not_used ---")
chk("the source names the tier and the control used", r["fp_rate_source"].startswith(
    "same_file: C1"), True)
chk("...and says C2 was left out for having no cut",
    "C2" in r["fp_rate_source"] and "no cut" in r["fp_rate_source"], True)
nocut = RR.corrected_rows(STAINED, CTL, {}, {("S1", "pERK"): ("same_file", ["C1", "C2"])},
                          fp_min_nuclei=50)[0]
chk("no paired control has a cut: blank, never 0",
    (nocut["fp_rate"], nocut["frac_positive_corrected"]), ("", ""))
chk("...and the source says so", "no cut" in nocut["fp_rate_source"], True)

print("--- region_fallback ---")
FEW = ([nuc("C1", 200, region="Dm") for _ in range(1)] + [nuc("C1", 50, region="Dm")
                                                          for _ in range(19)]
       + [nuc("C1", 200, region="Vv") for _ in range(2)]
       + [nuc("C1", 50, region="Vv") for _ in range(78)])
fb = one(RR.corrected_rows(STAINED, FEW, {("C1", "pERK"): 100.0},
                           {("S1", "pERK"): ("same_file", ["C1"])}, fp_min_nuclei=50), "S1")
chk("20 control nuclei in Dm (< 50): f over all regions, 3 / 100",
    (fb["fp_rate"], fb["fp_rate_n"]), (0.03, 100))
chk("...and the source says all regions", "all regions" in fb["fp_rate_source"], True)
tiny = one(RR.corrected_rows(STAINED, FEW[:20], {("C1", "pERK"): 100.0},
                             {("S1", "pERK"): ("same_file", ["C1"])}, fp_min_nuclei=50), "S1")
chk("under the minimum even over all regions: blank, never 0",
    (tiny["fp_rate"], tiny["frac_positive_corrected"]), ("", ""))
chk("...with the count in the note", "20" in tiny["correction_note"], True)

print("--- none and no cut ---")
none = one(RR.corrected_rows(STAINED, CTL, {("C1", "pERK"): 100.0},
                             {("S1", "pERK"): ("none", [])}, fp_min_nuclei=50), "S1")
chk("no pairable control: the spec's source",
    none["fp_rate_source"], "none: no same-slide or same-animal control")
chk("...and blank", (none["fp_rate"], none["frac_positive_corrected"]), ("", ""))
chk("...the raw rate still beside it", none["frac_positive"], 0.3)
nc = {("S1", "pERK", "Dm"): dict(STAINED[("S1", "pERK", "Dm")], pos="")}
ncr = one(RR.corrected_rows(nc, CTL, {("C1", "pERK"): 100.0},
                            {("S1", "pERK"): ("same_file", ["C1"])}, fp_min_nuclei=50), "S1")
chk("a stained section with no cut: blank, note says so",
    (ncr["frac_positive"], ncr["frac_positive_corrected"], "cut" in ncr["correction_note"]),
    ("", "", True))
lowp = {("S1", "pERK", "Dm"): dict(STAINED[("S1", "pERK", "Dm")], pos=2)}
lr = one(RR.corrected_rows(lowp, CTL, {("C1", "pERK"): 100.0},
                           {("S1", "pERK"): ("same_file", ["C1"])}, fp_min_nuclei=50), "S1")
chk("p_obs <= f: blank, with the note",
    (lr["frac_positive_corrected"],
     "at or below the reagent control's false-positive rate" in lr["correction_note"]),
    ("", True))

print("--- own_marker_density ---")
OWN = {("S1", "PCNA", "Dm"): {"n": 80, "pos": 40, "area": 0.5, "own": True, "pos_ab": 20.0}}
OCTL = ([nuc("C1", 200, marker="PCNA", own=True) for _ in range(5)]
        + [nuc("C1", 50, marker="PCNA", own=True) for _ in range(55)])
orow = one(RR.corrected_rows(OWN, OCTL, {("C1", "PCNA"): 100.0},
                             {("S1", "PCNA"): ("same_animal", ["C1"])}, fp_min_nuclei=50,
                             control_areas={("C1", "PCNA", "Dm"): 1.0}), "S1", "PCNA")
chk("segment: own -> no fraction, no corrected fraction",
    (orow["frac_positive"], orow["frac_positive_corrected"], orow["fp_rate"]), ("", "", ""))
chk("...the count of positives still there", orow["n_positive"], 40)
# stained 40 x 0.5 / 0.5 = 40 per mm2; control 5 x 0.5 / 1.0 = 2.5 per mm2.
chk("...positive_density_per_mm2 = sum(n_pos x factor) / area = 40",
    orow["positive_density_per_mm2"], 40.0)
chk("...the density correction in its columns: 40 - 2.5 = 37.5 per mm2",
    orow["positive_density_corrected"], 37.5)
chk("...with its interval",
    orow["positive_density_corrected_lo"] <= 37.5 <= orow["positive_density_corrected_hi"],
    True)
chk("...source names the region", orow["fp_rate_source"], "same_animal: C1 (Dm)")
# Two ROIs with different factors: 30 x 0.5 + 10 x 1.0 = 25 over 0.5 mm2.
mix = one(RR.corrected_rows({("S1", "PCNA", "Dm"): dict(OWN[("S1", "PCNA", "Dm")],
                                                       pos_ab=25.0)},
                            OCTL, {("C1", "PCNA"): 100.0},
                            {("S1", "PCNA"): ("same_animal", ["C1"])}, fp_min_nuclei=50,
                            control_areas={("C1", "PCNA", "Dm"): 1.0}), "S1", "PCNA")
chk("pooled over ROIs: the sum of each row's n_pos x factor, not the last factor",
    mix["positive_density_per_mm2"], 50.0)
FEWO = [nuc("C1", 200, marker="PCNA", own=True)] + [nuc("C1", 50, marker="PCNA", own=True)
                                                    for _ in range(2)]
fo = one(RR.corrected_rows(OWN, FEWO, {("C1", "PCNA"): 100.0},
                           {("S1", "PCNA"): ("same_animal", ["C1"])}, fp_min_nuclei=50,
                           control_areas={("C1", "PCNA", "Dm"): 1.0}), "S1", "PCNA")
chk("own marker: 3 control objects (< fp_min_nuclei) still corrected - no count gate",
    (fo["positive_density_corrected"] != "", fo["fp_rate_n"], "all regions"
     in fo["fp_rate_source"]), (True, 3, False))
VV = [dict(x, region="Vv") for x in OCTL]
fv = one(RR.corrected_rows(OWN, VV, {("C1", "PCNA"): 100.0},
                           {("S1", "PCNA"): ("same_animal", ["C1"])}, fp_min_nuclei=50,
                           control_areas={("C1", "PCNA", "Vv"): 1.0}), "S1", "PCNA")
chk("own marker, no control ROI area in the region: all regions, said so",
    ("all regions" in fv["fp_rate_source"], fv["positive_density_corrected"]), (True, 37.5))
fz = one(RR.corrected_rows(OWN, OCTL, {("C1", "PCNA"): 100.0},
                           {("S1", "PCNA"): ("same_animal", ["C1"])}, fp_min_nuclei=50,
                           control_areas={}), "S1", "PCNA")
chk("own marker, no control area at all: blank, never 0, with a note",
    (fz["positive_density_corrected"], "area" in fz["correction_note"]), ("", True))
fn = one(RR.corrected_rows(OWN, OCTL, {}, {("S1", "PCNA"): ("none", [])}), "S1", "PCNA")
chk("own marker, no control: the raw density still there, corrected blank",
    (fn["positive_density_per_mm2"], fn["positive_density_corrected"]), (40.0, ""))
chk("...against the same density as positive_density_per_mm2",
    RR.density_correction(40, 0.5, 5, 1.0, factor=0.5)["corrected"], 37.5)

# ===================================================== 06a, end to end
print("--- 06a writes results/roi_corrected.csv (multiplex) ---")
G6A = load_stage("06a_roi_dataset.py", name="lsstage_06a_corr")
G5 = G6A.G5
G6A.FP_MIN_NUCLEI = 5

MANIFEST = [
    ("A1_s01_sc01", "A1_1.czi", "A1"),     # stained, both markers
    ("A1_s01_sc02", "A1_1.czi", "A1"),     # pERK control; its PCNA is stained
    ("A1_s02_sc01", "A1_2.czi", "A1"),     # PCNA control; its pERK is stained
    ("B2_s01_sc01", "B2_1.czi", "B2"),     # stained, no control on file or animal
]
mdir = os.path.join(STUDY.out_root, "manifest")
os.makedirs(mdir, exist_ok=True)
with open(os.path.join(mdir, "manifest_scenes.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["scene_uid", "file", "animal", "marker_channel"])
    w.writeheader()
    for uid, f, a in MANIFEST:
        w.writerow({"scene_uid": uid, "file": f, "animal": a, "marker_channel": "pERK"})

NUC_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "roi_index", "nucleus_id", "czi_x", "czi_y", "sec_x", "sec_y",
            "area_um2", "equiv_diam_um", "dapi_mean", "marker_mean",
            "marker_median", "marker_p90", "censored", "artifact"]
BOX_COLS = ["scene_uid", "animal", "marker", "roi_kind", "region", "seed_n",
            "sec_x", "sec_y", "sec_r", "czi_file", "scene_index", "czi_cx",
            "czi_cy", "czi_x0", "czi_y0", "czi_w", "czi_h",
            "axis_a_um", "axis_b_um"]
BG = [100] * 10                       # cut 100 on every section
DATA = {
    # (uid, marker): ROI medians in Dm
    ("A1_s01_sc01", "pERK"): [200] * 6 + [50] * 14,     # p = 0.30
    ("A1_s01_sc02", "pERK"): [200] * 1 + [50] * 9,      # control, f = 0.10
    ("A1_s02_sc01", "pERK"): [200] * 8 + [50] * 12,     # stained, same animal
    ("B2_s01_sc01", "pERK"): [200] * 5 + [50] * 15,
    ("A1_s01_sc01", "PCNA"): [200] * 10 + [50] * 10,    # p = 0.50
    ("A1_s01_sc02", "PCNA"): [200] * 4 + [50] * 16,     # p = 0.20, stained
    ("A1_s02_sc01", "PCNA"): [200] * 2 + [50] * 18,     # control, f = 0.10
}


def write_fixture(tmp, data):
    nuc_rows, boxes, nid = [], [], 0
    for (uid, mk), roi in data.items():
        an = uid.split("_")[0]
        for kind, region in (("roi", "Dm"), ("background", "__background__")):
            boxes.append({"scene_uid": uid, "animal": an, "marker": mk, "roi_kind": kind,
                          "region": region, "seed_n": "1", "sec_x": 128, "sec_y": 128,
                          "sec_r": 20, "czi_file": "x.czi", "scene_index": 0, "czi_cx": 0,
                          "czi_cy": 0, "czi_x0": 0, "czi_y0": 0, "czi_w": 100, "czi_h": 100,
                          "axis_a_um": 1000.0, "axis_b_um": 1000.0})
        for idx, kind, region, vals in ((1, "roi", "Dm", roi),
                                        (2, "background", "__background__", BG)):
            for v in vals:
                nid += 1
                nuc_rows.append({"scene_uid": uid, "animal": an, "marker": mk,
                                 "roi_kind": kind, "region": region, "seed_n": "1",
                                 "roi_index": idx, "nucleus_id": nid, "czi_x": 0, "czi_y": 0,
                                 "sec_x": 128, "sec_y": 128, "area_um2": 50,
                                 "equiv_diam_um": 10.0, "dapi_mean": 1000, "marker_mean": v,
                                 "marker_median": v, "marker_p90": v, "censored": 0,
                                 "artifact": 0})
    npath, bpath = os.path.join(tmp, "roi_nuclei.csv"), os.path.join(tmp, "roi_boxes.csv")
    for path, cols, rws in ((npath, NUC_COLS, nuc_rows), (bpath, BOX_COLS, boxes)):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rws)
    return npath, bpath


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run_06a(tmp, npath, bpath):
    keep = (G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes)
    G6A.NUCLEI_CSV = npath
    G6A.MEAS_CSV = os.path.join(tmp, "roi_measurements.csv")
    G6A.SPEC_CSV = os.path.join(tmp, "detector_specificity.csv")
    G5.all_boxes = lambda: G5.load_csv(bpath)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = G6A.main()
    finally:
        G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes = keep
    return code, out.getvalue()


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


tmp = tempfile.mkdtemp(prefix="corr_")
npath, bpath = write_fixture(tmp, DATA)
code, out6a = run_06a(tmp, npath, bpath)
chk("06a runs", code, 0)
corr = read(os.path.join(tmp, "roi_corrected.csv"))
chk("roi_corrected.csv is written", corr is not None, True)
chk("its header is ROI_CORRECTED_COLUMNS", list(corr[0]) if corr else None,
    RR.ROI_CORRECTED_COLUMNS)
chk("one row per stained (section, marker, region); no control rows",
    sorted((r["scene_uid"], r["marker"]) for r in corr),
    [("A1_s01_sc01", "PCNA"), ("A1_s01_sc01", "pERK"), ("A1_s01_sc02", "PCNA"),
     ("A1_s02_sc01", "pERK"), ("B2_s01_sc01", "pERK")])
c = one(corr, "A1_s01_sc01")
chk("same file as the pERK control: f = 1/10", (c["fp_rate"], c["fp_rate_n"]), ("0.1", "10"))
chk("...source same_file", c["fp_rate_source"].startswith("same_file: A1_s01_sc02"), True)
chk("...corrected (0.3 - 0.1) / 0.9", approx(c["frac_positive_corrected"],
                                             round(0.2 / 0.9, 4)), True)
chk("...the raw rate is 06a's frac_positive", c["frac_positive"], "0.3")

print("--- multiplex_partial_control_correction ---")
p = one(corr, "A1_s01_sc02", "PCNA")
chk("the pERK-only control's PCNA rows are corrected as stained rows",
    p is not None and p["frac_positive"] == "0.2", True)
chk("...against the same-animal PCNA control", p["fp_rate_source"].startswith(
    "same_animal: A1_s02_sc01") if p else None, True)
chk("...(0.2 - 0.1) / 0.9", approx(p["frac_positive_corrected"], round(0.1 / 0.9, 4))
    if p else None, True)
b = one(corr, "B2_s01_sc01")
chk("no control on the file or animal: none, never study-wide",
    (b["fp_rate_source"], b["frac_positive_corrected"]),
    ("none: no same-slide or same-animal control", ""))
a2 = one(corr, "A1_s02_sc01")
chk("A1's other file: the same-animal pERK control", a2["fp_rate_source"].startswith(
    "same_animal: A1_s01_sc02"), True)
chk("...(0.4 - 0.1) / 0.9", approx(a2["frac_positive_corrected"], round(0.3 / 0.9, 4)), True)
chk("06a says it wrote it", "roi_corrected.csv" in out6a, True)

print("--- roi_measurements_unchanged ---")
# The hash of roi_measurements.csv for this fixture, taken at the commit before
# this task (87a8753): the correction adds a file, never a byte to this one.
MEAS_SHA_BEFORE = "1e0d1de021e6e5c12a65371fe936def48e4697aa762e94f6e364b544cd5a9462"
chk("roi_measurements.csv byte-identical to before this task",
    sha(os.path.join(tmp, "roi_measurements.csv")), MEAS_SHA_BEFORE)

print("--- no no_primary control: no file ---")
G6A.CONFIG["reagent_controls"] = {"declared": "no"}
try:
    code, _o = run_06a(tmp, npath, bpath)
finally:
    G6A.CONFIG["reagent_controls"] = RC_BLOCK
chk("06a runs", code, 0)
chk("a previous roi_corrected.csv is removed",
    os.path.exists(os.path.join(tmp, "roi_corrected.csv")), False)
iso_block = {"declared": "yes", "scenes": {
    k: dict(v, kind="isotype") for k, v in RC_BLOCK["scenes"].items()}}
G6A.CONFIG["reagent_controls"] = iso_block
try:
    run_06a(tmp, npath, bpath)
finally:
    G6A.CONFIG["reagent_controls"] = RC_BLOCK
chk("isotype controls only: no file either",
    os.path.exists(os.path.join(tmp, "roi_corrected.csv")), False)

print("--- own_marker_density (06a, end to end) ---")
# PCNA segmented on its own channel: the stained density and the control's in
# the note are 06a's positive_density_per_mm2 and the report's positive_per_mm2.
otmp = tempfile.mkdtemp(prefix="corr_own_")
onpath, obpath = write_fixture(otmp, DATA)
with open(onpath, newline="", encoding="utf-8") as fh:
    orows = list(csv.DictReader(fh))
for r in orows:
    r["segmented_on"] = "PCNA" if r["marker"] == "PCNA" else ""
    r["nucleus_shaped"] = "1"
with open(onpath, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(orows[0]))
    w.writeheader()
    w.writerows(orows)
code, _o = run_06a(otmp, onpath, obpath)
chk("06a runs", code, 0)
ocorr = read(os.path.join(otmp, "roi_corrected.csv"))
omeas = read(os.path.join(otmp, "roi_measurements.csv"))
orep = read(os.path.join(otmp, "reagent_controls.csv"))
oc = one(ocorr, "A1_s01_sc01", "PCNA")
om = [r for r in omeas if (r["scene_uid"], r["marker"], r["roi_kind"])
      == ("A1_s01_sc01", "PCNA", "roi")][0]
orc = one(orep, "A1_s02_sc01", "PCNA")
chk("own marker: no fraction, no corrected fraction, no fp_rate",
    (oc["frac_positive"], oc["frac_positive_corrected"], oc["fp_rate"]), ("", "", ""))
chk("...positive_density_per_mm2 is 06a's", oc["positive_density_per_mm2"],
    om["positive_density_per_mm2"])
chk("...the control density in the note is the report's positive_per_mm2",
    f"control positive density {orc['positive_per_mm2']} per mm2" in oc["correction_note"],
    True)
chk("...and a corrected density is given",
    approx(oc["positive_density_corrected"],
           round(float(om["positive_density_per_mm2"]) - float(orc["positive_per_mm2"]), 1),
           0.11), True)

# ===================================================== 06c
print("--- 06c: the corrected columns beside the raw rate ---")
import openpyxl  # noqa: E402

run_06a(tmp, npath, bpath)
G6C = load_stage("06c_excel_dataset.py", name="lsstage_06c_corr")
xlsx = os.path.join(tmp, "roi_dataset.xlsx")
saved = (G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes)
G6C.NUCLEI_CSV, G6C.MEAS_CSV = npath, os.path.join(tmp, "roi_measurements.csv")
G6C.G5.all_boxes = lambda: G6C.G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code6c = G6C.main(["--out", xlsx])
finally:
    G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes = saved
chk("06c runs", code6c, 0)
wb = openpyxl.load_workbook(xlsx)
name = G6C.CORRECTED_SHEET
chk("a corrected sheet", name in wb.sheetnames, True)
ws = wb[name]
vals = [[c.value for c in row] for row in ws.iter_rows()]
text = " ".join(str(v[0]) for v in vals if v and isinstance(v[0], str))
chk("its header states the sensitivity-1 assumption",
    "sensitivity" in text and "lower bound" in text, True)
chk("...and that it is provisional", "provisional" in text, True)
head = [v for v in vals if v and v[0] == "scene_uid"][0]
i = head.index("frac_positive")
chk("frac_positive_corrected right beside the raw frac_positive",
    head[i + 1:i + 4], ["frac_positive_corrected", "frac_positive_corrected_lo",
                        "frac_positive_corrected_hi"])
chk("the existing sheets are untouched", wb.sheetnames[:4],
    ["by_roi", "by_disc", "by_section", "coverage"])
os.remove(os.path.join(tmp, "roi_corrected.csv"))
G6C.NUCLEI_CSV, G6C.MEAS_CSV = npath, os.path.join(tmp, "roi_measurements.csv")
G6C.G5.all_boxes = lambda: G6C.G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        G6C.main(["--out", xlsx])
finally:
    G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes = saved
chk("no roi_corrected.csv: no sheet", name in openpyxl.load_workbook(xlsx).sheetnames, False)

print("--- an operator pin to a control not measured yet ---")
# A1_s02_sc02: a confirmed pERK no_primary control on A1's second file, boxed by
# 05a but not detected yet. A1_s02_sc01 (stained, same file) is corrected today
# by the same-animal control A1_s01_sc02, measured.
LATE = "A1_s02_sc02"


def write_manifest(rows):
    with open(os.path.join(mdir, "manifest_scenes.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["scene_uid", "file", "animal", "marker_channel"])
        w.writeheader()
        for uid, f, a in rows:
            w.writerow({"scene_uid": uid, "file": f, "animal": a, "marker_channel": "pERK"})


def run_late(paired_with):
    """06a with LATE confirmed (and boxed, not measured): roi_corrected.csv's rows."""
    ltmp = tempfile.mkdtemp(prefix="corr_late_")
    lnpath, lbpath = write_fixture(ltmp, DATA)
    with open(lbpath, newline="", encoding="utf-8") as fh:
        brows = list(csv.DictReader(fh))
    brows += [dict(b, scene_uid=LATE) for b in brows
              if (b["scene_uid"], b["marker"]) == ("A1_s02_sc01", "pERK")]
    with open(lbpath, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BOX_COLS)
        w.writeheader()
        w.writerows(brows)
    block = {"declared": "yes",
             "scenes": dict(RC_BLOCK["scenes"], **{LATE: {
                 "kind": "no_primary", "markers": ["pERK"], "source": "manual"}})}
    if paired_with:
        block["paired_with"] = paired_with
    write_manifest(MANIFEST + [(LATE, "A1_2.czi", "A1")])
    G6A.CONFIG["reagent_controls"] = block
    try:
        code, out = run_06a(ltmp, lnpath, lbpath)
    finally:
        G6A.CONFIG["reagent_controls"] = RC_BLOCK
        write_manifest(MANIFEST)
    assert code == 0, out
    return read(os.path.join(ltmp, "roi_corrected.csv")), read(
        os.path.join(ltmp, "reagent_controls.csv"))


late_corr, late_rep = run_late(None)
chk("(the late control is reported as not measured)",
    [r["note"].startswith(RR.NOT_MEASURED) for r in late_rep if r["scene_uid"] == LATE],
    [True])
lc = one(late_corr, "A1_s02_sc01")
chk("no pin: a same-file control not measured yet does not hide the measured "
    "same-animal one", (lc["fp_rate_source"].startswith("same_animal: A1_s01_sc02"),
                        approx(lc["frac_positive_corrected"], round(0.3 / 0.9, 4))),
    (True, True))
late_corr, _r = run_late({LATE: ["A1_s02_sc01"]})
lc = one(late_corr, "A1_s02_sc01")
chk("operator pin to a control not measured yet: the corrected row is blank",
    (lc["frac_positive_corrected"], lc["fp_rate"]), ("", ""))
chk("...with the note 'paired control not measured yet'",
    "paired control not measured yet" in lc["correction_note"], True)
chk("...the source names the pinned control, not a fallback",
    (lc["fp_rate_source"].startswith("operator: " + LATE),
     "same_animal" in lc["fp_rate_source"]), (True, False))
chk("...the raw rate is still there", lc["frac_positive"], "0.4")
lo = one(late_corr, "A1_s01_sc01")
chk("...another section keeps its own same-file control",
    lo["fp_rate_source"].startswith("same_file: A1_s01_sc02"), True)

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
