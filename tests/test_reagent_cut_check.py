"""The k check: the positivity cut against no-primary reagent controls.

06a writes results/reagent_cut_check.csv, one row per marker x k on a fixed
grid. For each k:

  control_fp  the no-primary controls' ROI nuclei called positive, each control
              section scored against ITS OWN cut formed at k, pooled over control
              sections, with a Wilson 95% interval;
  disc_fp     the same k applied leave-one-disc-out on the STAINED sections'
              background discs (06a's leave_one_disc_out), pooled;
  suggested   1 on the smallest k whose control_fp meets positivity.fp_target.

Only no_primary controls count; an isotype control contributes nothing. A
control with too few background nuclei for a cut is left out, and the note
names it. With no measured no_primary control there is no file.

Synthetic values throughout. Every number here is provisional until the
validation on the operator's study with known controls.

Run:  python tests/test_reagent_cut_check.py
"""

import contextlib
import csv
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
        # A multiplex scene that is a control for pERK only: its PCNA is stained.
        "LS45_s09_sc01": {"kind": "no_primary", "markers": ["pERK"], "source": "manual"},
        # An isotype control: never used for the k check.
        "LS45_s08_sc01": {"kind": "isotype", "markers": [], "source": "manual"},
    },
    # A control file whose name does not parse, made-up subject: every marker.
    "files": {"NC_1.czi": {"kind": "no_primary", "markers": [], "source": "manual",
                           "subject": "RC1", "slide": 1}},
}
STUDY = use_temp_study(
    acquisition={"layout": "multiplex", "channels": CHANNELS},
    reagent_controls=RC_BLOCK,
    positivity={"fp_target": 0.05, "cut_k": {"*": 3.0}})

import ls_overlay as LO  # noqa: E402
import ls_reagent_report as RR  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def of(rows, marker):
    return [r for r in rows if r["marker"] == marker]


# A background with a real spread: median 100, MAD 10 -> spread 14.826.
BG = [100.0] * 10 + [80.0, 90.0, 110.0, 120.0] * 5
ROI = [(float(v), False) for v in range(100, 260, 5)]       # 32 nuclei, spread above

# ------------------------------------------------------------------- wilson
print("--- wilson_known ---")
lo, hi = RR.wilson(1, 100)
chk("wilson(1, 100) lower ~ 0.0018", abs(lo - 0.0018) < 1e-3, True)
chk("wilson(1, 100) upper ~ 0.0545", abs(hi - 0.0545) < 1e-3, True)
chk("wilson(0, 10) starts at 0", RR.wilson(0, 10)[0], 0.0)
chk("wilson(10, 10) ends at 1", RR.wilson(10, 10)[1], 1.0)

# ------------------------------------------------------------- the grid
print("--- grid ---")
chk("K_GRID is the spec's", RR.K_GRID, (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0))
chk("columns", RR.CUT_CHECK_COLUMNS,
    ["marker", "k", "n_control_sections", "n_control_nuclei", "control_fp",
     "control_fp_lo", "control_fp_hi", "disc_fp", "disc_fp_n", "meets_target",
     "suggested", "note"])

print("--- fp_falls_with_k ---")
controls = {("C1", "pERK"): {"bg": BG, "roi": ROI}}
rows = RR.cut_check_rows(controls, {}, 0.05)
chk("one row per k", [r["k"] for r in rows], list(RR.K_GRID))
chk("every row has every column, in order",
    all(list(r) == RR.CUT_CHECK_COLUMNS for r in rows), True)
fps = [r["control_fp"] for r in rows]
chk("control_fp is non-increasing across K_GRID",
    all(a >= b for a, b in zip(fps, fps[1:])), True)
chk("...and actually falls", fps[0] > fps[-1], True)
# k = 3: cut 100 + 3 * 14.826 = 144.478; above it 145..255 -> 23 of 32.
r3 = [r for r in rows if r["k"] == 3.0][0]
chk("k=3: 23 of 32 control nuclei above the cut", r3["control_fp"], round(23 / 32, 4))
chk("...over 32 nuclei on 1 section", (r3["n_control_nuclei"], r3["n_control_sections"]),
    (32, 1))
lo3, hi3 = RR.wilson(23, 32)
chk("...with the Wilson interval", (r3["control_fp_lo"], r3["control_fp_hi"]),
    (round(lo3, 4), round(hi3, 4)))
chk("no stained discs: disc_fp blank, not 0", (r3["disc_fp"], r3["disc_fp_n"]), ("", ""))

print("--- censored ---")
cen = {("C1", "pERK"): {"bg": BG, "roi": [(1.0, True), (100.0, False)]}}
rc = RR.cut_check_rows(cen, {}, 0.05)
chk("a censored nucleus is positive at every k",
    [r["control_fp"] for r in rc], [0.5] * len(RR.K_GRID))

print("--- suggested_smallest_meeting_target ---")
# Two markers; pERK's rate falls under 5% within the grid.
ctl2 = {("C1", "pERK"): {"bg": BG, "roi": [(float(v), False) for v in
                                           [100] * 180 + [135, 150, 160, 170, 180,
                                                          190, 200, 210, 220, 230]]},
        ("C2", "PCNA"): {"bg": BG, "roi": [(100.0, False)] * 50}}
rows = RR.cut_check_rows(ctl2, {}, 0.05)
for mk in ("pERK", "PCNA"):
    m = of(rows, mk)
    first = next(r["k"] for r in m if r["control_fp"] <= 0.05)
    chk(f"{mk}: exactly one suggested row", sum(r["suggested"] == 1 for r in m), 1)
    chk(f"{mk}: ...the first k with control_fp <= 0.05",
        [r["k"] for r in m if r["suggested"] == 1], [first])
    chk(f"{mk}: meets_target is control_fp <= target",
        [r["meets_target"] for r in m], [int(r["control_fp"] <= 0.05) for r in m])
chk("pERK: 10 of 190 at k=2 misses 5%, 9 of 190 at k=2.5 meets it",
    [r["k"] for r in of(rows, "pERK") if r["suggested"] == 1], [2.5])
chk("rows are marker by marker, k ascending",
    [(r["marker"], r["k"]) for r in rows],
    [("PCNA", k) for k in RR.K_GRID] + [("pERK", k) for k in RR.K_GRID])
high = RR.cut_check_rows({("C1", "pERK"): {"bg": BG, "roi": [(1000.0, False)] * 10}},
                         {}, 0.05)
chk("none meets the target: no row suggested",
    [r["suggested"] for r in high], [0] * len(RR.K_GRID))
chk("...and the note says so",
    all(RR.NO_K_MEETS in r["note"] for r in high), True)
chk("NO_K_MEETS is the brief's words", RR.NO_K_MEETS, "no k on the grid meets the target")

print("--- control_without_cut_excluded ---")
ctl3 = {("C1", "pERK"): {"bg": BG, "roi": ROI},
        ("THIN_s01_sc01", "pERK"): {"bg": [100.0] * 4, "roi": [(1000.0, False)] * 20}}
rows = RR.cut_check_rows(ctl3, {}, 0.05)
chk("a control with 4 background values is not counted",
    {r["n_control_sections"] for r in rows}, {1})
chk("...nor are its nuclei", {r["n_control_nuclei"] for r in rows}, {32})
chk("...and the note names it", all("THIN_s01_sc01" in r["note"] for r in rows), True)
only_thin = RR.cut_check_rows({("THIN_s01_sc01", "pERK"): {"bg": [100.0] * 4,
                                                           "roi": [(1000.0, False)]}},
                              {}, 0.05)
chk("no control with a cut: rates, meets_target and suggested blank, not 0",
    {(r["control_fp"], r["control_fp_lo"], r["meets_target"], r["suggested"])
     for r in only_thin}, {("", "", "", "")})

print("--- multiplex_partial_control_k (function) ---")
ctl4 = {("M1", "pERK"): {"bg": BG, "roi": ROI},
        ("RC", "pERK"): {"bg": BG, "roi": ROI},
        ("RC", "PCNA"): {"bg": BG, "roi": ROI}}
rows = RR.cut_check_rows(ctl4, {}, 0.05)
chk("pERK counts both its controls", {r["n_control_sections"] for r in of(rows, "pERK")}, {2})
chk("PCNA counts only the control for PCNA",
    {r["n_control_sections"] for r in of(rows, "PCNA")}, {1})

print("--- isotype_not_used (function) ---")
iso = dict(ctl4)
iso[("ISO", "pERK")] = {"bg": BG, "roi": [(1000.0, False)] * 40, "kind": "isotype"}
chk("an isotype control contributes nothing",
    RR.cut_check_rows(iso, {}, 0.05), RR.cut_check_rows(ctl4, {}, 0.05))
chk("only isotype: no rows at all",
    RR.cut_check_rows({("ISO", "pERK"): iso[("ISO", "pERK")]}, {}, 0.05), [])

print("--- own channel ---")
own = RR.cut_check_rows({("C1", "PCNA"): {"bg": BG, "roi": ROI, "own": True}}, {}, 0.05)
chk("own-channel marker: no rate and no suggestion (06a withholds the rate)",
    {(r["control_fp"], r["meets_target"], r["suggested"]) for r in own}, {("", "", "")})
chk("...and the note says why", all("own channel" in r["note"] for r in own), True)

print("--- disc_fp (leave one disc out on stained sections) ---")
d1 = [(float(v), False) for v in (90, 95, 100, 105, 110, 150)]
d2 = [(float(v), False) for v in (92, 97, 100, 103, 108, 300)]
d3 = [(float(v), False) for v in (100, 100, 100)]           # too thin to leave out
stained = {("S1", "pERK"): [d1, d2, d3], ("S2", "pERK"): [d1, None]}
rows = RR.cut_check_rows({("C1", "pERK"): {"bg": BG, "roi": ROI}}, stained, 0.05)
for r in rows:
    want = LO.leave_one_disc_out([d1, d2, d3], r["k"])
    chk(f"k={r['k']}: disc_fp from S1 only (S2 has one usable disc)",
        (r["disc_fp"], r["disc_fp_n"]), (round(want[0] / want[1], 4), want[1]))

# ======================================================= 06a, leave_one_disc_out
print("--- leave_one_disc_out_k3_unchanged ---")
G6A = load_stage("06a_roi_dataset.py", name="lsstage_06a_kc")
G5 = G6A.G5


class _D:
    def __init__(self, v, cen=None):
        self.v = list(v)
        self.cen = list(cen) if cen is not None else [False] * len(self.v)


discs = [_D([90, 95, 100, 105, 110, 150]), _D([92, 97, 100, 103, 108, 300], [0] * 5 + [1]),
         None, _D([100, 100, 100])]
chk("leave_one_disc_out(discs) == leave_one_disc_out(discs, 3.0)",
    G6A.leave_one_disc_out(discs), G6A.leave_one_disc_out(discs, 3.0))


def base_loo(discs, k=3.0):
    """The rule as 06a wrote it before the k check, verbatim in substance."""
    usable = [[v for v, c in zip(d.v, d.cen) if not c] if d else [] for d in discs]
    qual = [i for i, u in enumerate(usable) if len(u) >= LO.CUT_MIN_NUCLEI]
    if len(qual) < 2:
        return None
    fp = n = 0
    for i in qual:
        cut = LO.section_cut([v for j, u in enumerate(usable) if j != i for v in u], k)
        d = discs[i]
        fp += sum(1 for v, c in zip(d.v, d.cen) if LO.is_positive(v, c, cut))
        n += len(d.v)
    return fp, n


chk("...and equals the base behaviour", G6A.leave_one_disc_out(discs), base_loo(discs))
chk("...at another k too", G6A.leave_one_disc_out(discs, 2.0), base_loo(discs, 2.0))
chk("...None with fewer than two usable discs",
    G6A.leave_one_disc_out([discs[0], None, discs[3]]), None)

# ===================================================== 06a, end to end, multiplex
print("--- 06a writes reagent_cut_check.csv ---")
MANIFEST = [
    ("LS45_s01_sc01", "LS45_1.czi", "LS45"),
    ("LS45_s02_sc01", "LS45_2.czi", "LS45"),
    ("LS45_s08_sc01", "LS45_8.czi", "LS45"),     # isotype, every marker
    ("LS45_s09_sc01", "LS45_9.czi", "LS45"),     # no_primary for pERK only
    ("RC1_s01_sc01", "NC_1.czi", "RC1"),         # no_primary for every marker
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
B1 = [90, 95, 100, 105, 110, 150]
B2 = [92, 97, 100, 103, 108, 300]
B3 = [80, 90, 100, 110, 120, 99]
SPREAD = list(range(100, 260, 10))
# (uid, marker) -> (background discs, ROI medians)
DATA = {
    ("LS45_s01_sc01", "pERK"): ([B1, B2, B3], [100, 200, 150, 140]),
    ("LS45_s02_sc01", "pERK"): ([B2, B3], [90, 120, 130, 95]),
    ("LS45_s08_sc01", "pERK"): ([B1, B2], [1000] * 30),
    ("LS45_s08_sc01", "PCNA"): ([B1, B2], [1000] * 30),
    ("LS45_s09_sc01", "pERK"): ([BG], SPREAD),
    ("RC1_s01_sc01", "pERK"): ([BG], SPREAD),
    ("LS45_s01_sc01", "PCNA"): ([B1, B3], [40, 60, 80]),
    ("LS45_s09_sc01", "PCNA"): ([B2, B3], [40, 60, 80]),
    ("RC1_s01_sc01", "PCNA"): ([BG], SPREAD),
}


def write_fixture(tmp, keep=lambda uid, mk: True):
    nuc, boxes, nid = [], [], 0
    for (uid, mk), (bgs, roi) in DATA.items():
        if not keep(uid, mk):
            continue
        an = uid.split("_")[0]
        discs = [(1, "roi", "Dm", roi)] + [(2 + j, "background", "__background__", b)
                                          for j, b in enumerate(bgs)]
        for _idx, kind, region, _vals in discs:
            boxes.append({"scene_uid": uid, "animal": an, "marker": mk, "roi_kind": kind,
                          "region": region, "seed_n": "1", "sec_x": 128, "sec_y": 128,
                          "sec_r": 20, "czi_file": "x.czi", "scene_index": 0, "czi_cx": 0,
                          "czi_cy": 0, "czi_x0": 0, "czi_y0": 0, "czi_w": 100, "czi_h": 100,
                          "axis_a_um": 1000.0, "axis_b_um": 1000.0})
        for idx, kind, region, vals in discs:
            for v in vals:
                nid += 1
                nuc.append({"scene_uid": uid, "animal": an, "marker": mk, "roi_kind": kind,
                            "region": region, "seed_n": "1", "roi_index": idx,
                            "nucleus_id": nid, "czi_x": 0, "czi_y": 0, "sec_x": 128,
                            "sec_y": 128, "area_um2": 50, "equiv_diam_um": 10.0,
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


tmp = tempfile.mkdtemp(prefix="kc_")
npath, bpath = write_fixture(tmp)
code, out6a = run_06a(tmp, npath, bpath)
chk("06a runs", code, 0)
chk("06a reads positivity.fp_target from the study (0.05 here)",
    G6A.CONFIG["positivity"]["fp_target"], 0.05)
kc = read(os.path.join(tmp, RR.CUT_CHECK_CSV))
chk("reagent_cut_check.csv is written beside roi_measurements.csv", kc is not None, True)
chk("its header is CUT_CHECK_COLUMNS", list(kc[0]) if kc else None, RR.CUT_CHECK_COLUMNS)
chk("one row per marker x k",
    [(r["marker"], r["k"]) for r in kc],
    [("PCNA", str(k)) for k in RR.K_GRID] + [("pERK", str(k)) for k in RR.K_GRID])
print("--- multiplex_partial_control_k (06a) ---")
chk("pERK: both no_primary controls (the partial one and the all-marker file)",
    {r["n_control_sections"] for r in kc if r["marker"] == "pERK"}, {"2"})
chk("PCNA: only the all-marker file (LS45_s09 is stained for PCNA)",
    {r["n_control_sections"] for r in kc if r["marker"] == "PCNA"}, {"1"})
chk("...16 ROI nuclei per control section",
    ({r["n_control_nuclei"] for r in kc if r["marker"] == "pERK"},
     {r["n_control_nuclei"] for r in kc if r["marker"] == "PCNA"}), ({"32"}, {"16"}))
print("--- isotype_not_used (06a) ---")
chk("the isotype control (every nucleus at 1000) is in no rate",
    max(float(r["control_fp"]) for r in kc) < 1.0, True)
want = RR.cut_check_rows(
    {("LS45_s09_sc01", "pERK"): {"bg": BG, "roi": [(float(v), False) for v in SPREAD]},
     ("RC1_s01_sc01", "pERK"): {"bg": BG, "roi": [(float(v), False) for v in SPREAD]},
     ("RC1_s01_sc01", "PCNA"): {"bg": BG, "roi": [(float(v), False) for v in SPREAD]}},
    {}, 0.05)
chk("control_fp is the no_primary controls' alone",
    [r["control_fp"] for r in kc], [str(r["control_fp"]) for r in want])

print("--- disc_fp agrees with detector_specificity.csv ---")
spec = read(os.path.join(tmp, "detector_specificity.csv"))
for mk in ("pERK", "PCNA"):
    s = [x for x in spec if x["marker"] == mk and x["loo_nuclei"] != ""]
    fp, n = sum(int(x["loo_false_positives"]) for x in s), sum(int(x["loo_nuclei"]) for x in s)
    r3 = [r for r in kc if r["marker"] == mk and r["k"] == "3.0"][0]
    chk(f"{mk}: disc_fp_n at k in force = summed loo_nuclei", r3["disc_fp_n"], str(n))
    chk(f"{mk}: disc_fp at k in force = summed loo rate", r3["disc_fp"], str(round(fp / n, 4)))
chk("the isotype section's discs are not stained discs",
    "LS45_s08_sc01" in {x["scene_uid"] for x in spec}, False)
chk("LS45_s09's PCNA discs (stained) feed PCNA's disc_fp",
    ("LS45_s09_sc01", "PCNA") in {(x["scene_uid"], x["marker"]) for x in spec}, True)
chk("roi_measurements.csv has no control row",
    {r["scene_uid"] for r in read(os.path.join(tmp, "roi_measurements.csv"))}
    & {"LS45_s08_sc01", "RC1_s01_sc01"}, set())

print("--- 06a says what it found ---")
chk("names the file", RR.CUT_CHECK_CSV in out6a, True)
chk("prints a suggested k (or none) per marker",
    [mk for mk in ("pERK", "PCNA")
     if f"[{mk}] suggested k" not in out6a and f"[{mk}] {RR.NO_K_MEETS}" not in out6a], [])
chk("says whether the discs agree at the k in force, per marker",
    sum(any(w in ln for w in ("discs agree with controls", "discs understate",
                              "discs overstate", "cannot compare"))
        for ln in out6a.splitlines()), 2)
chk("labels it provisional", "provisional" in out6a, True)

print("--- agreement ---")
chk("inside the interval", RR.agreement({"disc_fp": 0.02, "control_fp_lo": 0.01,
                                         "control_fp_hi": 0.03}),
    "discs agree with controls")
chk("below it", RR.agreement({"disc_fp": 0.005, "control_fp_lo": 0.01,
                              "control_fp_hi": 0.03}), "discs understate")
chk("above it", RR.agreement({"disc_fp": 0.05, "control_fp_lo": 0.01,
                              "control_fp_hi": 0.03}), "discs overstate")
chk("blank: None", RR.agreement({"disc_fp": "", "control_fp_lo": 0.01,
                                 "control_fp_hi": 0.03}), None)

print("--- no measured no_primary control, no file ---")
code, _ = run_06a(tmp, *write_fixture(
    tmp, keep=lambda uid, mk: uid in ("LS45_s01_sc01", "LS45_s02_sc01", "LS45_s08_sc01")))
chk("06a runs on stained sections and an isotype control", code, 0)
chk("...and removes the previous reagent_cut_check.csv",
    os.path.exists(os.path.join(tmp, RR.CUT_CHECK_CSV)), False)
chk("...while the isotype control is still reported",
    os.path.exists(os.path.join(tmp, RR.REPORT_CSV)), True)

print("--- sheet_header states the k in force ---")
txt = " ".join(RR.sheet_header([], {"pERK": 2.5, "PCNA": 3.0}))
chk("no hard-coded '3 x' in the header", "median + 3 x" in " ".join(RR.sheet_header([])),
    False)
chk("names positivity.cut_k", "positivity.cut_k" in " ".join(RR.sheet_header([])), True)
chk("with the k per marker", ("pERK 2.5" in txt, "PCNA 3.0" in txt), (True, True))

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
