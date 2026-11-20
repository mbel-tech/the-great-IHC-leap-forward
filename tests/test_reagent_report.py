"""Reagent controls measured and reported: 06a routes them out, and reports them.

A reagent control (no primary, isotype, unstained ...) is detected like any
section. 06a then keeps its rows (per scene x marker) out of every experimental
output - roi_measurements.csv, detector_specificity.csv, positivity_cuts.csv,
h and the Abercrombie fallbacks - and writes them to results/reagent_controls.csv
instead, beside the paired stained sections:

  A  the positivity rule's call on the control under its OWN per-section cut;
  B  the level comparison, and the control scored against EACH paired stained
     section's own cut (median and min-max; never a pooled cut). Empty under
     study-wide pairing, because across batches it measures the batch.

06b never sees a control, so an animal whose only scenes are controls is not
"missing" from the sheet. 06c adds a "Reagent controls" sheet, and its
limitation text switches only when a no-primary row exists.

Synthetic nuclei throughout.

Run:  python tests/test_reagent_report.py
"""

import contextlib
import csv
import io
import os
import re
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
    # A multiplex scene that is a control for pERK only: its PCNA is stained.
    "scenes": {"LS45_s09_sc01": {"kind": "no_primary", "markers": ["pERK"],
                                 "source": "manual"}},
    # A control file whose name does not parse, made-up subject: every marker.
    "files": {"NC_1.czi": {"kind": "no_primary", "markers": [], "source": "manual",
                           "subject": "RC1", "slide": 1}},
}
STUDY = use_temp_study(
    acquisition={"layout": "multiplex", "channels": CHANNELS},
    reagent_controls=RC_BLOCK)

import ls_overlay as LO  # noqa: E402
import ls_reagent_controls as RC  # noqa: E402
import ls_reagent_report as RR  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def nuc(uid, kind, value, marker="pERK", region="Dm", cen=False, own=False):
    return {"scene_uid": uid, "marker": marker,
            "region": "__background__" if kind == "background" else region,
            "roi_kind": kind, "marker_median": float(value), "censored": cen,
            "own": own}


def one(rows, uid, marker="pERK", region="Dm"):
    got = [r for r in rows if (r["scene_uid"], r["marker"], r["region"])
           == (uid, marker, region)]
    assert len(got) == 1, (uid, marker, region, rows)
    return got[0]


# ----------------------------------------------------------------- A: own cut
print("--- own_cut_rate ---")
ctl = ([nuc("C", "background", 100)] * 10
       + [nuc("C", "roi", v) for v in (90, 95, 105, 200)])
stained = [nuc("S1", "roi", v) for v in (100, 300)]
cuts = {("C", "pERK"): LO.section_cut([100.0] * 10), ("S1", "pERK"): 150.0}
chk("the control's cut is its own background's (100)", cuts[("C", "pERK")], 100.0)
rows = RR.report_rows(ctl, stained, cuts, {("C", "pERK"): ("same_file", ["S1"])})
r = one(rows, "C")
chk("n_nuclei is the ROI's nuclei only", r["n_nuclei"], 4)
chk("n_positive under its own cut", r["n_positive"], 2)
chk("pct_positive", r["pct_positive"], 50.0)
chk("one row per control x marker x region (no background row)", len(rows), 1)
chk("the paired stained section's pct beside it", r["stained_pct_median"], 50.0)
chk("specificity_margin = stained - control", r["specificity_margin"], 0.0)
chk("every column, in order", list(r), RR.COLUMNS)
# A censored nucleus is positive by any cut, and left out of every median.
ctl_c = ctl + [nuc("C", "roi", 1.0, cen=True)]
r = one(RR.report_rows(ctl_c, stained, cuts, {("C", "pERK"): ("same_file", ["S1"])}), "C")
chk("a censored nucleus counts as positive", r["n_positive"], 3)
chk("...and is out of control_nuclear_median", r["control_nuclear_median"], 100.0)
# Fewer than 5 background nuclei: no cut, blank, never 0.
r = one(RR.report_rows([nuc("C", "roi", 500)], stained, {("S1", "pERK"): 150.0},
                       {("C", "pERK"): ("same_file", ["S1"])}), "C")
chk("no own cut: n_positive blank, not 0", (r["n_positive"], r["pct_positive"]), ("", ""))
chk("...and the note says why", "background" in r["note"], True)
chk("...B is still scored against the paired cut", r["pct_above_paired_cut"], 100.0)

# ------------------------------------------------- B: each paired cut, not pooled
print("--- paired_each_cut ---")
ctl = ([nuc("C", "background", 100)] * 10
       + [nuc("C", "roi", v) for v in (90, 95, 160, 200)])
stained = ([nuc("S1", "roi", v) for v in (100, 200)]
           + [nuc("S2", "roi", v) for v in (100, 400)])
cuts = {("C", "pERK"): 100.0, ("S1", "pERK"): 150.0, ("S2", "pERK"): 300.0}
r = one(RR.report_rows(ctl, iter(stained), cuts,
                       {("C", "pERK"): ("same_animal", ["S1", "S2"])}), "C")
chk("pct_above_paired_cut_min (vs the 300 cut)", r["pct_above_paired_cut_min"], 0.0)
chk("pct_above_paired_cut_max (vs the 150 cut)", r["pct_above_paired_cut_max"], 50.0)
chk("pct_above_paired_cut is their median", r["pct_above_paired_cut"], 25.0)
# A pooled cut (median of 150 and 300 = 225) would give 0 - the score is not that.
chk("control_nuclear_median", r["control_nuclear_median"], 127.5)
chk("stained_nuclear_median pools the paired sections' ROI nuclei",
    r["stained_nuclear_median"], 150.0)
chk("level_ratio = control / stained", r["level_ratio"], 0.85)
chk("pairing column", r["pairing"], "same_animal")
chk("stained_pct over each paired section (50, 50)",
    (r["stained_pct_median"], r["stained_pct_min"], r["stained_pct_max"]), (50.0, 50.0, 50.0))
chk("no note when nothing is withheld", r["note"], "")

# ---------------------------------------------------------------- pairing order
print("--- pairing_for ---")
SCN = [
    {"scene_uid": "C_s01_sc01", "file": "C_1.czi", "animal": "C", "marker_channel": "pERK"},
    {"scene_uid": "C_s01_sc02", "file": "C_1.czi", "animal": "C", "marker_channel": "pERK"},
    {"scene_uid": "C_s02_sc01", "file": "C_2.czi", "animal": "C", "marker_channel": "pERK"},
    {"scene_uid": "D_s01_sc01", "file": "D_1.czi", "animal": "D", "marker_channel": "pERK"},
    {"scene_uid": "N_s01_sc01", "file": "NC.czi", "animal": "N", "marker_channel": "pERK"},
    {"scene_uid": "D_s02_sc01", "file": "D_2.czi", "animal": "D", "marker_channel": "PCNA"},
]
cfg = {"reagent_controls": {"scenes": {"C_s01_sc01": {"kind": "no_primary", "markers": []},
                                       "N_s01_sc01": {"kind": "isotype", "markers": []}}}}
lk = RC.expand(cfg, SCN)
chk("same file first", RR.pairing_for("C_s01_sc01", "pERK", lk, SCN, cfg),
    ("same_file", ["C_s01_sc02"]))
cfg2 = {"reagent_controls": dict(cfg["reagent_controls"],
                                 scenes={**cfg["reagent_controls"]["scenes"],
                                         "C_s01_sc02": {"kind": "no_primary"}})}
lk2 = RC.expand(cfg2, SCN)
chk("then the same animal and marker (controls never paired)",
    RR.pairing_for("C_s01_sc01", "pERK", lk2, SCN, cfg2), ("same_animal", ["C_s02_sc01"]))
chk("then every stained section of that marker", RR.pairing_for("N_s01_sc01", "pERK", lk, SCN, cfg),
    ("study_wide", ["C_s01_sc02", "C_s02_sc01", "D_s01_sc01"]))
cfg3 = {"reagent_controls": dict(cfg["reagent_controls"],
                                 paired_with={"NC.czi": ["D_1.czi", "C_s02_sc01"]})}
chk("an operator override wins, files and uids both resolved",
    RR.pairing_for("N_s01_sc01", "pERK", RC.expand(cfg3, SCN), SCN, cfg3),
    ("operator", ["C_s02_sc01", "D_s01_sc01"]))

print("--- study_wide_blanks_B ---")
kind, uids = RR.pairing_for("N_s01_sc01", "pERK", lk, SCN, cfg)
chk("pairing is study_wide", kind, "study_wide")
ctl = ([nuc("N_s01_sc01", "background", 100)] * 10
       + [nuc("N_s01_sc01", "roi", v) for v in (90, 95, 160, 200)])
stained = [nuc(u, "roi", v) for u in uids for v in (100, 200)]
cuts = {("N_s01_sc01", "pERK"): 100.0, **{(u, "pERK"): 150.0 for u in uids}}
r = one(RR.report_rows(ctl, stained, cuts, {("N_s01_sc01", "pERK"): (kind, uids)}),
        "N_s01_sc01")
chk("pairing column says study_wide", r["pairing"], "study_wide")
chk("every B column is empty",
    [r[c] for c in ("level_ratio", "control_nuclear_median", "stained_nuclear_median",
                    "pct_above_paired_cut", "pct_above_paired_cut_min",
                    "pct_above_paired_cut_max")], [""] * 6)
chk("the note says why: batch", "batch" in r["note"], True)
chk("A is still reported", (r["n_positive"], r["pct_positive"], r["stained_pct_median"]),
    (2, 50.0, 50.0))

# ------------------------------------------------------------- segment: own
print("--- own_segment_density ---")
ctl = ([nuc("C", "background", 100, marker="PCNA", own=True)] * 10
       + [nuc("C", "roi", v, marker="PCNA", own=True) for v in (90, 95, 105, 200)])
stained = [nuc("S1", "roi", v, marker="PCNA", own=True) for v in (100, 300)]
cuts = {("C", "PCNA"): 100.0, ("S1", "PCNA"): 150.0}
r = one(RR.report_rows(ctl, stained, cuts, {("C", "PCNA"): ("same_file", ["S1"])},
                       areas={("C", "PCNA", "Dm"): 0.5},
                       factors={("PCNA", "Dm"): 0.5}), "C", marker="PCNA")
chk("own: pct_positive withheld", r["pct_positive"], "")
chk("own: positive_per_mm2 = 2 x 0.5 (Abercrombie) / 0.5 mm2, as 06a",
    r["positive_per_mm2"], 2.0)
chk("own: the fractions beside it are withheld too",
    [r[c] for c in ("stained_pct_median", "specificity_margin", "pct_above_paired_cut")],
    ["", "", ""])
chk("own: the note says so", "denominator" in r["note"] or "counterstain" in r["note"], True)
# A region with ROI discs but no nucleus still gets a row: a control never disappears.
r = one(RR.report_rows([], [], {}, {("C", "pERK"): ("same_file", [])},
                       areas={("C", "pERK", "Dl"): 0.2}), "C", region="Dl")
chk("an empty control region is reported with n_nuclei 0", r["n_nuclei"], 0)

# ------------------------------------------------------------ limitation text
print("--- limitation_switch ---")
G6C = load_stage("06c_excel_dataset.py", name="lsstage_06c_rr")
doc = " ".join(G6C.__doc__.split())
chk("no rows: the existing 06c text, unchanged", RR.limitation_text([]) in doc, True)
chk("...and it is the no-no-primary text", "no no-primary control" in RR.limitation_text([]),
    True)
iso = {"kind": "isotype"}
chk("an isotype row alone does not switch it", RR.limitation_text([iso]), RR.limitation_text([]))
np_row = {"kind": "no_primary"}
chk("a no_primary row: it names the Reagent controls sheet",
    "Reagent controls" in RR.limitation_text([np_row]), True)
chk("...and no longer says there is none",
    "no no-primary control" in RR.limitation_text([np_row]), False)

print("--- no_disc_language ---")
texts = [open(os.path.join(SCRIPTS, "ls_reagent_report.py"), encoding="utf-8").read(),
         " ".join(RR.sheet_header([np_row])), " ".join(RR.sheet_header([]))]
bad = []
for t in texts:
    for sent in re.split(r"(?<=[.;:])\s+|\n\s*\n", t):
        s = " ".join(sent.lower().split())
        if "negative control" in s and "background disc" in s:
            bad.append(s[:120])
chk("nothing new calls a background disc a negative control", bad, [])
chk("the sheet header says a background disc is not a reagent control",
    "background disc is not a reagent control" in " ".join(RR.sheet_header([])), True)

# ===================================================== 06a, end to end, multiplex
print("--- multiplex_partial_control (06a) ---")
G6A = load_stage("06a_roi_dataset.py", name="lsstage_06a_rr")
G5 = G6A.G5

MANIFEST = [
    ("LS45_s01_sc01", "LS45_1.czi", "LS45"),
    ("LS45_s02_sc01", "LS45_2.czi", "LS45"),
    ("LS45_s09_sc01", "LS45_9.czi", "LS45"),     # control for pERK only
    ("RC1_s01_sc01", "NC_1.czi", "RC1"),         # control for every marker
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
# (uid, marker) -> (background medians, ROI medians, ROI diameter)
DATA = {
    ("LS45_s01_sc01", "pERK"): ([80, 90, 100, 110, 120], [100, 200, 150, 140], 10.0),
    ("LS45_s02_sc01", "pERK"): ([100] * 5, [90, 120, 130, 95], 10.0),
    ("LS45_s09_sc01", "pERK"): ([100] * 10, [90, 95, 105, 200], 40.0),
    ("RC1_s01_sc01", "pERK"): ([100] * 10, [90, 95, 105, 200], 40.0),
    ("LS45_s01_sc01", "PCNA"): ([50] * 5, [40, 60, 80], 6.0),
    ("LS45_s09_sc01", "PCNA"): ([50] * 5, [40, 60, 80], 6.0),
    ("RC1_s01_sc01", "PCNA"): ([50] * 5, [40, 60, 80], 40.0),
}


def write_fixture(tmp):
    nuc, boxes, nid = [], [], 0
    for (uid, mk), (bg, roi, diam) in DATA.items():
        an = uid.split("_")[0]
        for kind, region in (("roi", "Dm"), ("background", "__background__")):
            boxes.append({"scene_uid": uid, "animal": an, "marker": mk, "roi_kind": kind,
                          "region": region, "seed_n": "1", "sec_x": 128, "sec_y": 128,
                          "sec_r": 20, "czi_file": "x.czi", "scene_index": 0, "czi_cx": 0,
                          "czi_cy": 0, "czi_x0": 0, "czi_y0": 0, "czi_w": 100, "czi_h": 100,
                          "axis_a_um": 1000.0, "axis_b_um": 1000.0})
        for idx, kind, region, vals in ((1, "roi", "Dm", roi),
                                        (2, "background", "__background__", bg)):
            for v in vals:
                nid += 1
                nuc.append({"scene_uid": uid, "animal": an, "marker": mk, "roi_kind": kind,
                            "region": region, "seed_n": "1", "roi_index": idx,
                            "nucleus_id": nid, "czi_x": 0, "czi_y": 0, "sec_x": 128,
                            "sec_y": 128, "area_um2": 50, "equiv_diam_um": diam,
                            "dapi_mean": 1000, "marker_mean": v, "marker_median": v,
                            "marker_p90": v, "censored": 0, "artifact": 0})
    npath, bpath = os.path.join(tmp, "roi_nuclei.csv"), os.path.join(tmp, "roi_boxes.csv")
    for path, cols, rows in ((npath, NUC_COLS, nuc), (bpath, BOX_COLS, boxes)):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    return npath, bpath


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


tmp = tempfile.mkdtemp(prefix="rr_")
npath, bpath = write_fixture(tmp)
keep = (G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes)
G6A.NUCLEI_CSV = npath
G6A.MEAS_CSV = os.path.join(tmp, "roi_measurements.csv")
G6A.SPEC_CSV = os.path.join(tmp, "detector_specificity.csv")
G5.all_boxes = lambda: G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()) as out6a:
        code = G6A.main()
finally:
    G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes = keep
chk("06a runs", code, 0)
meas = read(os.path.join(tmp, "roi_measurements.csv"))
spec = read(os.path.join(tmp, "detector_specificity.csv"))
cutsf = read(os.path.join(tmp, "positivity_cuts.csv"))
rep = read(os.path.join(tmp, "reagent_controls.csv"))
pairs = lambda rows: sorted({(r["scene_uid"], r["marker"]) for r in rows})  # noqa: E731
chk("the partial control's PCNA rows are in roi_measurements.csv",
    ("LS45_s09_sc01", "PCNA") in pairs(meas), True)
chk("...and its pERK rows are not", ("LS45_s09_sc01", "pERK") in pairs(meas), False)
chk("no row of the all-marker control file", [p for p in pairs(meas) if p[0] == "RC1_s01_sc01"],
    [])
chk("detector_specificity.csv: stained sections only", pairs(spec),
    [("LS45_s01_sc01", "PCNA"), ("LS45_s01_sc01", "pERK"), ("LS45_s02_sc01", "pERK"),
     ("LS45_s09_sc01", "PCNA")])
chk("positivity_cuts.csv: stained sections only", pairs(cutsf), pairs(spec))
chk("h is from stained nuclei only (control nuclei are 40 um)",
    sorted({r["mean_nucleus_diam_um"] for r in meas if r["roi_kind"] == "roi"}),
    ["10.0", "6.0"])
chk("background rows' h fallback is stained-only too",
    sorted({r["mean_nucleus_diam_um"] for r in meas if r["roi_kind"] == "background"}),
    ["10.0", "6.0"])
chk("reagent_controls.csv is written", rep is not None, True)
chk("...with the partial control's pERK rows only",
    sorted({r["marker"] for r in rep if r["scene_uid"] == "LS45_s09_sc01"}), ["pERK"])
chk("...and the all-marker control for both markers",
    sorted({r["marker"] for r in rep if r["scene_uid"] == "RC1_s01_sc01"}), ["PCNA", "pERK"])
chk("its header is COLUMNS", list(rep[0]), RR.COLUMNS)
p9 = [r for r in rep if r["scene_uid"] == "LS45_s09_sc01"][0]
chk("same animal, other files: same_animal pairing", p9["pairing"], "same_animal")
chk("own cut 100 -> 2 of 4 positive", (p9["n_positive"], p9["pct_positive"]), ("2", "50.0"))
chk("file and kind come from the manifest and config", (p9["file"], p9["kind"]),
    ("LS45_9.czi", "no_primary"))
chk("each paired cut: 144.5 -> 25%, 100 -> 50%",
    (p9["pct_above_paired_cut_min"], p9["pct_above_paired_cut_max"]), ("25.0", "50.0"))
AB = (G6A.T_UM / (G6A.T_UM + 10.0)) if G6A.AB_ON else 1.0
chk("positive_per_mm2: 2 x the stained pERK/Dm Abercrombie factor / pi mm2",
    p9["positive_per_mm2"], str(round(2 * AB / 3.141592653589793, 1)))
ms = [r for r in meas if (r["marker"], r["region"]) == ("pERK", "Dm")][0]
chk("...the same factor 06a's own rows carry", ms["abercrombie_factor"], str(round(AB, 4)))
rc1 = [r for r in rep if r["scene_uid"] == "RC1_s01_sc01" and r["marker"] == "pERK"][0]
chk("a made-up subject with no stained scenes: study_wide", rc1["pairing"], "study_wide")
chk("...B empty, note says batch", (rc1["level_ratio"], "batch" in rc1["note"]), ("", True))
chk("06a says how many control rows it wrote", "reagent_controls.csv" in out6a.getvalue(),
    True)

# A stale report must not outlive its controls.
G6A.CONFIG["reagent_controls"] = {"declared": "no"}
G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV = npath, os.path.join(tmp, "roi_measurements.csv"), \
    os.path.join(tmp, "detector_specificity.csv")
G5.all_boxes = lambda: G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        G6A.main()
finally:
    G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes = keep
    G6A.CONFIG["reagent_controls"] = RC_BLOCK
chk("no control confirmed: the previous reagent_controls.csv is removed",
    os.path.exists(os.path.join(tmp, "reagent_controls.csv")), False)
chk("...and every section is experimental again",
    ("RC1_s01_sc01", "pERK") in pairs(read(os.path.join(tmp, "roi_measurements.csv"))), True)
# Back to the routed run for 06b and 06c.
G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV = npath, os.path.join(tmp, "roi_measurements.csv"), \
    os.path.join(tmp, "detector_specificity.csv")
G5.all_boxes = lambda: G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        G6A.main()
finally:
    G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes = keep

# ============================================================= 06b
print("--- animal_with_both (06b) ---")
B = load_stage("06b_join_sampling.py", name="lsstage_06b_rr")
sheet = os.path.join(tmp, "sampling.csv")
with open(sheet, "w", newline="", encoding="utf-8") as fh:
    fh.write("Fish ID,treatment\nLS45,trt\n")


def run_06b(meas_csv):
    saved = (B.MEAS_CSV, B.META_CSV, B.DATASET_CSV, sys.argv)
    B.MEAS_CSV = meas_csv
    B.META_CSV = os.path.join(tmp, "animal_metadata.csv")
    B.DATASET_CSV = os.path.join(tmp, "roi_dataset.csv")
    B.CONFIG["sampling"] = {"id_column": "Fish ID", "group_columns": ["treatment"],
                            "match": "exact", "fill": []}
    sys.argv = ["06b", "--xlsx", sheet]
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = B.main()
    finally:
        B.MEAS_CSV, B.META_CSV, B.DATASET_CSV, sys.argv = saved
        B.CONFIG.pop("sampling", None)
    return code, out.getvalue()


code, out = run_06b(os.path.join(tmp, "roi_measurements.csv"))
chk("06b joins LS45 (stained and control slides) and does not stop", code, 0)
chk("...RC1, whose only scenes are controls, is not reported missing",
    "RC1" in out, False)
meta = read(os.path.join(tmp, "animal_metadata.csv"))
chk("...animal_metadata.csv is LS45 alone", [r["animal"] for r in meta], ["LS45"])
chk("...roi_dataset.csv holds no control row",
    [r for r in read(os.path.join(tmp, "roi_dataset.csv"))
     if (r["scene_uid"], r["marker"]) in {("LS45_s09_sc01", "pERK"), ("RC1_s01_sc01", "pERK"),
                                          ("RC1_s01_sc01", "PCNA")}], [])
# The proof bites: the same table with the control's rows in it stops 06b.
leaky = os.path.join(tmp, "leaky.csv")
with open(leaky, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(meas[0]))
    w.writeheader()
    w.writerows(meas + [dict(meas[0], scene_uid="RC1_s01_sc01", animal="RC1")])
code, out = run_06b(leaky)
chk("(a control row reaching 06b would have stopped it on RC1)",
    (code, "RC1" in out), (1, True))

# ============================================================= 06c
print("--- 06c sheet ---")
saved = (G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes)
G6C.NUCLEI_CSV, G6C.MEAS_CSV = npath, os.path.join(tmp, "roi_measurements.csv")
G6C.G5.all_boxes = lambda: G6C.G5.load_csv(bpath)
xlsx = os.path.join(tmp, "roi_dataset.xlsx")
try:
    with contextlib.redirect_stdout(io.StringIO()) as out6c:
        code = G6C.main(["--out", xlsx])
finally:
    G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes = saved
chk("06c runs", code, 0)
chk("...and does not call the controls a 06a that is behind", "behind" in out6c.getvalue(), False)
import openpyxl  # noqa: E402
wb = openpyxl.load_workbook(xlsx)
chk("a 'Reagent controls' sheet is added", "Reagent controls" in wb.sheetnames, True)
ws = wb["Reagent controls"]
cells = [[c for c in row] for row in ws.iter_rows(values_only=True)]
head = " ".join(str(row[0]) for row in cells if row and row[0]
                and not str(row[0]).startswith("scene_uid"))
chk("its header names the sheet (a no_primary row exists)",
    "Reagent controls" in head and "no no-primary control" not in head, True)
chk("its header explains A and B", ("A " in head or "A:" in head) and "B" in head, True)
chk("...and isotype vs no-primary", "isotype" in head and "secondary" in head, True)
hdr = [row for row in cells if row and row[0] == "scene_uid"]
chk("the table under it has COLUMNS", list(hdr[0][:len(RR.COLUMNS)]) if hdr else None,
    RR.COLUMNS)
chk("coverage holds no control-only animal",
    sorted({r[0] for r in list(wb["coverage"].iter_rows(values_only=True))[1:]}), ["LS45"])
chk("coverage counts the partial control only for its stained marker",
    sorted((r[0], r[1], r[5]) for r in list(wb["coverage"].iter_rows(values_only=True))[1:]),
    [("LS45", "PCNA", 2), ("LS45", "pERK", 2)])
print("--- 06d coverage ---")
G6D = load_stage("06d_excel_by_slide.py", name="lsstage_06d_rr")
saved_d = (G6D.G6C.MEAS_CSV, G6D.G5.all_boxes)
G6D.G6C.MEAS_CSV = os.path.join(tmp, "roi_measurements.csv")
G6D.G5.all_boxes = lambda: G6D.G5.load_csv(bpath)
xlsx_d = os.path.join(tmp, "by_slide.xlsx")
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code = G6D.main(["--out", xlsx_d])
finally:
    G6D.G6C.MEAS_CSV, G6D.G5.all_boxes = saved_d
chk("06d runs", code, 0)
cov = list(openpyxl.load_workbook(xlsx_d)["coverage"].iter_rows(values_only=True))
chk("06d coverage: no control slide or control marker",
    sorted((r[0], r[1]) for r in cov[1:]),
    [("LS45_s01", "PCNA"), ("LS45_s01", "pERK"), ("LS45_s02", "pERK"), ("LS45_s09", "PCNA")])

# Controls first: 05c measures them before any stained section, so early in a
# run 06a has only them. 06a, 06c and 06d must all carry on.
print("--- controls_only (05c's first sections) ---")
only = os.path.join(tmp, "only")
os.makedirs(only)
with open(npath, newline="", encoding="utf-8") as fh:
    allnuc = list(csv.DictReader(fh))
onpath = os.path.join(only, "roi_nuclei.csv")
with open(onpath, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=NUC_COLS)
    w.writeheader()
    w.writerows(r for r in allnuc if r["scene_uid"] == "RC1_s01_sc01")
omeas = os.path.join(only, "roi_measurements.csv")
G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV = onpath, omeas, os.path.join(only, "spec.csv")
G5.all_boxes = lambda: G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code = G6A.main()
finally:
    G6A.NUCLEI_CSV, G6A.MEAS_CSV, G6A.SPEC_CSV, G5.all_boxes = keep
chk("06a on controls only runs", code, 0)
chk("...writes no roi_measurements.csv, and the report",
    (os.path.exists(omeas), os.path.exists(os.path.join(only, "reagent_controls.csv"))),
    (False, True))
G6C.NUCLEI_CSV, G6C.MEAS_CSV = onpath, omeas
G6C.G5.all_boxes = lambda: G6C.G5.load_csv(bpath)
oxl = os.path.join(only, "roi_dataset.xlsx")
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code = G6C.main(["--out", oxl])
finally:
    G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes = saved
chk("06c on controls only exits 0 (06e's loop would end on 1)", code, 0)
chk("...with the Reagent controls sheet alone", openpyxl.load_workbook(oxl).sheetnames,
    ["Reagent controls"])
G6D.G6C.MEAS_CSV = omeas
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code = G6D.main(["--out", os.path.join(only, "by_slide.xlsx")])
finally:
    G6D.G6C.MEAS_CSV = saved_d[0]
chk("06d on controls only exits 0", code, 0)

# No control: no sheet.
os.remove(os.path.join(tmp, "reagent_controls.csv"))
G6C.NUCLEI_CSV, G6C.MEAS_CSV = npath, os.path.join(tmp, "roi_measurements.csv")
G6C.G5.all_boxes = lambda: G6C.G5.load_csv(bpath)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        G6C.main(["--out", xlsx])
finally:
    G6C.NUCLEI_CSV, G6C.MEAS_CSV, G6C.G5.all_boxes = saved
chk("no reagent_controls.csv: no sheet", "Reagent controls" in
    openpyxl.load_workbook(xlsx).sheetnames, False)

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
