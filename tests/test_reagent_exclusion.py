"""Reagent controls stay out of dataset-wide estimates and are measured first.

A reagent control (no primary, isotype, unstained ...) is still exported,
curated and detected, but it must not set the study's frozen display range (01),
must not be in 05b's tuning sample, and 05c measures it before the balanced
order so a partial run still reports it. Readers that group sections by animal
and sort by section_order (01b, 04b, 04c) put a control in a series of its own,
as 00 numbered it. Which scenes are controls is always decided from config
through ls_reagent_controls.expand.

Run:  python tests/test_reagent_exclusion.py
"""

import contextlib
import csv
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import use_temp_study, load_stage  # noqa: E402

RC_BLOCK = {"declared": "unsure",
            "scenes": {"RC_sc01": {"kind": "no_primary", "markers": []},
                       "A_sc03": {"kind": "isotype", "markers": []}}}
STUDY = use_temp_study(
    acquisition={"layout": "paired", "markers": ["AF568", "AF488"]},
    groups={"order": ["ctl", "trt"], "by_animal": {"A": "trt", "B": "ctl", "RC": "trt"}},
    reagent_controls=RC_BLOCK)

import ls_reagent_controls as RC  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def row(uid, order=1, marker="AF568", animal=None, file=None):
    return {"scene_uid": uid, "file": file or f"{uid.split('_')[0]}_1.czi",
            "marker_channel": marker, "animal": animal or uid.split("_")[0],
            "section_order": str(order), "scene_index": "1"}


CFG = STUDY.config

print("--- display_pool_excludes ---")
O = load_stage("01_overviews.py", name="lsstage_01_rcx")
scenes = [row(f"A_sc{i:02d}") for i in range(1, 6)] + [row("RC_sc01"), row("B_sc01")]
lookup = RC.expand(CFG, scenes)
sample = O.display_sample(scenes, lookup)
chk("the display-range sample holds no reagent control",
    [r["scene_uid"] for r in sample if RC.is_control(lookup, r["scene_uid"], r["marker_channel"])],
    [])
chk("...and every stained scene is still drawn from", len(sample), 5)
only = [row("RC_sc01"), row("A_sc01")]
chk("with one stained scene left, it is the whole sample",
    [r["scene_uid"] for r in O.display_sample(only, RC.expand(CFG, only))], ["A_sc01"])
try:
    O.display_sample([row("RC_sc01")], RC.expand(CFG, [row("RC_sc01")]))
    chk("no stained scene left raises", False)
except SystemExit as exc:
    chk("no stained scene left raises a clear error",
        "reagent control" in str(exc) and "stained" in str(exc))
# A multiplex scene that is a control for one marker only stays in for the other.
mp = {"scenes": {"M_sc01": {"kind": "no_primary", "markers": ["AF568"]}}}
mrows = [row("M_sc01", marker="AF568"), row("M_sc01", marker="AF488")]
chk("a control for one marker only is still sampled for the other",
    [(r["scene_uid"], r["marker_channel"])
     for r in O.display_sample(mrows, RC.expand({"reagent_controls": mp}, mrows))],
    [("M_sc01", "AF488")])

print("--- display_ranges_resample: a cached range drawn from a now-confirmed control ---")
import json  # noqa: E402
_rp = O.RANGES_PATH
O.RANGES_PATH = os.path.join(tempfile.mkdtemp(), "display_ranges.json")
OLD = {"DAPI": {"lo": 1, "hi": 9}, "AF568": {"lo": 2, "hi": 8}}


def sample_with(cached, **kw):
    if cached is None:
        if os.path.exists(O.RANGES_PATH):
            os.remove(O.RANGES_PATH)
    else:
        with open(O.RANGES_PATH, "w", encoding="utf-8") as fh:
            json.dump(cached, fh)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = O.sample_display_ranges(scenes, {}, 1, False, **kw)
    return got, out.getvalue()


try:
    got, said = sample_with(None)
    chk("01_records_sample: a fresh sample records the (uid, marker) it drew from",
        sorted(map(tuple, got.get("_sampled", []))),
        sorted((r["scene_uid"], r["marker_channel"]) for r in O.display_sample(scenes, lookup)))
    chk("...and the record is not a channel: ranges_id ignores it",
        O.ranges_id(dict(OLD, _sampled=[["A_sc01", "AF568"]])), O.ranges_id(OLD))
    got, said = sample_with(dict(OLD, _sampled=[["A_sc01", "AF568"], ["B_sc01", "AF568"]]))
    chk("a cached range drawn from stained sections only is kept", got.get("DAPI"), OLD["DAPI"])
    drawn = []
    _ds = O.display_sample
    O.display_sample = lambda sc, lk: drawn.append(_ds(sc, lk)) or drawn[-1]
    try:
        got, said = sample_with(dict(OLD, _sampled=[["A_sc01", "AF568"], ["RC_sc01", "AF568"]]))
    finally:
        O.display_sample = _ds
    chk("01_resamples: one drawn from a section now confirmed is sampled again",
        (len(drawn), [r["scene_uid"] for r in (drawn or [[]])[0] if r["scene_uid"] == "RC_sc01"]),
        (1, []))
    chk("...and says so, naming it", "reagent control" in said and "RC_sc01" in said, True)
    chk("...with no slide readable here it keeps the old ranges rather than an empty file",
        (got.get("DAPI"), "NOT" in said), (OLD["DAPI"], True))
    got, said = sample_with(dict(OLD, _sampled=[["RC_sc01", "AF568"]]), narrowed=True)
    chk("...not on a --limit run, which says to run without it",
        (got.get("DAPI"), "--limit" in said), (OLD["DAPI"], True))
    got, said = sample_with(OLD)
    chk("a range older than the record is kept, with a note to resample with --force",
        (got.get("DAPI"), "--force" in said and "reagent control" in said), (OLD["DAPI"], True))
finally:
    O.RANGES_PATH = _rp
L1 = load_stage("01l_scene_renders.py", name="lsstage_01l_rcx")
_lp = L1.RANGES_PATH
L1.RANGES_PATH = os.path.join(tempfile.mkdtemp(), "display_ranges.json")
with open(L1.RANGES_PATH, "w", encoding="utf-8") as fh:
    json.dump(dict(OLD, _sampled=[["A_sc01", "AF568"]]), fh)
try:
    chk("01l reads channels only, not the record", sorted(L1.load_ranges()), ["AF568", "DAPI"])
finally:
    L1.RANGES_PATH = _lp

print("--- tune_sample_excludes ---")
B5 = load_stage("05b_tune_detection.py", name="lsstage_05b_rcx")
uids = ["A_sc01", "A_sc02", "RC_sc01", "B_sc01", "B_sc02"]
lk = RC.expand(CFG, [row(u) for u in uids])
for n in (1, 3, 12):
    chk(f"05b's sample of {n} never holds the control",
        "RC_sc01" in B5.tuning_sections(uids, lk, "AF568", n), False)
chk("...and samples the stained sections as before",
    B5.tuning_sections(uids, lk, "AF568", 12),
    B5.sample_sections(["A_sc01", "A_sc02", "B_sc01", "B_sc02"], 12))
chk("a control for another marker does not leave this marker's sample",
    "RC_sc01" in B5.tuning_sections(uids, lk, "AF488", 12), True)

print("--- controls_first ---")
D5 = load_stage("05c_detect_rois.py", name="lsstage_05c_rcx")
lk = RC.expand(CFG, [row(u) for u in ("A_sc01", "RC_sc01", "B_sc01")])
def quiet(fn, *a):
    """fn(*a) with the stage's own progress lines kept out of the ok/FAIL list."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


order = quiet(D5.measure_order, ["A_sc01", "RC_sc01", "B_sc01"], set(), lk, "AF568", "balanced")
chk("05c measures the reagent control first", order[0], "RC_sc01")
chk("...then the balanced order of the rest",
    order[1:], quiet(D5.balanced_order, ["A_sc01", "B_sc01"], set()))
chk("...which here is not plain uid order", order[1:], ["B_sc01", "A_sc01"])
chk("--order uid also puts the control first",
    quiet(D5.measure_order, ["A_sc01", "RC_sc01", "B_sc01"], set(), lk, "AF568", "uid"),
    ["RC_sc01", "A_sc01", "B_sc01"])
chk("reagent_order keeps natural order inside each part",
    RC.reagent_order(["A_sc10", "RC_sc01", "A_sc02", "A_sc03"],
                     RC.expand(CFG, [row(u) for u in ("A_sc10", "RC_sc01", "A_sc02", "A_sc03")])),
    (["A_sc03", "RC_sc01"], ["A_sc02", "A_sc10"]))

print("--- the lookup is read off the manifest, per marker ---")
os.makedirs(os.path.join(STUDY.out_root, "manifest"), exist_ok=True)
with open(os.path.join(STUDY.out_root, "manifest", "manifest_scenes.csv"), "w",
          newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["scene_uid", "file", "animal", "marker_channel", "reagent_control"])
    w.writeheader()
    w.writerow({"scene_uid": "RC_sc01", "file": "RC_1.czi", "animal": "RC",
                "marker_channel": "AF568", "reagent_control": ""})
    w.writerow({"scene_uid": "B_sc01", "file": "B_1.czi", "animal": "B",
                "marker_channel": "AF568", "reagent_control": "no_primary"})
chk("paired: the control for the manifest row's marker",
    sorted(RC.manifest_lookup(CFG)), [("RC_sc01", "AF568")])
chk("multiplex: a scene entry with markers [] covers every marker",
    sorted(RC.manifest_lookup(CFG, ["AF568", "AF488"])),
    [("RC_sc01", "AF488"), ("RC_sc01", "AF568")])
chk("the display-only column is never read", RC.is_control(RC.manifest_lookup(CFG),
                                                            "B_sc01", "AF568"), False)
chk("nothing confirmed, nothing read",
    RC.manifest_lookup({"out_root": STUDY.out_root, "reagent_controls": {}}), {})

print("--- warn_unanswered ---")
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "qc"))
with open(os.path.join(tmp, "qc", "reagent_screen.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["scene_uid", "file", "marker", "status"])
    w.writeheader()
    w.writerow({"scene_uid": "X_sc02", "file": "X_2.czi", "marker": "AF568", "status": "candidate"})
    w.writerow({"scene_uid": "X_sc03", "file": "X_2.czi", "marker": "AF568", "status": "normal"})


def cfg(**rc):
    return {"out_root": tmp, "reagent_controls": dict({"declared": "unsure"}, **rc)}


msg = RC.warn_unanswered(cfg())
chk("unsure with an unanswered candidate warns", isinstance(msg, str) and "X_sc02" in msg)
chk("...in the operator's words", "reagent control" in (msg or ""))
chk("...and names only the candidate", "X_sc03" in (msg or ""), False)
chk("put in rejected, no warning", RC.warn_unanswered(cfg(rejected=["X_sc02"])), None)
chk("confirmed by its file, no warning",
    RC.warn_unanswered(cfg(files={"X_2.czi": {"kind": "no_primary"}})), None)
chk("confirmed as a scene, no warning",
    RC.warn_unanswered(cfg(scenes={"X_sc02": {"kind": "no_primary"}})), None)
chk("declared yes, no warning",
    RC.warn_unanswered({"out_root": tmp, "reagent_controls": {"declared": "yes"}}), None)
chk("no screen yet, no warning",
    RC.warn_unanswered({"out_root": tempfile.mkdtemp(),
                        "reagent_controls": {"declared": "unsure"}}), None)

print("--- confirming late: what is tracked, and what Save says instead ---")
# 05c and 06a record what they read, so a change to the confirmed reagent
# controls marks them out of date (tests/test_workflow.py checks the status).
# The earlier stages that leave controls out - 01's display ranges, 01d, 01l,
# 05b's tuning sample, 06c and 06d - record no inputs at all: a settings list
# there tracked nothing. They list none now, and the confirm list's Save names
# them to run again instead.
import stages as S  # noqa: E402
for sid in ("overviews", "contactsheets", "scene_renders", "tune_detection", "excel_sample",
            "excel_slide"):
    chk(f"{sid}: records no inputs, so lists no reagent_controls setting",
        (S.BY_ID[sid].made_from,
         [k for k in S.BY_ID[sid].settings if k.startswith("reagent_controls")]), (None, []))
chk("05c tracks which sections are controls, and nothing else of the block",
    [k for k in S.BY_ID["detect"].settings if k.startswith("reagent_controls")],
    ["reagent_controls.files", "reagent_controls.scenes"])
chk("06a tracks them and the pairing",
    [k for k in S.BY_ID["roi_dataset"].settings if k.startswith("reagent_controls")],
    ["reagent_controls.files", "reagent_controls.scenes", "reagent_controls.paired_with"])
try:
    import reagent_dialog as RD  # noqa: E402
except ImportError:                 # no Qt here: the words are not testable
    RD = None
if RD is not None:
    said = RD.rerun_text(True)
    chk("rerun_listed: Save names each stage to run again, the untracked ones included",
        [w for w in ("00  Read slide headers", "02  Pair", "01  Export section overviews",
                     "04n", "05b", "05c", "06a", "06c") if w not in said], [])
    chk("...nothing when nothing changed", RD.rerun_text(False), "")
print("--- animal_with_control_two_series ---")
chk("series_animal: a stained row is its animal",
    RC.series_animal(row("A_sc01"), RC.expand(CFG, [row("A_sc01")])), "A")
a_rows = [row("A_sc01", 1), row("A_sc02", 2), row("A_sc03", 1), row("A_sc04", 3)]
a_lk = RC.expand(CFG, a_rows)
chk("series_animal: a control row is the animal's #rc series",
    RC.series_animal(row("A_sc03", 1), a_lk), "A#rc")
B4 = load_stage("04b_atlas_match.py", name="lsstage_04b_rcx")
got = B4.series_of(a_rows, a_lk)
chk("04b: an animal with a control gets two series", sorted(got), ["A", "A#rc"])
chk("...the stained series in its own order",
    [r["scene_uid"] for r in got["A"]], ["A_sc01", "A_sc02", "A_sc04"])
chk("...the control alone in its own", [r["scene_uid"] for r in got["A#rc"]], ["A_sc03"])
C4 = load_stage("04c_atlas_match.py", name="lsstage_04c_rcx")
idx = [{"kind": "section", "id": r["scene_uid"], "animal": "A",
        "section_order": r["section_order"]} for r in a_rows]
got = C4.series_of(idx, a_lk, {r["scene_uid"]: r["marker_channel"] for r in a_rows})
chk("04c: an animal with a control gets two series", sorted(got), ["A", "A#rc"])
chk("...the control alone in its own", [r["id"] for r in got["A#rc"]], ["A_sc03"])
chk("...the animal column is left as it is",
    {r["animal"] for v in got.values() for r in v}, {"A"})

D1 = load_stage("01d_contactsheets.py", name="lsstage_01d_rcx")
sheet = [dict(r, section_order=int(r["section_order"]), slide=1) for r in a_rows]
key = D1.sheet_order(a_lk)
chk("01d: one sheet, the control after the stained series, not interleaved",
    [r["scene_uid"] for r in sorted(sheet, key=key)], ["A_sc01", "A_sc02", "A_sc04", "A_sc03"])
_qc_root = tempfile.mkdtemp()
os.makedirs(os.path.join(_qc_root, "manifest"))
with open(os.path.join(_qc_root, "manifest", "variant_choices.csv"), "w", newline="",
          encoding="utf-8") as fh:
    fh.write("animal,slide,marker_channel,chosen_variant,candidates,source\n"
             "A,1,AF568,a,a(2)|nc(1),most_scenes\n")
_qc = os.path.join(_qc_root, "focus.csv")
with open(_qc, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, ["scene_uid", "file", "animal", "slide", "variant", "marker_channel",
                            "section_order", "focus_score", "tissue_fraction",
                            "tissue_area_mm2", "saturated_fraction",
                            "saturated_fraction_raw"], restval="0")
    w.writeheader()
    for uid, var in (("A_sc01", "a"), ("A_sc02", "a"), ("A_sc03", "nc"), ("A_sc04", "b")):
        w.writerow({"scene_uid": uid, "file": f"A_1{var}.czi", "animal": "A", "slide": "1",
                    "variant": var, "marker_channel": "AF568", "section_order": "1"})
_keep = (D1.QC_CSV, D1.OUT_ROOT)
D1.QC_CSV, D1.OUT_ROOT = _qc, _qc_root
try:
    with contextlib.redirect_stdout(io.StringIO()):
        kept = sorted(r["scene_uid"] for r in D1.load_qc())
finally:
    D1.QC_CSV, D1.OUT_ROOT = _keep
chk("01d_keeps_control_scan: a superseded re-scan is dropped, a reagent control's scan is not",
    kept, ["A_sc01", "A_sc02", "A_sc03"])
chk("01d: with no control, plain section order",
    [r["scene_uid"] for r in sorted(sheet, key=D1.sheet_order({}))],
    ["A_sc01", "A_sc03", "A_sc02", "A_sc04"])

print("--- blinding: no reagent-control code reads the group key ---")
# A reagent control is not blinding information, and handling one must never
# need the animal-to-group key: no code (docstrings and comments aside) in the
# reagent-control modules reads `by_animal` as a string key or an attribute (a
# local variable of that name, a per-animal tally, is not the key), and none
# imports the module that reads it (ls_sampling). Stages before 06b that touch
# reagent controls are
# covered too. The group LABELS (groups.order) may be read: the name scan uses
# them to keep a weak token that is a group name from being proposed.
import ast  # noqa: E402

BLIND = ["scripts/01m_reagent_screen.py", "scripts/ls_reagent_controls.py",
         "scripts/ls_reagent_screen.py", "scripts/ls_reagent_report.py",
         "app/reagent_dialog.py", "scripts/00_manifest.py", "scripts/01_overviews.py",
         "scripts/01d_contactsheets.py", "scripts/04k_level_curator.py",
         "scripts/05b_tune_detection.py", "scripts/06a_roi_dataset.py",
         "scripts/02_pair_passes.py", "scripts/04n_roi_worklist.py", "scripts/01b_pick_sections.py",
         "scripts/04b_atlas_match.py", "scripts/04c_atlas_match.py"]


def reads_group_key(path):
    tree = ast.parse(open(os.path.join(REPO, path), encoding="utf-8").read())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    hits = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
                and "by_animal" in n.value):
            hits.append(f"string at line {n.lineno}")
        elif isinstance(n, ast.Attribute) and n.attr == "by_animal":
            hits.append(f"attribute at line {n.lineno}")
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names] + ([n.module] if getattr(n, "module", None) else [])
            if any(x and x.split(".")[-1] in ("ls_sampling", "06b_join_sampling") for x in names):
                hits.append(f"import at line {n.lineno}")
    return hits


for path in BLIND:
    chk(f"blinding: {path} does not read groups.by_animal", reads_group_key(path), [])
chk("...and the check bites: 05c, which reads the key for its order, is flagged",
    bool(reads_group_key("scripts/05c_detect_rois.py")), True)

print()
print("all passed" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
