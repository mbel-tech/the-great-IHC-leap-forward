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

import csv
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
order = D5.measure_order(["A_sc01", "RC_sc01", "B_sc01"], set(), lk, "AF568", "balanced")
chk("05c measures the reagent control first", order[0], "RC_sc01")
chk("...then the balanced order of the rest",
    order[1:], D5.balanced_order(["A_sc01", "B_sc01"], set()))
chk("...which here is not plain uid order", order[1:], ["B_sc01", "A_sc01"])
chk("--order uid also puts the control first",
    D5.measure_order(["A_sc01", "RC_sc01", "B_sc01"], set(), lk, "AF568", "uid"),
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

print("--- settings_tracked ---")
import stages as S  # noqa: E402
for sid in ("overviews", "scene_renders", "tune_detection", "detect"):
    chk(f"{sid} lists the reagent control settings",
        [k for k in ("reagent_controls.declared", "reagent_controls.files",
                     "reagent_controls.scenes") if k not in S.BY_ID[sid].settings], [])

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

print()
print("all passed" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
