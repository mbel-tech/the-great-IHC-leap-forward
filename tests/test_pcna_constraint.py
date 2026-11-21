"""The PCNA tuning constraint, and reagent controls detected at a borrowed range.

What is guaranteed:

  - ls_tune.constrain keeps an admissible cut, moves an inadmissible one to
    the lowest admissible grid point above it, and keeps it (saying why) when
    none is admissible. A point is admissible when the control's false
    detections per mm2 are at most fp_target x the stained detections per mm2;
  - no_controls_detection_auto_identical: with no reagent control, or none
    usable (isotype only, or a no_primary one pairable only study-wide), 05b's
    detection.auto and qc/detection_tuning.csv are byte-identical to the base
    (5405f5f), and the constraint code is not entered;
  - with a usable no_primary control, 05b measures it at the paired stained
    section's range and moves the `segment: own` marker's cut; a nuclear
    marker is untouched;
  - control_uses_borrowed_range: 05c detects a control section of a
    `segment: own` marker at its paired stained section's range, records
    `range_from` (the stained uid, or "own" without a pair), leaves stained
    sections unchanged, and re-measures a control whose pairing changed.

SYNTHETIC: the stand-in model's counts are written in tests/_pcna_golden.py.

Run:  python tests/test_pcna_constraint.py
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

failures = []


def chk(label, got, want=True):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label[:62].ljust(62) + " " + repr(got)[:200])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


import ls_tune as TU  # noqa: E402

GRID = [0.30, 0.35, 0.40, 0.45, 0.479071, 0.50, 0.55, 0.60, 0.65]

print("--- constrain ---")
stained = [(p, 1000) for p in GRID]
chk("admissible_unchanged: every point admissible keeps the choice",
    TU.constrain(stained, [(p, 1) for p in GRID], 1.0, 1.0, 0.40, 0.01)[0], 0.40)
chk("...and a None (the model default) stays None",
    TU.constrain(stained, [(p, 1) for p in GRID], 1.0, 1.0, None, 0.01)[0], None)
ctrl = [(p, 50 if p < 0.475 else 5) for p in GRID]        # 0.30-0.45 out, 0.479+ in
got, why = TU.constrain(stained, [(p, n) for p, n in ctrl if p != 0.479071] + [(0.479071, 50)],
                        1.0, 1.0, 0.40, 0.01)
chk("moves_up_to_lowest_admissible: 0.40 -> 0.50", got, 0.50)
chk("...and says why", why, "moved from 0.4: control false detections above target")
got, why = TU.constrain(stained, ctrl, 1.0, 1.0, None, 0.01)
chk("the default (0.479) admissible: kept as None", got, None)
got, why = TU.constrain(stained, ctrl, 1.0, 1.0, 0.45, 0.01)
chk("'above' takes the model default when it is the next admissible point", got, 0.479071)
chk("densities are per mm2: a control twice the area halves its density",
    TU.constrain(stained, [(p, 15) for p in GRID], 1.0, 2.0, 0.40, 0.01)[0], 0.40)
got, why = TU.constrain(stained, [(p, 500) for p in GRID], 1.0, 1.0, 0.40, 0.01)
chk("none_admissible_keeps", (got, why), (0.40, "no admissible threshold; kept"))
chk("a stained count of 0 is never admissible",
    TU.constrain([(p, 0) for p in GRID], [(p, 0) for p in GRID], 1.0, 1.0, 0.40, 0.01),
    (0.40, "no admissible threshold; kept"))
dens = TU.densities(stained, [(p, 5) for p in GRID], 2.0, 2.5, 0.01)
chk("densities: one row per grid point, per mm2, with admissibility",
    dens[0.40], {"stained_density": 500.0, "control_density": 2.0, "admissible": True})

try:
    import pylibCZIrw.czi as pyczi  # noqa: F401
except ImportError:
    print("SKIP pylibCZIrw is not installed; 05b and 05c cannot be imported")
    print("ALL PASS" if not failures else f"{len(failures)} FAILED")
    sys.exit(1 if failures else 0)

GEN = os.path.join(HERE, "_pcna_golden.py")
GOLDEN = os.path.join(HERE, "golden", "pcna_constraint")
BASE = "5405f5f"


def run_gen(scripts, mode):
    out = tempfile.mkdtemp(prefix=f"pcna_{mode}_")
    env = dict(os.environ)
    env.pop("LS_CONFIG", None)
    p = subprocess.run([sys.executable, GEN, scripts, out, mode], env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-3000:])
        return None, out
    return json.loads(p.stdout.strip().splitlines()[-1]), out


def same(a, b, mode):
    names = [f"{mode}__auto.json", f"{mode}__detection_tuning.csv"]
    return [n for n in names
            if not os.path.exists(os.path.join(a, n))
            or open(os.path.join(a, n), "rb").read() != open(os.path.join(b, n), "rb").read()]


print()
print("--- no_controls_detection_auto_identical ---")
chk("the golden files are there", sorted(os.listdir(GOLDEN)),
    ["none__auto.json", "none__detection_tuning.csv",
     "unusable__auto.json", "unusable__detection_tuning.csv"])
for mode in ("none", "unusable"):
    info, now = run_gen(SCRIPTS, mode)
    chk(f"{mode}: this checkout's code ran", (info or {}).get("scripts"), SCRIPTS)
    chk(f"{mode}: 05b succeeded", (info or {}).get("rc"), 0)
    chk(f"{mode}: detection.auto and the tuning table are byte-identical",
        same(now, GOLDEN, mode), [])
    chk(f"{mode}: the constraint code was not entered (spy)",
        (info or {}).get("calls"), {"constrain": 0, "control_crops": 0})
    shutil.rmtree(now, ignore_errors=True)

reach = subprocess.run(["git", "-C", REPO, "cat-file", "-e", BASE + "^{commit}"],
                       capture_output=True).returncode == 0
if reach:
    wt_root = tempfile.mkdtemp(prefix="pcna_base_")
    wt = os.path.join(wt_root, "wt")
    try:
        add = subprocess.run(["git", "-C", REPO, "worktree", "add", "--detach", wt, BASE],
                             capture_output=True, text=True)
        chk(f"a worktree of {BASE}", add.returncode, 0)
        for mode in ("none", "unusable"):
            info, base = run_gen(os.path.join(wt, "scripts"), mode)
            chk(f"{mode}: ...ran the base's code",
                (info or {}).get("scripts", "").startswith(wt), True)
            chk(f"{mode}: the golden files are what the base writes today",
                same(base, GOLDEN, mode), [])
            shutil.rmtree(base, ignore_errors=True)
    finally:
        subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force", wt],
                       capture_output=True)
        shutil.rmtree(wt_root, ignore_errors=True)
    print(f"     (live comparison with {BASE} ran)")
else:
    print(f"     ({BASE} is not in this clone; the committed golden files stand in)")

print()
print("--- a usable no_primary control constrains the own-channel marker ---")
info, now = run_gen(SCRIPTS, "usable")
chk("05b succeeded", (info or {}).get("rc"), 0)
calls = (info or {}).get("calls") or {}
chk("the constraint ran once, for AF488 only",
    (calls.get("constrain"), calls.get("control_crops")), (1, 1))
auto = json.load(open(os.path.join(now, "usable__auto.json"), encoding="utf-8")) if info else {}
gold = json.load(open(os.path.join(GOLDEN, "none__auto.json"), encoding="utf-8"))
chk("the nuclear marker (AF568) is untouched", auto.get("AF568"), gold["AF568"])
e = auto.get("AF488") or {}
chk("plateau choice was 0.55; controls are too dense there: moved to 0.6",
    (e.get("chosen") or {}).get("prob_thresh"), 0.6)
con = e.get("constraint") or {}
chk("the constraint records what it moved from", con.get("moved_from"), 0.55)
chk("...the reason", con.get("why"), "moved from 0.55: control false detections above target")
chk("...the fp_target used", con.get("fp_target"), 0.01)
chk("...the control and the stained section whose range it borrowed",
    con.get("controls"), [{"control": "AB1_1a_s03", "range_from": "AB1_1a_s01",
                           "pairing": "same_file"}])
chk("...the control's counts at the same grid", len(con.get("control_curve") or []),
    len(e.get("curve") or []))
chk("count_at_chosen follows the move", e.get("count_at_chosen"), 3 * 320)
with open(os.path.join(now, "usable__detection_tuning.csv"), newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
chk("the tuning table gains the four columns",
    list(rows[0])[-4:] if rows else None,
    ["control_density", "stained_density", "admissible", "constraint_moved_from"])
own = [r for r in rows if r["marker"] == "AF488"]
nuc = [r for r in rows if r["marker"] == "AF568"]
px_mm2 = (0.65 * 0.65) / 1e6
crop = 120 * 120 * px_mm2
chk("AF488 rows: densities at the final cut, per mm2",
    (round(float(own[0]["control_density"]), 1), round(float(own[0]["stained_density"]), 1)),
    (round(3 / crop, 1), round(3 * 320 / (3 * crop), 1)))
chk("...admissible, moved from 0.55", (own[0]["admissible"], own[0]["constraint_moved_from"]),
    ("yes", "0.55"))
chk("AF568 rows: blank", {(r["control_density"], r["stained_density"], r["admissible"],
                           r["constraint_moved_from"]) for r in nuc}, {("", "", "", "")})
mdp = os.path.join(now, "usable__detection_tuning.md")
md = open(mdp, encoding="utf-8").read() if os.path.exists(mdp) else ""
chk("the report says the cut was moved, where the cut is given",
    "Moved up from 0.55 by the reagent-control constraint" in md, True)
chk("...and gives the constraint's reason and pair",
    ("moved from 0.55: control false detections above target" in md,
     "AB1_1a_s03 at AB1_1a_s01 (same_file)" in md), (True, True))
shutil.rmtree(now, ignore_errors=True)
for mode in ("none", "unusable"):
    p = subprocess.run([sys.executable, GEN, SCRIPTS, tempfile.mkdtemp(), mode],
                       env={k: v for k, v in os.environ.items() if k != "LS_CONFIG"},
                       capture_output=True, text=True)
    chk(f"{mode}: 05b says the constraint was not applied",
        "AF488: reagent-control constraint not applied" in p.stdout, True)

print()
print("--- control_uses_borrowed_range: 05c ---")
from _fixture import temp_study, load_stage  # noqa: E402
import _pcna_golden as PG  # noqa: E402


class Recorder(PG.StubModel):
    def __init__(self):
        self.means = []

    def predict_instances(self, x, **kw):
        self.means.append(float(np.mean(x)))
        return super().predict_instances(x, **kw)


def detect(cfg_over, controls, argv_extra=(), manifest_extra=()):
    """05c on AF488 in the current temp study; returns (rc, norm rows, means)."""
    D5 = load_stage("05c_detect_rois.py")
    uids = PG.STAINED + controls
    PG.lay_out(D5, "AF488", uids, controls)
    mask_dir = os.path.join(D5.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    for u in uids:
        np.save(os.path.join(mask_dir, f"{u}_mask.npy"), np.ones((256, 256), bool))
    rec = Recorder()
    saved = (pyczi.open_czi, sys.argv)
    pyczi.open_czi = PG.fake_open
    saved_load = D5.load_model
    D5.load_model = lambda: rec
    sys.argv = ["05c", "--marker", "AF488", "--order", "uid"] + list(argv_extra)
    try:
        rc = D5.main()
    finally:
        pyczi.open_czi, sys.argv = saved
        D5.load_model = saved_load
    detect.nuclei = []
    if os.path.exists(D5.NUCLEI_CSV):
        with open(D5.NUCLEI_CSV, newline="", encoding="utf-8") as fh:
            detect.nuclei = list(csv.reader(fh))
    rows = []
    if os.path.exists(D5.NORM_CSV):
        with open(D5.NORM_CSV, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    return rc, {r["scene_uid"]: r for r in rows}, rec.means, D5


class pyczi_fake:
    def __enter__(self):
        self.saved = pyczi.open_czi
        pyczi.open_czi = PG.fake_open

    def __exit__(self, *e):
        pyczi.open_czi = self.saved
        return False


def write_manifest(out_root, uids):
    man = os.path.join(out_root, "manifest")
    os.makedirs(man, exist_ok=True)
    with open(os.path.join(man, "manifest_scenes.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["scene_uid", "file", "animal", "marker_channel"])
        w.writeheader()
        for u in uids:
            for m in ("AF568", "AF488"):
                w.writerow({"scene_uid": u, "file": PG.FILE_OF[u],
                            "animal": u.split("_")[0], "marker_channel": m})


with temp_study(acquisition=PG.ACQ) as study:
    write_manifest(study.out_root, PG.STAINED)
    rc, base_rows, _m, _D = detect({}, [])
    base_nuclei = [r for r in detect.nuclei if r[0] in PG.STAINED or r[0] == "scene_uid"]
    chk("no controls: 05c runs", rc, 0)
    chk("no controls: every stained row says range_from own",
        sorted((u, r["range_from"]) for u, r in base_rows.items()),
        [(u, "own") for u in sorted(PG.STAINED)])

RC_BLOCK = {"declared": "yes",
            "scenes": {"AB1_1a_s03": {"kind": "no_primary", "markers": []},
                       "ZZ9_9a_s01": {"kind": "no_primary", "markers": []}}}
CONTROLS = ["AB1_1a_s03", "ZZ9_9a_s01"]
with temp_study(acquisition=PG.ACQ, reagent_controls=RC_BLOCK) as study:
    write_manifest(study.out_root, PG.STAINED + CONTROLS)
    rc, rows, means, D5 = detect({}, CONTROLS)
    chk("with controls: 05c runs", rc, 0)
    chk("a control with a same-file pair borrows its range",
        rows.get("AB1_1a_s03", {}).get("range_from"), "AB1_1a_s01")
    chk("...the very range the stained section is stretched by",
        (rows.get("AB1_1a_s03", {}).get("norm_lo"), rows.get("AB1_1a_s03", {}).get("norm_hi")),
        (rows["AB1_1a_s01"]["norm_lo"], rows["AB1_1a_s01"]["norm_hi"]))
    chk("a control pairable only study-wide keeps its own range",
        rows.get("ZZ9_9a_s01", {}).get("range_from"), "own")
    chk("...and its own range is not the stained one",
        rows.get("ZZ9_9a_s01", {}).get("norm_hi") != rows["AB1_1a_s01"]["norm_hi"], True)
    chk("the model saw the borrowed-range control dim, the own-range one stretched",
        (means[0] < 0.1, means[2] > 0.1), (True, True))
    chk("stained sections are unchanged",
        {u: rows[u] for u in PG.STAINED}, {u: base_rows[u] for u in PG.STAINED})
    chk("...and so are their roi_nuclei.csv rows",
        [r for r in detect.nuclei if r[0] in PG.STAINED or r[0] == "scene_uid"]
        == base_nuclei and len(base_nuclei) > 1, True)
    with pyczi_fake():
        ctl = D5.section_range_for("AB1_1a_s03", "AF488", None, [], 0)
    chk("06i's re-segment range: a control's is its stained pair's",
        ctl[:2] == (float(rows["AB1_1a_s01"]["norm_lo"]),
                    float(rows["AB1_1a_s01"]["norm_hi"])), True)
    with pyczi_fake():
        own = D5.section_range_for("AB1_1a_s01", "AF488", PG.fake_open("x.czi").doc,
                                   [b for b in D5.G5.load_csv(D5.G5.BOX_CSV)
                                    if b["scene_uid"] == "AB1_1a_s01"], 0)
    chk("...a stained section's is its own",
        own[:2] == (float(rows["AB1_1a_s01"]["norm_lo"]), float(rows["AB1_1a_s01"]["norm_hi"])),
        True)

    print()
    print("--- a control whose pairing changed is measured again on resume ---")
    cfg = json.load(open(study.path, encoding="utf-8"))
    cfg["reagent_controls"]["paired_with"] = {"ZZ9_9a_s01": ["AB2_3a_s01"]}
    json.dump(cfg, open(study.path, "w", encoding="utf-8"), indent=2)
    rc, rows2, means2, D5 = detect({}, CONTROLS)
    chk("the resume runs", rc, 0)
    chk("only the re-paired control was segmented again (2 boxes)", len(means2), 2)
    chk("...now at its operator pair's range",
        (rows2.get("ZZ9_9a_s01", {}).get("range_from"),
         rows2.get("ZZ9_9a_s01", {}).get("norm_hi")),
        ("AB2_3a_s01", rows2["AB2_3a_s01"]["norm_hi"]))
    chk("...and the rest kept their rows",
        {u: rows2[u] for u in PG.STAINED + ["AB1_1a_s03"]},
        {u: rows[u] for u in PG.STAINED + ["AB1_1a_s03"]})

print()
print("--- an old roi_normalisation.csv is backfilled with range_from own ---")
with temp_study(acquisition=PG.ACQ) as study:
    write_manifest(study.out_root, PG.STAINED)
    rc, rows, _m, D5 = detect({}, [], ["--limit", "1"])
    with open(D5.NORM_CSV, newline="", encoding="utf-8") as fh:
        old = [r[:-1] for r in csv.reader(fh)]
    with open(D5.NORM_CSV, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(old)
    rc, rows, _m, D5 = detect({}, [])
    chk("the resume onto the old header runs", rc, 0)
    chk("...every row now says range_from own",
        sorted((u, r["range_from"]) for u, r in rows.items()),
        [(u, "own") for u in sorted(PG.STAINED)])

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
